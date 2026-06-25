from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None and ffprobe_path() is not None


def assert_ffmpeg_available() -> None:
    assert ffmpeg_path() is not None, "ffmpeg missing; Pre-M7 must be BLOCKED, not PASS"
    assert ffprobe_path() is not None, "ffprobe missing; Pre-M7 must be BLOCKED, not PASS"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ffprobe_duration(path: Path, *, timeout: int = 15) -> float:
    probe = ffprobe_path()
    assert probe is not None, "ffprobe missing"
    result = subprocess.run(
        [
            probe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return float(result.stdout.strip())
