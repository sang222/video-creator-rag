# Phase To Domain Map

DX1 giữ phase reports làm lịch sử audit. Runtime nên đọc theo semantic module trước.

## M-phase map

| Phase-coded module | Semantic domain | Semantic module | Status |
| --- | --- | --- | --- |
| `app.services.m5` | Daily Operations | `app.services.daily_operations` | Facade re-export; old import kept. |
| `app.services.m5` | Context Resolver | `app.services.context_resolver` | Facade re-export; old import kept. |
| `app.services.m5` | Project Admission | `app.services.project_admission` | Facade re-export; old import kept. |
| `app.services.m9` | Post-Publish Diagnostics | `app.services.post_publish_diagnostics` | Facade re-export; old import kept. |
| `app.services.m10` | Learning Candidates | `app.services.learning_candidates` | Facade re-export; old import kept. |
| `app.services.m11` | Learning Review | `app.services.learning_review` | Facade re-export; old import kept. |
| `app.services.m11` | Approved Playbook | `app.services.approved_playbook` | Facade re-export; old import kept. |
| `app.services.m12` | Provider Readiness | `app.services.provider_readiness` | Facade re-export; old import kept. |
| `app.services.m12_1` | Prompt Registry | `app.services.prompt_registry` | Facade re-export; old import kept. |
| `app.services.m12_1` | Prompt Audit | `app.services.prompt_audit` | Facade re-export; old import kept. |
| `app.services.m12_1r` + `app.services.m2` | Runtime Provider Boundary | `app.services.runtime_provider_boundary` | Facade re-export; old import kept. |
| `app.services.m12_2` | Video Package Generation | `app.services.video_package_generation` | Facade re-export; old import kept. |
| `app.services.m12_2r` | Publish Handoff | `app.services.publish_handoff` | Facade re-export; old import kept. |
| `app.services.m12_2r` | Uploaded Video Backfill | `app.services.uploaded_video_backfill` | Facade re-export; old import kept. |
| `app.services.m12_2p3` | Channel Contract Compiler | `app.services.channel_contract_compiler` | Facade re-export; old import kept. |
| `app.services.m12_2p3` | Channel Init Research | `app.services.channel_init_research` | Facade re-export; old import kept. |
| `app.services.m12_2` | Agent Rehearsal | `app.services.agent_rehearsal` | Facade re-export; old import kept. |
| `app.services.m12_2` | Package Generation Rehearsal | `app.services.package_generation_rehearsal` | Facade re-export; old import kept. |
| `app.services.m1` | Packaging Handoff | `app.services.packaging_handoff` | Facade re-export; old import kept. |
| `app.services.m2` | Provider Wiring | `app.services.provider_wiring` | Facade re-export; old import kept. |

## R3D map

| Phase-coded module | Semantic domain | Semantic module | Status |
| --- | --- | --- | --- |
| `app.services.r3d1` | Channel Scope Authority | `app.services.channel_scope_authority` | Facade re-export; old import kept. |
| `app.services.r3d2` | Channel Runtime Context | `app.services.channel_runtime_context` | Facade re-export; old import kept. |
| `app.services.r3d3` | Agent Context Pack | `app.services.agent_context_pack` | Facade re-export; old import kept. |
| `app.services.r3d4` | Output Validation Gates | `app.services.output_validation_gates` | Facade re-export; old import kept. |
| `app.services.r3d5` | Controlled Memory | `app.services.controlled_memory` | Facade re-export; old import kept. |
| `app.services.r3d6` | Vector Retrieval | `app.services.vector_retrieval` | Facade re-export; old import kept. |
| `app.services.r3d7` | Learning Loop | `app.services.learning_loop` | Facade re-export; old import kept. |
| `app.services.r3d8` | Cost Firewall | `app.services.cost_firewall` | Facade re-export; old import kept. |

## Compatibility status

- Old service imports remain valid and are covered by `tests/test_dx1_semantic_code_convention.py`.
- Contracts/models remain phase-coded at file level to avoid table/DTO churn; semantic ownership is documented here.
- No public endpoint path changed.
- No table renamed.
- No Alembic migration history rewritten.

## Remaining active phase-coded modules

- M0-M8/M10.1/M10.2/M10.3/M10.5/M11.1/M12.2S-adjacent modules remain phase-coded where they are historical infrastructure or dashboard/runtime support.
- After Runtime LTS, move implementation bodies into semantic modules incrementally and keep phase-coded wrappers.
