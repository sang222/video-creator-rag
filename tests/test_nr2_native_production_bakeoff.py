from pathlib import Path

import pytest

from app.services.nr2_bakeoff import STRATEGIES, assert_local_output, placeholder_truthfulness, plan_diff_manifest, strategy_distribution_gate, validate_same_content, validate_strategy_risks


def test_nr1_final_is_approved_before_nr2():
    import json
    summary = json.loads(Path("reports/nr1_native_renderer_summary.json").read_text())
    assert summary["human_review"] == "PASS" and summary["final"] == "PASS" and summary["proceed_to_nr2"] is True


def test_provider_intent_is_data_only():
    source = Path("app/services/nr2_bakeoff.py").read_text().lower()
    assert set(STRATEGIES) == {"NR2_A_NATIVE_EXPLANATORY", "NR2_B_BALANCED", "NR2_C_HERO_HEAVY_PLACEHOLDER"}


@pytest.mark.parametrize("key", list(STRATEGIES))
def test_strategy_distributions_validate(key):
    assert strategy_distribution_gate(key, STRATEGIES[key]["roles"]) == "PASS"


@pytest.mark.parametrize("future", ["GOOGLE_VEO", "PEXELS"])
def test_local_placeholder_cannot_claim_provider_provenance(future):
    asset = {"planned_future_source": future, "actual_NR2_source": "LOCAL_PLACEHOLDER", "provider_quality_not_evaluated": True, "production_eligible": False}
    if future == "GOOGLE_VEO": asset |= {"asset_status": "LOCAL_HERO_PLACEHOLDER", "not_provider_generated": True}
    assert placeholder_truthfulness(asset) == "PASS"
    asset["actual_NR2_source"] = "PROVIDER_GENERATED"
    assert placeholder_truthfulness(asset) == "BLOCK"


def test_same_content_integrity_and_deterministic_distinct_manifests():
    common = {"script_ref": "s", "script_hash": "sh", "audio_ref": "a", "audio_hash": "ah", "srt_ref": "c", "srt_hash": "ch", "timing_hash": "t", "output_profile": "YT_LONG_1080P30_SDR_H264_VT"}
    plans = [common | {"strategy_key": key, "visual_treatment": value["roles"], "plan_id": key, "plan_hash": key} for key, value in STRATEGIES.items()]
    assert validate_same_content(plans) == "PASS"
    plans[2]["audio_hash"] = "changed"; assert validate_same_content(plans) == "BLOCK"
    plans[2]["audio_hash"] = "ah"
    diff = plan_diff_manifest(plans)
    assert diff["complete"] and diff["changed_fields"]


def test_no_character_and_non_production_are_required():
    base = {"character_policy_mode": "NO_CHARACTER", "production_eligible": False}
    assert base["character_policy_mode"] == "NO_CHARACTER" and base["production_eligible"] is False


def test_explanation_stock_hero_and_motion_risks():
    assert validate_strategy_risks("NR2_A_NATIVE_EXPLANATORY", ["SUPPORTING"] * 5 + ["NATIVE"] * 2, transition_count=6)["StockBackboneRiskGate"] == "BLOCK"
    c = validate_strategy_risks("NR2_C_HERO_HEAVY_PLACEHOLDER", STRATEGIES["NR2_C_HERO_HEAVY_PLACEHOLDER"]["roles"], transition_count=6)
    assert c["ExplanationCoverageGate"] == "PASS" and c["HeroOveruseRiskGate"] == "REVIEW_REQUIRED"
    assert validate_strategy_risks("NR2_B_BALANCED", STRATEGIES["NR2_B_BALANCED"]["roles"], transition_count=8)["MotionOverloadGate"] == "REVIEW_REQUIRED"


def test_output_escape_blocked(tmp_path):
    with pytest.raises(ValueError, match="OUTPUT_PATH_ESCAPE"): assert_local_output(tmp_path, Path("/tmp/outside.mp4"))


def test_forbidden_production_entities_absent_from_nr2_runtime():
    source = Path("app/services/nr2_bakeoff.py").read_text() + Path("tools/native_ffmpeg/nr2/run_nr2.py").read_text()
    for forbidden in ("FinalMediaRef", "CloudMediaRef", "HumanUploadTask", "ProviderJobSnapshot", "PaidProviderCallLedger"):
        assert forbidden not in source
