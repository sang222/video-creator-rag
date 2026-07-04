import uuid
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class OperatorNextActionRead(BaseModel):
    next_action_code: str
    next_action_label_vi: str
    allowed_actor_role: str = "OPERATOR"
    blocking_reason_codes: list[str] = Field(default_factory=list)
    target_url: str | None = None
    action_ref: dict[str, Any] | None = None
    is_manual_only: bool = True

    model_config = ConfigDict(extra="forbid")


class OpsCardRead(BaseModel):
    key: str
    entity_type: str
    entity_id: uuid.UUID | str | None = None
    title: str
    status: str
    severity: str = "INFO"
    blocker_reason_codes: list[str] = Field(default_factory=list)
    next_action: OperatorNextActionRead
    owner_role: str | None = "OPERATOR"
    link_target: str | None = None
    updated_at: AwareDatetime | None = None
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class RuntimeDashboardRead(BaseModel):
    generated_at: AwareDatetime
    active_channels: list[OpsCardRead] = Field(default_factory=list)
    packages_waiting_review: list[OpsCardRead] = Field(default_factory=list)
    upload_tasks_waiting_human: list[OpsCardRead] = Field(default_factory=list)
    uploaded_videos_waiting_verification_or_analytics: list[OpsCardRead] = Field(default_factory=list)
    diagnostics_needing_review: list[OpsCardRead] = Field(default_factory=list)
    recovery_proposals_needing_action: list[OpsCardRead] = Field(default_factory=list)
    learning_candidates_needing_review: list[OpsCardRead] = Field(default_factory=list)
    memory_approvals_needing_review: list[OpsCardRead] = Field(default_factory=list)
    provider_cost_blockers: list[OpsCardRead] = Field(default_factory=list)
    gate_failures: list[OpsCardRead] = Field(default_factory=list)
    next_actions: list[OperatorNextActionRead] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ChannelRuntimeTraceRead(BaseModel):
    channel_id: uuid.UUID
    video_project_id: uuid.UUID | None = None
    package_id: uuid.UUID | None = None
    channel_profile_version_id: uuid.UUID | None = None
    compiled_policy_snapshot_id: uuid.UUID | None = None
    channel_contract_hash: str | None = None
    effective_context_snapshot_id: uuid.UUID
    context_hash: str
    category_id: uuid.UUID | None = None
    character_binding_id: uuid.UUID | None = None
    market_locale_language: dict[str, Any] = Field(default_factory=dict)
    voice_profile: dict[str, Any] = Field(default_factory=dict)
    thumbnail_style: dict[str, Any] = Field(default_factory=dict)
    publish_timing_policy: dict[str, Any] = Field(default_factory=dict)
    provider_boundary: dict[str, Any] = Field(default_factory=dict)
    budget_cost_policy: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    snapshot_refs: dict[str, Any] = Field(default_factory=dict)
    latest_mutable_settings_used: bool = False
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class PackageOpsSummaryRead(BaseModel):
    package_id: uuid.UUID
    package_status: str
    video_project_id: uuid.UUID | None = None
    channel_id: uuid.UUID
    effective_context_snapshot_id: uuid.UUID | None = None
    effective_context_hash: str | None = None
    agent_context_pack_refs: list[dict[str, Any]] = Field(default_factory=list)
    prompt_budget_summary: list[dict[str, Any]] = Field(default_factory=list)
    hook_first_3_seconds: dict[str, Any] = Field(default_factory=dict)
    title_description_subtitles_disclosure: dict[str, Any] = Field(default_factory=dict)
    thumbnail_handoff: dict[str, Any] = Field(default_factory=dict)
    publish_timing_recommendation: dict[str, Any] = Field(default_factory=dict)
    r3d4_deterministic_gate_results: list[dict[str, Any]] = Field(default_factory=list)
    gatekeeper_soft_review_result: dict[str, Any] | None = None
    packaging_gate_results: list[dict[str, Any]] = Field(default_factory=list)
    provider_boundary_summary: dict[str, Any] = Field(default_factory=dict)
    manual_publish_handoff: dict[str, Any] = Field(default_factory=dict)
    next_action: OperatorNextActionRead
    no_provider_media_upload_execution: bool = True
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class PackagingProposedPatchRead(BaseModel):
    id: uuid.UUID
    queue_item_id: uuid.UUID
    package_id: uuid.UUID
    proposal_source: str
    routed_agent_key: str | None = None
    patch_type: str
    before_snapshot_ref: str
    proposed_patch_json: dict[str, Any] = Field(default_factory=dict)
    after_preview_json: dict[str, Any] = Field(default_factory=dict)
    affected_artifact_refs_json: list[dict[str, Any]] = Field(default_factory=list)
    risk_level: str
    requires_human_approval: bool = True
    patch_hash: str
    status: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class PackagingReviewQueueItemRead(BaseModel):
    id: uuid.UUID
    package_id: uuid.UUID
    video_project_id: uuid.UUID | None = None
    effective_context_snapshot_id: uuid.UUID | None = None
    gate_key: str
    issue_code: str
    severity: str
    target_artifact_type: str
    target_artifact_ref: str | None = None
    source_gate_run_id: uuid.UUID | None = None
    source_gate_batch_id: uuid.UUID | None = None
    status: str
    next_action_code: str
    human_readable_title: str
    human_readable_why: str
    human_readable_fix: str
    section: str
    proposed_patch: PackagingProposedPatchRead | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class PackagingReviewQueueRead(BaseModel):
    package_id: uuid.UUID
    review_verdict: str
    plain_language_status: str
    must_fix_count: int
    next_safe_action: str
    upload_task_creation_allowed: bool
    items: list[PackagingReviewQueueItemRead] = Field(default_factory=list)
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class PackagingPatchApprovalDecisionRead(BaseModel):
    id: uuid.UUID
    proposed_patch_id: uuid.UUID
    decision: str
    decided_by: str
    rationale: str | None = None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class PackagingPatchDecisionRequest(BaseModel):
    decided_by: str = "operator"
    rationale: str | None = None

    model_config = ConfigDict(extra="forbid")


class PackagingPatchApplyRunRead(BaseModel):
    id: uuid.UUID
    proposed_patch_id: uuid.UUID
    package_id: uuid.UUID
    apply_status: str
    created_artifact_ref: str | None = None
    created_handoff_override_ref: str | None = None
    created_version_hash: str | None = None
    reason_codes_json: list[str] = Field(default_factory=list)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class PackagingGateRerunRecordRead(BaseModel):
    id: uuid.UUID
    package_id: uuid.UUID
    proposed_patch_id: uuid.UUID | None = None
    gate_keys_json: list[str] = Field(default_factory=list)
    rerun_status: str
    gate_batch_run_id: uuid.UUID | None = None
    reason_codes_json: list[str] = Field(default_factory=list)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class UploadedVideoOpsSummaryRead(BaseModel):
    uploaded_video_id: uuid.UUID
    platform: str
    platform_video_id: str
    platform_url: str
    backfill_history: list[dict[str, Any]] = Field(default_factory=list)
    verification_status: str
    actual_upload_time: AwareDatetime | None = None
    actual_publish_time: AwareDatetime | None = None
    channel_timezone: str | None = None
    operator_timezone: str | None = None
    analytics_sync_status: str
    analytics_maturity: str
    analytics_confidence: str
    enforcement_restriction_flags: list[str] = Field(default_factory=list)
    linked_package_project: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    recovery_proposal_refs: list[dict[str, Any]] = Field(default_factory=list)
    learning_candidate_refs: list[dict[str, Any]] = Field(default_factory=list)
    next_action: OperatorNextActionRead
    no_youtube_studio_scraping: bool = True
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class DiagnosticOpsQueueRead(BaseModel):
    generated_at: AwareDatetime
    items: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class RecoveryOpsQueueRead(BaseModel):
    generated_at: AwareDatetime
    items: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class LearningOpsQueueRead(BaseModel):
    generated_at: AwareDatetime
    items: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class MemoryOpsQueueRead(BaseModel):
    generated_at: AwareDatetime
    items: list[dict[str, Any]] = Field(default_factory=list)
    prompt_eligibility_rule_vi: str = "Chỉ memory APPROVED + SAFE + PROMPT_SAFE + FRESH mới có thể đủ điều kiện vào prompt tương lai."

    model_config = ConfigDict(extra="forbid")


class RetrievalManifestOpsRead(BaseModel):
    manifest_id: uuid.UUID
    effective_context_snapshot_id: uuid.UUID
    agent_key: str
    use_case: str
    sql_filter: dict[str, Any]
    candidate_count_before_vector: int
    candidate_count_after_policy: int
    selected_facets: list[dict[str, Any]] = Field(default_factory=list)
    blocked_refs: list[dict[str, Any]] = Field(default_factory=list)
    rejected_refs: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_hash: str
    digest_hash: str | None = None
    raw_memory_hidden: bool = True
    advanced_refs_collapsed_by_default: bool = True
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class MemoryInfluenceOpsRead(BaseModel):
    manifest_id: uuid.UUID
    video_project_id: uuid.UUID
    package_id: uuid.UUID | None = None
    agent_key: str
    retrieval_manifest_id: uuid.UUID
    memory_facets_used: list[dict[str, Any]] = Field(default_factory=list)
    digest_hash: str
    prompt_context_hash: str
    applied_as: dict[str, Any] = Field(default_factory=dict)
    ignored_memory_refs: list[dict[str, Any]] = Field(default_factory=list)
    blocked_memory_refs: list[dict[str, Any]] = Field(default_factory=list)
    scope_status: str
    next_action: OperatorNextActionRead
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class QualityDeltaOpsRead(BaseModel):
    quality_delta_id: uuid.UUID
    memory_facets_used: list[dict[str, Any]] = Field(default_factory=list)
    expected_metric_family: str
    expected_direction: str
    baseline_snapshot: dict[str, Any] | None = None
    observed_snapshot: dict[str, Any] | None = None
    result: str
    confidence_delta: int
    reason_codes: list[str] = Field(default_factory=list)
    next_action: OperatorNextActionRead
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProviderCostOpsRead(BaseModel):
    package_id: uuid.UUID
    provider_readiness: dict[str, Any] = Field(default_factory=dict)
    missing_config: list[str] = Field(default_factory=list)
    render_revisions: list[dict[str, Any]] = Field(default_factory=list)
    cost_estimates: list[dict[str, Any]] = Field(default_factory=list)
    human_paid_render_approvals: list[dict[str, Any]] = Field(default_factory=list)
    paid_attempt_limits: list[dict[str, Any]] = Field(default_factory=list)
    provider_boundary_decisions: list[dict[str, Any]] = Field(default_factory=list)
    paid_provider_call_ledger: list[dict[str, Any]] = Field(default_factory=list)
    proxy_preview_flags: list[dict[str, Any]] = Field(default_factory=list)
    will_execute: bool = False
    next_action: OperatorNextActionRead
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
