from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.ofv0 import FormatIdentityContractDraftRequest
from app.core.errors import ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    CloudMediaRef,
    ChannelWorkspace,
    FinalMediaRef,
    HumanUploadTask,
    MediaRenderJob,
    MediaOffloadJob,
    PaidProviderCallLedger,
    ProviderJobSnapshot,
    ProviderAttempt,
    ReviewTask,
    UploadedVideo,
    User,
    VideoProject,
)
from app.main import create_app
from app.services import (
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    RBACService,
)
from app.services.ofv0 import FormatIdentityContractService
from app.services.pkg1 import FALLBACK_TOPIC, PKG1PackageService


ROOT = Path(__file__).resolve().parents[1]
APPROVAL_REF = "operator-approval://ch1-flex/small-team-ai/profile-v1"


def _scope(session, tmp_path: Path):
    ConfigRegistryService(session).seed([ROOT / "config"])
    company = CompanyService(session).create_company(name="PKG1", slug="pkg1")
    operator = User(email="pkg1@example.com", display_name="PKG1 operator", status="active")
    session.add(operator)
    session.flush()
    RBACService(session).assign_role(user_id=operator.id, role_key="operator", company_id=company.id)
    channel = ChannelWorkspaceService(session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(
            key="small-team-ai",
            name="Small Team AI",
            primary_language="en",
            primary_region="US",
            target_market="US",
        ),
    )
    profile = ChannelProfileService(session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage", created_by=operator.id),
    )
    contract = FormatIdentityContractService(session).draft(
        FormatIdentityContractDraftRequest(
            channel_id=channel.id,
            channel_profile_version_id=profile.id,
            created_by="ChannelAuthorityAgent",
        )
    )
    FormatIdentityContractService(session).approve(contract.id, decided_by="human-operator")
    compiled = ChannelProfileCompiler(session).compile(
        profile_version_id=profile.id,
        correlation_id="pkg1-ch1-compile",
    )
    service = ChannelProfileService(session)
    service.approve_profile_version(
        profile_version_id=profile.id,
        approved_by=operator.id,
        approval_ref=APPROVAL_REF,
    )
    snapshot = service.activate_snapshot(snapshot_id=compiled.snapshot_id)
    report_path = tmp_path / "ch1_summary.json"
    report_path.write_text(
        json.dumps(
            {
                "verdicts": {"CH1_FLEX_FINAL": "PASS", "PROCEED_TO_PKG1": True},
                "runtime": {
                    "channel_id": str(channel.id),
                    "compiled_policy_snapshot_id": str(snapshot.id),
                },
            }
        ),
        encoding="utf-8",
    )
    return company, operator, channel, profile, snapshot, report_path


def _external_counts(session) -> dict[str, int]:
    return {
        "provider_attempts": session.scalar(select(func.count()).select_from(ProviderAttempt)),
        "provider_jobs": session.scalar(select(func.count()).select_from(ProviderJobSnapshot)),
        "paid_calls": session.scalar(select(func.count()).select_from(PaidProviderCallLedger)),
        "render_jobs": session.scalar(select(func.count()).select_from(MediaRenderJob)),
        "final_media": session.scalar(select(func.count()).select_from(FinalMediaRef)),
        "upload_tasks": session.scalar(select(func.count()).select_from(HumanUploadTask)),
        "uploaded_videos": session.scalar(select(func.count()).select_from(UploadedVideo)),
        "media_offload_jobs": session.scalar(select(func.count()).select_from(MediaOffloadJob)),
        "cloud_media_refs": session.scalar(select(func.count()).select_from(CloudMediaRef)),
    }


def test_pkg1_requires_ch1_pass_and_builds_frozen_provider_free_package(db_session, tmp_path) -> None:
    company, operator, channel, profile, snapshot, report_path = _scope(db_session, tmp_path)
    blocked_report = tmp_path / "blocked.json"
    blocked_report.write_text(
        json.dumps({"verdicts": {"CH1_FLEX_FINAL": "FAIL", "PROCEED_TO_PKG1": False}}),
        encoding="utf-8",
    )
    blocked = PKG1PackageService(db_session, ch1_report_path=blocked_report)
    assert blocked.entry_status(channel.id)["status"] == "BLOCKED"
    with pytest.raises(ValidationFailureError, match="PKG1 entry blocked"):
        blocked.build_first_package(channel_id=channel.id, created_by_user_id=operator.id)

    before = _external_counts(db_session)
    service = PKG1PackageService(db_session, ch1_report_path=report_path)
    result = service.build_first_package(channel_id=channel.id, created_by_user_id=operator.id)
    assert result.technical_status == "PASS"
    assert result.human_review_state == "PENDING"
    assert result.provider_execution == "DISABLED"
    assert result.selected_topic == FALLBACK_TOPIC
    assert result.used_fallback_topic is True
    assert _external_counts(db_session) == before

    project = db_session.get(VideoProject, result.video_project_id)
    assert project is not None
    assert project.company_id == company.id
    assert project.channel_workspace_id == channel.id
    assert project.policy_snapshot_id == snapshot.id
    assert project.channel_profile_version_id == profile.id
    refs = snapshot.compiled_payload["snapshot_refs"]
    assert project.native_render_policy_snapshot_hash == refs["native_render_policy"]["content_hash"]
    assert project.creative_quality_policy_hash == refs["creative_quality_policy"]["content_hash"]
    assert project.provider_usage_policy_hash == refs["provider_usage_policy"]["content_hash"]
    assert project.budget_policy_hash == refs["budget_policy"]["content_hash"]
    assert project.format_identity_contract_hash == refs["format_identity_contract"]["content_hash"]

    # A later active-profile pointer must not alter the package read path or frozen project lineage.
    channel.active_policy_snapshot_id = snapshot.id
    db_session.flush()
    package = service.read_package(project.id)
    assert package["snapshot_lineage"]["compiled_channel_policy_snapshot_id"] == str(snapshot.id)
    assert package["technical_status"] == "PASS"
    assert package["human_review_state"] == "PENDING"
    assert package["unresolved_blockers"] == []

    claims = package["artifact_versions"]["claim_evidence_ledger"]["content"]
    scenario = claims["claims"][0]
    assert scenario["claim_type"] == "ILLUSTRATIVE_SCENARIO"
    assert scenario["calculation"] == "5 people * 1 hour/person/day * 4 days = 20 hours"
    assert "universal" not in scenario["allowed_wording"].lower()
    assert "guarante" not in scenario["allowed_wording"].lower()
    assert scenario["verification_state"] == "ILLUSTRATIVE_ONLY"

    script = package["artifact_versions"]["script"]["content"]
    spoken = package["artifact_versions"]["spoken_text_normalized"]["content"]
    normalizer = PKG1PackageService._normalize_script
    from app.contracts.channel_policy import ChannelScopedPolicy
    from app.contracts.pkg1 import PKG1EditorialScript

    policy = ChannelScopedPolicy.model_validate(snapshot.compiled_payload["channel_scoped_policy"])
    rerun = normalizer(PKG1EditorialScript.model_validate(script), policy).model_dump(mode="json")
    assert rerun == spoken
    assert len(spoken["mappings"]) == len(script["segments"])
    assert spoken["provider_timing_created"] is False

    pacing = package["artifact_versions"]["narration_pacing_preflight_estimate"]["content"]
    assert pacing["name"] == "NarrationPacingPreflightEstimate"
    assert pacing["advisory_only"] is True
    assert pacing["canonical_timing_authority"] is False
    assert pacing["decision"] == "ADVISORY_PASS"

    visual_contract = package["artifact_versions"]["visual_direction_contract"]["content"]
    visual_plan = package["artifact_versions"]["visual_plan"]["content"]
    assert visual_contract["contract_hash"] == project.format_identity_contract_hash
    assert visual_contract["stock_is_factual_evidence"] is False
    assert visual_contract["ai_hero_is_filler"] is False
    assert visual_plan["coverage"]["covered_segment_count"] == len(script["segments"])
    assert visual_plan["visual_direction_contract_hash"] == package["artifact_versions"]["visual_direction_contract"]["content_hash"]
    assert visual_plan["canonical_timestamps_created"] is False
    assert all(scene["canonical_timestamps"] is None for scene in visual_plan["scenes"])

    assets = package["artifact_versions"]["compiled_asset_request_plan"]["content"]
    assert assets["selected_provider_assets"] == []
    assert assets["visual_direction_contract_hash"] == package["artifact_versions"]["visual_direction_contract"]["content_hash"]
    assert assets["raw_provider_urls"] == []
    assert assets["ai_hero_asset_request_drafts"] == []
    assert assets["fixed_duration_fit_decision"]["reason"].startswith("Veo would be filler")

    captions = package["artifact_versions"]["caption_plan"]["content"]
    assert captions["final_cues"] == []
    assert captions["srt"] is None
    artifact_types = set(db_session.scalars(select(Artifact.artifact_type).where(Artifact.video_project_id == project.id)).all())
    assert "narration_timeline" not in artifact_types
    assert "caption_track" not in artifact_types
    assert "render_spec" not in artifact_types

    cost = package["artifact_versions"]["cost_estimate_snapshot"]["content"]
    assert "config://media_provider_budget_policy_catalog/1.0.1" in cost["catalog_refs"]
    assert cost["actual_cost"] is None
    assert cost["estimated_cost"] <= cost["hard_cap"]
    provider = service.provider_execution_plan(project.id)
    assert provider["provider_execution"] == "DISABLED"
    assert provider["technical_status"] == "PASS"
    assert provider["unresolved_blockers"] == []
    assert provider["plan"]["execution_enabled"] is False
    assert provider["plan"]["mr1_approval"] == "PENDING"
    assert db_session.scalar(select(func.count()).select_from(ReviewTask).where(ReviewTask.video_project_id == project.id, ReviewTask.status == "open")) == 1

    post = {item["gate_key"]: item["result"] for item in package["gate_results"]}
    assert post["NarrationPacingGate"] == "NOT_RUN"
    assert post["CaptionAudioSyncGate"] == "NOT_RUN"
    assert post["TechnicalMediaQC"] == "NOT_RUN"
    assert post["HumanWatchabilityReview"] == "NOT_RUN"
    assert set(PKG1PackageService(db_session, ch1_report_path=report_path).production_package_readiness(project.id)) >= {
        "snapshot_lineage",
        "artifact_versions",
        "gate_results",
        "cost_estimate",
        "provider_request_counts",
        "unresolved_blockers",
        "human_review_state",
        "exact_next_action",
    }
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]
    assert {
        "/video-projects/{project_id}/production-package-readiness",
        "/video-projects/{project_id}/pkg1",
        "/video-projects/{project_id}/provider-execution-plan",
    } <= set(paths)

    # Idempotent rerun does not create another real project.
    second = service.build_first_package(channel_id=channel.id, created_by_user_id=operator.id)
    assert second.video_project_id == result.video_project_id
    assert db_session.scalar(select(func.count()).select_from(VideoProject).where(VideoProject.project_type == "PKG1_FIRST_PRODUCTION_PACKAGE")) == 1


def test_pkg1_unsupported_claim_blocks_and_revisions_are_immutable_and_bounded(db_session, tmp_path) -> None:
    _, operator, channel, _, _, report_path = _scope(db_session, tmp_path)
    service = PKG1PackageService(db_session, ch1_report_path=report_path)
    result = service.build_first_package(channel_id=channel.id, created_by_user_id=operator.id)
    project_id = result.video_project_id
    artifact = db_session.scalars(
        select(Artifact).where(
            Artifact.video_project_id == project_id,
            Artifact.artifact_type == "claim_evidence_ledger",
        )
    ).one()
    v1 = db_session.get(ArtifactVersion, artifact.current_version_id)
    v1_content = deepcopy(v1.content)
    invalid = {
        "schema_version": "pkg1.claim-evidence-ledger.v1",
        "claims": [
            {
                "claim_id": "BAD-001",
                "claim_text": "Every small team will save twenty hours.",
                "claim_type": "UNIVERSAL_OUTCOME",
                "source_refs": ["SRC-002"],
                "freshness": "NOT_TIME_SENSITIVE",
                "confidence": "LOW",
                "allowed_wording": "Guaranteed saving.",
                "disallowed_wording": "None.",
                "verification_state": "BLOCKED",
                "assumptions": [],
                "calculation": None,
                "result": None,
                "result_unit": None,
            }
        ],
    }
    assert service.claim_evidence_gate(invalid)[0] == "BLOCK"
    cycle1 = service.revise_artifact_and_rerun(
        project_id=project_id,
        artifact_type="claim_evidence_ledger",
        revised_content=v1_content,
        created_by_user_id=operator.id,
        gate_keys=["ClaimEvidenceGate"],
    )
    cycle2 = service.revise_artifact_and_rerun(
        project_id=project_id,
        artifact_type="claim_evidence_ledger",
        revised_content=v1_content,
        created_by_user_id=operator.id,
        gate_keys=["ClaimEvidenceGate"],
    )
    assert cycle1["revision_cycle"] == 1
    assert cycle2["revision_cycle"] == 2
    assert cycle1["artifact_version_id"] != cycle2["artifact_version_id"]
    db_session.refresh(v1)
    assert v1.content == v1_content
    assert db_session.scalar(select(func.count()).select_from(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact.id)) == 3
    assert service.read_package(project_id)["technical_status"] == "PASS"
    with pytest.raises(ValidationFailureError, match="maximum automatic revision cycles exceeded"):
        service.revise_artifact_and_rerun(
            project_id=project_id,
            artifact_type="claim_evidence_ledger",
            revised_content=v1_content,
            created_by_user_id=operator.id,
            gate_keys=["ClaimEvidenceGate"],
        )
    assert _external_counts(db_session) == {
        "provider_attempts": 0,
        "provider_jobs": 0,
        "paid_calls": 0,
        "render_jobs": 0,
        "final_media": 0,
        "upload_tasks": 0,
        "uploaded_videos": 0,
        "media_offload_jobs": 0,
        "cloud_media_refs": 0,
    }


def test_pkg1_persists_exact_human_approval_and_opens_mr1_without_execution(db_session, tmp_path) -> None:
    _, operator, channel, profile, snapshot, report_path = _scope(db_session, tmp_path)
    service = PKG1PackageService(db_session, ch1_report_path=report_path)
    built = service.build_first_package(channel_id=channel.id, created_by_user_id=operator.id)
    before = _external_counts(db_session)
    approval_ref = "operator-approval://pkg1/small-team-ai/final-package-and-mr1"

    closeout = service.persist_human_approval_and_open_mr1(
        project_id=built.video_project_id,
        decided_by_user_id=operator.id,
        approval_ref=approval_ref,
    )

    assert closeout["PKG1_HUMAN_REVIEW"] == "PASS"
    assert closeout["PKG1_FINAL"] == "PASS"
    assert closeout["MR1_PAID_EXECUTION_APPROVAL"] == "APPROVED"
    assert closeout["MR1_ENTRY"] == "READY"
    assert closeout["MR1_EXECUTION"] == "NOT_STARTED"
    assert closeout["MR1_PROVIDER_CALL_COUNT"] == 0
    assert closeout["PROCEED_TO_MR1"] is True
    assert len(closeout["approval_decision_ids"]) == 8
    assert _external_counts(db_session) == before

    project = db_session.get(VideoProject, built.video_project_id)
    assert project.status == "approved"
    assert project.channel_profile_version_id == profile.id
    assert project.policy_snapshot_id == snapshot.id
    package = service.read_package(project.id)
    assert package["human_review_state"] == "PASS"
    package_version_id = package["artifact_versions"]["package_manifest"]["artifact_version_id"]
    decisions = list(
        db_session.scalars(
            select(ApprovalDecision).where(
                ApprovalDecision.id.in_(closeout["approval_decision_ids"].values())
            )
        ).all()
    )
    assert len(decisions) == 8
    assert all(item.decision == "approved" for item in decisions)
    assert all((item.metadata_ or {})["approval_ref"] == approval_ref for item in decisions)
    assert all(item.target_id == item.target_artifact_version_id for item in decisions)
    assert any(
        (item.metadata_ or {})["approval_scope"] == "PKG1_PACKAGE"
        and str(item.target_artifact_version_id) == package_version_id
        for item in decisions
    )
    assert db_session.scalar(
        select(func.count()).select_from(ReviewTask).where(
            ReviewTask.video_project_id == project.id,
            ReviewTask.status.in_(["open", "in_progress"]),
        )
    ) == 0

    readiness_artifact = db_session.scalars(
        select(Artifact).where(
            Artifact.video_project_id == project.id,
            Artifact.artifact_type == "mr1_readiness_state",
        )
    ).one()
    readiness = db_session.get(ArtifactVersion, readiness_artifact.current_version_id)
    assert readiness.content["video_project_id"] == str(project.id)
    assert readiness.content["pkg1_package"]["artifact_version_id"] == package_version_id
    assert readiness.content["frozen_policy_lineage"]["channel_profile_version_id"] == str(profile.id)
    assert readiness.content["frozen_policy_lineage"]["compiled_channel_policy_snapshot_id"] == str(snapshot.id)
    assert readiness.content["approved_script_artifact_version"]["approval_decision_id"]
    assert readiness.content["spoken_text_normalized_artifact_version"]["artifact_version_id"]
    assert readiness.content["visual_direction_contract"]["content_hash"]
    assert readiness.content["approved_provider_execution_plan"]["execution_enabled"] is False
    assert readiness.content["approved_provider_execution_plan"]["approval_status"] == "APPROVED"
    assert readiness.content["approved_cost_envelope"]["actual_cost"] is None
    assert readiness.content["MR1_RENDER_STATUS"] == "NOT_STARTED"
    assert readiness.content["MR1_HUMAN_REVIEW"] == "PENDING"

    second = service.persist_human_approval_and_open_mr1(
        project_id=project.id,
        decided_by_user_id=operator.id,
        approval_ref=approval_ref,
    )
    assert second["approval_decision_ids"] == closeout["approval_decision_ids"]
    assert second["mr1_readiness_artifact_version_id"] == closeout["mr1_readiness_artifact_version_id"]
    assert db_session.scalar(
        select(func.count()).select_from(ApprovalDecision).where(
            ApprovalDecision.id.in_(closeout["approval_decision_ids"].values())
        )
    ) == 8
    assert _external_counts(db_session) == before
