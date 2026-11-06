"""Direct, bounded adapters for the configured web search provider."""
from datetime import datetime, timezone
from html import unescape
import re
from urllib.parse import quote, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx
from ddgs import DDGS
from ddgs.exceptions import DDGSException

from src.settings import web_search_config

_TIMEOUT = 20
_MAX_CONTENT = 4000
_HEADERS = {"Accept": "application/json", "User-Agent": "Tufa-Scholar/0.1"}


def _url(value):
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return None
        port = parts.port
        host = parts.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        if port and (parts.scheme.lower(), port) not in (("http", 80), ("https", 443)):
            host += f":{port}"
        return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))
    except ValueError:
        return None


def _text(value):
    if not isinstance(value, str):
        return ""
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", value)).split())[:_MAX_CONTENT]


def _request(method, url, *, headers=None, **kwargs):
    response = httpx.request(method, url, headers={**_HEADERS, **(headers or {})},
                             timeout=_TIMEOUT, follow_redirects=False, **kwargs)
    response.raise_for_status()
    return response


def _items(data, *path):
    for name in path:
        if not isinstance(data, dict) or name not in data:
            raise ValueError("invalid response")
        data = data[name]
    if not isinstance(data, list):
        raise ValueError("invalid response")
    return data


def _normalise(items, provider, limit, level="web_snippet"):
    if not isinstance(items, list):
        raise ValueError("invalid response")
    now = datetime.now(timezone.utc).isoformat()
    seen, results = set(), []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid response")
        url = _url(item.get("url") or item.get("href") or item.get("link"))
        title = _text(item.get("title"))
        if not url or not title or url in seen:
            continue
        seen.add(url)
        result = {"title": title, "url": url,
                  "content": _text(item.get("raw_content") or item.get("extract") or item.get("content") or item.get("body") or item.get("snippet") or item.get("description") or item.get("desc") or item.get("summary")),
                  "source_level": "web_content" if _text(item.get("raw_content") or item.get("extract")) else level, "retrieved_at": now, "provider": provider}
        published = item.get("published_at") or item.get("published_date") or item.get("published")
        if isinstance(published, str) and published.strip():
            result["published_at"] = published.strip()[:100]
        results.append(result)
        if len(results) >= limit:
            break
    if items and not results:
        raise ValueError("invalid results")
    return results


def _infoquest(data):
    groups = _items(data, "search_result", "results")
    items = []
    for group in groups:
        content = group["content"]["results"]
        if not isinstance(content, dict):
            raise ValueError("invalid response")
        organic = content.get("organic", [])
        stories = content.get("top_stories", {})
        if not isinstance(organic, list) or not isinstance(stories, dict):
            raise ValueError("invalid response")
        news = stories.get("items", [])
        if not isinstance(news, list):
            raise ValueError("invalid response")
        items.extend(organic + news)
    return items


def _arxiv(response):
    root = ElementTree.fromstring(response.content)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    if root.tag != "{http://www.w3.org/2005/Atom}feed":
        raise ValueError("invalid feed")
    items = []
    for entry in root.findall("a:entry", ns):
        url = entry.findtext("a:id", default="", namespaces=ns)
        if "/api/errors" in url:
            raise ValueError("arxiv error")
        items.append({"title": entry.findtext("a:title", default="", namespaces=ns), "url": url,
                      "summary": entry.findtext("a:summary", default="", namespaces=ns),
                      "published": entry.findtext("a:published", default="", namespaces=ns)})
    return items


class _ConfigError(Exception):
    pass


def search_web(query: str, limit: int = 5) -> list[dict]:
    """Search one configured provider; failures never expose upstream bodies or credentials."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("搜索内容不能为空")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("搜索数量必须是正整数")
    limit = min(limit, 20)
    try:
        config = web_search_config()
    except Exception:
        raise RuntimeError("网络搜索配置无效") from None
    engine = config.get("engine") or "duckduckgo"
    key = config.get("api_key") or ""
    if engine not in {"duckduckgo", "tavily", "infoquest", "brave_search", "serper", "arxiv", "searx", "wikipedia"}:
        raise RuntimeError("不支持的网络搜索服务")
    if engine in {"tavily", "infoquest", "brave_search", "serper"} and not key:
        raise RuntimeError("网络搜索服务缺少 API 密钥")
    try:
        if engine == "duckduckgo":
            try:
                items = DDGS(timeout=_TIMEOUT).text(query, max_results=limit, backend="duckduckgo")
            except DDGSException as exc:
                # ddgs emits this sentinel only when no backend exception was reported.
                if exc.args == ("No results found.",):
                    items = []
                else:
                    raise
        elif engine == "tavily":
            data = _request("POST", "https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"},
                            json={"query": query, "max_results": limit, "include_raw_content": True}).json()
            items = _items(data, "results")
        elif engine == "infoquest":
            data = _request("POST", "https://search.infoquest.bytepluses.com", headers={"Authorization": f"Bearer {key}"},
                            json={"query": query, "format": "JSON"}).json()
            items = _infoquest(data)
        elif engine == "brave_search":
            data = _request("GET", "https://api.search.brave.com/res/v1/web/search", headers={"X-Subscription-Token": key},
                            params={"q": query, "count": limit}).json()
            items = _items(data, "web", "results")
        elif engine == "serper":
            data = _request("POST", "https://google.serper.dev/search", headers={"X-API-KEY": key},
                            json={"q": query, "num": limit}).json()
            items = _items(data, "organic")
        elif engine == "searx":
            base = config.get("base_url") or ""
            parts = urlsplit(base)
            if not _url(base) or parts.query or parts.fragment or "?" in base or "#" in base:
                raise _ConfigError("请配置有效的 Searx 地址")
            data = _request("GET", base.rstrip("/") + "/search", params={"q": query, "format": "json"}).json()
            items = _items(data, "results")
        elif engine == "arxiv":
            response = _request("GET", "https://export.arxiv.org/api/query", params={"search_query": "all:" + query, "start": 0, "max_results": limit})
            items = _arxiv(response)
        else:
            lang = config.get("wikipedia_lang") or "zh"
            if not isinstance(lang, str) or not re.fullmatch(r"[a-z]{2,12}(?:-[a-z0-9]{1,12})*", lang):
                raise _ConfigError("Wikipedia 语言设置无效")
            data = _request("GET", f"https://{lang}.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "srsearch": query, "srlimit": limit, "format": "json"}).json()
            items = _items(data, "query", "search")
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not isinstance(item.get("pageid"), int):
                    raise ValueError("invalid response")
                item["url"] = f"https://{lang}.wikipedia.org/wiki/" + quote(item["title"].replace(" ", "_"), safe="")
            if items:
                extracts = _request("GET", f"https://{lang}.wikipedia.org/w/api.php", params={"action": "query", "prop": "extracts", "exlimit": "max", "exintro": 1, "explaintext": 1, "pageids": "|".join(str(item["pageid"]) for item in items), "format": "json"}).json()
                pages = extracts["query"]["pages"]
                if not isinstance(pages, dict):
                    raise ValueError("invalid response")
                for item in items:
                    page = pages.get(str(item["pageid"]), {})
                    if not isinstance(page, dict):
                        raise ValueError("invalid response")
                    item["extract"] = page.get("extract")
        return _normalise(items, engine, limit, "abstract" if engine == "arxiv" else "web_snippet")
    except _ConfigError as exc:
        raise RuntimeError(str(exc)) from None
    except Exception:
        raise RuntimeError("网络搜索失败；请检查服务配置、网络或稍后重试") from None
