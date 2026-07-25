from __future__ import annotations

import fcntl
import hashlib
import json
import mimetypes
import os
import re
import shutil
import ssl
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Protocol
from urllib import parse as urlparse
from urllib import request as urlrequest

import certifi

from app.core.config import Settings
from app.services.m10_5 import (
    GOOGLE_DRIVE_FILES_URL,
    GoogleDriveConfigService,
    GoogleDriveMediaStorageProvider,
    GoogleDriveOAuthCredentialService,
    GoogleDriveUploadResult,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MR1_DRIVE_STATE_ROOT = ROOT / "var" / "state" / "mr1-drive"
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_DRIVE_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class MR1DriveProvider(Protocol):
    """The narrow Google Drive surface used by the MR1 archive boundary."""

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


class MR1DriveListingProvider(MR1DriveProvider, Protocol):
    def list_folder_files(
        self,
        *,
        access_token: str,
        folder_id: str,
    ) -> list[GoogleDriveUploadResult]: ...


@dataclass(frozen=True)
class MR1ArchiveItem:
    """Immutable local evidence for one exact MR1 archive item."""

    logical_role: str
    name: str
    source_path: str
    archive_path: str
    size_bytes: int
    sha256: str
    md5: str

    @classmethod
    def from_path(
        cls,
        *,
        logical_role: str,
        source_path: Path | str,
        archive_path: str,
        name: str | None = None,
    ) -> "MR1ArchiveItem":
        source = Path(source_path).resolve()
        size, sha256, md5 = _file_evidence(source)
        return cls(
            logical_role=logical_role,
            name=name or PurePosixPath(archive_path).name,
            source_path=str(source),
            archive_path=archive_path,
            size_bytes=size,
            sha256=sha256,
            md5=md5,
        )

    @classmethod
    def from_value(
        cls, value: "MR1ArchiveItem | Mapping[str, Any]"
    ) -> "MR1ArchiveItem":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("MR1_DRIVE_ARCHIVE_ITEM_INVALID")
        required = {
            "logical_role",
            "name",
            "source_path",
            "archive_path",
            "size_bytes",
            "sha256",
            "md5",
        }
        if set(value) != required:
            raise ValueError("MR1_DRIVE_ARCHIVE_ITEM_FIELDS_INVALID")
        return cls(
            logical_role=str(value["logical_role"]),
            name=str(value["name"]),
            source_path=str(value["source_path"]),
            archive_path=str(value["archive_path"]),
            size_bytes=int(value["size_bytes"]),
            sha256=str(value["sha256"]),
            md5=str(value["md5"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MR1DriveArchiveService:
    """Resumable, one-upload-per-item Drive archive for the approved MR1 run.

    The per-run journal is the idempotency authority. It records an upload attempt
    before the provider boundary and never repeats that item's upload call. If the
    response is lost, a later invocation may only reconcile the exact remote name.
    Folder listing is mandatory because VERIFIED means the remote item set is exact.
    """

    def __init__(
        self,
        *,
        provider: MR1DriveProvider,
        root_folder_id: str,
        upload_mode: str,
        source_root: Path,
        access_token_resolver: Callable[[], str] | None = None,
        folder_file_lister: Callable[..., list[GoogleDriveUploadResult]] | None = None,
        state_root: Path | None = None,
    ):
        if not root_folder_id:
            raise ValueError("MR1_DRIVE_ROOT_FOLDER_MISSING")
        if upload_mode not in {"multipart", "resumable"}:
            raise ValueError("MR1_DRIVE_UPLOAD_MODE_INVALID")
        self.provider = provider
        self.root_folder_id = root_folder_id
        self.upload_mode = upload_mode
        self.source_root = source_root.resolve()
        self.access_token_resolver = access_token_resolver
        self.folder_file_lister = folder_file_lister
        self.state_root = (state_root or DEFAULT_MR1_DRIVE_STATE_ROOT).resolve()

    @classmethod
    def from_existing_configuration(
        cls,
        *,
        session: Any,
        settings: Settings,
        source_root: Path,
        state_root: Path | None = None,
    ) -> "MR1DriveArchiveService":
        """Reuse the established Drive OAuth/config/provider implementation."""

        config = GoogleDriveConfigService(settings)
        root_folder_id = config.root_folder_id()
        if not root_folder_id:
            raise RuntimeError("MR1_DRIVE_ROOT_FOLDER_MISSING")
        credentials = GoogleDriveOAuthCredentialService(session, config_service=config)

        def resolve_access_token() -> str:
            reference = credentials.get_connected_reference()
            if reference is None:
                raise RuntimeError("MR1_DRIVE_OAUTH_NOT_CONNECTED")
            token = credentials.get_valid_access_token(reference)
            if not token:
                raise RuntimeError("MR1_DRIVE_OAUTH_NEEDS_REAUTH")
            return token

        return cls(
            provider=GoogleDriveMediaStorageProvider(),
            root_folder_id=root_folder_id,
            upload_mode=config.upload_mode(),
            source_root=source_root,
            access_token_resolver=resolve_access_token,
            folder_file_lister=_list_google_drive_folder_files,
            state_root=state_root,
        )

    def upload_and_verify(
        self,
        *,
        run_id: str,
        archive_identity: str,
        root_relative_path: str,
        items: Sequence[MR1ArchiveItem | Mapping[str, Any]],
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Upload and verify the exact item set, returning a JSON-safe receipt."""

        _validate_run_id(run_id)
        if not archive_identity or not archive_identity.strip():
            raise ValueError("MR1_DRIVE_ARCHIVE_IDENTITY_MISSING")
        if archive_identity != archive_identity.strip():
            raise ValueError("MR1_DRIVE_ARCHIVE_IDENTITY_NOT_CANONICAL")
        canonical_root = _validate_relative_path(root_relative_path, file_path=False)
        normalized_items = _validate_items(items, source_root=self.source_root)
        manifest = _manifest_payload(
            run_id=run_id,
            archive_identity=archive_identity,
            root_relative_path=canonical_root,
            items=normalized_items,
        )
        manifest_hash = _stable_hash(manifest)
        journal_path = self.journal_path(run_id)
        receipt_path = self.receipt_path(run_id)
        lock_path = self.lock_path(run_id)
        _assert_contained(journal_path, self.state_root, "MR1_DRIVE_STATE_PATH_ESCAPE")
        _assert_contained(receipt_path, self.state_root, "MR1_DRIVE_STATE_PATH_ESCAPE")

        with _exclusive_lock(lock_path):
            journal = self._load_or_create_journal(
                journal_path=journal_path,
                manifest=manifest,
                manifest_hash=manifest_hash,
            )
            prior = _load_verified_receipt(
                receipt_path=receipt_path,
                archive_identity=archive_identity,
                manifest_hash=manifest_hash,
            )
            if prior is not None:
                return prior

            token = access_token
            if not token and self.access_token_resolver is not None:
                token = self.access_token_resolver()
            if not token:
                raise RuntimeError("MR1_DRIVE_OAUTH_NOT_CONNECTED")

            receipt = self._resume(
                access_token=token,
                manifest=manifest,
                manifest_hash=manifest_hash,
                items=normalized_items,
                journal_path=journal_path,
                journal=journal,
            )
            _write_json_atomic(receipt_path, receipt)
            return receipt

    def read_only_root_readiness(
        self, *, access_token: str | None = None
    ) -> dict[str, Any]:
        """Verify the configured archive root without creating or uploading.

        A configured folder id and a usable OAuth token do not prove that the
        credential can still see the intended Drive folder.  This probe uses
        only Drive ``files.get`` and ``files.list`` reads.  In particular it
        never calls ``ensure_folder_path`` or ``upload_file``.
        """

        checks = {
            "oauth_access_token_usable": False,
            "root_folder_metadata_accessible": False,
            "root_folder_identity_exact": False,
            "root_folder_type_exact": False,
            "root_folder_listable": False,
            "mutation_free": True,
        }
        metadata_reads = 0
        listing_reads = 0
        reason_codes: list[str] = []
        token = access_token
        try:
            if not token and self.access_token_resolver is not None:
                token = self.access_token_resolver()
            checks["oauth_access_token_usable"] = bool(token)
            if not token:
                reason_codes.append("MR1_DRIVE_OAUTH_NOT_CONNECTED")
            else:
                metadata_reads += 1
                metadata = self.provider.get_file_metadata(
                    access_token=token,
                    drive_file_id=self.root_folder_id,
                )
                checks["root_folder_metadata_accessible"] = True
                checks["root_folder_identity_exact"] = (
                    metadata.drive_file_id == self.root_folder_id
                )
                checks["root_folder_type_exact"] = (
                    metadata.mime_type == "application/vnd.google-apps.folder"
                )
                if not checks["root_folder_identity_exact"]:
                    reason_codes.append("MR1_DRIVE_ROOT_FOLDER_ID_MISMATCH")
                if not checks["root_folder_type_exact"]:
                    reason_codes.append("MR1_DRIVE_ROOT_FOLDER_TYPE_INVALID")
                if (
                    checks["root_folder_identity_exact"]
                    and checks["root_folder_type_exact"]
                ):
                    listing_reads += 1
                    listed = self._list_folder_files(
                        access_token=token,
                        folder_id=self.root_folder_id,
                    )
                    if not isinstance(listed, list):
                        raise RuntimeError("MR1_DRIVE_FOLDER_LIST_RESPONSE_INVALID")
                    checks["root_folder_listable"] = True
        except Exception as exc:
            reason_codes.append(_safe_provider_reason(exc))

        failed = sorted(key for key, passed in checks.items() if passed is not True)
        return {
            "schema_version": "mr1.drive-root-readiness.v1",
            "mode": "READ_ONLY_NO_UPLOAD_NO_MUTATION",
            "root_folder_id_hash": _stable_hash(self.root_folder_id),
            "checks": {
                key: "PASS" if passed else "FAIL"
                for key, passed in sorted(checks.items())
            },
            "failed_checks": failed,
            "reason_codes": sorted(set(reason_codes)),
            "metadata_read_calls": metadata_reads,
            "folder_list_read_calls": listing_reads,
            "drive_archive_calls": 0,
            "drive_mutation_calls": 0,
            "raw_drive_response_persisted": False,
            "secret_values_exposed": False,
            "result": "PASS" if not failed else "FAIL",
            "checked_at": datetime.now(UTC).isoformat(),
        }

    def journal_path(self, run_id: str) -> Path:
        _validate_run_id(run_id)
        return self.state_root / run_id / "drive-upload-journal.json"

    def receipt_path(self, run_id: str) -> Path:
        _validate_run_id(run_id)
        return self.state_root / run_id / "drive-archive-receipt.json"

    def lock_path(self, run_id: str) -> Path:
        _validate_run_id(run_id)
        return self.state_root / run_id / "drive-archive.lock"

    def _load_or_create_journal(
        self,
        *,
        journal_path: Path,
        manifest: dict[str, Any],
        manifest_hash: str,
    ) -> dict[str, Any]:
        if journal_path.exists():
            journal = _read_json_object(journal_path)
            immutable = {
                "schema_version": "MR1_DRIVE_ARCHIVE_V1",
                "run_id": manifest["run_id"],
                "archive_identity": manifest["archive_identity"],
                "root_relative_path": manifest["root_relative_path"],
                "manifest_hash": manifest_hash,
                "manifest": manifest,
                "configured_root_folder_id_hash": _stable_hash(self.root_folder_id),
                "upload_mode": self.upload_mode,
            }
            if any(journal.get(key) != value for key, value in immutable.items()):
                raise RuntimeError("MR1_DRIVE_ARCHIVE_IDENTITY_OR_MANIFEST_CONFLICT")
            return journal

        entries = {
            item["archive_path"]: {
                "logical_role": item["logical_role"],
                "name": item["name"],
                "archive_path": item["archive_path"],
                "source_path": item["source_path"],
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
                "md5": item["md5"],
                "folder_id": None,
                "drive_file_id": None,
                "upload_attempted": False,
                "upload_call_count": 0,
                "state": "PLANNED",
                "verification": None,
            }
            for item in manifest["items"]
        }
        journal = {
            "schema_version": "MR1_DRIVE_ARCHIVE_V1",
            "run_id": manifest["run_id"],
            "archive_identity": manifest["archive_identity"],
            "root_relative_path": manifest["root_relative_path"],
            "manifest_hash": manifest_hash,
            "manifest": manifest,
            "configured_root_folder_id_hash": _stable_hash(self.root_folder_id),
            "upload_mode": self.upload_mode,
            "run_folder_id": None,
            "entries": entries,
            "state": "PLANNED",
            "provider_call_made": False,
            "remote_exact_set_verified": False,
            "remote_set_readback": {},
            "mismatch_reason_codes": [],
        }
        _write_json_atomic(journal_path, journal)
        return journal

    def _resume(
        self,
        *,
        access_token: str,
        manifest: dict[str, Any],
        manifest_hash: str,
        items: list[dict[str, Any]],
        journal_path: Path,
        journal: dict[str, Any],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        run_folder_id = str(journal.get("run_folder_id") or "") or None
        try:
            if run_folder_id is None:
                journal["provider_call_made"] = True
                journal["state"] = "CREATING_ARCHIVE_FOLDER"
                _write_json_atomic(journal_path, journal)
                run_folder_id = self.provider.ensure_folder_path(
                    access_token=access_token,
                    root_folder_id=self.root_folder_id,
                    folder_path=manifest["root_relative_path"].split("/"),
                )
                if not run_folder_id:
                    raise RuntimeError("MR1_DRIVE_ARCHIVE_FOLDER_ID_MISSING")
                journal["run_folder_id"] = run_folder_id
                journal["state"] = "ARCHIVE_FOLDER_READY"
                _write_json_atomic(journal_path, journal)

            for item in items:
                entry = journal["entries"][item["archive_path"]]
                if entry.get("state") == "VERIFIED":
                    continue
                item_reasons = self._resume_item(
                    access_token=access_token,
                    run_folder_id=run_folder_id,
                    item=item,
                    entry=entry,
                    journal_path=journal_path,
                    journal=journal,
                )
                if item_reasons:
                    reasons.extend(item_reasons)
                    break

            if not reasons and all(
                journal["entries"][item["archive_path"]].get("state") == "VERIFIED"
                for item in items
            ):
                set_reasons = self._verify_exact_set(
                    access_token=access_token,
                    items=items,
                    journal=journal,
                )
                reasons.extend(set_reasons)
                journal["remote_exact_set_verified"] = not set_reasons
        except Exception as exc:
            reasons.append(_safe_provider_reason(exc))

        if not reasons:
            ids = [
                str(journal["entries"][item["archive_path"]].get("drive_file_id") or "")
                for item in items
            ]
            if "" in ids or len(ids) != len(set(ids)):
                reasons.append("MR1_DRIVE_REMOTE_ID_SET_INVALID")

        journal["state"] = "VERIFIED" if not reasons else "FAILED"
        journal["mismatch_reason_codes"] = sorted(set(reasons))
        _write_json_atomic(journal_path, journal)
        return _build_receipt(
            manifest=manifest,
            manifest_hash=manifest_hash,
            run_folder_id=run_folder_id,
            journal=journal,
            reasons=reasons,
        )

    def _resume_item(
        self,
        *,
        access_token: str,
        run_folder_id: str,
        item: dict[str, Any],
        entry: dict[str, Any],
        journal_path: Path,
        journal: dict[str, Any],
    ) -> list[str]:
        role = item["logical_role"]
        folder_id = str(entry.get("folder_id") or "") or None
        if folder_id is None:
            # ``archive_path`` is the immutable logical package path.  Every
            # remote object in one revision is deliberately a direct child of
            # the revision run folder, so correct-parent and recursive
            # exact-subtree verification are independently provable.
            folder_id = run_folder_id
            entry["folder_id"] = folder_id
            entry["state"] = "ITEM_FOLDER_READY"
            _write_json_atomic(journal_path, journal)
        elif folder_id != run_folder_id:
            return [f"MR1_DRIVE_PARENT_MISMATCH:{role}"]

        drive_file_id = str(entry.get("drive_file_id") or "") or None
        if drive_file_id is None:
            try:
                remote_files = self._list_folder_files(
                    access_token=access_token,
                    folder_id=folder_id,
                )
            except Exception:
                return [f"MR1_DRIVE_REMOTE_LIST_FAILED:{role}"]
            named = [
                remote for remote in remote_files if remote.file_name == item["name"]
            ]
            if len(named) > 1:
                return [f"MR1_DRIVE_REMOTE_DUPLICATE_NAME:{role}"]
            if len(named) == 1:
                drive_file_id = str(named[0].drive_file_id or "") or None
                if not drive_file_id:
                    return [f"MR1_DRIVE_REMOTE_ID_MISSING:{role}"]
                entry["drive_file_id"] = drive_file_id
                entry["state"] = "REMOTE_ID_RECONCILED"
                entry["drive_id_source"] = "FOLDER_LIST"
                _write_json_atomic(journal_path, journal)
            elif entry.get("upload_attempted"):
                entry["state"] = "UPLOAD_OUTCOME_UNKNOWN_NO_REMOTE"
                _write_json_atomic(journal_path, journal)
                return [f"MR1_DRIVE_UPLOAD_NOT_REPEATABLE:{role}"]

        if drive_file_id is None:
            try:
                with tempfile.TemporaryDirectory(
                    prefix="mr1-drive-stage-",
                    dir=journal_path.parent,
                ) as temp_root:
                    staged = Path(temp_root) / item["name"]
                    shutil.copyfile(item["source_path"], staged)
                    if _file_evidence(staged) != (
                        item["size_bytes"],
                        item["sha256"],
                        item["md5"],
                    ):
                        return [f"MR1_DRIVE_STAGED_BYTES_MISMATCH:{role}"]
                    entry["upload_attempted"] = True
                    entry["upload_call_count"] = 1
                    entry["state"] = "UPLOAD_SUBMITTING"
                    entry["upload_started_at"] = datetime.now(UTC).isoformat()
                    journal["provider_call_made"] = True
                    _write_json_atomic(journal_path, journal)
                    upload = self.provider.upload_file(
                        access_token=access_token,
                        local_path=staged,
                        folder_id=folder_id,
                        upload_mode=self.upload_mode,
                        mime_type=mimetypes.guess_type(item["name"])[0]
                        or "application/octet-stream",
                    )
            except Exception:
                entry["state"] = "UPLOAD_OUTCOME_UNKNOWN"
                _write_json_atomic(journal_path, journal)
                return [f"MR1_DRIVE_UPLOAD_OUTCOME_UNKNOWN:{role}"]
            drive_file_id = str(upload.drive_file_id or "") or None
            if not drive_file_id:
                entry["state"] = "UPLOAD_OUTCOME_UNKNOWN"
                _write_json_atomic(journal_path, journal)
                return [f"MR1_DRIVE_UPLOAD_RESPONSE_ID_MISSING:{role}"]
            entry["drive_file_id"] = drive_file_id
            entry["drive_id_source"] = "UPLOAD_RESPONSE"
            entry["state"] = "REMOTE_ID_JOURNALED"
            entry["upload_recorded_at"] = datetime.now(UTC).isoformat()
            _write_json_atomic(journal_path, journal)

        try:
            remote = self.provider.get_file_metadata(
                access_token=access_token,
                drive_file_id=drive_file_id,
            )
        except Exception:
            entry["state"] = "READBACK_PENDING"
            _write_json_atomic(journal_path, journal)
            return [f"MR1_DRIVE_METADATA_READBACK_FAILED:{role}"]
        verification, verification_reasons = _verify_remote(
            item=item,
            remote=remote,
            expected_folder_id=folder_id,
            expected_drive_file_id=drive_file_id,
        )
        entry["verification"] = verification
        entry["state"] = (
            "VERIFIED" if not verification_reasons else "VERIFICATION_FAILED"
        )
        entry["verified_at"] = (
            datetime.now(UTC).isoformat() if not verification_reasons else None
        )
        _write_json_atomic(journal_path, journal)
        return verification_reasons

    def _list_folder_files(
        self,
        *,
        access_token: str,
        folder_id: str,
    ) -> list[GoogleDriveUploadResult]:
        journaled_listing = getattr(self.provider, "list_folder_files", None)
        if callable(journaled_listing):
            return list(
                journaled_listing(access_token=access_token, folder_id=folder_id)
            )
        if self.folder_file_lister is None:
            raise RuntimeError("MR1_DRIVE_EXACT_SET_LIST_CAPABILITY_REQUIRED")
        return list(
            self.folder_file_lister(access_token=access_token, folder_id=folder_id)
        )

    def _verify_exact_set(
        self,
        *,
        access_token: str,
        items: list[dict[str, Any]],
        journal: dict[str, Any],
    ) -> list[str]:
        reasons: list[str] = []
        readback: dict[str, Any] = {}
        run_folder_id = str(journal.get("run_folder_id") or "")
        if not run_folder_id:
            return ["MR1_DRIVE_RUN_FOLDER_ID_MISSING"]
        expected_pairs: set[tuple[str, str]] = set()
        for item in items:
            entry = journal["entries"][item["archive_path"]]
            if str(entry.get("folder_id") or "") != run_folder_id:
                reasons.append(
                    f"MR1_DRIVE_EXPECTED_PARENT_MISMATCH:{item['logical_role']}"
                )
            expected_pairs.add((item["name"], str(entry.get("drive_file_id") or "")))

        queue = [run_folder_id]
        seen_folders: set[str] = set()
        remote_files: list[GoogleDriveUploadResult] = []
        remote_file_parents: list[str] = []
        unexpected_folders: list[str] = []
        while queue:
            folder_id = queue.pop(0)
            if folder_id in seen_folders:
                reasons.append(f"MR1_DRIVE_SUBTREE_FOLDER_CYCLE:{folder_id}")
                continue
            seen_folders.add(folder_id)
            if len(seen_folders) > 10_000:
                reasons.append("MR1_DRIVE_SUBTREE_TRAVERSAL_LIMIT_EXCEEDED")
                break
            try:
                actual = self._list_folder_files(
                    access_token=access_token,
                    folder_id=folder_id,
                )
            except Exception:
                reasons.append(f"MR1_DRIVE_EXACT_SET_LIST_FAILED:{folder_id}")
                readback[folder_id] = {
                    "expected_count": len(items) if folder_id == run_folder_id else 0,
                    "actual_count": None,
                    "list_succeeded": False,
                }
                continue
            actual_names = [str(remote.file_name or "") for remote in actual]
            if len(actual_names) != len(set(actual_names)):
                reasons.append(f"MR1_DRIVE_EXACT_SET_DUPLICATE_NAMES:{folder_id}")
            direct_files: list[GoogleDriveUploadResult] = []
            direct_folders: list[GoogleDriveUploadResult] = []
            for remote in actual:
                remote_id = str(remote.drive_file_id or "")
                if str(remote.drive_folder_id or "") != folder_id:
                    reasons.append(f"MR1_DRIVE_LISTED_PARENT_MISMATCH:{remote_id}")
                if remote.mime_type == "application/vnd.google-apps.folder":
                    direct_folders.append(remote)
                    if not remote_id:
                        reasons.append("MR1_DRIVE_SUBTREE_FOLDER_ID_MISSING")
                    elif remote_id in seen_folders or remote_id in queue:
                        reasons.append(f"MR1_DRIVE_SUBTREE_FOLDER_CYCLE:{remote_id}")
                    else:
                        queue.append(remote_id)
                        unexpected_folders.append(remote_id)
                else:
                    direct_files.append(remote)
                    remote_files.append(remote)
                    remote_file_parents.append(folder_id)
            readback[folder_id] = {
                "expected_count": len(items) if folder_id == run_folder_id else 0,
                "actual_count": len(actual),
                "direct_file_count": len(direct_files),
                "direct_folder_count": len(direct_folders),
                "actual_name_id_pairs": sorted(
                    [
                        [str(remote.file_name or ""), str(remote.drive_file_id or "")]
                        for remote in direct_files
                    ]
                ),
                "child_folder_ids": sorted(
                    str(remote.drive_file_id or "") for remote in direct_folders
                ),
                "list_succeeded": True,
            }

        if unexpected_folders:
            reasons.append("MR1_DRIVE_EXACT_SET_UNEXPECTED_NESTED_FOLDER")
        remote_ids = [str(remote.drive_file_id or "") for remote in remote_files]
        if "" in remote_ids or len(remote_ids) != len(set(remote_ids)):
            reasons.append("MR1_DRIVE_EXACT_SET_DUPLICATE_OR_EMPTY_REMOTE_ID")
        global_names = [str(remote.file_name or "") for remote in remote_files]
        if len(global_names) != len(set(global_names)):
            reasons.append("MR1_DRIVE_EXACT_SET_DUPLICATE_NAMES_IN_SUBTREE")
        actual_pairs = {
            (str(remote.file_name or ""), str(remote.drive_file_id or ""))
            for remote in remote_files
        }
        if len(remote_files) != len(items):
            reasons.append(f"MR1_DRIVE_EXACT_SET_COUNT_MISMATCH:{run_folder_id}")
        if actual_pairs != expected_pairs:
            reasons.append(f"MR1_DRIVE_EXACT_SET_MISMATCH:{run_folder_id}")
        if any(parent != run_folder_id for parent in remote_file_parents):
            reasons.append("MR1_DRIVE_EXACT_SET_UNEXPECTED_NESTED_FILE")

        by_id = {str(remote.drive_file_id or ""): remote for remote in remote_files}
        for item in items:
            entry = journal["entries"][item["archive_path"]]
            remote = by_id.get(str(entry["drive_file_id"]))
            if remote is None:
                continue
            _, remote_reasons = _verify_remote(
                item=item,
                remote=remote,
                expected_folder_id=run_folder_id,
                expected_drive_file_id=str(entry["drive_file_id"]),
            )
            reasons.extend(remote_reasons)
        journal["remote_set_readback"] = readback
        return sorted(set(reasons))


def _validate_items(
    values: Sequence[MR1ArchiveItem | Mapping[str, Any]],
    *,
    source_root: Path,
) -> list[dict[str, Any]]:
    if not values:
        raise ValueError("MR1_DRIVE_ARCHIVE_ITEMS_EMPTY")
    items = [MR1ArchiveItem.from_value(value) for value in values]
    roles: set[str] = set()
    names: set[str] = set()
    archive_paths: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in items:
        role_key = item.logical_role.strip().casefold()
        if not role_key:
            raise ValueError("MR1_DRIVE_ARCHIVE_ROLE_EMPTY")
        if role_key in roles:
            raise ValueError("MR1_DRIVE_ARCHIVE_DUPLICATE_ROLE")
        roles.add(role_key)
        archive_path = _validate_relative_path(item.archive_path, file_path=True)
        if (
            item.name != PurePosixPath(archive_path).name
            or "/" in item.name
            or "\\" in item.name
        ):
            raise ValueError("MR1_DRIVE_ARCHIVE_NAME_PATH_MISMATCH")
        name_key = item.name.casefold()
        if name_key in names:
            raise ValueError("MR1_DRIVE_ARCHIVE_DUPLICATE_NAME")
        names.add(name_key)
        path_key = archive_path.casefold()
        if path_key in archive_paths:
            raise ValueError("MR1_DRIVE_ARCHIVE_DUPLICATE_PATH")
        archive_paths.add(path_key)
        source = Path(item.source_path)
        if not source.is_absolute():
            source = source_root / source
        source = source.resolve(strict=True)
        _assert_contained(source, source_root, "MR1_DRIVE_SOURCE_PATH_ESCAPE")
        if not source.is_file() or source.is_symlink():
            raise ValueError("MR1_DRIVE_SOURCE_NOT_REGULAR_FILE")
        evidence = _file_evidence(source)
        expected = (item.size_bytes, item.sha256.lower(), item.md5.lower())
        if evidence != expected:
            raise RuntimeError(f"MR1_DRIVE_LOCAL_EVIDENCE_MISMATCH:{item.logical_role}")
        if (
            item.size_bytes < 0
            or not _is_hex(item.sha256, 64)
            or not _is_hex(item.md5, 32)
        ):
            raise ValueError("MR1_DRIVE_ARCHIVE_DIGEST_INVALID")
        normalized.append(
            {
                "logical_role": item.logical_role.strip(),
                "name": item.name,
                "source_path": str(source),
                "archive_path": archive_path,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256.lower(),
                "md5": item.md5.lower(),
            }
        )
    return sorted(normalized, key=lambda item: item["archive_path"])


def _manifest_payload(
    *,
    run_id: str,
    archive_identity: str,
    root_relative_path: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "MR1_DRIVE_ARCHIVE_MANIFEST_V1",
        "run_id": run_id,
        "archive_identity": archive_identity,
        "root_relative_path": root_relative_path,
        "item_count": len(items),
        "total_size_bytes": sum(int(item["size_bytes"]) for item in items),
        "items": items,
    }


def _verify_remote(
    *,
    item: dict[str, Any],
    remote: GoogleDriveUploadResult,
    expected_folder_id: str,
    expected_drive_file_id: str,
) -> tuple[dict[str, Any], list[str]]:
    role = item["logical_role"]
    remote_sha256 = str(remote.checksum_sha256 or "").lower() or None
    appendix = remote.technical_appendix or {}
    remote_md5 = str(appendix.get("md5_checksum") or "").lower() or None
    id_ok = remote.drive_file_id == expected_drive_file_id
    parent_ok = remote.drive_folder_id == expected_folder_id
    name_ok = remote.file_name == item["name"]
    size_ok = remote.size_bytes == item["size_bytes"]
    sha_ok = bool(remote_sha256 and remote_sha256 == item["sha256"])
    md5_ok = bool(remote_md5 and remote_md5 == item["md5"])
    checksum_ok = sha_ok or md5_ok
    reasons: list[str] = []
    if not id_ok:
        reasons.append(f"MR1_DRIVE_FILE_ID_MISMATCH:{role}")
    if not parent_ok:
        reasons.append(f"MR1_DRIVE_PARENT_MISMATCH:{role}")
    if not name_ok:
        reasons.append(f"MR1_DRIVE_NAME_MISMATCH:{role}")
    if not size_ok:
        reasons.append(f"MR1_DRIVE_SIZE_MISMATCH:{role}")
    if not checksum_ok:
        reasons.append(f"MR1_DRIVE_CHECKSUM_MISMATCH_OR_UNAVAILABLE:{role}")
    return (
        {
            "logical_role": role,
            "name": item["name"],
            "archive_path": item["archive_path"],
            "drive_file_id": expected_drive_file_id,
            "drive_folder_id": expected_folder_id,
            "local_size_bytes": item["size_bytes"],
            "remote_size_bytes": remote.size_bytes,
            "local_sha256": item["sha256"],
            "remote_sha256": remote_sha256,
            "local_md5": item["md5"],
            "remote_md5": remote_md5,
            "verification_method": "SHA256_PLUS_SIZE"
            if sha_ok
            else "MD5_PLUS_SIZE"
            if md5_ok
            else "FAILED",
            "verified": not reasons,
        },
        reasons,
    )


def _build_receipt(
    *,
    manifest: dict[str, Any],
    manifest_hash: str,
    run_folder_id: str | None,
    journal: dict[str, Any],
    reasons: list[str],
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for item in manifest["items"]:
        entry = journal["entries"][item["archive_path"]]
        verification = entry.get("verification")
        files.append(
            verification
            or {
                "logical_role": item["logical_role"],
                "name": item["name"],
                "archive_path": item["archive_path"],
                "drive_file_id": entry.get("drive_file_id"),
                "drive_folder_id": entry.get("folder_id"),
                "local_size_bytes": item["size_bytes"],
                "remote_size_bytes": None,
                "local_sha256": item["sha256"],
                "remote_sha256": None,
                "local_md5": item["md5"],
                "remote_md5": None,
                "verification_method": entry.get("state", "NOT_ATTEMPTED"),
                "verified": False,
            }
        )
    verified = not reasons and journal.get("remote_exact_set_verified") is True
    payload = {
        "schema_version": "MR1_DRIVE_ARCHIVE_RECEIPT_V1",
        "run_id": manifest["run_id"],
        "archive_identity": manifest["archive_identity"],
        "archive_manifest_hash": manifest_hash,
        "root_relative_path": manifest["root_relative_path"],
        "drive_folder_id": run_folder_id,
        "expected_item_count": manifest["item_count"],
        "verified_item_count": sum(bool(item["verified"]) for item in files),
        "remote_item_count": sum(
            int(value.get("actual_count") or 0)
            for value in (journal.get("remote_set_readback") or {}).values()
        ),
        "total_local_size_bytes": manifest["total_size_bytes"],
        "total_remote_size_bytes": sum(
            int(item["remote_size_bytes"] or 0) for item in files
        ),
        "items": manifest["items"],
        "files": files,
        "remote_exact_set_verified": bool(journal.get("remote_exact_set_verified")),
        "archive_state": "VERIFIED" if verified else "FAILED",
        "mismatch_reason_codes": sorted(set(reasons)),
        "provider_call_made": bool(journal.get("provider_call_made")),
        "transport": "GOOGLE_DRIVE_API",
        "verified_at": datetime.now(UTC).isoformat() if verified else None,
    }
    return {**payload, "receipt_hash": _stable_hash(payload)}


def _load_verified_receipt(
    *,
    receipt_path: Path,
    archive_identity: str,
    manifest_hash: str,
) -> dict[str, Any] | None:
    if not receipt_path.exists():
        return None
    receipt = _read_json_object(receipt_path)
    if (
        receipt.get("archive_identity") != archive_identity
        or receipt.get("archive_manifest_hash") != manifest_hash
    ):
        raise RuntimeError("MR1_DRIVE_ARCHIVE_RECEIPT_CONFLICT")
    supplied_hash = str(receipt.get("receipt_hash") or "")
    expected_hash = _stable_hash(
        {key: value for key, value in receipt.items() if key != "receipt_hash"}
    )
    if supplied_hash != expected_hash:
        raise RuntimeError("MR1_DRIVE_ARCHIVE_RECEIPT_HASH_INVALID")
    return receipt if receipt.get("archive_state") == "VERIFIED" else None


def _validate_relative_path(value: str, *, file_path: bool) -> str:
    if not value or value.startswith("/") or "\\" in value or "\x00" in value:
        raise ValueError("MR1_DRIVE_ARCHIVE_PATH_INVALID")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("MR1_DRIVE_ARCHIVE_PATH_NOT_CONTAINED")
    canonical = str(path)
    if not path.parts or canonical != value:
        raise ValueError("MR1_DRIVE_ARCHIVE_PATH_NOT_CANONICAL")
    if file_path and (value.endswith("/") or not path.name):
        raise ValueError("MR1_DRIVE_ARCHIVE_FILE_PATH_INVALID")
    return canonical


def _validate_run_id(run_id: str) -> None:
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("MR1_DRIVE_RUN_ID_INVALID")


def _assert_contained(path: Path, root: Path, code: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError(code) from None


def _is_hex(value: str, length: int) -> bool:
    return len(value) == length and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def _file_evidence(path: Path) -> tuple[int, str, str]:
    if not path.is_file():
        raise ValueError("MR1_DRIVE_SOURCE_NOT_FILE")
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            sha256.update(chunk)
            md5.update(chunk)
    return size, sha256.hexdigest(), md5.hexdigest()


def _stable_hash(value: Any) -> str:
    if isinstance(value, str):
        payload = value
    else:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_provider_reason(exc: Exception) -> str:
    known = str(exc)
    if known.startswith("MR1_DRIVE_") and " " not in known:
        return known
    return "MR1_DRIVE_PROVIDER_OPERATION_FAILED"


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise RuntimeError("MR1_DRIVE_STATE_JSON_INVALID") from None
    if not isinstance(payload, dict):
        raise RuntimeError("MR1_DRIVE_STATE_JSON_INVALID")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(part, path)
        _fsync_directory(path.parent)
    finally:
        part.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _list_google_drive_folder_files(
    *,
    access_token: str,
    folder_id: str,
) -> list[GoogleDriveUploadResult]:
    """List every direct child so exact-set verification can walk the subtree."""

    escaped_parent = folder_id.replace("'", "\\'")
    page_token: str | None = None
    results: list[GoogleDriveUploadResult] = []
    while True:
        query: dict[str, str] = {
            "q": (f"'{escaped_parent}' in parents and trashed=false"),
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
            query["pageToken"] = page_token
        request = urlrequest.Request(
            f"{GOOGLE_DRIVE_FILES_URL}?{urlparse.urlencode(query)}",
            method="GET",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urlrequest.urlopen(
            request,
            timeout=20,
            context=_DRIVE_SSL_CONTEXT,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list):
            raise RuntimeError("MR1_DRIVE_FOLDER_LIST_RESPONSE_INVALID")
        for item in files:
            if not isinstance(item, dict):
                raise RuntimeError("MR1_DRIVE_FOLDER_LIST_ITEM_INVALID")
            parents = (
                item.get("parents") if isinstance(item.get("parents"), list) else []
            )
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
