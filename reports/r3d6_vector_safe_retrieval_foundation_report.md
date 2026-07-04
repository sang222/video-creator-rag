# R3D6 — Vector-Safe Retrieval Foundation

## Kết quả
- Status: PASS.
- R3D5 prerequisite đã có: `reports/r3d5_controlled_memory_foundation_report.md`.
- R3D6 chỉ thêm nền tảng retrieval an toàn; không bật mặc định trong production.

## Files changed
- Models: `app/db/models/r3d6.py`, `app/db/models/__init__.py`.
- Contracts: `app/contracts/r3d6.py`, `app/contracts/__init__.py`.
- Services: `app/services/r3d6.py`, `app/services/__init__.py`.
- Config: `app/core/config.py`.
- AgentContextPack integration: `app/services/r3d3.py`.
- Migration: `alembic/versions/0029_r3d6_vector_safe_retrieval.py`.
- Tests: `tests/test_r3d6_vector_safe_retrieval_foundation.py`, `tests/conftest.py`, `tests/test_migration.py`, `tests/qualification/helpers/qualification_asserts.py`.

## Models added
- `EmbeddingFacet`
- `EmbeddingJob`
- `VectorRetrievalManifest`

Vector lưu bằng JSONB-compatible path, không thêm pgvector/external vector DB.

## Embedding eligibility
- `EmbeddingJobService` chỉ cho `READY/EMBEDDED` khi facet thỏa R3D5 gate:
  - memory item `APPROVED`
  - rights `SAFE`
  - prompt safety `PROMPT_SAFE`
  - freshness `FRESH`
  - facet `PROMPT_SAFE`
  - facet `embedding_eligible=true`
  - scope hợp lệ
- Không gọi embedding provider mặc định.
- Blocked job ghi `blocker_reason_codes_json`.
- `EmbeddingFacet` snapshot trạng thái tại lúc embed: approval/rights/prompt-safety/embedding eligibility.
- Detect stale khi `facet_text_hash` thay đổi.

## SQL-filter-first proof
- `VectorSafeRetrievalService` query SQL policy trước:
  - company/channel scope
  - approval/rights/prompt safety/freshness
  - `embedding_eligible=true`
  - use-case allow/deny
  - character/category scope
- Vector score chỉ chạy trên candidate set đã qua SQL/policy.
- Test chứng minh cross-company/cross-channel/blocked memory không surface dù vector score cao.

## Vector/digest behavior
- Vector là ranking layer, không có quyền resurrect candidate bị reject/block.
- Không có vector hoặc không có approved memory trả `EMPTY_SAFE_DIGEST` / `VECTOR_RUNTIME_EMPTY_SAFE`.
- Digest agent-specific:
  - creative agents nhận lesson compact + ref facet.
  - `ProviderReadinessSummaryAgent` không nhận creative memory.
  - `GatekeeperSoftReviewAgent` nhận manifest summary, không raw memory.
- Conflict resolver gom positive/negative contradiction thành một rule rõ ràng.

## Manifest/audit behavior
- Mỗi retrieval ghi `VectorRetrievalManifest`.
- Manifest lưu:
  - SQL filter
  - before/after policy counts
  - selected/blocked/rejected refs
  - ranking params
  - retrieval hash
  - digest hash
- Retrieval hash stable với cùng request/candidate set, đổi khi eligible set đổi.

## AgentContextPack integration
- `AgentContextPackBuilder` hỗ trợ optional `memory_digest`.
- Default disabled qua config:
  - `CONTROLLED_MEMORY_RETRIEVAL_ENABLED=false`
  - `VECTOR_RETRIEVAL_ENABLED=false`
  - `EMBEDDING_EXECUTION_ENABLED=false`
- Khi enabled, context pack chỉ nhận digest + retrieval manifest refs.
- Không đưa raw memory item, raw facet text field, embedding row, hoặc full old script vào pack.

## Empty/no-memory safe behavior
- Không có approved memory không crash.
- Vector runtime unavailable không fake success production; trả safe empty result có reason code.

## Proof no raw RAG to prompt
- Không có agent direct vector query.
- Không inject raw memory vào production prompts.
- R3D3 integration chỉ thêm `memory_digest` optional, digest-only, default off.

## Proof no external provider/vector DB calls by default
- Không thêm Qdrant/Weaviate/Pinecone.
- Không gọi external embedding provider.
- Không thêm provider/media/upload calls.
- Tests có source guard cho external vector DB, embedding provider, upload/media tokens.

## Tests run
- `PYTHONPATH=. .venv/bin/python -m compileall -q app tests/test_r3d6_vector_safe_retrieval_foundation.py`
  - PASS
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d6_vector_safe_retrieval_foundation.py`
  - PASS: `11 passed, 1 warning`
- `PYTHONPATH=. .venv/bin/alembic heads`
  - PASS: `0029_r3d6_vector_safe_retrieval (head)`
- `git diff --check`
  - PASS
- Regression:
  - `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d1_hierarchical_scope.py tests/test_r3d2_effective_channel_runtime_context.py tests/qualification/test_r3d3_agent_context_pack.py tests/qualification/test_r3d4_agent_output_contract_gates.py tests/test_m1_channel_aware_packaging_handoff.py tests/test_m2_provider_wiring_without_paid_calls.py tests/test_r3d5_controlled_memory_foundation.py tests/test_r3d6_vector_safe_retrieval_foundation.py tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py tests/test_migration.py tests/qualification/test_pre_m7_migrations.py`
  - PASS: `126 passed, 1 warning`

## Follow-up R3D7
- Có thể thêm attribution/influence layer nếu được phê duyệt riêng.
- Không auto-promote learning.
- Không mở closed learning loop.
- Không để memory/vector override channel contract/effective context.
