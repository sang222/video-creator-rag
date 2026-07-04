# P1 Pre-LTS Package Runtime Quarantine + Freeze Verifier Scope Repair

Ngày: 2026-07-04

## Kết luận

Đã xử lý P1 freeze invariant regression. Live `GET /ops/runtime-lts-freeze-check` từ `BLOCKED` chuyển sang `PASS`, kèm warning đúng cho package pre-LTS/test rehearsal bị loại khỏi active runtime surface.

Không fake snapshot/gate. Không provider/media/upload/YouTube call. Không commit/tag.

## Blocker package inspection

Package: `fe563e52-ae78-4abb-acd1-3d45dfb9eea5`

- `created_at`: `2026-06-28 16:38:39.524595+00:00`
- `updated_at`: không có field trên `FirstScriptedVideoPackage`
- `package_status`: `READY_FOR_MEDIA_PROVIDERS`
- `video_project_id`: `null`
- `channel_id`: `a77bc5dc-f7be-4ae0-8523-55fb846d64bd`
- `channel`: `small-team-ai` / `Small Team AI`, active
- `company_id`: `e0b7c806-b39e-4792-bf2e-7e8c6d6ca464`
- `effective_context_snapshot_id`: `null`
- `AgentContextPackSnapshot`: `0`
- `R3D4GateRun`: `0`
- `HumanUploadTask`: `0`
- `UploadedVideo`: `0`
- `RenderRevision`: `0`
- `ProviderJobSnapshot`: `0`
- `PaidProviderCallLedger EXECUTED`: `0`
- `MediaRenderJob`: `0`
- `FinalMediaRef`: `0`
- `VideoGenerationBoundary`: `BLOCKED_PROVIDER_NOT_CONFIGURED`, `no_provider_calls_confirmed=true`
- `runtime_guard`: `real_ollama_agent_run=true`, `llm_router_only=true`, `no_media_provider_calls=true`, `no_upload_or_publish=true`

Package predates Runtime LTS freeze and matches M12.2S/text-only rehearsal evidence, not active runtime package.

## Classification decision

Classification: `TEST_REHEARSAL_PACKAGE`

Runtime disposition applied: `TEST_REHEARSAL_EXCLUDED`

Disposition row:

- `id`: `22b19f6a-0f90-41b3-8a25-950a49de9cff`
- `package_id`: `fe563e52-ae78-4abb-acd1-3d45dfb9eea5`
- `reason_codes_json`:
  - `MISSING_RUNTIME_LTS_INVARIANTS`
  - `NO_PROVIDER_MEDIA_UPLOAD_REFS_FOUND`
  - `PRE_LTS_PACKAGE_EXCLUDED_FROM_RUNTIME_SURFACE`
  - `TEST_REHEARSAL_TEXT_ONLY_PACKAGE`
- `decided_by`: `codex:p1_pre_lts_runtime_quarantine`

Không đổi package status. Không xóa package/artifact.

## Files changed

- `alembic/versions/0033_p1_pre_lts_package_runtime_disposition.py`
- `app/db/models/r3d10.py`
- `app/db/models/__init__.py`
- `app/services/r3d10.py`
- `app/services/__init__.py`
- `tests/conftest.py`
- `tests/test_r3d10_runtime_lts_freeze.py`
- `tests/test_migration.py`
- `tests/test_dx1_semantic_code_convention.py`
- `reports/p1_pre_lts_package_runtime_quarantine_report.md`

Worktree vẫn có file dirty ngoài scope từ trước; không revert.

## Disposition/quarantine mechanism

Thêm table `package_runtime_dispositions`:

- `package_id`
- `disposition`
- `reason_codes_json`
- `decided_by`
- `evidence_json`
- `created_at`

Allowed dispositions trong verifier:

- `ACTIVE_RUNTIME_ELIGIBLE`
- `PRE_LTS_HISTORICAL_EXCLUDED`
- `TEST_REHEARSAL_EXCLUDED`
- `CORRUPT_BLOCKED_NEEDS_REVIEW`

Service `PackageRuntimeDispositionService.create()` chỉ ghi audit row; không mutate package, Channel Contract, `ChannelProfileVersion`, `EffectiveChannelRuntimeContextSnapshot`, context pack, hoặc gate.

## Verifier scope before/after

Before:

- `RuntimeLTSFreezeVerifier._runtime_packages()` lấy mọi package có runtime status.
- Package historical `READY_FOR_MEDIA_PROVIDERS` thiếu LTS invariants làm live verifier `BLOCKED`.

After:

- Verifier đọc latest `PackageRuntimeDisposition`.
- `PRE_LTS_HISTORICAL_EXCLUDED` / `TEST_REHEARSAL_EXCLUDED` không nằm trong active runtime package scope nếu không có execution refs.
- Excluded package emit warning `PRE_LTS_PACKAGE_EXCLUDED_FROM_RUNTIME_SURFACE`.
- Excluded package vẫn `BLOCKED` nếu có upload/render/provider/media refs.
- Media-ready package thiếu invariants và chưa có disposition vẫn `BLOCKED`.
- Active/current package thiếu effective context/context pack/gate vẫn `BLOCKED`.

## Tests run/result

- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d10_runtime_lts_freeze.py` -> 13 passed
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_migration.py` -> 2 passed
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d9_runtime_dashboard_ops.py` -> 2 passed
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d9_ux2_packaging_review_queue.py` -> 10 passed
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_dx1_semantic_code_convention.py tests/test_dx2_provider_stack_reconciliation.py` -> 13 passed
- `PYTHONPATH=. .venv/bin/python -m compileall -q app` -> passed
- `PYTHONPATH=. .venv/bin/alembic current` -> `0033_p1_pre_lts_disposition (head)`
- `git diff --check` -> passed

## Live freeze verifier result

Live API after API rebuild/restart:

- Endpoint: `GET /ops/runtime-lts-freeze-check`
- `freeze_status`: `PASS`
- `blocker_reason_codes`: `[]`
- `warning_reason_codes`: `["PRE_LTS_PACKAGE_EXCLUDED_FROM_RUNTIME_SURFACE"]`
- `no_provider_media_upload_execution`: `true`
- `excluded_package_count`: `1`
- latest excluded package: `fe563e52-ae78-4abb-acd1-3d45dfb9eea5`

## Proof no fake snapshots/gates created

For package `fe563e52-ae78-4abb-acd1-3d45dfb9eea5` after disposition:

- `effective_context_snapshot_id`: `null`
- `effective_snapshot_exists`: `false`
- `AgentContextPackSnapshot` count: `0`
- `R3D4GateRun` count: `0`
- package status still `READY_FOR_MEDIA_PROVIDERS`

## Proof no provider/media/upload/YouTube calls

For package `fe563e52-ae78-4abb-acd1-3d45dfb9eea5` after disposition:

- `HumanUploadTask`: `0`
- `UploadedVideo`: `0`
- `RenderRevision`: `0`
- `ProviderJobSnapshot`: `0`
- `PaidProviderCallLedger EXECUTED`: `0`
- `MediaRenderJob`: `0`
- `FinalMediaRef`: `0`
- `VideoGenerationBoundary.no_provider_calls_confirmed`: `true`
- Live verifier `no_provider_media_upload_execution=true`

No Drive upload, YouTube upload/publish, render, media generation, or provider execution was called by this patch.

## P0/P1/P2/P3 classification

P1.

Reason: live Runtime LTS freeze verifier was `BLOCKED` by historical package scope, preventing INT2/manual-ops continuation despite isolated R3D10 tests passing.

## INT2 resume decision

INT2 can resume after this patch because live Runtime LTS verifier is now `PASS`.

Do not continue pilot automatically from this patch; next operator action should start from a fresh verified runtime state.
