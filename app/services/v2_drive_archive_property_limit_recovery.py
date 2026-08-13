"""Bounded append-only recovery for the V2 Drive app-property limit defect.

The recovery is deliberately separate from normal ARCHIVE replay.  Its public
surface is stable while the execution helpers remain private so callers cannot
turn it into a general Drive retry primitive.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.actor import ActorContext, ActorType
from app.core.db import get_session_factory
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.contracts.production_workflow import (
    ProductionWorkflowStage,
    WorkflowAuthorityRefs,
    WorkflowEffectState,
    WorkflowStageResult,
)
from app.db.models.foundation import DomainEvent
from app.db.models.m10_5 import CloudMediaRef, GoogleDriveMediaCredential
from app.db.models.mr1_budget import MR1MonthlyBudgetReservation
from app.db.models.ops import DeadLetterJob
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.db.models.script_qualification import (
    ControlledVerifierSettlementAuthority,
    ScriptContractReplacementAuthority,
    ScriptQualificationRun,
)
from app.db.models.v2_effect import (
    V2DriveArchivePropertyLimitRecoveryAuthority,
    V2DriveArchivePropertyLimitRecoveryReceipt,
    V2ProductionEffectLedger,
)
from app.db.models.workflow import ArtifactVersion
from app.services.config_registry import content_hash
from app.services.m10_5 import (
    DriveArchivePathBuilder,
    GoogleDriveConfigService,
    GoogleDriveMediaStorageProvider,
    GoogleDriveOAuthCredentialService,
    GoogleDriveUploadService,
    GoogleDriveVerificationResult,
    _drive_idempotency_property_value,
)
from app.services.production_workflow import (
    ProductionWorkflowCoordinator,
    WORKFLOW_AGGREGATE_TYPE,
    WORKFLOW_EVENT_TYPE,
    WORKFLOW_EVENT_VERSION,
    WorkflowStageContext,
    command_id_for,
    handler_key_for,
    semantic_hash,
)
from app.services.script_contract_replacement import (
    controlled_verifier_settlement_authority_body,
    operator_recovery_authority_body,
    resolve_replacement_qualification_leaf,
)
from app.services.v2_drive_archive import (
    V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY,
    _caption_sidecar_source_ref,
    _normalized_destination_for_drive,
    _resolve_or_create_v2_drive_archive,
    _sidecar_archive_authority,
    require_v2_google_drive_final_media,
)
from app.services.v2_native_effects import (
    V2LocalNativeProductionAdapter,
    _load_json,
    _sha256_file,
)
from app.services.v2_provider_production import (
    V2AuthorizedAdapterOperation,
    _authorized_adapter_operation,
)
from app.contracts.vcos_v2 import ProductionLane


AUTHORITY_SCHEMA = "vcos.v2-drive-archive-property-limit-recovery-authority.v1"
RECEIPT_SCHEMA = "vcos.v2-drive-archive-property-limit-recovery-receipt.v1"
RECOVERY_REASON = "DRIVE_APP_PROPERTY_LIMIT_PRE_FILE_FAILURE"
ORIGINAL_FAILURE = "V2_GOOGLE_DRIVE_ARCHIVE_PROVIDER_FAILURE"
DEFECT_CODE = "GOOGLE_DRIVE_APP_PROPERTY_VALUE_LIMIT_EXCEEDED"
RECOVERY_HANDLER_VERSION = (
    "production-workflow.v1+v2-drive-archive-property-limit-recovery@1"
)
_CONTROLLED_RECOVERY_ACTOR_ID = uuid.UUID("6d196d74-7938-5c85-bc10-f25466616258")
_AUTHORITY_NAMESPACE = uuid.UUID("fe499eea-9ec1-5969-836e-e39f54aebcc4")
ABSENCE_EVIDENCE_SCHEMA = "vcos.v2-drive-archive-property-limit-absence-evidence.v1"


@dataclass(frozen=True, slots=True)
class V2DriveArchiveRemoteFileProof:
    """GET-only truth for one exact remote idempotency identity."""

    idempotency_key: str
    folder_path: tuple[str, ...]
    state: str
    drive_file_id: str | None = None
    checksum_sha256: str | None = None
    size_bytes: int | None = None
    match_count: int = 0
    checksum_matches: bool | None = None


@dataclass(frozen=True, slots=True)
class V2DriveArchiveAbsenceProof:
    """The four exact GET lookups required before a fresh recovery upload."""

    legacy_media: V2DriveArchiveRemoteFileProof
    legacy_caption: V2DriveArchiveRemoteFileProof
    canonical_media: V2DriveArchiveRemoteFileProof
    canonical_caption: V2DriveArchiveRemoteFileProof
    evidence_hash: str
    evidence: dict[str, Any] | None = None


def _classify_reconciliation(
    proof: V2DriveArchiveAbsenceProof, recovery_journal_exists: bool
) -> str:
    """Return the only safe next action for the current exact GET truth."""

    rows = (
        proof.legacy_media,
        proof.legacy_caption,
        proof.canonical_media,
        proof.canonical_caption,
    )
    allowed = {"ABSENT", "PRESENT"}
    if any(row.state not in allowed for row in rows):
        raise ValidationFailureError(
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RECONCILIATION_AMBIGUOUS"
        )
    for row in rows:
        if row.state == "ABSENT" and row.match_count != 0:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RECONCILIATION_INVALID"
            )
        if row.state == "PRESENT" and (
            row.match_count != 1
            or not row.drive_file_id
            or not row.checksum_sha256
            or not row.size_bytes
            or row.size_bytes <= 0
            or row.checksum_matches is not True
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REMOTE_PROOF_INVALID"
            )
    if proof.legacy_media.state != "ABSENT" or proof.legacy_caption.state != "ABSENT":
        raise ValidationFailureError(
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_LEGACY_OBJECT_UNEXPECTED"
        )
    media_present = proof.canonical_media.state == "PRESENT"
    caption_present = proof.canonical_caption.state == "PRESENT"
    if caption_present and not media_present:
        raise ValidationFailureError(
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_CAPTION_WITHOUT_MEDIA"
        )
    if not media_present and not caption_present:
        return "block_unknown" if recovery_journal_exists else "fresh_pair"
    if not recovery_journal_exists:
        raise ValidationFailureError(
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REMOTE_WITHOUT_JOURNAL"
        )
    return "settle_existing" if caption_present else "caption_only"


class V2DriveArchiveRecoveryProbe(Protocol):
    def reconcile(
        self,
        *,
        workflow_run_id: uuid.UUID,
        legacy_media_idempotency_key: str,
        legacy_caption_idempotency_key: str,
        media_idempotency_key: str,
        caption_idempotency_key: str,
        media_folder_path: tuple[str, ...],
        caption_folder_path: tuple[str, ...],
        expected_media_checksum: str,
        expected_caption_checksum: str,
    ) -> V2DriveArchiveAbsenceProof: ...


class PersistedV2DriveArchiveRecoveryProbe:
    """Build exact four-row reconciliation evidence with Drive GETs only."""

    def __init__(
        self,
        session: Session,
        *,
        config_service: GoogleDriveConfigService | None = None,
        credential_service: GoogleDriveOAuthCredentialService | None = None,
        provider: GoogleDriveMediaStorageProvider | None = None,
        now: Callable[[], Any] = utc_now,
    ) -> None:
        self.session = session
        self.config = config_service or GoogleDriveConfigService()
        self.credentials = credential_service or GoogleDriveOAuthCredentialService(
            session, config_service=self.config
        )
        self.provider = provider or GoogleDriveMediaStorageProvider()
        self.now = now

    def reconcile(
        self,
        *,
        workflow_run_id: uuid.UUID,
        legacy_media_idempotency_key: str,
        legacy_caption_idempotency_key: str,
        media_idempotency_key: str,
        caption_idempotency_key: str,
        media_folder_path: tuple[str, ...],
        caption_folder_path: tuple[str, ...],
        expected_media_checksum: str,
        expected_caption_checksum: str,
    ) -> V2DriveArchiveAbsenceProof:
        run = self.session.get(ProductionWorkflowRun, workflow_run_id)
        if run is None:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_WORKFLOW_MISSING"
            )
        reference = self.credentials.get_connected_reference(
            company_id=run.company_id,
            channel_workspace_id=run.channel_workspace_id,
        )
        token = (
            self.credentials.get_valid_access_token(reference)
            if reference is not None
            else None
        )
        root = self.config.root_folder_id()
        credential = self.session.scalar(
            select(GoogleDriveMediaCredential).where(
                GoogleDriveMediaCredential.credential_reference_id
                == (reference.id if reference else None),
                GoogleDriveMediaCredential.company_id == run.company_id,
                GoogleDriveMediaCredential.channel_workspace_id
                == run.channel_workspace_id,
                GoogleDriveMediaCredential.connection_state == "CONNECTED",
            )
        )
        if not token or not root or credential is None:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_CREDENTIAL_REQUIRED"
            )
        media_folder_id = self.provider.find_folder_path(
            access_token=token,
            root_folder_id=root,
            folder_path=list(media_folder_path),
        )
        caption_folder_id = self.provider.find_folder_path(
            access_token=token,
            root_folder_id=root,
            folder_path=list(caption_folder_path),
        )

        def lookup(
            *,
            key: str,
            folder: tuple[str, ...],
            folder_id: str | None,
            checksum: str,
            literal: bool,
        ) -> V2DriveArchiveRemoteFileProof:
            found = None
            if folder_id is not None:
                if literal:
                    found = self.provider.find_file_by_app_property_value(
                        access_token=token,
                        folder_id=folder_id,
                        property_value=key,
                    )
                else:
                    found = self.provider.find_file_by_idempotency_key(
                        access_token=token,
                        folder_id=folder_id,
                        idempotency_key=key,
                    )
            if found is None:
                return V2DriveArchiveRemoteFileProof(
                    idempotency_key=key,
                    folder_path=folder,
                    state="ABSENT",
                    match_count=0,
                )
            actual = found.checksum_sha256
            if not actual:
                actual = self.provider.readback_sha256(
                    access_token=token, drive_file_id=found.drive_file_id
                )
            return V2DriveArchiveRemoteFileProof(
                idempotency_key=key,
                folder_path=folder,
                state="PRESENT",
                drive_file_id=found.drive_file_id,
                checksum_sha256=actual,
                size_bytes=found.size_bytes,
                match_count=1,
                checksum_matches=actual == checksum,
            )

        proof_rows = {
            "legacy_media": lookup(
                key=legacy_media_idempotency_key,
                folder=media_folder_path,
                folder_id=media_folder_id,
                checksum=expected_media_checksum,
                literal=True,
            ),
            "legacy_caption": lookup(
                key=legacy_caption_idempotency_key,
                folder=caption_folder_path,
                folder_id=caption_folder_id,
                checksum=expected_caption_checksum,
                literal=True,
            ),
            "canonical_media": lookup(
                key=media_idempotency_key,
                folder=media_folder_path,
                folder_id=media_folder_id,
                checksum=expected_media_checksum,
                literal=False,
            ),
            "canonical_caption": lookup(
                key=caption_idempotency_key,
                folder=caption_folder_path,
                folder_id=caption_folder_id,
                checksum=expected_caption_checksum,
                literal=False,
            ),
        }
        evidence = {
            "schema_version": ABSENCE_EVIDENCE_SCHEMA,
            "workflow_run_id": str(workflow_run_id),
            "provider": "google_drive",
            "probe_mode": "GET_ONLY",
            "drive_credential_id": str(credential.id),
            "drive_root_folder_id": root,
            "observed_at": self.now().isoformat(),
            "expected_media_checksum": expected_media_checksum,
            "expected_caption_checksum": expected_caption_checksum,
            **{name: _proof_body(row) for name, row in proof_rows.items()},
        }
        evidence_hash = content_hash(evidence)
        return V2DriveArchiveAbsenceProof(
            **proof_rows, evidence_hash=evidence_hash, evidence=evidence
        )


@dataclass(frozen=True, slots=True)
class V2DriveArchivePropertyLimitRecoveryResult:
    workflow_run_id: uuid.UUID
    authority_id: uuid.UUID
    receipt_id: uuid.UUID | None
    archive_effect_ledger_id: uuid.UUID
    workflow_command_receipt_id: uuid.UUID | None
    workflow_state: str
    next_domain_event_id: uuid.UUID | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class _RecoveryScope:
    run: ProductionWorkflowRun
    ledger: V2ProductionEffectLedger
    event: DomainEvent
    dead_letter: DeadLetterJob
    root: ScriptContractReplacementAuthority
    settlement: ControlledVerifierSettlementAuthority
    qualification: ScriptQualificationRun
    package_version: ArtifactVersion
    budget: MR1MonthlyBudgetReservation
    credential: GoogleDriveMediaCredential
    legacy_journal_path: Path
    legacy_journal: dict[str, Any]
    legacy_journal_hash: str
    legacy_media_key: str
    legacy_caption_key: str
    media_key: str
    caption_key: str
    media_folder_path: tuple[str, ...]
    caption_folder_path: tuple[str, ...]
    source: Path
    caption_source: Path
    caption_authority: dict[str, str]
    measured_duration_ms: int
    budget_authority_hash: str


class V2DriveArchivePropertyLimitRecoveryService:
    """Authorize and execute exactly one MP4+SRT archive recovery.

    Crash matrix enforced by the implementation contract:

    * no recovery journal + all four exact lookups absent: fresh upload allowed;
    * journal exists + both canonical files absent: outcome unknown, no resubmit;
    * exact media exists and caption is absent: submit caption only;
    * both exact files exist: checksum-verify and settle without another upload;
    * any ambiguity or checksum mismatch: fail closed.
    """

    def __init__(
        self,
        session: Session,
        *,
        reconciliation_probe: V2DriveArchiveRecoveryProbe | None = None,
        upload_service_factory: Callable[[Session], Any] | None = None,
        session_factory: Callable[[], Session] | None = None,
        workspace_root: Path | None = None,
        now: Callable[[], Any] = utc_now,
    ) -> None:
        self.session = session
        self.reconciliation_probe = reconciliation_probe or (
            PersistedV2DriveArchiveRecoveryProbe(session, now=now)
        )
        self.upload_service_factory = upload_service_factory or GoogleDriveUploadService
        self.session_factory = session_factory or get_session_factory()
        self.adapter = V2LocalNativeProductionAdapter(
            workspace_root=workspace_root,
            session_factory=self.session_factory,
        )
        self.now = now

    def recover(
        self, workflow_run_id: uuid.UUID, actor: ActorContext
    ) -> V2DriveArchivePropertyLimitRecoveryResult:
        self._require_actor(actor)
        with self._recovery_lock(workflow_run_id):
            replay = self._replay_result(workflow_run_id)
            if replay is not None:
                return replay
            authority = self.authorize(workflow_run_id, actor)
            return self._recover_locked(workflow_run_id, authority, actor)

    def authorize(
        self, workflow_run_id: uuid.UUID, actor: ActorContext
    ) -> V2DriveArchivePropertyLimitRecoveryAuthority:
        """GET-reconcile four exact keys and commit authority before any write."""

        self._require_actor(actor)
        existing = self.session.scalar(
            select(V2DriveArchivePropertyLimitRecoveryAuthority).where(
                V2DriveArchivePropertyLimitRecoveryAuthority.workflow_run_id
                == workflow_run_id
            )
        )
        if existing is not None:
            self._validate_existing_authority(existing)
            return existing
        scope = self._resolve_scope(workflow_run_id, require_blocked=True)
        proof = self._probe(scope)
        if _classify_reconciliation(proof, False) != "fresh_pair":
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_INITIAL_ABSENCE_REQUIRED"
            )
        evidence = proof.evidence or self._absence_body(scope, proof)
        if proof.evidence_hash != content_hash(evidence):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_ABSENCE_HASH_MISMATCH"
            )
        authority_id = uuid.uuid5(_AUTHORITY_NAMESPACE, str(workflow_run_id))
        values: dict[str, Any] = {
            "id": authority_id,
            "workflow_run_id": scope.run.id,
            "video_project_id": scope.run.video_project_id,
            "archive_effect_ledger_id": scope.ledger.id,
            "archive_domain_event_id": scope.event.id,
            "archive_dead_letter_job_id": scope.dead_letter.id,
            "root_replacement_authority_id": scope.root.id,
            "verifier_settlement_authority_id": scope.settlement.id,
            "settlement_qualification_run_id": scope.qualification.id,
            "production_package_artifact_version_id": scope.package_version.id,
            "production_package_hash": scope.run.production_package_hash,
            "render_output_ref": scope.run.render_output_ref,
            "render_output_checksum": scope.run.render_output_checksum,
            "caption_output_ref": scope.caption_authority["caption_ref"],
            "caption_output_checksum": scope.caption_authority["caption_checksum"],
            "technical_qc_hash": scope.run.technical_qc_receipt_hash,
            "creative_qc_hash": scope.run.creative_qc_receipt_hash,
            "cross_modal_qc_hash": scope.run.cross_modal_qc_receipt_hash,
            "budget_reservation_id": scope.budget.id,
            "budget_reservation_ref": scope.budget.reservation_ref,
            "budget_authority_hash": scope.budget_authority_hash,
            "drive_credential_id": scope.credential.id,
            "drive_root_folder_id": str(scope.credential.root_folder_id),
            "media_folder_path": list(scope.media_folder_path),
            "caption_folder_path": list(scope.caption_folder_path),
            "archive_command_id": scope.ledger.command_id,
            "archive_operation_id": scope.ledger.operation_id,
            "archive_adapter_key": scope.ledger.adapter_key,
            "archive_input_hash": scope.ledger.input_hash,
            "legacy_request_journal_ref": self.adapter._relative(
                scope.legacy_journal_path
            ),
            "legacy_request_journal_hash": scope.legacy_journal_hash,
            "legacy_media_idempotency_key": scope.legacy_media_key,
            "legacy_caption_idempotency_key": scope.legacy_caption_key,
            "media_idempotency_key": scope.media_key,
            "caption_idempotency_key": scope.caption_key,
            "absence_reconciliation_evidence": evidence,
            "absence_reconciliation_hash": proof.evidence_hash,
            "original_failure_reason_code": ORIGINAL_FAILURE,
            "defect_code": DEFECT_CODE,
            "max_actual_upload_submissions": 1,
            "automatic_publish": False,
            "schema_version": AUTHORITY_SCHEMA,
            "recovery_reason": RECOVERY_REASON,
            "authorized_by_actor_type": actor.actor_type.value,
            "authorized_by_actor_id": actor.actor_id,
            "authorized_by_actor_role": actor.actor_role,
            "created_at": self.now(),
        }
        authority = V2DriveArchivePropertyLimitRecoveryAuthority(
            **values, authority_hash=content_hash(_authority_body(values))
        )
        self.session.add(authority)
        self.session.flush()
        self.session.commit()
        self.session.refresh(authority)
        return authority

    def _recover_locked(
        self,
        workflow_run_id: uuid.UUID,
        authority: V2DriveArchivePropertyLimitRecoveryAuthority,
        actor: ActorContext,
    ) -> V2DriveArchivePropertyLimitRecoveryResult:
        replay = self._replay_result(workflow_run_id)
        if replay is not None:
            return replay
        scope = self._resolve_scope(
            workflow_run_id, require_blocked=True, authority=authority
        )
        self._assert_authority_matches_scope(authority, scope)
        effect_dir = self.adapter._effect_dir(scope.ledger.command_id)
        request_path = effect_dir / "google-drive-property-limit-recovery-request.json"
        response_path = (
            effect_dir / "google-drive-property-limit-recovery-response.json"
        )
        proof = self._probe(scope)
        self._validate_current_probe_evidence(authority, proof)
        action = _classify_reconciliation(proof, request_path.exists())
        if action == "block_unknown":
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RECOVERY_OUTCOME_UNKNOWN"
            )
        prior_response: dict[str, Any] | None = None
        if response_path.exists():
            prior_response = _load_hashed_json(
                response_path,
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RESPONSE_INVALID",
            )
            prior_action = prior_response.get("reconciliation_action")
            action_call_counts = {
                "fresh_pair": 2,
                "caption_only": 1,
                "settle_existing": 0,
            }
            if (
                prior_response.get("authority_id") != str(authority.id)
                or prior_response.get("authority_hash") != authority.authority_hash
                or prior_response.get("workflow_run_id") != str(workflow_run_id)
                or not isinstance(prior_action, str)
                or prior_action not in action_call_counts
                or prior_response.get("provider_upload_file_call_count")
                != action_call_counts[prior_action]
                or prior_response.get("provider_file_count") != 2
                or prior_response.get("checksum_verified_file_count") != 2
                or prior_response.get("automatic_publish") is not False
            ):
                raise ValidationFailureError(
                    "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RESPONSE_INVALID"
                )

        request_identity = {
            "schema_version": "vcos.v2-drive-archive-property-limit-recovery-request.v1",
            "authority_id": str(authority.id),
            "authority_hash": authority.authority_hash,
            "workflow_run_id": str(workflow_run_id),
            "archive_effect_ledger_id": str(scope.ledger.id),
            "archive_command_id": scope.ledger.command_id,
            "archive_operation_id": scope.ledger.operation_id,
            "media_idempotency_key": authority.media_idempotency_key,
            "caption_idempotency_key": authority.caption_idempotency_key,
            "source_relative_path": self.adapter._relative(scope.source),
            "source_checksum": scope.run.render_output_checksum,
            "caption_relative_path": self.adapter._relative(scope.caption_source),
            "caption_checksum": scope.caption_authority["caption_checksum"],
            "absence_reconciliation_hash": authority.absence_reconciliation_hash,
            "max_actual_upload_submissions": 1,
            "automatic_publish": False,
            "initial_action": "fresh_pair",
            "state": "SUBMITTED",
        }
        if request_path.exists():
            prior = _load_hashed_json(
                request_path,
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REQUEST_JOURNAL_MISMATCH",
            )
            prior_without_hash = {
                key: value for key, value in prior.items() if key != "content_hash"
            }
            if prior_without_hash != request_identity:
                raise ValidationFailureError(
                    "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REQUEST_JOURNAL_MISMATCH"
                )
        else:
            if action != "fresh_pair":
                raise ValidationFailureError(
                    "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REMOTE_WITHOUT_JOURNAL"
                )
            request = dict(request_identity)
            request["content_hash"] = content_hash(request_identity)
            _write_exclusive_json(request_path, request)

        # Authority and the request journal are both durable at this boundary.
        # No workflow/event/dead-letter row is mutated before the two exact
        # Drive identities have checksum-verified readback evidence.
        context = WorkflowStageContext(
            session=self.session,
            actor=actor,
            run=scope.run,
            event=scope.event,
            command_id=scope.ledger.command_id,
            input_hash=scope.ledger.input_hash,
            execution_started_at=self.now(),
            execution_deadline=self.now() + timedelta(minutes=30),
            heartbeat=lambda: None,
        )
        sealed_operation = _authorized_adapter_operation(
            context, ProductionWorkflowStage.ARCHIVE
        )
        if (
            sealed_operation.operation_id != scope.ledger.operation_id
            or sealed_operation.adapter_key != scope.ledger.adapter_key
            or sealed_operation.parameters.get("provider_execution", {}).get(
                "idempotency_key"
            )
            != scope.legacy_media_key
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_SEALED_OPERATION_DRIFT"
            )
        parameters = dict(sealed_operation.parameters)
        provider_execution = dict(parameters["provider_execution"])
        provider_execution["idempotency_key"] = authority.media_idempotency_key
        parameters["provider_execution"] = provider_execution
        operation = V2AuthorizedAdapterOperation(
            operation_id=sealed_operation.operation_id,
            stage=sealed_operation.stage,
            adapter_key=sealed_operation.adapter_key,
            paid_provider_call=sealed_operation.paid_provider_call,
            max_cost_usd=sealed_operation.max_cost_usd,
            parameters=parameters,
            execution_mode=sealed_operation.execution_mode,
        )
        self.session.commit()
        upload_service = self.upload_service_factory(self.session)
        upload_file_call_count = 0
        media_kwargs = {
            "local_path": scope.source,
            "media_type": "LONG_FORM_FINAL",
            "company_id": scope.run.company_id,
            "channel_workspace_id": scope.run.channel_workspace_id,
            "video_project_id": scope.run.video_project_id,
            "uploaded_video_id": None,
            "render_package_id": None,
            "source_refs": [
                {
                    "type": "v2_render_output",
                    "workflow_run_id": str(scope.run.id),
                    "render_output_ref": scope.run.render_output_ref,
                    "render_output_checksum": scope.run.render_output_checksum,
                    "production_package_artifact_version_id": str(
                        scope.run.production_package_artifact_version_id
                    ),
                    "production_package_hash": scope.run.production_package_hash,
                }
            ],
            "retention_policy": {
                "keep_local": True,
                "cleanup_authorized": False,
                "source": "v2-real-archive-property-limit-recovery",
            },
            "idempotency_key": authority.media_idempotency_key,
        }
        caption_kwargs = {
            "local_path": scope.caption_source,
            "media_type": "CAPTION",
            "company_id": scope.run.company_id,
            "channel_workspace_id": scope.run.channel_workspace_id,
            "video_project_id": scope.run.video_project_id,
            "uploaded_video_id": None,
            "render_package_id": None,
            "source_refs": [
                _caption_sidecar_source_ref(scope.run, scope.caption_authority)
            ],
            "retention_policy": {
                "keep_local": True,
                "cleanup_authorized": False,
                "source": "v2-real-archive-sidecar-property-limit-recovery",
                "sidecar_only": True,
            },
            "idempotency_key": authority.caption_idempotency_key,
        }
        existing_media = self._existing_cloud_ref(
            authority=authority,
            media_type="LONG_FORM_FINAL",
            checksum=str(scope.run.render_output_checksum),
            expected_drive_file_id=proof.canonical_media.drive_file_id,
        )
        existing_caption = self._existing_cloud_ref(
            authority=authority,
            media_type="CAPTION",
            checksum=scope.caption_authority["caption_checksum"],
            expected_drive_file_id=proof.canonical_caption.drive_file_id,
        )
        verified = GoogleDriveVerificationResult(
            ok=True,
            verification_status="CHECKSUM_VERIFIED",
            reason_code="MEDIA_OFFLOAD_CHECKSUM_VERIFIED",
            size_verified=True,
            checksum_verified=True,
            checksum_unavailable=False,
        )
        if action == "fresh_pair":
            if existing_media is not None or existing_caption is not None:
                raise ValidationFailureError(
                    "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_UNEXPECTED_DB_ARTIFACT"
                )
            cloud, verification = upload_service.upload_verified(**media_kwargs)
            caption_cloud, caption_verification = upload_service.upload_verified(
                **caption_kwargs
            )
            upload_file_call_count = 2
        elif action == "caption_only":
            # The normal service's GET-first semantics adopts the exact media
            # object without another upload and submits only the missing SRT.
            if existing_caption is not None:
                raise ValidationFailureError(
                    "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_CAPTION_DB_DRIFT"
                )
            if existing_media is None:
                cloud, verification = upload_service.upload_verified(**media_kwargs)
            else:
                cloud, verification = existing_media, verified
            caption_cloud, caption_verification = upload_service.upload_verified(
                **caption_kwargs
            )
            upload_file_call_count = 1
        elif action == "settle_existing":
            if (existing_media is None) != (existing_caption is None):
                raise ValidationFailureError(
                    "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_PARTIAL_DB_ARTIFACT"
                )
            if existing_media is None:
                cloud, verification = upload_service.upload_verified(**media_kwargs)
                caption_cloud, caption_verification = upload_service.upload_verified(
                    **caption_kwargs
                )
            else:
                cloud, verification = existing_media, verified
                caption_cloud, caption_verification = existing_caption, verified
            upload_file_call_count = 0
        else:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_ACTION_INVALID"
            )
        if (
            verification.verification_status != "CHECKSUM_VERIFIED"
            or verification.checksum_verified is not True
            or caption_verification.verification_status != "CHECKSUM_VERIFIED"
            or caption_verification.checksum_verified is not True
            or cloud.checksum_sha256 != scope.run.render_output_checksum
            or caption_cloud.checksum_sha256
            != scope.caption_authority["caption_checksum"]
            or (
                proof.canonical_media.drive_file_id is not None
                and cloud.drive_file_id != proof.canonical_media.drive_file_id
            )
            or (
                proof.canonical_caption.drive_file_id is not None
                and caption_cloud.drive_file_id != proof.canonical_caption.drive_file_id
            )
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_CHECKSUM_READBACK_REQUIRED"
            )
        cloud.technical_appendix = {
            **(cloud.technical_appendix or {}),
            "measured_render_duration_ms": scope.measured_duration_ms,
            "v2_archive_command_id": scope.ledger.command_id,
            "v2_archive_idempotency_key": authority.media_idempotency_key,
            "v2_remote_archive": True,
            "property_limit_recovery_authority_id": str(authority.id),
        }
        caption_cloud.technical_appendix = {
            **(caption_cloud.technical_appendix or {}),
            "v2_caption_sidecar": True,
            "caption_ref": scope.caption_authority["caption_ref"],
            "caption_artifact_hash": scope.caption_authority["caption_artifact_hash"],
            "subtitle_qc_ref": scope.caption_authority["subtitle_qc_ref"],
            "subtitle_qc_hash": scope.caption_authority["subtitle_qc_hash"],
            "v2_archive_command_id": scope.ledger.command_id,
            "v2_archive_idempotency_key": authority.caption_idempotency_key,
            "property_limit_recovery_authority_id": str(authority.id),
        }
        self.session.flush()

        run = self.session.get(ProductionWorkflowRun, workflow_run_id)
        event = self.session.get(DomainEvent, scope.event.id)
        if run is None or event is None:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_SCOPE_DISAPPEARED"
            )
        context = WorkflowStageContext(
            session=self.session,
            actor=actor,
            run=run,
            event=event,
            command_id=scope.ledger.command_id,
            input_hash=scope.ledger.input_hash,
            execution_started_at=self.now(),
            execution_deadline=self.now() + timedelta(minutes=30),
            heartbeat=lambda: None,
        )
        try:
            artifact = _resolve_or_create_v2_drive_archive(
                session=self.session,
                context=context,
                operation=operation,
                external_effect_performed=True,
            )
        except Exception:
            self.session.rollback()
            raise
        if (
            artifact.cloud_media.id != cloud.id
            or artifact.caption_cloud_media.id != caption_cloud.id
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REMOTE_AUTHORITY_DRIFT"
            )
        # Commit the checksum-verified CloudMediaRefs, lineage and FinalMediaRef
        # before the response journal. A replay can now recover the same DB
        # identities after a crash instead of generating conflicting UUIDs.
        self.session.commit()
        self.session.expire_all()
        refreshed_run = self.session.get(ProductionWorkflowRun, workflow_run_id)
        refreshed_event = self.session.get(DomainEvent, scope.event.id)
        if refreshed_run is None or refreshed_event is None:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_SCOPE_DISAPPEARED"
            )
        refreshed_context = WorkflowStageContext(
            session=self.session,
            actor=actor,
            run=refreshed_run,
            event=refreshed_event,
            command_id=scope.ledger.command_id,
            input_hash=scope.ledger.input_hash,
            execution_started_at=self.now(),
            execution_deadline=self.now() + timedelta(minutes=30),
            heartbeat=lambda: None,
        )
        artifact = _resolve_or_create_v2_drive_archive(
            session=self.session,
            context=refreshed_context,
            operation=operation,
            external_effect_performed=True,
        )
        cloud = artifact.cloud_media
        caption_cloud = artifact.caption_cloud_media
        response = {
            "schema_version": "vcos.v2-drive-archive-property-limit-recovery-response.v1",
            "authority_id": str(authority.id),
            "authority_hash": authority.authority_hash,
            "workflow_run_id": str(workflow_run_id),
            "reconciliation_action": action,
            "media_cloud_media_ref_id": str(cloud.id),
            "caption_cloud_media_ref_id": str(caption_cloud.id),
            "final_media_ref_id": str(artifact.final_media.id),
            "media_drive_file_id": cloud.drive_file_id,
            "caption_drive_file_id": caption_cloud.drive_file_id,
            "media_checksum_sha256": cloud.checksum_sha256,
            "caption_checksum_sha256": caption_cloud.checksum_sha256,
            "archive_receipt_hash": artifact.archive_receipt_hash,
            "archive_object_ref": artifact.archive_object_ref,
            "caption_archive_object_ref": artifact.caption_archive_object_ref,
            "provider_file_count": 2,
            "provider_upload_file_call_count": upload_file_call_count,
            "checksum_verified_file_count": 2,
            "automatic_publish": False,
        }
        if prior_response is not None:
            response["reconciliation_action"] = prior_response["reconciliation_action"]
            response["provider_upload_file_call_count"] = prior_response[
                "provider_upload_file_call_count"
            ]
        response_body_hash = content_hash(response)
        response["content_hash"] = response_body_hash
        _persist_exact_json(response_path, response)
        request_journal = _load_hashed_json(
            request_path,
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REQUEST_JOURNAL_MISMATCH",
        )
        request_hash = str(request_journal["content_hash"])
        response_hash = response_body_hash

        destination = _normalized_destination_for_drive(refreshed_context)
        refs = WorkflowAuthorityRefs(
            video_project_id=refreshed_run.video_project_id,
            archive_receipt_ref=(
                f"v2-drive-archive-receipt://{cloud.id}/{artifact.archive_receipt_hash}"
            ),
            archive_receipt_hash=artifact.archive_receipt_hash,
            archive_object_ref=artifact.archive_object_ref,
            archive_verification_state="VERIFIED",
            final_media_ref_id=artifact.final_media.id,
            final_media_ref_hash=refreshed_run.render_output_checksum,
            destination_binding_id=destination["id"],
            destination_binding_fingerprint=destination["content_hash"],
            destination_binding=destination["binding"],
        )
        result = WorkflowStageResult(
            result_type="V2_VERIFIED_GOOGLE_DRIVE_REMOTE_ARCHIVE",
            result_id=artifact.final_media.id,
            result_ref=artifact.archive_object_ref,
            result_hash=artifact.archive_receipt_hash,
            result_payload={
                "archive_state": "VERIFIED",
                "storage_provider": "GOOGLE_DRIVE",
                "cloud_media_ref_id": str(cloud.id),
                "drive_file_id": cloud.drive_file_id,
                "caption_cloud_media_ref_id": str(caption_cloud.id),
                "caption_drive_file_id": caption_cloud.drive_file_id,
                "caption_archive_object_ref": artifact.caption_archive_object_ref,
                "checksum_sha256": artifact.final_media.checksum_sha256,
                "external_effect_performed": True,
                "automatic_publish": False,
                "property_limit_recovery_authority_id": str(authority.id),
            },
            authority_refs=refs,
            reason_codes=[
                "V2_GOOGLE_DRIVE_PROPERTY_LIMIT_RECOVERY_VERIFIED",
                "V2_GOOGLE_DRIVE_CHECKSUM_READBACK_VERIFIED",
            ],
            effect_state=WorkflowEffectState.RECONCILED,
        )
        journal = {
            "schema_version": "vcos.production-effect-journal.v1",
            "command_id": scope.ledger.command_id,
            "stage": "ARCHIVE",
            "state": "VERIFIED",
            "effect_invocation_count": 1,
            "provider_call_count": 1,
            "provider_file_count": 2,
            "provider_upload_file_call_count": upload_file_call_count,
            "provider": "google_drive",
            "idempotency_key": authority.media_idempotency_key,
            "caption_idempotency_key": authority.caption_idempotency_key,
            "source_relative_path": self.adapter._relative(scope.source),
            "source_checksum": refreshed_run.render_output_checksum,
            "measured_render_duration_ms": scope.measured_duration_ms,
            "cloud_media_ref_id": str(cloud.id),
            "drive_file_id": cloud.drive_file_id,
            "caption_ref": scope.caption_authority["caption_ref"],
            "caption_checksum": scope.caption_authority["caption_checksum"],
            "subtitle_qc_ref": scope.caption_authority["subtitle_qc_ref"],
            "caption_cloud_media_ref_id": str(caption_cloud.id),
            "caption_drive_file_id": caption_cloud.drive_file_id,
            "caption_archive_object_ref": artifact.caption_archive_object_ref,
            "archive_receipt_hash": artifact.archive_receipt_hash,
            "archive_object_ref": artifact.archive_object_ref,
            "external_effect_performed": True,
            "property_limit_recovery_authority_id": str(authority.id),
            "property_limit_recovery_authority_hash": authority.authority_hash,
        }
        return self._settle(
            scope=scope,
            authority=authority,
            result=result,
            journal=journal,
            artifact=artifact,
            request_path=request_path,
            request_hash=request_hash,
            response_path=response_path,
            response_hash=response_hash,
        )

    def _existing_cloud_ref(
        self,
        *,
        authority: V2DriveArchivePropertyLimitRecoveryAuthority,
        media_type: str,
        checksum: str,
        expected_drive_file_id: str | None,
    ) -> CloudMediaRef | None:
        candidates = list(
            self.session.scalars(
                select(CloudMediaRef).where(
                    CloudMediaRef.video_project_id == authority.video_project_id,
                    CloudMediaRef.storage_provider == "GOOGLE_DRIVE",
                    CloudMediaRef.media_type == media_type,
                )
            )
        )
        candidates = [
            row
            for row in candidates
            if isinstance(row.technical_appendix, dict)
            and row.technical_appendix.get("property_limit_recovery_authority_id")
            == str(authority.id)
        ]
        if len(candidates) > 1:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_DB_ARTIFACT_AMBIGUOUS"
            )
        if not candidates:
            return None
        candidate = candidates[0]
        if (
            candidate.checksum_sha256 != checksum
            or candidate.upload_status != "VERIFIED"
            or candidate.verification_status != "CHECKSUM_VERIFIED"
            or not candidate.drive_file_id
            or (
                expected_drive_file_id is not None
                and candidate.drive_file_id != expected_drive_file_id
            )
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_DB_ARTIFACT_DRIFT"
            )
        return candidate

    def _settle(
        self,
        *,
        scope: _RecoveryScope,
        authority: V2DriveArchivePropertyLimitRecoveryAuthority,
        result: WorkflowStageResult,
        journal: dict[str, Any],
        artifact: Any,
        request_path: Path,
        request_hash: str,
        response_path: Path,
        response_hash: str,
    ) -> V2DriveArchivePropertyLimitRecoveryResult:
        self.session.expire_all()
        run = self.session.scalar(
            select(ProductionWorkflowRun)
            .where(ProductionWorkflowRun.id == scope.run.id)
            .with_for_update()
        )
        ledger = self.session.scalar(
            select(V2ProductionEffectLedger)
            .where(V2ProductionEffectLedger.id == scope.ledger.id)
            .with_for_update()
        )
        if (
            run is None
            or ledger is None
            or run.state != "BLOCKED"
            or run.current_stage != "ARCHIVE"
            or ledger.state != "FAILED_UNCERTAIN"
            or ledger.effect_invocation_count != 1
            or ledger.result_hash is not None
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_SETTLEMENT_SCOPE_DRIFT"
            )
        ledger.result_type = result.result_type
        ledger.result_id = result.result_id
        ledger.result_ref = result.result_ref
        ledger.result_hash = result.result_hash
        ledger.result_payload = dict(result.result_payload)
        ledger.authority_refs = result.authority_refs.model_dump(mode="json")
        ledger.effect_journal = journal
        ledger.state = "VERIFIED"
        ledger.completed_at = self.now()
        self.session.flush()

        receipt_values = {
            "id": uuid.uuid5(_AUTHORITY_NAMESPACE, f"receipt:{run.id}"),
            "authority_id": authority.id,
            "workflow_run_id": run.id,
            "archive_effect_ledger_id": ledger.id,
            "media_cloud_media_ref_id": artifact.cloud_media.id,
            "caption_cloud_media_ref_id": artifact.caption_cloud_media.id,
            "final_media_ref_id": artifact.final_media.id,
            "media_drive_file_id": artifact.cloud_media.drive_file_id,
            "caption_drive_file_id": artifact.caption_cloud_media.drive_file_id,
            "media_checksum_sha256": artifact.cloud_media.checksum_sha256,
            "caption_checksum_sha256": artifact.caption_cloud_media.checksum_sha256,
            "archive_receipt_hash": artifact.archive_receipt_hash,
            "archive_object_ref": artifact.archive_object_ref,
            "caption_archive_object_ref": artifact.caption_archive_object_ref,
            "recovery_request_journal_ref": self.adapter._relative(request_path),
            "recovery_request_journal_hash": request_hash,
            "recovery_response_journal_ref": self.adapter._relative(response_path),
            "recovery_response_journal_hash": response_hash,
            "absence_reconciliation_hash": authority.absence_reconciliation_hash,
            "actual_upload_submissions": 1,
            "provider_file_count": 2,
            "checksum_verified_file_count": 2,
            "automatic_publish": False,
            "schema_version": RECEIPT_SCHEMA,
            "recovery_state": "VERIFIED",
            "created_at": self.now(),
        }
        recovery_receipt = V2DriveArchivePropertyLimitRecoveryReceipt(
            **receipt_values,
            receipt_hash=content_hash(_receipt_body(receipt_values)),
        )
        self.session.add(recovery_receipt)
        self.session.flush()
        command_receipt = WorkflowCommandReceipt(
            workflow_run_id=run.id,
            domain_event_id=scope.event.id,
            command_id=ledger.command_id,
            stage="ARCHIVE",
            handler_key=str(scope.event.payload["handler_key"]),
            handler_version=RECOVERY_HANDLER_VERSION,
            input_hash=ledger.input_hash,
            effect_state="RECONCILED",
            result_type=result.result_type,
            result_id=result.result_id,
            result_ref=result.result_ref,
            result_hash=result.result_hash,
            result_payload={
                **result.result_payload,
                "property_limit_recovery_receipt_id": str(recovery_receipt.id),
                "property_limit_recovery_receipt_hash": recovery_receipt.receipt_hash,
                "recovered_archive_domain_event_id": str(scope.event.id),
                "recovered_archive_dead_letter_job_id": str(scope.dead_letter.id),
            },
            authority_refs=result.authority_refs.model_dump(
                mode="json", exclude_none=True
            ),
            started_at=ledger.started_at or self.now(),
            completed_at=ledger.completed_at,
        )
        self.session.add(command_receipt)
        coordinator = ProductionWorkflowCoordinator(self.session, now=self.now)
        coordinator._apply_authority_refs(run, result.authority_refs)
        self.session.flush()
        coordinator._advance_after_receipt(
            run, command_receipt, reason_codes=result.reason_codes
        )
        self.session.flush()
        self.session.commit()
        self.session.refresh(run)
        next_event = self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.workflow_run_id == run.id,
                DomainEvent.command_id
                == command_id_for(run.id, ProductionWorkflowStage.FINALIZE),
            )
        )
        if (
            next_event is None
            or (next_event.payload or {}).get("stage") != "FINALIZE"
            or next_event.payload_hash != semantic_hash(next_event.payload or {})
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_FINALIZE_EVENT_DRIFT"
            )
        return V2DriveArchivePropertyLimitRecoveryResult(
            workflow_run_id=run.id,
            authority_id=authority.id,
            receipt_id=recovery_receipt.id,
            archive_effect_ledger_id=ledger.id,
            workflow_command_receipt_id=command_receipt.id,
            workflow_state=run.state,
            next_domain_event_id=next_event.id,
            replayed=False,
        )

    def _resolve_scope(
        self,
        workflow_run_id: uuid.UUID,
        *,
        require_blocked: bool,
        authority: V2DriveArchivePropertyLimitRecoveryAuthority | None = None,
    ) -> _RecoveryScope:
        run = self.session.scalar(
            select(ProductionWorkflowRun)
            .where(ProductionWorkflowRun.id == workflow_run_id)
            .with_for_update()
        )
        if (
            run is None
            or run.video_project_id is None
            or run.current_stage != "ARCHIVE"
            or (require_blocked and run.state != "BLOCKED")
            or run.production_package_artifact_version_id is None
            or run.production_package_hash is None
            or not run.render_output_ref
            or not run.render_output_checksum
            or not run.technical_qc_receipt_hash
            or not run.creative_qc_receipt_hash
            or not run.cross_modal_qc_receipt_hash
            or run.archive_receipt_hash is not None
            or run.final_media_ref_id is not None
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_SCOPE_INVALID"
            )
        ledgers = list(
            self.session.scalars(
                select(V2ProductionEffectLedger)
                .where(
                    V2ProductionEffectLedger.workflow_run_id == workflow_run_id,
                    V2ProductionEffectLedger.stage == "ARCHIVE",
                )
                .with_for_update()
            )
        )
        if len(ledgers) != 1:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_LEDGER_INVALID"
            )
        ledger = ledgers[0]
        event = self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.workflow_run_id == workflow_run_id,
                DomainEvent.command_id == ledger.command_id,
            )
        )
        dead_letter = self.session.scalar(
            select(DeadLetterJob).where(
                DeadLetterJob.domain_event_id == (event.id if event else None)
            )
        )
        settlements = list(
            self.session.scalars(
                select(ControlledVerifierSettlementAuthority)
                .join(
                    ScriptQualificationRun,
                    ScriptQualificationRun.id
                    == ControlledVerifierSettlementAuthority.settlement_qualification_run_id,
                )
                .where(
                    ScriptQualificationRun.production_workflow_run_id == workflow_run_id
                )
            )
        )
        if len(settlements) != 1:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_SETTLEMENT_INVALID"
            )
        settlement = settlements[0]
        root = self.session.get(
            ScriptContractReplacementAuthority,
            settlement.root_replacement_authority_id,
        )
        qualification = self.session.get(
            ScriptQualificationRun, settlement.settlement_qualification_run_id
        )
        package_version = self.session.get(
            ArtifactVersion, run.production_package_artifact_version_id
        )
        budget = self.session.scalar(
            select(MR1MonthlyBudgetReservation).where(
                MR1MonthlyBudgetReservation.run_id == workflow_run_id
            )
        )
        credential = self.session.scalar(
            select(GoogleDriveMediaCredential).where(
                GoogleDriveMediaCredential.company_id == run.company_id,
                GoogleDriveMediaCredential.channel_workspace_id
                == run.channel_workspace_id,
                GoogleDriveMediaCredential.connection_state == "CONNECTED",
            )
        )
        expected_input_hash = ProductionWorkflowCoordinator(
            self.session, now=self.now
        )._stage_input_hash(run, ProductionWorkflowStage.ARCHIVE)
        if (
            event is None
            or dead_letter is None
            or root is None
            or qualification is None
            or package_version is None
            or budget is None
            or credential is None
            or ledger.command_id
            != command_id_for(workflow_run_id, ProductionWorkflowStage.ARCHIVE)
            or ledger.adapter_key != V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY
            or ledger.input_hash != expected_input_hash
            or ledger.effect_invocation_count != 1
            or ledger.state not in {"FAILED_UNCERTAIN", "VERIFIED"}
            or (ledger.state == "VERIFIED" and authority is None)
            or (ledger.state == "FAILED_UNCERTAIN" and ledger.result_hash is not None)
            or event.dead_lettered_at is None
            or event.event_type != WORKFLOW_EVENT_TYPE
            or event.event_version != WORKFLOW_EVENT_VERSION
            or event.aggregate_type != WORKFLOW_AGGREGATE_TYPE
            or event.aggregate_id != workflow_run_id
            or event.payload_hash != semantic_hash(event.payload or {})
            or (event.payload or {}).get("stage") != "ARCHIVE"
            or (event.payload or {}).get("workflow_run_id") != str(workflow_run_id)
            or (event.payload or {}).get("handler_key")
            != handler_key_for(
                ProductionLane(run.production_lane), ProductionWorkflowStage.ARCHIVE
            )
            or (event.payload or {}).get("input_hash") != expected_input_hash
            or event.last_error_code != ORIGINAL_FAILURE
            or dead_letter.reason_code != ORIGINAL_FAILURE
            or dead_letter.replay_state != "NOT_REPLAYABLE"
            or dead_letter.retry_eligible is not False
            or package_version.content_hash != run.production_package_hash
            or budget.video_project_id != run.video_project_id
            or budget.status not in {"RESERVED", "SUBMITTED"}
            or credential.root_folder_id is None
            or root.authority_hash
            != content_hash(operator_recovery_authority_body(root))
            or settlement.authority_hash
            != content_hash(controlled_verifier_settlement_authority_body(settlement))
            or resolve_replacement_qualification_leaf(self.session, authority=root).id
            != qualification.id
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_LINEAGE_INVALID"
            )
        if (
            self.session.scalar(
                select(WorkflowCommandReceipt.id).where(
                    WorkflowCommandReceipt.workflow_run_id == workflow_run_id,
                    WorkflowCommandReceipt.stage.in_({"ARCHIVE", "FINALIZE"}),
                )
            )
            is not None
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_DOWNSTREAM_RECEIPT_EXISTS"
            )
        effect_dir = self.adapter._effect_dir(ledger.command_id)
        legacy_path = effect_dir / "google-drive-archive-request-journal.json"
        if not legacy_path.is_file() or legacy_path.is_symlink():
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_LEGACY_JOURNAL_REQUIRED"
            )
        legacy = _load_json(legacy_path)
        legacy_key = legacy.get("idempotency_key")
        if (
            legacy.get("schema_version") != "vcos.v2-google-drive-archive-request.v1"
            or legacy.get("command_id") != ledger.command_id
            or legacy.get("operation_id") != ledger.operation_id
            or legacy.get("state") != "SUBMITTED"
            or legacy.get("attempt_limit") != 1
            or not isinstance(legacy_key, str)
            or _drive_idempotency_property_value(legacy_key) == legacy_key
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_DEFECT_NOT_PROVEN"
            )
        source = self.adapter._from_relative(str(legacy["source_relative_path"]))
        caption_source = self.adapter._from_relative(
            str(legacy["caption_relative_path"])
        )
        media_ledger = self.session.scalar(
            select(V2ProductionEffectLedger).where(
                V2ProductionEffectLedger.workflow_run_id == workflow_run_id,
                V2ProductionEffectLedger.stage == "MEDIA",
                V2ProductionEffectLedger.state == "VERIFIED",
            )
        )
        render_ledger = self.session.scalar(
            select(V2ProductionEffectLedger).where(
                V2ProductionEffectLedger.workflow_run_id == workflow_run_id,
                V2ProductionEffectLedger.stage == "RENDER",
                V2ProductionEffectLedger.state == "VERIFIED",
            )
        )
        if media_ledger is None or render_ledger is None:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_UPSTREAM_EFFECTS_REQUIRED"
            )
        caption_authority = _sidecar_archive_authority(
            dict(media_ledger.effect_journal or {})
        )
        measured_duration_ms = int(
            (render_ledger.effect_journal or {}).get("measured_render_duration_ms") or 0
        )
        if (
            _sha256_file(source) != run.render_output_checksum
            or legacy.get("source_checksum") != run.render_output_checksum
            or _sha256_file(caption_source) != caption_authority["caption_checksum"]
            or legacy.get("caption_checksum") != caption_authority["caption_checksum"]
            or measured_duration_ms <= 0
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_LOCAL_SOURCE_DRIFT"
            )
        path_builder = DriveArchivePathBuilder()
        media_path = tuple(
            path_builder.build(
                company_id=run.company_id,
                channel_workspace_id=run.channel_workspace_id,
                video_project_id=run.video_project_id,
                uploaded_video_id=None,
                media_type="LONG_FORM_FINAL",
            ).folder_path
        )
        caption_path = tuple(
            path_builder.build(
                company_id=run.company_id,
                channel_workspace_id=run.channel_workspace_id,
                video_project_id=run.video_project_id,
                uploaded_video_id=None,
                media_type="CAPTION",
            ).folder_path
        )
        budget_hash = str((budget.capacity_evidence_json or {}).get("content_hash"))
        if len(budget_hash) != 64:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_BUDGET_AUTHORITY_INVALID"
            )
        return _RecoveryScope(
            run=run,
            ledger=ledger,
            event=event,
            dead_letter=dead_letter,
            root=root,
            settlement=settlement,
            qualification=qualification,
            package_version=package_version,
            budget=budget,
            credential=credential,
            legacy_journal_path=legacy_path,
            legacy_journal=legacy,
            legacy_journal_hash=content_hash(legacy),
            legacy_media_key=legacy_key,
            legacy_caption_key=f"{legacy_key}.caption",
            media_key=_drive_idempotency_property_value(legacy_key),
            caption_key=_drive_idempotency_property_value(f"{legacy_key}.caption"),
            media_folder_path=media_path,
            caption_folder_path=caption_path,
            source=source,
            caption_source=caption_source,
            caption_authority=caption_authority,
            measured_duration_ms=measured_duration_ms,
            budget_authority_hash=budget_hash,
        )

    def _probe(self, scope: _RecoveryScope) -> V2DriveArchiveAbsenceProof:
        proof = self.reconciliation_probe.reconcile(
            workflow_run_id=scope.run.id,
            legacy_media_idempotency_key=scope.legacy_media_key,
            legacy_caption_idempotency_key=scope.legacy_caption_key,
            media_idempotency_key=scope.media_key,
            caption_idempotency_key=scope.caption_key,
            media_folder_path=scope.media_folder_path,
            caption_folder_path=scope.caption_folder_path,
            expected_media_checksum=str(scope.run.render_output_checksum),
            expected_caption_checksum=scope.caption_authority["caption_checksum"],
        )
        expected = (
            (proof.legacy_media, scope.legacy_media_key, scope.media_folder_path),
            (
                proof.legacy_caption,
                scope.legacy_caption_key,
                scope.caption_folder_path,
            ),
            (proof.canonical_media, scope.media_key, scope.media_folder_path),
            (
                proof.canonical_caption,
                scope.caption_key,
                scope.caption_folder_path,
            ),
        )
        if any(
            row.idempotency_key != key or row.folder_path != path
            for row, key, path in expected
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_PROBE_IDENTITY_DRIFT"
            )
        if len(proof.evidence_hash) != 64:
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_PROBE_HASH_INVALID"
            )
        return proof

    def _absence_body(
        self, scope: _RecoveryScope, proof: V2DriveArchiveAbsenceProof
    ) -> dict[str, Any]:
        return {
            "schema_version": ABSENCE_EVIDENCE_SCHEMA,
            "workflow_run_id": str(scope.run.id),
            "provider": "google_drive",
            "probe_mode": "GET_ONLY",
            "drive_credential_id": str(scope.credential.id),
            "drive_root_folder_id": scope.credential.root_folder_id,
            "observed_at": self.now().isoformat(),
            "expected_media_checksum": scope.run.render_output_checksum,
            "expected_caption_checksum": scope.caption_authority["caption_checksum"],
            "legacy_media": _proof_body(proof.legacy_media),
            "legacy_caption": _proof_body(proof.legacy_caption),
            "canonical_media": _proof_body(proof.canonical_media),
            "canonical_caption": _proof_body(proof.canonical_caption),
        }

    def _assert_authority_matches_scope(
        self,
        authority: V2DriveArchivePropertyLimitRecoveryAuthority,
        scope: _RecoveryScope,
    ) -> None:
        self._validate_existing_authority(authority)
        pairs = {
            "workflow_run_id": scope.run.id,
            "video_project_id": scope.run.video_project_id,
            "archive_effect_ledger_id": scope.ledger.id,
            "archive_domain_event_id": scope.event.id,
            "archive_dead_letter_job_id": scope.dead_letter.id,
            "root_replacement_authority_id": scope.root.id,
            "verifier_settlement_authority_id": scope.settlement.id,
            "settlement_qualification_run_id": scope.qualification.id,
            "production_package_artifact_version_id": scope.package_version.id,
            "production_package_hash": scope.run.production_package_hash,
            "render_output_ref": scope.run.render_output_ref,
            "render_output_checksum": scope.run.render_output_checksum,
            "caption_output_ref": scope.caption_authority["caption_ref"],
            "caption_output_checksum": scope.caption_authority["caption_checksum"],
            "technical_qc_hash": scope.run.technical_qc_receipt_hash,
            "creative_qc_hash": scope.run.creative_qc_receipt_hash,
            "cross_modal_qc_hash": scope.run.cross_modal_qc_receipt_hash,
            "budget_reservation_id": scope.budget.id,
            "budget_reservation_ref": scope.budget.reservation_ref,
            "budget_authority_hash": scope.budget_authority_hash,
            "drive_credential_id": scope.credential.id,
            "drive_root_folder_id": scope.credential.root_folder_id,
            "media_folder_path": list(scope.media_folder_path),
            "caption_folder_path": list(scope.caption_folder_path),
            "archive_command_id": scope.ledger.command_id,
            "archive_operation_id": scope.ledger.operation_id,
            "archive_adapter_key": scope.ledger.adapter_key,
            "archive_input_hash": scope.ledger.input_hash,
            "legacy_request_journal_ref": self.adapter._relative(
                scope.legacy_journal_path
            ),
            "legacy_request_journal_hash": scope.legacy_journal_hash,
            "legacy_media_idempotency_key": scope.legacy_media_key,
            "legacy_caption_idempotency_key": scope.legacy_caption_key,
            "media_idempotency_key": scope.media_key,
            "caption_idempotency_key": scope.caption_key,
        }
        if any(getattr(authority, key) != value for key, value in pairs.items()):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_AUTHORITY_SCOPE_DRIFT"
            )
        if (
            authority.caption_output_ref != scope.caption_authority["caption_ref"]
            or authority.caption_output_checksum
            != scope.caption_authority["caption_checksum"]
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_CAPTION_AUTHORITY_DRIFT"
            )

    @staticmethod
    def _validate_current_probe_evidence(
        authority: V2DriveArchivePropertyLimitRecoveryAuthority,
        proof: V2DriveArchiveAbsenceProof,
    ) -> None:
        evidence = proof.evidence
        expected_identity = {
            "schema_version": ABSENCE_EVIDENCE_SCHEMA,
            "workflow_run_id": str(authority.workflow_run_id),
            "provider": "google_drive",
            "probe_mode": "GET_ONLY",
            "drive_credential_id": str(authority.drive_credential_id),
            "drive_root_folder_id": authority.drive_root_folder_id,
            "expected_media_checksum": authority.render_output_checksum,
            "expected_caption_checksum": authority.caption_output_checksum,
        }
        expected_rows = {
            "legacy_media": (
                proof.legacy_media,
                authority.legacy_media_idempotency_key,
                authority.media_folder_path,
            ),
            "legacy_caption": (
                proof.legacy_caption,
                authority.legacy_caption_idempotency_key,
                authority.caption_folder_path,
            ),
            "canonical_media": (
                proof.canonical_media,
                authority.media_idempotency_key,
                authority.media_folder_path,
            ),
            "canonical_caption": (
                proof.canonical_caption,
                authority.caption_idempotency_key,
                authority.caption_folder_path,
            ),
        }
        if (
            not isinstance(evidence, dict)
            or proof.evidence_hash != content_hash(evidence)
            or any(
                evidence.get(key) != value for key, value in expected_identity.items()
            )
            or not isinstance(evidence.get("observed_at"), str)
            or not evidence["observed_at"]
            or any(
                evidence.get(name) != _proof_body(row)
                or row.idempotency_key != key
                or list(row.folder_path) != list(folder_path)
                for name, (row, key, folder_path) in expected_rows.items()
            )
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_PROBE_AUTHORITY_DRIFT"
            )

    @staticmethod
    def _validate_existing_authority(
        authority: V2DriveArchivePropertyLimitRecoveryAuthority,
    ) -> None:
        evidence = authority.absence_reconciliation_evidence
        expected_evidence_identity = {
            "schema_version": ABSENCE_EVIDENCE_SCHEMA,
            "workflow_run_id": str(authority.workflow_run_id),
            "provider": "google_drive",
            "probe_mode": "GET_ONLY",
            "drive_credential_id": str(authority.drive_credential_id),
            "drive_root_folder_id": authority.drive_root_folder_id,
            "expected_media_checksum": authority.render_output_checksum,
            "expected_caption_checksum": authority.caption_output_checksum,
        }
        expected_rows = {
            "legacy_media": (
                authority.legacy_media_idempotency_key,
                authority.media_folder_path,
            ),
            "legacy_caption": (
                authority.legacy_caption_idempotency_key,
                authority.caption_folder_path,
            ),
            "canonical_media": (
                authority.media_idempotency_key,
                authority.media_folder_path,
            ),
            "canonical_caption": (
                authority.caption_idempotency_key,
                authority.caption_folder_path,
            ),
        }
        evidence_valid = isinstance(evidence, dict) and all(
            evidence.get(key) == value
            for key, value in expected_evidence_identity.items()
        )
        evidence_valid = (
            evidence_valid
            and isinstance(evidence.get("observed_at"), str)
            and bool(evidence["observed_at"])
        )
        if evidence_valid:
            for name, (key, folder_path) in expected_rows.items():
                row = evidence.get(name)
                if not isinstance(row, dict) or row != {
                    "idempotency_key": key,
                    "folder_path": list(folder_path),
                    "state": "ABSENT",
                    "match_count": 0,
                    "drive_file_id": None,
                    "checksum_sha256": None,
                    "size_bytes": None,
                    "checksum_matches": None,
                }:
                    evidence_valid = False
                    break
        if (
            authority.schema_version != AUTHORITY_SCHEMA
            or authority.recovery_reason != RECOVERY_REASON
            or authority.original_failure_reason_code != ORIGINAL_FAILURE
            or authority.defect_code != DEFECT_CODE
            or authority.max_actual_upload_submissions != 1
            or authority.automatic_publish is not False
            or authority.authorized_by_actor_type != "SYSTEM_WORKER"
            or authority.authorized_by_actor_role != "SYSTEM_WORKER"
            or authority.authorized_by_actor_id != _CONTROLLED_RECOVERY_ACTOR_ID
            or not evidence_valid
            or authority.absence_reconciliation_hash != content_hash(evidence)
            or authority.authority_hash != content_hash(_authority_body(authority))
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_AUTHORITY_INVALID"
            )

    def _replay_result(
        self, workflow_run_id: uuid.UUID
    ) -> V2DriveArchivePropertyLimitRecoveryResult | None:
        receipt = self.session.scalar(
            select(V2DriveArchivePropertyLimitRecoveryReceipt).where(
                V2DriveArchivePropertyLimitRecoveryReceipt.workflow_run_id
                == workflow_run_id
            )
        )
        if receipt is None:
            return None
        authority = self.session.get(
            V2DriveArchivePropertyLimitRecoveryAuthority, receipt.authority_id
        )
        ledger = self.session.get(
            V2ProductionEffectLedger, receipt.archive_effect_ledger_id
        )
        run = self.session.get(ProductionWorkflowRun, workflow_run_id)
        command_receipt = self.session.scalar(
            select(WorkflowCommandReceipt).where(
                WorkflowCommandReceipt.workflow_run_id == workflow_run_id,
                WorkflowCommandReceipt.stage == "ARCHIVE",
            )
        )
        next_event = self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.workflow_run_id == workflow_run_id,
                DomainEvent.command_id
                == command_id_for(workflow_run_id, ProductionWorkflowStage.FINALIZE),
            )
        )
        dead_letter = (
            self.session.get(DeadLetterJob, authority.archive_dead_letter_job_id)
            if authority is not None
            else None
        )
        old_event = (
            self.session.get(DomainEvent, authority.archive_domain_event_id)
            if authority is not None
            else None
        )
        if (
            authority is None
            or ledger is None
            or run is None
            or command_receipt is None
            or next_event is None
            or receipt.receipt_hash != content_hash(_receipt_body(receipt))
            or receipt.schema_version != RECEIPT_SCHEMA
            or receipt.recovery_state != "VERIFIED"
            or receipt.workflow_run_id != workflow_run_id
            or receipt.actual_upload_submissions != 1
            or receipt.provider_file_count != 2
            or receipt.checksum_verified_file_count != 2
            or receipt.automatic_publish is not False
            or receipt.absence_reconciliation_hash
            != authority.absence_reconciliation_hash
            or receipt.authority_id != authority.id
            or receipt.archive_effect_ledger_id != ledger.id
            or ledger.state != "VERIFIED"
            or ledger.result_type != "V2_VERIFIED_GOOGLE_DRIVE_REMOTE_ARCHIVE"
            or ledger.result_hash != receipt.archive_receipt_hash
            or ledger.workflow_run_id != workflow_run_id
            or ledger.video_project_id != authority.video_project_id
            or ledger.command_id != authority.archive_command_id
            or ledger.operation_id != authority.archive_operation_id
            or ledger.adapter_key != authority.archive_adapter_key
            or ledger.input_hash != authority.archive_input_hash
            or ledger.effect_invocation_count != 1
            or ledger.result_id != receipt.final_media_ref_id
            or ledger.result_ref != receipt.archive_object_ref
            or command_receipt.domain_event_id != authority.archive_domain_event_id
            or command_receipt.result_hash != receipt.archive_receipt_hash
            or command_receipt.workflow_run_id != workflow_run_id
            or command_receipt.command_id != authority.archive_command_id
            or command_receipt.stage != "ARCHIVE"
            or command_receipt.handler_key
            != handler_key_for(
                ProductionLane(run.production_lane), ProductionWorkflowStage.ARCHIVE
            )
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_INVALID"
            )
        self._validate_existing_authority(authority)
        request_path = self._journal_path(
            receipt.recovery_request_journal_ref,
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_REQUEST_INVALID",
        )
        response_path = self._journal_path(
            receipt.recovery_response_journal_ref,
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_RESPONSE_INVALID",
        )
        request = _load_hashed_json(
            request_path, "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_REQUEST_INVALID"
        )
        response = _load_hashed_json(
            response_path, "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_RESPONSE_INVALID"
        )
        request_hash = request.get("content_hash")
        response_hash = response.get("content_hash")
        legacy_path = self._journal_path(
            authority.legacy_request_journal_ref,
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_LEGACY_INVALID",
        )
        legacy = _load_json(legacy_path)
        if (
            not isinstance(legacy, dict)
            or content_hash(legacy) != authority.legacy_request_journal_hash
            or legacy.get("schema_version") != "vcos.v2-google-drive-archive-request.v1"
            or legacy.get("command_id") != authority.archive_command_id
            or legacy.get("operation_id") != authority.archive_operation_id
            or legacy.get("state") != "SUBMITTED"
            or legacy.get("attempt_limit") != 1
            or legacy.get("idempotency_key") != authority.legacy_media_idempotency_key
            or legacy.get("source_checksum") != authority.render_output_checksum
            or legacy.get("caption_checksum") != authority.caption_output_checksum
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_LEGACY_INVALID"
            )
        source_path = self._journal_path(
            str(legacy.get("source_relative_path")),
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_REQUEST_INVALID",
        )
        caption_path = self._journal_path(
            str(legacy.get("caption_relative_path")),
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_REQUEST_INVALID",
        )
        if (
            _sha256_file(source_path) != authority.render_output_checksum
            or _sha256_file(caption_path) != authority.caption_output_checksum
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_LOCAL_SOURCE_DRIFT"
            )
        request_expected = {
            "schema_version": "vcos.v2-drive-archive-property-limit-recovery-request.v1",
            "authority_id": str(authority.id),
            "authority_hash": authority.authority_hash,
            "workflow_run_id": str(workflow_run_id),
            "archive_effect_ledger_id": str(ledger.id),
            "archive_command_id": authority.archive_command_id,
            "archive_operation_id": authority.archive_operation_id,
            "media_idempotency_key": authority.media_idempotency_key,
            "caption_idempotency_key": authority.caption_idempotency_key,
            "source_relative_path": self.adapter._relative(source_path),
            "source_checksum": authority.render_output_checksum,
            "caption_relative_path": self.adapter._relative(caption_path),
            "caption_checksum": authority.caption_output_checksum,
            "absence_reconciliation_hash": authority.absence_reconciliation_hash,
            "max_actual_upload_submissions": 1,
            "automatic_publish": False,
            "initial_action": "fresh_pair",
            "state": "SUBMITTED",
        }
        request_without_hash = {
            key: value for key, value in request.items() if key != "content_hash"
        }
        response_action = response.get("reconciliation_action")
        response_call_counts = {
            "fresh_pair": 2,
            "caption_only": 1,
            "settle_existing": 0,
        }
        response_expected = {
            "schema_version": "vcos.v2-drive-archive-property-limit-recovery-response.v1",
            "authority_id": str(authority.id),
            "authority_hash": authority.authority_hash,
            "workflow_run_id": str(workflow_run_id),
            "reconciliation_action": response_action,
            "media_cloud_media_ref_id": str(receipt.media_cloud_media_ref_id),
            "caption_cloud_media_ref_id": str(receipt.caption_cloud_media_ref_id),
            "final_media_ref_id": str(receipt.final_media_ref_id),
            "media_drive_file_id": receipt.media_drive_file_id,
            "caption_drive_file_id": receipt.caption_drive_file_id,
            "media_checksum_sha256": receipt.media_checksum_sha256,
            "caption_checksum_sha256": receipt.caption_checksum_sha256,
            "archive_receipt_hash": receipt.archive_receipt_hash,
            "archive_object_ref": receipt.archive_object_ref,
            "caption_archive_object_ref": receipt.caption_archive_object_ref,
            "provider_file_count": 2,
            "provider_upload_file_call_count": response_call_counts.get(
                str(response_action)
            ),
            "checksum_verified_file_count": 2,
            "automatic_publish": False,
        }
        response_without_hash = {
            key: value for key, value in response.items() if key != "content_hash"
        }
        if (
            request_without_hash != request_expected
            or request_hash != receipt.recovery_request_journal_hash
            or response_hash != receipt.recovery_response_journal_hash
            or response_action not in response_call_counts
            or response_without_hash != response_expected
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_JOURNAL_INVALID"
            )
        artifact = require_v2_google_drive_final_media(
            self.session,
            project_id=authority.video_project_id,
            final_media_id=receipt.final_media_ref_id,
            expected_checksum=authority.render_output_checksum,
            expected_archive_hash=receipt.archive_receipt_hash,
        )
        result_payload = command_receipt.result_payload or {}
        archive_result_payload = {
            "archive_state": "VERIFIED",
            "storage_provider": "GOOGLE_DRIVE",
            "cloud_media_ref_id": str(receipt.media_cloud_media_ref_id),
            "drive_file_id": receipt.media_drive_file_id,
            "caption_cloud_media_ref_id": str(receipt.caption_cloud_media_ref_id),
            "caption_drive_file_id": receipt.caption_drive_file_id,
            "caption_archive_object_ref": receipt.caption_archive_object_ref,
            "checksum_sha256": authority.render_output_checksum,
            "external_effect_performed": True,
            "automatic_publish": False,
            "property_limit_recovery_authority_id": str(authority.id),
        }
        expected_command_payload = {
            **archive_result_payload,
            "property_limit_recovery_receipt_id": str(receipt.id),
            "property_limit_recovery_receipt_hash": receipt.receipt_hash,
            "recovered_archive_domain_event_id": str(authority.archive_domain_event_id),
            "recovered_archive_dead_letter_job_id": str(
                authority.archive_dead_letter_job_id
            ),
        }
        if (
            artifact.cloud_media.id != receipt.media_cloud_media_ref_id
            or artifact.caption_cloud_media.id != receipt.caption_cloud_media_ref_id
            or artifact.cloud_media.drive_file_id != receipt.media_drive_file_id
            or artifact.caption_cloud_media.drive_file_id
            != receipt.caption_drive_file_id
            or artifact.archive_object_ref != receipt.archive_object_ref
            or artifact.caption_archive_object_ref != receipt.caption_archive_object_ref
            or old_event is None
            or old_event.id != authority.archive_domain_event_id
            or old_event.event_type != WORKFLOW_EVENT_TYPE
            or old_event.event_version != WORKFLOW_EVENT_VERSION
            or old_event.aggregate_type != WORKFLOW_AGGREGATE_TYPE
            or old_event.aggregate_id != workflow_run_id
            or old_event.workflow_run_id != workflow_run_id
            or old_event.command_id != authority.archive_command_id
            or old_event.payload_hash != semantic_hash(old_event.payload or {})
            or (old_event.payload or {}).get("workflow_run_id") != str(workflow_run_id)
            or (old_event.payload or {}).get("stage") != "ARCHIVE"
            or (old_event.payload or {}).get("handler_key")
            != handler_key_for(
                ProductionLane(run.production_lane), ProductionWorkflowStage.ARCHIVE
            )
            or (old_event.payload or {}).get("input_hash")
            != authority.archive_input_hash
            or old_event.dead_lettered_at is None
            or old_event.last_error_code != ORIGINAL_FAILURE
            or dead_letter is None
            or dead_letter.id != authority.archive_dead_letter_job_id
            or dead_letter.domain_event_id != authority.archive_domain_event_id
            or dead_letter.workflow_run_id != workflow_run_id
            or dead_letter.command_id != authority.archive_command_id
            or dead_letter.reason_code != ORIGINAL_FAILURE
            or dead_letter.replay_state != "NOT_REPLAYABLE"
            or dead_letter.retry_eligible is not False
            or command_receipt.handler_version != RECOVERY_HANDLER_VERSION
            or command_receipt.input_hash != authority.archive_input_hash
            or command_receipt.effect_state != "RECONCILED"
            or command_receipt.result_type != "V2_VERIFIED_GOOGLE_DRIVE_REMOTE_ARCHIVE"
            or command_receipt.result_id != receipt.final_media_ref_id
            or command_receipt.result_ref != receipt.archive_object_ref
            or ledger.result_payload != archive_result_payload
            or result_payload != expected_command_payload
            or next_event.event_type != WORKFLOW_EVENT_TYPE
            or next_event.event_version != WORKFLOW_EVENT_VERSION
            or next_event.aggregate_type != WORKFLOW_AGGREGATE_TYPE
            or next_event.aggregate_id != workflow_run_id
            or next_event.company_id != run.company_id
            or next_event.channel_workspace_id != run.channel_workspace_id
            or next_event.workflow_run_id != workflow_run_id
            or next_event.correlation_id != f"production-workflow:{workflow_run_id}"
            or next_event.causation_id != authority.archive_domain_event_id
            or next_event.command_id
            != command_id_for(workflow_run_id, ProductionWorkflowStage.FINALIZE)
            or next_event.payload_hash != semantic_hash(next_event.payload or {})
            or (next_event.payload or {}).get("workflow_run_id") != str(workflow_run_id)
            or (next_event.payload or {}).get("stage") != "FINALIZE"
            or (next_event.payload or {}).get("production_lane") != run.production_lane
            or (next_event.payload or {}).get("handler_key")
            != handler_key_for(
                ProductionLane(run.production_lane), ProductionWorkflowStage.FINALIZE
            )
            or (next_event.payload or {}).get("input_hash")
            != ProductionWorkflowCoordinator(
                self.session, now=self.now
            )._stage_input_hash(run, ProductionWorkflowStage.FINALIZE)
            or (next_event.metadata_ or {}).get("schema_version")
            != "production-workflow-stage-event.v1"
            or (next_event.metadata_ or {}).get("stage") != "FINALIZE"
            or (next_event.metadata_ or {}).get("production_lane")
            != run.production_lane
            or next_event.attempt_count != 0
            or next_event.max_attempts
            != int((run.metadata_ or {}).get("max_attempts", 5))
            or next_event.dead_lettered_at is not None
            or next_event.delivered_at is not None
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_ARTIFACT_INVALID"
            )
        return V2DriveArchivePropertyLimitRecoveryResult(
            workflow_run_id=workflow_run_id,
            authority_id=authority.id,
            receipt_id=receipt.id,
            archive_effect_ledger_id=ledger.id,
            workflow_command_receipt_id=command_receipt.id,
            workflow_state=run.state,
            next_domain_event_id=next_event.id,
            replayed=True,
        )

    def _journal_path(self, value: str, error_code: str) -> Path:
        try:
            return self.adapter._from_relative(value)
        except (OSError, TypeError, ValueError) as exc:
            raise ValidationFailureError(error_code) from exc

    @contextmanager
    def _recovery_lock(self, workflow_run_id: uuid.UUID):
        engine = self.session.get_bind()
        lock_connection = engine.connect()
        acquired = False
        try:
            acquired = bool(
                lock_connection.scalar(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:key, 7702))"),
                    {"key": str(workflow_run_id)},
                )
            )
            if not acquired:
                raise ValidationFailureError(
                    "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RECOVERY_ALREADY_RUNNING"
                )
            yield
        finally:
            if acquired:
                lock_connection.scalar(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 7702))"),
                    {"key": str(workflow_run_id)},
                )
            lock_connection.close()

    @staticmethod
    def _require_actor(actor: ActorContext) -> None:
        if (
            actor.actor_type != ActorType.SYSTEM_WORKER
            or actor.actor_role != "SYSTEM_WORKER"
            or actor.actor_id != _CONTROLLED_RECOVERY_ACTOR_ID
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RECOVERY_SYSTEM_WORKER_REQUIRED"
            )


def _proof_body(row: V2DriveArchiveRemoteFileProof) -> dict[str, Any]:
    return {
        "idempotency_key": row.idempotency_key,
        "folder_path": list(row.folder_path),
        "state": row.state,
        "match_count": row.match_count,
        "drive_file_id": row.drive_file_id,
        "checksum_sha256": row.checksum_sha256,
        "size_bytes": row.size_bytes,
        "checksum_matches": row.checksum_matches,
    }


def _hash_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_hash_value(item) for item in value]
    if isinstance(value, list):
        return [_hash_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _hash_value(item) for key, item in value.items()}
    return value


def _model_hash_body(value: Any, *, hash_field: str, id_label: str) -> dict[str, Any]:
    columns = (
        [column.key for column in value.__table__.columns if column.key != hash_field]
        if not isinstance(value, dict)
        else [key for key in value if key != hash_field]
    )
    raw = (
        value
        if isinstance(value, dict)
        else {name: getattr(value, name) for name in columns}
    )
    row_id = raw["id"]
    body = {name: _hash_value(raw[name]) for name in columns if name != "id"}
    return {id_label: _hash_value(row_id), **body}


def _authority_body(value: Any) -> dict[str, Any]:
    return _model_hash_body(value, hash_field="authority_hash", id_label="authority_id")


def _receipt_body(value: Any) -> dict[str, Any]:
    return _model_hash_body(value, hash_field="receipt_hash", id_label="receipt_id")


def _load_hashed_json(path: Path, error_code: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValidationFailureError(error_code)
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValidationFailureError(error_code)
    body = {key: value for key, value in payload.items() if key != "content_hash"}
    if payload.get("content_hash") != content_hash(body):
        raise ValidationFailureError(error_code)
    return payload


def _write_exclusive_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if (
            _load_hashed_json(path, "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_JOURNAL_MISMATCH")
            != payload
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_JOURNAL_MISMATCH"
            )
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    directory_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _persist_exact_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        if (
            _load_hashed_json(path, "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RESPONSE_MISMATCH")
            != payload
        ):
            raise ValidationFailureError(
                "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RESPONSE_MISMATCH"
            )
        return
    _write_exclusive_json(path, payload)


__all__ = [
    "ABSENCE_EVIDENCE_SCHEMA",
    "AUTHORITY_SCHEMA",
    "DEFECT_CODE",
    "ORIGINAL_FAILURE",
    "RECEIPT_SCHEMA",
    "RECOVERY_REASON",
    "V2DriveArchiveAbsenceProof",
    "V2DriveArchivePropertyLimitRecoveryAuthority",
    "V2DriveArchivePropertyLimitRecoveryReceipt",
    "V2DriveArchivePropertyLimitRecoveryResult",
    "V2DriveArchivePropertyLimitRecoveryService",
    "V2DriveArchiveRecoveryProbe",
    "V2DriveArchiveRemoteFileProof",
    "_classify_reconciliation",
]
