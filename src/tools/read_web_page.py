"""Read a bounded slice of a public web page."""
from pydantic import BaseModel, ConfigDict, Field
from src.integrations.web_page import fetch_page


class ReadWebPageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=4096)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=1000, ge=1, le=4000)


def _read(params, context):
    result = fetch_page(params.url)
    content = result['markdown']
    end = min(len(content), params.offset + params.limit)
    return {**result, 'markdown': content[params.offset:end], 'offset': params.offset,
            'next_offset': end if end < len(content) else None, 'truncated': end < len(content)}


def register_read_web_page_tools(registry):
    registry.register('read_web_page', '读取公开网页的分页正文。', ReadWebPageInput, _read, {'web'})
