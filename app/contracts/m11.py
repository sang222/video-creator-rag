import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


OperatorRole = Literal[
    "OWNER_ADMIN",
    "CHANNEL_MANAGER",
    "PRODUCER",
    "REVIEWER",
    "PUBLISHER",
    "ANALYST",
    "PROCUREMENT_OPERATOR",
    "COMPLIANCE_REVIEWER",
    "LEARNING_REVIEWER",
    "READ_ONLY_OBSERVER",
]
ChannelLifecycleState = Literal["DRAFT", "READY", "ACTIVE", "PAUSED", "DEACTIVATED", "ARCHIVED"]
ChannelHealthStatus = Literal[
    "NEW",
    "OBSERVING",
    "HEALTHY",
    "LOW_VIEW",
    "NO_VIEW",
    "LOW_PROFIT",
    "RECOVERY_ACTIVE",
    "WATCHLIST",
    "NEEDS_HUMAN_REVIEW",
]
ChannelLifecycleAction = Literal[
    "KEEP_ACTIVE",
    "PAUSE_DAILY_GENERATION",
    "CONTINUE_OBSERVING",
    "ADD_MANUAL_NOTE",
    "DEACTIVATE_CHANNEL",
    "ARCHIVE_CHANNEL",
    "REACTIVATE_CHANNEL",
]
LearningReviewAction = Literal["APPROVE", "REJECT", "REQUEST_MORE_EVIDENCE", "SUPPRESS", "EXPIRE"]


class DashboardMetricCard(BaseModel):
    key: str
    label: str
    value: int | float | str | None
    state: str = "UNKNOWN"
    next_action: str | None = None

    model_config = ConfigDict(extra="forbid")


class DashboardActionCard(BaseModel):
    key: str
    title: str
    count: int
    severity: str
    next_action: str
    route: str | None = None

    model_config = ConfigDict(extra="forbid")


class DashboardWarning(BaseModel):
    key: str
    label: str
    severity: str = "INFO"
    text: str

    model_config = ConfigDict(extra="forbid")


class CommandCenterRead(BaseModel):
    generated_at: AwareDatetime
    company_id: uuid.UUID | None = None
    cards: list[DashboardActionCard]
    metrics: list[DashboardMetricCard]
    required_actions: list[dict[str, Any]]
    safety_warnings: list[DashboardWarning]
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ApprovalQueueSummary(BaseModel):
    queue_type: str
    label: str
    count: int
    priority: str = "NORMAL"
    next_action: str
    allowed_actions: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ApprovalQueueItem(BaseModel):
    queue_item_id: uuid.UUID | None = None
    queue_type: str
    entity_type: str
    entity_id: uuid.UUID | None = None
    channel: dict[str, Any] | None = None
    project: dict[str, Any] | None = None
    operator_summary: str
    friendly_status: str
    priority: str = "NORMAL"
    risk_level: str = "UNKNOWN"
    confidence_label: str = "UNKNOWN"
    freshness_label: str = "UNKNOWN"
    evidence_summary: str
    next_action: str
    due_at: AwareDatetime | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    audit_refs: list[dict[str, Any]] = Field(default_factory=list)
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class DashboardQueuesRead(BaseModel):
    generated_at: AwareDatetime
    summaries: list[ApprovalQueueSummary]
    items: list[ApprovalQueueItem]

    model_config = ConfigDict(extra="forbid")


class ChannelLifecycleRead(BaseModel):
    channel_id: uuid.UUID
    lifecycle_state: ChannelLifecycleState
    health_status: ChannelHealthStatus
    daily_generation_allowed: bool
    next_action: str
    main_blocker: str | None = None
    allowed_actions: list[ChannelLifecycleAction]
    last_decision: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class ChannelLifecycleDecisionCreate(BaseModel):
    action: ChannelLifecycleAction
    health_status: ChannelHealthStatus | None = None
    reason: str | None = None
    decided_by_user_id: uuid.UUID | None = None
    actor_role: OperatorRole = "OWNER_ADMIN"
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ChannelLifecycleDecisionRead(BaseModel):
    id: uuid.UUID
    channel_workspace_id: uuid.UUID
    company_id: uuid.UUID
    previous_lifecycle_state: ChannelLifecycleState | None
    lifecycle_state: ChannelLifecycleState
    health_status: ChannelHealthStatus
    action: ChannelLifecycleAction
    reason: str | None
    next_action: str
    decided_by_user_id: uuid.UUID | None
    decision_metadata: dict[str, Any]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class ChannelWorkspaceDashboardRead(BaseModel):
    channel: dict[str, Any]
    health_summary: dict[str, Any]
    lifecycle: ChannelLifecycleRead
    projects: list[dict[str, Any]]
    daily_runs: list[dict[str, Any]]
    approvals: list[ApprovalQueueItem]
    uploaded_videos: list[dict[str, Any]]
    publish_ledger: dict[str, Any] = Field(default_factory=dict)
    media_storage: dict[str, Any]
    provider_health: dict[str, Any]
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class UploadedVideoListItem(BaseModel):
    id: uuid.UUID
    title: str
    channel_id: uuid.UUID
    platform: str
    platform_video_id: str
    video_url: str
    external_video_id: str | None = None
    external_url: str | None = None
    actual_visibility: str | None = None
    verification_status: str = "NOT_VERIFIED"
    analytics_sync_status: str = "NOT_STARTED"
    published_at: AwareDatetime
    metrics: dict[str, Any]
    freshness: str
    owner_analytics_status: str
    latest_diagnostic: str | None = None
    next_action: str | None = None

    model_config = ConfigDict(extra="forbid")


class UploadedVideoDashboardRead(BaseModel):
    uploaded_video: dict[str, Any]
    public_stats: dict[str, Any]
    owner_analytics: dict[str, Any]
    publish_check: dict[str, Any]
    diagnostics: list[dict[str, Any]]
    recovery_proposals: list[dict[str, Any]]
    learning_candidates: list[dict[str, Any]]
    media: list[dict[str, Any]]
    safety_warnings: list[DashboardWarning]
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class LearningReviewDecisionCreate(BaseModel):
    action: LearningReviewAction
    actor_role: OperatorRole = "LEARNING_REVIEWER"
    decided_by_user_id: uuid.UUID | None = None
    rationale: str | None = None

    model_config = ConfigDict(extra="forbid")


class ApprovedPlaybookEntryRead(BaseModel):
    id: uuid.UUID
    learning_candidate_id: uuid.UUID
    learning_review_decision_id: uuid.UUID | None
    playbook_candidate_draft_id: uuid.UUID | None
    evidence_bundle_id: uuid.UUID | None
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    scope: str
    category: str
    playbook_text: str
    evidence_refs: list[dict[str, Any]]
    limitations: list[dict[str, Any]]
    counter_evidence: list[dict[str, Any]]
    policy_rights_summary: dict[str, Any]
    state: str
    approved_by_user_id: uuid.UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class LearningReviewDecisionRead(BaseModel):
    id: uuid.UUID
    learning_candidate_id: uuid.UUID
    learning_review_queue_item_id: uuid.UUID | None
    evidence_bundle_id: uuid.UUID | None
    playbook_candidate_draft_id: uuid.UUID | None
    approved_playbook_entry_id: uuid.UUID | None
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    action: LearningReviewAction
    decision_state: str
    actor_role: OperatorRole
    decided_by_user_id: uuid.UUID | None
    rationale: str | None
    reason_codes: list[str]
    evidence_refs: list[dict[str, Any]]
    technical_appendix: dict[str, Any]
    created_at: AwareDatetime
    approved_playbook_entry: ApprovedPlaybookEntryRead | None = None

    model_config = ConfigDict(extra="forbid")


class ProviderOpsDashboardRead(BaseModel):
    generated_at: AwareDatetime
    providers: list[dict[str, Any]]
    credentials: list[dict[str, Any]]
    quotas: list[dict[str, Any]]
    costs: list[dict[str, Any]]
    incidents: list[dict[str, Any]]
    manual_actions: list[dict[str, Any]]
    integrations: dict[str, Any]
    safety_warnings: list[DashboardWarning]

    model_config = ConfigDict(extra="forbid")
