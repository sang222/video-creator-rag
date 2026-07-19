# M10.2 Media Provider Role Matrix

M10.2 builds the VCOS Media Provider Role Matrix for Quality-First $250 mode. It adds backend contracts, provider classifications, routing decisions, capability gates, budget gates, license evidence gates, package states, and planning APIs.


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
| NativeFFmpegRenderer | `LOCAL_RENDERER_CAPABILITY` | `CORE` | Local compiled final MP4 assembly authority. |
| VCOS storage/object storage | `MEDIA_STORAGE` | `CORE` | Object refs and durable media storage references. |
| VCOS MediaQC | `MEDIA_QC_ENGINE` | `CORE` | Correctness/QC gate, integrated with M6 MediaQC. |
| VCOS publish handoff | `PUBLISH_PACKAGE_BUILDER` | `CORE` | Manual publish handoff package. |
| Paid stock providers | `API_NATIVE_STOCK_PROVIDER` | `DEFERRED` | Deferred; not part of daily backbone. |
| Pexels/Pixabay/free fallback | `FREE_FALLBACK_PROVIDER` | `FALLBACK` | Fallback only, license evidence required. |
| Envato/manual stock | `DEFERRED_MANUAL_LIBRARY` | `DEFERRED` | Manual library, not automated provider or daily backbone. |
| Mock provider | `MOCK_PROVIDER` | `MOCK` | Tests/dev only. |

## Hard Role Boundaries



Google Veo may create opening hook visuals, key metaphor scenes, high-impact background clips, and still frames for thumbnails. It does not render full edited video, create accurate workflow/data/diagram cards, manage captions, guarantee final brand layout, handle final publish packages, or generate separate AI hero clips for every Short by default.

ElevenLabs may create long narration voice, short narration voice, voice segments, and voice usage metadata. It does not write scripts, render video, manage captions, license stock/source material, or build publish packages.

VCOS owns orchestration, artifact state, provider routing, budget checks, manifests, QC, approval workflow, and publish handoff packages. VCOS must not fake external provider outputs, bypass human approval, or auto publish/upload/reupload.

## Critical Invariant

`LONG_FORM_FINAL_RENDER` requires an approved NativeRenderPlan and the local NativeFFmpeg boundary.

LPRO1 tightens this invariant with a strict package projection. A provider route
decision is planning evidence only. Legacy packages that lack approved script,
exact package review, final narration/alignment, canonical timeline, captions,
per-scene source decisions, resolved normalized assets, frozen policy/provider/
cost authorities, rights and idempotency evidence are readable but cannot be
reported as `READY_FOR_FINAL_RENDER`.

## Render Readiness States

| State | Exact meaning |
| --- | --- |
| `PACKAGE_READY_FOR_HUMAN_REVIEW` | Scripted/no-media package awaits exact human review. |
| `PACKAGE_HUMAN_REVIEW_PASSED` | The reviewed package version has a completed ReviewTask and matching PASS decision. |
| `READY_FOR_LONG_PRODUCTION` | Frozen package/context/niche/provider-cost lineage may enter LPRO1. No MP4 is implied. |
| `ROUTED_AWAITING_MEDIA` | Provider routing exists, but one or more strict media/render authorities are absent. |
| `NATIVE_RENDER_PLAN_READY` | A checksum-bound native plan exists. Render completion is not implied. |
| `READY_FOR_HUMAN_REVIEW` | Actual MP4 exists and actual-byte TechnicalMediaQC passed; creative/human review remains separate. |
| `UPLOAD_INPUT_MISSING` | Upload metadata may exist, but exact final media bytes are absent. |
| `UPLOAD_READY` | Exact final file/checksum and matching human PASS are present. |

No API or read model may collapse these states.

## FinalMediaRef Lifecycle

An LPRO1 `ReviewMediaCandidate` is never a production `FinalMediaRef`. Production
closeout requires the exact candidate bytes and SHA-256, TechnicalMediaQC PASS,
accepted creative evidence, exact human PASS on the same package/plan/output,
current package and plan lineage, complete asset rights/provenance, and verified
archive evidence when archive is required. Any missing or stale authority fails
closed. `FinalMediaRef` creation does not itself create an upload task or grant
Drive/YouTube write authority.



## Job Routing

- `AI_HERO_GENERATION`, `AI_METAPHOR_GENERATION` route only to `GOOGLE_VEO`.
- `VOICE_GENERATION`, `LONG_VOICE_GENERATION`, `SHORT_VOICE_GENERATION` route to ElevenLabs.
- Unknown jobs return `BLOCKED_UNKNOWN_PROVIDER`.

## Budget Assumptions

Configured assumptions for Quality-First $250 mode:

- ElevenLabs Creator is the baseline starting plan; track voice budget by credits/characters where possible.
- Google Veo: $0.10/second configured for Veo 3.1 Fast 1080p video-only.
- Default 8s Veo attempt estimate: $0.80.
- Monthly AI hero cap: $175 by default.
- ElevenLabs Pro is an upgrade path if Creator credits become tight.
- Stock: $0 core.
- Music/SFX subscription: $0 core.

M10.2 does not invent provider usage prices when provider usage or price is unavailable. Budget gates use configured caps and supplied estimates only.

## Workload Allocation


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

## Gates

- `BudgetGate` uses configured media provider budget policies and supplied estimates only.
- `LicenseEvidenceGate` blocks stock/free/manual assets without confirmed license evidence.
- `ReusedContentRiskGate` flags template-only or weakly original reuse for review.
- `MediaQCGate` delegates to M6 MediaQC when a report exists and blocks missing files, bad duration/aspect ratio, missing audio, unreadable captions, or black frames.
- `HumanApprovalGate` remains required before publishing long-form and Shorts. M10.2 does not build dashboard approval UI.
- `YouTubeOnlyAnalyticsGate` keeps YouTube analytics as the only learning authority in this mode.

## Durable Runtime Tables


## Deferred

- M10.3: YouTube Public + Owner Analytics Follow Patch, now complete.
- M10.4: Google Veo AI Hero Provider Binding and config externalization audit, now complete.
- M11: dashboard/operator cockpit, approvals, upload task dashboard, derivative graph dashboard, learning promotion UX, and human-owned channel config editing.
