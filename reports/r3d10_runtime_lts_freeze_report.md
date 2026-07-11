# R3D10 Runtime LTS Freeze + Post-Freeze Protocol

Ngày: 2026-07-04

## Freeze verdict

`PASS`

Runtime LTS v1 được freeze bằng read-only verifier. Không thêm engine, không thêm job, không provider execution, không upload/publish automation.

## Files changed

- `app/contracts/r3d10.py`
- `app/services/r3d10.py`
- `app/contracts/__init__.py`
- `app/services/__init__.py`
- `app/main.py`
- `docs/architecture/runtime_lts_v1.md`
- `docs/architecture/runtime_lts_invariant_catalog.md`
- `docs/architecture/provider_stack_freeze.md`
- `docs/architecture/source-of-truth.md`
- `docs/operations/post_freeze_protocol.md`
- `docs/operations/production_pain_log_policy.md`
- `tests/test_r3d10_runtime_lts_freeze.py`
- `reports/r3d10_runtime_lts_freeze_report.md`

Không thêm migration.

## Docs added/updated

- `runtime_lts_v1.md`: kiến trúc Runtime LTS v1, phase map, manual boundary, memory/vector rules, provider disabled boundary.
- `runtime_lts_invariant_catalog.md`: invariant catalog dạng machine-readable table.
- `post_freeze_protocol.md`: P0/P1/P2/P3, patch window, staging-first, regression gate.
- `production_pain_log_policy.md`: rule cho P2/P3 ProductionPainLog.
- `source-of-truth.md`: thêm Runtime LTS v1 và sửa wording M12 renderer gap theo DX2/R3D10.

## Verifier behavior

`RuntimeLTSFreezeVerifier` là read-only service:

- trả `freeze_status: PASS | BLOCKED | REVIEW_REQUIRED`
- gom `blocker_reason_codes`, `warning_reason_codes`
- trả `verified_components`, `evidence_refs`, `test_refs`, `generated_at`
- expose optional read-only API: `GET /ops/runtime-lts-freeze-check`

Verifier kiểm:

- channel/runtime authority
- agent context/prompt digest path
- output validation/deterministic gates
- packaging/manual handoff
- provider stack truth + DX2 drift guard
- memory/vector/learning rules
- R3D9 dashboard ops boundary
- Postgres/snapshot source-of-truth
- DX1 semantic import compatibility

## Invariant catalog summary

Catalog gồm P0/P1 invariants chính:

- `channel_runtime_authority`
- `agent_context_pack_snapshot_required`
- `prompt_digest_ref_hash_only`
- `deterministic_gate_freeze_rules`
- `provider_stack_drift_guard`
- `provider_execution_flags_default_false`
- `memory_prompt_eligibility_rule`
- `vector_sql_filter_first`
- `r3d9_ops_endpoints_get_only`
- `r3d9_frontend_no_job_control_buttons`
- `dx1_semantic_imports_and_wrappers`

P2/P3 sau freeze đi ProductionPainLog, không patch core ngay.

## Provider stack verification

DX2 `ProviderStackDriftGuard=PASS`.

Canonical active keys:

- `elevenlabs`
- `luma_api`
- `pexels_api`

Verifier block nếu thấy active stale key:

- `GOOGLE_VERTEX_VEO`
- `google-vertex-veo`
- `pexels_pixabay_free_fallback`
- `elevenlabs_flash_turbo`

Provider execution flags default false. Paid ledger default fixture không có `EXECUTED`. `ALLOWED_NOT_EXECUTED` không consume paid attempt.

## Dashboard ops verification

- R3D9 ops endpoints GET-only.
- Frontend ops view không có daily/no-view/vector/provider/render/upload/YouTube run button.
- Provider/Cost panel dùng `ProviderStackDriftGuard`.
- Retrieval manifest hide raw memory by default.
- Runtime trace đọc `EffectiveChannelRuntimeContextSnapshot`, không đọc latest mutable settings.

## Memory/vector/learning verification

- Memory prompt eligibility yêu cầu `APPROVED + SAFE + PROMPT_SAFE + FRESH`.
- Vector retrieval là SQL-filter-first.
- Agent context pack không chứa raw memory text.
- `MemoryInfluenceManifest` và `QualityDeltaAttribution` tồn tại.
- Memory/vector không override Channel Contract / Effective runtime snapshot.
- Không learning auto-promotion trong freeze.

## Provider execution/upload prohibition proof

- R3D10 chỉ thêm read-only verifier + GET endpoint.
- Không tạo ProviderAttempt/MediaRenderJob/upload job.
- Không thêm YouTube upload API route.
- Không thêm dashboard job-control button.
- `no_provider_media_upload_execution=true` trong verifier response.

## Post-freeze protocol summary

- P0: safety/security/data-integrity/policy/provider/upload leak.
- P1: operator-blocking runtime defect hoặc freeze invariant regression.
- P2/P3: ProductionPainLog, batch review 2-4 tuần.
- Backend/core chỉ đổi cho P0/P1.
- Staging-first, regression gate trước merge.
- Provider activation cần future phase explicit.

## Tests run

Pass:

- `pytest tests/test_r3d10_runtime_lts_freeze.py -q` → 8 passed
- R3D1-R3D10 group → 114 passed
- M1/M2 group → 64 passed
- DX1/DX2 group → 13 passed
- M12.2/M12.2R/M12.2S qualification group → 66 passed
- `pytest tests/test_migration.py -q` → 2 passed
- `python -m compileall -q app` → passed
- `npm run typecheck && npm run lint && npm run test` → passed, 21 frontend tests
- `git diff --check` → passed

Warnings only: Starlette `httpx` TestClient deprecation warning.

## Remaining known P2/P3

None recorded for R3D10 freeze.

## Final Runtime LTS v1 status

`Runtime LTS v1 = PASS`

R3D10 freezes current backend/core as dashboard/manual-ops runtime. Future work should follow post-freeze protocol and ProductionPainLog rules.
