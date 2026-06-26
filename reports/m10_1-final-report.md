# M10.1 Final Report

## Verdict

PASS

## Repo path

`/Users/sangss/Desktop/video-creator-rag`

## Preflight status

PASS

- Working tree sạch trước khi mở M10.1: PASS.
- Tag `m10-learning-review-queue` tồn tại: PASS.
- Tag `m9-post-publish-diagnostics` tồn tại: PASS.
- Đã đọc M10 final report trước khi implement.
- Không commit/tag sau build.

## Migration status

PASS

- Added Alembic revision: `0012_m10_1_router_derivatives`.
- `.venv/bin/vcos db migrate`: PASS.
- Re-run `.venv/bin/vcos db migrate`: PASS, idempotent.

## Config seed status

PASS

- Added 16 M10.1 catalogs.
- `.venv/bin/vcos config seed`: PASS, 109 catalogs.
- Re-run `.venv/bin/vcos config seed`: PASS, idempotent.

## Test status

PASS

- `tests/test_config_registry.py`: 4 passed.
- `tests/qualification/test_m10_1_llm_router_derivatives.py`: 5 passed.
- `tests/qualification/test_m10_learning_review_queue.py`: 8 passed.
- `tests/test_migration.py`: 2 passed.
- `tests/qualification`: 74 passed.
- Full pytest: 189 passed.
- Warning còn lại: Starlette/httpx TestClient deprecation hiện hữu.

## Ollama real smoke status

SKIPPED

- `VCOS_LLM_ROUTER_REAL_SMOKE=false` mặc định.
- Không gọi Ollama/network trong full test.
- Real smoke sẽ chạy local Ollama only khi bật rõ `VCOS_LLM_ROUTER_REAL_SMOKE=true` và `VCOS_LLM_REAL_EXECUTION_ENABLED=true`.

## Ollama provider behavior

- Real execution mặc định tắt bằng `VCOS_LLM_REAL_EXECUTION_ENABLED=false`.
- Khi tắt: router ghi attempt `SKIPPED`, không gọi provider/network.
- Khi bật: gọi Ollama `/api/chat` non-streaming, route theo lane, fallback theo thứ tự lane, ghi `ProviderAttempt`, `LLMRunSnapshot`, `llm_route_attempts`.
- Không auto-pull model, không gọi provider ngoài Ollama, không tự bịa dollar cost.

## Implemented scope

- Real guarded Ollama `OllamaLLMProvider`.
- `LLMRouterConfigLoader`, `LLMRouterService`.
- Router profiles/lanes/model profiles/route attempts.
- Derivative graph, ShortCandidate extraction/ranking, originality/reuse governance.
- Cross-platform funnel package, upload card, human upload task contracts.
- Short winner to long-form opportunity candidate.

## Schema added

- `llm_router_profiles`, `llm_router_lanes`, `llm_model_profiles`, `llm_route_attempts`
- `content_derivative_graph_edges`, `short_candidates`, `short_candidate_scores`, `short_render_plans`
- `promote_short_to_long_candidates`, `reusable_artifacts`, `asset_reuse_index_entries`
- `derivative_originality_checks`, `originality_budgets`, `derivative_release_plans`
- `cross_platform_funnel_packages`, `upload_cards`, `human_upload_tasks`, `usage_savings_ledger_entries`

## Services/API added

- Services: LLM router, Ollama provider, short extraction/ranking, derivative graph, originality, reusable artifacts, asset reuse search, funnel package, upload card, human upload task, promote short to long.
- API: `/llm-router/*`, short candidate extract/list/get/rank, derivative graph reads/create edge, originality checks, reusable artifacts, asset reuse search, funnel packages, upload cards, human upload tasks, promote-short-to-long candidates.

## LLMRouter lanes

- `cheap_structured`: `gpt-oss:20b-cloud` -> `qwen3.5:cloud`
- `default_multimodal`: `qwen3.5:cloud` -> `gemma4:31b-cloud`
- `visual_creative_review`: `minimax-m3:cloud` -> `qwen3.5:cloud` -> emergency `gemma4:31b-cloud`
- `long_context_text`: `deepseek-v4-flash:cloud` -> `nemotron-3-super:cloud` -> premium `deepseek-v4-flash:cloud`
- `engineering_architect`: `qwen3-coder:480b-cloud` -> `kimi-k2.7-code:cloud` -> backup `deepseek-v4-flash:cloud`
- `gatekeeper_soft_review`: `nemotron-3-super:cloud` -> `deepseek-v4-flash:cloud` -> premium `deepseek-v4-flash:cloud`

## No GLM verification

- Không seed GLM model.
- Không có `experimental_quarantine` lane.
- Router chặn model name chứa `glm` bằng reason/error `GLM_MODEL_FORBIDDEN`.

## Agent-to-router mapping

- Agent chỉ map tới lane; model cụ thể nằm ở lane-role env vars.
- Metadata/classification/summary nhỏ: `cheap_structured`.
- Script/research/long-context synthesis: `long_context_text`.
- Visual/thumbnail/creative review: `visual_creative_review`.
- Policy/risk/finality soft review: `gatekeeper_soft_review`.
- Internal engineering/dev reasoning: `engineering_architect`.

## ShortCandidate extraction/scoring

- Extraction deterministic/rule-based từ long-form artifacts.
- Target 20-45s, hard cap dưới 59s.
- Candidate giữ hook/core idea/standalone summary/caption refs.
- Scoring deterministic, phạt context dependency/policy risk/generic template, không ép đủ 3-5 shorts nếu không đủ giá trị.

## Derivative graph

- Model parent long-form -> short derivative edge.
- Model short winner -> follow-up long opportunity candidate.
- Không auto-create `VideoProject` từ opportunity.
- Raw compilation không được publish-allowed nếu thiếu new value.

## Reuse/originality governance

- `DerivativeOriginalityCheck` kiểm standalone value, new value, reused runtime, policy/rights flags.
- `ReusableArtifact` giữ license/rights envelope/reuse scope/cooldown/reuse count.
- Reuse không đồng nghĩa quyền dùng vô hạn.
- Envato/manual stock không được coi là automated API provider.

## CrossPlatformFunnelPackage

- YouTube-first funnel package.
- TikTok/Facebook chỉ là export/support surface.
- Không có external post API, không có analytics learning loop ngoài YouTube.

## UploadCard / HumanUploadTask

- `UploadCard` chuẩn bị title/caption/hashtags/CTA/disclosure/music policy/manual notes.
- `HumanUploadTask` là manual-only workflow.
- Upload/publish thực tế vẫn do người vận hành làm và paste-back qua flow hiện có.

## YouTube-only analytics authority

- YouTube analytics vẫn là learning authority.
- TikTok/Facebook analytics loop deferred/out of scope.

## Invariants verified

- No excluded model usage.
- No `experimental_quarantine` lane.
- No global strongest-model default.
- Business service optional LLM path routes by lane.
- No dashboard UI.
- No auto publish/upload/reupload.
- No M10.2 media provider routing/integration.
- No TikTok/Facebook analytics learning loop.
- `UploadedVideo` remains canonical published video record.

## Scope explicitly not built

- No dashboard UI.
- No approval/reject dashboard actions.
- No ElevenLabs/Creatomate/AI Hero/cloud final renderer integration.
- No auto publish/upload/reupload.
- No channel config mutation or config upgrade suggestion.
- No approved playbook promotion.
- No scraping/RAG/vector/OPA/Cedar.
- No Algorithm/Growth/View agents.
- No fake traffic/bot engagement/platform evasion.

## Deferred

- M10.2: Media Provider Role Matrix, media provider routing, ElevenLabs, Creatomate, AI Hero, cloud final renderer.
- M11: dashboard/operator cockpit, approvals, upload task dashboard, derivative graph dashboard, learning promotion UX, channel config editing.

## Risks / limitations

- Real Ollama smoke was not run because env flag is disabled.
- Short extraction is deterministic/rule-based foundation, not final creative selection UX.
- Cross-platform package creates manual upload tasks only; actual paste-back still relies on existing/manual `UploadedVideo` flow.

## Next suggested milestone

M10.2 Media Provider Role Matrix / Quality-First Render Routing Patch.
