import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.agents import literature as workflow
from markdown_fixture import plan, review, draft as draft_text, section, claim
from src.infrastructure.diagnostics import diagnostic_scope
from src.integrations.model import ModelServiceError


def researcher_output(*, action="finish", tool_name="", arguments=None, reason="", draft=None, findings=None):
    """Return a lightweight test response independent of either phase schema."""
    return SimpleNamespace(
        action=action,
        tool_name=tool_name,
        arguments=arguments or {},
        reason=reason,
        draft=draft or draft_text(),
        findings=findings or [],
    )


def sample_paper(identifier=41):
    return {
        "id": identifier,
        "title": "Accuracy study",
        "abstract": "Accuracy was 92 percent in the reported evaluation.",
        "doi": f"10.1234/accuracy-{identifier}",
        "source_level": "abstract",
    }


def sample_web_page(content="Accuracy is 92% now."):
    return {
        "title": "Accuracy page",
        "url": "https://example.org/accuracy",
        "content": "Search result snippet for accuracy.",
        "source_level": "web_snippet",
        "provider": "fixture",
        "retrieved_at": "2026-11-12T10:00:00+08:00",
    }


class WorkflowFailureTests(unittest.TestCase):
    def setUp(self):
        domains = patch("src.tools.local_markdown.list_local_domains", return_value=[])
        domains.start()
        self.addCleanup(domains.stop)
        self.tmp = tempfile.TemporaryDirectory()
        env = patch.dict(os.environ, {"TUFA_DATA_DIR": self.tmp.name}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_analyst_retries_invalid_control_and_keeps_the_corrected_review(self):
        state = {"research": {"query": "accuracy", "source": "local"}}
        corrected = review(sufficient=True, covered_requirements=["accuracy"])
        with patch.object(workflow, "_model", side_effect=["Review with no final control block", corrected]) as model:
            result = workflow._analyst(state, None, None)

        self.assertTrue(result["current_review"]["sufficient"])
        self.assertEqual(result["current_review"]["covered_requirements"], ["accuracy"])
        first, second = (call.args[2] for call in model.call_args_list)
        self.assertEqual(second, {**first, "retry_reason": "invalid_analyst_control"})

    def test_analyst_distinguishes_invalid_output_from_service_failure(self):
        cases = [(["invalid", "still invalid"], "invalid_output", "格式检查"),
                 ([ModelServiceError("private details")], "model_failed", "服务不可用")]
        for responses, code, expected_message in cases:
            with self.subTest(code=code):
                state = {"research": {"query": "accuracy", "source": "local"}}
                with patch.object(workflow, "_model", side_effect=responses) as model:
                    result = workflow._analyst(state, None, None)
                self.assertEqual(model.call_count, len(responses))
                self.assertFalse(result["current_review"]["sufficient"])
                self.assertIn(expected_message, result["current_review"]["gaps"][0])
                self.assertEqual(result["errors"][-1]["code"], code)
                self.assertNotIn("private details", str(result))

    @staticmethod
    def answer_with_evidence(evidence, text="材料与问题相关。"):
        return draft_text(
            sections=[section(
                heading="已收集证据",
                paragraphs=[claim(text=text, citations=[item["id"] for item in evidence])],
            )] if evidence else [],
            limitations=[claim(text="资料覆盖仍有限。", citations=[])],
        )

    def _invoke_run(self, model, *, source="local", query="accuracy", retrieve=None, web_results=None, fetch_page=None, on_stage=None):
        def default_retrieve(project_id, query_text, source_name):
            return {"query": query_text, "source": source_name, "method": "fixture", "results": [sample_paper()], "warnings": []}

        retrieve = retrieve or default_retrieve
        def adapted_model(prompt_id, payload, schema, **kwargs):
            result = model(prompt_id, payload, schema, **kwargs)
            if prompt_id == "literature/researcher" and payload.get("phase") == "draft" and isinstance(result, SimpleNamespace):
                return result.draft
            return result

        with patch.object(workflow, "structured", side_effect=adapted_model), \
             patch("src.tools.search.retrieve", side_effect=retrieve), \
             patch("src.tools.search.retrieve_collection", side_effect=retrieve), \
             patch("src.tools.search.search_web", return_value=web_results or []), \
             patch("src.tools.read_web_page.fetch_page", side_effect=fetch_page or RuntimeError("unexpected page fetch")), \
             patch("src.persistence.repository.project_evidence", return_value=[]):
            return workflow.run(
                {"project_id": 1, "query": query, "source": source},
                history=[],
                on_stage=on_stage,
            )

    def test_researcher_metered_actions_are_followed_by_tool_free_draft(self):
        paper = sample_paper()
        researcher_calls = []
        retrieved = []

        class Model:
            def __call__(self, prompt_id, payload, schema, **kwargs):
                if prompt_id == "runtime/dispatcher":
                    return workflow.DispatcherOutput(needs_research=True, reason="需要证据")
                if prompt_id == "literature/background_investigator":
                    if payload["phase"] == "query":
                        return workflow.BackgroundOutput(source="paper", queries=[payload["question"]])
                    chosen = payload["candidates"][0]
                    return workflow.BackgroundOutput(source="paper", selected_candidate_ids=[chosen["candidate_id"]])
                if prompt_id == "literature/planner":
                    return plan(
                        requirements=["查找支持资料"], plan=["检索论文"], source="paper",
                        action="research" if payload["round_count"] == 0 else "report",
                    )
                if prompt_id == "literature/researcher":
                    researcher_calls.append((payload, kwargs["template_context"], kwargs.get("native_tools")))
                    if payload["phase"] == "act":
                        index = sum(item[0]["phase"] == "act" for item in researcher_calls)
                        if not payload["calls_remaining"]:
                            return researcher_output(action="finish")
                        return researcher_output(
                            action="tool", tool_name="search_papers",
                            arguments={"query": f"accuracy retry {index}"},
                        )
                    return researcher_output(
                        action="finish",
                        draft=draft_text(limitations=[claim(text="工具预算耗尽，尚未收集到可核查证据。")]),
                    )
                if prompt_id == "literature/analyst":
                    return review(sufficient=False, gaps=["暂无可核查证据"], review="资料不足。")
                if prompt_id == "literature/reporter":
                    return draft_text(limitations=[claim(text="本轮资料不足，未能形成有证据支持的结论。")])
                raise AssertionError(prompt_id)

        def retrieve(project_id, query_text, source_name):
            retrieved.append(query_text)
            return {"query": query_text, "source": source_name, "method": "fixture", "results": [paper], "warnings": []}

        stages = []
        with patch.object(workflow.registry, "invoke", wraps=workflow.registry.invoke) as invoke:
            content, evidence = self._invoke_run(Model(), source="openalex", retrieve=retrieve, on_stage=stages.append)
            invoke_calls = [call.args[0] for call in invoke.call_args_list]

        act_calls = [item for item in researcher_calls if item[0]["phase"] == "act"]
        draft_calls = [item for item in researcher_calls if item[0]["phase"] == "draft"]
        action_cap = workflow.DEPTH_BUDGETS["standard"]["actions_per_round"]
        self.assertEqual(len(act_calls), action_cap + 1)
        self.assertEqual(len(draft_calls), 1)
        self.assertEqual(draft_calls[0][1]["available_tools"], [])
        self.assertIsNone(draft_calls[0][2])
        for _, template_context, native_tools in act_calls:
            self.assertTrue(native_tools)
            self.assertTrue(all(set(item) == {"name", "description"} for item in template_context["available_tools"]))
            for tool in native_tools:
                reason = tool["input_schema"]["properties"]["reason"]
                self.assertEqual(reason["type"], "string")
                self.assertEqual(reason["maxLength"], 240)
                self.assertNotIn("reason", tool["input_schema"].get("required", []))
        for payload, _, _ in act_calls:
            self.assertNotIn("history", payload)
            self.assertNotIn("background", payload)
            self.assertNotIn("rounds", payload)
            self.assertNotIn("candidates", payload)
            self.assertTrue(all(len(item.get("abstract", "")) <= 240 for item in payload["current_candidates"]))
            self.assertTrue(all(len(item.get("text", "")) <= 240 for item in payload["evidence"]))
        self.assertNotIn("background", draft_calls[0][0])
        self.assertNotIn("rounds", draft_calls[0][0])
        self.assertEqual(invoke_calls.count("search_papers"), 1 + action_cap)
        self.assertEqual(invoke_calls.count("collect_evidence"), 0)
        self.assertEqual(len(retrieved), 1 + action_cap)
        self.assertEqual(retrieved[0], "accuracy")
        self.assertFalse(evidence)
        self.assertIn("未能形成有证据支持的结论", content)
        self.assertIn("没有可核查证据", content)
        self.assertEqual(stages[-1], "reporter")

    def test_background_and_planner_model_failures_fall_back_to_original_query_and_report(self):
        paper = sample_paper()
        calls = []
        searched = []

        class Model:
            def __call__(self, prompt_id, payload, schema, **kwargs):
                calls.append((prompt_id, payload))
                if prompt_id == "runtime/dispatcher":
                    return workflow.DispatcherOutput(needs_research=True, reason="需要证据")
                if prompt_id in {"literature/background_investigator", "literature/planner"}:
                    raise ModelServiceError("private upstream detail")
                if prompt_id == "literature/researcher":
                    evidence = payload["evidence"]
                    if not evidence:
                        candidate = payload["current_candidates"][0]
                        return researcher_output(
                            action="tool", tool_name="collect_evidence",
                            arguments={"query": "accuracy", "candidate_ids": [candidate["candidate_id"]]},
                        )
                    return researcher_output(
                        action="finish",
                        draft=draft_text(sections=[section(
                            heading="资料", paragraphs=[claim(text="资料记录了相关评估结果。", citations=[evidence[0]["id"]])],
                        )]),
                    )
                if prompt_id == "literature/analyst":
                    return review(sufficient=False, gaps=["模型审查失败后仍须报告"], review="未充分。")
                if prompt_id == "literature/reporter":
                    return self_test.answer_with_evidence(payload["evidence"])
                raise AssertionError(prompt_id)

        self_test = self

        def retrieve(project_id, query_text, source_name):
            searched.append((query_text, source_name))
            return {"query": query_text, "source": source_name, "method": "fixture", "results": [paper], "warnings": []}

        stages = []
        invoke_calls = []
        with patch.object(workflow.registry, "invoke", wraps=workflow.registry.invoke) as invoke:
            content, evidence = self._invoke_run(
                Model(), source="auto", query="question as submitted", retrieve=retrieve, on_stage=stages.append,
            )
            invoke_calls = [(call.args[0], call.args[1], call.kwargs) for call in invoke.call_args_list]

        self.assertIn(("question as submitted", "openalex"), searched)
        self.assertLessEqual(len(searched), workflow.DEPTH_BUDGETS["standard"]["background_searches"])
        self.assertTrue(all(query_text == "question as submitted" for query_text, _ in searched))
        self.assertEqual(len([call for call in calls if call[0] == "literature/reporter"]), 1)
        self.assertIn("reporter", stages)
        self.assertIn("collect_evidence", [call[0] for call in invoke_calls])
        self.assertTrue(evidence)
        self.assertNotIn("private upstream detail", content)
        self.assertTrue(all("private upstream detail" not in repr(payload) for _, payload in calls))

    def test_forged_candidate_and_papers_body_are_rejected_before_evidence_tool(self):
        paper = sample_paper()
        reporter_payloads = []

        class Model:
            researcher_actions = 0

            def __call__(self, prompt_id, payload, schema, **kwargs):
                if prompt_id == "runtime/dispatcher":
                    return workflow.DispatcherOutput(needs_research=True, reason="需要证据")
                if prompt_id == "literature/background_investigator":
                    if payload["phase"] == "query":
                        return workflow.BackgroundOutput(source="paper", queries=["accuracy"])
                    candidate = payload["candidates"][0]
                    return workflow.BackgroundOutput(source="paper", selected_candidate_ids=[candidate["candidate_id"]])
                if prompt_id == "literature/planner":
                    return plan(
                        requirements=["检查准确率"], plan=["阅读论文"], source="paper",
                        action="research" if payload["round_count"] == 0 else "report",
                    )
                if prompt_id == "literature/researcher":
                    self.researcher_actions += 1
                    if self.researcher_actions == 1:
                        return researcher_output(
                            action="tool", tool_name="collect_evidence",
                            arguments={
                                "query": "accuracy",
                                "candidate_ids": ["C-forged-candidate"],
                                "papers": [{"title": "FORGED_PRIVATE", "abstract": "Accuracy was 99 percent."}],
                            },
                        )
                    return researcher_output(
                        action="finish",
                        draft=draft_text(limitations=[claim(text="当前没有可核查证据，无法支持结论。")]),
                    )
                if prompt_id == "literature/analyst":
                    return review(sufficient=False, gaps=["没有有效证据"], review="证据不足。")
                if prompt_id == "literature/reporter":
                    reporter_payloads.append(payload)
                    return draft_text(limitations=[claim(text="没有可核查证据，报告无法给出事实结论。")])
                raise AssertionError(prompt_id)

        with patch.object(workflow.registry, "invoke", wraps=workflow.registry.invoke) as invoke, \
             diagnostic_scope(run_id=42), self.assertLogs("tufa.agent", "WARNING") as logs:
            content, evidence = self._invoke_run(Model(), source="openalex", retrieve=lambda project, query, source: {
                "query": query, "source": source, "method": "fixture", "results": [paper], "warnings": [],
            })
            invoke_names = [call.args[0] for call in invoke.call_args_list]

        self.assertNotIn("collect_evidence", invoke_names)
        rejection = next(line for line in logs.output if "research_tool rejected" in line)
        self.assertIn("run_id=42", rejection)
        self.assertIn("tool=collect_evidence source=paper code=unknown_candidate", rejection)
        self.assertIn('"requested_ids":["<invalid-id>"]', rejection)
        self.assertRegex(rejection, r'"pool_ids":\["C[0-9a-f]{16}"\]')
        self.assertNotIn("C-forged-candidate", rejection)
        self.assertNotIn("FORGED_PRIVATE", rejection)
        self.assertNotIn("accuracy", rejection)
        self.assertFalse(evidence)
        self.assertEqual(reporter_payloads[0]["evidence"], [])
        self.assertNotIn("FORGED_PRIVATE", content)
        self.assertNotIn("99%", content)
        self.assertRegex(content, "没有可核查证据|资料不足")

    def test_conflicting_web_candidate_references_never_reach_the_tool(self):
        candidate = {**sample_web_page(), "candidate_id": "C" + "1" * 16}
        other = {**sample_web_page(), "candidate_id": "C" + "2" * 16, "url": "https://example.org/other"}
        state = {
            "research": {"project_id": 1, "query": "accuracy", "source": "web"},
            "candidate_pools": {"web": [candidate, other]},
        }
        with patch.object(workflow.registry, "invoke") as invoke, self.assertRaises(workflow.ToolCallValidationError) as error:
            workflow._invoke_tool(state, "web", "read_web_page", {
                "candidate_id": candidate["candidate_id"], "url": other["url"],
            })
        self.assertEqual(error.exception.code, "conflicting_candidate_reference")
        invoke.assert_not_called()

    def test_partial_paper_search_warning_is_sanitized_and_evidence_is_reported(self):
        paper = sample_paper()
        retrieval_calls = []
        model_payloads = []

        class Model:
            researcher_actions = 0

            def __call__(self, prompt_id, payload, schema, **kwargs):
                model_payloads.append((prompt_id, payload))
                if prompt_id == "runtime/dispatcher":
                    return workflow.DispatcherOutput(needs_research=True, reason="需要论文资料")
                if prompt_id == "literature/background_investigator":
                    if payload["phase"] == "query":
                        return workflow.BackgroundOutput(source="paper", queries=["accuracy"])
                    candidate = payload["candidates"][0]
                    return workflow.BackgroundOutput(source="paper", selected_candidate_ids=[candidate["candidate_id"]])
                if prompt_id == "literature/planner":
                    return plan(
                        requirements=["检查相关研究"], plan=["检索并取证"], source="paper",
                        action="research" if payload["round_count"] == 0 else "report",
                    )
                if prompt_id == "literature/researcher":
                    self.researcher_actions += 1
                    if self.researcher_actions == 1:
                        return researcher_output(
                            action="tool", tool_name="search_papers", arguments={"query": "accuracy related work"},
                        )
                    if self.researcher_actions == 2:
                        candidate = payload["current_candidates"][0]
                        return researcher_output(
                            action="tool", tool_name="collect_evidence",
                            arguments={"query": "accuracy", "candidate_ids": [candidate["candidate_id"]]},
                        )
                    evidence = payload["evidence"]
                    return researcher_output(
                        action="finish",
                        draft=draft_text(sections=[section(
                            heading="研究资料",
                            paragraphs=[claim(text="已取得与问题相关的研究资料。", citations=[item["id"] for item in evidence])],
                        )]),
                    )
                if prompt_id == "literature/analyst":
                    return review(sufficient=False, gaps=["部分检索来源不可用"], review="现有资料仍有缺口。")
                if prompt_id == "literature/reporter":
                    return self_test.answer_with_evidence(payload["evidence"], text="已保留可用的研究资料。")
                raise AssertionError(prompt_id)

        self_test = self

        def retrieve(project_id, query_text, source_name):
            retrieval_calls.append(query_text)
            if len(retrieval_calls) == 1:
                return {"query": query_text, "source": source_name, "method": "fixture", "results": [paper], "warnings": []}
            return {
                "query": query_text,
                "source": source_name,
                "method": "fixture",
                "results": [paper],
                "warnings": ["private upstream message"],
            }

        invoke_calls = []
        with patch.object(workflow.registry, "invoke", wraps=workflow.registry.invoke) as invoke:
            content, evidence = self._invoke_run(Model(), source="all", retrieve=retrieve)
            invoke_calls = [(call.args[0], call.args[1], call.kwargs) for call in invoke.call_args_list]

        self.assertIn("search_papers", [name for name, _, _ in invoke_calls])
        self.assertIn("collect_evidence", [name for name, _, _ in invoke_calls])
        self.assertTrue(evidence)
        self.assertIn("已保留可用的研究资料", content)
        self.assertIn("资料可能不完整", content)
        self.assertNotIn("private upstream message", content)
        self.assertTrue(model_payloads)
        for _, payload in model_payloads:
            self.assertNotIn("private upstream message", repr(payload))
            if payload.get("warnings"):
                self.assertEqual(payload["warnings"], ["paper_search_incomplete"])

    def test_web_page_reads_once_and_free_collection_keeps_the_budget(self):
        full_page = "Accuracy is 92% now."
        web_calls = []
        fetch_calls = []
        researcher_payloads = []
        native_act_calls = []
        reporter_payloads = []

        class Model:
            researcher_actions = 0

            def __call__(self, prompt_id, payload, schema, **kwargs):
                if prompt_id == "runtime/dispatcher":
                    return workflow.DispatcherOutput(needs_research=True, reason="需要网页资料")
                if prompt_id == "literature/background_investigator":
                    if payload["phase"] == "query":
                        return workflow.BackgroundOutput(source="web", queries=["accuracy"])
                    candidate = payload["candidates"][0]
                    return workflow.BackgroundOutput(source="web", selected_candidate_ids=[candidate["candidate_id"]])
                if prompt_id == "literature/planner":
                    return plan(
                        requirements=["读取网页正文"], plan=["读取网页并提取证据"], source="web",
                        action="research" if payload["round_count"] == 0 else "report",
                    )
                if prompt_id == "literature/researcher":
                    researcher_payloads.append(payload)
                    if payload.get("phase") == "act":
                        native_act_calls.append((payload, kwargs.get("native_tools")))
                    self.researcher_actions += 1
                    candidates = payload["current_candidates"]
                    candidate_id = candidates[0]["candidate_id"]
                    if self.researcher_actions == 1:
                        return researcher_output(
                            action="tool", tool_name="read_web_page",
                            arguments={"candidate_id": candidate_id, "limit": 20},
                        )
                    if self.researcher_actions == 2:
                        read = next(item for item in payload["tool_results"] if item["tool"] == "read_web_page")
                        self_test.assertNotIn("next_offset", read)
                        web_calls.append(payload["calls_remaining"])
                        return researcher_output(
                            action="tool", tool_name="collect_evidence",
                            arguments={"query": "accuracy", "candidate_ids": [candidate_id]},
                        )
                    if self.researcher_actions == 3:
                        web_calls.append(payload["calls_remaining"])
                    evidence = payload["evidence"]
                    return researcher_output(
                        action="finish",
                        draft=draft_text(sections=[section(
                            heading="网页证据",
                            paragraphs=[claim(text=f"读取到第 {index + 1} 段网页资料。", citations=[item["id"]]) for index, item in enumerate(evidence)],
                        )]),
                    )
                if prompt_id == "literature/analyst":
                    return review(sufficient=True, covered_requirements=["网页正文"], review="已读取两个片段。")
                if prompt_id == "literature/reporter":
                    reporter_payloads.append(payload)
                    evidence = payload["evidence"]
                    return self_test.answer_with_evidence(evidence, text="网页资料已按片段读取。")
                raise AssertionError(prompt_id)

        self_test = self

        def fetch(url):
            fetch_calls.append(url)
            return {"url": url, "title": "Accuracy page", "markdown": full_page, "retrieved_at": "2026-11-12T10:00:00+08:00"}

        stages = []
        with patch.object(workflow.registry, "invoke", wraps=workflow.registry.invoke) as invoke:
            content, evidence = self._invoke_run(
                Model(), source="web", query="accuracy", web_results=[sample_web_page(full_page)],
                fetch_page=fetch, on_stage=stages.append,
            )
            invoke_calls = [(call.args[0], call.args[1], call.kwargs) for call in invoke.call_args_list]

        self.assertEqual(web_calls, [9, 9])
        self.assertTrue(all(native_tools for _, native_tools in native_act_calls))
        read_tool = next(
            item for item in native_act_calls[2][1]
            if item["name"] == "read_web_page"
        )
        self.assertNotIn("offset", read_tool["input_schema"]["properties"])
        prior_read = next(
            item for item in native_act_calls[2][0]["tool_results"]
            if item["tool"] == "read_web_page"
        )
        self.assertNotIn("next_offset", prior_read)
        self.assertEqual(fetch_calls, ["https://example.org/accuracy"])
        read_calls = [(name, args, kwargs) for name, args, kwargs in invoke_calls if name == "read_web_page"]
        self.assertEqual(len(read_calls), 1)
        self.assertEqual([name for name, _, _ in invoke_calls].count("collect_evidence"), 1)
        self.assertEqual(sorted((item["char_start"], item["char_end"]) for item in evidence), [(0, 20)])
        self.assertEqual(len({item["id"] for item in evidence}), 1)
        self.assertEqual(len(reporter_payloads), 1)
        self.assertIn("网页资料已按片段读取", content)
        self.assertEqual(stages[-1], "reporter")

    def test_successful_repeated_page_read_is_skipped(self):
        fetch_calls = []
        draft_feedback = []

        class Model:
            researcher_actions = 0

            def __call__(self, prompt_id, payload, schema, **kwargs):
                if prompt_id == "runtime/dispatcher":
                    return workflow.DispatcherOutput(needs_research=True, reason="需要网页资料")
                if prompt_id == "literature/background_investigator":
                    if payload["phase"] == "query":
                        return workflow.BackgroundOutput(source="web", queries=["accuracy"])
                    candidate = payload["candidates"][0]
                    return workflow.BackgroundOutput(source="web", selected_candidate_ids=[candidate["candidate_id"]])
                if prompt_id == "literature/planner":
                    return plan(
                        requirements=["读取网页正文"], plan=["读取网页"], source="web",
                        action="research" if payload["round_count"] == 0 else "report",
                    )
                if prompt_id == "literature/researcher":
                    if payload["phase"] == "act":
                        self.researcher_actions += 1
                        candidate_id = payload["current_candidates"][0]["candidate_id"]
                        if self.researcher_actions <= 2:
                            return researcher_output(
                                action="tool", tool_name="read_web_page",
                                arguments={"candidate_id": candidate_id, "limit": 20},
                            )
                        return researcher_output(action="finish")
                    draft_feedback.extend(payload.get("tool_feedback", []))
                    return researcher_output(
                        draft=draft_text(limitations=[claim(text="重复网页读取已跳过。")]),
                    )
                if prompt_id == "literature/analyst":
                    return review(sufficient=False, gaps=["页面正文尚未提取"], review="资料不足。")
                if prompt_id == "literature/reporter":
                    return draft_text(limitations=[claim(text="当前没有可引用证据。")])
                raise AssertionError(prompt_id)

        def fetch(url):
            fetch_calls.append(url)
            return {"url": url, "title": "Accuracy page", "markdown": "Accuracy is 92% now.", "retrieved_at": "2026-11-12T10:00:00+08:00"}

        invoke_calls = []
        with patch.object(workflow.registry, "invoke", wraps=workflow.registry.invoke) as invoke:
            self._invoke_run(Model(), source="web", query="accuracy", web_results=[sample_web_page()], fetch_page=fetch)
            invoke_calls = [(call.args[0], call.args[1], call.kwargs) for call in invoke.call_args_list]

        self.assertEqual(fetch_calls, ["https://example.org/accuracy"])
        self.assertEqual([call[0] for call in invoke_calls].count("read_web_page"), 1)
        self.assertIn("duplicate_web_read_skipped", [item.get("code") for item in draft_feedback])

    def test_estimated_context_budget_stops_after_two_decisions_and_latches(self):
        full_page = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        fetch_calls = []
        act_model_calls = []
        act_native_tools = []
        estimated_native_tools = []
        measured_units = []
        schemas = []

        class Model:
            def __call__(self, prompt_id, payload, schema, **kwargs):
                if prompt_id == "runtime/dispatcher":
                    return workflow.DispatcherOutput(needs_research=True, reason="需要网页资料")
                if prompt_id == "literature/background_investigator":
                    if payload["phase"] == "query":
                        return workflow.BackgroundOutput(source="web", queries=["accuracy"])
                    candidate = payload["candidates"][0]
                    return workflow.BackgroundOutput(source="web", selected_candidate_ids=[candidate["candidate_id"]])
                if prompt_id == "literature/planner":
                    return plan(
                        requirements=["读取网页正文"], plan=["逐段读取网页"], source="web", action="research",
                    )
                if prompt_id == "literature/researcher":
                    schemas.append((payload["phase"], schema, kwargs.get("native_tools")))
                    if payload["phase"] == "draft":
                        return researcher_output(
                            draft=draft_text(limitations=[claim(text="读取的页面片段尚未提取为可引用证据。")]),
                        )
                    act_model_calls.append(payload)
                    act_native_tools.append(kwargs.get("native_tools"))
                    candidate_id = payload["current_candidates"][0]["candidate_id"]
                    if len(act_model_calls) > 1:
                        return researcher_output(action="tool", tool_name="search_web", arguments={"query": "another page"})
                    return researcher_output(
                        action="tool", tool_name="read_web_page",
                        arguments={"candidate_id": candidate_id, "limit": 10},
                    )
                if prompt_id == "literature/analyst":
                    return review(sufficient=False, gaps=["頁面片段尚未轉成證據"], review="資料不足。")
                if prompt_id == "literature/reporter":
                    return draft_text(limitations=[claim(text="当前证据不足。")])
                raise AssertionError(prompt_id)

        def fetch(url):
            fetch_calls.append(url)
            return {"url": url, "title": "Accuracy page", "markdown": full_page, "retrieved_at": "2026-11-12T10:00:00+08:00"}

        original_estimator = workflow.structured_input_units

        def measure_actual_input(prompt_id, payload, schema, *, template_context=None, native_tools=None):
            estimated_native_tools.append(native_tools)
            units = original_estimator(
                prompt_id,
                payload,
                schema,
                template_context=template_context,
                native_tools=native_tools,
            )
            measured_units.append(units)
            # Set the cumulative ceiling to the exact rendered-input total at
            # the third researcher decision. Its equality must stop the call.
            if len(measured_units) == 3:
                decision_budget["cumulative_decision_input_units"] = sum(measured_units)
            return units

        stages = []
        invoke_calls = []
        decision_budget = {
            **workflow.DEPTH_BUDGETS["standard"],
            "decision_input_units": 100_000,
            "cumulative_decision_input_units": 100_000,
        }
        with patch.dict(workflow.DEPTH_BUDGETS, {"standard": decision_budget}), \
             patch.object(workflow, "structured_input_units", side_effect=measure_actual_input), \
             patch.object(workflow.registry, "invoke", wraps=workflow.registry.invoke) as invoke:
            content, evidence = self._invoke_run(
                Model(), source="web", query="accuracy", web_results=[sample_web_page(full_page)],
                fetch_page=fetch, on_stage=stages.append,
            )
            invoke_calls = [(call.args[0], call.args[1], call.kwargs) for call in invoke.call_args_list]

        self.assertEqual(len(measured_units), 3)
        self.assertTrue(all(0 < units < 100_000 for units in measured_units))
        self.assertLess(sum(measured_units[:2]), sum(measured_units))
        self.assertEqual(len(act_model_calls), 2)
        self.assertEqual([phase for phase, _, _ in schemas], ["act", "act", "draft"])
        self.assertEqual([schema for phase, schema, _ in schemas if phase == "act"], [workflow.ResearchActionOutput] * 2)
        self.assertTrue(all(schema is None for phase, schema, _ in schemas if phase == "draft"))
        self.assertEqual(act_native_tools, estimated_native_tools[:2])
        self.assertEqual(act_native_tools[0], estimated_native_tools[2])
        self.assertTrue(all(native_tools for native_tools in act_native_tools))
        self.assertIsNone(schemas[-1][2])
        self.assertEqual([name for name, _, _ in invoke_calls].count("read_web_page"), 1)
        self.assertEqual(fetch_calls, ["https://example.org/accuracy"])
        self.assertEqual(stages.count("researcher"), 1)
        self.assertEqual(stages[-1], "reporter")
        self.assertEqual(evidence, [])
        self.assertRegex(content, r"估算上下文预算|证据不足|没有可核查证据")

        latched_state = {
            "research": {"project_id": 1, "query": "accuracy", "source": "web"},
            "research_context_limit_reached": True,
        }
        with patch.object(workflow.registry, "invoke") as invoke:
            with self.assertRaises(workflow.ToolCallValidationError):
                workflow._invoke_tool(
                    latched_state,
                    "web",
                    "read_web_page",
                    {"url": "https://example.org/accuracy", "limit": 10},
                )
            invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
