# M12.2R Publish Handoff Ledger + Uploaded Video Backfill Report

## Verdict

PASS.

## Repo path

`/Users/sangss/Desktop/video-creator-rag`

## Preflight status

PASS.

- Repo path đúng.
- Working tree sạch trước khi mở M12.2R.
- Source reports đã đọc:
  - `reports/m12_2_first_scripted_video_package_report.md`
  - `reports/m12_1-final-report.md`
  - `reports/m12_1r_mock_dryrun_purge_report.md`
  - `reports/m12-final-report.md`
  - `reports/m11_1-final-report.md`
- Không thiếu report source-of-truth.

## Tags verified

PASS.

- `m12-1-prompt-registry-contracts`
- `m12-1r-mock-dryrun-purge`
- `m12-2-first-scripted-video-package`

## Schema/migration status

PASS.

- Added Alembic revision `0021_m12_2r_handoff_ledger`.
- Added `UploadedVideoBackfillEvent`.
- Extended existing `HumanUploadTask`.
- Extended existing `UploadedVideo`; không tạo bảng UploadedVideo trùng.
- Updated migration/test head to `0021_m12_2r_handoff_ledger`.

## API added

PASS.

- `GET /channels/{channel_id}/upload-tasks`
- `POST /video-packages/{package_id}/upload-task`
- `POST /upload-tasks/{task_id}/start`
- `POST /upload-tasks/{task_id}/backfill-uploaded-video`
- `GET /channels/{channel_id}/uploaded-videos`
- `GET /uploaded-videos/{uploaded_video_id}`
- `POST /uploaded-videos/{uploaded_video_id}/verify`
- `GET /channels/{channel_id}/publish-ledger`

No upload/publish/reupload API added.

## CLI added

PASS.

- `vcos upload-tasks list --channel-id <id>`
- `vcos upload-tasks create --package-id <id>`
- `vcos upload-tasks start --task-id <id>`
- `vcos upload-tasks backfill --task-id <id> --youtube-url-or-id <value> --visibility public`
- `vcos uploaded-videos list --channel-id <id>`
- `vcos uploaded-videos verify --uploaded-video-id <id>`

No upload/publish command added.

## Dashboard changes

PASS.

- Channel list now shows upload ledger counts.
- Channel detail now exposes manual upload workflow.
- Uploaded videos page now shows YouTube ID/URL, visibility, verification, analytics sync.
- Copy uses `upload thủ công` and `VCOS chỉ ghi nhận và xác minh`.

## Channel list upload visibility

PASS.

Added visible counts:

- Cần upload
- Chờ nhập video_id
- Đã upload
- Chờ xác minh YouTube

## Channel detail upload tabs

PASS.

Added tabs:

- `Cần upload`
- `Đã upload`
- `Nhập kết quả upload`

Operator can see task status, package/project refs, checklist/assets summary, and actions to start/backfill manual upload.

## Backfill form behavior

PASS.

- Form title: `Nhập kết quả upload thủ công`.
- Accepts YouTube watch URL, youtu.be URL, Shorts URL, or raw video_id.
- Rejects invalid video_id.
- Detects duplicate video_id within the same channel.
- Preserves original URL/input.
- Shows parsed video_id, status, and next action.

## UploadedVideo behavior

PASS.

- Backfill creates/updates `UploadedVideo`.
- Links channel, project, first scripted package, publish package, and human upload task where available.
- Records actual title, visibility, publish/upload time, playlist, thumbnail/subtitle flags, description diff flag, operator note.
- Does not overwrite package snapshot metadata.
- API responses avoid local paths and Drive proxy preview/download fields.

## Verification behavior

PASS.

- Missing YouTube credentials returns:
  - `verification_status = VERIFICATION_UNAVAILABLE`
  - `analytics_sync_status = NOT_CONFIGURED`
  - next action: connect YouTube to verify.
- Verification path is read-only public/owner check when configured.
- No fake metrics.
- No unavailable analytics converted to zero.

## Audit/events

PASS.

Added/recorded:

- `HUMAN_UPLOAD_TASK_CREATED`
- `HUMAN_UPLOAD_STARTED`
- `YOUTUBE_VIDEO_ID_PARSED`
- `UPLOADED_VIDEO_BACKFILLED`
- `UPLOADED_VIDEO_DUPLICATE_BLOCKED`
- `UPLOADED_VIDEO_VERIFICATION_SKIPPED`
- `UPLOADED_VIDEO_VERIFIED`
- `UPLOADED_VIDEO_VERIFICATION_FAILED`

Reason codes include human-upload-only and no-upload-API policy states.

## Tests run

PASS.

- `PYTHONPATH=. .venv/bin/python -m compileall app`
- `PYTHONPATH=. .venv/bin/pytest -q tests/qualification/test_m12_2r_publish_handoff_ledger.py tests/qualification/test_m12_2_first_scripted_video_package.py`
- `PYTHONPATH=. .venv/bin/pytest -q tests -k "m12_2r or upload_task or uploaded_video or publish_ledger or first_video or video_package"`
- `PYTHONPATH=. .venv/bin/pytest -q tests/qualification/test_m12_1_prompt_registry.py`
- `git diff --check`
- `cd frontend && npm run typecheck`
- `cd frontend && npm run lint`
- `cd frontend && npm run test`
- `cd frontend && npm run build`

## Old smoke rule status

PASS.

- Did not run old M12 real-smoke/provider-smoke tests.
- Did not call media providers.
- Did not enable Veo, ElevenLabs, Creatomate, Google Drive upload, or YouTube upload.
- Did not add mock fallback.
- Did not create dry-run success.

## Scope explicitly not built

- No YouTube upload API.
- No auto upload.
- No auto publish.
- No reupload.
- No YouTube scheduling.
- No YouTube Studio scraping/browser automation.
- No Drive proxy preview/download.
- No ChannelProfileVersion mutation.
- No auto-promote learning.

## Risks/limitations

- Real YouTube verification remains unavailable until public API key or owner OAuth is configured.
- Analytics sync readiness is recorded only; no metrics are invented.
- Legacy M7/M8/M9 UploadedVideo fields remain for backward compatibility.

## Next suggested milestone

M12.3 Real Voice + Media Plan Activation.
