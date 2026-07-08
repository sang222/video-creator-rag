# P1-PRECHECK-REPAIR-2 — TopicIdea JSON Hardening + Fixture Alignment

Date: 2026-07-07

## Verdict

PRE_PROVIDER_PURCHASE_CHECK=BLOCKED
OK_TO_BUY_PROVIDER_CREDENTIALS=false

Provider activation remains NO-GO.

## Root Cause

Latest original blocker for package `3befa313-b0c0-48a3-ac70-97c75c8f7482` was `TopicIdeaScoringAgent` `JSON_PARSE_FAILED`.

Observed class:
- Model emitted non-JSON prose / intermediate JSON before the final BaseEnvelope.
- Existing repair selected first `{` through last `}`, merging unrelated objects and failing parse.
- TopicIdea prompt did not strongly enough require JSON-only BaseEnvelope.

## Fix Summary

Files changed:
- `app/services/m12_1.py`
- `app/services/m12_2.py`
- `app/services/r3d3.py`
- `app/prompts/common/common_output_contract.md`
- `app/prompts/agents/system_deltas/channel_authority_agent.md`
- `app/prompts/agents/system_deltas/research_pack_summarizer.md`
- `app/prompts/agents/system_deltas/script_writer_agent.md`
- `app/prompts/agents/system_deltas/script_rewrite_agent.md`
- `app/prompts/agents/system_deltas/topic_idea_scoring_agent.md`
- `tests/qualification/test_m12_1_prompt_registry.py`
- `tests/qualification/test_m12_2_first_scripted_video_package.py`
- `tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py`
- `tests/qualification/test_r3d3_agent_context_pack.py`

Implemented:
- TopicIdea prompt hardening: JSON-only, BaseEnvelope-only, no markdown/prose, no top-level `risk_level`.
- Bounded JSON extraction: only balanced BaseEnvelope-looking objects; audit reason `BASE_ENVELOPE_OBJECT_EXTRACTED_FROM_TEXT`; `semantic_change_allowed=false`.
- Bounded syntax repairs: fenced JSON, trailing commas, stray `text: "...": {"approx_seconds": ...}` shape, missing final JSON closing delimiters.
- Strict JSON literal prompt guard: no formulas like `60 / 140` inside JSON.
- ResearchPackSummarizer compact artifact guidance to avoid copying provider maps into output.
- ChannelAuthority required-field skeleton to reduce partial-envelope output.
- Duration contract and prompt updates; deterministic post-parse duration self-check recompute.
- Visual context budget compacting for long scripts.

Forbidden changes not made:
- No `extra=allow`.
- No broad schema relaxation.
- No mock fallback.
- No provider/media/upload execution.
- No Channel Contract / ChannelProfileVersion / EffectiveContext mutation.
- No commit/tag.

## Repair Rules

Allowed repair is syntax/shape only:
- extract a complete BaseEnvelope object from surrounding prose only when it is balanced and recognizable;
- strip full markdown code fence only when content is JSON;
- remove trailing commas;
- repair the known malformed sentence timing object;
- append missing EOF closing delimiters only when delimiter stack is clean and short.

All repair attempts record `semantic_change_allowed=false`.

## Test Result

Passed:
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_1_prompt_registry.py -q` -> 15 passed
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py -q` -> 26 passed
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_r3d3_agent_context_pack.py -q` -> 7 passed

Required suite final:
- `alembic heads` -> `0033_p1_pre_lts_disposition (head)`
- `compileall -q app` -> PASS
- R3D10 -> 13 passed
- DX2 -> 7 passed
- R3D9 ops -> 2 passed
- R3D9 UX2 -> 24 passed
- combined M12/R3D3/R3D4/M1 -> 90 passed

## Latest Real Ollama Rerun

Package id: `6d01b9c5-79fc-4a87-8e8a-9a7829358fa0`

Agent sequence:
- ChannelAuthorityAgent: OK
- TopicIdeaScoringAgent: OK
- ResearchPackSummarizer: OK
- ScriptPlanningAgent: OK
- ScriptWriterAgent: OK

Schema table:
- TopicIdea JSON parse: PASS
- top-level `risk_level`: not present
- unknown top-level fields: not accepted by schema
- metadata repair: bounded/audited

Snowball table:
- Created agent context packs: PASS
- No AgentContextPack contract regression detected in tests

Timing table:
- target_seconds: 450
- allowed_range_seconds: 405-495
- actual_total_seconds: 645.429
- narration_word_count: 1506
- coverage_ratio: 1.4343
- result: BLOCK
- reason: `SCRIPT_DURATION_ABOVE_MAXIMUM`

Hook table:
- HookSpec present in script output
- first_3_seconds_script: present
- first_3_seconds_visual: present
- promise_made: present
- payoff_location: present
- clickbait_risk: present
- result: NOT_REACHED in final gate summary because duration gate blocked downstream continuation

R3D4 result:
- BLOCK
- fail_codes: `SCRIPT_DURATION_ABOVE_MAXIMUM`

Provider plan dry validation:
- NOT_REACHED
- reason: text gate blocked before provider dry validation

No-execution proof:
- media_provider_calls_made=false
- upload_or_publish_calls_made=false
- no_provider_calls_confirmed=true
- old_provider_smoke_run=false
- mock_fallback_used=false
- dry_run_success_used=false
- channel_config_mutated=false
- learning_auto_promotion=false

## Classification

P1:
- ScriptWriterAgent duration output remains unstable on real Ollama: final rerun overshot frozen 450s target.

P2:
- Fixture/status expectations aligned with stricter duration/provider-boundary gates.
- Visual context digest compacted for longer scripts.

P3:
- Prompt clarity improvements for JSON literal-only output.

## Final

TopicIdeaScoringAgent no longer fails at `JSON_PARSE_FAILED`.

PRE_PROVIDER_PURCHASE_CHECK=BLOCKED
OK_TO_BUY_PROVIDER_CREDENTIALS=false
