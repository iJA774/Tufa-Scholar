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
            return '# 结果\n\nThe outcome was 92%. [[later]]'
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
            return f"# 结果\n\nThe outcome was 92%. [[{payload['evidence'][0]['id']}]]\n\nThe outcome was 92%. [[{hidden}]]"
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

    def test_final_report_checks_citation_ids_and_retains_uncited_analysis(self):
        allowed = evidence('allowed', paper_id=7, text='a' * 1200 + ' Outcome 92%.')
        excluded = evidence('excluded', paper_id=8)
        state = {'research': {'project_id': 1, 'query': 'study', 'source': 'collection', 'paper_ids': [7]},
                 'history': [], 'background': {}, 'plan': {}, 'errors': [], 'warnings': [],
                 'rounds': [], 'evidence_pool': [allowed, excluded], 'candidate_pools': {'paper': [], 'web': []}}
        captured = {}
        def model(state, prompt, payload, schema, **kwargs):
            captured.update(payload)
            return ('# 结果\n\nOutcome 92%. [[allowed]]\n\n'
                    '我的判断是，成本可能限制应用。\n\n'
                    'This is outside the collection. [[excluded]]')
        with patch.object(workflow, '_model', side_effect=model):
            result = workflow._reporter(state, None)
        self.assertEqual([x['id'] for x in captured['evidence']], ['allowed'])
        self.assertEqual(len(captured['evidence'][0]['text']), 1200)
        self.assertEqual([x['id'] for x in result['final_evidence']], ['allowed'])
        self.assertIn('Outcome 92%', result['content'])
        self.assertIn('我的判断是，成本可能限制应用。', result['content'])
        self.assertNotIn('outside the collection', result['content'])
        self.assertFalse(result['report_fallback'])

    def test_final_report_with_no_citations_does_not_fall_back(self):
        state = {'research': {'query': 'study', 'source': 'auto'},
                 'evidence_pool': [evidence('e1')]}
        markdown = '# 我的判断\n\n目前最值得比较的是各方法的适用条件。'
        with patch.object(workflow, '_model', return_value=markdown):
            result = workflow._reporter(state, None)
        self.assertEqual(result['content'], markdown)
        self.assertEqual(result['final_evidence'], [])
        self.assertFalse(result['report_fallback'])


if __name__ == '__main__':
    unittest.main()
