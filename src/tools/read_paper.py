"""Validate and adapt evidence tool calls to research services."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.literature.evidence import collect_evidence
from .search import SearchPapersInput


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


def _collect_evidence(params, context):
    try:
        if context.source == 'web':
            items = [WebCandidate.model_validate(item.model_dump(exclude_unset=True)).model_dump(exclude_unset=True) for item in params.papers]
            for item in items:
                if not item['content'].strip():
                    continue
                parts = urlsplit(item['url'])
                if parts.scheme.lower() not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
                    raise ValueError
        else:
            items = [item.model_dump(exclude_unset=True) for item in params.papers]
    except (ValidationError, ValueError):
        raise ValueError('工具参数无效') from None
    return collect_evidence(context.project_id, context.source, context.paper_ids, params.query, items)


def register_read_paper_tools(registry):
    registry.register('collect_evidence', '从检索结果中提取带来源定位的证据。', CollectEvidenceInput, _collect_evidence, {'local', 'openalex', 'all', 'collection', 'web'})
