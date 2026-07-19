# Policy Snapshot Invariants

CompiledChannelPolicySnapshot is immutable policy truth for future runtime execution.

Invariants:

- `compiled_payload` and `content_hash` are set at creation.
- No service mutates `compiled_payload` or `content_hash` after creation.
- Approval and activation may update state fields only.
- Channel active snapshot lookup is for admin/channel setup only.
- Future VideoProject must store an explicit `policy_snapshot_id`.
- Project execution must never lookup latest profile or latest snapshot.
- A strict CH1-FLEX v2 EditorialCalendarSlot must bind the active snapshot, category, pillar, series, and production goal.
- A strict admitted project must freeze the matching `NicheContractDigest` semantic payload and ref/hash; Effective Context must reject stale or cross-channel digest lineage.
- `gate_policy.channel_fit_threshold` is compiled policy truth. LLM output and caller input cannot replace it or directly declare policy fit.
- NicheProfileTemplate is not runtime truth.
- No LLM free-form output can become policy truth.
- CapCut pilot does not create a production dependency.
- M1 does not implement media pipeline.
