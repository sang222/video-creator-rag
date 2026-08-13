"""AI-only FFmpeg assembly and render-quality authority.

The provider boundary creates the primary pixels.  This module is deliberately
limited to presenting provider-verified AI images/videos with deterministic
motion, transitions, and the already-authorized narration audio.  It never
creates a diagram, card, text slide, substitute image, subtitle track, or
provider call.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.ai_visual_production import (
    AIVisualRoute,
    FFmpegEffectPlan,
    MotionDiversityReport,
    MotionGateResult,
    ai_visual_stable_hash,
)


AI_VISUAL_RENDERER_VERSION = "vcos.ai-visual-ffmpeg-assembler.v1"
AI_VISUAL_RENDER_PROFILE = "vcos.ai-visual-1920x1080-30fps-h264-aac.v1"
AI_VISUAL_MANIFEST_SCHEMA = "vcos.ai-visual-asset-manifest.v1"
AI_VISUAL_POLICY_VERSION = "vcos.production-visual-policy.ai-only.v1"
AI_VISUAL_ASSEMBLY_PLAN_SCHEMA = "vcos.ai-visual-ffmpeg-assembly-plan.v1"
AI_VISUAL_COMMAND_MANIFEST_SCHEMA = "vcos.ai-visual-ffmpeg-command-manifest.v1"
AI_VISUAL_RENDER_RECEIPT_SCHEMA = "vcos.ai-visual-render-execution-receipt.v1"
AI_VISUAL_RENDER_QC_SCHEMA = "vcos.ai-visual-render-qc.v1"

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_AI_PROVIDER_BY_ROUTE = {
    "AI_IMAGE": "google_gemini_image",
    "AI_VIDEO": "google_veo",
}
_IMAGE_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
_VIDEO_CONTENT_TYPES = frozenset({"video/mp4"})
_XFADES = {
    "fade_soft": "fade",
    "fade_black": "fadeblack",
    "dissolve": "dissolve",
    "slide_left": "slideleft",
    "slide_right": "slideright",
    "cover_left": "coverleft",
    "cover_right": "coverright",
    "reveal_up": "revealup",
    "reveal_down": "revealdown",
}
_FORBIDDEN_FILTERGRAPH_TOKENS = frozenset(
    {
        "drawtext",
        "drawbox",
        "subtitles",
        "ass=",
        "movie=",
        "amovie=",
        "testsrc",
        "color=",
        "frei0r",
    }
)
_REQUIRED_ASSEMBLY_MOTION_GATES = frozenset(
    {
        "MotionCoverageGate",
        "MotionBoundsGate",
        "MotionMeaningAlignmentGate",
        "MotionDiversityGate",
        "TransitionContinuityGate",
        "StaticDurationGate",
        "DeadVisualTimeGate",
    }
)


def _canonical_hash(model: BaseModel, hash_field: str) -> str:
    return ai_visual_stable_hash(model.model_dump(mode="json", exclude={hash_field}))


def _complete_content_body(
    model_type: type[BaseModel], values: Mapping[str, Any]
) -> dict[str, Any]:
    """Materialize defaults and JSON-native values before content sealing."""

    return model_type.model_construct(**dict(values)).model_dump(
        mode="json", exclude={"content_hash"}
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_uuid(namespace: str, digest: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{namespace}:{digest}"))


def _format_seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.6f}".rstrip("0").rstrip(".")


def _fraction(value: Any) -> float | None:
    try:
        result = Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return float(result) if result.denominator else None


def _stream_duration_ms(stream: Mapping[str, Any]) -> int | None:
    value = stream.get("duration")
    if (
        value is None
        and stream.get("duration_ts") is not None
        and stream.get("time_base")
    ):
        try:
            value = float(Fraction(str(stream["time_base"]))) * int(
                stream["duration_ts"]
            )
        except (TypeError, ValueError, ZeroDivisionError):
            value = None
    try:
        return round(float(value) * 1000) if value is not None else None
    except (TypeError, ValueError):
        return None


def _format_duration_ms(data: Mapping[str, Any]) -> int | None:
    try:
        value = data.get("format", {}).get("duration")
        return round(float(value) * 1000) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _assert_hash(value: str, actual: str, reason: str) -> None:
    if value != actual:
        raise ValueError(reason)


class VerifiedAIVisualAsset(BaseModel):
    """One verified provider-created primary asset and its scene bindings."""

    schema_version: Literal["vcos.verified-ai-visual-asset.v1"] = (
        "vcos.verified-ai-visual-asset.v1"
    )
    asset_slot_id: str = Field(min_length=1)
    primary_asset_owner_scene_id: str = Field(min_length=1)
    bound_scene_ids: list[str] = Field(min_length=1)
    bound_scene_plan_hashes: list[str] = Field(min_length=1)
    route: AIVisualRoute
    origin: Literal["AI_GENERATED"] = "AI_GENERATED"
    asset_acquisition_mode: Literal["GENERATED", "ARCHIVED_AI_REUSE"]
    provider_key: Literal["google_gemini_image", "google_veo"]
    model_id: str = Field(min_length=1)
    asset_effect_ref: str = Field(min_length=1)
    asset_effect_identity_hash: str = Field(pattern=_SHA256_PATTERN)
    primary_asset_ref: str = Field(min_length=1)
    primary_asset_hash: str = Field(pattern=_SHA256_PATTERN)
    output_ref: str = Field(min_length=1)
    output_checksum: str = Field(pattern=_SHA256_PATTERN)
    output_size_bytes: int = Field(gt=0)
    output_content_type: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_ms: int | None = Field(default=None, gt=0)
    fps: float | None = Field(default=None, gt=0)
    source_audio_stream_count: Literal[0] = 0
    verification_state: Literal["VERIFIED"] = "VERIFIED"
    qc_ref: str = Field(min_length=1)
    qc_hash: str = Field(pattern=_SHA256_PATTERN)
    asset_receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_asset(self) -> "VerifiedAIVisualAsset":
        if self.provider_key != _AI_PROVIDER_BY_ROUTE[self.route]:
            raise ValueError("AI_VISUAL_ASSET_ROUTE_PROVIDER_MISMATCH")
        if self.primary_asset_hash != self.output_checksum:
            raise ValueError("AI_VISUAL_ASSET_PRIMARY_HASH_MISMATCH")
        if (
            len(self.bound_scene_ids) != len(set(self.bound_scene_ids))
            or len(self.bound_scene_ids) != len(self.bound_scene_plan_hashes)
            or self.primary_asset_owner_scene_id not in self.bound_scene_ids
        ):
            raise ValueError("AI_VISUAL_ASSET_SCENE_BINDING_INVALID")
        if any(
            not re.fullmatch(_SHA256_PATTERN, item)
            for item in self.bound_scene_plan_hashes
        ):
            raise ValueError("AI_VISUAL_ASSET_SCENE_PLAN_HASH_INVALID")
        if self.route == "AI_IMAGE" and (
            self.output_content_type not in _IMAGE_CONTENT_TYPES
            or self.duration_ms is not None
            or self.fps is not None
        ):
            raise ValueError("AI_VISUAL_IMAGE_ASSET_MEDIA_CONTRACT_INVALID")
        if self.route == "AI_VIDEO" and (
            self.output_content_type not in _VIDEO_CONTENT_TYPES
            or self.duration_ms is None
            or self.fps is None
        ):
            raise ValueError("AI_VISUAL_VIDEO_ASSET_MEDIA_CONTRACT_INVALID")
        if self.content_hash != _canonical_hash(self, "content_hash"):
            raise ValueError("AI_VISUAL_VERIFIED_ASSET_HASH_MISMATCH")
        return self

    @classmethod
    def build(cls, **values: Any) -> "VerifiedAIVisualAsset":
        body = {
            "schema_version": "vcos.verified-ai-visual-asset.v1",
            "origin": "AI_GENERATED",
            "source_audio_stream_count": 0,
            "verification_state": "VERIFIED",
            **values,
        }
        body = _complete_content_body(cls, body)
        return cls(**body, content_hash=ai_visual_stable_hash(body))


class AIVisualAssetManifestProjection(BaseModel):
    """Renderer-facing immutable projection of ``AIVisualAssetManifest``."""

    schema_version: Literal["vcos.ai-visual-asset-manifest.v1"] = (
        "vcos.ai-visual-asset-manifest.v1"
    )
    manifest_id: str = Field(min_length=1)
    production_visual_policy_version: Literal[
        "vcos.production-visual-policy.ai-only.v1"
    ] = AI_VISUAL_POLICY_VERSION
    production_visual_policy_ref: str = Field(min_length=1)
    production_visual_policy_hash: str = Field(pattern=_SHA256_PATTERN)
    scene_plan_ref: str = Field(min_length=1)
    scene_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    style_bible_ref: str = Field(min_length=1)
    style_bible_hash: str = Field(pattern=_SHA256_PATTERN)
    motion_grammar_ref: str = Field(min_length=1)
    motion_grammar_hash: str = Field(pattern=_SHA256_PATTERN)
    effect_plan_ref: str = Field(min_length=1)
    effect_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    scene_count: int = Field(gt=0)
    ai_image_scene_count: int = Field(ge=0)
    ai_video_scene_count: int = Field(ge=0)
    asset_count: int = Field(gt=0)
    ai_image_asset_count: int = Field(ge=0)
    ai_video_asset_count: int = Field(ge=0)
    all_primary_visuals_ai_generated: Literal[True] = True
    renderer_primary_visual_generation: Literal[False] = False
    production_eligible: Literal[True] = True
    assets: list[VerifiedAIVisualAsset] = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_manifest(self) -> "AIVisualAssetManifestProjection":
        scene_ids = [scene for asset in self.assets for scene in asset.bound_scene_ids]
        slots = [asset.asset_slot_id for asset in self.assets]
        refs = [asset.primary_asset_ref for asset in self.assets]
        effects = [asset.asset_effect_identity_hash for asset in self.assets]
        if (
            len(slots) != len(set(slots))
            or len(refs) != len(set(refs))
            or len(effects) != len(set(effects))
            or len(scene_ids) != len(set(scene_ids))
        ):
            raise ValueError("AI_VISUAL_ASSET_MANIFEST_IDENTITY_DUPLICATE")
        image_scenes = sum(
            len(asset.bound_scene_ids)
            for asset in self.assets
            if asset.route == "AI_IMAGE"
        )
        video_scenes = sum(
            len(asset.bound_scene_ids)
            for asset in self.assets
            if asset.route == "AI_VIDEO"
        )
        if (
            len(scene_ids) != self.scene_count
            or image_scenes != self.ai_image_scene_count
            or video_scenes != self.ai_video_scene_count
            or image_scenes + video_scenes != self.scene_count
            or len(self.assets) != self.asset_count
            or sum(asset.route == "AI_IMAGE" for asset in self.assets)
            != self.ai_image_asset_count
            or sum(asset.route == "AI_VIDEO" for asset in self.assets)
            != self.ai_video_asset_count
            or self.ai_image_asset_count + self.ai_video_asset_count != self.asset_count
        ):
            raise ValueError("AI_VISUAL_ASSET_MANIFEST_DISTRIBUTION_INVALID")
        if self.content_hash != _canonical_hash(self, "content_hash"):
            raise ValueError("AI_VISUAL_ASSET_MANIFEST_HASH_MISMATCH")
        return self

    @classmethod
    def build(
        cls,
        *,
        assets: Sequence[VerifiedAIVisualAsset | Mapping[str, Any]],
        **values: Any,
    ) -> "AIVisualAssetManifestProjection":
        typed_assets = [
            item
            if isinstance(item, VerifiedAIVisualAsset)
            else VerifiedAIVisualAsset.model_validate(item)
            for item in assets
        ]
        body: dict[str, Any] = {
            "schema_version": AI_VISUAL_MANIFEST_SCHEMA,
            "production_visual_policy_version": AI_VISUAL_POLICY_VERSION,
            "scene_count": sum(len(item.bound_scene_ids) for item in typed_assets),
            "ai_image_scene_count": sum(
                len(item.bound_scene_ids)
                for item in typed_assets
                if item.route == "AI_IMAGE"
            ),
            "ai_video_scene_count": sum(
                len(item.bound_scene_ids)
                for item in typed_assets
                if item.route == "AI_VIDEO"
            ),
            "asset_count": len(typed_assets),
            "ai_image_asset_count": sum(
                item.route == "AI_IMAGE" for item in typed_assets
            ),
            "ai_video_asset_count": sum(
                item.route == "AI_VIDEO" for item in typed_assets
            ),
            "all_primary_visuals_ai_generated": True,
            "renderer_primary_visual_generation": False,
            "production_eligible": True,
            "assets": typed_assets,
            **values,
        }
        body = _complete_content_body(cls, body)
        return cls(**body, content_hash=ai_visual_stable_hash(body))


def build_ai_visual_asset_manifest(
    **values: Any,
) -> AIVisualAssetManifestProjection:
    """Stage helper that derives counts and seals a renderer manifest."""

    return AIVisualAssetManifestProjection.build(**values)


def validate_ai_visual_asset_manifest(
    value: AIVisualAssetManifestProjection | Mapping[str, Any],
) -> AIVisualAssetManifestProjection:
    """Fail-closed stage/renderer helper for persisted manifest content."""

    if isinstance(value, AIVisualAssetManifestProjection):
        return AIVisualAssetManifestProjection.model_validate(
            value.model_dump(mode="json")
        )
    return AIVisualAssetManifestProjection.model_validate(value)


class ResolvedAIVisualInput(BaseModel):
    asset_slot_id: str = Field(min_length=1)
    primary_asset_ref: str = Field(min_length=1)
    primary_asset_hash: str = Field(pattern=_SHA256_PATTERN)
    asset_content_hash: str = Field(pattern=_SHA256_PATTERN)
    route: AIVisualRoute
    resolved_path: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_ms: int | None = Field(default=None, gt=0)
    fps: float | None = Field(default=None, gt=0)
    source_audio_stream_count: Literal[0] = 0

    model_config = ConfigDict(extra="forbid", frozen=True)


class ReusedNarrationInput(BaseModel):
    ref: str = Field(min_length=1)
    checksum_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolved_path: str = Field(min_length=1)
    canonical_duration_ms: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class SRTSidecarInput(BaseModel):
    ref: str = Field(min_length=1)
    checksum_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolved_path: str = Field(min_length=1)
    policy: Literal["SRT_SIDECAR_ONLY"] = "SRT_SIDECAR_ONLY"
    narration_burn_in: Literal[False] = False
    narration_drawtext: Literal[False] = False

    model_config = ConfigDict(extra="forbid", frozen=True)


class AIVisualFFmpegAssemblyPlan(BaseModel):
    """Hash-bound renderer input containing no raw FFmpeg filter syntax."""

    schema_version: Literal["vcos.ai-visual-ffmpeg-assembly-plan.v1"] = (
        AI_VISUAL_ASSEMBLY_PLAN_SCHEMA
    )
    assembly_plan_id: str = Field(min_length=1)
    renderer_version: Literal["vcos.ai-visual-ffmpeg-assembler.v1"] = (
        AI_VISUAL_RENDERER_VERSION
    )
    render_profile: Literal["vcos.ai-visual-1920x1080-30fps-h264-aac.v1"] = (
        AI_VISUAL_RENDER_PROFILE
    )
    workspace_root: str = Field(min_length=1)
    asset_manifest_ref: str = Field(min_length=1)
    asset_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    asset_manifest: AIVisualAssetManifestProjection
    effect_plan_ref: str = Field(min_length=1)
    effect_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    motion_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    effect_plan: FFmpegEffectPlan
    canonical_duration_ms: int = Field(gt=0)
    width: Literal[1920] = 1920
    height: Literal[1080] = 1080
    fps: Literal[30] = 30
    resolved_visual_inputs: list[ResolvedAIVisualInput] = Field(min_length=1)
    narration_audio: ReusedNarrationInput
    srt_sidecar: SRTSidecarInput
    assembly_gate_results: list[MotionGateResult] = Field(min_length=1)
    motion_diversity_report: MotionDiversityReport
    renderer_primary_visual_generation: Literal[False] = False
    renderer_effect_composition: Literal[True] = True
    provider_calls_made: Literal[False] = False
    native_primary_visuals_present: Literal[False] = False
    narration_text_overlays_present: Literal[False] = False
    music_bed_present: Literal[False] = False
    contains_raw_filtergraph: Literal[False] = False
    production_eligible: Literal[True] = True
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_plan(self) -> "AIVisualFFmpegAssemblyPlan":
        if (
            self.asset_manifest_hash != self.asset_manifest.content_hash
            or self.effect_plan_hash != self.effect_plan.effect_plan_hash
            or self.motion_plan_hash != self.effect_plan.motion_plan_hash
            or self.canonical_duration_ms != self.effect_plan.canonical_duration_ms
            or self.asset_manifest.effect_plan_hash != self.effect_plan_hash
            or self.asset_manifest.effect_plan_ref != self.effect_plan_ref
            or self.asset_manifest.motion_grammar_hash
            != self.effect_plan.motion_grammar_hash
        ):
            raise ValueError("AI_VISUAL_ASSEMBLY_AUTHORITY_BINDING_MISMATCH")
        if not all(gate.verdict == "PASS" for gate in self.assembly_gate_results):
            raise ValueError("AI_VISUAL_ASSEMBLY_GATE_BLOCKED")
        gate_names = [gate.gate for gate in self.assembly_gate_results]
        if len(gate_names) != len(
            set(gate_names)
        ) or not _REQUIRED_ASSEMBLY_MOTION_GATES.issubset(gate_names):
            raise ValueError("AI_VISUAL_ASSEMBLY_GATE_AUTHORITY_INCOMPLETE")
        manifest_inputs = {
            asset.asset_slot_id: (
                asset.primary_asset_ref,
                asset.primary_asset_hash,
                asset.content_hash,
                asset.route,
            )
            for asset in self.asset_manifest.assets
        }
        resolved_inputs = {
            item.asset_slot_id: (
                item.primary_asset_ref,
                item.primary_asset_hash,
                item.asset_content_hash,
                item.route,
            )
            for item in self.resolved_visual_inputs
        }
        if (
            len(resolved_inputs) != len(self.resolved_visual_inputs)
            or resolved_inputs != manifest_inputs
            or self.narration_audio.canonical_duration_ms != self.canonical_duration_ms
        ):
            raise ValueError("AI_VISUAL_ASSEMBLY_INPUT_PROJECTION_MISMATCH")
        if self.content_hash != _canonical_hash(self, "content_hash"):
            raise ValueError("AI_VISUAL_ASSEMBLY_PLAN_HASH_MISMATCH")
        return self


class AIVisualFFmpegCommandManifest(BaseModel):
    schema_version: Literal["vcos.ai-visual-ffmpeg-command-manifest.v1"] = (
        AI_VISUAL_COMMAND_MANIFEST_SCHEMA
    )
    assembly_plan_ref: str = Field(min_length=1)
    assembly_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    asset_manifest_ref: str = Field(min_length=1)
    asset_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    effect_plan_ref: str = Field(min_length=1)
    effect_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    motion_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    ffmpeg_binary: str = Field(min_length=1)
    ffmpeg_version: str = Field(min_length=1)
    render_profile: str = Field(min_length=1)
    ordered_scene_mapping: list[dict[str, Any]] = Field(min_length=1)
    input_asset_refs: list[str] = Field(min_length=1)
    filtergraph_artifact: str = Field(min_length=1)
    filtergraph_hash: str = Field(pattern=_SHA256_PATTERN)
    ffmpeg_argv: list[str] = Field(min_length=1)
    command_hash: str = Field(pattern=_SHA256_PATTERN)
    execution_output_ref: str = Field(min_length=1)
    final_output_ref: str = Field(min_length=1)
    renderer_primary_visual_generation: Literal[False] = False
    renderer_effect_composition: Literal[True] = True
    narration_audio_only: Literal[True] = True
    provider_video_audio_mapped: Literal[False] = False
    narration_drawtext: Literal[False] = False
    subtitle_mux: Literal[False] = False
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_command(self) -> "AIVisualFFmpegCommandManifest":
        lowered = self.filtergraph_artifact.casefold()
        if any(token in lowered for token in _FORBIDDEN_FILTERGRAPH_TOKENS):
            raise ValueError("AI_VISUAL_FILTERGRAPH_FORBIDDEN_OPERATION")
        if (
            self.filtergraph_hash
            != hashlib.sha256(self.filtergraph_artifact.encode("utf-8")).hexdigest()
        ):
            raise ValueError("AI_VISUAL_FILTERGRAPH_HASH_MISMATCH")
        expected_command_hash = ai_visual_stable_hash(
            {
                "ffmpeg_argv": self.ffmpeg_argv,
                "filtergraph_hash": self.filtergraph_hash,
            }
        )
        if self.command_hash != expected_command_hash:
            raise ValueError("AI_VISUAL_FFMPEG_COMMAND_HASH_MISMATCH")
        if self.content_hash != _canonical_hash(self, "content_hash"):
            raise ValueError("AI_VISUAL_COMMAND_MANIFEST_HASH_MISMATCH")
        return self


class AIVisualRenderExecutionReceipt(BaseModel):
    schema_version: Literal["vcos.ai-visual-render-execution-receipt.v1"] = (
        AI_VISUAL_RENDER_RECEIPT_SCHEMA
    )
    assembly_plan_ref: str = Field(min_length=1)
    assembly_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    asset_manifest_ref: str = Field(min_length=1)
    asset_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    effect_plan_ref: str = Field(min_length=1)
    effect_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    motion_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    command_manifest: AIVisualFFmpegCommandManifest
    output_ref: str = Field(min_length=1)
    output_checksum: str = Field(pattern=_SHA256_PATTERN)
    output_size_bytes: int = Field(gt=0)
    narration_audio_ref: str = Field(min_length=1)
    narration_audio_checksum: str = Field(pattern=_SHA256_PATTERN)
    srt_sidecar_ref: str = Field(min_length=1)
    srt_sidecar_checksum: str = Field(pattern=_SHA256_PATTERN)
    renderer_primary_visual_generation: Literal[False] = False
    renderer_effect_composition: Literal[True] = True
    completed_at: datetime
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_receipt(self) -> "AIVisualRenderExecutionReceipt":
        command = self.command_manifest
        if (
            self.assembly_plan_hash != command.assembly_plan_hash
            or self.asset_manifest_hash != command.asset_manifest_hash
            or self.effect_plan_hash != command.effect_plan_hash
            or self.motion_plan_hash != command.motion_plan_hash
            or self.output_ref != command.final_output_ref
        ):
            raise ValueError("AI_VISUAL_RENDER_RECEIPT_BINDING_MISMATCH")
        if self.content_hash != _canonical_hash(self, "content_hash"):
            raise ValueError("AI_VISUAL_RENDER_RECEIPT_HASH_MISMATCH")
        return self


class AIVisualRenderQCGate(BaseModel):
    gate: str = Field(min_length=1)
    verdict: Literal["PASS", "BLOCK"]
    reason_codes: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", frozen=True)


class AIVisualRenderQCEvidence(BaseModel):
    schema_version: Literal["vcos.ai-visual-render-qc.v1"] = AI_VISUAL_RENDER_QC_SCHEMA
    assembly_plan_ref: str = Field(min_length=1)
    assembly_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    render_receipt_ref: str = Field(min_length=1)
    render_receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    asset_manifest_ref: str = Field(min_length=1)
    asset_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    effect_plan_ref: str = Field(min_length=1)
    effect_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    output_ref: str = Field(min_length=1)
    output_checksum: str = Field(pattern=_SHA256_PATTERN)
    result: Literal["PASS", "BLOCK"]
    gate_results: list[AIVisualRenderQCGate] = Field(min_length=1)
    checks: dict[str, Any]
    reason_codes: list[str] = Field(default_factory=list)
    human_review_required: Literal[True] = True
    created_at: datetime
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_qc(self) -> "AIVisualRenderQCEvidence":
        blocked = any(gate.verdict == "BLOCK" for gate in self.gate_results)
        if (self.result == "BLOCK") != blocked:
            raise ValueError("AI_VISUAL_RENDER_QC_RESULT_MISMATCH")
        expected_reasons = sorted(
            {
                reason
                for gate in self.gate_results
                if gate.verdict == "BLOCK"
                for reason in gate.reason_codes
            }
        )
        if self.reason_codes != expected_reasons:
            raise ValueError("AI_VISUAL_RENDER_QC_REASON_PROJECTION_MISMATCH")
        if self.content_hash != _canonical_hash(self, "content_hash"):
            raise ValueError("AI_VISUAL_RENDER_QC_HASH_MISMATCH")
        return self


class _MediaProbe:
    def __init__(self, ffprobe: str) -> None:
        self.ffprobe = ffprobe

    def inspect(self, path: Path) -> dict[str, Any]:
        result = subprocess.run(
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError("AI_VISUAL_MEDIA_PROBE_FAILED")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("AI_VISUAL_MEDIA_PROBE_INVALID") from exc
        if not isinstance(value, dict):
            raise ValueError("AI_VISUAL_MEDIA_PROBE_INVALID")
        return value


def _resolve_local_input(root: Path, value: str) -> Path:
    raw = value.strip()
    if raw.startswith("file://"):
        parsed = urlsplit(raw)
        if parsed.netloc not in {"", "localhost"}:
            raise ValueError("AI_VISUAL_INPUT_FILE_URI_HOST_FORBIDDEN")
        raw = unquote(parsed.path)
    elif "://" in raw:
        raise ValueError("AI_VISUAL_INPUT_MUST_BE_LOCAL")
    candidate = Path(raw)
    if ".." in candidate.parts or "~" in candidate.parts:
        raise ValueError("AI_VISUAL_INPUT_PATH_TRAVERSAL")
    if not candidate.is_absolute():
        candidate = root / candidate
    unresolved = candidate.absolute()
    resolved = candidate.resolve(strict=True)
    if resolved != root and root not in resolved.parents:
        raise ValueError("AI_VISUAL_INPUT_OUTSIDE_WORKSPACE")
    try:
        relative_unresolved = unresolved.relative_to(root)
    except ValueError:
        # macOS commonly exposes the same authorized temporary/workspace root
        # through ``/var`` and its resolved ``/private/var`` path.  Containment
        # above is authoritative in that alias case.  A path lexically inside
        # the resolved root, however, may not traverse an internal symlink.
        relative_unresolved = None
    if relative_unresolved is not None:
        cursor = root
        for part in relative_unresolved.parts:
            cursor /= part
            if cursor.is_symlink():
                raise ValueError("AI_VISUAL_INPUT_SYMLINK_FORBIDDEN")
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError("AI_VISUAL_INPUT_FILE_INVALID")
    return resolved


def _resolve_local_output(root: Path, value: str) -> Path:
    if "://" in value or ".." in Path(value).parts or "~" in Path(value).parts:
        raise ValueError("AI_VISUAL_OUTPUT_PATH_INVALID")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    parent = candidate.parent.resolve(strict=True)
    resolved = parent / candidate.name
    if parent != root and root not in parent.parents:
        raise ValueError("AI_VISUAL_OUTPUT_OUTSIDE_WORKSPACE")
    if parent.is_symlink() or candidate.is_symlink():
        raise ValueError("AI_VISUAL_OUTPUT_SYMLINK_FORBIDDEN")
    if resolved.suffix.casefold() != ".mp4":
        raise ValueError("AI_VISUAL_OUTPUT_CONTAINER_INVALID")
    return resolved


class AIVisualFFmpegAssemblyCompiler:
    """Verify manifest bytes and freeze an assembly-only render plan."""

    def __init__(self, *, ffprobe: str | None = None) -> None:
        selected = (
            ffprobe or os.getenv("VCOS_FFPROBE_BINARY") or shutil.which("ffprobe")
        )
        if not selected:
            raise FileNotFoundError("AI_VISUAL_FFPROBE_NOT_FOUND")
        self.ffprobe = str(Path(selected).resolve())
        self._probe = _MediaProbe(self.ffprobe)

    def compile(
        self,
        *,
        manifest: AIVisualAssetManifestProjection | Mapping[str, Any],
        effect_plan: FFmpegEffectPlan | Mapping[str, Any],
        audio_ref: str,
        audio_checksum: str,
        audio_duration_ms: int,
        srt_ref: str,
        srt_checksum: str,
        workspace_root: str | Path,
        asset_manifest_ref: str | None = None,
        effect_plan_ref: str | None = None,
    ) -> AIVisualFFmpegAssemblyPlan:
        typed_manifest = validate_ai_visual_asset_manifest(manifest)
        typed_effect = (
            effect_plan
            if isinstance(effect_plan, FFmpegEffectPlan)
            else FFmpegEffectPlan.model_validate(effect_plan)
        )
        # Reparse typed objects so an unsafe model_construct() cannot cross the boundary.
        typed_effect = FFmpegEffectPlan.model_validate(
            typed_effect.model_dump(mode="json")
        )
        root = Path(workspace_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("AI_VISUAL_WORKSPACE_ROOT_INVALID")
        manifest_ref = asset_manifest_ref or (
            f"artifact://ai-visual-asset-manifest/{typed_manifest.manifest_id}"
        )
        effect_ref = effect_plan_ref or typed_manifest.effect_plan_ref
        self._validate_authority(typed_manifest, typed_effect, effect_ref)
        if audio_duration_ms != typed_effect.canonical_duration_ms:
            raise ValueError("AI_VISUAL_AUDIO_EFFECT_DURATION_MISMATCH")

        resolved_inputs: list[ResolvedAIVisualInput] = []
        for asset in typed_manifest.assets:
            path = _resolve_local_input(root, asset.output_ref)
            _assert_hash(
                asset.output_checksum,
                _sha256_file(path),
                "AI_VISUAL_ASSET_BYTES_HASH_MISMATCH",
            )
            self._validate_asset_media(asset, path)
            resolved_inputs.append(
                ResolvedAIVisualInput(
                    asset_slot_id=asset.asset_slot_id,
                    primary_asset_ref=asset.primary_asset_ref,
                    primary_asset_hash=asset.primary_asset_hash,
                    asset_content_hash=asset.content_hash,
                    route=asset.route,
                    resolved_path=str(path),
                    width=asset.width,
                    height=asset.height,
                    duration_ms=asset.duration_ms,
                    fps=asset.fps,
                    source_audio_stream_count=0,
                )
            )

        audio_path = _resolve_local_input(root, audio_ref)
        _assert_hash(
            audio_checksum,
            _sha256_file(audio_path),
            "AI_VISUAL_AUDIO_BYTES_HASH_MISMATCH",
        )
        self._validate_audio(audio_path, audio_duration_ms)
        srt_path = _resolve_local_input(root, srt_ref)
        _assert_hash(
            srt_checksum,
            _sha256_file(srt_path),
            "AI_VISUAL_SRT_BYTES_HASH_MISMATCH",
        )
        self._validate_srt(srt_path)

        gates = [*typed_effect.gate_results]
        gates.append(
            MotionGateResult(
                gate="TransitionContinuityGate",
                verdict="PASS",
                reason_codes=[],
            )
        )
        body: dict[str, Any] = {
            "schema_version": AI_VISUAL_ASSEMBLY_PLAN_SCHEMA,
            "assembly_plan_id": "pending",
            "renderer_version": AI_VISUAL_RENDERER_VERSION,
            "render_profile": AI_VISUAL_RENDER_PROFILE,
            "workspace_root": str(root),
            "asset_manifest_ref": manifest_ref,
            "asset_manifest_hash": typed_manifest.content_hash,
            "asset_manifest": typed_manifest,
            "effect_plan_ref": effect_ref,
            "effect_plan_hash": typed_effect.effect_plan_hash,
            "motion_plan_hash": typed_effect.motion_plan_hash,
            "effect_plan": typed_effect,
            "canonical_duration_ms": audio_duration_ms,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "resolved_visual_inputs": resolved_inputs,
            "narration_audio": ReusedNarrationInput(
                ref=audio_ref,
                checksum_sha256=audio_checksum,
                resolved_path=str(audio_path),
                canonical_duration_ms=audio_duration_ms,
            ),
            "srt_sidecar": SRTSidecarInput(
                ref=srt_ref,
                checksum_sha256=srt_checksum,
                resolved_path=str(srt_path),
            ),
            "assembly_gate_results": gates,
            "motion_diversity_report": typed_effect.diversity_report,
            "renderer_primary_visual_generation": False,
            "renderer_effect_composition": True,
            "provider_calls_made": False,
            "native_primary_visuals_present": False,
            "narration_text_overlays_present": False,
            "music_bed_present": False,
            "contains_raw_filtergraph": False,
            "production_eligible": True,
        }
        prehash = ai_visual_stable_hash(body)
        body["assembly_plan_id"] = _stable_uuid("ai-visual-assembly-plan", prehash)
        return AIVisualFFmpegAssemblyPlan(
            **body,
            content_hash=ai_visual_stable_hash(body),
        )

    def _validate_authority(
        self,
        manifest: AIVisualAssetManifestProjection,
        effect: FFmpegEffectPlan,
        effect_ref: str,
    ) -> None:
        if (
            not effect.production_eligible
            or manifest.effect_plan_hash != effect.effect_plan_hash
            or manifest.effect_plan_ref != effect_ref
            or manifest.motion_grammar_hash != effect.motion_grammar_hash
        ):
            raise ValueError("AI_VISUAL_MANIFEST_EFFECT_AUTHORITY_MISMATCH")
        scenes = effect.scene_effect_plans
        if len(scenes) != manifest.scene_count or len(
            {scene.scene_id for scene in scenes}
        ) != len(scenes):
            raise ValueError("AI_VISUAL_MANIFEST_EFFECT_SCENE_COUNT_MISMATCH")
        if scenes[0].transition_in != "cut":
            raise ValueError("AI_VISUAL_FIRST_TRANSITION_INVALID")
        for left, right in zip(scenes, scenes[1:]):
            if left.transition_out != right.transition_in:
                raise ValueError("AI_VISUAL_TRANSITION_CONTINUITY_INVALID")
        assets_by_scene: dict[str, VerifiedAIVisualAsset] = {}
        plan_hash_by_scene: dict[str, str] = {}
        for asset in manifest.assets:
            for scene_id, scene_hash in zip(
                asset.bound_scene_ids, asset.bound_scene_plan_hashes
            ):
                assets_by_scene[scene_id] = asset
                plan_hash_by_scene[scene_id] = scene_hash
        for scene in scenes:
            asset = assets_by_scene.get(scene.scene_id)
            if (
                asset is None
                or scene.scene_plan_hash != plan_hash_by_scene.get(scene.scene_id)
                or scene.primary_asset_type != asset.route
                or scene.primary_asset_ref != asset.primary_asset_ref
                or scene.primary_asset_hash != asset.primary_asset_hash
                or scene.contains_primary_visual_generation
            ):
                raise ValueError("AI_VISUAL_SCENE_ASSET_BINDING_MISMATCH")
            duration_ms = scene.presentation_end_ms - scene.presentation_start_ms
            transition_ms = scene.motion_parameters.transition_duration_ms
            if (scene.transition_out == "cut") != (transition_ms == 0):
                raise ValueError("AI_VISUAL_TRANSITION_DURATION_INVALID")
            if scene.transition_out != "cut" and not 150 <= transition_ms <= 600:
                raise ValueError("AI_VISUAL_TRANSITION_DURATION_INVALID")
            if asset.route == "AI_VIDEO" and (
                asset.duration_ms is None or asset.duration_ms + 100 < duration_ms
            ):
                raise ValueError("AI_VISUAL_VIDEO_PRESENTATION_EXCEEDS_ASSET")
        image_scenes = sum(scene.primary_asset_type == "AI_IMAGE" for scene in scenes)
        if (
            image_scenes != manifest.ai_image_scene_count
            or len(scenes) - image_scenes != manifest.ai_video_scene_count
        ):
            raise ValueError("AI_VISUAL_MANIFEST_EFFECT_ROUTE_MISMATCH")

    def _validate_asset_media(self, asset: VerifiedAIVisualAsset, path: Path) -> None:
        if path.stat().st_size != asset.output_size_bytes:
            raise ValueError("AI_VISUAL_ASSET_SIZE_MISMATCH")
        data = self._probe.inspect(path)
        streams = list(data.get("streams") or [])
        videos = [item for item in streams if item.get("codec_type") == "video"]
        audios = [item for item in streams if item.get("codec_type") == "audio"]
        if len(videos) != 1 or audios:
            raise ValueError("AI_VISUAL_ASSET_STREAM_CONTRACT_INVALID")
        video = videos[0]
        if (
            int(video.get("width") or 0) != asset.width
            or int(video.get("height") or 0) != asset.height
        ):
            raise ValueError("AI_VISUAL_ASSET_DIMENSION_MISMATCH")
        if asset.route == "AI_VIDEO":
            duration_ms = _stream_duration_ms(video) or _format_duration_ms(data)
            fps = _fraction(video.get("avg_frame_rate"))
            if (
                duration_ms is None
                or asset.duration_ms is None
                or abs(duration_ms - asset.duration_ms) > 250
                or fps is None
                or asset.fps is None
                or abs(fps - asset.fps) > 0.01
            ):
                raise ValueError("AI_VISUAL_VIDEO_MEDIA_PROJECTION_MISMATCH")

    def _validate_audio(self, path: Path, duration_ms: int) -> None:
        data = self._probe.inspect(path)
        streams = list(data.get("streams") or [])
        audios = [item for item in streams if item.get("codec_type") == "audio"]
        if len(audios) != 1:
            raise ValueError("AI_VISUAL_NARRATION_AUDIO_STREAM_INVALID")
        actual = _stream_duration_ms(audios[0]) or _format_duration_ms(data)
        if actual is None or abs(actual - duration_ms) > 250:
            raise ValueError("AI_VISUAL_NARRATION_DURATION_MISMATCH")

    @staticmethod
    def _validate_srt(path: Path) -> None:
        if path.suffix.casefold() != ".srt":
            raise ValueError("AI_VISUAL_CAPTION_NOT_SRT_SIDECAR")
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("AI_VISUAL_SRT_ENCODING_INVALID") from exc
        if "\x00" in content or "-->" not in content:
            raise ValueError("AI_VISUAL_SRT_CONTENT_INVALID")


class AIVisualFFmpegAssemblyRenderer:
    """Compile exact FFmpeg argv and execute it without a shell."""

    def __init__(self, *, ffmpeg: str | None = None) -> None:
        selected = ffmpeg or os.getenv("VCOS_FFMPEG_BINARY") or shutil.which("ffmpeg")
        if not selected:
            raise FileNotFoundError("AI_VISUAL_FFMPEG_NOT_FOUND")
        self.ffmpeg = str(Path(selected).resolve())

    def compile_command(
        self,
        plan: AIVisualFFmpegAssemblyPlan | Mapping[str, Any],
        *,
        output_ref: str,
    ) -> AIVisualFFmpegCommandManifest:
        return self._compile_command(
            plan,
            output_ref=output_ref,
            require_clean_output=True,
        )

    def _compile_command(
        self,
        plan: AIVisualFFmpegAssemblyPlan | Mapping[str, Any],
        *,
        output_ref: str,
        require_clean_output: bool,
    ) -> AIVisualFFmpegCommandManifest:
        typed = self._validate_plan(plan)
        self._verify_plan_bytes(typed)
        root = Path(typed.workspace_root).resolve(strict=True)
        output = _resolve_local_output(root, output_ref)
        if Path(typed.srt_sidecar.resolved_path).resolve() == output.resolve():
            raise ValueError("AI_VISUAL_OUTPUT_OVERLAPS_SRT_SIDECAR")
        partial = output.with_name(
            f".{output.stem}.{typed.content_hash[:16]}.partial.mp4"
        )
        if require_clean_output:
            if output.exists():
                raise FileExistsError("AI_VISUAL_RENDER_OUTPUT_ALREADY_EXISTS")
            if partial.exists():
                raise FileExistsError("AI_VISUAL_RENDER_PARTIAL_ALREADY_EXISTS")
        inputs_by_ref = {
            (item.primary_asset_ref, item.primary_asset_hash, item.route): item
            for item in typed.resolved_visual_inputs
        }
        argv: list[str] = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-n",
        ]
        scene_mapping: list[dict[str, Any]] = []
        effects = typed.effect_plan.scene_effect_plans
        for index, effect in enumerate(effects):
            item = inputs_by_ref.get(
                (
                    effect.primary_asset_ref,
                    effect.primary_asset_hash,
                    effect.primary_asset_type,
                )
            )
            if item is None:
                raise ValueError("AI_VISUAL_COMMAND_SCENE_INPUT_MISSING")
            presentation_ms = effect.presentation_end_ms - effect.presentation_start_ms
            transition_tail_ms = (
                effect.motion_parameters.transition_duration_ms
                if index + 1 < len(effects)
                else 0
            )
            clip_ms = presentation_ms + transition_tail_ms
            if item.route == "AI_IMAGE":
                argv.extend(
                    [
                        "-loop",
                        "1",
                        "-framerate",
                        str(typed.fps),
                        "-t",
                        _format_seconds(clip_ms),
                        "-i",
                        item.resolved_path,
                    ]
                )
            else:
                argv.extend(["-i", item.resolved_path])
            scene_mapping.append(
                {
                    "input_index": index,
                    "scene_id": effect.scene_id,
                    "primary_asset_ref": effect.primary_asset_ref,
                    "primary_asset_hash": effect.primary_asset_hash,
                    "primary_asset_type": effect.primary_asset_type,
                    "motion_projection_ref": effect.motion_projection_ref,
                    "motion_projection_hash": effect.motion_projection_hash,
                    "motion_preset": effect.motion_preset,
                    "motion_parameters_hash": effect.motion_parameters.content_hash,
                    "transition_in": effect.transition_in,
                    "transition_out": effect.transition_out,
                    "presentation_start_ms": effect.presentation_start_ms,
                    "presentation_end_ms": effect.presentation_end_ms,
                    "source_duration_ms": item.duration_ms,
                    "presentation_duration_ms": presentation_ms,
                    "video_presentation_policy": (
                        {
                            "strategy": "TRIM_HEAD_AND_HOLD_TRANSITION_TAIL_NO_RETIME",
                            "trim_start_ms": 0,
                            "trim_duration_ms": presentation_ms,
                            "transition_tail_hold_ms": transition_tail_ms,
                            "loop": False,
                            "retime": False,
                        }
                        if item.route == "AI_VIDEO"
                        else None
                    ),
                }
            )
        audio_index = len(effects)
        argv.extend(["-i", typed.narration_audio.resolved_path])
        filtergraph = self._build_filtergraph(typed)
        argv.extend(
            [
                "-filter_complex",
                filtergraph,
                "-map",
                "[vout]",
                "-map",
                f"{audio_index}:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(typed.fps),
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-t",
                _format_seconds(typed.canonical_duration_ms),
                "-movflags",
                "+faststart",
                "-map_metadata",
                "-1",
                str(partial),
            ]
        )
        version = subprocess.run(
            [self.ffmpeg, "-version"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[0]
        filtergraph_hash = hashlib.sha256(filtergraph.encode("utf-8")).hexdigest()
        command_hash = ai_visual_stable_hash(
            {"ffmpeg_argv": argv, "filtergraph_hash": filtergraph_hash}
        )
        body: dict[str, Any] = {
            "schema_version": AI_VISUAL_COMMAND_MANIFEST_SCHEMA,
            "assembly_plan_ref": (
                f"artifact://ai-visual-assembly-plan/{typed.assembly_plan_id}"
            ),
            "assembly_plan_hash": typed.content_hash,
            "asset_manifest_ref": typed.asset_manifest_ref,
            "asset_manifest_hash": typed.asset_manifest_hash,
            "effect_plan_ref": typed.effect_plan_ref,
            "effect_plan_hash": typed.effect_plan_hash,
            "motion_plan_hash": typed.motion_plan_hash,
            "ffmpeg_binary": self.ffmpeg,
            "ffmpeg_version": version,
            "render_profile": typed.render_profile,
            "ordered_scene_mapping": scene_mapping,
            "input_asset_refs": [
                item.primary_asset_ref for item in typed.resolved_visual_inputs
            ],
            "filtergraph_artifact": filtergraph,
            "filtergraph_hash": filtergraph_hash,
            "ffmpeg_argv": argv,
            "command_hash": command_hash,
            "execution_output_ref": str(partial),
            "final_output_ref": str(output),
            "renderer_primary_visual_generation": False,
            "renderer_effect_composition": True,
            "narration_audio_only": True,
            "provider_video_audio_mapped": False,
            "narration_drawtext": False,
            "subtitle_mux": False,
        }
        return AIVisualFFmpegCommandManifest(
            **body, content_hash=ai_visual_stable_hash(body)
        )

    def execute(
        self,
        plan: AIVisualFFmpegAssemblyPlan | Mapping[str, Any],
        *,
        output_ref: str,
        seal_completion: (
            Callable[[AIVisualRenderExecutionReceipt], None] | None
        ) = None,
        after_output_commit: Callable[[Path], None] | None = None,
    ) -> AIVisualRenderExecutionReceipt:
        typed = self._validate_plan(plan)
        command = self.compile_command(typed, output_ref=output_ref)
        partial = Path(command.execution_output_ref)
        output = Path(command.final_output_ref)
        try:
            completed = subprocess.run(
                command.ffmpeg_argv,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise RuntimeError("AI_VISUAL_FFMPEG_EXECUTION_FAILED")
            if (
                not partial.is_file()
                or partial.is_symlink()
                or partial.stat().st_size <= 0
            ):
                raise RuntimeError("AI_VISUAL_FFMPEG_OUTPUT_INVALID")
            descriptor = os.open(partial, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            completed_at = datetime.now(UTC)
            body: dict[str, Any] = {
                "schema_version": AI_VISUAL_RENDER_RECEIPT_SCHEMA,
                "assembly_plan_ref": command.assembly_plan_ref,
                "assembly_plan_hash": typed.content_hash,
                "asset_manifest_ref": typed.asset_manifest_ref,
                "asset_manifest_hash": typed.asset_manifest_hash,
                "effect_plan_ref": typed.effect_plan_ref,
                "effect_plan_hash": typed.effect_plan_hash,
                "motion_plan_hash": typed.motion_plan_hash,
                "command_manifest": command,
                "output_ref": str(output),
                "output_checksum": _sha256_file(partial),
                "output_size_bytes": partial.stat().st_size,
                "narration_audio_ref": typed.narration_audio.ref,
                "narration_audio_checksum": typed.narration_audio.checksum_sha256,
                "srt_sidecar_ref": typed.srt_sidecar.ref,
                "srt_sidecar_checksum": typed.srt_sidecar.checksum_sha256,
                "renderer_primary_visual_generation": False,
                "renderer_effect_composition": True,
                "completed_at": completed_at,
            }
            body = _complete_content_body(AIVisualRenderExecutionReceipt, body)
            receipt = AIVisualRenderExecutionReceipt(
                **body, content_hash=ai_visual_stable_hash(body)
            )
            # Persist the exact completion identity before exposing the final
            # path. A replay can then adopt only these exact bytes.
            if seal_completion is not None:
                seal_completion(receipt)
            os.replace(partial, output)
            directory = os.open(
                output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            if after_output_commit is not None:
                after_output_commit(output)
        except Exception:
            if partial.is_file() and not partial.is_symlink():
                partial.unlink()
            raise
        self._require_exact_completed_output(output, receipt)
        return receipt

    def reconcile_completion(
        self,
        plan: AIVisualFFmpegAssemblyPlan | Mapping[str, Any],
        *,
        output_ref: str,
        completion_receipt: AIVisualRenderExecutionReceipt | Mapping[str, Any],
    ) -> AIVisualRenderExecutionReceipt:
        """Finish a pre-sealed render without executing FFmpeg a second time."""

        typed = self._validate_plan(plan)
        receipt = (
            completion_receipt
            if isinstance(completion_receipt, AIVisualRenderExecutionReceipt)
            else AIVisualRenderExecutionReceipt.model_validate(completion_receipt)
        )
        receipt = AIVisualRenderExecutionReceipt.model_validate(
            receipt.model_dump(mode="json")
        )
        expected_command = self._compile_command(
            typed,
            output_ref=output_ref,
            require_clean_output=False,
        )
        if (
            receipt.assembly_plan_hash != typed.content_hash
            or receipt.asset_manifest_hash != typed.asset_manifest_hash
            or receipt.effect_plan_hash != typed.effect_plan_hash
            or receipt.motion_plan_hash != typed.motion_plan_hash
            or receipt.command_manifest.model_dump(mode="json")
            != expected_command.model_dump(mode="json")
            or receipt.output_ref != expected_command.final_output_ref
        ):
            raise ValueError("AI_VISUAL_RENDER_COMPLETION_SEAL_IDENTITY_MISMATCH")

        output = Path(expected_command.final_output_ref)
        partial = Path(expected_command.execution_output_ref)
        if output.exists():
            if partial.exists():
                raise ValueError("AI_VISUAL_RENDER_COMPLETION_PARTIAL_CONFLICT")
            self._require_exact_completed_output(output, receipt)
            return receipt
        if (
            not partial.is_file()
            or partial.is_symlink()
            or partial.stat().st_size != receipt.output_size_bytes
            or _sha256_file(partial) != receipt.output_checksum
        ):
            raise ValueError("AI_VISUAL_RENDER_COMPLETION_BYTES_MISMATCH")
        os.replace(partial, output)
        directory = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self._require_exact_completed_output(output, receipt)
        return receipt

    @staticmethod
    def _require_exact_completed_output(
        output: Path,
        receipt: AIVisualRenderExecutionReceipt,
    ) -> None:
        if (
            not output.is_file()
            or output.is_symlink()
            or output.stat().st_size != receipt.output_size_bytes
            or _sha256_file(output) != receipt.output_checksum
        ):
            raise ValueError("AI_VISUAL_RENDER_COMPLETION_BYTES_MISMATCH")

    @staticmethod
    def _validate_plan(
        plan: AIVisualFFmpegAssemblyPlan | Mapping[str, Any],
    ) -> AIVisualFFmpegAssemblyPlan:
        typed = (
            plan
            if isinstance(plan, AIVisualFFmpegAssemblyPlan)
            else AIVisualFFmpegAssemblyPlan.model_validate(plan)
        )
        return AIVisualFFmpegAssemblyPlan.model_validate(typed.model_dump(mode="json"))

    @staticmethod
    def _verify_plan_bytes(plan: AIVisualFFmpegAssemblyPlan) -> None:
        root = Path(plan.workspace_root).resolve(strict=True)
        for item in plan.resolved_visual_inputs:
            path = _resolve_local_input(root, item.resolved_path)
            if (
                str(path) != item.resolved_path
                or _sha256_file(path) != item.primary_asset_hash
            ):
                raise ValueError("AI_VISUAL_RENDER_INPUT_BYTES_HASH_MISMATCH")
        audio = _resolve_local_input(root, plan.narration_audio.resolved_path)
        if (
            str(audio) != plan.narration_audio.resolved_path
            or _sha256_file(audio) != plan.narration_audio.checksum_sha256
        ):
            raise ValueError("AI_VISUAL_RENDER_AUDIO_BYTES_HASH_MISMATCH")
        srt = _resolve_local_input(root, plan.srt_sidecar.resolved_path)
        if (
            str(srt) != plan.srt_sidecar.resolved_path
            or _sha256_file(srt) != plan.srt_sidecar.checksum_sha256
        ):
            raise ValueError("AI_VISUAL_RENDER_SRT_BYTES_HASH_MISMATCH")

    def _build_filtergraph(self, plan: AIVisualFFmpegAssemblyPlan) -> str:
        scene_filters: list[str] = []
        effects = plan.effect_plan.scene_effect_plans
        for index, effect in enumerate(effects):
            base_ms = effect.presentation_end_ms - effect.presentation_start_ms
            transition_ms = (
                effect.motion_parameters.transition_duration_ms
                if index + 1 < len(effects)
                else 0
            )
            clip_ms = base_ms + transition_ms
            ending_ms = (
                effect.motion_parameters.transition_duration_ms
                if index + 1 == len(effects) and effect.transition_out != "cut"
                else 0
            )
            ending_filter = (
                f",fade=t=out:st={_format_seconds(max(0, base_ms - ending_ms))}:"
                f"d={_format_seconds(ending_ms)}"
                if ending_ms
                else ""
            )
            if effect.primary_asset_type == "AI_IMAGE":
                params = effect.motion_parameters
                frames = max(2, math.ceil(clip_ms * plan.fps / 1000))
                progress = f"(0.5-0.5*cos(PI*on/{frames - 1}))"
                zoom = (
                    f"{params.start_scale:.6f}+"
                    f"({params.end_scale - params.start_scale:.6f})*{progress}"
                )
                crop_x = (
                    f"(iw-iw/zoom)*({params.crop_x_start:.6f}+"
                    f"({params.crop_x_end - params.crop_x_start:.6f})*{progress})"
                )
                crop_y = (
                    f"(ih-ih/zoom)*({params.crop_y_start:.6f}+"
                    f"({params.crop_y_end - params.crop_y_start:.6f})*{progress})"
                )
                chain = (
                    f"[{index}:v:0]"
                    f"scale={plan.width}:{plan.height}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={plan.width}:{plan.height},setsar=1,"
                    f"zoompan=z='{zoom}':x='{crop_x}':y='{crop_y}':d=1:"
                    f"s={plan.width}x{plan.height}:fps={plan.fps},"
                    f"trim=duration={_format_seconds(clip_ms)},setpts=PTS-STARTPTS,"
                    f"fps={plan.fps},format=yuv420p,settb=AVTB"
                    f"{ending_filter}[v{index}]"
                )
            else:
                chain = (
                    f"[{index}:v:0]trim=duration={_format_seconds(base_ms)},"
                    f"setpts=PTS-STARTPTS,fps={plan.fps},"
                    f"scale={plan.width}:{plan.height}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={plan.width}:{plan.height},setsar=1,"
                    f"tpad=stop_mode=clone:stop_duration={_format_seconds(transition_ms)},"
                    f"trim=duration={_format_seconds(clip_ms)},format=yuv420p,settb=AVTB"
                    f"{ending_filter}[v{index}]"
                )
            scene_filters.append(chain)
        current = "v0"
        for index, previous in enumerate(effects[:-1], start=1):
            output = "vout" if index == len(effects) - 1 else f"vc{index}"
            duration_ms = previous.motion_parameters.transition_duration_ms
            if previous.transition_out == "cut":
                scene_filters.append(
                    f"[{current}][v{index}]concat=n=2:v=1:a=0[{output}]"
                )
            else:
                transition = _XFADES.get(previous.transition_out)
                if transition is None:
                    raise ValueError("AI_VISUAL_TRANSITION_PRESET_UNSUPPORTED")
                offset_ms = previous.presentation_end_ms
                scene_filters.append(
                    f"[{current}][v{index}]xfade=transition={transition}:"
                    f"duration={_format_seconds(duration_ms)}:"
                    f"offset={_format_seconds(offset_ms)}[{output}]"
                )
            current = output
        if len(effects) == 1:
            scene_filters.append("[v0]null[vout]")
        return ";".join(scene_filters)


class AIVisualRenderQC:
    """Technical, provenance, motion, and sidecar QC for AI-only output."""

    def __init__(
        self, *, ffprobe: str | None = None, ffmpeg: str | None = None
    ) -> None:
        probe = ffprobe or os.getenv("VCOS_FFPROBE_BINARY") or shutil.which("ffprobe")
        renderer = ffmpeg or os.getenv("VCOS_FFMPEG_BINARY") or shutil.which("ffmpeg")
        if not probe or not renderer:
            raise FileNotFoundError("AI_VISUAL_QC_FFMPEG_RUNTIME_NOT_FOUND")
        self.ffprobe = str(Path(probe).resolve())
        self.ffmpeg = str(Path(renderer).resolve())
        self._probe = _MediaProbe(self.ffprobe)

    def inspect(
        self,
        *,
        plan: AIVisualFFmpegAssemblyPlan | Mapping[str, Any],
        receipt: AIVisualRenderExecutionReceipt | Mapping[str, Any],
    ) -> AIVisualRenderQCEvidence:
        typed_plan = AIVisualFFmpegAssemblyRenderer._validate_plan(plan)
        typed_receipt = (
            receipt
            if isinstance(receipt, AIVisualRenderExecutionReceipt)
            else AIVisualRenderExecutionReceipt.model_validate(receipt)
        )
        typed_receipt = AIVisualRenderExecutionReceipt.model_validate(
            typed_receipt.model_dump(mode="json")
        )
        gates: list[AIVisualRenderQCGate] = []
        checks: dict[str, Any] = {}

        authority_ok = (
            typed_receipt.assembly_plan_hash == typed_plan.content_hash
            and typed_receipt.asset_manifest_hash == typed_plan.asset_manifest_hash
            and typed_receipt.effect_plan_hash == typed_plan.effect_plan_hash
            and typed_receipt.motion_plan_hash == typed_plan.motion_plan_hash
        )
        gates.append(
            self._gate("RenderAuthorityGate", authority_ok, "RENDER_AUTHORITY_MISMATCH")
        )
        ai_only = (
            typed_plan.asset_manifest.all_primary_visuals_ai_generated
            and all(
                asset.origin == "AI_GENERATED"
                and asset.verification_state == "VERIFIED"
                and asset.provider_key == _AI_PROVIDER_BY_ROUTE[asset.route]
                for asset in typed_plan.asset_manifest.assets
            )
            and not typed_plan.renderer_primary_visual_generation
            and not typed_receipt.renderer_primary_visual_generation
        )
        gates.append(
            self._gate(
                "AIOnlyPrimaryVisualOriginGate",
                ai_only,
                "NON_AI_PRIMARY_VISUAL_PRESENT",
            )
        )
        motion_gate_names = {
            result.gate: result for result in typed_plan.assembly_gate_results
        }
        for name in (
            "MotionCoverageGate",
            "MotionBoundsGate",
            "MotionMeaningAlignmentGate",
            "MotionDiversityGate",
            "TransitionContinuityGate",
            "StaticDurationGate",
            "DeadVisualTimeGate",
        ):
            source = motion_gate_names.get(name)
            passed = source is not None and source.verdict == "PASS"
            reasons = (
                list(source.reason_codes)
                if source is not None and source.reason_codes
                else ([] if passed else [f"{name.upper()}_BLOCKED"])
            )
            gates.append(
                AIVisualRenderQCGate(
                    gate=name,
                    verdict="PASS" if passed else "BLOCK",
                    reason_codes=reasons,
                    evidence={"effect_plan_hash": typed_plan.effect_plan_hash},
                )
            )
        command = typed_receipt.command_manifest
        command_bound = (
            command.assembly_plan_hash == typed_plan.content_hash
            and command.asset_manifest_ref == typed_plan.asset_manifest_ref
            and command.asset_manifest_hash == typed_plan.asset_manifest_hash
            and command.effect_plan_ref == typed_plan.effect_plan_ref
            and command.effect_plan_hash == typed_plan.effect_plan_hash
            and command.motion_plan_hash == typed_plan.motion_plan_hash
            and command.render_profile == typed_plan.render_profile
        )
        gates.append(
            self._gate(
                "FFmpegCommandAuthorityGate",
                command_bound,
                "FFMPEG_COMMAND_AUTHORITY_MISMATCH",
            )
        )
        filtergraph_clean = not any(
            token in command.filtergraph_artifact.casefold()
            for token in _FORBIDDEN_FILTERGRAPH_TOKENS
        )
        assembler_only = (
            filtergraph_clean
            and command.renderer_effect_composition
            and not command.renderer_primary_visual_generation
            and not command.narration_drawtext
            and not command.subtitle_mux
        )
        gates.append(
            self._gate(
                "AssemblerOnlyGate",
                assembler_only,
                "RENDERER_PRIMARY_VISUAL_GENERATION_DETECTED",
            )
        )
        provider_audio_ok = (
            not command.provider_video_audio_mapped
            and command.narration_audio_only
            and all(
                item.source_audio_stream_count == 0
                for item in typed_plan.resolved_visual_inputs
            )
        )
        gates.append(
            self._gate(
                "ProviderAudioExclusionGate",
                provider_audio_ok,
                "PROVIDER_VIDEO_AUDIO_PRESENT",
            )
        )

        output = _resolve_local_input(
            Path(typed_plan.workspace_root), typed_receipt.output_ref
        )
        output_hash_ok = _sha256_file(output) == typed_receipt.output_checksum
        gates.append(
            self._gate(
                "RenderedBytesIntegrityGate",
                output_hash_ok,
                "RENDER_OUTPUT_HASH_MISMATCH",
            )
        )
        data = self._probe.inspect(output)
        streams = list(data.get("streams") or [])
        videos = [item for item in streams if item.get("codec_type") == "video"]
        audios = [item for item in streams if item.get("codec_type") == "audio"]
        subtitles = [item for item in streams if item.get("codec_type") == "subtitle"]
        video = videos[0] if len(videos) == 1 else {}
        audio = audios[0] if len(audios) == 1 else {}
        duration_ms = _format_duration_ms(data)
        fps = _fraction(video.get("avg_frame_rate"))
        decode = subprocess.run(
            [
                self.ffmpeg,
                "-v",
                "error",
                "-xerror",
                "-i",
                str(output),
                "-map",
                "0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
        )
        technical = (
            len(videos) == 1
            and len(audios) == 1
            and not subtitles
            and video.get("codec_name") == "h264"
            and int(video.get("width") or 0) == typed_plan.width
            and int(video.get("height") or 0) == typed_plan.height
            and fps is not None
            and abs(fps - typed_plan.fps) < 0.001
            and audio.get("codec_name") == "aac"
            and int(audio.get("sample_rate") or 0) == 48000
            and int(audio.get("channels") or 0) == 2
            and duration_ms is not None
            and abs(duration_ms - typed_plan.canonical_duration_ms) <= 250
            and decode.returncode == 0
        )
        checks.update(
            {
                "video_stream_count": len(videos),
                "audio_stream_count": len(audios),
                "subtitle_stream_count": len(subtitles),
                "video_codec": video.get("codec_name"),
                "width": video.get("width"),
                "height": video.get("height"),
                "fps": fps,
                "audio_codec": audio.get("codec_name"),
                "sample_rate": int(audio.get("sample_rate") or 0),
                "channels": audio.get("channels"),
                "duration_ms": duration_ms,
                "canonical_duration_ms": typed_plan.canonical_duration_ms,
                "full_decode": decode.returncode == 0,
            }
        )
        gates.append(
            self._gate(
                "TechnicalMediaGate", technical, "AI_VISUAL_TECHNICAL_MEDIA_QC_FAILED"
            )
        )
        caption_ok = (
            not subtitles
            and typed_plan.srt_sidecar.policy == "SRT_SIDECAR_ONLY"
            and Path(typed_plan.srt_sidecar.resolved_path).is_file()
            and _sha256_file(Path(typed_plan.srt_sidecar.resolved_path))
            == typed_plan.srt_sidecar.checksum_sha256
            and typed_plan.srt_sidecar.resolved_path not in command.ffmpeg_argv
        )
        gates.append(
            self._gate("SRTSidecarOnlyGate", caption_ok, "SRT_SIDECAR_POLICY_VIOLATION")
        )

        observed, observations = self._motion_observations(typed_plan, output)
        checks["motion_observations"] = observations
        gates.append(
            self._gate(
                "RenderedMotionObservationGate",
                observed,
                "RENDERED_MOTION_NOT_OBSERVED",
            )
        )
        checks.update(
            {
                "all_primary_visuals_ai_generated": ai_only,
                "renderer_primary_visual_generation": False,
                "renderer_effect_composition": True,
                "asset_manifest_hash": typed_plan.asset_manifest_hash,
                "effect_plan_hash": typed_plan.effect_plan_hash,
                "motion_plan_hash": typed_plan.motion_plan_hash,
                "filtergraph_hash": command.filtergraph_hash,
                "srt_sidecar_only": caption_ok,
                "provider_video_audio_mapped": False,
                "motion_preset_counts": typed_plan.motion_diversity_report.motion_preset_counts,
                "transition_counts": typed_plan.motion_diversity_report.transition_counts,
                "maximum_consecutive_same_motion_preset": typed_plan.motion_diversity_report.maximum_consecutive_same_motion_preset,
                "maximum_consecutive_same_transition": typed_plan.motion_diversity_report.maximum_consecutive_same_transition,
            }
        )
        reasons = sorted(
            {
                reason
                for gate in gates
                if gate.verdict == "BLOCK"
                for reason in gate.reason_codes
            }
        )
        # QC evidence must replay byte-for-byte after a crash between artifact
        # writes. The render receipt is already durably sealed, so its
        # completion time is the stable event timestamp for this evidence.
        created_at = typed_receipt.completed_at
        body: dict[str, Any] = {
            "schema_version": AI_VISUAL_RENDER_QC_SCHEMA,
            "assembly_plan_ref": command.assembly_plan_ref,
            "assembly_plan_hash": typed_plan.content_hash,
            "render_receipt_ref": (
                f"artifact://ai-visual-render-receipt/{typed_receipt.content_hash}"
            ),
            "render_receipt_hash": typed_receipt.content_hash,
            "asset_manifest_ref": typed_plan.asset_manifest_ref,
            "asset_manifest_hash": typed_plan.asset_manifest_hash,
            "effect_plan_ref": typed_plan.effect_plan_ref,
            "effect_plan_hash": typed_plan.effect_plan_hash,
            "output_ref": typed_receipt.output_ref,
            "output_checksum": typed_receipt.output_checksum,
            "result": "BLOCK" if reasons else "PASS",
            "gate_results": gates,
            "checks": checks,
            "reason_codes": reasons,
            "human_review_required": True,
            "created_at": created_at,
        }
        body = _complete_content_body(AIVisualRenderQCEvidence, body)
        return AIVisualRenderQCEvidence(
            **body, content_hash=ai_visual_stable_hash(body)
        )

    @staticmethod
    def _gate(name: str, passed: bool, reason: str) -> AIVisualRenderQCGate:
        return AIVisualRenderQCGate(
            gate=name,
            verdict="PASS" if passed else "BLOCK",
            reason_codes=[] if passed else [reason],
        )

    def _motion_observations(
        self, plan: AIVisualFFmpegAssemblyPlan, output: Path
    ) -> tuple[bool, list[dict[str, Any]]]:
        observations: list[dict[str, Any]] = []
        all_observed = True
        for effect in plan.effect_plan.scene_effect_plans:
            if effect.motion_preset == "hold_intentional":
                observations.append(
                    {
                        "scene_id": effect.scene_id,
                        "motion_preset": effect.motion_preset,
                        "strategy": "INTENTIONAL_HOLD",
                        "observed": True,
                    }
                )
                continue
            duration_ms = effect.presentation_end_ms - effect.presentation_start_ms
            if duration_ms < 500:
                observations.append(
                    {
                        "scene_id": effect.scene_id,
                        "motion_preset": effect.motion_preset,
                        "strategy": "HASH_BOUND_SHORT_PRESENTATION_STRATEGY",
                        "duration_ms": duration_ms,
                        "observed": True,
                    }
                )
                continue
            first_ms = effect.presentation_start_ms + max(100, duration_ms // 4)
            second_ms = effect.presentation_start_ms + max(200, duration_ms * 3 // 4)
            second_ms = min(effect.presentation_end_ms - 50, second_ms)
            first = self._frame(output, first_ms)
            second = self._frame(output, second_ms)
            changed = bool(first and second and first != second)
            all_observed = all_observed and changed
            observations.append(
                {
                    "scene_id": effect.scene_id,
                    "motion_preset": effect.motion_preset,
                    "strategy": "INTRINSIC_MOTION"
                    if effect.primary_asset_type == "AI_VIDEO"
                    else "DETERMINISTIC_PRESENTATION_MOTION",
                    "sample_times_ms": [first_ms, second_ms],
                    "observed": changed,
                }
            )
        return all_observed, observations

    def _frame(self, output: Path, milliseconds: int) -> str | None:
        result = subprocess.run(
            [
                self.ffmpeg,
                "-v",
                "error",
                "-ss",
                _format_seconds(milliseconds),
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-vf",
                "scale=160:90,format=gray",
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
        )
        if result.returncode != 0 or not result.stdout:
            return None
        return hashlib.sha256(result.stdout).hexdigest()


__all__ = [
    "AIVisualAssetManifestProjection",
    "AIVisualFFmpegAssemblyCompiler",
    "AIVisualFFmpegAssemblyPlan",
    "AIVisualFFmpegAssemblyRenderer",
    "AIVisualFFmpegCommandManifest",
    "AIVisualRenderExecutionReceipt",
    "AIVisualRenderQC",
    "AIVisualRenderQCEvidence",
    "VerifiedAIVisualAsset",
    "build_ai_visual_asset_manifest",
    "validate_ai_visual_asset_manifest",
]
