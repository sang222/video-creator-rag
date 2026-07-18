from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.contracts.ai_image import ai_image_stable_hash
from app.services.img_canary_drive import (
    IMG_CANARY_MANIFEST_ARCHIVE_PATH,
    IMGCanaryDriveArchive,
    _canonical_manifest_bytes,
    _list_google_drive_folder_files,
)
from app.services.m10_5 import GoogleDriveUploadResult
from app.services.production_archive import (
    IMG_CANARY_REQUIRED_ARCHIVE_ROLES,
    IMG_CANARY_ROLE_ARCHIVE_PATHS,
    ArchiveSource,
    ProductionArchiveBuilder,
)


class FakeDriveProvider:
    def __init__(
        self,
        *,
        checksum: str = "sha256",
        corrupt_role_name: str | None = None,
        explode: bool = False,
        metadata_fail_once: bool = False,
        metadata_fail_name_once: str | None = None,
        raise_after_remote_create_once: bool = False,
        duplicate_remote_name_on_first_upload: bool = False,
    ):
        self.checksum = checksum
        self.corrupt_role_name = corrupt_role_name
        self.explode = explode
        self.metadata_fail_once = metadata_fail_once
        self.metadata_fail_name_once = metadata_fail_name_once
        self.raise_after_remote_create_once = raise_after_remote_create_once
        self.duplicate_remote_name_on_first_upload = duplicate_remote_name_on_first_upload
        self._metadata_failed = False
        self._raised_after_create = False
        self.folder_calls: list[tuple[str, tuple[str, ...]]] = []
        self.upload_calls: list[dict[str, object]] = []
        self.list_calls: list[str] = []
        self.metadata: dict[str, GoogleDriveUploadResult] = {}
        self.before_metadata = None

    def ensure_folder_path(self, *, access_token, root_folder_id, folder_path):
        assert access_token == "ephemeral-access-token"
        self.folder_calls.append((root_folder_id, tuple(folder_path)))
        return "folder:" + root_folder_id + "/" + "/".join(folder_path)

    def upload_file(self, *, access_token, local_path, folder_id, upload_mode, mime_type):
        assert access_token == "ephemeral-access-token"
        if self.explode:
            raise RuntimeError("provider leaked ephemeral-access-token")
        data = local_path.read_bytes()
        file_id = f"file-{len(self.upload_calls)}"
        role_name = local_path.name
        sha256 = hashlib.sha256(data).hexdigest()
        md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
        if role_name == self.corrupt_role_name:
            sha256 = "0" * 64
            md5 = "0" * 32
        result = GoogleDriveUploadResult(
            drive_file_id=file_id,
            drive_folder_id=folder_id,
            web_view_link=f"https://drive.invalid/{file_id}",
            file_name=role_name,
            mime_type=mime_type,
            size_bytes=len(data),
            checksum_sha256=sha256 if self.checksum == "sha256" else None,
            upload_mode=upload_mode,
            technical_appendix={"md5_checksum": md5 if self.checksum == "md5" else None},
        )
        self.upload_calls.append(
            {
                "name": role_name,
                "folder_id": folder_id,
                "bytes": data,
                "source_path": str(local_path),
            }
        )
        self.metadata[file_id] = result
        if self.duplicate_remote_name_on_first_upload and len(self.upload_calls) == 1:
            duplicate_id = file_id + "-duplicate"
            self.metadata[duplicate_id] = replace(
                result,
                drive_file_id=duplicate_id,
                web_view_link=f"https://drive.invalid/{duplicate_id}",
            )
        if self.raise_after_remote_create_once and not self._raised_after_create:
            self._raised_after_create = True
            raise RuntimeError("connection ended after remote creation")
        return result

    def get_file_metadata(self, *, access_token, drive_file_id):
        assert access_token == "ephemeral-access-token"
        if self.before_metadata is not None:
            self.before_metadata(drive_file_id)
        remote = self.metadata[drive_file_id]
        should_fail_by_name = remote.file_name == self.metadata_fail_name_once
        if (self.metadata_fail_once or should_fail_by_name) and not self._metadata_failed:
            self._metadata_failed = True
            raise RuntimeError("transient metadata failure")
        return remote

    def list_folder_files(self, *, access_token, folder_id):
        assert access_token == "ephemeral-access-token"
        self.list_calls.append(folder_id)
        return sorted(
            [item for item in self.metadata.values() if item.drive_folder_id == folder_id],
            key=lambda item: (item.file_name or "", item.drive_file_id or ""),
        )


RUN_ID = "img-canary-20260718T050000Z-deadbeef"
EXPECTED_REMOTE_FILE_COUNT = len(IMG_CANARY_REQUIRED_ARCHIVE_ROLES) + 1


def _manifest(tmp_path: Path, *, run_id: str = RUN_ID, part_role: str | None = None):
    sources: list[ArchiveSource] = []
    required_paths = {
        role: IMG_CANARY_ROLE_ARCHIVE_PATHS[role]
        for role in IMG_CANARY_REQUIRED_ARCHIVE_ROLES
    }
    for index, (role, archive_path) in enumerate(sorted(required_paths.items())):
        source_name = f"local-{index}.part.png" if role == part_role else f"local-{index}.bin"
        source = tmp_path / "source" / source_name
        source.parent.mkdir(parents=True, exist_ok=True)
        if role == "IMG_CANARY_RUN_IDENTITY":
            identity = {
                "run_id": run_id,
                "run_type": "IMG_CANARY",
                "project_id": f"{run_id}-project",
                "package_id": f"{run_id}-package",
                "canary_id": f"{run_id}-candidate",
                "channel_key": "small-team-ai",
                "niche_visual_source_profile": "STOCK_ASSISTED",
                "production_eligible": False,
                "not_publishable": True,
                "created_at": "2026-07-18T05:00:00Z",
            }
            identity["content_hash"] = ai_image_stable_hash(identity)
            source.write_text(json.dumps(identity), encoding="utf-8")
        else:
            source.write_bytes(f"{role}:fixture".encode())
        sources.append(ArchiveSource(role, source, archive_path))
    return ProductionArchiveBuilder().build(
        manifest_id=f"{run_id}-archive-manifest",
        project_id=f"{run_id}-project",
        package_id=f"{run_id}-package",
        sources=sources,
        required_roles=IMG_CANARY_REQUIRED_ARCHIVE_ROLES,
    )


@pytest.mark.parametrize("checksum", ["sha256", "md5"])
def test_complete_manifest_uploads_to_exact_path_and_verifies_without_purge(tmp_path, checksum):
    manifest = _manifest(tmp_path)
    source_snapshots = {entry.source_path: Path(entry.source_path).read_bytes() for entry in manifest.files}
    provider = FakeDriveProvider(checksum=checksum)
    archive = IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state" / checksum,
    )
    receipt = archive.upload_and_verify(
        manifest=manifest,
        run_id=RUN_ID,
        archive_date="2026-07-18",
        access_token="ephemeral-access-token",
    )
    receipt_path = archive.receipt_path(RUN_ID)
    manifest_bytes = _canonical_manifest_bytes(manifest)

    assert receipt.archive_state == "VERIFIED"
    assert receipt.root_relative_folder_path == f"smoke_tests/2026-07-18/img_canary/{RUN_ID}"
    assert receipt.total_drive_size == receipt.total_local_size == (
        manifest.total_size_bytes + len(manifest_bytes)
    )
    assert len(receipt.files) == EXPECTED_REMOTE_FILE_COUNT
    assert all(item.verified and item.verification_method in {"SHA256", "DRIVE_MD5_PLUS_SIZE"} for item in receipt.files)
    assert provider.folder_calls[0] == (
        "configured-root-id",
        ("smoke_tests", "2026-07-18", "img_canary", RUN_ID),
    )
    assert {call["name"] for call in provider.upload_calls} == {
        Path(IMG_CANARY_ROLE_ARCHIVE_PATHS[role]).name
        for role in IMG_CANARY_REQUIRED_ARCHIVE_ROLES
    } | {Path(IMG_CANARY_MANIFEST_ARCHIVE_PATH).name}
    manifest_upload = next(
        call
        for call in provider.upload_calls
        if call["name"] == Path(IMG_CANARY_MANIFEST_ARCHIVE_PATH).name
    )
    manifest_receipt = next(
        item for item in receipt.files if item.archive_path == IMG_CANARY_MANIFEST_ARCHIVE_PATH
    )
    assert manifest_upload["bytes"] == manifest_bytes
    assert json.loads(manifest_upload["bytes"]) == manifest.model_dump(mode="json")
    assert all(
        entry.expected_archive_path != IMG_CANARY_MANIFEST_ARCHIVE_PATH
        for entry in manifest.files
    )
    assert manifest_receipt.local_size == len(manifest_bytes)
    assert manifest_receipt.drive_size == len(manifest_bytes)
    assert manifest_receipt.local_sha256 == hashlib.sha256(manifest_bytes).hexdigest()
    assert manifest_receipt.verified is True
    assert all(Path(path).read_bytes() == data for path, data in source_snapshots.items())
    persisted = receipt_path.read_text(encoding="utf-8")
    guard = receipt_path.with_name(receipt_path.name + ".guard.json").read_text(encoding="utf-8")
    assert "ephemeral-access-token" not in persisted + guard
    assert json.loads(guard)["local_purge_allowed"] is False


def test_verified_receipt_is_idempotent_and_makes_no_second_drive_calls(tmp_path):
    manifest = _manifest(tmp_path)
    provider = FakeDriveProvider()
    archive = IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root-id",
        upload_mode="multipart",
        state_root=tmp_path / "state",
    )
    kwargs = {
        "manifest": manifest,
        "run_id": RUN_ID,
        "archive_date": "2026-07-18",
        "access_token": "ephemeral-access-token",
    }
    first = archive.upload_and_verify(**kwargs)
    counts = (len(provider.folder_calls), len(provider.upload_calls))
    second = archive.upload_and_verify(**kwargs)

    assert second.receipt_hash == first.receipt_hash
    assert (len(provider.folder_calls), len(provider.upload_calls)) == counts


def test_checksum_failure_reverifies_same_id_without_duplicate_upload(tmp_path):
    manifest = _manifest(tmp_path)
    first_name = Path(sorted(IMG_CANARY_ROLE_ARCHIVE_PATHS.values())[0]).name
    provider = FakeDriveProvider(corrupt_role_name=first_name)
    archive = IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state",
    )
    kwargs = {
        "manifest": manifest,
        "run_id": RUN_ID,
        "archive_date": "2026-07-18",
        "access_token": "ephemeral-access-token",
    }
    receipt = archive.upload_and_verify(**kwargs)

    assert receipt.archive_state == "FAILED"
    assert len(provider.upload_calls) == 1
    assert any(code.startswith("DRIVE_CHECKSUM_MISMATCH_OR_UNAVAILABLE:") for code in receipt.mismatch_reason_codes)
    upload_count = len(provider.upload_calls)
    second = archive.upload_and_verify(**kwargs)
    assert second.archive_state == "FAILED"
    assert len(provider.upload_calls) == upload_count
    assert second.files[0].drive_file_id == receipt.files[0].drive_file_id


def test_manifest_or_local_source_tampering_fails_before_any_drive_call(tmp_path):
    manifest = _manifest(tmp_path)
    Path(manifest.files[0].source_path).write_bytes(b"changed-after-manifest")
    provider = FakeDriveProvider()
    archive = IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state",
    )

    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        archive.upload_and_verify(
            manifest=manifest,
            run_id=RUN_ID,
            archive_date="2026-07-18",
            access_token="ephemeral-access-token",
        )
    assert provider.folder_calls == [] and provider.upload_calls == []


def test_provider_failure_is_redacted_and_persisted_as_failed_receipt(tmp_path):
    manifest = _manifest(tmp_path)
    provider = FakeDriveProvider(explode=True)
    archive = IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state",
    )
    receipt = archive.upload_and_verify(
        manifest=manifest,
        run_id=RUN_ID,
        archive_date="2026-07-18",
        access_token="ephemeral-access-token",
    )
    receipt_path = archive.receipt_path(RUN_ID)

    assert receipt.archive_state == "FAILED" and receipt.provider_call_made is True
    persisted = receipt_path.read_text(encoding="utf-8")
    assert "ephemeral-access-token" not in persisted
    assert "provider leaked" not in persisted
    assert any(code.startswith("DRIVE_PROVIDER_OR_STAGING_FAILURE:") for code in receipt.mismatch_reason_codes)


def test_metadata_failure_resumes_from_journaled_id_without_duplicate_upload(tmp_path):
    manifest = _manifest(tmp_path)
    provider = FakeDriveProvider(metadata_fail_once=True)
    archive = IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state",
    )
    kwargs = {
        "manifest": manifest,
        "run_id": RUN_ID,
        "archive_date": "2026-07-18",
        "access_token": "ephemeral-access-token",
    }
    journal_was_durable_before_metadata: list[bool] = []

    def observe_journal(drive_file_id):
        journal = json.loads(archive.journal_path(RUN_ID).read_text(encoding="utf-8"))
        journal_was_durable_before_metadata.append(
            any(
                item["drive_file_id"] == drive_file_id
                and item["state"] in {"REMOTE_ID_JOURNALED", "VERIFIED"}
                for item in journal["entries"].values()
            )
        )

    provider.before_metadata = observe_journal

    first = archive.upload_and_verify(**kwargs)
    journal = json.loads(archive.journal_path(RUN_ID).read_text(encoding="utf-8"))
    first_entry = journal["entries"][sorted(journal["entries"])[0]]

    assert first.archive_state == "FAILED"
    assert len(provider.upload_calls) == 1
    assert first_entry["drive_file_id"] == "file-0"
    assert first_entry["state"] == "METADATA_VERIFICATION_PENDING"
    assert journal_was_durable_before_metadata == [True]
    second = archive.upload_and_verify(**kwargs)
    assert second.archive_state == "VERIFIED"
    assert len(provider.upload_calls) == EXPECTED_REMOTE_FILE_COUNT
    assert len({item["name"] for item in provider.upload_calls}) == len(provider.upload_calls)
    assert json.loads(archive.journal_path(RUN_ID).read_text(encoding="utf-8"))[
        "remote_set_verification"
    ] == "VERIFIED"


def test_canonical_manifest_verification_failure_resumes_same_remote_id(tmp_path):
    manifest = _manifest(tmp_path)
    manifest_name = Path(IMG_CANARY_MANIFEST_ARCHIVE_PATH).name
    provider = FakeDriveProvider(metadata_fail_name_once=manifest_name)
    archive = IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state",
    )
    kwargs = {
        "manifest": manifest,
        "run_id": RUN_ID,
        "archive_date": "2026-07-18",
        "access_token": "ephemeral-access-token",
    }

    first = archive.upload_and_verify(**kwargs)
    journal = json.loads(archive.journal_path(RUN_ID).read_text(encoding="utf-8"))
    manifest_journal = journal["entries"][IMG_CANARY_MANIFEST_ARCHIVE_PATH]
    first_manifest_upload = next(
        item for item in provider.upload_calls if item["name"] == manifest_name
    )
    uploads_after_failure = len(provider.upload_calls)

    assert first.archive_state == "FAILED"
    assert manifest_journal["drive_file_id"] is not None
    assert manifest_journal["state"] == "METADATA_VERIFICATION_PENDING"
    second = archive.upload_and_verify(**kwargs)
    second_manifest_uploads = [
        item for item in provider.upload_calls if item["name"] == manifest_name
    ]
    assert second.archive_state == "VERIFIED"
    assert len(provider.upload_calls) == EXPECTED_REMOTE_FILE_COUNT
    assert len(provider.upload_calls) >= uploads_after_failure
    assert second_manifest_uploads == [first_manifest_upload]
    assert next(
        item for item in second.files if item.archive_path == IMG_CANARY_MANIFEST_ARCHIVE_PATH
    ).drive_file_id == manifest_journal["drive_file_id"]


def test_unknown_upload_outcome_is_reconciled_by_name_and_never_duplicated(tmp_path):
    manifest = _manifest(tmp_path)
    provider = FakeDriveProvider(raise_after_remote_create_once=True)
    archive = IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state",
    )
    kwargs = {
        "manifest": manifest,
        "run_id": RUN_ID,
        "archive_date": "2026-07-18",
        "access_token": "ephemeral-access-token",
    }

    first = archive.upload_and_verify(**kwargs)
    first_journal = json.loads(archive.journal_path(RUN_ID).read_text(encoding="utf-8"))
    interrupted = first_journal["entries"][sorted(first_journal["entries"])[0]]
    assert first.archive_state == "FAILED"
    assert interrupted["state"] == "UPLOAD_OUTCOME_UNKNOWN"
    assert interrupted["drive_file_id"] is None

    second = archive.upload_and_verify(**kwargs)
    assert second.archive_state == "VERIFIED"
    assert len(provider.upload_calls) == EXPECTED_REMOTE_FILE_COUNT
    assert len(provider.metadata) == EXPECTED_REMOTE_FILE_COUNT
    assert len({item.file_name for item in provider.metadata.values()}) == len(provider.metadata)
    first_remote_name = Path(sorted(IMG_CANARY_ROLE_ARCHIVE_PATHS.values())[0]).name
    assert sum(item.file_name == first_remote_name for item in provider.metadata.values()) == 1


def test_provider_without_listing_blocks_uncertain_resume_instead_of_reupload(tmp_path):
    manifest = _manifest(tmp_path)
    backing = FakeDriveProvider(raise_after_remote_create_once=True)

    class ProviderWithoutListing:
        ensure_folder_path = backing.ensure_folder_path
        upload_file = backing.upload_file
        get_file_metadata = backing.get_file_metadata

    archive = IMGCanaryDriveArchive(
        provider=ProviderWithoutListing(),
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state",
    )
    kwargs = {
        "manifest": manifest,
        "run_id": RUN_ID,
        "archive_date": "2026-07-18",
        "access_token": "ephemeral-access-token",
    }

    assert archive.upload_and_verify(**kwargs).archive_state == "FAILED"
    second = archive.upload_and_verify(**kwargs)
    assert second.archive_state == "FAILED"
    assert any(
        code.startswith("DRIVE_UPLOAD_OUTCOME_UNCERTAIN_NO_LIST:")
        for code in second.mismatch_reason_codes
    )
    assert len(backing.upload_calls) == 1


def test_provider_without_listing_cannot_claim_exact_set_verified(tmp_path):
    manifest = _manifest(tmp_path)
    backing = FakeDriveProvider()

    class ProviderWithoutListing:
        ensure_folder_path = backing.ensure_folder_path
        upload_file = backing.upload_file
        get_file_metadata = backing.get_file_metadata

    archive = IMGCanaryDriveArchive(
        provider=ProviderWithoutListing(),
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state",
    )
    receipt = archive.upload_and_verify(
        manifest=manifest,
        run_id=RUN_ID,
        archive_date="2026-07-18",
        access_token="ephemeral-access-token",
    )

    assert receipt.archive_state == "FAILED"
    assert all(item.verified for item in receipt.files)
    assert "DRIVE_REMOTE_SET_LIST_CAPABILITY_REQUIRED" in receipt.mismatch_reason_codes


def test_exact_remote_set_verification_rejects_duplicate_names(tmp_path):
    manifest = _manifest(tmp_path)
    provider = FakeDriveProvider(duplicate_remote_name_on_first_upload=True)
    archive = IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state",
    )
    receipt = archive.upload_and_verify(
        manifest=manifest,
        run_id=RUN_ID,
        archive_date="2026-07-18",
        access_token="ephemeral-access-token",
    )

    assert receipt.archive_state == "FAILED"
    assert any(code.startswith("DRIVE_REMOTE_DUPLICATE_NAMES:") for code in receipt.mismatch_reason_codes)
    assert any(code.startswith("DRIVE_REMOTE_ITEM_COUNT_MISMATCH:") for code in receipt.mismatch_reason_codes)
    assert len(provider.upload_calls) == EXPECTED_REMOTE_FILE_COUNT


def test_existing_pa1r_boundary_supplies_auth_config_and_provider(tmp_path):
    manifest = _manifest(tmp_path)
    provider = FakeDriveProvider()

    class ExistingConfig:
        def root_folder_id(self):
            return "configured-root-id"

        def upload_mode(self):
            return "multipart"

    class ExistingArchive:
        config = ExistingConfig()

        def __init__(self):
            self.provider = provider

        @staticmethod
        def access_token():
            return "ephemeral-access-token"

    archive = IMGCanaryDriveArchive.from_pa1r_archive(
        ExistingArchive(),
        state_root=tmp_path / "state",
    )
    receipt = archive.upload_and_verify(
        manifest=manifest,
        run_id=RUN_ID,
        archive_date="2026-07-18",
    )
    assert receipt.archive_state == "VERIFIED"


def test_existing_guard_without_receipt_blocks_blind_reupload(tmp_path):
    manifest = _manifest(tmp_path)
    provider = FakeDriveProvider()
    archive = IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state",
    )
    guard = archive.receipt_path(RUN_ID).with_name("drive-archive-receipt.json.guard.json")
    guard.parent.mkdir(parents=True)
    guard.write_text('{"state":"UPLOADING"}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="ATTEMPT_ALREADY_EXISTS"):
        archive.upload_and_verify(
            manifest=manifest,
            run_id=RUN_ID,
            archive_date="2026-07-18",
            access_token="ephemeral-access-token",
        )
    assert provider.folder_calls == [] and provider.upload_calls == []


def test_run_identity_mismatch_and_part_source_fail_before_drive(tmp_path):
    provider = FakeDriveProvider()
    archive = IMGCanaryDriveArchive(
        provider=provider,
        root_folder_id="configured-root-id",
        upload_mode="resumable",
        state_root=tmp_path / "state",
    )
    manifest = _manifest(tmp_path / "identity")
    with pytest.raises(ValueError, match="RUN_IDENTITY_MISMATCH"):
        archive.upload_and_verify(
            manifest=manifest,
            run_id="img-canary-20260718T050001Z-feedface",
            archive_date="2026-07-18",
            access_token="ephemeral-access-token",
        )

    part_role = next(role for role in IMG_CANARY_REQUIRED_ARCHIVE_ROLES if role != "IMG_CANARY_RUN_IDENTITY")
    manifest_with_part = _manifest(tmp_path / "part", part_role=part_role)
    with pytest.raises(ValueError, match="PART_SOURCE_FORBIDDEN"):
        archive.upload_and_verify(
            manifest=manifest_with_part,
            run_id=RUN_ID,
            archive_date="2026-07-18",
            access_token="ephemeral-access-token",
        )
    assert provider.folder_calls == [] and provider.upload_calls == []


def test_real_drive_folder_lister_paginates_for_exact_set_check(monkeypatch):
    pages = [
        {
            "files": [
                {
                    "id": "remote-a",
                    "name": "a.json",
                    "size": "3",
                    "mimeType": "application/json",
                    "webViewLink": "https://drive.invalid/remote-a",
                    "parents": ["folder-id"],
                    "md5Checksum": "a" * 32,
                    "sha256Checksum": "b" * 64,
                }
            ],
            "nextPageToken": "page-2",
        },
        {
            "files": [
                {
                    "id": "remote-b",
                    "name": "b.json",
                    "size": "4",
                    "mimeType": "application/json",
                    "webViewLink": "https://drive.invalid/remote-b",
                    "parents": ["folder-id"],
                    "md5Checksum": "c" * 32,
                }
            ]
        },
    ]
    requested_urls: list[str] = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def fake_urlopen(request, timeout, context=None):
        assert timeout == 20
        assert context is not None
        assert request.get_header("Authorization") == "Bearer ephemeral-access-token"
        requested_urls.append(request.full_url)
        return FakeResponse(pages[len(requested_urls) - 1])

    monkeypatch.setattr("app.services.img_canary_drive.urlrequest.urlopen", fake_urlopen)
    listed = _list_google_drive_folder_files(
        access_token="ephemeral-access-token",
        folder_id="folder-id",
    )

    assert [item.drive_file_id for item in listed] == ["remote-a", "remote-b"]
    assert all(item.drive_folder_id == "folder-id" for item in listed)
    assert len(requested_urls) == 2
    assert "pageToken=page-2" in requested_urls[1]
