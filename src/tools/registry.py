"""Small explicit registry for context-scoped research tools."""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError


class ToolCallValidationError(ValueError):
    """Safe, structured rejection before a tool handler is invoked."""
    def __init__(self, message, code, errors=None):
        super().__init__(message)
        self.code = code
        self.errors = errors or []


def _safe_errors(exc, schema):
    fields = set()
    def collect(value):
        if isinstance(value, dict):
            fields.update(value.get('properties', {}).keys())
            for child in value.values(): collect(child)
        elif isinstance(value, list):
            for child in value: collect(child)
    collect(schema)
    allowed = {'missing', 'extra_forbidden', 'string_type', 'string_too_short', 'string_too_long',
               'list_type', 'too_short', 'too_long', 'int_type', 'int_parsing', 'greater_than_equal',
               'less_than_equal', 'literal_error', 'model_type', 'value_error', 'bool_type'}
    errors = []
    for error in exc.errors(include_input=False, include_context=False, include_url=False):
        path = [part if type(part) is int or (isinstance(part, str) and part in fields) else '*'
                for part in error['loc']]
        if error['type'] == 'extra_forbidden' and path:
            path[-1] = '*'
        errors.append({'type': error['type'] if error['type'] in allowed else 'validation_error', 'path': path})
    return errors


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
        from .search import register_search_tools
        from .read_paper import register_read_paper_tools
        from .read_web_page import register_read_web_page_tools
        register_search_tools(self)
        register_read_paper_tools(self)
        register_read_web_page_tools(self)

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
        if tool is None:
            raise ToolCallValidationError('工具不存在或不适用于当前范围', 'unknown_tool')
        if context.source not in tool[3]:
            raise ToolCallValidationError('工具不存在或不适用于当前范围', 'disallowed_tool')
        try:
            params = tool[1].model_validate(arguments)
        except ValidationError as exc:
            raise ToolCallValidationError('工具参数无效', 'invalid_arguments', _safe_errors(exc, tool[1].model_json_schema())) from None
        return tool[2](params, context)
