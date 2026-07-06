# PA1-PRECHECK - Ollama Production Rehearsal Before Provider Purchase

Date: 2026-07-06

## Verdict

PRE_PROVIDER_PURCHASE_CHECK=BLOCKED

OK_TO_BUY_PROVIDER_CREDENTIALS=false

Provider activation remains NO-GO.

This was a real Ollama text-path rehearsal through LLMRouter. It did not use mock, dry-run success, local fixture success, media generation, provider execution, Drive upload, YouTube upload, or upload task creation.

## Files changed

- Added `reports/pa1_precheck_ollama_production_rehearsal_report.md`.
- No app/backend/frontend implementation changed.
- No Channel Contract, ChannelProfileVersion, or EffectiveChannelRuntimeContextSnapshot mutation was performed by this task.

## Preflight

| Check | Result | Evidence |
|---|---:|---|
| Docker API/frontend/ollama/postgres | PASS | all services healthy |
| Runtime LTS live verifier | PASS | `GET /ops/runtime-lts-freeze-check`, `freeze_status=PASS`, `no_provider_media_upload_execution=true` |
| ProviderStackDriftGuard | PASS | canonical stack: `elevenlabs`, `luma_api`, `creatomate_growth_10k`, `pexels_api`; stale keys `[]`; `no_provider_call_made=true` |
| Provider/cost panel | PASS | `GET /provider-cost/81c48d7a-dfc3-4207-b585-744673491b59`, `will_execute=false`, `no_paid_provider_calls=true` |
| Ollama reachable | PASS | local Ollama tag list returned available cloud models |
| Real Ollama flags | PASS | `VCOS_LLM_PROVIDER=ollama`, `VCOS_LLM_REAL_EXECUTION_ENABLED=true`, `VCOS_LLM_ROUTER_REAL_SMOKE=true` |
| Rehearsal command real-run flags | PASS | `VCOS_ENABLE_REAL_OLLAMA_AGENT_RUN=true`, `VCOS_ENABLE_REAL_LLM_PACKAGE_RUN=true` |
| Media/provider/upload disabled | PASS | `VCOS_DISABLE_MEDIA_PROVIDER_CALLS=true`, `VCOS_DISABLE_UPLOAD_AND_PUBLISH=true`, `VCOS_DISABLE_OLD_PROVIDER_SMOKE=true` |

Container note: `vcos package rehearse-full-preflight` inside API container was blocked because the container does not include `git`. Host preflight with explicit real Ollama flags returned READY, so the rehearsal was run from host `.venv`.

## Rehearsal scope

| Field | Value |
|---|---|
| channel_key | `small-team-ai` |
| channel_id | `a77bc5dc-f7be-4ae0-8523-55fb846d64bd` |
| market | US / English |
| video_project_id | `372a1e94-3d3a-45e0-bab0-55f1916bb662` |
| topic | `How One Automation Can Save a Small Team 20 Hours Every Week` |
| mode | manual publish only, text artifacts only |
| stop boundary | before provider execution, media generation, render submit, Drive upload, YouTube upload, upload task creation |

Two fresh rehearsal packages were created. The closed PPP1 package `81c48d7a-dfc3-4207-b585-744673491b59` was not reused.

| Attempt | Package | Result | Stop reason |
|---|---|---|---|
| 1 | `516a0882-e91b-42a2-aa32-b218e60a52fb` | BLOCKED | `SCRIPT_FORBIDDEN_STYLE_USED` and timing mismatch found |
| 2 | `4bf488f4-aab0-4736-92a9-c68815934605` | REVIEW_REQUIRED | `TopicIdeaScoringAgent` schema validation rejected `risk_level` |

## Agent sequence and refs

Attempt 1 package `516a0882-e91b-42a2-aa32-b218e60a52fb`:

| Agent | ProviderAttempt | LLMRunSnapshot | PromptRenderRun | AgentOutputValidationRun |
|---|---|---|---|---|
| ChannelAuthorityAgent | `a867e8e6-7197-437d-b236-6244b0c4eb3a` | `7cc82f23-5848-4263-9f27-fbd17a1ea04c` | `a52eea19-39f7-499e-a7b9-cf9e9e41832f` | `18398b25-9e6f-4189-b69d-4b1bc8ba034e` |
| TopicIdeaScoringAgent | `aedfef7b-6990-44c0-bcf6-bdaca1e4a49d` | `f4f35a57-8d09-4872-95d0-aaf8a2c4b6e9` | `e47f40e1-7f61-4caf-9aa0-dc32f7b047ba` | `b0957985-93c4-4e13-8a59-370cf2d2c5c1` |
| ResearchPackSummarizer | `92c93ffb-c96c-45f5-a11b-1a9f8ca5708f` | `30dd7ea5-dcd7-4e6e-a36c-2873c4f2992f` | `558a9477-cef1-4362-ba0a-0079470bf5de` | `4079b725-f7df-4057-9e64-290446cc669a` |
| ScriptPlanningAgent | `bc7d3740-e3ce-46f3-915a-bf2a3483e659` | `3a095f83-8654-4de2-b952-cbbf9eecc0de` | `99aa4e30-f167-4a01-a985-556cb3dceca0` | `f7815372-b315-4ff4-b4fe-ced22b4979e9` |
| ScriptWriterAgent | `0dda9ca1-4740-44ee-af29-747c1146e16b` | `fd2bac91-c957-43bf-b259-c5d5f19b5b4a` | `d466fde6-6f5b-40f1-8df1-be3fe2c5c38b` | `b38c036d-63b2-4302-b582-74ebaa36b883` |

Attempt 2 package `4bf488f4-aab0-4736-92a9-c68815934605`:

| Agent | ProviderAttempt | LLMRunSnapshot | PromptRenderRun | AgentOutputValidationRun |
|---|---|---|---|---|
| ChannelAuthorityAgent | `0b3647d1-8874-4b93-bd24-b37164d2114b` | `5ff8b919-12f0-4fe7-b834-998cd89bc2e9` | `696fbb0a-c17a-44a9-8257-580f7ab1d727` | `ca08deca-f520-4752-8ac2-224222b6a459` |
| TopicIdeaScoringAgent | `0364f274-1d63-40e3-a110-03371ef3b34f` | `203587fc-4ffa-4b3b-a787-693d1c81e6fe` | `20fcc6ab-fad5-48b6-96c4-903d6c4a0697` | not created because schema validation stopped before canonical validation row |

All ProviderAttempt refs attached to these rehearsal packages use `provider_key=OLLAMA`, `operation_key=llm_router.chat`, `status=SUCCESS`. All LLMRunSnapshot refs use `run_mode=REAL`.

PromptAuditSnapshot refs:

- Attempt 1: `0015a830-534e-4a0d-8a84-6724845390b1`, `18387c6e-7125-4635-bf33-e136bd69ce87`, `3288dc4d-c55b-413b-9733-a4c7d6731863`, `491b7a00-38ef-47be-8f5b-f04db651af14`, `57d00545-5ef8-4fca-a88e-bc3e476e7dba`, `75011dcf-1ad9-434e-8000-d1d43b26f58f`, `9af8afc1-6962-477e-b735-b5701acbf320`, `cf4247b6-433d-4849-bac1-c73fbcd890ed`, `d204b962-d4c8-4360-9d24-f0046eef3735`, `ff275979-d2db-403b-a326-fda8310b054f`.
- Attempt 2: `446215bb-385e-4ce6-90bd-474bfe17cfa2`, `946fc132-5877-425b-9552-47975cf48f91`, `acd2c79c-d001-4772-a630-6092a1083655`, `dd5e3b87-c795-4a03-b7bb-b501d81fc87b`.

## AgentContextPackSnapshot and snowball checks

| Package | Agent | ContextPackSnapshot | Shape | Budget | Omitted count | Prompt chars | Result |
|---|---|---|---|---|---:|---:|---|
| attempt 1 | ChannelAuthorityAgent | `02b10969-921e-4fd9-91cd-782599ffc868` | OK | OK | 27 | 25184 | PASS |
| attempt 1 | TopicIdeaScoringAgent | `d5f5dcad-2b6c-4b08-a435-96da3a3b4ced` | OK | OK | 27 | 24925 | PASS |
| attempt 1 | ResearchPackSummarizer | `2f40c40d-635a-4ea9-b88a-c194a1420b96` | OK | OK | 27 | 25028 | PASS |
| attempt 1 | ScriptPlanningAgent | `70b47c02-7307-4487-a058-a0f9a7413e6a` | OK | OK | 25 | 26802 | PASS |
| attempt 1 | ScriptWriterAgent | `7d113837-fba9-4180-b57f-9bf3f0acfc3a` | OK | OK | 24 | 27640 | PASS |
| attempt 2 | ChannelAuthorityAgent | `e4cff7af-99c3-49c6-b095-477030578ba0` | OK | OK | 27 | 25228 | PASS |
| attempt 2 | TopicIdeaScoringAgent | `b37ada53-67b2-4e7e-8e75-1187c2869a47` | OK | OK | 27 | 24920 | PASS |

Snowball result for created agents:

- `full_previous_artifacts`: absent from rendered prompt payload.
- `raw_memory_text`: absent.
- raw `channel_contract_json:` blob marker: absent.
- raw `compiled_policy_snapshot_json:` blob marker: absent.
- raw `provider_readiness_snapshot_json`: absent.
- `latest_channel_settings` appeared only as guard metadata such as `latest_channel_settings_read=false`, not as latest mutable settings payload.
- Late agents received digest/hash/ref style context. No full previous artifact chain was injected.

SNOWBALL_CHECK=PASS_FOR_CREATED_AGENTS.

## Schema/envelope mismatch checks

| Package | Agent | Status | Repair attempts | Errors | Result |
|---|---|---|---|---|---|
| attempt 1 | all 5 agents | OK | bounded shape repair only where needed | none blocking | PASS |
| attempt 2 | ChannelAuthorityAgent | OK | none | none | PASS |
| attempt 2 | TopicIdeaScoringAgent | REVIEW_REQUIRED | `STATUS_SUCCESS_TO_OK_REPAIRED`, `TECHNICAL_APPENDIX_OBJECT_REPAIRED`, `semantic_change_allowed=false` | `risk_level is not allowed` | BLOCKED |

The prior schema drift class reproduced exactly: `risk_level` appeared although the base agent envelope does not allow it. The output was not treated as success. No broad `extra=allow` path was used.

Reason code: PRECHECK_BLOCKED_SCHEMA_MISMATCH.

## Timing mismatch checks

| Package | script_seconds_estimated | narration_seconds_estimated | srt_total_seconds | visual_plan_total_seconds | target/plan seconds | coverage_percent | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| attempt 1 | 450 | 71 | N/A | N/A | 450 plan, `long_form` | N/A | BLOCKED |
| attempt 2 | N/A | N/A | N/A | N/A | N/A | N/A | not reached due schema blocker |

Attempt 1 claimed a long-form plan but produced a short narration script. No SRT or visual plan existed because the run stopped before those stages. This is enough to block provider purchase recommendation.

Reason code: PRECHECK_BLOCKED_TIMING_MISMATCH.

## Hook first 3 seconds checks

| Package | first_3_seconds_script | first_3_seconds_visual | payoff_location | title_hook_alignment | Result |
|---|---|---|---|---|---|
| attempt 1 | not represented as dedicated hook fields | not generated | not generated | not fully checkable | BLOCKED/INCOMPLETE |
| attempt 2 | not reached | not reached | not reached | not reached | not reached due schema blocker |

Dedicated hook fields were not available before the stop boundary. Hook gates were therefore not sufficient to recommend provider purchase.

Reason code: PRECHECK_BLOCKED_HOOK_3S_INCOMPLETE.

## R3D4 gate result

Attempt 1:

| Gate | Status | Severity | Fail codes | Measurements |
|---|---|---|---|---|
| `script_duration_gate` | PASS | INFO | `[]` | sentence_count=13, actual_total_seconds=71.0 |
| `script_style_compliance_gate` | BLOCK | HIGH | `SCRIPT_FORBIDDEN_STYLE_USED` | forbidden style detected in generated narration |

Gate batch `87772487-eea7-41f0-a040-c9a60235284e`: `status=BLOCK`, `hard_block_count=1`, `review_required_count=0`.

Reason code: PRECHECK_BLOCKED_SCRIPT_STYLE_GATE.

Attempt 2 stopped at schema validation before R3D4 gate execution.

## R3D9 review queue summary

| Package | Review verdict | must_fix_count | items | upload_task_creation_allowed | no_provider_media_upload_execution |
|---|---|---:|---:|---:|---:|
| `516a0882-e91b-42a2-aa32-b218e60a52fb` | BLOCKED | 0 | 0 | false | true |
| `4bf488f4-aab0-4736-92a9-c68815934605` | REVIEW_REQUIRED | 0 | 0 | false | true |

No R3D9 proposed patch was created automatically for these fresh rehearsal packages.

## Provider plan dry validation

Provider plan preview was not reached because the rehearsal stopped before visual/provider readiness stages.

Read-model validation still confirms:

- canonical provider stack only: `elevenlabs`, `luma_api`, `creatomate_growth_10k`, `pexels_api`.
- ProviderStackDriftGuard PASS.
- provider/cost `will_execute=false`.
- media providers remain NOT_CONFIGURED / inactive for execution.
- no production FinalMediaRef created.
- no HumanUploadTask created.
- no YouTube upload/publish path used.

PROVIDER_PLAN_DRY_VALIDATION=NOT_REACHED_DUE_TO_PRECHECK_BLOCKERS.

## No-execution proof

| Check | Result |
|---|---:|
| rehearsal package ProviderAttempt refs | OLLAMA only |
| LLMRunSnapshot run mode | REAL |
| mock_fallback_used | false |
| dry_run_success_used | false |
| local_fixture_success_used | false |
| media_provider_calls_made | false |
| upload_or_publish_calls_made | false |
| old_provider_smoke_run | false |
| channel_config_mutated | false |
| learning_auto_promotion | false |
| MediaRenderJob for project | 0 |
| ProviderJobSnapshot SUBMITTED | 0 |
| PaidProviderCallLedger EXECUTED | 0 |
| FinalMediaRef for project | 0 |
| CloudMediaRef for project | 0 |
| HumanUploadTask for project | 0 |
| UploadedVideo for project | 0 |

No ElevenLabs, Luma, Creatomate, Pexels, Drive upload, YouTube upload, media render, final media creation, or upload task creation occurred.

## P0/P1/P2/P3

| Class | Items |
|---|---|
| P0 | none. No provider execution leak. No upload/publish leak. |
| P1 | `PRECHECK_BLOCKED_SCHEMA_MISMATCH`, `PRECHECK_BLOCKED_TIMING_MISMATCH`, `PRECHECK_BLOCKED_SCRIPT_STYLE_GATE`, `PRECHECK_BLOCKED_HOOK_3S_INCOMPLETE` |
| P2 | none new |
| P3 | none new |

`reports/production_pain_log.md` was not updated because no new P2/P3 item was found. The blockers are P1 pre-provider-purchase issues and need a scoped fix task.

## Commands run

| Command | Result |
|---|---|
| `PYTHONPATH=. .venv/bin/alembic heads` | PASS, head `0033_p1_pre_lts_disposition` |
| `PYTHONPATH=. .venv/bin/python -m compileall -q app` | PASS |
| `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` | PASS, 13 passed |
| `PYTHONPATH=. .venv/bin/pytest tests/test_dx2_provider_stack_reconciliation.py -q` | PASS, 7 passed |
| `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q` | PASS, 2 passed |
| `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_ux2_packaging_review_queue.py -q` | PASS, 24 passed |
| `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py -q` | PASS, 22 passed |
| `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_r3d4_agent_output_contract_gates.py -q` | PASS, 27 passed |

`git diff --check` will be run after this report is written.

## Decision

PRE_PROVIDER_PURCHASE_CHECK=BLOCKED

OK_TO_BUY_PROVIDER_CREDENTIALS=false

Next checkpoint recommendation:

1. Open a scoped P1 fix for agent envelope/schema contract drift, especially `TopicIdeaScoringAgent` emitting disallowed `risk_level`.
2. Fix long-form timing alignment so script outline, narration, SRT, visual plan, and provider plan share one target duration model.
3. Add or enforce dedicated hook first-3-second fields before provider plan preview.
4. Rerun PA1-PRECHECK with real Ollama text path only.
5. Only after PA1-PRECHECK=PASS should PA1-SMOKE provider purchase/credential activation be considered.
