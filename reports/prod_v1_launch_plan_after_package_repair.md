# Prod V1 Launch Plan After Package Repair

Date: 2026-07-06

Package repair package: `81c48d7a-dfc3-4207-b585-744673491b59`

## Current Roadmap Status

| Checkpoint | Status | Notes |
| --- | --- | --- |
| Runtime LTS v1 | `PASS` | Live verifier is `PASS`; provider/media/upload execution remains disabled. |
| INT1A Ollama smoke | `PASS` | Real Ollama smoke passed in INT1; not a media provider activation. |
| INT1B Drive archive/path | `PASS` | Drive archive path fixed; Drive remains storage/archive-only and disabled for this package closeout. |
| Package Repair | `PASS` | 9 approved patches applied; latest gate rerun `PASS`; queue closed. |
| PPP1 manual-only pilot | `PASS` | Waiting for final media asset is expected; no upload task. |
| Code Closeout Prod V1 | `GO` | Core/backend architecture closed for Production V1; P0/P1 only after this point. |

## Current Package State

| Field | Value |
| --- | --- |
| `review_verdict` | `WAITING_FINAL_MEDIA_ASSET` |
| `must_fix_count` | `0` |
| `latest_gate_rerun_status` | `PASS` |
| `applied_patch_count` | `9` |
| `upload_task_creation_allowed` | `false` |
| `FinalMediaRef` | `0` |
| `CloudMediaRef` | `0` |
| `HumanUploadTask` | `0` |
| provider/media/upload/YouTube execution | `0` |

`WAITING_FINAL_MEDIA_ASSET` is expected. The package is repaired but no final video asset exists yet.

## Production V1 Rules After Code Closeout

- Do not add core/backend architecture features before first production run.
- Do not add dashboard job-control buttons.
- Do not activate providers from R3D9/PPP1.
- Do not generate media in closeout.
- Do not create upload task before verified final media exists.
- Do not upload/publish/reupload YouTube automatically.
- Do not mutate Channel Contract, ChannelProfileVersion, or EffectiveChannelRuntimeContextSnapshot.
- P0/P1 only for immediate hotfix.
- P2/P3 go to Production Pain Log.

## Deferred Backlog

| ID | Severity | Area | Summary | Decision |
| --- | --- | --- | --- | --- |
| `PPL-CC1-001` | P2 | R3D9 ops status | Raw package `package_status=BLOCKED` can appear stale while read-model is `WAITING_FINAL_MEDIA_ASSET`. | Defer to UX/read-model cleanup. |
| `PPL-INT1-001` | P2 | qualification tests | Some stale M10.1/M10.5 qualification expectations remain from INT1. | Batch-review maintenance. |

No P0/P1 is open.

## Next Roadmap

### 1. PA1-SPEC - Provider Activation Design Spec

Goal: design only. No provider execution.

Required spec coverage:

- execution flags
- `CostEstimateSnapshot`
- `HumanPaidRenderApproval`
- `ProviderIdempotencyKey`
- `PaidAttemptLimitGate`
- `ProviderBoundaryGate`
- `ChannelMonthlyBudgetGate`
- provider ledger
- poll/resume
- retry rules
- duplicate prevention
- kill switch
- rollback

Expected outputs:

- `docs/architecture/provider_activation_design_spec.md`
- `reports/pa1_provider_activation_design_review.md`

### 2. PA1-SMOKE - Tiny Provider Smoke

Only after `PA1-SPEC` is approved.

Allowed sequence:

1. ElevenLabs tiny voice.
2. Google Veo tiny 8s clip.
4. Pexels one fallback search if enabled.
5. Drive archive proof.

Every paid/provider call must pass:

- `CostEstimateSnapshot`
- `HumanPaidRenderApproval`
- `ProviderIdempotencyKey`
- `PaidAttemptLimitGate`
- `ProviderBoundaryGate`
- `ChannelMonthlyBudgetGate`
- ledger write
- explicit real execution flag

Expected output:

- `reports/pa1_provider_activation_smoke_report.md`

### 3. CH1 - First Production Channel Finalization

Finalize the first real production channel contract/context for a real production run.

Rules:

- No silent Channel Contract mutation.
- No silent ChannelProfileVersion mutation.
- No silent EffectiveChannelRuntimeContextSnapshot mutation.
- Operator-approved changes only.

### 4. PKG1 - First Real Production Package

Create the first real production package using the frozen runtime path.

Rules:

- Runtime LTS verifier must still pass.
- Provider stack drift guard must still pass.
- Package queue must be repaired before render/upload handoff.

### 5. MR1 - First Real Media Render

Only after PA1 and PKG1 are ready.

Target sequence:

- ElevenLabs
- Google Veo
- Drive archive
- MediaQC

Hard requirements:

- cost estimate before execution
- human paid approval before paid execution
- idempotency key
- paid attempt limit
- provider boundary gate
- monthly budget gate
- ledger proof

### 6. PUB1 - Human Upload + Backfill

Manual upload only.

Flow:

1. Human uploads final video outside VCOS.
2. Human pastes YouTube URL/video ID into VCOS.
3. VCOS records backfill/read-model state.
4. No YouTube upload/publish automation.

### 7. OBS1 - Analytics / Read-Only Learning

Observe 24h/48h/72h analytics.

Rules:

- read-only analytics
- no learning auto-promotion
- memory candidates require human review and eligibility gates

### 8. CANARY - 3-Video Controlled Canary

Run three controlled packages/videos after `OBS1` readiness.

Rules:

- no auto publish
- no hidden provider execution
- each package must pass gates
- each paid/media call must have ledger and approval evidence

## Current Launch Decision

`PROD_V1_LAUNCH_PLAN_READY_FOR_PA1_SPEC=true`

Next checkpoint:

`PA1-SPEC Provider Activation Design Spec`
