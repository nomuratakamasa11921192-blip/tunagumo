from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Company(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    business: str
    context: str = ""


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_budget_usd: float = Field(gt=0)
    max_total_steps: int = Field(gt=0)
    max_retries_per_dept: int = Field(ge=0)
    max_rejections: int = Field(ge=0)
    max_clarify_rounds: int = Field(ge=0)
    approval_deadline_hours: int = Field(gt=0)
    max_parallel_depts: int = Field(ge=1)


class Compliance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forbidden_words: list[str] = Field(default_factory=list)
    required_format: str = "markdown"


class Department(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    model: str
    depends_on: list[str] = Field(default_factory=list)
    system_prompt: str
    # Phase 11: この部署が使えるツール名(名前の参照のみ。実装はsrc/tools/配下)。
    # 設定にない名前が指定されたら起動時に検証エラーにする(config_loader.py)。
    tools: list[str] = Field(default_factory=list)

    # ceo_office のみ使う
    triage_model: str | None = None
    triage_prompt: str | None = None

    # qa_auditor のみ使う
    quality_fail_threshold: int | None = None

    @model_validator(mode="after")
    def system_prompt_not_blank(self) -> "Department":
        if not self.system_prompt.strip():
            raise ValueError("system_prompt が空です")
        return self


class ModelPricing(BaseModel):
    """百万トークンあたりの単価(USD)。Anthropicの価格改定に追従するため設定側に持つ(4-1)。"""

    model_config = ConfigDict(extra="forbid")

    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    cache_write_per_mtok: float = Field(ge=0, default=0)
    cache_read_per_mtok: float = Field(ge=0, default=0)


class Pricing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updated_at: date
    models: dict[str, ModelPricing]


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: Company
    limits: Limits
    compliance: Compliance
    pricing: Pricing
    departments: dict[str, Department]
