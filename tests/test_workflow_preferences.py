import os
import tempfile
import unittest
from unittest.mock import patch

from src.agents import literature as workflow
from markdown_fixture import plan, review


class ResearchPreferenceTests(unittest.TestCase):
    def setUp(self):
        domains = patch("src.tools.local_markdown.list_local_domains", return_value=[])
        domains.start()
        self.addCleanup(domains.stop)
        self.tmp = tempfile.TemporaryDirectory()
        env = patch.dict(os.environ, {"TUFA_DATA_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)

    def state(self, **research):
        return {
            "research": {"query": "climate model uncertainty", "source": "all", **research},
            "current_plan": ["compare climate model uncertainty estimates"],
            "plan": {"requirements": ["uncertainty calibration"]},
            "rounds": [],
            "evidence_pool": [],
        }

    def test_relevance_levels_change_the_priority_queue_without_losing_translations(self):
        candidates = [
            {"candidate_id": "indirect", "title": "Forecasting systems", "abstract": "Climate model uncertainty calibration compares regional estimates across models."},
            {"candidate_id": "direct", "title": "Climate model uncertainty calibration", "abstract": "A direct comparison."},
            {"candidate_id": "mismatch", "title": "Marine biology survey", "abstract": "A long description about unrelated ocean species and habitat measurements."},
        ]
        standard = workflow._prioritized_candidates(self.state(relevance_filter="standard"), candidates)
        strict = workflow._prioritized_candidates(self.state(relevance_filter="strict"), candidates)
        self.assertNotIn("mismatch", [item["candidate_id"] for item in standard])
        self.assertEqual([item["candidate_id"] for item in strict], ["direct"])

        chinese = self.state(query="气候模型不确定性", relevance_filter="standard")
        translated_candidate = {"candidate_id": "translated", "title": "Climate model uncertainty", "abstract": "A relevant English-language review."}
        self.assertEqual(workflow._prioritized_candidates(chinese, [translated_candidate]), [translated_candidate])

    def test_boilerplate_web_challenges_are_removed_from_search_queue(self):
        junk = {"title": "Access Denied", "content": "403 forbidden"}
        legitimate = {"title": "Captcha systems in research", "content": "We evaluate methods for detecting automated traffic in academic search systems."}
        self.assertTrue(workflow._obvious_web_junk(junk))
        self.assertFalse(workflow._obvious_web_junk(legitimate))
        pools, _ = workflow._record_search_results(
            {"candidate_pools": {"paper": [], "web": []}, "query_results": {}},
            "web", "captcha research", [
                {**junk, "url": "https://example.org/blocked"},
                {**legitimate, "url": "https://example.org/captcha-study"},
            ],
        )
        self.assertEqual([item["title"] for item in pools["web"]], [legitimate["title"]])

    def test_primary_preference_ranks_located_direct_text_ahead_of_abstract(self):
        abstract = {"id": "abstract", "source_level": "abstract", "text": "climate model uncertainty calibration"}
        located = {"id": "located", "source_level": "full_text", "page": 4, "text": "climate model uncertainty calibration"}
        state = self.state()
        state["evidence_pool"] = [located, abstract]
        self.assertEqual([item["id"] for item in workflow._select_evidence(state)], ["located", "abstract"])
        state["research"]["prefer_primary_evidence"] = False
        self.assertEqual([item["id"] for item in workflow._select_evidence(state)], ["abstract", "located"])

    def test_primary_preference_changes_which_equally_relevant_candidates_researcher_sees(self):
        summary = {"candidate_id": "summary", "title": "climate model uncertainty", "abstract": "calibration result", "source_level": "abstract"}
        full_text = {"candidate_id": "full", "title": "climate model uncertainty", "abstract": "calibration result", "source_level": "full_text", "page": 2}
        state = self.state()
        self.assertEqual([item["candidate_id"] for item in workflow._prioritized_candidates(state, [summary, full_text])], ["full", "summary"])
        state["research"]["prefer_primary_evidence"] = False
        self.assertEqual([item["candidate_id"] for item in workflow._prioritized_candidates(state, [summary, full_text])], ["summary", "full"])

    def run_mock_workflow(self, *, depth, actions_per_round=None):
        planner_rounds = []
        searches = []
        research_actions = []
        actions_by_round = {}
        stages = []

        def model(state, prompt_id, payload, schema, **kwargs):
            if prompt_id == "literature/background_investigator":
                if payload["phase"] == "query":
                    return workflow.BackgroundOutput(source="paper", queries=["background-q1", "background-q2", "background-q3", "background-q4"])
                return workflow.BackgroundOutput(source="paper", summary="background")
            if prompt_id == "literature/planner":
                round_count = payload["round_count"]
                planner_rounds.append(round_count)
                return plan(requirements=["answer"], plan=["answer the question"], source="paper", action="research")
            if prompt_id == "literature/researcher":
                if payload["phase"] == "act":
                    if not payload["calls_remaining"]:
                        return workflow.ResearchActionOutput(action="finish")
                    round_number = payload["current_round"]
                    action_count = actions_by_round.get(round_number, 0)
                    if actions_per_round is not None and action_count >= actions_per_round:
                        return workflow.ResearchActionOutput(action="finish")
                    actions_by_round[round_number] = action_count + 1
                    research_actions.append(payload["current_round"])
                    return workflow.ResearchActionOutput(
                        action="tool", tool_name="search_papers",
                        arguments={"query": f"research-{payload['current_round']}-{payload['calls_remaining']}"},
                    )
                return "> 局限：Evidence remains incomplete."
            if prompt_id == "literature/analyst":
                return review(
                    sufficient=False,
                    covered_requirements=[],
                    gaps=["Need more evidence."],
                    next_steps=[f"Search a distinct direction after round {payload['current_round']}"],
                    evidence_reliability_confirmed=False,
                    important_conclusions_cross_validated=False,
                )
            if prompt_id == "literature/reporter":
                return "# Research\n\n> 局限：Evidence remains incomplete."
            raise AssertionError(prompt_id)

        def invoke(name, arguments, *, context):
            searches.append(arguments["query"])
            return {"results": [], "warnings": []}

        with patch.object(workflow, "_model", side_effect=model), patch.object(workflow.registry, "invoke", side_effect=invoke), patch.object(workflow, "structured_input_units", return_value=1):
            workflow.run(
                {"query": "research question", "project_id": 1, "source": "paper", "research_mode": "deep", "research_depth": depth},
                history=[], on_stage=stages.append,
            )
        return planner_rounds, searches, stages, research_actions

    def test_depth_caps_bound_executed_rounds_actions_and_background_queries(self):
        expected_budgets = {
            "light": (1, 4, 4),
            "standard": (1, 10, 20),
            "deep": (1, 15, 48),
        }
        expected_executed_actions = {"light": [4], "standard": [10, 10], "deep": [15, 15, 15, 3]}
        for depth, expected_budget in expected_budgets.items():
            with self.subTest(depth=depth):
                background, per_round, total = expected_budget
                _, searches, stages, actions = self.run_mock_workflow(depth=depth)
                expected_round_actions = expected_executed_actions[depth]
                self.assertEqual(stages.count("analyst"), len(expected_round_actions))
                self.assertEqual(sum(query.startswith("background-") for query in searches), background)
                self.assertEqual(
                    [actions.count(round_number) for round_number in range(1, len(expected_round_actions) + 1)],
                    expected_round_actions,
                )
                self.assertLessEqual(len(actions), total)
                for actions_this_round in expected_round_actions:
                    self.assertLessEqual(actions_this_round, per_round)

    def test_deep_research_can_use_all_four_rounds_before_stopping(self):
        _, searches, stages, actions = self.run_mock_workflow(depth="deep", actions_per_round=1)
        self.assertEqual(stages.count("analyst"), 4)
        self.assertEqual(actions, [1, 2, 3, 4])
        self.assertEqual(sum(query.startswith("background-") for query in searches), 1)
        self.assertLessEqual(len(actions), workflow.DEPTH_BUDGETS["deep"]["actions"])

    @staticmethod
    def deep_review(**overrides):
        review = {
            "sufficient": True,
            "covered_requirements": ["requirement A", "requirement B"],
            "gaps": [],
            "unresolved_contradictions": [],
            "evidence_reliability_confirmed": True,
            "important_conclusions_cross_validated": True,
        }
        review.update(overrides)
        return review

    def deep_planner_state(self, review):
        return {
            "research": {"query": "research question", "project_id": 1, "source": "paper", "research_depth": "deep"},
            "plan": {"requirements": ["requirement A", "requirement B"], "plan": ["initial search direction"], "source": "paper", "action": "research"},
            "core_requirements": ["requirement A", "requirement B"],
            "current_plan": ["initial search direction"],
            "current_review": review,
            "current_draft": {},
            "current_round": 1,
            "current_source": "paper",
            "rounds": [{"plan": ["initial search direction"], "queries": ["initial query"], "tool_actions": 1, "evidence_ids": []}],
            "evidence_pool": [{"id": "e1", "title": "Research source", "text": "Evidence directly relevant to the research question.", "source_level": "abstract"}],
            "research_actions_used": 1,
            "research_decision_input_units": 1,
            "research_context_limit_reached": False,
            "candidate_pools": {"paper": [], "web": []},
        }

    def test_deep_review_can_end_early_only_after_all_core_checks_pass(self):
        state = self.deep_planner_state(self.deep_review())
        with patch.object(workflow, "_model", return_value=plan(
            requirements=["requirement A", "requirement B"], plan=[], source="paper", action="report", completion_confirmed=True,
        )):
            result = workflow._planner(state, None)

        self.assertTrue(workflow._review_is_sufficient(state, state["current_review"]))
        self.assertEqual(result["plan"]["action"], "report")
        self.assertEqual(result["research_stop_reason"], "sufficient")
        with patch.object(workflow, "_model", return_value=plan(
            requirements=["requirement A", "requirement B"], plan=[], source="paper", action="report", completion_confirmed=False,
        )):
            unconfirmed = workflow._planner(state, None)
        self.assertNotEqual(unconfirmed["research_stop_reason"], "sufficient")
        self.assertFalse(workflow._review_is_sufficient(
            state,
            self.deep_review(covered_requirements=["requirement A"]),
        ))

    def test_deep_planner_report_is_overridden_when_a_feasible_follow_up_remains(self):
        review = self.deep_review(
            sufficient=False,
            covered_requirements=["requirement A"],
            gaps=["requirement B still lacks direct evidence"],
            next_steps=["Check an independent source for requirement B"],
            evidence_reliability_confirmed=False,
            important_conclusions_cross_validated=False,
        )
        state = self.deep_planner_state(review)
        with patch.object(workflow, "_model", return_value=plan(
            requirements=["requirement A", "requirement B"], plan=[], source="paper", action="report", completion_confirmed=True,
        )):
            result = workflow._planner(state, None)

        self.assertEqual(result["plan"]["action"], "research")
        self.assertEqual(result["current_plan"], review["next_steps"])
        self.assertEqual(result["research_stop_reason"], "")

    def test_budget_stop_reason_is_distinct_from_sufficient_completion_in_report(self):
        round_limited = self.deep_planner_state(self.deep_review(sufficient=False))
        round_limited["rounds"] *= workflow.DEPTH_BUDGETS["deep"]["rounds"]
        stop = workflow._planner(round_limited, None)
        self.assertEqual(stop["research_stop_reason"], "round_budget_exhausted")

        def report_with_reason(reason, actions_used):
            state = self.deep_planner_state(self.deep_review())
            state.update({
                "rounds": [],
                "research_stop_reason": reason,
                "research_actions_used": actions_used,
            })
            payloads = []

            def model(current, prompt_id, payload, schema, **kwargs):
                self.assertEqual(prompt_id, "literature/reporter")
                payloads.append(payload)
                return "Evidence directly relevant to the research question. [[e1]]"

            with patch.object(workflow, "_model", side_effect=model):
                result = workflow._reporter(state, None)
            return payloads[0], result["content"]

        budget = workflow.DEPTH_BUDGETS["deep"]
        completed_payload, completed_report = report_with_reason("sufficient", 1)
        exhausted_payload, exhausted_report = report_with_reason("tool_budget_exhausted", budget["actions"])
        self.assertEqual(completed_payload["research_stop_reason"], "sufficient")
        self.assertNotIn("预算已用完", completed_report)
        self.assertEqual(exhausted_payload["research_stop_reason"], "tool_budget_exhausted")
        self.assertIn("研究工具总预算已用完", exhausted_report)


if __name__ == "__main__":
    unittest.main()
