from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.contracts.asset_acquisition import DriveArchiveReceipt, LocalCleanupReceipt
from app.services.native_render_plan import stable_hash


AUDIT_DIRECTORIES = {"manifests", "qc", "publish"}


class LocalCleanupService:
    """AS1 cleanup executor is intentionally fixture-only."""

    def evaluate(self, *, project_id: str, archive_receipt: DriveArchiveReceipt) -> LocalCleanupReceipt:
        eligible = archive_receipt.archive_state == "VERIFIED" and not archive_receipt.mismatch_reason_codes
        payload = {
            "project_id": project_id,
            "archive_receipt_ref": archive_receipt.archive_manifest_ref,
            "archive_receipt_hash": archive_receipt.receipt_hash,
            "eligibility_status": "ELIGIBLE" if eligible else "INELIGIBLE",
            "deleted_files": [],
            "retained_files": [],
            "failed_deletions": [],
            "bytes_reclaimed": 0,
            "cleanup_status": "ELIGIBLE_NOT_EXECUTED" if eligible else "BLOCKED",
            "executed_at": None,
        }
        return LocalCleanupReceipt(**payload, receipt_hash=stable_hash(payload))

    def execute_fixture_only(
        self,
        *,
        project_id: str,
        workspace_path: Path,
        archive_receipt: DriveArchiveReceipt,
        candidate_files: list[Path],
        fixture_only: bool,
    ) -> LocalCleanupReceipt:
        if not fixture_only or archive_receipt.transport != "LOCAL_FIXTURE_ONLY":
            raise PermissionError("AS1_REAL_WORKSPACE_PURGE_FORBIDDEN")
        eligibility = self.evaluate(project_id=project_id, archive_receipt=archive_receipt)
        if eligibility.eligibility_status != "ELIGIBLE":
            return eligibility
        root = workspace_path.resolve()
        deleted: list[str] = []
        retained: list[str] = []
        failed: list[str] = []
        bytes_reclaimed = 0
        for candidate in candidate_files:
            resolved = candidate.resolve(strict=False)
            if root != resolved and root not in resolved.parents:
                failed.append(str(candidate))
                continue
            relative = resolved.relative_to(root)
            if relative.parts and relative.parts[0] in AUDIT_DIRECTORIES:
                retained.append(str(resolved))
                continue
            if not resolved.exists():
                retained.append(str(resolved))
                continue
            try:
                size = resolved.stat().st_size if resolved.is_file() else 0
                if resolved.is_dir():
                    retained.append(str(resolved))
                    continue
                resolved.unlink()
                deleted.append(str(resolved))
                bytes_reclaimed += size
            except OSError:
                failed.append(str(resolved))
        status = "FAILED" if failed else "COMPLETED" if deleted else "NOOP_IDEMPOTENT"
        payload = {
            "project_id": project_id,
            "archive_receipt_ref": archive_receipt.archive_manifest_ref,
            "archive_receipt_hash": archive_receipt.receipt_hash,
            "eligibility_status": "ELIGIBLE",
            "deleted_files": deleted,
            "retained_files": retained,
            "failed_deletions": failed,
            "bytes_reclaimed": bytes_reclaimed,
            "cleanup_status": status,
            "executed_at": datetime.now(UTC),
        }
        return LocalCleanupReceipt(**payload, receipt_hash=stable_hash(payload))
