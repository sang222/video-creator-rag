from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from app.services.native_ffmpeg_renderer import (
    FFMPEG_FULL_DEFAULT,
    FFPROBE_FULL_DEFAULT,
)


MINIMUM_RENDER_FREE_BYTES = 2 * 1024**3


def probe_mr1_production_toolchain(
    *,
    workspace_root: Path,
    allowed_workspace_root: Path,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    minimum_free_bytes: int = MINIMUM_RENDER_FREE_BYTES,
) -> dict[str, Any]:
    """Exercise the exact local capabilities MR1 needs before provider submit.

    The probe creates only a short-lived local clip below ``workspace_root``.  It
    neither imports a provider adapter nor performs a network request.  Listing
    a codec/filter is not enough: the probe actually renders a semantic native
    drawtext overlay, runs blackdetect, encodes libx264/AAC, probes both
    streams, and fully decodes the resulting bytes. Captions remain external
    SRT sidecars and are deliberately outside this MP4 capability probe.
    """

    requested_workspace = Path(workspace_root)
    resolved_workspace = requested_workspace.resolve()
    resolved_allowed = Path(allowed_workspace_root).resolve()
    ffmpeg_path = _resolve_executable(
        ffmpeg,
        preferred=FFMPEG_FULL_DEFAULT,
        fallback_name="ffmpeg",
    )
    ffprobe_path = _resolve_executable(
        ffprobe,
        preferred=FFPROBE_FULL_DEFAULT,
        fallback_name="ffprobe",
    )
    checks: dict[str, bool] = {
        "workspace_contained": _is_contained(resolved_workspace, resolved_allowed),
        "workspace_not_symlink": not requested_workspace.is_symlink(),
        "workspace_writable": False,
        "render_disk_space_available": False,
        "ffmpeg_executable": ffmpeg_path is not None,
        "ffprobe_executable": ffprobe_path is not None,
        "libx264_encoder_available": False,
        "aac_encoder_available": False,
        "drawtext_filter_available": False,
        "blackdetect_filter_available": False,
        "actual_local_encode_pass": False,
        "actual_h264_stream_verified": False,
        "actual_aac_stream_verified": False,
        "actual_local_decode_pass": False,
    }
    reason_codes: list[str] = []
    command_count = 0

    try:
        if not checks["workspace_contained"]:
            raise RuntimeError("MR1_TOOLCHAIN_WORKSPACE_ESCAPE")
        if not checks["workspace_not_symlink"]:
            raise RuntimeError("MR1_TOOLCHAIN_WORKSPACE_SYMLINK")
        if ffmpeg_path is None:
            raise RuntimeError("MR1_TOOLCHAIN_FFMPEG_UNAVAILABLE")
        if ffprobe_path is None:
            raise RuntimeError("MR1_TOOLCHAIN_FFPROBE_UNAVAILABLE")
        resolved_workspace.mkdir(parents=True, exist_ok=True)
        checks["render_disk_space_available"] = (
            shutil.disk_usage(resolved_workspace).free >= minimum_free_bytes
        )
        if not checks["render_disk_space_available"]:
            raise RuntimeError("MR1_TOOLCHAIN_DISK_SPACE_INSUFFICIENT")

        with tempfile.TemporaryDirectory(
            prefix=".mr1-toolchain-readiness-", dir=resolved_workspace
        ) as temporary:
            work = Path(temporary).resolve()
            if not _is_contained(work, resolved_workspace):
                raise RuntimeError("MR1_TOOLCHAIN_TEMP_WORKSPACE_ESCAPE")
            writable_probe = work / "writable.probe"
            with writable_probe.open("xb") as stream:
                stream.write(b"mr1-local-readiness")
                stream.flush()
                os.fsync(stream.fileno())
            checks["workspace_writable"] = writable_probe.is_file()

            encoders = _run_probe_command(
                [ffmpeg_path, "-hide_banner", "-encoders"],
                cwd=work,
                timeout=20,
            )
            command_count += 1
            if encoders.returncode != 0:
                raise RuntimeError("MR1_TOOLCHAIN_ENCODER_LIST_FAILED")
            encoder_listing = (encoders.stdout or "") + (encoders.stderr or "")
            checks["libx264_encoder_available"] = _listing_contains(
                encoder_listing, "libx264"
            )
            checks["aac_encoder_available"] = _listing_contains(encoder_listing, "aac")

            filters = _run_probe_command(
                [ffmpeg_path, "-hide_banner", "-filters"],
                cwd=work,
                timeout=20,
            )
            command_count += 1
            if filters.returncode != 0:
                raise RuntimeError("MR1_TOOLCHAIN_FILTER_LIST_FAILED")
            filter_listing = (filters.stdout or "") + (filters.stderr or "")
            checks["drawtext_filter_available"] = _listing_contains(
                filter_listing, "drawtext"
            )
            checks["blackdetect_filter_available"] = _listing_contains(
                filter_listing, "blackdetect"
            )
            listed_requirements = (
                "libx264_encoder_available",
                "aac_encoder_available",
                "drawtext_filter_available",
                "blackdetect_filter_available",
            )
            if not all(checks[name] for name in listed_requirements):
                raise RuntimeError("MR1_TOOLCHAIN_REQUIRED_CAPABILITY_MISSING")

            output = work / "probe.mp4"
            filtergraph = (
                "[0:v]drawtext=font='Arial':text='MR1':fontcolor=white:"
                "fontsize=22:x=12:y=12,"
                "blackdetect=d=0.10:pix_th=0.10:pic_th=0.98,"
                "format=yuv420p[v]"
            )
            encoded = _run_probe_command(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "verbose",
                    "-nostdin",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=0x18304d:s=320x180:r=30:d=0.60",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=880:sample_rate=48000:duration=0.60",
                    "-filter_complex",
                    filtergraph,
                    "-map",
                    "[v]",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-t",
                    "0.60",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(output),
                ],
                cwd=work,
                timeout=30,
            )
            command_count += 1
            if (
                encoded.returncode != 0
                or not output.is_file()
                or output.stat().st_size <= 0
            ):
                raise RuntimeError("MR1_TOOLCHAIN_ACTUAL_ENCODE_FAILED")
            checks["actual_local_encode_pass"] = True
            probed = _run_probe_command(
                [
                    ffprobe_path,
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(output),
                ],
                cwd=work,
                timeout=20,
            )
            command_count += 1
            if probed.returncode != 0:
                raise RuntimeError("MR1_TOOLCHAIN_ACTUAL_PROBE_FAILED")
            try:
                media = json.loads(probed.stdout or "{}")
            except json.JSONDecodeError:
                raise RuntimeError("MR1_TOOLCHAIN_ACTUAL_PROBE_JSON_INVALID") from None
            streams = media.get("streams") if isinstance(media, dict) else None
            if not isinstance(streams, list):
                raise RuntimeError("MR1_TOOLCHAIN_ACTUAL_PROBE_STREAMS_INVALID")
            video = next(
                (item for item in streams if item.get("codec_type") == "video"),
                {},
            )
            audio = next(
                (item for item in streams if item.get("codec_type") == "audio"),
                {},
            )
            checks["actual_h264_stream_verified"] = bool(
                video.get("codec_name") == "h264"
                and video.get("width") == 320
                and video.get("height") == 180
            )
            checks["actual_aac_stream_verified"] = bool(
                audio.get("codec_name") == "aac"
                and int(audio.get("sample_rate") or 0) == 48000
                and int(audio.get("channels") or 0) == 2
            )
            if not (
                checks["actual_h264_stream_verified"]
                and checks["actual_aac_stream_verified"]
            ):
                raise RuntimeError("MR1_TOOLCHAIN_ACTUAL_STREAMS_INVALID")

            decoded = _run_probe_command(
                [
                    ffmpeg_path,
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(output),
                    "-map",
                    "0",
                    "-f",
                    "null",
                    "-",
                ],
                cwd=work,
                timeout=30,
            )
            command_count += 1
            checks["actual_local_decode_pass"] = decoded.returncode == 0
            if not checks["actual_local_decode_pass"]:
                raise RuntimeError("MR1_TOOLCHAIN_ACTUAL_DECODE_FAILED")
    except Exception as exc:
        reason_codes.append(_safe_reason(exc))

    failed = sorted(key for key, passed in checks.items() if passed is not True)
    return {
        "schema_version": "mr1.production-toolchain-readiness.v1",
        "mode": "LOCAL_CAPABILITY_PROBE_NO_PROVIDER_NO_NETWORK",
        "ffmpeg_path": ffmpeg_path,
        "ffprobe_path": ffprobe_path,
        "workspace_root": str(resolved_workspace),
        "minimum_free_bytes": minimum_free_bytes,
        "checks": {
            key: "PASS" if passed else "FAIL" for key, passed in sorted(checks.items())
        },
        "failed_checks": failed,
        "reason_codes": reason_codes,
        "local_probe_command_count": command_count,
        "production_render_calls": 0,
        "provider_calls": 0,
        "drive_calls": 0,
        "youtube_calls": 0,
        "temporary_probe_artifacts_retained": False,
        "result": "PASS" if not failed else "FAIL",
        "checked_at": datetime.now(UTC).isoformat(),
    }


def _resolve_executable(
    requested: str | None, *, preferred: str, fallback_name: str
) -> str | None:
    candidates = [requested, preferred, shutil.which(fallback_name)]
    for raw in candidates:
        if not raw:
            continue
        path = Path(str(raw)).resolve()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _listing_contains(value: str, name: str) -> bool:
    for line in value.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] == name:
            return True
    return False


def _is_contained(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _run_probe_command(
    argv: Sequence[str], *, cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=timeout,
    )


def _safe_reason(exc: Exception) -> str:
    value = str(exc)
    if value.startswith("MR1_TOOLCHAIN_") and " " not in value:
        return value
    return "MR1_TOOLCHAIN_LOCAL_PROBE_FAILED"
