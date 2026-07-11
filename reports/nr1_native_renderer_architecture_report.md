# NR1 Native renderer architecture report

Date: 2026-07-11. Scope: local-only, non-production.

Stack reconciliation, contracts, NativeMotionPack_v1, deterministic compiler, secure argv command builder, local wrapper, workspace boundary, output profiles, ffprobe MediaQC, execution/human/archive receipt contracts and read-only evidence routes are implemented. Creatomate history remains, but first-channel canonical truth rejects it as stale/deferred. NativeFFmpeg is local and has no paid-provider ledger path.

The 12-second 1920×1080/30 synthetic smoke used ffmpeg-full 8.1.2, H.264 VideoToolbox, yuv420p BT.709, AAC 48 kHz stereo and fast-start. QC PASS. Evidence is under `var/tmp/native_renderer/runs/nr1-local-synthetic-smoke/`. No provider, Drive or YouTube call occurred; no FinalMediaRef, CloudMediaRef or HumanUploadTask was created.

Architecture and local smoke pass. The operator explicitly completed human review on 2026-07-11 and marked scene sequence, narrative fidelity, motion/transitions, caption safe area, lower-third/data card layout, Ken Burns smoothness, audio sync, overall renderer foundation quality, and evidence readability as PASS.

Final verdict: `NR1_HUMAN_REVIEW=PASS`; `NR1_FINAL=PASS`; `PROCEED_TO_NR2=true`. NR2 was not run as part of this closeout update.
