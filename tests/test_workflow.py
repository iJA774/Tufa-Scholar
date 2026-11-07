import unittest
from types import SimpleNamespace
from unittest.mock import patch
from src.agents import literature as w
from src.persistence import repository


TASK = {'project_id': 2, 'query': 'attention', 'source': 'local'}


class WorkflowTests(unittest.TestCase):
    def test_direct_graph_uses_history_without_tools(self):
        history = [{'role': 'user', 'content': 'prior question'}]
        def model(name, payload, schema):
            self.assertEqual(name, 'runtime/route')
            self.assertEqual(payload['history'], history)
            return w.Route(action='direct', answer='你好')
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
                return w.Route(action=action, task='attention' if action == 'paper_research' else '')
            if name == 'literature/research':
                action = next(actions)
                return w.Specialist(action=action, query='attention' if action == 'search' else '', candidate_ids=[payload['candidates'][0]['id']] if action == 'read' else [])
            if name == 'literature/review': return w.Review(sufficient=True, summary='ok')
            if name == 'literature/synthesize': return w.Draft()
            raise AssertionError(name)
        with patch.object(repository, 'project_evidence', return_value=[paper]), patch('src.tools.search.retrieve', return_value={'results': [paper]}), patch.object(w, 'structured', side_effect=model), patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as invoke:
            w.run(TASK, history=[])
        self.assertEqual([c.args[0] for c in invoke.call_args_list], ['search_papers', 'search_papers', 'collect_evidence'])
        self.assertIn('literature/review', calls)

    def test_forged_candidate_never_calls_collect(self):
        def model(name, payload, schema):
            if name == 'runtime/route': return w.Route(action='paper_research', task='attention')
            return w.Specialist(action='read', candidate_ids=['fake'])
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
                return w.Route(action=action, task='accuracy' if action == 'paper_research' else '')
            if name == 'literature/research':
                if not payload['recent_result']: return w.Specialist(action='search', query='accuracy')
                if not payload['evidence']: return w.Specialist(action='read', candidate_ids=[payload['candidates'][0]['id']])
                return w.Specialist(action='finish')
            if name == 'literature/review': return w.Review(sufficient=True, summary='ok')
            if name == 'literature/synthesize': return w.Draft(claims=[w.Claim(text='Accuracy 99%', citations=[payload['evidence'][0]['id']])])
            if name == 'literature/revise':
                revisions.append(payload['evidence'])
                return w.Draft(claims=[w.Claim(text='Accuracy 92%', citations=[payload['evidence'][0]['id']])])
            if name == 'literature/verify_claim': return w.Verdict(supported=True)
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
                    return w.Route(action='paper_research', task='initial')
                if 'review' in payload['available_actions']:
                    return w.Route(action='review')
                if 'paper_research' in payload['available_actions']:
                    return w.Route(action='paper_research', task='supplement')
                return w.Route(action='report')
            if name == 'literature/research':
                tools.append([t['name'] for t in payload['tools']])
                if not payload['recent_result']:
                    tasks.append(payload['task'])
                    return w.Specialist(action='search', query=payload['task'])
                if payload['recent_result'].startswith('检索'):
                    for c in payload['candidates']:
                        if c['title'] in identifiers:
                            self.assertEqual(identifiers[c['title']], c['id'])
                        identifiers[c['title']] = c['id']
                    return w.Specialist(action='read', candidate_ids=[c['id'] for c in payload['candidates']])
                return w.Specialist(action='finish')
            if name == 'literature/review':
                reviews.append(payload['evidence'])
                return w.Review(sufficient=False, gaps=['supplement'], summary='missing')
            if name == 'literature/synthesize': return w.Draft()
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
                if not payload['results']: return w.Route(action='paper_research', task='attention')
                if 'review' in payload['available_actions']: return w.Route(action='review')
                if 'paper_research' in payload['available_actions']: return w.Route(action='paper_research', task='latency')
                return w.Route(action='report')
            if name == 'literature/research':
                if not payload['recent_result']: return w.Specialist(action='search', query=payload['task'])
                if payload['recent_result'].startswith('检索'): return w.Specialist(action='read', candidate_ids=[payload['candidates'][0]['id']])
                return w.Specialist(action='finish')
            if name == 'literature/review':
                snapshots.append(payload['evidence'])
                return w.Review(sufficient=False, gaps=['latency'], summary='missing')
            if name == 'literature/synthesize': return w.Draft()
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
                return w.Route(action=action, task='attention' if action == 'paper_research' else '')
            if name == 'literature/research': return w.Specialist(action='search', query='attention')
            if name == 'literature/review': return w.Review(sufficient=False, gaps=['missing'], summary='missing')
            if name == 'literature/synthesize': return w.Draft()
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
                    return w.Route(action='paper_research', task=f'topic {len(tasks)}')
                return w.Route(action='review' if 'review' in payload['available_actions'] else 'report')
            if name == 'literature/research':
                if not payload['recent_result']:
                    tasks.append(payload['task'])
                    return w.Specialist(action='search', query=payload['task'])
                if payload['recent_result'].startswith('检索'):
                    return w.Specialist(action='read', candidate_ids=[next(c['id'] for c in payload['candidates'] if c['title'] == papers[len(tasks)-1]['title'])])
                return w.Specialist(action='finish')
            if name == 'literature/review': return w.Review(sufficient=False, gaps=['missing'], summary='missing')
            if name == 'literature/synthesize': return w.Draft()
            raise AssertionError(name)
        with patch.object(repository, 'project_evidence', return_value=papers), patch('src.tools.search.retrieve', side_effect=[{'results': [p]} for p in papers]), patch.object(w, 'structured', side_effect=model):
            content, _ = w.run(TASK, history=[])
        self.assertEqual(len(tasks), 3)
        self.assertIn(0, remaining)
        self.assertIn('证据不足', content)

    def test_forbidden_route_never_invokes_tools(self):
        for source, action in [('collection', 'direct'), ('collection', 'web_research'), ('web', 'paper_research'), ('all', 'web_research')]:
            with self.subTest(source=source, action=action):
                research = {**TASK, 'source': source, 'paper_ids': [3]}
                choice = w.Route(action=action, task='task', answer='answer')
                with patch.object(w, 'structured', return_value=choice), patch.object(w.registry, 'invoke') as invoke:
                    with self.assertRaisesRegex(ValueError, '不允许'):
                        w.run(research, history=[])
                invoke.assert_not_called()

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
        state = {'research': TASK, 'evidence': evidence, 'draft': w.Draft(claims=claims, limitations='成本下降 70%')}
        with patch.object(w, 'structured', return_value=w.Verdict(supported=False)) as model:
            result = w.verify(state)
        self.assertEqual(model.call_count, 0)
        self.assertNotIn('成本降低', result['content'])
        self.assertNotIn('70%', result['content'])
        self.assertEqual(result['evidence'], [])

    def test_accuracy_number_cannot_be_reused_as_cost_evidence(self):
        state = {'research': TASK, 'evidence':[{'id':'e1','text':'Accuracy 92%'}], 'draft':w.Draft(claims=[w.Claim(text='成本降低 92%',citations=['e1'])])}
        with patch.object(w, 'structured', return_value=w.Verdict(supported=True)) as model:
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
