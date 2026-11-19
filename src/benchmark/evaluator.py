"""Execute pinned upstream DRB algorithms inside a short-lived scoring process."""

from contextlib import redirect_stderr, redirect_stdout
import importlib
import json
import logging
from math import isfinite
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import time
import sys
from urllib.parse import urlsplit

import httpx

from src.benchmark import datasets
from src.benchmark.reports import LINK
from src.integrations.web_page import _target, WebPageError

DIMENSIONS = ("comprehensiveness", "insight", "instruction_following", "readability", "overall_score")


class ScoringFailure(Exception):
    pass


class Transport:
    def __init__(self, config, keys, monitor):
        self.config = config
        self.keys = keys
        self.monitor = monitor
        self.lock = Lock()
        self.reasoning = []
        self.errors = []

    def generate(self, user_prompt, system_prompt="", *, model=None, return_metadata=False, stage=None):
        stage = stage or "score"
        model_field = {"score": "race_model", "clean": "clean_model", "fact": "fact_model"}[stage]
        model = model or self.config[model_field]
        messages = [{"role": "user", "content": user_prompt}]
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})
        payload = {"model": model, "messages": messages, "max_completion_tokens": 64000,
                   "reasoning_effort": "medium" if stage == "score" else "low"}
        for i in range(self.config["retries"] + 1):
            with self.lock:
                response = None
                call_id = self.monitor.begin("judge", stage)
                started = time.monotonic()
                usage = None
                try:
                    response = httpx.post(self.config["base_url"] + "/chat/completions", json=payload,
                                          headers={"Authorization": "Bearer " + self.keys["api_key"]},
                                          timeout=self.config["timeout"])
                    response.raise_for_status()
                    data = response.json()
                    usage = data.get("usage")
                    choice = data["choices"][0]
                    content = choice["message"]["content"] or ""
                    finish_reason = choice.get("finish_reason", "stop")
                    if not isinstance(content, str):
                        raise ValueError
                    if stage == "score":
                        self.reasoning.append(content)
                    return (content, finish_reason) if return_metadata else content
                except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError):
                    status = response.status_code if response is not None else None
                    if status not in (429, 502, 503, 504) or i >= self.config["retries"]:
                        self.errors.append("judge_service_failed")
                        raise ScoringFailure("judge_service_failed") from None
                finally:
                    self.monitor.end(call_id, started, response.status_code if response is not None else None, usage)
            time.sleep(1.5 ** (i + 1))
        raise ScoringFailure("judge_service_failed")

    def fact_model(self, prompt):
        return self.generate(prompt, stage="fact")

    def read(self, url):
        try:
            target = urlsplit(url)
            if target.username or target.password or not target.hostname or target.scheme not in ("https", "http"):
                return {"error": "invalid_url"}
            _target(url)
        except ValueError:
            return {"error": "invalid_url"}
        except WebPageError as exc:
            return {"error": "inaccessible" if exc.category == "dns_error" else "invalid_url"}
        call_id = self.monitor.begin("reader", "scrape")
        started = time.monotonic()
        response = None
        try:
            auth = self.keys["reader_key"]
            if not auth.lower().startswith("bearer "):
                auth = "Bearer " + auth
            response = httpx.get(self.config["reader_url"] + "/" + url,
                                 headers={"Authorization": auth, "Accept": "application/json",
                                          "X-Timeout": str(self.config["timeout"] * 1000),
                                          "X-With-Generated-Alt": "true"},
                                 timeout=self.config["timeout"])
            if response.status_code in (404, 410, 451):
                return {"error": "inaccessible"}
            response.raise_for_status()
            data = response.json()["data"]
            if not isinstance(data.get("content"), str) or not data["content"].strip():
                return {"error": "inaccessible"}
            return {k: data.get(k) or "" for k in ("title", "description", "content")}
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            return {"error": "reader_service_failed"}
        finally:
            self.monitor.end(call_id, started, response.status_code if response is not None else None, None)


class Progress:
    def update(self, _):
        pass


def race(root, task, row, transport):
    upstream = importlib.import_module("deepresearch_bench_race")
    criteria = {r["prompt"]: r for r in datasets.read_jsonl((root / datasets.CRITERIA).read_bytes())
                if r.get("prompt") == row["prompt"]}
    references = {r["prompt"]: r for r in datasets.read_jsonl((root / datasets.REFERENCE).read_bytes())
                  if r.get("prompt") == row["prompt"]}
    if not criteria or not references:
        raise ScoringFailure("missing_official_reference")
    cleaned = upstream.ArticleCleaner(transport).clean_single(row, max_retries=1, language=task["language"])
    if not cleaned or "article" not in cleaned:
        raise ScoringFailure("judge_service_failed" if transport.errors else "cleaning_failed")
    result = upstream.process_single_item(row, {row["prompt"]: cleaned}, references, criteria,
                                          transport, Lock(), Progress(), 1, task["language"])
    if "error" in result or any(not isfinite(result[key]) for key in DIMENSIONS):
        raise ScoringFailure("judge_service_failed" if transport.errors else "race_response_invalid")
    reasons = upstream.extract_json_from_markdown(transport.reasoning[-1])
    return {"metrics": {key: result[key] for key in DIMENSIONS},
            "reasoning": json.loads(reasons),
            "cleaned_article": cleaned["article"]}


def fact(task, row, diagnostics, transport):
    extract = importlib.import_module("utils.extract")
    dedup = importlib.import_module("utils.deduplicate")
    scrape = importlib.import_module("utils.scrape")
    validate = importlib.import_module("utils.validate")
    extract.call_model = dedup.call_model = validate.call_model = transport.fact_model
    scrape.scrape_url = transport.read
    language = {row["id"]: task["language"]}
    with TemporaryDirectory(prefix="fact-", dir=datasets.root()) as directory:
        output = Path(directory)
        path = output / "extracted.jsonl"
        extract.run([row.copy()], str(path), language)
        extracted = datasets.read_jsonl(path.read_bytes()) if path.is_file() else []
        if not extracted or not isinstance(extracted[0].get("citations"), list):
            raise ScoringFailure("judge_service_failed" if transport.errors else "fact_extraction_failed")
        citations = extracted[0]["citations"]
        if any(not isinstance(c, dict) or not isinstance(c.get("url"), str) or not isinstance(c.get("fact"), str)
               for c in citations):
            raise ScoringFailure("fact_response_invalid")
        path = output / "deduplicated.jsonl"
        dedup.run(extracted, str(path), language)
        groups = datasets.read_jsonl(path.read_bytes())[0]["citations_deduped"]

    verified = []
    supported = determinate = 0
    had_service_error = bool(transport.errors)
    report_urls = set(LINK.findall(row["article"]))
    for url, group in groups.items():
        facts = group["facts"]
        if url not in report_urls:
            try:
                parts = urlsplit(url)
                missing = "unmatched_citation" if parts.hostname and parts.scheme in ("http", "https") else "invalid_url"
            except ValueError:
                missing = "invalid_url"
            verified.extend({"url": url, "fact": claim, "status": missing} for claim in facts)
            continue
        result = scrape.scrape(url)
        reference = result["url_content"]
        if reference.startswith("scrape failed: "):
            state = reference.removeprefix("scrape failed: ")
            had_service_error |= state == "reader_service_failed"
            verified.extend({"url": url, "fact": claim, "status": state} for claim in facts)
            continue
        group.update(url_content=reference, article_id=row["id"])
        result = validate.validate((url, group), language)
        verdicts = result["validate_res"]
        valid = (not result["error"]
                 and all(type(v.get("idx")) is int and v.get("result") in ("supported", "unsupported", "unknown")
                         for v in verdicts)
                 and sorted(v["idx"] for v in verdicts) == list(range(len(facts))))
        if not valid:
            had_service_error = True
            verified.extend({"url": url, "fact": claim, "status": "judge_validation_failed"} for claim in facts)
            continue
        for verdict in verdicts:
            status = verdict["result"]
            supported += status == "supported"
            determinate += status != "unknown"
            verified.append({"url": url, "fact": facts[verdict["idx"]], "status": status})

    # utils/stat.py excludes empty citations from its report denominator, and
    # excludes validation errors and unknown verdicts from citation accuracy.
    result = {"metrics": {"supported": supported, "determinate": determinate,
                          "reports_with_extracted_citations": int(bool(citations))},
              "details": verified, "missing_url": diagnostics["missing_url"],
              "extracted_citations": len(citations)}
    if had_service_error:
        result["error"] = "fact_service_failed"
    return result


def evaluate(phase, task, row, diagnostics, config, keys, monitor):
    if not datasets.scorer_ready(verify=True):
        raise ScoringFailure("scorer_missing_or_invalid")
    root = datasets.root() / "official"
    transport = Transport(config, keys, monitor)
    # Imports and hooks are scoped to a fresh, single-attempt Python process.
    # Discard upstream text logs, which may contain raw report/provider text.
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    # The upstream utility module reads these at import time even though its
    # HTTP methods are replaced by the batch-scoped Transport.
    os.environ.update(LLM_BACKEND="openrouter", MAX_OUTPUT_TOKENS="64000", LLM_HTTP_TIMEOUT=str(config["timeout"]))
    logging.disable(logging.CRITICAL)
    with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
        if phase == "race":
            return race(root, task, row, transport)
        return fact(task, row, diagnostics, transport)
