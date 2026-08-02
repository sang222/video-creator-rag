"""Fail-closed V2 Google Drive archive execution and resolution.

The qualification adapter only resolves an already verified remote object.
The real adapter uses the existing Drive upload service behind a durable
effect ledger and a sealed request journal, then resolves the resulting
checksum-verified ``CloudMediaRef`` into final-media authority.  Neither path
can treat a local recovery copy as a real archive.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.m10_2 import FinalMediaRefCreate
from app.contracts.production_workflow import (
    ProductionWorkflowStage,
    WorkflowAuthorityRefs,
    WorkflowFailureClassification,
    WorkflowStageResult,
)
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.errors import ValidationFailureError
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef, GoogleDriveMediaCredential
from app.db.models.v2_effect import V2ProductionEffectLedger
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.config_registry import content_hash
from app.services.m10_2 import FinalMediaRefService
from app.services.m10_5 import (
    GOOGLE_DRIVE_SCOPE,
    GoogleDriveConfigService,
    GoogleDriveUploadService,
)
from app.services.production_package import ProductionPackageService
from app.services.production_workflow import WorkflowStageContext, WorkflowStageError
from app.services.v2_native_effects import (
    V2LocalNativeProductionAdapter,
    _load_json,
    _production_inputs,
    _required_text,
    _sha256_file,
    _write_json_atomic,
)
from app.services.v2_provider_production import (
    V2AuthorizedAdapterOperation,
    V2ProductionAdapterDescriptor,
)
from app.services.workflow import ArtifactService


V2_GOOGLE_DRIVE_ARCHIVE_ADAPTER_KEY = "v2-google-drive-archive"
V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY = "v2-google-drive-remote"
V2_DRIVE_ARCHIVE_LINEAGE_ARTIFACT_TYPE = "v2_drive_final_media_lineage_receipt"
V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA = "vcos.v2-drive-final-media-lineage.v1"
V2_DRIVE_ARCHIVE_RECEIPT_SCHEMA = "vcos.v2-drive-archive-receipt.v1"


@dataclass(frozen=True, slots=True)
class V2DriveArchiveReadiness:
    """Credential/configuration evidence checked without a Drive request."""

    credential_id: uuid.UUID
    root_folder_id: str
    scopes: tuple[str, ...]


@runtime_checkable
class V2DriveArchiveReadinessGate(Protocol):
    """Prove local configuration and a scoped connected credential exist."""

    def require_ready(
        self,
        *,
        session: Session,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
    ) -> V2DriveArchiveReadiness: ...


class PersistedV2DriveArchiveReadinessGate:
    """Read-only readiness gate for a future explicitly authorized upload.

    It intentionally does not refresh a token, call ``files.get``, list a
    Drive folder, or upload anything.  Runtime execution remains blocked until
    the connected credential and root-folder configuration are present.
    """

    def __init__(
        self,
        *,
        config_service: GoogleDriveConfigService | None = None,
    ) -> None:
        self._config_service = config_service or GoogleDriveConfigService()

    def require_ready(
        self,
        *,
        session: Session,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
    ) -> V2DriveArchiveReadiness:
        try:
            config = self._config_service.safe_status()
        except ValidationFailureError as exc:
            raise _external_block(
                "V2_DRIVE_ARCHIVE_CONFIGURATION_INVALID",
                "Google Drive archive configuration is invalid; no archive was attempted.",
            ) from exc
        if not bool(config.get("offload_enabled")):
            raise _external_block(
                "V2_DRIVE_ARCHIVE_OFFLOAD_DISABLED",
                "Google Drive archive is disabled; no archive was attempted.",
            )
        credential = session.scalar(
            select(GoogleDriveMediaCredential)
            .where(
                GoogleDriveMediaCredential.company_id == company_id,
                GoogleDriveMediaCredential.channel_workspace_id == channel_workspace_id,
                GoogleDriveMediaCredential.connection_state == "CONNECTED",
            )
            .order_by(GoogleDriveMediaCredential.updated_at.desc())
            .limit(1)
        )
        configured_scopes = tuple(str(item) for item in (config.get("scopes") or []))
        credential_scopes = tuple(
            str(item) for item in (credential.scopes if credential is not None else [])
        )
        configured_root_folder_id = str(
            self._config_service.root_folder_id() or ""
        ).strip()
        root_folder_id = str(
            credential.root_folder_id
            if credential is not None and credential.root_folder_id
            else ""
        ).strip()
        if (
            not bool(config.get("root_folder_id_configured"))
            or not configured_root_folder_id
        ):
            raise _external_block(
                "V2_DRIVE_ARCHIVE_ROOT_FOLDER_REQUIRED",
                "Google Drive archive needs a configured root folder; no archive was attempted.",
            )
        if credential is None:
            raise _external_block(
                "V2_DRIVE_ARCHIVE_CREDENTIAL_REQUIRED",
                "A connected channel-scoped Google Drive credential is required; no archive was attempted.",
            )
        if root_folder_id != configured_root_folder_id:
            raise _external_block(
                "V2_DRIVE_ARCHIVE_CREDENTIAL_ROOT_MISMATCH",
                "The connected Google Drive credential is not bound to the configured archive root; no archive was attempted.",
            )
        if (
            GOOGLE_DRIVE_SCOPE not in configured_scopes
            or GOOGLE_DRIVE_SCOPE not in credential_scopes
        ):
            raise _external_block(
                "V2_DRIVE_ARCHIVE_SCOPE_REQUIRED",
                "The Google Drive credential must use the drive.file scope; no archive was attempted.",
            )
        return V2DriveArchiveReadiness(
            credential_id=credential.id,
            root_folder_id=root_folder_id,
            scopes=credential_scopes,
        )


@dataclass(frozen=True, slots=True)
class V2VerifiedDriveArchiveArtifact:
    """The exact persisted remote artifact used by final review."""

    final_media: FinalMediaRef
    cloud_media: CloudMediaRef
    lineage: ArtifactVersion
    archive_receipt_hash: str
    archive_object_ref: str


class V2GoogleDriveArchiveAdapter:
    """Resolve an already-verified Drive artifact into V2 archive authority.

    The adapter performs only database authority writes.  It never invokes an
    upload, a remote checksum probe, or an MR1 component.  A caller must use a
    separately authorized Drive upload boundary to create the CloudMediaRef
    before this ARCHIVE command can advance.
    """

    descriptor = V2ProductionAdapterDescriptor(
        adapter_key=V2_GOOGLE_DRIVE_ARCHIVE_ADAPTER_KEY,
        supported_stages=frozenset({ProductionWorkflowStage.ARCHIVE}),
        production_eligible=True,
        fixture_only=False,
        invokes_mr1=False,
        paid_provider_calls=False,
        automatic_publish=False,
    )

    def __init__(
        self,
        *,
        readiness_gate: V2DriveArchiveReadinessGate | None = None,
    ) -> None:
        self._readiness_gate = readiness_gate or PersistedV2DriveArchiveReadinessGate()

    def execute(
        self,
        *,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> WorkflowStageResult:
        context.ensure_active()
        if (
            operation.stage != ProductionWorkflowStage.ARCHIVE
            or operation.adapter_key != V2_GOOGLE_DRIVE_ARCHIVE_ADAPTER_KEY
            or operation.execution_mode != "QUALIFICATION_LOCAL"
            or operation.paid_provider_call
            or operation.max_cost_usd != Decimal("0")
            or operation.parameters.get("mode") != "GOOGLE_DRIVE_VERIFIED_ARCHIVE"
            or operation.parameters.get("archive_resolution")
            != "PERSISTED_VERIFIED_CLOUD_MEDIA"
        ):
            raise ValidationFailureError("V2_DRIVE_ARCHIVE_OPERATION_INVALID")
        run = context.run
        if (
            run.video_project_id is None
            or run.production_package_artifact_version_id is None
            or run.production_package_hash is None
            or run.render_output_checksum is None
            or run.render_output_ref is None
        ):
            raise ValidationFailureError("V2_DRIVE_ARCHIVE_INPUT_REQUIRED")
        self._readiness_gate.require_ready(
            session=context.session,
            company_id=run.company_id,
            channel_workspace_id=run.channel_workspace_id,
        )
        artifact = _resolve_or_create_v2_drive_archive(
            session=context.session,
            context=context,
            operation=operation,
        )
        destination = _normalized_destination_for_drive(context)
        result = WorkflowStageResult(
            result_type="V2_VERIFIED_GOOGLE_DRIVE_ARCHIVE",
            result_id=artifact.final_media.id,
            result_ref=artifact.archive_object_ref,
            result_hash=artifact.archive_receipt_hash,
            result_payload={
                "archive_state": "VERIFIED",
                "storage_provider": "GOOGLE_DRIVE",
                "cloud_media_ref_id": str(artifact.cloud_media.id),
                "drive_file_id": artifact.cloud_media.drive_file_id,
                "checksum_sha256": artifact.final_media.checksum_sha256,
                "automatic_publish": False,
                "external_effect_performed": False,
            },
            authority_refs=WorkflowAuthorityRefs(
                video_project_id=run.video_project_id,
                archive_receipt_ref=(
                    f"v2-drive-archive-receipt://{artifact.cloud_media.id}/"
                    f"{artifact.archive_receipt_hash}"
                ),
                archive_receipt_hash=artifact.archive_receipt_hash,
                archive_object_ref=artifact.archive_object_ref,
                archive_verification_state="VERIFIED",
                final_media_ref_id=artifact.final_media.id,
                final_media_ref_hash=run.render_output_checksum,
                destination_binding_id=destination["id"],
                destination_binding_fingerprint=destination["content_hash"],
                destination_binding=destination["binding"],
            ),
            reason_codes=[
                "V2_GOOGLE_DRIVE_ARCHIVE_RESOLVED",
                "V2_DRIVE_ARCHIVE_NO_EXTERNAL_CALL",
            ],
        )
        return result


class V2GoogleDriveRemoteArchiveAdapter(V2LocalNativeProductionAdapter):
    """Upload one exact V2 render to channel-scoped Google Drive authority.

    The inherited V2 effect ledger records ``EFFECT_STARTED`` before this
    method runs.  This adapter additionally seals a filesystem request journal
    before making the Drive call.  Therefore a crash after submission, but
    before the verified ``CloudMediaRef`` transaction commits, is fail-closed
    rather than a second upload under the same workflow command.
    """

    descriptor = V2ProductionAdapterDescriptor(
        adapter_key=V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY,
        supported_stages=frozenset({ProductionWorkflowStage.ARCHIVE}),
        production_eligible=True,
        fixture_only=False,
        invokes_mr1=False,
        paid_provider_calls=False,
        automatic_publish=False,
    )

    def __init__(
        self,
        *,
        readiness_gate: V2DriveArchiveReadinessGate | None = None,
        upload_service_factory: Callable[[Session], GoogleDriveUploadService]
        | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._readiness_gate = readiness_gate or PersistedV2DriveArchiveReadinessGate()
        self._upload_service_factory = (
            upload_service_factory or GoogleDriveUploadService
        )

    def _validate_operation(
        self,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> None:
        details = operation.parameters.get("provider_execution")
        if (
            context.run.production_lane != "LONG_FORM"
            or context.run.planning_source_type != "LONG_FORM_PLAN"
            or operation.stage != ProductionWorkflowStage.ARCHIVE
            or operation.adapter_key != V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY
            or operation.execution_mode != "REAL_LONG_FORM_PRODUCTION"
            or operation.paid_provider_call
            or operation.max_cost_usd != Decimal("0")
            or operation.parameters.get("mode") != "GOOGLE_DRIVE_REMOTE_ARCHIVE"
            or not isinstance(details, dict)
            or details.get("provider") != "google_drive"
            or details.get("credential_ref") != "oauth://google-drive/channel-connected"
            or details.get("attempt_limit") != 1
            or not isinstance(details.get("idempotency_key"), str)
            or not details["idempotency_key"].strip()
            or details.get("remote_object_required") is not True
            or details.get("checksum_readback_required") is not True
        ):
            raise ValidationFailureError("V2_GOOGLE_DRIVE_REMOTE_OPERATION_INVALID")

    def _archive(
        self,
        *,
        ledger_id: uuid.UUID,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> tuple[WorkflowStageResult, dict[str, Any]]:
        with self._session_factory() as session:
            run, project, _package, _script, _visual = _production_inputs(
                session, context.run.id
            )
            render_ledger = session.scalar(
                select(V2ProductionEffectLedger).where(
                    V2ProductionEffectLedger.workflow_run_id == run.id,
                    V2ProductionEffectLedger.stage == "RENDER",
                    V2ProductionEffectLedger.state == "VERIFIED",
                )
            )
            if render_ledger is None:
                raise ValidationFailureError("V2_GOOGLE_DRIVE_REMOTE_RENDER_REQUIRED")
            render_journal = dict(render_ledger.effect_journal or {})
        source = self._from_relative(
            _required_text(render_journal, "output_relative_path")
        )
        checksum = _sha256_file(source)
        if checksum != run.render_output_checksum:
            raise ValidationFailureError(
                "V2_GOOGLE_DRIVE_REMOTE_SOURCE_CHECKSUM_MISMATCH"
            )
        measured_duration_ms = int(
            render_journal.get("measured_render_duration_ms") or 0
        )
        if measured_duration_ms <= 0:
            raise ValidationFailureError("V2_GOOGLE_DRIVE_REMOTE_DURATION_REQUIRED")

        self._readiness_gate.require_ready(
            session=context.session,
            company_id=run.company_id,
            channel_workspace_id=run.channel_workspace_id,
        )
        artifact = self._resolve_existing_or_upload(
            context=context,
            operation=operation,
            source=source,
            checksum=checksum,
            measured_duration_ms=measured_duration_ms,
        )
        destination = _normalized_destination_for_drive(context)
        journal = {
            "schema_version": "vcos.production-effect-journal.v1",
            "command_id": context.command_id,
            "stage": "ARCHIVE",
            "state": "VERIFIED",
            "effect_invocation_count": 1,
            "provider_call_count": 1,
            "provider": "google_drive",
            "idempotency_key": operation.parameters["provider_execution"][
                "idempotency_key"
            ],
            "source_relative_path": self._relative(source),
            "source_checksum": checksum,
            "measured_render_duration_ms": measured_duration_ms,
            "cloud_media_ref_id": str(artifact.cloud_media.id),
            "drive_file_id": artifact.cloud_media.drive_file_id,
            "archive_receipt_hash": artifact.archive_receipt_hash,
            "archive_object_ref": artifact.archive_object_ref,
            "external_effect_performed": True,
        }
        _write_json_atomic(
            self._effect_dir(context.command_id) / "google-drive-archive-receipt.json",
            journal,
        )
        return (
            WorkflowStageResult(
                result_type="V2_VERIFIED_GOOGLE_DRIVE_REMOTE_ARCHIVE",
                result_id=artifact.final_media.id,
                result_ref=artifact.archive_object_ref,
                result_hash=artifact.archive_receipt_hash,
                result_payload={
                    "archive_state": "VERIFIED",
                    "storage_provider": "GOOGLE_DRIVE",
                    "cloud_media_ref_id": str(artifact.cloud_media.id),
                    "drive_file_id": artifact.cloud_media.drive_file_id,
                    "checksum_sha256": artifact.final_media.checksum_sha256,
                    "external_effect_performed": True,
                    "automatic_publish": False,
                },
                authority_refs=WorkflowAuthorityRefs(
                    video_project_id=run.video_project_id,
                    archive_receipt_ref=(
                        f"v2-drive-archive-receipt://{artifact.cloud_media.id}/"
                        f"{artifact.archive_receipt_hash}"
                    ),
                    archive_receipt_hash=artifact.archive_receipt_hash,
                    archive_object_ref=artifact.archive_object_ref,
                    archive_verification_state="VERIFIED",
                    final_media_ref_id=artifact.final_media.id,
                    final_media_ref_hash=run.render_output_checksum,
                    destination_binding_id=destination["id"],
                    destination_binding_fingerprint=destination["content_hash"],
                    destination_binding=destination["binding"],
                ),
                reason_codes=[
                    "V2_GOOGLE_DRIVE_REMOTE_ARCHIVE_VERIFIED",
                    "V2_GOOGLE_DRIVE_CHECKSUM_READBACK_VERIFIED",
                ],
            ),
            journal,
        )

    def _resolve_existing_or_upload(
        self,
        *,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
        source: Any,
        checksum: str,
        measured_duration_ms: int,
    ) -> V2VerifiedDriveArchiveArtifact:
        try:
            return _resolve_or_create_v2_drive_archive(
                session=context.session,
                context=context,
                operation=operation,
                external_effect_performed=True,
            )
        except WorkflowStageError as exc:
            if exc.error_code != "V2_DRIVE_ARCHIVE_ARTIFACT_REQUIRED":
                raise

        details = dict(operation.parameters["provider_execution"])
        effect_dir = self._effect_dir(context.command_id)
        request_path = effect_dir / "google-drive-archive-request-journal.json"
        identity = {
            "schema_version": "vcos.v2-google-drive-archive-request.v1",
            "command_id": context.command_id,
            "operation_id": operation.operation_id,
            "idempotency_key": details["idempotency_key"],
            "source_relative_path": self._relative(source),
            "source_checksum": checksum,
            "source_size_bytes": source.stat().st_size,
            "measured_render_duration_ms": measured_duration_ms,
            "attempt_limit": 1,
        }
        if request_path.exists():
            prior = _load_json(request_path)
            if any(prior.get(key) != value for key, value in identity.items()):
                raise ValidationFailureError("V2_GOOGLE_DRIVE_REQUEST_JOURNAL_MISMATCH")
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                error_code="V2_GOOGLE_DRIVE_OUTCOME_UNCERTAIN",
                summary=(
                    "A Google Drive archive request was submitted without a "
                    "sealed checksum-verified authority; no duplicate upload "
                    "was attempted."
                ),
                incident_type="PROVIDER_OUTCOME_UNCERTAIN",
                retry_eligible=False,
            )
        _write_json_atomic(request_path, {**identity, "state": "SUBMITTED"})
        try:
            cloud, verification = self._upload_service_factory(
                context.session
            ).upload_verified(
                local_path=source,
                media_type="LONG_FORM_FINAL",
                company_id=context.run.company_id,
                channel_workspace_id=context.run.channel_workspace_id,
                video_project_id=context.run.video_project_id,
                uploaded_video_id=None,
                render_package_id=None,
                source_refs=[
                    {
                        "type": "v2_render_output",
                        "workflow_run_id": str(context.run.id),
                        "render_output_ref": context.run.render_output_ref,
                        "render_output_checksum": context.run.render_output_checksum,
                        "production_package_artifact_version_id": str(
                            context.run.production_package_artifact_version_id
                        ),
                        "production_package_hash": context.run.production_package_hash,
                    }
                ],
                retention_policy={
                    "keep_local": True,
                    "cleanup_authorized": False,
                    "source": "v2-real-archive",
                },
                idempotency_key=details["idempotency_key"],
            )
        except Exception as exc:
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                error_code="V2_GOOGLE_DRIVE_ARCHIVE_PROVIDER_FAILURE",
                summary=(
                    "Google Drive archive did not yield a sealed verified "
                    "response; no retry or local archive fallback was attempted."
                ),
                incident_type="PROVIDER_OUTCOME_UNCERTAIN",
                retry_eligible=False,
            ) from exc
        if (
            verification.verification_status != "CHECKSUM_VERIFIED"
            or verification.checksum_verified is not True
        ):
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                error_code="V2_GOOGLE_DRIVE_CHECKSUM_READBACK_REQUIRED",
                summary=(
                    "Google Drive did not provide a checksum-verified readback; "
                    "the archive cannot become final-media authority."
                ),
                incident_type="ARCHIVE_VERIFICATION_BLOCK",
                retry_eligible=False,
            )
        cloud.technical_appendix = {
            **(cloud.technical_appendix or {}),
            "measured_render_duration_ms": measured_duration_ms,
            "v2_archive_command_id": context.command_id,
            "v2_archive_idempotency_key": details["idempotency_key"],
            "v2_remote_archive": True,
        }
        context.session.flush()
        artifact = _resolve_or_create_v2_drive_archive(
            session=context.session,
            context=context,
            operation=operation,
            external_effect_performed=True,
        )
        context.session.commit()
        return artifact


def require_v2_google_drive_final_media(
    session: Session,
    *,
    project_id: uuid.UUID | None,
    final_media_id: uuid.UUID,
    expected_checksum: str | None,
    expected_archive_hash: str,
) -> V2VerifiedDriveArchiveArtifact:
    """Resolve exactly one non-MR1 verified Drive final-media authority."""

    media = session.get(FinalMediaRef, final_media_id)
    cloud = (
        session.get(CloudMediaRef, media.cloud_media_ref_id)
        if media is not None and media.cloud_media_ref_id is not None
        else None
    )
    lineage = (
        session.get(ArtifactVersion, media.lineage_artifact_version_id)
        if media is not None and media.lineage_artifact_version_id is not None
        else None
    )
    lineage_artifact = (
        session.get(Artifact, lineage.artifact_id) if lineage is not None else None
    )
    archive_object_ref = _drive_object_ref(cloud.drive_file_id) if cloud else ""
    content = (
        lineage.content
        if lineage is not None and isinstance(lineage.content, dict)
        else {}
    )
    measured_duration_ms = _verified_duration_ms(cloud) if cloud is not None else None
    if (
        media is None
        or project_id is None
        or media.video_project_id != project_id
        or media.media_type != "LONG_FORM_FINAL"
        or media.provider_key
        not in {V2_GOOGLE_DRIVE_ARCHIVE_ADAPTER_KEY, V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY}
        or media.provider_type != "MEDIA_STORAGE"
        or media.file_ref != archive_object_ref
        or media.checksum_sha256 != expected_checksum
        or cloud is None
        or cloud.company_id != media.company_id
        or cloud.channel_workspace_id != media.channel_workspace_id
        or cloud.video_project_id != project_id
        or cloud.storage_provider != "GOOGLE_DRIVE"
        or cloud.media_type != "LONG_FORM_FINAL"
        or cloud.checksum_sha256 != expected_checksum
        or cloud.upload_status != "VERIFIED"
        or cloud.verification_status != "CHECKSUM_VERIFIED"
        or not _drive_remote_identity_valid(cloud)
        or not _cloud_appendix_verifies_checksum(cloud)
        or measured_duration_ms is None
        or not _has_final_media_render_source(cloud, media, expected_checksum)
        or lineage is None
        or lineage_artifact is None
        or lineage_artifact.artifact_type != V2_DRIVE_ARCHIVE_LINEAGE_ARTIFACT_TYPE
        or lineage_artifact.current_version_id != lineage.id
        or lineage_artifact.status != "approved"
        or lineage.status != "approved"
        or lineage.content_hash != content_hash(content)
        or content.get("schema_version") != V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA
        or content.get("archive_receipt_hash") != expected_archive_hash
        or content.get("archive_object_ref") != archive_object_ref
        or content.get("cloud_media_ref_id") != str(cloud.id)
        or content.get("render_output_checksum") != expected_checksum
        or content.get("measured_render_duration_ms") != measured_duration_ms
        or content.get("storage_provider") != "GOOGLE_DRIVE"
        or content.get("invokes_mr1") is not False
        or content.get("automatic_publish") is not False
    ):
        raise ValidationFailureError("V2_DRIVE_ARCHIVE_FINAL_MEDIA_AUTHORITY_MISMATCH")
    return V2VerifiedDriveArchiveArtifact(
        final_media=media,
        cloud_media=cloud,
        lineage=lineage,
        archive_receipt_hash=expected_archive_hash,
        archive_object_ref=archive_object_ref,
    )


def _resolve_or_create_v2_drive_archive(
    *,
    session: Session,
    context: WorkflowStageContext,
    operation: V2AuthorizedAdapterOperation,
    external_effect_performed: bool = False,
) -> V2VerifiedDriveArchiveArtifact:
    run = context.run
    project = session.get(VideoProject, run.video_project_id)
    if (
        project is None
        or project.company_id != run.company_id
        or project.channel_workspace_id != run.channel_workspace_id
        or project.schema_version != "v2"
        or project.production_lane != "LONG_FORM"
        or project.planning_source_type != "LONG_FORM_PLAN"
    ):
        raise ValidationFailureError("V2_DRIVE_ARCHIVE_PROJECT_REQUIRED")
    package = ProductionPackageService(session).validate_for_readiness(
        run.production_package_artifact_version_id
    )
    if (
        package.video_project_id != project.id
        or package.production_lane.value != run.production_lane
    ):
        raise ValidationFailureError("V2_DRIVE_ARCHIVE_PACKAGE_MISMATCH")
    candidates = list(
        session.scalars(
            select(CloudMediaRef).where(
                CloudMediaRef.company_id == run.company_id,
                CloudMediaRef.channel_workspace_id == run.channel_workspace_id,
                CloudMediaRef.video_project_id == project.id,
                CloudMediaRef.storage_provider == "GOOGLE_DRIVE",
                CloudMediaRef.media_type == "LONG_FORM_FINAL",
                CloudMediaRef.checksum_sha256 == run.render_output_checksum,
                CloudMediaRef.upload_status == "VERIFIED",
                CloudMediaRef.verification_status == "CHECKSUM_VERIFIED",
            )
        )
    )
    candidates = [
        cloud
        for cloud in candidates
        if _drive_remote_identity_valid(cloud)
        and _cloud_appendix_verifies_checksum(cloud)
        and _has_exact_render_source(cloud, run, run.render_output_checksum)
    ]
    if not candidates:
        raise _external_block(
            "V2_DRIVE_ARCHIVE_ARTIFACT_REQUIRED",
            "A checksum-verified Google Drive final artifact for this exact render is required before final review.",
        )
    if len(candidates) != 1:
        raise ValidationFailureError("V2_DRIVE_ARCHIVE_ARTIFACT_AMBIGUOUS")
    cloud = candidates[0]
    measured_duration_ms = _verified_duration_ms(cloud)
    if measured_duration_ms is None:
        raise _external_block(
            "V2_DRIVE_ARCHIVE_DURATION_EVIDENCE_REQUIRED",
            "The checksum-verified Drive artifact lacks measured render-duration evidence.",
        )
    if not (
        package.duration_contract.minimum_duration_ms
        <= measured_duration_ms
        <= package.duration_contract.maximum_duration_ms
    ):
        raise ValidationFailureError("V2_DRIVE_ARCHIVE_DURATION_OUTSIDE_CONTRACT")
    archive_object_ref = _drive_object_ref(cloud.drive_file_id)
    receipt = {
        "schema_version": V2_DRIVE_ARCHIVE_RECEIPT_SCHEMA,
        "workflow_run_id": str(run.id),
        "archive_command_id": context.command_id,
        "provider_operation_id": operation.operation_id,
        "video_project_id": str(project.id),
        "production_package_artifact_version_id": str(
            run.production_package_artifact_version_id
        ),
        "production_package_hash": run.production_package_hash,
        "render_output_ref": run.render_output_ref,
        "render_output_checksum": run.render_output_checksum,
        "cloud_media_ref_id": str(cloud.id),
        "drive_file_id": cloud.drive_file_id,
        "archive_object_ref": archive_object_ref,
        "size_bytes": cloud.size_bytes,
        "checksum_sha256": cloud.checksum_sha256,
        "measured_render_duration_ms": measured_duration_ms,
        "verification_status": cloud.verification_status,
        "archive_state": "VERIFIED",
        "invokes_mr1": False,
        "automatic_publish": False,
        "external_effect_performed": external_effect_performed,
    }
    receipt_hash = content_hash(receipt)
    lineage_content = {
        "schema_version": V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA,
        "workflow_run_id": str(run.id),
        "archive_command_id": context.command_id,
        "provider_operation_id": operation.operation_id,
        "video_project_id": str(project.id),
        "production_package_artifact_version_id": str(
            run.production_package_artifact_version_id
        ),
        "production_package_hash": run.production_package_hash,
        "duration_contract": package.duration_contract.model_dump(mode="json"),
        "canonical_media_timeline_hash": run.canonical_media_timeline_hash,
        "native_render_plan_hash": run.native_render_plan_hash,
        "render_output_ref": run.render_output_ref,
        "render_output_checksum": run.render_output_checksum,
        "measured_render_duration_ms": measured_duration_ms,
        "technical_qc_hash": run.technical_qc_receipt_hash,
        "creative_qc_hash": run.creative_qc_receipt_hash,
        "archive_receipt_hash": receipt_hash,
        "archive_state": "VERIFIED",
        "cloud_media_ref_id": str(cloud.id),
        "archive_object_ref": archive_object_ref,
        "storage_provider": "GOOGLE_DRIVE",
        "invokes_mr1": False,
        "automatic_publish": False,
        "external_effect_performed": external_effect_performed,
    }
    lineage = _ensure_lineage(
        session=session,
        project=project,
        command_id=context.command_id,
        content=lineage_content,
        producer_key=operation.adapter_key,
        external_effect_performed=external_effect_performed,
    )
    final_media = _ensure_final_media(
        session=session,
        project=project,
        package=package,
        run=context.run,
        cloud=cloud,
        lineage=lineage,
        archive_object_ref=archive_object_ref,
        measured_duration_ms=measured_duration_ms,
        provider_key=operation.adapter_key,
    )
    return require_v2_google_drive_final_media(
        session,
        project_id=project.id,
        final_media_id=final_media.id,
        expected_checksum=run.render_output_checksum,
        expected_archive_hash=receipt_hash,
    )


def _ensure_lineage(
    *,
    session: Session,
    project: VideoProject,
    command_id: str,
    content: dict[str, Any],
    producer_key: str,
    external_effect_performed: bool,
) -> ArtifactVersion:
    expected_hash = content_hash(content)
    existing = session.execute(
        select(ArtifactVersion, Artifact)
        .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
        .where(
            Artifact.video_project_id == project.id,
            Artifact.artifact_type == V2_DRIVE_ARCHIVE_LINEAGE_ARTIFACT_TYPE,
            ArtifactVersion.content_hash == expected_hash,
        )
    ).one_or_none()
    if existing is not None:
        version, artifact = existing
        domain = (version.packaging_metadata or {}).get("_vcos_domain_authority")
        if (
            artifact.current_version_id != version.id
            or artifact.status != "approved"
            or version.status != "approved"
            or version.content != content
            or not isinstance(domain, dict)
            or domain.get("writer") != "server_domain_service"
            or (version.packaging_metadata or {}).get("archive_command_id")
            != command_id
        ):
            raise ValidationFailureError("V2_DRIVE_ARCHIVE_LINEAGE_MISMATCH")
        return version
    artifact_service = ArtifactService(session)
    artifact = artifact_service.create_artifact(
        data=ArtifactCreate(
            video_project_id=project.id,
            artifact_type=V2_DRIVE_ARCHIVE_LINEAGE_ARTIFACT_TYPE,
            status="approved",
            created_by_user_id=project.created_by_user_id,
        ),
        correlation_id=f"v2-drive-archive-{command_id}",
        trusted_authority_write=True,
    )
    version = artifact_service.create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            content=content,
            status="approved",
            created_by_user_id=project.created_by_user_id,
            external_entity_refs=[],
            packaging_metadata={
                "producer": producer_key,
                "archive_command_id": command_id,
                "external_effect_performed": external_effect_performed,
            },
            media_qc_metadata={
                "technical_qc_hash": content["technical_qc_hash"],
                "creative_qc_hash": content["creative_qc_hash"],
            },
            source_manifest={
                "items": [
                    {
                        "type": "production_package",
                        "artifact_version_id": content[
                            "production_package_artifact_version_id"
                        ],
                        "content_hash": content["production_package_hash"],
                    },
                    {
                        "type": "checksum_verified_google_drive_cloud_media",
                        "cloud_media_ref_id": content["cloud_media_ref_id"],
                        "render_output_checksum": content["render_output_checksum"],
                    },
                ]
            },
            evidence_refs=[],
            context_refs=[],
            claim_refs=[],
        ),
        correlation_id=f"v2-drive-archive-{command_id}",
        trusted_authority_write=True,
    )
    artifact.status = "approved"
    if version.content_hash != expected_hash:
        raise ValidationFailureError("V2_DRIVE_ARCHIVE_LINEAGE_HASH_MISMATCH")
    session.flush()
    return version


def _ensure_final_media(
    *,
    session: Session,
    project: VideoProject,
    package: Any,
    run: Any,
    cloud: CloudMediaRef,
    lineage: ArtifactVersion,
    archive_object_ref: str,
    measured_duration_ms: int,
    provider_key: str,
) -> FinalMediaRef:
    existing = session.scalar(
        select(FinalMediaRef).where(
            FinalMediaRef.video_project_id == project.id,
            FinalMediaRef.production_package_artifact_version_id
            == run.production_package_artifact_version_id,
            FinalMediaRef.production_package_hash == run.production_package_hash,
            FinalMediaRef.checksum_sha256 == run.render_output_checksum,
        )
    )
    duration_seconds = Decimal(measured_duration_ms) / Decimal(1000)
    if existing is not None:
        if (
            existing.file_ref != archive_object_ref
            or existing.cloud_media_ref_id != cloud.id
            or existing.lineage_artifact_version_id != lineage.id
            or existing.provider_key != provider_key
            or existing.provider_type != "MEDIA_STORAGE"
            or existing.duration_seconds != duration_seconds
        ):
            raise ValidationFailureError("V2_DRIVE_ARCHIVE_FINAL_MEDIA_MISMATCH")
        return existing
    return FinalMediaRefService(session).create(
        data=FinalMediaRefCreate(
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            video_project_id=project.id,
            production_package_artifact_version_id=(
                run.production_package_artifact_version_id
            ),
            production_package_hash=run.production_package_hash,
            duration_contract=package.duration_contract,
            media_type="LONG_FORM_FINAL",
            file_ref=archive_object_ref,
            duration_seconds=duration_seconds,
            aspect_ratio="16:9",
            resolution="1920x1080",
            provider_key=provider_key,
            provider_type="MEDIA_STORAGE",
            checksum_sha256=run.render_output_checksum,
            cloud_media_ref_id=cloud.id,
            lineage_artifact_version_id=lineage.id,
        )
    )


def _normalized_destination_for_drive(context: WorkflowStageContext) -> dict[str, Any]:
    """Avoid importing the V2 gateway helper at module import time."""

    from app.services.v2_provider_production import (
        _destination_authority,
        _normalized_destination,
    )

    destination = _destination_authority(context)
    return {
        "id": destination.id,
        "content_hash": destination.content_hash,
        "binding": _normalized_destination(destination.content),
    }


def _has_exact_render_source(
    cloud: CloudMediaRef,
    authority: Any,
    expected_checksum: str | None,
) -> bool:
    """Require the upload record to be bound to this exact V2 render output."""

    run_id = getattr(authority, "id", None)
    render_output_ref = getattr(authority, "render_output_ref", None)
    package_id = getattr(authority, "production_package_artifact_version_id", None)
    package_hash = getattr(authority, "production_package_hash", None)
    required = {
        "type": "v2_render_output",
        "workflow_run_id": str(run_id),
        "render_output_ref": render_output_ref,
        "render_output_checksum": expected_checksum,
        "production_package_artifact_version_id": str(package_id),
        "production_package_hash": package_hash,
    }
    return any(
        isinstance(item, dict)
        and all(item.get(key) == value for key, value in required.items())
        for item in (cloud.source_refs or [])
    )


def _has_final_media_render_source(
    cloud: CloudMediaRef,
    media: FinalMediaRef,
    expected_checksum: str | None,
) -> bool:
    """Keep the remote upload tied to the V2 package after workflow closure."""

    required = {
        "type": "v2_render_output",
        "render_output_checksum": expected_checksum,
        "production_package_artifact_version_id": str(
            media.production_package_artifact_version_id
        ),
        "production_package_hash": media.production_package_hash,
    }
    return any(
        isinstance(item, dict)
        and all(item.get(key) == value for key, value in required.items())
        for item in (cloud.source_refs or [])
    )


def _cloud_appendix_verifies_checksum(cloud: CloudMediaRef) -> bool:
    appendix = cloud.technical_appendix
    return bool(
        isinstance(appendix, dict)
        and appendix.get("drive_file_id_verified") is True
        and appendix.get("size_verified") is True
        and appendix.get("checksum_verified") is True
    )


def _verified_duration_ms(cloud: CloudMediaRef) -> int | None:
    appendix = cloud.technical_appendix
    value = (
        appendix.get("measured_render_duration_ms")
        if isinstance(appendix, dict)
        else None
    )
    if isinstance(value, bool):
        return None
    try:
        duration_ms = int(value)
    except (TypeError, ValueError):
        return None
    return duration_ms if duration_ms > 0 and str(value) == str(duration_ms) else None


def _drive_remote_identity_valid(cloud: CloudMediaRef) -> bool:
    if (
        not cloud.drive_file_id
        or not cloud.web_view_link
        or cloud.size_bytes is None
        or cloud.size_bytes <= 0
        or not cloud.checksum_sha256
        or cloud.mime_type != "video/mp4"
    ):
        return False
    parsed = urlparse(cloud.web_view_link)
    if parsed.scheme != "https" or parsed.netloc not in {
        "drive.google.com",
        "docs.google.com",
    }:
        return False
    file_id = str(cloud.drive_file_id)
    if file_id in parse_qs(parsed.query).get("id", []):
        return True
    parts = [part for part in parsed.path.split("/") if part]
    return any(
        part == "d" and index + 1 < len(parts) and parts[index + 1] == file_id
        for index, part in enumerate(parts)
    )


def _drive_object_ref(drive_file_id: str) -> str:
    if not drive_file_id or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in drive_file_id
    ):
        raise ValidationFailureError("V2_DRIVE_ARCHIVE_FILE_ID_INVALID")
    return f"drive://{drive_file_id}/final.mp4"


def _external_block(error_code: str, summary: str) -> WorkflowStageError:
    return WorkflowStageError(
        classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
        error_code=error_code,
        summary=summary,
        incident_type="EXTERNAL_CONFIGURATION",
        retry_eligible=False,
        operator_visible_blocker=summary,
    )


__all__ = [
    "PersistedV2DriveArchiveReadinessGate",
    "V2DriveArchiveReadiness",
    "V2DriveArchiveReadinessGate",
    "V2GoogleDriveArchiveAdapter",
    "V2GoogleDriveRemoteArchiveAdapter",
    "V2VerifiedDriveArchiveArtifact",
    "V2_DRIVE_ARCHIVE_LINEAGE_ARTIFACT_TYPE",
    "V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA",
    "V2_GOOGLE_DRIVE_ARCHIVE_ADAPTER_KEY",
    "V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY",
    "require_v2_google_drive_final_media",
]
