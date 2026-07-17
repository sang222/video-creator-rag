from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PolicyRef(BaseModel):
    ref: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid")


class RatioBand(BaseModel):
    minimum: float = Field(ge=0, le=1)
    maximum: float = Field(ge=0, le=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ordered(self) -> "RatioBand":
        if self.minimum > self.maximum:
            raise ValueError("ratio band minimum cannot exceed maximum")
        return self


class RuntimeMinuteRange(BaseModel):
    minimum: float = Field(gt=0, le=120)
    maximum: float = Field(gt=0, le=120)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ordered(self) -> "RuntimeMinuteRange":
        if self.minimum > self.maximum:
            raise ValueError("runtime minimum cannot exceed maximum")
        return self


class ChannelIdentityPolicy(BaseModel):
    channel_key: str
    primary_market: str
    locale: str
    content_language: str
    operator_language: str
    primary_platform: Literal["YouTube"]
    primary_format: Literal["long-form documentary/explainer"]

    model_config = ConfigDict(extra="forbid")


class AudiencePacingProfile(BaseModel):
    audience: str
    target_runtime_minutes: RuntimeMinuteRange
    tone: str
    sentence_style: str
    pre_tts_estimate_advisory_only: Literal[True] = True

    model_config = ConfigDict(extra="forbid")


class FormatIdentityBinding(BaseModel):
    ref: str
    version: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["APPROVED"]

    model_config = ConfigDict(extra="forbid")


class ChannelVisualStrategyProfile(BaseModel):
    strategy_label: str
    native_explanatory_target_range: RatioBand
    supporting_visual_target_range: RatioBand
    ai_hero_target_range: RatioBand
    ranges_are_planning_guidance_only: Literal[True] = True
    minimum_pexels_quota: Literal[0] = 0
    minimum_veo_quota: Literal[0] = 0
    asset_selected_only_to_satisfy_ratio: Literal[False] = False
    native_preferred_scene_kinds: list[str] = Field(min_length=1)
    pexels_allowed_scene_kinds: list[str] = Field(min_length=1)
    veo_allowed_scene_kinds: list[str] = Field(min_length=1)
    forced_provider_alternation: Literal[False] = False

    model_config = ConfigDict(extra="forbid")


class MediaProductionProfile(BaseModel):
    native_visual_backbone: Literal[True] = True
    final_render_authority: Literal["native_ffmpeg_renderer"]
    final_narration_authority: Literal["elevenlabs"]
    canonical_media_timeline_required: Literal[True] = True
    drive_verified_archive_only: Literal[True] = True
    youtube_manual_upload_only: Literal[True] = True

    model_config = ConfigDict(extra="forbid")


class VoiceSettings(BaseModel):
    speed: float = Field(gt=0, le=1.2)
    stability: float = Field(ge=0, le=1)
    similarity_boost: float = Field(ge=0, le=1)
    style: float = Field(ge=0, le=1)
    use_speaker_boost: bool

    model_config = ConfigDict(extra="forbid")


class VoicePolicy(BaseModel):
    provider: Literal["elevenlabs"]
    voice_id: str
    voice_name: str
    model_id: str
    commercial_use_state: Literal["APPROVED_PLAN_REQUIRED"]
    pronunciation_dictionary_refs: list[str]
    settings: VoiceSettings
    one_complete_narration_preferred: Literal[True] = True
    forced_alignment_required: Literal[True] = True
    canonical_media_timeline_required: Literal[True] = True
    unavailable_behavior: Literal["BLOCK_FOR_REVIEW"]
    paid_retry_cap: int = Field(ge=0, le=1)
    retry_requires_new_approval: Literal[True] = True

    model_config = ConfigDict(extra="forbid")


class CreativeQualityBinding(BaseModel):
    policy_ref: str
    policy_version: str
    source_run_id: str
    required_families: list[str] = Field(min_length=7)

    model_config = ConfigDict(extra="forbid")


class CharacterPolicy(BaseModel):
    mode: Literal["NO_CHARACTER"]
    recurring_host_allowed: Literal[False] = False
    real_person_likeness_allowed: Literal[False] = False

    model_config = ConfigDict(extra="forbid")


class PexelsUsagePolicy(BaseModel):
    enabled: bool
    optional: Literal[True] = True
    role: Literal["SUPPORTING_ONLY"]
    semantic_fit_threshold: float = Field(gt=0, le=1)
    max_searches_per_video: int = Field(ge=0)
    max_downloads_per_video: int = Field(ge=0)
    factual_evidence_allowed: Literal[False] = False
    recurring_host_allowed: Literal[False] = False
    rights_provenance_required: Literal[True] = True
    minimum_quota: Literal[0] = 0

    model_config = ConfigDict(extra="forbid")


class VeoUsagePolicy(BaseModel):
    enabled: bool
    optional: Literal[True] = True
    role: Literal["AI_HERO_ONLY"]
    allowed_hero_reasons: list[str] = Field(min_length=1)
    approved_model_catalog_ref: str
    max_hero_clips_per_video: int = Field(ge=0)
    max_hero_seconds_per_video: float = Field(ge=0)
    max_hero_cost_usd_per_video: float = Field(ge=0)
    provider_audio_policy: Literal["DISCARD"]
    unavailable_behavior: Literal["NATIVE_VISUAL_OR_REVIEW"]
    external_provider_fallback_allowed: Literal[False] = False
    minimum_quota: Literal[0] = 0

    model_config = ConfigDict(extra="forbid")


class ElevenLabsUsagePolicy(BaseModel):
    enabled: bool
    final_narration_authority: Literal[True] = True
    forced_alignment_required: Literal[True] = True
    initial_tts_attempts: Literal[1] = 1
    controlled_retry_requires_new_approval: Literal[True] = True

    model_config = ConfigDict(extra="forbid")


class ProviderUsagePolicy(BaseModel):
    pexels: PexelsUsagePolicy
    google_veo: VeoUsagePolicy
    elevenlabs: ElevenLabsUsagePolicy
    native_ffmpeg_final_render_authority: Literal[True] = True
    drive_archive_required_before_cleanup: Literal[True] = True
    youtube_manual_publish_only: Literal[True] = True

    model_config = ConfigDict(extra="forbid")


class CostEnvelope(BaseModel):
    channel_stage: Literal["NEW_UNPROVEN"]
    tier: Literal["TIER_0_TEXT_ONLY", "TIER_1_LOW_COST_PRODUCTION"]
    currency: Literal["USD"]
    max_estimated_cost_per_video: float = Field(ge=0)
    max_actual_cost_per_video: float = Field(ge=0)
    max_paid_attempts_per_provider_per_video: int = Field(ge=0, le=1)
    max_veo_clips_per_video: int = Field(ge=0)
    max_veo_seconds_per_video: float = Field(ge=0)
    max_veo_cost_per_video: float = Field(ge=0)
    monthly_channel_budget: float = Field(ge=0)
    cost_overrun_review_required: Literal[True] = True
    premium_experiment_permission: Literal[False] = False
    resolution_state: Literal["APPROVED_DETERMINISTIC"]
    derivation_refs: list[str] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class EvidencePolicy(BaseModel):
    material_claim_ledger_required: Literal[True] = True
    scenario_assumptions_required: Literal[True] = True
    stock_is_not_factual_evidence: Literal[True] = True
    source_rights_required: Literal[True] = True
    human_full_watch_required: Literal[True] = True

    model_config = ConfigDict(extra="forbid")


class OriginalityPolicy(BaseModel):
    format_identity_contract_ref: str
    fixed_identity_elements: list[str] = Field(min_length=1)
    must_vary_elements: list[str] = Field(min_length=1)
    hook_repetition_budget: int = Field(ge=1)
    thumbnail_grammar_repetition_budget: int = Field(ge=1)
    asset_reuse_checks_required: Literal[True] = True
    hero_concept_reuse_checks_required: Literal[True] = True
    rolling_same_channel_comparison_scope: int = Field(ge=1)
    cross_channel_duplication_awareness: Literal[True] = True

    model_config = ConfigDict(extra="forbid")


class PublishPolicy(BaseModel):
    primary_destination: Literal["YouTube"]
    manual_upload_only: Literal[True] = True
    synthetic_media_disclosure_required: Literal[True] = True
    rights_license_complete_required: Literal[True] = True
    metadata_thumbnail_truthfulness_required: Literal[True] = True
    human_final_approval_required: Literal[True] = True
    drive_archive_required: Literal[True] = True
    local_purge_after_archive_state: Literal["ARCHIVE_VERIFIED"]

    model_config = ConfigDict(extra="forbid")


class AnalyticsMaturityPolicy(BaseModel):
    maturity: Literal["NEW_UNPROVEN"]
    learning_promotion_allowed: Literal[False] = False
    minimum_published_episode_count_before_promotion: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class GatePolicy(BaseModel):
    hard_policy_cannot_be_weakened: Literal[True] = True
    technical_media_qc_required: Literal[True] = True
    creative_perceptual_media_qc_required: Literal[True] = True
    human_full_watch_required: Literal[True] = True
    no_gate_weakening: Literal[True] = True

    model_config = ConfigDict(extra="forbid")


class CapabilityRequirements(BaseModel):
    required: list[str] = Field(min_length=1)
    launch_requires_all: Literal[True] = True

    model_config = ConfigDict(extra="forbid")


class ChannelScopedPolicy(BaseModel):
    channel_key: str
    policy_version: str
    policy_status: Literal["APPROVED", "FIXTURE_ONLY"]
    approval_ref: str
    channel_identity_policy: ChannelIdentityPolicy
    audience_pacing_profile: AudiencePacingProfile
    format_identity_contract: FormatIdentityBinding
    channel_visual_strategy_profile: ChannelVisualStrategyProfile
    media_production_profile: MediaProductionProfile
    voice_policy: VoicePolicy
    creative_quality_binding: CreativeQualityBinding
    character_policy: CharacterPolicy
    provider_usage_policy: ProviderUsagePolicy
    budget_policy: CostEnvelope
    evidence_policy: EvidencePolicy
    originality_policy: OriginalityPolicy
    publish_policy: PublishPolicy
    analytics_maturity_policy: AnalyticsMaturityPolicy
    gate_policy: GatePolicy
    capability_requirements: CapabilityRequirements

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def scope_and_invariants(self) -> "ChannelScopedPolicy":
        if self.channel_identity_policy.channel_key != self.channel_key:
            raise ValueError("channel identity does not match policy scope")
        if self.originality_policy.format_identity_contract_ref != self.format_identity_contract.ref:
            raise ValueError("originality policy must bind the same format identity contract")
        veo = self.provider_usage_policy.google_veo
        if veo.max_hero_clips_per_video != self.budget_policy.max_veo_clips_per_video:
            raise ValueError("Veo clip cap mismatch")
        if veo.max_hero_seconds_per_video != self.budget_policy.max_veo_seconds_per_video:
            raise ValueError("Veo seconds cap mismatch")
        if veo.max_hero_cost_usd_per_video != self.budget_policy.max_veo_cost_per_video:
            raise ValueError("Veo cost cap mismatch")
        return self


class NativeRenderPolicySnapshot(BaseModel):
    policy_ref: str
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_render_authority: Literal["native_ffmpeg_renderer"]
    temporal_authority: Literal["CanonicalMediaTimeline"]
    strict_plan_requires_final_audio: Literal[True] = True
    caption_policy_ref: str
    caption_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid")


class CapabilityEvaluation(BaseModel):
    status: Literal["PASS", "BLOCKED"]
    required: list[str]
    available: list[str]
    blockers: list[str]

    model_config = ConfigDict(extra="forbid")


class LaunchRestrictions(BaseModel):
    provider_execution_enabled: Literal[False] = False
    production_render_enabled: Literal[False] = False
    drive_upload_enabled: Literal[False] = False
    youtube_action_enabled: Literal[False] = False
    manual_publish_only: Literal[True] = True
    future_projects_only: Literal[True] = True

    model_config = ConfigDict(extra="forbid")


class CompilerInputManifest(BaseModel):
    precedence: list[str] = Field(min_length=7)
    profile_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_policy_catalog_ref: str
    channel_policy_catalog_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    creative_quality_policy_ref: str
    creative_quality_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    format_identity_contract_ref: str
    format_identity_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid")


class PolicySnapshotRefs(BaseModel):
    native_render_policy: PolicyRef
    creative_quality_policy: PolicyRef
    provider_usage_policy: PolicyRef
    budget_policy: PolicyRef
    publish_policy: PolicyRef
    format_identity_contract: PolicyRef

    model_config = ConfigDict(extra="forbid")
