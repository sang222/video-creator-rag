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
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

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
            Decimal("0"),
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
            "shortfall_usd": str(max(Decimal("0"), total - self.approved_ceiling_usd)),
        }


@dataclass(frozen=True, slots=True)
class NarrationSeamQCReport:
    state: str
    reason_codes: tuple[str, ...]
    segment_count: int
    content_hash: str


def seam_qc(*, segments: Sequence[Mapping[str, Any]]) -> NarrationSeamQCReport:
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
    state = "FAIL" if reasons else "PASS"
    body = {
        "state": state,
        "reason_codes": sorted(set(reasons)),
        "segment_count": len(segments),
    }
    return NarrationSeamQCReport(
        state, tuple(body["reason_codes"]), len(segments), content_hash(body)
    )


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
                    "-c",
                    "copy",
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
