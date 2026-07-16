from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts.caption_voice_quality import (
    CaptionBBoxMetrics,
    CaptionDisplayToken,
    CaptionStylePolicy,
    CreativeQualityGateResult,
    NarrationAudioAnalysis,
)
from app.contracts.native_renderer import CanvasSpec, NativeRenderPlan, NativeRenderScene
from app.contracts.temporal_authority import (
    DisplayCaptionText,
    DisplayCaptionToken,
    EditorialSegmentInput,
    TextSpan,
    VerifiedNarrationAlignment,
    VerifiedNarrationWord,
)
from app.services.caption_ass import build_caption_ass_document
from app.services.caption_voice_quality import (
    CaptionAudioSyncGate,
    CaptionBoundsPreflight,
    CaptionCompilationGate,
    CaptionCoverageGate,
    CaptionLayoutGate,
    CaptionSafeAreaGate,
    NarrationPacingAnalyzer,
    NarrationPacingCorrectionPlanner,
    NarrationPacingGate,
    ReadableCaptionCompiler,
    TimelineDriftGate,
)
from app.services.creative_media_qc import TechnicalMediaQC
from app.services.native_ffmpeg_renderer import FFmpegCommandBuilder, NativeFFmpegRenderer
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import (
    canonical_caption_render_hash,
    canonical_plan_hash,
    stable_hash,
)
from app.services.temporal_authority import CanonicalMediaTimelineCompiler, SpokenTextNormalizer


FFMPEG_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"


def pacing_policy() -> dict:
    return {
        "policy_ref": "policy://fixture/narration-pacing/v1",
        "policy_version": "fixture-narration-pacing/v1",
        "silence_gap_threshold_ms": 350,
        "body_active_speech_wpm": {
            "pass": [145, 170], "review": [135, 180], "block_above": 180,
            "extreme_slow_block_below": 120,
        },
        "body_delivered_wpm": {
            "pass": [130, 155], "review": [120, 165], "block_above": 165,
            "extreme_slow_block_below": 105,
        },
        "hook_first_8s_active_wpm": {"pass_max": 180, "review_max": 188, "block_above": 188},
        "comma_pause_ms": {"pass": [180, 320], "review": [140, 380], "block_below": 140},
        "sentence_pause_ms": {"pass": [320, 650], "review": [250, 800], "block_below": 250},
        "section_pause_ms": {"pass": [600, 1200], "review": [450, 1500], "block_below": 450},
        "emergency_atempo": {
            "max_abs_delta_percent_without_human": 2,
            "human_approval_required_above_percent": 2,
            "block_above_percent": 3,
        },
    }


def caption_policy(*, long_cpl: int = 42, short_cpl: int = 32) -> dict:
    return {
        "policy_ref": "policy://fixture/caption-style/v1",
        "policy_version": "fixture-caption-style/v1",
        "longform_16_9": {
            "font_scale_pass": [0.044, 0.050], "font_scale_review": [0.040, 0.054],
            "block_outside": [0.040, 0.054],
            "max_chars_per_line_pass": long_cpl,
            "max_chars_per_line_review": max(long_cpl, 46),
            "max_chars_per_line_block": max(long_cpl, 46),
            "max_block_width_pass": 0.68, "max_block_width_review": 0.74,
            "max_block_width_block": 0.74,
            "bottom_safe_margin_pass": 0.08, "bottom_safe_margin_review_min": 0.05,
        },
        "shorts_9_16": {
            "font_scale_pass": [0.046, 0.054], "font_scale_review": [0.042, 0.058],
            "block_outside": [0.042, 0.058],
            "max_chars_per_line_pass": short_cpl,
            "max_chars_per_line_review": max(short_cpl, 36),
            "max_chars_per_line_block": max(short_cpl, 36),
            "max_block_width_pass": 0.84, "max_block_width_review": 0.88,
            "max_block_width_block": 0.88,
            "bottom_safe_margin_pass": 0.12, "bottom_safe_margin_review_min": 0.08,
        },
        "global": {
            "max_lines_per_cue": 2,
            "cue_duration_seconds": {"pass": [1.0, 6.0], "review": [0.8, 7.0], "block_outside": [0.8, 7.0]},
            "reading_speed_cps": {
                "pass_average_max": 15, "review_average_max": 17.5,
                "block_average_above": 17.5, "pass_p95_max": 17,
                "review_p95_max": 20, "block_any_above": 20,
            },
        },
        "font_family": "Arial",
        "outline_ratio": 0.002,
        "shadow_ratio": 0.0,
    }


def sync_policy() -> dict:
    return {
        "policy_ref": "policy://fixture/caption-sync/v1",
        "policy_version": "fixture-caption-sync/v1",
        "median_abs_start_offset_ms": {"pass_max": 80, "review_max": 120, "block_above": 120},
        "p95_abs_start_offset_ms": {"pass_max": 150, "review_max": 220, "block_above": 220},
        "max_abs_start_offset_ms": {"pass_max": 220, "review_max": 300, "block_above": 300},
        "median_abs_end_offset_ms": {"pass_max": 100, "review_max": 150, "block_above": 150},
        "end_of_video_drift_ms": {"pass_max": 150, "review_max": 250, "block_above": 250},
        "spoken_token_coverage_required": 1.0,
        "unexpected_cue_overlap_block": True,
    }


def final_cue_trailing_hold_policy(*, maximum_hold_ms: int = 1_000) -> dict:
    return {
        "policy_ref": "policy://fixture/final-cue-trailing-hold/v1",
        "policy_version": "fixture-final-cue-trailing-hold/v1",
        "maximum_hold_ms": maximum_hold_ms,
        "target_endpoint": "CANONICAL_AUDIO_END",
    }


def authority_components(
    source: str = "A calm media workflow turns one approved script into a synchronized final video.",
    *,
    word_ms: int = 320,
    base_gap_ms: int = 80,
    punctuation_gaps: dict[str, int] | None = None,
    trailing_ms: int = 0,
):
    normalized = SpokenTextNormalizer().normalize(script_revision_id="cqr1b-script", source_text=source)
    punctuation_gaps = punctuation_gaps or {}
    words = []
    cursor = 0
    for index, token in enumerate(normalized.spoken_tokens):
        end = cursor + word_ms
        words.append(
            VerifiedNarrationWord(
                word_id=f"verified-{index + 1:04d}", text=token.text,
                start_ms=cursor, end_ms=end, source_spoken_token_ids=[token.token_id],
                provider_start_ms=cursor, provider_end_ms=end,
                forced_start_ms=cursor, forced_end_ms=end,
                confidence=1.0,
                reason_codes=["PROVIDER_TIMING_PRIMARY_SEED", "FORCED_ALIGNMENT_VERIFIED"],
            )
        )
        if index + 1 < len(normalized.spoken_tokens):
            following = normalized.spoken_tokens[index + 1]
            separator = normalized.spoken_text[token.spoken_span.end:following.spoken_span.start]
            gap = next((value for mark, value in punctuation_gaps.items() if mark in separator), base_gap_ms)
            cursor = end + gap
        else:
            cursor = end
    duration_ms = words[-1].end_ms + trailing_ms
    alignment_payload = {
        "spoken_text_hash": normalized.spoken_text_hash,
        "audio_asset_ref": "fixture://cqr1b/final-narration.wav",
        "audio_duration_ms": duration_ms,
        "verified_words": [item.model_dump(mode="json") for item in words],
        "provider_seed_ref": "provider-seed:fixture",
        "forced_alignment_ref": "forced-alignment:fixture",
        "token_coverage": 1.0,
        "missing_tokens": [], "extra_tokens": [], "normalization_only_differences": [],
        "timing_conflicts": [], "alignment_confidence": 1.0,
        "reconciliation_reason_codes": ["PROVIDER_SEED_FORCED_ALIGNMENT_RECONCILED"],
        "verification_status": "PASS",
    }
    alignment = VerifiedNarrationAlignment(**alignment_payload, content_hash=stable_hash(alignment_payload))
    timeline = CanonicalMediaTimelineCompiler().compile(
        project_id="cqr1b-project", package_id="cqr1b-package", channel_id="fixture-channel",
        script_revision_id=normalized.script_revision_id,
        spoken_text_revision_id=normalized.content_hash, tts_request_id="fixture-tts",
        normalized=normalized, alignment=alignment,
        segments=[EditorialSegmentInput(
            segment_id="scene-1", editorial_span=TextSpan(start=0, end=len(source)),
            spoken_token_ids=[item.token_id for item in normalized.spoken_tokens],
        )],
    )
    return normalized, alignment, timeline


def compiled_components(source: str | None = None, *, policy: dict | None = None):
    normalized, alignment, timeline = authority_components(source or "A calm media workflow turns one approved script into a synchronized final video.")
    result = ReadableCaptionCompiler().compile(
        normalized=normalized, alignment=alignment, timeline=timeline,
        policy=policy or caption_policy(), aspect_ratio="16:9",
    )
    return normalized, alignment, timeline, result


def test_pacing_policy_accepts_catalog_alias_and_defaults_to_350ms():
    parsed = __import__("app.contracts.caption_voice_quality", fromlist=["NarrationPacingPolicy"]).NarrationPacingPolicy.model_validate(pacing_policy())
    assert parsed.active_speech_silence_gap_threshold_ms == 350


def test_pacing_analyzer_uses_alignment_and_pause_evidence_deterministically():
    source = "A calm workflow, keeps one approved script moving. Then final media stays synchronized."
    normalized, alignment, _ = authority_components(
        source, word_ms=300, base_gap_ms=80,
        punctuation_gaps={",": 220, ".": 420},
    )
    period_token = next(
        token.token_id for token, following in zip(normalized.spoken_tokens, normalized.spoken_tokens[1:])
        if "." in normalized.spoken_text[token.spoken_span.end:following.spoken_span.start]
    )
    analysis = NarrationAudioAnalysis(
        audio_asset_ref=alignment.audio_asset_ref,
        audio_duration_ms=alignment.audio_duration_ms,
        waveform_summary={"peak_dbfs": -2.0, "rms_dbfs": -18.0},
    )
    kwargs = dict(
        normalized=normalized, alignment=alignment, audio_analysis=analysis,
        policy=pacing_policy(), section_boundary_after_token_ids=[period_token],
    )
    first = NarrationPacingAnalyzer().analyze(**kwargs)
    second = NarrationPacingAnalyzer().analyze(**kwargs)
    assert first == second
    assert first.metrics.comma_pause_ms_median == 220
    assert first.metrics.section_pause_ms_median == 420
    assert first.metrics.spoken_word_count == len(normalized.spoken_tokens)
    assert first.waveform_summary["peak_dbfs"] == -2.0


def test_pacing_fast_and_short_pauses_block_with_required_reason_codes():
    normalized, alignment, _ = authority_components("One, two. Three four.", word_ms=100, base_gap_ms=20, punctuation_gaps={",": 100, ".": 120})
    report = NarrationPacingAnalyzer().analyze(
        normalized=normalized, alignment=alignment,
        audio_analysis=NarrationAudioAnalysis(audio_asset_ref=alignment.audio_asset_ref, audio_duration_ms=alignment.audio_duration_ms),
        policy=pacing_policy(),
    )
    result = NarrationPacingGate().evaluate(report, pacing_policy())
    assert result.status == "BLOCK"
    assert {"PACE_ACTIVE_TOO_FAST", "PACE_DELIVERED_TOO_FAST", "PACE_COMMA_PAUSE_SHORT", "PACE_SENTENCE_PAUSE_SHORT"} <= set(result.reason_codes)


def test_extreme_slow_blocks_but_moderately_slow_routes_review():
    normalized, alignment, _ = authority_components("One two three four five.")
    report = NarrationPacingAnalyzer().analyze(
        normalized=normalized, alignment=alignment,
        audio_analysis=NarrationAudioAnalysis(audio_asset_ref=alignment.audio_asset_ref, audio_duration_ms=alignment.audio_duration_ms),
        policy=pacing_policy(),
    )
    slow = report.model_copy(update={"metrics": report.metrics.model_copy(update={"active_speech_wpm": 110, "delivered_wpm": 110, "hook_first_8s_active_wpm": 110})})
    review = report.model_copy(update={"metrics": report.metrics.model_copy(update={"active_speech_wpm": 130, "delivered_wpm": 115, "hook_first_8s_active_wpm": 130})})
    assert NarrationPacingGate().evaluate(slow, pacing_policy()).status == "BLOCK"
    assert "PACE_EXTREME_SLOW" in NarrationPacingGate().evaluate(slow, pacing_policy()).reason_codes
    assert NarrationPacingGate().evaluate(review, pacing_policy()).status == "REVIEW_REQUIRED"


def test_pacing_correction_allows_only_one_explicitly_authorized_speed_regeneration():
    gate = CreativeQualityGateResult(
        gate="NarrationPacingGate",
        status="REVIEW_REQUIRED",
        reason_codes=["PACE_DELIVERED_TOO_FAST"],
    )
    planner = NarrationPacingCorrectionPlanner()
    allowed = planner.plan(
        pacing_gate=gate,
        policy=pacing_policy(),
        current_model_supports_speed=True,
        one_provider_regeneration_authorized=True,
    )
    blocked = planner.plan(
        pacing_gate=gate,
        policy=pacing_policy(),
        current_model_supports_speed=True,
        one_provider_regeneration_authorized=False,
    )
    assert allowed.action == "ONE_CONTROLLED_SPEED_REGENERATION"
    assert allowed.provider_speed_regeneration_count == 1
    assert allowed.remeasure_final_audio_required is True
    assert blocked.action == "BLOCK_NO_AUTHORIZED_REGENERATION"
    assert "PAID_TTS_REGENERATION_NOT_AUTHORIZED" in blocked.reason_codes


def test_pacing_correction_routes_density_and_pause_defects_to_script_rewrite():
    gate = CreativeQualityGateResult(
        gate="NarrationPacingGate",
        status="BLOCK",
        reason_codes=["PACE_ACTIVE_TOO_FAST", "PACE_SENTENCE_PAUSE_SHORT"],
    )
    decision = NarrationPacingCorrectionPlanner().plan(
        pacing_gate=gate,
        policy=pacing_policy(),
        current_model_supports_speed=True,
        one_provider_regeneration_authorized=True,
        text_density_excessive=True,
    )
    assert decision.action == "SCRIPT_PACING_REWRITE_REQUIRED"
    assert "SCRIPT_PACING_REWRITE_REQUIRED" in decision.reason_codes
    assert decision.provider_speed_regeneration_count == 0
    assert decision.ffmpeg_atempo_allowed is False


@pytest.mark.parametrize(
    ("delta", "human_approved", "expected_action"),
    [
        (2.0, False, "EMERGENCY_ATEMPO"),
        (2.1, False, "HUMAN_ATEMPO_APPROVAL_REQUIRED"),
        (2.1, True, "EMERGENCY_ATEMPO"),
        (3.1, True, "SCRIPT_PACING_REWRITE_REQUIRED"),
    ],
)
def test_emergency_atempo_policy_is_explicit_and_fail_closed(
    delta: float,
    human_approved: bool,
    expected_action: str,
):
    gate = CreativeQualityGateResult(
        gate="NarrationPacingGate",
        status="REVIEW_REQUIRED",
        reason_codes=["PACE_DELIVERED_TOO_FAST"],
    )
    decision = NarrationPacingCorrectionPlanner().plan(
        pacing_gate=gate,
        policy=pacing_policy(),
        current_model_supports_speed=False,
        one_provider_regeneration_authorized=False,
        emergency_atempo_delta_percent=delta,
        human_atempo_approval=human_approved,
    )
    assert decision.action == expected_action
    assert decision.ffmpeg_atempo_allowed is (expected_action == "EMERGENCY_ATEMPO")


def test_emergency_atempo_cannot_hide_short_pause_or_dense_script():
    gate = CreativeQualityGateResult(
        gate="NarrationPacingGate",
        status="BLOCK",
        reason_codes=["PACE_COMMA_PAUSE_SHORT"],
    )
    decision = NarrationPacingCorrectionPlanner().plan(
        pacing_gate=gate,
        policy=pacing_policy(),
        current_model_supports_speed=False,
        one_provider_regeneration_authorized=False,
        emergency_atempo_delta_percent=1.0,
    )
    assert decision.action == "SCRIPT_PACING_REWRITE_REQUIRED"
    assert "ATEMPO_CANNOT_HIDE_SCRIPT_DEFECT" in decision.reason_codes


def test_caption_compiler_maps_all_tokens_adds_explicit_lines_and_rehashes_timeline():
    normalized, _, original, result = compiled_components()
    assert result.timeline.timeline_hash != original.timeline_hash
    assert result.timeline.qc_metrics["caption_compilation_hash"] == result.track.content_hash
    assert result.timeline.qc_metrics["caption_compilation_ref"].endswith(result.track.content_hash)
    assert [token for cue in result.track.cues for token in cue.spoken_token_ids] == [item.token_id for item in normalized.spoken_tokens]
    assert all(1 <= len(cue.caption_lines) <= 2 for cue in result.track.cues)
    assert all(event.text for event in result.track.ass_events)
    assert "\n" in result.track.srt_text
    assert result.track.compilation_gate.status == "PASS"


def test_caption_compiler_holds_only_final_cue_through_bounded_canonical_tail_silence():
    normalized, alignment, timeline = authority_components(trailing_ms=546)
    original_words = alignment.verified_words
    result = ReadableCaptionCompiler().compile(
        normalized=normalized,
        alignment=alignment,
        timeline=timeline,
        policy=caption_policy(),
        final_cue_trailing_hold_policy=final_cue_trailing_hold_policy(),
        aspect_ratio="16:9",
    )
    evidence = result.track.final_cue_trailing_hold
    assert evidence is not None
    assert evidence.status == "APPLIED"
    assert evidence.hold_duration_ms == 546
    assert evidence.caption_end_before_ms == alignment.verified_words[-1].end_ms
    assert evidence.caption_end_after_ms == alignment.audio_duration_ms
    assert evidence.spoken_token_ids_unchanged is True
    assert evidence.spoken_word_timing_unchanged is True
    assert alignment.verified_words == original_words
    assert result.track.cues[-1].caption_end_ms == alignment.audio_duration_ms
    assert [
        token_id for cue in result.track.cues for token_id in cue.spoken_token_ids
    ] == [token.token_id for token in normalized.spoken_tokens]
    assert result.timeline.qc_metrics["caption_final_cue_trailing_hold"] == evidence.model_dump(
        mode="json"
    )
    sync = CaptionAudioSyncGate().evaluate(
        timeline=result.timeline,
        alignment=alignment,
        policy=sync_policy(),
    )
    assert sync.status == "PASS"
    assert sync.metrics["raw_final_cue_end_offset_ms"] == 546
    assert sync.metrics["authorized_final_cue_trailing_hold_ms"] == 546
    assert TimelineDriftGate().evaluate(
        timeline=result.timeline,
        final_audio_duration_ms=alignment.audio_duration_ms,
        policy=sync_policy(),
    ).status == "PASS"


def test_caption_compiler_blocks_trailing_hold_above_explicit_maximum():
    normalized, alignment, timeline = authority_components(trailing_ms=1_001)
    with pytest.raises(ValueError, match="CAPTION_TRAILING_HOLD_EXCEEDS_POLICY"):
        ReadableCaptionCompiler().compile(
            normalized=normalized,
            alignment=alignment,
            timeline=timeline,
            policy=caption_policy(),
            final_cue_trailing_hold_policy=final_cue_trailing_hold_policy(),
            aspect_ratio="16:9",
        )


def test_caption_compiler_blocks_trailing_hold_when_canonical_endpoint_is_inconsistent():
    normalized, alignment, timeline = authority_components(trailing_ms=546)
    final_segment = timeline.segments[-1]
    invalid_scene_end = timeline.audio_duration_ms - 1
    invalid_timeline = timeline.model_copy(
        update={
            "segments": [
                *timeline.segments[:-1],
                final_segment.model_copy(
                    update={
                        "scene_end_ms": invalid_scene_end,
                        "target_scene_duration_ms": invalid_scene_end
                        - final_segment.scene_start_ms,
                    }
                ),
            ]
        }
    )
    with pytest.raises(
        ValueError,
        match="CAPTION_TRAILING_HOLD_CANONICAL_ENDPOINT_INVALID",
    ):
        ReadableCaptionCompiler().compile(
            normalized=normalized,
            alignment=alignment,
            timeline=invalid_timeline,
            policy=caption_policy(),
            final_cue_trailing_hold_policy=final_cue_trailing_hold_policy(),
            aspect_ratio="16:9",
        )


def test_caption_sync_blocks_final_cue_held_to_audio_end_without_compiler_evidence():
    _, alignment, _, compiled = compiled_components()
    segment = compiled.timeline.segments[-1]
    final_cue = segment.caption_cues[-1]
    audio_end = alignment.audio_duration_ms + 546
    extended_cue = final_cue.model_copy(update={"caption_end_ms": audio_end})
    extended_segment = segment.model_copy(
        update={
            "caption_end_ms": audio_end,
            "caption_cues": [*segment.caption_cues[:-1], extended_cue],
        }
    )
    unauthorized_timeline = compiled.timeline.model_copy(
        update={
            "audio_duration_ms": audio_end,
            "segments": [
                *compiled.timeline.segments[:-1],
                extended_segment.model_copy(
                    update={
                        "scene_end_ms": audio_end,
                        "target_scene_duration_ms": audio_end - extended_segment.scene_start_ms,
                    }
                ),
            ],
        }
    )
    unauthorized_alignment = alignment.model_copy(update={"audio_duration_ms": audio_end})
    gate = CaptionAudioSyncGate().evaluate(
        timeline=unauthorized_timeline,
        alignment=unauthorized_alignment,
        policy=sync_policy(),
    )
    assert gate.status == "BLOCK"
    assert "SYNC_UNAUTHORIZED_FINAL_CUE_TRAILING_HOLD" in gate.reason_codes


def test_display_caption_allows_acronym_and_number_recompaction_but_blocks_rewrite():
    normalized, alignment, timeline = authority_components("AI costs $12.")
    ids = [item.token_id for item in normalized.spoken_tokens]
    display = DisplayCaptionText(
        spoken_text_hash=normalized.spoken_text_hash,
        display_text="AI costs $12.",
        tokens=[
            DisplayCaptionToken(display_token_id="d1", text="AI", spoken_token_ids=ids[:2], transform_reason_code="APPROVED_BRANDED_CASING"),
            DisplayCaptionToken(display_token_id="d2", text="costs", spoken_token_ids=[ids[2]]),
            DisplayCaptionToken(display_token_id="d3", text="$12", spoken_token_ids=ids[3:], transform_reason_code="KNOWN_NUMBER_RECOMPACTION"),
        ],
        content_hash="display-fixture",
    )
    result = ReadableCaptionCompiler().compile(
        normalized=normalized, alignment=alignment, timeline=timeline,
        display_caption_text=display, policy=caption_policy(), aspect_ratio="16:9",
    )
    assert result.track.cues[0].caption_lines == ["AI costs $12."]
    bad = display.model_copy(update={"tokens": [display.tokens[0].model_copy(update={"text": "Robots", "transform_reason_code": None}), *display.tokens[1:]]})
    with pytest.raises(ValueError, match="CAPTION_SEMANTIC_REWRITE_BLOCKED"):
        ReadableCaptionCompiler().compile(
            normalized=normalized, alignment=alignment, timeline=timeline,
            display_caption_text=bad, policy=caption_policy(), aspect_ratio="16:9",
        )


def test_line_break_prefers_clause_boundary_and_does_not_split_preposition_or_name():
    policy = CaptionStylePolicy.model_validate(caption_policy(long_cpl=22))
    units = [
        SimpleNamespace(display_token=CaptionDisplayToken(display_token_id=f"d{i}", text=text, spoken_token_ids=[f"s{i}"]), rendered_text=text, spoken_token_ids=(f"s{i}",))
        for i, text in enumerate(["Morgan", "Lee", "explains,", "through", "one", "approved", "workflow"])
    ]
    lines = ReadableCaptionCompiler._wrap_lines(units, policy.longform_16_9)
    assert len(lines) == 2
    assert lines[0].endswith("explains,")
    assert not lines[0].endswith("through")
    assert "Morgan\nLee" not in "\n".join(lines)


def test_compilation_and_coverage_gates_block_three_lines_and_missing_token():
    normalized, _, _, result = compiled_components()
    cue = result.track.cues[0]
    three_line_reading = cue.reading_metrics.model_copy(update={"line_count": 3, "chars_per_line": [3, 3, 3]})
    bad_cue = cue.model_copy(update={"caption_lines": ["one", "two", "three"], "reading_metrics": three_line_reading})
    compilation = CaptionCompilationGate().evaluate(
        cues=[bad_cue, *result.track.cues[1:]], normalized=normalized,
        timeline=result.timeline, policy=caption_policy(),
    )
    assert compilation.status == "BLOCK"
    assert "CAPTION_MORE_THAN_TWO_LINES" in compilation.reason_codes
    missing = result.timeline.model_copy(update={"segments": [result.timeline.segments[0].model_copy(update={"caption_cues": [cue.model_copy(update={"spoken_token_ids": cue.spoken_token_ids[:-1]})]})]})
    coverage = CaptionCoverageGate().evaluate(normalized=normalized, timeline=missing, policy=sync_policy())
    assert coverage.status == "BLOCK"
    assert "SYNC_COVERAGE_GAP" in coverage.reason_codes


def test_layout_gate_blocks_high_cps_and_bbox_overflow():
    _, _, _, result = compiled_components()
    cue = result.track.cues[0]
    cue = cue.model_copy(update={"reading_metrics": cue.reading_metrics.model_copy(update={"characters_per_second": 21.0})})
    bbox = CaptionBBoxMetrics(
        cue_id=cue.cue_id, frame_width=1920, frame_height=1080,
        x=100, y=900, width=1500, height=100, block_width_ratio=0.78125,
        left_margin_ratio=0.05, right_margin_ratio=0.16, top_margin_ratio=0.83,
        bottom_margin_ratio=0.074, font_scale=0.047, line_count=cue.reading_metrics.line_count,
        cpl=cue.reading_metrics.max_chars_per_line, cps=21.0,
        duration_seconds=cue.reading_metrics.duration_seconds,
    )
    gate = CaptionLayoutGate().evaluate(cues=[cue], bbox_metrics=[bbox], policy=caption_policy(), aspect_ratio="16:9")
    assert gate.status == "BLOCK"
    assert {"CAPTION_READING_SPEED_TOO_HIGH", "CAPTION_BBOX_OVERFLOW"} <= set(gate.reason_codes)


def test_safe_area_gate_reviews_and_blocks_bottom_margin_and_subject_overlap():
    base = dict(
        cue_id="c1", frame_width=1920, frame_height=1080, x=300, y=900,
        width=1000, height=80, block_width_ratio=0.52, left_margin_ratio=0.15,
        right_margin_ratio=0.32, top_margin_ratio=0.83, font_scale=0.047,
        line_count=1, cpl=20, cps=10, duration_seconds=2,
    )
    review = CaptionBBoxMetrics(**base, bottom_margin_ratio=0.06)
    blocked = CaptionBBoxMetrics(**base, bottom_margin_ratio=0.04, required_safe_zone_overlap=True)
    assert CaptionSafeAreaGate().evaluate(bbox_metrics=[review], policy=caption_policy(), aspect_ratio="16:9").status == "REVIEW_REQUIRED"
    result = CaptionSafeAreaGate().evaluate(bbox_metrics=[blocked], policy=caption_policy(), aspect_ratio="16:9")
    assert result.status == "BLOCK"
    assert {"CAPTION_UNSAFE_BOTTOM_MARGIN", "CAPTION_REQUIRED_VISUAL_SAFE_ZONE_OVERLAP"} <= set(result.reason_codes)


def test_bbox_preflight_uses_injected_libass_alpha_bbox_and_relative_style(tmp_path: Path):
    _, _, _, compiled = compiled_components()
    observed = {}

    def fake_runner(argv, **kwargs):
        observed["argv"] = argv
        ass_filter = argv[argv.index("-vf") + 1]
        ass_path = Path(ass_filter.split("filename='")[1].split("':alpha")[0].replace("\\:", ":"))
        observed["ass"] = ass_path.read_text(encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="[Parsed_bbox_3] n:0 pts:0 x1:40 x2:279 y1:130 y2:159 w:240 h:30 crop=240:30:40:130")

    report = CaptionBoundsPreflight(ffmpeg_binary="fixture-ffmpeg", runner=fake_runner).preflight(
        cues=[compiled.track.cues[0]], frame_width=320, frame_height=180,
        policy=caption_policy(), aspect_ratio="16:9", evidence_dir=tmp_path,
    )
    assert "alphaextract,bbox" in observed["argv"][observed["argv"].index("-vf") + 1]
    assert observed["argv"][observed["argv"].index("-i") + 1].endswith(",format=rgba")
    assert "PlayResX: 320" in observed["ass"] and "PlayResY: 180" in observed["ass"]
    assert "FontSize,PrimaryColour" not in observed["ass"]
    assert report.cue_metrics[0].width == 240
    assert report.cue_metrics[0].font_scale == pytest.approx(0.047)


@pytest.mark.skipif(not Path(FFMPEG_FULL).is_file(), reason="ffmpeg-full unavailable")
def test_real_ffmpeg_libass_bbox_preflight_produces_nonempty_geometry(tmp_path: Path):
    _, _, _, compiled = compiled_components()
    report = CaptionBoundsPreflight(ffmpeg_binary=FFMPEG_FULL).preflight(
        cues=[compiled.track.cues[0]], frame_width=640, frame_height=360,
        policy=caption_policy(), aspect_ratio="16:9", evidence_dir=tmp_path,
    )
    metric = report.cue_metrics[0]
    assert 0 < metric.width < metric.frame_width
    assert 0 < metric.height < metric.frame_height
    assert metric.block_width_ratio < 1.0
    assert metric.bottom_margin_ratio > 0.0
    assert report.safe_area_gate.status == "PASS"
    assert Path(report.cue_metrics[0].preview_frame_ref).is_file()


def test_caption_sync_passes_exact_alignment_and_blocks_lead_lag_overlap_and_extra_token():
    _, alignment, _, result = compiled_components()
    assert CaptionAudioSyncGate().evaluate(timeline=result.timeline, alignment=alignment, policy=sync_policy()).status == "PASS"
    segment = result.timeline.segments[0]
    cue = segment.caption_cues[0]
    shifted = cue.model_copy(update={"caption_start_ms": cue.caption_start_ms + 301})
    bad_segment = segment.model_copy(update={"caption_cues": [shifted, *segment.caption_cues[1:]]})
    bad_timeline = result.timeline.model_copy(update={"segments": [bad_segment]})
    gate = CaptionAudioSyncGate().evaluate(timeline=bad_timeline, alignment=alignment, policy=sync_policy())
    assert gate.status == "BLOCK" and "SYNC_START_OFFSET" in gate.reason_codes

    duplicate = cue.model_copy(update={"cue_id": "duplicate", "caption_start_ms": cue.caption_start_ms, "caption_end_ms": cue.caption_end_ms})
    overlap_timeline = result.timeline.model_copy(update={"segments": [segment.model_copy(update={"caption_cues": [cue, duplicate, *segment.caption_cues[1:]]})]})
    overlap = CaptionAudioSyncGate().evaluate(timeline=overlap_timeline, alignment=alignment, policy=sync_policy())
    assert overlap.status == "BLOCK"
    assert {"SYNC_CUE_OVERLAP", "SYNC_EXTRA_TOKEN"} <= set(overlap.reason_codes)


def test_timeline_drift_gate_uses_caption_scene_timeline_and_audio_endpoints():
    _, alignment, _, result = compiled_components()
    assert TimelineDriftGate().evaluate(timeline=result.timeline, final_audio_duration_ms=alignment.audio_duration_ms, policy=sync_policy()).status == "PASS"
    drifted = result.timeline.model_copy(update={"audio_duration_ms": alignment.audio_duration_ms + 251})
    gate = TimelineDriftGate().evaluate(timeline=drifted, final_audio_duration_ms=alignment.audio_duration_ms, policy=sync_policy())
    assert gate.status == "BLOCK"
    assert {"SYNC_END_DRIFT", "SYNC_PARALLEL_TIMELINE"} <= set(gate.reason_codes)


def test_bbox_application_rehashes_timeline_and_attaches_gate_evidence():
    _, _, _, compiled = compiled_components()

    def fake_runner(argv, **kwargs):
        return SimpleNamespace(returncode=0, stderr="[Parsed_bbox_3] x1:200 x2:439 y1:290 y2:319 w:240 h:30")

    preflight = CaptionBoundsPreflight(ffmpeg_binary="fixture-ffmpeg", runner=fake_runner)
    report = preflight.preflight(
        cues=compiled.track.cues, frame_width=640, frame_height=360,
        policy=caption_policy(), aspect_ratio="16:9",
    )
    updated = preflight.apply_to_timeline(compiled.timeline, report)
    assert updated.timeline_hash != compiled.timeline.timeline_hash
    assert updated.qc_metrics["caption_bbox_preflight_hash"] == report.content_hash
    assert all(cue.bbox_metrics for cue in updated.segments[0].caption_cues)


# CQR1 local golden renderer fixtures. These deliberately remain in this focused
# test module so the caption compiler and the renderer are exercised as one
# canonical chain without introducing a second subtitle fixture.
FFPROBE_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
_CQR1_CAPTION_GATES = (
    "NarrationPacingGate",
    "CaptionCompilationGate",
    "CaptionLayoutGate",
    "CaptionSafeAreaGate",
    "CaptionAudioSyncGate",
    "CaptionCoverageGate",
    "TimelineDriftGate",
)


def golden_strict_plan(timeline, **changes) -> NativeRenderPlan:
    metrics = timeline.qc_metrics
    scenes = [
        NativeRenderScene(
            scene_id=segment.segment_id,
            source_segment_ids=[segment.segment_id],
            narration_start_ms=segment.scene_start_ms,
            narration_end_ms=segment.scene_end_ms,
            duration_ms=segment.target_scene_duration_ms,
            visual_treatment="NATIVE_SLIDE",
            layout_type="TITLE",
            animation_type="HOLD_STATIC",
            originality_role="HOOK",
        )
        for segment in timeline.segments
    ]
    payload = {
        "plan_id": "cqr1b-local-golden-plan",
        "plan_version": 1,
        "package_id": timeline.package_id,
        "video_project_id": timeline.project_id,
        "company_id": "fixture-company",
        "channel_id": timeline.channel_id,
        "channel_profile_version_id": "fixture-profile-v1",
        "effective_context_snapshot_id": "fixture-context-v1",
        "effective_context_hash": "fixture-context-hash",
        "format_identity_contract_ref": "fixture-format-ref",
        "format_identity_contract_hash": "fixture-format-hash",
        "episode_originality_manifest_ref": "fixture-originality-ref",
        "episode_originality_manifest_hash": "fixture-originality-hash",
        "script_ref": "fixture-script-ref",
        "script_hash": "fixture-script-hash",
        # Strict mode uses the canonical compilation artifact identity here. It
        # must never point at an independently timed SRT file.
        "srt_ref": metrics["caption_compilation_ref"],
        "srt_hash": metrics["caption_compilation_hash"],
        "temporal_authority_mode": "CANONICAL_STRICT",
        "canonical_media_timeline_ref": f"canonical-timeline:{timeline.timeline_hash}",
        "canonical_media_timeline_hash": timeline.timeline_hash,
        "canonical_audio_asset_ref": timeline.audio_asset_id,
        "canonical_caption_compilation_ref": metrics["caption_compilation_ref"],
        "canonical_caption_compilation_hash": metrics["caption_compilation_hash"],
        "canonical_caption_render_payload_hash": metrics["caption_render_payload_hash"],
        "scene_timing_source": "CANONICAL_MEDIA_TIMELINE",
        "caption_timing_source": "CANONICAL_MEDIA_TIMELINE",
        "visual_plan_ref": "fixture-visual-plan-ref",
        "visual_plan_hash": "fixture-visual-plan-hash",
        "creative_gate_results": {
            name: {"result": "PASS"} for name in _CQR1_CAPTION_GATES
        },
        "canvas_spec": CanvasSpec(width=1920, height=1080),
        "scenes": scenes,
        "global_motion_policy": {"motion_pack": "NativeMotionPack_v1"},
        # A deliberately irrelevant legacy value proves strict compilation takes
        # its frozen render style from the canonical timeline instead.
        "caption_policy": {"legacy_font_size_px": 999},
        "audio_policy": {"narration_asset_ref": timeline.audio_asset_id},
        "output_profiles": ["YT_LONG_1080P30_SDR_H264_VT"],
        "purpose": "CQR1_LOCAL_GOLDEN_FIXTURE",
        "production_eligible": False,
        "status": "APPROVED",
        "created_at": datetime(2026, 7, 14, tzinfo=UTC),
        "created_by": "cqr1b-golden-fixture",
    }
    payload.update(changes)
    plan = NativeRenderPlan(**payload)
    plan.content_hash = canonical_plan_hash(plan)
    return plan


def golden_compilation(source: str = "Calm captions align."):
    normalized, alignment, original, compiled = compiled_components(source)
    plan = golden_strict_plan(compiled.timeline)
    manifest = NativeMotionCompiler().compile(
        plan,
        canonical_timeline=compiled.timeline,
    )
    return normalized, alignment, original, compiled, plan, manifest


def test_comfortable_measured_pacing_passes_all_initial_bands():
    source = "A calm workflow, keeps one approved script moving. Then final media stays synchronized."
    normalized, alignment, _ = authority_components(
        source,
        word_ms=320,
        base_gap_ms=80,
        punctuation_gaps={",": 220, ".": 420},
    )
    report = NarrationPacingAnalyzer().analyze(
        normalized=normalized,
        alignment=alignment,
        audio_analysis=NarrationAudioAnalysis(
            audio_asset_ref=alignment.audio_asset_ref,
            audio_duration_ms=alignment.audio_duration_ms,
        ),
        policy=pacing_policy(),
    )
    gate = NarrationPacingGate().evaluate(report, pacing_policy())
    assert gate.status == "PASS"
    assert 145 <= report.metrics.active_speech_wpm <= 170
    assert 130 <= report.metrics.delivered_wpm <= 155
    assert report.metrics.comma_pause_ms_median == 220
    assert report.metrics.sentence_pause_ms_median == 420


def test_measured_fast_hook_and_short_section_pause_block():
    normalized, alignment, _ = authority_components(
        "One two. Three four.",
        word_ms=100,
        base_gap_ms=20,
        punctuation_gaps={".": 200},
    )
    section_token_id = next(
        token.token_id
        for token, following in zip(normalized.spoken_tokens, normalized.spoken_tokens[1:])
        if "." in normalized.spoken_text[token.spoken_span.end:following.spoken_span.start]
    )
    report = NarrationPacingAnalyzer().analyze(
        normalized=normalized,
        alignment=alignment,
        audio_analysis=NarrationAudioAnalysis(
            audio_asset_ref=alignment.audio_asset_ref,
            audio_duration_ms=alignment.audio_duration_ms,
        ),
        policy=pacing_policy(),
        section_boundary_after_token_ids=[section_token_id],
    )
    gate = NarrationPacingGate().evaluate(report, pacing_policy())
    assert gate.status == "BLOCK"
    assert report.metrics.section_pause_ms_median == 200
    assert {"PACE_HOOK_TOO_FAST", "PACE_SECTION_PAUSE_SHORT"} <= set(gate.reason_codes)


@pytest.mark.parametrize("duration_seconds", [0.79, 7.01])
def test_caption_layout_blocks_flashing_and_overlong_cues(duration_seconds: float):
    _, _, _, compiled = compiled_components("Calm captions align.")
    cue = compiled.track.cues[0]
    cue = cue.model_copy(
        update={
            "reading_metrics": cue.reading_metrics.model_copy(
                update={"duration_seconds": duration_seconds}
            )
        }
    )
    bbox = CaptionBBoxMetrics(
        cue_id=cue.cue_id,
        frame_width=1920,
        frame_height=1080,
        x=500,
        y=880,
        width=700,
        height=80,
        block_width_ratio=700 / 1920,
        left_margin_ratio=500 / 1920,
        right_margin_ratio=720 / 1920,
        top_margin_ratio=880 / 1080,
        bottom_margin_ratio=120 / 1080,
        font_scale=0.047,
        line_count=cue.reading_metrics.line_count,
        cpl=cue.reading_metrics.max_chars_per_line,
        cps=cue.reading_metrics.characters_per_second,
        duration_seconds=duration_seconds,
    )
    gate = CaptionLayoutGate().evaluate(
        cues=[cue],
        bbox_metrics=[bbox],
        policy=caption_policy(),
        aspect_ratio="16:9",
    )
    assert gate.status == "BLOCK"
    assert "CAPTION_DURATION_OUTSIDE_POLICY" in gate.reason_codes


def test_portrait_caption_style_and_preflight_margin_are_shared_byte_for_byte():
    normalized, alignment, timeline = authority_components("Portrait captions stay readable.")
    compiled = ReadableCaptionCompiler().compile(
        normalized=normalized,
        alignment=alignment,
        timeline=timeline,
        policy=caption_policy(),
        aspect_ratio="9:16",
    )
    style = compiled.timeline.qc_metrics["caption_render_style"]
    assert style["aspect_ratio"] == "9:16"
    assert style["font_scale"] == pytest.approx(0.05)
    assert style["bottom_safe_margin"] == pytest.approx(0.12)
    final_document = build_caption_ass_document(
        cues=compiled.track.cues,
        frame_width=1080,
        frame_height=1920,
        render_style=style,
    )
    parsed_policy = CaptionStylePolicy.model_validate(caption_policy())
    preflight_document = CaptionBoundsPreflight._ass_document(
        cue=compiled.track.cues[0],
        frame_width=1080,
        frame_height=1920,
        font_scale=style["font_scale"],
        bottom_margin_ratio=style["bottom_safe_margin"],
        policy=parsed_policy,
    )
    final_header = final_document.encode().split(b"[Events]\n", 1)[0]
    preflight_header = preflight_document.encode().split(b"[Events]\n", 1)[0]
    assert final_header == preflight_header
    assert next(line for line in final_document.splitlines() if line.startswith("Style: CQR1")).endswith(
        ",0,0,233,1"
    )


def test_strict_golden_manifest_uses_only_canonical_caption_hash_style_and_ref():
    _, _, _, compiled, plan, manifest = golden_compilation()
    timeline = compiled.timeline
    cues = [cue for segment in timeline.segments for cue in segment.caption_cues]
    metrics = timeline.qc_metrics
    assert metrics["caption_render_payload_hash"] == canonical_caption_render_hash(cues)
    assert plan.srt_ref == metrics["caption_compilation_ref"]
    assert plan.srt_hash == metrics["caption_compilation_hash"]
    assert manifest.canonical_caption_compilation_ref == metrics["caption_compilation_ref"]
    assert manifest.canonical_caption_compilation_hash == metrics["caption_compilation_hash"]
    assert manifest.canonical_caption_render_payload_hash == metrics["caption_render_payload_hash"]
    assert manifest.normalized_caption == metrics["caption_render_style"]
    assert manifest.caption_schedule["render_style"] == metrics["caption_render_style"]
    assert manifest.caption_schedule["independent_srt_used"] is False
    assert "srt_ref" not in manifest.caption_schedule
    assert manifest.expected_input_refs == []


@pytest.mark.skipif(
    not Path(FFMPEG_FULL).is_file() or not Path(FFPROBE_FULL).is_file(),
    reason="ffmpeg-full/ffprobe unavailable",
)
def test_strict_command_uses_timeline_duration_and_preflight_identical_ass_style(tmp_path: Path):
    _, _, _, compiled, _, manifest = golden_compilation()
    timeline = compiled.timeline
    command = FFmpegCommandBuilder(
        tmp_path,
        ffmpeg=FFMPEG_FULL,
        ffprobe=FFPROBE_FULL,
    ).build_synthetic(manifest, run_key="cqr1b-golden-command", duration_seconds=99)
    expected_seconds = timeline.audio_duration_ms / 1000
    assert "-t" not in command.sanitized_argv
    assert "-shortest" in command.sanitized_argv
    assert command.canonical_duration_ms == timeline.audio_duration_ms
    assert command.expected_qc["expected_duration_seconds"] == expected_seconds
    assert any(
        value.startswith("color=") and value.endswith(f":d={expected_seconds}")
        for value in command.sanitized_argv
    )
    assert any(
        value.startswith("sine=") and value.endswith(f"duration={expected_seconds}")
        for value in command.sanitized_argv
    )
    final_ass = Path(command.generated_caption_path).read_bytes()
    style = timeline.qc_metrics["caption_render_style"]
    preflight_ass = CaptionBoundsPreflight._ass_document(
        cue=compiled.track.cues[0],
        frame_width=1920,
        frame_height=1080,
        font_scale=style["font_scale"],
        bottom_margin_ratio=style["bottom_safe_margin"],
        policy=CaptionStylePolicy.model_validate(caption_policy()),
    ).encode()
    assert final_ass.split(b"[Events]\n", 1)[0] == preflight_ass.split(b"[Events]\n", 1)[0]
    assert command.canonical_caption_render_payload_hash == timeline.qc_metrics[
        "caption_render_payload_hash"
    ]


def test_caption_lag_over_block_threshold_is_measured_from_verified_alignment():
    _, alignment, _, compiled = compiled_components("Calm captions align.")
    segment = compiled.timeline.segments[0]
    cue = segment.caption_cues[0]
    lagged = cue.model_copy(update={"caption_start_ms": cue.caption_start_ms + 301})
    timeline = compiled.timeline.model_copy(
        update={
            "segments": [
                segment.model_copy(update={"caption_cues": [lagged, *segment.caption_cues[1:]]})
            ]
        }
    )
    gate = CaptionAudioSyncGate().evaluate(
        timeline=timeline,
        alignment=alignment,
        policy=sync_policy(),
    )
    assert gate.status == "BLOCK"
    assert gate.metrics["max_abs_start_offset_ms"] == 301
    assert "SYNC_START_OFFSET" in gate.reason_codes


def test_mutated_caption_line_with_stale_render_hash_is_rejected():
    _, _, _, compiled, _, _ = golden_compilation()
    timeline = compiled.timeline
    segment = timeline.segments[0]
    cue = segment.caption_cues[0]
    changed_line = "Mutated after canonical caption compilation."
    changed_cue = cue.model_copy(update={"caption_lines": [changed_line]})
    changed_segment = segment.model_copy(
        update={
            "caption_lines": [changed_line],
            "caption_cues": [changed_cue, *segment.caption_cues[1:]],
        }
    )
    timeline_payload = timeline.model_dump(mode="json", exclude={"timeline_hash"})
    timeline_payload["segments"] = [changed_segment.model_dump(mode="json")]
    # The timeline itself is consistently rehashed, while the frozen render
    # payload hash intentionally remains stale to exercise the cross-link gate.
    mutated = timeline.__class__(
        **timeline_payload,
        timeline_hash=stable_hash(timeline_payload),
    )
    plan = golden_strict_plan(mutated)
    with pytest.raises(ValueError, match="CAPTION_RENDER_PAYLOAD_HASH_MISMATCH"):
        NativeMotionCompiler().compile(plan, canonical_timeline=mutated)


def test_strict_builder_rejects_scene_audio_endpoint_mismatch(tmp_path: Path):
    _, _, _, _, _, manifest = golden_compilation()
    scenes = [dict(item) for item in manifest.compiled_scenes]
    scenes[-1]["end_ms"] -= 1
    scenes[-1]["duration_ms"] -= 1
    mismatched = manifest.model_copy(update={"compiled_scenes": scenes})
    with pytest.raises(ValueError, match="TEMPORAL_AUDIO_ENDPOINT_MISMATCH"):
        FFmpegCommandBuilder(
            tmp_path,
            ffmpeg="fixture-ffmpeg",
            ffprobe="fixture-ffprobe",
        ).build_synthetic(mismatched, run_key="endpoint-mismatch")


@pytest.mark.parametrize("unsafe_line", [r"literal\Nsequence", "embedded\nnewline"])
def test_strict_ass_rejects_literal_control_sequence_and_embedded_newline(
    tmp_path: Path,
    unsafe_line: str,
):
    _, _, _, _, _, manifest = golden_compilation()
    schedule = dict(manifest.caption_schedule)
    cues = [dict(item) for item in schedule["cues"]]
    cues[0]["caption_lines"] = [unsafe_line]
    schedule["cues"] = cues
    unsafe = manifest.model_copy(update={"caption_schedule": schedule})
    with pytest.raises(ValueError, match="CAPTION_ASS_CONTROL_SEQUENCE_BLOCKED"):
        FFmpegCommandBuilder(
            tmp_path,
            ffmpeg="fixture-ffmpeg",
            ffprobe="fixture-ffprobe",
        ).build_synthetic(unsafe, run_key=f"unsafe-ass-{len(unsafe_line)}")


@pytest.mark.skipif(
    not Path(FFMPEG_FULL).is_file() or not Path(FFPROBE_FULL).is_file(),
    reason="ffmpeg-full/ffprobe unavailable",
)
def test_renderer_rejects_generated_ass_and_command_argv_tamper(tmp_path: Path):
    _, _, _, _, _, manifest = golden_compilation()
    builder = FFmpegCommandBuilder(tmp_path, ffmpeg=FFMPEG_FULL, ffprobe=FFPROBE_FULL)
    ass_command = builder.build_synthetic(manifest, run_key="cqr1b-ass-tamper")
    Path(ass_command.generated_caption_path).write_text("tampered ASS\n", encoding="utf-8")
    renderer = NativeFFmpegRenderer(tmp_path, smoke_enabled=True, production_enabled=False)
    with pytest.raises(ValueError, match="GENERATED_FILE_CHECKSUM_MISMATCH"):
        renderer.execute(
            manifest,
            ass_command,
            purpose="CQR1_LOCAL_GOLDEN_FIXTURE",
        )

    argv_command = builder.build_synthetic(manifest, run_key="cqr1b-argv-tamper")
    tampered_argv = argv_command.model_copy(
        update={"sanitized_argv": [*argv_command.sanitized_argv[:-1], "-t", "99", argv_command.sanitized_argv[-1]]}
    )
    with pytest.raises(ValueError, match="COMMAND_MANIFEST_HASH_MISMATCH"):
        renderer.execute(
            manifest,
            tampered_argv,
            purpose="CQR1_LOCAL_GOLDEN_FIXTURE",
        )


@pytest.mark.skipif(
    not Path(FFMPEG_FULL).is_file() or not Path(FFPROBE_FULL).is_file(),
    reason="ffmpeg-full/ffprobe unavailable",
)
def test_actual_strict_golden_render_passes_ffprobe_decode_faststart_and_technical_qc(
    tmp_path: Path,
):
    _, _, _, compiled, _, manifest = golden_compilation()
    command = FFmpegCommandBuilder(
        tmp_path,
        ffmpeg=FFMPEG_FULL,
        ffprobe=FFPROBE_FULL,
    ).build_synthetic(manifest, run_key="cqr1b-actual-golden")
    receipt, native_qc = NativeFFmpegRenderer(
        tmp_path,
        smoke_enabled=True,
        production_enabled=False,
    ).execute(
        manifest,
        command,
        purpose="CQR1_LOCAL_GOLDEN_FIXTURE",
    )
    technical = TechnicalMediaQC().from_native_media_qc(
        run_id="cqr1b-local-golden-fixture",
        native_report=native_qc,
    )
    expected_seconds = compiled.timeline.audio_duration_ms / 1000
    assert native_qc.result == "PASS"
    assert technical.result == "PASS"
    assert abs(native_qc.checks["duration"] - expected_seconds) <= 0.25
    assert native_qc.checks["av_drift_ms"] <= 250
    assert native_qc.checks["duration_matches_expected"] is True
    assert native_qc.checks["full_decode"] is True
    assert native_qc.checks["stream_integrity"] is True
    assert native_qc.checks["fast_start"] is True
    assert Path(receipt.output_path).is_file()
    assert receipt.no_provider_calls_confirmed is True
    assert receipt.production_eligible is False


@pytest.mark.skipif(
    not Path(FFMPEG_FULL).is_file() or not Path(FFPROBE_FULL).is_file(),
    reason="ffmpeg-full/ffprobe unavailable",
)
def test_strict_golden_decoded_video_and_audio_are_deterministic(tmp_path: Path):
    _, _, _, _, _, manifest = golden_compilation()
    builder = FFmpegCommandBuilder(tmp_path, ffmpeg=FFMPEG_FULL, ffprobe=FFPROBE_FULL)
    renderer = NativeFFmpegRenderer(tmp_path, smoke_enabled=True, production_enabled=False)
    outputs: list[Path] = []
    for run_key in ("cqr1b-deterministic-a", "cqr1b-deterministic-b"):
        command = builder.build_synthetic(manifest, run_key=run_key)
        receipt, native_qc = renderer.execute(
            manifest,
            command,
            purpose="CQR1_LOCAL_GOLDEN_FIXTURE",
        )
        assert native_qc.result == "PASS"
        outputs.append(Path(receipt.output_path))

    def decoded_digest(path: Path, stream: str) -> str:
        completed = subprocess.run(
            [FFMPEG_FULL, "-v", "error", "-i", str(path), "-map", stream, "-f", "framemd5", "-"],
            capture_output=True,
            check=True,
        )
        return hashlib.sha256(completed.stdout).hexdigest()

    # Hardware H.264 bitstreams need not be byte-identical. The decoded video
    # frames, audio samples, timestamps, and known canonical duration must be.
    assert decoded_digest(outputs[0], "0:v:0") == decoded_digest(outputs[1], "0:v:0")
    assert decoded_digest(outputs[0], "0:a:0") == decoded_digest(outputs[1], "0:a:0")
