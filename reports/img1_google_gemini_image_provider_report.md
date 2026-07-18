# IMG1 Google Gemini Image Provider Foundation Report

Date: 2026-07-18
Repository: `/Users/sangss/Desktop/video-creator-rag`
Scope: production-safe provider foundation, local fixture transport only

## Outcome

IMG1 is complete and passes its offline acceptance boundary. The repository now
has a distinct `google_gemini_image` still-image route with
`gemini-3.1-flash-image` as its catalog-bound default, while final composition
and exact text/number/UI authority remain native. Real Gemini Image execution is
disabled and no paid canary was run.

Entry evidence was read from the required repository artifacts rather than
reconstructed:

```text
VSR1_FINAL=PASS
PROCEED_TO_IMG1=true
```

The VSR1 report/summary, visual impact report/summary and both routing
architecture documents are present and consistent. Historical CH1-FLEX v1 and
PKG1 v1 evidence remains immutable; MR1 remains on hold.

## Existing uncommitted work review

The pre-existing uncommitted `app/core/config.py`, `app/contracts/ai_image.py`
and `app/contracts/google_gemini_image.py` work is directly related to IMG1 and
was retained and completed. The local `tmp/` directory contains research
evidence relevant to model/catalog selection; it remains user-owned and is
excluded by `tmp/*`, so its contents are not staged. No unrelated application
change was folded into IMG1.

The implementation inventory records 44 repo mappings: 13 reused, 11 extended
and 20 added components. Every mapping has `migration_impact=NONE`:
`reports/img1_google_gemini_image_inventory.json`.

## Implemented foundation

| Area | Repository implementation | Result |
| --- | --- | --- |
| Provider route | `app/services/provider_stack.py`, `app/services/m2.py`, `config/provider_registry_catalog.yaml` | `google_gemini_image` is a canonical external IMAGE provider distinct from `google_veo`; capability is `AI_IMAGE_GENERATION`, transport is `GEMINI_API_NATIVE`. |
| Settings | `app/core/config.py` | Reuses the single typed `GEMINI_API_KEY`; default model `gemini-3.1-flash-image`, size `2K`, aspect `16:9`, one output and one automated attempt. Real execution defaults false and fixture-only defaults true. |
| Model/cost catalog | `config/google_gemini_image_model_price_catalog.yaml` | Version `2026-07-17`; complete 1K/2K/4K × 16:9/9:16/1:1 matrix. 1K blocks, 2K is allowed/default, 4K requires review. Actual billed amount remains null. |
| Provider-neutral request | `app/contracts/ai_image.py`, `app/services/ai_image.py` | Typed request and builder bind VSR1 decisions, visual direction, rights, cost, approval, idempotency, native overlay and deterministic hashes. Evidence/UI/product truth rejects fail closed. |
| Gemini request | `app/contracts/google_gemini_image.py` | Locks provider/model/size/aspect/output, grounding approval, references, negative constraints, gate evidence and hash chain. |
| Prompt compiler | `app/services/ai_image.py::ImagePromptCompiler` | Stable anatomy and continuity; always forbids letters, numbers, logos, watermark, interface text and fake software UI; reserves clean overlay space. |
| Reference policy | `app/contracts/ai_image.py::AIImageReferenceAsset` | Provenance, rights, checksum and authorization are mandatory; third-party style references are blocked and references cannot bypass factual-truth gates. |
| Adapter/output safety | `app/providers/google_gemini_image.py` | Fixture protocol only; guarded real path is deliberately unimplemented. Workspace containment, no unsafe overwrite, `.part` + fsync + SHA-256 + atomic rename + cleanup, transient purge and bounded PNG validation are enforced. |
| Cost/firewall/idempotency | `app/services/google_gemini_image_catalog.py`, `app/services/r3d8.py` | Cost is derived from the versioned catalog and existing generic R3D8 approval/budget/attempt/call-ledger persistence. No second paid ledger or attempt domain was added. |
| Generic manifest | `app/contracts/asset_acquisition.py::AIGenerationManifest` | Provider-neutral `STILL_IMAGE` support adds request/output/dimension/QC/overlay/disclosure bindings without creating a second AI generation manifest. Legacy video manifests remain readable. |
| QC/normalization/overlay/provenance | `app/contracts/ai_image.py`, `app/services/ai_image.py` | Nine typed fixture QC gates, normalization planning, exact native-overlay binding and synthetic-media provenance all remain non-publishable and non-evidentiary. |
| Readiness | `app/api/routes/google_gemini_image.py` | Read-only `GET /providers/google-gemini-image/readiness`; exposes booleans/state/next action only, with no execution endpoint or generation probe. |

NativeFFmpeg remains final composition authority. Gemini Image is not a Veo
route, an LLM lane or a renderer.

## Guard and execution boundary

Any future real submit requires all typed provider-boundary, paid authorization,
catalog cost, monthly budget, attempt-limit, idempotency and kill-switch evidence
to bind the exact request fingerprint. The real adapter branch additionally
requires both global production execution flags, image execution enabled,
fixture-only disabled and media provider calls enabled. Even with those flags,
IMG1 raises `IMG1_REAL_GEMINI_IMAGE_EXECUTION_NOT_IMPLEMENTED`; it cannot make a
real network request in this task.

Fixture planning does not pretend to have paid authorization or open kill
switches. It consumes no provider attempt, and duplicate fixture submission
returns the existing in-memory result. The existing persistent R3D8 idempotency
and ledger records remain the authority for a future execution phase.

## Offline fixture rehearsal

The deterministic `knowledge silos` rehearsal exercised the required flow:

```text
VisualSourceDecision
→ AIImageRequest
→ GeminiImageGenerationRequest
→ fixture-only GoogleGeminiImageAdapter
→ safe local PNG materialization
→ image normalization plan
→ nine post-generation QC gates
→ native overlay binding
→ AIGenerationManifest + provenance/disclosure
```

Evidence:

```text
profile=STOCK_ASSISTED
route=AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY
model=gemini-3.1-flash-image
size=2K
aspect_ratio=16:9
estimated_cost_usd=0.101
actual_cost=null
transport=LOCAL_FIXTURE_ONLY
provider_call_made=false
attempts_consumed=0
duplicate_submit_prevented=true
post_generation_qc_verdict=PASS
native_overlay_bound=true
generated_evidence_authority=false
production_eligible=false
not_publishable=true
external_provider_fallback_used=false
```

All nine fixture QC gates passed: generated text artifact, fake UI/logo,
composition compliance, semantic match, visual-language match, technical image
fitness, crop safety, reuse similarity and rights/disclosure completeness. Raw
bytes and the fake signed URL were kept transient and purged; neither appears in
durable manifests.

## Persistence and migration decision

IMG1 reuses `ArtifactVersion.content` JSON, generic provider attempt,
idempotency, cost snapshot, budget, authorization and paid-call ledger storage,
plus versioned repository catalogs. The provider/stage columns already accept
generic keys, so no schema addition is required.

```text
PYTHONPATH=. .venv/bin/alembic heads
0037_ch1_flex (head)
```

No applied migration was edited, no empty migration was added and the repository
still has one Alembic head.

## Verification

```text
PYTHONPATH=. .venv/bin/python -m compileall -q app            PASS

Focused IMG1 suite                                            PASS
tests/test_img1_google_gemini_image_provider.py               26 passed in 1.35s

Required acceptance suite                                     PASS
tests/test_r3d10_runtime_lts_freeze.py
tests/test_ofv0_originality_format_validation.py
tests/test_vsr1_niche_aware_visual_routing.py
tests/test_img1_google_gemini_image_provider.py                96 passed, 1 warning in 99.63s

git diff --check                                              PASS
inventory/summary JSON parse                                  PASS
independent residual review                                   P0=0, P1=0
```

The DB-backed required suite used its isolated pytest database and did not touch
production data. Its single warning is the existing Starlette `TestClient`
deprecation.

An additional provider/config compatibility run produced 90 passes and one
baseline-only failure in
`test_current_tree_contains_no_unapproved_removed_provider_reference`: the scan
finds the word `Creatomate` in the immutable historical
`reports/pa1r_guarded_provider_smoke_report.md`. That report and the relevant
scan behavior predate IMG1; the test's only IMG1 diff is the new canonical
provider tuple. The historical evidence was correctly left unchanged. This is
classified as unrelated baseline debt, not an IMG1 P0/P1 or an acceptance-suite
failure.

Frontend checks were not required because no frontend file changed.

## Bounded self-repair

One offline repair/rerun cycle was used.

| Cycle | Failure classification | Repair | Files changed | Tests rerun | Result |
| --- | --- | --- | --- | --- | --- |
| 1 | `HASH_DETERMINISM_AND_INTEGRATION_GUARD_REVIEW` | Canonicalized UTC datetimes to `Z` for stable receipt hashes; separated fixture-planning readiness from paid execution evidence; bound cost evidence to the versioned catalog; tightened request fingerprint, output containment/PNG decode, transient purge, native-overlay and generic-manifest validation. No gate or threshold was weakened. | `app/contracts/ai_image.py`, `app/contracts/google_gemini_image.py`, `app/providers/google_gemini_image.py`, `app/services/google_gemini_image_rehearsal.py`, `app/services/r3d8.py`, readiness/docs/tests | Focused IMG1, required acceptance suite, compatibility suite, compile, Alembic head, JSON and diff checks | `PASS` |

## Non-actions and historical state

No Gemini Image, Pexels, ElevenLabs, Forced Alignment, Veo, Drive or YouTube
call occurred. No paid attempt, production render, `FinalMediaRef`,
`HumanUploadTask`, `UploadedVideo`, provider fallback, CH1-FLEX v2 activation,
PKG1 revision, MR1 mutation, prompt self-mutation or learning promotion
occurred. No secret value, raw image bytes or signed URL was logged or persisted.

```text
IMG1_ENTRY=PASS
IMG1_PROVIDER_ROUTE_REGISTRATION=PASS
IMG1_SETTINGS_READINESS=PASS
IMG1_MODEL_COST_CATALOG=PASS
IMG1_PROVIDER_NEUTRAL_IMAGE_REQUEST=PASS
IMG1_GEMINI_REQUEST_CONTRACT=PASS
IMG1_IMAGE_PROMPT_COMPILER=PASS
IMG1_REFERENCE_IMAGE_POLICY=PASS
IMG1_GEMINI_ADAPTER_FOUNDATION=PASS
IMG1_COST_APPROVAL_IDEMPOTENCY=PASS
IMG1_OUTPUT_SAFETY=PASS
IMG1_NATIVE_OVERLAY_BINDING=PASS
IMG1_PROVENANCE_DISCLOSURE=PASS
IMG1_READ_ONLY_READINESS=PASS
IMG1_OFFLINE_FIXTURE_REHEARSAL=PASS
IMG1_SELF_REPAIR_CYCLES=1
IMG1_PROVIDER_EXECUTION=DISABLED
IMG1_DATABASE_MIGRATION=NOT_REQUIRED
IMG1_FINAL=PASS
MR1_EXECUTION=ON_HOLD
PROCEED_TO_MR1=false
PROCEED_TO_VQC1=true
```
