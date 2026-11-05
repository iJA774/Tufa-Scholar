import unittest
from types import SimpleNamespace
from unittest.mock import patch
from src.literature import workflow as w


TASK = {'id': 1, 'project_id': 2, 'query': 'attention', 'source': 'local'}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stage = patch.object(w.repository, 'update_stage').start()
        self.addCleanup(patch.stopall)

    def test_direct_graph(self):
        def model(name, payload, schema):
            return w.Plan(direct=True) if name.endswith('plan_review') else w.Answer(answer='你好')
        with patch.object(w.repository, 'task_history', return_value=[]), patch.object(w, 'structured', side_effect=model), patch.object(w, 'retrieve') as retrieve:
            content, evidence = w.run(TASK)
        self.assertIn('模型回答', content)
        self.assertEqual(evidence, [])
        retrieve.assert_not_called()

    def test_research_graph_stops_without_new_evidence(self):
        calls = []
        def model(name, payload, schema):
            calls.append(name)
            if name.endswith('plan_review'): return w.Plan(queries=['attention'])
            if name.endswith('assess_gap'): return w.Gap(query='more')
            if name.endswith('synthesize'): return w.Draft(claims=[])
            raise AssertionError(name)
        paper = {'title': 'paper', 'abstract': 'attention study'}
        with patch.object(w.repository, 'task_history', return_value=[]), patch.object(w.repository, 'project_evidence', return_value=[]), patch.object(w, 'structured', side_effect=model), patch.object(w, 'retrieve', return_value={'results': [paper]}) as retrieval:
            content, _ = w.run(TASK)
        self.assertEqual(retrieval.call_count, 2)
        self.assertEqual(sum(x.endswith('assess_gap') for x in calls), 1)
        self.assertIn('证据不足', content)

    def test_two_round_limit(self):
        with patch.object(w, 'structured', return_value=w.Gap(query='more')) as model:
            self.assertEqual(w.assess({'task': TASK, 'round': 2, 'evidence': [1, 2], 'previous_count': 1})['queries'], [])
            model.assert_not_called()

    def test_rejects_missing_cost_bad_ids_and_invented_numbers(self):
        evidence = [{'id': 'e1', 'text': 'Accuracy was 95%.', 'title': 'paper', 'version_id': None}]
        claims = [w.Claim(text='成本降低', citations=['e1']), w.Claim(text='准确率 99%', citations=['e1']), w.Claim(text='结果', citations=['fake'])]
        state = {'task': TASK, 'evidence': evidence, 'draft': w.Draft(claims=claims, limitations='成本下降 70%')}
        with patch.object(w, 'structured', return_value=w.Verdict(supported=False)) as model:
            result = w.verify(state)
        self.assertEqual(model.call_count, 0)
        self.assertNotIn('成本降低', result['content'])
        self.assertNotIn('70%', result['content'])
        self.assertEqual(result['evidence'], [])

    def test_accuracy_number_cannot_be_reused_as_cost_evidence(self):
        state = {'task': TASK, 'evidence':[{'id':'e1','text':'Accuracy 92%'}], 'draft':w.Draft(claims=[w.Claim(text='成本降低 92%',citations=['e1'])])}
        with patch.object(w, 'structured', return_value=w.Verdict(supported=True)) as model:
            result = w.verify(state)
        model.assert_not_called()
        self.assertEqual(result['evidence'], [])
        self.assertIn('证据不足', result['content'])

    def test_pdf_page_and_version_anchor(self):
        pages = [SimpleNamespace(extract_text=lambda: 'other'), SimpleNamespace(extract_text=lambda: 'attention 95%')]
        paper = {'id': 3, 'title': 'paper'}
        local = [{'id': 3, 'versions': [{'id': 7, 'path': 'fixture.pdf', 'text_status': 'text_available'}]}]
        with patch.object(w.repository, 'project_evidence', return_value=local), patch.object(w, 'PdfReader', return_value=SimpleNamespace(pages=pages)):
            result = w.evidence({'task': TASK, 'papers': [paper]})['evidence']
        self.assertEqual((result[0]['version_id'], result[0]['page'], result[0]['id']), (7, 2, 'p3v7p2'))
