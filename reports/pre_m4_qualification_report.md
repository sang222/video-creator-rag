# Pre-M4 Qualification Report

## Verdict

PASS

`safe_to_start_M4: YES`

## Repo path

`/Users/sangss/Desktop/video-creator-rag`

## Git tag / commit tested

- Base tag: `m3-policy-gate-readiness`
- Base commit: `4c3be2137c1e551d885ba276152a6fb1d9e152d5`
- Working tree tested: plus Pre-M4 test/report/script changes, not committed.

## Changes made

- Added `tests/test_pre_m4_regression_gauntlet.py`.
- Added `scripts/pre_m4_qualification.py`.
- Added this report.
- No M4 feature built.
- No architecture change.
- No commit/tag created.

## Automated coverage added

P0 automated:

- Migration chain `0001 -> 0002 -> 0003 -> 0004`.
- Fresh DB upgrade to head.
- Alembic head idempotency.
- Downgrade `M3 -> M2`, then re-upgrade to head.
- Expected M0-M3 tables present.
- Forbidden M5-M11 table fragments absent.
- Config seed idempotency: `16` catalogs stable.
- Gate seed idempotency: `15` active gates stable.
- DB uniqueness / FK / rollback smoke.
- M2 exact project snapshot binding, artifact versioning, review, finding, revision, approval.
- Self-approval blocked.
- Old approval does not transfer to new version.
- ArtifactVersion immutability covered by existing + regression suite.
- GateRun exact target/hash/result contract.
- PASS / REVIEW_REQUIRED / BLOCK / SKIPPED / NOT_APPLICABLE matrix.
- Gate result malformed contract rejection.
- ReviewTask integration for `REVIEW_REQUIRED`.
- Revalidation appends new GateRun rows and old run remains unchanged.
- Policy catalog/version/source-ref lifecycle.
- Active policy version immutability.
- API -> CLI and CLI -> API smoke over same DB state.
- No network/provider sentinel during M0-M3 test path.
- Scope guard scans schema, app code, routes, service/import text.

Existing tests retained coverage for M0/M1/M2/M3 services, contracts, API, CLI, seeds, docs, audit/domain events.

## Commands run

Direct suite, first pass:

```bash
.venv/bin/pytest
```

Result: `84 passed, 1 warning in 9.36s`.

Direct suite, second pass:

```bash
.venv/bin/pytest
```

Result: `84 passed, 1 warning in 8.71s`.

Qualification runner on disposable DB:

```bash
VCOS_DATABASE_URL="postgresql+psycopg://vcos:vcos@localhost:55432/<disposable_db>" scripts/pre_m4_qualification.py
```

Result: PASS.

Runner steps passed:

- `.venv/bin/alembic upgrade head`
- `.venv/bin/alembic current` -> `0004_m3_policy_gate_readiness (head)`
- `.venv/bin/vcos config seed` -> `16 catalogs`
- `.venv/bin/vcos gate seed-definitions` -> `15`
- seed both again, counts stable
- `.venv/bin/pytest` -> `84 passed`
- `.venv/bin/pytest` again -> `84 passed`

Warning only: existing Starlette/httpx TestClient deprecation.

## Gate command plan

Canonical repo commands:

```bash
make db-up
.venv/bin/alembic upgrade head
.venv/bin/alembic current
.venv/bin/vcos config seed
.venv/bin/vcos gate seed-definitions
.venv/bin/vcos config seed
.venv/bin/vcos gate seed-definitions
.venv/bin/pytest
.venv/bin/pytest
```

For isolated qualification, use a disposable `VCOS_DATABASE_URL` and run:

```bash
scripts/pre_m4_qualification.py
```

## Double-test matrix status

- Pass A fresh empty DB + seed once + CLI/API: AUTOMATED/PASS.
- Pass B fresh empty DB + seed twice + CLI/API: AUTOMATED/PASS.
- Pass C existing migrated DB at head + API/CLI: AUTOMATED via idempotent head + in-suite DB reuse/PASS.
- Pass D existing migrated DB + reseed twice + API/CLI: AUTOMATED/PASS.
- Pass E same suite second run: AUTOMATED/PASS.
- Pass F restored fresh DB compare hidden state: AUTOMATED by fresh pytest DB per pass + runner disposable DB/PASS.

## Scope guard

PASS.

Confirmed absent in runtime code/schema/routes:

- ResourceResolver/RAG/vector/ContextPack/RetrievalPlan.
- Media/render/QC pipeline.
- Publish/upload/manual publish.
- Analytics/semantic layer.
- No-view/recovery/self-funding.
- Memory promotion workflow.
- Dashboard/operator cockpit.
- Source scraping/parser.
- OPA/Cedar/general policy engine.
- Algorithm/Growth/View agents.
- OpenAI/Anthropic/requests/httpx provider imports in app runtime.

## NOT_IMPLEMENTED_WITH_REASON

- P1 concurrent version creation: NOT_IMPLEMENTED_WITH_REASON - repo has no concurrency harness/locking abstraction yet; adding one would expand architecture.
- P1 concurrent approval/revision conflict: NOT_IMPLEMENTED_WITH_REASON - same reason; out of Pre-M4 P0 scope.
- P1 partial seed crash recovery: NOT_IMPLEMENTED_WITH_REASON - no seed checkpoint/interruption API; idempotent full rerun covered.
- P2 cases: NOT_IMPLEMENTED_WITH_REASON - user requested no P2 unless trivial.

## Mutation drills to prove tests fail

Do not leave code mutated. Apply one temporary break, run `.venv/bin/pytest`, revert.

- Disable same-channel snapshot validation in `VideoProjectService.create_project`.
- Allow creator self-approval in `ApprovalService.create_approval_decision`.
- Remove ArtifactVersion immutability trigger/migration guard.
- Remove `reason_codes` from GateRun result creation.
- Allow active `PlatformPolicyVersion.policy_blob` mutation.
- Allow active `GateDefinitionVersion.definition` mutation.
- Change revalidation to update old GateRun instead of inserting new row.
- Allow latest lookup in workflow project execution.
- Disable scope guard assertions.
- Disable no-network/provider sentinel.

## Final decision

Pre-M4 qualification gate passes.

`safe_to_start_M4: YES`
