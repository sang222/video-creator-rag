# Google Gemini Image Provider Foundation

IMG1 registers a new external still-image route without activating paid
generation:

```text
provider_key=google_gemini_image
vendor=google
capability=AI_IMAGE_GENERATION
transport=GEMINI_API_NATIVE
default_model=gemini-3.1-flash-image
execution=DISABLED
fixture_only=true
```

`google_gemini_image` is distinct from `google_veo`. It is not an LLM lane,
renderer or fallback provider. Google Veo remains the selective motion provider;
NativeFFmpeg remains final composition authority.

## Boundary and flow

```text
VisualSourceDecision
  -> AIImageRequest
  -> ImagePromptCompiler
  -> GeminiImageGenerationRequest
  -> GoogleGeminiImageAdapter (LOCAL_FIXTURE_ONLY in IMG1)
  -> AIGenerationManifest(media_kind=STILL_IMAGE)
  -> PostGenerationImageQCManifest
  -> ImageNormalizationManifest
  -> NativeOverlayImageBinding
  -> future NativeRenderPlan consumption
```

The provider-neutral request is bound to the VSR1 decision and visual-direction
hashes. It rejects evidence, actual UI, actual product and actual document
scenes; unsupported size/aspect; missing route approval; and exact text or
numbers without a native-overlay contract. Reference images require provenance,
checksum and authorization evidence. Style-reference uploads are disabled.

The Gemini-specific request fixes one output, one automated attempt and the
approved model/size/aspect matrix. Grounding is off by default. A 1K request is
blocked by the 1080p minimum; 4K requires explicit review evidence. Provider
failure never opens another provider.

## Prompt and exact-content authority

`ImagePromptCompiler` produces stable sections for subject, environment,
composition, treatment, lighting, palette, camera, depth, negative space,
continuity and negative constraints. Every prompt forbids letters, numbers,
logos, watermarks, interface text and fake software UI.

When an overlay is required, the generated image supplies only the editorial
visual foundation. Native overlay data remains authoritative for headlines,
numbers, labels, citations, CTA, tool/product names and workflow nodes.
`NativeOverlayImageBinding` binds the image checksum to the VSR1 decision,
safe regions and `NativeOverlayPlan`; the image model never owns final text.

## Model, cost and attempt policy

The single price source is
`config/google_gemini_image_model_price_catalog.yaml`, version
`2026-07-17`. It contains the approved
`gemini-3.1-flash-image` matrix for 1K, 2K and 4K across 16:9, 9:16 and 1:1.
The default route is 2K/16:9. Estimates are planning evidence; actual billed
amount remains null because IMG1 makes no provider call.

Generic R3D8 cost, human approval, idempotency, paid-attempt and call-ledger
storage is reused. Image-specific cost snapshots bind catalog version, model,
size, aspect ratio, output count, attempt count, cap and approval amount. A
network submit would consume one attempt; fixture planning and materialization
consume none. Materialization retries do not regenerate an image.

## Output safety and quality

Raw bytes and temporary URLs live only in
`GeminiImageTransientOutput`. Durable contracts accept a redacted
`volatile://google-gemini-image/...` reference, provider IDs, checksum,
dimensions and format; they reject raw or signed URLs.

Fixture materialization writes a `.part` file, streams SHA-256, probes
complete PNG structure, CRC, bounded decode and dimensions, atomically renames on success and removes the partial
file on failure. Normalization planning uses sRGB by default, does not silently
upscale and blocks an effective crop below 1080p.

IMG1 defines deterministic fixture behavior for generated text/number, fake UI,
logo/trademark, watermark, composition, semantic match, visual language,
technical fitness, crop safety, reuse similarity and rights/disclosure gates.
VQC1 owns later detector calibration and production thresholds.

## Readiness and execution state

`GET /providers/google-gemini-image/readiness` is configuration-only. It
reports route registration, credential presence as a boolean, model/catalog
presence, route approval, execution/fixture flags, cost catalog state, kill
switches and the exact next action. It never probes Gemini or returns
`GEMINI_API_KEY`.

Real generation is deliberately unimplemented in IMG1. The adapter returns
non-executing evidence unless every future boundary, paid approval, cost,
budget, attempt, idempotency and kill-switch gate passes; even then this
milestone raises rather than performing a network call.

## Persistence and compatibility

New request, manifest, normalization, QC, overlay and provenance evidence fits
existing `ArtifactVersion` JSON plus generic provider ledgers. No database
migration is required; Alembic remains at `0037_ch1_flex`. Historical
CH1-FLEX v1, PKG1 v1, VSR1 evidence and MR1 approvals are not rewritten.

IMG1 does not run a paid canary, activate CH1-FLEX v2, revise PKG1, resume MR1,
render production media, upload Drive or publish YouTube.
