# VCOS paid-provider authority audit and repair

Audit mode: source-grounded; no paid provider, upload, publication, project
creation, cadence bypass, or final-video decision was performed.

## Result

```text
PAID_PROVIDER_AUTHORITY_CLASSIFICATION=A_LEGACY_OR_DISPLAY_LABEL_ONLY
INTERMEDIATE_HUMAN_PAID_APPROVAL_REQUIRED=false
V2_REVIEW_TASK_CREATED=false
V2_APPROVAL_DECISION_REQUIRED=false
POLICY_BUDGET_AUTO_AUTHORIZATION=PASS
CADENCE_BYPASS_USED=false
PAID_PROVIDER_CALLS_DURING_AUDIT=0
FINAL_RULE_COMPLIANCE=PASS
READINESS_LABEL_REPAIR=PASS
```

The former readiness label was misleading. It was written by static M2
configuration validation, but it was not a V2 workflow transition and it
never created a review or approval. The active runtime now writes and displays
`READY_FOR_EXECUTION_AUTHORIZATION` instead.

## Checkpoint and runtime evidence

- Launch run `009178fc-2ee5-46f3-8fb2-38403e4693fa` is `ACTIVE` for channel
  `a77bc5dc-f7be-4ae0-8523-55fb846d64bd`.
- Current slot `698497f6-4c16-4687-b328-aa78440038af` is `OPEN`, with the
  genuine production window `2026-08-02T14:00:00Z` through
  `2026-08-03T14:00:00Z` and intended publish at `2026-08-04T14:00:00Z`.
- At the audit checkpoint, the latest durable cadence receipt was
  `59f3a87b-7eed-4b94-b939-f993b6deae64`, decision
  `WAIT_BUDGET_BLOCKED`. Its `2026-08-02T12:00:02Z` static snapshot predates
  the configured ElevenLabs voice/model and Drive archive values; it recorded
  missing configuration and made no provider call. It is preserved unchanged.
- Read-only verification of the rebuilt `/integrations/provider-wiring`
  endpoint returns ElevenLabs as `READY_FOR_EXECUTION_AUTHORIZATION`, Drive
  as `READY_FOR_FUTURE_EXECUTION`, and `no_network_calls_made=true`.
- Readiness snapshot `8dc021ba-877a-41b9-aedd-e21444897689` remains immutable:
  `PARTIAL`, zero blocking items, five expected warnings.
- No project admission, current-launch project, or production workflow exists.
  The durable worker was rebuilt and is running normally.

## Actual V2 call graph

```text
ProductionWorkflowWorker.run_once
  -> LongFormCadenceService.evaluate (only worker-owned cadence event)
  -> START_LONG_FORM_PRODUCTION only inside a real open slot
  -> ProjectAdmissionDecision + VideoProject
  -> ProductionWorkflowCoordinator.start_from_project_system
  -> V2PackageReadinessGateway
       -> canonical support artifacts
       -> immutable ProductionPackage
       -> ProductionReadinessReceipt PASS
  -> V2ProviderProductionGateway
       -> package provider plan + budget operation authorization
       -> local media/render/QC + verified Drive archive
       -> FinalReviewCandidate
  -> human may choose only UPLOAD | DO_NOT_UPLOAD
```

The cadence writer is `app/services/launch_cadence.py:746-939`; it only calls
admission/start after `_decision` selects an open slot. The natural worker
owns outbox evaluation in `app/workers/production_workflow.py:123-182` and
constructs the V2 pre/post-readiness gateways at lines 82-98.

The V2 package gateway creates package/readiness records without a manual API
step (`app/services/v2_package_readiness.py:265-407`). Its current operation
authority is deliberately zero-cost: MEDIA/RENDER/QC/ARCHIVE operations set
`execution_authorized=true`, `paid_provider_call=false`, and
`max_cost_usd=0` (`app/services/v2_package_readiness.py:957-1049`); the sealed
plan has `paid_provider_calls=false` and the matching budget authority is
automatic (`app/services/v2_package_readiness.py:1191-1261`). Current V2 uses
local OS TTS, native FFmpeg, automated QC, and the Drive archive adapter.

Before every effect, the post-readiness gateway validates the exact current
package plan, operation, and budget authorization. It rejects mismatched paid
capability or cost bindings fail-closed; it does not read `ReviewTask` or
`ApprovalDecision` (`app/services/v2_provider_production.py:516-665` and
`app/services/production_workflow.py:566-619,1106-1146`). Retries are bounded
by the immutable command/ledger authority; no V2 continuation branch creates a
human paid-approval request.

## Classification of the former label

| Former reference family | Writer / reader | V2 effect | Classification |
| --- | --- | --- | --- |
| `app/contracts/m2.py`, `app/services/m2.py` | M2 static environment validator writes readiness; M2 snapshot/read-only builders read it | No network call, no review record, no workflow transition | A: display/configuration label only |
| `app/services/production_start_readiness.py` | Reads M2 state for the cadence provider-readiness check | Deterministic provider-health prerequisite only | A: active reader with misleading label, repaired |
| `app/services/m12_2.py`, `app/services/r3d4.py` | Historical/rehearsal read projections | No V2 worker authorization | A: reader compatibility repaired |
| `app/services/r3d8.py` | Legacy cost-firewall provider-readiness reader | Legacy paid-render route; no V2 import/call path | A for this state reference; legacy human approval remains outside V2 |
| `tests/test_r3d8_production_cost_firewall_provider_boundary.py` | Assertion of the M2 state | Test-only | A, updated |

There are now no tracked `READY_FOR_HUMAN_PAID_APPROVAL` occurrences under
`app`, `tests`, or `frontend`. The remaining `WAIT_HUMAN_PAID_APPROVAL`,
`HumanPaidRenderApproval`, `ReviewTask`, and `ApprovalDecision` surfaces are
legacy R3D8/R3D9/general workflow records. Source tracing found no import or
call from `LongFormCadenceService`, `V2PackageReadinessGateway`,
`V2ProviderProductionGateway`, `ProductionWorkflowCoordinator`, or the
production worker to those legacy paid-render approval services. No historical
row or immutable artifact hash was rewritten.

## Repair performed

Repair cycle: 1.

1. Replaced the active readiness literal with
   `READY_FOR_EXECUTION_AUTHORIZATION` in the M2 contract, writer, snapshot
   readers, cadence provider readiness, R3D4/R3D8 compatibility readers, and
   tests.
2. Corrected `_item` ordering so `configured` is calculated after the final
   deterministic readiness state is chosen.
3. Added a Vietnamese dashboard label, “Sẵn sàng xác thực thực thi”, with a
   success status tone; it presents no human paid-approval action.
4. Added an actual V2 support-compiler assertion: the generated package plan
   and budget authority are machine-authorized, zero-cost, and create neither
   a `ReviewTask` nor an `ApprovalDecision` for the project/package/receipt.
5. Corrected the M2 test fixture to use the existing allowed Pexels role
   `brief_broll`; it did not alter any provider policy.

No schema migration was needed. The existing equivalent of a provider
execution authorization is the immutable `provider_execution_plan` artifact
plus the paired `cost_estimate_snapshot` operation authority, exact package
and readiness hashes, run context, and idempotent command/effect ledger.
This is a trusted machine policy/budget authority, not an approval record.

## Verification

- `python3 -m compileall -q app tests alembic/versions`: PASS.
- `ruff check` and `ruff format --check` for all changed Python files: PASS.
- `git diff --check`: PASS.
- Disposable Postgres test DB, provider-readiness and legacy-reader subset:
  `8 passed`.
- Disposable Postgres test DB, V2 package/budget authorization and support
  compiler flow: `2 passed`.
- Disposable Postgres test DB, immutable readiness, cadence ownership and
  idempotency, exactly-one workflow, and final upload boundary: `7 passed`
  (one pre-existing Pydantic deprecation warning only).
- `docker compose config --quiet`: PASS; rebuilt API, frontend, and durable
  worker are healthy. The frontend production build compiled and completed
  its TypeScript validity check.
- OpenAPI read check: PASS (`364` paths).
- The standalone frontend Vitest runner is not present in the slim production
  image; the dashboard assertion is covered by the successful production build
  rather than claiming an unrun Vitest result.

## Current durable state

```text
CURRENT_TIME=2026-08-02T12:47:41.642550Z / 2026-08-02 19:47:41.642550+07:00
CADENCE_WINDOW_STATE=BEFORE
LATEST_CADENCE_DECISION=WAIT_BUDGET_BLOCKED (receipt at 2026-08-02T12:00:02.108133Z)
VIDEO_PROJECT_CREATED=false
WORKFLOW_CREATED=false
NEXT_DURABLE_ACTION=WAIT_FOR_CADENCE
TESTS=17 passed, 1 Pydantic deprecation warning
REPAIR_CYCLES=1
FILES_CHANGED=12 audit files; prior unrelated worktree changes preserved
```

The next action is the worker's normal hourly cadence evaluation. It must not
be manually started or supplied a synthetic time. No provider smoke, paid
execution, upload, publish, or final decision was invoked during this work.
