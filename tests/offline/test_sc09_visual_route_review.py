from __future__ import annotations

import json
from pathlib import Path

from app.services.sc07_sc09_visual_route_audit import (
    RUN_ID,
    SCENE_CONTEXT,
    build_revision_contract,
    run_offline_gate_rehearsal,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_STATE = REPO_ROOT / "var" / "mr1" / "runs" / RUN_ID / "run_state.json"
CONTINUATION = REPO_ROOT / "reports" / "mr1_pexels_continuation_review.json"
SUMMARY = REPO_ROOT / "reports" / "sc09_visual_route_review_summary.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_sc09_revised_query_was_not_submitted_or_consumed() -> None:
    run_state = _load(RUN_STATE)
    review = _load(CONTINUATION)
    summary = _load(SUMMARY)
    attempt = run_state["attempts"]["pexels:SC-09"]
    proof = review["pending_unsubmitted_attempt_proofs"]["SC-09"]

    assert attempt["attempt_count"] == proof["attempt_count"] == 0
    assert attempt["search_submit_count"] == proof["search_submit_count"] == 0
    assert attempt["download_submit_count"] == proof["download_submit_count"] == 0
    assert attempt["network_submit_started"] is False
    assert proof["submit_state"] == "NOT_SUBMITTED"
    assert proof["request_hash"] is None
    assert summary["revised_query_evidence"]["provider_submit_started"] is False
    assert summary["immutability_and_zero_call_proof"][
        "sc09_revised_query_submitted"
    ] is False


def test_sc09_route_is_independently_validated_not_inferred_from_attempt_budget() -> None:
    first = run_offline_gate_rehearsal("SC-09")
    second = run_offline_gate_rehearsal("SC-09")
    summary = _load(SUMMARY)

    assert first == second
    assert first["result"] == "PASS"
    assert first["preferred_source_route"] == "NATIVE_DIAGRAM"
    assert first["pexels_result"] == "PEXELS_PROHIBITED"
    assert first["provider_calls"] == 0
    assert summary["verdicts"]["SC09_ROUTE_VERDICT"] == (
        "SC09_PEXELS_ROUTE_INVALID"
    )
    assert summary["verdicts"]["SC09_QUERY_VERDICT"] == "REJECT_PEXELS_ROUTE"
    assert SCENE_CONTEXT["SC-09"]["features"]["motion_semantic_value"] < 0.70
    assert SCENE_CONTEXT["SC-09"]["features"]["diagram_clarity_advantage"] >= 0.60


def test_sc09_query_hash_cannot_authorize_execution_without_package_binding() -> None:
    contract = build_revision_contract()
    summary = _load(SUMMARY)

    assert summary["revised_query_evidence"]["query_authority_hash"] == (
        "95554e739a0e7d460e2f58d7400b43a426ef7db95c67431dbb1abae19f97c5f1"
    )
    assert contract["approval_rules"]["sc09_query_hash_requires_package_binding"] is True
    assert contract["approval_rules"]["old_approvals_historical_only"] is True
    assert contract["scenes"]["SC-09"]["provider_execution_required"] is False
    assert contract["scenes"]["SC-09"]["model_or_query"] == "N/A_NATIVE"
    assert contract["scenes"]["SC-09"]["maximum_approved_attempts"] == 0
    assert contract["fallback"] is False
