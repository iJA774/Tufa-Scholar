"""State, isolation, data contracts and export regression tests for DRB v1."""

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient

from apps.api.main import app
from src.agents.report import numbered_references
from src.benchmark import datasets, reports, results
from src.benchmark.evaluator import DIMENSIONS
from src.benchmark.registry import ADAPTERS
from src.infrastructure import settings
from src.persistence import benchmarks as db, repository

ROWS = [
    {"id": 1, "prompt": "First isolated question.\nPreserve this line.", "language": "en", "topic": "Science"},
    {"id": 2, "prompt": "Second isolated question.", "language": "zh", "topic": "Science"},
    {"id": 3, "prompt": "Third isolated question.", "language": "en", "topic": "History"},
]
PAYLOAD = datasets.jsonl(ROWS).encode()


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        work = self.enterContext(tempfile.TemporaryDirectory(prefix="tufa-benchmark-test-"))
        self.enterContext(patch.dict(os.environ, {"TUFA_DATA_DIR": work}, clear=False))
        for variable in (*settings.ENV.values(), "TUFA_BENCHMARK_JUDGE_API_KEY", "TUFA_BENCHMARK_READER_API_KEY"):
            os.environ.pop(variable, None)
        self.enterContext(patch.object(datasets, "QUERY_HASH", hashlib.sha256(PAYLOAD).hexdigest()))
        self.client = self.enterContext(TestClient(app))
        response = self.client.put("/settings", json={"openai_base_url": "http://127.0.0.1:9001/v1",
                                                       "openai_model": "snapshot-model", "openai_api_key": "test-agent-key-123"})
        self.assertEqual(response.status_code, 200)

    def load(self):
        response = self.client.post("/benchmark/dataset/import", files={"file": ("query.jsonl", PAYLOAD)})
        self.assertEqual(response.status_code, 200, response.text)

    def create(self, **changes):
        self.load()
        request = {"request_id": str(uuid4()), "sample": None, "authorize": True, **changes}
        response = self.client.post("/benchmark/runs", json=request)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), request

    def action(self, id, action, expected=200, **body):
        response = self.client.post(f"/benchmark/runs/{id}/actions", json={"action": action, **body})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def start(self):
        item = db.dispatch()
        self.assertIsNotNone(item)
        attempt = db.get_attempt(item[0])
        if attempt["phase"] == "generate":
            repository.claim_task(attempt["research_id"])
        return attempt

    def save(self, attempt):
        evidence = [{"id": "ev1", "title": "Fixture", "original_url": "https://example.org/study?a=1",
                     "text": "observed", "source_level": "web_snippet"}]
        report = numbered_references(r"Observed [[ev1]].", evidence)
        repository.finish_chat_task(attempt["research_id"], report, evidence)
        db.finish(attempt["id"])

    def test_dataset_schema_provenance_selection(self):
        self.load()
        info = self.client.get("/benchmark").json()["dataset"]
        self.assertEqual(info["languages"], {"en": 2, "zh": 1})
        self.assertEqual(self.client.get("/benchmark/dataset/tasks").json(), ROWS)
        for data in (b"not JSONL", PAYLOAD + PAYLOAD, PAYLOAD + b"\n", datasets.jsonl([{**ROWS[0], "id": "1"}]).encode()):
            with self.subTest(data=data[:15]):
                response = self.client.post("/benchmark/dataset/import", files={"file": ("query.jsonl", data)})
                self.assertEqual(response.status_code, 422)
        windows = b"\xef\xbb\xbf" + PAYLOAD.replace(b"\n", b"\r\n")
        self.assertEqual(datasets.install_dataset(windows)["sha256"], hashlib.sha256(windows).hexdigest())
        self.assertEqual(datasets.select(ROWS, language="en", topic="Science"), [ROWS[0]])
        self.assertEqual(datasets.select(ROWS, sample=2, seed=19), datasets.select(ROWS[::-1], sample=2, seed=19))
        with self.assertRaises(datasets.BenchmarkError):
            datasets.select(ROWS, ids=[1], language="zh")

    def test_task_priority_isolation_pause_restart_and_retry(self):
        run, creation = self.create()
        id = run["id"]
        dupe = self.client.post("/benchmark/runs", json=creation)
        self.assertEqual(dupe.json()["id"], id)
        self.assertEqual(len(self.client.get("/benchmark/runs").json()), 1)
        ordinary = repository.create_project("ordinary")["id"]
        normal_task = repository.create_task(ordinary, None, "ordinary question", "local")
        self.assertIsNone(db.dispatch())
        self.assertEqual(repository.claim_task()["id"], normal_task)
        repository.finish_task(normal_task, {})
        self.client.put("/settings", json={"openai_model": "new-default"})
        private = db.get_run(id, private=True)
        self.assertEqual(private["credentials"]["research"]["model"]["model"], "snapshot-model")
        self.assertEqual(self.client.get("/projects").json(), [{"id": ordinary, "name": "ordinary"}])
        first = self.start()
        self.assertEqual(repository.task_history(first["research_id"]), [{"role": "user", "content": ROWS[0]["prompt"]}])
        self.assertEqual(self.action(id, "pause")["status"], "pausing")
        self.save(first)
        self.assertEqual(db.get_run(id)["status"], "paused")
        self.assertIsNone(db.dispatch())
        self.action(id, "resume", authorize=False, expected=422)
        self.action(id, "resume", authorize=True)
        second = self.start()
        self.assertEqual(second["task_id"], ROWS[1]["id"])
        self.assertEqual(repository.task_history(second["research_id"]), [{"role": "user", "content": ROWS[1]["prompt"]}])
        self.assertFalse(repository.get_task(second["research_id"])["local_literature"])
        repository.finish_chat_task(second["research_id"], "Committed before restart", [])
        repository.interrupt_running_tasks()
        db.recover()
        third = self.start()
        self.assertEqual(third["task_id"], 3)
        repository.interrupt_running_tasks()
        db.recover()
        third_again = self.start()
        self.assertEqual(third_again["task_id"], 3)
        self.assertNotEqual(third_again["research_id"], third["research_id"])
        db.finish(third_again["id"], error="timeout")
        self.assertEqual(db.get_run(id)["status"], "partial")
        self.assertEqual(results.snapshot(id)["summary"]["timeout_rate"], 1 / 3)
        self.action(id, "retry", authorize=True)
        self.save(self.start())
        self.assertEqual(db.get_run(id)["status"], "completed")
        self.assertEqual(self.client.get("/conversations").json(), [])
        self.assertEqual(self.client.get(f"/runs/{first['research_id']}").status_code, 404)
        self.assertEqual(self.client.delete(f"/benchmark/runs/{id}").status_code, 204)
        self.assertEqual(self.client.get("/projects").json(), [{"id": ordinary, "name": "ordinary"}])

    def test_budget_lifecycle_scoring_and_coverage(self):
        with patch.dict(ADAPTERS, {datasets.DATASET: replace(ADAPTERS[datasets.DATASET], scorer_ready=lambda: True)}):
            run, _ = self.create(ids=[1, 2], metrics=["race", "fact"],
                                 judge={"api_key": "test-judge-key-987", "reader_key": "test-reader-key-876"},
                                 budget={"max_requests": 1})
            id = run["id"]
            first = self.start()
            budget = run["config"]["budget"]
            call = db.begin_call(first["id"], "agent", "agent_phase", budget)
            self.assertIsNotNone(call)
            self.assertIsNone(db.begin_call(first["id"], "agent", "agent_phase", budget))
            db.end_call(call, .1, 200, {"total_tokens": 50})
            self.save(first)
            self.assertIsNone(db.dispatch())
            self.assertEqual(db.get_run(id)["reason"], "max_requests")
            self.action(id, "skip", task_id=2)
            self.action(id, "resume", authorize=True, budget={**budget, "max_requests": 10})
            race = self.start()
            self.assertEqual(race["phase"], "race")
            db.finish(race["id"], result={"metrics": dict.fromkeys(DIMENSIONS, .6)})
            fact = self.start()
            self.assertEqual(fact["phase"], "fact")
            db.finish(fact["id"], error="reader_service_failed", result={
                "metrics": {"supported": 1, "determinate": 2, "reports_with_extracted_citations": 1}})
            summary = results.snapshot(id)["summary"]
            self.assertEqual(summary["race"]["overall_score"], .6)
            self.assertEqual(summary["fact"]["citation_accuracy"], .5)
            self.assertEqual(summary["fact"]["effective_citations"], 1)
            self.assertEqual(summary["coverage"], {"race": .5, "fact": 0})
            self.assertEqual(summary["model"]["agent"]["usage"]["total_tokens"], 50)
            compact = self.client.get("/benchmark/runs?compact=true").json()[0]["summary"]
            self.assertEqual(compact["race"]["overall_score"], summary["race"]["overall_score"])
            self.assertEqual(compact["fact"]["citation_accuracy"], summary["fact"]["citation_accuracy"])
            historical = results.snapshot(id, history=True)["summary"]
            for key in historical:
                self.assertEqual(historical[key], summary[key], key)
            self.assertIsNone(summary["model"]["agent"]["usage"]["cost"])
            self.action(id, "retry", authorize=True, metrics=["fact"])
            again = self.start()
            self.assertEqual(again["phase"], "fact")
            db.finish(again["id"], result={"metrics": {"supported": 2, "determinate": 2, "reports_with_extracted_citations": 1}})
            self.assertEqual(results.snapshot(id)["summary"]["fact"]["citation_accuracy"], 1)

    def test_scoring_only_respects_selected_phase_and_stops_safely(self):
        with patch.dict(ADAPTERS, {datasets.DATASET: replace(ADAPTERS[datasets.DATASET], scorer_ready=lambda: True)}):
            run, _ = self.create(ids=[1, 2], metrics=["race", "fact"],
                                 judge={"api_key": "test-judge-key", "reader_key": "test-reader-key"})
            id = run["id"]
            first = self.start()
            self.action(id, "pause")
            self.save(first)
            self.action(id, "evaluate", authorize=True, metrics=["race"])
            race = self.start()
            self.assertEqual((race["task_id"], race["phase"]), (1, "race"))
            db.finish(race["id"], result={"metrics": dict.fromkeys(DIMENSIONS, .6)})
            self.assertEqual(db.get_run(id)["reason"], "selected_scores_finished")
            self.assertIsNone(db.dispatch())
            self.action(id, "resume", authorize=True)
            second = self.start()
            self.assertEqual((second["task_id"], second["phase"]), (2, "generate"))
            self.action(id, "stop")
            self.save(second)
            self.assertEqual(db.get_run(id)["status"], "cancelled")
            self.assertIsNone(db.dispatch())
            self.assertEqual([task["fact"] for task in db.task_rows(id)], ["skipped", "skipped"])
            self.assertEqual(results.snapshot(id)["summary"]["race"]["overall_score"], .6)
            self.assertEqual(self.action(id, "evaluate", authorize=True, metrics=["fact"])["status"], "pending")
            self.assertEqual(self.start()["phase"], "fact")

    def test_citation_adapter_and_export(self):
        run, _ = self.create(ids=[1])
        id = run["id"]
        self.save(self.start())
        detail = self.client.get(f"/benchmark/runs/{id}/tasks/1").json()
        report = detail["report"]
        self.assertIn(r"Observed \[1\].", report["content"])
        self.assertIn("Observed [1](https://example.org/study?a=1).", report["adapted"])
        self.assertEqual(report["citation_diagnostics"]["linked"], [1])
        article, diagnostics = reports.adapt(r'Claim \[2\].' + reports.REFERENCE_HEADING + r'- \[2\] Local', [])
        self.assertIn(r"\[2\]", article)
        self.assertEqual(diagnostics["missing_url"], [2])
        for format in ("json", "jsonl", "csv", "md", "zip"):
            response = self.client.get(f"/benchmark/runs/{id}/export?format={format}")
            self.assertEqual(response.status_code, 200, response.text if format != "zip" else "")
            self.assertNotIn(b"test-agent-key-123", response.content)
            if format == "jsonl":
                [record] = datasets.read_jsonl(response.content)
                self.assertEqual(record, {"id": 1, "prompt": ROWS[0]["prompt"], "article": report["adapted"]})
            if format == "zip":
                with ZipFile(io.BytesIO(response.content)) as archive:
                    self.assertEqual(archive.read("reports/1.md").decode(), report["content"])
                    self.assertEqual(archive.read("adapted/1.md").decode(), report["adapted"])
                    self.assertEqual(json.loads(archive.read("evidence/1.json")), report["evidence"])
        other, _ = self.create(ids=[3])
        response = self.client.post("/benchmark/compare", json={"ids": [id, other["id"]]})
        self.assertIn("测试样本不同", response.json()["warnings"])

    def test_history_and_exports_avoid_unneeded_details(self):
        run, _ = self.create(ids=[1, 2, 3], budget={"max_requests": 500})
        run_id = run["id"]
        for _ in range(3):
            attempt = self.start()
            for index in range(40):
                call = db.begin_call(attempt["id"], "agent", "research", run["config"]["budget"])
                db.end_call(call, .1, 200 if index else 500, {"total_tokens": 2})
            self.save(attempt)
        expected = results.snapshot(run_id)
        expected_summary = {key: value for key, value in expected["summary"].items()
                            if key not in ("node_mean_seconds", "language", "topic", "failures")}
        # The list route used by the workbench executes the same bounded set of
        # queries regardless of run count and does not scan events or calls.
        self.create(ids=[1])
        statements = []
        connect = db.connect_database
        @contextmanager
        def traced_connection():
            with connect() as connection:
                connection.set_trace_callback(statements.append)
                yield connection
        with patch.object(db, "connect_database", side_effect=traced_connection), \
             patch.object(results, "snapshot", side_effect=AssertionError("full snapshot loaded")):
            compact = self.client.get("/benchmark/runs?compact=true")
        self.assertEqual(compact.status_code, 200)
        self.assertEqual(len(compact.json()), 2)
        select_sql = [sql.lower() for sql in statements if sql.lstrip().lower().startswith("select")]
        self.assertEqual(len(select_sql), 3)
        self.assertFalse(any("research_events" in sql or "benchmark_calls" in sql for sql in select_sql))
        row = next(row for row in compact.json() if row["id"] == run_id)
        self.assertEqual(row["summary"]["samples"], expected_summary["samples"])
        with patch.object(results, "snapshot", side_effect=AssertionError("full snapshot loaded")), \
             patch.object(results, "activity", side_effect=AssertionError("event details loaded")), \
             patch.object(db, "calls", side_effect=AssertionError("call details loaded")), \
             patch.object(results, "task_detail", side_effect=AssertionError("task detail loaded")):
            statements.clear()
            with patch.object(db, "connect_database", side_effect=traced_connection):
                markdown = self.client.get(f"/benchmark/runs/{run_id}/export?format=md")
            self.assertEqual(markdown.status_code, 200)
            self.assertFalse(any("benchmark_calls" in sql.lower() or "research_events" in sql.lower() for sql in statements))
            statements.clear()
            with patch.object(db, "connect_database", side_effect=traced_connection):
                csv_response = self.client.get(f"/benchmark/runs/{run_id}/export?format=csv")
            self.assertEqual(csv_response.status_code, 200)
            sql = " ".join(statements).lower()
            self.assertNotIn("research_events", sql)
            self.assertNotIn("t.prompt", sql)
            self.assertIn("c.role in ('agent','judge')", sql)
        archive_response = self.client.get(f"/benchmark/runs/{run_id}/export?format=zip")
        with ZipFile(io.BytesIO(archive_response.content)) as archive:
            self.assertEqual(markdown.content, archive.read("summary.md"))
            self.assertEqual(csv_response.content, archive.read("scores.csv"))
        with patch.object(results, "activity", side_effect=AssertionError("event details loaded")), \
             patch.object(db, "calls", side_effect=AssertionError("call details loaded")), \
             patch.object(results, "task_detail", side_effect=AssertionError("task detail loaded")):
            history = self.client.get("/benchmark/runs")
            self.assertEqual(history.status_code, 200)
            self.assertEqual(next(row for row in history.json() if row["id"] == run_id)["summary"], expected_summary)
            markdown = self.client.get(f"/benchmark/runs/{run_id}/export?format=md")
            self.assertEqual(markdown.status_code, 200)
            raw = self.client.get(f"/benchmark/runs/{run_id}/export?format=jsonl")
            self.assertEqual(len(datasets.read_jsonl(raw.content)), 3)
        with patch.object(results, "task_detail", side_effect=AssertionError("task detail loaded")):
            self.assertEqual(self.client.get(f"/benchmark/runs/{run_id}/export?format=csv").status_code, 200)

    def test_invalid_api_input_does_not_echo_credentials(self):
        response = self.client.post("/benchmark/runs", json={"authorize": False,
                                                            "judge": {"api_key": ["never-echo-this"]}})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("never-echo-this", response.text)


if __name__ == "__main__":
    unittest.main()
