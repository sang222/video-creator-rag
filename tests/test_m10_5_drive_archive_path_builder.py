from __future__ import annotations

import uuid
from datetime import date

from app.services.m10_5 import (
    DRIVE_ARCHIVE_PATH_MODE_PROJECT_SCOPED,
    DRIVE_ARCHIVE_PATH_MODE_SMOKE_TEST_UNSCOPED,
    DRIVE_ARCHIVE_PATH_MODE_UPLOADED_VIDEO_SCOPED,
    DriveArchivePathBuilder,
    GoogleDriveUploadResult,
    GoogleDriveUploadService,
)


class FakeDriveConfigService:
    def offload_enabled(self) -> bool:
        return True

    def root_folder_id(self) -> str:
        return "root-vcos-media"

    def upload_mode(self) -> str:
        return "multipart"


class FakeCredentialService:
    def get_connected_reference(self):
        return object()

    def get_valid_access_token(self, reference) -> str:
        return "access-token"


class FakeDriveProvider:
    def __init__(self):
        self.root_folder_id = None
        self.folder_path = None
        self.uploaded_file_size = None

    def ensure_folder_path(self, *, access_token: str, root_folder_id: str, folder_path: list[str]) -> str:
        self.root_folder_id = root_folder_id
        self.folder_path = folder_path
        return "drive-folder-smoke"

    def upload_file(self, *, access_token: str, local_path, folder_id: str, upload_mode: str, mime_type: str | None):
        self.uploaded_file_size = local_path.stat().st_size
        return GoogleDriveUploadResult(
            drive_file_id="drive-file-smoke",
            drive_folder_id=folder_id,
            web_view_link="https://drive.google.com/file/d/drive-file-smoke/view",
            file_name=local_path.name,
            mime_type=mime_type,
            size_bytes=self.uploaded_file_size,
            upload_mode=upload_mode,
        )

    def get_file_metadata(self, *, access_token: str, drive_file_id: str):
        return GoogleDriveUploadResult(
            drive_file_id=drive_file_id,
            drive_folder_id="drive-folder-smoke",
            web_view_link="https://drive.google.com/file/d/drive-file-smoke/view",
            file_name="smoke.json",
            mime_type="application/json",
            size_bytes=self.uploaded_file_size,
        )


def _assert_no_legacy_segments(folder_path: list[str]) -> None:
    joined = "/".join(folder_path)
    assert folder_path[0] not in {"VCOS", "VCOS Media"}
    assert "company_unknown" not in joined
    assert "channel_unknown" not in joined
    assert "project_unknown" not in joined


def test_drive_archive_project_scoped_path_is_relative_to_configured_root() -> None:
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    project_id = uuid.uuid4()

    archive_path = DriveArchivePathBuilder(today=date(2026, 7, 4)).build(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        uploaded_video_id=None,
        media_type="AI_HERO",
    )

    assert archive_path.mode == DRIVE_ARCHIVE_PATH_MODE_PROJECT_SCOPED
    assert archive_path.folder_path == [
        f"company_{company_id}",
        f"channel_{channel_id}",
        f"project_{project_id}",
        "ai_hero",
    ]
    _assert_no_legacy_segments(archive_path.folder_path)


def test_drive_archive_smoke_unscoped_path_uses_smoke_tests_date() -> None:
    archive_path = DriveArchivePathBuilder(today=date(2026, 7, 4)).build(
        company_id=None,
        channel_workspace_id=None,
        video_project_id=None,
        uploaded_video_id=None,
        media_type="OTHER",
    )

    assert archive_path.mode == DRIVE_ARCHIVE_PATH_MODE_SMOKE_TEST_UNSCOPED
    assert archive_path.folder_path == ["smoke_tests", "2026-07-04"]
    _assert_no_legacy_segments(archive_path.folder_path)


def test_drive_archive_uploaded_video_scoped_path_uses_existing_ref_without_unknowns() -> None:
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    uploaded_video_id = uuid.uuid4()

    archive_path = DriveArchivePathBuilder(today=date(2026, 7, 4)).build(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=None,
        uploaded_video_id=uploaded_video_id,
        media_type="CAPTION",
    )

    assert archive_path.mode == DRIVE_ARCHIVE_PATH_MODE_UPLOADED_VIDEO_SCOPED
    assert archive_path.folder_path == [
        f"company_{company_id}",
        f"channel_{channel_id}",
        f"uploaded_video_{uploaded_video_id}",
        "captions",
    ]
    _assert_no_legacy_segments(archive_path.folder_path)


def test_google_drive_upload_service_passes_smoke_path_to_provider_without_real_network(db_session, tmp_path) -> None:
    local_file = tmp_path / "smoke.json"
    local_file.write_text('{"purpose":"path-builder-unit-test"}', encoding="utf-8")
    provider = FakeDriveProvider()
    service = GoogleDriveUploadService(
        db_session,
        config_service=FakeDriveConfigService(),
        credential_service=FakeCredentialService(),
        provider=provider,
        archive_path_builder=DriveArchivePathBuilder(today=date(2026, 7, 4)),
    )

    cloud_ref, verification = service.upload_verified(
        local_path=local_file,
        media_type="OTHER",
        company_id=None,
        channel_workspace_id=None,
        video_project_id=None,
        uploaded_video_id=None,
        render_package_id=None,
        source_refs=[{"type": "unit_test", "id": "drive_archive_path"}],
        retention_policy={"keep_local_after_upload": True, "cleanup_after_verified": False},
    )

    assert verification.ok is True
    assert provider.root_folder_id == "root-vcos-media"
    assert provider.folder_path == ["smoke_tests", "2026-07-04"]
    assert cloud_ref.technical_appendix["folder_path"] == ["smoke_tests", "2026-07-04"]
    assert cloud_ref.technical_appendix["folder_path_mode"] == DRIVE_ARCHIVE_PATH_MODE_SMOKE_TEST_UNSCOPED
    _assert_no_legacy_segments(provider.folder_path)
