from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.contracts.native_renderer import GateResult, NativeRenderPlan
from app.contracts.temporal_authority import CanonicalMediaTimeline
from app.services.caption_ass import caption_render_payload


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def canonical_plan_hash(plan: NativeRenderPlan) -> str:
    data = plan.model_dump(mode="json", exclude={"content_hash", "created_at", "status"})
    return stable_hash(data)


def canonical_caption_cues(timeline: CanonicalMediaTimeline | None) -> list[Any]:
    if timeline is None:
        return []
    top_level = list(getattr(timeline, "caption_cues", []) or [])
    if top_level:
        return top_level
    return [cue for segment in timeline.segments for cue in list(getattr(segment, "caption_cues", []) or [])]


def canonical_caption_compilation_hash(timeline: CanonicalMediaTimeline, cues: list[Any]) -> str:
    direct = getattr(timeline, "caption_compilation_hash", None)
    if direct:
        return str(direct)
    metrics = timeline.qc_metrics or {}
    recorded = metrics.get("caption_compilation_hash")
    if recorded:
        return str(recorded)
    return stable_hash([item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in cues])


def canonical_caption_render_hash(cues: list[Any]) -> str:
    return stable_hash(caption_render_payload(cues))


class NativeRenderPlanValidator:
    """Pure deterministic gates. No DB mutation, provider call, or narrative inference."""

    def validate(
        self,
        plan: NativeRenderPlan,
        *,
        workspace_root: Path | None = None,
        execution: bool = False,
        allow_resolved_provider_assets: bool = False,
        canonical_timeline: CanonicalMediaTimeline | None = None,
    ) -> list[GateResult]:
        results: list[GateResult] = []
        required = [plan.channel_profile_version_id, plan.effective_context_snapshot_id, plan.effective_context_hash, plan.script_hash, plan.srt_hash, plan.visual_plan_hash]
        results.append(self._gate("NativeRenderPlanCompletenessGate", all(required) and bool(plan.scenes), "PLAN_INCOMPLETE"))
        results.append(self._gate("FrozenContextReferenceGate", bool(plan.channel_profile_version_id and plan.effective_context_snapshot_id and plan.effective_context_hash), "FROZEN_CONTEXT_REF_MISSING"))
        results.append(self._gate("FormatIdentityReferenceGate", plan.format_identity_status == "APPROVED" and bool(plan.format_identity_contract_hash), "FORMAT_IDENTITY_NOT_APPROVED"))
        originality_ok = (not plan.production_eligible) or plan.final_originality_gate == "PASS"
        results.append(self._gate("FinalOriginalityReferenceGate", originality_ok and bool(plan.episode_originality_manifest_hash), "FINAL_ORIGINALITY_NOT_PASS"))
        results.append(self._gate("NoCharacterRenderGate", plan.character_policy_mode == "NO_CHARACTER", "CHARACTER_POLICY_CONFLICT"))
        ordered = sorted(plan.scenes, key=lambda s: s.narration_start_ms)
        overlap = any(a.narration_end_ms > b.narration_start_ms for a, b in zip(ordered, ordered[1:]))
        results.append(self._gate("SceneTimelineGate", not overlap, "SCENE_TIMELINE_OVERLAP"))
        segments = [seg for scene in plan.scenes for seg in scene.source_segment_ids]
        results.append(self._gate("SegmentCoverageGate", bool(segments) and len(segments) == len(set(segments)), "SEGMENT_COVERAGE_INVALID"))
        canonical_cues = canonical_caption_cues(canonical_timeline)
        legacy_caption_exists = Path(plan.srt_ref).is_file() if execution else bool(plan.srt_ref)
        results.append(
            self._gate(
                "CaptionTimelineGate",
                bool(canonical_cues) if plan.temporal_authority_mode == "CANONICAL_STRICT" else legacy_caption_exists,
                "CAPTION_AUTHORITY_MISSING",
            )
        )
        if plan.temporal_authority_mode == "CANONICAL_STRICT":
            refs_present = bool(
                plan.canonical_media_timeline_ref
                and plan.canonical_media_timeline_hash
                and plan.canonical_audio_asset_ref
            )
            results.append(self._gate("CanonicalMediaTimelineReferenceGate", refs_present, "TEMPORAL_CANONICAL_TIMELINE_REQUIRED"))
            results.append(self._gate("ParallelTimingInputGate", not plan.parallel_timing_inputs, "TEMPORAL_PARALLEL_TIMELINE_DETECTED", plan.parallel_timing_inputs))
            results.append(
                self._gate(
                    "CanonicalSceneTimingSourceGate",
                    plan.scene_timing_source == "CANONICAL_MEDIA_TIMELINE",
                    "TEMPORAL_SCENE_ESTIMATE_USED",
                )
            )
            results.append(
                self._gate(
                    "CanonicalCaptionTimingSourceGate",
                    plan.caption_timing_source == "CANONICAL_MEDIA_TIMELINE",
                    "TEMPORAL_PARALLEL_TIMELINE_DETECTED",
                )
            )
            results.append(
                self._gate(
                    "CanonicalMediaTimelineEvidenceGate",
                    canonical_timeline is not None,
                    "TEMPORAL_CANONICAL_TIMELINE_EVIDENCE_MISSING",
                )
            )
            if canonical_timeline is not None:
                actual_hash = stable_hash(canonical_timeline.model_dump(mode="json", exclude={"timeline_hash"}))
                hash_ok = (
                    canonical_timeline.timeline_hash == actual_hash
                    and plan.canonical_media_timeline_hash == actual_hash
                )
                results.append(self._gate("CanonicalMediaTimelineHashGate", hash_ok, "TEMPORAL_TIMELINE_HASH_MISMATCH"))
                results.append(
                    self._gate(
                        "CanonicalAudioAssetGate",
                        plan.canonical_audio_asset_ref == canonical_timeline.audio_asset_id,
                        "TEMPORAL_AUDIO_ASSET_MISMATCH",
                    )
                )
                timeline_endpoint_ok = bool(
                    canonical_timeline.segments
                    and max(item.scene_end_ms for item in canonical_timeline.segments)
                    == canonical_timeline.audio_duration_ms
                )
                plan_endpoint_ok = bool(
                    plan.scenes
                    and max(item.narration_end_ms for item in plan.scenes)
                    == canonical_timeline.audio_duration_ms
                )
                results.append(
                    self._gate(
                        "CanonicalTimelineDurationEndpointGate",
                        timeline_endpoint_ok and plan_endpoint_ok,
                        "TEMPORAL_AUDIO_ENDPOINT_MISMATCH",
                    )
                )
                metrics = canonical_timeline.qc_metrics or {}
                expected_caption_hash = metrics.get("caption_compilation_hash")
                expected_caption_ref = metrics.get("caption_compilation_ref")
                expected_render_hash = metrics.get("caption_render_payload_hash")
                actual_render_hash = canonical_caption_render_hash(canonical_cues) if canonical_cues else None
                caption_refs_ok = bool(
                    canonical_cues
                    and expected_caption_hash
                    and expected_caption_ref == f"caption-compilation:{expected_caption_hash}"
                    and plan.canonical_caption_compilation_ref == expected_caption_ref
                    and plan.canonical_caption_compilation_hash == expected_caption_hash
                )
                results.append(
                    self._gate(
                        "CanonicalCaptionCompilationReferenceGate",
                        caption_refs_ok,
                        "CAPTION_CANONICAL_COMPILATION_REQUIRED",
                    )
                )
                render_payload_ok = bool(
                    expected_render_hash
                    and actual_render_hash == expected_render_hash
                    and plan.canonical_caption_render_payload_hash == expected_render_hash
                )
                results.append(
                    self._gate(
                        "CanonicalCaptionRenderPayloadGate",
                        render_payload_ok,
                        "CAPTION_RENDER_PAYLOAD_HASH_MISMATCH",
                    )
                )
                render_style = metrics.get("caption_render_style")
                results.append(
                    self._gate(
                        "CanonicalCaptionRenderStyleGate",
                        isinstance(render_style, dict)
                        and bool(render_style.get("policy_hash"))
                        and bool(render_style.get("style_version")),
                        "CAPTION_RENDER_STYLE_REQUIRED",
                    )
                )
                no_independent_srt = bool(
                    expected_caption_hash
                    and plan.srt_ref == expected_caption_ref
                    and plan.srt_hash == expected_caption_hash
                    and not Path(plan.srt_ref).is_file()
                )
                results.append(
                    self._gate(
                        "CanonicalCaptionArtifactGate",
                        no_independent_srt,
                        "SYNC_PARALLEL_TIMELINE",
                    )
                )
                timing_by_scene = {
                    item.segment_id: (item.scene_start_ms, item.scene_end_ms, item.target_scene_duration_ms)
                    for item in canonical_timeline.segments
                }
                scene_timing_ok = len(timing_by_scene) == len(plan.scenes) and all(
                    timing_by_scene.get(scene.scene_id)
                    == (scene.narration_start_ms, scene.narration_end_ms, scene.duration_ms)
                    for scene in plan.scenes
                )
                results.append(
                    self._gate(
                        "CanonicalSceneTimingDerivationGate",
                        scene_timing_ok,
                        "TEMPORAL_SCENE_NOT_DERIVED_FROM_TIMELINE",
                    )
                )
                caption_gate_names = (
                    "NarrationPacingGate",
                    "CaptionCompilationGate",
                    "CaptionLayoutGate",
                    "CaptionSafeAreaGate",
                    "CaptionAudioSyncGate",
                    "CaptionCoverageGate",
                    "TimelineDriftGate",
                )
                cqr1_render = plan.purpose in {
                    "CQR1_LOCAL_GOLDEN_FIXTURE",
                    "CQR1_CONTROLLED_PAID_CANARY",
                }
                caption_gate_missing: list[str] = []
                caption_gate_blocked: list[str] = []
                for gate_name in caption_gate_names:
                    raw = plan.creative_gate_results.get(gate_name)
                    verdict = (
                        raw.get("result", raw.get("status"))
                        if isinstance(raw, dict)
                        else raw
                    )
                    if verdict == "BLOCK":
                        caption_gate_blocked.append(gate_name)
                    elif cqr1_render and verdict not in {"PASS", "REVIEW_REQUIRED"}:
                        caption_gate_missing.append(gate_name)
                results.append(
                    self._gate(
                        "CQR1CaptionCreativeGateEvidenceGate",
                        not caption_gate_blocked and not caption_gate_missing,
                        "CAPTION_CREATIVE_GATE_EVIDENCE_MISSING_OR_BLOCKED",
                        [*caption_gate_blocked, *caption_gate_missing],
                    )
                )
        unresolved: list[str] = []
        provider_scenes = [
            scene for scene in plan.scenes if scene.visual_treatment in {"STOCK_VIDEO", "AI_HERO_VIDEO"}
        ]
        if plan.temporal_authority_mode == "CANONICAL_STRICT" and provider_scenes:
            visual_refs_ok = bool(plan.visual_direction_contract_ref and plan.visual_direction_contract_hash)
            results.append(
                self._gate(
                    "VisualDirectionContractReferenceGate",
                    visual_refs_ok,
                    "VISUAL_DIRECTION_CONTRACT_REQUIRED",
                )
            )
            required_creative = ("SceneSemanticMatchGate", "VisualContinuityGate", "AssetAdjacencyGate")
            creative_ok = True
            details: list[str] = []
            for gate_name in required_creative:
                raw = plan.creative_gate_results.get(gate_name)
                verdict = raw.get("result") if isinstance(raw, dict) else raw
                if verdict not in {"PASS", "REVIEW_REQUIRED"}:
                    creative_ok = False
                    details.append(gate_name)
            results.append(
                self._gate(
                    "CreativeVisualGateEvidenceGate",
                    creative_ok,
                    "CREATIVE_VISUAL_GATE_EVIDENCE_MISSING_OR_BLOCKED",
                    details,
                )
            )
        for scene in plan.scenes:
            resolved = {item.key for item in scene.resolved_asset_refs}
            unresolved.extend(f"{scene.scene_id}:{req.key}" for req in scene.asset_requirements if req.required and req.key not in resolved)
            if execution and not allow_resolved_provider_assets and scene.visual_treatment in {"STOCK_VIDEO", "AI_HERO_VIDEO"}:
                unresolved.append(f"{scene.scene_id}:PROVIDER_INTENT_NOT_LOCAL")
        results.append(self._gate("AssetResolutionGate", not unresolved, "ASSET_UNRESOLVED", unresolved))
        results.append(self._gate("OutputProfileGate", bool(plan.output_profiles) and all(p in ACTIVE_OUTPUT_PROFILES for p in plan.output_profiles), "OUTPUT_PROFILE_UNSUPPORTED"))
        if workspace_root:
            results.append(self._gate("WorkspaceBoundaryGate", workspace_root.is_absolute(), "WORKSPACE_ROOT_NOT_ABSOLUTE"))
        return results

    @staticmethod
    def reduce(results: list[GateResult]) -> str:
        verdicts = {item.verdict for item in results}
        return "BLOCK" if "BLOCK" in verdicts else "REVIEW_REQUIRED" if "REVIEW_REQUIRED" in verdicts else "PASS"

    @staticmethod
    def _gate(name: str, passed: bool, code: str, details: list[str] | None = None) -> GateResult:
        return GateResult(gate=name, verdict="PASS" if passed else "BLOCK", reason_codes=[] if passed else [code, *(details or [])])


ACTIVE_OUTPUT_PROFILES = {
    "YT_LONG_1080P30_SDR_H264_VT",
    "YT_SHORT_1080X1920_30_SDR_H264_VT",
}

OUTPUT_PROFILES = {
    "YT_LONG_1080P30_SDR_H264_VT": {"width": 1920, "height": 1080, "fps": 30, "codec": "h264_videotoolbox", "pix_fmt": "yuv420p", "color": "bt709", "audio_codec": "aac", "sample_rate": 48000, "channels": 2, "faststart": True, "bitrate_policy": {"mode": "VBR_TARGET_MAX", "target": "8M", "maxrate": "10M", "strict_cbr": False}},
    "YT_SHORT_1080X1920_30_SDR_H264_VT": {"width": 1080, "height": 1920, "fps": 30, "codec": "h264_videotoolbox", "pix_fmt": "yuv420p", "color": "bt709", "audio_codec": "aac", "sample_rate": 48000, "channels": 2, "faststart": True, "caption_safe_area": "PORTRAIT", "bitrate_policy": {"mode": "VBR_TARGET_MAX", "target": "8M", "maxrate": "10M", "strict_cbr": False}},
}
