"""Tool adapters for paper and web search."""
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.integrations.web_search import search_web
from src.literature.retrieval import retrieve, retrieve_collection


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


def _search_papers(params, context):
    if context.source != 'collection':
        return retrieve(context.project_id, params.query, context.source)
    return retrieve_collection(context.project_id, params.query, context.paper_ids)


def _search_web(params, context):
    return search_web(params.query, limit=params.limit)


def register_search_tools(registry):
    registry.register('search_papers', '在当前项目及指定来源中检索论文。', SearchPapersInput, _search_papers, {'local', 'openalex', 'all', 'collection'})
    registry.register('search_web', '使用已配置的网页搜索服务检索网页。', SearchWebInput, _search_web, {'web'})
