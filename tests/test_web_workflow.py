import unittest
from unittest.mock import patch

from src.agents import literature as w
from src.persistence import repository


TASK = {'project_id': 2, 'query': 'research', 'source': 'web'}


def result(url, level='web_snippet', content='Research found 95%.'):
    return {'title': 'Source ' + url, 'url': url, 'content': content,
            'source_level': level, 'retrieved_at': '2026-11-06T10:00:00+08:00',
            'provider': 'test', 'published_at': '2025-01-01'}


def scripted_model(*, rounds=1, queries=None, assert_excerpt=False):
    queries = queries or ['first'] * rounds
    calls = {'delegation': 0, 'step': 0}

    def model(name, payload, schema, **kwargs):
        if name == 'runtime/route':
            if len(payload['results']) < rounds and ('web_research' in payload['available_actions'] or 'paper_research' in payload['available_actions']):
                calls['delegation'] += 1
                calls['step'] = 0
                action = 'web_research' if payload['source'] == 'web' else 'paper_research'
                return w.Route(action=action, task='research')
            return w.Route(action='review' if payload['review'] is None else 'report')
        if name == 'literature/research':
            step = calls['step']
            calls['step'] += 1
            if step == 0:
                return w.Specialist(action='search', query=queries[min(calls['delegation']-1, len(queries)-1)])
            if step == 1 and payload['candidates']:
                return w.Specialist(action='read', candidate_ids=[c['id'] for c in payload['candidates'][:6]])
            return w.Specialist(action='finish')
        if name == 'literature/review':
            return w.Review(sufficient=bool(payload['evidence']), gaps=[] if payload['evidence'] else ['缺少证据'], summary='审查')
        if name == 'literature/synthesize':
            if assert_excerpt:
                assert len(payload['evidence'][0]['text']) == 1200
            return w.Draft(claims=[w.Claim(text='Research found 95%.', citations=[e['id']]) for e in payload['evidence'] if '95%' in e['text']])
        if name == 'literature/verify_claim': return w.Verdict(supported=True)
        raise AssertionError(name)
    return model


class WebWorkflowTests(unittest.TestCase):
    def test_web_graph_forces_search_stable_ids_and_persists_source_metadata(self):
        pages = [
            [result('https://example.com/a#fragment'), result('https://example.com/a#other')],
            [result('https://example.com/a'), result('https://example.com/b', 'web_content')],
            [result('https://arxiv.org/abs/1234', 'abstract')],
        ]
        candidate_ids = []
        scripted = scripted_model(rounds=3, queries=['first', 'next', 'third'])
        def model(name, payload, schema, **kwargs):
            if name == 'literature/research' and payload['candidates']:
                candidate_ids.append(next(c['id'] for c in payload['candidates'] if 'example.com/a' in c['title']))
            return scripted(name, payload, schema, **kwargs)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web', side_effect=pages) as search, patch('src.tools.search.retrieve') as retrieve, patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as invoke, patch.object(repository, 'project_evidence') as local:
            content, evidence = w.run(TASK, history=[])
        self.assertEqual(search.call_count, 3)
        self.assertGreaterEqual(len(candidate_ids), 3)
        self.assertEqual(len(set(candidate_ids)), 1)
        self.assertEqual([call.args[0] for call in invoke.call_args_list], ['search_web', 'collect_evidence'] * 3)
        self.assertTrue(all(call.kwargs['context'].project_id == 2 and call.kwargs['context'].source == 'web' for call in invoke.call_args_list))
        self.assertEqual(len(evidence), 3)
        by_url = {e['original_url']: e for e in evidence}
        first = by_url['https://example.com/a#fragment']
        self.assertEqual(first['retrieved_at'], '2026-11-06T10:00:00+08:00')
        self.assertEqual(first['published_at'], '2025-01-01')
        self.assertEqual(first['provider'], 'test')
        self.assertEqual({url: e['source_level'] for url, e in by_url.items()}, {'https://example.com/a#fragment': 'web_snippet', 'https://example.com/b': 'web_content', 'https://arxiv.org/abs/1234': 'abstract'})
        self.assertIn('搜索摘要', content)
        self.assertIn('网页文本', content)
        self.assertIn('论文摘要', content)
        self.assertNotIn('原文版本', content)
        retrieve.assert_not_called()
        local.assert_not_called()

    def test_no_new_evidence_stops_and_limits_excerpt(self):
        with patch.object(w, 'structured', side_effect=scripted_model(rounds=3, queries=['first', 'again', 'third'], assert_excerpt=True)), patch('src.tools.search.search_web', return_value=[result('https://example.com', content='a' * 2000)]) as search:
            w.run(TASK, history=[])
        self.assertEqual(search.call_count, 2)

    def test_all_failed_requests_fail_safely_and_can_be_retried(self):
        with patch.object(w, 'structured', side_effect=scripted_model()), patch('src.tools.search.search_web', side_effect=RuntimeError('secret')):
            with self.assertRaisesRegex(RuntimeError, '网页检索失败') as error:
                w.run(TASK, history=[])
        self.assertNotIn('secret', str(error.exception))

    def test_partial_failure_keeps_results_and_reports_incomplete(self):
        with patch.object(w, 'structured', side_effect=scripted_model(rounds=2, queries=['good', 'bad'])), patch('src.tools.search.search_web', side_effect=[[result('https://example.com')], RuntimeError('secret')]):
            content, evidence = w.run(TASK, history=[])
        self.assertIn('失败', content)
        self.assertIn('不完整', content)
        self.assertNotIn('secret', content)
        self.assertEqual(len(evidence), 1)

    def test_successful_empty_results_are_explicit(self):
        with patch.object(w, 'structured', side_effect=scripted_model()), patch('src.tools.search.search_web', return_value=[]):
            content, evidence = w.run(TASK, history=[])
        self.assertIn('未找到可用文本证据', content)
        self.assertNotIn('失败', content)
        self.assertEqual(evidence, [])

    def test_collection_never_searches_web(self):
        task = {'project_id': 2, 'query': 'research', 'source': 'collection', 'paper_ids': [7]}
        paper = {'id': 7, 'title': 'Local', 'abstract': 'local text', 'versions': []}
        with patch.object(w, 'structured', side_effect=scripted_model()), patch('src.tools.search.search_web') as search, patch.object(repository, 'project_evidence', return_value=[paper]), patch.object(repository, 'local_search', return_value=[]):
            w.run(task, history=[])
        search.assert_not_called()

    def test_web_direct_is_rejected_before_tool_call(self):
        with patch.object(w, 'structured', return_value=w.Route(action='direct', answer='answer')), patch.object(w.registry, 'invoke') as invoke:
            with self.assertRaises(ValueError):
                w.run(TASK, history=[])
        invoke.assert_not_called()


if __name__ == '__main__':
    unittest.main()
