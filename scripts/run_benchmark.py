"""CLI client for the same local benchmark queue used by the workbench."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


def parser():
    top = argparse.ArgumentParser(description=__doc__)
    top.add_argument("--api", default="http://127.0.0.1:8000", help="Loopback Tufa API")
    sub = top.add_subparsers(dest="command", required=True)
    for action in ("download", "import", "setup-scorer"):
        command = sub.add_parser(action)
        command.add_argument("--dataset", choices=["deepresearch_bench"], default="deepresearch_bench")
        if action == "import":
            command.add_argument("file", type=Path)
    run = sub.add_parser("run")
    run.add_argument("--dataset", choices=["deepresearch_bench"], default="deepresearch_bench")
    run.add_argument("--name", default="DeepResearch Bench")
    run.add_argument("--request-id", default=None, help="Reuse the same UUID to retry a submission")
    run.add_argument("--language", choices=["zh", "en"])
    run.add_argument("--topic")
    scope = run.add_mutually_exclusive_group()
    scope.add_argument("--limit", type=int)
    scope.add_argument("--all", action="store_true")
    scope.add_argument("--ids", type=int, nargs="+")
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--model")
    run.add_argument("--search-engine")
    run.add_argument("--depth", choices=["light", "standard", "deep"], default="standard")
    run.add_argument("--mode", choices=["deep", "auto"], default="deep")
    run.add_argument("--source", choices=["auto", "web", "openalex", "all", "local"], default="auto")
    scores = run.add_mutually_exclusive_group()
    scores.add_argument("--generate-only", action="store_true")
    scores.add_argument("--metrics", nargs="+", choices=["race", "fact"])
    for action in ("evaluate", "resume", "retry", "pause", "skip", "stop", "status", "export"):
        command = sub.add_parser(action)
        command.add_argument("--run-id", required=True)
        if action in ("retry", "skip"):
            command.add_argument("--task-id", type=int, required=action == "skip")
        if action in ("evaluate", "retry"):
            command.add_argument("--metrics", nargs="+", choices=["race", "fact"], default=[])
        if action == "export":
            command.add_argument("--format", choices=["jsonl", "json", "csv", "md", "zip"], default="zip")
            command.add_argument("--output", type=Path)
    for action in ("run", "resume", "retry", "evaluate"):
        command = sub.choices[action]
        command.add_argument("--authorize", action="store_true", required=True,
                             help="Authorize configured external services and the selected budget")
        command.add_argument("--budget", type=Path, help="Optional JSON benchmark budget")
        if action in ("run", "evaluate"):
            command.add_argument("--judge", type=Path, help="Optional JSON Judge settings")
    compare = sub.add_parser("compare")
    compare.add_argument("ids", nargs="+")
    sub.add_parser("history")
    return top


def json_file(path):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path else {}


def execute(client, args):
    command = args.command
    if command == "download":
        return client.post("/benchmark/dataset/download", params={"dataset_id": args.dataset})
    if command == "import":
        with args.file.open("rb") as f:
            return client.post("/benchmark/dataset/import", files={"file": ("query.jsonl", f)}, params={"dataset_id": args.dataset})
    if command == "setup-scorer":
        return client.post("/benchmark/scorer/install", params={"dataset_id": args.dataset}, timeout=900)
    if command == "history":
        return client.get("/benchmark/runs")
    if command == "compare":
        return client.post("/benchmark/compare", json={"ids": args.ids})
    if command == "run":
        limits = json_file(args.budget)
        scope = None if args.all or args.ids or (args.language or args.topic) and args.limit is None else (1 if args.limit is None else args.limit)
        request = {"request_id": args.request_id or str(uuid4()), "name": args.name, "dataset_id": args.dataset,
                   "language": args.language, "topic": args.topic, "ids": args.ids or [], "sample": scope,
                   "seed": args.seed, "model_name": args.model, "search_engine": args.search_engine,
                   "research_mode": args.mode, "research_depth": args.depth, "source": args.source,
                   "metrics": [] if args.generate_only else args.metrics or ["race", "fact"], "authorize": True,
                   "budget": limits, "judge": json_file(args.judge)}
        return client.post("/benchmark/runs", json=request)
    url = f"/benchmark/runs/{args.run_id}"
    if command == "status":
        return client.get(url)
    if command == "export":
        return client.get(url + "/export", params={"format": args.format}, timeout=180)
    request = {"action": command, "task_id": getattr(args, "task_id", None),
               "metrics": getattr(args, "metrics", []) or (["race", "fact"] if command == "evaluate" else []),
               "authorize": getattr(args, "authorize", False)}
    if getattr(args, "budget", None):
        request["budget"] = json_file(args.budget)
    if getattr(args, "judge", None):
        request["judge"] = json_file(args.judge)
    return client.post(url + "/actions", json=request)


def main():
    top = parser()
    args = top.parse_args()
    try:
        origin = urlsplit(args.api)
        if origin.scheme not in ("http", "https") or origin.hostname not in ("localhost", "127.0.0.1", "::1") or origin.username:
            top.error("--api must be a loopback URL")
        with httpx.Client(base_url=args.api, timeout=60, trust_env=False) as client:
            response = execute(client, args)
        if response.is_error:
            top.exit(1, str(response.json().get("detail", "Benchmark API request failed")) + "\n")
        if args.command == "export":
            path = args.output or Path(f"benchmark-{args.run_id}.{args.format}")
            with path.open("xb") as output:
                output.write(response.content)
            print(path.resolve())
        else:
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    except (OSError, ValueError, httpx.HTTPError) as exc:
        top.exit(1, f"Benchmark command failed ({type(exc).__name__}).\n")


if __name__ == "__main__":
    main()
