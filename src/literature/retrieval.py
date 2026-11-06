"""Combine ranked local keyword results and OpenAlex search results."""
import re
import logging
import time
from src.persistence import repository
from src.integrations.openalex import search_works

logger = logging.getLogger(__name__)


def _identifiers(item):
    doi = (item.get("doi") or "").lower().strip()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi)
    arxiv = (item.get("arxiv_id") or "").lower().strip()
    arxiv = re.sub(r"^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)", "", arxiv)
    arxiv = re.sub(r"(?:v\d+)?(?:\.pdf)?$", "", arxiv)
    return ({f"doi:{doi}"} if doi else set()) | ({f"arxiv:{arxiv}"} if arxiv else set())


def retrieve(project_id, query, source="all"):
    start = time.monotonic()
    logger.info('retrieval started project_id=%s source=%s', project_id, source)
    if source not in {"local", "openalex", "all"}:
        raise ValueError("不支持的检索来源")
    groups = []
    warnings = []
    if source in {"local", "all"}:
        groups.append(("local", repository.local_search(project_id, query)))
    if source in {"openalex", "all"}:
        try:
            groups.append(("openalex", search_works(query)))
        except RuntimeError as exc:
            if source == 'openalex':
                raise
            warnings.append(str(exc))
    combined = []
    for name, items in groups:
        for rank, item in enumerate(items, 1):
            identifiers = _identifiers(item)
            entry = next((e for e in combined if identifiers & e["ids"]), None)
            if entry is None:
                entry = {"paper": dict(item), "ids": set(), "score": 0, "sources": []}
                combined.append(entry)
            else:
                for field, value in item.items():
                    if field != "source_level" and value is not None and not entry["paper"].get(field):
                        entry["paper"][field] = value
                if entry["paper"].get("source_level") == "metadata_only" and item.get("source_level") == "abstract":
                    entry["paper"]["source_level"] = "abstract"
            entry["ids"].update(identifiers)
            entry["score"] += 1 / (60 + rank)
            entry["sources"].append({"source": name, "rank": rank})
    combined.sort(key=lambda entry: entry["score"], reverse=True)
    results = []
    for rank, entry in enumerate(combined[:20], 1):
        paper = entry["paper"]
        paper["retrieval_sources"] = entry["sources"]
        paper["rank"] = rank
        results.append(paper)
    logger.info('retrieval completed project_id=%s source=%s elapsed_ms=%d result_count=%d warning_count=%d', project_id, source, int((time.monotonic() - start) * 1000), len(results), len(warnings))
    return {"query": query, "source": source, "method": "keyword search; rank fusion when both sources are used", "results": results, "warnings": warnings}


def retrieve_collection(project_id, query, paper_ids):
    """Rank only the intersection of the submitted snapshot and project papers."""
    allowed = set(paper_ids)
    candidates = repository.project_evidence(project_id, list(allowed)) if allowed else []
    candidates = [p for p in candidates if p['id'] in allowed]
    ranked = repository.local_search(project_id, query, list(allowed)) if allowed else []
    order = {p['id']: i for i, p in enumerate(ranked) if p['id'] in allowed}
    candidates.sort(key=lambda p: order.get(p['id'], len(order)))
    return {'query': query, 'source': 'collection', 'method': 'project collection keyword ranking', 'results': candidates[:6], 'warnings': []}
