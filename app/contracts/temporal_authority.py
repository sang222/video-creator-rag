from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.caption_voice_quality import (
    CanonicalCaptionCue,
    CaptionReadingMetrics,
    CreativeQualityGateResult,
)
from app.contracts.vcos_v2 import DurationContractV2


VerificationStatus = Literal["PASS", "BLOCK"]


class TextSpan(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ordered(self) -> "TextSpan":
        if self.end < self.start:
            raise ValueError("TEXT_SPAN_INVALID")
        return self


class EditorialScriptText(BaseModel):
    script_revision_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    locale: str = Field(min_length=2)
    language: str = Field(min_length=2)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class NormalizationOperation(BaseModel):
    operation_id: str = Field(min_length=1)
    operation_type: str = Field(min_length=1)
    source_span: TextSpan
    spoken_span: TextSpan
    source_text: str
    spoken_text: str
    reason_code: str = Field(min_length=1)
    whitelisted: bool

    model_config = ConfigDict(extra="forbid")


class SourceToSpokenSpan(BaseModel):
    source_span: TextSpan
    spoken_span: TextSpan
    operation_ids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class SpokenToken(BaseModel):
    token_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    spoken_span: TextSpan
    source_spans: list[TextSpan] = Field(min_length=1)
    normalization_operation_ids: list[str] = Field(default_factory=list)
    comparison_key: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class SpokenTextNormalized(BaseModel):
    normalization_version: str = Field(min_length=1)
    script_revision_id: str = Field(min_length=1)
    source_text_hash: str = Field(min_length=1)
    source_character_count: int = Field(gt=0)
    spoken_text: str = Field(min_length=1)
    spoken_text_hash: str = Field(min_length=1)
    spoken_character_count: int = Field(gt=0)
    normalization_operations: list[NormalizationOperation] = Field(default_factory=list)
    source_to_spoken_spans: list[SourceToSpokenSpan] = Field(min_length=1)
    spoken_tokens: list[SpokenToken] = Field(min_length=1)
    pronunciation_dictionary_refs: list[str] = Field(default_factory=list)
    normalization_warnings: list[str] = Field(default_factory=list)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def complete_mapping(self) -> "SpokenTextNormalized":
        if self.spoken_character_count != len(self.spoken_text):
            raise ValueError("NORMALIZATION_SPOKEN_LENGTH_MISMATCH")
        source_cursor = 0
        spoken_cursor = 0
        operation_ids = {item.operation_id for item in self.normalization_operations}
        referenced_operation_ids: set[str] = set()
        for mapping in self.source_to_spoken_spans:
            if (
                mapping.source_span.start != source_cursor
                or mapping.spoken_span.start != spoken_cursor
            ):
                raise ValueError("NORMALIZATION_SOURCE_ACCOUNTING_GAP")
            if not set(mapping.operation_ids).issubset(operation_ids):
                raise ValueError("NORMALIZATION_OPERATION_REF_UNKNOWN")
            referenced_operation_ids.update(mapping.operation_ids)
            source_cursor = mapping.source_span.end
            spoken_cursor = mapping.spoken_span.end
        if (
            source_cursor != self.source_character_count
            or spoken_cursor != self.spoken_character_count
        ):
            raise ValueError("NORMALIZATION_SOURCE_ACCOUNTING_GAP")
        if referenced_operation_ids != operation_ids:
            raise ValueError("NORMALIZATION_OPERATION_UNTRACEABLE")
        if any(
            token.spoken_span.end > self.spoken_character_count
            for token in self.spoken_tokens
        ):
            raise ValueError("NORMALIZATION_TOKEN_SPAN_INVALID")
        return self


class DisplayCaptionToken(BaseModel):
    display_token_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    spoken_token_ids: list[str] = Field(min_length=1)
    transform_reason_code: str | None = None

    model_config = ConfigDict(extra="forbid")


class DisplayCaptionText(BaseModel):
    spoken_text_hash: str = Field(min_length=1)
    display_text: str = Field(min_length=1)
    tokens: list[DisplayCaptionToken] = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class CharacterAlignment(BaseModel):
    character_index: int = Field(ge=0)
    character: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_time(self) -> "CharacterAlignment":
        if self.end_ms <= self.start_ms:
            raise ValueError("ALIGNMENT_TIME_INVALID")
        return self


class NarrationTimingSeed(BaseModel):
    provider_key: str = Field(min_length=1)
    provider_request_id: str | None = None
    audio_asset_ref: str = Field(min_length=1)
    audio_duration_ms: int = Field(gt=0)
    source_text_hash: str = Field(min_length=1)
    spoken_text_hash: str = Field(min_length=1)
    original_character_alignment: list[CharacterAlignment] = Field(default_factory=list)
    normalized_character_alignment: list[CharacterAlignment] = Field(
        default_factory=list
    )
    provider_model_id: str = Field(min_length=1)
    provider_voice_id: str = Field(min_length=1)
    seed: int | None = None
    voice_settings: dict[str, Any] = Field(default_factory=dict)
    pronunciation_dictionary_refs: list[str] = Field(default_factory=list)
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    timing_available: bool
    timing_parse_warnings: list[str] = Field(default_factory=list)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class AlignedWord(BaseModel):
    word_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    loss: float | None = None
    source_spoken_token_ids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_time(self) -> "AlignedWord":
        if self.end_ms <= self.start_ms:
            raise ValueError("ALIGNMENT_TIME_INVALID")
        return self


class ForcedAlignmentEvidence(BaseModel):
    provider_key: str = Field(min_length=1)
    provider_request_id: str | None = None
    provider_request_id_availability: Literal["PRESENT", "NOT_EXPOSED_BY_ENDPOINT"]
    audio_asset_ref: str = Field(min_length=1)
    audio_duration_ms: int = Field(gt=0)
    spoken_text_hash: str = Field(min_length=1)
    words: list[AlignedWord] = Field(default_factory=list)
    characters: list[CharacterAlignment] = Field(default_factory=list)
    alignment_loss: float | None = None
    transcript_loss: float | None = None
    missing_tokens: list[str] = Field(default_factory=list)
    extra_words: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def request_id_availability_is_truthful(self) -> "ForcedAlignmentEvidence":
        if self.provider_request_id:
            if self.provider_request_id_availability != "PRESENT":
                raise ValueError("FORCED_ALIGNMENT_REQUEST_ID_AVAILABILITY_INVALID")
        elif self.provider_request_id_availability != "NOT_EXPOSED_BY_ENDPOINT":
            raise ValueError("FORCED_ALIGNMENT_REQUEST_ID_AVAILABILITY_INVALID")
        return self


class VerifiedNarrationWord(BaseModel):
    word_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_spoken_token_ids: list[str] = Field(min_length=1)
    provider_start_ms: int | None = Field(default=None, ge=0)
    provider_end_ms: int | None = Field(default=None, gt=0)
    forced_start_ms: int | None = Field(default=None, ge=0)
    forced_end_ms: int | None = Field(default=None, gt=0)
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class TimingConflict(BaseModel):
    spoken_token_ids: list[str] = Field(min_length=1)
    provider_start_ms: int
    provider_end_ms: int
    forced_start_ms: int
    forced_end_ms: int
    max_delta_ms: int = Field(ge=0)
    reason_code: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class VerifiedNarrationAlignment(BaseModel):
    spoken_text_hash: str = Field(min_length=1)
    audio_asset_ref: str = Field(min_length=1)
    audio_duration_ms: int = Field(gt=0)
    verified_words: list[VerifiedNarrationWord] = Field(default_factory=list)
    provider_seed_ref: str | None = None
    forced_alignment_ref: str | None = None
    token_coverage: float = Field(ge=0, le=1)
    missing_tokens: list[str] = Field(default_factory=list)
    extra_tokens: list[str] = Field(default_factory=list)
    normalization_only_differences: list[dict[str, Any]] = Field(default_factory=list)
    timing_conflicts: list[TimingConflict] = Field(default_factory=list)
    alignment_confidence: float = Field(ge=0, le=1)
    reconciliation_reason_codes: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class PhraseBoundary(BaseModel):
    phrase_id: str = Field(min_length=1)
    spoken_token_ids: list[str] = Field(min_length=1)
    audio_start_ms: int = Field(ge=0)
    audio_end_ms: int = Field(gt=0)
    boundary_reason: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class CanonicalTimelineSegment(BaseModel):
    segment_id: str = Field(min_length=1)
    editorial_span: TextSpan
    spoken_span: TextSpan
    display_span: TextSpan | None = None
    spoken_token_ids: list[str] = Field(min_length=1)
    audio_start_ms: int = Field(ge=0)
    audio_end_ms: int = Field(gt=0)
    words: list[VerifiedNarrationWord] = Field(min_length=1)
    phrase_boundaries: list[PhraseBoundary] = Field(min_length=1)
    caption_start_ms: int | None = Field(default=None, ge=0)
    caption_end_ms: int | None = Field(default=None, gt=0)
    caption_lines: list[str] = Field(default_factory=list)
    caption_cues: list[CanonicalCaptionCue] = Field(default_factory=list)
    caption_cue_ids: list[str] = Field(default_factory=list)
    caption_spoken_token_ids: list[str] = Field(default_factory=list)
    caption_reading_metrics: list[CaptionReadingMetrics] = Field(default_factory=list)
    caption_gate_results: list[CreativeQualityGateResult] = Field(default_factory=list)
    scene_start_ms: int = Field(ge=0)
    scene_end_ms: int = Field(gt=0)
    target_scene_duration_ms: int = Field(gt=0)
    asset_binding: dict[str, Any] | None = None
    asset_in_ms: int | None = Field(default=None, ge=0)
    asset_out_ms: int | None = Field(default=None, ge=0)
    transition: str | None = None
    motion_intent: str | None = None
    semantic_score: float | None = None
    continuity_score: float | None = None
    alignment_confidence: float = Field(ge=0, le=1)
    timing_source: Literal["VERIFIED_NARRATION_ALIGNMENT"] = (
        "VERIFIED_NARRATION_ALIGNMENT"
    )
    source_provenance: list[dict[str, Any]] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_timing(self) -> "CanonicalTimelineSegment":
        if self.audio_end_ms <= self.audio_start_ms:
            raise ValueError("TEMPORAL_AUDIO_SPAN_INVALID")
        if self.scene_end_ms <= self.scene_start_ms:
            raise ValueError("TEMPORAL_SCENE_SPAN_INVALID")
        if self.target_scene_duration_ms != self.scene_end_ms - self.scene_start_ms:
            raise ValueError("TEMPORAL_SCENE_DURATION_MISMATCH")
        if self.caption_cues:
            cue_ids = [cue.cue_id for cue in self.caption_cues]
            if len(cue_ids) != len(set(cue_ids)):
                raise ValueError("CAPTION_CUE_ID_DUPLICATE")
            if self.caption_cue_ids and self.caption_cue_ids != cue_ids:
                raise ValueError("CAPTION_CUE_INDEX_MISMATCH")
            cue_tokens = [
                token_id
                for cue in self.caption_cues
                for token_id in cue.spoken_token_ids
            ]
            if (
                self.caption_spoken_token_ids
                and self.caption_spoken_token_ids != cue_tokens
            ):
                raise ValueError("CAPTION_TOKEN_INDEX_MISMATCH")
            if any(
                cue.source_segment_ids != [self.segment_id] for cue in self.caption_cues
            ):
                raise ValueError("CAPTION_CUE_SEGMENT_MISMATCH")
        return self


class CanonicalMediaTimeline(BaseModel):
    timeline_version: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    script_revision_id: str = Field(min_length=1)
    spoken_text_revision_id: str = Field(min_length=1)
    tts_request_id: str = Field(min_length=1)
    audio_asset_id: str = Field(min_length=1)
    audio_duration_ms: int = Field(gt=0)
    duration_contract: DurationContractV2 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    provider_timing_seed_ref: str = Field(min_length=1)
    forced_alignment_ref: str = Field(min_length=1)
    verified_alignment_ref: str = Field(min_length=1)
    segments: list[CanonicalTimelineSegment] = Field(min_length=1)
    qc_metrics: dict[str, Any] = Field(default_factory=dict)
    compilation_warnings: list[str] = Field(default_factory=list)
    timeline_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def duration_matches_frozen_contract(self) -> "CanonicalMediaTimeline":
        if self.duration_contract is not None and not (
            self.duration_contract.minimum_duration_ms
            <= self.audio_duration_ms
            <= self.duration_contract.maximum_duration_ms
        ):
            raise ValueError("TIMELINE_DURATION_OUTSIDE_CHANNEL_CONTRACT")
        return self


class EditorialSegmentInput(BaseModel):
    segment_id: str = Field(min_length=1)
    editorial_span: TextSpan
    spoken_token_ids: list[str] = Field(min_length=1)
    display_span: TextSpan | None = None
    motion_intent: str | None = None
    source_provenance: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class FinalNarrationAudio(BaseModel):
    audio_asset_ref: str = Field(min_length=1)
    duration_ms: int = Field(gt=0)
    is_final: bool = True
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class TemporalAuthorityGateResult(BaseModel):
    gate_status: VerificationStatus
    block_reasons: list[str] = Field(default_factory=list)
    exact_next_action: str = Field(min_length=1)
    supporting_visual_subwindows_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")
