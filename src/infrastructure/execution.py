"""Request telemetry and source filtering scoped to one research execution."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from math import isfinite
import time
from urllib.parse import unquote, urlsplit

_scope = ContextVar("execution_scope", default=None)


@contextmanager
def execution_scope(observer):
    token = _scope.set(observer)
    try:
        yield
    finally:
        _scope.reset(token)


def observer():
    return _scope.get()


def numeric_usage(usage):
    if not isinstance(usage, dict):
        usage = {}
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    values = {k: usage.get(k) for k in fields}
    for key, parent in (("cached_tokens", "prompt_tokens_details"), ("reasoning_tokens", "completion_tokens_details")):
        details = usage.get(parent)
        values[key] = details.get(key) if isinstance(details, dict) else None
    for key, value in values.items():
        values[key] = value if type(value) is int and 0 <= value < 2**53 else None
    if values["total_tokens"] is None and all(values[key] is not None for key in fields[:2]):
        values["total_tokens"] = values["prompt_tokens"] + values["completion_tokens"]
    cost = usage.get("cost")
    values["cost"] = cost if type(cost) in (int, float) and 0 <= cost < 10**9 and isfinite(cost) else None
    return values



@contextmanager
def observe_retrieval(phase):
    monitor = observer()
    if monitor is None:
        yield
        return
    call_id = monitor.begin("retrieval", phase)
    started = time.monotonic()
    try:
        yield
    except Exception:
        monitor.end(call_id, started, None, None)
        raise
    else:
        monitor.end(call_id, started, 200, None)


def observed_retrieval(phase):
    def wrap(method):
        @wraps(method)
        def measured(*args, **kwargs):
            with observe_retrieval(phase):
                return method(*args, **kwargs)
        return measured
    return wrap

def allowed_source(url):
    if not observer() or not url:
        return True
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        path = unquote(parts.path).lower()
    except ValueError:
        return False
    if host in {"deepresearch-bench.github.io", "deep-research-bench.github.io"}:
        return False
    if host in {"github.com", "raw.githubusercontent.com", "huggingface.co"}:
        return not any(name in path for name in (
            "/ayanami0730/deep_research_bench", "/muset-ai/deepresearch-bench",
        ))
    return not (host.endswith("arxiv.org") and "2506.11763" in path)
