import unittest
from unittest.mock import patch

from src.agents import literature as workflow
from src.integrations.web_page import WebPageError


class BackgroundPlanningTests(unittest.TestCase):
    def state(self, depth="standard", **options):
        return {"research": {"project_id": 1, "query": "accuracy", "source": "web",
                             "research_depth": depth, **options}}

    def test_queries_reads_and_planner_candidates_obey_budgets_and_visible_ids(self):
        for depth, initial, supplement, reads, planner_cap, per_query in [
            ("light", 1, 0, 0, 5, 3), ("standard", 3, 1, 1, 12, 5), ("deep", 4, 2, 3, 18, 5),
        ]:
            with self.subTest(depth=depth):
                phases = []
                def model(state, prompt, payload, schema, **kwargs):
                    if payload["phase"] == "query":
                        return workflow.BackgroundOutput(source="web", queries=["q1", "Q1!", "q2", "q3", "q4", "q5"])
                    phases.append(payload)
                    ids = [item["candidate_id"] for item in payload["candidates"]]
                    return workflow.BackgroundOutput(
                        source="web", selected_candidate_ids=ids + ["forged"],
                        additional_queries=["Q1", "extra1", "extra2", "extra3"],
                        read_candidate_ids=["forged", *ids],
                        confirmed_background=[workflow.BackgroundFinding(text="valid", candidate_ids=[ids[0], "forged"]),
                                              workflow.BackgroundFinding(text="invented", candidate_ids=["forged"])],
                    )

                def search(query, limit):
                    return [{"title": f"{query} {index}", "url": f"https://example.org/{query}/{index}",
                             "content": "accuracy snippet", "source_level": "web_snippet", "provider": "fixture"}
                            for index in range(7)]

                with patch.object(workflow, "_model", side_effect=model), \
                     patch("src.tools.search.search_web", side_effect=search) as search_tool, \
                     patch("src.tools.read_web_page.fetch_page", return_value={"markdown": "body", "title": "Page"}) as fetch:
                    result = workflow._background_investigator(self.state(depth), None)
                self.assertEqual(search_tool.call_count, initial + supplement)
                self.assertTrue(all(call.kwargs["limit"] == per_query for call in search_tool.call_args_list))
                self.assertEqual(fetch.call_count, reads)
                self.assertEqual(len(result["background"]["selected_candidates"]), min((initial + supplement) * per_query, planner_cap))
                self.assertNotIn("forged", str(result["background"]))
                self.assertEqual(result["background"]["confirmed_background"][0]["text"], "valid")
                self.assertEqual(len(result["background"]["confirmed_background"]), 1)
                self.assertEqual(len(phases), 2 if supplement or reads else 1)
                self.assertNotIn("evidence_pool", result)
                self.assertEqual(len(result["read_web_pages"]), reads)
                planner = workflow._planner_background_candidates({**self.state(depth), **result})
                self.assertTrue(all(len(item.get("summary", "")) <= 240 for item in planner))
                self.assertTrue(all(item.get("source_level") and item.get("provider") for item in planner))

    def test_read_failure_consumes_limit_and_keeps_snippet(self):
        def model(state, prompt, payload, schema, **kwargs):
            if payload["phase"] == "query":
                return workflow.BackgroundOutput(source="web", queries=["accuracy"])
            ids = [item["candidate_id"] for item in payload["candidates"]]
            return workflow.BackgroundOutput(source="web", selected_candidate_ids=ids, read_candidate_ids=ids)

        pages = [{"title": str(index), "url": f"https://example.org/{index}", "content": "accuracy",
                  "source_level": "web_snippet"} for index in range(3)]
        with patch.object(workflow, "_model", side_effect=model), \
             patch("src.tools.search.search_web", return_value=pages), \
             patch("src.tools.read_web_page.fetch_page", side_effect=WebPageError()) as fetch:
            result = workflow._background_investigator(self.state(), None)
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(result["errors"])
        self.assertEqual(result["background"]["selected_candidates"][0]["source_level"], "web_snippet")

    def test_empty_queries_fall_back_and_disabled_network_never_searches(self):
        def model(state, prompt, payload, schema, **kwargs):
            if payload["phase"] == "query":
                return workflow.BackgroundOutput(source="web", queries=[" ", "!!!"])
            return workflow.BackgroundOutput(source="web", summary="unsupported claim")
        with patch.object(workflow, "_model", side_effect=model), \
             patch("src.tools.search.search_web", return_value=[]) as search:
            result = workflow._background_investigator(self.state(), None)
        self.assertEqual(search.call_args.args[0], "accuracy")
        self.assertEqual(result["background"]["summary"], "")
        self.assertTrue(result["background"]["gaps"])
        with patch.object(workflow, "_model", side_effect=model), \
             patch("src.tools.search.search_web") as search:
            result = workflow._background_investigator(self.state(network_search=False, local_literature=False), None)
        search.assert_not_called()
        self.assertEqual(result["background"]["selected_candidates"], [])


if __name__ == "__main__":
    unittest.main()
