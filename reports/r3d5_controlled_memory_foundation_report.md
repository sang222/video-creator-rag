# R3D5 Controlled Memory Foundation Report

## Kết quả

PASS.

Đã thêm nền Controlled Memory scope-bound, human-approved, audit được. R3D5 không phải generic RAG, không inject memory vào prompt production, không thêm vector retrieval.

## Files changed

- `app/db/models/r3d5.py`
- `app/contracts/r3d5.py`
- `app/services/r3d5.py`
- `alembic/versions/0028_r3d5_controlled_memory.py`
- `app/db/models/__init__.py`
- `app/contracts/__init__.py`
- `app/services/__init__.py`
- `app/main.py`
- `tests/test_r3d5_controlled_memory_foundation.py`
- `tests/conftest.py`
- `tests/test_migration.py`
- `tests/qualification/helpers/qualification_asserts.py`

## Models added

- `ChannelMemoryItem`
- `MemoryFacet`
- `MemoryReviewQueueItem`
- `MemoryApprovalDecision`
- `MemoryUsageManifest`
- `MemorySourceLink`

Alembic head: `0028_r3d5_controlled_memory`.

## Service summary

- `ControlledMemoryService`: create draft, create facets, submit review, approve/reject/archive, usage manifest, approved playbook import.
- `MemoryFacetExtractor`: tạo facet nhỏ từ approved playbook/manual reference; chặn raw script/provider payload/analytics blob/secret-like text.
- Gates: approval, rights, prompt-safety, scope, budget, duplication, freshness, retrieval-audit placeholder.

## Approval / rules summary

- LearningCandidate chưa human-approved bị chặn.
- Rejected/suppressed/expired/blocked learning không được tạo/approve memory.
- Failed output chỉ vào memory qua `AVOID_REPEAT` + NEGATIVE facet sau human approval.
- ApprovedPlaybookEntry tạo memory draft + review queue, không auto prompt-eligible.
- Human approval memory ghi `MemoryApprovalDecision`, `human_approved_at`, `approved_by`.

## Scope / rights / prompt-safety gates

- Prompt eligibility yêu cầu `APPROVED + SAFE + PROMPT_SAFE + FRESH`.
- ScopeGate chặn cross-company, cross-channel mặc định, category mismatch, character mismatch.
- `COMPANY_APPROVED` cần explicit allow ở gate.
- `NO_CHARACTER` context chặn character-specific memory.
- BudgetGate chặn facet quá dài/raw artifact blob.

## M10 / M11 integration

- ApprovedPlaybookEntry là source hợp lệ để tạo memory draft.
- LearningCandidate chỉ được draft nếu có human approval hoặc approved playbook link.
- Không mutate `ChannelProfileVersion`, `CompiledChannelPolicySnapshot`, workflow, title/metadata/platform state.

## Proof no vector / embedding / retrieval

- Không thêm `EmbeddingFacet`, vector retrieval service, pgvector, vector DB, hay external vector provider.
- `MemoryFacet.embedding_eligible` chỉ là flag chuẩn bị cho R3D6, default `false`.
- `RetrievalAuditGate` chỉ là placeholder manifest-required.

## Proof no prompt injection

- R3D3 `AgentContextPackBuilder` không đọc R3D5.
- M12.2/M12.2S prompt path không nhận `ChannelMemoryItem`/`MemoryFacet`.
- `MemoryUsageManifest` chỉ ghi planned/blocked usage audit, không render prompt.

## Tests run

- `PYTHONPATH=. .venv/bin/python -m compileall -q app tests/test_r3d5_controlled_memory_foundation.py` -> PASS.
- `PYTHONPATH=. .venv/bin/alembic heads` -> `0028_r3d5_controlled_memory (head)`.
- `git diff --check` -> PASS.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d5_controlled_memory_foundation.py` -> 11 passed, 1 warning.
- Regression:
  `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d1_hierarchical_scope.py tests/test_r3d2_effective_channel_runtime_context.py tests/qualification/test_r3d3_agent_context_pack.py tests/qualification/test_r3d4_agent_output_contract_gates.py tests/test_m1_channel_aware_packaging_handoff.py tests/test_m2_provider_wiring_without_paid_calls.py tests/test_r3d5_controlled_memory_foundation.py tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py tests/test_migration.py tests/qualification/test_pre_m7_migrations.py`
  -> 115 passed, 1 warning.

Warning hiện hữu: Starlette/httpx TestClient deprecation.

## Follow-up R3D6

- Thêm embedding job/facet storage an toàn.
- SQL policy filter trước vector rank.
- Persist `VectorRetrievalManifest`.
- Agent chỉ nhận digest + manifest refs, không raw memory/RAG.
- Default retrieval/embedding disabled, empty-safe behavior.
