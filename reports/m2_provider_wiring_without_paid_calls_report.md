# M2 — Provider Wiring Without Paid Calls

## Kết quả
- Đã thêm provider wiring/readiness validation-only cho ElevenLabs, Luma API, Creatomate Growth 10K, Pexels API, Google Drive archive và YouTube read-only.
- Empty API keys không crash; trả blocker rõ ràng `NOT_CONFIGURED / NEEDS_CREDENTIAL`.
- Không gọi network/provider, không tạo voice/video/render, không search/download Pexels, không upload Drive/YouTube.
- Không cần migration: dùng read-model/schema/service và JSON snapshot hiện có.

## Files changed
- Config/env: `app/core/config.py`.
- Contracts/read models: `app/contracts/m2.py`, `app/contracts/__init__.py`.
- Services: `app/services/m2.py`, `app/services/m12.py`, `app/services/m12_2.py`, `app/services/r3d4.py`, `app/services/config_registry.py`, `app/services/__init__.py`.
- API/CLI: `app/main.py`, `app/cli/main.py`.
- Policy catalog: `config/pexels_policy_catalog.yaml`.
- Tests: `tests/test_m2_provider_wiring_without_paid_calls.py`.

## Env/config summary
- Parsed env mới: `VOICE_PROVIDER`, `AI_VIDEO_HERO_PROVIDER`, `CLOUD_FINAL_ASSEMBLY_RENDERER`, `CLOUD_TEMPLATE_RENDERER`, `FREE_VISUAL_FALLBACK_PROVIDER`.
- Parsed credentials/ids: `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL_ID`, `LUMA_API_KEY`, `LUMA_HERO_MODEL`, `CREATOMATE_API_KEY`, `CREATOMATE_TEMPLATE_ID`, `CREATOMATE_WORKSPACE_ID`, `PEXELS_API_KEY`.
- Parsed limits/guards: `LUMA_*DURATION*`, `LUMA_VIDEO_ONLY`, `PEXELS_MAX_*`, `PEXELS_ATTRIBUTION_REQUIRED`, `GOOGLE_DRIVE_ARCHIVE_ENABLED`, `PROVIDER_REAL_READINESS_PROBE_ENABLED`.

## Capability matrix
- ElevenLabs: `VOICE_GENERATION`, requires key/voice/model, future execution R3D8 only.
- Luma API: `AI_HERO_VIDEO`, duration 4/6/8s, max 8, video-only, future execution R3D8 only.
- Creatomate Growth 10K: `FINAL_ASSEMBLY_RENDER`, `TEMPLATE_RENDER`, `CARD_RENDER`, `THUMBNAIL_COMPOSITION`, requires key/template/workspace.
- Pexels API: `FREE_VISUAL_FALLBACK`, role/limit/attribution policy enforced.
- Google Drive: `ARCHIVE_STORAGE` only, not source of truth, no upload in M2.
- YouTube: read-only verification/analytics only, no upload.

## Readiness states
- Empty env result: `elevenlabs`, `luma_api`, `creatomate_growth_10k`, `pexels_api` => `NOT_CONFIGURED` with `NEEDS_CREDENTIAL`.
- Drive archive => `DISABLED` unless `GOOGLE_DRIVE_ARCHIVE_ENABLED=true`.
- Partial env returns exact blockers: missing voice/model/template/workspace/model/key.
- Real readiness probe flag is parsed but default false; no tests depend on network.

## Builders/adapters
- Builders: `ElevenLabsVoiceRequestBuilder`, `LumaHeroVideoRequestBuilder`, `CreatomateRenderRequestBuilder`, `PexelsSearchRequestBuilder`, `DriveArchiveRequestBuilder`.
- Adapters: `ElevenLabsVoiceAdapter`, `LumaHeroVideoAdapter`, `CreatomateFinalRendererAdapter`, `PexelsVisualFallbackAdapter`, `GoogleDriveArchiveAdapter`.
- All builders return validation result only: idempotency key, package/project/context refs, capability, cost placeholder, human approval flag, `will_execute=false`.

## Pexels policy
- Allowed: `background_visual`, `short_broll`, `thumbnail_background`, `mood_support`.
- Blocked: `factual_evidence`, `fake_testimonial`, `implied_endorsement`, `core_visual_backbone`, `recurring_host_identity`, `every_scene_default_stock`.
- Limits enforced: runtime pct, clips per long, same asset reuse per 30 days.
- Required manifest fields recorded in catalog/read model; no download/storage built.

## Boundary/preflight
- `ProviderBoundaryPreflight` blocks real call by default in M2.
- Blocks missing provider config, missing human paid approval, missing R3D8 ledger refs, Luma duration > 8, missing Creatomate template, missing ElevenLabs voice/model, Pexels role/limit violations.
- R3D4 `ProviderBoundaryGate` can read embedded M2 readiness.
- M12.2 boundary now includes M2 readiness keys and still blocks safely as `BLOCKED_PROVIDER_NOT_CONFIGURED / WAITING_PROVIDER_CONFIG` with empty keys.

## API/CLI
- API: `GET /integrations/provider-wiring`.
- Existing `GET /integrations/readiness` includes `technical_appendix.m2_provider_wiring`.
- CLI: `vcos integrations provider-wiring`.

## Proof empty keys / no calls
- Empty keys tested: app returns blockers, not fake success.
- Network sentinel + source guard confirm no HTTP/provider/media/upload/vector/RAG path in M2.
- DB assertion confirms no `ProviderAttempt` or `RealSmokeRun` during M2 boundary test.

## Tests run
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_m2_provider_wiring_without_paid_calls.py` → 10 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_m1_channel_aware_packaging_handoff.py tests/test_m2_provider_wiring_without_paid_calls.py tests/qualification/test_m12_provider_readiness.py` → 33 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d1_hierarchical_scope.py tests/test_r3d2_effective_channel_runtime_context.py tests/qualification/test_r3d3_agent_context_pack.py tests/qualification/test_r3d4_agent_output_contract_gates.py` → 58 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py tests/qualification/test_m12_2r_publish_handoff_ledger.py` → 35 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_migration.py tests/qualification/test_pre_m7_migrations.py tests/qualification/test_pre_m7_seed_idempotency.py` → 4 passed, 1 historical skip.
- `PYTHONPATH=. .venv/bin/python -m compileall -q app tests/test_m1_channel_aware_packaging_handoff.py tests/test_m2_provider_wiring_without_paid_calls.py` → pass.
- `PYTHONPATH=. .venv/bin/python -m app.cli.main integrations provider-wiring` → pass, returns blocker snapshot.
- `cd frontend && npm run typecheck` → pass.
- `cd frontend && npm run lint` → pass.
- `cd frontend && npm test` → 20 passed.
- `git diff --check` → pass.

## Follow-up R3D5/R3D8
- R3D5: richer repair/review workflow around provider-bound artifacts.
- R3D8: real paid execution ledger, RenderRevision, CostEstimate, PaidAttemptLimit, HumanPaidRenderApproval, and safe read-only readiness probes.
- Still out of scope: real ElevenLabs/Luma/Creatomate/Pexels/Drive execution and YouTube upload.
