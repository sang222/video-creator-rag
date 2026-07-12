# Runtime LTS v1 Baseline

Date: 2026-07-04

## Baseline Identity

- Project: Video Creator Operating System / VCOS
- Runtime state: Runtime LTS v1 frozen after R3D10
- Current git commit SHA: `5ffa56ac2fb204bb7b3d11388c2f0803cbfbb4f4`
- Expected tag name: `r3d10-runtime-lts-v1`
- Migration head: `0031_r3d8_cost_firewall (head)`
- Backend/core status: locked; post-freeze backend/core change allowed only for P0/P1
- Provider activation status: NO-GO
- Auto upload/publish status: NO-GO

## Freeze Verifier

- Verifier service: `app.services.r3d10.RuntimeLTSFreezeVerifier`
- Read-only verifier endpoint: `GET /ops/runtime-lts-freeze-check`
- Freeze verifier command:

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q
```

- Freeze verifier result: PASS
- Observed result: `8 passed, 1 warning in 8.72s`
- Warning: Starlette `httpx` TestClient deprecation warning only
- Required verifier flag: `no_provider_media_upload_execution=true`

## Test Commands To Reproduce Baseline

```bash
PYTHONPATH=. .venv/bin/alembic heads
PYTHONPATH=. .venv/bin/python -m compileall -q app
PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q
PYTHONPATH=. .venv/bin/pytest tests/test_dx1_semantic_code_convention.py tests/test_dx2_provider_stack_reconciliation.py -q
PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q
PYTHONPATH=. .venv/bin/pytest tests/test_migration.py -q
git diff --check
```

Frontend commands are not part of this baseline task because no frontend files are touched. If a future patch touches frontend files, also run:

```bash
cd frontend
npm run typecheck
npm run lint
npm run test
```

## Verification Run Results

| Command | Result |
| --- | --- |
| `PYTHONPATH=. .venv/bin/alembic heads` | `0031_r3d8_cost_firewall (head)` |
| `PYTHONPATH=. .venv/bin/python -m compileall -q app` | PASS |
| `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` | PASS, `8 passed, 1 warning` |
| `PYTHONPATH=. .venv/bin/pytest tests/test_dx1_semantic_code_convention.py tests/test_dx2_provider_stack_reconciliation.py -q` | PASS, `13 passed` |
| `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q` | PASS, `2 passed, 1 warning` |
| `PYTHONPATH=. .venv/bin/pytest tests/test_migration.py -q` | PASS, `2 passed` |
| `git diff --check` | PASS |

## Provider Stack Canonical Keys

- `elevenlabs`
- `google_veo`
- `pexels_api`

## Stale Provider Keys That Must Remain Inactive/Rejected

- `GOOGLE_VEO`
- `google_veo`
- `pexels_pixabay_free_fallback`
- `elevenlabs_flash_turbo`

## Forbidden Runtime Actions

- provider generation
- provider render submit
- Pexels search/download
- Drive upload
- YouTube upload/publish/reupload
- dashboard job-control buttons

## Accepted Runtime Mode

- dashboard/manual-ops/read-only
- production-ready package / publish handoff package
- manual publish handoff only
- provider/cost/readiness panel read-only
- ProductionPainLog for P2/P3

## Accepted Runtime Capabilities

- channel contract runtime authority
- effective runtime context snapshot
- compact agent context
- deterministic gates
- packaging handoff
- provider wiring/readiness
- provider boundary/cost firewall
- controlled memory
- vector-safe retrieval
- closed learning loop
- runtime dashboard ops
- freeze verifier

## No-Execution Proof Checklist

| Proof | Expected | Baseline status |
| --- | --- | --- |
| ProviderAttempt created | no | PASS by R3D10 verifier/default fixture |
| MediaRenderJob created | no | PASS by R3D10/manual runtime boundary |
| Provider job submitted | no | PASS; provider execution remains disabled |
| Drive upload | no | PASS |
| YouTube upload/publish/reupload | no | PASS; no YouTube upload API route |
| Pexels download/search | no | PASS |
| Dashboard execute/generate/render/upload buttons | no | PASS by R3D9/R3D10 dashboard checks |
| Read model flag | `no_provider_media_upload_execution=true` | PASS |

## Evidence References

- `reports/r3d10_runtime_lts_freeze_report.md`
- `docs/architecture/runtime_lts_v1.md`
- `docs/architecture/runtime_lts_invariant_catalog.md`
- `docs/architecture/provider_stack_freeze.md`
- `docs/operations/post_freeze_protocol.md`
- `docs/operations/production_pain_log_policy.md`
- `tests/test_r3d10_runtime_lts_freeze.py`
- `tests/test_r3d9_runtime_dashboard_ops.py`
- `tests/test_dx1_semantic_code_convention.py`
- `tests/test_dx2_provider_stack_reconciliation.py`

## Remaining Known P0/P1/P2/P3

| Severity | Known remaining items | Rule |
| --- | --- | --- |
| P0 | None recorded for this baseline | Patch immediately if safety/security/data-integrity/policy/provider/upload leak appears |
| P1 | None recorded for this baseline | Patch only for serious runtime/gate/operator-blocking defect or freeze invariant regression |
| P2 | None recorded for this baseline | Log in `reports/production_pain_log.md`, batch review every 2-4 weeks |
| P3 | None recorded for this baseline | Log in `reports/production_pain_log.md`, batch review every 2-4 weeks |

## Baseline Verdict

Runtime LTS v1 baseline is PASS for manual-ops/read-only operation. Manual Pilot 001 may be planned from this baseline, but pilot execution is a separate next task.
