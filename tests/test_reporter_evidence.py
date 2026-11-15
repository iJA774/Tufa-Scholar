import unittest
from unittest.mock import patch

from src.agents import literature as workflow


def evidence(identifier, **fields):
    return {'id': identifier, 'title': identifier, 'source_level': 'abstract',
            'text': 'The finding was confirmed.', **fields}


class ReporterEvidenceTests(unittest.TestCase):
    def test_later_evidence_reaches_model_and_final_report(self):
        old = [evidence(f'old{i}') for i in range(12)]
        new = evidence('later', title='Supplement', source_level='web_content',
                       text='The outcome was 92%.', original_url='https://example.org/a')
        old_ids = [item['id'] for item in old]
        finding = {'text': 'The finding was confirmed.', 'citations': old_ids}
        state = {'research': {'project_id': 1, 'query': 'study', 'source': 'auto'},
                 'history': [], 'background': {}, 'plan': {}, 'errors': [], 'warnings': [],
                 'rounds': [{'source': 'paper', 'evidence_ids': old_ids, 'findings': [finding], 'review': {}},
                            {'source': 'web', 'evidence_ids': old_ids + ['later'], 'findings': [], 'review': {}}],
                 'evidence_pool': old + [new], 'candidate_pools': {'paper': [], 'web': []}}
        captured = {}
        def model(state, prompt, payload, schema, **kwargs):
            captured.update(payload)
            return workflow.ReporterOutput(sections=[workflow.Section(heading='结果', paragraphs=[
                workflow.Claim(text='The outcome was 92%.', citations=['later'])])])
        with patch.object(workflow, '_model', side_effect=model):
            result = workflow._reporter(state, None)
        self.assertIn('later', [item['id'] for item in captured['evidence']])
        self.assertEqual(captured['evidence_omitted_count'], 0)
        self.assertEqual([item['id'] for item in result['final_evidence']], ['later'])

    def test_large_pool_balances_rounds_and_cited_and_uncited(self):
        old = [evidence(f'a{i}', paper_id=1) for i in range(100)]
        later = [evidence(f'b{i}', paper_id=2) for i in range(40)]
        cited = [{'text': 'x', 'citations': [item['id'] for item in old]}]
        state = {'rounds': [{'evidence_ids': [x['id'] for x in old], 'findings': cited},
                            {'evidence_ids': [x['id'] for x in old + later], 'findings': []}]}
        selected = workflow._reporter_evidence(state, old + later)
        self.assertEqual(len(selected), 48)
        self.assertTrue(any(x['id'].startswith('a') for x in selected))
        self.assertTrue(any(x['id'].startswith('b') for x in selected))

    def test_overflow_is_disclosed_and_excludes_unseen_citations(self):
        pool = [evidence(f'e{i}', text='The outcome was 92%.') for i in range(60)]
        state = {'research': {'project_id': 1, 'query': 'study', 'source': 'auto'},
                 'history': [], 'background': {}, 'plan': {}, 'errors': [], 'warnings': [],
                 'rounds': [{'evidence_ids': [x['id'] for x in pool], 'findings': [], 'review': {}}],
                 'evidence_pool': pool, 'candidate_pools': {'paper': [], 'web': []}}
        captured = {}
        def model(state, prompt, payload, schema, **kwargs):
            captured.update(payload)
            visible = {x['id'] for x in payload['evidence']}
            hidden = next(x['id'] for x in pool if x['id'] not in visible)
            return workflow.ReporterOutput(sections=[workflow.Section(heading='结果', paragraphs=[
                workflow.Claim(text='The outcome was 92%.', citations=[payload['evidence'][0]['id']]),
                workflow.Claim(text='The outcome was 92%.', citations=[hidden])])])
        with patch.object(workflow, '_model', side_effect=model):
            result = workflow._reporter(state, None)
        self.assertEqual(captured['evidence_omitted_count'], 12)
        self.assertIn('本报告基于其中部分证据', result['content'])
        self.assertEqual(len(result['final_evidence']), 1)
        self.assertTrue(result['final_rejections'])

    def test_primary_preference_can_be_disabled(self):
        pool = [evidence(f'e{i}', title='same', source_level='abstract') for i in range(49)]
        pool[-1] = evidence('direct', title='same', source_level='full_text', page=1)
        state = {'rounds': [{'evidence_ids': [x['id'] for x in pool]}],
                 'research': {'prefer_primary_evidence': False}}
        self.assertNotIn('direct', [x['id'] for x in workflow._reporter_evidence(state, pool)])
        state['research']['prefer_primary_evidence'] = True
        self.assertIn('direct', [x['id'] for x in workflow._reporter_evidence(state, pool)])

    def test_collection_scope_and_visible_text_apply_to_validation(self):
        allowed = evidence('allowed', paper_id=7, text='a' * 1200 + ' Outcome 92%.')
        excluded = evidence('excluded', paper_id=8)
        state = {'research': {'project_id': 1, 'query': 'study', 'source': 'collection', 'paper_ids': [7]},
                 'history': [], 'background': {}, 'plan': {}, 'errors': [], 'warnings': [],
                 'rounds': [], 'evidence_pool': [allowed, excluded], 'candidate_pools': {'paper': [], 'web': []}}
        captured = {}
        def model(state, prompt, payload, schema, **kwargs):
            captured.update(payload)
            return workflow.ReporterOutput(sections=[workflow.Section(heading='结果', paragraphs=[
                workflow.Claim(text='Outcome 92%.', citations=['allowed'])])])
        with patch.object(workflow, '_model', side_effect=model):
            result = workflow._reporter(state, None)
        self.assertEqual([x['id'] for x in captured['evidence']], ['allowed'])
        self.assertEqual(len(captured['evidence'][0]['text']), 1200)
        self.assertEqual(result['final_evidence'], [])


if __name__ == '__main__':
    unittest.main()
