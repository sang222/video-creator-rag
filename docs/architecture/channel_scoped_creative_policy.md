# Channel-scoped creative policy

CH1-FLEX freezes the first production policy as channel data, not renderer or service logic. The versioned `small-team-ai` chain is:

```text
NicheProfileTemplate / init preset
  -> ChannelProfileVersion v1 (historical immutable baseline)
  -> ChannelProfileVersion v2 (qualified visual/niche policy)
  -> ChannelProfileCompiler
  -> CompiledChannelPolicySnapshot
  -> NativeRenderPolicySnapshot
  -> immutable refs on each new VideoProject
```

`config/channel_scoped_policy_catalog.yaml` is validated as `ChannelScopedPolicy`. It owns typed identity, pacing, format identity, visual strategy, media production, voice, creative-quality binding, character, provider use, cost, evidence, originality, publish, analytics maturity, gate and capability blocks. Draft profile versions can embed the same typed policy in `ChannelProfileInput.channel_policy`; an embedded draft overrides the initialization catalog only for that new version.

## v2 visual and niche policy

CH1-FLEX v2 extends the channel policy through two optional typed blocks which must appear together:

- `visual_source_policy_binding` binds VSR1, the routing catalog, the Gemini Image provider/model catalogs, VQC1, IMG-CANARY-v3, and the Drive-verified canary receipt by ref/version/hash.
- `provider_usage_policy.google_gemini_image` fixes the editorial-still role, `gemini-3.1-flash-image`, 2K, 16:9, one output, one automated attempt, no fallback, and execution-disabled-by-default guards.

The visual binding requires the complete route taxonomy, exactly one source decision per scene, no automatic Pexels-to-AI failover, NativeFFmpeg composition, native-only exact text/numbers, no generated evidence authority, a 1080p floor, final human visual approval, and archive verification. The compiler also publishes `channel_fit_threshold=0.78` as top-level compiled policy truth by reusing the approved Pexels semantic-fit threshold with its ref/version/hash and `REUSE_APPROVED_SEMANTIC_FIT_THRESHOLD` derivation. This avoids an unrelated profile diff, and callers cannot supply a different policy-fit state.

These blocks authorize planning and deterministic governance only. They do not authorize a provider call, render, Drive upload, or YouTube action.

## Deterministic precedence

The compiler records this precedence in every input manifest:

1. hard global/company policy;
2. approved channel contract;
3. approved `ChannelProfileVersion`;
4. category policy;
5. series policy;
6. episode/project brief;
7. approved operator overrides.

Downstream input cannot weaken a hard rule. Literal typed invariants keep manual YouTube publishing, Drive archive-before-purge, `NO_CHARACTER`, canonical timing, final NativeFFmpeg authority and human full-watch requirements fail-closed.

## Strategy B representation

The approved direction is stored as effective planning data:

- native explanatory range `0.50–0.70`;
- supporting visual range `0.20–0.35`;
- AI hero range `0.05–0.18`;
- minimum Pexels quota `0`;
- minimum Veo quota `0`;
- no provider alternation or ratio-filling selection.

These are planning ranges, not per-video quotas. Mechanism/data/text/workflow/UI scenes prefer native visuals; Pexels is optional grounded context; Veo is optional hero/metaphor/signature media. `AssetRequestCompiler` validates scope from the supplied profile and contains no channel-key branch.

## Compiled outputs

For a channel with a scoped policy the compiler emits immutable, hashed blocks:

- `channel_scoped_policy`;
- `native_render_policy_snapshot`;
- `provider_usage_policy_snapshot`;
- `creative_quality_policy_snapshot`;
- `publish_policy_snapshot`;
- `capability_evaluation`;
- `launch_restrictions`;
- `compiler_input_manifest` and `compiler_decision_log`;
- `snapshot_refs` for native render, creative quality, provider use, budget, publish and format identity.

When the v2 visual binding exists, `snapshot_refs` also includes visual routing policy/catalog, Gemini Image registry/model catalog, VQC1, canary qualification, and Drive receipt refs/hashes. Missing, stale, or mismatched qualification evidence blocks compilation.

The CQR1 binding points to `creative-policy://small-team-ai/small-team-ai.creative-quality.v1` and its catalog hash. Numeric narration, caption, sync and visual thresholds remain in the approved CQR1 catalog; renderer code does not copy them.

## VideoProject freeze boundary

New scoped projects copy the exact profile ID plus every compiled ref/hash from their selected active snapshot. NICH1 additionally freezes the channel-contract and `NicheContractDigest` lineage selected during admission. Project creation rejects a caller-provided mismatch. After creation, runtime reads the project snapshot and frozen niche lineage; it does not resolve “latest profile.” Migration `0037_ch1_flex` adds nullable freeze columns and performs no historical backfill, so old project rows and snapshots remain unchanged.
