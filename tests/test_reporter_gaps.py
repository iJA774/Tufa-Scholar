import unittest

from src.agents import literature


class ReporterGapsTests(unittest.TestCase):
    def state(self, depth="standard"):
        old = {"sufficient": False, "gaps": ["旧缺口"], "next_steps": ["旧补查"]}
        current = {"sufficient": True, "gaps": [], "next_steps": []}
        return {
            "research": {"query": "研究问题", "source": "web", "research_depth": depth},
            "rounds": [
                {"review": old, "draft": {"limitations": [{"text": "旧缺口", "citations": []}]}},
                {"review": current, "draft": {"limitations": []}},
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
                self.assertEqual(literature._outstanding_gaps(state, fallback=True), [])
                caveats = literature._warning_caveats(state, fallback=True)
                self.assertTrue(any("预算耗尽" in item for item in caveats))
                self.assertFalse(any("旧缺口" in item for item in caveats))
                fallback = literature._reporter_fallback(state, "报告模型不可用")
                self.assertNotIn("旧缺口", fallback["content"])

    def test_current_incomplete_review_is_preserved(self):
        state = self.state()
        state["current_review"] = {"sufficient": False, "gaps": ["当前缺口"], "next_steps": ["当前补查"]}
        self.assertEqual(literature._outstanding_gaps(state), ["当前缺口"])
        self.assertEqual(literature._outstanding_gaps(state, fallback=True), ["当前缺口", "当前补查"])
        self.assertIn("当前缺口", literature._warning_caveats(state))

    def test_sufficient_review_supersedes_latest_draft_process_gap(self):
        state = self.state()
        state["rounds"][-1]["draft"]["limitations"] = [
            {"text": "本轮尚未找到论文原文", "citations": []},
        ]
        fallback = literature._reporter_fallback(state, "报告模型不可用")
        self.assertNotIn("本轮尚未找到论文原文", fallback["content"])
        state["current_review"] = {}
        state["rounds"][-1]["review"] = {}
        fallback_without_review = literature._reporter_fallback(state, "报告模型不可用")
        self.assertIn("本轮尚未找到论文原文", fallback_without_review["content"])

    def test_background_gaps_retained_when_no_review_exists(self):
        state = self.state()
        state.update(rounds=[], current_review={})
        self.assertIn("背景旧缺口", literature._outstanding_gaps(state, fallback=True))

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
