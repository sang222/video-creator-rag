# INT2 Small Team AI Package / Manual-Ops Trial Report

Date: 2026-07-04

## Verdict

BLOCKED.

Drive path fix và archive smoke PASS. Small Team AI package trial chạy an toàn và dừng `REVIEW_REQUIRED`. Tuy nhiên live Runtime LTS verifier đang `BLOCKED` do package cũ `fe563e52-ae78-4abb-acd1-3d45dfb9eea5` ở trạng thái `READY_FOR_MEDIA_PROVIDERS` nhưng thiếu effective context, AgentContextPackSnapshot và deterministic gates.

## Baseline identity

- Commit SHA: `3effb2a04e3107cb88bb8590afd939755d6f0d88`
- Migration head: `0031_r3d8_cost_firewall`
- Expected tag: `r3d10-runtime-lts-v1`
- Provider activation: NO-GO
- Auto upload/publish: NO-GO

## Runtime verifier

- Isolated regression test: PASS via `tests/test_r3d10_runtime_lts_freeze.py`
- Live API: `GET /ops/runtime-lts-freeze-check` => `BLOCKED`
- Blocker reason codes:
  - `EFFECTIVE_CONTEXT_SNAPSHOT_MISSING`
  - `AGENT_CONTEXT_PACK_SNAPSHOT_MISSING`
  - `DETERMINISTIC_GATE_MISSING`
- Evidence package: `fe563e52-ae78-4abb-acd1-3d45dfb9eea5`
- `no_provider_media_upload_execution`: true

## Small Team AI fixture

- company_id: `e0b7c806-b39e-4792-bf2e-7e8c6d6ca464`
- channel_id: `a77bc5dc-f7be-4ae0-8523-55fb846d64bd`
- channel_key: `small-team-ai`
- channel_name: `Small Team AI`
- channel_status: `active`
- primary_market: `US`
- language: `en`
- ChannelProfileVersion: `f5e45981-51eb-4c24-95a8-f9f5db761195`, status `active`
- CompiledChannelPolicySnapshot: `98074ce8-35c6-4349-93b4-afcbb3f2e151`, status `active`
- channel_contract_hash: `47ef8716145fb781471293d864f82cc8721a6e79f466a31e1ce0351c20b2b988`

Operator setup created for trial:

- RBAC operator role assigned to existing user fixture.
- ContentCategory: `816f2db3-3a2b-48a0-b28c-76383ae1b6ee`, key `int2-workflow-automation`, mode `NO_CHARACTER`
- Trial VideoProject: `372a1e94-3d3a-45e0-bab0-55f1916bb662`

No ChannelProfileVersion or Channel Contract mutation was made.

## Effective context evidence

- EffectiveChannelRuntimeContextSnapshot: `d1d0333a-d896-40aa-a6d8-a5766f339450`
- context_hash: `796ee1ec217eceed511ebdbbc2123aa4fb2f29161add0e8a35e415aeb1d25150`
- compile_status: `PASS`
- reason_codes: none
- category_id: `816f2db3-3a2b-48a0-b28c-76383ae1b6ee`
- character_binding_id: null
- market: `US`
- language: `en`
- voice_profile_id: null
- publish timing: manual publish only, timezone `America/New_York`

## Package trial evidence

Command path:

`package rehearse-full --stop-at video-generation`

Topic:

`How One Automation Can Save a Small Team 20 Hours Every Week`

Result:

- package_id: `48b7d58b-bf3c-4ca9-85c8-ff983b3865f9`
- package_status: `REVIEW_REQUIRED`
- provider_readiness_snapshot_id: `cfc80a66-ecb1-4d97-8e90-36556efecf85`
- next_action: `Sửa output schema/LLM response trước khi tiếp tục full rehearsal.`
- runtime_guard:
  - `real_ollama_agent_run=true`
  - `llm_router_only=true`
  - `no_media_provider_calls=true`
  - `no_upload_or_publish=true`

The package stopped before visual/provider/media agents because `ChannelAuthorityAgent` output failed schema validation:

- `status is not allowed`
- `technical_appendix must be an object`

This is a safe review stop, not provider/media execution.

## AgentContextPack / prompt evidence

- AgentContextPackSnapshot: `a7046814-1ebf-41dd-9db0-1e80007fa795`
- agent_key: `ChannelAuthorityAgent`
- lane: `cheap_structured`
- context_pack_hash: `5d54237382eff02be678f2e5ef3bdc43447dab37b93bd1c46f70a80132e93e22`
- prompt_context_hash: `0fef92d0cc4b186edbda18183073fa62b184efe3d9b540b60c1c562d3961cb60`
- prompt_chars_system: 2928
- prompt_chars_user: 19760
- prompt_tokens_estimated: 5672
- context_pack_chars: 9727
- artifact_digest_chars: 2
- evidence_digest_chars: 1708
- omitted_context_count: 27
- ContextPackShapeGate: `OK`

Prompt refs:

- PromptRenderRun: `9806a271-288d-4d94-99e2-4a151a905e90`
- PromptAuditSnapshot:
  - `d0ee03d8-891c-4180-af01-1575880f3cbc`
  - `155638d0-1830-4125-9375-d16b00903ea7`
- prompt_hash: `8df830ee194832be9e61f20fc1d5d72e868e04c5fc662968fc0b2158a7eca399`

Snowball check:

- Rendered prompt messages did not contain `full_previous_artifacts`, raw memory text, full `channel_contract_json`, or full `compiled_policy_snapshot_json`.
- Context pack records forbidden sections as omitted/forbidden policy metadata; it does not pass those sections as production payload.
- Later agents were not created because the trial stopped at `ChannelAuthorityAgent` schema validation.

## Ollama evidence

- LLMRunSnapshot: `ecb225a5-8995-453d-9a3b-bac6a30fb827`
- provider: `ollama`
- provider_key: `OLLAMA`
- model: `gpt-oss:20b-cloud`
- run_mode: `REAL`
- status: `SUCCESS`
- correlation_id: `m12-2s-full-agent-rehearsal-ChannelAuthorityAgent`
- ProviderAttempt: `efd25bf3-7a8c-4c24-9cf9-b87708b487e8`
- operation: `llm_router.chat`
- status: `SUCCESS`

No mock fallback or dry-run success was used.

## R3D4 / handoff / read-model evidence

- AgentOutputValidationRun: not created for this package because schema validation stopped inside prompt audit path.
- R3D4GateBatchRun: not created for this package because the trial stopped before deterministic gate phase.
- PackageOpsSummary read model:
  - package_status: `REVIEW_REQUIRED`
  - no_provider_media_upload_execution: true
  - next_action: manual review package
- ProviderCost read model:
  - `will_execute=false`
- Runtime trace:
  - channel trace snapshot: `d1d0333a-d896-40aa-a6d8-a5766f339450`
  - project trace snapshot: `d1d0333a-d896-40aa-a6d8-a5766f339450`
  - package trace snapshot: `d1d0333a-d896-40aa-a6d8-a5766f339450`
- Command Center forbidden actions include provider execution, YouTube upload, dashboard automation, Channel Contract mutation, and learning auto-promotion.

## Provider/cost boundary

ProviderStackDriftGuard:

- status: `PASS`
- canonical active keys:
  - `elevenlabs`
  - `luma_api`
  - `creatomate_growth_10k`
  - `pexels_api`
- stale provider keys: none active
- no_provider_call_made: true

No paid provider ledger execution was found.

## Drive archive smoke

Drive smoke ran after the path fix in the API container.

- MediaOffloadJob: `33450d42-3512-4746-96c1-4cfde93c5bc4`
- CloudMediaRef: `9e036f9d-5f0a-4cd6-954f-324c10df278f`
- job_state: `VERIFIED`
- media_type: `OTHER`
- upload_status: `VERIFIED`
- verification_status: `CHECKSUM_UNAVAILABLE`
- folder_path: `smoke_tests/2026-07-04`
- folder_path_mode: `SMOKE_TEST_UNSCOPED`
- render_package_id: null
- video_project_id: null

Proof:

- no nested `VCOS`
- no nested `VCOS Media`
- no `company_unknown/channel_unknown/project_unknown`
- archive/storage only
- not final media
- not publishable

## No-execution proof

- no ElevenLabs generation
- no Luma generation
- no Creatomate render submit
- no Pexels search/download
- no YouTube upload/publish/reupload
- no MediaRenderJob for trial project: 0
- no ProviderJobSnapshot `SUBMITTED`: 0
- no PaidProviderCallLedger `EXECUTED`: 0
- no disallowed provider attempts for canonical/stale media providers: 0
- no CloudMediaRef linked to trial package/project: 0
- Drive archive smoke CloudMediaRef is unscoped archive-only JSON: 1
- dashboard/read models remain manual/read-only

## Findings

P0: none found.

P1:

- `INT2-P1-001`: Live RuntimeLTSFreezeVerifier is `BLOCKED` due package `fe563e52-ae78-4abb-acd1-3d45dfb9eea5` in `READY_FOR_MEDIA_PROVIDERS` without effective context, AgentContextPackSnapshot, or deterministic gates. Stop before further pilot/operator execution until this runtime data invariant is triaged.

P2:

- `PPL-INT2-001`: Ollama full rehearsal stopped at `ChannelAuthorityAgent` schema validation, preventing inspection of later agents in this run. Logged in `reports/production_pain_log.md`.

P3: none.

## Commands run

- `PYTHONPATH=. .venv/bin/alembic heads` — PASS, `0031_r3d8_cost_firewall (head)`
- `PYTHONPATH=. .venv/bin/python -m compileall -q app` — PASS
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` — PASS
- `PYTHONPATH=. .venv/bin/pytest tests/test_dx2_provider_stack_reconciliation.py -q` — PASS
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q` — PASS
- `PYTHONPATH=. .venv/bin/pytest tests/test_m10_5_drive_archive_path_builder.py -q` — PASS
- Focused M10.5 offload tests — PASS
- `docker compose up -d --build api` — PASS
- `GET /ops/runtime-lts-freeze-check` — BLOCKED, evidence above

## ProductionPainLog

Added:

- `PPL-INT2-001`

## Next safe action

NO-GO for additional Manual-Ops execution until `INT2-P1-001` is triaged. Recommended next task: inspect package `fe563e52-ae78-4abb-acd1-3d45dfb9eea5` and decide whether it is stale historical data to retire from runtime status or a true freeze invariant regression requiring a P0/P1 patch window.
