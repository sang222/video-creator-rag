from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.contracts.native_renderer import CompiledNativeRenderManifest, FFmpegCommandManifest, NativeRenderExecutionReceipt
from app.services.caption_ass import write_caption_ass
from app.services.native_media_qc import NativeMediaQC
from app.services.native_render_plan import stable_hash


FFMPEG_FULL_DEFAULT = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
FFPROBE_FULL_DEFAULT = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
COMMAND_BUILDER_VERSION = "native-ffmpeg-command-builder/1.0.0"
_RENDER_LOCK = threading.Lock()


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


def _write_canonical_ass(
    path: Path,
    *,
    cues: list[dict],
    width: int,
    height: int,
    caption_policy: dict,
) -> None:
    write_caption_ass(
        path,
        cues=cues,
        frame_width=width,
        frame_height=height,
        render_style=caption_policy,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


class FFmpegCommandBuilder:
    def __init__(self, workspace_root: Path, *, ffmpeg: str = FFMPEG_FULL_DEFAULT, ffprobe: str = FFPROBE_FULL_DEFAULT):
        self.root = workspace_root.resolve()
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe

    def build_synthetic(self, manifest: CompiledNativeRenderManifest, *, run_key: str, duration_seconds: float = 12.0) -> FFmpegCommandManifest:
        if manifest.production_eligible:
            raise ValueError("SYNTHETIC_BUILDER_REJECTS_PRODUCTION")
        if manifest.render_purpose == "CQR1_CONTROLLED_PAID_CANARY":
            raise ValueError("SYNTHETIC_BUILDER_REJECTS_PAID_CANARY")
        work = _inside(self.root, Path("runs") / run_key)
        work.mkdir(parents=True, exist_ok=True)
        canonical_cues = list(manifest.caption_schedule.get("cues") or [])
        canonical_strict = manifest.temporal_authority_mode == "CANONICAL_STRICT"
        if canonical_strict:
            if not manifest.compiled_scenes or not manifest.canonical_duration_ms:
                raise ValueError("TEMPORAL_CANONICAL_DURATION_REQUIRED")
            if max(int(item["end_ms"]) for item in manifest.compiled_scenes) != manifest.canonical_duration_ms:
                raise ValueError("TEMPORAL_AUDIO_ENDPOINT_MISMATCH")
            duration_seconds = manifest.canonical_duration_ms / 1000.0
        width = int(manifest.normalized_canvas["width"])
        height = int(manifest.normalized_canvas["height"])
        fps = int(manifest.normalized_canvas.get("fps", 30))
        output = _inside(self.root, work / "nr1_smoke.mp4")
        filtergraph = _inside(self.root, work / "filtergraph.txt")
        # Registry-owned graph only; never accepts raw payload syntax.
        generated_caption_path: str | None = None
        generated_text_files: list[str] = []
        if canonical_cues:
            caption_path = _inside(self.root, work / "canonical-captions.ass")
            _write_canonical_ass(
                caption_path,
                cues=canonical_cues,
                width=width,
                height=height,
                caption_policy=dict(manifest.caption_schedule.get("render_style") or manifest.normalized_caption),
            )
            generated_caption_path = str(caption_path)
            generated_text_files.append(str(caption_path))
        else:
            legacy_caption = str(manifest.normalized_caption.get("srt_ref") or manifest.caption_schedule.get("srt_ref") or "")
            generated_caption_path = legacy_caption or None
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
            f"[0:v]fade=t=in:st=0:d=0.5,drawbox=x={panel_x}:y={panel_y}:w={panel_w}:h={panel_h}:color=0x172033@1:t=fill,"
            f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='VCOS Native Renderer':fontcolor=white:fontsize={title_size}:x=(w-text_w)/2:y={round(height * 0.20)},"
            f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='Canonical timeline / captions / duration':fontcolor=0x6ee7ff:fontsize={body_size}:x=(w-text_w)/2:y={round(height * 0.35)},"
            f"drawbox=x={round(width * 0.094)}:y={round(height * 0.56)}:w={round(width * 0.60)}:h={round(height * 0.12)}:color=0x2563eb@0.9:t=fill,"
            f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='{label}':fontcolor=white:fontsize={body_size}:x={round(width * 0.115)}:y={round(height * 0.60)},"
            f"drawbox=x={round(width * 0.89)}:y={round(height * 0.055)}:w={round(width * 0.073)}:h={round(height * 0.056)}:color=white@0.9:t=fill,"
            f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='VCOS':fontcolor=black:fontsize={badge_size}:x={round(width * 0.91)}:y={round(height * 0.072)}"
        )
        if generated_caption_path:
            caption_filter_path = generated_caption_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            if canonical_cues:
                graph += f",ass=filename='{caption_filter_path}'"
            else:
                graph += f",subtitles=filename='{caption_filter_path}':force_style='FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Alignment=2,MarginV=55'"
        graph += "[v]"
        filtergraph.write_text(graph + "\n", encoding="utf-8")
        part = str(output) + ".part.mp4"
        argv = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i", f"color=c=0x0b1020:s={width}x{height}:r={fps}:d={duration_seconds}", "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration_seconds}", "-filter_complex_script", str(filtergraph), "-map", "[v]", "-map", "1:a", "-c:v", "h264_videotoolbox", "-b:v", "8M", "-maxrate", "10M", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart"]
        if canonical_strict:
            argv.extend(["-shortest", part])
        else:
            argv.extend(["-t", str(duration_seconds), part])
        version = subprocess.run([self.ffmpeg, "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
        expected_qc = {**manifest.output_specs[0], "expected_duration_seconds": duration_seconds, "max_av_drift_ms": 250}
        generated_file_checksums = {str(filtergraph): _sha256_file(filtergraph)}
        if generated_caption_path and canonical_cues:
            generated_file_checksums[generated_caption_path] = _sha256_file(Path(generated_caption_path))
        core = {"run_key": run_key, "compiled_manifest_ref": manifest.compiled_manifest_id, "compiled_manifest_hash": manifest.manifest_hash, "ffmpeg_binary_path": self.ffmpeg, "ffprobe_binary_path": self.ffprobe, "ffmpeg_version": version, "command_builder_version": COMMAND_BUILDER_VERSION, "input_files": [], "generated_filtergraph_path": str(filtergraph), "generated_text_files": generated_text_files, "generated_caption_path": generated_caption_path, "generated_file_checksums": generated_file_checksums, "output_file": str(output), "output_profile": manifest.renderer_profile_refs[0], "sanitized_argv": argv, "working_directory": str(work), "expected_qc": expected_qc, "temporal_authority_mode": manifest.temporal_authority_mode, "canonical_media_timeline_ref": manifest.canonical_media_timeline_ref, "canonical_media_timeline_hash": manifest.canonical_media_timeline_hash, "canonical_audio_asset_ref": manifest.canonical_audio_asset_ref, "canonical_duration_ms": manifest.canonical_duration_ms, "canonical_caption_compilation_ref": manifest.canonical_caption_compilation_ref, "canonical_caption_compilation_hash": manifest.canonical_caption_compilation_hash, "canonical_caption_render_payload_hash": manifest.canonical_caption_render_payload_hash}
        command_hash = stable_hash(core)
        command = FFmpegCommandManifest(command_hash=command_hash, created_at=datetime.now(UTC), **core)
        (work / "command_manifest.json").write_text(command.model_dump_json(indent=2), encoding="utf-8")
        (work / "command.sh").write_text("#!/bin/sh\n" + shlex.join(argv) + "\n", encoding="utf-8")
        return command


class NativeFFmpegRenderer:
    def __init__(self, workspace_root: Path, *, smoke_enabled: bool | None = None, production_enabled: bool | None = None):
        self.root = workspace_root.resolve()
        self.smoke_enabled = _flag("VCOS_NATIVE_FFMPEG_LOCAL_SMOKE_ENABLED") if smoke_enabled is None else smoke_enabled
        self.production_enabled = _flag("VCOS_NATIVE_FFMPEG_PRODUCTION_ENABLED") if production_enabled is None else production_enabled

    def execute(self, manifest: CompiledNativeRenderManifest, command: FFmpegCommandManifest, *, purpose: str) -> tuple[NativeRenderExecutionReceipt, object]:
        if manifest.production_eligible and not self.production_enabled:
            raise PermissionError("PRODUCTION_RENDER_DISABLED")
        if purpose not in {"NR1_LOCAL_SYNTHETIC_SMOKE", "PA1R_NON_PRODUCTION_SMOKE", "CQR1_LOCAL_GOLDEN_FIXTURE", "CQR1_CONTROLLED_PAID_CANARY"} or manifest.production_eligible or not self.smoke_enabled:
            raise PermissionError("LOCAL_SMOKE_BOUNDARY_REJECTED")
        if purpose != manifest.render_purpose:
            raise PermissionError("RENDER_PURPOSE_MISMATCH")
        if command.compiled_manifest_hash != manifest.manifest_hash:
            raise ValueError("MANIFEST_HASH_MISMATCH")
        if stable_hash(_manifest_hash_payload(manifest)) != manifest.manifest_hash:
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
        if command.generated_caption_path and manifest.temporal_authority_mode == "CANONICAL_STRICT":
            if command.generated_caption_path not in command.generated_file_checksums:
                raise ValueError("CAPTION_ASS_CHECKSUM_REQUIRED")
        if manifest.temporal_authority_mode == "CANONICAL_STRICT":
            if not (
                manifest.canonical_media_timeline_ref
                and manifest.canonical_media_timeline_hash
                and manifest.canonical_audio_asset_ref
                and manifest.canonical_duration_ms
            ):
                raise ValueError("TEMPORAL_CANONICAL_TIMELINE_REQUIRED")
            if (
                command.canonical_media_timeline_ref != manifest.canonical_media_timeline_ref
                or command.canonical_media_timeline_hash != manifest.canonical_media_timeline_hash
                or command.canonical_audio_asset_ref != manifest.canonical_audio_asset_ref
                or command.canonical_duration_ms != manifest.canonical_duration_ms
            ):
                raise ValueError("TEMPORAL_RENDER_COMMAND_AUTHORITY_MISMATCH")
            if (
                command.canonical_caption_compilation_ref != manifest.canonical_caption_compilation_ref
                or command.canonical_caption_compilation_hash != manifest.canonical_caption_compilation_hash
                or command.canonical_caption_render_payload_hash
                != manifest.canonical_caption_render_payload_hash
            ):
                raise ValueError("CAPTION_RENDER_COMMAND_AUTHORITY_MISMATCH")
        output = _inside(self.root, Path(command.output_file))
        _inside(self.root, Path(command.working_directory))
        if shutil.disk_usage(self.root).free < 2 * 1024**3:
            raise RuntimeError("WORKSPACE_FREE_SPACE_ABORT")
        if not _RENDER_LOCK.acquire(blocking=False):
            raise RuntimeError("RENDER_ALREADY_RUNNING")
        started = datetime.now(UTC); tick = time.monotonic()
        try:
            proc = subprocess.run(command.sanitized_argv, cwd=command.working_directory, capture_output=True, text=True, shell=False)
            (Path(command.working_directory) / "ffmpeg.stderr.log").write_text(proc.stderr[-200000:], encoding="utf-8")
            if proc.returncode != 0:
                raise RuntimeError(f"FFMPEG_FAILED:{proc.returncode}")
            part = Path(str(output) + ".part.mp4")
            if not part.is_file():
                raise RuntimeError("PARTIAL_OUTPUT_MISSING")
            os.replace(part, output)
            qc = NativeMediaQC(command.ffprobe_binary_path).inspect(output, command.expected_qc, command.run_key)
            if qc.result == "FAIL":
                raise RuntimeError("MEDIA_QC_FAILED:" + ",".join(qc.reason_codes))
            ended = datetime.now(UTC); checksum = hashlib.sha256(output.read_bytes()).hexdigest()
            body = {"run_key": command.run_key, "manifest_refs": {"compiled_manifest": manifest.compiled_manifest_id, "compiled_manifest_hash": manifest.manifest_hash}, "command_hash": command.command_hash, "start_time": started, "end_time": ended, "exit_code": 0, "elapsed_time": time.monotonic() - tick, "realtime_factor": None, "peak_rss": None, "output_path": str(output), "output_checksum": checksum, "local_only": True, "production_eligible": False, "no_provider_calls_confirmed": True}
            receipt = NativeRenderExecutionReceipt(receipt_hash=stable_hash(body), **body)
            work = Path(command.working_directory)
            (work / "media_qc.json").write_text(qc.model_dump_json(indent=2), encoding="utf-8")
            (work / "execution_receipt.json").write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
            (work / "cleanup_receipt.json").write_text(json.dumps({"partial_files_remaining": 0, "completed_at": ended.isoformat()}, indent=2), encoding="utf-8")
            return receipt, qc
        finally:
            _RENDER_LOCK.release()


def _flag(key: str) -> bool:
    return os.getenv(key, "false").strip().lower() in {"1", "true", "yes", "on"}
