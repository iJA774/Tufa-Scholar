import unittest
from unittest.mock import patch

from src.agents import literature as w


TASK = {'id': 1, 'project_id': 2, 'query': 'research', 'source': 'web'}


def result(url, level='web_snippet', content='Research found 95%.'):
    return {'title': 'Source ' + url, 'url': url, 'content': content,
            'source_level': level, 'retrieved_at': '2026-11-06T10:00:00+08:00',
            'provider': 'test', 'published_at': '2025-01-01'}


class WebWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stage = patch.object(w.repository, 'update_stage').start()
        self.history = patch.object(w.repository, 'task_history', return_value=[]).start()
        self.addCleanup(patch.stopall)

    def test_web_graph_forces_search_stable_ids_and_persists_source_metadata(self):
        rounds = []
        def model(name, payload, schema):
            if name.endswith('plan_review'): return w.Plan(direct=True, queries=['first'])
            if name.endswith('assess_gap'):
                rounds.append(payload['evidence'])
                return w.Gap(query='next')
            if name.endswith('synthesize'):
                return w.Draft(claims=[w.Claim(text='Research found 95%.', citations=[e['id']]) for e in payload['evidence']])
            if name.endswith('verify_claim'): return w.Verdict(supported=True)
            raise AssertionError(name)
        pages = [
            [result('https://example.com/a#fragment'), result('https://example.com/a#other')],
            [result('https://example.com/a'), result('https://example.com/b', 'web_content')],
            [result('https://arxiv.org/abs/1234', 'abstract')],
        ]
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web', side_effect=pages) as search, patch('src.tools.search.retrieve') as retrieve, patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as invoke, patch.object(w.repository, 'project_evidence') as local:
            content, evidence = w.run(TASK)
        self.assertEqual(search.call_count, 3)
        self.assertEqual([call.args[0] for call in invoke.call_args_list], ['search_web', 'collect_evidence', 'search_web', 'collect_evidence', 'search_web', 'collect_evidence'])
        self.assertTrue(all(call.kwargs['context'].project_id == 2 and call.kwargs['context'].source == 'web' for call in invoke.call_args_list))
        self.assertEqual(len(rounds), 2)
        self.assertEqual(rounds[0][0]['id'], rounds[1][0]['id'])
        self.assertEqual(len(evidence), 3)
        self.assertEqual(evidence[0]['original_url'], 'https://example.com/a#fragment')
        self.assertEqual(evidence[0]['retrieved_at'], '2026-11-06T10:00:00+08:00')
        self.assertEqual(evidence[0]['published_at'], '2025-01-01')
        self.assertEqual(evidence[0]['provider'], 'test')
        self.assertEqual([e['source_level'] for e in evidence], ['web_snippet', 'web_content', 'abstract'])
        self.assertIn('搜索摘要', content)
        self.assertIn('网页文本', content)
        self.assertIn('论文摘要', content)
        self.assertNotIn('前 20 页', content)
        self.assertNotIn('原文版本', content)
        retrieve.assert_not_called()
        local.assert_not_called()

    def test_no_new_evidence_stops_and_limits_excerpt(self):
        def model(name, payload, schema):
            if name.endswith('plan_review'): return w.Plan(queries=['first'])
            if name.endswith('assess_gap'): return w.Gap(query='again')
            if name.endswith('synthesize'):
                self.assertEqual(len(payload['evidence'][0]['text']), 1200)
                return w.Draft()
            raise AssertionError(name)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web', return_value=[result('https://example.com', content='a' * 2000)]) as search:
            w.run(TASK)
        self.assertEqual(search.call_count, 2)

    def test_all_failed_requests_fail_safely_and_can_be_retried(self):
        with patch.object(w, 'structured', return_value=w.Plan(queries=['first'])), patch('src.tools.search.search_web', side_effect=RuntimeError('secret')):
            with self.assertRaisesRegex(RuntimeError, '网页检索失败') as error:
                w.run(TASK)
        self.assertNotIn('secret', str(error.exception))

    def test_partial_failure_keeps_results_and_reports_incomplete(self):
        def model(name, payload, schema):
            if name.endswith('plan_review'): return w.Plan(queries=['good', 'bad'])
            if name.endswith('assess_gap'): return w.Gap()
            if name.endswith('synthesize'):
                return w.Draft(claims=[w.Claim(text='Research found 95%.', citations=[payload['evidence'][0]['id']])])
            if name.endswith('verify_claim'): return w.Verdict(supported=True)
            raise AssertionError(name)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web', side_effect=[[result('https://example.com')], RuntimeError('secret')]):
            content, evidence = w.run(TASK)
        self.assertIn('失败', content)
        self.assertIn('不完整', content)
        self.assertNotIn('secret', content)
        self.assertEqual(len(evidence), 1)

    def test_successful_empty_results_are_explicit(self):
        def model(name, payload, schema):
            if name.endswith('plan_review'): return w.Plan(queries=['first'])
            if name.endswith('assess_gap'): return w.Gap()
            if name.endswith('synthesize'): return w.Draft()
            raise AssertionError(name)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web', return_value=[]):
            content, evidence = w.run(TASK)
        self.assertIn('未找到可用文本证据', content)
        self.assertNotIn('失败', content)
        self.assertEqual(evidence, [])

    def test_collection_never_searches_web(self):
        task = {'id': 1, 'project_id': 2, 'query': 'research', 'source': 'collection', 'paper_ids': [7]}
        paper = {'id': 7, 'title': 'Local', 'abstract': 'local text', 'versions': []}
        def model(name, payload, schema):
            if name.endswith('plan_review'): return w.Plan(direct=True, queries=['first'])
            if name.endswith('assess_gap'): return w.Gap()
            if name.endswith('synthesize'): return w.Draft()
            raise AssertionError(name)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web') as search, patch.object(w.repository, 'project_evidence', return_value=[paper]), patch.object(w.repository, 'local_search', return_value=[]):
            w.run(task)
        search.assert_not_called()
        self.history.assert_not_called()


if __name__ == '__main__':
    unittest.main()
