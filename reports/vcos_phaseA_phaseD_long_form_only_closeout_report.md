# VCOS Phase A–D long-form-only closeout

Date: 2026-07-31 (Asia/Ho_Chi_Minh)

## Scope and checkpoint

The repository was continued from the existing Phase 4–6 checkpoint; its
intentionally dirty worktree was preserved. No reset, stash, history rewrite,
commit, tag, MR1 action, paid-provider call, Drive upload, or YouTube action
was performed by this closeout.

Tracked baseline: `2e153a6` on `main`.

## Completed authority changes

| Phase | Result |
|---|---|
| A — remove active Shorts authority | PASS: only `LONG_FORM` remains in the active planning/production lane; legacy Shorts identifiers are rejected and historical cleanup is guarded. |
| B — first-channel launch policy | PASS: immutable approval snapshot, launch-run state, private/manual-only handoff, and post-launch activation rules are durable. |
| C — long-form cadence | PASS: deterministic slot/window/receipt authority, duplicate prevention, launch-buffer checks, cadence warning/degraded states, and tenant authorization are covered. |
| D — full-duration evidence | PASS: existing qualification evidence records a 1920×1080 H.264/AAC MP4 at 539.173 seconds, within the frozen 360000–720000 ms duration contract, with verified archive and zero paid-provider calls. |

The durable migration chain is linear:

```text
0046_vcos_v2_effect_ledger
→ 0047_vcos_remove_shorts
→ 0048_vcos_first_channel_launch
→ 0049_vcos_long_form_cadence
```

`alembic heads` and the configured development database both report
`0049_vcos_long_form_cadence (head)`.

## Continuation repairs

This continuation fixed only incompatibilities exposed by the post-removal
qualification gates:

1. PostgreSQL check-constraint discovery in migration `0047` now handles the
   naming-convention physical name before replacing the slot-type constraint.
2. NICH1 validates series binding only for `SERIES_REQUIRED`; standalone/open
   long-form admission remains valid without a series key.
3. Strict long-form fixtures activate through the real format-contract and
   CH1 authority chain. Legacy Phase 2/3 trusted fixtures now compile and
   approve their scoped budget/provider authority instead of bypassing it.
4. The context-resolver compatibility facade re-exports the effective-runtime
   context compiler required by the Phase 4 suite.

## Verification evidence

| Gate | Result |
|---|---|
| `git diff --check` | PASS |
| `python3 -m compileall -q app tests alembic/versions` | PASS |
| Scoped Ruff over changed Phase A–D/fixture paths | PASS |
| Migration + cadence + tenant/publish-timing suite | 40 passed, 1 upstream deprecation warning |
| Phase 4–6 orchestration/production/publish/qualification suite | 92 passed, 1 upstream deprecation warning |
| Migration-only suite | 9 passed (included in the 40-test gate) |
| Frontend TypeScript + ESLint + Vitest | PASS; 44 Vitest tests across 13 files |
| Next.js 15.5.20 production build | PASS |
| Active-source Shorts absence scan | PASS; remaining matches are intentional migration cleanup assertions in `tests/test_migration.py` |

The full-duration provenance is retained in
`reports/vcos_phaseD_full_duration_evidence.json`; it records one native
FFmpeg render, one local archive copy, one local narration action, zero upload
tasks, and `automatic_publish=false`.

The only observed warnings were the existing Starlette TestClient deprecation.
Running Ruff across the entire pre-existing dirty worktree still reports
unrelated legacy findings; the scoped lint gate for this continuation passes.

## Final status

```text
PHASE_A_SHORTS_REMOVAL=PASS
PHASE_B_FIRST_LAUNCH_POLICY=PASS
PHASE_C_LONG_FORM_CADENCE=PASS
PHASE_D_FULL_DURATION_EVIDENCE=PASS
ALEMBIC_SINGLE_HEAD=PASS
LONG_FORM_ONLY_AUTHORITY=PASS
MANUAL_PUBLISH_ONLY=PASS
PAID_PROVIDER_CALLS_THIS_CLOSEOUT=0
COMMIT_CREATED=false
```
