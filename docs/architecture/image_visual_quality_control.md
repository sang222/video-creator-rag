# Image and Visual Quality Control Architecture

VQC1 extends the existing media-QC model for generated editorial stills. It
does not create a second generic QC platform and does not call an OCR, VLM or
LLM review provider.

## Authority model

Deterministic code owns actual raster decode, format, dimensions, checksum,
minimum effective crop pixels, normalized region bounds, focal/protected-region
collision, native-overlay contrast inputs, request/model/cost/approval/attempt
bindings, provenance completeness and archive eligibility.

Checksum-bound structured review owns observations and concerns, but never
infers semantic or visual-language PASS from filenames or metadata. Human review
is final authority for metaphor clarity, artifact perception, brand fit,
headline readability, motion/crop quality and production-pattern acceptance.

Every report always contains the same fourteen typed gates:

```text
GeneratedTextArtifactGate
GeneratedNumberArtifactGate
FakeUILogoGate
WatermarkArtifactGate
CompositionComplianceGate
SemanticMatchGate
VisualLanguageMatchGate
TechnicalImageFitnessGate
CropSafetyGate
ReuseSimilarityGate
VisualContinuityGate
RightsDisclosureCompletenessGate
NativeOverlayComplianceGate
HumanVisualApprovalGate
```

Verdicts are `PASS`, `REVIEW_REQUIRED` or `BLOCK`. All gates require reason
codes, evidence refs and stable content hashes. A technical/rights/composition
PASS can make a creative-review candidate archive-eligible even while semantic,
continuity, artifact perception and human gates remain `REVIEW_REQUIRED`.

## Byte and lineage chain

PNG technical probing verifies signature, chunk framing, CRC, zlib stream,
scanline filter bytes, declared raster size, alpha behavior and corruption.
Provider JPEG outputs are fully decoded through the existing native FFmpeg
authority before materialization acceptance.

For a real canary, VQC requires all of these typed bindings:

```text
GeminiImageGenerationRequest
IMGCanaryScopedApproval
GeminiImageCostEstimateSnapshot
IMGCanaryAttemptLedger(SUCCEEDED, attempts=1)
IMGCanaryProviderResponseSummary
VQC1ImageMaterializationEvidence
VQC1ImageNormalizationEvidence
```

The service re-hashes every contract, re-probes the actual materialized original
and normalized PNG, and proves response checksum/dimensions/format/size →
original file → normalization receipt → current VQC bytes. Provider request and
operation ID refs, estimated/actual costs, attempt refs, overlay plan and
synthetic disclosure must agree end to end.

## Generated-content and native authority

Generated pixels never own exact text, letters, numbers, UI labels, product
names, logos or watermarks. Suspicious regions are normalized boxes with state,
confidence, crop refs, notes and repairability. Pending inspection yields
`REVIEW_REQUIRED`; visible irreparable artifacts yield `BLOCK`. Safe native
overlay repairs may cover only a specifically bound region without hiding the
meaning-bearing subject.

The exact headline lives in `IMGCanaryNativeHeadlineArtifact`. The overlay plan,
headline hash, normalized image checksum, safe region, font size and contrast
panel are bound before render.

## Persistence boundary

Evidence and reports are JSON/hash artifacts. No raw API key, authorization
header, raw response/base64, temporary URL or raw image bytes are embedded in
manifests. Original and normalized image files are separate checksum-addressed
archive entries. Historical evidence is immutable and no database migration is
required.
