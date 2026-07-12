# INT1 Post-Freeze Integration Smoke: Ollama + Drive Archive + Snowball

Date: 2026-07-04

Verdict: PASS for focused Ollama + Drive rerun

Summary:
- Runtime LTS freeze verifier: PASS.
- ProviderStackDriftGuard: PASS.
- Snowball regression: PASS on safe M12.2S fixture.
- Ollama real smoke: PASS after Docker/env sync.
- Google Drive archive smoke: PASS after OAuth reauth.
- No media-provider/render/YouTube upload-publish execution observed.
- Allowed executions in this focused rerun: Ollama LLM smoke and Google Drive archive/storage upload only.

Focused rerun note:
- Docker API was rebuilt/recreated with current `Dockerfile`.
- Google Drive OAuth reauth completed via existing `/auth/google-drive/start` and callback flow.
- No backend/core patch was made.
- Stale M10.1/M10.5 qualification tests remain a P2 maintenance-window item.

## Baseline identity

| item | value |
| --- | --- |
| current repo HEAD | `0c09acb42e7f888853a3ab9c3561da0bc9bc059a` |
| expected freeze commit | `5ffa56ac2fb204bb7b3d11388c2f0803cbfbb4f4` |
| expected tag | `r3d10-runtime-lts-v1` |
| tag SHA | `5ffa56ac2fb204bb7b3d11388c2f0803cbfbb4f4` |
| migration head | `0031_r3d8_cost_firewall (head)` |
| provider activation | NO-GO |
| auto upload/publish | NO-GO |

Note: current HEAD contains post-freeze reports/evidence after the freeze tag. Freeze tag still points to the expected baseline commit.

## Commands run

| command | result |
| --- | --- |
| `PYTHONPATH=. .venv/bin/alembic heads` | PASS: `0031_r3d8_cost_firewall (head)` |
| `PYTHONPATH=. .venv/bin/python -m compileall -q app` | PASS |
| `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` | PASS: 8 passed, 1 warning |
| `PYTHONPATH=. .venv/bin/pytest tests/test_dx2_provider_stack_reconciliation.py -q` | PASS: 7 passed |
| `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q` | PASS: 2 passed, 1 warning |
| `git diff --check` | PASS before report edits |
| `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m10_1_llm_router_derivatives.py::test_llm_router_lanes_disabled_guard_no_provider_call_and_smoke_skip tests/qualification/test_m10_1_llm_router_derivatives.py::test_ollama_payload_and_router_fallback_logging tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py::test_m12_2s_llmrouter_real_path_creates_provider_and_llm_snapshots -q` | PASS: 3 passed, 1 warning |
| `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m10_1_llm_router_derivatives.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py -q` | REVIEW_REQUIRED: 3 stale qualification failures |
| `PYTHONPATH=. .venv/bin/pytest tests/test_m10_5_drive_real_smoke.py tests/qualification/test_m10_5_google_drive_offload.py -q` | REVIEW_REQUIRED: 7 passed, 1 skipped, 2 stale qualification failures |
| `docker compose up -d --build api` | PASS: API image rebuilt and container recreated healthy |
| `docker compose exec -T api vcos integrations smoke --provider ollama` | PASS: RealSmokeRun `995a5641-7c95-4825-822b-9ff6c929cd1a` |
| `docker compose exec -T api vcos integrations smoke --provider ollama` | PASS: focused rerun RealSmokeRun `6a948227-4c58-41bf-84c8-c533f436cbed` |
| `GET /auth/google-drive/start` then browser consent/callback | PASS: latest GoogleDriveOAuthSession `TOKEN_EXCHANGED` |
| `docker compose exec -T api vcos drive offload --path /app/var/tmp/int1-drive-smoke/vcos_drive_archive_smoke_20260704T122628Z.json --media-type OTHER --keep-local` | PASS: MediaOffloadJob `f98c7aae-f534-4b3a-89e0-d6a83cd9b834` VERIFIED |

Stale qualification failures are logged as `PPL-INT1-001`; no backend/core patch was made.

## Freeze and provider stack

RuntimeLTSFreezeVerifier:
- `freeze_status=PASS`
- `blocker_reason_codes=[]`
- `warning_reason_codes=[]`
- `verified_components_count=30`
- `evidence_refs_count=38`
- `no_provider_media_upload_execution=true`

ProviderStackDriftGuard:
- `status=PASS`
- stale active providers: none
- `no_provider_call_made=true`

## Ollama / LLMRouter smoke

Status: PASS after Docker/env sync

Docker env:
- `VCOS_LLM_REAL_EXECUTION_ENABLED=true`
- `VCOS_LLM_ROUTER_REAL_SMOKE=true`
- `VCOS_LLM_PROVIDER=ollama`
- `OLLAMA_BASE_URL=http://ollama:11434`

RealSmokeRun initial Docker rerun:
- id: `995a5641-7c95-4825-822b-9ff6c929cd1a`
- provider_key: `ollama`
- run_state: `PASS`
- result_summary: `Ollama smoke 4 lane thành công.`
- started_at: `2026-07-04T12:21:07.628920+00:00`
- completed_at: `2026-07-04T12:21:24.144195+00:00`

Lanes:

| lane | route_attempt_id | provider_attempt_id | llm_run_snapshot_id | model | status | tokens |
| --- | --- | --- | --- | --- | --- | --- |
| cheap_structured | `b56d063a-f1fb-4906-b083-763980900457` | `637a8e47-f065-4bdf-89d7-e938362902fc` | `3e1e04b4-35cd-44f5-bb2e-97c7b7c9789e` | `gpt-oss:20b-cloud` | SUCCESS | 218 |
| long_context_text | `dbdb1268-527f-429b-b95b-7d826027e42c` | `0322e1a6-9788-4133-8705-c539e67927d1` | `cd861af9-414b-4206-a4c3-a855ad0acdb9` | `deepseek-v4-flash:cloud` | SUCCESS | 249 |
| visual_creative_review | `0007c1ee-3963-4650-b2de-32cb3ead5f0f` | `5b49f7dc-0aa3-47ba-bd61-c8396cdea7f4` | `34b0486e-142c-4e22-b872-b090e8745902` | `minimax-m3:cloud` | SUCCESS | 343 |
| gatekeeper_soft_review | `d942b4b6-f9bf-4d70-9c95-4942f09eb9a1` | `6881a5e2-8239-45fa-b723-f9bd21707b32` | `3a3b666c-ffb0-47bb-9c03-2b1474208ca7` | `nemotron-3-super:cloud` | SUCCESS | 61 |

Focused rerun:
- id: `6a948227-4c58-41bf-84c8-c533f436cbed`
- run_state: `PASS`
- result_summary: `Ollama smoke 4 lane thành công.`
- lanes: `cheap_structured`, `long_context_text`, `visual_creative_review`, `gatekeeper_soft_review`
- lane statuses: all `SUCCESS`
- note: `cheap_structured` primary `gpt-oss:20b-cloud` had one `PROVIDER_TIMEOUT`, then fallback `qwen3.5:cloud` succeeded; route result remained PASS.

No mock fallback or dry-run success was treated as production success.

## Safe fixture

Fixture source: `tests.qualification.test_m12_2s_full_agent_ollama_rehearsal`

Selected safe fixture evidence:
- package_id: `c44bd487-2a42-497f-8b62-b87e29e2ca8a`
- package_status: `WAITING_PROVIDER_CONFIG`
- fake_router_call_count: 13
- boundary_status: `BLOCKED_PROVIDER_NOT_CONFIGURED`

This fixture creates only test DB evidence and package handoff artifacts. It does not create media, provider jobs, Drive upload, or YouTube upload.

## Snowball regression

Status: PASS

| agent | context_pack_snapshot_id | prompt_render_run_id | shape | budget | sys/user chars | est tokens | context chars | omitted | raw hits | late digest hits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ScriptWriterAgent | `9d8aee30-0860-4875-9d01-63b8ce5e0eb1` | `f9870250-c2b8-4097-8a20-cd5201913f4c` | OK | OK | 3109 / 21756 | 6216 | 11779 | 24 | none | none |
| VisualPlanningAgent | `62b733f2-e6d4-4720-9b16-636234ad0f85` | `d2f8d8ff-3314-40ae-9aa8-d9696a7ed4ec` | OK | OK | 3525 / 21898 | 6356 | 11774 | 24 | none | none |
| ProviderReadinessSummaryAgent | `26d281f3-ba81-4257-adca-f54e607a8f42` | `cc996f90-26aa-4ab0-b0e6-335c1ddc9611` | OK | OK | 3408 / 21353 | 6190 | 11050 | 27 | none | none |
| MediaQCExplanationAgent | `26a068f5-ad31-46fb-8f55-de73b9374a4e` | `f790213e-45be-44bc-b24f-b3fb9a96bfda` | OK | OK | 3316 / 22624 | 6485 | 12317 | 25 | none | none |

Digest/ref/hash checks:
- no full previous artifacts in production prompt payload
- no raw memory text
- no raw `channel_contract_json` / `compiled_policy_snapshot_json` payload marker
- no provider readiness raw snapshot in late agents
- no latest mutable channel settings; `latest_channel_settings_read=false`
- PromptBudgetMetrics present
- full contract/policy retained in audit refs, not injected as default production prompt payload

Late-agent contract checks:
- ProviderReadinessSummaryAgent digest keys: `common_skill_digest`, `effective_channel_runtime_digest`, `package_status_digest`, `provider_readiness_digest`, `runtime_guard_digest`
- MediaQCExplanationAgent digest keys: `common_skill_digest`, `effective_channel_runtime_digest`, `gate_summary_digest`, `media_inventory_digest`, `package_summary_digest`, `provider_readiness_digest`, `runtime_guard_digest`
- no disallowed full script/visual/metadata/topic digest for these late agents

## Google Drive archive smoke

Status: PASS after OAuth reauth

Preflight:
- archive/offload enabled by Settings: true
- root folder configured: true
- explicit smoke flag by Settings: true
- token file count: 1
- safe status: `CONFIGURED`
- scope: `https://www.googleapis.com/auth/drive.file`
- upload mode: `resumable`
- secret values exposed: false

Attempted artifact:
- filename: `vcos_drive_archive_smoke_20260704T115444Z.json`
- mime_type: `application/json`
- size_bytes: 232
- checksum_sha256: `bd9a0a4cee2000369885402a624a36c8033255eb187ab37821710f678a001da2`
- purpose: archive/storage smoke only

Initial result before reauth:
- job_id: `ec59e701-c7fc-4897-85c5-715c5607769a`
- job_state: `FAILED`
- error_code: `GOOGLE_DRIVE_NEEDS_REAUTH`
- cloud_media_ref_id: none
- uploaded: false

Reauth evidence:
- latest GoogleDriveOAuthSession status: `TOKEN_EXCHANGED`
- OAuth session id: `a5bdd8c8-f3b1-43f2-986f-6dcd284b24e0`
- credential_reference_id: `765eb3a1-e708-4be6-ac08-41b9dcac8fbc`
- connection_state: `CONNECTED`

Focused rerun result:
- filename: `vcos_drive_archive_smoke_20260704T122628Z.json`
- host path: `var/tmp/int1-drive-smoke/vcos_drive_archive_smoke_20260704T122628Z.json`
- size_bytes: 233
- checksum_sha256: `45749dcc3e6325c8989880c4366132974cf0840179a10a2f3e20323e3485ef05`
- MediaOffloadJob: `f98c7aae-f534-4b3a-89e0-d6a83cd9b834`
- job_state: `VERIFIED`
- CloudMediaRef: `c5f489b8-d09d-4818-8146-2206e487d76f`
- storage_provider: `GOOGLE_DRIVE`
- media_type: `OTHER`
- mime_type: `application/json`
- upload_status: `VERIFIED`
- verification_status: `CHECKSUM_UNAVAILABLE`
- size_verified: true
- web_view_link: present
- local_cleanup_status: `SKIPPED` because `--keep-local` was used

Archive-only proof:
- target_media_type: `OTHER`
- company/channel/project/uploaded_video/render_package refs: none
- FinalMediaRef count: 0
- HumanUploadTask count: 0
- MediaRenderJob count: 0
- not source of truth
- not publishable

`PPL-INT1-002` is resolved by this focused rerun. No backend/core patch was made.

## No-execution proof

Package/snowball fixture counts:
- ProviderAttempt total: 0
- forbidden provider attempts: 0
- MediaRenderJob: 0
- HumanUploadTask: 0
- ProviderJobSnapshot SUBMITTED: 0
- PaidProviderCallLedger EXECUTED: 0
- CloudMediaRef: 0
- MediaOffloadJob: 0
- FinalMediaRef: 0

Drive archive focused rerun exception:
- exactly one Drive archive job was executed because explicit Settings config was enabled and OAuth was reauthed
- it created CloudMediaRef `c5f489b8-d09d-4818-8146-2206e487d76f`
- CloudMediaRef is archive/storage only: `media_type=OTHER`, no project/uploaded-video/render-package refs
- FinalMediaRef remained 0
- HumanUploadTask remained 0
- MediaRenderJob remained 0

Hard boundaries confirmed:
- no ElevenLabs generation
- no Google Veo generation
- no Pexels search/download
- no YouTube upload/publish/reupload
- no provider media job
- no paid provider executed call
- no dashboard execute/generate/render/upload buttons added
- no ChannelProfileVersion or Channel Contract mutation
- no learning auto-promotion
- no prompt self-mutation

Initial Ollama rerun no-execution proof captured before Drive focused rerun:
- forbidden provider attempts: 0
- MediaRenderJob: 0
- HumanUploadTask: 0
- ProviderJobSnapshot SUBMITTED: 0
- PaidProviderCallLedger EXECUTED: 0
- CloudMediaRef: 0
- MediaOffloadJob: 0
- FinalMediaRef: 0

Focused Ollama + Drive no-execution proof since `2026-07-04T12:23:59+00:00`:
- Ollama ProviderAttempt rows: 5, all provider_key `OLLAMA`
- forbidden media provider attempts excluding allowed Drive archive: 0
- Drive ProviderAttempt rows: 0
- Drive CloudMediaRef rows: 1, archive/storage only
- Drive MediaOffloadJob rows: 1, target_media_type `OTHER`
- MediaRenderJob: 0
- HumanUploadTask: 0
- ProviderJobSnapshot SUBMITTED: 0
- PaidProviderCallLedger EXECUTED: 0
- FinalMediaRef: 0

## Findings

P0: none.

P1: none.

P2:
- `PPL-INT1-001`: stale M10.1/M10.5 qualification tests still assert pre-R3D10 migration/config/helper behavior.
- `PPL-INT1-002`: resolved by OAuth reauth + focused Drive archive rerun.

P3: none.

## Next safe action

1. Batch-review stale qualification tests in the next approved P2/P3 maintenance window.
