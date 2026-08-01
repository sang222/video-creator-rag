from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import VEO_APPROVED_MODEL_IDS, VEO_FORBIDDEN_MODEL_IDS


VeoOperationStatus = Literal[
    "PLANNED",
    "APPROVED",
    "SUBMITTING",
    "SUBMITTED",
    "PROCESSING",
    "SUCCEEDED",
    "FAILED",
    "MODERATED",
    "TIMED_OUT",
    "CANCELLED",
    "OUTPUT_MISSING",
    "DOWNLOAD_FAILED",
    "CHECKSUM_FAILED",
    "QC_FAILED",
]


class AIHeroProviderPolicySnapshot(BaseModel):
    channel_id: str
    ai_video_hero_enabled: bool
    ai_video_provider: Literal["google_veo"]
    allowed_model_ids: list[str]
    default_model_id: str
    allowed_resolutions: list[str]
    max_ai_hero_clips_per_video: int = Field(ge=0)
    max_ai_hero_seconds_per_video: int = Field(ge=0)
    max_ai_hero_cost_per_video: Decimal = Field(ge=0)
    allowed_hero_reasons: list[str]
    provider_audio_policy: Literal["DISCARD"]
    unavailable_behavior: Literal["NATIVE_VISUAL_OR_REVIEW", "REVIEW_REQUIRED", "BLOCK"]
    frozen_at_project_creation: bool = True
    snapshot_hash: str
    model_config = ConfigDict(extra="forbid")


class GoogleVeoGenerationRequest(BaseModel):
    request_id: str
    generic_ai_hero_request_ref: str
    generic_ai_hero_request_hash: str
    project_id: str
    scene_id: str
    hero_reason: str
    model_id: str
    prompt: str
    prompt_hash: str
    duration_seconds: int
    resolution: str
    aspect_ratio: Literal["16:9"]
    output_count: int
    negative_prompt: str | None = None
    reference_image_refs: list[str] = Field(default_factory=list, max_length=3)
    first_frame_ref: str | None = None
    last_frame_ref: str | None = None
    character_policy_mode: str
    human_likeness_requested: bool = False
    generate_audio_expected: bool = True
    provider_audio_usage_policy: Literal["DISCARD"] = "DISCARD"
    synthetic_media_disclosure_required: bool = True
    cost_catalog_ref: str
    approval_ref: str
    approval_scope: str
    idempotency_key: str
    request_hash: str
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def approved_contract(self):
        if (
            self.model_id in VEO_FORBIDDEN_MODEL_IDS
            or self.model_id not in VEO_APPROVED_MODEL_IDS
        ):
            raise ValueError("VEO_MODEL_NOT_APPROVED")
        if self.duration_seconds != 8:
            raise ValueError("VEO_DURATION_NOT_APPROVED")
        allowed_resolutions = {
            "veo-3.1-generate-preview": {"720p", "1080p", "4k"},
            "veo-3.1-fast-generate-preview": {"720p", "1080p", "4k"},
            "veo-3.1-lite-generate-preview": {"720p", "1080p"},
        }
        if self.resolution not in allowed_resolutions[self.model_id]:
            raise ValueError("VEO_RESOLUTION_NOT_SUPPORTED")
        if self.output_count != 1:
            raise ValueError("VEO_PA1R_OUTPUT_COUNT_MUST_EQUAL_ONE")
        if (
            self.character_policy_mode != "NO_CHARACTER"
            or self.human_likeness_requested
        ):
            raise ValueError("VEO_NO_CHARACTER_POLICY_CONFLICT")
        if self.hero_reason not in {
            "HOOK",
            "METAPHOR",
            "EMOTIONAL_PAYOFF",
            "VISUAL_SIGNATURE",
            "NATIVE_MOTION_INSUFFICIENT",
        }:
            raise ValueError("VEO_HERO_REASON_NOT_APPROVED")
        if not all(
            (
                self.cost_catalog_ref,
                self.approval_ref,
                self.approval_scope,
                self.idempotency_key,
            )
        ):
            raise ValueError("VEO_APPROVAL_BUDGET_IDEMPOTENCY_REQUIRED")
        return self


class GoogleVeoExecutionGates(BaseModel):
    provider_boundary_gate_passed: bool
    human_paid_render_approval_passed: bool
    cost_estimate_snapshot_passed: bool
    channel_monthly_budget_gate_passed: bool
    paid_attempt_limit_gate_passed: bool
    provider_idempotency_key_valid: bool
    global_kill_switch_open: bool
    provider_kill_switch_open: bool
    approved_production_execution_scope: bool = False
    model_config = ConfigDict(extra="forbid")

    @property
    def all_passed(self) -> bool:
        return all(
            (
                self.provider_boundary_gate_passed,
                self.human_paid_render_approval_passed,
                self.cost_estimate_snapshot_passed,
                self.channel_monthly_budget_gate_passed,
                self.paid_attempt_limit_gate_passed,
                self.provider_idempotency_key_valid,
                self.global_kill_switch_open,
                self.provider_kill_switch_open,
            )
        )


class GoogleVeoOperationReceipt(BaseModel):
    internal_job_id: str
    provider_operation_id: str | None = None
    request_ref: str
    request_hash: str
    idempotency_key: str
    submit_attempt_no: int = Field(ge=0)
    provider_status: str
    normalized_status: VeoOperationStatus
    started_at: datetime | None = None
    last_polled_at: datetime | None = None
    completed_at: datetime | None = None
    provider_error_code: str | None = None
    provider_error_message_redacted: str | None = None
    output_reference: str | None = None
    provider_call_made: bool = False
    generation_attempts_consumed: int = Field(default=0, ge=0)
    production_eligible: bool = False
    state_hash: str
    model_config = ConfigDict(extra="forbid")


class GoogleVeoOutputDownloadPlan(BaseModel):
    operation_ref: str
    volatile_output_reference: str
    destination_path: str
    raw_url_persisted: bool = False
    execution_allowed: bool = False
    plan_hash: str
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def durable_reference_is_safe(self):
        if self.raw_url_persisted or self.volatile_output_reference.startswith(
            ("http://", "https://")
        ):
            raise ValueError("VEO_RAW_OUTPUT_URL_FORBIDDEN")
        return self


class GoogleVeoCostEstimateSnapshot(BaseModel):
    price_catalog_version: str
    price_catalog_ref: str
    provider_key: Literal["google_veo"] = "google_veo"
    model_id: str
    resolution: str
    duration_seconds: int
    output_count: int
    currency: Literal["USD"] = "USD"
    price_per_second: Decimal
    estimated_amount: Decimal
    hard_cap: Decimal
    approval_amount: Decimal
    actual_amount: Decimal | None = None
    variance_reason: str | None = None
    snapshot_hash: str
    model_config = ConfigDict(extra="forbid")


class GoogleVeoProvenanceManifest(BaseModel):
    provider: Literal["GOOGLE_VEO"] = "GOOGLE_VEO"
    gemini_project_reference: str
    model_id: str
    operation_id: str
    prompt_hash: str
    reference_asset_hashes: list[str]
    generated_at: datetime
    output_reference: str
    downloaded_file_path: str
    size_bytes: int = Field(gt=0)
    sha256: str
    provider_audio_present: bool
    provider_audio_stream_metadata: dict
    provider_audio_discarded: bool
    generation_cost_ref: str
    human_approval_ref: str
    media_qc_ref: str | None = None
    used_by_segments: list[str]
    synthetic_media_disclosure_required: bool = True
    production_eligible: bool = False
    manifest_hash: str
    model_config = ConfigDict(extra="forbid")


class ProviderAudioNormalizationReceipt(BaseModel):
    provider_audio_present: bool
    provider_audio_stream_count: int = Field(default=0, ge=0)
    provider_audio_stream_metadata: dict
    provider_audio_usage_policy: Literal["DISCARD"] = "DISCARD"
    provider_audio_discarded: bool
    narration_authority: Literal["ELEVENLABS"] = "ELEVENLABS"
    final_mix_authority: Literal["NATIVE_FFMPEG"] = "NATIVE_FFMPEG"
    normalized_contains_audio_stream: bool = False
    media_qc_status: Literal["PASS", "FAIL"]
    receipt_hash: str
    model_config = ConfigDict(extra="forbid")


class AIHeroUnavailableDecision(BaseModel):
    original_ai_hero_intent_ref: str
    unavailable_reason: str
    frozen_policy_behavior: Literal[
        "NATIVE_VISUAL_OR_REVIEW", "REVIEW_REQUIRED", "BLOCK"
    ]
    decision: Literal["NATIVE_VISUAL_REQUIRED", "REVIEW_REQUIRED", "BLOCK"]
    human_review_required: bool
    resulting_source_role: Literal["NATIVE_VISUAL", "AI_HERO_UNRESOLVED"]
    cost_avoided_usd: Decimal
    external_provider_attempted: bool = False
    external_provider_fallback_used: bool = False
    decision_hash: str
    model_config = ConfigDict(extra="forbid")
