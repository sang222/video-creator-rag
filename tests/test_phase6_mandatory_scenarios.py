"""Executable coverage for the mandatory Phase 6 Q1-Q12 scenarios.

The real-render Q3 and Q5 qualifications live in
``test_phase6_archive_qualification.py``.  This module exercises the remaining
authoritative admission/package/review, manual-publish, durability, incident,
and proposal-only boundaries.
"""

from __future__ import annotations

import runpy
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.production_publish import (
    FinalReviewCandidateCreateV2,
    HumanUploadTaskStartV2,
)
from app.contracts.production_package import ProductionPackageContentV2
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.r3d7 import QualityDeltaAttributionRunRequest
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.contracts.vcos_v2 import (
    AssignmentMode,
    ContentMode,
    ProductionLane,
)
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ArtifactVersion,
    DomainEvent,
    MemoryConfidenceUpdateLedger,
)
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.m7 import UploadedVideo
from app.db.models.production_publish import FinalReviewCandidate
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.workflow import VideoProject
from app.services.production_package import ProductionReadinessService
from app.services.production_publish import ProductionPublishService
from app.services.r3d1 import R3D1AdminService
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler
from app.services.r3d5 import ControlledMemoryService
from app.services.r3d7 import QualityDeltaAttributionService
from app.services.vcos_v2 import (
    LongFormPlanningService,
)
from app.services.workflow import ArtifactService
from tests.qualification.conftest import QualificationFactory


ROOT = Path(__file__).resolve().parents[1]
_PHASE2 = runpy.run_path(str(ROOT / "tests/test_phase2_typed_admission.py"))
_PHASE3 = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
_PHASE4 = runpy.run_path(str(ROOT / "tests/test_phase4_durable_orchestration.py"))
_PHASE5 = runpy.run_path(str(ROOT / "tests/test_phase5_final_publish.py"))
_COCKPIT = runpy.run_path(str(ROOT / "tests/test_phase6_operator_cockpit.py"))
_R3D7 = runpy.run_path(str(ROOT / "tests/test_r3d7_closed_learning_retrieval_loop.py"))


def test_q4_long_form_typed_authority_reaches_final_review_ready(
    db_session: Session,
) -> None:
    scope, series_run = _admitted_scope(
        db_session,
        assignment_mode=AssignmentMode.SERIES_REQUIRED,
    )
    candidate, workflow = _create_final_review_ready(db_session, scope)

    assert scope.project.production_lane == ProductionLane.LONG_FORM
    assert scope.project.content_mode == ContentMode.SERIES_EPISODE
    assert workflow.state == "FINAL_REVIEW_READY"
    assert workflow.final_review_candidate_id == candidate.id
    assert candidate.production_lane == ProductionLane.LONG_FORM
    assert candidate.content_mode == ContentMode.SERIES_EPISODE
    assert series_run is not None
    assert scope.project.series_run_id == series_run.id
    assert scope.project.episode_number == 1
    assert series_run.next_episode_number == 2
    assert series_run.reserved_episode_count == 1
    assert series_run.published_episode_count == 0


def test_q6_upload_creates_task_and_verified_uploaded_video(
    db_session: Session,
) -> None:
    ready = _PHASE5["_ready_final"](db_session)
    _result, task = _PHASE5["_decide_upload"](db_session, ready)
    confirmation = _PHASE5["_submit"](db_session, ready, task)
    verified = ProductionPublishService(db_session).verify_confirmation(
        confirmation_id=confirmation.id,
        data=_PHASE5["_verification_data"](confirmation),
        actor=_PHASE5["_actor"](ready.scope),
    )
    assert verified.status == "VERIFIED"
    assert verified.uploaded_video is not None
    assert db_session.scalar(select(func.count()).select_from(UploadedVideo)) == 1


def test_q7_do_not_upload_is_terminal_and_creates_no_task(
    db_session: Session,
) -> None:
    _PHASE5["test_19_do_not_upload_is_terminal"](db_session)


def test_q8_wrong_file_or_destination_fails_closed_without_uploaded_video(
    db_session: Session,
) -> None:
    ready = _PHASE5["_ready_final"](db_session)
    _result, task = _PHASE5["_decide_upload"](db_session, ready)
    service = ProductionPublishService(db_session)

    with pytest.raises(
        ValidationFailureError,
        match="SELECTED_FILE_CHECKSUM_MISMATCH",
    ):
        service.start_upload_task(
            task_id=task.id,
            data=HumanUploadTaskStartV2(
                selected_file_name="final.mp4",
                selected_file_ref=ready.candidate_data.archive_object_ref,
                selected_file_checksum="0" * 64,
                archive_object_ref=ready.candidate_data.archive_object_ref,
            ),
            actor=_PHASE5["_actor"](ready.scope),
        )
    assert task.task_state == "READY_FOR_OPERATOR"

    _PHASE5["_start"](db_session, ready, task)
    confirmation = service.submit_confirmation(
        task_id=task.id,
        data=_PHASE5["_confirmation_data"](destination_binding_id=uuid.uuid4()),
        actor=_PHASE5["_actor"](ready.scope),
    )
    assert confirmation.confirmation_state == "BLOCKED_DESTINATION"
    assert db_session.scalar(select(func.count()).select_from(UploadedVideo)) == 0


@pytest.mark.parametrize(
    "effect_type",
    ["provider_submission", "render_output", "archive_object"],
)
def test_q9_crash_resume_does_not_duplicate_external_effects(
    db_session: Session,
    engine,
    effect_type: str,
) -> None:
    _PHASE4["test_crash_after_effect_before_ack_does_not_duplicate_effect"](
        db_session,
        engine,
        effect_type,
    )


def test_q10_analytics_ready_emits_exactly_once(
    db_session: Session,
) -> None:
    ready = _PHASE5["_ready_final"](db_session)
    _result, task = _PHASE5["_decide_upload"](db_session, ready)
    confirmation = _PHASE5["_submit"](db_session, ready, task)
    verification = _PHASE5["_verification_data"](confirmation)
    service = ProductionPublishService(db_session)
    first = service.verify_confirmation(
        confirmation_id=confirmation.id,
        data=verification,
        actor=_PHASE5["_actor"](ready.scope),
    )
    second = service.verify_confirmation(
        confirmation_id=confirmation.id,
        data=verification,
        actor=_PHASE5["_actor"](ready.scope),
    )
    assert second.uploaded_video.id == first.uploaded_video.id
    counts = dict(
        db_session.execute(
            select(DomainEvent.event_type, func.count())
            .where(
                DomainEvent.event_type.in_(
                    ["UPLOADED_VIDEO_VERIFIED", "ANALYTICS_READY"]
                )
            )
            .group_by(DomainEvent.event_type)
        ).all()
    )
    assert counts == {
        "ANALYTICS_READY": 1,
        "UPLOADED_VIDEO_VERIFIED": 1,
    }


def test_q11_incident_marks_project_learning_excluded(
    db_session: Session,
) -> None:
    _COCKPIT["test_cockpit_surfaces_incident_blocker_and_learning_exclusion"](
        db_session
    )


def test_q12_mature_comparable_learning_is_proposal_only(
    db_session: Session,
    monkeypatch,
) -> None:
    qualification_factory = QualificationFactory(db_session)
    scope, effective, manifest = _R3D7["_attribution_manifest_fixture"](
        db_session,
        qualification_factory,
        monkeypatch,
    )
    facet = ControlledMemoryService(db_session).require_facet(
        uuid.UUID(manifest.memory_facet_ids_used_json[0])
    )
    facet.confidence_label = "LOW"
    profile_hash_before = scope.profile.profile_input_hash
    policy_hash_before = scope.snapshot.content_hash
    db_session.flush()

    result = QualityDeltaAttributionService(db_session).run(
        QualityDeltaAttributionRunRequest(
            source_memory_influence_manifest_id=manifest.id,
            target_video_project_id=effective.video_project_id,
            expected_metric_family="PACKAGING_PATTERN",
            baseline_snapshot_ref={"metrics": {"click_through_rate": 0.02}},
            observed_snapshot_ref={
                "metrics": {"click_through_rate": 0.04},
                "freshness_state": "FRESH",
                "confidence_level": "HIGH",
                "comparable_window": "MATURE",
            },
        )
    )

    assert result.confidence_result == "IMPROVED"
    assert facet.confidence_label == "LOW"
    assert scope.profile.profile_input_hash == profile_hash_before
    assert scope.snapshot.content_hash == policy_hash_before
    ledger = db_session.scalars(
        select(MemoryConfidenceUpdateLedger)
        .where(MemoryConfidenceUpdateLedger.memory_facet_id == facet.id)
        .order_by(MemoryConfidenceUpdateLedger.created_at.desc())
    ).first()
    assert ledger is not None
    assert ledger.requires_human_review is True
    assert "CONFIDENCE_CHANGE_PROPOSAL_ONLY" in ledger.reason_codes_json
    assert "ACTIVE_MEMORY_CONFIDENCE_UNCHANGED" in ledger.reason_codes_json


def _admitted_scope(
    session: Session,
    *,
    assignment_mode: AssignmentMode,
) -> tuple[SimpleNamespace, object | None]:
    authority = _PHASE2["_authority"](session)
    series_plan = None
    series_run = None
    if assignment_mode == AssignmentMode.SERIES_REQUIRED:
        series_plan, series_run = _PHASE2["_series"](
            session,
            authority,
            lane=ProductionLane.LONG_FORM,
        )
    slot = _PHASE2["_slot"](
        session,
        authority,
        lane=ProductionLane.LONG_FORM,
        assignment_mode=assignment_mode,
        preferred_plan_id=series_plan.id if series_plan else None,
        preferred_run_id=series_run.id if series_run else None,
    )
    category = R3D1AdminService(session).create_content_category(
        ContentCategoryCreate(
            company_id=authority.company.id,
            channel_workspace_id=authority.channel.id,
            category_key=f"phase6-{uuid.uuid4().hex[:8]}",
            name="Phase 6 scenario category",
            sub_niche="operator education",
            audience_segment="creator operators",
            content_pillar="education",
            default_format_policy_json={"format": "explainer"},
            default_visual_style_json={"style_note": "clear diagrams"},
            default_voice_style_json={"tone": "calm"},
            default_thumbnail_style_json={"style": "clear text"},
            visual_mode="DIAGRAM_FIRST",
            character_policy_mode="NO_CHARACTER",
            status="ACTIVE",
            human_approved_at=utc_now(),
        )
    )
    slot.category_id = category.id
    session.flush()
    duration = _PHASE2["_duration"](
        authority,
        production_lane=ProductionLane.LONG_FORM,
    )
    preflight = _PHASE2["_preflight"](
        session,
        authority,
        editorial_calendar_slot_id=slot.id,
    )
    request = _PHASE2["_long_request"](
        authority,
        slot,
        preflight,
        assignment_mode=assignment_mode,
        preferred_plan_id=series_plan.id if series_plan else None,
        preferred_run_id=series_run.id if series_run else None,
    ).model_copy(update={"category_id": category.id})
    admission = LongFormPlanningService(session).admit(request)

    assert admission.decision == "ADMIT"
    project = session.get(VideoProject, admission.admitted_video_project_id)
    assert project is not None
    effective = EffectiveChannelRuntimeContextCompiler(session).ensure_for_project(
        project.id,
        editorial_calendar_slot_id=slot.id,
    )
    assert effective.compile_status == "PASS", effective.reason_codes_json
    return (
        SimpleNamespace(
            company=authority.company,
            operator=authority.operator,
            channel=authority.channel,
            profile=authority.profile,
            policy=authority.policy,
            duration=duration,
            admission=admission,
            project=project,
            effective=effective,
        ),
        series_run,
    )


def _create_final_review_ready(
    session: Session,
    scope: SimpleNamespace,
) -> tuple[FinalReviewCandidate, ProductionWorkflowRun]:
    package = _PHASE3["_create_package"](session, scope)
    readiness = ProductionReadinessService(session).evaluate(
        package_artifact_version_id=package.artifact_version_id,
        created_by_user_id=scope.operator.id,
    )
    assert readiness.receipt is not None
    package_version = session.get(ArtifactVersion, package.artifact_version_id)
    assert package_version is not None
    package_content = ProductionPackageContentV2.model_validate(package_version.content)
    destination_version = session.get(
        ArtifactVersion,
        package_content.destination_binding_ref.artifact_version_id,
    )
    assert destination_version is not None
    destination = destination_version.content["destination"]
    checksum = (uuid.uuid4().hex * 2)[:64]
    render_output_ref = f"fixture://phase6/{scope.project.id}/final.mp4"
    drive_file_id = f"phase6-{uuid.uuid4().hex}"
    archive_object_ref = f"drive://{drive_file_id}/final.mp4"
    archive_receipt_hash = "5" * 64
    cloud = CloudMediaRef(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=scope.project.id,
        media_type="LONG_FORM_FINAL",
        storage_provider="GOOGLE_DRIVE",
        drive_file_id=drive_file_id,
        drive_folder_id="phase6-scenario-fixtures",
        web_view_link=(f"https://drive.google.com/file/d/{drive_file_id}/view"),
        mime_type="video/mp4",
        file_name="final.mp4",
        size_bytes=1024,
        checksum_sha256=checksum,
        local_source_path_hash=checksum,
        upload_status="VERIFIED",
        verification_status="CHECKSUM_VERIFIED",
        source_refs=[
            {
                "type": "archive_receipt",
                "ref": (f"drive-receipt://phase6/scenario#{archive_receipt_hash}"),
            }
        ],
        technical_appendix={
            "archive_receipt_hash": archive_receipt_hash,
            "remote_exact_set_verified": True,
        },
    )
    session.add(cloud)
    session.flush()
    lineage_artifact = ArtifactService(session).create_artifact(
        data=ArtifactCreate(
            video_project_id=scope.project.id,
            artifact_type="mr1_final_media_lineage_receipt",
            created_by_user_id=scope.operator.id,
        ),
        correlation_id=f"phase6-lineage-{scope.project.id}",
        trusted_authority_write=True,
    )
    lineage_version = ArtifactService(session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=lineage_artifact.id,
            content={
                "schema_version": "vcos.native-final-media-lineage.v2",
                "video_project_id": str(scope.project.id),
                "production_package_artifact_version_id": str(
                    package.artifact_version_id
                ),
                "production_package_hash": package.canonical_hash,
                "duration_contract": (
                    package_content.duration_contract.model_dump(mode="json")
                ),
                "canonical_media_timeline_hash": "1" * 64,
                "native_render_plan_hash": "2" * 64,
                "render_output_checksum": checksum,
                "technical_qc_hash": "3" * 64,
                "creative_qc_hash": "4" * 64,
                "archive_receipt_hash": archive_receipt_hash,
                "archive_state": "VERIFIED",
                "cloud_media_ref_id": str(cloud.id),
                "file_ref": archive_object_ref,
            },
            status="approved",
            created_by_user_id=scope.operator.id,
        ),
        correlation_id=f"phase6-lineage-version-{scope.project.id}",
        trusted_authority_write=True,
    )
    lineage_artifact.status = "approved"
    session.flush()
    final_media = FinalMediaRef(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=scope.project.id,
        production_package_artifact_version_id=package.artifact_version_id,
        production_package_hash=package.canonical_hash,
        duration_contract=scope.duration.model_dump(mode="json"),
        media_type="LONG_FORM_FINAL",
        file_ref=archive_object_ref,
        duration_seconds=(Decimal(scope.duration.target_duration_ms) / Decimal(1000)),
        aspect_ratio="16:9",
        resolution="1920x1080",
        provider_key="native-ffmpeg",
        provider_type="LOCAL_RENDERER_CAPABILITY",
        checksum_sha256=checksum,
        cloud_media_ref_id=cloud.id,
        lineage_artifact_version_id=lineage_version.id,
    )
    session.add(final_media)
    session.flush()
    destination_id = destination_version.id
    destination_fingerprint = destination_version.content_hash
    admission = scope.admission
    source_id = admission.editorial_calendar_slot_id
    assert source_id is not None
    workflow = ProductionWorkflowRun(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=scope.project.id,
        production_lane=scope.project.production_lane,
        planning_source_type=admission.planning_source_type,
        planning_source_id=source_id,
        planning_source_hash=(uuid.uuid4().hex * 2)[:64],
        workflow_key=(uuid.uuid4().hex * 2)[:64],
        start_input_hash=(uuid.uuid4().hex * 2)[:64],
        state="FINAL_REVIEW_READY",
        current_stage="FINALIZE",
        project_admission_decision_id=admission.id,
        project_admission_decision_hash=admission.decision_hash,
        production_package_artifact_version_id=package.artifact_version_id,
        production_package_hash=package.canonical_hash,
        production_readiness_receipt_artifact_version_id=(
            readiness.receipt.artifact_version_id
        ),
        production_readiness_receipt_hash=readiness.receipt.receipt_hash,
        canonical_media_timeline_ref="fixture://phase6/timeline/v1",
        canonical_media_timeline_hash="1" * 64,
        native_render_plan_ref="fixture://phase6/native-render-plan/v1",
        native_render_plan_hash="2" * 64,
        render_output_ref=render_output_ref,
        render_output_checksum=checksum,
        technical_qc_receipt_ref="fixture://phase6/technical-qc/v1",
        technical_qc_receipt_hash="3" * 64,
        creative_qc_receipt_ref="fixture://phase6/creative-qc/v1",
        creative_qc_receipt_hash="4" * 64,
        archive_receipt_ref="fixture://phase6/archive-receipt/v1",
        archive_receipt_hash=archive_receipt_hash,
        archive_object_ref=archive_object_ref,
        archive_verification_state="VERIFIED",
        final_media_ref_id=final_media.id,
        final_media_ref_hash=checksum,
        destination_binding_id=destination_id,
        destination_binding_fingerprint=destination_fingerprint,
        destination_binding={
            "platform": destination["platform"],
            "platform_channel_id": destination["platform_channel_id"],
            "account_identity": destination["platform_account_ref"],
        },
    )
    session.add(workflow)
    session.flush()
    candidate = ProductionPublishService(session).create_final_review_candidate(
        FinalReviewCandidateCreateV2(
            workflow_run_id=workflow.id,
            production_package_artifact_version_id=(package.artifact_version_id),
            production_package_hash=package.canonical_hash,
            production_readiness_receipt_artifact_version_id=(
                readiness.receipt.artifact_version_id
            ),
            production_readiness_receipt_hash=readiness.receipt.receipt_hash,
            canonical_media_timeline_ref=workflow.canonical_media_timeline_ref,
            canonical_media_timeline_hash=workflow.canonical_media_timeline_hash,
            native_render_plan_ref=workflow.native_render_plan_ref,
            native_render_plan_hash=workflow.native_render_plan_hash,
            render_output_ref=render_output_ref,
            render_output_checksum=checksum,
            technical_qc_receipt_ref=workflow.technical_qc_receipt_ref,
            technical_qc_receipt_hash=workflow.technical_qc_receipt_hash,
            technical_qc_state="PASS",
            creative_qc_receipt_ref=workflow.creative_qc_receipt_ref,
            creative_qc_receipt_hash=workflow.creative_qc_receipt_hash,
            creative_qc_state="PASS",
            archive_receipt_ref=workflow.archive_receipt_ref,
            archive_receipt_hash=workflow.archive_receipt_hash,
            archive_object_ref=workflow.archive_object_ref,
            archive_verification_state="VERIFIED",
            final_media_ref_id=final_media.id,
            destination_binding_id=destination_id,
            destination_binding_fingerprint=destination_fingerprint,
            destination_platform_channel_id=destination["platform_channel_id"],
            destination_account_identity=destination["platform_account_ref"],
            target_platform="YOUTUBE",
            target_surface=scope.project.production_lane,
            target_market_lineage={
                "profile_hash": scope.profile.profile_input_hash,
                "policy_hash": scope.policy.content_hash,
            },
            publish_metadata_snapshot={
                "title": scope.project.title,
                "description": scope.project.description
                or "Phase 6 authoritative fixture",
                "privacy_status": "PRIVATE",
                "thumbnail_required": True,
                "caption_required": True,
            },
            disclosure_snapshot={
                "ai_disclosure_confirmed": True,
                "rights_confirmed": True,
            },
        )
    )
    session.refresh(workflow)
    return candidate, workflow
