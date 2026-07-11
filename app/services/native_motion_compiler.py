from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from app.contracts.native_renderer import CompiledNativeRenderManifest, NativeRenderPlan
from app.services.native_render_plan import OUTPUT_PROFILES, NativeRenderPlanValidator, canonical_plan_hash, stable_hash


MOTION_PACK_VERSION = "NativeMotionPack_v1"
COMPILER_VERSION = "native-motion-compiler/1.0.0"
RAW_FILTER_PATTERN = re.compile(r"[;\[\]`$|&<>\\\n\r]")


def _preset(key: str, category: str, treatments: list[str], handler: str, purpose: str, bounds: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"preset_key": key, "preset_version": "1.0.0", "category": category, "supported_visual_treatments": treatments, "required_inputs": [], "optional_parameters": list((bounds or {}).keys()), "parameter_bounds": bounds or {}, "compiler_handler": handler, "deterministic_defaults": {}, "output_constraints": {"max_fps": 30, "max_dimension": 1920}, "human_readable_purpose": purpose, "deprecated": False}


ALL_NATIVE = ["NATIVE_SLIDE", "DIAGRAM", "UI_SIMULATION", "KINETIC_TYPOGRAPHY", "DATA_CARD", "QUOTE_SLIDE", "COMPARISON_SLIDE", "TIMELINE", "STATIC_COMPOSITION"]
MOTION_PACK = {
    **{k: _preset(k, "TRANSITION", ALL_NATIVE, "compile_transition", k.replace("_", " ")) for k in ("cut", "fade_soft", "fade_black", "dissolve", "slide_left", "slide_right", "cover_left", "reveal_up")},
    **{k: _preset(k, "STILL_MOTION", ALL_NATIVE, "compile_still_motion", k.replace("_", " "), {"intensity": [0.0, 1.0], "zoom_max": [1.0, 1.12]}) for k in ("hold_static", "kenburns_center_soft", "kenburns_subject_left", "pushin_slow", "pan_left_slow", "pan_right_slow")},
    **{k: _preset(k, "CARD_UI", ALL_NATIVE, "compile_card", k.replace("_", " ")) for k in ("lowerthird_slidein", "fact_card_pop", "data_card_hold", "comparison_reveal", "timeline_step_reveal", "cta_card_fadeup")},
    **{k: _preset(k, "OVERLAY", ALL_NATIVE, "compile_overlay", k.replace("_", " ")) for k in ("caption_burn_ass_v1", "logo_bug_static", "badge_corner")},
    **{k: _preset(k, "AUDIO", ALL_NATIVE, "compile_audio", k.replace("_", " ")) for k in ("voice_only_basic", "voice_music_duck_basic", "fade_in_out_basic")},
}

SEMANTIC_MAP = {"HOLD_STATIC": "hold_static", "SLOW_ZOOM_IN": "kenburns_center_soft", "SLOW_ZOOM_OUT": "pushin_slow", "PAN_LEFT": "pan_left_slow", "PAN_RIGHT": "pan_right_slow", "SLIDE_IN_LEFT": "lowerthird_slidein", "SLIDE_IN_RIGHT": "lowerthird_slidein", "REVEAL_UP": "reveal_up", "FADE_IN": "fade_soft", "FADE_OUT": "fade_soft", "HIGHLIGHT": "fact_card_pop", "COUNT_UP": "data_card_hold", "PARALLAX_LIGHT": "kenburns_subject_left"}
TRANSITION_MAP = {k.upper(): k for k in ("cut", "fade_soft", "fade_black", "dissolve", "slide_left", "slide_right", "cover_left", "reveal_up")}


class NativeMotionCompiler:
    def __init__(self, *, ffmpeg_capability_digest: str = "ffmpeg-full:h264_videotoolbox+aac+libass"):
        self.ffmpeg_capability_digest = ffmpeg_capability_digest
        self.validator = NativeRenderPlanValidator()

    def compile(self, plan: NativeRenderPlan) -> CompiledNativeRenderManifest:
        plan_hash = canonical_plan_hash(plan)
        if plan.content_hash and plan.content_hash != plan_hash:
            raise ValueError("PLAN_CONTENT_HASH_STALE")
        gates = self.validator.validate(plan, execution=True)
        reason_codes = [code for gate in gates if gate.verdict == "BLOCK" for code in gate.reason_codes]
        compiled_scenes, transitions, inputs = [], [], []
        for scene in plan.scenes:
            values = [scene.animation_type, scene.transition_in, scene.transition_out]
            if any(v and RAW_FILTER_PATTERN.search(v) for v in values):
                reason_codes.append("RAW_FILTER_SYNTAX_REJECTED")
            motion = SEMANTIC_MAP.get(scene.animation_type or "HOLD_STATIC")
            if motion not in MOTION_PACK:
                reason_codes.append("MOTION_PRESET_UNSUPPORTED")
            for transition in (scene.transition_in, scene.transition_out):
                if transition and transition not in TRANSITION_MAP:
                    reason_codes.append("TRANSITION_PRESET_UNSUPPORTED")
            compiled_scenes.append({"scene_id": scene.scene_id, "start_ms": scene.narration_start_ms, "end_ms": scene.narration_end_ms, "duration_ms": scene.duration_ms, "visual_treatment": scene.visual_treatment, "motion_preset": motion, "layout_type": scene.layout_type, "asset_refs": [a.model_dump() for a in scene.resolved_asset_refs]})
            inputs.extend(a.path for a in scene.resolved_asset_refs)
            if scene.transition_out:
                transitions.append({"scene_id": scene.scene_id, "preset": TRANSITION_MAP[scene.transition_out], "duration_ms": min(600, max(100, scene.duration_ms // 8))})
        if reason_codes:
            raise ValueError(";".join(sorted(set(reason_codes))))
        base = {"source_plan_ref": plan.plan_id, "source_plan_hash": plan_hash, "compiler_version": COMPILER_VERSION, "motion_pack_version": MOTION_PACK_VERSION, "renderer_profile_refs": plan.output_profiles, "ffmpeg_capability_digest": self.ffmpeg_capability_digest, "normalized_canvas": plan.canvas_spec.model_dump(), "normalized_audio": plan.audio_policy, "normalized_caption": plan.caption_policy, "compiled_scenes": compiled_scenes, "transition_schedule": transitions, "overlay_schedule": [], "audio_mix_schedule": plan.audio_policy, "caption_schedule": {"srt_ref": plan.srt_ref}, "output_specs": [OUTPUT_PROFILES[p] | {"profile": p} for p in plan.output_profiles], "expected_input_refs": sorted(set(inputs + [plan.srt_ref])), "unresolved_inputs": [], "compilation_warnings": [], "compilation_reason_codes": [], "production_eligible": plan.production_eligible}
        manifest_hash = stable_hash(base)
        return CompiledNativeRenderManifest(compiled_manifest_id=str(uuid.uuid5(uuid.NAMESPACE_URL, manifest_hash)), ffmpeg_binary_requirement="ffmpeg-full>=8", manifest_hash=manifest_hash, created_at=datetime.now(UTC), **base)
