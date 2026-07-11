#!/usr/bin/env python3
"""Local-only ffprobe/decode QC helper for NR0-LITE."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from faststart_check import inspect as faststart_inspect


def probe(ffprobe: str, media: Path) -> dict:
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(media)],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def qc(ffmpeg: str, ffprobe: str, media: Path, expected: dict) -> tuple[dict, dict]:
    raw = probe(ffprobe, media)
    streams = raw.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    decode = subprocess.run([ffmpeg, "-v", "error", "-i", str(media), "-f", "null", "-"], text=True, capture_output=True)
    fmt = raw.get("format", {})
    duration = float(fmt.get("duration", 0) or 0)
    video_duration = float((video or {}).get("duration", duration) or duration)
    audio_duration = float((audio or {}).get("duration", duration) or duration)
    av_drift_ms = round(abs(video_duration - audio_duration) * 1000, 3) if video and audio else None
    checks = {
        "exists": media.is_file(),
        "non_empty": media.stat().st_size > 0 if media.exists() else False,
        "container_mp4": fmt.get("format_name", "").find("mp4") >= 0,
        "video_stream": video is not None,
        "audio_stream": audio is not None if expected.get("audio_required", False) else True,
        "decode_ok": decode.returncode == 0 and not decode.stderr.strip(),
        "codec_h264": (video or {}).get("codec_name") == "h264",
        "width": (video or {}).get("width") == expected.get("width", 1920),
        "height": (video or {}).get("height") == expected.get("height", 1080),
        "pix_fmt": (video or {}).get("pix_fmt") == "yuv420p",
        "fps": (video or {}).get("r_frame_rate") == expected.get("fps", "30/1"),
        "bt709": all((video or {}).get(key) == "bt709" for key in ("color_space", "color_transfer", "color_primaries")),
        "aac_48k_stereo": (not expected.get("audio_required", False)) or ((audio or {}).get("codec_name") == "aac" and str((audio or {}).get("sample_rate")) == "48000" and (audio or {}).get("channels") == 2),
        "av_drift_le_250ms": av_drift_ms is None or av_drift_ms <= 250,
    }
    faststart = faststart_inspect(media)
    checks["faststart"] = faststart["moov_before_mdat"]
    def diagnostic(args: list[str], marker: str) -> dict:
        completed = subprocess.run([ffmpeg, "-hide_banner", "-v", "info", "-i", str(media), *args, "-f", "null", "-"], text=True, capture_output=True)
        lines = [line for line in completed.stderr.splitlines() if marker in line]
        return {"exit_code": completed.returncode, "event_count": len(lines), "events": lines[:20]}
    diagnostics = {
        "blackdetect": diagnostic(["-vf", "blackdetect=d=0.10:pix_th=0.10", "-an"], "blackdetect"),
        "freezedetect": diagnostic(["-vf", "freezedetect=n=0.003:d=2", "-an"], "freezedetect"),
    }
    if expected.get("audio_required", False):
        diagnostics["silencedetect"] = diagnostic(["-af", "silencedetect=noise=-50dB:d=1", "-vn"], "silencedetect")
    report = {
        "media": str(media),
        "expected": expected,
        "duration_seconds": duration,
        "video_duration_seconds": video_duration if video else None,
        "audio_duration_seconds": audio_duration if audio else None,
        "av_drift_ms": av_drift_ms,
        "checks": checks,
        "faststart": faststart,
        "decode_stderr": decode.stderr[-4000:],
        "diagnostics": diagnostics,
        "overall_pass": all(checks.values()),
    }
    return raw, report


if __name__ == "__main__":
    ffmpeg, ffprobe, media, target = sys.argv[1:]
    raw, report = qc(ffmpeg, ffprobe, Path(media), json.loads(target))
    print(json.dumps({"ffprobe": raw, "qc": report}, indent=2))
