# Source Of Truth

## Runtime State

Runtime traceability state belongs in PostgreSQL when it must be queried, audited, replayed, correlated, or joined with operational records.

Runtime tables through M6 are companies, users, roles, user_roles, audit_events, domain_events, llm_run_snapshots, config_catalog_versions, channel workspaces, profile versions, compiled policy snapshots, video projects, artifacts, artifact versions, review tasks, review findings, revision requests, approval decisions, gate definition versions, gate runs, platform policy catalogs, platform policy versions, policy source refs, policy change records, policy revalidation batches, provider registry entries, credential references, credential health snapshots, quota accounts, quota events, cost events, budget policies, provider health snapshots, component health snapshots, system health snapshots, retry policies, provider attempts, dead-letter jobs, ops incidents, manual action queue records, editorial calendar slots, channel daily runs, retrieval plan snapshots, context pack snapshots, channel state pack snapshots, search demand evidence, search intent maps, audience target packs, idea market preflights, daily idea decisions, project admission decisions, production artifact runs, voice timeline snapshots, caption track snapshots, visual plan snapshots, scene manifest snapshots, asset manifest snapshots, source manifest snapshots, render spec snapshots, media render jobs, render package snapshots, media QC reports, accessibility QC reports, and pronunciation dictionary entries.

`VideoProject.policy_snapshot_id` is explicit runtime truth. Project execution must not resolve latest profile or latest policy snapshot.

`ArtifactVersion` is immutable content truth. Revisions create newer versions rather than mutating old rows.

Approval truth is exact-target truth. An approval decision applies only to its stored target and `target_artifact_version_id` when present.

Gate truth is immutable exact-target truth. A `GateRun` stores explicit target ids, explicit `gate_definition_version_id`, canonical input snapshot hash, reason codes, evidence refs, freshness, confidence, and decision basis. Revalidation creates new gate runs and never mutates old results.

Policy catalog truth is versioned external dependency truth. Active policy versions and active gate definition payloads are not rewritten; new interpretations or new gate behavior require new versions.

Provider registry truth is metadata truth. It catalogs provider identity, capability, policy fit, cost, quota, and retry metadata. It does not store raw credentials and does not execute content workflows.

Credential truth is reference truth. `credential_references.secret_ref` is a handle only, not a secret value. Credential health snapshots preserve history.

Quota and cost truth are ledger-backed. Quota account aggregates may update, but `quota_events` are append-only audit truth. `cost_events` are append-only provider cost records and do not implement revenue or PnL.

Health truth is snapshot history. Provider, component, and system health snapshots preserve prior states. Degraded or blocked system states carry `next_action`.

Ops truth is explicit action state. Incidents and manual actions must carry owner/assignee where available, severity/priority, reason, and next action.

Editorial slot truth is demand-envelope truth. Slots carry explicit channel and policy snapshot scope and are not TopicBank items.

Retrieval truth is scoped snapshot truth. RetrievalPlanSnapshot and ContextPackSnapshot are immutable records created through ResourceResolverService only; M5 has no vector/RAG engine and no default all-company memory retrieval.

Channel state truth is derived snapshot truth. ChannelStatePackSnapshot is derived from M1-M4 SQL state and represents unknown analytics explicitly instead of inventing metrics.

Search demand truth is evidence-reference truth. SearchDemandEvidence can be manual, CSV, internal, official, or mock evidence; M5 does not scrape or use autosuggest as truth.

Idea and admission truth is decision artifact truth. DailyIdeaDecision stores proposal/rationale/evidence refs/context refs/LLM run refs. ProjectAdmissionDecision stores deterministic admission outcome and the admitted VideoProject/artifact refs when ADMIT occurs.

Production artifact truth is snapshot-backed. ProductionArtifactRun binds exact `video_project_id` and `policy_snapshot_id`; it does not look up latest policy. VoiceTimelineSnapshot is master timing truth for captions, visual plan, scene manifest, and RenderSpec. RenderSpecSnapshot must validate before MediaRenderJob creation. RenderPackageSnapshot stores refs/manifests/checksums only, not binary blobs. MediaQCReport and AccessibilityQCReport are correctness/QC truth, not aesthetic or growth scoring truth.

## Repo Catalogs

Versioned policy catalogs live in `config/` as YAML or JSON. M0 catalogs are loaded, schema validated, canonicalized, hashed, and seeded idempotently into `config_catalog_versions`.

The database stores immutable catalog versions. A matching catalog key and version with a different hash is a conflict and must be blocked.

## Events

`audit_events` are append-only accountability records.

`domain_events` are append-only outbox-style records for future publishing. M0 does not include an external broker.

M2 workflow actions write both audit and domain events for project, artifact, artifact version, review, finding, revision, and approval transitions.

M3 gate and policy actions write audit/domain events for gate definition lifecycle, gate runs, gate-created review tasks, policy catalogs, policy versions, policy source refs, policy change records, and revalidation batches.

M4 provider, credential, quota, cost, budget, retry, health, dead-letter, incident, and manual-action changes write audit/domain events. Event payloads must not contain raw secret values.

## LLM Runs

`llm_run_snapshots` captures M5 mock LLM proposal attempts and M6 mock script draft attempts. M5 and M6 use MockLLMProvider only; LLM output is proposal/draft/rationale only and cannot approve, publish, compute metrics, or become numeric truth. Real provider calls remain out of scope.
