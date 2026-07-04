# Post-Freeze Protocol

Applies after Runtime LTS v1.

## Priority definitions

- P0: production safety/security/data-integrity break, provider/upload execution leak, policy bypass, snapshot/runtime truth corruption, public API break.
- P1: operator-blocking runtime defect, freeze invariant regression, dashboard/manual workflow blocked without workaround, provider stack drift blocking readiness truth.
- P2: workflow friction, confusing UI/status, missing convenience view, non-blocking reporting gap.
- P3: polish, naming cleanup, minor docs/UI copy, non-urgent refactor.

## Change rule

- No backend/core change unless P0/P1.
- P2/P3 must go to ProductionPainLog.
- No immediate refactor for P2/P3.
- No provider activation without explicit future phase.
- No dashboard job controls unless explicitly approved in a future phase.

## Patch window policy

- P0: patch immediately, smallest possible diff.
- P1: patch in scheduled window or same-day if operator-blocking.
- P2/P3: batch review every 2-4 weeks.

## Staging-first policy

- Reproduce in staging/local fixture first.
- Run `RuntimeLTSFreezeVerifier`.
- Run targeted regression before merge.
- Production migration/change requires rollback note.

## Regression gate before merge

Minimum:

- R3D1-R3D10 focused tests.
- M1/M2 tests.
- DX1/DX2 tests.
- M12.2/M12.2S qualification tests.
- migration tests.
- frontend typecheck/lint/tests if frontend touched.
- `python -m compileall -q app`.
- `git diff --check`.

## Forbidden shortcuts

- No provider execution.
- No upload/publish/reupload automation.
- No YouTube upload API.
- No browser/dashboard automation.
- No prompt self-mutation.
- No ChannelProfileVersion mutation by agent.
- No learning auto-promotion.
- No mock fallback/dry-run success as production.
