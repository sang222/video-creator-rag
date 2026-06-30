import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

ProjectStatus = Literal["draft", "in_review", "approved", "archived"]
ArtifactStatus = Literal["draft", "in_review", "approved", "revision_requested", "archived"]
ArtifactVersionStatus = Literal["draft", "submitted", "approved", "rejected", "superseded"]
ReviewTaskStatus = Literal["open", "in_progress", "completed", "cancelled"]
RevisionRequestStatus = Literal["open", "resolved", "cancelled"]
ApprovalDecisionValue = Literal["approved", "rejected", "blocked"]
WorkflowTargetType = Literal["artifact_version", "review_task", "video_project"]
ReviewSeverity = Literal["info", "low", "medium", "high", "critical"]


class VideoProjectCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    category_id: uuid.UUID | None = None
    character_binding_id: uuid.UUID | None = None
    channel_contract_content_hash: str | None = None
    effective_context_snapshot_id: uuid.UUID | None = None
    title: str = Field(min_length=1)
    description: str | None = None
    status: ProjectStatus = "draft"
    project_type: str | None = None
    priority: str | None = None
    owner_user_id: uuid.UUID | None = None
    created_by_user_id: uuid.UUID
    financial_summary: dict[str, Any] = Field(default_factory=dict)
    brand_safety_summary: dict[str, Any] = Field(default_factory=dict)
    legal_compliance_summary: dict[str, Any] = Field(default_factory=dict)
    audience_delivery_summary: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class VideoProjectRead(VideoProjectCreate):
    id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ArtifactCreate(BaseModel):
    video_project_id: uuid.UUID
    artifact_type: str = Field(min_length=1)
    status: ArtifactStatus = "draft"
    created_by_user_id: uuid.UUID

    model_config = ConfigDict(extra="forbid")


class ArtifactRead(ArtifactCreate):
    id: uuid.UUID
    current_version_id: uuid.UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ArtifactVersionCreate(BaseModel):
    artifact_id: uuid.UUID
    parent_version_id: uuid.UUID | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    status: ArtifactVersionStatus = "draft"
    created_by_user_id: uuid.UUID
    external_entity_refs: list[dict[str, Any]] = Field(default_factory=list)
    packaging_metadata: dict[str, Any] = Field(default_factory=dict)
    media_qc_metadata: dict[str, Any] = Field(default_factory=dict)
    source_manifest: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    context_refs: list[dict[str, Any]] = Field(default_factory=list)
    claim_refs: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_plan_ref: str | None = None

    model_config = ConfigDict(extra="forbid")


class ArtifactVersionRead(ArtifactVersionCreate):
    id: uuid.UUID
    version_number: int
    content_hash: str
    created_at: AwareDatetime


class ReviewTaskCreate(BaseModel):
    video_project_id: uuid.UUID
    target_type: WorkflowTargetType
    target_id: uuid.UUID
    target_artifact_version_id: uuid.UUID | None = None
    review_type: str = Field(min_length=1)
    status: ReviewTaskStatus = "open"
    assigned_to_user_id: uuid.UUID | None = None
    requested_by_user_id: uuid.UUID
    due_at: AwareDatetime | None = None
    review_reason_codes: list[str] = Field(default_factory=list)
    evidence_required: bool = False
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    review_scope: str | None = None
    context_pack_ref: str | None = None

    model_config = ConfigDict(extra="forbid")


class ReviewTaskRead(ReviewTaskCreate):
    id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ReviewFindingCreate(BaseModel):
    review_task_id: uuid.UUID
    severity: ReviewSeverity
    reason_code: str = Field(min_length=1)
    finding_text: str = Field(min_length=1)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_by_user_id: uuid.UUID

    model_config = ConfigDict(extra="forbid")


class ReviewFindingRead(ReviewFindingCreate):
    id: uuid.UUID
    created_at: AwareDatetime


class RevisionRequestCreate(BaseModel):
    review_task_id: uuid.UUID
    target_artifact_version_id: uuid.UUID
    requested_by_user_id: uuid.UUID
    reason: str = Field(min_length=1)
    status: RevisionRequestStatus = "open"

    model_config = ConfigDict(extra="forbid")


class RevisionRequestRead(RevisionRequestCreate):
    id: uuid.UUID
    resolved_by_artifact_version_id: uuid.UUID | None
    created_at: AwareDatetime
    resolved_at: AwareDatetime | None


class ApprovalDecisionCreate(BaseModel):
    target_type: WorkflowTargetType
    target_id: uuid.UUID
    target_artifact_version_id: uuid.UUID | None = None
    decision: ApprovalDecisionValue
    decided_by_user_id: uuid.UUID
    rationale: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    decision_basis: dict[str, Any] = Field(default_factory=dict)
    evidence_basis: dict[str, Any] = Field(default_factory=dict)
    policy_basis: dict[str, Any] = Field(default_factory=dict)
    context_pack_ref: str | None = None
    human_decision_note: str | None = None

    model_config = ConfigDict(extra="forbid")


class ApprovalDecisionRead(ApprovalDecisionCreate):
    id: uuid.UUID
    decided_at: AwareDatetime
    created_at: AwareDatetime


class RevisionResolveRequest(BaseModel):
    resolved_by_artifact_version_id: uuid.UUID

    model_config = ConfigDict(extra="forbid")
