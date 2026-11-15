import unittest
from unittest.mock import patch

from src.agents import literature as workflow


class ResearchPreferenceTests(unittest.TestCase):
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

    def run_mock_workflow(self, *, depth, sufficient):
        planner_rounds = []
        searches = []
        research_actions = []
        stages = []

        def model(state, prompt_id, payload, schema, **kwargs):
            if prompt_id == "literature/background_investigator":
                if payload["phase"] == "query":
                    return workflow.BackgroundOutput(source="paper", queries=["background-q1", "background-q2", "background-q3"])
                return workflow.BackgroundOutput(source="paper", summary="background")
            if prompt_id == "literature/planner":
                round_count = payload["round_count"]
                planner_rounds.append(round_count)
                action = "research" if round_count == 0 or not sufficient else "report"
                return workflow.PlannerOutput(requirements=["answer"], plan=["answer the question"], source="paper", action=action)
            if prompt_id == "literature/researcher":
                if payload["phase"] == "act":
                    research_actions.append(payload["current_round"])
                    return workflow.ResearchActionOutput(
                        action="tool", tool_name="search_papers",
                        arguments={"query": f"research-{payload['current_round']}-{payload['calls_remaining']}"},
                    )
                return workflow.ResearchDraftOutput(draft=workflow.Draft(limitations=[workflow.Claim(text="Evidence remains incomplete.")]))
            if prompt_id == "literature/analyst":
                return workflow.AnalystOutput(sufficient=sufficient, gaps=[] if sufficient else ["Need more evidence."], next_steps=[] if sufficient else ["Search another direction"])
            if prompt_id == "literature/reporter":
                return workflow.ReporterOutput(title="Research", limitations=[workflow.Claim(text="Evidence remains incomplete.")])
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

    def test_depth_caps_bound_executed_rounds_and_background_queries(self):
        expected_actions = {
            "light": [4],
            "standard": [6, 4],
            "deep": [6, 6, 3],
        }
        for depth, per_round in expected_actions.items():
            with self.subTest(depth=depth):
                planner_rounds, searches, stages, actions = self.run_mock_workflow(depth=depth, sufficient=False)
                self.assertEqual(stages.count("analyst"), len(per_round))
                self.assertEqual(sum(query.startswith("background-") for query in searches), {"light": 1, "standard": 2, "deep": 3}[depth])
                self.assertEqual([actions.count(round_number) for round_number in range(1, len(per_round) + 1)], per_round)
                self.assertEqual(len(actions), sum(per_round))

    def test_deep_research_stops_after_one_sufficient_analyst_review(self):
        _, searches, stages, _ = self.run_mock_workflow(depth="deep", sufficient=True)
        self.assertEqual(stages.count("analyst"), 1)
        self.assertEqual(sum(query.startswith("background-") for query in searches), 3)


if __name__ == "__main__":
    unittest.main()
