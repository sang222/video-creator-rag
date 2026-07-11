# Báo cáo DX2 Provider Stack Reconciliation

## File thay đổi

- Backend/contracts: `app/services/provider_stack.py`, `app/services/dx2.py`, `app/contracts/dx2.py`, `app/services/m10_2.py`, `app/services/m2.py`, `app/services/r3d8.py`, `app/services/m12.py`, `app/services/m12_2.py`, `app/services/m11.py`, `app/services/r3d3.py`, `app/services/r3d4.py`, `app/core/config.py`, `app/services/config_registry.py`, `app/services/channel_contract.py`.
- Catalog/env/docs: `.env.example`, `config/media_provider_*`, `config/pexels_policy_catalog.yaml`, `config/provider_registry_catalog.yaml`, `docs/architecture/provider_stack_freeze.md`, `docs/architecture/source-of-truth.md`, `docs/m12_1_prompt_registry.md`.
- Frontend readiness/wizard: `frontend/src/features/integrations/*`, `frontend/src/features/channels/*`, `frontend/src/lib/api.ts`.
- Test: `tests/test_dx2_provider_stack_reconciliation.py`, regression M2/R3D8/M12/M12.2/frontend.
- R3D9 đang in-progress được giữ nguyên hướng: `app/services/r3d9.py`, `app/contracts/r3d9.py`, frontend ops files, `tests/test_r3d9_runtime_dashboard_ops.py`.

## Chuẩn hóa provider key

- Model/plan/tier là metadata; `provider_key` giữ canonical.

## Routing / capability

- `AI_HERO_GENERATION`, `AI_METAPHOR_GENERATION` route tới `luma_api`.
- `pexels_api` là key fallback visual miễn phí duy nhất.

## M12 readiness label


## ProviderStackDriftGuard

- Read model mới trả `PASS` hoặc `PROVIDER_STACK_DRIFT`.
- Check active catalogs + M2 readiness.
- Khi drift, R3D9 Provider/Cost read model set `snapshot_state=PROVIDER_STACK_DRIFT`, next action `PROVIDER_STACK_DRIFT`.

## Fix PaidAttemptLimit

- `ALLOWED_NOT_EXECUTED` không tăng `PaidAttemptLimitRecord.attempt_count`.
- `will_execute=false` ép không consume attempt.
- Attempt chỉ tăng khi real execution thật sự sẽ submit sau khi pass flags, approval, boundary, limit.

## Docs cập nhật

- Thêm `docs/architecture/provider_stack_freeze.md`.
- Cập nhật source-of-truth và prompt registry docs.
- Luma duration lock: allowed `4/6/8`, max `8s`; không mở `10s`.

## Tests run

- `pytest tests/test_dx2_provider_stack_reconciliation.py -q` → 7 passed.
- `pytest tests/test_r3d9_runtime_dashboard_ops.py -q` → 2 passed.
- `pytest tests/test_m2_provider_wiring_without_paid_calls.py tests/test_r3d8_production_cost_firewall_provider_boundary.py tests/qualification/test_m10_4_google_vertex_veo.py tests/qualification/test_m12_provider_readiness.py tests/test_app_health.py -q` → 42 passed.
- `pytest tests/test_m12_1r_mock_runtime_purge.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py tests/qualification/test_m12_2p_channel_init_contract.py tests/test_channel_activation_cta.py tests/qualification/test_m12_1_prompt_registry.py -q` → 43 passed.
- `pytest tests/test_r3d1_hierarchical_scope.py tests/test_r3d2_effective_channel_runtime_context.py tests/qualification/test_r3d3_agent_context_pack.py tests/qualification/test_r3d4_agent_output_contract_gates.py tests/test_r3d5_controlled_memory_foundation.py tests/test_r3d6_vector_safe_retrieval_foundation.py tests/test_r3d7_closed_learning_retrieval_loop.py tests/test_r3d8_production_cost_firewall_provider_boundary.py -q` → 104 passed.
- `pytest tests/test_m1_contracts.py tests/test_m1_channel.py tests/test_m1_compiler_snapshot.py tests/test_m1_api_cli.py tests/test_m1_channel_aware_packaging_handoff.py tests/test_m2_workflow.py tests/qualification/test_pre_m7_m0_m1_profiles.py tests/qualification/test_pre_m7_m2_workflow.py -q` → 54 passed.
- `pytest tests/test_migration.py tests/test_dx1_semantic_code_convention.py -q` → 8 passed.
- `python -m compileall -q app` via `.venv/bin/python` → passed.
- Frontend `npm run typecheck && npm run lint && npm run test` → 20 tests passed.
- `git diff --check` → passed.

## Bằng chứng không có provider/media/upload thật

- DX2 service chỉ read/validation.
- ProviderStackDriftGuard trả `no_provider_call_made=true`.
- Test assert không có `ProviderAttempt`, không có `RealSmokeRun`, không có provider/media/upload execution trong DX2 paid-boundary path.

## R3D9 go/no-go

- GO: Provider/Cost/Readiness panels có thể dùng canonical provider truth.
- GO: R3D9 read model vẫn ops/read-only và manual-action only.
- NO-GO: provider activation/execution vẫn bị chặn; tiếp tục R3D10 freeze sau R3D9 final review.
