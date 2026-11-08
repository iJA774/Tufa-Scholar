import unittest
from pydantic import ValidationError
from src.agents import report as r


class ReportTests(unittest.TestCase):
    def test_structured_report_and_mixed_sources(self):
        evidence = [
            {'id': 'p1', 'paper_id': 1, 'title': 'Paper', 'text': 'accuracy 92%', 'source_level': 'full_text', 'version_id': 3, 'page': 2},
            {'id': 'w1', 'title': 'Web', 'text': 'independent observation', 'source_level': 'web_snippet', 'provider': 'searx', 'original_url': 'https://example.org', 'retrieved_at': 'today'},
        ]
        draft = r.Draft(sections=[r.Section(heading='概述', paragraphs=[r.Claim(text='accuracy 92%。这一结果值得关注。', citations=['p1'])]), r.Section(heading='综合判断', paragraphs=[r.Claim(text='independent observation。', citations=['w1'])])])
        def model(s, name, payload, schema):
            return draft if name.endswith('synthesize') else r.Verdict(supported=True)
        result = r.build_report_graph(model).invoke({'research': {'source': 'auto', 'query': 'question'}, 'history': [], 'evidence': evidence})
        self.assertIn('## 概述', result['content']); self.assertIn('这一结果值得关注', result['content'])
        self.assertIn('## 综合判断', result['content']); self.assertIn('原文版本 3，第 2 页', result['content'])
        self.assertIn('搜索摘要', result['content']); self.assertIn('https://example.org', result['content'])
        self.assertEqual(result['evidence'], evidence)

    def test_revision_preserves_evidence_and_removes_rejected_text(self):
        evidence = [{'id': 'a1', 'text': 'accuracy 92%', 'title': 'paper', 'source_level': 'abstract'}]
        seen = []
        def model(s, name, payload, schema):
            if name.endswith('synthesize'):
                return r.Draft(sections=[r.Section(heading='概述', paragraphs=[r.Claim(text='accuracy 99%', citations=['a1'])])])
            if name.endswith('revise'):
                seen.append(payload)
                return r.Draft(sections=[r.Section(heading='概述', paragraphs=[r.Claim(text='accuracy 92%', citations=['a1'])])])
            return r.Verdict(supported=True)
        result = r.build_report_graph(model).invoke({'research': {'source': 'local', 'query': 'q'}, 'history': [], 'evidence': evidence})
        self.assertEqual(seen[0]['evidence'], evidence)
        self.assertEqual(seen[0]['feedback'][0]['paragraph_index'], 0)
        self.assertIn('92%', result['content']); self.assertNotIn('99%', result['content'])

    def test_rejects_outside_collection_cost_numbers_and_fake_references(self):
        ev = [{'id': 'a1', 'paper_id': 4, 'text': 'accuracy 92%', 'title': 'paper'}]
        for text, citations in [('accuracy 92%', ['a1']), ('成本 92%', ['a1']), ('accuracy 99%', ['a1']), ('result [fake1]', ['a1'])]:
            state = {'research': {'source': 'collection', 'paper_ids': [3]}, 'evidence': ev, 'draft': r.Draft(sections=[r.Section(heading='概述', paragraphs=[r.Claim(text=text, citations=citations)])])}
            result = r.verify(state, lambda *args: self.fail('must reject before model'))
            self.assertTrue(result['rejection_feedback'])
        state['research'] = {'source': 'local'}
        for text in ('成本 92%', 'accuracy 99%', 'result [fake1]'):
            state['draft'].sections[0].paragraphs[0].text = text
            self.assertTrue(r.verify(state, lambda *args: self.fail('must reject before model'))['rejection_feedback'])

    def test_scope_and_review_directions_are_marked_as_unverified(self):
        from src.agents.literature import Review, NextStep
        draft = r.Draft(sections=[r.Section(heading='概述', paragraphs=[r.Claim(text='result', citations=['p', 'w'])])])
        state = {'research': {'source': 'all'}, 'draft': draft, 'accepted': [(0, 0)], 'evidence': [
            {'id': 'p', 'title': 'paper', 'source_level': 'abstract', 'text': 'result'},
            {'id': 'w', 'title': 'web', 'source_level': 'web_content', 'provider': 'searx', 'original_url': 'https://example.org', 'text': 'result'}],
            'review_result': Review(sufficient=False, summary='missing', next_steps=[NextStep(direction='比较 [方法](https://example.org)\n# 条件', goal='check', route='paper_research')])}
        content = r.render(state)['content']
        self.assertIn('首选来源：项目与外部论文', content)
        self.assertIn('实际引用的资料：论文摘要、网页文本片段', content)
        self.assertIn('现有证据尚不足以全面回答', content)
        self.assertIn('待查方向：比较 方法 条件', content)
        self.assertNotIn('[方法](https://example.org)', content)

    def test_empty_report_and_schema(self):
        with self.assertRaises(ValidationError): r.Draft.model_validate({'claims': []})
        result = r.build_report_graph(lambda s,n,p,t: r.Draft(sections=[])).invoke({'research': {'source': 'web', 'query': 'q'}, 'history': [], 'evidence': []})
        self.assertIn('研究报告', result['content']); self.assertIn('证据不足', result['content']); self.assertEqual(result['evidence'], [])
