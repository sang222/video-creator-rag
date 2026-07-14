from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CreativeGateStatus = Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]
CaptionTimingSource = Literal["CANONICAL_MEDIA_TIMELINE"]


class ThresholdBand(BaseModel):
    """Inclusive PASS/review bands with optional one-sided hard limits."""

    pass_range: tuple[float, float] = Field(alias="pass")
    review_range: tuple[float, float] = Field(alias="review")
    block_above: float | None = None
    block_below: float | None = None
    extreme_slow_block_below: float | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def ordered(self) -> "ThresholdBand":
        if self.pass_range[0] > self.pass_range[1] or self.review_range[0] > self.review_range[1]:
            raise ValueError("POLICY_THRESHOLD_RANGE_INVALID")
        if self.review_range[0] > self.pass_range[0] or self.review_range[1] < self.pass_range[1]:
            raise ValueError("POLICY_REVIEW_RANGE_MUST_CONTAIN_PASS_RANGE")
        return self


class MaximumThreshold(BaseModel):
    pass_max: float
    review_max: float
    block_above: float

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ordered(self) -> "MaximumThreshold":
        if not self.pass_max <= self.review_max <= self.block_above:
            raise ValueError("POLICY_THRESHOLD_ORDER_INVALID")
        return self


class NarrationPacingPolicy(BaseModel):
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str | None = None
    channel_id: str | None = None
    active_speech_silence_gap_threshold_ms: int = Field(
        default=350,
        alias="silence_gap_threshold_ms",
        ge=0,
    )
    hook_window_ms: int = Field(default=8_000, gt=0)
    body_active_speech_wpm: ThresholdBand
    body_delivered_wpm: ThresholdBand
    hook_first_8s_active_wpm: MaximumThreshold
    comma_pause_ms: ThresholdBand
    sentence_pause_ms: ThresholdBand
    section_pause_ms: ThresholdBand
    emergency_atempo: dict[str, float] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CaptionFormatPolicy(BaseModel):
    font_scale_pass: tuple[float, float]
    font_scale_review: tuple[float, float]
    block_outside: tuple[float, float]
    max_chars_per_line_pass: int = Field(gt=0)
    max_chars_per_line_review: int = Field(gt=0)
    max_chars_per_line_block: int = Field(gt=0)
    max_block_width_pass: float = Field(gt=0, le=1)
    max_block_width_review: float = Field(gt=0, le=1)
    max_block_width_block: float = Field(gt=0, le=1)
    bottom_safe_margin_pass: float = Field(ge=0, le=1)
    bottom_safe_margin_review_min: float = Field(ge=0, le=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ordered(self) -> "CaptionFormatPolicy":
        if not (
            self.block_outside[0]
            <= self.font_scale_review[0]
            <= self.font_scale_pass[0]
            <= self.font_scale_pass[1]
            <= self.font_scale_review[1]
            <= self.block_outside[1]
        ):
            raise ValueError("CAPTION_FONT_SCALE_POLICY_INVALID")
        if not self.max_chars_per_line_pass <= self.max_chars_per_line_review <= self.max_chars_per_line_block:
            raise ValueError("CAPTION_CPL_POLICY_INVALID")
        if not self.max_block_width_pass <= self.max_block_width_review <= self.max_block_width_block:
            raise ValueError("CAPTION_BLOCK_WIDTH_POLICY_INVALID")
        if self.bottom_safe_margin_review_min > self.bottom_safe_margin_pass:
            raise ValueError("CAPTION_SAFE_MARGIN_POLICY_INVALID")
        return self


class CueDurationPolicy(BaseModel):
    pass_range: tuple[float, float] = Field(alias="pass")
    review_range: tuple[float, float] = Field(alias="review")
    block_outside: tuple[float, float]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def ordered(self) -> "CueDurationPolicy":
        if not (
            self.block_outside[0]
            <= self.review_range[0]
            <= self.pass_range[0]
            <= self.pass_range[1]
            <= self.review_range[1]
            <= self.block_outside[1]
        ):
            raise ValueError("CAPTION_DURATION_POLICY_INVALID")
        return self


class ReadingSpeedPolicy(BaseModel):
    pass_average_max: float = Field(gt=0)
    review_average_max: float = Field(gt=0)
    block_average_above: float = Field(gt=0)
    pass_p95_max: float = Field(gt=0)
    review_p95_max: float = Field(gt=0)
    block_any_above: float = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


class CaptionGlobalPolicy(BaseModel):
    max_lines_per_cue: int = Field(default=2, ge=1)
    cue_duration_seconds: CueDurationPolicy
    reading_speed_cps: ReadingSpeedPolicy

    model_config = ConfigDict(extra="forbid")


class CaptionStylePolicy(BaseModel):
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str | None = None
    channel_id: str | None = None
    longform_16_9: CaptionFormatPolicy
    shorts_9_16: CaptionFormatPolicy
    global_policy: CaptionGlobalPolicy = Field(alias="global")
    font_family: str = Field(default="Arial", min_length=1)
    primary_colour: str = "&H00FFFFFF"
    outline_colour: str = "&H80000000"
    border_style: int = Field(default=3, ge=1, le=4)
    outline_ratio: float = Field(default=0.055, ge=0, le=0.2)
    shadow_ratio: float = Field(default=0.025, ge=0, le=0.2)
    alignment: int = Field(default=2, ge=1, le=9)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("font_family")
    @classmethod
    def ass_safe_font_family(cls, value: str) -> str:
        if any(character in value for character in (",", "\r", "\n", "{", "}", "\\")):
            raise ValueError("CAPTION_ASS_FONT_FAMILY_UNSAFE")
        return value

    @field_validator("primary_colour", "outline_colour")
    @classmethod
    def ass_safe_colour(cls, value: str) -> str:
        if len(value) != 10 or not value.startswith("&H") or any(
            character not in "0123456789abcdefABCDEF" for character in value[2:]
        ):
            raise ValueError("CAPTION_ASS_COLOUR_INVALID")
        return value


class OffsetThreshold(BaseModel):
    pass_max: float = Field(ge=0)
    review_max: float = Field(ge=0)
    block_above: float = Field(ge=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ordered(self) -> "OffsetThreshold":
        if not self.pass_max <= self.review_max <= self.block_above:
            raise ValueError("SYNC_THRESHOLD_ORDER_INVALID")
        return self


class CaptionSyncPolicy(BaseModel):
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str | None = None
    channel_id: str | None = None
    median_abs_start_offset_ms: OffsetThreshold
    p95_abs_start_offset_ms: OffsetThreshold
    max_abs_start_offset_ms: OffsetThreshold
    median_abs_end_offset_ms: OffsetThreshold
    end_of_video_drift_ms: OffsetThreshold
    spoken_token_coverage_required: float = Field(default=1.0, ge=0, le=1)
    unexpected_cue_overlap_block: bool = True

    model_config = ConfigDict(extra="forbid")


class PauseSpan(BaseModel):
    pause_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source: Literal["AUDIO_SILENCE_ANALYSIS", "VERIFIED_WORD_GAP", "AUDIO_BOUNDARY"]
    after_spoken_token_id: str | None = None
    before_spoken_token_id: str | None = None
    boundary_kind: Literal["COMMA", "SENTENCE", "SECTION", "OTHER", "BOUNDARY"] = "OTHER"
    detected_in_audio: bool = False

    model_config = ConfigDict(extra="forbid")

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class NarrationAudioAnalysis(BaseModel):
    audio_asset_ref: str = Field(min_length=1)
    audio_duration_ms: int = Field(gt=0)
    silence_spans: list[PauseSpan] = Field(default_factory=list)
    waveform_summary: dict[str, Any] = Field(default_factory=dict)
    analysis_ref: str | None = None
    analysis_hash: str | None = None

    model_config = ConfigDict(extra="forbid")


class NarrationPacingMetrics(BaseModel):
    spoken_word_count: int = Field(ge=0)
    active_speech_duration_ms: int = Field(ge=0)
    delivered_duration_ms: int = Field(gt=0)
    hook_word_count: int = Field(ge=0)
    hook_active_speech_duration_ms: int = Field(ge=0)
    active_speech_wpm: float = Field(ge=0)
    delivered_wpm: float = Field(ge=0)
    hook_first_8s_active_wpm: float = Field(ge=0)
    comma_pause_ms_median: float | None = Field(default=None, ge=0)
    sentence_pause_ms_median: float | None = Field(default=None, ge=0)
    section_pause_ms_median: float | None = Field(default=None, ge=0)
    comma_pause_count: int = Field(ge=0)
    sentence_pause_count: int = Field(ge=0)
    section_pause_count: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class CreativeQualityGateResult(BaseModel):
    gate: str = Field(min_length=1)
    status: CreativeGateStatus
    reason_codes: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    policy_ref: str | None = None
    policy_version: str | None = None
    policy_hash: str | None = None

    model_config = ConfigDict(extra="forbid")


class NarrationPacingReport(BaseModel):
    audio_asset_ref: str = Field(min_length=1)
    spoken_text_hash: str = Field(min_length=1)
    verified_alignment_ref: str = Field(min_length=1)
    metrics: NarrationPacingMetrics
    detected_pause_spans: list[PauseSpan] = Field(default_factory=list)
    waveform_summary: dict[str, Any] = Field(default_factory=dict)
    word_count_evidence: list[dict[str, Any]] = Field(default_factory=list)
    gate_result: CreativeQualityGateResult | None = None
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class NarrationPacingCorrectionPlan(BaseModel):
    action: Literal[
        "ACCEPT_MEASURED_NARRATION",
        "ONE_CONTROLLED_SPEED_REGENERATION",
        "SCRIPT_PACING_REWRITE_REQUIRED",
        "EMERGENCY_ATEMPO",
        "HUMAN_ATEMPO_APPROVAL_REQUIRED",
        "BLOCK_NO_AUTHORIZED_REGENERATION",
        "HUMAN_PACING_REVIEW_REQUIRED",
    ]
    pacing_gate_status: CreativeGateStatus
    provider_speed_regeneration_count: int = Field(ge=0, le=1)
    provider_regeneration_authorized: bool
    emergency_atempo_delta_percent: float | None = Field(default=None, ge=0)
    ffmpeg_atempo_allowed: bool
    human_approval_required: bool
    blocks_current_narration: bool
    remeasure_final_audio_required: bool
    reason_codes: list[str] = Field(min_length=1)
    exact_recommendation: str = Field(min_length=1)
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def correction_boundary_is_consistent(self) -> "NarrationPacingCorrectionPlan":
        if self.action == "ONE_CONTROLLED_SPEED_REGENERATION":
            if self.provider_speed_regeneration_count != 1 or not self.provider_regeneration_authorized:
                raise ValueError("PACING_REGENERATION_SCOPE_INVALID")
        elif self.provider_speed_regeneration_count:
            raise ValueError("PACING_REGENERATION_SCOPE_INVALID")
        if self.action == "EMERGENCY_ATEMPO":
            if not self.ffmpeg_atempo_allowed or self.emergency_atempo_delta_percent is None:
                raise ValueError("PACING_ATEMPO_SCOPE_INVALID")
        elif self.ffmpeg_atempo_allowed:
            raise ValueError("PACING_ATEMPO_SCOPE_INVALID")
        if self.action == "ACCEPT_MEASURED_NARRATION" and self.blocks_current_narration:
            raise ValueError("PACING_ACCEPTANCE_SCOPE_INVALID")
        return self


class CaptionTextSpan(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ordered(self) -> "CaptionTextSpan":
        if self.end < self.start:
            raise ValueError("CAPTION_TEXT_SPAN_INVALID")
        return self


class CaptionDisplayToken(BaseModel):
    display_token_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    spoken_token_ids: list[str] = Field(min_length=1)
    transform_reason_code: str | None = None

    model_config = ConfigDict(extra="forbid")


class CaptionReadingMetrics(BaseModel):
    duration_seconds: float = Field(gt=0)
    character_count: int = Field(ge=0)
    characters_per_second: float = Field(ge=0)
    chars_per_line: list[int] = Field(min_length=1)
    max_chars_per_line: int = Field(ge=0)
    line_count: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class CaptionBBoxMetrics(BaseModel):
    cue_id: str = Field(min_length=1)
    frame_width: int = Field(gt=0)
    frame_height: int = Field(gt=0)
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    block_width_ratio: float = Field(default=0, ge=0)
    left_margin_ratio: float = Field(default=0, ge=0)
    right_margin_ratio: float = Field(default=0, ge=0)
    top_margin_ratio: float = Field(default=0, ge=0)
    bottom_margin_ratio: float = Field(default=0, ge=0)
    font_scale: float = Field(gt=0)
    line_count: int = Field(ge=1)
    cpl: int = Field(ge=0)
    cps: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    text_outside_frame: bool = False
    required_safe_zone_overlap: bool = False
    preview_frame_ref: str | None = None
    ffmpeg_stderr_excerpt: str | None = None

    model_config = ConfigDict(extra="forbid")


class CanonicalCaptionCue(BaseModel):
    cue_id: str = Field(min_length=1)
    source_segment_ids: list[str] = Field(min_length=1)
    display_span: CaptionTextSpan | None = None
    caption_start_ms: int = Field(ge=0)
    caption_end_ms: int = Field(gt=0)
    caption_lines: list[str] = Field(min_length=1)
    spoken_token_ids: list[str] = Field(min_length=1)
    display_tokens: list[CaptionDisplayToken] = Field(min_length=1)
    reading_metrics: CaptionReadingMetrics
    bbox_metrics: CaptionBBoxMetrics | None = None
    gate_results: list[CreativeQualityGateResult] = Field(default_factory=list)
    timing_source: CaptionTimingSource = "CANONICAL_MEDIA_TIMELINE"
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def timing_and_lines(self) -> "CanonicalCaptionCue":
        if self.caption_end_ms <= self.caption_start_ms:
            raise ValueError("CAPTION_CUE_TIME_INVALID")
        if any(not line.strip() for line in self.caption_lines):
            raise ValueError("CAPTION_EMPTY_LINE")
        if any(
            any(character in line for character in ("\r", "\n", "\\", "{", "}"))
            for line in self.caption_lines
        ):
            raise ValueError("CAPTION_ASS_CONTROL_SEQUENCE_BLOCKED")
        return self

    @property
    def display_text(self) -> str:
        return "\n".join(self.caption_lines)


class ASSCaptionEvent(BaseModel):
    cue_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class CompiledCaptionTrack(BaseModel):
    compilation_version: str = Field(min_length=1)
    spoken_text_hash: str = Field(min_length=1)
    canonical_timeline_ref: str = Field(min_length=1)
    canonical_timeline_hash: str = Field(min_length=1)
    cues: list[CanonicalCaptionCue] = Field(min_length=1)
    srt_text: str = Field(min_length=1)
    ass_events: list[ASSCaptionEvent] = Field(min_length=1)
    spoken_token_coverage: float = Field(ge=0, le=1)
    missing_spoken_token_ids: list[str] = Field(default_factory=list)
    extra_spoken_token_ids: list[str] = Field(default_factory=list)
    compilation_gate: CreativeQualityGateResult
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class CaptionBoundsPreflightReport(BaseModel):
    ffmpeg_binary: str = Field(min_length=1)
    frame_width: int = Field(gt=0)
    frame_height: int = Field(gt=0)
    cue_metrics: list[CaptionBBoxMetrics] = Field(default_factory=list)
    layout_gate: CreativeQualityGateResult
    safe_area_gate: CreativeQualityGateResult
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class CaptionSyncMetrics(BaseModel):
    median_abs_start_offset_ms: float = Field(ge=0)
    p95_abs_start_offset_ms: float = Field(ge=0)
    max_abs_start_offset_ms: float = Field(ge=0)
    median_abs_end_offset_ms: float = Field(ge=0)
    end_of_video_drift_ms: float = Field(ge=0)
    spoken_token_coverage: float = Field(ge=0, le=1)
    unexpected_cue_overlap_count: int = Field(ge=0)
    non_monotonic_cue_count: int = Field(ge=0)
    cue_outside_audio_count: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")
