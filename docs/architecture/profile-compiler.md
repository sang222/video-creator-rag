# Profile Compiler

The M1 profile compiler is deterministic, typed, and audit-oriented.

Inputs:

- ChannelProfileVersion profile input.
- NicheProfileTemplate catalog item.
- CapabilityMatrix catalog item.
- ProfileCompilerPolicy catalog item.

Rules:

- No LLM calls.
- No free-form LLM output can become policy truth.
- No per-niche runtime pipeline.
- Catalogs are validated before use.
- JSON is canonicalized with sorted keys before hashing.
- Same input and same catalogs produce the same content hash.
- Capability gaps are represented in `capability_status`, not hidden.

The compiler produces a `CompiledChannelPolicySnapshot` payload with typed sections. When a channel has a `ChannelScopedPolicy`, it also emits hashed native-render, creative-quality, provider-use, budget, publish and format-identity refs, a capability evaluation, launch restrictions, input manifest and decision log. Draft versions may carry a typed `ChannelProfileInput.channel_policy`; there is one generic compiler path and no per-niche runtime pipeline.

## CH1-FLEX v2 qualification overlay

`build_ch1_flex_v2_profile_input()` copies the effective v1 input and changes only the approved typed overlay. The overlay binds all of the following into the compiled snapshot:

- the `STOCK_ASSISTED` VSR1 routing policy and complete 13-route taxonomy;
- the `google_gemini_image` registry entry and `gemini-3.1-flash-image` 2K, 16:9 model catalog entry;
- VQC1, IMG-CANARY-v3 human-review qualification, and its Drive-verified receipt;
- one source decision per scene, no automatic Pexels-to-AI failover, and no provider fallback;
- NativeFFmpeg final composition, native-only exact text/number authority, and no generated evidence authority;
- a block below effective 1080p output, final human visual approval, and archive verification before cleanup;
- the compiled `channel_fit_threshold=0.78` used by NICH1, derived from the already-approved Pexels semantic-fit threshold and emitted with authority ref/version/hash rather than added as a new profile override.

The compiler reads and hashes those existing catalogs/reports only. It does not call Gemini Image, Pexels, Drive, a renderer, or YouTube. Preview compile is deterministic and read-only. A persisted compile must match the preview hash.

For v2, `snapshot_refs` additionally carries immutable refs/hashes for visual routing policy/catalog, Gemini Image provider/model catalogs, VQC1, the canary qualification, and the Drive receipt. The provider-use policy keeps Gemini Image execution disabled by default and requires a scoped approval, cost estimate, monthly budget gate, and idempotency before any later execution task.

An active scoped profile may be recompiled only when the output is identical. Any business change requires a new draft/profile version, approval and snapshot. Preview compile is read-only.

`ChannelProfileService.approve_and_activate_ch1_flex_v2()` is the bounded lifecycle helper. It reuses an existing mutable v2 draft when present, validates two identical previews, records the v1-to-v2 semantic diff, compiles, approves, activates the exact snapshot, and returns compiler/approval/activation receipts plus a v1 rollback pointer. If v2 is already immutable but differs from the exact target, the helper blocks instead of creating v3 or rewriting history.

The M6 manual pilot only affects render policy state:

- CapCut is prototype viewer only.
- Production renderer authority is NativeFFmpeg for the active first-channel policy.
- Transcription pilot was local faster-whisper.
- AI video mode is manual external assets.
- VisualPlan is required later.
