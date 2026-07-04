# R3D9-UX2 Packaging Review Queue + Patch Approval Cockpit

Ngày: 2026-07-04

## Kết luận

Đã implement R3D9-UX2 như patch P1 post-freeze/operator-blocking. Manual Pilot 001 cho thấy operator phải đọc raw gate/debug để biết sửa gì; patch này chuyển gate issue thành hàng chờ duyệt với proposed patch.

Không thêm generic fix agent. Không auto-apply. Không provider/media/upload/YouTube call.

## Files changed

- `alembic/versions/0032_r3d9_ux2_packaging_review_queue.py`
- `app/db/models/r3d9_ux2.py`
- `app/db/models/__init__.py`
- `app/contracts/r3d9.py`
- `app/contracts/m12_2.py`
- `app/contracts/__init__.py`
- `app/services/r3d9_ux2.py`
- `app/services/m12_2.py`
- `app/services/m12_2r.py`
- `app/services/m11.py`
- `app/services/__init__.py`
- `app/main.py`
- `frontend/src/lib/types.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/features/publishing/package-review-view.tsx`
- `frontend/src/features/publishing/__tests__/package-review-view.test.tsx`
- `frontend/src/components/approval-card.tsx`
- `tests/conftest.py`
- `tests/test_r3d9_ux2_packaging_review_queue.py`
- `tests/test_migration.py`
- `tests/test_dx1_semantic_code_convention.py`
- `tests/test_r3d9_runtime_dashboard_ops.py`
- `tests/qualification/test_m12_2r_publish_handoff_ledger.py`
- `reports/r3d9_ux2_packaging_review_queue_report.md`

Ghi chú: worktree có vài file dirty sẵn ngoài phạm vi R3D9-UX2; không revert/commit.

## Models/tables added

- `packaging_review_queue_items`
- `packaging_proposed_patches`
- `packaging_patch_approval_decisions`
- `packaging_patch_apply_runs`
- `packaging_gate_rerun_records`

Migration head: `0032_r3d9_ux2_review_queue`.

## Queue flow summary

`PackagingReviewQueueService` build queue từ R3D4 gate runs và M1 packaging gate summary.

Flow:

Gate issue -> `PackagingReviewQueueItem` -> route/proposal -> `PackagingProposedPatch` -> approve/reject/request changes -> apply versioned artifact/handoff patch -> targeted gate rerun record -> update queue state.

Dedupe key: `package_id + gate_key + issue_code + target_artifact_ref`.

Khi latest gate PASS sau rerun, item unresolved được close với `GATE_PASS_AFTER_RERUN`.

## Patch routing summary

- `HOOK_PROMISE_MISSING` -> `ScriptRewriteAgent` / `ScriptPlanningAgent`, `HOOK_SPEC`
- `TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM` -> `PublishingMetadataAgent`, `METADATA`
- `THUMBNAIL_BRIEF_MISSING` -> `ThumbnailBriefAgent`, `THUMBNAIL_BRIEF`
- `SUBTITLE_REFS_MISSING` -> deterministic subtitle handoff, `SUBTITLE_HANDOFF`
- `PUBLISH_WINDOW_MISSING` -> deterministic manual publish timing override, `PUBLISH_TIMING_OVERRIDE`
- `DISCLOSURE_CONFLICT` -> `RightsDisclosureReviewer+UploadCardCopyAgent`, `DISCLOSURE_COPY`
- `UNSUPPORTED_CTA` / `FAKE_CHECKLIST` / `FAKE_DEMO` -> `UploadCardCopyAgent`, `UPLOAD_COPY`

Nếu LLM patch proposal chưa bật/an toàn, service trả `NEEDS_PROPOSED_PATCH`; không mock fallback.

## Approval/apply behavior

- Approve/reject/request changes đều ghi `PackagingPatchApprovalDecision`.
- Reject/request changes không apply.
- Apply chỉ chạy khi patch đã `APPROVED`.
- Apply tạo `Artifact` + `ArtifactVersion` mới cho handoff patch.
- Không mutate artifact cũ in-place.
- Không mutate `EffectiveChannelRuntimeContextSnapshot`.
- Không mutate Channel Contract / `ChannelProfileVersion`.
- Apply tạo `PackagingPatchApplyRun` và `PackagingGateRerunRecord`.

## UI behavior

Package review cockpit đã có:

- Review Verdict card: `READY_FOR_MANUAL_UPLOAD`, `REVIEW_REQUIRED`, `BLOCKED`, `WAITING_PROVIDER_CONFIG`
- Must Fix Before Upload panel với title/why/fix/section/gate/status/proposed patch summary.
- Approve / Reject / Request changes chỉ hiện khi patch `READY_FOR_REVIEW`.
- Raw gate table nằm trong section collapsed `Chi tiết kỹ thuật`.
- Queue panel qua M11 approval queue hiển thị `packaging_review` với package/gate/issue/patch status/next action.

Không thêm editor text/DB-field editor trong dashboard.

## Upload button gating

- `BLOCKED` -> disable, label `Đang bị block`
- `REVIEW_REQUIRED` -> disable, label `Còn mục cần review`
- `WAITING_PROVIDER_CONFIG` -> disable, label `Chờ cấu hình provider`
- all required gates pass/queue closed -> enable `Tạo task upload thủ công`

Không thêm override button mới.

## API endpoints

- `GET /video-packages/{package_id}/packaging-review-queue`
- `POST /video-packages/{package_id}/packaging-review-queue/build-from-gates`
- `POST /packaging-proposed-patches/{patch_id}/approve`
- `POST /packaging-proposed-patches/{patch_id}/reject`
- `POST /packaging-proposed-patches/{patch_id}/request-changes`
- `POST /packaging-proposed-patches/{patch_id}/apply`
- `POST /video-packages/{package_id}/rerun-packaging-gates`

Write endpoints là human action/audit endpoints; không chạy provider/media/upload.

## Tests run/result

- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d9_ux2_packaging_review_queue.py` -> 10 passed
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_migration.py` -> 2 passed
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d9_runtime_dashboard_ops.py` -> 2 passed
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d10_runtime_lts_freeze.py` -> 8 passed
- M1/M2 subset -> 68 passed
- R3D1-R3D8 subset -> 104 passed
- DX1/DX2 subset -> 13 passed
- M12.2/M12.2R/M12.2S subset -> 35 passed
- `PYTHONPATH=. .venv/bin/python -m compileall -q app` -> passed
- `cd frontend && npm run test -- package-review-view.test.tsx` -> 5 passed
- `cd frontend && npm run lint && npm run test` -> 25 passed
- `cd frontend && npm run typecheck` -> passed
- `cd frontend && npm run build` -> passed
- `PYTHONPATH=. .venv/bin/alembic heads` -> `0032_r3d9_ux2_review_queue (head)`
- `git diff --check` -> passed

## Proof no provider/media/upload/YouTube calls

- `PackagingPatchProposalService` chỉ tạo deterministic patch hoặc route metadata; không gọi provider.
- `PackagingPatchApplyService` tạo artifact/version audit, không gọi render/upload service.
- Test assert count `ProviderAttempt`, `MediaRenderJob`, `FinalMediaRef`, `UploadedVideo` đều bằng 0 sau approve/apply.
- Test scan `app/services/r3d9_ux2.py` không có `GoogleDriveUploadService` / `YouTubeUpload`.
- Frontend test assert không có provider/render/upload/YouTube execution button mới.

## P0/P1/P2/P3 classification

P1 post-freeze operator-blocking patch.

Lý do: manual upload/review flow trước patch buộc operator đọc raw gate/debug detail để biết việc cần làm. Sau patch, gate failure được chuyển thành queue item + proposed patch approval flow.

## ProductionPainLog

Không thêm entry mới. Đây là P1 đã được xử lý bằng patch core behavior theo post-freeze protocol.
