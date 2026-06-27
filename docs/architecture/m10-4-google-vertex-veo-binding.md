# M10.4 Google Vertex Veo Binding

M10.4 binds the M10.2 `AI_VIDEO_HERO_PROVIDER` role to one concrete provider: `GOOGLE_VERTEX_VEO`.

## Provider Binding

- Provider type: `AI_VIDEO_HERO_PROVIDER`.
- Provider key: `GOOGLE_VERTEX_VEO`.
- Provider name: Google Vertex AI - Veo 3.1 Fast video-only 1080p.
- Model id: `veo-3.1-fast-generate-001`.
- Mode: `video_only`.
- Resolution: `1080p`.
- Allowed durations: `[4, 6, 8]` seconds.
- Default duration: 8 seconds.
- Max duration: 8 seconds.
- Audio: false.
- Backup provider: none.

No Runway, Luma, generic cinematic AI fallback, web-app-only provider, or auto-route alternative is configured.

## Usage Policy

- Opening hook: Veo.
- Key metaphor: Veo.
- Thumbnail background: still frame from Veo clip.
- Shorts: reuse/crop long-form hero by default.
- Workflow/data/diagram visuals: Creatomate/cards, not Veo.
- Per-Short AI hero generation: off by default.

## Cost Policy

- Cost assumption: $0.10/second for Veo 3.1 Fast 1080p video-only.
- Default 8s attempt estimate: $0.80.
- Monthly AI hero cap: $175 by default.
- Cost is loaded from config/env, not service logic.
- If cost config is missing, budget gate returns `UNKNOWN`/`REVIEW_REQUIRED` instead of inventing price.

## Config Placement

Env/secret-store only:

- `GOOGLE_CLOUD_PROJECT_ID`
- `GOOGLE_CLOUD_LOCATION`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `VCOS_VEO_REAL_EXECUTION_ENABLED`
- `VCOS_VEO_REAL_SMOKE`

Config registry/catalog defaults:

- `media_provider_role_profile_catalog`
- `media_provider_capability_matrix_catalog`
- `media_provider_budget_policy_catalog`
- `media_provider_routing_policy_catalog`
- Veo model, mode, resolution, duration, audio, cost, monthly budget, and usage policy defaults.

Env override support:

- `VCOS_AI_HERO_PROVIDER`
- `VCOS_VEO_MODEL_ID`
- `VCOS_VEO_MODEL` as an optional backward-compatible alias only.
- `VCOS_VEO_MODE`
- `VCOS_VEO_RESOLUTION`
- `VCOS_VEO_AUDIO_ENABLED`
- `VCOS_VEO_DEFAULT_DURATION_SECONDS`
- `VCOS_VEO_MAX_DURATION_SECONDS`
- `VCOS_VEO_COST_PER_SECOND_1080P_VIDEO_ONLY`
- `VCOS_VEO_MONTHLY_CAP_USD`
- `VCOS_VEO_MONTHLY_BUDGET_USD` as a backward-compatible alias only.

Service account JSON must never be committed, stored in DB, or printed. `GOOGLE_APPLICATION_CREDENTIALS` is a path/handle only.

## Runtime Behavior

`AI_HERO_GENERATION` and `AI_METAPHOR_GENERATION` route to `GOOGLE_VERTEX_VEO`.

`AIHeroAssetPlanningService` creates provider-ready asset records with configured duration and route/cost checks. `AIHeroGenerationService` returns provider-ready state while real execution is disabled. It attempts real Veo smoke only when both `VCOS_VEO_REAL_EXECUTION_ENABLED=true` and `VCOS_VEO_REAL_SMOKE=true`.

The deprecated preview model id and vague model name are blocked from final resolved config. Duration 10 is blocked; only 4, 6, and 8 seconds are allowed.

The Google Vertex Veo adapter does not persist raw Google responses, tokens, credentials, or service account JSON.

## Non-Scope

M10.4 does not build dashboard UI, final long-form renderer, Creatomate real integration, ElevenLabs real integration, YouTube sync, upload/publish APIs, full long-form final video generation, channel config mutation, backup AI hero provider routing, Runway/Luma fallback, fake traffic, bot engagement, or platform evasion.
