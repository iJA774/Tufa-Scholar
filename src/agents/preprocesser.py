from typing import Annotated
from pydantic import BaseModel, Field, field_validator

from src.infrastructure.diagnostics import diagnostic_scope
from src.integrations.model import structured


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


def classify_markdown(markdown: str, model_name: str) -> Classification:
    if len(markdown) > 30000:
        markdown = markdown[:20000] + markdown[-10000:]
    with diagnostic_scope(suppress_artifacts=True):
        return structured('literature/preprocesser', {'markdown': markdown}, Classification,
                          model_options={'model_name': model_name, 'reasoning_effort': 'none'})
