from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.contracts.asset_acquisition import AssetDownloadReceipt, LocalProjectWorkspaceSummary
from app.services.native_render_plan import stable_hash


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKSPACE_ROOT = REPO_ROOT / "var" / "tmp" / "vcos-project-workspaces"
WORKSPACE_DIRECTORIES = (
    "source/script",
    "source/audio",
    "source/pexels",
    "source/luma",
    "normalized/stock",
    "normalized/hero",
    "normalized/audio",
    "render/scenes",
    "render/proxy",
    "render/final",
    "manifests",
    "qc",
    "publish",
)
SUCCESS_EVIDENCE_STATES = {"ASSET_DOWNLOADED", "ASSET_NORMALIZED", "READY_FOR_RENDER"}
FAILURE_STATES = {"SEARCH_FAILED", "DOWNLOAD_FAILED", "CHECKSUM_FAILED", "NORMALIZATION_FAILED", "BLOCKED_POLICY"}
TRANSITIONS = {
    "PLANNED": {"ASSET_SEARCHING", "BLOCKED_POLICY"},
    "ASSET_SEARCHING": {"ASSET_SELECTED", "SEARCH_FAILED", "BLOCKED_POLICY"},
    "ASSET_SELECTED": {"ASSET_DOWNLOADING", "BLOCKED_POLICY"},
    "ASSET_DOWNLOADING": {"ASSET_DOWNLOADED", "DOWNLOAD_FAILED", "CHECKSUM_FAILED"},
    "ASSET_DOWNLOADED": {"ASSET_NORMALIZED", "NORMALIZATION_FAILED", "BLOCKED_POLICY"},
    "ASSET_NORMALIZED": {"READY_FOR_RENDER", "BLOCKED_POLICY"},
    "READY_FOR_RENDER": set(),
    **{state: set() for state in FAILURE_STATES},
}


class AssetDownloadStateMachine:
    def transition(self, current: str, target: str, *, file_path: Path | None = None, sha256: str | None = None) -> str:
        if target not in TRANSITIONS.get(current, set()):
            raise ValueError(f"ASSET_STATE_TRANSITION_FORBIDDEN:{current}->{target}")
        if target in SUCCESS_EVIDENCE_STATES:
            if file_path is None or not file_path.is_file() or not sha256:
                raise ValueError("ASSET_SUCCESS_REQUIRES_FILE_AND_CHECKSUM")
            if _sha256_file(file_path) != sha256:
                raise ValueError("ASSET_SUCCESS_CHECKSUM_MISMATCH")
        return target


class LocalProjectWorkspaceService:
    def __init__(self, root: Path | str | None = None, *, minimum_free_bytes: int = 1, max_file_size_bytes: int = 2 * 1024 * 1024 * 1024):
        configured = Path(root) if root is not None else DEFAULT_WORKSPACE_ROOT
        if not configured.is_absolute():
            configured = REPO_ROOT / configured
        self._configured_root = configured
        self.minimum_free_bytes = minimum_free_bytes
        self.max_file_size_bytes = max_file_size_bytes

    @property
    def root(self) -> Path:
        if self._configured_root.exists() and self._configured_root.is_symlink():
            raise ValueError("WORKSPACE_ROOT_SYMLINK_FORBIDDEN")
        return self._configured_root.resolve()

    def create(self, project_id: str) -> LocalProjectWorkspaceSummary:
        self._validate_identifier(project_id)
        self._configured_root.mkdir(parents=True, exist_ok=True)
        root = self.root
        available = shutil.disk_usage(root).free
        if available < self.minimum_free_bytes:
            raise OSError("WORKSPACE_DISK_FREE_PREFLIGHT_FAILED")
        project = self._inside(project_id, ".")
        if project.exists() and project.is_symlink():
            raise ValueError("WORKSPACE_PROJECT_SYMLINK_FORBIDDEN")
        project.mkdir(parents=True, exist_ok=True)
        for relative in WORKSPACE_DIRECTORIES:
            self._inside(project_id, relative).mkdir(parents=True, exist_ok=True)
        ownership_path = self._inside(project_id, "manifests/workspace_ownership.json")
        if ownership_path.exists():
            ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
            if ownership.get("project_id") != project_id or Path(ownership.get("workspace_path", "")).resolve() != project:
                raise ValueError("WORKSPACE_OWNERSHIP_CONFLICT")
        else:
            ownership_path.write_text(
                json.dumps({"project_id": project_id, "workspace_path": str(project), "one_project_one_workspace": True}, sort_keys=True, indent=2),
                encoding="utf-8",
            )
        payload = {
            "project_id": project_id,
            "workspace_root": str(root),
            "workspace_path": str(project),
            "directories": list(WORKSPACE_DIRECTORIES),
            "available_bytes": available,
            "ownership_verified": True,
            "transport": "LOCAL_FIXTURE_ONLY",
            "provider_execution_allowed": False,
        }
        summary = LocalProjectWorkspaceSummary(**payload, summary_hash=stable_hash(payload))
        self.write_json(project_id, "manifests/local_workspace_summary.json", summary.model_dump(mode="json"))
        return summary

    def path(self, project_id: str, relative: str | Path) -> Path:
        return self._inside(project_id, relative)

    def write_json(self, project_id: str, relative: str | Path, payload: dict) -> Path:
        destination = self._inside(project_id, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_chain(destination)
        part = destination.with_name(f"{destination.name}.part")
        try:
            part.write_text(json.dumps(payload, sort_keys=True, indent=2, default=str), encoding="utf-8")
            os.replace(part, destination)
        finally:
            part.unlink(missing_ok=True)
        return destination

    def fixture_download(
        self,
        *,
        project_id: str,
        request_id: str,
        fixture_source: Path,
        destination_relative: str,
        fail_after_bytes: int | None = None,
    ) -> AssetDownloadReceipt:
        if fixture_source.is_symlink() or not fixture_source.is_file():
            raise ValueError("FIXTURE_SOURCE_INVALID")
        source_size = fixture_source.stat().st_size
        if source_size > self.max_file_size_bytes:
            raise ValueError("ASSET_FILE_SIZE_LIMIT_EXCEEDED")
        destination = self._inside(project_id, destination_relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_chain(destination)
        part = destination.with_name(f"{destination.name}.part")
        states = ["PLANNED", "ASSET_SEARCHING", "ASSET_SELECTED", "ASSET_DOWNLOADING"]
        digest = hashlib.sha256()
        written = 0
        try:
            with fixture_source.open("rb") as source, part.open("xb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    if fail_after_bytes is not None and written + len(chunk) > fail_after_bytes:
                        raise OSError("LOCAL_FIXTURE_INJECTED_DOWNLOAD_FAILURE")
                    output.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    if written > self.max_file_size_bytes:
                        raise ValueError("ASSET_FILE_SIZE_LIMIT_EXCEEDED")
                output.flush()
                os.fsync(output.fileno())
            sha256 = digest.hexdigest()
            os.replace(part, destination)
            AssetDownloadStateMachine().transition("ASSET_DOWNLOADING", "ASSET_DOWNLOADED", file_path=destination, sha256=sha256)
        except Exception:
            part.unlink(missing_ok=True)
            raise
        states.append("ASSET_DOWNLOADED")
        payload = {
            "request_id": request_id,
            "state": "ASSET_DOWNLOADED",
            "states": states,
            "transport": "LOCAL_FIXTURE_ONLY",
            "provider_call_made": False,
            "production_eligible": False,
            "local_path": str(destination),
            "size_bytes": written,
            "sha256": sha256,
            "completed_at": datetime.now(UTC),
        }
        receipt = AssetDownloadReceipt(**payload, receipt_hash=stable_hash(payload))
        self.write_json(project_id, f"manifests/{request_id}_download_receipt.json", receipt.model_dump(mode="json"))
        return receipt

    def _inside(self, project_id: str, relative: str | Path) -> Path:
        self._validate_identifier(project_id)
        raw = Path(relative)
        if raw.is_absolute() or ".." in raw.parts:
            raise ValueError("WORKSPACE_PATH_TRAVERSAL")
        root = self.root
        project = (root / project_id).resolve(strict=False)
        candidate = (project / raw).resolve(strict=False)
        if project != candidate and project not in candidate.parents:
            raise ValueError("WORKSPACE_PATH_ESCAPE")
        self._reject_symlink_chain(candidate)
        return candidate

    def _reject_symlink_chain(self, path: Path) -> None:
        root = self.root
        current = root
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError as exc:
            raise ValueError("WORKSPACE_PATH_ESCAPE") from exc
        for part in relative_parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ValueError("WORKSPACE_SYMLINK_ESCAPE")

    @staticmethod
    def _validate_identifier(value: str) -> None:
        if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("WORKSPACE_PROJECT_ID_INVALID")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
