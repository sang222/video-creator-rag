# M1 — Channel-Aware Packaging Performance Handoff

## Kết quả
- Đã thêm handoff layer cho review/upload thủ công: hook 3 giây đầu, upload copy, thumbnail brief, publish timing theo channel context và gate packaging.
- Không thêm upload/publish automation, provider call, YouTube API, browser automation, vector/RAG/memory retrieval.
- Không cần migration: M1 dùng read-model/serializer từ artifact/package/task/snapshot hiện có.

## Files changed
- Backend contracts/services: `app/contracts/m1.py`, `app/services/m1.py`, `app/contracts/__init__.py`, `app/services/__init__.py`.
- API/review integration: `app/main.py`, `app/contracts/m12_2.py`, `app/services/m12_2.py`, `app/services/m12_2r.py`.
- Frontend UI/API/types: `frontend/src/features/publishing/package-review-view.tsx`, `frontend/src/app/video-packages/[packageId]/review/page.tsx`, `frontend/src/features/channels/channel-workspace-view.tsx`, `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/components/friendly-status-badge.tsx`.
- Tests: `tests/test_m1_channel_aware_packaging_handoff.py`, `frontend/src/features/publishing/__tests__/package-review-view.test.tsx`.

## Backend read models / schemas
- `HookSpecRead`: canonical hook extraction/read model, không duplicate artifact nếu dữ liệu đã có.
- `UploadHandoffCopyRead`: title, description, hashtags, subtitle refs, disclosure notes, checklist, locale/language, contract hash.
- `ThumbnailHandoffRead`: concept, overlay, subject, composition, mobile notes, refs/branch/variant plan.
- `PublishTimingRecommendationRead`: timezone, configured window, suggested time, manual-only policy ref.
- `PackagingGateResultRead`, `PackagingGateSummaryRead`, `PackagingHandoffSnapshotRead`.

## API
- `GET /video-packages/{package_id}/review` nay trả thêm `packaging_handoff`.
- Thêm alias tối thiểu: `GET /video-packages/{package_id}/packaging-handoff`.
- `HumanUploadTask` snapshot nay giữ `packaging_handoff` ref/data để operator tiếp tục upload/backfill thủ công.

## Packaging gates
- Đã expose/implement lightweight deterministic gates:
  `HookTruthfulnessGate`, `HookPayoffGate`, `VisualHookRelevanceGate`, `TitlePromiseGate`, `MetadataTruthfulnessGate`, `CaptionCoverageGate`, `DescriptionCompletenessGate`, `ThumbnailTruthfulnessGate`, `MobileThumbnailLegibilityGate`, `CharacterThumbnailConsistencyGate`, `PublishTimingComplianceGate`, `ManualPublishOnlyGate`.
- Gate trả `PASS / REVIEW_REQUIRED / BLOCK`, reason codes, checked artifact refs, checked contract paths và next action tiếng Việt.

## ChannelRuntimeContext
- Publish timing chỉ đọc `EffectiveChannelRuntimeContextSnapshot.publish_timing_context_json` đã snapshot.
- Không đọc latest mutable channel settings để tránh lệch runtime source of truth.
- Operator local time được render từ timezone local cấu hình, chỉ là recommendation.

## Manual-only boundary
- UI chỉ tạo `HumanUploadTask` thủ công và hiển thị paste-back `video_id`.
- `ManualPublishOnlyGate` block dấu hiệu automation như auto publish/schedule/upload API/reupload.
- Không có YouTube upload API, không provider/media call, không Drive upload.

## Frontend
- Thêm page `/video-packages/[packageId]/review`.
- Panels: Hook Review, Upload Handoff Copy, Thumbnail Handoff, Publish Timing, Packaging Gate Summary.
- Có copy button cho title/description/checklist; không thêm daily generation, NoView, vector run buttons.

## Tests run
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_m1_channel_aware_packaging_handoff.py` → 11 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d1_hierarchical_scope.py tests/test_r3d2_effective_channel_runtime_context.py tests/qualification/test_r3d3_agent_context_pack.py tests/qualification/test_r3d4_agent_output_contract_gates.py` → 58 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py tests/qualification/test_m12_2r_publish_handoff_ledger.py` → 35 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_migration.py tests/qualification/test_pre_m7_migrations.py` → 4 passed.
- `PYTHONPATH=. .venv/bin/python -m compileall -q app tests/test_m1_channel_aware_packaging_handoff.py` → pass.
- `cd frontend && npm run typecheck` → pass.
- `cd frontend && npm run lint` → pass.
- `cd frontend && npm test` → 20 passed.
- `git diff --check` → pass.

## Follow-up M2
- Dùng M1 handoff/provider summary để hiển thị readiness blockers.
- Implement provider config/readiness/preflight/request builders ở wiring-only mode.
- Giữ empty API keys pass với blocker rõ ràng; không gọi network/provider và không fake success.
