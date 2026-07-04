# DX3 Safe Modular Refactor Report

Ngày: 2026-07-04

## Phạm vi

- Loại patch: DX3 maintainability refactor.
- Nguyên tắc: không đổi behavior runtime, không đổi route path/method/schema, không DB migration, không provider/media/upload/YouTube execution.
- Trọng tâm: tách `app/main.py` từ monolith endpoint 5k+ dòng thành bootstrap + route modules.

## Inventory trước refactor (>1000 dòng)

| File | Dòng | Loại | Safe extraction target |
|---|---:|---|---|
| `app/main.py` | 5411 | API/router + serializers mixed responsibility | Route modules + explicit route imports/serializers |
| `app/cli/main.py` | 3201 | CLI command surface | Để lại: cần parity test cho command registry |
| `app/services/m12_2.py` | 2298 | service | Để lại: business logic phức tạp |
| `app/services/m5.py` | 1989 | service | Để lại: service logic, không refactor khi chưa có target hẹp |
| `app/services/m6.py` | 1849 | service | Để lại |
| `app/services/r3d3.py` | 1829 | service | Để lại |
| `app/services/m10_3.py` | 1625 | service | Để lại |
| `app/services/m10_1.py` | 1605 | service | Để lại |
| `app/services/m9.py` | 1583 | service | Để lại |
| `app/services/m11.py` | 1560 | service | Để lại |
| `app/services/m10_2.py` | 1468 | service | Để lại |
| `app/services/m12.py` | 1416 | service | Để lại |
| `app/services/m10_5.py` | 1405 | service | Để lại; file đã dirty trước DX3 |
| `app/services/r3d9.py` | 1404 | service/read model | Để lại |
| `app/services/m10.py` | 1296 | service | Để lại |
| `app/services/m8.py` | 1271 | service | Để lại |
| `app/services/r3d4.py` | 1263 | service/gates | Để lại |
| `app/services/gates.py` | 1241 | service/gates | Để lại |
| `app/services/m7.py` | 1166 | service | Để lại |
| `app/services/m12_2p3.py` | 1100 | service | Để lại |
| `frontend/src/features/channels/channel-init-wizard.tsx` | 1013 | frontend component | Tách schema/type/helper form thuần |

## Files refactored

- `app/main.py`
- `app/api/__init__.py`
- `app/api/routes/__init__.py`
- `app/api/routes/core_auth.py`
- `app/api/routes/dashboard_ops.py`
- `app/api/routes/integration_readiness.py`
- `app/api/routes/channel_workspace.py`
- `app/api/routes/project_foundation.py`
- `app/api/routes/artifact_policy_gates.py`
- `app/api/routes/provider_ops.py`
- `app/api/routes/production_planning.py`
- `app/api/routes/publishing_handoff.py`
- `app/api/routes/drive_archive.py`
- `app/api/routes/youtube_follow.py`
- `app/api/routes/learning_memory.py`
- `app/api/routes/provider_execution_safety.py`
- `app/api/routes/llm_prompt_ops.py`
- `app/api/routes/package_review.py`
- `app/api/routes/derivative_media.py`
- `app/api/routes/media_provider_workflow.py`
- `app/api/routes/imports.py`
- `app/api/routes/serializers_core.py`
- `app/api/routes/serializers_publish_learning.py`
- `app/api/routes/shared.py`
- `frontend/src/features/channels/channel-init-wizard.tsx`
- `frontend/src/features/channels/channel-init-wizard-form.ts`

## Inventory sau refactor (>1000 dòng)

- `app/main.py`: 59 dòng.
- `app/api/routes/shared.py`: 5 dòng compatibility shim.
- `app/api/routes/serializers_core.py`: 900 dòng.
- `app/api/routes/serializers_publish_learning.py`: 651 dòng.
- `frontend/src/features/channels/channel-init-wizard.tsx`: 894 dòng.
- `frontend/src/features/channels/channel-init-wizard-form.ts`: 130 dòng.
- Không tạo file route/helper mới >1000 dòng.
- Các file >1000 dòng còn lại là service/CLI đã liệt kê ở inventory trước và được cố ý để nguyên vì không đủ low-risk cho DX3.

## Route movement

- `core_auth`: `/health`, `/auth/*`.
- `dashboard_ops`: dashboard/ops/runtime trace/R3D9 read-model routes.
- `integration_readiness`: `/integrations/*`.
- `channel_workspace`: company/channel init/channel profile/policy activation routes.
- `project_foundation`: project/content/character/voice/localization foundation routes.
- `artifact_policy_gates`: artifact/review/revision/gate/policy routes.
- `provider_ops`: provider registry/credential/quota/cost/health/incident/manual action routes.
- `production_planning`: editorial/context/daily-run/production/render/QC routes.
- `publishing_handoff`: publish handoff/uploaded video/analytics/YouTube auth routes.
- `drive_archive`: Google Drive OAuth/media offload/local cleanup/retention routes.
- `youtube_follow`: YouTube public/owner analytics follow routes.
- `learning_memory`: post-publish health/recovery/learning/memory routes.
- `provider_execution_safety`: paid render/provider boundary/job/ledger routes.
- `llm_prompt_ops`: LLM router + prompt registry routes.
- `package_review`: video package/review queue/patch approval/manual upload task routes.
- `derivative_media`: short/derivative/reusable/upload-card/human-upload task routes.
- `media_provider_workflow`: media provider role/capability/routing/render asset/gate routes.

Route registration giữ thứ tự gốc bằng `_include_router_flat()` để `create_app().routes` vẫn phẳng như trước.

## Compatibility

- Public import `from app.main import create_app` giữ nguyên.
- `app.main.app = create_app()` giữ nguyên.
- Local classes `CompanyCreate`, `CompanyRead`, `ChannelCreateRequest`, `ChannelActivateRequest` được chuyển sang `app/api/routes/imports.py`.
- Mapper/helper `_company`, `_channel`, `_artifact`, `_gate_run`, `_as_http_error`, ... được chuyển sang serializers modules.
- Route modules import explicit symbols trực tiếp từ `imports.py`, `serializers_core.py`, `serializers_publish_learning.py`; không dùng wildcard `shared` import trong route modules.
- `app/api/routes/shared.py` chỉ giữ compatibility shim 5 dòng.
- OpenAPI parity với `HEAD:app/main.py`: 318 paths, 338 operations, missing=0, extra=0.

## Tests/checks

- PASS: `PYTHONPATH=. .venv/bin/python -m compileall -q app`
- PASS: OpenAPI parity script vs `HEAD:app/main.py` — 318 paths / 338 operations unchanged.
- PASS: `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py tests/test_r3d9_ux2_packaging_review_queue.py -q` — 23 passed.
- PASS: `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q` — 2 passed.
- PASS: `PYTHONPATH=. .venv/bin/pytest tests/test_dx1_semantic_code_convention.py tests/test_dx2_provider_stack_reconciliation.py -q` — 13 passed.
- PASS: `cd frontend && npm run typecheck`
- PASS: `cd frontend && npm run lint`
- PASS: `cd frontend && npm run test` — 8 files / 25 tests passed.
- PASS: `git diff --check`
- PARTIAL: `PYTHONPATH=. .venv/bin/pytest tests/test_app_health.py tests/test_m1_api_cli.py tests/test_m2_workflow.py tests/test_m3_policy_gates.py tests/test_m4_ops_foundation.py -q` — 38 passed, 2 failed.
  - Failures only in `tests/test_m4_ops_foundation.py` scope guard: test forbids later `embedding*` / learning/vector tables that now exist in current schema.
  - Không phải route/refactor regression.

## No behavior/provider/upload mutation proof

- Không DB migration.
- Không đổi contracts/schema.
- Không đổi service/model logic.
- Không gọi provider/media/render/Drive/YouTube/upload API.
- Không thêm execute/job-control button.
- Không mutate Channel Contract / ChannelProfileVersion / EffectiveContextSnapshot.
- Refactor chỉ di chuyển route handler code và mapper code; OpenAPI path/method parity giữ nguyên.
