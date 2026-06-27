import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


ReadinessCheckType = Literal["CONFIG", "CREDENTIAL", "CONNECTION", "REAL_SMOKE", "CAPABILITY", "BUDGET", "SECURITY"]
ReadinessCheckState = Literal["PASS", "WARNING", "BLOCKED", "SKIPPED", "FAILED", "UNKNOWN"]
ReadinessSnapshotState = Literal["READY", "PARTIAL", "BLOCKED", "UNKNOWN"]
RealSmokeRunState = Literal["SKIPPED", "RUNNING", "PASS", "FAILED", "BLOCKED"]


class ProviderReadinessCheckRead(BaseModel):
    id: uuid.UUID | None = None
    provider_key: str
    provider_type: str
    check_type: ReadinessCheckType
    check_state: ReadinessCheckState
    operator_summary: str
    next_action: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    technical_appendix: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ProviderSummaryRead(BaseModel):
    provider_key: str
    provider_name: str
    provider_type: str
    readiness_state: ReadinessCheckState
    status_label: str
    operator_summary: str
    next_action: str
    smoke_state: RealSmokeRunState | ReadinessCheckState | None = None
    learning_authority: str | None = None
    safe_config: dict[str, Any] = Field(default_factory=dict)
    missing_env_keys: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProviderBudgetCardRead(BaseModel):
    key: str
    provider_name: str
    role: str
    configured_plan: str | None = None
    configured_monthly_cap: str | None = None
    budget_basis: str
    readiness_state: ReadinessCheckState
    missing_env_keys: list[str] = Field(default_factory=list)
    note: str
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProviderReadinessSnapshotRead(BaseModel):
    id: uuid.UUID
    snapshot_state: ReadinessSnapshotState
    provider_summaries: list[dict[str, Any]] = Field(default_factory=list)
    blocking_items: list[dict[str, Any]] = Field(default_factory=list)
    warning_items: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class RealSmokeRunRead(BaseModel):
    id: uuid.UUID
    provider_key: str
    smoke_type: str
    run_state: RealSmokeRunState
    env_flags: dict[str, Any] = Field(default_factory=dict)
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    result_summary: str | None = None
    technical_appendix: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class IntegrationReadinessRead(BaseModel):
    generated_at: AwareDatetime
    snapshot_state: ReadinessSnapshotState
    latest_snapshot_id: uuid.UUID | None = None
    provider_summaries: list[ProviderSummaryRead]
    checks: list[ProviderReadinessCheckRead]
    blocking_items: list[dict[str, Any]] = Field(default_factory=list)
    warning_items: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    budget_cards: list[ProviderBudgetCardRead] = Field(default_factory=list)
    security_summary: dict[str, Any] = Field(default_factory=dict)
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ReadinessRunRequest(BaseModel):
    persist_checks: bool = True

    model_config = ConfigDict(extra="forbid")


class ProviderSmokeRequest(BaseModel):
    smoke_type: str | None = None

    model_config = ConfigDict(extra="forbid")
