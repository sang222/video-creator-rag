# VCOS post-window cadence and real-provider plan check

## Audit scope and time

- Checked at `2026-08-02T15:23:18Z` (`2026-08-02T22:23:18+0700`).
- The real start window is open: `2026-08-02T14:00:00Z` through
  `2026-08-03T14:00:00Z`; no synthetic time or cadence bypass was used.
- Runtime after the local repair: `api` is healthy and
  `production-workflow-worker` is running from freshly rebuilt Compose images.
  PostgreSQL and the frontend are healthy. API OpenAPI generation succeeds
  against the rebuilt API (`VCOS`, `0.1.0`, 364 paths).
- No provider call, manual production start, manual replay, direct database
  insertion, upload, final-video decision, commit, tag, or push was performed.

## Durable cadence evidence

The pre-window record remains intact:

| Receipt | Evaluated at | Decision | Input / decision hashes |
| --- | --- | --- | --- |
| `59f3a87b-7eed-4b94-b939-f993b6deae64` | `2026-08-02T12:00:02.108133Z` | `WAIT_BUDGET_BLOCKED` | `b4148d…455ce` / `d3efd2…29da9f` |

`cadence_evaluation_receipts` has an immutable update/delete trigger and the
above record was only read.

The authoritative launch run `009178fc-2ee5-46f3-8fb2-38403e4693fa` has two
post-window receipts:

| Receipt | Evaluated at | Window key | Decision / reason | Slot / candidate |
| --- | --- | --- | --- | --- |
| `05a7a122-9c10-4edd-ae14-8e148e31235d` | `2026-08-02T14:00:52.982183Z` | `46ff6f27…:2026-08-02T10:00:00-04:00` | `START_LONG_FORM_PRODUCTION`: `BUFFER_BELOW_TARGET`, `STRICT_PREFLIGHT_CANDIDATE_SELECTED`, `LONG_FORM_SLOT_ELIGIBLE` | `698497f6-4c16-4687-b328-aa78440038af` / `4bbacf2b-1781-4ce2-91d7-3914fb25e687` |
| `553bfe54-75a2-43b4-b4e6-515efb1e9d25` | `2026-08-02T15:00:07.615824Z` | `46ff6f27…:2026-08-02T11:00:00-04:00` | `WAIT_ACTIVE_PRODUCTION`: `MAX_CONCURRENT_PRODUCTIONS_REACHED` | Not selected, because the existing workflow occupies the sole lane |

The start receipt is tied to the active policy version
`46ff6f27-357b-4093-bffe-2aab6ec5a473`, the required slot/candidate, project
`9a38dea5-2181-491e-88f8-1bca2505723a`, and workflow
`1f5103cd-d2f1-4ebb-a9a4-eff4ce441dc9`. Its frozen start authority was
`READY`: channel budget `$20.00`, `$1.00` per-video cap and `$20.00`
remaining; ElevenLabs was `READY_FOR_EXECUTION_AUTHORIZATION` and Google Drive
was `READY_FOR_FUTURE_EXECUTION`. That receipt reports no network call.

## Exactly-once evidence

For the active launch run and selected candidate:

| Counter | Value |
| --- | ---: |
| ProjectAdmissionDecision v2 | 1 |
| VideoProject | 1 |
| ProductionWorkflowRun | 1 |
| MR1 budget reservations for that project | 0 |
| V2 provider-effect rows for that project | 0 |
| Duplicate start effects | 0 |

Admission `629303d6-8f08-4ea7-a08b-856021cc33fa` is `ADMIT`,
`content_mode=STANDALONE`, `assignment_mode=OPEN_MIX`; it has no series run.
Database uniqueness constrains cadence input and launch-run/window identity,
the v2 candidate/long-form source admission, the workflow key, and a V2 effect
per workflow stage. These constraints plus the count above support the
exactly-once result across worker restart/re-delivery.

## Failure attribution and current boundary

The started historical workflow is `DEAD_LETTERED` at `RESEARCH` with
`STAGE_RETRY_EXHAUSTED` and `AUTO_RETRY_WITHIN_POLICY`. It has no effective
runtime-context snapshot, no package/readiness artifact, no final-review
candidate, no provider effect, no media, and no archive. Dead-letter
`9a5a4fb0-eab1-41cf-8c15-dee9028e89dc` is `REPLAYABLE`, retry eligible, with
five failures; its durable safe-resume text is: `Retry under ops.manage after
evidence review.` The associated open incident is the reason the subsequent
natural scan truthfully returned `WAIT_ACTIVE_PRODUCTION`.

This is not a provider effect failure and was not replayed here. The active
blocker is therefore `VALID_POLICY_BLOCK` (`MAX_CONCURRENT_PRODUCTIONS_REACHED`)
with a **human operations boundary** for the pre-repair RESEARCH dead letter.
The first failed stage, retry state, and safe resume are durable in the existing
workflow/dead-letter/incident records.

There are currently zero channel-scoped `google_drive_media_credentials` for
the admitted project's company/workspace. This is a prospective
`VALID_EXTERNAL_CREDENTIAL_BLOCK` for a future real Drive archive; it is not
being misreported as an archive result for the workflow that never reached
MEDIA.

## Local repairs

The repair keeps qualification and real production separate rather than
relabeling a local result:

1. Cadence now compiles and requires an effective channel runtime context for
   a new project and binds a fresh NICH1 governance digest for the actual
   publish-editorial slot. Failure rolls back the newly created workflow/event
   transaction.
2. Support authority has explicit `QUALIFICATION_LOCAL` and
   `REAL_LONG_FORM_PRODUCTION` modes. Real mode requires a workflow-bound
   MR1 budget reservation, carries its actual reservation evidence, and checks
   immutable replay before any new reservation.
3. A real package resolves only `v2-elevenlabs-narration` for final narration
   and `v2-google-drive-remote` for final archive. It freezes the approved
   ElevenLabs voice/model, credential reference (without exposing a secret),
   one attempt, idempotency key, estimated cost, package/readiness hash, and
   the real reservation reference. Its visual plan is `NATIVE_FFMPEG`.
4. The production gateway forbids qualification MEDIA/ARCHIVE and local native
   adapters for real final narration/archive. Missing ElevenLabs credentials
   block with `V2_REAL_ELEVENLABS_BLOCKED_CREDENTIAL`; no local-OS-TTS fallback
   exists. Real final review also requires real mode.
5. The media role catalog now identifies Google Drive archive as real
   `MEDIA_STORAGE` authority, while retaining the local archive only for
   qualification.

The current default gateway deliberately has no concrete, live
`v2-elevenlabs-narration` or `v2-google-drive-remote` executor registered.
Consequently a future real run will fail closed at the adapter boundary instead
of silently using local narration or a local archive. No claim is made that a
real external narration/archive has occurred. A credentialed remote executor
and channel-scoped Drive OAuth authority must exist before a future workflow
can pass those stages.

No intermediate `ReviewTask` or approval was created. The only normal human
decision after all real media/QC/archive evidence remains
`FinalReviewCandidate -> UPLOAD | DO_NOT_UPLOAD`; neither option was chosen.

## Verification and repair cycle

- Rebuilt and restarted only `api` and `production-workflow-worker`; API health
  checks pass and worker startup is clean.
- `python -m compileall -q app tests alembic/versions`: pass.
- `ruff format --check` for changed source/tests: pass (9 files).
- `ruff check` for changed source/tests: pass.
- `git diff --check`: pass.
- `docker compose config --quiet`: pass.
- `alembic heads` and live `alembic current`: single
  `0053_ops_incident_constraint (head)`.
- Live OpenAPI JSON generation: pass.
- Targeted regression suite:
  `35 passed, 2 warnings in 33.84s`. The warnings are unrelated Pydantic
  deprecation notices in editorial research.

One source-repair/rebuild cycle was completed. The changed implementation and
test files are the six service modules, the media provider role catalog, and
three targeted test modules shown by `git status`; unrelated pre-existing
untracked reports were preserved.

## Exact next durable action

Do not manually start or replay the workflow. The supported next action is an
authorized `ops.manage` evidence review of dead-letter
`9a5a4fb0-eab1-41cf-8c15-dee9028e89dc`, followed only by its documented retry
if that operator approves it. Before any future real workflow reaches MEDIA,
the channel must also have an authorized Drive OAuth credential and a concrete
remote ElevenLabs/Drive executor; otherwise the new code fails closed without
provider calls or a final-media claim.
