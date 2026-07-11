from __future__ import annotations

from collections import Counter
from typing import Any

from app.services.native_render_plan import stable_hash


HOLD_REASONS = {"data_readability", "dense_diagram", "emphasis_hold", "pacing_rest", "caption_heavy_section"}
MOVING_PRESETS = {"kenburns_center_soft", "kenburns_subject_left", "pushin_slow", "pan_left_slow", "pan_right_slow", "lowerthird_slidein", "fact_card_pop", "comparison_reveal", "timeline_step_reveal", "cta_card_fadeup"}

MOTION_DECISIONS = {
    "NR2_A_NATIVE_EXPLANATORY": [
        ("fact_card_pop", {}), ("pushin_slow", {"zoom_delta": .025}), ("data_card_hold", {"hold_reason": "data_readability"}),
        ("timeline_step_reveal", {}), ("hold_static", {"hold_reason": "caption_heavy_section"}),
        ("comparison_reveal", {}), ("hold_static", {"hold_reason": "pacing_rest"}),
    ],
    "NR2_B_BALANCED": [
        ("kenburns_center_soft", {"zoom_delta": .04}), ("pushin_slow", {"zoom_delta": .035}),
        ("data_card_hold", {"hold_reason": "data_readability"}), ("timeline_step_reveal", {}),
        ("pan_left_slow", {"pan_displacement": .05}), ("pan_right_slow", {"pan_displacement": .05}),
        ("cta_card_fadeup", {}),
    ],
    "NR2_C_HERO_HEAVY_PLACEHOLDER": [
        ("kenburns_subject_left", {"zoom_delta": .055}), ("pushin_slow", {"zoom_delta": .05}),
        ("pan_left_slow", {"pan_displacement": .07}), ("pan_right_slow", {"pan_displacement": .07}),
        ("kenburns_center_soft", {"zoom_delta": .06}), ("kenburns_subject_left", {"zoom_delta": .06}),
        ("cta_card_fadeup", {}),
    ],
}


def compile_motion_decisions(strategy_key: str, scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decisions = []
    for scene, (preset, params) in zip(scenes, MOTION_DECISIONS[strategy_key]):
        item = {"scene_id": scene["scene_id"], "narrative_unit": scene["narrative_unit"], "visual_treatment": scene["role"], "animation_type_requested": preset.upper(), "animation_preset_compiled": preset, "transition_in": "cut" if not decisions else "fade_soft", "transition_out": "fade_soft" if scene != scenes[-1] else "cut", "animation_start_ms": scene["start_ms"], "animation_end_ms": scene["end_ms"], "animation_parameters": {"intensity": .45, "duration_ms": scene["end_ms"] - scene["start_ms"], **params}, "compiler_fallback_used": False, "hold_static_reason": params.get("hold_reason"), "expected_visible_movement": preset not in {"hold_static", "data_card_hold"}, "actual_evidence_ref": None}
        item["decision_hash"] = stable_hash(item); decisions.append(item)
    return decisions


def motion_gates(decisions: list[dict[str, Any]]) -> dict[str, str]:
    completeness = all(d.get("animation_preset_compiled") and (d["animation_preset_compiled"] != "hold_static" or d.get("hold_static_reason") in HOLD_REASONS) for d in decisions)
    fallback = all(d.get("compiler_fallback_used") is False for d in decisions)
    visibility = True
    for d in decisions:
        p, params = d["animation_preset_compiled"], d["animation_parameters"]
        if p in {"kenburns_center_soft", "kenburns_subject_left", "pushin_slow"}: visibility &= params.get("zoom_delta", 0) >= .02
        if p in {"pan_left_slow", "pan_right_slow"}: visibility &= params.get("pan_displacement", 0) >= .03
    moving = sum(d["animation_preset_compiled"] in MOVING_PRESETS for d in decisions)
    overload = "REVIEW_REQUIRED" if moving == len(decisions) and sum(d["animation_preset_compiled"].startswith("kenburns") for d in decisions) >= 3 else "PASS"
    alignment = all(not (d["narrative_unit"] == "QUANTIFIED_SCENARIO" and d["animation_preset_compiled"] not in {"data_card_hold", "hold_static"}) for d in decisions)
    return {"MotionDecisionCompletenessGate": "PASS" if completeness else "BLOCK", "MotionVisibilityGate": "PASS" if visibility else "REVIEW_REQUIRED", "MotionFallbackAuditGate": "PASS" if fallback else "BLOCK", "MotionOverloadGate": overload, "MotionNarrativeAlignmentGate": "PASS" if alignment else "REVIEW_REQUIRED"}


def differentiation_gate(groups: dict[str, list[dict[str, Any]]]) -> str:
    signatures = {key: tuple(d["animation_preset_compiled"] for d in value) for key, value in groups.items()}
    distinct = len(set(signatures.values())) == len(signatures)
    distances = [sum(a != b for a, b in zip(signatures[x], signatures[y])) for x, y in ((list(signatures)[0], list(signatures)[1]), (list(signatures)[1], list(signatures)[2]))]
    return "PASS" if distinct and min(distances) >= 3 else "REVIEW_REQUIRED"


def audit_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    presets = Counter(d["animation_preset_compiled"] for d in decisions); transitions = Counter(d["transition_out"] for d in decisions)
    zoom = [d["animation_parameters"].get("zoom_delta") for d in decisions if d["animation_parameters"].get("zoom_delta")]
    pan = [d["animation_parameters"].get("pan_displacement") for d in decisions if d["animation_parameters"].get("pan_displacement")]
    return {"total_scene_count": len(decisions), "scenes_with_explicit_animation": len(decisions), "scenes_defaulted_to_HOLD_STATIC": 0, "intentional_HOLD_STATIC_count": presets["hold_static"], "motion_preset_usage_counts": dict(presets), "transition_usage_counts": dict(transitions), "native_motion_coverage_ratio": round(sum(d["expected_visible_movement"] for d in decisions) / len(decisions), 4), "average_transition_duration_ms": 500, "average_zoom_delta": round(sum(zoom) / len(zoom), 4) if zoom else 0, "average_pan_displacement": round(sum(pan) / len(pan), 4) if pan else 0, "fallback_default_count": 0}
