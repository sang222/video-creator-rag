# M12.2P3 Research-Assisted Minimal Channel Init

## Verdict

PASS.

## Repo path

`/Users/sangss/Desktop/video-creator-rag`

## Preflight status

PASS.

- Repo path đúng.
- Working tree sạch trước khi bắt đầu lại.
- Expected tags có đủ, gồm M12.1, M12.1R, M12.2, M12.2R, M12.2P, M12.2P-R, M12.2S0.
- Đã đọc source reports/docs yêu cầu.
- Đã đọc Deep Research PDF và lưu spec repo tại `docs/m12_2p3_research_assisted_minimal_channel_init_spec.md`.
- Không commit/tag.

## Models/migrations added

- Added `channel_init_drafts`.
- Added `channel_contract_drafts`.
- Added contracts:
  - `MinimalAdminInput`
  - `FieldMeta`
  - `ChannelInitDraftRead`
  - `ChannelContractDraftRead`
  - review/compile payloads.
- Migration: `alembic/versions/0023_m12_2p3_channel_init_drafts.py`.

## API added

- `POST /channel-init-drafts`
- `GET /channel-init-drafts/{draft_id}`
- `POST /channel-init-drafts/{draft_id}/research`
- `POST /channel-init-drafts/{draft_id}/review`
- `POST /channel-init-drafts/{draft_id}/compile`
- `GET /channel-init-drafts/{draft_id}/contract-preview`
- Existing `POST /channels/{channel_id}/activate` kept and now marks related init draft `ACTIVATED` after successful explicit activation.

## Research agent added

- Added deterministic `ChannelSetupResearchAgentService`.
- Research is draft-only.
- No provider/media calls.
- No YouTube Studio scraping.
- No activation/config mutation.
- No ChannelProfileVersion or snapshot is created by research.

## Evidence collectors added

- `ChannelResearchEvidenceCollector`.
- Supports admin note, YouTube public anchor, website anchor, public social/profile anchors.
- Optional web snippet flag exists but remains local/no external provider by default.

## Prompt registry integration

- Added `ChannelSetupResearchAgent`.
- Added system delta.
- Added schema `channel_setup_research_draft.v1`.
- Added registry manifest entry with setup-class contract and `no_runtime_mutation_guarantee`.

## Human review boundary

- Review supports confirm/edit/reject/mark_unknown/add_note.
- Confirm/edit flips field source to `HUMAN_CONFIRMED`.
- Review decision log records field path, previous/new value, previous/new source type, timestamp, reviewer if available, and note.
- Locked system/provider fields reject human override.

## Compiler behavior

- Compile creates `ChannelProfileVersion` and `CompiledChannelPolicySnapshot`.
- Default path does not call catalog template compiler.
- `source_template_key` is `None`; profile input records `m12_2p3_no_catalog_template_used=true`.
- COMPLETE requires strategic fields to be admin/human/locked truth.
- PARTIAL snapshots can be created for preview but cannot activate.
- Hard locks force no auto publish, no Studio scraping, no dashboard scraping, no config mutation, no auto learning promotion.

## Frontend wizard changes

- Minimal research-assisted wizard is default.
- Step 1 minimal setup.
- Step 2 research result with evidence/confidence/missing fields.
- Step 3 human review with field source badges.
- Step 4 compile status/coverage/missing fields.
- Step 5 activate only after COMPLETE.
- Required safety copy added.
- No upload/publish buttons.

## Advanced manual mode status

Existing heavy full form remains available as:

`Nâng cao: nhập thủ công toàn bộ hồ sơ`

## Field source map behavior

- `channel_contract_json` remains canonical plain values.
- `field_source_map_json` is parallel provenance.
- Compiler enforces leaf-path coverage for generated contracts.
- Snapshot payload stores both `channel_contract_json` and `field_source_map_json`.

## Small Team AI scenario

Covered in backend/frontend tests.

- Research suggests AI workflows / automation systems / operating dashboards.
- Content language may be suggested as `en`, still review-required.
- Primary market remains `UNKNOWN` until human edit/confirm.
- COMPLETE only after strategic fields are confirmed.

## Tests run

- `PYTHONPATH=. .venv/bin/python -m compileall app` PASS.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_m12_2p3_research_assisted_channel_init.py` PASS, 10 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/qualification/test_m12_1_prompt_registry.py` PASS, 5 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests -k "m12_2p3 or channel_init_draft or channel_setup_research or field_source_map or channel_contract"` PASS, 14 passed, 323 deselected, 1 warning.
- `cd frontend && npm test -- src/features/channels/__tests__/channel-init-wizard.test.tsx` PASS, 5 passed.
- `cd frontend && npm run typecheck` PASS.
- `cd frontend && npm run lint` PASS.
- `cd frontend && npm run test` PASS, 19 passed.
- `cd frontend && npm run build` PASS.
- `git diff --check` PASS.

## Old smoke/provider rule status

PASS.

- Did not run old provider smoke tests.
- Did not call real providers.
- Did not upload/publish/reupload.
- Did not add YouTube upload API.
- Did not use browser/dashboard automation.
- Did not add mock fallback or dry-run success.

## Scope explicitly not built

- No real YouTube Data API integration.
- No external web snippet connector.
- No per-channel provider budgets.
- No config upgrade suggestion engine.
- No auto-confirm.
- No auto-activation.
- No auto publish/upload/reupload.

## Risks/limitations

- Research collector is intentionally local/deterministic; public API/page adapters are policy-shaped stubs, not real external calls.
- Minimal review UI confirms required fields in batch; deeper per-field edit UX can be polished later.
- Advanced manual mode still uses the older full-form path.

## Next suggested milestone

Rerun M12.2S full agent rehearsal after operator creates/reviews/activates the real channel through M12.2P3.
