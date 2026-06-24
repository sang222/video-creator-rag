# Architecture Ledger

## Product Definition

VCOS is a budgeted, self-funding, multi-channel, artifact-first media workflow engine.

## Foundation Principles

- One engine, many profiles.
- No niche-specific pipelines.
- State lives in the database when runtime traceability is required.
- Versioned policy catalogs live in repo YAML/JSON and compile into immutable snapshots later.
- The dashboard must be action-first, not vanity telemetry.
- M0 builds foundation.
- M1 builds channel profile and policy snapshot backbone.
- M2 builds artifact workflow, review, revision, approval, decision rights, audit, and allowance schema backbone only.
- M3 builds policy catalog, deterministic gates, evidence contracts, review-required integration, policy revalidation, and readiness inspection only.

## Scope Guardrails

M0 creates the repository, source-of-truth documents, initial database schema, config catalog loading, contracts, minimal services, and CLI.

M1 adds ChannelWorkspace, ChannelProfileVersion, deterministic profile compiler, and CompiledChannelPolicySnapshot.

M2 adds VideoProject, Artifact, ArtifactVersion, ReviewTask, ReviewFinding, RevisionRequest, ApprovalDecision, decision rights, and workflow events.

M2 does not build M3 gates or policy engine, M5 ResourceResolver/RAG, M6 media, M7 publish/upload, M8 analytics, M9 no-view recovery, M10 memory engine, or M11 dashboard.

M3 adds GateDefinitionVersion, GateRun, PlatformPolicyCatalog, PlatformPolicyVersion, PolicySourceRef, PolicyChangeRecord, PolicyRevalidationBatch, deterministic built-in gates, and read-only readiness inspection.

M3 does not build M5 ResourceResolver/RAG/vector/ContextPack/RetrievalPlan, M6 media/render/QC pipeline, M7 publish/upload/manual publish, M8 analytics/semantic layer, M9 no-view/recovery/self-funding, M10 memory promotion, or M11 dashboard/operator cockpit.

## Roadmap Mapping

- AI policy/provenance maps to M3 policy/gate foundation, M6 media provenance/QC, M7 publish handoff, M8 measurement, M9 recovery, and M10 governance.
- Proactive Audience Delivery maps to M3 readiness gates, M5 retrieval/context, M6 packaging/QC, M7 distribution, M8 analytics, and M9 recovery.
- Policy drift maps to M3 policy source refs, policy change records, and revalidation backbone.
- Critique accuracy patch maps to M3 evidence/reason contracts, M5 retrieval, M6 QC, M7 publish checks, M8 metric truth, M9 recovery, and M10 memory review.
- Retrieval/Memory Governance maps to M3 privacy contracts, M5 retrieval objects, M8 metric truth, and M10 memory promotion.
- M11 dashboard remains M11 only; M3 only supplies future readiness output shape.

## Pilot Notes

The manual voice-first timeline pilot proved the SRT-centered flow can work. CapCut is useful as a prototype viewer for timeline validation, but it is not a production renderer dependency. Production rendering should be FFmpeg in the later renderer milestone.
