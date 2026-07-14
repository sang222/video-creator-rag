# CQR1-B — Caption and Voice Quality

Date: 2026-07-14. Scope: historical PA1R defect baseline, channel-scoped pacing/caption/sync policy, deterministic caption compilation, measured libass geometry, strict canonical renderer integration and local golden media verification. CQR1-B made no provider, paid narration, Drive, YouTube or publish call.

## Verdict

```text
CQR1B_HISTORICAL_BASELINE=CAPTURED
CQR1B_TYPED_POLICY_SNAPSHOT=PASS
CQR1B_NARRATION_PACING=PASS_OFFLINE_FIXTURE
CQR1B_CAPTION_COMPILATION=PASS_OFFLINE_FIXTURE
CQR1B_CAPTION_LAYOUT=PASS_OFFLINE_FIXTURE
CQR1B_CAPTION_SAFE_AREA=PASS_OFFLINE_FIXTURE
CQR1B_CAPTION_AUDIO_SYNC=PASS_OFFLINE_FIXTURE
CQR1B_CAPTION_COVERAGE=PASS_OFFLINE_FIXTURE
CQR1B_TIMELINE_DRIFT=PASS_OFFLINE_FIXTURE
CQR1B_STRICT_GOLDEN_RENDER=PASS_LOCAL
CQR1B_TECHNICAL_MEDIA_QC=PASS_LOCAL_GOLDEN
CQR1B_PROVIDER_CALL_COUNT=0
CQR1B_OFFLINE_VERDICT=PASS
CQR1B_PAID_NARRATION=NOT_RUN
CQR1B_REAL_PROVIDER_ALIGNMENT=NOT_RUN
CQR1B_HUMAN_FULL_WATCH=NOT_RUN
CQR1B_DRIVE_ARCHIVE=NOT_RUN
```

Primary verdict: **OFFLINE PASS**. `PASS_OFFLINE_FIXTURE` and `PASS_LOCAL_GOLDEN` validate the deterministic implementation and local render boundary. They do not claim that a new paid voice, real canary, human watchability review or archive was completed.

## Historical PA1R baseline and defect statement

PA1R remains immutable historical evidence. Its technical render completed, but the later CQR1 review identified three defects inside CQR1-B scope: subtitles were visually dominant, narration felt rushed/unnatural, and subtitles did not reliably follow final narration. Visual relevance/coherence is handled separately by CQR1-C.

| Historical evidence | Baseline value | CQR1-B interpretation |
| --- | ---: | --- |
| Reused ElevenLabs audio duration | `24.102313s` | Final audio endpoint |
| Render duration | `25.000000s` | Independent fixed render endpoint |
| Absolute endpoint difference | `0.897687s` | Larger than the new `250ms` hard drift boundary |
| Old SRT cue CPS | `6.429 / 9.000 / 6.875 / 11.500` | Low reading load alone did not prove acceptable geometry or sync |
| Old delivered-rate estimate | `129.448 WPM` | Estimate only; it cannot substitute for final-audio active/delivered/hook measurements |
| Old measured caption bbox width | `1574 / 1920 = 0.8198` | Above the new long-form hard maximum block-width ratio `0.74` |

The `24.102313s` audio and `25.0s` render are recorded in `reports/pa1r_summary.json`; CQR1-A also traced the old fixed `0–7 / 7–13 / 13–21 / 21–25` scene plan and final `-t 25`. The SRT CPS, WPM estimate and measured bbox are retrospective CQR1 baseline measurements, not measurements from the new local golden fixture.

This baseline demonstrates why technical decode PASS, character-count/CPS checks, or a historical checklist cannot by themselves establish current creative quality. The old cue CPS values were below the new reading-speed limits, yet the actual `0.8198` block width was still dominant. CQR1-B therefore requires measured geometry and canonical timing in addition to text load.

## Versioned policy

The approved offline snapshot is catalog `1.0.0`, channel policy `small-team-ai.creative-quality.v1`. Services receive typed policy data; they contain no branch hard-coded to the channel name.

Narration pacing uses final-audio evidence and a `350ms` silence-gap threshold:

| Metric | PASS | REVIEW | BLOCK |
| --- | --- | --- | --- |
| Body active-speech WPM | `145–170` | `135–180` | `>180`; extreme slow `<120` |
| Body delivered WPM | `130–155` | `120–165` | `>165`; extreme slow `<105` |
| First-8s hook active WPM | `<=180` | `<=188` | `>188` |
| Comma pause | `180–320ms` | `140–380ms` | `<140ms` |
| Sentence pause | `320–650ms` | `250–800ms` | `<250ms` |
| Section pause | `600–1200ms` | `450–1500ms` | `<450ms` |

FFmpeg `atempo` is emergency-only: absolute change up to `2%` may proceed without human approval, above `2%` requires human approval, and above `3%` blocks. It is not a normal way to conceal dense writing, poor punctuation or missing pauses.

`NarrationPacingCorrectionPlanner` makes this order executable. A measured PASS accepts the complete narration. A mild fast result can request exactly one model-speed regeneration only when that model supports speed and a provider regeneration is explicitly authorized. Dense text, short punctuation pauses or hard pace failure returns `SCRIPT_PACING_REWRITE_REQUIRED`. The current paid-canary scope authorizes no second TTS call, so a mild failure returns `PAID_TTS_REGENERATION_NOT_AUTHORIZED`. Emergency `atempo` is separately bounded, always requires remeasurement, and cannot hide a script/punctuation defect.

Caption presentation is relative to `min(frame_width, frame_height)`, never a fixed 1080p pixel authority:

| Policy | 16:9 long-form | 9:16 short-form |
| --- | ---: | ---: |
| Font-scale PASS | `0.044–0.050` | `0.046–0.054` |
| Font-scale hard bounds | `0.040–0.054` | `0.042–0.058` |
| Characters/line PASS / hard max | `42 / 46` | `32 / 36` |
| Block-width PASS / hard max | `0.68 / 0.74` | `0.84 / 0.88` |
| Bottom safe margin PASS / hard minimum | `0.08 / 0.05` | `0.12 / 0.08` |

Global caption policy permits at most two explicit lines, cue duration PASS `1.0–6.0s` with hard bounds `0.8–7.0s`, average CPS PASS `<=15` and block `>17.5`, plus per-cue/P95 hard block above `20`. The resolved ASS style additionally freezes Arial, outline ratio `0.055`, shadow ratio `0.025`, colors, border style, alignment and aspect-specific margins.

Sync requires exact spoken-token coverage `1.0` and blocks unexpected overlap. Start offsets use median/P95/max hard boundaries `120/220/300ms`; median end offset blocks above `150ms`; final drift blocks above `250ms`.

## Architecture and evidence chain

```text
SpokenTextNormalized
  -> final narration audio
  -> VerifiedNarrationAlignment
  -> NarrationPacingAnalyzer / NarrationPacingGate
  -> CanonicalMediaTimeline
  -> ReadableCaptionCompiler
  -> canonical cue payload + frozen ASS style
  -> CaptionBoundsPreflight / sync / coverage / drift gates
  -> NativeMotionCompiler (CANONICAL_STRICT)
  -> FFmpegCommandBuilder
  -> NativeFFmpegRenderer / NativeMediaQC
```

`NarrationPacingAnalyzer` derives active-speech, delivered and first-8s WPM from measured duration and verified word timing. It stores pause spans, waveform summary and word-count evidence. The gate distinguishes hard fast/short-pause defects, reviewable mild slow pace and extreme slow pace.

`ReadableCaptionCompiler` consumes only verified spoken-token timing. Every display token maps to ordered `spoken_token_ids`; coverage must remain exactly `1.0`. Approved casing/branded casing, minor punctuation simplification and known number re-compaction are permitted, while missing/extra tokens, semantic rewrites and independent timing block. Cues carry explicit one- or two-line text, first/last verified-word endpoints, reading metrics and their source segment.

Caption compilation deterministically changes the canonical timeline hash and freezes:

```text
caption_compilation_ref
caption_compilation_hash
caption_render_payload_hash
caption_render_style
caption_policy_ref / version / hash
```

The render-payload hash covers cue identity, start/end, explicit lines, spoken-token lineage and timing source. Changing a rendered line without updating the frozen hash is rejected even if the surrounding timeline is rehashed.

## Shared ASS geometry and safe area

Preflight and final rendering call the same ASS document builder and consume the same frozen `caption_render_style`. `PlayResX`, `PlayResY`, `LayoutResX` and `LayoutResY` equal the target canvas. Font size, outline, shadow and margins are resolved from relative policy. Literal ASS control sequences, backslashes, braces and embedded newlines block before document generation.

`CaptionBoundsPreflight` preserves RGBA transparency at the lavfi source before libass, then uses `alphaextract,bbox` for actual non-empty pixel geometry. This prevents an opaque YUV-to-RGBA conversion from falsely reporting the entire canvas. ASS `MarginV` includes the relative policy floor plus the frozen outline/shadow/antialias raster footprint, so the measured glyph bbox—not just the anchor—clears the safe-area threshold. Evidence includes bbox, frame size, block-width and safe-margin ratios, font scale, line count, CPL, CPS, duration, safe-zone overlap, reason codes and optional preview ref. The local tests verify that 16:9 and 9:16 preflight/final ASS style headers are byte-equivalent; the 9:16 fixture starts from the `0.12 * 1920` policy floor and resolves `233px` after its measured raster compensation.

Layout and safe-area gates block more than two lines, cue duration outside hard bounds, high CPS, missing/overflowing bbox, text outside frame, unsafe bottom margin and required subject-safe-zone overlap. Character count remains supporting evidence rather than geometry authority.

## Strict renderer integrity

`CANONICAL_STRICT` rejects an independent SRT. `NativeRenderPlan`, compiled manifest and command manifest must agree on canonical timeline ref/hash, final audio ref, caption compilation ref/hash, caption render-payload hash and frozen style. The last scene endpoint must equal canonical audio duration.

The strict command builder:

- generates ASS only from canonical cue payloads;
- derives both video and audio input duration from `canonical_duration_ms`;
- uses `-shortest` and does not add an independent final `-t`;
- hashes the generated ASS and filtergraph and binds those checksums into the command manifest;
- carries the canonical caption refs/hashes into execution.

Before FFmpeg starts, the renderer recomputes manifest content hash, command hash and every generated-file checksum. A modified ASS file, filtergraph, command argv, caption payload, authority ref or duration endpoint therefore blocks instead of silently rendering a parallel artifact.

## Local golden render

The focused suite builds one non-production strict plan with purpose `CQR1_LOCAL_GOLDEN_FIXTURE`. Its 1.12-second canonical timeline comes from the local text fixture `Calm captions align.`; the render uses a local synthetic audio carrier and is not a paid or human-quality narration sample.

Golden assertions passed for:

```text
temporal_authority_mode=CANONICAL_STRICT
production_eligible=false
independent_srt_used=false
canonical caption compilation ref/hash=BOUND
canonical caption render-payload hash=BOUND_AND_RECOMPUTED
preflight/final ASS style semantics=BYTE_EQUIVALENT
final -t=ABSENT
duration source=CANONICAL_MEDIA_TIMELINE
generated ASS/filtergraph checksums=VERIFIED
native ffmpeg/ffprobe QC=PASS
TechnicalMediaQC adapter=PASS
full decode=true
stream integrity=true
Fast Start=true
duration delta<=250ms
A/V drift<=250ms
no_provider_calls_confirmed=true
```

The actual local encode used `ffmpeg-full`, H.264 VideoToolbox, 1920x1080 at 30 fps and AAC 48 kHz stereo. The returned receipt remained local-only and non-production; its adapted `TechnicalMediaQC` report explicitly remained `not_publishable=true`.

The durable final qualification packet additionally renders a `7.770s` approved-policy fixture under `var/tmp/vcos-project-workspaces/pa1r-cqr1-20260714-paid-canary-001/offline-golden/`. Its measured active/delivered/hook pace is `152.239 / 131.274 / 152.239 WPM`; comma/sentence/section pauses are `220 / 420 / 650ms`. Actual cue bboxes are `790x110` (`0.411458` width ratio) and `435x59` (`0.226562`), both with `0.081481` bottom margin. Average/P95 CPS are `14.201 / 14.474`; every B gate PASSes. The MP4 duration is exactly `7770ms`, A/V drift is `3ms`, native and adapted technical QC PASS, and the full CreativePerceptualMediaQC aggregate PASSes with zero provider calls.

Two independent H.264 VideoToolbox encodes may differ at container/bitstream byte level. The golden determinism regression therefore hashes FFmpeg `framemd5` output and proves decoded video frames, decoded audio samples, timestamps and canonical duration are identical across two renders.

## Offline and negative verification

Exact focused CQR1-B command and result:

```text
PYTHONPATH=. .venv/bin/pytest tests/test_cqr1b_caption_voice_quality.py -q
37 passed in 18.30s
```

| Area | Positive fixtures | Blocking/review fixtures |
| --- | --- | --- |
| Pacing | comfortable measured pace; deterministic comma/sentence/section evidence; one explicitly authorized speed regeneration | fast active/delivered body, fast hook, short comma/sentence/section pause, mild slow review, extreme slow block, dense-script rewrite, unauthorized second TTS, bounded emergency `atempo` |
| Caption mapping | exact token coverage, one/two lines, acronym/branded casing, number/currency re-compaction, punctuation/clause/name/preposition breaks | missing/duplicate/extra token, semantic rewrite, three-line overflow |
| Reading/layout | valid relative 16:9 and 9:16 style, real libass bbox | flashing `<0.8s`, long `>7s`, high CPS, actual width overflow, unsafe margin, subject-safe-zone overlap |
| Sync/drift | exact verified-word start/end and final endpoint | caption lead/lag, missing/extra token, cue overlap, cumulative/final drift, cue outside audio, parallel timeline |
| Renderer integrity | canonical ref/hash/style, no independent SRT, no final `-t`, full local encode/decode | stale rendered-line hash, literal `\\N`, embedded newline, scene/audio endpoint mismatch, generated ASS tamper, command argv/hash tamper |

The suite also executes real `ffmpeg-full`/libass bbox measurement, asserts the alpha mask is not full-frame and that measured safe-area PASSes, and performs real strict MP4 encodes followed by `ffprobe`, all-stream decode, atom-order Fast Start inspection, decoded-essence determinism comparison and the technical-QC adapter. No network or provider transport is part of this path.

## Explicit non-claims and next boundary

```text
provider_call_made=false
elevenlabs_generation_call=0
forced_alignment_provider_call=0
pexels_call=0
veo_call=0
drive_call=0
youtube_call=0
paid_canary=NOT_RUN
real_paid_narration_pacing=NOT_RUN
human_voice_naturalness_review=NOT_RUN
human_caption_readability_review=NOT_RUN
human_sync_trust_review=NOT_RUN
human_full_watch_1x=NOT_RUN
drive_archive_verification=NOT_RUN
production_eligible=false
not_publishable=true
```

Historical PA1R provider, human and Drive receipts are not reused as CQR1-B PASS evidence. CQR1-B does not claim that voice naturalness, real-media caption comfort, paid narration, uninterrupted human watchability or archive completeness passed.

Exact next boundary: CQR1-D may run a separately approved controlled paid canary only after its entry gates, credentials/permissions, budget approval and no-retry controls pass. Until then, the completed result is **CQR1-B OFFLINE PASS** only.
