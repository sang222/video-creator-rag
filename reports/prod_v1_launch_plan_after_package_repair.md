# Prod V1 Launch Plan After Package Repair

Updated: 2026-07-19

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
| CH1 first production channel | `PASS / v2 ACTIVE` | CH1-FLEX v2 profile `d735ec40-d29f-4d73-9e8a-58b4e1bfe325` and snapshot `6304e2a4-f096-410b-af09-a2748b311855` are active for future work; v1 remains the immutable rollback baseline. |
| NICH1 niche governance | `PASS` | Bounded authoritative digest, strict slot contract, DailyIdeaAgent router path, derived channel fit, five gates, and consolidated dossier are enforced. |
| D2P1 daily-to-package bridge | `PASS / NO MEDIA` | Immutable DailyIdeaDecision admission receipt, Effective Context, idempotent research resume, M12.2 package, read-only status, and zero-execution guards passed. |
| LPRO1 | `AUTHORIZED NEXT / NOT_STARTED` | `PROCEED_TO_LPRO1=true`; this master task did not start LPRO1. |
| PKG1 first production package | `PASS` | Technical and explicit human review passed; all six review areas approved. |
| Visual impact review | `PASS` | Repository-grounded mapping complete; VSR1 may proceed without a DB migration. |
| VSR1 niche-aware visual routing | `PASS` | Provider-neutral taxonomy, gates, deterministic router, routing evidence and offline fixtures complete; provider execution remains disabled. |
| IMG1 Google Gemini Image foundation | `PASS / EXECUTION DISABLED` | Distinct `google_gemini_image` IMAGE route, versioned `gemini-3.1-flash-image` catalog, native exact-content authority and fixture-only acceptance are complete; no provider call or paid canary ran. |
| IMG-CANARY-v3 | `PASS / ARCHIVE VERIFIED` | One provider attempt succeeded; operator human review passed; immutable 47-item Drive closeout verified. CH1-FLEX v2 may proceed in a separate task. |
| MR1 first real media render | `ON_HOLD / NOT_STARTED` | Historical approval does not cover the new visual-source decision set or Gemini Image route; provider call count and render count remain 0. |

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

## Major Visual Architecture Hold

The 2026-07-17 `VISUAL-IMPACT-REVIEW` supersedes the prior permission to start MR1, while preserving CH1-FLEX v1 and PKG1 v1 as immutable historical passes.

```text
MR1_EXECUTION=ON_HOLD
PROCEED_TO_MR1=false
PROCEED_TO_VSR1=true
```

Required sequence before MR1:

1. VSR1 provider-neutral routing foundation — `PASS` on 2026-07-17.
2. IMG1 Gemini Image provider route — `PASS` on 2026-07-18; fixture-only
   acceptance complete, execution disabled.
3. VQC1 generated-image/native-overlay QC.
4. Offline routing/image/overlay fixtures.
5. One controlled paid image canary.
6. Human full-watch and Drive verification — `PASS` on 2026-07-18 for
   `img-canary-v3-20260718T162027Z-a90959ed` (47/47 Drive items verified).
7. CH1-FLEX v2 approval/activation for future projects — `PASS` on 2026-07-19 with provider/media/Drive/YouTube deltas all zero.
8. NICH1 and D2P1 governance/bridge — `PASS` on 2026-07-19; scripted package remains no-media and human-review-only.
9. LPRO1 — authorized as the next separate operator-started task, not started here.
10. PKG1 visual/provider/cost/disclosure revision — still on hold and not performed by this task.
11. New exact-target MR1 approval.
12. MR1.

The old PKG1/MR1 approval remains historical evidence; it is not rewritten or treated as authority for the new route.

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

Status: `PASS`.

Finalize the first real production channel contract/context for a real production run.

Rules:

- No silent Channel Contract mutation.
- No silent ChannelProfileVersion mutation.
- No silent EffectiveChannelRuntimeContextSnapshot mutation.
- Operator-approved changes only.

### 4. PKG1 - First Real Production Package

Status: `PASS`. Package-manifest v2 is approved under `operator-approval://pkg1/small-team-ai/final-package-and-mr1`; prior `REVIEW_REQUIRED` evidence is retained as resolved history.

Create the first real production package using the frozen runtime path.

Rules:

- Runtime LTS verifier must still pass.
- Provider stack drift guard must still pass.
- Package queue must be repaired before render/upload handoff.

### 5. MR1 - First Real Media Render

Status: `ON_HOLD`; execution is `NOT_STARTED`. Historical readiness remains frozen in `artifact-version://cd1d93f7-29b0-4af3-9fa3-b877ddc0457b`, but it does not cover the new visual-source policy or Gemini Image route.

Do not run MR1 until the major visual architecture sequence above is complete and a new exact-target approval exists. This checkpoint update did not call a provider, consume an attempt, render, archive, upload or publish.

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

`MR1_EXECUTION=ON_HOLD`

`PROCEED_TO_MR1=false`

`VSR1_FINAL=PASS`

`PROCEED_TO_IMG1=true`

`IMG1_FINAL=PASS`

`PROCEED_TO_VQC1=true`

`VQC1_FINAL=PASS`

`IMG_CANARY_V3_FINAL=PASS`

`ARCHIVE_VERIFIED=true`

`CH1_FLEX_V2_FINAL=PASS`

Next checkpoint:

`PROCEED_TO_LPRO1=true`

The registered route is `google_gemini_image`, separate from
`google_veo`. Its default model is `gemini-3.1-flash-image`; generated
pixels are only a visual foundation, while exact text/numbers remain native
overlay authority. The V3 paid canary and its Drive closeout passed; no
CH1-FLEX v2 was subsequently activated by the 2026-07-19 master task with
NICH1 and D2P1 PASS. No PKG1 revision, LPRO1 execution, or MR1 execution
occurred.
