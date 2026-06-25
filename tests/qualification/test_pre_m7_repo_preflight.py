from __future__ import annotations

from .helpers.ffmpeg_checks import assert_ffmpeg_available
from .helpers.git_checks import collect_git_status
from .helpers.qualification_asserts import (
    REQUIRED_SOURCE_OF_TRUTH_PATHS,
    m0_m1_evidence_status,
    missing_paths,
)


ALLOWED_QUALIFICATION_DIRTY_PREFIXES = (
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
