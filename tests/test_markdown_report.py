import unittest

from src.agents.report import validate_markdown


class MarkdownReportTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [
            {'id': 'a1', 'paper_id': 1, 'text': '结果为 92%。'},
            {'id': 'b2', 'paper_id': 2, 'text': '结果为 80%。'},
        ]

    def test_preserves_layout_and_checks_each_table_row(self):
        report = '# 比较\n\n结果为 92%。[[a1]]\n\n- 结果为 80%。[[b2]]\n\n| 方法 | 结果 |\n| --- | --- |\n| A | 92% [[a1]] |\n| B | 99% [[b2]] |'
        result = validate_markdown(report, self.evidence)
        self.assertIn('# 比较', result['content'])
        self.assertIn('- 结果为 80%。[[b2]]', result['content'])
        self.assertIn('| A | 92% [[a1]] |', result['content'])
        self.assertNotIn('99%', result['content'])
        self.assertEqual({item['id'] for item in result['evidence']}, {'a1', 'b2'})
        self.assertTrue(result['rejected'])

    def test_scope_unsafe_and_missing_citations_are_rejected(self):
        report = '结果为 92%。[[a1]]\n\n结果为 80%。[[b2]]\n\n没有引用的断言。\n\n[链接](https://example.org) [[a1]]\n\n错误。[[missing]]'
        result = validate_markdown(report, self.evidence, allowed_paper_ids={1})
        self.assertIn('92%', result['content'])
        self.assertNotIn('80%', result['content'])
        self.assertNotIn('链接', result['content'])
        self.assertNotIn('断言', result['content'])
        self.assertNotIn('missing', result['content'])
        self.assertEqual([item['id'] for item in result['evidence']], ['a1'])

    def test_process_limitations_are_kept_without_citations(self):
        result = validate_markdown('> 局限：现有证据不足以比较方法。\n\n> 普通断言。', self.evidence)
        self.assertIn('现有证据不足', result['content'])
        self.assertNotIn('普通断言', result['content'])
        self.assertFalse(result['evidence'])

    def test_unsafe_table_header_does_not_consume_evidence(self):
        result = validate_markdown('| <script> | 值 |\n| --- | --- |\n| A | 92% [[a1]] |', self.evidence)
        self.assertFalse(result['evidence'])
        self.assertFalse(result['findings'])

    def test_fenced_code_is_removed_and_multiline_list_is_checked_as_one_item(self):
        text = '- 方法结果\n  准确率为 92%。 [[a1]]\n\n```text\n\n伪装为正文 92%。 [[a1]]\n\n```'
        result = validate_markdown(text, self.evidence)
        self.assertIn('准确率为 92%', result['content'])
        self.assertNotIn('伪装为正文', result['content'])
        self.assertEqual(len(result['findings']), 1)

    def test_markdown_wrapper_is_unwrapped_and_reference_definition_rejected(self):
        result = validate_markdown('```markdown\n# 标题\n\n结果 92% [[a1]]\n\n[x]: //example.org [[a1]]\n```', self.evidence)
        self.assertIn('# 标题', result['content'])
        self.assertIn('结果 92% [[a1]]', result['content'])
        self.assertNotIn('[x]:', result['content'])

    def test_malformed_marker_is_not_accepted(self):
        evidence = [{'id': 'x.y', 'text': '结果 92%。'}]
        result = validate_markdown('结果 92% [[x.y]]', evidence)
        self.assertFalse(result['evidence'])
        self.assertNotIn('结果 92%', result['content'])

    def test_citation_without_claim_is_rejected(self):
        result = validate_markdown('[[a1]]\n\n- [[a1]]', self.evidence)
        self.assertFalse(result['evidence'])
        self.assertFalse(result['content'])


if __name__ == '__main__':
    unittest.main()
