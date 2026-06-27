import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


ProductionRunMode = Literal["REAL", "REAL_DISABLED", "NOT_CONFIGURED", "HUMAN_REVIEW_ONLY", "BLOCKED"]
ProductionRunStatus = Literal["PENDING", "RUNNING", "COMPLETED", "REVIEW_REQUIRED", "BLOCKED", "FAILED", "CANCELLED"]
TimingSource = Literal["ESTIMATED", "LOCAL_AUDIO_ANALYSIS"]
ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
CaptionFormat = Literal["SRT", "VTT", "JSON"]
PreferredSource = Literal[
    "APPROVED_ASSET_POOL",
    "STOCK_PLACEHOLDER",
    "DIAGRAM_PLACEHOLDER",
    "SCREENSHOT_PLACEHOLDER",
    "AI_PLACEHOLDER",
    "MANUAL_ENVATO_PLACEHOLDER",
    "MANUAL_PREMIUM_PLACEHOLDER",
]
SceneType = Literal[
    "GENERIC_BROLL",
    "HERO_SHOT",
    "MECHANISM",
    "PROCESS",
    "DATA",
    "UI_SCREENSHOT",
    "BRANDED_TEMPLATE",
    "PREMIUM_MANUAL",
    "PLACEHOLDER",
]
ImportanceLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
ProviderSourceClass = Literal[
    "API_NATIVE_PROVIDER",
    "MANUAL_ASSET_LIBRARY",
    "BATCH_PROCUREMENT_SOURCE",
    "LOCAL_RENDERER",
    "PREMIUM_FALLBACK_PROVIDER",
    "APPROVED_ASSET_POOL",
]
RequiredMediaType = Literal["IMAGE", "VIDEO", "AUDIO", "DIAGRAM", "SCREENSHOT", "GENERATED_PLACEHOLDER"]
LicenseRequirement = Literal["COMMERCIAL_SAFE", "INTERNAL_TEST_ONLY", "LICENSE_REQUIRED", "UNKNOWN"]
RequirementStatus = Literal["SATISFIED", "FALLBACK_USED", "WAITING_FOR_ASSET", "BLOCKED", "NOT_REQUIRED"]
AssetCandidateSourceType = Literal["MANUAL_PLACEHOLDER", "MANUAL_ENVATO_PLACEHOLDER", "EXTERNAL_DISABLED"]
LicenseState = Literal["INTERNAL_TEST_ONLY", "LICENSE_REQUIRED", "LICENSE_VERIFIED", "UNKNOWN"]
Platform = Literal["YOUTUBE", "YOUTUBE_SHORTS", "TIKTOK", "FACEBOOK", "INSTAGRAM", "GENERIC"]
Surface = Literal["LONG_FORM", "SHORT_FORM", "REELS", "FEED", "STORY", "GENERIC"]
AspectRatio = Literal["16:9", "9:16", "1:1", "4:5", "4:3", "CUSTOM"]
CropStrategy = Literal["CENTER_CROP", "FIT_WITH_BLUR", "SMART_CROP_PLACEHOLDER", "LETTERBOX", "PILLARBOX", "CUSTOM"]
VariantStatus = Literal["PLANNED", "READY", "BLOCKED", "RENDERED", "QC_PASSED", "QC_REVIEW_REQUIRED"]
RenderIntent = Literal["LOCAL_SMOKE", "DRAFT_PREVIEW", "PRODUCTION_DISABLED"]
RenderSpecValidationState = Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]
RendererKey = Literal["REAL_DISABLED"]
MediaRenderJobStatus = Literal["PENDING", "RUNNING", "COMPLETED", "REVIEW_REQUIRED", "BLOCKED", "FAILED"]
RenderPackageState = Literal["CREATED", "QC_PASSED", "QC_REVIEW_REQUIRED", "QC_BLOCKED", "FAILED"]
QCState = Literal["PASS", "REVIEW_REQUIRED", "BLOCK", "FAILED"]
PronunciationStatus = Literal["ACTIVE", "DISABLED", "NEEDS_REVIEW"]


class ScriptSection(BaseModel):
    section_id: str = Field(min_length=1)
    heading: str = Field(min_length=1)
    text: str = Field(min_length=1)
    sequence_index: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class NarrationSegmentContract(BaseModel):
    narration_segment_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    sequence_index: int = Field(ge=0)
    estimated_start_time: float = Field(ge=0)
    estimated_end_time: float = Field(gt=0)
    estimated_duration_seconds: float = Field(gt=0)
    source_artifact_version_id: uuid.UUID | None = None
    pronunciation_hints: dict[str, Any] | list[Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_timing(self) -> "NarrationSegmentContract":
        if self.estimated_end_time <= self.estimated_start_time:
            raise ValueError("narration segment end_time must be greater than start_time")
        expected = round(self.estimated_end_time - self.estimated_start_time, 3)
        if abs(expected - self.estimated_duration_seconds) > 0.05:
            raise ValueError("narration segment duration must match start/end")
        return self


class ScriptDraftContract(BaseModel):
    script_id: str = Field(min_length=1)
    video_project_id: uuid.UUID
    title: str = Field(min_length=1)
    sections: list[ScriptSection] = Field(min_length=1)
    narration_segments: list[NarrationSegmentContract] = Field(min_length=1)
    source_artifact_version_ids: list[uuid.UUID] = Field(default_factory=list)
    llm_run_snapshot_id: uuid.UUID | None = None
    script_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_sequence(self) -> "ScriptDraftContract":
        _validate_ordered_segments(self.narration_segments)
        return self


class VoiceTimelineContract(BaseModel):
    total_duration_seconds: float = Field(gt=0)
    segments: list[NarrationSegmentContract] = Field(min_length=1)
    timing_source: TimingSource
    timeline_hash: str = Field(min_length=1)
    confidence_level: ConfidenceLevel

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_voice_timeline(self) -> "VoiceTimelineContract":
        _validate_ordered_segments(self.segments)
        if abs(self.total_duration_seconds - self.segments[-1].estimated_end_time) > 0.05:
            raise ValueError("voice timeline total duration must match final segment end")
        return self


class CaptionCueContract(BaseModel):
    caption_id: str = Field(min_length=1)
    narration_segment_id: str = Field(min_length=1)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    text: str = Field(min_length=1)
    line_count: int | None = Field(default=None, ge=1)
    char_count: int | None = Field(default=None, ge=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_timing(self) -> "CaptionCueContract":
        if self.end_time <= self.start_time:
            raise ValueError("caption cue end_time must be greater than start_time")
        return self


class CaptionTrackContract(BaseModel):
    cues: list[CaptionCueContract] = Field(min_length=1)
    format: CaptionFormat = "SRT"
    language: str | None = None
    srt_text: str | None = None
    caption_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_cues(self) -> "CaptionTrackContract":
        ids = set()
        last_end = -1.0
        for cue in self.cues:
            if cue.caption_id in ids:
                raise ValueError("caption ids must be unique")
            ids.add(cue.caption_id)
            if cue.start_time < last_end - 0.05:
                raise ValueError("caption cues must not overlap")
            last_end = cue.end_time
        if self.format == "SRT" and not self.srt_text:
            raise ValueError("SRT caption tracks require srt_text")
        return self


class SceneSpecContract(BaseModel):
    scene_id: str = Field(min_length=1)
    sequence_index: int = Field(ge=0)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    narration_segment_id: str = Field(min_length=1)
    caption_ids: list[str] = Field(default_factory=list)
    narration_summary: str = Field(min_length=1)
    visual_intent: str = Field(min_length=1)
    preferred_source: PreferredSource
    asset_requirements: list[dict[str, Any]] = Field(default_factory=list)
    overlay_text: str | None = None
    fallback_visual: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_scene_timing(self) -> "SceneSpecContract":
        if self.end_time <= self.start_time:
            raise ValueError("scene end_time must be greater than start_time")
        return self


class SceneSourceDecisionContract(BaseModel):
    scene_id: str
    source_class: ProviderSourceClass
    preferred_source: PreferredSource
    fallback_order: list[PreferredSource] = Field(min_length=1)
    procurement_required: bool
    rights_review_required: bool
    requires_ai_disclosure_check: bool
    max_cost_usd: float | None = Field(default=None, ge=0)
    reason_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class SceneManifestSceneContract(SceneSpecContract):
    scene_type: SceneType
    importance: ImportanceLevel
    specificity: RiskLevel
    factual_risk: RiskLevel
    need_realism: RiskLevel
    expected_viewer_impact: RiskLevel
    deadline_sensitivity: RiskLevel
    max_cost_usd: float | None = Field(default=None, ge=0)
    preferred_source_order: list[PreferredSource] = Field(min_length=1)
    fallback_order: list[PreferredSource] = Field(min_length=1)
    requires_ai_disclosure_check: bool = False
    requires_rights_review: bool = False
    procurement_required: bool = False
    procurement_priority: str | None = None
    source_decision: SceneSourceDecisionContract


class VisualPlanContract(BaseModel):
    scenes: list[SceneSpecContract] = Field(min_length=1)
    total_duration_seconds: float = Field(gt=0)
    source_voice_timeline_snapshot_id: uuid.UUID
    visual_plan_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_scenes(self) -> "VisualPlanContract":
        _validate_ordered_scenes(self.scenes, self.total_duration_seconds)
        return self


class SceneManifestContract(BaseModel):
    scenes: list[SceneManifestSceneContract] = Field(min_length=1)
    visual_plan_snapshot_id: uuid.UUID
    scene_manifest_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_scenes(self) -> "SceneManifestContract":
        _validate_ordered_scenes(self.scenes, self.scenes[-1].end_time)
        return self


class RightsEnvelopeContract(BaseModel):
    license_state: LicenseState
    commercial_use_allowed: bool | None = None
    attribution_required: bool | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class AssetRequirementContract(BaseModel):
    requirement_id: str
    scene_id: str
    required_media_type: RequiredMediaType
    source_class: ProviderSourceClass
    search_keywords: list[str] | None = None
    visual_description: str
    license_requirement: LicenseRequirement
    required_evidence: list[str] = Field(default_factory=list)
    fallback_allowed: bool
    procurement_required: bool
    requirement_status: RequirementStatus

    model_config = ConfigDict(extra="forbid")


class AssetCandidateContract(BaseModel):
    asset_ref: str
    requirement_id: str
    source_type: AssetCandidateSourceType
    file_path: str | None = None
    mime_type: str | None = None
    checksum: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    rights_envelope: RightsEnvelopeContract
    provenance_blob: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class AssetManifestContract(BaseModel):
    requirements: list[AssetRequirementContract] = Field(default_factory=list)
    candidates: list[AssetCandidateContract] = Field(default_factory=list)
    manifest_hash: str

    model_config = ConfigDict(extra="forbid")


class SourceManifestContract(BaseModel):
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    asset_refs: list[str] = Field(default_factory=list)
    generated_by: str
    provider_classification_summary: dict[str, Any]
    procurement_queue_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    manifest_hash: str

    model_config = ConfigDict(extra="forbid")


class SafeAreaProfileContract(BaseModel):
    profile_key: str = "generic_safe_area"
    top_pct: float = Field(default=0.08, ge=0, le=0.5)
    bottom_pct: float = Field(default=0.12, ge=0, le=0.5)
    left_pct: float = Field(default=0.06, ge=0, le=0.5)
    right_pct: float = Field(default=0.06, ge=0, le=0.5)

    model_config = ConfigDict(extra="forbid")


class CaptionPlacementProfileContract(BaseModel):
    placement_key: str = "lower_third"
    vertical_anchor: Literal["TOP", "MIDDLE", "BOTTOM"] = "BOTTOM"
    max_lines: int = Field(default=2, ge=1, le=4)
    safe_area_aware: bool = True

    model_config = ConfigDict(extra="forbid")


class ExportProfileContract(BaseModel):
    aspect_ratio: AspectRatio
    resolution_width: int = Field(gt=0)
    resolution_height: int = Field(gt=0)
    fps: int = Field(gt=0, le=120)
    container: Literal["mp4"] = "mp4"
    codec: str | None = "h264"

    model_config = ConfigDict(extra="forbid")


class RenderVariantSpec(BaseModel):
    variant_id: str
    destination_binding_id: uuid.UUID | None = None
    platform: Platform
    surface: Surface
    aspect_ratio: AspectRatio
    resolution_width: int = Field(gt=0)
    resolution_height: int = Field(gt=0)
    fps: int = Field(gt=0, le=120)
    crop_strategy: CropStrategy
    caption_placement: CaptionPlacementProfileContract
    safe_area_profile: SafeAreaProfileContract
    overlay_scale: float = Field(default=1.0, gt=0, le=4)
    export_filename: str = Field(min_length=1)
    variant_status: VariantStatus

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_ratio(self) -> "RenderVariantSpec":
        expected = {
            "16:9": 16 / 9,
            "9:16": 9 / 16,
            "1:1": 1.0,
            "4:5": 4 / 5,
            "4:3": 4 / 3,
        }
        if self.aspect_ratio in expected:
            actual = self.resolution_width / self.resolution_height
            if abs(actual - expected[self.aspect_ratio]) > 0.02:
                raise ValueError("render variant resolution does not match aspect_ratio")
        return self


class LayerSpec(BaseModel):
    layer_id: str
    layer_type: Literal["VIDEO", "IMAGE", "TEXT", "CAPTION", "AUDIO_PLACEHOLDER"]
    asset_ref: str | None = None
    text: str | None = None
    z_index: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class RenderSceneSpec(BaseModel):
    scene_id: str
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    narration_segment_id: str
    visual_asset_ref: str | None = None
    layer_specs: list[LayerSpec] = Field(default_factory=list)
    overlay_text: str | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_scene(self) -> "RenderSceneSpec":
        if self.end_time <= self.start_time:
            raise ValueError("render scene end_time must be greater than start_time")
        if self.visual_asset_ref is None and not any(layer.asset_ref for layer in self.layer_specs):
            raise ValueError("render scene requires an asset ref or placeholder layer")
        return self


class AudioTrackSpec(BaseModel):
    track_id: str
    track_type: Literal["SILENT", "TEST_TONE"]
    source_ref: str | None = None
    duration_seconds: float = Field(gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class CaptionStyleSpec(BaseModel):
    style_key: str = "default_caption"
    font_family: str = "system"
    font_size: int = Field(default=42, gt=0)
    max_lines: int = Field(default=2, ge=1, le=4)

    model_config = ConfigDict(extra="forbid")


class AudioDuckingSpec(BaseModel):
    enabled: bool = False
    ducking_db: float = 0

    model_config = ConfigDict(extra="forbid")


class ThumbnailCompositorSpec(BaseModel):
    enabled: bool = False
    intent: Literal["CONTRACT_ONLY"] = "CONTRACT_ONLY"

    model_config = ConfigDict(extra="forbid")


class RenderSpecContract(BaseModel):
    render_spec_id: str
    video_project_id: uuid.UUID
    voice_timeline_snapshot_id: uuid.UUID
    visual_plan_snapshot_id: uuid.UUID
    caption_track_snapshot_id: uuid.UUID
    asset_manifest_snapshot_id: uuid.UUID
    scene_manifest_snapshot_id: uuid.UUID
    scenes: list[RenderSceneSpec] = Field(min_length=1)
    render_variants: list[RenderVariantSpec] = Field(min_length=1)
    audio_tracks: list[AudioTrackSpec] = Field(min_length=1)
    caption_track_ref: str
    default_export_profile: ExportProfileContract
    caption_style: CaptionStyleSpec = Field(default_factory=CaptionStyleSpec)
    audio_ducking: AudioDuckingSpec = Field(default_factory=AudioDuckingSpec)
    thumbnail_compositor: ThumbnailCompositorSpec = Field(default_factory=ThumbnailCompositorSpec)
    render_intent: RenderIntent
    total_duration_seconds: float = Field(gt=0)
    render_spec_hash: str

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_render_spec(self) -> "RenderSpecContract":
        _validate_ordered_render_scenes(self.scenes, self.total_duration_seconds)
        return self


class FileRefContract(BaseModel):
    file_path: str
    mime_type: str
    size_bytes: int = Field(gt=0)
    checksum: str
    duration_seconds: float | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    created_by: str
    source_type: str
    license_state: LicenseState
    provenance_blob: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProductionArtifactRunCreate(BaseModel):
    video_project_id: uuid.UUID
    source_project_admission_decision_id: uuid.UUID | None = None
    run_mode: ProductionRunMode = "REAL_DISABLED"
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProductionArtifactRunRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    source_project_admission_decision_id: uuid.UUID | None
    run_mode: ProductionRunMode
    status: ProductionRunStatus
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    script_artifact_version_id: uuid.UUID | None
    voice_timeline_snapshot_id: uuid.UUID | None
    caption_track_snapshot_id: uuid.UUID | None
    visual_plan_snapshot_id: uuid.UUID | None
    scene_manifest_snapshot_id: uuid.UUID | None
    render_spec_snapshot_id: uuid.UUID | None
    asset_manifest_snapshot_id: uuid.UUID | None
    source_manifest_snapshot_id: uuid.UUID | None
    render_package_snapshot_id: uuid.UUID | None
    media_qc_report_id: uuid.UUID | None
    accessibility_qc_report_id: uuid.UUID | None
    reason_codes: list[str]
    metadata: dict[str, Any]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class RenderLocalSmokeRequest(BaseModel):
    render_spec_snapshot_id: uuid.UUID
    output_dir: str | None = None

    model_config = ConfigDict(extra="forbid")


class QCRunRequest(BaseModel):
    render_package_snapshot_id: uuid.UUID | None = None
    render_spec_snapshot_id: uuid.UUID | None = None
    caption_track_snapshot_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


def _validate_ordered_segments(segments: list[NarrationSegmentContract]) -> None:
    seen_ids: set[str] = set()
    expected_index = segments[0].sequence_index
    last_end = -1.0
    for segment in segments:
        if segment.narration_segment_id in seen_ids:
            raise ValueError("narration segment ids must be unique")
        seen_ids.add(segment.narration_segment_id)
        if segment.sequence_index != expected_index:
            raise ValueError("narration sequence_index must be strictly increasing")
        if segment.estimated_start_time < last_end - 0.05:
            raise ValueError("narration segments must not overlap")
        if segment.estimated_start_time > last_end + 0.05 and last_end >= 0:
            raise ValueError("narration gaps must be explicit")
        last_end = segment.estimated_end_time
        expected_index += 1


def _validate_ordered_scenes(scenes: list[SceneSpecContract], total_duration_seconds: float) -> None:
    seen_ids: set[str] = set()
    narration_ids: set[str] = set()
    expected_index = scenes[0].sequence_index
    last_end = -1.0
    for scene in scenes:
        if scene.scene_id in seen_ids:
            raise ValueError("scene ids must be unique")
        if scene.narration_segment_id in narration_ids:
            raise ValueError("M6 scene compiler expects one scene per narration segment")
        seen_ids.add(scene.scene_id)
        narration_ids.add(scene.narration_segment_id)
        if scene.sequence_index != expected_index:
            raise ValueError("scene sequence_index must be strictly increasing")
        if scene.start_time < last_end - 0.05:
            raise ValueError("scenes must not overlap")
        if scene.start_time > last_end + 0.05 and last_end >= 0:
            raise ValueError("scene gaps must be explicit")
        last_end = scene.end_time
        expected_index += 1
    if abs(total_duration_seconds - scenes[-1].end_time) > 0.05:
        raise ValueError("scene coverage must match total duration")


def _validate_ordered_render_scenes(scenes: list[RenderSceneSpec], total_duration_seconds: float) -> None:
    last_end = -1.0
    for scene in scenes:
        if scene.start_time < last_end - 0.05:
            raise ValueError("render scenes must not overlap")
        if scene.start_time > last_end + 0.05 and last_end >= 0:
            raise ValueError("render scene gaps must be explicit")
        last_end = scene.end_time
    if abs(total_duration_seconds - scenes[-1].end_time) > 0.05:
        raise ValueError("render scene coverage must match voice timeline duration")
