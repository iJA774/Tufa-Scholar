"""Benchmark history, coverage statistics and secret-free exports."""

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from io import BytesIO, StringIO
import json
from math import ceil
import re
from statistics import mean, median
from zipfile import ZipFile, ZIP_DEFLATED

from src.benchmark.datasets import jsonl
from src.benchmark.evaluator import DIMENSIONS
from src.benchmark.registry import adapter_for
from src.persistence import benchmarks as store
from src.persistence.storage import connect_database


def redact(value, credentials):
    research, judge = credentials["research"], credentials["judge"]
    secrets = {secret for secret in (research["model"]["api_key"], research["search"]["api_key"],
                                    research["openalex_key"], judge["api_key"], judge["reader_key"]) if secret}
    sensitive = re.compile(r"^(authorization|api[_-]?key|password|secret|token|x-api-key)$", re.I)
    url_secret = re.compile(r"([?&](?:api[_-]?key|token|access_token|password|auth|signature|x-amz-[^= &]+)=)[^&\s)]+", re.I)

    def clean(item):
        if isinstance(item, str):
            for secret in sorted(secrets, key=len, reverse=True):
                item = item.replace(secret, "[redacted]")
            return url_secret.sub(r"\1[redacted]", item)
        if isinstance(item, dict):
            return {clean(key): clean(val) for key, val in item.items() if not sensitive.match(key)}
        if isinstance(item, list):
            return [clean(val) for val in item]
        return item

    return clean(value)


def saved_report(run_id, task_id):
    with connect_database() as db:
        db.row_factory = store.sqlite_row
        row = db.execute("""SELECT r.id,r.content,r.evidence,a.result AS benchmark_result
            FROM benchmark_attempts a JOIN tasks t ON t.id=a.research_id
            JOIN reports r ON r.run_id=t.id WHERE a.run_id=? AND a.task_id=? AND t.status='succeeded'
            ORDER BY a.id DESC LIMIT 1""", (run_id, task_id)).fetchone()
    if row:
        store.decode(row, "evidence", "benchmark_result")
    return row


def activity(research_id, *, summary=False, session=None):
    with store.reader(session) as db:
        kinds = "AND kind IN ('node.started','node.completed','tool.started','tool.failed','report.fallback')" if summary else ""
        rows = db.execute(f"""SELECT id,kind,data FROM research_events WHERE run_id=?
            AND kind NOT IN ('model.delta','model.started','model.completed','model.failed') {kinds} ORDER BY id""",
                          (research_id,)).fetchall()
    return [store.decode(row, "data") for row in rows]


def model_totals(calls):
    totals = {}
    for role in ("agent", "judge", "reader"):
        requests = [call for call in calls if call["role"] == role]
        usage = {}
        coverage = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens", "cost"):
            values = [call["usage"][key] for call in requests
                      if call["usage"] is not None and call["usage"].get(key) is not None]
            usage[key] = sum(values) if values else None
            coverage[key] = len(values)
        totals[role] = {"requests": len(requests), "usage": usage, "usage_coverage": coverage,
                        "errors": sum(not call["status"] or call["status"] >= 400 for call in requests)}
    return totals


def task_stats(attempts, events):
    generation = [row for row in attempts if row["phase"] == "generate"]
    last = generation[-1] if generation else None
    nodes = defaultdict(list)
    spans = {}
    tools = Counter()
    errors = Counter()
    fallback = None
    for event in events:
        kind, data = event["kind"], event["data"]
        if kind == "node.started" and (timestamp := data.get("timestamp")) is not None:
            spans[data["node"]] = timestamp
        elif kind == "node.completed" and data.get("node") in spans and data.get("timestamp") is not None:
            nodes[data["node"]].append(max(0, data["timestamp"] - spans.pop(data["node"])))
        elif kind == "tool.started":
            tools[data.get("tool") or "unknown"] += 1
        elif kind == "tool.failed":
            errors[data.get("tool") or "unknown"] += 1
        elif kind == "report.fallback":
            fallback = data
    report_metrics = (last.get("report_metrics") or {}) if last else {}
    return {"elapsed": last["ended"] - last["started"] if last and last["ended"] is not None else None,
            "fallback": fallback, "nodes": dict(nodes), "tools": dict(tools), "tool_errors": dict(errors),
            "rounds": len(last["subtasks"]) if last and last["subtasks"] is not None else None,
            "report_characters": report_metrics.get("characters"), "source_count": report_metrics.get("sources"),
            "stage": last["stage"] if last else None}


def quality(tasks):
    races = [task["scores"]["race"] for task in tasks if "race" in task["scores"]]
    facts = [task["scores"]["fact"] for task in tasks if "fact" in task["scores"]]
    supported = sum(f["supported"] for f in facts)
    determinate = sum(f["determinate"] for f in facts)
    cited = sum(f["reports_with_extracted_citations"] for f in facts)
    n = len(tasks)
    elapsed = sorted(task["performance"]["elapsed"] for task in tasks if task["performance"]["elapsed"] is not None)
    count = Counter(task["generate"] for task in tasks)
    return {"samples": n, "success": count["succeeded"], "failed": count["failed"], "skipped": count["skipped"],
            "success_rate": count["succeeded"] / n, "failure_rate": count["failed"] / n,
            "race": {key: mean(r[key] for r in races) for key in DIMENSIONS} if races else None,
            "fact": {"citation_accuracy": supported / determinate if determinate else None,
                     "effective_citations": supported / cited if cited else None, "determinate": determinate,
                     "reports_with_extracted_citations": cited} if facts else None,
            "coverage": {phase: sum(task[phase] == "succeeded" for task in tasks) / n for phase in ("race", "fact")},
            "mean_seconds": mean(elapsed) if elapsed else None, "median_seconds": median(elapsed) if elapsed else None,
            "p95_seconds": elapsed[ceil(0.95 * len(elapsed)) - 1] if elapsed else None}


def compact_history():
    runs, counts, score_rows = store.compact_history_rows()
    samples = {row["run_id"]: row["samples"] for row in counts}
    scores = {(row["run_id"], row["phase"]): row for row in score_rows}
    result = []
    for run in runs:
        credentials = run.pop("credentials")
        config = run.pop("config")
        race = scores.get((run["id"], "race")) if "race" in config["available_metrics"] else None
        fact = scores.get((run["id"], "fact")) if "fact" in config["available_metrics"] else None
        run["config"] = {"research": {"model_name": config["research"]["model_name"]}}
        run["summary"] = {"samples": samples.get(run["id"], 0),
                          "race": {"overall_score": race["overall"]} if race else None,
                          "fact": {"citation_accuracy": fact["supported"] / fact["determinate"] if fact["determinate"] else None} if fact else None}
        result.append(redact(run, credentials))
    return result


def projected_export(run_id, *, tokens=False):
    run, tasks, score_rows, usage_rows = store.export_projection(run_id, tokens=tokens)
    scores = {(row["task_id"], row["phase"]): json.loads(row["metrics"]) for row in score_rows}
    usage = {(row["task_id"], row["role"]): row["total_tokens"] for row in usage_rows}
    for task in tasks:
        task["scores"] = {phase: scores[task["task_id"], phase] for phase in run["config"]["available_metrics"]
                          if (task["task_id"], phase) in scores}
        task["performance"] = {"elapsed": task.pop("elapsed")}
        if tokens:
            task["usage"] = {role: {"usage": {"total_tokens": usage.get((task["task_id"], role))}}
                             for role in ("agent", "judge")}
    run["summary"] = quality(tasks)
    run["tasks"] = tasks
    credentials = run.pop("credentials")
    return redact(run, credentials)


def history_snapshot(run_id):
    """Keep the history summary contract without materializing event and call details."""
    with store.reader() as db:
        db.execute("BEGIN")
        run = store.get_run(run_id, private=True, session=db)
        if not run:
            raise KeyError(run_id)
        rows = db.execute("""SELECT t.task_id,
            (SELECT status FROM benchmark_stages s WHERE s.run_id=t.run_id AND s.task_id=t.task_id AND phase='generate') AS generate,
            (SELECT status FROM benchmark_stages s WHERE s.run_id=t.run_id AND s.task_id=t.task_id AND phase='race') AS race,
            (SELECT status FROM benchmark_stages s WHERE s.run_id=t.run_id AND s.task_id=t.task_id AND phase='fact') AS fact,
            (SELECT ended-started FROM benchmark_attempts a WHERE a.run_id=t.run_id AND a.task_id=t.task_id AND phase='generate' ORDER BY id DESC LIMIT 1) AS elapsed,
            (SELECT error FROM benchmark_attempts a WHERE a.run_id=t.run_id AND a.task_id=t.task_id AND phase='generate' AND ended IS NOT NULL ORDER BY id DESC LIMIT 1) AS error,
            EXISTS(SELECT 1 FROM research_events e WHERE e.run_id=(SELECT research_id FROM benchmark_attempts a WHERE a.run_id=t.run_id AND a.task_id=t.task_id AND phase='generate' ORDER BY id DESC LIMIT 1) AND e.kind='report.fallback') AS fallback
            FROM benchmark_tasks t WHERE t.run_id=? ORDER BY t.position""", (run_id,)).fetchall()
        score_rows = db.execute("""SELECT task_id,phase,json_extract(result,'$.metrics') AS metrics
            FROM benchmark_attempts a WHERE run_id=? AND phase<>'generate' AND result IS NOT NULL
            AND id=(SELECT MAX(a2.id) FROM benchmark_attempts a2 WHERE a2.run_id=a.run_id
                    AND a2.task_id=a.task_id AND a2.phase=a.phase AND a2.result IS NOT NULL) ORDER BY id""", (run_id,)).fetchall()
        attempt_summary = db.execute("""SELECT COALESCE(SUM(COALESCE(ended, ?)-started),0) AS elapsed,
            COUNT(*) AS count, COUNT(DISTINCT task_id || ':' || phase) AS unique_count
            FROM benchmark_attempts WHERE run_id=?""", (datetime.now(timezone.utc).timestamp(), run_id)).fetchone()
        keys = ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens", "cost")
        aggregates = ", ".join(f"SUM(json_extract(c.usage, '$.{key}')) AS {key}, COUNT(json_extract(c.usage, '$.{key}')) AS {key}_coverage" for key in keys)
        calls = db.execute(f"""SELECT c.role, COUNT(*) AS requests,
            SUM(CASE WHEN c.status IS NULL OR c.status=0 OR c.status>=400 THEN 1 ELSE 0 END) AS errors,
            SUM(CASE WHEN c.role='retrieval' AND c.status IS NULL THEN 1 ELSE 0 END) AS search_errors,
            {aggregates} FROM benchmark_calls c JOIN benchmark_attempts a ON a.id=c.attempt_id
            WHERE a.run_id=? GROUP BY c.role""", (run_id,)).fetchall()
    credentials = run.pop("credentials")
    scores = {(row["task_id"], row["phase"]): json.loads(row["metrics"]) for row in score_rows}
    tasks = [{**row, "scores": {phase: scores[row["task_id"], phase] for phase in run["config"]["available_metrics"]
                              if (row["task_id"], phase) in scores},
              "performance": {"elapsed": row["elapsed"]}} for row in rows]
    summary = quality(tasks)
    call_by_role = {row["role"]: row for row in calls}
    model = {}
    for role in ("agent", "judge", "reader"):
        row = call_by_role.get(role)
        model[role] = {"requests": row["requests"] if row else 0,
                       "errors": row["errors"] if row else 0,
                       "usage": {key: row[key] if row else None for key in keys},
                       "usage_coverage": {key: row[key + "_coverage"] if row else 0 for key in keys}}
    summary.update(elapsed_seconds=attempt_summary["elapsed"],
                   timeout_rate=sum(row["error"] == "timeout" for row in rows) / len(rows),
                   fallback_rate=sum(row["fallback"] for row in rows) / len(rows),
                   retries=attempt_summary["count"] - attempt_summary["unique_count"],
                   model=model, search_errors=sum(row["search_errors"] for row in calls))
    run["summary"] = summary
    return redact(run, credentials)


def snapshot(run_id, history=False):
    if history:
        return history_snapshot(run_id)
    with store.reader() as db:
        db.execute("BEGIN")
        run = store.get_run(run_id, private=True, session=db)
        if not run:
            raise KeyError(run_id)
        attempts = store.attempts(run_id, report_metrics=True, session=db)
        model_calls = store.calls(run_id, session=db)
        tasks = store.task_rows(run_id, session=db)
        scores = store.scores(run_id, session=db)
        latest_generation = {row["task_id"]: row["research_id"] for row in attempts if row["phase"] == "generate"}
        activity_by_task = {task_id: activity(research_id, summary=True, session=db)
                            for task_id, research_id in latest_generation.items()}

    credentials = run.pop("credentials")
    by_task = defaultdict(list)
    for attempt in attempts:
        by_task[attempt["task_id"]].append(attempt)
    calls_by_task = defaultdict(list)
    for call in model_calls:
        calls_by_task[call["task_id"]].append(call)
    node_times = defaultdict(list)
    for task in tasks:
        task_attempts = by_task[task["task_id"]]
        task["performance"] = task_stats(task_attempts, activity_by_task.get(task["task_id"], []))
        for node, durations in task["performance"]["nodes"].items():
            node_times[node].extend(durations)
        task["scores"] = {phase: scores[task["task_id"], phase] for phase in run["config"]["available_metrics"]
                          if (task["task_id"], phase) in scores}
        request_rows = calls_by_task[task["task_id"]]
        task["usage"] = model_totals(request_rows)
        task["performance"]["retrieval"] = dict(Counter(c["phase"] for c in request_rows if c["role"] == "retrieval"))
        task["errors"] = {a["phase"]: a["error"] for a in task_attempts if a["ended"] is not None}
    summary = quality(tasks)
    durations = [(a["ended"] if a["ended"] is not None else datetime.now(timezone.utc).timestamp()) - a["started"]
                 for a in attempts]
    run.update(summary=summary)
    summary.update(elapsed_seconds=sum(durations),
                   timeout_rate=sum(t["errors"].get("generate") == "timeout" for t in tasks) / len(tasks),
                   fallback_rate=sum(bool(t["performance"]["fallback"]) for t in tasks) / len(tasks),
                   retries=len(attempts) - len({(a["task_id"], a["phase"]) for a in attempts}),
                   model=model_totals(model_calls),
                   search_errors=sum(c["role"] == "retrieval" and c["status"] is None for c in model_calls))
    if not history:
        run["tasks"] = tasks
        summary["node_mean_seconds"] = {node: mean(times) for node, times in node_times.items()}
        for dimension in ("language", "topic"):
            groups = defaultdict(list)
            for task in tasks:
                groups[task[dimension]].append(task)
            summary[dimension] = {key: quality(group) for key, group in sorted(groups.items())}
        summary["failures"] = dict(Counter(err for t in tasks for err in t["errors"].values() if err))
        run["activity"] = [{k: row[k] for k in ("task_id", "phase", "status", "stage", "started", "ended", "error")}
                           for row in reversed(attempts[-40:])]
    return redact(run, credentials)


def task_detail(run_id, task_id):
    run = store.get_run(run_id, private=True)
    task = next((t for t in store.task_rows(run_id) if t["task_id"] == task_id), None)
    if not run or not task:
        raise KeyError(task_id)
    attempts = store.attempts(run_id, task_id, details=True)
    report = saved_report(run_id, task_id)
    if report:
        adapter = adapter_for(run["config"]["dataset"]["id"])
        adapted, diagnostics = adapter.report_record(task, report)
        cached = report.pop("benchmark_result")
        report.update(adapted=cached["article"] if cached else adapted["article"],
                      citation_diagnostics=cached["citation_diagnostics"] if cached else diagnostics)
    return redact({"task": task, "report": report, "attempts": [a for a in attempts if a["phase"] != "generate"],
                   "research_attempts": [{**{k: v for k, v in a.items() if k != "result"},
                                          "activity": activity(a["research_id"])}
                                         for a in attempts if a["phase"] == "generate"],
                   "model_calls": store.calls(run_id, task_id)}, run["credentials"])


def compare(ids):
    runs = [snapshot(run_id, history=True) for run_id in ids]
    warnings = []
    first = runs[0]["config"]
    first_coverage = runs[0]["summary"]["coverage"]
    if any(first_coverage[p] < 1 for p in first["metrics"]):
        warnings.append("评分覆盖率不同或存在未评分样本")
    for other in runs[1:]:
        config = other["config"]
        checks = (("dataset", ("id", "version", "commit", "canonical_sha256"), "数据集版本不同"),
                  ("judge", None, "Judge 配置不同"))
        for key, fields, message in checks:
            a, b = first[key], config[key]
            if ((a != b) if fields is None else any(a[field] != b[field] for field in fields)):
                warnings.append(message)
        if first["task_ids"] != config["task_ids"]:
            warnings.append("测试样本不同")
        if first["evaluation_commit"] != config["evaluation_commit"] or first["metrics"] != config["metrics"]:
            warnings.append("评分版本或方式不同")
        coverage = other["summary"]["coverage"]
        if coverage != first_coverage or any(coverage[p] < 1 for p in config["metrics"]):
            warnings.append("评分覆盖率不同或存在未评分样本")
    return {"runs": runs, "warnings": list(dict.fromkeys(warnings))}


def raw_records(run_id):
    with store.reader() as db:
        db.execute("BEGIN")
        run = store.get_run(run_id, private=True, session=db)
        if not run:
            raise KeyError(run_id)
        tasks = store.task_rows(run_id, session=db)
        rows = db.execute("""SELECT a.task_id,r.content,r.evidence,a.result AS benchmark_result
            FROM benchmark_attempts a JOIN tasks t ON t.id=a.research_id
            JOIN reports r ON r.run_id=t.id WHERE a.run_id=? AND t.status='succeeded'
            AND a.id=(SELECT MAX(a2.id) FROM benchmark_attempts a2 JOIN tasks t2 ON t2.id=a2.research_id
                      JOIN reports r2 ON r2.run_id=t2.id WHERE a2.run_id=a.run_id AND a2.task_id=a.task_id
                      AND t2.status='succeeded')""", (run_id,)).fetchall()
    reports = {row["task_id"]: store.decode(row, "evidence", "benchmark_result") for row in rows}
    adapter = adapter_for(run["config"]["dataset"]["id"])
    records = []
    for task in tasks:
        report = reports.get(task["task_id"])
        if report:
            cached = report["benchmark_result"]
            article = cached["article"] if cached else adapter.report_record(task, report)[0]["article"]
            records.append({"id": task["task_id"], "prompt": task["prompt"], "article": article})
    return redact(records, run["credentials"])


def exports(run_id, kind):
    if kind == "jsonl":
        return "application/x-ndjson", jsonl(raw_records(run_id)).encode()
    run = projected_export(run_id, tokens=(kind == "csv")) if kind in ("md", "csv") else snapshot(run_id)
    if kind in ("json", "zip"):
        details = [task_detail(run_id, task["task_id"]) for task in run["tasks"]]
        records = [{"id": item["task"]["task_id"], "prompt": item["task"]["prompt"], "article": item["report"]["adapted"]}
                   for item in details if item["report"] is not None]
        run["details"] = details
        full = json.dumps(run, ensure_ascii=False, indent=2, allow_nan=False).encode()
        if kind == "json":
            return "application/json", full
    metadata = run["config"]
    created = datetime.fromtimestamp(run["created"], timezone.utc).isoformat()
    summary = run["summary"]
    metrics = {"样本": summary["samples"], "成功率": summary["success_rate"],
               "RACE 覆盖率": summary["coverage"]["race"], "FACT 覆盖率": summary["coverage"]["fact"],
               "RACE Overall": (summary["race"] or {}).get("overall_score"),
               "Citation Accuracy": (summary["fact"] or {}).get("citation_accuracy"),
               "Effective Citations": (summary["fact"] or {}).get("effective_citations")}
    markdown = ["# DeepResearch Bench", "", f"- ID · {run_id}", f"- UTC · {created}", f"- 状态 · {run['status']}",
                *[f"- {key} · {value if value is not None else 'N/A'}" for key, value in metrics.items()],
                "", "## 配置", "", "````json", json.dumps(metadata, ensure_ascii=False, indent=2), "````"]
    md = "\n".join(markdown).encode()
    if kind == "md":
        return "text/markdown; charset=utf-8", md
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["run_id", "created_utc", "dataset_version", "dataset_commit", "selection", "config",
                     "task_id", "language", "topic", "generate", "race", "fact", "elapsed_seconds", *DIMENSIONS,
                     "supported", "determinate", "agent_tokens", "judge_tokens"])
    for task in run["tasks"]:
        scores = task["scores"]
        writer.writerow([run_id, created, metadata["dataset"]["version"], metadata["dataset"]["commit"],
                         json.dumps(metadata["task_ids"]), json.dumps(metadata, ensure_ascii=False),
                         task["task_id"], task["language"], task["topic"], *[task[k] for k in store.run_phases(metadata)],
                         task["performance"]["elapsed"], *[(scores.get("race") or {}).get(key) for key in DIMENSIONS],
                         *[(scores.get("fact") or {}).get(key) for key in ("supported", "determinate")],
                         *[task["usage"][key]["usage"]["total_tokens"] for key in ("agent", "judge")]])
    data = output.getvalue().encode("utf-8-sig")
    if kind == "csv":
        return "text/csv; charset=utf-8", data
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        archive.writestr("summary.md", md)
        archive.writestr("results.json", full)
        archive.writestr("scores.csv", data)
        archive.writestr("raw_results.jsonl", jsonl(records))
        for detail in details:
            task_id = detail["task"]["task_id"]
            report = detail["report"]
            if report:
                archive.writestr(f"reports/{task_id}.md", report["content"])
                archive.writestr(f"adapted/{task_id}.md", report["adapted"])
                archive.writestr(f"evidence/{task_id}.json", json.dumps(report["evidence"], ensure_ascii=False, indent=2))
    return "application/zip", out.getvalue()
