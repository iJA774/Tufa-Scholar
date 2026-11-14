import unittest

from src.agents import literature, report


class ReportValidationTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {
            'paper': {
                'id': 'paper', 'paper_id': 7, 'title': 'Study',
                'source_level': 'full_text', 'text': 'Accuracy was 92%; cost was $5.',
                'version_id': 3, 'page': 4, 'file_path': 'C:/private/material.pdf',
            },
            'web': {
                'id': 'web', 'title': 'Official page', 'source_level': 'web_content',
                'text': 'The system was tested independently.',
                'provider': 'fixture', 'original_url': 'https://example.org/report',
                'retrieved_at': '2026-11-12T10:00:00+08:00', 'char_start': 25, 'char_end': 61,
            },
        }

    def test_measurements_normalize_units_and_keep_signs(self):
        self.assertEqual(report.measurements('92.0% and 1,000 ms'), report.measurements('百分之 92 和 1 second'))
        self.assertNotEqual(report.measurements('-4%'), report.measurements('4%'))
        self.assertEqual(report.measurements('4MBModel version 2024'), set())
        self.assertNotEqual(report.measurements('1,000 ms'), report.measurements('0ms'))

    def test_unsafe_text_rejects_active_markup_and_embedded_citations(self):
        for text in ('<script>run()</script>', 'See https://example.org', '```code```', '[p1]', '[label](https://example.org)'):
            with self.subTest(text=text):
                self.assertTrue(report.unsafe_text(text))
        self.assertFalse(report.unsafe_text('Accuracy was 92 percent.'))

    def test_valid_claim_keeps_only_cited_evidence(self):
        cited, reason = report.validate_claim(
            '准确率为 92%', ['paper'], self.evidence, allowed_paper_ids={7}
        )
        self.assertEqual(reason, '')
        self.assertEqual(cited, [self.evidence['paper']])

    def test_claim_rejects_unknown_duplicate_and_out_of_snapshot_citations(self):
        cases = [
            (['missing'], None, '引用不存在'),
            (['paper', 'paper'], None, '引用重复'),
            (['paper'], {8}, '引用超出收藏夹快照范围'),
        ]
        for citations, scope, reason in cases:
            with self.subTest(citations=citations, scope=scope):
                cited, actual = report.validate_claim('Study results', citations, self.evidence, scope)
                self.assertEqual((cited, actual), ([], reason))

    def test_numeric_and_cost_claims_need_matching_source_text(self):
        for text, citation, reason in [
            ('Accuracy was 95%', ['paper'], '引用或测量值依据不匹配'),
            ('Cost decreased by 20%', ['web'], '引用或测量值依据不匹配'),
            ('Cost decreased', ['web'], '缺少成本依据'),
        ]:
            with self.subTest(text=text):
                cited, actual = report.validate_claim(text, citation, self.evidence)
                self.assertEqual((cited, actual), ([], reason))

    def test_uncited_limitations_can_name_a_missing_measure_but_not_claim_a_result(self):
        self.assertEqual(report.validate_claim('资料有限', [], self.evidence, allow_uncited=True), ([], ''))
        self.assertEqual(report.validate_claim('现有材料没有成本证据', [], self.evidence, allow_uncited=True), ([], ''))
        self.assertEqual(
            report.validate_claim('现有材料没有成本证据，但成本显著降低', [], self.evidence, allow_uncited=True)[1],
            '无引用段落包含成本结论',
        )
        self.assertEqual(
            report.validate_claim('现有材料没有成本证据，但成本显著降低', ['web'], self.evidence, is_limitation=True)[1],
            '缺少成本依据',
        )
        self.assertEqual(report.validate_claim('Accuracy is 92%', [], self.evidence, allow_uncited=True)[1], '无引用段落包含数值')
        self.assertEqual(report.validate_claim('Cost is lower', [], self.evidence, allow_uncited=True)[1], '无引用段落包含成本结论')
        self.assertEqual(report.validate_claim('资料有限', [], self.evidence)[1], '段落缺少证据引用')

    def test_reference_preserves_source_anchors_without_local_paths(self):
        paper_ref = report.reference(self.evidence['paper'])
        web_ref = report.reference(self.evidence['web'])
        self.assertIn('原文版本 3，第 4 页', paper_ref)
        self.assertIn('Markdown 字符区间 25–61', web_ref)
        self.assertIn('https://example.org/report', web_ref)
        self.assertNotIn('C:/private', paper_ref)

    def test_reference_does_not_expose_local_paths_from_url_or_title(self):
        local = {
            'id': 'local', 'title': r'C:\private\material.pdf',
            'source_level': 'abstract', 'original_url': 'file:///C:/private/material.pdf',
        }
        rendered = report.reference(local)
        self.assertNotIn('C:/private', rendered)
        self.assertNotIn('C:\\private', rendered)
        self.assertNotIn('file://', rendered)

    def test_markdown_reference_escapes_urls_and_filters_only_sensitive_query_keys(self):
        rendered = report.reference({
            'id': 'web-1', 'title': '<script>alert(1)</script>',
            'source_level': 'web_content', 'provider': 'fixture',
            'original_url': 'https://example.org/a)<script>?title=Useful+Page&token=private',
        }, markdown=True)

        self.assertIn('https://example.org/a%29%3Cscript%3E?title=Useful+Page', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('token=private', rendered)


class ReporterLayoutTests(unittest.TestCase):
    def setUp(self):
        self.paper = {
            'id': 'paper', 'paper_id': 7, 'title': 'Study',
            'source_level': 'full_text', 'text': 'Accuracy was 92%; cost was $5.',
            'version_id': 3, 'page': 4, 'file_path': 'C:/private/material.pdf',
            'research_source': 'paper',
        }
        self.web = {
            'id': 'web', 'title': 'Official page', 'source_level': 'web_content',
            'text': 'The system was tested independently.',
            'provider': 'fixture', 'original_url': 'https://example.org/report?title=Useful+Title&token=private#part',
            'retrieved_at': '2026-11-12T10:00:00+08:00', 'char_start': 25, 'char_end': 61,
            'research_source': 'web',
        }

    def test_reporter_renders_enforced_layout_and_validated_link_references(self):
        result = literature.ReporterOutput(
            title='评估结果',
            key_points=[
                literature.Claim(text='准确率为 92%。', citations=['paper']),
                literature.Claim(text='网页记录了独立测试。', citations=['web']),
            ],
            overview=[literature.Claim(text='现有材料报告准确率为 92%。', citations=['paper'])],
            sections=[literature.Section(
                heading='准确率与独立测试',
                paragraphs=[
                    literature.Claim(text='论文记录准确率为 92%。', citations=['paper']),
                    literature.Claim(text='网页记载系统经过独立测试。', citations=['web']),
                    literature.Claim(text='该研究采用独立的评价流程。', citations=['paper']),
                    literature.Claim(text='页面说明了测试过程。', citations=['web']),
                ],
            )],
            limitations=[literature.Claim(text='现有材料不足以判断外部适用性。', citations=[])],
        )

        rendered = literature._render_draft(
            result, [self.paper, self.web], 'all', title='评估系统的结果',
        )

        content = rendered['content']
        self.assertEqual(content.count('\n# '), 0)
        self.assertEqual(sum(line.startswith('# ') for line in content.splitlines()), 1)
        self.assertIn('# 评估结果', content)
        headings = [line for line in content.splitlines() if line.startswith('## ')]
        self.assertEqual(headings[:4], ['## 关键点', '## 概述', '## 详细分析', '## 局限与缺口'])
        self.assertIn('### 准确率与独立测试', content)
        key_points_block = content.split('## 关键点\n\n', 1)[1].split('\n\n## 概述', 1)[0]
        self.assertEqual(sum(line.startswith('- ') for line in key_points_block.splitlines()), 4)
        self.assertIn('## 关键引文', content)
        self.assertIn('- [Official page](https://example.org/report?title=Useful+Title)', content)
        self.assertEqual(content.count('https://example.org/report?title=Useful+Title'), 1)
        self.assertIn('Markdown 字符区间 25–61', content)
        self.assertNotIn('token=private', content)
        self.assertNotIn('file_path', content)
        self.assertEqual([item['id'] for item in rendered['evidence']], ['paper', 'web'])

    def test_missing_layout_fields_are_filled_from_surviving_legacy_sections(self):
        legacy = literature.Draft(sections=[literature.Section(
            heading='准确率', paragraphs=[
                literature.Claim(text='准确率为 92%。', citations=['paper']),
                literature.Claim(text='材料报告了成本信息。', citations=['paper']),
            ],
        )])

        rendered = literature._render_draft(
            legacy, [self.paper], 'local', title='研究报告',
        )

        content = rendered['content']
        self.assertIn('## 关键点\n\n- 准确率为 92%。 [paper]', content)
        self.assertIn('## 概述', content)
        self.assertIn('## 详细分析\n\n### 准确率', content)
        self.assertEqual([item['id'] for item in rendered['evidence']], ['paper'])

    def test_invalid_new_fields_are_rejected_and_title_falls_back_to_topic(self):
        result = literature.ReporterOutput(
            title='[malicious](https://bad.example)',
            key_points=[literature.Claim(text='准确率为 95%。', citations=['paper'])],
            overview=[literature.Claim(text='访问 https://bad.example', citations=['paper'])],
            sections=[literature.Section(
                heading='结果',
                paragraphs=[literature.Claim(text='准确率为 92%。', citations=['paper'])],
            )],
        )

        rendered = literature._render_draft(
            result, [self.paper], 'local', title='系统准确率评估',
        )

        self.assertTrue(rendered['content'].startswith('# 系统准确率评估'))
        self.assertNotIn('95%', rendered['content'])
        self.assertNotIn('bad.example', rendered['content'])
        self.assertIn('准确率为 92%', rendered['content'])
        self.assertGreaterEqual(len(rendered['rejected']), 2)

    def test_evidence_free_legacy_report_keeps_explicit_gap_and_no_references(self):
        legacy = literature.Draft(limitations=[
            literature.Claim(text='当前没有可核查资料，无法形成已核验结论。', citations=[]),
        ])

        rendered = literature._render_draft(legacy, [], 'web', title='研究报告')

        self.assertIn('# 研究报告', rendered['content'])
        self.assertIn('## 关键点', rendered['content'])
        self.assertIn('## 概述', rendered['content'])
        self.assertIn('## 详细分析', rendered['content'])
        self.assertIn('## 局限与缺口', rendered['content'])
        self.assertNotIn('## 关键引文', rendered['content'])
        self.assertEqual(rendered['evidence'], [])

    def test_overview_only_supported_output_remains_visible_in_report(self):
        result = literature.ReporterOutput(
            title='独立测试结果',
            overview=[literature.Claim(text='网页记录系统经过独立测试。', citations=['web'])],
        )

        rendered = literature._render_draft(
            result, [self.web], 'web', title='网页测试',
        )

        self.assertIn('## 概述\n\n网页记录系统经过独立测试。 [web]', rendered['content'])
        self.assertIn('## 详细分析\n\n### 证据支持的发现', rendered['content'])
        self.assertEqual([item['id'] for item in rendered['evidence']], ['web'])


if __name__ == '__main__':
    unittest.main()
