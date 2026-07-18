# Google Gemini Image Activation Guard

IMG1 is a fixture-only provider foundation, not an activation or paid canary.

```text
IMG1_PROVIDER_EXECUTION=DISABLED
MR1_EXECUTION=ON_HOLD
PROCEED_TO_MR1=false
```

## Safe defaults

```text
GEMINI_API_KEY=<existing shared secret>
VCOS_GEMINI_IMAGE_REAL_GENERATION_ENABLED=false
VCOS_IMG1_FIXTURE_ONLY=true
VCOS_GEMINI_IMAGE_PROVIDER_ROUTE_APPROVED=true
GEMINI_IMAGE_MODEL_ID=gemini-3.1-flash-image
GEMINI_IMAGE_DEFAULT_SIZE=2K
GEMINI_IMAGE_DEFAULT_ASPECT_RATIO=16:9
GEMINI_IMAGE_MAX_OUTPUTS=1
GEMINI_IMAGE_MAX_ATTEMPTS_PER_SCENE=1
```

Use only the existing `GEMINI_API_KEY`; do not create an image-specific key
alias. Never print, persist or place the key in a URL, manifest or report.
Provider-route approval means the route may be planned; it is not paid-call
authorization.

## Read-only inspection

Use:

```text
GET /providers/google-gemini-image/readiness
```

The endpoint performs no generation probe. Check:

- route registered as `google_gemini_image`, separate from `google_veo`;
- credential configured boolean and redaction marker;
- approved model and versioned cost catalog present;
- route approval state;
- real execution false and fixture-only true;
- global/provider kill switches closed for execution;
- exact next action.

Missing credentials may block future activation readiness, but they do not
block an offline fixture rehearsal. Do not enable real generation to make a
readiness card green.

## Offline fixture rehearsal

The allowed IMG1 rehearsal uses a fake client and a deterministic local raster.
It may compile the knowledge-silos scene, estimate catalog cost, validate
approval/idempotency refs, materialize through `.part`, compute SHA-256, probe
dimensions, run fixture QC and create overlay/provenance evidence.

Required proof:

```text
transport=LOCAL_FIXTURE_ONLY
provider_call_made=false
generation_attempts_consumed=0
actual_cost=null
production_eligible=false
not_publishable=true
```

Delete temporary fixture workspace after verification. A fixture success is
not evidence that Gemini generation, billing, moderation or production image
quality works.

## Future paid activation prerequisites

Do not change the safe defaults in IMG1. A separate, explicitly approved canary
task must first require:

1. IMG1 and VQC1 acceptance evidence.
2. A fresh VSR1-eligible scene and native-overlay binding when exact content is
   required.
3. Versioned cost estimate, hard cap and exact approval amount.
4. Exact provider/stage approval and an unexpired paid-call authorization.
5. Provider boundary, monthly budget and attempt-limit gates passing.
6. A unique provider idempotency key and no active/completed duplicate.
7. Global and Gemini Image kill switches explicitly opened for the canary only.
8. One output and at most one network generation attempt.
9. A reviewed retention, provenance, disclosure and post-generation QC plan.

The current adapter has no real network implementation. Opening flags alone
must not produce a call.

## Failure, retry and fallback

- Configuration, eligibility, truth, cost or approval failure blocks before the
  provider boundary.
- Fixture planning does not consume an attempt.
- A future network submit would consume exactly one attempt.
- Download/materialization retry reuses the existing output and never submits
  another generation.
- A second generation requires a new explicit approval.
- Provider failure does not open Pexels, Veo or another image provider.
- Generated text, visible number, fake UI, logo/trademark or watermark risk
  yields review/block evidence; it never transfers exact-content authority to
  the image.

## Rollback and hold state

The safe rollback is configuration-only:

```text
VCOS_GEMINI_IMAGE_REAL_GENERATION_ENABLED=false
VCOS_IMG1_FIXTURE_ONLY=true
```

Keep CH1-FLEX v1 and PKG1 v1 immutable. Do not create a migration, production
render, Drive upload, YouTube action or MR1 approval from this runbook. The next
milestones remain VQC1, offline calibration and a separately authorized paid
canary before CH1-FLEX v2 or MR1 can be considered.
