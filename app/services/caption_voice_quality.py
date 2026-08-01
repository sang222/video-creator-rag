from __future__ import annotations

import math
import re
import statistics
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from app.contracts.caption_voice_quality import (
    ASSCaptionEvent,
    CanonicalCaptionCue,
    CaptionBBoxMetrics,
    CaptionBoundsPreflightReport,
    CaptionDisplayToken,
    CaptionFormatPolicy,
    CaptionReadingMetrics,
    CaptionStylePolicy,
    CaptionSyncMetrics,
    CaptionSyncPolicy,
    CaptionTextSpan,
    CompiledCaptionTrack,
    CreativeQualityGateResult,
    FinalCueTrailingHoldEvidence,
    FinalCueTrailingHoldPolicy,
    NarrationAudioAnalysis,
    NarrationPacingCorrectionPlan,
    NarrationPacingMetrics,
    NarrationPacingPolicy,
    NarrationPacingReport,
    OffsetThreshold,
    PauseSpan,
    ThresholdBand,
)
from app.contracts.temporal_authority import (
    CanonicalMediaTimeline,
    CanonicalTimelineSegment,
    DisplayCaptionText,
    SpokenTextNormalized,
    TextSpan,
    VerifiedNarrationAlignment,
)
from app.services.caption_ass import (
    build_caption_ass_document,
    caption_render_payload,
    resolved_caption_render_style,
)
from app.services.native_render_plan import stable_hash


CAPTION_COMPILATION_VERSION = "readable-caption-compiler/v1.1.0"
FFMPEG_FULL_DEFAULT = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
ALLOWED_DISPLAY_TRANSFORMS = {
    "APPROVED_CASING",
    "APPROVED_BRANDED_CASING",
    "KNOWN_NUMBER_RECOMPACTION",
    "MINOR_PUNCTUATION_SIMPLIFICATION",
}
PREPOSITIONS = {
    "about",
    "above",
    "across",
    "after",
    "against",
    "along",
    "among",
    "around",
    "at",
    "before",
    "behind",
    "below",
    "beneath",
    "beside",
    "between",
    "beyond",
    "by",
    "during",
    "for",
    "from",
    "in",
    "inside",
    "into",
    "near",
    "of",
    "on",
    "onto",
    "over",
    "through",
    "to",
    "toward",
    "under",
    "until",
    "up",
    "with",
    "within",
    "without",
}
_WORD_KEY_RE = re.compile(r"[^a-z0-9]")
_BBOX_RE = re.compile(
    r"x1:\s*(-?\d+)\s+x2:\s*(-?\d+)\s+y1:\s*(-?\d+)\s+y2:\s*(-?\d+)\s+w:\s*(\d+)\s+h:\s*(\d+)"
)


def _policy_hash(policy: Any) -> str:
    payload = policy.model_dump(mode="json", by_alias=True, exclude={"policy_hash"})
    return stable_hash(payload)


def _coerce_policy(policy: Any, *, family: str, model: type[Any]) -> Any:
    if isinstance(policy, model):
        return policy
    if not isinstance(policy, dict):
        return model.model_validate(policy)
    snapshot = dict(policy)
    family_payload = snapshot.get(family)
    if isinstance(family_payload, dict):
        payload = dict(family_payload)
        for key in ("policy_ref", "policy_version", "policy_hash", "channel_id"):
            if snapshot.get(key) is not None:
                payload.setdefault(key, snapshot[key])
    else:
        payload = snapshot
    family_hash = stable_hash(payload)
    payload.setdefault("policy_ref", f"creative-policy-inline://{family}/{family_hash}")
    payload.setdefault("policy_version", f"inline-{family_hash[:12]}")
    payload.setdefault("policy_hash", family_hash)
    return model.model_validate(payload)


def _narration_policy(policy: Any) -> NarrationPacingPolicy:
    return _coerce_policy(
        policy, family="narration_pacing_policy", model=NarrationPacingPolicy
    )


def _caption_style_policy(policy: Any) -> CaptionStylePolicy:
    return _coerce_policy(
        policy, family="caption_style_policy", model=CaptionStylePolicy
    )


def _caption_sync_policy(policy: Any) -> CaptionSyncPolicy:
    return _coerce_policy(policy, family="caption_sync_policy", model=CaptionSyncPolicy)


def _final_cue_trailing_hold_policy(
    policy: FinalCueTrailingHoldPolicy | dict[str, Any],
) -> FinalCueTrailingHoldPolicy:
    parsed = (
        policy
        if isinstance(policy, FinalCueTrailingHoldPolicy)
        else FinalCueTrailingHoldPolicy.model_validate(policy)
    )
    calculated_hash = _policy_hash(parsed)
    if parsed.policy_hash is not None and parsed.policy_hash != calculated_hash:
        raise ValueError("CAPTION_TRAILING_HOLD_POLICY_HASH_INVALID")
    return parsed.model_copy(update={"policy_hash": calculated_hash})


def _resolved_policy_hash(policy: Any) -> str:
    return str(policy.policy_hash or _policy_hash(policy))


def _comparison_key(value: str) -> str:
    return _WORD_KEY_RE.sub("", value.casefold())


def _median(values: Sequence[float | int]) -> float | None:
    return round(float(statistics.median(values)), 3) if values else None


def _percentile(values: Sequence[float | int], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[rank], 3)


def _union_duration(
    spans: Iterable[tuple[int, int]], *, start_ms: int, end_ms: int
) -> int:
    clipped = sorted(
        (max(start_ms, start), min(end_ms, end))
        for start, end in spans
        if min(end_ms, end) > max(start_ms, start)
    )
    if not clipped:
        return 0
    merged: list[list[int]] = []
    for start, end in clipped:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged)


def _gate(
    name: str,
    status: str,
    reasons: Iterable[str],
    metrics: dict[str, Any],
    policy: Any,
) -> CreativeQualityGateResult:
    return CreativeQualityGateResult(
        gate=name,
        status=status,
        reason_codes=sorted(set(reasons)),
        metrics=metrics,
        policy_ref=policy.policy_ref,
        policy_version=policy.policy_version,
        policy_hash=_resolved_policy_hash(policy),
    )


def _worst_status(statuses: Iterable[str]) -> str:
    values = set(statuses)
    if "BLOCK" in values:
        return "BLOCK"
    if "REVIEW_REQUIRED" in values:
        return "REVIEW_REQUIRED"
    return "PASS"


class NarrationPacingAnalyzer:
    """Measures pacing only from final-audio alignment and local pause evidence."""

    def analyze(
        self,
        *,
        normalized: SpokenTextNormalized,
        alignment: VerifiedNarrationAlignment,
        audio_analysis: NarrationAudioAnalysis,
        policy: NarrationPacingPolicy | dict[str, Any],
        section_boundary_after_token_ids: Iterable[str] = (),
    ) -> NarrationPacingReport:
        policy = _narration_policy(policy)
        if alignment.verification_status != "PASS" or alignment.token_coverage != 1.0:
            raise ValueError("PACING_VERIFIED_ALIGNMENT_REQUIRED")
        if alignment.spoken_text_hash != normalized.spoken_text_hash:
            raise ValueError("PACING_SPOKEN_TEXT_MISMATCH")
        if (
            alignment.audio_asset_ref != audio_analysis.audio_asset_ref
            or alignment.audio_duration_ms != audio_analysis.audio_duration_ms
        ):
            raise ValueError("PACING_AUDIO_EVIDENCE_MISMATCH")
        words = sorted(
            alignment.verified_words,
            key=lambda item: (item.start_ms, item.end_ms, item.word_id),
        )
        if not words:
            raise ValueError("PACING_VERIFIED_WORDS_REQUIRED")
        token_by_id = {token.token_id: token for token in normalized.spoken_tokens}
        token_ids = [
            token_id for word in words for token_id in word.source_spoken_token_ids
        ]
        if len(token_ids) != len(set(token_ids)) or set(token_ids) != set(token_by_id):
            raise ValueError("PACING_WORD_COUNT_EVIDENCE_INVALID")

        sections = set(section_boundary_after_token_ids)
        word_gaps = self._word_gaps(
            normalized=normalized,
            alignment=alignment,
            sections=sections,
            audio_silences=audio_analysis.silence_spans,
        )
        threshold = policy.active_speech_silence_gap_threshold_ms
        silence_source = audio_analysis.silence_spans or word_gaps
        excluded = [
            (span.start_ms, span.end_ms)
            for span in silence_source
            if span.duration_ms > threshold
        ]
        duration_ms = alignment.audio_duration_ms
        active_ms = max(
            1, duration_ms - _union_duration(excluded, start_ms=0, end_ms=duration_ms)
        )
        hook_end = min(policy.hook_window_ms, duration_ms)
        hook_words = [word for word in words if word.start_ms < hook_end]
        hook_active_ms = max(
            1,
            hook_end - _union_duration(excluded, start_ms=0, end_ms=hook_end),
        )
        word_count = len(token_ids)
        hook_token_ids = {
            token_id for word in hook_words for token_id in word.source_spoken_token_ids
        }
        pause_values = {
            kind: [span.duration_ms for span in word_gaps if span.boundary_kind == kind]
            for kind in ("COMMA", "SENTENCE", "SECTION")
        }
        metrics = NarrationPacingMetrics(
            spoken_word_count=word_count,
            active_speech_duration_ms=active_ms,
            delivered_duration_ms=duration_ms,
            hook_word_count=len(hook_token_ids),
            hook_active_speech_duration_ms=hook_active_ms,
            active_speech_wpm=round(word_count * 60_000 / active_ms, 3),
            delivered_wpm=round(word_count * 60_000 / duration_ms, 3),
            hook_first_8s_active_wpm=round(
                len(hook_token_ids) * 60_000 / hook_active_ms, 3
            ),
            comma_pause_ms_median=_median(pause_values["COMMA"]),
            sentence_pause_ms_median=_median(pause_values["SENTENCE"]),
            section_pause_ms_median=_median(pause_values["SECTION"]),
            comma_pause_count=len(pause_values["COMMA"]),
            sentence_pause_count=len(pause_values["SENTENCE"]),
            section_pause_count=len(pause_values["SECTION"]),
        )
        payload = {
            "audio_asset_ref": alignment.audio_asset_ref,
            "spoken_text_hash": normalized.spoken_text_hash,
            "verified_alignment_ref": f"verified-alignment:{alignment.content_hash}",
            "metrics": metrics.model_dump(mode="json"),
            "detected_pause_spans": [
                item.model_dump(mode="json") for item in word_gaps
            ],
            "waveform_summary": audio_analysis.waveform_summary,
            "word_count_evidence": [
                {
                    "word_id": word.word_id,
                    "spoken_token_ids": word.source_spoken_token_ids,
                    "start_ms": word.start_ms,
                    "end_ms": word.end_ms,
                }
                for word in words
            ],
            "gate_result": None,
            "policy_ref": policy.policy_ref,
            "policy_version": policy.policy_version,
            "policy_hash": _resolved_policy_hash(policy),
        }
        return NarrationPacingReport(**payload, content_hash=stable_hash(payload))

    @staticmethod
    def _word_gaps(
        *,
        normalized: SpokenTextNormalized,
        alignment: VerifiedNarrationAlignment,
        sections: set[str],
        audio_silences: Sequence[PauseSpan],
    ) -> list[PauseSpan]:
        words = sorted(
            alignment.verified_words, key=lambda item: (item.start_ms, item.end_ms)
        )
        tokens = {item.token_id: item for item in normalized.spoken_tokens}
        spans: list[PauseSpan] = []
        if words[0].start_ms > 0:
            spans.append(
                PauseSpan(
                    pause_id="pause-boundary-start",
                    start_ms=0,
                    end_ms=words[0].start_ms,
                    source="AUDIO_BOUNDARY",
                    before_spoken_token_id=words[0].source_spoken_token_ids[0],
                    boundary_kind="BOUNDARY",
                    detected_in_audio=NarrationPacingAnalyzer._overlaps_audio_silence(
                        0, words[0].start_ms, audio_silences
                    ),
                )
            )
        for index, (current, following) in enumerate(zip(words, words[1:]), start=1):
            if following.start_ms <= current.end_ms:
                continue
            after_id = current.source_spoken_token_ids[-1]
            before_id = following.source_spoken_token_ids[0]
            separator = normalized.spoken_text[
                tokens[after_id].spoken_span.end : tokens[before_id].spoken_span.start
            ]
            if after_id in sections:
                kind = "SECTION"
            elif re.search(r"[.!?]", separator):
                kind = "SENTENCE"
            elif "," in separator:
                kind = "COMMA"
            else:
                kind = "OTHER"
            spans.append(
                PauseSpan(
                    pause_id=f"pause-word-gap-{index:04d}",
                    start_ms=current.end_ms,
                    end_ms=following.start_ms,
                    source="VERIFIED_WORD_GAP",
                    after_spoken_token_id=after_id,
                    before_spoken_token_id=before_id,
                    boundary_kind=kind,
                    detected_in_audio=NarrationPacingAnalyzer._overlaps_audio_silence(
                        current.end_ms, following.start_ms, audio_silences
                    ),
                )
            )
        if words[-1].end_ms < alignment.audio_duration_ms:
            spans.append(
                PauseSpan(
                    pause_id="pause-boundary-end",
                    start_ms=words[-1].end_ms,
                    end_ms=alignment.audio_duration_ms,
                    source="AUDIO_BOUNDARY",
                    after_spoken_token_id=words[-1].source_spoken_token_ids[-1],
                    boundary_kind="BOUNDARY",
                    detected_in_audio=NarrationPacingAnalyzer._overlaps_audio_silence(
                        words[-1].end_ms, alignment.audio_duration_ms, audio_silences
                    ),
                )
            )
        return spans

    @staticmethod
    def _overlaps_audio_silence(
        start_ms: int, end_ms: int, spans: Sequence[PauseSpan]
    ) -> bool:
        return any(
            min(end_ms, span.end_ms) > max(start_ms, span.start_ms) for span in spans
        )


class NarrationPacingGate:
    def evaluate(
        self,
        report: NarrationPacingReport,
        policy: NarrationPacingPolicy | dict[str, Any],
    ) -> CreativeQualityGateResult:
        policy = _narration_policy(policy)
        statuses: list[str] = []
        reasons: list[str] = []
        metrics = report.metrics
        for value, threshold, fast_reason in (
            (
                metrics.active_speech_wpm,
                policy.body_active_speech_wpm,
                "PACE_ACTIVE_TOO_FAST",
            ),
            (
                metrics.delivered_wpm,
                policy.body_delivered_wpm,
                "PACE_DELIVERED_TOO_FAST",
            ),
        ):
            status, reason = self._wpm_status(value, threshold, fast_reason)
            statuses.append(status)
            reasons.extend(reason)
        hook_status = "PASS"
        if (
            metrics.hook_first_8s_active_wpm
            > policy.hook_first_8s_active_wpm.block_above
        ):
            hook_status = "BLOCK"
            reasons.append("PACE_HOOK_TOO_FAST")
        elif (
            metrics.hook_first_8s_active_wpm > policy.hook_first_8s_active_wpm.pass_max
        ):
            hook_status = "REVIEW_REQUIRED"
            reasons.append("PACE_HOOK_TOO_FAST")
        statuses.append(hook_status)
        for value, threshold, reason in (
            (
                metrics.comma_pause_ms_median,
                policy.comma_pause_ms,
                "PACE_COMMA_PAUSE_SHORT",
            ),
            (
                metrics.sentence_pause_ms_median,
                policy.sentence_pause_ms,
                "PACE_SENTENCE_PAUSE_SHORT",
            ),
            (
                metrics.section_pause_ms_median,
                policy.section_pause_ms,
                "PACE_SECTION_PAUSE_SHORT",
            ),
        ):
            if value is None:
                continue
            pause_status = "PASS"
            if threshold.block_below is not None and value < threshold.block_below:
                pause_status = "BLOCK"
                reasons.append(reason)
            elif not threshold.pass_range[0] <= value <= threshold.pass_range[1]:
                pause_status = "REVIEW_REQUIRED"
                reasons.append(reason)
            statuses.append(pause_status)
        return _gate(
            "NarrationPacingGate",
            _worst_status(statuses),
            reasons,
            metrics.model_dump(mode="json"),
            policy,
        )

    @staticmethod
    def _wpm_status(
        value: float, threshold: ThresholdBand, fast_reason: str
    ) -> tuple[str, list[str]]:
        if threshold.block_above is not None and value > threshold.block_above:
            return "BLOCK", [fast_reason]
        if (
            threshold.extreme_slow_block_below is not None
            and value < threshold.extreme_slow_block_below
        ):
            return "BLOCK", ["PACE_EXTREME_SLOW"]
        if threshold.pass_range[0] <= value <= threshold.pass_range[1]:
            return "PASS", []
        if value > threshold.pass_range[1]:
            return "REVIEW_REQUIRED", [fast_reason]
        return "REVIEW_REQUIRED", ["PACE_SLOW_REVIEW"]

    def attach(
        self,
        report: NarrationPacingReport,
        policy: NarrationPacingPolicy | dict[str, Any],
    ) -> NarrationPacingReport:
        result = self.evaluate(report, policy)
        payload = report.model_dump(mode="json", exclude={"content_hash"})
        payload["gate_result"] = result.model_dump(mode="json")
        return NarrationPacingReport(**payload, content_hash=stable_hash(payload))


class NarrationPacingCorrectionPlanner:
    """Fail-closed correction order for one measured final narration asset."""

    _FAST_REASONS = {
        "PACE_ACTIVE_TOO_FAST",
        "PACE_DELIVERED_TOO_FAST",
        "PACE_HOOK_TOO_FAST",
    }
    _SCRIPT_REPAIR_REASONS = {
        "PACE_COMMA_PAUSE_SHORT",
        "PACE_SENTENCE_PAUSE_SHORT",
        "PACE_SECTION_PAUSE_SHORT",
    }

    def plan(
        self,
        *,
        pacing_gate: CreativeQualityGateResult,
        policy: NarrationPacingPolicy | dict[str, Any],
        current_model_supports_speed: bool,
        one_provider_regeneration_authorized: bool,
        text_density_excessive: bool = False,
        emergency_atempo_delta_percent: float | None = None,
        human_atempo_approval: bool = False,
    ) -> NarrationPacingCorrectionPlan:
        policy = _narration_policy(policy)
        if pacing_gate.gate != "NarrationPacingGate":
            raise ValueError("PACING_GATE_EVIDENCE_REQUIRED")
        reasons = set(pacing_gate.reason_codes)
        pause_or_text_repair = text_density_excessive or bool(
            reasons & self._SCRIPT_REPAIR_REASONS
        )

        if emergency_atempo_delta_percent is not None:
            delta = abs(float(emergency_atempo_delta_percent))
            atempo = dict(policy.emergency_atempo)
            required = {
                "max_abs_delta_percent_without_human",
                "human_approval_required_above_percent",
                "block_above_percent",
            }
            if not required <= set(atempo):
                raise ValueError("PACING_ATEMPO_POLICY_REQUIRED")
            no_human_max = float(atempo["max_abs_delta_percent_without_human"])
            human_above = float(atempo["human_approval_required_above_percent"])
            block_above = float(atempo["block_above_percent"])
            if not 0 <= no_human_max <= human_above <= block_above:
                raise ValueError("PACING_ATEMPO_POLICY_INVALID")
            if pause_or_text_repair:
                return self._decision(
                    policy=policy,
                    pacing_gate=pacing_gate,
                    action="SCRIPT_PACING_REWRITE_REQUIRED",
                    reason_codes=[
                        "SCRIPT_PACING_REWRITE_REQUIRED",
                        "ATEMPO_CANNOT_HIDE_SCRIPT_DEFECT",
                    ],
                    recommendation="Repair text density, punctuation, and pause structure; generate and remeasure one complete narration.",
                )
            if delta > block_above:
                return self._decision(
                    policy=policy,
                    pacing_gate=pacing_gate,
                    action="SCRIPT_PACING_REWRITE_REQUIRED",
                    reason_codes=[
                        "SCRIPT_PACING_REWRITE_REQUIRED",
                        "ATEMPO_DELTA_ABOVE_POLICY",
                    ],
                    recommendation="Do not time-stretch; repair the script or regenerate narration, then remeasure final audio.",
                    emergency_atempo_delta_percent=delta,
                )
            if delta > human_above and not human_atempo_approval:
                return self._decision(
                    policy=policy,
                    pacing_gate=pacing_gate,
                    action="HUMAN_ATEMPO_APPROVAL_REQUIRED",
                    reason_codes=["ATEMPO_HUMAN_APPROVAL_REQUIRED"],
                    recommendation="Obtain explicit human approval for this emergency atempo delta, or regenerate and remeasure narration.",
                    emergency_atempo_delta_percent=delta,
                    human_approval_required=True,
                )
            if delta <= no_human_max or human_atempo_approval:
                return self._decision(
                    policy=policy,
                    pacing_gate=pacing_gate,
                    action="EMERGENCY_ATEMPO",
                    reason_codes=["EMERGENCY_ATEMPO_EXPLICITLY_SCOPED"],
                    recommendation="Apply the single scoped emergency atempo transform and remeasure the resulting final audio.",
                    emergency_atempo_delta_percent=delta,
                    ffmpeg_atempo_allowed=True,
                    human_approval_required=delta > human_above,
                )
            raise ValueError("PACING_ATEMPO_POLICY_GAP")

        if pacing_gate.status == "PASS":
            return self._decision(
                policy=policy,
                pacing_gate=pacing_gate,
                action="ACCEPT_MEASURED_NARRATION",
                reason_codes=["PACING_MEASURED_PASS"],
                recommendation="Accept the measured complete narration; keep its verified alignment as temporal authority.",
                blocks_current_narration=False,
                remeasure_final_audio_required=False,
            )
        if pause_or_text_repair or pacing_gate.status == "BLOCK":
            return self._decision(
                policy=policy,
                pacing_gate=pacing_gate,
                action="SCRIPT_PACING_REWRITE_REQUIRED",
                reason_codes=["SCRIPT_PACING_REWRITE_REQUIRED", *sorted(reasons)],
                recommendation="Repair script density or punctuation, generate one complete narration, and remeasure final audio.",
            )
        if reasons & self._FAST_REASONS:
            if current_model_supports_speed and one_provider_regeneration_authorized:
                return self._decision(
                    policy=policy,
                    pacing_gate=pacing_gate,
                    action="ONE_CONTROLLED_SPEED_REGENERATION",
                    reason_codes=[
                        "ONE_CONTROLLED_SPEED_REGENERATION_ALLOWED",
                        *sorted(reasons),
                    ],
                    recommendation="Use the model speed control for one modestly slower regeneration, then rerun alignment and pacing gates.",
                    provider_regeneration_authorized=True,
                    provider_speed_regeneration_count=1,
                )
            return self._decision(
                policy=policy,
                pacing_gate=pacing_gate,
                action="BLOCK_NO_AUTHORIZED_REGENERATION",
                reason_codes=["PAID_TTS_REGENERATION_NOT_AUTHORIZED", *sorted(reasons)],
                recommendation="Stop this run; obtain a new run ID and explicit paid TTS approval before any regeneration.",
            )
        return self._decision(
            policy=policy,
            pacing_gate=pacing_gate,
            action="HUMAN_PACING_REVIEW_REQUIRED",
            reason_codes=["PACING_HUMAN_REVIEW_REQUIRED", *sorted(reasons)],
            recommendation="Review the measured slow pacing; do not use automatic speed or atempo correction.",
            human_approval_required=True,
        )

    @staticmethod
    def _decision(
        *,
        policy: NarrationPacingPolicy,
        pacing_gate: CreativeQualityGateResult,
        action: str,
        reason_codes: list[str],
        recommendation: str,
        provider_regeneration_authorized: bool = False,
        provider_speed_regeneration_count: int = 0,
        emergency_atempo_delta_percent: float | None = None,
        ffmpeg_atempo_allowed: bool = False,
        human_approval_required: bool = False,
        blocks_current_narration: bool = True,
        remeasure_final_audio_required: bool = True,
    ) -> NarrationPacingCorrectionPlan:
        payload = {
            "action": action,
            "pacing_gate_status": pacing_gate.status,
            "provider_speed_regeneration_count": provider_speed_regeneration_count,
            "provider_regeneration_authorized": provider_regeneration_authorized,
            "emergency_atempo_delta_percent": emergency_atempo_delta_percent,
            "ffmpeg_atempo_allowed": ffmpeg_atempo_allowed,
            "human_approval_required": human_approval_required,
            "blocks_current_narration": blocks_current_narration,
            "remeasure_final_audio_required": remeasure_final_audio_required,
            "reason_codes": sorted(set(reason_codes)),
            "exact_recommendation": recommendation,
            "policy_ref": policy.policy_ref,
            "policy_version": policy.policy_version,
            "policy_hash": _resolved_policy_hash(policy),
        }
        return NarrationPacingCorrectionPlan(
            **payload, content_hash=stable_hash(payload)
        )


@dataclass(frozen=True)
class _DisplayUnit:
    display_token: CaptionDisplayToken
    rendered_text: str
    spoken_token_ids: tuple[str, ...]


@dataclass(frozen=True)
class CaptionCompilationOutput:
    timeline: CanonicalMediaTimeline
    track: CompiledCaptionTrack


class ReadableCaptionCompiler:
    """Compiles display cues without authoring or changing spoken-word timing."""

    def compile(
        self,
        *,
        normalized: SpokenTextNormalized,
        alignment: VerifiedNarrationAlignment,
        timeline: CanonicalMediaTimeline,
        policy: CaptionStylePolicy | dict[str, Any],
        final_cue_trailing_hold_policy: FinalCueTrailingHoldPolicy
        | dict[str, Any]
        | None = None,
        display_caption_text: DisplayCaptionText | None = None,
        aspect_ratio: str = "16:9",
    ) -> CaptionCompilationOutput:
        policy = _caption_style_policy(policy)
        trailing_hold_policy = (
            _final_cue_trailing_hold_policy(final_cue_trailing_hold_policy)
            if final_cue_trailing_hold_policy is not None
            else None
        )
        if alignment.verification_status != "PASS" or alignment.token_coverage != 1.0:
            raise ValueError("CAPTION_VERIFIED_ALIGNMENT_REQUIRED")
        if alignment.spoken_text_hash != normalized.spoken_text_hash:
            raise ValueError("CAPTION_SPOKEN_TEXT_MISMATCH")
        if (
            timeline.audio_asset_id != alignment.audio_asset_ref
            or timeline.audio_duration_ms != alignment.audio_duration_ms
        ):
            raise ValueError("CAPTION_TIMELINE_AUDIO_MISMATCH")
        if (
            display_caption_text
            and display_caption_text.spoken_text_hash != normalized.spoken_text_hash
        ):
            raise ValueError("CAPTION_DISPLAY_SPOKEN_HASH_MISMATCH")

        format_policy = self._format_policy(policy, aspect_ratio)
        render_style = resolved_caption_render_style(
            policy=policy,
            format_policy=format_policy,
            aspect_ratio=aspect_ratio,
            policy_hash=_resolved_policy_hash(policy),
        )
        units = self._display_units(normalized, display_caption_text)
        expected_ids = [item.token_id for item in normalized.spoken_tokens]
        actual_ids = [token_id for unit in units for token_id in unit.spoken_token_ids]
        if actual_ids != expected_ids:
            raise ValueError("CAPTION_SPOKEN_TOKEN_COVERAGE_REQUIRED")
        unit_by_token = {
            token_id: unit for unit in units for token_id in unit.spoken_token_ids
        }
        word_by_token = {
            token_id: word
            for word in alignment.verified_words
            for token_id in word.source_spoken_token_ids
        }
        if set(word_by_token) != set(expected_ids):
            raise ValueError("CAPTION_ALIGNMENT_TOKEN_COVERAGE_REQUIRED")

        cues: list[CanonicalCaptionCue] = []
        display_cursor = 0
        updated_segments = []
        for segment in timeline.segments:
            segment_units: list[_DisplayUnit] = []
            seen_units: set[str] = set()
            for token_id in segment.spoken_token_ids:
                unit = unit_by_token[token_id]
                if unit.display_token.display_token_id not in seen_units:
                    segment_units.append(unit)
                    seen_units.add(unit.display_token.display_token_id)
            for unit in segment_units:
                if not set(unit.spoken_token_ids).issubset(segment.spoken_token_ids):
                    raise ValueError("CAPTION_DISPLAY_TOKEN_CROSSES_SEGMENT")
            batches = self._cue_batches(
                segment_units,
                word_by_token=word_by_token,
                format_policy=format_policy,
                policy=policy,
            )
            segment_cues: list[CanonicalCaptionCue] = []
            for batch in batches:
                lines = self._wrap_lines(batch, format_policy)
                spoken_ids = [
                    token_id for unit in batch for token_id in unit.spoken_token_ids
                ]
                start_ms = word_by_token[spoken_ids[0]].start_ms
                end_ms = word_by_token[spoken_ids[-1]].end_ms
                visible = " ".join(lines)
                display_span = CaptionTextSpan(
                    start=display_cursor, end=display_cursor + len(visible)
                )
                display_cursor = display_span.end + 1
                duration_seconds = (end_ms - start_ms) / 1000
                reading = CaptionReadingMetrics(
                    duration_seconds=duration_seconds,
                    character_count=len(visible),
                    characters_per_second=round(len(visible) / duration_seconds, 3),
                    chars_per_line=[len(line) for line in lines],
                    max_chars_per_line=max(len(line) for line in lines),
                    line_count=len(lines),
                )
                cue_payload = {
                    "cue_id": f"caption-{len(cues) + 1:04d}",
                    "source_segment_ids": [segment.segment_id],
                    "display_span": display_span.model_dump(mode="json"),
                    "caption_start_ms": start_ms,
                    "caption_end_ms": end_ms,
                    "caption_lines": lines,
                    "spoken_token_ids": spoken_ids,
                    "display_tokens": [
                        unit.display_token.model_dump(mode="json") for unit in batch
                    ],
                    "reading_metrics": reading.model_dump(mode="json"),
                    "bbox_metrics": None,
                    "gate_results": [],
                    "timing_source": "CANONICAL_MEDIA_TIMELINE",
                }
                cue = CanonicalCaptionCue(
                    **cue_payload, content_hash=stable_hash(cue_payload)
                )
                cues.append(cue)
                segment_cues.append(cue)
            updated_segments.append(
                segment.model_copy(
                    update={
                        "display_span": TextSpan(
                            start=segment_cues[0].display_span.start,
                            end=segment_cues[-1].display_span.end,
                        )
                        if segment_cues
                        else segment.display_span,
                        "caption_start_ms": segment_cues[0].caption_start_ms
                        if segment_cues
                        else None,
                        "caption_end_ms": segment_cues[-1].caption_end_ms
                        if segment_cues
                        else None,
                        "caption_lines": [
                            line for cue in segment_cues for line in cue.caption_lines
                        ],
                        "caption_cues": segment_cues,
                        "caption_cue_ids": [cue.cue_id for cue in segment_cues],
                        "caption_spoken_token_ids": [
                            token_id
                            for cue in segment_cues
                            for token_id in cue.spoken_token_ids
                        ],
                        "caption_reading_metrics": [
                            cue.reading_metrics for cue in segment_cues
                        ],
                        "caption_bbox_metrics": [],
                        "caption_gate_results": [],
                    }
                )
            )
        trailing_hold_evidence: FinalCueTrailingHoldEvidence | None = None
        if trailing_hold_policy is not None:
            cues, updated_segments, trailing_hold_evidence = (
                self._apply_final_cue_trailing_hold(
                    cues=cues,
                    updated_segments=updated_segments,
                    normalized=normalized,
                    alignment=alignment,
                    source_timeline=timeline,
                    policy=trailing_hold_policy,
                )
            )
        compilation_gate = CaptionCompilationGate().evaluate(
            cues=cues,
            normalized=normalized,
            timeline=timeline,
            policy=policy,
        )
        if compilation_gate.status == "BLOCK":
            raise ValueError(";".join(compilation_gate.reason_codes))
        caption_compilation_payload = {
            "compilation_version": CAPTION_COMPILATION_VERSION,
            "source_timeline_hash": timeline.timeline_hash,
            "spoken_text_hash": normalized.spoken_text_hash,
            "policy_hash": _resolved_policy_hash(policy),
            "final_cue_trailing_hold": (
                trailing_hold_evidence.model_dump(mode="json")
                if trailing_hold_evidence is not None
                else None
            ),
            "cues": [cue.model_dump(mode="json") for cue in cues],
        }
        compilation_hash = stable_hash(caption_compilation_payload)
        render_payload_hash = stable_hash(caption_render_payload(cues))
        timeline_payload = timeline.model_dump(mode="json", exclude={"timeline_hash"})
        timeline_payload["segments"] = [
            item.model_dump(mode="json") for item in updated_segments
        ]
        timeline_payload["qc_metrics"] = {
            **timeline.qc_metrics,
            "caption_compilation_ref": f"caption-compilation:{compilation_hash}",
            "caption_compilation_hash": compilation_hash,
            "caption_compilation_version": CAPTION_COMPILATION_VERSION,
            "caption_render_payload_hash": render_payload_hash,
            "caption_render_style": render_style,
            "caption_policy_ref": policy.policy_ref,
            "caption_policy_version": policy.policy_version,
            "caption_policy_hash": _resolved_policy_hash(policy),
            "caption_compilation_gate": compilation_gate.status,
            "caption_spoken_token_coverage": 1.0,
            "caption_timing_source": "CANONICAL_MEDIA_TIMELINE",
            "caption_final_cue_trailing_hold": (
                trailing_hold_evidence.model_dump(mode="json")
                if trailing_hold_evidence is not None
                else None
            ),
        }
        compilation_warnings = list(timeline_payload["compilation_warnings"])
        if (
            trailing_hold_evidence is not None
            and trailing_hold_evidence.status == "APPLIED"
        ):
            compilation_warnings.append(trailing_hold_evidence.reason_code)
        timeline_payload["compilation_warnings"] = sorted(set(compilation_warnings))
        compiled_timeline = CanonicalMediaTimeline(
            **timeline_payload,
            timeline_hash=stable_hash(timeline_payload),
        )
        srt_text = self._srt(cues)
        ass_events = [
            ASSCaptionEvent(
                cue_id=cue.cue_id,
                start_ms=cue.caption_start_ms,
                end_ms=cue.caption_end_ms,
                text=r"\N".join(self._ass_escape(line) for line in cue.caption_lines),
            )
            for cue in cues
        ]
        track_payload = {
            "compilation_version": CAPTION_COMPILATION_VERSION,
            "spoken_text_hash": normalized.spoken_text_hash,
            "canonical_timeline_ref": f"canonical-timeline:{compiled_timeline.timeline_hash}",
            "canonical_timeline_hash": compiled_timeline.timeline_hash,
            "cues": [cue.model_dump(mode="json") for cue in cues],
            "srt_text": srt_text,
            "ass_events": [event.model_dump(mode="json") for event in ass_events],
            "spoken_token_coverage": 1.0,
            "missing_spoken_token_ids": [],
            "extra_spoken_token_ids": [],
            "compilation_gate": compilation_gate.model_dump(mode="json"),
            "final_cue_trailing_hold": (
                trailing_hold_evidence.model_dump(mode="json")
                if trailing_hold_evidence is not None
                else None
            ),
            "policy_ref": policy.policy_ref,
            "policy_version": policy.policy_version,
            "policy_hash": _resolved_policy_hash(policy),
        }
        track = CompiledCaptionTrack(**track_payload, content_hash=compilation_hash)
        return CaptionCompilationOutput(timeline=compiled_timeline, track=track)

    @staticmethod
    def _apply_final_cue_trailing_hold(
        *,
        cues: list[CanonicalCaptionCue],
        updated_segments: list[CanonicalTimelineSegment],
        normalized: SpokenTextNormalized,
        alignment: VerifiedNarrationAlignment,
        source_timeline: CanonicalMediaTimeline,
        policy: FinalCueTrailingHoldPolicy,
    ) -> tuple[
        list[CanonicalCaptionCue],
        list[CanonicalTimelineSegment],
        FinalCueTrailingHoldEvidence,
    ]:
        if (
            not cues
            or not updated_segments
            or not normalized.spoken_tokens
            or not alignment.verified_words
        ):
            raise ValueError("CAPTION_TRAILING_HOLD_CANONICAL_ENDPOINT_INVALID")
        final_segment = updated_segments[-1]
        final_source_segment = source_timeline.segments[-1]
        final_cue = cues[-1]
        final_word = alignment.verified_words[-1]
        final_token_id = normalized.spoken_tokens[-1].token_id
        target_ms = source_timeline.audio_duration_ms
        if (
            alignment.audio_duration_ms != target_ms
            or final_source_segment.scene_end_ms != target_ms
            or final_segment.scene_end_ms != target_ms
            or final_source_segment.audio_end_ms != final_word.end_ms
            or final_cue.source_segment_ids != [final_segment.segment_id]
            or final_cue.spoken_token_ids[-1] != final_token_id
            or final_word.source_spoken_token_ids != [final_token_id]
            or final_cue.caption_end_ms != final_word.end_ms
        ):
            raise ValueError("CAPTION_TRAILING_HOLD_CANONICAL_ENDPOINT_INVALID")
        if final_cue.caption_end_ms > target_ms:
            raise ValueError("CAPTION_TRAILING_HOLD_CANONICAL_ENDPOINT_INVALID")
        hold_ms = target_ms - final_cue.caption_end_ms
        if hold_ms > policy.maximum_hold_ms:
            raise ValueError("CAPTION_TRAILING_HOLD_EXCEEDS_POLICY")

        before_ms = final_cue.caption_end_ms
        status = "NOT_REQUIRED"
        reason_code = "CAPTION_FINAL_CUE_ALREADY_REACHES_CANONICAL_AUDIO_END"
        if hold_ms:
            duration_seconds = (target_ms - final_cue.caption_start_ms) / 1000
            reading = final_cue.reading_metrics.model_copy(
                update={
                    "duration_seconds": duration_seconds,
                    "characters_per_second": round(
                        final_cue.reading_metrics.character_count / duration_seconds,
                        3,
                    ),
                }
            )
            cue_payload = final_cue.model_dump(mode="json", exclude={"content_hash"})
            cue_payload.update(
                {
                    "caption_end_ms": target_ms,
                    "reading_metrics": reading.model_dump(mode="json"),
                }
            )
            final_cue = CanonicalCaptionCue(
                **cue_payload,
                content_hash=stable_hash(cue_payload),
            )
            cues = [*cues[:-1], final_cue]
            segment_cues = [*final_segment.caption_cues[:-1], final_cue]
            updated_segments = [
                *updated_segments[:-1],
                final_segment.model_copy(
                    update={
                        "caption_end_ms": target_ms,
                        "caption_cues": segment_cues,
                        "caption_reading_metrics": [
                            cue.reading_metrics for cue in segment_cues
                        ],
                    }
                ),
            ]
            status = "APPLIED"
            reason_code = "CAPTION_FINAL_CUE_HELD_THROUGH_CANONICAL_TRAILING_SILENCE"

        policy_hash = _resolved_policy_hash(policy)
        evidence_payload = {
            "status": status,
            "reason_code": reason_code,
            "target_endpoint": policy.target_endpoint,
            "final_segment_id": final_segment.segment_id,
            "final_spoken_token_id": final_token_id,
            "aligned_word_end_ms": final_word.end_ms,
            "caption_end_before_ms": before_ms,
            "caption_end_after_ms": target_ms,
            "canonical_audio_end_ms": target_ms,
            "hold_duration_ms": hold_ms,
            "maximum_hold_ms": policy.maximum_hold_ms,
            "spoken_token_ids_unchanged": True,
            "spoken_word_timing_unchanged": True,
            "policy_ref": policy.policy_ref,
            "policy_version": policy.policy_version,
            "policy_hash": policy_hash,
        }
        evidence = FinalCueTrailingHoldEvidence(
            **evidence_payload,
            content_hash=stable_hash(evidence_payload),
        )
        return cues, updated_segments, evidence

    @staticmethod
    def _format_policy(
        policy: CaptionStylePolicy, aspect_ratio: str
    ) -> CaptionFormatPolicy:
        normalized = aspect_ratio.replace(" ", "")
        if normalized in {"16:9", "longform_16_9", "LANDSCAPE"}:
            return policy.longform_16_9
        raise ValueError("CAPTION_ASPECT_RATIO_UNSUPPORTED")

    @staticmethod
    def _display_units(
        normalized: SpokenTextNormalized,
        display: DisplayCaptionText | None,
    ) -> list[_DisplayUnit]:
        spoken_by_id = {item.token_id: item for item in normalized.spoken_tokens}
        if display is None:
            display_tokens = [
                CaptionDisplayToken(
                    display_token_id=f"display-{index:04d}",
                    text=token.text,
                    spoken_token_ids=[token.token_id],
                )
                for index, token in enumerate(normalized.spoken_tokens, start=1)
            ]
        else:
            display_tokens = [
                CaptionDisplayToken.model_validate(item.model_dump())
                for item in display.tokens
            ]
        positions = {
            token.token_id: index
            for index, token in enumerate(normalized.spoken_tokens)
        }
        units: list[_DisplayUnit] = []
        last_position = -1
        for index, token in enumerate(display_tokens):
            if any(token_id not in spoken_by_id for token_id in token.spoken_token_ids):
                raise ValueError("CAPTION_DISPLAY_TOKEN_REF_UNKNOWN")
            token_positions = [
                positions[token_id] for token_id in token.spoken_token_ids
            ]
            if token_positions != list(
                range(token_positions[0], token_positions[-1] + 1)
            ):
                raise ValueError("CAPTION_DISPLAY_TOKEN_REF_NONCONTIGUOUS")
            if token_positions[0] <= last_position:
                raise ValueError("CAPTION_DISPLAY_TOKEN_ORDER_INVALID")
            last_position = token_positions[-1]
            spoken_text = " ".join(
                spoken_by_id[token_id].text for token_id in token.spoken_token_ids
            )
            if token.transform_reason_code:
                if token.transform_reason_code not in ALLOWED_DISPLAY_TRANSFORMS:
                    raise ValueError("CAPTION_DISPLAY_TRANSFORM_NOT_ALLOWED")
            elif _comparison_key(token.text) != _comparison_key(spoken_text):
                raise ValueError("CAPTION_SEMANTIC_REWRITE_BLOCKED")
            last_spoken = spoken_by_id[token.spoken_token_ids[-1]]
            if index + 1 < len(display_tokens):
                next_first_id = display_tokens[index + 1].spoken_token_ids[0]
                next_start = spoken_by_id[next_first_id].spoken_span.start
            else:
                next_start = len(normalized.spoken_text)
            separator = normalized.spoken_text[last_spoken.spoken_span.end : next_start]
            punctuation = "".join(re.findall(r"[,.!?;:%…]+", separator))
            units.append(
                _DisplayUnit(
                    display_token=token,
                    rendered_text=token.text + punctuation,
                    spoken_token_ids=tuple(token.spoken_token_ids),
                )
            )
        return units

    @classmethod
    def _cue_batches(
        cls,
        units: list[_DisplayUnit],
        *,
        word_by_token: dict[str, Any],
        format_policy: CaptionFormatPolicy,
        policy: CaptionStylePolicy,
    ) -> list[list[_DisplayUnit]]:
        if not units:
            return []
        result: list[list[_DisplayUnit]] = []
        cursor = 0
        max_total_chars = (
            format_policy.max_chars_per_line_pass
            * policy.global_policy.max_lines_per_cue
        )
        pass_min, pass_max = policy.global_policy.cue_duration_seconds.pass_range
        while cursor < len(units):
            viable: list[int] = []
            for end in range(cursor + 1, len(units) + 1):
                batch = units[cursor:end]
                visible = " ".join(unit.rendered_text for unit in batch)
                ids = [token_id for unit in batch for token_id in unit.spoken_token_ids]
                duration = (
                    word_by_token[ids[-1]].end_ms - word_by_token[ids[0]].start_ms
                ) / 1000
                if len(visible) <= max_total_chars and duration <= pass_max:
                    viable.append(end)
                else:
                    break
            if not viable:
                viable = [cursor + 1]
            eligible = [
                end
                for end in viable
                if (
                    word_by_token[units[end - 1].spoken_token_ids[-1]].end_ms
                    - word_by_token[units[cursor].spoken_token_ids[0]].start_ms
                )
                / 1000
                >= pass_min
            ]
            candidates = eligible or viable
            punctuation_breaks = [
                end
                for end in candidates
                if re.search(r"[.!?;:]$", units[end - 1].rendered_text)
            ]
            selected = punctuation_breaks[-1] if punctuation_breaks else candidates[-1]
            result.append(units[cursor:selected])
            cursor = selected
        if len(result) > 1:
            last = result[-1]
            last_ids = [token_id for unit in last for token_id in unit.spoken_token_ids]
            last_duration = (
                word_by_token[last_ids[-1]].end_ms - word_by_token[last_ids[0]].start_ms
            ) / 1000
            merged_visible = " ".join(
                unit.rendered_text for unit in [*result[-2], *last]
            )
            merged_ids = [
                token_id
                for unit in [*result[-2], *last]
                for token_id in unit.spoken_token_ids
            ]
            merged_duration = (
                word_by_token[merged_ids[-1]].end_ms
                - word_by_token[merged_ids[0]].start_ms
            ) / 1000
            if (
                last_duration
                < policy.global_policy.cue_duration_seconds.review_range[0]
                and len(merged_visible) <= format_policy.max_chars_per_line_block * 2
                and merged_duration
                <= policy.global_policy.cue_duration_seconds.block_outside[1]
            ):
                result[-2:] = [[*result[-2], *last]]
        return result

    @classmethod
    def _wrap_lines(
        cls, units: list[_DisplayUnit], policy: CaptionFormatPolicy
    ) -> list[str]:
        full = " ".join(item.rendered_text for item in units)
        if len(full) <= policy.max_chars_per_line_pass:
            return [full]
        for limit in (
            policy.max_chars_per_line_pass,
            policy.max_chars_per_line_review,
            policy.max_chars_per_line_block,
        ):
            choices: list[tuple[float, int, str, str]] = []
            for split in range(1, len(units)):
                left = " ".join(item.rendered_text for item in units[:split])
                right = " ".join(item.rendered_text for item in units[split:])
                if len(left) > limit or len(right) > limit:
                    continue
                score = abs(len(left) - len(right))
                left_word = re.sub(
                    r"[^A-Za-z0-9'-]", "", units[split - 1].display_token.text
                )
                right_word = re.sub(
                    r"[^A-Za-z0-9'-]", "", units[split].display_token.text
                )
                if re.search(r"[.!?;:,]$", units[split - 1].rendered_text):
                    score -= 50
                if left_word.casefold() in PREPOSITIONS:
                    score += 1_000
                if left_word[:1].isupper() and right_word[:1].isupper():
                    score += 1_000
                last_preposition = max(
                    (
                        index
                        for index, unit in enumerate(units[:split])
                        if unit.display_token.text.casefold() in PREPOSITIONS
                    ),
                    default=-1,
                )
                if last_preposition >= 0 and not any(
                    re.search(r"[.!?;:,]$", item.rendered_text)
                    for item in units[last_preposition:split]
                ):
                    score += 200
                choices.append((score, split, left, right))
            if choices:
                _, _, left, right = min(choices, key=lambda item: (item[0], item[1]))
                return [left, right]
        return [full]

    @staticmethod
    def _srt(cues: Sequence[CanonicalCaptionCue]) -> str:
        blocks = []
        for index, cue in enumerate(cues, start=1):
            blocks.append(
                f"{index}\n{_srt_timestamp(cue.caption_start_ms)} --> {_srt_timestamp(cue.caption_end_ms)}\n"
                + "\n".join(cue.caption_lines)
            )
        return "\n\n".join(blocks) + "\n"

    @staticmethod
    def _ass_escape(value: str) -> str:
        return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


class CaptionCompilationGate:
    def evaluate(
        self,
        *,
        cues: Sequence[CanonicalCaptionCue],
        normalized: SpokenTextNormalized,
        timeline: CanonicalMediaTimeline,
        policy: CaptionStylePolicy | dict[str, Any],
    ) -> CreativeQualityGateResult:
        policy = _caption_style_policy(policy)
        reasons: list[str] = []
        expected = [item.token_id for item in normalized.spoken_tokens]
        actual = [token_id for cue in cues for token_id in cue.spoken_token_ids]
        if not cues:
            reasons.append("CAPTION_CUES_MISSING")
        if actual != expected:
            if set(expected) - set(actual):
                reasons.append("CAPTION_MISSING_SPOKEN_TOKEN")
            if set(actual) - set(expected) or len(actual) != len(set(actual)):
                reasons.append("CAPTION_EXTRA_OR_DUPLICATE_SPOKEN_TOKEN")
        segment_ids = {item.segment_id for item in timeline.segments}
        for cue in cues:
            if cue.timing_source != "CANONICAL_MEDIA_TIMELINE":
                reasons.append("CAPTION_PARALLEL_TIMELINE")
            if len(cue.caption_lines) > policy.global_policy.max_lines_per_cue:
                reasons.append("CAPTION_MORE_THAN_TWO_LINES")
            if any(
                segment_id not in segment_ids for segment_id in cue.source_segment_ids
            ):
                reasons.append("CAPTION_CANONICAL_SEGMENT_REF_MISSING")
            display_ids = [
                token_id
                for token in cue.display_tokens
                for token_id in token.spoken_token_ids
            ]
            if display_ids != cue.spoken_token_ids:
                reasons.append("CAPTION_DISPLAY_TOKEN_MAPPING_INVALID")
            for token in cue.display_tokens:
                if (
                    token.transform_reason_code
                    and token.transform_reason_code not in ALLOWED_DISPLAY_TRANSFORMS
                ):
                    reasons.append("CAPTION_DISPLAY_TRANSFORM_NOT_ALLOWED")
        metrics = {
            "cue_count": len(cues),
            "expected_spoken_token_count": len(expected),
            "caption_spoken_token_count": len(actual),
            "spoken_token_coverage": len(set(actual) & set(expected)) / len(expected)
            if expected
            else 0,
            "timing_source": "CANONICAL_MEDIA_TIMELINE",
        }
        return _gate(
            "CaptionCompilationGate",
            "BLOCK" if reasons else "PASS",
            reasons,
            metrics,
            policy,
        )


class CaptionLayoutGate:
    def evaluate(
        self,
        *,
        cues: Sequence[CanonicalCaptionCue],
        bbox_metrics: Sequence[CaptionBBoxMetrics],
        policy: CaptionStylePolicy | dict[str, Any],
        aspect_ratio: str,
    ) -> CreativeQualityGateResult:
        policy = _caption_style_policy(policy)
        format_policy = ReadableCaptionCompiler._format_policy(policy, aspect_ratio)
        statuses: list[str] = []
        reasons: list[str] = []
        metric_by_id = {item.cue_id: item for item in bbox_metrics}
        all_cps = [cue.reading_metrics.characters_per_second for cue in cues]
        for cue in cues:
            status = "PASS"
            bbox = metric_by_id.get(cue.cue_id)
            reading = cue.reading_metrics
            duration = reading.duration_seconds
            if reading.line_count > policy.global_policy.max_lines_per_cue:
                status = "BLOCK"
                reasons.append("CAPTION_MORE_THAN_TWO_LINES")
            if reading.max_chars_per_line > format_policy.max_chars_per_line_block:
                status = "BLOCK"
                reasons.append("CAPTION_LINE_TOO_LONG")
            elif reading.max_chars_per_line > format_policy.max_chars_per_line_pass:
                status = _worst_status([status, "REVIEW_REQUIRED"])
                reasons.append("CAPTION_LINE_LENGTH_REVIEW")
            block_low, block_high = (
                policy.global_policy.cue_duration_seconds.block_outside
            )
            pass_low, pass_high = policy.global_policy.cue_duration_seconds.pass_range
            if duration < block_low or duration > block_high:
                status = "BLOCK"
                reasons.append("CAPTION_DURATION_OUTSIDE_POLICY")
            elif duration < pass_low or duration > pass_high:
                status = _worst_status([status, "REVIEW_REQUIRED"])
                reasons.append("CAPTION_DURATION_REVIEW")
            if (
                reading.characters_per_second
                > policy.global_policy.reading_speed_cps.block_any_above
            ):
                status = "BLOCK"
                reasons.append("CAPTION_READING_SPEED_TOO_HIGH")
            elif (
                reading.characters_per_second
                > policy.global_policy.reading_speed_cps.pass_average_max
            ):
                status = _worst_status([status, "REVIEW_REQUIRED"])
                reasons.append("CAPTION_READING_SPEED_REVIEW")
            if bbox is None or bbox.width <= 0 or bbox.height <= 0:
                status = "BLOCK"
                reasons.append("CAPTION_BBOX_MISSING")
            else:
                if bbox.block_width_ratio > format_policy.max_block_width_block:
                    status = "BLOCK"
                    reasons.append("CAPTION_BBOX_OVERFLOW")
                elif bbox.block_width_ratio > format_policy.max_block_width_pass:
                    status = _worst_status([status, "REVIEW_REQUIRED"])
                    reasons.append("CAPTION_BLOCK_WIDTH_REVIEW")
                if (
                    not format_policy.block_outside[0]
                    <= bbox.font_scale
                    <= format_policy.block_outside[1]
                ):
                    status = "BLOCK"
                    reasons.append("CAPTION_FONT_SCALE_OUTSIDE_POLICY")
                elif (
                    not format_policy.font_scale_pass[0]
                    <= bbox.font_scale
                    <= format_policy.font_scale_pass[1]
                ):
                    status = _worst_status([status, "REVIEW_REQUIRED"])
                    reasons.append("CAPTION_FONT_SCALE_REVIEW")
            statuses.append(status)
        average_cps = statistics.fmean(all_cps) if all_cps else 0.0
        p95_cps = _percentile(all_cps, 0.95)
        if average_cps > policy.global_policy.reading_speed_cps.block_average_above:
            statuses.append("BLOCK")
            reasons.append("CAPTION_AVERAGE_READING_SPEED_TOO_HIGH")
        elif average_cps > policy.global_policy.reading_speed_cps.pass_average_max:
            statuses.append("REVIEW_REQUIRED")
            reasons.append("CAPTION_AVERAGE_READING_SPEED_REVIEW")
        if p95_cps > policy.global_policy.reading_speed_cps.block_any_above:
            statuses.append("BLOCK")
            reasons.append("CAPTION_P95_READING_SPEED_TOO_HIGH")
        elif p95_cps > policy.global_policy.reading_speed_cps.pass_p95_max:
            statuses.append("REVIEW_REQUIRED")
            reasons.append("CAPTION_P95_READING_SPEED_REVIEW")
        return _gate(
            "CaptionLayoutGate",
            _worst_status(statuses),
            reasons,
            {
                "cue_count": len(cues),
                "average_cps": round(average_cps, 3),
                "p95_cps": p95_cps,
                "bbox_count": len(bbox_metrics),
            },
            policy,
        )


class CaptionSafeAreaGate:
    def evaluate(
        self,
        *,
        bbox_metrics: Sequence[CaptionBBoxMetrics],
        policy: CaptionStylePolicy | dict[str, Any],
        aspect_ratio: str,
    ) -> CreativeQualityGateResult:
        policy = _caption_style_policy(policy)
        format_policy = ReadableCaptionCompiler._format_policy(policy, aspect_ratio)
        statuses: list[str] = []
        reasons: list[str] = []
        for metric in bbox_metrics:
            status = "PASS"
            if metric.text_outside_frame or metric.width <= 0 or metric.height <= 0:
                status = "BLOCK"
                reasons.append("CAPTION_TEXT_OUTSIDE_FRAME")
            if metric.required_safe_zone_overlap:
                status = "BLOCK"
                reasons.append("CAPTION_REQUIRED_VISUAL_SAFE_ZONE_OVERLAP")
            if metric.bottom_margin_ratio < format_policy.bottom_safe_margin_review_min:
                status = "BLOCK"
                reasons.append("CAPTION_UNSAFE_BOTTOM_MARGIN")
            elif metric.bottom_margin_ratio < format_policy.bottom_safe_margin_pass:
                status = _worst_status([status, "REVIEW_REQUIRED"])
                reasons.append("CAPTION_BOTTOM_MARGIN_REVIEW")
            if metric.block_width_ratio > format_policy.max_block_width_block:
                status = "BLOCK"
                reasons.append("CAPTION_BBOX_OVERFLOW")
            statuses.append(status)
        if not bbox_metrics:
            statuses.append("BLOCK")
            reasons.append("CAPTION_BBOX_MISSING")
        return _gate(
            "CaptionSafeAreaGate",
            _worst_status(statuses),
            reasons,
            {"cue_count": len(bbox_metrics)},
            policy,
        )


class CaptionBoundsPreflight:
    """Uses the production libass chain and parses FFmpeg's measured bbox evidence."""

    def __init__(
        self,
        *,
        ffmpeg_binary: str = FFMPEG_FULL_DEFAULT,
        runner: Callable[..., Any] | None = None,
    ):
        self.ffmpeg_binary = ffmpeg_binary
        self.runner = runner or subprocess.run

    def preflight(
        self,
        *,
        cues: Sequence[CanonicalCaptionCue],
        frame_width: int,
        frame_height: int,
        policy: CaptionStylePolicy | dict[str, Any],
        aspect_ratio: str,
        evidence_dir: Path | None = None,
        required_safe_zones: Sequence[dict[str, int]] = (),
    ) -> CaptionBoundsPreflightReport:
        policy = _caption_style_policy(policy)
        format_policy = ReadableCaptionCompiler._format_policy(policy, aspect_ratio)
        render_style = resolved_caption_render_style(
            policy=policy,
            format_policy=format_policy,
            aspect_ratio=aspect_ratio,
            policy_hash=_resolved_policy_hash(policy),
        )
        font_scale = float(render_style["font_scale"])
        metrics: list[CaptionBBoxMetrics] = []
        with tempfile.TemporaryDirectory(prefix="cqr1b-caption-bbox-") as temporary:
            work = evidence_dir.resolve() if evidence_dir else Path(temporary)
            work.mkdir(parents=True, exist_ok=True)
            for cue in cues:
                ass_path = work / f"{cue.cue_id}.ass"
                preview_path = work / f"{cue.cue_id}-alpha.png"
                ass_path.write_text(
                    build_caption_ass_document(
                        cues=[cue],
                        frame_width=frame_width,
                        frame_height=frame_height,
                        render_style=render_style,
                        force_event_window_ms=(0, 1_000),
                    ),
                    encoding="utf-8",
                )
                filter_value = (
                    "format=rgba,"
                    f"ass=filename='{self._filter_escape(ass_path)}':alpha=1,"
                    "alphaextract,bbox=min_val=1"
                )
                argv = [
                    self.ffmpeg_binary,
                    "-hide_banner",
                    "-nostdin",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    # The color source defaults to an opaque YUV format even when
                    # its color carries @0.0 alpha.  Converting that opaque frame
                    # to RGBA later would make alphaextract/bbox report the whole
                    # canvas.  Preserve transparency at the source boundary so
                    # bbox measures only the libass glyph/outline pixels.
                    (
                        f"color=c=black@0.0:s={frame_width}x{frame_height}:r=1:d=1,"
                        "format=rgba"
                    ),
                    "-vf",
                    filter_value,
                    "-frames:v",
                    "1",
                    str(preview_path),
                ]
                completed = self.runner(
                    argv, capture_output=True, text=True, shell=False
                )
                stderr = str(getattr(completed, "stderr", "") or "")
                if getattr(completed, "returncode", 1) != 0:
                    raise ValueError(
                        f"CAPTION_BBOX_PREFLIGHT_FAILED:{cue.cue_id}:{stderr[-500:]}"
                    )
                matches = list(_BBOX_RE.finditer(stderr))
                if not matches:
                    raise ValueError(f"CAPTION_BBOX_NOT_DETECTED:{cue.cue_id}")
                match = matches[-1]
                x1, x2, y1, y2, width, height = (int(value) for value in match.groups())
                outside = x1 < 0 or y1 < 0 or x2 >= frame_width or y2 >= frame_height
                overlap = any(
                    x1 < zone["x"] + zone["width"]
                    and x2 + 1 > zone["x"]
                    and y1 < zone["y"] + zone["height"]
                    and y2 + 1 > zone["y"]
                    for zone in required_safe_zones
                )
                metrics.append(
                    CaptionBBoxMetrics(
                        cue_id=cue.cue_id,
                        frame_width=frame_width,
                        frame_height=frame_height,
                        x=max(0, x1),
                        y=max(0, y1),
                        width=width,
                        height=height,
                        block_width_ratio=round(width / frame_width, 6),
                        left_margin_ratio=round(max(0, x1) / frame_width, 6),
                        right_margin_ratio=round(
                            max(0, frame_width - (x2 + 1)) / frame_width, 6
                        ),
                        top_margin_ratio=round(max(0, y1) / frame_height, 6),
                        bottom_margin_ratio=round(
                            max(0, frame_height - (y2 + 1)) / frame_height, 6
                        ),
                        font_scale=font_scale,
                        line_count=cue.reading_metrics.line_count,
                        cpl=cue.reading_metrics.max_chars_per_line,
                        cps=cue.reading_metrics.characters_per_second,
                        duration_seconds=cue.reading_metrics.duration_seconds,
                        text_outside_frame=outside,
                        required_safe_zone_overlap=overlap,
                        preview_frame_ref=str(preview_path) if evidence_dir else None,
                        ffmpeg_stderr_excerpt="\n".join(
                            line for line in stderr.splitlines() if "bbox" in line
                        )[-2_000:],
                    )
                )
        layout = CaptionLayoutGate().evaluate(
            cues=cues,
            bbox_metrics=metrics,
            policy=policy,
            aspect_ratio=aspect_ratio,
        )
        safe = CaptionSafeAreaGate().evaluate(
            bbox_metrics=metrics,
            policy=policy,
            aspect_ratio=aspect_ratio,
        )
        payload = {
            "ffmpeg_binary": self.ffmpeg_binary,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "cue_metrics": [item.model_dump(mode="json") for item in metrics],
            "layout_gate": layout.model_dump(mode="json"),
            "safe_area_gate": safe.model_dump(mode="json"),
            "policy_ref": policy.policy_ref,
            "policy_version": policy.policy_version,
            "policy_hash": _resolved_policy_hash(policy),
        }
        return CaptionBoundsPreflightReport(
            **payload, content_hash=stable_hash(payload)
        )

    def apply_to_timeline(
        self,
        timeline: CanonicalMediaTimeline,
        report: CaptionBoundsPreflightReport,
    ) -> CanonicalMediaTimeline:
        metrics = {item.cue_id: item for item in report.cue_metrics}
        segments = []
        for segment in timeline.segments:
            cues = []
            for cue in segment.caption_cues:
                bbox = metrics.get(cue.cue_id)
                cue_payload = cue.model_dump(mode="json", exclude={"content_hash"})
                cue_payload["bbox_metrics"] = (
                    bbox.model_dump(mode="json") if bbox else None
                )
                cue_payload["gate_results"] = [
                    *[item.model_dump(mode="json") for item in cue.gate_results],
                    report.layout_gate.model_dump(mode="json"),
                    report.safe_area_gate.model_dump(mode="json"),
                ]
                cues.append(
                    CanonicalCaptionCue(
                        **cue_payload, content_hash=stable_hash(cue_payload)
                    )
                )
            segments.append(
                segment.model_copy(
                    update={
                        "caption_cues": cues,
                        "caption_bbox_metrics": [
                            item.bbox_metrics for item in cues if item.bbox_metrics
                        ],
                        "caption_gate_results": [
                            report.layout_gate,
                            report.safe_area_gate,
                        ],
                    }
                )
            )
        payload = timeline.model_dump(mode="json", exclude={"timeline_hash"})
        payload["segments"] = [item.model_dump(mode="json") for item in segments]
        payload["qc_metrics"] = {
            **timeline.qc_metrics,
            "caption_bbox_preflight_hash": report.content_hash,
            "caption_layout_gate": report.layout_gate.status,
            "caption_safe_area_gate": report.safe_area_gate.status,
        }
        return CanonicalMediaTimeline(**payload, timeline_hash=stable_hash(payload))

    @staticmethod
    def _filter_escape(path: Path) -> str:
        return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    @staticmethod
    def _ass_time(milliseconds: int) -> str:
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1_000)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"

    @classmethod
    def _ass_document(
        cls,
        *,
        cue: CanonicalCaptionCue,
        frame_width: int,
        frame_height: int,
        font_scale: float,
        bottom_margin_ratio: float,
        policy: CaptionStylePolicy,
    ) -> str:
        format_policy = CaptionFormatPolicy(
            font_scale_pass=(font_scale, font_scale),
            font_scale_review=(font_scale, font_scale),
            block_outside=(font_scale, font_scale),
            max_chars_per_line_pass=1,
            max_chars_per_line_review=1,
            max_chars_per_line_block=1,
            max_block_width_pass=1,
            max_block_width_review=1,
            max_block_width_block=1,
            bottom_safe_margin_pass=bottom_margin_ratio,
            bottom_safe_margin_review_min=bottom_margin_ratio,
        )
        style = resolved_caption_render_style(
            policy=policy,
            format_policy=format_policy,
            aspect_ratio="PREFLIGHT_COMPAT",
            policy_hash=_resolved_policy_hash(policy),
        )
        return build_caption_ass_document(
            cues=[cue],
            frame_width=frame_width,
            frame_height=frame_height,
            render_style=style,
            force_event_window_ms=(0, 1_000),
        )


def _timeline_cues(timeline: CanonicalMediaTimeline) -> list[CanonicalCaptionCue]:
    return [cue for segment in timeline.segments for cue in segment.caption_cues]


def _timeline_final_cue_trailing_hold(
    timeline: CanonicalMediaTimeline,
    cues: Sequence[CanonicalCaptionCue],
) -> tuple[FinalCueTrailingHoldEvidence | None, str | None]:
    raw = timeline.qc_metrics.get("caption_final_cue_trailing_hold")
    if raw is None:
        return None, None
    try:
        evidence = FinalCueTrailingHoldEvidence.model_validate(raw)
    except (TypeError, ValueError):
        return None, "SYNC_TRAILING_HOLD_EVIDENCE_INVALID"
    payload = evidence.model_dump(mode="json", exclude={"content_hash"})
    if stable_hash(payload) != evidence.content_hash:
        return None, "SYNC_TRAILING_HOLD_EVIDENCE_INVALID"
    evidence_policy = FinalCueTrailingHoldPolicy(
        policy_ref=evidence.policy_ref,
        policy_version=evidence.policy_version,
        maximum_hold_ms=evidence.maximum_hold_ms,
        target_endpoint=evidence.target_endpoint,
    )
    if _policy_hash(evidence_policy) != evidence.policy_hash:
        return None, "SYNC_TRAILING_HOLD_EVIDENCE_INVALID"
    if not cues or not timeline.segments:
        return None, "SYNC_TRAILING_HOLD_EVIDENCE_INVALID"
    final_cue = cues[-1]
    final_segment = timeline.segments[-1]
    if (
        evidence.final_segment_id != final_segment.segment_id
        or final_cue.source_segment_ids != [final_segment.segment_id]
        or final_cue.spoken_token_ids[-1] != evidence.final_spoken_token_id
        or final_cue.caption_end_ms != evidence.caption_end_after_ms
        or evidence.canonical_audio_end_ms != timeline.audio_duration_ms
        or final_segment.scene_end_ms != timeline.audio_duration_ms
    ):
        return None, "SYNC_TRAILING_HOLD_EVIDENCE_INVALID"
    return evidence, None


class CaptionCoverageGate:
    def evaluate(
        self,
        *,
        normalized: SpokenTextNormalized,
        timeline: CanonicalMediaTimeline,
        policy: CaptionSyncPolicy | dict[str, Any],
    ) -> CreativeQualityGateResult:
        policy = _caption_sync_policy(policy)
        expected = [item.token_id for item in normalized.spoken_tokens]
        cues = _timeline_cues(timeline)
        actual = [token_id for cue in cues for token_id in cue.spoken_token_ids]
        reasons: list[str] = []
        missing = [token_id for token_id in expected if token_id not in set(actual)]
        extra = [token_id for token_id in actual if token_id not in set(expected)]
        duplicates = sorted(
            {token_id for token_id in actual if actual.count(token_id) > 1}
        )
        if (
            missing
            or len(set(actual) & set(expected)) / max(1, len(expected))
            < policy.spoken_token_coverage_required
        ):
            reasons.append("SYNC_COVERAGE_GAP")
        if extra or duplicates:
            reasons.append("SYNC_EXTRA_TOKEN")
        display_mapping_invalid = False
        for cue in cues:
            mapped = [
                token_id
                for token in cue.display_tokens
                for token_id in token.spoken_token_ids
            ]
            if mapped != cue.spoken_token_ids:
                display_mapping_invalid = True
            if any(
                token.transform_reason_code
                and token.transform_reason_code not in ALLOWED_DISPLAY_TRANSFORMS
                for token in cue.display_tokens
            ):
                display_mapping_invalid = True
        if display_mapping_invalid:
            reasons.append("SYNC_EXTRA_TOKEN")
        coverage = len(set(actual) & set(expected)) / len(expected) if expected else 0.0
        return _gate(
            "CaptionCoverageGate",
            "BLOCK" if reasons else "PASS",
            reasons,
            {
                "spoken_token_coverage": round(coverage, 6),
                "missing_spoken_token_ids": missing,
                "extra_spoken_token_ids": extra,
                "duplicate_spoken_token_ids": duplicates,
            },
            policy,
        )


class CaptionAudioSyncGate:
    def evaluate(
        self,
        *,
        timeline: CanonicalMediaTimeline,
        alignment: VerifiedNarrationAlignment,
        policy: CaptionSyncPolicy | dict[str, Any],
    ) -> CreativeQualityGateResult:
        policy = _caption_sync_policy(policy)
        cues = _timeline_cues(timeline)
        word_by_token = {
            token_id: word
            for word in alignment.verified_words
            for token_id in word.source_spoken_token_ids
        }
        start_offsets: list[int] = []
        end_offsets: list[int] = []
        reasons: list[str] = []
        trailing_hold, trailing_hold_error = _timeline_final_cue_trailing_hold(
            timeline, cues
        )
        if trailing_hold_error:
            reasons.append(trailing_hold_error)
        authorized_trailing_hold_ms = 0
        raw_final_end_offset_ms = 0
        overlap_count = 0
        non_monotonic_count = 0
        outside_count = 0
        previous_start = -1
        previous_end = -1
        for cue_index, cue in enumerate(cues):
            words = [word_by_token.get(token_id) for token_id in cue.spoken_token_ids]
            if any(word is None for word in words):
                reasons.append("SYNC_COVERAGE_GAP")
                continue
            expected_start = words[0].start_ms
            expected_end = words[-1].end_ms
            start_offsets.append(abs(cue.caption_start_ms - expected_start))
            raw_end_offset = abs(cue.caption_end_ms - expected_end)
            is_final_cue = cue_index == len(cues) - 1
            authorized_final_hold = bool(
                is_final_cue
                and trailing_hold is not None
                and trailing_hold.status == "APPLIED"
                and trailing_hold.aligned_word_end_ms == expected_end
                and trailing_hold.caption_end_before_ms == expected_end
                and trailing_hold.caption_end_after_ms == cue.caption_end_ms
                and trailing_hold.hold_duration_ms == raw_end_offset
                and cue.caption_end_ms == alignment.audio_duration_ms
            )
            if is_final_cue:
                raw_final_end_offset_ms = raw_end_offset
                if (
                    cue.caption_end_ms == alignment.audio_duration_ms
                    and expected_end < alignment.audio_duration_ms
                    and not authorized_final_hold
                ):
                    reasons.append("SYNC_UNAUTHORIZED_FINAL_CUE_TRAILING_HOLD")
            if authorized_final_hold:
                authorized_trailing_hold_ms = trailing_hold.hold_duration_ms
                end_offsets.append(0)
            else:
                end_offsets.append(raw_end_offset)
            if (
                cue.caption_start_ms < previous_start
                or cue.caption_end_ms <= cue.caption_start_ms
            ):
                non_monotonic_count += 1
            if cue.caption_start_ms < previous_end:
                overlap_count += 1
            if (
                cue.caption_start_ms < 0
                or cue.caption_end_ms > alignment.audio_duration_ms
            ):
                outside_count += 1
            if cue.timing_source != "CANONICAL_MEDIA_TIMELINE":
                reasons.append("SYNC_PARALLEL_TIMELINE")
            previous_start = cue.caption_start_ms
            previous_end = cue.caption_end_ms
        if overlap_count and policy.unexpected_cue_overlap_block:
            reasons.append("SYNC_CUE_OVERLAP")
        if non_monotonic_count or outside_count:
            reasons.append("SYNC_PARALLEL_TIMELINE")
        # Coverage is computed directly here because this gate intentionally accepts
        # only alignment/timeline evidence, not a second transcript object.
        expected_tokens = {
            token_id
            for word in alignment.verified_words
            for token_id in word.source_spoken_token_ids
        }
        actual_tokens = [token_id for cue in cues for token_id in cue.spoken_token_ids]
        coverage = (
            len(set(actual_tokens) & expected_tokens) / len(expected_tokens)
            if expected_tokens
            else 0.0
        )
        if (
            coverage < policy.spoken_token_coverage_required
            or set(actual_tokens) != expected_tokens
        ):
            reasons.append("SYNC_COVERAGE_GAP")
        if (
            len(actual_tokens) != len(set(actual_tokens))
            or set(actual_tokens) - expected_tokens
        ):
            reasons.append("SYNC_EXTRA_TOKEN")
        last_caption_end = cues[-1].caption_end_ms if cues else 0
        sync_metrics = CaptionSyncMetrics(
            median_abs_start_offset_ms=_median(start_offsets) or 0,
            p95_abs_start_offset_ms=_percentile(start_offsets, 0.95),
            max_abs_start_offset_ms=max(start_offsets, default=0),
            median_abs_end_offset_ms=_median(end_offsets) or 0,
            end_of_video_drift_ms=abs(alignment.audio_duration_ms - last_caption_end),
            spoken_token_coverage=round(coverage, 6),
            unexpected_cue_overlap_count=overlap_count,
            non_monotonic_cue_count=non_monotonic_count,
            cue_outside_audio_count=outside_count,
        )
        statuses = ["BLOCK" if reasons else "PASS"]
        for value, threshold, reason in (
            (
                sync_metrics.median_abs_start_offset_ms,
                policy.median_abs_start_offset_ms,
                "SYNC_START_OFFSET",
            ),
            (
                sync_metrics.p95_abs_start_offset_ms,
                policy.p95_abs_start_offset_ms,
                "SYNC_START_OFFSET",
            ),
            (
                sync_metrics.max_abs_start_offset_ms,
                policy.max_abs_start_offset_ms,
                "SYNC_START_OFFSET",
            ),
            (
                sync_metrics.median_abs_end_offset_ms,
                policy.median_abs_end_offset_ms,
                "SYNC_END_OFFSET",
            ),
            (
                sync_metrics.end_of_video_drift_ms,
                policy.end_of_video_drift_ms,
                "SYNC_END_DRIFT",
            ),
        ):
            status = self._offset_status(value, threshold)
            statuses.append(status)
            if status != "PASS":
                reasons.append(reason)
        sync_metric_payload = sync_metrics.model_dump(mode="json")
        sync_metric_payload.update(
            {
                "raw_final_cue_end_offset_ms": raw_final_end_offset_ms,
                "authorized_final_cue_trailing_hold_ms": authorized_trailing_hold_ms,
                "final_cue_trailing_hold_evidence_hash": (
                    trailing_hold.content_hash if trailing_hold is not None else None
                ),
            }
        )
        return _gate(
            "CaptionAudioSyncGate",
            _worst_status(statuses),
            reasons,
            sync_metric_payload,
            policy,
        )

    @staticmethod
    def _offset_status(value: float, threshold: OffsetThreshold) -> str:
        if value > threshold.block_above:
            return "BLOCK"
        if value > threshold.pass_max:
            return "REVIEW_REQUIRED"
        return "PASS"


class TimelineDriftGate:
    def evaluate(
        self,
        *,
        timeline: CanonicalMediaTimeline,
        final_audio_duration_ms: int,
        policy: CaptionSyncPolicy | dict[str, Any],
    ) -> CreativeQualityGateResult:
        policy = _caption_sync_policy(policy)
        cues = _timeline_cues(timeline)
        reasons: list[str] = []
        statuses: list[str] = []
        caption_end = cues[-1].caption_end_ms if cues else 0
        scene_end = timeline.segments[-1].scene_end_ms if timeline.segments else 0
        caption_drift = abs(final_audio_duration_ms - caption_end)
        scene_drift = abs(final_audio_duration_ms - scene_end)
        timeline_drift = abs(final_audio_duration_ms - timeline.audio_duration_ms)
        for value in (caption_drift, scene_drift, timeline_drift):
            status = CaptionAudioSyncGate._offset_status(
                value, policy.end_of_video_drift_ms
            )
            statuses.append(status)
            if status != "PASS":
                reasons.append("SYNC_END_DRIFT")
        if (
            timeline.audio_duration_ms != final_audio_duration_ms
            or scene_end != timeline.audio_duration_ms
        ):
            reasons.append("SYNC_PARALLEL_TIMELINE")
            statuses.append(
                "BLOCK"
                if max(scene_drift, timeline_drift)
                > policy.end_of_video_drift_ms.block_above
                else "REVIEW_REQUIRED"
            )
        return _gate(
            "TimelineDriftGate",
            _worst_status(statuses),
            reasons,
            {
                "canonical_timeline_duration_ms": timeline.audio_duration_ms,
                "final_audio_duration_ms": final_audio_duration_ms,
                "final_caption_end_ms": caption_end,
                "final_scene_end_ms": scene_end,
                "caption_end_drift_ms": caption_drift,
                "scene_end_drift_ms": scene_drift,
                "timeline_audio_drift_ms": timeline_drift,
            },
            policy,
        )
