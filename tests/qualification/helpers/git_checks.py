from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .qualification_asserts import MEDIA_GITIGNORE_PATTERNS, REQUIRED_TAGS, ROOT


MEDIA_SUFFIXES = {".mp4", ".mov", ".wav"}


@dataclass(frozen=True)
class GitStatus:
    head_commit: str
    required_tags: dict[str, bool]
    porcelain: list[str]
    staged_binary_media: list[str]
    media_gitignore: dict[str, bool]

    @property
    def working_tree_clean(self) -> bool:
        return not self.porcelain


def git(args: list[str], *, root: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)


def head_commit(*, root: Path = ROOT) -> str:
    result = git(["rev-parse", "HEAD"], root=root)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def tag_exists(tag: str, *, root: Path = ROOT) -> bool:
    result = git(["rev-parse", "-q", "--verify", f"refs/tags/{tag}"], root=root)
    return result.returncode == 0


def required_tags_status(*, root: Path = ROOT) -> dict[str, bool]:
    return {tag: tag_exists(tag, root=root) for tag in sorted(REQUIRED_TAGS)}


def status_porcelain(*, root: Path = ROOT) -> list[str]:
    result = git(["status", "--porcelain"], root=root)
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line.strip()]


def gitignore_covers_media(*, root: Path = ROOT) -> dict[str, bool]:
    lines = set((root / ".gitignore").read_text(encoding="utf-8").splitlines())
    return {pattern: pattern in lines for pattern in sorted(MEDIA_GITIGNORE_PATTERNS)}


def staged_files(*, root: Path = ROOT) -> list[str]:
    result = git(["diff", "--cached", "--name-only", "--diff-filter=ACMRT"], root=root)
    assert result.returncode == 0, result.stderr
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def staged_binary_media(*, root: Path = ROOT) -> list[str]:
    return [path for path in staged_files(root=root) if Path(path).suffix.lower() in MEDIA_SUFFIXES]


def is_gitignored(path: Path, *, root: Path = ROOT) -> bool:
    result = git(["check-ignore", str(path)], root=root)
    return result.returncode == 0


def collect_git_status(*, root: Path = ROOT) -> GitStatus:
    return GitStatus(
        head_commit=head_commit(root=root),
        required_tags=required_tags_status(root=root),
        porcelain=status_porcelain(root=root),
        staged_binary_media=staged_binary_media(root=root),
        media_gitignore=gitignore_covers_media(root=root),
    )
