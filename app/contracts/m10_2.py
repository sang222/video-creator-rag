import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


MediaProviderType = Literal[
    "WORKFLOW_ORCHESTRATOR",
    "LLM_SCRIPT_ENGINE",
    "API_NATIVE_TTS",
    "CAPTION_TIMELINE_ENGINE",
    "AI_VIDEO_HERO_PROVIDER",
    "CLOUD_TEMPLATE_RENDERER_LIGHT",
    "CLOUD_FINAL_ASSEMBLY_RENDERER",
    "MEDIA_STORAGE",
    "MEDIA_QC_ENGINE",
    "PUBLISH_PACKAGE_BUILDER",
    "API_NATIVE_STOCK_PROVIDER",
    "FREE_FALLBACK_PROVIDER",
    "DEFERRED_MANUAL_LIBRARY",
]
MediaProviderRecommendation = Literal[
    "CORE",
    "CORE_QUALITY_LAYER",
    "CORE_LIGHT_RENDER",
    "REQUIRED_GAP",
    "DEFERRED",
    "FALLBACK",
    "AVOIDED",
]
MediaJobType = Literal[
    "TOPIC_DECISION",
    "LONG_SCRIPT_GENERATION",
    "LONG_VOICE_GENERATION",
    "LONG_CAPTION_TIMELINE",
    "LONG_VISUAL_PLAN",
    "AI_HERO_GENERATION",
    "AI_METAPHOR_GENERATION",
    "TITLE_CARD_RENDER",
    "DIAGRAM_CARD_RENDER",
    "STAT_CARD_RENDER",
    "LOWER_THIRD_RENDER",
    "HERO_COMPOSITION_RENDER",
    "THUMBNAIL_RENDER",
    "LONG_FORM_FINAL_RENDER",
    "LONG_MEDIA_QC",
    "LONG_PUBLISH_PACKAGE",
    "SHORT_CANDIDATE_EXTRACTION",
    "SHORT_SCRIPT_GENERATION",
    "SHORT_VOICE_GENERATION",
    "SHORT_CAPTION_TIMELINE",
    "SHORT_HERO_REUSE",
    "SHORT_RENDER",
    "SHORT_MEDIA_QC",
    "SHORT_PUBLISH_PACKAGE",
    "PREVIEW_CLIP_RENDER",
    "LICENSE_EVIDENCE_CHECK",
    "BUDGET_CHECK",
    "PROVIDER_CAPABILITY_CHECK",
    "VOICE_GENERATION",
]
ProviderCapability = Literal[
    "SUPPORTED",
    "UNSUPPORTED",
    "BLOCKED_BY_PLAN",
    "REQUIRES_UPGRADE",
    "REQUIRES_EXTERNAL_PROVIDER",
]
MediaRoutingResult = Literal[
    "ROUTED",
    "BLOCKED_PROVIDER_CAPABILITY_REQUIRED",
    "BLOCKED_BUDGET",
    "BLOCKED_LICENSE",
    "BLOCKED_UNKNOWN_PROVIDER",
    "BLOCKED_SCOPE",
]
MediaBudgetMode = Literal["QUALITY_FIRST_250", "CUSTOM", "TEST"]
MediaBudgetEnforcement = Literal["HARD_BLOCK", "REVIEW_REQUIRED", "OBSERVE_ONLY"]
MediaBudgetState = Literal["OK", "WARNING", "EXCEEDED", "UNKNOWN"]
LongFormRenderPackageState = Literal[
    "DRAFT",
    "READY_FOR_FINAL_RENDER",
    "BLOCKED_PROVIDER_CAPABILITY_REQUIRED",
    "FINAL_RENDERED",
    "QC_READY",
    "CANCELLED",
]
ShortRenderPackageState = Literal["DRAFT", "READY_FOR_TEMPLATE_RENDER", "RENDERED", "QC_READY", "BLOCKED", "CANCELLED"]
AIHeroIntendedUsage = Literal["OPENING_HOOK", "KEY_METAPHOR", "SHORT_HOOK", "THUMBNAIL_STILL", "OTHER"]
AIHeroAssetState = Literal["PLANNED", "READY_FOR_PROVIDER", "GENERATED", "BLOCKED", "CANCELLED"]
CreatomateRenderAssetState = Literal["PLANNED", "READY_FOR_PROVIDER", "RENDERED", "BLOCKED", "CANCELLED"]
ThumbnailVariantState = Literal["DRAFT", "READY_FOR_PROVIDER", "RENDERED", "SELECTED", "REJECTED", "CANCELLED"]
FinalMediaType = Literal["LONG_FORM_FINAL", "SHORT_FINAL", "THUMBNAIL", "CARD", "AI_HERO", "PREVIEW"]
LicenseStatus = Literal["CONFIRMED", "NEEDS_REVIEW", "BLOCKED", "NOT_REQUIRED", "UNKNOWN"]
ProviderGateDecision = Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]


class _ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class MediaProviderRoleProfileRead(_ReadModel):
    id: uuid.UUID
    provider_key: str
    provider_name: str
    provider_type: MediaProviderType
    role_description: str
    recommendation: MediaProviderRecommendation
    is_enabled: bool
    is_real_provider: bool
    supports_real_execution: bool
    monthly_budget_assumption: dict[str, Any]
    notes: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ProviderCapabilityMatrixEntryRead(_ReadModel):
    id: uuid.UUID
    provider_key: str
    provider_type: MediaProviderType
    job_type: str
    capability: ProviderCapability
    max_duration_seconds: Decimal | None
    supported_aspect_ratios: list[str]
    supported_outputs: list[str]
    plan_requirement: str | None
    capability_reason: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class MediaRenderRoutingDecisionRequest(BaseModel):
    company_id: uuid.UUID | None = None
    channel_workspace_id: uuid.UUID | None = None
    video_project_id: uuid.UUID | None = None
    job_type: str
    requested_provider_type: MediaProviderType | None = None
    target_duration_seconds: Decimal | None = None
    target_aspect_ratio: str | None = None
    estimated_usage_usd: Decimal | None = None
    estimated_usage_seconds: Decimal | None = None
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class MediaRenderRoutingDecisionRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    channel_workspace_id: uuid.UUID | None
    video_project_id: uuid.UUID | None
    job_type: str
    requested_provider_type: MediaProviderType | None
    selected_provider_type: MediaProviderType | None
    selected_provider_key: str | None
    routing_result: MediaRoutingResult
    blocker_reason: str | None
    capability_entry_id: uuid.UUID | None
    budget_snapshot_id: uuid.UUID | None
    technical_appendix: dict[str, Any]
    created_at: AwareDatetime


class MediaProviderBudgetPolicyRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    provider_type: MediaProviderType
    provider_key: str | None
    monthly_cap_units: Decimal | None
    monthly_cap_usd: Decimal | None
    monthly_cap_seconds: Decimal | None
    monthly_cap_renders: int | None
    current_mode: MediaBudgetMode
    enforcement: MediaBudgetEnforcement
    created_at: AwareDatetime
    updated_at: AwareDatetime


class MediaProviderBudgetSnapshotRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    provider_type: MediaProviderType
    provider_key: str | None
    period_start: AwareDatetime
    period_end: AwareDatetime
    estimated_usage_units: Decimal | None
    estimated_usage_usd: Decimal | None
    estimated_usage_seconds: Decimal | None
    estimated_render_count: int | None
    budget_state: MediaBudgetState
    created_at: AwareDatetime


class MediaProviderBudgetCheckRequest(BaseModel):
    company_id: uuid.UUID | None = None
    provider_type: MediaProviderType
    provider_key: str | None = None
    estimated_usage_units: Decimal | None = None
    estimated_usage_usd: Decimal | None = None
    estimated_usage_seconds: Decimal | None = None
    estimated_render_count: int | None = None

    model_config = ConfigDict(extra="forbid")


class MediaProviderBudgetGateRead(BaseModel):
    decision: ProviderGateDecision
    budget_state: MediaBudgetState
    reason_codes: list[str] = Field(min_length=1)
    operator_summary: str
    policy_id: uuid.UUID | None = None
    snapshot_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class ProviderCapabilityGateCheckRequest(BaseModel):
    job_type: str
    provider_key: str | None = None
    provider_type: MediaProviderType | None = None
    target_duration_seconds: Decimal | None = None
    target_aspect_ratio: str | None = None

    model_config = ConfigDict(extra="forbid")


class ProviderCapabilityGateRead(BaseModel):
    decision: ProviderGateDecision
    routing_result: MediaRoutingResult | None = None
    provider_key: str | None = None
    provider_type: MediaProviderType | None = None
    capability: ProviderCapability | None = None
    reason_codes: list[str] = Field(min_length=1)
    blocker_reason: str | None = None
    operator_summary: str
    capability_entry_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class LicenseEvidenceGateCheckRequest(BaseModel):
    company_id: uuid.UUID | None = None
    channel_workspace_id: uuid.UUID | None = None
    video_project_id: uuid.UUID | None = None
    asset_ref: str
    source_provider_type: MediaProviderType
    license_status: LicenseStatus = "UNKNOWN"
    rights_envelope_id: uuid.UUID | None = None
    evidence_text: str | None = None
    evidence_ref: str | None = None

    model_config = ConfigDict(extra="forbid")


class LicenseEvidenceGateRead(BaseModel):
    decision: ProviderGateDecision
    license_status: LicenseStatus
    reason_codes: list[str] = Field(min_length=1)
    operator_summary: str
    license_evidence_record_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class ReusedContentRiskGateCheckRequest(BaseModel):
    template_only: bool = False
    original_script_present: bool = True
    topic_specific_examples_present: bool = True
    human_approval_path_present: bool = True
    reused_runtime_pct: Decimal | None = None

    model_config = ConfigDict(extra="forbid")


class ReusedContentRiskGateRead(BaseModel):
    decision: ProviderGateDecision
    reason_codes: list[str] = Field(min_length=1)
    operator_summary: str

    model_config = ConfigDict(extra="forbid")


class MediaQCGateCheckRequest(BaseModel):
    media_qc_report_id: uuid.UUID | None = None
    file_ref: str | None = None
    duration_ok: bool = True
    aspect_ratio_ok: bool = True
    audio_present: bool = True
    captions_readable: bool = True
    black_frames_detected: bool = False

    model_config = ConfigDict(extra="forbid")


class MediaQCGateRead(BaseModel):
    decision: ProviderGateDecision
    reason_codes: list[str] = Field(min_length=1)
    operator_summary: str

    model_config = ConfigDict(extra="forbid")


class HumanApprovalGateRead(BaseModel):
    decision: ProviderGateDecision
    reason_codes: list[str] = Field(min_length=1)
    operator_summary: str

    model_config = ConfigDict(extra="forbid")


class YouTubeOnlyAnalyticsGateRead(BaseModel):
    decision: ProviderGateDecision
    reason_codes: list[str] = Field(min_length=1)
    operator_summary: str

    model_config = ConfigDict(extra="forbid")


class LongFormRenderPackageCreate(BaseModel):
    voice_timeline_id: uuid.UUID | None = None
    caption_track_id: uuid.UUID | None = None
    visual_plan_id: uuid.UUID | None = None
    ai_hero_asset_refs: list[dict[str, Any]] = Field(default_factory=list)
    creatomate_asset_refs: list[dict[str, Any]] = Field(default_factory=list)
    approved_asset_refs: list[dict[str, Any]] = Field(default_factory=list)
    thumbnail_variant_refs: list[dict[str, Any]] = Field(default_factory=list)
    music_sfx_refs: list[dict[str, Any]] = Field(default_factory=list)
    cloud_media_refs: list[dict[str, Any]] = Field(default_factory=list)
    render_manifest: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class LongFormRenderPackageRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    voice_timeline_id: uuid.UUID | None
    caption_track_id: uuid.UUID | None
    visual_plan_id: uuid.UUID | None
    ai_hero_asset_refs: list[dict[str, Any]]
    creatomate_asset_refs: list[dict[str, Any]]
    approved_asset_refs: list[dict[str, Any]]
    thumbnail_variant_refs: list[dict[str, Any]]
    music_sfx_refs: list[dict[str, Any]]
    cloud_media_refs: list[dict[str, Any]]
    render_manifest: dict[str, Any]
    final_renderer_required: bool
    final_renderer_provider_key: str | None
    package_state: LongFormRenderPackageState
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ShortRenderPackageCreate(BaseModel):
    short_render_plan_id: uuid.UUID | None = None
    voice_ref: str | None = None
    caption_track_id: uuid.UUID | None = None
    hero_reuse_ref: str | None = None
    template_asset_refs: list[dict[str, Any]] = Field(default_factory=list)
    cloud_media_refs: list[dict[str, Any]] = Field(default_factory=list)
    render_manifest: dict[str, Any] = Field(default_factory=dict)
    target_duration_seconds: Decimal | None = None
    target_aspect_ratio: str = "9:16"

    model_config = ConfigDict(extra="forbid")


class ShortRenderPackageRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID | None
    short_candidate_id: uuid.UUID | None
    short_render_plan_id: uuid.UUID | None
    voice_ref: str | None
    caption_track_id: uuid.UUID | None
    hero_reuse_ref: str | None
    template_asset_refs: list[dict[str, Any]]
    cloud_media_refs: list[dict[str, Any]]
    render_manifest: dict[str, Any]
    target_duration_seconds: Decimal | None
    target_aspect_ratio: str
    hard_cap_seconds: int
    renderer_provider_key: str | None
    package_state: ShortRenderPackageState
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AIHeroAssetPlanRequest(BaseModel):
    prompt: str
    intended_usage: AIHeroIntendedUsage = "OPENING_HOOK"
    duration_seconds: Decimal | None = None

    model_config = ConfigDict(extra="forbid")


class AIHeroAssetRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID | None
    prompt: str
    intended_usage: AIHeroIntendedUsage
    provider_type: MediaProviderType
    provider_key: str | None
    duration_seconds: Decimal | None
    asset_ref: str | None
    still_frame_ref: str | None
    rights_evidence_ref: str | None
    generation_state: AIHeroAssetState
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AIHeroGenerationExecuteRequest(BaseModel):
    output_gcs_uri: str | None = None

    model_config = ConfigDict(extra="forbid")


class AIHeroGenerationJobRead(BaseModel):
    ai_hero_asset_id: uuid.UUID
    provider_key: str | None
    provider_type: MediaProviderType
    generation_state: AIHeroAssetState
    model: str | None = None
    mode: str | None = None
    resolution: str | None = None
    audio_enabled: bool | None = None
    requested_duration_seconds: Decimal | None = None
    estimated_cost_usd: Decimal | None = None
    budget_gate: MediaProviderBudgetGateRead | None = None
    real_execution_attempted: bool = False
    asset_ref: str | None = None
    still_frame_ref: str | None = None
    provider_operation_ref: str | None = None
    reason_codes: list[str] = Field(min_length=1)
    operator_summary: str

    model_config = ConfigDict(extra="forbid")


class CreatomateRenderAssetPlanRequest(BaseModel):
    job_type: str
    short_candidate_id: uuid.UUID | None = None
    template_key: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class CreatomateRenderAssetRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID | None
    short_candidate_id: uuid.UUID | None
    job_type: str
    template_key: str | None
    input_payload: dict[str, Any]
    output_ref: str | None
    provider_type: MediaProviderType
    provider_key: str | None
    render_state: CreatomateRenderAssetState
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ThumbnailVariantInput(BaseModel):
    variant_label: str
    title_text: str
    subtitle_text: str | None = None
    hero_still_ref: str | None = None

    model_config = ConfigDict(extra="forbid")


class ThumbnailVariantPlanRequest(BaseModel):
    variants: list[ThumbnailVariantInput] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class ThumbnailVariantRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    variant_label: str
    title_text: str
    subtitle_text: str | None
    hero_still_ref: str | None
    output_ref: str | None
    provider_type: MediaProviderType
    provider_key: str | None
    state: ThumbnailVariantState
    created_at: AwareDatetime
    updated_at: AwareDatetime


class FinalMediaRefCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID | None = None
    uploaded_video_id: uuid.UUID | None = None
    media_type: FinalMediaType
    file_ref: str
    duration_seconds: Decimal | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    provider_key: str | None = None
    provider_type: MediaProviderType | None = None
    media_qc_report_id: uuid.UUID | None = None
    cloud_media_ref_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class FinalMediaRefRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID | None
    uploaded_video_id: uuid.UUID | None
    media_type: FinalMediaType
    file_ref: str
    duration_seconds: Decimal | None
    aspect_ratio: str | None
    resolution: str | None
    provider_key: str | None
    provider_type: MediaProviderType | None
    media_qc_report_id: uuid.UUID | None
    cloud_media_ref_id: uuid.UUID | None
    created_at: AwareDatetime


class LicenseEvidenceRecordRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    video_project_id: uuid.UUID | None
    asset_ref: str
    source_provider_type: MediaProviderType
    license_status: LicenseStatus
    rights_envelope_id: uuid.UUID | None
    evidence_text: str | None
    evidence_ref: str | None
    created_at: AwareDatetime
