# P1 Precheck Repair - Schema, Timing, Hook, Style

Date: 2026-07-07
Scope: VCOS / Video Creator Operating System, text-only Ollama rehearsal path.

## Final Verdict

PRE_PROVIDER_PURCHASE_CHECK=BLOCKED
OK_TO_BUY_PROVIDER_CREDENTIALS=false

Provider activation remains NO-GO. No ElevenLabs, Luma, Creatomate, Pexels, Drive upload, YouTube upload/publish, final media, HumanUploadTask, ProviderJobSnapshot SUBMITTED, or PaidProviderCallLedger EXECUTED was created.

## Files Changed

- app/contracts/m1.py
- app/contracts/m12_1.py
- app/core/config.py
- app/prompts/agents/system_deltas/channel_authority_agent.md
- app/prompts/agents/system_deltas/script_planning_agent.md
- app/prompts/agents/system_deltas/script_rewrite_agent.md
- app/prompts/agents/system_deltas/script_writer_agent.md
- app/prompts/agents/system_deltas/topic_idea_scoring_agent.md
- app/prompts/common/common_output_contract.md
- app/prompts/fixtures/eval_cases/base_envelope_valid.json
- app/prompts/registry/agents.yaml
- app/prompts/schemas/base_envelope.schema.json
- app/services/m1.py
- app/services/m10_1.py
- app/services/m12_1.py
- app/services/m12_2.py
- app/services/r3d4.py
- tests/qualification/test_m12_1_prompt_registry.py
- tests/qualification/test_m12_2_first_scripted_video_package.py
- tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py
- tests/qualification/test_r3d3_agent_context_pack.py
- tests/qualification/test_r3d4_agent_output_contract_gates.py

## Root Cause and Repair

| Blocker | Root cause | Repair result |
|---|---|---|
| PRECHECK_BLOCKED_SCHEMA_MISMATCH | Base envelope allowed/expected top-level `risk_level` drifted against agent output contract. | Removed top-level `risk_level` from base envelope/schema/registry/prompts. Added bounded repair moving top-level `risk_level` to `artifact.risk_assessment.risk_level` with `semantic_change_allowed=false`. Unknown top-level fields still fail. |
| PRECHECK_BLOCKED_TIMING_MISMATCH | Script duration gate used weak sentence timing and writer prompt did not enforce frozen duration/word budget. | Added frozen duration model, script duration contract, WPM/word-count based duration estimate, word budget measurement, and explicit `SCRIPT_DURATION_*` reason codes. Gate still blocks under-budget output. |
| PRECHECK_BLOCKED_HOOK_3S_INCOMPLETE | Hook fields were not required before downstream visual/provider planning. | Added `HookSpecGate` requiring first-3-second script/visual, promise, payoff, clickbait risk, visual relevance, title alignment. Missing fields block/review. |
| PRECHECK_BLOCKED_SCRIPT_STYLE_GATE | ScriptWriter prompt lacked compact forbidden-style digest and repair path. | Added forbidden-style prompt guard and one bounded style repair attempt for `SCRIPT_FORBIDDEN_STYLE_USED`; gate still blocks if forbidden style remains. |

## Rerun Result

Latest rerun package id: `3befa313-b0c0-48a3-ac70-97c75c8f7482`

Agent sequence reached:

1. ChannelAuthorityAgent: schema PASS, output REVIEW_REQUIRED.
2. TopicIdeaScoringAgent: BLOCKED at output validation, `JSON_PARSE_FAILED`.

Snowball status: PASS for created AgentContextPack snapshots before blocker. AgentContextPack was not changed.

## Tables

### Schema Mismatch

| Agent | Result | Notes |
|---|---|---|
| TopicIdeaScoringAgent contract tests | PASS | Top-level `risk_level` no longer passes silently; approved repair moves risk into artifact. |
| Latest real rerun | BLOCKED | Output was not parseable JSON, so validation stopped before schema contract success. |
| Unknown top-level field tests | PASS | Unknown top-level fields still fail. |
| `extra=allow` | PASS | Not introduced. |

### Timing

| Case | Result | Measurements |
|---|---|---|
| 450s target + ~71s script fixture | BLOCKS | `SCRIPT_DURATION_BELOW_MINIMUM` explicit. |
| Long-form in-range fixture | PASSES | Uses WPM/word-count estimate against frozen target/range. |
| Latest real rerun | NOT_REACHED | Stopped at TopicIdeaScoringAgent non-JSON before ScriptWriter. |

### Hook 3s

| Field | Gate behavior |
|---|---|
| `first_3_seconds_script` | Missing blocks. |
| `first_3_seconds_visual` | Missing blocks. |
| `promise_made` | Missing blocks. |
| `payoff_location` | Missing blocks. |
| `clickbait_risk=HIGH` | Review/block per policy path. |
| Valid HookSpec | Passes in tests. |

### R3D4 Gate

| Gate | Result |
|---|---|
| ScriptDurationGate | Strengthened; targeted tests pass. |
| HookSpecGate | Added; targeted tests pass. |
| ScriptStyleComplianceGate | Unweakened; style repair is bounded and re-gated. |

### Provider Plan Dry Validation

| Condition | Result |
|---|---|
| Text gate/schema block present | Provider preview NOT_REACHED. |
| Latest rerun | NOT_REACHED due `JSON_PARSE_FAILED`. |
| Execution | No provider network/media/upload execution. |

## No-Execution Proof

Runtime flags used: `VCOS_DISABLE_MEDIA_PROVIDER_CALLS=true`, `VCOS_DISABLE_UPLOAD_AND_PUBLISH=true`, `VCOS_DISABLE_OLD_PROVIDER_SMOKE=true`, Ollama-only LLM route.

Latest package risk summary reported:

- `media_provider_calls_made=false`
- `upload_or_publish_calls_made=false`
- `no_provider_calls_confirmed=true`
- `old_provider_smoke_run=false`
- `mock_fallback_used=false`
- `dry_run_success_used=false`
- `channel_config_mutated=false`
- `learning_auto_promotion=false`

## P Classification

| Priority | Status | Item |
|---|---|---|
| P0 | PASS | No provider/media/upload execution occurred. |
| P1 | PARTIAL | Schema/timing/hook/style repairs implemented and tested, but real rerun remains blocked. |
| P2 | OPEN | M12 fixture suite has regressions after stricter duration model; needs follow-up before merge. |
| P3 | OPEN | Ollama model adherence remains variable; latest real blocker is non-JSON TopicIdeaScoring output. |

## Commands

PASS:

- `PYTHONPATH=. .venv/bin/alembic heads`
- `PYTHONPATH=. .venv/bin/python -m compileall -q app`
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py tests/test_dx2_provider_stack_reconciliation.py tests/test_r3d9_runtime_dashboard_ops.py tests/test_r3d9_ux2_packaging_review_queue.py -q`
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_r3d4_agent_output_contract_gates.py -q`
- `git diff --check`

FAILED:

- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_1_prompt_registry.py tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py tests/qualification/test_r3d3_agent_context_pack.py tests/qualification/test_r3d4_agent_output_contract_gates.py tests/test_m1_channel_aware_packaging_handoff.py -q`
- Result: 9 failed, 72 passed. Remaining failures are M12/M12.2S package status expectations after stricter duration/provider-boundary gating.

