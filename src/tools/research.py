"""Context-scoped research operations, independent of the workflow graph."""
import hashlib
import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pypdf import PdfReader

from src import repository
from src.integrations.web_search import search_web
from src.literature.retrieval import retrieve


class SearchPapersInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, description='用于检索文献的查询文本')

    @field_validator('query')
    @classmethod
    def clean_query(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('查询不能为空')
        return value


class SearchWebInput(SearchPapersInput):
    limit: int = Field(default=5, ge=1, le=5, description='最多返回的网页数量')


class Candidate(BaseModel):
    model_config = ConfigDict(extra='allow')
    title: str = Field(description='来源标题')
    id: int | None = Field(default=None, description='项目论文 ID')
    abstract: str | None = Field(default=None, description='论文摘要')
    url: str | None = Field(default=None, description='网页来源 URL')
    content: str | None = Field(default=None, description='网页文本片段')
    source_level: str | None = Field(default=None, description='来源层级')
    provider: str | None = Field(default=None, description='网页搜索服务')
    retrieved_at: str | None = Field(default=None, description='网页获取时间')


class WebCandidate(Candidate):
    url: str = Field(min_length=1)
    content: str
    source_level: Literal['web_snippet', 'web_content', 'abstract']
    provider: str = Field(min_length=1)
    retrieved_at: str = Field(min_length=1)


class CollectEvidenceInput(SearchPapersInput):
    papers: list[Candidate] = Field(max_length=6, description='已检索的论文或网页结果，最多六条')


def _search_papers(params, context):
    if context.source != 'collection':
        return retrieve(context.project_id, params.query, context.source)
    allowed = set(context.paper_ids)
    candidates = repository.project_evidence(context.project_id, list(allowed)) if allowed else []
    candidates = [p for p in candidates if p['id'] in allowed]
    ranked = repository.local_search(context.project_id, params.query, list(allowed)) if allowed else []
    order = {p['id']: i for i, p in enumerate(ranked) if p['id'] in allowed}
    candidates.sort(key=lambda p: order.get(p['id'], len(order)))
    return {'query': params.query, 'source': 'collection', 'method': 'project collection keyword ranking', 'results': candidates[:6], 'warnings': []}


def _search_web(params, context):
    return search_web(params.query, limit=params.limit)


def _collect_evidence(params, context):
    if context.source == 'web':
        try:
            items = [WebCandidate.model_validate(item.model_dump(exclude_unset=True)).model_dump(exclude_unset=True) for item in params.papers]
        except ValidationError:
            raise ValueError('工具参数无效') from None
        found = []
        seen = set()
        for item in items:
            text = item.get('content', '').strip()[:1200]
            if not text:
                continue
            try:
                parts = urlsplit(item['url'])
                if parts.scheme.lower() not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
                    raise ValueError
                url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ''))
            except ValueError:
                raise ValueError('工具参数无效') from None
            if url in seen:
                continue
            seen.add(url)
            entry = {
                'id': 'w' + hashlib.sha256(url.encode('utf-8')).hexdigest()[:16],
                'title': item['title'], 'original_url': item['url'],
                'source_level': item['source_level'], 'provider': item['provider'],
                'retrieved_at': item['retrieved_at'], 'text': text,
            }
            if item.get('published_at'):
                entry['published_at'] = item['published_at']
            found.append(entry)
        return found
    allowed = list(context.paper_ids) if context.source == 'collection' else None
    records = repository.project_evidence(context.project_id, allowed) if allowed is not None else repository.project_evidence(context.project_id)
    local = {p['id']: p for p in records if allowed is None or p['id'] in allowed}
    found = []
    for candidate in params.papers:
        incoming = candidate.model_dump(exclude_unset=True)
        record = local.get(incoming.get('id'))
        if context.source in {'local', 'collection'} and not record:
            continue
        p = record if record else {**incoming, 'id': None}
        if record:
            for v in record['versions']:
                if v['text_status'] != 'text_available':
                    continue
                reader = PdfReader(v['path'])
                terms = [t.lower() for t in re.findall(r'\w+', params.query) if len(t) > 1]
                pages = []
                for page_num, page in enumerate(reader.pages[:20], 1):
                    text = (page.extract_text() or '').strip()
                    if text:
                        positions = [text.lower().find(term) for term in terms if term in text.lower()]
                        start = max(0, min(positions) - 200) if positions else 0
                        pages.append((len(positions), page_num, text[start:start + 1200]))
                for _, page_num, text in sorted(pages, key=lambda x: (-x[0], x[1]))[:3]:
                    found.append({'id': f"p{p['id']}v{v['id']}p{page_num}", 'paper_id': p['id'], 'title': p['title'], 'source_level': 'full_text', 'version_id': v['id'], 'page': page_num, 'text': text})
                break
        if not any(e['title'] == p['title'] for e in found) and p.get('abstract'):
            found.append({'id': f"a{len(found)+1}", 'paper_id': p.get('id'), 'title': p['title'], 'source_level': 'abstract', 'version_id': None, 'page': None, 'text': p['abstract'][:1200], 'original_url': p.get('original_url')})
    return found


def register_research_tools(registry):
    registry.register('search_papers', '在当前项目及指定来源中检索论文。', SearchPapersInput, _search_papers, {'local', 'openalex', 'all', 'collection'})
    registry.register('search_web', '使用已配置的网页搜索服务检索网页。', SearchWebInput, _search_web, {'web'})
    registry.register('collect_evidence', '从检索结果中提取带来源定位的证据。', CollectEvidenceInput, _collect_evidence, {'local', 'openalex', 'all', 'collection', 'web'})
