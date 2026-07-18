from __future__ import annotations

from pathlib import Path

import pytest

from app.services.img_canary_drive import IMGCanaryDriveArchive
from app.services.img_canary_runner import IMGCanaryControlledRunner
from app.services.m10_5 import GoogleDriveUploadResult


class _RootReadProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.metadata_calls: list[tuple[str, str]] = []

    def get_file_metadata(
        self,
        *,
        access_token: str,
        drive_file_id: str,
    ) -> GoogleDriveUploadResult:
        self.metadata_calls.append((access_token, drive_file_id))
        if self.fail:
            raise RuntimeError(f"unsafe provider detail: {access_token}")
        return GoogleDriveUploadResult(
            drive_file_id=drive_file_id,
            drive_folder_id=None,
            web_view_link="https://drive.invalid/root",
            file_name="VCOS archive",
            mime_type="application/vnd.google-apps.folder",
            size_bytes=None,
        )

    def ensure_folder_path(self, **_kwargs):  # pragma: no cover - readiness only
        raise AssertionError("upload path must not run during readiness")

    def upload_file(self, **_kwargs):  # pragma: no cover - readiness only
        raise AssertionError("upload path must not run during readiness")


def _archive(tmp_path: Path, provider: _RootReadProvider) -> IMGCanaryDriveArchive:
    return IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root",
        upload_mode="resumable",
        state_root=tmp_path / "drive-state",
    )


def test_drive_root_oauth_readiness_is_transient_and_sanitized(tmp_path: Path) -> None:
    token = "ephemeral-drive-access-token"
    provider = _RootReadProvider()
    archive = _archive(tmp_path, provider)

    IMGCanaryControlledRunner.verify_drive_readiness(
        drive_archive=archive,
        access_token=token,
    )

    assert provider.metadata_calls == [(token, "configured-root")]
    assert not (tmp_path / "drive-state").exists()
    assert not any(
        token in path.read_text(encoding="utf-8", errors="ignore")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )

    failing = _archive(tmp_path, _RootReadProvider(fail=True))
    with pytest.raises(
        RuntimeError,
        match="^IMG_CANARY_DRIVE_ROOT_OR_OAUTH_NOT_READY$",
    ) as raised:
        IMGCanaryControlledRunner.verify_drive_readiness(
            drive_archive=failing,
            access_token=token,
        )
    assert token not in str(raised.value)
