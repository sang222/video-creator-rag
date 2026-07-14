from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PlanStatus = Literal["DRAFT", "VALIDATED", "REVIEW_REQUIRED", "BLOCKED", "APPROVED", "COMPILED", "SUPERSEDED"]
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


class NativeRenderScene(BaseModel):
    scene_id: str
    source_segment_ids: list[str]
    narration_start_ms: int = Field(ge=0)
    narration_end_ms: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    visual_treatment: Literal["NATIVE_SLIDE", "DIAGRAM", "UI_SIMULATION", "KINETIC_TYPOGRAPHY", "DATA_CARD", "QUOTE_SLIDE", "COMPARISON_SLIDE", "TIMELINE", "STATIC_COMPOSITION", "STOCK_VIDEO", "AI_HERO_VIDEO"]
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
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def timing_matches(self):
        if self.narration_end_ms - self.narration_start_ms != self.duration_ms:
            raise ValueError("SCENE_DURATION_MISMATCH")
        return self


class NativeRenderPlan(BaseModel):
    plan_id: str
    plan_version: int = Field(ge=1)
    package_id: str
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
    temporal_authority_mode: Literal["LEGACY_HISTORICAL", "CANONICAL_STRICT"] = "LEGACY_HISTORICAL"
    canonical_media_timeline_ref: str | None = None
    canonical_media_timeline_hash: str | None = None
    canonical_audio_asset_ref: str | None = None
    scene_timing_source: str | None = None
    caption_timing_source: str | None = None
    parallel_timing_inputs: list[str] = Field(default_factory=list)
    visual_plan_ref: str
    visual_plan_hash: str
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
    temporal_authority_mode: Literal["LEGACY_HISTORICAL", "CANONICAL_STRICT"] = "LEGACY_HISTORICAL"
    canonical_media_timeline_ref: str | None = None
    canonical_media_timeline_hash: str | None = None
    canonical_audio_asset_ref: str | None = None
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
    output_file: str
    output_profile: str
    sanitized_argv: list[str]
    working_directory: str
    expected_qc: dict[str, Any]
    temporal_authority_mode: Literal["LEGACY_HISTORICAL", "CANONICAL_STRICT"] = "LEGACY_HISTORICAL"
    canonical_media_timeline_ref: str | None = None
    canonical_media_timeline_hash: str | None = None
    canonical_audio_asset_ref: str | None = None
    command_hash: str
    created_at: datetime


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
