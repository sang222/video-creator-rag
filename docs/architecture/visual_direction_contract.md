# VisualDirectionContract architecture

## Contract and lineage

`VisualDirectionContract` is an immutable, provider-neutral projection of the
approved format identity, visual strategy and versioned channel visual policy.
It is stored as a version of the existing visual-plan artifact; no table or new
artifact platform is required.

The contract fixes realism/treatment, human presence, environment, industry,
lighting, palette, contrast, saturation, camera language, motion, framing,
depth, texture, tone, prohibited clichés, identity markers and adjacency
constraints. It carries source refs/hashes and its own deterministic content
hash. Frozen context and FormatIdentity are inputs and are never mutated.

## Source roles

Source choice follows meaning rather than ratios:

- `NATIVE_VISUAL`: mechanism, workflow, data, UI, text, comparison and timeline;
- `PEXELS_SUPPORTING`: grounded documentary context or real environment;
- `GOOGLE_VEO`: a hero, metaphor, signature beat or section transition.

There is no mechanical Pexels/Veo alternation, stock quota satisfaction, Veo
filler or external fallback. A native bridge is preferred when it explains the
idea more clearly or makes an adjacent cut coherent.

## Pexels planning and ranking

The existing bounded Pexels query/download boundary is extended with scene
semantics, the visual-direction hash, aspect/crop requirements, locale,
previous/next scene summaries and reuse history. Candidate ranking is local and
deterministic; it is not presented as an ML probability.

Contextual weights are 0.36 semantic relevance, 0.18 direction fit, 0.12
previous continuity, 0.08 next continuity, 0.08 crop safety, 0.07 motion, 0.05
technical quality and 0.03 originality, followed by explicit risk penalties.
Logo, brand, person, endorsement, fake-evidence and rights risks remain
independent gates. Borderline scores require review.

## Veo prompt and fixed duration

`VeoPromptCompiler` deterministically emits subject/action, environment and
industry, realism/treatment, lighting/time, camera angle/shot size, movement,
framing/focal style, motion intensity, continuity hints and negative
constraints. It has no transport and cannot call a provider.

The downstream request remains the Gemini Developer API contract:

```text
model=veo-3.1-fast-generate-preview
duration=8 seconds
resolution=720p
aspect_ratio=16:9
output_count=1
generate_audio omitted
person_generation=allow_all
provider_audio_policy=DISCARD
```

The prompt/negative prompt enforces `NO_CHARACTER` separately. Narration owns
scene duration. A shorter scene trims around the action peak; a small longer
mismatch uses a native or stock bridge; a material mismatch replans before
execution. Default speed changes and loops are forbidden.

## Evaluation gates

`SceneSemanticMatchGate`, `VisualContinuityGate` and `AssetAdjacencyGate` use
versioned operating thresholds. Hard conflicts override scores. A cross-provider
cut requires both semantic and adjacency PASS; otherwise the planner clusters
the source, selects another asset, inserts a native bridge or requests review.
Full evidence stays in visual/asset artifacts and only binding, scores and gate
refs project into the canonical timeline. The renderer propagates this evidence
but never re-ranks or infers creative quality.
