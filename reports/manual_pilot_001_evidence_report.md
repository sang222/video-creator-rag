# Manual Pilot 001 Evidence Report

Ngày: 2026-07-04

## Pilot Verdict

`PASS`

Runtime LTS v1 vận hành được qua manual-ops/read-model và giữ nguyên post-freeze boundary. Package fixture có R3D4 `BLOCK` có chủ đích; pilot vẫn PASS vì runtime chặn an toàn, không lên media-ready và không kích hoạt provider/media/upload.

## Baseline Identity

- Commit SHA: `5ffa56ac2fb204bb7b3d11388c2f0803cbfbb4f4`
- Expected tag: `r3d10-runtime-lts-v1`
- Migration head: `0031_r3d8_cost_firewall (head)`
- Runtime state: Runtime LTS v1 frozen after R3D10
- Provider activation: NO-GO
- Auto upload/publish: NO-GO
- Freeze verifier result: PASS
- Verifier flag: `no_provider_media_upload_execution=true`

## Commands Run

| Command | Result |
| --- | --- |
| `PYTHONPATH=. .venv/bin/alembic heads` | PASS, `0031_r3d8_cost_firewall (head)` |
| `PYTHONPATH=. .venv/bin/python -m compileall -q app` | PASS |
| `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` | PASS, `8 passed, 1 warning` |
| `PYTHONPATH=. .venv/bin/pytest tests/test_dx1_semantic_code_convention.py tests/test_dx2_provider_stack_reconciliation.py -q` | PASS, `13 passed` |
| `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q` | PASS, `2 passed, 1 warning` |
| `PYTHONPATH=. .venv/bin/pytest tests/test_migration.py -q` | PASS, `2 passed` |
| `git diff --check` | PASS |

Warnings: Starlette `httpx` TestClient deprecation only. Frontend commands not run because this task did not touch frontend files.

## Selected Safe Fixture

- Fixture: `tests.test_r3d9_runtime_dashboard_ops._fixture`
- Scope: isolated pytest-style transient database, created for evidence capture and dropped after capture.
- No production channel/project/package was created.
- No agent/provider/media/upload runtime job was executed.

## Fixture IDs

| Ref | ID |
| --- | --- |
| company_id | `431414ec-4533-43fe-81db-62f1d429338d` |
| channel_id | `dcf81a4d-f682-4bcb-8efd-98780369e7df` |
| channel_profile_version_id | `5fdcf2d7-e13c-4efa-b0d5-1f8c63bd4b8f` |
| compiled_policy_snapshot_id | `f846ecbb-715d-462a-8a0b-7e401743a919` |
| video_project_id | `273177ce-faff-4993-a8f1-4d45dc323a09` |
| package_id | `6fc8bb34-a0af-4ffe-8a6c-a583d77fd5e3` |
| effective_context_snapshot_id | `e22861c7-7c73-4c34-9635-02ab9f98139b` |
| uploaded_video_id | `3d000601-e5e8-4cff-aa1f-bd538d711c7f` |
| retrieval_manifest_id | `61ae5079-bfb0-41e2-914d-85b05d932f68` |
| memory_influence_manifest_id | `931183bb-2155-4c5b-af5e-2f178722f03c` |
| quality_delta_attribution_id | `3728c72e-db52-45b6-a0e8-1d1d35f56bb2` |

## Runtime LTS Freeze Check

- Service: `RuntimeLTSFreezeVerifier`
- API: `GET /ops/runtime-lts-freeze-check`
- Service status: PASS
- API status: PASS
- Blockers: none
- Warnings: none
- Verified components: 30
- Evidence refs: 38
- `no_provider_media_upload_execution`: true

Verified component sample:
- `channel_runtime_authority`
- `agent_context_pack_snapshot_required`
- `prompt_digest_ref_hash_only`
- `deterministic_gate_freeze_rules`
- `provider_stack_drift_guard`
- `provider_execution_flags_default_false`
- `r3d9_ops_endpoints_get_only`
- `r3d9_frontend_no_job_control_buttons`
- `no_youtube_upload_api_route`

## Channel / Runtime Authority Evidence

- Channel Contract hash: `a8c6d5f5183f4be0d079b62e8412133a41ae5ab6b49915ff72f703280f058b87`
- Compiled policy snapshot hash: `5eb37a6fbecf5cf2799423db11de2cf8ec06796becd655a7f3a8736a64a39730`
- Effective context hash: `15f4407c8ad8511d7d8667dfb27ae6069d69f27f093e6e73358ecc6b51ce310f`
- Category id: `184f40f0-0772-4855-83d5-97619506b117`
- Character binding: null
- Market/locale/language: `US`, `en-US`, `en`
- Voice profile: null; voice context language `vi`
- Thumbnail style: `high contrast`
- Publish timing policy: `manual_publish_only=true`, timezone `Asia/Ho_Chi_Minh`
- Provider boundary: `provider_real_execution_enabled=false`, `budget_cap=manual-only`
- Runtime trace says `latest_mutable_settings_used=false`

Conclusion: Channel Contract + EffectiveChannelRuntimeContextSnapshot remain runtime authority. No ChannelProfileVersion/Channel Contract mutation path was used.

## AgentContextPack Evidence

- AgentContextPackSnapshot id: `7b74da8c-6e3c-45c1-b634-5ddfea97e849`
- Agent: `ScriptWriterAgent`
- Lane: `long_context_text`
- `context_pack_hash`: `context-pack-hash`
- `prompt_context_hash`: `prompt-context-hash`
- `runtime_guard_digest_hash`: `runtime-guard-hash`
- Budget summary: `prompt_budget=1234`, `used=456`
- Omitted items: none
- Context pack payload keys: none

Safety checks:
- `raw_memory_text` absent
- `previous_artifacts` absent
- `latest_mutable_settings` absent
- `channel_contract_json` absent
- `compiled_policy_snapshot_json` absent

## Prompt / Audit Evidence

- PromptRenderRun refs on package: none in this safe fixture
- PromptAuditSnapshot refs on package: none in this safe fixture
- PromptRenderRun rows: 0
- PromptAuditSnapshot rows: 0
- Replayable prompt context evidence: `prompt_context_hash=prompt-context-hash` from AgentContextPackSnapshot
- Full contract/policy audit payload was not injected into production prompt payload.

## Package / Handoff Evidence

- Package status: `READY_FOR_HUMAN_REVIEW`
- Title: `VCOS manual publish handoff`
- Description: manual YouTube copy; VCOS does not upload or publish.
- Hook first 3 seconds: `VCOS prepares a manual-only handoff.`
- Hook promise: `VCOS stops before provider calls`
- Subtitle refs: `subtitle:draft`
- Disclosure: AI-assisted draft, human final approval required.
- Thumbnail handoff: concept `Operator cockpit`, overlay `Manual only`, no Drive ref.
- Publish timing: manual-only; missing publish window is represented as recommendation blocker, not automation.
- Manual next action: `REVIEW_PACKAGE`
- Handoff flags: `manual_upload_only=true`, `no_upload_api_by_policy=true`, `no_upload_or_publish_calls_made=true`


## R3D4 Gate Evidence

- AgentOutputValidationRun refs: none in selected fixture
- R3D4GateBatchRun: `4abf1227-bfd0-4b1a-8122-4026a30c1edc`
- Batch status: `BLOCK`
- Hard block count: 1
- Reducer decision: `BLOCK`
- R3D4GateRun: `a39aec4a-c69a-46c2-ae72-6bd37ae5490a`
- Gate: `TitlePromiseGate`
- Gate status: `BLOCK`
- Severity: `HARD_RULE`
- Fail code: `TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM`
- Gatekeeper soft review result: not available in fixture
- Deterministic BLOCK did not become media-ready: confirmed

Conclusion: deterministic gate behavior is safe. Required gate missing / gate exception / unknown gatekeeper regressions remain covered by R3D10 verifier tests.

## M1 Packaging Handoff Evidence

- HookSpec read model: present
- UploadHandoffCopy read model: present
- ThumbnailHandoff read model: present
- PublishTimingRecommendation read model: present
- ManualPublishOnlyGate: PASS in packaging gate set
- HumanUploadTask skeleton: `d9cf0c61-e842-43ce-9ead-a57f551f8831`
- Task status: `BACKFILLED_WAITING_VERIFICATION`
- Next action: verify pasted-back YouTube id via read-only path when configured

Confirmed:
- no auto upload/publish
- no YouTube upload API
- manual handoff/backfill only

## R3D9 Dashboard / Read-Model Evidence

All inspected API paths returned HTTP 200:
- `/ops/runtime-lts-freeze-check`
- `/ops/command-center`
- `/channels/{channel_id}/runtime-trace`
- `/video-projects/{project_id}/runtime-trace`
- `/video-packages/{package_id}/ops-summary`
- `/uploaded-videos/{uploaded_video_id}/ops-summary`
- `/diagnostics/queue`
- `/recovery/queue`
- `/learning/queue`
- `/memory/review-queue/ops`
- `/retrieval-manifests/{manifest_id}`
- `/memory-influence/{manifest_id}`
- `/quality-delta/{quality_delta_id}`
- `/provider-cost/{package_id}`

R3D9 ops route methods:
- `/ops/command-center`: GET
- `/ops/next-actions`: GET
- `/ops/runtime-lts-freeze-check`: GET
- `/diagnostics/queue`: GET
- `/recovery/queue`: GET
- `/learning/queue`: GET
- `/memory/review-queue/ops`: GET

Card counts:
- active channels: 1
- packages waiting review: 1
- upload tasks waiting human: 1
- uploaded videos waiting verification/analytics: 1
- diagnostics needing review: 1
- recovery proposals: 1
- learning candidates: 1
- memory approvals: 1
- provider cost blockers: 1
- gate failures: 1
- next actions: 9

Forbidden actions exposed by read model:
- `RUN_DAILY_GENERATION`
- `RUN_NOVIEW_SCANNER`
- `RUN_VECTOR_LEARNING`
- `EXECUTE_PROVIDER`
- `UPLOAD_OR_PUBLISH_YOUTUBE`
- `BROWSER_DASHBOARD_AUTOMATION`
- `MUTATE_CHANNEL_CONTRACT`
- `PROMOTE_LEARNING_AUTOMATICALLY`

Frontend ops forbidden button scan: no hits.

## Provider / Cost Boundary Evidence

- ProviderStackDriftGuard: PASS
- Expected canonical providers:
  - `elevenlabs`
  - `google_veo`
  - `pexels_api`
- Found active providers:
  - `elevenlabs`
  - `google_veo`
  - `pexels_api`
- Stale provider keys: none active
- `will_execute=false`
- Provider jobs: none
- Read model appendix: `read_only=true`, `provider_boundary_preflight_not_called=true`, `no_network_call_made_by_read_model=true`

Provider blockers/readiness:
- `GOOGLE_DRIVE_ARCHIVE_DISABLED`
- `PROVIDER_REAL_EXECUTION_DISABLED`
- `HUMAN_PAID_APPROVAL_PENDING`

Paid boundary:
- PaidProviderCallLedger entry: `VALIDATION_ONLY`, `BLOCKED`, `will_execute=false`
- PaidProviderCallLedger `EXECUTED`: 0
- `ALLOWED_NOT_EXECUTED`: 0
- PaidAttemptLimitRecord: 1, status `BLOCKED`
- ProviderAttempt: 0
- MediaRenderJob: 0
- ProviderJobSnapshot: 0

## UploadedVideo / Backfill Evidence

Existing safe fixture includes a pasted-back UploadedVideo record.

- uploaded_video_id: `3d000601-e5e8-4cff-aa1f-bd538d711c7f`
- platform: YouTube
- platform_video_id: `abcDEF12345`
- URL: `https://www.youtube.com/watch?v=abcDEF12345`
- verification_status: `NOT_VERIFIED`
- analytics_sync_status: `NOT_CONFIGURED`
- analytics_maturity: `TOO_EARLY`
- analytics_confidence: `MEDIUM`
- backfill events: 1
- no YouTube Studio scraping: true
- next action: `VERIFY_UPLOADED_VIDEO`

No YouTube upload/publish/reupload was called. This is paste-back ledger evidence only.

## Diagnostic / Recovery / Learning / Memory Evidence

Diagnostics:
- health_state: `INSUFFICIENT_DATA`
- reason: `ANALYTICS_NOT_MATURE`
- action_ready: false
- next action: wait analytics maturity

Recovery:
- proposal_state: `PROPOSED`
- type: `REVIEW_TITLE_THUMBNAIL`
- allowed actions: approve/reject

Learning:
- candidate state: `READY_FOR_HUMAN_REVIEW`
- linked memory promotion: `PENDING`
- no auto-promotion

Memory:
- memory approval_status: `REVIEW_REQUIRED`
- prompt_eligible: false
- blockers: `MEMORY_NOT_APPROVED`, `MEMORY_RIGHTS_NOT_SAFE`, `MEMORY_NOT_PROMPT_SAFE`
- raw memory text hidden in read model
- prompt rule: only `APPROVED + SAFE + PROMPT_SAFE + FRESH` can be eligible later

## Retrieval / Influence / Quality Delta Evidence

Retrieval:
- VectorRetrievalManifest: `61ae5079-bfb0-41e2-914d-85b05d932f68`
- agent: `ScriptWriterAgent`
- SQL filter: `approval_status=APPROVED`
- candidate before vector: 1
- candidate after policy: 0
- raw memory hidden: true
- dump contains `facet_text`: false

Memory influence:
- MemoryInfluenceManifest: `931183bb-2155-4c5b-af5e-2f178722f03c`
- scope_status: `BLOCK`
- applied as: `memory_digest`
- blocked reason: `MEMORY_NOT_APPROVED`
- raw memory hidden: true

Quality delta:
- QualityDeltaAttribution: `3728c72e-db52-45b6-a0e8-1d1d35f56bb2`
- result: `TOO_EARLY`
- reason: `ANALYTICS_NOT_MATURE`
- next action: wait analytics maturity

No vector job was run. No memory was forced into production prompts.

## No-Execution Proof Checklist

| Proof | Result |
| --- | --- |
| no ProviderAttempt created | PASS, count `0` |
| no MediaRenderJob created | PASS, count `0` |
| no ProviderJobSnapshot submitted | PASS, count `0` |
| no external provider network call | PASS, read models only; provider cost appendix says no network call |
| no Drive upload | PASS, MediaOffloadJob count `0` |
| no YouTube upload | PASS, sync run counts `0`; paste-back ledger only |
| no Pexels search/download | PASS, no provider execution path invoked |
| no dashboard execute button | PASS, frontend ops scan no hits |
| verifier/read model flag | PASS, `no_provider_media_upload_execution=true` |
| PaidProviderCallLedger EXECUTED | PASS, count `0` |

## P0/P1/P2/P3 Findings

| Severity | Finding | Status |
| --- | --- | --- |
| P0 | none | closed |
| P1 | none | closed |
| P2 | none | closed |
| P3 | none | closed |

## ProductionPainLog Entries Added

None. `reports/production_pain_log.md` already exists and no P2/P3 item was found.

## Next Recommended Safe Operator Action

Review package/title manually and resolve the `TitlePromiseGate` BLOCK before any future handoff continuation. Keep provider activation, media render, Pexels, Drive, and YouTube upload/publish disabled until a separate explicit provider activation phase.
