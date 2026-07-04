# INT2-RESUME Small Team AI Full Manual Ops Report

Date: 2026-07-04

## Summary

INT2-RESUME ran from fresh Runtime LTS state against the existing Small Team AI channel only.

Result:
- Live Runtime LTS verifier: PASS.
- PPL-INT2-001 reproduced once at ChannelAuthorityAgent, then fixed with bounded schema/prompt shaping.
- Fresh rerun no longer stops at ChannelAuthorityAgent. It reaches ScriptWriterAgent and R3D4 deterministic gates.
- Current package remains safely BLOCKED by deterministic/content gates, not provider/media execution.
- R3D9-UX2 review queue is actionable and upload task creation is disabled.
- No provider media, upload, publish, reupload, render, or paid provider execution occurred.

## Preflight

Read-first sources were reviewed:
- `reports/p1_pre_lts_package_runtime_quarantine_report.md`
- `reports/int2_small_team_ai_package_manual_ops_trial_report.md`
- `reports/r3d9_ux2_packaging_review_queue_report.md`
- `reports/post_freeze_drive_archive_path_fix_report.md`
- `reports/int1_post_freeze_integration_smoke_ollama_drive_snowball_report.md`
- `docs/architecture/runtime_lts_v1.md`
- `docs/operations/post_freeze_protocol.md`
- `docs/operations/production_pain_log_policy.md`

Preflight commands passed:
- `PYTHONPATH=. .venv/bin/alembic heads` -> `0033_p1_pre_lts_disposition (head)`
- `PYTHONPATH=. .venv/bin/python -m compileall -q app`
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` -> 13 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q` -> 2 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_ux2_packaging_review_queue.py -q` -> 10 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_dx2_provider_stack_reconciliation.py -q` -> 7 passed

API rebuild:
- `docker compose up -d --build api` succeeded.

Live Runtime LTS verifier after rebuild:
- Endpoint: `GET /ops/runtime-lts-freeze-check`
- `freeze_status=PASS`
- `blocker_reason_codes=[]`
- `no_provider_media_upload_execution=true`
- Accepted warning: `PRE_LTS_PACKAGE_EXCLUDED_FROM_RUNTIME_SURFACE`

## Channel And Topic

Existing channel used:
- company_id: `e0b7c806-b39e-4792-bf2e-7e8c6d6ca464`
- company_slug: `small-team-ai`
- channel_id: `a77bc5dc-f7be-4ae0-8523-55fb846d64bd`
- channel_key: `small-team-ai`
- channel_name: `Small Team AI`

Existing project used:
- video_project_id: `372a1e94-3d3a-45e0-bab0-55f1916bb662`
- topic/title: `How One Automation Can Save a Small Team 20 Hours Every Week`
- effective_context_snapshot_id: `d1d0333a-d896-40aa-a6d8-a5766f339450`
- effective_context_hash: `796ee1ec217eceed511ebdbbc2123aa4fb2f29161add0e8a35e415aeb1d25150`

Research pack:
- `var/tmp/int2-resume/small_team_ai_20_hours_research_pack.md`
- Treated `20 hours` as operator scenario claim requiring human review.

## Trial Evidence

Initial guardrails:
- No new production channel was created.
- No Channel Contract mutation.
- No ChannelProfileVersion mutation.
- No EffectiveChannelRuntimeContextSnapshot mutation.
- No fake EffectiveContextSnapshot / AgentContextPackSnapshot / R3D4 gate run.

Trial progression:
- `5f73ebd7-c96b-44ed-872d-3e493c390f52` stopped before LLM: `EFFECTIVE_CONTEXT_SNAPSHOT_MISSING`.
- `4ca24f2b-...` stopped before real LLM: `OLLAMA_REAL_EXECUTION_DISABLED`.
- `cba442b2-cd9c-4e91-b576-d8abf19ede18` reproduced PPL-INT2-001 at ChannelAuthorityAgent: `technical_appendix must be an object`.
- Additional bounded envelope drift observed in later intermediate runs: `limitations must be a list`, `operator_summary_vi is required`, `risk_level is not allowed`.
- After patch, final fresh package `81c48d7a-dfc3-4207-b585-744673491b59` passed ChannelAuthorityAgent and reached R3D4.

Final package:
- package_id: `81c48d7a-dfc3-4207-b585-744673491b59`
- package_status: `BLOCKED`
- provider_readiness_snapshot_id: `e339fbb5-9726-45be-83aa-eb9014cfd61c`
- artifact keys: `admission_decision`, `topic_scores`, `research_notes`, `script_outline`, `narration_script`, `deterministic_gate_report`, `package_state_reducer`, `human_review_checklist`, context refs.

Created agent sequence:
- ChannelAuthorityAgent -> OK
- TopicIdeaScoringAgent -> OK
- ResearchPackSummarizer -> OK
- ScriptPlanningAgent -> OK
- ScriptWriterAgent -> OK, then deterministic gate BLOCK

Prompt/LLM refs:
- PromptRenderRun refs:
  - `7ae837f7-085e-4fec-8e18-92c3317f0d8e`
  - `bbd52062-5b70-4fb3-a62b-576dcb22538c`
  - `377e844d-a243-4b8f-b141-cd7b937f8511`
  - `aecb9408-16c3-4a8c-805b-7f32b47c7f06`
  - `ccc98326-16ef-421a-804e-3b72a7e899cc`
- AgentOutputValidationRun refs:
  - `89d6edeb-6d41-4370-bf51-15c812720108`
  - `2c0b8399-247e-4471-95a1-502670b881c5`
  - `33595802-09f1-4c32-b712-1b559c5eb5c0`
  - `6190f490-3d13-44be-9d75-ae799cd5d5bf`
  - `49366bbd-470b-409f-9815-b110ee1d19e5`
- LLMRunSnapshot refs, all `provider=ollama`, `provider_key=OLLAMA`, `run_mode=REAL`, `status=SUCCESS`:
  - `f667eace-852d-4f9e-8e44-464f02e8eb95`
  - `7958262a-7c38-44d9-aa62-94cdbddde07b`
  - `9460aa15-7827-4101-8fcb-0a56e859a6c3`
  - `6c51aa9a-b8a5-4a21-8f0a-aa59ee231575`
  - `b370657c-16fd-4ca8-9376-ab22346a6680`
- ProviderAttempt refs, all `OLLAMA llm_router.chat`:
  - `64f4951c-79ce-4882-89a5-403b2f253f0c`
  - `ba4158da-3883-4a1c-a251-44a4cbb62c81`
  - `0ecffbed-4e20-41ab-be72-ae1a828c9eb6`
  - `86062336-cd20-4cff-bc6b-d06b23aa99b8`
  - `ba4de58e-d81d-4323-b8bf-36070ec5ed46`

## Schema Shaping Result

PPL-INT2-001 reproduced:
- package_id: `cba442b2-cd9c-4e91-b576-d8abf19ede18`
- failing agent: `ChannelAuthorityAgent`
- validation error: `technical_appendix must be an object`
- package state: `REVIEW_REQUIRED`, safe stop.

Patch applied:
- Prompt shaping for ChannelAuthorityAgent exact BaseEnvelope output.
- Common output contract clarification: use `OK`, not `SUCCESS` or `PASS`.
- Bounded shared metadata-shape repair in `app/services/m12_1.py`.
- ChannelAuthorityAgent R3D4 contract now requires `artifact.decision`.

Repair behavior:
- Audited with `normalize_envelope_metadata_shape`.
- One bounded pass, `semantic_change_allowed=false`.
- Does not allow unknown fields.
- Does not allow broad `extra=allow`.
- Does not make invalid output silently pass.
- Invalid status such as `ADMIT` still returns `REVIEW_REQUIRED`.
- Artifact list/string still returns `REVIEW_REQUIRED`.

Detailed patch report:
- `reports/ppl_int2_001_channel_authority_schema_shaping_report.md`

## R3D3 / Context Safety

AgentContextPackSnapshot refs:
- ChannelAuthorityAgent: `f08f7433-2f6d-47fb-997f-0958ad8b4cdc`
- TopicIdeaScoringAgent: `56243cf1-5e17-491f-ba3b-ae90ecc62107`
- ResearchPackSummarizer: `693c5195-473e-4ccc-a8e7-6afbd5c42aaf`
- ScriptPlanningAgent: `3370ac0f-8d5c-4bc2-8d7e-8d47e147da0c`
- ScriptWriterAgent: `5e309452-5d73-4aae-9f5f-daf016de9d09`

Context safety:
- ContextPackShapeGate: `OK` for all created agents.
- PromptBudgetGate/budget_status: `OK` for all created agents.
- omitted_context_count recorded: 27, 27, 27, 25, 24.
- Rendered prompt payload and render_vars did not contain:
  - `full_previous_artifacts`
  - `raw_memory_text`
  - `channel_contract_json`
  - `compiled_policy_snapshot_json`
  - `provider_readiness_snapshot_json`
  - `raw_provider_readiness`
- These names appear only in `agent_context_contract.forbidden_context_sections`, not as included prompt payload.

Late agents created in this run:
- ScriptPlanningAgent
- ScriptWriterAgent

Late agents not reached because R3D4 blocked after ScriptWriterAgent:
- VisualPlanningAgent
- ProviderReadinessSummaryAgent
- MediaQCExplanationAgent

## R3D4 Evidence

R3D4GateBatchRun:
- id: `45c50e92-f5ea-437e-b174-a1890aceba64`
- trigger_agent_key: `ScriptWriterAgent`
- status: `BLOCK`
- hard_block_count: 1
- review_required_count: 0
- reducer: `{"status":"BLOCK","hard_block_count":1,"review_required_count":0}`

R3D4GateRun:
- `054ae8ad-8d03-4814-aa27-9029154dc601`: `script_duration_gate` -> PASS
- `bbb3296d-ee71-4096-83d9-fb293b7f5c97`: `script_style_compliance_gate` -> BLOCK
- fail_codes: `SCRIPT_FORBIDDEN_STYLE_USED`
- repair_hint: `Sửa script language/style theo contract.`

Package reducer:
- package_status: `BLOCKED`
- reason_codes: `SCRIPT_FORBIDDEN_STYLE_USED`
- source: `deterministic_gates`

## M1 Handoff Evidence

M1 handoff was read-only:
- package_status: `BLOCKED`
- manual_publish_only: true
- no_upload_or_publish_calls_made: true
- human_upload_task_id: null
- task_status: null
- no_upload_api_by_policy: true

M1 gate summary:
- overall_status: `REVIEW_REQUIRED`
- additional handoff gaps: hook promise/payoff, visual hook, title, subtitles, description, thumbnail brief, publish window.

## R3D9-UX2 Queue Evidence

Queue build was executed without approve/apply.

Queue summary:
- review_verdict: `BLOCKED`
- must_fix_count: 9
- upload_task_creation_allowed: false
- next_safe_action: create/wait for proposed patches for failing gates.

Queue items:
- `bdd8cc64-1541-4f4f-80a0-108711ddc6b7`: `script_style_compliance_gate` / `SCRIPT_FORBIDDEN_STYLE_USED` / BLOCK / `PENDING_PATCH` / `NEEDS_PROPOSED_PATCH`
- `7512c25b-3bcc-4931-9980-735b292fcd49`: `HookTruthfulnessGate` / `HOOK_PROMISE_MISSING` / REVIEW_REQUIRED / `PENDING_PATCH`
- `f1da67bc-f013-4e8c-a034-d0c2d522fbef`: `HookPayoffGate` / `HOOK_PROMISE_MISSING` / REVIEW_REQUIRED / `PENDING_PATCH`
- `3188bf68-e097-4c33-9065-d0fe293c8264`: `VisualHookRelevanceGate` / `HOOK_VISUAL_MISSING` / REVIEW_REQUIRED / `PENDING_PATCH`
- `b470f179-25ea-4062-a8d4-c289d655b91f`: `TitlePromiseGate` / `TITLE_MISSING` / REVIEW_REQUIRED / `PENDING_PATCH`
- `05385cea-895d-4e70-a2f6-4eba7e92925a`: `CaptionCoverageGate` / `SUBTITLE_REFS_MISSING` / REVIEW_REQUIRED / `PENDING_HUMAN_REVIEW`
- `5a88fbb9-f222-4382-a5b1-ed05ca83cdfb`: `DescriptionCompletenessGate` / `DESCRIPTION_MISSING` / REVIEW_REQUIRED / `PENDING_PATCH`
- `0ec23fae-a01f-4f2c-b375-4a7a8cd6a0d3`: `ThumbnailTruthfulnessGate` / `THUMBNAIL_BRIEF_MISSING` / REVIEW_REQUIRED / `PENDING_PATCH`
- `5a5a979b-a435-4905-8c6f-2d36688c360d`: `PublishTimingComplianceGate` / `PUBLISH_WINDOW_MISSING` / REVIEW_REQUIRED / `PENDING_HUMAN_REVIEW`

Proposed patches created:
- `52927ad4-8e9f-459d-a058-41c5f8876212`
  - queue_item_id: `05385cea-895d-4e70-a2f6-4eba7e92925a`
  - type: `SUBTITLE_HANDOFF`
  - source: `DETERMINISTIC_SERVICE`
  - status: `READY_FOR_REVIEW`
  - requires_human_approval: true
- `238c9f86-8c72-428b-ba32-6b1c3be0ab7e`
  - queue_item_id: `5a5a979b-a435-4905-8c6f-2d36688c360d`
  - type: `PUBLISH_TIMING_OVERRIDE`
  - source: `DETERMINISTIC_SERVICE`
  - status: `READY_FOR_REVIEW`
  - requires_human_approval: true

Human approval boundary:
- approval_count: 0
- apply_count: 0
- No live package proposed patch was approved.
- No live package proposed patch was applied.

UX proof:
- Review verdict is meaningful: `BLOCKED`.
- Must-fix items include issue/why/fix/section copy.
- Raw gate evidence remains technical/read-only.
- Upload task button/read-model disabled while unresolved BLOCK/REVIEW_REQUIRED queue exists.

## No-Execution Proof

For package `81c48d7a-dfc3-4207-b585-744673491b59`:
- non-Ollama ProviderAttempt count: 0
- RenderRevision count: 0
- ProviderJobSnapshot SUBMITTED count: 0
- ProviderJobSnapshot total count: 0
- PaidProviderCallLedger EXECUTED count: 0
- PaidProviderCallLedger total count: 0
- HumanUploadTask count: 0

For video_project_id `372a1e94-3d3a-45e0-bab0-55f1916bb662`:
- MediaRenderJob count: 0
- FinalMediaRef count: 0
- CloudMediaRef count: 0

Explicitly not executed:
- ElevenLabs generation: none
- Luma generation: none
- Creatomate render submit: none
- Pexels search/download: none
- YouTube upload/publish/reupload: none
- Provider/media render job: none
- Paid provider execution: none

Drive archive:
- No Drive upload was created by this trial package.
- Latest unrelated archive smoke ref `9e036f9d-5f0a-4cd6-954f-324c10df278f` is `media_type=OTHER`, JSON file, unscoped path `smoke_tests/2026-07-04`.

## Classification

- P0: none.
- P1: none.
- P2: PPL-INT2-001 schema shaping maintenance, resolved in this run.
- P3: none logged.

Current package BLOCK is content/gate state, not Runtime LTS or provider execution failure.

## Human Next Actions

Before any manual upload handoff can continue:
- Create or request a proposed patch for `SCRIPT_FORBIDDEN_STYLE_USED`.
- Review/request changes for hook promise/payoff and visual hook gaps.
- Provide title and description metadata.
- Review proposed subtitle handoff patch `52927ad4-8e9f-459d-a058-41c5f8876212`.
- Provide/review thumbnail brief.
- Review proposed publish timing override patch `238c9f86-8c72-428b-ba32-6b1c3be0ab7e`.
- Rerun deterministic gates after human-approved patch application.

Manual pilot status:
- Can continue only at human review/patch workflow.
- Cannot continue to upload task creation yet.
- Must not proceed to media generation/provider execution/upload/publish.

## Final Verification Commands

Passed:
- `PYTHONPATH=. .venv/bin/alembic heads`
- `PYTHONPATH=. .venv/bin/python -m compileall -q app`
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` -> 13 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q` -> 2 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_ux2_packaging_review_queue.py -q` -> 10 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_dx2_provider_stack_reconciliation.py -q` -> 7 passed
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_1_prompt_registry.py tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py -q` -> 29 passed
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_r3d4_agent_output_contract_gates.py -q` -> 27 passed
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_2r_publish_handoff_ledger.py -q` -> 14 passed

Final hygiene:
- `git diff --check` -> passed
