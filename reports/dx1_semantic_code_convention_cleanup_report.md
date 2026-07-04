# DX1 — Semantic Code Convention Cleanup

## Files changed
- Added semantic facades under `app/services/`.
- Added compatibility comments to phase-coded service modules: `m1`, `m2`, `m5`, `m9`, `m10`, `m11`, `m12`, `m12_1`, `m12_1r`, `m12_2`, `m12_2r`, `m12_2p3`, `r3d1`-`r3d8`.
- Updated `app/services/__init__.py` to import R3D5-R3D8 exports via semantic facades.
- Added docs:
  - `docs/architecture/semantic_module_map.md`
  - `docs/architecture/domain_glossary.md`
  - `docs/architecture/phase_to_domain_map.md`
- Added tests: `tests/test_dx1_semantic_code_convention.py`.

## Semantic modules added
- `daily_operations`, `context_resolver`, `project_admission`
- `post_publish_diagnostics`
- `learning_candidates`, `learning_review`, `approved_playbook`
- `provider_readiness`, `provider_wiring`, `runtime_provider_boundary`
- `prompt_registry`, `prompt_audit`
- `video_package_generation`, `agent_rehearsal`, `package_generation_rehearsal`
- `publish_handoff`, `uploaded_video_backfill`
- `channel_contract_compiler`, `channel_init_research`
- `channel_scope_authority`, `channel_runtime_context`
- `agent_context_pack`, `output_validation_gates`
- `packaging_handoff`
- `controlled_memory`, `vector_retrieval`, `learning_loop`, `cost_firewall`

## Old → new mapping
- `m5` → `daily_operations`, `context_resolver`, `project_admission`
- `m9` → `post_publish_diagnostics`
- `m10` → `learning_candidates`
- `m11` → `learning_review`, `approved_playbook`
- `m12` → `provider_readiness`
- `m12_1` → `prompt_registry`, `prompt_audit`
- `m12_1r` + `m2` → `runtime_provider_boundary`
- `m12_2` → `video_package_generation`, `agent_rehearsal`, `package_generation_rehearsal`
- `m12_2r` → `publish_handoff`, `uploaded_video_backfill`
- `m12_2p3` → `channel_contract_compiler`, `channel_init_research`
- `m1` → `packaging_handoff`
- `m2` → `provider_wiring`
- `r3d1` → `channel_scope_authority`
- `r3d2` → `channel_runtime_context`
- `r3d3` → `agent_context_pack`
- `r3d4` → `output_validation_gates`
- `r3d5` → `controlled_memory`
- `r3d6` → `vector_retrieval`
- `r3d7` → `learning_loop`
- `r3d8` → `cost_firewall`

## Wrappers kept
- Old phase-coded modules remain import-compatible.
- Semantic modules are facade re-exports; no implementation move in DX1.
- Phase-coded services include compatibility comments for reports/tests/backward compatibility.

## Imports updated
- `app/services/__init__.py` now routes R3D5-R3D8 service exports through:
  - `controlled_memory`
  - `vector_retrieval`
  - `learning_loop`
  - `cost_firewall`

## Docs added
- `semantic_module_map.md`: domain ownership and semantic service entrypoints.
- `domain_glossary.md`: runtime terms and boundaries.
- `phase_to_domain_map.md`: M/R3D phase-coded map and compatibility status.

## Tests run and result
- `PYTHONPATH=. .venv/bin/python -m compileall -q app` — PASS
- `PYTHONPATH=. .venv/bin/pytest tests/test_dx1_semantic_code_convention.py -q` — PASS, `6 passed`
- Regression:
  - DX1, R3D1-R3D8, M1/M2, M12.2/M12.2S, migration tests
  - PASS: `154 passed, 1 warning`
- `PYTHONPATH=. .venv/bin/alembic heads` — PASS, head `0031_r3d8_cost_firewall`
- `PYTHONPATH=. .venv/bin/alembic upgrade head --sql` — PASS
- `git diff --check` — PASS

## Behavior-change statement
No runtime behavior changes intended.

## DB/API safety
- No DB table renamed.
- No Alembic history rewritten.
- No public API route removed.
- No frontend route changed.
- No provider/media/upload execution added.

## Unresolved naming debt
- Contracts/models remain phase-coded at file level to avoid DTO/table churn.
- M0-M8, M10.1/M10.2/M10.3/M10.5, M11.1 remain phase-coded where they are historical infrastructure or dashboard/runtime support.
- `m12_2p` has no active service file; current compiler path is represented by `m12_2p3` semantic facades.

## Recommended cleanup after Runtime LTS
- Move implementation bodies into semantic modules one domain at a time.
- Keep old phase-coded files as thin wrappers permanently until all external references are retired.
- Add contract/model semantic facades only after table/DTO stability is frozen.
