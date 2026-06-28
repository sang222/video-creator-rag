from __future__ import annotations

import os
import stat
import urllib.parse
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.contracts.m10_5 import MediaOffloadExecuteRequest, MediaOffloadJobCreate
from app.core.config import get_settings
from app.core.errors import ValidationFailureError
from app.db.models import CloudMediaRef, CredentialReference, LocalMediaRetentionPolicy, MediaOffloadJob
from app.main import create_app
from app.services.m10_5 import (
    CloudMediaRefService,
    GoogleDriveConfigService,
    GoogleDriveOAuthCredentialService,
    GoogleDriveOAuthSessionService,
    GoogleDriveUploadResult,
    GoogleDriveUploadVerifier,
    GoogleDriveVerificationResult,
    LocalMediaRetentionPolicyService,
    MediaOffloadJobService,
    _hash_path,
    _sha256_file,
)

from .helpers.git_checks import tag_exists

runner = CliRunner()

M10_5_TABLES = {
    "cloud_media_refs",
    "media_offload_jobs",
    "local_media_retention_policies",
    "google_drive_media_credentials",
    "google_drive_oauth_sessions",
}

FORBIDDEN_TABLES = {
    "dashboard_widgets",
    "backend_download_proxies",
    "backend_preview_proxies",
    "youtube_upload_jobs",
    "youtube_studio_scrapes",
    "fake_traffic_events",
    "bot_engagement_events",
}


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeTokenExchanger:
    def exchange_code(self, *, code: str, client_config: dict[str, str], scopes: list[str]) -> dict:
        if code == "fail-code":
            raise RuntimeError("token exchange failed")
        return {
            "access_token": "access-ok",
            "refresh_token": "refresh-ok",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": " ".join(scopes),
        }

    def refresh_access_token(self, *, refresh_token: str, client_config: dict[str, str]) -> dict:
        return {"access_token": "access-ok", "expires_in": 3600, "token_type": "Bearer"}


class FakeUploadService:
    def __init__(self, session):
        self.session = session

    def upload_verified(self, *, local_path, media_type, company_id, channel_workspace_id, video_project_id, uploaded_video_id, render_package_id, source_refs, retention_policy):
        checksum = _sha256_file(local_path)
        upload_result = GoogleDriveUploadResult(
            drive_file_id="drive-file-1",
            drive_folder_id="drive-folder-1",
            web_view_link="https://drive.google.com/file/d/drive-file-1/view",
            file_name=local_path.name,
            mime_type="video/mp4",
            size_bytes=local_path.stat().st_size,
            upload_mode="resumable",
        )
        verification = GoogleDriveVerificationResult(True, "CHECKSUM_UNAVAILABLE", "MEDIA_OFFLOAD_UPLOAD_VERIFIED", True, False, True)
        ref = CloudMediaRefService(self.session).create_verified_ref(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            video_project_id=video_project_id,
            uploaded_video_id=uploaded_video_id,
            render_package_id=render_package_id,
            media_type=media_type,
            upload_result=upload_result,
            verification=verification,
            local_source_path_hash=_hash_path(local_path),
            checksum_sha256=checksum,
            source_refs=source_refs,
            retention_policy=retention_policy,
        )
        return ref, verification


class FailingUploadService:
    def upload_verified(self, **kwargs):
        raise ValidationFailureError("Drive API quota/auth failure")


class StaticRetentionService(LocalMediaRetentionPolicyService):
    def __init__(self, policy):
        self.policy = policy

    def get_or_create_default(self, *, company_id=None, channel_workspace_id=None):
        return self.policy

    def retention_blob(self, policy, *, keep_local: bool) -> dict:
        return {
            "keep_local_after_upload": keep_local,
            "cleanup_after_verified": policy.cleanup_after_verified,
            "allowed_cleanup_roots_count": len(policy.allowed_cleanup_roots),
            "protected_paths_count": len(policy.protected_paths),
        }


def _policy(db_session, tmp_path, *, cleanup: bool = True) -> LocalMediaRetentionPolicy:
    policy = LocalMediaRetentionPolicy(
        keep_local_after_upload=False,
        cleanup_after_verified=cleanup,
        max_local_age_hours=24,
        max_local_storage_gb=20,
        protected_paths=[],
        allowed_cleanup_roots=[str(tmp_path.resolve())],
        state="ACTIVE",
    )
    db_session.add(policy)
    db_session.flush()
    return policy


def _enable_drive_oauth(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/google-drive/callback")
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_SCOPES", "https://www.googleapis.com/auth/drive.file")
    get_settings.cache_clear()


def test_m10_5_preflight_schema_defaults_config_catalogs_and_scope(engine, db_session) -> None:
    assert tag_exists("m10-4-google-vertex-veo-binding") is True
    tables = set(inspect(engine).get_table_names())
    assert M10_5_TABLES <= tables
    assert tables.isdisjoint(FORBIDDEN_TABLES)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0021_m12_2r_handoff_ledger"
        defaults = connection.execute(
            text(
                """
                select table_name, column_name, column_default
                from information_schema.columns
                where table_name in ('cloud_media_refs','media_offload_jobs','local_media_retention_policies')
                  and column_name in ('retention_policy','source_refs','technical_appendix','target_folder_policy','protected_paths','allowed_cleanup_roots')
                """
            )
        ).mappings().all()
    assert defaults
    assert all(row["column_default"] is not None for row in defaults)


def test_m10_5_config_env_scope_and_no_secret_output(monkeypatch) -> None:
    status = GoogleDriveConfigService().safe_status()
    assert status["offload_enabled"] is False
    assert status["secret_values_exposed"] is False
    _enable_drive_oauth(monkeypatch)
    assert GoogleDriveConfigService().scopes == ["https://www.googleapis.com/auth/drive.file"]
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_SCOPES", "https://www.googleapis.com/auth/drive")
    get_settings.cache_clear()
    with pytest.raises(ValidationFailureError):
        GoogleDriveConfigService().scopes


def test_m10_5_oauth_session_and_token_storage(db_session, monkeypatch, tmp_path) -> None:
    _enable_drive_oauth(monkeypatch)
    config = GoogleDriveConfigService()
    credential_service = GoogleDriveOAuthCredentialService(
        db_session,
        config_service=config,
        token_exchanger=FakeTokenExchanger(),
        credential_dir=tmp_path / "credentials",
    )
    service = GoogleDriveOAuthSessionService(db_session, config_service=config, credential_service=credential_service)
    started = service.start()
    parsed = urllib.parse.urlparse(started.authorization_url)
    params = urllib.parse.parse_qs(parsed.query)
    state = params["state"][0]
    assert params["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    with pytest.raises(ValidationFailureError):
        service.handle_callback(state="bad-state", code="ok-code")
    failed = service.handle_callback(state=state, code="fail-code")
    assert failed.status == "FAILED"
    retry = service.start()
    retry_state = urllib.parse.parse_qs(urllib.parse.urlparse(retry.authorization_url).query)["state"][0]
    exchanged = service.handle_callback(state=retry_state, code="ok-code")
    assert exchanged.status == "TOKEN_EXCHANGED"
    assert exchanged.credential_reference_id is not None
    assert "ok-code" not in str(exchanged.__dict__)
    reference = db_session.get(CredentialReference, exchanged.credential_reference_id)
    assert reference is not None
    assert reference.secret_ref.startswith("local_file://")
    assert reference.metadata_["raw_values_in_db"] is False
    token_file = tmp_path / "credentials" / "oauth" / f"{reference.id}.json"
    assert token_file.exists()
    assert stat.S_IMODE(os.stat(token_file).st_mode) & 0o077 == 0
    assert db_session.scalar(select(func.count()).select_from(CredentialReference)) == 1


def test_m10_5_verifier_checksum_unavailable_is_size_verified() -> None:
    result = GoogleDriveUploadResult(
        drive_file_id="drive-file",
        drive_folder_id="folder",
        web_view_link="https://drive.google.com/file/d/drive-file/view",
        file_name="clip.mp4",
        mime_type="video/mp4",
        size_bytes=12,
    )
    verification = GoogleDriveUploadVerifier().verify(upload_result=result, local_size_bytes=12, local_sha256="abc")
    assert verification.ok is True
    assert verification.verification_status == "CHECKSUM_UNAVAILABLE"


def test_m10_5_offload_success_cleans_only_after_verified(db_session, tmp_path) -> None:
    local_file = tmp_path / "hero.mp4"
    local_file.write_bytes(b"verified-media")
    policy = _policy(db_session, tmp_path)
    service = MediaOffloadJobService(
        db_session,
        upload_service=FakeUploadService(db_session),
        retention_service=StaticRetentionService(policy),
    )
    job = service.create_job(data=MediaOffloadJobCreate(local_source_path=str(local_file), target_media_type="AI_HERO"))
    executed = service.execute_job(job_id=job.id, data=MediaOffloadExecuteRequest(local_source_path=str(local_file)))
    assert executed.job_state == "CLEANED_LOCAL"
    assert executed.cloud_media_ref_id is not None
    assert not local_file.exists()
    cloud_ref = db_session.get(CloudMediaRef, executed.cloud_media_ref_id)
    assert cloud_ref.upload_status == "VERIFIED"
    assert cloud_ref.local_cleanup_status == "CLEANED"
    assert cloud_ref.web_view_link.startswith("https://drive.google.com/")


def test_m10_5_failed_upload_preserves_local_file(db_session, tmp_path) -> None:
    local_file = tmp_path / "failed.mp4"
    local_file.write_bytes(b"keep-me")
    policy = _policy(db_session, tmp_path)
    service = MediaOffloadJobService(
        db_session,
        upload_service=FailingUploadService(),
        retention_service=StaticRetentionService(policy),
    )
    job = service.create_job(data=MediaOffloadJobCreate(local_source_path=str(local_file), target_media_type="AI_HERO"))
    executed = service.execute_job(job_id=job.id, data=MediaOffloadExecuteRequest(local_source_path=str(local_file)))
    assert executed.job_state == "FAILED"
    assert local_file.exists()
    assert db_session.scalar(select(func.count()).select_from(CloudMediaRef)) == 0


def test_m10_5_keep_local_and_protected_roots_skip_cleanup(db_session, tmp_path) -> None:
    local_file = tmp_path / "keep.mp4"
    local_file.write_bytes(b"keep-local")
    policy = _policy(db_session, tmp_path)
    service = MediaOffloadJobService(
        db_session,
        upload_service=FakeUploadService(db_session),
        retention_service=StaticRetentionService(policy),
    )
    job = service.create_job(data=MediaOffloadJobCreate(local_source_path=str(local_file), target_media_type="AI_HERO", keep_local=True))
    executed = service.execute_job(job_id=job.id, data=MediaOffloadExecuteRequest(local_source_path=str(local_file), keep_local=True))
    assert executed.job_state == "VERIFIED"
    assert local_file.exists()
    cloud_ref = db_session.get(CloudMediaRef, executed.cloud_media_ref_id)
    assert cloud_ref.local_cleanup_status == "SKIPPED"


def test_m10_5_api_dashboard_contract_has_drive_cta_only(db_session) -> None:
    cloud_ref = CloudMediaRef(
        media_type="AI_HERO",
        storage_provider="GOOGLE_DRIVE",
        drive_file_id="drive-file-api",
        web_view_link="https://drive.google.com/file/d/drive-file-api/view",
        file_name="hero.mp4",
        size_bytes=10,
        checksum_sha256="abc",
        local_source_path_hash="hash-only",
        upload_status="VERIFIED",
        verification_status="SIZE_VERIFIED",
        local_cleanup_status="CLEANED",
        uploaded_at=datetime.now(UTC),
        source_refs=[{"type": "test", "id": "1"}],
        technical_appendix={"local_source_path": "/tmp/secret.mp4", "backend_download_url": "no", "safe": True},
    )
    db_session.add(cloud_ref)
    db_session.flush()
    db_session.commit()
    client = TestClient(create_app())
    response = client.get(f"/media/cloud-refs/{cloud_ref.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["web_view_link"].startswith("https://drive.google.com/")
    assert "local_source_path" not in str(payload)
    assert "backend_download_url" not in str(payload)
    routes = {route.path for route in create_app().routes}
    assert not any("/download" in path or "/preview" in path for path in routes)


def test_m10_5_cli_status_and_cleanup_do_not_print_paths(db_session, tmp_path) -> None:
    _policy(db_session, tmp_path)
    status = runner.invoke(cli_app, ["drive", "connection-status"])
    assert status.exit_code == 0
    assert "client-secret" not in status.output
    cleanup = runner.invoke(cli_app, ["media", "cleanup-local", "--dry-run"])
    assert cleanup.exit_code == 0
    assert str(tmp_path) not in cleanup.output
