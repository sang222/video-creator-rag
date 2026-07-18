# Google Gemini Image Paid Canary Operations

This runbook controls one non-production Gemini Image request. It is not a
general provider activation path.

## Fixed scope

```text
provider=google_gemini_image
model=gemini-3.1-flash-image
size=2K
aspect_ratio=16:9
output_count=1
attempt_limit=1
hard_cap_usd=0.15
reference_images=[]
grounding=false
search_grounding=false
fallback=false
production_eligible=false
not_publishable=true
```

Current official model, image-generation and pricing references:

- https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-image
- https://ai.google.dev/gemini-api/docs/image-generation
- https://ai.google.dev/gemini-api/docs/pricing

The repository catalog estimate for one 2K image is USD 0.101. The dedicated
extra AI-image monthly budget takes precedence over a general AI budget.

## Preflight-only operation

Preflight creates a new timestamped identity, immutable request/approval,
shared monthly-budget capacity evidence, a zero-attempt durable ledger and closed
runtime gates. It never calls the image provider. Task authorization, credential
incident evidence and monthly budget authority live under ignored, mode-0600
`var/credentials/img-canary/` state and use `flock`, atomic replace and directory
`fsync`.

For this known incident only, record the still-configured exposed credential once.
The command persists its SHA-256 fingerprint, never the key:

```bash
cd /Users/sangss/Desktop/video-creator-rag
PYTHONPATH=. .venv/bin/python scripts/run_img_canary.py \
  --vqc1-final-pass \
  --open-scoped-execution-switches \
  --record-current-credential-compromised
```

```bash
cd /Users/sangss/Desktop/video-creator-rag
PYTHONPATH=. .venv/bin/python scripts/run_img_canary.py \
  --vqc1-final-pass \
  --open-scoped-execution-switches
```

Do not claim a credential safe merely because it is configured. After any log
or terminal exposure, rotate it first. The runner proves that the live fingerprint
differs from the incident record. Budget capacity is read from the dedicated
configured cap and a shared authority ledger; CLI spend/reservation assertions are
not accepted.

## Paid execution

Paid execution is allowed only after persisted preflight PASS. Supply the exact
runner token; do not invoke the adapter from an ad-hoc script.

```bash
PYTHONPATH=. .venv/bin/python scripts/run_img_canary.py \
  --vqc1-final-pass \
  --credential-rotation-ref rotation://img-canary/security-ticket/<safe-ticket-ref> \
  --open-scoped-execution-switches \
  --execute \
  --execution-token EXECUTE_EXACTLY_ONE_PAID_GEMINI_IMAGE_CANARY
```

Before submit, the CLI resolves the existing PA1R OAuth credential and performs a
sanitized read-only Drive-root probe. The task-wide authorization is then claimed
atomically and budget is reserved in the shared ledger. The runner persists a
runtime-submit preflight bound to the live `CLAIMED` task and `RESERVED` budget.
The adapter independently reopens the concrete canonical flocked attempt ledger,
rechecks live canonical task/budget/credential state and the catalog amount, then
consumes that ledger before the SDK call. Copied ledgers, alternate authority
paths and objects that merely claim a canonical path are rejected. The global
authorization becomes terminal after the attempt; a different run cannot submit
again. SDK retries are disabled. Any provider failure or unusable output consumes
the only attempt and blocks a second generation. Parsing, checksum,
normalization, native overlay, render and Drive verification may be retried
against the same response/files without a new request.

For the May-2026 Interactions contract, image output is selected only through
the polymorphic `response_format={"type":"image", ...}`. Do not also send the
removed legacy `response_modalities` field. Keep `store=false`. Fresh V3 image
requests must omit `response_format.delivery`, matching Google's current image
generation examples; V1/V2 serialized artifacts retain their historical
`delivery="inline"` bytes and must not be rewritten. The controlled parser still
requires inline image bytes in the response and rejects remote URI output.
Both preflight and the submit boundary must prove that the bounded FFmpeg
runtime advertises an MJPEG/JPEG decoder before the attempt is consumed. Legacy
preflight artifacts without this typed evidence may be loaded for historical
inspection only; they cannot derive execution gates or authorize submission.

## Local post-call and archive

On success the controlled runner atomically materializes the output before
ledger success, normalizes to 1920x1080 PNG, runs real-byte VQC, builds native
motion and a six-second H.264 review MP4, and freezes the complete local archive
manifest. No narration, Pexels, Veo, Forced Alignment or fallback call is
allowed.

Drive uses the existing PA1R OAuth/provider. Each remote ID is journaled before
verification. Interrupted uploads reconcile and reuse the same ID; exact remote
set/count/name/parent/size/checksum and duplicate absence are mandatory. The
canonical manifest is uploaded alongside its listed files without self-
reference. No local review artifact is purged.

## Human boundary

Only a verified archive receipt, technical/VQC archive eligibility, successful
native render receipt, exact local/manifest hashes, per-item Drive size/checksum
bindings, task-wide consumed authorization and one-attempt SUCCEEDED ledger can
create the pending human packet. Duplicate receipt paths or self-consistent receipt
checksums that differ from the manifest block. Human review must answer all seven
visual questions. Codex cannot auto-PASS it.

If the operator rejects an overlay, crop, motion, readability, render or archive
issue, repair from the same generated image and reverify. If the generated
source is irreparably wrong, stop with `BLOCKED_REQUIRES_NEW_PAID_CANARY_APPROVAL`.
Never submit again under the current approval.

## Current blocker (2026-07-18)

Run `img-canary-20260718T075252Z-319bacb0` passed planning/runtime preflight and
made the one authorized native submission. The provider returned HTTP 400 with
no image:

```text
planning_preflight=PASS
runtime_submit_preflight=PASS
credential_rotation=PASS
monthly_budget_gate=PASS
task_authorization=CONSUMED
budget_reservation=SPENT_CONSERVATIVE_ESTIMATE
attempts_consumed=1
provider_call_made=true
provider_error_code=GEMINI_IMAGE_PROVIDER_HTTP_400
provider_output_count=0
external_fallback_used=false
```

A non-generating model metadata GET proved that the rotated key and
`gemini-3.1-flash-image` access are valid. Offline diagnosis found that the
failed request mixed legacy `response_modalities` with the new
`response_format`; the adapter and fake-transport contract test are repaired.
The adapter now uses explicit inline delivery, and offline tests verify the
official SDK's serialized body, a fully decodable JPEG success path and a
pre-consumption decoder-readiness block. The repair has not been revalidated
with generation because the task authority is terminal.

Do not reset or reuse this run. Another request requires a new explicit paid
canary approval, a new unique run/task authority and a new budget reservation.

## IMG-CANARY-v2 one-shot approval (2026-07-18)

The attachment with SHA-256
`6261dfc83261e6470d6a1e0755e827880e57261c8791851b20812267b84e3319`
authorizes exactly one fresh `gemini-3.1-flash-image` submission at `2K`,
`16:9`, inline JPEG, output count one, estimated `$0.101`, hard cap `$0.15`.
It is not a provider-wide, CH1-FLEX v2, PKG1 or MR1 authorization.

Use only the fixed v2 profile. VQC1 status is read from repository evidence;
the CLI does not accept the legacy VQC flag as authority for v2:

```bash
PYTHONPATH=. .venv/bin/python scripts/run_img_canary.py \
  --fresh-v2-approval \
  --credential-rotation-ref rotation://img-canary/operator-confirmed/new-key \
  --open-scoped-execution-switches \
  --execute \
  --execution-token EXECUTE_EXACTLY_ONE_PAID_GEMINI_IMAGE_CANARY
```

The v2 task authority lives at a fixed approval-specific path under
`var/credentials/img-canary/authorizations/`. It binds the first run ID,
request fingerprint, prompt hash, official-SDK serialized-body hash and scoped
approval hash. A second fresh run using this attachment conflicts before any
provider attempt. The historical v1 authority and all 24 historical run files
remain immutable.

Planning preflight requires a typed, redacted Drive-root readiness receipt and
an official-SDK MockTransport body receipt. Immediately before submit, the
runner recomputes the old-run aggregate and SDK body. The provider transitions
the v2 task from `CLAIMED` to terminal `PROVIDER_ATTEMPT_SUBMITTED`, consumes the
attempt ledger `0 -> 1`, then invokes transport with SDK retries disabled.

After a successful paid response, resume only the same run for local/Drive
repairs:

```bash
PYTHONPATH=. .venv/bin/python scripts/run_img_canary.py \
  --resume-run-id img-canary-v2-<UTC>-<suffix> \
  --open-scoped-execution-switches \
  --execute \
  --execution-token EXECUTE_EXACTLY_ONE_PAID_GEMINI_IMAGE_CANARY
```

Resume reloads the terminal successful attempt and cannot generate again. Keep
rerunning deterministic normalization, VQC, native render and Drive
reconciliation until the archive verifies, then stop at human review
`PENDING`. Never auto-PASS the seven-question human checklist.

### v2 terminal execution result

The fresh run `img-canary-v2-20260718T091203Z-cce118a4` passed planning and
runtime-submit preflight, then used the one v2 authorization in a local-only
execution with Drive export excluded by the operator's final scope. Google
returned HTTP 400 before returning an interaction ID or image:

```text
attempts_consumed=1
task_authorization=CONSUMED
task_authorization_completion=PROVIDER_ATTEMPT_SUBMITTED
provider_status=NATIVE_SUBMIT_FAILED
provider_error_code=GEMINI_IMAGE_PROVIDER_HTTP_400
provider_output_count=0
external_fallback_used=false
drive_upload_calls=0
```

The provider error body was intentionally redacted, so the exact server-side
validation message cannot be recovered. The v2 payload proves that removing
legacy `response_modalities` was not sufficient. Current Google examples support
the selected model, JPEG, 16:9 and 2K fields, but do not show the SDK-exposed
`response_format.delivery` field. Treat `delivery=inline` as the leading offline
compatibility hypothesis, not as a proven cause. Removing it and sending another
request requires a new explicit paid approval, fresh run, fresh authority and
fresh reservation. Do not retry or reuse this run.

Current Google references:

- https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026
- https://ai.google.dev/gemini-api/docs/image-generation
- https://ai.google.dev/gemini-api/docs/interactions-overview

## IMG-CANARY-v3 verified closeout (2026-07-18)

Run `img-canary-v3-20260718T162027Z-a90959ed` succeeded with one provider attempt,
then received an explicit operator human-review `PASS`. A later, separate closeout
scope exported only the existing immutable package; it made zero Gemini calls.

The controlled closeout command is fixed to that run and token:

```bash
PYTHONPATH=. .venv/bin/python scripts/closeout_img_canary_v3_drive.py \
  --run-id img-canary-v3-20260718T162027Z-a90959ed \
  --confirmation-token EXPORT_REVIEWED_IMG_CANARY_V3_TO_DRIVE
```

The original manifest is historical and must not be rewritten. Its semantic
`manifest_hash` is `45140e3e…`, while its file-bytes SHA-256 is `0e853978…`.
The separate closeout envelope records both. Drive verification passed for the
exact 47-item set in folder ID `1qqlcy3m7Ry36xFRpBJEKng94yTpYFS10`; archive
receipt hash is `c60c1f…`. `PROCEED_TO_CH1_FLEX_V2=true`, but this is eligibility
only: do not automatically start CH1-FLEX v2, MR1 or publication.
