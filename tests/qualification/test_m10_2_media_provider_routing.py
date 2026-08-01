from __future__ import annotations

from pathlib import Path

import yaml

from app.services.m10_2 import (
    LOCAL_RENDERER_CAPABILITY,
    LONG_FORM_FINAL_RENDER,
    MediaRenderJobRouterService,
)


ROOT = Path(__file__).resolve().parents[2]


def _items(name: str):
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))["items"]


def test_native_renderer_owns_local_render_capabilities():
    entries = _items("media_provider_capability_matrix_catalog.yaml")
    native = [
        item for item in entries if item["provider_key"] == "native_ffmpeg_renderer"
    ]
    assert native
    assert all(item["provider_type"] == LOCAL_RENDERER_CAPABILITY for item in native)
    assert any(item["job_type"] == LONG_FORM_FINAL_RENDER for item in native)


def test_render_routing_catalog_targets_native_renderer():
    entries = _items("media_provider_routing_policy_catalog.yaml")
    render_jobs = {"LONG_FORM_FINAL_RENDER", "THUMBNAIL_RENDER", "DIAGRAM_CARD_RENDER"}
    routed = {
        item["job_type"]: item["provider_key"]
        for item in entries
        if item["job_type"] in render_jobs
    }
    assert all(
        routed.get(job) == "native_ffmpeg_renderer"
        for job in render_jobs - {"LONG_FORM_FINAL_RENDER"}
    )


def test_long_form_router_declares_native_local_authority():
    source = Path(
        MediaRenderJobRouterService.__module__.replace(".", "/") + ".py"
    ).read_text(encoding="utf-8")
    assert "NATIVE_FFMPEG_RENDER_AUTHORITY" in source
    assert 'selected_provider_key="native_ffmpeg_renderer"' in source
