"""Two-round evidence workflow expressed as a single LangGraph state graph."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Literal, TypedDict
from urllib.parse import urlsplit, urlunsplit

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.agents.report import reference, validate_claim
from src.integrations.web_page import WebPageError
from src.integrations.web_search import WebSearchError
from src.integrations.model import ModelServiceError, StructuredOutputError, structured
from src.infrastructure.diagnostics import diagnostic_context
from src.literature.keywords import relevance_score, relevance_terms
from src.literature.retrieval import _candidate_ids, rank_candidates
from src.tools import ToolContext, ToolRegistry
from src.tools.registry import ToolCallValidationError

logger = logging.getLogger("tufa.agent")
registry = ToolRegistry()

MAX_ROUNDS = 2
MAX_RESEARCH_ACTIONS = 8
MAX_BACKGROUND_SEARCHES = 3
MAX_CANDIDATE_CONTEXT = 20
MAX_EVIDENCE_CONTEXT = 18
MAX_EVIDENCE_TEXT = 21_600
MAX_CANDIDATE_TEXT = 1_600


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1_500)
    citations: list[str] = Field(default_factory=list, max_length=12)


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(min_length=1, max_length=150)
    paragraphs: list[Claim] = Field(default_factory=list, max_length=8)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[Section] = Field(default_factory=list, max_length=8)
    limitations: list[Claim] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def bounded_claims(self):
        if sum(len(section.paragraphs) for section in self.sections) + len(self.limitations) > 24:
            raise ValueError("草稿段落数量超限")
        return self


class ReporterOutput(Draft):
    """Structured final report payload; same paragraph/citation contract as a draft."""


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
    queries: list[str] = Field(default_factory=list, max_length=3)
    selected_candidate_ids: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(default="", max_length=5_000)
    gaps: list[str] = Field(default_factory=list, max_length=8)


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirements: list[str] = Field(default_factory=list, max_length=12)
    plan: list[str] = Field(default_factory=list, max_length=8)
    source: Literal["web", "paper"]
    action: Literal["research", "report"]
    rationale: str = Field(default="", max_length=1_000)


class ResearcherOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["tool", "finish"]
    tool_name: str = Field(default="", max_length=100)
    arguments: dict = Field(default_factory=dict)
    draft: Draft = Field(default_factory=Draft)
    findings: list[Claim] = Field(default_factory=list, max_length=12)

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
    review: str = Field(default="", max_length=3_000)


class State(TypedDict, total=False):
    research: dict
    history: list
    background: dict
    plan: dict
    current_plan: list
    current_source: str
    current_round: int
    current_draft: dict
    current_review: dict
    current_findings: list
    candidate_pools: dict
    query_results: dict
    collected_web_blocks: list
    evidence_pool: list
    evidence: list
    rounds: list
    tool_results: list
    errors: list
    warnings: list
    needs_research: bool
    content: str
    coder_status: str
    background_decision: dict
    last_completed: str
    next_node: str
    prepared_round: int
    final_evidence: list
    final_rejections: list


def _emit(on_stage, node_name):
    if on_stage:
        on_stage(node_name)


def _source_modes(source):
    return ["paper"] if source in {"local", "collection"} else ["paper", "web"]


def _preferred_mode(source):
    if source == "web":
        return "web"
    return "paper"


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
    return ToolContext(research["project_id"], tool_source, tuple(research.get("paper_ids") or ()))


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


def _model(state, prompt_id, payload, schema, *, node_role, search_mode="none", available_tools=None):
    research = state["research"]
    catalog = _catalog_for_mode(research, search_mode) if available_tools is None and search_mode in ("web", "paper") else (available_tools or [])
    template_context = {
        "search_mode": search_mode,
        "research_mode": research.get("research_mode", "auto"),
        "available_tools": catalog,
        "local_papers": research.get("local_papers", []),
        "node_role": node_role,
    }
    return structured(prompt_id, payload, schema, model_options=research, template_context=template_context)


def _payload(state, **extra):
    research = state["research"]
    return {
        "question": research["query"],
        "history": _history_for_model(state.get("history", [])),
        "source": research["source"],
        "background": _background_summary_for_model(state.get("background", {})),
        "plan": state.get("plan", {}),
        "rounds": _rounds_for_model(state.get("rounds", [])),
        "evidence": _evidence_views(_select_evidence(state)),
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


def _rounds_for_model(rounds):
    bounded = []
    for round_item in rounds:
        item = dict(round_item)
        item["candidate_ids"] = list(round_item.get("candidate_ids", []))[-MAX_CANDIDATE_CONTEXT:]
        cited = sorted(_claim_ids(round_item.get("draft", {}))) + sorted(_findings_ids(round_item.get("findings", [])))
        evidence_ids = list(dict.fromkeys(cited + list(round_item.get("evidence_ids", []))[-18:]))
        item["evidence_ids"] = evidence_ids[:36]
        bounded.append(item)
    return bounded


def _history_for_model(history):
    bounded = []
    for item in (history or [])[-12:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if isinstance(role, str) and isinstance(content, str):
            bounded.append({"role": role[:30], "content": content[-2_000:]})
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
        item.get("char_start"), item.get("char_end"),
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
    fields = ("id", "paper_id", "title", "text", "source_level", "version_id", "page", "original_url", "provider", "retrieved_at", "char_start", "char_end", "published_at", "candidate_id")
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


def _candidate_summaries(state, mode, ids=None, *, limit=MAX_CANDIDATE_CONTEXT, include_text=True, text_limit=MAX_CANDIDATE_TEXT):
    pool = state.get("candidate_pools", {}).get(mode, [])
    if ids is not None:
        wanted = set(ids)
        pool = [item for item in pool if item.get("candidate_id") in wanted]
    return [_candidate_view(item, mode, include_text=include_text, text_limit=text_limit) for item in pool[:limit]]


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
    ids = set()
    for section in draft.get("sections", []):
        for claim in section.get("paragraphs", []):
            ids.update(claim.get("citations", []))
    for claim in draft.get("limitations", []):
        ids.update(claim.get("citations", []))
    return ids


def _findings_ids(findings):
    return {item for finding in findings for item in finding.get("citations", [])}


def _select_evidence(state, include_ids=None):
    pool = _unique_evidence(state.get("evidence_pool", []))
    include_ids = set(include_ids or ())
    selected = []
    size = 0

    def add(item):
        nonlocal size
        if item in selected or len(selected) >= MAX_EVIDENCE_CONTEXT:
            return
        text = str(item.get("text", ""))
        if size + len(text) > MAX_EVIDENCE_TEXT:
            return
        selected.append(item)
        size += len(text)

    for item in pool:
        if item.get("id") in include_ids:
            add(item)

    facets = [state["research"]["query"]]
    for round_item in state.get("rounds", []):
        facets.extend(round_item.get("plan", []))
        review = round_item.get("review", {})
        facets.extend(review.get("gaps", []))
    terms = [relevance_terms(value) for value in facets if value]
    ranked = sorted(
        enumerate(pool),
        key=lambda pair: (
            -sum(relevance_score(facet, (pair[1].get("title", "") + " " + pair[1].get("text", "")).casefold()) for facet in terms),
            pair[0],
        ),
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
        return _safe_error("researcher", "web_page_read_failed", "网页正文读取失败，可继续尝试其他候选。")
    category = getattr(exc, "category", "")
    if category:
        return _safe_error("researcher", "search_" + str(category)[:40], "搜索服务返回失败，可改写查询或尝试其他允许来源。")
    if name in {"search_web", "search_papers"}:
        return _safe_error("researcher", name + "_failed", "搜索失败，可改写查询后继续尝试。")
    return _safe_error("researcher", "tool_failed", "工具调用失败，可根据允许工具继续研究。")


def _find_candidates(pool, ids):
    by_id = {item.get("candidate_id"): item for item in pool}
    if not isinstance(ids, list) or not ids or any(item not in by_id for item in ids):
        raise ToolCallValidationError("工具参数无效", "unknown_candidate")
    if len(set(ids)) != len(ids):
        raise ToolCallValidationError("工具参数无效", "duplicate_candidate")
    return [by_id[item] for item in ids]


def _helper_catalog(research, mode):
    """Expose the candidate-ID helper while keeping the registry's real schemas visible."""
    catalog = [dict(item) for item in _catalog_for_mode(research, mode)]
    for item in catalog:
        if item["name"] == "collect_evidence":
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


def _invoke_tool(state, mode, tool_name, arguments):
    research = state["research"]
    context = _tool_context(research, mode)
    args = dict(arguments)
    if tool_name == "collect_evidence":
        pool = state.get("candidate_pools", {}).get(mode, [])
        chosen = _find_candidates(pool, args.pop("candidate_ids", []))
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
        pool = state.get("candidate_pools", {}).get("web", [])
        candidate_id = args.pop("candidate_id", "")
        if candidate_id:
            match = _find_candidates(pool, [candidate_id])[0]
            args["url"] = match.get("url") or match.get("original_url")
        allowed_urls = {item.get("url") or item.get("original_url") for item in pool}
        if not args.get("url") or args["url"] not in allowed_urls:
            raise ToolCallValidationError("工具参数无效", "unknown_candidate_url")
        result = registry.invoke(tool_name, args, context=context)
        original = next((item for item in pool if (item.get("url") or item.get("original_url")) == args["url"]), {})
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
        return {"candidate_pools": pools, "tool_results": tool_results}, {"candidate_id": page_item["candidate_id"], "content": content[:MAX_CANDIDATE_TEXT], "offset": page_item["content_offset"], "next_offset": page_item["next_offset"], "truncated": result.get("truncated", False)}
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
        result = registry.invoke(tool_name, args, context=context)
        pools, query_results = _record_search_results(state, "web", args.get("query", ""), result)
        tool_results = list(state.get("tool_results", [])) + [{"tool": tool_name, "query": args.get("query", ""), "candidate_count": len(result)}]
        return {"candidate_pools": pools, "query_results": query_results, "tool_results": tool_results}, {"candidate_count": len(result)}
    result = registry.invoke(tool_name, args, context=context)
    return {"tool_results": list(state.get("tool_results", [])) + [{"tool": tool_name, "result": str(result)[:MAX_CANDIDATE_TEXT]}]}, result


def _background_investigator(state, on_stage):
    _emit(on_stage, "background_investigator")
    if state.get("background"):
        return {}
    research = state["research"]
    preferred = _preferred_mode(research["source"])
    allowed_modes = _source_modes(research["source"])
    query_tools = _search_catalog(research, _source_modes(research["source"]) if research["source"] == "auto" else [preferred])
    try:
        query_plan = _model(
            state,
            "literature/background_investigator",
            _payload(state, phase="query", available_sources=allowed_modes),
            BackgroundOutput,
            node_role="background_investigator",
            search_mode="none" if research["source"] == "auto" else preferred,
            available_tools=query_tools,
        )
        mode = query_plan.source if research["source"] == "auto" and query_plan.source in allowed_modes else preferred
        queries = [value.strip() for value in query_plan.queries if value.strip()][:MAX_BACKGROUND_SEARCHES]
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
    for query in queries[:MAX_BACKGROUND_SEARCHES]:
        tool_name = "search_web" if mode == "web" else "search_papers"
        try:
            if mode == "web":
                results = registry.invoke(tool_name, {"query": query, "limit": 5}, context=context)
            else:
                result = registry.invoke(tool_name, {"query": query}, context=context)
                results = result.get("results", [])
                if result.get("warnings"):
                    warnings.append("paper_search_incomplete")
            search_state = {"candidate_pools": pools, "query_results": query_results}
            pools, query_results = _record_search_results(search_state, mode, query, results)
        except (ToolCallValidationError, WebSearchError, WebPageError, RuntimeError) as exc:
            errors.append(_tool_error(tool_name, exc))
    query_state = {**state, "candidate_pools": pools, "query_results": query_results, "errors": errors, "warnings": warnings}
    background_decision = {"source": mode, "queries": queries[:MAX_BACKGROUND_SEARCHES]}
    candidates = _candidate_summaries(query_state, mode)
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
        valid_ids = {item["candidate_id"] for item in query_state.get("candidate_pools", {}).get(mode, [])}
        selected_ids = [item for item in selected.selected_candidate_ids if item in valid_ids]
        selected_items = _candidate_summaries(query_state, mode, selected_ids)
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


def _planner(state, on_stage):
    _emit(on_stage, "planner")
    rounds = state.get("rounds", [])
    allowed = _source_modes(state["research"]["source"])
    if len(rounds) >= MAX_ROUNDS:
        data = dict(state.get("plan") or _fallback_plan(state))
        data["action"] = "report"
        return {"plan": data, "current_plan": data["plan"], "current_source": data["source"], "last_completed": "planner"}
    prompt_id = "literature/planner"
    payload = _payload(
        state,
        round_count=len(rounds),
        current_plan=state.get("current_plan", []),
        current_draft=state.get("current_draft", {}),
        current_review=state.get("current_review", {}),
        allowed_sources=allowed,
        source_preference=state.get("background", {}).get("source", _preferred_mode(state["research"]["source"])),
    )
    try:
        result = _model(
            state,
            prompt_id,
            payload,
            PlannerOutput,
            node_role="planner",
            search_mode="none",
            available_tools=[],
        )
        data = result.model_dump()
        if data["source"] not in allowed:
            data["source"] = state.get("current_source") or state.get("background", {}).get("source") or allowed[0]
        if not data["requirements"]:
            data["requirements"] = [state["research"]["query"][:500]]
        if not data["plan"]:
            data["plan"] = ["围绕原问题补充可核验证据"]
        if not rounds:
            if state["research"]["source"] != "auto":
                data["source"] = _preferred_mode(state["research"]["source"])
            data["action"] = "research"
        return {"plan": data, "current_plan": data["plan"], "current_source": data["source"], "last_completed": "planner"}
    except (ModelServiceError, StructuredOutputError):
        data = _fallback_plan(state)
        if rounds and len(rounds) >= MAX_ROUNDS:
            data["action"] = "report"
        return {
            "plan": data,
            "current_plan": data["plan"],
            "current_source": data["source"],
            "last_completed": "planner",
            "errors": list(state.get("errors", [])) + [_safe_error("planner", "model_failed", "规划模型不可用，按当前来源继续有限研究。")],
        }


def _supervisor(state, on_stage):
    _emit(on_stage, "supervisor")
    completed = state.get("last_completed", "planner")
    rounds = state.get("rounds", [])
    plan = state.get("plan") or _fallback_plan(state)
    if completed == "researcher":
        target = "analyst"
    elif completed == "analyst":
        target = "coder"
    elif completed == "coder":
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
            allowed = _source_modes(state["research"]["source"])
            if mode not in allowed:
                mode = state.get("background", {}).get("source", allowed[0])
            update.update({
                "current_source": mode,
                "current_plan": plan.get("plan", []),
                "current_round": next_round,
                "current_draft": {},
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
    args = {"query": query, "limit": 5} if mode == "web" else {"query": query}
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
    allowed_modes = _source_modes(state["research"]["source"])
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
    action_count = 0
    draft = Draft()
    findings = []
    failure_message = ""
    tools = _helper_catalog(state["research"], mode)
    while action_count < MAX_RESEARCH_ACTIONS:
        payload = {
            "question": state["research"]["query"],
            "source": state["research"]["source"],
            "phase": "act",
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
            "calls_remaining": MAX_RESEARCH_ACTIONS - action_count,
            "search_attempts": search_attempts,
            "search_failures": search_failures,
        }
        try:
            result = _model(
                state,
                "literature/researcher",
                payload,
                ResearcherOutput,
                node_role="researcher",
                search_mode=mode,
                available_tools=tools,
            )
        except (ModelServiceError, StructuredOutputError):
            state["errors"].append(_safe_error("researcher", "model_failed", "研究模型不可用，保留已收集证据并继续生成缺口说明。"))
            failure_message = "研究模型不可用，未能完成本轮初稿。"
            break
        if result.action == "finish":
            # A failed search is feedback, not a stop condition. Give the model a
            # chance to rewrite the query; if it finishes immediately, make a
            # bounded code-generated rewrite while budget remains.
            if search_failures and search_attempts < 3 and action_count < MAX_RESEARCH_ACTIONS:
                name, args = _force_search_retry(state, mode, tried_queries)
                action_count += 1
                search_attempts += 1
                tried_queries.add(" ".join(args["query"].casefold().split()))
                try:
                    patch, _ = _invoke_tool(state, mode, name, args)
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
        if result.tool_name == "read_web_page":
            read_key = _web_read_key(state, result.arguments)
            if read_key in failed_web_reads:
                action_count += 1
                state["errors"].append(_safe_error("researcher", "duplicate_web_read_skipped", "已跳过本轮失败候选与字符区间的重复请求。"))
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
        try:
            if result.tool_name in {"search_web", "search_papers"}:
                search_attempts += 1
                query = result.arguments.get("query")
                if isinstance(query, str):
                    tried_queries.add(" ".join(query.casefold().split()))
            patch, _ = _invoke_tool(state, mode, result.tool_name, result.arguments)
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
    if not draft.sections and not draft.limitations:
        finalization_payload = {
            "question": state["research"]["query"],
            "source": state["research"]["source"],
            "phase": "draft",
            "current_round": state.get("current_round", 1),
            "current_source": mode,
            "current_plan": state.get("current_plan", []),
            "current_candidates": _candidate_summaries(
                state, mode, limit=5, text_limit=MAX_CANDIDATE_TEXT,
            ),
            "evidence": _evidence_views(_select_evidence(state)),
            "tool_results": _compact_tool_results(state.get("tool_results", [])),
            "tool_feedback": state.get("errors", [])[-6:],
            "calls_remaining": 0,
        }
        try:
            final_draft = _model(
                state,
                "literature/researcher",
                finalization_payload,
                ResearcherOutput,
                node_role="researcher",
                search_mode=mode,
                available_tools=[],
            )
            if final_draft.action == "finish":
                draft = final_draft.draft
                findings = final_draft.findings
        except (ModelServiceError, StructuredOutputError):
            state["errors"].append(_safe_error("researcher", "draft_failed", "初稿整理模型不可用，保留证据并说明缺口。"))
            failure_message = "未能生成本轮初稿；当前证据和来源仍需人工核对。"
    if not draft.sections and not draft.limitations and not failure_message:
        failure_message = "本轮研究未生成可引用段落；现有来源或证据不足以形成结论。"
    if not draft.sections and not draft.limitations and failure_message:
        draft = Draft(sections=[], limitations=[Claim(text=failure_message, citations=[])])
    evidence_pool = _unique_evidence(state.get("evidence_pool", []))
    evidence_by_id = {item["id"]: item for item in evidence_pool}
    allowed_papers = set(state["research"].get("paper_ids") or []) if state["research"]["source"] == "collection" else None
    safe_sections = []
    rejected = []
    for section in draft.sections:
        paragraphs = []
        for claim in section.paragraphs:
            _, reason = validate_claim(claim.text, claim.citations, evidence_by_id, allowed_papers)
            if reason:
                rejected.append(reason)
            else:
                paragraphs.append(claim)
        if paragraphs:
            citations = list(dict.fromkeys(item for claim in paragraphs for item in claim.citations))
            _, heading_error = validate_claim(section.heading, citations, evidence_by_id, allowed_papers)
            heading = section.heading if not heading_error else "本轮研究发现"
            if heading_error:
                rejected.append("章节标题未通过格式或数值依据检查")
            safe_sections.append(Section(heading=heading, paragraphs=paragraphs))
    safe_limitations = []
    for claim in draft.limitations:
        _, reason = validate_claim(claim.text, claim.citations, evidence_by_id, allowed_papers, allow_uncited=True, is_limitation=True)
        if reason:
            rejected.append(reason)
        else:
            safe_limitations.append(claim)
    safe_draft = Draft(sections=safe_sections, limitations=safe_limitations)
    if not safe_draft.sections and not safe_draft.limitations:
        safe_draft = Draft(limitations=[Claim(text="本轮初稿内容未通过引用、数值或来源范围检查，尚无可验证结论。", citations=[])])
    safe_findings = []
    for claim in findings:
        _, reason = validate_claim(claim.text, claim.citations, evidence_by_id, allowed_papers)
        if not reason:
            safe_findings.append(claim.model_dump())
        else:
            rejected.append(reason)
    if rejected:
        state["errors"].append(_safe_error("researcher", "claim_rejected", "代码检查移除了引用、数值或范围不符合要求的草稿段落。"))
    return {
        "candidate_pools": state.get("candidate_pools", {}),
        "query_results": state.get("query_results", {}),
        "evidence_pool": evidence_pool,
        "evidence": evidence_pool,
        "current_draft": safe_draft.model_dump(),
        "current_findings": safe_findings,
        "tool_results": state.get("tool_results", []),
        "errors": state.get("errors", []),
        "warnings": state.get("warnings", []),
        "last_completed": "researcher",
        "collected_web_blocks": state.get("collected_web_blocks", []),
    }


def _analyst(state, on_stage, on_subtask):
    _emit(on_stage, "analyst")
    mode = state.get("current_source", "paper")
    draft = state.get("current_draft", {})
    evidence = _select_evidence(state, _claim_ids(draft) | _findings_ids(state.get("current_findings", [])))
    allowed_papers = set(state["research"].get("paper_ids") or []) if state["research"]["source"] == "collection" else None
    payload = _payload(
        state,
        current_round=state.get("current_round", 1),
        current_source=mode,
        current_plan=state.get("current_plan", []),
        current_draft=draft,
        tool_results=state.get("tool_results", [])[-8:],
        evidence=_evidence_views(evidence),
        requirements=(state.get("plan") or {}).get("requirements", []),
        current_findings=state.get("current_findings", []),
    )
    try:
        result = _model(state, "literature/analyst", payload, AnalystOutput, node_role="analyst", search_mode=mode, available_tools=[])
        review = result.model_dump()
    except (ModelServiceError, StructuredOutputError):
        review = {
            "sufficient": False,
            "covered_requirements": [],
            "gaps": ["审查模型不可用，当前初稿和证据尚未完成语义审查。"],
            "next_steps": ["针对原问题补充检索并复核现有资料。"],
            "review": "审查模型不可用。",
        }
        state["errors"] = list(state.get("errors", [])) + [_safe_error("analyst", "model_failed", "审查模型不可用，保留缺口后继续。")]
    evidence_by_id = {item["id"]: item for item in state.get("evidence_pool", [])}
    findings = []
    for claim in state.get("current_findings", []):
        _, reason = validate_claim(claim.get("text", ""), claim.get("citations", []), evidence_by_id, allowed_papers)
        if not reason:
            findings.append(claim)
    current_findings = findings
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
        "review": review,
        "findings": current_findings,
        "candidate_ids": [item["candidate_id"] for item in state.get("candidate_pools", {}).get(mode, [])],
        "evidence_ids": [item["id"] for item in state.get("evidence_pool", [])],
        "status": status,
    }
    rounds = list(state.get("rounds", [])) + [round_item]
    return {"current_review": review, "current_findings": current_findings, "rounds": rounds, "errors": state.get("errors", []), "last_completed": "analyst"}


def _coder(state, on_stage):
    _emit(on_stage, "coder")
    # This node reserves a deterministic role in the pipeline. Data safety and
    # citation validation are performed by code at ingestion and report time.
    return {"coder_status": "placeholder", "last_completed": "coder"}


def _render_draft(draft, evidence, source, *, title, caveats=None, allowed_paper_ids=None, visible_ids=None):
    evidence_by_id = {item["id"]: item for item in evidence}
    permitted_ids = set(visible_ids) if visible_ids is not None else set(evidence_by_id)
    used_ids = set()
    rejected = []
    lines = [title]
    for section in draft.sections:
        accepted = []
        section_ids = []
        for claim in section.paragraphs:
            if any(identifier not in permitted_ids for identifier in claim.citations):
                rejected.append("引用不在报告工作证据范围内")
                continue
            cited, reason = validate_claim(claim.text, claim.citations, evidence_by_id, allowed_paper_ids)
            if reason:
                rejected.append(reason)
                continue
            accepted.append(claim)
            used_ids.update(item["id"] for item in cited)
            section_ids.extend(claim.citations)
        if accepted:
            _, heading_error = validate_claim(section.heading, list(dict.fromkeys(section_ids)), evidence_by_id, allowed_paper_ids)
            heading = section.heading if not heading_error else "研究发现"
            if heading_error:
                rejected.append("章节标题未通过格式或数值依据检查")
            lines.append("## " + heading)
            for claim in accepted:
                citations = " ".join(f"[{item}]" for item in claim.citations)
                lines.append(claim.text + (" " + citations if citations else ""))
    accepted_limitations = []
    for claim in draft.limitations:
        if any(identifier not in permitted_ids for identifier in claim.citations):
            rejected.append("局限引用不在报告工作证据范围内")
            continue
        cited, reason = validate_claim(claim.text, claim.citations, evidence_by_id, allowed_paper_ids, allow_uncited=True, is_limitation=True)
        if reason:
            rejected.append(reason)
            continue
        accepted_limitations.append(claim)
        used_ids.update(item["id"] for item in cited)
    if accepted_limitations:
        lines.append("## 局限与缺口")
        for claim in accepted_limitations:
            citations = " ".join(f"[{item}]" for item in claim.citations)
            lines.append(claim.text + (" " + citations if citations else ""))
    if caveats:
        lines.append("## 局限与待核查")
        lines.extend(caveats)
    source_label = {"auto": "自动选择", "local": "项目文献", "openalex": "OpenAlex", "all": "项目与外部论文", "collection": "收藏夹快照", "web": "网页"}.get(source, source)
    lines.append("初始来源偏好：" + source_label + "。")
    kept = [item for item in evidence if item.get("id") in used_ids]
    used_modes = {item.get("research_source") for item in kept if item.get("research_source")}
    if used_modes:
        actual_labels = {"paper": "论文", "web": "网页"}
        lines.append("本次引用资料：" + "、".join(actual_labels.get(mode, mode) for mode in ("paper", "web") if mode in used_modes) + "。")
    if kept:
        lines.append("## 参考证据")
        lines.extend(reference(item) for item in kept)
    return {"content": "\n\n".join(lines), "evidence": kept, "rejected": rejected}


def _reporter_fallback(state, reason):
    all_evidence = state.get("evidence_pool", [])
    allowed = set(state["research"].get("paper_ids") or []) if state["research"]["source"] == "collection" else None
    if allowed is not None:
        all_evidence = [item for item in all_evidence if item.get("paper_id") in allowed]
    evidence_by_id = {item["id"]: item for item in all_evidence}
    grouped_sections = {}
    claim_count = 0
    fallback_findings = []
    fallback_limitations = []
    for round_item in state.get("rounds", []):
        for section in round_item.get("draft", {}).get("sections", []):
            for claim in section.get("paragraphs", []):
                _, invalid = validate_claim(claim.get("text", ""), claim.get("citations", []), evidence_by_id, allowed)
                if invalid or claim_count >= 16:
                    continue
                heading = section["heading"]
                if heading not in grouped_sections and len(grouped_sections) >= 8:
                    continue
                _, heading_error = validate_claim(heading, claim.get("citations", []), evidence_by_id, allowed)
                if heading_error:
                    heading = "研究发现"
                claims = grouped_sections.setdefault(heading, [])
                if len(claims) < 8:
                    claims.append(Claim.model_validate(claim))
                    claim_count += 1
        for finding in round_item.get("findings", []):
            _, invalid = validate_claim(finding.get("text", ""), finding.get("citations", []), evidence_by_id, allowed)
            if not invalid and len(fallback_findings) < 8:
                fallback_findings.append(Claim.model_validate(finding))
        for claim in round_item.get("draft", {}).get("limitations", []):
            if len(fallback_limitations) >= 4:
                break
            _, invalid = validate_claim(claim.get("text", ""), claim.get("citations", []), evidence_by_id, allowed, allow_uncited=True, is_limitation=True)
            if not invalid:
                fallback_limitations.append(Claim.model_validate(claim))
    round_sections = [Section(heading=heading, paragraphs=claims) for heading, claims in grouped_sections.items()]
    if fallback_findings and len(round_sections) < 8 and claim_count < 16:
        round_sections.append(Section(heading="初步发现", paragraphs=fallback_findings[:min(8, 16 - claim_count)]))
    gaps = []
    for round_item in state.get("rounds", []):
        review = round_item.get("review", {})
        gaps.extend(review.get("gaps", []))
        if not review.get("sufficient"):
            gaps.extend(review.get("next_steps", []))
    gaps.extend(state.get("background", {}).get("gaps", []))
    limitations = [Claim(text=reason[:1_500], citations=[])]
    limitations.extend(fallback_limitations[:3])
    for gap in list(dict.fromkeys(value.strip() for value in gaps if isinstance(value, str) and value.strip()))[:5]:
        if len(limitations) >= 8:
            break
        limitations.append(Claim(text=gap[:1_500], citations=[]))
    draft = Draft(sections=round_sections, limitations=limitations)
    return _render_draft(
        draft,
        all_evidence,
        state["research"]["source"],
        title="# 研究报告",
        caveats=["以上仅保留通过代码引用与数值检查的初稿和发现。", "研究结论仍需结合原始资料核对。", *_warning_caveats(state)],
        allowed_paper_ids=allowed,
    )


def _reporter_insufficient_fallback(state, reason):
    """Show only stage findings that still pass the final citation and scope checks."""
    reason = reason.rstrip("。.!！ ")
    evidence = state.get("evidence_pool", [])
    allowed = set(state["research"].get("paper_ids") or []) if state["research"]["source"] == "collection" else None
    evidence = [item for item in evidence if allowed is None or item.get("paper_id") in allowed]
    evidence_by_id = {item["id"]: item for item in evidence}
    findings = []
    seen = set()
    for round_item in state.get("rounds", []):
        for finding in round_item.get("findings", []):
            text = finding.get("text", "")
            citations = finding.get("citations", [])
            _, invalid = validate_claim(text, citations, evidence_by_id, allowed)
            key = (text, tuple(citations))
            if invalid or key in seen or len(findings) >= 8:
                continue
            seen.add(key)
            findings.append(Claim.model_validate(finding))
    if findings:
        draft = Draft(
            sections=[Section(heading="阶段性发现（尚未完成核验）", paragraphs=findings)],
            limitations=[Claim(text=f"证据不足：{reason}。已展示的阶段性发现通过了当前引用和来源范围检查，但仍未解决研究缺口，不能视为完整结论。")],
        )
    else:
        draft = Draft(limitations=[Claim(text=f"证据不足：{reason}。当前没有通过引用检查的报告结论或阶段性发现，现有资料不足以形成完整终稿。")])
    gaps = []
    for round_item in state.get("rounds", []):
        review = round_item.get("review", {})
        gaps.extend(review.get("gaps", []))
        if not review.get("sufficient"):
            gaps.extend(review.get("next_steps", []))
    gaps.extend(state.get("background", {}).get("gaps", []))
    for gap in list(dict.fromkeys(value.strip() for value in gaps if isinstance(value, str) and value.strip()))[:5]:
        claim = Claim(text=gap[:1_000])
        _, invalid = validate_claim(claim.text, claim.citations, evidence_by_id, allowed, allow_uncited=True, is_limitation=True)
        if not invalid and len(draft.limitations) < 8:
            draft.limitations.append(claim)
    return _render_draft(
        draft,
        evidence,
        state["research"]["source"],
        title="# 研究报告（证据不足）",
        caveats=["本结果不构成完整终稿。", *_warning_caveats(state)],
        allowed_paper_ids=allowed,
    )


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


def _reporter_recovery(state, *, reason, reason_code, input_evidence_count, rejected_claim_count=0):
    result = _reporter_fallback(state, reason)
    if not result["evidence"]:
        result = _reporter_insufficient_fallback(state, reason)
    _log_report_validation(
        input_evidence_count=input_evidence_count,
        final_evidence_count=len(result["evidence"]),
        rejected_claim_count=rejected_claim_count,
        fallback_rejected_count=len(result.get("rejected", [])),
        fallback_reason=reason_code,
    )
    return result


def _warning_caveats(state):
    if "paper_search_incomplete" in state.get("warnings", []):
        return ["论文检索的部分外部来源失败，已保留可用结果，资料可能不完整。"]
    return []


def _reporter(state, on_stage):
    _emit(on_stage, "reporter")
    all_evidence = state.get("evidence_pool", [])
    allowed = set(state["research"].get("paper_ids") or []) if state["research"]["source"] == "collection" else None
    all_evidence = [item for item in all_evidence if allowed is None or item.get("paper_id") in allowed]
    required_ids = set()
    for round_item in state.get("rounds", []):
        required_ids |= _claim_ids(round_item.get("draft", {}))
        required_ids |= _findings_ids(round_item.get("findings", []))
    report_state = {**state, "evidence_pool": all_evidence}
    working = _select_evidence(report_state, required_ids)
    payload = _payload(
        state,
        source_scope=state["research"]["source"],
        rounds=_rounds_for_model(state.get("rounds", [])),
        evidence=_evidence_views(working),
        final_requirements=(state.get("plan") or {}).get("requirements", []),
        outstanding_gaps=[gap for item in state.get("rounds", []) for gap in item.get("review", {}).get("gaps", [])],
        errors=state.get("errors", []),
    )
    try:
        result = _model(state, "literature/reporter", payload, ReporterOutput, node_role="reporter", search_mode="none", available_tools=[])
    except ModelServiceError:
        reason = "报告模型服务不可用，未能形成完整终稿。"
        result = _reporter_recovery(
            state,
            reason=reason,
            reason_code="model_service_failed",
            input_evidence_count=len(working),
        )
        return {"content": result["content"], "final_evidence": result["evidence"], "errors": list(state.get("errors", [])) + [_safe_error("reporter", "model_service_failed", "报告模型服务不可用，已返回明确缺口和可核查资料。")], "final_rejections": result.get("rejected", []), "last_completed": "reporter"}
    except StructuredOutputError:
        reason = "报告结构校验失败，未能形成完整终稿。"
        result = _reporter_recovery(
            state,
            reason=reason,
            reason_code="invalid_report_output",
            input_evidence_count=len(working),
        )
        return {"content": result["content"], "final_evidence": result["evidence"], "errors": list(state.get("errors", [])) + [_safe_error("reporter", "invalid_output", "报告输出未通过结构校验，已返回明确缺口和可核查资料。")], "final_rejections": result.get("rejected", []), "last_completed": "reporter"}
    if not result.sections and not result.limitations:
        reason = "报告内容为空，未能形成完整终稿。"
        fallback = _reporter_recovery(
            state,
            reason=reason,
            reason_code="empty_report",
            input_evidence_count=len(working),
        )
        return {"content": fallback["content"], "final_evidence": fallback["evidence"], "errors": list(state.get("errors", [])) + [_safe_error("reporter", "empty_report", "报告输出为空，已返回明确缺口和可核查资料。")], "final_rejections": fallback.get("rejected", []), "last_completed": "reporter"}
    visible_ids = {item["id"] for item in working}
    caveats = []
    if not all_evidence:
        caveats.append("本次没有获得可用证据，以下内容不能视为已核验结论。")
    if any(not item.get("sufficient") or item.get("gaps") for item in (round_item.get("review", {}) for round_item in state.get("rounds", []))):
        caveats.append("研究审查仍记录有未解决缺口；请以原始证据为准。")
    caveats.extend(_warning_caveats(state))
    rendered = _render_draft(
        result,
        working,
        state["research"]["source"],
        title="# 研究报告",
        caveats=caveats,
        allowed_paper_ids=allowed,
        visible_ids=visible_ids,
    )
    if rendered["rejected"]:
        rendered["content"] += "\n\n部分段落因引用、数值或来源范围校验未通过而省略。"
    if not rendered["evidence"]:
        reason = "报告输出没有可保留的有效引用"
        fallback = _reporter_insufficient_fallback(state, reason)
        _log_report_validation(
            input_evidence_count=len(working),
            final_evidence_count=len(fallback["evidence"]),
            rejected_claim_count=len(rendered["rejected"]),
            fallback_rejected_count=len(fallback.get("rejected", [])),
            fallback_reason="no_valid_report_citations",
        )
        return {
            "content": fallback["content"],
            "final_evidence": fallback["evidence"],
            "final_rejections": rendered["rejected"] + fallback.get("rejected", []),
            "last_completed": "reporter",
        }
    _log_report_validation(
        input_evidence_count=len(working),
        final_evidence_count=len(rendered["evidence"]),
        rejected_claim_count=len(rendered["rejected"]),
        fallback_rejected_count=0,
        fallback_reason="none",
    )
    return {"content": rendered["content"], "final_evidence": rendered["evidence"], "final_rejections": rendered["rejected"], "last_completed": "reporter"}


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


def _graph(on_stage, on_subtask):
    graph = StateGraph(State)
    graph.add_node("dispatcher", lambda state: _dispatch(state, on_stage))
    graph.add_node("background_investigator", lambda state: _background_investigator(state, on_stage))
    graph.add_node("planner", lambda state: _planner(state, on_stage))
    graph.add_node("supervisor", lambda state: _supervisor(state, on_stage))
    graph.add_node("researcher", lambda state: _researcher(state, on_stage))
    graph.add_node("analyst", lambda state: _analyst(state, on_stage, on_subtask))
    graph.add_node("coder", lambda state: _coder(state, on_stage))
    graph.add_node("reporter", lambda state: _reporter(state, on_stage))
    graph.add_edge(START, "dispatcher")
    graph.add_conditional_edges(
        "dispatcher",
        lambda state: "background_investigator" if state.get("needs_research") else END,
        {"background_investigator": "background_investigator", END: END},
    )
    graph.add_edge("background_investigator", "planner")
    graph.add_conditional_edges(
        "planner",
        lambda state: "reporter" if len(state.get("rounds", [])) >= MAX_ROUNDS or (state.get("rounds") and (state.get("plan") or {}).get("action") == "report") else "supervisor",
        {"reporter": "reporter", "supervisor": "supervisor"},
    )
    graph.add_conditional_edges(
        "supervisor",
        lambda state: state["next_node"],
        {"researcher": "researcher", "analyst": "analyst", "coder": "coder", "planner": "planner"},
    )
    graph.add_edge("researcher", "supervisor")
    graph.add_edge("analyst", "supervisor")
    graph.add_edge("coder", "supervisor")
    graph.add_edge("reporter", END)
    return graph.compile()


def _safe_history(history, source):
    if source == "collection":
        return []
    return list(history or [])


def run(research, *, history, on_stage=None, on_subtask=None):
    """Run the research graph and return the final Markdown and cited evidence."""
    if not isinstance(research, dict):
        raise TypeError("research must be a dict")
    required = {"query", "project_id", "source"}
    missing = required - set(research)
    if missing:
        raise ValueError("research input is missing required fields")
    if research.get("research_mode", "auto") not in {"auto", "deep", "quick"}:
        raise ValueError("research_mode must be auto, deep, or quick")
    initial = {
        "research": dict(research),
        "history": _safe_history(history, research["source"]),
        "background": {},
        "plan": {},
        "current_plan": [],
        "current_source": "",
        "current_round": 0,
        "current_draft": {},
        "current_review": {},
        "candidate_pools": {"paper": [], "web": []},
        "query_results": {},
        "collected_web_blocks": [],
        "evidence_pool": [],
        "evidence": [],
        "rounds": [],
        "tool_results": [],
        "errors": [],
        "warnings": [],
        "prepared_round": 0,
    }
    graph = _graph(on_stage, on_subtask)
    result = graph.invoke(initial, config={"recursion_limit": 40})
    if result.get("content") is None:
        fallback = _reporter_fallback(result, "研究流程未能形成报告，以下为可核查资料及当前缺口。")
        return fallback["content"], fallback["evidence"]
    return result["content"], result.get("final_evidence", result.get("evidence", []))
