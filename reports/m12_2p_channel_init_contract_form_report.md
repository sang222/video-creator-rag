# M12.2P Channel Init Contract Form + Snapshot Compiler Repair Report

## Verdict

PASS.

## Repo path

`/Users/sangss/Desktop/video-creator-rag`

## Preflight status

PASS.

- Repo path đúng.
- Working tree sạch trước khi mở M12.2P.
- Required source reports tồn tại và đã đọc.
- Không commit/tag sau build.

## Tags verified

PASS.

- `m12-1-prompt-registry-contracts`
- `m12-1r-mock-dryrun-purge`
- `m12-2-first-scripted-video-package`
- `m12-2r-publish-handoff-ledger`

## Schema/model changes

PASS.

- Extended `ChannelWorkspaceCreate/Read` with structured locale/profile fields already present in DB.
- Extended `CompiledChannelPolicyPayload` with:
  - `channel_contract_json`
  - `compiled_policy_snapshot_json`
  - `contract_status`
  - `missing_fields`
  - `contradiction_reasons`
  - `activation_required`
- No DB migration needed; existing JSONB columns store the M12.2P contract/snapshot payload.

## API changes

PASS.

- Added `POST /channels` alias with `company_id`.
- Added `POST /channels/{channel_id}/compile-policy-snapshot`.
- Added `GET /channels/{channel_id}/policy-snapshot`.
- Added `POST /channels/{channel_id}/activate`.
- Kept existing `/companies/{company_id}/channels`, profile compile, and snapshot activate routes.
- Activation route now blocks if Channel Contract is not `COMPLETE`.

## Compiler changes

PASS.

- Added shared `app/services/channel_contract.py`.
- `ChannelProfileCompiler` now compiles full `channel_contract_json`.
- Snapshot payload includes market/locale, audience, editorial, format, voice, platform, media, rights, cost, learning, and forbidden behavior.
- Contract statuses: `COMPLETE`, `PARTIAL`, `MISSING`, `STALE`, `CONTRADICTORY`.
- Missing/contradictory fields are persisted in the compiled payload.
- M12.2 package uses frozen contract from active snapshot, not profile fallback.

## Frontend changes

PASS.

- Rebuilt `Tạo kênh` as structured Vietnamese multi-section form.
- Added status preview:
  - `Hồ sơ đủ để kích hoạt`
  - `Thiếu thông tin cấu hình`
  - backend supports `Có cấu hình mâu thuẫn`
- Channel detail tab `Hồ sơ & chính sách kênh` now shows snapshot ID/version, contract status, missing fields, market/locale, and human handoff.
- Channel list now shows contract review states:
  - `Cần bổ sung hồ sơ`
  - `Cần review policy snapshot`
  - `Đủ điều kiện kích hoạt`

## Removed budget fields

PASS.

- Removed channel init UI/payload inputs:
  - `tts_character_budget`
  - `Ngân sách ký tự TTS`
  - `ai_hero_budget_usd`
  - `Ngân sách AI hero USD`
- Backend rejects legacy provider budget keys in channel/profile init payloads.
- Channel Contract keeps only non-numeric cost policy:
  - `cost_sensitivity`
  - `avoid_unnecessary_ai_hero`
  - `prefer_reuse_safe_assets`
  - `exact_cost_claim_requires_provider_snapshot`
- Dashboard copy added:
  `Ngân sách provider được cấu hình trong Cài đặt / Tích hợp, không nhập theo từng kênh.`

## Channel Contract sections implemented

PASS.

- `channel_identity`
- `target_audience`
- `market_locale`
- `editorial_strategy`
- `format_policy`
- `voice_style`
- `platform_strategy`
- `media_policy`
- `rights_policy`
- `budget_policy`
- `learning_policy`
- `forbidden_behavior`
- `contract_status`

## Market/locale behavior

PASS.

- Market and locale are structured fields.
- Explicit missing `primary_market` stays missing and marks `PARTIAL`; no US default.
- Explicit missing `content_language` stays missing and marks `PARTIAL`; no English default.
- Missing timezone/currency stay null/missing in explicit contract mode.
- Contract hash changes when `market_locale` changes.

## Activation gating

PASS.

- `PARTIAL`, `MISSING`, `STALE`, and `CONTRADICTORY` contracts block activation.
- `auto_publish_allowed=true` creates `CONTRADICTORY`.
- Studio scraping, config mutation, auto-promote learning, AI hero audio, and forbidden-use conflicts are blocked by compiler validation.
- Complete contracts can activate channel snapshots.

## Snapshot behavior

PASS.

- Channel creation flow creates `ChannelProfileVersion`.
- Compile creates `CompiledChannelPolicySnapshot` with frozen `channel_contract_json`.
- Future package/prompt runs use active snapshot contract.
- Existing `VideoProject.policy_snapshot_id` remains unchanged when a new snapshot is compiled/activated.
- Snapshot payload immutability preserved.

## Tests run

PASS.

- `PYTHONPATH=. .venv/bin/python -m compileall app`
- `PYTHONPATH=. .venv/bin/pytest -q tests/qualification/test_m12_2p_channel_init_contract.py`
- `PYTHONPATH=. .venv/bin/pytest -q tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_1_prompt_registry.py tests/qualification/test_m12_2r_publish_handoff_ledger.py`
- `PYTHONPATH=. .venv/bin/pytest -q tests -k "m12_2p or channel_contract or channel_init or policy_snapshot or first_video or prompt_registry or upload_task"`
- `cd frontend && npm run typecheck`
- `cd frontend && npm run lint`
- `cd frontend && npm run test`
- `cd frontend && npm run build`
- `git diff --check`

## Old smoke rule status

PASS.

- Did not run old M12 real-smoke/provider-smoke tests.
- Did not call media providers.
- Did not enable Veo, ElevenLabs, Creatomate, Google Drive upload, or YouTube upload.
- Did not add mock fallback.
- Did not create dry-run success.

## Scope explicitly not built

- No real video generation.
- No TTS generation.
- No Veo generation.
- No Creatomate render.
- No Google Drive upload.
- No YouTube upload/publish/reupload.
- No YouTube Studio scraping/browser automation.
- No analytics learning activation.
- No prompt mutation.
- No provider budget usage tracking.

## Risks/limitations

- Legacy template-only profile creation can still compile a backward-compatible contract from template/channel defaults; explicit M12.2P contract fields do not get US/en defaults when missing.
- Channel edit/profile UI now displays policy state, but full edit workflow can be expanded in a later repair if operator editing needs parity with creation.

## Next suggested milestone

M12.3 Real Voice + Media Plan Activation.
