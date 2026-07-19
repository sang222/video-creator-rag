import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


DailyToPackageState = Literal[
    "DAILY_DECISION_ACCEPTED",
    "PROJECT_ADMITTED",
    "EFFECTIVE_CONTEXT_READY",
    "AWAITING_RESEARCH",
    "RESEARCH_READY",
    "PACKAGE_BUILDING",
    "PACKAGE_READY_FOR_HUMAN_REVIEW",
    "PACKAGE_HUMAN_REVIEW_PASSED",
    "READY_FOR_LONG_PRODUCTION",
    "BLOCKED_POLICY",
    "FAILED_TECHNICAL",
]

HumanReviewState = Literal["NOT_READY", "PENDING", "PASS", "BLOCKED"]


class DailyToPackageRequest(BaseModel):
    """Control input for the bridge; creative truth is resolved from lineage."""

    daily_idea_decision_id: uuid.UUID
    created_by_user_id: uuid.UUID | None = None
    approved_research_artifact_version_id: uuid.UUID | None = None
    operator_confirmation_ref: str | None = None

    model_config = ConfigDict(extra="forbid")


class DailyToPackageRunRequest(BaseModel):
    """Narrow application trigger; creative inputs always come from frozen lineage."""

    created_by_user_id: uuid.UUID | None = None
    approved_research_artifact_version_id: uuid.UUID | None = None
    operator_confirmation_ref: str | None = None

    model_config = ConfigDict(extra="forbid")


class DailyToPackageReceiptContent(BaseModel):
    schema_version: Literal["d2p1.daily-to-package-receipt.v1"] = "d2p1.daily-to-package-receipt.v1"
    orchestrator_version: str
    state: DailyToPackageState
    last_successful_state: DailyToPackageState | None = None
    daily_idea_decision_ref: dict[str, Any]
    project_ref: dict[str, Any] | None = None
    admission_receipt_ref: dict[str, Any] | None = None
    effective_context_ref: dict[str, Any] | None = None
    niche_contract_digest_ref: dict[str, Any] | None = None
    editorial_slot_ref: dict[str, Any] | None = None
    research_assignment_ref: dict[str, Any] | None = None
    research_pack_ref: dict[str, Any] | None = None
    scripted_package_ref: dict[str, Any] | None = None
    package_human_review_ref: dict[str, Any] | None = None
    niche_gate_refs: list[dict[str, Any]] = Field(default_factory=list)
    human_review_state: HumanReviewState = "NOT_READY"
    provider_calls_made: int = Field(default=0, ge=0)
    media_calls_made: int = Field(default=0, ge=0)
    idempotency_fingerprint: str | None = None
    blockers: list[str] = Field(default_factory=list)
    exact_next_action: str

    model_config = ConfigDict(extra="forbid")


class DailyToPackageStatusRead(BaseModel):
    daily_idea_decision_id: uuid.UUID
    current_state: DailyToPackageState
    project: dict[str, Any] | None = None
    effective_context: dict[str, Any] | None = None
    research: dict[str, Any] = Field(default_factory=dict)
    package: dict[str, Any] | None = None
    package_human_review: dict[str, Any] | None = None
    niche_gates: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    exact_next_action: str
    human_review_state: HumanReviewState = "NOT_READY"
    idempotency_fingerprint: str | None = None
    receipt: dict[str, Any] | None = None
    provider_calls_made: int = Field(default=0, ge=0)
    media_calls_made: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid")
