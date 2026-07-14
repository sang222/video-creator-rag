from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts.asset_acquisition import (
    DriveArchiveFileReceipt,
    DriveArchiveReceipt,
    ProductionArchiveFileEntry,
    ProductionArchiveManifest,
)
from app.services.native_render_plan import stable_hash


ARCHIVE_SECTIONS = (
    "00-manifests/",
    "01-script/",
    "02-audio/",
    "03-stock/selected-originals/",
    "04-ai-hero/selected-takes/",
    "05-render/",
    "06-qc/",
    "07-publish/",
)
ROLE_ARCHIVE_PATHS = {
    "PACKAGE_MANIFEST": "00-manifests/package-manifest.json",
    "STOCK_SOURCES": "00-manifests/stock-sources.json",
    "AI_GENERATION_MANIFEST": "00-manifests/ai-generation-manifest.json",
    "AI_PROVIDER_OPERATION_RECEIPT": "00-manifests/ai-provider-operation-receipt.json",
    "AI_COST_APPROVAL_IDEMPOTENCY": "00-manifests/ai-cost-approval-idempotency.json",
    "SYNTHETIC_MEDIA_DISCLOSURE": "00-manifests/synthetic-media-disclosure.json",
    "AI_HERO_NORMALIZATION_RECEIPT": "00-manifests/ai-hero-normalization-receipt.json",
    "NATIVE_RENDER_PLAN": "00-manifests/native-render-plan.json",
    "COMPILED_NATIVE_RENDER_MANIFEST": "00-manifests/compiled-native-render-manifest.json",
    "FFMPEG_COMMAND_MANIFEST": "00-manifests/ffmpeg-command-manifest.json",
    "SCRIPT_JSON": "01-script/script.json",
    "SCRIPT_MARKDOWN": "01-script/script.md",
    "CAPTIONS_SRT": "01-script/captions.srt",
    "NARRATION_AUDIO_TIMELINE": "02-audio/narration-audio-timeline.json",
    "CANONICAL_MEDIA_TIMELINE": "02-audio/canonical-media-timeline.json",
    "SELECTED_STOCK_ORIGINAL": "03-stock/selected-originals/stock-original.mp4",
    "SELECTED_AI_HERO_TAKE": "04-ai-hero/selected-takes/ai-hero-take.mp4",
    "FINAL_MASTER": "05-render/final-master.mp4",
    "REVIEW_PROXY": "05-render/review-proxy.mp4",
    "MEDIA_QC": "06-qc/media-qc.json",
    "FFPROBE": "06-qc/ffprobe.json",
    "MANUAL_PUBLISH_PACKAGE": "07-publish/manual-publish-package.json",
}
CQR1_ROLE_ARCHIVE_PATHS = {
    "SPOKEN_TEXT_NORMALIZED": "01-script/spoken-text-normalized.json",
    "PROVIDER_TIMING_SEED": "02-audio/provider-timing-seed.json",
    "FORCED_ALIGNMENT_EVIDENCE": "02-audio/forced-alignment-evidence.json",
    "VERIFIED_NARRATION_ALIGNMENT": "02-audio/verified-narration-alignment.json",
    "NARRATION_PACING_REPORT": "06-qc/narration-pacing.json",
    "CAPTION_COMPILATION_REPORT": "01-script/caption-compilation.json",
    "CAPTION_BBOX_SAFE_AREA_EVIDENCE": "06-qc/caption-bbox-safe-area.json",
    "CAPTION_SYNC_COVERAGE_DRIFT_REPORT": "06-qc/caption-sync-coverage-drift.json",
    "VISUAL_DIRECTION_CONTRACT": "00-manifests/visual-direction-contract.json",
    "PEXELS_RANKING_PROVENANCE": "00-manifests/pexels-ranking-provenance.json",
    "VEO_PROMPT_REQUEST_PROVENANCE": "00-manifests/veo-prompt-request-provenance.json",
    "VISUAL_CONTINUITY_REPORT": "06-qc/visual-continuity.json",
    "CONTACT_SHEET": "05-render/contact-sheet.jpg",
    "TECHNICAL_MEDIA_QC": "06-qc/technical-media-qc.json",
    "CREATIVE_PERCEPTUAL_MEDIA_QC": "06-qc/creative-perceptual-media-qc.json",
    "HUMAN_REVIEW_PACKET": "06-qc/human-watchability-review.md",
    "NOT_PUBLISHABLE_MANIFEST": "07-publish/not-publishable-manifest.json",
}
ALL_ROLE_ARCHIVE_PATHS = {**ROLE_ARCHIVE_PATHS, **CQR1_ROLE_ARCHIVE_PATHS}
# Historical AS1/PA1R builders retain their frozen role set. New repaired runs pass
# this extended set explicitly, so old archive evidence is never rewritten.
LEGACY_REQUIRED_ARCHIVE_ROLES = frozenset(set(ROLE_ARCHIVE_PATHS) - {"CANONICAL_MEDIA_TIMELINE"})
CQR1A_REQUIRED_ARCHIVE_ROLES = frozenset(ROLE_ARCHIVE_PATHS)
CQR1_REQUIRED_ARCHIVE_ROLES = frozenset(
    {
        "CANONICAL_MEDIA_TIMELINE",
        "SPOKEN_TEXT_NORMALIZED",
        "PROVIDER_TIMING_SEED",
        "FORCED_ALIGNMENT_EVIDENCE",
        "VERIFIED_NARRATION_ALIGNMENT",
        "NARRATION_PACING_REPORT",
        "CAPTION_COMPILATION_REPORT",
        "CAPTION_BBOX_SAFE_AREA_EVIDENCE",
        "CAPTION_SYNC_COVERAGE_DRIFT_REPORT",
        "VISUAL_DIRECTION_CONTRACT",
        "PEXELS_RANKING_PROVENANCE",
        "VEO_PROMPT_REQUEST_PROVENANCE",
        "VISUAL_CONTINUITY_REPORT",
        "NATIVE_RENDER_PLAN",
        "COMPILED_NATIVE_RENDER_MANIFEST",
        "FFMPEG_COMMAND_MANIFEST",
        "FINAL_MASTER",
        "CONTACT_SHEET",
        "TECHNICAL_MEDIA_QC",
        "CREATIVE_PERCEPTUAL_MEDIA_QC",
        "HUMAN_REVIEW_PACKET",
        "SYNTHETIC_MEDIA_DISCLOSURE",
        "NOT_PUBLISHABLE_MANIFEST",
    }
)
EXCLUDED_MARKERS = {"rejected", "normalized", "scratch", "cache", "failed-generation", ".part"}


@dataclass(frozen=True)
class ArchiveSource:
    logical_role: str
    source_path: Path
    expected_archive_path: str | None = None
    required_for_archive: bool = True
    required_for_local_purge: bool = True


class ProductionArchiveBuilder:
    def build(
        self,
        *,
        manifest_id: str,
        project_id: str,
        package_id: str,
        sources: list[ArchiveSource],
        required_roles: frozenset[str] = LEGACY_REQUIRED_ARCHIVE_ROLES,
    ) -> ProductionArchiveManifest:
        entries: list[ProductionArchiveFileEntry] = []
        excluded: list[str] = []
        for source in sources:
            lowered = {part.lower() for part in source.source_path.parts}
            if lowered & EXCLUDED_MARKERS or source.source_path.name.endswith(".part"):
                excluded.append(str(source.source_path))
                continue
            if not source.source_path.is_file() or source.source_path.is_symlink():
                raise ValueError(f"ARCHIVE_SOURCE_INVALID:{source.logical_role}")
            expected = source.expected_archive_path or ALL_ROLE_ARCHIVE_PATHS.get(source.logical_role)
            if not expected or expected.startswith("/") or ".." in Path(expected).parts:
                raise ValueError(f"ARCHIVE_PATH_INVALID:{source.logical_role}")
            sha256, md5 = _file_hashes(source.source_path)
            entry_payload = {
                "logical_role": source.logical_role,
                "source_path": str(source.source_path),
                "expected_archive_path": expected,
                "size_bytes": source.source_path.stat().st_size,
                "sha256": sha256,
                "md5": md5,
                "required_for_archive": source.required_for_archive,
                "required_for_local_purge": source.required_for_local_purge,
            }
            entries.append(ProductionArchiveFileEntry(**entry_payload, manifest_hash=stable_hash(entry_payload)))
        present_roles = {entry.logical_role for entry in entries}
        unknown_required = sorted(set(required_roles) - set(ALL_ROLE_ARCHIVE_PATHS))
        if unknown_required:
            raise ValueError(f"ARCHIVE_REQUIRED_ROLE_UNKNOWN:{','.join(unknown_required)}")
        missing = sorted(set(required_roles) - present_roles)
        if missing:
            raise ValueError(f"ARCHIVE_REQUIRED_ROLES_MISSING:{','.join(missing)}")
        payload = {
            "manifest_id": manifest_id,
            "project_id": project_id,
            "package_id": package_id,
            "sections": list(ARCHIVE_SECTIONS),
            "files": [entry.model_dump(mode="json") for entry in sorted(entries, key=lambda item: item.expected_archive_path)],
            "excluded_paths": sorted(excluded),
            "total_size_bytes": sum(entry.size_bytes for entry in entries),
            "required_roles_complete": True,
            "provider_execution_allowed": False,
        }
        return ProductionArchiveManifest(**payload, manifest_hash=stable_hash(payload))


class ProductionArchivePathBuilder:
    def build(self, *, company_id: str, channel_workspace_id: str, video_project_id: str) -> str:
        values = (company_id, channel_workspace_id, video_project_id)
        if any(not value or value.lower() == "unknown" or not re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in values):
            raise ValueError("ARCHIVE_UNKNOWN_OR_INVALID_SCOPE_SEGMENT")
        path = f"company_{company_id}/channel_{channel_workspace_id}/project_{video_project_id}/production-package-v1"
        self.validate(path)
        return path

    @staticmethod
    def validate(path: str) -> None:
        parts = Path(path).parts
        if any(part.lower() in {"vcos", "vcos media"} for part in parts):
            raise ValueError("ARCHIVE_NESTED_CONFIGURED_ROOT_FORBIDDEN")
        if any(part.lower().endswith("_unknown") for part in parts):
            raise ValueError("ARCHIVE_UNKNOWN_SCOPE_FORBIDDEN")
        if path.startswith("/") or ".." in parts:
            raise ValueError("ARCHIVE_RELATIVE_PATH_REQUIRED")


class CQR1ArchivePathBuilder:
    @staticmethod
    def build(*, run_id: str, archive_date: str = "2026-07-14") -> str:
        path = f"smoke_tests/{archive_date}/cqr1/{run_id}"
        CQR1ArchivePathBuilder.validate(path)
        return path

    @staticmethod
    def validate(path: str) -> None:
        if path.startswith("/") or ".." in Path(path).parts:
            raise ValueError("CQR1_ARCHIVE_RELATIVE_PATH_REQUIRED")
        parts = Path(path).parts
        if len(parts) != 4 or parts[0] != "smoke_tests" or parts[2] != "cqr1":
            raise ValueError("CQR1_ARCHIVE_PATH_INVALID")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1]):
            raise ValueError("CQR1_ARCHIVE_DATE_INVALID")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", parts[3]) or not parts[3].startswith("pa1r-cqr1-"):
            raise ValueError("CQR1_ARCHIVE_RUN_ID_INVALID")


class DriveArchiveFixtureVerifier:
    """Compare fixture metadata only; no Drive SDK/client exists in this boundary."""

    def verify(
        self,
        *,
        manifest: ProductionArchiveManifest,
        configured_root_folder_id_reference: str,
        root_relative_folder_path: str,
        fixture_files: list[dict[str, Any]],
    ) -> DriveArchiveReceipt:
        ProductionArchivePathBuilder.validate(root_relative_folder_path)
        fixture_by_path = {str(item["archive_path"]): item for item in fixture_files}
        receipts: list[DriveArchiveFileReceipt] = []
        mismatches: list[str] = []
        for entry in manifest.files:
            remote = fixture_by_path.get(entry.expected_archive_path)
            drive_size = int(remote["size_bytes"]) if remote and remote.get("size_bytes") is not None else None
            drive_sha = str(remote.get("sha256")) if remote and remote.get("sha256") else None
            verified = bool(remote and drive_size == entry.size_bytes and drive_sha == entry.sha256)
            if entry.required_for_archive and not verified:
                if remote is None:
                    mismatches.append(f"REQUIRED_FILE_MISSING:{entry.logical_role}")
                elif drive_size != entry.size_bytes:
                    mismatches.append(f"SIZE_MISMATCH:{entry.logical_role}")
                else:
                    mismatches.append(f"CHECKSUM_MISMATCH:{entry.logical_role}")
            receipts.append(
                DriveArchiveFileReceipt(
                    archive_path=entry.expected_archive_path,
                    local_size=entry.size_bytes,
                    drive_size=drive_size,
                    local_sha256=entry.sha256,
                    drive_sha256=drive_sha,
                    verified=verified,
                )
            )
        state = "FAILED" if mismatches else "VERIFIED"
        payload = {
            "archive_manifest_ref": manifest.manifest_id,
            "archive_manifest_hash": manifest.manifest_hash,
            "configured_root_folder_id_reference": configured_root_folder_id_reference,
            "root_relative_folder_path": root_relative_folder_path,
            "drive_folder_id": None,
            "files": [item.model_dump(mode="json") for item in receipts],
            "total_local_size": manifest.total_size_bytes,
            "total_drive_size": sum(item.drive_size or 0 for item in receipts),
            "archive_state": state,
            "mismatch_reason_codes": mismatches,
            "verified_at": datetime.now(UTC) if state == "VERIFIED" else None,
            "provider_call_made": False,
            "transport": "LOCAL_FIXTURE_ONLY",
        }
        return DriveArchiveReceipt(**payload, receipt_hash=stable_hash(payload))


ARCHIVE_PURGE_TRANSITIONS = {
    "MEDIA_QC_PASSED": {"ARCHIVE_PLANNED"},
    "ARCHIVE_PLANNED": {"ARCHIVE_UPLOADING"},
    "ARCHIVE_UPLOADING": {"ARCHIVE_UPLOADED_UNVERIFIED"},
    "ARCHIVE_UPLOADED_UNVERIFIED": {"ARCHIVE_VERIFYING"},
    "ARCHIVE_VERIFYING": {"ARCHIVE_VERIFIED"},
    "ARCHIVE_VERIFIED": {"LOCAL_PURGE_ELIGIBLE"},
    "LOCAL_PURGE_ELIGIBLE": {"LOCAL_PURGED"},
    "LOCAL_PURGED": set(),
}


class ArchivePurgeStateMachine:
    def transition(self, current: str, target: str) -> str:
        if target not in ARCHIVE_PURGE_TRANSITIONS.get(current, set()):
            raise ValueError(f"ARCHIVE_PURGE_TRANSITION_FORBIDDEN:{current}->{target}")
        return target


def _file_hashes(path: Path) -> tuple[str, str]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(chunk)
            md5.update(chunk)
    return sha256.hexdigest(), md5.hexdigest()
