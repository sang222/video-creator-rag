# YouTube Private Delivery Closeout

This change introduces a governed, private-only YouTube staging lane.

## Boundary

`PRIVATE_STAGE_VERIFIED` is not publication. It does not create canonical `UploadedVideo`, start analytics windows, advance series publication, or feed post-publish learning. Those actions require an immutable `PublicPublicationReceipt` created only after observed and verified public release.

## Delivery flow

`FINAL_REVIEW_READY -> human UPLOAD decision -> PRIVATE stage -> resumable upload -> metadata/thumbnail/caption read-back -> processing verification -> WAITING_HUMAN_RELEASE`.

Public release remains a human action in YouTube Studio. API scheduling, API public release, delete/unpublish, and automatic publication are intentionally unsupported.

## Reliability

Upload, thumbnail, caption, Telegram notification, and local purge effects use durable identities and fail closed on uncertain provider outcomes. A verified YouTube private asset may replace Drive as the final-MP4 review host; Drive remains only a legacy/internal byte-source fallback.

## Series

Series playlist and episode bindings are explicit. Standalone projects are `NOT_APPLICABLE`. Private staging never increments published episode counts; playlist ordering must use a future public ordinal authority rather than technical attempt numbering.
