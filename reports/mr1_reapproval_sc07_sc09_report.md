# MR1 Re-approval SC-07/SC-09

Ngày: 2026-07-27

Fresh exact-target MR1 approval đã được tạo sau human PASS. MR1 chưa execute.

```text
approval_id=de4edc50-23df-5065-b652-69e5978825b4
approval_content_hash=57bbc5964a5ba63bb115ec8bc82ca2db43a2b37ee79066127238dfab28aa13b5
approval_receipt_artifact_version_id=e85366d4-fea4-5f4d-9d69-4241dc4102b3
approval_receipt_content_hash=003bf3487d27421efa36f4b31555bfb11a1bfbe8b1212e8ce0ef1e38cc5bb933
task_wide_authorization_id=41b9f1ac-9af0-5ae1-8a39-04cea2090913

SC-07 route=NATIVE_MOTION_GRAPHIC external_provider_attempts=0
SC-09 route=NATIVE_DIAGRAM external_provider_attempts=0
fallback=false
provider_substitution=false
production_render_authorized=true
publish_authorized=false
```

Reuse manifest `4ce06532-9b27-50a6-b0ae-4710cce1c053`, hash
`10b6448dc603d982fa883bf6cfb9e8b5a59eefc360d07c55aa59af6da696426b`:

- Narration và forced alignment: `REUSE_VALID` theo exact hash/checksum.
- Pexels SC-07, supplement và SC-09: `INVALIDATED_BY_REVISION`.
- Native SC-07/SC-09: `REQUIRES_LOCAL_COMPILATION_RENDER`.
- Final media: `MISSING`; Drive archive: `REQUIRES_NEW_EXECUTION`.

Approval cũ `f21fb49d-6695-45f1-be2c-231908f3eb93` và
`40193854-8633-45a5-97be-54b380a8c8e5` chỉ là historical authority, không được reuse.

```text
MR1_REAPPROVAL_FINAL=PASS
MR1_PROVIDER_CALL_COUNT=4
MR1_RENDER_CALL_COUNT=0
MR1_DRIVE_CALL_COUNT=0
MR1_YOUTUBE_CALL_COUNT=0
MR1_EXECUTION=NOT_STARTED
MR1_RENDER_STATUS=NOT_STARTED
MR1_HUMAN_REVIEW=PENDING
DESTINATION_STATUS=PENDING_PLATFORM_ID
UPLOAD_READY=false
PUBLISH_EXECUTION_READY=false
PROCEED_TO_MR1=true
```
