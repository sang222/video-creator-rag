from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.enums import AuthorityDecisionType, ProjectState, ReviewActionType, ReviewTaskType


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CompanyCreate(BaseModel):
    name: str
    default_language: str = "en"
    config_json: dict[str, Any] = Field(default_factory=dict)


class CompanyOut(OrmModel):
    id: str
    name: str
    status: str
    default_language: str
    config_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class WorkspaceProfileInput(BaseModel):
    brand_voice: str | None = "clear, practical, not hype"
    target_audience: str | None = None
    forbidden_topics: list[str] = Field(default_factory=list)
    preferred_formats: list[str] = Field(default_factory=list)
    target_market: list[str] = Field(default_factory=list)
    monetization_thesis_json: dict[str, Any] = Field(default_factory=dict)
    platform_rules_json: dict[str, Any] = Field(default_factory=dict)
    human_review_required: bool = True
    default_workflow_mode: str = "MONETIZATION_VALIDATION_MODE"
    config_json: dict[str, Any] = Field(default_factory=dict)


class WorkspaceCreate(BaseModel):
    company_id: str
    workspace_name: str
    platform: str
    channel_name: str
    platform_channel_id: str | None = None
    channel_url: str | None = None
    niche: str | None = None
    language: str = "en"
    target_market: list[str] = Field(default_factory=list)
    follower_count: int = 0
    published_video_count: int = 0
    monetization_status: str = "NOT_STARTED"
    baseline_confidence: float = 0.0
    profile: WorkspaceProfileInput | None = None


class WorkspaceOut(OrmModel):
    id: str
    company_id: str
    workspace_name: str
    platform: str
    platform_channel_id: str | None
    channel_name: str
    channel_url: str | None
    niche: str | None
    language: str
    target_market: list[str]
    status: str
    follower_count: int
    published_video_count: int
    monetization_status: str
    baseline_confidence: float
    maturity_stage: str
    created_at: datetime
    updated_at: datetime


class WorkspaceProfileOut(OrmModel):
    workspace_id: str
    company_id: str
    brand_voice: str | None
    target_audience: str | None
    forbidden_topics: list[str]
    preferred_formats: list[str]
    target_market: list[str]
    monetization_thesis_json: dict[str, Any]
    platform_rules_json: dict[str, Any]
    human_review_required: bool
    default_workflow_mode: str
    config_json: dict[str, Any]


class WorkspaceContextOut(BaseModel):
    company_id: str
    workspace_id: str
    platform: str
    channel_name: str
    niche: str | None
    language: str
    target_market: list[str]
    maturity_stage: str
    follower_count: int
    published_video_count: int
    baseline_confidence: float
    brand_voice: str | None
    risk_tolerance: str
    quality_bar: str
    default_workflow_mode: str
    playbook_version: str
    budget: dict[str, float]


class MaturityResult(BaseModel):
    workspace_id: str
    maturity_stage: str
    reason: dict[str, Any]


class ConstitutionOut(OrmModel):
    id: str
    company_id: str
    workspace_id: str
    version: str
    content: str
    source_versions: dict[str, Any]
    token_estimate: int
    created_at: datetime
    active: bool


class WorkflowModeResult(BaseModel):
    selected_mode: str
    reason: list[str] = Field(default_factory=list)
    budget: dict[str, float]
    media_policy: dict[str, Any]
    requires_human_preapproval: bool = False
    alternative_modes: list[str] = Field(default_factory=list)


class ProjectStart(BaseModel):
    company_id: str
    workspace_id: str
    title: str = "Mock Phase 1 Video"
    topic: str | None = "AI creator tools with monetization-safe buyer intent"


class ProjectOut(OrmModel):
    id: str
    company_id: str
    workspace_id: str
    title: str
    topic: str | None
    workflow_mode: str | None
    current_state: str
    status: str
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProjectStateOut(BaseModel):
    project_id: str
    current_state: str
    history: list[dict[str, Any]]


class UploadedVideoCreate(BaseModel):
    project_id: str | None = None
    platform: str
    platform_video_id: str | None = None
    video_url: str | None = None
    title: str
    description: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    thumbnail_uri: str | None = None
    publish_time: datetime | None = None
    duration_seconds: int | None = None
    visibility: str = "PUBLIC"
    monetization_status: str = "UNKNOWN"
    upload_status: str = "IMPORTED"
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class UploadedVideoOut(OrmModel):
    id: str
    company_id: str
    workspace_id: str
    project_id: str | None
    platform: str
    platform_video_id: str | None
    video_url: str | None
    title: str
    description: str | None
    hashtags: list[str]
    thumbnail_uri: str | None
    publish_time: datetime | None
    duration_seconds: int | None
    visibility: str
    monetization_status: str
    upload_status: str
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MarkPublishedRequest(BaseModel):
    platform_video_id: str
    video_url: str
    title: str | None = None
    publish_time: datetime | None = None


class AnalyticsSnapshotCreate(BaseModel):
    uploaded_video_id: str | None = None
    snapshot_time: datetime | None = None
    hours_since_publish: int | None = None
    views: int = 0
    impressions: int = 0
    ctr: float | None = None
    avg_view_duration: float | None = None
    avg_percentage_viewed: float | None = None
    subscribers_gained: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    estimated_revenue: float = 0.0
    rpm: float | None = None
    traffic_source_json: dict[str, Any] = Field(default_factory=dict)
    geography_json: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class AnalyticsSnapshotOut(OrmModel):
    id: str
    company_id: str
    workspace_id: str
    uploaded_video_id: str | None
    project_id: str | None
    snapshot_time: datetime
    hours_since_publish: int | None
    views: int
    impressions: int
    ctr: float | None
    avg_view_duration: float | None
    avg_percentage_viewed: float | None
    subscribers_gained: int
    likes: int
    comments: int
    shares: int
    estimated_revenue: float
    rpm: float | None
    traffic_source_json: dict[str, Any]
    geography_json: dict[str, Any]
    metadata_json: dict[str, Any]


class ReviewTaskCreate(BaseModel):
    task_type: ReviewTaskType
    title: str
    payload_json: dict[str, Any] = Field(default_factory=dict)


class ReviewTaskOut(OrmModel):
    id: str
    company_id: str
    workspace_id: str
    project_id: str | None
    task_type: str
    status: str
    title: str
    payload_json: dict[str, Any]
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReviewTaskCardOut(ReviewTaskOut):
    project_state: str | None = None
    summary: str | None = None
    required_actions: list[str] = Field(default_factory=list)
    linked_artifact_ids: list[str] = Field(default_factory=list)
    linked_agent_run_ids: list[str] = Field(default_factory=list)


class ReviewActionRequest(BaseModel):
    action: ReviewActionType
    actor: str | None = "human"
    notes: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)


class ReviewActionOut(OrmModel):
    id: str
    company_id: str
    workspace_id: str
    project_id: str | None
    review_task_id: str
    action: str
    actor: str | None
    notes: str | None
    payload_json: dict[str, Any]
    created_at: datetime


class MemoryItemCreate(BaseModel):
    company_id: str
    workspace_id: str | None = None
    scope: str
    family: str
    type: str = "note"
    title: str
    content: str
    summary: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    sample_size: int = 0
    source_video_ids: list[str] = Field(default_factory=list)


class MemoryItemOut(OrmModel):
    id: str
    company_id: str
    workspace_id: str | None
    scope: str
    family: str
    type: str
    title: str
    content: str
    summary: str | None
    metadata_json: dict[str, Any]
    confidence: float
    sample_size: int
    source_video_ids: list[str]
    status: str
    embedding: list[float] | None
    embedding_model: str | None
    embedding_version: str | None
    embedded_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemorySearchRequest(BaseModel):
    company_id: str
    workspace_id: str
    query: str
    agent_role: str = "generic"
    families: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=lambda: ["workspace_only", "company_global", "platform_global"])
    limit: int = 5
    top_k: int | None = None


class CostReportOut(BaseModel):
    project_id: str
    total_cost: float
    total_input_tokens: int
    total_output_tokens: int
    total_media_units: float
    cost_by_agent: dict[str, float]
    cost_by_provider_model: dict[str, float]
    events: list[dict[str, Any]]


class ProjectArtifactOut(BaseModel):
    id: str
    project_id: str
    type: str
    name: str | None = None
    status: str | None = None
    uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class ProjectArtifactsOut(BaseModel):
    project_id: str
    artifacts: list[ProjectArtifactOut] = Field(default_factory=list)
    render_timelines: list[dict[str, Any]] = Field(default_factory=list)
    qa_reports: list[dict[str, Any]] = Field(default_factory=list)


class AuthorityDecision(BaseModel):
    decision: AuthorityDecisionType
    monetization_passability_impact: Literal["POSITIVE", "NEUTRAL", "NEGATIVE"]
    revenue_impact: Literal["LOW", "MEDIUM", "HIGH"]
    policy_risk: Literal["LOW", "MEDIUM", "HIGH"]
    brand_fit_score: float
    audience_fit_score: float
    buyer_intent_score: float
    reasoning_summary: list[str] = Field(default_factory=list)
    instructions: dict[str, Any] = Field(default_factory=dict)
    playbook_update_allowed: bool = False


class ScriptCriticResult(BaseModel):
    decision: Literal["APPROVE", "REQUEST_REVISION", "REJECT"]
    issues: list[str] = Field(default_factory=list)
    monetization_alignment_score: float
    policy_risk: Literal["LOW", "MEDIUM", "HIGH"]


class MediaQAResult(BaseModel):
    decision: Literal["PASS", "FAIL", "NEEDS_HUMAN_ATTENTION"]
    score: float
    issues: list[str] = Field(default_factory=list)


class ComplianceResult(BaseModel):
    decision: Literal["PASS", "FAIL", "NEEDS_HUMAN_ATTENTION"]
    policy_risk: Literal["LOW", "MEDIUM", "HIGH"]
    issues: list[str] = Field(default_factory=list)


class ComplianceChecklistItem(BaseModel):
    item: str
    status: Literal["PASS", "FAIL", "NEEDS_REVIEW"]
    notes: str | None = None


class ComplianceChecklistResult(BaseModel):
    decision: Literal["PASS", "FAIL", "NEEDS_HUMAN_ATTENTION"]
    policy_risk: Literal["LOW", "MEDIUM", "HIGH"]
    checklist: list[ComplianceChecklistItem] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    required_fixes: list[str] = Field(default_factory=list)
    disclosure_required: bool = False
    ai_disclosure_required: bool = False
    affiliate_disclosure_required: bool = False
    sponsorship_disclosure_required: bool = False
    reused_content_risk: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    copyright_basis: str | None = None
    copyright_risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    monetization_eligibility_risk: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    misleading_claims_risk: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    platform_safety_risk: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    policy_references: list[str] = Field(default_factory=list)
    required_disclosures: list[str] = Field(default_factory=list)


class MemoryCuratorResult(BaseModel):
    decision: Literal["STORE", "SKIP", "NEEDS_HUMAN_ATTENTION"]
    memories: list[MemoryItemCreate] = Field(default_factory=list)


class AnalyticsDiagnosisResult(BaseModel):
    decision: Literal["CONTINUE_CURRENT_PLAYBOOK", "MINOR_TUNING", "CORRECTIVE_ACTION"]
    baseline_comparison: dict[str, Any]
    recommendations: list[str] = Field(default_factory=list)


class MonetizationStrategyResult(BaseModel):
    monetization_hypothesis: str
    target_metric: str
    cta_strategy: str
    expected_revenue_path: str
    buyer_intent_score: float


class ScriptResult(BaseModel):
    script: str
    outline: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    monetization_hypothesis: str | None = None


class SEOMetadataResult(BaseModel):
    recommended_title: str | None = None
    title: str | None = None
    title_variants: list[str] = Field(default_factory=list)
    description: str
    hashtags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    clickbait_risk: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    misleading_risk: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"

    @model_validator(mode="after")
    def normalize_titles(self) -> "SEOMetadataResult":
        if not self.recommended_title and self.title:
            self.recommended_title = self.title
        if not self.title and self.recommended_title:
            self.title = self.recommended_title
        if not self.title:
            raise ValueError("SEO metadata requires title or recommended_title")
        if not self.title_variants:
            self.title_variants = [self.title]
        if not self.tags:
            self.tags = list(self.keywords)
        return self


class PublishingContentResult(BaseModel):
    final_title: str | None = None
    final_description: str | None = None
    pinned_comment: str
    community_post: str
    short_description: str
    disclosure_note: str | None = None
    affiliate_disclosure_note: str | None = None
    sponsorship_disclosure_note: str | None = None
    upload_checklist: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_publishing_fields(self) -> "PublishingContentResult":
        if not self.final_description:
            self.final_description = self.short_description
        if not self.upload_checklist:
            self.upload_checklist = ["title", "description", "pinned_comment", "disclosures"]
        return self


class MemoryBulkCreate(BaseModel):
    items: list[MemoryItemCreate]
    embed: bool = False


class MemoryContextPackRequest(BaseModel):
    company_id: str
    workspace_id: str
    project_id: str | None = None
    agent_role: str
    query: str
    families: list[str] = Field(default_factory=list)
    scopes: list[str] | None = None
    limit: int = 5
    top_k: int | None = None


class SalvageDecision(BaseModel):
    action: Literal[
        "PUBLISH_ANYWAY",
        "CONVERT_TO_EVERGREEN",
        "REPURPOSE_ASSET",
        "ARCHIVE_FOR_REUSE",
        "REJECT",
    ]
    reason: list[str] = Field(default_factory=list)
    next_state: ProjectState | None = None
