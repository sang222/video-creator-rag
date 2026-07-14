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


class FFmpegCommandBuilder:
    def __init__(self, workspace_root: Path, *, ffmpeg: str = FFMPEG_FULL_DEFAULT, ffprobe: str = FFPROBE_FULL_DEFAULT):
        self.root = workspace_root.resolve()
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe

    def build_synthetic(self, manifest: CompiledNativeRenderManifest, *, run_key: str, duration_seconds: float = 12.0) -> FFmpegCommandManifest:
        if manifest.production_eligible:
            raise ValueError("SYNTHETIC_BUILDER_REJECTS_PRODUCTION")
        work = _inside(self.root, Path("runs") / run_key)
        work.mkdir(parents=True, exist_ok=True)
        output = _inside(self.root, work / "nr1_smoke.mp4")
        filtergraph = _inside(self.root, work / "filtergraph.txt")
        # Registry-owned graph only; never accepts raw payload syntax.
        srt = str(manifest.normalized_caption.get("srt_ref") or manifest.caption_schedule.get("srt_ref") or "").replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        graph = "[0:v]fade=t=in:st=0:d=0.5,drawbox=x=120:y=110:w=1680:h=860:color=0x172033@1:t=fill,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='VCOS Native Renderer':fontcolor=white:fontsize=72:x=(w-text_w)/2:y=220,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='NR1 - slide / data card / Ken Burns / lower third':fontcolor=0x6ee7ff:fontsize=38:x=(w-text_w)/2:y=380,drawbox=x='180+min(t*80,240)':y=760:w=900:h=140:color=0x2563eb@0.9:t=fill,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='LOCAL SYNTHETIC SMOKE':fontcolor=white:fontsize=38:x='220+min(t*80,240)':y=810,drawbox=x=1710:y=60:w=140:h=60:color=white@0.9:t=fill,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='VCOS':fontcolor=black:fontsize=24:x=1745:y=78"
        if srt:
            graph += f",subtitles=filename='{srt}':force_style='FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Alignment=2,MarginV=55'"
        graph += "[v]"
        filtergraph.write_text(graph + "\n", encoding="utf-8")
        part = str(output) + ".part.mp4"
        argv = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i", f"color=c=0x0b1020:s=1920x1080:r=30:d={duration_seconds}", "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration_seconds}", "-filter_complex_script", str(filtergraph), "-map", "[v]", "-map", "1:a", "-c:v", "h264_videotoolbox", "-b:v", "8M", "-maxrate", "10M", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-t", str(duration_seconds), part]
        version = subprocess.run([self.ffmpeg, "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
        core = {"run_key": run_key, "compiled_manifest_ref": manifest.compiled_manifest_id, "compiled_manifest_hash": manifest.manifest_hash, "ffmpeg_binary_path": self.ffmpeg, "ffprobe_binary_path": self.ffprobe, "ffmpeg_version": version, "command_builder_version": COMMAND_BUILDER_VERSION, "input_files": [], "generated_filtergraph_path": str(filtergraph), "generated_text_files": [], "generated_caption_path": manifest.normalized_caption.get("srt_ref"), "output_file": str(output), "output_profile": manifest.renderer_profile_refs[0], "sanitized_argv": argv, "working_directory": str(work), "expected_qc": manifest.output_specs[0], "temporal_authority_mode": manifest.temporal_authority_mode, "canonical_media_timeline_ref": manifest.canonical_media_timeline_ref, "canonical_media_timeline_hash": manifest.canonical_media_timeline_hash, "canonical_audio_asset_ref": manifest.canonical_audio_asset_ref}
        command_hash = stable_hash(core | {"filtergraph_hash": hashlib.sha256(graph.encode()).hexdigest()})
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
        if purpose not in {"NR1_LOCAL_SYNTHETIC_SMOKE", "PA1R_NON_PRODUCTION_SMOKE"} or manifest.production_eligible or not self.smoke_enabled:
            raise PermissionError("LOCAL_SMOKE_BOUNDARY_REJECTED")
        if command.compiled_manifest_hash != manifest.manifest_hash:
            raise ValueError("MANIFEST_HASH_MISMATCH")
        if manifest.temporal_authority_mode == "CANONICAL_STRICT":
            if not (
                manifest.canonical_media_timeline_ref
                and manifest.canonical_media_timeline_hash
                and manifest.canonical_audio_asset_ref
            ):
                raise ValueError("TEMPORAL_CANONICAL_TIMELINE_REQUIRED")
            if (
                command.canonical_media_timeline_ref != manifest.canonical_media_timeline_ref
                or command.canonical_media_timeline_hash != manifest.canonical_media_timeline_hash
                or command.canonical_audio_asset_ref != manifest.canonical_audio_asset_ref
            ):
                raise ValueError("TEMPORAL_RENDER_COMMAND_AUTHORITY_MISMATCH")
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
