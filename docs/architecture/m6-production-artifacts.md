# M6 Production Artifacts, Render Spec, And Local Media QC

## Scope

M6 consumes an M5-admitted VideoProject with `creative_brief`, `research_pack`, and `source_pack` draft artifacts. It creates production-ready draft artifacts and runtime snapshots:

- script draft
- voice timeline
- caption track and deterministic SRT
- visual plan
- scene manifest with source decision metadata
- asset manifest and source manifest
- RenderSpec with platform render variants
- local/mock render job and render package
- MediaQC and AccessibilityQC reports

M6 is mock/local only. It uses `MockLLMProvider` for script drafting and local FFmpeg only for dummy MP4 smoke when available.

## Non-Scope

M6 does not build publish/upload/manual publish handoff, analytics, dashboard, memory promotion, real provider integrations, Envato API/download/generation, asset marketplace scraping, source scraping/parser, vector/RAG engine, OPA/Cedar, or Algorithm/Growth/View agents.

## Voice As Master

`VoiceTimelineContract` is the master timing contract. Captions, scenes, scene manifests, and RenderSpec scenes are derived from narration segment timing. Every scene carries `start_time`, `end_time`, and `narration_segment_id`. Overlaps are rejected, and gaps must be explicit.

## Contracts

M6 contracts live in `app/contracts/m6.py`.

LLM-derived script output must validate as `ScriptDraftContract` before any script ArtifactVersion is created. The deterministic compiler owns narration segment ids and timing.

`CaptionTrackContract` exports deterministic SRT. Every cue references an existing narration segment and must have valid non-overlapping timing.

`VisualPlanContract` and `SceneManifestContract` keep scene timing aligned to the voice timeline. Source decisions are deterministic metadata only.

`RenderSpecContract` validates before any render job is created. Bad RenderSpec data fails safely and creates no render job.

## Source And Rights

`AssetManifestContract` and `SourceManifestContract` represent requirements, candidates, source class, provenance, and rights envelope. Local fixtures are `INTERNAL_TEST_ONLY`. Envato is represented only as manual placeholder metadata; no Envato API, download, generation, or scraping exists in M6.

## Render Variants

M6 uses one master project and one master voice timeline. Platform-specific output is represented as `RenderVariantSpec`. The default smoke variant is 16:9 YouTube long-form MP4. M6 does not create publish packages.

## Local Renderer

`LocalFixtureRendererService` uses `shutil.which("ffmpeg")` and `shutil.which("ffprobe")`. If either is missing, the render job is `BLOCKED` with `FFMPEG_UNAVAILABLE` or `FFPROBE_UNAVAILABLE`; it does not fake a pass.

When available, FFmpeg generates a simple local dummy MP4 with silent audio and SRT sidecar under `var/generated/` or a supplied output directory. Generated binaries are not committed.

## QC

`MediaQCService` checks file existence, nonzero size, checksum, duration tolerance, scene coverage, caption alignment metadata, explicit silent/mock audio, manifest presence, and render variant validity.

`AccessibilityQCService` checks caption presence, basic caption readability, safe-area placeholder metadata, flashing-risk placeholder metadata, disclosure placement placeholder metadata, and pronunciation placeholder metadata.

QC checks correctness and contract integrity only. It does not judge voice quality, visual quality, thumbnail appeal, CTR, retention, reach, or monetization.

## Pre-M7

M6 creates enough evidence for a later Pre-M7 M0-M6 Qualification Gate. The gate itself is a separate milestone after M6 review/commit/tag.
