# M12.1R Mock/Dry-run Purge Report

## Verdict

PASS.

Runtime đã chuyển sang real-provider-or-blocked. Mock/local fixture không còn là đường production success qua API/CLI/runtime catalog.

## Repo path

`/Users/sangss/Desktop/video-creator-rag`

## Preflight

- Repo path đúng.
- Git status ban đầu sạch; sau triển khai dirty tree chỉ gồm thay đổi M12.1R liên quan.
- Đã đọc report M12/M12.1 và docs provider/readiness/source-of-truth.
- Không gọi real provider, không chạy old real-smoke/provider-smoke, không upload/publish/reupload.

## Files changed

- Runtime/API/CLI: `app/main.py`, `app/cli/main.py`, `app/providers/*`, `app/services/{m5,m6,m8,m10_2,m12,ops,m12_1r}.py`.
- Contracts/models: `app/contracts/{m5,m6,m8,m10,m10_1,m10_2,ops}.py`, `app/db/models/{m5,m6}.py`.
- Catalogs: provider registry, media/provider capability, source/render/search/analytics/retry/reason-code catalogs.
- Tests: new `tests/fakes/`, new `tests/test_m12_1r_mock_runtime_purge.py`, updated readiness/M4 tests, historical mock/local-render qualification tests marked skipped with explicit M12.1R reason.

## Runtime mock removal

- `app.providers.mock` no longer exposes runtime providers; compatibility stub raises: `Runtime mock providers were removed from production. Use tests/fakes only.`
- Test doubles moved to `tests/fakes/providers.py`.
- Production app modules no longer import mock provider classes.
- Provider registry seed/call mock paths fail-fast with no mutation.

## API endpoint removal

- Removed `/providers/seed-mocks`.
- Removed `/provider-attempts/mock`.
- Removed `/render-jobs/local-smoke`.
- OpenAPI guard test confirms removed routes are not exposed.

## CLI removal

- Removed mock provider seed/attempt commands from CLI help.
- Removed daily/analytics mock flags.
- Added `vcos data purge-mock-runtime --dry-run|--apply`.

## M5 changes

- Default run mode is `REAL_DISABLED`, not `MOCK`.
- Mock LLM authority path removed.
- Missing real LLM/router config returns `BLOCKED` / `LLM_PROVIDER_NOT_CONFIGURED` / `HUMAN_ACTION_REQUIRED`.
- DB snapshot writes use `REAL_DISABLED` to stay compatible with current constraints.

## M6 changes

- Mock LLM/local fixture production-success path removed from production execute.
- Production execute now blocks on final renderer/provider readiness gaps.
- Final renderer target is `Creatomate Growth 10K`.
- Local FFmpeg remains only as internal read/QC utility; create/execute production local-smoke entrypoints are removed.

## M8 changes

- Analytics default is `YOUTUBE_OWNER_ANALYTICS`, not `MOCK`.
- Mock analytics provider path removed.
- Missing analytics credentials blocks with `ANALYTICS_PROVIDER_NOT_CONFIGURED`.
- Manual/CSV import path preserved.

## Provider catalog

Active runtime catalog is real-only:

- `ollama`
- `youtube-public`
- `youtube-owner`
- `google-drive`
- `google-vertex-veo`
- `elevenlabs`
- `creatomate`
- `cloud-final-renderer`

No `mock_*`, `MOCK_PROVIDER`, `LOCAL_FIXTURE`, or `LOCAL_FFMPEG` provider/catalog entries remain in runtime config.

## Readiness/dashboard

- Readiness excludes mock providers.
- Cloud Final Renderer is `Cloud Final Renderer = Creatomate Growth 10K`.
- If Creatomate plan/key missing: `NOT_CONFIGURED/BLOCKED`.
- If configured: readiness may pass, smoke still `SKIPPED` with no render.
- Dashboard/readiness must not offer mock fallback.

## DB purge

Command run:

```bash
PYTHONPATH=. .venv/bin/python -m app.cli.main data purge-mock-runtime --dry-run
PYTHONPATH=. .venv/bin/python -m app.cli.main data purge-mock-runtime --apply
```

Before:

```json
{"active_mock_providers":6,"mock_provider_attempts":1,"mock_llm_snapshots":1,"mock_channel_daily_runs":0,"mock_production_runs":1,"local_ffmpeg_render_jobs":1,"mock_analytics_runs":0}
```

After:

```json
{"active_mock_providers":0,"mock_provider_attempts":0,"mock_llm_snapshots":0,"mock_channel_daily_runs":0,"mock_production_runs":0,"local_ffmpeg_render_jobs":0,"mock_analytics_runs":0}
```

Second apply was idempotent with all counts still 0. Audit-linked append-only rows were quarantined, not hard-deleted, to preserve FK integrity.

## Tests run

```bash
PYTHONPATH=. .venv/bin/python -m compileall app
PYTHONPATH=. .venv/bin/pytest -q tests/test_m12_1r_mock_runtime_purge.py tests/qualification/test_m12_provider_readiness.py tests/test_m4_ops_foundation.py tests/qualification/test_pre_m7_m4_ops.py
PYTHONPATH=. .venv/bin/pytest -q tests -k "mock or provider or readiness or m5 or m6 or m8 or cli or m12_1"
git diff --check
```

Results:

- Compile: PASS.
- Focused suite: `32 passed, 1 warning`.
- Suggested `-k` suite: `49 passed, 51 skipped, 153 deselected, 1 warning`.
- `git diff --check`: PASS.

Skipped tests are historical pre-M12 mock/local-render qualification contracts; each skip has an explicit reason and points to M12.1R cutover coverage.

## Old smoke rule

Old real/provider smoke tests were not run. No real provider calls were made.

## Scope not built

- No YouTube upload API.
- No publish/reupload automation.
- No channel config mutation.
- No auto-promote learning.
- No real Creatomate/ElevenLabs/Veo execution.
- No commit/tag.

## Risks / limitations

- Some historical qualification suites are now skipped because they validated removed mock/local-render success behavior.
- `LocalFixtureRendererService` still exists for internal read/QC compatibility, but no API/CLI production create/execute path remains.
- DB enum/check constraints still reflect some older values; runtime writes use allowed safe values and reason codes carry the new blocked semantics.

## Next

M12.2 Production Prompt Activation / First Real Scripted Video Dry-run.
