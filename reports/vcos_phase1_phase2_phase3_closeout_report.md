# VCOS Phase 1 + Phase 2 + Phase 3 Closeout

## 1. Repository state before and after

| State | Branch | Tracked HEAD | Worktree |
|---|---|---|---|
| Before | `main` | `4d6eb9a3e272de33fe6aeb5a5831d998e2ac9a64` | Clean |
| After | `main` | `4d6eb9a3e272de33fe6aeb5a5831d998e2ac9a64` | 95 changed paths, including this report |

The tracked HEAD is intentionally unchanged. No commit, tag, merge request, MR1 execution, paid-provider call, upload, or real production-media execution was performed.

The pre-implementation targeted baseline was `55 passed, 12 skipped, 1 warning in 69.02s`. The final authoritative Phase 1–3 matrix is recorded in section 14.

## 2. Alembic state

| Item | Result |
|---|---|
| Previous head | `0042_mr1_final_lineage` |
| New head | `0043_vcos_phase123` |
| Head count | One |
| Development database current revision | `0043_vcos_phase123` |
| Upgrade | `0042_mr1_final_lineage -> 0043_vcos_phase123` passed |
| Guarded downgrade | `0043 -> 0042` passed with no authority rows, then immediately re-upgraded |
| Destructive downgrade precondition | Fails closed when canonical identity/authority rows exist |
| Offline SQL | Passed; 7,839 lines generated, including 0043 tables, partial indexes, projection FKs, checks, and head update |

The only historical migration edit is an offline-safe correction in `0036_hpr1_google_veo_replacement.py`; historical row semantics were not rewritten. Migration 0043 is linear from 0042 and contains the Phase 1–3 schema additions. ORM checks and partial indexes match the migration definitions.

## 3. Files changed

Final count: **95 paths**.

| Area | Count |
|---|---:|
| Alembic migrations | 2 |
| API routes | 12 |
| App bootstrap | 1 |
| CLI | 1 |
| Contracts | 13 |
| Actor core | 1 |
| Database models | 8 |
| Prompt policy deltas | 3 |
| Services | 26 |
| Config catalogs | 4 |
| Tests | 23 |
| Closeout report | 1 |

<details>
<summary>Complete changed-path manifest</summary>

```text
alembic/versions/0036_hpr1_google_veo_replacement.py
alembic/versions/0043_vcos_phase123_closeout.py
app/api/routes/artifact_policy_gates.py
app/api/routes/channel_workspace.py
app/api/routes/core_auth.py
app/api/routes/learning_memory.py
app/api/routes/package_review.py
app/api/routes/production_planning.py
app/api/routes/project_foundation.py
app/api/routes/provider_execution_safety.py
app/api/routes/publishing_handoff.py
app/api/routes/serializers_core.py
app/api/routes/serializers_publish_learning.py
app/cli/main.py
app/contracts/__init__.py
app/contracts/creative_quality_canary.py
app/contracts/long_production.py
app/contracts/m10_2.py
app/contracts/m11_1.py
app/contracts/m5.py
app/contracts/m7.py
app/contracts/mr1.py
app/contracts/native_renderer.py
app/contracts/production_package.py
app/contracts/temporal_authority.py
app/contracts/vcos_v2.py
app/contracts/workflow.py
app/core/actor.py
app/db/models/__init__.py
app/db/models/m10_2.py
app/db/models/m11_1.py
app/db/models/m5.py
app/db/models/m6.py
app/db/models/m7.py
app/db/models/vcos_v2.py
app/db/models/workflow.py
app/main.py
app/prompts/agents/system_deltas/script_planning_agent.md
app/prompts/agents/system_deltas/script_rewrite_agent.md
app/prompts/agents/system_deltas/script_writer_agent.md
app/services/__init__.py
app/services/creative_media_qc.py
app/services/d2p1.py
app/services/gates.py
app/services/long_production.py
app/services/m10_1.py
app/services/m10_2.py
app/services/m11.py
app/services/m11_1.py
app/services/m12_2.py
app/services/m12_2r.py
app/services/m5.py
app/services/m6.py
app/services/m7.py
app/services/mr1_real_production.py
app/services/native_render_plan.py
app/services/pkg1.py
app/services/production_package.py
app/services/r3d7.py
app/services/r3d8.py
app/services/r3d9_ux2.py
app/services/rbac.py
app/services/security_boundary.py
app/services/temporal_authority.py
app/services/vcos_v2.py
app/services/workflow.py
config/artifact_type_registry.yaml
config/gate_definition_catalog.yaml
config/niche_profile_templates.yaml
config/role_catalog.yaml
tests/conftest.py
tests/qualification/helpers/qualification_asserts.py
tests/qualification/test_m10_1_llm_router_derivatives.py
tests/qualification/test_m10_3_youtube_follow.py
tests/qualification/test_m10_5_google_drive_offload.py
tests/qualification/test_m10_learning_review_queue.py
tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py
tests/qualification/test_m7_publish_handoff.py
tests/qualification/test_m8_analytics_sync.py
tests/qualification/test_m9_post_publish_diagnostics.py
tests/test_d2p1_daily_to_package_bridge.py
tests/test_lpro1_long_production_orchestrator.py
tests/test_m2_workflow.py
tests/test_m4_ops_foundation.py
tests/test_m5_daily_run_context_admission.py
tests/test_migration.py
tests/test_phase1_review_actor_provenance.py
tests/test_phase1_security_identity_boundary.py
tests/test_phase2_m5_hardening.py
tests/test_phase2_typed_admission.py
tests/test_phase3_production_package_v2.py
tests/test_r3d7_closed_learning_retrieval_loop.py
tests/test_r3d9_ux2_packaging_review_queue.py
reports/vcos_phase1_phase2_phase3_closeout_report.md
```

</details>

## 4. Phase 1 implementation summary

- Added immutable `ActorContext` with canonical user, operator-user, role, permissions, and trusted `SYSTEM_WORKER` identity.
- Added a centralized authentication/RBAC middleware for all unsafe HTTP methods and the deliberately protected side-effect/read routes. Every protected route has a permission mapping.
- Canonically linked `OperatorUser` to `User`; public body identity is overwritten by the authenticated session, including nested decisions and bodyless decision routes.
- Bound target-market approval, localization final review, package-patch decisions, paid-render decisions, lifecycle decisions, and long-production starts to the session actor. Localization states that can satisfy readiness require `review.final_decide`.
- Added durable authorization and completion audit events with actor, role, permission, request ID, target, old/new authorization state, and timestamp. Failed-login audit survives the rejected login transaction.
- Made system-worker identity constructible only by trusted internal code; public `SYSTEM_WORKER` claims fail closed.
- Retired legacy public project/admission/D2P writers for new work; typed v2 admission is required.
- Stopped R3D7 from directly changing active `MemoryFacet.confidence_label`. Evidence now produces an immutable proposal/ledger state and cannot silently change R3D6 ranking.

```text
PHASE1_AUTHENTICATED_MUTATIONS=PASS
PHASE1_RBAC=PASS
PHASE1_ACTOR_SPOOFING_BLOCKED=PASS
PHASE1_SYSTEM_WORKER_IDENTITY=PASS
PHASE1_DIRECT_MEMORY_CONFIDENCE_MUTATION_DISABLED=PASS
PHASE1_AUDIT_IDENTITY=PASS
PHASE1_FINAL=PASS
```

## 5. Phase 2 implementation summary

- Added typed `ProductionLane`, `AssignmentMode`, `ContentMode`, `PlanningSourceType`, and v2 duration contracts.
- Added versioned `SeriesPlan` and capacity-bound `SeriesRun` authorities with guarded state machines.
- Added v2 fields/checks to editorial slots, daily ideas, preflight, admission receipts, and `VideoProject`.
- Added dedicated long-form planning that does not manufacture a daily run or daily idea.
- Made daily admission bind the exact frozen run slot, gated idea, title, category, profile, policy, and preflight. Cross-slot/content source splicing fails closed.
- Long-form preflight derives its result only from persisted, scoped niche/market evidence and the current exact slot-validation hash; caller-supplied PASS data is ignored.
- Deterministic assignment locks the source/run before hashing and reserves unique episode numbers atomically. Transaction failure releases the reservation.
- `LONG_DERIVED_SHORT` is always standalone, requires an admitted/ready long-form parent, exact canonical timeline, and current `FinalMediaRef` when one exists.
- Added explicit v1/v2 serializers and dual readers. Historical untyped rows remain classified as legacy; v2 writes cannot use a raw series key as authority.
- Added the deferrable bidirectional `ProjectAdmissionDecision <-> VideoProject` link and partial unique source/episode indexes.

```text
PHASE2_TYPED_LANES=PASS
PHASE2_TRUE_STANDALONE=PASS
PHASE2_SERIES_PLAN=PASS
PHASE2_SERIES_RUN=PASS
PHASE2_DETERMINISTIC_ASSIGNMENT=PASS
PHASE2_ATOMIC_EPISODE_RESERVATION=PASS
PHASE2_DAILY_SHORT_ONLY=PASS
PHASE2_LONG_FORM_ENTRY=PASS
PHASE2_V1_V2_COMPATIBILITY=PASS
PHASE2_FINAL=PASS
```

## 6. Phase 3 implementation summary

- Added immutable `production_package` ArtifactVersions as the only canonical v2 package authority. Exact support refs must be current, approved, same-project, type-correct, and hash-correct before any package version is persisted.
- Added deterministic timestamp-free canonical hashing and immutable material/non-material revisions. Invalid refs cannot poison the one-current-package slot.
- Added exact channel duration resolution from the approved `ChannelProfileVersion` and matching `CompiledChannelPolicySnapshot`; missing, stale, global-default, or mismatched authority fails closed.
- Added eleven deterministic production gate definitions/runs and immutable `production_readiness_receipt` authority. The receipt binds package/admission/profile/policy/duration hashes, exact gate runs, research/rights refs, provider plan, budget scope, and evaluator version.
- Removed filler expansion. Under-depth content blocks with `BLOCK_INSUFFICIENT_EDITORIAL_DEPTH`, or may produce an explicit shorter-format planning result only where policy permits.
- Removed blanket v2 pre-render human approval and fake system approvals. Unresolved rights, evidence, policy, or security exceptions block deterministically.
- Bound `LongFormRenderPackage`, `RenderPackageSnapshot`, `PublishHandoffPackage`, and `FinalMediaRef` projections to the exact canonical package version/hash/duration contract.
- LPRO resolves only the current ready v2 package and checks readiness before local render side effects.
- MR1 has a validation-only v2 admission branch. It validates current package/receipt, lane, profile, policy, duration, provider, and budget authority, then refuses execution with `MR1_V2_REAL_EXECUTION_DISABLED`.
- M12.2r rejects canonical v2 packages and legacy-shaped projections attached to a v2 project with `FINAL_MEDIA_DECISION_REQUIRED`.
- The derivative funnel rejects v2 upload-card/task construction before side effects. Metadata-only legacy cards no longer auto-create `HumanUploadTask`, and the task service independently rejects v2, non-ready, or fileless cards.
- Public callers cannot create canonical authority artifacts or self-attest v2 readiness support artifacts.

```text
PHASE3_CANONICAL_PRODUCTION_PACKAGE=PASS
PHASE3_CHANNEL_DURATION_AUTHORITY=PASS
PHASE3_GLOBAL_DURATION_HARDCODE_REMOVED_FOR_V2=PASS
PHASE3_PADDING_REMOVED=PASS
PHASE3_AUTOMATED_READINESS=PASS
PHASE3_PRE_RENDER_HUMAN_REVIEW_REMOVED_FOR_V2=PASS
PHASE3_EXCEPTION_FAIL_CLOSED=PASS
PHASE3_V2_UPLOAD_BYPASS_BLOCKED=PASS
PHASE3_V1_V2_PACKAGE_COMPATIBILITY=PASS
PHASE3_FINAL=PASS
```

## 7. Security permission matrix

The first matching centralized route rule is authoritative. `OWNER_ADMIN` has wildcard authority; `COMPANY_ADMIN` has the complete Phase 1 permission set. Roles not listed for a permission are denied.

| Permission | Principal roles | Protected surface |
|---|---|---|
| `channel.manage` | owner/admin, company admin, channel manager | company/channel/profile/policy management |
| `editorial.manage` | owner/admin, company admin, operator, channel manager, producer, procurement operator | planning, typed admission, artifacts, editorial operations |
| `production.start` | owner/admin, company admin, operator, channel manager, producer | production runs, package/readiness, render planning |
| `production.cancel` | owner/admin, company admin, channel manager | cancellation/abort/stop |
| `provider.execute` | owner/admin, company admin, operator, producer | provider, credentials, quota, paid-render and provider-boundary actions |
| `review.final_decide` | owner/admin, company admin, reviewer, compliance reviewer | approvals, final review, target market, final localization, package-patch decisions |
| `publish.prepare` | owner/admin, company admin, channel manager, publisher | publish handoff preparation and non-executing upload-card surfaces |
| `publish.confirm` | owner/admin, company admin, publisher | manual confirmation, paste-back, uploaded-video verification |
| `analytics.sync` | owner/admin, company admin, analyst | analytics synchronization/import |
| `learning.review` | owner/admin, company admin, learning reviewer | diagnostics, recovery, learning review |
| `memory.promote` | owner/admin, company admin, learning reviewer | governed memory proposals/promotions |
| `ops.manage` | owner/admin, company admin, operator, channel manager | health, incidents, retry/DLQ, manual operations |

Read-only observers have no mutation permission. `/auth/login` is the only public mutation; logout is authenticated-only. Credential-reference reads require `provider.execute`.

## 8. Lane and assignment matrix

| Production lane | Planning source | Allowed content mode | Admission authority |
|---|---|---|---|
| `DAILY_SHORT` | `DAILY_IDEA` | Series episode or standalone according to assignment mode | Exact v2 daily run, frozen slot, daily idea, strict preflight |
| `LONG_FORM` | `LONG_FORM_PLAN` | Series episode or standalone according to assignment mode | Dedicated v2 long-form slot and persisted strict preflight; no daily IDs |
| `LONG_DERIVED_SHORT` | `DERIVED_SHORT` | `STANDALONE` only | Exact admitted/ready long parent, timeline, inherited gates, and current parent media where available |

| Assignment mode | Deterministic behavior |
|---|---|
| `SERIES_REQUIRED` | Must select an eligible active run with capacity; otherwise BLOCK |
| `SERIES_PREFERRED` | Select eligible series deterministically; otherwise true standalone |
| `STANDALONE_REQUIRED` | Never carries plan/run/episode bindings |
| `OPEN_MIX` | Deterministically ranks eligible series versus standalone using frozen resolver inputs and stable tie-breaks |

Only `APPROVED` plans and `ACTIVE` runs are assignable. Reservation occurs under row lock and the receipt stores the exact resolver input/hash used for the committed episode.

## 9. SeriesPlan and SeriesRun state transitions

### SeriesPlan

```text
DRAFT -> APPROVED | ARCHIVED
APPROVED -> SUPERSEDED | ARCHIVED
SUPERSEDED -> ARCHIVED
ARCHIVED -> terminal
```

Approval requires evidence and the authenticated actor. A version that supersedes an approved prior plan atomically marks the prior version `SUPERSEDED`.

### SeriesRun

```text
PROPOSED -> APPROVED | CANCELED | ARCHIVED
APPROVED -> SCHEDULED | ACTIVE | CANCELED | ARCHIVED
SCHEDULED -> ACTIVE | PAUSED | CANCELED | ARCHIVED
ACTIVE -> PAUSED | COMPLETION_PENDING | CANCELED
PAUSED -> ACTIVE | CANCELED | ARCHIVED
COMPLETION_PENDING -> ACTIVE | COMPLETED | CANCELED
COMPLETED -> ARCHIVED
CANCELED -> ARCHIVED
ARCHIVED -> terminal
```

An approvable/schedulable/active run must remain bound to an approved plan. Capacity, next episode, reserved count, and unique `(series_run_id, episode_number)` authority are updated atomically.

## 10. Canonical package authority matrix

| Concept | v2 classification | Behavior |
|---|---|---|
| `ProductionPackage` ArtifactVersion | `CANONICAL_V2_AUTHORITY` | Immutable exact preproduction/production authority |
| `ProductionReadinessReceipt` ArtifactVersion | Readiness authority | Immutable exact PASS receipt for the package version |
| `FirstScriptedVideoPackage` | `LEGACY_ONLY` for v1 | Never v2 authority; cannot create a v2 upload task |
| PKG1 `package_manifest` | `LEGACY_ONLY` for v1 | Historical compatibility only |
| D2P1 receipt/package lineage | `LEGACY_ONLY` | v2 public writer is retired; cannot compete with package v2 |
| `LongFormRenderPackage` | `RENDER_PROJECTION` | Must bind exact canonical package/hash/duration for v2 |
| `RenderPackageSnapshot` | `RENDER_PROJECTION` | Must bind exact canonical package/hash/duration for v2 |
| `PublishHandoffPackage` | `PUBLISH_PROJECTION` | Must bind exact canonical package/hash/duration for v2 |
| `FinalMediaRef` | Final-media projection | May bind exact package/hash/duration after later-phase render/review |

Package creation validates all exact refs before persistence. A material revision changes the canonical hash and requires a new planning/package cycle. A policy-authorized non-material technical repair creates a new immutable version and re-evaluates all eleven readiness gates, which guarantees that every affected gate is rerun.

## 11. Duration lineage

```text
approved ChannelProfileVersion
  + matching CompiledChannelPolicySnapshot
  -> ProjectAdmissionDecision.duration_contract
  -> VideoProject.duration_contract
  -> ProductionPackage.duration_contract
  -> script/narration and CanonicalMediaTimeline contracts
  -> NativeRenderPlan / LongFormRenderPackage
  -> RenderPackageSnapshot / QC
  -> PublishHandoffPackage
  -> FinalMediaRef
```

The frozen fields are:

```text
minimum_duration_ms
target_duration_ms
maximum_duration_ms
duration_contract_version
duration_contract_hash
source_profile_version_id
source_policy_snapshot_id
```

The invariant is `0 < minimum <= target <= maximum`. The profile and policy values must match exactly. New v2 logic has no 360–720 second MR1 fallback, no 450-second M12.2 fallback, and no global duration substitution.

## 12. Removed human-review boundaries

| Boundary | v1 behavior | v2 behavior |
|---|---|---|
| Idea/admission | Legacy review may exist | Deterministic typed admission |
| Research/evidence | D2P evidence task may exist | Exact deterministic evidence gates |
| Individual gates | Review-oriented legacy flow | Persisted automated `GateRun` |
| Package readiness | Final-human package task | Immutable automated readiness receipt |
| Non-material technical repair | May require manual continuation | Policy-authorized repair + full deterministic readiness rerun |
| Provider continuation | Legacy review surface | Not a normal readiness prerequisite |
| Final rendered video | Human decision | Intentionally not implemented until Phase 5 |

No fake system `ApprovalDecision` is created. Human review remains mandatory only for true exception/final-decision boundaries, including rights/policy exceptions and the future `UPLOAD` / `DO_NOT_UPLOAD` decision.

## 13. v1/v2 compatibility behavior

- Existing v1 rows, hashes, serializers, PKG1 packages, and readers remain readable; historical rows are not silently rewritten.
- New projects cannot use the legacy public `POST /video-projects`, legacy public ProjectAdmission writer, or D2P shortcut. They enter through typed v2 admission.
- v2 read models expose all lane/source/assignment/series/duration fields and permit daily, long-form, and derived shapes.
- Legacy series data is explicitly classified as `UNRESOLVED_LEGACY` or `LEGACY_SERIES_BOUND`; only exact v2 plan/run IDs are v2 authority.
- Legacy projection columns are nullable. For v2, package version/hash/duration are all-or-none and database-constrained.
- M12.2r retains its historical v1 path but rejects canonical or legacy-shaped v2 project packages before upload-task creation.
- LPRO dual-reads historical package authority and exact v2 readiness. MR1 legacy flow is unchanged; v2 is validation-only and execution-disabled.

## 14. Targeted verification and exact results

### Final authoritative test matrix

```text
68 passed, 0 failed, 1 skipped, 1 warning in 64.29s
```

The sole skip is explicitly classified by the suite as a historical pre-M12 runtime mock contract:

```text
tests/test_m5_daily_run_context_admission.py
Historical pre-M12 runtime mock contract; authoritative M12.1R cutover coverage
lives in tests/test_m12_1r_mock_runtime_purge.py.
```

No Phase 1–3 v2 test is satisfied by that skip.

The final matrix covered:

- complete Phase 1 authentication, RBAC, actor, worker, audit, and provenance suites;
- complete Phase 2 typed admission/concurrency suite and strict M5 hardening suite;
- complete Phase 3 package/duration/readiness/revision/upload-boundary suite;
- bodyless package and paid-render decision provenance;
- M2 compatibility smoke;
- R3D7/R3D6 confidence behavior and source guards;
- D2P1 read-only/idempotent/provider-free/typed-v2 boundary;
- M5/NICH1 strict admission behavior;
- M12.2 no-padding behavior;
- PKG1 legacy provider-free compatibility;
- M12.2r upload bypass;
- derivative funnel upload boundary;
- all migration tests, including round-trip and destructive downgrade guard.

Additional verification:

| Check | Result |
|---|---|
| `python -m compileall -q app tests` | PASS |
| Ruff on every changed Python path | PASS |
| `git diff --check` | PASS |
| `alembic heads` | `0043_vcos_phase123 (head)` |
| `alembic current` | `0043_vcos_phase123 (head)` |
| `alembic upgrade head --sql` | PASS |
| Offline SQL contains 0043/SeriesPlan/partial episode index/projection bindings | PASS |
| Guarded development downgrade and immediate re-upgrade | PASS |
| Unsafe/protected route permission registry | PASS; uncovered routes `[]` |
| Final read-only P0/P1 invariant audit | PASS; no unresolved blocker |

Repair-cycle count is **16**. This counts distinct implementation/audit repair loops: preflight/schema identifiers; offline-safe historical migration; duration authority; typed model/migration; RBAC/audit; identity/failed-login durability; deterministic assignment and source locks; long-form persisted preflight/serializer scope; bidirectional admission links; package/revision/readiness; downstream projection/null compatibility; no-padding/legacy tests; LPRO/MR1/side-effect ordering; package ref reachability/poison prevention; actor/reviewer and derivative-upload bypass hardening; and final whole-worktree lint cleanup.

## 15. Remaining Phase 4 work

These are Phase 4 entry items, not Phase 1–3 blockers:

1. Wire admitted v2 projects through durable orchestration of the existing trusted domain producers that populate research, script, visual, rights, provider, budget, metadata, and destination artifacts.
2. Add durable worker scheduling, outbox/lease/heartbeat ownership, retry/backoff, cancellation, and crash-safe resume across those producers.
3. Persist orchestration checkpoints and exact command/idempotency keys so package assembly/readiness can resume without duplicating artifacts or reservations.
4. Operationalize trusted `SYSTEM_WORKER` identities for durable workers while retaining the public-forgery boundary.
5. Add Phase 4 end-to-end orchestration tests using fixtures only. Public self-attestation of support artifacts must remain forbidden.
6. Keep real rendering, final-video review, Drive verification, and `UPLOAD` / `DO_NOT_UPLOAD` for their declared later phases.

The absence of automatic support-producer scheduling is intentionally Phase 4 scope. Phase 3 package/readiness services and APIs are reachable when trusted domain outputs exist; opening an arbitrary public support-artifact writer would undermine exact readiness authority.

## 16. Current MR1 impact

MR1’s historical series-bound execution and approvals are not reusable for v2. The new branch validates exact automated readiness but deliberately refuses real execution.

```text
CURRENT_MR1=LEGACY_SERIES_BOUND
CURRENT_MR1_EXECUTION_ALLOWED=false
CURRENT_MR1_PACKAGE_REUSABLE=false
CURRENT_MR1_APPROVAL_REUSABLE=false

NEXT:
PHASE 4 DURABLE ORCHESTRATION
```

## Final closeout matrix

```text
PHASE1_FINAL=PASS
PHASE2_FINAL=PASS
PHASE3_FINAL=PASS

ALEMBIC_SINGLE_HEAD=PASS
V1_COMPATIBILITY=PASS
V2_TYPED_ASSIGNMENT=PASS
CHANNEL_DURATION_AUTHORITY=PASS
AUTOMATED_READINESS=PASS
PRE_RENDER_HUMAN_REVIEW_REMOVED_FOR_V2=PASS
DIRECT_MEMORY_CONFIDENCE_MUTATION_DISABLED=PASS
V2_UPLOAD_BYPASS_BLOCKED=PASS

TESTS=68 passed, 0 failed, 1 skipped
REPAIR_CYCLES=16
FILES_CHANGED=95

MR1_EXECUTION_READY=false
PROCEED_TO_PHASE4=true
```
