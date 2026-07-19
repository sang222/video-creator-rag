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
- M4 builds provider registry, mock provider interfaces, credential references, quota/cost ledgers, budget gates, health snapshots, retry/dead-letter, incident, and manual-action foundation only.
- M5 builds daily run, ResourceResolver MVP, ContextPack, ChannelStatePack, safe search-demand evidence, mock LLM proposal capture, deterministic preflight, and budgeted project admission only.
- M6 builds production artifact runs, script/narration/caption contracts, voice-as-master visual plans, scene/source manifests, RenderSpec, platform render variants, local/mock render package foundation, and MediaQC/AccessibilityQC only.
- M7 builds manual publish handoff packages, operator checklists/instructions, human-entered publish confirmations, uploaded video records, lineage, and publication summaries only.
- M8 builds analytics sync runs, local mock/manual/import analytics snapshots, metric definitions/availability, traffic/retention/engagement snapshots, and uploaded video metrics summaries only.
- M9 builds deterministic post-publish observation windows, no-view/packaging/retention/engagement/policy diagnostics, failure trace reports, recovery proposals, and manual review actions only.
- M10 builds evidence-backed learning candidates, evidence bundles, deterministic promotion eligibility runs, M11-ready learning review queue items, and playbook candidate drafts only.
- M10.1 builds guarded real Ollama LLMRouter lanes plus derivative/reuse/shorts/cross-platform funnel backend contracts only.
- M10.2 builds the Quality-First $250 media provider role matrix, provider capability matrix, render routing decisions, media provider gates, and render package planning only.
- M10.3 builds YouTube PublicMonitorMode, OAuth-backed OwnerAnalyticsMode, safe credential references, YouTube sync runs/snapshots, M8 truth-layer integration, and dashboard-ready UploadedVideo follow read models only.
- M10.4 binds Google Veo as the only AI hero provider and externalizes media/provider routing, capability, budget, and Veo defaults into config/env only.
- M10.5 builds Google Drive media offload/cloud archive, CloudMediaRef read contracts, MediaOffloadJob lifecycle, Drive OAuth credential references, and verified local cleanup only.
- M11 builds the human-operated Operator Dashboard cockpit, dashboard read models, channel lifecycle decisions, learning review decisions, approved playbook entries, and Next.js frontend only.
- M11.1 builds Vietnamese-only dashboard language, local/dev password auth shell, localization/subtitle/metadata package support, localization readiness gate, and configured publish timing by channel timezone only.
- M12 builds production credential readiness, provider status aggregation, guarded real-smoke orchestration, hard-env AI budget display, and Settings / Integrations dashboard only.
- VSR1 builds provider-neutral, niche-aware visual-source decisions, truth and
  eligibility gates, exact-content native-overlay contracts and offline routing
  evidence only.
- IMG1 registers `google_gemini_image` as a distinct still-image provider,
  binds `gemini-3.1-flash-image` in a versioned model/cost catalog and adds
  fixture-only request, adapter, QC, normalization, provenance and readiness
  boundaries only.

## Scope Guardrails

M0 creates the repository, source-of-truth documents, initial database schema, config catalog loading, contracts, minimal services, and CLI.

M1 adds ChannelWorkspace, ChannelProfileVersion, deterministic profile compiler, and CompiledChannelPolicySnapshot.

M2 adds VideoProject, Artifact, ArtifactVersion, ReviewTask, ReviewFinding, RevisionRequest, ApprovalDecision, decision rights, and workflow events.

M2 does not build M3 gates or policy engine, M5 ResourceResolver/RAG, M6 media, M7 publish/upload, M8 analytics, M9 no-view recovery, M10 memory engine, or M11 dashboard.

M3 adds GateDefinitionVersion, GateRun, PlatformPolicyCatalog, PlatformPolicyVersion, PolicySourceRef, PolicyChangeRecord, PolicyRevalidationBatch, deterministic built-in gates, and read-only readiness inspection.

M3 does not build M5 ResourceResolver/RAG/vector/ContextPack/RetrievalPlan, M6 media/render/QC pipeline, M7 publish/upload/manual publish, M8 analytics/semantic layer, M9 no-view/recovery/self-funding, M10 memory promotion, or M11 dashboard/operator cockpit.

M4 adds ProviderRegistryEntry, CredentialReference, CredentialHealthSnapshot, QuotaAccount, QuotaEvent, CostEvent, BudgetPolicy, ProviderHealthSnapshot, ComponentHealthSnapshot, SystemHealthSnapshot, RetryPolicy, ProviderAttempt, DeadLetterJob, OpsIncident, ManualActionQueue, deterministic mock providers, and API/CLI read paths.

M4 does not build M5 ResourceResolver/RAG/vector/ContextPack/RetrievalPlan, M5 DailyRun/Authority execution/project admission, M6 media/render/QC pipeline, M7 publish/upload/manual publish, M8 analytics/semantic layer, M9 no-view/recovery/self-funding, M10 memory promotion, M11 dashboard/operator cockpit, source scraping/parser, OPA/Cedar/general policy engine, or Algorithm/Growth/View agents.

M5 adds EditorialCalendarSlot, ChannelDailyRun, RetrievalPlanSnapshot, ContextPackSnapshot, ChannelStatePackSnapshot, SearchDemandEvidence, SearchIntentMap, AudienceTargetPack, IdeaMarketPreflight, DailyIdeaDecision, and ProjectAdmissionDecision.

M5 does not build M6 media/render/QC pipeline, thumbnail compositor, TTS/audio/video generation, asset ingestion, M7 publish/upload/manual publish, M8 analytics/semantic layer, M9 no-view/recovery/self-funding, M10 memory promotion, M11 dashboard/operator cockpit, real provider integrations, external network calls, source scraping/parser, vector/RAG engine, OPA/Cedar/general policy engine, or Algorithm/Growth/View agents.

M6 adds ProductionArtifactRun, VoiceTimelineSnapshot, CaptionTrackSnapshot, VisualPlanSnapshot, SceneManifestSnapshot, AssetManifestSnapshot, SourceManifestSnapshot, RenderSpecSnapshot, MediaRenderJob, RenderPackageSnapshot, MediaQCReport, AccessibilityQCReport, and PronunciationDictionaryEntry.

M6 does not build M7 publish/upload/manual publish, M8 analytics/semantic layer, M9 no-view/recovery/self-funding, M10 memory promotion, M11 dashboard/operator cockpit, real provider integrations, Envato API/download/generation, marketplace scraping, source scraping/parser, vector/RAG engine, OPA/Cedar/general policy engine, Algorithm/Growth/View agents, or platform evasion systems.

M7 adds PublishHandoffPackage, ManualPublishConfirmation, UploadedVideo, and UploadedVideoPublicationSummary for manual human upload handoff and confirmation capture.

M7 does not build auto upload, YouTube/TikTok/Facebook/Instagram publish APIs, OAuth upload flows, scheduled upload automation, analytics sync/snapshots/semantic layer, no-view/recovery diagnostics, memory promotion, dashboard/operator cockpit UI, real provider integrations, source scraping/parser, vector/RAG engine, OPA/Cedar/general policy engine, Algorithm/Growth/View agents, fake traffic, bot engagement, platform evasion, IP/VPS tricks, or auto-reupload spam.

M8 adds AnalyticsSyncRun, MetricDefinitionVersion, MetricAvailabilitySnapshot, AnalyticsSnapshot, TrafficSourceSnapshot, RetentionCurveSnapshot, EngagementSnapshot, and UploadedVideoMetricsSummary for local analytics measurement.

M8 does not build NoViewService, PostPublishHealthMonitor, underperformance diagnosis, recovery proposal, title/thumbnail recommendation, dashboard/operator cockpit UI, auto-publish, real analytics provider integration, OAuth, token refresh, analytics page scraping, source scraping/parser, vector/RAG engine, OPA/Cedar/general policy engine, Algorithm/Growth/View agents, fake traffic, bot engagement, platform evasion, IP/VPS tricks, or auto-reupload.

M9 adds PostPublishObservationWindow, PostPublishHealthRun, DiagnosticTaxonomyVersion, NoViewDiagnosticRun, PackagingDiagnosticRun, RetentionDiagnosticRun, EngagementDiagnosticRun, PolicyRightsDiagnosticRun, FailureTraceReport, and RecoveryProposal.

M9 does not build analytics sync, dashboard/operator cockpit UI, memory promotion, auto publish/upload, auto reupload, platform metadata editing, real provider/platform integrations, OAuth/token refresh, analytics scraping, source scraping/parser, vector/RAG engine, OPA/Cedar/general policy engine, Algorithm/Growth/View agents, fake traffic, bot engagement, platform evasion, or IP/VPS tricks.

M10 adds LearningCandidateGenerationRun, LearningCandidate, LearningEvidenceBundle, LearningPromotionEligibilityRun, LearningReviewQueueItem, and PlaybookCandidateDraft.


M10.1 adds LLMRouterProfile, LLMRouterLane, LLMModelProfile, LLMRouteAttempt, ContentDerivativeGraphEdge, ShortCandidate, ShortCandidateScore, ShortRenderPlan, PromoteShortToLongCandidate, ReusableArtifact, AssetReuseIndexEntry, DerivativeOriginalityCheck, OriginalityBudget, DerivativeReleasePlan, CrossPlatformFunnelPackage, UploadCard, HumanUploadTask, and UsageSavingsLedgerEntry.




M10.3 adds YouTubeMonitoringCredential, YouTubeOAuthSession, YouTubePublicSyncRun, YouTubeOwnerAnalyticsSyncRun, UploadedVideoYouTubePublicMonitorSnapshot, and UploadedVideoYouTubeOwnerAnalyticsSnapshot. It follows existing YouTube `UploadedVideo` records, maps public stats as WEAK authority, maps OAuth owner analytics as STRONG authority, preserves zero/UNKNOWN/NOT_AVAILABLE, and feeds M8 analytics snapshots/summaries.


M10.4 binds `AI_HERO_GENERATION` and `AI_METAPHOR_GENERATION` to `GOOGLE_VEO`, externalizes media provider role/capability/routing/budget defaults into config catalogs, adds env support for Google Vertex/Veo real execution guards, and keeps real Veo smoke disabled by default.


M10.5 uploads generated heavy media to Google Drive only after OAuth/root config is present, verifies Drive file id, web view link, size, and checksum where available, stores `CloudMediaRef` in VCOS DB, and cleans local files only after verified upload and cleanup safety checks.


M11 adds dashboard aggregation endpoints, channel lifecycle decision endpoints, learning approval/rejection/request-more-evidence/suppress/expire endpoints, approved playbook entries, provider/ops dashboard reads, uploaded video dashboard reads, and a Next.js frontend under `frontend/`.

M11 does not build auto publish/upload/reupload, YouTube upload API, backend Drive download/preview proxy, Google Drive as source of truth, YouTube Studio scraping/browser automation, TikTok/Facebook analytics learning loops, config upgrade suggestions, automatic ChannelProfileVersion mutation from learning, unguarded provider calls, new provider decisions, vector/RAG, OPA/Cedar, Algorithm/Growth/View agents, fake traffic, bot engagement, or platform evasion.

M11.1 adds local password auth, Vietnamese dashboard copy, channel localization config, subtitle/metadata language packages, localization readiness, publish timing policy/suggestion, and friendly cockpit UI polish.

M11.1 does not build production SSO/2FA/password reset, auto translation publish, AI dubbing, YouTube upload/publish APIs, reupload-by-country workflows, backend Drive download/preview proxy, TikTok/Facebook analytics loops, config upgrade suggestions, fake traffic, bot engagement, browser automation, or platform evasion.

M12 adds `provider_readiness_checks`, `provider_readiness_snapshots`, `real_smoke_runs`, `/integrations/readiness`, provider-scoped readiness/smoke endpoints, `vcos integrations readiness`, `vcos integrations smoke`, and Vietnamese integrations dashboard routes `/settings`, `/settings/integrations`, and `/providers/readiness`.


M12.1 adds repo-first prompt authoring under `app/prompts/`, DB-backed prompt template/profile/contract/schema records, prompt render runs, prompt audit snapshots, prompt evaluation cases/runs, frozen channel contract binding, common VCOS skills, agent-specific deltas for required production roles, BaseEnvelope JSON output validation, safe syntax-only JSON repair, and Ollama chat message support.

M12.1 does not build real provider calls, provider strategy changes, YouTube upload/publish/reupload APIs, channel config mutation, prompt self-mutation, DB-only canonical prompt blobs, dashboard scraping/browser automation, fake traffic, bot engagement, platform evasion, auto learning promotion, or TikTok/Facebook analytics learning loops.

VSR1 and IMG1 do not mutate the approved CH1-FLEX v1 profile, PKG1 v1
artifacts or MR1 approval. IMG1 reuses generic provider cost, approval,
idempotency, attempt and call-ledger persistence; it adds no migration and
performs no Gemini Image network call, paid canary, production render, Drive
upload or YouTube action. NativeFFmpeg remains final composition and exact
text/number authority.

## Roadmap Mapping

- AI policy/provenance maps to M3 policy/gate foundation, M6 media provenance/QC, M7 publish handoff, M8 measurement, M9 recovery, and M10 governance.
- Proactive Audience Delivery maps to M3 readiness gates, M5 retrieval/context, M6 packaging/QC, M7 distribution, M8 analytics, and M9 recovery.
- Policy drift maps to M3 policy source refs, policy change records, and revalidation backbone.
- Critique accuracy patch maps to M3 evidence/reason contracts, M5 retrieval, M6 QC, M7 publish checks, M8 metric truth, M9 recovery, and M10 memory review.
- Retrieval/Memory Governance maps to M3 privacy contracts, M5 retrieval objects, M8 metric truth, and M10 learning review governance.
- M11 dashboard remains M11 only; M3 only supplies future readiness output shape.
- M4 complete means provider/cost/quota/ops health rails exist.
- M5 complete means daily run/admission/context foundation exists with mock-first LLM proposal capture.
- M6 complete means an admitted project can produce validated production artifact snapshots, RenderSpec, local/mock media package refs, and QC reports. A playable dummy MP4 smoke requires local FFmpeg/ffprobe.
- M6 can use mock/local fixture providers for media tests.
- Pre-M7 M0-M6 Qualification Gate will be required after M6.
- M7 complete means VCOS can hand off an M6 render package to a human operator for manual upload, then capture actual publish metadata, disclosures, external id/URL, uploaded video lineage, and a metrics-free publication summary.
- M8 complete means UploadedVideo has local analytics snapshots, metric availability, freshness/confidence, traffic/retention/engagement snapshots, and a latest metrics read model.
- M9 complete means VCOS can diagnose uploaded video health and propose human-approved actions without automatic recovery.
- M10 complete means VCOS can create evidence-backed learning candidates and queue them for M11 review without approval or promotion.
- M10.1 complete means VCOS can real-smoke guarded local Ollama routing and prepare derivative/reuse/shorts/cross-platform funnel backend contracts without media provider routing or publishing.
- M10.2 complete means VCOS can route media production jobs by provider role and safely block unsupported long-form final rendering when no final assembly renderer is configured.
- M10.3 complete means VCOS can follow uploaded YouTube videos through public stats and owner analytics when OAuth is connected, then expose dashboard-ready UploadedVideo follow payloads.
- M10.4 complete means VCOS routes AI hero/metaphor jobs only to Google Veo, keeps real execution guarded, and has provider config externalized/audited.
- M10.5 complete means generated media can be offloaded to Google Drive, verified, referenced in DB, and cleaned locally under policy.
- M11 complete means VCOS has an operator dashboard for channel setup, production tracking, approvals, publish handoff queues, uploaded video monitoring, recovery review, learning review, media Drive links, and provider/storage status.
- M11.1 complete means dashboard has Vietnamese UI, login/local auth shell, localization/subtitle package support, localized metadata package support, localization readiness, and configured publish timing by channel location/timezone.
- M12 complete means VCOS can validate credentials/provider readiness, display hard-env AI budget caps, show the Cloud Final Renderer required gap, and run guarded real smoke tests before production. Next milestones can focus on production dry-run, first real video package, or final renderer selection.
- M12.1 complete means VCOS can render deterministic, versioned, channel-contract-bound chat prompts for production agents, persist prompt audit snapshots, validate BaseEnvelope JSON outputs, and run prompt eval cases without real provider calls.
- CH1-FLEX v1 remains the immutable historical/rollback baseline. CH1-FLEX v2 complete means the exact `small-team-ai` profile/snapshot lifecycle binds VSR1, Gemini Image planning, VQC1, canary and Drive evidence; publishes the niche channel-fit threshold; records diff/compiler/approval/activation receipts; and keeps provider execution, rendering, Drive upload, and YouTube actions outside the profile task.
- NICH1 complete means strict v2 daily work compiles a semantic `NicheContractDigest`, validates category/pillar/series/goal, routes `DailyIdeaAgent` through Prompt Registry/LLMRouter with an allowlisted context pack, derives channel fit from compiled policy, freezes admission lineage, and requires Topic/Script/Visual/Thumbnail/Metadata niche evidence plus `NicheAlignmentDossier`.
- D2P1 complete means one immutable admissible DailyIdeaDecision receives or reuses an immutable M5 ADMIT receipt and idempotently resumes through the existing project, Effective Context, approved research workflow, and M12.2 scripted package. Durable receipts expose the state machine/read-only status and cannot reach ready-for-human-review without all mandatory niche gates; provider/media/render/publish calls remain zero in this bridge.
- VSR1 complete means each strict scene can produce one auditable,
  provider-neutral preferred source decision without a provider call or
  automatic Pexels-to-paid fallback.
- IMG1 complete means the separate `google_gemini_image` route can rehearse
  one non-publishable local fixture through typed requests, cost/guard
  evidence, safe materialization, QC, normalization, native-overlay binding and
  provenance while real generation remains disabled. It does not imply a paid
  canary or permission to proceed to MR1.

## Pilot Notes

The manual voice-first timeline pilot proved the SRT-centered flow can work. CapCut is useful as a prototype viewer for timeline validation, but it is not a production renderer dependency. Production rendering should be FFmpeg in the later renderer milestone.
