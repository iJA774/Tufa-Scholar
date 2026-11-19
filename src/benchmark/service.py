"""Shared benchmark commands used by the HTTP API and CLI."""

import os
import subprocess
import time
from hashlib import sha256

from src.benchmark import datasets
from src.benchmark.models import Judge
from src.benchmark.registry import adapter_for
from src.infrastructure import settings
from src.persistence import benchmarks as store
from src.persistence.storage import PROJECT_ROOT


def installation(dataset_id=datasets.DATASET):
    adapter = adapter_for(dataset_id)
    try:
        metadata, _ = adapter.dataset()
    except datasets.BenchmarkError:
        metadata = {**adapter.identity(), "count": None}
    current = settings.public_settings()
    return {"dataset": metadata, "scorer_ready": adapter.scorer_ready(),
            "model": current["model"], "search": current["web_search"],
            "judge": Judge().snapshot(),
            "judge_key_configured": bool(os.environ.get("TUFA_BENCHMARK_JUDGE_API_KEY")),
            "reader_key_configured": bool(os.environ.get("TUFA_BENCHMARK_READER_API_KEY"))}


def code_version():
    def git(*args):
        try:
            result = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True,
                                    timeout=8, check=True, text=True)
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    def fingerprint(directory, extension):
        entries = sorted(PROJECT_ROOT.glob(f"{directory}/**/*.{extension}"))
        digest = sha256()
        for entry in entries:
            digest.update(entry.relative_to(PROJECT_ROOT).as_posix().encode())
            digest.update(b"\0")
            digest.update(entry.read_bytes())
        return digest.hexdigest()

    status = git("status", "--porcelain")
    return {"commit": git("rev-parse", "HEAD"), "dirty": None if status is None else bool(status),
            "agents_sha256": fingerprint("src/agents", "py"),
            "prompts_sha256": fingerprint("src/prompts", "md")}


def judge_snapshot(judge, metrics, saved=None):
    try:
        config = judge.snapshot()
    except ValueError as exc:
        raise datasets.BenchmarkError(str(exc)) from None
    defaults = Judge().snapshot()
    saved = saved or {}
    keys = {}
    for url, field, env in (("base_url", "api_key", "TUFA_BENCHMARK_JUDGE_API_KEY"),
                            ("reader_url", "reader_key", "TUFA_BENCHMARK_READER_API_KEY")):
        provided = getattr(judge, field)
        configured_url = os.environ.get(env.replace("_API_KEY", "_URL"), defaults[url]).rstrip("/")
        env_key = os.environ.get(env) if config[url] == configured_url else None
        old_key = saved.get(field) if saved.get(url) == config[url] else None
        keys[field] = env_key or (provided.get_secret_value() if provided else None) or old_key
        keys[url] = config[url]
    if metrics and not keys["api_key"]:
        raise datasets.BenchmarkError("请配置独立的 Judge API Key")
    if "fact" in metrics and not keys["reader_key"]:
        raise datasets.BenchmarkError("FACT 需要配置网页正文提取服务的密钥")
    config["official_defaults"] = all(config[k] == defaults[k] for k in
                                     ("base_url", "race_model", "fact_model", "clean_model", "reader_url"))
    return config, keys


def selected_metrics(adapter, chosen):
    if set(chosen) - set(adapter.metrics):
        raise datasets.BenchmarkError("数据集不支持所选评分方式")
    return [metric for metric in adapter.metrics if metric in chosen]


def create(request):
    run_id = str(request.request_id)
    existing = store.get_run(run_id)
    if existing:
        return existing
    adapter = adapter_for(request.dataset_id)
    metadata, rows = adapter.dataset()
    sample = request.sample
    if "sample" not in request.model_fields_set and not (request.ids or request.language or request.topic):
        sample = 1
    if request.ids and sample is not None:
        raise datasets.BenchmarkError("指定任务 ID 时请移除随机抽样")
    selected = datasets.select(rows, language=request.language, topic=request.topic,
                               ids=request.ids, sample=sample, seed=request.seed, maximum=request.budget.max_tasks)
    metrics = selected_metrics(adapter, request.metrics)
    if metrics and not adapter.scorer_ready():
        raise datasets.BenchmarkError("请先安装官方评分器")
    try:
        snapshot = settings.benchmark_research_snapshot(request.model_name, request.search_engine)
    except ValueError as exc:
        raise datasets.BenchmarkError(str(exc)) from None
    judge, judge_keys = judge_snapshot(request.judge, metrics)
    research = request.model_dump(include={"source", "research_mode", "research_depth", "reasoning_effort",
                                           "network_search", "prefer_primary_evidence", "relevance_filter"})
    research["model_name"] = snapshot["model"]["model"]
    public = {k: {field: val for field, val in snapshot[k].items() if field != "api_key"}
              for k in ("model", "search")}
    config = {"dataset": metadata, "metrics": metrics, "judge": judge, "research": research,
              "services": public, "software": code_version(), "budget": request.budget.model_dump(),
              "initial_budget": request.budget.model_dump(), "budget_changes": [],
              "selection": {**request.model_dump(include={"language", "topic", "ids", "seed"}), "sample": sample},
              "task_ids": [row["id"] for row in selected], "score_only": None,
              "available_metrics": list(adapter.metrics), "evaluation_commit": metadata["commit"]}
    store.create(run_id, request.name, selected, config, {"research": snapshot, "judge": judge_keys})
    return store.get_run(run_id)


def control(run_id, request):
    run = store.get_run(run_id, private=True)
    if not run:
        raise KeyError(run_id)
    previous = run["config"]
    config = {**previous}
    adapter = adapter_for(previous["dataset"]["id"])
    phases = selected_metrics(adapter, request.metrics)
    credentials = run["credentials"]
    if request.action in ("resume", "retry", "evaluate") and not request.authorize:
        raise datasets.BenchmarkError("请确认预算和外部服务调用")
    if request.judge is not None and request.action != "evaluate":
        raise datasets.BenchmarkError("请通过仅评分入口配置 Judge")
    if request.budget is not None:
        if request.action not in ("resume", "retry", "evaluate"):
            raise datasets.BenchmarkError("请通过继续、重试或评分调整预算")
        if request.budget.max_tasks < len(previous["task_ids"]):
            raise datasets.BenchmarkError("任务数量上限低于批次样本数")
        config["budget"] = request.budget.model_dump()
        config["budget_changes"] = [*previous["budget_changes"], {"at": time.time(), "budget": config["budget"]}]
    if request.action == "evaluate":
        if not phases:
            raise datasets.BenchmarkError("请选择 RACE 或 FACT")
        if not adapter.scorer_ready():
            raise datasets.BenchmarkError("请先安装官方评分器")
        judge = request.judge or Judge(**{k: previous["judge"][k] for k in Judge.model_fields
                                          if k in previous["judge"]})
        settings_snapshot, keys = judge_snapshot(judge, phases, credentials["judge"])
        if settings_snapshot != previous["judge"] and any(row["phase"] in ("race", "fact") for row in store.attempts(run_id)):
            raise datasets.BenchmarkError("此批次已开始评分，请沿用原 Judge 配置")
        config.update(judge=settings_snapshot, metrics=selected_metrics(adapter, previous["metrics"] + phases), score_only=phases)
        credentials = {**credentials, "judge": keys}
    elif request.action == "retry":
        config["score_only"] = phases or None
    elif request.action == "resume" and run["reason"] == "selected_scores_finished":
        config["score_only"] = None
    store.change(run_id, request.action, task_id=request.task_id, phases=phases,
                 config=(previous, config) if config != previous or request.judge is not None else None,
                 credentials=credentials)
    return store.get_run(run_id)
