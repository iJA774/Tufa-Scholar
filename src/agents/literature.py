"""Two-round evidence workflow expressed as a single LangGraph state graph."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
from typing import Literal, TypedDict
from urllib.parse import urlsplit, urlunsplit

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.agents.report import numbered_references, validate_claim, validate_markdown
from src.integrations.web_page import WebPageError
from src.integrations.web_search import WebSearchError
from src.integrations.model import ModelServiceError, StructuredOutputError, structured, structured_input_units
from src.infrastructure.diagnostics import diagnostic_context
from src.literature.keywords import relevance_score, relevance_terms
from src.literature.retrieval import _candidate_ids, rank_candidates
from src.tools import ToolContext, ToolRegistry
from src.tools.registry import ToolCallValidationError

logger = logging.getLogger("tufa.agent")
registry = ToolRegistry()

MAX_BACKGROUND_WEB_RESULTS = 3
MAX_RESEARCH_WEB_RESULTS = 3
MAX_CANDIDATE_CONTEXT = 6
MAX_EVIDENCE_CONTEXT = 8
MAX_EVIDENCE_TEXT = 9_600
MAX_REPORT_EVIDENCE_CONTEXT = 48
MAX_CANDIDATE_TEXT = 1_600

DEPTH_BUDGETS = {
    "light": {
        "rounds": 1, "actions_per_round": 4, "actions": 4,
        "background_searches": 1, "decision_input_units": 10_000,
        "cumulative_decision_input_units": 12_000,
    },
    "standard": {
        "rounds": 2, "actions_per_round": 6, "actions": 10,
        "background_searches": 2, "decision_input_units": 10_000,
        "cumulative_decision_input_units": 24_000,
    },
    "deep": {
        "rounds": 4, "actions_per_round": 8, "actions": 24,
        "background_searches": 4, "decision_input_units": 14_000,
        "cumulative_decision_input_units": 60_000,
    },
}


def _budget(state):
    return DEPTH_BUDGETS[_depth(state)]


def _depth(state):
    depth = (state.get("research") or {}).get("research_depth", "standard")
    return depth if depth in DEPTH_BUDGETS else "standard"


def _evidence_context_limits(state, *, expanded=False):
    if expanded and _depth(state) == "deep":
        return 12, 14_400
    return MAX_EVIDENCE_CONTEXT, MAX_EVIDENCE_TEXT


def _clean_labels(values, *, limit=12):
    result = []
    seen = set()
    for value in values or []:
        if not isinstance(value, str):
            continue
        label = re.sub(r"\s+", " ", value).strip()[:500]
        key = label.casefold()
        if label and key not in seen:
            result.append(label)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def _label_key(value):
    return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)


def _core_requirement_gaps(state, review):
    core = _clean_labels(state.get("core_requirements") or (state.get("plan") or {}).get("requirements", []))
    covered = {_label_key(value) for value in review.get("covered_requirements", []) if isinstance(value, str)}
    return [item for item in core if _label_key(item) not in covered]


def _review_is_sufficient(state, review):
    if not review.get("sufficient"):
        return False
    if _depth(state) != "deep":
        return True
    return not (
        _core_requirement_gaps(state, review)
        or review.get("gaps")
        or review.get("unresolved_contradictions")
        or not review.get("evidence_reliability_confirmed")
        or not review.get("important_conclusions_cross_validated")
        or not _select_evidence(state, expanded=True)
    )


def _similar_step(left, right):
    left_text = re.sub(r"\s+", " ", str(left or "").casefold()).strip()
    right_text = re.sub(r"\s+", " ", str(right or "").casefold()).strip()
    if not left_text or not right_text:
        return False
    if _label_key(left_text) == _label_key(right_text):
        return True
    def units(value):
        words = re.findall(r"[a-z0-9]+", value)
        cjk = re.findall(r"[\u3400-\u9fff]", value)
        words.extend("".join(cjk[index:index + 2]) for index in range(max(0, len(cjk) - 1)))
        return set(words)
    left_units, right_units = units(left_text), units(right_text)
    if not left_units or not right_units:
        return False
    return len(left_units & right_units) / len(left_units | right_units) >= 0.82


def _distinct_next_steps(state, review):
    return _distinct_plan_steps(state, review.get("next_steps", []))


def _distinct_plan_steps(state, steps):
    proposed = _clean_labels(steps, limit=8)
    executed = []
    for item in state.get("rounds", []):
        executed.extend(item.get("plan", []))
        executed.extend(item.get("queries", []))
    return [step for step in proposed if not any(_similar_step(step, old) for old in executed)]


class DispatcherOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    needs_research: bool
    reason: str = Field(default="", max_length=1_000)
    answer: str = Field(default="", max_length=8_000)

    @model_validator(mode="after")
    def direct_answer_present(self):
        if not self.needs_research and not self.answer.strip():
            raise ValueError("直接回答不能为空")
        return self


class BackgroundOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["web", "paper"]
    queries: list[str] = Field(default_factory=list, max_length=4)
    selected_candidate_ids: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(default="", max_length=5_000)
    gaps: list[str] = Field(default_factory=list, max_length=8)


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirements: list[str] = Field(default_factory=list, max_length=12)
    plan: list[str] = Field(default_factory=list, max_length=8)
    source: Literal["web", "paper"]
    action: Literal["research", "report"]
    completion_confirmed: bool = False
    rationale: str = Field(default="", max_length=1_000)


class ResearchActionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["tool", "finish"]
    tool_name: str = Field(default="", max_length=100)
    arguments: dict = Field(default_factory=dict)
    reason: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def action_has_tool(self):
        if self.action == "tool" and not self.tool_name.strip():
            raise ValueError("工具动作缺少名称")
        return self


class AnalystOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sufficient: bool
    covered_requirements: list[str] = Field(default_factory=list, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=12)
    next_steps: list[str] = Field(default_factory=list, max_length=8)
    unresolved_contradictions: list[str] = Field(default_factory=list, max_length=8)
    evidence_reliability_confirmed: bool = False
    important_conclusions_cross_validated: bool = False


_CONTROL_OPEN = re.compile(r"^```tufa-control[ \t]*$", re.MULTILINE)
_CONTROL = re.compile(r"\n(?P<data>.*?)\n```[ \t]*\Z", re.DOTALL)
_CITATION = re.compile(r"\[\[([a-zA-Z0-9_-]+)\]\]")


def _parse_control(result, schema):
    """Separate public prose from validated workflow decisions."""
    result = result.strip().replace("\r\n", "\n") if isinstance(result, str) else ""
    openings = list(_CONTROL_OPEN.finditer(result))
    if len(openings) != 1:
        raise StructuredOutputError("工作流控制块缺失或无效")
    opening = openings[0]
    body = result[:opening.start()].strip()
    match = _CONTROL.match(result, opening.end())
    if not body or not match:
        raise StructuredOutputError("工作流控制块缺失或无效")

    def unique_keys(pairs):
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError("duplicate control key")
        return data

    try:
        data = schema.model_validate(json.loads(match["data"], object_pairs_hook=unique_keys), strict=True)
    except (ValueError, RecursionError):
        raise StructuredOutputError("工作流控制块缺失或无效") from None
    return body, data.model_dump()


class State(TypedDict, total=False):
    research: dict
    history: list
    background: dict
    plan: dict
    current_plan: list
    current_source: str
    current_round: int
    current_draft: str
    current_review: dict
    current_findings: list
    candidate_pools: dict
    query_results: dict
    collected_web_blocks: list
    disclosed_local_documents: list
    read_web_pages: list
    evidence_pool: list
    evidence: list
    rounds: list
    core_requirements: list
    tool_results: list
    research_actions_used: int
    research_decision_input_units: int
    research_context_limit_reached: bool
    research_stop_reason: str
    errors: list
    warnings: list
    needs_research: bool
    content: str
    background_decision: dict
    last_completed: str
    next_node: str
    prepared_round: int
    final_evidence: list
    final_rejections: list
    report_fallback: bool
    _event_context: object


class _EventContext:
    """Per-run callback and call ID source; held only in the in-memory graph state."""

    def __init__(self, callback):
        self.callback = callback
        self.sequence = 0

    def next_call_id(self):
        self.sequence += 1
        return f"call-{self.sequence}"

    def emit(self, kind, data):
        if self.callback:
            self.callback(kind, data)


def _emit(on_stage, node_name):
    if on_stage:
        on_stage(node_name)


def _emit_event(state, kind, data):
    context = state.get("_event_context")
    if context:
        context.emit(kind, data)


def _safe_event_text(value, limit=500):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _public_event_url(value):
    """Return a display URL without credentials, query strings, fragments, or private hosts."""
    if not isinstance(value, str) or len(value) > 4_096:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if parts.scheme.lower() not in {"http", "https"} or not host or parts.username or parts.password:
            return None
        host = host.rstrip(".").lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test")) or "." not in host:
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        netloc = host
        if parts.port and not ((parts.scheme.lower() == "http" and parts.port == 80) or (parts.scheme.lower() == "https" and parts.port == 443)):
            netloc = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme.lower(), netloc, parts.path[:1_000], "", ""))
    except ValueError:
        return None


def _source_modes(source):
    return ["paper"] if source in {"local", "collection"} else ["paper", "web"]


def _preferred_mode(source):
    if source == "web":
        return "web"
    return "paper"


def _report_mode(state):
    source = state["research"]["source"]
    if source != "auto":
        return _preferred_mode(source)
    for item in state.get("rounds", []):
        if item.get("source") in _source_modes(source):
            return item["source"]
    mode = state.get("current_source") or state.get("background", {}).get("source")
    return mode if mode in _source_modes(source) else "none"


def _tool_context(research, mode):
    source = research["source"]
    if mode == "web":
        tool_source = "web"
    elif source in {"local", "openalex", "all", "collection"}:
        tool_source = source
    elif source == "web":
        # The planner may broaden a web-preferred task to papers on a later round.
        tool_source = "all"
    else:
        tool_source = "all"
    return ToolContext(
        research["project_id"], tool_source, tuple(research.get("paper_ids") or ()),
        network_search=research.get("network_search", True),
        local_literature=research.get("local_literature", True),
    )


def _catalog_for_mode(research, mode):
    return registry.list_tools(_tool_context(research, mode))


def _search_catalog(research, modes=None):
    catalogs = []
    seen = set()
    for mode in modes or _source_modes(research["source"]):
        for item in _catalog_for_mode(research, mode):
            if item["name"] in {"search_papers", "search_web"} and item["name"] not in seen:
                catalogs.append(item)
                seen.add(item["name"])
    return catalogs


def _available_modes(research):
    modes = _source_modes(research["source"])
    available = [mode for mode in modes if any(
        item["name"] in {"search_papers", "search_web", "select_local_domains"}
        for item in _catalog_for_mode(research, mode)
    )]
    # Keep a valid workflow mode even when every source is disabled; its
    # empty tool catalog lets the model report the evidence gap.
    return available or modes


def _model_template_context(state, search_mode, node_role, available_tools):
    research = state["research"]
    catalog = _catalog_for_mode(research, search_mode) if available_tools is None and search_mode in ("web", "paper") else (available_tools or [])
    return {
        "search_mode": search_mode,
        "research_mode": research.get("research_mode", "auto"),
        "available_tools": catalog,
        "node_role": node_role,
        "local_capable": search_mode == "paper" and research["source"] in {"local", "collection", "all", "auto"} and research.get("local_literature", True),
    }


def _model(state, prompt_id, payload, schema, *, node_role, search_mode="none", available_tools=None, native_tools=None):
    research = state["research"]
    template_context = _model_template_context(state, search_mode, node_role, available_tools)
    context = state.get("_event_context")
    if not context:
        call_options = {"model_options": research, "template_context": template_context}
        if native_tools is not None:
            call_options["native_tools"] = native_tools
        return structured(prompt_id, payload, schema, **call_options)

    call_id = context.next_call_id()

    def on_stream(kind, data):
        # The model adapter exposes only streamed assistant content. Keep the
        # workflow envelope small and never forward request prompts or options.
        if kind == "model.delta":
            phase = data.get("phase") if isinstance(data, dict) else None
            stream_public_output = (
                node_role == "reporter"
                or (node_role == "researcher" and phase == "draft")
                or (node_role == "dispatcher" and research.get("research_mode") == "quick")
            )
            if not stream_public_output:
                return
        raw = data if isinstance(data, dict) else {}
        event_data = {"node": node_role, "call_id": call_id}
        attempt = raw.get("attempt")
        if isinstance(attempt, int) and not isinstance(attempt, bool):
            event_data["attempt"] = attempt
        phase = raw.get("phase")
        if isinstance(phase, str):
            event_data["phase"] = phase[:40]
        text = raw.get("text")
        if kind == "model.delta" and isinstance(text, str):
            for offset in range(0, len(text), 2_000):
                context.emit(kind, {**event_data, "text": text[offset:offset + 2_000]})
            return
        context.emit(kind, event_data)

    call_options = {
        "model_options": research,
        "template_context": template_context,
        "on_stream": on_stream,
    }
    if native_tools is not None:
        call_options["native_tools"] = native_tools
    return structured(prompt_id, payload, schema, **call_options)


def _payload(state, *, expanded_evidence=False, **extra):
    research = state["research"]
    evidence_limit, _ = _evidence_context_limits(state, expanded=expanded_evidence)
    return {
        "question": research["query"],
        "history": _history_for_model(state.get("history", [])),
        "source": research["source"],
        "background": _background_summary_for_model(state.get("background", {})),
        "plan": state.get("plan", {}),
        "rounds": _rounds_for_model(state.get("rounds", []), evidence_limit=evidence_limit),
        "evidence": _evidence_views(_select_evidence(state, expanded=expanded_evidence), text_limit=1_200),
        "errors": state.get("errors", []),
        "warnings": state.get("warnings", []),
        **extra,
    }


def _background_summary_for_model(background):
    """Keep the screened overview and gaps, not candidate records repeated elsewhere."""
    if not isinstance(background, dict):
        return {}
    summary = {key: value for key, value in background.items() if key != "selected_candidates"}
    selected_ids = [
        item.get("candidate_id")
        for item in background.get("selected_candidates", [])
        if isinstance(item, dict) and item.get("candidate_id")
    ]
    if selected_ids:
        summary["selected_candidate_ids"] = selected_ids
    return summary


def _rounds_for_model(rounds, *, include_drafts=False, visible_evidence=None, evidence_limit=MAX_EVIDENCE_CONTEXT):
    bounded = []
    for round_item in rounds:
        review = round_item.get("review", {})
        if include_drafts:
            item = dict(round_item)
            item["candidate_ids"] = list(round_item.get("candidate_ids", []))[-MAX_CANDIDATE_CONTEXT:]
            if visible_evidence is not None:
                checked = validate_markdown(item.get("draft", ""), visible_evidence)
                item["draft"] = checked["content"]
                item["findings"] = checked["findings"]
                visible = {entry["id"] for entry in visible_evidence}
                item["evidence_ids"] = [value for value in item.get("evidence_ids", []) if value in visible]
        else:
            item = {
                "index": round_item.get("index"),
                "source": round_item.get("source"),
                "plan": list(round_item.get("plan", []))[:6],
                "status": round_item.get("status"),
                "review": {
                    "sufficient": bool(review.get("sufficient")),
                    "covered_requirements": list(review.get("covered_requirements", []))[:6],
                    "gaps": list(review.get("gaps", []))[:6],
                },
                "findings": [
                    {"text": claim.get("text", "")[:500], "citations": list(claim.get("citations", []))[:4]}
                    for claim in round_item.get("findings", [])[:4]
                ],
            }
            cited = sorted(_claim_ids(round_item.get("draft", {}))) + sorted(_findings_ids(round_item.get("findings", [])))
            item["evidence_ids"] = list(dict.fromkeys(cited))[:evidence_limit]
        bounded.append(item)
    return bounded


def _history_for_model(history):
    bounded = []
    for item in (history or [])[-8:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if isinstance(role, str) and isinstance(content, str):
            bounded.append({"role": role[:30], "content": content[-1_200:]})
    return bounded


def _candidate_key(item, mode):
    identities = sorted(_candidate_ids(item, "web" if mode == "web" else "paper"))
    raw = "|".join(identities) if identities else "title:" + " ".join(str(item.get("title", "")).casefold().split())
    return "C" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _evidence_signature(item):
    url = item.get("original_url") or ""
    try:
        parts = urlsplit(url)
        url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))
    except ValueError:
        pass
    return (
        item.get("paper_id"), item.get("version_id"), item.get("page"),
        item.get("title"), item.get("source_level"), item.get("text"), url,
        item.get("document_id"), item.get("char_start"), item.get("char_end"),
    )


def _web_block_key(candidate_id, block):
    body = str(block.get("content", ""))
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    offset = block.get("content_offset")
    return f"{candidate_id}:{offset if offset is not None else 'snippet'}:{digest}"


def _candidate_view(item, mode, *, include_text=True, text_limit=MAX_CANDIDATE_TEXT):
    allowed = ("candidate_id", "id", "title", "doi", "arxiv_id", "openalex_id", "year", "source_level", "original_url", "url", "provider", "retrieved_at", "content_offset")
    result = {key: item.get(key) for key in allowed if item.get(key) is not None}
    result["source"] = mode
    if include_text and item.get("abstract"):
        result["abstract"] = str(item["abstract"])[:text_limit]
    if item.get("content") and include_text:
        result["content"] = str(item["content"])[:text_limit]
    if item.get("next_offset") is not None:
        result["next_offset"] = item["next_offset"]
    if include_text and item.get("content_blocks"):
        blocks = []
        budget = text_limit
        for block in item["content_blocks"][-3:]:
            body = str(block.get("content", ""))[:min(800, budget)]
            if not body:
                continue
            blocks.append({
                "content": body,
                "offset": block.get("content_offset"),
                "next_offset": block.get("next_offset"),
                "source_level": block.get("source_level"),
            })
            budget -= len(body)
            if budget <= 0:
                break
        if blocks:
            result["content_blocks"] = blocks
    return result


def _evidence_views(items, *, text_limit=1_200):
    fields = ("id", "paper_id", "title", "text", "source_level", "version_id", "page", "original_url", "provider", "retrieved_at", "char_start", "char_end", "published_at", "candidate_id", "document_id")
    result = []
    for item in items:
        view = {key: item.get(key) for key in fields if item.get(key) is not None}
        if view.get("text"):
            view["text"] = str(view["text"])[:text_limit]
        result.append(view)
    return result


def _pool_add(pools, mode, items):
    pool = list(pools.get(mode, []))
    indexes = {item.get("candidate_id"): i for i, item in enumerate(pool)}
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        item["candidate_id"] = _candidate_key(item, mode)
        key = item["candidate_id"]
        if item.get("content"):
            block = {
                "content": item["content"],
                "content_offset": item.get("content_offset"),
                "next_offset": item.get("next_offset"),
                "source_level": item.get("source_level"),
            }
            blocks = list(item.get("content_blocks", []))
            if not any(existing.get("content_offset") == block["content_offset"] and existing.get("content") == block["content"] for existing in blocks):
                blocks.append(block)
            item["content_blocks"] = blocks
        if key in indexes:
            old = dict(pool[indexes[key]])
            old_blocks = list(old.get("content_blocks", []))
            for name, value in item.items():
                if name == "content_blocks":
                    continue
                if value is not None and (not old.get(name) or name in {"content", "source_level", "content_offset", "next_offset"}):
                    old[name] = value
            merged_blocks = old_blocks
            for block in item.get("content_blocks", []):
                if not any(existing.get("content_offset") == block.get("content_offset") and existing.get("content") == block.get("content") for existing in merged_blocks):
                    merged_blocks.append(block)
            if merged_blocks:
                old["content_blocks"] = merged_blocks
            pool[indexes[key]] = old
        else:
            indexes[key] = len(pool)
            pool.append(item)
    pools[mode] = pool
    return pools


def _ranked_candidates(query_results, mode, old_pool):
    ranked = rank_candidates(query_results.get(mode, {}), mode)
    ordered = []
    for item in ranked:
        candidate = dict(item)
        identities = _candidate_ids(candidate, "web" if mode == "web" else "paper")
        previous = next((old for old in old_pool if old.get("candidate_id") == _candidate_key(candidate, mode) or identities & _candidate_ids(old, "web" if mode == "web" else "paper")), None)
        key = previous.get("candidate_id") if previous else _candidate_key(candidate, mode)
        if previous:
            merged = dict(previous)
            for name, value in candidate.items():
                if value is not None and (not merged.get(name) or name in {"rank", "retrieval_sources"}):
                    merged[name] = value
            candidate = merged
        candidate["candidate_id"] = key
        ordered.append(candidate)
    return ordered


def _record_search_results(state, mode, query, items):
    query_results = {
        key: {query_key: [dict(entry) for entry in entries] for query_key, entries in value.items()}
        for key, value in state.get("query_results", {}).items()
        if isinstance(value, dict)
    }
    source_results = query_results.setdefault(mode, {})
    query_key = " ".join(str(query).casefold().split())
    entries = source_results.setdefault(query_key, [])
    by_key = {_candidate_key(entry.get("paper", {}), mode): i for i, entry in enumerate(entries)}
    items = [item for item in items if mode != "web" or not _obvious_web_junk(item)]
    for position, item in enumerate(items, 1):
        rank = item.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            rank = position
        key = _candidate_key(item, mode)
        if key in by_key:
            index = by_key[key]
            entries[index]["rank"] = min(entries[index].get("rank", rank), rank)
            stored = entries[index].get("paper", {})
            for name, value in item.items():
                if value is not None and not stored.get(name):
                    stored[name] = value
        else:
            by_key[key] = len(entries)
            entries.append({"paper": dict(item), "rank": rank})
    pools = {key: list(value) for key, value in state.get("candidate_pools", {"paper": [], "web": []}).items()}
    ranked = _ranked_candidates(query_results, mode, pools.get(mode, []))
    pools[mode] = ranked
    return pools, query_results


def _obvious_web_junk(item):
    title = str(item.get("title") or "").strip().casefold()
    content = str(item.get("content") or item.get("snippet") or "").strip().casefold()
    title = re.sub(r"\s+", " ", title)
    content = re.sub(r"\s+", " ", content)
    if not title and not content:
        return True
    placeholder_titles = {"access denied", "403 forbidden", "security challenge", "attention required", "just a moment..."}
    if title in placeholder_titles and len(content) < 300:
        return True
    boilerplate = {
        "verify you are human", "are you a robot", "checking your browser",
        "enable javascript and cookies", "access denied", "403 forbidden",
        "captcha required", "security challenge",
    }
    return content in boilerplate


def _candidate_summaries(state, mode, ids=None, *, limit=MAX_CANDIDATE_CONTEXT, include_text=True, text_limit=MAX_CANDIDATE_TEXT):
    pool = state.get("candidate_pools", {}).get(mode, [])
    if ids is not None:
        wanted = set(ids)
        pool = [item for item in pool if item.get("candidate_id") in wanted]
    pool = _prioritized_candidates(state, pool)
    return [_candidate_view(item, mode, include_text=include_text, text_limit=text_limit) for item in pool[:limit]]


def _relevance_facets(state):
    research = state.get("research", {})
    facets = [research.get("query", "")]
    facets.extend(state.get("current_plan", []))
    facets.extend((state.get("plan") or {}).get("requirements", []))
    return [relevance_terms(value) for value in facets if isinstance(value, str) and value.strip()]


def _candidate_relevance(state, candidate, facets):
    body = " ".join(str(candidate.get(field) or "") for field in ("title", "abstract", "content", "snippet")).casefold()
    return sum(relevance_score(facet, body) for facet in facets), body


def _primary_evidence_score(item):
    """Prefer locatable source text; this scores evidence properties, not domains."""
    level = str(item.get("source_level") or "").casefold()
    located = item.get("page") is not None or item.get("char_start") is not None or item.get("content_offset") is not None
    direct = level in {"full_text", "web_content", "local_markdown"}
    return (2 if direct else 0) + (1 if located else 0)


def _primary_candidate_score(item):
    """Use observable material metadata; never infer quality from a domain."""
    level = str(item.get("source_level") or "").casefold()
    located = item.get("page") is not None or item.get("content_offset") is not None
    return (2 if level in {"full_text", "web_content"} else 0) + (1 if located else 0)


def _prioritized_candidates(state, candidates):
    preference = (state.get("research") or {}).get("relevance_filter", "standard")
    facets = _relevance_facets(state)
    query_has_cjk = bool(re.search(r"[\u3400-\u9fff]", (state.get("research") or {}).get("query", "")))
    scored = []
    for index, candidate in enumerate(candidates):
        score, body = _candidate_relevance(state, candidate, facets)
        title = str(candidate.get("title") or "").casefold()
        title_score = sum(relevance_score(facet, title) for facet in facets)
        # Lexical matching is only a prioritization signal. Avoid excluding likely
        # translations and short metadata where the heuristic is particularly weak.
        candidate_has_cjk = bool(re.search(r"[\u3400-\u9fff]", body))
        comparable_language = not query_has_cjk or candidate_has_cjk
        substantial = len(body) >= 80
        scored.append((index, candidate, score, title_score, comparable_language, substantial))
    # Strict narrows to title-level direct matches when such matches exist.
    # If there is no direct lexical signal, retain candidates rather than treating
    # this language heuristic as proof of irrelevance.
    if preference in {"standard", "strict"}:
        scored = [row for row in scored if not (row[2] == 0 and row[5] and row[4])]
    if preference == "strict":
        has_direct_match = any(row[3] > 0 for row in scored)
        if has_direct_match:
            scored = [row for row in scored if not (row[3] == 0 and row[4] and row[5])]
        scored.sort(key=lambda row: (-row[2], -row[3], row[0]))
    elif preference == "standard":
        scored.sort(key=lambda row: (-row[2], row[0]))
    # Relevance remains the primary order; when equally relevant, prefer candidates
    # whose supplied metadata indicates direct text and a verifiable location.
    if (state.get("research") or {}).get("prefer_primary_evidence", True):
        scored.sort(key=lambda row: (-row[2], -row[3] if preference == "strict" else 0, -_primary_candidate_score(row[1]), row[0]))
    return [candidate for _, candidate, _, _, _, _ in scored]


def _compact_tool_results(results, *, limit=4, text_limit=400):
    compact = []
    for item in (results or [])[-limit:]:
        if not isinstance(item, dict):
            continue
        entry = {}
        for key, value in item.items():
            if isinstance(value, str):
                entry[key] = value[:text_limit]
            elif isinstance(value, list):
                entry[key] = [part[:text_limit] if isinstance(part, str) else part for part in value[:20]]
            elif isinstance(value, (int, float, bool)) or value is None:
                entry[key] = value
        compact.append(entry)
    return compact


def _unique_evidence(items):
    kept = []
    seen_signatures = set()
    used_ids = set()
    for item in items:
        signature = _evidence_signature(item)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        entry = dict(item)
        identifier = str(entry.get("id") or "evidence")
        if identifier in used_ids:
            key = json.dumps(signature, ensure_ascii=False, default=str, separators=(",", ":"))
            identifier = "e" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
            suffix = 2
            while identifier in used_ids:
                identifier = "e" + hashlib.sha256((key + str(suffix)).encode("utf-8")).hexdigest()[:16]
                suffix += 1
        entry["id"] = identifier
        used_ids.add(identifier)
        kept.append(entry)
    return kept


def _enrich_evidence(items, candidates, mode):
    enriched = []
    for evidence in items:
        item = dict(evidence)
        match = next((candidate for candidate in candidates if (
            item.get("paper_id") is not None and candidate.get("id") == item.get("paper_id")
        ) or (
            item.get("title") and candidate.get("title") == item.get("title")
            and (not item.get("original_url") or item.get("original_url") in {candidate.get("original_url"), candidate.get("url")})
        )), None)
        if match and match.get("candidate_id"):
            item["candidate_id"] = match["candidate_id"]
        item["research_source"] = mode
        enriched.append(item)
    return enriched


def _claim_ids(draft):
    return set(_CITATION.findall(draft)) if isinstance(draft, str) else set()


def _findings_ids(findings):
    return {item for finding in findings for item in finding.get("citations", [])}


def _select_evidence(state, include_ids=None, *, expanded=False):
    pool = _unique_evidence(state.get("evidence_pool", []))
    include_ids = set(include_ids or ())
    selected = []
    size = 0
    evidence_limit, text_limit = _evidence_context_limits(state, expanded=expanded)

    def add(item):
        nonlocal size
        if item in selected or len(selected) >= evidence_limit:
            return
        text = str(item.get("text", ""))
        if size + len(text) > text_limit:
            return
        selected.append(item)
        size += len(text)

    facets = [state["research"]["query"]]
    facets.extend(state.get("current_plan", []))
    facets.extend((state.get("plan") or {}).get("requirements", []))
    for round_item in state.get("rounds", []):
        facets.extend(round_item.get("plan", []))
        review = round_item.get("review", {})
        facets.extend(review.get("gaps", []))
    terms = [relevance_terms(value) for value in facets if value]

    def score(item):
        body = (item.get("title", "") + " " + item.get("text", "")).casefold()
        return sum(relevance_score(facet, body) for facet in terms)

    def evidence_rank(item):
        primary = _primary_evidence_score(item) if (state.get("research") or {}).get("prefer_primary_evidence", True) else 0
        return (-primary, -score(item))

    previous_ids = set(state.get("rounds", [])[-1].get("evidence_ids", [])) if state.get("rounds") else set()
    cited = [item for item in pool if item.get("id") in include_ids]
    for item in cited:
        add(item)

    new_evidence = sorted(
        ((index, item) for index, item in enumerate(pool) if item.get("id") not in previous_ids and item not in cited),
        key=lambda pair: (*evidence_rank(pair[1]), -pair[0]),
    )
    for _, item in new_evidence:
        add(item)

    ranked = sorted(
        enumerate(pool),
        key=lambda pair: (*evidence_rank(pair[1]), -pair[0]),
    )
    for _, item in ranked:
        add(item)
    return selected


def _safe_error(node, code, message):
    return {"node": node, "code": code, "message": message[:300]}


def _tool_error(name, exc):
    if isinstance(exc, ToolCallValidationError):
        return _safe_error("researcher", exc.code, "工具参数不符合当前来源范围或结构要求。")
    if name == "read_web_page":
        if getattr(exc, "reason", "") == "non_public_address":
            return _safe_error(
                "researcher",
                "web_page_non_public_address",
                "来源地址未通过公网校验，无法读取正文。可使用已有搜索摘要或尝试其他来源。",
            )
        return _safe_error("researcher", "web_page_read_failed", "网页正文读取失败，可继续尝试其他候选。")
    category = getattr(exc, "category", "")
    if category:
        if name in {"search_web", "search_papers"} and category in {"network", "timeout"}:
            reason = "网络连接失败" if category == "network" else "连接等待超时"
            return _safe_error(
                "researcher",
                "search_" + str(category)[:40],
                f"搜索{reason}，本次没有取得搜索结果。可稍后重试。",
            )
        return _safe_error("researcher", "search_" + str(category)[:40], "搜索服务返回失败，可改写查询或尝试其他允许来源。")
    if name in {"search_web", "search_papers"}:
        return _safe_error("researcher", name + "_failed", "搜索失败，可改写查询后继续尝试。")
    return _safe_error("researcher", "tool_failed", "工具调用失败，可根据允许工具继续研究。")


def _find_candidates(pool, ids):
    by_id = {item.get("candidate_id"): item for item in pool}
    if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or item not in by_id for item in ids):
        raise ToolCallValidationError("工具参数无效", "unknown_candidate")
    if len(set(ids)) != len(ids):
        raise ToolCallValidationError("工具参数无效", "duplicate_candidate")
    return [by_id[item] for item in ids]


def _helper_catalog(research, mode):
    """Expose the candidate-ID helper while keeping the registry's real schemas visible."""
    catalog = [dict(item) for item in _catalog_for_mode(research, mode)
               if not (mode == "paper" and (item["name"] == "read_paper" or
                   (research["source"] in {"local", "collection"} and item["name"] in {"search_papers", "collect_evidence"})))]
    if mode == "paper" and not research.get("network_search", True):
        catalog = [item for item in catalog if item["name"] not in {"search_papers", "collect_evidence"}]
    for item in catalog:
        if item["name"] == "search_web":
            schema = item.get("input_schema", {})
            properties = dict(schema.get("properties", {}))
            limit = dict(properties.get("limit", {}))
            limit["default"] = MAX_RESEARCH_WEB_RESULTS
            limit["maximum"] = MAX_RESEARCH_WEB_RESULTS
            properties["limit"] = limit
            item["input_schema"] = {**schema, "properties": properties}
        elif item["name"] == "collect_evidence":
            item["description"] += " 研究节点用 candidate_ids 引用本次检索池中已存在的候选；运行器会映射为原始候选对象，不能自行编造论文或网页正文。"
            item["input_schema"] = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "candidate_ids": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "string"}},
                    "relevance_queries": {"type": "array", "maxItems": 8, "items": {"type": "string", "minLength": 1, "maxLength": 1000}},
                },
                "required": ["query", "candidate_ids"],
            }
        elif item["name"] == "read_web_page":
            item["description"] += " URL 必须来自当前网页候选池，运行器会拒绝模型自行构造的地址。"
            item["input_schema"] = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "candidate_id": {"type": "string"},
                    "url": {"type": "string", "maxLength": 4096},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 4000},
                },
                "anyOf": [{"required": ["candidate_id"]}, {"required": ["url"]}],
            }
    return catalog


def _native_tool_specs(catalog):
    """Copy the current helper catalog into provider function definitions."""
    specs = []
    for item in catalog:
        schema = dict(item.get("input_schema") or {})
        properties = dict(schema.get("properties") or {})
        properties["reason"] = {
            "type": "string",
            "maxLength": 240,
            "description": "简短说明这次操作对用户的目的，可省略。",
        }
        schema["properties"] = properties
        specs.append({
            "name": item["name"],
            "description": item["description"],
            "input_schema": schema,
        })
    return specs


def _invoke_tool(state, mode, tool_name, arguments):
    research = state["research"]
    if state.get("research_context_limit_reached"):
        raise ToolCallValidationError("工具参数无效", "decision_context_budget_reached")
    allowed_tool_names = {item["name"] for item in _helper_catalog(research, mode)}
    if tool_name not in allowed_tool_names:
        raise ToolCallValidationError("工具参数无效", "tool_not_available")
    context = _tool_context(research, mode)
    if mode == "paper" and tool_name in {"search_papers", "collect_evidence"}:
        context = ToolContext(research["project_id"], "openalex", (), network_search=research.get("network_search", True), local_literature=False)
    args = dict(arguments)
    # Only candidates presented in this round's ranked queue may be explicitly
    # fetched. The full pool remains available for stable identity and validation.
    visible_ids = {item["candidate_id"] for item in _candidate_summaries(state, mode)}
    previously_selected = {
        item.get("candidate_id")
        for item in (state.get("background", {}).get("selected_candidates", []) or [])
        if item.get("candidate_id")
    }
    previously_read = {
        item.get("candidate_id")
        for item in state.get("evidence_pool", [])
        if item.get("candidate_id")
    }
    allowed_queue_ids = visible_ids | previously_selected | previously_read
    if tool_name == "select_local_domains":
        from src.tools.local_markdown import list_local_domains
        domains = [d for d in list_local_domains(context)[:200] if isinstance(d, str) and len(d) <= 200]
        requested = args.get("domains")
        if not isinstance(requested, list) or not requested or any(d not in domains for d in requested):
            raise ToolCallValidationError("工具参数无效", "unknown_local_domain")
        result = registry.invoke(tool_name, args, context=context)
        documents = [{**item, "title": str(item.get("title", ""))[:240],
                      "description": str(item.get("description", ""))[:240],
                      "tags": [str(tag)[:100] for tag in item.get("tags", [])[:10]],
                      "domains": [str(domain)[:200] for domain in item.get("domains", [])[:10]]}
                     for item in result.get("documents", [])[:20]]
        result = {**result, "documents": documents}
        disclosed = list(dict.fromkeys(state.get("disclosed_local_documents", []) + [item["document_id"] for item in documents]))
        results = list(state.get("tool_results", [])) + [{"tool": tool_name, **result}]
        return {"disclosed_local_documents": disclosed, "tool_results": results}, result
    if tool_name == "read_local_markdown":
        if args.get("document_id") not in state.get("disclosed_local_documents", []):
            raise ToolCallValidationError("工具参数无效", "undisclosed_local_document")
        result = registry.invoke(tool_name, args, context=context)
        pool = _unique_evidence(state.get("evidence_pool", []) + result)
        signatures = {_evidence_signature(item): item["id"] for item in pool}
        normalized = [{**item, "id": signatures[_evidence_signature(item)]} for item in result]
        results = list(state.get("tool_results", [])) + [{"tool": tool_name, "document_id": args["document_id"], "evidence_ids": [item["id"] for item in normalized], "next_offset": args.get("offset", 0) + len(normalized) if len(normalized) == 5 else None}]
        return {"evidence_pool": pool, "evidence": pool, "tool_results": results}, normalized
    if tool_name == "collect_evidence":
        pool = state.get("candidate_pools", {}).get(mode, [])
        candidate_ids = args.pop("candidate_ids", [])
        chosen = _find_candidates(pool, candidate_ids)
        if any(item.get("candidate_id") not in allowed_queue_ids for item in chosen):
            raise ToolCallValidationError("工具参数无效", "candidate_not_in_priority_queue")
        if len(chosen) > 6 or "papers" in args:
            raise ToolCallValidationError("工具参数无效", "invalid_arguments")
        papers = []
        blocks_to_mark = []
        processed = set(state.get("collected_web_blocks", []))
        per_candidate = []
        for item in chosen:
            blocks = item.get("content_blocks") or []
            if mode == "web" and blocks:
                pending = []
                for block in sorted(blocks, key=lambda value: value.get("content_offset") if value.get("content_offset") is not None else -1, reverse=True):
                    marker = _web_block_key(item["candidate_id"], block)
                    if marker not in processed:
                        pending.append((item, block, marker))
                per_candidate.append(pending)
            else:
                papers.append({key: value for key, value in item.items() if key != "candidate_id"})
        if mode == "web":
            # First take the newest unprocessed block for each candidate, then
            # fill the six-entry tool limit in round-robin order.
            selected_blocks = []
            for pending in per_candidate:
                if pending and len(selected_blocks) < 6:
                    selected_blocks.append(pending[0])
            offset = 1
            while len(selected_blocks) < 6 and any(len(pending) > offset for pending in per_candidate):
                for pending in per_candidate:
                    if len(pending) > offset and len(selected_blocks) < 6:
                        selected_blocks.append(pending[offset])
                offset += 1
            for item, block, marker in selected_blocks:
                candidate = {key: value for key, value in item.items() if key not in {"candidate_id", "content_blocks"}}
                candidate.update(block)
                papers.append(candidate)
                blocks_to_mark.append(marker)
        args["papers"] = papers[:6]
        result = registry.invoke(tool_name, args, context=context)
        enriched = _enrich_evidence(result, chosen, mode)
        evidence_pool = _unique_evidence(state.get("evidence_pool", []) + enriched)
        id_by_signature = {_evidence_signature(item): item["id"] for item in evidence_pool}
        normalized = [{**item, "id": id_by_signature[_evidence_signature(item)]} for item in enriched]
        tool_results = list(state.get("tool_results", [])) + [{"tool": tool_name, "count": len(normalized), "evidence_ids": [item["id"] for item in normalized]}]
        return {"evidence_pool": evidence_pool, "evidence": evidence_pool, "tool_results": tool_results, "collected_web_blocks": list(processed | set(blocks_to_mark))}, normalized
    if tool_name == "read_web_page":
        read_key = _web_read_key(state, args)
        pool = state.get("candidate_pools", {}).get("web", [])
        visible_pool = [item for item in pool if item.get("candidate_id") in allowed_queue_ids]
        if "candidate_id" in args:
            candidate_id = args.pop("candidate_id")
            match = _find_candidates(visible_pool, [candidate_id])[0]
            candidate_url = match.get("url") or match.get("original_url")
            if "url" in args and args["url"] != candidate_url:
                raise ToolCallValidationError("工具参数无效", "conflicting_candidate_reference")
            args["url"] = candidate_url
        allowed_urls = {item.get("url") or item.get("original_url") for item in visible_pool}
        if not isinstance(args.get("url"), str) or args["url"] not in allowed_urls:
            raise ToolCallValidationError("工具参数无效", "unknown_candidate_url")
        result = registry.invoke(tool_name, args, context=context)
        original = next((item for item in visible_pool if (item.get("url") or item.get("original_url")) == args["url"]), {})
        content = result.get("markdown", "")
        page_item = {
            **original,
            "title": result.get("title") or original.get("title") or args["url"],
            "url": original.get("url") or args["url"],
            "original_url": original.get("original_url") or args["url"],
            "content": content,
            "content_offset": result.get("offset", args.get("offset", 0)),
            "next_offset": result.get("next_offset"),
            "source_level": "web_content",
            "provider": original.get("provider") or "web_reader",
            "retrieved_at": result.get("retrieved_at") or original.get("retrieved_at") or "",
        }
        pools = dict(state.get("candidate_pools", {}))
        _pool_add(pools, "web", [page_item])
        tool_results = list(state.get("tool_results", [])) + [{"tool": tool_name, "candidate_id": page_item["candidate_id"], "offset": page_item["content_offset"], "next_offset": page_item["next_offset"], "content": content[:MAX_CANDIDATE_TEXT]}]
        read_web_pages = list(state.get("read_web_pages", []))
        if read_key is not None and read_key not in read_web_pages:
            read_web_pages.append(read_key)
        return {"candidate_pools": pools, "tool_results": tool_results, "read_web_pages": read_web_pages}, {"candidate_id": page_item["candidate_id"], "content": content[:MAX_CANDIDATE_TEXT], "offset": page_item["content_offset"], "next_offset": page_item["next_offset"], "truncated": result.get("truncated", False)}
    if tool_name == "search_papers":
        result = registry.invoke(tool_name, args, context=context)
        items = result.get("results", [])
        pools, query_results = _record_search_results(state, mode, args.get("query", ""), items)
        warnings = list(state.get("warnings", []))
        if result.get("warnings") and "paper_search_incomplete" not in warnings:
            warnings.append("paper_search_incomplete")
        tool_warnings = ["paper_search_incomplete"] if result.get("warnings") else []
        tool_results = list(state.get("tool_results", [])) + [{"tool": tool_name, "query": args.get("query", ""), "candidate_count": len(items), "warnings": tool_warnings}]
        return {"candidate_pools": pools, "query_results": query_results, "tool_results": tool_results, "warnings": warnings}, {"candidate_count": len(items), "warnings": tool_warnings}
    if tool_name == "search_web":
        if "limit" not in args:
            args["limit"] = MAX_RESEARCH_WEB_RESULTS
        elif type(args.get("limit")) is int:
            args["limit"] = min(args["limit"], MAX_RESEARCH_WEB_RESULTS)
        else:
            raise ToolCallValidationError("工具参数无效", "invalid_search_limit")
        result = registry.invoke(tool_name, args, context=context)
        pools, query_results = _record_search_results(state, "web", args.get("query", ""), result)
        tool_results = list(state.get("tool_results", [])) + [{"tool": tool_name, "query": args.get("query", ""), "candidate_count": len(result)}]
        return {"candidate_pools": pools, "query_results": query_results, "tool_results": tool_results}, {"candidate_count": len(result)}
    result = registry.invoke(tool_name, args, context=context)
    return {"tool_results": list(state.get("tool_results", [])) + [{"tool": tool_name, "result": str(result)[:MAX_CANDIDATE_TEXT]}]}, result


def _tool_event_start(state, node, mode, tool_name, arguments, reason=None):
    context = state.get("_event_context")
    if not context:
        return None
    call_id = context.next_call_id()
    data = {"node": node, "call_id": call_id, "tool": tool_name}
    public_reason = _safe_event_text(reason, 240)
    if public_reason:
        data["reason"] = public_reason
    query = _safe_event_text(arguments.get("query"), 500)
    if query:
        data["query"] = query

    pool = state.get("candidate_pools", {}).get("web" if tool_name == "read_web_page" else mode, [])
    candidate_ids = []
    if arguments.get("candidate_id"):
        candidate_ids.append(arguments["candidate_id"])
    candidate_ids.extend(arguments.get("candidate_ids", []) if isinstance(arguments.get("candidate_ids"), list) else [])
    candidate = next((item for item in pool if item.get("candidate_id") in candidate_ids), None)
    if not candidate and tool_name == "read_web_page" and arguments.get("url"):
        candidate = next((item for item in pool if arguments["url"] in {item.get("url"), item.get("original_url")}), None)
    if candidate:
        title = _safe_event_text(candidate.get("title"), 240)
        if title:
            data["title"] = title
        url = _public_event_url(candidate.get("url") or candidate.get("original_url"))
        if url:
            data["url"] = url
    elif tool_name == "read_web_page":
        url = _public_event_url(arguments.get("url"))
        if url:
            data["url"] = url
    context.emit("tool.started", data)
    return call_id


def _tool_sources(mode, tool_name, arguments, result, patch):
    items = []
    if tool_name in {"search_web", "search_papers"}:
        pool_mode = "web" if tool_name == "search_web" else "paper"
        query_results = patch.get("query_results", {}).get(pool_mode, {})
        query = " ".join(str(arguments.get("query", "")).casefold().split())
        items = [entry.get("paper", {}) for entry in query_results.get(query, []) if isinstance(entry, dict)]
    elif tool_name == "collect_evidence" and isinstance(result, list):
        items = result
    elif tool_name == "read_web_page" and isinstance(result, dict):
        candidate_id = result.get("candidate_id")
        items = [
            item for item in patch.get("candidate_pools", {}).get("web", [])
            if item.get("candidate_id") == candidate_id
        ]

    sources = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _safe_event_text(item.get("title"), 240)
        url = _public_event_url(item.get("url") or item.get("original_url"))
        key = (title, url)
        if key == ("", None) or key in seen:
            continue
        seen.add(key)
        source = {}
        if title:
            source["title"] = title
        if url:
            source["url"] = url
        sources.append(source)
        if len(sources) >= 8:
            break
    return sources


def _tool_event_completed(state, node, call_id, mode, tool_name, arguments, result, patch):
    if not call_id:
        return
    data = {"node": node, "call_id": call_id, "tool": tool_name}
    if isinstance(result, dict):
        count = result.get("candidate_count")
        if tool_name == "read_web_page" and count is None:
            count = 1
    else:
        count = len(result) if isinstance(result, list) else None
    if isinstance(count, int) and not isinstance(count, bool):
        data["count"] = count
    sources = _tool_sources(mode, tool_name, arguments, result, patch)
    if sources:
        data["sources"] = sources
    _emit_event(state, "tool.completed", data)


def _candidate_failure_details(state, mode, tool_name, arguments):
    if tool_name not in {"collect_evidence", "read_web_page"}:
        return {}
    field = "candidate_id" if tool_name == "read_web_page" else "candidate_ids"
    if field not in arguments:
        shape, requested = "missing", []
    elif field == "candidate_id":
        shape, requested = "single", [arguments[field]]
    elif isinstance(arguments[field], list):
        shape, requested = "array", arguments[field]
    else:
        shape, requested = "invalid_type", []

    def safe_id(value):
        return value if isinstance(value, str) and re.fullmatch(r"C[0-9a-f]{16}", value) else "<invalid-id>"

    pool = state.get("candidate_pools", {}).get("web" if tool_name == "read_web_page" else mode, [])
    return {
        "id_parameter": shape,
        "requested_count": len(requested),
        "requested_ids": [safe_id(value) for value in requested[:6]],
        "pool_count": len(pool),
        "pool_ids": [safe_id(item.get("candidate_id")) for item in pool[:12]],
    }


def _tool_event_failed(state, node, call_id, mode, tool_name, arguments, exc):
    safe = _tool_error(tool_name, exc)
    if isinstance(exc, ToolCallValidationError):
        details = _candidate_failure_details(state, mode, tool_name, arguments)
        if exc.errors:
            details["errors"] = exc.errors[:8]
        logger.warning(
            "research_tool rejected run_id=%s call_id=%s node=%s tool=%s source=%s code=%s details=%s",
            diagnostic_context().get("run_id", "na"), call_id or "na", node, tool_name, mode, safe["code"],
            json.dumps(details, ensure_ascii=False, separators=(",", ":")),
        )
    if not call_id:
        return
    _emit_event(state, "tool.failed", {
        "node": node,
        "call_id": call_id,
        "tool": tool_name,
        "message": _safe_event_text(safe.get("message"), 240),
    })


def _invoke_tool_with_events(state, mode, tool_name, arguments, *, node="researcher", reason=None):
    call_id = _tool_event_start(state, node, mode, tool_name, arguments, reason)
    try:
        patch, result = _invoke_tool(state, mode, tool_name, arguments)
    except Exception as exc:
        _tool_event_failed(state, node, call_id, mode, tool_name, arguments, exc)
        raise
    _tool_event_completed(state, node, call_id, mode, tool_name, arguments, result, patch)
    return patch, result


def _background_investigator(state, on_stage):
    _emit(on_stage, "background_investigator")
    if state.get("background"):
        return {}
    research = state["research"]
    allowed_modes = _available_modes(research)
    preferred = _preferred_mode(research["source"])
    if preferred not in allowed_modes:
        preferred = allowed_modes[0]
    query_tools = _search_catalog(research, allowed_modes if research["source"] == "auto" else [preferred])
    try:
        query_plan = _model(
            state,
            "literature/background_investigator",
            _payload(
                state, phase="query", available_sources=allowed_modes,
                research_depth=research.get("research_depth", "standard"),
                prefer_primary_evidence=research.get("prefer_primary_evidence", True),
                relevance_filter=research.get("relevance_filter", "standard"),
                background_search_limit=_budget(state)["background_searches"],
            ),
            BackgroundOutput,
            node_role="background_investigator",
            search_mode="none" if research["source"] == "auto" else preferred,
            available_tools=query_tools,
        )
        mode = query_plan.source if research["source"] == "auto" and query_plan.source in allowed_modes else preferred
        queries = [value.strip() for value in query_plan.queries if value.strip()][:_budget(state)["background_searches"]]
    except (ModelServiceError, StructuredOutputError):
        mode = preferred
        queries = []
        state = {**state, "errors": list(state.get("errors", [])) + [_safe_error("background_investigator", "model_failed", "背景检索规划不可用，改用原问题进行有限检索。")]
        }
    if not queries:
        queries = [research["query"][:1_000]]
    pools = {key: list(value) for key, value in state.get("candidate_pools", {"paper": [], "web": []}).items()}
    query_results = state.get("query_results", {})
    errors = list(state.get("errors", []))
    warnings = list(state.get("warnings", []))
    context = _tool_context(research, mode)
    if mode == "paper":
        context = ToolContext(research["project_id"], "openalex", (), network_search=research.get("network_search", True), local_literature=False)
    search_name = "search_web" if mode == "web" else "search_papers"
    available_names = {item["name"] for item in registry.list_tools(context)}
    for query in queries[:_budget(state)["background_searches"]] if search_name in available_names and research["source"] not in {"local", "collection"} else []:
        tool_name = search_name
        arguments = {"query": query, "limit": MAX_BACKGROUND_WEB_RESULTS} if mode == "web" else {"query": query}
        call_id = _tool_event_start(state, "background_investigator", mode, tool_name, arguments)
        try:
            if mode == "web":
                results = registry.invoke(tool_name, arguments, context=context)
            else:
                result = registry.invoke(tool_name, arguments, context=context)
                results = result.get("results", [])
                if result.get("warnings"):
                    warnings.append("paper_search_incomplete")
            search_state = {"candidate_pools": pools, "query_results": query_results}
            pools, query_results = _record_search_results(search_state, mode, query, results)
            _tool_event_completed(
                state,
                "background_investigator",
                call_id,
                mode,
                tool_name,
                arguments,
                {"candidate_count": len(results)},
                {"candidate_pools": pools, "query_results": query_results},
            )
        except (ToolCallValidationError, WebSearchError, WebPageError, RuntimeError) as exc:
            _tool_event_failed(state, "background_investigator", call_id, mode, tool_name, arguments, exc)
            errors.append(_tool_error(tool_name, exc))
    query_state = {**state, "candidate_pools": pools, "query_results": query_results, "errors": errors, "warnings": warnings}
    background_decision = {"source": mode, "queries": queries[:_budget(state)["background_searches"]]}
    candidates = _candidate_summaries(query_state, mode, text_limit=500)
    try:
        selected = _model(
            query_state,
            "literature/background_investigator",
            _payload(query_state, phase="select", search_mode=mode, queries=background_decision["queries"], candidates=candidates),
            BackgroundOutput,
            node_role="background_investigator",
            search_mode=mode,
            available_tools=[],
        )
        valid_ids = {item["candidate_id"] for item in candidates}
        selected_ids = [item for item in selected.selected_candidate_ids if item in valid_ids]
        selected_items = _candidate_summaries(query_state, mode, selected_ids, text_limit=500)
        background = {
            "source": mode,
            "queries": background_decision["queries"],
            "selected_candidates": selected_items,
            "summary": selected.summary,
            "gaps": selected.gaps,
        }
    except (ModelServiceError, StructuredOutputError):
        valid_items = candidates[:5]
        background = {
            "source": mode,
            "queries": background_decision["queries"],
            "selected_candidates": valid_items,
            "summary": "；".join((item.get("title", "未命名来源") + "：" + (item.get("abstract") or item.get("content") or "无摘要"))[:500] for item in valid_items),
            "gaps": ["背景筛选模型不可用，候选仅按检索顺序保留。"],
        }
        query_state["errors"] = list(query_state.get("errors", [])) + [_safe_error("background_investigator", "selection_failed", "背景候选筛选模型不可用，保留实际检索候选。")]
    return {
        "candidate_pools": query_state.get("candidate_pools", {"paper": [], "web": []}),
        "query_results": query_state.get("query_results", {}),
        "background_decision": background_decision,
        "background": background,
        "errors": query_state.get("errors", []),
        "warnings": query_state.get("warnings", []),
    }


def _fallback_plan(state):
    background = state.get("background", {})
    mode = background.get("source") or _preferred_mode(state["research"]["source"])
    return {
        "requirements": [state["research"]["query"][:500]],
        "plan": ["围绕原问题和已有背景检索可核验资料"],
        "source": mode,
        "action": "research",
        "rationale": "使用保守默认计划继续研究。",
    }


def _planner_stop(state, reason, rationale):
    data = dict(state.get("plan") or _fallback_plan(state))
    deep = _depth(state) == "deep"
    core = _clean_labels(state.get("core_requirements") or data.get("requirements") or [state["research"]["query"]]) if deep else []
    if deep:
        data["requirements"] = _clean_labels([*core, *data.get("requirements", [])])
    data["action"] = "report"
    data["completion_confirmed"] = False
    data["rationale"] = rationale
    return {
        "plan": data,
        "current_plan": data.get("plan", []),
        "current_source": data.get("source", _preferred_mode(state["research"]["source"])),
        "core_requirements": core,
        "research_stop_reason": reason,
        "last_completed": "planner",
    }


def _planner(state, on_stage):
    _emit(on_stage, "planner")
    rounds = state.get("rounds", [])
    allowed = _available_modes(state["research"])
    budget = _budget(state)
    if rounds and (
        state.get("research_context_limit_reached")
        or state.get("research_actions_used", 0) >= budget["actions"]
    ):
        context_limited = bool(state.get("research_context_limit_reached"))
        return _planner_stop(
            state,
            "decision_context_budget_exhausted" if context_limited else "tool_budget_exhausted",
            "研究输入预算已达到上限，整理现有材料并说明尚未完成的要求。"
            if context_limited else "研究工具动作预算已达到上限，整理现有材料并说明尚未完成的要求。",
        )
    if len(rounds) >= budget["rounds"]:
        return _planner_stop(
            state,
            "round_budget_exhausted",
            "已达到研究轮次上限；报告将说明资源限制和仍未确认完成的研究要求。",
        )
    core_requirements = (
        _clean_labels(
            state.get("core_requirements")
            or (state.get("plan") or {}).get("requirements", [])
            or ([state["research"]["query"][:500]] if rounds else [])
        )
        if _depth(state) == "deep" else []
    )
    reference_mode = state.get("current_source") or state.get("background", {}).get("source")
    if reference_mode not in allowed:
        reference_mode = "none" if state["research"]["source"] == "auto" else _preferred_mode(state["research"]["source"])
    prompt_id = "literature/planner"
    payload = _payload(
        state,
        round_count=len(rounds),
        core_requirements=core_requirements,
        current_plan=state.get("current_plan", []),
        current_draft=state.get("current_draft", ""),
        current_review=state.get("current_review", {}),
        evidence=_evidence_views(_select_evidence(state), text_limit=240),
        allowed_sources=allowed,
        source_preference=state.get("background", {}).get("source", _preferred_mode(state["research"]["source"])),
        research_depth=state.get("research", {}).get("research_depth", "standard"),
        research_budget=budget,
        analyst_sufficient=_review_is_sufficient(state, state.get("current_review", {})),
        actionable_next_steps=_distinct_next_steps(state, state.get("current_review", {})),
    )
    errors = list(state.get("errors", []))
    try:
        result = _model(
            state,
            prompt_id,
            payload,
            None,
            node_role="planner",
            search_mode=reference_mode,
            available_tools=[],
        )
        body, data = _parse_control(result, PlannerOutput)
        data["body"] = body
        if data["source"] not in allowed:
            data["source"] = state.get("current_source") or state.get("background", {}).get("source") or allowed[0]
        if not data["requirements"]:
            data["requirements"] = core_requirements or [state["research"]["query"][:500]]
        if not data["plan"] and (not rounds or _depth(state) != "deep"):
            data["plan"] = ["围绕原问题补充可核验证据"]
        if not rounds:
            if state["research"]["source"] != "auto":
                data["source"] = _preferred_mode(state["research"]["source"])
            data["action"] = "research"
    except (ModelServiceError, StructuredOutputError):
        data = _fallback_plan(state)
        errors.append(_safe_error("planner", "model_failed", "规划模型不可用，按当前来源继续有限研究。"))

    if _depth(state) == "deep":
        core_requirements = core_requirements or _clean_labels(data.get("requirements", []))
        data["requirements"] = _clean_labels([*core_requirements, *data.get("requirements", [])])
    stop_reason = ""
    if rounds and _depth(state) == "deep":
        review = state.get("current_review", {})
        complete = _review_is_sufficient(state, review)
        next_steps = _distinct_next_steps(state, review)
        planner_steps = _distinct_plan_steps(state, data.get("plan", [])) if data.get("action") == "research" else []
        if complete and data.get("action") == "report" and data.get("completion_confirmed"):
            data["action"] = "report"
            data["rationale"] = "核心研究要求已覆盖，关键证据审查通过且没有尚待解决的重要矛盾，整理现有结果。"
            stop_reason = "sufficient"
        elif complete and planner_steps:
            data["plan"] = planner_steps
            data["action"] = "research"
            data["rationale"] = "分析审查认为主要要求已覆盖；规划器仍识别到新的可执行研究方向，继续补充资料。"
        elif complete:
            data["action"] = "report"
            data["rationale"] = "分析审查认为研究已充分，但规划器没有提出新的可执行方向；报告将保留审慎的完成状态说明。"
            stop_reason = "no_distinct_research_path"
        elif planner_steps or next_steps:
            data["plan"] = planner_steps or next_steps
            data["action"] = "research"
            data["rationale"] = "仍有研究缺口，且存在未尝试的补查方向，继续研究。"
        else:
            data["action"] = "report"
            data["rationale"] = "仍有研究缺口，但没有新的可执行补查方向；报告将明确列出未解决要求和证据限制。"
            stop_reason = "no_distinct_research_path"
    elif rounds and data.get("action") == "report":
        stop_reason = "sufficient" if _review_is_sufficient(state, state.get("current_review", {})) else "planner_reported_incomplete"

    return {
        "plan": data,
        "current_plan": data.get("plan", []),
        "current_source": data.get("source", state.get("current_source") or allowed[0]),
        "core_requirements": core_requirements,
        "research_stop_reason": stop_reason,
        "errors": errors,
        "last_completed": "planner",
    }


def _supervisor(state, on_stage):
    _emit(on_stage, "supervisor")
    completed = state.get("last_completed", "planner")
    rounds = state.get("rounds", [])
    plan = state.get("plan") or _fallback_plan(state)
    if completed == "researcher":
        target = "analyst"
    elif completed == "analyst":
        target = "planner"
    elif completed == "planner":
        target = "researcher"
    else:
        target = "researcher"
    update = {"next_node": target}
    if target == "researcher":
        next_round = len(rounds) + 1
        if state.get("prepared_round", 0) != next_round:
            mode = plan.get("source")
            allowed = _available_modes(state["research"])
            if mode not in allowed:
                mode = state.get("background", {}).get("source", allowed[0])
            update.update({
                "current_source": mode,
                "current_plan": plan.get("plan", []),
                "current_round": next_round,
                "current_draft": "",
                "current_review": {},
                "current_findings": [],
                "tool_results": [],
                "prepared_round": next_round,
            })
    return update


def _retry_query(state, attempted):
    question = state["research"]["query"].strip()
    normalized_attempts = {" ".join(value.casefold().split()) for value in attempted}
    choices = [
        " ".join(state.get("current_plan", [])),
        " ".join((state.get("plan") or {}).get("requirements", [])),
        question,
    ]
    for item in choices:
        value = re.sub(r"\s+", " ", item).strip()[:900]
        if value and " ".join(value.casefold().split()) not in normalized_attempts:
            return value
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*|[\u3400-\u9fff]{2,}", question)
    if len(tokens) > 3:
        shortened = " ".join(tokens[::2])[:900]
        if " ".join(shortened.casefold().split()) not in normalized_attempts:
            return shortened
    if re.search(r"[\u3400-\u9fff]", question):
        qualifiers = ("相关研究", "公开资料", "官方来源") if state.get("current_source") == "web" else ("相关论文", "研究结果", "实验方法")
    else:
        qualifiers = ("related research", "public information", "official sources") if state.get("current_source") == "web" else ("related papers", "research results", "experimental methods")
    expanded = [f"{question} {qualifier}".strip()[:900] for qualifier in qualifiers]
    for value in expanded:
        if " ".join(value.casefold().split()) not in normalized_attempts:
            return value
    return expanded[-1] if expanded else question[:900]


def _force_search_retry(state, mode, tried_queries):
    query = _retry_query(state, tried_queries)
    name = "search_web" if mode == "web" else "search_papers"
    args = {"query": query, "limit": MAX_RESEARCH_WEB_RESULTS} if mode == "web" else {"query": query}
    return name, args


def _web_read_key(state, arguments):
    """Return an in-memory identity for a candidate page slice, without logging it."""
    candidate_id = arguments.get("candidate_id")
    offset = arguments.get("offset", 0)
    if not isinstance(offset, int) or isinstance(offset, bool):
        return None
    pool = state.get("candidate_pools", {}).get("web", [])
    candidate = next((item for item in pool if item.get("candidate_id") == candidate_id), None)
    url = (candidate or {}).get("url") or (candidate or {}).get("original_url")
    url = url or arguments.get("url") or candidate_id
    return (str(url), offset) if url else None


def _log_research_stop(reason, *, failed_reads, tool_actions, consecutive_failures):
    run_id = diagnostic_context().get("run_id", "na")
    logger.info(
        "research_round_stopped run_id=%s reason=%s failed_read_count=%d tool_actions=%d consecutive_failures=%d",
        run_id,
        reason,
        failed_reads,
        tool_actions,
        consecutive_failures,
    )


def _researcher(state, on_stage):
    _emit(on_stage, "researcher")
    mode = state.get("current_source", "paper")
    allowed_modes = _available_modes(state["research"])
    if mode not in allowed_modes:
        mode = allowed_modes[0]
    state = {**state, "candidate_pools": {key: list(value) for key, value in state.get("candidate_pools", {"paper": [], "web": []}).items()}, "tool_results": [], "errors": list(state.get("errors", []))}
    tried_queries = set()
    search_failures = 0
    consecutive_search_failures = 0
    consecutive_web_read_failures = 0
    failed_web_reads = set()
    consecutive_duplicate_reads = 0
    search_attempts = 0
    round_search_queries = []
    deep = _depth(state) == "deep"
    previous_queries = []
    if deep:
        previous_queries = [
            query for item in state.get("rounds", []) if item.get("source") == mode
            for query in item.get("queries", [])
        ]
    action_count = 0
    actions_used = state.get("research_actions_used", 0)
    decision_input_units = state.get("research_decision_input_units", 0)
    budget = _budget(state)
    round_action_limit = budget["actions_per_round"]
    failure_message = (
        "研究决策输入达到估算上下文预算，已停止新增工具调用；以下仅整理已提取的证据。"
        if state.get("research_context_limit_reached")
        else ""
    )
    tools = _helper_catalog(state["research"], mode)
    native_tools = _native_tool_specs(tools)
    local_domains = []
    if any(item["name"] == "select_local_domains" for item in tools):
        from src.tools.local_markdown import list_local_domains
        local_domains = [d for d in list_local_domains(_tool_context(state["research"], mode))[:200] if isinstance(d, str) and len(d) <= 200]
    prompt_tools = [
        {key: item[key] for key in ("name", "description") if key in item}
        for item in tools
    ]
    while (
        native_tools
        and action_count < round_action_limit
        and actions_used < budget["actions"]
        and not state.get("research_context_limit_reached")
    ):
        payload = {
            "question": state["research"]["query"],
            "local_domains": local_domains,
            "source": state["research"]["source"],
            "phase": "act",
            "research_depth": _depth(state),
            "current_round": state.get("current_round", 1),
            "current_source": mode,
            "current_plan": state.get("current_plan", []),
            "requirements": (state.get("plan") or {}).get("requirements", []),
            "selected_candidate_ids": _background_summary_for_model(state.get("background", {})).get("selected_candidate_ids", []),
            "current_candidates": _candidate_summaries(
                state, mode, limit=MAX_CANDIDATE_CONTEXT, text_limit=240,
            ),
            "evidence": _evidence_views(_select_evidence(state), text_limit=240),
            "tool_results": _compact_tool_results(state.get("tool_results", [])),
            "tool_feedback": state.get("errors", [])[-4:],
            "calls_remaining": min(
                round_action_limit - action_count,
                budget["actions"] - actions_used,
            ),
            "search_attempts": search_attempts,
            "search_failures": search_failures,
            "prefer_primary_evidence": state.get("research", {}).get("prefer_primary_evidence", True),
            "relevance_filter": state.get("research", {}).get("relevance_filter", "standard"),
        }
        if deep:
            payload["attempted_queries"] = [query[:200] for query in (previous_queries + round_search_queries)[-8:]]
        template_context = _model_template_context(state, mode, "researcher", prompt_tools)
        input_units = structured_input_units(
            "literature/researcher",
            payload,
            ResearchActionOutput,
            template_context=template_context,
            native_tools=native_tools,
        )
        projected_input_units = decision_input_units + input_units
        if (
            input_units >= budget["decision_input_units"]
            or projected_input_units >= budget["cumulative_decision_input_units"]
        ):
            state["research_context_limit_reached"] = True
            state["errors"].append(_safe_error(
                "researcher",
                "decision_context_budget_reached",
                "研究决策输入达到本机设置的估算上下文预算，停止新增工具调用。",
            ))
            failure_message = "研究决策输入达到估算上下文预算，已停止新增工具调用；以下仅整理已提取的证据。"
            _log_research_stop(
                "decision_context_budget",
                failed_reads=len(failed_web_reads),
                tool_actions=action_count,
                consecutive_failures=0,
            )
            break
        # Charge the estimated request before invoking the provider so a
        # failed model call cannot make the cumulative decision budget appear
        # available again.
        decision_input_units = projected_input_units
        state["research_decision_input_units"] = decision_input_units
        try:
            result = _model(
                state,
                "literature/researcher",
                payload,
                ResearchActionOutput,
                node_role="researcher",
                search_mode=mode,
                available_tools=prompt_tools,
                native_tools=native_tools,
            )
        except (ModelServiceError, StructuredOutputError):
            state["errors"].append(_safe_error("researcher", "model_failed", "研究模型不可用，保留已收集证据并继续生成缺口说明。"))
            failure_message = "研究模型不可用，未能完成本轮初稿。"
            break
        if result.action == "finish":
            # A failed search is feedback, not a stop condition. Give the model a
            # chance to rewrite the query; if it finishes immediately, make a
            # bounded code-generated rewrite while budget remains.
            if (
                search_failures
                and search_attempts < 3
                and action_count < round_action_limit
                and actions_used < budget["actions"]
                and not state.get("research_context_limit_reached")
            ):
                name, args = _force_search_retry(state, mode, tried_queries)
                action_count += 1
                actions_used += 1
                state["research_actions_used"] = actions_used
                search_attempts += 1
                tried_queries.add(" ".join(args["query"].casefold().split()))
                round_search_queries.append(args["query"])
                try:
                    patch, _ = _invoke_tool_with_events(
                        state,
                        mode,
                        name,
                        args,
                        reason="改写查询以恢复失败的检索。",
                    )
                    state.update(patch)
                    consecutive_search_failures = 0
                    consecutive_web_read_failures = 0
                    consecutive_duplicate_reads = 0
                except (ToolCallValidationError, WebSearchError, WebPageError, RuntimeError) as exc:
                    search_failures += 1
                    state["errors"].append(_tool_error(name, exc))
                    consecutive_search_failures += 1
                    if consecutive_search_failures >= 2:
                        failure_message = "连续检索失败，本轮停止重试并保留已收集资料。"
                        _log_research_stop(
                            "consecutive_search_failures",
                            failed_reads=len(failed_web_reads),
                            tool_actions=action_count,
                            consecutive_failures=consecutive_search_failures,
                        )
                        break
                continue
            # The act phase carries compact evidence previews. Rebuild any finish
            # answer from the full evidence in the dedicated draft phase below.
            break
        if state.get("research_context_limit_reached"):
            failure_message = "研究决策输入达到估算上下文预算，已停止新增工具调用；以下仅整理已提取的证据。"
            break
        if result.tool_name == "read_web_page":
            read_key = _web_read_key(state, result.arguments)
            if read_key in failed_web_reads or read_key in set(state.get("read_web_pages", [])):
                action_count += 1
                actions_used += 1
                state["research_actions_used"] = actions_used
                state["errors"].append(_safe_error("researcher", "duplicate_web_read_skipped", "已跳过同一网页候选与字符区间的重复读取请求。"))
                consecutive_duplicate_reads += 1
                if consecutive_web_read_failures:
                    consecutive_web_read_failures += 1
                    failure_message = "网页内容连续读取失败，已停止本轮检索并保留已收集资料。"
                    _log_research_stop(
                        "consecutive_web_read_failures",
                        failed_reads=len(failed_web_reads),
                        tool_actions=action_count,
                        consecutive_failures=consecutive_web_read_failures,
                    )
                    break
                if consecutive_duplicate_reads >= 2:
                    failure_message = "网页读取重复请求已停止，本轮保留已收集资料。"
                    _log_research_stop(
                        "duplicate_web_read",
                        failed_reads=len(failed_web_reads),
                        tool_actions=action_count,
                        consecutive_failures=consecutive_duplicate_reads,
                    )
                    break
                continue
        action_count += 1
        actions_used += 1
        state["research_actions_used"] = actions_used
        try:
            if result.tool_name in {"search_web", "search_papers"}:
                search_attempts += 1
                query = result.arguments.get("query")
                if isinstance(query, str):
                    tried_queries.add(" ".join(query.casefold().split()))
                    round_search_queries.append(query)
            patch, _ = _invoke_tool_with_events(
                state,
                mode,
                result.tool_name,
                result.arguments,
                reason=result.reason,
            )
            state.update(patch)
            consecutive_search_failures = 0
            consecutive_web_read_failures = 0
            consecutive_duplicate_reads = 0
        except (ToolCallValidationError, WebSearchError, WebPageError, RuntimeError) as exc:
            error = _tool_error(result.tool_name, exc)
            state["errors"].append(error)
            if result.tool_name == "read_web_page":
                read_key = _web_read_key(state, result.arguments)
                if read_key is not None:
                    failed_web_reads.add(read_key)
                consecutive_web_read_failures += 1
                if consecutive_web_read_failures >= 2:
                    failure_message = "网页内容连续读取失败，已停止本轮检索并保留已收集资料。"
                    _log_research_stop(
                        "consecutive_web_read_failures",
                        failed_reads=len(failed_web_reads),
                        tool_actions=action_count,
                        consecutive_failures=consecutive_web_read_failures,
                    )
                    break
            elif result.tool_name in {"search_web", "search_papers"}:
                search_failures += 1
                consecutive_search_failures += 1
                if consecutive_search_failures >= 2:
                    failure_message = "连续检索失败，本轮停止重试并保留已收集资料。"
                    _log_research_stop(
                        "consecutive_search_failures",
                        failed_reads=len(failed_web_reads),
                        tool_actions=action_count,
                        consecutive_failures=consecutive_search_failures,
                    )
                    break
    allowed_papers = _allowed_report_papers(state)
    working = _visible_report_evidence(_select_evidence(state), allowed_papers)
    finalization_payload = {
        "question": state["research"]["query"],
        "source": state["research"]["source"],
        "phase": "draft",
        "research_depth": _depth(state),
        "current_round": state.get("current_round", 1),
        "current_source": mode,
        "current_plan": state.get("current_plan", []),
        "current_candidates": _candidate_summaries(
            state, mode, limit=4, text_limit=400,
        ),
        "evidence": _evidence_views(working),
        "plan_body": (state.get("plan") or {}).get("body", ""),
        "tool_results": _compact_tool_results(state.get("tool_results", [])),
        "tool_feedback": state.get("errors", [])[-6:],
        "calls_remaining": 0,
        "prefer_primary_evidence": state.get("research", {}).get("prefer_primary_evidence", True),
        "relevance_filter": state.get("research", {}).get("relevance_filter", "standard"),
    }
    try:
        draft = _model(
            state,
            "literature/researcher",
            finalization_payload,
            None,
            node_role="researcher",
            search_mode=mode,
            available_tools=[],
        )
    except (ModelServiceError, StructuredOutputError):
        state["errors"].append(_safe_error("researcher", "draft_failed", "初稿整理模型不可用，保留证据并说明缺口。"))
        failure_message = "未能生成本轮初稿；当前证据和来源仍需人工核对。"
        draft = ""
    verified = validate_markdown(draft, working, allowed_paper_ids=allowed_papers)
    if verified["rejected"]:
        state["errors"].append(_safe_error("researcher", "claim_rejected", "引用、数值或范围检查移除了部分草稿内容。"))
    safe_draft = verified["content"]
    if not safe_draft and failure_message:
        safe_draft = f"> 局限：{failure_message}"
    evidence_pool = _unique_evidence(state.get("evidence_pool", []))
    return {
        "candidate_pools": state.get("candidate_pools", {}),
        "query_results": state.get("query_results", {}),
        "evidence_pool": evidence_pool,
        "evidence": evidence_pool,
        "current_draft": safe_draft,
        "current_findings": verified["findings"],
        "tool_results": state.get("tool_results", []),
        "research_actions_used": state.get("research_actions_used", 0),
        "research_decision_input_units": state.get("research_decision_input_units", 0),
        "round_tool_actions": action_count,
        "round_search_queries": round_search_queries,
        "research_context_limit_reached": state.get("research_context_limit_reached", False),
        "errors": state.get("errors", []),
        "warnings": state.get("warnings", []),
        "last_completed": "researcher",
        "collected_web_blocks": state.get("collected_web_blocks", []),
        "read_web_pages": state.get("read_web_pages", []),
        "disclosed_local_documents": state.get("disclosed_local_documents", []),
    }


def _analyst(state, on_stage, on_subtask):
    _emit(on_stage, "analyst")
    mode = state.get("current_source", "paper")
    draft = state.get("current_draft", "")
    expanded = _depth(state) == "deep"
    evidence = _select_evidence(state, _claim_ids(draft) | _findings_ids(state.get("current_findings", [])), expanded=expanded)
    allowed_papers = set(state["research"].get("paper_ids") or []) if state["research"]["source"] == "collection" else None
    payload = _payload(
        state,
        current_round=state.get("current_round", 1),
        current_source=mode,
        current_plan=state.get("current_plan", []),
        current_draft=draft,
        tool_results=state.get("tool_results", [])[-8:],
        expanded_evidence=expanded,
        research_depth=_depth(state),
        evidence=_evidence_views(evidence),
        requirements=(state.get("core_requirements") or (state.get("plan") or {}).get("requirements", [])),
        core_requirements=state.get("core_requirements", []),
        current_findings=state.get("current_findings", []),
    )
    if expanded:
        for partial, previous in zip(payload["rounds"], state.get("rounds", [])):
            partial["review"]["unresolved_contradictions"] = previous.get("review", {}).get("unresolved_contradictions", [])
    try:
        for attempt in (1, 2):
            result = _model(state, "literature/analyst", payload, None, node_role="analyst", search_mode=mode, available_tools=[])
            try:
                body, review = _parse_control(result, AnalystOutput)
            except StructuredOutputError:
                logger.warning("analyst_control_invalid run_id=%s attempt=%d", diagnostic_context().get("run_id", "na"), attempt)
                if attempt == 2:
                    raise
                payload = {**payload, "retry_reason": "invalid_analyst_control"}
            else:
                break
        review["review"] = body
    except (ModelServiceError, StructuredOutputError) as exc:
        service_failed = isinstance(exc, ModelServiceError)
        reason = "审查模型服务不可用" if service_failed else "审查输出未通过格式检查"
        code = "model_failed" if service_failed else "invalid_output"
        review = {
            "sufficient": False,
            "covered_requirements": [],
            "gaps": [f"{reason}，当前初稿和证据尚未完成语义审查。"],
            "next_steps": ["针对原问题补充检索并复核现有资料。"],
            "review": f"{reason}。",
        }
        state["errors"] = list(state.get("errors", [])) + [_safe_error("analyst", code, f"{reason}，保留缺口后继续。")]
    evidence_by_id = {item["id"]: item for item in state.get("evidence_pool", [])}
    findings = []
    for claim in state.get("current_findings", []):
        _, reason = validate_claim(claim.get("text", ""), claim.get("citations", []), evidence_by_id, allowed_papers)
        if not reason:
            findings.append(claim)
    current_findings = findings
    if expanded and not _review_is_sufficient(state, review):
        review["sufficient"] = False
    evidence_ids = set(_claim_ids(draft)) | _findings_ids(current_findings)
    used = [item for item in state.get("evidence_pool", []) if item.get("id") in evidence_ids]
    candidate_count = len(state.get("candidate_pools", {}).get(mode, []))
    status = "findings" if current_findings else ("sources_only" if candidate_count or evidence else "no_evidence")
    task_text = "；".join(state.get("current_plan", []))[:1_000]
    note_sources = [
        {key: item.get(key) for key in ("id", "title", "original_url", "source_level")}
        for item in used
    ]
    note = {
        "index": state.get("current_round", len(state.get("rounds", [])) + 1),
        "source": mode,
        "task": task_text or state["research"]["query"],
        "status": status,
        "findings": current_findings,
        "gap": "；".join(review.get("gaps", [])[:5]),
        "sources": note_sources,
        "evidence": [item.get("id") for item in used],
    }
    if on_subtask:
        on_subtask(note)
    round_item = {
        "index": note["index"],
        "source": mode,
        "plan": list(state.get("current_plan", [])),
        "draft": draft,
        "plan_body": (state.get("plan") or {}).get("body", ""),
        "review": review,
        "findings": current_findings,
        "candidate_ids": [item["candidate_id"] for item in state.get("candidate_pools", {}).get(mode, [])],
        "evidence_ids": [item["id"] for item in state.get("evidence_pool", [])],
        "status": status,
        "tool_actions": state.get("round_tool_actions", 0),
        "queries": list(state.get("round_search_queries", [])),
    }
    rounds = list(state.get("rounds", [])) + [round_item]
    return {"current_review": review, "current_findings": current_findings, "rounds": rounds, "errors": state.get("errors", []), "last_completed": "analyst"}


def _allowed_report_papers(state):
    if state["research"]["source"] == "collection":
        return set(state["research"].get("paper_ids") or [])
    return None


def _visible_report_evidence(items, allowed_papers):
    visible = []
    for item in items:
        if allowed_papers is not None and item.get("paper_id") not in allowed_papers:
            continue
        text = str(item.get("text", ""))
        entry = {**item, "text": text[:1_200]}
        start, end = item.get("char_start"), item.get("char_end")
        if len(text) > 1_200 and isinstance(start, int) and isinstance(end, int):
            entry["char_end"] = min(end, start + len(entry["text"]))
        visible.append(entry)
    return visible


def _publish_report(verified, caveats=()):
    notes = ["> " + note for note in dict.fromkeys(caveats) if note]
    body = "\n\n".join(part for part in [verified["content"], *notes] if part)
    return {**verified, "content": numbered_references(body, verified["evidence"])}


def _outstanding_gaps(state, *, fallback=False):
    rounds = state.get("rounds", [])
    review = state.get("current_review") or (rounds[-1].get("review", {}) if rounds else {})
    if _depth(state) != "deep":
        gaps = list(review.get("gaps", []))
        if fallback and review and not review.get("sufficient"):
            gaps.extend(review.get("next_steps", []))
        if fallback and not review:
            gaps.extend(state.get("background", {}).get("gaps", []))
        return _clean_labels(gaps, limit=5 if fallback else 24)
    gaps = [
        *(f"核心要求尚未覆盖：{item}" for item in _core_requirement_gaps(state, review)),
        *(f"尚未解决的重要矛盾：{item}" for item in review.get("unresolved_contradictions", [])),
        *review.get("gaps", []),
    ]
    if review and not review.get("evidence_reliability_confirmed"):
        gaps.append("现有证据的可靠性或充分性尚未确认。")
    if review and not review.get("important_conclusions_cross_validated"):
        gaps.append("重要结论尚未完成交叉验证。")
    return _clean_labels(gaps, limit=40)


def _reporter_fallback(state):
    allowed = _allowed_report_papers(state)
    evidence = _visible_report_evidence(state.get("evidence_pool", []), allowed)
    rounds = state.get("rounds", [])
    review = state.get("current_review") or (rounds[-1].get("review", {}) if rounds else {})
    drafts = [item.get("draft", "") for item in rounds]
    current_draft = state.get("current_draft", "")
    if current_draft and (not drafts or current_draft != drafts[-1]):
        drafts.append(current_draft)
    limitations = None
    for index, draft in enumerate(reversed(drafts)):
        checked = validate_markdown(draft, evidence, allowed_paper_ids=allowed)
        if checked["evidence"]:
            return _publish_report(checked, _warning_caveats(state, fallback=True))
        if index == 0 and not review.get("sufficient") and any(
            line.lstrip().startswith("> 局限") for line in checked["content"].splitlines()
        ):
            limitations = checked
    checked = limitations or {"content": "现有资料不足以形成有证据支持的结论。", "evidence": [], "rejected": []}
    return _publish_report(checked, _warning_caveats(state, fallback=True))


def _log_report_validation(*, input_evidence_count, final_evidence_count, rejected_claim_count, fallback_rejected_count, fallback_reason):
    run_id = diagnostic_context().get("run_id", "na")
    level = logging.WARNING if fallback_reason != "none" or rejected_claim_count or fallback_rejected_count else logging.INFO
    logger.log(
        level,
        "report_validation run_id=%s input_evidence_count=%d final_evidence_count=%d rejected_claim_count=%d fallback_rejected_count=%d fallback_reason=%s",
        run_id,
        input_evidence_count,
        final_evidence_count,
        rejected_claim_count,
        fallback_rejected_count,
        fallback_reason,
    )


def _reporter_recovery(state, *, reason_code, input_evidence_count, rejected_claim_count=0):
    result = _reporter_fallback(state)
    _log_report_validation(
        input_evidence_count=input_evidence_count,
        final_evidence_count=len(result["evidence"]),
        rejected_claim_count=rejected_claim_count,
        fallback_rejected_count=len(result.get("rejected", [])),
        fallback_reason=reason_code,
    )
    return result


def _warning_caveats(state, *, fallback=False):
    caveats = []
    if "paper_search_incomplete" in state.get("warnings", []):
        caveats.append("论文检索的部分外部来源失败，已保留可用结果，资料可能不完整。")
    if state.get("research_context_limit_reached"):
        caveats.append("研究决策输入达到本机估算上下文预算，已停止新增工具调用。本报告基于现有材料，收束原因是预算耗尽。")
    elif state.get("research_actions_used", 0) >= _budget(state)["actions"]:
        caveats.append("研究工具总预算已用完。本报告基于现有材料，收束原因是预算耗尽。")
    stop_reason = state.get("research_stop_reason")
    if stop_reason == "round_budget_exhausted":
        caveats.append("研究轮次达到上限。本报告基于现有材料，收束原因是预算耗尽。")
    elif stop_reason == "no_distinct_research_path":
        caveats.append("当前没有新的可执行补查方向，研究充分性仍未确认。")
    elif stop_reason == "planner_reported_incomplete":
        caveats.append("本次研究已收束，现有证据尚未确认覆盖所有要求。")
    if fallback or stop_reason != "sufficient":
        caveats.extend(
            gap for gap in _outstanding_gaps(state, fallback=fallback)
            if not validate_claim(gap, [], {}, allow_uncited=True, is_limitation=True)[1]
        )
    return caveats


def _reporter_evidence(state, pool):
    """Balance bounded report input across rounds and sources, without lexical ranking."""
    limit = MAX_REPORT_EVIDENCE_CONTEXT
    if len(pool) <= limit:
        return pool
    by_id = {item['id']: item for item in pool}
    def diverse(items):
        sources = {}
        prefer_primary = (state.get('research') or {}).get('prefer_primary_evidence', True)
        for item in sorted(items, key=lambda value: -_primary_evidence_score(value) if prefer_primary else 0):
            key = item.get('candidate_id') or item.get('paper_id') or item.get('original_url') or item.get('title') or item['id']
            sources.setdefault(key, []).append(item)
        result = []
        while any(sources.values()):
            for entries in sources.values():
                if entries:
                    result.append(entries.pop(0))
        return result
    seen = set()
    groups = []
    for round_item in state.get('rounds', []):
        identifiers = [identifier for identifier in round_item.get('evidence_ids', [])
                       if identifier in by_id and identifier not in seen]
        seen.update(identifiers)
        cited = _findings_ids(round_item.get('findings', [])) | _claim_ids(round_item.get('draft', {}))
        # Alternate cited and uncited material, giving both a place in every round.
        buckets = [[], []]
        for identifier in identifiers:
            item = by_id[identifier]
            buckets[0 if identifier in cited else 1].append(item)
        buckets = [diverse(bucket) for bucket in buckets]
        group = []
        while any(buckets):
            for bucket in buckets:
                if bucket:
                    group.append(bucket.pop(0))
        if group:
            groups.append(group)
    remaining = [item for item in pool if item['id'] not in seen]
    if remaining:
        groups.append(diverse(remaining))
    selected = []
    # Latest rounds first at each pass, so follow-up evidence cannot be crowded out.
    while len(selected) < limit and any(groups):
        for group in reversed(groups):
            if group and len(selected) < limit:
                selected.append(group.pop(0))
    return selected


def _reporter(state, on_stage):
    _emit(on_stage, "reporter")
    all_evidence = state.get("evidence_pool", [])
    allowed = _allowed_report_papers(state)
    all_evidence = [item for item in all_evidence if allowed is None or item.get("paper_id") in allowed]
    all_evidence = _unique_evidence(all_evidence)
    selected = _reporter_evidence(state, all_evidence)
    working = _visible_report_evidence(selected, allowed)
    payload = _payload(
        state,
        expanded_evidence=True,
        source_scope=state["research"]["source"],
        rounds=_rounds_for_model(
            state.get("rounds", []),
            include_drafts=True,
            visible_evidence=working,
            evidence_limit=_evidence_context_limits(state, expanded=True)[0],
        ),
        evidence=_evidence_views(working),
        evidence_omitted_count=len(all_evidence) - len(working),
        final_requirements=(state.get("plan") or {}).get("requirements", []),
        outstanding_gaps=_outstanding_gaps(state),
        research_stop_reason=state.get("research_stop_reason", ""),
        prefer_primary_evidence=state.get("research", {}).get("prefer_primary_evidence", True),
        errors=state.get("errors", []),
    )
    errors = list(state.get("errors", []))
    checked = None
    suggestion = "请稍后重试，或更换模型后重试。"
    try:
        result = _model(state, "literature/reporter", payload, None, node_role="reporter",
                        search_mode=_report_mode(state), available_tools=[])
        checked = validate_markdown(result, working, allowed_paper_ids=allowed, citation_only=True)
    except ModelServiceError as exc:
        reason = {
            "timeout": "报告生成超时，未能完成终稿。",
            "network": "无法连接报告模型服务，未能完成终稿。",
            "rate_limit": "报告模型服务触发限流，可能是请求过于频繁或额度不足，未能完成终稿。",
            "auth": "报告模型服务的 API 密钥或访问权限不可用，未能完成终稿。",
            "model_not_found": "报告模型不存在或无法访问，未能完成终稿。",
            "not_found": "报告模型服务地址或模型不可用，未能完成终稿。",
            "bad_request": "报告模型服务无法处理本次请求，请检查模型设置。",
            "context_length": "报告内容超出模型的输入长度限制，未能完成终稿。",
        }.get(exc.category, "报告模型服务暂时不可用，未能完成终稿。")
        suggestion = {
            "timeout": "请稍后重试，或降低研究深度后重试。",
            "network": "请检查模型服务地址和网络连接后重试。",
            "rate_limit": "请检查服务额度，等待一段时间后重试。",
            "auth": "请检查 API 密钥和模型访问权限后重试。",
            "model_not_found": "请检查手动填写的模型 ID 及访问权限，或选择其他模型。",
            "not_found": "请检查模型服务地址和手动填写的模型 ID。",
            "bad_request": "请检查模型设置，或更换支持所需功能的模型。",
            "context_length": "请缩短问题或降低研究深度后重试。",
        }.get(exc.category, suggestion)
        reason_code = "model_service_failed"
        errors.append(_safe_error("reporter", reason_code, reason))
    except StructuredOutputError as exc:
        reason = {
            "length": "报告生成达到长度限制，未能完成终稿。",
            "filtered": "报告内容受到模型服务限制，未能完成终稿。",
            "empty": "报告模型返回了空内容，未能完成终稿。",
        }.get(exc.category, "报告模型返回的内容无法使用，未能完成终稿。")
        suggestion = {
            "length": "请缩短问题或降低研究深度后重试。",
            "filtered": "请调整问题表述或更换模型后重试。",
            "empty": "请稍后重试，或更换模型。",
        }.get(exc.category, suggestion)
        reason_code = "invalid_report_output"
        errors.append(_safe_error("reporter", "invalid_output", reason))
    else:
        reason = "终稿没有可保留的正文。"
        reason_code = "no_valid_report_body"

    if checked and checked["has_body"]:
        caveats = _warning_caveats(state)
        if not working:
            caveats.append("本次没有可核查证据，相关判断仍待验证。")
        if len(all_evidence) > len(working):
            caveats.append("累计证据超出终稿上下文预算，本报告基于其中部分证据。")
        if checked["rejected"]:
            caveats.append("部分内容因引用无效或格式不安全而省略。")
        report = _publish_report(checked, caveats)
        rejected = report["rejected"]
        _log_report_validation(
            input_evidence_count=len(working),
            final_evidence_count=len(report["evidence"]),
            rejected_claim_count=len(rejected),
            fallback_rejected_count=0,
            fallback_reason="none",
        )
        fallback = False
    else:
        rejected = checked["rejected"] if checked else []
        _emit_event(state, "report.fallback", {"node": "reporter", "message": reason, "suggestion": suggestion})
        report = _reporter_recovery(
            state, reason_code=reason_code,
            input_evidence_count=len(working), rejected_claim_count=len(rejected),
        )
        rejected = rejected + report["rejected"]
        fallback = True
    return {
        "content": report["content"],
        "final_evidence": report["evidence"],
        "final_rejections": rejected,
        "report_fallback": fallback,
        "errors": errors,
        "last_completed": "reporter",
    }


def _dispatch(state, on_stage):
    _emit(on_stage, "dispatcher")
    research = state["research"]
    research_mode = research.get("research_mode", "auto")
    if research_mode == "deep":
        return {"needs_research": True}
    if research_mode != "quick" and research["source"] == "collection":
        return {"needs_research": True}
    try:
        result = _model(
            state,
            "runtime/dispatcher",
            _payload(state, allowed_sources=_source_modes(research["source"])),
            DispatcherOutput,
            node_role="dispatcher",
            search_mode="none",
            available_tools=[],
        )
        if research_mode == "quick":
            if not result.answer.strip():
                raise StructuredOutputError("快速探索模式未返回直接答案")
            return {"needs_research": False, "content": result.answer, "evidence": []}
        if result.needs_research:
            return {"needs_research": True}
        return {"needs_research": False, "content": result.answer, "evidence": []}
    except (ModelServiceError, StructuredOutputError):
        if research_mode == "quick":
            raise
        return {
            "needs_research": True,
            "errors": list(state.get("errors", [])) + [_safe_error("dispatcher", "model_failed", "路由模型不可用，按有限研究流程继续。")],
        }


def _node_completion_data(node, state, patch):
    data = {"node": node}
    if node == "background_investigator":
        background = patch.get("background", state.get("background", {}))
        data["source"] = background.get("source", "paper")
        count = len(background.get("selected_candidates", []))
        data["message"] = f"已筛选 {count} 条背景候选。"
    elif node == "planner":
        plan = patch.get("plan", state.get("plan", {}))
        action = plan.get("action")
        if action in {"research", "report"}:
            data["action"] = action
        source = plan.get("source")
        if source in {"web", "paper"}:
            data["source"] = source
            data["route"] = source
        rationale = _safe_event_text(plan.get("rationale"), 240)
        if rationale:
            data["message"] = rationale
        if plan.get("body") and not patch.get("research_stop_reason", "").endswith("_exhausted"):
            data["body"] = plan["body"][:8_000]
    elif node == "supervisor":
        target = patch.get("next_node", "researcher")
        data["message"] = f"已安排 {target} 节点继续。"
    elif node == "researcher":
        data["source"] = state.get("current_source", "paper")
        evidence = patch.get("evidence_pool", state.get("evidence_pool", []))
        data["message"] = f"已整理 {len(evidence)} 条证据，提交本轮审查。"
    elif node == "analyst":
        review = patch.get("current_review", state.get("current_review", {}))
        data["source"] = state.get("current_source", "paper")
        data["sufficient"] = bool(review.get("sufficient"))
        gaps = [item for item in review.get("gaps", []) if isinstance(item, str) and item.strip()]
        data["message"] = _safe_event_text(gaps[0], 240) if gaps else ("已覆盖主要要求。" if review.get("sufficient") else "仍需补充证据。")
        if review.get("review"):
            data["body"] = review["review"][:8_000]
    elif node == "reporter":
        evidence = patch.get("final_evidence", state.get("final_evidence", []))
        if patch.get("report_fallback"):
            data["message"] = "终稿未能完成，已返回最近一份可用研究草稿。" if evidence else "证据不足，已返回已知缺口。"
        else:
            data["message"] = f"已完成终稿整理与引用校验，保留 {len(evidence)} 条引用证据。"
    return data


def _run_node_with_events(state, node, callback):
    _emit_event(state, "node.started", {"node": node})
    patch = callback(state)
    _emit_event(state, "node.completed", _node_completion_data(node, state, patch))
    return patch


def _after_dispatch(state):
    if not state.get("needs_research"):
        return END
    if not state["research"].get("network_search", True):
        return "planner"
    return "background_investigator"


def _graph(on_stage, on_subtask):
    graph = StateGraph(State)
    graph.add_node("dispatcher", lambda state: _dispatch(state, on_stage))
    graph.add_node("background_investigator", lambda state: _run_node_with_events(state, "background_investigator", lambda current: _background_investigator(current, on_stage)))
    graph.add_node("planner", lambda state: _run_node_with_events(state, "planner", lambda current: _planner(current, on_stage)))
    graph.add_node("supervisor", lambda state: _run_node_with_events(state, "supervisor", lambda current: _supervisor(current, on_stage)))
    graph.add_node("researcher", lambda state: _run_node_with_events(state, "researcher", lambda current: _researcher(current, on_stage)))
    graph.add_node("analyst", lambda state: _run_node_with_events(state, "analyst", lambda current: _analyst(current, on_stage, on_subtask)))
    graph.add_node("reporter", lambda state: _run_node_with_events(state, "reporter", lambda current: _reporter(current, on_stage)))
    graph.add_edge(START, "dispatcher")
    graph.add_conditional_edges(
        "dispatcher",
        _after_dispatch,
        {"background_investigator": "background_investigator", "planner": "planner", END: END},
    )
    graph.add_edge("background_investigator", "planner")
    graph.add_conditional_edges(
        "planner",
        lambda state: "reporter" if len(state.get("rounds", [])) >= _budget(state)["rounds"] or (state.get("rounds") and (state.get("plan") or {}).get("action") == "report") else "supervisor",
        {"reporter": "reporter", "supervisor": "supervisor"},
    )
    graph.add_conditional_edges(
        "supervisor",
        lambda state: state["next_node"],
        {"researcher": "researcher", "analyst": "analyst", "planner": "planner"},
    )
    graph.add_edge("researcher", "supervisor")
    graph.add_edge("analyst", "supervisor")
    graph.add_edge("reporter", END)
    return graph.compile()


def _safe_history(history, source):
    if source == "collection":
        return []
    return list(history or [])


def run(research, *, history, on_stage=None, on_subtask=None, on_event=None):
    """Run the research graph and return the final Markdown and cited evidence."""
    if not isinstance(research, dict):
        raise TypeError("research must be a dict")
    required = {"query", "project_id", "source"}
    missing = required - set(research)
    if missing:
        raise ValueError("research input is missing required fields")
    if research.get("research_mode", "auto") not in {"auto", "deep", "quick"}:
        raise ValueError("research_mode must be auto, deep, or quick")
    if research.get("research_depth", "standard") not in DEPTH_BUDGETS:
        raise ValueError("research_depth must be light, standard, or deep")
    if not isinstance(research.get("prefer_primary_evidence", True), bool):
        raise ValueError("prefer_primary_evidence must be a boolean")
    if research.get("relevance_filter", "standard") not in {"relaxed", "standard", "strict"}:
        raise ValueError("relevance_filter must be relaxed, standard, or strict")
    initial = {
        "research": dict(research),
        "history": _safe_history(history, research["source"]),
        "background": {},
        "plan": {},
        "current_plan": [],
        "current_source": "",
        "current_round": 0,
        "current_draft": "",
        "current_review": {},
        "candidate_pools": {"paper": [], "web": []},
        "query_results": {},
        "collected_web_blocks": [],
        "read_web_pages": [],
        "evidence_pool": [],
        "evidence": [],
        "rounds": [],
        "tool_results": [],
        "research_actions_used": 0,
        "research_decision_input_units": 0,
        "research_context_limit_reached": False,
        "errors": [],
        "warnings": [],
        "prepared_round": 0,
    }
    if on_event is not None:
        initial["_event_context"] = _EventContext(on_event)
    graph = _graph(on_stage, on_subtask)
    result = graph.invoke(initial, config={"recursion_limit": 40})
    if result.get("content") is None:
        _emit_event(initial, "report.fallback", {"node": "reporter", "message": "研究流程未能形成完整报告。", "suggestion": "请稍后重试，或降低研究深度后重试。"})
        fallback = _reporter_fallback(result)
        return fallback["content"], fallback["evidence"]
    return result["content"], result.get("final_evidence", result.get("evidence", []))
