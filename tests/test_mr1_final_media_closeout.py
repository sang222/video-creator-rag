from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.contracts.mr1 import MR1FinalMediaCloseoutCommand
from app.core.errors import ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    CloudMediaRef,
    FinalMediaRef,
    HumanUploadTask,
    ReviewTask,
    UploadedVideo,
)
from app.services.mr1_real_production import MR1RealProductionService
from app.services.native_render_plan import stable_hash
from tests.test_mr1_real_production_execution import (
    DRIVE_IDEMPOTENCY_PHASES,
    FakeDriveGateway,
    FakeLocalContinuation,
    _approved_mr1,
    _gateways,
)


DRIVE_RECEIPT_HASH_KEYS = (
    "schema_version",
    "run_id",
    "archive_identity",
    "archive_manifest_hash",
    "root_relative_path",
    "drive_folder_id",
    "expected_item_count",
    "verified_item_count",
    "remote_item_count",
    "total_local_size_bytes",
    "total_remote_size_bytes",
    "items",
    "files",
    "remote_exact_set_verified",
    "archive_state",
    "mismatch_reason_codes",
    "provider_call_made",
    "transport",
    "verified_at",
)


@dataclass(frozen=True)
class _CloseoutScope:
    project_id: uuid.UUID
    operator_id: uuid.UUID
    run_id: uuid.UUID
    candidate: ArtifactVersion
    drive_receipt: ArtifactVersion
    archive_identity: str
    output_path: Path
    output_sha256: str
    review_task_id: uuid.UUID
    drive_gateway: FakeDriveGateway


def _scope(
    db_session,
    tmp_path: Path,
    *,
    technical_qc_result: str = "PASS",
    creative_review_result: str = "REVIEW_REQUIRED",
    archive_verified: bool = True,
    archive_verification_overrides: dict[str, bool] | None = None,
    run_state: str = "AWAITING_HUMAN_FULL_WATCH",
) -> _CloseoutScope:
    approved = _approved_mr1(db_session, tmp_path)
    gateways, fakes = _gateways()
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    )
    pending = service.start(approved["command"], gateways=gateways)
    approval = approved["approval"]
    approval_decision = db_session.get(
        ApprovalDecision, uuid.UUID(approval["approval_decision_id"])
    )
    assert approval_decision is not None

    project_id = approved["command"].project_id
    run_id = uuid.UUID(pending["run_id"])
    candidate = _current_artifact_version(
        db_session, project_id, "mr1_review_media_candidate"
    )
    drive_receipt = _current_artifact_version(
        db_session, project_id, "mr1_drive_archive_receipt"
    )
    run_artifact = list(
        db_session.scalars(
            select(Artifact).where(
                Artifact.video_project_id == project_id,
                Artifact.artifact_type == "mr1_execution_run",
            )
        ).all()
    )
    assert len(run_artifact) == 1
    assert run_artifact[0].current_version_id is not None
    run_version = db_session.get(ArtifactVersion, run_artifact[0].current_version_id)
    assert run_version is not None
    state = deepcopy(run_version.content or {})

    candidate_changed = (
        technical_qc_result != "PASS" or creative_review_result != "REVIEW_REQUIRED"
    )
    if candidate_changed:
        candidate_payload = deepcopy(candidate.content or {})
        candidate_payload["technical_media_qc"] = technical_qc_result
        candidate_payload["technical_qc_result"] = technical_qc_result
        candidate_payload["creative_media_qc"] = creative_review_result
        candidate_payload["creative_review_result"] = creative_review_result
        candidate_payload.pop("content_hash", None)
        candidate_payload["content_hash"] = stable_hash(candidate_payload)
        candidate = service._create_version_on_existing_artifact(
            existing=candidate,
            content=candidate_payload,
            actor_id=approval_decision.decided_by_user_id,
            correlation_id=f"test-mr1-invalid-candidate-{run_id}",
        )
        state["review_media_candidate"] = {
            **candidate_payload,
            "artifact_version_id": str(candidate.id),
            "content_hash": candidate.content_hash,
        }

    drive_changed = not archive_verified or bool(archive_verification_overrides)
    if drive_changed:
        drive_payload = deepcopy(drive_receipt.content or {})
        verification = deepcopy(drive_payload.get("verification") or {})
        if not archive_verified:
            verification = {key: False for key in verification}
        verification.update(archive_verification_overrides or {})
        drive_payload["verification"] = verification
        drive_payload["remote_exact_set_verified"] = verification.get(
            "exact_item_set", False
        )
        drive_payload["parent_verified"] = verification.get("correct_parent", False)
        drive_payload["names_verified"] = verification.get("correct_names", False)
        drive_payload["sizes_verified"] = verification.get("size_verified", False)
        drive_payload["checksums_verified"] = verification.get(
            "checksum_readback_verified", False
        )
        drive_payload["duplicate_count"] = (
            0 if verification.get("duplicate_absence") is True else 1
        )
        drive_payload["status"] = "VERIFIED" if archive_verified else "FAILED"
        drive_payload["archive_state"] = "VERIFIED" if archive_verified else "FAILED"
        drive_payload["ARCHIVE_VERIFIED"] = archive_verified
        if not archive_verified:
            expected_count = int(drive_payload["expected_item_count"])
            drive_payload["actual_item_count"] = expected_count - 1
            drive_payload["verified_item_count"] = expected_count - 1
            drive_payload["mismatch_reason_codes"] = [
                "MR1_DRIVE_EXACT_SET_COUNT_MISMATCH"
            ]
        drive_receipt = service._create_version_on_existing_artifact(
            existing=drive_receipt,
            content=drive_payload,
            actor_id=approval_decision.decided_by_user_id,
            correlation_id=f"test-mr1-invalid-drive-{run_id}",
        )
        state["drive_archive"] = {
            **drive_payload,
            "artifact_version_id": str(drive_receipt.id),
            "content_hash": drive_receipt.content_hash,
        }

    if run_state != "AWAITING_HUMAN_FULL_WATCH":
        state["current_state"] = run_state
        state["state"] = run_state
    if candidate_changed or drive_changed or run_state != "AWAITING_HUMAN_FULL_WATCH":
        service._save_run(
            run_artifact[0],
            state,
            actor_id=approval_decision.decided_by_user_id,
        )

    output_path = Path(str((candidate.content or {})["output_file_ref"]))
    output_sha256 = str((candidate.content or {})["output_sha256"])
    archive_identity = str((drive_receipt.content or {})["archive_identity"])
    review_task_id = uuid.UUID(str(state["final_human_review_task_id"]))
    return _CloseoutScope(
        project_id=project_id,
        operator_id=approval_decision.decided_by_user_id,
        run_id=run_id,
        candidate=candidate,
        drive_receipt=drive_receipt,
        archive_identity=archive_identity,
        output_path=output_path,
        output_sha256=output_sha256,
        review_task_id=review_task_id,
        drive_gateway=fakes["drive"],
    )


def _command(
    scope: _CloseoutScope,
    *,
    decision: str = "PASS",
    operator_decision_text: str = "PASS",
    **changes,
) -> MR1FinalMediaCloseoutCommand:
    values = {
        "run_id": scope.run_id,
        "project_id": scope.project_id,
        "review_media_candidate_artifact_version_id": scope.candidate.id,
        "review_media_candidate_content_hash": scope.candidate.content_hash,
        "reviewed_output_sha256": scope.output_sha256,
        "drive_archive_receipt_artifact_version_id": scope.drive_receipt.id,
        "drive_archive_receipt_content_hash": scope.drive_receipt.content_hash,
        "archive_identity": scope.archive_identity,
        "decided_by_user_id": scope.operator_id,
        "decision": decision,
        "decision_source": "OPERATOR",
        "review_authority": "HUMAN",
        "operator_decision_text": operator_decision_text,
    }
    values.update(changes)
    return MR1FinalMediaCloseoutCommand(**values)


def _final_count(db_session, project_id: uuid.UUID) -> int:
    return int(
        db_session.scalar(
            select(func.count())
            .select_from(FinalMediaRef)
            .where(FinalMediaRef.video_project_id == project_id)
        )
        or 0
    )


def _upload_counts(db_session) -> dict[str, int]:
    return {
        "human_upload_tasks": int(
            db_session.scalar(select(func.count()).select_from(HumanUploadTask)) or 0
        ),
        "uploaded_videos": int(
            db_session.scalar(select(func.count()).select_from(UploadedVideo)) or 0
        ),
    }


def _human_receipts(db_session, project_id: uuid.UUID) -> list[ArtifactVersion]:
    return list(
        db_session.scalars(
            select(ArtifactVersion)
            .join(Artifact, ArtifactVersion.artifact_id == Artifact.id)
            .where(
                Artifact.video_project_id == project_id,
                Artifact.artifact_type == "mr1_human_full_watch_receipt",
            )
        ).all()
    )


def _current_artifact_version(
    db_session, project_id: uuid.UUID, artifact_type: str
) -> ArtifactVersion:
    artifacts = list(
        db_session.scalars(
            select(Artifact).where(
                Artifact.video_project_id == project_id,
                Artifact.artifact_type == artifact_type,
            )
        ).all()
    )
    assert len(artifacts) == 1
    assert artifacts[0].current_version_id is not None
    version = db_session.get(ArtifactVersion, artifacts[0].current_version_id)
    assert version is not None
    return version


def _artifact_count(db_session, project_id: uuid.UUID, artifact_type: str) -> int:
    return int(
        db_session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(
                Artifact.video_project_id == project_id,
                Artifact.artifact_type == artifact_type,
            )
        )
        or 0
    )


def _recompute_drive_receipt_hash(receipt: dict) -> str:
    return stable_hash({key: receipt.get(key) for key in DRIVE_RECEIPT_HASH_KEYS})


@pytest.mark.parametrize(
    "authority_change",
    (
        {"decision_source": "CODEX"},
        {"review_authority": "AI"},
    ),
)
def test_closeout_contract_cannot_auto_pass_as_human(authority_change) -> None:
    values = {
        "run_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "review_media_candidate_artifact_version_id": uuid.uuid4(),
        "review_media_candidate_content_hash": "1" * 64,
        "reviewed_output_sha256": "2" * 64,
        "drive_archive_receipt_artifact_version_id": uuid.uuid4(),
        "drive_archive_receipt_content_hash": "3" * 64,
        "archive_identity": "drive-archive://small-team-ai/mr1/test",
        "decided_by_user_id": uuid.uuid4(),
        "decision": "PASS",
        "decision_source": "OPERATOR",
        "review_authority": "HUMAN",
        "operator_decision_text": "PASS",
    }
    values.update(authority_change)

    with pytest.raises(ValidationError):
        MR1FinalMediaCloseoutCommand(**values)


@pytest.mark.parametrize(
    ("decision", "operator_decision_text"),
    (
        ("PASS", "looks good"),
        ("PASS", "pass"),
        ("REJECT", "caption readability needs repair"),
        ("REJECT", "REJECT"),
    ),
)
def test_closeout_contract_requires_literal_human_decision_semantics(
    decision: str, operator_decision_text: str
) -> None:
    with pytest.raises(ValidationError):
        MR1FinalMediaCloseoutCommand(
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            review_media_candidate_artifact_version_id=uuid.uuid4(),
            review_media_candidate_content_hash="1" * 64,
            reviewed_output_sha256="2" * 64,
            drive_archive_receipt_artifact_version_id=uuid.uuid4(),
            drive_archive_receipt_content_hash="3" * 64,
            archive_identity="drive-archive://small-team-ai/mr1/test",
            decided_by_user_id=uuid.uuid4(),
            decision=decision,
            decision_source="OPERATOR",
            review_authority="HUMAN",
            operator_decision_text=operator_decision_text,
        )


def test_final_media_ref_does_not_exist_before_exact_human_pass(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path, archive_verified=False)
    before_upload = _upload_counts(db_session)

    assert _final_count(db_session, scope.project_id) == 0
    assert _human_receipts(db_session, scope.project_id) == []
    with pytest.raises(
        ValidationFailureError,
        match=(
            "MR1_CANONICAL_DRIVE_RECEIPT_PROOF_INVALID:"
            "MR1_DRIVE_ARCHIVE_NOT_VERIFIED"
        ),
    ):
        MR1RealProductionService(db_session, workspace_root=tmp_path).closeout(
            _command(scope), drive_gateway=scope.drive_gateway
        )

    assert _final_count(db_session, scope.project_id) == 0
    assert _human_receipts(db_session, scope.project_id) == []
    assert _upload_counts(db_session) == before_upload


def test_pass_requires_live_finalization_drive_gateway_before_human_receipt(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    task = db_session.get(ReviewTask, scope.review_task_id)
    assert task is not None
    assert task.status == "open"

    with pytest.raises(
        ValidationFailureError,
        match="FINALIZATION_DRIVE_GATEWAY_REQUIRED",
    ):
        MR1RealProductionService(db_session, workspace_root=tmp_path).closeout(
            _command(scope)
        )

    db_session.refresh(task)
    assert task.status == "open"
    assert _human_receipts(db_session, scope.project_id) == []
    assert _final_count(db_session, scope.project_id) == 0


def test_closeout_requires_run_to_be_at_exact_human_full_watch_boundary(
    db_session, tmp_path
) -> None:
    scope = _scope(
        db_session,
        tmp_path,
        run_state="REPAIRABLE_LOCAL_FAILURE",
    )

    with pytest.raises(
        ValidationFailureError,
        match="HUMAN_FULL_WATCH|RUN.*STATE|NOT_WAITING_HUMAN_REVIEW",
    ):
        MR1RealProductionService(db_session, workspace_root=tmp_path).closeout(
            _command(scope), drive_gateway=scope.drive_gateway
        )

    assert _final_count(db_session, scope.project_id) == 0
    assert _human_receipts(db_session, scope.project_id) == []


def test_verified_label_cannot_mask_failed_archive_checksum_readback(
    db_session, tmp_path
) -> None:
    scope = _scope(
        db_session,
        tmp_path,
        archive_verified=True,
        archive_verification_overrides={
            "checksum_readback_verified": False,
        },
    )

    assert scope.drive_receipt.content["archive_state"] == "VERIFIED"
    assert scope.drive_receipt.content["ARCHIVE_VERIFIED"] is True
    with pytest.raises(
        ValidationFailureError,
        match=(
            "MR1_CANONICAL_DRIVE_RECEIPT_PROOF_INVALID:"
            "MR1_DRIVE_ARCHIVE_CHECKSUM_READBACK_FAILED"
        ),
    ):
        MR1RealProductionService(db_session, workspace_root=tmp_path).closeout(
            _command(scope), drive_gateway=scope.drive_gateway
        )

    assert _final_count(db_session, scope.project_id) == 0
    assert _human_receipts(db_session, scope.project_id) == []


def test_closeout_recomputes_canonical_drive_checksum_proof(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    service = MR1RealProductionService(db_session, workspace_root=tmp_path)
    tampered_content = deepcopy(scope.drive_receipt.content or {})
    remote_proof = tampered_content["files"][0]
    remote_proof["remote_sha256"] = "0" * 64
    remote_proof["remote_md5"] = "0" * 32
    tampered_content["receipt_hash"] = _recompute_drive_receipt_hash(tampered_content)
    assert all(tampered_content["verification"].values())
    assert service._drive_receipt_hash_valid(tampered_content)
    tampered_drive = service._create_version_on_existing_artifact(
        existing=scope.drive_receipt,
        content=tampered_content,
        actor_id=scope.operator_id,
        correlation_id=f"test-mr1-canonical-drive-proof-tamper-{scope.run_id}",
    )
    run_version = _current_artifact_version(
        db_session, scope.project_id, "mr1_execution_run"
    )
    run_artifact = db_session.get(Artifact, run_version.artifact_id)
    assert run_artifact is not None
    state = deepcopy(run_version.content or {})
    state["drive_archive"] = {
        **tampered_content,
        "artifact_version_id": str(tampered_drive.id),
        "content_hash": tampered_drive.content_hash,
    }
    service._save_run(run_artifact, state, actor_id=scope.operator_id)

    with pytest.raises(
        ValidationFailureError,
        match="MR1_CANONICAL_DRIVE_RECEIPT_PROOF_INVALID",
    ):
        service.closeout(
            _command(
                scope,
                drive_archive_receipt_artifact_version_id=tampered_drive.id,
                drive_archive_receipt_content_hash=tampered_drive.content_hash,
            ),
            drive_gateway=scope.drive_gateway,
        )

    assert _final_count(db_session, scope.project_id) == 0
    assert _human_receipts(db_session, scope.project_id) == []


@pytest.mark.parametrize(
    ("technical_qc_result", "creative_review_result", "reason"),
    (
        (
            "FAIL",
            "ACCEPTED",
            "MR1_TECHNICAL_QC_PASS_REQUIRED",
        ),
        (
            "PASS",
            "BLOCK",
            "MR1_CREATIVE_MEDIA_QC_ACCEPTANCE_REQUIRED",
        ),
    ),
)
def test_human_pass_cannot_bypass_technical_or_creative_gate(
    db_session,
    tmp_path,
    technical_qc_result,
    creative_review_result,
    reason,
) -> None:
    scope = _scope(
        db_session,
        tmp_path,
        technical_qc_result=technical_qc_result,
        creative_review_result=creative_review_result,
    )

    with pytest.raises(ValidationFailureError, match=reason):
        MR1RealProductionService(db_session, workspace_root=tmp_path).closeout(
            _command(scope), drive_gateway=scope.drive_gateway
        )

    assert _final_count(db_session, scope.project_id) == 0
    assert _human_receipts(db_session, scope.project_id) == []


@pytest.mark.parametrize(
    ("change", "reason"),
    (
        ({"reviewed_output_sha256": "0" * 64}, "SHA256|HASH"),
        (
            {"drive_archive_receipt_content_hash": "0" * 64},
            "MR1_CLOSEOUT_DRIVE_RECEIPT_HASH_MISMATCH",
        ),
        (
            {"archive_identity": "drive-archive://wrong"},
            "MR1_CLOSEOUT_ARCHIVE_IDENTITY_MISMATCH",
        ),
    ),
)
def test_pass_must_bind_exact_mp4_and_drive_archive_identity(
    db_session, tmp_path, change, reason
) -> None:
    scope = _scope(db_session, tmp_path)

    with pytest.raises(ValidationFailureError, match=reason):
        MR1RealProductionService(db_session, workspace_root=tmp_path).closeout(
            _command(scope, **change), drive_gateway=scope.drive_gateway
        )

    assert _final_count(db_session, scope.project_id) == 0
    assert _human_receipts(db_session, scope.project_id) == []


def test_closeout_rehashes_actual_mp4_bytes_before_registration(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    scope.output_path.write_bytes(b"different-bytes-after-review-candidate")

    with pytest.raises(
        ValidationFailureError,
        match="MR1_REVIEW_MEDIA_OUTPUT_SHA256_MISMATCH",
    ):
        MR1RealProductionService(db_session, workspace_root=tmp_path).closeout(
            _command(scope), drive_gateway=scope.drive_gateway
        )

    assert _final_count(db_session, scope.project_id) == 0
    assert _human_receipts(db_session, scope.project_id) == []


def test_closeout_rehashes_actual_asset_provenance_manifest(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    provenance_path = Path(
        str(scope.candidate.content["asset_provenance_manifest_ref"])
    )
    provenance_path.write_text('{"tampered":true}', encoding="utf-8")

    with pytest.raises(
        ValidationFailureError,
        match="CANDIDATE_PROVENANCE_MANIFEST_INVALID",
    ):
        MR1RealProductionService(db_session, workspace_root=tmp_path).closeout(
            _command(scope), drive_gateway=scope.drive_gateway
        )

    assert _final_count(db_session, scope.project_id) == 0
    assert _human_receipts(db_session, scope.project_id) == []


def test_exact_human_pass_creates_one_bound_final_media_without_upload(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    before_upload = _upload_counts(db_session)
    service = MR1RealProductionService(db_session, workspace_root=tmp_path)
    task = db_session.get(ReviewTask, scope.review_task_id)

    assert _final_count(db_session, scope.project_id) == 0
    assert task is not None
    assert task.status == "open"
    assert task.review_type == "final_human"
    assert task.target_artifact_version_id == scope.candidate.id
    assert task.assigned_to_user_id == scope.operator_id
    assert task.requested_by_user_id == scope.operator_id
    assert task.review_reason_codes == [
        "MR1_EXACT_FINAL_MEDIA_FULL_WATCH_REQUIRED",
        "MR1_REVIEW_ROUND_1",
    ]
    assert task.context_pack_ref.endswith("/review-round-1")
    assert {
        (item["type"], item.get("review_round"))
        for item in task.evidence_refs
        if item["type"] != "mr1_exact_package"
    } == {
        ("mr1_review_media_candidate", 1),
        ("mr1_drive_archive_receipt", 1),
        ("mr1_technical_media_qc_receipt", 1),
    }
    result = service.closeout(_command(scope), drive_gateway=scope.drive_gateway)

    assert result["MR1_HUMAN_REVIEW"] == "PASS"
    assert result["MR1_FINAL_MEDIA_REF"] == "PASS"
    assert result["MR1_FINAL"] == "PASS"
    for key in (
        "MR1_ENTRY",
        "MR1_APPROVAL_BINDING",
        "MR1_PREFLIGHT",
        "MR1_ELEVENLABS",
        "MR1_FORCED_ALIGNMENT",
        "MR1_CANONICAL_TIMELINE",
        "MR1_PEXELS",
        "MR1_NATIVE_ASSETS",
        "MR1_MEDIA_NORMALIZATION",
        "MR1_NATIVE_RENDER_PLAN",
        "MR1_NATIVE_MOTION_COMPILER",
        "MR1_NATIVE_FFMPEG_RENDER",
        "MR1_TECHNICAL_MEDIA_QC",
        "MR1_CREATIVE_MEDIA_QC",
        "MR1_REVIEW_MEDIA_CANDIDATE",
        "MR1_DRIVE_ARCHIVE",
    ):
        assert result[key] == "PASS"
    assert result["MR1_GEMINI_IMAGE"] == "NOT_REQUIRED"
    assert result["MR1_GOOGLE_VEO"] == "NOT_REQUIRED"
    assert result["ARCHIVE_VERIFIED"] is True
    assert result["MR1_PROVIDER_CALL_COUNT"] == 7
    assert result["provider_call_counts"]["google_drive_archive_flows"] == 2
    assert result["MR1_RENDER_ATTEMPTS"] == 1
    assert result["MR1_REPAIR_CYCLES"] == 0
    assert result["DESTINATION_STATUS"] == "PENDING_PLATFORM_ID"
    assert result["UPLOAD_READY"] is False
    assert result["PUBLISH_EXECUTION_READY"] is False
    assert result["PROCEED_TO_DESTINATION_CLOSEOUT"] is True
    assert result["PROCEED_TO_PUB1"] is False
    assert result["youtube_calls"] == 0
    assert _final_count(db_session, scope.project_id) == 1

    final_media = db_session.get(FinalMediaRef, uuid.UUID(result["final_media_ref_id"]))
    assert final_media is not None
    assert final_media.video_project_id == scope.project_id
    assert final_media.media_type == "LONG_FORM_FINAL"
    assert final_media.file_ref == str(scope.output_path)
    assert final_media.provider_key == "mr1-native-ffmpeg-renderer"
    assert final_media.provider_type == "LOCAL_RENDERER_CAPABILITY"
    assert final_media.checksum_sha256 == scope.output_sha256
    assert final_media.cloud_media_ref_id is not None
    assert final_media.lineage_artifact_version_id is not None
    assert final_media.media_qc_report_id is None
    assert final_media.uploaded_video_id is None

    task = db_session.get(ReviewTask, scope.review_task_id)
    assert task is not None
    assert task.status == "completed"
    assert any(
        item.get("type") == "explicit_human_approval_resolution"
        and item.get("approval_decision_ids") == [result["approval_id"]]
        for item in task.evidence_refs
    )

    receipts = _human_receipts(db_session, scope.project_id)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert result["human_review_receipt_artifact_version_id"] == str(receipt.id)
    assert result["human_review_receipt_content_hash"] == receipt.content_hash
    assert receipt.content["decision"] == "PASS"
    assert receipt.content["decision_source"] == "OPERATOR"
    assert receipt.content["review_authority"] == "HUMAN"
    assert receipt.content["run_id"] == str(scope.run_id)
    assert receipt.content["reviewed_output_sha256"] == scope.output_sha256
    assert receipt.content["review_media_candidate"]["artifact_version_id"] == str(
        scope.candidate.id
    )
    assert (
        receipt.content["review_media_candidate"]["content_hash"]
        == scope.candidate.content_hash
    )
    assert receipt.content["drive_archive_receipt"]["artifact_version_id"] == str(
        scope.drive_receipt.id
    )
    assert (
        receipt.content["drive_archive_receipt"]["content_hash"]
        == scope.drive_receipt.content_hash
    )
    assert (
        receipt.content["drive_archive_receipt"]["archive_identity"]
        == scope.archive_identity
    )
    assert receipt.content["technical_qc_result"] == "PASS"
    assert receipt.content["creative_review_result"] == "ACCEPTED"
    assert receipt.content["archive_verification_result"] == "PASS"
    assert receipt.content["youtube_calls"] == 0

    technical_qc = _current_artifact_version(
        db_session, scope.project_id, "mr1_technical_media_qc_receipt"
    )
    lineage = db_session.get(ArtifactVersion, final_media.lineage_artifact_version_id)
    cloud = db_session.get(CloudMediaRef, final_media.cloud_media_ref_id)
    assert lineage is not None
    assert cloud is not None
    lineage_artifact = db_session.get(Artifact, lineage.artifact_id)
    assert lineage_artifact is not None
    assert lineage_artifact.current_version_id == lineage.id
    assert lineage_artifact.status == "approved"
    assert lineage.status == "approved"
    assert set(lineage.content) == {
        "schema_version",
        "run_id",
        "project_id",
        "review_round",
        "output_sha256",
        "output_size_bytes",
        "review_media_candidate",
        "drive_archive_receipt",
        "drive_final_media_proof",
        "cloud_media_ref",
        "technical_media_qc",
        "human_full_watch_receipt",
        "drive_finalization_authority",
        "frozen_authority",
        "source_refs",
        "provider_key",
        "provider_type",
        "media_qc_report_id",
        "production_eligible",
        "human_pass_required_and_present",
        "publish_execution_authorized",
        "youtube_calls",
    }
    assert lineage.content["review_round"] == 1
    assert lineage.content["output_sha256"] == scope.output_sha256
    assert lineage.content["review_media_candidate"] == {
        "artifact_version_id": str(scope.candidate.id),
        "content_hash": scope.candidate.content_hash,
    }
    assert lineage.content["drive_archive_receipt"] == {
        "artifact_version_id": str(scope.drive_receipt.id),
        "content_hash": scope.drive_receipt.content_hash,
        "archive_identity": scope.archive_identity,
    }
    assert lineage.content["technical_media_qc"] == {
        "artifact_version_id": str(technical_qc.id),
        "content_hash": technical_qc.content_hash,
        "result": "PASS",
    }
    assert lineage.content["human_full_watch_receipt"] == {
        "artifact_version_id": str(receipt.id),
        "content_hash": receipt.content_hash,
        "decision": "PASS",
    }
    assert lineage.content["drive_finalization_authority"] == {
        "phase": DRIVE_IDEMPOTENCY_PHASES[1],
        "distinct_from_canonical_archive": True,
        "verified_supplement_required_before_final_media_ref": True,
    }
    assert lineage.content["cloud_media_ref"]["id"] == str(cloud.id)
    assert lineage.content["cloud_media_ref"]["checksum_sha256"] == (
        scope.output_sha256
    )
    assert lineage.content["media_qc_report_id"] is None
    assert cloud.checksum_sha256 == scope.output_sha256
    assert cloud.upload_status == "VERIFIED"
    assert cloud.verification_status == "CHECKSUM_VERIFIED"
    final_drive_proofs = [
        item
        for item in scope.drive_receipt.content["files"]
        if item["logical_role"] == "MR1_FINAL_REVIEW_MP4"
    ]
    assert len(final_drive_proofs) == 1
    assert cloud.drive_file_id == final_drive_proofs[0]["drive_file_id"]
    assert cloud.drive_folder_id == final_drive_proofs[0]["drive_folder_id"]
    assert cloud.size_bytes == final_drive_proofs[0]["remote_size_bytes"]
    assert len(scope.drive_gateway.finalization_calls) == 1
    finalization_attempts = [
        item
        for item in result["attempts"]
        if item.get("operation") == "finalization_supplement"
    ]
    assert len(finalization_attempts) == 1
    finalization_attempt = finalization_attempts[0]
    assert finalization_attempt["state"] == "SUCCEEDED"
    assert finalization_attempt["submit_state"] == "SUCCEEDED"
    assert finalization_attempt["attempt_count"] == 1
    assert finalization_attempt["network_submit_started"] is True
    assert (
        finalization_attempt["drive_phase_authority"] == (DRIVE_IDEMPOTENCY_PHASES[1])
    )
    supplement = _current_artifact_version(
        db_session,
        scope.project_id,
        "mr1_drive_finalization_supplement_receipt",
    )
    supplement_body = supplement.content
    supplement_manifest = supplement_body["supplement_manifest"]
    supplement_remote = supplement_body["remote_verification_receipt"]
    assert supplement_body["review_media_candidate"] == {
        "artifact_version_id": str(scope.candidate.id),
        "content_hash": scope.candidate.content_hash,
    }
    assert supplement_body["canonical_drive_archive_receipt"] == {
        "artifact_version_id": str(scope.drive_receipt.id),
        "content_hash": scope.drive_receipt.content_hash,
    }
    assert supplement_body["human_full_watch_receipt"] == {
        "artifact_version_id": str(receipt.id),
        "content_hash": receipt.content_hash,
    }
    assert supplement_body["final_media_lineage_receipt"] == {
        "artifact_version_id": str(lineage.id),
        "content_hash": lineage.content_hash,
    }
    assert [item["logical_role"] for item in supplement_manifest["files"]] == [
        "MR1_FINAL_MEDIA_LINEAGE_RECEIPT",
        "MR1_HUMAN_FULL_WATCH_RECEIPT",
    ]
    assert supplement_manifest["drive_phase_authority"] == (DRIVE_IDEMPOTENCY_PHASES[1])
    assert supplement_manifest["idempotency_identity"] == {
        "operation_key": "google_drive:finalization-supplement",
        "idempotency_key": finalization_attempt["idempotency_key"],
        "idempotency_fingerprint": finalization_attempt["idempotency_fingerprint"],
        "review_round": 1,
        "distinct_from_canonical_archive": True,
        "automatic_retry_allowed": False,
    }
    assert scope.drive_gateway.finalization_calls[0]["manifest"] == (
        supplement_manifest
    )
    assert supplement_remote["archive_phase"] == "FINALIZATION_SUPPLEMENT"
    assert supplement_remote["ARCHIVE_VERIFIED"] is True
    assert supplement_remote["archive_state"] == "VERIFIED"
    assert set(supplement_remote["verification"]) == {
        "exact_item_set",
        "exact_item_count",
        "correct_parent",
        "correct_names",
        "size_verified",
        "checksum_readback_verified",
        "duplicate_absence",
        "receipt_hash_valid",
        "final_request_manifest_exact",
        "archive_identity_exact",
        "run_identity_exact",
        "provider_archive_state_verified",
    }
    assert all(supplement_remote["verification"].values())
    assert supplement_body["exact_supplement_item_set_verified"] is True
    assert supplement_body["canonical_review_archive_mutated"] is False
    assert supplement_body["final_media_registration_allowed"] is True
    assert (
        result["event_order"].index("FINAL_MEDIA_LINEAGE_RECEIPT_CREATED")
        < result["event_order"].index("FINAL_ARCHIVE_SUPPLEMENT_VERIFIED")
        < result["event_order"].index("FINAL_MEDIA_REF_CREATED")
    )
    assert {item["type"] for item in cloud.source_refs} >= {
        "mr1_review_media_candidate",
        "mr1_drive_archive_receipt",
        "mr1_technical_media_qc_receipt",
        "mr1_human_full_watch_receipt",
        "package_manifest",
        "mr1_approval",
        "channel_profile_version",
        "compiled_channel_policy_snapshot",
        "target_market_profile",
        "target_market_digest",
        "market_alignment_dossier",
        "niche_alignment_dossier",
    }
    assert _upload_counts(db_session) == before_upload


def test_human_pass_closeout_is_idempotent_for_same_exact_evidence(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    service = MR1RealProductionService(db_session, workspace_root=tmp_path)
    command = _command(scope)

    first = service.closeout(command, drive_gateway=scope.drive_gateway)
    second = service.closeout(command, drive_gateway=scope.drive_gateway)

    assert second["final_media_ref_id"] == first["final_media_ref_id"]
    assert (
        second["human_review_receipt_artifact_version_id"]
        == first["human_review_receipt_artifact_version_id"]
    )
    assert (
        second["human_review_receipt_content_hash"]
        == first["human_review_receipt_content_hash"]
    )
    assert _final_count(db_session, scope.project_id) == 1
    assert len(_human_receipts(db_session, scope.project_id)) == 1
    assert (
        int(
            db_session.scalar(
                select(func.count())
                .select_from(CloudMediaRef)
                .where(CloudMediaRef.video_project_id == scope.project_id)
            )
            or 0
        )
        == 1
    )
    assert (
        len(
            list(
                db_session.scalars(
                    select(Artifact).where(
                        Artifact.video_project_id == scope.project_id,
                        Artifact.artifact_type == "mr1_final_media_lineage_receipt",
                    )
                ).all()
            )
        )
        == 1
    )
    assert len(scope.drive_gateway.finalization_calls) == 1
    assert _upload_counts(db_session) == {
        "human_upload_tasks": 0,
        "uploaded_videos": 0,
    }


def test_human_pass_closeout_resumes_after_finalization_drive_interruption(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    service = MR1RealProductionService(db_session, workspace_root=tmp_path)
    command = _command(scope)
    scope.drive_gateway.fail_finalization_after_mutation_once = True

    with pytest.raises(
        RuntimeError,
        match="FAKE_DRIVE_FINALIZATION_INTERRUPTED_AFTER_MUTATION",
    ):
        service.closeout(command, drive_gateway=scope.drive_gateway)

    interrupted_run = _current_artifact_version(
        db_session, scope.project_id, "mr1_execution_run"
    )
    interrupted_state = interrupted_run.content or {}
    review_task = db_session.get(ReviewTask, scope.review_task_id)
    assert interrupted_state["current_state"] == "FINALIZING_ARCHIVE_SUPPLEMENT"
    assert interrupted_state["state"] == "FINALIZING_ARCHIVE_SUPPLEMENT"
    assert interrupted_state["final_archive_supplement_attempt"]["state"] == (
        "MUTATING_OR_RECONCILING"
    )
    interrupted_attempt = interrupted_state["attempts"][
        "google_drive:finalization-supplement"
    ]
    assert interrupted_attempt["state"] == "MUTATING_OR_RECONCILING"
    assert interrupted_attempt["submit_state"] == "SUBMITTING_OR_RECONCILING"
    assert interrupted_attempt["attempt_count"] == 1
    assert interrupted_attempt["network_submit_started"] is True
    assert interrupted_attempt["drive_phase_authority"] == (DRIVE_IDEMPOTENCY_PHASES[1])
    assert interrupted_state["provider_call_counts"]["drive"] == 2
    assert review_task is not None
    assert review_task.status == "completed"
    assert _final_count(db_session, scope.project_id) == 0
    assert len(_human_receipts(db_session, scope.project_id)) == 1
    assert (
        _artifact_count(db_session, scope.project_id, "mr1_final_media_lineage_receipt")
        == 1
    )
    assert (
        _artifact_count(
            db_session,
            scope.project_id,
            "mr1_drive_finalization_supplement_receipt",
        )
        == 0
    )
    assert (
        int(
            db_session.scalar(
                select(func.count())
                .select_from(CloudMediaRef)
                .where(CloudMediaRef.video_project_id == scope.project_id)
            )
            or 0
        )
        == 1
    )

    scope.drive_gateway.fail_finalization_after_mutation_once = False
    resumed = service.closeout(command, drive_gateway=scope.drive_gateway)

    assert resumed["MR1_FINAL"] == "PASS"
    assert _final_count(db_session, scope.project_id) == 1
    assert len(_human_receipts(db_session, scope.project_id)) == 1
    assert (
        _artifact_count(db_session, scope.project_id, "mr1_final_media_lineage_receipt")
        == 1
    )
    assert (
        _artifact_count(
            db_session,
            scope.project_id,
            "mr1_drive_finalization_supplement_receipt",
        )
        == 1
    )
    assert len(scope.drive_gateway.finalization_calls) == 2
    resumed_run = _current_artifact_version(
        db_session, scope.project_id, "mr1_execution_run"
    )
    resumed_attempt = resumed_run.content["attempts"][
        "google_drive:finalization-supplement"
    ]
    assert resumed_attempt["state"] == "SUCCEEDED"
    assert resumed_attempt["submit_state"] == "SUCCEEDED"
    assert resumed_attempt["attempt_count"] == 1
    assert resumed_run.content["provider_call_counts"]["drive"] == 2


def test_rejection_records_exact_human_receipt_but_never_final_media(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    service = MR1RealProductionService(db_session, workspace_root=tmp_path)

    result = service.closeout(
        _command(
            scope,
            decision="REJECT",
            operator_decision_text=(
                "REJECT: caption readability requires deterministic repair"
            ),
        )
    )

    assert result["MR1_HUMAN_REVIEW"] == "REJECT"
    assert result["MR1_FINAL_MEDIA_REF"] == "NOT_CREATED"
    assert result["MR1_FINAL"] != "PASS"
    assert result["PROCEED_TO_DESTINATION_CLOSEOUT"] is False
    assert result["PROCEED_TO_PUB1"] is False
    assert result["youtube_calls"] == 0
    assert _final_count(db_session, scope.project_id) == 0
    receipts = _human_receipts(db_session, scope.project_id)
    assert len(receipts) == 1
    assert receipts[0].content["decision"] == "REJECT"
    assert receipts[0].content["reviewed_output_sha256"] == (scope.output_sha256)
    assert (
        receipts[0].content["drive_archive_receipt"]["archive_identity"]
        == scope.archive_identity
    )
    assert _upload_counts(db_session) == {
        "human_upload_tasks": 0,
        "uploaded_videos": 0,
    }


def test_fake_provider_start_output_closes_without_execution_schema_drift(
    db_session, tmp_path
) -> None:
    approved = _approved_mr1(db_session, tmp_path)
    gateways, fakes = _gateways()
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    )
    pending = service.start(approved["command"], gateways=gateways)
    project_id = approved["command"].project_id
    candidate = _current_artifact_version(
        db_session, project_id, "mr1_review_media_candidate"
    )
    drive_receipt = _current_artifact_version(
        db_session, project_id, "mr1_drive_archive_receipt"
    )
    candidate_content = candidate.content.get(
        "review_media_candidate", candidate.content
    )
    drive_content = drive_receipt.content.get(
        "drive_archive_receipt", drive_receipt.content
    )
    approval_decision = db_session.get(
        ApprovalDecision, approved["command"].approval_id
    )
    assert approval_decision is not None

    result = service.closeout(
        MR1FinalMediaCloseoutCommand(
            run_id=uuid.UUID(pending["run_id"]),
            project_id=project_id,
            review_media_candidate_artifact_version_id=candidate.id,
            review_media_candidate_content_hash=candidate.content_hash,
            reviewed_output_sha256=candidate_content["output_sha256"],
            drive_archive_receipt_artifact_version_id=drive_receipt.id,
            drive_archive_receipt_content_hash=drive_receipt.content_hash,
            archive_identity=drive_content["archive_identity"],
            decided_by_user_id=approval_decision.decided_by_user_id,
            decision="PASS",
            operator_decision_text="PASS",
        ),
        drive_gateway=fakes["drive"],
    )

    assert result["MR1_FINAL"] == "PASS"
    assert result["MR1_HUMAN_REVIEW"] == "PASS"
    assert result["MR1_FINAL_MEDIA_REF"] == "PASS"
    assert result["ARCHIVE_VERIFIED"] is True
    assert result["MR1_PROVIDER_CALL_COUNT"] == 7
    assert result["provider_call_counts"]["google_drive_archive_flows"] == 2
    assert result["DESTINATION_STATUS"] == "PENDING_PLATFORM_ID"
    assert result["UPLOAD_READY"] is False
    assert result["PUBLISH_EXECUTION_READY"] is False
    assert result["PROCEED_TO_PUB1"] is False
    assert result["youtube_calls"] == 0
    assert _final_count(db_session, project_id) == 1
    final_media = db_session.get(FinalMediaRef, uuid.UUID(result["final_media_ref_id"]))
    assert final_media is not None
    assert final_media.provider_key == "mr1-native-ffmpeg-renderer"
    assert final_media.checksum_sha256 == candidate_content["output_sha256"]
    assert final_media.media_qc_report_id is None
    assert final_media.cloud_media_ref_id is not None
    assert final_media.lineage_artifact_version_id is not None
    cloud = db_session.get(CloudMediaRef, final_media.cloud_media_ref_id)
    lineage = db_session.get(ArtifactVersion, final_media.lineage_artifact_version_id)
    assert cloud is not None
    assert lineage is not None
    assert cloud.storage_provider == "GOOGLE_DRIVE"
    assert cloud.upload_status == "VERIFIED"
    assert cloud.verification_status == "CHECKSUM_VERIFIED"
    assert cloud.checksum_sha256 == candidate_content["output_sha256"]
    assert lineage.content["output_sha256"] == candidate_content["output_sha256"]
    assert lineage.content["cloud_media_ref"]["id"] == str(cloud.id)
    assert lineage.content["media_qc_report_id"] is None
    assert len(_human_receipts(db_session, project_id)) == 1
    assert _upload_counts(db_session) == {
        "human_upload_tasks": 0,
        "uploaded_videos": 0,
    }


def test_final_closeout_replay_rehashes_immutable_lineage(db_session, tmp_path) -> None:
    approved = _approved_mr1(db_session, tmp_path)
    gateways, fakes = _gateways()
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    )
    pending = service.start(approved["command"], gateways=gateways)
    candidate = _current_artifact_version(
        db_session,
        approved["command"].project_id,
        "mr1_review_media_candidate",
    )
    drive = _current_artifact_version(
        db_session,
        approved["command"].project_id,
        "mr1_drive_archive_receipt",
    )
    approval = db_session.get(ApprovalDecision, approved["command"].approval_id)
    assert approval is not None
    command = MR1FinalMediaCloseoutCommand(
        run_id=uuid.UUID(pending["run_id"]),
        project_id=approved["command"].project_id,
        review_media_candidate_artifact_version_id=candidate.id,
        review_media_candidate_content_hash=candidate.content_hash,
        reviewed_output_sha256=candidate.content["output_sha256"],
        drive_archive_receipt_artifact_version_id=drive.id,
        drive_archive_receipt_content_hash=drive.content_hash,
        archive_identity=drive.content["archive_identity"],
        decided_by_user_id=approval.decided_by_user_id,
        decision="PASS",
        operator_decision_text="PASS",
    )
    closed = service.closeout(command, drive_gateway=fakes["drive"])
    final_media = db_session.get(FinalMediaRef, uuid.UUID(closed["final_media_ref_id"]))
    assert final_media is not None
    assert final_media.lineage_artifact_version_id is not None
    lineage = db_session.get(ArtifactVersion, final_media.lineage_artifact_version_id)
    assert lineage is not None
    lineage.content = {
        **lineage.content,
        "output_sha256": "0" * 64,
    }
    with db_session.no_autoflush:
        with pytest.raises(
            ValidationFailureError,
            match="HASH_MISMATCH|FINAL_MEDIA_AUTHORITY_INVALID",
        ):
            service.closeout(command)


def test_final_closeout_replay_rejects_cloud_checksum_tamper(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    service = MR1RealProductionService(db_session, workspace_root=tmp_path)
    command = _command(scope)
    closed = service.closeout(command, drive_gateway=scope.drive_gateway)
    final_media = db_session.get(FinalMediaRef, uuid.UUID(closed["final_media_ref_id"]))
    assert final_media is not None
    assert final_media.cloud_media_ref_id is not None
    cloud = db_session.get(CloudMediaRef, final_media.cloud_media_ref_id)
    assert cloud is not None
    cloud.checksum_sha256 = "0" * 64
    db_session.flush()

    with pytest.raises(
        ValidationFailureError,
        match="FINAL_MEDIA_AUTHORITY_INVALID",
    ):
        service.closeout(command)


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        ("company_id", uuid.uuid4()),
        ("channel_workspace_id", uuid.uuid4()),
        ("uploaded_video_id", uuid.uuid4()),
        ("duration_seconds", Decimal("1.000000")),
    ],
)
def test_final_closeout_replay_rejects_final_media_scope_tamper(
    db_session,
    tmp_path,
    field_name: str,
    tampered_value: object,
) -> None:
    scope = _scope(db_session, tmp_path)
    service = MR1RealProductionService(db_session, workspace_root=tmp_path)
    command = _command(scope)
    closed = service.closeout(command, drive_gateway=scope.drive_gateway)
    final_media = db_session.get(FinalMediaRef, uuid.UUID(closed["final_media_ref_id"]))
    assert final_media is not None
    setattr(final_media, field_name, tampered_value)

    with db_session.no_autoflush:
        with pytest.raises(
            ValidationFailureError,
            match="MR1_FINAL_MEDIA_AUTHORITY_INVALID",
        ):
            service.closeout(command)


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        ("company_id", uuid.uuid4()),
        ("channel_workspace_id", uuid.uuid4()),
        ("uploaded_video_id", uuid.uuid4()),
        ("render_package_id", uuid.uuid4()),
        ("local_cleanup_status", "CLEANED"),
    ],
)
def test_final_closeout_replay_rejects_cloud_media_scope_tamper(
    db_session,
    tmp_path,
    field_name: str,
    tampered_value: object,
) -> None:
    scope = _scope(db_session, tmp_path)
    service = MR1RealProductionService(db_session, workspace_root=tmp_path)
    command = _command(scope)
    closed = service.closeout(command, drive_gateway=scope.drive_gateway)
    final_media = db_session.get(FinalMediaRef, uuid.UUID(closed["final_media_ref_id"]))
    assert final_media is not None
    assert final_media.cloud_media_ref_id is not None
    cloud = db_session.get(CloudMediaRef, final_media.cloud_media_ref_id)
    assert cloud is not None
    setattr(cloud, field_name, tampered_value)

    with db_session.no_autoflush:
        with pytest.raises(
            ValidationFailureError,
            match="MR1_FINAL_MEDIA_AUTHORITY_INVALID",
        ):
            service.closeout(command)


def test_final_closeout_replay_rehashes_technical_qc_source(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    service = MR1RealProductionService(db_session, workspace_root=tmp_path)
    command = _command(scope)
    service.closeout(command, drive_gateway=scope.drive_gateway)
    technical_qc = _current_artifact_version(
        db_session, scope.project_id, "mr1_technical_media_qc_receipt"
    )
    qc_path = Path(str(technical_qc.content["source_qc_file"]["file_ref"]))
    qc_path.write_text('{"tampered":true}', encoding="utf-8")

    with pytest.raises(
        ValidationFailureError,
        match="TECHNICAL_QC_(AUTHORITY_INVALID|SOURCE_CONTENT_MISMATCH)",
    ):
        service.closeout(command)


def test_final_closeout_replay_rejects_missing_supplement_verification_key(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    service = MR1RealProductionService(db_session, workspace_root=tmp_path)
    command = _command(scope)
    service.closeout(command, drive_gateway=scope.drive_gateway)
    supplement = _current_artifact_version(
        db_session,
        scope.project_id,
        "mr1_drive_finalization_supplement_receipt",
    )
    tampered_content = deepcopy(supplement.content or {})
    tampered_content["remote_verification_receipt"]["verification"].pop(
        "checksum_readback_verified"
    )
    tampered_supplement = service._create_version_on_existing_artifact(
        existing=supplement,
        content=tampered_content,
        actor_id=scope.operator_id,
        correlation_id=f"test-mr1-supplement-verification-tamper-{scope.run_id}",
    )
    run_version = _current_artifact_version(
        db_session, scope.project_id, "mr1_execution_run"
    )
    run_artifact = db_session.get(Artifact, run_version.artifact_id)
    assert run_artifact is not None
    state = deepcopy(run_version.content or {})
    state["final_archive_supplement"] = {
        "artifact_version_id": str(tampered_supplement.id),
        "content_hash": tampered_supplement.content_hash,
    }
    service._save_run(run_artifact, state, actor_id=scope.operator_id)

    with pytest.raises(
        ValidationFailureError,
        match="MR1_FINALIZATION_SUPPLEMENT_AUTHORITY_INVALID",
    ):
        service.closeout(command)


def test_final_closeout_replay_recomputes_supplement_remote_checksum_proof(
    db_session, tmp_path
) -> None:
    scope = _scope(db_session, tmp_path)
    service = MR1RealProductionService(db_session, workspace_root=tmp_path)
    command = _command(scope)
    service.closeout(command, drive_gateway=scope.drive_gateway)
    supplement = _current_artifact_version(
        db_session,
        scope.project_id,
        "mr1_drive_finalization_supplement_receipt",
    )
    tampered_content = deepcopy(supplement.content or {})
    remote = tampered_content["remote_verification_receipt"]
    remote_proof = remote["files"][0]
    remote_proof["remote_sha256"] = "0" * 64
    remote_proof["remote_md5"] = "0" * 32
    remote["receipt_hash"] = _recompute_drive_receipt_hash(remote)
    assert all(remote["verification"].values())
    assert service._drive_receipt_hash_valid(remote)
    tampered_supplement = service._create_version_on_existing_artifact(
        existing=supplement,
        content=tampered_content,
        actor_id=scope.operator_id,
        correlation_id=f"test-mr1-supplement-proof-tamper-{scope.run_id}",
    )
    assert tampered_supplement.content_hash != supplement.content_hash
    run_version = _current_artifact_version(
        db_session, scope.project_id, "mr1_execution_run"
    )
    run_artifact = db_session.get(Artifact, run_version.artifact_id)
    assert run_artifact is not None
    state = deepcopy(run_version.content or {})
    state["final_archive_supplement"] = {
        "artifact_version_id": str(tampered_supplement.id),
        "content_hash": tampered_supplement.content_hash,
    }
    service._save_run(run_artifact, state, actor_id=scope.operator_id)

    with pytest.raises(
        ValidationFailureError,
        match="MR1_FINALIZATION_SUPPLEMENT_AUTHORITY_INVALID",
    ):
        service.closeout(command)
