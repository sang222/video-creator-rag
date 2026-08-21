# YouTube Private Delivery Closeout

This change introduces a governed, private-only YouTube staging lane.

## Boundary

`PRIVATE_STAGE_VERIFIED` is not publication. It does not create canonical `UploadedVideo`, start analytics windows, advance series publication, or feed post-publish learning. Those actions require an immutable `PublicPublicationReceipt` created only after observed and verified public release.

## Delivery flow

The legacy lane remains readable as `FINAL_REVIEW_READY -> human UPLOAD decision -> PRIVATE stage`.
The active lane is `QC PASS -> candidate-bound PRIVATE stage -> resumable upload -> PRIVATE_VERIFIED -> human Studio review -> read-only PUBLIC observation -> PUBLICATION_VERIFIED` and does not require `FinalVideoDecision=UPLOAD`.

Public release remains a human action in YouTube Studio. API scheduling, API public release, delete/unpublish, and automatic publication are intentionally unsupported.

## Reliability

Upload, thumbnail, caption, Telegram notification, and local purge effects use durable identities and fail closed on uncertain provider outcomes. A verified YouTube private asset may replace Drive as the final-MP4 review host; Drive remains only a legacy/internal byte-source fallback.

## Series

Series playlist and episode bindings are explicit. Standalone projects are `NOT_APPLICABLE`. Private staging never increments published episode counts; playlist ordering must use a future public ordinal authority rather than technical attempt numbering.

## PR #3 code-only closeout audit

The code-only boundary is closed when CI proves all of the following:

- one Alembic head at `0091_youtube_publication_v2`;
- complete upload results cannot exist without a platform video ID;
- every resumable-session query and byte upload is authenticated;
- stage, credential, thumbnail, media, metadata, and release-expectation hashes are recomputed before effects;
- concurrent upload/component workers cannot emit a second provider effect;
- public publication requires frozen metadata/channel/public visibility readback; thumbnail and caption remain explicit local/provider assurance and never become an exact remote-byte claim;
- series playlist authority cannot cross company/channel scope and public ordinals cannot bind before verified publication;
- local media paths and local secret paths cannot escape their configured roots;
- local purge survives crashes between quarantine, unlink, and receipt persistence;
- Telegram credentials are company/channel scoped and ambiguous bindings fail closed;
- no API path exists for automatic public release or delete.

Live OAuth, provider, Telegram, filesystem, and human-public-release evidence remains a local operator task. Execute it with `docs/operations/youtube_private_delivery_local_codex_master_prompt.md`; never weaken the human publication boundary to make a canary pass.
