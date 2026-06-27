# M10.4 Final Report

## Verdict

PASS, có ghi chú.

M10.4 đã bind `AI_VIDEO_HERO_PROVIDER` sang `GOOGLE_VERTEX_VEO` và externalize cấu hình provider/media đúng scope. Không commit/tag.

## Repo path

`/Users/sangss/Desktop/video-creator-rag`

## Preflight status

PASS.

- Working tree sạch trước khi mở M10.4: PASS.
- Tag `m10-3-youtube-follow` tồn tại: PASS.
- `reports/m10_3-final-report.md` tồn tại: PASS.
- Đã đọc M10.2/M10.3 docs + final reports làm source of truth.
- Working tree hiện dirty là expected vì chứa thay đổi M10.4; chưa commit/tag.

## Migration status

PASS.

- Không thêm schema migration mới.
- Alembic head hiện vẫn là M10.3: `0014_m10_3_youtube_follow`.
- M10.4 chỉ thêm config/catalog/service/API/CLI/test/doc binding.

## Config seed status

PASS.

- Thêm 4 config catalogs:
  - `media_provider_role_profile_catalog`
  - `media_provider_capability_matrix_catalog`
  - `media_provider_budget_policy_catalog`
  - `media_provider_routing_policy_catalog`
- Tổng catalog hiện tại: 137.
- `PYTHONPATH=/Users/sangss/Desktop/video-creator-rag .venv/bin/vcos config seed`: PASS, 137 catalogs.
- Lưu ý local: plain `.venv/bin/vcos` đang import package đã install trong `.venv`; cần `PYTHONPATH`, `python -m app.cli.main`, hoặc reinstall editable để console script thấy source chưa commit.

## Provider binding

PASS.

- Provider type: `AI_VIDEO_HERO_PROVIDER`.
- Provider key duy nhất: `GOOGLE_VERTEX_VEO`.
- Model id: `veo-3.1-fast-generate-001`.
- Mode: `video_only`.
- Resolution: `1080p`.
- Default duration: 8s.
- Allowed durations: `[4, 6, 8]`.
- Max duration: 8s.
- Audio: false.
- Cost: `$0.10`/second.
- Default 8s attempt estimate: `$0.80`.
- Monthly cap: `$175`.
- Không cấu hình Runway, Luma, generic cinematic fallback, web-app-only provider, hoặc backup/alternative auto-route.

## Runtime behavior

PASS.

- `AI_HERO_GENERATION` và `AI_METAPHOR_GENERATION` route về `GOOGLE_VERTEX_VEO`.
- Opening hook/key metaphor dùng Veo.
- Thumbnail background dùng still frame từ Veo clip.
- Shorts mặc định reuse/crop long-form hero asset.
- Workflow/data/diagram visuals route Creatomate/cards, không dùng Veo.
- `AIHeroGenerationService` chỉ attempt real provider khi cả `VCOS_VEO_REAL_EXECUTION_ENABLED=true` và `VCOS_VEO_REAL_SMOKE=true`.
- Default real execution disabled trả `READY_FOR_PROVIDER` với reason `VEO_REAL_EXECUTION_DISABLED`.

## Env/config split

PASS.

- Env/secret-store only: `GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS`, `VCOS_VEO_REAL_EXECUTION_ENABLED`, `VCOS_VEO_REAL_SMOKE`.
- Env overrides: `VCOS_AI_HERO_PROVIDER`, `VCOS_VEO_MODEL_ID`, optional legacy alias `VCOS_VEO_MODEL`, `VCOS_VEO_MODE`, `VCOS_VEO_RESOLUTION`, `VCOS_VEO_AUDIO_ENABLED`, `VCOS_VEO_DEFAULT_DURATION_SECONDS`, `VCOS_VEO_MAX_DURATION_SECONDS`, `VCOS_VEO_COST_PER_SECOND_1080P_VIDEO_ONLY`, `VCOS_VEO_MONTHLY_BUDGET_USD`.
- Config registry/catalog: provider role profile, capability matrix, routing policy, budget policy, model/mode/resolution/duration/audio/cost/monthly budget/usage policy defaults.
- Service account JSON không commit, không DB, không log; `GOOGLE_APPLICATION_CREDENTIALS` chỉ là path/handle.

## Config hardcode audit

PASS, phân loại như sau.

- Moved to env: Google project/location/credential path, Veo real execution flags, provider API keys, YouTube OAuth/client paths, DB URL, Veo env overrides.
- Moved to config registry/catalog: provider role profiles, provider capability matrix, routing policy, budget policy, Veo model/mode/resolution/duration/audio/cost/monthly cap/usage policy.
- Kept as catalog/enum/contract with reason: provider/job/status/reason-code literals, route result literals, provider class identity `GOOGLE_VERTEX_VEO`.
- Test fixture only: injected Runway/Luma/cinematic fallback rows in M10.4 tests, `/absolute/path` placeholders, mock provider keys, fixture secret markers.
- Safe constants: local dev `http://localhost:11434` default/Makefile check, fixed Docker Ollama image, provider operation URI prefix, catalog file paths.
- Needs follow-up: consider moving default YouTube OAuth scopes from `Settings` to config catalog; production secret-manager integration for Google credentials remains out of scope; real Veo smoke not run because env flags are disabled.

## Secrets leak check

PASS.

- Source/docs/config scan found no committed Google service account JSON, API key, OAuth token, or raw credential.
- Matches found in app source are `RAW_SECRET_MARKERS` guard constants only.
- Test matches are fixture/qualification marker strings only.
- `.env.example` contains placeholders/paths only.

## Test status

PASS with one expected guard caveat.

- `.venv/bin/python -m compileall app`: PASS.
- Focused M10.4/M10.2/config tests: 12 passed, 1 skipped.
- `tests/test_m10_4_veo_real_smoke.py`: SKIPPED by default because real Veo smoke disabled.
- `.venv/bin/pytest -q -k 'not test_worktree_has_no_unrelated_dirty_product_changes'`: PASS, 203 passed, 3 skipped, 1 deselected, 1 warning.
- Plain `.venv/bin/pytest -q` fails only on `test_worktree_has_no_unrelated_dirty_product_changes` because working tree intentionally contains M10.4 product changes.

## Real Veo smoke status

SKIPPED.

- Default disabled.
- Required to run real smoke: `VCOS_VEO_REAL_EXECUTION_ENABLED=true`, `VCOS_VEO_REAL_SMOKE=true`, Google project/location, service account path, and optional GCS output URI.

## Scope explicitly not built

- No dashboard/operator cockpit UI.
- No full long-form final renderer.
- No real Creatomate/ElevenLabs integration.
- No YouTube sync/upload/publish API changes.
- No channel config mutation.
- No backup AI hero routing.
- No Runway/Luma/generic cinematic fallback.
- No fake traffic, bot engagement, platform evasion, scraping/vector/RAG/OPA/Cedar.

## Changed files summary

- Added Google Vertex Veo provider adapter.
- Added AI hero generate API/CLI entrypoints.
- Added M10.4 catalogs and registry validators.
- Updated M10.2 routing/planning/budget services to load provider policy from catalogs.
- Updated settings and `.env.example` for Google/Veo config.
- Updated README/source-of-truth/architecture docs.
- Added/updated tests for single-provider binding, budget behavior, disabled real execution, and real smoke guard.
