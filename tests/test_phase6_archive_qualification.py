from __future__ import annotations

import hashlib
import json
import runpy
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import func, select

from app.contracts.production_publish import FinalReviewCandidateCreateV2
from app.contracts.vcos_qualification import NativeQualificationRenderRequest
from app.contracts.vcos_v2 import (
    DurationContractV2,
    ProductionLane,
)
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.workflow import ArtifactVersion
from app.services.m10_5 import GoogleDriveUploadResult
from app.services.mr1_drive_archive import MR1ArchiveItem, MR1DriveArchiveService
from app.services.production_package import ProductionReadinessService
from app.services.production_publish import ProductionPublishService
from app.services.vcos_qualification import NativeQualificationService


ROOT = Path(__file__).resolve().parents[1]
_PHASE3 = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
_phase3_scope = _PHASE3["_scope"]
_phase3_create_package = _PHASE3["_create_package"]


class _DeterministicArchiveDrive:
    def __init__(self) -> None:
        self.upload_calls: list[str] = []
        self.folder_calls = 0
        self.metadata_calls = 0
        self.list_calls = 0
        self.remote: dict[str, GoogleDriveUploadResult] = {}

    def ensure_folder_path(
        self,
        *,
        access_token: str,
        root_folder_id: str,
        folder_path: list[str],
    ) -> str:
        assert access_token == "fixture-token"
        assert root_folder_id == "fixture-root"
        self.folder_calls += 1
        return "folder:" + "/".join(folder_path)

    def upload_file(
        self,
        *,
        access_token: str,
        local_path: Path,
        folder_id: str,
        upload_mode: str,
        mime_type: str,
    ) -> GoogleDriveUploadResult:
        assert access_token == "fixture-token"
        assert upload_mode == "resumable"
        data = local_path.read_bytes()
        self.upload_calls.append(local_path.name)
        file_id = f"fixture-file-{len(self.upload_calls)}"
        result = GoogleDriveUploadResult(
            drive_file_id=file_id,
            drive_folder_id=folder_id,
            web_view_link=f"https://drive.invalid/{file_id}",
            file_name=local_path.name,
            mime_type=mime_type,
            size_bytes=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
            upload_mode=upload_mode,
            technical_appendix={},
        )
        self.remote[file_id] = result
        return result

    def get_file_metadata(
        self,
        *,
        access_token: str,
        drive_file_id: str,
    ) -> GoogleDriveUploadResult:
        assert access_token == "fixture-token"
        self.metadata_calls += 1
        return self.remote[drive_file_id]

    def list_folder_files(
        self,
        *,
        access_token: str,
        folder_id: str,
    ) -> list[GoogleDriveUploadResult]:
        assert access_token == "fixture-token"
        self.list_calls += 1
        return sorted(
            (
                item
                for item in self.remote.values()
                if item.drive_folder_id == folder_id
            ),
            key=lambda item: (item.file_name or "", item.drive_file_id),
        )


def _duration() -> DurationContractV2:
    profile_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    values = {
        "minimum_duration_ms": 1_500,
        "target_duration_ms": 2_000,
        "maximum_duration_ms": 3_500,
        "duration_contract_version": "channel-duration-contract.v2",
        "source_profile_version_id": profile_id,
        "source_policy_snapshot_id": policy_id,
    }
    return DurationContractV2(
        **values,
        duration_contract_hash=DurationContractV2.calculate_hash(**values),
    )


def _archive_render(
    *,
    tmp_path: Path,
    workspace: Path,
    render,
    duration: DurationContractV2,
    lane: ProductionLane,
    run_id: str,
) -> tuple[dict, _DeterministicArchiveDrive]:
    authority = workspace / "authority"
    authority.mkdir(parents=True, exist_ok=True)
    timeline = authority / "canonical-timeline.json"
    plan = authority / "native-render-plan.json"
    qc = authority / "media-qc.json"
    final_media = authority / "final-media-ref.json"
    timeline.write_text(
        json.dumps(
            {
                "duration_contract": duration.model_dump(mode="json"),
                "duration_ms": duration.target_duration_ms,
                "output_checksum": render.output_checksum,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    plan.write_text(
        render.plan.model_dump_json(),
        encoding="utf-8",
    )
    qc.write_text(render.media_qc.model_dump_json(), encoding="utf-8")
    final_media.write_text(
        json.dumps(
            {
                "schema_version": "final-media-ref.v2",
                "media_ref": render.output_path.name,
                "checksum": render.output_checksum,
                "production_lane": lane.value,
                "archive_state": "PENDING",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    items = [
        MR1ArchiveItem.from_path(
            logical_role="REVIEW_MEDIA",
            source_path=render.output_path,
            archive_path="01-media/final-long.mp4",
        ),
        MR1ArchiveItem.from_path(
            logical_role="CANONICAL_TIMELINE",
            source_path=timeline,
            archive_path="00-authority/canonical-timeline.json",
        ),
        MR1ArchiveItem.from_path(
            logical_role="NATIVE_RENDER_PLAN",
            source_path=plan,
            archive_path="00-authority/native-render-plan.json",
        ),
        MR1ArchiveItem.from_path(
            logical_role="MEDIA_QC",
            source_path=qc,
            archive_path="00-authority/media-qc.json",
        ),
        MR1ArchiveItem.from_path(
            logical_role="FINAL_MEDIA_REF",
            source_path=final_media,
            archive_path="00-authority/final-media-ref.json",
        ),
    ]
    provider = _DeterministicArchiveDrive()
    archive = MR1DriveArchiveService(
        provider=provider,
        root_folder_id="fixture-root",
        upload_mode="resumable",
        source_root=workspace,
        state_root=tmp_path / "archive-state",
    )
    kwargs = {
        "run_id": run_id,
        "archive_identity": f"phase6:{render.output_checksum}",
        "root_relative_path": f"qualification/phase6/{lane.value.lower()}",
        "items": items,
        "access_token": "fixture-token",
    }

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda _index: archive.upload_and_verify(**kwargs),
                range(2),
            )
        )
    replay = archive.upload_and_verify(**kwargs)

    assert receipts[0] == receipts[1] == replay
    assert replay["archive_state"] == "VERIFIED"
    assert replay["remote_exact_set_verified"] is True
    assert replay["expected_item_count"] == replay["verified_item_count"] == 5
    assert len(provider.upload_calls) == 5
    assert len(set(provider.upload_calls)) == 5
    assert provider.folder_calls == 1
    assert provider.metadata_calls >= 5
    assert provider.list_calls >= 1
    assert all(
        item["verification_method"] == "SHA256_PLUS_SIZE" for item in replay["files"]
    )
    journal = json.loads(archive.journal_path(run_id).read_text(encoding="utf-8"))
    assert journal["manifest_hash"] == replay["archive_manifest_hash"]
    assert journal["remote_exact_set_verified"] is True
    assert all(entry["upload_call_count"] == 1 for entry in journal["entries"].values())
    assert "fixture-token" not in json.dumps(journal, sort_keys=True)
    return replay, provider


def _create_qualification_candidate(
    session,
    *,
    scope,
    package,
    readiness,
    render,
    archive_receipt: dict,
    final_media: FinalMediaRef,
):
    destination_ref = package.content.destination_binding_ref
    assert destination_ref.artifact_version_id is not None
    destination_version = session.get(
        ArtifactVersion,
        destination_ref.artifact_version_id,
    )
    assert destination_version is not None
    wrapped_destination = destination_version.content.get("destination")
    destination = (
        wrapped_destination
        if isinstance(wrapped_destination, dict)
        else destination_version.content
    )
    archive_ref = (
        f"archive-receipt://{archive_receipt['run_id']}"
        f"#{archive_receipt['receipt_hash']}"
    )
    lineage_ref = f"artifact-version://{final_media.lineage_artifact_version_id}"
    planning_source_id = (
        scope.admission.editorial_calendar_slot_id or scope.admission.id
    )
    run = ProductionWorkflowRun(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=scope.project.id,
        production_lane=scope.project.production_lane,
        planning_source_type=scope.project.planning_source_type,
        planning_source_id=planning_source_id,
        planning_source_hash="a" * 64,
        workflow_key=hashlib.sha256(f"phase6:{scope.project.id}".encode()).hexdigest(),
        start_input_hash=hashlib.sha256(
            f"phase6:start:{scope.project.id}".encode()
        ).hexdigest(),
        state="ARCHIVE_RUNNING",
        current_stage="FINALIZE",
        project_admission_decision_id=scope.admission.id,
        project_admission_decision_hash=scope.admission.decision_hash,
        production_package_artifact_version_id=package.artifact_version_id,
        production_package_hash=package.canonical_hash,
        production_readiness_receipt_artifact_version_id=(
            readiness.receipt.artifact_version_id
        ),
        production_readiness_receipt_hash=readiness.receipt.receipt_hash,
        canonical_media_timeline_ref=f"{lineage_ref}#canonical-media-timeline",
        canonical_media_timeline_hash=render.plan.content_hash,
        native_render_plan_ref=f"{lineage_ref}#native-render-plan",
        native_render_plan_hash=render.plan.content_hash,
        render_output_ref=str(render.output_path),
        render_output_checksum=render.output_checksum,
        technical_qc_receipt_ref=f"{lineage_ref}#technical-qc",
        technical_qc_receipt_hash=render.technical_qc_hash,
        creative_qc_receipt_ref=f"{lineage_ref}#creative-qc",
        creative_qc_receipt_hash=render.creative_qc_hash,
        archive_receipt_ref=archive_ref,
        archive_receipt_hash=archive_receipt["receipt_hash"],
        archive_object_ref=final_media.file_ref,
        archive_verification_state="VERIFIED",
        final_media_ref_id=final_media.id,
        final_media_ref_hash=final_media.checksum_sha256,
        destination_binding_id=destination_version.id,
        destination_binding_fingerprint=destination_version.content_hash,
        destination_binding={
            "platform": str(destination["platform"]).upper(),
            "platform_channel_id": destination["platform_channel_id"],
            "account_identity": destination["platform_account_ref"],
        },
    )
    session.add(run)
    session.flush()
    data = FinalReviewCandidateCreateV2(
        workflow_run_id=run.id,
        production_package_artifact_version_id=package.artifact_version_id,
        production_package_hash=package.canonical_hash,
        production_readiness_receipt_artifact_version_id=(
            readiness.receipt.artifact_version_id
        ),
        production_readiness_receipt_hash=readiness.receipt.receipt_hash,
        canonical_media_timeline_ref=run.canonical_media_timeline_ref,
        canonical_media_timeline_hash=run.canonical_media_timeline_hash,
        native_render_plan_ref=run.native_render_plan_ref,
        native_render_plan_hash=run.native_render_plan_hash,
        render_output_ref=run.render_output_ref,
        render_output_checksum=run.render_output_checksum,
        technical_qc_receipt_ref=run.technical_qc_receipt_ref,
        technical_qc_receipt_hash=run.technical_qc_receipt_hash,
        technical_qc_state="PASS",
        creative_qc_receipt_ref=run.creative_qc_receipt_ref,
        creative_qc_receipt_hash=run.creative_qc_receipt_hash,
        creative_qc_state="PASS",
        archive_receipt_ref=run.archive_receipt_ref,
        archive_receipt_hash=run.archive_receipt_hash,
        archive_object_ref=run.archive_object_ref,
        archive_verification_state="VERIFIED",
        final_media_ref_id=final_media.id,
        destination_binding_id=destination_version.id,
        destination_binding_fingerprint=destination_version.content_hash,
        destination_platform_channel_id=destination["platform_channel_id"],
        destination_account_identity=destination["platform_account_ref"],
        target_platform=destination["platform"],
        target_surface="LONG_FORM",
        target_market_lineage={
            "policy_snapshot_id": str(scope.policy.id),
            "destination_binding_hash": destination_version.content_hash,
        },
        publish_metadata_snapshot={
            "title": scope.project.title,
            "description": scope.project.description or "",
            "privacy_status": "PRIVATE",
            "thumbnail_required": True,
            "caption_required": True,
        },
        disclosure_snapshot={
            "rights_confirmed": True,
            "ai_disclosure_confirmed": True,
        },
    )
    candidate = ProductionPublishService(session).create_final_review_candidate(data)
    return run, candidate


def test_real_long_render_archive_persists_exact_final_media_ref_once(
    db_session,
    tmp_path: Path,
) -> None:
    scope = _phase3_scope(
        db_session,
        minimum_ms=1_500,
        target_ms=2_000,
        maximum_ms=3_500,
    )
    package = _phase3_create_package(db_session, scope)
    readiness = ProductionReadinessService(db_session).evaluate(
        package_artifact_version_id=package.artifact_version_id,
        created_by_user_id=scope.operator.id,
    )
    assert readiness.receipt is not None
    workspace = tmp_path / "persisted-long"
    request = NativeQualificationRenderRequest(
        run_key=f"phase6-final-media-{uuid.uuid4().hex[:8]}",
        workspace_root=workspace,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=scope.project.id,
        production_package_artifact_version_id=package.artifact_version_id,
        production_package_hash=package.canonical_hash,
        channel_profile_version_id=scope.profile.id,
        effective_context_snapshot_id=scope.effective.id,
        effective_context_hash=scope.effective.context_hash,
        duration_contract=scope.duration,
        production_lane=ProductionLane.LONG_FORM,
    )
    qualification = NativeQualificationService()
    render = qualification.render_from_frozen_channel(db_session, request)
    archive, _provider = _archive_render(
        tmp_path=tmp_path,
        workspace=workspace,
        render=render,
        duration=scope.duration,
        lane=ProductionLane.LONG_FORM,
        run_id="phase6-persisted-long",
    )

    first = qualification.persist_verified_final_media(
        db_session,
        request=request,
        render=render,
        archive_receipt=archive,
        created_by_user_id=scope.operator.id,
    )
    replay = qualification.persist_verified_final_media(
        db_session,
        request=request,
        render=render,
        archive_receipt=archive,
        created_by_user_id=scope.operator.id,
    )

    assert replay.id == first.id
    assert first.media_type == "LONG_FORM_FINAL"
    assert first.file_ref.startswith("drive://")
    assert first.checksum_sha256 == render.output_checksum
    assert first.production_package_artifact_version_id == package.artifact_version_id
    assert first.production_package_hash == package.canonical_hash
    assert first.cloud_media_ref_id is not None
    assert first.lineage_artifact_version_id is not None
    cloud = db_session.get(CloudMediaRef, first.cloud_media_ref_id)
    assert cloud is not None
    assert cloud.upload_status == "VERIFIED"
    assert cloud.verification_status == "CHECKSUM_VERIFIED"
    assert cloud.checksum_sha256 == render.output_checksum
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(FinalMediaRef)
            .where(
                FinalMediaRef.video_project_id == scope.project.id,
                FinalMediaRef.checksum_sha256 == render.output_checksum,
            )
        )
        == 1
    )
    run, candidate = _create_qualification_candidate(
        db_session,
        scope=scope,
        package=package,
        readiness=readiness,
        render=render,
        archive_receipt=archive,
        final_media=first,
    )
    assert run.final_review_candidate_id == candidate.id
    assert candidate.final_media_ref_id == first.id
    assert candidate.archive_verification_state == "VERIFIED"
    assert candidate.production_lane == ProductionLane.LONG_FORM
