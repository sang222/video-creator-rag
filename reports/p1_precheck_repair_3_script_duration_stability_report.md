# P1-PRECHECK-REPAIR-3 — Script Duration Stability Report

Date: 2026-07-07

## Verdict

PRE_PROVIDER_PURCHASE_CHECK=BLOCKED  
OK_TO_BUY_PROVIDER_CREDENTIALS=false

Provider activation remains NO-GO.

## Files Changed

- `app/services/m12_2.py`
- `app/services/m12_1.py`
- `app/prompts/agents/system_deltas/channel_authority_agent.md`
- `app/prompts/agents/system_deltas/topic_idea_scoring_agent.md`
- `app/prompts/agents/system_deltas/script_planning_agent.md`
- `app/prompts/agents/system_deltas/script_writer_agent.md`
- `app/prompts/agents/system_deltas/script_rewrite_agent.md`
- `tests/qualification/test_m12_1_prompt_registry.py`
- `tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py`
- `tests/qualification/test_r3d4_agent_output_contract_gates.py`

Note: worktree also contains earlier P1/P2 repair files; no unrelated change was reverted.

## Root Cause

Original blocker package `6d01b9c5-79fc-4a87-8e8a-9a7829358fa0` exceeded the frozen long-form duration:

| Field | Value |
|---|---:|
| target_seconds | 450 |
| min_seconds | 405 |
| max_seconds | 495 |
| WPM | 140 |
| target_words | 1050 |
| min_words | 945 |
| max_words | 1155 |
| actual_words | 1506 |
| actual_seconds | 645.429 |
| fail_code | SCRIPT_DURATION_ABOVE_MAXIMUM |

Root cause: ScriptWriterAgent treated duration as prose guidance instead of a hard word-budget contract.

## Implemented Repair

- Added frozen `script_word_budget` contract from runtime duration model.
- ScriptPlanningAgent section budgets are normalized into structured `section_word_budgets`.
- ScriptWriterAgent receives exact target/min/max words, WPM, and section limits.
- Added deterministic post-writer word count and duration self-check refresh.
- Added one audited bounded over-max trim repair.
- Under-min expansion remains blocked unless safe new text generation is available; no silent filler is inserted.
- Hardened prompts against placeholder/truncated JSON, package-shaped planning output, and short under-budget scripts.
- Added bounded JSON syntax repair for duplicate standalone number after numeric property.

## Repair Audit Behavior

Over-max repair:

- `repair_type=bounded_script_duration_trim`
- `semantic_change_allowed=false`
- max attempts: 1
- preserves `hook_spec`, `promise_made`, `payoff_location`, sentence order, evidence refs
- records word count before/after
- does not call provider/media/upload

Under-min result:

- `repair_status=NOT_SAFE_TO_EXPAND_WITHOUT_NEW_TEXT_GENERATION`
- package remains BLOCKED

## Tests

Passed after current patch set:

- `PYTHONPATH=. .venv/bin/alembic heads`
- `PYTHONPATH=. .venv/bin/python -m compileall -q app`
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_1_prompt_registry.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py tests/qualification/test_r3d4_agent_output_contract_gates.py -q`
- `git diff --check`

Result: 67 passed, 1 warning.

Previously in this repair turn also passed:

- `tests/test_r3d10_runtime_lts_freeze.py` — 13 passed
- `tests/test_dx2_provider_stack_reconciliation.py` — 7 passed
- `tests/test_r3d9_runtime_dashboard_ops.py` — 2 passed
- `tests/test_r3d9_ux2_packaging_review_queue.py` — 24 passed
- combined M12/R3D/M1 qualification suite — 93 passed

## Real Ollama Reruns

| Run | Package | Result | Blocker |
|---|---|---|---|
| 3e | `0f5b065f-7610-4893-b228-c0ec30108492` | BLOCKED | under-min duration, 646 words |
| 3f | `3e90b3a7-7cc0-4702-abbb-1b4796ab4cba` | BLOCKED | under-min duration, 684 words |
| 3g | `3f304500-d63b-489a-aaa4-9141510215fb` | REVIEW_REQUIRED | ScriptPlanningAgent emitted package-shaped schema |
| 3h | `60a90a91-39e9-470c-9f65-b55af65d0e3d` | REVIEW_REQUIRED | TopicIdeaScoringAgent missing `artifact` and `operator_summary_vi` |

Latest package: `60a90a91-39e9-470c-9f65-b55af65d0e3d`

## No-Execution Proof

Latest rerun risk summary:

- `media_provider_calls_made=false`
- `upload_or_publish_calls_made=false`
- `no_provider_calls_confirmed=true`
- `old_provider_smoke_run=false`
- `mock_fallback_used=false`
- `dry_run_success_used=false`
- `local_fixture_success_used=false`
- `channel_config_mutated=false`
- `learning_auto_promotion=false`

No FinalMediaRef, HumanUploadTask, provider media call, Drive upload, YouTube upload/publish, or provider execution was created.

## Classification

- P1: Latest real rerun still blocked before provider plan preview.
- P1: TopicIdeaScoringAgent stochastic schema omission remains unresolved in real Ollama path.
- P1: Duration stability improved structurally but still not proven PASS in latest real path because the latest run stopped earlier.
- P2: Prompt compliance remains stochastic across Ollama runs; consider bounded retry with stricter same-agent prompt before full pipeline stop.

## Final

PRE_PROVIDER_PURCHASE_CHECK=BLOCKED  
OK_TO_BUY_PROVIDER_CREDENTIALS=false
