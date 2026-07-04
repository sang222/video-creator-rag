import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


RenderRevisionStatus = Literal[
    "DRAFT",
    "READY_FOR_COST_ESTIMATE",
    "COST_ESTIMATED",
    "APPROVAL_REQUIRED",
    "APPROVED_FOR_PROVIDER_BOUNDARY",
    "BLOCKED",
    "SUPERSEDED",
]
CostEstimateStatus = Literal[
    "ESTIMATE_NOT_AVAILABLE",
    "ESTIMATE_PENDING_PROVIDER_CONFIG",
    "ESTIMATE_REQUIRES_REAL_PROVIDER",
    "ESTIMATED",
    "BLOCKED",
]
HumanPaidRenderApprovalStatus = Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED", "REVOKED"]
ProviderJobStatus = Literal[
    "NOT_SUBMITTED",
    "SUBMISSION_BLOCKED",
    "SUBMITTED",
    "POLLING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "TIMED_OUT",
    "RESUME_REQUIRED",
]
PaidProviderCallType = Literal["VALIDATION_ONLY", "SUBMIT", "POLL", "RESUME", "CANCEL"]
PaidProviderCallStatus = Literal["BLOCKED", "ALLOWED_NOT_EXECUTED", "EXECUTED", "FAILED"]
PaidAttemptLimitStatus = Literal["PASS", "BLOCKED", "REVIEW_REQUIRED"]
ProviderBoundaryStatus = Literal[
    "READY_FOR_PROVIDER_BOUNDARY",
    "WAITING_PROVIDER_CONFIG",
    "WAITING_HUMAN_PAID_APPROVAL",
    "BLOCKED_COST_ESTIMATE",
    "BLOCKED_ATTEMPT_LIMIT",
    "BLOCKED_CHARACTER_INPUT",
    "BLOCKED_VOICE_INPUT",
    "BLOCKED_PROVIDER_NOT_CONFIGURED",
    "BLOCKED_DETERMINISTIC_GATE",
    "BLOCKED_PROXY_PREVIEW",
    "BLOCKED_EXECUTION_DISABLED",
    "ALLOWED_NOT_EXECUTED",
]


class RenderRevisionCreateRequest(BaseModel):
    package_id: uuid.UUID
    source_artifact_refs_json: list[dict[str, Any]] = Field(default_factory=list)
    gate_batch_refs_json: list[dict[str, Any]] = Field(default_factory=list)
    provider_plan_json: dict[str, Any] = Field(default_factory=dict)
    created_by: str = "operator"
    supersede_previous: bool = True

    model_config = ConfigDict(extra="forbid")


class CostEstimateCreateRequest(BaseModel):
    render_revision_id: uuid.UUID
    currency: str = "USD"

    model_config = ConfigDict(extra="forbid")


class HumanPaidRenderApprovalCreateRequest(BaseModel):
    render_revision_id: uuid.UUID
    max_approved_cost: Decimal | None = None
    approved_provider_stages_json: list[str] = Field(default_factory=list)
    rationale: str = "Pending paid render approval."
    expires_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")


class HumanPaidRenderApprovalDecisionRequest(BaseModel):
    approved_by: str | None = None
    rationale: str | None = None
    max_approved_cost: Decimal | None = None
    approved_provider_stages_json: list[str] | None = None
    expires_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")


class ProviderBoundaryPreflightRequest(BaseModel):
    render_revision_id: uuid.UUID
    provider_key: str
    provider_stage: str
    call_type: PaidProviderCallType = "VALIDATION_ONLY"
    request_payload_json: dict[str, Any] = Field(default_factory=dict)
    cost_estimate_snapshot_id: uuid.UUID | None = None
    human_approval_id: uuid.UUID | None = None
    idempotency_key_id: uuid.UUID | None = None
    real_call_requested: bool = False
    consume_attempt: bool = False

    model_config = ConfigDict(extra="forbid")


class ProviderIdempotencyKeyCreateRequest(BaseModel):
    render_revision_id: uuid.UUID
    provider_key: str
    provider_stage: str
    request_payload_json: dict[str, Any] = Field(default_factory=dict)
    request_fingerprint: str | None = None

    model_config = ConfigDict(extra="forbid")


class ProviderJobCreateRequest(BaseModel):
    render_revision_id: uuid.UUID
    provider_key: str
    provider_stage: str
    provider_request_json: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProxyPreviewArtifactFlagCreateRequest(BaseModel):
    artifact_ref: str
    video_project_id: uuid.UUID
    package_id: uuid.UUID
    source_type: str
    preview_only: bool = True
    not_final_media: bool = True
    not_publishable: bool = True

    model_config = ConfigDict(extra="forbid")


class ProviderBoundaryDecisionRead(BaseModel):
    render_revision_id: uuid.UUID
    provider_key: str
    provider_stage: str
    status: ProviderBoundaryStatus
    call_status: PaidProviderCallStatus
    allowed: bool
    will_execute: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    ledger_id: uuid.UUID | None = None
    cost_estimate_snapshot_id: uuid.UUID | None = None
    human_approval_id: uuid.UUID | None = None
    idempotency_key_id: uuid.UUID | None = None
    attempt_limit_record_id: uuid.UUID | None = None
    no_network_call_made: bool = True

    model_config = ConfigDict(extra="forbid")


class RenderRevisionRead(BaseModel):
    id: uuid.UUID
    video_project_id: uuid.UUID
    package_id: uuid.UUID
    effective_context_snapshot_id: uuid.UUID
    revision_no: int
    revision_status: RenderRevisionStatus
    source_artifact_refs_json: list[dict[str, Any]]
    gate_batch_refs_json: list[dict[str, Any]]
    render_plan_hash: str
    provider_plan_json: dict[str, Any]
    created_by: str
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CostEstimateSnapshotRead(BaseModel):
    id: uuid.UUID
    render_revision_id: uuid.UUID
    video_project_id: uuid.UUID
    package_id: uuid.UUID
    estimate_status: CostEstimateStatus
    currency: str
    estimated_total_cost: Decimal | None
    estimated_voice_cost: Decimal | None
    estimated_ai_hero_cost: Decimal | None
    estimated_final_render_cost: Decimal | None
    estimated_pexels_cost: Decimal
    provider_estimates_json: dict[str, Any]
    blocker_reason_codes_json: list[str]
    content_hash: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class HumanPaidRenderApprovalRead(BaseModel):
    id: uuid.UUID
    render_revision_id: uuid.UUID
    approval_status: HumanPaidRenderApprovalStatus
    approved_by: str | None
    approved_at: AwareDatetime | None
    max_approved_cost: Decimal | None
    approved_provider_stages_json: list[str]
    rationale: str
    expires_at: AwareDatetime | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ProviderIdempotencyKeyRead(BaseModel):
    id: uuid.UUID
    render_revision_id: uuid.UUID
    provider_key: str
    provider_stage: str
    idempotency_key: str
    request_fingerprint: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ProviderJobSnapshotRead(BaseModel):
    id: uuid.UUID
    render_revision_id: uuid.UUID
    provider_key: str
    provider_stage: str
    job_status: ProviderJobStatus
    external_job_id: str | None
    provider_request_hash: str | None
    provider_response_hash: str | None
    last_error_code: str | None
    last_error_message: str | None
    poll_count: int
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class PaidProviderCallLedgerRead(BaseModel):
    id: uuid.UUID
    render_revision_id: uuid.UUID
    provider_key: str
    provider_stage: str
    call_type: PaidProviderCallType
    call_status: PaidProviderCallStatus
    human_approval_id: uuid.UUID | None
    idempotency_key_id: uuid.UUID | None
    cost_estimate_snapshot_id: uuid.UUID | None
    request_fingerprint: str
    response_ref: str | None
    reason_codes_json: list[str]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class PaidAttemptLimitRecordRead(BaseModel):
    id: uuid.UUID
    render_revision_id: uuid.UUID
    provider_key: str
    provider_stage: str
    attempt_count: int
    max_attempts: int
    last_attempt_at: AwareDatetime | None
    status: PaidAttemptLimitStatus
    reason_codes_json: list[str]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ProxyPreviewArtifactFlagRead(BaseModel):
    artifact_ref: str
    video_project_id: uuid.UUID
    package_id: uuid.UUID
    preview_only: bool
    not_final_media: bool
    not_publishable: bool
    source_type: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)
