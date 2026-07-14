from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.services.temporal_authority import elevenlabs_temporal_permission_readiness


def create_router() -> APIRouter:
    router = APIRouter(tags=["temporal-authority-readonly"])

    def root() -> Path:
        configured = Path(get_settings().local_project_workspace_root)
        return configured.resolve() if configured.is_absolute() else (Path(__file__).resolve().parents[3] / configured).resolve()

    def valid(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise HTTPException(400, "INVALID_TEMPORAL_AUTHORITY_REFERENCE")
        return value

    def read(path: Path, *, required: bool = True) -> dict[str, Any] | None:
        base = root()
        resolved = path.resolve(strict=False)
        if base != resolved and base not in resolved.parents:
            raise HTTPException(400, "TEMPORAL_AUTHORITY_READ_PATH_ESCAPE")
        if not resolved.is_file() or resolved.is_symlink():
            if required:
                raise HTTPException(404, "TEMPORAL_AUTHORITY_EVIDENCE_NOT_FOUND")
            return None
        return json.loads(resolved.read_text(encoding="utf-8"))

    def find_project_by_package(package_id: str) -> Path:
        wanted = valid(package_id)
        for candidate in sorted(root().glob("*/manifests/canonical_media_timeline.json")):
            payload = read(candidate)
            if payload and payload.get("package_id") == wanted:
                return candidate.parents[1]
        raise HTTPException(404, "CANONICAL_MEDIA_TIMELINE_NOT_FOUND")

    def safe_asset_ref(value: Any) -> str | None:
        if not value:
            return None
        text = str(value)
        if "/" in text or "://" in text:
            return "audio-asset-ref:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        return text

    def evidence(project: Path) -> dict[str, Any]:
        manifests = project / "manifests"
        timeline = read(manifests / "canonical_media_timeline.json") or {}
        normalized = read(manifests / "spoken_text_normalized.json", required=False) or {}
        alignment = read(manifests / "verified_narration_alignment.json", required=False) or {}
        gate = read(manifests / "temporal_authority_gate.json", required=False) or {}
        readiness = elevenlabs_temporal_permission_readiness()
        block_reasons = gate.get("block_reasons") or []
        gate_status = gate.get("gate_status") or "BLOCK"
        return {
            "project_id": timeline.get("project_id"),
            "package_id": timeline.get("package_id"),
            "script_revision": timeline.get("script_revision_id"),
            "spoken_text_revision": timeline.get("spoken_text_revision_id"),
            "audio_asset_ref": safe_asset_ref(timeline.get("audio_asset_id")),
            "audio_duration_ms": timeline.get("audio_duration_ms"),
            "provider_timing_available": bool(timeline.get("provider_timing_seed_ref")),
            "forced_alignment_available": bool(timeline.get("forced_alignment_ref")),
            "token_coverage": alignment.get("token_coverage", timeline.get("qc_metrics", {}).get("spoken_token_coverage")),
            "timeline_hash": timeline.get("timeline_hash"),
            "gate_status": gate_status,
            "block_reasons": block_reasons,
            "exact_next_action": gate.get("exact_next_action") or "REPAIR_TEMPORAL_EVIDENCE_AND_RECOMPILE",
            "normalization_version": normalized.get("normalization_version"),
            **readiness,
            "provider_execution_disabled": True,
        }

    @router.get("/video-projects/{project_id}/temporal-authority")
    def project_temporal_authority(project_id: str):
        project = root() / valid(project_id)
        return evidence(project)

    @router.get("/video-packages/{package_id}/canonical-media-timeline")
    def package_canonical_media_timeline(package_id: str):
        return evidence(find_project_by_package(package_id))

    return router
