"""Deterministic citation and measurement checks used by the reporter node."""

import re
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit


_UNSAFE = re.compile(
    r"```|<[^>]+>|https?://|\[[^\]\n]+\]\s*\([^)]*\)|"
    r"\[(?:[a-zA-Z]+[\w-]*\d+[\w-]*|\d+)\]",
    re.I,
)
_COST = re.compile(r"成本|费用|cost|expense", re.I)
_COST_EVIDENCE = re.compile(r"成本|费用|cost|expense|\$|美元", re.I)
_COST_GAP = re.compile(
    r"(?:(?:现有材料|本次资料|当前材料|相关研究|证据|资料|数据)?\s*"
    r"(?:没有|缺少|缺乏|尚无|未(?:能)?(?:提供|发现|找到|报告|测量|披露)|无法评估)\s*"
    r"(?:关于)?\s*(?:相关)?(?:成本|费用)\s*(?:相关)?"
    r"(?:证据|数据|信息|记录|报告|指标|比较|评估)"
    r"|(?:成本|费用)\s*(?:相关)?(?:证据|数据|信息|记录|报告|指标|比较|评估)\s*"
    r"(?:缺失|缺少|不足|未提供|未报告|未发现)"
    r"|(?:no|lack(?:s|ing)?|without|unavailable|not\s+(?:reported|provided|measured))\s+"
    r"(?:any\s+)?(?:cost|expense)\s+(?:data|evidence|information|results|measurements)"
    r"|(?:cost|expense)\s+(?:data|evidence|information|results|measurements)\s+"
    r"(?:is|are)?\s*(?:missing|unavailable|not\s+(?:reported|provided|measured)))",
    re.I,
)
_NUM = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_MEASURE = re.compile(
    r"(?<![A-Za-z0-9_.,])(?:百分之\s*(" + _NUM + r")|(" + _NUM
    + r")\s*(milliseconds?|seconds?|percent|gib|mib|ms|gb|mb|kg|km|毫秒|秒|%))"
    r"(?![A-Za-z0-9_])",
    re.I,
)


def measurements(text):
    """Return normalized numeric measurements in a claim or evidence snippet."""
    result = set()
    for prefix, number, unit in _MEASURE.findall(text or ""):
        unit = unit.casefold()
        value = Decimal((prefix or number).replace(",", ""))
        if prefix or unit in ("%", "percent"):
            unit = "%"
        elif unit in ("milliseconds", "millisecond", "毫秒", "ms"):
            value /= Decimal(1000)
            unit = "second"
        elif unit in ("seconds", "second", "秒"):
            unit = "second"
        result.add((value, unit))
    return result


def unsafe_text(text):
    """Reject raw links, HTML, code blocks, and embedded citation syntax."""
    return bool(_UNSAFE.search(text or ""))


def validate_claim(text, citations, evidence_by_id, allowed_paper_ids=None, *, allow_uncited=False, is_limitation=False):
    """Check citation existence/scope and numeric or cost support for one claim.

    Returns ``(cited_evidence, reason)``. The semantic fit of prose remains the
    analyst's responsibility; this function enforces objective data boundaries.
    """
    if unsafe_text(text):
        return [], "格式或引用不安全"
    if len(citations) != len(set(citations)):
        return [], "引用重复"
    if not citations:
        if not allow_uncited:
            return [], "段落缺少证据引用"
        if measurements(text):
            return [], "无引用段落包含数值"
        if _COST.search(_COST_GAP.sub("", text)):
            return [], "无引用段落包含成本结论"
        return [], ""
    if any(not isinstance(item, str) or item not in evidence_by_id for item in citations):
        return [], "引用不存在"
    cited = [evidence_by_id[item] for item in citations]
    if allowed_paper_ids is not None and any(
        item.get("paper_id") not in allowed_paper_ids for item in cited
    ):
        return [], "引用超出收藏夹快照范围"
    required = measurements(text)
    supported = set().union(*(measurements(item.get("text", "")) for item in cited))
    if not required.issubset(supported):
        return [], "引用或测量值依据不匹配"
    cost_is_supported = any(_COST_EVIDENCE.search(item.get("text", "")) for item in cited)
    remaining_cost_claim = _COST.search(_COST_GAP.sub("", text))
    cost_is_reported_gap = is_limitation and bool(_COST_GAP.search(text)) and not remaining_cost_claim
    if remaining_cost_claim and not cost_is_supported and not cost_is_reported_gap:
        return [], "缺少成本依据"
    return cited, ""


def _safe_title(value):
    title = value if isinstance(value, str) else ""
    if re.match(r"^(?:[a-z]:[\\/]|[/\\]{2}|/)", title, re.I):
        return ""
    return title


def _safe_http_url(value):
    if not isinstance(value, str) or not value:
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme.casefold() not in ("http", "https") or not parts.hostname:
            return ""
        host = parts.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, parts.query, ""))
    except ValueError:
        return ""


def reference(evidence):
    """Render a compact reference line without exposing local filesystem paths."""
    level = evidence.get("source_level", "")
    if level in ("web_snippet", "web_content"):
        label = {"web_snippet": "搜索摘要", "web_content": "网页文本"}.get(level, level)
        anchor = ""
        if evidence.get("char_start") is not None and evidence.get("char_end") is not None:
            anchor = f" · Markdown 字符区间 {evidence['char_start']}–{evidence['char_end']}（从 0 开始）"
        return (
            f"[{evidence['id']}] {_safe_title(evidence.get('title', ''))} · {label} · "
            f"{evidence.get('provider', '')} · {_safe_http_url(evidence.get('original_url', ''))} · "
            f"获取于 {evidence.get('retrieved_at', '')}{anchor}"
        )
    if evidence.get("version_id"):
        return f"[{evidence['id']}] {_safe_title(evidence.get('title', ''))} · 原文版本 {evidence['version_id']}，第 {evidence.get('page', '')} 页"
    url = _safe_http_url(evidence.get("original_url", ""))
    return f"[{evidence['id']}] {_safe_title(evidence.get('title', ''))} · 论文摘要" + (f" · {url}" if url else "")
