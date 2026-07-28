# VCOS Final Architecture Closeout Audit

Date: 2026-07-27  
Mode: source-grounded audit and replan only  
Repository: `/Users/sangss/Desktop/video-creator-rag`

## 0. Repository lock and audit method

```text
TRACKED_HEAD=f037d84c4a28707d40777e3e44bc7a2b565304a8
BRANCH=main
ALEMBIC_HEAD=0042_mr1_final_lineage (single head)
WORKTREE_STATUS=DIRTY: 1 modified tracked report, 21 untracked paths/groups; no dirty or untracked file was used as runtime authority
```

The tracked modification is `reports/current_roadmap_checkpoint_report.md`. The
untracked set includes four `app/services/*sc07_sc09*.py` files, SC-07/SC-09
reports/receipts, and `tests/offline/`. They were deliberately excluded from
source conclusions. Evidence order was tracked source, ORM/migrations, tracked
tests, config/prompts, then local runtime observations. Previous reports were not
used as primary evidence.

The configured local runtime database was queried read-only. It does **not**
contain ApprovalDecision
`de4edc50-23df-5065-b652-69e5978825b4`; therefore the claimed MR1 approval/hash
is not currently executable runtime authority. `MR1RealProductionService.start`
loads and locks that exact row before doing anything
(`app/services/mr1_real_production.py:225-245`). The claimed receipt exists only
in the excluded untracked set.

Targeted audit tests:

```text
44 passed, 42 skipped, 1 failed
failure: tests/test_migration.py still expects 0036_hpr1_veo, actual 0042_mr1_final_lineage
skips: historical M5/M7/M8/M9/M10 fixture paths disabled after M12.1R

7 passed
tests/test_mr1_runtime_readiness.py
tests/test_lpro1_long_production_orchestrator.py
```

No paid provider, real renderer, upload, MR1 execution, commit, or tag was run.

## 1. Executive verdict

```text
FINAL_ARCHITECTURE_CLOSEOUT_READY=NO
MR1_EXECUTION_READY=NO
POST_UPLOAD_LOOP_CLOSED=NO
NORMAL_OPERATION_WITHOUT_CLI_DB=NO
```

The strongest implemented parts are MR1 provider attempt/budget/Drive crash
safety, immutable artifacts, LPRO1 temporal authority, strict destination
verification, and manual-publish-only policy. They are not connected into one
authenticated, resumable normal-operation flow.

The principal blockers are:

1. the named MR1 approval is absent from the configured runtime DB;
2. production-changing APIs and decisions are largely unauthenticated and trust
   actor IDs supplied in request bodies;
3. lane, typed assignment, true standalone, SeriesPlan/SeriesRun and episode
   concurrency authority do not exist;
4. MR1 hard-codes 6–12 minutes and M12.2 can add filler to reach a word budget;
5. Daily stops at idea creation and no worker connects the later state machines;
6. blanket pre-render reviews remain mandatory;
7. an M12.2r bypass can create an upload task from a pre-render package;
8. final human semantics are PASS/FAIL, not identity-bound UPLOAD/DO_NOT_UPLOAD;
9. analytics windows, cohort isolation, scheduling and learning exclusion are
   incomplete;
10. analytics attribution directly mutates memory confidence used in retrieval.

## 2. Source-grounded current architecture

### 2.1 Production lanes and editorial assignment

No tracked contract, ORM column, migration or runtime enum defines
`DAILY_SHORT`, `LONG_FORM`, `LONG_DERIVED_SHORT`, `SERIES_EPISODE`,
`STANDALONE`, or the four assignment modes.

Current facts:

- `ChannelProfileInput.series_plan` is required raw JSON
  (`app/contracts/profile.py:13`); it is not SeriesPlan/SeriesRun state.
- `EditorialCalendarSlot.series_key` and `format_hint` are nullable text
  (`app/db/models/m5.py:15-36`, `app/contracts/m5.py:35-48`).
- `DailyIdeaAgent` can return `proposed_series_key`
  (`app/services/m5.py:1366-1402`). The LLM output, rather than a deterministic
  resolver, therefore participates in assignment.
- `DailyIdeaDecision.proposed_series_key` is nullable text
  (`app/db/models/m5.py:350-385`).
- `VideoProject` has `project_type` but no production lane, content mode,
  series run or episode columns (`app/db/models/workflow.py:22-89`).
- NICH1 strict lineage may block when the expected series key is missing or
  conflicts, but it cannot express true standalone.
- `ProjectAdmissionService` already locks a DailyIdeaDecision, returns the first
  existing admission receipt, freezes policy/niche context, creates the project,
  and rolls the candidate back if Effective Context is not PASS
  (`app/services/m5.py:1801-1945`). This is the correct component to extend.
- No SeriesPlan or SeriesRun ORM/state machine exists. No active/capacity/next
  episode/pause/completion authority exists.

Disposition:

| Concept | Verdict | Smallest change |
|---|---|---|
| production lane | ADD | Typed field at slot, admission, project, package, publish and analytics lineage |
| assignment mode | ADD | Slot policy plus immutable admission decision |
| content mode | ADD | Resolver output frozen by ProjectAdmissionDecision |
| deterministic resolver | EXTEND | Extend ProjectAdmissionService; no new agent |
| SeriesPlan/SeriesRun lifecycle | ADD | Versioned authority tables and service |
| niche/market gates | REUSE + EXTEND | Reuse EffectiveContext/NICH1/market preflight; standalone never bypasses them |
| `series_key` | REMOVE from new authority | Retain only as v1 compatibility projection |

### 2.2 Duration authority

Duration is not end-to-end channel scoped:

- profile compilation preserves raw `format_strategy.long_form_minutes`
  (`app/services/profile_compiler.py:423`);
- channel contract supplies defaults when input is absent
  (`app/services/channel_contract.py:211+`);
- M12.2 falls back to 450 seconds and derives ±10%
  (`app/services/m12_2.py:4180-4219`);
- MR1 then requires the frozen limits to be exactly 360,000 and 720,000 ms
  (`app/services/mr1_real_production.py:2635-2669`).

This is a hard conflict with the final product truth. In addition,
`_expand_script_to_word_budget` appends rotating canned clauses until a target
word count is reached (`app/services/m12_2.py:4625-4669`). That is padding, even
though it is described as repair.

Target: type and freeze `{minimum,target,maximum}` once from the active profile
and policy into admission/package v2; every script, narration, timeline, render
and QC gate reads that exact hash. Insufficient editorial depth must block or
choose a shorter policy-allowed format; it must never be padded.

### 2.3 Human review boundaries

Tracked `ReviewTask.review_type` values in actual creation/query paths are
`evidence`, `editorial`, and `final_human`.

| Current boundary | Source | Classification |
|---|---|---|
| D2P1 research evidence approval | `app/services/d2p1.py:1175-1271` | REMOVE_FROM_NORMAL_FLOW; exception-only for unresolved rights/compliance |
| D2P1 package final_human review | `app/services/d2p1.py:1789-1868` | REMOVE_FROM_NORMAL_FLOW |
| LPRO1 requires `human_review_state=PASS` | `app/services/long_production.py:1119` | REMOVE_FROM_NORMAL_FLOW |
| PKG1/market/SC04 exact package reviews | tracked PKG1 revision services | LEGACY_ONLY after package v2 |
| packaging patch approvals | `app/services/r3d9_ux2.py` | deterministic/local repair normally; exception-only if material |
| provider continuation review | MR1 Pexels continuation/amendment paths | REMOVE_FROM_NORMAL_FLOW; policy retry or external block |
| MR1 final full-watch ReviewTask | `app/services/mr1_real_production.py:8492-8593` | KEEP_AS_FINAL_VIDEO_DECISION, change semantics |

`ReviewMediaCandidate` is a Pydantic artifact, not a first-class ORM row
(`app/contracts/long_production.py:259-279`). MR1 persists it as an
ArtifactVersion and creates a final_human ReviewTask, but there is no normal API
or dashboard action for identity-bound `UPLOAD | DO_NOT_UPLOAD`. The current
command records PASS/FAIL and creates a `mr1_human_full_watch_receipt`.

The replacement readiness rule should reuse:

```text
deterministic GateRun(PASS)
+ D2P1/package readiness receipt
+ exact profile/policy/admission/package hashes
= READY_FOR_PRODUCTION
```

Do not manufacture an ApprovalDecision whose actor is “system”.

### 2.4 Canonical package and media lineage

Current package roles:

| Object | Actual role | Target |
|---|---|---|
| FirstScriptedVideoPackage | M12.2 editorial/pre-media bundle | preproduction input; legacy reader |
| PKG1 package_manifest | MR1 special exact package authority | legacy-only after v2 |
| LongFormRenderPackage / RenderPackageSnapshot | render inputs/projections | render projection |
| PublishHandoffPackage | publish command/read package | publish projection only |

The target canonical truth is one immutable v2 ProductionPackage artifact
created after automated readiness, binding admission, duration, content mode,
series/standalone, destination, provider/budget plan and all input hashes.
CanonicalMediaTimeline remains temporal authority; render and publish packages
are projections from that package.

The critical bypass is
`PublishHandoffLedgerService.create_upload_task_from_package`
(`app/services/m12_2r.py:125-185`): a
`FirstScriptedVideoPackage` in `READY_FOR_HUMAN_REVIEW` can directly create a
`READY_FOR_HUMAN_UPLOAD` task without FinalMediaRef, rendered MP4, verified
Drive archive, or final video decision. This path must be disabled for all v2
writes.

`FinalMediaRef` has checksum and immutable lineage artifact support
(`app/db/models/m10_2.py:354+`, migration 0042), which should be reused.
However, `HumanUploadTask` has no required FinalMediaRef/final-decision binding
(`app/db/models/m10_1.py:451+`).

M7 is the stronger publish foundation, but a mismatch creates
`ManualPublishConfirmation.REVIEW_REQUIRED`
(`app/services/m7.py:730-871`) and `accept_confirmation` accepts only SUBMITTED
(`app/services/m7.py:888-907`). No resolution transition exists: this is a
dead-end state.

YouTube cannot prove the local binary checksum after upload. The attainable
invariant is:

1. authenticated operator selects the exact checksum-bound FinalMediaRef;
2. task records an identity-bound file-selection attestation;
3. confirmation verifies destination, video ID, duration and all observable
   metadata;
4. UploadedVideo freezes the reviewed checksum and attestation.

That proves VCOS instructed and recorded the reviewed binary, not a
cryptographic checksum read back from YouTube. Any observable mismatch must
be `REJECTED_MISMATCH`, `BLOCKED_DESTINATION`, or an explicit resolved variance,
never a generic dead end.

### 2.5 Provider, cost, retry and archive

MR1 has substantial reusable safety:

- ApprovalDecision and run are locked `FOR UPDATE`; approval is single-run;
- provider substitution and automatic retry are false;
- provider requests carry operation-specific idempotency keys/fingerprints;
- durable monthly budget is reserved before provider execution and settled on
  success or consumed failure;
- narration, alignment and Pexels use explicit attempt ledgers;
- Drive uses a local journal, exclusive lock, exact remote-name reconciliation,
  checksum/readback verification, and fails closed on uncertain outcomes
  (`app/services/mr1_drive_archive.py:146+`);
- NativeFFmpeg remains final composition authority.

Gaps:

- normal policy-authorized retry is replaced in Pexels by a human continuation
  approval path;
- ProviderAttempt has no uniqueness constraint on idempotency/attempt identity;
- generic RetryPolicy and DeadLetterJob foundations are not connected to MR1 or
  the lane workflow;
- the initial archive precedes final human decision; MR1 adds a finalization
  supplement, but the later publish confirmation is not archived back into the
  canonical package;
- stale configuration still exposes Pixabay credentials and an Envato
  documented role (`app/core/config.py:188`, provider config catalogs). Active
  provider-stack code rejects old fallbacks, so this is configuration drift,
  not an active fallback.

Provider outcomes should be normalized to:

```text
AUTO_RETRY_WITHIN_POLICY
POLICY_AUTHORIZED_LOCAL_REPAIR
BLOCK_EXTERNAL_FAILURE
```

### 2.6 Orchestration and resume

There is no background worker/scheduler implementation.

- `POST /channel-daily-runs/{id}/execute` builds retrieval/context/state packs,
  creates `DailyIdeaDecision`, marks the run COMPLETED, and stops
  (`app/services/m5.py:1680-1776`).
- preflight and admission are separate APIs
  (`app/api/routes/production_planning.py:318-344`);
- D2P1 run/resume is a separate API (`:245-278`);
- LPRO1 run/resume is another API (`:280-316`);
- MR1 real production has no normal application API/dashboard entry;
- analytics sync, M9 diagnostics and M10 learning are separate manual APIs.

`ProductionArtifactRunService` is not a suitable full coordinator as-is. Its
real-provider method is deliberately blocked and it represents obsolete M6
artifact production (`app/services/m6.py:97-154`).

The smallest reuse is:

- retain ChannelDailyRun, ProjectAdmissionDecision, D2P1 receipt, LPRO1/MR1
  state artifacts as stage authorities;
- use existing `DomainEvent` rows as the durable outbox;
- add one authenticated worker/dispatcher that claims unpublished events,
  invokes idempotent stage handlers, then marks events published;
- reuse RetryPolicy, DeadLetterJob, OpsIncident and existing idempotent service
  methods;
- use deterministic event/command IDs and handler receipts so a crash after an
  effect but before `published_at` safely replays.

This extends existing infrastructure; it does not introduce a new workflow
framework.

### 2.7 Short and derivative reality

- `format_hint` is text and is not a lane authority.
- Daily currently builds a long-form FirstScriptedVideoPackage through D2P1.
- `ShortRenderPackageService` validates 9:16 and under 59 seconds but only
  creates a package whose manifest says `real_render_executed=False`
  (`app/services/m10_2.py:1198-1255`).
- Native long render contracts are fixed at 1920x1080.
- No tracked source proves production of a 9:16 final MP4.
- M10.1 extracts ShortCandidate from latest `VoiceTimelineSnapshot`, not from
  an approved CanonicalMediaTimeline (`app/services/m10_1.py:1403-1421`).
- parent references exist, but the derivative edge is not a v2 immutable,
  hash-bound admission/package lineage.

Therefore Daily Short and Long-derived Short are both PARTIAL and must remain
separate. A derivative must not be reintroduced as a Daily idea.

### 2.8 Authentication and authorization

`app/main.py:49-71` mounts production, provider, publish, analytics, learning,
ops and review routers without a global auth dependency. `require_permission`
exists in `app/services/rbac.py`, but production routes do not use it. Many
commands accept `created_by_user_id`, `confirmed_by_user_id` or `decided_by`
from the request.

There are isolated authenticated endpoints (for example one package recheck
path in `app/api/routes/package_review.py:241+`), but no consistent mutation
boundary. Actor-role strings are not authorization. This is P0 because an
unauthenticated caller can trigger paid/planning/publish state and spoof the
human identity.

### 2.9 Analytics, maturity and learning

The current chain is only manual:

```text
UploadedVideo READY_FOR_ANALYTICS
→ manual public/owner sync
→ M8 snapshot
→ manual observation window
→ manual M9 health run
→ manual M10 candidate
```

Specific defects:

- M10.3 emits M8 snapshots with `observation_window="UNKNOWN"` and
  `observed_from=None` (`app/services/m10_3.py:1209-1211`);
- there is no uniqueness/idempotency authority per
  `(uploaded_video, metric_authority, observation_window)`;
- M9 windows are 1h, 6h, 24h, 48h and 7d, not 24h, 72h, 7d and 30d
  (`app/services/m9.py:43-55`);
- M9 loads `latest_analytics_snapshot_id`, then latest arbitrary snapshot,
  rather than the requested window (`app/services/m9.py:1249-1271`);
- an early run becomes terminal `INSUFFICIENT_DATA`, so it cannot resume;
- UploadedVideo/analytics lack lane/content-mode/series cohort lineage;
- thresholds are not format-specific;
- incidents do not reliably exclude a video from learning;
- one-video candidates can reach review/playbook paths without a mature,
  comparable cohort authority.

The good reusable foundations are UploadedVideo, ObservationWindow, public and
owner read-only YouTube clients, AnalyticsSnapshot, PostPublishHealthRun,
LearningCandidate and proposal/review models.

### 2.10 Silent mutation of runtime truth

`QualityDeltaAttributionService` directly changes
`MemoryFacet.confidence_label` before writing a ledger that says human review
may be required (`app/services/r3d7.py:625-649`). Retrieval scoring immediately
uses that mutable label (`app/services/r3d6.py:661-676`).

This is a P0 learning-governance violation: analytics can silently change future
agent context ranking. Replace it with an immutable
MemoryConfidenceChangeProposal; only an authenticated approval may create a new
facet/version and switch a versioned active pointer.

No tracked path was found that directly mutates active ChannelProfileVersion,
CompiledChannelPolicySnapshot or TargetMarketProfile from analytics. Series
truth cannot be mutated because it does not yet exist.

### 2.11 Incidents, geo and economics

OpsIncident already carries type/severity/state/references/reasons/next action,
but lacks first-class project/video/stage, learning exclusion, retry eligibility,
deduplication and resolution evidence (`app/db/models/ops.py:316-339`).

GeoDistributionTracker and SelfFundingGate exist as deterministic contract
services, not an integrated persistent runtime. The geo diagnostic has a useful
three-comparable-signal maturity pattern. SelfFundingGate correctly requires
finalized platform revenue and rejects estimates, but there are no
ProjectPnlSnapshot or ChannelPnlSnapshot models and owner analytics does not
currently collect authoritative finalized revenue. Series health/progress does
not exist.

### 2.12 Storage and retention

MR1 Drive verification should be reused. Production local purge is not closed:
AS1 forbids real purge; M10.5 can unlink a local media ref after VERIFIED but
permits `CHECKSUM_UNAVAILABLE`, which is insufficient for authoritative final
packages. Publish receipt is also not folded into the final Drive archive.
Only exact checksum/readback `ARCHIVE_VERIFIED` should make an item purge
eligible.

### 2.13 Exact normal-flow transition audit

This table is the current shared backbone. The five lane graphs below identify
where a lane does not actually enter or diverges.

| Transition | Current service / state | Artifact and immutable binding | Trigger / idempotency / retry | Operator stop or blocker |
|---|---|---|---|---|
| channel init | Channel profile/version and compiler services | immutable ChannelProfileVersion + CompiledChannelPolicySnapshot hashes | separate authenticated-ish init APIs; version creation is durable | no automatic editorial schedule |
| editorial planning | EditorialCalendarSlot CRUD | policy snapshot FK; series/format are nullable text | caller API/manual creation; no scheduler or deterministic assignment | caller chooses text values |
| topic creation | ChannelDailyRunService PENDING→RUNNING→COMPLETED | ContextPack, ChannelStatePack, DailyIdeaDecision | POST execute; rejects repeat unless PENDING; no automatic retry | stops after idea |
| niche/market | NICH1/EffectiveContext and IdeaMarketPreflight | gate evidence and snapshot refs | preflight is a separate API; admission recompiles context | REVIEW_REQUIRED/BLOCK may require a new manual call/input |
| assignment/admission | ProjectAdmissionService | first immutable ProjectAdmissionDecision; admitted VideoProject | separate API; locks idea and returns first existing receipt | no typed lane/mode/run |
| research | DailyToPackageOrchestrator | D2P1 receipt and research artifacts | separate run/resume API; artifact-based resume | mandatory evidence ReviewTask |
| script/package | D2P1 + M12.2 package builder | FirstScriptedVideoPackage and receipt hashes | same manual run; partial resume | package final_human ReviewTask |
| provider plan | package/MR1 approval authority | provider plan, scope and approval hash | package-specific; current MR1 only after exact ApprovalDecision | claimed approval absent in DB |
| budget reservation | MR1MonthlyBudgetAuthority | durable reservation/request hash | atomic reserve; success/failure settlement; resumable | package approval and exact cap required |
| narration/alignment/assets | MR1RealProductionService | attempt ledger, request hashes, provider evidence | service/script entry; no normal API/worker; no automatic retry/substitution | Pexels continuation may require human approval |
| media timeline | LongProduction/MR1 | CanonicalMediaTimeline hash | created inside manual LPRO1/MR1 run | Daily/D2P1 does not call it |
| render | NativeFFmpeg long renderer | NativeRenderPlan + output checksum | manual LPRO1/MR1; MR1 state artifacts support resume | only 16:9 production proof |
| QC | technical/creative QC services | QC reports/hashes bound in ReviewMediaCandidate | inside LPRO1/MR1 | creative REVIEW_REQUIRED still reaches human boundary |
| Drive archive | MR1DriveArchive | journal, remote IDs, exact checksum/readback receipt | inside MR1; strongest crash-safe idempotency | uncertain remote outcome fails closed |
| final human decision | MR1 final_human ReviewTask | candidate hash + full-watch receipt | service command, no normal app API/UI | PASS/FAIL, not UPLOAD/DO_NOT_UPLOAD |
| upload task | M12.2r or M7 | HumanUploadTask | separate API; partial duplicate lookup | M12.2r bypass does not require final media |
| manual publish confirmation | M7 | PublishHandoffPackage + ManualPublishConfirmation | separate API then separate accept API | mismatch can dead-end in REVIEW_REQUIRED |
| UploadedVideo | M7 accept or M12.2r backfill | handoff/policy/destination refs | idempotent only after ACCEPTED; duplicate platform check | exact reviewed-binary binding absent |
| analytics sync | M10.3→M8 | sync run and AnalyticsSnapshot | manual API; no window uniqueness/scheduler/backoff | owner auth/data availability |
| maturity/diagnostics | ObservationWindow/M9 | window and health run | manual APIs; early run becomes terminal | arbitrary latest snapshot may be used |
| learning | M10/M11/R3D7 | candidate/proposal/playbook/memory rows | manual API/review | cohort authority missing; memory may mutate silently |

### 2.14 External operation safety matrix

| Operation | Scope / attempts | Idempotency and budget | Crash/retry behavior | Verdict |
|---|---|---|---|---|
| ElevenLabs narration | exact MR1 approval; one attempt | request/fingerprint ledger; monthly reservation and settlement | consumed audio can be recovered; no automatic retry or substitution | REUSE; add policy-authorized retry classification |
| ElevenLabs forced alignment | follows exact narration/package scope; one attempt | distinct operation key/fingerprint | persisted boundary; failure blocks | REUSE |
| Pexels supporting stock | exact scene/query scope; initial attempt plus current human-approved continuation | search/download submit ledgers and semantic evidence; no provider substitution | duplicate submit guards; current continuation review violates normal boundary | EXTEND: deterministic policy retry or BLOCK_EXTERNAL_FAILURE |
| Gemini Image | active provider capability elsewhere; current MR1 state reports NOT_REQUIRED | routed provider safety/cost infrastructure, not full lane orchestration | no current MR1 call permitted | KEEP capability; execute only when frozen package selects it |
| Google Veo | approved hero/metaphor role; current MR1 reports NOT_REQUIRED | provider routing/cost foundations; no current MR1 call | no silent fallback | KEEP capability; exact scoped package only |
| Google Drive | archive/finalization phases | journal, deterministic names, remote IDs, checksum/readback | reconciles uncertain response; blocks duplicate/unknown mutation | REUSE unchanged as archive authority |
| YouTube analytics | read-only OAuth/public scope; no paid budget | sync records exist, but no command/window uniqueness | manual retry may duplicate snapshots; no scheduler/backoff | EXTEND before closed post-upload loop |

## 3. Current and target call graphs

Legend: `//` is a manual/API/CLI stop. `[missing]` is absent runtime authority.

### A. DAILY_SHORT + SERIES_EPISODE

Current:

```text
ChannelProfileVersion/CompiledPolicy
→ EditorialCalendarSlot(series_key?, format_hint text)
→ POST ChannelDailyRun
→ POST execute
→ ContextPack + ChannelStatePack + DailyIdeaAgent
→ DailyIdeaDecision(proposed_series_key)
// STOP
→ manual preflight
→ manual ProjectAdmissionDecision + VideoProject(project_type=m5_daily_run)
→ manual D2P1
→ evidence human review
→ long-form FirstScriptedVideoPackage
→ package human review
→ manual LPRO1
→ 16:9 long render candidate
```

There is no current Daily Short series execution.

Target:

```text
typed DAILY_SHORT slot(SERIES_REQUIRED/PREFERRED)
→ outbox worker
→ idea candidate inside frozen niche/market
→ ProjectAdmissionService deterministic assignment
→ ProjectAdmissionDecision(SERIES_EPISODE, run, atomic episode)
→ VideoProject + v2 package readiness
→ Short script/timeline → 9:16 NativeRenderPlan
→ providers/budget → NativeFFmpeg → QC → Drive → FinalMediaRef
→ authenticated UPLOAD|DO_NOT_UPLOAD
→ HumanUploadTask only on UPLOAD
→ manual publish → verified confirmation → UploadedVideo(DAILY_SHORT cohort)
→ 24h/72h/7d/30d analytics → proposal-only learning
```

### B. DAILY_SHORT + STANDALONE

Current: same path as A; null `series_key` is nullable at schema level but can
be blocked by strict NICH1 and has no typed standalone semantics. It still
produces a long-form package.

Target: same as A, except admission freezes
`content_mode=STANDALONE`, all series fields null, a required
`standalone_reason_code`, and unchanged niche/market gates.

### C. LONG_FORM + SERIES_EPISODE

Current: there is no dedicated long-form planner. The only integrated entry is
the same DailyIdeaDecision path or manual project/package bootstrap, followed by
manual D2P1/LPRO1/MR1. Series is legacy text lineage only.

Target:

```text
dedicated LONG_FORM calendar slot
→ long-form planning trigger/outbox
→ topic + niche/market gates
→ ProjectAdmissionService deterministic assignment
→ immutable SERIES_EPISODE admission
→ automated research/script/package readiness
→ duration/depth gates
→ ElevenLabs → Forced Alignment → CanonicalMediaTimeline
→ Pexels/Gemini/Veo within frozen plan and budget
→ NativeFFmpeg → QC → Drive → FinalMediaRef
→ final decision → manual publish chain
→ UploadedVideo(LONG_FORM cohort)
→ mature analytics → series proposal, never direct run mutation
```

### D. LONG_FORM + STANDALONE

Current: no typed path; only null/legacy series text or manual bootstrap.

Target: C with `STANDALONE_REQUIRED` or deterministic OPEN_MIX/PREFERRED
fallback; freezes standalone reason and no series/episode IDs.

### E. LONG_DERIVED_SHORT

Current:

```text
parent VideoProject
→ latest VoiceTimelineSnapshot
// manual M10.1 candidate extraction/originality/selection
→ ShortRenderPlan
→ ShortRenderPackage(9:16,<59s,real_render_executed=False)
// STOP: no production 9:16 FinalMediaRef/publish loop
```

Target:

```text
approved long-form parent + approved CanonicalMediaTimeline/hash
→ deterministic derivative eligibility
→ immutable child admission(edge,parent FinalMediaRef/timeline/package)
→ 9:16 NativeRenderPlan → assets/reuse → NativeFFmpeg
→ QC → Drive → child FinalMediaRef
→ final UPLOAD|DO_NOT_UPLOAD → manual publish
→ UploadedVideo(LONG_DERIVED_SHORT cohort)
→ derivative analytics isolated from parent/long-form strategy
```

## 4. Missing-gap register

| ID | Sev | Current behavior and risk | Source evidence | Required action | Change | Blocks MR1 | Blocks freeze |
|---|---|---|---|---|---|---|---|
| G01 | P0 | Named MR1 approval absent; checkpoint cannot execute | local DB read-only; MR1 start lock at `mr1_real_production.py:225-245` | reconcile authoritative DB; never recreate same identity from untracked receipt | EXTEND | YES | YES |
| G02 | P0 | Production mutations and decisions mostly unauthenticated; actor spoofing | `app/main.py:49-71`; route bodies | global/session auth + per-command RBAC; actor from session | EXTEND | YES | YES |
| G03 | P0 | MR1 hard-codes 6–12 and M12.2 pads scripts | `mr1_real_production.py:2635-2669`; `m12_2.py:4180-4219,4625-4669` | typed channel duration lineage; remove padding | REMOVE/EXTEND | YES | YES |
| G04 | P0 | Lanes/modes/SeriesRun do not exist; assignment can be LLM text | M5 contracts/models/services | v2 typed assignment in ProjectAdmissionDecision | ADD/EXTEND | YES | YES |
| G05 | P0 | Daily and all later stages require separate calls; no worker | production routes and M5 execute | DomainEvent outbox worker with idempotent handlers/retry/dead-letter | EXTEND | YES | YES |
| G06 | P0 | blanket evidence/package/provider continuation human reviews | D2P1/LPRO1/MR1 | automated readiness; exception-only compliance; final decision only | REMOVE | YES | YES |
| G07 | P0 | pre-render package can create upload task; two publish lanes | `m12_2r.py:125-185`; M7 | disable bypass; one FinalMediaRef-derived task | REMOVE/EXTEND | YES | YES |
| G08 | P0 | no identity-bound UPLOAD/DO_NOT_UPLOAD; no exact task binding | MR1 final review; HumanUploadTask ORM | FinalVideoDecision and required FinalMediaRef/package/destination bindings | ADD | YES | YES |
| G09 | P0 | memory confidence mutates retrieval from analytics | `r3d7.py:625-649`; `r3d6.py:661-676` | stop mutation; proposal/version/approval boundary | REMOVE/ADD | YES | YES |
| G10 | P1 | Short package never produces production 9:16 MP4 | `m10_2.py:1198-1255` | extend NativeFFmpeg plan/renderer and FinalMediaRef lineage | EXTEND | NO* | YES |
| G11 | P1 | no series lifecycle or atomic episode reservation | no source implementation | SeriesPlan/Run state machine, row lock, unique run/episode | ADD | YES for series MR1 | YES |
| G12 | P1 | M7 REVIEW_REQUIRED is dead-end | `m7.py:730-907` | deterministic reject/block/variance resolution transitions | EXTEND | NO | YES |
| G13 | P1 | analytics windows arbitrary/latest; no scheduler/cohorts | M9/M10.3 cited above | window-keyed unique sync and resumable scheduler | EXTEND | NO | YES |
| G14 | P1 | incidents lack exclusion/retry/dedupe and are not auto-linked | OpsIncident model/service | extend existing incident model and hook stage failures | EXTEND | NO | YES |
| G15 | P1 | package authorities compete | M12.2/PKG1/M6/M7 | canonical v2 ProductionPackage; projections only | EXTEND/REMOVE | YES | YES |
| G16 | P1 | provider retry policy uses human continuation; attempt uniqueness incomplete | MR1 continuation and ProviderAttempt model | policy retry or fail; unique command/attempt identity | EXTEND | YES | YES |
| G17 | P1 | dashboard lacks lane/mode/assignment/final-decision surfaces | M11/R3D9/frontend searches | extend read models and UI; Advanced Details for hashes | EXTEND | YES | YES |
| G18 | P1 | geo/economic/series learning is contract-only or absent | geo_delivery services; no PnL/Series ORM | persist mature projections/proposals, finalized revenue only | EXTEND/ADD | NO | NO |
| G19 | P1 | no real channel-duration long-form qualification; many skipped legacy tests | targeted test result | 12 mandatory scenarios, one real duration MP4 | ADD/REMOVE stale | YES | YES |
| G20 | P1 | migration test asserts obsolete head | `tests/test_migration.py:207` | one 0043 migration; update head/upgrade/rollback tests | EXTEND | YES | YES |
| G21 | P2 | final archive lacks publish receipt; purge permits weak verification | MR1 supplement/M10.5 | append publish supplement; checksum-only purge eligibility | EXTEND | NO | NO |
| G22 | P2 | removed providers remain in settings/catalog documentation | config/core files | remove stale operational config while retaining historical readers | REMOVE | NO | NO |

`*` G10 does not block a long-form-only MR1, but blocks architecture code freeze.

## 5. Authority matrix

| Domain | Current authority | Duplicate/bypass | Target canonical authority | Migration |
|---|---|---|---|---|
| channel duration | raw profile/compiled category + M12 defaults | MR1 exact 6–12 check | typed profile → policy → admission/package duration contract hash | v2 fields/hash |
| niche | ChannelProfile, EffectiveContext, NICH1 | prompt/raw JSON may be weaker | existing deterministic gates, required for both modes | refs/version |
| editorial assignment | slot text + DailyIdeaAgent proposal + admission | caller/LLM `series_key` | ProjectAdmissionDecision resolver receipt | columns + hash |
| series | raw profile JSON / text key | title/playlist/legacy key | SeriesPlanVersion + SeriesRun + episode reservation | new tables/unique |
| package | FirstScripted/PKG1/RenderPackage | M12.2r upload bypass | immutable ProductionPackage v2 artifact | v2 artifact schema |
| media timeline | VoiceTimeline in legacy; CanonicalMediaTimeline in LPRO1 | derivative uses latest voice timeline | CanonicalMediaTimeline hash | derivative refs |
| final media | FinalMediaRef with v2 checksum lineage | upload task need not reference it | FinalMediaRef + archive receipt + FinalVideoDecision | FKs/not-null for v2 |
| upload | M12.2r HumanUploadTask and M7 handoff | pre-render task path | one HumanUploadTask from UPLOAD decision | FKs/checks/unique |
| UploadedVideo | M7 and M12.2r backfill | unverified package backfill | accepted strict confirmation bound to reviewed ref | lineage columns |
| analytics snapshot | M8 + M10.3 projections | latest arbitrary snapshot | unique authority/window snapshot | unique key/window |
| maturity | M9 ObservationWindow | wrong windows/terminal early run | 24h/72h/7d/30d resumable window | enum/check/unique |
| geo | pure tracker/diagnostic | caller-supplied input | frozen expected lineage + authoritative analytics actual | persisted projection |
| learning | M10 candidates/reviews/playbook | single case; no cohort isolation | mature cohort → typed proposal → authenticated approval | cohort/proposal refs |
| revenue | SelfFundingGate contract | no finalized revenue ingestion | finalized platform revenue ledger/PnL snapshots | new snapshots |
| memory | MemoryFacet mutable confidence | R3D7 direct mutation | immutable facet versions + approved active pointer | proposal/version |

## 6. Deterministic assignment matrix

| Situation | Current verdict | Target mode | Assignment mode | Required series state | Reason / block |
|---|---|---|---|---|---|
| exact valid series slot | PARTIAL/PASS as text | SERIES_EPISODE | SERIES_REQUIRED or PREFERRED | APPROVED/ACTIVE, capacity, next episode | EXPLICIT_SLOT_SERIES; block if required run invalid |
| no series key | UNDEFINED or strict BLOCK | STANDALONE | STANDALONE_REQUIRED/OPEN_MIX | none | NICHE_OPPORTUNITY; niche/market must pass |
| invalid/superseded key | BLOCK/PARTIAL | none | SERIES_REQUIRED | active exact run | SERIES_BINDING_INVALID |
| niche-fit but wrong series | REVIEW/BLOCK depending NICH1 | STANDALONE or block | PREFERRED/REQUIRED | coherent promise | SERIES_COHERENCE_FAILED |
| several active series | UNDEFINED | deterministic winner | OPEN_MIX/PREFERRED | eligible runs | obligation score then stable run ID tie-break |
| no series capacity, valuable topic | UNDEFINED | STANDALONE | OPEN_MIX/PREFERRED | none | SERIES_CAPACITY_EXHAUSTED + NICHE_OPPORTUNITY |
| mandatory next episode | MISSING | SERIES_EPISODE | SERIES_REQUIRED/PREFERRED | active next episode | MANDATORY_NEXT_EPISODE |
| timely topic competes | MISSING | slot policy winner | OPEN_MIX | valid run if selected | TIMELINESS_SCORE / RUN_OBLIGATION_SCORE |
| eligible approved parent Short | PARTIAL | STANDALONE child with derivative lane | OPEN_MIX/derivative eligibility | parent approved, timeline frozen | LONG_DERIVATIVE_AVAILABLE |

OPEN_MIX must use a versioned deterministic score and stable tie-breaker, never
generic human review. A normal ambiguity either resolves or becomes an
operator-visible planning BLOCK before project creation.

## 7. Migration and compatibility plan

Create one linear `0043` from `0042_mr1_final_lineage`.

1. Add nullable-for-history typed lane/assignment projections to
   EditorialCalendarSlot, DailyIdeaDecision, ProjectAdmissionDecision,
   VideoProject, package/publish/final/upload/analytics rows.
2. Make ProjectAdmissionDecision the immutable assignment receipt with
   `resolver_version`, `input_hash`, `decision_hash`, typed series/standalone
   fields and policy snapshot.
3. Add versioned SeriesPlan and SeriesRun tables and state checks. Reserve with
   `SELECT ... FOR UPDATE`; enforce
   `UNIQUE(series_run_id, episode_number)` on v2 projects.
4. Add FinalVideoDecision with operator FK, FinalMediaRef FK/hash, package hash,
   destination binding, timestamp and reason; unique active decision per exact
   candidate/hash.
5. Extend HumanUploadTask and UploadedVideo with required v2 final-media,
   decision, lane/mode/series/parent lineage.
6. Add analytics authority/window uniqueness and incident exclusion fields.
7. Extend OpsIncident; add immutable memory confidence proposal/version rows.
8. Add checks that all **new v2 writes** have complete typed lineage. Do not make
   historical null rows pass as standalone.

Compatibility rules:

- v1 null series remains `UNRESOLVED_LEGACY`, never STANDALONE;
- non-empty v1 series text is read as `LEGACY_SERIES_BOUND`;
- v1 ArtifactVersion content and hashes are never rewritten;
- v2 canonical serialization includes lane, assignment, content mode, series or
  standalone reason, duration contract, destination and parent lineage;
- readers explicitly support v1/v2;
- rollback removes only unused v2 schema. Once an authoritative v2 row exists,
  downgrade must fail closed with a precondition error.

Artifacts requiring v2 canonicalization on new writes:

```text
Editorial assignment / ProjectAdmissionDecision
VideoProject frozen lineage
D2P1/package readiness receipt
ProductionPackage
provider execution plan and budget authorization
CanonicalMediaTimeline envelope
NativeRenderPlan
ReviewMediaCandidate
Drive archive/finalization receipts
FinalMediaRef lineage receipt
FinalVideoDecision
PublishHandoffPackage / HumanUploadTask
ManualPublishConfirmation / UploadedVideo
AnalyticsSnapshot window/cohort
diagnostic and learning evidence manifests
```

Existing MR1 v1 hashes must not be recomputed.

## 8. Final implementation roadmap

### Phase 1 — Runtime identity and safety boundary (before MR1)

**Goal:** authenticate every mutation and make actor identity non-spoofable.  
**Reuse:** AuthService, RBAC, audit/domain events.  
**Exact changes:** global authenticated mutation dependency; permission map;
derive actor from session; lock down provider, planning, review, publish,
analytics, learning and ops commands; keep read-only health endpoints explicit.  
**Migration:** none except optional session/audit indexes.  
**API/UI:** login-expiry and forbidden handling.  
**Tests:** anonymous mutation rejected; role matrix; actor payload ignored.  
**Exit:** no production state/cost/review decision can be changed anonymously.  
**Dependency:** none.  
**MR1 impact:** current approval unchanged semantically, but checkpoint must be
reconciled before use.

### Phase 2 — Typed lane, assignment and series authority (before MR1)

**Goal:** deterministic DAILY_SHORT/LONG_FORM/LONG_DERIVED_SHORT and
SERIES_EPISODE/STANDALONE.  
**Reuse:** ProjectAdmissionService, EffectiveContext/NICH1, slot, admission
receipt.  
**Exact changes:** 0043 core fields/tables; SeriesPlan/Run state service;
atomic episode reservation; resolver with versioned score/reasons; new
long-form planning entry; Daily forced to DAILY_SHORT.  
**Migration:** 0043 sections 1–3.  
**API/UI:** typed slot/assignment read models; no human assignment action.  
**Tests:** four primary lane/mode cases, concurrent episode reservation,
invalid/superseded run, niche invariants.  
**Exit:** every new project has complete v2 assignment hash.  
**Dependency:** Phase 1.  
**MR1 impact:** current legacy series package cannot be relabeled; it requires a
new admission/package lineage.

### Phase 3 — Canonical package, duration and automated readiness (before MR1)

**Goal:** one production truth and no blanket pre-render review.  
**Reuse:** D2P1 artifact receipts, GateRun, CanonicalMediaTimeline, provider plan,
budget authority.  
**Exact changes:** ProductionPackage v2; freeze exact channel duration; delete
450s and exact 6–12 fallbacks; remove filler expansion; convert evidence/package
reviews to gates or exception incidents; deprecate PKG1 as new-write authority.  
**Migration:** package/version markers in 0043; artifacts remain immutable.  
**API/UI:** readiness/blocker read model, no normal approve buttons.  
**Tests:** channel-specific min/target/max, no-padding, rights exception,
automated readiness idempotency.  
**Exit:** package reaches READY_FOR_PRODUCTION using only deterministic evidence.  
**Dependency:** Phase 2.  
**MR1 impact:** package hash and approval must be rebuilt.

### Phase 4 — Durable orchestration and provider crash safety (before MR1)

**Goal:** one user action schedules/resumes the appropriate lane.  
**Reuse:** DomainEvent outbox, ChannelDailyRun, D2P1/LPRO1/MR1 receipts,
RetryPolicy, DeadLetterJob, OpsIncident, MR1 attempt/budget/Drive journals.  
**Exact changes:** event dispatcher/worker; handler idempotency receipts;
claim/lease/stuck reconciliation/cancel; policy retry; eliminate human provider
continuation; unique provider command/attempt identities.  
**Migration:** outbox claim/attempt fields and uniqueness in 0043.  
**API/UI:** Start/Resume/Cancel production; blocker view.  
**Tests:** crash at every paid boundary; replay without second charge; concurrent
start; dead-letter/reconciliation.  
**Exit:** no normal preflight/admission/D2P1/LPRO1 CLI or separate API sequence.  
**Dependency:** Phase 3.  
**MR1 impact:** reusable provider mechanisms, new package command IDs.

### Phase 5 — Final decision and canonical manual publish (before MR1)

**Goal:** only normal human decision is UPLOAD/DO_NOT_UPLOAD.  
**Reuse:** MR1 ReviewMediaCandidate, FinalMediaRef, Drive receipt, M7 strict
destination/confirmation.  
**Exact changes:** FinalVideoDecision; create task only on UPLOAD; disable M12.2r
package bypass; bind task/confirmation/UploadedVideo to reviewed checksum;
replace REVIEW_REQUIRED dead-end with typed resolutions; DO_NOT_UPLOAD terminal.  
**Migration:** 0043 sections 4–5.  
**API/UI:** final player, thumbnail/captions/metadata/destination/warnings and two
buttons; manual confirmation/verification.  
**Tests:** UPLOAD, DO_NOT_UPLOAD, wrong destination, wrong file attestation,
metadata mismatch resolution, duplicate task.  
**Exit:** no upload task exists without exact final decision and verified archive.  
**Dependency:** Phase 4.  
**MR1 impact:** old PASS receipt is not sufficient; new final-decision lineage.

### Phase 6 — Operator cockpit and qualification/code freeze (before MR1)

**Goal:** normal operation without CLI/DB and evidence for freeze.  
**Reuse:** M11/R3D9 read models and frontend, runtime readiness tests.  
**Exact changes:** Next Video card; lane/mode/series/reason/state/blocker; final
review; Advanced Details; remove stale skipped qualification claims.  
**Migration:** none beyond 0043.  
**API/UI:** full preproduction/final/publish surfaces.  
**Tests:** all 12 required scenarios; one real NativeFFmpeg MP4 at the exact
channel-configured long-form duration; no fake-byte or metadata-only proof.  
**Exit:** P0/P1 pre-MR1 gaps closed, one head, rollback checks, clean qualification.  
**Dependency:** Phases 1–5.  
**MR1 impact:** code-freeze point; create a new v2 package and approval afterward.

### Phase 7 — Windowed analytics and incident exclusion (safe after first upload)

**Goal:** close UploadedVideo → mature diagnostics automatically.  
**Reuse:** M8/M10.3 clients, ObservationWindow, M9, DomainEvent worker,
OpsIncident.  
**Exact changes:** exact 24h/72h/7d/30d scheduling; unique snapshots; requested
window lookup; resumable insufficient data; lane/content cohorts; incident
exclusion.  
**Migration:** 0043 sections 6–7 or a later linear 0044 if code freeze policy
requires.  
**API/UI:** freshness/window/incidents.  
**Tests:** scheduled 24h chain, duplicate sync, unavailable/private/zero/null,
incident exclusion.  
**Exit:** no CLI for sync/diagnostics and no arbitrary latest snapshot.  
**Dependency:** verified UploadedVideo from Phase 5.  
**MR1 impact:** none on render/package.

### Phase 8 — Governed learning, series health, geo and economics (safe after mature evidence)

**Goal:** proposals only; no silent runtime mutation.  
**Reuse:** M10/M11 review, geo maturity diagnostic, SelfFundingGate, memory
approval/version foundations.  
**Exact changes:** stop direct memory confidence mutation; comparable-cohort
eligibility; typed series/pillar/standalone/memory proposals; PnL snapshots from
finalized platform revenue; authenticated approval creates immutable versions.  
**Migration:** proposal/version/PnL projections.  
**API/UI:** mature proposal queue, series health, geo and revenue provenance.  
**Tests:** one video/immature Short cannot propose strategy; cohort isolation;
proposal-only invariants; finalized revenue only.  
**Exit:** no analytics path can mutate active runtime truth.  
**Dependency:** Phase 7 and mature data.  
**MR1 impact:** none.

## 9. Code-freeze and MR1 decision

```text
MR1_DECISION=BLOCK_MR1_UNTIL_CLOSEOUT
```

Why:

- the supplied approval ID is absent from the configured runtime DB;
- the current package is at best `LEGACY_SERIES_BOUND`;
- typed content mode, SeriesRun/episode and duration lineage change
  ProjectAdmissionDecision and the canonical package hash;
- removing pre-render human approval changes readiness evidence;
- the final-decision receipt and upload lineage do not yet exist;
- the current provider ledger mechanisms can be reused, but their authorization
  must point to the new package/approval hash.

Impact by semantic:

| Semantic | Current MR1 reusable? |
|---|---|
| ProjectAdmissionDecision | no; v2 assignment receipt required |
| package hash | no; lane/mode/duration/series lineage changes |
| duration contract | no; current exact 6–12 gate conflicts |
| content mode | absent; must be added |
| series binding | legacy text only; cannot be relabeled in place |
| review boundary | package approval becomes automated readiness; final decision changes |
| provider ledger | mechanisms reusable; existing command bindings cannot cross package hash |
| FinalMediaRef lineage | schema/mechanism reusable; new v2 lineage required |

After Phases 1–6, build a **new** package and approval. Do not supersede only the
approval over the old package.

## 10. Explicit answers

### 1. What important issue was still missing from all previous audits?

The most consequential newly verified issue is the silent learning mutation:
R3D7 directly changes `MemoryFacet.confidence_label`, and R3D6 immediately uses
that value to rank future context. Thus analytics can change runtime behavior
without an approved new policy/memory version. The equally urgent operational
issue is that production-changing APIs are not consistently authenticated, so
identity-bound review is not currently enforceable.

### 2. What is the minimum architecture closeout required before MR1?

Phases 1–6: authenticated mutation/RBAC; typed v2 lane/assignment/series and
atomic episodes; channel-scoped duration with no padding; one canonical package
and automated readiness; durable event-worker orchestration; final
UPLOAD/DO_NOT_UPLOAD and canonical manual publish lineage; then the 12-scenario
qualification including a real channel-duration MP4. Reconcile the runtime DB,
then rebuild the MR1 package and approval.

### 3. What can safely be deferred until after the first uploaded video?

The full automated 24h/72h/7d/30d analytics scheduler, persisted geo/economic
projections, mature series health and richer learning dashboard can follow the
first verified UploadedVideo. Before MR1, however, direct memory mutation must
already be disabled and incident/cohort fields must be present so the first
video cannot contaminate learning.

### 4. After the final plan is implemented, can VCOS operate normally without developer/DB/CLI intervention?

Yes. Phases 1–6 remove CLI/DB/manual API chaining for production; Phases 7–8
remove it for analytics and governed learning. The remaining human actions are
the final UPLOAD/DO_NOT_UPLOAD decision, the physical manual YouTube upload, and
approval of material strategy/memory proposals.

### 5. Is any current subsystem still capable of silently changing runtime truth?

Yes: R3D7 mutates `MemoryFacet.confidence_label`, which changes R3D6 retrieval
ranking. Also, unauthenticated/body-supplied actor IDs allow callers to mutate
decisions without trustworthy identity. No direct analytics mutation of active
profile/policy was found.

## 11. Final concise operator plan

```text
PHASE 1  Authenticate and authorize every mutation; reconcile MR1 DB authority.
PHASE 2  Add v2 lanes, deterministic assignment, SeriesPlan/Run and atomic episodes.
PHASE 3  Canonicalize package/duration; remove padding and normal pre-render review.
PHASE 4  Connect existing stage machines through DomainEvent worker/retry/dead-letter.
PHASE 5  Add identity-bound UPLOAD/DO_NOT_UPLOAD and one strict manual publish lane.
PHASE 6  Complete dashboard and 12-scenario qualification, including real-duration MP4.
CODE FREEZE
MR1       Rebuild v2 package and approval; execute only after checkpoint verification.
UPLOAD    Human reviews final binary, chooses UPLOAD, uploads manually, VCOS verifies.
LEARNING  Automate exact maturity windows; mature cohort creates proposals only.
```
