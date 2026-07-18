# IMG-CANARY-v2 — Fresh approved Google Gemini Image paid canary

Date: 2026-07-18
Repository: `/Users/sangss/Desktop/video-creator-rag`
Run: `img-canary-v2-20260718T091203Z-cce118a4`

## Terminal outcome

Offline acceptance, planning preflight and runtime-submit preflight all passed.
The controlled runner then made exactly one native Gemini Interactions request.
Google returned HTTP 400 before returning an interaction identifier or usable
image. The approved attempt is consumed and the run is terminal at
`BLOCKED_REQUIRES_NEW_APPROVAL`.

```text
provider_attempts=1
provider_call_made=true
provider_status=NATIVE_SUBMIT_FAILED
provider_error_code=GEMINI_IMAGE_PROVIDER_HTTP_400
provider_output_count=0
task_authorization=CONSUMED
task_authorization_completion=PROVIDER_ATTEMPT_SUBMITTED
external_fallback_used=false
actual_cost=null
```

This is an explicit hard-stop condition in the master prompt: one paid attempt
was consumed without a usable output. No retry, prompt mutation, model switch,
provider switch, Pexels/Veo fallback or fabricated image occurred.

## Entry and old-run immutability

Repository evidence confirmed `VSR1_FINAL=PASS`, `IMG1_FINAL=PASS`,
`VQC1_FINAL=PASS`, MR1 on hold and Alembic head `0037_ch1_flex`.

The historical run `img-canary-20260718T075252Z-319bacb0` remains terminal with
attempts `1`, authority `CONSUMED`, provider status `NATIVE_SUBMIT_FAILED`, error
`GEMINI_IMAGE_PROVIDER_HTTP_400` and output count `0`. Its 24-file aggregate
SHA-256 remains:

```text
6ea77966c51b012e09430c88e9f3c91d630ea4de67cbc87a54aa1ec1ab13f423
```

No historical ledger, request, verdict or authority was reopened, reset, moved,
copied or relabeled.

## Fresh approval, identity and authorities

The v2 approval source is
`attachment://d6de1eab-f9bd-44fe-ab23-4bf7e05ce167`, SHA-256
`6261dfc83261e6470d6a1e0755e827880e57261c8791851b20812267b84e3319`.
It binds one fresh request to:

```text
provider=google_gemini_image
model=gemini-3.1-flash-image
image_size=2K
aspect_ratio=16:9
output_count=1
estimate_usd=0.101
hard_cap_usd=0.15
attempt_limit=1
fallback=false
production_eligible=false
not_publishable=true
```

Fresh evidence binds run ID, request hash, prompt hash, serialized-body hash,
scoped approval and task authority. The task transitioned
`AVAILABLE → CLAIMED → CONSUMED`; the attempt transitioned `0 → 1` exactly at
the submit boundary. Duplicate submit is durably blocked.

## Serialized request contract

The pinned official Google SDK (`google-genai` 2.10.0) serialized the request
through local MockTransport before the paid call. The redacted evidence binds:

```text
POST /v1beta/interactions
model=gemini-3.1-flash-image
response_format.type=image
response_format.mime_type=image/jpeg
response_format.delivery=inline
response_format.aspect_ratio=16:9
response_format.image_size=2K
response_modalities absent
store=false
stream=false
background=false
SDK retries disabled
```

Serialized body SHA-256:
`05bc3e657fdede00effda92844a79f66e658eddb0b2871303a83a0c9d3fa2da7`.
No key or authorization header is present in the evidence.

## Preflight and budget

Planning preflight was `PASS` with task authority `AVAILABLE` and zero attempts.
Runtime-submit preflight was `PASS` with authority `CLAIMED`, a reserved budget,
credential-rotation evidence, JPEG decoder evidence, Drive readiness and the
same request/approval/serialized-body bindings.

The internal image authority cap is USD 20. Historical conservative spend before
v2 was USD 0.101; USD 0.101 was available and reserved for this request. Because
Google returned no billing metadata after transport, the v2 reservation was
conservatively marked spent. Internal conservative totals are therefore USD
0.202 spent and USD 19.798 remaining. Remaining budget is capacity evidence,
not authority for another request.

Repository defaults stayed disabled; real execution switches were open only in
the scoped canary subprocess.

## Provider response and HTTP 400 diagnosis

Confirmed evidence is limited to an HTTP 400 from the provider. The adapter
intentionally did not persist the raw response body, so the exact Google
validation message is unavailable. Consequently, a definitive parameter-level
root cause cannot be claimed from this run.

The earlier v1 diagnosis — mixing legacy `response_modalities` with current
`response_format` — was insufficient: v2 removed `response_modalities` and still
received HTTP 400.

The v2 body otherwise matches Google's current public examples for model,
image response format, JPEG, 16:9 and 2K. The strongest offline compatibility
hypothesis is `response_format.delivery=inline`: it is exposed by the pinned SDK
and was mandatory in the operator-approved payload, but is absent from Google's
current image-generation examples and May-2026 migration guide. This is a
hypothesis, not a proven root cause; backend rollout/regression or an account-side
provider rejection also cannot be excluded without the redacted server message.
Quota exhaustion is not proven by the 400 evidence.

Official references:

- https://ai.google.dev/gemini-api/docs/image-generation
- https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026
- https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-image

No provider revalidation was performed because it would be a forbidden second
paid generation request.

## Output safety and downstream stages

No image was returned or materialized. The run contains no original JPEG,
normalized image, QC crops, review MP4, archive manifest, Drive receipt or human
review packet. No transient `.part` file remains.

Therefore real-byte VQC, generated-artifact QC, composition QC, crop safety,
native overlay binding, render and rights/provenance completion are all
`BLOCKED_NO_PROVIDER_OUTPUT`; none is reported as a synthetic PASS.

The configured Drive root passed readiness before submit. The user's final
execution authorization explicitly narrowed this invocation to local-only with
no Drive export, and the missing provider output independently prevents creation
of a valid review archive. Drive upload count is zero and
`ARCHIVE_VERIFIED=false`.

## Primary evidence

```text
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v2-20260718T091203Z-cce118a4/manifests/previous-run-immutability.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v2-20260718T091203Z-cce118a4/manifests/operator-approval-v2-binding.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v2-20260718T091203Z-cce118a4/manifests/serialized-request-evidence.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v2-20260718T091203Z-cce118a4/manifests/preflight.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v2-20260718T091203Z-cce118a4/manifests/preflight-runtime-submit.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v2-20260718T091203Z-cce118a4/manifests/attempt-ledger.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v2-20260718T091203Z-cce118a4/manifests/provider-operation-receipt.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v2-20260718T091203Z-cce118a4/manifests/provider-response-summary-raw-safe.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v2-20260718T091203Z-cce118a4/manifests/task-authorization-consumed.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v2-20260718T091203Z-cce118a4/manifests/budget-authority-spent.json
```

The safe response summary proves raw response, base64 image data, temporary URLs,
authorization headers and API credentials were not persisted.

## Verification and repair history

Before submit:

```text
Alembic head                                      0037_ch1_flex
required five-file PostgreSQL acceptance          142 passed in 73.01s
post-paid determinism/archive suite                 78 passed
v2 focused suite                                   13 passed
Drive/archive focused suite                         21 passed
planning preflight                                  PASS
runtime-submit preflight                            PASS
```

Seven cycles are recorded in `reports/img_canary_v2_repair_cycles.json`. Cycles
1–5 are offline/pre-submit repairs and preserve attempts `0 → 0`. Cycle 6 records
the single provider execution, terminal HTTP 400 and attempt transition `0 → 1`.
Cycle 7 repairs only clock-coupled test fixtures; post-failure focused v2 tests
pass `13/13`, and security/CLI/controlled-runner tests pass `22/22`, while
attempts remain `1 → 1`. There are zero retry submissions.

## Exact next action

Keep this run and the historical run immutable. Before any future paid canary,
add safe allowlisted preservation of the provider validation category and obtain
a new explicit approval for a fresh run/payload. Removing `delivery`, changing
the model or sending any diagnostic generation request requires that new
approval. Do not reuse this authority, retry, fallback, export to Drive, start
CH1-FLEX v2, revise PKG1, execute MR1 or upload YouTube.

```text
VQC1_FINAL=PASS

IMG_CANARY_V2_ENTRY=PASS
IMG_CANARY_V2_FRESH_RUN_ID=PASS
IMG_CANARY_V2_OLD_RUN_IMMUTABLE=PASS
IMG_CANARY_V2_NEW_APPROVAL=PASS
IMG_CANARY_V2_SERIALIZED_REQUEST_CONTRACT=PASS

IMG_CANARY_V2_PROVIDER_ROUTE=google_gemini_image
IMG_CANARY_V2_MODEL=gemini-3.1-flash-image
IMG_CANARY_V2_IMAGE_SIZE=2K
IMG_CANARY_V2_ASPECT_RATIO=16:9
IMG_CANARY_V2_OUTPUT_COUNT=1
IMG_CANARY_V2_PROVIDER_ATTEMPTS=1
IMG_CANARY_V2_EXTERNAL_FALLBACK_USED=false

IMG_CANARY_V2_PROVIDER_EXECUTION=BLOCKED
IMG_CANARY_V2_OUTPUT_SAFETY=BLOCKED
IMG_CANARY_V2_TECHNICAL_IMAGE_QC=BLOCKED
IMG_CANARY_V2_GENERATED_ARTIFACT_QC=BLOCKED
IMG_CANARY_V2_COMPOSITION_QC=BLOCKED
IMG_CANARY_V2_CROP_SAFETY=BLOCKED
IMG_CANARY_V2_NATIVE_OVERLAY=BLOCKED
IMG_CANARY_V2_RENDER=BLOCKED
IMG_CANARY_V2_RIGHTS_PROVENANCE=BLOCKED
IMG_CANARY_V2_DRIVE_ARCHIVE=BLOCKED
ARCHIVE_VERIFIED=false

IMG_CANARY_V2_REPAIR_CYCLES=7
IMG_CANARY_V2_HUMAN_REVIEW=PENDING
IMG_CANARY_V2_FINAL=BLOCKED

PROCEED_TO_CH1_FLEX_V2=false
MR1_EXECUTION=ON_HOLD
PROCEED_TO_MR1=false
```
