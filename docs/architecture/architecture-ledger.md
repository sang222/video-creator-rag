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

## Scope Guardrails

M0 creates the repository, source-of-truth documents, initial database schema, config catalog loading, contracts, minimal services, and CLI.

M1 adds ChannelWorkspace, ChannelProfileVersion, deterministic profile compiler, and CompiledChannelPolicySnapshot.

M2 adds VideoProject, Artifact, ArtifactVersion, ReviewTask, ReviewFinding, RevisionRequest, ApprovalDecision, decision rights, and workflow events.

M2 does not build M3 gates or policy engine, M5 ResourceResolver/RAG, M6 media, M7 publish/upload, M8 analytics, M9 no-view recovery, M10 memory engine, or M11 dashboard.

## Pilot Notes

The manual voice-first timeline pilot proved the SRT-centered flow can work. CapCut is useful as a prototype viewer for timeline validation, but it is not a production renderer dependency. Production rendering should be FFmpeg in the later renderer milestone.
