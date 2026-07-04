from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


FreezeStatus = Literal["PASS", "BLOCKED", "REVIEW_REQUIRED"]
InvariantStatus = Literal["PASS", "BLOCKED", "REVIEW_REQUIRED", "WARNING"]
InvariantSeverity = Literal["P0", "P1", "P2", "P3"]


class RuntimeInvariantCheckRead(BaseModel):
    invariant_key: str
    description: str
    severity: InvariantSeverity
    status: InvariantStatus
    reason_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    verification_method: str

    model_config = ConfigDict(extra="forbid")


class RuntimeLTSFreezeCheckRead(BaseModel):
    freeze_status: FreezeStatus
    blocker_reason_codes: list[str] = Field(default_factory=list)
    warning_reason_codes: list[str] = Field(default_factory=list)
    verified_components: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    test_refs: list[str] = Field(default_factory=list)
    generated_at: AwareDatetime
    invariant_checks: list[RuntimeInvariantCheckRead] = Field(default_factory=list)
    no_provider_media_upload_execution: bool = True
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
