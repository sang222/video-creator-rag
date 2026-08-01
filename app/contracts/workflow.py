import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.contracts.vcos_v2 import (
    AssignmentMode,
    ContentMode,
    DurationContractV2,
    PlanningSourceType,
    ProductionLane,
)

ProjectStatus = Literal["draft", "in_review", "approved", "archived"]
ArtifactStatus = Literal[
    "draft", "in_review", "approved", "revision_requested", "archived"
]
ArtifactVersionStatus = Literal[
    "draft", "submitted", "approved", "rejected", "superseded"
]
ReviewTaskStatus = Literal["open", "in_progress", "completed", "cancelled"]
RevisionRequestStatus = Literal["open", "resolved", "cancelled"]
ApprovalDecisionValue = Literal["approved", "rejected", "blocked"]
WorkflowTargetType = Literal["artifact_version", "review_task", "video_project"]
ReviewSeverity = Literal["info", "low", "medium", "high", "critical"]


class VideoProjectCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    channel_profile_version_id: uuid.UUID | None = None
    native_render_policy_snapshot_ref: str | None = None
    native_render_policy_snapshot_hash: str | None = None
    creative_quality_policy_ref: str | None = None
    creative_quality_policy_hash: str | None = None
    provider_usage_policy_ref: str | None = None
    provider_usage_policy_hash: str | None = None
    budget_policy_ref: str | None = None
    budget_policy_hash: str | None = None
    format_identity_contract_ref: str | None = None
    format_identity_contract_hash: str | None = None
    category_id: uuid.UUID | None = None
    character_binding_id: uuid.UUID | None = None
    channel_contract_content_hash: str | None = None
    effective_context_snapshot_id: uuid.UUID | None = None
    title: str = Field(min_length=1)
    description: str | None = None
    status: ProjectStatus = "draft"
    project_type: str | None = None
    schema_version: Literal["v1", "v2"] = "v1"
    planning_source_type: PlanningSourceType | None = None
    production_lane: ProductionLane | None = None
    content_mode: ContentMode | None = None
    assignment_mode: AssignmentMode | None = None
    series_plan_id: uuid.UUID | None = None
    series_run_id: uuid.UUID | None = None
    episode_number: int | None = Field(default=None, gt=0)
    episode_role: str | None = None
    standalone_reason_code: str | None = None
    project_admission_decision_id: uuid.UUID | None = None
    duration_contract: DurationContractV2 | None = None
    render_eligible: bool = True
    priority: str | None = None
    owner_user_id: uuid.UUID | None = None
    created_by_user_id: uuid.UUID
    financial_summary: dict[str, Any] = Field(default_factory=dict)
    brand_safety_summary: dict[str, Any] = Field(default_factory=dict)
    legal_compliance_summary: dict[str, Any] = Field(default_factory=dict)
    audience_delivery_summary: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_v2_assignment(self) -> "VideoProjectCreate":
        if self.schema_version == "v1":
            return self
        if (
            self.planning_source_type is None
            or self.production_lane is None
            or self.content_mode is None
            or self.assignment_mode is None
            or self.duration_contract is None
            or self.project_admission_decision_id is None
        ):
            raise ValueError(
                "v2 project requires planning source, lane, content mode, "
                "assignment mode, duration contract, and admission decision"
            )
        if (
            self.duration_contract.source_profile_version_id
            != self.channel_profile_version_id
            or self.duration_contract.source_policy_snapshot_id
            != self.policy_snapshot_id
        ):
            raise ValueError("duration contract must bind the project profile/policy")
        series_values = (
            self.series_plan_id,
            self.series_run_id,
            self.episode_number,
        )
        if self.content_mode == ContentMode.SERIES_EPISODE:
            if any(value is None for value in series_values):
                raise ValueError(
                    "series episode requires plan, run, and episode number"
                )
            if self.standalone_reason_code is not None:
                raise ValueError("series episode cannot have standalone_reason_code")
        else:
            if any(value is not None for value in series_values):
                raise ValueError("standalone project cannot carry series fields")
            if not self.standalone_reason_code:
                raise ValueError("standalone project requires standalone_reason_code")
        return self

    @property
    def standalone_reason(self) -> str | None:
        """Read-only compatibility view for pre-v2 callers."""
        return self.standalone_reason_code


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
    # Nullable for historical/non-market approvals. Strict market publication
    # requires all of these fields and validates them fail-closed.
    policy_snapshot_id: uuid.UUID | None = None
    destination_binding_id: uuid.UUID | None = None
    destination_binding_fingerprint: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    market_policy_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    approved_package_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    target_market_profile_ref: str | None = None
    target_market_profile_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    market_alignment_dossier_ref: str | None = None
    market_alignment_dossier_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    approved_publish_window: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class ApprovalDecisionRead(ApprovalDecisionCreate):
    id: uuid.UUID
    decided_at: AwareDatetime
    created_at: AwareDatetime


class RevisionResolveRequest(BaseModel):
    resolved_by_artifact_version_id: uuid.UUID

    model_config = ConfigDict(extra="forbid")
