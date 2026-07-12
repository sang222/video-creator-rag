from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings


def create_router() -> APIRouter:
    router = APIRouter(tags=["asset-acquisition-readonly"])

    def root() -> Path:
        configured = Path(get_settings().local_project_workspace_root)
        return configured.resolve() if configured.is_absolute() else (Path(__file__).resolve().parents[3] / configured).resolve()

    def valid(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise HTTPException(400, "INVALID_AS1_REFERENCE")
        return value

    def read(path: Path) -> dict[str, Any]:
        base = root()
        resolved = path.resolve(strict=False)
        if base != resolved and base not in resolved.parents:
            raise HTTPException(400, "AS1_READ_PATH_ESCAPE")
        if not resolved.is_file() or resolved.is_symlink():
            raise HTTPException(404, "AS1_EVIDENCE_NOT_FOUND")
        return json.loads(resolved.read_text(encoding="utf-8"))

    def find_package(package_id: str) -> Path:
        wanted = valid(package_id)
        for candidate in sorted(root().glob("*/manifests/compiled_asset_request_plan.json")):
            payload = read(candidate)
            if payload.get("package_id") == wanted:
                return candidate
        raise HTTPException(404, "ASSET_ACQUISITION_PLAN_NOT_FOUND")

    @router.get("/video-packages/{package_id}/asset-acquisition-plan")
    def acquisition_plan(package_id: str):
        payload = read(find_package(package_id))
        project = find_package(package_id).parents[1]
        source_manifests = [read(path) for path in sorted((project / "manifests").glob("stock_source_manifest*.json"))]
        return {
            "package_id": payload["package_id"],
            "request_counts": {
                "native": payload["native_request_count"],
                "supporting_stock": payload["supporting_stock_request_count"],
                "ai_hero": payload["ai_hero_request_count"],
            },
            "unresolved_requests": payload["unresolved_request_count"],
            "planned_provider_intent": {"supporting_stock": payload["supporting_stock_request_count"], "ai_hero": payload["ai_hero_request_count"]},
            "source_manifests": source_manifests,
            "provider_execution_disabled": True,
            "exact_next_action": "REVIEW_ASSET_PLAN_AND_KEEP_PROVIDER_EXECUTION_DISABLED",
            "technical_details": payload,
        }

    @router.get("/video-projects/{project_id}/local-workspace-summary")
    def workspace_summary(project_id: str):
        project = valid(project_id)
        payload = read(root() / project / "manifests/local_workspace_summary.json")
        return {**payload, "provider_execution_disabled": True, "exact_next_action": "REVIEW_FIXTURE_WORKSPACE_EVIDENCE"}

    @router.get("/video-projects/{project_id}/archive-readiness")
    def archive_readiness(project_id: str):
        project = valid(project_id)
        manifests = root() / project / "manifests"
        archive = read(manifests / "production_archive_manifest.json")
        receipt_path = manifests / "drive_archive_receipt.json"
        receipt = read(receipt_path) if receipt_path.is_file() else None
        verified = bool(receipt and receipt.get("archive_state") == "VERIFIED" and not receipt.get("mismatch_reason_codes"))
        return {
            "project_id": project,
            "archive_completeness": archive.get("required_roles_complete", False),
            "archive_state": receipt.get("archive_state") if receipt else "PLANNED",
            "purge_eligibility": verified,
            "provider_execution_disabled": True,
            "exact_next_action": "FIXTURE_PURGE_ELIGIBLE" if verified else "VERIFY_ALL_REQUIRED_ARCHIVE_FILES_BEFORE_PURGE",
            "archive_manifest": archive,
            "drive_archive_receipt": receipt,
        }

    return router
