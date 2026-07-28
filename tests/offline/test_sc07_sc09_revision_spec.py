from __future__ import annotations

import json
from pathlib import Path

from app.services.sc07_sc09_visual_route_audit import (
    RUN_ID,
    build_revision_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SPEC = REPO_ROOT / "reports" / "sc07_sc09_package_revision_spec.md"
REPAIR_CYCLES = REPO_ROOT / "reports" / "sc07_sc09_audit_repair_cycles.json"
SC07_SUMMARY = REPO_ROOT / "reports" / "sc07_visual_route_audit_summary.json"
SC09_SUMMARY = REPO_ROOT / "reports" / "sc09_visual_route_review_summary.json"
RUN_STATE = REPO_ROOT / "var" / "mr1" / "runs" / RUN_ID / "run_state.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_cross_scene_continuity_constraints_are_persisted_and_distinct() -> None:
    spec = PACKAGE_SPEC.read_text(encoding="utf-8")
    contract = build_revision_contract()

    assert "SC07_SC09_CONTINUITY_DECISION=PASS_DISTINCT_NATIVE_VISUAL_GRAMMARS" in spec
    assert "horizontal state flow + exception branch + queue" in spec
    assert "centered audit card + baseline/pilot/result rail" in spec
    assert "không dùng stock office/team/paperwork/planning" in spec
    assert contract["scenes"]["SC-07"]["preferred_source_route"] == (
        "NATIVE_MOTION_GRAPHIC"
    )
    assert contract["scenes"]["SC-09"]["preferred_source_route"] == "NATIVE_DIAGRAM"
    assert contract["scenes"]["SC-07"]["decision_hash"] != (
        contract["scenes"]["SC-09"]["decision_hash"]
    )


def test_revision_spec_touches_only_affected_visual_artifacts() -> None:
    contract = build_revision_contract()
    affected = set(contract["affected_artifacts"])
    unchanged = set(contract["unchanged_artifacts"])

    assert {
        "SC-07 SceneVisualIntent",
        "SC-07 VisualSourceDecision",
        "SC-09 SceneVisualIntent",
        "SC-09 VisualSourceDecision",
        "VisualPlan",
        "CompiledAssetRequestPlan",
        "ProviderExecutionPlan",
        "CostEstimateSnapshot",
    } <= affected
    assert {
        "idea",
        "research",
        "claims",
        "script",
        "SpokenTextNormalized",
        "voice_policy",
        "channel_profile_v3",
        "compiled_snapshot_v3",
        "TargetMarketProfile",
        "SC-01..SC-06",
        "SC-08",
    } <= unchanged
    assert affected.isdisjoint(unchanged)


def test_old_approvals_cannot_authorize_new_routes_and_counters_stay_fixed() -> None:
    contract = build_revision_contract()
    run_state = _load(RUN_STATE)
    sc07 = _load(SC07_SUMMARY)
    sc09 = _load(SC09_SUMMARY)

    rules = contract["approval_rules"]
    assert rules["old_sc07_consumed_ledger_immutable"] is True
    assert rules["old_approvals_historical_only"] is True
    assert rules["new_sc07_route_cannot_use_old_approval"] is True
    assert rules["new_package_revision_and_new_mr1_approval_required"] is True
    assert contract["provider_calls_during_task"] == 0
    assert contract["fallback"] is False
    assert run_state["provider_call_counts"]["logical_total"] == 4
    assert sc07["immutability_and_zero_call_proof"]["provider_call_count_after"] == 4
    assert sc09["immutability_and_zero_call_proof"]["provider_call_count_after"] == 4
    assert run_state["render_attempts"] == 0
    assert run_state["provider_call_counts"]["drive"] == 0
    assert run_state["provider_call_counts"]["youtube"] == 0


def test_reports_and_repair_cycle_contract_are_complete() -> None:
    repair = _load(REPAIR_CYCLES)
    sc07 = _load(SC07_SUMMARY)
    sc09 = _load(SC09_SUMMARY)
    spec = PACKAGE_SPEC.read_text(encoding="utf-8")

    assert repair["cycle_count"] == len(repair["cycles"]) == 2
    assert repair["provider_calls_during_repairs"] == 0
    assert repair["thresholds_changed"] is False
    assert repair["tests_deleted_or_skipped"] is False
    assert sc07["offline_gate_rehearsal"][
        "SC07_REVISED_ROUTE_TECHNICAL_PREFLIGHT"
    ] == "PASS"
    assert sc09["offline_gate_rehearsal"][
        "SC09_REVISED_ROUTE_TECHNICAL_PREFLIGHT"
    ] == "PASS"
    assert "PROCEED_TO_SC07_SC09_PACKAGE_REVISION=true" in spec
    assert "MR1_FINAL=BLOCKED_REQUIRES_PACKAGE_REVISION_AND_NEW_APPROVAL" in spec
