# M10.5 Google Drive Media Offload

M10.5 adds Google Drive media offload and cloud archive contracts for generated heavy media.

## Scope

- VCOS DB remains source of truth.
- Google Drive is blob/archive storage only.
- `CloudMediaRef` stores Drive file id, folder id, `web_view_link`, size, checksum metadata, verification state, cleanup state, source refs, and technical appendix.
- `MediaOffloadJob` uploads a local generated file, verifies Drive metadata, creates a cloud ref only after verification, then optionally cleans local media.
- Local cleanup runs only after verified upload and only inside allowed cleanup roots.
- OAuth uses `https://www.googleapis.com/auth/drive.file` only.
- Tokens are stored through `CredentialReference` pointing to ignored local dev files under `var/credentials/google-drive/oauth/`.

## Non-Scope

- No dashboard UI.
- No backend file download proxy.
- No backend preview proxy.
- No backend streaming from Drive.
- No local absolute path in media read payloads.
- No Google Drive as DB/source of truth.
- No YouTube upload/publish, Studio scraping, or platform automation.
- No Veo provider changes.

## Drive Folder Policy

Default logical path under configured root folder:

```txt
VCOS/
  company_<id>/
    channel_<id>/
      project_<id>/
        long_form/
        shorts/
        thumbnails/
        captions/
        ai_hero/
        creatomate_assets/
        publish_package/
        qc/
        misc/
```

Real upload blocks when `GOOGLE_DRIVE_ROOT_FOLDER_ID` is missing.

## Read Contract

M11 should read media through DB-backed payloads only:

- `cloud_media_ref_id`
- `media_type`
- `file_name`
- `storage_provider: GOOGLE_DRIVE`
- `web_view_link`
- `upload_status`
- `verification_status`
- `local_cleanup_status`
- `size_bytes`
- `mime_type`
- `uploaded_at`
- `cleaned_at`
- `source_refs`
- `technical_appendix`

Dashboard copy is CTA-only:

- “File đã được lưu trên Google Drive.”
- “Mở Google Drive để xem hoặc download file.”
- “Không mở được file trên Google Drive. Cần kiểm tra quyền hoặc re-upload.”

No payload includes backend download URL, preview URL, token info, secret refs, or local absolute path.

## API

```bash
GET /auth/google-drive/start
GET /auth/google-drive/callback
GET /google-drive/connection-status
POST /media/offload-jobs
POST /media/offload-jobs/{job_id}/execute
GET /media/offload-jobs/{job_id}
GET /media/cloud-refs/{cloud_media_ref_id}
GET /video-projects/{video_project_id}/media
GET /render-packages/{render_package_id}/media
GET /uploaded-videos/{uploaded_video_id}/media
POST /media/local-cleanup/run
GET /media/local-retention-policy
```

There are no `/download` or `/preview` endpoints.

## CLI

```bash
vcos drive connection-status
vcos drive offload --path <local-file> --media-type AI_HERO --video-project-id <id>
vcos drive offload-job --job-id <id>
vcos drive cloud-ref --id <cloud-media-ref-id>
vcos media cleanup-local
```

CLI output is redacted and does not print local absolute paths by default.

## M11 Deferred

M11 owns the operator dashboard, media cards, Google Drive CTA UI, storage status UI, and human download workflow.
