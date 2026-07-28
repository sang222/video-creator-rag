from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.services.sc07_sc09_visual_route_audit import (
    AUDIT_INPUT_SNAPSHOT_HASH,
    PRIOR_BEST_SEMANTIC_SCORE,
    RUN_ID,
    SEMANTIC_THRESHOLD,
    build_requirements,
    run_offline_gate_rehearsal,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = REPO_ROOT / "var" / "mr1" / "runs" / RUN_ID
RUN_STATE = RUN_ROOT / "run_state.json"
RANKING_EVIDENCE = (
    RUN_ROOT
    / "source_assets"
    / "pexels-search-ranking-failure-c91f13fab9d4-6a305fc90759e883.json"
)
SUMMARY = REPO_ROOT / "reports" / "sc07_visual_route_audit_summary.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sc07_old_attempt_evidence_and_threshold_are_immutable() -> None:
    evidence = _load(RANKING_EVIDENCE)
    summary = _load(SUMMARY)

    assert _sha256(RANKING_EVIDENCE) == (
        "1ebb69f9aece7a1ebcd375680d96738c517597a269c84d8b6d342a8caf86cba2"
    )
    assert _sha256(RUN_STATE) == (
        "769091619bc6bcfeeec1ade185a0efb4262d61381b9e5126a43fb6b86dfcff59"
    )
    assert evidence["semantic_fit_gate"]["threshold"] == SEMANTIC_THRESHOLD == 0.78
    assert (
        evidence["semantic_fit_gate"]["highest_ranked_semantic_relevance"]
        == PRIOR_BEST_SEMANTIC_SCORE
        == 0.60
    )
    assert evidence["retrieval_evidence"]["provider_result_count"] == 20
    assert evidence["technical_viability_filter"]["viable_candidate_count"] == 19
    assert len(summary["prior_provider_evidence"]["candidates"]) == 20
    assert summary["audit_input_snapshot_hash"] == AUDIT_INPUT_SNAPSHOT_HASH


def test_sc07_route_decision_is_deterministic_and_native_only() -> None:
    first = run_offline_gate_rehearsal("SC-07")
    second = run_offline_gate_rehearsal("SC-07")
    requirements = build_requirements("SC-07")

    assert first == second
    assert first["result"] == "PASS"
    assert first["preferred_source_route"] == "NATIVE_MOTION_GRAPHIC"
    assert first["pexels_result"] == "PEXELS_PROHIBITED"
    assert first["provider_execution_allowed"] is False
    assert first["provider_calls"] == 0
    assert requirements.named_workflow_nodes_required is True
    assert requirements.motion_semantic_value >= 0.70
    assert requirements.diagram_clarity_advantage >= 0.60
    assert all(first["named_gates"].values())


def test_sc07_audit_performs_no_provider_call_reset_or_runtime_fallback() -> None:
    run_state = _load(RUN_STATE)
    summary = _load(SUMMARY)
    old_attempt = run_state["attempts"]["pexels:SC-07"]
    supplemental = run_state["attempts"]["pexels:SC-07:supplement:02"]

    assert old_attempt["attempt_count"] == 1
    assert old_attempt["state"] == "CONSUMED_FAILED"
    assert supplemental["attempt_count"] == 1
    assert supplemental["state"] == "CONSUMED_FAILED"
    assert run_state["provider_call_counts"]["logical_total"] == 4
    proof = summary["immutability_and_zero_call_proof"]
    assert proof["provider_call_count_before"] == proof["provider_call_count_after"] == 4
    assert proof["old_attempt_ledgers_mutated"] is False
    assert proof["provider_submit_started"] is False
    assert proof["runtime_fallback"] is False
    assert summary["route_decision"]["preferred_source_route"] == (
        "NATIVE_MOTION_GRAPHIC"
    )
    assert summary["verdicts"]["SC07_ROUTE_VERDICT"] == "PEXELS_ROUTE_INVALID"
