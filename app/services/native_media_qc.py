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
        native_plan_checks = _measure_native_explanatory_plan(output, expected)
        native_visual_present: bool | None = None
        native_visual_metrics: list[dict[str, Any]] = []
        if expected.get("native_explanatory_visual_check_required"):
            native_frames = [
                _frame_bytes(
                    self.ffmpeg,
                    output,
                    seconds=float(seconds),
                    filtergraph="scale=160:90,format=gray",
                )
                for seconds in list(expected.get("native_visual_probe_seconds") or [])
            ]
            native_visual_metrics = [
                _native_visual_detail_metrics(frame, width=160, height=90)
                for frame in native_frames
            ]
            expected_scene_count = expected.get("native_explanatory_scene_count")
            native_visual_present = bool(
                isinstance(expected_scene_count, int)
                and not isinstance(expected_scene_count, bool)
                and expected_scene_count > 0
                and len(native_frames) == expected_scene_count
                and all(frame for frame in native_frames)
                and all(
                    metrics.get("detail_present") is True
                    for metrics in native_visual_metrics
                )
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
                "native_explanatory_visual_present": native_visual_present,
                "native_explanatory_visual_metrics": native_visual_metrics,
                **native_plan_checks,
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
        if expected.get("native_explanatory_drawtext_count") is not None:
            requirements["native_explanatory_drawtext_count"] = int(
                expected["native_explanatory_drawtext_count"]
            )
        if expected.get("scene_coverage_required"):
            requirements["timeline_coverage"] = True
        if expected.get("native_explanatory_visual_check_required"):
            requirements["native_explanatory_plan_attested"] = True
            requirements["native_presentation_window_policy_attested"] = True
            requirements["native_explanatory_visual_present"] = True
            requirements["native_explanatory_scene_count"] = int(
                expected["native_explanatory_scene_count"]
            )
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


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _measure_native_explanatory_plan(
    output: Path, expected: dict[str, Any]
) -> dict[str, Any]:
    if not expected.get("native_explanatory_visual_check_required"):
        return {}
    checks: dict[str, Any] = {
        "native_explanatory_plan_attested": False,
        "native_explanatory_plan_checksum_sha256": None,
        "native_explanatory_plan_hash": None,
        "native_explanatory_scene_count": None,
        "native_explanatory_visible_signature_count": None,
        "native_presentation_window_policy_attested": False,
        "native_presentation_window_policy_hash": None,
    }
    raw_path = expected.get("native_explanatory_plan_path")
    expected_checksum = expected.get("native_explanatory_plan_checksum_sha256")
    expected_plan_hash = expected.get("native_explanatory_plan_hash")
    expected_scene_count = expected.get("native_explanatory_scene_count")
    expected_window_policy_hash = expected.get("native_presentation_window_policy_hash")
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or not _is_sha256(expected_checksum)
        or not _is_sha256(expected_plan_hash)
        or not isinstance(expected_scene_count, int)
        or isinstance(expected_scene_count, bool)
        or expected_scene_count <= 0
        or not _is_sha256(expected_window_policy_hash)
    ):
        return checks
    plan_path = Path(raw_path)
    try:
        resolved = plan_path.resolve(strict=True)
        output_parent = output.parent.resolve(strict=True)
        if (
            plan_path.is_symlink()
            or not resolved.is_file()
            or resolved.parent != output_parent
            or resolved.stat().st_size > 8 * 1024 * 1024
        ):
            return checks
        raw = resolved.read_bytes()
        actual_checksum = hashlib.sha256(raw).hexdigest()
        checks["native_explanatory_plan_checksum_sha256"] = actual_checksum
        if actual_checksum != expected_checksum:
            return checks
        payload = json.loads(raw)
    except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError):
        return checks
    if not isinstance(payload, dict):
        return checks
    content_hash = payload.get("content_hash")
    body = {key: value for key, value in payload.items() if key != "content_hash"}
    specs = payload.get("scene_specs")
    window_policy = payload.get("presentation_window_policy")
    if (
        payload.get("schema_version") != "vcos.native-explanatory-render-plan.v1"
        or not isinstance(specs, list)
        or len(specs) != expected_scene_count
        or content_hash != _canonical_hash(body)
        or content_hash != expected_plan_hash
        or not isinstance(window_policy, dict)
        or window_policy.get("content_hash") != expected_window_policy_hash
        or window_policy.get("content_hash")
        != _canonical_hash(
            {
                key: value
                for key, value in window_policy.items()
                if key != "content_hash"
            }
        )
    ):
        return checks
    scene_ids: list[str] = []
    signatures: list[tuple[str, str, tuple[str, ...]]] = []
    for spec in specs:
        if not isinstance(spec, dict):
            return checks
        spec_body = {key: value for key, value in spec.items() if key != "content_hash"}
        labels = spec.get("step_labels")
        if (
            spec.get("content_hash") != _canonical_hash(spec_body)
            or not isinstance(spec.get("scene_id"), str)
            or not isinstance(spec.get("headline_hash"), str)
            or not isinstance(spec.get("composition"), str)
            or not isinstance(labels, list)
            or len(labels) != 3
            or any(not isinstance(label, str) for label in labels)
        ):
            return checks
        scene_ids.append(spec["scene_id"])
        signatures.append(
            (
                spec["headline_hash"],
                spec["composition"],
                tuple(labels),
            )
        )
    if len(scene_ids) != len(set(scene_ids)) or len(signatures) != len(set(signatures)):
        return checks
    windows = window_policy.get("windows")
    canonical_duration_ms = window_policy.get("canonical_duration_ms")
    maximum_hold_ms = window_policy.get("maximum_silence_hold_ms")
    if (
        window_policy.get("schema_version")
        != "vcos.native-presentation-window-policy.v1"
        or window_policy.get("binding_authority")
        != "ELEVENLABS_FORCED_ALIGNMENT_WORD_BOUNDARIES"
        or window_policy.get("presentation_policy")
        != "HOLD_PRECEDING_SCENE_ACROSS_BOUNDED_SILENCE"
        or window_policy.get("spoken_word_timing_unchanged") is not True
        or window_policy.get("timing_synthesized") is not False
        or not isinstance(canonical_duration_ms, int)
        or isinstance(canonical_duration_ms, bool)
        or canonical_duration_ms <= 0
        or not isinstance(maximum_hold_ms, int)
        or isinstance(maximum_hold_ms, bool)
        or not 0 < maximum_hold_ms <= 2_000
        or not isinstance(windows, list)
        or len(windows) != expected_scene_count
    ):
        return checks
    previous_presentation_end = 0
    for index, (scene_id, window) in enumerate(zip(scene_ids, windows, strict=True)):
        if not isinstance(window, dict):
            return checks
        try:
            binding_start = int(window["binding_start_ms"])
            binding_end = int(window["binding_end_ms"])
            presentation_start = int(window["presentation_start_ms"])
            presentation_end = int(window["presentation_end_ms"])
            leading_hold = int(window["leading_silence_hold_ms"])
            trailing_hold = int(window["trailing_silence_hold_ms"])
        except (KeyError, TypeError, ValueError):
            return checks
        if (
            window.get("scene_id") != scene_id
            or presentation_start != previous_presentation_end
            or binding_start < presentation_start
            or binding_end <= binding_start
            or presentation_end < binding_end
            or leading_hold != (binding_start if index == 0 else 0)
            or trailing_hold != presentation_end - binding_end
            or not 0 <= leading_hold <= maximum_hold_ms
            or not 0 <= trailing_hold <= maximum_hold_ms
        ):
            return checks
        previous_presentation_end = presentation_end
    if previous_presentation_end != canonical_duration_ms:
        return checks
    checks.update(
        {
            "native_explanatory_plan_attested": True,
            "native_explanatory_plan_hash": content_hash,
            "native_explanatory_scene_count": len(specs),
            "native_explanatory_visible_signature_count": len(signatures),
            "native_presentation_window_policy_attested": True,
            "native_presentation_window_policy_hash": expected_window_policy_hash,
        }
    )
    return checks


def _native_visual_detail_metrics(
    frame: bytes, *, width: int, height: int
) -> dict[str, Any]:
    expected_size = width * height
    if not isinstance(frame, bytes) or len(frame) != expected_size:
        return {
            "detail_present": False,
            "byte_count": len(frame) if isinstance(frame, bytes) else 0,
        }
    values = list(frame)
    unique_luma = len(set(values))
    luma_range = max(values) - min(values)
    bright_ratio = sum(value >= 190 for value in values) / expected_size
    edge_count = 0
    edge_total = 0
    for y in range(height):
        row = y * width
        for x in range(width - 1):
            edge_total += 1
            if abs(values[row + x + 1] - values[row + x]) >= 18:
                edge_count += 1
    for y in range(height - 1):
        row = y * width
        next_row = row + width
        for x in range(width):
            edge_total += 1
            if abs(values[next_row + x] - values[row + x]) >= 18:
                edge_count += 1
    edge_ratio = edge_count / edge_total if edge_total else 0.0
    detail_present = bool(
        unique_luma >= 20 and luma_range >= 96 and edge_ratio >= 0.008
    )
    return {
        "detail_present": detail_present,
        "byte_count": expected_size,
        "unique_luma_values": unique_luma,
        "luma_range": luma_range,
        "bright_pixel_ratio": round(bright_ratio, 6),
        "edge_ratio": round(edge_ratio, 6),
    }


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
        "native_explanatory_drawtext_count": None,
        "narration_drawtext_count": None,
    }

    raw_path = expected.get("drawtext_filtergraph_path")
    expected_checksum = expected.get("drawtext_filtergraph_checksum_sha256")
    authorized_hashes = expected.get("semantic_overlay_drawtext_filter_hashes")
    native_hashes = expected.get("native_explanatory_drawtext_filter_hashes", [])
    expected_semantic_count = expected.get("semantic_overlay_drawtext_count")
    expected_native_count = expected.get("native_explanatory_drawtext_count", 0)
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or not _is_sha256(expected_checksum)
        or not isinstance(authorized_hashes, list)
        or any(not _is_sha256(value) for value in authorized_hashes)
        or len(set(authorized_hashes)) != len(authorized_hashes)
        or not isinstance(native_hashes, list)
        or any(not _is_sha256(value) for value in native_hashes)
        or len(set(native_hashes)) != len(native_hashes)
        or not set(native_hashes).issubset(set(authorized_hashes))
        or not isinstance(expected_semantic_count, int)
        or isinstance(expected_semantic_count, bool)
        or expected_semantic_count < 0
        or len(authorized_hashes) != expected_semantic_count
        or not isinstance(expected_native_count, int)
        or isinstance(expected_native_count, bool)
        or expected_native_count < 0
        or len(native_hashes) != expected_native_count
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
    native_count = 0
    for actual_hash in actual_hashes:
        if actual_hash in remaining_authorized:
            semantic_count += 1
            remaining_authorized.remove(actual_hash)
            if actual_hash in native_hashes:
                native_count += 1

    checks.update(
        {
            "drawtext_filtergraph_attested": True,
            "drawtext_filter_count": len(actual_hashes),
            "semantic_overlay_drawtext_count": semantic_count,
            "native_explanatory_drawtext_count": native_count,
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
