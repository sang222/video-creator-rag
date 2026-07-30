from __future__ import annotations

import hashlib
import inspect
import runpy
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.production_publish import (
    FinalReviewCandidateCreateV2,
    FinalVideoDecisionCreate,
    HumanUploadTaskStartV2,
    ManualPublishConfirmationCreateV2,
    ManualPublishVerificationV2,
)
from app.contracts.m12_2r import BackfillUploadedVideoRequest
from app.contracts.m7 import (
    ManualPublishConfirmationCreate as LegacyManualPublishConfirmationCreate,
)
from app.contracts.production_package import ProductionPackageContentV2
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.actor import authenticated_actor_context
from app.core.errors import ConflictError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import ArtifactVersion, DomainEvent
from app.db.models.m10_1 import HumanUploadTask
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.m7 import ManualPublishConfirmation, UploadedVideo
from app.db.models.production_publish import (
    FinalReviewCandidate,
    FinalVideoDecision,
    SeriesEpisodePublication,
)
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun
from app.services.m12_2r import PublishHandoffLedgerService
from app.services.m7 import ManualPublishConfirmationService, PublishHandoffService
from app.services.production_package import ProductionReadinessService
from app.services.production_publish import (
    ProductionPublishService,
    _verification_evidence_payload,
    stable_hash,
)
from app.services.workflow import ArtifactService


ROOT = Path(__file__).resolve().parents[1]
_PHASE3 = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
_phase3_scope = _PHASE3["_scope"]
_phase3_create_package = _PHASE3["_create_package"]


@dataclass(frozen=True)
class _ReadyFinal:
    scope: object
    final_media: FinalMediaRef
    workflow: ProductionWorkflowRun
    candidate_data: FinalReviewCandidateCreateV2
    candidate: FinalReviewCandidate | None


def _actor(scope: object):
    return authenticated_actor_context(
        canonical_user_id=scope.operator.id,
        operator_user_id=scope.operator.id,
        actor_role="operator",
        permissions={
            "production.read",
            "review.final_decide",
            "publish.prepare",
            "publish.confirm",
        },
    )


def _ready_final(
    session: Session,
    *,
    create_candidate: bool = True,
    archive_state: str = "VERIFIED",
    local_archive_root: Path | None = None,
    local_archive_payload: bytes | None = None,
) -> _ReadyFinal:
    scope = _phase3_scope(session)
    package = _phase3_create_package(session, scope)
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
    checksum = "a" * 64
    output_ref = "workspace://renders/final.mp4"
    archive_object_ref = "drive://phase5-final/final.mp4"
    archive_receipt_hash = "8" * 64
    storage_provider = "GOOGLE_DRIVE"
    drive_file_id = "phase5-final"
    web_view_link = "https://drive.google.com/file/d/phase5-final/view"
    file_name = "final.mp4"
    size_bytes = 1024
    cloud_appendix = {
        "archive_receipt_hash": archive_receipt_hash,
        "remote_exact_set_verified": True,
    }
    if local_archive_root is not None:
        if local_archive_payload is None:
            raise AssertionError("local archive payload is required")
        checksum = hashlib.sha256(local_archive_payload).hexdigest()
        archive_object_ref = (
            f"vcos-local-archive://{scope.project.id}/{checksum}/final.mp4"
        )
        storage_provider = "VCOS_LOCAL_ARCHIVE"
        drive_file_id = f"local-{checksum}"
        web_view_link = archive_object_ref
        file_name = f"{checksum}.mp4"
        size_bytes = len(local_archive_payload)
        cloud_appendix = {
            "archive_receipt_hash": archive_receipt_hash,
            "archive_journal_hash": "b" * 64,
            "readback_checksum": checksum,
        }
        archive_dir = local_archive_root / "archive" / str(scope.project.id)
        archive_dir.mkdir(parents=True)
        (archive_dir / file_name).write_bytes(local_archive_payload)
    cloud = CloudMediaRef(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=scope.project.id,
        media_type="LONG_FORM_FINAL",
        storage_provider=storage_provider,
        drive_file_id=drive_file_id,
        drive_folder_id="phase5-folder",
        web_view_link=web_view_link,
        mime_type="video/mp4",
        file_name=file_name,
        size_bytes=size_bytes,
        checksum_sha256=checksum,
        local_source_path_hash=checksum,
        upload_status="VERIFIED",
        verification_status="CHECKSUM_VERIFIED",
        source_refs=[
            {
                "type": "archive_receipt",
                "ref": f"drive-receipt://phase5/v1#{archive_receipt_hash}",
            }
        ],
        technical_appendix=cloud_appendix,
    )
    session.add(cloud)
    session.flush()
    lineage_artifact = ArtifactService(session).create_artifact(
        data=ArtifactCreate(
            video_project_id=scope.project.id,
            artifact_type="mr1_final_media_lineage_receipt",
            created_by_user_id=scope.operator.id,
        ),
        correlation_id="phase5-final-media-lineage-artifact",
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
                "duration_contract": package_content.duration_contract.model_dump(
                    mode="json"
                ),
                "canonical_media_timeline_hash": "4" * 64,
                "native_render_plan_hash": "5" * 64,
                "render_output_checksum": checksum,
                "technical_qc_hash": "6" * 64,
                "creative_qc_hash": "7" * 64,
                "archive_receipt_hash": archive_receipt_hash,
                "archive_state": "VERIFIED",
                "cloud_media_ref_id": str(cloud.id),
                "file_ref": archive_object_ref,
            },
            status="approved",
            created_by_user_id=scope.operator.id,
        ),
        correlation_id="phase5-final-media-lineage-version",
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
        duration_seconds=Decimal(scope.duration.target_duration_ms) / Decimal(1000),
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
    project_identity = scope.project.id.hex
    workflow = ProductionWorkflowRun(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=scope.project.id,
        production_lane="LONG_FORM",
        planning_source_type="LONG_FORM_PLAN",
        planning_source_id=uuid.uuid4(),
        planning_source_hash=project_identity * 2,
        workflow_key=project_identity[::-1] * 2,
        start_input_hash=(project_identity[16:] + project_identity[:16]) * 2,
        state="ARCHIVE_RUNNING",
        current_stage="ARCHIVE",
        production_package_artifact_version_id=package.artifact_version_id,
        production_package_hash=package.canonical_hash,
        production_readiness_receipt_artifact_version_id=(
            readiness.receipt.artifact_version_id
        ),
        production_readiness_receipt_hash=readiness.receipt.receipt_hash,
        canonical_media_timeline_ref="artifact://timeline/v1",
        canonical_media_timeline_hash="4" * 64,
        native_render_plan_ref="artifact://native-render-plan/v1",
        native_render_plan_hash="5" * 64,
        render_output_ref=output_ref,
        render_output_checksum=checksum,
        technical_qc_receipt_ref="artifact://technical-qc/v1",
        technical_qc_receipt_hash="6" * 64,
        creative_qc_receipt_ref="artifact://creative-qc/v1",
        creative_qc_receipt_hash="7" * 64,
        archive_receipt_ref="drive-receipt://phase5/v1",
        archive_receipt_hash=archive_receipt_hash,
        archive_object_ref=archive_object_ref,
        archive_verification_state=archive_state,
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
    data = FinalReviewCandidateCreateV2(
        workflow_run_id=workflow.id,
        production_package_artifact_version_id=package.artifact_version_id,
        production_package_hash=package.canonical_hash,
        production_readiness_receipt_artifact_version_id=(
            readiness.receipt.artifact_version_id
        ),
        production_readiness_receipt_hash=readiness.receipt.receipt_hash,
        canonical_media_timeline_ref=workflow.canonical_media_timeline_ref,
        canonical_media_timeline_hash=workflow.canonical_media_timeline_hash,
        native_render_plan_ref=workflow.native_render_plan_ref,
        native_render_plan_hash=workflow.native_render_plan_hash,
        render_output_ref=output_ref,
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
        target_surface="LONG_FORM",
        target_market_lineage={
            "target_market_profile_hash": "9" * 64,
            "rights_envelope_ref": "rights://phase5/v1",
        },
        publish_metadata_snapshot={
            "title": "Exact reviewed title",
            "description": "Exact reviewed description",
            "privacy_status": "PUBLIC",
            "thumbnail_required": True,
            "caption_required": True,
        },
        disclosure_snapshot={
            "ai_disclosure_confirmed": True,
            "rights_confirmed": True,
        },
    )
    candidate = (
        ProductionPublishService(session).create_final_review_candidate(data)
        if create_candidate
        else None
    )
    return _ReadyFinal(scope, final_media, workflow, data, candidate)


def _replacement_candidate_data(
    session: Session,
    ready: _ReadyFinal,
) -> FinalReviewCandidateCreateV2:
    checksum = "d" * 64
    output_ref = "workspace://renders/replacement.mp4"
    archive_object_ref = "drive://phase5-replacement/replacement.mp4"
    cloud = CloudMediaRef(
        company_id=ready.scope.company.id,
        channel_workspace_id=ready.scope.channel.id,
        video_project_id=ready.scope.project.id,
        media_type="LONG_FORM_FINAL",
        storage_provider="GOOGLE_DRIVE",
        drive_file_id="phase5-replacement",
        drive_folder_id="phase5-folder",
        web_view_link=("https://drive.google.com/file/d/phase5-replacement/view"),
        mime_type="video/mp4",
        file_name="replacement.mp4",
        size_bytes=2048,
        checksum_sha256=checksum,
        local_source_path_hash=checksum,
        upload_status="VERIFIED",
        verification_status="CHECKSUM_VERIFIED",
        source_refs=[
            {
                "type": "archive_receipt",
                "ref": (
                    f"{ready.candidate_data.archive_receipt_ref}"
                    f"#{ready.candidate_data.archive_receipt_hash}"
                ),
            }
        ],
        technical_appendix={
            "archive_receipt_hash": (ready.candidate_data.archive_receipt_hash),
            "remote_exact_set_verified": True,
        },
    )
    session.add(cloud)
    session.flush()
    lineage_artifact = ArtifactService(session).create_artifact(
        data=ArtifactCreate(
            video_project_id=ready.scope.project.id,
            artifact_type="mr1_final_media_lineage_receipt",
            created_by_user_id=ready.scope.operator.id,
        ),
        correlation_id="phase5-replacement-lineage-artifact",
        trusted_authority_write=True,
    )
    lineage_version = ArtifactService(session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=lineage_artifact.id,
            content={
                "schema_version": "vcos.native-final-media-lineage.v2",
                "video_project_id": str(ready.scope.project.id),
                "production_package_artifact_version_id": str(
                    ready.candidate_data.production_package_artifact_version_id
                ),
                "production_package_hash": (
                    ready.candidate_data.production_package_hash
                ),
                "duration_contract": (ready.scope.duration.model_dump(mode="json")),
                "canonical_media_timeline_hash": (
                    ready.candidate_data.canonical_media_timeline_hash
                ),
                "native_render_plan_hash": (
                    ready.candidate_data.native_render_plan_hash
                ),
                "render_output_checksum": checksum,
                "technical_qc_hash": (ready.candidate_data.technical_qc_receipt_hash),
                "creative_qc_hash": (ready.candidate_data.creative_qc_receipt_hash),
                "archive_receipt_hash": (ready.candidate_data.archive_receipt_hash),
                "archive_state": "VERIFIED",
                "cloud_media_ref_id": str(cloud.id),
                "file_ref": archive_object_ref,
            },
            status="approved",
            created_by_user_id=ready.scope.operator.id,
        ),
        correlation_id="phase5-replacement-lineage-version",
        trusted_authority_write=True,
    )
    lineage_artifact.status = "approved"
    session.flush()
    final_media = FinalMediaRef(
        company_id=ready.scope.company.id,
        channel_workspace_id=ready.scope.channel.id,
        video_project_id=ready.scope.project.id,
        production_package_artifact_version_id=(
            ready.candidate_data.production_package_artifact_version_id
        ),
        production_package_hash=ready.candidate_data.production_package_hash,
        duration_contract=ready.scope.duration.model_dump(mode="json"),
        media_type="LONG_FORM_FINAL",
        file_ref=archive_object_ref,
        duration_seconds=(
            Decimal(ready.scope.duration.target_duration_ms) / Decimal(1000)
        ),
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
    ready.workflow.render_output_ref = output_ref
    ready.workflow.render_output_checksum = checksum
    ready.workflow.archive_object_ref = archive_object_ref
    ready.workflow.final_media_ref_id = final_media.id
    ready.workflow.final_media_ref_hash = checksum
    session.flush()
    return ready.candidate_data.model_copy(
        update={
            "render_output_ref": output_ref,
            "render_output_checksum": checksum,
            "archive_object_ref": archive_object_ref,
            "final_media_ref_id": final_media.id,
        }
    )


def _decide_upload(session: Session, ready: _ReadyFinal):
    assert ready.candidate is not None
    result = ProductionPublishService(session).decide(
        candidate_id=ready.candidate.id,
        data=FinalVideoDecisionCreate(
            command_id=uuid.uuid4(),
            decision="UPLOAD",
            warnings_acknowledged=[],
        ),
        actor=_actor(ready.scope),
    )
    assert result.human_upload_task_id is not None
    task = session.get(HumanUploadTask, result.human_upload_task_id)
    assert task is not None
    return result, task


def _start(session: Session, ready: _ReadyFinal, task: HumanUploadTask):
    return ProductionPublishService(session).start_upload_task(
        task_id=task.id,
        data=HumanUploadTaskStartV2(
            selected_file_name="final.mp4",
            selected_file_ref=ready.candidate_data.archive_object_ref,
            selected_file_checksum=ready.final_media.checksum_sha256,
            archive_object_ref=ready.candidate_data.archive_object_ref,
        ),
        actor=_actor(ready.scope),
    )


def _confirmation_data(**updates):
    payload = {
        "command_id": uuid.uuid4(),
        "platform": "YOUTUBE",
        "platform_channel_id": "channel-phase5",
        "destination_binding_id": updates.pop(
            "destination_binding_id", uuid.UUID(int=0)
        ),
        "destination_binding_fingerprint": updates.pop(
            "destination_binding_fingerprint", "b" * 64
        ),
        "destination_account_identity": "account-phase5",
        "platform_video_id": "phase5-video",
        "video_url": "https://www.youtube.com/watch?v=phase5-video",
        "title": "Exact reviewed title",
        "description": "Exact reviewed description",
        "privacy_status": "PUBLIC",
        "published_at": utc_now(),
        "duration_seconds": Decimal("300"),
        "thumbnail_confirmed": True,
        "caption_confirmed": True,
        "disclosures": {
            "ai_disclosure_confirmed": True,
            "rights_confirmed": True,
        },
    }
    payload.update(updates)
    return ManualPublishConfirmationCreateV2(**payload)


def _submit(
    session: Session,
    ready: _ReadyFinal,
    task: HumanUploadTask,
    **updates,
) -> ManualPublishConfirmation:
    _start(session, ready, task)
    confirmation_values = {
        "destination_binding_id": (ready.candidate_data.destination_binding_id),
        "destination_binding_fingerprint": (
            ready.candidate_data.destination_binding_fingerprint
        ),
        "platform_channel_id": (ready.candidate_data.destination_platform_channel_id),
        "destination_account_identity": (
            ready.candidate_data.destination_account_identity
        ),
    }
    confirmation_values.update(updates)
    data = _confirmation_data(**confirmation_values)
    return ProductionPublishService(session).submit_confirmation(
        task_id=task.id,
        data=data,
        actor=_actor(ready.scope),
    )


def _verification_data(confirmation: ManualPublishConfirmation):
    metadata = confirmation.actual_metadata
    return ManualPublishVerificationV2(
        verification_command_id=uuid.uuid4(),
        verification_evidence_ref="fixture://observable-platform-read/v1",
        observed_platform=confirmation.target_platform,
        observed_platform_channel_id=confirmation.platform_channel_id,
        observed_destination_account_identity=(
            confirmation.destination_account_identity
        ),
        observed_platform_video_id=confirmation.actual_video_id,
        observed_video_url=confirmation.actual_video_url,
        observed_title=metadata["title"],
        observed_description=metadata["description"],
        observed_privacy_status=metadata["privacy_status"],
        observed_published_at=confirmation.actual_published_at,
        observed_duration_seconds=confirmation.actual_duration_seconds,
    )


def _classification_candidate() -> SimpleNamespace:
    return SimpleNamespace(
        target_platform="YOUTUBE",
        destination_binding_id=uuid.uuid4(),
        destination_binding_fingerprint="b" * 64,
        destination_platform_channel_id="channel-phase5",
        destination_account_identity="account-phase5",
        publish_metadata_snapshot={
            "title": "Exact reviewed title",
            "description": "Exact reviewed description",
            "privacy_status": "PUBLIC",
            "thumbnail_required": True,
            "caption_required": True,
        },
        disclosure_snapshot={
            "ai_disclosure_confirmed": True,
            "rights_confirmed": True,
        },
    )


def test_01_no_candidate_before_final_media_and_archive_verified(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session, create_candidate=False, archive_state="PENDING")
    with pytest.raises(
        ValidationFailureError,
        match="FINAL_REVIEW_WORKFLOW_BINDING_MISMATCH:archive_verification_state",
    ):
        ProductionPublishService(db_session).create_final_review_candidate(
            ready.candidate_data
        )


def test_01b_candidate_requires_checksum_verified_cloud_media(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session, create_candidate=False)
    cloud = db_session.get(CloudMediaRef, ready.final_media.cloud_media_ref_id)
    assert cloud is not None
    cloud.verification_status = "SIZE_VERIFIED"

    with pytest.raises(
        ValidationFailureError,
        match="FINAL_REVIEW_CLOUD_MEDIA_NOT_CHECKSUM_VERIFIED",
    ):
        ProductionPublishService(db_session).create_final_review_candidate(
            ready.candidate_data
        )


def test_01c_candidate_requires_exact_lineage_receipt(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session, create_candidate=False)
    lineage = db_session.get(
        ArtifactVersion, ready.final_media.lineage_artifact_version_id
    )
    assert lineage is not None
    lineage.content = {**lineage.content, "archive_state": "FAILED"}

    with pytest.raises(
        ValidationFailureError,
        match="FINAL_REVIEW_FINAL_MEDIA_LINEAGE_MISMATCH",
    ):
        ProductionPublishService(db_session).create_final_review_candidate(
            ready.candidate_data
        )


def test_01c2_candidate_requires_v2_lineage_schema(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session, create_candidate=False)
    lineage = db_session.get(
        ArtifactVersion, ready.final_media.lineage_artifact_version_id
    )
    assert lineage is not None
    lineage.content = {
        **lineage.content,
        "schema_version": "mr1.final-media-lineage-receipt.v1",
    }

    with pytest.raises(
        ValidationFailureError,
        match="FINAL_REVIEW_FINAL_MEDIA_LINEAGE_MISMATCH",
    ):
        ProductionPublishService(db_session).create_final_review_candidate(
            ready.candidate_data
        )


def test_01d_candidate_resolves_destination_from_exact_package_ref(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session, create_candidate=False)
    unrelated_binding_id = uuid.uuid4()
    ready.workflow.destination_binding_id = unrelated_binding_id
    candidate_data = ready.candidate_data.model_copy(
        update={"destination_binding_id": unrelated_binding_id}
    )

    with pytest.raises(
        ValidationFailureError,
        match="FINAL_REVIEW_DESTINATION_BINDING_MISMATCH",
    ):
        ProductionPublishService(db_session).create_final_review_candidate(
            candidate_data
        )


def test_01e_final_media_lineage_requires_trusted_domain_writer(
    db_session: Session,
) -> None:
    phase3 = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
    scope = phase3["_scope"](db_session)

    with pytest.raises(
        ValidationFailureError,
        match="AUTHORITY_ARTIFACT_DOMAIN_SERVICE_REQUIRED",
    ):
        ArtifactService(db_session).create_artifact(
            data=ArtifactCreate(
                video_project_id=scope.project.id,
                artifact_type="mr1_final_media_lineage_receipt",
                created_by_user_id=scope.operator.id,
            ),
            correlation_id="phase5-public-lineage-forbidden",
        )


def test_02_upload_creates_exactly_one_task(db_session: Session) -> None:
    ready = _ready_final(db_session)
    result, _task = _decide_upload(db_session, ready)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(HumanUploadTask)
            .where(HumanUploadTask.final_video_decision_id == result.decision.id)
        )
        == 1
    )


def test_03_duplicate_upload_does_not_duplicate_task(db_session: Session) -> None:
    ready = _ready_final(db_session)
    assert ready.candidate is not None
    command = FinalVideoDecisionCreate(command_id=uuid.uuid4(), decision="UPLOAD")
    first = ProductionPublishService(db_session).decide(
        candidate_id=ready.candidate.id,
        data=command,
        actor=_actor(ready.scope),
    )
    second = ProductionPublishService(db_session).decide(
        candidate_id=ready.candidate.id,
        data=command,
        actor=_actor(ready.scope),
    )
    assert second.human_upload_task_id == first.human_upload_task_id
    assert db_session.scalar(select(func.count()).select_from(HumanUploadTask)) == 1


def test_04_do_not_upload_creates_no_task(db_session: Session) -> None:
    ready = _ready_final(db_session)
    assert ready.candidate is not None
    result = ProductionPublishService(db_session).decide(
        candidate_id=ready.candidate.id,
        data=FinalVideoDecisionCreate(
            command_id=uuid.uuid4(), decision="DO_NOT_UPLOAD"
        ),
        actor=_actor(ready.scope),
    )
    assert result.human_upload_task_id is None
    assert db_session.scalar(select(func.count()).select_from(HumanUploadTask)) == 0


def test_05_actor_spoofing_is_rejected_and_session_actor_is_persisted(
    db_session: Session,
) -> None:
    with pytest.raises(ValidationError):
        FinalVideoDecisionCreate.model_validate(
            {
                "command_id": str(uuid.uuid4()),
                "decision": "UPLOAD",
                "operator_user_id": str(uuid.uuid4()),
            }
        )
    ready = _ready_final(db_session)
    result, _task = _decide_upload(db_session, ready)
    assert result.decision.operator_user_id == ready.scope.operator.id


def test_06_task_binds_exact_final_media_package_and_destination(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    result, task = _decide_upload(db_session, ready)
    assert ready.candidate is not None
    assert task.final_video_decision_id == result.decision.id
    assert task.final_media_ref_id == ready.final_media.id
    assert task.reviewed_checksum == ready.final_media.checksum_sha256
    assert (
        task.production_package_artifact_version_id
        == ready.candidate.production_package_artifact_version_id
    )
    assert task.destination_binding_id == ready.candidate.destination_binding_id
    assert task.publish_package_id is None
    assert task.first_scripted_video_package_id is None


def test_07_m12_2r_remains_legacy_only_for_v2_task_creation() -> None:
    source = inspect.getsource(
        PublishHandoffLedgerService.create_upload_task_from_package
    )
    assert "FINAL_MEDIA_DECISION_REQUIRED" in source
    assert "ProductionPublishService" not in source


def test_07a_m12_2r_backfill_cannot_mutate_a_v2_upload_task(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    _result, task = _decide_upload(db_session, ready)
    original_state = task.task_state

    with pytest.raises(
        ValidationFailureError,
        match="CANONICAL_V2_MANUAL_PUBLISH_REQUIRED",
    ):
        PublishHandoffLedgerService(db_session).backfill_uploaded_video(
            task_id=task.id,
            data=BackfillUploadedVideoRequest(
                youtube_url_or_video_id="v2guard0001",
            ),
        )

    assert task.task_state == original_state
    assert task.actual_uploaded_video_id is None
    assert db_session.scalar(select(func.count()).select_from(UploadedVideo)) == 0


def test_07b_m7_create_and_accept_cannot_cross_into_v2_publish_scope(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_final(db_session)
    fake_handoff = SimpleNamespace(
        id=uuid.uuid4(),
        company_id=ready.scope.company.id,
        video_project_id=ready.scope.project.id,
    )
    monkeypatch.setattr(
        PublishHandoffService,
        "require",
        lambda _service, _handoff_id: fake_handoff,
    )
    with pytest.raises(
        ValidationFailureError,
        match="CANONICAL_V2_MANUAL_PUBLISH_REQUIRED",
    ):
        ManualPublishConfirmationService(db_session).create_confirmation(
            data=LegacyManualPublishConfirmationCreate(
                publish_handoff_package_id=fake_handoff.id,
                confirmed_by_user_id=ready.scope.operator.id,
                actual_video_id="v2guard0002",
                actual_video_url=("https://www.youtube.com/watch?v=v2guard0002"),
                actual_published_at=utc_now(),
            )
        )

    _result, task = _decide_upload(db_session, ready)
    confirmation = _submit(db_session, ready, task)
    with pytest.raises(
        ValidationFailureError,
        match="CANONICAL_V2_MANUAL_PUBLISH_REQUIRED",
    ):
        ManualPublishConfirmationService(db_session).accept_confirmation(
            confirmation_id=confirmation.id,
        )
    assert confirmation.confirmation_state == "SUBMITTED"
    assert db_session.scalar(select(func.count()).select_from(UploadedVideo)) == 0


def test_08_wrong_destination_blocks_confirmation() -> None:
    candidate = _classification_candidate()
    data = _confirmation_data(destination_binding_id=uuid.uuid4())
    state = ProductionPublishService._classify_confirmation(
        object(),
        candidate=candidate,
        final_media=SimpleNamespace(duration_seconds=Decimal("300")),
        data=data,
    )
    assert state["state"] == "BLOCKED_DESTINATION"


def test_09_wrong_video_id_rejects_confirmation() -> None:
    candidate = _classification_candidate()
    data = _confirmation_data(
        destination_binding_id=candidate.destination_binding_id,
        video_url="https://www.youtube.com/watch?v=other-video",
    )
    state = ProductionPublishService._classify_confirmation(
        object(),
        candidate=candidate,
        final_media=SimpleNamespace(duration_seconds=Decimal("300")),
        data=data,
    )
    assert state["state"] == "REJECTED_MISMATCH"


def test_10_material_metadata_variance_requires_correction() -> None:
    candidate = _classification_candidate()
    data = _confirmation_data(
        destination_binding_id=candidate.destination_binding_id,
        title="Materially different title",
    )
    state = ProductionPublishService._classify_confirmation(
        object(),
        candidate=candidate,
        final_media=SimpleNamespace(duration_seconds=Decimal("300")),
        data=data,
    )
    assert state["state"] == "CORRECTION_REQUIRED"
    assert "MATERIAL_TITLE_VARIANCE" in state["reason_codes"]


def test_11_non_material_variance_requires_authenticated_attestation(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    _result, task = _decide_upload(db_session, ready)
    confirmation = _submit(
        db_session,
        ready,
        task,
        description="Allowed operator wording variance",
        accept_non_material_variance=True,
    )
    assert confirmation.confirmation_state == "VARIANCE_ACCEPTED"
    assert confirmation.variance_attested_by_user_id == ready.scope.operator.id
    assert confirmation.variance_attested_at is not None


def test_12_verified_confirmation_creates_one_uploaded_video(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    _result, task = _decide_upload(db_session, ready)
    confirmation = _submit(db_session, ready, task)
    verified = ProductionPublishService(db_session).verify_confirmation(
        confirmation_id=confirmation.id,
        data=_verification_data(confirmation),
        actor=_actor(ready.scope),
    )
    assert verified.status == "VERIFIED"
    assert verified.uploaded_video is not None
    assert db_session.scalar(select(func.count()).select_from(UploadedVideo)) == 1
    expected_evidence_hash = stable_hash(
        _verification_evidence_payload(
            confirmation=confirmation,
            task=task,
            final_media=ready.final_media,
            data=_verification_data(confirmation),
        )
    )
    assert confirmation.verification_evidence_hash == expected_evidence_hash
    uploaded = db_session.get(UploadedVideo, verified.uploaded_video.id)
    assert uploaded is not None
    assert (
        uploaded.archive_supplement["verification"]["evidence_hash"]
        == expected_evidence_hash
    )
    assert (
        uploaded.archive_supplement["verification"]["evidence_hash_authority"]
        == "SERVER_CANONICAL_OBSERVATION"
    )


def test_12a_client_cannot_supply_or_tamper_with_v2_evidence_hash() -> None:
    payload = {
        "verification_command_id": str(uuid.uuid4()),
        "verification_evidence_ref": "fixture://observable-platform-read/v1",
        "verification_evidence_hash": "0" * 64,
        "observed_platform": "YOUTUBE",
        "observed_platform_channel_id": "channel-phase5",
        "observed_destination_account_identity": "account-phase5",
        "observed_platform_video_id": "phase5-video",
        "observed_video_url": ("https://www.youtube.com/watch?v=phase5-video"),
        "observed_title": "Exact reviewed title",
        "observed_description": "Exact reviewed description",
        "observed_privacy_status": "PUBLIC",
        "observed_published_at": utc_now().isoformat(),
        "observed_duration_seconds": "300",
    }
    with pytest.raises(ValidationError):
        ManualPublishVerificationV2.model_validate(payload)


def test_12b_m12_2r_verify_cannot_mutate_or_call_provider_for_v2_video(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    _result, task = _decide_upload(db_session, ready)
    confirmation = _submit(db_session, ready, task)
    verified = ProductionPublishService(db_session).verify_confirmation(
        confirmation_id=confirmation.id,
        data=_verification_data(confirmation),
        actor=_actor(ready.scope),
    )
    uploaded = db_session.get(UploadedVideo, verified.uploaded_video.id)
    assert uploaded is not None

    class _ForbiddenProvider:
        def fetch(self, **_kwargs):
            raise AssertionError("legacy YouTube provider must not be called")

    legacy = PublishHandoffLedgerService(
        db_session,
        public_provider=_ForbiddenProvider(),
    )
    with pytest.raises(
        ValidationFailureError,
        match="CANONICAL_V2_MANUAL_PUBLISH_REQUIRED",
    ):
        legacy.verify_uploaded_video(uploaded.id)
    assert uploaded.verification_status == "VERIFIED"
    assert task.task_state == "VERIFIED"


def test_13_uploaded_video_lineage_splice_fails_closed(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    _result, task = _decide_upload(db_session, ready)
    confirmation = _submit(db_session, ready, task)
    original_hash = task.production_package_hash
    task.production_package_hash = "d" * 64
    with pytest.raises(
        ValidationFailureError, match="UPLOAD_TASK_LINEAGE_SPLICE_DETECTED"
    ):
        ProductionPublishService(db_session).verify_confirmation(
            confirmation_id=confirmation.id,
            data=_verification_data(confirmation),
            actor=_actor(ready.scope),
        )
    task.production_package_hash = original_hash


def _series_run_for_scope(session: Session, scope: object) -> SeriesRun:
    plan = SeriesPlan(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        policy_snapshot_id=scope.policy.id,
        stable_series_key=f"phase5-{uuid.uuid4().hex}",
        display_name="Phase 5 series",
        editorial_promise="Exact verified publication progress",
        allowed_production_lanes=["LONG_FORM"],
        episode_role_policy={},
        state="DRAFT",
        version=1,
        created_by_user_id=scope.operator.id,
    )
    session.add(plan)
    session.flush()
    run = SeriesRun(
        series_plan_id=plan.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        policy_snapshot_id=scope.policy.id,
        run_key=f"phase5-{uuid.uuid4().hex}",
        run_number=1,
        capacity=1,
        first_episode_number=1,
        next_episode_number=2,
        reserved_episode_count=1,
        published_episode_count=0,
        state="ACTIVE",
        created_by_user_id=scope.operator.id,
    )
    session.add(run)
    session.flush()
    return run


def _mark_series_once(
    session: Session,
    ready: _ReadyFinal,
    uploaded: UploadedVideo,
    confirmation: ManualPublishConfirmation,
    task: HumanUploadTask,
    decision: FinalVideoDecision,
    run: SeriesRun,
) -> SeriesEpisodePublication:
    candidate = SimpleNamespace(
        content_mode="SERIES_EPISODE",
        series_plan_id=run.series_plan_id,
        series_run_id=run.id,
        episode_number=1,
        company_id=ready.scope.company.id,
        channel_workspace_id=ready.scope.channel.id,
        video_project_id=ready.scope.project.id,
    )
    receipt = ProductionPublishService(session)._advance_series_after_verified(
        candidate=candidate,
        decision=decision,
        task=task,
        confirmation=confirmation,
        uploaded=uploaded,
    )
    assert receipt is not None
    return receipt


def test_14_series_progress_advances_only_after_verified_upload(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    result, task = _decide_upload(db_session, ready)
    run = _series_run_for_scope(db_session, ready.scope)
    assert run.published_episode_count == 0
    confirmation = _submit(db_session, ready, task)
    assert run.published_episode_count == 0
    verified = ProductionPublishService(db_session).verify_confirmation(
        confirmation_id=confirmation.id,
        data=_verification_data(confirmation),
        actor=_actor(ready.scope),
    )
    uploaded = db_session.get(UploadedVideo, verified.uploaded_video.id)
    decision = db_session.get(FinalVideoDecision, result.decision.id)
    _mark_series_once(db_session, ready, uploaded, confirmation, task, decision, run)
    assert run.published_episode_count == 1


def test_15_duplicate_confirmation_does_not_double_advance_series(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    result, task = _decide_upload(db_session, ready)
    run = _series_run_for_scope(db_session, ready.scope)
    confirmation = _submit(db_session, ready, task)
    verified = ProductionPublishService(db_session).verify_confirmation(
        confirmation_id=confirmation.id,
        data=_verification_data(confirmation),
        actor=_actor(ready.scope),
    )
    uploaded = db_session.get(UploadedVideo, verified.uploaded_video.id)
    decision = db_session.get(FinalVideoDecision, result.decision.id)
    first = _mark_series_once(
        db_session, ready, uploaded, confirmation, task, decision, run
    )
    second = _mark_series_once(
        db_session, ready, uploaded, confirmation, task, decision, run
    )
    assert second.id == first.id
    assert run.published_episode_count == 1


def test_16_standalone_upload_does_not_touch_series_run(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    _result, task = _decide_upload(db_session, ready)
    run = _series_run_for_scope(db_session, ready.scope)
    confirmation = _submit(db_session, ready, task)
    ProductionPublishService(db_session).verify_confirmation(
        confirmation_id=confirmation.id,
        data=_verification_data(confirmation),
        actor=_actor(ready.scope),
    )
    assert run.published_episode_count == 0
    assert (
        db_session.scalar(select(func.count()).select_from(SeriesEpisodePublication))
        == 0
    )


def test_17_derived_short_parent_lineage_is_copied_to_all_v2_entities() -> None:
    task_source = inspect.getsource(ProductionPublishService._create_upload_task_once)
    verify_source = inspect.getsource(ProductionPublishService.verify_confirmation)
    assert "parent_video_project_id=candidate.parent_video_project_id" in task_source
    assert (
        "parent_final_media_ref_id=candidate.parent_final_media_ref_id" in task_source
    )
    assert "parent_video_project_id=candidate.parent_video_project_id" in verify_source
    assert (
        "parent_final_media_ref_id=candidate.parent_final_media_ref_id" in verify_source
    )


def test_18_analytics_ready_and_verified_events_emit_exactly_once(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    _result, task = _decide_upload(db_session, ready)
    confirmation = _submit(db_session, ready, task)
    verification = _verification_data(confirmation)
    service = ProductionPublishService(db_session)
    first = service.verify_confirmation(
        confirmation_id=confirmation.id,
        data=verification,
        actor=_actor(ready.scope),
    )
    second = service.verify_confirmation(
        confirmation_id=confirmation.id,
        data=verification,
        actor=_actor(ready.scope),
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
    assert counts == {"ANALYTICS_READY": 1, "UPLOADED_VIDEO_VERIFIED": 1}


def test_19_do_not_upload_is_terminal(db_session: Session) -> None:
    ready = _ready_final(db_session)
    assert ready.candidate is not None
    service = ProductionPublishService(db_session)
    service.decide(
        candidate_id=ready.candidate.id,
        data=FinalVideoDecisionCreate(
            command_id=uuid.uuid4(), decision="DO_NOT_UPLOAD"
        ),
        actor=_actor(ready.scope),
    )
    with pytest.raises(ConflictError, match="FINAL_VIDEO_DECISION_TERMINAL"):
        service.decide(
            candidate_id=ready.candidate.id,
            data=FinalVideoDecisionCreate(command_id=uuid.uuid4(), decision="UPLOAD"),
            actor=_actor(ready.scope),
        )
    assert db_session.scalar(select(func.count()).select_from(HumanUploadTask)) == 0


def test_20_canonical_v2_path_has_no_auto_publish_code() -> None:
    service_source = inspect.getsource(ProductionPublishService)
    forbidden = (
        "youtube.videos.insert",
        "videos().insert",
        "selenium",
        "playwright",
        "studio.youtube.com",
    )
    assert all(token not in service_source.lower() for token in forbidden)


def test_21_rejected_candidate_can_be_replaced_by_new_exact_render(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    assert ready.candidate is not None
    service = ProductionPublishService(db_session)
    decision = service.decide(
        candidate_id=ready.candidate.id,
        data=FinalVideoDecisionCreate(
            command_id=uuid.uuid4(), decision="DO_NOT_UPLOAD"
        ),
        actor=_actor(ready.scope),
    )
    replacement_data = _replacement_candidate_data(db_session, ready)

    replacement = service.create_final_review_candidate(replacement_data)

    assert replacement.id != ready.candidate.id
    assert replacement.final_media_ref_id != ready.candidate.final_media_ref_id
    assert ready.workflow.final_review_candidate_id == replacement.id
    assert decision.decision.final_review_candidate_id == ready.candidate.id
    assert (
        db_session.scalar(select(func.count()).select_from(FinalReviewCandidate)) == 2
    )
