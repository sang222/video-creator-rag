# M10.5 Final Report - Google Drive Media Offload / Cloud Archive

## Verdict

PASS.

Repo: `/Users/sangss/Desktop/video-creator-rag`

## Preflight

- Working tree trước khi mở M10.5: clean.
- Tag `m10-4-google-vertex-veo-binding`: có.
- M10.4 report: có `reports/m10_4-final-report.md`.
- Source-of-truth đã đọc: README, architecture ledger, source-of-truth, M10.2/M10.3/M10.4 docs/reports, migrations, config, services, API/CLI/tests hiện hữu.

## Migration / Config / Tests

- Alembic head: `0015_m10_5_drive_offload`.
- `vcos db migrate`: PASS, idempotent.
- `vcos config seed`: PASS, `146` catalogs.
- Targeted M10.5 tests: PASS, `9 passed, 1 skipped`.
- Full pytest: PASS, `213 passed, 4 skipped`.
- Real Google Drive smoke: SKIPPED by default. Guard requires `GOOGLE_DRIVE_OFFLOAD_ENABLED=true`, `VCOS_DRIVE_REAL_UPLOAD_SMOKE=true`, valid local OAuth token, and `GOOGLE_DRIVE_ROOT_FOLDER_ID`.

## Implemented Scope

- Google Drive media storage provider foundation.
- OAuth/session/credential-reference support for Drive upload.
- `CloudMediaRef` read contract for M11 CTA-only media cards.
- `MediaOffloadJob` upload/verify/cleanup lifecycle.
- Local retention policy and cleanup safety rails.
- API/CLI for Drive status, offload job, cloud ref read, media lists, cleanup.
- M6/M7/M10.2/M10.4 integration points without changing provider routing or Veo binding.

## Schema Added

- `cloud_media_refs`
- `media_offload_jobs`
- `local_media_retention_policies`
- `google_drive_media_credentials`
- `google_drive_oauth_sessions`

Schema extensions:

- `final_media_refs.cloud_media_ref_id`
- `long_form_render_packages.cloud_media_refs`
- `short_render_packages.cloud_media_refs`
- `publish_handoff_packages.cloud_media_refs`

## Services / API / CLI

Services:

- `GoogleDriveConfigService`
- `GoogleDriveOAuthCredentialService`
- `GoogleDriveOAuthSessionService`
- `GoogleDriveMediaStorageProvider`
- `GoogleDriveUploadService`
- `GoogleDriveUploadVerifier`
- `CloudMediaRefService`
- `MediaOffloadJobService`
- `LocalMediaRetentionPolicyService`
- `LocalMediaCleanupService`
- `MediaCloudReadService`
- `MediaOffloadReadService`

API:

- `GET /auth/google-drive/start`
- `GET /auth/google-drive/callback`
- `GET /google-drive/connection-status`
- `POST /media/offload-jobs`
- `POST /media/offload-jobs/{job_id}/execute`
- `GET /media/offload-jobs/{job_id}`
- `GET /media/cloud-refs/{cloud_media_ref_id}`
- `GET /video-projects/{video_project_id}/media`
- `GET /render-packages/{render_package_id}/media`
- `GET /uploaded-videos/{uploaded_video_id}/media`
- `POST /media/local-cleanup/run`
- `GET /media/local-retention-policy`

CLI:

- `vcos drive connection-status`
- `vcos drive offload --path ... --media-type ...`
- `vcos drive offload-job --job-id ...`
- `vcos drive cloud-ref --id ...`
- `vcos media cleanup-local`

## Google Drive Auth / Config

- Scope mặc định và bắt buộc: `https://www.googleapis.com/auth/drive.file`.
- Broad `https://www.googleapis.com/auth/drive` bị chặn.
- OAuth state chỉ lưu hash.
- Authorization code không persist sau callback.
- Access/refresh token không lưu plain DB fields.
- Local dev token file nằm dưới ignored path `var/credentials/google-drive/oauth/` với permission an toàn.

## Upload Verification / Cleanup

- Cloud ref chỉ tạo sau khi Drive upload có file id, `web_view_link`, size verified, và checksum verified hoặc checksum unavailable.
- Drive checksum SHA256 không khả dụng thì trạng thái `CHECKSUM_UNAVAILABLE`, không fail nếu size verified.
- Local cleanup chỉ chạy sau verified upload.
- Không xóa local file khi upload fail, verify fail, thiếu Drive id, `keep_local=true`, path ngoài allowed roots, hoặc path protected.

## Dashboard CTA-Only Contract

- Payload trả `web_view_link` làm CTA duy nhất.
- Không có backend download endpoint.
- Không có backend preview endpoint.
- Không stream Drive qua backend.
- Không expose local absolute path trong media read payload.
- Không dùng `web_content_link` làm dashboard contract.

## Invariants Verified

- M10.3 YouTube follow không đổi.
- M10.4 Veo provider binding không đổi.
- M10.2 provider routing không đổi.
- No dashboard UI.
- No YouTube upload/publish/reupload.
- No Studio scraping/browser automation.
- No fake traffic/bot engagement/platform evasion.
- No RAG/vector/OPA/Cedar.
- No real Creatomate/ElevenLabs/final renderer calls.

## Risks / Limitations

- Real Drive upload smoke chưa chạy vì mặc định disabled và thiếu explicit OAuth/root-folder guard trong môi trường test.
- Folder creation/upload uses Drive API only when real guard/config/token are present.
- Local cleanup endpoint cannot clean old pending refs without a runtime local path; job execution can clean immediately while path is available.

## Next Suggested Milestone

M11 Dashboard / Operator Cockpit: media cards dùng “Open in Google Drive” CTA, storage status, upload/download human workflow qua Drive link.
