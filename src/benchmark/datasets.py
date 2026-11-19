"""Pinned DeepResearch Bench v1 prompts and upstream scorer assets."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from random import Random
import tempfile

import httpx

from src.persistence.storage import data_directory

DATASET = "deepresearch_bench"
VERSION = "DRB v1"
COMMIT = "f2735b5c3636759c22f9e936c0232de8bf545b67"
SOURCE = "https://github.com/Ayanami0730/deep_research_bench"
RAW = f"https://raw.githubusercontent.com/Ayanami0730/deep_research_bench/{COMMIT}"
QUERIES = "data/prompt_data/query.jsonl"
QUERY_HASH = "62b197a264a027fd4e2e795ff322a82dd6d566f5e350596ccc7572539ec987d1"
CRITERIA = "data/criteria_data/criteria.jsonl"
REFERENCE = "data/test_data/cleaned_data/reference.jsonl"
MAX_BYTES = 16 * 1024 * 1024
MANIFEST = json.loads(Path(__file__).with_name("drb_v1_manifest.json").read_text(encoding="utf-8"))


class BenchmarkError(ValueError):
    """Safe message suitable for API and CLI clients."""


def read_jsonl(raw):
    return [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]


def jsonl(rows):
    return "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows)


def root():
    return data_directory() / "benchmarks" / DATASET / COMMIT


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(data)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def fetch(name, digest):
    try:
        with httpx.stream("GET", f"{RAW}/{name}", timeout=30) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise BenchmarkError("官方文件超出大小限制")
    except httpx.HTTPError:
        raise BenchmarkError("无法从官方来源下载文件") from None
    payload = bytes(data)
    if sha256(payload).hexdigest() != digest:
        raise BenchmarkError("官方文件与锁定版本的 SHA-256 不匹配")
    return payload


def parse_queries(payload):
    if len(payload) > MAX_BYTES:
        raise BenchmarkError("JSONL 文件超出大小限制")
    try:
        rows = read_jsonl(payload)
        queries = []
        seen = set()
        for row in rows:
            item = {key: row[key] for key in ("id", "prompt", "language", "topic")}
            task_id = item["id"]
            if (type(task_id) is not int or task_id < 1 or task_id in seen
                or item["language"] not in ("zh", "en")
                or not all(isinstance(item[k], str) and item[k].strip() for k in ("prompt", "topic"))):
                raise ValueError
            queries.append(item)
            seen.add(task_id)
        if not queries:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise BenchmarkError("JSONL 需要唯一整数 id、prompt、language（zh/en）和 topic") from None
    return queries


def install_dataset(payload=None):
    method = "import" if payload is not None else "download"
    if payload is None:
        payload = fetch(QUERIES, QUERY_HASH)
    rows = parse_queries(payload)
    # Permit BOM and Windows newline conversion of the official JSONL.
    normalized = payload.removeprefix(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")
    if sha256(normalized).hexdigest() != QUERY_HASH:
        raise BenchmarkError("请选择锁定版本的官方 query.jsonl")
    from datetime import datetime, timezone
    metadata = {
        **identity(), "sha256": sha256(payload).hexdigest(), "canonical_sha256": QUERY_HASH,
        "count": len(rows), "languages": dict(Counter(row["language"] for row in rows)),
        "domains": dict(Counter(row["topic"] for row in rows)), "method": method,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write(root() / "queries.jsonl", normalized)
    atomic_write(root() / "dataset.json", json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
    return metadata


def identity():
    return {"id": DATASET, "name": "DeepResearch Bench", "version": VERSION,
            "commit": COMMIT, "source": SOURCE, "query_url": f"{RAW}/{QUERIES}"}


def dataset():
    try:
        metadata = json.loads((root() / "dataset.json").read_text(encoding="utf-8"))
        payload = (root() / "queries.jsonl").read_bytes()
    except (OSError, ValueError):
        raise BenchmarkError("请先下载或导入 DRB v1 数据集") from None
    if sha256(payload).hexdigest() != QUERY_HASH:
        raise BenchmarkError("本地数据集校验失败，请重新下载或导入")
    return metadata, parse_queries(payload)


def install_scorer():
    for name, digest in MANIFEST.items():
        path = root() / "official" / name
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != digest:
            atomic_write(path, fetch(name, digest))
    return {"commit": COMMIT, "ready": True}


def scorer_ready(*, verify=False):
    for name, digest in MANIFEST.items():
        path = root() / "official" / name
        if not path.is_file() or (verify and sha256(path.read_bytes()).hexdigest() != digest):
            return False
    return True


def select(rows, *, language=None, topic=None, ids=(), sample=None, seed=42, maximum=100):
    selected = [row for row in rows if (not language or row["language"] == language)
                and (not topic or row["topic"] == topic) and (not ids or row["id"] in ids)]
    if ids and set(ids) != {row["id"] for row in selected}:
        raise BenchmarkError("所选 ID 与数据集或筛选条件不匹配")
    if not selected or (sample is not None and sample > len(selected)):
        raise BenchmarkError("符合条件的任务不足")
    selected.sort(key=lambda row: row["id"])
    if sample is not None:
        selected = sorted(Random(seed).sample(selected, sample), key=lambda row: row["id"])
    if len(selected) > maximum:
        raise BenchmarkError("所选任务数超过本次任务数量上限")
    return selected
