# R3D9-UX4 Safe Apply Approved Changes & Recheck Package Report

Date: 2026-07-06

Package: `81c48d7a-dfc3-4207-b585-744673491b59`

## Summary

Implemented R3D9-UX4 as a P1 post-freeze/operator-blocking patch.

Production operator can now use a safe UI/API action:

`Apply approved changes & recheck package`

The action applies only human-approved patches, records versioned artifacts through the existing patch apply service, creates deterministic gate rerun records only after at least one patch is applied, rebuilds the R3D9-UX2 queue, and returns a structured no-execution proof.

No live patch was approved. No live patch was applied. No live gate rerun was created.

## Files Changed

- `app/contracts/r3d9.py`
- `app/contracts/__init__.py`
- `app/services/r3d9_ux2.py`
- `app/services/__init__.py`
- `app/api/routes/imports.py`
- `app/api/routes/package_review.py`
- `frontend/src/lib/types.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/features/publishing/package-review-view.tsx`
- `frontend/src/features/publishing/__tests__/package-review-view.test.tsx`
- `tests/test_r3d9_ux2_packaging_review_queue.py`
- `reports/r3d9_ux4_safe_apply_approved_changes_report.md`

## Endpoint And Service Added

Backend service:
- `PackagingApprovedPatchApplyAndRecheckService`

Endpoint:
- `POST /video-packages/{package_id}/apply-approved-changes-and-recheck`

Auth:
- Uses existing dashboard auth via `AuthService.current_user`.
- With dashboard auth enabled, unauthenticated requests stop before workflow execution.
- Read-only users are blocked.

Structured statuses:
- `APPLIED_AND_RECHECKED`
- `BLOCKED_WAITING_HUMAN_APPROVAL`
- `BLOCKED_PENDING_HUMAN_DECISIONS`
- `APPLY_FAILED`
- `NOOP_ALREADY_APPLIED`

Safety behavior:
- Applies only patch rows with `status=APPROVED` and latest human decision `APPROVE`.
- Blocks if any current actionable patch remains `READY_FOR_REVIEW`.
- Skips `REJECTED`, `REQUEST_CHANGES`, `SUPERSEDED`, and already `APPLIED`.
- Duplicate click does not create duplicate `ArtifactVersion` or duplicate gate rerun record.
- Existing direct patch apply endpoint still blocks non-approved patches.

## Read-Model And Button Behavior

Added queue fields:
- `approved_patch_count`
- `ready_for_review_patch_count`
- `rejected_patch_count`
- `request_changes_patch_count`
- `applied_patch_count`
- `can_apply_approved_changes`
- `apply_approved_changes_label`
- `apply_approved_changes_disabled_reason`
- `last_apply_recheck_result`

UI behavior:
- No approved patch: disabled, `Chưa có patch được duyệt`.
- Ready-for-review patch remains: disabled, `Còn patch chưa quyết định`.
- Approved patches exist and all current patches have decisions: enabled, `Apply approved changes & recheck package`.
- Loading: `Đang apply và kiểm tra lại...`.
- Result summary shows applied count, gate recheck count, remaining blockers, manual task allowed state, and next safe action.

Raw technical gate table remains collapsed by default.

## Live Package State Before Human Approval

Read-only validation for package `81c48d7a-dfc3-4207-b585-744673491b59`:

- `package_status=BLOCKED`
- `review_verdict=BLOCKED`
- `must_fix_count=9`
- `approved_patch_count=0`
- `ready_for_review_patch_count=9`
- `rejected_patch_count=0`
- `request_changes_patch_count=0`
- `applied_patch_count=0`
- `can_apply_approved_changes=false`
- `apply_approved_changes_label=Chưa có patch được duyệt`

Backend workflow result before approvals:
- `status=BLOCKED_WAITING_HUMAN_APPROVAL`
- `applied_patch_ids=0`
- `gate_rerun_record_ids=0`

Dashboard auth is enabled in live settings, so unauthenticated API calls are rejected before workflow execution. The endpoint calls the same service path validated above after auth.

## Proof No Live Approval/Apply/Rerun

Live package counts after validation:
- `PackagingPatchApprovalDecision`: 0
- `PackagingPatchApplyRun`: 0
- `PackagingGateRerunRecord`: 0
- `HumanUploadTask`: 0

No R3D9-UX4 task approved any of the 9 live patches.
No R3D9-UX4 task applied any of the 9 live patches.
No gate rerun occurred for the live package.

## No-Execution Proof

Live package/project counts:
- `ProviderAttempt` total remains 144 historical Ollama attempts.
- `MediaRenderJob` for project: 0
- `FinalMediaRef` for project: 0
- `CloudMediaRef` for project: 0
- `UploadedVideo` for project: 0
- `HumanUploadTask` for package: 0

Explicitly not executed:
- ElevenLabs
- Luma
- Pexels
- Drive upload
- YouTube upload/publish/reupload
- real video/media generation
- provider/media render job
- paid provider execution

No mutation occurred to:
- Channel Contract
- `ChannelProfileVersion`
- `EffectiveChannelRuntimeContextSnapshot`

No learning auto-promotion. No prompt self-mutation. No upload task creation.

## Tests Run

Passed:
- `PYTHONPATH=. .venv/bin/alembic heads` -> `0033_p1_pre_lts_disposition (head)`
- `PYTHONPATH=. .venv/bin/python -m compileall -q app`
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` -> 13 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q` -> 2 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_ux2_packaging_review_queue.py -q` -> 24 passed
- `cd frontend && npm run test -- package-review-view.test.tsx` -> 10 passed
- `cd frontend && npm run typecheck` -> passed
- `cd frontend && npm run lint` -> passed
- `cd frontend && npm run test` -> 30 passed
- `cd frontend && npm run build` -> passed
- `git diff --check` -> passed

## P0/P1/P2/P3 Classification

- P0: none.
- P1: R3D9-UX4 safe apply approved changes and recheck workflow.
- P2: none.
- P3: none.

Reason: production manual package operation was still blocked on a Codex/ChatGPT prompt for apply + deterministic gate rerun after human approval. This patch moves that safe internal review step into UI/API while preserving human approval and no-execution boundaries.

## Operator Workflow After This Patch

1. Open package review cockpit.
2. Review proposed patches.
3. Approve / Reject / Request changes.
4. When all patches have decisions and at least one is approved, click `Apply approved changes & recheck package`.
5. VCOS applies approved patches, reruns relevant deterministic gate records, and rebuilds the queue.
6. If gates pass and queue closes, manual handoff can proceed.
7. If still blocked, operator reviews remaining queue items.

Acceptance result:
- Production operation no longer requires ChatGPT/Codex prompt for apply + gate rerun.
- Human approval boundary is preserved.
- No provider/media/upload/YouTube execution.
- Package remains safe before human approval.
- Commit/tag only happen by explicit operator request; the UX4 workflow itself does not create git commits/tags.

## Post-Approval Hotfix Addendum: No Final Video, No Upload Task

After the operator later approved/applied the 9 proposed patches and reran gates, the live package reached deterministic review pass but still had no final video media asset. That state must not be treated as upload-ready.

Additional behavior now enforced:
- Review/gate pass without `LONG_FORM_FINAL` media returns `WAITING_FINAL_MEDIA_ASSET`.
- `upload_task_creation_allowed=false` unless a project has a `FinalMediaRef` with `media_type=LONG_FORM_FINAL` and a real `file_ref`, or a verified `CloudMediaRef` with `media_type=LONG_FORM_FINAL`.
- `POST /video-packages/{package_id}/upload-task` remains blocked when there is no uploadable final media.
- The package review summary cards prefer the packaging review queue/read-model, so stale handoff/package status does not show "blocked/review required" after a successful recheck.

Live package `81c48d7a-dfc3-4207-b585-744673491b59` after Docker sync:
- `review_verdict=WAITING_FINAL_MEDIA_ASSET`
- `must_fix_count=0`
- `upload_task_creation_allowed=false`
- `applied_patch_count=9`
- `has_uploadable_final_media=false`
- `FinalMediaRef` for project: 0
- `CloudMediaRef` for project: 0
- `HumanUploadTask` for package: 0

Additional tests run:
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_2r_publish_handoff_ledger.py -q` -> 15 passed
- `cd frontend && npm run test` -> 35 passed
- `cd frontend && npm run typecheck` -> passed after `next build` regenerated `.next/types`
- `cd frontend && npm run lint` -> passed
- `cd frontend && npm run build` -> passed

Docker sync:
- `docker compose up -d --build api frontend` completed.
- API and frontend containers healthy.
