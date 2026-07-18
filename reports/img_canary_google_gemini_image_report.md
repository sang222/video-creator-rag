# IMG-CANARY Controlled Google Gemini Image Report

Date: 2026-07-18
Repository: `/Users/sangss/Desktop/video-creator-rag`
Run: `img-canary-20260718T075252Z-319bacb0`

## Outcome

VQC1 and both planning/runtime canary preflights passed. The runner made exactly
one native Gemini Image submission. Google returned HTTP 400 before any usable
image was returned, so the durable attempt ledger moved to
`BLOCKED_REQUIRES_NEW_APPROVAL`, the task-wide authority moved to `CONSUMED`,
and the USD 0.101 reservation was conservatively marked `SPENT`. No fallback or
second generation request occurred.

```text
provider_attempts=1
provider_call_made=true
provider_status=NATIVE_SUBMIT_FAILED
provider_error_code=GEMINI_IMAGE_PROVIDER_HTTP_400
provider_output_count=0
external_fallback_used=false
actual_cost=null
```

This is a master-prompt hard blocker: one paid attempt was consumed without a
usable output. The task cannot normalize, run real-image VQC, render, upload an
archive or open human review without fabricating an asset or violating the
single-attempt rule.

## Entry and immutable state

Repository evidence confirms VSR1 and IMG1 PASS, VQC1 entry authorization, MR1
ON_HOLD and Alembic head `0037_ch1_flex`. Historical CH1-FLEX v1, PKG1 v1,
PA1R, CQR1, VSR1 and IMG1 artifacts remain immutable. No commit or tag was
created.

## Controlled request and approval

The immutable request binds `google_gemini_image`,
`gemini-3.1-flash-image`, 2K, 16:9, one output, no references, no grounding,
one attempt, no fallback and a USD 0.15 hard cap. The catalog estimate is USD
0.101. The exact headline is stored in a separate native artifact and is absent
from the generation prompt.

The dedicated monthly image budget is USD 20. Before submit, the runner claimed
the fixed task authority and reserved USD 0.101. After the failed provider
submission, the safety ledger marked the full estimate spent because the API
returned no actual billing metadata. The remaining internal capacity is USD
19.899; this does not authorize another call.

## Preflight and execution authorities

The rotated key fingerprint differed from the mode-0600 incident record.
Planning preflight was `PASS/AVAILABLE/AVAILABLE_UNRESERVED`; immediately before
transport the runtime preflight was `PASS/CLAIMED/RESERVED`. Repository defaults
remained `REAL_GENERATION_ENABLED=false` and `FIXTURE_ONLY=true`; both execution
switches were open only in the scoped subprocess.

Drive readiness passed before task claim. The local TLS chain was repaired with
the environment's certifi CA bundle, and the missing configured root was replaced
with one verified dedicated `VCOS IMG Canary` folder. No archive upload occurred.

Primary evidence:

```text
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-20260718T075252Z-319bacb0/manifests/preflight.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-20260718T075252Z-319bacb0/manifests/preflight-runtime-submit.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-20260718T075252Z-319bacb0/manifests/attempt-ledger.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-20260718T075252Z-319bacb0/manifests/provider-operation-receipt.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-20260718T075252Z-319bacb0/manifests/provider-response-summary-raw-safe.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-20260718T075252Z-319bacb0/manifests/task-authorization-consumed.json
/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-20260718T075252Z-319bacb0/manifests/budget-authority-spent.json
```

The provider summary proves that raw response data, base64, temporary URLs,
authorization headers and credentials were not persisted.

## HTTP 400 root cause and offline repair

After the attempt, a non-generating `models.get` call with the same key returned
the exact model and supported generation actions. This excludes an invalid key
or inaccessible model without consuming another image attempt. Official Google
Interactions guidance for the May-2026 schema consolidates output selection into
the polymorphic `response_format`. The failed payload mixed that new contract
with legacy `response_modalities=["image"]`; SDK 2.10.0 accepted the field but
the server rejected the request.

The adapter now sends one current image `response_format` with JPEG, explicit
inline delivery, 16:9 and 2K, keeps `store=false`, and continues to disable SDK
retries. The exact SDK-serialized HTTP body is locked by MockTransport coverage.
Preflight and the submit boundary also prove the bounded FFmpeg runtime
advertises MJPEG/JPEG decoding before the one paid attempt can be consumed.
Legacy preflight records without that typed field remain readable only as
history; they cannot derive execution gates or pass the persisted-submit boundary.

Official references:

- https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026
- https://ai.google.dev/gemini-api/docs/image-generation
- https://ai.google.dev/gemini-api/docs/interactions-overview

This repair has offline verification only. Revalidating it against the provider
would be a second paid generation request and is forbidden under the consumed
approval.

## Downstream result

No original image, normalized image, QC crop, review MP4, local review package,
Drive archive receipt or human-review packet exists. No `.part` file remains.
All downstream gates are therefore `BLOCKED_NO_PROVIDER_OUTPUT`, not synthetic
PASS results. The dedicated Drive folder is ready but contains no canary archive
from this run.

## Verification and repair history

```text
expanded offline acceptance before paid execution        164 passed in 8.97s
required four-file offline acceptance                    120 passed in 4.11s
latest full PostgreSQL acceptance before hardening        117 passed in 56.99s
post-hardening IMG1/provider suite                         41 passed in 19.71s
post-hardening runner/security suite                       19 passed in 10.57s
post-HTTP400 IMG canary + IMG1 regression suite            42 passed in 30.02s
post-decoder-hardening focused regression suite             54 passed in 28.83s
final eight-file acceptance (includes required four)       160 passed in 76.72s
post-legacy-gate focused regression suite                   55 passed in 30.29s
post-legacy-gate final eight-file acceptance               161 passed in 76.40s
real Gemini Image submissions                               1
non-generating model metadata diagnostics                   1
Drive archive uploads for this run                          0
external provider fallbacks                                 0
```

Twenty-five repair cycles are recorded in
`reports/img_canary_repair_cycles.json`. Cycles 20-21 repaired Drive readiness
with attempts `0 → 0`; cycle 22 records the real HTTP 400 and attempt `0 → 1`;
cycle 23 records the offline request-contract repair; cycle 24 adds the
pre-submit JPEG decoder gate and serialized-body/valid-JPEG regressions. Both
offline cycles preserve attempt `1 → 1`; cycle 25 confines legacy preflight
compatibility to read-only history and requires typed decoder evidence at every
execution boundary, also preserving attempt `1 → 1`.

## Exact next action

Obtain a new explicit paid-canary approval. Only then may a fresh unique run,
fresh task-wide authority and fresh budget reservation submit the repaired
payload once. Do not reset or reuse the consumed authority, and do not execute
fallback, MR1, CH1-FLEX v2, PKG1 revision, YouTube upload or production publish.

```text
VQC1_FINAL=PASS
IMG_CANARY_PREFLIGHT=PASS
IMG_CANARY_PROVIDER_ATTEMPTS=1
IMG_CANARY_EXTERNAL_FALLBACK_USED=false
IMG_CANARY_PROVIDER_EXECUTION=FAIL
IMG_CANARY_DRIVE_ARCHIVE=BLOCKED
ARCHIVE_VERIFIED=false
IMG_CANARY_HUMAN_REVIEW=PENDING
IMG_CANARY_FINAL=BLOCKED
MR1_EXECUTION=ON_HOLD
PROCEED_TO_MR1=false
PROCEED_TO_CH1_FLEX_V2=false
```
