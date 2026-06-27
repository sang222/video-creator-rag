# M12.1 Final Report - Agent Prompt Registry + Channel Contract

## Verdict

PASS.

## Repo

`/Users/sangss/Desktop/video-creator-rag`

## Preflight

- Working tree sạch trước khi mở M12.1: PASS.
- Tag `m12-production-readiness-center`: PASS.
- Source spec `reports/m12_1_prompt_registry_source_spec.md`: PASS.
- Đã đọc M12, M11.1, M10.1-M10.5 final reports trước khi sửa.
- Không commit/tag sau build.

## Implemented

- Repo-first prompt registry under `app/prompts/`.
- Common skills C1-C11.
- Agent-specific system deltas for 25 required roles.
- Versioned manifest `app/prompts/registry/agents.yaml`.
- BaseEnvelope JSON schema and eval fixtures.
- DB-backed prompt template/profile/contract/schema records.
- Prompt render runs and prompt audit snapshots.
- Prompt evaluation cases/runs.
- Prompt hash and context hash.
- Frozen Channel Contract binding to ChannelProfileVersion and CompiledChannelPolicySnapshot refs.
- Missing/incomplete channel contract returns REVIEW_REQUIRED with exact next action:
  `Bổ sung hoặc compile lại ChannelProfileVersion trước khi render prompt.`
- Ollama `/api/chat` message array support with legacy raw prompt compatibility.
- Router/API support for chat messages.
- ProviderAttempt/LLMRunSnapshot metadata captures lane, model, hashes, usage, validation pending, and repair outcome.
- Safe JSON parse/repair and BaseEnvelope validation.
- Prompt registry API:
  - `POST /prompt-registry/sync`
  - `POST /prompt-registry/render`
  - `POST /prompt-registry/validate-output`
  - `POST /prompt-registry/evaluations/run`

## Schema Added

- `prompt_template_records`
- `agent_prompt_profiles`
- `prompt_contract_versions`
- `structured_output_schemas`
- `prompt_render_runs`
- `prompt_audit_snapshots`
- `prompt_evaluation_cases`
- `prompt_evaluation_runs`

## Docs

- Added `docs/architecture/m12-1-prompt-registry.md`.
- Updated README, architecture ledger, and source-of-truth.
- Activation/rollback documented.

## Tests

- `.venv/bin/python -m compileall app`: PASS.
- `.venv/bin/pytest -q tests/qualification/test_m12_1_prompt_registry.py`: 5 passed.
- `.venv/bin/pytest -q tests/test_migration.py`: 2 passed.
- `.venv/bin/pytest -q tests/qualification/test_m10_1_llm_router_derivatives.py`: 5 passed, 1 warning.
- Combined focused suite `.venv/bin/pytest -q tests/qualification/test_m12_1_prompt_registry.py tests/test_migration.py tests/qualification/test_m10_1_llm_router_derivatives.py`: 12 passed, 1 warning.
- `.venv/bin/pytest -q tests/qualification/test_pre_m7_repo_preflight.py -k 'not test_worktree_has_no_unrelated_dirty_product_changes'`: 2 passed, 1 deselected.
- `git diff --check`: PASS.

## Expected Dirty-Tree Guard

- `.venv/bin/pytest -q tests/qualification/test_pre_m7_repo_preflight.py`: 2 passed, 1 failed.
- Failure was only `test_worktree_has_no_unrelated_dirty_product_changes` because M12.1 product files are intentionally uncommitted per user instruction.

## Not Run

- Không chạy old M12 real-smoke tests.
- Không bật real provider flags.
- Không gọi provider thật.
- Không chạy provider smoke từ milestone cũ.

## Scope Not Built

- No real provider calls by default.
- No YouTube upload/publish/reupload.
- No auto publish/upload/reupload.
- No new provider strategy.
- No channel config mutation or config upgrade suggestion.
- No DB-only canonical prompt blob system.
- No prompt self-mutation.
- No dashboard scraping/browser automation.
- No fake traffic, bot engagement, platform evasion, spam, or manipulation.
- No TikTok/Facebook analytics learning loop.

## Limitations

- Existing M1 compiled policy payload is narrower than the richer M12.1 Channel Contract shape; production callers should pass canonical `channel_contract_json` until Channel Init/Profile stores all rich fields directly.
- JSON schema validation is intentionally local/simple for BaseEnvelope; no external schema dependency added.
