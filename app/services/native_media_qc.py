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
        self.ffmpeg = ffmpeg or (
            sibling if Path(sibling).is_file() else shutil.which("ffmpeg")
        )

    def inspect(
        self, output: Path, expected: dict[str, Any], run_key: str
    ) -> MediaQCReport:
        failures: list[str] = []
        exists = (
            output.is_file() and not output.is_symlink() and output.stat().st_size > 0
        )
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
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(output),
            ],
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
        video = next(
            (item for item in streams if item.get("codec_type") == "video"), {}
        )
        audio = next(
            (item for item in streams if item.get("codec_type") == "audio"), {}
        )
        subtitle_streams = [
            item for item in streams if item.get("codec_type") == "subtitle"
        ]
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
        av_drift_within_limit = (
            av_drift_ms is not None and av_drift_ms <= max_av_drift_ms
        )
        expected_duration = expected.get("expected_duration_seconds")
        duration_ok = duration > 0 and (
            expected_duration is None
            or abs(duration - float(expected_duration)) <= 0.25
        )
        container_mp4 = "mp4" in str(data.get("format", {}).get("format_name", ""))
        video_codec_h264 = video.get("codec_name") == "h264"
        width = video.get("width")
        height = video.get("height")
        dimensions_match_expected = (
            bool(width and height)
            and (expected.get("width") is None or width == expected.get("width"))
            and (expected.get("height") is None or height == expected.get("height"))
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
                or int(audio.get("sample_rate", 0) or 0)
                == int(expected["sample_rate"]),
                expected.get("channels") is None
                or audio.get("channels") == expected.get("channels"),
            )
        )
        stream_integrity = bool(probe_ok and video and audio and full_decode)
        black_output_absent: bool | None = None
        if expected.get("black_output_check_required"):
            black_probe = subprocess.run(
                [
                    self.ffmpeg,
                    "-hide_banner",
                    "-nostdin",
                    "-i",
                    str(output),
                    "-vf",
                    # pix_th is the per-pixel luma threshold, not the required
                    # percentage of black pixels.  A value near 1.0 labels
                    # ordinary dark branded backgrounds as black.  pic_th
                    # expresses the intended 98% frame-coverage threshold.
                    "blackdetect=d=0.40:pix_th=0.10:pic_th=0.98",
                    "-an",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
            )
            black_output_absent = (
                black_probe.returncode == 0 and "black_start:" not in black_probe.stderr
            )
        scene_coverage: bool | None = None
        if expected.get("scene_coverage_required"):
            frames = [
                _frame_bytes(
                    self.ffmpeg,
                    output,
                    seconds=float(seconds),
                    filtergraph="scale=96:54,format=gray",
                )
                for seconds in list(expected.get("scene_probe_seconds") or [])
            ]
            fingerprints = {
                hashlib.sha256(frame).hexdigest() for frame in frames if frame
            }
            scene_coverage = (
                bool(frames) and all(frames) and len(fingerprints) == len(frames)
            )
        checksum_sha256 = _sha256_file(output)
        drawtext_checks = _measure_drawtext_contract(output, expected)
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
                "subtitle_stream_count": len(subtitle_streams),
                "subtitle_stream_codecs": sorted(
                    str(item.get("codec_name") or "") for item in subtitle_streams
                ),
                "black_output_absent": black_output_absent,
                "blackdetect": "PASS"
                if black_output_absent is True
                else "NOT_REQUIRED"
                if black_output_absent is None
                else "FAIL",
                "audio_presence": bool(audio),
                "av_drift_ms": av_drift_ms,
                "timeline_coverage": scene_coverage,
                **drawtext_checks,
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
        if expected.get("black_output_check_required"):
            requirements["black_output_absent"] = True
        if expected.get("subtitle_stream_count") is not None:
            requirements["subtitle_stream_count"] = int(
                expected["subtitle_stream_count"]
            )
        if (
            expected.get("narration_drawtext_count") is not None
            or expected.get("semantic_overlay_drawtext_count") is not None
        ):
            requirements["drawtext_filtergraph_attested"] = True
        if expected.get("narration_drawtext_count") is not None:
            requirements["narration_drawtext_count"] = int(
                expected["narration_drawtext_count"]
            )
        if expected.get("semantic_overlay_drawtext_count") is not None:
            requirements["semantic_overlay_drawtext_count"] = int(
                expected["semantic_overlay_drawtext_count"]
            )
        if expected.get("scene_coverage_required"):
            requirements["timeline_coverage"] = True
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
    if (
        value is None
        and stream.get("duration_ts") is not None
        and stream.get("time_base")
    ):
        try:
            return float(
                Fraction(str(stream["time_base"])) * int(stream["duration_ts"])
            )
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


def _measure_drawtext_contract(
    output: Path, expected: dict[str, Any]
) -> dict[str, Any]:
    """Attest and classify every drawtext filter used by the render command.

    Only command-bound semantic-overlay filter hashes are classified as semantic.
    Every additional drawtext filter is fail-closed as narration/unapproved text.
    """

    if (
        expected.get("narration_drawtext_count") is None
        and expected.get("semantic_overlay_drawtext_count") is None
    ):
        return {}

    checks: dict[str, Any] = {
        "drawtext_filtergraph_attested": False,
        "drawtext_filtergraph_checksum_sha256": None,
        "drawtext_filter_count": None,
        "semantic_overlay_drawtext_count": None,
        "narration_drawtext_count": None,
    }

    raw_path = expected.get("drawtext_filtergraph_path")
    expected_checksum = expected.get("drawtext_filtergraph_checksum_sha256")
    authorized_hashes = expected.get("semantic_overlay_drawtext_filter_hashes")
    expected_semantic_count = expected.get("semantic_overlay_drawtext_count")
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or not _is_sha256(expected_checksum)
        or not isinstance(authorized_hashes, list)
        or any(not _is_sha256(value) for value in authorized_hashes)
        or len(set(authorized_hashes)) != len(authorized_hashes)
        or not isinstance(expected_semantic_count, int)
        or isinstance(expected_semantic_count, bool)
        or expected_semantic_count < 0
        or len(authorized_hashes) != expected_semantic_count
    ):
        return checks

    filtergraph = Path(raw_path)
    try:
        resolved_filtergraph = filtergraph.resolve(strict=True)
        resolved_output_parent = output.parent.resolve(strict=True)
        if (
            filtergraph.is_symlink()
            or not resolved_filtergraph.is_file()
            or resolved_filtergraph.parent != resolved_output_parent
            or resolved_filtergraph.stat().st_size > 8 * 1024 * 1024
        ):
            return checks
        raw_graph = resolved_filtergraph.read_bytes()
    except (OSError, RuntimeError):
        return checks

    actual_checksum = hashlib.sha256(raw_graph).hexdigest()
    checks["drawtext_filtergraph_checksum_sha256"] = actual_checksum
    if actual_checksum != expected_checksum:
        return checks
    try:
        graph = raw_graph.decode("utf-8")
    except UnicodeDecodeError:
        return checks

    actual_hashes = [
        hashlib.sha256(item.encode("utf-8")).hexdigest()
        for item in _drawtext_filters(graph)
    ]
    remaining_authorized = list(authorized_hashes)
    semantic_count = 0
    for actual_hash in actual_hashes:
        if actual_hash in remaining_authorized:
            semantic_count += 1
            remaining_authorized.remove(actual_hash)

    checks.update(
        {
            "drawtext_filtergraph_attested": True,
            "drawtext_filter_count": len(actual_hashes),
            "semantic_overlay_drawtext_count": semantic_count,
            "narration_drawtext_count": len(actual_hashes) - semantic_count,
        }
    )
    return checks


def _drawtext_filters(graph: str) -> list[str]:
    """Return exact drawtext filter clauses from one generated filter chain."""

    filters: list[str] = []
    start = 0
    escaped = False
    for index, char in enumerate(graph):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ",":
            filters.append(graph[start:index])
            start = index + 1
    filters.append(graph[start:])

    drawtext: list[str] = []
    for item in filters:
        clause = item.strip()
        if not clause.startswith("drawtext="):
            continue
        if clause.endswith("[v]"):
            clause = clause[:-3]
        drawtext.append(clause)
    return drawtext


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _frame_bytes(
    ffmpeg: str | None,
    output: Path,
    *,
    seconds: float,
    filtergraph: str,
) -> bytes:
    if not ffmpeg:
        return b""
    frame = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-ss",
            f"{max(0.0, seconds):.6f}",
            "-i",
            str(output),
            "-frames:v",
            "1",
            "-vf",
            filtergraph,
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
    )
    return frame.stdout if frame.returncode == 0 else b""


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
