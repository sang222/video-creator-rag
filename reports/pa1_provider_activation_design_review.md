# PA1 Provider Activation Design Review

Date: 2026-07-06

Status: `DESIGN_ONLY`

## Decision

`PA1_SPEC=COMPLETE`

Next checkpoint recommendation:

`PA1-SMOKE` only after explicit operator approval.

## Files Changed

- `docs/architecture/provider_activation_design_spec.md`
- `reports/pa1_provider_activation_design_review.md`

No app/backend/frontend execution behavior was changed.

## Spec Summary

The provider activation spec defines the future Production V1 activation contract for:

- `elevenlabs`
- `luma_api`
- `creatomate_growth_10k`
- `pexels_api`
- `google_drive_archive` as archive/storage only

The spec is design-only and covers:

- activation principles
- required pre-call gates
- provider-specific rules
- execution state machine
- idempotency and retry
- ledger/audit evidence
- kill switch and rollback
- PA1-SMOKE plan
- PA1-SMOKE go/no-go criteria

## Provider Activation Boundaries

Provider activation remains `NO-GO` after this task.

Hard boundaries preserved:

- no ElevenLabs call
- no Luma call
- no Creatomate call
- no Pexels call
- no Drive upload
- no YouTube upload/publish/reupload
- no real media/video generation
- no final media creation
- no HumanUploadTask creation
- no provider activation implementation
- no dashboard execution buttons
- no Channel Contract mutation
- no ChannelProfileVersion mutation
- no EffectiveChannelRuntimeContextSnapshot mutation
- no learning auto-promotion
- no prompt self-mutation

Out of scope remains:

- YouTube upload API
- auto publish
- browser automation
- dashboard run/render/upload buttons
- new providers
- paid stock
- TikTok/Facebook
- core/backend architecture changes

## Required Pre-Call Gate Contract

The spec requires every future provider execution attempt to pass:

- Runtime LTS verifier `PASS`
- ProviderStackDriftGuard `PASS`
- package has no deterministic `BLOCK`
- CostEstimateSnapshot exists
- HumanPaidRenderApproval approved
- ProviderIdempotencyKey exists
- PaidAttemptLimitGate `PASS`
- ProviderBoundaryGate `PASS`
- ChannelMonthlyBudgetGate `PASS`
- real execution flag `ON`
- ledger row planned before execution
- no duplicate active job
- kill switch checks `PASS`

If any gate fails, operation is `NOT_EXECUTED`, and no paid attempt is consumed when `will_execute=false`.

## Provider Rules Reviewed

| Provider | Future role | Key constraints |
| --- | --- | --- |
| `elevenlabs` | voice/TTS only | tiny voice smoke first, idempotent cache key, no draft TTS as final without approval, VoiceQC required |
| `luma_api` | AI hero/metaphor clip only | only `4/6/8s`, max `8s`, prompt safety and visual intent required, no recurring character unless policy allows |
| `creatomate_growth_10k` | final assembly/render only | template required, render payload preview, `FinalRenderApproval`, archive before upload handoff, MediaQC before FinalMediaRef |
| `pexels_api` | fallback only | no stock face as recurring host, no factual/testimonial use, source/rights manifest, runtime percentage limits |
| `google_drive_archive` | archive/storage only | configured root is root, no nested `VCOS`, no unknown path segments, not source of truth |

## Execution State Machine

The spec defines these statuses:

- `NOT_CONFIGURED`
- `CONFIGURED_NOT_ACTIVE`
- `READY_FOR_SMOKE_APPROVAL`
- `SMOKE_APPROVED`
- `SMOKE_RUNNING`
- `SMOKE_PASS`
- `SMOKE_FAILED`
- `READY_FOR_PRODUCTION_APPROVAL`
- `APPROVED_FOR_SINGLE_RUN`
- `EXECUTING`
- `EXECUTED`
- `FAILED`
- `CANCELLED`
- `KILLED`
- `ARCHIVED`
- `QC_PASSED`
- `QC_FAILED`

No state transition may create YouTube upload/publish automation.

## Idempotency And Retry Review

ProviderIdempotencyKey format:

```text
pa1:v1:{environment}:{package_id}:{render_revision_id}:{provider_key}:{operation}:{payload_sha256_24}
```

Required behavior:

- one active job per render revision/provider operation
- duplicate submit returns existing job snapshot/read-model
- retry requires reason code and explicit operator approval
- retry cannot bypass cost, approval, boundary, attempt, budget, or kill switch gates
- failed attempt accounting is separate from `NOT_EXECUTED`
- no attempt consumed when `will_execute=false`

## Ledger And Audit Review

Required future evidence:

- RenderRevision
- CostEstimateSnapshot
- HumanPaidRenderApproval
- ProviderIdempotencyKey
- ProviderBoundaryDecision
- PaidAttemptLimitRecord
- ProviderJobSnapshot
- PaidProviderCallLedger
- CloudMediaRef only after archive/storage verification
- FinalMediaRef only after final render archive + MediaQC pass
- MediaQC result
- Source/Rights manifest

Smoke evidence must be non-production and must not become production `FinalMediaRef`.

## Kill Switch / Rollback Review

Required controls:

- global provider execution kill switch
- per-provider kill switch
- per-package execution lock
- per-render-revision execution lock
- no auto-resume after kill
- explicit operator approval for resume
- failed/superseded outputs cannot be used in final assembly
- rollback does not mutate Channel Contract, ChannelProfileVersion, or EffectiveChannelRuntimeContextSnapshot

## PA1-SMOKE Checklist

Smoke order:

1. ElevenLabs tiny voice.
2. Luma tiny `4/6/8s` clip.
3. Creatomate tiny render.
4. Pexels one fallback search if enabled.
5. Google Drive archive proof.

Every smoke must be:

- non-production
- cost-capped
- human-approved
- ledgered
- idempotent
- not linked as production FinalMediaRef
- no HumanUploadTask
- no YouTube upload

## PA1-SMOKE Go/No-Go Criteria

GO only if:

- every smoke call has cost snapshot, approval, idempotency, and ledger
- no duplicate submit
- no hidden dashboard execution
- no provider stack drift
- no upload automation
- no YouTube upload scope/execution
- no P0/P1

NO-GO if:

- provider can execute without approval
- ledger is missing
- retry bypass is possible
- stale provider becomes active
- Drive archive creates wrong path
- media output becomes publishable without QC
- any YouTube upload scope/execution appears
- dashboard adds run/render/upload buttons

## No-Implementation Confirmation

This task did not add:

- DB migration
- API endpoint
- provider service
- queue worker
- dashboard button
- execution flag behavior
- provider credential/config change
- media generation code
- upload/publish code

## No-Execution Confirmation

No provider/media/upload/YouTube execution was performed by this task.

Provider activation remains `NO-GO`.

## P0/P1/P2/P3 Classification

| Severity | Items |
| --- | --- |
| P0 | None |
| P1 | None |
| P2 | None |
| P3 | None |

## Required Checks

| Command | Result |
| --- | --- |
| `PYTHONPATH=. .venv/bin/python -m compileall -q app` | PASS |
| `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` | PASS, 13 passed |
| `PYTHONPATH=. .venv/bin/pytest tests/test_dx2_provider_stack_reconciliation.py -q` | PASS, 7 passed |
| `git diff --check` | PASS |

## Final Recommendation

Proceed to `PA1-SMOKE` only after explicit operator approval.

Do not implement or execute providers from this spec task.
