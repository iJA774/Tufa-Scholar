import re
from typing import Annotated
from pydantic import BaseModel, Field, field_validator

from src.infrastructure.diagnostics import diagnostic_scope
from src.integrations.model import StructuredOutputError, structured


class Classification(BaseModel):
    domains: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(min_length=1, max_length=30)
    tags: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=20, pattern=r"^[^\r\n]+$")

    @field_validator('domains', 'tags', mode='before')
    @classmethod
    def clean_labels(cls, values):
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError('labels must be a list of strings')
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator('description', mode='before')
    @classmethod
    def clean_description(cls, value):
        return value.strip() if isinstance(value, str) else value


_SECTION = re.compile(r'(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:\d+(?:\.\d+)*[.\s]+)?(abstract|introduction|conclusions?|摘要|引言|结论)(?=[\s:：]|$)[^\n]*')


def _sample(markdown: str, limit: int) -> str:
    if len(markdown) <= limit:
        return markdown
    # Keep the beginning for title and authors, then bounded sections from the
    # actual document. Do not fill remaining space with references or converter noise.
    matches = list(_SECTION.finditer(markdown))
    parts = [markdown[:limit // 4]]
    section_budget = (limit - len(parts[0])) // 3
    for group in (('abstract', '摘要'), ('introduction', '引言'), ('conclusion', '结论')):
        match = next((m for m in matches if m.group(1).lower().startswith(group)), None)
        if match:
            end = min(len(markdown), match.start() + section_budget)
            parts.append(markdown[match.start():end])
    if len(parts) == 1:
        return markdown[:limit]
    return '\n\n'.join(parts)[:limit]


def classify_markdown(markdown: str, model_name: str) -> Classification:
    sample = _sample(markdown, 12000)
    with diagnostic_scope(suppress_artifacts=True):
        try:
            return structured('literature/preprocesser', {'markdown': sample}, Classification,
                              model_options={'model_name': model_name, 'reasoning_effort': 'none'})
        except StructuredOutputError as exc:
            shorter = _sample(markdown, 4000)
            if exc.category != 'invalid' or len(shorter) >= len(sample):
                raise
            return structured('literature/preprocesser', {'markdown': shorter}, Classification,
                              model_options={'model_name': model_name, 'reasoning_effort': 'none'})
