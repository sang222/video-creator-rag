"""Safe operator contracts for selecting and launching frozen v2 planning sources."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


PlanningLane = Literal["LONG_FORM"]
PlanningSourceKind = Literal["LONG_FORM_PLAN"]
PlanningOptionState = Literal[
    "READY",
    "ALREADY_ADMITTED",
    "WORKFLOW_STARTED",
    "BLOCKED",
]


class OperatorPlanningOptionRead(BaseModel):
    """Friendly read model; raw authority identifiers stay in the appendix."""

    source_id: uuid.UUID
    source_type: PlanningSourceKind
    lane: PlanningLane
    title: str
    company_label: str
    channel_label: str
    slot_label: str
    assignment_label: str
    duration_label: str | None = None
    state: PlanningOptionState
    status_label: str
    launchable: bool
    guidance: str
    project_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class OperatorPlanningCatalogRead(BaseModel):
    generated_at: AwareDatetime
    long_form_options: list[OperatorPlanningOptionRead] = Field(default_factory=list)
    safety_notice: str = (
        "Thao tác này đóng băng support authority qua LLMRouter trong trần ngân "
        "sách, tạo hoặc dùng lại admission typed v2 rồi khởi động workflow bền "
        "vững. VCOS không gọi media provider, không chạy MR1 và không tự "
        "publish/upload."
    )
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class LongFormPlanningLaunchRequest(BaseModel):
    editorial_calendar_slot_id: uuid.UUID
    max_attempts: int = Field(default=5, ge=1, le=5)
    max_budget_usd: Decimal = Field(default=Decimal("0"), ge=0, le=250)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)

    model_config = ConfigDict(extra="forbid")


class OperatorPlanningPrepareRequest(BaseModel):
    """ID-only request; all creative and authority inputs are server-resolved."""

    source_type: PlanningSourceKind
    source_id: uuid.UUID
    max_budget_usd: Decimal = Field(default=Decimal("0"), ge=0, le=250)

    model_config = ConfigDict(extra="forbid")


class OperatorPlanningStartRequest(BaseModel):
    """One-action prepare + start command from an exact catalog source."""

    source_type: PlanningSourceKind
    source_id: uuid.UUID
    max_attempts: int = Field(default=5, ge=1, le=5)
    max_budget_usd: Decimal = Field(default=Decimal("0"), ge=0, le=250)
    idempotency_key: str = Field(min_length=1, max_length=160)

    model_config = ConfigDict(extra="forbid")


class OperatorPlanningPrepareRead(BaseModel):
    source_type: PlanningSourceKind
    source_id: uuid.UUID
    lane: PlanningLane
    title: str
    admission_id: uuid.UUID
    project_id: uuid.UUID
    support_artifact_id: uuid.UUID
    support_artifact_version_id: uuid.UUID
    envelope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["APPROVED"]
    replayed: bool
    approved_script_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_script_word_count: int = Field(ge=24)
    exact_source_refs: list[dict[str, Any]] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    next_action: str
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class OperatorPlanningLaunchRead(BaseModel):
    lane: PlanningLane
    title: str
    admission_id: uuid.UUID
    project_id: uuid.UUID
    workflow_run_id: uuid.UUID
    workflow_state: str
    reused_admission: bool
    reused_workflow: bool
    next_action: str
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
