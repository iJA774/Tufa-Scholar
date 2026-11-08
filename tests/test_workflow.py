import unittest
from types import SimpleNamespace
from unittest.mock import patch
from src.agents import literature as w
from src.persistence import repository


TASK = {'project_id': 2, 'query': 'attention', 'source': 'local'}


def route(**kwargs):
    if kwargs.get('action') in ('paper_research', 'web_research'):
        kwargs.setdefault('directions', ['研究方向'])
    return w.Route(**kwargs)


def specialist(**kwargs):
    kwargs.setdefault('plan', ['检索并取证'])
    return w.Specialist(**kwargs)


def draft(claims=None, limitations='', sections=None):
    return w.Draft(sections=sections if sections is not None else ([w.Section(heading='结果', paragraphs=claims)] if claims else []))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        diagnostic = patch.object(w, 'record_failure', return_value='diag-test')
        self.record_failure = diagnostic.start()
        self.addCleanup(diagnostic.stop)
        report_diagnostic = patch('src.agents.report.record_failure', return_value='diag-test', create=True)
        report_diagnostic.start()
        self.addCleanup(report_diagnostic.stop)

    def test_auto_direct_needs_no_tools(self):
        def model(name, payload, schema):
            self.assertEqual(payload['source'], 'auto')
            self.assertIsNone(payload['effective_source'])
            self.assertEqual(set(payload['available_actions']), {'direct', 'paper_research', 'web_research'})
            return route(action='direct', answer='你好')
        with patch.object(w, 'structured', side_effect=model), patch.object(w.registry, 'invoke') as invoke:
            content, evidence = w.run({**TASK, 'source': 'auto'}, history=[])
        self.assertIn('你好', content)
        self.assertEqual(evidence, [])
        invoke.assert_not_called()

    def test_auto_research_locks_source_and_uses_matching_tools(self):
        for action, effective in [('paper_research', 'all'), ('web_research', 'web')]:
            with self.subTest(action=action):
                routes, catalogs, stages = [], [], []
                paper = {'id': 3, 'title': 'paper', 'abstract': 'evidence', 'versions': []}
                page = {'title': 'web page', 'url': 'https://example.com/', 'content': 'evidence', 'source_level': 'web_content', 'provider': 'fixture', 'retrieved_at': '2026-11-07'}
                def model(name, payload, schema):
                    if name == 'runtime/route':
                        routes.append(payload)
                        if len(payload['results']) < 2:
                            return route(action=action, task='topic')
                        return route(action='report')
                    if name == 'literature/research':
                        catalogs.append((payload['source'], {t['name'] for t in payload['tools']}))
                        if not payload['recent_result']: return specialist(action='search', query='topic')
                        if payload['recent_result'].startswith('检索'): return specialist(action='read', candidate_ids=[payload['candidates'][0]['id']])
                        return specialist(action='finish')
                    if name == 'literature/review': return w.Review(sufficient=False, gaps=['more'], summary='missing')
                    if name == 'literature/synthesize': return draft(claims=[w.Claim(text='evidence', citations=[payload['evidence'][0]['id']])])
                    if name == 'literature/verify_claim': return w.Verdict(status='supported')
                    raise AssertionError(name)
                with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web', return_value=[page]), patch('src.tools.search.retrieve', return_value={'results': [paper]}), patch.object(repository, 'project_evidence', return_value=[paper]), patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as invoke:
                    content, evidence = w.run({**TASK, 'source': 'auto'}, history=[], on_stage=stages.append)
                self.assertEqual(routes[0]['source'], 'auto')
                self.assertIsNone(routes[0]['effective_source'])
                self.assertEqual(routes[1]['source'], 'auto')
                self.assertEqual(routes[1]['effective_source'], effective)
                self.assertIn(action, routes[1]['available_actions'])
                self.assertIn('web_research' if effective == 'all' else 'paper_research', routes[1]['available_actions'])
                self.assertTrue(all(source == effective for source, _ in catalogs))
                expected_search = 'search_web' if effective == 'web' else 'search_papers'
                self.assertTrue(all(names == ({expected_search, 'collect_evidence', 'read_web_page'} if effective == 'web' else {expected_search, 'collect_evidence'}) for _, names in catalogs))
                self.assertTrue(all(c.kwargs['context'].source == effective for c in invoke.call_args_list))
                self.assertEqual([c.args[0] for c in invoke.call_args_list], [expected_search, 'collect_evidence'] * 2)
                if effective == 'web':
                    self.assertIn('searching_web', stages)
                    self.assertEqual(evidence[0]['original_url'], page['url'])
                    self.assertEqual(evidence[0]['provider'], 'fixture')
                    self.assertIn(page['url'], content)
                else:
                    self.assertIn('searching', stages)

    def test_review_can_switch_sources_without_mixing_candidates(self):
        for source, first, second in [('all', 'paper_research', 'web_research'), ('web', 'web_research', 'paper_research')]:
            with self.subTest(source=source):
                paper = {'id': 3, 'title': 'paper', 'abstract': 'paper evidence', 'versions': []}
                page = {'title': 'web page', 'url': 'https://example.com/', 'content': 'web evidence', 'source_level': 'web_content', 'provider': 'fixture', 'retrieved_at': '2026-11-07'}
                seen, reviews = [], []
                def model(name, payload, schema):
                    if name == 'runtime/route':
                        action = first if not payload['results'] else second
                        if payload['results']:
                            self.assertEqual(payload['available_actions'], [second])
                            self.assertNotIn('report', payload['available_actions'])
                        return route(action=action, task='补查', directions=['方法'] if not payload['results'] else ['对照'])
                    if name == 'literature/research':
                        if not payload['recent_result']: return specialist(action='search', query='topic')
                        if payload['recent_result'].startswith('检索'):
                            seen.append((payload['role'], [c['title'] for c in payload['candidates']]))
                            return specialist(action='read', candidate_ids=[payload['candidates'][0]['id']])
                        return specialist(action='finish')
                    if name == 'literature/review':
                        reviews.append(payload)
                        return w.Review(sufficient=False, gaps=['对照'], summary='不足', coverage=['方法'], next_steps=[w.NextStep(direction='对照', goal='补查', route=second)])
                    if name in ('literature/synthesize', 'literature/revise'):
                        return draft(claims=[w.Claim(text=e['text'], citations=[e['id']]) for e in payload['evidence']])
                    if name == 'literature/verify_claim': return w.Verdict(status='supported')
                    raise AssertionError(name)
                # After the second review choose report.
                wrapped = model
                def limited(name, payload, schema):
                    if name == 'runtime/route' and len(payload['results']) >= 2: return route(action='report')
                    return wrapped(name, payload, schema)
                with patch.object(w, 'structured', side_effect=limited), patch('src.tools.search.search_web', return_value=[page]), patch('src.tools.search.retrieve', return_value={'results': [paper]}), patch.object(repository, 'project_evidence', return_value=[paper]), patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as invoke:
                    _, evidence = w.run({**TASK, 'source': source}, history=[])
                self.assertEqual(len(reviews), 2)
                self.assertEqual({role for role, _ in seen}, {'paper', 'web'})
                self.assertTrue(all(names == (['paper'] if role == 'paper' else ['web page']) for role, names in seen))
                self.assertEqual(len(evidence), 2)
                self.assertEqual({c.kwargs['context'].source for c in invoke.call_args_list}, {'all', 'web'})
                self.assertEqual(reviews[1]['directions'], ['方法', '对照'])

    def test_empty_paper_result_can_switch_to_web_after_review(self):
        actions, reviews = [], []
        page = {'title': 'page', 'url': 'https://example.com', 'content': 'evidence', 'source_level': 'web_snippet', 'provider': 'fixture', 'retrieved_at': '2026-11-07'}
        def model(name, payload, schema):
            if name == 'runtime/route':
                action = 'paper_research' if not payload['results'] else 'web_research'
                actions.append(action)
                return route(action=action, task='topic')
            if name == 'literature/research':
                if not payload['recent_result']: return specialist(action='search', query='topic')
                if payload['candidates'] and payload['recent_result'].startswith('检索'): return specialist(action='read', candidate_ids=[payload['candidates'][0]['id']])
                return specialist(action='finish')
            if name == 'literature/review':
                reviews.append(payload)
                return w.Review(sufficient=False, summary='missing')
            if name in ('literature/synthesize', 'literature/revise'): return draft()
            raise AssertionError(name)
        def bounded(name, payload, schema):
            if name == 'runtime/route' and len(payload['results']) == 2: return route(action='report')
            return model(name, payload, schema)
        with patch.object(w, 'structured', side_effect=bounded), patch('src.tools.search.retrieve', return_value={'results': []}), patch('src.tools.search.search_web', return_value=[page]):
            w.run({**TASK, 'source': 'all'}, history=[])
        self.assertEqual(actions, ['paper_research', 'web_research'])
        self.assertEqual(len(reviews), 2)
        self.assertEqual(reviews[0]['evidence'], [])
        self.assertTrue(reviews[1]['evidence'])

    def test_local_and_collection_never_expand_after_review(self):
        for source in ('local', 'collection'):
            with self.subTest(source=source):
                calls = []
                def model(name, payload, schema):
                    if name == 'runtime/route':
                        calls.append(payload)
                        return route(action='paper_research', task='topic')
                    if name == 'literature/research': return specialist(action='finish')
                    if name == 'literature/review': return w.Review(sufficient=False, summary='missing')
                    if name in ('literature/synthesize', 'literature/revise'): return draft()
                    raise AssertionError(name)
                with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.search_web') as web:
                    w.run({**TASK, 'source': source, 'paper_ids': [3]}, history=[])
                self.assertTrue(all('web_research' not in call['available_actions'] for call in calls))
                web.assert_not_called()

    def test_report_missing_evidence_triggers_one_research_then_report(self):
        paper = {'id': 3, 'title': 'paper', 'abstract': 'evidence', 'versions': []}
        routes, drafts = [], []
        def model(name, payload, schema):
            if name == 'runtime/route':
                routes.append(payload)
                if payload.get('verification_gaps'):
                    self.assertEqual(payload['available_actions'], ['paper_research'])
                return route(action='paper_research', task='查证') if not payload['results'] or payload.get('verification_gaps') and len(payload['results']) == 1 else route(action='report')
            if name == 'literature/research':
                if not payload['recent_result']: return specialist(action='search', query='查证')
                if payload['recent_result'].startswith('检索'): return specialist(action='read', candidate_ids=[payload['candidates'][0]['id']])
                return specialist(action='finish')
            if name == 'literature/review': return w.Review(sufficient=True, summary='ok')
            if name == 'literature/synthesize':
                drafts.append(payload)
                return draft(claims=[w.Claim(text='evidence', citations=[payload['evidence'][0]['id']])])
            if name == 'literature/verify_claim':
                return w.Verdict(status='missing_evidence', reason='缺对照', research_query='对照', research_route='paper_research') if len(drafts) == 1 else w.Verdict(status='supported')
            raise AssertionError(name)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.retrieve', return_value={'results': [paper]}), patch.object(repository, 'project_evidence', return_value=[paper]):
            content, evidence = w.run(TASK, history=[])
        self.assertEqual(len(drafts), 2)
        self.assertTrue(drafts[1]['verification_gaps'])
        self.assertEqual(len(evidence), 1)
        self.assertIn('evidence', content)

    def test_report_gap_cannot_expand_local_scope(self):
        paper = {'id': 3, 'title': 'paper', 'abstract': 'evidence', 'versions': []}
        routes = []
        def model(name, payload, schema):
            if name == 'runtime/route':
                routes.append(payload)
                return route(action='paper_research', task='查证') if not payload['results'] else route(action='report')
            if name == 'literature/research':
                if not payload['recent_result']: return specialist(action='search', query='topic')
                if payload['recent_result'].startswith('检索'): return specialist(action='read', candidate_ids=[payload['candidates'][0]['id']])
                return specialist(action='finish')
            if name == 'literature/review': return w.Review(sufficient=True, summary='ok')
            if name == 'literature/synthesize': return draft(claims=[w.Claim(text='evidence', citations=[payload['evidence'][0]['id']])])
            if name == 'literature/verify_claim': return w.Verdict(status='missing_evidence', reason='缺数据', research_query='检索', research_route='web_research')
            raise AssertionError(name)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.retrieve', return_value={'results': [paper]}), patch.object(repository, 'project_evidence', return_value=[paper]), patch('src.tools.search.search_web') as web:
            content, _ = w.run(TASK, history=[])
        self.assertEqual(len(routes), 2)
        self.assertIn('证据不足', content)
        web.assert_not_called()

    def test_direct_graph_uses_history_without_tools(self):
        history = [{'role': 'user', 'content': 'prior question'}]
        def model(name, payload, schema):
            self.assertEqual(name, 'runtime/route')
            self.assertEqual(payload['history'], history)
            return route(action='direct', answer='你好')
        with patch.object(w, 'structured', side_effect=model), patch.object(w.registry, 'invoke') as invoke:
            content, evidence = w.run(TASK, history=history)
        self.assertIn('你好', content)
        self.assertEqual(evidence, [])
        invoke.assert_not_called()

    def test_autonomous_search_and_read_then_review_and_report(self):
        paper = {'id': 3, 'title': 'paper', 'abstract': 'attention study', 'versions': []}
        actions = iter(['search', 'search', 'read', 'finish'])
        calls = []
        def model(name, payload, schema):
            calls.append(name)
            if name == 'runtime/route':
                action = 'paper_research' if not payload['results'] else ('review' if payload['review'] is None else 'report')
                return route(action=action, task='attention' if action == 'paper_research' else '')
            if name == 'literature/research':
                action = next(actions)
                return specialist(action=action, query='attention' if action == 'search' else '', candidate_ids=[payload['candidates'][0]['id']] if action == 'read' else [])
            if name == 'literature/review': return w.Review(sufficient=True, summary='ok')
            if name in ('literature/synthesize', 'literature/revise'): return draft()
            raise AssertionError(name)
        with patch.object(repository, 'project_evidence', return_value=[paper]), patch('src.tools.search.retrieve', return_value={'results': [paper]}), patch.object(w, 'structured', side_effect=model), patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as invoke:
            w.run(TASK, history=[])
        self.assertEqual([c.args[0] for c in invoke.call_args_list], ['search_papers', 'search_papers', 'collect_evidence'])
        self.assertIn('literature/review', calls)

    def test_forged_candidate_never_calls_collect(self):
        def model(name, payload, schema):
            if name == 'runtime/route': return route(action='paper_research', task='attention')
            return specialist(action='read', candidate_ids=['fake'])
        with patch.object(w, 'structured', side_effect=model), patch.object(w.registry, 'invoke') as invoke:
            with self.assertRaisesRegex(ValueError, '候选'):
                w.run(TASK, history=[])
        invoke.assert_not_called()

    def test_rejected_claim_can_be_revised_using_original_evidence(self):
        paper = {'id': 3, 'title': 'paper', 'abstract': 'Accuracy was 92%.', 'versions': []}
        revisions = []
        def model(name, payload, schema):
            if name == 'runtime/route':
                action = 'paper_research' if not payload['results'] else ('review' if payload['review'] is None else 'report')
                return route(action=action, task='accuracy' if action == 'paper_research' else '')
            if name == 'literature/research':
                if not payload['recent_result']: return specialist(action='search', query='accuracy')
                if not payload['evidence']: return specialist(action='read', candidate_ids=[payload['candidates'][0]['id']])
                return specialist(action='finish')
            if name == 'literature/review': return w.Review(sufficient=True, summary='ok')
            if name == 'literature/synthesize': return draft(claims=[w.Claim(text='Accuracy 92%', citations=[payload['evidence'][0]['id']])])
            if name == 'literature/revise':
                revisions.append(payload['evidence'])
                return schema(patches=[{'section_index': 0, 'paragraph_index': 0, 'text': 'Accuracy 92%', 'citations': [payload['evidence'][0]['id']]}])
            if name == 'literature/verify_claim': return w.Verdict(status='qualified', reason='需限定', suggestion='限定') if not revisions else w.Verdict(status='supported')
            raise AssertionError(name)
        with patch.object(repository, 'project_evidence', return_value=[paper]), patch('src.tools.search.retrieve', return_value={'results': [paper]}), patch.object(w, 'structured', side_effect=model):
            content, evidence = w.run(TASK, history=[])
        self.assertTrue(revisions and revisions[0])
        self.assertIn('92%', content)
        self.assertNotIn('99%', content)
        self.assertEqual(len(evidence), 1)

    def test_supplement_research_rechecks_and_keeps_stable_candidates(self):
        old = {'id': 1, 'title': 'old', 'abstract': 'old evidence', 'versions': []}
        new = {'id': 2, 'title': 'new', 'abstract': 'new evidence', 'versions': []}
        identifiers, reviews, tasks, tools = {}, [], [], []
        def model(name, payload, schema):
            if name == 'runtime/route':
                if not payload['results']:
                    return route(action='paper_research', task='initial')
                if 'review' in payload['available_actions']:
                    return route(action='review')
                if 'paper_research' in payload['available_actions']:
                    return route(action='paper_research', task='supplement')
                return route(action='report')
            if name == 'literature/research':
                tools.append([t['name'] for t in payload['tools']])
                if not payload['recent_result']:
                    tasks.append(payload['task'])
                    return specialist(action='search', query=payload['task'])
                if payload['recent_result'].startswith('检索'):
                    for c in payload['candidates']:
                        if c['title'] in identifiers:
                            self.assertEqual(identifiers[c['title']], c['id'])
                        identifiers[c['title']] = c['id']
                    return specialist(action='read', candidate_ids=[c['id'] for c in payload['candidates']])
                return specialist(action='finish')
            if name == 'literature/review':
                reviews.append(payload['evidence'])
                return w.Review(sufficient=False, gaps=['supplement'], summary='missing')
            if name in ('literature/synthesize', 'literature/revise'): return draft()
            raise AssertionError(name)
        with patch.object(repository, 'project_evidence', return_value=[old, new]), patch('src.tools.search.retrieve', side_effect=[{'results': [old]}, {'results': [new, old]}, {'results': [new, old]}]), patch.object(w, 'structured', side_effect=model):
            w.run(TASK, history=[])
        self.assertEqual(tasks, ['initial', 'supplement', 'supplement'])
        self.assertEqual(len(reviews), 3)
        self.assertTrue({'old', 'new'} <= {e['title'] for e in reviews[-1]})
        self.assertTrue(all('search_web' not in names for names in tools))

    def test_supplement_task_selects_pdf_page(self):
        paper = {'id': 3, 'title': 'paper', 'versions': [{'id': 7, 'path': 'fixture.pdf', 'text_status': 'text_available', 'pages_indexed': True}]}
        pages = [{'page': 1, 'text': 'attention initial'}, {'page': 2, 'text': 'latency supplement details'}]
        snapshots = []
        def model(name, payload, schema):
            if name == 'runtime/route':
                if not payload['results']: return route(action='paper_research', task='attention')
                if 'review' in payload['available_actions']: return route(action='review')
                if 'paper_research' in payload['available_actions']: return route(action='paper_research', task='latency')
                return route(action='report')
            if name == 'literature/research':
                if not payload['recent_result']: return specialist(action='search', query=payload['task'])
                if payload['recent_result'].startswith('检索'): return specialist(action='read', candidate_ids=[payload['candidates'][0]['id']])
                return specialist(action='finish')
            if name == 'literature/review':
                snapshots.append(payload['evidence'])
                return w.Review(sufficient=False, gaps=['latency'], summary='missing')
            if name in ('literature/synthesize', 'literature/revise'): return draft()
            raise AssertionError(name)
        with patch.object(repository, 'project_evidence', return_value=[paper]), patch.object(repository, 'version_pages', return_value=pages), patch('src.tools.search.retrieve', return_value={'results': [paper]}) as retrieve, patch.object(w, 'structured', side_effect=model):
            w.run(TASK, history=[])
        self.assertEqual([c.args[1] for c in retrieve.call_args_list], ['attention', 'latency'])
        self.assertTrue(any('latency' in e['text'] for e in snapshots[-1]))
        self.assertTrue(any('attention' in e['text'] for e in snapshots[-1]))

    def test_specialist_search_budget_is_six(self):
        def model(name, payload, schema):
            if name == 'runtime/route':
                action = 'paper_research' if not payload['results'] else ('review' if payload['review'] is None else 'report')
                return route(action=action, task='attention' if action == 'paper_research' else '')
            if name == 'literature/research': return specialist(action='search', query='attention')
            if name == 'literature/review': return w.Review(sufficient=False, gaps=['missing'], summary='missing')
            if name in ('literature/synthesize', 'literature/revise'): return draft()
            raise AssertionError(name)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.retrieve', return_value={'results': []}), patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as invoke:
            content, _ = w.run(TASK, history=[])
        self.assertEqual([c.args[0] for c in invoke.call_args_list], ['search_papers'] * 6)
        self.assertIn('证据不足', content)

    def test_delegation_budget_stops_after_three_with_new_evidence(self):
        papers = [{'id': i, 'title': f'paper {i}', 'abstract': f'evidence {i}', 'versions': []} for i in range(1, 4)]
        tasks, remaining = [], []
        def model(name, payload, schema):
            if name == 'runtime/route':
                remaining.append(payload['delegations_remaining'])
                if not payload['results'] or ('paper_research' in payload['available_actions'] and payload['review'] is not None):
                    return route(action='paper_research', task=f'topic {len(tasks)}')
                return route(action='review' if 'review' in payload['available_actions'] else 'report')
            if name == 'literature/research':
                if not payload['recent_result']:
                    tasks.append(payload['task'])
                    return specialist(action='search', query=payload['task'])
                if payload['recent_result'].startswith('检索'):
                    return specialist(action='read', candidate_ids=[next(c['id'] for c in payload['candidates'] if c['title'] == papers[len(tasks)-1]['title'])])
                return specialist(action='finish')
            if name == 'literature/review': return w.Review(sufficient=False, gaps=['missing'], summary='missing')
            if name in ('literature/synthesize', 'literature/revise'): return draft()
            raise AssertionError(name)
        with patch.object(repository, 'project_evidence', return_value=papers), patch('src.tools.search.retrieve', side_effect=[{'results': [p]} for p in papers]), patch.object(w, 'structured', side_effect=model):
            content, _ = w.run(TASK, history=[])
        self.assertEqual(len(tasks), 3)
        self.assertEqual(remaining[-1], 1)  # Budget exhaustion routes without another model call.
        self.assertIn('证据不足', content)

    def test_forbidden_route_never_invokes_tools(self):
        for source, action in [('collection', 'direct'), ('collection', 'web_research'), ('web', 'paper_research'), ('all', 'web_research')]:
            with self.subTest(source=source, action=action):
                research = {**TASK, 'source': source, 'paper_ids': [3]}
                choice = route(action=action, task='task', answer='answer')
                with patch.object(w, 'structured', return_value=choice), patch.object(w.registry, 'invoke') as invoke:
                    with self.assertRaisesRegex(ValueError, '不允许'):
                        w.run(research, history=[])
                invoke.assert_not_called()

    def test_no_progress_forces_review_and_report_without_another_route(self):
        routes, reviews = [], []
        def model(name, payload, schema):
            if name == 'runtime/route':
                routes.append(payload)
                return route(action='paper_research', task='topic') if not payload['results'] else route(action='report')
            if name == 'literature/research': return specialist(action='search', query='topic')
            if name == 'literature/review':
                reviews.append(payload)
                return w.Review(sufficient=False, gaps=['缺少证据'], summary='不足')
            if name in ('literature/synthesize', 'literature/revise'): return draft()
            raise AssertionError(name)
        with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.retrieve', return_value={'results': []}), patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as invoke:
            content, _ = w.run(TASK, history=[])
        self.assertEqual(len(routes), 1)
        self.assertEqual(len(reviews), 1)
        self.assertEqual([c.args[0] for c in invoke.call_args_list], ['search_papers'] * 6)
        self.assertIn('证据不足', content)

    def test_invalid_route_retries_with_feedback_before_any_tool(self):
        payloads = []
        def model(name, payload, schema):
            payloads.append(payload)
            return route(action='web_research', task='bad') if len(payloads) == 1 else route(action='direct', answer='ok')
        with patch.object(w, 'structured', side_effect=model), patch.object(w.registry, 'invoke') as invoke:
            content, _ = w.run(TASK, history=[])
        self.assertIn('ok', content)
        self.assertEqual(payloads[1]['validation_feedback']['code'], 'invalid_action')
        recorded = self.record_failure.call_args.kwargs
        self.assertEqual(recorded['kind'], 'semantic_route')
        self.assertEqual(recorded['response']['action'], 'web_research')
        self.assertEqual(recorded['context']['allowed_actions'], payloads[0]['available_actions'])
        self.assertEqual(recorded['payload'], payloads[0])
        self.assertEqual(recorded['schema'], w.Route.model_json_schema())
        self.assertEqual(payloads[1]['validation_feedback']['allowed_actions'], payloads[0]['available_actions'])
        invoke.assert_not_called()

    def test_candidate_ids_retry_then_read_or_fail_safely(self):
        paper = {'id': 3, 'title': 'paper', 'abstract': 'evidence', 'versions': []}
        for corrected in (True, False):
            with self.subTest(corrected=corrected):
                attempts = []
                def model(name, payload, schema):
                    if name == 'runtime/route':
                        action = 'paper_research' if not payload['results'] else ('review' if payload['review'] is None else 'report')
                        return route(action=action, task='topic' if action == 'paper_research' else '')
                    if name == 'literature/research':
                        if not payload['recent_result']: return specialist(action='search', query='topic')
                        if payload['recent_result'].startswith('检索'):
                            attempts.append(payload)
                            ids = [payload['candidates'][0]['id']] if corrected and len(attempts) == 2 else ['fake', 'fake']
                            return specialist(action='read', candidate_ids=ids)
                        return specialist(action='finish')
                    if name == 'literature/review': return w.Review(sufficient=True, summary='ok')
                    if name in ('literature/synthesize', 'literature/revise'): return draft()
                    raise AssertionError(name)
                with patch.object(repository, 'project_evidence', return_value=[paper]), patch('src.tools.search.retrieve', return_value={'results': [paper]}), patch.object(w, 'structured', side_effect=model), patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as invoke:
                    if corrected: w.run(TASK, history=[])
                    else:
                        with self.assertRaisesRegex(ValueError, '候选'): w.run(TASK, history=[])
                self.assertEqual(len(attempts), 2)
                self.assertEqual(attempts[1]['validation_feedback']['code'], 'invalid_candidate_ids')
                records = [c.kwargs for c in self.record_failure.call_args_list if c.kwargs['kind'] == 'semantic_candidates']
                self.assertEqual(len(records), 1 if corrected else 3)
                self.assertEqual(records[-1]['response']['candidate_ids'], ['fake', 'fake'])
                self.assertEqual(records[-1]['context']['candidate_ids'], [attempts[0]['candidates'][0]['id']])
                self.assertEqual(records[-1]['schema'], w.Specialist.model_json_schema())
                calls = [c.args[0] for c in invoke.call_args_list]
                self.assertEqual(calls.count('collect_evidence'), 1 if corrected else 0)

    def test_repeated_query_deduplicates_ids_without_extra_votes(self):
        from src.literature.retrieval import rank_candidates
        first = {'id': 1, 'title': 'first'}
        repeated = {'id': 2, 'title': 'repeated'}
        new = {'id': 3, 'title': 'new'}
        entries = [{'paper': first, 'rank': 1}, {'paper': repeated, 'rank': 2},
                   {'paper': repeated, 'rank': 2}, {'paper': new, 'rank': 3}]
        result = rank_candidates({'same query': entries}, 'local')
        self.assertEqual([p['id'] for p in result], [1, 2, 3])

    def test_same_evidence_with_new_transient_id_is_not_new(self):
        first = {'id': 'a1', 'paper_id': 3, 'title': 'paper', 'source_level': 'abstract', 'text': 'same content'}
        with patch.object(w.registry, 'invoke', return_value=[first]):
            initial = w.evidence({'research': TASK, 'papers': []})
        with patch.object(w.registry, 'invoke', return_value=[{**first, 'id': 'a6'}]):
            repeated = w.evidence({'research': TASK, 'papers': [], **initial})
        self.assertTrue(initial['new_evidence'])
        self.assertFalse(repeated['new_evidence'])

    def test_accumulates_and_disambiguates_ids_across_rounds(self):
        first = {'id': 'a1', 'paper_id': 1, 'title': 'original', 'text': 'attention result'}
        second = {'id': 'a1', 'paper_id': 2, 'title': 'new', 'text': 'latency result'}
        with patch.object(w.registry, 'invoke', side_effect=[[first], [second]]):
            state = {'research': TASK, 'papers': []}
            state.update(w.evidence(state))
            state.update(evidence_query='latency', papers=[])
            state.update(w.evidence(state))
        self.assertEqual({e['id'] for e in state['evidence']}, {'a1', 'a1_2'})
        self.assertEqual(len(state['evidence_pool']), 2)

    def test_bounded_selection_reserves_distinct_facets(self):
        pool = [{'id': str(i), 'title': '', 'text': 'attention transformer ' + 'x' * 1178} for i in range(25)]
        pool += [{'id': 'latency', 'title': '', 'text': 'transformer latency details'},
                 {'id': 'energy', 'title': '', 'text': 'energy details'}]
        selected = w._select_evidence(pool, ['attention transformer', 'transformer latency', 'energy'])
        self.assertLessEqual(len(selected), w.EVIDENCE_LIMIT)
        self.assertLessEqual(sum(len(e['text']) for e in selected), w.EVIDENCE_TEXT_LIMIT)
        self.assertTrue({'latency', 'energy'} <= {e['id'] for e in selected})

    def test_rejects_missing_cost_bad_ids_and_invented_numbers(self):
        evidence = [{'id': 'e1', 'text': 'Accuracy was 95%.', 'title': 'paper', 'version_id': None}]
        claims = [w.Claim(text='成本降低', citations=['e1']), w.Claim(text='准确率 99%', citations=['e1']), w.Claim(text='结果', citations=['fake'])]
        state = {'research': TASK, 'evidence': evidence, 'draft': draft(claims=claims, limitations='成本下降 70%')}
        with patch.object(w, 'structured', return_value=w.Verdict(status='unsupported', reason='不支持')) as model:
            result = w.verify(state)
        self.assertEqual(model.call_count, 0)
        self.assertNotIn('成本降低', result['content'])
        self.assertNotIn('70%', result['content'])
        self.assertEqual(result['evidence'], [])

    def test_accuracy_number_cannot_be_reused_as_cost_evidence(self):
        state = {'research': TASK, 'evidence':[{'id':'e1','text':'Accuracy 92%'}], 'draft':draft(claims=[w.Claim(text='成本降低 92%',citations=['e1'])])}
        with patch.object(w, 'structured', return_value=w.Verdict(status='supported')) as model:
            result = w.verify(state)
        model.assert_not_called()
        self.assertEqual(result['evidence'], [])
        self.assertIn('证据不足', result['content'])

    def test_pdf_page_and_version_anchor(self):
        pages = [SimpleNamespace(extract_text=lambda: 'other'), SimpleNamespace(extract_text=lambda: 'attention 95%')]
        paper = {'id': 3, 'title': 'paper'}
        local = [{'id': 3, 'title': 'paper', 'versions': [{'id': 7, 'path': 'fixture.pdf', 'text_status': 'text_available', 'pages_indexed': True}]}]
        with patch.object(repository, 'project_evidence', return_value=local), patch.object(repository, 'version_pages', return_value=[{'page': i, 'text': page.extract_text()} for i, page in enumerate(pages, 1)]):
            result = w.evidence({'research': TASK, 'papers': [paper]})['evidence']
        self.assertEqual((result[0]['version_id'], result[0]['page'], result[0]['id']), (7, 2, 'p3v7p2'))
