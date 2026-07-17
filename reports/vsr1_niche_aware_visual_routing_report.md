# VSR1 Niche-Aware Visual Source Routing Report

Date: 2026-07-17
Repository: `/Users/sangss/Desktop/video-creator-rag`
Scope: provider-neutral routing foundation only

## Outcome

VSR1 is implemented as a deterministic, meaning-first planning boundary. Each
strict scene produces one auditable preferred route with explicit allowed and
forbidden fallbacks. Pexels is optional context rather than a global default;
evidence truth cannot use stock or generated substitutes; exact text and
numbers remain native authority; AI-image and Veo routes remain non-executable
plans.

Entry evidence is current and consistent:

```text
VISUAL_IMPACT_REVIEW_FINAL=PASS
PROCEED_TO_VSR1=true
```

## Implemented repository mapping

| Area | Implementation | Result |
| --- | --- | --- |
| Typed taxonomy | `app/contracts/visual_routing.py` | Four niche profiles, 13 source routes, six fallback classes. |
| Strict scene input | `SceneVisualRealizationRequirements` extends canonical `app/contracts/visual_direction.py::SceneVisualIntent` | Bounded scores, explicit booleans, duration/aspect/output requirements and deterministic input hash. |
| Source decision | `VisualSourceDecision` extends historical M6 `SceneSourceDecisionContract` | One preferred route, explicit fallback sets, policy/input evidence, provider execution disabled and human approval required. |
| Eligibility and truth gates | `app/services/visual_source_routing.py` | Completeness, Pexels, evidence truth, diagram, AI-image and archive gates fail closed. |
| Deterministic router | `VisualSourceRouter` | No provider result/search-failure input; identical input and policy produce an identical decision hash. |
| Read-only preview | `VisualSourceRoutingPreviewService` | Exposes route, confidence, blockers and exact next action; performs no persistence or execution. |
| Versioned policy | `config/visual_source_routing_policy_catalog.yaml` | Repository document is `draft`; nested lifecycle is `INACTIVE`, fixture-only, unbound and provider-disabled. |
| Routing evidence | `app/contracts/m6.py` | Typed preferred/actual routes, fallback class, reason/gate refs, decision binding, truth classification and overlay requirement. |
| Native authority | `app/contracts/native_renderer.py` | Strict decision refs, eligibility refs, normalized safe regions and authoritative native-overlay binding. |
| Compiler evidence | `app/services/native_motion_compiler.py` | Pure deterministic pass-through into compiled scene evidence and overlay schedule; no render execution. |

The policy catalog is registered in `ConfigRegistryService`; all 161 repository
catalogs load with unique keys. No database migration or new persistence domain
was added.

## Routing behavior

Decision order is completeness, verified archive reuse, evidence authorization,
native diagram/motion, exact-content native authority, Pexels eligibility,
high-value Veo motion, provider-neutral AI-image planning, then fail-closed
`UNRESOLVED_BLOCK`.

Hard invariants proven by contracts and fixtures:

- actual UI, product, document and evidence require an authorized source;
- mechanism, process, named workflow and diagram-worthy scenes cannot be
  satisfied by generic Pexels footage;
- Pexels search failure cannot alter AI eligibility or open a paid route;
- AI-image planning requires explicit rights permission and rejects evidence,
  product/UI truth and unapproved identity/likeness dependency;
- exact text/number refs bind to a native overlay and normalized safe regions;
- archive reuse requires semantic fit, rights, cooldown, originality, current
  truth, reuse count and authorization evidence when truth-sensitive;
- Veo requires high semantic motion, an allowed scene class, insufficient
  still/native motion and an allowed future cost class;
- all provider decisions persist `provider_execution_allowed=false`.

## Offline fixtures and tests

`tests/test_vsr1_niche_aware_visual_routing.py` contains the complete required
matrix:

- `STOCK_NATIVE`: travel street video and static real-object photo;
- `STOCK_ASSISTED`: approval bottleneck, manual handoff, context switching,
  knowledge silos, automation leverage, named system flow, before/after workflow
  and hours-saved result;
- `GENERATED_EDITORIAL_FIRST`: custom metaphor with and without native overlay;
- `AUTHORITY_ASSET_FIRST`: authorized CRM UI and missing-evidence block;
- Veo high-motion and low-motion boundaries;
- negative routing, archive, fallback, exact-content, minimum-resolution,
  deterministic-hash, no-I/O and legacy-compatibility cases;
- pure `NativeMotionCompiler` evidence propagation with the validator isolated;
  no FFmpeg or renderer execution.

Focused result:

```text
44 passed in 1.03s
```

## Compatibility and data decision

- CH1-FLEX v1, PKG1 v1, PA1R, CQR1 and historical M6 artifacts were not
  rewritten or activated.
- Legacy `NativeRenderScene` remains readable without VSR1 fields; a strict
  render plan cannot mix legacy and strict scenes.
- New strict plans require decision/gate evidence and at least 1080p.
- Storage remains repository policy plus existing JSON artifact content.
- Alembic remains one head: `0037_ch1_flex (head)`.
- Database migration decision: `NOT_REQUIRED`.

## Verification and execution proof

```text
PYTHONPATH=. .venv/bin/alembic heads                         PASS
PYTHONPATH=. .venv/bin/python -m compileall -q app           PASS
PYTHONPATH=. .venv/bin/pytest --noconftest \
  tests/test_vsr1_niche_aware_visual_routing.py -q           PASS (44)
PYTHONPATH=. .venv/bin/pytest \
  tests/test_r3d10_runtime_lts_freeze.py \
  tests/test_ofv0_originality_format_validation.py \
  tests/test_ch1_flex_channel_policy.py \
  tests/test_pkg1_first_production_package.py \
  tests/test_vsr1_niche_aware_visual_routing.py -q           PASS (77)
ConfigRegistryService load of all config catalogs            PASS (161/161)
git diff --check                                              PASS
```

The DB-backed suite used its isolated pytest database, applied the single
Alembic head and dropped the test database at session finish. Result: `77
passed, 1 deprecation warning in 63.21s`. No production database or approved
artifact was mutated.

No Pexels, ElevenLabs, Forced Alignment, Veo, Gemini Image, Drive or YouTube
client was called. No paid attempt, production DB mutation, provider
response/URL, profile
activation, PKG1 mutation, MR1 action, production render, upload or publish
occurred. During read-only review, an out-of-scope legacy synthetic-smoke test
made one failed local FFmpeg invocation; it produced no successful or production
render, provider call or execution receipt and was excluded from VSR1
verification.

## Bounded self-repair

1. Contract/catalog preflight: corrected the canonical scene-intent lineage,
   repository catalog status/registration and typed policy thresholds; config
   load and compile were rerun successfully.
2. Fail-closed integrity: tightened archive/evidence binding, AI/Pexels gates,
   provider execution state, native overlay refs, manifest evidence and the full
   offline fixture matrix; 44 focused tests passed.

No gate or policy threshold was weakened.

## Exact next action

Implement IMG1 as a separately approved Google image-provider boundary. Do not
activate a channel profile, call a provider, revise PKG1 or resume MR1 in VSR1.

```text
VSR1_ENTRY=PASS
VSR1_NICHE_PROFILE_TAXONOMY=PASS
VSR1_VISUAL_ROUTE_TAXONOMY=PASS
VSR1_SCENE_REQUIREMENT_CONTRACT=PASS
VSR1_VISUAL_REALIZATION_COMPLETENESS_GATE=PASS
VSR1_PEXELS_ELIGIBILITY=PASS
VSR1_EVIDENCE_TRUTH_GATE=PASS
VSR1_DIAGRAM_SUITABILITY_GATE=PASS
VSR1_AI_IMAGE_ELIGIBILITY=PASS
VSR1_EXACT_TEXT_NATIVE_AUTHORITY=PASS
VSR1_FALLBACK_POLICY=PASS
VSR1_VISUAL_SOURCE_ROUTER=PASS
VSR1_ROUTING_EVIDENCE=PASS
VSR1_READ_ONLY_PREVIEW=PASS
VSR1_OFFLINE_FIXTURES=PASS
VSR1_SELF_REPAIR_CYCLES=2
VSR1_PROVIDER_EXECUTION=DISABLED
VSR1_DATABASE_MIGRATION=NOT_REQUIRED
VSR1_FINAL=PASS
MR1_EXECUTION=ON_HOLD
PROCEED_TO_MR1=false
PROCEED_TO_IMG1=true
```
