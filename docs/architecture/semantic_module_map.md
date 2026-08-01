# Semantic Module Map

DX1 chuẩn hóa cách tìm runtime theo domain. Phase reports vẫn là audit history; runtime code mới nên ưu tiên import semantic module.

## Domain modules

| Domain | Service module | Owns |
| --- | --- | --- |
| Editorial Research | `app.services.editorial_research` | Research run, evidence-bound candidate, and auditable runway stages. |
| Context Resolver | `app.services.context_resolver` | Retrieval plan, context pack, scoped resource resolution. |
| Project Admission | `app.services.project_admission` | Admission decision, budget/readiness gates before project execution. |
| Post-Publish Diagnostics | `app.services.post_publish_diagnostics` | No-view/retention/packaging diagnostics, failure trace, recovery proposal. |
| Learning Candidates | `app.services.learning_candidates` | Candidate generation, evidence bundle, promotion eligibility queue. |
| Learning Review | `app.services.learning_review` | Human learning review workflow. |
| Approved Playbook | `app.services.approved_playbook` | Approved playbook entry creation/read path. |
| Provider Readiness | `app.services.provider_readiness` | Credential/readiness snapshot and guarded smoke metadata. |
| Prompt Registry | `app.services.prompt_registry` | Prompt template registry and render contract. |
| Prompt Audit | `app.services.prompt_audit` | Prompt context hash, audit snapshot helpers. |
| Runtime Provider Boundary | `app.services.runtime_provider_boundary` | Mock purge/provider boundary compatibility and M2 validation preflight. |
| Video Package Generation | `app.services.video_package_generation` | First scripted package generation. |
| Agent Rehearsal | `app.services.agent_rehearsal` | Full agent rehearsal entrypoint. |
| Package Generation Rehearsal | `app.services.package_generation_rehearsal` | Rehearsal preflight and package rehearsal flow. |
| Publish Handoff | `app.services.publish_handoff` | Human upload task ledger and publish handoff package. |
| Uploaded Video Backfill | `app.services.uploaded_video_backfill` | Uploaded video backfill/verification ledger. |
| Channel Contract Compiler | `app.services.channel_contract_compiler` | Channel init draft compiler into policy snapshot. |
| Channel Init Research | `app.services.channel_init_research` | Research-assisted channel init draft and review. |
| Channel Scope Authority | `app.services.channel_scope_authority` | Hierarchical scope, category, character, voice authority. |
| Channel Runtime Context | `app.services.channel_runtime_context` | EffectiveChannelRuntimeContextSnapshot compiler. |
| Agent Context Pack | `app.services.agent_context_pack` | AgentContextPack snapshot and digest builder. |
| Output Validation Gates | `app.services.output_validation_gates` | Agent output contracts and deterministic gates. |
| Packaging Handoff | `app.services.packaging_handoff` | Read-only package handoff and packaging gates. |
| Provider Wiring | `app.services.provider_wiring` | M2 provider matrix/readiness wiring without paid calls. |
| Controlled Memory | `app.services.controlled_memory` | Memory item/facet draft, approval, safety gates. |
| Vector Retrieval | `app.services.vector_retrieval` | SQL-filter-first vector-safe retrieval and digest builder. |
| Learning Loop | `app.services.learning_loop` | R3D7 closed learning loop, influence manifest, attribution. |
| Cost Firewall | `app.services.cost_firewall` | R3D8 render revision, cost estimate, paid approval, provider boundary. |

## Contracts and models

- Contracts/models giữ tên bảng và import phase-coded để tránh đổi DB truth.
- Semantic ownership được ghi trong docs; service facade là entrypoint đọc code.
- Không đổi Alembic history, table name, public API route.

## Owner boundary

- Domain service facade chỉ re-export implementation hiện hữu.
- Phase-coded modules vẫn import-compatible cho reports/tests/backward compatibility.
- Sau Runtime LTS có thể move implementation thật sang semantic modules, nhưng phải giữ wrapper cũ và migration history nguyên vẹn.
