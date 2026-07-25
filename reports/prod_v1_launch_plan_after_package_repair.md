# Prod V1 Launch Plan After Package Repair

Updated: 2026-07-21

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
| LPRO1 | `PASS / OFFLINE REVIEW CANDIDATE` | Strict reviewed-package handoff, canonical timeline, asset normalization, native plan/compiler, real local FFmpeg render, actual-byte TechnicalMediaQC, review-only candidate and FinalMediaRef closeout boundary passed with zero provider/Drive/YouTube calls. |
| PKG1 first production package | `PASS` | Technical and explicit human review passed; all six review areas approved. |
| PKG1 market-aware revision | `PASS / PRODUCTION PACKAGE APPROVED` | Exact operator PASS closed review `a99f2ad4-9b1d-4bc6-bafc-024ecd7e9c56` for revision `a90e2786-f6e0-5480-94a4-fb28fd000edf` v2 and package `7de25ac8-46e4-46da-b112-f805f16ebaaa`. Approval is package-planning authority only; destination remains pending and no execution authority exists. |
| Visual impact review | `PASS` | Repository-grounded mapping complete; VSR1 may proceed without a DB migration. |
| VSR1 niche-aware visual routing | `PASS` | Provider-neutral taxonomy, gates, deterministic router, routing evidence and offline fixtures complete; provider execution remains disabled. |
| IMG1 Google Gemini Image foundation | `PASS / EXECUTION DISABLED` | Distinct `google_gemini_image` IMAGE route, versioned `gemini-3.1-flash-image` catalog, native exact-content authority and fixture-only acceptance are complete; no provider call or paid canary ran. |
| IMG-CANARY-v3 | `PASS / ARCHIVE VERIFIED` | One provider attempt succeeded; operator human review passed; immutable 47-item Drive closeout verified. CH1-FLEX v2 may proceed in a separate task. |
| MR1 first real media render | `BLOCKED_REQUIRES_NEW_MR1_APPROVAL` | Historical run `020bf2a7-eb17-41a3-ac3c-301b5c6b41fd` consumed narration, forced alignment, and two failed Pexels `SC-04` attempts under terminal approval `4ccc7185-e760-4470-aba9-857ab0a18f77`. Render, Drive, and YouTube counts remain 0. |
| Geo/market delivery closeout | `IMPLEMENTED / PRODUCTION ACCEPTANCE PENDING` | Verification and immutable closeout paths are implemented. No Geo PASS receipt or closeout artifact is claimed until the DB-backed verifier runs. |
| PKG1 `SC-04` native revision | `IMPLEMENTED / HUMAN REVIEW NOT CREATED` | The replacement route is `NATIVE_MOTION_GRAPHIC`; exact Geo authority, immutable revision, and human closeout are required before a fresh MR1 approval may exist. |

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
| historical MR1 provider/render/Drive/upload/YouTube calls | `4 / 0 / 0 / 0 / 0` |

`WAITING_FINAL_MEDIA_ASSET` is expected. The package is repaired but no final video asset exists yet.

## PKG1 Market Revision Checkpoint

| Field | Value |
|---|---|
| revision | `a90e2786-f6e0-5480-94a4-fb28fd000edf` · v2 · `b50ff5d3bcbf07de4b709ae0d9017a9df04fec49481fb14c224a709c85b0875b` |
| package | ArtifactVersion `7de25ac8-46e4-46da-b112-f805f16ebaaa` · hash `200b3be30b92ccff3b0efb26881d5654ab4b53162afe73d4e7f34bed3b0454bd` |
| technical lineage/niche/market/consistency | `PASS` |
| human review | `PASS`; task `a99f2ad4-9b1d-4bc6-bafc-024ecd7e9c56` is `completed` |
| approval | `ef766b1d-c1a5-43b8-be98-0751bd055653`; scope `PKG1_MARKET_REVISION_PACKAGE_PLANNING` |
| immutable receipt | ArtifactVersion `a35c55b8-6887-4e60-a19c-22928205c572` · hash `24a2d4c7b0dec7394a8b78ab646f66750fbca35282700d50dcde77bd304c2231` |
| destination | `PENDING_PLATFORM_ID` |
| publish risk | content `REVIEW_REQUIRED`; execution `BLOCK` |
| upload / publish execution | `false` / `false` |
| production package approved | `true` |
| MR1 re-approval / execution | old approval `TERMINAL` / `BLOCKED_REQUIRES_NEW_MR1_APPROVAL` |
| historical provider/render/Drive/YouTube calls | `4 / 0 / 0 / 0` |
| Geo closeout | `PENDING_PRODUCTION_ACCEPTANCE` |
| PKG1 `SC-04` revision | `NOT_CREATED`; implementation and static checks are ready |

The source package approval remains immutable planning authority, but the old
MR1 approval is terminal after execution began. The next allowable sequence is
Geo closeout, immutable `SC-04` revision, exact human revision PASS, then a
fresh exact-target MR1 re-approval. Publishing remains unauthorized.

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

The 2026-07-17 visual hold was resolved far enough to start the historical MR1
run, which then exposed the `SC-04` semantic-fit defect. Historical passes and
failed execution evidence remain immutable; the current repair boundary is:

```text
GEO_DELIVERY_CLOSEOUT_FINAL=PENDING_PRODUCTION_ACCEPTANCE
SC04_VISUAL_REPAIR=PENDING_PRODUCTION_ARTIFACT
PKG1_SC04_REVISION_HUMAN_REVIEW=NOT_CREATED
MR1_OLD_RUN=BLOCKED_REQUIRES_NEW_MR1_APPROVAL
PROCEED_TO_MR1_REAPPROVAL=false
PROCEED_TO_MR1=false
DESTINATION_STATUS=PENDING_PLATFORM_ID
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
9. LPRO1 — `PASS` on 2026-07-19; the offline MP4 is a non-publishable review candidate and not a production FinalMediaRef.
10. PKG1 visual/provider/cost/disclosure revision — technical and exact human review `PASS`; production package approved while destination/publish remain not ready.
11. Exact-target MR1 approval `4ccc7185-e760-4470-aba9-857ab0a18f77` — historical `PASS`, now terminal after execution began.
12. Historical MR1 run — blocked after two consumed `SC-04` Pexels semantic-fit failures; narration and forced alignment are preserved as candidate reuse evidence.
13. Geo/market delivery verification and immutable closeout — implementation ready; production acceptance pending.
14. Immutable `SC-04` native-motion revision plus exact human review — not yet created.
15. Fresh MR1 re-approval bound to the approved revision — not yet created.
16. Fresh MR1 run, Drive verification, and human full-watch.

The old PKG1/MR1 approval remains historical evidence; it is not rewritten or
treated as authority for the new route.

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

Historical status: `PASS`. Package-manifest v2 remains approved under `operator-approval://pkg1/small-team-ai/final-package-and-mr1`; prior `REVIEW_REQUIRED` evidence is retained as resolved history.

Market revision status: `PASS / PRODUCTION PACKAGE APPROVED`. Revision v2 is a superseding project under exact Market v3 authority; it does not reuse the historical package/MR1 approval. Approval `ef766b1d-c1a5-43b8-be98-0751bd055653` covers only exact package planning and preparation for a separate MR1 re-approval.

Create the first real production package using the frozen runtime path.

Rules:

- Runtime LTS verifier must still pass.
- Provider stack drift guard must still pass.
- Package queue must be repaired before render/upload handoff.

### 5. MR1 - First Real Media Render

Status: `BLOCKED_REQUIRES_NEW_MR1_APPROVAL`. Exact historical single-run MR1
approval `4ccc7185-e760-4470-aba9-857ab0a18f77`, approval content hash
`4a8c259debc1ae3f94feb7c5be959e0d42bca048911b052a221eda7373d1c25c`,
receipt `artifact-version://d875858d-46fe-4ce5-a89c-785f266c6b4c` và readiness
`artifact-version://432f42be-3a17-400a-a97d-2658b05a2ebc` bind revision
`a90e2786-f6e0-5480-94a4-fb28fd000edf` plus package
`7de25ac8-46e4-46da-b112-f805f16ebaaa`. Historical run:
`020bf2a7-eb17-41a3-ac3c-301b5c6b41fd`.

That run made four logical provider calls: successful ElevenLabs narration,
successful forced alignment, and two consumed failed Pexels `SC-04` attempts.
It produced no render, Drive archive, upload, or YouTube call. The approval is
terminal and cannot authorize the repaired route.

Target sequence after Geo closeout, immutable revision, human revision PASS,
and fresh MR1 re-approval:

- Revalidate and reuse only exact narration audio and forced alignment when all
  request, byte-hash, settings, rights, and QC bindings still match
- Build a fresh canonical timeline and captions
- Use native scenes for `SC-01/02/03/04/05/06/08`; `SC-04` is
  `NATIVE_MOTION_GRAPHIC`
- Use Pexels only for `SC-07` and `SC-09`
- Native render and MediaQC
- Canonical Drive archive verification
- Human full-watch pause
- Finalization supplement on Drive only after human PASS, then FinalMediaRef

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

`GEO_DELIVERY_CLOSEOUT_FINAL=PENDING_PRODUCTION_ACCEPTANCE`

`SC04_VISUAL_REPAIR=PENDING_PRODUCTION_ARTIFACT`

`PKG1_SC04_REVISION_HUMAN_REVIEW=NOT_CREATED`

`MR1_OLD_RUN_FINAL=BLOCKED_REQUIRES_NEW_MR1_APPROVAL`

`MR1_FRESH_REAPPROVAL=NOT_CREATED`

`MR1_EXECUTION=BLOCKED`

`PROCEED_TO_MR1_REAPPROVAL=false`

`PROCEED_TO_MR1=false`

`VSR1_FINAL=PASS`

`PROCEED_TO_IMG1=true`

`IMG1_FINAL=PASS`

`PROCEED_TO_VQC1=true`

`VQC1_FINAL=PASS`

`IMG_CANARY_V3_FINAL=PASS`

`ARCHIVE_VERIFIED=true`

`CH1_FLEX_V2_FINAL=PASS`

`LPRO1_FINAL=PASS`

`PKG1_MARKET_REVISION_TECHNICAL=PASS`

`PKG1_MARKET_REVISION_HUMAN_REVIEW=PASS`

`PKG1_MARKET_REVISION_FINAL=PASS`

`PRODUCTION_PACKAGE_APPROVED=true`

`MR1_OLD_RUN_FINAL=BLOCKED_REQUIRES_NEW_MR1_APPROVAL`

`MR1_FRESH_REAPPROVAL=NOT_CREATED`

`PROCEED_TO_MR1_REAPPROVAL=false`

`PROCEED_TO_MR1=false`

Next checkpoint: run the Geo verifier and immutable closeout against the real
database, build the exact `SC-04` revision, and pause for human revision review.

The registered route is `google_gemini_image`, separate from
`google_veo`. Its default model is `gemini-3.1-flash-image`; generated
pixels are only a visual foundation, while exact text/numbers remain native
overlay authority. The V3 paid canary and its Drive closeout passed; CH1
Market v3, NICH1, D2P1 and LPRO1 are PASS. PKG1 Market Revision and its
exact human closeout remain immutable historical PASS. The old MR1 run made four
logical provider calls but no render, Drive, upload, or YouTube call. The new
Geo/SC-04 repair implementation has made no production or provider claim.
