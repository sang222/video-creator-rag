from app.services.nr2_motion_audit import MOTION_DECISIONS, compile_motion_decisions, differentiation_gate, motion_gates


SCENES = [{"scene_id": f"s{i}", "narrative_unit": unit, "role": "NATIVE", "start_ms": i * 1000, "end_ms": (i + 1) * 1000} for i, unit in enumerate(["HOOK", "OPERATIONAL_PROBLEM", "QUANTIFIED_SCENARIO", "MECHANISM_SETUP", "OPERATIONAL_COST", "MECHANISM_EXPLANATION", "PRACTICAL_EXAMPLE"])]


def groups(): return {key: compile_motion_decisions(key, SCENES) for key in MOTION_DECISIONS}


def test_every_scene_explicit_and_hold_has_reason():
    for decisions in groups().values(): assert motion_gates(decisions)["MotionDecisionCompletenessGate"] == "PASS"


def test_unrecorded_fallback_blocks():
    d = groups()["NR2_A_NATIVE_EXPLANATORY"]; d[0]["compiler_fallback_used"] = True
    assert motion_gates(d)["MotionFallbackAuditGate"] == "BLOCK"


def test_subthreshold_zoom_and_pan_require_review():
    d = groups()["NR2_C_HERO_HEAVY_PLACEHOLDER"]; d[0]["animation_parameters"]["zoom_delta"] = .01
    assert motion_gates(d)["MotionVisibilityGate"] == "REVIEW_REQUIRED"
    d[0]["animation_parameters"]["zoom_delta"] = .05; d[2]["animation_parameters"]["pan_displacement"] = .01
    assert motion_gates(d)["MotionVisibilityGate"] == "REVIEW_REQUIRED"


def test_motion_distributions_meaningfully_different(): assert differentiation_gate(groups()) == "PASS"


def test_c_excessive_continuous_motion_requires_review(): assert motion_gates(groups()["NR2_C_HERO_HEAVY_PLACEHOLDER"])["MotionOverloadGate"] == "REVIEW_REQUIRED"


def test_proxy_labels_and_clean_graph_separation():
    from tools.native_ffmpeg.nr2_1.run_nr2_1 import scene_filtergraph
    decisions = groups()["NR2_B_BALANCED"]
    assert "MOTION AUDIT" in scene_filtergraph("B", decisions, proxy=True, srt="x")
    assert "MOTION AUDIT" not in scene_filtergraph("B", decisions, proxy=False, srt="x")


def test_same_content_and_forbidden_entities_absent():
    from pathlib import Path
    source = Path("tools/native_ffmpeg/nr2_1/run_nr2_1.py").read_text()
    for term in ("FinalMediaRef", "HumanUploadTask", "requests.", "http://", "https://"): assert term not in source
