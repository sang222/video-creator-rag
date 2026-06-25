from __future__ import annotations

from .helpers.git_checks import staged_binary_media
from .helpers.repo_scanners import all_scope_violations


def test_pre_m7_scope_guard_has_no_m7_plus_executable_implementation(engine) -> None:
    violations = all_scope_violations(engine)
    assert violations == []


def test_no_generated_binary_media_is_staged() -> None:
    assert staged_binary_media() == []
