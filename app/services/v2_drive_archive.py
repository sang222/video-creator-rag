"""Fail-closed V2 Google Drive archive execution and resolution.

The qualification adapter only resolves an already verified remote object.
The real adapter uses the existing Drive upload service behind a durable
effect ledger and a sealed request journal, then resolves the resulting
checksum-verified ``CloudMediaRef`` into final-media authority.  Neither path
can treat a local recovery copy as a real archive.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
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
    CloudMediaRefService,
    GoogleDriveConfigService,
    GoogleDriveUploadResult,
    GoogleDriveUploadService,
    GoogleDriveVerificationResult,
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
V2_DRIVE_CAPTION_REVIEW_SCHEMA = "vcos.v2-drive-caption-review.v1"
V2_DRIVE_ARCHIVE_RECONCILIATION_SCHEMA = (
    "vcos.v2-google-drive-archive-get-only-reconciliation.v1"
)
V2_DRIVE_CAPTION_SIDECAR_LABEL = (
    "Tệp phụ đề SRT rời đã xác minh trên Google Drive (sidecar, không phải chữ "
    "được chèn vào khung hình)."
)


def _media_workflow_run_id_for_ai_visual_archive(
    *,
    session: Session,
    run: Any,
) -> uuid.UUID:
    """Resolve caption lineage without conflating normal and governed runs."""

    visual_run_id = run.ai_visual_production_run_id
    if visual_run_id is None:
        return run.id

    from app.db.models.ai_visual import AIVisualProductionRun

    visual_run = session.get(AIVisualProductionRun, visual_run_id)
    if (
        visual_run is None
        or visual_run.id != visual_run_id
        or visual_run.workflow_run_id != run.id
        or visual_run.video_project_id != run.video_project_id
    ):
        raise ValidationFailureError(
            "V2_GOOGLE_DRIVE_REMOTE_AI_VISUAL_AUTHORITY_REQUIRED"
        )
    if visual_run.execution_kind == "NORMAL_PRODUCTION":
        if visual_run.rerender_authority_id is not None:
            raise ValidationFailureError(
                "V2_GOOGLE_DRIVE_REMOTE_AI_VISUAL_AUTHORITY_REQUIRED"
            )
        return run.id
    if (
        visual_run.execution_kind == "GOVERNED_RERENDER"
        and visual_run.rerender_authority_id is not None
    ):
        from app.services.ai_visual_rerender_authority import (
            resolve_governed_ai_visual_rerender_execution_authority,
        )

        governed = resolve_governed_ai_visual_rerender_execution_authority(
            session,
            workflow_run_id=run.id,
            required=True,
        )
        if governed is None or governed.source_workflow.id == run.id:
            raise ValidationFailureError(
                "V2_GOOGLE_DRIVE_REMOTE_AI_VISUAL_AUTHORITY_REQUIRED"
            )
        return governed.source_workflow.id
    raise ValidationFailureError("V2_GOOGLE_DRIVE_REMOTE_AI_VISUAL_AUTHORITY_REQUIRED")


def _remote_archive_request_identity(
    *,
    command_id: str,
    operation_id: str,
    idempotency_key: str,
    source_relative_path: str,
    source_checksum: str,
    source_size_bytes: int,
    measured_render_duration_ms: int,
    caption_relative_path: str,
    sidecar: Mapping[str, str],
) -> dict[str, Any]:
    """Canonical identity sealed before either Google Drive submission."""

    return {
        "schema_version": "vcos.v2-google-drive-archive-request.v1",
        "command_id": command_id,
        "operation_id": operation_id,
        "idempotency_key": idempotency_key,
        "source_relative_path": source_relative_path,
        "source_checksum": source_checksum,
        "source_size_bytes": source_size_bytes,
        "measured_render_duration_ms": measured_render_duration_ms,
        "caption_relative_path": caption_relative_path,
        "caption_checksum": sidecar["caption_checksum"],
        "caption_ref": sidecar["caption_ref"],
        "caption_artifact_hash": sidecar["caption_artifact_hash"],
        "subtitle_qc_ref": sidecar["subtitle_qc_ref"],
        "subtitle_qc_hash": sidecar["subtitle_qc_hash"],
        "attempt_limit": 1,
    }


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
    caption_cloud_media: CloudMediaRef
    lineage: ArtifactVersion
    archive_receipt_hash: str
    archive_object_ref: str
    caption_archive_object_ref: str


@dataclass(frozen=True, slots=True)
class _V2DriveGETOnlyFileProof:
    """One exact remote Drive object resolved without a provider write."""

    media_type: str
    idempotency_key: str
    folder_path: tuple[str, ...]
    upload_result: GoogleDriveUploadResult
    verification: GoogleDriveVerificationResult
    checksum_readback_performed: bool


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
                "caption_cloud_media_ref_id": str(artifact.caption_cloud_media.id),
                "caption_drive_file_id": artifact.caption_cloud_media.drive_file_id,
                "caption_archive_object_ref": artifact.caption_archive_object_ref,
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
            media_workflow_run_id = _media_workflow_run_id_for_ai_visual_archive(
                session=session,
                run=run,
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
            media_ledger = session.scalar(
                select(V2ProductionEffectLedger).where(
                    V2ProductionEffectLedger.workflow_run_id == media_workflow_run_id,
                    V2ProductionEffectLedger.stage == "MEDIA",
                    V2ProductionEffectLedger.state == "VERIFIED",
                )
            )
            if media_ledger is None:
                raise ValidationFailureError("V2_GOOGLE_DRIVE_REMOTE_CAPTION_REQUIRED")
            media_journal = dict(media_ledger.effect_journal or {})
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
        sidecar = _sidecar_archive_authority(media_journal)
        caption_source = self._from_relative(sidecar["caption_relative_path"])
        if (
            not caption_source.is_file()
            or caption_source.is_symlink()
            or _sha256_file(caption_source) != sidecar["caption_checksum"]
        ):
            raise ValidationFailureError("CAPTION_SIDECAR_MISSING")

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
            caption_source=caption_source,
            sidecar=sidecar,
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
            "caption_ref": sidecar["caption_ref"],
            "caption_checksum": sidecar["caption_checksum"],
            "subtitle_qc_ref": sidecar["subtitle_qc_ref"],
            "caption_cloud_media_ref_id": str(artifact.caption_cloud_media.id),
            "caption_drive_file_id": artifact.caption_cloud_media.drive_file_id,
            "caption_archive_object_ref": artifact.caption_archive_object_ref,
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
                    "caption_cloud_media_ref_id": str(artifact.caption_cloud_media.id),
                    "caption_drive_file_id": artifact.caption_cloud_media.drive_file_id,
                    "caption_archive_object_ref": artifact.caption_archive_object_ref,
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
        caption_source: Any,
        sidecar: dict[str, str],
    ) -> V2VerifiedDriveArchiveArtifact:
        try:
            return _resolve_or_create_v2_drive_archive(
                session=context.session,
                context=context,
                operation=operation,
                external_effect_performed=True,
                caption_sidecar_authority=sidecar,
            )
        except WorkflowStageError as exc:
            if exc.error_code not in {
                "V2_DRIVE_ARCHIVE_ARTIFACT_REQUIRED",
                "V2_DRIVE_ARCHIVE_CAPTION_ARTIFACT_REQUIRED",
            }:
                raise

        details = dict(operation.parameters["provider_execution"])
        effect_dir = self._effect_dir(context.command_id)
        request_path = effect_dir / "google-drive-archive-request-journal.json"
        identity = _remote_archive_request_identity(
            command_id=context.command_id,
            operation_id=operation.operation_id,
            idempotency_key=details["idempotency_key"],
            source_relative_path=self._relative(source),
            source_checksum=checksum,
            source_size_bytes=source.stat().st_size,
            measured_render_duration_ms=measured_duration_ms,
            caption_relative_path=self._relative(caption_source),
            sidecar=sidecar,
        )
        if request_path.exists():
            if not request_path.is_file() or request_path.is_symlink():
                raise ValidationFailureError("V2_GOOGLE_DRIVE_REQUEST_JOURNAL_MISMATCH")
            prior = _load_json(request_path)
            if prior != {**identity, "state": "SUBMITTED"}:
                raise ValidationFailureError("V2_GOOGLE_DRIVE_REQUEST_JOURNAL_MISMATCH")
            self._reconcile_submitted_remote_archive_get_only(
                context=context,
                operation=operation,
                source=source,
                checksum=checksum,
                measured_duration_ms=measured_duration_ms,
                caption_source=caption_source,
                sidecar=sidecar,
                request_identity=identity,
            )
            artifact = _resolve_or_create_v2_drive_archive(
                session=context.session,
                context=context,
                operation=operation,
                external_effect_performed=True,
                caption_sidecar_authority=sidecar,
            )
            context.session.commit()
            return artifact
        attempt_count = context.event.attempt_count
        if (
            isinstance(attempt_count, bool)
            or not isinstance(attempt_count, int)
            or attempt_count != 1
        ):
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                error_code="V2_GOOGLE_DRIVE_RESUBMISSION_FORBIDDEN",
                summary=(
                    "An ARCHIVE replay has no exact prior request journal; no "
                    "Google Drive upload or replacement journal was attempted."
                ),
                incident_type="PROVIDER_OUTCOME_UNCERTAIN",
                retry_eligible=False,
            )
        _write_json_atomic(request_path, {**identity, "state": "SUBMITTED"})
        try:
            upload_service = self._upload_service_factory(context.session)
            cloud, verification = upload_service.upload_verified(
                local_path=source,
                media_type="LONG_FORM_FINAL",
                company_id=context.run.company_id,
                channel_workspace_id=context.run.channel_workspace_id,
                video_project_id=context.run.video_project_id,
                uploaded_video_id=None,
                render_package_id=None,
                source_refs=[_v2_render_output_source_ref(context.run)],
                retention_policy={
                    "keep_local": True,
                    "cleanup_authorized": False,
                    "source": "v2-real-archive",
                },
                idempotency_key=details["idempotency_key"],
            )
            caption_cloud, caption_verification = upload_service.upload_verified(
                local_path=caption_source,
                media_type="CAPTION",
                company_id=context.run.company_id,
                channel_workspace_id=context.run.channel_workspace_id,
                video_project_id=context.run.video_project_id,
                uploaded_video_id=None,
                render_package_id=None,
                source_refs=[_caption_sidecar_source_ref(context.run, sidecar)],
                retention_policy={
                    "keep_local": True,
                    "cleanup_authorized": False,
                    "source": "v2-real-archive-sidecar",
                    "sidecar_only": True,
                },
                idempotency_key=details["idempotency_key"] + ".caption",
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
            or caption_verification.verification_status != "CHECKSUM_VERIFIED"
            or caption_verification.checksum_verified is not True
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
        caption_cloud.technical_appendix = {
            **(caption_cloud.technical_appendix or {}),
            "v2_caption_sidecar": True,
            "caption_ref": sidecar["caption_ref"],
            "caption_artifact_hash": sidecar["caption_artifact_hash"],
            "subtitle_qc_ref": sidecar["subtitle_qc_ref"],
            "subtitle_qc_hash": sidecar["subtitle_qc_hash"],
            "v2_archive_command_id": context.command_id,
            "v2_archive_idempotency_key": details["idempotency_key"] + ".caption",
        }
        context.session.flush()
        artifact = _resolve_or_create_v2_drive_archive(
            session=context.session,
            context=context,
            operation=operation,
            external_effect_performed=True,
            caption_sidecar_authority=sidecar,
        )
        context.session.commit()
        return artifact

    def _reconcile_submitted_remote_archive_get_only(
        self,
        *,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
        source: Path,
        checksum: str,
        measured_duration_ms: int,
        caption_source: Path,
        sidecar: dict[str, str],
        request_identity: Mapping[str, Any],
    ) -> None:
        """Adopt an accepted prior MP4+SRT pair without any Drive write.

        The pre-submit request journal proves the first invocation crossed the
        submission boundary, so this path may only use exact remote reads.  It
        resolves and verifies both objects before materializing either DB row;
        an absent, partial, ambiguous, or mismatched pair remains uncertain.
        """

        details = dict(operation.parameters["provider_execution"])
        try:
            upload_service = self._upload_service_factory(context.session)
            proofs = _probe_submitted_drive_archive_get_only(
                upload_service=upload_service,
                company_id=context.run.company_id,
                channel_workspace_id=context.run.channel_workspace_id,
                video_project_id=context.run.video_project_id,
                source=source,
                source_checksum=checksum,
                source_idempotency_key=details["idempotency_key"],
                caption_source=caption_source,
                caption_checksum=sidecar["caption_checksum"],
                caption_idempotency_key=details["idempotency_key"] + ".caption",
            )
            media_source_ref = _v2_render_output_source_ref(context.run)
            caption_source_ref = _caption_sidecar_source_ref(context.run, sidecar)
            cloud = _materialize_reconciled_drive_cloud_ref(
                session=context.session,
                run=context.run,
                local_path=source,
                proof=proofs["media"],
                source_ref=media_source_ref,
                retention_policy={
                    "keep_local": True,
                    "cleanup_authorized": False,
                    "source": "v2-real-archive",
                },
            )
            caption_cloud = _materialize_reconciled_drive_cloud_ref(
                session=context.session,
                run=context.run,
                local_path=caption_source,
                proof=proofs["caption"],
                source_ref=caption_source_ref,
                retention_policy={
                    "keep_local": True,
                    "cleanup_authorized": False,
                    "source": "v2-real-archive-sidecar",
                    "sidecar_only": True,
                },
            )
            cloud.technical_appendix = {
                **(cloud.technical_appendix or {}),
                "measured_render_duration_ms": measured_duration_ms,
                "v2_archive_command_id": context.command_id,
                "v2_archive_idempotency_key": details["idempotency_key"],
                "v2_remote_archive": True,
                "v2_remote_archive_reconciliation_mode": "GET_ONLY",
            }
            caption_cloud.technical_appendix = {
                **(caption_cloud.technical_appendix or {}),
                "v2_caption_sidecar": True,
                "caption_ref": sidecar["caption_ref"],
                "caption_artifact_hash": sidecar["caption_artifact_hash"],
                "subtitle_qc_ref": sidecar["subtitle_qc_ref"],
                "subtitle_qc_hash": sidecar["subtitle_qc_hash"],
                "v2_archive_command_id": context.command_id,
                "v2_archive_idempotency_key": details["idempotency_key"] + ".caption",
                "v2_remote_archive_reconciliation_mode": "GET_ONLY",
            }
            context.session.flush()
            receipt = _get_only_reconciliation_receipt(
                command_id=context.command_id,
                operation_id=operation.operation_id,
                request_identity=request_identity,
                media=proofs["media"],
                caption=proofs["caption"],
            )
            receipt_path = (
                self._effect_dir(context.command_id)
                / "google-drive-archive-get-only-reconciliation.json"
            )
            _write_or_require_exact_json(receipt_path, receipt)
        except Exception as exc:
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                error_code="V2_GOOGLE_DRIVE_OUTCOME_UNCERTAIN",
                summary=(
                    "The prior Google Drive request could not be reconciled as "
                    "one exact checksum-verified MP4 and SRT pair using reads "
                    "only; no provider upload was attempted."
                ),
                incident_type="PROVIDER_OUTCOME_UNCERTAIN",
                retry_eligible=False,
            ) from exc


def _probe_submitted_drive_archive_get_only(
    *,
    upload_service: GoogleDriveUploadService,
    company_id: uuid.UUID,
    channel_workspace_id: uuid.UUID,
    video_project_id: uuid.UUID,
    source: Path,
    source_checksum: str,
    source_idempotency_key: str,
    caption_source: Path,
    caption_checksum: str,
    caption_idempotency_key: str,
) -> dict[str, _V2DriveGETOnlyFileProof]:
    """Resolve an exact previously submitted pair using Drive reads only."""

    if (
        not upload_service.config_service.offload_enabled()
        or not source.is_file()
        or source.is_symlink()
        or not caption_source.is_file()
        or caption_source.is_symlink()
        or _sha256_file(source) != source_checksum
        or _sha256_file(caption_source) != caption_checksum
    ):
        raise ValidationFailureError("V2_GOOGLE_DRIVE_RECONCILIATION_INPUT_INVALID")
    root_folder_id = str(upload_service.config_service.root_folder_id() or "").strip()
    reference = upload_service.credential_service.get_connected_reference(
        company_id=company_id,
        channel_workspace_id=channel_workspace_id,
    )
    access_token = (
        upload_service.credential_service.get_valid_access_token(reference)
        if reference is not None
        else None
    )
    if not root_folder_id or not access_token:
        raise ValidationFailureError(
            "V2_GOOGLE_DRIVE_RECONCILIATION_CREDENTIAL_REQUIRED"
        )

    media = _probe_submitted_drive_file_get_only(
        upload_service=upload_service,
        access_token=access_token,
        root_folder_id=root_folder_id,
        company_id=company_id,
        channel_workspace_id=channel_workspace_id,
        video_project_id=video_project_id,
        local_path=source,
        media_type="LONG_FORM_FINAL",
        expected_checksum=source_checksum,
        idempotency_key=source_idempotency_key,
    )
    caption = _probe_submitted_drive_file_get_only(
        upload_service=upload_service,
        access_token=access_token,
        root_folder_id=root_folder_id,
        company_id=company_id,
        channel_workspace_id=channel_workspace_id,
        video_project_id=video_project_id,
        local_path=caption_source,
        media_type="CAPTION",
        expected_checksum=caption_checksum,
        idempotency_key=caption_idempotency_key,
    )
    if (
        media.upload_result.drive_file_id == caption.upload_result.drive_file_id
        or media.upload_result.drive_folder_id == caption.upload_result.drive_folder_id
    ):
        raise ValidationFailureError(
            "V2_GOOGLE_DRIVE_RECONCILIATION_REMOTE_IDENTITY_COLLISION"
        )
    return {"media": media, "caption": caption}


def _probe_submitted_drive_file_get_only(
    *,
    upload_service: GoogleDriveUploadService,
    access_token: str,
    root_folder_id: str,
    company_id: uuid.UUID,
    channel_workspace_id: uuid.UUID,
    video_project_id: uuid.UUID,
    local_path: Path,
    media_type: str,
    expected_checksum: str,
    idempotency_key: str,
) -> _V2DriveGETOnlyFileProof:
    archive_path = upload_service.archive_path_builder.build(
        company_id=company_id,
        channel_workspace_id=channel_workspace_id,
        video_project_id=video_project_id,
        uploaded_video_id=None,
        media_type=media_type,
    )
    folder_path = tuple(archive_path.folder_path)
    folder_id = upload_service.provider.find_folder_path(
        access_token=access_token,
        root_folder_id=root_folder_id,
        folder_path=list(folder_path),
    )
    if not folder_id:
        raise ValidationFailureError(
            "V2_GOOGLE_DRIVE_RECONCILIATION_REMOTE_FOLDER_ABSENT"
        )
    found = upload_service.provider.find_file_by_idempotency_key(
        access_token=access_token,
        folder_id=folder_id,
        idempotency_key=idempotency_key,
    )
    if found is None:
        raise ValidationFailureError(
            "V2_GOOGLE_DRIVE_RECONCILIATION_REMOTE_FILE_ABSENT"
        )
    metadata = upload_service.provider.get_file_metadata(
        access_token=access_token,
        drive_file_id=found.drive_file_id,
    )
    expected_mime_type = (
        mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    )
    expected_size_bytes = local_path.stat().st_size
    common_identity = (
        found.drive_file_id
        and metadata.drive_file_id == found.drive_file_id
        and found.drive_folder_id == folder_id
        and metadata.drive_folder_id == folder_id
        and found.web_view_link
        and metadata.web_view_link == found.web_view_link
        and found.file_name == local_path.name
        and metadata.file_name == local_path.name
        and found.mime_type == expected_mime_type
        and metadata.mime_type == expected_mime_type
        and found.size_bytes == expected_size_bytes
        and metadata.size_bytes == expected_size_bytes
    )
    if not common_identity:
        raise ValidationFailureError(
            "V2_GOOGLE_DRIVE_RECONCILIATION_REMOTE_IDENTITY_MISMATCH"
        )
    found_checksum = found.checksum_sha256
    metadata_checksum = metadata.checksum_sha256
    if found_checksum and metadata_checksum and found_checksum != metadata_checksum:
        raise ValidationFailureError(
            "V2_GOOGLE_DRIVE_RECONCILIATION_REMOTE_CHECKSUM_CONFLICT"
        )
    checksum_readback_performed = False
    remote_checksum = metadata_checksum or found_checksum
    if not remote_checksum:
        remote_checksum = upload_service.provider.readback_sha256(
            access_token=access_token,
            drive_file_id=found.drive_file_id,
        )
        checksum_readback_performed = True
    if remote_checksum != expected_checksum:
        raise ValidationFailureError(
            "V2_GOOGLE_DRIVE_RECONCILIATION_REMOTE_CHECKSUM_MISMATCH"
        )
    upload_result = GoogleDriveUploadResult(
        drive_file_id=metadata.drive_file_id,
        drive_folder_id=metadata.drive_folder_id,
        web_view_link=metadata.web_view_link,
        file_name=metadata.file_name,
        mime_type=metadata.mime_type,
        size_bytes=metadata.size_bytes,
        checksum_sha256=remote_checksum,
        upload_mode="reconciled",
        technical_appendix={
            "folder_path": list(folder_path),
            "folder_path_mode": archive_path.mode,
            "upload_mode": "reconciled",
            "root_folder_configured": True,
            "idempotency_key": idempotency_key,
            "checksum_readback_performed": checksum_readback_performed,
            "remote_request_reconciliation_mode": "GET_ONLY",
            "provider_write_count": 0,
        },
    )
    verification = upload_service.verifier.verify(
        upload_result=upload_result,
        local_size_bytes=expected_size_bytes,
        local_sha256=expected_checksum,
    )
    if (
        not verification.ok
        or verification.verification_status != "CHECKSUM_VERIFIED"
        or verification.size_verified is not True
        or verification.checksum_verified is not True
        or verification.checksum_unavailable is not False
    ):
        raise ValidationFailureError(
            "V2_GOOGLE_DRIVE_RECONCILIATION_CHECKSUM_READBACK_REQUIRED"
        )
    return _V2DriveGETOnlyFileProof(
        media_type=media_type,
        idempotency_key=idempotency_key,
        folder_path=folder_path,
        upload_result=upload_result,
        verification=verification,
        checksum_readback_performed=checksum_readback_performed,
    )


def _materialize_reconciled_drive_cloud_ref(
    *,
    session: Session,
    run: Any,
    local_path: Path,
    proof: _V2DriveGETOnlyFileProof,
    source_ref: dict[str, str],
    retention_policy: dict[str, Any],
) -> CloudMediaRef:
    """Create or reuse only the DB row for the exact GET-only remote proof."""

    result = proof.upload_result
    rows = list(
        session.scalars(
            select(CloudMediaRef).where(
                CloudMediaRef.storage_provider == "GOOGLE_DRIVE",
                CloudMediaRef.drive_file_id == result.drive_file_id,
            )
        )
    )
    if len(rows) > 1:
        raise ValidationFailureError(
            "V2_GOOGLE_DRIVE_RECONCILIATION_CLOUD_REF_AMBIGUOUS"
        )
    path_hash = hashlib.sha256(str(local_path.resolve()).encode("utf-8")).hexdigest()
    if rows:
        cloud = rows[0]
        appendix = cloud.technical_appendix or {}
        if (
            cloud.company_id != run.company_id
            or cloud.channel_workspace_id != run.channel_workspace_id
            or cloud.video_project_id != run.video_project_id
            or cloud.uploaded_video_id is not None
            or cloud.render_package_id is not None
            or cloud.media_type != proof.media_type
            or cloud.storage_provider != "GOOGLE_DRIVE"
            or cloud.drive_folder_id != result.drive_folder_id
            or cloud.web_view_link != result.web_view_link
            or cloud.mime_type != result.mime_type
            or cloud.file_name != result.file_name
            or cloud.size_bytes != result.size_bytes
            or cloud.checksum_sha256 != result.checksum_sha256
            or cloud.local_source_path_hash != path_hash
            or cloud.upload_status != "VERIFIED"
            or cloud.verification_status != "CHECKSUM_VERIFIED"
            or cloud.source_refs != [source_ref]
            or cloud.retention_policy != retention_policy
            or appendix.get("idempotency_key") != proof.idempotency_key
            or appendix.get("drive_file_id_verified") is not True
            or appendix.get("size_verified") is not True
            or appendix.get("checksum_verified") is not True
        ):
            raise ValidationFailureError(
                "V2_GOOGLE_DRIVE_RECONCILIATION_CLOUD_REF_MISMATCH"
            )
        return cloud
    return CloudMediaRefService(session).create_verified_ref(
        company_id=run.company_id,
        channel_workspace_id=run.channel_workspace_id,
        video_project_id=run.video_project_id,
        uploaded_video_id=None,
        render_package_id=None,
        media_type=proof.media_type,
        upload_result=result,
        verification=proof.verification,
        local_source_path_hash=path_hash,
        checksum_sha256=result.checksum_sha256,
        source_refs=[source_ref],
        retention_policy=retention_policy,
    )


def _get_only_reconciliation_receipt(
    *,
    command_id: str,
    operation_id: str,
    request_identity: Mapping[str, Any],
    media: _V2DriveGETOnlyFileProof,
    caption: _V2DriveGETOnlyFileProof,
) -> dict[str, Any]:
    def item(proof: _V2DriveGETOnlyFileProof) -> dict[str, Any]:
        result = proof.upload_result
        return {
            "media_type": proof.media_type,
            "idempotency_key": proof.idempotency_key,
            "folder_path": list(proof.folder_path),
            "drive_folder_id": result.drive_folder_id,
            "drive_file_id": result.drive_file_id,
            "web_view_link": result.web_view_link,
            "file_name": result.file_name,
            "mime_type": result.mime_type,
            "size_bytes": result.size_bytes,
            "checksum_sha256": result.checksum_sha256,
            "verification_status": proof.verification.verification_status,
            "checksum_readback_performed": proof.checksum_readback_performed,
        }

    payload = {
        "schema_version": V2_DRIVE_ARCHIVE_RECONCILIATION_SCHEMA,
        "command_id": command_id,
        "operation_id": operation_id,
        "request_identity_hash": content_hash(dict(request_identity)),
        "provider": "google_drive",
        "reconciliation_mode": "GET_ONLY",
        "provider_write_count": 0,
        "media": item(media),
        "caption": item(caption),
    }
    return {**payload, "reconciliation_receipt_hash": content_hash(payload)}


def _write_or_require_exact_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        if path.is_symlink() or _load_json(path) != payload:
            raise ValidationFailureError(
                "V2_GOOGLE_DRIVE_RECONCILIATION_RECEIPT_MISMATCH"
            )
        return
    _write_json_atomic(path, payload)


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
    caption_cloud_id = _uuid_or_none(content.get("caption_cloud_media_ref_id"))
    caption_cloud = (
        session.get(CloudMediaRef, caption_cloud_id)
        if caption_cloud_id is not None
        else None
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
        or caption_cloud is None
        or caption_cloud.company_id != media.company_id
        or caption_cloud.channel_workspace_id != media.channel_workspace_id
        or caption_cloud.video_project_id != project_id
        or caption_cloud.storage_provider != "GOOGLE_DRIVE"
        or caption_cloud.media_type != "CAPTION"
        or caption_cloud.checksum_sha256 != content.get("caption_checksum")
        or caption_cloud.upload_status != "VERIFIED"
        or caption_cloud.verification_status != "CHECKSUM_VERIFIED"
        or not _drive_sidecar_identity_valid(caption_cloud)
        or not _cloud_appendix_verifies_checksum(caption_cloud)
        or not _has_caption_sidecar_source(caption_cloud, content)
        or not _caption_appendix_matches_lineage(caption_cloud, content)
        or content.get("caption_archive_object_ref")
        != _drive_caption_object_ref(caption_cloud.drive_file_id)
        or not isinstance(content.get("caption_ref"), str)
        or not isinstance(content.get("subtitle_qc_ref"), str)
        or not isinstance(content.get("caption_artifact_hash"), str)
        or not isinstance(content.get("subtitle_qc_hash"), str)
    ):
        raise ValidationFailureError("V2_DRIVE_ARCHIVE_FINAL_MEDIA_AUTHORITY_MISMATCH")
    return V2VerifiedDriveArchiveArtifact(
        final_media=media,
        cloud_media=cloud,
        caption_cloud_media=caption_cloud,
        lineage=lineage,
        archive_receipt_hash=expected_archive_hash,
        archive_object_ref=archive_object_ref,
        caption_archive_object_ref=_drive_caption_object_ref(
            caption_cloud.drive_file_id
        ),
    )


def has_exact_drive_archive_get_only_reconciliation_authority(
    session: Session,
    *,
    run: Any,
    source_workflow_run_id: uuid.UUID,
    ledger: V2ProductionEffectLedger,
    input_hash: str,
    operation: Mapping[str, Any],
    workspace_root: Path | None = None,
) -> bool:
    """Prove an interrupted ARCHIVE can only resolve or reconcile read-only.

    This gate never creates authority or constructs a Drive client.  It admits
    an ``EFFECT_STARTED`` or ``FAILED_UNCERTAIN`` ledger only when its exact
    immutable request journal and both local source hashes remain intact.  The
    adapter must therefore either resolve already-durable DB authority or take
    its request-present branch, which permits Drive GETs and DB materialization
    but contains no upload call.  Remote absence, partial acceptance,
    ambiguity, or checksum drift then fails closed inside that branch.
    """

    if not _preverified_archive_ledger_identity(
        run=run,
        ledger=ledger,
        input_hash=input_hash,
        operation=operation,
    ):
        return False
    parameters = operation.get("parameters")
    details = (
        parameters.get("provider_execution")
        if isinstance(parameters, Mapping)
        else None
    )
    operation_id = operation.get("operation_id")
    idempotency_key = (
        details.get("idempotency_key") if isinstance(details, Mapping) else None
    )
    if (
        operation.get("stage") != "ARCHIVE"
        or operation.get("adapter_key") != V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY
        or operation.get("paid_provider_call") is not False
        or not isinstance(parameters, Mapping)
        or parameters.get("mode") != "GOOGLE_DRIVE_REMOTE_ARCHIVE"
        or not isinstance(details, Mapping)
        or details.get("provider") != "google_drive"
        or details.get("attempt_limit") != 1
        or details.get("remote_object_required") is not True
        or details.get("checksum_readback_required") is not True
        or not isinstance(operation_id, str)
        or not operation_id
        or not isinstance(idempotency_key, str)
        or not idempotency_key
    ):
        return False
    try:
        if Decimal(str(operation.get("max_cost_usd"))) != Decimal("0"):
            return False
        render_ledger = session.scalar(
            select(V2ProductionEffectLedger).where(
                V2ProductionEffectLedger.workflow_run_id == run.id,
                V2ProductionEffectLedger.stage == "RENDER",
                V2ProductionEffectLedger.state == "VERIFIED",
            )
        )
        media_ledger = session.scalar(
            select(V2ProductionEffectLedger).where(
                V2ProductionEffectLedger.workflow_run_id == source_workflow_run_id,
                V2ProductionEffectLedger.stage == "MEDIA",
                V2ProductionEffectLedger.state == "VERIFIED",
            )
        )
        if render_ledger is None or media_ledger is None:
            return False
        render_journal = dict(render_ledger.effect_journal or {})
        sidecar = _sidecar_archive_authority(dict(media_ledger.effect_journal or {}))
        root = _read_only_archive_workspace_root(workspace_root)
        source_relative_path = _required_text(render_journal, "output_relative_path")
        caption_relative_path = sidecar["caption_relative_path"]
        source = _read_only_relative_file(root, source_relative_path)
        caption = _read_only_relative_file(root, caption_relative_path)
        measured_duration_ms = int(
            render_journal.get("measured_render_duration_ms") or 0
        )
        if (
            measured_duration_ms <= 0
            or _sha256_file(source) != run.render_output_checksum
            or _sha256_file(caption) != sidecar["caption_checksum"]
        ):
            return False
        request_identity = _remote_archive_request_identity(
            command_id=ledger.command_id,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            source_relative_path=source_relative_path,
            source_checksum=run.render_output_checksum,
            source_size_bytes=source.stat().st_size,
            measured_render_duration_ms=measured_duration_ms,
            caption_relative_path=caption_relative_path,
            sidecar=sidecar,
        )
        request_path = (
            root
            / "effects"
            / hashlib.sha256(ledger.command_id.encode("utf-8")).hexdigest()
            / "google-drive-archive-request-journal.json"
        )
        if (
            not request_path.is_file()
            or request_path.is_symlink()
            or _load_json(request_path) != {**request_identity, "state": "SUBMITTED"}
        ):
            return False
        return True
    except (OSError, TypeError, ValueError, ValidationFailureError):
        return False


def has_exact_governed_drive_archive_reconciliation_authority(
    session: Session,
    *,
    run: Any,
    source_workflow_run_id: uuid.UUID,
    ledger: V2ProductionEffectLedger,
    input_hash: str,
    operation: Mapping[str, Any],
    workspace_root: Path | None = None,
) -> bool:
    """Compatibility name for the governed recovery reader."""

    return has_exact_drive_archive_get_only_reconciliation_authority(
        session,
        run=run,
        source_workflow_run_id=source_workflow_run_id,
        ledger=ledger,
        input_hash=input_hash,
        operation=operation,
        workspace_root=workspace_root,
    )


def _preverified_archive_ledger_identity(
    *,
    run: Any,
    ledger: V2ProductionEffectLedger,
    input_hash: str,
    operation: Mapping[str, Any],
) -> bool:
    if (
        ledger.workflow_run_id != run.id
        or ledger.video_project_id != run.video_project_id
        or ledger.production_package_artifact_version_id
        != run.production_package_artifact_version_id
        or ledger.production_package_hash != run.production_package_hash
        or ledger.stage != "ARCHIVE"
        or ledger.operation_id != operation.get("operation_id")
        or ledger.adapter_key != operation.get("adapter_key")
        or ledger.input_hash != input_hash
        or ledger.effect_invocation_count != 1
        or ledger.state not in {"EFFECT_STARTED", "FAILED_UNCERTAIN"}
        or ledger.started_at is None
        or ledger.completed_at is not None
        or ledger.result_type is not None
        or ledger.result_id is not None
        or ledger.result_ref is not None
        or ledger.result_hash is not None
        or bool(ledger.result_payload)
        or bool(ledger.authority_refs)
    ):
        return False
    base = {
        "schema_version": "vcos.production-effect-journal.v1",
        "command_id": ledger.command_id,
        "stage": "ARCHIVE",
        "state": ledger.state,
    }
    journal = dict(ledger.effect_journal or {})
    if ledger.state == "EFFECT_STARTED":
        return journal == base
    last_error_type = journal.pop("last_error_type", None)
    return (
        journal == base and isinstance(last_error_type, str) and bool(last_error_type)
    )


def _read_only_archive_workspace_root(workspace_root: Path | None) -> Path:
    configured = os.getenv("VCOS_V2_PRODUCTION_ROOT")
    root = (
        workspace_root
        or (
            Path(configured)
            if configured
            else Path(__file__).resolve().parents[2] / "var" / "v2-production"
        )
    ).resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValidationFailureError("V2_EFFECT_WORKSPACE_AUTHORITY_MISSING")
    return root


def _read_only_relative_file(root: Path, value: str) -> Path:
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts or not raw.parts:
        raise ValidationFailureError("V2_EFFECT_RELATIVE_PATH_INVALID")
    cursor = root
    for part in raw.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValidationFailureError("V2_EFFECT_FILE_INVALID")
    resolved = (root / raw).resolve()
    if root not in resolved.parents or not resolved.is_file() or resolved.is_symlink():
        raise ValidationFailureError("V2_EFFECT_FILE_INVALID")
    return resolved


def v2_drive_caption_sidecar_review_metadata(
    artifact: V2VerifiedDriveArchiveArtifact,
) -> dict[str, Any]:
    """Project the exact verified SRT authority into immutable final review.

    Callers first resolve ``artifact`` with
    :func:`require_v2_google_drive_final_media`.  These checks deliberately
    repeat the caption-specific bindings so a fabricated dataclass cannot turn
    an unrelated URL or checksum into an operator-facing Drive action.
    """

    cloud = artifact.caption_cloud_media
    lineage = artifact.lineage
    lineage_content = lineage.content if isinstance(lineage.content, dict) else {}
    caption_ref = lineage_content.get("caption_ref")
    caption_checksum = lineage_content.get("caption_checksum")
    caption_artifact_hash = lineage_content.get("caption_artifact_hash")
    subtitle_qc_ref = lineage_content.get("subtitle_qc_ref")
    subtitle_qc_hash = lineage_content.get("subtitle_qc_hash")
    hashes = (caption_checksum, caption_artifact_hash, subtitle_qc_hash)
    refs = (caption_ref, subtitle_qc_ref)
    expected_object_ref = _drive_caption_object_ref(cloud.drive_file_id)
    if (
        lineage_content.get("schema_version") != V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA
        or lineage.content_hash != content_hash(lineage_content)
        or lineage_content.get("caption_cloud_media_ref_id") != str(cloud.id)
        or lineage_content.get("caption_archive_object_ref") != expected_object_ref
        or artifact.caption_archive_object_ref != expected_object_ref
        or cloud.storage_provider != "GOOGLE_DRIVE"
        or cloud.media_type != "CAPTION"
        or cloud.upload_status != "VERIFIED"
        or cloud.verification_status != "CHECKSUM_VERIFIED"
        or cloud.checksum_sha256 != caption_checksum
        or not _drive_sidecar_identity_valid(cloud)
        or not _cloud_appendix_verifies_checksum(cloud)
        or not _has_caption_sidecar_source(cloud, lineage_content)
        or not _caption_appendix_matches_lineage(cloud, lineage_content)
        or any(not isinstance(value, str) or not value for value in refs)
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in hashes
        )
    ):
        raise ValidationFailureError("V2_DRIVE_CAPTION_REVIEW_AUTHORITY_MISMATCH")
    return {
        "schema_version": V2_DRIVE_CAPTION_REVIEW_SCHEMA,
        "delivery_mode": "SIDECAR_ONLY",
        "label": V2_DRIVE_CAPTION_SIDECAR_LABEL,
        "file_name": cloud.file_name,
        "caption_ref": caption_ref,
        "caption_archive_object_ref": expected_object_ref,
        "caption_drive_web_view_url": cloud.web_view_link,
        "caption_checksum_sha256": caption_checksum,
        "caption_artifact_hash": caption_artifact_hash,
        "subtitle_qc_ref": subtitle_qc_ref,
        "subtitle_qc_hash": subtitle_qc_hash,
        "caption_cloud_media_ref_id": str(cloud.id),
        "caption_drive_file_id": cloud.drive_file_id,
        "archive_verification_state": "VERIFIED",
        "storage_provider": "GOOGLE_DRIVE",
    }


def _drive_archive_receipt_content(
    *,
    run: Any,
    project: VideoProject,
    command_id: str,
    operation_id: str,
    cloud: CloudMediaRef,
    caption_cloud: CloudMediaRef,
    sidecar: Mapping[str, str],
    measured_duration_ms: int,
    external_effect_performed: bool,
) -> dict[str, Any]:
    archive_object_ref = _drive_object_ref(cloud.drive_file_id)
    caption_archive_object_ref = _drive_caption_object_ref(caption_cloud.drive_file_id)
    return {
        "schema_version": V2_DRIVE_ARCHIVE_RECEIPT_SCHEMA,
        "workflow_run_id": str(run.id),
        "archive_command_id": command_id,
        "provider_operation_id": operation_id,
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
        "caption_ref": sidecar["caption_ref"],
        "caption_checksum": sidecar["caption_checksum"],
        "caption_artifact_hash": sidecar["caption_artifact_hash"],
        "subtitle_qc_ref": sidecar["subtitle_qc_ref"],
        "subtitle_qc_hash": sidecar["subtitle_qc_hash"],
        "caption_cloud_media_ref_id": str(caption_cloud.id),
        "caption_drive_file_id": caption_cloud.drive_file_id,
        "caption_archive_object_ref": caption_archive_object_ref,
        "verification_status": cloud.verification_status,
        "archive_state": "VERIFIED",
        "invokes_mr1": False,
        "automatic_publish": False,
        "external_effect_performed": external_effect_performed,
    }


def _drive_archive_lineage_content(
    *,
    run: Any,
    project: VideoProject,
    package: Any,
    command_id: str,
    operation_id: str,
    cloud: CloudMediaRef,
    caption_cloud: CloudMediaRef,
    sidecar: Mapping[str, str],
    measured_duration_ms: int,
    archive_receipt_hash: str,
    external_effect_performed: bool,
) -> dict[str, Any]:
    return {
        "schema_version": V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA,
        "workflow_run_id": str(run.id),
        "archive_command_id": command_id,
        "provider_operation_id": operation_id,
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
        "archive_receipt_hash": archive_receipt_hash,
        "archive_state": "VERIFIED",
        "cloud_media_ref_id": str(cloud.id),
        "archive_object_ref": _drive_object_ref(cloud.drive_file_id),
        "storage_provider": "GOOGLE_DRIVE",
        "caption_ref": sidecar["caption_ref"],
        "caption_checksum": sidecar["caption_checksum"],
        "caption_artifact_hash": sidecar["caption_artifact_hash"],
        "subtitle_qc_ref": sidecar["subtitle_qc_ref"],
        "subtitle_qc_hash": sidecar["subtitle_qc_hash"],
        "caption_cloud_media_ref_id": str(caption_cloud.id),
        "caption_archive_object_ref": _drive_caption_object_ref(
            caption_cloud.drive_file_id
        ),
        "invokes_mr1": False,
        "automatic_publish": False,
        "external_effect_performed": external_effect_performed,
    }


def _resolve_or_create_v2_drive_archive(
    *,
    session: Session,
    context: WorkflowStageContext,
    operation: V2AuthorizedAdapterOperation,
    external_effect_performed: bool = False,
    caption_sidecar_authority: dict[str, str] | None = None,
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
    sidecar = (
        _sidecar_archive_authority(dict(caption_sidecar_authority))
        if caption_sidecar_authority is not None
        else _sidecar_archive_authority_for_run(session, run)
    )
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
    caption_candidates = list(
        session.scalars(
            select(CloudMediaRef).where(
                CloudMediaRef.company_id == run.company_id,
                CloudMediaRef.channel_workspace_id == run.channel_workspace_id,
                CloudMediaRef.video_project_id == project.id,
                CloudMediaRef.storage_provider == "GOOGLE_DRIVE",
                CloudMediaRef.media_type == "CAPTION",
                CloudMediaRef.checksum_sha256 == sidecar["caption_checksum"],
                CloudMediaRef.upload_status == "VERIFIED",
                CloudMediaRef.verification_status == "CHECKSUM_VERIFIED",
            )
        )
    )
    caption_candidates = [
        candidate
        for candidate in caption_candidates
        if _drive_sidecar_identity_valid(candidate)
        and _cloud_appendix_verifies_checksum(candidate)
        and _has_exact_caption_sidecar_source(candidate, run, sidecar)
        and _caption_appendix_matches_sidecar(candidate, sidecar)
    ]
    if not caption_candidates:
        raise _external_block(
            "V2_DRIVE_ARCHIVE_CAPTION_ARTIFACT_REQUIRED",
            "A checksum-verified Google Drive SRT sidecar for this exact caption artifact is required before final review.",
        )
    if len(caption_candidates) != 1:
        raise ValidationFailureError("V2_DRIVE_ARCHIVE_CAPTION_ARTIFACT_AMBIGUOUS")
    caption_cloud = caption_candidates[0]
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
    receipt = _drive_archive_receipt_content(
        run=run,
        project=project,
        command_id=context.command_id,
        operation_id=operation.operation_id,
        cloud=cloud,
        caption_cloud=caption_cloud,
        sidecar=sidecar,
        measured_duration_ms=measured_duration_ms,
        external_effect_performed=external_effect_performed,
    )
    receipt_hash = content_hash(receipt)
    lineage_content = _drive_archive_lineage_content(
        run=run,
        project=project,
        package=package,
        command_id=context.command_id,
        operation_id=operation.operation_id,
        cloud=cloud,
        caption_cloud=caption_cloud,
        sidecar=sidecar,
        measured_duration_ms=measured_duration_ms,
        archive_receipt_hash=receipt_hash,
        external_effect_performed=external_effect_performed,
    )
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
                    {
                        "type": "checksum_verified_google_drive_caption_sidecar",
                        "cloud_media_ref_id": content["caption_cloud_media_ref_id"],
                        "caption_ref": content["caption_ref"],
                        "caption_checksum": content["caption_checksum"],
                        "subtitle_qc_ref": content["subtitle_qc_ref"],
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


def _sidecar_archive_authority_for_run(session: Session, run: Any) -> dict[str, str]:
    ledger = session.scalar(
        select(V2ProductionEffectLedger).where(
            V2ProductionEffectLedger.workflow_run_id == run.id,
            V2ProductionEffectLedger.stage == "MEDIA",
            V2ProductionEffectLedger.state == "VERIFIED",
        )
    )
    if ledger is None:
        raise ValidationFailureError("V2_DRIVE_ARCHIVE_CAPTION_AUTHORITY_REQUIRED")
    return _sidecar_archive_authority(dict(ledger.effect_journal or {}))


def _sidecar_archive_authority(journal: dict[str, Any]) -> dict[str, str]:
    required = (
        "caption_relative_path",
        "caption_checksum",
        "caption_ref",
        "caption_artifact_hash",
        "subtitle_qc_ref",
        "subtitle_qc_hash",
    )
    values = {key: journal.get(key) for key in required}
    if journal.get("subtitle_qc_state") != "PASS" or any(
        not isinstance(value, str) or not value for value in values.values()
    ):
        raise ValidationFailureError("SUBTITLE_QC_FAILED")
    return {
        **{key: str(value) for key, value in values.items()},
        "subtitle_qc_state": "PASS",
    }


def _v2_render_output_source_ref(run: Any) -> dict[str, str]:
    return {
        "type": "v2_render_output",
        "workflow_run_id": str(run.id),
        "render_output_ref": str(run.render_output_ref),
        "render_output_checksum": str(run.render_output_checksum),
        "production_package_artifact_version_id": str(
            run.production_package_artifact_version_id
        ),
        "production_package_hash": str(run.production_package_hash),
    }


def _caption_sidecar_source_ref(run: Any, sidecar: dict[str, str]) -> dict[str, str]:
    return {
        "type": "v2_caption_sidecar",
        "workflow_run_id": str(run.id),
        "caption_ref": sidecar["caption_ref"],
        "caption_checksum": sidecar["caption_checksum"],
        "caption_artifact_hash": sidecar["caption_artifact_hash"],
        "subtitle_qc_ref": sidecar["subtitle_qc_ref"],
        "subtitle_qc_hash": sidecar["subtitle_qc_hash"],
    }


def _has_exact_caption_sidecar_source(
    cloud: CloudMediaRef,
    run: Any,
    sidecar: dict[str, str],
) -> bool:
    required = _caption_sidecar_source_ref(run, sidecar)
    return any(
        isinstance(item, dict)
        and all(item.get(key) == value for key, value in required.items())
        for item in (cloud.source_refs or [])
    )


def _has_caption_sidecar_source(cloud: CloudMediaRef, lineage: dict[str, Any]) -> bool:
    required = {
        "type": "v2_caption_sidecar",
        "workflow_run_id": lineage.get("workflow_run_id"),
        "caption_ref": lineage.get("caption_ref"),
        "caption_checksum": lineage.get("caption_checksum"),
        "caption_artifact_hash": lineage.get("caption_artifact_hash"),
        "subtitle_qc_ref": lineage.get("subtitle_qc_ref"),
        "subtitle_qc_hash": lineage.get("subtitle_qc_hash"),
    }
    return all(isinstance(value, str) and value for value in required.values()) and any(
        isinstance(item, dict)
        and all(item.get(key) == value for key, value in required.items())
        for item in (cloud.source_refs or [])
    )


def _caption_appendix_matches_sidecar(
    cloud: CloudMediaRef, sidecar: dict[str, str]
) -> bool:
    appendix = cloud.technical_appendix
    return bool(
        isinstance(appendix, dict)
        and appendix.get("v2_caption_sidecar") is True
        and appendix.get("caption_ref") == sidecar["caption_ref"]
        and appendix.get("caption_artifact_hash") == sidecar["caption_artifact_hash"]
        and appendix.get("subtitle_qc_ref") == sidecar["subtitle_qc_ref"]
        and appendix.get("subtitle_qc_hash") == sidecar["subtitle_qc_hash"]
    )


def _caption_appendix_matches_lineage(
    cloud: CloudMediaRef, lineage: dict[str, Any]
) -> bool:
    appendix = cloud.technical_appendix
    return bool(
        isinstance(appendix, dict)
        and appendix.get("v2_caption_sidecar") is True
        and appendix.get("caption_ref") == lineage.get("caption_ref")
        and appendix.get("caption_artifact_hash")
        == lineage.get("caption_artifact_hash")
        and appendix.get("subtitle_qc_ref") == lineage.get("subtitle_qc_ref")
        and appendix.get("subtitle_qc_hash") == lineage.get("subtitle_qc_hash")
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
    return _drive_web_identity_valid(cloud)


def _drive_sidecar_identity_valid(cloud: CloudMediaRef) -> bool:
    if (
        not cloud.drive_file_id
        or not cloud.web_view_link
        or cloud.size_bytes is None
        or cloud.size_bytes <= 0
        or not cloud.checksum_sha256
        or cloud.mime_type not in {"application/x-subrip", "text/plain"}
        or not str(cloud.file_name or "").endswith(".srt")
    ):
        return False
    return _drive_web_identity_valid(cloud)


def _drive_web_identity_valid(cloud: CloudMediaRef) -> bool:
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


def _drive_caption_object_ref(drive_file_id: str) -> str:
    if not drive_file_id or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in drive_file_id
    ):
        raise ValidationFailureError("V2_DRIVE_ARCHIVE_CAPTION_FILE_ID_INVALID")
    return f"drive://{drive_file_id}/canonical-captions.srt"


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


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
    "V2_DRIVE_CAPTION_REVIEW_SCHEMA",
    "V2_DRIVE_CAPTION_SIDECAR_LABEL",
    "V2_GOOGLE_DRIVE_ARCHIVE_ADAPTER_KEY",
    "V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY",
    "require_v2_google_drive_final_media",
    "v2_drive_caption_sidecar_review_metadata",
]
