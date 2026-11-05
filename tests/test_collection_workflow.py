import unittest
from unittest.mock import patch
from src.literature import workflow as w


class CollectionWorkflowTests(unittest.TestCase):
    def test_empty_never_calls_model_or_network(self):
        with patch.object(w, 'structured') as model, patch.object(w, 'retrieve') as retrieve:
            content, evidence = w.run({'source': 'collection', 'paper_ids': []})
        self.assertIn('证据不足', content)
        self.assertEqual(evidence, [])
        model.assert_not_called()
        retrieve.assert_not_called()

    def test_isolated_graph_filters_other_papers_and_history(self):
        task = {'id': 1, 'project_id': 1, 'query': 'summarize', 'source': 'collection', 'paper_ids': [3]}
        allowed = {'id': 3, 'title': 'selected', 'abstract': 'selected evidence', 'versions': []}
        decoy = {'id': 4, 'title': 'forbidden', 'abstract': 'forbidden text', 'versions': []}
        payloads = []
        def model(name, payload, schema):
            payloads.append(str(payload))
            if name.endswith('plan_review'): return w.Plan(direct=True, queries=['anything'])
            if name.endswith('assess_gap'): return w.Gap(query='more')
            if name.endswith('synthesize'): return w.Draft(claims=[w.Claim(text='selected evidence', citations=['a1'])])
            if name.endswith('verify_claim'): return w.Verdict(supported=True)
            raise AssertionError(name)
        with patch.object(w.repository, 'update_stage'), patch.object(w.repository, 'task_history', return_value=[{'content': 'OUTSIDE_SECRET forbidden history'}]) as history, patch.object(w.repository, 'project_evidence', return_value=[allowed, decoy]), patch.object(w.repository, 'local_search', return_value=[]) as local, patch.object(w, 'retrieve') as retrieve, patch('src.literature.retrieval.search_works') as external, patch.object(w, 'structured', side_effect=model):
            content, evidence = w.run(task)
        history.assert_not_called()
        retrieve.assert_not_called()
        external.assert_not_called()
        self.assertEqual(local.call_count, 2)
        self.assertTrue(all(call.args[2] == [3] for call in local.call_args_list))
        self.assertTrue(all('OUTSIDE_SECRET' not in payload for payload in payloads))
        self.assertTrue(all('forbidden' not in payload for payload in payloads))
        self.assertNotIn('forbidden', content)
        self.assertEqual([e['paper_id'] for e in evidence], [3])

    def test_verify_rejects_unselected_paper(self):
        state = {'task': {'id': 1, 'source': 'collection', 'paper_ids': [3]}, 'draft': w.Draft(claims=[w.Claim(text='result', citations=['x'])]), 'evidence': [{'id': 'x', 'paper_id': 4, 'text': 'result', 'title': 'other'}]}
        with patch.object(w.repository, 'update_stage'), patch.object(w, 'structured') as model:
            result = w.verify(state)
        model.assert_not_called()
        self.assertEqual(result['evidence'], [])
