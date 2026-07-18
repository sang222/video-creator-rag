# Niche-Aware Visual Source Routing

## Decision and scope

VCOS routes each scene by meaning, truth requirements and composition needs. A
niche profile supplies defaults, but it never selects a provider. A complete
scene requirement is evaluated against a versioned policy and produces exactly
one deterministic preferred route.

VSR1 is a provider-neutral planning boundary. It does not search Pexels, call
Veo, generate an image, render media, upload to Drive or publish to YouTube.
`NativeFFmpegRenderer` remains the only final composition authority.

## Canonical flow

```text
ChannelVisualStrategyProfile + inactive routing policy
  -> route-aware SceneVisualIntent projection
  -> completeness, archive, truth, diagram and eligibility gates
  -> route-aware SceneSourceDecisionContract
  -> AssetRequestCompiler and planning manifests
  -> provider acquisition or generation in a later authorized milestone
  -> NativeRenderPlan / NativeMotionCompiler
  -> NativeFFmpegRenderer
```

The route-aware decision extends the existing
`SceneSourceDecisionContract`; it is not a parallel `VisualRealization` domain.
Resolved asset references, asset manifests and `NativeRenderPlan` continue to
record realization. Identical scene inputs and policy hashes must produce the
same decision content hash.

## Niche profiles

The typed profile set expresses editorial defaults:

| Profile | Meaning | Default route families |
| --- | --- | --- |
| `STOCK_NATIVE` | Observable reality is dense and custom composition, exact text and diagrams are uncommon. | archive reuse, Pexels video or photo |
| `STOCK_ASSISTED` | Stock provides context, while mechanisms, labels and relationships remain native or editorial. | native diagram/motion/text, Pexels, AI image with native overlay |
| `GENERATED_EDITORIAL_FIRST` | Custom metaphor or composition is central and stock searchability is low. | provider-neutral AI image, AI image with native overlay, native motion |
| `AUTHORITY_ASSET_FIRST` | Product, UI, document or evidence truth is authoritative. | authorized or human-supplied asset, native diagram, unresolved block |

Profiles never bypass scene gates and do not create a per-niche pipeline,
renderer or business-service branch.

## Scene requirement contract

New strict plans project the existing `SceneVisualIntent` into a route-aware
requirement containing:

- scene, segment, profile, class, narrative function, meaning and editorial
  intent;
- filmability, stock searchability, specificity, custom composition, diagram
  advantage and motion-value scores in the closed range `0.0..1.0`;
- explicit exact-text, exact-number, workflow-label, product, evidence,
  authorization, identity, recurring-identity and human-action requirements;
- aspect ratio, minimum resolution, crop safety and adjacent-intent refs;
- a deterministic content hash.

Routing-critical booleans are explicit. Missing meaning, narrative function,
truth classification, exact-text status, product specificity or output shape
blocks in `VisualRealizationCompletenessGate` before a provider planner can be
reached. New strict output is at least 1080p and cannot silently downgrade.

## Route taxonomy

Every decision has one scalar preferred route, explicit allowed and forbidden
fallbacks, and a typed fallback class.

| Family | Routes |
| --- | --- |
| Existing/reused media | `ARCHIVED_ASSET_REUSE`, `PEXELS_VIDEO`, `PEXELS_PHOTO` |
| Provider-neutral generated still | `AI_GENERATED_IMAGE`, `AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY` |
| Native editorial | `NATIVE_DIAGRAM`, `NATIVE_MOTION_GRAPHIC`, `EDITORIAL_TEXT_GRAPHIC` |
| Truth-authorized input | `AUTHORIZED_UI_OR_PRODUCT_ASSET`, `HUMAN_SUPPLIED_ASSET` |
| Motion generation | `VEO_TEXT_TO_VIDEO`, `VEO_IMAGE_TO_VIDEO` |
| Fail closed | `UNRESOLVED_BLOCK` |

There is no `AUTO` route. IMG1 registers `google_gemini_image` as the
approved, distinct IMAGE planning boundary, but the route remains
non-executable and fixture-only.

## Deterministic decision order

`VisualSourceRouter` evaluates semantics rather than provider priority:

1. Require a complete route-aware scene contract.
2. Accept archive reuse only after semantic, rights, cooldown, originality and
   truth-freshness checks pass.
3. Route evidence, actual UI, product or document truth to an authorized asset;
   block when it is unavailable.
4. Prefer a native diagram or motion graphic when relationships, sequence,
   labels or state changes communicate the meaning more clearly.
5. Keep exact text and numbers native, using an editorial graphic or a generated
   foundation with a native overlay only when custom composition is justified.
6. Evaluate Pexels filmability and searchability against hard truth, text,
   specificity, composition and recurring-identity exclusions.
7. Use a Veo planning route only when motion has high semantic value and a still
   or native motion treatment is insufficient.
8. Use a provider-neutral AI-image planning route only for an eligible custom
   composition.
9. Otherwise emit `UNRESOLVED_BLOCK`.

Pexels search results are not an input to this order. A weak or empty search
cannot reclassify a scene or open a paid AI route.

## Gates and evidence

`PexelsEligibilityGate` distinguishes eligible, supporting-only,
low-confidence and prohibited outcomes without an API call.
`EvidenceTruthSourceGate` rejects stock, generated fake UI and illustrative
substitutes where source truth is required. A human-supplied asset is not
authorized until its rights and authorization evidence pass.
`DiagramSuitabilityGate` makes diagram clarity authoritative over generic
imagery. `AIImageEligibilityGate` can plan an image route. Historical VSR1
evidence retains its inactive-provider reason; IMG1 adds route registration
without rewriting that evidence, and execution remains disabled.

The route-aware decision records policy ref/version/hash, the feature snapshot,
gate refs, confidence, reason and block codes, cost class, approval requirement,
provider-execution requirement and its own hash. Asset planning evidence carries
the preferred route, nullable actual route, decision ref/hash, fallback class,
truth classification and native-overlay requirement. VSR1 fabricates no asset,
provider response, URL or actual route.

## Exact text and native overlays

Native rendering owns headlines, numbers, percentages, workflow and diagram
labels, tool/product names, quotes, citations, CTA, data values and UI text.
Stock or generated pixels may be a visual foundation, never the authority.

Strict plans declare exact-text and exact-number requirements; generated text,
logos and fake UI prohibitions; normalized text-safe and reserved-overlay
regions; and a required native-overlay plan bound to authoritative content
refs. Captions retain their canonical timeline and ASS authority. The compiled
overlay schedule must preserve those bindings and must not collide with caption
safe regions. VSR1 defines and validates this contract but does not render it.

## Archive, Veo and provider boundaries

Archive reuse is a source decision, not a binary fetch. Its evidence preserves
semantic fit, rights scope, reuse count, cooldown, originality/repetition and
truth freshness.

Veo remains isolated behind the existing `GoogleVeoAdapter`. VSR1 only emits
`VEO_TEXT_TO_VIDEO` or `VEO_IMAGE_TO_VIDEO` when motion is meaning-bearing,
evidence truth is low and future cost policy permits it. Diagram-clearer,
low-motion and truth-authoritative scenes cannot use Veo. VSR1 does not modify
or invoke the adapter.

AI-image eligibility never implies network permission. IMG1 adds the separate
`google_gemini_image` route, `gemini-3.1-flash-image` catalog and
provider-neutral request/manifest boundary while reusing generic cost,
approval, idempotency and attempt ledgers plus `AIGenerationManifest`.
`GEMINI_API_KEY` is shared as credential ownership only; Veo and Gemini Image
retain separate provider keys, capabilities, costs, attempts and approvals.

The generated raster is an editorial foundation. `AIImageRequest` and
`NativeOverlayImageBinding` preserve the VSR1 decision/hash and require native
safe-region bindings for exact text or numbers. IMG1 permits only local fixture
transport; a future paid canary remains a separate authorization.

## Compatibility and persistence

Routing policy lives in versioned repository configuration. Strict decisions,
plans and manifest evidence use existing `ArtifactVersion` JSON content and
compiled policy JSON. No database migration is required.

Historical M6, PA1R, CQR1, CH1-FLEX v1 and PKG1 v1 artifacts remain immutable
and readable as `LEGACY_VISUAL_ROUTING`. A new provider-aware plan must carry a
route decision ref/hash and eligibility evidence. Legacy Pexels roles are never
silently promoted into VSR1, and a missing strict requirement never defaults to
Pexels.
