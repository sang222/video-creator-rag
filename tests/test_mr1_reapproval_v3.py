from __future__ import annotations

import json
import uuid
from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import set_committed_value

from app.contracts.mr1 import MR1ReapprovalCommand
from app.core.errors import ValidationFailureError
from app.db.models import ApprovalDecision, Artifact, ArtifactVersion
from app.services.config_registry import content_hash
from app.services.mr1_reapproval import (
    APPROVAL_SCOPE,
    READINESS_ARTIFACT_TYPE,
    RECEIPT_ARTIFACT_TYPE,
    SUPERSESSION_ARTIFACT_TYPE,
    MR1ReapprovalService,
)
from app.services.pkg1_market_revision_closeout import (
    PKG1MarketRevisionCloseoutService,
)
from app.services.pkg1_market_revision import PKG1MarketRevisionService
from tests.test_pkg1_first_production_package import _external_counts
from tests.test_pkg1_market_revision_human_closeout import _pending_revision


PASS_VERDICTS = (
    "MR1_REAPPROVAL_ENTRY",
    "MR1_REAPPROVAL_EXACT_TARGET",
    "MR1_REAPPROVAL_HASH_REVALIDATION",
    "MR1_REAPPROVAL_PROFILE_V3_BINDING",
    "MR1_REAPPROVAL_TARGET_MARKET_BINDING",
    "MR1_REAPPROVAL_MARKET_ALIGNMENT",
    "MR1_REAPPROVAL_DESTINATION_BINDING",
    "MR1_REAPPROVAL_PROVIDER_PLAN",
    "MR1_REAPPROVAL_COST_SCOPE",
    "MR1_REAPPROVAL_RIGHTS_DISCLOSURE",
    "MR1_REAPPROVAL_LPRO1_CONTRACT",
    "MR1_REAPPROVAL_APPROVAL_RECEIPT",
    "MR1_REAPPROVAL_READINESS",
    "MR1_REAPPROVAL_PUBLISH_BOUNDARY",
)


def _approved_revision(db_session, tmp_path):
    historical, pending, closeout_command, revision_service = _pending_revision(
        db_session, tmp_path
    )
    closeout = PKG1MarketRevisionCloseoutService(db_session).closeout(closeout_command)
    bindings = pending["package"]["exact_bindings"]
    command = MR1ReapprovalCommand(
        project_id=uuid.UUID(pending["video_project_id"]),
        pkg1_approval_decision_id=uuid.UUID(closeout["approval_decision_id"]),
        pkg1_human_review_receipt_version_id=uuid.UUID(
            closeout["human_review_receipt_artifact_version_id"]
        ),
        channel_profile_version_id=uuid.UUID(bindings["channel_profile_version"]["id"]),
        compiled_policy_snapshot_id=uuid.UUID(
            bindings["compiled_channel_policy_snapshot"]["id"]
        ),
    )
    return historical, pending, closeout, command, revision_service


def _artifact_version(db_session, ref: dict) -> ArtifactVersion:
    version = db_session.get(ArtifactVersion, uuid.UUID(ref["artifact_version_id"]))
    assert version is not None
    return version


def _mr1_counts(db_session, project_id: uuid.UUID) -> dict[str, int]:
    artifact_types = (
        RECEIPT_ARTIFACT_TYPE,
        READINESS_ARTIFACT_TYPE,
        SUPERSESSION_ARTIFACT_TYPE,
    )
    return {
        "artifacts": db_session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(
                Artifact.video_project_id == project_id,
                Artifact.artifact_type.in_(artifact_types),
            )
        ),
        "approvals": db_session.scalar(
            select(func.count())
            .select_from(ApprovalDecision)
            .where(
                ApprovalDecision.target_type == "artifact_version",
                ApprovalDecision.metadata_["approval_scope"].astext == APPROVAL_SCOPE,
            )
        ),
    }


def test_mr1_reapproval_binds_exact_package_policy_market_and_execution_scope(
    db_session, tmp_path
) -> None:
    _, pending, closeout, command, revision_service = _approved_revision(
        db_session, tmp_path
    )
    package = pending["package"]
    revised = package["revised_artifacts"]
    provider_version = _artifact_version(db_session, revised["provider_execution_plan"])
    cost_version = _artifact_version(db_session, revised["cost_estimate_snapshot"])
    package_version = db_session.get(
        ArtifactVersion, uuid.UUID(pending["package_artifact_version_id"])
    )
    receipt_version = db_session.get(
        ArtifactVersion, command.pkg1_human_review_receipt_version_id
    )
    assert package_version is not None and receipt_version is not None
    no_execution_before = revision_service._no_execution_counts()
    external_before = _external_counts(db_session)

    result = MR1ReapprovalService(db_session).approve(command)

    assert all(result[key] == "PASS" for key in PASS_VERDICTS)
    assert result["MR1_EXECUTION"] == "NOT_STARTED"
    assert result["MR1_PROVIDER_CALL_COUNT"] == 0
    assert result["MR1_RENDER_STATUS"] == "NOT_STARTED"
    assert result["MR1_HUMAN_REVIEW"] == "PENDING"
    assert result["PROCEED_TO_MR1"] is True
    assert result["provider_calls"] == 0
    assert result["render_calls"] == 0
    assert result["drive_calls"] == 0
    assert result["youtube_calls"] == 0
    assert result["no_execution_counts_before"] == no_execution_before
    assert result["no_execution_counts_after"] == no_execution_before
    assert revision_service._no_execution_counts() == no_execution_before
    assert _external_counts(db_session) == external_before

    exact_target = result["exact_target"]
    assert exact_target["project_id"] == pending["video_project_id"]
    assert exact_target["package_artifact_version_id"] == str(package_version.id)
    assert exact_target["package_content_hash"] == package_version.content_hash
    assert exact_target["revision_id"] == pending["revision_id"]
    assert exact_target["revision_version"] == pending["revision_version"]
    assert exact_target["revision_hash"] == pending["revision_hash"]
    assert exact_target["pkg1_approval_decision_id"] == closeout["approval_decision_id"]
    assert exact_target["pkg1_human_review_receipt_version_id"] == str(
        receipt_version.id
    )
    assert exact_target["pkg1_human_review_receipt_content_hash"] == (
        receipt_version.content_hash
    )

    bindings = result["exact_bindings"]
    for key, expected in package["exact_bindings"].items():
        assert bindings[key] == expected
    for key in (
        "market_alignment_dossier",
        "niche_alignment_dossier",
        "provider_execution_plan",
        "cost_estimate_snapshot",
        "rights_disclosure_completeness_report",
        "synthetic_media_disclosure_receipt_draft",
        "asset_provenance_plan",
        "publish_risk_dossier",
    ):
        assert bindings[key] == package["revised_artifacts"][key]
    assert bindings["channel_profile_version"]["version"] == 3
    assert bindings["channel_profile_version"]["id"] == str(
        command.channel_profile_version_id
    )
    assert bindings["compiled_channel_policy_snapshot"]["id"] == str(
        command.compiled_policy_snapshot_id
    )
    assert bindings["provider_execution_plan"] == revised["provider_execution_plan"]
    assert bindings["cost_estimate_snapshot"] == revised["cost_estimate_snapshot"]

    attempt_scope = result["provider_attempt_scope"]
    provider_plan = provider_version.content
    assert (
        attempt_scope["provider_execution_plan"] == revised["provider_execution_plan"]
    )
    assert attempt_scope["stages"] == provider_plan["stages"]
    assert attempt_scope["scene_routes"] == provider_plan["scene_routes"]
    assert attempt_scope["one_route_per_scene"] is True
    assert attempt_scope["single_run"] is True
    assert attempt_scope["terminal_after_execution_begins"] is True
    assert attempt_scope["automatic_retry_allowed"] is False
    assert attempt_scope["provider_switch_allowed"] is False
    assert attempt_scope["automatic_pexels_to_ai_fallback"] is False
    assert attempt_scope["external_ai_video_fallback"] is False
    for stage in attempt_scope["stages"]:
        cap = stage.get("attempt_cap", stage.get("attempt_cap_per_scene"))
        if stage.get("provider") in {
            "elevenlabs",
            "forced_alignment",
            "pexels_api",
            "google_gemini_image",
            "google_veo",
        }:
            assert isinstance(cap, int) and cap == 1
    for scene in attempt_scope["scene_routes"]:
        assert scene["attempt_cap"] in {0, 1}
        assert scene["idempotency_ref"].startswith("provider-plan://")

    cost_scope = result["cost_scope"]
    approved_cost = cost_version.content
    assert cost_scope["cost_estimate_snapshot"] == revised["cost_estimate_snapshot"]
    assert cost_scope["currency"] == approved_cost["currency"] == "USD"
    assert cost_scope["estimated_cost"] == approved_cost["estimated_cost"]
    assert cost_scope["hard_cap"] == approved_cost["hard_cap"]
    assert cost_scope["catalog_bindings"] == approved_cost["catalog_bindings"]
    assert cost_scope["actual_cost"] is None
    assert cost_scope["approval_amount"] == approved_cost["hard_cap"]
    assert cost_scope["attempt_caps_bound"] is True

    destination = result["destination"]
    assert destination["platform"] == "YOUTUBE"
    assert destination["channel_handle"] == "@SmallTeamAI"
    assert destination["target_market"] == "US"
    assert destination["platform_channel_id"] is None
    assert destination["destination_status"] == "PENDING_PLATFORM_ID"
    assert destination["MR1_RENDER_DESTINATION_GATE"] == "PASS"
    assert destination["PUBLISH_DESTINATION_GATE"] == ("BLOCKED_PENDING_PLATFORM_ID")
    assert result["PUBLISH_DESTINATION_STATUS"] == "PENDING_PLATFORM_ID"
    assert result["PUBLISH_EXECUTION_READY"] is False

    final_media_policy = result["human_and_final_media_policy"]
    assert final_media_policy["technical_media_qc_pass_required"] is True
    assert (
        final_media_policy["creative_perceptual_media_qc_operator_acceptance_required"]
        is True
    )
    assert final_media_policy["exact_final_mp4_hash_review_required"] is True
    assert final_media_policy["drive_archive_verified_required"] is True
    assert final_media_policy["rights_and_provenance_complete_required"] is True
    assert final_media_policy["pre_human_pass_media_authority"] == (
        "REVIEW_MEDIA_CANDIDATE_ONLY"
    )
    assert final_media_policy["final_media_ref_created"] is False
    assert final_media_policy["final_media_ref_before_human_pass_allowed"] is False
    assert final_media_policy["final_media_ref_before_drive_verified_allowed"] is False
    assert final_media_policy["publish_approved"] is False

    approval = db_session.get(
        ApprovalDecision, uuid.UUID(result["approval_decision_id"])
    )
    approval_receipt = db_session.get(
        ArtifactVersion,
        uuid.UUID(result["approval_receipt_artifact_version_id"]),
    )
    readiness = db_session.get(
        ArtifactVersion, uuid.UUID(result["readiness_artifact_version_id"])
    )
    assert approval is not None
    assert approval_receipt is not None and readiness is not None
    assert approval.target_artifact_version_id == package_version.id
    assert approval.decision == "approved"
    assert approval.metadata_["approval_scope"] == APPROVAL_SCOPE
    assert approval.metadata_["decision_source"] == "OPERATOR"
    assert approval.metadata_["approval_purpose"] == ("MR1_REAL_PRODUCTION_EXECUTION")
    assert approval.metadata_["execution_mode"] == "REAL_APPROVED_PRODUCTION"
    assert approval.metadata_["single_run"] is True
    assert approval.metadata_["publish_execution_authorized"] is False
    assert approval_receipt.content_hash == result["approval_receipt_content_hash"]
    frozen_approval_authority = deepcopy(
        approval_receipt.content["approval_decision_authority"]
    )
    frozen_approval_authority_hash = frozen_approval_authority.pop("content_hash")
    assert frozen_approval_authority_hash == content_hash(frozen_approval_authority)
    assert approval.metadata_ == frozen_approval_authority["metadata"]
    assert approval.decision_basis == frozen_approval_authority["decision_basis"]
    assert approval.evidence_basis == frozen_approval_authority["evidence_basis"]
    assert approval.policy_basis == frozen_approval_authority["policy_basis"]
    approval_payload_hash = approval_receipt.content["approval_content_hash"]
    assert len(approval_payload_hash) == 64
    assert all(character in "0123456789abcdef" for character in approval_payload_hash)
    receipt_text = json.dumps(approval_receipt.content, sort_keys=True)
    assert pending["revision_hash"] in receipt_text
    assert provider_version.content_hash in receipt_text
    assert cost_version.content_hash in receipt_text
    assert receipt_version.content_hash in receipt_text
    assert readiness.content["MR1_RENDER_DESTINATION_GATE"] == "PASS"
    assert readiness.content["PUBLISH_DESTINATION_GATE"] == (
        "BLOCKED_PENDING_PLATFORM_ID"
    )
    assert readiness.content["provider_calls"] == 0
    assert readiness.content["render_calls"] == 0
    assert readiness.content["drive_calls"] == 0
    assert readiness.content["youtube_calls"] == 0


@pytest.mark.parametrize(
    "tamper_case",
    (
        "publish_execution_authorized",
        "prohibited_operations",
        "provider_attempt_scope",
        "decision_verdict",
    ),
)
def test_mr1_reapproval_read_rejects_mutated_approval_decision_authority(
    db_session, tmp_path, tamper_case: str
) -> None:
    _, _, _, command, _ = _approved_revision(db_session, tmp_path)
    service = MR1ReapprovalService(db_session)
    approved = service.approve(command)
    approval = db_session.get(
        ApprovalDecision, uuid.UUID(approved["approval_decision_id"])
    )
    assert approval is not None

    if tamper_case == "publish_execution_authorized":
        mutated = deepcopy(approval.metadata_)
        mutated["publish_execution_authorized"] = True
        approval.metadata_ = mutated
    elif tamper_case == "prohibited_operations":
        mutated = deepcopy(approval.policy_basis)
        mutated["prohibited_operations"].remove("YOUTUBE_UPLOAD")
        approval.policy_basis = mutated
    elif tamper_case == "provider_attempt_scope":
        mutated = deepcopy(approval.evidence_basis)
        mutated["provider_attempt_scope"]["automatic_retry_allowed"] = True
        approval.evidence_basis = mutated
    else:
        mutated = deepcopy(approval.decision_basis)
        mutated[PASS_VERDICTS[0]] = "FAIL"
        approval.decision_basis = mutated
    db_session.flush()

    with pytest.raises(
        ValidationFailureError,
        match="MR1_APPROVAL_DECISION_AUTHORITY_ROW_MISMATCH",
    ):
        service.read_approval(command.project_id)


def test_mr1_reapproval_hash_mismatch_blocks_without_partial_approval(
    db_session, tmp_path
) -> None:
    _, pending, _, command, _ = _approved_revision(db_session, tmp_path)
    project_id = command.project_id
    provider_ref = pending["package"]["revised_artifacts"]["provider_execution_plan"]
    provider_version = _artifact_version(db_session, provider_ref)
    # Simulate a corrupt read without attempting to UPDATE the immutable
    # ArtifactVersion row (the database correctly rejects such updates).
    set_committed_value(provider_version, "content_hash", "0" * 64)
    before = _mr1_counts(db_session, project_id)

    with pytest.raises(ValidationFailureError, match="HASH|MISMATCH"):
        MR1ReapprovalService(db_session).approve(command)

    assert (
        _mr1_counts(db_session, project_id)
        == before
        == {
            "artifacts": 0,
            "approvals": 0,
        }
    )


@pytest.mark.parametrize("tamper_case", ("legacy_drive_plan", "stale_drive_cost"))
def test_mr1_reapproval_rejects_incomplete_drive_finalization_authority_without_partial_approval(
    db_session, tmp_path, monkeypatch, tamper_case: str
) -> None:
    if tamper_case == "legacy_drive_plan":
        original_provider_plan = PKG1MarketRevisionService._provider_plan

        def legacy_provider_plan(**kwargs):
            payload = original_provider_plan(**kwargs)
            drive = next(
                item
                for item in payload["stages"]
                if item["provider"] == "google_drive"
            )
            drive["planned_requests"] = 1
            drive.pop("operation", None)
            drive.pop("idempotency_phases", None)
            return payload

        monkeypatch.setattr(
            PKG1MarketRevisionService,
            "_provider_plan",
            staticmethod(legacy_provider_plan),
        )
        expected_error = "MR1_DRIVE_MUTATION_PHASE_AUTHORITY_INVALID"
    else:
        original_cost_estimate = PKG1MarketRevisionService._cost_estimate

        def stale_drive_cost(self, **kwargs):
            payload = original_cost_estimate(self, **kwargs)
            drive = next(
                item
                for item in payload["line_items"]
                if item["provider"] == "google_drive"
            )
            drive["planned_requests"] = 1
            drive.pop("idempotency_phases", None)
            return payload

        monkeypatch.setattr(
            PKG1MarketRevisionService,
            "_cost_estimate",
            stale_drive_cost,
        )
        expected_error = "MR1_COST_OUTPUT_COUNT_MISMATCH"

    _, _, _, command, revision_service = _approved_revision(db_session, tmp_path)
    before = _mr1_counts(db_session, command.project_id)
    execution_before = revision_service._no_execution_counts()
    external_before = _external_counts(db_session)

    with pytest.raises(ValidationFailureError, match=expected_error):
        MR1ReapprovalService(db_session).approve(command)

    assert _mr1_counts(db_session, command.project_id) == before
    assert revision_service._no_execution_counts() == execution_before
    assert _external_counts(db_session) == external_before


def test_mr1_reapproval_is_idempotent_and_supersession_is_append_only(
    db_session, tmp_path
) -> None:
    historical, pending, _, command, revision_service = _approved_revision(
        db_session, tmp_path
    )
    old_approval_id = uuid.UUID(
        pending["package"]["old_mr1_approval"]["approval_decision_id"]
    )
    old_approval = db_session.get(ApprovalDecision, old_approval_id)
    assert old_approval is not None
    old_approval_before = {
        "id": old_approval.id,
        "decision": old_approval.decision,
        "target": old_approval.target_artifact_version_id,
        "metadata": deepcopy(old_approval.metadata_),
        "decision_basis": deepcopy(old_approval.decision_basis),
        "evidence_basis": deepcopy(old_approval.evidence_basis),
        "policy_basis": deepcopy(old_approval.policy_basis),
        "created_at": old_approval.created_at,
    }
    historical_artifact_count = db_session.scalar(
        select(func.count())
        .select_from(Artifact)
        .where(Artifact.video_project_id == historical.video_project_id)
    )
    execution_before = revision_service._no_execution_counts()
    external_before = _external_counts(db_session)

    service = MR1ReapprovalService(db_session)
    first = service.approve(command)
    counts_after_first = _mr1_counts(db_session, command.project_id)
    read_model = service.read_approval(command.project_id)
    second = service.approve(command)

    for key in (
        "approval_decision_id",
        "approval_ref",
        "approval_receipt_artifact_version_id",
        "approval_receipt_content_hash",
        "readiness_artifact_version_id",
        "readiness_content_hash",
        "supersession_artifact_version_id",
        "supersession_content_hash",
    ):
        assert read_model[key] == first[key]
        assert second[key] == first[key]
    assert (
        _mr1_counts(db_session, command.project_id)
        == counts_after_first
        == {
            "artifacts": 3,
            "approvals": 1,
        }
    )

    supersession = db_session.get(
        ArtifactVersion,
        uuid.UUID(first["supersession_artifact_version_id"]),
    )
    assert supersession is not None
    assert supersession.content_hash == first["supersession_content_hash"]
    supersession_text = json.dumps(supersession.content, sort_keys=True)
    assert str(old_approval_id) in supersession_text
    assert "SUPERSEDED" in supersession_text
    assert "PRESERVED" in supersession_text

    old_approval_after = db_session.get(ApprovalDecision, old_approval_id)
    assert old_approval_after is not None
    assert {
        "id": old_approval_after.id,
        "decision": old_approval_after.decision,
        "target": old_approval_after.target_artifact_version_id,
        "metadata": old_approval_after.metadata_,
        "decision_basis": old_approval_after.decision_basis,
        "evidence_basis": old_approval_after.evidence_basis,
        "policy_basis": old_approval_after.policy_basis,
        "created_at": old_approval_after.created_at,
    } == old_approval_before
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(Artifact.video_project_id == historical.video_project_id)
        )
        == historical_artifact_count
    )
    assert revision_service._no_execution_counts() == execution_before
    assert _external_counts(db_session) == external_before
    assert first["no_execution_counts_before"] == execution_before
    assert first["no_execution_counts_after"] == execution_before
    assert second["no_execution_counts_before"] == execution_before
    assert second["no_execution_counts_after"] == execution_before
