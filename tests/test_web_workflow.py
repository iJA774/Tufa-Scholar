import unittest
from unittest.mock import patch

from src.agents import literature as w
from src.persistence import repository


TASK = {'project_id': 2, 'query': 'topic', 'source': 'web'}


def result(url, level='web_snippet', content='Topic research found 95%.'):
    return {'title': 'Source ' + url, 'url': url, 'content': content,
            'source_level': level, 'retrieved_at': '2026-11-06T10:00:00+08:00',
            'provider': 'test', 'published_at': '2025-01-01'}


def scripted_model(*, rounds=1, queries=None, assert_excerpt=False):
    queries = queries or ['first'] * rounds
    calls = {'delegation': 0, 'step': 0}

    def model(name, payload, schema, **kwargs):
        if name == 'runtime/route':
            if len(payload['results']) < rounds and ('web_research' if payload['source'] == 'web' else 'paper_research') in payload['available_actions']:
                action = 'web_research' if payload['source'] == 'web' and 'web_research' in payload['available_actions'] else 'paper_research'
                return w.Route(action=action, task='research', directions=['research'])
            return w.Route(action='report')
        if name == 'literature/research':
            if not payload['recent_result']:
                calls['delegation'] += 1
                calls['step'] = 0
            step = calls['step']
            calls['step'] += 1
            if step == 0:
                return w.Specialist(plan=['检索取证'], action='search', query=queries[min(calls['delegation']-1, len(queries)-1)])
            if step == 1 and payload['candidates']:
                return w.Specialist(plan=['检索取证'], action='read', candidate_ids=[c['id'] for c in payload['candidates'][:6]])
            return w.Specialist(plan=['检索取证'], action='finish')
        if name == 'literature/review':
            if calls['delegation'] < rounds:
                return w.Review(sufficient=False, gaps=['需补研'], summary='审查', next_steps=[w.NextStep(direction='research', goal=f'research {calls["delegation"]}', route='web_research')])
            return w.Review(sufficient=bool(payload['evidence']), gaps=[] if payload['evidence'] else ['缺少证据'], summary='审查')
        if name == 'literature/synthesize':
            if assert_excerpt:
                assert len(payload['evidence'][0]['text']) <= 1200
            return w.Draft(sections=[w.Section(heading='概述', paragraphs=[w.Claim(text='Research found 95%.', citations=[e['id']])]) for e in payload['evidence'] if '95%' in e['text']])
        if name == 'literature/revise': return w.Draft(sections=[])
        if name == 'literature/verify_claim': return w.Verdict(status='supported')
        raise AssertionError(name)
    return model


class WebWorkflowTests(unittest.TestCase):
    def setUp(self):
        diagnostic = patch('src.agents.report.record_failure', return_value='diag-test', create=True)
        diagnostic.start()
        self.addCleanup(diagnostic.stop)

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
        with patch.object(w, 'structured', side_effect=scripted_model(rounds=3, queries=['first', 'again', 'third'], assert_excerpt=True)), patch('src.tools.search.search_web', return_value=[result('https://example.com', content='topic ' + 'a' * 2000)]) as search:
            w.run(TASK, history=[])
        self.assertEqual(search.call_count, 3)

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
        task = {'project_id': 2, 'query': 'topic', 'source': 'collection', 'paper_ids': [7]}
        paper = {'id': 7, 'title': 'Local', 'abstract': 'local text', 'versions': []}
        with patch.object(w, 'structured', side_effect=scripted_model()), patch('src.tools.search.search_web') as search, patch.object(repository, 'project_evidence', return_value=[paper]), patch.object(repository, 'local_search', return_value=[]):
            w.run(task, history=[])
        search.assert_not_called()

    def test_web_page_chunks_can_be_read_and_collected_separately(self):
        steps = iter(['search', 'page0', 'page1', 'read', 'finish'])
        snapshots, offsets = [], []
        def model(name, payload, schema):
            if name == 'runtime/route':
                return w.Route(action='web_research', task='topic', directions=['topic']) if not payload['results'] else w.Route(action='report')
            if name == 'literature/research':
                action = next(steps)
                if action == 'search': return w.Specialist(action='search', query='topic', plan=['read'])
                if action.startswith('page'):
                    if action == 'page1': snapshots.append(payload['web_blocks'][0])
                    return w.Specialist(action='read_web_page', candidate_ids=[payload['candidates'][0]['id']], offset=0 if action == 'page0' else 10, plan=['read'])
                return w.Specialist(action=action, candidate_ids=[payload['candidates'][0]['id']] if action == 'read' else [], plan=['read'])
            if name == 'literature/review': return w.Review(sufficient=True, summary='ok')
            if name == 'literature/synthesize':
                return w.Draft(sections=[w.Section(heading='结果', paragraphs=[w.Claim(text=e['text'], citations=[e['id']]) for e in payload['evidence']])])
            if name == 'literature/verify_claim': return w.Verdict(status='supported')
            raise AssertionError(name)
        original = w.registry.invoke
        def invoke(name, arguments, *, context):
            if name == 'read_web_page':
                offsets.append(arguments['offset'])
                return {'url': arguments['url'], 'title': 'page', 'markdown': 'topic first text' if arguments['offset'] == 0 else 'topic second text', 'offset': arguments['offset'], 'next_offset': 10 if arguments['offset'] == 0 else None, 'retrieved_at': '2026-11-07'}
            return original(name, arguments, context=context)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web', return_value=[result('https://example.com')]), patch.object(w.registry, 'invoke', side_effect=invoke):
            _, evidence = w.run(TASK, history=[])
        self.assertEqual(offsets, [0, 10])
        self.assertEqual(snapshots[0]['next_offset'], 10)
        self.assertEqual({e['text'] for e in evidence}, {'topic first text', 'topic second text'})
        self.assertEqual({e['char_start'] for e in evidence}, {0, 10})

    def test_page_failure_preserves_empty_or_available_search_snippet(self):
        steps = iter(['search', 'page', 'read', 'finish'])
        recent = []
        def model(name, payload, schema):
            if name == 'runtime/route': return w.Route(action='web_research', task='topic', directions=['topic']) if not payload['results'] else w.Route(action='report')
            if name == 'literature/research':
                action = next(steps)
                if action == 'search': return w.Specialist(action='search', query='topic', plan=['read'])
                if action == 'page': return w.Specialist(action='read_web_page', candidate_ids=[payload['candidates'][0]['id']], plan=['read'])
                recent.append(payload['recent_result'])
                return w.Specialist(action=action, candidate_ids=[payload['candidates'][0]['id']] if action == 'read' else [], plan=['read'])
            if name == 'literature/review': return w.Review(sufficient=True, summary='ok')
            if name == 'literature/synthesize': return w.Draft(sections=[w.Section(heading='结果', paragraphs=[w.Claim(text='Research found 95%.', citations=[payload['evidence'][0]['id']])])])
            if name == 'literature/verify_claim': return w.Verdict(status='supported')
            raise AssertionError(name)
        original = w.registry.invoke
        def invoke(name, arguments, *, context):
            if name == 'read_web_page': raise RuntimeError('private error')
            return original(name, arguments, context=context)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web', return_value=[result('https://example.com')]), patch.object(w.registry, 'invoke', side_effect=invoke):
            content, evidence = w.run(TASK, history=[])
        self.assertTrue(any('失败' in r for r in recent))
        self.assertEqual(evidence[0]['source_level'], 'web_snippet')
        self.assertNotIn('private error', content)

    def test_empty_search_preserves_candidates_and_reports_current_status(self):
        page = result('https://example.com')
        with patch.object(w.registry, 'invoke', side_effect=[[page], []]):
            first = w.search({'research': TASK, 'queries': ['first']})
            second = w.search({'research': TASK, 'queries': ['next'], **first})
        self.assertEqual(first['search_status'], 'results')
        self.assertEqual(second['search_status'], 'empty')
        self.assertEqual(second['candidates'][0]['url'], page['url'])
        self.assertIn('next', second['query_results'])

    def test_search_retains_url_without_snippet(self):
        with patch.object(w.registry, 'invoke', return_value=[result('https://example.com', content='')]):
            found = w.search({'research': TASK, 'queries': ['topic']})
        self.assertEqual(found['candidates'][0]['url'], 'https://example.com')

    def test_paper_role_cannot_read_web_page(self):
        def model(name, payload, schema):
            if name == 'runtime/route': return w.Route(action='paper_research', task='topic', directions=['topic'])
            return w.Specialist(action='read_web_page', candidate_ids=['C1'], plan=['read'])
        with patch.object(w, 'record_failure', return_value='test'), patch.object(w, 'structured', side_effect=model), patch.object(w.registry, 'invoke') as invoke:
            with self.assertRaises(ValueError): w.run({**TASK, 'source': 'local'}, history=[])
        invoke.assert_not_called()

    def test_auto_web_empty_then_failure_and_total_failure_can_switch_to_paper(self):
        for preceding_empty in (5, 0):
            with self.subTest(preceding_empty=preceding_empty):
                calls, reviews = [], []
                def model(name, payload, schema):
                    if name == 'runtime/route':
                        calls.append(payload)
                        if not payload['results']: return w.Route(action='web_research', task='topic', directions=['topic'])
                        if len(payload['results']) == 1: return w.Route(action='paper_research', task='topic', directions=['topic'])
                        return w.Route(action='report')
                    if name == 'literature/research':
                        if payload['role'] == 'paper': calls.append({'paper_invoked': True})
                        return w.Specialist(action='search', query='topic', plan=['search']) if payload['role'] == 'web' else w.Specialist(action='finish', plan=['finish'])
                    if name == 'literature/review':
                        reviews.append(payload)
                        return w.Review(sufficient=False, summary='missing', next_steps=[w.NextStep(direction='topic', goal='查找论文', route='paper_research')] if len(reviews) == 1 else [])
                    if name in ('literature/synthesize', 'literature/revise'): return w.Draft(sections=[])
                    raise AssertionError(name)
                with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web', side_effect=[[]] * preceding_empty + [RuntimeError('private')]) as search:
                    content, evidence = w.run({**TASK, 'source': 'auto'}, history=[])
                self.assertEqual(search.call_count, preceding_empty + 1)
                self.assertEqual(reviews[0]['results'][0]['search_status'], 'failed')
                self.assertEqual(reviews[0]['results'][0]['source'], 'web')
                self.assertTrue(any(item.get('paper_invoked') for item in calls))
                self.assertIn('证据不足', content)
                self.assertNotIn('private', content)
                self.assertEqual(evidence, [])

    def test_typed_failures_pause_and_empty_success_resets(self):
        from src.integrations.web_search import WebSearchError
        error = WebSearchError('private', 'provider_error')
        with patch.object(w.registry, 'invoke', side_effect=[error, [], error, error]):
            state = {'research': TASK, 'queries': ['topic']}
            state.update(w.search(state)); self.assertFalse(state['web_search_blocked'])
            state.update(w.search(state)); self.assertEqual(state['web_failures'], 0)
            state.update(w.search(state)); self.assertFalse(state['web_search_blocked'])
            state.update(w.search(state)); self.assertEqual(state['web_search_blocked'], 'provider_error')
        with patch.object(w.registry, 'invoke', side_effect=WebSearchError('private', 'challenge', 202)):
            self.assertEqual(w.search({'research': TASK, 'queries': ['topic']})['web_search_blocked'], 'challenge')

    def test_blocked_search_direct_public_page_read_and_collect(self):
        from src.integrations.web_search import WebSearchError
        steps = iter(['search', 'direct', 'read', 'finish'])
        def model(name, payload, schema):
            if name == 'runtime/route': return w.Route(action='web_research', task='选举', directions=['选举'])
            if name == 'literature/research':
                step = next(steps)
                if step == 'search': return w.Specialist(action='search', query='election', plan=['search'])
                self.assertEqual(payload['search_blocked'], 'challenge')
                if step == 'direct': return w.Specialist(action='read_web_page', url='https://example.org/official', query='election', plan=['read'])
                if step == 'read': return w.Specialist(action='read', candidate_ids=[payload['candidates'][0]['id']], plan=['collect'])
                return w.Specialist(action='finish', plan=['finish'])
            if name == 'literature/review':
                self.assertEqual(payload['search_blocked'], 'challenge')
                return w.Review(sufficient=True, summary='ok')
            if name == 'literature/synthesize': return w.Draft(sections=[w.Section(heading='结果', paragraphs=[w.Claim(text='election results', citations=[payload['evidence'][0]['id']])])])
            if name == 'literature/verify_claim': return w.Verdict(status='supported')
            raise AssertionError(name)
        original = w.registry.invoke
        def invoke(name, arguments, *, context):
            if name == 'search_web': raise WebSearchError('private', 'challenge', 202)
            if name == 'read_web_page': return {'url': arguments['url'], 'title': 'Official', 'markdown': 'election results', 'offset': 0, 'next_offset': None, 'retrieved_at': '2026-11-07'}
            return original(name, arguments, context=context)
        with patch.object(w, 'structured', side_effect=model), patch.object(w.registry, 'invoke', side_effect=invoke):
            content, evidence = w.run({**TASK, 'query': '最近的选举结果'}, history=[])
        self.assertIn('election results', content)
        self.assertEqual(evidence[0]['original_url'], 'https://example.org/official')

    def test_blocked_invalid_or_previously_attempted_url_finishes_safely(self):
        for url, prior in [('https://[broken', []), ('HTTPS://EXAMPLE.ORG', ['https://example.org/']), ('https://example.org/?', [])]:
            with self.subTest(url=url):
                payloads = []
                def model(name, payload, schema):
                    payloads.append(payload)
                    return w.Specialist(action='read_web_page', url=url, query='topic', plan=['read'])
                state = {'research': TASK, 'task': 'topic', 'history': [], 'web_search_blocked': 'challenge', 'direct_url_attempts': prior, 'candidates': [], 'evidence': [], 'plan': []}
                with patch.object(w, 'structured', side_effect=model), patch.object(w, 'record_failure', return_value='test'), patch.object(w.registry, 'invoke') as invoke:
                    result = w._research_graph('web', None).invoke(state)
                self.assertEqual(result['choice'].action, 'finish')
                self.assertEqual(len(payloads), 2)
                self.assertNotIn('search_web', {t['name'] for t in payloads[0]['tools']})
                invoke.assert_not_called()

    def test_explicit_web_single_transient_no_plan_keeps_retryable_failure(self):
        from src.integrations.web_search import WebSearchError
        def model(name, payload, schema):
            if name == 'runtime/route': return w.Route(action='web_research', task='topic', directions=['topic'])
            if name == 'literature/research': return w.Specialist(action='search', query='topic', plan=['search'])
            if name == 'literature/review': return w.Review(sufficient=False, gaps=['missing'], summary='missing')
            raise AssertionError(name)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web', side_effect=WebSearchError('private', 'http_status', 503)):
            with self.assertRaisesRegex(RuntimeError, '网页检索失败') as error:
                w.run(TASK, history=[])
        self.assertNotIn('private', str(error.exception))

    def test_web_direct_is_rejected_before_tool_call(self):
        with patch.object(w, 'record_failure', return_value='diag-test'), patch.object(w, 'structured', return_value=w.Route(action='direct', answer='answer')), patch.object(w.registry, 'invoke') as invoke:
            with self.assertRaises(ValueError):
                w.run(TASK, history=[])
        invoke.assert_not_called()


if __name__ == '__main__':
    unittest.main()
