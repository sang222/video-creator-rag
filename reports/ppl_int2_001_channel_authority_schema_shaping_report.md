# PPL-INT2-001 ChannelAuthority Schema Shaping Report

Date: 2026-07-04

## Status

PPL-INT2-001 is RESOLVED.

Evidence package:
- `81c48d7a-dfc3-4207-b585-744673491b59`

Evidence report:
- `reports/int2_resume_small_team_ai_full_manual_ops_report.md`

## Root Cause

Real Ollama outputs for ChannelAuthorityAgent and nearby envelope users were semantically usable but not always shaped as the strict `base_agent_envelope` contract required.

Reproduced first failure:
- package_id: `cba442b2-cd9c-4e91-b576-d8abf19ede18`
- agent: `ChannelAuthorityAgent`
- validation_result: `technical_appendix must be an object`
- package state: `REVIEW_REQUIRED`, safe stop.

Additional drift observed during bounded reruns:
- `limitations must be a list`
- `operator_summary_vi is required`
- `risk_level is not allowed`
- later agent drift: success status returned as `SUCCESS` instead of `OK`

## Files Changed

- `app/prompts/agents/system_deltas/channel_authority_agent.md`
- `app/prompts/common/common_output_contract.md`
- `app/services/m12_1.py`
- `app/services/r3d4.py`
- `tests/qualification/test_m12_1_prompt_registry.py`
- `tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py`

## Patch Behavior

Prompt shaping:
- ChannelAuthorityAgent now asks for exact BaseEnvelope shape.
- Explicitly forbids `artifact.status`.
- Requires `technical_appendix` as object, `{}` when empty.
- Requires `limitations` as list of strings.
- Requires non-empty `operator_summary_vi`.
- Uses `MEDIUM`, not `MODERATE`.
- Requires `artifact.decision`.

Common output contract:
- Clarifies that successful envelopes must use `OK`, not `SUCCESS` or `PASS`.

Bounded repair:
- Repair type: `normalize_envelope_metadata_shape`
- Recorded in PromptAuditSnapshot repair attempts.
- `semantic_change_allowed=false`
- One bounded pass in the existing repair path.
- Converts only narrow envelope metadata shape issues:
  - empty/missing `technical_appendix` -> `{}`
  - non-empty non-object `technical_appendix` -> object with `repaired_non_object_value`
  - string/object `limitations` -> list of strings
  - ChannelAuthority empty `operator_summary_vi` -> concise review summary from existing reason/next_action
  - `risk_level=MODERATE` -> `MEDIUM`
  - `confidence_label=UNKNOWN` -> `LOW`
  - `status=SUCCESS/PASS` -> `OK`

## Before / After

Before:
```json
{
  "agent_key": "ChannelAuthorityAgent",
  "status": "REVIEW_REQUIRED",
  "technical_appendix": "debug notes"
}
```

Validation:
- `REVIEW_REQUIRED`
- error: `technical_appendix must be an object`

After:
```json
{
  "agent_key": "ChannelAuthorityAgent",
  "status": "REVIEW_REQUIRED",
  "technical_appendix": {
    "repaired_non_object_value": "debug notes"
  }
}
```

Audit:
```json
{
  "repair_type": "normalize_envelope_metadata_shape",
  "semantic_change_allowed": false,
  "fields": ["technical_appendix"],
  "reason_codes": ["TECHNICAL_APPENDIX_OBJECT_REPAIRED"]
}
```

## Proof Of Strictness

The patch does not:
- relax business gates
- add broad `extra=allow`
- treat invalid LLM output as production success
- mock successful output
- call provider/media/upload services
- bypass human approval

Still fails safely:
- `status=ADMIT` remains invalid.
- unknown top-level fields remain invalid.
- `artifact` list/string remains invalid.
- missing required ChannelAuthority `artifact.decision` blocks via R3D4 contract validation.

## Trial Proof

Final fresh package after patch:
- package_id: `81c48d7a-dfc3-4207-b585-744673491b59`
- ChannelAuthorityAgent validation: OK
- TopicIdeaScoringAgent validation: OK
- ResearchPackSummarizer validation: OK
- ScriptPlanningAgent validation: OK
- ScriptWriterAgent validation: OK
- R3D4 reached and blocked on deterministic content gate `SCRIPT_FORBIDDEN_STYLE_USED`

This proves PPL-INT2-001 is not the active blocker anymore.

## Tests

Passed:
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_1_prompt_registry.py tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py -q` -> 29 passed
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_r3d4_agent_output_contract_gates.py -q` -> 27 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` -> 13 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_ux2_packaging_review_queue.py -q` -> 10 passed

Focused coverage added:
- ChannelAuthority non-object `technical_appendix` repairs with audit.
- Invalid status remains `REVIEW_REQUIRED`.
- Invalid artifact type remains `REVIEW_REQUIRED`.
- `limitations` object is repaired to list with audit.
- empty ChannelAuthority operator summary is repaired only from existing reason/next_action.
- `risk_level=MODERATE` normalizes to `MEDIUM`.
- shared `SUCCESS/PASS` success drift normalizes to `OK`.
- ChannelAuthority prompt explicitly forbids bad envelope shape.
- ChannelAuthority R3D4 output contract requires `artifact.decision`.

## No-Execution Proof

For final package `81c48d7a-dfc3-4207-b585-744673491b59`:
- non-Ollama ProviderAttempt count: 0
- RenderRevision count: 0
- ProviderJobSnapshot SUBMITTED count: 0
- PaidProviderCallLedger EXECUTED count: 0
- MediaRenderJob count for project: 0
- FinalMediaRef count for project: 0
- CloudMediaRef count for project: 0
- HumanUploadTask count for package: 0

## Resolution

PPL-INT2-001 can be marked RESOLVED because:
- the schema issue reproduced,
- the patch is bounded/audited/strict,
- a fresh rerun passed ChannelAuthorityAgent,
- the package reached R3D4/M1/R3D9-UX2 boundaries,
- remaining blockers are deterministic content/review items, not ChannelAuthority schema validation.
