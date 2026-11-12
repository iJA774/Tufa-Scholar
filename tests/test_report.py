import unittest

from src.agents import report


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


if __name__ == '__main__':
    unittest.main()
