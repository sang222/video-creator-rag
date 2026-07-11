# NR0-LITE local renderer smoke

This folder is isolated experiment tooling; it is not registered in VCOS runtime.

```bash
export NR0_LITE_ROOT="$PWD/var/tmp/native_ffmpeg_nr0_lite"
export NR0_LITE_MIN_FREE_GB=40
export NR0_LITE_ABORT_FREE_GB=20
export NR0_FFMPEG_BIN="$(brew --prefix ffmpeg-full)/bin/ffmpeg"
export NR0_FFPROBE_BIN="$(brew --prefix ffmpeg-full)/bin/ffprobe"
python3 tools/native_ffmpeg/nr0_lite/run_nr0_lite.py
```

The runner only uses local synthetic fixtures and the existing PA1 SRT artifact. It uses one FFmpeg render process at a time, samples disk/RSS/CPU/vm/iostat, aborts beneath the configured disk guard, and leaves final review outputs plus QC evidence under `var/tmp/` (gitignored).
