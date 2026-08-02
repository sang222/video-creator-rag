# VCOS stale-workflow recovery, real-provider activation, and MR1 resume

Final status at 2026-08-02T16:23:34Z: stale-workflow recovery and real-provider
wiring are complete, and the human OAuth boundary has been resolved.  The
scheduled task was then cancelled at the operator's request.  No cadence,
replay, clock, SQL, provider execution, or final-video action was forced
manually.

## Durable recovery evidence

- Target workflow: `1f5103cd-d2f1-4ebb-a9a4-eff4ce441dc9`.
- The ordinary outbox event
  `3eedd395-6a36-56e9-a0cb-3bf853b38bd6`
  (`production.workflow.stale_recovery.requested`) was created at
  `2026-08-02T15:59:48.227902Z` and delivered by the worker at
  `2026-08-02T16:07:37.052814Z`.
- Immutable recovery receipt:
  `c6ec6762-2b75-46d5-a251-80f1d574d332`, decision
  `AUTO_SUPERSEDE_STALE_PRE_REPAIR_WORKFLOW`, created by `SYSTEM_WORKER`.
- The stale run is now `SUPERSEDED`; its dead-letter job
  `9a5a4fb0-eab1-41cf-8c15-dee9028e89dc` is `DISCARDED` and not retryable; its
  related incident `8fdc47e5-89d2-4ea7-b727-d43419a12c5b` is `RESOLVED`.
- The receipt's zero-effect proof records: no effective runtime context,
  package, readiness, render, archive, final-review, cloud-media or
  final-media state; `0` V2 effect rows, effect invocations, provider
  attempts, budget reservations, and budget settlements.

## Real-provider authority

- The default V2 registry now contains the durable ElevenLabs narration
  executor and the remote Google Drive archive executor.  Both use explicit
  idempotency evidence; Drive requires persisted channel OAuth, an exact root,
  and checksum readback before archive lineage can be recorded.
- Worker runtime configuration now enables real provider execution and removes
  the media-call kill-switch through its authoritative
  `VCOS_DISABLE_MEDIA_PROVIDER_CALLS` alias.  The container confirms that the
  media kill-switch is `false`; no secret values are recorded here.
- The production-start readiness gate performs no provider probe.  It still
  requires a connected, channel-scoped Google Drive OAuth credential and root
  authority.  That credential is absent, so a natural evaluation must stop
  with `WAIT_PROVIDER_AUTHORITY` before any production start or paid call.

## Natural cadence evidence so far

- The `2026-08-02T16:00:48.549279Z` cadence receipt
  `0cb7d6a3-213b-4dfc-bf59-f593cdb54b44` is
  `WAIT_POLICY_OR_RIGHTS_BLOCKED` because it was evaluated before the stale
  incident was resolved at `16:07:37Z`.
- The worker remains running and will enqueue the next normal hourly
  evaluation itself.  This report must be updated from that receipt only; no
  manual evaluation or recovery may be substituted.

## Verification completed

- Database migration head/current: `0054_vcos_stale_recovery`.
- Python: focused durable orchestration, cadence, V2 gateway/authority, Drive,
  lint, format, and diff checks passed.
- Frontend: typecheck, lint, 45 Vitest assertions, and a production Next build
  passed.  The generated `next-env.d.ts` triple-slash exemption is scoped to
  that generated file only.
- API health reports database `ok`; neither OpenAI canary history nor a final
  media/publish decision was changed.

## OAuth completion and terminal boundary

- At `2026-08-02T16:23:34Z`, the channel-scoped Drive credential is
  `CONNECTED`, bound to a root folder, and has exactly the
  `https://www.googleapis.com/auth/drive.file` scope.  The connection-status
  endpoint also records `GOOGLE_DRIVE_TOKEN_EXCHANGED`.
- No production workflow was created after recovery.  No provider execution,
  reservation, archive object, final-media reference, review candidate, or
  publish action was created.
- The scheduled cadence task was cancelled by the operator, so no automatic
  start will follow this closeout.  Any later production attempt requires a
  new explicit instruction and must still proceed only through normal
  cadence/outbox authority.
