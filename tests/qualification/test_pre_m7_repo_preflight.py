from __future__ import annotations

from .helpers.ffmpeg_checks import assert_ffmpeg_available
from .helpers.git_checks import collect_git_status
from .helpers.qualification_asserts import (
    REQUIRED_SOURCE_OF_TRUTH_PATHS,
    m0_m1_evidence_status,
    missing_paths,
)


ALLOWED_QUALIFICATION_DIRTY_PREFIXES = (
    "README.md",
    "app/cli/main.py",
    "app/contracts/__init__.py",
    "app/contracts/m7.py",
    "app/db/models/__init__.py",
    "app/db/models/m7.py",
    "app/main.py",
    "app/services/__init__.py",
    "app/services/config_registry.py",
    "app/services/m7.py",
    "alembic/versions/0008_m7_publish_handoff.py",
    "config/disclosure_confirmation_catalog.yaml",
    "config/m7_reason_code_catalog.yaml",
    "config/manual_publish_confirmation_state_catalog.yaml",
    "config/metadata_diff_severity_catalog.yaml",
    "config/publish_checklist_category_catalog.yaml",
    "config/publish_handoff_state_catalog.yaml",
    "config/publish_target_platform_catalog.yaml",
    "config/publish_target_surface_catalog.yaml",
    "config/uploaded_video_monitoring_state_catalog.yaml",
    "config/uploaded_video_publish_status_catalog.yaml",
    "docs/architecture/architecture-ledger.md",
    "docs/architecture/m7-manual-publish-handoff.md",
    "docs/architecture/source-of-truth.md",
    "reports/m7-final-report.md",
    "tests/conftest.py",
    "tests/test_config_registry.py",
    "tests/test_m4_ops_foundation.py",
    "tests/test_m5_daily_run_context_admission.py",
    "tests/test_m6_production.py",
    "tests/test_migration.py",
    "tests/test_pre_m4_regression_gauntlet.py",
    "tests/qualification/",
    "scripts/pre_m7_qualification.py",
    "reports/pre_m7_qualification_",
    "alembic/versions/0002_m1_channel_profile_backbone.py",
)


def _dirty_path(line: str) -> str:
    return line[3:] if len(line) > 3 else line


def test_required_tags_source_docs_gitignore_and_media_preflight() -> None:
    status = collect_git_status()
    assert all(status.required_tags.values())
    assert all(status.media_gitignore.values())
    assert status.staged_binary_media == []
    assert missing_paths(REQUIRED_SOURCE_OF_TRUTH_PATHS) == []
    assert_ffmpeg_available()


def test_worktree_has_no_unrelated_dirty_product_changes() -> None:
    status = collect_git_status()
    unrelated_dirty = [
        line
        for line in status.porcelain
        if not _dirty_path(line).startswith(ALLOWED_QUALIFICATION_DIRTY_PREFIXES)
    ]
    assert unrelated_dirty == []


def test_m0_m1_final_report_waiver_is_explicit() -> None:
    evidence = m0_m1_evidence_status()
    assert evidence["waiver_docs_present"] == ["docs/architecture/m0-scope.md", "docs/architecture/m1-scope.md"]
    assert evidence["waiver_applied"] is True
