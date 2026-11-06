"""Small explicit registry for context-scoped research tools."""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class ToolContext:
    project_id: int
    source: Literal['local', 'openalex', 'all', 'collection', 'web']
    paper_ids: tuple[int, ...] = ()

    def __post_init__(self):
        if self.source not in {'local', 'openalex', 'all', 'collection', 'web'}:
            raise ValueError('不支持的检索来源')
        object.__setattr__(self, 'paper_ids', tuple(self.paper_ids))


class ToolRegistry:
    def __init__(self):
        self._tools = {}
        from .research import register_research_tools
        register_research_tools(self)

    def register(self, name, description, parameters: type[BaseModel], handler, sources):
        if name in self._tools:
            raise ValueError('工具名称重复')
        self._tools[name] = (description, parameters, handler, frozenset(sources))

    def list_tools(self, context: ToolContext):
        return [{'name': name, 'description': description, 'input_schema': model.model_json_schema()}
                for name, (description, model, _, sources) in self._tools.items()
                if context.source in sources]

    def invoke(self, name, arguments, *, context: ToolContext):
        tool = self._tools.get(name)
        if tool is None or context.source not in tool[3]:
            raise ValueError('工具不存在或不适用于当前范围')
        try:
            params = tool[1].model_validate(arguments)
        except ValidationError:
            raise ValueError('工具参数无效') from None
        return tool[2](params, context)
