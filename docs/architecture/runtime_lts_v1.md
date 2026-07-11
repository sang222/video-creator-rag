# Runtime LTS v1

R3D10 freeze VCOS backend/core ở trạng thái Runtime LTS v1. Đây là freeze gate, không phải feature phase.

## Runtime LTS v1 nghĩa là gì

- Backend/core đủ ổn định để vận hành qua dashboard ops và manual workflow.
- Runtime truth là PostgreSQL + immutable snapshots.
- Thay đổi core sau freeze chỉ dành cho P0/P1 theo post-freeze protocol.
- P2/P3 đi vào ProductionPainLog, gom review theo batch.
- Provider activation, paid execution, upload/publish automation vẫn disabled cho tới phase tương lai explicit.

## Architecture summary

- Channel Init/Channel Contract là authority.
- `VideoProject` freeze `effective_context_snapshot_id` và `channel_contract_content_hash`.
- `EffectiveChannelRuntimeContextSnapshot` là runtime context used-by-project/package.
- Agent prompt dùng `AgentContextPackSnapshot`, digest/ref/hash, budget gate và shape gate.
- Output qua `AgentOutputContract`, `ArtifactCanonicalizer`, R3D4 deterministic gates.
- M1/M12.2R handoff là manual publish/backfill flow.
- R3D9 là operator cockpit/read-model, không phải job control center.

## Phase completion map

- R3D1: hierarchical scope + Channel Contract authority.
- R3D2: EffectiveChannelRuntimeContextSnapshot.
- R3D3: AgentContextPackSnapshot + prompt budget/shape gates.
- R3D4: AgentOutputContract + deterministic gates.
- M1: packaging handoff/manual publish gate.
- M2: provider wiring/readiness without paid calls.
- R3D5: controlled memory.
- R3D6: vector-safe retrieval.
- R3D7: memory influence + quality attribution.
- R3D8: production cost firewall/provider boundary.
- DX1: semantic module convention.
- DX2: provider stack reconciliation/drift guard.
- R3D9: runtime dashboard ops/backfill/runtime trace.
- R3D10: Runtime LTS freeze verification/protocol.

## Provider stack freeze

- `elevenlabs`: voice/TTS.
- `luma_api`: AI hero/metaphor video, max 8s, allowed 4/6/8.
- `native_ffmpeg_renderer`: local final assembly + native motion + caption/compositing authority; not an external paid provider.
- `pexels_api`: free visual fallback only.
- YouTube: manual publish + read-only analytics/verification.
- Drive/object storage: optional archive later.

Deferred/inactive: Veo, Runway, Envato, Adobe, Shutterstock, DaVinci API core, TBD final renderer, Pexels+Pixabay combined fallback.

## Manual publish boundary

- VCOS prepares title/description/subtitles/thumbnail/disclosure/timing/checklist.
- Human uploads outside VCOS.
- Human paste-back/backfill creates/updates uploaded video ledger.
- VCOS verifies/read-models analytics only through allowed read paths.
- No YouTube upload API, auto publish, scheduled publish, reupload, or platform edit.

## Memory/vector rules

- Memory future prompt eligibility requires `APPROVED + SAFE + PROMPT_SAFE + FRESH`.
- Retrieval is SQL-filter-first.
- Agents receive memory digest + manifest refs only.
- Raw memory/RAG text is hidden from production prompts by default.
- Memory cannot override Channel Contract or EffectiveChannelRuntimeContextSnapshot.
- Learning cannot auto-promote memory.

## Disabled activation boundary

Runtime LTS v1 keeps these disabled:

- provider execution
- ElevenLabs generation
- Luma generation
- NativeFFmpeg production render execution
- Pexels search/download
- Drive upload
- YouTube upload/publish
- daily/no-view/vector/provider job-control buttons

Any future activation requires explicit future phase, staging-first verification, regression gate, and human approval.
