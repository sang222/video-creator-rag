from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.services.pkg1_sc07_sc09_revision import (
    REPO_ROOT,
    SOURCE_PACKAGE_HASH,
    SOURCE_PACKAGE_VERSION_ID,
    build_revision_bundle,
    compile_native_rehearsal,
    revalidate_bundle,
)


RUN_STATE = (
    REPO_ROOT
    / "var"
    / "mr1"
    / "runs"
    / "b932773c-4049-482a-8827-6933d924c34f"
    / "run_state.json"
)
SOURCE_SUMMARY = REPO_ROOT / "reports" / "pkg1_sc04_visual_revision_summary.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_revision_is_deterministic_superseding_and_source_immutable() -> None:
    source_before = _sha256(SOURCE_SUMMARY)
    run_before = _sha256(RUN_STATE)
    first = build_revision_bundle()
    second = build_revision_bundle()

    assert first == second
    assert revalidate_bundle(first) is True
    assert first["identity"]["project_type"] == "PKG1_SC07_SC09_REVISION"
    assert first["identity"]["revision_version"] == 4
    assert first["source"]["package_artifact_version_id"] == SOURCE_PACKAGE_VERSION_ID
    assert first["source"]["package_content_hash"] == SOURCE_PACKAGE_HASH
    assert first["package_manifest"]["content"]["supersedes"][
        "package_artifact_version_id"
    ] == SOURCE_PACKAGE_VERSION_ID
    assert first["package_manifest"]["artifact_version_id"] != SOURCE_PACKAGE_VERSION_ID
    assert first["package_manifest"]["content_hash"] != SOURCE_PACKAGE_HASH
    assert _sha256(SOURCE_SUMMARY) == source_before
    assert _sha256(RUN_STATE) == run_before


def test_only_sc07_sc09_visual_patches_change_and_routes_are_native() -> None:
    bundle = build_revision_bundle()
    intents = bundle["artifacts"]["scene_visual_intent"]["content"]
    decisions = bundle["artifacts"]["visual_source_decision_set"]["content"]
    visual_plan = bundle["artifacts"]["visual_plan"]["content"]

    assert set(intents["affected_scenes"]) == {"SC-07", "SC-09"}
    assert set(decisions["affected_scenes"]) == {"SC-07", "SC-09"}
    assert intents["unaffected_scenes_exact"] is True
    assert decisions["unaffected_scenes_exact"] is True
    assert visual_plan["unaffected_scenes_exact"] is True
    assert decisions["affected_scenes"]["SC-07"]["preferred_source_route"] == (
        "NATIVE_MOTION_GRAPHIC"
    )
    assert decisions["affected_scenes"]["SC-09"]["preferred_source_route"] == (
        "NATIVE_DIAGRAM"
    )
    assert all(
        item["provider_execution_required"] is False
        for item in decisions["affected_scenes"].values()
    )


def test_no_pexels_gemini_veo_or_fallback_remains_for_affected_scenes() -> None:
    bundle = build_revision_bundle()
    requests = bundle["artifacts"]["compiled_asset_request_plan"]["content"]
    provider = bundle["artifacts"]["provider_execution_plan"]["content"]

    assert requests["removed_requests"] == ["pexels:SC-07", "pexels:SC-09"]
    assert {item["request_type"] for item in requests["requests"]} == {
        "NativeMotionGraphicRequest",
        "NativeDiagramRequest",
    }
    assert all(item["provider_call_required"] is False for item in requests["requests"])
    assert provider["removed_operations"] == ["pexels:SC-07", "pexels:SC-09"]
    assert provider["gemini_image_operations_added"] == 0
    assert provider["veo_operations_added"] == 0
    assert provider["fallback"] is False
    assert provider["provider_substitution_allowed"] is False
    assert provider["native_operations"]["SC-07"]["external_provider_attempts"] == 0
    assert provider["native_operations"]["SC-09"]["external_provider_attempts"] == 0


def test_native_plans_compile_deterministically_from_canonical_projection() -> None:
    first = compile_native_rehearsal()
    second = compile_native_rehearsal()
    bundle = build_revision_bundle()

    assert first == second
    assert first["scene_results"]["SC-07"]["result"] == "PASS"
    assert first["scene_results"]["SC-09"]["result"] == "PASS"
    assert first["scene_results"]["SC-07"]["route"] == "NATIVE_MOTION_GRAPHIC"
    assert first["scene_results"]["SC-09"]["route"] == "NATIVE_DIAGRAM"
    assert first["production_timing_authority_unchanged"] is True
    assert first["offline_projection_only"] is True
    assert first["renderer_input_eligible"] is True
    assert all(item["verdict"] == "PASS" for item in first["gate_results"])
    for key in ("sc07_native_motion_plan", "sc09_native_diagram_plan"):
        timing = bundle["artifacts"][key]["content"]["timeline_binding_policy"]
        assert timing["duration_source"] == "CanonicalMediaTimeline"
        assert timing["relative_phases_only"] is True
        assert timing["production_milliseconds_persisted_in_plan"] is False


def test_cost_rights_continuity_and_counters_are_closed() -> None:
    bundle = build_revision_bundle()
    cost = bundle["artifacts"]["cost_estimate_snapshot"]["content"]
    provenance = bundle["artifacts"]["asset_provenance_plan"]["content"]
    continuity = bundle["artifacts"]["visual_continuity_evidence"]["content"]
    run_state = json.loads(RUN_STATE.read_text(encoding="utf-8"))

    assert cost["operations"]["SC-07"]["cost_class"] == "COST_0_NATIVE"
    assert cost["operations"]["SC-09"]["cost_class"] == "COST_0_NATIVE"
    assert cost["historical_actual_cost_invented"] is False
    for scene_id in ("SC-07", "SC-09"):
        assert provenance["affected_scenes"][scene_id]["source_class"] == (
            "NATIVE_AUTHORED"
        )
        assert provenance["affected_scenes"][scene_id]["external_provider"] is False
        assert provenance["affected_scenes"][scene_id]["asset_file_state"] == (
            "NOT_CREATED_PLANNING_ONLY"
        )
    assert continuity["decision"] == "PASS_DISTINCT_NATIVE_VISUAL_GRAMMARS"
    assert continuity["repeated_stock_or_generated_metaphor"] is False
    assert bundle["no_execution_proof"]["provider_calls"] == 0
    assert bundle["no_execution_proof"]["render_calls"] == 0
    assert run_state["provider_call_counts"]["logical_total"] == 4
    assert run_state["render_attempts"] == 0
    assert run_state["provider_call_counts"]["drive"] == 0
    assert run_state["provider_call_counts"]["youtube"] == 0


def test_review_packet_and_supersession_are_exact_and_pending() -> None:
    bundle = build_revision_bundle()
    summary = json.loads(
        (
            REPO_ROOT / "reports" / "pkg1_sc07_sc09_revision_summary.json"
        ).read_text(encoding="utf-8")
    )
    report = (
        REPO_ROOT / "reports" / "pkg1_sc07_sc09_revision_report.md"
    ).read_text(encoding="utf-8")
    supersession = bundle["artifacts"]["approval_supersession_manifest"]["content"]

    assert summary["identity"]["bundle_hash"] == bundle["bundle_hash"]
    assert summary["review_packet"]["content_hash"] == (
        bundle["review_packet"]["content_hash"]
    )
    assert bundle["review_packet"]["content_hash"] in report
    assert "PASS" in report
    assert "REJECT: <reason>" in report
    assert supersession["superseded_by"] == "SUPERSEDED_BY_SC07_SC09_REVISION"
    assert all(
        item["preserved"] is True
        and item["execution_reusable"] is False
        for item in supersession["historical_authorities"]
    )
    assert supersession["consumed_ledgers_mutated"] is False
