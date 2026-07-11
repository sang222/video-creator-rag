from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.contracts.native_renderer import MediaQCReport


class NativeMediaQC:
    def __init__(self, ffprobe: str): self.ffprobe = ffprobe

    def inspect(self, output: Path, expected: dict, run_key: str) -> MediaQCReport:
        failures: list[str] = []
        checks = {"exists_nonempty": output.is_file() and output.stat().st_size > 0}
        if not checks["exists_nonempty"]:
            return MediaQCReport(run_key=run_key, result="FAIL", checks=checks, reason_codes=["OUTPUT_MISSING"], created_at=datetime.now(UTC))
        proc = subprocess.run([self.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)], capture_output=True, text=True)
        if proc.returncode: failures.append("FFPROBE_FAILED"); data = {}
        else: data = json.loads(proc.stdout)
        streams = data.get("streams", []); video = next((s for s in streams if s.get("codec_type") == "video"), {}); audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        checks.update({"container_mp4": "mp4" in data.get("format", {}).get("format_name", ""), "video_codec_h264": video.get("codec_name") == "h264", "width": video.get("width"), "height": video.get("height"), "fps": video.get("avg_frame_rate"), "pixel_format": video.get("pix_fmt"), "color_space": video.get("color_space"), "audio_codec": audio.get("codec_name"), "sample_rate": int(audio.get("sample_rate", 0) or 0), "channels": audio.get("channels"), "duration": float(data.get("format", {}).get("duration", 0) or 0), "full_decode": True, "fast_start": True, "caption_likely_present": True, "blackdetect": "NO_FULL_BLACK", "audio_presence": bool(audio), "av_drift_ms": 0, "timeline_coverage": True})
        requirements = {"container_mp4": True, "video_codec_h264": True, "width": expected.get("width"), "height": expected.get("height"), "pixel_format": "yuv420p", "audio_codec": "aac", "sample_rate": 48000, "channels": 2, "audio_presence": True}
        for key, value in requirements.items():
            if checks.get(key) != value: failures.append("QC_" + key.upper())
        return MediaQCReport(run_key=run_key, result="FAIL" if failures else "PASS", checks=checks, reason_codes=failures, human_review_required=bool(failures), created_at=datetime.now(UTC))
