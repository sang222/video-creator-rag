import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


LearningRunMode = Literal["MOCK", "RULE_BASED", "MANUAL_TRIGGER", "REAL_DISABLED"]
LearningRunState = Literal["PENDING", "RUNNING", "COMPLETED", "BLOCKED", "FAILED", "CANCELLED"]
LearningCandidateType = Literal[
    "TOPIC_DEMAND_PATTERN",
    "PACKAGING_PATTERN",
    "HOOK_PATTERN",
    "RETENTION_PATTERN",
    "VISUAL_SOURCE_PATTERN",
    "VOICE_NARRATION_PATTERN",
    "POLICY_RIGHTS_PATTERN",
    "COST_EFFICIENCY_PATTERN",
    "RECOVERY_PATTERN",
    "CHANNEL_FIT_PATTERN",
    "OTHER",
]
LearningCandidateState = Literal[
    "GENERATED",
    "NEEDS_MORE_EVIDENCE",
    "INELIGIBLE_LOW_EVIDENCE",
    "BLOCKED_POLICY_RISK",
    "BLOCKED_RIGHTS_RISK",
    "READY_FOR_HUMAN_REVIEW",
    "EXPIRED",
    "CANCELLED",
]
LearningRecommendedScope = Literal["CHANNEL", "SERIES", "COMPANY_DEBRANDED", "DO_NOT_PROMOTE", "UNKNOWN"]
LearningConfidenceLabel = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
LearningRiskLevel = Literal["LOW", "MEDIUM", "HIGH", "BLOCKED", "UNKNOWN"]
LearningEligibilityResult = Literal["ELIGIBLE_FOR_REVIEW", "NEEDS_MORE_EVIDENCE", "BLOCKED", "INELIGIBLE"]
LearningReviewQueueState = Literal["READY_FOR_HUMAN_REVIEW", "NEEDS_MORE_EVIDENCE", "BLOCKED", "EXPIRED", "CANCELLED"]
LearningReviewPriority = Literal["LOW", "NORMAL", "HIGH", "CRITICAL"]
LearningReviewFutureAction = Literal["APPROVE", "REJECT", "REQUEST_MORE_EVIDENCE", "SUPPRESS", "EXPIRE"]
PlaybookCandidateScope = Literal["CHANNEL", "SERIES", "COMPANY_DEBRANDED", "UNKNOWN"]
PlaybookCandidateCategory = Literal[
    "TOPIC",
    "PACKAGING",
    "HOOK",
    "RETENTION",
    "VISUAL_SOURCE",
    "VOICE",
    "POLICY",
    "COST",
    "RECOVERY",
    "OTHER",
]
PlaybookCandidateState = Literal["DRAFT", "READY_FOR_REVIEW", "BLOCKED", "EXPIRED"]


class LearningCandidateGenerationRunCreate(BaseModel):
    uploaded_video_id: uuid.UUID
    source_failure_trace_report_id: uuid.UUID | None = None
    source_recovery_proposal_id: uuid.UUID | None = None
    source_analytics_snapshot_id: uuid.UUID | None = None
    source_uploaded_video_metrics_summary_id: uuid.UUID | None = None
    run_mode: LearningRunMode = "RULE_BASED"
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class LearningCandidateGenerationRunExecuteRequest(BaseModel):
    correlation_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class LearningCandidateGenerationRunRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    video_project_id: uuid.UUID | None
    uploaded_video_id: uuid.UUID | None
    source_failure_trace_report_id: uuid.UUID | None
    source_recovery_proposal_id: uuid.UUID | None
    source_analytics_snapshot_id: uuid.UUID | None
    source_uploaded_video_metrics_summary_id: uuid.UUID | None
    run_mode: LearningRunMode
    run_state: LearningRunState
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    generated_candidate_count: int
    reason_codes: list[str]
    next_action: str | None
    metadata: dict[str, Any]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class LearningCandidateRead(BaseModel):
    id: uuid.UUID
    generation_run_id: uuid.UUID | None
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    video_project_id: uuid.UUID | None
    uploaded_video_id: uuid.UUID | None
    candidate_type: LearningCandidateType
    candidate_state: LearningCandidateState
    operator_summary: str
    friendly_status: str
    candidate_summary: str
    suggested_learning: str
    suggested_playbook_text: str | None
    recommended_scope: LearningRecommendedScope
    confidence_label: LearningConfidenceLabel
    risk_level: LearningRiskLevel
    evidence_bundle_id: uuid.UUID | None
    eligibility_run_id: uuid.UUID | None
    source_refs: list[dict[str, Any]]
    diagnostic_refs: list[dict[str, Any]]
    recovery_refs: list[dict[str, Any]]
    metric_refs: list[dict[str, Any]]
    policy_flags: list[dict[str, Any]]
    rights_flags: list[dict[str, Any]]
    limitations: list[dict[str, Any]]
    counter_evidence: list[dict[str, Any]]
    technical_appendix: dict[str, Any]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class LearningEvidenceBundleRead(BaseModel):
    id: uuid.UUID
    learning_candidate_id: uuid.UUID | None
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    evidence_summary: str
    source_video_refs: list[dict[str, Any]]
    source_project_refs: list[dict[str, Any]]
    analytics_snapshot_refs: list[dict[str, Any]]
    diagnostic_refs: list[dict[str, Any]]
    recovery_refs: list[dict[str, Any]]
    metric_support: list[dict[str, Any]]
    counter_evidence: list[dict[str, Any]]
    limitations: list[dict[str, Any]]
    freshness_summary: dict[str, Any]
    confidence_summary: dict[str, Any]
    policy_rights_summary: dict[str, Any]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class LearningPromotionEligibilityRunRead(BaseModel):
    id: uuid.UUID
    learning_candidate_id: uuid.UUID
    evidence_bundle_id: uuid.UUID | None
    result: LearningEligibilityResult
    min_evidence_met: bool
    metric_freshness_ok: bool
    policy_flags_ok: bool
    rights_flags_ok: bool
    confidence_label: LearningConfidenceLabel
    risk_level: LearningRiskLevel
    blockers: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    reason_codes: list[str]
    operator_summary: str
    next_action: str | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class LearningReviewQueueItemRead(BaseModel):
    id: uuid.UUID
    learning_candidate_id: uuid.UUID
    evidence_bundle_id: uuid.UUID | None
    eligibility_run_id: uuid.UUID | None
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    video_project_id: uuid.UUID | None
    uploaded_video_id: uuid.UUID | None
    queue_state: LearningReviewQueueState
    priority: LearningReviewPriority
    operator_summary: str
    friendly_status: str
    evidence_summary: str
    recommended_scope: LearningRecommendedScope
    confidence_label: LearningConfidenceLabel
    risk_level: LearningRiskLevel
    next_action: str
    approval_actions_allowed: list[LearningReviewFutureAction]
    source_refs: list[dict[str, Any]]
    audit_refs: list[dict[str, Any]]
    technical_appendix: dict[str, Any]
    due_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class PlaybookCandidateDraftRead(BaseModel):
    id: uuid.UUID
    learning_candidate_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    candidate_scope: PlaybookCandidateScope
    playbook_category: PlaybookCandidateCategory
    draft_text: str
    rationale: str | None
    evidence_refs: list[dict[str, Any]]
    risk_notes: list[dict[str, Any]]
    state: PlaybookCandidateState
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")
