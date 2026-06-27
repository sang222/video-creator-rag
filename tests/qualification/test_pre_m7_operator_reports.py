from __future__ import annotations

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skip(
    reason="Historical operator report fixture expected runtime mock providers; M12.1R cutover coverage lives in tests/test_m12_1r_mock_runtime_purge.py."
)

from app.contracts.gates import GateRunCreate
from app.db.models import GateRun
from app.services import GateDefinitionService, GateRunnerService, ProviderHealthService, SystemHealthService, WorkflowReadinessService

from .helpers.qualification_asserts import assert_operator_signal


def test_operator_readiness_gate_and_health_outputs_are_actionable(db_session, qualification_factory) -> None:
    flow = qualification_factory.m2_project()
    gate_run = GateRunnerService(db_session).run_gate(
        data=GateRunCreate(gate_key="rights_copyright_gate", target_type="artifact_version", target_id=flow.version.id)
    )
    assert gate_run.reason_codes
    assert gate_run.evidence_refs is not None
    assert gate_run.confidence_level
    assert gate_run.freshness_state

    definition = GateDefinitionService(db_session).get_active_gate_version("publish_risk_gate")
    db_session.add(
        GateRun(
            gate_definition_version_id=definition.id,
            gate_key="manual_block_gate",
            target_type="artifact_version",
            target_id=flow.version.id,
            video_project_id=flow.project.id,
            artifact_version_id=flow.version.id,
            policy_snapshot_id=flow.project.policy_snapshot_id,
            input_snapshot={"manual": True},
            input_snapshot_hash="operator-block",
            result="BLOCK",
            reason_codes=["MANUAL_REVIEW_REQUIRED"],
            freshness_state="UNKNOWN",
            confidence_level="HIGH",
            decision_basis={"next_action": "Review blocker before M7."},
        )
    )
    db_session.flush()
    readiness = WorkflowReadinessService(db_session).inspect_project(flow.project.id)
    assert readiness["status"] == "BLOCKED"
    assert readiness["blockers"]
    assert_operator_signal({"reason_codes": readiness["blockers"][0]["reason_codes"], "next_action": "Review blocker before M7."})

    health = ProviderHealthService(db_session).check_provider(provider_key="mock_llm", mode="unavailable")
    system = SystemHealthService(db_session).create_snapshot()
    assert_operator_signal({"reason_codes": health.reason_codes, "next_action": health.next_action, "overall_state": "BLOCKED"})
    assert_operator_signal({"reason_codes": system.reason_codes, "next_action": system.next_action, "overall_state": system.overall_state})
    assert db_session.scalars(select(GateRun)).all()
