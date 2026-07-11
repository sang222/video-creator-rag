from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings


def create_router() -> APIRouter:
    router = APIRouter(tags=["native-renderer-readonly"])

    def root() -> Path:
        configured = Path(get_settings().native_render_workspace_root)
        return configured if configured.is_absolute() else Path(__file__).resolve().parents[3] / configured

    def read(name: str, filename: str):
        if not name or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in name):
            raise HTTPException(400, "INVALID_RENDER_REF")
        matches = list((root() / "runs").glob(f"{name}/{filename}"))
        if not matches: raise HTTPException(404, "NATIVE_RENDER_EVIDENCE_NOT_FOUND")
        return json.loads(matches[0].read_text(encoding="utf-8"))

    @router.get("/video-packages/{package_id}/native-render-plan")
    def plan(package_id: str): return read(package_id, "native_render_plan.json")

    @router.get("/native-render-plans/{plan_id}/compiled-manifest")
    def manifest(plan_id: str): return read(plan_id, "compiled_manifest.json")

    @router.get("/native-render-runs/{run_key}/summary")
    def run(run_key: str):
        return {"execution_receipt": read(run_key, "execution_receipt.json"), "media_qc": read(run_key, "media_qc.json"), "next_action": "HUMAN_REVIEW", "no_provider_proof": True}

    return router
