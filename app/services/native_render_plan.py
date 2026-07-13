from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.contracts.native_renderer import GateResult, NativeRenderPlan


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def canonical_plan_hash(plan: NativeRenderPlan) -> str:
    data = plan.model_dump(mode="json", exclude={"content_hash", "created_at", "status"})
    return stable_hash(data)


class NativeRenderPlanValidator:
    """Pure deterministic gates. No DB mutation, provider call, or narrative inference."""

    def validate(
        self,
        plan: NativeRenderPlan,
        *,
        workspace_root: Path | None = None,
        execution: bool = False,
        allow_resolved_provider_assets: bool = False,
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
        srt_exists = Path(plan.srt_ref).is_file() if execution else bool(plan.srt_ref)
        results.append(self._gate("CaptionTimelineGate", srt_exists, "SRT_MISSING"))
        unresolved: list[str] = []
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
