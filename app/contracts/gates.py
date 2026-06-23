from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.time import utc_now


class ReasonCodeDefinition(BaseModel):
    code: str
    description: str

    model_config = ConfigDict(extra="forbid")


class GateResult(BaseModel):
    gate_key: str
    gate_version: str
    decision: Literal["PASS", "BLOCK", "REVIEW_REQUIRED"]
    reason_codes: list[str] = Field(min_length=1)
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: AwareDatetime = Field(default_factory=utc_now)
    correlation_id: str

    model_config = ConfigDict(extra="forbid")
