import unittest
from unittest.mock import patch
from pydantic import ValidationError
from src.agents import report as r


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.diagnostic = patch('src.agents.report.record_failure', return_value='diagnostic-id')
        self.diagnostic_mock = self.diagnostic.start()
        self.addCleanup(self.diagnostic.stop)

    def test_structured_report_and_mixed_sources(self):
        evidence = [
            {'id': 'p1', 'paper_id': 1, 'title': 'Paper', 'text': 'accuracy 92%', 'source_level': 'full_text', 'version_id': 3, 'page': 2},
            {'id': 'w1', 'title': 'Web', 'text': 'independent observation', 'source_level': 'web_snippet', 'provider': 'searx', 'original_url': 'https://example.org', 'retrieved_at': 'today'},
        ]
        draft = r.Draft(sections=[r.Section(heading='概述', paragraphs=[r.Claim(text='accuracy 92%。这一结果值得关注。', citations=['p1'])]), r.Section(heading='综合判断', paragraphs=[r.Claim(text='independent observation。', citations=['w1'])])])
        def model(s, name, payload, schema):
            return draft if name.endswith('synthesize') else r.Verdict(status='supported')
        result = r.build_report_graph(model).invoke({'research': {'source': 'auto', 'query': 'question'}, 'history': [], 'evidence': evidence})
        self.assertIn('## 概述', result['content']); self.assertIn('这一结果值得关注', result['content'])
        self.assertIn('## 综合判断', result['content']); self.assertIn('原文版本 3，第 2 页', result['content'])
        self.assertIn('搜索摘要', result['content']); self.assertIn('https://example.org', result['content'])
        self.assertEqual(result['evidence'], evidence)

    def test_revision_preserves_evidence_and_excludes_unsupported(self):
        evidence = [{'id': 'a1', 'text': 'accuracy 92%', 'title': 'paper', 'source_level': 'abstract'}]
        seen = []
        def model(s, name, payload, schema):
            if name.endswith('synthesize'):
                return r.Draft(sections=[r.Section(heading='概述', paragraphs=[r.Claim(text='accuracy is high', citations=['a1']), r.Claim(text='unsupported statement', citations=['a1'])])])
            if name.endswith('revise'):
                seen.append(payload)
                return r.PatchSet(patches=[r.Patch(section_index=0, paragraph_index=0, text='accuracy 92%', citations=['a1']), r.Patch(section_index=0, paragraph_index=1, text='resurrected', citations=['a1'])])
            if payload['claim'] == 'accuracy is high': return r.Verdict(status='qualified', reason='限定', suggestion='使用准确数值')
            if payload['claim'] == 'unsupported statement': return r.Verdict(status='unsupported', reason='缺乏支持')
            return r.Verdict(status='supported')
        result = r.build_report_graph(model).invoke({'research': {'source': 'local', 'query': 'q'}, 'history': [], 'evidence': evidence})
        self.assertEqual(seen[0]['evidence'], evidence)
        self.assertEqual(len(seen[0]['feedback']), 1)
        self.assertIn('92%', result['content']); self.assertNotIn('resurrected', result['content']); self.assertNotIn('unsupported statement', result['content'])
        self.assertIn('不受支持', result['content'])
        diagnostic = self.diagnostic_mock.call_args.kwargs
        self.assertEqual(diagnostic['kind'], 'report_verification')
        self.assertEqual(diagnostic['payload']['rejected'], [{'section_index': 0, 'paragraph_index': 1, 'reason': '缺乏支持'}])
        self.assertNotIn('缺乏支持', result['content'])
        self.assertNotIn('unsupported statement', str(diagnostic['payload']))

    def test_four_verdicts_limitations_and_measurements(self):
        evidence = [{'id': 'a', 'text': 'result 92 percent with 10 ms', 'title': 'paper'}]
        sections = [r.Section(heading='概述', paragraphs=[r.Claim(text='Model 2024 result 百分之92', citations=['a']), r.Claim(text='result 11 ms', citations=['a']), r.Claim(text='extra detail', citations=['a'])])]
        draft = r.Draft(sections=sections, limitations=[r.Claim(text='limited evidence', citations=['a'])])
        def model(s, name, payload, schema):
            if name.endswith('synthesize'): return draft
            if payload['claim'] == 'extra detail': return r.Verdict(status='missing_evidence', reason='细节缺失', research_query='extra', research_route='paper_research')
            return r.Verdict(status='supported')
        result = r.build_report_graph(model).invoke({'research': {'source':'local', 'query':'q'}, 'history': [], 'evidence':evidence})
        self.assertIn('Model 2024', result['content']); self.assertNotIn('11 ms', result['content'])
        self.assertIn('limited evidence', result['content']); self.assertNotIn('extra detail', result['content'])
        self.assertEqual(result['research_gaps'][0]['query'], 'extra')

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

    def test_measurements_normalize_and_preserve_boundaries(self):
        self.assertEqual(r._measurements('92.0% and 1,000 ms'), r._measurements('百分之92 和 1 second'))
        self.assertNotEqual(r._measurements('-4%'), r._measurements('4%'))
        self.assertEqual(r._measurements('4MBModel version 2024'), set())
        self.assertNotEqual(r._measurements('1,000 ms'), r._measurements('0ms'))

    def test_limitations_boundaries_anchor_warning_and_gap(self):
        ev = [{'id': 'w', 'title': 'web', 'source_level': 'web_content', 'provider': 'searx', 'original_url': 'https://example.org', 'char_start': 0, 'char_end': 80, 'text': 'accuracy 92%'}]
        draft = r.Draft(sections=[], limitations=[r.Claim(text='所引摘要未提供成本', citations=['w'])])
        def model(s, name, payload, schema):
            if name.endswith('synthesize'): return draft
            return r.Verdict(status='supported')
        result = r.build_report_graph(model).invoke({'research': {'source':'web','query':'q'}, 'history':[], 'evidence':ev, 'warnings':['web_page_read_failed']})
        self.assertIn('未提供成本', result['content']); self.assertIn('字符区间 0–80', result['content'])
        self.assertIn('网页正文读取部分失败', result['content']); self.assertNotIn('论文外部检索部分失败', result['content'])
        rendered = r.render({**result, 'research': {'source':'web'}, 'research_gaps':[{'query':'查看 [成本](https://example.org)\n# 细节'}]})['content']
        self.assertIn('待查方向：查看 成本 细节', rendered)

    def test_empty_draft_retries_once_only_with_evidence(self):
        calls = []
        def model(s, name, payload, schema):
            calls.append(name)
            if name.endswith('synthesize'): return r.Draft(sections=[])
            raise AssertionError(name)
        graph = r.build_report_graph(model)
        graph.invoke({'research': {'source':'local','query':'q'}, 'history':[], 'evidence':[{'id':'a','text':'x'}]})
        self.assertEqual(calls, ['literature/synthesize'] * 2)
        calls.clear()
        graph.invoke({'research': {'source':'local','query':'q'}, 'history':[], 'evidence':[]})
        self.assertEqual(calls, ['literature/synthesize'])

    def test_writer_input_echo_is_discarded_but_citations_still_checked(self):
        raw = {'sections': [{'heading': '概述', 'paragraphs': [
            {'text': 'supported result', 'citations': ['a1']},
            {'text': 'invented result', 'citations': ['fake']}]}],
            'limitations': [], 'verification_gaps': [{'query': 'input only'}]}
        original = __import__('copy').deepcopy(raw)
        parsed = r.Draft.model_validate(raw)
        self.assertEqual(raw, original)
        self.assertNotIn('verification_gaps', parsed.model_dump())
        for extra in ({'claims': []}, {'unknown': 'x'}):
            with self.assertRaises(ValidationError):
                r.Draft.model_validate({**raw, **extra})
        calls = []
        def model(state, name, payload, schema):
            if name.endswith('synthesize'): return schema.model_validate(raw)
            calls.append(payload['claim'])
            return r.Verdict(status='supported')
        result = r.build_report_graph(model).invoke({'research': {'source': 'local', 'query': 'q'}, 'history': [], 'evidence': [{'id': 'a1', 'text': 'supported result', 'title': 'paper'}]})
        self.assertEqual(calls, ['supported result'])
        self.assertIn('supported result', result['content'])
        self.assertNotIn('invented result', result['content'])
        self.assertNotIn('input only', result['content'])
        self.assertEqual([e['id'] for e in result['evidence']], ['a1'])

    def test_empty_report_and_schema(self):
        with self.assertRaises(ValidationError): r.Draft.model_validate({'claims': []})
        result = r.build_report_graph(lambda s,n,p,t: r.Draft(sections=[])).invoke({'research': {'source': 'web', 'query': 'q'}, 'history': [], 'evidence': []})
        self.assertIn('研究报告', result['content']); self.assertIn('证据不足', result['content']); self.assertEqual(result['evidence'], [])
