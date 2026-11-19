"""Adapt Tufa's saved citation markers to official DRB Markdown links."""

import re

from src.agents.report import _safe_markdown_url

REFERENCE_HEADING = "\n\n## 参考文献\n\n"
REFERENCE_ITEM = re.compile(r"^- \\\[([0-9]+)\\\] ", re.MULTILINE)
CITATION = re.compile(r"\\\[([0-9]+)\\\]")
LINK = re.compile(r"(?<!\\)\]\((https?://[^\s)]+)\)")


def adapt(content, evidence):
    before, separator, references = content.rpartition(REFERENCE_HEADING)
    if not separator:
        return content, {"linked": [], "missing_url": []}
    # The official scorer reads inline [label](URL). Tufa's numbered
    # bibliography already resolves source groups from validated evidence IDs.
    known = {_safe_markdown_url(row.get("original_url")) for row in evidence}
    entries = list(REFERENCE_ITEM.finditer(references))
    urls = {}
    for index, entry in enumerate(entries):
        end = entries[index + 1].start() if index + 1 < len(entries) else len(references)
        bibliography_entry = references[entry.end():end]
        link = LINK.search(bibliography_entry)
        if link and link.group(1) in known:
            urls[entry.group(1)] = link.group(1)
    linked = set()
    missing = set()

    def replace(match):
        number = match.group(1)
        if number not in urls:
            missing.add(int(number))
            return match.group()
        linked.add(int(number))
        return f"[{number}]({urls[number]})"

    body = CITATION.sub(replace, before)
    return body + separator + references, {"linked": sorted(linked), "missing_url": sorted(missing)}


def record(task, report):
    article, diagnostics = adapt(report["content"], report["evidence"])
    return {"id": task["task_id"], "prompt": task["prompt"], "article": article}, diagnostics
