# NR0 - NativeFFmpegRenderer Technical Feasibility (Mac mini M4)

Date: 2026-07-11
Mode: local-only, no provider/DB/runtime execution

## Verdict

`NR0=BLOCKED / NOT_READY_FOR_OFV0_OR_NR1`

The installed FFmpeg can use Apple VideoToolbox and has the core motion/compositing filters.  It cannot currently meet the required caption burn-in capability: `drawtext`, `subtitles`, and `ass` are absent.  In addition, the required external scratch root is not mounted, so no smoke render was run.  Per NR0 boundary, no render output was written to the Mac internal SSD.

## Source and scope check

- Read: Runtime LTS, provider stack freeze, post-freeze protocol, production pain log, code closeout, production launch plan, and PA1 SRT rehearsal report.
- The requested `NativeFFmpegRenderer cho VCOS first-channel production.pdf` (or an equivalent NR0 renderer report) was not present in this checkout at time of run.
- Used existing local fixture only: `var/tmp/pa1-precheck-srt/7c4a6b4e-60d2-4e99-8d65-304afef33c2b/narration.en.srt`.
- Fixture checksum matches PA1 report: `0bdcd564a3d47c342b52bc0d057a510656e380087fbcd69d0ab4015ea310f6a2`.
- No provider/network call; no DB write; no FinalMediaRef, CloudMediaRef, HumanUploadTask, MediaRenderJob, ProviderJobSnapshot, or PaidProviderCallLedger was created.

## Host and storage preflight

| Item | Observation | Result |
| --- | --- | --- |
| Host | Mac mini `Mac16,10`, Apple M4, 10 CPU cores, 16 GB unified memory | Recorded |
| OS | macOS 26.5.1 (25F80), arm64 | Recorded |
| Internal root | 67 GiB available of 228 GiB | Not used for NR0 render |
| `VCOS_SCRATCH_ROOT` | Defaults to `/Volumes/VCOS_SCRATCH`; directory/mount absent | **BLOCKED** |

Required precondition before any render: mount a writable external volume, then set `VCOS_SCRATCH_ROOT` to it.  Do not substitute the internal SSD, especially for 4K.

## FFmpeg capability inspection

FFmpeg: `/opt/homebrew/bin/ffmpeg`, version `8.1.2`; build includes `--enable-videotoolbox`, `--enable-audiotoolbox`, and `--enable-neon`.

| Requirement | Evidence | Status |
| --- | --- | --- |
| H.264 VideoToolbox | `h264_videotoolbox` encoder listed; accepts `nv12`/`yuv420p` | PASS (inspection) |
| HEVC VideoToolbox | `hevc_videotoolbox` encoder listed; Main/Main10 profiles listed | PASS (inspection) |
| ProRes VideoToolbox | `prores_videotoolbox` encoder listed | PASS (inspection) |
| Hardware acceleration | `videotoolbox` listed by `-hwaccels` | PASS (inspection) |
| Native motion/compositing | `scale`, `crop`, `fps`, `trim`, `setpts`, `concat`, `overlay`, `xfade`, `fade`, `zoompan`, `format`, `colorspace`, `zscale` listed | PASS (inspection only) |
| Audio processing | `adelay`, `amix`, `aresample`, `loudnorm`, `afade`, `anullsrc` listed | PASS (inspection only) |
| Caption burn-in | `drawtext`, `subtitles`, and `ass` absent from `ffmpeg -filters`; buildconf has no libass/freetype enable flag | **FAIL** |

## Required smoke matrix

No entry below was executed.  Execution would create temporary/output media, which must be external scratch only.

| Smoke | Target | Status | Reason |
| --- | --- | --- | --- |
| Long-form 1080p | H.264 VideoToolbox, AAC, SRT burn-in, deterministic timeline | NOT RUN | external scratch missing; caption filter missing |
| Long-form 4K30 | HEVC/H.264 VideoToolbox, AAC, SRT burn-in, deterministic timeline | NOT RUN | external scratch missing; caption filter missing; internal SSD forbidden |
| Shorts 9:16 | 1080x1920 H.264 VideoToolbox, AAC, SRT burn-in | NOT RUN | external scratch missing; caption filter missing |
| Motion presets | hold, crop/scale, pan/zoom, fade, xfade, overlay | NOT RUN | external scratch missing |
| Output QC | codec/audio/caption/color/timing/file decode | NOT RUN | no output artifact |
| Performance telemetry | elapsed time, peak RSS, scratch high-water mark | NOT RUN | no output artifact |

## Gate result

1. VideoToolbox encoder and baseline motion/audio filter inspection: partial PASS.
2. Caption burn-in: FAIL on installed build.
3. Required 1080p, 4K30, and 9:16 smoke renders: NOT RUN.
4. Codec, audio, captions, color, timing, and output-file validation: NOT RUN.
5. Render time, memory, and scratch requirement measurement: NOT RUN.
6. Safety to proceed to OFV0/NR1: **NO**.

## Unblock conditions

1. Operator mounts a writable external scratch volume and exports, for example, `VCOS_SCRATCH_ROOT=/Volumes/<external-volume>/vcos-nr0`.
2. Operator explicitly approves a non-mutating FFmpeg package remediation path that provides a proven caption burn-in filter (`subtitles`/libass preferred, or `drawtext` with a controlled font strategy).  NR0 must not reinstall or modify Homebrew/FFmpeg without that approval.
3. Provide the renderer PDF/equivalent technical specification if it exists outside this checkout, so the smoke presets and output profiles can be tested against the authoritative contract.
4. Rerun the three smoke renders and collect ffprobe/decode, timing, peak RSS, and scratch high-water evidence.  Only then decide OFV0/NR1.

## Commands used (read-only)

```bash
VCOS_SCRATCH_ROOT="${VCOS_SCRATCH_ROOT:-/Volumes/VCOS_SCRATCH}"
ffmpeg -hide_banner -version
ffmpeg -hide_banner -encoders
ffmpeg -hide_banner -filters
ffmpeg -hide_banner -hwaccels
ffmpeg -hide_banner -h encoder=h264_videotoolbox
ffmpeg -hide_banner -h encoder=hevc_videotoolbox
ffmpeg -hide_banner -buildconf
ffmpeg -hide_banner -filters | rg ' drawtext| subtitles| ass '
ffmpeg -hide_banner -filters | rg 'scale|crop|fps|trim|setpts|concat|overlay|xfade|fade|zoompan|format|colorspace|zscale|adelay|amix|aresample|loudnorm|afade|anullsrc'
```
