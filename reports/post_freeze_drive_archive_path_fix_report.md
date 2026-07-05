# Post-Freeze Drive Archive Path Fix Report

Date: 2026-07-04

## Verdict

PASS cho patch Drive archive path normalization.

## Files changed

- `.gitignore`
- `app/services/m10_5.py`
- `docs/architecture/provider_stack_freeze.md`
- `tests/test_m10_5_drive_archive_path_builder.py`

## Old path behavior

Drive archive/smoke path cũ tự thêm root logic nội bộ:

`VCOS/company_unknown/channel_unknown/project_unknown/misc`

Vì `GOOGLE_DRIVE_ROOT_FOLDER_ID` đã trỏ tới folder root cấu hình, behavior này tạo nested folder sai dưới root, ví dụ:

`VCOS Media / VCOS / company_unknown / channel_unknown / project_unknown`

## New path behavior

`GOOGLE_DRIVE_ROOT_FOLDER_ID` được coi là archive root. App chỉ gửi folder path tương đối dưới root.

- `PROJECT_SCOPED`: `company_{company_id}/channel_{channel_workspace_id}/project_{video_project_id}/{media_type_or_subfolder}`
- `UPLOADED_VIDEO_SCOPED`: `company_{company_id}/channel_{channel_workspace_id}/uploaded_video_{uploaded_video_id}/{media_type_or_subfolder}`
- `SMOKE_TEST_UNSCOPED`: `smoke_tests/YYYY-MM-DD`

New writes không tạo `VCOS`, `VCOS Media`, `company_unknown`, `channel_unknown`, hoặc `project_unknown`.

## Implementation notes

- Added `DriveArchivePathBuilder` and `DriveArchivePath` in `app/services/m10_5.py`.
- `GoogleDriveUploadService.upload_verified()` now records `folder_path` and `folder_path_mode` in `CloudMediaRef.technical_appendix`.
- Kept `_default_drive_folder_path()` as compatibility wrapper, now using the new builder.
- Added docs note: old `VCOS/company_unknown/channel_unknown/project_unknown` folders are historical and may be manually cleaned; VCOS does not move/delete old Drive folders automatically.
- Added `.gitignore` entry for `var/tmp/` to avoid committing smoke artifacts.

## Tests run

- `PYTHONPATH=. .venv/bin/python -m compileall -q app` — PASS
- `PYTHONPATH=. .venv/bin/pytest tests/test_m10_5_drive_archive_path_builder.py -q` — PASS, 4 passed
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m10_5_google_drive_offload.py::test_m10_5_offload_success_cleans_only_after_verified tests/qualification/test_m10_5_google_drive_offload.py::test_m10_5_failed_upload_preserves_local_file tests/qualification/test_m10_5_google_drive_offload.py::test_m10_5_keep_local_and_protected_roots_skip_cleanup -q` — PASS, 3 passed
- `PYTHONPATH=. .venv/bin/alembic heads` — `0031_r3d8_cost_firewall (head)`
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q` — PASS, 8 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_dx2_provider_stack_reconciliation.py -q` — PASS, 7 passed
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q` — PASS, 2 passed

## Runtime smoke evidence

Drive archive smoke after Docker sync:

- MediaOffloadJob: `33450d42-3512-4746-96c1-4cfde93c5bc4`
- CloudMediaRef: `9e036f9d-5f0a-4cd6-954f-324c10df278f`
- job_state: `VERIFIED`
- upload_status: `VERIFIED`
- verification_status: `CHECKSUM_UNAVAILABLE`
- folder_path: `smoke_tests/2026-07-04`
- folder_path_mode: `SMOKE_TEST_UNSCOPED`
- render_package_id: null
- video_project_id: null

## Confirmation

- No old Drive files/folders moved or deleted.
- No DB migration added.
- No ElevenLabs/Luma/Creatomate/Pexels execution added.
- No YouTube upload/publish added.
- No provider/media render job added.
- Google Drive use remained archive/storage only.
