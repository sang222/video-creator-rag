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

The compiler produces a CompiledChannelPolicySnapshot payload with lightweight typed sections. It records catalog versions and hashes in compile diagnostics and result provenance.

The M6 manual pilot only affects render policy state:

- CapCut is prototype viewer only.
- Production renderer is planned as FFmpeg.
- Transcription pilot was local faster-whisper.
- AI video mode is manual external assets.
- VisualPlan is required later.
