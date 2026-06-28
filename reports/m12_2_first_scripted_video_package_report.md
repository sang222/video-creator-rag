# M12.2 First Scripted Video Package Report

## Verdict

PASS.

Đã thêm đường chạy Production Prompt Activation cho first scripted video package. Runtime vẫn real-provider-or-blocked: nếu LLM thật chưa bật/cấu hình thì package trả `NOT_CONFIGURED`, không dùng mock/dry-run/local fixture success.

## Repo path

`/Users/sangss/Desktop/video-creator-rag`

## Preflight status

- Repo path đúng: PASS.
- Working tree sạch trước khi mở M12.2: PASS.
- Source-of-truth reports đã đọc: M12.1, M12.1R, M12, M11.1.
- M12.1/M12.1R reports tồn tại: PASS.
- Không commit/tag sau build.

## Tags verified

- `m12-1-prompt-registry-contracts`: PASS.
- `m12-1r-mock-dryrun-purge`: PASS.

## Provider readiness status

- M12.2 package tạo `ProviderReadinessSnapshot` bằng `ProviderReadinessService.run()`.
- Không gọi `RealSmokeOrchestratorService`.
- Không chạy old provider smoke.
- Media providers vẫn disabled trong package path.

## LLM activation status

- Thêm flags:
  - `VCOS_ENABLE_PRODUCTION_PROMPT_ACTIVATION`
  - `VCOS_ENABLE_REAL_LLM_PACKAGE_RUN`
  - `VCOS_DISABLE_MEDIA_PROVIDER_CALLS`
  - `VCOS_DISABLE_UPLOAD_AND_PUBLISH`
  - `VCOS_DISABLE_OLD_PROVIDER_SMOKE`
- Nếu production activation false: `BLOCKED`.
- Nếu real LLM package run hoặc Ollama real execution chưa configured: `NOT_CONFIGURED`.
- Business service chỉ gọi router lane, không hardcode model.

## Package run status

- Model/table mới: `first_scripted_video_packages`.
- API:
  - `POST /video-packages/first-scripted`
  - `GET /video-packages/{package_id}`
  - `GET /video-packages/{package_id}/review`
- CLI:
  - `vcos package first-video --channel-id ... --topic ... --research-pack ... --no-media --human-review-only`
- Test path với fake LLM test double đạt `READY_FOR_HUMAN_REVIEW`.
- Real LLM không được gọi trong validation run này.

## Channel contract binding

Package bind đủ:

- `channel_contract_json`
- `compiled_policy_snapshot_json`
- `channel_profile_version_id`
- `compiled_policy_snapshot_id`

Nếu thiếu/incomplete: trả `REVIEW_REQUIRED/BLOCKED`, không gọi LLM.

## Agents activated

- ChannelAuthorityAgent
- TopicIdeaScoringAgent
- ResearchPackSummarizer
- ScriptPlanningAgent
- ScriptWriterAgent
- PublishingMetadataAgent
- VisualPlanningAgent
- UploadCardCopyAgent
- GatekeeperSoftReviewAgent

Gatekeeper chạy trước khi package được `READY_FOR_HUMAN_REVIEW`.

## Prompt snapshots created

- Mỗi agent lưu `PromptRenderRun`.
- Lưu `PromptAuditSnapshot`.
- Lưu `prompt_hash`, `prompt_context_hash`, rendered system/user messages, validation result, provider/router refs.

## Schemas validated

- Mọi agent output validate qua M12.1 BaseEnvelope/schema path.
- Invalid output dừng chain với `REVIEW_REQUIRED` hoặc `ERROR`.
- Không tiếp tục downstream khi upstream output không dùng được.

## Gatekeeper result

- `PASS` -> `READY_FOR_HUMAN_REVIEW`.
- `REVIEW_REQUIRED` -> `REVIEW_REQUIRED`.
- `BLOCK` -> `BLOCKED`.

## Human review package result

Package artifact chứa:

- channel contract snapshot ref
- admission decision
- topic scores
- research notes
- script outline
- narration script có sentence IDs
- metadata package
- visual plan
- gatekeeper review
- upload card copy
- human review checklist
- prompt render/audit refs
- provider readiness snapshot ref
- risk/limitations summary

Package dừng ở human review.

## Tests run

```bash
PYTHONPATH=. .venv/bin/python -m compileall app
PYTHONPATH=. .venv/bin/pytest -q tests/qualification/test_m12_2_first_scripted_video_package.py
PYTHONPATH=. .venv/bin/pytest -q tests -k "m12_2 or first_video or video_package or prompt_registry or m12_1 or mock_dryrun_purge"
git diff --check
```

Results:

- Compile: PASS.
- M12.2 focused: `9 passed, 1 warning`.
- Focused regression: `22 passed, 240 deselected, 1 warning`.
- `git diff --check`: PASS.

## Old smoke rule status

PASS.

- Không chạy old M12 real-smoke/provider-smoke tests.
- Không bật Veo/ElevenLabs/Creatomate/YouTube/Drive real smoke flags.
- Không gọi Veo, ElevenLabs, Creatomate, Google Drive upload, YouTube upload/publish.

## Scope explicitly not built

- No final video rendering.
- No TTS generation.
- No Veo generation.
- No Creatomate render.
- No Google Drive upload.
- No YouTube upload/publish/reupload/scheduling.
- No dashboard scraping/browser automation.
- No fake traffic/bot engagement/platform evasion.
- No prompt self-mutation.
- No ChannelProfileVersion mutation.
- No learning auto-promotion.
- No frontend page for M12.2.

## Risks / limitations

- Real Ollama package run was not executed in this turn; runtime will block with `NOT_CONFIGURED` until the real LLM flags/readiness are enabled.
- Existing M12.1 generated Channel Contract can be `PARTIAL` if operator locale metadata is missing; M12.2 blocks instead of guessing.
- Working tree currently also contains unrelated Docker/frontend changes outside the M12.2 package path; not reverted.

## Next suggested milestone

M12.3 Real Voice + Media Plan Activation.
