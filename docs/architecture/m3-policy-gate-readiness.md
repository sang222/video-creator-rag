# M3 Policy Gate Readiness

## Scope

M3 builds the policy, gate, evidence, and readiness foundation.

Included:

- Versioned `GateDefinitionVersion` records.
- Immutable deterministic `GateRun` records.
- Versioned platform policy catalogs and policy versions.
- Policy source refs and policy change records.
- Minimal policy revalidation batches.
- Review task integration when a gate returns `REVIEW_REQUIRED`.
- Read-only workflow readiness inspection from gate runs.

M3 consumes M2 allowance fields. It does not populate M5, M6, M7, M8, M9, M10, or M11 data.

## Runtime Invariants

- Project execution still uses explicit `VideoProject.policy_snapshot_id`.
- No latest-profile lookup is used during project execution.
- Every `GateRun` references an explicit target and explicit gate definition version id.
- `GateRun` rows are immutable after creation.
- `GateRun.input_snapshot_hash` is deterministic from canonical `input_snapshot`.
- Every gate result includes reason codes, evidence refs, metric refs, freshness, confidence, and decision basis fields.
- Gate runs never call LLM/provider code.
- Gate runs never mutate `ArtifactVersion.content` or `ApprovalDecision`.
- Policy changes create new versions or new gate runs; old versions and old gate runs remain audit history.
- `REVIEW_REQUIRED` creates or links an M2 `ReviewTask` when the gate definition requires review.
- `BLOCK` appears in readiness inspection as a blocker.

## Policy Model

Policy is a versioned external dependency. `platform_policy_catalogs` identify the policy domain, and `platform_policy_versions` hold typed JSON interpretation blobs plus source refs. Source refs are references only; M3 does not scrape or store full source content.

Active gate definitions and active policy versions do not allow payload mutation. New behavior requires a new version. Superseding is lifecycle metadata, not a rewrite of policy meaning.

No LLM policy decisions are allowed. M3 uses deterministic presence checks over M2 allowance fields only.

## Gate Behavior

M3 gates are intentionally minimal. They check declared flags and evidence presence, then return one of `PASS`, `REVIEW_REQUIRED`, `BLOCK`, `SKIPPED`, or `NOT_APPLICABLE` with reason codes.

If future data does not exist yet, a gate can return `REVIEW_REQUIRED`, `SKIPPED`, or `NOT_APPLICABLE` with `GATE_INPUT_INSUFFICIENT` or a domain reason code.

`publish_risk_gate` only aggregates existing gate results for the same target. It does not build publish packages or upload anything.

## Review Integration

Gate review mapping:

- `ai_use_disclosure_gate` -> `ai_disclosure`
- `rights_copyright_gate` -> `rights`
- `brand_conflict_gate` -> `brand_conflict_review`
- `search_demand_gate` -> `search_demand_review`
- `packaging_expectation_gate` -> `packaging_review`
- `privacy_retention_gate` -> `policy_review`

Review tasks target exact projects, artifact versions, or review tasks. Artifact-version review tasks carry `target_artifact_version_id`.

## Revalidation

Policy revalidation creates new `gate_runs`. It never mutates old gate results. M3 implements a minimal target-list scope and not a distributed scheduler.

## Not Built

- M3 does not build a full policy engine, OPA, or Cedar.
- M3 does not use LLMs to decide policy.
- M5 will build ResourceResolver, ContextPack, and RetrievalPlan.
- M6 will build media, thumbnail, audio, accessibility, render, and QC workflow.
- M7 will build manual publish handoff and launch distribution.
- M8 will build analytics and semantic metric layer.
- M9 will build no-view recovery and self-funding gates.
- M10 will build memory promotion.
- M11 will build multi-channel operator cockpit.

## Roadmap Mapping

- AI policy/provenance -> M3 foundation, M6 media provenance/QC, M7 publish handoff, M8 metrics, M9 recovery, M10 memory governance.
- Proactive Audience Delivery -> M3 gates, M5 retrieval inputs, M6 packaging/QC, M7 distribution, M8 analytics, M9 recovery.
- Policy drift -> M3 policy source, change record, and revalidation backbone.
- Critique accuracy patch -> M3 reason/evidence contracts, M5 retrieval, M6 QC, M7 publish checks, M8 metrics, M9 recovery, M10 memory review.
- Retrieval/Memory Governance -> M3 privacy/readiness contracts, M5 retrieval objects, M8 metric truth, M10 memory promotion.
- M11 dashboard -> M11 only; M3 only exposes readiness output shape for future read models.
