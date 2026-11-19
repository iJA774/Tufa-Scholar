"""Request contracts for benchmark batches and controls."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator

from src.infrastructure.settings import validate_base_url

Metric = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
Source = Literal["auto", "web", "openalex", "all", "local"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Budget(Input):
    max_tasks: int = Field(default=5, ge=1, le=100)
    task_seconds: int = Field(default=1800, ge=1, le=14400)
    total_seconds: int = Field(default=7200, ge=1, le=604800)
    max_requests: int = Field(default=500, ge=1, le=100000)
    max_tokens: int | None = Field(default=None, ge=1, le=10**10)
    max_cost: float | None = Field(default=None, gt=0, le=100000)


class Judge(Input):
    base_url: str = "https://openrouter.ai/api/v1"
    race_model: str = Field(default="openai/gpt-5.5", min_length=1, max_length=200)
    fact_model: str = Field(default="openai/gpt-5.4-mini", min_length=1, max_length=200)
    clean_model: str = Field(default="openai/gpt-5.6-luna", min_length=1, max_length=200)
    reader_url: str = "https://r.jina.ai"
    timeout: int = Field(default=120, ge=1, le=600)
    retries: int = Field(default=2, ge=0, le=5)
    api_key: SecretStr | None = None
    reader_key: SecretStr | None = None

    def snapshot(self):
        public = self.model_dump(exclude={"api_key", "reader_key"})
        for key in ("base_url", "reader_url"):
            if not public[key]:
                raise ValueError("请填写 Judge 和正文提取服务地址")
            validate_base_url(public[key])
            public[key] = public[key].rstrip("/")
        return public


class CreateRun(Input):
    request_id: UUID
    dataset_id: str = "deepresearch_bench"
    name: str = Field(default="DeepResearch Bench", min_length=1, max_length=120)
    language: Literal["zh", "en"] | None = None
    topic: str | None = Field(default=None, max_length=100)
    ids: list[int] = Field(default_factory=list, max_length=100)
    sample: int | None = Field(default=None, ge=1, le=100)
    seed: int = Field(default=42, ge=-(2**32), le=2**32)
    model_name: str | None = Field(default=None, max_length=200)
    search_engine: str | None = Field(default=None, max_length=40)
    research_mode: Literal["deep", "auto"] = "deep"
    research_depth: Literal["light", "standard", "deep"] = "standard"
    source: Source = "auto"
    network_search: bool = True
    prefer_primary_evidence: bool = True
    relevance_filter: Literal["relaxed", "standard", "strict"] = "standard"
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    metrics: list[Metric] = Field(default_factory=list, max_length=12)
    judge: Judge = Field(default_factory=Judge)
    budget: Budget = Field(default_factory=Budget)
    authorize: StrictBool

    @field_validator("authorize")
    @classmethod
    def require_consent(cls, value):
        if not value:
            raise ValueError("需要明确授权")
        return value


class Action(Input):
    action: Literal["pause", "resume", "stop", "skip", "retry", "evaluate"]
    task_id: int | None = None
    metrics: list[Metric] = Field(default_factory=list, max_length=12)
    judge: Judge | None = None
    budget: Budget | None = None
    authorize: StrictBool = False
