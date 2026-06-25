from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.contracts.gates import GateRunCreate, PolicyChangeRecordCreate, PolicyRevalidationBatchCreate
from app.contracts.workflow import ArtifactVersionCreate
from app.core.errors import ValidationFailureError
from app.db.models import GateRun, ReviewTask
from app.services import ArtifactService, GateDefinitionService, GateRunnerService, PolicyChangeService, PolicyRevalidationService, WorkflowReadinessService


def test_m3_gate_states_hash_review_block_and_immutability(db_session, qualification_factory) -> None:
    flow = qualification_factory.m2_project()
    ai_version = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=flow.artifact.id,
            parent_version_id=flow.version.id,
            content={"title": "ai v2"},
            created_by_user_id=flow.operator.id,
            media_qc_metadata={"ai_used": True},
        )
    )
    pass_run = GateRunnerService(db_session).run_gate(
        data=GateRunCreate(gate_key="rights_copyright_gate", target_type="artifact_version", target_id=flow.version.id)
    )
    review_run = GateRunnerService(db_session).run_gate(
        data=GateRunCreate(gate_key="ai_use_disclosure_gate", target_type="artifact_version", target_id=ai_version.id)
    )
    same_hash = GateRunnerService(db_session).run_gate(
        data=GateRunCreate(gate_key="ai_use_disclosure_gate", target_type="artifact_version", target_id=ai_version.id)
    ).input_snapshot_hash
    assert pass_run.result == "PASS"
    assert review_run.result == "REVIEW_REQUIRED"
    assert review_run.input_snapshot_hash == same_hash
    assert review_run.reason_codes
    assert review_run.freshness_state
    assert review_run.confidence_level
    assert review_run.decision_basis
    assert db_session.get(ReviewTask, review_run.created_review_task_id) is not None

    definition = GateDefinitionService(db_session).get_active_gate_version("publish_risk_gate")
    for result in ["BLOCK", "SKIPPED", "NOT_APPLICABLE"]:
        db_session.add(
            GateRun(
                gate_definition_version_id=definition.id,
                gate_key=f"manual_{result.lower()}",
                target_type="artifact_version",
                target_id=ai_version.id,
                video_project_id=flow.project.id,
                artifact_version_id=ai_version.id,
                policy_snapshot_id=flow.project.policy_snapshot_id,
                input_snapshot={"manual": result},
                input_snapshot_hash=f"manual-{result}",
                result=result,
                reason_codes=["MANUAL_REVIEW_REQUIRED"],
                freshness_state="UNKNOWN",
                confidence_level="HIGH",
                decision_basis={"manual_test": True},
            )
        )
    db_session.flush()
    readiness = WorkflowReadinessService(db_session).inspect_project(flow.project.id)
    assert readiness["status"] == "BLOCKED"
    assert readiness["blockers"]

    review_run.result = "PASS"
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()


def test_m3_policy_change_revalidation_is_append_only(db_session, qualification_factory) -> None:
    flow = qualification_factory.m2_project()
    first = GateRunnerService(db_session).run_gate(
        data=GateRunCreate(gate_key="rights_copyright_gate", target_type="artifact_version", target_id=flow.version.id)
    )
    change_service = PolicyChangeService(db_session)
    change = change_service.create_change_record(
        data=PolicyChangeRecordCreate(
            change_key=f"pre-m7-change-{flow.version.id}",
            platform="generic",
            policy_domain="legal_compliance",
            summary="Manual policy update",
        )
    )
    change_service.transition_state(change.id, "SOURCE_VERIFIED")
    change_service.transition_state(change.id, "DIFFED")
    with pytest.raises(ValidationFailureError):
        change_service.transition_state(change.id, "ACTIVE")
    batch = PolicyRevalidationService(db_session).create_batch(
        data=PolicyRevalidationBatchCreate(
            policy_change_record_id=change.id,
            scope={"targets": [{"target_type": "artifact_version", "target_id": str(flow.version.id), "gate_key": "rights_copyright_gate"}]},
        )
    )
    completed = PolicyRevalidationService(db_session).run_batch(batch.id)
    runs = db_session.scalars(select(GateRun).where(GateRun.gate_key == "rights_copyright_gate")).all()
    db_session.refresh(first)
    assert completed.status == "COMPLETED"
    assert completed.counts["created"] == 1
    assert len(runs) == 2
    assert first.input_snapshot_hash == runs[0].input_snapshot_hash
