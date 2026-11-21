import unittest
from src.integrations.model import _render_prompt

class ReportFormatTests(unittest.TestCase):
    def test_graph_rejects_invalid_style(self):
        from src.agents.literature import run
        for value in ('invalid', [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'report_format'):
                run({'project_id': 1, 'query': 'q', 'source': 'auto', 'report_format': value}, history=[])

    def test_context_only_contains_style_for_output_nodes(self):
        from src.agents.literature import _model_template_context
        state = {'research': {'source': 'auto', 'report_format': 'brief'}}
        for role in ('dispatcher', 'reporter', 'planner'):
            context = _model_template_context(state, 'none', role, [])
            if role == 'planner':
                self.assertNotIn('report_format', context)
            else:
                self.assertEqual(context['report_format'], 'brief')

    def test_reporter_and_dispatcher_render_each_style(self):
        labels = {'academic': ('学术综述', '严谨的学术表述'), 'exploratory': ('探索发现', '值得探索的联系'), 'brief': ('决策简报', '先给结论'), 'explainer': ('通俗解读', '通俗语言')}
        for style, (heading, guidance) in labels.items():
            for mode in ('paper', 'web'):
                prompt = _render_prompt('literature/reporter', {'report_format': style, 'search_mode': mode}, None, 'now')
                self.assertIn('### ' + heading, prompt)
                self.assertIn('研究主线：`' + mode + '`', prompt)
                for other, (other_heading, _) in labels.items():
                    if other != style:
                        self.assertNotIn('### ' + other_heading, prompt)
            for mode in ('quick', 'auto'):
                prompt = _render_prompt('runtime/dispatcher', {'report_format': style, 'research_mode': mode}, {}, 'now')
                self.assertIn(guidance, prompt)
                self.assertIn('needs_research', prompt)
