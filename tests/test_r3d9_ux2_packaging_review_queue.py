from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.workflow import VideoProjectCreate
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ArtifactVersion,
    ChannelProfileVersion,
    CloudMediaRef,
    EffectiveChannelRuntimeContextSnapshot,
    FinalMediaRef,
    FirstScriptedVideoPackage,
    HumanUploadTask,
    MediaRenderJob,
    OperatorAuthSession,
    OperatorUser,
    PaidProviderCallLedger,
    PackagingGateRerunRecord,
    PackagingPatchApplyRun,
    PackagingPatchApprovalDecision,
    PackagingProposedPatch,
    PackagingReviewQueueItem,
    ProviderJobSnapshot,
    ProviderAttempt,
    R3D4GateBatchRun,
    R3D4GateRun,
    RenderRevision,
    UploadedVideo,
    VideoProject,
)
from app.main import create_app
from app.services import EffectiveChannelRuntimeContextCompiler, PublishHandoffLedgerService, R3D1AdminService, VideoProjectService
from app.services.m11_1 import AUTH_COOKIE_NAME, hash_password, hash_session_token
from app.services.r3d3 import stable_hash
from app.services.r3d9_ux2 import (
    PackagingApprovedPatchApplyAndRecheckService,
    PackagingPatchApplyService,
    PackagingPatchApprovalService,
    PackagingPatchRouter,
    PackagingReviewQueueService,
)
from tests.qualification.conftest import QualificationFactory


@pytest.fixture
def qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)


def _base_artifacts() -> dict:
    return {
        "hook_spec": {
            "hook_type": "DIRECT",
            "first_3_seconds_script": "VCOS prepares a manual-only handoff.",
            "first_3_seconds_visual": "Operator review cockpit",
            "promise_made": "VCOS stops before provider calls",
            "payoff_location": "S2",
            "clickbait_risk": "LOW",
        },
        "narration_script": {
            "sentences": [
                {"sentence_id": "S1", "text": "VCOS prepares copy for a human operator."},
                {"sentence_id": "S2", "text": "VCOS stops before provider calls and upload."},
            ]
        },
        "metadata_package": {
            "title": "VCOS manual publish handoff",
            "description": "Copy into YouTube manually after final human review. VCOS does not upload or publish.",
            "subtitle_refs": [{"ref": "subtitle:final", "lifecycle_state": "FINAL"}],
            "disclosure_notes": ["AI-assisted draft, human final approval required."],
            "language": "vi",
            "locale": "vi-VN",
        },
        "thumbnail_brief": {
            "concept": "Operator cockpit",
            "text_overlay": "Manual only",
            "main_subject": "VCOS dashboard",
            "composition": "Clear card",
            "mobile_readability_notes": "Short text.",
        },
        "upload_card_copy": {
            "title": "VCOS manual publish handoff",
            "description": "Copy into YouTube manually after final human review. VCOS does not upload or publish.",
            "checklist_items": ["Copy title", "Copy description", "Upload subtitles"],
        },
    }


def _package_fixture(db_session, qualification_factory, *, publish_window: bool = True) -> dict:
    scope = qualification_factory.channel_scope(name=f"R3D9UX2-{uuid.uuid4().hex[:6]}")
    scope.channel.status = "active"
    scope.channel.primary_timezone = "Asia/Ho_Chi_Minh"
    category = R3D1AdminService(db_session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key=f"r3d9-ux2-{uuid.uuid4().hex[:8]}",
            name="R3D9 UX2",
            default_thumbnail_style_json={"style": "high contrast"},
            default_visual_style_json={"style": "operator cards"},
            character_policy_mode="NO_CHARACTER",
            status="ACTIVE",
            human_approved_at=utc_now(),
        )
    )
    project_read = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            policy_snapshot_id=scope.snapshot.id,
            category_id=category.id,
            title="R3D9 UX2 package review",
            description="Queue fixture",
            created_by_user_id=scope.operator.id,
        )
    )
    project = db_session.get(VideoProject, project_read.id)
    assert project is not None
    effective = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    effective.publish_timing_context_json = {
        "channel_timezone": "Asia/Ho_Chi_Minh",
        "manual_publish_only": True,
        "source_contract_paths": ["publish_timing"],
    }
    if publish_window:
        effective.publish_timing_context_json["configured_publish_window"] = {"windows": [{"day": "MONDAY", "start": "09:00", "end": "11:00"}]}
    effective.thumbnail_style_context_json = {"style": "high contrast"}
    effective.cost_provider_policy_context_json = {"provider_real_execution_enabled": False, "budget_cap": "manual-only"}
    db_session.flush()
    package = FirstScriptedVideoPackage(
        video_project_id=project.id,
        channel_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        compiled_policy_snapshot_id=scope.snapshot.id,
        effective_context_snapshot_id=effective.id,
        effective_context_hash=effective.context_hash,
        package_status="READY_FOR_HUMAN_REVIEW",
        agent_run_refs=[],
        prompt_render_run_refs=[],
        prompt_audit_snapshot_refs=[],
        artifacts=_base_artifacts(),
        limitations=["Human final approval required."],
        risk_limitations_summary={"provider_calls": False, "upload_calls": False},
        next_action="Human final approval required.",
    )
    db_session.add(package)
    db_session.flush()
    return {"scope": scope, "project": project, "effective": effective, "package": package}


def _add_gate_issue(db_session, fx: dict, *, gate_key: str, issue_code: str, status: str = "BLOCK") -> R3D4GateRun:
    batch = R3D4GateBatchRun(
        package_id=fx["package"].id,
        video_project_id=fx["project"].id,
        effective_context_snapshot_id=fx["effective"].id,
        context_hash=fx["effective"].context_hash,
        trigger_agent_key="r3d9-ux2-test",
        status=status,
        hard_block_count=1 if status == "BLOCK" else 0,
        review_required_count=1 if status == "REVIEW_REQUIRED" else 0,
        gate_results_json=[{"gate_key": gate_key, "status": status, "reason_codes": [issue_code]}],
        reducer_decision_json={"decision": status},
    )
    db_session.add(batch)
    db_session.flush()
    gate = R3D4GateRun(
        gate_batch_run_id=batch.id,
        package_id=fx["package"].id,
        video_project_id=fx["project"].id,
        effective_context_snapshot_id=fx["effective"].id,
        gate_key=gate_key,
        status=status,
        severity="HARD_RULE" if status == "BLOCK" else "REVIEW_REQUIRED",
        measurements_json={},
        fail_codes=[issue_code],
        blocking_refs=[],
        checked_artifact_refs=[{"artifact_key": _artifact_for_issue(issue_code)}],
        checked_contract_paths=["packaging_review"],
        evidence_refs=[],
        repair_hint="Create proposed patch.",
        human_readable_summary="Gate issue needs proposed patch.",
    )
    db_session.add(gate)
    db_session.flush()
    return gate


def _add_gate_pass(db_session, fx: dict, *, gate_key: str) -> R3D4GateRun:
    batch = R3D4GateBatchRun(
        package_id=fx["package"].id,
        video_project_id=fx["project"].id,
        effective_context_snapshot_id=fx["effective"].id,
        context_hash=fx["effective"].context_hash,
        trigger_agent_key="r3d9-ux2-test",
        status="PASS",
        hard_block_count=0,
        review_required_count=0,
        gate_results_json=[{"gate_key": gate_key, "status": "PASS", "reason_codes": []}],
        reducer_decision_json={"decision": "PASS"},
    )
    db_session.add(batch)
    db_session.flush()
    gate = R3D4GateRun(
        gate_batch_run_id=batch.id,
        package_id=fx["package"].id,
        video_project_id=fx["project"].id,
        effective_context_snapshot_id=fx["effective"].id,
        gate_key=gate_key,
        status="PASS",
        severity="INFO",
        measurements_json={},
        fail_codes=[],
        blocking_refs=[],
        checked_artifact_refs=[],
        checked_contract_paths=[],
        evidence_refs=[],
        repair_hint=None,
        human_readable_summary="Gate passed after rerun.",
    )
    db_session.add(gate)
    db_session.flush()
    return gate


def _artifact_for_issue(issue_code: str) -> str:
    return {
        "SCRIPT_FORBIDDEN_STYLE_USED": "narration_script",
        "HOOK_PROMISE_MISSING": "hook_spec",
        "HOOK_VISUAL_MISSING": "hook_spec",
        "TITLE_MISSING": "metadata_package",
        "THUMBNAIL_BRIEF_MISSING": "thumbnail_brief",
        "DESCRIPTION_MISSING": "metadata_package",
        "PUBLISH_WINDOW_MISSING": "publish_timing",
        "TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM": "metadata_package",
        "SUBTITLE_REFS_MISSING": "subtitle_package",
    }.get(issue_code, "upload_card_copy")


def _replace_artifacts(package: FirstScriptedVideoPackage, updates: dict) -> None:
    artifacts = deepcopy(package.artifacts)
    for key, value in updates.items():
        if value is None:
            artifacts.pop(key, None)
        else:
            artifacts[key] = value
    package.artifacts = artifacts


def _patch_id_for_issue(queue, issue_code: str) -> uuid.UUID:
    item = next(item for item in queue.items if item.issue_code == issue_code)
    assert item.proposed_patch is not None
    return item.proposed_patch.id


def _operator_session_token(db_session) -> str:
    token = f"test-operator-{uuid.uuid4()}"
    user = OperatorUser(
        email=f"r3d9-ux4-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password("unused"),
        display_name="R3D9 UX4 Reviewer",
        role="REVIEWER",
        status="ACTIVE",
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        OperatorAuthSession(
            user_id=user.id,
            session_token_hash=hash_session_token(token),
            expires_at=utc_now() + timedelta(hours=1),
        )
    )
    return token


def test_gate_results_create_deduped_queue_items_and_human_action_copy(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory)
    _add_gate_issue(db_session, fx, gate_key="HookTruthfulnessGate", issue_code="HOOK_PROMISE_MISSING", status="REVIEW_REQUIRED")
    service = PackagingReviewQueueService(db_session)

    queue = service.build_from_gates(fx["package"].id)
    duplicate = service.build_from_gates(fx["package"].id)

    assert len(queue.items) == 1
    assert len(duplicate.items) == 1
    item = duplicate.items[0]
    assert item.human_readable_title == "Hook thiếu promise rõ ràng"
    assert item.human_readable_fix == "Duyệt patch bổ sung promise và payoff location cho hook."
    assert item.section == "Hook Review"
    assert item.next_action_code == "REVIEW_PROPOSED_PATCH"
    assert item.proposed_patch is not None
    assert item.proposed_patch.patch_type == "HOOK_SPEC"


def test_issue_copy_maps_thumbnail_publish_window_and_title(db_session, qualification_factory) -> None:
    cases = [
        ("ThumbnailTruthfulnessGate", "THUMBNAIL_BRIEF_MISSING", "Thiếu thumbnail brief", "Thumbnail Handoff"),
        ("PublishTimingComplianceGate", "PUBLISH_WINDOW_MISSING", "Thiếu publish window", "Publish Timing"),
        ("script_style_compliance_gate", "SCRIPT_FORBIDDEN_STYLE_USED", "Script dùng style bị cấm", "Script Review"),
        ("VisualHookRelevanceGate", "HOOK_VISUAL_MISSING", "Hook thiếu visual 3 giây đầu", "Hook Review"),
        ("TitlePromiseGate", "TITLE_MISSING", "Thiếu title upload", "Upload Copy"),
        ("DescriptionCompletenessGate", "DESCRIPTION_MISSING", "Thiếu description upload", "Upload Copy"),
        ("TitlePromiseGate", "TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM", "Title đang hứa quá mức", "Upload Copy"),
    ]
    for gate_key, issue_code, title, section in cases:
        fx = _package_fixture(db_session, qualification_factory)
        _add_gate_issue(db_session, fx, gate_key=gate_key, issue_code=issue_code, status="REVIEW_REQUIRED")
        item = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id).items[0]
        assert item.human_readable_title == title
        assert item.section == section


def test_patch_router_reuses_existing_agent_keys() -> None:
    router = PackagingPatchRouter()
    script = router.route(issue_code="SCRIPT_FORBIDDEN_STYLE_USED", gate_key="script_style_compliance_gate")
    hook = router.route(issue_code="HOOK_PROMISE_MISSING", gate_key="HookTruthfulnessGate")
    visual = router.route(issue_code="HOOK_VISUAL_MISSING", gate_key="VisualHookRelevanceGate")
    title = router.route(issue_code="TITLE_MISSING", gate_key="TitlePromiseGate")
    description = router.route(issue_code="DESCRIPTION_MISSING", gate_key="DescriptionCompletenessGate")
    thumbnail = router.route(issue_code="THUMBNAIL_BRIEF_MISSING", gate_key="ThumbnailTruthfulnessGate")

    assert script is not None and script.routed_agent_key in {"ScriptRewriteAgent", "ScriptWriterAgent"}
    assert script.patch_type == "SCRIPT_STYLE_PATCH"
    assert hook is not None and hook.routed_agent_key in {"ScriptRewriteAgent", "ScriptPlanningAgent"}
    assert hook.patch_type == "HOOK_SPEC"
    assert visual is not None and visual.routed_agent_key == "VisualPlanningAgent"
    assert visual.patch_type == "VISUAL_HOOK"
    assert title is not None and title.routed_agent_key == "PublishingMetadataAgent"
    assert title.patch_type == "METADATA"
    assert description is not None and description.routed_agent_key in {"PublishingMetadataAgent", "UploadCardCopyAgent"}
    assert description.patch_type == "METADATA"
    assert thumbnail is not None and thumbnail.routed_agent_key == "ThumbnailBriefAgent"
    assert thumbnail.patch_type == "THUMBNAIL_BRIEF"


@pytest.mark.parametrize(
    ("gate_key", "issue_code", "expected_patch_type", "expected_operation"),
    [
        ("script_style_compliance_gate", "SCRIPT_FORBIDDEN_STYLE_USED", "SCRIPT_STYLE_PATCH", "rewrite_script_style_only"),
        ("HookTruthfulnessGate", "HOOK_PROMISE_MISSING", "HOOK_SPEC", "create_hook_spec_patch"),
        ("VisualHookRelevanceGate", "HOOK_VISUAL_MISSING", "VISUAL_HOOK", "create_visual_hook_patch"),
        ("TitlePromiseGate", "TITLE_MISSING", "METADATA", "create_metadata_title_patch"),
        ("DescriptionCompletenessGate", "DESCRIPTION_MISSING", "METADATA", "create_upload_description_patch"),
        ("ThumbnailTruthfulnessGate", "THUMBNAIL_BRIEF_MISSING", "THUMBNAIL_BRIEF", "create_thumbnail_brief_patch"),
    ],
)
def test_r3d9_ux3_reason_codes_create_ready_human_review_patches(
    db_session,
    qualification_factory,
    gate_key: str,
    issue_code: str,
    expected_patch_type: str,
    expected_operation: str,
) -> None:
    fx = _package_fixture(db_session, qualification_factory)
    if issue_code == "SCRIPT_FORBIDDEN_STYLE_USED":
        fx["effective"].brand_voice_persona_context_json = {"forbidden_style": ["hype bait"]}
        script = deepcopy(fx["package"].artifacts["narration_script"])
        script["sentences"][0]["text"] = "This hype bait automation claim needs calmer wording."
        _replace_artifacts(fx["package"], {"narration_script": script})
    elif issue_code == "HOOK_PROMISE_MISSING":
        hook = deepcopy(fx["package"].artifacts["hook_spec"])
        hook["promise_made"] = None
        hook["payoff_location"] = None
        _replace_artifacts(fx["package"], {"hook_spec": hook})
    elif issue_code == "HOOK_VISUAL_MISSING":
        hook = deepcopy(fx["package"].artifacts["hook_spec"])
        hook["first_3_seconds_visual"] = None
        _replace_artifacts(fx["package"], {"hook_spec": hook, "visual_plan": {}})
    elif issue_code == "TITLE_MISSING":
        metadata = deepcopy(fx["package"].artifacts["metadata_package"])
        upload = deepcopy(fx["package"].artifacts["upload_card_copy"])
        metadata.pop("title", None)
        upload.pop("title", None)
        _replace_artifacts(fx["package"], {"metadata_package": metadata, "upload_card_copy": upload})
    elif issue_code == "DESCRIPTION_MISSING":
        metadata = deepcopy(fx["package"].artifacts["metadata_package"])
        upload = deepcopy(fx["package"].artifacts["upload_card_copy"])
        metadata.pop("description", None)
        upload.pop("description", None)
        _replace_artifacts(fx["package"], {"metadata_package": metadata, "upload_card_copy": upload})
    elif issue_code == "THUMBNAIL_BRIEF_MISSING":
        _replace_artifacts(fx["package"], {"thumbnail_brief": {}})
    db_session.flush()
    _add_gate_issue(db_session, fx, gate_key=gate_key, issue_code=issue_code, status="BLOCK" if issue_code == "SCRIPT_FORBIDDEN_STYLE_USED" else "REVIEW_REQUIRED")

    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)
    item = next(item for item in queue.items if item.gate_key == gate_key and item.issue_code == issue_code)

    assert item.status == "PENDING_HUMAN_REVIEW"
    assert item.next_action_code == "REVIEW_PROPOSED_PATCH"
    assert item.proposed_patch is not None
    assert item.proposed_patch.patch_type == expected_patch_type
    assert item.proposed_patch.status == "READY_FOR_REVIEW"
    assert item.proposed_patch.requires_human_approval is True
    assert item.proposed_patch.proposed_patch_json["operation"] == expected_operation
    assert db_session.scalar(select(func.count()).select_from(PackagingPatchApprovalDecision)) == 0
    assert db_session.scalar(select(func.count()).select_from(PackagingPatchApplyRun)) == 0
    if issue_code == "SCRIPT_FORBIDDEN_STYLE_USED":
        preview = item.proposed_patch.after_preview_json["before_after_preview"]
        assert preview
        assert "hype bait" not in preview[0]["after_text"].lower()
    if issue_code == "HOOK_PROMISE_MISSING":
        hook_patch = item.proposed_patch.proposed_patch_json["hook_spec_patch"]
        assert hook_patch["promise_made"]
        assert hook_patch["payoff_location"]
        assert hook_patch["first_3_seconds_script"]
    if issue_code == "TITLE_MISSING":
        assert len(item.proposed_patch.proposed_patch_json["title_candidates"]) == 3
        assert item.proposed_patch.proposed_patch_json["recommended_title"]
    if issue_code == "DESCRIPTION_MISSING":
        assert item.proposed_patch.proposed_patch_json["description"]
        assert "VCOS does not upload" in item.proposed_patch.proposed_patch_json["description"]
    if issue_code == "THUMBNAIL_BRIEF_MISSING":
        brief = item.proposed_patch.proposed_patch_json["thumbnail_brief"]
        assert brief["concept"]
        assert brief["text_overlay"]
        assert brief["rendered"] is False


def test_r3d9_ux3_blocked_package_stays_upload_task_disabled_with_proposed_patch(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory)
    fx["effective"].brand_voice_persona_context_json = {"forbidden_style": ["hype bait"]}
    script = deepcopy(fx["package"].artifacts["narration_script"])
    script["sentences"][0]["text"] = "This hype bait phrase should be rewritten before upload."
    _replace_artifacts(fx["package"], {"narration_script": script})
    db_session.flush()
    _add_gate_issue(db_session, fx, gate_key="script_style_compliance_gate", issue_code="SCRIPT_FORBIDDEN_STYLE_USED", status="BLOCK")

    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)

    assert queue.review_verdict == "BLOCKED"
    assert queue.upload_task_creation_allowed is False
    assert queue.must_fix_count >= 1
    script_item = next(item for item in queue.items if item.issue_code == "SCRIPT_FORBIDDEN_STYLE_USED")
    assert script_item.proposed_patch is not None
    with pytest.raises(ValidationFailureError, match="PACKAGING_REVIEW_UNRESOLVED"):
        PublishHandoffLedgerService(db_session).create_upload_task_from_package(fx["package"].id)
    assert db_session.scalar(select(func.count()).select_from(HumanUploadTask)) == 0


def test_r3d9_ux3_proposal_generation_has_no_provider_media_upload_youtube_calls(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory)
    _replace_artifacts(fx["package"], {"thumbnail_brief": {}})
    db_session.flush()
    _add_gate_issue(db_session, fx, gate_key="ThumbnailTruthfulnessGate", issue_code="THUMBNAIL_BRIEF_MISSING", status="REVIEW_REQUIRED")

    PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)

    assert db_session.scalar(select(func.count()).select_from(PackagingProposedPatch).where(PackagingProposedPatch.package_id == fx["package"].id)) >= 1
    assert db_session.scalar(select(func.count()).select_from(PackagingPatchApprovalDecision)) == 0
    assert db_session.scalar(select(func.count()).select_from(PackagingPatchApplyRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(ProviderAttempt)) == 0
    assert db_session.scalar(select(func.count()).select_from(MediaRenderJob)) == 0
    assert db_session.scalar(select(func.count()).select_from(FinalMediaRef)) == 0
    assert db_session.scalar(select(func.count()).select_from(UploadedVideo)) == 0
    assert db_session.scalar(select(func.count()).select_from(HumanUploadTask)) == 0


def test_deterministic_publish_timing_patch_does_not_mutate_frozen_context(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory, publish_window=False)
    before_context_hash = stable_hash(fx["effective"].publish_timing_context_json)
    before_profile_count = db_session.scalar(select(func.count()).select_from(ChannelProfileVersion))
    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)

    item = next(item for item in queue.items if item.issue_code == "PUBLISH_WINDOW_MISSING")
    assert item.proposed_patch is not None
    assert item.proposed_patch.status == "READY_FOR_REVIEW"
    assert item.proposed_patch.patch_type == "PUBLISH_TIMING_OVERRIDE"
    assert item.proposed_patch.proposed_patch_json["does_not_mutate"] == [
        "Channel Contract",
        "EffectiveChannelRuntimeContextSnapshot",
        "ChannelProfileVersion",
    ]
    effective_after = db_session.get(EffectiveChannelRuntimeContextSnapshot, fx["effective"].id)
    assert effective_after is not None
    assert stable_hash(effective_after.publish_timing_context_json) == before_context_hash
    assert db_session.scalar(select(func.count()).select_from(ChannelProfileVersion)) == before_profile_count


def test_human_approval_reject_and_request_changes_are_audited_without_apply(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory, publish_window=False)
    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)
    patch_id = next(item.proposed_patch.id for item in queue.items if item.proposed_patch)
    approval = PackagingPatchApprovalService(db_session)

    decision = approval.approve(patch_id, decided_by="operator", rationale="ok")
    assert decision.decision == "APPROVE"
    assert db_session.scalar(select(func.count()).select_from(PackagingPatchApprovalDecision)) == 1

    fx_reject = _package_fixture(db_session, qualification_factory, publish_window=False)
    reject_patch_id = next(item.proposed_patch.id for item in PackagingReviewQueueService(db_session).build_from_gates(fx_reject["package"].id).items if item.proposed_patch)
    rejected = approval.reject(reject_patch_id, decided_by="operator", rationale="too risky")
    blocked_run = PackagingPatchApplyService(db_session).apply(reject_patch_id)
    assert rejected.decision == "REJECT"
    assert blocked_run.apply_status == "BLOCKED"
    assert "PATCH_NOT_APPROVED" in blocked_run.reason_codes_json

    fx_changes = _package_fixture(db_session, qualification_factory, publish_window=False)
    change_patch_id = next(item.proposed_patch.id for item in PackagingReviewQueueService(db_session).build_from_gates(fx_changes["package"].id).items if item.proposed_patch)
    changes = approval.request_changes(change_patch_id, decided_by="operator", rationale="need narrower window")
    blocked_changes = PackagingPatchApplyService(db_session).apply(change_patch_id)
    assert changes.decision == "REQUEST_CHANGES"
    assert blocked_changes.apply_status == "BLOCKED"
    assert db_session.scalar(select(func.count()).select_from(ArtifactVersion)) == 0


def test_approved_patch_creates_versioned_artifact_and_gate_rerun_record(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory, publish_window=False)
    before_artifacts_hash = stable_hash(fx["package"].artifacts)
    before_context_id = fx["package"].effective_context_snapshot_id
    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)
    patch_id = next(item.proposed_patch.id for item in queue.items if item.proposed_patch)

    PackagingPatchApprovalService(db_session).approve(patch_id, decided_by="operator", rationale="manual override")
    run = PackagingPatchApplyService(db_session).apply(patch_id)

    assert run.apply_status == "APPLIED"
    assert run.created_artifact_ref and run.created_artifact_ref.startswith("artifact_version:")
    assert run.created_handoff_override_ref and run.created_handoff_override_ref.startswith("artifact_version:")
    assert db_session.scalar(select(func.count()).select_from(ArtifactVersion)) == 1
    assert db_session.scalar(select(func.count()).select_from(PackagingPatchApplyRun).where(PackagingPatchApplyRun.apply_status == "APPLIED")) == 1
    rerun = db_session.scalars(select(PackagingGateRerunRecord).where(PackagingGateRerunRecord.proposed_patch_id == patch_id)).one()
    assert rerun.gate_keys_json == ["PublishTimingComplianceGate"]
    assert rerun.rerun_status == "REVIEW_REQUIRED"
    package_after = db_session.get(FirstScriptedVideoPackage, fx["package"].id)
    assert package_after is not None
    assert stable_hash(package_after.artifacts) == before_artifacts_hash
    assert package_after.effective_context_snapshot_id == before_context_id


def test_upload_task_creation_is_gated_by_unresolved_queue_and_gate_status(db_session, qualification_factory) -> None:
    blocked_fx = _package_fixture(db_session, qualification_factory)
    _add_gate_issue(db_session, blocked_fx, gate_key="TitlePromiseGate", issue_code="TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM")
    PackagingReviewQueueService(db_session).build_from_gates(blocked_fx["package"].id)
    with pytest.raises(ValidationFailureError, match="PACKAGING_REVIEW_UNRESOLVED"):
        PublishHandoffLedgerService(db_session).create_upload_task_from_package(blocked_fx["package"].id)

    allowed_fx = _package_fixture(db_session, qualification_factory)
    task = PublishHandoffLedgerService(db_session).create_upload_task_from_package(allowed_fx["package"].id)
    assert task.status == "READY_FOR_HUMAN_UPLOAD"
    assert db_session.scalar(select(func.count()).select_from(HumanUploadTask)) == 1


def test_latest_gate_pass_closes_queue_item_and_allows_manual_upload(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory)
    _add_gate_issue(db_session, fx, gate_key="TitlePromiseGate", issue_code="TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM")
    first = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)
    assert first.review_verdict == "BLOCKED"

    _add_gate_pass(db_session, fx, gate_key="TitlePromiseGate")
    second = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)

    assert second.items[0].status == "CLOSED"
    assert second.upload_task_creation_allowed is True


def test_no_provider_media_upload_youtube_or_channel_contract_mutation(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory, publish_window=False)
    before_profile_id = fx["package"].channel_profile_version_id
    before_context_hash = fx["effective"].context_hash
    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)
    patch_id = next(item.proposed_patch.id for item in queue.items if item.proposed_patch)
    PackagingPatchApprovalService(db_session).approve(patch_id, decided_by="operator")
    PackagingPatchApplyService(db_session).apply(patch_id)

    assert db_session.scalar(select(func.count()).select_from(ProviderAttempt)) == 0
    assert db_session.scalar(select(func.count()).select_from(MediaRenderJob)) == 0
    assert db_session.scalar(select(func.count()).select_from(FinalMediaRef)) == 0
    assert db_session.scalar(select(func.count()).select_from(UploadedVideo)) == 0
    package = db_session.get(FirstScriptedVideoPackage, fx["package"].id)
    effective = db_session.get(EffectiveChannelRuntimeContextSnapshot, fx["effective"].id)
    assert package is not None and package.channel_profile_version_id == before_profile_id
    assert effective is not None and effective.context_hash == before_context_hash
    route_paths = [getattr(route, "path", "") for route in create_app().routes]
    assert not any("youtube-upload" in path or "upload-youtube" in path for path in route_paths)
    source = Path("app/services/r3d9_ux2.py").read_text(encoding="utf-8").lower()
    assert "googledriveuploadservice" not in source
    assert "youtubeupload" not in source


def test_apply_approved_changes_endpoint_blocks_when_no_patch_is_approved(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory, publish_window=False)
    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)
    assert queue.approved_patch_count == 0
    assert queue.ready_for_review_patch_count >= 1
    assert queue.can_apply_approved_changes is False
    assert queue.apply_approved_changes_disabled_reason == "Chưa có patch được duyệt"
    token = _operator_session_token(db_session)
    db_session.commit()
    client = TestClient(create_app())
    client.cookies.set(AUTH_COOKIE_NAME, token)

    response = client.post(f"/video-packages/{fx['package'].id}/apply-approved-changes-and-recheck")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "BLOCKED_WAITING_HUMAN_APPROVAL"
    assert payload["applied_patch_ids"] == []
    assert payload["gate_rerun_record_ids"] == []
    assert payload["no_provider_media_upload_execution"] is True
    assert db_session.scalar(select(func.count()).select_from(PackagingPatchApplyRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(PackagingGateRerunRecord)) == 0


def test_apply_approved_changes_blocks_when_any_patch_remains_ready_for_review(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory)
    _add_gate_issue(db_session, fx, gate_key="HookTruthfulnessGate", issue_code="HOOK_PROMISE_MISSING", status="REVIEW_REQUIRED")
    _add_gate_issue(db_session, fx, gate_key="ThumbnailTruthfulnessGate", issue_code="THUMBNAIL_BRIEF_MISSING", status="REVIEW_REQUIRED")
    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)
    approved_patch_id = _patch_id_for_issue(queue, "HOOK_PROMISE_MISSING")
    PackagingPatchApprovalService(db_session).approve(approved_patch_id, decided_by="operator")

    result = PackagingApprovedPatchApplyAndRecheckService(db_session).apply_and_recheck(fx["package"].id)

    assert result.status == "BLOCKED_PENDING_HUMAN_DECISIONS"
    assert result.applied_patch_ids == []
    assert result.gate_rerun_record_ids == []
    assert db_session.scalar(select(func.count()).select_from(ArtifactVersion)) == 0
    assert db_session.scalar(select(func.count()).select_from(PackagingGateRerunRecord)) == 0


def test_apply_approved_changes_applies_only_approved_and_skips_rejected_or_request_changes(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory)
    _add_gate_issue(db_session, fx, gate_key="HookTruthfulnessGate", issue_code="HOOK_PROMISE_MISSING", status="REVIEW_REQUIRED")
    _add_gate_issue(db_session, fx, gate_key="ThumbnailTruthfulnessGate", issue_code="THUMBNAIL_BRIEF_MISSING", status="REVIEW_REQUIRED")
    _add_gate_issue(db_session, fx, gate_key="DescriptionCompletenessGate", issue_code="DESCRIPTION_MISSING", status="REVIEW_REQUIRED")
    before_context_hash = fx["effective"].context_hash
    before_profile_count = db_session.scalar(select(func.count()).select_from(ChannelProfileVersion))
    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)
    approved_patch_id = _patch_id_for_issue(queue, "HOOK_PROMISE_MISSING")
    rejected_patch_id = _patch_id_for_issue(queue, "THUMBNAIL_BRIEF_MISSING")
    request_changes_patch_id = _patch_id_for_issue(queue, "DESCRIPTION_MISSING")
    approval = PackagingPatchApprovalService(db_session)
    approval.approve(approved_patch_id, decided_by="operator")
    approval.reject(rejected_patch_id, decided_by="operator")
    approval.request_changes(request_changes_patch_id, decided_by="operator")

    result = PackagingApprovedPatchApplyAndRecheckService(db_session).apply_and_recheck(fx["package"].id)

    assert result.status == "APPLIED_AND_RECHECKED"
    assert result.applied_patch_ids == [approved_patch_id]
    assert rejected_patch_id in result.skipped_patch_ids
    assert request_changes_patch_id in result.skipped_patch_ids
    assert len(result.gate_rerun_record_ids) == 1
    assert result.upload_task_creation_allowed is False
    assert db_session.scalar(select(func.count()).select_from(ArtifactVersion)) == 1
    assert db_session.scalar(select(func.count()).select_from(PackagingPatchApplyRun).where(PackagingPatchApplyRun.apply_status == "APPLIED")) == 1
    assert db_session.scalar(select(func.count()).select_from(PackagingGateRerunRecord)) == 1
    assert db_session.get(PackagingProposedPatch, approved_patch_id).status == "APPLIED"
    assert db_session.get(PackagingProposedPatch, request_changes_patch_id).status == "REQUEST_CHANGES"
    effective = db_session.get(EffectiveChannelRuntimeContextSnapshot, fx["effective"].id)
    assert effective is not None and effective.context_hash == before_context_hash
    assert db_session.scalar(select(func.count()).select_from(ChannelProfileVersion)) == before_profile_count


def test_apply_approved_changes_duplicate_call_does_not_create_duplicate_versions_or_reruns(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory, publish_window=False)
    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)
    patch_id = next(item.proposed_patch.id for item in queue.items if item.proposed_patch)
    PackagingPatchApprovalService(db_session).approve(patch_id, decided_by="operator")

    first = PackagingApprovedPatchApplyAndRecheckService(db_session).apply_and_recheck(fx["package"].id)
    second = PackagingApprovedPatchApplyAndRecheckService(db_session).apply_and_recheck(fx["package"].id)

    assert first.status == "APPLIED_AND_RECHECKED"
    assert second.status == "NOOP_ALREADY_APPLIED"
    assert second.applied_patch_ids == []
    assert db_session.scalar(select(func.count()).select_from(ArtifactVersion)) == 1
    assert db_session.scalar(select(func.count()).select_from(PackagingPatchApplyRun).where(PackagingPatchApplyRun.apply_status == "APPLIED")) == 1
    assert db_session.scalar(select(func.count()).select_from(PackagingGateRerunRecord)) == 1


def test_apply_approved_changes_rebuilds_queue_and_preserves_no_execution_boundaries(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory, publish_window=False)
    before_profile_id = fx["package"].channel_profile_version_id
    before_context_hash = fx["effective"].context_hash
    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)
    patch_id = next(item.proposed_patch.id for item in queue.items if item.proposed_patch)
    PackagingPatchApprovalService(db_session).approve(patch_id, decided_by="operator")

    result = PackagingApprovedPatchApplyAndRecheckService(db_session).apply_and_recheck(fx["package"].id)
    rebuilt = PackagingReviewQueueService(db_session).read(fx["package"].id)

    assert result.status == "APPLIED_AND_RECHECKED"
    assert rebuilt.applied_patch_count == 1
    assert rebuilt.items[0].status == "GATE_RERUN_REQUIRED"
    assert rebuilt.last_apply_recheck_result is not None
    assert result.upload_task_creation_allowed is False
    assert result.no_execution_proof["no_upload_task_created"] is True
    assert db_session.scalar(select(func.count()).select_from(ProviderAttempt)) == 0
    assert db_session.scalar(select(func.count()).select_from(MediaRenderJob)) == 0
    assert db_session.scalar(select(func.count()).select_from(FinalMediaRef)) == 0
    assert db_session.scalar(select(func.count()).select_from(CloudMediaRef)) == 0
    assert db_session.scalar(select(func.count()).select_from(UploadedVideo)) == 0
    assert db_session.scalar(select(func.count()).select_from(HumanUploadTask)) == 0
    assert db_session.scalar(select(func.count()).select_from(RenderRevision)) == 0
    assert db_session.scalar(select(func.count()).select_from(ProviderJobSnapshot)) == 0
    assert db_session.scalar(select(func.count()).select_from(PaidProviderCallLedger).where(PaidProviderCallLedger.call_status == "EXECUTED")) == 0
    package = db_session.get(FirstScriptedVideoPackage, fx["package"].id)
    effective = db_session.get(EffectiveChannelRuntimeContextSnapshot, fx["effective"].id)
    assert package is not None and package.channel_profile_version_id == before_profile_id
    assert effective is not None and effective.context_hash == before_context_hash


def test_direct_patch_apply_endpoint_still_blocks_non_approved_patch(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory, publish_window=False)
    queue = PackagingReviewQueueService(db_session).build_from_gates(fx["package"].id)
    patch_id = next(item.proposed_patch.id for item in queue.items if item.proposed_patch)
    db_session.commit()
    client = TestClient(create_app())

    response = client.post(f"/packaging-proposed-patches/{patch_id}/apply")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["apply_status"] == "BLOCKED"
    assert "PATCH_NOT_APPROVED" in payload["reason_codes_json"]
    assert db_session.scalar(select(func.count()).select_from(ArtifactVersion)) == 0


def test_packaging_review_queue_api_endpoints(db_session, qualification_factory) -> None:
    fx = _package_fixture(db_session, qualification_factory, publish_window=False)
    db_session.commit()
    client = TestClient(create_app())

    build = client.post(f"/video-packages/{fx['package'].id}/packaging-review-queue/build-from-gates")
    assert build.status_code == 200, build.text
    payload = build.json()
    assert payload["review_verdict"] == "REVIEW_REQUIRED"
    assert payload["upload_task_creation_allowed"] is False
    patch_id = next(item["proposed_patch"]["id"] for item in payload["items"] if item["proposed_patch"])

    approve = client.post(f"/packaging-proposed-patches/{patch_id}/approve", json={"decided_by": "operator", "rationale": "ok"})
    assert approve.status_code == 200, approve.text
    apply = client.post(f"/packaging-proposed-patches/{patch_id}/apply")
    assert apply.status_code == 200, apply.text
    rerun = client.post(f"/video-packages/{fx['package'].id}/rerun-packaging-gates")
    assert rerun.status_code == 200, rerun.text

    get_queue = client.get(f"/video-packages/{fx['package'].id}/packaging-review-queue")
    assert get_queue.status_code == 200, get_queue.text
    assert get_queue.json()["items"][0]["status"] == "GATE_RERUN_REQUIRED"
