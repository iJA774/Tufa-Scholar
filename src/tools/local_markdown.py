"""Tools for browsing and retrieving project Markdown."""
from pydantic import BaseModel, ConfigDict, Field
from src.literature.markdown_corpus import list_local_domains, select_domains, read_markdown

class SelectDomains(BaseModel):
    model_config = ConfigDict(extra='forbid')
    domains: list[str] = Field(max_length=20)
    offset: int = Field(default=0, ge=0, le=100000)

class ReadMarkdown(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(min_length=1, max_length=128)
    query: str = Field(default='', max_length=500)
    offset: int = Field(default=0, ge=0, le=100000)

def register_local_markdown_tools(registry):
    registry.register('select_local_domains', '按领域列出当前范围内的文档元数据。', SelectDomains, lambda p,c: select_domains(c,p.domains,p.offset), {'local','all','collection'})
    registry.register('read_local_markdown', '根据查询读取当前范围内 Markdown 文档片段。', ReadMarkdown, lambda p,c: read_markdown(c,p.document_id,p.query,p.offset), {'local','all','collection'})
