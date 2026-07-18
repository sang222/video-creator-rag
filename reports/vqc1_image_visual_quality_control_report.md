# VQC1 Production Image and Visual Quality Control Hardening

Date: 2026-07-18
Repository: `/Users/sangss/Desktop/video-creator-rag`

## Outcome

VQC1 passes its production-capable contract and deterministic offline boundary.
The implementation extends the existing technical/creative media-QC vocabulary,
uses actual image bytes for technical authority, and leaves semantic, visual-
language, generated-artifact perception and final acceptance to checksum-bound
structured/human review. No provider call occurred during VQC1.

Entry evidence was read from repository artifacts:

```text
VSR1_FINAL=PASS
IMG1_FINAL=PASS
PROCEED_TO_VQC1=true
MR1_EXECUTION=ON_HOLD
PROCEED_TO_MR1=false
alembic_head=0037_ch1_flex
```

Historical CH1-FLEX v1, PKG1 v1, PA1R, CQR1, VSR1 and IMG1 evidence was not
rewritten.

## Authority and gates

`app/contracts/image_visual_quality_control.py` defines fourteen typed gates,
reason codes, evidence references and stable hashes. The deterministic boundary
owns format/decode/dimensions/checksum, normalized bounds, crop resolution,
overlay collision/contrast inputs, native-text authority, request/model/cost/
approval/attempt binding and original-to-normalized lineage. The creative
boundary remains `REVIEW_REQUIRED`; human final approval can never auto-PASS.

Actual PNG bytes are parsed with signature, chunk length, CRC, zlib inflation,
filter-byte and raster-size validation. Crop evidence calculates effective
pixels and blocks any result below 1920x1080 or requiring upscale. Real-provider
evidence re-probes the materialized JPEG/PNG and proves this chain:

```text
Gemini response summary checksum/dimensions/format/size
→ atomic materialization receipt and actual original bytes
→ deterministic normalization receipt
→ actual normalized PNG checksum/size/1920x1080 probe
→ rights, provenance, overlay and VQC report
```

The generated foundation cannot own headline, letters, numbers, UI labels,
logos or watermarks. Native exact text is separately bound to the overlay plan.
Pending human artifact inspection produces `REVIEW_REQUIRED`, never a fabricated
absence claim.

## Offline golden and negative matrix

The suite covers a clean editorial foundation plus visible text, number, fake
UI, logo, watermark, repairable marks, safe-region and focal collisions,
sub-1080p crop, corrupt/unsupported raster, checksum/provenance/disclosure
tampering, excessive reuse, semantic/style metadata spoofing, missing human
boundary, and real JPEG→PNG lineage tampering. Every gate has typed evidence and
reason codes.

```text
tests/test_vqc1_image_visual_quality_control.py   34 passed
```

One offline repair cycle was required: the first fixture run exposed a helper
argument collision in the hash-bound test factory. The helper was corrected,
focused failures were rerun, and the complete VQC suite passed without changing
thresholds or weakening a gate. Provider attempts remained `0 → 0`.

## Verification

```text
focused VQC1                                           PASS (34)
final nine-file offline acceptance                     PASS (164 in 8.97s)
final required four-file offline acceptance            PASS (120 in 4.11s)
latest PostgreSQL acceptance before final hardening    PASS (117 in 56.99s)
post-hardening IMG1/provider PostgreSQL suite          PASS (41 in 19.71s)
post-hardening runner/security PostgreSQL suite        PASS (19 in 10.57s)
provider calls                                         0
paid attempts                                          0
production DB mutations                                0
```

```text
VQC1_ENTRY=PASS
VQC1_GENERATED_TEXT_NUMBER_QC=PASS
VQC1_FAKE_UI_LOGO_WATERMARK_QC=PASS
VQC1_COMPOSITION_COMPLIANCE=PASS
VQC1_TECHNICAL_IMAGE_FITNESS=PASS
VQC1_CROP_SAFETY=PASS
VQC1_NATIVE_OVERLAY_COMPLIANCE=PASS
VQC1_SEMANTIC_REVIEW_CONTRACT=PASS
VQC1_VISUAL_LANGUAGE_REVIEW_CONTRACT=PASS
VQC1_REUSE_SIMILARITY=PASS
VQC1_VISUAL_CONTINUITY=PASS
VQC1_RIGHTS_PROVENANCE_DISCLOSURE=PASS
VQC1_HUMAN_REVIEW_BOUNDARY=PASS
VQC1_OFFLINE_GOLDEN=PASS
VQC1_REPAIR_CYCLES=1
VQC1_FINAL=PASS
PROCEED_TO_IMG_CANARY=true
```
