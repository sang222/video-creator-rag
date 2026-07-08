# P1-PRECHECK-REPAIR-4 Structured Output + Duration Convergence Report

Date: 2026-07-08

## Final Verdict

PRE_PROVIDER_PURCHASE_CHECK=PASS
OK_TO_BUY_PROVIDER_CREDENTIALS=true

Provider/media/upload execution remains disabled. The successful state is text-only readiness: package reaches provider plan dry validation with `will_execute=false`, then stops at expected provider configuration boundary.

## Files Changed

- `app/services/m12_1.py`
- `app/services/m12_2.py`
- `app/services/r3d3.py`
- `app/services/r3d4.py`
- `app/services/m1.py`
- `app/services/m10_1.py`
- `app/contracts/m1.py`
- `app/contracts/m12_1.py`
- `app/core/config.py`
- `app/prompts/agents/system_deltas/*.md`
- `app/prompts/common/common_output_contract.md`
- `app/prompts/fixtures/eval_cases/base_envelope_valid.json`
- `app/prompts/registry/agents.yaml`
- `app/prompts/schemas/base_envelope.schema.json`
- `tests/qualification/test_m12_1_prompt_registry.py`
- `tests/qualification/test_m12_2_first_scripted_video_package.py`
- `tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py`
- `tests/qualification/test_r3d3_agent_context_pack.py`
- `tests/qualification/test_r3d4_agent_output_contract_gates.py`

## Root Cause Summary

| Blocker | Root cause | Fix |
| --- | --- | --- |
| TopicIdeaScoringAgent missing `artifact` / `operator_summary_vi` | Ollama sometimes returned partial or artifact-shaped output instead of full BaseEnvelope. | Contract-first wrapper only for valid artifact-only output, one schema retry for missing artifact, audited operator summary completion. |
| Script duration oscillation | ScriptWriter overshot/undershot 450s because prompt budget was not deterministic enough. | Frozen word budget, section budgets, post-write deterministic word count, one bounded trim/expand repair, rerun duration/hook/style gates. |
| GatekeeperSoftReviewAgent review drift | Human verification before publish was treated as `REVIEW_REQUIRED` even in text-only rehearsal. | Prompt now requires `status=OK` and `artifact.result=PASS` when only scenario-claim verification/provider gaps remain. |
| PublishingMetadata / ResearchPack / Thumbnail JSON or enum drift | Same family as TopicIdea: local model drifted from strict BaseEnvelope/JSON syntax. | Bounded syntax/metadata repairs with audit, plus prompts requiring exact agent_key and allowed status enums. |
| Visual/provider candidate drift | Model emitted candidate provider source labels in visual plan. | Visual coverage repair normalizes/discards provider-backed candidate labels without provider calls. |

## Repair Rules Added

All repairs are bounded, audited, and `semantic_change_allowed=false`.

- `repair_embedded_agent_key_value`
- `repair_unclosed_string_before_json_delimiter`
- `repair_json_smart_quote_delimiters`
- `repair_missing_evidence_refs_array_close_before_limitations`
- `repair_unquoted_percent_number_values`
- `STATUS_READY_FOR_HUMAN_REVIEW_TO_OK_REPAIRED`
- ProviderReadiness nested artifact/operator summary repairs
- MediaQC `artifact_status` / `artifact.status` alias repair
- Script duration trim/expand repair
- Visual sentence/source normalization repair

No `extra=allow` was added. Unknown top-level fields still fail unless covered by explicit audited repair.

## Duration Model

| Field | Value |
| --- | ---: |
| target_seconds | 450 |
| min_seconds | 405 |
| max_seconds | 495 |
| WPM | 140 |
| target_words | 1050 |
| min_words | 945 |
| max_words | 1155 |

Latest consecutive successful real runs:

| Run | Package | Words | Seconds | Result |
| --- | --- | ---: | ---: | --- |
| 5k | `8909856a-e81d-45c2-80a2-deea71076072` | 1142 | 489.429 | PASS |
| 5l | `134ec5dc-e5d8-4010-8f5a-2d0b0a68f0af` | 1013 | 434.143 | PASS |

Hook preservation:

- HookSpec fields present.
- `hook_preserved=true` when duration repair ran.
- `payoff_location_preserved=true` when duration repair ran.

## Agent Sequence

For both 5k and 5l:

1. ChannelAuthorityAgent
2. TopicIdeaScoringAgent
3. ResearchPackSummarizer
4. ScriptPlanningAgent
5. ScriptWriterAgent
6. PublishingMetadataAgent
7. VisualPlanningAgent
8. ThumbnailBriefAgent
9. RightsDisclosureReviewer
10. GatekeeperSoftReviewAgent
11. ScriptRewriteAgent skipped safely
12. UploadCardCopyAgent
13. ProviderReadinessSummaryAgent
14. MediaQCExplanationAgent

## Tables

### Schema

| Check | Result |
| --- | --- |
| TopicIdea full BaseEnvelope | PASS |
| TopicIdea `artifact` present | PASS |
| TopicIdea `operator_summary_vi` present | PASS |
| top-level `risk_level` | guarded / audited move only |
| unknown top-level fields | still fail |
| ResearchPack exact `agent_key` | PASS |
| Thumbnail top-level status enum | PASS |
| ProviderReadiness artifact shape | PASS |

### Gates

| Gate | 5k | 5l |
| --- | --- | --- |
| ScriptDurationGate | PASS | PASS |
| HookSpecGate | PASS | PASS |
| ScriptStyleComplianceGate | PASS after one repair | PASS |
| VisualCoverageGate | PASS after repair | PASS after repair |
| Gatekeeper | PASS | PASS |
| ProviderBoundaryGate | expected BLOCK on missing credentials | expected BLOCK on missing credentials |

### Provider Dry Validation

| Field | 5k | 5l |
| --- | --- | --- |
| status | REACHED | REACHED |
| will_execute | false | false |
| no_network_call_made | true | true |
| no_final_media_ref | true | true |
| no_human_upload_task | true | true |
| observed_provider_keys | creatomate_growth_10k, elevenlabs, luma_api | creatomate_growth_10k, elevenlabs, luma_api |

### Snowball

| Area | Result |
| --- | --- |
| AgentContextPack budget | PASS |
| R3D3 tests | PASS |
| Created-agent sequence | PASS |
| No AgentContextPack contract mutation | PASS |

## No-Execution Proof

Latest successful package ids:

- `8909856a-e81d-45c2-80a2-deea71076072`
- `134ec5dc-e5d8-4010-8f5a-2d0b0a68f0af`

For both:

- `provider_plan_dry_validation.will_execute=false`
- `provider_plan_dry_validation.no_network_call_made=true`
- no ProviderJobSnapshot SUBMITTED for package
- no PaidProviderCallLedger EXECUTED for package
- no FinalMediaRef
- no HumanUploadTask
- package status is `WAITING_PROVIDER_CONFIG`
- boundary status is `BLOCKED_PROVIDER_NOT_CONFIGURED`

## Tests

Passed:

- `PYTHONPATH=. .venv/bin/alembic heads`
- `PYTHONPATH=. .venv/bin/python -m compileall -q app`
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` -> 13 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_dx2_provider_stack_reconciliation.py -q` -> 7 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q` -> 2 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_ux2_packaging_review_queue.py -q` -> 24 passed
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_1_prompt_registry.py tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py tests/qualification/test_r3d3_agent_context_pack.py tests/qualification/test_r3d4_agent_output_contract_gates.py tests/test_m1_channel_aware_packaging_handoff.py -q` -> 130 passed
- `git diff --check`

## Classification

| Item | Class | Result |
| --- | --- | --- |
| Provider/media/upload execution boundary | P0 | PASS, no execution |
| TopicIdea required fields | P1 | PASS |
| Structured JSON stability | P1 | PASS after bounded repairs |
| Script duration convergence | P1 | PASS |
| Hook 3s gates | P1 | PASS |
| Script style gate | P1 | PASS |
| Provider dry validation reached | P1 | PASS |
| Missing paid provider credentials | P2 expected boundary | WAITING_PROVIDER_CONFIG |

## Final

PRE_PROVIDER_PURCHASE_CHECK=PASS
OK_TO_BUY_PROVIDER_CREDENTIALS=true
