import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


ScopeStatus = Literal["PASS", "EMPTY_SAFE_DIGEST", "BLOCK"]
QualityConfidenceResult = Literal["IMPROVED", "DEGRADED", "INCONCLUSIVE", "TOO_EARLY", "BLOCKED_BY_DATA_QUALITY"]
PromotionRunStatus = Literal["CREATED", "REVIEW_REQUIRED", "BLOCKED", "COMPLETED"]
MemoryApplicationMode = Literal["GUIDANCE", "AVOID_PATTERN", "STYLE_ANCHOR", "PACKAGING_HINT", "VISUAL_HINT", "METADATA_HINT"]


class LearningToMemoryPromotionRequest(BaseModel):
    approved_playbook_entry_id: uuid.UUID | None = None
    learning_candidate_id: uuid.UUID | None = None
    evidence_bundle_id: uuid.UUID | None = None
    failure_trace_report_id: uuid.UUID | None = None
    recovery_proposal_id: uuid.UUID | None = None
    uploaded_video_metrics_summary_id: uuid.UUID | None = None
    source_uploaded_video_id: uuid.UUID | None = None
    content_category_id: uuid.UUID | None = None
    reuse_scope: str = "CHANNEL"
    memory_type: str = "APPROVED_PLAYBOOK"
    facet_type: str | None = None
    allowed_use_cases_json: list[str] = Field(default_factory=list)
    embedding_eligible: bool = True
    prompt_safe: bool = True
    rights_safe: bool = True
    human_approval_ref: str | None = None

    model_config = ConfigDict(extra="forbid")


class QualityDeltaAttributionRunRequest(BaseModel):
    source_memory_influence_manifest_id: uuid.UUID
    target_uploaded_video_id: uuid.UUID | None = None
    target_video_project_id: uuid.UUID | None = None
    effective_context_snapshot_id: uuid.UUID | None = None
    expected_metric_family: str
    expected_improvement_direction: Literal["HIGHER", "LOWER"] = "HIGHER"
    baseline_snapshot_ref: dict[str, Any] | None = None
    observed_snapshot_ref: dict[str, Any] | None = None
    attribution_window: str = "T_PLUS_24H"
    notes: str | None = None

    model_config = ConfigDict(extra="forbid")


class ClosedLearningLoopStatusRead(BaseModel):
    status: str
    uploaded_video_id: uuid.UUID | None = None
    target_video_project_id: uuid.UUID | None = None
    steps: list[dict[str, Any]]
    reason_codes: list[str] = Field(default_factory=list)
    next_action: str | None = None

    model_config = ConfigDict(extra="forbid")


class MemoryInfluenceManifestRead(BaseModel):
    id: uuid.UUID
    video_project_id: uuid.UUID
    package_id: uuid.UUID | None
    effective_context_snapshot_id: uuid.UUID
    agent_key: str
    retrieval_manifest_id: uuid.UUID
    memory_facet_ids_used_json: list[str]
    memory_item_ids_used_json: list[str]
    digest_hash: str
    prompt_render_run_id: uuid.UUID | None
    prompt_context_hash: str
    applied_as_json: dict[str, Any]
    ignored_memory_refs_json: list[dict[str, Any]]
    blocked_memory_refs_json: list[dict[str, Any]]
    scope_status: ScopeStatus
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class QualityDeltaAttributionRead(BaseModel):
    id: uuid.UUID
    source_memory_influence_manifest_id: uuid.UUID
    source_video_project_id: uuid.UUID
    target_uploaded_video_id: uuid.UUID | None
    target_video_project_id: uuid.UUID
    effective_context_snapshot_id: uuid.UUID
    market_context_hash: str | None
    category_id: uuid.UUID | None
    character_binding_id: uuid.UUID | None
    expected_metric_family: str
    expected_improvement_direction: str
    baseline_snapshot_ref: dict[str, Any] | None
    observed_snapshot_ref: dict[str, Any] | None
    attribution_window: str
    confidence_result: QualityConfidenceResult
    confidence_delta: int
    reason_codes_json: list[str]
    notes: str | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class LearningToMemoryPromotionRunRead(BaseModel):
    id: uuid.UUID
    learning_candidate_id: uuid.UUID | None
    approved_playbook_entry_id: uuid.UUID | None
    evidence_bundle_id: uuid.UUID | None
    source_uploaded_video_id: uuid.UUID | None
    created_memory_item_ids_json: list[str]
    created_memory_facet_ids_json: list[str]
    run_status: PromotionRunStatus
    reason_codes_json: list[str]
    human_approval_ref: str | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class AgentMemoryApplicationRecordRead(BaseModel):
    id: uuid.UUID
    video_project_id: uuid.UUID
    package_id: uuid.UUID | None
    agent_key: str
    memory_influence_manifest_id: uuid.UUID
    memory_digest_hash: str
    application_mode: MemoryApplicationMode
    applied_context_refs_json: list[dict[str, Any]]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class MemoryConfidenceUpdateLedgerRead(BaseModel):
    id: uuid.UUID
    memory_facet_id: uuid.UUID
    quality_delta_attribution_id: uuid.UUID | None
    old_confidence_label: str
    new_confidence_label: str
    confidence_delta: int
    reason_codes_json: list[str]
    requires_human_review: bool
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)
