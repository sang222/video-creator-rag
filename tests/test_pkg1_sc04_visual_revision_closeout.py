from __future__ import annotations

import uuid
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.contracts.pkg1_sc04_revision_closeout import (
    PKG1SC04RevisionApprovalCommand,
)
from app.core.errors import ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ReviewTask,
    VideoProject,
)
from app.services.pkg1_sc04_revision import PKG1SC04RevisionService
from app.services.pkg1_sc04_revision_closeout import (
    APPROVAL_SCOPE,
    HUMAN_RECEIPT_ARTIFACT_STATUS,
    HUMAN_RECEIPT_VERSION_STATUS,
    RECEIPT_ARTIFACT_TYPE,
    PKG1SC04RevisionCloseoutService,
)
from tests.test_pkg1_sc04_visual_revision import _scope


def _pending(db_session, tmp_path):
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    pending = PKG1SC04RevisionService(db_session).build_revision(
        channel_id=source.channel_workspace_id,
        created_by_user_id=actor_id,
        ads_only_overlay_artifact_version_id=overlay.id,
        ads_only_overlay_content_hash=overlay.content_hash,
        geo_closeout_artifact_version_id=closeout.id,
        geo_closeout_content_hash=closeout.content_hash,
    )
    approval_ref = (
        "operator-approval://pkg1-sc04-revision/"
        f"{pending['revision_id']}/v{pending['revision_version']}/"
        f"{pending['revision_hash']}/package/"
        f"{pending['package_artifact_version_id']}/"
        f"{pending['package_content_hash']}"
    )
    command = PKG1SC04RevisionApprovalCommand(
        project_id=uuid.UUID(pending["video_project_id"]),
        review_task_id=uuid.UUID(pending["human_review_task_ids"][0]),
        reviewed_package_artifact_version_id=uuid.UUID(
            pending["package_artifact_version_id"]
        ),
        reviewed_package_hash=pending["package_content_hash"],
        reviewed_revision_id=uuid.UUID(pending["revision_id"]),
        reviewed_revision_version=pending["revision_version"],
        reviewed_revision_hash=pending["revision_hash"],
        decided_by_user_id=actor_id,
        decision="PASS",
        decision_source="OPERATOR",
        review_authority="HUMAN",
        operator_decision_text="PASS",
        approval_ref=approval_ref,
    )
    return pending, command


def test_sc04_human_closeout_is_exact_immutable_and_execution_free(
    db_session, tmp_path
) -> None:
    pending, command = _pending(db_session, tmp_path)
    package = db_session.get(
        ArtifactVersion, command.reviewed_package_artifact_version_id
    )
    assert package is not None
    package_before = deepcopy(package.content)
    package_hash_before = package.content_hash
    service = PKG1SC04RevisionCloseoutService(db_session)
    result = service.closeout(command)

    assert result["PKG1_SC04_REVISION_HUMAN_REVIEW"] == "PASS"
    assert result["PKG1_SC04_REVISION_FINAL"] == "PASS"
    assert result["PRODUCTION_PACKAGE_APPROVED"] is True
    assert result["MR1_EXECUTION"] == ("BLOCKED_REQUIRES_FRESH_SC04_PACKAGE_REAPPROVAL")
    assert result["PROCEED_TO_MR1_REAPPROVAL"] is True
    assert result["PROCEED_TO_MR1"] is False
    assert result["provider_calls"] == result["render_calls"] == 0
    assert result["drive_calls"] == result["youtube_calls"] == 0
    assert result["no_execution_counts_before"] == result["no_execution_counts_after"]

    package_after = db_session.get(ArtifactVersion, package.id)
    assert package_after is not None
    assert package_after.content == package_before
    assert package_after.content_hash == package_hash_before
    package_artifact = db_session.get(Artifact, package.artifact_id)
    project = db_session.get(VideoProject, command.project_id)
    review = db_session.get(ReviewTask, command.review_task_id)
    assert package_artifact is not None and package_artifact.status == "approved"
    assert project is not None and project.status == "approved"
    assert review is not None and review.status == "completed"

    approval = db_session.get(
        ApprovalDecision, uuid.UUID(result["approval_decision_id"])
    )
    assert approval is not None
    assert approval.target_artifact_version_id == package.id
    assert approval.approved_package_hash == package.content_hash
    assert approval.metadata_["approval_scope"] == APPROVAL_SCOPE
    assert (
        approval.metadata_["effective_market_policy_hash"]
        == pending["package"]["effective_market_policy_hash"]
    )
    composite = pending["package"]["effective_artifact_authority"][
        "composite_market_alignment_authority"
    ]
    assert approval.market_alignment_dossier_ref == composite["ref"]
    assert approval.market_alignment_dossier_hash == composite["content_hash"]
    assert approval.destination_binding_id is not None
    assert approval.destination_binding_fingerprint
    assert approval.approved_publish_window
    assert approval.policy_basis["market_alignment_dossier"] == {
        "ref": composite["ref"],
        "content_hash": composite["content_hash"],
    }

    receipt = db_session.get(
        ArtifactVersion,
        uuid.UUID(result["human_review_receipt_artifact_version_id"]),
    )
    assert receipt is not None
    receipt_artifact = db_session.get(Artifact, receipt.artifact_id)
    assert receipt.status == HUMAN_RECEIPT_VERSION_STATUS == "submitted"
    assert receipt_artifact is not None
    assert receipt_artifact.status == HUMAN_RECEIPT_ARTIFACT_STATUS == "approved"
    assert receipt.content["schema_version"] == ("pkg1.sc04-human-review-receipt.v1")
    assert receipt.content["decided_by_user_id"] == str(command.decided_by_user_id)
    scopes = {
        item["approval_scope"] for item in receipt.content["superseded_mr1_approvals"]
    }
    assert {
        "MR1_REAL_PRODUCTION_EXECUTION",
        "MR1_EXACT_PROVIDER_ATTEMPT_CONTINUATION",
    } <= scopes
    assert all(
        item["reuse_allowed"] is False and item["historical_receipt_mutated"] is False
        for item in receipt.content["superseded_mr1_approvals"]
    )
    assert receipt.content["no_execution_proof"]["all_deltas_zero"] is True

    counts = {
        "receipt": db_session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(
                Artifact.video_project_id == command.project_id,
                Artifact.artifact_type == RECEIPT_ARTIFACT_TYPE,
            )
        ),
        "approval": db_session.scalar(
            select(func.count())
            .select_from(ApprovalDecision)
            .where(
                ApprovalDecision.target_artifact_version_id == package.id,
                ApprovalDecision.metadata_["approval_scope"].astext == APPROVAL_SCOPE,
            )
        ),
    }
    rerun = service.closeout(command)
    assert rerun["approval_decision_id"] == result["approval_decision_id"]
    assert counts == {
        "receipt": db_session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(
                Artifact.video_project_id == command.project_id,
                Artifact.artifact_type == RECEIPT_ARTIFACT_TYPE,
            )
        ),
        "approval": db_session.scalar(
            select(func.count())
            .select_from(ApprovalDecision)
            .where(
                ApprovalDecision.target_artifact_version_id == package.id,
                ApprovalDecision.metadata_["approval_scope"].astext == APPROVAL_SCOPE,
            )
        ),
    }

    original_evidence_refs = deepcopy(receipt.evidence_refs)
    receipt.evidence_refs = []
    with pytest.raises(
        ValidationFailureError,
        match="PKG1_SC04_CLOSEOUT_RECEIPT_PROVENANCE_INVALID",
    ):
        service.closeout(command)
    receipt.evidence_refs = original_evidence_refs

    conflicting_actor = command.model_copy(update={"decided_by_user_id": uuid.uuid4()})
    with pytest.raises(
        ValidationFailureError,
        match="SC04_EXISTING_CLOSEOUT_COMMAND_MISMATCH",
    ):
        service.closeout(conflicting_actor)

    conflicting_notes = command.model_copy(
        update={"review_notes": "Different replay authority."}
    )
    with pytest.raises(
        ValidationFailureError,
        match="SC04_EXISTING_CLOSEOUT_COMMAND_MISMATCH",
    ):
        service.closeout(conflicting_notes)

    # Historical closeout authority must survive legitimate later execution.
    service.revision_service.source_service._no_execution_counts = lambda: {
        key: value + 1 for key, value in result["no_execution_counts_after"].items()
    }
    historical_read = service.read_closeout(command.project_id)
    assert historical_read["approval_decision_id"] == result["approval_decision_id"]


def test_sc04_human_closeout_hash_mismatch_creates_no_partial_authority(
    db_session, tmp_path
) -> None:
    pending, command = _pending(db_session, tmp_path)
    mismatched = command.model_copy(update={"reviewed_revision_hash": "0" * 64})
    with pytest.raises(
        ValidationFailureError,
        match="SC04_REVIEWED_REVISION_IDENTITY_MISMATCH",
    ):
        PKG1SC04RevisionCloseoutService(db_session).closeout(mismatched)
    package_id = uuid.UUID(pending["package_artifact_version_id"])
    assert not [
        item
        for item in db_session.scalars(
            select(ApprovalDecision).where(
                ApprovalDecision.target_artifact_version_id == package_id
            )
        ).all()
        if (item.metadata_ or {}).get("approval_scope") == APPROVAL_SCOPE
    ]


def test_sc04_human_closeout_rejects_superseded_source_authority(
    db_session, tmp_path
) -> None:
    pending, command = _pending(db_session, tmp_path)
    source_package_ref = pending["package"]["source_human_authority"][
        "approved_package"
    ]
    source_package_artifact = db_session.get(
        Artifact, uuid.UUID(source_package_ref["artifact_id"])
    )
    assert source_package_artifact is not None
    source_package_artifact.status = "superseded"

    with pytest.raises(
        ValidationFailureError,
        match="PKG1_SC04_EXACT_SOURCE_PACKAGE_INVALID",
    ):
        PKG1SC04RevisionCloseoutService(db_session).closeout(command)


def test_sc04_human_closeout_replay_revalidates_source_authority(
    db_session, tmp_path
) -> None:
    pending, command = _pending(db_session, tmp_path)
    service = PKG1SC04RevisionCloseoutService(db_session)
    service.closeout(command)
    source_package_ref = pending["package"]["source_human_authority"][
        "approved_package"
    ]
    source_package_artifact = db_session.get(
        Artifact, uuid.UUID(source_package_ref["artifact_id"])
    )
    assert source_package_artifact is not None
    source_package_artifact.status = "superseded"

    with pytest.raises(
        ValidationFailureError,
        match="PKG1_SC04_EXACT_SOURCE_PACKAGE_INVALID",
    ):
        service.closeout(command)


def test_sc04_human_closeout_replay_revalidates_geo_authority(
    db_session, tmp_path
) -> None:
    pending, command = _pending(db_session, tmp_path)
    service = PKG1SC04RevisionCloseoutService(db_session)
    service.closeout(command)
    geo_ref = pending["package"]["geo_market_delivery_closeout_evidence"]
    geo_artifact = db_session.get(Artifact, uuid.UUID(geo_ref["artifact_id"]))
    assert geo_artifact is not None
    geo_artifact.current_version_id = None

    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_EVIDENCE_BINDING_INVALID",
    ):
        service.closeout(command)
