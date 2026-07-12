# NR0-LITE - NativeFFmpegRenderer Toolchain + 1080p Feasibility

Date: 2026-07-11
Scope: local-only; synthetic fixtures; no VCOS runtime registration.

## Verdict

```txt
NR0_LITE_TOOLCHAIN=PASS
NR0_LITE_TECHNICAL=PASS
NR0_LITE_HUMAN_REVIEW=PASS
NR0_LITE_FINAL=PASS
PROCEED_TO_OFV0_NR1=true
```

The Mac mini M4 passes the reduced 1080p path. Operator human review of every NR0-LITE output, including the end-to-end video, is PASS. This authorizes the next decision gate only; NR0-LITE did not run OFV0/NR1.

## Boundaries and repository preflight

| Item | Result |
| --- | --- |
| Worktree at start | One existing untracked NR0 report: `reports/nr0_native_ffmpeg_renderer_feasibility_mac_m4.md`; not reverted |
| `git diff --check` at start | PASS |
| Internal Data volume headroom before first render | 66.26 GiB; threshold 40 GiB PASS |
| Guard | `NR0_LITE_ROOT=var/tmp/native_ffmpeg_nr0_lite`, abort below 20 GiB, one render at a time |
| End-to-end render disk | 66.44 GiB before; 66.40 GiB after; no abort |
| Cleanup | no `.part` / `.tag.part` remains; `temp/` empty; retained NR0-LITE total 52,016,381 bytes (49.6 MiB) |

`.gitignore` already covers `var/tmp/`, `*.mp4`, and `*.mov`; no broad edit was made. No 4K, ProRes, parallel render, external scratch fallback, or provider asset was used.

## Toolchain remediation

| Item | Result |
| --- | --- |
| Existing binary retained | `/opt/homebrew/bin/ffmpeg` (8.1.2); not unlinked/replaced |
| Side-by-side binary | `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` (8.1.2_1) |
| Homebrew action | `ffmpeg-full` was already installed; no install/download/unlink/link action executed |
| FFmpeg-full build | VideoToolbox, AudioToolbox, libass, libfreetype, fontconfig enabled |
| Required encoder | `h264_videotoolbox`: PASS; `aac`: PASS |
| Optional encoder observed | `hevc_videotoolbox`: present; `prores_videotoolbox`: present; neither rendered |
| Required filters | `xfade`, `overlay`, `fade`, `zoompan`, `scale`, `crop`, `fps`, `format`, `drawtext`, `subtitles`, `drawbox`, `amix`, `afade`, `volume`: all present |
| `ass` filter | present; not required independently because `subtitles` SRT burn-in passed |

Captured preflight: `var/tmp/native_ffmpeg_nr0_lite/preflight/`.

Selected caption font: `/System/Library/Fonts/Supplemental/Arial.ttf`; evidence: `preflight/font_selection.json`.

Actual text probes, both H.264 VideoToolbox / yuv420p / BT.709 / Fast Start:

- drawtext: `outputs/nr0l_drawtext_probe.mp4` - PASS, 5.0 s.
- subtitles/libass: `outputs/nr0l_subtitle_probe.mp4` - PASS, 5.0 s.

## Fixtures

- Source SRT was copied only after SHA-256 matched PA1: `0bdcd564a3d47c342b52bc0d057a510656e380087fbcd69d0ab4015ea310f6a2`.
- Local subset `narration.en.0_45s.srt` is renumbered, ordered, and ends no later than 45 s.
- Two 8 s 1080p30 synthetic clips, static still, RGBA lower-third, RGBA logo, and 45 s AAC 48 kHz stereo fixture were generated locally.
- Full checksum/shape manifest: `var/tmp/native_ffmpeg_nr0_lite/fixtures/fixtures_manifest.json`.

## Probe and motion smoke results

All are 1920x1080, 30 fps, H.264 VideoToolbox, yuv420p, BT.709-tagged MP4 Fast Start, local-only.

| Run | Duration | Elapsed | Realtime | Peak RSS | QC |
| --- | ---: | ---: | ---: | ---: | --- |
| drawtext probe | 5.0 s | 1.019 s | 0.204x | 107 MiB | PASS |
| subtitles probe | 5.0 s | 1.019 s | 0.204x | 454 MiB | PASS |
| `NR0L_slideleft` | 7.0 s | 2.036 s | 0.291x | 336 MiB | PASS |
| `NR0L_fade_dissolve` | 7.0 s | 2.044 s | 0.292x | 333 MiB | PASS |
| `NR0L_lowerthird` | 8.0 s | 2.038 s | 0.255x | 362 MiB | PASS |
| `NR0L_kenburns` | 8.0 s | 2.035 s | 0.254x | 192 MiB | PASS |
| `NR0L_caption` | 8.0 s | 2.038 s | 0.255x | 532 MiB | PASS |
| `NR0L_logo_bug` | 8.0 s | 2.036 s | 0.254x | 230 MiB | PASS |

Each run has `command.sh`, `filtergraph.txt`, stderr, exit code, ffprobe, MediaQC, telemetry, output size, and a contact sheet in `var/tmp/native_ffmpeg_nr0_lite/runs/<run>/`. All exit codes are 0 and decode checks are clean.

## `NR0L_1080P_END_TO_END`

Path: `var/tmp/native_ffmpeg_nr0_lite/outputs/nr0l_1080p_end_to_end.mp4`

Timeline uses local fixtures only: clean cut, fade/dissolve, slide transition, Ken Burns still path, lower-third, data card, logo bug, SRT burn-in, and mixed AAC with fade in/out.

| Field | Observed | Threshold | Result |
| --- | ---: | ---: | --- |
| Duration | 45.000 s | ~45 s | PASS |
| Render elapsed | 12.218 s | <=90 s (2x) | PASS, 0.272x realtime |
| Peak RSS | 1,098,896 KiB (about 1.05 GiB) | <=12 GiB | PASS |
| CPU sample peak | 468.6% | telemetry only | recorded |
| Video | H.264, 1920x1080, 30/1, yuv420p | required | PASS |
| Color | `bt709` space/transfer/primaries | required | PASS |
| Audio | AAC, 48 kHz, stereo | required | PASS |
| A/V drift | 0.0 ms | <=250 ms | PASS |
| Container/decode | MP4, non-empty, clean full decode | required | PASS |
| Fast Start | `moov` offset 32 before `mdat` offset 40,688 | required | PASS |
| Output size | 18,133,367 bytes (17.3 MiB) | sanity signal | WARN |

The command set 8 Mbps / maxrate 8 Mbps / VideoToolbox CBR request; this synthetic output measured 3.062 Mbps video and 3.224 Mbps container. It is not a technical fail under NR0-LITE size guidance, but it is a profile-enforcement follow-up before production profile freeze.

## QC and diagnostics

`MediaQCReport.json` is present for every output. End-to-end report: `runs/NR0L_1080P_END_TO_END/MediaQCReport.json`.

- `blackdetect`: 0 events.
- `silencedetect`: 0 events; audio stream is present and duration-aligned.
- `freezedetect`: 18 events in low-motion synthetic/still portions (first 16.3 s). Operator reviewed these portions and accepted the visual output; no unintended freeze or jitter was observed.
- Caption automation: subtitles filter executed, decode passed, and frames were extracted at 1.0 s, 22.5 s, and 44.0 s. This is only `LIKELY_PRESENT_FILTER_APPLIED`; readability/glyph/safe-area approval remains human-only.

Contact sheets: `var/tmp/native_ffmpeg_nr0_lite/contact_sheets/`. Caption-frame paths are stored in each caption `MediaQCReport.json`.

## Tooling and changed files

- `tools/native_ffmpeg/nr0_lite/run_nr0_lite.py`
- `tools/native_ffmpeg/nr0_lite/media_qc.py`
- `tools/native_ffmpeg/nr0_lite/faststart_check.py`
- `tools/native_ffmpeg/nr0_lite/README.md`
- This report, checklist, summary JSON, and one P2 pain-log entry.

The tooling is isolated; no `app/`, frontend, Alembic, runtime registration, DB migration, dashboard control, commit, or tag was created.

## Regression checks

| Command | Result |
| --- | --- |
| `PYTHONPATH=. .venv/bin/python -m compileall -q app` | PASS |
| `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` | PASS, 13 passed (one existing Starlette deprecation warning) |
| `PYTHONPATH=. .venv/bin/pytest tests/test_dx2_provider_stack_reconciliation.py -q` | PASS, 7 passed |
| `git diff --check` | PASS |

## No-execution proof

This run executed local FFmpeg/ffprobe/Homebrew inspection only. It made no network or media-provider call and made no DB write. No ElevenLabs, Google Veo, Pexels, Drive, or YouTube action occurred. No FinalMediaRef, CloudMediaRef, HumanUploadTask, production MediaRenderJob, submitted ProviderJobSnapshot, executed PaidProviderCallLedger, UploadedVideo, Channel Contract, ChannelProfileVersion, EffectiveChannelRuntimeContextSnapshot, learning promotion, or prompt mutation was created. Existing zero-count proof remains in `reports/code_closeout_readiness_prod_v1.md` and `reports/pa1_precheck_srt_ollama_rehearsal_report.md`.

## Classification and recommendation

| Severity | Finding | Decision |
| --- | --- | --- |
| P0 | none | none |
| P1 | none | none |
| P2 | VideoToolbox accepted the requested 8 Mbps CBR parameters but the synthetic output measured 3.062 Mbps video. | Logged as `PPL-NR0L-001`; define production bitrate/tolerance policy before profile freeze. |
| P3 | none | none |

Technical 1080p feasibility and human review are PASS. `PROCEED_TO_OFV0_NR1=true` is an authorization for the next decision gate only; do not run OFV0/NR1 automatically.
