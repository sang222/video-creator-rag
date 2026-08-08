from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.img_canary import IMGCanaryNativeHeadlineArtifact
from app.contracts.long_production import ProductionRenderExecutionEnvelope
from app.contracts.native_renderer import (
    CompiledNativeRenderManifest,
    FFmpegCommandManifest,
    MediaQCReport,
    NativeRenderExecutionReceipt,
    V2ProductionRenderExecutionEnvelope,
)
from app.services.native_media_qc import NativeMediaQC
from app.services.native_render_plan import stable_hash


FFMPEG_FULL_DEFAULT = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
FFPROBE_FULL_DEFAULT = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
COMMAND_BUILDER_VERSION = "native-ffmpeg-command-builder/1.1.0"
_RENDER_LOCK = threading.Lock()
IMG_CANARY_OVERLAY_PANEL_RGB = "08111f"
IMG_CANARY_OVERLAY_PANEL_OPACITY = 1.0
_V2_SEMANTIC_OVERLAY_TYPES = frozenset(
    {
        "SEMANTIC_CALLOUT",
        "DIAGRAM_LABEL",
        "DATA_LABEL",
        "UI_ANNOTATION",
        "TITLE_CARD",
    }
)


def srgb_hex_relative_luminance(value: str) -> float:
    """Calculate WCAG relative luminance for one six-digit sRGB color."""

    if len(value) != 6 or any(char not in "0123456789abcdefABCDEF" for char in value):
        raise ValueError("SRGB_HEX_COLOR_INVALID")

    def linear(channel: int) -> float:
        normalized = channel / 255.0
        return (
            normalized / 12.92
            if normalized <= 0.04045
            else ((normalized + 0.055) / 1.055) ** 2.4
        )

    red, green, blue = (
        linear(int(value[index : index + 2], 16)) for index in (0, 2, 4)
    )
    return round(0.2126 * red + 0.7152 * green + 0.0722 * blue, 8)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _inside(root: Path, value: Path, *, must_exist: bool = False) -> Path:
    root = root.resolve()
    candidate = value if value.is_absolute() else root / value
    if ".." in candidate.parts:
        raise ValueError("PATH_TRAVERSAL_REJECTED")
    resolved = candidate.resolve(strict=must_exist)
    if resolved != root and root not in resolved.parents:
        raise ValueError("PATH_OUTSIDE_WORKSPACE")
    if must_exist and candidate.is_symlink():
        raise ValueError("SYMLINK_INPUT_REJECTED")
    return resolved


def _filter_path(path: Path) -> str:
    """Escape one trusted local path for FFmpeg filter-option syntax."""

    return (
        str(path)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
    )


def _v2_native_font_path() -> Path:
    """Resolve one explicit cross-platform font for production drawtext."""

    configured = str(os.getenv("VCOS_V2_NATIVE_FONT_PATH") or "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if not candidate.is_file():
            raise FileNotFoundError("V2_NATIVE_CONFIGURED_FONT_NOT_FOUND")
        return candidate
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    )
    font = next((candidate for candidate in candidates if candidate.is_file()), None)
    if font is None:
        raise FileNotFoundError("V2_NATIVE_FONT_NOT_FOUND")
    return font.resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _available_filters(ffmpeg: str) -> set[str]:
    """Return the installed filter names for capability-aware command building."""

    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        check=True,
    )
    names: set[str] = set()
    for raw_line in f"{completed.stdout}\n{completed.stderr}".splitlines():
        parts = raw_line.split()
        if len(parts) >= 2 and len(parts[0]) in {2, 3}:
            names.add(parts[1])
    return names


def _v2_semantic_overlays(
    manifest: CompiledNativeRenderManifest,
) -> list[dict[str, str]]:
    """Validate explicit visual-text authority before it reaches drawtext."""

    required = {
        "overlay_id",
        "scene_id",
        "overlay_type",
        "text",
        "source_ref",
        "source_hash",
    }
    scene_ids = {str(item.get("scene_id") or "") for item in manifest.compiled_scenes}
    overlays: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in manifest.overlay_schedule:
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("V2_SEMANTIC_OVERLAY_CONTRACT_INVALID")
        overlay = {key: str(item.get(key) or "").strip() for key in required}
        if (
            not all(overlay.values())
            or overlay["overlay_id"] in seen
            or overlay["scene_id"] not in scene_ids
            or overlay["overlay_type"] not in _V2_SEMANTIC_OVERLAY_TYPES
            or not re.fullmatch(r"[0-9a-f]{64}", overlay["source_hash"])
        ):
            raise ValueError("V2_SEMANTIC_OVERLAY_CONTRACT_INVALID")
        seen.add(overlay["overlay_id"])
        overlays.append(overlay)
    return sorted(overlays, key=lambda item: (item["scene_id"], item["overlay_id"]))


def _command_hash_payload(command: FFmpegCommandManifest) -> dict:
    return command.model_dump(mode="json", exclude={"command_hash", "created_at"})


def _manifest_hash_payload(manifest: CompiledNativeRenderManifest) -> dict:
    return manifest.model_dump(
        mode="json",
        exclude={
            "compiled_manifest_id",
            "ffmpeg_binary_requirement",
            "manifest_hash",
            "created_at",
        },
    )


def _manifest_hash_matches(manifest: CompiledNativeRenderManifest) -> bool:
    """Accept the current canonical hash and the one historic reader shape.

    ``asset_request_plan`` was later added as an optional typed field.  Older
    sealed manifests were hashed before that field existed, so Pydantic reads
    them back as ``None`` even though their immutable hash intentionally lacks
    the key.  This permits retrieval/reconciliation of that historical shape;
    newly compiled manifests always use the complete typed payload.
    """

    payload = _manifest_hash_payload(manifest)
    if stable_hash(payload) == manifest.manifest_hash:
        return True
    if payload.get("asset_request_plan") is None:
        legacy_payload = dict(payload)
        legacy_payload.pop("asset_request_plan", None)
        return stable_hash(legacy_payload) == manifest.manifest_hash
    return False


def _write_text_atomic(path: Path, value: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.unlink(missing_ok=True)
    try:
        with part.open("x", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        if executable:
            part.chmod(0o700)
        os.replace(part, path)
        _fsync_directory(path.parent)
    finally:
        part.unlink(missing_ok=True)


def _persist_or_reuse_command(
    *,
    work: Path,
    candidate: FFmpegCommandManifest,
) -> FFmpegCommandManifest:
    """Persist one command identity or reuse its exact prior typed artifact."""

    manifest_path = work / "command_manifest.json"
    script_path = work / "command.sh"
    script = "#!/bin/sh\n" + shlex.join(candidate.sanitized_argv) + "\n"
    command = candidate
    if manifest_path.exists():
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise FileExistsError("COMMAND_MANIFEST_PATH_CONFLICT")
        try:
            prior = FFmpegCommandManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise FileExistsError("COMMAND_MANIFEST_INVALID") from exc
        if (
            stable_hash(_command_hash_payload(prior)) != prior.command_hash
            or prior.command_hash != candidate.command_hash
            or _command_hash_payload(prior) != _command_hash_payload(candidate)
        ):
            raise FileExistsError("COMMAND_MANIFEST_IDENTITY_CONFLICT")
        command = prior
    else:
        _write_text_atomic(manifest_path, candidate.model_dump_json(indent=2) + "\n")

    if script_path.exists():
        if (
            not script_path.is_file()
            or script_path.is_symlink()
            or script_path.read_text(encoding="utf-8") != script
        ):
            raise FileExistsError("COMMAND_SCRIPT_IDENTITY_CONFLICT")
    else:
        _write_text_atomic(script_path, script, executable=True)
    return command


def _load_completed_render(
    *,
    output: Path,
    work: Path,
    manifest: CompiledNativeRenderManifest,
    command: FFmpegCommandManifest,
) -> tuple[NativeRenderExecutionReceipt, MediaQCReport] | None:
    """Return a fully bound completed render without invoking FFmpeg again."""

    receipt_path = work / "execution_receipt.json"
    qc_path = work / "media_qc.json"
    present = (output.exists(), receipt_path.exists(), qc_path.exists())
    if not any(present):
        return None
    # Any state before the typed completion receipt is a recoverable crash
    # boundary: the deterministic renderer may recreate the output and QC.
    # Once a receipt exists, every claimed bound artifact must exist.
    if not present[1]:
        return None
    if present != (True, True, True):
        raise FileExistsError("IMG_CANARY_RENDER_COMPLETION_SET_INCOMPLETE")
    if any(
        path.is_symlink() or not path.is_file()
        for path in (output, receipt_path, qc_path)
    ):
        raise FileExistsError("IMG_CANARY_RENDER_COMPLETION_SET_INVALID")
    if Path(str(output) + ".part.mp4").exists():
        raise FileExistsError("IMG_CANARY_RENDER_PART_REMAINS")
    try:
        receipt = NativeRenderExecutionReceipt.model_validate_json(
            receipt_path.read_text(encoding="utf-8")
        )
        qc = MediaQCReport.model_validate_json(qc_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FileExistsError("IMG_CANARY_RENDER_COMPLETION_ARTIFACT_INVALID") from exc
    # Renderer receipt hashes historically canonicalize aware datetimes through
    # ``default=str``; preserve that exact contract when loading from JSON.
    receipt_payload = receipt.model_dump(mode="python", exclude={"receipt_hash"})
    output_checksum = _sha256_file(output)
    if (
        stable_hash(receipt_payload) != receipt.receipt_hash
        or receipt.run_key != command.run_key
        or receipt.command_hash != command.command_hash
        or receipt.manifest_refs
        != {
            "compiled_manifest": manifest.compiled_manifest_id,
            "compiled_manifest_hash": manifest.manifest_hash,
        }
        or Path(receipt.output_path).resolve() != output
        or receipt.output_checksum != output_checksum
        or receipt.exit_code != 0
        or receipt.local_only != (not manifest.production_eligible)
        or receipt.production_eligible != manifest.production_eligible
        or not receipt.no_provider_calls_confirmed
        or qc.run_key != command.run_key
        or qc.result != "PASS"
        or qc.checks.get("checksum_sha256") != output_checksum
    ):
        raise FileExistsError("IMG_CANARY_RENDER_COMPLETION_BINDING_MISMATCH")
    return receipt, qc


def _load_v2_render_execution_journal(
    *,
    work: Path,
    output: Path,
    manifest: CompiledNativeRenderManifest,
    command: FFmpegCommandManifest,
    envelope: V2ProductionRenderExecutionEnvelope,
) -> dict[str, object] | None:
    """Load and validate the durable intent written before FFmpeg starts."""

    path = work / "v2-render-execution-journal.json"
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise FileExistsError("V2_RENDER_EXECUTION_JOURNAL_INVALID")
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FileExistsError("V2_RENDER_EXECUTION_JOURNAL_INVALID") from exc
    if (
        not isinstance(journal, dict)
        or journal.get("schema_version") != "vcos.v2-render-execution-journal.v1"
        or journal.get("workflow_run_id") != str(envelope.workflow_run_id)
        or journal.get("command_id") != envelope.command_id
        or journal.get("run_key") != command.run_key
        or journal.get("command_hash") != command.command_hash
        or journal.get("manifest_hash") != manifest.manifest_hash
        or journal.get("authorization_hash") != envelope.authorization_hash
        or journal.get("output_path") != str(output)
        or journal.get("effect_invocation_count") != 1
        or journal.get("state") not in {"EFFECT_STARTED", "VERIFIED"}
        or not isinstance(journal.get("started_at"), str)
    ):
        raise FileExistsError("V2_RENDER_EXECUTION_JOURNAL_MISMATCH")
    try:
        started_at = datetime.fromisoformat(str(journal["started_at"]))
    except ValueError as exc:
        raise FileExistsError("V2_RENDER_EXECUTION_JOURNAL_MISMATCH") from exc
    if started_at.tzinfo is None:
        raise FileExistsError("V2_RENDER_EXECUTION_JOURNAL_MISMATCH")
    return journal


def _start_v2_render_execution_journal(
    *,
    work: Path,
    output: Path,
    manifest: CompiledNativeRenderManifest,
    command: FFmpegCommandManifest,
    envelope: V2ProductionRenderExecutionEnvelope,
    started_at: datetime,
) -> dict[str, object]:
    """Persist the one permitted render invocation before starting FFmpeg."""

    path = work / "v2-render-execution-journal.json"
    if path.exists():
        raise FileExistsError("V2_RENDER_EFFECT_ALREADY_STARTED")
    journal: dict[str, object] = {
        "schema_version": "vcos.v2-render-execution-journal.v1",
        "workflow_run_id": str(envelope.workflow_run_id),
        "command_id": envelope.command_id,
        "run_key": command.run_key,
        "command_hash": command.command_hash,
        "manifest_hash": manifest.manifest_hash,
        "authorization_hash": envelope.authorization_hash,
        "output_path": str(output),
        "effect_invocation_count": 1,
        "state": "EFFECT_STARTED",
        "started_at": started_at.isoformat(),
    }
    _write_text_atomic(
        path,
        json.dumps(journal, indent=2, sort_keys=True) + "\n",
    )
    return journal


def _seal_v2_render_execution_journal(
    *,
    work: Path,
    journal: dict[str, object],
    receipt: NativeRenderExecutionReceipt,
    recovered_after_effect: bool,
) -> None:
    """Seal the intent with exact output and receipt evidence."""

    sealed = {
        **journal,
        "state": "VERIFIED",
        "output_checksum": receipt.output_checksum,
        "execution_receipt_hash": receipt.receipt_hash,
        "completed_at": receipt.end_time.isoformat(),
        "recovered_after_effect": recovered_after_effect,
    }
    _write_text_atomic(
        work / "v2-render-execution-journal.json",
        json.dumps(sealed, indent=2, sort_keys=True) + "\n",
    )


def _recover_v2_completed_render(
    *,
    output: Path,
    work: Path,
    manifest: CompiledNativeRenderManifest,
    command: FFmpegCommandManifest,
    journal: dict[str, object],
) -> tuple[NativeRenderExecutionReceipt, MediaQCReport]:
    """Seal a final output left behind before its completion receipt.

    The durable invocation journal proves FFmpeg was already started.  Recovery
    therefore validates the final bytes and writes receipts, but never invokes
    FFmpeg a second time.
    """

    part = Path(str(output) + ".part.mp4")
    if (
        not output.is_file()
        or output.is_symlink()
        or part.exists()
        or (work / "execution_receipt.json").exists()
    ):
        raise FileExistsError("V2_RENDER_RECOVERY_OUTPUT_INVALID")
    fresh_qc = NativeMediaQC(command.ffprobe_binary_path).inspect(
        output,
        command.expected_qc,
        command.run_key,
    )
    if fresh_qc.result == "FAIL":
        raise RuntimeError(
            "V2_RENDER_RECOVERY_QC_FAILED:" + ",".join(fresh_qc.reason_codes)
        )
    qc_path = work / "media_qc.json"
    if qc_path.exists():
        if not qc_path.is_file() or qc_path.is_symlink():
            raise FileExistsError("V2_RENDER_RECOVERY_QC_INVALID")
        try:
            prior_qc = MediaQCReport.model_validate_json(
                qc_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise FileExistsError("V2_RENDER_RECOVERY_QC_INVALID") from exc
        if prior_qc.model_dump(exclude={"created_at"}) != fresh_qc.model_dump(
            exclude={"created_at"}
        ):
            raise FileExistsError("V2_RENDER_RECOVERY_QC_MISMATCH")
        qc = prior_qc
    else:
        qc = fresh_qc
        _write_text_atomic(qc_path, qc.model_dump_json(indent=2) + "\n")

    started = datetime.fromisoformat(str(journal["started_at"]))
    ended = datetime.now(UTC)
    checksum = _sha256_file(output)
    body = {
        "run_key": command.run_key,
        "manifest_refs": {
            "compiled_manifest": manifest.compiled_manifest_id,
            "compiled_manifest_hash": manifest.manifest_hash,
        },
        "command_hash": command.command_hash,
        "start_time": started,
        "end_time": ended,
        "exit_code": 0,
        "elapsed_time": max(0.0, (ended - started).total_seconds()),
        "realtime_factor": None,
        "peak_rss": None,
        "output_path": str(output),
        "output_checksum": checksum,
        "local_only": False,
        "production_eligible": True,
        "no_provider_calls_confirmed": True,
    }
    receipt = NativeRenderExecutionReceipt(
        receipt_hash=stable_hash(body),
        **body,
    )
    _write_text_atomic(
        work / "execution_receipt.json",
        receipt.model_dump_json(indent=2) + "\n",
    )
    _write_text_atomic(
        work / "cleanup_receipt.json",
        json.dumps(
            {
                "partial_files_remaining": 0,
                "completed_at": ended.isoformat(),
                "recovered_after_effect": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    _seal_v2_render_execution_journal(
        work=work,
        journal=journal,
        receipt=receipt,
        recovered_after_effect=True,
    )
    return receipt, qc


class FFmpegCommandBuilder:
    def __init__(
        self,
        workspace_root: Path,
        *,
        ffmpeg: str = FFMPEG_FULL_DEFAULT,
        ffprobe: str = FFPROBE_FULL_DEFAULT,
    ):
        self.root = workspace_root.resolve()
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe

    def build_synthetic(
        self,
        manifest: CompiledNativeRenderManifest,
        *,
        run_key: str,
        duration_seconds: float = 12.0,
    ) -> FFmpegCommandManifest:
        if manifest.production_eligible:
            raise ValueError("SYNTHETIC_BUILDER_REJECTS_PRODUCTION")
        if manifest.render_purpose == "CQR1_CONTROLLED_PAID_CANARY":
            raise ValueError("SYNTHETIC_BUILDER_REJECTS_PAID_CANARY")
        work = _inside(self.root, Path("runs") / run_key)
        work.mkdir(parents=True, exist_ok=True)
        canonical_strict = manifest.temporal_authority_mode == "CANONICAL_STRICT"
        if canonical_strict:
            if not manifest.compiled_scenes or not manifest.canonical_duration_ms:
                raise ValueError("TEMPORAL_CANONICAL_DURATION_REQUIRED")
            if (
                max(int(item["end_ms"]) for item in manifest.compiled_scenes)
                != manifest.canonical_duration_ms
            ):
                raise ValueError("TEMPORAL_AUDIO_ENDPOINT_MISMATCH")
            duration_seconds = manifest.canonical_duration_ms / 1000.0
        width = int(manifest.normalized_canvas["width"])
        height = int(manifest.normalized_canvas["height"])
        fps = int(manifest.normalized_canvas.get("fps", 30))
        output = _inside(self.root, work / "nr1_smoke.mp4")
        filtergraph = _inside(self.root, work / "filtergraph.txt")
        available_filters = _available_filters(self.ffmpeg)
        drawtext_available = "drawtext" in available_filters
        # Narration captions are a canonical SRT sidecar.  The renderer never
        # consumes their cues or source file: only semantic overlays are
        # composited into the video graph below.
        generated_text_files: list[str] = []
        panel_x, panel_y = round(width * 0.0625), round(height * 0.102)
        panel_w, panel_h = round(width * 0.875), round(height * 0.796)
        title_size = max(28, round(min(width, height) * 0.067))
        body_size = max(18, round(min(width, height) * 0.035))
        badge_size = max(16, round(min(width, height) * 0.022))
        label = (
            "VCOS CQR1 NON-PRODUCTION GOLDEN"
            if manifest.render_purpose == "CQR1_LOCAL_GOLDEN_FIXTURE"
            else "LOCAL SYNTHETIC SMOKE"
        )
        graph = (
            f"[0:v]fade=t=in:st=0:d=0.5,"
            f"drawbox=x={panel_x}:y={panel_y}:w={panel_w}:h={panel_h}:"
            "color=0x172033@1:t=fill,"
        )
        if drawtext_available:
            graph += (
                f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:"
                f"text='VCOS Native Renderer':fontcolor=white:fontsize={title_size}:"
                f"x=(w-text_w)/2:y={round(height * 0.20)},"
                f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:"
                "text='Canonical timeline / captions / duration':"
                f"fontcolor=0x6ee7ff:fontsize={body_size}:"
                f"x=(w-text_w)/2:y={round(height * 0.35)},"
            )
        graph += (
            f"drawbox=x={round(width * 0.094)}:y={round(height * 0.56)}:"
            f"w={round(width * 0.60)}:h={round(height * 0.12)}:"
            "color=0x2563eb@0.9:t=fill,"
        )
        if drawtext_available:
            graph += (
                f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:"
                f"text='{label}':fontcolor=white:fontsize={body_size}:"
                f"x={round(width * 0.115)}:y={round(height * 0.60)},"
            )
        graph += (
            f"drawbox=x={round(width * 0.89)}:y={round(height * 0.055)}:"
            f"w={round(width * 0.073)}:h={round(height * 0.056)}:"
            "color=white@0.9:t=fill"
        )
        if drawtext_available:
            graph += (
                ",drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:"
                f"text='VCOS':fontcolor=black:fontsize={badge_size}:"
                f"x={round(width * 0.91)}:y={round(height * 0.072)}"
            )
        graph += "[v]"
        filtergraph.write_text(graph + "\n", encoding="utf-8")
        part = str(output) + ".part.mp4"
        argv = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x0b1020:s={width}x{height}:r={fps}:d={duration_seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration_seconds}",
            "-filter_complex_script",
            str(filtergraph),
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-b:v",
            "8M",
            "-maxrate",
            "10M",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
        ]
        if canonical_strict:
            argv.extend(["-shortest", part])
        else:
            argv.extend(["-t", str(duration_seconds), part])
        version = subprocess.run(
            [self.ffmpeg, "-version"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[0]
        expected_qc = {
            **manifest.output_specs[0],
            "expected_duration_seconds": duration_seconds,
            "max_av_drift_ms": 250,
            "drawtext_filter_available": drawtext_available,
            "subtitle_stream_count": 0,
        }
        generated_file_checksums = {str(filtergraph): _sha256_file(filtergraph)}
        core = {
            "run_key": run_key,
            "compiled_manifest_ref": manifest.compiled_manifest_id,
            "compiled_manifest_hash": manifest.manifest_hash,
            "ffmpeg_binary_path": self.ffmpeg,
            "ffprobe_binary_path": self.ffprobe,
            "ffmpeg_version": version,
            "command_builder_version": COMMAND_BUILDER_VERSION,
            "input_files": [],
            "generated_filtergraph_path": str(filtergraph),
            "generated_text_files": generated_text_files,
            "generated_file_checksums": generated_file_checksums,
            "output_file": str(output),
            "output_profile": manifest.renderer_profile_refs[0],
            "sanitized_argv": argv,
            "working_directory": str(work),
            "expected_qc": expected_qc,
            "temporal_authority_mode": manifest.temporal_authority_mode,
            "canonical_media_timeline_ref": manifest.canonical_media_timeline_ref,
            "canonical_media_timeline_hash": manifest.canonical_media_timeline_hash,
            "canonical_audio_asset_ref": manifest.canonical_audio_asset_ref,
            "canonical_duration_ms": manifest.canonical_duration_ms,
            "canonical_caption_compilation_ref": manifest.canonical_caption_compilation_ref,
            "canonical_caption_compilation_hash": manifest.canonical_caption_compilation_hash,
        }
        command_hash = stable_hash(core)
        command = FFmpegCommandManifest(
            command_hash=command_hash, created_at=datetime.now(UTC), **core
        )
        (work / "command_manifest.json").write_text(
            command.model_dump_json(indent=2), encoding="utf-8"
        )
        (work / "command.sh").write_text(
            "#!/bin/sh\n" + shlex.join(argv) + "\n", encoding="utf-8"
        )
        return command

    def build_v2_local_native(
        self,
        manifest: CompiledNativeRenderManifest,
        *,
        run_key: str,
        audio_path: Path | None = None,
    ) -> FFmpegCommandManifest:
        """Compile package-derived timeline cards into a real production MP4."""

        if (
            not manifest.production_eligible
            or manifest.render_purpose != "VCOS_V2_NATIVE_PRODUCTION"
            or manifest.temporal_authority_mode != "CANONICAL_STRICT"
            or not manifest.canonical_duration_ms
            or not manifest.canonical_media_timeline_ref
            or not manifest.canonical_media_timeline_hash
        ):
            raise ValueError("V2_NATIVE_PRODUCTION_MANIFEST_REQUIRED")
        scenes = list(manifest.compiled_scenes)
        if (
            not scenes
            or max(int(scene["end_ms"]) for scene in scenes)
            != manifest.canonical_duration_ms
        ):
            raise ValueError("V2_NATIVE_PRODUCTION_TIMELINE_INVALID")

        work = _inside(self.root, Path("runs") / run_key)
        work.mkdir(parents=True, exist_ok=True)
        output = _inside(self.root, work / "v2-native-production.mp4")
        filtergraph = _inside(self.root, work / "filtergraph.txt")
        width = int(manifest.normalized_canvas["width"])
        height = int(manifest.normalized_canvas["height"])
        fps = int(manifest.normalized_canvas.get("fps", 30))
        duration_seconds = manifest.canonical_duration_ms / 1000.0
        semantic_overlays = _v2_semantic_overlays(manifest)
        if (
            manifest.normalized_caption.get("mode") != "SIDECAR_SRT_ONLY"
            or manifest.normalized_caption.get("render_consumes_caption_cues")
            is not False
            or manifest.caption_schedule.get("authority") != "SIDECAR_SRT_ONLY"
            or manifest.caption_schedule.get("render_consumes_caption_cues")
            is not False
        ):
            raise ValueError("V2_NATIVE_CAPTION_COMPOSITION_FORBIDDEN")
        if semantic_overlays:
            available_filters = _available_filters(self.ffmpeg)
            if "drawtext" not in available_filters:
                raise ValueError("V2_NATIVE_DRAWTEXT_FILTER_REQUIRED")
            font_ref = _filter_path(_v2_native_font_path())
        else:
            font_ref = ""

        overlay_size = max(22, round(min(width, height) * 0.033))
        generated_text_files: list[str] = []
        graph = "[0:v]format=yuv420p"
        palette = ("17324d", "31435f", "304d46", "4b3b5f", "563f35", "29465b")
        probe_seconds: list[float] = []
        scene_windows: dict[str, tuple[float, float]] = {}
        for index, scene in enumerate(scenes):
            start = int(scene["start_ms"]) / 1000.0
            end = int(scene["end_ms"]) / 1000.0
            if start < 0 or end <= start or end > duration_seconds + 0.001:
                raise ValueError("V2_NATIVE_SCENE_TIMING_INVALID")
            scene_id = str(scene.get("scene_id") or "")
            if not scene_id or scene_id in scene_windows:
                raise ValueError("V2_NATIVE_SCENE_IDENTITY_INVALID")
            scene_windows[scene_id] = (start, end)
            enable = f"between(t\\,{start:.3f}\\,{end:.3f})"
            color = str(scene.get("background_rgb") or palette[index % len(palette)])
            if len(color) != 6 or any(
                char not in "0123456789abcdefABCDEF" for char in color
            ):
                raise ValueError("V2_NATIVE_SCENE_COLOR_INVALID")
            graph += (
                f",drawbox=x=0:y=0:w=iw:h=ih:color=0x{color}:t=fill:"
                f"enable='{enable}'"
                f",drawbox=x={round(width * 0.06)}:y={round(height * 0.10)}:"
                f"w={round(width * 0.88)}:h={round(height * 0.78)}:"
                f"color=0x08111f@0.82:t=fill:enable='{enable}'"
            )
            if len(probe_seconds) < 4:
                probe_seconds.append(round(start + (end - start) / 2, 3))
        for index, overlay in enumerate(semantic_overlays, start=1):
            start, end = scene_windows[overlay["scene_id"]]
            text_path = _inside(
                self.root, work / f"semantic-overlay-{index:03d}.txt"
            )
            _write_text_atomic(text_path, overlay["text"] + "\n")
            generated_text_files.append(str(text_path))
            text_ref = _filter_path(text_path)
            enable = f"between(t\\,{start:.3f}\\,{end:.3f})"
            graph += (
                f",drawtext=fontfile='{font_ref}':"
                f"textfile='{text_ref}':expansion=none:"
                f"fontcolor=white:fontsize={overlay_size}:line_spacing=10:"
                f"x={round(width * 0.10)}:y={round(height * 0.20)}:"
                f"enable='{enable}'"
            )
        graph += "[v]"
        _write_text_atomic(filtergraph, graph + "\n")

        part = str(output) + ".part.mp4"
        normalized_audio = dict(manifest.normalized_audio)
        audio_strategy = str(normalized_audio.get("strategy") or "")
        audio_asset_ref = str(normalized_audio.get("audio_asset_ref") or "")
        canonical_audio_checksum = normalized_audio.get("audio_checksum")
        audio_mix = dict(manifest.audio_mix_schedule)
        if (
            not audio_asset_ref
            or manifest.canonical_audio_asset_ref != audio_asset_ref
            or audio_mix.get("strategy") != audio_strategy
            or audio_mix.get("audio_asset_ref") != audio_asset_ref
            or audio_mix.get("audio_checksum") != canonical_audio_checksum
            or audio_mix.get("narration_present") is not normalized_audio.get(
                "narration_present"
            )
        ):
            raise ValueError("V2_NATIVE_AUDIO_AUTHORITY_MISMATCH")
        if audio_strategy in {
            "LOCAL_OS_TTS_SCRIPT_BOUND",
            "ELEVENLABS_FINAL_NARRATION",
        }:
            if (
                audio_path is None
                or not isinstance(canonical_audio_checksum, str)
                or not re.fullmatch(r"[0-9a-f]{64}", canonical_audio_checksum)
                or normalized_audio.get("narration_present") is not True
            ):
                raise ValueError("V2_NATIVE_NARRATION_AUDIO_REQUIRED")
            audio = _inside(self.root, audio_path, must_exist=True)
            if _sha256_file(audio) != canonical_audio_checksum:
                raise ValueError("V2_NATIVE_NARRATION_AUDIO_CHECKSUM_MISMATCH")
            audio_input_argv = ["-i", str(audio)]
            input_files = [str(audio)]
        elif audio_strategy == "SILENT_STEREO_TEXT_LED":
            if audio_path is not None or canonical_audio_checksum is not None:
                raise ValueError("V2_NATIVE_SILENT_AUDIO_AUTHORITY_INVALID")
            audio_input_argv = [
                "-f",
                "lavfi",
                "-i",
                (
                    "anullsrc=channel_layout=stereo:sample_rate=48000:"
                    f"d={duration_seconds}"
                ),
            ]
            input_files = []
        else:
            raise ValueError("V2_NATIVE_AUDIO_STRATEGY_INVALID")
        argv = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x17324d:s={width}x{height}:r={fps}:d={duration_seconds}",
            *audio_input_argv,
            "-filter_complex_script",
            str(filtergraph),
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-shortest",
            part,
        ]
        version = subprocess.run(
            [self.ffmpeg, "-version"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()[0]
        expected_qc = {
            **manifest.output_specs[0],
            "expected_duration_seconds": duration_seconds,
            "max_av_drift_ms": 250,
            "black_output_check_required": True,
            "scene_coverage_required": len(probe_seconds) > 1,
            "scene_probe_seconds": probe_seconds,
            "subtitle_stream_count": 0,
            "narration_drawtext_count": 0,
            "semantic_overlay_drawtext_count": len(semantic_overlays),
            "faststart": True,
        }
        generated_file_checksums = {
            str(filtergraph): _sha256_file(filtergraph),
            **{path: _sha256_file(Path(path)) for path in generated_text_files},
        }
        if input_files:
            generated_file_checksums[input_files[0]] = str(canonical_audio_checksum)
        core = {
            "run_key": run_key,
            "compiled_manifest_ref": manifest.compiled_manifest_id,
            "compiled_manifest_hash": manifest.manifest_hash,
            "ffmpeg_binary_path": self.ffmpeg,
            "ffprobe_binary_path": self.ffprobe,
            "ffmpeg_version": version,
            "command_builder_version": COMMAND_BUILDER_VERSION,
            "input_files": input_files,
            "generated_filtergraph_path": str(filtergraph),
            "generated_text_files": generated_text_files,
            "generated_file_checksums": generated_file_checksums,
            "output_file": str(output),
            "output_profile": manifest.renderer_profile_refs[0],
            "sanitized_argv": argv,
            "working_directory": str(work),
            "expected_qc": expected_qc,
            "temporal_authority_mode": manifest.temporal_authority_mode,
            "canonical_media_timeline_ref": manifest.canonical_media_timeline_ref,
            "canonical_media_timeline_hash": manifest.canonical_media_timeline_hash,
            "canonical_audio_asset_ref": manifest.canonical_audio_asset_ref,
            "canonical_duration_ms": manifest.canonical_duration_ms,
            "canonical_caption_compilation_ref": None,
            "canonical_caption_compilation_hash": None,
        }
        command = FFmpegCommandManifest(
            **core,
            command_hash=stable_hash(core),
            created_at=datetime.now(UTC),
        )
        return _persist_or_reuse_command(work=work, candidate=command)

    def build_lpro1_fixture(
        self,
        manifest: CompiledNativeRenderManifest,
        *,
        run_key: str,
        audio_path: Path,
    ) -> FFmpegCommandManifest:
        """Build a multi-scene, asset-backed, canonical-timeline fixture render."""

        if (
            manifest.production_eligible
            or manifest.render_purpose != "LPRO1_OFFLINE_FIXTURE"
        ):
            raise ValueError("LPRO1_OFFLINE_FIXTURE_MANIFEST_REQUIRED")
        if manifest.temporal_authority_mode != "CANONICAL_STRICT":
            raise ValueError("LPRO1_CANONICAL_TIMELINE_REQUIRED")
        if len(manifest.compiled_scenes) < 3 or not manifest.canonical_duration_ms:
            raise ValueError("LPRO1_MULTIPLE_CANONICAL_SCENES_REQUIRED")
        width = int(manifest.normalized_canvas["width"])
        height = int(manifest.normalized_canvas["height"])
        fps = int(manifest.normalized_canvas.get("fps", 30))
        if (width, height, fps) != (1920, 1080, 30):
            raise ValueError("LPRO1_OUTPUT_PROFILE_INVALID")
        audio = _inside(self.root, audio_path, must_exist=True)
        if audio.is_symlink() or not audio.is_file():
            raise ValueError("LPRO1_AUDIO_INPUT_INVALID")

        asset_paths: list[Path] = []
        scene_midpoints: list[float] = []
        for scene in manifest.compiled_scenes:
            refs = list(scene.get("asset_refs") or [])
            if len(refs) != 1:
                raise ValueError("LPRO1_ONE_NORMALIZED_ASSET_PER_SCENE_REQUIRED")
            asset = _inside(self.root, Path(str(refs[0]["path"])), must_exist=True)
            if asset.is_symlink() or not asset.is_file():
                raise ValueError("LPRO1_NORMALIZED_ASSET_INVALID")
            asset_paths.append(asset)
            scene_midpoints.append(
                (float(scene["start_ms"]) + float(scene["end_ms"])) / 2000.0
            )

        work = _inside(self.root, Path("runs") / run_key)
        work.mkdir(parents=True, exist_ok=True)
        output = _inside(self.root, work / "lpro1-review-candidate.mp4")
        filtergraph = _inside(self.root, work / "filtergraph.txt")
        font_candidates = (
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        )
        font_path = next((item for item in font_candidates if item.is_file()), None)
        if font_path is None:
            raise FileNotFoundError("LPRO1_NATIVE_FONT_NOT_FOUND")

        graph_parts: list[str] = []
        generated_text_files: list[str] = []
        for index, scene in enumerate(manifest.compiled_scenes):
            route = str(
                (scene.get("visual_routing") or {}).get("preferred_source_route")
                or "NATIVE_DIAGRAM"
            )
            label_path = _inside(
                self.root, work / f"scene-{index + 1}-native-label.txt"
            )
            label = {
                "NATIVE_DIAGRAM": "Native diagram: one approved flow",
                "PEXELS_VIDEO": "Stock-like fixture: team workflow in motion",
                "AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY": "Generated-like fixture + native overlay",
            }.get(route, f"VCOS fixture route: {route}")
            label_path.write_text(label + "\n", encoding="utf-8")
            generated_text_files.append(str(label_path))
            duration = float(scene["duration_ms"]) / 1000.0
            graph_parts.append(
                f"[{index}:v]trim=start=0:duration={duration:.6f},setpts=PTS-STARTPTS,"
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p,"
                "drawbox=x=96:y=72:w=920:h=96:color=0x07111f@0.88:t=fill,"
                f"drawtext=fontfile='{font_path}':textfile='{label_path}':"
                "fontcolor=white:fontsize=38:x=128:y=98[s"
                f"{index}]"
            )
        concat_inputs = "".join(f"[s{index}]" for index in range(len(asset_paths)))
        graph_parts.append(
            f"{concat_inputs}concat=n={len(asset_paths)}:v=1:a=0[scenevideo]"
        )
        graph_parts.append("[scenevideo]null[v]")
        filtergraph.write_text(";\n".join(graph_parts) + "\n", encoding="utf-8")

        duration_seconds = manifest.canonical_duration_ms / 1000.0
        argv = [self.ffmpeg, "-hide_banner", "-nostdin", "-y"]
        for asset in asset_paths:
            argv.extend(["-i", str(asset)])
        argv.extend(
            [
                "-i",
                str(audio),
                "-filter_complex_script",
                str(filtergraph),
                "-map",
                "[v]",
                "-map",
                f"{len(asset_paths)}:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-b:v",
                "6M",
                "-maxrate",
                "8M",
                "-bufsize",
                "12M",
                "-pix_fmt",
                "yuv420p",
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                "-t",
                f"{duration_seconds:.6f}",
                "-shortest",
                str(output) + ".part.mp4",
            ]
        )
        version = subprocess.run(
            [self.ffmpeg, "-version"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[0]
        expected_qc = {
            **manifest.output_specs[0],
            "expected_duration_seconds": duration_seconds,
            "max_av_drift_ms": 250,
            "black_output_check_required": True,
            "subtitle_stream_count": 0,
            "scene_coverage_required": True,
            "scene_probe_seconds": scene_midpoints,
        }
        checksums = {
            str(filtergraph): _sha256_file(filtergraph),
            str(audio): _sha256_file(audio),
            **{str(path): _sha256_file(path) for path in asset_paths},
            **{
                path: _sha256_file(Path(path))
                for path in generated_text_files
            },
        }
        core = {
            "run_key": run_key,
            "compiled_manifest_ref": manifest.compiled_manifest_id,
            "compiled_manifest_hash": manifest.manifest_hash,
            "ffmpeg_binary_path": self.ffmpeg,
            "ffprobe_binary_path": self.ffprobe,
            "ffmpeg_version": version,
            "command_builder_version": COMMAND_BUILDER_VERSION,
            "input_files": [str(path) for path in [*asset_paths, audio]],
            "generated_filtergraph_path": str(filtergraph),
            "generated_text_files": generated_text_files,
            "generated_file_checksums": checksums,
            "output_file": str(output),
            "output_profile": manifest.renderer_profile_refs[0],
            "sanitized_argv": argv,
            "working_directory": str(work),
            "expected_qc": expected_qc,
            "temporal_authority_mode": manifest.temporal_authority_mode,
            "canonical_media_timeline_ref": manifest.canonical_media_timeline_ref,
            "canonical_media_timeline_hash": manifest.canonical_media_timeline_hash,
            "canonical_audio_asset_ref": manifest.canonical_audio_asset_ref,
            "canonical_duration_ms": manifest.canonical_duration_ms,
            "canonical_caption_compilation_ref": manifest.canonical_caption_compilation_ref,
            "canonical_caption_compilation_hash": manifest.canonical_caption_compilation_hash,
        }
        command = FFmpegCommandManifest(
            **core,
            command_hash=stable_hash(core),
            created_at=datetime.now(UTC),
        )
        return _persist_or_reuse_command(work=work, candidate=command)

    def build_image_review(
        self,
        manifest: CompiledNativeRenderManifest,
        *,
        run_key: str,
        image_path: Path,
        headline_artifact: IMGCanaryNativeHeadlineArtifact,
        duration_seconds: float = 6.0,
    ) -> FFmpegCommandManifest:
        """Build a non-production still-image review clip with native text authority."""

        if (
            manifest.production_eligible
            or manifest.render_purpose != "IMG_CANARY_NON_PRODUCTION_REVIEW"
        ):
            raise ValueError("IMG_CANARY_REVIEW_MANIFEST_REQUIRED")
        if manifest.temporal_authority_mode != "LEGACY_HISTORICAL":
            raise ValueError("IMG_CANARY_REVIEW_REQUIRES_ISOLATED_TIMELINE")
        if len(manifest.compiled_scenes) != 1 or len(manifest.overlay_schedule) != 1:
            raise ValueError("IMG_CANARY_REVIEW_REQUIRES_ONE_SCENE_AND_OVERLAY")
        if not 5.0 <= duration_seconds <= 7.0:
            raise ValueError("IMG_CANARY_REVIEW_DURATION_OUT_OF_RANGE")
        if headline_artifact.content_hash != ai_image_stable_hash(
            headline_artifact.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("IMG_CANARY_NATIVE_HEADLINE_HASH_MISMATCH")
        headline = headline_artifact.exact_text
        if headline != "Information is everywhere. Context is nowhere.":
            raise ValueError("IMG_CANARY_NATIVE_HEADLINE_INVALID")

        image = _inside(self.root, image_path, must_exist=True)
        if image.is_symlink() or not image.is_file():
            raise ValueError("IMG_CANARY_NORMALIZED_IMAGE_INVALID")
        expected_inputs = {
            str(Path(value).resolve()) for value in manifest.expected_input_refs
        }
        if str(image) not in expected_inputs:
            raise ValueError("IMG_CANARY_IMAGE_NOT_BOUND_TO_COMPILED_MANIFEST")

        overlay = manifest.overlay_schedule[0]
        if (
            overlay.get("overlay_content_refs") != [headline_artifact.artifact_ref]
            or (overlay.get("exact_text_contract") or {}).get(
                "authoritative_content_refs"
            )
            != [headline_artifact.artifact_ref]
            or overlay.get("scene_id") != headline_artifact.scene_id
            or headline_artifact.run_id not in str(overlay.get("plan_id") or "")
        ):
            raise ValueError("IMG_CANARY_NATIVE_HEADLINE_MANIFEST_BINDING_MISMATCH")
        safe_regions = list(overlay.get("text_safe_regions") or [])
        if len(safe_regions) != 1:
            raise ValueError("IMG_CANARY_ONE_HEADLINE_SAFE_REGION_REQUIRED")
        region = safe_regions[0]
        coordinates = tuple(float(region[key]) for key in ("x", "y", "width", "height"))
        x_norm, y_norm, width_norm, height_norm = coordinates
        if (
            x_norm < 0
            or y_norm < 0
            or width_norm <= 0
            or height_norm <= 0
            or x_norm + width_norm > 1
            or y_norm + height_norm > 1
        ):
            raise ValueError("IMG_CANARY_HEADLINE_SAFE_REGION_OUT_OF_BOUNDS")
        if float(region.get("minimum_contrast_requirement") or 0) < 4.5:
            raise ValueError("IMG_CANARY_HEADLINE_CONTRAST_REQUIREMENT_TOO_LOW")

        width = int(manifest.normalized_canvas["width"])
        height = int(manifest.normalized_canvas["height"])
        fps = int(manifest.normalized_canvas.get("fps", 30))
        if (width, height) != (1920, 1080) or fps != 30:
            raise ValueError("IMG_CANARY_REVIEW_OUTPUT_PROFILE_MISMATCH")

        work = _inside(self.root, Path("runs") / run_key)
        work.mkdir(parents=True, exist_ok=True)
        output = _inside(self.root, work / "img-canary-review.mp4")
        filtergraph = _inside(self.root, work / "filtergraph.txt")
        headline_path = _inside(self.root, work / "native-headline.txt")
        wrapped = "\n".join(
            textwrap.wrap(
                headline, width=30, break_long_words=False, break_on_hyphens=False
            )
        )
        headline_path.write_text(wrapped + "\n", encoding="utf-8")

        panel_x = round(width * x_norm)
        panel_y = round(height * y_norm)
        panel_w = round(width * width_norm)
        panel_h = round(height * height_norm)
        padding_x = max(24, round(panel_w * 0.06))
        padding_y = max(20, round(panel_h * 0.10))
        font_size = max(42, min(64, round(min(width, height) * 0.055)))
        escaped_headline_path = (
            str(headline_path)
            .replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
        )
        fade_out_start = duration_seconds - 0.35
        graph = (
            "[0:v]"
            "scale=2028:1141:force_original_aspect_ratio=increase,"
            f"crop=1920:1080:x='(iw-ow)/2+(iw-ow)*0.10*sin(t/{duration_seconds})':y='(ih-oh)/2',"
            "setsar=1,format=yuv420p,"
            f"drawbox=x={panel_x}:y={panel_y}:w={panel_w}:h={panel_h}:"
            f"color=0x{IMG_CANARY_OVERLAY_PANEL_RGB}@{IMG_CANARY_OVERLAY_PANEL_OPACITY:g}:t=fill,"
            f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:textfile='{escaped_headline_path}':"
            f"fontcolor=white:fontsize={font_size}:line_spacing=12:x={panel_x + padding_x}:y={panel_y + padding_y},"
            f"fade=t=in:st=0:d=0.35,fade=t=out:st={fade_out_start}:d=0.35[v]"
        )
        filtergraph.write_text(graph + "\n", encoding="utf-8")
        part = str(output) + ".part.mp4"
        argv = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(image),
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate=48000:d={duration_seconds}",
            "-filter_complex_script",
            str(filtergraph),
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-b:v",
            "8M",
            "-maxrate",
            "10M",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-t",
            str(duration_seconds),
            "-shortest",
            part,
        ]
        version = subprocess.run(
            [self.ffmpeg, "-version"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[0]
        expected_qc = {
            **manifest.output_specs[0],
            "expected_duration_seconds": duration_seconds,
            "max_av_drift_ms": 250,
        }
        generated_file_checksums = {
            str(image): _sha256_file(image),
            str(filtergraph): _sha256_file(filtergraph),
            str(headline_path): _sha256_file(headline_path),
        }
        core = {
            "run_key": run_key,
            "compiled_manifest_ref": manifest.compiled_manifest_id,
            "compiled_manifest_hash": manifest.manifest_hash,
            "ffmpeg_binary_path": self.ffmpeg,
            "ffprobe_binary_path": self.ffprobe,
            "ffmpeg_version": version,
            "command_builder_version": COMMAND_BUILDER_VERSION,
            "input_files": [str(image)],
            "generated_filtergraph_path": str(filtergraph),
            "generated_text_files": [str(headline_path)],
            "generated_file_checksums": generated_file_checksums,
            "output_file": str(output),
            "output_profile": manifest.renderer_profile_refs[0],
            "sanitized_argv": argv,
            "working_directory": str(work),
            "expected_qc": expected_qc,
            "temporal_authority_mode": manifest.temporal_authority_mode,
            "canonical_media_timeline_ref": None,
            "canonical_media_timeline_hash": None,
            "canonical_audio_asset_ref": None,
            "canonical_duration_ms": None,
            "canonical_caption_compilation_ref": None,
            "canonical_caption_compilation_hash": None,
        }
        command = FFmpegCommandManifest(
            **core,
            command_hash=stable_hash(core),
            created_at=datetime.now(UTC),
        )
        return _persist_or_reuse_command(work=work, candidate=command)


class NativeFFmpegRenderer:
    def __init__(
        self,
        workspace_root: Path,
        *,
        smoke_enabled: bool | None = None,
        production_enabled: bool | None = None,
    ):
        self.root = workspace_root.resolve()
        self.smoke_enabled = (
            _flag("VCOS_NATIVE_FFMPEG_LOCAL_SMOKE_ENABLED")
            if smoke_enabled is None
            else smoke_enabled
        )
        self.production_enabled = (
            _flag("VCOS_NATIVE_FFMPEG_PRODUCTION_ENABLED")
            if production_enabled is None
            else production_enabled
        )

    def authorize(
        self,
        manifest: CompiledNativeRenderManifest,
        *,
        purpose: str,
        execution_envelope: (
            ProductionRenderExecutionEnvelope
            | V2ProductionRenderExecutionEnvelope
            | None
        ) = None,
    ) -> dict[str, object]:
        """Validate execution eligibility without starting FFmpeg."""

        if purpose != manifest.render_purpose:
            raise PermissionError("RENDER_PURPOSE_MISMATCH")
        if manifest.production_eligible:
            if execution_envelope is None:
                raise PermissionError("PRODUCTION_RENDER_EXECUTION_ENVELOPE_REQUIRED")
            payload = execution_envelope.model_dump(
                mode="json", exclude={"authorization_hash"}
            )
            if stable_hash(payload) != execution_envelope.authorization_hash:
                raise PermissionError("PRODUCTION_RENDER_AUTHORIZATION_HASH_MISMATCH")
            if isinstance(execution_envelope, V2ProductionRenderExecutionEnvelope):
                package_ref = (
                    "artifact-version://"
                    f"{execution_envelope.production_package_artifact_version_id}"
                )
                if (
                    execution_envelope.plan_ref != manifest.source_plan_ref
                    or execution_envelope.plan_hash != manifest.source_plan_hash
                    or execution_envelope.adapter_key != "v2-local-native"
                    or execution_envelope.paid_provider_call
                    or purpose != "VCOS_V2_NATIVE_PRODUCTION"
                    or package_ref not in manifest.expected_input_refs
                ):
                    raise PermissionError(
                        "V2_PRODUCTION_RENDER_EXECUTION_ENVELOPE_INVALID"
                    )
                return {
                    "eligible": True,
                    "production_eligible": True,
                    "authorization_hash": (execution_envelope.authorization_hash),
                    "authorization_mode": "V2_PACKAGE_AND_BUDGET",
                }
            if (
                not execution_envelope.production_eligible
                or execution_envelope.execution_mode.value != "REAL_APPROVED_PRODUCTION"
                or execution_envelope.plan_ref != manifest.source_plan_ref
                or execution_envelope.plan_hash != manifest.source_plan_hash
                or not str(execution_envelope.operator_approval_ref).startswith(
                    "operator-approval://"
                )
                or not str(execution_envelope.mr1_scoped_approval_ref).startswith(
                    "mr1-approval://"
                )
                or purpose != "REAL_APPROVED_PRODUCTION"
            ):
                raise PermissionError("PRODUCTION_RENDER_EXECUTION_ENVELOPE_INVALID")
            return {
                "eligible": True,
                "production_eligible": True,
                "authorization_hash": execution_envelope.authorization_hash,
            }
        allowed_fixture_purposes = {
            "NR1_LOCAL_SYNTHETIC_SMOKE",
            "PA1R_NON_PRODUCTION_SMOKE",
            "CQR1_LOCAL_GOLDEN_FIXTURE",
            "CQR1_CONTROLLED_PAID_CANARY",
            "IMG_CANARY_NON_PRODUCTION_REVIEW",
            "LPRO1_OFFLINE_FIXTURE",
        }
        if purpose not in allowed_fixture_purposes or not self.smoke_enabled:
            raise PermissionError("LOCAL_SMOKE_BOUNDARY_REJECTED")
        if execution_envelope is not None and execution_envelope.production_eligible:
            raise PermissionError("OFFLINE_RENDER_REJECTS_PRODUCTION_ENVELOPE")
        return {"eligible": True, "production_eligible": False}

    def execute(
        self,
        manifest: CompiledNativeRenderManifest,
        command: FFmpegCommandManifest,
        *,
        purpose: str,
        execution_envelope: (
            ProductionRenderExecutionEnvelope
            | V2ProductionRenderExecutionEnvelope
            | None
        ) = None,
    ) -> tuple[NativeRenderExecutionReceipt, object]:
        self.authorize(
            manifest,
            purpose=purpose,
            execution_envelope=execution_envelope,
        )
        if manifest.production_eligible and not self.production_enabled:
            raise PermissionError("PRODUCTION_RENDER_DISABLED")
        if (
            isinstance(execution_envelope, V2ProductionRenderExecutionEnvelope)
            and command.run_key != execution_envelope.render_run_key
        ):
            raise PermissionError("V2_PRODUCTION_RENDER_COMMAND_ID_MISMATCH")
        if command.compiled_manifest_hash != manifest.manifest_hash:
            raise ValueError("MANIFEST_HASH_MISMATCH")
        if not _manifest_hash_matches(manifest):
            raise ValueError("MANIFEST_CONTENT_HASH_MISMATCH")
        if stable_hash(_command_hash_payload(command)) != command.command_hash:
            raise ValueError("COMMAND_MANIFEST_HASH_MISMATCH")
        if not command.generated_file_checksums:
            raise ValueError("GENERATED_FILE_CHECKSUMS_REQUIRED")
        for raw_path, expected_checksum in command.generated_file_checksums.items():
            generated = _inside(self.root, Path(raw_path), must_exist=True)
            if _sha256_file(generated) != expected_checksum:
                raise ValueError("GENERATED_FILE_CHECKSUM_MISMATCH")
        if command.generated_filtergraph_path not in command.generated_file_checksums:
            raise ValueError("FILTERGRAPH_CHECKSUM_REQUIRED")
        if manifest.temporal_authority_mode == "CANONICAL_STRICT":
            if not (
                manifest.canonical_media_timeline_ref
                and manifest.canonical_media_timeline_hash
                and manifest.canonical_audio_asset_ref
                and manifest.canonical_duration_ms
            ):
                raise ValueError("TEMPORAL_CANONICAL_TIMELINE_REQUIRED")
            if (
                command.canonical_media_timeline_ref
                != manifest.canonical_media_timeline_ref
                or command.canonical_media_timeline_hash
                != manifest.canonical_media_timeline_hash
                or command.canonical_audio_asset_ref
                != manifest.canonical_audio_asset_ref
                or command.canonical_duration_ms != manifest.canonical_duration_ms
            ):
                raise ValueError("TEMPORAL_RENDER_COMMAND_AUTHORITY_MISMATCH")
            if (
                command.canonical_caption_compilation_ref
                != manifest.canonical_caption_compilation_ref
                or command.canonical_caption_compilation_hash
                != manifest.canonical_caption_compilation_hash
            ):
                raise ValueError("CAPTION_RENDER_COMMAND_AUTHORITY_MISMATCH")
        output = _inside(self.root, Path(command.output_file))
        work = _inside(self.root, Path(command.working_directory))
        v2_envelope = (
            execution_envelope
            if isinstance(execution_envelope, V2ProductionRenderExecutionEnvelope)
            else None
        )
        v2_journal = (
            _load_v2_render_execution_journal(
                work=work,
                output=output,
                manifest=manifest,
                command=command,
                envelope=v2_envelope,
            )
            if v2_envelope is not None
            else None
        )
        completed_render = _load_completed_render(
            output=output,
            work=work,
            manifest=manifest,
            command=command,
        )
        if completed_render is not None:
            if v2_envelope is not None:
                if v2_journal is None:
                    raise FileExistsError("V2_RENDER_EXECUTION_JOURNAL_REQUIRED")
                _seal_v2_render_execution_journal(
                    work=work,
                    journal=v2_journal,
                    receipt=completed_render[0],
                    recovered_after_effect=bool(
                        v2_journal.get("recovered_after_effect")
                    ),
                )
            return completed_render
        if v2_envelope is not None and output.exists():
            if v2_journal is None:
                raise FileExistsError("V2_RENDER_UNJOURNALED_OUTPUT")
            if v2_journal.get("state") == "VERIFIED":
                raise FileExistsError("V2_RENDER_VERIFIED_RECEIPT_MISSING")
            return _recover_v2_completed_render(
                output=output,
                work=work,
                manifest=manifest,
                command=command,
                journal=v2_journal,
            )
        if v2_journal is not None:
            raise RuntimeError("V2_RENDER_EFFECT_UNCERTAIN_OUTPUT_MISSING")
        if shutil.disk_usage(self.root).free < 2 * 1024**3:
            raise RuntimeError("WORKSPACE_FREE_SPACE_ABORT")
        if not _RENDER_LOCK.acquire(blocking=False):
            raise RuntimeError("RENDER_ALREADY_RUNNING")
        started = datetime.now(UTC)
        tick = time.monotonic()
        try:
            if v2_envelope is not None:
                v2_journal = _start_v2_render_execution_journal(
                    work=work,
                    output=output,
                    manifest=manifest,
                    command=command,
                    envelope=v2_envelope,
                    started_at=started,
                )
            proc = subprocess.run(
                command.sanitized_argv,
                cwd=command.working_directory,
                capture_output=True,
                text=True,
                shell=False,
            )
            (Path(command.working_directory) / "ffmpeg.stderr.log").write_text(
                proc.stderr[-200000:], encoding="utf-8"
            )
            if proc.returncode != 0:
                raise RuntimeError(f"FFMPEG_FAILED:{proc.returncode}")
            part = Path(str(output) + ".part.mp4")
            if not part.is_file():
                raise RuntimeError("PARTIAL_OUTPUT_MISSING")
            with part.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(part, output)
            _fsync_directory(output.parent)
            qc = NativeMediaQC(command.ffprobe_binary_path).inspect(
                output, command.expected_qc, command.run_key
            )
            if qc.result == "FAIL":
                raise RuntimeError("MEDIA_QC_FAILED:" + ",".join(qc.reason_codes))
            ended = datetime.now(UTC)
            checksum = hashlib.sha256(output.read_bytes()).hexdigest()
            body = {
                "run_key": command.run_key,
                "manifest_refs": {
                    "compiled_manifest": manifest.compiled_manifest_id,
                    "compiled_manifest_hash": manifest.manifest_hash,
                },
                "command_hash": command.command_hash,
                "start_time": started,
                "end_time": ended,
                "exit_code": 0,
                "elapsed_time": time.monotonic() - tick,
                "realtime_factor": None,
                "peak_rss": None,
                "output_path": str(output),
                "output_checksum": checksum,
                "local_only": not manifest.production_eligible,
                "production_eligible": manifest.production_eligible,
                "no_provider_calls_confirmed": True,
            }
            receipt = NativeRenderExecutionReceipt(
                receipt_hash=stable_hash(body), **body
            )
            _write_text_atomic(
                work / "media_qc.json",
                qc.model_dump_json(indent=2) + "\n",
            )
            _write_text_atomic(
                work / "execution_receipt.json",
                receipt.model_dump_json(indent=2) + "\n",
            )
            _write_text_atomic(
                work / "cleanup_receipt.json",
                json.dumps(
                    {
                        "partial_files_remaining": 0,
                        "completed_at": ended.isoformat(),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
            if v2_journal is not None:
                _seal_v2_render_execution_journal(
                    work=work,
                    journal=v2_journal,
                    receipt=receipt,
                    recovered_after_effect=False,
                )
            return receipt, qc
        finally:
            _RENDER_LOCK.release()


def _flag(key: str) -> bool:
    return os.getenv(key, "false").strip().lower() in {"1", "true", "yes", "on"}
