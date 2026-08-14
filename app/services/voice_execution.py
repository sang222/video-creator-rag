"""Fail-closed execution helpers for frozen narration authority.

This module is deliberately provider-transport agnostic.  It verifies the
immutable voice lineage, produces safe per-segment provider projections, and
owns local stitching/QC.  The adapter remains responsible for credentials and
the one-shot provider transport.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailureError
from app.services.config_registry import content_hash

ELEVENLABS_CAPABILITY_PROFILE_VERSION = "vcos.elevenlabs-capabilities.2026-08-14.v2"


@dataclass(frozen=True, slots=True)
class ElevenLabsModelCapabilityProfile:
    model_id: str
    max_characters: int
    supports_voice_settings: bool
    supports_speed: bool
    supports_audio_tags: bool
    supports_previous_text: bool
    supports_next_text: bool
    supports_request_id_stitching: bool
    supports_seed: bool
    supports_timestamps: bool


# Verified against the first-party Create speech and TTS capability docs on
# 2026-08-14.  Unknown models are intentionally unsupported until reviewed.
_CAPABILITIES: dict[str, ElevenLabsModelCapabilityProfile] = {
    "eleven_multilingual_v2": ElevenLabsModelCapabilityProfile(
        "eleven_multilingual_v2",
        10_000,
        True,
        True,
        False,
        True,
        True,
        True,
        True,
        True,
    ),
    "eleven_flash_v2_5": ElevenLabsModelCapabilityProfile(
        "eleven_flash_v2_5", 40_000, True, True, False, True, True, True, True, True
    ),
    "eleven_turbo_v2_5": ElevenLabsModelCapabilityProfile(
        "eleven_turbo_v2_5", 40_000, True, True, False, True, True, True, True, True
    ),
    # v3 expressive controls are tags, not the normal similarity/style/speaker
    # boost settings.  Request-ID stitching is not enabled without a model-
    # specific confirmation, so multi-request v3 routes to seam QC instead.
    "eleven_v3": ElevenLabsModelCapabilityProfile(
        "eleven_v3", 5_000, False, False, True, True, True, False, True, True
    ),
}


def elevenlabs_capability(model_id: str) -> ElevenLabsModelCapabilityProfile:
    try:
        return _CAPABILITIES[model_id]
    except KeyError as exc:
        raise ValidationFailureError("ELEVENLABS_MODEL_CAPABILITY_UNKNOWN") from exc


def select_execution_strategy(*, model_id: str, segment_count: int) -> str:
    capability = elevenlabs_capability(model_id)
    if segment_count < 1:
        raise ValidationFailureError("NARRATION_SEGMENTS_REQUIRED")
    if segment_count == 1:
        return "SINGLE_REQUEST_EXPRESSIVE"
    if capability.supports_request_id_stitching:
        return "CONTEXT_STITCHED_MULTI_REQUEST"
    return "SEGMENTED_WITH_SEAM_QC"


def provider_text_projection(
    *,
    canonical_text: str,
    context: Mapping[str, Any],
    capability: ElevenLabsModelCapabilityProfile,
) -> dict[str, Any]:
    """Return only provider fields known to be valid for this frozen model."""

    text = canonical_text.strip()
    if not text or len(text) > capability.max_characters:
        raise ValidationFailureError("NARRATION_PROVIDER_TEXT_LENGTH_INVALID")
    output: dict[str, Any] = {"text": text, "apply_text_normalization": "off"}
    for field, enabled in (
        ("previous_text", capability.supports_previous_text),
        ("next_text", capability.supports_next_text),
        ("previous_request_ids", capability.supports_request_id_stitching),
        ("next_request_ids", capability.supports_request_id_stitching),
        ("seed", capability.supports_seed),
    ):
        value = context.get(field)
        if value is None:
            continue
        if not enabled:
            raise ValidationFailureError(f"ELEVENLABS_CONTEXT_UNSUPPORTED:{field}")
        output[field] = value
    return output


def narration_text_fidelity_gate(
    *, canonical_text: str, segments: Sequence[Mapping[str, Any]]
) -> None:
    """Prove ordered exact coverage before any paid request is made."""

    if not canonical_text.strip() or not segments:
        raise ValidationFailureError("NARRATION_TEXT_FIDELITY_GATE_FAILED")
    ordered = sorted(segments, key=lambda item: int(item["source_text_start"]))
    cursor = 0
    pieces: list[str] = []
    ids: set[str] = set()
    for item in ordered:
        segment_id = str(item.get("segment_id") or "")
        start, end = int(item["source_text_start"]), int(item["source_text_end"])
        if (
            not segment_id
            or segment_id in ids
            or start != cursor
            or end <= start
            or end > len(canonical_text)
        ):
            raise ValidationFailureError("NARRATION_TEXT_FIDELITY_GATE_FAILED")
        text = canonical_text[start:end]
        if item.get("text_hash") != content_hash({"text": text}):
            raise ValidationFailureError("NARRATION_TEXT_FIDELITY_GATE_FAILED")
        ids.add(segment_id)
        pieces.append(text)
        cursor = end
    if cursor != len(canonical_text) or "".join(pieces) != canonical_text:
        raise ValidationFailureError("NARRATION_TEXT_FIDELITY_GATE_FAILED")


def frozen_voice_authority_gate(
    *, authority: Mapping[str, Any], script_hash: str, voice_id: str, model_id: str
) -> None:
    required = (
        "approved_voice_pool_id",
        "approved_voice_pool_hash",
        "voice_casting_decision_id",
        "voice_casting_decision_hash",
        "narration_voice_snapshot_id",
        "narration_voice_snapshot_hash",
        "narration_performance_plan_id",
        "narration_performance_plan_hash",
        "tts_performance_projection_id",
        "tts_performance_projection_hash",
    )
    if authority.get("authority_mode") != "FROZEN_PROJECT_VOICE_AUTHORITY" or any(
        not isinstance(authority.get(key), str) or not authority[key]
        for key in required
    ):
        raise ValidationFailureError("REAL_PRODUCTION_VOICE_AUTHORITY_REQUIRED")
    if authority.get("qualified_script_hash") not in {None, script_hash}:
        raise ValidationFailureError("NARRATION_VOICE_SCRIPT_HASH_MISMATCH")
    if authority.get("voice_id") not in {None, voice_id} or authority.get(
        "model_id"
    ) not in {None, model_id}:
        raise ValidationFailureError("NARRATION_VOICE_IDENTITY_MISMATCH")


@dataclass(frozen=True, slots=True)
class CombinedReplacementBudget:
    new_tts_projected_cost_usd: Decimal
    forced_alignment_projected_cost_usd: Decimal
    ai_image_projected_cost_usd: Decimal
    ai_video_projected_cost_usd: Decimal
    other_metered_effects_projected_cost_usd: Decimal
    approved_ceiling_usd: Decimal

    @property
    def projected_incremental_cost_usd(self) -> Decimal:
        return sum(
            (
                self.new_tts_projected_cost_usd,
                self.forced_alignment_projected_cost_usd,
                self.ai_image_projected_cost_usd,
                self.ai_video_projected_cost_usd,
                self.other_metered_effects_projected_cost_usd,
            ),
            Decimal(0),
        )

    def require_authorized(self) -> None:
        if self.projected_incremental_cost_usd > self.approved_ceiling_usd:
            raise ValidationFailureError("COMBINED_REPLACEMENT_BUDGET_INSUFFICIENT")

    def report(self) -> dict[str, str]:
        total = self.projected_incremental_cost_usd
        return {
            "new_tts_projected_cost_usd": str(self.new_tts_projected_cost_usd),
            "forced_alignment_projected_cost_usd": str(
                self.forced_alignment_projected_cost_usd
            ),
            "ai_image_projected_cost_usd": str(self.ai_image_projected_cost_usd),
            "ai_video_projected_cost_usd": str(self.ai_video_projected_cost_usd),
            "other_metered_effects_projected_cost_usd": str(
                self.other_metered_effects_projected_cost_usd
            ),
            "combined_replacement_projected_cost_usd": str(total),
            "approved_ceiling_usd": str(self.approved_ceiling_usd),
            "shortfall_usd": str(max(Decimal(0), total - self.approved_ceiling_usd)),
        }


COMBINED_REPLACEMENT_BUDGET_SCHEMA = "vcos.combined-replacement-budget.v1"
_COMBINED_REPLACEMENT_BUDGET_FIELDS = (
    "new_tts_projected_cost_usd",
    "forced_alignment_projected_cost_usd",
    "ai_image_projected_cost_usd",
    "ai_video_projected_cost_usd",
    "other_metered_effects_projected_cost_usd",
    "approved_ceiling_usd",
)


def combined_replacement_budget_authority(
    authority: Mapping[str, Any],
) -> tuple[CombinedReplacementBudget, str, str]:
    """Resolve an exact, hash-sealed cost projection before paid TTS.

    Route ceilings and optional provider fields are not component cost
    evidence.  The caller must carry this complete package-bound authority;
    absent or malformed components are intentionally not treated as zero.
    """

    if (
        authority.get("schema_version") != COMBINED_REPLACEMENT_BUDGET_SCHEMA
        or authority.get("state") != "FROZEN"
        or not isinstance(authority.get("authority_ref"), str)
        or not authority["authority_ref"].strip()
        or not isinstance(authority.get("authority_hash"), str)
    ):
        raise ValidationFailureError("COMBINED_REPLACEMENT_BUDGET_AUTHORITY_REQUIRED")
    body = {key: authority.get(key) for key in authority if key != "authority_hash"}
    if authority["authority_hash"] != content_hash(body):
        raise ValidationFailureError("COMBINED_REPLACEMENT_BUDGET_AUTHORITY_MISMATCH")
    values: list[Decimal] = []
    for field in _COMBINED_REPLACEMENT_BUDGET_FIELDS:
        value = authority.get(field)
        if value is None:
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_BUDGET_COMPONENT_REQUIRED:" + field
            )
        try:
            decimal = Decimal(str(value))
        except Exception as exc:
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_BUDGET_COMPONENT_INVALID:" + field
            ) from exc
        if not decimal.is_finite() or decimal < 0:
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_BUDGET_COMPONENT_INVALID:" + field
            )
        values.append(decimal)
    return CombinedReplacementBudget(*values), authority["authority_ref"], authority[
        "authority_hash"
    ]


@dataclass(frozen=True, slots=True)
class NarrationSeamQCReport:
    state: str
    reason_codes: tuple[str, ...]
    segment_count: int
    content_hash: str


def seam_qc(
    *,
    segments: Sequence[Mapping[str, Any]],
    stitched_duration_ms: int | None = None,
    encoder_tolerance_ms: int = 250,
) -> NarrationSeamQCReport:
    reasons: list[str] = []
    prior_end = -1
    seen: set[int] = set()
    for expected, item in enumerate(segments):
        index = int(item.get("segment_index", -1))
        duration = int(item.get("duration_ms") or 0)
        start = int(item.get("canonical_start_ms") or prior_end + 1)
        if index != expected or index in seen:
            reasons.append("SEGMENT_ORDER_INVALID")
        if duration <= 0 or not item.get("audio_checksum"):
            reasons.append("SEGMENT_AUDIO_INVALID")
        if prior_end >= 0 and start < prior_end:
            reasons.append("SEGMENT_TIMELINE_OVERLAP")
        seen.add(index)
        prior_end = start + duration
    summed_duration = sum(int(item.get("duration_ms") or 0) for item in segments)
    if stitched_duration_ms is not None and (
        stitched_duration_ms <= 0
        or encoder_tolerance_ms < 0
        or abs(stitched_duration_ms - summed_duration) > encoder_tolerance_ms
    ):
        reasons.append("STITCHED_DURATION_OUTSIDE_ENCODER_TOLERANCE")
    state = "FAIL" if reasons else "PASS"
    body = {
        "state": state,
        "reason_codes": sorted(set(reasons)),
        "segment_count": len(segments),
        "summed_segment_duration_ms": summed_duration,
        "stitched_duration_ms": stitched_duration_ms,
        "encoder_tolerance_ms": encoder_tolerance_ms,
    }
    return NarrationSeamQCReport(
        state, tuple(body["reason_codes"]), len(segments), content_hash(body)
    )


class NarrationSegmentExecutionService:
    """Durably journal each paid narration request before it can be sent."""

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def intend_and_submit(
        self,
        *,
        video_project_id: Any,
        authority: Mapping[str, Any],
        segment: Mapping[str, Any],
        canonical_text: str,
        provider_projection: Mapping[str, Any],
        voice_id: str,
        model_id: str,
        settings: Mapping[str, Any],
        context: Mapping[str, Any],
        estimated_cost_usd: Decimal,
    ) -> NarrationSegmentEffectHandle:
        """Return a newly submitted effect or refuse an uncertain replay.

        Every transition is committed independently.  Therefore a worker crash
        after this call is unambiguously a provider-outcome reconciliation
        problem, never authorization to resend a paid request.
        """

        from app.db.models.voice_authority import NarrationSegmentExecution

        index = int(segment["segment_index"])
        effect_key = str(
            content_hash(
                {
                    "projection_hash": authority["tts_performance_projection_hash"],
                    "segment_id": segment["segment_id"],
                    "segment_index": index,
                    "canonical_text_hash": content_hash({"text": canonical_text}),
                }
            )
        )
        projection_hash = content_hash(dict(provider_projection))
        body = {
            "video_project_id": str(video_project_id),
            "effect_key": effect_key,
            "segment_id": segment["segment_id"],
            "segment_index": index,
            "canonical_text_hash": content_hash({"text": canonical_text}),
            "provider_projection_hash": projection_hash,
            "voice_id": voice_id,
            "model_id": model_id,
            "settings": dict(settings),
            "context": dict(context),
        }
        with self._session_factory() as session:
            existing = session.scalar(
                select(NarrationSegmentExecution).where(
                    NarrationSegmentExecution.video_project_id == video_project_id,
                    NarrationSegmentExecution.provider_effect_key == effect_key,
                )
            )
            if existing is None:
                record = NarrationSegmentExecution(
                    video_project_id=video_project_id,
                    narration_voice_snapshot_id=authority[
                        "narration_voice_snapshot_id"
                    ],
                    narration_voice_snapshot_hash=authority[
                        "narration_voice_snapshot_hash"
                    ],
                    narration_performance_plan_id=authority[
                        "narration_performance_plan_id"
                    ],
                    narration_performance_plan_hash=authority[
                        "narration_performance_plan_hash"
                    ],
                    tts_performance_projection_id=authority[
                        "tts_performance_projection_id"
                    ],
                    tts_performance_projection_hash=authority[
                        "tts_performance_projection_hash"
                    ],
                    segment_id=str(segment["segment_id"]),
                    segment_index=index,
                    canonical_text_hash=body["canonical_text_hash"],
                    provider_projection_hash=projection_hash,
                    provider_effect_key=effect_key,
                    voice_id=voice_id,
                    model_id=model_id,
                    compiled_voice_settings=dict(settings),
                    provider_context=dict(context),
                    state="INTENDED",
                    estimated_cost_usd=str(estimated_cost_usd),
                    attempt_count=0,
                    outcome_certainty="NOT_SENT",
                    content_hash=content_hash(body),
                )
                session.add(record)
                session.commit()
                effect_id = record.id
            else:
                effect_id = existing.id
                if existing.state == "VERIFIED":
                    if (
                        existing.content_hash != content_hash(body)
                        or existing.provider_projection_hash != projection_hash
                        or existing.canonical_text_hash != body["canonical_text_hash"]
                        or existing.voice_id != voice_id
                        or existing.model_id != model_id
                        or not existing.audio_ref
                        or not existing.audio_checksum
                        or not existing.duration_ms
                        or not existing.provider_request_hash
                        or not existing.provider_request_id
                        or not isinstance(existing.timing_seed, dict)
                    ):
                        raise ValidationFailureError(
                            "NARRATION_SEGMENT_RECONCILIATION_EVIDENCE_INVALID"
                        )
                    return NarrationSegmentEffectHandle(
                        id=existing.id,
                        provider_effect_key=existing.provider_effect_key,
                        state=existing.state,
                        provider_request_hash=existing.provider_request_hash,
                        provider_request_id=existing.provider_request_id,
                        audio_ref=existing.audio_ref,
                        audio_checksum=existing.audio_checksum,
                        duration_ms=existing.duration_ms,
                        timing_seed=dict(existing.timing_seed),
                        estimated_cost_usd=existing.estimated_cost_usd,
                        actual_cost_usd=existing.actual_cost_usd,
                    )
                if existing.state in {"SUBMITTED", "PROVIDER_OUTCOME_UNKNOWN"}:
                    raise ValidationFailureError("PROVIDER_OUTCOME_UNKNOWN")
                if (
                    existing.state != "INTENDED"
                    or existing.content_hash != content_hash(body)
                ):
                    raise ValidationFailureError(
                        "NARRATION_SEGMENT_EFFECT_IDENTITY_MISMATCH"
                    )

        with self._session_factory() as session:
            record = session.get(
                NarrationSegmentExecution, effect_id, with_for_update=True
            )
            if (
                record is None
                or record.state != "INTENDED"
                or record.attempt_count != 0
            ):
                raise ValidationFailureError("PROVIDER_OUTCOME_UNKNOWN")
            record.state = "SUBMITTED"
            record.outcome_certainty = "SUBMITTED"
            record.attempt_count = 1
            session.commit()
            return NarrationSegmentEffectHandle(
                id=record.id,
                provider_effect_key=record.provider_effect_key,
                state="SUBMITTED",
            )

    def verify(
        self,
        *,
        effect_id: Any,
        provider_request_hash: str,
        provider_request_id: str | None,
        audio_ref: str,
        audio_checksum: str,
        duration_ms: int,
        timing_seed: Mapping[str, Any],
        actual_cost_usd: Decimal | None = None,
    ) -> None:
        from app.core.time import utc_now
        from app.db.models.voice_authority import NarrationSegmentExecution

        with self._session_factory() as session:
            record = session.get(
                NarrationSegmentExecution, effect_id, with_for_update=True
            )
            if (
                record is None
                or record.state != "SUBMITTED"
                or record.attempt_count != 1
            ):
                raise ValidationFailureError("NARRATION_SEGMENT_VERIFICATION_INVALID")
            record.state = "VERIFIED"
            record.outcome_certainty = "VERIFIED"
            record.provider_request_hash = provider_request_hash
            record.provider_request_id = provider_request_id
            record.audio_ref = audio_ref
            record.audio_checksum = audio_checksum
            record.duration_ms = duration_ms
            record.timing_seed = dict(timing_seed)
            record.actual_cost_usd = (
                str(actual_cost_usd) if actual_cost_usd is not None else None
            )
            record.verified_at = utc_now()
            session.commit()

    def mark_unknown(self, *, effect_id: Any) -> None:
        from app.db.models.voice_authority import NarrationSegmentExecution

        with self._session_factory() as session:
            record = session.get(
                NarrationSegmentExecution, effect_id, with_for_update=True
            )
            if record is not None and record.state == "SUBMITTED":
                record.state = "PROVIDER_OUTCOME_UNKNOWN"
                record.outcome_certainty = "UNKNOWN"
                session.commit()


@dataclass(frozen=True, slots=True)
class NarrationSegmentEffectHandle:
    id: Any
    provider_effect_key: str
    state: str
    provider_request_hash: str | None = None
    provider_request_id: str | None = None
    audio_ref: str | None = None
    audio_checksum: str | None = None
    duration_ms: int | None = None
    timing_seed: Mapping[str, Any] | None = None
    estimated_cost_usd: str | None = None
    actual_cost_usd: str | None = None


class AudioStitchCompiler:
    """Deterministically compose audio via FFmpeg; never concatenate MP3 bytes."""

    def __init__(self, *, ffmpeg_binary: str | None = None) -> None:
        self.ffmpeg_binary = ffmpeg_binary or shutil.which("ffmpeg")

    def stitch(
        self, *, audio_paths: Sequence[Path], destination: Path
    ) -> dict[str, Any]:
        if not self.ffmpeg_binary:
            raise ValidationFailureError("AUDIO_STITCH_FFMPEG_UNAVAILABLE")
        if not audio_paths or len(set(audio_paths)) != len(audio_paths):
            raise ValidationFailureError("AUDIO_STITCH_SEGMENTS_INVALID")
        if destination.exists():
            raise ValidationFailureError("AUDIO_STITCH_DESTINATION_NOT_FRESH")
        for path in audio_paths:
            if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
                raise ValidationFailureError("AUDIO_STITCH_SEGMENT_FILE_INVALID")
        list_path = destination.with_suffix(".concat.txt")
        list_path.write_text(
            "".join(f"file '{path.as_posix()}'\n" for path in audio_paths),
            encoding="utf-8",
        )
        try:
            subprocess.run(
                [
                    self.ffmpeg_binary,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    # Provider outputs may differ in encoder delay or stream
                    # metadata.  Re-encode to a fixed canonical MP3 profile
                    # rather than relying on unsafe byte/stream concatenation.
                    "-c:a",
                    "libmp3lame",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-b:a",
                    "192k",
                    str(destination),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise ValidationFailureError("AUDIO_STITCH_FFMPEG_FAILED") from exc
        finally:
            list_path.unlink(missing_ok=True)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise ValidationFailureError("AUDIO_STITCH_OUTPUT_INVALID")
        return {"audio_checksum": _sha256_file(destination), "audio_path": destination}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
