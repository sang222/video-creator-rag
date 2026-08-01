from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import ValidationFailureError
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.m5 import ProjectAdmissionDecision
from app.db.models.workflow import Artifact, ArtifactVersion
from app.db.models.workflow import VideoProject
from app.services.config_registry import content_hash
from app.services.operator_cockpit import OperatorCockpitService
from app.services.production_package import ProductionPackageService
from app.services.production_workflow import WorkflowStageError
from app.services.production_publish import ProductionPublishService
from app.contracts.vcos_v2 import (
    DecisionReversibility,
    StrategicIntent,
    StrategicLineageV2,
)
from app.services.v2_drive_archive import (
    PersistedV2DriveArchiveReadinessGate,
    V2_DRIVE_ARCHIVE_LINEAGE_ARTIFACT_TYPE,
    V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA,
    V2_GOOGLE_DRIVE_ARCHIVE_ADAPTER_KEY,
    require_v2_google_drive_final_media,
)


class _LookupSession:
    def __init__(self, rows: dict[tuple[type[object], uuid.UUID], object]) -> None:
        self._rows = rows

    def get(self, model: type[object], identifier: uuid.UUID) -> object | None:
        return self._rows.get((model, identifier))

    def scalar(self, _statement: object) -> None:
        return None


class _DisabledDriveConfig:
    def safe_status(self) -> dict[str, object]:
        return {
            "offload_enabled": False,
            "root_folder_id_configured": False,
            "scopes": [],
        }


class _FinalReviewSession(_LookupSession):
    def __init__(
        self,
        rows: dict[tuple[type[object], uuid.UUID], object],
        lineage_row: tuple[object, object],
    ) -> None:
        super().__init__(rows)
        self._lineage_row = lineage_row

    def execute(self, _statement: object) -> SimpleNamespace:
        return SimpleNamespace(one_or_none=lambda: self._lineage_row)


def _verified_drive_authority() -> tuple[_LookupSession, SimpleNamespace, str]:
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    project_id = uuid.uuid4()
    package_id = uuid.uuid4()
    cloud_id = uuid.uuid4()
    media_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    lineage_id = uuid.uuid4()
    checksum = "a" * 64
    archive_hash = "b" * 64
    file_id = "v2-drive-final-file"
    object_ref = f"drive://{file_id}/final.mp4"
    cloud = SimpleNamespace(
        id=cloud_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        storage_provider="GOOGLE_DRIVE",
        media_type="LONG_FORM_FINAL",
        drive_file_id=file_id,
        web_view_link="https://drive.google.com/file/d/v2-drive-final-file/view",
        mime_type="video/mp4",
        size_bytes=1024,
        checksum_sha256=checksum,
        upload_status="VERIFIED",
        verification_status="CHECKSUM_VERIFIED",
        source_refs=[
            {
                "type": "v2_render_output",
                "workflow_run_id": str(uuid.uuid4()),
                "render_output_ref": "v2-native-render://example",
                "render_output_checksum": checksum,
                "production_package_artifact_version_id": str(package_id),
                "production_package_hash": "c" * 64,
            }
        ],
        technical_appendix={
            "drive_file_id_verified": True,
            "size_verified": True,
            "checksum_verified": True,
            "measured_render_duration_ms": 120_000,
        },
    )
    media = SimpleNamespace(
        id=media_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        media_type="LONG_FORM_FINAL",
        provider_key=V2_GOOGLE_DRIVE_ARCHIVE_ADAPTER_KEY,
        provider_type="MEDIA_STORAGE",
        file_ref=object_ref,
        checksum_sha256=checksum,
        cloud_media_ref_id=cloud_id,
        lineage_artifact_version_id=lineage_id,
        production_package_artifact_version_id=package_id,
        production_package_hash="c" * 64,
    )
    lineage_content = {
        "schema_version": V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA,
        "archive_receipt_hash": archive_hash,
        "archive_object_ref": object_ref,
        "cloud_media_ref_id": str(cloud_id),
        "render_output_checksum": checksum,
        "measured_render_duration_ms": 120_000,
        "storage_provider": "GOOGLE_DRIVE",
        "invokes_mr1": False,
        "automatic_publish": False,
    }
    artifact = SimpleNamespace(
        id=artifact_id,
        artifact_type=V2_DRIVE_ARCHIVE_LINEAGE_ARTIFACT_TYPE,
        current_version_id=lineage_id,
        status="approved",
    )
    lineage = SimpleNamespace(
        id=lineage_id,
        artifact_id=artifact_id,
        status="approved",
        content=lineage_content,
        content_hash=content_hash(lineage_content),
    )
    session = _LookupSession(
        {
            (FinalMediaRef, media_id): media,
            (CloudMediaRef, cloud_id): cloud,
            (ArtifactVersion, lineage_id): lineage,
            (Artifact, artifact_id): artifact,
        }
    )
    return session, media, archive_hash


def test_resolves_only_checksum_verified_non_mr1_drive_artifact() -> None:
    session, media, archive_hash = _verified_drive_authority()

    resolved = require_v2_google_drive_final_media(
        session,  # type: ignore[arg-type]
        project_id=media.video_project_id,
        final_media_id=media.id,
        expected_checksum=media.checksum_sha256,
        expected_archive_hash=archive_hash,
    )

    assert resolved.final_media is media
    assert resolved.cloud_media.drive_file_id == "v2-drive-final-file"
    assert resolved.archive_object_ref == media.file_ref


def test_rejects_legacy_mr1_lineage_even_when_drive_checksum_matches() -> None:
    session, media, archive_hash = _verified_drive_authority()
    lineage = session.get(ArtifactVersion, media.lineage_artifact_version_id)
    artifact = session.get(Artifact, lineage.artifact_id)  # type: ignore[union-attr]
    artifact.artifact_type = "mr1_final_media_lineage_receipt"  # type: ignore[union-attr]

    with pytest.raises(
        ValidationFailureError,
        match="V2_DRIVE_ARCHIVE_FINAL_MEDIA_AUTHORITY_MISMATCH",
    ):
        require_v2_google_drive_final_media(
            session,  # type: ignore[arg-type]
            project_id=media.video_project_id,
            final_media_id=media.id,
            expected_checksum=media.checksum_sha256,
            expected_archive_hash=archive_hash,
        )


def test_rejects_drive_web_link_for_a_different_file_id() -> None:
    session, media, archive_hash = _verified_drive_authority()
    cloud = session.get(CloudMediaRef, media.cloud_media_ref_id)
    cloud.web_view_link = "https://drive.google.com/file/d/other-file/view"  # type: ignore[union-attr]

    with pytest.raises(
        ValidationFailureError,
        match="V2_DRIVE_ARCHIVE_FINAL_MEDIA_AUTHORITY_MISMATCH",
    ):
        require_v2_google_drive_final_media(
            session,  # type: ignore[arg-type]
            project_id=media.video_project_id,
            final_media_id=media.id,
            expected_checksum=media.checksum_sha256,
            expected_archive_hash=archive_hash,
        )


def test_readiness_gate_blocks_before_any_drive_request_when_offload_is_disabled() -> (
    None
):
    gate = PersistedV2DriveArchiveReadinessGate(
        config_service=_DisabledDriveConfig()  # type: ignore[arg-type]
    )

    with pytest.raises(WorkflowStageError) as exc_info:
        gate.require_ready(
            session=_LookupSession({}),  # type: ignore[arg-type]
            company_id=uuid.uuid4(),
            channel_workspace_id=uuid.uuid4(),
        )

    assert exc_info.value.error_code == "V2_DRIVE_ARCHIVE_OFFLOAD_DISABLED"
    assert exc_info.value.retry_eligible is False


def test_final_review_accepts_the_v2_drive_lineage_without_mr1_reuse() -> None:
    session, media, archive_hash = _verified_drive_authority()
    cloud = session.get(CloudMediaRef, media.cloud_media_ref_id)
    lineage = session.get(ArtifactVersion, media.lineage_artifact_version_id)
    artifact = session.get(Artifact, lineage.artifact_id)  # type: ignore[union-attr]
    duration_contract = {
        "minimum_duration_ms": 60_000,
        "target_duration_ms": 120_000,
        "maximum_duration_ms": 180_000,
    }
    media.duration_contract = duration_contract
    media.duration_seconds = Decimal("120")
    media.aspect_ratio = "16:9"
    media.resolution = "1920x1080"
    media.media_qc_report_id = None
    lineage.video_project_id = media.video_project_id
    lineage.content = {
        "schema_version": V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA,
        "video_project_id": str(media.video_project_id),
        "production_package_artifact_version_id": str(
            media.production_package_artifact_version_id
        ),
        "production_package_hash": media.production_package_hash,
        "duration_contract": duration_contract,
        "canonical_media_timeline_hash": "1" * 64,
        "native_render_plan_hash": "2" * 64,
        "render_output_ref": "v2-native-render://example",
        "render_output_checksum": media.checksum_sha256,
        "technical_qc_hash": "3" * 64,
        "creative_qc_hash": "4" * 64,
        "archive_receipt_hash": archive_hash,
        "archive_state": "VERIFIED",
        "cloud_media_ref_id": str(cloud.id),
        "archive_object_ref": media.file_ref,
        "measured_render_duration_ms": 120_000,
        "storage_provider": "GOOGLE_DRIVE",
        "invokes_mr1": False,
        "automatic_publish": False,
    }
    lineage.content_hash = content_hash(lineage.content)
    artifact.video_project_id = media.video_project_id
    review_session = _FinalReviewSession(
        {
            (CloudMediaRef, cloud.id): cloud,
        },
        (lineage, artifact),
    )
    project = SimpleNamespace(
        id=media.video_project_id,
        company_id=media.company_id,
        channel_workspace_id=media.channel_workspace_id,
    )
    package = SimpleNamespace(
        duration_contract=SimpleNamespace(
            minimum_duration_ms=60_000,
            maximum_duration_ms=180_000,
            model_dump=lambda **_kwargs: duration_contract,
        )
    )
    data = SimpleNamespace(
        archive_object_ref=media.file_ref,
        production_package_artifact_version_id=(
            media.production_package_artifact_version_id
        ),
        production_package_hash=media.production_package_hash,
        canonical_media_timeline_hash="1" * 64,
        native_render_plan_hash="2" * 64,
        render_output_ref="v2-native-render://example",
        render_output_checksum=media.checksum_sha256,
        technical_qc_receipt_hash="3" * 64,
        creative_qc_receipt_hash="4" * 64,
        archive_receipt_hash=archive_hash,
    )

    ProductionPublishService(review_session)._require_final_media_authority(
        final_media=media,
        project=project,
        package_content=package,
        data=data,
    )


def _strategic_lineage() -> StrategicLineageV2:
    audience_promise = "Give operators a useful, grounded video."
    audience_promise_version = "v1"
    target_audience_definition = {"segment": "operators"}
    audience_drift_guard_version = "v1"
    strategic_intent = StrategicIntent.ACQUISITION
    intent_success_criteria = {"target": "qualified view"}
    intent_success_criteria_version = "v1"
    primary_variable_under_test = "opening_hook"
    decision_reversibility = DecisionReversibility.TWO_WAY_DOOR
    return StrategicLineageV2(
        audience_promise=audience_promise,
        audience_promise_version=audience_promise_version,
        audience_promise_hash=StrategicLineageV2.calculate_audience_promise_hash(
            audience_promise=audience_promise,
            audience_promise_version=audience_promise_version,
            target_audience_definition=target_audience_definition,
            audience_drift_guard_version=audience_drift_guard_version,
        ),
        target_audience_definition=target_audience_definition,
        audience_drift_guard_version=audience_drift_guard_version,
        strategic_intent=strategic_intent,
        intent_success_criteria=intent_success_criteria,
        intent_success_criteria_version=intent_success_criteria_version,
        intent_success_criteria_hash=(
            StrategicLineageV2.calculate_intent_success_criteria_hash(
                strategic_intent=strategic_intent,
                intent_success_criteria=intent_success_criteria,
                intent_success_criteria_version=intent_success_criteria_version,
                experiment_hypothesis=None,
                primary_variable_under_test=primary_variable_under_test,
                decision_reversibility=decision_reversibility,
            )
        ),
        primary_variable_under_test=primary_variable_under_test,
        decision_reversibility=decision_reversibility,
        active_launch_policy_version_id=uuid.uuid4(),
        active_launch_policy_hash="c" * 64,
        active_launch_run_id=uuid.uuid4(),
        active_launch_run_hash="d" * 64,
    )


def test_uploaded_video_event_projects_only_the_sealed_package_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    project_id = uuid.uuid4()
    admission_id = uuid.uuid4()
    package_id = uuid.uuid4()
    package_hash = "e" * 64
    admission_hash = "f" * 64
    lineage = _strategic_lineage()
    lineage_values = lineage.model_dump()
    project = SimpleNamespace(
        id=project_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        schema_version="v2",
        project_admission_decision_id=admission_id,
        **lineage_values,
    )
    admission = SimpleNamespace(
        id=admission_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        schema_version="v2",
        decision="ADMIT",
        admitted_video_project_id=project_id,
        decision_hash=admission_hash,
        **lineage_values,
    )
    package = SimpleNamespace(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        production_lane=SimpleNamespace(value="LONG_FORM"),
        project_admission_decision_id=admission_id,
        project_admission_decision_hash=admission_hash,
        strategic_lineage=lineage,
    )
    candidate = SimpleNamespace(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        production_lane="LONG_FORM",
        production_package_artifact_version_id=package_id,
        production_package_hash=package_hash,
    )
    session = _LookupSession(
        {
            (ArtifactVersion, package_id): SimpleNamespace(content_hash=package_hash),
            (VideoProject, project_id): project,
            (ProjectAdmissionDecision, admission_id): admission,
        }
    )
    monkeypatch.setattr(
        ProductionPackageService,
        "validate_for_readiness",
        lambda _self, _package_id: package,
    )

    payload = ProductionPublishService(
        session
    )._require_uploaded_video_strategic_lineage(  # type: ignore[arg-type]
        candidate=candidate,
    )

    assert payload == lineage.model_dump(mode="json")


def test_uploaded_video_event_rejects_missing_sealed_package_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    project_id = uuid.uuid4()
    admission_id = uuid.uuid4()
    package_id = uuid.uuid4()
    package_hash = "e" * 64
    admission_hash = "f" * 64
    lineage = _strategic_lineage()
    lineage_values = lineage.model_dump()
    project = SimpleNamespace(
        id=project_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        schema_version="v2",
        project_admission_decision_id=admission_id,
        **lineage_values,
    )
    admission = SimpleNamespace(
        id=admission_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        schema_version="v2",
        decision="ADMIT",
        admitted_video_project_id=project_id,
        decision_hash=admission_hash,
        **lineage_values,
    )
    package = SimpleNamespace(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        production_lane=SimpleNamespace(value="LONG_FORM"),
        project_admission_decision_id=admission_id,
        project_admission_decision_hash=admission_hash,
        strategic_lineage=None,
    )
    candidate = SimpleNamespace(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        production_lane="LONG_FORM",
        production_package_artifact_version_id=package_id,
        production_package_hash=package_hash,
    )
    session = _LookupSession(
        {
            (ArtifactVersion, package_id): SimpleNamespace(content_hash=package_hash),
            (VideoProject, project_id): project,
            (ProjectAdmissionDecision, admission_id): admission,
        }
    )
    monkeypatch.setattr(
        ProductionPackageService,
        "validate_for_readiness",
        lambda _self, _package_id: package,
    )

    with pytest.raises(
        ValidationFailureError,
        match="UPLOADED_VIDEO_STRATEGIC_LINEAGE_REQUIRED",
    ):
        ProductionPublishService(session)._require_uploaded_video_strategic_lineage(  # type: ignore[arg-type]
            candidate=candidate,
        )


def test_final_review_resolves_only_matching_package_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    project_id = uuid.uuid4()
    admission_id = uuid.uuid4()
    package_id = uuid.uuid4()
    package_hash = "e" * 64
    admission_hash = "f" * 64
    lineage = _strategic_lineage()
    lineage_values = lineage.model_dump()
    project = SimpleNamespace(
        id=project_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        schema_version="v2",
        production_lane="LONG_FORM",
        project_admission_decision_id=admission_id,
        **lineage_values,
    )
    admission = SimpleNamespace(
        id=admission_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        schema_version="v2",
        decision="ADMIT",
        admitted_video_project_id=project_id,
        decision_hash=admission_hash,
        **lineage_values,
    )
    package = SimpleNamespace(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        production_lane=SimpleNamespace(value="LONG_FORM"),
        project_admission_decision_id=admission_id,
        project_admission_decision_hash=admission_hash,
        strategic_lineage=lineage,
    )
    candidate = SimpleNamespace(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        production_package_artifact_version_id=package_id,
        production_package_hash=package_hash,
    )
    run = SimpleNamespace(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        production_package_artifact_version_id=package_id,
        production_package_hash=package_hash,
    )
    session = _LookupSession(
        {
            (ArtifactVersion, package_id): SimpleNamespace(content_hash=package_hash),
            (ProjectAdmissionDecision, admission_id): admission,
        }
    )
    monkeypatch.setattr(
        ProductionPackageService,
        "validate_for_readiness",
        lambda _self, _package_id: package,
    )

    resolved = OperatorCockpitService(
        session  # type: ignore[arg-type]
    )._require_final_review_strategic_lineage(
        project=project,
        run=run,
        candidate=candidate,
    )

    assert resolved == lineage
    admission.strategic_intent = StrategicIntent.AUTHORITY
    admission.intent_success_criteria_hash = (
        StrategicLineageV2.calculate_intent_success_criteria_hash(
            strategic_intent=StrategicIntent.AUTHORITY,
            intent_success_criteria=admission.intent_success_criteria,
            intent_success_criteria_version=admission.intent_success_criteria_version,
            experiment_hypothesis=admission.experiment_hypothesis,
            primary_variable_under_test=admission.primary_variable_under_test,
            decision_reversibility=admission.decision_reversibility,
        )
    )
    with pytest.raises(
        ValidationFailureError,
        match="FINAL_REVIEW_STRATEGIC_LINEAGE_MISMATCH",
    ):
        OperatorCockpitService(
            session  # type: ignore[arg-type]
        )._require_final_review_strategic_lineage(
            project=project,
            run=run,
            candidate=candidate,
        )
