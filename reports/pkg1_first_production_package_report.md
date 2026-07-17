# PKG1 first production package report

Date: 2026-07-17
Channel: `small-team-ai`
Technical verdict: **PASS**
Human-review verdict: **PASS**
Final state: **PASS**
MR1 entry: **READY; execution NOT_STARTED**

## Selection and admission

No approved existing DailyIdeaDecision was available. PKG1 used the operator-approved fallback topic, **How One Automation Can Save a Small Team 20 Hours Every Week**, with explicit scenario-only framing.

- EditorialCalendarSlot: `66846b1c-9825-4c6c-b54e-dea19bca277d`
- ChannelDailyRun: `9f6b15d1-255b-4371-9b80-1ee6b42ee7c9`
- ContextPackSnapshot: `e5a12e69-3804-4525-84ae-55c58e6ae3a6`
- ChannelStatePackSnapshot: `6625e63d-e838-4049-9283-a3ebf1a9bed4`
- DailyIdeaDecision: `06b4d1ff-a2dc-4754-bd27-2a63cd367ac7`
- ProjectAdmissionDecision: `e99f7468-5689-4ac3-ab43-2ba68a4f59e0`
- VideoProject: `e601fde5-e502-4c38-ae51-dd7e8d149b4e`
- Package artifact: `e05f33f6-ea0f-44c6-9116-55b8d3b5a33c`

IdeaGate passed before the VideoProject was created. The builder is idempotent and will not create a second PKG1 VideoProject.

## Frozen snapshot lineage

- ChannelProfileVersion: `f5e45981-51eb-4c24-95a8-f9f5db761195` (v1)
- CompiledChannelPolicySnapshot: `f9201609-faad-4b68-aebf-b56679d0bde6`
- native-render hash: `7ad28ebb279378468aca6c22f9e3da48401778af4965bae27156e3df3200975b`
- creative-quality hash: `67b44172c858a41301041b4a0a38d2083efbf8f2b4badf690ccf7730eda999c1`
- provider-use hash: `9bec7008598fccbcebdfcc42ae60c60432275dd0d88f1f744f9032471ece6d6b`
- budget hash: `fb6e7e53bcf7200a97e1994a83f9b5bfabeb9edd76a6332e1af2c9856b3fba3e`
- FormatIdentityContract hash: `8522fb38cdfe3ff6ae615d39b7d1c8ff2a6fb34a33363276bd3ebea98a320cbc`

No active profile or snapshot was mutated or replaced.

## Package content

The package contains 22 current ArtifactVersions. Research and source packs contain only the operator-approved topic, deterministic arithmetic, and frozen policy evidence; there are no external measured claims. The ClaimEvidenceLedger holds three claims, including `CLM-001` as `ILLUSTRATIVE_SCENARIO` with `5 × 1 × 4 = 20` and explicit disallowed universal wording.

The English US documentary/explainer script has 976 words in 9 segments. SpokenTextNormalized contains 1,136 tokens, complete segment mapping, deterministic hashes, and no provider timing. The advisory pacing interval is 6.30–7.51 minutes against the policy's 6–12 minute range.

Originality is a first-episode same-channel baseline. The visual plan contains 6 native explanatory scenes and 3 meaningful Pexels-supporting drafts. Stock is never evidence. There is no AI hero request because Veo would be filler for this mechanism-led episode. No canonical scene timestamp, final caption cue, SRT, CanonicalMediaTimeline, or strict NativeRenderPlan exists.

The cost artifact uses the ElevenLabs budget catalog and the approved Veo price catalog. Estimated incremental cash is USD 0.00, hard cap USD 1.00, and actual cost is `null`. Provider plans are future drafts only.

Rights/disclosure artifacts require Pexels provenance, voice provenance, metadata and thumbnail truthfulness, synthetic-media review, manual YouTube upload, and archive-before-purge. PublishPackage and ManualPublishChecklist remain drafts without final media or an upload task.

## Gates and revisions

All 18 required deterministic pre-render gates passed. The seven audio/media-dependent gates are stored as `NOT_RUN`, never as PASS. Real-package automatic revision cycles: 1.

Cycle 1 found that the VisualPlan and CompiledAssetRequestPlan referred to the VisualDirectionContract through the manifest but did not carry its exact content hash. The repair created VisualPlan v2 (`a02a24e8-23a9-4dac-a1f7-d1ba6d676163`), CompiledAssetRequestPlan v2 (`ca1c025c-0fa2-44e4-a9b8-857ffd6e18d3`), gate-results v2 (`4dacbb61-3a03-42cf-a1e5-8852ef26b227`), and package-manifest v2 (`4d157930-de56-406c-82da-69d13ec675fd`). The first check recorded `BLOCK`; the post-repair rerun recorded `PASS`. Every current plan now binds hash `1d6c1627d439932292db7596a0b901137360b4346e6285393c07d823f943c111`. All v1 versions remain unchanged. The isolated repair tests also prove that a third automatic cycle is blocked.

The required regression suite passed: **129 tests**, with one existing Starlette deprecation warning.

## No-execution proof

Captured builder counts were unchanged before and after package construction:

| Record | Before | After |
|---|---:|---:|
| ProviderJobSnapshot | 0 | 0 |
| PaidProviderCallLedger | 0 | 0 |
| MediaRenderJob | 0 | 0 |
| FinalMediaRef | 0 | 0 |
| HumanUploadTask | 0 | 0 |
| UploadedVideo | 0 | 0 |

Historical rows also remained untouched: 674 legacy ProviderAttempt rows, 4 CloudMediaRef rows, and 3 MediaOffloadJob rows existed; rows created at or after the PKG1 VideoProject timestamp are 0 for all three.

No Pexels, ElevenLabs, forced-alignment, Veo, NativeFFmpeg production render, Drive, or YouTube operation was executed.

## Human approval closeout

The operator reviewed all six `REVIEW_REQUIRED` areas—Script, Originality, Visual Direction, Asset Request Plan, Cost / Budget, and Rights / Disclosure—and approved all six under `operator-approval://pkg1/small-team-ai/final-package-and-mr1`.

Approval was persisted through eight exact-target `ApprovalDecision` records. Package approval `195ddda4-2490-4411-9735-ae5b349a7d38` binds package-manifest v2 (`4d157930-de56-406c-82da-69d13ec675fd`, hash `0e674d5f75aae45e9c999133ccc799fa48c36933e5d709435cc099d628beb328`). MR1 paid approval `ba688de8-4274-4414-9be1-e8dda827b97e` binds ProviderExecutionPlan v1 (`440a964a-ba67-4d51-97b5-a01741447611`, hash `944e6ab726a10aa80289628093aec64e36383f2f44831edd09171196a163a885`).

Review task `4a4ce6c3-9fa8-4c1f-aac1-5dde6cb97046` is `completed` against the exact package-manifest v2. The prior `REVIEW_REQUIRED` reason codes and revision evidence were retained and linked to the approval ref. The v1 final-review task remains `cancelled` as superseded, not relabeled.

## MR1 opening state

MR1 readiness ArtifactVersion `cd1d93f7-29b0-4af3-9fa3-b877ddc0457b` (hash `621f22c0ddc8da27466bec139ea1ef176ff454e54bedf75802d7b5ce637cf553`) freezes the exact project/profile/snapshot and approved package references, including Script v1, SpokenTextNormalized v1, VisualDirectionContract v1, ProviderExecutionPlan v1, and CostEstimateSnapshot v1. It does not perform a latest-profile lookup.

MR1 has not started. ProviderExecutionPlan remains `execution_enabled=false`; the PKG1 provider boundary remains `DISABLED`. No ElevenLabs, forced alignment, Pexels, Veo, NativeFFmpeg production render, Drive, YouTube, FinalMediaRef, HumanUploadTask or UploadedVideo operation was performed. Provider attempts created since this VideoProject remain 0.

`PKG1_SCRIPT=PASS`
`PKG1_ORIGINALITY=PASS`
`PKG1_VISUAL_DIRECTION=PASS`
`PKG1_ASSET_REQUEST_PLAN=PASS`
`PKG1_COST_BUDGET=PASS`
`PKG1_RIGHTS_DISCLOSURE=PASS`
`PKG1_TECHNICAL=PASS`
`PKG1_PROVIDER_EXECUTION=DISABLED`
`PKG1_REVISION_CYCLES=1`
`PKG1_HUMAN_REVIEW=PASS`
`PKG1_FINAL=PASS`
`MR1_PAID_EXECUTION_APPROVAL=APPROVED`
`MR1_ENTRY=READY`
`MR1_EXECUTION=NOT_STARTED`
`MR1_PROVIDER_CALL_COUNT=0`
`MR1_RENDER_STATUS=NOT_STARTED`
`MR1_HUMAN_REVIEW=PENDING`
`PROCEED_TO_MR1=true`
