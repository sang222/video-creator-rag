# PA1-PRECHECK Ollama Production Rehearsal Rerun 3

Date: 2026-07-07  
Channel: `small-team-ai`  
Topic: `How One Automation Can Save a Small Team 20 Hours Every Week`

## Final Verdict

PRE_PROVIDER_PURCHASE_CHECK=BLOCKED  
OK_TO_BUY_PROVIDER_CREDENTIALS=false

Provider activation remains NO-GO.

## Latest Package

`60a90a91-39e9-470c-9f65-b55af65d0e3d`

Status: `REVIEW_REQUIRED`  
Next action: fix LLM output schema before continuing full rehearsal.

## Agent Sequence

| Agent | Reached | Validation |
|---|---:|---|
| ChannelAuthorityAgent | yes | OK |
| TopicIdeaScoringAgent | yes | REVIEW_REQUIRED |
| ResearchPackSummarizer | no | NOT_REACHED |
| ScriptPlanningAgent | no | NOT_REACHED |
| ScriptWriterAgent | no | NOT_REACHED |
| PublishingMetadataAgent | no | NOT_REACHED |
| VisualPlanningAgent | no | NOT_REACHED |
| ProviderReadinessSummaryAgent | no | NOT_REACHED |

## Schema Table

| Agent | Result | Notes |
|---|---|---|
| ChannelAuthorityAgent | PASS | BaseEnvelope valid; code fence stripped with audited syntax repair |
| TopicIdeaScoringAgent | BLOCKED/REVIEW_REQUIRED | Missing required `artifact` and `operator_summary_vi`; cannot semantic-repair safely |

Latest blocker:

`SCHEMA_VALIDATION_REVIEW_REQUIRED`

Errors:

- `Missing required fields: ['artifact', 'operator_summary_vi']`
- `operator_summary_vi is required`

## Snowball Table

| Check | Result |
|---|---|
| Created agent context packs before blocker | PASS |
| Context pack hashes recorded | PASS |
| AgentContextPack mutation | none |
| Snowball regression evidence | none observed |

## Timing Table

| Run | Package | Actual Words | Actual Seconds | Gate |
|---|---|---:|---:|---|
| 3e | `0f5b065f-7610-4893-b228-c0ec30108492` | 646 | 276.857 | BLOCK below min |
| 3f | `3e90b3a7-7cc0-4702-abbb-1b4796ab4cba` | 684 | 293.143 | BLOCK below min |
| 3h | `60a90a91-39e9-470c-9f65-b55af65d0e3d` | n/a | n/a | NOT_REACHED |

Frozen contract:

| Field | Value |
|---|---:|
| target_seconds | 450 |
| min_seconds | 405 |
| max_seconds | 495 |
| WPM | 140 |
| target_words | 1050 |
| min_words | 945 |
| max_words | 1155 |

## Hook Table

| Run | HookSpec |
|---|---|
| 3e | PRESENT before duration block |
| 3f | PRESENT before duration block |
| 3h | NOT_REACHED due TopicIdea schema blocker |

## R3D4 Gate Result

Latest package did not reach R3D4 script gates because TopicIdeaScoringAgent schema validation stopped the chain.

Most recent script-gate evidence:

- package `3e90b3a7-7cc0-4702-abbb-1b4796ab4cba`
- `SCRIPT_DURATION_BELOW_MINIMUM`
- `SCRIPT_WORD_BUDGET_BELOW_MINIMUM`
- duration repair attempted once
- expansion repair not performed because safe new text generation was not available in the deterministic repair path

## Provider Plan Dry Validation

Result: NOT_REACHED

Reason: text gates/schema validation did not pass.

Expected provider plan constraints remain enforced:

- `will_execute=false` only when reached
- no network call
- no FinalMediaRef
- no HumanUploadTask

## No-Execution Proof

Latest `risk_limitations_summary`:

- `media_provider_calls_made=false`
- `upload_or_publish_calls_made=false`
- `no_provider_calls_confirmed=true`
- `old_provider_smoke_run=false`
- `mock_fallback_used=false`
- `dry_run_success_used=false`
- `local_fixture_success_used=false`
- `channel_config_mutated=false`
- `learning_auto_promotion=false`

No ElevenLabs, Luma, Creatomate, Pexels, Drive, YouTube, media generation, final media creation, HumanUploadTask, ProviderJobSnapshot SUBMITTED, or PaidProviderCallLedger EXECUTED occurred.

## P0/P1/P2/P3

| Class | Item |
|---|---|
| P1 | TopicIdeaScoringAgent omitted required schema fields in latest real Ollama run |
| P1 | Provider purchase remains blocked |
| P1 | Duration stability not proven PASS in latest run |
| P2 | Ollama prompt adherence remains stochastic despite hardening |
| P3 | Report/check output warnings are limited to existing Starlette deprecation warning |

## Commands

Passed after latest patches:

- `PYTHONPATH=. .venv/bin/alembic heads`
- `PYTHONPATH=. .venv/bin/python -m compileall -q app`
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_1_prompt_registry.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py tests/qualification/test_r3d4_agent_output_contract_gates.py -q`
- `git diff --check`

Earlier in this repair run:

- `tests/test_r3d10_runtime_lts_freeze.py -q`
- `tests/test_dx2_provider_stack_reconciliation.py -q`
- `tests/test_r3d9_runtime_dashboard_ops.py -q`
- `tests/test_r3d9_ux2_packaging_review_queue.py -q`
- combined M12/R3D/M1 suite

## Final

PRE_PROVIDER_PURCHASE_CHECK=BLOCKED  
OK_TO_BUY_PROVIDER_CREDENTIALS=false
