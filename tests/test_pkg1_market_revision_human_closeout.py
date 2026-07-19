from __future__ import annotations

import uuid
from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.contracts.pkg1_market_revision_closeout import (
    PKG1MarketRevisionApprovalCommand,
)
from app.core.errors import ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ReviewTask,
    VideoProject,
)
from app.services.pkg1_market_revision import PKG1MarketRevisionService
from app.services.pkg1_market_revision_closeout import (
    APPROVAL_SCOPE,
    PKG1MarketRevisionCloseoutService,
    READ_MODEL_ARTIFACT_TYPE,
    RECEIPT_ARTIFACT_TYPE,
)
from tests.test_pkg1_market_revision_v3 import (
    _historical_state,
    _revision_scope,
)


def _pending_revision(db_session, tmp_path):
    _, operator, channel, historical, _, _, reports = _revision_scope(
        db_session, tmp_path
    )
    revision_service = PKG1MarketRevisionService(
        db_session, report_paths=reports
    )
    pending = revision_service.build_revision(
        channel_id=channel.id,
        created_by_user_id=operator.id,
    )
    command = PKG1MarketRevisionApprovalCommand(
        project_id=uuid.UUID(pending["video_project_id"]),
        review_task_id=uuid.UUID(pending["human_review_task_ids"][0]),
        reviewed_package_artifact_version_id=uuid.UUID(
            pending["package_artifact_version_id"]
        ),
        reviewed_package_hash=pending["package_content_hash"],
        reviewed_revision_id=uuid.UUID(pending["revision_id"]),
        reviewed_revision_version=pending["revision_version"],
        reviewed_revision_hash=pending["revision_hash"],
        decided_by_user_id=operator.id,
        decision="PASS",
        decision_source="OPERATOR",
        review_authority="HUMAN",
        operator_decision_text="PASS",
        approval_ref=(
            "operator-approval://pkg1-market-revision/"
            f"{pending['revision_id']}/v{pending['revision_version']}/"
            f"{pending['revision_hash']}/package/"
            f"{pending['package_artifact_version_id']}/"
            f"{pending['package_content_hash']}"
        ),
    )
    return historical, pending, command, revision_service


def test_human_closeout_binds_exact_package_and_preserves_boundaries(
    db_session, tmp_path
) -> None:
    historical, pending, command, revision_service = _pending_revision(
        db_session, tmp_path
    )
    project_id = uuid.UUID(pending["video_project_id"])
    package_id = uuid.UUID(pending["package_artifact_version_id"])
    package = db_session.get(ArtifactVersion, package_id)
    assert package is not None
    package_content_before = deepcopy(package.content)
    package_hash_before = package.content_hash
    historical_before = _historical_state(
        db_session, historical.video_project_id
    )
    old_mr1_id = uuid.UUID(
        pending["package"]["old_mr1_approval"]["approval_decision_id"]
    )
    old_mr1 = db_session.get(ApprovalDecision, old_mr1_id)
    assert old_mr1 is not None
    old_mr1_before = {
        "decision": old_mr1.decision,
        "metadata": deepcopy(old_mr1.metadata_),
        "evidence": deepcopy(old_mr1.evidence_basis),
        "policy": deepcopy(old_mr1.policy_basis),
    }
    no_execution_before = revision_service._no_execution_counts()

    closeout_service = PKG1MarketRevisionCloseoutService(db_session)
    result = closeout_service.closeout(command)

    assert result["PKG1_MARKET_REVISION_HUMAN_REVIEW"] == "PASS"
    assert result["PKG1_MARKET_REVISION_FINAL"] == "PASS"
    assert result["PRODUCTION_PACKAGE_APPROVED"] is True
    assert result["FINAL_MARKET_PACKAGE_PENDING_MEDIA"] is True
    assert result["UPLOAD_READY"] is False
    assert result["PUBLISH_EXECUTION_READY"] is False
    assert result["destination_status"] == "PENDING_PLATFORM_ID"
    assert result["publish_blocker_reason_code"] == (
        "DESTINATION_PLATFORM_ID_NOT_VERIFIED"
    )
    assert result["MR1_REAPPROVAL_ENTRY"] == "READY"
    assert result["MR1_EXECUTION"] == "NOT_STARTED"
    assert result["MR1_PROVIDER_CALL_COUNT"] == 0
    assert result["MR1_RENDER_STATUS"] == "NOT_STARTED"
    assert result["MR1_HUMAN_REVIEW"] == "PENDING"
    assert result["PROCEED_TO_MR1_REAPPROVAL"] is True
    assert result["PROCEED_TO_MR1"] is False
    assert result["provider_calls"] == 0
    assert result["render_calls"] == 0
    assert result["drive_calls"] == 0
    assert result["youtube_calls"] == 0
    assert result["no_execution_counts_before"] == no_execution_before
    assert result["no_execution_counts_after"] == no_execution_before

    approval = db_session.get(
        ApprovalDecision, uuid.UUID(result["approval_decision_id"])
    )
    assert approval is not None
    assert approval.target_artifact_version_id == package_id
    assert approval.decided_by_user_id == command.decided_by_user_id
    assert approval.metadata_["approval_scope"] == APPROVAL_SCOPE
    assert approval.metadata_["decision_source"] == "OPERATOR"
    assert approval.metadata_["review_authority"] == "HUMAN"
    assert approval.metadata_["operator_decision_text"] == "PASS"
    assert approval.metadata_["revision_hash"] == pending["revision_hash"]
    assert approval.metadata_["package_content_hash"] == package_hash_before
    assert approval.decision_basis["MR1_EXECUTION"] == "NOT_STARTED"
    assert approval.decision_basis["PROCEED_TO_MR1"] is False
    assert len(approval.evidence_basis["reviewed_artifacts"]) == 36
    for binding_key in (
        "channel_profile_version",
        "compiled_channel_policy_snapshot",
        "target_market_profile",
        "target_market_digest",
        "destination_binding",
    ):
        assert approval.evidence_basis["exact_bindings"][binding_key] == (
            pending["package"]["exact_bindings"][binding_key]
        )
    market_dossier = approval.evidence_basis["reviewed_artifacts"][
        "market_alignment_dossier"
    ]
    reviewed_market_dossier = pending["package"]["revised_artifacts"][
        "market_alignment_dossier"
    ]
    for ref_key in (
        "artifact_id",
        "artifact_version_id",
        "version_number",
        "content_hash",
    ):
        assert market_dossier[ref_key] == reviewed_market_dossier[ref_key]
    assert approval.evidence_basis["exact_bindings"]["destination_binding"][
        "destination_status"
    ] == "PENDING_PLATFORM_ID"

    receipt = db_session.get(
        ArtifactVersion,
        uuid.UUID(result["human_review_receipt_artifact_version_id"]),
    )
    readiness = db_session.get(
        ArtifactVersion,
        uuid.UUID(result["mr1_readiness_artifact_version_id"]),
    )
    read_model = db_session.get(
        ArtifactVersion,
        uuid.UUID(result["read_model_artifact_version_id"]),
    )
    assert receipt is not None and readiness is not None and read_model is not None
    assert receipt.content["decision_source"] == "OPERATOR"
    assert receipt.content["review_authority"] == "HUMAN"
    assert receipt.content["operator_decision_text"] == "PASS"
    assert receipt.content["approval_decision_id"] == str(approval.id)
    assert receipt.content["reviewed_package"]["content_hash"] == package_hash_before
    assert receipt.content["historical_lineage"]["old_mr1_approval"] == (
        "SUPERSEDED_BY_PKG1_MARKET_REVISION"
    )
    assert readiness.content["MR1_REAPPROVAL_ENTRY"] == "READY"
    assert readiness.content["MR1_EXECUTION"] == "NOT_STARTED"
    assert readiness.content["MR1_PROVIDER_CALL_COUNT"] == 0
    assert readiness.content["PROCEED_TO_MR1_REAPPROVAL"] is True
    assert readiness.content["PROCEED_TO_MR1"] is False
    assert read_model.content["PRODUCTION_PACKAGE_APPROVED"] is True
    assert read_model.content["revision_status"] == "APPROVED"
    assert read_model.content["destination_verification"] == (
        "PENDING_PLATFORM_ID"
    )
    assert read_model.content["publish_readiness"] == "NOT_READY"
    assert read_model.content["UPLOAD_READY"] is False

    review = db_session.get(ReviewTask, command.review_task_id)
    project = db_session.get(VideoProject, project_id)
    assert review is not None and review.status == "completed"
    assert project is not None and project.status == "approved"
    package_after = db_session.get(ArtifactVersion, package_id)
    assert package_after is not None
    assert package_after.content == package_content_before
    assert package_after.content_hash == package_hash_before
    assert package_after.content["PKG1_MARKET_REVISION_HUMAN_REVIEW"] == (
        "PENDING"
    )
    assert _historical_state(
        db_session, historical.video_project_id
    ) == historical_before
    old_mr1_after = db_session.get(ApprovalDecision, old_mr1_id)
    assert old_mr1_after is not None
    assert {
        "decision": old_mr1_after.decision,
        "metadata": old_mr1_after.metadata_,
        "evidence": old_mr1_after.evidence_basis,
        "policy": old_mr1_after.policy_basis,
    } == old_mr1_before
    revision_version_ids = list(
        db_session.scalars(
            select(ArtifactVersion.id)
            .join(Artifact, ArtifactVersion.artifact_id == Artifact.id)
            .where(Artifact.video_project_id == project_id)
        ).all()
    )
    assert not list(
        db_session.scalars(
            select(ApprovalDecision).where(
                ApprovalDecision.target_artifact_version_id.in_(
                    revision_version_ids
                ),
                ApprovalDecision.metadata_["approval_scope"].astext
                == "MR1_PAID_EXECUTION",
            )
        ).all()
    )

    effective = revision_service.read_revision(project_id)
    assert effective["human_review_state"] == "PASS"
    assert effective["final_state"] == "PASS"
    assert effective["effective_state"]["PRODUCTION_PACKAGE_APPROVED"] is True
    assert effective["effective_state"]["UPLOAD_READY"] is False
    assert effective["effective_state"]["MR1_REAPPROVAL_ENTRY"] == "READY"
    assert effective["package"]["PKG1_MARKET_REVISION_HUMAN_REVIEW"] == (
        "PENDING"
    )

    counts_before_rerun = {
        "artifacts": db_session.scalar(
            select(func.count()).select_from(Artifact).where(
                Artifact.video_project_id == project_id
            )
        ),
        "approvals": db_session.scalar(
            select(func.count()).select_from(ApprovalDecision).where(
                ApprovalDecision.target_artifact_version_id == package_id
            )
        ),
    }
    second = closeout_service.closeout(command)
    assert second["approval_decision_id"] == result["approval_decision_id"]
    assert second["human_review_receipt_content_hash"] == result[
        "human_review_receipt_content_hash"
    ]
    assert counts_before_rerun == {
        "artifacts": db_session.scalar(
            select(func.count()).select_from(Artifact).where(
                Artifact.video_project_id == project_id
            )
        ),
        "approvals": db_session.scalar(
            select(func.count()).select_from(ApprovalDecision).where(
                ApprovalDecision.target_artifact_version_id == package_id
            )
        ),
    }


def test_hash_mismatch_prevents_closeout_without_partial_approval(
    db_session, tmp_path
) -> None:
    _, pending, command, _ = _pending_revision(db_session, tmp_path)
    mismatched = command.model_copy(
        update={"reviewed_revision_hash": "0" * 64}
    )

    with pytest.raises(
        ValidationFailureError,
        match="REVIEWED_REVISION_IDENTITY_MISMATCH",
    ):
        PKG1MarketRevisionCloseoutService(db_session).closeout(mismatched)

    package_id = uuid.UUID(pending["package_artifact_version_id"])
    assert not list(
        db_session.scalars(
            select(ApprovalDecision).where(
                ApprovalDecision.target_artifact_version_id == package_id
            )
        ).all()
    )
    review = db_session.get(ReviewTask, command.review_task_id)
    project = db_session.get(VideoProject, command.project_id)
    assert review is not None and review.status == "open"
    assert project is not None and project.status == "in_review"
    assert not list(
        db_session.scalars(
            select(Artifact).where(
                Artifact.video_project_id == command.project_id,
                Artifact.artifact_type.in_(
                    [RECEIPT_ARTIFACT_TYPE, READ_MODEL_ARTIFACT_TYPE]
                ),
            )
        ).all()
    )


def test_closeout_contract_rejects_codex_as_human_authority() -> None:
    payload = {
        "project_id": str(uuid.uuid4()),
        "review_task_id": str(uuid.uuid4()),
        "reviewed_package_artifact_version_id": str(uuid.uuid4()),
        "reviewed_package_hash": "1" * 64,
        "reviewed_revision_id": str(uuid.uuid4()),
        "reviewed_revision_version": 2,
        "reviewed_revision_hash": "2" * 64,
        "decided_by_user_id": str(uuid.uuid4()),
        "decision": "PASS",
        "decision_source": "CODEX",
        "review_authority": "AI",
        "operator_decision_text": "PASS",
        "approval_ref": "operator-approval://pkg1-market-revision/test",
    }
    with pytest.raises(ValidationError):
        PKG1MarketRevisionApprovalCommand.model_validate(payload)
