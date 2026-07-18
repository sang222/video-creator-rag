# IMG-CANARY-v3 — Corrected one-shot Gemini Image canary

Date: 2026-07-18
Run: `img-canary-v3-20260718T162027Z-a90959ed`
Scope: one paid Gemini image, local review only, no Drive export

## Outcome

The corrected request succeeded. Google returned one JPEG; the runner consumed
exactly one provider attempt, materialized the bytes, passed real-byte technical
VQC, rendered a six-second native review MP4 and built a complete 44-role local
archive.

```text
provider_execution=SUCCEEDED
provider_status=INTERACTION_COMPLETED
provider_attempts=1
retry_generation_submissions=0
external_fallback_used=false
output_count=1
output_format=JPEG
output_dimensions=2752x1536
technical_image_qc=PASS
creative_review=REVIEW_REQUIRED
native_render=PASS
local_archive_roles=44
drive_upload_calls=0
production_eligible=false
not_publishable=true
```

The local deliverables are:

- Original: `/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v3-20260718T162027Z-a90959ed/source/original-generated.jpg`
- Normalized: `/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v3-20260718T162027Z-a90959ed/source/normalized-1920x1080.png`
- Review MP4: `/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v3-20260718T162027Z-a90959ed/runs/img-canary-v3-20260718T162027Z-a90959ed/img-canary-review.mp4`
- Archive manifest: `/Users/sangss/Desktop/video-creator-rag/artifacts/img_canary/img-canary-v3-20260718T162027Z-a90959ed/archive/production-archive-manifest.json`

## Corrected request

The official SDK serialization was captured offline before submit. V3 uses the
current polymorphic image response contract:

```json
{
  "type": "image",
  "mime_type": "image/jpeg",
  "aspect_ratio": "16:9",
  "image_size": "2K"
}
```

`delivery` and legacy `response_modalities` are absent; `store=false`; SDK
automatic retries are disabled. This matches Google's current Gemini image
generation examples. The successful V3 result strongly confirms that the
V2-only `delivery="inline"` field caused or participated in the prior HTTP 400,
although the redacted V2 error body still prevents claiming its exact server
message.

Serialized body SHA-256:
`b32c6f2ea5b3cd113ddb1e1b92beb56f29c1956d514829643a1844c2654adce8`.

## Approval, budget and immutability

The operator message `tự fix và rerun prompt` was bound as a fresh V3 scoped
approval to one run/request/prompt/serialized body, model
`gemini-3.1-flash-image`, 2K, 16:9, one output, estimate USD 0.101 and hard cap
USD 0.15. The task authority transitioned `AVAILABLE → CLAIMED → CONSUMED` and
the attempt ledger transitioned `PLANNED/0 → SUCCEEDED/1`. Resume after local
failure loaded the persisted success and did not call Gemini again.

The internal USD 20 image budget conservatively records USD 0.303 across V1,
V2 and V3, leaving USD 19.697. Google returned usage metadata but no billing
amount, so `actual_cost_usd` remains null and V3 is marked at the USD 0.101
conservative estimate.

Historical evidence remains unchanged:

```text
V1 files=24 aggregate=6ea77966c51b012e09430c88e9f3c91d630ea4de67cbc87a54aa1ec1ab13f423
V2 files=28 aggregate=7528b4c0fcbcb523174d158e6e2e760ba14409d8d05a3df0a330daa990b22603
```

## Output, VQC and render

The original JPEG is 2,340,507 bytes with SHA-256
`3ab066bdb556be8161f1736959346c6decbdba61d3f12c3348e249445b1f7293`.
The deterministic 1920x1080 PNG has SHA-256
`af752598e540ee83e88f960c71bb4255877753cf4efb799e97dcabb4a604e4b4`.

Google's successful SDK response exposed no provider interaction ID. The first
local VQC pass therefore stopped at the old explicit-ID assumption. No provider
retry occurred. V3 now uses a truthful deterministic evidence URI bound to the
immutable provider-response hash; V1/V2 remain strict and still require their
explicit IDs. Rights/provenance and technical VQC then passed.

The review MP4 is H.264, 1920x1080, yuv420p/BT.709, six seconds, with SHA-256
`8e5a4dd39fa7da4321fc8e0efb93076cc1637805f5300d0ed54861c5cfcacab4`.
Creative judgment remains `REVIEW_REQUIRED` for the operator.

## Local-only boundary

The user's approved execution scope explicitly excluded Drive export. Drive
OAuth/root readiness was checked read-only before submit, but no file upload or
Drive receipt exists. The local archive contains all 44 required V3 roles and
has manifest SHA-256
`45140e3e6f2a0291935bf241d5776ecabd78d5e76ecb07715723b366fc77e268`.

Because Drive verification and human review are not complete, the architectural
rollout gate remains closed:

```text
IMG_CANARY_V3_PROVIDER_EXECUTION=PASS
IMG_CANARY_V3_TECHNICAL_IMAGE_QC=PASS
IMG_CANARY_V3_NATIVE_RENDER=PASS
IMG_CANARY_V3_LOCAL_ARCHIVE=PASS
IMG_CANARY_V3_DRIVE_EXPORT=SKIPPED_BY_OPERATOR_SCOPE
IMG_CANARY_V3_HUMAN_REVIEW=PENDING
IMG_CANARY_V3_FINAL=BLOCKED_DRIVE_EXPORT_AND_HUMAN_REVIEW
PROCEED_TO_CH1_FLEX_V2=false
MR1_EXECUTION=ON_HOLD
```

## Verification

Before submit, the PostgreSQL acceptance suite passed 200/200 with Alembic head
`0037_ch1_flex`. The V3 missing-ID lineage plus V1/V2/VQC regression passed
58/58. After the local repair and real-image closeout, the expanded PostgreSQL
acceptance suite passed 204/204 in 104.51 seconds. A fake V3 end-to-end run
independently passed plan, preflight, exact-one submit, duplicate suppression,
JPEG materialization, VQC, render and 44-role archive. Compile and
`git diff --check` pass. No commit or tag was created.

## Drive closeout (separately authorized)

The operator later returned human review `PASS` and separately authorized export
of this existing immutable run. No Gemini request was made during closeout. The
original 44-role manifest was preserved; a closeout envelope records its semantic
hash `45140e3e…` and actual file-bytes SHA-256 `0e853978…` without conflating them.

Google Drive exact-set verification passed for 47/47 items in folder
`1qqlcy3m7Ry36xFRpBJEKng94yTpYFS10`. Names, parents, unique remote IDs, sizes,
checksums and duplicate absence passed. Receipt hash is `c60c1f25307f21a25192dc8a2b192373996da0ee3971bb5c59803134a27046c5`.

```text
IMG_CANARY_V3_HUMAN_REVIEW=PASS
IMG_CANARY_V3_DRIVE_EXPORT=PASS
ARCHIVE_VERIFIED=true
IMG_CANARY_V3_FINAL=PASS
PROCEED_TO_CH1_FLEX_V2=true
MR1_EXECUTION=ON_HOLD
```

CH1-FLEX v2 is eligible for a separate planning task but was not started here.
