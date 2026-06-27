# Source Of Truth

## Runtime State

Runtime traceability state belongs in PostgreSQL when it must be queried, audited, replayed, correlated, or joined with operational records.

Runtime tables through M10.3 are companies, users, roles, user_roles, audit_events, domain_events, llm_run_snapshots, config_catalog_versions, channel workspaces, profile versions, compiled policy snapshots, video projects, artifacts, artifact versions, review tasks, review findings, revision requests, approval decisions, gate definition versions, gate runs, platform policy catalogs, platform policy versions, policy source refs, policy change records, policy revalidation batches, provider registry entries, credential references, credential health snapshots, quota accounts, quota events, cost events, budget policies, provider health snapshots, component health snapshots, system health snapshots, retry policies, provider attempts, dead-letter jobs, ops incidents, manual action queue records, editorial calendar slots, channel daily runs, retrieval plan snapshots, context pack snapshots, channel state pack snapshots, search demand evidence, search intent maps, audience target packs, idea market preflights, daily idea decisions, project admission decisions, production artifact runs, voice timeline snapshots, caption track snapshots, visual plan snapshots, scene manifest snapshots, asset manifest snapshots, source manifest snapshots, render spec snapshots, media render jobs, render package snapshots, media QC reports, accessibility QC reports, pronunciation dictionary entries, publish handoff packages, manual publish confirmations, uploaded videos, uploaded video publication summaries, analytics sync runs, metric definition versions, metric availability snapshots, analytics snapshots, traffic source snapshots, retention curve snapshots, engagement snapshots, uploaded video metrics summaries, post publish observation windows, diagnostic taxonomy versions, post publish health runs, no-view diagnostic runs, packaging diagnostic runs, retention diagnostic runs, engagement diagnostic runs, policy rights diagnostic runs, failure trace reports, recovery proposals, learning candidate generation runs, learning candidates, learning evidence bundles, learning promotion eligibility runs, learning review queue items, playbook candidate drafts, LLM router profiles, LLM router lanes, LLM model profiles, LLM route attempts, content derivative graph edges, short candidates, short candidate scores, short render plans, promote-short-to-long candidates, reusable artifacts, asset reuse index entries, derivative originality checks, originality budgets, derivative release plans, cross-platform funnel packages, upload cards, human upload tasks, usage savings ledger entries, media provider role profiles, provider capability matrix entries, media render routing decisions, media provider budget policies, media provider budget snapshots, long-form render packages, short render packages, AI hero assets, Creatomate render assets, thumbnail variants, final media refs, license evidence records, YouTube monitoring credentials, YouTube OAuth sessions, YouTube public sync runs, YouTube owner analytics sync runs, uploaded video YouTube public monitor snapshots, and uploaded video YouTube owner analytics snapshots.

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

Manual publish handoff truth is human-operated. PublishHandoffPackage binds an exact RenderPackageSnapshot and policy snapshot and gives the operator planned files, metadata, disclosures, checklist, and instructions. ManualPublishConfirmation stores actual platform id, URL, published time, actual metadata, actual files, and actual disclosure/license confirmations supplied by a human after uploading outside VCOS. UploadedVideo is the durable M8 analytics anchor and preserves VideoProject, RenderPackage, PolicySnapshot, SourceManifest, RightsEnvelope, and QC lineage. M7 stores no analytics metrics and performs no upload, OAuth, platform publish API, or provider network call.

Analytics truth is append-only snapshot truth. AnalyticsSyncRun binds an exact UploadedVideo, VideoProject, ChannelWorkspace, platform id, and policy snapshot. AnalyticsSnapshot stores raw metrics separately from normalized metrics and preserves source/provider/platform provenance, captured time, observed window, freshness, confidence, and metric availability. Missing metric means UNKNOWN, unsupported metric means NOT_AVAILABLE, and zero means numeric zero. TrafficSourceSnapshot, RetentionCurveSnapshot, and EngagementSnapshot store supplied data only; they do not diagnose performance. UploadedVideoMetricsSummary is a derived latest read model and not source truth. M8 performs no real provider network call, OAuth, scraping, no-view diagnosis, recovery proposal, dashboard, or title/thumbnail recommendation.

YouTube follow truth is UploadedVideo-bound analytics truth. `youtube_monitoring_credentials` and `youtube_oauth_sessions` store safe credential/session metadata only; raw API keys, client secrets, authorization codes, access tokens, and refresh tokens do not belong in plain DB fields. `uploaded_video_youtube_public_monitor_snapshots` store YouTube Data API public stats and publish consistency fields with WEAK authority. `uploaded_video_youtube_owner_analytics_snapshots` store OAuth-backed owner analytics with STRONG authority. `youtube_public_sync_runs` and `youtube_owner_analytics_sync_runs` preserve operational sync state and failures without fake snapshots. M10.3 may feed M8 AnalyticsSnapshot/MetricAvailabilitySnapshot/UploadedVideoMetricsSummary, but it does not create dashboard UI, diagnostics, upload/publish jobs, platform edits, or learning promotion.

Post-publish diagnostic truth is deterministic and evidence-bound. PostPublishObservationWindow stores fixed check windows derived from UploadedVideo.published_at. PostPublishHealthRun reads only M7 UploadedVideo lineage and M8 analytics snapshots/summaries; it does not sync analytics or call providers. NoViewDiagnosticRun, PackagingDiagnosticRun, RetentionDiagnosticRun, EngagementDiagnosticRun, and PolicyRightsDiagnosticRun preserve metric availability and reason codes separately from conclusions. FailureTraceReport is the operator-friendly diagnostic report with technical appendix and evidence refs. RecoveryProposal is proposal truth only and always requires human approval. M9 distinguishes zero metrics from unavailable metrics and returns INSUFFICIENT_DATA when evidence is not enough. M9 performs no dashboard, memory promotion, auto publish/upload, auto reupload, platform edit, scraping, fake engagement, bot traffic, or platform evasion.

Learning review truth is evidence-bound and review-only. LearningCandidateGenerationRun reads stored M8/M9 evidence and records whether deterministic candidate generation completed or was blocked. LearningCandidate stores a hypothesis, confidence, risk, recommended review scope, source refs, limitations, counter-evidence, and technical appendix. LearningEvidenceBundle preserves supporting evidence, unavailable or unknown metrics, freshness, confidence, policy/rights summary, limitations, and counter-evidence. LearningPromotionEligibilityRun deterministically classifies a candidate as eligible for review, needs more evidence, blocked, or ineligible. LearningReviewQueueItem is a dashboard-ready read model for M11 and stores allowed future actions only; M10 does not implement those actions. PlaybookCandidateDraft is draft text only and is not an approved playbook entry. M10 does not mutate ChannelProfileVersion, CompiledPolicySnapshot, production workflow, daily workflow, platform metadata, or approved learning state.

LLM router truth is lane-bound and guard-controlled. `llm_router_profiles`, `llm_router_lanes`, and `llm_model_profiles` define allowed Ollama routing by lane. Business services must request a lane, not a runtime model. `llm_route_attempts` records selected model, fallback level, status, hashes, usage/duration metadata, and refs to `provider_attempts` and `llm_run_snapshots` when created. Real Ollama execution is disabled unless the explicit environment guard is enabled; tests and normal runs do not require Ollama. Provider cost is not invented when Ollama returns no price.

Derivative truth is originality-bound. `content_derivative_graph_edges` records parent/derivative lineage and can be publish-allowed only when originality and risk checks pass. `short_candidates` are selected standalone derivative opportunities from stored long-form artifacts; they are not fixed-count filler. `short_candidate_scores` store deterministic ShortValueScore components. `short_render_plans` are future render plans only and do not call media providers in M10.1.

Reuse and funnel truth is manual and governed. `reusable_artifacts` preserve license state, rights envelope references, reuse scope, cooldowns, and reuse counts. Reusable artifacts do not imply unlimited rights. `derivative_originality_checks` and `originality_budgets` preserve standalone value, new value, reuse percentage, repetition risk, policy flags, rights flags, and result. `cross_platform_funnel_packages`, `upload_cards`, and `human_upload_tasks` prepare YouTube-first/manual export workflow only. They do not upload, auto-publish, schedule, or create platform posts. `UploadedVideo` remains the canonical published video record after human paste-back/confirmation. YouTube analytics remains the learning authority; TikTok/Facebook analytics learning loops are deferred.

Media provider routing truth is role-bound and capability-gated. `media_provider_role_profiles` stores the Quality-First $250 provider role matrix. `provider_capability_matrix_entries` declares supported, unsupported, blocked-by-plan, required-upgrade, external-provider-required, and mock-only job capabilities. `media_render_routing_decisions` preserves every deterministic provider route or block. Creatomate Essential 2K is `CLOUD_TEMPLATE_RENDERER_LIGHT` and cannot be the default long-form final render backbone. `LONG_FORM_FINAL_RENDER` must return `BLOCKED_PROVIDER_CAPABILITY_REQUIRED` unless a capable `CLOUD_FINAL_ASSEMBLY_RENDERER` or explicit upgraded Creatomate final-render profile is configured. `long_form_render_packages`, `short_render_packages`, `ai_hero_assets`, `creatomate_render_assets`, and `thumbnail_variants` are planning/package truth only in M10.2 and do not prove real provider output. `final_media_refs` require actual known file refs or test fixture refs. `license_evidence_records` preserve license review state for stock/free/manual assets.

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

## LLM Runs

`llm_run_snapshots` captures M5 mock LLM proposal attempts, M6 mock script draft attempts, and M10.1 guarded Ollama router attempts. LLM output is proposal/draft/rationale/assistant text only and cannot approve, publish, compute metrics, or become numeric truth. Real Ollama calls are local-only and disabled by default.
