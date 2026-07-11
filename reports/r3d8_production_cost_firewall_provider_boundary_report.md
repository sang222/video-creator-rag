# R3D8 — Production Cost Firewall + Provider/Character/Voice Boundary

## Files changed
- `app/db/models/r3d8.py`
- `app/contracts/r3d8.py`
- `app/services/r3d8.py`
- `alembic/versions/0031_r3d8_production_cost_firewall.py` (`revision=0031_r3d8_cost_firewall`)
- `app/core/config.py`
- `app/db/models/__init__.py`
- `app/contracts/__init__.py`
- `app/services/__init__.py`
- `app/main.py`
- `app/cli/main.py`
- `tests/test_r3d8_production_cost_firewall_provider_boundary.py`
- `tests/conftest.py`
- `tests/test_migration.py`
- `tests/qualification/helpers/qualification_asserts.py`

## Models added
- `RenderRevision`
- `CostEstimateSnapshot`
- `HumanPaidRenderApproval`
- `ProviderIdempotencyKey`
- `ProviderJobSnapshot`
- `PaidProviderCallLedger`
- `PaidAttemptLimitRecord`
- `ProxyPreviewArtifactFlag`

## Cost firewall behavior
- `RenderRevisionService` tạo revision từ package artifacts/gate refs, hash ổn định theo render plan, supersede revision cũ cùng package.
- `CostEstimateService` đọc M2 readiness/capability; provider thiếu config trả `ESTIMATE_PENDING_PROVIDER_CONFIG`, paid provider thiếu real pricing trả `ESTIMATE_REQUIRES_REAL_PROVIDER`.
- Không fake zero-cost cho paid provider. Pexels cost có thể `0`, nhưng vẫn yêu cầu attribution/usage manifest.

## Approval behavior
- `HumanPaidRenderApprovalService` tạo `PENDING`, hỗ trợ approve/reject/revoke.
- `PaidRenderApprovalGate` chặn approval thiếu, sai revision, stage chưa approve, rejected/revoked/expired, hoặc vượt `max_approved_cost`.
- Approval scope theo `render_revision_id` và provider stage.

## Idempotency behavior
- `ProviderIdempotencyService` tạo key ổn định từ `provider_key + provider_stage + render_revision_id + request_fingerprint`.
- Cùng revision/request dùng lại key; đổi request hoặc revision tạo key khác.

## Provider boundary decisions
- `PaidProviderBoundaryService` là preflight trung tâm, validation-only.
- Chặn khi provider chưa ready, cost estimate thiếu/không estimated, approval thiếu, attempt limit vượt, deterministic gate BLOCK, input voice/character không hợp lệ, proxy artifact không publishable.
- Default flags false trả `ALLOWED_NOT_EXECUTED` khi mọi gate pass; không có `EXECUTED`.
- Mọi decision được ghi `PaidProviderCallLedger`.

## Pexels policy gate behavior
- `PexelsUsagePolicyGate` dùng policy M2.
- Chặn `factual_evidence`, `recurring_host_identity`, role bị cấm, quá max clips/runtime/reuse.
- Pexels không được làm evidence source, recurring character source, hoặc core visual backbone.

## Character / voice boundary behavior
- `ProviderCharacterInputGate` chặn `NO_CHARACTER` khi provider/stage cần character.
- Character refs phải khớp `EffectiveChannelRuntimeContextSnapshot`; thiếu image branch/reference pack bị block.
- `ProviderVoiceInputGate` yêu cầu voice profile active, language/accent, consent và commercial use hợp lệ.

## Proxy preview protection
- `ProxyPreviewArtifactFlag` luôn `preview_only=true`, `not_final_media=true`, `not_publishable=true`.
- `ProxyPreviewGate` chặn preview artifact làm final media/handoff input.

## Proof no real provider calls by default
- Default flags:
  - `PROVIDER_REAL_EXECUTION_ENABLED=false`
  - `ELEVENLABS_REAL_GENERATION_ENABLED=false`
  - `LUMA_REAL_GENERATION_ENABLED=false`
  - `PEXELS_REAL_SEARCH_ENABLED=false`
  - `GOOGLE_DRIVE_REAL_ARCHIVE_ENABLED=false`
- R3D8 service không instantiate provider adapters, không tạo `ProviderAttempt`, `MediaRenderJob`, `HumanUploadTask`, không gọi YouTube upload.
- Boundary allow state vẫn là `ALLOWED_NOT_EXECUTED`, `will_execute=false`, `no_network_call_made=true`.

## Tests run
- `PYTHONPATH=. .venv/bin/python -m compileall -q app` — PASS
- `PYTHONPATH=. .venv/bin/alembic heads` — PASS, head `0031_r3d8_cost_firewall`
- `PYTHONPATH=. .venv/bin/alembic upgrade head --sql` — PASS
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d8_production_cost_firewall_provider_boundary.py -q` — PASS, `12 passed`
- Regression:
  - `tests/test_r3d1_hierarchical_scope.py`
  - `tests/test_r3d2_effective_channel_runtime_context.py`
  - `tests/qualification/test_r3d3_agent_context_pack.py`
  - `tests/qualification/test_r3d4_agent_output_contract_gates.py`
  - `tests/test_m1_channel_aware_packaging_handoff.py`
  - `tests/test_m2_provider_wiring_without_paid_calls.py`
  - `tests/test_r3d5_controlled_memory_foundation.py`
  - `tests/test_r3d6_vector_safe_retrieval_foundation.py`
  - `tests/test_r3d7_closed_learning_retrieval_loop.py`
  - `tests/test_r3d8_production_cost_firewall_provider_boundary.py`
  - `tests/qualification/test_m12_2_first_scripted_video_package.py`
  - `tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py`
  - `tests/test_migration.py`
  - PASS: `148 passed, 1 warning`

## Follow-up R3D9 / future provider activation
- Implement real provider activation only behind explicit flags, valid config, cost estimate, human approval, idempotency, attempt limit, and R3D8 ledger.
- Add provider polling/resume workers without duplicate submit.
- Add archive/storage execution for Google Drive only after separate human-approved archive boundary.
