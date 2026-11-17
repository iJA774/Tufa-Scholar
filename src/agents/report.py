"""Deterministic citation and measurement checks used by the reporter node."""

import ipaddress
import re
from decimal import Decimal
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


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
_SENSITIVE_QUERY_KEY = re.compile(
    r"(?:^|[^a-z0-9])(?:token|secret|key|auth|signature|sig|password|credential|session)(?:$|[^a-z0-9])"
    r"|(?:access|refresh|api|client)(?:token|key|secret)",
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


def _resolve_citations(citations, evidence_by_id, allowed_paper_ids):
    if any(not isinstance(item, str) or item not in evidence_by_id for item in citations):
        return [], "引用不存在"
    cited = [evidence_by_id[item] for item in citations]
    if allowed_paper_ids is not None and any(
        item.get("paper_id") not in allowed_paper_ids for item in cited
    ):
        return [], "引用超出收藏夹快照范围"
    return cited, ""


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
    cited, reason = _resolve_citations(citations, evidence_by_id, allowed_paper_ids)
    if reason:
        return [], reason
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


_MARKDOWN_CITATION = re.compile(r"\[\[([a-zA-Z0-9_-]+)\]\]")
_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_MARKDOWN_LIST = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_MARKDOWN_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_PROCESS_GAP = re.compile(r"(?:不足|缺少|缺乏|尚未|未能|无法|不可用|未提供|未覆盖|未确认|未验证|缺口|失败|限制)")


def validate_markdown(markdown, evidence, *, allowed_paper_ids=None, citation_only=False):
    """Validate cited Markdown blocks while preserving their presentation order.

    Draft validation checks claim support. Final reports use citation_only to
    validate explicit references while allowing uncited prose. Both retain
    valid markers for publication and apply the same Markdown safety checks.
    """
    by_id = {item['id']: item for item in evidence}
    raw = str(markdown or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    wrapped = re.fullmatch(r'(?is)```(?:markdown|md)\s*\n(.*?)\n```\s*', raw)
    if wrapped:
        raw = wrapped.group(1)
    lines = raw.split('\n')
    output, rejected, findings, used = [], [], [], set()
    has_body = False

    def check(block, *, heading=False, limitation=False):
        nonlocal has_body
        ids = _MARKDOWN_CITATION.findall(block)
        plain = _MARKDOWN_CITATION.sub('', block).strip()
        body = re.sub(r'^\d+[.)](?:\s+|$)', '', plain)
        substantive = re.sub(r'[\s\-+*#>|.:：()\[\]`]+', '', body)
        if ids and not substantive:
            rejected.append('引用缺少正文')
            return False
        if (unsafe_text(plain) or '[[' in plain or ']]' in plain or '![' in plain
                or re.search(r'(?i)\b(?:www\.|javascript:|data:)', plain)
                or re.search(r'(?m)^\s{0,3}\[[^\]]+\]:\s*\S', plain)):
            rejected.append('格式或引用不安全')
            return False
        if citation_only:
            _, reason = _resolve_citations(ids, by_id, allowed_paper_ids)
        elif heading and not ids:
            _, reason = validate_claim(plain, [], by_id, allowed_paper_ids, allow_uncited=True)
        else:
            process = limitation and bool(_PROCESS_GAP.search(plain))
            _, reason = validate_claim(plain, ids, by_id, allowed_paper_ids, allow_uncited=process, is_limitation=process)
        if reason:
            rejected.append(reason)
            return False
        used.update(ids)
        if not heading and substantive:
            has_body = True
        if ids and not heading and len(findings) < 12:
            text = re.sub(r'^\s*(?:[-+*]|\d+[.)]|>)\s*', '', plain).strip()
            findings.append({'text': text[:1500], 'citations': ids})
        return True

    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            if output and output[-1] != '':
                output.append('')
            index += 1
            continue
        fence = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if fence:
            delimiter = fence.group(1)
            index += 1
            while index < len(lines):
                closing = re.match(r'^\s{0,3}(' + re.escape(delimiter[0]) + r'{%d,})\s*$' % len(delimiter), lines[index])
                index += 1
                if closing:
                    break
            rejected.append('格式或引用不安全')
            continue
        if index + 1 < len(lines) and '|' in line and _MARKDOWN_SEPARATOR.match(lines[index + 1]):
            header, separator = line, lines[index + 1]
            index += 2
            rows = []
            header_safe = (not unsafe_text(header) and (citation_only or not measurements(header))
                           and not _MARKDOWN_CITATION.search(header) and '![' not in header)
            while index < len(lines) and '|' in lines[index] and lines[index].strip():
                row = lines[index]
                if header_safe and check(row):
                    rows.append(row)
                index += 1
            if rows:
                output.extend([header, separator, *rows])
            elif not header_safe:
                rejected.append('表格标题不安全')
            continue
        if _MARKDOWN_HEADING.match(line):
            if check(line, heading=True):
                output.append(line)
            index += 1
            continue
        block = [line]
        index += 1
        if _MARKDOWN_LIST.match(line):
            while index < len(lines) and lines[index].strip() and re.match(r'^\s{2,}\S', lines[index]) and not _MARKDOWN_LIST.match(lines[index]):
                block.append(lines[index])
                index += 1
        elif not line.lstrip().startswith('>'):
            while index < len(lines) and lines[index].strip() and not _MARKDOWN_HEADING.match(lines[index]) and not _MARKDOWN_LIST.match(lines[index]) and not lines[index].lstrip().startswith('>') and not (index + 1 < len(lines) and _MARKDOWN_SEPARATOR.match(lines[index + 1])):
                block.append(lines[index])
                index += 1
        text = '\n'.join(block)
        limitation = bool(re.match(r'^\s*>\s*局限[：:]', text))
        if check(text, limitation=limitation):
            output.extend(block)
    return {
        'content': '\n'.join(output).strip(),
        'evidence': [item for item in evidence if item['id'] in used],
        'rejected': rejected,
        'findings': findings,
        'has_body': has_body,
    }


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


def _safe_markdown_url(value):
    """Keep useful source query values while removing common credentials."""
    safe = _safe_http_url(value)
    if not safe:
        return ""
    parts = urlsplit(safe)
    host = (parts.hostname or "").rstrip(".").casefold()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test")) or "." not in host:
        return ""
    try:
        if not ipaddress.ip_address(host).is_global:
            return ""
    except ValueError:
        pass
    query = [
        (key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not _SENSITIVE_QUERY_KEY.search(key)
    ]
    query_text = urlencode(query, doseq=True)
    link = urlunsplit((parts.scheme, parts.netloc, parts.path, query_text, ""))
    return quote(link, safe=":/@%?&=+$,;!~*'-._")


def reference(evidence, *, markdown=False, include_title=True):
    """Render a compact reference line, optionally linking a safe public URL."""
    level = evidence.get("source_level", "")
    title = _safe_title(evidence.get("title", ""))
    url = _safe_http_url(evidence.get("original_url", ""))
    if markdown:
        if title and unsafe_text(title):
            title = ""
        title = title.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        title = title.replace("(", "\\(").replace(")", "\\)")
        if url:
            # Preserve useful parameters such as article titles, while removing
            # common credential and one-time access parameters.
            url = _safe_markdown_url(evidence.get("original_url", ""))
        source_title = (f"[{title or '来源'}]({url})" if url else (title or "来源")) if include_title else ""
        identifier = str(evidence.get("id", ""))
        if unsafe_text(identifier):
            identifier = ""
        metadata = [f"证据 {identifier}"] if identifier else []
    else:
        source_title = f"[{evidence['id']}] {title}"
    if level == "local_markdown":
        start, end = evidence.get("char_start"), evidence.get("char_end")
        suffix = "本地 Markdown"
        if type(start) is int and type(end) is int and 0 <= start < end:
            suffix += f" 字符区间 {start}–{end}（从 0 开始）"
        return ' · '.join(part for part in (source_title, *metadata, suffix) if part) if markdown else f"{source_title} · {suffix}"
    if level in ("web_snippet", "web_content"):
        source_kind = {"web_snippet": "搜索摘要", "web_content": "网页文本"}.get(level, level)
        anchor = ""
        if evidence.get("char_start") is not None and evidence.get("char_end") is not None:
            anchor = f" · Markdown 字符区间 {evidence['char_start']}–{evidence['char_end']}（从 0 开始）"
        provider = evidence.get("provider", "")
        retrieved_at = evidence.get("retrieved_at", "")
        if markdown:
            metadata.append(source_kind)
            if provider and not unsafe_text(str(provider)):
                metadata.append(str(provider))
            if retrieved_at and not unsafe_text(str(retrieved_at)):
                metadata.append(f"获取于 {retrieved_at}")
            suffix = " · ".join(metadata + ([anchor[3:]] if anchor else []))
            return ' · '.join(part for part in (source_title, suffix) if part)
        return f"[{evidence['id']}] {title} · {source_kind} · {provider} · {url} · 获取于 {retrieved_at}{anchor}"
    if evidence.get("version_id"):
        suffix = f"原文版本 {evidence['version_id']}，第 {evidence.get('page', '')} 页"
        return ' · '.join(part for part in (source_title, *metadata, suffix) if part) if markdown else f"{source_title} · {suffix}"
    suffix = "论文摘要"
    if url and not markdown:
        suffix += f" · {url}"
    return ' · '.join(part for part in (source_title, *metadata, suffix) if part) if markdown else f"{source_title} · {suffix}"


def numbered_references(content, evidence, *, legacy=False):
    """Render validated markers (or exact historical references) by source."""
    if legacy and '〔' not in content:
        return content
    by_id = {str(item['id']): item for item in evidence}
    if legacy:
        markers = {}
        for item in evidence:
            marker = '〔' + re.sub(r'\s+', ' ', reference(item, markdown=True)).replace('|', r'\|') + '〕'
            markers.setdefault(marker, []).append(item)
        pattern = re.compile('|'.join(re.escape(k) for k, v in sorted(markers.items(), key=lambda x: -len(x[0])) if len(v) == 1)) if any(len(v) == 1 for v in markers.values()) else None
    else:
        pattern = _MARKDOWN_CITATION
    groups = {}

    def replace(match):
        item = markers[match.group()][0] if legacy else by_id.get(match.group(1))
        if not item:
            return match.group()
        document_id = item.get('document_id')
        paper_id = item.get('paper_id')
        url = _safe_http_url(item.get('original_url'))
        if item.get('source_level') == 'local_markdown' and document_id:
            key = ('markdown', document_id)
        elif paper_id is not None:
            key = ('paper', str(paper_id))
        elif url:
            parts = urlsplit(url)
            key = ('url', urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, '')))
        else:
            key = ('evidence', str(item['id']))
        if key not in groups:
            groups[key] = (len(groups) + 1, [])
        number, group = groups[key]
        if item not in group:
            group.append(item)
        return r'\[' + str(number) + r'\]'

    body = pattern.sub(replace, content) if pattern else content
    if not groups:
        return body
    lines = []
    for number, items in groups.values():
        details = []
        seen_pages = set()
        metadata = []
        for key in ('authors', 'year', 'doi', 'published_at'):
            value = items[0].get(key)
            value = ', '.join(str(v) for v in value) if isinstance(value, list) else str(value or '')
            if value and not unsafe_text(value) and not re.search(r'[\r\n|]', value):
                metadata.append(re.sub(r'([\\`*_{}\[\]()#!>])', r'\\\1', value))
        for index, item in enumerate(items):
            version, page = item.get('version_id'), item.get('page')
            page_key = (version, page) if isinstance(version, int) and version > 0 and isinstance(page, int) and page > 0 else None
            if page_key and page_key in seen_pages:
                identifier = str(item.get('id', ''))
                detail = f'证据 {identifier}' if identifier and not unsafe_text(identifier) else ''
            else:
                detail = reference(item, markdown=True, include_title=index == 0).replace('|', r'\|')
            detail = re.sub(r'\s+', ' ', detail)
            if page_key and page_key not in seen_pages:
                detail += f' · [查看第 {page} 页](/paper-versions/{version}/file#page={page})'
                seen_pages.add(page_key)
            if detail and detail not in details:
                details.append(detail)
        lines.append(f'- \\[{number}\\] ' + '；'.join(details) + ((' · ' + ' · '.join(metadata)) if metadata else ''))
    return body.rstrip() + '\n\n## 参考文献\n\n' + '\n'.join(lines)
