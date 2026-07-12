"""Absence and canonical-renderer regression coverage for the retired provider."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.services.provider_stack import CANONICAL_PROVIDER_KEYS, LOCAL_CAPABILITY_KEYS, provider_key_rejection_reasons


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = next((ROOT / "reports").glob("*full_removal_inventory.json"))
REMOVED = json.loads(INVENTORY.read_text(encoding="utf-8"))["search_terms"][0].lower()


def _contains_removed(path: Path) -> bool:
    return REMOVED in path.read_text(encoding="utf-8", errors="ignore").lower()


def test_settings_contain_no_removed_provider_fields():
    assert all(REMOVED not in name.lower() for name in Settings.model_fields)


def test_provider_registry_and_aliases_do_not_know_removed_provider():
    assert CANONICAL_PROVIDER_KEYS == ("elevenlabs", "google_veo", "pexels_api")
    assert LOCAL_CAPABILITY_KEYS == ("native_ffmpeg_renderer",)
    assert provider_key_rejection_reasons(REMOVED) == ["UNKNOWN_PROVIDER_KEY"]


def test_runtime_has_no_removed_adapter_builder_or_service_symbol():
    hits = [path for path in (ROOT / "app").rglob("*") if path.is_file() and _contains_removed(path)]
    assert hits == []


def test_openapi_has_no_removed_path_schema_or_tag():
    schema = json.dumps(create_app().openapi(), sort_keys=True).lower()
    assert REMOVED not in schema


def test_cost_readiness_and_prompts_have_no_removed_mapping():
    roots = [ROOT / "app/services", ROOT / "app/prompts", ROOT / "config"]
    hits = [path for base in roots for path in base.rglob("*") if path.is_file() and _contains_removed(path)]
    assert hits == []


def test_dependency_files_have_no_removed_package():
    candidates = [ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT / "package.json", ROOT / "frontend/package.json"]
    candidates.extend(ROOT.glob("requirements*"))
    assert [path for path in candidates if path.is_file() and _contains_removed(path)] == []


def test_database_metadata_has_no_removed_table_or_column():
    removed_table = f"{REMOVED}_render_assets"
    removed_column = f"{REMOVED}_asset_refs"
    assert removed_table not in Base.metadata.tables
    assert all(removed_column not in table.columns for table in Base.metadata.tables.values())


def test_current_tree_contains_no_unapproved_removed_provider_reference():
    excluded = {
        INVENTORY.resolve(),
        *[path.resolve() for path in (ROOT / "reports").glob("*full_removal_report.md")],
        *[path.resolve() for path in (ROOT / "reports").glob("*full_removal_summary.json")],
    }
    hits = []
    for base_name in ("app", "tests", "docs", "reports", "scripts", "frontend", "config", "alembic"):
        base = ROOT / base_name
        if not base.exists():
            continue
        hits.extend(path for path in base.rglob("*") if path.is_file() and path.resolve() not in excluded and _contains_removed(path))
    assert hits == []


def test_native_renderer_remains_canonical_render_authority():
    architecture = (ROOT / "docs/architecture/native_ffmpeg_renderer.md").read_text(encoding="utf-8")
    assert "NativeFFmpegRenderer" in architecture
    assert "NativeRenderPlan → NativeMotionCompiler" in architecture


def test_as1_nr1_nr2_reports_remain_present():
    for name in (
        "as1_asset_acquisition_provenance_report.md",
        "nr1_native_renderer_architecture_report.md",
        "nr2_native_production_bakeoff_report.md",
    ):
        assert (ROOT / "reports" / name).is_file()
