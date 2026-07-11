# PA1 Provider Activation Design Spec

Date: 2026-07-06

Status: `DESIGN_ONLY`

Applies after:

- `CODE_CLOSEOUT_PROD_V1=GO`
- Runtime LTS live verifier `PASS`
- ProviderStackDriftGuard `PASS`
- Production Package Pilot 001 manual-only `PASS`

This document is a design contract for a future provider activation phase. It does not implement provider activation.

## Scope

Provider stack in scope:

- `elevenlabs`
- `luma_api`
- `pexels_api`
- `google_drive_archive` as archive/storage only

Out of scope:

- YouTube upload API
- auto publish
- browser automation
- dashboard run/render/upload buttons
- new providers
- paid stock
- TikTok/Facebook
- core/backend architecture changes in this spec task

## Activation Principles

1. Provider activation is explicit, per-provider, and off by default.
2. No provider call is allowed without human approval.
3. No retry is allowed without idempotency and an attempt gate.
4. R3D9 dashboard remains a read-model/operator cockpit, not a provider job-control center.
5. No dashboard execution button may be added for provider/render/upload.
6. No YouTube upload/publish automation is allowed.
7. Drive is archive/storage only and is never a publish trigger.
8. Provider activation must preserve immutable runtime truth: no Channel Contract, ChannelProfileVersion, or EffectiveChannelRuntimeContextSnapshot mutation by provider execution.
9. Paid/free external calls both require evidence, ledgering, duplicate prevention, and kill switch checks.
10. A smoke output is never production final media unless a later production approval/QC path explicitly promotes a separate production render.

## Required Pre-Call Gates

Every provider execution attempt must pass all gates below before any network call:

| Gate | Required result | Notes |
| --- | --- | --- |
| Runtime LTS verifier | `PASS` | `GET /ops/runtime-lts-freeze-check` or equivalent internal verifier. |
| ProviderStackDriftGuard | `PASS` | Active provider keys must match canonical stack. |
| Package deterministic gate state | no deterministic `BLOCK` | Package review/read-model must have no hard blocker for production renders. |
| CostEstimateSnapshot | exists | Cost can be zero for free/search/archive paths, but snapshot is still required. |
| HumanPaidRenderApproval | `APPROVED` | Approval must bind provider, operation, cost snapshot, package/render revision, and expiry. |
| ProviderIdempotencyKey | exists | Deterministic key for provider operation and payload hash. |
| PaidAttemptLimitGate | `PASS` | No attempt limit bypass for retries. |
| ProviderBoundaryGate | `PASS` | Verifies allowed provider, operation, media type, and scope. |
| ChannelMonthlyBudgetGate | `PASS` | Applies to paid and zero-cost external operations. |
| real execution flag | `ON` | Default must remain off. Smoke/prod flags are separate. |
| planned ledger row | exists before call | Ledger starts as planned/preflighted before network execution. |
| duplicate active job check | `PASS` | One active job per render revision/provider operation. |
| kill switch checks | `PASS` | Global, provider, and package locks must allow execution. |

If any gate fails, the operation is `NOT_EXECUTED`. It must not consume a paid attempt when `will_execute=false`.

## Provider-Specific Activation Rules

### ElevenLabs

Allowed role:

- Voice/TTS only.

Rules:

- Tiny voice smoke must run before production narration.
- Narration generation requires approved voice profile, script input hash, and human approval.
- Cache key/idempotency is required for each narration payload.
- Draft TTS must not be treated as final unless explicitly approved.
- VoiceQC is required before final assembly.
- Failed VoiceQC cannot enter the NativeFFmpeg compiled render boundary.

Required evidence:

- voice profile/version ref
- script/artifact input hash
- CostEstimateSnapshot
- HumanPaidRenderApproval
- ProviderIdempotencyKey
- ProviderJobSnapshot
- PaidProviderCallLedger
- VoiceQC result

### Luma API

Allowed role:

- AI hero/metaphor video clip only.

Rules:

- Allowed durations: `4`, `6`, `8` seconds.
- Max duration: `8` seconds.
- Long-form hero clip is not the backbone of the final video.
- Prompt safety and visual intent must be reviewed before execution.
- No recurring character use unless character policy explicitly allows it.
- Output must pass visual relevance/QC before final assembly.

Required evidence:

- visual intent artifact/version
- prompt safety decision
- duration proof
- CostEstimateSnapshot
- HumanPaidRenderApproval
- ProviderIdempotencyKey
- ProviderJobSnapshot
- PaidProviderCallLedger
- MediaQC result

### Pexels API

Allowed role:

- Free visual fallback only.

Rules:

- Use only when owned/generated visuals are insufficient.
- No stock face may be used as a recurring host.
- No stock asset may be used as factual evidence, testimonial, endorsement, or proof.
- Attribution/source manifest is required.
- Runtime percentage limits must be enforced.
- Asset rights/source metadata must remain visible in audit evidence.

Required evidence:

- fallback reason
- query/input hash
- source/rights manifest
- attribution manifest
- runtime percentage calculation
- CostEstimateSnapshot with zero/limited cost
- HumanPaidRenderApproval
- ProviderIdempotencyKey
- ProviderJobSnapshot
- PaidProviderCallLedger

### Google Drive Archive

Allowed role:

- Archive/storage only.

Rules:

- Configured root is the root.
- New writes must not create nested `VCOS` folders under the configured root.
- New writes must not use `company_unknown`, `channel_unknown`, or `project_unknown`.
- Final media archive occurs only after provider render is verified.
- Drive is not the source of truth.
- Drive archive never creates a YouTube upload task by itself.

Allowed new path forms:

- smoke: `smoke_tests/YYYY-MM-DD`
- project-scoped: `company_{company_id}/channel_{channel_workspace_id}/project_{video_project_id}/{media_type_or_subfolder}`
- uploaded-video-scoped: `company_{company_id}/channel_{channel_workspace_id}/uploaded_video_{uploaded_video_id}/{media_type_or_subfolder}`

Required evidence:

- archive path mode
- folder path
- checksum or checksum-unavailable reason
- CloudMediaRef only after verified archive write
- no FinalMediaRef for smoke archive
- no HumanUploadTask from archive alone

## Execution State Machine

Provider activation states:

| Status | Meaning | Terminal |
| --- | --- | --- |
| `NOT_CONFIGURED` | Required provider config/credential is missing. | no |
| `CONFIGURED_NOT_ACTIVE` | Config exists, but execution flag is off. | no |
| `READY_FOR_SMOKE_APPROVAL` | Smoke can be reviewed for human approval. | no |
| `SMOKE_APPROVED` | Human approved one smoke operation. | no |
| `SMOKE_RUNNING` | Smoke provider job is active. | no |
| `SMOKE_PASS` | Smoke completed and evidence/QC are acceptable. | no |
| `SMOKE_FAILED` | Smoke failed or evidence/QC is missing. | yes for that smoke run |
| `READY_FOR_PRODUCTION_APPROVAL` | Provider passed smoke and can request production approval. | no |
| `APPROVED_FOR_SINGLE_RUN` | Human approved exactly one production execution. | no |
| `EXECUTING` | Provider job is active. | no |
| `EXECUTED` | Provider returned output or completion result. | no |
| `FAILED` | Provider call/job failed safely. | yes unless retried by policy |
| `CANCELLED` | Operator/provider cancelled before successful completion. | yes unless reapproved |
| `KILLED` | Kill switch stopped execution/resume. | yes unless reapproved |
| `ARCHIVED` | Output was durably archived. | no |
| `QC_PASSED` | Output passed required QC and can progress to next stage. | yes for that stage |
| `QC_FAILED` | Output failed QC and cannot progress. | yes unless new approval/retry |

Allowed high-level transitions:

```text
NOT_CONFIGURED
  -> CONFIGURED_NOT_ACTIVE
  -> READY_FOR_SMOKE_APPROVAL
  -> SMOKE_APPROVED
  -> SMOKE_RUNNING
  -> SMOKE_PASS
  -> READY_FOR_PRODUCTION_APPROVAL
  -> APPROVED_FOR_SINGLE_RUN
  -> EXECUTING
  -> EXECUTED
  -> ARCHIVED
  -> QC_PASSED
```

Failure transitions:

```text
SMOKE_RUNNING -> SMOKE_FAILED
EXECUTING -> FAILED | CANCELLED | KILLED
EXECUTED -> QC_FAILED
ARCHIVED -> QC_FAILED
```

No automatic transition may create upload/publish state.

## Idempotency And Retry

ProviderIdempotencyKey format:

```text
pa1:v1:{environment}:{package_id}:{render_revision_id}:{provider_key}:{operation}:{payload_sha256_24}
```

Rules:

- `payload_sha256_24` is derived from canonical provider payload, input artifact refs, cost snapshot ref, approval ref, and operation version.
- One active job is allowed per `(render_revision_id, provider_key, operation)`.
- Duplicate submit with the same active key returns the existing job snapshot/read-model, not a new provider call.
- Retry requires a reason code and explicit operator approval.
- Retry cannot bypass CostEstimateSnapshot, HumanPaidRenderApproval, ProviderBoundaryGate, PaidAttemptLimitGate, ChannelMonthlyBudgetGate, or kill switch checks.
- If provider supports external idempotency, retry with the same payload reuses the same external idempotency key.
- If payload changes, a new idempotency key, estimate, approval, and ledger path are required.
- Failed attempt accounting is separate from not-executed accounting.
- A planned attempt with `will_execute=false` does not consume a paid attempt.
- A network call that reaches the provider consumes an attempt according to the paid attempt gate, even if the provider later fails.

Required retry reason codes:

- `PROVIDER_TIMEOUT`
- `PROVIDER_TRANSIENT_ERROR`
- `PROVIDER_RATE_LIMIT`
- `OPERATOR_CANCELLED_RETRY_REQUEST`
- `QC_FAILED_RETRY_WITH_REVISED_INPUT`
- `ARCHIVE_FAILED_RETRY`

## Ledger And Audit Evidence

Required evidence for any production provider operation:

| Evidence | Required timing |
| --- | --- |
| `RenderRevision` | before provider execution |
| `CostEstimateSnapshot` | before approval/execution |
| `HumanPaidRenderApproval` | before execution |
| `ProviderIdempotencyKey` | before execution |
| `ProviderBoundaryDecision` | before execution |
| `PaidAttemptLimitRecord` | before execution |
| `ProviderJobSnapshot` | before and after provider submit/poll |
| `PaidProviderCallLedger` | planned before execution, finalized after outcome |
| `CloudMediaRef` | only after archive/storage verification |
| `FinalMediaRef` | only after final render archive + MediaQC pass |
| `MediaQC result` | before final handoff/upload task eligibility |
| `Source/Rights manifest` | before visual asset/final assembly use |

Smoke evidence must be marked non-production and must not be linked as production `FinalMediaRef`.

## Kill Switch And Rollback

Required controls:

- global provider execution kill switch
- per-provider kill switch
- per-package execution lock
- per-render-revision execution lock
- operator-visible killed/blocked reason

Kill behavior:

- If kill switch is on before call, result is `KILLED` or `NOT_EXECUTED`.
- If kill switch is enabled during active execution, VCOS records `KILLED`, attempts provider cancellation if supported, and stops polling/resume except for read-only status reconciliation.
- No auto-resume after kill.
- Operator must explicitly approve resume after reviewing cost, attempt count, and partial outputs.
- Partial outputs cannot become `FinalMediaRef` without archive verification and QC.

Rollback behavior:

- Remote provider outputs are not assumed deletable.
- VCOS rolls forward with audit: mark failed/superseded, do not hide evidence.
- Failed or superseded outputs cannot be used in final assembly.
- Existing Channel Contract, ChannelProfileVersion, and EffectiveChannelRuntimeContextSnapshot remain immutable.
- No upload/publish state is created during rollback.

## PA1-SMOKE Plan

Smoke order:

1. ElevenLabs tiny voice.
2. Luma tiny `4`, `6`, or `8` second clip.
3. Pexels one fallback search if enabled.
4. NativeFFmpeg local assembly/QC proof.
5. Google Drive archive proof.

Every smoke must be:

- non-production
- cost-capped
- human-approved
- ledgered
- idempotent
- bounded to one provider operation
- not linked as production `FinalMediaRef`
- not eligible for HumanUploadTask
- not uploaded/published to YouTube

Smoke outputs:

- may prove provider wiring, idempotency, ledgering, polling, archive, and QC harness behavior
- must not be used as production package final media
- must be clearly marked `SMOKE`

## PA1-SMOKE Go/No-Go Criteria

GO requires:

- every smoke call has CostEstimateSnapshot, human approval, idempotency key, and ledger evidence
- no duplicate submit
- no hidden dashboard execution
- no provider stack drift
- no upload automation
- no YouTube upload scope/execution
- no P0/P1
- all smoke outputs remain non-production

NO-GO if any of these occur:

- provider can execute without approval
- ledger is missing
- retry bypass is possible
- stale provider becomes active
- Drive archive creates wrong path
- media output becomes publishable without QC
- any YouTube upload scope/execution appears
- dashboard adds run/render/upload/job-control buttons
- Channel Contract, ChannelProfileVersion, or EffectiveChannelRuntimeContextSnapshot mutates during provider execution

## Next Checkpoint

After this design spec is approved, the next checkpoint is:

`PA1-SMOKE`

`PA1-SMOKE` must remain tiny, explicit, human-approved, ledgered, cost-capped, and non-production.
