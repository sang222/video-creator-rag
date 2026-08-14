"""Typed market-aware voice casting and narration-performance contracts."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

_SHA256 = r"^[0-9a-f]{64}$"

NarrationMode = Literal[
    "TECHNICAL_EXPLAINER",
    "ANALYTICAL",
    "TACTICAL",
    "STORY_CASE_STUDY",
    "DOCUMENTARY",
    "CAUTIONARY",
]
NarrationFunction = Literal[
    "HOOK",
    "SETUP",
    "PROBLEM",
    "EXPLANATION",
    "PROCESS",
    "CONTRAST",
    "KEY_INSIGHT",
    "EXAMPLE",
    "WARNING",
    "LIMITATION",
    "PAYOFF",
    "CONCLUSION",
]
DeliveryIntent = Literal[
    "CURIOUS_ENGAGED",
    "CLEAR_PRECISE",
    "CONVERSATIONAL",
    "SERIOUS_MEASURED",
    "CAUTIONARY",
    "EMPHATIC",
    "CONFIDENT",
    "WARM",
    "DECISIVE",
]
TTSExecutionStrategy = Literal[
    "SINGLE_REQUEST_EXPRESSIVE",
    "CONTEXT_STITCHED_MULTI_REQUEST",
    "SEGMENTED_WITH_SEAM_QC",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VoiceMarketIdentity(_StrictModel):
    primary_market: str = Field(min_length=2)
    target_countries: list[str] = Field(min_length=1)
    content_language: str = Field(min_length=2)
    locale: str = Field(min_length=2)
    audience_profile: dict[str, Any] = Field(default_factory=dict)
    channel_positioning: str = Field(min_length=3)


class NarrationMarketRequirements(_StrictModel):
    accent_families: list[str] = Field(min_length=1)
    pronunciation_locale: str = Field(min_length=2)
    clarity_profile: Literal["LOW", "MEDIUM", "HIGH"] = "HIGH"
    pacing_profile: Literal["SLOW", "MEASURED", "MEDIUM", "MEDIUM_FAST", "FAST"]
    energy_profile: Literal["LOW", "CONTROLLED", "MEDIUM", "MEDIUM_HIGH"]
    authority_profile: Literal["LOW", "MEDIUM", "HIGH"]
    warmth_profile: Literal["LOW", "MEDIUM", "HIGH"]
    conversationality_profile: Literal["LOW", "MEDIUM", "HIGH"]
    required_narration_modes: list[NarrationMode] = Field(min_length=1)


class VoiceResearchEvidence(_StrictModel):
    evidence_id: str = Field(min_length=1)
    source_url: str | None = None
    source_title: str = Field(min_length=1)
    source_class: Literal[
        "CHANNEL_POLICY",
        "MARKET_RESEARCH",
        "PROVIDER_CATALOG",
        "HUMAN_APPROVED_REFERENCE",
    ]
    excerpt: str = Field(min_length=3)
    source_hash: str = Field(pattern=_SHA256)


class VoiceMarketResearchCreate(_StrictModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    market_identity: VoiceMarketIdentity
    requirements: NarrationMarketRequirements
    evidence: list[VoiceResearchEvidence] = Field(min_length=1)
    confidence_label: Literal["LOW", "MEDIUM", "HIGH"]
    limitations: list[str] = Field(default_factory=list)
    created_by_user_id: uuid.UUID | None = None


class VoiceMarketResearchRead(VoiceMarketResearchCreate):
    id: uuid.UUID
    schema_version: str
    state: str
    content_hash: str = Field(pattern=_SHA256)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ProviderVoiceCandidate(_StrictModel):
    voice_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    provider: Literal["elevenlabs"] = "elevenlabs"
    language_tags: list[str] = Field(min_length=1)
    locale_tags: list[str] = Field(default_factory=list)
    accent_tags: list[str] = Field(default_factory=list)
    narration_mode_fit: list[NarrationMode] = Field(min_length=1)
    market_fit_tags: list[str] = Field(default_factory=list)
    clarity_score: int = Field(ge=0, le=100)
    energy_score: int = Field(ge=0, le=100)
    warmth_score: int = Field(ge=0, le=100)
    authority_score: int = Field(ge=0, le=100)
    conversationality_score: int = Field(ge=0, le=100)
    approved_model_ids: list[str] = Field(min_length=1)
    default_model_id: str = Field(min_length=1)
    default_settings: dict[str, Any]
    safe_setting_bounds: dict[str, dict[str, float]]
    commercial_use_state: Literal["APPROVED", "REQUIRES_APPROVED_PLAN"]
    availability_state: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]
    priority: int = Field(default=100, ge=0, le=1000)
    evidence_refs: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_defaults(self) -> "ProviderVoiceCandidate":
        if self.default_model_id not in self.approved_model_ids:
            raise ValueError("VOICE_DEFAULT_MODEL_NOT_APPROVED")
        required_settings = {
            "speed",
            "stability",
            "similarity_boost",
            "style",
            "use_speaker_boost",
        }
        if set(self.default_settings) != required_settings:
            raise ValueError("VOICE_DEFAULT_SETTINGS_INVALID")
        for key in ("speed", "stability", "similarity_boost", "style"):
            bounds = self.safe_setting_bounds.get(key)
            if not isinstance(bounds, dict) or set(bounds) != {"min", "max"}:
                raise ValueError("VOICE_SAFE_SETTING_BOUNDS_INVALID")
            value = float(self.default_settings[key])
            if not float(bounds["min"]) <= value <= float(bounds["max"]):
                raise ValueError("VOICE_DEFAULT_SETTING_OUTSIDE_SAFE_BOUNDS")
        return self


class VoiceProviderCatalogCreate(_StrictModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    provider: Literal["elevenlabs"] = "elevenlabs"
    catalog_version: str = Field(min_length=1)
    voices: list[ProviderVoiceCandidate] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    created_by_user_id: uuid.UUID | None = None


class VoiceProviderCatalogRead(VoiceProviderCatalogCreate):
    id: uuid.UUID
    schema_version: str
    content_hash: str = Field(pattern=_SHA256)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ApprovedVoicePoolCreate(_StrictModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    voice_market_research_id: uuid.UUID
    provider_catalog_snapshot_id: uuid.UUID
    version: int = Field(ge=1)
    voices: list[ProviderVoiceCandidate] = Field(min_length=1)
    status: Literal["APPROVED"] = "APPROVED"
    approved_by_user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def unique_voices(self) -> "ApprovedVoicePoolCreate":
        ids = [voice.voice_id for voice in self.voices]
        if len(ids) != len(set(ids)):
            raise ValueError("APPROVED_VOICE_POOL_DUPLICATE_VOICE")
        if not any(voice.availability_state == "AVAILABLE" for voice in self.voices):
            raise ValueError("APPROVED_VOICE_POOL_NO_AVAILABLE_VOICE")
        return self


class ApprovedVoicePoolRead(ApprovedVoicePoolCreate):
    id: uuid.UUID
    schema_version: str
    content_hash: str = Field(pattern=_SHA256)
    approved_at: AwareDatetime
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class VoiceCastingRequest(_StrictModel):
    video_project_id: uuid.UUID
    qualified_script_ref: str = Field(min_length=1)
    qualified_script_hash: str = Field(pattern=_SHA256)
    narration_mode: NarrationMode
    required_locale: str = Field(min_length=2)
    required_market: str = Field(min_length=2)
    baseline_delivery_profile: dict[str, Any] = Field(default_factory=dict)
    casting_policy_version: str = Field(min_length=1)
    created_by_user_id: uuid.UUID | None = None


class VoiceCastingDecisionRead(_StrictModel):
    id: uuid.UUID
    schema_version: str
    video_project_id: uuid.UUID
    approved_voice_pool_id: uuid.UUID
    approved_voice_pool_hash: str = Field(pattern=_SHA256)
    qualified_script_ref: str
    qualified_script_hash: str = Field(pattern=_SHA256)
    narration_mode: NarrationMode
    selected_voice_id: str
    selected_model_id: str
    baseline_delivery_profile: dict[str, Any]
    selection_reason_codes: list[str]
    market_fit_evidence_refs: list[str]
    series_narrator_binding_id: uuid.UUID | None = None
    casting_policy_version: str
    decision_version: int
    state: str
    content_hash: str = Field(pattern=_SHA256)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class NarrationVoiceSnapshotRead(_StrictModel):
    id: uuid.UUID
    schema_version: str
    video_project_id: uuid.UUID
    voice_casting_decision_id: uuid.UUID
    approved_voice_pool_id: uuid.UUID
    provider: str
    voice_id: str
    model_id: str
    baseline_voice_settings: dict[str, Any]
    voice_catalog_version: str
    approved_voice_pool_version: int
    market_identity_hash: str = Field(pattern=_SHA256)
    qualified_script_hash: str = Field(pattern=_SHA256)
    state: str
    content_hash: str = Field(pattern=_SHA256)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class NarrationPerformanceBeat(_StrictModel):
    beat_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    source_text_start: int = Field(ge=0)
    source_text_end: int = Field(gt=0)
    source_text_hash: str = Field(pattern=_SHA256)
    narration_function: NarrationFunction
    delivery_intent: DeliveryIntent
    energy: Literal["LOW", "CONTROLLED", "MEDIUM", "MEDIUM_HIGH"]
    pace: Literal["SLOW", "MEASURED", "MEDIUM", "MEDIUM_FAST"]
    emphasis: Literal["LOW", "MEDIUM", "HIGH"]
    pause_before_ms: int = Field(default=0, ge=0, le=3000)
    pause_after_ms: int = Field(default=0, ge=0, le=3000)
    continuity_intent: str = Field(min_length=1)
    provider_control_intent: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_span(self) -> "NarrationPerformanceBeat":
        if self.source_text_end <= self.source_text_start:
            raise ValueError("PERFORMANCE_BEAT_SPAN_INVALID")
        return self


class NarrationPerformancePlanCreate(_StrictModel):
    video_project_id: uuid.UUID
    qualified_script_ref: str = Field(min_length=1)
    qualified_script_hash: str = Field(pattern=_SHA256)
    canonical_narration: str = Field(min_length=1)
    narration_voice_snapshot_id: uuid.UUID
    baseline_delivery: dict[str, Any]
    beats: list[NarrationPerformanceBeat] = Field(min_length=1)
    performance_policy_version: str = Field(min_length=1)
    created_by_user_id: uuid.UUID | None = None


class NarrationPerformancePlanRead(_StrictModel):
    id: uuid.UUID
    schema_version: str
    video_project_id: uuid.UUID
    qualified_script_ref: str
    qualified_script_hash: str = Field(pattern=_SHA256)
    canonical_narration_hash: str = Field(pattern=_SHA256)
    narration_voice_snapshot_id: uuid.UUID
    voice_snapshot_hash: str = Field(pattern=_SHA256)
    baseline_delivery: dict[str, Any]
    beats: list[NarrationPerformanceBeat]
    performance_policy_version: str
    coverage_gate_state: str
    semantic_alignment_gate_state: str
    continuity_gate_state: str
    monotony_risk_gate_state: str
    state: str
    content_hash: str = Field(pattern=_SHA256)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class TTSPerformanceSegment(_StrictModel):
    segment_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    beat_ids: list[str] = Field(min_length=1)
    source_text_start: int = Field(ge=0)
    source_text_end: int = Field(gt=0)
    text_hash: str = Field(pattern=_SHA256)
    voice_settings: dict[str, Any]
    previous_text: str | None = None
    next_text: str | None = None


class TTSPerformanceProjectionRead(_StrictModel):
    id: uuid.UUID
    schema_version: str
    video_project_id: uuid.UUID
    narration_performance_plan_id: uuid.UUID
    narration_voice_snapshot_id: uuid.UUID
    provider: Literal["elevenlabs"]
    model_id: str
    execution_strategy: TTSExecutionStrategy
    capability_profile_version: str
    segments: list[TTSPerformanceSegment]
    state: str
    content_hash: str = Field(pattern=_SHA256)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)
