# HPR1 — Full Luma Physical Removal and Google Veo Replacement

Date: 2026-07-12. Scope: repository-wide physical removal plus local-fixture-only Veo foundation. PA1R was not run.

## Verdict

```text
HPR1_INVENTORY=PASS
LUMA_RUNTIME_REMOVAL=PASS
LUMA_SETTINGS_REMOVAL=PASS
LUMA_API_SCHEMA_REMOVAL=PASS
LUMA_DATABASE_REMOVAL=PASS
LUMA_DOC_CURRENT_TREE_REMOVAL=PASS
LUMA_TREE_SCAN=PASS
UNAPPROVED_LUMA_REFERENCE_COUNT=0
GOOGLE_VEO_PROVIDER_FOUNDATION=PASS
GOOGLE_VEO_COST_GUARDS=PASS
GOOGLE_VEO_LOCAL_FIXTURE_REHEARSAL=PASS
EXTERNAL_AI_VIDEO_FALLBACKS=NONE
HPR1_FINAL=PASS
PROCEED_TO_PA1R=true
```

## Inventory and removal

Pre-removal inventory recorded 438 matching lines across Settings/env, provider registries, capability/routing/cost/readiness, AS1 contracts/services, prompts, tests, frontend read models, docs, reports, scripts and local fixture paths. Full entries are in `reports/hpr1_google_veo_replacement_inventory.json`.

Dedicated obsolete files removed:

- `app/providers/google_vertex_veo.py`;
- `tests/test_m10_4_veo_real_smoke.py`;
- `tests/qualification/test_m10_4_google_vertex_veo.py`;
- `docs/architecture/m10-4-google-vertex-veo-binding.md`;
- `reports/m10_4-final-report.md`;
- stale generated package metadata and provider-named fixture folder.

Mixed runtime/config/tests/prompts/docs/reports/frontend files were rewritten to `google_veo` or provider-neutral `AI_HERO` semantics as appropriate. No Luma alias, disabled flag, fallback, compatibility shim, Ray model, secret field or filename remains.

Local ignored `.env` cleanup removed obsolete lines without reading or printing values. The only Gemini video credential is `GEMINI_API_KEY`; readiness exposes only configured/not-configured and redaction booleans.

## Domain and provider architecture

`CompiledAssetRequestPlan` now uses `native_request_count`, `supporting_stock_request_count`, `ai_hero_request_count`, and `unresolved_request_count`. `AIHeroAssetRequest` and `AIGenerationManifest` are provider-neutral. Frozen policy resolves `AI_HERO` to `GoogleVeoGenerationRequest` and `GoogleVeoAdapter`.

Canonical external media providers are `elevenlabs`, `pexels_api`, and `google_veo`; NativeFFmpeg is local final assembly, Drive is archive-only, and YouTube is manual publish. `external_ai_video_provider_order=[google_veo]`. Unavailable routing is explicit native/review/block with no external failover.

The adapter uses the official Google Gen AI SDK boundary with `GEMINI_API_NATIVE`. It models build, guarded submit, bounded polling, parsing, volatile output reference, download planning and fixture download. Async states and terminal failures are explicit. Internal idempotency binds provider/model/prompt/references/duration/resolution/aspect/output/project/scene/approval scope. Duplicate active/completed submission returns the same operation; polling/download never consume a generation attempt.

Real execution requires both flags plus provider boundary, human approval, cost snapshot, monthly budget, attempt limit, idempotency and global/provider kill switches. Defaults are false. No generation action endpoint or dashboard button was added.

## Model, price and audio policy

`config/google_veo_model_price_catalog.yaml` version `2026-07-12` contains approved Veo 3.1 model/resolution rows. The PA1R default is `veo-3.1-fast-generate-preview`, 8 seconds, 720p, 16:9, one output. Price is 0.10 USD/second and fixture estimate is 0.80 USD; actual cost remains null.

Provider audio presence and stream metadata are recorded. Current policy is `DISCARD`; MediaNormalizer compiles `-an`, expected normalized audio-stream presence is false, ElevenLabs remains narration authority and NativeFFmpeg remains final-mix authority.

## Database

Source migrations and SQLAlchemy metadata contained zero provider-specific tables, columns, enums, constraints or indexes. Live DB inspection found 246 obsolete AI-video readiness rows and 82 readiness snapshot payloads, all non-execution evidence. Forward-only revision `0036_hpr1_veo` removed the rows and filtered snapshot items without relabeling them as Veo. No generation, cost, approval, idempotency, attempt or paid-call history was rewritten.

```text
LUMA_SCHEMA_OBJECTS_FOUND=0
VEO_SCHEMA_OBJECTS_ADDED=0
HPR1_SCHEMA_MIGRATION_ADDED=true
ALEMBIC_HEAD=0036_hpr1_veo
POST_MIGRATION_OBSOLETE_DIRECT_ROWS=0
POST_MIGRATION_OBSOLETE_SNAPSHOT_JSON_HITS=0
```

## Local fixture rehearsal

Evidence: `var/tmp/hpr1-google-veo-fixture/`.

The rehearsal resolved one approved Strategy B `AIHeroAssetRequest`, validated Fast/720p/16:9/8s/output-one, estimated 0.80 USD, validated approval/idempotency gates, performed fake submit and two fake polls, parsed fake success, converted a raw fixture URL to a volatile reference, copied a local MP4 and computed SHA-256, built Veo provenance, compiled provider-audio removal, completed all archive roles and prevented duplicate submit.

```text
transport=LOCAL_FIXTURE_ONLY
provider_call_made=false
generation_attempts_consumed=0
actual_cost_usd=null
production_eligible=false
duplicate_submit_prevented=true
archive_roles_complete=true
```

This is not a real Veo success claim.

## Verification and no-execution proof

- focused backend suite: 93 passed;
- migration suite: 2 passed;
- frontend typecheck: PASS;
- frontend Vitest: 35 passed;
- frontend production build: PASS;
- `compileall`: PASS;
- `git diff --check`: PASS;
- OpenAPI unapproved references: 0; Veo mutation/action paths: 0;
- SQLAlchemy provider-specific schema objects: 0;
- final current-tree content scan: ripgrep exit 1 with empty stdout;
- filesystem and tracked filename scans: empty.

No Gemini/Veo, Pexels, ElevenLabs, Drive or YouTube call occurred. No PA1R, production render, FinalMediaRef, HumanUploadTask, submitted ProviderJobSnapshot, executed PaidProviderCallLedger, frozen channel/profile/effective-context/FormatIdentity mutation, learning promotion or prompt self-mutation occurred.

Exact blockers: none. Recommendation: HPR1 permits a separately authorized PA1R, but both Veo execution flags must remain false until that approval exists.
