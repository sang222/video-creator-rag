# Source Of Truth

## Runtime LTS v1

R3D10 freezes VCOS backend/core as Runtime LTS v1. Freeze truth is verified by `RuntimeLTSFreezeVerifier`, documented in `docs/architecture/runtime_lts_v1.md`, and governed by `docs/operations/post_freeze_protocol.md`.

Runtime LTS v1 is additive/read-only at the freeze layer. It does not rename DB tables, rewrite Alembic history, remove public API routes, activate providers, or add upload/publish automation.

## Runtime State

Runtime traceability state belongs in PostgreSQL when it must be queried, audited, replayed, correlated, or joined with operational records.


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

Idea and admission truth is editorial-candidate plus decision truth.
`EditorialIdeaCandidate` stores proposal, evidence, research-run lineage,
experiment metadata, and deterministic readiness states.
`EditorialIdeaResearchAgent` receives only its bounded Prompt
Registry/LLMRouter context; deterministic preflight and policy, not the LLM or
caller, decide fit. `ProjectAdmissionDecision` stores the production outcome
and admitted VideoProject refs when ADMIT occurs.

Niche governance truth is frozen semantic-digest truth. `NicheContractDigest` is compiled from the active Channel Contract/profile/snapshot plus exact ContentCategory and EditorialCalendarSlot. It carries semantic content and refs/hashes; a bare hash is not sufficient. Topic, Script, Visual, Thumbnail, and Metadata niche gates bind exact subject/digest/snapshot evidence. `NicheAlignmentDossier` aggregates those results and cannot report production PASS with a missing mandatory gate.

Candidate-to-package truth is exact lineage truth. Cadence selects one strict
`GREENLIT` candidate and delegates assignment to the existing v2 admission
authority. The resulting project and M12.2 package bind candidate, preflight,
admission/project, profile/snapshot, niche/market digests, slot, research,
assignment, destination, duration, and builder version; they never treat
“latest” state as frozen lineage. A package never implies media execution,
render completion, or upload permission.

Production artifact truth is snapshot-backed. ProductionArtifactRun binds exact `video_project_id` and `policy_snapshot_id`; it does not look up latest policy. `VoiceTimelineSnapshot` is historical M6 lineage and may contain estimated timing. New `CANONICAL_STRICT` work uses final narration audio, `VerifiedNarrationAlignment`, and `CanonicalMediaTimeline` as the sole timing truth for captions, visual scenes and rendering. RenderSpecSnapshot must validate before MediaRenderJob creation. RenderPackageSnapshot stores refs/manifests/checksums only, not binary blobs. Technical MediaQC, CreativePerceptual MediaQC and human watchability are separate truths; none may infer another layer's PASS.

Manual publish handoff truth is human-operated. PublishHandoffPackage binds an exact RenderPackageSnapshot and policy snapshot and gives the operator planned files, metadata, disclosures, checklist, and instructions. ManualPublishConfirmation stores actual platform id, URL, published time, actual metadata, actual files, and actual disclosure/license confirmations supplied by a human after uploading outside VCOS. UploadedVideo is the durable M8 analytics anchor and preserves VideoProject, RenderPackage, PolicySnapshot, SourceManifest, RightsEnvelope, and QC lineage. M7 stores no analytics metrics and performs no upload, OAuth, platform publish API, or provider network call.

Analytics truth is append-only snapshot truth. AnalyticsSyncRun binds an exact UploadedVideo, VideoProject, ChannelWorkspace, platform id, and policy snapshot. AnalyticsSnapshot stores raw metrics separately from normalized metrics and preserves source/provider/platform provenance, captured time, observed window, freshness, confidence, and metric availability. Missing metric means UNKNOWN, unsupported metric means NOT_AVAILABLE, and zero means numeric zero. TrafficSourceSnapshot, RetentionCurveSnapshot, and EngagementSnapshot store supplied data only; they do not diagnose performance. UploadedVideoMetricsSummary is a derived latest read model and not source truth. M8 performs no real provider network call, OAuth, scraping, no-view diagnosis, recovery proposal, dashboard, or title/thumbnail recommendation.

YouTube follow truth is UploadedVideo-bound analytics truth. `youtube_monitoring_credentials` and `youtube_oauth_sessions` store safe credential/session metadata only; raw API keys, client secrets, authorization codes, access tokens, and refresh tokens do not belong in plain DB fields. `uploaded_video_youtube_public_monitor_snapshots` store YouTube Data API public stats and publish consistency fields with WEAK authority. `uploaded_video_youtube_owner_analytics_snapshots` store OAuth-backed owner analytics with STRONG authority. `youtube_public_sync_runs` and `youtube_owner_analytics_sync_runs` preserve operational sync state and failures without fake snapshots. M10.3 may feed M8 AnalyticsSnapshot/MetricAvailabilitySnapshot/UploadedVideoMetricsSummary, but it does not create dashboard UI, diagnostics, upload/publish jobs, platform edits, or learning promotion.

Cloud media offload truth is DB-first and Drive-as-blob truth. `cloud_media_refs` store verified Google Drive file ids, `web_view_link`, size/checksum metadata, source refs, and local cleanup state. `media_offload_jobs` preserve upload/verify/cleanup state transitions and failures. `local_media_retention_policies` define cleanup safety roots and keep-local policy. `google_drive_media_credentials` and `google_drive_oauth_sessions` store safe OAuth/session metadata only; raw client secrets, authorization codes, access tokens, and refresh tokens do not belong in plain DB fields. Google Drive is not source of truth and is not served through a backend download or preview proxy. M11 dashboard must use `web_view_link` CTA only.

Post-publish diagnostic truth is deterministic and evidence-bound. PostPublishObservationWindow stores fixed check windows derived from UploadedVideo.published_at. PostPublishHealthRun reads only M7 UploadedVideo lineage and M8 analytics snapshots/summaries; it does not sync analytics or call providers. NoViewDiagnosticRun, PackagingDiagnosticRun, RetentionDiagnosticRun, EngagementDiagnosticRun, and PolicyRightsDiagnosticRun preserve metric availability and reason codes separately from conclusions. FailureTraceReport is the operator-friendly diagnostic report with technical appendix and evidence refs. RecoveryProposal is proposal truth only and always requires human approval. M9 distinguishes zero metrics from unavailable metrics and returns INSUFFICIENT_DATA when evidence is not enough. M9 performs no dashboard, memory promotion, auto publish/upload, auto reupload, platform edit, scraping, fake engagement, bot traffic, or platform evasion.

Learning review truth is evidence-bound. LearningCandidateGenerationRun reads stored M8/M9 evidence and records whether deterministic candidate generation completed or was blocked. LearningCandidate stores a hypothesis, confidence, risk, recommended review scope, source refs, limitations, counter-evidence, and technical appendix. LearningEvidenceBundle preserves supporting evidence, unavailable or unknown metrics, freshness, confidence, policy/rights summary, limitations, and counter-evidence. LearningPromotionEligibilityRun deterministically classifies a candidate as eligible for review, needs more evidence, blocked, or ineligible. LearningReviewQueueItem is a dashboard-ready read model. PlaybookCandidateDraft is draft text. M11 `learning_review_decisions` records human approve/reject/request-more-evidence/suppress/expire decisions, and `approved_playbook_entries` stores approved guidance with evidence refs. M11 approval does not mutate ChannelProfileVersion, CompiledPolicySnapshot, production workflow, editorial research, platform metadata, or channel config.

Dashboard truth is read-model and human-decision truth. The long-form operator
dashboard aggregates launch mode/day, runway, public-ready buffer, series,
publish slots, production state, QC/archive state, blockers, final decisions,
and publication verification. Health status does not auto-change lifecycle or
force production. Dashboard read paths must not call real providers, scrape
pages, upload/publish/reupload, or expose local media paths.

M11.1 auth truth is local/dev operator auth shell truth. `operator_users.password_hash` stores hashed passwords only, `operator_auth_sessions.session_token_hash` stores hashed session tokens only, and the frontend uses httpOnly cookies rather than localStorage tokens. Bootstrap admin comes from env only when no operator user exists.

M11.1 localization truth is canonical-video language-package truth. One `UploadedVideo`/YouTube video can have many `localized_subtitle_packages` and `localized_metadata_packages`; VCOS does not reupload country-specific copies. Subtitle files use `CloudMediaRef`/Google Drive CTA when present. Localized metadata is human-reviewed before use.

Publish timing truth is the approved immutable
`FirstChannelLaunchPolicyVersion`. Its IANA timezone, weekdays, local time,
minimum interval, version, and canonical hash are the only active configured
window authority. `publish_timing_suggestions` stores target local time, UTC
equivalent, operator local time, and an exact `LP:<policy-id>` lineage source.
Legacy `channel_publish_timing_policies` rows are historical/read-only and are
ignored by active reads. VCOS does not auto-schedule or auto-publish.


Prompt registry truth is repo-authored and audit-snapshotted. Canonical prompt templates, common skills, agent deltas, user templates, schemas, and eval fixtures live under `app/prompts/`. `prompt_template_records`, `agent_prompt_profiles`, `prompt_contract_versions`, and `structured_output_schemas` mirror the repo state for activation, audit, and replay. `prompt_render_runs` store rendered chat messages, prompt hash, context hash, router lane, output schema ref, frozen `channel_contract_json`, frozen `compiled_policy_snapshot_json`, `channel_profile_version_id`, and `compiled_policy_snapshot_id`. `prompt_audit_snapshots` preserve validation and repair outcomes. If required channel contract data is missing, incomplete, stale, or contradictory, content agents return REVIEW_REQUIRED/BLOCK instead of guessing defaults. Prompt rendering does not mutate ChannelProfileVersion, does not choose concrete models, and does not call real providers.

LLM router truth is lane-bound and guard-controlled. `llm_router_profiles`, `llm_router_lanes`, and `llm_model_profiles` define allowed Ollama routing by lane. Business services must request a lane, not a runtime model. `llm_route_attempts` records selected model, fallback level, status, hashes, usage/duration metadata, and refs to `provider_attempts` and `llm_run_snapshots` when created. Real Ollama execution is disabled unless the explicit environment guard is enabled; tests and normal runs do not require Ollama. Provider cost is not invented when Ollama returns no price.

Launch policy truth is channel-scoped immutable version truth.
`FirstChannelLaunchPolicyVersion` binds exact profile, compiled policy, initial
approved series, controlled-evidence targets, experiment constraints, cadence,
and human boundaries by canonical hash. Duration remains referenced from the
channel contract.

Cadence truth is slot plus receipt truth. `LongFormPublishSlot` records
publication intent in the policy timezone. `CadenceEvaluationReceipt` binds the
exact evaluation window, policy hash, buffer, active production, candidate,
budget/rights/quality/incident evidence, decision, and hashes. It may start one
idempotent long-form workflow but never chooses `UPLOAD` or publishes.


## Repo Catalogs

Versioned policy catalogs live in `config/` as YAML or JSON. M0 catalogs are loaded, schema validated, canonicalized, hashed, and seeded idempotently into `config_catalog_versions`.

The database stores immutable catalog versions. A matching catalog key and version with a different hash is a conflict and must be blocked.

## Events

`audit_events` are append-only accountability records.

`domain_events` are append-only outbox-style records for future publishing. M0 does not include an external broker.

M2 workflow actions write both audit and domain events for project, artifact, artifact version, review, finding, revision, and approval transitions.

M3 gate and policy actions write audit/domain events for gate definition lifecycle, gate runs, gate-created review tasks, policy catalogs, policy versions, policy source refs, policy change records, and revalidation batches.

M4 provider, credential, quota, cost, budget, retry, health, dead-letter, incident, and manual-action changes write audit/domain events. Event payloads must not contain raw secret values.

M7 handoff, confirmation, uploaded video, metadata diff, disclosure review, and ready-for-analytics state changes write audit/domain events. Event payloads must not contain credentials, raw secrets, binary blobs, huge file payloads, or analytics metrics.

M8 analytics sync, manual import, snapshot, metric availability, traffic, retention, engagement, and summary updates write audit/domain events. Event payloads must not contain credentials, raw secrets, OAuth tokens, scraped payloads, diagnosis, recovery instructions, or title/thumbnail recommendations.

M9 post-publish health runs, diagnostics, failure trace reports, recovery proposals, and manual action handoffs write audit/domain events. Event payloads must not contain credentials, raw secrets, OAuth tokens, scraped payloads, platform edit instructions, fake engagement instructions, or automatic reupload/publish actions.

M10 learning generation runs, candidates, evidence bundles, eligibility runs, review queue items, and playbook candidate drafts write audit/domain events. Event payloads must not contain credentials, raw secrets, OAuth tokens, provider payloads, approval decisions, config edit recommendations, platform edit instructions, or automatic promotion actions.

M10.2 media provider routing, package planning, asset planning, budget snapshots, and license evidence records are deterministic planning/state artifacts. Event payloads, when added, must not contain credentials, raw secrets, provider API payloads, external render outputs, auto-publish instructions, or platform edit instructions.

M10.3 YouTube follow sync, OAuth sessions, credential health, public monitor snapshots, owner analytics snapshots, and M8 follow-derived analytics updates must not emit credentials, raw secrets, OAuth tokens, authorization codes, raw Google payloads, scraped payloads, upload/publish instructions, platform edit instructions, dashboard actions, fake engagement instructions, or automatic reupload/publish actions.

M10.5 Drive offload jobs, OAuth sessions, credential health, cloud media refs, and cleanup events must not emit credentials, raw secrets, OAuth tokens, authorization codes, local absolute paths, raw Google payloads, backend download or preview URLs, upload/publish instructions, platform edit instructions, fake engagement instructions, or automatic reupload/publish actions.

M11 dashboard, lifecycle, learning decision, and approved playbook events must not emit credentials, raw secrets, OAuth tokens, local absolute paths, backend download or preview URLs, upload/publish instructions, platform edit instructions, fake engagement instructions, automatic reupload/publish actions, or config upgrade suggestions.

M12 readiness/smoke records must not emit credentials, raw secrets, OAuth tokens, local absolute paths, service account paths, backend download or preview URLs, upload/publish instructions, platform edit instructions, fake engagement instructions, automatic reupload/publish actions, or config upgrade suggestions.

## LLM Runs

`llm_run_snapshots` captures M5 mock LLM proposal attempts, M6 mock script draft attempts, and M10.1/M12.1 guarded Ollama router attempts. LLM output is proposal/draft/rationale/assistant text only and cannot approve, publish, compute metrics, or become numeric truth. Real Ollama calls are local-only and disabled by default. Production prompt calls use chat-style `system` and `user` messages; legacy raw prompt callers remain supported.
