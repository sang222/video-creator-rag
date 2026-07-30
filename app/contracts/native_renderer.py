from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.vcos_v2 import DurationContractV2
from app.contracts.visual_routing import (
    ExactTextNativeOverlayContract,
    VisualSourceRoute,
)


PlanStatus = Literal[
    "DRAFT",
    "VALIDATED",
    "REVIEW_REQUIRED",
    "BLOCKED",
    "APPROVED",
    "COMPILED",
    "SUPERSEDED",
]
GateVerdict = Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]


class CanvasSpec(BaseModel):
    width: int = Field(ge=320, le=1920)
    height: int = Field(ge=320, le=1920)
    fps: int = Field(default=30, ge=24, le=30)
    model_config = ConfigDict(extra="forbid")


class AssetRequirement(BaseModel):
    key: str
    kind: str = "LOCAL_FILE"
    required: bool = True
    model_config = ConfigDict(extra="forbid")


class ResolvedAssetRef(BaseModel):
    key: str
    path: str
    checksum: str | None = None
    model_config = ConfigDict(extra="forbid")


class TextSafeRegion(BaseModel):
    id: str = Field(min_length=1)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)
    coordinate_space: Literal["normalized"] = "normalized"
    purpose: str = Field(min_length=1)
    minimum_contrast_requirement: float = Field(ge=1.0, le=21.0)
    alignment: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_normalized_bounds(self) -> "TextSafeRegion":
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("VSR1_TEXT_SAFE_REGION_OUT_OF_BOUNDS")
        if not self.purpose.strip() or not self.alignment.strip():
            raise ValueError("VSR1_TEXT_SAFE_REGION_METADATA_MISSING")
        return self


class NativeOverlayPlan(BaseModel):
    plan_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    source_decision_ref: str = Field(min_length=1)
    source_decision_hash: str = Field(min_length=1)
    preferred_source_route: VisualSourceRoute
    exact_text_contract: ExactTextNativeOverlayContract
    text_safe_regions: list[TextSafeRegion]
    reserved_overlay_regions: list[TextSafeRegion]
    overlay_content_refs: list[str] = Field(min_length=1)
    native_overlay_required: Literal[True]
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_route_aware_overlay(self) -> "NativeOverlayPlan":
        region_ids = [
            region.id
            for region in self.text_safe_regions + self.reserved_overlay_regions
        ]
        if len(region_ids) != len(set(region_ids)):
            raise ValueError("VSR1_DUPLICATE_OVERLAY_REGION_ID")
        if len(self.overlay_content_refs) != len(set(self.overlay_content_refs)) or any(
            not ref.strip() for ref in self.overlay_content_refs
        ):
            raise ValueError("VSR1_OVERLAY_CONTENT_REF_INVALID")
        exact = self.exact_text_contract
        if exact.scene_id != self.scene_id:
            raise ValueError("VSR1_OVERLAY_SCENE_MISMATCH")
        if exact.source_decision_ref != self.source_decision_ref:
            raise ValueError("VSR1_OVERLAY_DECISION_REF_MISMATCH")
        if exact.source_decision_hash != self.source_decision_hash:
            raise ValueError("VSR1_OVERLAY_DECISION_HASH_MISMATCH")
        if exact.preferred_source_route != self.preferred_source_route:
            raise ValueError("VSR1_OVERLAY_ROUTE_MISMATCH")
        if self.overlay_content_refs != exact.authoritative_content_refs:
            raise ValueError("VSR1_OVERLAY_AUTHORITATIVE_CONTENT_BINDING_MISMATCH")
        if not exact.native_overlay_required:
            raise ValueError("VSR1_NATIVE_OVERLAY_PLAN_WITHOUT_NATIVE_AUTHORITY")
        if (
            exact.exact_text_required or exact.exact_number_required
        ) and not self.text_safe_regions:
            raise ValueError("VSR1_EXACT_CONTENT_TEXT_SAFE_REGION_REQUIRED")
        return self


class NativeRenderScene(BaseModel):
    scene_id: str
    source_segment_ids: list[str]
    narration_start_ms: int = Field(ge=0)
    narration_end_ms: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    visual_treatment: Literal[
        "NATIVE_SLIDE",
        "DIAGRAM",
        "UI_SIMULATION",
        "KINETIC_TYPOGRAPHY",
        "DATA_CARD",
        "QUOTE_SLIDE",
        "COMPARISON_SLIDE",
        "TIMELINE",
        "STATIC_COMPOSITION",
        "STOCK_VIDEO",
        "AI_HERO_VIDEO",
    ]
    layout_type: str
    asset_requirements: list[AssetRequirement] = Field(default_factory=list)
    resolved_asset_refs: list[ResolvedAssetRef] = Field(default_factory=list)
    animation_type: str | None = None
    transition_in: str | None = None
    transition_out: str | None = None
    emphasis_targets: list[str] = Field(default_factory=list)
    caption_behavior: str = "BURN_IN"
    safe_area_policy: str = "DEFAULT"
    originality_role: str
    provider_intent: str | None = None
    scene_notes: str = ""
    scene_hash: str = ""
    visual_routing_mode: Literal["VSR1_STRICT"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    source_decision_ref: str | None = Field(
        default=None, min_length=1, exclude_if=lambda value: value is None
    )
    source_decision_hash: str | None = Field(
        default=None, min_length=1, exclude_if=lambda value: value is None
    )
    preferred_source_route: VisualSourceRoute | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    exact_text_required: bool | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    exact_number_required: bool | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    forbidden_generated_text: bool | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    forbidden_generated_logo: bool | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    forbidden_generated_fake_ui: bool | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    text_safe_regions: list[TextSafeRegion] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    reserved_overlay_regions: list[TextSafeRegion] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    eligibility_gate_refs: list[str] | None = Field(
        default=None,
        min_length=1,
        exclude_if=lambda value: value is None,
    )
    native_overlay_required: bool | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    native_overlay_plan: NativeOverlayPlan | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def timing_matches(self):
        if self.narration_end_ms - self.narration_start_ms != self.duration_ms:
            raise ValueError("SCENE_DURATION_MISMATCH")
        route_aware_values = (
            self.source_decision_ref,
            self.source_decision_hash,
            self.preferred_source_route,
            self.exact_text_required,
            self.exact_number_required,
            self.forbidden_generated_text,
            self.forbidden_generated_logo,
            self.forbidden_generated_fake_ui,
            self.text_safe_regions,
            self.reserved_overlay_regions,
            self.eligibility_gate_refs,
            self.native_overlay_required,
            self.native_overlay_plan,
        )
        if self.visual_routing_mode is None and all(
            value is None for value in route_aware_values
        ):
            return self
        if self.visual_routing_mode != "VSR1_STRICT":
            raise ValueError("VSR1_STRICT_ROUTING_MODE_REQUIRED")
        if any(value is None for value in route_aware_values[:-1]):
            raise ValueError("VSR1_ROUTE_AWARE_NATIVE_FIELDS_INCOMPLETE")
        if (
            not self.source_decision_ref.strip()
            or not self.source_decision_hash.strip()
        ):
            raise ValueError("VSR1_SOURCE_DECISION_BINDING_EMPTY")
        if len(self.eligibility_gate_refs) != len(
            set(self.eligibility_gate_refs)
        ) or any(not ref.strip() for ref in self.eligibility_gate_refs):
            raise ValueError("VSR1_ELIGIBILITY_GATE_REF_INVALID")
        if not all(
            (
                self.forbidden_generated_text,
                self.forbidden_generated_logo,
                self.forbidden_generated_fake_ui,
            )
        ):
            raise ValueError("VSR1_GENERATED_TEXT_LOGO_FAKE_UI_MUST_BE_FORBIDDEN")
        if (
            self.exact_text_required or self.exact_number_required
        ) and not self.native_overlay_required:
            raise ValueError("VSR1_EXACT_CONTENT_REQUIRES_NATIVE_OVERLAY")
        if self.native_overlay_required and self.native_overlay_plan is None:
            raise ValueError("VSR1_NATIVE_OVERLAY_PLAN_REQUIRED")
        if not self.native_overlay_required and self.native_overlay_plan is not None:
            raise ValueError("VSR1_UNEXPECTED_NATIVE_OVERLAY_PLAN")
        if self.native_overlay_plan is not None:
            overlay = self.native_overlay_plan
            exact = overlay.exact_text_contract
            if overlay.scene_id != self.scene_id:
                raise ValueError("VSR1_RENDER_SCENE_OVERLAY_SCENE_MISMATCH")
            if overlay.source_decision_ref != self.source_decision_ref:
                raise ValueError("VSR1_RENDER_SCENE_OVERLAY_DECISION_REF_MISMATCH")
            if overlay.source_decision_hash != self.source_decision_hash:
                raise ValueError("VSR1_RENDER_SCENE_OVERLAY_DECISION_HASH_MISMATCH")
            if overlay.preferred_source_route != self.preferred_source_route:
                raise ValueError("VSR1_RENDER_SCENE_OVERLAY_ROUTE_MISMATCH")
            if exact.exact_text_required != self.exact_text_required:
                raise ValueError("VSR1_RENDER_SCENE_EXACT_TEXT_MISMATCH")
            if exact.exact_number_required != self.exact_number_required:
                raise ValueError("VSR1_RENDER_SCENE_EXACT_NUMBER_MISMATCH")
            if overlay.text_safe_regions != self.text_safe_regions:
                raise ValueError("VSR1_RENDER_SCENE_TEXT_SAFE_REGION_MISMATCH")
            if overlay.reserved_overlay_regions != self.reserved_overlay_regions:
                raise ValueError("VSR1_RENDER_SCENE_RESERVED_REGION_MISMATCH")
        return self


class NativeRenderPlan(BaseModel):
    plan_id: str
    plan_version: int = Field(ge=1)
    package_id: str
    production_package_schema_version: Literal["v2"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    production_package_artifact_version_id: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    production_package_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )
    duration_contract: DurationContractV2 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    video_project_id: str
    company_id: str
    channel_id: str
    channel_profile_version_id: str
    effective_context_snapshot_id: str
    effective_context_hash: str
    format_identity_contract_ref: str
    format_identity_contract_hash: str
    format_identity_status: str = "APPROVED"
    episode_originality_manifest_ref: str
    episode_originality_manifest_hash: str
    final_originality_gate: str = "PASS"
    claim_evidence_ledger_refs: list[str] = Field(default_factory=list)
    synthetic_media_disclosure_receipt_ref: str | None = None
    script_ref: str
    script_hash: str
    srt_ref: str
    srt_hash: str
    audio_timeline_ref: str | None = None
    temporal_authority_mode: Literal["LEGACY_HISTORICAL", "CANONICAL_STRICT"] = (
        "LEGACY_HISTORICAL"
    )
    canonical_media_timeline_ref: str | None = None
    canonical_media_timeline_hash: str | None = None
    canonical_audio_asset_ref: str | None = None
    canonical_caption_compilation_ref: str | None = None
    canonical_caption_compilation_hash: str | None = None
    canonical_caption_render_payload_hash: str | None = None
    scene_timing_source: str | None = None
    caption_timing_source: str | None = None
    parallel_timing_inputs: list[str] = Field(default_factory=list)
    visual_plan_ref: str
    visual_plan_hash: str
    visual_direction_contract_ref: str | None = None
    visual_direction_contract_hash: str | None = None
    creative_gate_results: dict[str, Any] = Field(default_factory=dict)
    canvas_spec: CanvasSpec
    scenes: list[NativeRenderScene]
    global_motion_policy: dict[str, Any] = Field(default_factory=dict)
    caption_policy: dict[str, Any] = Field(default_factory=dict)
    audio_policy: dict[str, Any] = Field(default_factory=dict)
    output_profiles: list[str]
    character_policy_mode: str = "NO_CHARACTER"
    purpose: str = "PRODUCTION"
    production_eligible: bool = True
    status: PlanStatus = "DRAFT"
    content_hash: str = ""
    created_at: datetime
    created_by: str
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_vsr1_scene_set(self) -> "NativeRenderPlan":
        strict_scenes = [
            scene for scene in self.scenes if scene.visual_routing_mode == "VSR1_STRICT"
        ]
        if not strict_scenes:
            return self
        if len(strict_scenes) != len(self.scenes):
            raise ValueError("VSR1_STRICT_AND_LEGACY_SCENE_MIX_PROHIBITED")
        if min(self.canvas_spec.width, self.canvas_spec.height) < 1080:
            raise ValueError("VSR1_OUTPUT_RESOLUTION_BELOW_1080P")
        return self

    @model_validator(mode="after")
    def validate_v2_package_duration_authority(self) -> "NativeRenderPlan":
        if self.production_package_schema_version != "v2":
            return self
        if (
            not self.production_package_artifact_version_id
            or not self.production_package_hash
            or self.duration_contract is None
            or self.package_id != self.production_package_artifact_version_id
        ):
            raise ValueError("NATIVE_RENDER_V2_PACKAGE_AUTHORITY_REQUIRED")
        duration_ms = max(
            (scene.narration_end_ms for scene in self.scenes),
            default=0,
        )
        if not (
            self.duration_contract.minimum_duration_ms
            <= duration_ms
            <= self.duration_contract.maximum_duration_ms
        ):
            raise ValueError("NATIVE_RENDER_DURATION_OUTSIDE_CHANNEL_CONTRACT")
        return self


class GateResult(BaseModel):
    gate: str
    verdict: GateVerdict
    reason_codes: list[str] = Field(default_factory=list)


class CompiledNativeRenderManifest(BaseModel):
    compiled_manifest_id: str
    source_plan_ref: str
    source_plan_hash: str
    compiler_version: str
    motion_pack_version: str
    renderer_profile_refs: list[str]
    ffmpeg_binary_requirement: str
    ffmpeg_capability_digest: str
    normalized_canvas: dict[str, Any]
    normalized_audio: dict[str, Any]
    normalized_caption: dict[str, Any]
    compiled_scenes: list[dict[str, Any]]
    transition_schedule: list[dict[str, Any]]
    overlay_schedule: list[dict[str, Any]]
    audio_mix_schedule: dict[str, Any]
    caption_schedule: dict[str, Any]
    output_specs: list[dict[str, Any]]
    expected_input_refs: list[str]
    unresolved_inputs: list[str]
    compilation_warnings: list[str]
    compilation_reason_codes: list[str]
    production_eligible: bool
    temporal_authority_mode: Literal["LEGACY_HISTORICAL", "CANONICAL_STRICT"] = (
        "LEGACY_HISTORICAL"
    )
    canonical_media_timeline_ref: str | None = None
    canonical_media_timeline_hash: str | None = None
    canonical_audio_asset_ref: str | None = None
    canonical_duration_ms: int | None = Field(default=None, gt=0)
    canonical_caption_compilation_ref: str | None = None
    canonical_caption_compilation_hash: str | None = None
    canonical_caption_render_payload_hash: str | None = None
    visual_direction_contract_ref: str | None = None
    visual_direction_contract_hash: str | None = None
    creative_gate_results: dict[str, Any] = Field(default_factory=dict)
    render_purpose: str = "PRODUCTION"
    manifest_hash: str
    created_at: datetime


class FFmpegCommandManifest(BaseModel):
    run_key: str
    compiled_manifest_ref: str
    compiled_manifest_hash: str
    ffmpeg_binary_path: str
    ffprobe_binary_path: str
    ffmpeg_version: str
    command_builder_version: str
    input_files: list[str]
    generated_filtergraph_path: str
    generated_text_files: list[str]
    generated_caption_path: str | None
    generated_file_checksums: dict[str, str] = Field(default_factory=dict)
    output_file: str
    output_profile: str
    sanitized_argv: list[str]
    working_directory: str
    expected_qc: dict[str, Any]
    temporal_authority_mode: Literal["LEGACY_HISTORICAL", "CANONICAL_STRICT"] = (
        "LEGACY_HISTORICAL"
    )
    canonical_media_timeline_ref: str | None = None
    canonical_media_timeline_hash: str | None = None
    canonical_audio_asset_ref: str | None = None
    canonical_duration_ms: int | None = Field(default=None, gt=0)
    canonical_caption_compilation_ref: str | None = None
    canonical_caption_compilation_hash: str | None = None
    canonical_caption_render_payload_hash: str | None = None
    command_hash: str
    created_at: datetime


class V2ProductionRenderExecutionEnvelope(BaseModel):
    """Package/budget-bound authorization for the non-MR1 V2 renderer."""

    envelope_version: Literal["vcos.v2-native-render-envelope.v1"] = (
        "vcos.v2-native-render-envelope.v1"
    )
    workflow_run_id: uuid.UUID
    command_id: str = Field(min_length=1, max_length=160)
    render_run_key: str = Field(min_length=1, max_length=160)
    production_package_artifact_version_id: uuid.UUID
    production_package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_execution_plan_ref: str = Field(min_length=1)
    provider_execution_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    budget_scope_ref: str = Field(min_length=1)
    budget_scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: str = Field(min_length=1, max_length=160)
    adapter_key: Literal["v2-local-native"] = "v2-local-native"
    plan_ref: str = Field(min_length=1)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_eligible: Literal[True] = True
    paid_provider_call: Literal[False] = False
    authorization_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)


class MediaQCReport(BaseModel):
    run_key: str
    result: Literal["PASS", "WARN", "FAIL"]
    checks: dict[str, Any]
    reason_codes: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    created_at: datetime


class NativeRenderExecutionReceipt(BaseModel):
    run_key: str
    manifest_refs: dict[str, str]
    command_hash: str
    start_time: datetime
    end_time: datetime
    exit_code: int
    elapsed_time: float
    realtime_factor: float | None
    peak_rss: int | None
    output_path: str
    output_checksum: str
    local_only: bool = True
    production_eligible: bool
    no_provider_calls_confirmed: bool = True
    receipt_hash: str


class HumanReviewReceipt(BaseModel):
    render_receipt_ref: str
    reviewer: str
    decision: Literal["PASS", "FAIL", "REQUEST_CHANGES"]
    checklist_results: dict[str, bool]
    notes: str = ""
    decided_at: datetime


class ArchiveReceipt(BaseModel):
    production_archive_manifest_ref: str
    drive_file_refs: list[str] = Field(default_factory=list)
    size_checksum_verification: dict[str, Any] = Field(default_factory=dict)
    archive_state: str = "DESIGN_ONLY"
    verified_at: datetime | None = None
