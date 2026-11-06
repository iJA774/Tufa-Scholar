import unittest
from types import SimpleNamespace
from unittest.mock import patch
from src.agents import literature as w
from src.persistence import repository


TASK = {'project_id': 2, 'query': 'attention', 'source': 'local'}


class WorkflowTests(unittest.TestCase):
    def test_direct_graph(self):
        def model(name, payload, schema):
            return w.Plan(direct=True) if name.endswith('plan_review') else w.Answer(answer='你好')
        with patch.object(w, 'structured', side_effect=model), patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as retrieve:
            content, evidence = w.run(TASK, history=[])
        self.assertIn('模型回答', content)
        self.assertEqual(evidence, [])
        retrieve.assert_not_called()

    def test_agent_uses_explicit_history_without_task_database(self):
        history = [{'role': 'user', 'content': 'prior question'}]
        payloads = []
        stages = []
        def model(name, payload, schema):
            payloads.append(payload)
            return w.Plan(direct=True) if name.endswith('plan_review') else w.Answer(answer='hello')
        with patch.object(repository, 'task_history', side_effect=AssertionError('unexpected history read')), patch.object(repository, 'update_stage', side_effect=AssertionError('unexpected stage write')), patch.object(w, 'structured', side_effect=model):
            content, _ = w.run(TASK, history=history, on_stage=stages.append)
            w.run(TASK, history=history)
        self.assertIn('hello', content)
        self.assertTrue(all(payload['history'] == history for payload in payloads))
        self.assertEqual(stages, ['planning', 'answering'])

    def test_research_graph_stops_without_new_evidence(self):
        calls = []
        def model(name, payload, schema):
            calls.append(name)
            if name.endswith('plan_review'): return w.Plan(queries=['attention'])
            if name.endswith('assess_gap'): return w.Gap(query='more')
            if name.endswith('synthesize'): return w.Draft(claims=[])
            raise AssertionError(name)
        paper = {'id': 3, 'title': 'paper', 'abstract': 'attention study', 'versions': []}
        with patch.object(repository, 'project_evidence', return_value=[paper]), patch.object(w, 'structured', side_effect=model), patch('src.tools.search.retrieve', return_value={'results': [paper]}) as retrieval, patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as invoke:
            content, _ = w.run(TASK, history=[])
        self.assertEqual(retrieval.call_count, 2)
        self.assertEqual([call.args[0] for call in invoke.call_args_list], ['search_papers', 'collect_evidence', 'search_papers', 'collect_evidence'])
        self.assertTrue(all(call.kwargs['context'].project_id == 2 and call.kwargs['context'].source == 'local' for call in invoke.call_args_list))
        self.assertEqual(sum(x.endswith('assess_gap') for x in calls), 1)
        self.assertIn('证据不足', content)

    def test_new_ranked_candidate_replaces_full_selection_and_continues(self):
        research = {**TASK, 'source': 'openalex'}
        old = [{'title': f'old {i}', 'doi': f'10.1000/{i}', 'abstract': f'old evidence {i}'} for i in range(6)]
        new = {'title': 'new discovery', 'doi': '10.1000/new', 'abstract': 'new discovery evidence'}
        searches = [[*old], [new, {**old[0], 'doi': 'https://doi.org/10.1000/0'}], [new]]
        assessments, syntheses, states, stages = [], [], [], []
        def model(name, payload, schema):
            if name.endswith('plan_review'): return w.Plan(queries=['first'])
            if name.endswith('assess_gap'):
                assessments.append(payload['evidence'])
                return w.Gap(query=f'next {len(assessments)}')
            if name.endswith('synthesize'):
                syntheses.append(payload['evidence'])
                target = next(e for e in payload['evidence'] if e['title'] == 'new discovery')
                return w.Draft(claims=[w.Claim(text='new discovery evidence', citations=[target['id']])])
            if name.endswith('verify_claim'): return w.Verdict(supported=True)
            raise AssertionError(name)
        original_search = w.search
        def capture(state):
            result = original_search(state)
            states.append(result)
            return result
        with patch.object(repository, 'project_evidence', return_value=[]), patch.object(w, 'structured', side_effect=model), patch('src.tools.search.retrieve', side_effect=[{'results': result} for result in searches]) as retrieve, patch.object(w, 'search', side_effect=capture):
            content, evidence = w.run(research, history=[], on_stage=stages.append)
        self.assertEqual(retrieve.call_count, 3)
        self.assertEqual(len(states[1]['candidates']), 7)
        self.assertEqual(len(states[1]['papers']), 6)
        self.assertIn('new discovery', [p['title'] for p in states[1]['papers']])
        self.assertEqual(sum(p['title'] == 'old 0' for p in states[1]['candidates']), 1)
        self.assertEqual([len(items) for items in assessments], [6, 6])
        self.assertIn('new discovery evidence', content)
        self.assertEqual([e['title'] for e in evidence], ['new discovery'])
        self.assertEqual(stages, ['planning', 'searching', 'evidence', 'assessing', 'searching', 'evidence', 'assessing', 'searching', 'evidence', 'assessing', 'synthesizing', 'verifying'])

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
            repeated = w.evidence({'research': TASK, 'papers': [], 'seen_evidence': initial['seen_evidence']})
        self.assertTrue(initial['new_evidence'])
        self.assertFalse(repeated['new_evidence'])

    def test_two_round_limit(self):
        with patch.object(w, 'structured', return_value=w.Gap(query='more')) as model:
            self.assertEqual(w.assess({'research': TASK, 'round': 2, 'evidence': [1, 2], 'new_evidence': True})['queries'], [])
            model.assert_not_called()

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
        local = [{'id': 3, 'title': 'paper', 'versions': [{'id': 7, 'path': 'fixture.pdf', 'text_status': 'text_available'}]}]
        with patch.object(repository, 'project_evidence', return_value=local), patch('src.literature.evidence.PdfReader', return_value=SimpleNamespace(pages=pages)):
            result = w.evidence({'research': TASK, 'papers': [paper]})['evidence']
        self.assertEqual((result[0]['version_id'], result[0]['page'], result[0]['id']), (7, 2, 'p3v7p2'))
