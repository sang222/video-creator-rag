"""No-network coverage for bounded Drive archive pre-effect recovery."""

from __future__ import annotations

import json
import socket
import uuid
import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.contracts.production_workflow import ProductionWorkflowStage
from app.core.actor import _system_worker_actor, authenticated_actor_context
from app.core.errors import ValidationFailureError
from app.db.models.foundation import DomainEvent
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef, GoogleDriveMediaCredential
from app.db.models.m7 import PublishHandoffPackage
from app.db.models.ops import CredentialReference, DeadLetterJob
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.db.models.v2_effect import (
    V2DriveArchivePropertyLimitRecoveryAuthority,
    V2DriveArchivePropertyLimitRecoveryReceipt,
    V2ProductionEffectLedger,
)
from app.db.models.workflow import ArtifactVersion
from app.services.config_registry import content_hash
from app.services.launch_cadence import LongFormCadenceService
from app.services.m10_5 import (
    GOOGLE_DRIVE_IDEMPOTENCY_PROPERTY_KEY,
    GOOGLE_DRIVE_SCOPE,
    GoogleDriveVerificationResult,
)
from app.services.production_package import ProductionPackageService
from app.services.production_workflow import (
    ProductionWorkflowCoordinator,
    command_id_for,
)
from app.services.script_verifier_settlement import (
    ScriptVerifierSettlementRecoveryService,
)
from app.services.scoped_replacement_runner import ScopedReplacementContinuationRunner
from app.services.v2_drive_archive import (
    V2DriveArchiveReadiness,
    V2GoogleDriveRemoteArchiveAdapter,
)
from app.services.v2_drive_archive_property_limit_recovery import (
    V2DriveArchiveAbsenceProof,
    V2DriveArchivePropertyLimitRecoveryService,
    V2DriveArchiveRemoteFileProof,
    _classify_reconciliation,
)
import app.services.v2_drive_archive_property_limit_recovery as recovery_module
from app.services.v2_provider_production import build_v2_provider_production_gateway
from app.workers.production_workflow import ProductionWorkflowWorker
from tests.qualification.conftest import QualificationFactory
from tests.test_controlled_verifier_settlement import (
    _blocked_live_shaped_source,
    _install_ready_finalization_authorities,
    _install_research_context_source,
    _run_exact_worker_to_readiness,
)
from tests.test_v2_narration_timing_recovery import _compact_live_shaped_payload
import tests.test_controlled_verifier_settlement as settlement_test


class _NetworkForbidden(AssertionError):
    pass


class _SimulatedCrash(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _forbid_accidental_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recovery tests may use only injected fakes, never a real provider."""

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise _NetworkForbidden("test attempted an undeclared network connection")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def _proof(
    *,
    legacy_media: str = "ABSENT",
    legacy_caption: str = "ABSENT",
    canonical_media: str = "ABSENT",
    canonical_caption: str = "ABSENT",
) -> V2DriveArchiveAbsenceProof:
    def item(key: str, state: str, role: str) -> V2DriveArchiveRemoteFileProof:
        return V2DriveArchiveRemoteFileProof(
            idempotency_key=key,
            folder_path=("company_scope", "project_scope", role),
            state=state,
            drive_file_id=f"drive-{key}" if state == "PRESENT" else None,
            checksum_sha256="b" * 64 if state == "PRESENT" else None,
            size_bytes=1024 if state == "PRESENT" else None,
            match_count=1 if state == "PRESENT" else 0,
            checksum_matches=True if state == "PRESENT" else None,
        )

    return V2DriveArchiveAbsenceProof(
        legacy_media=item("legacy-media", legacy_media, "video"),
        legacy_caption=item("legacy-caption", legacy_caption, "captions"),
        canonical_media=item("canonical-media", canonical_media, "video"),
        canonical_caption=item("canonical-caption", canonical_caption, "captions"),
        evidence_hash="a" * 64,
    )


@dataclass(slots=True)
class _BlockedArchiveScope:
    workflow_id: uuid.UUID
    archive_ledger_id: uuid.UUID
    archive_event_id: uuid.UUID
    command_id: str
    operation_id: str
    legacy_media_key: str
    workspace_root: Path
    source_path: Path
    caption_path: Path
    media_checksum: str
    caption_checksum: str
    credential_id: uuid.UUID


class _ReadyGate:
    def __init__(self, credential_id: uuid.UUID) -> None:
        self.credential_id = credential_id

    def require_ready(self, **_kwargs: object) -> V2DriveArchiveReadiness:
        return V2DriveArchiveReadiness(
            credential_id=self.credential_id,
            root_folder_id="offline-drive-root",
            scopes=(GOOGLE_DRIVE_SCOPE,),
        )


class _PreFilePropertyLimitFailure:
    """Old upload boundary: validation fails before a remote file can exist."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def upload_verified(self, **kwargs: Any) -> tuple[object, object]:
        self.calls.append(dict(kwargs))
        raise ValidationFailureError(
            "The combined key and value size of this app property exceeds 124 bytes."
        )


@dataclass(slots=True)
class _RemoteObject:
    drive_file_id: str
    checksum: str
    size_bytes: int


class _FakeDriveBoundary:
    """Shared GET/upload state with provider-call counters and no network."""

    def __init__(
        self,
        *,
        credential_id: uuid.UUID | None = None,
        root_folder_id: str = "offline-drive-root",
        observed_at: datetime = datetime(2026, 8, 13, 0, 0, tzinfo=UTC),
    ) -> None:
        self.credential_id = credential_id
        self.root_folder_id = root_folder_id
        self.observed_at = observed_at
        self.remote: dict[str, _RemoteObject] = {}
        self.ambiguous_keys: set[str] = set()
        self.probe_calls: list[dict[str, Any]] = []
        self.upload_file_calls: list[str] = []
        self.upload_verified_calls: list[str] = []

    def reconcile(self, **kwargs: Any) -> V2DriveArchiveAbsenceProof:
        self.probe_calls.append(dict(kwargs))

        def row(
            *, key: str, folder: tuple[str, ...], expected: str
        ) -> V2DriveArchiveRemoteFileProof:
            if key in self.ambiguous_keys:
                return V2DriveArchiveRemoteFileProof(
                    idempotency_key=key,
                    folder_path=folder,
                    state="AMBIGUOUS",
                    match_count=2,
                )
            remote = self.remote.get(key)
            if remote is None:
                return V2DriveArchiveRemoteFileProof(
                    idempotency_key=key,
                    folder_path=folder,
                    state="ABSENT",
                    match_count=0,
                )
            return V2DriveArchiveRemoteFileProof(
                idempotency_key=key,
                folder_path=folder,
                state="PRESENT",
                drive_file_id=remote.drive_file_id,
                checksum_sha256=remote.checksum,
                size_bytes=remote.size_bytes,
                match_count=1,
                checksum_matches=remote.checksum == expected,
            )

        rows = {
            "legacy_media": row(
                key=kwargs["legacy_media_idempotency_key"],
                folder=kwargs["media_folder_path"],
                expected=kwargs["expected_media_checksum"],
            ),
            "legacy_caption": row(
                key=kwargs["legacy_caption_idempotency_key"],
                folder=kwargs["caption_folder_path"],
                expected=kwargs["expected_caption_checksum"],
            ),
            "canonical_media": row(
                key=kwargs["media_idempotency_key"],
                folder=kwargs["media_folder_path"],
                expected=kwargs["expected_media_checksum"],
            ),
            "canonical_caption": row(
                key=kwargs["caption_idempotency_key"],
                folder=kwargs["caption_folder_path"],
                expected=kwargs["expected_caption_checksum"],
            ),
        }
        evidence = {
            "schema_version": "vcos.v2-drive-archive-property-limit-absence-evidence.v1",
            "workflow_run_id": str(kwargs["workflow_run_id"]),
            "provider": "google_drive",
            "probe_mode": "GET_ONLY",
            "drive_credential_id": str(self.credential_id),
            "drive_root_folder_id": self.root_folder_id,
            "observed_at": self.observed_at.isoformat(),
            "expected_media_checksum": kwargs["expected_media_checksum"],
            "expected_caption_checksum": kwargs["expected_caption_checksum"],
            **{
                name: {
                    "idempotency_key": proof.idempotency_key,
                    "folder_path": list(proof.folder_path),
                    "state": proof.state,
                    "match_count": proof.match_count,
                    "drive_file_id": proof.drive_file_id,
                    "checksum_sha256": proof.checksum_sha256,
                    "size_bytes": proof.size_bytes,
                    "checksum_matches": proof.checksum_matches,
                }
                for name, proof in rows.items()
            },
        }
        return V2DriveArchiveAbsenceProof(
            **rows,
            evidence_hash=content_hash(evidence),
            evidence=evidence,
        )

    def upload_service(self, session: Any) -> "_FakeDriveUploadService":
        return _FakeDriveUploadService(session=session, boundary=self)


class _FakeDriveUploadService:
    def __init__(self, *, session: Any, boundary: _FakeDriveBoundary) -> None:
        self.session = session
        self.boundary = boundary

    def upload_verified(self, **kwargs: Any) -> tuple[CloudMediaRef, Any]:
        key = kwargs["idempotency_key"]
        local_path = Path(kwargs["local_path"])
        checksum = _sha256(local_path)
        self.boundary.upload_verified_calls.append(key)
        remote = self.boundary.remote.get(key)
        if remote is None:
            self.boundary.upload_file_calls.append(key)
            remote = _RemoteObject(
                drive_file_id=f"offline-drive-{len(self.boundary.remote) + 1}",
                checksum=checksum,
                size_bytes=local_path.stat().st_size,
            )
            self.boundary.remote[key] = remote
        cloud = self.session.scalar(
            select(CloudMediaRef).where(
                CloudMediaRef.drive_file_id == remote.drive_file_id,
                CloudMediaRef.video_project_id == kwargs["video_project_id"],
                CloudMediaRef.media_type == kwargs["media_type"],
            )
        )
        if cloud is None:
            cloud = CloudMediaRef(
                company_id=kwargs["company_id"],
                channel_workspace_id=kwargs["channel_workspace_id"],
                video_project_id=kwargs["video_project_id"],
                uploaded_video_id=None,
                render_package_id=None,
                media_type=kwargs["media_type"],
                storage_provider="GOOGLE_DRIVE",
                drive_file_id=remote.drive_file_id,
                drive_folder_id="offline-folder",
                web_view_link=(
                    f"https://drive.google.com/file/d/{remote.drive_file_id}/view"
                ),
                mime_type=(
                    "video/mp4"
                    if kwargs["media_type"] == "LONG_FORM_FINAL"
                    else "application/x-subrip"
                ),
                file_name=local_path.name,
                size_bytes=remote.size_bytes,
                checksum_sha256=remote.checksum,
                local_source_path_hash=checksum,
                upload_status="VERIFIED",
                verification_status="CHECKSUM_VERIFIED",
                retention_policy=kwargs["retention_policy"],
                source_refs=kwargs["source_refs"],
                technical_appendix={
                    "drive_file_id_verified": True,
                    "size_verified": True,
                    "checksum_verified": True,
                },
            )
            self.session.add(cloud)
            self.session.flush()
        verification = GoogleDriveVerificationResult(
            ok=remote.checksum == checksum,
            verification_status="CHECKSUM_VERIFIED",
            reason_code="GOOGLE_DRIVE_CHECKSUM_VERIFIED",
            size_verified=remote.size_bytes == local_path.stat().st_size,
            checksum_verified=remote.checksum == checksum,
            checksum_unavailable=False,
        )
        return cloud, verification


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _controlled_recovery_actor() -> Any:
    return _system_worker_actor(
        "vcos-controlled-recovery", permissions={"production.start"}
    )


def _recovery_service(
    *,
    db_session: Any,
    engine: Any,
    scope: _BlockedArchiveScope,
    boundary: _FakeDriveBoundary,
    upload_service_factory: Callable[[Any], Any] | None = None,
) -> V2DriveArchivePropertyLimitRecoveryService:
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return V2DriveArchivePropertyLimitRecoveryService(
        db_session,
        reconciliation_probe=boundary,
        upload_service_factory=upload_service_factory or boundary.upload_service,
        session_factory=factory,
        workspace_root=scope.workspace_root,
    )


def _recovery_paths(scope: _BlockedArchiveScope) -> tuple[Path, Path]:
    effect_dir = (
        scope.workspace_root
        / "effects"
        / hashlib.sha256(scope.command_id.encode("utf-8")).hexdigest()
    )
    return (
        effect_dir / "google-drive-property-limit-recovery-request.json",
        effect_dir / "google-drive-property-limit-recovery-response.json",
    )


def _verified_effect(
    *,
    workflow: ProductionWorkflowRun,
    stage: str,
    journal: dict[str, Any],
) -> V2ProductionEffectLedger:
    now = datetime.now(UTC)
    result_hash = content_hash({"workflow": str(workflow.id), "stage": stage})
    return V2ProductionEffectLedger(
        workflow_run_id=workflow.id,
        video_project_id=workflow.video_project_id,
        production_package_artifact_version_id=(
            workflow.production_package_artifact_version_id
        ),
        production_package_hash=workflow.production_package_hash,
        command_id=f"offline-{stage.casefold()}-{workflow.id}",
        stage=stage,
        operation_id=f"offline-{stage.casefold()}-operation",
        adapter_key=(
            "v2-elevenlabs-narration" if stage == "MEDIA" else "v2-local-native"
        ),
        input_hash=content_hash({"input": str(workflow.id), "stage": stage}),
        state="VERIFIED",
        effect_invocation_count=1,
        result_type=f"OFFLINE_{stage}",
        result_ref=f"offline://{stage.casefold()}/{result_hash}",
        result_hash=result_hash,
        result_payload={},
        authority_refs={},
        effect_journal=journal,
        started_at=now,
        completed_at=now,
    )


def _seed_blocked_archive_scope(
    *,
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[_BlockedArchiveScope, _PreFilePropertyLimitFailure]:
    """Create the exact old SUBMITTED/FAILED_UNCERTAIN failure via the worker."""

    monkeypatch.setattr(
        settlement_test,
        "_live_shaped_v2_payload",
        _compact_live_shaped_payload,
    )
    _install_research_context_source(monkeypatch)
    lineage = _blocked_live_shaped_source(
        db_session, QualificationFactory(db_session), monkeypatch
    )
    _install_ready_finalization_authorities(monkeypatch)
    child = ScriptVerifierSettlementRecoveryService(
        db_session, now=lambda: lineage.settlement_now
    ).create(source_qualification_run_id=lineage.child.id)
    _admission, workflow = LongFormCadenceService(
        db_session, now=lambda: lineage.settlement_now
    ).finalize_qualified_script_run(
        script_qualification_run_id=child.id,
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    db_session.commit()
    _run_exact_worker_to_readiness(engine, workflow.id)
    db_session.expire_all()
    workflow = db_session.get(ProductionWorkflowRun, workflow.id)
    assert workflow is not None

    workspace_root = tmp_path / "v2-production"
    source_path = workspace_root / "runs" / "offline-render" / "final.mp4"
    caption_path = (
        workspace_root / "effects" / "offline-media" / "canonical-captions.srt"
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    caption_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"offline-exact-render-bytes")
    caption_path.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nExact sidecar.\n")
    media_checksum = _sha256(source_path)
    caption_checksum = _sha256(caption_path)

    timeline_hash = content_hash({"workflow": str(workflow.id), "timeline": True})
    plan_hash = content_hash({"workflow": str(workflow.id), "plan": True})
    technical_hash = content_hash({"workflow": str(workflow.id), "technical": True})
    creative_hash = content_hash({"workflow": str(workflow.id), "creative": True})
    cross_modal_hash = content_hash({"workflow": str(workflow.id), "cross_modal": True})
    workflow.canonical_media_timeline_ref = f"offline://timeline/{timeline_hash}"
    workflow.canonical_media_timeline_hash = timeline_hash
    workflow.native_render_plan_ref = f"offline://plan/{plan_hash}"
    workflow.native_render_plan_hash = plan_hash
    workflow.render_output_ref = f"offline://render/{media_checksum}"
    workflow.render_output_checksum = media_checksum
    workflow.technical_qc_receipt_ref = f"offline://qc/technical/{technical_hash}"
    workflow.technical_qc_receipt_hash = technical_hash
    workflow.creative_qc_receipt_ref = f"offline://qc/creative/{creative_hash}"
    workflow.creative_qc_receipt_hash = creative_hash
    workflow.cross_modal_qc_receipt_ref = f"offline://qc/cross/{cross_modal_hash}"
    workflow.cross_modal_qc_receipt_hash = cross_modal_hash
    workflow.state = "ARCHIVE_PENDING"
    workflow.current_stage = "ARCHIVE"
    workflow.state_reason_codes = []

    caption_ref = "artifact-version://offline-caption"
    caption_artifact_hash = content_hash({"caption": caption_checksum})
    subtitle_qc_ref = "artifact-version://offline-subtitle-qc"
    subtitle_qc_hash = content_hash({"subtitle_qc": caption_checksum})
    relative_source = source_path.relative_to(workspace_root).as_posix()
    relative_caption = caption_path.relative_to(workspace_root).as_posix()
    db_session.add_all(
        [
            _verified_effect(
                workflow=workflow,
                stage="MEDIA",
                journal={
                    "state": "VERIFIED",
                    "caption_relative_path": relative_caption,
                    "caption_checksum": caption_checksum,
                    "caption_ref": caption_ref,
                    "caption_artifact_hash": caption_artifact_hash,
                    "subtitle_qc_ref": subtitle_qc_ref,
                    "subtitle_qc_hash": subtitle_qc_hash,
                    "subtitle_qc_state": "PASS",
                },
            ),
            _verified_effect(
                workflow=workflow,
                stage="RENDER",
                journal={
                    "state": "VERIFIED",
                    "output_relative_path": relative_source,
                    "output_checksum": media_checksum,
                    "measured_render_duration_ms": 543_254,
                },
            ),
            _verified_effect(
                workflow=workflow,
                stage="QC",
                journal={
                    "state": "VERIFIED",
                    "technical_qc_hash": technical_hash,
                    "creative_qc_hash": creative_hash,
                    "cross_modal_qc_hash": cross_modal_hash,
                },
            ),
        ]
    )
    reference = CredentialReference(
        provider_key="google_drive",
        credential_key=f"offline-recovery-{workflow.id}",
        credential_type="OAUTH_TOKEN",
        secret_ref="local_file://offline-never-read.json",
        scope_blob={"scopes": [GOOGLE_DRIVE_SCOPE]},
        status="CONFIGURED",
    )
    db_session.add(reference)
    db_session.flush()
    credential = GoogleDriveMediaCredential(
        company_id=workflow.company_id,
        channel_workspace_id=workflow.channel_workspace_id,
        credential_reference_id=reference.id,
        connection_state="CONNECTED",
        scopes=[GOOGLE_DRIVE_SCOPE],
        root_folder_id="offline-drive-root",
    )
    db_session.add(credential)
    db_session.flush()

    package = ProductionPackageService(db_session).validate_for_readiness(
        workflow.production_package_artifact_version_id
    )
    provider_plan = db_session.get(
        ArtifactVersion,
        package.provider_execution_plan_ref.artifact_version_id,
    )
    assert provider_plan is not None
    operation = provider_plan.content["adapter_operations"]["ARCHIVE"]
    operation_id = operation["operation_id"]
    legacy_media_key = operation["parameters"]["provider_execution"]["idempotency_key"]
    assert len(legacy_media_key.encode("utf-8")) == 109

    coordinator = ProductionWorkflowCoordinator(db_session)
    archive_event = coordinator._schedule_stage(
        workflow, ProductionWorkflowStage.ARCHIVE, max_attempts=1
    )
    db_session.commit()

    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    old_failure = _PreFilePropertyLimitFailure()
    adapter = V2GoogleDriveRemoteArchiveAdapter(
        workspace_root=workspace_root,
        session_factory=factory,
        readiness_gate=_ReadyGate(credential.id),
        upload_service_factory=lambda _session: old_failure,
    )
    gateway = build_v2_provider_production_gateway(
        adapters={"v2-google-drive-remote": adapter}
    )
    worker = ProductionWorkflowWorker(
        session_factory=factory,
        worker_id=f"offline-drive-property-limit-{workflow.id}",
        handlers=None,
        post_readiness_gateway=gateway,
    )
    result = worker.run_exact_event(event_id=archive_event.id)
    assert result.status == "DEAD_LETTERED"
    assert len(old_failure.calls) == 1

    db_session.expire_all()
    workflow = db_session.get(ProductionWorkflowRun, workflow.id)
    archive_event = db_session.get(DomainEvent, archive_event.id)
    archive_ledger = db_session.scalar(
        select(V2ProductionEffectLedger).where(
            V2ProductionEffectLedger.workflow_run_id == workflow.id,
            V2ProductionEffectLedger.stage == "ARCHIVE",
        )
    )
    assert workflow is not None and workflow.state == "BLOCKED"
    assert archive_event is not None
    assert archive_event.last_error_code == "V2_GOOGLE_DRIVE_ARCHIVE_PROVIDER_FAILURE"
    assert archive_event.dead_lettered_at is not None
    assert archive_ledger is not None and archive_ledger.state == "FAILED_UNCERTAIN"
    assert archive_ledger.effect_invocation_count == 1
    dead_letter = db_session.scalar(
        select(DeadLetterJob).where(DeadLetterJob.domain_event_id == archive_event.id)
    )
    assert dead_letter is not None and dead_letter.replay_state == "NOT_REPLAYABLE"
    request_path = (
        workspace_root
        / "effects"
        / __import__("hashlib")
        .sha256(archive_ledger.command_id.encode("utf-8"))
        .hexdigest()
        / "google-drive-archive-request-journal.json"
    )
    assert request_path.is_file()
    assert json.loads(request_path.read_text(encoding="utf-8"))["state"] == "SUBMITTED"

    return (
        _BlockedArchiveScope(
            workflow_id=workflow.id,
            archive_ledger_id=archive_ledger.id,
            archive_event_id=archive_event.id,
            command_id=command_id_for(workflow.id, ProductionWorkflowStage.ARCHIVE),
            operation_id=operation_id,
            legacy_media_key=legacy_media_key,
            workspace_root=workspace_root,
            source_path=source_path,
            caption_path=caption_path,
            media_checksum=media_checksum,
            caption_checksum=caption_checksum,
            credential_id=credential.id,
        ),
        old_failure,
    )


def test_recovery_rejects_every_non_controlled_actor_before_probe_or_upload() -> None:
    class BombProbe:
        def reconcile(self, **_kwargs: object) -> V2DriveArchiveAbsenceProof:
            raise AssertionError("probe must not run for an unauthorized actor")

    def bomb_upload(_session: object) -> object:
        raise AssertionError("upload service must not be created")

    service = V2DriveArchivePropertyLimitRecoveryService(
        object(),  # type: ignore[arg-type]
        reconciliation_probe=BombProbe(),
        upload_service_factory=bomb_upload,
    )
    human = authenticated_actor_context(
        canonical_user_id=uuid.uuid4(),
        operator_user_id=uuid.uuid4(),
        actor_role="company_admin",
        permissions={"*"},
    )

    with pytest.raises(
        ValidationFailureError,
        match="V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RECOVERY_SYSTEM_WORKER_REQUIRED",
    ):
        service.recover(uuid.uuid4(), human)


def test_recovery_contract_accepts_only_the_controlled_worker_identity() -> None:
    controlled = _system_worker_actor(
        "vcos-controlled-recovery", permissions={"production.start"}
    )
    durable = _system_worker_actor(
        "vcos-durable-worker", permissions={"production.start"}
    )

    V2DriveArchivePropertyLimitRecoveryService._require_actor(controlled)
    with pytest.raises(
        ValidationFailureError,
        match="V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RECOVERY_SYSTEM_WORKER_REQUIRED",
    ):
        V2DriveArchivePropertyLimitRecoveryService._require_actor(durable)


def test_absence_proof_can_represent_exact_live_four_lookup_precondition() -> None:
    proof = _proof()

    assert {
        proof.legacy_media.state,
        proof.legacy_caption.state,
        proof.canonical_media.state,
        proof.canonical_caption.state,
    } == {"ABSENT"}
    assert proof.evidence_hash == "a" * 64


@pytest.mark.parametrize(
    (
        "canonical_media",
        "canonical_caption",
        "recovery_journal_exists",
        "expected_action",
    ),
    [
        ("ABSENT", "ABSENT", False, "fresh_pair"),
        ("ABSENT", "ABSENT", True, "block_unknown"),
        ("PRESENT", "ABSENT", True, "caption_only"),
        ("PRESENT", "PRESENT", True, "settle_existing"),
    ],
)
def test_reconciliation_crash_matrix_is_bounded(
    canonical_media: str,
    canonical_caption: str,
    recovery_journal_exists: bool,
    expected_action: str,
) -> None:
    assert (
        _classify_reconciliation(
            _proof(
                canonical_media=canonical_media,
                canonical_caption=canonical_caption,
            ),
            recovery_journal_exists,
        )
        == expected_action
    )


def test_submitted_request_with_both_remote_files_absent_never_resubmits() -> None:
    assert _classify_reconciliation(_proof(), True) == "block_unknown"


@pytest.mark.parametrize(
    ("proof", "journal_exists", "error_code"),
    [
        (
            _proof(canonical_caption="PRESENT"),
            True,
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_CAPTION_WITHOUT_MEDIA",
        ),
        (
            _proof(canonical_media="PRESENT"),
            False,
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REMOTE_WITHOUT_JOURNAL",
        ),
        (
            _proof(legacy_media="PRESENT"),
            False,
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_LEGACY_OBJECT_UNEXPECTED",
        ),
        (
            _proof(canonical_media="AMBIGUOUS"),
            True,
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RECONCILIATION_AMBIGUOUS",
        ),
    ],
)
def test_wrong_or_ambiguous_remote_shapes_fail_closed(
    proof: V2DriveArchiveAbsenceProof,
    journal_exists: bool,
    error_code: str,
) -> None:
    with pytest.raises(ValidationFailureError, match=error_code):
        _classify_reconciliation(proof, journal_exists)


def test_wrong_remote_checksum_fails_closed() -> None:
    present = _proof(canonical_media="PRESENT")
    wrong_media = replace(present.canonical_media, checksum_matches=False)
    wrong = V2DriveArchiveAbsenceProof(
        legacy_media=present.legacy_media,
        legacy_caption=present.legacy_caption,
        canonical_media=wrong_media,
        canonical_caption=present.canonical_caption,
        evidence_hash=present.evidence_hash,
    )

    with pytest.raises(
        ValidationFailureError,
        match="V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REMOTE_PROOF_INVALID",
    ):
        _classify_reconciliation(wrong, True)


def test_live_shaped_old_failure_is_pre_file_and_seals_submitted_journal(
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    assert len(scope.legacy_media_key.encode("utf-8")) == 109
    assert len((scope.legacy_media_key + ".caption").encode("utf-8")) == 117
    assert (
        len(GOOGLE_DRIVE_IDEMPOTENCY_PROPERTY_KEY.encode("utf-8"))
        + len(scope.legacy_media_key.encode("utf-8"))
        == 129
    )
    assert (
        len(GOOGLE_DRIVE_IDEMPOTENCY_PROPERTY_KEY.encode("utf-8"))
        + len((scope.legacy_media_key + ".caption").encode("utf-8"))
        == 137
    )
    assert len(old_failure.calls) == 1
    assert old_failure.calls[0]["media_type"] == "LONG_FORM_FINAL"
    assert old_failure.calls[0]["idempotency_key"] == scope.legacy_media_key
    assert scope.source_path.is_file()
    assert scope.caption_path.is_file()


def test_authority_seals_exact_live_defect_and_replays_without_upload(
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)
    actor = _system_worker_actor(
        "vcos-controlled-recovery", permissions={"production.start"}
    )
    service = V2DriveArchivePropertyLimitRecoveryService(
        db_session,
        reconciliation_probe=boundary,
        upload_service_factory=boundary.upload_service,
        session_factory=factory,
        workspace_root=scope.workspace_root,
    )

    authority = service.authorize(scope.workflow_id, actor)
    replayed = service.authorize(scope.workflow_id, actor)

    assert replayed.id == authority.id
    assert authority.legacy_media_idempotency_key == scope.legacy_media_key
    assert (
        authority.legacy_caption_idempotency_key == scope.legacy_media_key + ".caption"
    )
    assert authority.media_idempotency_key.startswith("sha256:")
    assert authority.caption_idempotency_key.startswith("sha256:")
    assert authority.media_idempotency_key != authority.caption_idempotency_key
    assert (
        len(GOOGLE_DRIVE_IDEMPOTENCY_PROPERTY_KEY.encode("utf-8"))
        + len(authority.media_idempotency_key.encode("utf-8"))
        <= 124
    )
    assert (
        len(GOOGLE_DRIVE_IDEMPOTENCY_PROPERTY_KEY.encode("utf-8"))
        + len(authority.caption_idempotency_key.encode("utf-8"))
        <= 124
    )
    assert authority.max_actual_upload_submissions == 1
    assert authority.absence_reconciliation_evidence["probe_mode"] == "GET_ONLY"
    assert len(boundary.probe_calls) == 1
    assert boundary.upload_file_calls == []
    assert (
        db_session.scalar(
            select(V2DriveArchivePropertyLimitRecoveryAuthority).where(
                V2DriveArchivePropertyLimitRecoveryAuthority.workflow_run_id
                == scope.workflow_id
            )
        ).authority_hash
        == authority.authority_hash
    )


def test_fresh_pair_recovery_verifies_mp4_srt_lineage_and_replays_exactly_once(
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)
    actor = _system_worker_actor(
        "vcos-controlled-recovery", permissions={"production.start"}
    )
    service = V2DriveArchivePropertyLimitRecoveryService(
        db_session,
        reconciliation_probe=boundary,
        upload_service_factory=boundary.upload_service,
        session_factory=factory,
        workspace_root=scope.workspace_root,
    )

    result = service.recover(scope.workflow_id, actor)
    replay = service.recover(scope.workflow_id, actor)

    authority = db_session.get(
        V2DriveArchivePropertyLimitRecoveryAuthority, result.authority_id
    )
    receipt = db_session.get(
        V2DriveArchivePropertyLimitRecoveryReceipt, result.receipt_id
    )
    ledger = db_session.get(V2ProductionEffectLedger, scope.archive_ledger_id)
    workflow = db_session.get(ProductionWorkflowRun, scope.workflow_id)
    command_receipt = db_session.get(
        WorkflowCommandReceipt, result.workflow_command_receipt_id
    )
    next_event = db_session.get(DomainEvent, result.next_domain_event_id)
    old_event = db_session.get(DomainEvent, scope.archive_event_id)
    dead_letter = db_session.scalar(
        select(DeadLetterJob).where(
            DeadLetterJob.domain_event_id == scope.archive_event_id
        )
    )

    assert result.replayed is False
    assert replay.replayed is True
    assert replay.authority_id == result.authority_id
    assert replay.receipt_id == result.receipt_id
    assert replay.workflow_command_receipt_id == result.workflow_command_receipt_id
    assert replay.next_domain_event_id == result.next_domain_event_id
    assert authority is not None and receipt is not None
    assert boundary.upload_file_calls == [
        authority.media_idempotency_key,
        authority.caption_idempotency_key,
    ]
    assert len(set(boundary.upload_file_calls)) == 2
    assert boundary.upload_verified_calls == boundary.upload_file_calls
    assert len(boundary.probe_calls) == 2
    assert receipt.actual_upload_submissions == 1
    assert receipt.provider_file_count == 2
    assert receipt.checksum_verified_file_count == 2
    assert receipt.media_checksum_sha256 == scope.media_checksum
    assert receipt.caption_checksum_sha256 == scope.caption_checksum
    assert receipt.automatic_publish is False

    assert ledger is not None and ledger.state == "VERIFIED"
    assert ledger.effect_invocation_count == 1
    assert ledger.result_hash == receipt.archive_receipt_hash
    assert ledger.effect_journal["provider_call_count"] == 1
    assert ledger.effect_journal["provider_file_count"] == 2
    assert command_receipt is not None
    assert command_receipt.domain_event_id == scope.archive_event_id
    assert command_receipt.command_id == scope.command_id
    assert command_receipt.effect_state == "RECONCILED"
    assert command_receipt.result_hash == receipt.archive_receipt_hash

    assert workflow is not None
    assert result.workflow_state == workflow.state == "ARCHIVE_PENDING"
    assert workflow.current_stage == "FINALIZE"
    assert workflow.archive_receipt_hash == receipt.archive_receipt_hash
    assert workflow.final_media_ref_id == receipt.final_media_ref_id
    assert next_event is not None
    assert next_event.command_id == command_id_for(
        scope.workflow_id, ProductionWorkflowStage.FINALIZE
    )
    assert next_event.payload["stage"] == "FINALIZE"
    assert next_event.dead_lettered_at is None

    media_cloud = db_session.get(CloudMediaRef, receipt.media_cloud_media_ref_id)
    caption_cloud = db_session.get(CloudMediaRef, receipt.caption_cloud_media_ref_id)
    final_media = db_session.get(FinalMediaRef, receipt.final_media_ref_id)
    assert media_cloud is not None and caption_cloud is not None
    assert final_media is not None
    assert media_cloud.checksum_sha256 == final_media.checksum_sha256
    assert media_cloud.checksum_sha256 == scope.media_checksum
    assert caption_cloud.checksum_sha256 == scope.caption_checksum
    assert media_cloud.drive_file_id == receipt.media_drive_file_id
    assert caption_cloud.drive_file_id == receipt.caption_drive_file_id
    assert final_media.cloud_media_ref_id == media_cloud.id

    # The historical dead letter remains immutable evidence; recovery appends a
    # receipt and the exact FINALIZE command without publishing anything.
    assert old_event is not None
    assert old_event.last_error_code == "V2_GOOGLE_DRIVE_ARCHIVE_PROVIDER_FAILURE"
    assert old_event.dead_lettered_at is not None
    assert dead_letter is not None and dead_letter.replay_state == "NOT_REPLAYABLE"
    assert (
        db_session.scalar(
            select(PublishHandoffPackage.id).where(
                PublishHandoffPackage.video_project_id == workflow.video_project_id
            )
        )
        is None
    )


def test_public_scoped_runner_recognizes_only_exact_drive_recovery_candidate(
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    runner = ScopedReplacementContinuationRunner(
        db_session, now=lambda: datetime.now(UTC)
    )

    assert runner._is_drive_archive_recovery_candidate(scope.workflow_id) is True

    event = db_session.get(DomainEvent, scope.archive_event_id)
    assert event is not None
    event.last_error_code = "SOME_OTHER_UNCERTAIN_PROVIDER_FAILURE"
    db_session.flush()
    assert runner._is_drive_archive_recovery_candidate(scope.workflow_id) is False


def test_request_journal_with_both_files_absent_blocks_without_resubmission(
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)
    factory_calls = 0

    def crash_factory(_session: Any) -> Any:
        nonlocal factory_calls
        factory_calls += 1
        raise _SimulatedCrash("crash after durable request, before provider boundary")

    service = _recovery_service(
        db_session=db_session,
        engine=engine,
        scope=scope,
        boundary=boundary,
        upload_service_factory=crash_factory,
    )
    with pytest.raises(_SimulatedCrash):
        service.recover(scope.workflow_id, _controlled_recovery_actor())
    db_session.rollback()
    request_path, response_path = _recovery_paths(scope)
    assert request_path.is_file()
    assert not response_path.exists()
    assert factory_calls == 1
    assert boundary.remote == {}

    with pytest.raises(
        ValidationFailureError,
        match="V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RECOVERY_OUTCOME_UNKNOWN",
    ):
        service.recover(scope.workflow_id, _controlled_recovery_actor())
    db_session.rollback()

    assert factory_calls == 1
    assert boundary.upload_file_calls == []
    assert boundary.upload_verified_calls == []
    workflow = db_session.get(ProductionWorkflowRun, scope.workflow_id)
    ledger = db_session.get(V2ProductionEffectLedger, scope.archive_ledger_id)
    assert workflow is not None and workflow.state == "BLOCKED"
    assert workflow.current_stage == "ARCHIVE"
    assert ledger is not None and ledger.state == "FAILED_UNCERTAIN"
    assert ledger.effect_invocation_count == 1


@pytest.mark.parametrize(
    ("crash_point", "expected_resume_uploads", "expected_action"),
    [
        ("before_caption", 1, "caption_only"),
        ("after_caption", 0, "settle_existing"),
    ],
)
def test_crash_matrix_resumes_only_missing_exact_file_without_duplicates(
    crash_point: str,
    expected_resume_uploads: int,
    expected_action: str,
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)

    class CrashUploadService:
        def __init__(self, session: Any) -> None:
            self.base = _FakeDriveUploadService(session=session, boundary=boundary)

        def upload_verified(self, **kwargs: Any) -> tuple[CloudMediaRef, Any]:
            if kwargs["media_type"] == "CAPTION" and crash_point == "before_caption":
                raise _SimulatedCrash("crash before caption file submission")
            result = self.base.upload_verified(**kwargs)
            if kwargs["media_type"] == "CAPTION" and crash_point == "after_caption":
                raise _SimulatedCrash("crash after caption file submission")
            return result

    first = _recovery_service(
        db_session=db_session,
        engine=engine,
        scope=scope,
        boundary=boundary,
        upload_service_factory=CrashUploadService,
    )
    with pytest.raises(_SimulatedCrash):
        first.recover(scope.workflow_id, _controlled_recovery_actor())
    db_session.rollback()

    authority = db_session.scalar(
        select(V2DriveArchivePropertyLimitRecoveryAuthority).where(
            V2DriveArchivePropertyLimitRecoveryAuthority.workflow_run_id
            == scope.workflow_id
        )
    )
    assert authority is not None
    assert boundary.upload_file_calls[0] == authority.media_idempotency_key
    assert len(boundary.upload_file_calls) == (
        1 if crash_point == "before_caption" else 2
    )
    uploads_before_resume = len(boundary.upload_file_calls)

    resumed = _recovery_service(
        db_session=db_session,
        engine=engine,
        scope=scope,
        boundary=boundary,
    )
    result = resumed.recover(scope.workflow_id, _controlled_recovery_actor())

    assert result.replayed is False
    assert (
        len(boundary.upload_file_calls) - uploads_before_resume
        == expected_resume_uploads
    )
    assert boundary.upload_file_calls.count(authority.media_idempotency_key) == 1
    assert boundary.upload_file_calls.count(authority.caption_idempotency_key) == 1
    request_path, response_path = _recovery_paths(scope)
    assert request_path.is_file() and response_path.is_file()
    response = json.loads(response_path.read_text(encoding="utf-8"))
    assert response["reconciliation_action"] == expected_action
    assert response["provider_upload_file_call_count"] == expected_resume_uploads
    assert response["media_checksum_sha256"] == scope.media_checksum
    assert response["caption_checksum_sha256"] == scope.caption_checksum


def test_post_artifact_commit_crash_replays_without_duplicate_rows_or_uploads(
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)
    service = _recovery_service(
        db_session=db_session,
        engine=engine,
        scope=scope,
        boundary=boundary,
    )
    real_persist = recovery_module._persist_exact_json

    def crash_before_response(_path: Path, _payload: dict[str, Any]) -> None:
        raise _SimulatedCrash("crash after durable Cloud/Final lineage")

    monkeypatch.setattr(recovery_module, "_persist_exact_json", crash_before_response)
    with pytest.raises(_SimulatedCrash):
        service.recover(scope.workflow_id, _controlled_recovery_actor())
    db_session.rollback()
    monkeypatch.setattr(recovery_module, "_persist_exact_json", real_persist)

    workflow = db_session.get(ProductionWorkflowRun, scope.workflow_id)
    assert workflow is not None
    cloud_ids = set(
        db_session.scalars(
            select(CloudMediaRef.id).where(
                CloudMediaRef.video_project_id == workflow.video_project_id,
                CloudMediaRef.storage_provider == "GOOGLE_DRIVE",
            )
        )
    )
    final_ids = set(
        db_session.scalars(
            select(FinalMediaRef.id).where(
                FinalMediaRef.video_project_id == workflow.video_project_id,
                FinalMediaRef.provider_key == "v2-google-drive-remote",
            )
        )
    )
    uploads_before_resume = list(boundary.upload_file_calls)
    assert len(cloud_ids) == 2
    assert len(final_ids) == 1
    assert len(uploads_before_resume) == 2
    _request_path, response_path = _recovery_paths(scope)
    assert not response_path.exists()

    result = service.recover(scope.workflow_id, _controlled_recovery_actor())

    assert result.replayed is False
    assert boundary.upload_file_calls == uploads_before_resume
    assert (
        set(
            db_session.scalars(
                select(CloudMediaRef.id).where(
                    CloudMediaRef.video_project_id == workflow.video_project_id,
                    CloudMediaRef.storage_provider == "GOOGLE_DRIVE",
                )
            )
        )
        == cloud_ids
    )
    assert (
        set(
            db_session.scalars(
                select(FinalMediaRef.id).where(
                    FinalMediaRef.video_project_id == workflow.video_project_id,
                    FinalMediaRef.provider_key == "v2-google-drive-remote",
                )
            )
        )
        == final_ids
    )


def test_crash_after_ledger_flush_cannot_commit_without_receipt_then_replays(
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)
    service = _recovery_service(
        db_session=db_session,
        engine=engine,
        scope=scope,
        boundary=boundary,
    )
    real_add = db_session.add

    def crash_on_recovery_receipt(instance: Any, *args: Any, **kwargs: Any) -> None:
        if isinstance(instance, V2DriveArchivePropertyLimitRecoveryReceipt):
            raise _SimulatedCrash("crash after ledger flush, before recovery receipt")
        real_add(instance, *args, **kwargs)

    monkeypatch.setattr(db_session, "add", crash_on_recovery_receipt)
    with pytest.raises(_SimulatedCrash):
        service.recover(scope.workflow_id, _controlled_recovery_actor())
    monkeypatch.setattr(db_session, "add", real_add)

    ledger = db_session.get(V2ProductionEffectLedger, scope.archive_ledger_id)
    assert ledger is not None and ledger.state == "VERIFIED"
    with pytest.raises(
        Exception,
        match="V2 Drive archive recovery verification requires its sealed receipt",
    ):
        db_session.commit()
    db_session.rollback()
    ledger = db_session.get(V2ProductionEffectLedger, scope.archive_ledger_id)
    assert ledger is not None and ledger.state == "FAILED_UNCERTAIN"
    uploads_before_resume = list(boundary.upload_file_calls)
    request_path, response_path = _recovery_paths(scope)
    response_before = response_path.read_bytes()
    assert request_path.is_file() and response_before

    result = service.recover(scope.workflow_id, _controlled_recovery_actor())

    assert result.replayed is False
    assert boundary.upload_file_calls == uploads_before_resume
    assert response_path.read_bytes() == response_before
    assert (
        db_session.scalar(
            select(V2DriveArchivePropertyLimitRecoveryReceipt.id).where(
                V2DriveArchivePropertyLimitRecoveryReceipt.workflow_run_id
                == scope.workflow_id
            )
        )
        == result.receipt_id
    )


def test_advisory_lock_rejects_concurrent_recovery_without_probe_or_upload(
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)
    service = _recovery_service(
        db_session=db_session,
        engine=engine,
        scope=scope,
        boundary=boundary,
    )
    lock_sql = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 7702))")
    unlock_sql = text("SELECT pg_advisory_unlock(hashtextextended(:key, 7702))")

    with engine.connect() as owner:
        assert owner.scalar(lock_sql, {"key": str(scope.workflow_id)}) is True
        try:
            with pytest.raises(
                ValidationFailureError,
                match="V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RECOVERY_ALREADY_RUNNING",
            ):
                service.recover(scope.workflow_id, _controlled_recovery_actor())
        finally:
            assert owner.scalar(unlock_sql, {"key": str(scope.workflow_id)}) is True

    assert boundary.probe_calls == []
    assert boundary.upload_file_calls == []
    assert (
        db_session.scalar(
            select(V2DriveArchivePropertyLimitRecoveryAuthority.id).where(
                V2DriveArchivePropertyLimitRecoveryAuthority.workflow_run_id
                == scope.workflow_id
            )
        )
        is None
    )


@pytest.mark.parametrize("invalid_shape", ["missing", "null"])
def test_db_authority_seal_rejects_missing_or_null_absence_proof_rows(
    invalid_shape: str,
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)

    class InvalidEvidenceProbe:
        def reconcile(self, **kwargs: Any) -> V2DriveArchiveAbsenceProof:
            proof = boundary.reconcile(**kwargs)
            evidence = dict(proof.evidence or {})
            if invalid_shape == "missing":
                evidence.pop("canonical_caption")
            else:
                evidence["canonical_caption"] = None
            return replace(
                proof,
                evidence=evidence,
                evidence_hash=content_hash(evidence),
            )

    service = _recovery_service(
        db_session=db_session,
        engine=engine,
        scope=scope,
        boundary=boundary,
    )
    service.reconciliation_probe = InvalidEvidenceProbe()

    with pytest.raises(
        Exception, match="V2 Drive archive recovery authority seal mismatch"
    ):
        service.authorize(scope.workflow_id, _controlled_recovery_actor())
    db_session.rollback()

    assert boundary.upload_file_calls == []
    assert (
        db_session.scalar(
            select(V2DriveArchivePropertyLimitRecoveryAuthority.id).where(
                V2DriveArchivePropertyLimitRecoveryAuthority.workflow_run_id
                == scope.workflow_id
            )
        )
        is None
    )


def test_db_authority_rejects_canonical_property_value_at_125_total_bytes(
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)

    def invalid_compaction(raw: str) -> str:
        return ("c" if raw.endswith(".caption") else "m") * 105

    assert (
        len("vcos_idempotency_key".encode()) + len(invalid_compaction("media")) == 125
    )
    monkeypatch.setattr(
        recovery_module, "_drive_idempotency_property_value", invalid_compaction
    )
    service = _recovery_service(
        db_session=db_session,
        engine=engine,
        scope=scope,
        boundary=boundary,
    )

    with pytest.raises(Exception, match="violates check constraint"):
        service.authorize(scope.workflow_id, _controlled_recovery_actor())
    db_session.rollback()

    assert boundary.upload_file_calls == []


@pytest.mark.parametrize(
    ("remote_shape", "error_code"),
    [
        (
            "ambiguous",
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_RECONCILIATION_AMBIGUOUS",
        ),
        ("wrong_checksum", "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REMOTE_PROOF_INVALID"),
    ],
)
def test_public_recovery_blocks_ambiguous_or_wrong_checksum_remote_object(
    remote_shape: str,
    error_code: str,
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)
    canonical_key = recovery_module._drive_idempotency_property_value(
        scope.legacy_media_key
    )
    if remote_shape == "ambiguous":
        boundary.ambiguous_keys.add(canonical_key)
    else:
        boundary.remote[canonical_key] = _RemoteObject(
            drive_file_id="offline-wrong-checksum",
            checksum="f" * 64,
            size_bytes=scope.source_path.stat().st_size,
        )
    service = _recovery_service(
        db_session=db_session,
        engine=engine,
        scope=scope,
        boundary=boundary,
    )

    with pytest.raises(ValidationFailureError, match=error_code):
        service.recover(scope.workflow_id, _controlled_recovery_actor())
    db_session.rollback()

    assert boundary.upload_file_calls == []
    assert (
        db_session.scalar(
            select(V2DriveArchivePropertyLimitRecoveryAuthority.id).where(
                V2DriveArchivePropertyLimitRecoveryAuthority.workflow_run_id
                == scope.workflow_id
            )
        )
        is None
    )


def test_receipt_seals_cloud_final_event_and_dead_letter_rows_against_tamper(
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)
    service = _recovery_service(
        db_session=db_session,
        engine=engine,
        scope=scope,
        boundary=boundary,
    )
    result = service.recover(scope.workflow_id, _controlled_recovery_actor())
    receipt = db_session.get(
        V2DriveArchivePropertyLimitRecoveryReceipt, result.receipt_id
    )
    assert receipt is not None
    media_cloud_id = receipt.media_cloud_media_ref_id
    final_media_id = receipt.final_media_ref_id
    dead_letter_id = db_session.scalar(
        select(DeadLetterJob.id).where(
            DeadLetterJob.domain_event_id == scope.archive_event_id
        )
    )
    assert dead_letter_id is not None

    media_cloud = db_session.get(CloudMediaRef, media_cloud_id)
    assert media_cloud is not None
    media_cloud.file_name = "tampered.mp4"
    with pytest.raises(Exception, match="recovery cloud media is immutable"):
        db_session.flush()
    db_session.rollback()

    final_media = db_session.get(FinalMediaRef, final_media_id)
    assert final_media is not None
    final_media.file_ref = "drive://tampered/final.mp4"
    with pytest.raises(Exception, match="recovery final media is immutable"):
        db_session.flush()
    db_session.rollback()

    old_event = db_session.get(DomainEvent, scope.archive_event_id)
    assert old_event is not None
    old_event.last_error_code = "TAMPERED"
    with pytest.raises(Exception, match="archive dead-letter event is immutable"):
        db_session.flush()
    db_session.rollback()

    dead_letter = db_session.get(DeadLetterJob, dead_letter_id)
    assert dead_letter is not None
    dead_letter.replay_state = "REPLAYABLE"
    with pytest.raises(Exception, match="archive dead letter is immutable"):
        db_session.flush()
    db_session.rollback()


@pytest.mark.parametrize(
    ("journal_name", "error_code"),
    [
        (
            "request",
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_REQUEST_INVALID",
        ),
        (
            "response",
            "V2_DRIVE_ARCHIVE_PROPERTY_LIMIT_REPLAY_RESPONSE_INVALID",
        ),
    ],
)
def test_public_replay_revalidates_exact_hashed_journals_before_returning(
    journal_name: str,
    error_code: str,
    db_session: Any,
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope, _old_failure = _seed_blocked_archive_scope(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    boundary = _FakeDriveBoundary(credential_id=scope.credential_id)
    service = _recovery_service(
        db_session=db_session,
        engine=engine,
        scope=scope,
        boundary=boundary,
    )
    initial = service.recover(scope.workflow_id, _controlled_recovery_actor())
    request_path, response_path = _recovery_paths(scope)
    target = request_path if journal_name == "request" else response_path
    document = json.loads(target.read_text(encoding="utf-8"))
    document["automatic_publish"] = True
    target.write_text(json.dumps(document), encoding="utf-8")
    uploads_before_replay = list(boundary.upload_file_calls)
    probes_before_replay = len(boundary.probe_calls)

    with pytest.raises(ValidationFailureError, match=error_code):
        service.recover(scope.workflow_id, _controlled_recovery_actor())

    assert initial.replayed is False
    assert boundary.upload_file_calls == uploads_before_replay
    assert len(boundary.probe_calls) == probes_before_replay
