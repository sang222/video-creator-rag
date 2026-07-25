from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from app.services.m10_5 import GoogleDriveUploadResult
from app.services.mr1_drive_archive import MR1ArchiveItem, MR1DriveArchiveService


class _FakeDrive:
    def __init__(
        self,
        *,
        checksum: str = "sha256",
        fail_metadata_once: bool = False,
        lose_upload_response_once: bool = False,
    ):
        self.checksum = checksum
        self.fail_metadata_once = fail_metadata_once
        self.lose_upload_response_once = lose_upload_response_once
        self._metadata_failed = False
        self._response_lost = False
        self.upload_calls: list[str] = []
        self.folder_calls: list[tuple[str, tuple[str, ...]]] = []
        self.metadata_calls: list[str] = []
        self.list_calls: list[str] = []
        self.remote: dict[str, GoogleDriveUploadResult] = {}

    def ensure_folder_path(self, *, access_token, root_folder_id, folder_path):
        assert access_token == "access-token"
        self.folder_calls.append((root_folder_id, tuple(folder_path)))
        suffix = "/".join(folder_path)
        return f"folder:{root_folder_id}/{suffix}"

    def upload_file(
        self, *, access_token, local_path, folder_id, upload_mode, mime_type
    ):
        assert access_token == "access-token"
        data = local_path.read_bytes()
        self.upload_calls.append(local_path.name)
        drive_file_id = f"drive-file-{len(self.upload_calls)}"
        sha256 = hashlib.sha256(data).hexdigest()
        md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
        result = GoogleDriveUploadResult(
            drive_file_id=drive_file_id,
            drive_folder_id=folder_id,
            web_view_link=f"https://drive.invalid/{drive_file_id}",
            file_name=local_path.name,
            mime_type=mime_type,
            size_bytes=len(data),
            checksum_sha256=sha256 if self.checksum == "sha256" else None,
            upload_mode=upload_mode,
            technical_appendix={
                "md5_checksum": md5 if self.checksum == "md5" else None
            },
        )
        self.remote[drive_file_id] = result
        if self.lose_upload_response_once and not self._response_lost:
            self._response_lost = True
            raise RuntimeError("response lost after remote create")
        return result

    def get_file_metadata(self, *, access_token, drive_file_id):
        assert access_token == "access-token"
        self.metadata_calls.append(drive_file_id)
        if self.fail_metadata_once and not self._metadata_failed:
            self._metadata_failed = True
            raise RuntimeError("metadata unavailable")
        return self.remote[drive_file_id]

    def list_folder_files(self, *, access_token, folder_id):
        assert access_token == "access-token"
        self.list_calls.append(folder_id)
        return sorted(
            [
                item
                for item in self.remote.values()
                if item.drive_folder_id == folder_id
            ],
            key=lambda item: (item.file_name or "", item.drive_file_id),
        )


def _source(root: Path, name: str, content: bytes) -> Path:
    path = root / "outputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _service(tmp_path: Path, provider: _FakeDrive) -> MR1DriveArchiveService:
    return MR1DriveArchiveService(
        provider=provider,
        root_folder_id="configured-root",
        upload_mode="resumable",
        source_root=tmp_path / "workspace",
        state_root=tmp_path / "state",
    )


def test_root_readiness_is_explicit_read_only_and_listable(tmp_path):
    provider = _FakeDrive()
    provider.remote["configured-root"] = GoogleDriveUploadResult(
        drive_file_id="configured-root",
        drive_folder_id=None,
        web_view_link="https://drive.invalid/root",
        file_name="VCOS archive root",
        mime_type="application/vnd.google-apps.folder",
        size_bytes=None,
    )
    service = _service(tmp_path, provider)

    evidence = service.read_only_root_readiness(access_token="access-token")

    assert evidence["result"] == "PASS"
    assert set(evidence["checks"].values()) == {"PASS"}
    assert evidence["metadata_read_calls"] == 1
    assert evidence["folder_list_read_calls"] == 1
    assert evidence["drive_archive_calls"] == 0
    assert evidence["drive_mutation_calls"] == 0
    assert provider.metadata_calls == ["configured-root"]
    assert provider.list_calls == ["configured-root"]
    assert provider.folder_calls == []
    assert provider.upload_calls == []
    assert "access-token" not in str(evidence)


def test_root_readiness_rejects_non_folder_without_listing_or_mutation(tmp_path):
    provider = _FakeDrive()
    provider.remote["configured-root"] = GoogleDriveUploadResult(
        drive_file_id="configured-root",
        drive_folder_id=None,
        web_view_link="https://drive.invalid/not-a-folder",
        file_name="wrong.txt",
        mime_type="text/plain",
        size_bytes=1,
    )
    service = _service(tmp_path, provider)

    evidence = service.read_only_root_readiness(access_token="access-token")

    assert evidence["result"] == "FAIL"
    assert evidence["checks"]["root_folder_type_exact"] == "FAIL"
    assert evidence["checks"]["root_folder_listable"] == "FAIL"
    assert provider.metadata_calls == ["configured-root"]
    assert provider.list_calls == []
    assert provider.folder_calls == []
    assert provider.upload_calls == []


@pytest.mark.parametrize("checksum", ["sha256", "md5"])
def test_uploads_exact_manifest_once_and_returns_stable_verified_receipt(
    tmp_path, checksum
):
    workspace = tmp_path / "workspace"
    first = _source(workspace, "final.mp4", b"final-media")
    second = _source(workspace, "manifest.json", b'{"exact":true}')
    items = [
        MR1ArchiveItem.from_path(
            logical_role="REVIEW_MEDIA",
            source_path=first,
            archive_path="01-media/final.mp4",
        ),
        MR1ArchiveItem.from_path(
            logical_role="RUN_MANIFEST",
            source_path=second,
            archive_path="00-manifests/manifest.json",
        ),
    ]
    provider = _FakeDrive(checksum=checksum)
    service = _service(tmp_path, provider)

    receipt = service.upload_and_verify(
        run_id="mr1-run-001",
        archive_identity="mr1-archive-001",
        root_relative_path="production/2026-07-19/mr1-run-001",
        items=items,
        access_token="access-token",
    )
    resumed = service.upload_and_verify(
        run_id="mr1-run-001",
        archive_identity="mr1-archive-001",
        root_relative_path="production/2026-07-19/mr1-run-001",
        items=items,
        access_token="access-token",
    )

    assert receipt == resumed
    assert receipt["archive_state"] == "VERIFIED"
    assert receipt["remote_exact_set_verified"] is True
    assert receipt["expected_item_count"] == receipt["verified_item_count"] == 2
    assert {item["logical_role"] for item in receipt["items"]} == {
        "REVIEW_MEDIA",
        "RUN_MANIFEST",
    }
    assert all(
        item["verification_method"] in {"SHA256_PLUS_SIZE", "MD5_PLUS_SIZE"}
        for item in receipt["files"]
    )
    assert sorted(provider.upload_calls) == ["final.mp4", "manifest.json"]
    journal = service.journal_path("mr1-run-001").read_text(encoding="utf-8")
    assert "access-token" not in journal


def test_metadata_failure_resumes_by_id_without_a_second_upload(tmp_path):
    workspace = tmp_path / "workspace"
    source = _source(workspace, "final.mp4", b"review-media")
    item = MR1ArchiveItem.from_path(
        logical_role="REVIEW_MEDIA",
        source_path=source,
        archive_path="media/final.mp4",
    )
    provider = _FakeDrive(fail_metadata_once=True)
    service = _service(tmp_path, provider)

    failed = service.upload_and_verify(
        run_id="mr1-run-002",
        archive_identity="mr1-archive-002",
        root_relative_path="production/mr1-run-002",
        items=[item],
        access_token="access-token",
    )
    verified = service.upload_and_verify(
        run_id="mr1-run-002",
        archive_identity="mr1-archive-002",
        root_relative_path="production/mr1-run-002",
        items=[item],
        access_token="access-token",
    )

    assert failed["archive_state"] == "FAILED"
    assert failed["mismatch_reason_codes"] == [
        "MR1_DRIVE_METADATA_READBACK_FAILED:REVIEW_MEDIA"
    ]
    assert verified["archive_state"] == "VERIFIED"
    assert provider.upload_calls == ["final.mp4"]


def test_lost_upload_response_is_reconciled_and_never_uploaded_twice(tmp_path):
    workspace = tmp_path / "workspace"
    source = _source(workspace, "timeline.json", b"canonical-timeline")
    item = MR1ArchiveItem.from_path(
        logical_role="CANONICAL_TIMELINE",
        source_path=source,
        archive_path="authority/timeline.json",
    )
    provider = _FakeDrive(lose_upload_response_once=True)
    service = _service(tmp_path, provider)

    first = service.upload_and_verify(
        run_id="mr1-run-003",
        archive_identity="mr1-archive-003",
        root_relative_path="production/mr1-run-003",
        items=[item],
        access_token="access-token",
    )
    second = service.upload_and_verify(
        run_id="mr1-run-003",
        archive_identity="mr1-archive-003",
        root_relative_path="production/mr1-run-003",
        items=[item],
        access_token="access-token",
    )

    assert first["archive_state"] == "FAILED"
    assert second["archive_state"] == "VERIFIED"
    assert provider.upload_calls == ["timeline.json"]


def test_conflicting_identity_duplicate_names_and_source_escape_are_blocked(tmp_path):
    workspace = tmp_path / "workspace"
    first_path = _source(workspace, "one.bin", b"one")
    second_path = _source(workspace, "two.bin", b"two")
    first = MR1ArchiveItem.from_path(
        logical_role="ONE",
        source_path=first_path,
        archive_path="a/shared.bin",
        name="shared.bin",
    )
    second = MR1ArchiveItem.from_path(
        logical_role="TWO",
        source_path=second_path,
        archive_path="b/shared.bin",
        name="shared.bin",
    )
    provider = _FakeDrive()
    service = _service(tmp_path, provider)

    with pytest.raises(ValueError, match="MR1_DRIVE_ARCHIVE_DUPLICATE_NAME"):
        service.upload_and_verify(
            run_id="mr1-run-004",
            archive_identity="mr1-archive-004",
            root_relative_path="production/mr1-run-004",
            items=[first, second],
            access_token="access-token",
        )

    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    escaped = MR1ArchiveItem.from_path(
        logical_role="OUTSIDE",
        source_path=outside,
        archive_path="outside.bin",
    )
    with pytest.raises(ValueError, match="MR1_DRIVE_SOURCE_PATH_ESCAPE"):
        service.upload_and_verify(
            run_id="mr1-run-004",
            archive_identity="mr1-archive-004",
            root_relative_path="production/mr1-run-004",
            items=[escaped],
            access_token="access-token",
        )

    verified = service.upload_and_verify(
        run_id="mr1-run-004",
        archive_identity="mr1-archive-004",
        root_relative_path="production/mr1-run-004",
        items=[first],
        access_token="access-token",
    )
    assert verified["archive_state"] == "VERIFIED"
    with pytest.raises(RuntimeError, match="IDENTITY_OR_MANIFEST_CONFLICT"):
        service.upload_and_verify(
            run_id="mr1-run-004",
            archive_identity="mr1-archive-replacement",
            root_relative_path="production/mr1-run-004",
            items=[first],
            access_token="access-token",
        )


def test_remote_exact_set_rejects_extra_or_duplicate_item(tmp_path):
    workspace = tmp_path / "workspace"
    source = _source(workspace, "final.mp4", b"review-media")
    item = MR1ArchiveItem.from_path(
        logical_role="REVIEW_MEDIA",
        source_path=source,
        archive_path="media/final.mp4",
    )
    provider = _FakeDrive()
    service = _service(tmp_path, provider)
    original_metadata = provider.get_file_metadata

    def add_extra(*, access_token, drive_file_id):
        result = original_metadata(
            access_token=access_token,
            drive_file_id=drive_file_id,
        )
        extra = replace(
            result,
            drive_file_id="unexpected-extra",
            file_name="extra.bin",
        )
        provider.remote[extra.drive_file_id] = extra
        return result

    provider.get_file_metadata = add_extra  # type: ignore[method-assign]
    receipt = service.upload_and_verify(
        run_id="mr1-run-005",
        archive_identity="mr1-archive-005",
        root_relative_path="production/mr1-run-005",
        items=[item],
        access_token="access-token",
    )

    assert receipt["archive_state"] == "FAILED"
    assert receipt["remote_exact_set_verified"] is False
    assert any(
        "EXACT_SET_COUNT_MISMATCH" in code for code in receipt["mismatch_reason_codes"]
    )


def test_remote_exact_set_recursively_rejects_unexpected_nested_subtree(tmp_path):
    workspace = tmp_path / "workspace"
    source = _source(workspace, "final.mp4", b"review-media")
    item = MR1ArchiveItem.from_path(
        logical_role="REVIEW_MEDIA",
        source_path=source,
        archive_path="media/final.mp4",
    )
    provider = _FakeDrive()
    service = _service(tmp_path, provider)
    original_metadata = provider.get_file_metadata

    def add_nested_subtree(*, access_token, drive_file_id):
        result = original_metadata(
            access_token=access_token,
            drive_file_id=drive_file_id,
        )
        folder = GoogleDriveUploadResult(
            drive_file_id="unexpected-folder",
            drive_folder_id=result.drive_folder_id,
            web_view_link=None,
            file_name="unexpected-nested",
            mime_type="application/vnd.google-apps.folder",
            size_bytes=None,
        )
        nested = replace(
            result,
            drive_file_id="unexpected-nested-file",
            drive_folder_id=folder.drive_file_id,
            file_name="nested-extra.bin",
        )
        provider.remote[folder.drive_file_id] = folder
        provider.remote[nested.drive_file_id] = nested
        return result

    provider.get_file_metadata = add_nested_subtree  # type: ignore[method-assign]
    receipt = service.upload_and_verify(
        run_id="mr1-run-nested-extra",
        archive_identity="mr1-archive-nested-extra",
        root_relative_path="production/mr1-run-nested-extra",
        items=[item],
        access_token="access-token",
    )

    reasons = receipt["mismatch_reason_codes"]
    assert receipt["archive_state"] == "FAILED"
    assert "MR1_DRIVE_EXACT_SET_UNEXPECTED_NESTED_FOLDER" in reasons
    assert "MR1_DRIVE_EXACT_SET_UNEXPECTED_NESTED_FILE" in reasons
    assert "unexpected-folder" in provider.list_calls


def test_remote_exact_set_rejects_duplicate_sibling_name(tmp_path):
    workspace = tmp_path / "workspace"
    source = _source(workspace, "final.mp4", b"review-media")
    item = MR1ArchiveItem.from_path(
        logical_role="REVIEW_MEDIA",
        source_path=source,
        archive_path="media/final.mp4",
    )
    provider = _FakeDrive()
    service = _service(tmp_path, provider)
    original_metadata = provider.get_file_metadata

    def add_duplicate_sibling(*, access_token, drive_file_id):
        result = original_metadata(
            access_token=access_token,
            drive_file_id=drive_file_id,
        )
        duplicate = replace(result, drive_file_id="unexpected-duplicate")
        provider.remote[duplicate.drive_file_id] = duplicate
        return result

    provider.get_file_metadata = add_duplicate_sibling  # type: ignore[method-assign]
    receipt = service.upload_and_verify(
        run_id="mr1-run-duplicate-sibling",
        archive_identity="mr1-archive-duplicate-sibling",
        root_relative_path="production/mr1-run-duplicate-sibling",
        items=[item],
        access_token="access-token",
    )

    reasons = receipt["mismatch_reason_codes"]
    assert receipt["archive_state"] == "FAILED"
    assert any("DUPLICATE_NAMES" in code for code in reasons)
    assert any("COUNT_MISMATCH" in code for code in reasons)
