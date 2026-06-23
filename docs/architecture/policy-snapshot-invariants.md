# Policy Snapshot Invariants

CompiledChannelPolicySnapshot is immutable policy truth for future runtime execution.

Invariants:

- `compiled_payload` and `content_hash` are set at creation.
- No service mutates `compiled_payload` or `content_hash` after creation.
- Approval and activation may update state fields only.
- Channel active snapshot lookup is for admin/channel setup only.
- Future VideoProject must store an explicit `policy_snapshot_id`.
- Project execution must never lookup latest profile or latest snapshot.
- NicheProfileTemplate is not runtime truth.
- No LLM free-form output can become policy truth.
- CapCut pilot does not create a production dependency.
- M1 does not implement media pipeline.
