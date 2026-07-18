# Niche-Aware Visual Routing Change Specification

Status: implementation-ready after `VISUAL-IMPACT-REVIEW`
Decision date: 2026-07-17
Scope: VSR1, IMG1 and VQC1 design mapping
Database migration decision: `NOT_REQUIRED`

## Architectural decision

VCOS will use meaning-first, niche-aware and deterministic scene routing. Niche policy provides defaults; a complete scene requirement and global invariants determine one preferred route. A provider search failure never reclassifies the scene and never opens a paid provider automatically.

`NativeFFmpegRenderer` remains final composition authority. Generated or stock pixels are never exact-text, exact-number, product/UI or evidence authority.

This design extends the current artifact-first architecture:

```text
Channel policy defaults
  -> strict SceneVisualIntent projection
  -> route eligibility gates
  -> route-aware SceneSourceDecisionContract
  -> provider-neutral AssetRequest planning
  -> provider-specific acquisition/generation (later IMG1)
  -> existing manifests/QC
  -> NativeRenderPlan and NativeFFmpegRenderer
```

No per-niche pipeline, renderer or service branch is permitted.

## Existing authorities to reuse

- channel policy: `app/contracts/channel_policy.py::ChannelVisualStrategyProfile`, compiled by `app/services/profile_compiler.py::ChannelProfileCompiler`;
- scene intent/evaluation: `app/contracts/visual_direction.py::SceneVisualIntent`, `VisualDirectionContract`, `VisualAssetEvidence`;
- persisted scene decision: `app/contracts/m6.py::SceneSourceDecisionContract`;
- plan/manifests: `VisualPlanContract`, `SceneManifestContract`, `AssetManifestContract`, `AssetRequest`, `CompiledAssetRequestPlan`;
- renderer: `app/contracts/native_renderer.py::NativeRenderScene`, `NativeRenderPlan`, `CompiledNativeRenderManifest`;
- provider-neutral AI provenance: `app/contracts/asset_acquisition.py::AIGenerationManifest`;
- cost/approval/idempotency/attempt evidence: `app/db/models/r3d8.py` and `app/services/r3d8.py`;
- artifact persistence: `app/db/models/workflow.py::ArtifactVersion.content` JSONB.

Historical M6/CH1/PKG1 payloads remain valid legacy inputs. Strict route-aware execution requires new versioned evidence and never silently upgrades a legacy placeholder decision.

## Change inventory

### 1. Niche visual-source profile

- Existing component: `ChannelVisualStrategyProfile` in `app/contracts/channel_policy.py`; catalog item in `config/channel_scoped_policy_catalog.yaml`.
- Change: `EXTEND_EXISTING`, but VSR1 first introduces an inactive versioned routing-policy catalog/fixture. CH1-FLEX v1 is not edited or activated.
- Typed value set: `STOCK_NATIVE`, `STOCK_ASSISTED`, `GENERATED_EDITORIAL_FIRST`, `AUTHORITY_ASSET_FIRST`.
- Producer: channel policy author/compiler.
- Consumer: strict scene planning and `VisualSourceRouter`.
- Persistence: versioned repo config; later compiled snapshot JSONB.
- Migration/API impact: none in VSR1; later CH1-FLEX v2 compiler/read-model extension.
- Tests: typed value set, fixture `small-team-ai=STOCK_ASSISTED`, no channel-key branch.
- Backward compatibility: missing niche profile means `LEGACY_VISUAL_ROUTING`; it must block strict routing, not default to Pexels.

### 2. Scene feature/requirement contract

- Existing component: `app/contracts/visual_direction.py::SceneVisualIntent` plus M6/PKG1 scene projections.
- Change: `EXTEND_EXISTING` using a strict route-aware projection, not a replacement of historical payloads.
- Typed fields: source segment IDs; niche profile; scene class; narrative function; scene meaning/editorial intent; filmability, stock-searchability, required-specificity and custom-composition scores; exact text/number dependencies; named workflow flag; diagram clarity advantage; brand/product dependency and specificity; evidence truth and authorized-asset state; identity/recurring identity; human action; motion value; aspect, minimum resolution and crop safety; adjacent intent refs; content hash.
- Validation: scores `0.0..1.0`; critical booleans required explicitly; minimum resolution at least 1080p; content hash deterministic.
- Producer: visual planning/classification.
- Consumer: completeness, evidence, diagram, Pexels, AI-image and router services.
- Persistence: route-aware VisualPlan/SceneManifest ArtifactVersion JSON.
- Migration/API impact: none; optional read-only preview may serialize it.
- Tests: missing critical values block before any provider planner.
- Backward compatibility: base `SceneVisualIntent` stays readable; strict projection is required only for new route-aware artifacts.

### 3. VisualSourceDecision

- Existing component: `app/contracts/m6.py::SceneSourceDecisionContract`.
- Change: `EXTEND_EXISTING`; a route-aware v2 contract/subtype must retain the existing decision domain.
- Typed fields: decision version, scene/profile, scalar preferred route, allowed and forbidden fallback routes, fallback class, confidence/reasons, input snapshot, policy ref/version/hash, cost class, provider-execution and human-approval flags, status/block reasons, content hash.
- Producer: `VisualSourceRouter`.
- Consumer: VisualPlan/SceneManifest, AssetRequestCompiler, asset manifests, preview/read model and later provider execution plans.
- Persistence: new ArtifactVersion content and existing scene/asset JSONB.
- Migration/API impact: none; read-only preview only.
- Tests: exactly one preferred route; deterministic identical hash; no allowed/forbidden overlap.
- Backward compatibility: legacy `preferred_source`/`fallback_order` values remain readable and are labeled `LEGACY_VISUAL_ROUTING`.

### 4. Pexels eligibility

- Existing component: `PexelsUsagePolicyGate`, `PexelsQueryPlanner`, `AssetRequest` safeguards.
- Change: `ADD_NEW` pre-provider `PexelsEligibilityAssessment/Gate`; keep current post-search semantic/ranking gates.
- Outputs: `PEXELS_ELIGIBLE`, `PEXELS_SUPPORTING_ONLY`, `PEXELS_LOW_CONFIDENCE`, `PEXELS_PROHIBITED` plus deterministic reason codes.
- Producer: route planning from strict scene features and versioned thresholds.
- Consumer: router; Pexels planner later requires decision/assessment refs.
- Persistence: embedded eligibility evidence in decision/asset plan JSON.
- Migration/API impact: none.
- Tests: thresholds, mechanism/evidence/product/exact-text/named-workflow blocks; supporting-only distinction; no API calls.
- Backward compatibility: existing Pexels plans stay historical; strict new plans require assessment evidence.

### 5. Evidence truth source gate

- Existing component: factual risk/evidence role, ClaimEvidenceLedger, `EvidencePolicy.stock_is_not_factual_evidence`, `VisualAssetEvidence.fake_ui_used_as_evidence`.
- Change: `EXTEND_EXISTING` with a focused deterministic gate.
- Rule: evidence/product/UI/document truth at or above policy threshold requires an authorized asset; missing authorization returns `UNRESOLVED_BLOCK`.
- Producer: scene planner plus authorization/provenance evidence.
- Consumer: router and rights/disclosure plan.
- Persistence: decision input snapshot/reason codes and existing evidence refs.
- Migration/API impact: none.
- Tests: no stock/generation substitute; human-supplied does not imply authorized.

### 6. Diagram suitability gate

- Existing component: M6 mechanism/process/data -> diagram rule; native diagram/motion treatments.
- Change: `EXTEND_EXISTING` as typed gate evidence.
- Inputs: relation/sequence/state/labels/comparison/before-after/data-flow/exact-number features represented by scene requirements; initial threshold `diagram_clarity_advantage>=0.60`, motion variant at `motion_semantic_value>=0.70`.
- Producer/consumer: route planning -> router/native plan.
- Persistence: decision reasons and VisualPlan.
- Migration/API impact: none.
- Tests: named workflows and diagram-worthy mechanisms cannot route to generic stock/art.

### 7. AI-image eligibility gate

- Existing component: no image route; AI-hero video is not reusable as image eligibility.
- Change: `ADD_NEW`, provider-neutral and non-executable in VSR1.
- Outputs: allowed, native-overlay-required, low-confidence or prohibited;
  historical VSR1 evidence retains `IMAGE_PROVIDER_ROUTE_NOT_YET_ACTIVE`.
  IMG1 registers the route without rewriting that evidence, and execution
  remains false.
- Producer: strict scene requirements and global policy.
- Consumer: router plus IMG1 fixture-only request compilation from an approved
  route decision.
- Persistence: assessment evidence in decision JSON.
- Migration/API impact: none.
- Tests: evidence/UI/product/quote/likeness blocks; custom composition plans an image route without network execution.

### 8. Exact-text/native-overlay contract

- Existing component: `NativeRenderScene`, `CompiledNativeRenderManifest.overlay_schedule`, canonical ASS caption path, M6 legacy overlay fields.
- Change: `EXTEND_EXISTING`.
- Typed fields: exact-text/number flags; generated text/logo/fake-UI prohibitions; normalized text-safe regions; reserved region IDs; native-overlay-required; typed overlay plan bound to authoritative content refs.
- Producer: route-aware visual planning/native plan compiler.
- Consumer: `NativeMotionCompiler`, VQC1 and `NativeFFmpegRenderer`.
- Persistence: NativeRenderPlan/compiled manifest JSON.
- Migration/API impact: none.
- Tests: exact authoritative content cannot be generated pixels; region geometry valid; required overlay plan cannot be absent.
- Backward compatibility: old plans remain legacy; VSR1 does not render or rewrite them.

### 9. Deterministic VisualSourceRouter

- Existing component: `SceneSourceDecisionService` is the coarse legacy authority.
- Change: `EXTEND` via a dedicated provider-neutral service returning the route-aware decision contract.
- Decision order: completeness -> archive reuse -> evidence truth -> diagram/native motion -> exact text/number -> Pexels eligibility -> motion-valuable Veo route -> AI-image custom composition -> block.
- Producer: policy plus strict scene requirement and explicit archive assessment.
- Consumer: read-only preview and future plan/compiler integration.
- Persistence: decision set as JSON ArtifactVersion later; no mutation in VSR1 preview.
- Migration/API impact: none.
- Tests: deterministic hash, fixture matrix, Pexels failure cannot influence output, no provider import/call.
- Backward compatibility: service never mutates or silently upgrades M6 decisions.

### 10. Routing evidence in asset manifests

- Existing component: `AssetManifestContract`, `AssetRequirementContract`, `StockSourceManifest`, `AIGenerationManifest`.
- Change: `EXTEND_EXISTING` planning evidence with preferred/actual route, decision ref/hash, eligibility refs, reason codes, fallback class, evidence truth classification and native-overlay flag.
- Producer: router/asset planner; actual route remains null in VSR1.
- Consumer: acquisition, QC, archive and human review.
- Persistence: existing JSONB and provider manifests.
- Migration/API impact: none.
- Tests: no fabricated provider URL/response/actual route.

### 11. Provider-neutral AI image request (IMG1)

- Existing component: `AssetRequest`, `CompiledAssetRequestPlan`, provider-neutral `AIGenerationManifest`; `AIHeroAssetRequest` is video-specific.
- Change: `ADD_NEW` `AIImageRequest` value object and extend existing
  `AIGenerationManifest`. Do not overload `AIHeroAssetRequest`.
- Typed fields: route decision ref/hash, scene/prompt/negative constraints, image size/aspect, authorized reference hashes, safe regions/overlay requirement, cost/approval/idempotency refs and execution disabled state.
- Producer: `AIImageRequestBuilder` and `ImagePromptCompiler`.
- Consumer: `GoogleGeminiImageAdapter` fixture boundary.
- Persistence: ArtifactVersion/workspace JSON plus existing AIGenerationManifest.
- Migration/API impact: none.
- Tests: provider neutrality, reference authorization, no exact text authority, default non-executable.

### 12. Gemini Image adapter boundary (IMG1)

- Existing component: `GoogleVeoAdapter` must remain isolated; M2/R3D8 generic provider infrastructure is reusable.
- Change: `ADD_NEW` `google_gemini_image` provider contract/adapter; do not restore or clone Veo code blindly.
- Settings: explicit provider/model/size/aspect/real-generation/fixture flags;
  real generation defaults false and fixture-only defaults true; reuse only
  `GEMINI_API_KEY`.
- Registry/capability/readiness: add provider type IMAGE and `AI_IMAGE_GENERATION` route under current M2/provider-registry conventions.
- Persistence: existing generic provider job/ledger plus
  `AIGenerationManifest(media_kind=STILL_IMAGE)`.
- Migration/API impact: none under the current M2/R3D8 route; do not seed the legacy M10.2 AI-image role.
- Tests: route isolation, no Veo flag/approval inheritance, fake transport by
  default and no network call.

### 13. Image cost, approval and idempotency (IMG1)

- Existing component: `CostEstimateSnapshot`, `HumanPaidRenderApproval`, `ProviderIdempotencyKey`, `PaidAttemptLimitRecord`, `PaidProviderCallLedger`.
- Change: `EXTEND_EXISTING` service recognition and provider-estimate JSON; add image price catalog, not a table.
- Producer: catalog estimator and paid-boundary preflight.
- Consumer: image adapter.
- Persistence: current R3D8 tables.
- Migration/API impact: none.
- Tests: one attempt, exact approval scope, idempotent duplicate prevention, kill switches default closed.

### 14. Image post-generation QC (VQC1)

- Existing component: `VisualAssetEvidence`, semantic/continuity gates, `TechnicalMediaQC`, `CreativePerceptualMediaQC`, rights completeness.
- Change: `EXTEND_EXISTING` with generated-text/OCR, fake UI/logo, composition-safe-region, image fitness and crop-safety gates.
- Producer: downloaded image metadata/raster analysis plus route/overlay contracts.
- Consumer: AIGenerationManifest, NativeRenderPlan admission and human review.
- Persistence: QC refs and gate evidence in existing manifests/artifacts.
- Migration/API impact: none.
- Tests: generated text/number/fake UI rejection, crop/overlay safety, technical PASS never implies creative/human PASS.

### 15. Archive/provenance additions (IMG1/VQC1)

- Existing component: `AIGenerationManifest`, `ProductionArchiveManifest`, archive roles and Drive receipt.
- Change: `EXTEND_EXISTING` with media kind, source route, dimensions, authorized reference hashes, generation safety/overlay/QC refs and disclosure state.
- Producer: adapter/QC/archive builder.
- Consumer: human review, disclosure and Drive verification.
- Migration/API impact: none.
- Tests: complete before purge; no raw URLs persisted.

### 16. CH1-FLEX v2 (later)

- Existing component: `ChannelProfileVersion` and `CompiledChannelPolicySnapshot` lifecycle.
- Change: append a new approved version after VSR1/IMG1/VQC1/canary evidence. Do not mutate v1 or activate the existing unrelated draft v2 as-is.
- Required binding: niche profile, routing policy/hash, Pexels/image/native overlay policy, image cost/attempt cap and provenance/disclosure requirements.
- Persistence: existing profile/snapshot JSONB.
- Migration/API impact: none.
- Tests: immutable v1, future-project-only activation, exact compiler hash and operator approval.

### 17. PKG1 visual revision (later)

- Existing component: current 22-artifact package and exact ApprovalDecision bindings.
- Change: append revisions to visual/provider/cost/disclosure artifacts while reusing editorial artifacts.
- Producer: revised PKG1 package builder/operator workflow.
- Consumer: new MR1 readiness/approvals.
- Persistence: new ArtifactVersion and ApprovalDecision rows.
- Migration/API impact: none.
- Tests: prior versions unchanged; new decision refs/hashes exact; no provider execution during package revision.

### 18. Paid image canary (later)

- Existing component: PA1R/CQR1 guarded provider smoke patterns and R3D8 firewall.
- Change: one explicit image-only canary after IMG1/VQC1 offline PASS and operator approval.
- Required evidence: exact route decision, image request, catalog estimate, approval, idempotency, attempt limit, provider boundary, provenance, VQC1, native overlay verification, human review and Drive archive verification.
- Scope: exactly one paid image; no MR1, publish or automatic retry.

## Route ownership matrix

| Route | Acquisition/generation owner | Render owner | Eligibility | Fallback class |
| --- | --- | --- | --- | --- |
| `ARCHIVED_ASSET_REUSE` | archive/reuse lookup | NativeFFmpegRenderer | semantic/rights/cooldown/originality/truth | explicit/no paid |
| `PEXELS_VIDEO` | Pexels video boundary | NativeFFmpegRenderer | Pexels eligibility | `PEXELS_ONLY` |
| `PEXELS_PHOTO` | future Pexels photo boundary | NativeFFmpegRenderer | Pexels eligibility | `PEXELS_ONLY` |
| `AI_GENERATED_IMAGE` | future image adapter | NativeFFmpegRenderer | AI-image eligibility + future provider approval | `AI_IMAGE_PRIMARY` |
| `AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY` | future image adapter | NativeFFmpegRenderer | AI eligibility + overlay contract | `AI_IMAGE_PRIMARY` |
| `NATIVE_DIAGRAM` | native planner | NativeFFmpegRenderer | diagram suitability | `NATIVE_ONLY` |
| `NATIVE_MOTION_GRAPHIC` | NativeMotionCompiler | NativeFFmpegRenderer | diagram/motion suitability | `NATIVE_ONLY` |
| `EDITORIAL_TEXT_GRAPHIC` | native planner | NativeFFmpegRenderer | exact text/number | `NATIVE_ONLY` |
| `AUTHORIZED_UI_OR_PRODUCT_ASSET` | authorized intake/archive | NativeFFmpegRenderer | evidence truth + authorization | `AUTHORIZED_ASSET_ONLY` |
| `HUMAN_SUPPLIED_ASSET` | human intake | NativeFFmpegRenderer | explicit rights/authorization | `AUTHORIZED_ASSET_ONLY` |
| `VEO_TEXT_TO_VIDEO` | GoogleVeoAdapter | NativeFFmpegRenderer | high motion value + Veo gates | `NO_FALLBACK` |
| `VEO_IMAGE_TO_VIDEO` | future explicit Veo reference-image extension | NativeFFmpegRenderer | high motion + reference rights | `NO_FALLBACK` |
| `UNRESOLVED_BLOCK` | none | none | no safe route | `NO_FALLBACK` |

## Migration boundary

No migration is allowed or needed for the approved path:

- typed policy: repository YAML and CompiledChannelPolicySnapshot JSON;
- route-aware artifacts: ArtifactVersion/VisualPlan/SceneManifest/AssetManifest JSONB;
- future provider cost/attempts: generic R3D8 JSON/provider-key/stage columns.

Do not add an empty migration, dedicated image tables, image-specific attempt ledger or duplicate cost table. Do not use legacy M10.2 media-role tables for the new image provider. If a later decision requires that integration, separately approve a forward migration to widen its check constraints.

## Backward compatibility and historical behavior

- `CH1_FLEX_v1`, `PKG1_v1` and all existing approvals stay immutable historical evidence.
- Legacy visual plans/scene decisions are readable as `LEGACY_VISUAL_ROUTING`.
- Strict new provider planning requires a route-aware decision ref/hash and eligibility evidence.
- No old Pexels decision is silently promoted to VSR1.
- New fields must use explicit defaults only where historical reading is safe; routing-critical absence blocks strict routing.
- No task in VSR1 may activate a profile, provider, paid call, render, archive upload or publish action.

## Verification plan

VSR1 focused tests must cover the four profiles, all routes, completeness, Pexels/evidence/diagram/AI gates, exact-text authority, fallback prohibitions, archive rights/originality, Veo motion threshold, deterministic content hash, small-team fixture and network/provider absence.

Regression scope follows the repository gate:

```text
tests/test_r3d10_runtime_lts_freeze.py
tests/test_ofv0_originality_format_validation.py
tests/test_ch1_flex_channel_policy.py
tests/test_pkg1_first_production_package.py
tests/test_vsr1_niche_aware_visual_routing.py
```

Also run one Alembic-head check, application compile, JSON/YAML parsing and `git diff --check`. Frontend checks are not required because VSR1 changes no frontend file.

## Rollout gate

```text
VSR1 -> IMG1 -> VQC1 -> offline fixtures -> one paid image canary
-> human full-watch + Drive verification -> CH1-FLEX v2
-> PKG1 visual revision -> new MR1 approval -> MR1
```

Any new duplicate-domain ambiguity, provider-route ambiguity or need to make the legacy M10.2 matrix authoritative blocks the affected milestone for operator decision.
