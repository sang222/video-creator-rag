# VCOS Phase 4–6 implementation closeout

Date: 2026-07-29 (Asia/Ho_Chi_Minh)

## 1. Repository state before and after

| Field | Before Phase 4–6 | Final |
|---|---|---|
| Repository | `/Users/sangss/Desktop/video-creator-rag` | unchanged |
| Branch | `main` | `main` |
| Tracked HEAD | `13841cff8e1f4da29be67e632a006b0cd169bdb6` | unchanged |
| Prompt's expected HEAD | `4d6eb9a3e272de33fe6aeb5a5831d998e2ac9a64` | not used; tracked source was authoritative |
| Worktree | intentionally dirty Phase 1–3 checkpoint; preserved | intentionally dirty implementation worktree; 95 changed paths including this report |
| Prior report | Phase 1/2/3 all `PASS` | preserved and regression-qualified |
| Migration checkpoint | `0043_vcos_phase123` | `0046_vcos_v2_effect_ledger` |
| Development DB | an intermediate final-gate read found `0045_vcos_final_publish` | upgraded and verified at `0046_vcos_v2_effect_ledger` |
| Commit/tag/MR1 | none executed | none executed |

The existing Phase 1–3 worktree was not reset, stashed, rewritten, or
reclassified. No historical v1 artifact/hash or MR1 approval/package authority
was recreated.

## 2. Previous and new Alembic heads

The linear sequence is:

```text
0043_vcos_phase123
→ 0044_vcos_orchestration
→ 0045_vcos_final_publish
→ 0046_vcos_v2_effect_ledger
```

`alembic heads` returns exactly:

```text
0046_vcos_v2_effect_ledger (head)
```

`0044` adds the durable workflow/outbox projection and incident lineage.
`0045` adds immutable final-review, final-decision, canonical manual-publish,
and UploadedVideo v2 lineage. `0046` adds the crash-safe post-readiness effect
ledger and the guarded local-renderer/local-archive capability widening.

Offline SQL generation from `0043` through `0046` passed. The configured
development DB upgraded from `0045` to `0046`. Nine migration tests passed,
including empty-DB upgrade, no-authority downgrade/re-upgrade, and fail-closed
downgrades after durable workflow, final-publish, effect-ledger, or local
archive authority exists.

## 3. Changed-file manifest

Root, configuration, migrations, and this report:

```text
.env.example
Dockerfile
Makefile
docker-compose.yml
config/artifact_type_registry.yaml
config/media_provider_routing_policy_catalog.yaml
config/role_catalog.yaml
alembic/versions/0044_vcos_orchestration.py
alembic/versions/0045_vcos_final_publish.py
alembic/versions/0046_vcos_v2_effect_ledger.py
reports/vcos_phase4_phase5_phase6_closeout_report.md
```

Backend APIs, contracts, models, services, and worker:

```text
app/api/routes/operator_cockpit.py
app/api/routes/operator_planning.py
app/api/routes/package_review.py
app/api/routes/production_publish.py
app/api/routes/production_workflow.py
app/api/routes/publishing_handoff.py
app/contracts/native_renderer.py
app/contracts/operator_cockpit.py
app/contracts/operator_planning.py
app/contracts/ops.py
app/contracts/production_package.py
app/contracts/production_publish.py
app/contracts/production_workflow.py
app/contracts/vcos_qualification.py
app/db/models/__init__.py
app/db/models/foundation.py
app/db/models/m10_1.py
app/db/models/m10_2.py
app/db/models/m10_5.py
app/db/models/m7.py
app/db/models/ops.py
app/db/models/production_publish.py
app/db/models/production_workflow.py
app/db/models/v2_effect.py
app/db/models/workflow.py
app/main.py
app/services/company_access.py
app/services/m10_2.py
app/services/m12_2r.py
app/services/m7.py
app/services/native_ffmpeg_renderer.py
app/services/operator_cockpit.py
app/services/operator_planning.py
app/services/outbox_dispatcher.py
app/services/production_package.py
app/services/production_publish.py
app/services/production_workflow.py
app/services/security_boundary.py
app/services/v2_native_effects.py
app/services/v2_package_readiness.py
app/services/v2_provider_production.py
app/services/v2_support_authority.py
app/services/vcos_qualification.py
app/services/workflow.py
app/workers/__init__.py
app/workers/__main__.py
app/workers/production_workflow.py
```

Frontend:

```text
frontend/src/app/drive-media/page.tsx
frontend/src/app/projects/[projectId]/production/page.tsx
frontend/src/app/projects/page.tsx
frontend/src/app/publishing/page.tsx
frontend/src/components/app-shell.tsx
frontend/src/components/friendly-status-badge.tsx
frontend/src/features/dashboard/command-center.tsx
frontend/src/features/production/__tests__/final-review-surface.test.tsx
frontend/src/features/production/__tests__/manual-publish-surface.test.tsx
frontend/src/features/production/__tests__/operator-planning-launcher.test.tsx
frontend/src/features/production/final-review-surface.tsx
frontend/src/features/production/manual-publish-surface.tsx
frontend/src/features/production/operator-planning-launcher.tsx
frontend/src/features/production/production-cockpit-card.tsx
frontend/src/features/production/production-progress.tsx
frontend/src/features/production/project-production-view.tsx
frontend/src/features/publishing/production-publishing-view.tsx
frontend/src/lib/api.ts
frontend/src/lib/types.ts
```

Tests:

```text
tests/conftest.py
tests/qualification/conftest.py
tests/qualification/test_m12_2r_publish_handoff_ledger.py
tests/test_migration.py
tests/test_phase3_production_package_v2.py
tests/test_phase4_durable_orchestration.py
tests/test_phase4_production_gateway.py
tests/test_phase4_support_compiler.py
tests/test_phase4_v2_native_effects.py
tests/test_phase4_v2_support_authority.py
tests/test_phase5_final_publish.py
tests/test_phase6_archive_qualification.py
tests/test_phase6_mandatory_scenarios.py
tests/test_phase6_native_qualification.py
tests/test_phase6_operator_cockpit.py
tests/test_phase6_operator_planning.py
tests/test_phase6_operator_security.py
tests/test_v2_local_narration_command.py
```

## 4. Phase 4 implementation

- Added a thin `ProductionWorkflowCoordinator`; it sequences existing planning,
  preflight, admission, support, package, readiness, production, QC, archive,
  and final-review authorities without replacing them.
- Reused `DomainEvent` as the transactional outbox. Claims use PostgreSQL row
  locks with `SKIP LOCKED`, bounded leases, heartbeat, execution deadlines,
  deterministic retry time, shutdown release, dead-letter settlement, and
  stuck-run incidents.
- Added deterministic command IDs, immutable command receipts, and the
  `V2ProductionEffectLedger`. Every MEDIA/RENDER/QC/ARCHIVE command moves from
  `PREPARED` to one physical invocation and then `VERIFIED` or
  `FAILED_UNCERTAIN`; a verified row is immutable.
- Added exact crash reconciliation for local narration, FFmpeg render,
  thumbnail generation, and archive copy. Replays read journals/receipts and
  do not invoke the physical effect twice.
- Added authenticated start/get/list/resume/cancel/dead-letter-retry APIs and a
  supervised `production-workflow-worker`. Only worker runtime code can mint
  the trusted system-worker actor.
- Added normalized failure classes, bounded retry/backoff, incidents, safe
  cancellation, and cancellation uncertainty records.
- Enforced lane-qualified handlers for `DAILY_SHORT`, `LONG_FORM`, and
  `LONG_DERIVED_SHORT`; Daily cannot resolve long-form handlers, and derived
  Short requires the exact parent/timeline authority.
- Added a frozen trusted-support envelope with project/profile/policy/admission,
  lane, producer receipt, input/output hashes, cited claims, script, visual,
  rights, destination, provider, and budget bindings. Missing/failed support
  fails closed; there is no legacy fallback.

```text
PHASE4_OUTBOX_DISPATCHER=PASS
PHASE4_TRUSTED_SYSTEM_WORKER=PASS
PHASE4_IDEMPOTENT_STAGE_HANDLERS=PASS
PHASE4_LEASE_HEARTBEAT=PASS
PHASE4_RETRY_BACKOFF=PASS
PHASE4_DEAD_LETTER=PASS
PHASE4_OPS_INCIDENT_WIRING=PASS
PHASE4_CANCEL_RESUME=PASS
PHASE4_NORMAL_API_CHAINING_REMOVED=PASS
PHASE4_DOUBLE_EFFECT_PREVENTION=PASS
PHASE4_FINAL=PASS
```

## 5. Phase 5 implementation

- Added immutable `FinalReviewCandidate` bound to the exact package/readiness,
  timeline, render plan, output checksum, technical/creative QC, verified
  archive, FinalMediaRef, destination, lane, assignment, and derivative
  lineage.
- Added immutable `FinalVideoDecision(UPLOAD|DO_NOT_UPLOAD)` with the
  authenticated session actor and exact candidate/media/package/destination
  identity.
- `UPLOAD` creates exactly one schema-v2 `HumanUploadTask`;
  `DO_NOT_UPLOAD` is terminal and creates none.
- Added exact file-selection attestation, manual confirmation, deterministic
  mismatch/correction/variance states, and server-owned verification evidence.
  The evidence hash is computed from the exact lineage and observed values;
  clients cannot supply or tamper with it.
- Added one canonical schema-v2 `UploadedVideo` only after verification,
  immutable publish supplement, exactly-once `UPLOADED_VIDEO_VERIFIED` and
  `ANALYTICS_READY` events, and idempotent series publication progress.
- M7 and M12.2r remain available for legacy v1 work but now fail closed before
  mutating any v2 task, confirmation, UploadedVideo, event, or provider path.
  Their public mutations use the session actor and persisted company RBAC.
- No YouTube upload API, browser automation, Studio scraping, or auto-publish
  path was added or exercised.

```text
PHASE5_FINAL_VIDEO_DECISION=PASS
PHASE5_IDENTITY_BOUND_UPLOAD_DECISION=PASS
PHASE5_DO_NOT_UPLOAD_TERMINAL=PASS
PHASE5_UPLOAD_TASK_AFTER_DECISION_ONLY=PASS
PHASE5_CANONICAL_MANUAL_PUBLISH=PASS
PHASE5_MISMATCH_RESOLUTION=PASS
PHASE5_UPLOADED_VIDEO_LINEAGE=PASS
PHASE5_SERIES_PROGRESS_AFTER_VERIFIED_UPLOAD=PASS
PHASE5_ANALYTICS_READY_EVENT=PASS
PHASE5_AUTO_PUBLISH_DISABLED=PASS
PHASE5_FINAL=PASS
```

## 6. Phase 6 implementation

- Added a company-scoped operator cockpit and progress read model covering the
  selected work, lane, assignment, stage, blockers, retry/lease health,
  provider/budget state, render, QC, archive, incident, cost, and deterministic
  next action.
- Added one Vietnamese “prepare and start” planning surface for Daily Short and
  Long-form. Normal flow uses one authenticated launch action; the server
  prepares authoritative planning input and starts the coordinator.
- Added final review with authenticated candidate-only MP4/thumbnail delivery,
  same-origin media URLs, player/download, metadata, warnings, rights,
  disclosures, repair summary, and only `UPLOAD`/`DO_NOT_UPLOAD` decisions.
- Added the manual publish surface with exact checksum/file/destination,
  confirmation, correction, verification, UploadedVideo, and analytics-ready
  state.
- Candidate media resolution verifies company/project/run/final-media/cloud
  lineage, archive state, hash, size, readback, root containment, and rejects
  symlinks/path traversal. Responses are private/no-store and `nosniff`.
- Added real local long/short H.264/AAC qualification and deterministic archive
  manifest/journal/readback qualification.
- Docker API and worker share `./var:/app/var`. The worker image now contains
  FFmpeg/ffprobe, `espeak-ng`, and DejaVu, so normal container operation has the
  same local production capabilities as the host qualification.

```text
PHASE6_OPERATOR_COCKPIT=PASS
PHASE6_NORMAL_OPERATION_WITHOUT_CLI_DB=PASS
PHASE6_REAL_LONG_FORM_MP4=PASS
PHASE6_REAL_SHORT_9_16_MP4=PASS
PHASE6_FINAL_MEDIA_LINEAGE=PASS
PHASE6_ARCHIVE_CONTRACT=PASS
PHASE6_12_SCENARIOS=PASS
PHASE6_SECURITY_RECHECK=PASS
PHASE6_ALEMBIC_SINGLE_HEAD=PASS
PHASE6_P0_OPEN=0
PHASE6_P1_PRE_MR1_OPEN=0
PHASE6_CODE_FREEZE=PASS
PHASE6_FINAL=PASS
```

## 7. Orchestration event/state matrix

| Stage | Running projection | Success authority | Next projection |
|---|---|---|---|
| PLANNING | `PLANNING_RUNNING` | authoritative planning source | `PLANNING_PENDING` |
| PREFLIGHT | `PLANNING_RUNNING` | strict preflight receipt | `PLANNING_PENDING` |
| ADMISSION | `PLANNING_RUNNING` | immutable v2 admission | `ASSIGNMENT_READY` / `RESEARCH_PENDING` |
| RESEARCH | `RESEARCH_RUNNING` | frozen trusted-support envelope | `PACKAGE_PENDING` |
| PACKAGE | `PACKAGE_RUNNING` | canonical ProductionPackage v2 | `PACKAGE_PENDING` |
| READINESS | `PACKAGE_RUNNING` | automated readiness receipt | `READY_FOR_PRODUCTION` / `MEDIA_PENDING` |
| MEDIA | `MEDIA_RUNNING` | timeline/narration receipt | `RENDER_PENDING` |
| RENDER | `RENDER_RUNNING` | NativeRenderPlan + output checksum | `QC_PENDING` |
| QC | `QC_RUNNING` | technical and creative QC receipts | `ARCHIVE_PENDING` |
| ARCHIVE | `ARCHIVE_RUNNING` | verified archive receipt + FinalMediaRef | `ARCHIVE_PENDING` |
| FINALIZE | `ARCHIVE_RUNNING` | immutable FinalReviewCandidate | `FINAL_REVIEW_READY` |

Every scheduled stage is one `PRODUCTION_WORKFLOW_STAGE_REQUESTED` outbox event
with workflow/company/channel scope, command ID, input hash, retry policy,
attempt/lease fields, and an immutable handler receipt. Exceptional projections
are `BLOCKED`, `RETRY_SCHEDULED`, `CANCELED`, `FAILED_TERMINAL`, or
`DEAD_LETTERED`.

## 8. Idempotency, retry, and crash-recovery matrix

| Effect/boundary | Stable identity | Replay behavior | Qualification |
|---|---|---|---|
| Workflow start | source + semantic hash + idempotency key | returns same run | duplicate start PASS |
| Stage delivery | run/stage command ID + payload/input hash | existing receipt settles event | restart/resume PASS |
| Provider/budget plan | package-bound operation/idempotency identity | no second submission/reservation/settlement | duplicate-effect tests PASS |
| Narration | command journal + approved-script/runtime/argv hash | reconstructs receipt from output; no second TTS call | one `say` invocation PASS |
| FFmpeg render | command ID + execution journal + advisory lock | validates existing MP4/QC; no second render | one FFmpeg invocation PASS |
| Thumbnail | archive command journal | verifies existing JPEG | one thumbnail invocation PASS |
| Archive copy | archive identity + checksum/readback journal | returns verified object; no second copy/upload | one copy/upload per item PASS |
| Retry | normalized class + unchanged identity + bounded policy | deterministic backoff | retry tests PASS |
| Lease expiry | owner/generation/deadline | safe reclaim + incident | concurrent reclaim PASS |
| Cancel after intent commit | re-lock run/event and compare pre-handler authority | stale handler cannot advance canceled projection | race regression PASS |
| Dead letter | bounded attempts + event identity | one DeadLetterJob + OpsIncident | exhaustion/retry PASS |

The physical-effect ledger restricts invocation count to `0..1`. MEDIA,
RENDER, QC, and ARCHIVE each finished `VERIFIED` with count `1` in the real
crash-recovery run.

## 9. Final-decision authority

| Invariant | Enforcement |
|---|---|
| Decision values | DB/contract allow only `UPLOAD` or `DO_NOT_UPLOAD` |
| Actor | derived from authenticated session; body spoof fields are forbidden/overwritten |
| Media identity | exact FinalReviewCandidate + FinalMediaRef + reviewed SHA-256 |
| Package identity | exact ProductionPackage artifact version and canonical hash |
| Destination | exact binding ID, fingerprint, channel, and account |
| Cardinality | one terminal decision per exact candidate/hash |
| Changed render/package | requires a new immutable candidate and decision |
| `UPLOAD` effect | exactly one schema-v2 HumanUploadTask |
| `DO_NOT_UPLOAD` effect | terminal; no task and no publish |

Generic `ApprovalDecision`, pre-render gate approvals, fake system decisions,
and legacy package/handoff-only task creation are not v2 decision authorities.

## 10. Canonical publish state machine

```text
FinalVideoDecision(UPLOAD)
→ HumanUploadTask READY_FOR_OPERATOR
→ IN_PROGRESS
→ file-selection attestation
→ ManualPublishConfirmation
   ├─ BLOCKED_DESTINATION
   ├─ REJECTED_MISMATCH
   ├─ CORRECTION_REQUIRED
   ├─ VARIANCE_ACCEPTED
   ├─ CANCELED
   └─ VERIFIED
→ HumanUploadTask VERIFIED
→ UploadedVideo(schema_version=v2)
→ UPLOADED_VIDEO_VERIFIED
→ ANALYTICS_READY
```

Wrong destination/file/video identity fails closed. Material metadata,
privacy, or disclosure variance requires correction. Accepted non-material
variance records the authenticated attestation. The platform is uploaded
manually; VCOS records an attainable file-selection attestation and observable
metadata, not a false platform-returned checksum.

## 11. UploadedVideo lineage matrix

| UploadedVideo field group | Required authority |
|---|---|
| Project/company/channel | candidate, task, confirmation, and destination must agree |
| Final media | FinalMediaRef ID, reviewed checksum, archive object |
| Human authority | FinalVideoDecision, HumanUploadTask, ManualPublishConfirmation |
| Package/policy | package version/hash, profile version, compiled policy |
| Destination/market | binding ID/fingerprint, platform channel/account, target-market lineage |
| Lane/assignment | lane, content mode, series plan/run/episode or standalone reason |
| Derivative | parent project and parent FinalMediaRef where applicable |
| Verification | server-owned evidence hash and authenticated verifier |
| Archive | immutable post-publish supplement/ref/hash |
| Events | deterministic verified and analytics-ready event IDs |

DB checks and service locks reject cross-project, cross-channel, cross-package,
cross-media, and cross-destination splicing. Series publication advances only
after the verified v2 UploadedVideo and is idempotent; standalone publication
does not touch SeriesRun.

## 12. Operator cockpit surfaces

| Surface | Operator-visible authority/action |
|---|---|
| Planning launcher | Daily Short or Long-form selection; one “prepare and start” action |
| Main production card | next video, lane/mode/assignment, why selected, destination, state, blocker, next action, costs |
| Progress | stage timestamps, retry/next retry, safe lease health, provider/budget, render, QC, archive, incident |
| Final review | verified MP4/thumbnail, captions/metadata, warnings, rights/disclosures, archive status |
| Final decision | only `UPLOAD` and `DO_NOT_UPLOAD` |
| Manual publish | exact file/checksum/destination, start, confirmation, correction, verification |
| Completion | verified UploadedVideo and analytics-ready status |
| Advanced details | IDs, hashes, profile/policy/admission, receipts, ledgers, events, incidents |

The main path does not expose package/gate approval buttons and does not require
CLI commands, direct DB writes, hidden service calls, or manually chaining stage
APIs.

## 13. Real MP4 qualification evidence

Primary real long-form crash-recovery run:

```text
path=/private/tmp/vcos_phase456_media_evidence_019fae90/test_v2_native_real_say_h264_a0/v2-production/runs/v2-22293e30-248a-5bd7-a97f-cf46d0eba437/v2-native-production.mp4
sha256=04d513b302496e15c29274e81d84b40927b0e9f43e165d2eba0bff278a598400
size_bytes=278631
duration_seconds=12.181
video=h264 1920x1080
audio=aac 48000Hz stereo
duration_contract_ms=min:6000 target:12000 max:15000
tts=/usr/bin/say (approved-script-bound)
paid_provider_calls=0
```

The timeline duration, narration receipt, render measurement, FinalMediaRef,
archive readback, and output checksum agree within the frozen contract/QC
tolerance. The render was recovered after an injected post-effect crash without
a second TTS or FFmpeg invocation.

Real Daily Short qualification:

```text
path=/private/tmp/vcos_phase456_media_evidence_019fae90/test_real_daily_short_uses_fro0/persisted-short/runs/phase6-short-final-43d593ee/nr1_smoke.mp4
sha256=f0737f3af635d986776fe0c6d8075d33288a8d30c7e330240176f45b277f5342
size_bytes=64063
duration_seconds=2.000
video=h264 1080x1920
audio=aac 48000Hz stereo
duration_contract_ms=min:1500 target:2000 max:3500
paid_provider_calls=0
```

The Short persisted NativeRenderPlan, QC, archive receipt, and FinalMediaRef.
The separate frozen-channel long qualification also produced a 2.000s
1920x1080 H.264/AAC MP4 from its exact 1500/2000/3500ms channel contract.

Final frozen-tree Docker image:

```text
image=vcos-phase456-qualification:local
image_id=sha256:a27274e03b4857da73ff4736691e83ea0a07d1a43802bc6c9e9b4a2c57193d35
platform=linux/arm64
size_bytes=260404431
ffmpeg_ffprobe=7.1.5
tts=ESPEAK_NG /usr/bin/espeak-ng voice=en-us rate=150
container_tts_sha256=25263a18a700156159003a6a622542d5766d302a89a6e4368510097c58838e40
container_vertical_mp4_sha256=ef4186932649f41c79a4460763dd5607308f9257facdf42c3ff3a2dd13400fe8
container_vertical_mp4=h264 1080x1920 + aac 48000Hz stereo, 1.000s, 28276 bytes
app_import=VCOS 0.1.0, 416 routes
```

Host/image hashes matched for the final patched publish boundary and production
effect sources. The image includes drawtext, DejaVuSans, FFmpeg, ffprobe, and
`espeak-ng`.

## 14. Archive qualification evidence

The real production adapter copied the long MP4 to:

```text
/private/tmp/vcos_phase456_media_evidence_019fae90/test_v2_native_real_say_h264_a0/v2-production/archive/a322d689-0a20-4ee8-acc8-ef6e3377a6c6/04d513b302496e15c29274e81d84b40927b0e9f43e165d2eba0bff278a598400.mp4
```

Readback SHA-256 matched the render. The associated JPEG SHA-256 was
`0d7e984bbbc849ded78d40b20ac2e1a8ed22cc871877621672aac4b338e02417`.
Archive copy and thumbnail physical invocation counts were each exactly one
after injected crashes/replay.

The deterministic Drive client exercised the real archive
manifest/journal/exclusive-replay/readback contract without live Drive:

| Artifact | Long | Daily Short |
|---|---|---|
| Archive state | `VERIFIED` | `VERIFIED` |
| Item count | 5/5 exact remote set | 5/5 exact remote set |
| Review-media SHA-256 | `cde251312a0dc4fc790cba3881e2416763e3e29fce239c52f8e5fcf8a8f8db82` | `f0737f3af635d986776fe0c6d8075d33288a8d30c7e330240176f45b277f5342` |
| Manifest hash | `a9815bb7b07b3b75ca33983604eb65ccfe757f825bb868cb5d6ff0091254c52e` | `16243809fdc2373610da27f0209d1d42c0a965f1c97e56f4364e02d2703a8f80` |
| Receipt hash | `b94ef1fe1c3b89253a5e3f5c6f8144fd27cd3c797fe9ee30a7b8e8b3f0dcbc5f` | `4a25db282f5e999a7ee54e493935e54287f9b32330dca18405b0b69478d44e9d` |
| Replay | same receipt; one upload per item | same receipt; one upload per item |

The journal contained no access token. This report does not claim a live Drive
execution.

## 15. Mandatory Q1–Q12 scenario results

| Scenario | Result | Authoritative coverage |
|---|---|---|
| Q1 Daily Short + standalone → final review | PASS | typed authority + mandatory scenario + real Short archive |
| Q2 Daily Short + series episode → final review | PASS | atomic typed series scenario |
| Q3 Long-form + standalone → configured-duration MP4 | PASS | real long frozen contract and production E2E |
| Q4 Long-form + series episode → final review | PASS | typed authority mandatory scenario |
| Q5 Long-derived Short → exact parent/timeline + 9:16 | PASS | real parent/child archive qualification |
| Q6 `UPLOAD` → task → verified UploadedVideo | PASS | canonical Phase 5/mandatory scenario |
| Q7 `DO_NOT_UPLOAD` → terminal/no task | PASS | canonical Phase 5/mandatory scenario |
| Q8 wrong destination/file → no UploadedVideo | PASS | mismatch and attestation tests |
| Q9 crash after effect → resume/no duplicate | PASS | provider/render/archive parameterization + real crash E2E |
| Q10 verified video → `ANALYTICS_READY` once | PASS | deterministic event identity tests |
| Q11 incident → `learning_excluded=true` | PASS | incident schema/event scenario |
| Q12 mature comparable input → proposal-only | PASS | Phase 1 boundary + R3D6/R3D7 suites |

## 16. Exact qualification tests and static gates

Non-overlapping backend inventory:

| Group | Result |
|---|---|
| Phase 1 security + Phase 2 admission + Phase 3 package + Phase 4 orchestration/support/native effects/narration | 110 passed |
| Phase 5 final decision/canonical publish/legacy-v2 boundaries | 30 passed |
| Phase 6 mandatory scenarios/cockpit/planning | 23 passed |
| Phase 6 candidate media/company security | 3 passed |
| Real NativeFFmpeg + archive qualification | 7 passed |
| Migration + NR1/LPRO + M12.2r legacy + R3D6/R3D7 | 70 passed |
| Final additive package-to-task cross-company regression | 1 passed |
| **Backend total** | **244 passed, 0 failed, 0 skipped** |

Frontend:

```text
TypeScript --noEmit: PASS, 0 diagnostics
ESLint --max-warnings=0: PASS
Vitest: 12/12 files, 41/41 tests, 0 failed/skipped
Next 15.5.20 build: PASS, 18/18 static pages
```

Combined test total is `285 passed, 0 failed, 0 skipped`.

Additional gates:

```text
python -m compileall -q app tests alembic/versions: PASS
Ruff check on all changed Python paths: PASS
Ruff format --check on all changed Python paths: PASS
git diff --check: PASS
docker compose config --quiet: PASS
Alembic heads/current/upgrade/offline SQL: PASS
guarded downgrade + immediate re-upgrade tests: PASS
unprotected unsafe mutation route inventory: []
```

`tests/qualification/test_m7_publish_handoff.py` remains an explicitly
legacy-only historical suite because its fixture depends on the removed
pre-M12 production-success path. It was not counted as a v2 pass. Its relevant
v2 boundary was replaced by authoritative Phase 5 tests, while the active
M12.2r legacy compatibility suite passed. The initial pnpm wrapper attempted an
offline dependency-status fetch and exited before running a gate; existing
bundled Node and installed CLIs then completed all four frontend gates without
installing or changing dependencies.

The only non-failing warnings were upstream Starlette TestClient and Vite CJS
API deprecations.

## 17. Repair cycles

Nineteen repair cycles were completed rather than deferred:

1. Persisted company-scope authorization across new workflow/cockpit/publish resources.
2. Authenticated candidate-only local video/thumbnail exposure.
3. Archive path traversal, symlink, size, and readback tamper rejection.
4. Frozen trusted-support authority with no legacy fallback.
5. Daily Short duration/target-boundary compatibility.
6. PostgreSQL self-deadlock caused by effect intent and parent FK transaction ordering.
7. Cancel-after-intent stale projection race.
8. Editorial depth failure from a one-claim support fixture.
9. Docker worker missing FFmpeg/TTS/font runtime capabilities.
10. Migration/ORM capability constraint and naming alignment.
11. Phase 5 multi-scope fixture reusing unique workflow hashes.
12. `VCOS_LOCAL_ARCHIVE` CloudMediaRef schema and guarded downgrade authority.
13. FINALIZE post-handler authority-drift false positive for legitimate stage outputs.
14. QC working-directory validation incorrectly using a file-only resolver.
15. Security fixture mutating an immutable FinalReviewCandidate after creation.
16. Legacy M12.2r backfill/verify/start crossing into v2 state.
17. Legacy M7 confirmation/accept crossing into v2 publication.
18. Client-supplied verification evidence hash.
19. Legacy package-to-upload-task route missing persisted cross-company enforcement.

Each cycle was classified, repaired at the smallest authority boundary, and
rerun with focused coverage. The final cross-company test initially exposed an
idempotent same-name company fixture; the fixture was corrected to use two
distinct persisted scopes, after which the authorization regression passed.

## 18. Remaining post-upload Phase 7/8 work

Phase 7 remains responsible for scheduling and operating post-upload analytics,
freshness, cohort, and incident-aware diagnostics from the emitted
`ANALYTICS_READY` authority. Phase 8 remains responsible for proposal review,
promotion governance, and controlled memory/profile/policy changes.

No Phase 7 scheduler or Phase 8 direct-mutation system was smuggled into this
closeout. Current R3D6/R3D7 behavior is proposal-only and does not mutate active
profile, policy, runtime memory confidence, or production truth.

The Starlette/httpx and Vite CJS deprecation warnings are P2 maintenance items,
not P0/P1 production-authority defects. There is no open P0 or P1_PRE_MR1 issue.

## 19. Final MR1 impact and status

The legacy MR1 package and approval are not reusable. A new MR1 execution must
be rebuilt after code freeze from a new v2 admission, canonical package,
readiness receipt, provider/budget authorization, final render/QC/archive, and
identity-bound final decision.

```text
CURRENT_MR1=LEGACY_SERIES_BOUND
CURRENT_MR1_PACKAGE_REUSABLE=false
CURRENT_MR1_APPROVAL_REUSABLE=false
MR1_EXECUTION_READY=false
PROCEED_TO_REBUILD_MR1=true
```

Final closeout block:

```text
PHASE4_FINAL=PASS
PHASE5_FINAL=PASS
PHASE6_FINAL=PASS

DURABLE_ORCHESTRATION=PASS
CRASH_SAFE_RESUME=PASS
DOUBLE_EFFECT_PREVENTION=PASS
FINAL_VIDEO_DECISION=PASS
UPLOAD_TASK_AFTER_UPLOAD_DECISION_ONLY=PASS
CANONICAL_MANUAL_PUBLISH=PASS
UPLOADED_VIDEO_LINEAGE=PASS
SERIES_PROGRESS_AFTER_VERIFIED_UPLOAD=PASS
REAL_LONG_FORM_MP4=PASS
REAL_SHORT_9_16_MP4=PASS
NORMAL_OPERATION_WITHOUT_CLI_DB=PASS
ALEMBIC_SINGLE_HEAD=PASS
CODE_FREEZE=PASS

TESTS=285 passed/0 failed/0 skipped (244 pytest + 41 Vitest)
REPAIR_CYCLES=19
FILES_CHANGED=95

P0_OPEN=0
P1_PRE_MR1_OPEN=0

MR1_EXECUTION_READY=false
PROCEED_TO_REBUILD_MR1=true
```
