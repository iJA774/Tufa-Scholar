import unittest
from unittest.mock import patch

from src.agents import literature


class ReporterGapsTests(unittest.TestCase):
    def state(self, depth="standard"):
        old = {"sufficient": False, "gaps": ["旧缺口"], "next_steps": ["旧补查"]}
        current = {"sufficient": True, "gaps": [], "next_steps": []}
        return {
            "research": {"query": "研究问题", "source": "web", "research_depth": depth},
            "rounds": [
                {"review": old, "draft": "> 局限：旧缺口"},
                {"review": current, "draft": ""},
            ],
            "current_review": current,
            "background": {"gaps": ["背景旧缺口"]},
            "research_stop_reason": "round_budget_exhausted",
            "evidence_pool": [],
        }

    def test_latest_review_replaces_historical_and_background_gaps(self):
        for depth in ("light", "standard"):
            with self.subTest(depth=depth):
                state = self.state(depth)
                self.assertEqual(literature._outstanding_gaps(state), [])
                fallback = literature._reporter_fallback(state)
                self.assertNotIn("旧缺口", fallback["content"])
                self.assertNotIn("预算耗尽", fallback["content"])

    def test_current_gap_reaches_the_model_without_an_automatic_footer(self):
        state = self.state()
        state["current_review"] = {"sufficient": False, "gaps": ["缺少直接对比实验"], "next_steps": ["当前补查"]}
        state["warnings"] = ["paper_search_incomplete"]
        markdown = "# 方法比较\n\n缺少直接对比实验，因此两者的相对表现尚不能确认。"
        with patch.object(literature, "_model", return_value=markdown) as model:
            result = literature._reporter(state, None)
        payload = model.call_args.args[2]
        self.assertEqual(payload["outstanding_gaps"], ["缺少直接对比实验"])
        self.assertEqual(payload["research_stop_reason"], "round_budget_exhausted")
        self.assertEqual(result["content"], markdown)
        fallback = literature._reporter_fallback(state)
        self.assertNotIn("缺少直接对比实验", fallback["content"])
        self.assertNotIn("预算耗尽", fallback["content"])

    def test_fallback_preserves_latest_valid_scoped_draft(self):
        state = self.state()
        state['research'].update(source='collection', paper_ids=[1])
        state['evidence_pool'] = [
            {'id': 'old', 'paper_id': 1, 'title': 'old', 'text': 'accuracy 80%', 'source_level': 'abstract'},
            {'id': 'new', 'paper_id': 1, 'title': 'new', 'text': 'accuracy 92%', 'source_level': 'abstract'},
            {'id': 'outside', 'paper_id': 2, 'title': 'outside', 'text': 'accuracy 99%', 'source_level': 'abstract'},
        ]
        state['rounds'][0]['draft'] = '# 旧稿\n\naccuracy 80% [[old]]'
        state['rounds'][1]['draft'] = '# 新稿\n\naccuracy 92% [[new]]\n\naccuracy 99% [[outside]]'
        fallback = literature._reporter_fallback(state)
        self.assertIn('# 新稿', fallback['content'])
        self.assertIn('accuracy 92%', fallback['content'])
        self.assertNotIn('旧稿', fallback['content'])
        self.assertNotIn('99%', fallback['content'])
        self.assertNotIn('最近一份通过校验的研究草稿', fallback['content'])
        self.assertEqual([item['id'] for item in fallback['evidence']], ['new'])

    def test_deep_review_retains_current_contradiction(self):
        state = self.state("deep")
        state.update(core_requirements=["问题"], current_review={
            "sufficient": False, "covered_requirements": ["问题"], "gaps": [],
            "unresolved_contradictions": ["当前矛盾"],
            "evidence_reliability_confirmed": True, "important_conclusions_cross_validated": True,
        })
        self.assertEqual(literature._outstanding_gaps(state), ["尚未解决的重要矛盾：当前矛盾"])


if __name__ == "__main__":
    unittest.main()
