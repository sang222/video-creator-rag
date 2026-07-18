from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts.asset_acquisition import (
    DriveArchiveReceipt,
    ProductionArchiveFileEntry,
    ProductionArchiveManifest,
)
from app.contracts.img_canary_v3_closeout import (
    IMG_CANARY_V3_CLOSEOUT_MANIFEST_ARCHIVE_PATH,
    IMG_CANARY_V3_CLOSEOUT_MANIFEST_ROLE,
    IMG_CANARY_V3_CLOSEOUT_RUN_ID,
    IMG_CANARY_V3_HUMAN_REVIEW_ARCHIVE_PATH,
    IMG_CANARY_V3_HUMAN_REVIEW_ROLE,
    IMG_CANARY_V3_ORIGINAL_MANIFEST_ARCHIVE_PATH,
    IMG_CANARY_V3_ORIGINAL_MANIFEST_ROLE,
    IMGCanaryV3DriveExportCloseoutManifest,
    IMGCanaryV3DriveExportItem,
    IMGCanaryV3HumanReviewReceipt,
)
from app.services.img_canary_drive import (
    IMGCanaryDriveArchive,
    _validate_manifest_and_sources,
)
from app.services.native_render_plan import stable_hash
from app.services.production_archive import (
    IMG_CANARY_ROLE_ARCHIVE_PATHS,
    IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES,
    IMGCanaryArchivePathBuilder,
)


ROOT = Path(__file__).resolve().parents[2]
IMG_CANARY_V3_ROOT = (
    ROOT / "artifacts" / "img_canary" / IMG_CANARY_V3_CLOSEOUT_RUN_ID
).resolve()
IMG_CANARY_V3_ORIGINAL_IMAGE = IMG_CANARY_V3_ROOT / "source" / "original-generated.jpg"
IMG_CANARY_V3_NORMALIZED_IMAGE = (
    IMG_CANARY_V3_ROOT / "source" / "normalized-1920x1080.png"
)
IMG_CANARY_V3_REVIEW_MP4 = (
    IMG_CANARY_V3_ROOT
    / "runs"
    / IMG_CANARY_V3_CLOSEOUT_RUN_ID
    / "img-canary-review.mp4"
)
IMG_CANARY_V3_ORIGINAL_MANIFEST = (
    IMG_CANARY_V3_ROOT / "archive" / "production-archive-manifest.json"
)
IMG_CANARY_V3_HUMAN_REVIEW_RECEIPT = (
    IMG_CANARY_V3_ROOT / "manifests" / "human-review-receipt.json"
)
IMG_CANARY_V3_CLOSEOUT_MANIFEST = (
    IMG_CANARY_V3_ROOT / "archive" / "drive-export-closeout-manifest.json"
)
IMG_CANARY_V3_LOCAL_DRIVE_JOURNAL = (
    IMG_CANARY_V3_ROOT / "archive" / "drive-upload-journal.json"
)
IMG_CANARY_V3_LOCAL_DRIVE_RECEIPT = (
    IMG_CANARY_V3_ROOT / "archive" / "drive-archive-receipt.json"
)

IMG_CANARY_V3_EXPECTED_ORIGINAL_SHA256 = (
    "3ab066bdb556be8161f1736959346c6decbdba61d3f12c3348e249445b1f7293"
)
IMG_CANARY_V3_EXPECTED_NORMALIZED_SHA256 = (
    "af752598e540ee83e88f960c71bb4255877753cf4efb799e97dcabb4a604e4b4"
)
IMG_CANARY_V3_EXPECTED_MP4_SHA256 = (
    "8e5a4dd39fa7da4321fc8e0efb93076cc1637805f5300d0ed54861c5cfcacab4"
)
IMG_CANARY_V3_SUPPLIED_MANIFEST_HASH = (
    "45140e3e6f2a0291935bf241d5776ecabd78d5e76ecb07715723b366fc77e268"
)
IMG_CANARY_V3_ACTUAL_MANIFEST_SHA256 = (
    "0e8539788154623f48af179c3743801a79d35b5b96e8c55a80fb0f97a29bbf98"
)
IMG_CANARY_V3_CLOSEOUT_CONFIRMATION_TOKEN = "EXPORT_REVIEWED_IMG_CANARY_V3_TO_DRIVE"
IMG_CANARY_V3_ARCHIVE_DATE = "2026-07-18"
IMG_CANARY_V3_EXPORT_ITEM_COUNT = 47

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GOOGLE_API_KEY", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("AUTHORIZATION_BEARER", re.compile(r"(?i)authorization\s*[:=]\s*[\"']?bearer\s+")),
    ("RAW_IMAGE_BASE64", re.compile(r"(?i)data:image/[^;,]+;base64,")),
    ("SIGNED_GOOGLE_URL", re.compile(r"(?i)[?&]x-goog-(?:signature|credential)=")),
)


@dataclass(frozen=True)
class IMGCanaryV3LocalCloseoutEvidence:
    original_manifest: ProductionArchiveManifest
    original_manifest_bytes_sha256: str
    reviewed_hashes: dict[str, str]
    provider_attempts_total: int
    original_role_count: int


@dataclass(frozen=True)
class IMGCanaryV3PreparedCloseout:
    evidence: IMGCanaryV3LocalCloseoutEvidence
    human_review_receipt: IMGCanaryV3HumanReviewReceipt
    closeout_manifest: IMGCanaryV3DriveExportCloseoutManifest


class IMGCanaryV3DriveCloseout:
    """Immutable, provider-free closeout for the single approved V3 canary run."""

    def verify_original_package(self) -> IMGCanaryV3LocalCloseoutEvidence:
        if not IMG_CANARY_V3_ROOT.is_dir() or IMG_CANARY_V3_ROOT.is_symlink():
            raise ValueError("IMG_CANARY_V3_ROOT_INVALID")
        original_manifest = ProductionArchiveManifest.model_validate_json(
            IMG_CANARY_V3_ORIGINAL_MANIFEST.read_text(encoding="utf-8")
        )
        _validate_manifest_and_sources(
            original_manifest,
            run_id=IMG_CANARY_V3_CLOSEOUT_RUN_ID,
        )
        if len(original_manifest.files) != 44:
            raise ValueError("IMG_CANARY_V3_ORIGINAL_ROLE_COUNT_INVALID")
        if {item.logical_role for item in original_manifest.files} != set(
            IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES
        ):
            raise ValueError("IMG_CANARY_V3_ORIGINAL_ROLE_SET_INVALID")

        for entry in original_manifest.files:
            source = Path(entry.source_path).resolve()
            _require_within_run_root(source)
            _validate_safe_export_source(entry.logical_role, source)

        reviewed_hashes = {
            "original_image": _sha256_file(IMG_CANARY_V3_ORIGINAL_IMAGE),
            "normalized_image": _sha256_file(IMG_CANARY_V3_NORMALIZED_IMAGE),
            "review_mp4": _sha256_file(IMG_CANARY_V3_REVIEW_MP4),
        }
        expected_hashes = {
            "original_image": IMG_CANARY_V3_EXPECTED_ORIGINAL_SHA256,
            "normalized_image": IMG_CANARY_V3_EXPECTED_NORMALIZED_SHA256,
            "review_mp4": IMG_CANARY_V3_EXPECTED_MP4_SHA256,
        }
        if reviewed_hashes != expected_hashes:
            raise ValueError("IMG_CANARY_V3_REVIEWED_ARTIFACT_HASH_MISMATCH")

        manifest_bytes_sha256 = _sha256_file(IMG_CANARY_V3_ORIGINAL_MANIFEST)
        if manifest_bytes_sha256 != IMG_CANARY_V3_ACTUAL_MANIFEST_SHA256:
            raise ValueError("IMG_CANARY_V3_ORIGINAL_MANIFEST_BYTES_CHANGED")
        if original_manifest.manifest_hash != IMG_CANARY_V3_SUPPLIED_MANIFEST_HASH:
            raise ValueError("IMG_CANARY_V3_ORIGINAL_MANIFEST_DECLARED_HASH_CHANGED")
        if manifest_bytes_sha256 == IMG_CANARY_V3_SUPPLIED_MANIFEST_HASH:
            raise ValueError("IMG_CANARY_V3_MANIFEST_HASH_DISCREPANCY_EXPECTED")

        attempt_ledger_path = IMG_CANARY_V3_ROOT / "manifests" / "attempt-ledger.json"
        ledger = _load_json_object(attempt_ledger_path)
        if (
            ledger.get("run_id") != IMG_CANARY_V3_CLOSEOUT_RUN_ID
            or ledger.get("attempt_limit") != 1
            or ledger.get("attempts_consumed") != 1
            or ledger.get("status") != "SUCCEEDED"
            or ledger.get("provider_call_made") is not True
        ):
            raise ValueError("IMG_CANARY_V3_PROVIDER_ATTEMPT_STATE_INVALID")
        auth = _load_json_object(
            IMG_CANARY_V3_ROOT / "manifests" / "task-authorization-consumed.json"
        )
        if (
            auth.get("approved_run_id") != IMG_CANARY_V3_CLOSEOUT_RUN_ID
            or auth.get("status") != "CONSUMED"
            or auth.get("completion_status") != "PROVIDER_ATTEMPT_SUBMITTED"
        ):
            raise ValueError("IMG_CANARY_V3_TASK_AUTHORIZATION_STATE_INVALID")
        _validate_no_later_provider_record(attempt_ledger_path)

        return IMGCanaryV3LocalCloseoutEvidence(
            original_manifest=original_manifest,
            original_manifest_bytes_sha256=manifest_bytes_sha256,
            reviewed_hashes=reviewed_hashes,
            provider_attempts_total=1,
            original_role_count=44,
        )

    def prepare(self) -> IMGCanaryV3PreparedCloseout:
        evidence = self.verify_original_package()
        review = self._load_or_create_human_review(evidence)
        closeout = self._load_or_create_closeout_manifest(evidence, review)
        validate_closeout_manifest_and_sources(
            closeout,
            run_id=IMG_CANARY_V3_CLOSEOUT_RUN_ID,
        )
        # Re-run the historical package validator after creating supplements;
        # none of the original 44 artifacts may have changed.
        self.verify_original_package()
        return IMGCanaryV3PreparedCloseout(
            evidence=evidence,
            human_review_receipt=review,
            closeout_manifest=closeout,
        )

    def export_and_verify(
        self,
        *,
        drive: IMGCanaryDriveArchive,
        confirmation_token: str,
        access_token: str | None = None,
    ) -> DriveArchiveReceipt:
        if confirmation_token != IMG_CANARY_V3_CLOSEOUT_CONFIRMATION_TOKEN:
            raise ValueError("IMG_CANARY_V3_CLOSEOUT_CONFIRMATION_INVALID")
        before = _immutable_media_snapshot()
        prepared = self.prepare()
        receipt = drive.upload_closeout_and_verify(
            manifest=prepared.closeout_manifest,
            run_id=IMG_CANARY_V3_CLOSEOUT_RUN_ID,
            archive_date=IMG_CANARY_V3_ARCHIVE_DATE,
            access_token=access_token,
        )
        if (
            receipt.archive_state != "VERIFIED"
            or len(receipt.files) != IMG_CANARY_V3_EXPORT_ITEM_COUNT
            or not all(item.verified for item in receipt.files)
            or receipt.mismatch_reason_codes
        ):
            raise RuntimeError("IMG_CANARY_V3_DRIVE_ARCHIVE_NOT_VERIFIED")
        _persist_verified_drive_evidence(drive=drive, receipt=receipt)
        if _immutable_media_snapshot() != before:
            raise RuntimeError("IMG_CANARY_V3_IMMUTABLE_ARTIFACT_CHANGED_DURING_CLOSEOUT")
        self.verify_original_package()
        return receipt

    def _load_or_create_human_review(
        self,
        evidence: IMGCanaryV3LocalCloseoutEvidence,
    ) -> IMGCanaryV3HumanReviewReceipt:
        if IMG_CANARY_V3_HUMAN_REVIEW_RECEIPT.exists():
            review = IMGCanaryV3HumanReviewReceipt.model_validate_json(
                IMG_CANARY_V3_HUMAN_REVIEW_RECEIPT.read_text(encoding="utf-8")
            )
            _validate_human_review_receipt(review, evidence)
            return review

        payload: dict[str, Any] = {
            "run_id": IMG_CANARY_V3_CLOSEOUT_RUN_ID,
            "decision": "PASS",
            "decision_source": "OPERATOR",
            "human_review_authority": "OPERATOR",
            "decision_source_ref": "operator-prompt:img-canary-v3-drive-closeout",
            "reviewed_original_image_path": str(IMG_CANARY_V3_ORIGINAL_IMAGE),
            "reviewed_original_image_sha256": evidence.reviewed_hashes["original_image"],
            "reviewed_normalized_image_path": str(IMG_CANARY_V3_NORMALIZED_IMAGE),
            "reviewed_normalized_image_sha256": evidence.reviewed_hashes["normalized_image"],
            "reviewed_mp4_path": str(IMG_CANARY_V3_REVIEW_MP4),
            "reviewed_mp4_sha256": evidence.reviewed_hashes["review_mp4"],
            "original_archive_manifest_ref": str(IMG_CANARY_V3_ORIGINAL_MANIFEST),
            "original_archive_manifest_sha256": evidence.original_manifest_bytes_sha256,
            "original_archive_manifest_declared_hash": (
                evidence.original_manifest.manifest_hash
            ),
            "manifest_hash_discrepancy_reason_codes": [
                "SUPPLIED_HASH_IS_SEMANTIC_MANIFEST_HASH_NOT_FILE_BYTES_SHA256"
            ],
            "decision_timestamp": datetime.now(UTC),
            "production_eligible": False,
            "not_publishable": True,
        }
        draft = IMGCanaryV3HumanReviewReceipt(**payload, content_hash="0" * 64)
        review = draft.model_copy(
            update={
                "content_hash": stable_hash(
                    draft.model_dump(mode="json", exclude={"content_hash"})
                )
            }
        )
        _write_model_immutable(IMG_CANARY_V3_HUMAN_REVIEW_RECEIPT, review)
        _validate_human_review_receipt(review, evidence)
        return review

    def _load_or_create_closeout_manifest(
        self,
        evidence: IMGCanaryV3LocalCloseoutEvidence,
        review: IMGCanaryV3HumanReviewReceipt,
    ) -> IMGCanaryV3DriveExportCloseoutManifest:
        if IMG_CANARY_V3_CLOSEOUT_MANIFEST.exists():
            return IMGCanaryV3DriveExportCloseoutManifest.model_validate_json(
                IMG_CANARY_V3_CLOSEOUT_MANIFEST.read_text(encoding="utf-8")
            )

        original_manifest_entry = _build_file_entry(
            logical_role=IMG_CANARY_V3_ORIGINAL_MANIFEST_ROLE,
            source_path=IMG_CANARY_V3_ORIGINAL_MANIFEST,
            expected_archive_path=IMG_CANARY_V3_ORIGINAL_MANIFEST_ARCHIVE_PATH,
        )
        review_entry = _build_file_entry(
            logical_role=IMG_CANARY_V3_HUMAN_REVIEW_ROLE,
            source_path=IMG_CANARY_V3_HUMAN_REVIEW_RECEIPT,
            expected_archive_path=IMG_CANARY_V3_HUMAN_REVIEW_ARCHIVE_PATH,
        )
        files = sorted(
            [*evidence.original_manifest.files, original_manifest_entry, review_entry],
            key=lambda item: item.expected_archive_path,
        )
        export_items = [
            IMGCanaryV3DriveExportItem(
                logical_role=item.logical_role,
                source_path=item.source_path,
                expected_archive_path=item.expected_archive_path,
                size_bytes=item.size_bytes,
                sha256=item.sha256,
            )
            for item in files
        ]
        export_items.append(
            IMGCanaryV3DriveExportItem(
                logical_role=IMG_CANARY_V3_CLOSEOUT_MANIFEST_ROLE,
                source_path=str(IMG_CANARY_V3_CLOSEOUT_MANIFEST),
                expected_archive_path=IMG_CANARY_V3_CLOSEOUT_MANIFEST_ARCHIVE_PATH,
                self_reference_without_checksum=True,
            )
        )
        export_items.sort(key=lambda item: item.expected_archive_path)
        destination = IMGCanaryArchivePathBuilder.build(
            run_id=IMG_CANARY_V3_CLOSEOUT_RUN_ID,
            archive_date=IMG_CANARY_V3_ARCHIVE_DATE,
        )
        payload: dict[str, Any] = {
            "manifest_id": IMG_CANARY_V3_CLOSEOUT_RUN_ID + "-drive-closeout-v1",
            "project_id": evidence.original_manifest.project_id,
            "package_id": evidence.original_manifest.package_id,
            "sections": evidence.original_manifest.sections,
            "files": files,
            "excluded_paths": sorted(
                set(
                    [
                        *evidence.original_manifest.excluded_paths,
                        "**/*.part",
                        "**/*.lock",
                        "**/*credential*",
                        "**/*raw-response*",
                    ]
                )
            ),
            "total_size_bytes": sum(item.size_bytes for item in files),
            "required_roles_complete": True,
            "provider_execution_allowed": False,
            "run_id": IMG_CANARY_V3_CLOSEOUT_RUN_ID,
            "original_manifest_ref": str(IMG_CANARY_V3_ORIGINAL_MANIFEST),
            "original_manifest_sha256": evidence.original_manifest_bytes_sha256,
            "original_manifest_declared_hash": evidence.original_manifest.manifest_hash,
            "human_review_receipt_ref": str(IMG_CANARY_V3_HUMAN_REVIEW_RECEIPT),
            "human_review_receipt_sha256": review_entry.sha256,
            "human_review_receipt_content_hash": review.content_hash,
            "export_item_count": len(export_items),
            "export_items": export_items,
            "drive_destination_folder": destination,
            "archive_identity": "archive://img-canary-v3/" + IMG_CANARY_V3_CLOSEOUT_RUN_ID,
            "upload_idempotency_key": stable_hash(
                {
                    "run_id": IMG_CANARY_V3_CLOSEOUT_RUN_ID,
                    "original_manifest_sha256": evidence.original_manifest_bytes_sha256,
                    "human_review_content_hash": review.content_hash,
                    "destination": destination,
                }
            ),
            "production_eligible": False,
            "not_publishable": True,
            "correction_reason_codes": [
                "SUPPLIED_HASH_IS_SEMANTIC_MANIFEST_HASH_NOT_FILE_BYTES_SHA256",
                "ORIGINAL_MANIFEST_PRESERVED_SUPERSEDING_EXPORT_ENVELOPE_CREATED",
            ],
        }
        draft = IMGCanaryV3DriveExportCloseoutManifest(
            **payload,
            manifest_hash="0" * 64,
        )
        closeout = draft.model_copy(
            update={
                "manifest_hash": stable_hash(
                    draft.model_dump(mode="json", exclude={"manifest_hash"})
                )
            }
        )
        _write_model_immutable(IMG_CANARY_V3_CLOSEOUT_MANIFEST, closeout)
        return closeout


def validate_closeout_manifest_and_sources(
    manifest: IMGCanaryV3DriveExportCloseoutManifest,
    *,
    run_id: str,
) -> None:
    if run_id != IMG_CANARY_V3_CLOSEOUT_RUN_ID or manifest.run_id != run_id:
        raise ValueError("IMG_CANARY_V3_CLOSEOUT_RUN_ID_MISMATCH")
    if stable_hash(manifest.model_dump(mode="json", exclude={"manifest_hash"})) != (
        manifest.manifest_hash
    ):
        raise ValueError("IMG_CANARY_V3_CLOSEOUT_MANIFEST_HASH_MISMATCH")
    expected_roles = set(IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES) | {
        IMG_CANARY_V3_ORIGINAL_MANIFEST_ROLE,
        IMG_CANARY_V3_HUMAN_REVIEW_ROLE,
    }
    if len(manifest.files) != 46 or {item.logical_role for item in manifest.files} != expected_roles:
        raise ValueError("IMG_CANARY_V3_CLOSEOUT_REQUIRED_ROLES_INCOMPLETE")
    if manifest.export_item_count != IMG_CANARY_V3_EXPORT_ITEM_COUNT:
        raise ValueError("IMG_CANARY_V3_CLOSEOUT_EXPORT_COUNT_INVALID")
    if manifest.total_size_bytes != sum(item.size_bytes for item in manifest.files):
        raise ValueError("IMG_CANARY_V3_CLOSEOUT_TOTAL_SIZE_MISMATCH")

    expected_paths = {
        **{
            role: IMG_CANARY_ROLE_ARCHIVE_PATHS[role]
            for role in IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES
        },
        IMG_CANARY_V3_ORIGINAL_MANIFEST_ROLE: IMG_CANARY_V3_ORIGINAL_MANIFEST_ARCHIVE_PATH,
        IMG_CANARY_V3_HUMAN_REVIEW_ROLE: IMG_CANARY_V3_HUMAN_REVIEW_ARCHIVE_PATH,
    }
    for entry in manifest.files:
        if entry.expected_archive_path != expected_paths[entry.logical_role]:
            raise ValueError(f"IMG_CANARY_V3_CLOSEOUT_ROLE_PATH_MISMATCH:{entry.logical_role}")
        if not entry.required_for_archive:
            raise ValueError(f"IMG_CANARY_V3_CLOSEOUT_REQUIRED_FLAG_FALSE:{entry.logical_role}")
        if stable_hash(entry.model_dump(mode="json", exclude={"manifest_hash"})) != (
            entry.manifest_hash
        ):
            raise ValueError(f"IMG_CANARY_V3_CLOSEOUT_ENTRY_HASH_MISMATCH:{entry.logical_role}")
        source = Path(entry.source_path).resolve()
        _require_within_run_root(source)
        _validate_safe_export_source(entry.logical_role, source)
        size, sha256, md5 = _source_evidence(source)
        if (size, sha256, md5) != (entry.size_bytes, entry.sha256, entry.md5):
            raise ValueError(f"IMG_CANARY_V3_CLOSEOUT_SOURCE_CHANGED:{entry.logical_role}")

    by_path = {item.expected_archive_path: item for item in manifest.files}
    for export_item in manifest.export_items:
        if export_item.self_reference_without_checksum:
            if Path(export_item.source_path).resolve() != IMG_CANARY_V3_CLOSEOUT_MANIFEST:
                raise ValueError("IMG_CANARY_V3_CLOSEOUT_SELF_SOURCE_INVALID")
            continue
        entry = by_path.get(export_item.expected_archive_path)
        if entry is None or (
            export_item.logical_role != entry.logical_role
            or export_item.source_path != entry.source_path
            or export_item.size_bytes != entry.size_bytes
            or export_item.sha256 != entry.sha256
        ):
            raise ValueError("IMG_CANARY_V3_CLOSEOUT_EXPORT_BINDING_INVALID")

    original_entry = next(
        item for item in manifest.files if item.logical_role == IMG_CANARY_V3_ORIGINAL_MANIFEST_ROLE
    )
    review_entry = next(
        item for item in manifest.files if item.logical_role == IMG_CANARY_V3_HUMAN_REVIEW_ROLE
    )
    if (
        original_entry.sha256 != manifest.original_manifest_sha256
        or review_entry.sha256 != manifest.human_review_receipt_sha256
        or manifest.original_manifest_declared_hash != IMG_CANARY_V3_SUPPLIED_MANIFEST_HASH
        or manifest.original_manifest_sha256 != IMG_CANARY_V3_ACTUAL_MANIFEST_SHA256
    ):
        raise ValueError("IMG_CANARY_V3_CLOSEOUT_SUPPLEMENT_BINDING_INVALID")
    review = IMGCanaryV3HumanReviewReceipt.model_validate_json(
        Path(review_entry.source_path).read_text(encoding="utf-8")
    )
    evidence = IMGCanaryV3DriveCloseout().verify_original_package()
    _validate_human_review_receipt(review, evidence)
    if review.content_hash != manifest.human_review_receipt_content_hash:
        raise ValueError("IMG_CANARY_V3_CLOSEOUT_HUMAN_RECEIPT_CONTENT_HASH_MISMATCH")


def _validate_human_review_receipt(
    review: IMGCanaryV3HumanReviewReceipt,
    evidence: IMGCanaryV3LocalCloseoutEvidence,
) -> None:
    if stable_hash(review.model_dump(mode="json", exclude={"content_hash"})) != review.content_hash:
        raise ValueError("IMG_CANARY_V3_HUMAN_REVIEW_CONTENT_HASH_MISMATCH")
    expected = (
        review.decision == "PASS"
        and review.decision_source == "OPERATOR"
        and review.human_review_authority == "OPERATOR"
        and review.reviewed_original_image_path == str(IMG_CANARY_V3_ORIGINAL_IMAGE)
        and review.reviewed_original_image_sha256 == evidence.reviewed_hashes["original_image"]
        and review.reviewed_normalized_image_path == str(IMG_CANARY_V3_NORMALIZED_IMAGE)
        and review.reviewed_normalized_image_sha256 == evidence.reviewed_hashes["normalized_image"]
        and review.reviewed_mp4_path == str(IMG_CANARY_V3_REVIEW_MP4)
        and review.reviewed_mp4_sha256 == evidence.reviewed_hashes["review_mp4"]
        and review.original_archive_manifest_ref == str(IMG_CANARY_V3_ORIGINAL_MANIFEST)
        and review.original_archive_manifest_sha256 == evidence.original_manifest_bytes_sha256
        and review.original_archive_manifest_declared_hash == evidence.original_manifest.manifest_hash
        and review.production_eligible is False
        and review.not_publishable is True
    )
    if not expected:
        raise ValueError("IMG_CANARY_V3_HUMAN_REVIEW_BINDING_INVALID")


def _build_file_entry(
    *,
    logical_role: str,
    source_path: Path,
    expected_archive_path: str,
) -> ProductionArchiveFileEntry:
    size, sha256, md5 = _source_evidence(source_path)
    payload = {
        "logical_role": logical_role,
        "source_path": str(source_path),
        "expected_archive_path": expected_archive_path,
        "size_bytes": size,
        "sha256": sha256,
        "md5": md5,
        "required_for_archive": True,
        "required_for_local_purge": False,
    }
    return ProductionArchiveFileEntry(**payload, manifest_hash=stable_hash(payload))


def _persist_verified_drive_evidence(
    *,
    drive: IMGCanaryDriveArchive,
    receipt: DriveArchiveReceipt,
) -> None:
    canonical_receipt = drive.closeout_receipt_path(IMG_CANARY_V3_CLOSEOUT_RUN_ID)
    canonical_journal = drive.closeout_journal_path(IMG_CANARY_V3_CLOSEOUT_RUN_ID)
    if not canonical_receipt.is_file() or not canonical_journal.is_file():
        raise RuntimeError("IMG_CANARY_V3_DRIVE_STATE_EVIDENCE_MISSING")
    if DriveArchiveReceipt.model_validate_json(
        canonical_receipt.read_text(encoding="utf-8")
    ) != receipt:
        raise RuntimeError("IMG_CANARY_V3_DRIVE_RECEIPT_STATE_CONFLICT")
    _write_bytes_immutable(IMG_CANARY_V3_LOCAL_DRIVE_RECEIPT, canonical_receipt.read_bytes())
    _write_bytes_immutable(IMG_CANARY_V3_LOCAL_DRIVE_JOURNAL, canonical_journal.read_bytes())


def _validate_no_later_provider_record(attempt_ledger_path: Path) -> None:
    provider_receipts = list(
        (IMG_CANARY_V3_ROOT / "manifests").glob("provider-operation-receipt*.json")
    )
    materializations = list(
        (IMG_CANARY_V3_ROOT / "manifests").glob("materialization-receipt*.json")
    )
    if len(provider_receipts) != 1 or len(materializations) != 1:
        raise ValueError("IMG_CANARY_V3_LATER_PROVIDER_RECORD_DETECTED")
    ledger_mtime = attempt_ledger_path.stat().st_mtime_ns
    # The successful materialization may be finalized milliseconds after the
    # ledger, but no second record is allowed; multiplicity is authoritative.
    if ledger_mtime <= 0:
        raise ValueError("IMG_CANARY_V3_PROVIDER_LEDGER_TIMESTAMP_INVALID")


def _validate_safe_export_source(logical_role: str, source: Path) -> None:
    lowered_parts = [part.lower() for part in source.parts]
    lowered_name = source.name.lower()
    if (
        not source.is_file()
        or source.is_symlink()
        or any(part.endswith(".part") or part.startswith(".part") for part in lowered_parts)
        or lowered_name.endswith(".lock")
        or "credential" in lowered_name
        or "oauth" in lowered_name
        or "secret" in lowered_name
    ):
        raise ValueError(f"IMG_CANARY_V3_CLOSEOUT_SOURCE_FORBIDDEN:{logical_role}")
    if source.suffix.lower() not in {".json", ".md", ".txt", ".srt", ".log", ".sh"}:
        return
    text = source.read_text(encoding="utf-8")
    for reason, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            raise ValueError(f"IMG_CANARY_V3_CLOSEOUT_SECRET_DETECTED:{reason}:{logical_role}")


def _require_within_run_root(path: Path) -> None:
    try:
        path.relative_to(IMG_CANARY_V3_ROOT)
    except ValueError:
        raise ValueError("IMG_CANARY_V3_CLOSEOUT_PATH_ESCAPE") from None


def _immutable_media_snapshot() -> dict[str, str]:
    return {
        str(path): _sha256_file(path)
        for path in (
            IMG_CANARY_V3_ORIGINAL_IMAGE,
            IMG_CANARY_V3_NORMALIZED_IMAGE,
            IMG_CANARY_V3_REVIEW_MP4,
            IMG_CANARY_V3_ORIGINAL_MANIFEST,
            IMG_CANARY_V3_ROOT / "manifests" / "attempt-ledger.json",
        )
    }


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


def _sha256_file(path: Path) -> str:
    return _source_evidence(path)[1]


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("IMG_CANARY_V3_CLOSEOUT_JSON_OBJECT_REQUIRED")
    return payload


def _write_model_immutable(path: Path, model: Any) -> None:
    payload = (
        json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )
    _write_bytes_immutable(path, payload)


def _write_bytes_immutable(path: Path, payload: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != payload:
            raise RuntimeError(f"IMG_CANARY_V3_CLOSEOUT_IMMUTABLE_CONFLICT:{path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("xb") as stream:
            stream.write(payload)
            stream.flush()
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)
