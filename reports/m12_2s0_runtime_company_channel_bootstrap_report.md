# M12.2S0 Runtime Bootstrap Company + Channel Contract Report

## Verdict

PASS.

M12.2S now has a safe local runtime bootstrap prerequisite for real operator-owned company/channel setup before any full agent rehearsal is attempted.

## Repo Path

`/Users/sangss/Desktop/video-creator-rag`

## Company Bootstrap Behavior

- API supports `GET /companies` and `POST /companies`.
- `POST /companies` creates real setup data with `name` and `slug`, returning the company UUID.
- CLI supports:
  - `vcos companies list`
  - `vcos companies create --name "VCOS Company" --slug "vcos-company"`
  - `vcos bootstrap company --name "VCOS Company" --slug "vcos-company"`
- Company creation/bootstrap is idempotent by slug and returns the existing UUID when the slug already exists.
- No company creation path creates a channel, mock data, provider record, upload, or publish artifact.

## Dashboard Company Selector

- Create Channel no longer asks the operator to type raw `ID công ty`.
- The form now loads companies and displays a `Công ty` dropdown with company name and slug.
- The selected option carries the hidden UUID value submitted as `company_id`.
- If exactly one company exists, the dashboard auto-selects it.
- If no company exists, the form shows `Tạo công ty trước` with an inline mini form for company name and slug; after creation, the returned company UUID is selected.

## Channel Creation Behavior

- Channel Init sends the selected company UUID as `company_id`.
- Backend channel creation validates that the company exists before creating a channel.
- Channel Init still creates `ChannelProfileVersion`, compiles `CompiledChannelPolicySnapshot`, builds `channel_contract_json`, and computes contract status.
- Activation still requires Channel Contract `COMPLETE`; no `ChannelProfileVersion` mutation after activation was introduced.

## M12.2S Preflight Update

Added a local M12.2S preflight path:

- No company: `BLOCKED_NEEDS_COMPANY`, next action `Tạo company trước, sau đó tạo channel.`
- Company exists but no channel: `BLOCKED_NEEDS_CHANNEL`, next action `Tạo channel bằng Channel Init và compile snapshot.`
- Channel exists but active compiled contract is missing/incomplete: `BLOCKED_NEEDS_CHANNEL_CONTRACT`, next action `Bổ sung field còn thiếu và compile lại ChannelProfileVersion.`

M12.2S full rehearsal now checks local company/channel/contract prerequisites before provider readiness or LLM routing.

## Tests Run

- `.venv/bin/pytest -q tests/test_m12_2s0_runtime_bootstrap.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py tests/test_m1_api_cli.py` -> 23 passed, 1 warning.
- `.venv/bin/pytest -q tests/test_m12_2s0_runtime_bootstrap.py` -> 9 passed, 1 warning.
- `npm test -- src/features/channels/__tests__/channel-init-wizard.test.tsx` -> 5 passed.
- `npm run typecheck` -> passed.
- `npm run lint` -> passed.
- `npm test` -> 18 passed.
- `npm run build` -> passed.
- `npm run e2e` -> 1 passed.

## Old Smoke Rule Status

- No old provider smoke tests were run.
- M12.2S0 preflight tests assert no provider readiness call before company/channel/contract bootstrap.
- Source guard confirms no `RealSmokeOrchestratorService` or `run_provider` path in `app/services/m12_2.py`.

## Scope Not Built

- Did not create mock company/channel.
- Did not create fake runtime data.
- Did not call real providers.
- Did not upload, publish, or reupload.
- Did not run M12.2S agent rehearsal against local runtime data.
- Did not create commit or tag.

## Next Action

Create the real local company and channel contract, activate only after Channel Contract is `COMPLETE`, then rerun M12.2S.
