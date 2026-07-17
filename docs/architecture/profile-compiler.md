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

The compiler produces a `CompiledChannelPolicySnapshot` payload with typed sections. When a channel has a `ChannelScopedPolicy`, it also emits hashed native-render, creative-quality, provider-use, budget, publish and format-identity refs, a capability evaluation, launch restrictions, input manifest and decision log. Draft versions may carry a typed `ChannelProfileInput.channel_policy`; there is one generic compiler path and no channel/niche service branch.

An active scoped profile may be recompiled only when the output is identical. Any business change requires a new draft/profile version, approval and snapshot. Preview compile is read-only.

The M6 manual pilot only affects render policy state:

- CapCut is prototype viewer only.
- Production renderer authority is NativeFFmpeg for the active first-channel policy.
- Transcription pilot was local faster-whisper.
- AI video mode is manual external assets.
- VisualPlan is required later.
