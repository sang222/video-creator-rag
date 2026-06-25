from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.contracts.workflow import ApprovalDecisionCreate, ArtifactVersionCreate, ReviewFindingCreate, ReviewTaskCreate, RevisionRequestCreate
from app.core.errors import ForbiddenError, ValidationFailureError
from app.db.models import AuditEvent, DomainEvent
from app.services import ApprovalService, ArtifactService, ReviewService, VideoProjectService
from app.services.workflow import deterministic_artifact_content_hash


def test_m2_artifact_exact_version_lineage_immutability_and_review_flow(db_session, qualification_factory) -> None:
    flow = qualification_factory.m2_project()
    assert flow.version.content_hash == deterministic_artifact_content_hash({"title": "v1", "lines": ["hello"]})
    assert flow.version.source_manifest == {"rights_basis": "licensed"}
    assert flow.version.evidence_refs
    assert flow.version.context_refs
    assert flow.version.claim_refs

    review = ReviewService(db_session).create_review_task(
        data=ReviewTaskCreate(
            video_project_id=flow.project.id,
            target_type="artifact_version",
            target_id=flow.version.id,
            target_artifact_version_id=flow.version.id,
            review_type="editorial",
            requested_by_user_id=flow.operator.id,
            review_reason_codes=["VALIDATION_FAILED"],
            evidence_refs=[{"type": "manual", "id": "ev-1"}],
        )
    )
    with pytest.raises(ValidationFailureError):
        ReviewService(db_session).create_review_task(
            data=ReviewTaskCreate(
                video_project_id=flow.project.id,
                target_type="artifact_version",
                target_id=flow.artifact.id,
                target_artifact_version_id=flow.version.id,
                review_type="editorial",
                requested_by_user_id=flow.operator.id,
            )
        )
    finding = ReviewService(db_session).add_finding(
        data=ReviewFindingCreate(
            review_task_id=review.id,
            severity="medium",
            reason_code="VALIDATION_FAILED",
            finding_text="Needs revision",
            created_by_user_id=flow.operator.id,
        )
    )
    revision = ReviewService(db_session).create_revision_request(
        data=RevisionRequestCreate(
            review_task_id=review.id,
            target_artifact_version_id=flow.version.id,
            requested_by_user_id=flow.operator.id,
            reason="Fix finding",
        )
    )
    with pytest.raises(ValidationFailureError):
        ReviewService(db_session).resolve_revision_request(
            revision_request_id=revision.id,
            resolved_by_artifact_version_id=flow.version.id,
        )
    v2 = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=flow.artifact.id,
            parent_version_id=flow.version.id,
            content={"title": "v2"},
            created_by_user_id=flow.operator.id,
        )
    )
    resolved = ReviewService(db_session).resolve_revision_request(
        revision_request_id=revision.id,
        resolved_by_artifact_version_id=v2.id,
    )
    approval = ApprovalService(db_session).create_approval_decision(
        data=ApprovalDecisionCreate(
            target_type="artifact_version",
            target_id=flow.version.id,
            target_artifact_version_id=flow.version.id,
            decision="approved",
            decided_by_user_id=flow.admin.id,
        )
    )
    with pytest.raises(ForbiddenError):
        ApprovalService(db_session).create_approval_decision(
            data=ApprovalDecisionCreate(
                target_type="artifact_version",
                target_id=v2.id,
                target_artifact_version_id=v2.id,
                decision="approved",
                decided_by_user_id=flow.operator.id,
            )
        )
    assert finding.review_task_id == review.id
    assert resolved.resolved_by_artifact_version_id == v2.id
    assert ApprovalService(db_session).is_decision_stale_for_current_version(approval.id)
    state = VideoProjectService(db_session).inspect_workflow_state(flow.project.id)
    assert state["policy_snapshot_id"] == str(flow.snapshot.id)
    assert any(item["stale_for_current_artifact_version"] for item in state["approval_decisions"])

    flow.version.content = {"mutated": True}
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()


def test_m2_workflow_writes_events_and_bad_payload_rolls_back(db_session, qualification_factory) -> None:
    flow = qualification_factory.m2_project()
    db_session.commit()
    before_events = (db_session.query(AuditEvent).count(), db_session.query(DomainEvent).count())
    with pytest.raises(Exception):
        ArtifactService(db_session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=flow.artifact.id,
                parent_version_id=flow.version.id,
                content={"title": "bad"},
                source_manifest={"raw_vendor_payload": {"forbidden": True}},
                created_by_user_id=flow.operator.id,
            )
        )
    db_session.rollback()
    assert db_session.query(AuditEvent).count() == before_events[0]
    assert db_session.query(DomainEvent).count() == before_events[1]
    event_types = set(db_session.scalars(select(DomainEvent.event_type)).all())
    assert {"video_project.created", "artifact.created", "artifact_version.created"} <= event_types
