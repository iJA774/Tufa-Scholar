"""Validate and adapt evidence tool calls to research services."""
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.literature.evidence import collect_evidence
from .search import SearchPapersInput
from .registry import ToolCallValidationError, _safe_errors


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
    content_offset: int | None = Field(default=None, ge=0)
    url: str = Field(min_length=1)
    content: str
    source_level: Literal['web_snippet', 'web_content', 'abstract']
    provider: str = Field(min_length=1)
    retrieved_at: str = Field(min_length=1)


class CollectEvidenceInput(SearchPapersInput):
    relevance_queries: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=8, description='相关性查询，最多八条')
    papers: list[Candidate] = Field(max_length=6, description='已检索的论文或网页结果，最多六条')

    @field_validator('relevance_queries')
    @classmethod
    def validate_relevance_queries(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError('相关性查询无效')
        return [value.strip() for value in values]


def _collect_evidence(params, context):
    if context.source == 'web':
        items = []
        for index, candidate in enumerate(params.papers):
            try:
                item = WebCandidate.model_validate(candidate.model_dump(exclude_unset=True)).model_dump(exclude_unset=True)
            except ValidationError as exc:
                errors = _safe_errors(exc, WebCandidate.model_json_schema())
                for error in errors:
                    error['path'] = ['papers', index, *error['path']]
                raise ToolCallValidationError('工具参数无效', 'invalid_arguments', errors) from None
            if item['content'].strip():
                try:
                    parts = urlsplit(item['url'])
                    valid = parts.scheme.lower() in {'http', 'https'} and parts.hostname and not parts.username and not parts.password
                except ValueError:
                    valid = False
                if not valid:
                    raise ToolCallValidationError('工具参数无效', 'invalid_arguments',
                                                  [{'type': 'value_error', 'path': ['papers', index, 'url']}]) from None
            items.append(item)
    else:
        items = [item.model_dump(exclude_unset=True) for item in params.papers]
    if params.relevance_queries:
        return collect_evidence(context.project_id, context.source, context.paper_ids, params.query, items, relevance_queries=params.relevance_queries)
    return collect_evidence(context.project_id, context.source, context.paper_ids, params.query, items)


def register_read_paper_tools(registry):
    registry.register('collect_evidence', '从检索结果中提取带来源定位的证据。', CollectEvidenceInput, _collect_evidence, {'local', 'openalex', 'all', 'collection', 'web'})
