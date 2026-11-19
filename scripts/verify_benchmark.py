"""Exercise API, worker and both pinned DRB scorers with loopback services."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from scripts.verification_support import ROOT, FixtureHandler, FixtureHTTPServer, api_commands, assert_isolated_services, free_port, stop_processes
from src.benchmark import datasets
from src.infrastructure.settings import ENV

CITATION = "https://93.184.216.34/fixture"
DIMS = ("comprehensiveness", "insight", "instruction_following", "readability")
FIXTURE_JUDGE = "benchmark-judge-test-key"
FIXTURE_READER = "benchmark-reader-test-key"


class BenchmarkHandler(FixtureHandler):
    def do_GET(self):
        if self.path.startswith("/reader/"):
            assert self.headers["Authorization"] == f"Bearer {FIXTURE_READER}"
            if self.server.fail_reader:
                self._send_json({}, 503)
            else:
                self._send_json({"data": {"title": "Study", "content": "Accuracy was 92% in the simulated search result."}})
        elif urlsplit(self.path).path == "/search":
            self.server.search_requests.append(self.path)
            self._send_json({"results": [{"title": "Study on accuracy", "url": CITATION, "content": "Accuracy was 92%."}]})
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/judge/chat/completions":
            assert self.headers["Authorization"] == f"Bearer {FIXTURE_JUDGE}"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            model, text = body["model"], body["messages"][-1]["content"]
            self.server.judge_requests.append(model)
            if model == "fixture-clean":
                value = "Synthetic cleaned research report with the observed accuracy result. " * 5
            elif model == "fixture-race":
                value = json.dumps({dim: [{"criterion": "fixture criterion", "article_1_score": 6,
                                          "article_2_score": 4, "reason": "fixture response"}] for dim in DIMS})
            elif model == "fixture-fact":
                if f"[1]({CITATION})" in text:
                    value = json.dumps([{"url": CITATION, "fact": "Accuracy was 92%."}] * 2)
                elif "de-duplicate" in text:
                    self.server.deduplication_calls += 1
                    value = "[1]"
                else:
                    value = json.dumps([{"idx": 1, "result": "supported"}])
            else:
                raise AssertionError("Unexpected Judge model")
            self._send_json({"choices": [{"message": {"content": value}, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 45, "completion_tokens": 20, "total_tokens": 65}})
            return
        barrier = self.server.barrier
        if barrier is not None:
            self.server.barrier = None
            reached, release = barrier
            reached.set()
            release.wait(15)
        try:
            super().do_POST()
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    def _model_result(role, payload, system=""):
        if role == "background_investigator" and payload.get("phase") == "query":
            clean = {**payload, "question": "study accuracy"}
            return FixtureHandler._model_result(role, clean, system)
        return FixtureHandler._model_result(role, payload, system)


def wait_for(read, condition, seconds=35):
    deadline = time.monotonic() + seconds
    value = None
    while time.monotonic() < deadline:
        try:
            value = read()
            if condition(value):
                return value
        except httpx.ConnectError:
            pass
        time.sleep(.15)
    raise AssertionError(f"Timed out; last status={value.get('status') if isinstance(value, dict) else None}")


def verify(assets):
    with TemporaryDirectory(prefix="tufa-verify-benchmark-") as directory, open(os.devnull, "wb") as sink:
        fixture = FixtureHTTPServer(("127.0.0.1", 0))
        fixture.RequestHandlerClass = BenchmarkHandler
        fixture.barrier = None
        fixture.fail_reader = False
        fixture.judge_requests = []
        fixture.deduplication_calls = 0
        thread = Thread(target=fixture.serve_forever, daemon=True)
        thread.start()
        running = []
        try:
            root = Path(directory) / "benchmarks" / datasets.DATASET / datasets.COMMIT
            shutil.copytree(assets / "official", root / "official")
            env = os.environ.copy()
            for var in (*ENV.values(), "TUFA_BENCHMARK_JUDGE_API_KEY", "TUFA_BENCHMARK_READER_API_KEY"):
                env.pop(var, None)
            env["TUFA_DATA_DIR"] = directory
            port = free_port()
            api_command, worker_command = api_commands(port)
            api_url, fixture_url = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{fixture.server_port}"

            def start(command):
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=sink, stderr=sink)
                running.append(process)
                return process

            start(api_command)
            worker = start(worker_command)
            with httpx.Client(base_url=api_url, trust_env=False, timeout=30) as client:
                wait_for(lambda: client.get("/health"), lambda response: response.is_success)
                assert_isolated_services(client, running, directory)

                def call(method, url, **kwargs):
                    response = client.request(method, url, **kwargs)
                    response.raise_for_status()
                    return response.json()

                call("PUT", "/settings", json={"openai_base_url": fixture_url + "/v1", "openai_model": "fixture-agent",
                                               "web_search_engine": "searx", "searx_base_url": fixture_url})
                original = (assets / "queries.jsonl").read_bytes()
                imported = call("POST", "/benchmark/dataset/import", files={"file": ("query.jsonl", original)})
                assert imported["count"] == 100
                ids = [51, 52]
                request = {"request_id": str(uuid4()), "sample": None, "ids": ids, "source": "web",
                           "research_depth": "light", "authorize": True}
                first = (Event(), Event())
                fixture.barrier = first
                run = call("POST", "/benchmark/runs", json=request)
                run_url = "/benchmark/runs/" + run["id"]

                def state(status):
                    return wait_for(lambda: call("GET", run_url), lambda value: value["status"] == status)

                def action(name, **extra):
                    return call("POST", run_url + "/actions", json={"action": name, **extra})

                assert first[0].wait(15), "Generation did not reach the Agent model"
                assert call("GET", run_url)["status"] == "generating"
                assert action("pause")["status"] == "pausing"
                first[1].set()
                assert state("paused")["summary"]["success"] == 1
                first_report = call("GET", run_url + "/tasks/51")["report"]
                assert first_report["evidence"] and f"[1]({CITATION})" in first_report["adapted"]
                assert call("POST", "/benchmark/runs", json=request)["id"] == run["id"]

                call("PUT", "/settings", json={"openai_model": "unexpected-model"})
                second = (Event(), Event())
                fixture.barrier = second
                action("resume", authorize=True)
                assert second[0].wait(15)
                stop_processes([worker])
                second[1].set()
                worker = start(worker_command)
                completed = state("completed")
                assert completed["summary"]["success"] == 2
                assert completed["summary"]["retries"] == 1
                before_scoring = len(fixture.model_requests)
                assert before_scoring >= 2 and all(row["model"] == "fixture-agent" for row in fixture.model_requests)
                assert call("GET", run_url + "/tasks/51")["report"] == first_report
                second_report = call("GET", run_url + "/tasks/52")
                assert any(a["status"] == "interrupted" for a in second_report["research_attempts"])
                assert call("GET", "/projects") == [] and call("GET", "/conversations") == []
                assert {row["id"] for row in datasets.read_jsonl(client.get(run_url + "/export?format=jsonl").content)} == set(ids)

                profile = {"base_url": fixture_url + "/judge", "reader_url": fixture_url + "/reader",
                           "race_model": "fixture-race", "clean_model": "fixture-clean", "fact_model": "fixture-fact",
                           "api_key": FIXTURE_JUDGE, "reader_key": FIXTURE_READER, "retries": 0}
                action("evaluate", authorize=True, metrics=["race"], judge=profile)
                race = state("completed")
                assert race["summary"]["coverage"] == {"race": 1, "fact": 0}, race["activity"]
                assert abs(race["summary"]["race"]["overall_score"] - .6) < 1e-8
                assert "fixture-clean" in fixture.judge_requests
                fixture.fail_reader = True
                action("evaluate", authorize=True, metrics=["fact"])
                partial = state("partial")
                assert partial["summary"]["coverage"] == {"race": 1, "fact": 0}, partial["activity"]
                failed = call("GET", run_url + "/tasks/51")["attempts"]
                fact = next(item for item in failed if item["phase"] == "fact")
                assert fact["result"]["details"][0]["status"] == "reader_service_failed"
                fixture.fail_reader = False
                action("retry", authorize=True, metrics=["fact"])
                scored = state("completed")
                assert scored["summary"]["coverage"] == {"race": 1, "fact": 1}, scored["activity"]
                assert scored["summary"]["fact"]["citation_accuracy"] == 1
                assert scored["summary"]["fact"]["effective_citations"] == 1
                assert fixture.deduplication_calls >= 2
                assert len(fixture.model_requests) == before_scoring
                response = client.get(run_url + "/export?format=json")
                response.raise_for_status()
                assert FIXTURE_JUDGE not in response.text and FIXTURE_READER not in response.text

                path = Path(directory) / "official-format.jsonl"
                subprocess.run([sys.executable, "-m", "scripts.run_benchmark", "--api", api_url,
                                "export", "--run-id", run["id"], "--format", "jsonl", "--output", str(path)],
                               cwd=ROOT, env=env, check=True, stdout=sink, timeout=15)
                assert len(datasets.read_jsonl(path.read_bytes())) == 2
        except Exception:
            for component in ("api", "worker"):
                log = Path(directory) / "logs" / f"{component}.log"
                if log.is_file():
                    entries = log.read_text(encoding="utf-8", errors="replace").splitlines()
                    print("\n".join(line for line in entries if " ERROR " in line)[-4000:], file=sys.stderr)
            raise
        finally:
            stop_processes(running)
            fixture.shutdown()
            fixture.server_close()
            thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, help="Verified DRB commit directory containing queries.jsonl and official/")
    args = parser.parse_args()
    if args.assets:
        verify(args.assets.resolve())
    else:
        with TemporaryDirectory(prefix="tufa-drb-verification-assets-") as directory:
            from unittest.mock import patch
            with patch.dict(os.environ, {"TUFA_DATA_DIR": directory}):
                datasets.install_dataset()
                datasets.install_scorer()
                verify(datasets.root())
    print("Pinned DRB v1, API, Worker, restart, RACE/FACT, score-only retry and CLI export passed with local fixtures.")


if __name__ == "__main__":
    main()
