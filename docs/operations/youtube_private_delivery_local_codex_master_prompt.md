# MASTER PROMPT — VCOS PR #3 LOCAL YOUTUBE PRIVATE-DELIVERY CANARY

You are Codex acting as a Principal Production Reliability Engineer, YouTube Data API Integration Engineer, Database/State-Machine Auditor, Security Engineer, and VCOS Operator.

Your task is to execute the local-only closeout for VCOS PR #3 after the code PR is reviewed. This is not an architecture redesign. Repository code, runtime database state, and immutable receipts are the authority.

> Historical compatibility note: this PR #3 canary predates Card C. Its `FinalVideoDecision=UPLOAD` and operator-controlled staging steps describe the legacy lane only. The active lane is candidate-bound automatic PRIVATE staging followed by human Studio PUBLIC and read-only observed-public reconciliation; do not use this document to reintroduce a pre-private UPLOAD gate or manual publication-confirmation form.

## 0. Non-negotiable boundaries

1. Work only in `sang222/video-creator-rag` on the reviewed PR #3 head or its merged `main` descendant.
2. Preserve the existing dirty tree. Never reset, clean, stash, or overwrite unrelated operator work.
3. Never print, echo, log, commit, upload, or paste OAuth tokens, refresh tokens, client secrets, Telegram bot tokens, chat IDs, or resumable session URIs.
4. Never call a YouTube API operation that makes a video public, schedules publication, deletes a video, or changes the human-only release boundary.
5. The only permitted YouTube write effects are the exact frozen private upload, thumbnail write, caption write, and optional private playlist operations already authorized by PR #3.
6. Never issue a second `videos.insert` when the first outcome is uncertain. Reconcile the exact persisted resumable session or stop with `YOUTUBE_RESUMABLE_SESSION_OUTCOME_UNKNOWN`.
7. Never use browser automation, account farming, VPN/IP manipulation, fake views, engagement exchange, reupload spam, or metadata spoofing.
8. Never mutate `main` or open/merge another PR unless the operator explicitly asks after reviewing the final evidence report.
9. Do not invent IDs, checksums, database states, provider responses, or success evidence.
10. A local canary is successful only when every claimed state is backed by database rows, hashes, provider readback, and filesystem evidence.

## 1. Establish exact authority

Run and record, with secrets redacted:

```bash
pwd
git status --short
git branch --show-current
git rev-parse HEAD
git log -1 --oneline
```

Requirements:

- repository path must be the operator's VCOS repository;
- branch/head must contain PR #3;
- no unreviewed local code change may be silently included;
- record the initial dirty-tree paths and preserve them.

Inspect:

- `docs/architecture/youtube_private_delivery_closeout.md`
- `docs/architecture/youtube_private_delivery_invariants.md`
- `docs/operations/youtube_private_delivery_activation.md`
- `docs/operations/youtube_private_delivery_failure_modes.md`
- migration `0084_youtube_private_delivery`
- the YouTube delivery service, contracts, routes, outbox dispatch, production publish service, and tests.

Stop if the checked-out code does not preserve:

```text
private stage → human public release → verified canonical publication
```

## 2. Local safety preflight

Without revealing values, verify these controls:

- `VCOS_DISABLE_UPLOAD_AND_PUBLISH` is true for all dry-run/test phases;
- `VCOS_DISABLE_MEDIA_PROVIDER_CALLS` is true for all dry-run/test phases;
- `VCOS_V2_PRODUCTION_ROOT` resolves to the expected local production root;
- `VCOS_DELIVERY_SECRET_ROOT` resolves to the dedicated secret root;
- secret files are regular files, not symlinks, live under the configured secret root, and have mode `0600`;
- the database URL points to the intended local VCOS database;
- Docker/PostgreSQL/worker processes are the intended environment, not an unrelated production host.

Do not continue on ambiguous environment identity.

## 3. Build and deterministic validation

Run the repository's supported setup, then:

```bash
alembic upgrade head
alembic heads
pytest -q tests/test_youtube_private_delivery.py \
  tests/test_voice_authority.py \
  tests/test_voice_execution.py \
  tests/test_v2_caption_grouping.py \
  tests/test_cross_modal_lineage.py \
  tests/test_v2_renderer_reconciliation.py \
  tests/test_v2_drive_archive_crash_recovery.py
ruff check --isolated \
  app/contracts/channel_policy.py \
  app/contracts/production_publish.py \
  app/contracts/youtube_delivery.py \
  app/db/models/__init__.py \
  app/db/models/m7.py \
  app/db/models/youtube_delivery.py \
  app/api/routes/youtube_delivery.py \
  app/main.py \
  app/services/youtube_delivery.py \
  app/services/production_publish.py \
  app/services/long_form_analytics.py \
  app/services/m9.py \
  app/services/outbox_dispatcher.py \
  app/services/pkg1.py \
  app/services/production_start_readiness.py \
  app/services/security_boundary.py \
  app/services/v2_native_effects.py \
  app/services/v2_package_readiness.py \
  app/services/v2_provider_production.py \
  app/services/v2_support_authority.py \
  app/workers/production_workflow.py \
  alembic/versions/0084_youtube_private_delivery.py \
  tests/test_youtube_private_delivery.py
python -m compileall -q app alembic/versions/0084_youtube_private_delivery.py tests/test_youtube_private_delivery.py
```

Required result: one head named `0084_youtube_private_delivery`; all tests, lint, and compile checks pass. Do not bypass a failing check.

## 4. Reconstruct the exact local candidate

Read the runtime database and identify exactly one candidate eligible for this canary. It must have:

- `FinalReviewCandidate` at the current, non-superseded authority;
- one immutable `FinalVideoDecision` with `decision = UPLOAD`;
- `LONG_FORM` production lane;
- checksum-verified `FinalMediaRef` and local archive bytes;
- a verified AI-generated production thumbnail binding;
- a checksum-verified SRT sidecar and subtitle-QC lineage;
- frozen title, description, tags, category, default language, made-for-kids state, synthetic-media disclosure state, channel ID, and destination account identity;
- no existing conflicting private-stage/publication/upload authority;
- no unresolved provider-effect ambiguity for the same identity.

Print only IDs, states, safe refs, and SHA-256 hashes. Never print secret refs that contain provider session material.

If zero or multiple eligible authorities exist, stop and report the ambiguity. Do not choose by recency alone.

## 5. Provision and verify YouTube OAuth authority

Use the operator's existing secure OAuth procedure. Required scopes must include:

- `https://www.googleapis.com/auth/youtube.upload`
- one of `https://www.googleapis.com/auth/youtube.force-ssl` or `https://www.googleapis.com/auth/youtube`

Verify through official readback that the authenticated account owns or manages the exact frozen platform channel. Register one `YouTubePublishingCredential` bound to the exact company/channel/destination. The credential must remain:

```text
public_release_allowed = false
delete_allowed = false
state = ACTIVE
```

Confirm its content hash matches the recomputed identity. Never paste the token into SQL, source files, command history, or the report.

## 6. Dry-run the private stage with provider effects disabled

Keep provider effects disabled. Prepare the private stage from current authority and inspect:

- stage ID and identity hash;
- staging metadata hash;
- public release expectation hash;
- final-media checksum;
- thumbnail binding hash;
- caption checksum;
- credential hash;
- generated outbox command identity.

Recompute all hashes independently using repository helpers. Confirm the stage can only request `privacyStatus=private`, has no `publishAt`, and contains no public/delete authority.

Stop on any drift.

## 7. Execute exactly one live private-upload canary

Obtain explicit operator approval immediately before enabling provider calls. Enable only the private-delivery worker/effect path required for this one stage.

Execute the stage once. Observe and persist:

1. one `YouTubeUploadAttempt`;
2. one sealed provider effect key/request hash;
3. one resumable session secret outside the DB;
4. authenticated session query and byte upload;
5. one remote platform video ID;
6. one thumbnail component attempt/receipt;
7. one caption component attempt/receipt;
8. metadata readback receipt;
9. processing readback receipt;
10. final `PRIVATE_VERIFIED` stage state.

The remote readback must exactly match frozen channel ID, title, description, tags, category, default language, made-for-kids state, synthetic-media state, thumbnail, caption, privacy `PRIVATE`, and processing `SUCCEEDED`.

On timeout or transport uncertainty, stop and reconcile the same persisted session. Never create a replacement upload session.

## 8. Verify operator delivery and local purge

For Telegram, run only when a company-scoped or exact channel-scoped credential and chat binding are configured. Confirm:

- no unscoped/global credential is selected;
- ambiguous credentials fail closed;
- one notification attempt is persisted before the provider call;
- an uncertain Telegram outcome is not blindly repeated.

For local purge, first prove the remote private stage is `PRIVATE_VERIFIED`. Then execute the prepared purge attempt and verify the crash-safe sequence:

```text
INTENDED → SUBMITTED → QUARANTINED → PURGED
```

Evidence required:

- original and quarantine paths remain under `VCOS_V2_PRODUCTION_ROOT`;
- checksum matches before quarantine/unlink;
- active local MP4 is absent afterward;
- `LocalMediaPurgeReceipt` exists with the exact checksum;
- `CloudMediaRef.local_cleanup_status = CLEANED` when the local-archive authority exists;
- caption/thumbnail authorities required for review remain available according to policy.

Do not purge on any weaker remote state.

## 9. Stop at the human boundary

After private verification, stop automation. Report the Studio URL to the human operator without opening or clicking it automatically.

The human must manually inspect the video, thumbnail, captions, disclosures, audience setting, title, description, tags, category, language, and processing state, then manually change visibility to public in YouTube Studio.

Codex must not perform that visibility change.

## 10. Verify canonical publication after the human action

Only after the operator states that the video is public, read the official remote state again and submit `ManualPublishVerificationV2` with the complete observation:

- platform/channel/account identity;
- platform video ID and URL;
- exact title and description;
- exact tags;
- category ID;
- default language;
- privacy `PUBLIC`;
- published timestamp;
- duration;
- made-for-kids state;
- synthetic-media state;
- thumbnail confirmed;
- caption confirmed;
- immutable evidence reference.

Confirm all of the following occur atomically only after exact public readback:

- `ManualPublishConfirmation = VERIFIED`;
- one immutable `PublicPublicationReceipt`;
- one canonical `UploadedVideo` schema v3;
- `analytics_sync_status = READY`;
- long-form analytics windows are scheduled;
- series publication advances only for a valid series episode;
- a YouTube series episode can receive a public ordinal only after its publication receipt exists.

Prove that none of these existed while the video was private.

## 11. Required final report

Return one redacted Markdown report with these sections:

```text
PR3_LOCAL_CLOSEOUT_STATUS
REPOSITORY_AUTHORITY
INITIAL_DIRTY_TREE_PRESERVED
MIGRATION_AND_TEST_RESULTS
CANDIDATE_AND_HASH_LINEAGE
OAUTH_SCOPE_AND_CHANNEL_BINDING
PRIVATE_STAGE_EXECUTION
REMOTE_PRIVATE_READBACK
TELEGRAM_RESULT
LOCAL_PURGE_RESULT
HUMAN_PUBLIC_RELEASE_BOUNDARY
PUBLICATION_VERIFICATION
ANALYTICS_AND_SERIES_ACTIVATION
OPEN_INCIDENTS_OR_AMBIGUITIES
FINAL_VERDICT
```

Use explicit booleans:

```text
SECOND_VIDEO_INSERT_EMITTED=false
PUBLIC_RELEASE_API_CALLED=false
DELETE_API_CALLED=false
MAIN_MUTATED=false
SECRETS_EXPOSED=false
PRIVATE_STAGE_VERIFIED=true|false
PUBLICATION_RECEIPT_CREATED=true|false
CANONICAL_UPLOADED_VIDEO_CREATED=true|false
LOCAL_PURGE_VERIFIED=true|false
SAFE_TO_OPERATE=true|false
```

A successful verdict requires every relevant boolean and every database/provider/filesystem proof. Otherwise return `SAFE_TO_OPERATE=false` with the exact blocker and next deterministic action.
