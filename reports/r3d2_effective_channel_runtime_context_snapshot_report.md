# R3D2 EffectiveChannelRuntimeContextSnapshot

## Files changed
- `alembic/versions/0025_r3d2_effective_context_snapshot.py`
- `app/db/models/r3d2.py`
- `app/contracts/r3d2.py`
- `app/services/r3d2.py`
- `app/db/models/workflow.py`
- `app/db/models/m12_2.py`
- `app/db/models/__init__.py`
- `app/contracts/m12_2.py`
- `app/contracts/__init__.py`
- `app/services/m5.py`
- `app/services/m12_2.py`
- `app/services/__init__.py`
- `app/services/r3d1.py` (fix prerequisite: DB payload giữ UUID/Enum đúng type, hash vẫn dùng JSON payload)
- `tests/test_r3d2_effective_channel_runtime_context.py`
- `tests/test_migration.py`
- `tests/conftest.py`
- `tests/qualification/helpers/qualification_asserts.py`

## Model / schema summary
- Added `EffectiveChannelRuntimeContextSnapshot`.
- Snapshot lưu frozen refs: `video_project_id`, `channel_profile_version_id`, `compiled_policy_snapshot_id`, `channel_contract_hash`, `content_category_id`, optional character refs, `compile_status`, `reason_codes_json`, `context_hash`.
- Added 14 subcontext JSON fields:
  market/locale, audience, brand voice persona, category runtime, character identity, visual style, voice audio, thumbnail, metadata SEO, publish timing, source rights disclosure, monetization CTA, cost/provider policy, safety/forbidden claims.
- `VideoProject.effective_context_snapshot_id` now has FK to snapshot table, vẫn nullable cho row cũ.
- `FirstScriptedVideoPackage` stores `effective_context_snapshot_id` and `effective_context_hash`.

## Migration summary
- Alembic head: `0025_r3d2_effective_context`.
- Creates `effective_channel_runtime_context_snapshots`.
- Adds FK/index for `video_projects.effective_context_snapshot_id`.
- Adds effective context ref/hash columns to `first_scripted_video_packages`.

## Compiler behavior
- `EffectiveChannelRuntimeContextCompiler.ensure_for_project()` returns existing project snapshot if already frozen.
- If missing, compiler reads frozen `VideoProject.policy_snapshot_id`, `CompiledChannelPolicySnapshot`, `ChannelProfileVersion`, `ContentCategory`, and optional `CharacterBinding`.
- Compiler inserts immutable snapshot, then stores snapshot id/hash on `VideoProject`.
- Does not mutate `ChannelProfileVersion`, `CompiledChannelPolicySnapshot`, or channel init truth.

## Compile precedence
1. Hard VCOS/runtime safety context.
2. Channel Contract hard rules.
3. Character continuity refs if bound.
4. Category rules.
5. Channel creative defaults.
6. Editorial slot binding hint if provided.
7. No memory/vector/RAG in R3D2.

## PASS / REVIEW / BLOCK rules
- `PASS`: complete contract + policy snapshot + active category + valid required character refs.
- `REVIEW_REQUIRED`: optional style note missing via category visual style flag.
- `BLOCK`: missing/incomplete contract, missing policy snapshot, missing category, inactive category, required character missing, forbidden character binding for `NO_CHARACTER`, inactive/unsafe character refs, language/market conflict, publish timezone missing, paid-provider policy conflict.

## Integration
- M5 admission: after R3D1 admits and creates `VideoProject`, R3D2 compiles effective snapshot and writes refs into `audience_delivery_summary`.
- M12.2 package/full rehearsal: if request has `video_project_id`, package generation ensures effective snapshot before agent chain. `BLOCK`/`REVIEW_REQUIRED` stops before prompt render/LLM.
- Package read/review exposes effective context ref/hash.

## Tests run
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d2_effective_channel_runtime_context.py` -> 15 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d1_hierarchical_scope.py` -> 10 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_migration.py` -> 2 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py` -> 21 passed, 1 warning.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_m5_daily_run_context_admission.py tests/qualification/test_pre_m7_m5_daily.py` -> 15 skipped by existing M12.1R cutover marks.
- `PYTHONPATH=. .venv/bin/pytest -q tests/qualification/test_pre_m7_migrations.py` -> 2 passed.
- Compile smoke: `PYTHONPATH=. .venv/bin/python -m compileall -q ...` -> passed.

## Guardrail result
- No provider/media/upload calls added in `app/services/r3d2.py`.
- No vector/RAG/memory retrieval added.
- No EffectiveChannelRuntimeContextSnapshot prompt compression, AgentContextPack, QC gates, dashboard UI, or paid provider activation.

## Follow-up R3D3
- Build AgentContextPack/digest rendering from frozen snapshot refs.
- Add PromptBudgetGate / ContextPackShapeGate if R3D3 requires.
- Keep agents reading snapshot ref/hash, not latest channel settings.
