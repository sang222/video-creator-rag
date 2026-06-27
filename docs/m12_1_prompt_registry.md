# M12.1 Agent Prompt Registry + Channel Contract + Production Prompt Contracts

## Verdict

M12.1 must be implemented as a deterministic, auditable prompt contract system, not as a loose collection of prompt files.

The prompt system must be:

* repo-first for canonical authoring
* DB-backed for audit/run-state/replay
* versioned
* schema-enforced
* snapshot-able
* lane-safe through LLMRouter
* bound to frozen ChannelProfileVersion and CompiledChannelPolicySnapshot

Hard rule:

* LLMRouter decides which model/lane runs.
* Prompt Registry decides what the agent is allowed to think/do/output.
* Channel Contract decides what channel boundary the agent must obey.

## Channel Init / Channel Contract Truth

Human-entered Channel Init / Channel Profile data is production truth.

Lifecycle:
ChannelInitConfig
→ ChannelProfileVersion
→ CompiledChannelPolicySnapshot
→ frozen per VideoProject / PromptRenderRun

Every production prompt must bind to:

* channel_contract_json
* compiled_policy_snapshot_json
* channel_profile_version_id
* compiled_policy_snapshot_id

Agents must not:

* redefine the channel
* guess channel identity
* guess target market
* guess audience
* guess language
* guess provider
* guess duration/format/cadence/budget
* override human-entered channel config
* suggest config upgrades
* mutate ChannelProfileVersion
* use loose scattered channel fields instead of canonical channel_contract_json

If required Channel Contract data is missing:

* content generation agents return REVIEW_REQUIRED or BLOCK
* do not assume defaults
* do not silently use US/English/USD/timezone defaults
* next_action should be: “Bổ sung hoặc compile lại ChannelProfileVersion trước khi render prompt.”

## Required Common Skills

C1 common_vcos_constitution

* VCOS is artifact-first, policy-aware, human-approved.
* Human final approval is required.
* No auto publish/upload/reupload.
* No fake traffic, bot engagement, platform evasion, IP/VPS tricks, scraping, spam, manipulation.
* VCOS DB and supplied snapshots are source of truth.
* Google Drive is media storage/archive, not DB truth.

C2 common_channel_contract

* Treat ChannelProfileVersion and CompiledChannelPolicySnapshot as binding production truth.
* Force agents to follow channel identity, target audience, market/locale, language, editorial strategy, content pillars, format policy, tone/style, platform strategy, media/provider policy, rights/disclosure policy, budget policy, learning policy, forbidden behavior.
* Do not infer missing channel strategy.
* Do not override human config.
* Do not suggest config upgrade unless human explicitly asks.
* Missing/incomplete/stale/contradictory contract => REVIEW_REQUIRED or BLOCK.

C3 common_output_contract

* JSON-only when schema requires JSON.
* No markdown/code fences/prose outside schema.
* Required fields include limitations, confidence_label, risk_level, next_action, operator_summary_vi, technical_appendix.
* No unknown fields.

C4 common_evidence_handling

* Use only provided refs.
* Do not invent metrics/sources/assets/rights/analytics/screenshots/citations.
* Preserve ZERO vs UNKNOWN vs UNAVAILABLE.
* Missing data is not zero.
* Stale data is not fresh.

C5 common_policy_safety

* No fake engagement, comment spam, reupload spam, malicious clickbait, platform/detection evasion, repetitive synthetic churn without meaningful difference.
* Unsafe request => BLOCK or REVIEW_REQUIRED with safe alternative.

C6 common_operator_language

* operator_summary_vi must be Vietnamese unless schema says otherwise.
* Short, clear, friendly.
* Raw enums/details go to technical_appendix.

C7 common_market_locale_context

* Adapt to target market/locale from channel contract.
* Use only provided primary_market, secondary_markets, audience_locale, content_language, operator_language, timezone, currency, units, date_format, cultural_style, examples preference, regulatory sensitivity.
* Do not assume US unless configured.
* Do not assume English unless configured.
* Do not invent local laws/events/audience behavior/search demand/culture.
* No fake local relevance, country manipulation, unsupported local claims, over-localized clickbait.

C8 common_media_constraints

* Voice: ElevenLabs.
* AI hero: Google Vertex Veo only.
* Veo model: veo-3.1-fast-generate-001.
* Veo mode: video_only, 1080p, audio=false.
* Veo durations: 4, 6, 8 seconds only.
* Renderer: Creatomate Growth 10K.
* Creatomate is CLOUD_RENDER_ENGINE and final renderer when configured.
* Google Drive access: CTA only.
* Do not propose Runway/Luma/Envato as production default.
* Do not use Veo for diagrams/data charts.

C9 common_cost_awareness

* Prefer safe reuse.
* Do not overuse Veo.
* Do not claim exact spend if unavailable.
* M12 budget display is hard-env cap only, not actual spend tracking.

C10 common_rights_disclosure

* Check rights_envelope, source_manifest, music_policy, ai_disclosure flags.
* Do not invent license evidence.
* Flag realistic AI media/disclosure needs.
* Preserve disclosure meaning in localization.

C11 common_json_repair

* Repair syntax/shape only.
* Do not add claims/evidence refs/metrics/rights.
* Do not change decision semantics.
* Max repair attempts: 2.
* Never turn BLOCK into PASS.

## Channel Contract Schema

ChannelContract should include:

channel_identity:

* channel_name
* channel_type
* niche
* positioning
* brand_promise
* platform_targets
* series_plan

target_audience:

* primary_persona
* audience_level
* pain_points
* desired_outcome

market_locale:

* primary_market
* secondary_markets
* audience_locale
* content_language
* operator_language
* timezone
* currency
* measurement_units
* date_format
* cultural_style
* market_examples_preference
* regulatory_sensitivity
* market_locale_context_status

editorial_strategy:

* content_pillars
* allowed_angles
* forbidden_angles
* claim_style
* allowed_topics
* forbidden_topics

format_policy:

* long_form.enabled
* long_form.target_duration_minutes_min
* long_form.target_duration_minutes_max
* long_form.structure
* shorts.enabled
* shorts.target_duration_seconds_min
* shorts.target_duration_seconds_max
* shorts.hard_max_seconds
* captions.required
* chapters.required_for_long_form

voice_style:

* narration_tone
* pacing
* allowed_style
* forbidden_style

platform_strategy:

* primary_platform
* youtube_is_learning_authority
* secondary_platforms
* disabled_authorities
* publish_mode
* auto_publish_allowed
* studio_scraping_allowed

media_policy:

* voice_provider
* ai_hero_provider
* ai_hero_model_id
* ai_hero_allowed_durations_seconds
* ai_hero_default_duration_seconds
* ai_hero_audio
* ai_hero_allowed_use
* ai_hero_forbidden_use
* renderer
* storage_archive

rights_policy:

* source_manifest_required
* rights_evidence_required
* ai_disclosure_required_when_ai_media_used
* synthetic_media_warning_required_when_applicable
* music_policy
* reused_content_sensitivity

budget_policy:

* monthly_budget_usd
* cost_sensitivity
* avoid_unnecessary_ai_hero
* prefer_reuse_safe_assets
* exact_cost_claim_requires_provider_snapshot

learning_policy:

* authority
* min_evidence_required
* auto_promote_learning
* config_mutation_by_agent_allowed
* weak_evidence_action

forbidden_behavior:

* fake_traffic
* bot_engagement
* spam_reupload
* algorithm_manipulation
* platform_evasion
* ip_vps_tricks
* youtube_studio_scraping
* dashboard_scraping
* invented_metrics
* invented_sources
* invented_rights
* unsupported_local_claims

contract_status:

* COMPLETE
* PARTIAL
* MISSING
* STALE
* CONTRADICTORY

## MarketLocaleContext Schema

Fields:

* primary_market
* secondary_markets
* audience_locale
* content_language
* operator_language
* timezone
* currency
* measurement_units
* date_format
* cultural_style
* market_examples_preference
* regulatory_sensitivity
* market_locale_context_status: KNOWN | PARTIAL | UNKNOWN

## Required Agents

M12.1 must cover these agents:

* ChannelAuthorityAgent
* TopicIdeaScoringAgent
* ResearchPackSummarizer
* ScriptPlanningAgent
* ScriptWriterAgent
* ScriptRewriteAgent
* PublishingMetadataAgent
* VisualPlanningAgent
* ThumbnailBriefAgent
* GatekeeperSoftReviewAgent
* LearningCandidateService
* EvidenceBundleSummarizer
* PostPublishSummaryAgent
* EngineeringArchitectAgent
* ShortCandidateExtractor
* ShortCandidateRanker
* DerivativeOriginalityReviewer
* RecoveryProposalReviewer
* LocalizationSubtitleAgent
* LocalizedMetadataAgent
* PublishTimingSummaryAgent
* ProviderReadinessSummaryAgent
* MediaQCExplanationAgent
* RightsDisclosureReviewer
* UploadCardCopyAgent

## Output Contract

Every agent output must use BaseEnvelope:

* contract_version
* agent_key
* status: OK | REVIEW_REQUIRED | BLOCK | REFUSAL | ERROR
* confidence_label: LOW | MEDIUM | HIGH
* risk_level: LOW | MEDIUM | HIGH | CRITICAL | null
* evidence_refs
* limitations
* next_action
* operator_summary_vi
* technical_appendix
* artifact

## Prompt Registry Architecture

M12.1 should add:

* PromptTemplateRegistry
* AgentPromptProfile
* PromptContractVersion
* StructuredOutputSchema
* PromptRenderRun
* PromptAuditSnapshot
* PromptEvaluationCase
* PromptEvaluationRun

Prompt templates must include:

* agent_key
* template_key
* template_version
* status
* allowed_router_lanes
* default_router_lane
* input_contract
* output_contract
* output_schema_ref
* common_skill_refs
* safety_policy_refs
* system_delta file
* user_template file
* required_vars
* optional_vars
* forbidden
* examples
* tests

## Message Format

Production agents must use chat messages:
[
{"role": "system", "content": "common skills + agent delta + safety boundaries + output contract"},
{"role": "user", "content": "task payload + channel contract refs + required output instruction"}
]

Do not use one raw prompt string for production agents.
Keep legacy raw prompt compatibility for old callers.

## Prompt Hashing

prompt_hash =
sha256(normalized(system_prompt_compiled) + normalized(user_prompt_template) + output_schema_ref + template_version + common_skill_versions)

prompt_context_hash =
sha256(normalized(render_vars_json) + channel_profile_version_id + compiled_policy_snapshot_id + hash(channel_contract_json) + hash(market_locale_context_json if provided) + artifact_refs_sorted)

## Safety / Non-goals

Do not build:

* new provider strategy
* auto publish/upload/reupload
* YouTube upload API
* fake traffic/bot engagement/platform evasion
* dashboard scraping/browser automation
* prompt self-mutation
* DB-only canonical prompt authoring
* silent semantic JSON repair
* channel config mutation
* config upgrade suggestion
* learning auto-promotion
* real provider calls by default
* old provider smoke rerun

## Old Smoke Test Rule

Do not rerun old M12 provider smoke tests.
Do not enable real provider flags.
Do not spend provider credits.
Focused M12.1 mock/unit/contract/eval tests are allowed.
