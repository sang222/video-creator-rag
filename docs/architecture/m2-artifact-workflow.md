# M2 Artifact Workflow Backbone

## Scope

M2 builds only the artifact-first workflow backbone:

- `VideoProject` with explicit `policy_snapshot_id`.
- `Artifact` and immutable `ArtifactVersion`.
- `ReviewTask`, `ReviewFinding`, `RevisionRequest`.
- `ApprovalDecision` with exact target/version binding.
- Minimal decision rights, audit events, domain events, API and CLI smoke paths.

M2 does not build a policy engine, deterministic gates, ResourceResolver, RAG engine, media pipeline, publish/upload flow, analytics, no-view recovery, memory engine, or dashboard.

## Runtime Invariants

- Every `VideoProject` stores an explicit `policy_snapshot_id`.
- Project execution never looks up latest profile or latest snapshot.
- A project policy snapshot must belong to the same channel.
- M2 project creation requires the explicit snapshot to be active for that channel.
- `ArtifactVersion` rows are immutable after creation.
- Review tasks target exact artifact versions or exact non-artifact refs.
- Approval decisions target exact artifact versions or exact review targets.
- A creator cannot approve their own artifact version.
- Approval is not transferable to newer artifact versions.
- Revision resolution must point to a newer artifact version.
- Important workflow changes write audit and domain events.
- M2 performs no LLM or provider calls.

## Allowance Fields

Allowance fields exist to avoid future schema churn. M2 stores empty or stub values only and does not populate, interpret, score, retrieve, rank, publish, render, or gate on these fields.

Current allowance fields:

- Project summaries: `financial_summary`, `brand_safety_summary`, `legal_compliance_summary`, `audience_delivery_summary`.
- Artifact version refs: `external_entity_refs`, `packaging_metadata`, `media_qc_metadata`, `source_manifest`, `evidence_refs`, `context_refs`, `claim_refs`, `retrieval_plan_ref`.
- Review refs: `review_reason_codes`, `evidence_required`, `evidence_refs`, `review_scope`, `context_pack_ref`.
- Approval refs: `decision_basis`, `evidence_basis`, `policy_basis`, `context_pack_ref`, `human_decision_note`.

Forbidden JSONB payloads in M2 allowance fields:

- Raw vendor payloads.
- Prompts or prompt traces.
- Tool traces or provider traces.
- Waveforms, blobs, media binaries, or render outputs.
- Free-form policy prose.
- Unscoped memory dumps.

## Future Ownership

- M3 owns deterministic gates and policy interpretation.
- M5 owns ResourceResolver MVP, RetrievalPlanSnapshot, and ContextPackSnapshot.
- M6 owns media provenance, QC, thumbnail, audio, and accessibility workflow.
- M7 owns manual publish handoff and launch distribution.
- M8 owns analytics and semantic metric layer.
- M9 owns no-view recovery and self-funding gates.
- M10 owns memory promotion workflow.
- M11 owns multi-channel operator cockpit.

## Architecture Contracts

### Numeric Truth Contract

- All numeric truth must come from SQL, read models, or API snapshots.
- RAG and LLM must not compute metrics.
- LLM may summarize or explain metrics only when metrics are supplied by system state.

### Retrieval Boundary Contract

- Agents must not query memory, vectors, or databases directly.
- Retrieval must eventually go through ResourceResolver.
- Retrieval must be scoped by company, channel, profile, project, and policy snapshot.

### Answer Contract

- Any answer used in a decision, gate, or dashboard must carry evidence refs, freshness, confidence, reason codes, and input snapshot ref.
- Creative drafts can be lighter.
- Gate, authority, and dashboard decisions cannot be evidence-free.

### Memory Promotion Contract

- Channel memory must not automatically become company memory.
- LearningCandidate must pass evidence and human approval before promotion.
- No single successful video can become a company-wide playbook without review.
