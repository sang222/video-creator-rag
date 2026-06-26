import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


PostPublishObservationWindowName = Literal["T_PLUS_1H", "T_PLUS_6H", "T_PLUS_24H", "T_PLUS_48H", "T_PLUS_7D", "CUSTOM"]
ObservationWindowState = Literal["PENDING", "READY", "COMPLETED", "SKIPPED", "BLOCKED"]
PostPublishHealthRunState = Literal["PENDING", "COMPLETED", "BLOCKED", "INSUFFICIENT_DATA", "FAILED"]
PostPublishHealthState = Literal[
    "HEALTHY",
    "WATCH",
    "NO_VIEW_RISK",
    "UNDERPERFORMING",
    "POLICY_REVIEW_REQUIRED",
    "INSUFFICIENT_DATA",
    "UNKNOWN",
]
DiagnosticSeverity = Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
DiagnosticConfidence = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
TaxonomyStatus = Literal["ACTIVE", "DISABLED", "DEPRECATED"]
NoViewDiagnosticState = Literal[
    "NOT_APPLICABLE",
    "INSUFFICIENT_DATA",
    "NO_VIEW_RISK",
    "LOW_IMPRESSIONS",
    "DATA_UNAVAILABLE",
    "HEALTHY",
    "UNKNOWN",
]
PackagingDiagnosticState = Literal["NOT_APPLICABLE", "INSUFFICIENT_DATA", "LOW_CTR", "WATCH", "HEALTHY", "UNKNOWN"]
RetentionDiagnosticState = Literal["NOT_APPLICABLE", "INSUFFICIENT_DATA", "EARLY_DROP", "MID_VIDEO_DROP", "WATCH", "HEALTHY", "UNKNOWN"]
EngagementDiagnosticState = Literal["NOT_APPLICABLE", "INSUFFICIENT_DATA", "LOW_ENGAGEMENT", "WATCH", "HEALTHY", "UNKNOWN"]
PolicyRightsDiagnosticState = Literal["NOT_APPLICABLE", "REVIEW_REQUIRED", "BLOCKED", "PASS", "UNKNOWN"]
RecoveryProposalType = Literal[
    "WAIT_AND_MONITOR",
    "REVIEW_TITLE_THUMBNAIL",
    "REVIEW_HOOK",
    "REVIEW_RETENTION_SECTION",
    "REVIEW_RIGHTS_DISCLOSURE",
    "REVIEW_SOURCE_QUALITY",
    "CREATE_FUTURE_VARIANT",
    "NO_ACTION",
]
RecoveryProposalState = Literal["PROPOSED", "ACCEPTED", "REJECTED", "SUPERSEDED", "CANCELLED"]
RecoveryRiskLevel = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]


class PostPublishHealthRunCreate(BaseModel):
    uploaded_video_id: uuid.UUID
    observation_window: PostPublishObservationWindowName = "T_PLUS_24H"

    model_config = ConfigDict(extra="forbid")


class PostPublishObservationWindowRead(BaseModel):
    id: uuid.UUID
    uploaded_video_id: uuid.UUID
    platform: str
    platform_video_id: str
    published_at: AwareDatetime
    observation_window: PostPublishObservationWindowName
    window_start_at: AwareDatetime
    window_end_at: AwareDatetime
    expected_check_at: AwareDatetime
    state: ObservationWindowState
    reason_codes: list[str]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class DiagnosticTaxonomyVersionRead(BaseModel):
    id: uuid.UUID
    taxonomy_key: str
    version: str
    taxonomy_blob: dict[str, Any]
    status: TaxonomyStatus
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class PostPublishHealthRunRead(BaseModel):
    id: uuid.UUID
    uploaded_video_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    platform: str
    platform_video_id: str
    observation_window: PostPublishObservationWindowName
    analytics_snapshot_id: uuid.UUID | None
    uploaded_video_metrics_summary_id: uuid.UUID | None
    retention_curve_snapshot_id: uuid.UUID | None
    traffic_source_snapshot_id: uuid.UUID | None
    engagement_snapshot_id: uuid.UUID | None
    run_state: PostPublishHealthRunState
    health_state: PostPublishHealthState
    severity: DiagnosticSeverity
    confidence_level: DiagnosticConfidence
    evidence_refs: list[dict[str, Any]]
    reason_codes: list[str]
    operator_summary: str
    next_action: str | None
    do_not_do: list[str]
    technical_appendix: dict[str, Any]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class NoViewDiagnosticRunRead(BaseModel):
    id: uuid.UUID
    post_publish_health_run_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    analytics_snapshot_id: uuid.UUID | None
    uploaded_video_metrics_summary_id: uuid.UUID | None
    observation_window: PostPublishObservationWindowName
    diagnostic_state: NoViewDiagnosticState
    views: float | None
    impressions: float | None
    metric_availability: dict[str, Any]
    evidence_blob: dict[str, Any]
    confidence_level: DiagnosticConfidence
    reason_codes: list[str]
    operator_summary: str
    next_action: str | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class PackagingDiagnosticRunRead(BaseModel):
    id: uuid.UUID
    post_publish_health_run_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    analytics_snapshot_id: uuid.UUID | None
    observation_window: PostPublishObservationWindowName
    diagnostic_state: PackagingDiagnosticState
    impressions: float | None
    click_through_rate: float | None
    views: float | None
    evidence_blob: dict[str, Any]
    confidence_level: DiagnosticConfidence
    reason_codes: list[str]
    operator_summary: str
    next_action: str | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class RetentionDiagnosticRunRead(BaseModel):
    id: uuid.UUID
    post_publish_health_run_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    analytics_snapshot_id: uuid.UUID | None
    retention_curve_snapshot_id: uuid.UUID | None
    observation_window: PostPublishObservationWindowName
    diagnostic_state: RetentionDiagnosticState
    average_view_duration_seconds: float | None
    average_view_percentage: float | None
    evidence_blob: dict[str, Any]
    scene_alignment: list[dict[str, Any]]
    confidence_level: DiagnosticConfidence
    reason_codes: list[str]
    operator_summary: str
    next_action: str | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class EngagementDiagnosticRunRead(BaseModel):
    id: uuid.UUID
    post_publish_health_run_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    analytics_snapshot_id: uuid.UUID | None
    engagement_snapshot_id: uuid.UUID | None
    observation_window: PostPublishObservationWindowName
    diagnostic_state: EngagementDiagnosticState
    engagement_metrics: dict[str, Any]
    evidence_blob: dict[str, Any]
    confidence_level: DiagnosticConfidence
    reason_codes: list[str]
    operator_summary: str
    next_action: str | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class PolicyRightsDiagnosticRunRead(BaseModel):
    id: uuid.UUID
    post_publish_health_run_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    observation_window: PostPublishObservationWindowName
    diagnostic_state: PolicyRightsDiagnosticState
    source_manifest_snapshot_id: uuid.UUID | None
    rights_envelope_ref: str | None
    actual_disclosures: dict[str, Any]
    evidence_blob: dict[str, Any]
    confidence_level: DiagnosticConfidence
    reason_codes: list[str]
    operator_summary: str
    next_action: str | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class FailureTraceReportRead(BaseModel):
    id: uuid.UUID
    post_publish_health_run_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    video_project_id: uuid.UUID
    platform: str
    platform_video_id: str
    observation_window: PostPublishObservationWindowName
    primary_status: PostPublishHealthState
    primary_suspected_cause: str | None
    secondary_suspected_causes: list[str]
    confidence_level: DiagnosticConfidence
    severity: DiagnosticSeverity
    evidence_plain_text: list[str]
    operator_summary: str
    operator_report: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None
    do_not_do: list[str]
    technical_appendix: dict[str, Any]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class RecoveryProposalRead(BaseModel):
    id: uuid.UUID
    failure_trace_report_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    video_project_id: uuid.UUID
    proposal_type: RecoveryProposalType
    proposal_state: RecoveryProposalState
    operator_summary: str
    recommended_actions: list[str]
    do_not_do: list[str]
    evidence_refs: list[dict[str, Any]]
    risk_level: RecoveryRiskLevel
    requires_human_approval: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")
