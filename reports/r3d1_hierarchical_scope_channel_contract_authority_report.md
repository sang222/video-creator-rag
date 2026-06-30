# R3D1 Hierarchical Scope + Channel Contract Authority Report

## Kết quả

IMPLEMENTED. DB pytest chưa chạy được vì Postgres local `localhost:55432` và Docker daemon đều đang tắt.

## Files changed

- `app/db/models/r3d1.py`
- `app/contracts/r3d1.py`
- `app/services/r3d1.py`
- `alembic/versions/0024_r3d1_hierarchical_scope.py`
- `app/db/models/{m5,workflow,__init__}.py`
- `app/contracts/{m5,workflow,m10_5,__init__}.py`
- `app/services/{m5,workflow,__init__}.py`
- `app/main.py`
- `config/cloud_media_type_catalog.yaml`
- `tests/test_r3d1_hierarchical_scope.py`
- `tests/{conftest.py,test_migration.py}`
- `tests/qualification/{conftest.py,helpers/qualification_asserts.py}`

## Models added

- `ContentCategory`
- `CategoryCreativeDigest`
- `CharacterProfile`
- `CharacterVersion`
- `CharacterImageBranch`
- `CharacterReferenceAssetPack`
- `CharacterReferenceAsset`
- `VoiceProfile`
- `CharacterBinding`

Patched:

- `EditorialCalendarSlot.category_id`
- `EditorialCalendarSlot.character_binding_policy_json`
- `VideoProject.category_id`
- `VideoProject.character_binding_id`
- `VideoProject.channel_contract_content_hash`
- `VideoProject.effective_context_snapshot_id`

## Migration summary

Added Alembic head `0024_r3d1_hierarchical_scope`.

Creates R3D1 scope/character tables and nullable FKs on `editorial_calendar_slots` and `video_projects`.

## Service summary

- `ChannelRuntimeAuthorityService`: reads active `CompiledChannelPolicySnapshot`, profile ref, frozen `channel_contract_json`, and computes `channel_contract_content_hash`.
- `CategoryScopeResolver`: resolves explicit/slot category or auto-binds when exactly one ACTIVE category exists.
- `CharacterBindingResolver`: enforces `NO_CHARACTER`, `OPTIONAL_CHARACTER`, `REQUIRED_CHARACTER` and verifies active version/branch/asset pack/voice readiness.
- `ProjectScopeAdmissionGuard`: blocks production admission before `VideoProject` creation when runtime scope is incomplete.
- `R3D1AdminService`: minimal create/list/read backend service for categories and character authority records.

## Channel contract authority

Production admission now requires:

- active policy snapshot ref,
- matching active channel snapshot,
- `channel_contract_json.contract_status == COMPLETE`,
- immutable contract hash stored on `VideoProject.channel_contract_content_hash`.

No `ChannelProfileVersion` or `CompiledChannelPolicySnapshot` mutation is introduced.

## Category scope

Production admission now requires deterministic category scope:

- explicit category/request or slot category must belong to same company/channel and be ACTIVE,
- if missing and exactly one ACTIVE category exists, auto-bind,
- if missing/ambiguous, block with `CATEGORY_SCOPE_MISSING`.

## Character binding

- `NO_CHARACTER`: binding is forbidden.
- `OPTIONAL_CHARACTER`: no binding required.
- `REQUIRED_CHARACTER`: requires one valid ACTIVE binding plus active `CharacterVersion`, active `CharacterImageBranch`, SAFE/PROMPT_SAFE `CharacterReferenceAssetPack`, and ALLOWED active `VoiceProfile`.

No prompt-based character continuity added.

## Tests run

- `PYTHONPATH=. .venv/bin/python -m compileall app` -> PASS.
- `PYTHONPATH=. .venv/bin/alembic heads` -> PASS, head `0024_r3d1_hierarchical_scope`.
- `PYTHONPATH=. .venv/bin/alembic upgrade head --sql > /tmp/vcos_r3d1_upgrade.sql` -> PASS.
- `PYTHONPATH=. .venv/bin/python -c "from app.main import create_app; app=create_app(); print(len(app.routes))"` -> PASS.
- `PYTHONPATH=. .venv/bin/python -m py_compile tests/test_r3d1_hierarchical_scope.py` -> PASS.
- `git diff --check` -> PASS.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d1_hierarchical_scope.py` -> BLOCKED before tests: Postgres `localhost:55432` refused connection; Docker daemon unavailable.

## Source guards

- No provider/media/upload calls added in R3D1 service.
- No vector/RAG/embedding retrieval added.
- Cloud media additions are enum/catalog only: `CHARACTER_REFERENCE`, `CHARACTER_FACE_REF`, `CHARACTER_BRANCH`, `VOICE_REFERENCE`, `REFERENCE_PACK`.

## Remaining work for R3D2

- Implement `EffectiveChannelRuntimeContextSnapshot`.
- Add `AgentContextPack`.
- Add prompt budget/context packaging.
- Keep DB snapshot refs as runtime source of truth.
