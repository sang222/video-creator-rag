# R3D7 - Closed Learning Retrieval Loop

## Files changed
- `app/db/models/r3d7.py`
- `app/contracts/r3d7.py`
- `app/services/r3d7.py`
- `alembic/versions/0030_r3d7_closed_learning_loop.py`
- `app/services/r3d3.py`
- `app/services/r3d6.py`
- `app/main.py`
- `app/cli/main.py`
- `app/db/models/__init__.py`
- `app/contracts/__init__.py`
- `app/services/__init__.py`
- `tests/test_r3d7_closed_learning_retrieval_loop.py`
- `tests/conftest.py`
- `tests/test_migration.py`
- `tests/qualification/helpers/qualification_asserts.py`

## Models added
- `MemoryInfluenceManifest`
- `QualityDeltaAttribution`
- `LearningToMemoryPromotionRun`
- `AgentMemoryApplicationRecord`
- `MemoryConfidenceUpdateLedger`

## Promotion flow
- `LearningToMemoryPromotionService` chỉ nhận `ApprovedPlaybookEntry` human-approved.
- Raw `LearningCandidate` chưa approved bị `BLOCKED`.
- Promotion tạo `ChannelMemoryItem`/`MemoryFacet` draft qua R3D5 và ghi `LearningToMemoryPromotionRun`.
- Memory vẫn ở review queue nếu R3D5 yêu cầu approval. Không tự approve, không tự inject.

## Memory digest injection behavior
- `AgentContextPackBuilder` chỉ gọi memory khi `CONTROLLED_MEMORY_RETRIEVAL_ENABLED=true`.
- Retrieval vẫn qua R3D6 `VectorSafeRetrievalService`.
- Agent nhận digest + manifest refs; không nhận raw memory item, raw facet row, embedding row, hay full old scripts.
- `ProviderReadinessSummaryAgent` không nhận creative memory.
- `GatekeeperSoftReviewAgent` chỉ nhận manifest summary.

## MemoryInfluenceManifest behavior
- Mỗi digest được inject đều tạo `MemoryInfluenceManifest`.
- Manifest link `video_project_id`, `package_id`, `effective_context_snapshot_id`, `agent_key`, `retrieval_manifest_id`, facet/item ids, `digest_hash`, `prompt_context_hash`.
- `MemoryInfluenceManifestGate` block nếu thiếu manifest, mismatch effective context, mismatch retrieval scope, hoặc memory ngoài scope.

## QualityDeltaAttribution behavior
- `QualityDeltaAttributionService` so baseline vs observed theo metric family.
- Kết quả hỗ trợ: `IMPROVED`, `DEGRADED`, `INCONCLUSIVE`, `TOO_EARLY`, `BLOCKED_BY_DATA_QUALITY`.
- Ledger ghi confidence change qua `MemoryConfidenceUpdateLedger`.
- Một sample không tự nâng memory lên `HIGH`; cap ở `MEDIUM` với reason `ONE_SAMPLE_CONFIDENCE_CAP`.

## Data quality / too early
- Analytics immature trả `TOO_EARLY`.
- Missing/stale/conflicted/low-confidence snapshot trả `BLOCKED_BY_DATA_QUALITY`.
- Severe unresolved enforcement incident freeze attribution bằng reason-coded block.

## Proof no auto-promotion
- Test `unapproved LearningCandidate cannot promote`.
- Promotion run ghi `NO_AUTO_MEMORY_APPROVAL`; memory draft cần R3D5 human approval.
- Không mutate `ChannelProfileVersion`, channel contract, hay policy snapshot.

## Proof no raw RAG to prompt
- Digest có `context_pack_payload=digest_only`.
- Test chặn raw `facet_text`, `embedding_vector_json`, full script/history trong `AgentContextPack`.
- Agents không query vector DB trực tiếp; retrieval qua service R3D6/R3D7.

## Proof no provider/media/upload calls
- R3D7 không thêm provider/media/upload execution.
- Test source guard chặn ElevenLabs/Luma/Creatomate/Pexels/Drive/YouTube upload path.
- Default flags vẫn off cho vector/provider external execution.

## Tests run
- `PYTHONPATH=. .venv/bin/python -m compileall -q app tests/test_r3d7_closed_learning_retrieval_loop.py` - PASS
- `PYTHONPATH=. .venv/bin/alembic heads` - PASS, head `0030_r3d7_closed_learning_loop`
- `PYTHONPATH=. .venv/bin/alembic upgrade head --sql` - PASS
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d7_closed_learning_retrieval_loop.py` - PASS, 12 passed
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d6_vector_safe_retrieval_foundation.py` - PASS, 11 passed
- Regression R3D1-R3D7 + M1/M2 + M12.2/M12.2S + migration - PASS, 138 passed

## Follow-up for R3D8
- Add production cost firewall and paid-provider boundary.
- Keep default real execution flags off.
- Add render revision, cost estimate, approval, idempotency, attempt limit, provider job snapshot, paid call ledger, and proxy preview guards.
