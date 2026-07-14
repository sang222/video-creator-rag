from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.contracts.native_renderer import MediaQCReport


class NativeMediaQC:
    def __init__(self, ffprobe: str, ffmpeg: str | None = None):
        self.ffprobe = ffprobe
        sibling = str(Path(ffprobe).with_name("ffmpeg"))
        self.ffmpeg = ffmpeg or (sibling if Path(sibling).is_file() else shutil.which("ffmpeg"))

    def inspect(self, output: Path, expected: dict[str, Any], run_key: str) -> MediaQCReport:
        failures: list[str] = []
        exists = output.is_file() and not output.is_symlink() and output.stat().st_size > 0
        checks: dict[str, Any] = {"exists_nonempty": exists}
        if not exists:
            return MediaQCReport(
                run_key=run_key,
                result="FAIL",
                checks=checks,
                reason_codes=["OUTPUT_MISSING"],
                human_review_required=True,
                created_at=datetime.now(UTC),
            )

        probe = subprocess.run(
            [self.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
            capture_output=True,
            text=True,
        )
        probe_ok = probe.returncode == 0
        if not probe_ok:
            failures.append("FFPROBE_FAILED")
            data: dict[str, Any] = {}
        else:
            try:
                data = json.loads(probe.stdout)
            except json.JSONDecodeError:
                failures.append("FFPROBE_JSON_INVALID")
                data = {}
        streams = data.get("streams", [])
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
        duration = float(data.get("format", {}).get("duration", 0) or 0)
        fps = _fps(video.get("avg_frame_rate"))
        full_decode = False
        if self.ffmpeg:
            decode = subprocess.run(
                [
                    self.ffmpeg,
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
                capture_output=True,
                text=True,
            )
            full_decode = decode.returncode == 0
        video_duration = _stream_duration(video)
        audio_duration = _stream_duration(audio)
        av_drift_ms = (
            round(abs(video_duration - audio_duration) * 1000)
            if video_duration is not None and audio_duration is not None
            else None
        )
        max_av_drift_ms = int(expected.get("max_av_drift_ms", 250))
        if max_av_drift_ms < 0:
            raise ValueError("QC_MAX_AV_DRIFT_INVALID")
        av_drift_within_limit = av_drift_ms is not None and av_drift_ms <= max_av_drift_ms
        expected_duration = expected.get("expected_duration_seconds")
        duration_ok = duration > 0 and (
            expected_duration is None or abs(duration - float(expected_duration)) <= 0.25
        )
        container_mp4 = "mp4" in str(data.get("format", {}).get("format_name", ""))
        video_codec_h264 = video.get("codec_name") == "h264"
        width = video.get("width")
        height = video.get("height")
        dimensions_match_expected = bool(width and height) and (
            expected.get("width") is None or width == expected.get("width")
        ) and (
            expected.get("height") is None or height == expected.get("height")
        )
        fps_matches_expected = fps is not None and (
            expected.get("fps") is None or abs(fps - float(expected["fps"])) < 0.001
        )
        pixel_format_matches_expected = bool(video.get("pix_fmt")) and (
            expected.get("pix_fmt") is None
            or video.get("pix_fmt") == expected.get("pix_fmt")
        )
        color_space_matches_expected = bool(video.get("color_space")) and (
            expected.get("color") is None
            or video.get("color_space") == expected.get("color")
        )
        audio_format_matches_expected = bool(audio) and all(
            (
                expected.get("audio_codec") is None
                or audio.get("codec_name") == expected.get("audio_codec"),
                expected.get("sample_rate") is None
                or int(audio.get("sample_rate", 0) or 0) == int(expected["sample_rate"]),
                expected.get("channels") is None
                or audio.get("channels") == expected.get("channels"),
            )
        )
        stream_integrity = bool(probe_ok and video and audio and full_decode)
        checksum_sha256 = _sha256_file(output)
        checks.update(
            {
                "container_mp4": container_mp4,
                "video_codec_h264": video_codec_h264,
                "width": width,
                "height": height,
                "fps": fps,
                "pixel_format": video.get("pix_fmt"),
                "color_space": video.get("color_space"),
                "audio_codec": audio.get("codec_name"),
                "sample_rate": int(audio.get("sample_rate", 0) or 0),
                "channels": audio.get("channels"),
                "duration": duration,
                "duration_matches_expected": duration_ok,
                "full_decode": full_decode,
                "stream_integrity": stream_integrity,
                "av_drift_within_limit": av_drift_within_limit,
                "max_av_drift_ms": max_av_drift_ms,
                "fast_start": _fast_start_atom_order(output),
                "checksum_sha256": checksum_sha256,
                "codec_container_matches_expected": (
                    container_mp4
                    and video_codec_h264
                    and pixel_format_matches_expected
                    and color_space_matches_expected
                ),
                "dimensions_match_expected": dimensions_match_expected,
                "fps_matches_expected": fps_matches_expected,
                "audio_format_matches_expected": audio_format_matches_expected,
                "caption_likely_present": None,
                "blackdetect": "NOT_EVALUATED_BY_TECHNICAL_PROBE",
                "audio_presence": bool(audio),
                "av_drift_ms": av_drift_ms,
                "timeline_coverage": None,
            }
        )
        requirements = {
            "container_mp4": True,
            "video_codec_h264": True,
            "width": expected.get("width"),
            "height": expected.get("height"),
            "pixel_format": expected.get("pix_fmt", "yuv420p"),
            "audio_codec": expected.get("audio_codec", "aac"),
            "sample_rate": expected.get("sample_rate", 48000),
            "channels": expected.get("channels", 2),
            "audio_presence": True,
            "duration_matches_expected": True,
            "full_decode": True,
            "stream_integrity": True,
            "av_drift_within_limit": True,
            "fast_start": bool(expected.get("faststart", True)),
        }
        if expected.get("fps") is not None:
            requirements["fps"] = float(expected["fps"])
        if expected.get("color") is not None:
            requirements["color_space"] = expected["color"]
        for key, value in requirements.items():
            if value is not None and checks.get(key) != value:
                failures.append("QC_" + key.upper())
        return MediaQCReport(
            run_key=run_key,
            result="FAIL" if failures else "PASS",
            checks=checks,
            reason_codes=sorted(set(failures)),
            # Technical PASS never waives the separate human-watchability review.
            human_review_required=True,
            created_at=datetime.now(UTC),
        )


def _fps(value: Any) -> float | None:
    try:
        fraction = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    return float(fraction) if fraction.denominator else None


def _stream_duration(stream: dict[str, Any]) -> float | None:
    value = stream.get("duration")
    if value is None and stream.get("duration_ts") is not None and stream.get("time_base"):
        try:
            return float(Fraction(str(stream["time_base"])) * int(stream["duration_ts"]))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if value is None:
        tags = stream.get("tags") or {}
        value = tags.get("DURATION")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        if not isinstance(value, str):
            return None
        parts = value.split(":")
        if len(parts) != 3:
            return None
        try:
            hours, minutes, seconds = float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError:
            return None
        return hours * 3600 + minutes * 60 + seconds


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fast_start_atom_order(path: Path) -> bool:
    """Parse top-level ISO BMFF boxes and require moov before mdat."""

    moov_offset: int | None = None
    mdat_offset: int | None = None
    file_size = path.stat().st_size
    offset = 0
    with path.open("rb") as stream:
        while offset + 8 <= file_size:
            stream.seek(offset)
            header = stream.read(8)
            if len(header) != 8:
                break
            size, atom_type = struct.unpack(">I4s", header)
            header_size = 8
            if size == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    return False
                size = struct.unpack(">Q", extended)[0]
                header_size = 16
            elif size == 0:
                size = file_size - offset
            if size < header_size or offset + size > file_size:
                return False
            if atom_type == b"moov" and moov_offset is None:
                moov_offset = offset
            elif atom_type == b"mdat" and mdat_offset is None:
                mdat_offset = offset
            if moov_offset is not None and mdat_offset is not None:
                return moov_offset < mdat_offset
            offset += size
    return False
