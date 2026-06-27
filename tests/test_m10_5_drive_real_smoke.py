from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.contracts.m10_5 import MediaOffloadExecuteRequest, MediaOffloadJobCreate
from app.core.config import get_settings
from app.db.models import CredentialReference, GoogleDriveMediaCredential
from app.services.m10_5 import GOOGLE_DRIVE_SCOPE, MediaOffloadJobService

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _enabled() -> bool:
    return (
        os.getenv("GOOGLE_DRIVE_OFFLOAD_ENABLED", "").lower() == "true"
        and os.getenv("VCOS_DRIVE_REAL_UPLOAD_SMOKE", "").lower() == "true"
        and bool(os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID"))
    )


def _token_secret_ref() -> str | None:
    token_dir = ROOT / "var" / "credentials" / "google-drive" / "oauth"
    token_files = sorted(token_dir.glob("*.json"))
    if not token_files:
        return None
    return f"local_file://{token_files[-1].relative_to(ROOT)}"


def test_m10_5_real_google_drive_upload_smoke(db_session, tmp_path) -> None:
    if not _enabled():
        pytest.skip("real Google Drive smoke disabled")
    secret_ref = _token_secret_ref()
    if secret_ref is None:
        pytest.skip("real Google Drive smoke token file missing under var/credentials/google-drive/oauth")
    settings = get_settings()
    reference = CredentialReference(
        provider_key="google_drive",
        credential_key="media_offload_default",
        credential_type="OAUTH_TOKEN",
        secret_ref=secret_ref,
        scope_blob={"scopes": [GOOGLE_DRIVE_SCOPE]},
        status="CONFIGURED",
        metadata_={"storage": "LOCAL_DEV_FILE", "raw_values_in_db": False, "real_smoke": True},
    )
    db_session.add(reference)
    db_session.flush()
    db_session.add(
        GoogleDriveMediaCredential(
            credential_reference_id=reference.id,
            connection_state="CONNECTED",
            scopes=[GOOGLE_DRIVE_SCOPE],
            root_folder_id=settings.google_drive_root_folder_id,
        )
    )
    db_session.flush()
    local_file = tmp_path / "vcos-drive-smoke.txt"
    local_file.write_text("vcos drive smoke", encoding="utf-8")
    service = MediaOffloadJobService(db_session)
    job = service.create_job(data=MediaOffloadJobCreate(local_source_path=str(local_file), target_media_type="OTHER", keep_local=True))
    executed = service.execute_job(job_id=job.id, data=MediaOffloadExecuteRequest(local_source_path=str(local_file), keep_local=True))
    assert executed.job_state in {"VERIFIED", "CLEANED_LOCAL"}
    assert executed.cloud_media_ref_id is not None
