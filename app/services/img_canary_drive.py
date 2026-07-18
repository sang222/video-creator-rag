from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import ssl
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Protocol
from urllib import parse as urlparse
from urllib import request as urlrequest

import fcntl
import certifi

from app.contracts.asset_acquisition import (
    DriveArchiveFileReceipt,
    DriveArchiveReceipt,
    ProductionArchiveFileEntry,
    ProductionArchiveManifest,
)
from app.contracts.img_canary import IMGCanaryRunIdentity
from app.core.config import Settings
from app.services.m10_5 import (
    GOOGLE_DRIVE_FILES_URL,
    GoogleDriveMediaStorageProvider,
    GoogleDriveUploadResult,
)
from app.services.native_render_plan import stable_hash
from app.services.pa1r import DrivePA1RArchive
from app.services.production_archive import (
    IMG_CANARY_REQUIRED_ARCHIVE_ROLES,
    IMG_CANARY_ROLE_ARCHIVE_PATHS,
    IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES,
    IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES,
    IMGCanaryArchivePathBuilder,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMG_CANARY_DRIVE_STATE_ROOT = ROOT / "var" / "state" / "img-canary-drive"
IMG_CANARY_MANIFEST_LOGICAL_ROLE = "IMG_CANARY_PRODUCTION_ARCHIVE_MANIFEST"
IMG_CANARY_MANIFEST_ARCHIVE_PATH = "00-manifests/production-archive-manifest.json"
IMG_CANARY_DRIVE_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class IMGCanaryDriveProvider(Protocol):
    """The existing GoogleDriveMediaStorageProvider surface used by this archive."""

    def ensure_folder_path(
        self,
        *,
        access_token: str,
        root_folder_id: str,
        folder_path: list[str],
    ) -> str: ...

    def upload_file(
        self,
        *,
        access_token: str,
        local_path: Path,
        folder_id: str,
        upload_mode: str,
        mime_type: str | None,
    ) -> GoogleDriveUploadResult: ...

    def get_file_metadata(
        self,
        *,
        access_token: str,
        drive_file_id: str,
    ) -> GoogleDriveUploadResult: ...


class IMGCanaryDriveListingProvider(Protocol):
    """Optional capability used for crash reconciliation and exact-set checks.

    Google Drive providers can add this method without changing the established
    upload/auth surface. Providers without it retain per-ID verification but cannot
    claim an exact-set VERIFIED archive; unknown outcomes are never repeated.
    """

    def list_folder_files(
        self,
        *,
        access_token: str,
        folder_id: str,
    ) -> list[GoogleDriveUploadResult]: ...


class IMGCanaryDriveArchive:
    """One-way IMG canary archive upload with resumable verification and no purge.

    Real construction delegates authentication, Drive configuration, and the media
    provider to DrivePA1RArchive. An atomic journal records every returned Drive
    file ID before metadata verification. A retry re-verifies those IDs and, when
    folder listing is supported, reconciles an upload interrupted before its ID
    could be journaled. It never deletes or overwrites a remote or local artifact.
    """

    def __init__(
        self,
        *,
        provider: IMGCanaryDriveProvider,
        root_folder_id: str,
        upload_mode: str,
        access_token_resolver: Callable[[], str] | None = None,
        folder_file_lister: Callable[..., list[GoogleDriveUploadResult]] | None = None,
        state_root: Path | None = None,
    ):
        self.provider = provider
        self.root_folder_id = root_folder_id
        self.upload_mode = upload_mode
        self.access_token_resolver = access_token_resolver
        self.folder_file_lister = folder_file_lister
        self.state_root = (state_root or DEFAULT_IMG_CANARY_DRIVE_STATE_ROOT).resolve()

    @classmethod
    def from_pa1r_archive(
        cls,
        archive: DrivePA1RArchive,
        *,
        state_root: Path | None = None,
    ) -> "IMGCanaryDriveArchive":
        """Reuse the established OAuth/config/provider boundary without new auth."""

        root_folder_id = archive.config.root_folder_id()
        if not root_folder_id:
            raise RuntimeError("DRIVE_ROOT_FOLDER_MISSING")
        provider = archive.provider
        return cls(
            provider=provider,
            root_folder_id=root_folder_id,
            upload_mode=archive.config.upload_mode(),
            access_token_resolver=archive.access_token,
            folder_file_lister=(
                _list_google_drive_folder_files
                if isinstance(provider, GoogleDriveMediaStorageProvider)
                else None
            ),
            state_root=state_root,
        )

    @classmethod
    def from_existing_configuration(
        cls,
        *,
        session: Any,
        settings: Settings,
        state_root: Path | None = None,
    ) -> "IMGCanaryDriveArchive":
        return cls.from_pa1r_archive(
            DrivePA1RArchive(session, settings),
            state_root=state_root,
        )

    def upload_and_verify(
        self,
        *,
        manifest: ProductionArchiveManifest,
        run_id: str,
        archive_date: str,
        access_token: str | None = None,
    ) -> DriveArchiveReceipt:
        return self._upload_with_profile(
            manifest=manifest,
            run_id=run_id,
            archive_date=archive_date,
            access_token=access_token,
            validator=_validate_manifest_and_sources,
            receipt_path=self.receipt_path(run_id),
            journal_path=self.journal_path(run_id),
            canonical_manifest_path=self.canonical_manifest_path(run_id),
            manifest_logical_role=IMG_CANARY_MANIFEST_LOGICAL_ROLE,
            manifest_archive_path=IMG_CANARY_MANIFEST_ARCHIVE_PATH,
        )

    def upload_closeout_and_verify(
        self,
        *,
        manifest: ProductionArchiveManifest,
        run_id: str,
        archive_date: str,
        access_token: str | None = None,
    ) -> DriveArchiveReceipt:
        """Upload the immutable V3 closeout set, including its external envelope."""

        from app.contracts.img_canary_v3_closeout import (
            IMG_CANARY_V3_CLOSEOUT_MANIFEST_ARCHIVE_PATH,
            IMG_CANARY_V3_CLOSEOUT_MANIFEST_ROLE,
            IMGCanaryV3DriveExportCloseoutManifest,
        )
        from app.services.img_canary_v3_closeout import (
            validate_closeout_manifest_and_sources,
        )

        closeout = IMGCanaryV3DriveExportCloseoutManifest.model_validate(
            manifest.model_dump(mode="json")
        )
        return self._upload_with_profile(
            manifest=closeout,
            run_id=run_id,
            archive_date=archive_date,
            access_token=access_token,
            validator=validate_closeout_manifest_and_sources,
            receipt_path=self.closeout_receipt_path(run_id),
            journal_path=self.closeout_journal_path(run_id),
            canonical_manifest_path=self.closeout_canonical_manifest_path(run_id),
            manifest_logical_role=IMG_CANARY_V3_CLOSEOUT_MANIFEST_ROLE,
            manifest_archive_path=IMG_CANARY_V3_CLOSEOUT_MANIFEST_ARCHIVE_PATH,
        )

    def _upload_with_profile(
        self,
        *,
        manifest: ProductionArchiveManifest,
        run_id: str,
        archive_date: str,
        access_token: str | None,
        validator: Callable[..., None],
        receipt_path: Path,
        journal_path: Path,
        canonical_manifest_path: Path,
        manifest_logical_role: str,
        manifest_archive_path: str,
    ) -> DriveArchiveReceipt:
        root_relative_path = IMGCanaryArchivePathBuilder.build(
            run_id=run_id,
            archive_date=archive_date,
        )
        validator(manifest, run_id=run_id)
        _validate_receipt_target(receipt_path)

        if not self.root_folder_id:
            raise RuntimeError("DRIVE_ROOT_FOLDER_MISSING")

        guard_path = receipt_path.with_name(receipt_path.name + ".guard.json")
        lock_path = receipt_path.with_name(receipt_path.name + ".lock")
        _validate_state_targets(
            receipt_path,
            guard_path,
            journal_path,
            canonical_manifest_path,
            lock_path,
        )

        with _exclusive_state_lock(lock_path):
            manifest_entry = _ensure_canonical_manifest_entry(
                path=canonical_manifest_path,
                manifest=manifest,
                logical_role=manifest_logical_role,
                archive_path=manifest_archive_path,
            )
            upload_entries = sorted(
                [*manifest.files, manifest_entry],
                key=lambda item: item.expected_archive_path,
            )
            prior = _load_prior_receipt(
                receipt_path=receipt_path,
                manifest=manifest,
                root_relative_path=root_relative_path,
                upload_entries=upload_entries,
            )
            if prior is not None:
                return prior

            token = access_token or (
                self.access_token_resolver() if self.access_token_resolver else None
            )
            if not token:
                raise RuntimeError("DRIVE_OAUTH_NOT_CONNECTED")

            guard_exists = guard_path.exists()
            journal = _load_or_create_journal(
                journal_path=journal_path,
                manifest=manifest,
                run_id=run_id,
                root_relative_path=root_relative_path,
                guard_exists=guard_exists,
                configured_root_folder_id=self.root_folder_id,
                upload_mode=self.upload_mode,
                upload_entries=upload_entries,
            )
            _acquire_or_resume_guard(
                guard_path,
                {
                    "archive_manifest_ref": manifest.manifest_id,
                    "archive_manifest_hash": manifest.manifest_hash,
                    "root_relative_folder_path": root_relative_path,
                    "state": "UPLOADING",
                    "provider_call_made": bool(journal["provider_call_made"]),
                    "local_purge_allowed": False,
                },
            )

            receipt = self._resume_upload(
                token=token,
                manifest=manifest,
                root_relative_path=root_relative_path,
                journal_path=journal_path,
                journal=journal,
                upload_entries=upload_entries,
            )
            _write_json_atomic(receipt_path, receipt.model_dump(mode="json"))
            _update_guard(
                guard_path,
                {
                    "archive_manifest_ref": manifest.manifest_id,
                    "archive_manifest_hash": manifest.manifest_hash,
                    "root_relative_folder_path": root_relative_path,
                    "state": receipt.archive_state,
                    "provider_call_made": receipt.provider_call_made,
                    "local_purge_allowed": False,
                    "receipt_hash": receipt.receipt_hash,
                },
            )
            return receipt

    def _resume_upload(
        self,
        *,
        token: str,
        manifest: ProductionArchiveManifest,
        root_relative_path: str,
        journal_path: Path,
        journal: dict[str, Any],
        upload_entries: list[ProductionArchiveFileEntry],
    ) -> DriveArchiveReceipt:
        entries = upload_entries
        mismatches: list[str] = []
        provider_call_made = bool(journal["provider_call_made"])
        run_folder_id = str(journal.get("run_folder_id") or "") or None

        try:
            if run_folder_id is None:
                provider_call_made = True
                journal["provider_call_made"] = True
                _write_journal(journal_path, journal)
                run_folder_id = self.provider.ensure_folder_path(
                    access_token=token,
                    root_folder_id=self.root_folder_id,
                    folder_path=root_relative_path.split("/"),
                )
                if not run_folder_id:
                    raise RuntimeError("EMPTY_RUN_FOLDER_ID")
                journal["run_folder_id"] = run_folder_id
                _write_journal(journal_path, journal)

            with tempfile.TemporaryDirectory(prefix="vcos-img-canary-drive-") as staging_root:
                for index, entry in enumerate(entries):
                    item = journal["entries"].setdefault(
                        entry.expected_archive_path,
                        _new_journal_entry(entry),
                    )
                    remote_parent = Path(entry.expected_archive_path).parent
                    folder_id = str(item.get("folder_id") or "") or None
                    if folder_id is None:
                        provider_call_made = True
                        journal["provider_call_made"] = True
                        _write_journal(journal_path, journal)
                        folder_id = self.provider.ensure_folder_path(
                            access_token=token,
                            root_folder_id=run_folder_id,
                            folder_path=list(remote_parent.parts) if str(remote_parent) != "." else [],
                        )
                        if not folder_id:
                            raise RuntimeError("EMPTY_ENTRY_FOLDER_ID")
                        item["folder_id"] = folder_id
                        item["state"] = "FOLDER_READY"
                        _write_journal(journal_path, journal)

                    drive_file_id = str(item.get("drive_file_id") or "") or None
                    if drive_file_id is None:
                        listed = self._list_folder_files(token=token, folder_id=folder_id)
                        if listed is not None:
                            named = [
                                remote
                                for remote in listed
                                if remote.file_name == Path(entry.expected_archive_path).name
                            ]
                            if len(named) > 1:
                                item["state"] = "DUPLICATE_REMOTE_NAME"
                                mismatches.append(f"DRIVE_DUPLICATE_REMOTE_NAME:{entry.logical_role}")
                                _write_journal(journal_path, journal)
                                break
                            if len(named) == 1:
                                drive_file_id = named[0].drive_file_id or None
                                if not drive_file_id:
                                    mismatches.append(f"DRIVE_FILE_ID_MISSING:{entry.logical_role}")
                                    break
                                item["drive_file_id"] = drive_file_id
                                item["state"] = "REMOTE_ID_RECONCILED"
                                item["id_source"] = "FOLDER_LIST_RECONCILIATION"
                                # Persist the recovered remote ID before verification.
                                _write_journal(journal_path, journal)
                        elif item.get("state") in {"UPLOAD_IN_FLIGHT", "UPLOAD_OUTCOME_UNKNOWN"}:
                            mismatches.append(f"DRIVE_UPLOAD_OUTCOME_UNCERTAIN_NO_LIST:{entry.logical_role}")
                            break

                    if drive_file_id is None:
                        staged_path = _stage_with_archive_name(
                            entry=entry,
                            staging_root=Path(staging_root) / str(index),
                        )
                        item["state"] = "UPLOAD_IN_FLIGHT"
                        item["upload_started_at"] = datetime.now(UTC).isoformat()
                        provider_call_made = True
                        journal["provider_call_made"] = True
                        _write_journal(journal_path, journal)
                        try:
                            upload = self.provider.upload_file(
                                access_token=token,
                                local_path=staged_path,
                                folder_id=folder_id,
                                upload_mode=self.upload_mode,
                                mime_type=mimetypes.guess_type(staged_path.name)[0]
                                or "application/octet-stream",
                            )
                        except Exception:
                            item["state"] = "UPLOAD_OUTCOME_UNKNOWN"
                            _write_journal(journal_path, journal)
                            mismatches.append(f"DRIVE_PROVIDER_OR_STAGING_FAILURE:{entry.logical_role}")
                            break
                        drive_file_id = upload.drive_file_id or None
                        if not drive_file_id:
                            item["state"] = "UPLOAD_OUTCOME_UNKNOWN"
                            _write_journal(journal_path, journal)
                            mismatches.append(f"EMPTY_DRIVE_FILE_ID:{entry.logical_role}")
                            break
                        item["drive_file_id"] = drive_file_id
                        item["state"] = "REMOTE_ID_JOURNALED"
                        item["id_source"] = "UPLOAD_RESPONSE"
                        item["upload_recorded_at"] = datetime.now(UTC).isoformat()
                        # Critical ordering: durable ID journal precedes metadata read.
                        _write_journal(journal_path, journal)

                    try:
                        remote = self.provider.get_file_metadata(
                            access_token=token,
                            drive_file_id=drive_file_id,
                        )
                    except Exception:
                        item["state"] = "METADATA_VERIFICATION_PENDING"
                        _write_journal(journal_path, journal)
                        mismatches.append(f"DRIVE_METADATA_READ_FAILURE:{entry.logical_role}")
                        break
                    file_receipt, reasons = _verify_remote(
                        entry=entry,
                        remote=remote,
                        expected_folder_id=folder_id,
                        expected_drive_file_id=drive_file_id,
                    )
                    item["verification_receipt"] = file_receipt.model_dump(mode="json")
                    item["state"] = "VERIFIED" if not reasons else "VERIFICATION_FAILED"
                    item["verified_at"] = datetime.now(UTC).isoformat() if not reasons else None
                    _write_journal(journal_path, journal)
                    if reasons:
                        mismatches.extend(reasons)
                        break
        except Exception:
            current = _first_incomplete_entry(entries, journal)
            mismatches.append(
                "DRIVE_PROVIDER_OR_STAGING_FAILURE:"
                + (current.logical_role if current is not None else "ARCHIVE")
            )

        receipts = _journal_receipts(entries=entries, journal=journal, failed=bool(mismatches))
        if not mismatches and len(receipts) == len(entries) and all(item.verified for item in receipts):
            set_reasons, set_status = self._verify_exact_remote_set(
                token=token,
                entries=entries,
                journal=journal,
            )
            mismatches.extend(set_reasons)
            journal["remote_set_verification"] = set_status

        if mismatches:
            completed_paths = {item.archive_path for item in receipts if item.verified}
            for entry in entries:
                if entry.expected_archive_path not in completed_paths and not any(
                    item.archive_path == entry.expected_archive_path for item in receipts
                ):
                    receipts.append(_not_attempted_receipt(entry))
                if entry.expected_archive_path not in completed_paths:
                    mismatches.append(f"NOT_VERIFIED_AFTER_FAILURE:{entry.logical_role}")

        receipts.sort(key=lambda item: item.archive_path)
        journal["state"] = "FAILED" if mismatches else "VERIFIED"
        journal["provider_call_made"] = provider_call_made
        journal["last_mismatch_reason_codes"] = sorted(set(mismatches))
        _write_journal(journal_path, journal)
        return _build_receipt(
            manifest=manifest,
            root_relative_path=root_relative_path,
            run_folder_id=run_folder_id,
            receipts=receipts,
            mismatches=mismatches,
            provider_call_made=provider_call_made,
        )

    def _list_folder_files(
        self,
        *,
        token: str,
        folder_id: str,
    ) -> list[GoogleDriveUploadResult] | None:
        listing = getattr(self.provider, "list_folder_files", None)
        if callable(listing):
            return list(listing(access_token=token, folder_id=folder_id))
        if self.folder_file_lister is None:
            return None
        return list(self.folder_file_lister(access_token=token, folder_id=folder_id))

    def _supports_folder_listing(self) -> bool:
        return (
            callable(getattr(self.provider, "list_folder_files", None))
            or self.folder_file_lister is not None
        )

    def _verify_exact_remote_set(
        self,
        *,
        token: str,
        entries: list[ProductionArchiveFileEntry],
        journal: dict[str, Any],
    ) -> tuple[list[str], str]:
        grouped: dict[str, list[ProductionArchiveFileEntry]] = {}
        for entry in entries:
            item = journal["entries"][entry.expected_archive_path]
            grouped.setdefault(str(item["folder_id"]), []).append(entry)

        if not self._supports_folder_listing():
            ids = [
                str(journal["entries"][entry.expected_archive_path].get("drive_file_id") or "")
                for entry in entries
            ]
            if "" in ids or len(ids) != len(set(ids)):
                return ["DRIVE_JOURNALED_ID_SET_INVALID"], "FAILED"
            return ["DRIVE_REMOTE_SET_LIST_CAPABILITY_REQUIRED"], "FAILED"

        reasons: list[str] = []
        for folder_id, expected_entries in sorted(grouped.items()):
            try:
                actual = self._list_folder_files(token=token, folder_id=folder_id)
            except Exception:
                reasons.append(f"DRIVE_REMOTE_SET_LIST_FAILURE:{folder_id}")
                continue
            assert actual is not None
            expected_pairs = {
                (
                    Path(entry.expected_archive_path).name,
                    str(journal["entries"][entry.expected_archive_path]["drive_file_id"]),
                )
                for entry in expected_entries
            }
            actual_pairs = {(item.file_name, item.drive_file_id) for item in actual}
            names = [item.file_name for item in actual]
            if len(names) != len(set(names)):
                reasons.append(f"DRIVE_REMOTE_DUPLICATE_NAMES:{folder_id}")
            if len(actual) != len(expected_entries):
                reasons.append(f"DRIVE_REMOTE_ITEM_COUNT_MISMATCH:{folder_id}")
            if actual_pairs != expected_pairs:
                reasons.append(f"DRIVE_REMOTE_ITEM_SET_MISMATCH:{folder_id}")
        return sorted(set(reasons)), "FAILED" if reasons else "VERIFIED"

    def receipt_path(self, run_id: str) -> Path:
        """Canonical per-run path; callers cannot redirect the idempotency guard."""

        return self.state_root / run_id / "drive-archive-receipt.json"

    def journal_path(self, run_id: str) -> Path:
        """Canonical crash-recovery journal; never caller-selectable."""

        return self.state_root / run_id / "drive-upload-journal.json"

    def canonical_manifest_path(self, run_id: str) -> Path:
        """Durable canonical bytes uploaded alongside, but not inside, the manifest."""

        return self.state_root / run_id / "production-archive-manifest.json"

    def closeout_receipt_path(self, run_id: str) -> Path:
        return self.state_root / run_id / "drive-closeout-archive-receipt.json"

    def closeout_journal_path(self, run_id: str) -> Path:
        return self.state_root / run_id / "drive-closeout-upload-journal.json"

    def closeout_canonical_manifest_path(self, run_id: str) -> Path:
        return self.state_root / run_id / "drive-export-closeout-manifest.json"


def _list_google_drive_folder_files(
    *,
    access_token: str,
    folder_id: str,
) -> list[GoogleDriveUploadResult]:
    """Enumerate every direct non-folder child with the existing OAuth token."""

    escaped_parent = folder_id.replace("'", "\\'")
    page_token: str | None = None
    results: list[GoogleDriveUploadResult] = []
    while True:
        query_values = {
            "q": (
                f"'{escaped_parent}' in parents and trashed=false and "
                "mimeType!='application/vnd.google-apps.folder'"
            ),
            "fields": (
                "nextPageToken,files("
                "id,name,size,mimeType,webViewLink,parents,md5Checksum,sha256Checksum)"
            ),
            "spaces": "drive",
            "pageSize": "1000",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            query_values["pageToken"] = page_token
        request = urlrequest.Request(
            f"{GOOGLE_DRIVE_FILES_URL}?{urlparse.urlencode(query_values)}",
            method="GET",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urlrequest.urlopen(
            request,
            timeout=20,
            context=IMG_CANARY_DRIVE_SSL_CONTEXT,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list):
            raise RuntimeError("DRIVE_FOLDER_LIST_RESPONSE_INVALID")
        for item in files:
            if not isinstance(item, dict):
                raise RuntimeError("DRIVE_FOLDER_LIST_ITEM_INVALID")
            parents = item.get("parents") if isinstance(item.get("parents"), list) else []
            size_value = item.get("size")
            results.append(
                GoogleDriveUploadResult(
                    drive_file_id=str(item.get("id") or ""),
                    drive_folder_id=str(parents[0]) if parents else None,
                    web_view_link=str(item.get("webViewLink") or ""),
                    file_name=str(item.get("name") or "") or None,
                    mime_type=str(item.get("mimeType") or "") or None,
                    size_bytes=int(size_value) if size_value is not None else None,
                    checksum_sha256=str(item.get("sha256Checksum") or "") or None,
                    upload_mode=None,
                    technical_appendix={
                        "md5_checksum": str(item.get("md5Checksum") or "") or None,
                    },
                )
            )
        page_token = str(payload.get("nextPageToken") or "") or None
        if page_token is None:
            return results


def _canonical_manifest_bytes(manifest: ProductionArchiveManifest) -> bytes:
    """Stable complete manifest bytes; the upload item is intentionally external."""

    return (
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _ensure_canonical_manifest_entry(
    *,
    path: Path,
    manifest: ProductionArchiveManifest,
    logical_role: str = IMG_CANARY_MANIFEST_LOGICAL_ROLE,
    archive_path: str = IMG_CANARY_MANIFEST_ARCHIVE_PATH,
) -> ProductionArchiveFileEntry:
    if any(
        entry.expected_archive_path == archive_path
        for entry in manifest.files
    ):
        raise ValueError("IMG_CANARY_ARCHIVE_MANIFEST_SELF_REFERENCE_FORBIDDEN")
    expected = _canonical_manifest_bytes(manifest)
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != expected:
            raise RuntimeError("IMG_CANARY_ARCHIVE_CANONICAL_MANIFEST_CONFLICT")
    else:
        _write_bytes_atomic(path, expected)
    size, sha256, md5 = _source_evidence(path)
    payload = {
        "logical_role": logical_role,
        "source_path": str(path),
        "expected_archive_path": archive_path,
        "size_bytes": size,
        "sha256": sha256,
        "md5": md5,
        "required_for_archive": True,
        # The manifest is state evidence and this archive exposes no purge path.
        "required_for_local_purge": False,
    }
    return ProductionArchiveFileEntry(
        **payload,
        manifest_hash=stable_hash(payload),
    )


def _validate_manifest_and_sources(manifest: ProductionArchiveManifest, *, run_id: str) -> None:
    manifest_payload = manifest.model_dump(mode="json", exclude={"manifest_hash"})
    if stable_hash(manifest_payload) != manifest.manifest_hash:
        raise ValueError("IMG_CANARY_ARCHIVE_MANIFEST_HASH_MISMATCH")
    if not manifest.required_roles_complete:
        raise ValueError("IMG_CANARY_ARCHIVE_MANIFEST_INCOMPLETE")
    roles = [entry.logical_role for entry in manifest.files]
    paths = [entry.expected_archive_path for entry in manifest.files]
    if len(roles) != len(set(roles)) or len(paths) != len(set(paths)):
        raise ValueError("IMG_CANARY_ARCHIVE_MANIFEST_DUPLICATE_ENTRY")
    required_roles = (
        IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES
        if run_id.startswith("img-canary-v3-")
        else IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES
        if run_id.startswith("img-canary-v2-")
        else IMG_CANARY_REQUIRED_ARCHIVE_ROLES
    )
    if set(roles) != set(required_roles):
        raise ValueError("IMG_CANARY_ARCHIVE_REQUIRED_ROLES_INCOMPLETE")
    if manifest.total_size_bytes != sum(entry.size_bytes for entry in manifest.files):
        raise ValueError("IMG_CANARY_ARCHIVE_TOTAL_SIZE_MISMATCH")

    for entry in manifest.files:
        if entry.expected_archive_path != IMG_CANARY_ROLE_ARCHIVE_PATHS[entry.logical_role]:
            raise ValueError(f"IMG_CANARY_ARCHIVE_ROLE_PATH_MISMATCH:{entry.logical_role}")
        if not entry.required_for_archive:
            raise ValueError(f"IMG_CANARY_ARCHIVE_REQUIRED_FLAG_FALSE:{entry.logical_role}")
        expected_entry_hash = stable_hash(entry.model_dump(mode="json", exclude={"manifest_hash"}))
        if expected_entry_hash != entry.manifest_hash:
            raise ValueError(f"IMG_CANARY_ARCHIVE_ENTRY_HASH_MISMATCH:{entry.logical_role}")
        source = Path(entry.source_path)
        lowered_name = source.name.lower()
        if (
            lowered_name.startswith(".part")
            or lowered_name.endswith(".part")
            or ".part." in lowered_name
            or any(part.lower().startswith(".part") for part in source.parts)
        ):
            raise ValueError(f"IMG_CANARY_ARCHIVE_PART_SOURCE_FORBIDDEN:{entry.logical_role}")
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"IMG_CANARY_ARCHIVE_SOURCE_INVALID:{entry.logical_role}")
        size, sha256, md5 = _source_evidence(source)
        if size != entry.size_bytes or sha256 != entry.sha256 or md5 != entry.md5:
            raise ValueError(f"IMG_CANARY_ARCHIVE_SOURCE_CHANGED:{entry.logical_role}")

    identity_entry = next(
        entry for entry in manifest.files if entry.logical_role == "IMG_CANARY_RUN_IDENTITY"
    )
    try:
        identity = IMGCanaryRunIdentity.model_validate_json(
            Path(identity_entry.source_path).read_text(encoding="utf-8")
        )
    except Exception:
        raise ValueError("IMG_CANARY_ARCHIVE_RUN_IDENTITY_INVALID") from None
    if (
        identity.run_id != run_id
        or identity.project_id != manifest.project_id
        or identity.package_id != manifest.package_id
        or not manifest.manifest_id.startswith(run_id + "-")
    ):
        raise ValueError("IMG_CANARY_ARCHIVE_RUN_IDENTITY_MISMATCH")


def _validate_receipt_target(receipt_path: Path) -> None:
    if receipt_path.exists() and receipt_path.is_symlink():
        raise ValueError("IMG_CANARY_ARCHIVE_RECEIPT_SYMLINK_FORBIDDEN")
    if receipt_path.parent.exists() and receipt_path.parent.is_symlink():
        raise ValueError("IMG_CANARY_ARCHIVE_RECEIPT_PARENT_SYMLINK_FORBIDDEN")


def _validate_state_targets(*paths: Path) -> None:
    for path in paths:
        if path.exists() and path.is_symlink():
            raise ValueError("IMG_CANARY_ARCHIVE_STATE_SYMLINK_FORBIDDEN")
        if path.parent.exists() and path.parent.is_symlink():
            raise ValueError("IMG_CANARY_ARCHIVE_STATE_PARENT_SYMLINK_FORBIDDEN")


@contextmanager
def _exclusive_state_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _load_prior_receipt(
    *,
    receipt_path: Path,
    manifest: ProductionArchiveManifest,
    root_relative_path: str,
    upload_entries: list[ProductionArchiveFileEntry],
) -> DriveArchiveReceipt | None:
    if not receipt_path.exists():
        return None
    try:
        receipt = DriveArchiveReceipt.model_validate_json(receipt_path.read_text(encoding="utf-8"))
    except Exception:
        raise RuntimeError("IMG_CANARY_ARCHIVE_RECEIPT_INVALID") from None
    canonical = receipt.model_dump(mode="json", exclude={"receipt_hash"})
    if stable_hash(canonical) != receipt.receipt_hash:
        raise RuntimeError("IMG_CANARY_ARCHIVE_RECEIPT_HASH_MISMATCH")
    if (
        receipt.archive_manifest_ref != manifest.manifest_id
        or receipt.archive_manifest_hash != manifest.manifest_hash
        or receipt.root_relative_folder_path != root_relative_path
    ):
        raise RuntimeError("IMG_CANARY_ARCHIVE_RECEIPT_CONFLICT")
    expected_by_path = {entry.expected_archive_path: entry for entry in upload_entries}
    receipt_by_path = {item.archive_path: item for item in receipt.files}
    if len(receipt.files) != len(receipt_by_path) or set(receipt_by_path) != set(expected_by_path):
        raise RuntimeError("IMG_CANARY_ARCHIVE_RECEIPT_ITEM_SET_INVALID")
    for archive_path, entry in expected_by_path.items():
        item = receipt_by_path[archive_path]
        if (
            item.local_size != entry.size_bytes
            or item.local_sha256 != entry.sha256
            or item.local_md5 != entry.md5
        ):
            raise RuntimeError("IMG_CANARY_ARCHIVE_RECEIPT_ITEM_BINDING_INVALID")
    if receipt.archive_state == "VERIFIED" and all(item.verified for item in receipt.files):
        return receipt
    # Failed receipts are audit evidence, not a reason to repeat uploads. The
    # bound journal below decides whether same-ID verification can safely resume.
    return None


def _load_or_create_journal(
    *,
    journal_path: Path,
    manifest: ProductionArchiveManifest,
    run_id: str,
    root_relative_path: str,
    guard_exists: bool,
    configured_root_folder_id: str,
    upload_mode: str,
    upload_entries: list[ProductionArchiveFileEntry],
) -> dict[str, Any]:
    if journal_path.exists():
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except Exception:
            raise RuntimeError("IMG_CANARY_ARCHIVE_JOURNAL_INVALID") from None
        if not isinstance(journal, dict):
            raise RuntimeError("IMG_CANARY_ARCHIVE_JOURNAL_INVALID")
        state_hash = journal.pop("state_hash", None)
        if not isinstance(state_hash, str) or stable_hash(journal) != state_hash:
            raise RuntimeError("IMG_CANARY_ARCHIVE_JOURNAL_HASH_MISMATCH")
        expected_binding = (
            manifest.manifest_id,
            manifest.manifest_hash,
            run_id,
            root_relative_path,
            configured_root_folder_id,
            upload_mode,
        )
        actual_binding = (
            journal.get("archive_manifest_ref"),
            journal.get("archive_manifest_hash"),
            journal.get("run_id"),
            journal.get("root_relative_folder_path"),
            journal.get("configured_root_folder_id"),
            journal.get("upload_mode"),
        )
        if actual_binding != expected_binding:
            raise RuntimeError("IMG_CANARY_ARCHIVE_JOURNAL_CONFLICT")
        expected_paths = {entry.expected_archive_path for entry in upload_entries}
        journal_entries = journal.get("entries")
        if not isinstance(journal_entries, dict) or set(journal_entries) != expected_paths:
            raise RuntimeError("IMG_CANARY_ARCHIVE_JOURNAL_ENTRY_SET_INVALID")
        for entry in upload_entries:
            item = journal_entries[entry.expected_archive_path]
            if not isinstance(item, dict) or (
                item.get("archive_path") != entry.expected_archive_path
                or item.get("logical_role") != entry.logical_role
                or item.get("local_sha256") != entry.sha256
                or item.get("local_size") != entry.size_bytes
            ):
                raise RuntimeError("IMG_CANARY_ARCHIVE_JOURNAL_ENTRY_CONFLICT")
        return journal

    if guard_exists:
        # A legacy/partial guard without an ID journal cannot prove whether a
        # remote mutation occurred, so blind upload is forbidden.
        raise RuntimeError("IMG_CANARY_ARCHIVE_ATTEMPT_ALREADY_EXISTS_NO_JOURNAL")
    now = datetime.now(UTC).isoformat()
    journal = {
        "version": 1,
        "archive_manifest_ref": manifest.manifest_id,
        "archive_manifest_hash": manifest.manifest_hash,
        "run_id": run_id,
        "root_relative_folder_path": root_relative_path,
        "configured_root_folder_id": configured_root_folder_id,
        "upload_mode": upload_mode,
        "state": "PLANNED",
        "provider_call_made": False,
        "run_folder_id": None,
        "remote_set_verification": "PENDING",
        "last_mismatch_reason_codes": [],
        "created_at": now,
        "updated_at": now,
        "entries": {
            entry.expected_archive_path: _new_journal_entry(entry)
            for entry in upload_entries
        },
    }
    _write_journal(journal_path, journal)
    return journal


def _new_journal_entry(entry: ProductionArchiveFileEntry) -> dict[str, Any]:
    return {
        "archive_path": entry.expected_archive_path,
        "logical_role": entry.logical_role,
        "local_size": entry.size_bytes,
        "local_sha256": entry.sha256,
        "local_md5": entry.md5,
        "folder_id": None,
        "drive_file_id": None,
        "id_source": None,
        "state": "PLANNED",
        "upload_started_at": None,
        "upload_recorded_at": None,
        "verified_at": None,
        "verification_receipt": None,
    }


def _write_journal(path: Path, journal: dict[str, Any]) -> None:
    journal["updated_at"] = datetime.now(UTC).isoformat()
    _write_json_atomic(path, {**journal, "state_hash": stable_hash(journal)})


def _acquire_or_resume_guard(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        try:
            guarded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raise RuntimeError("IMG_CANARY_ARCHIVE_ATTEMPT_ALREADY_EXISTS_GUARD_INVALID") from None
        if not isinstance(guarded, dict):
            raise RuntimeError("IMG_CANARY_ARCHIVE_ATTEMPT_ALREADY_EXISTS_GUARD_INVALID")
        state_hash = guarded.pop("state_hash", None)
        if not isinstance(state_hash, str) or stable_hash(guarded) != state_hash:
            raise RuntimeError("IMG_CANARY_ARCHIVE_ATTEMPT_ALREADY_EXISTS_GUARD_INVALID")
        binding_keys = (
            "archive_manifest_ref",
            "archive_manifest_hash",
            "root_relative_folder_path",
        )
        if any(guarded.get(key) != payload.get(key) for key in binding_keys):
            raise RuntimeError("IMG_CANARY_ARCHIVE_GUARD_CONFLICT")
        if guarded.get("local_purge_allowed") is not False:
            raise RuntimeError("IMG_CANARY_ARCHIVE_GUARD_PURGE_POLICY_INVALID")
        _update_guard(path, payload)
        return
    _update_guard(path, payload)


def _update_guard(path: Path, payload: dict[str, Any]) -> None:
    _write_json_atomic(path, {**payload, "state_hash": stable_hash(payload)})


def _stage_with_archive_name(*, entry: ProductionArchiveFileEntry, staging_root: Path) -> Path:
    staging_root.mkdir(parents=True, exist_ok=False)
    staged = staging_root / Path(entry.expected_archive_path).name
    shutil.copyfile(entry.source_path, staged)
    size, sha256, md5 = _source_evidence(staged)
    if size != entry.size_bytes or sha256 != entry.sha256 or md5 != entry.md5:
        raise RuntimeError("STAGED_ARCHIVE_SOURCE_MISMATCH")
    return staged


def _verify_remote(
    *,
    entry: ProductionArchiveFileEntry,
    remote: GoogleDriveUploadResult,
    expected_folder_id: str,
    expected_drive_file_id: str,
) -> tuple[DriveArchiveFileReceipt, list[str]]:
    remote_md5 = str((remote.technical_appendix or {}).get("md5_checksum") or "").lower() or None
    remote_sha256 = str(remote.checksum_sha256 or "").lower() or None
    size_ok = remote.size_bytes == entry.size_bytes
    name_ok = remote.file_name == Path(entry.expected_archive_path).name
    folder_ok = remote.drive_folder_id == expected_folder_id
    id_ok = remote.drive_file_id == expected_drive_file_id
    sha_ok = bool(remote_sha256 and remote_sha256 == entry.sha256.lower())
    md5_ok = bool(not remote_sha256 and remote_md5 and entry.md5 and remote_md5 == entry.md5.lower())
    checksum_ok = sha_ok or md5_ok
    verified = bool(id_ok and size_ok and name_ok and folder_ok and checksum_ok)
    reasons: list[str] = []
    if not remote.drive_file_id:
        reasons.append(f"DRIVE_FILE_ID_MISSING:{entry.logical_role}")
    elif not id_ok:
        reasons.append(f"DRIVE_FILE_ID_MISMATCH:{entry.logical_role}")
    if not size_ok:
        reasons.append(f"DRIVE_SIZE_MISMATCH:{entry.logical_role}")
    if not name_ok:
        reasons.append(f"DRIVE_NAME_MISMATCH:{entry.logical_role}")
    if not folder_ok:
        reasons.append(f"DRIVE_PARENT_MISMATCH:{entry.logical_role}")
    if not checksum_ok:
        reasons.append(f"DRIVE_CHECKSUM_MISMATCH_OR_UNAVAILABLE:{entry.logical_role}")
    receipt = DriveArchiveFileReceipt(
        archive_path=entry.expected_archive_path,
        drive_file_id=expected_drive_file_id,
        local_size=entry.size_bytes,
        drive_size=remote.size_bytes,
        local_sha256=entry.sha256,
        drive_sha256=remote_sha256,
        local_md5=entry.md5,
        drive_md5=remote_md5,
        verification_method="SHA256" if sha_ok else "DRIVE_MD5_PLUS_SIZE" if md5_ok else "FAILED",
        verified=verified,
    )
    return receipt, reasons


def _first_incomplete_entry(
    entries: list[ProductionArchiveFileEntry],
    journal: dict[str, Any],
) -> ProductionArchiveFileEntry | None:
    for entry in entries:
        item = journal.get("entries", {}).get(entry.expected_archive_path, {})
        if item.get("state") != "VERIFIED":
            return entry
    return None


def _journal_receipts(
    *,
    entries: list[ProductionArchiveFileEntry],
    journal: dict[str, Any],
    failed: bool,
) -> list[DriveArchiveFileReceipt]:
    receipts: list[DriveArchiveFileReceipt] = []
    for entry in entries:
        item = journal["entries"][entry.expected_archive_path]
        payload = item.get("verification_receipt")
        if payload is not None:
            try:
                receipt = DriveArchiveFileReceipt.model_validate(payload)
            except Exception:
                raise RuntimeError("IMG_CANARY_ARCHIVE_JOURNAL_RECEIPT_INVALID") from None
            if (
                receipt.archive_path != entry.expected_archive_path
                or receipt.local_size != entry.size_bytes
                or receipt.local_sha256 != entry.sha256
                or receipt.local_md5 != entry.md5
                or receipt.drive_file_id != item.get("drive_file_id")
            ):
                raise RuntimeError("IMG_CANARY_ARCHIVE_JOURNAL_RECEIPT_CONFLICT")
            receipts.append(receipt)
            continue
        if failed:
            method = (
                "REMOTE_ID_JOURNALED_NOT_VERIFIED"
                if item.get("drive_file_id")
                else "UPLOAD_OUTCOME_UNKNOWN"
                if item.get("state") in {"UPLOAD_IN_FLIGHT", "UPLOAD_OUTCOME_UNKNOWN"}
                else "NOT_ATTEMPTED_AFTER_FAILURE"
            )
            receipts.append(
                DriveArchiveFileReceipt(
                    archive_path=entry.expected_archive_path,
                    drive_file_id=item.get("drive_file_id"),
                    local_size=entry.size_bytes,
                    local_sha256=entry.sha256,
                    local_md5=entry.md5,
                    verification_method=method,
                    verified=False,
                )
            )
    return receipts


def _not_attempted_receipt(entry: ProductionArchiveFileEntry) -> DriveArchiveFileReceipt:
    return DriveArchiveFileReceipt(
        archive_path=entry.expected_archive_path,
        local_size=entry.size_bytes,
        local_sha256=entry.sha256,
        local_md5=entry.md5,
        verification_method="NOT_ATTEMPTED_AFTER_FAILURE",
        verified=False,
    )


def _build_receipt(
    *,
    manifest: ProductionArchiveManifest,
    root_relative_path: str,
    run_folder_id: str | None,
    receipts: list[DriveArchiveFileReceipt],
    mismatches: list[str],
    provider_call_made: bool,
) -> DriveArchiveReceipt:
    state = "FAILED" if mismatches else "VERIFIED"
    payload = {
        "archive_manifest_ref": manifest.manifest_id,
        "archive_manifest_hash": manifest.manifest_hash,
        "configured_root_folder_id_reference": "configured://google-drive-root",
        "root_relative_folder_path": root_relative_path,
        "drive_folder_id": run_folder_id,
        "files": [item.model_dump(mode="json") for item in receipts],
        "total_local_size": sum(item.local_size for item in receipts),
        "total_drive_size": sum(item.drive_size or 0 for item in receipts),
        "archive_state": state,
        "mismatch_reason_codes": sorted(set(mismatches)),
        "verified_at": datetime.now(UTC) if state == "VERIFIED" else None,
        "provider_call_made": provider_call_made,
        "transport": "GOOGLE_DRIVE_API",
    }
    draft = DriveArchiveReceipt(**payload, receipt_hash="PENDING")
    receipt_hash = stable_hash(draft.model_dump(mode="json", exclude={"receipt_hash"}))
    return draft.model_copy(update={"receipt_hash": receipt_hash})


def _source_evidence(path: Path) -> tuple[int, str, str]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            sha256.update(chunk)
            md5.update(chunk)
    return size, sha256.hexdigest(), md5.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(part, path)
        _fsync_parent_directory(path)
    finally:
        part.unlink(missing_ok=True)


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(part, path)
        _fsync_parent_directory(path)
    finally:
        part.unlink(missing_ok=True)


def _fsync_parent_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
