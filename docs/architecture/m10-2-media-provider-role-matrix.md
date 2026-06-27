# M10.2 Media Provider Role Matrix

M10.2 builds the VCOS Media Provider Role Matrix for Quality-First $250 mode. It adds backend contracts, provider classifications, routing decisions, capability gates, budget gates, license evidence gates, package states, and planning APIs.

M10.2 is a routing and planning foundation only. M10.4 binds the AI hero role to Google Vertex Veo, but real execution remains guarded and disabled by default. It does not call ElevenLabs, Creatomate, Veo, or a cloud final renderer unless an explicit real smoke guard is enabled. It does not add dashboard UI, YouTube follow/sync, auto upload, auto publish, auto reupload, TikTok/Facebook analytics learning loops, Envato automation, scraping, vector/RAG, OPA/Cedar, fake traffic, bot engagement, or platform evasion.

## Production Mode

- 10 long-form videos/month.
- 30 Shorts/month.
- Long-form duration: 12-14 minutes.
- Shorts duration: 30-45 seconds, hard cap under 59 seconds.
- YouTube-first only.
- TikTok/Facebook export and analytics learning loops are out of scope.
- Envato is out of the daily production backbone.
- Local visual/video generation backbone is out of scope.

## Provider Role Matrix

| Provider | Provider type | Recommendation | Role |
| --- | --- | --- | --- |
| VCOS Backend | `WORKFLOW_ORCHESTRATOR` | `CORE` | Orchestration, state, manifest, budget, QC, approval workflow, publish package. |
| Existing LLM source / LLMRouter | `LLM_SCRIPT_ENGINE` | `CORE` | Script/planning language tasks through guarded M10.1 router contracts. |
| ElevenLabs Flash/Turbo | `API_NATIVE_TTS` | `CORE_QUALITY_LAYER` | Voice generation only. |
| VCOS caption timeline service | `CAPTION_TIMELINE_ENGINE` | `CORE` | Caption timing and caption track planning. |
| Google Vertex AI - Veo 3.1 Fast video-only 1080p | `AI_VIDEO_HERO_PROVIDER` | `CORE_QUALITY_LAYER` | Premium opening hook, key metaphor, and thumbnail still-frame source only. |
| Creatomate Essential 2K | `CLOUD_TEMPLATE_RENDERER_LIGHT` | `CORE_LIGHT_RENDER` | Light template renderer for Shorts, cards, thumbnails, lower thirds, and hero composition. |
| TBD cloud renderer | `CLOUD_FINAL_ASSEMBLY_RENDERER` | `REQUIRED_GAP` | Required gap for full long-form final MP4 assembly. |
| VCOS storage/object storage | `MEDIA_STORAGE` | `CORE` | Object refs and durable media storage references. |
| VCOS MediaQC | `MEDIA_QC_ENGINE` | `CORE` | Correctness/QC gate, integrated with M6 MediaQC. |
| VCOS publish handoff | `PUBLISH_PACKAGE_BUILDER` | `CORE` | Manual publish handoff package. |
| Paid stock providers | `API_NATIVE_STOCK_PROVIDER` | `DEFERRED` | Deferred; not part of daily backbone. |
| Pexels/Pixabay/free fallback | `FREE_FALLBACK_PROVIDER` | `FALLBACK` | Fallback only, license evidence required. |
| Envato/manual stock | `DEFERRED_MANUAL_LIBRARY` | `DEFERRED` | Manual library, not automated provider or daily backbone. |
| Mock provider | `MOCK_PROVIDER` | `MOCK` | Tests/dev only. |

## Hard Role Boundaries

Creatomate Essential 2K may render thumbnails, thumbnail variants, Shorts final renders, title cards, intro/outro cards, section cards, workflow diagrams, statistic cards, lower thirds, caption burn-in, AI hero compositions, and 9:16/16:9/1:1 template exports.

Creatomate Essential 2K must not generate AI hero footage, generate voice, buy/license stock, decide editorial content, run YouTube analytics, act as the long-form full render backbone, or act as permanent storage/archive.

Google Vertex Veo may create opening hook visuals, key metaphor scenes, high-impact background clips, and still frames for thumbnails. It does not render full edited video, create accurate workflow/data/diagram cards, manage captions, guarantee final brand layout, handle final publish packages, or generate separate AI hero clips for every Short by default.

ElevenLabs may create long narration voice, short narration voice, voice segments, and voice usage metadata. It does not write scripts, render video, manage captions, license stock/source material, or build publish packages.

VCOS owns orchestration, artifact state, provider routing, budget checks, manifests, QC, approval workflow, and publish handoff packages. VCOS must not fake external provider outputs, bypass human approval, or auto publish/upload/reupload.

## Critical Invariant

`LONG_FORM_FINAL_RENDER` requires a configured `CLOUD_FINAL_ASSEMBLY_RENDERER`.

If only Creatomate Essential 2K is available, routing returns `BLOCKED_PROVIDER_CAPABILITY_REQUIRED` with `CREATOMATE_ESSENTIAL_NOT_FINAL_RENDERER` and `CLOUD_FINAL_RENDERER_REQUIRED`. VCOS must not claim a final long-form MP4 was generated.

Creatomate may route `LONG_FORM_FINAL_RENDER` only when a Growth 10K-or-higher profile is explicitly configured as a final renderer and its capability entry is `SUPPORTED`.

## Job Routing

- `THUMBNAIL_RENDER`, `SHORT_RENDER`, `TITLE_CARD_RENDER`, `DIAGRAM_CARD_RENDER`, `STAT_CARD_RENDER`, `LOWER_THIRD_RENDER`, `HERO_COMPOSITION_RENDER`, `PREVIEW_CLIP_RENDER` route to Creatomate Essential 2K.
- `AI_HERO_GENERATION`, `AI_METAPHOR_GENERATION` route only to `GOOGLE_VERTEX_VEO`.
- `VOICE_GENERATION`, `LONG_VOICE_GENERATION`, `SHORT_VOICE_GENERATION` route to ElevenLabs.
- `LONG_FORM_FINAL_RENDER` routes only to a configured cloud final assembly renderer or explicit upgraded Creatomate final renderer.
- Unknown jobs return `BLOCKED_UNKNOWN_PROVIDER`.

## Budget Assumptions

Configured assumptions for Quality-First $250 mode:

- ElevenLabs Creator is the baseline starting plan; track voice budget by credits/characters where possible.
- Creatomate Essential 2K: $59/month assumption.
- Google Vertex Veo: $0.10/second configured for Veo 3.1 Fast 1080p video-only.
- Default 8s Veo attempt estimate: $0.80.
- Monthly AI hero cap: $175 by default.
- ElevenLabs Pro is an upgrade path if Creator credits become tight.
- Stock: $0 core.
- Music/SFX subscription: $0 core.

M10.2 does not invent provider usage prices when provider usage or price is unavailable. Budget gates use configured caps and supplied estimates only.

## Workload Allocation

Creatomate Essential 2K allocation:

- Shorts final renders: 30/month.
- Thumbnail variants: 30-50/month.
- Title cards: 10-20/month.
- Diagram/stat cards: 20-40/month.
- Hero compositions: 10-20/month.
- Preview clips: limited.
- Not allocated to 10 full long-form renders, large retry loops, full-length preview renders, or random filler video scenes.

AI Hero allocation:

- Opening hero clips: 10/month.
- Optional metaphor clips: 5-10/month.
- Retry/variants: budget-dependent.
- Shorts-specific hero: 0 default; reuse long-form hero.
- Thumbnail background: still frame from the Veo clip.
- Workflow/data/diagram sections: Creatomate/cards, not Veo.

## Gates

- `ProviderCapabilityGate` blocks long-form final render without a capable final renderer, blocks Creatomate Essential 2K as final long-form renderer, and blocks unknown provider/job pairs.
- `BudgetGate` uses configured media provider budget policies and supplied estimates only.
- `LicenseEvidenceGate` blocks stock/free/manual assets without confirmed license evidence.
- `ReusedContentRiskGate` flags template-only or weakly original reuse for review.
- `MediaQCGate` delegates to M6 MediaQC when a report exists and blocks missing files, bad duration/aspect ratio, missing audio, unreadable captions, or black frames.
- `HumanApprovalGate` remains required before publishing long-form and Shorts. M10.2 does not build dashboard approval UI.
- `YouTubeOnlyAnalyticsGate` keeps YouTube analytics as the only learning authority in this mode.

## Durable Runtime Tables

M10.2 adds `media_provider_role_profiles`, `provider_capability_matrix_entries`, `media_render_routing_decisions`, `media_provider_budget_policies`, `media_provider_budget_snapshots`, `long_form_render_packages`, `short_render_packages`, `ai_hero_assets`, `creatomate_render_assets`, `thumbnail_variants`, `final_media_refs`, and `license_evidence_records`.

## Deferred

- M10.3: YouTube Public + Owner Analytics Follow Patch, now complete.
- M10.4: Google Vertex Veo AI Hero Provider Binding and config externalization audit, now complete.
- M11: dashboard/operator cockpit, approvals, upload task dashboard, derivative graph dashboard, learning promotion UX, and human-owned channel config editing.
