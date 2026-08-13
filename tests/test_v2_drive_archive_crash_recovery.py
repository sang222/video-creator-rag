from __future__ import annotations

import hashlib
import json
import mimetypes
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts.production_workflow import WorkflowFailureClassification
from app.contracts.production_workflow import ProductionWorkflowStage
from app.core.errors import ValidationFailureError
from app.services import production_workflow, v2_drive_archive
from app.services.m10_5 import (
    DriveArchivePathBuilder,
    GoogleDriveUploadResult,
    GoogleDriveUploadVerifier,
    GoogleDriveVerificationResult,
)
from app.services.production_workflow import WorkflowStageError
from app.services.v2_drive_archive import (
    V2GoogleDriveRemoteArchiveAdapter,
    _media_workflow_run_id_for_ai_visual_archive,
    _remote_archive_request_identity,
    has_exact_drive_archive_get_only_reconciliation_authority,
)


class _FakeConfig:
    def offload_enabled(self) -> bool:
        return True

    def root_folder_id(self) -> str:
        return "configured-root"


class _FakeCredentials:
    def get_connected_reference(self, **kwargs):
        assert kwargs["company_id"] is not None
        assert kwargs["channel_workspace_id"] is not None
        return SimpleNamespace(id=uuid.uuid4())

    def get_valid_access_token(self, reference):
        assert reference is not None
        return "read-only-token"


class _FakeDriveProvider:
    def __init__(self) -> None:
        self.folders: dict[tuple[str, ...], str] = {}
        self.files: dict[tuple[str, str], GoogleDriveUploadResult] = {}
        self.metadata: dict[str, GoogleDriveUploadResult] = {}
        self.ambiguous_keys: set[str] = set()
        self.get_calls: list[str] = []
        self.upload_file_calls = 0

    def seed(
        self,
        *,
        folder_path: list[str],
        idempotency_key: str,
        local_path: Path,
        checksum: str,
    ) -> GoogleDriveUploadResult:
        folder_tuple = tuple(folder_path)
        folder_id = self.folders.setdefault(
            folder_tuple, f"folder-{len(self.folders) + 1}"
        )
        drive_file_id = f"file-{len(self.metadata) + 1}"
        result = GoogleDriveUploadResult(
            drive_file_id=drive_file_id,
            drive_folder_id=folder_id,
            web_view_link=f"https://drive.google.com/file/d/{drive_file_id}/view",
            file_name=local_path.name,
            mime_type=mimetypes.guess_type(local_path.name)[0],
            size_bytes=local_path.stat().st_size,
            checksum_sha256=checksum,
            upload_mode="resumable",
        )
        self.files[(folder_id, idempotency_key)] = result
        self.metadata[drive_file_id] = result
        return result

    def find_folder_path(self, **kwargs):
        assert kwargs["access_token"] == "read-only-token"
        assert kwargs["root_folder_id"] == "configured-root"
        self.get_calls.append("find_folder_path")
        return self.folders.get(tuple(kwargs["folder_path"]))

    def find_file_by_idempotency_key(self, **kwargs):
        self.get_calls.append("find_file_by_idempotency_key")
        key = kwargs["idempotency_key"]
        if key in self.ambiguous_keys:
            raise ValidationFailureError("Google Drive idempotency key is ambiguous")
        return self.files.get((kwargs["folder_id"], key))

    def get_file_metadata(self, **kwargs):
        self.get_calls.append("get_file_metadata")
        return self.metadata[kwargs["drive_file_id"]]

    def readback_sha256(self, **kwargs):
        self.get_calls.append("readback_sha256")
        return self.metadata[kwargs["drive_file_id"]].checksum_sha256

    def upload_file(self, **_kwargs):
        self.upload_file_calls += 1
        raise AssertionError("GET-only reconciliation attempted a provider write")


class _FakeUploadService:
    def __init__(self, provider: _FakeDriveProvider) -> None:
        self.config_service = _FakeConfig()
        self.credential_service = _FakeCredentials()
        self.provider = provider
        self.verifier = GoogleDriveUploadVerifier()
        self.archive_path_builder = DriveArchivePathBuilder()
        self.upload_verified_calls = 0

    def upload_verified(self, **kwargs):
        self.upload_verified_calls += 1
        path = self.archive_path_builder.build(
            company_id=kwargs["company_id"],
            channel_workspace_id=kwargs["channel_workspace_id"],
            video_project_id=kwargs["video_project_id"],
            uploaded_video_id=kwargs["uploaded_video_id"],
            media_type=kwargs["media_type"],
        )
        checksum = hashlib.sha256(kwargs["local_path"].read_bytes()).hexdigest()
        self.provider.seed(
            folder_path=path.folder_path,
            idempotency_key=kwargs["idempotency_key"],
            local_path=kwargs["local_path"],
            checksum=checksum,
        )
        cloud = SimpleNamespace(technical_appendix={})
        verification = GoogleDriveVerificationResult(
            ok=True,
            verification_status="CHECKSUM_VERIFIED",
            reason_code="MEDIA_OFFLOAD_UPLOAD_VERIFIED",
            size_verified=True,
            checksum_verified=True,
            checksum_unavailable=False,
        )
        return cloud, verification


class _FakeSession:
    def __init__(self, rows: list[object] | None = None) -> None:
        self.flush_calls = 0
        self.commit_calls = 0
        self.added: list[object] = []
        self.rows = rows or []

    def scalars(self, _statement):
        return list(self.rows)

    def add(self, value) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        self.commit_calls += 1


def _fixture(tmp_path: Path):
    source = tmp_path / "render" / "final.mp4"
    source.parent.mkdir()
    source.write_bytes(b"exact final render")
    caption = tmp_path / "caption" / "final.srt"
    caption.parent.mkdir()
    caption.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nExact caption.\n",
        encoding="utf-8",
    )
    source_checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    caption_checksum = hashlib.sha256(caption.read_bytes()).hexdigest()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        channel_workspace_id=uuid.uuid4(),
        video_project_id=uuid.uuid4(),
        render_output_ref="v2-ai-visual-render://exact",
        render_output_checksum=source_checksum,
        production_package_artifact_version_id=uuid.uuid4(),
        production_package_hash="a" * 64,
    )
    command_id = f"archive:{uuid.uuid4()}"
    operation_id = f"v2:{run.id}:archive"
    idempotency_key = f"{operation_id}:google-drive-archive"
    operation = SimpleNamespace(
        operation_id=operation_id,
        parameters={
            "provider_execution": {
                "idempotency_key": idempotency_key,
            }
        },
    )
    sidecar = {
        "caption_relative_path": caption.relative_to(tmp_path).as_posix(),
        "caption_checksum": caption_checksum,
        "caption_ref": f"artifact-version://{uuid.uuid4()}",
        "caption_artifact_hash": "b" * 64,
        "subtitle_qc_ref": f"artifact-version://{uuid.uuid4()}",
        "subtitle_qc_hash": "c" * 64,
    }
    session = _FakeSession()
    context = SimpleNamespace(
        session=session,
        run=run,
        command_id=command_id,
        event=SimpleNamespace(attempt_count=1),
    )
    adapter = object.__new__(V2GoogleDriveRemoteArchiveAdapter)
    adapter.root = tmp_path.resolve()
    provider = _FakeDriveProvider()
    service = _FakeUploadService(provider)
    adapter._upload_service_factory = lambda _session: service
    return SimpleNamespace(
        source=source,
        source_checksum=source_checksum,
        caption=caption,
        caption_checksum=caption_checksum,
        run=run,
        command_id=command_id,
        idempotency_key=idempotency_key,
        operation=operation,
        sidecar=sidecar,
        session=session,
        context=context,
        adapter=adapter,
        provider=provider,
        service=service,
    )


def _artifact_required() -> WorkflowStageError:
    return WorkflowStageError(
        classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
        error_code="V2_DRIVE_ARCHIVE_ARTIFACT_REQUIRED",
        summary="missing",
        incident_type="ARCHIVE_VERIFICATION_BLOCK",
        retry_eligible=False,
    )


def test_normal_ai_visual_archive_uses_its_own_media_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import ai_visual_rerender_authority

    workflow_id = uuid.uuid4()
    project_id = uuid.uuid4()
    visual_run_id = uuid.uuid4()
    run = SimpleNamespace(
        id=workflow_id,
        video_project_id=project_id,
        ai_visual_production_run_id=visual_run_id,
    )
    visual_run = SimpleNamespace(
        id=visual_run_id,
        workflow_run_id=workflow_id,
        video_project_id=project_id,
        execution_kind="NORMAL_PRODUCTION",
        rerender_authority_id=None,
    )
    session = SimpleNamespace(
        get=lambda _model, identity: (
            visual_run
            if identity == visual_run_id
            else pytest.fail("unexpected row lookup")
        )
    )

    monkeypatch.setattr(
        ai_visual_rerender_authority,
        "resolve_governed_ai_visual_rerender_execution_authority",
        lambda *_args, **_kwargs: pytest.fail(
            "normal archive consulted governed rerender authority"
        ),
    )

    assert (
        _media_workflow_run_id_for_ai_visual_archive(
            session=session,
            run=run,
        )
        == workflow_id
    )


def test_governed_ai_visual_archive_preserves_source_media_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import ai_visual_rerender_authority

    workflow_id = uuid.uuid4()
    source_workflow_id = uuid.uuid4()
    project_id = uuid.uuid4()
    visual_run_id = uuid.uuid4()
    run = SimpleNamespace(
        id=workflow_id,
        video_project_id=project_id,
        ai_visual_production_run_id=visual_run_id,
    )
    visual_run = SimpleNamespace(
        id=visual_run_id,
        workflow_run_id=workflow_id,
        video_project_id=project_id,
        execution_kind="GOVERNED_RERENDER",
        rerender_authority_id=uuid.uuid4(),
    )
    session = SimpleNamespace(
        get=lambda _model, identity: (
            visual_run
            if identity == visual_run_id
            else pytest.fail("unexpected row lookup")
        )
    )
    governed = SimpleNamespace(
        visual_run=visual_run,
        replacement_workflow=run,
        source_workflow=SimpleNamespace(id=source_workflow_id),
    )

    def resolve(_session, *, workflow_run_id, required):
        assert _session is session
        assert workflow_run_id == workflow_id
        assert required is True
        return governed

    monkeypatch.setattr(
        ai_visual_rerender_authority,
        "resolve_governed_ai_visual_rerender_execution_authority",
        resolve,
    )

    assert (
        _media_workflow_run_id_for_ai_visual_archive(
            session=session,
            run=run,
        )
        == source_workflow_id
    )


@pytest.mark.parametrize(
    ("execution_kind", "rerender_authority_id"),
    [
        ("NORMAL_PRODUCTION", uuid.uuid4()),
        ("GOVERNED_RERENDER", None),
        ("UNKNOWN", None),
    ],
)
def test_ai_visual_archive_rejects_incoherent_execution_kind(
    monkeypatch: pytest.MonkeyPatch,
    execution_kind: str,
    rerender_authority_id: uuid.UUID | None,
) -> None:
    from app.services import ai_visual_rerender_authority

    workflow_id = uuid.uuid4()
    project_id = uuid.uuid4()
    visual_run_id = uuid.uuid4()
    run = SimpleNamespace(
        id=workflow_id,
        video_project_id=project_id,
        ai_visual_production_run_id=visual_run_id,
    )
    visual_run = SimpleNamespace(
        id=visual_run_id,
        workflow_run_id=workflow_id,
        video_project_id=project_id,
        execution_kind=execution_kind,
        rerender_authority_id=rerender_authority_id,
    )
    session = SimpleNamespace(get=lambda _model, _identity: visual_run)

    monkeypatch.setattr(
        ai_visual_rerender_authority,
        "resolve_governed_ai_visual_rerender_execution_authority",
        lambda *_args, **_kwargs: pytest.fail(
            "incoherent archive consulted governed rerender authority"
        ),
    )

    with pytest.raises(
        ValidationFailureError,
        match="V2_GOOGLE_DRIVE_REMOTE_AI_VISUAL_AUTHORITY_REQUIRED",
    ):
        _media_workflow_run_id_for_ai_visual_archive(
            session=session,
            run=run,
        )


def _write_request(case) -> Path:
    identity = _remote_archive_request_identity(
        command_id=case.command_id,
        operation_id=case.operation.operation_id,
        idempotency_key=case.idempotency_key,
        source_relative_path=case.source.relative_to(case.adapter.root).as_posix(),
        source_checksum=case.source_checksum,
        source_size_bytes=case.source.stat().st_size,
        measured_render_duration_ms=1_000,
        caption_relative_path=case.caption.relative_to(case.adapter.root).as_posix(),
        sidecar=case.sidecar,
    )
    path = (
        case.adapter._effect_dir(case.command_id)
        / "google-drive-archive-request-journal.json"
    )
    path.write_text(json.dumps({**identity, "state": "SUBMITTED"}), encoding="utf-8")
    return path


def _seed_remote_pair(case, *, caption_checksum: str | None = None) -> None:
    builder = case.service.archive_path_builder
    for local_path, media_type, key, checksum in (
        (
            case.source,
            "LONG_FORM_FINAL",
            case.idempotency_key,
            case.source_checksum,
        ),
        (
            case.caption,
            "CAPTION",
            case.idempotency_key + ".caption",
            caption_checksum or case.caption_checksum,
        ),
    ):
        path = builder.build(
            company_id=case.run.company_id,
            channel_workspace_id=case.run.channel_workspace_id,
            video_project_id=case.run.video_project_id,
            uploaded_video_id=None,
            media_type=media_type,
        )
        case.provider.seed(
            folder_path=path.folder_path,
            idempotency_key=key,
            local_path=local_path,
            checksum=checksum,
        )


def test_retry_after_both_remote_uploads_uses_zero_second_upload_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    case = _fixture(tmp_path)
    artifact = object()
    resolution_calls = 0

    class InjectedProcessCrash(BaseException):
        pass

    def resolve(**_kwargs):
        nonlocal resolution_calls
        resolution_calls += 1
        if resolution_calls in {1, 3}:
            raise _artifact_required()
        if resolution_calls == 2:
            raise InjectedProcessCrash
        return artifact

    monkeypatch.setattr(
        v2_drive_archive, "_resolve_or_create_v2_drive_archive", resolve
    )

    with pytest.raises(InjectedProcessCrash):
        case.adapter._resolve_existing_or_upload(
            context=case.context,
            operation=case.operation,
            source=case.source,
            checksum=case.source_checksum,
            measured_duration_ms=1_000,
            caption_source=case.caption,
            sidecar=case.sidecar,
        )
    assert case.service.upload_verified_calls == 2
    request = (
        case.adapter._effect_dir(case.command_id)
        / "google-drive-archive-request-journal.json"
    )
    assert request.is_file()
    assert json.loads(request.read_text(encoding="utf-8"))["state"] == "SUBMITTED"

    uploads_before_retry = case.service.upload_verified_calls
    case.context.event.attempt_count = 2
    resolved = case.adapter._resolve_existing_or_upload(
        context=case.context,
        operation=case.operation,
        source=case.source,
        checksum=case.source_checksum,
        measured_duration_ms=1_000,
        caption_source=case.caption,
        sidecar=case.sidecar,
    )

    assert resolved is artifact
    assert case.service.upload_verified_calls == uploads_before_retry
    assert case.provider.upload_file_calls == 0
    assert [row.media_type for row in case.session.added] == [
        "LONG_FORM_FINAL",
        "CAPTION",
    ]
    assert case.session.commit_calls == 1
    receipt_path = (
        case.adapter._effect_dir(case.command_id)
        / "google-drive-archive-get-only-reconciliation.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["reconciliation_mode"] == "GET_ONLY"
    assert receipt["provider_write_count"] == 0
    assert receipt["media"]["drive_file_id"] != receipt["caption"]["drive_file_id"]


@pytest.mark.parametrize("failure_mode", ["partial", "ambiguous", "wrong_checksum"])
def test_prior_request_remote_negative_proofs_never_write_or_materialize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_mode: str,
) -> None:
    case = _fixture(tmp_path)
    _write_request(case)
    if failure_mode == "partial":
        builder = case.service.archive_path_builder
        path = builder.build(
            company_id=case.run.company_id,
            channel_workspace_id=case.run.channel_workspace_id,
            video_project_id=case.run.video_project_id,
            uploaded_video_id=None,
            media_type="LONG_FORM_FINAL",
        )
        case.provider.seed(
            folder_path=path.folder_path,
            idempotency_key=case.idempotency_key,
            local_path=case.source,
            checksum=case.source_checksum,
        )
    else:
        _seed_remote_pair(
            case,
            caption_checksum=("d" * 64 if failure_mode == "wrong_checksum" else None),
        )
        if failure_mode == "ambiguous":
            case.provider.ambiguous_keys.add(case.idempotency_key)

    monkeypatch.setattr(
        v2_drive_archive,
        "_resolve_or_create_v2_drive_archive",
        lambda **_kwargs: (_ for _ in ()).throw(_artifact_required()),
    )
    monkeypatch.setattr(
        v2_drive_archive,
        "_materialize_reconciled_drive_cloud_ref",
        lambda **_kwargs: pytest.fail("negative proof materialized a DB authority"),
    )

    with pytest.raises(WorkflowStageError) as caught:
        case.adapter._resolve_existing_or_upload(
            context=case.context,
            operation=case.operation,
            source=case.source,
            checksum=case.source_checksum,
            measured_duration_ms=1_000,
            caption_source=case.caption,
            sidecar=case.sidecar,
        )

    assert caught.value.error_code == "V2_GOOGLE_DRIVE_OUTCOME_UNCERTAIN"
    assert case.service.upload_verified_calls == 0
    assert case.provider.upload_file_calls == 0
    assert case.session.flush_calls == 0
    assert case.session.commit_calls == 0


def test_prior_request_journal_must_be_exact_before_any_remote_get(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    case = _fixture(tmp_path)
    request = _write_request(case)
    payload = json.loads(request.read_text(encoding="utf-8"))
    request.write_text(json.dumps({**payload, "state": "UNSEALED"}), encoding="utf-8")
    monkeypatch.setattr(
        v2_drive_archive,
        "_resolve_or_create_v2_drive_archive",
        lambda **_kwargs: (_ for _ in ()).throw(_artifact_required()),
    )

    with pytest.raises(ValidationFailureError) as caught:
        case.adapter._resolve_existing_or_upload(
            context=case.context,
            operation=case.operation,
            source=case.source,
            checksum=case.source_checksum,
            measured_duration_ms=1_000,
            caption_source=case.caption,
            sidecar=case.sidecar,
        )

    assert "V2_GOOGLE_DRIVE_REQUEST_JOURNAL_MISMATCH" in str(caught.value)
    assert case.provider.get_calls == []
    assert case.service.upload_verified_calls == 0


def test_replay_with_missing_request_journal_cannot_resubmit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    case = _fixture(tmp_path)
    case.context.event.attempt_count = 2
    monkeypatch.setattr(
        v2_drive_archive,
        "_resolve_or_create_v2_drive_archive",
        lambda **_kwargs: (_ for _ in ()).throw(_artifact_required()),
    )

    with pytest.raises(WorkflowStageError) as caught:
        case.adapter._resolve_existing_or_upload(
            context=case.context,
            operation=case.operation,
            source=case.source,
            checksum=case.source_checksum,
            measured_duration_ms=1_000,
            caption_source=case.caption,
            sidecar=case.sidecar,
        )

    assert caught.value.error_code == "V2_GOOGLE_DRIVE_RESUBMISSION_FORBIDDEN"
    assert case.service.upload_verified_calls == 0
    assert case.provider.upload_file_calls == 0
    assert case.provider.get_calls == []


def test_exact_interrupted_ledger_and_request_authorize_get_only_adapter_replay(
    tmp_path: Path,
) -> None:
    case = _fixture(tmp_path)
    _write_request(case)
    input_hash = "e" * 64
    operation = {
        "stage": "ARCHIVE",
        "adapter_key": "v2-google-drive-remote",
        "operation_id": case.operation.operation_id,
        "paid_provider_call": False,
        "max_cost_usd": "0",
        "parameters": {
            "mode": "GOOGLE_DRIVE_REMOTE_ARCHIVE",
            "provider_execution": {
                "provider": "google_drive",
                "attempt_limit": 1,
                "idempotency_key": case.idempotency_key,
                "remote_object_required": True,
                "checksum_readback_required": True,
            },
        },
    }
    ledger = SimpleNamespace(
        workflow_run_id=case.run.id,
        video_project_id=case.run.video_project_id,
        production_package_artifact_version_id=(
            case.run.production_package_artifact_version_id
        ),
        production_package_hash=case.run.production_package_hash,
        command_id=case.command_id,
        stage="ARCHIVE",
        operation_id=case.operation.operation_id,
        adapter_key="v2-google-drive-remote",
        input_hash=input_hash,
        effect_invocation_count=1,
        state="FAILED_UNCERTAIN",
        started_at=object(),
        completed_at=None,
        result_type=None,
        result_id=None,
        result_ref=None,
        result_hash=None,
        result_payload={},
        authority_refs={},
        effect_journal={
            "schema_version": "vcos.production-effect-journal.v1",
            "command_id": case.command_id,
            "stage": "ARCHIVE",
            "state": "FAILED_UNCERTAIN",
            "last_error_type": "InjectedProcessCrash",
        },
    )
    render_ledger = SimpleNamespace(
        effect_journal={
            "output_relative_path": case.source.relative_to(tmp_path).as_posix(),
            "measured_render_duration_ms": 1_000,
        }
    )
    media_ledger = SimpleNamespace(
        effect_journal={**case.sidecar, "subtitle_qc_state": "PASS"}
    )

    class UpstreamOnlySession:
        def __init__(self) -> None:
            self.values = [render_ledger, media_ledger]

        def scalar(self, _statement):
            return self.values.pop(0)

    assert has_exact_drive_archive_get_only_reconciliation_authority(
        UpstreamOnlySession(),
        run=case.run,
        source_workflow_run_id=case.run.id,
        ledger=ledger,
        input_hash=input_hash,
        operation=operation,
        workspace_root=tmp_path,
    )


def test_get_only_remote_proof_materializes_then_reuses_same_exact_cloud_ref(
    tmp_path: Path,
) -> None:
    case = _fixture(tmp_path)
    _seed_remote_pair(case)
    proofs = v2_drive_archive._probe_submitted_drive_archive_get_only(
        upload_service=case.service,
        company_id=case.run.company_id,
        channel_workspace_id=case.run.channel_workspace_id,
        video_project_id=case.run.video_project_id,
        source=case.source,
        source_checksum=case.source_checksum,
        source_idempotency_key=case.idempotency_key,
        caption_source=case.caption,
        caption_checksum=case.caption_checksum,
        caption_idempotency_key=case.idempotency_key + ".caption",
    )
    source_ref = v2_drive_archive._v2_render_output_source_ref(case.run)
    retention = {
        "keep_local": True,
        "cleanup_authorized": False,
        "source": "v2-real-archive",
    }
    create_session = _FakeSession()
    created = v2_drive_archive._materialize_reconciled_drive_cloud_ref(
        session=create_session,
        run=case.run,
        local_path=case.source,
        proof=proofs["media"],
        source_ref=source_ref,
        retention_policy=retention,
    )

    assert create_session.added == [created]
    assert created.drive_file_id == proofs["media"].upload_result.drive_file_id
    assert created.verification_status == "CHECKSUM_VERIFIED"
    assert created.technical_appendix["remote_request_reconciliation_mode"] == (
        "GET_ONLY"
    )
    reuse_session = _FakeSession([created])
    reused = v2_drive_archive._materialize_reconciled_drive_cloud_ref(
        session=reuse_session,
        run=case.run,
        local_path=case.source,
        proof=proofs["media"],
        source_ref=source_ref,
        retention_policy=retention,
    )

    assert reused is created
    assert reuse_session.added == []


@pytest.mark.parametrize("governed", [False, True])
def test_retry_gateway_reaches_get_only_archive_reconciliation_for_both_lanes(
    monkeypatch: pytest.MonkeyPatch,
    governed: bool,
) -> None:
    from app.services import ai_visual_rerender_authority

    run_id = uuid.uuid4()
    package_id = uuid.uuid4()
    readiness_id = uuid.uuid4()
    source_workflow_id = uuid.uuid4() if governed else run_id
    run = SimpleNamespace(
        id=run_id,
        video_project_id=uuid.uuid4(),
        production_package_artifact_version_id=package_id,
        production_package_hash="1" * 64,
        production_readiness_receipt_artifact_version_id=readiness_id,
        production_readiness_receipt_hash="2" * 64,
    )
    provider_plan = {"plan": "provider"}
    budget_plan = {"plan": "budget"}
    governed_authority = (
        SimpleNamespace(
            provider_plan=provider_plan,
            budget_plan=budget_plan,
            source_workflow=SimpleNamespace(id=source_workflow_id),
        )
        if governed
        else None
    )
    monkeypatch.setattr(
        ai_visual_rerender_authority,
        "resolve_governed_ai_visual_rerender_execution_authority",
        lambda _session, *, workflow_run_id: (
            governed_authority
            if workflow_run_id == run_id
            else pytest.fail("wrong workflow lookup")
        ),
    )
    monkeypatch.setattr(
        production_workflow,
        "_support_authority_is_positive",
        lambda _content: True,
    )
    monkeypatch.setattr(
        production_workflow,
        "_contains_forbidden_fixture_marker",
        lambda _content: False,
    )
    if not governed:
        package = SimpleNamespace(
            readiness_evidence=SimpleNamespace(
                provider_plan_valid=True,
                budget_scope_valid=True,
            ),
            provider_execution_plan_ref=SimpleNamespace(
                artifact_version_id=uuid.uuid4(),
                content_hash="3" * 64,
            ),
            budget_scope_ref=SimpleNamespace(
                artifact_version_id=uuid.uuid4(),
                content_hash="4" * 64,
            ),
        )
        readiness = SimpleNamespace(id=readiness_id, content_hash="2" * 64)
        monkeypatch.setattr(
            production_workflow,
            "ProductionPackageService",
            lambda _session: SimpleNamespace(
                validate_for_readiness=lambda identity: (
                    package if identity == package_id else pytest.fail("wrong package")
                ),
                _receipt_for_package=lambda identity, checksum: (
                    readiness
                    if (identity, checksum) == (package_id, "1" * 64)
                    else pytest.fail("wrong readiness")
                ),
            ),
        )

        def support(_session, **kwargs):
            return provider_plan if kwargs["label"] == "PROVIDER_PLAN" else budget_plan

        monkeypatch.setattr(
            production_workflow, "_require_package_support_authority", support
        )

    captured: dict[str, object] = {}

    def proven(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        production_workflow,
        "_is_proven_drive_archive_get_only_reconciliation",
        proven,
    )
    monkeypatch.setattr(
        production_workflow,
        "_require_package_bound_retry_authority",
        lambda **_kwargs: pytest.fail("GET-only replay reached provider retry policy"),
    )
    context = SimpleNamespace(
        session=object(),
        run=run,
        event=SimpleNamespace(attempt_count=2),
    )

    production_workflow._require_gateway_execution_authority(
        context,
        stage=ProductionWorkflowStage.ARCHIVE,
    )

    assert captured["source_workflow_run_id"] == source_workflow_id
    assert captured["provider_content"] is provider_plan
