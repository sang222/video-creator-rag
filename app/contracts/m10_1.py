import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


FallbackLevel = Literal["PRIMARY", "FALLBACK", "PREMIUM", "EMERGENCY", "BACKUP"]
LLMRouteStatus = Literal["SUCCESS", "FAILED", "SKIPPED", "BLOCKED"]
DerivativeType = Literal["SHORT", "CLIP", "FOLLOW_UP_LONG", "COMPILATION", "UPDATE", "TRANSLATION", "OTHER"]
ShortCandidateState = Literal["GENERATED", "SCORED", "SELECTED_FOR_RENDER", "REJECTED", "NEEDS_REWRITE", "BLOCKED"]
ShortCropStrategy = Literal["VERTICAL_9_16", "CENTER_CROP", "SMART_CROP", "TEMPLATE_CARD", "DIAGRAM_CARD"]
ShortVisualSource = Literal[
    "PARENT_HERO_REUSE",
    "PARENT_SCENE_REUSE",
    "TEMPLATE_CARD",
    "DIAGRAM_CARD",
    "SCREENSHOT",
    "NEW_AI_HERO_REQUIRED",
    "UNKNOWN",
]
TargetShortPlatform = Literal["YOUTUBE_SHORTS", "TIKTOK", "FACEBOOK_REELS"]
UploadPlatform = Literal["YOUTUBE_LONG", "YOUTUBE_SHORTS", "TIKTOK", "FACEBOOK_REELS"]
VoiceSource = Literal["REUSE_PARENT_AUDIO", "NEW_SHORT_VOICE_REQUIRED"]
ShortRenderState = Literal["PLANNED", "BLOCKED", "READY_FOR_M10_2_RENDER", "CANCELLED"]
PromoteShortToLongState = Literal["GENERATED", "NEEDS_MORE_EVIDENCE", "READY_FOR_HUMAN_REVIEW", "REJECTED", "CANCELLED"]
WatchHourPotential = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
ReusableArtifactType = Literal[
    "SCRIPT_BLOCK",
    "RESEARCH_PACKET",
    "DIAGRAM_TEMPLATE",
    "MOTION_TEMPLATE",
    "STOCK_CLIP",
    "AI_VIDEO_CLIP",
    "MUSIC_BED",
    "SFX",
    "VOICE_LINE",
    "CAPTION_STYLE",
    "PROMPT_PREFIX",
    "THUMBNAIL_TEMPLATE",
    "OTHER",
]
ReuseScope = Literal["CHANNEL", "SERIES", "COMPANY", "PROJECT_ONLY"]
ReusableArtifactState = Literal["ACTIVE", "NEEDS_REVIEW", "RETIRED", "BLOCKED"]
OriginalityCheckResult = Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]
OriginalityBudgetResult = Literal["OK", "REVIEW_REQUIRED", "BLOCK"]
ReleasePlanState = Literal["DRAFT", "READY_FOR_HUMAN_REVIEW", "BLOCKED", "CANCELLED"]
FunnelPackageState = Literal["DRAFT", "READY_FOR_HUMAN_REVIEW", "READY_FOR_UPLOAD_TASKS", "BLOCKED", "CANCELLED"]
CTAType = Literal["NONE", "SEARCH_YOUTUBE", "BRAND_CTA", "LINK_IN_BIO", "PINNED_COMMENT"]
MusicPolicy = Literal["SAFE_MODE", "PLATFORM_NATIVE_MODE", "NO_MUSIC_MODE"]
UploadCardState = Literal["DRAFT", "READY", "BLOCKED", "USED", "CANCELLED"]
HumanUploadTaskState = Literal["READY", "UPLOADED", "NEEDS_FIX", "SKIPPED", "CANCELLED"]


class _ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class LLMRouterProfileRead(_ReadModel):
    id: uuid.UUID
    profile_key: str
    provider_key: str
    base_url: str
    real_execution_enabled: bool
    default_timeout_seconds: int
    created_at: AwareDatetime
    updated_at: AwareDatetime


class LLMRouterLaneRead(_ReadModel):
    id: uuid.UUID
    router_profile_id: uuid.UUID
    lane_name: str
    lane_description: str
    allowed_task_types: list[str]
    primary_model: str
    fallback_models: list[str]
    premium_model: str | None
    emergency_model: str | None
    backup_model: str | None
    max_input_tokens: int | None
    max_output_tokens: int | None
    cost_tier: str
    latency_tier: str
    critical_path_allowed: bool
    requires_human_approval_for_premium: bool
    route_priority: int
    real_execution_enabled: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class LLMModelProfileRead(_ReadModel):
    id: uuid.UUID
    provider_key: str
    model_id: str
    model_role: str
    lane_names: list[str]
    is_enabled: bool
    critical_path_allowed: bool
    notes: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class LLMRouteAttemptRead(_ReadModel):
    id: uuid.UUID
    router_profile_id: uuid.UUID
    lane_name: str
    requested_task_type: str | None
    selected_model: str
    fallback_level: FallbackLevel
    request_hash: str
    response_hash: str | None
    status: LLMRouteStatus
    error_code: str | None
    error_message: str | None
    prompt_eval_count: int | None
    eval_count: int | None
    total_duration_ms: int | None
    load_duration_ms: int | None
    prompt_eval_duration_ms: int | None
    eval_duration_ms: int | None
    provider_attempt_id: uuid.UUID | None
    llm_run_snapshot_id: uuid.UUID | None
    created_at: AwareDatetime


class LLMRouteRequest(BaseModel):
    lane_name: str
    prompt: str | None = None
    messages: list[dict[str, str]] | None = None
    requested_task_type: str | None = None
    response_format: Literal["text", "json"] = "text"
    profile_key: str = "default"

    model_config = ConfigDict(extra="forbid")


class LLMRouteResponse(BaseModel):
    status: LLMRouteStatus
    lane_name: str
    selected_model: str
    fallback_level: FallbackLevel
    content: str | None = None
    structured_output: dict[str, Any] | None = None
    route_attempt_id: uuid.UUID
    provider_attempt_id: uuid.UUID | None = None
    llm_run_snapshot_id: uuid.UUID | None = None
    reason_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class LLMRouterSmokeTestRequest(BaseModel):
    profile_key: str = "default"

    model_config = ConfigDict(extra="forbid")


class LLMRouterSmokeTestRead(BaseModel):
    status: LLMRouteStatus
    real_smoke_enabled: bool
    health_check: dict[str, Any] = Field(default_factory=dict)
    cheap_structured: dict[str, Any] = Field(default_factory=dict)
    long_context_text: dict[str, Any] = Field(default_factory=dict)
    fallback_probe: dict[str, Any] = Field(default_factory=dict)
    route_attempt_ids: list[uuid.UUID] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    next_action: str | None = None

    model_config = ConfigDict(extra="forbid")


class ShortCandidateExtractRequest(BaseModel):
    max_candidates: int = Field(default=3, ge=1, le=3)
    use_llm_enhancement: bool = False

    model_config = ConfigDict(extra="forbid")


class ShortCandidateRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    parent_video_project_id: uuid.UUID
    parent_voice_timeline_id: uuid.UUID | None
    parent_caption_track_id: uuid.UUID | None
    parent_visual_plan_id: uuid.UUID | None
    start_time_ms: int
    end_time_ms: int
    duration_ms: int
    caption_ids: list[str]
    core_idea: str
    hook_line: str
    standalone_summary: str
    suggested_title: str | None
    overlay_text: str | None
    crop_strategy: ShortCropStrategy
    visual_source: ShortVisualSource
    candidate_state: ShortCandidateState
    policy_risk_level: str | None
    rights_risk_level: str | None
    production_cost_estimate: dict[str, Any]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ShortCandidateRankRequest(BaseModel):
    select_threshold: Decimal = Decimal("60")

    model_config = ConfigDict(extra="forbid")


class ShortCandidateScoreRead(_ReadModel):
    id: uuid.UUID
    short_candidate_id: uuid.UUID
    hook_strength: Decimal
    standalone_clarity: Decimal
    insight_density: Decimal
    visual_punch: Decimal
    audience_relevance: Decimal
    bridge_value: Decimal
    production_reuse_saving: Decimal
    context_dependency_penalty: Decimal
    policy_risk_penalty: Decimal
    generic_template_penalty: Decimal
    total_score: Decimal
    score_version: str
    explanation: str
    created_at: AwareDatetime


class ContentDerivativeGraphEdgeCreate(BaseModel):
    parent_video_project_id: uuid.UUID | None = None
    parent_uploaded_video_id: uuid.UUID | None = None
    derivative_video_project_id: uuid.UUID | None = None
    derivative_uploaded_video_id: uuid.UUID | None = None
    derivative_type: DerivativeType
    transformation_summary: str
    new_value_added: str | None = None
    originality_score: Decimal | None = None
    reused_runtime_pct: Decimal | None = None
    policy_risk_level: str | None = "LOW"
    rights_risk_level: str | None = "LOW"
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ContentDerivativeGraphEdgeRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    parent_video_project_id: uuid.UUID | None
    parent_uploaded_video_id: uuid.UUID | None
    derivative_video_project_id: uuid.UUID | None
    derivative_uploaded_video_id: uuid.UUID | None
    derivative_type: DerivativeType
    transformation_summary: str
    new_value_added: str | None
    originality_score: Decimal | None
    reused_runtime_pct: Decimal | None
    publish_allowed: bool
    policy_risk_level: str | None
    rights_risk_level: str | None
    source_refs: list[dict[str, Any]]
    technical_appendix: dict[str, Any]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DerivativeOriginalityCheckCreate(BaseModel):
    company_id: uuid.UUID | None = None
    channel_workspace_id: uuid.UUID | None = None
    content_derivative_edge_id: uuid.UUID | None = None
    short_candidate_id: uuid.UUID | None = None
    derivative_type: DerivativeType = "SHORT"
    standalone_value_ok: bool = False
    new_value_added_ok: bool = False
    reused_runtime_pct: Decimal | None = None
    template_repetition_risk: str | None = None
    generic_stock_risk: str | None = None
    commentary_or_context_added: bool = False
    policy_flags: list[dict[str, Any]] = Field(default_factory=list)
    rights_flags: list[dict[str, Any]] = Field(default_factory=list)
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class DerivativeOriginalityCheckRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    content_derivative_edge_id: uuid.UUID | None
    short_candidate_id: uuid.UUID | None
    derivative_type: DerivativeType
    standalone_value_ok: bool
    new_value_added_ok: bool
    reused_runtime_pct: Decimal | None
    template_repetition_risk: str | None
    generic_stock_risk: str | None
    commentary_or_context_added: bool
    policy_flags: list[dict[str, Any]]
    rights_flags: list[dict[str, Any]]
    result: OriginalityCheckResult
    operator_summary: str
    technical_appendix: dict[str, Any]
    created_at: AwareDatetime


class ReusableArtifactCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None = None
    artifact_type: ReusableArtifactType
    content_hash: str
    source_provider: str | None = None
    license_status: str
    rights_envelope_id: uuid.UUID | None = None
    reuse_scope: ReuseScope = "PROJECT_ONLY"
    max_reuse_policy: dict[str, Any] = Field(default_factory=dict)
    cooldown_days: int | None = None
    quality_score: Decimal | None = None
    state: ReusableArtifactState = "ACTIVE"

    model_config = ConfigDict(extra="forbid")


class ReusableArtifactRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    artifact_type: ReusableArtifactType
    content_hash: str
    source_provider: str | None
    license_status: str
    rights_envelope_id: uuid.UUID | None
    reuse_scope: ReuseScope
    reuse_count: int
    max_reuse_policy: dict[str, Any]
    cooldown_days: int | None
    last_used_at: AwareDatetime | None
    last_used_video_ids: list[str]
    quality_score: Decimal | None
    state: ReusableArtifactState
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AssetReuseSearchRequest(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None = None
    scene_requirement_hash: str
    min_match_score: Decimal = Decimal("0")

    model_config = ConfigDict(extra="forbid")


class AssetReuseIndexEntryRead(_ReadModel):
    id: uuid.UUID
    reusable_artifact_id: uuid.UUID
    scene_requirement_hash: str
    match_reason: str
    match_score: Decimal
    last_selected_at: AwareDatetime | None
    created_at: AwareDatetime


class CrossPlatformFunnelPackageCreate(BaseModel):
    parent_video_project_id: uuid.UUID | None = None
    parent_uploaded_video_id: uuid.UUID | None = None
    youtube_long_package_id: uuid.UUID | None = None
    selected_short_candidate_ids: list[uuid.UUID] = Field(default_factory=list)
    bridge_strategy: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class CrossPlatformFunnelPackageRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    parent_video_project_id: uuid.UUID | None
    parent_uploaded_video_id: uuid.UUID | None
    youtube_long_package_id: uuid.UUID | None
    selected_short_candidate_ids: list[str]
    youtube_shorts_package_status: str | None
    tiktok_package_status: str | None
    facebook_reels_package_status: str | None
    bridge_strategy: dict[str, Any]
    package_state: FunnelPackageState
    created_at: AwareDatetime
    updated_at: AwareDatetime


class BuildUploadCardsRequest(BaseModel):
    platforms: list[UploadPlatform] = Field(default_factory=lambda: ["YOUTUBE_SHORTS"])

    model_config = ConfigDict(extra="forbid")


class UploadCardRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    platform: UploadPlatform
    video_project_id: uuid.UUID | None
    short_candidate_id: uuid.UUID | None
    render_plan_id: uuid.UUID | None
    file_ref: str | None
    title_internal: str
    hook_line: str | None
    caption: str | None
    description: str | None
    hashtags: list[str]
    cta_type: CTAType
    cta_text: str | None
    pinned_comment: str | None
    ai_disclosure_required: bool
    ai_disclosure_reason: list[dict[str, Any]]
    music_policy: MusicPolicy
    cover_frame_suggestion: str | None
    human_notes: list[dict[str, Any]]
    paste_back_required_fields: list[str]
    card_state: UploadCardState
    created_at: AwareDatetime
    updated_at: AwareDatetime


class HumanUploadTaskRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    upload_card_id: uuid.UUID
    target_platform: UploadPlatform
    task_state: HumanUploadTaskState
    required_checklist: list[dict[str, Any]]
    scheduled_time_suggestion: AwareDatetime | None
    actual_uploaded_video_id: uuid.UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PromoteShortToLongCandidateCreate(BaseModel):
    source_short_uploaded_video_id: uuid.UUID | None = None
    source_short_candidate_id: uuid.UUID | None = None
    winning_hook: str
    audience_signal: dict[str, Any] = Field(default_factory=dict)
    suggested_long_topic: str
    suggested_outline: dict[str, Any] = Field(default_factory=dict)
    expected_watch_hour_potential: WatchHourPotential = "UNKNOWN"
    confidence_label: str | None = "UNKNOWN"
    risk_level: str | None = "UNKNOWN"
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PromoteShortToLongCandidateRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    source_short_uploaded_video_id: uuid.UUID | None
    source_short_candidate_id: uuid.UUID | None
    winning_hook: str
    audience_signal: dict[str, Any]
    suggested_long_topic: str
    suggested_outline: dict[str, Any]
    expected_watch_hour_potential: WatchHourPotential
    confidence_label: str | None
    risk_level: str | None
    state: PromoteShortToLongState
    evidence_refs: list[dict[str, Any]]
    created_at: AwareDatetime
    updated_at: AwareDatetime
