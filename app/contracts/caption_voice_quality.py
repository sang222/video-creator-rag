from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        if (
            self.pass_range[0] > self.pass_range[1]
            or self.review_range[0] > self.review_range[1]
        ):
            raise ValueError("POLICY_THRESHOLD_RANGE_INVALID")
        if (
            self.review_range[0] > self.pass_range[0]
            or self.review_range[1] < self.pass_range[1]
        ):
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


class SubtitleSidecarFormatPolicy(BaseModel):
    max_chars_per_line_pass: int = Field(gt=0)
    max_chars_per_line_review: int = Field(gt=0)
    max_chars_per_line_block: int = Field(gt=0)
    # Historical style snapshots may include burn-in-only fields. They remain
    # readable but are intentionally ignored by the sidecar-only compiler.
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def ordered(self) -> "SubtitleSidecarFormatPolicy":
        if (
            not self.max_chars_per_line_pass
            <= self.max_chars_per_line_review
            <= self.max_chars_per_line_block
        ):
            raise ValueError("CAPTION_CPL_POLICY_INVALID")
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


class SubtitleSidecarPolicy(BaseModel):
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str | None = None
    channel_id: str | None = None
    longform_16_9: SubtitleSidecarFormatPolicy
    global_policy: CaptionGlobalPolicy = Field(alias="global")

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


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


class FinalCueTrailingHoldPolicy(BaseModel):
    """Narrow policy for displaying the final cue through bounded tail silence."""

    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str | None = None
    maximum_hold_ms: int = Field(gt=0, le=2_000)
    target_endpoint: Literal["CANONICAL_AUDIO_END"] = "CANONICAL_AUDIO_END"

    model_config = ConfigDict(extra="forbid")


class FinalCueTrailingHoldEvidence(BaseModel):
    status: Literal["NOT_REQUIRED", "APPLIED"]
    reason_code: Literal[
        "CAPTION_FINAL_CUE_ALREADY_REACHES_CANONICAL_AUDIO_END",
        "CAPTION_FINAL_CUE_HELD_THROUGH_CANONICAL_TRAILING_SILENCE",
    ]
    target_endpoint: Literal["CANONICAL_AUDIO_END"]
    final_segment_id: str = Field(min_length=1)
    final_spoken_token_id: str = Field(min_length=1)
    aligned_word_end_ms: int = Field(ge=0)
    caption_end_before_ms: int = Field(ge=0)
    caption_end_after_ms: int = Field(ge=0)
    canonical_audio_end_ms: int = Field(gt=0)
    hold_duration_ms: int = Field(ge=0)
    maximum_hold_ms: int = Field(gt=0, le=2_000)
    spoken_token_ids_unchanged: Literal[True] = True
    spoken_word_timing_unchanged: Literal[True] = True
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def bounded_canonical_hold(self) -> "FinalCueTrailingHoldEvidence":
        expected_reason = (
            "CAPTION_FINAL_CUE_HELD_THROUGH_CANONICAL_TRAILING_SILENCE"
            if self.status == "APPLIED"
            else "CAPTION_FINAL_CUE_ALREADY_REACHES_CANONICAL_AUDIO_END"
        )
        if self.reason_code != expected_reason:
            raise ValueError("CAPTION_TRAILING_HOLD_STATUS_INVALID")
        if self.caption_end_before_ms != self.aligned_word_end_ms:
            raise ValueError("CAPTION_TRAILING_HOLD_ALIGNMENT_ENDPOINT_INVALID")
        if self.caption_end_after_ms != self.canonical_audio_end_ms:
            raise ValueError("CAPTION_TRAILING_HOLD_CANONICAL_ENDPOINT_INVALID")
        if (
            self.hold_duration_ms
            != self.caption_end_after_ms - self.caption_end_before_ms
        ):
            raise ValueError("CAPTION_TRAILING_HOLD_DURATION_INVALID")
        if self.hold_duration_ms > self.maximum_hold_ms:
            raise ValueError("CAPTION_TRAILING_HOLD_EXCEEDS_POLICY")
        if self.status == "APPLIED" and self.hold_duration_ms <= 0:
            raise ValueError("CAPTION_TRAILING_HOLD_DURATION_INVALID")
        if self.status == "NOT_REQUIRED" and self.hold_duration_ms != 0:
            raise ValueError("CAPTION_TRAILING_HOLD_DURATION_INVALID")
        return self


class PauseSpan(BaseModel):
    pause_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source: Literal["AUDIO_SILENCE_ANALYSIS", "VERIFIED_WORD_GAP", "AUDIO_BOUNDARY"]
    after_spoken_token_id: str | None = None
    before_spoken_token_id: str | None = None
    boundary_kind: Literal["COMMA", "SENTENCE", "SECTION", "OTHER", "BOUNDARY"] = (
        "OTHER"
    )
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
            if (
                self.provider_speed_regeneration_count != 1
                or not self.provider_regeneration_authorized
            ):
                raise ValueError("PACING_REGENERATION_SCOPE_INVALID")
        elif self.provider_speed_regeneration_count:
            raise ValueError("PACING_REGENERATION_SCOPE_INVALID")
        if self.action == "EMERGENCY_ATEMPO":
            if (
                not self.ffmpeg_atempo_allowed
                or self.emergency_atempo_delta_percent is None
            ):
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
    gate_results: list[CreativeQualityGateResult] = Field(default_factory=list)
    # Legacy renderer diagnostics are retained only so immutable historical
    # fixtures remain readable.  They are not caption authority and are never
    # emitted into final media or the canonical sidecar SRT.
    bbox_metrics: dict[str, Any] | None = None
    timing_source: CaptionTimingSource = "CANONICAL_MEDIA_TIMELINE"
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def timing_and_lines(self) -> "CanonicalCaptionCue":
        if self.caption_end_ms <= self.caption_start_ms:
            raise ValueError("CAPTION_CUE_TIME_INVALID")
        if any(not line.strip() for line in self.caption_lines):
            raise ValueError("CAPTION_EMPTY_LINE")
        if any(any(character in line for character in ("\r", "\n")) for line in self.caption_lines):
            raise ValueError("CAPTION_SRT_LINE_BREAK_INVALID")
        return self

    @property
    def display_text(self) -> str:
        return "\n".join(self.caption_lines)


class CompiledCaptionTrack(BaseModel):
    compilation_version: str = Field(min_length=1)
    spoken_text_hash: str = Field(min_length=1)
    canonical_timeline_ref: str = Field(min_length=1)
    canonical_timeline_hash: str = Field(min_length=1)
    cues: list[CanonicalCaptionCue] = Field(min_length=1)
    srt_text: str = Field(min_length=1)
    spoken_token_coverage: float = Field(ge=0, le=1)
    missing_spoken_token_ids: list[str] = Field(default_factory=list)
    extra_spoken_token_ids: list[str] = Field(default_factory=list)
    compilation_gate: CreativeQualityGateResult
    subtitle_qc_gate: CreativeQualityGateResult | None = None
    final_cue_trailing_hold: FinalCueTrailingHoldEvidence | None = None
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
