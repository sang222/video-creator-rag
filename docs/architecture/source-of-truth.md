# Source Of Truth

## Runtime State

Runtime traceability state belongs in PostgreSQL when it must be queried, audited, replayed, correlated, or joined with operational records.

Runtime tables through M3 are companies, users, roles, user_roles, audit_events, domain_events, llm_run_snapshots, config_catalog_versions, channel workspaces, profile versions, compiled policy snapshots, video projects, artifacts, artifact versions, review tasks, review findings, revision requests, approval decisions, gate definition versions, gate runs, platform policy catalogs, platform policy versions, policy source refs, policy change records, and policy revalidation batches.

`VideoProject.policy_snapshot_id` is explicit runtime truth. Project execution must not resolve latest profile or latest policy snapshot.

`ArtifactVersion` is immutable content truth. Revisions create newer versions rather than mutating old rows.

Approval truth is exact-target truth. An approval decision applies only to its stored target and `target_artifact_version_id` when present.

Gate truth is immutable exact-target truth. A `GateRun` stores explicit target ids, explicit `gate_definition_version_id`, canonical input snapshot hash, reason codes, evidence refs, freshness, confidence, and decision basis. Revalidation creates new gate runs and never mutates old results.

Policy catalog truth is versioned external dependency truth. Active policy versions and active gate definition payloads are not rewritten; new interpretations or new gate behavior require new versions.

## Repo Catalogs

Versioned policy catalogs live in `config/` as YAML or JSON. M0 catalogs are loaded, schema validated, canonicalized, hashed, and seeded idempotently into `config_catalog_versions`.

The database stores immutable catalog versions. A matching catalog key and version with a different hash is a conflict and must be blocked.

## Events

`audit_events` are append-only accountability records.

`domain_events` are append-only outbox-style records for future publishing. M0 does not include an external broker.

M2 workflow actions write both audit and domain events for project, artifact, artifact version, review, finding, revision, and approval transitions.

M3 gate and policy actions write audit/domain events for gate definition lifecycle, gate runs, gate-created review tasks, policy catalogs, policy versions, policy source refs, policy change records, and revalidation batches.

## LLM Runs

`llm_run_snapshots` is inert persistence only. M0 stores records but performs no provider calls.
