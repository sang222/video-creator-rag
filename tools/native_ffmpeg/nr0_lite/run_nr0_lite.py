#!/usr/bin/env python3
"""NR0-LITE local-only deterministic fixture, smoke, telemetry and QC runner."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import re
from pathlib import Path
from typing import Any

from media_qc import qc

ROOT = Path(os.environ.get("NR0_LITE_ROOT", Path.cwd() / "var/tmp/native_ffmpeg_nr0_lite")).resolve()
FFMPEG = os.environ.get("NR0_FFMPEG_BIN", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFPROBE = os.environ.get("NR0_FFPROBE_BIN", "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
MIN_FREE_GB = int(os.environ.get("NR0_LITE_MIN_FREE_GB", "40"))
ABORT_FREE_GB = int(os.environ.get("NR0_LITE_ABORT_FREE_GB", "20"))
EXPECTED_SRT_SHA = "0bdcd564a3d47c342b52bc0d057a510656e380087fbcd69d0ab4015ea310f6a2"
SOURCE_SRT = Path("var/tmp/pa1-precheck-srt/7c4a6b4e-60d2-4e99-8d65-304afef33c2b/narration.en.srt").resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def free_bytes() -> int:
    return shutil.disk_usage(ROOT).free


def gb(value: int) -> float:
    return round(value / 1024**3, 3)


def shell(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True)


def ensure_layout() -> None:
    for name in ("preflight", "fixtures", "runs", "outputs", "contact_sheets", "temp"):
        (ROOT / name).mkdir(parents=True, exist_ok=True)
    if free_bytes() < MIN_FREE_GB * 1024**3:
        raise RuntimeError(f"BLOCKED_LOW_DISK_HEADROOM free_gib={gb(free_bytes())} min_gib={MIN_FREE_GB}")


def save_preflight() -> dict[str, Any]:
    preflight = ROOT / "preflight"
    records = {}
    for name, command in {
        "ffmpeg_full_version.txt": [FFMPEG, "-hide_banner", "-version"],
        "ffprobe_full_version.txt": [FFPROBE, "-hide_banner", "-version"],
        "ffmpeg_full_buildconf.txt": [FFMPEG, "-hide_banner", "-buildconf"],
        "ffmpeg_full_encoders.txt": [FFMPEG, "-hide_banner", "-encoders"],
        "ffmpeg_full_filters.txt": [FFMPEG, "-hide_banner", "-filters"],
        "ffmpeg_full_hwaccels.txt": [FFMPEG, "-hide_banner", "-hwaccels"],
    }.items():
        result = shell(command)
        (preflight / name).write_text(result.stdout + result.stderr)
        records[name] = str(preflight / name)
    filters = (preflight / "ffmpeg_full_filters.txt").read_text()
    encoders = (preflight / "ffmpeg_full_encoders.txt").read_text()
    required_filters = ["xfade", "overlay", "fade", "zoompan", "scale", "crop", "fps", "format", "drawtext", "subtitles", "drawbox", "amix", "afade", "volume"]
    filter_names = {fields[1] for line in filters.splitlines() if len(fields := line.split()) >= 2}
    missing = [item for item in required_filters if item not in filter_names]
    capabilities = {
        "h264_videotoolbox": "h264_videotoolbox" in encoders,
        "aac": any(" aac " in line for line in encoders.splitlines()),
        "hevc_videotoolbox": "hevc_videotoolbox" in encoders,
        "prores_videotoolbox": "prores_videotoolbox" in encoders,
        "ass_filter": "ass" in filter_names,
        "missing_required_filters": missing,
    }
    (preflight / "capabilities.json").write_text(json.dumps(capabilities, indent=2) + "\n")
    if not capabilities["h264_videotoolbox"] or not capabilities["aac"] or missing:
        raise RuntimeError(f"FFMPEG_FULL_INSTALL_OR_CAPABILITY_FAILED {capabilities}")
    fonts = [Path("/System/Library/Fonts/Supplemental/Arial.ttf"), Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"), Path("/System/Library/Fonts/SFNS.ttf")]
    font = next((candidate for candidate in fonts if candidate.is_file()), None)
    if not font:
        raise RuntimeError("NO_STABLE_SYSTEM_FONT")
    font_selection = {"font_path": str(font), "font_name": font.stem, "exists": True, "reason": "NR0-LITE caption/text fixture"}
    (preflight / "font_selection.json").write_text(json.dumps(font_selection, indent=2) + "\n")
    return {"files": records, "capabilities": capabilities, "font": str(font)}


def write_subset_srt(source: Path, target: Path, maximum: float = 45.0) -> None:
    chunks = source.read_text(encoding="utf-8-sig").strip().split("\n\n")
    selected = []
    for chunk in chunks:
        lines = chunk.splitlines()
        if len(lines) < 3:
            continue
        start, end = lines[1].split(" --> ")
        to_seconds = lambda timestamp: sum(float(part) * factor for part, factor in zip(timestamp.replace(",", ".").split(":"), (3600, 60, 1)))
        if to_seconds(end) <= maximum:
            selected.append((start, end, lines[2:]))
    target.write_text("\n\n".join(f"{index}\n{start} --> {end}\n" + "\n".join(text) for index, (start, end, text) in enumerate(selected, 1)) + "\n", encoding="utf-8")


def make_fixture(command: list[str], output: Path) -> None:
    if output.exists():
        output.unlink()
    shell(command)
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"FIXTURE_FAILED {output}")


def fixtures(font: str) -> dict[str, Any]:
    root = ROOT / "fixtures"
    if sha256(SOURCE_SRT) != EXPECTED_SRT_SHA:
        raise RuntimeError("PA1_SRT_CHECKSUM_MISMATCH")
    source_copy = root / "narration.en.source.srt"
    shutil.copy2(SOURCE_SRT, source_copy)
    caption = root / "narration.en.0_45s.srt"
    write_subset_srt(SOURCE_SRT, caption)
    if not caption.read_text().strip():
        raise RuntimeError("EMPTY_SRT_SUBSET")
    scene_a, scene_b = root / "scene_a.mp4", root / "scene_b.mp4"
    common = ["-hide_banner", "-y", "-f", "lavfi", "-i"]
    encode = ["-r", "30", "-c:v", "h264_videotoolbox", "-b:v", "8M", "-maxrate", "8M", "-bufsize", "8M", "-constant_bit_rate", "1", "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-movflags", "+faststart"]
    make_fixture([FFMPEG, *common, "testsrc2=size=1920x1080:rate=30:duration=8", *encode, str(scene_a)], scene_a)
    make_fixture([FFMPEG, *common, "smptebars=size=1920x1080:rate=30:duration=8", "-vf", "drawbox=x=120:y=120:w=1680:h=840:color=0x173f5f@0.55:t=fill,drawgrid=w=120:h=120:t=2:c=white@0.18", *encode, str(scene_b)], scene_b)
    still = root / "still.png"
    make_fixture([FFMPEG, "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=1", "-frames:v", "1", str(still)], still)
    lower = root / "lower_third.png"
    make_fixture([FFMPEG, "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=black@0.0:s=1920x1080", "-vf", f"format=rgba,drawbox=x=0:y=0:w=920:h=180:color=0x123047@0.90:t=fill,drawbox=x=0:y=0:w=14:h=180:color=0x43d5ff@1:t=fill,drawtext=fontfile={font}:text=VCOS NR0-LITE:fontcolor=white:fontsize=48:x=52:y=52", "-frames:v", "1", str(lower)], lower)
    logo = root / "logo_bug.png"
    make_fixture([FFMPEG, "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=black@0.0:s=280x100", "-vf", f"format=rgba,drawbox=x=0:y=0:w=280:h=100:color=0x123047@0.82:t=fill,drawtext=fontfile={font}:text=VCOS:fontcolor=white:fontsize=42:x=55:y=28", "-frames:v", "1", str(logo)], logo)
    audio = root / "audio_45s.m4a"
    make_fixture([FFMPEG, "-hide_banner", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=45", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=45", "-filter_complex", "[0:a][1:a]amix=inputs=2:weights='0.55 0.20',volume=0.55,afade=t=in:st=0:d=1,afade=t=out:st=43:d=2,aformat=channel_layouts=stereo", "-c:a", "aac", "-b:a", "160k", str(audio)], audio)
    manifest = []
    for logical_role, path, dimensions, duration, fps in [
        ("scene_a", scene_a, (1920, 1080), 8, 30), ("scene_b", scene_b, (1920, 1080), 8, 30), ("static_still", still, (1920, 1080), 0, 0),
        ("lower_third_rgba", lower, (1920, 1080), 0, 0), ("logo_bug_rgba", logo, (280, 100), 0, 0), ("audio_45s", audio, (None, None), 45, 0),
        ("caption_subset_45s", caption, (None, None), 45, 0), ("source_srt_copy", source_copy, (None, None), 491.571, 0),
    ]:
        manifest.append({"fixture_id": path.stem, "logical_role": logical_role, "path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size, "duration_seconds": duration, "width": dimensions[0], "height": dimensions[1], "fps": fps, "mime/container": path.suffix.lstrip("."), "synthetic": logical_role not in ("caption_subset_45s", "source_srt_copy"), "production_asset": False})
    (root / "fixtures_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {item["logical_role"]: item["path"] for item in manifest}


def command_text(command: list[str]) -> str:
    import shlex
    return "#!/bin/sh\nset -eu\n" + " ".join(shlex.quote(part) for part in command) + "\n"


def monitor(process: subprocess.Popen[str], stop: threading.Event, samples: list[dict[str, Any]], abort: dict[str, bool]) -> None:
    while process.poll() is None and not stop.is_set():
        current_free = free_bytes()
        sample: dict[str, Any] = {"time_epoch": time.time(), "free_bytes": current_free}
        children = subprocess.run(["pgrep", "-P", str(process.pid)], text=True, capture_output=True)
        sampled_pid = children.stdout.splitlines()[0] if children.stdout.splitlines() else str(process.pid)
        sample["sampled_pid"] = int(sampled_pid)
        ps = subprocess.run(["ps", "-o", "rss=,pcpu=", "-p", sampled_pid], text=True, capture_output=True)
        fields = ps.stdout.split()
        if len(fields) >= 2:
            sample["rss_kib"] = int(float(fields[0]))
            sample["cpu_percent"] = float(fields[1])
        vm = subprocess.run(["vm_stat"], text=True, capture_output=True)
        sample["vm_stat_tail"] = vm.stdout.splitlines()[-3:]
        io = subprocess.run(["iostat", "-Id", "disk0", "1", "1"], text=True, capture_output=True)
        sample["iostat_tail"] = io.stdout.splitlines()[-2:]
        samples.append(sample)
        if current_free < ABORT_FREE_GB * 1024**3:
            abort["low_disk"] = True
            process.terminate()
            return
        time.sleep(1)


def contact_sheet(media: Path, target: Path, duration: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [FFMPEG, "-hide_banner", "-y", "-i", str(media), "-vf", f"fps=1/{max(1, duration // 4)},scale=480:270,tile=2x2", "-frames:v", "1", str(target)]
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode != 0:
        target.write_bytes(b"")


def caption_frames(media: Path, target_dir: Path, duration: float) -> list[str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamps = sorted({min(max(1.0, value), max(1.0, duration - 0.5)) for value in (1.0, duration / 2, duration - 1.0)})
    paths = []
    for index, timestamp in enumerate(timestamps, 1):
        frame = target_dir / f"caption_{index}_{timestamp:.1f}s.jpg"
        completed = subprocess.run([FFMPEG, "-hide_banner", "-y", "-ss", str(timestamp), "-i", str(media), "-frames:v", "1", "-q:v", "2", str(frame)], text=True, capture_output=True)
        if completed.returncode == 0 and frame.exists():
            paths.append(str(frame))
    return paths


def run_case(name: str, command: list[str], filtergraph: str, output: Path, duration: int, audio_required: bool, caption_expected: bool = False) -> dict[str, Any]:
    run_dir = ROOT / "runs" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    part = output.with_name(f"{output.stem}.part{output.suffix}")
    command = [part if str(token) == "__OUTPUT_PART__" else token for token in command]
    command = [str(token) for token in command]
    (run_dir / "command.sh").write_text(command_text(command))
    (run_dir / "filtergraph.txt").write_text(filtergraph + "\n")
    before = free_bytes()
    start = time.monotonic()
    samples: list[dict[str, Any]] = []
    abort = {"low_disk": False}
    timed_command = ["/usr/bin/time", "-l", *command]
    process = subprocess.Popen(timed_command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stopped = threading.Event()
    watcher = threading.Thread(target=monitor, args=(process, stopped, samples, abort), daemon=True)
    watcher.start()
    stdout, stderr = process.communicate()
    stopped.set(); watcher.join()
    elapsed = time.monotonic() - start
    (run_dir / "render.stderr.log").write_text(stderr)
    (run_dir / "exit_code.txt").write_text(str(process.returncode) + "\n")
    (run_dir / "telemetry_samples.json").write_text(json.dumps(samples, indent=2) + "\n")
    if process.returncode == 0 and not abort["low_disk"] and part.exists():
        tagged_part = output.with_name(f"{output.stem}.tag.part{output.suffix}")
        tag = subprocess.run([FFMPEG, "-hide_banner", "-y", "-i", str(part), "-map", "0", "-c", "copy", "-movflags", "+faststart+write_colr", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", str(tagged_part)], text=True, capture_output=True)
        with (run_dir / "render.stderr.log").open("a") as log:
            log.write("\n--- color-tag remux ---\n" + tag.stderr)
        if tag.returncode == 0 and tagged_part.exists():
            part.unlink(missing_ok=True)
            tagged_part.replace(output)
        else:
            tagged_part.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
    else:
        part.unlink(missing_ok=True)
    after = free_bytes()
    time_rss = re.search(r"([0-9]+)\s+maximum resident set size", stderr)
    time_rss_kib = int(time_rss.group(1)) // 1024 if time_rss else 0
    summary: dict[str, Any] = {"name": name, "exit_code": process.returncode, "aborted_low_disk": abort["low_disk"], "wall_clock_seconds": round(elapsed, 3), "free_disk_before_bytes": before, "free_disk_after_bytes": after, "max_rss_kib": max(max((item.get("rss_kib", 0) for item in samples), default=0), time_rss_kib), "max_cpu_percent": max((item.get("cpu_percent", 0) for item in samples), default=0), "output": str(output), "output_size_bytes": output.stat().st_size if output.exists() else 0}
    if output.exists():
        raw, report = qc(FFMPEG, FFPROBE, output, {"width": 1920, "height": 1080, "fps": "30/1", "audio_required": audio_required})
        (run_dir / "ffprobe_full.json").write_text(json.dumps(raw, indent=2) + "\n")
        report["caption_automated_result"] = "LIKELY_PRESENT_FILTER_APPLIED" if caption_expected else "NOT_APPLICABLE"
        if caption_expected:
            report["caption_frame_paths"] = caption_frames(output, run_dir / "caption_frames", report["duration_seconds"])
        (run_dir / "MediaQCReport.json").write_text(json.dumps(report, indent=2) + "\n")
        (run_dir / "ffprobe_video_qc.json").write_text(json.dumps(report, indent=2) + "\n")
        (run_dir / "filesize.txt").write_text(str(output.stat().st_size) + "\n")
        contact = ROOT / "contact_sheets" / f"{name}.jpg"
        contact_sheet(output, contact, duration)
        if contact.exists():
            shutil.copy2(contact, run_dir / "contact_sheet.jpg")
        summary.update({"media_qc_pass": report["overall_pass"], "duration_seconds": report["duration_seconds"], "realtime_factor": round(elapsed / max(report["duration_seconds"], 0.001), 4), "faststart": report["checks"]["faststart"], "av_drift_ms": report["av_drift_ms"]})
    else:
        summary.update({"media_qc_pass": False, "realtime_factor": None})
    (run_dir / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def ffmpeg_output() -> Path:
    return Path("__OUTPUT_PART__")


def run_all() -> dict[str, Any]:
    ensure_layout()
    preflight = save_preflight()
    font = preflight["font"]
    fixture = fixtures(font)
    scene_a, scene_b, still, lower, logo, audio, caption = (fixture[key] for key in ("scene_a", "scene_b", "static_still", "lower_third_rgba", "logo_bug_rgba", "audio_45s", "caption_subset_45s"))
    output_root = ROOT / "outputs"
    encode = ["-r", "30", "-c:v", "h264_videotoolbox", "-b:v", "8M", "-maxrate", "8M", "-bufsize", "8M", "-constant_bit_rate", "1", "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-movflags", "+faststart"]
    draw_probe_graph = f"drawtext=fontfile={font}:text='VCOS NativeFFmpeg drawtext probe':fontcolor=white:fontsize=52:x=(w-text_w)/2:y=(h-text_h)/2,format=yuv420p"
    draw_probe = run_case("NR0L_drawtext_probe", [FFMPEG, "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=0x203040:s=1920x1080:r=30:d=5", "-vf", draw_probe_graph, "-t", "5", *encode, str(ffmpeg_output())], draw_probe_graph, output_root / "nr0l_drawtext_probe.mp4", 5, False)
    subtitle_probe_graph = f"subtitles=filename={caption}:fontsdir={Path(font).parent}:force_style='FontName=Arial,FontSize=34,PrimaryColour=&H00FFFFFF,OutlineColour=&H00102030,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=90',format=yuv420p"
    subtitle_probe = run_case("NR0L_subtitle_probe", [FFMPEG, "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=0x203040:s=1920x1080:r=30:d=5", "-vf", subtitle_probe_graph, "-t", "5", *encode, str(ffmpeg_output())], subtitle_probe_graph, output_root / "nr0l_subtitle_probe.mp4", 5, False, True)
    if not draw_probe.get("media_qc_pass") or not subtitle_probe.get("media_qc_pass"):
        raise RuntimeError("CAPTION_TEXT_EXECUTION_FAILED")
    cases: list[tuple[str, list[str], str, int, bool, bool]] = []
    graph = "[0:v][1:v]xfade=transition=slideleft:duration=1:offset=4,format=yuv420p"
    cases.append(("NR0L_slideleft", [FFMPEG, "-hide_banner", "-y", "-i", scene_a, "-i", scene_b, "-filter_complex", graph, "-t", "7", *encode, str(ffmpeg_output())], graph, 7, False, False))
    graph = "[0:v][1:v]xfade=transition=fade:duration=1:offset=4,format=yuv420p"
    cases.append(("NR0L_fade_dissolve", [FFMPEG, "-hide_banner", "-y", "-i", scene_a, "-i", scene_b, "-filter_complex", graph, "-t", "7", *encode, str(ffmpeg_output())], graph, 7, False, False))
    graph = f"[1:v]format=rgba[lt];[0:v][lt]overlay=x='if(lt(t,1),-w+(w+80)*t,80)':y=840:format=auto,drawtext=fontfile={font}:text='LOCAL ONLY':fontcolor=white:fontsize=32:x=100:y=990,format=yuv420p"
    cases.append(("NR0L_lowerthird", [FFMPEG, "-hide_banner", "-y", "-i", scene_a, "-loop", "1", "-i", lower, "-filter_complex", graph, "-t", "8", *encode, str(ffmpeg_output())], graph, 8, False, False))
    graph = "zoompan=z='min(zoom+0.0008,1.10)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30,format=yuv420p"
    cases.append(("NR0L_kenburns", [FFMPEG, "-hide_banner", "-y", "-loop", "1", "-i", still, "-vf", graph, "-t", "8", *encode, str(ffmpeg_output())], graph, 8, False, False))
    graph = f"subtitles=filename={caption}:fontsdir={Path(font).parent}:force_style='FontName=Arial,FontSize=28,PrimaryColour=&H00FFFFFF,OutlineColour=&H00102030,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=90',format=yuv420p"
    cases.append(("NR0L_caption", [FFMPEG, "-hide_banner", "-y", "-i", scene_a, "-vf", graph, "-t", "8", *encode, str(ffmpeg_output())], graph, 8, False, True))
    graph = "[1:v]format=rgba[logo];[0:v][logo]overlay=x=W-w-80:y=80:format=auto,format=yuv420p"
    cases.append(("NR0L_logo_bug", [FFMPEG, "-hide_banner", "-y", "-i", scene_a, "-loop", "1", "-i", logo, "-filter_complex", graph, "-t", "8", *encode, str(ffmpeg_output())], graph, 8, False, False))
    results = []
    for name, command, graph, duration, audio_required, caption_expected in cases:
        result = run_case(name, command, graph, output_root / f"{name}.mp4", duration, audio_required, caption_expected)
        results.append(result)
        if not result.get("media_qc_pass"):
            raise RuntimeError(f"MOTION_SMOKE_FAILED {name}")
    if free_bytes() < MIN_FREE_GB * 1024**3:
        raise RuntimeError("BLOCKED_LOW_DISK_HEADROOM_BEFORE_E2E")
    e2e_graph = f"[0:v]trim=duration=16,setpts=PTS-STARTPTS[a];[1:v]trim=duration=16,setpts=PTS-STARTPTS[b];[2:v]loop=loop=-1:size=1:start=0,trim=duration=16,setpts=PTS-STARTPTS,zoompan=z='min(zoom+0.0007,1.10)':d=1:s=1920x1080:fps=30,settb=1/15360,setpts=PTS-STARTPTS[c];[a][b]xfade=transition=fade:duration=1:offset=15[ab];[ab][c]xfade=transition=slideleft:duration=1:offset=30[abc];[3:v]format=rgba[lt];[4:v]format=rgba[lg];[abc][lt]overlay=x='if(lt(t,1),-w+(w+80)*t,80)':y=840:format=auto[withlt];[withlt][lg]overlay=x=W-w-80:y=80:format=auto,drawbox=x=1320:y=200:w=460:h=150:color=0x123047@0.85:t=fill,drawtext=fontfile={font}:text='LOCAL DATA CARD':fontcolor=white:fontsize=34:x=1360:y=250,subtitles=filename={caption}:fontsdir={Path(font).parent}:force_style='FontName=Arial,FontSize=28,PrimaryColour=&H00FFFFFF,OutlineColour=&H00102030,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=90',trim=duration=45,setpts=PTS-STARTPTS,format=yuv420p[v];[5:a]asplit=2[voice][music];[music]volume=0.12[musiclow];[voice][musiclow]amix=inputs=2:weights='1 1',afade=t=in:st=0:d=1,afade=t=out:st=43:d=2,aresample=48000[aout]"
    e2e_command = [FFMPEG, "-hide_banner", "-y", "-stream_loop", "-1", "-i", scene_a, "-stream_loop", "-1", "-i", scene_b, "-loop", "1", "-i", still, "-loop", "1", "-i", lower, "-loop", "1", "-i", logo, "-i", audio, "-filter_complex", e2e_graph, "-map", "[v]", "-map", "[aout]", "-t", "45", *encode, "-c:a", "aac", "-b:a", "160k", str(ffmpeg_output())]
    e2e = run_case("NR0L_1080P_END_TO_END", e2e_command, e2e_graph, output_root / "nr0l_1080p_end_to_end.mp4", 45, True, True)
    if not e2e.get("media_qc_pass"):
        raise RuntimeError("E2E_QC_FAILED")
    technical_pass = draw_probe["media_qc_pass"] and subtitle_probe["media_qc_pass"] and all(item["media_qc_pass"] for item in results) and e2e["realtime_factor"] <= 2 and e2e["max_rss_kib"] <= 12 * 1024 * 1024
    return {"root": str(ROOT), "toolchain": preflight, "fixtures": fixture, "probes": [draw_probe, subtitle_probe], "motion_results": results, "e2e": e2e, "technical_pass": technical_pass, "retained_bytes": sum(path.stat().st_size for path in ROOT.rglob("*") if path.is_file()), "free_disk_after_bytes": free_bytes()}


if __name__ == "__main__":
    try:
        result = run_all()
        (ROOT / "nr0_lite_run_summary.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    except Exception as error:
        ROOT.mkdir(parents=True, exist_ok=True)
        failure = {"error": str(error), "free_disk_bytes": free_bytes() if ROOT.exists() else None}
        (ROOT / "nr0_lite_run_failure.json").write_text(json.dumps(failure, indent=2) + "\n")
        print(json.dumps(failure, indent=2), file=sys.stderr)
        raise
