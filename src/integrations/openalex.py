"""OpenAlex works search; network operations run outside database transactions."""
import re
import logging
import time
import httpx

from src.infrastructure.settings import openalex_key

logger = logging.getLogger(__name__)


def normalize_work(work):
    locations = work.get("locations") or []
    primary = work.get("primary_location") or {}
    best = work.get("best_oa_location") or {}
    urls = [location.get(key) or "" for location in [*locations, primary, best]
            for key in ("landing_page_url", "pdf_url")]
    arxiv = next((match.group(1) for url in urls
                  if (match := re.search(r"arxiv\.org/(?:abs|pdf)/([^?#]+?)(?:\.pdf)?$", url, re.I))), None)
    index = work.get("abstract_inverted_index") or {}
    words = sorted((position, word) for word, positions in index.items() for position in positions)
    abstract = " ".join(word for _, word in words) or None
    oa = work.get("open_access") or {}
    return {
        "title": work.get("title") or work.get("display_name") or "",
        "authors": [entry.get("author", {}).get("display_name") for entry in work.get("authorships", [])
                    if entry.get("author", {}).get("display_name")],
        "year": work.get("publication_year"), "doi": work.get("doi"), "arxiv_id": arxiv,
        "abstract": abstract, "source": (primary.get("source") or {}).get("display_name"),
        "open_access": oa.get("is_oa"), "pdf_url": best.get("pdf_url") or primary.get("pdf_url"),
        "original_url": primary.get("landing_page_url") or work.get("doi") or work.get("id"),
        "source_level": "abstract" if abstract else "metadata_only", "openalex_id": work.get("id"),
    }


def search_works(query, limit=20):
    params = {"search": query, "per_page": limit}
    key = openalex_key()
    headers = {"Accept": "application/json", "User-Agent": "Tufa-Scholar/0.1"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    start = time.monotonic()
    logger.info('openalex_search started')
    try:
        response = httpx.get("https://api.openalex.org/works", params=params, headers=headers, timeout=20)
        response.raise_for_status()
        results = [normalize_work(work) for work in response.json()["results"]]
        logger.info('openalex_search completed elapsed_ms=%d status=%d result_count=%d', int((time.monotonic() - start) * 1000), response.status_code, len(results))
        return results
    except httpx.HTTPStatusError as exc:
        logger.warning('openalex_search failed elapsed_ms=%d status=%d error_type=%s', int((time.monotonic() - start) * 1000), exc.response.status_code, type(exc).__name__)
        raise RuntimeError(f"OpenAlex 请求失败 (HTTP {exc.response.status_code})；请检查网络、额度或 OPENALEX_API_KEY") from None
    except (httpx.RequestError, ValueError, KeyError, TypeError) as exc:
        logger.warning('openalex_search failed elapsed_ms=%d error_type=%s', int((time.monotonic() - start) * 1000), type(exc).__name__)
        raise RuntimeError("OpenAlex 请求失败；请检查网络或稍后重试") from None
