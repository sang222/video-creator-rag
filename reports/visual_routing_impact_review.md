# VISUAL-IMPACT-REVIEW - Repository-Grounded Audit

Date: 2026-07-17
Repository: `/Users/sangss/Desktop/video-creator-rag`
Reviewed HEAD: `3b157f9dc4d1c303eb90f7d0eeddac007c2ac81e` on `main`
Research input: `VCOS Niche-Aware Visual Source Routing and AI Cloud Image Provider Selection.pdf` (43 pages)

## Executive decision

The research direction is compatible with the repository, but its repository map was intentionally `UNVERIFIED`. The real repository already contains most of the durable foundations: channel policy snapshots, a legacy scene source-decision contract, provider-neutral visual intent/evidence, native render plans, generic artifact JSONB, generic cost/approval/idempotency/attempt ledgers, Pexels video provenance and a provider-neutral `AIGenerationManifest`.

VSR1 therefore requires a bounded extension, not an architecture rewrite or migration. The main correction is to make scene meaning/requirements authoritative before the Pexels or Veo planners are reachable.

`CH1_FLEX_v1` and `PKG1_v1` remain immutable historical passes. They are superseded for future production because neither their frozen policy nor exact MR1 approval covers the new visual-source decision set or Gemini Image route. MR1 remains on hold.

## Repository identity

| Field | Evidence |
| --- | --- |
| repository path | `/Users/sangss/Desktop/video-creator-rag` |
| worktree | Git worktree, verified by `git rev-parse --show-toplevel` |
| branch/default branch | `main` / `origin/main` |
| HEAD | `3b157f9dc4d1c303eb90f7d0eeddac007c2ac81e` |
| remote | `https://github.com/sang222/video-creator-rag.git`; no embedded credentials |
| visibility | not determinable from local metadata without network |
| initial worktree | dirty with 41 pre-existing entries; audit did not clean or rewrite them |

`REPOSITORY_IDENTITY=PASS`.

## Research-to-repository conflict register

The PDF says repository identity and all paths are blocked because its research session had no repository access. That is not a product conflict; it is an evidence-access limitation that this audit resolves.

Repository truth overrides these target-design assumptions:

1. `SceneSourceDecisionContract` and `SceneSourceDecisionService` already exist in `app/contracts/m6.py` and `app/services/m6.py`; VSR1 must extend/modernize that authority rather than create an unrelated decision domain.
2. `AIGenerationManifest` already exists in `app/contracts/asset_acquisition.py`; IMG1 must extend it, not add `AIImageGenerationManifest`.
3. Generic provider ledgers already exist in `app/db/models/r3d8.py`; IMG1 must not add an image-only attempt ledger.
4. Native exact-text rendering is complete only for canonical captions. The non-caption `overlay_schedule` exists but is currently emitted empty by `NativeMotionCompiler`.
5. Pexels is already restricted to optional/supporting-only in approved CH1 policy, but the legacy global provider role still names it `FREE_VISUAL_FALLBACK_PROVIDER`, and strict scene eligibility is absent.
6. Pexels photo acquisition is absent; current typed/query/runtime path is video-only.

## Current visual path

Current production and compatibility layers are fragmented but traceable:

```text
ChannelProfileVersion / CompiledChannelPolicySnapshot
  -> ChannelVisualStrategyProfile + ProviderUsagePolicy
  -> PKG1 VisualDirectionContract / visual_plan ArtifactVersion
  -> NativeRenderPlan / NativeRenderScene visual_treatment
  -> AssetRequestCompiler
       NATIVE treatments -> NATIVE_VISUAL
       STOCK_VIDEO       -> SUPPORTING_STOCK -> PEXELS
       AI_HERO_VIDEO     -> AI_HERO          -> GOOGLE_VEO
  -> PexelsQueryPlanner or VeoPromptCompiler
  -> provider-specific provenance / resolved asset refs
  -> NativeMotionCompiler
  -> CompiledNativeRenderManifest
  -> NativeFFmpegRenderer
  -> TechnicalMediaQC + CreativePerceptualMediaQC
  -> human final review
  -> ProductionArchiveManifest / DriveArchiveReceipt
```

Historical M6 also persists `VisualPlanSnapshot`, `SceneManifestSnapshot` and `AssetManifestSnapshot` JSONB. Its `SceneSourceDecisionService` routes high factual risk to screenshot placeholders and mechanism/process/data to diagrams, but does not have the target feature scores, fallback classes or route taxonomy.

There is no single current `VisualSourceRouter`, `VisualRealization` receipt or strict decision set consumed by all provider planners.

## Pexels default and fallback audit

Pexels has two different current meanings:

- global compatibility/provider configuration: `FREE_VISUAL_FALLBACK_PROVIDER` defaults to `pexels_api` in `app/services/m2.py`;
- approved CH1 policy: Pexels is optional, `SUPPORTING_ONLY`, has zero minimum quota, cannot be factual evidence or a recurring host, and is not the native backbone.

The strict conclusion is therefore that a global fallback default is present in legacy/provider wiring even though the approved channel policy does not treat Pexels as the default for every scene.

| Check | Result | Evidence |
| --- | --- | --- |
| scene can reach Pexels before feature eligibility | yes | `AssetRequestCompiler` accepts `STOCK_VIDEO`; `PexelsQueryPlanner` validates role/query, not meaning-first features |
| failed Pexels search opens paid AI | no | Pexels flows fail/return review; no fallback executor; Veo external fallback is false |
| generic stock can satisfy coverage if upstream misclassifies | yes | `VisualCoverageGate` checks coverage/source allowlist, not evidence truth or mechanism suitability |
| declared factual evidence may use Pexels | no | channel policy, `AssetRequest`, R3D8 gates and tests forbid it |
| latent evidence risk remains | yes | evidence policy is derived from the selected role rather than a required scene truth classification |
| stock ranges are quotas | no | minimum quota zero; ranges guidance only; ratio-only selection and forced alternation false |
| Pexels photo path exists | no | `PexelsQueryPlan.endpoint` is `/v1/videos/search`; candidate/rendition contracts require video duration |

```text
PEXELS_GLOBAL_DEFAULT_PRESENT=true
AUTO_PEXELS_TO_PAID_AI_FAILOVER_PRESENT=false
PEXELS_MECHANISM_SCENE_RISK=true
PEXELS_EVIDENCE_SCENE_RISK=true
PEXELS_QUOTA_DRIVEN_SELECTION_RISK=false
```

## Current source-decision authority

| Target concept | Repository decision | Existing authority |
| --- | --- | --- |
| niche visual-source profile | `EXTEND_EXISTING` | `ChannelVisualStrategyProfile` |
| visual source decision | `EXTEND_EXISTING` | `SceneSourceDecisionContract`; current PKG1 `SceneVisualIntent.source_role` is the package projection |
| Pexels eligibility assessment | `ADD_NEW` | current gates are coarse/post-classification |
| AI-image eligibility | `ADD_NEW` | AI hero is video-specific; no image route |
| evidence truth classification | `EXTEND_EXISTING` | factual risk, claim ledger, evidence role/policy and `VisualAssetEvidence` |
| diagram suitability | `EXTEND_EXISTING` | mechanism/process/data already prefer diagram; add typed inputs/reasons |
| fallback class | `EXTEND_EXISTING` | current `fallback_order` lists need typed allowed/forbidden semantics |
| standalone VisualRealization | `DO_NOT_BUILD` | resolved asset refs, manifests and NativeRenderPlan already cover realization |

VSR1 may introduce typed value objects and gates, but the durable scene decision must remain an extension of `SceneSourceDecisionContract`, not a second source of truth.

## Exact-text and native-overlay authority

Architecture authority is native, but runtime completeness differs by content type:

- canonical captions: fully bound by timeline ref/hash, compiled to ASS, checksummed and rendered by FFmpeg;
- headline, CTA, citations, workflow/tool/product/UI/diagram labels: native authority is intended, but the current `NativeRenderScene` has no typed overlay payload/regions and `overlay_schedule` is empty;
- exact numbers/percentages: semantic truth can be bound to claim/script artifacts, but there is no typed native pixel binding;
- non-native stock/hero assets: prompt/planning/ranking policies reject or penalize embedded text/logo/fake UI;
- generated-image OCR/fake-UI QC: absent because no image generation route exists.

```text
EXACT_TEXT_AUTHORITY=Native renderer; fully operational today only for canonical captions
EXACT_NUMBER_AUTHORITY=Native renderer intended; typed display binding absent
FAKE_UI_REJECTION=Conditional hard conflict for evidence; explanatory native UI simulation remains allowed
GENERATED_TEXT_REJECTION=Prompt/policy prevention only; no image-raster post-generation gate
```

The current contracts can be extended safely with normalized `text_safe_regions`, `reserved_overlay_regions`, `native_overlay_required`, a typed `native_overlay_plan` and explicit generated-text/logo/fake-UI prohibitions. No fields were added during Prompt 1.

## Google image/provider route state

Current Google media support is exclusively Veo video through `GoogleVeoAdapter`. `GEMINI_API_KEY` is the Veo transport credential; it is not an approved image route. There is no image model setting, adapter, capability row, cost catalog, provider readiness card, execution flag or paid canary flag.

Reusable foundations do exist:

- generic provider type `IMAGE` in the M4 provider registry schema/catalog;
- provider-neutral `AIGenerationManifest` with provider/model/request/prompt/operation/output checksum/cost/attempt/QC/disclosure fields;
- generic `CostEstimateSnapshot.provider_estimates_json`;
- generic approval stage JSON, idempotency keys, paid-call ledger and paid-attempt records;
- generic ArtifactVersion and profile/snapshot JSONB.

```text
GEMINI_IMAGE_RUNTIME_ROUTE_PRESENT=false
GEMINI_IMAGE_SETTINGS_PRESENT=false
IMAGE_PROVIDER_COST_CATALOG_PRESENT=false
IMAGE_PROVIDER_ATTEMPT_LEDGER_PRESENT=true
IMAGE_PROVIDER_PROVENANCE_PRESENT=true
```

No deleted/obsolete Gemini or Imagen image adapter was found to restore.

## Data and migration decision

`DATABASE_MIGRATION=NOT_REQUIRED`.

VSR1 can use versioned repository policy data and existing JSONB artifacts. IMG1 can reuse the generic provider registry and R3D8 provider-key/stage storage. No enum/check constraint in those current generic stores blocks a new provider key.

Caveat: legacy M10.2 media-role tables have check constraints that do not include `AI_IMAGE_PROVIDER`. The implementation spec explicitly keeps IMG1 off that legacy DB matrix. If a later operator decision makes that matrix authoritative for image acquisition, a forward migration would become required.

Alembic remains at one head: `0037_ch1_flex`.

## CH1-FLEX impact

Classification: `VALID_HISTORICAL_BUT_SUPERSEDED_FOR_FUTURE_PRODUCTION`.

The active immutable v1 profile and compiled snapshot remain valid evidence. V1 contains supporting-only Pexels, native/stock/hero planning guidance, generic paid-attempt caps and provenance/disclosure policy, but lacks:

- `niche_visual_source_profile`;
- typed visual-source route policy and scene decision requirements;
- Pexels/AI-image/evidence/diagram eligibility gates;
- native overlay policy;
- image model/cost/attempt/provenance policy.

The existing non-active draft v2 only changes a native planning band; it is not the required future profile. A later CH1-FLEX v2 must be newly compiled, reviewed and approved after IMG1/VQC1/canary evidence. VSR1 does not activate it.

## PKG1 impact

Historical PKG1 remains an immutable PASS. Future production requires appended revisions, not rewrites.

| Classification | Artifacts |
| --- | --- |
| `REUSABLE_UNCHANGED` | idea/admission lineage, research pack, source pack editorial evidence, claim evidence, script, SpokenTextNormalized, approved FormatIdentityContract, narration pacing preflight |
| `REVISION_REQUIRED` | creative brief policy binding, originality visual concepts, VisualDirectionContract, SceneVisualIntent/VisualPlan, CompiledAssetRequestPlan, CaptionPlan if reserved regions affect captions, CostEstimateSnapshot, ProviderExecutionPlan, PaidAttemptPlan, rights/disclosure report, synthetic-media disclosure draft, package manifest and MR1 readiness state |
| `REBUILD_REQUIRED` | exact MR1 paid-execution approval and package approvals that target revised visual/provider/cost/rights artifacts |

The current plan contains six native and three hard-coded Pexels-supporting scenes, no Veo request, no Gemini Image route, and `execution_enabled=false`.

## MR1 hold and approval impact

The existing reports and exact bindings prove:

- MR1 execution is `NOT_STARTED`;
- project provider attempts created since PKG1 are `0`;
- provider jobs, paid-call ledger rows, media render jobs, FinalMediaRef, HumanUploadTask and UploadedVideo created by PKG1 are `0`;
- old approval binds exact ProviderExecutionPlan `440a964a-ba67-4d51-97b5-a01741447611`, which does not contain Gemini Image.

The newer operator decision in this task supersedes execution permission without rewriting historical artifacts:

```text
MR1_EXECUTION=ON_HOLD
PROCEED_TO_MR1=false
```

Future re-approval inputs are: approved ChannelProfileVersion v2, new CompiledChannelPolicySnapshot, revised VisualDirectionContract and VisualPlan, complete VisualSourceDecision set, revised asset request plan, explicit Gemini Image route/model/capability/kill switch, new cost snapshot, rights/provenance/disclosure plan, disabled revised ProviderExecutionPlan, revised package/readiness artifacts and new exact-target human approvals.

## Duplicate-concept risk register

| Research recommendation | Existing concept | Decision |
| --- | --- | --- |
| VisualSourceDecision | SceneSourceDecisionContract | extend existing; expose a route-aware v2 projection |
| VisualRealization | resolved asset refs + manifests + NativeRenderPlan | do not build standalone in VSR1 |
| AIImageGenerationManifest | AIGenerationManifest | rename target recommendation; extend existing in IMG1 |
| ImageCostEstimate | CostEstimateSnapshot | do not build |
| ImageProviderAttemptLedger | PaidProviderCallLedger + PaidAttemptLimitRecord | do not build |
| NativeOverlayPlan | NativeRenderScene + compiled overlay schedule | extend existing |
| AssetUsageManifest | AssetManifestContract, StockSourceManifest, AIGenerationManifest | consolidate routing evidence in existing manifests |
| EvidenceTruthClassification | factual risk/evidence role/claim ledger | normalize and extend existing evidence truth |

No unresolved duplicate-domain ambiguity remains.

## Implementation decision and order

Repository evidence supports the requested order:

1. VSR1 - typed taxonomy, strict scene requirements, eligibility gates, deterministic router, routing evidence and inactive policy fixture.
2. IMG1 - Gemini Image provider foundation using a new provider route and existing generic ledgers.
3. VQC1 - generated-image/text/fake-UI/crop/overlay QC.
4. Offline routing/image/overlay fixtures.
5. One controlled paid image canary.
6. Human full-watch and Drive verification.
7. CH1-FLEX v2.
8. PKG1 visual/provider/cost/disclosure revision.
9. New exact MR1 approval.
10. MR1.

The detailed repo-grounded change specification is in `docs/architecture/niche_aware_visual_routing_change_spec.md`.

## Verification and self-repair

Prompt 1 checks:

- `PYTHONPATH=. .venv/bin/alembic heads` -> `0037_ch1_flex (head)`;
- `PYTHONPATH=. .venv/bin/python -m compileall -q app` -> PASS;
- `git diff --check` -> PASS;
- focused visual/provider/profile/PKG1 pytest command was attempted; after sandbox approval it reached the configured local PostgreSQL boundary and stopped because port `55432` was not running. This is an unavailable test dependency, not a product assertion or repaired failure.

Self-repair cycles: `0`. No audit helper or report serialization repair was needed.

## No-execution proof

This review performed only local file/PDF inspection, static scans and offline checks. It made zero Pexels, ElevenLabs, Forced Alignment, Veo, Gemini Image, Drive or YouTube calls; produced no render; consumed no provider attempt; and mutated no DB row, profile, policy snapshot, PKG1 artifact or MR1 approval.

## Exact next action

Implement VSR1 only: provider-neutral contracts, gates, deterministic router, inactive policy/fixture, routing evidence and tests. Keep all provider execution disabled and do not add Gemini Image settings/adapter yet.

```text
REPOSITORY_IDENTITY=PASS
VISUAL_RESEARCH_INPUT=PASS
CURRENT_VISUAL_PIPELINE_MAPPING=PASS
PEXELS_GLOBAL_DEFAULT_AUDIT=PASS
CURRENT_SOURCE_ROUTING_AUTHORITY=PASS
EXACT_TEXT_AUTHORITY_AUDIT=PASS
GOOGLE_IMAGE_ROUTE_AUDIT=PASS
TARGET_DESIGN_REPO_MAPPING=PASS
DUPLICATE_CONCEPT_RISK_REVIEW=PASS
DATABASE_MIGRATION_DECISION=NOT_REQUIRED
CH1_FLEX_IMPACT_REVIEW=PASS
PKG1_IMPACT_REVIEW=PASS
MR1_APPROVAL_IMPACT_REVIEW=PASS
IMPLEMENTATION_CHANGE_SPEC=PASS
VISUAL_IMPACT_REVIEW_SELF_REPAIR_CYCLES=0
VISUAL_IMPACT_REVIEW_FINAL=PASS
MR1_EXECUTION=ON_HOLD
PROCEED_TO_MR1=false
PROCEED_TO_VSR1=true
```
