# M10.1 LLM Router

M10.1 provides the guarded LLMRouter foundation used by active long-form
research, planning, scripting, metadata, visual, quality, and learning agents.

## LLMRouter

Real execution is disabled by default. VCOS calls the OpenAI Responses API only when `OPENAI_API_KEY` is available, `VCOS_LLM_REAL_EXECUTION_ENABLED=true`, `VCOS_LLM_PROVIDER=openai`, and the router profile/lane are enabled. The bounded Luna/Terra smoke is skipped unless `VCOS_LLM_ROUTER_REAL_SMOKE=true`.

Router lanes:

- `cheap_structured`: `gpt-5.6-luna`, reasoning `none`
- `default_multimodal`: `gpt-5.6-terra`, reasoning `low`
- `visual_creative_review`: `gpt-5.6-terra`, reasoning `medium`
- `long_context_text`: `gpt-5.6-terra`, reasoning `medium`
- `engineering_architect`: `gpt-5.6-terra`, reasoning `high`
- `gatekeeper_soft_review`: `gpt-5.6-terra`, reasoning `medium`

The mapping is source-controlled and admits only Luna and Terra. Each lane has one model, with no automatic model/provider fallback, premium override, emergency route, or backup route. Business services route by lane name, not by hardcoded runtime model. Agents map to lanes only; they do not own separate model defaults.

## Agent Mapping

- `ChannelAuthorityAgent`: `cheap_structured`, optionally `long_context_text`
- `EditorialIdeaResearchAgent`: `cheap_structured`
- `TopicIdeaScoringAgent`: `cheap_structured`
- `ResearchPackSummarizer`: `long_context_text`
- `ScriptPlanningAgent`: `long_context_text`
- `ScriptWriterAgent`: `long_context_text`
- `ScriptRewriteAgent`: `long_context_text`
- `PublishingMetadataAgent`: `cheap_structured`
- `VisualPlanningAgent`: `visual_creative_review`, `long_context_text`
- `ThumbnailBriefAgent`: `visual_creative_review`
- `GatekeeperSoftReviewAgent`: `gatekeeper_soft_review`
- `LearningCandidateService`: rule-based first, optional `cheap_structured` phrasing only
- `EvidenceBundleSummarizer`: `cheap_structured` or `long_context_text`
- `PostPublishSummaryAgent`: `cheap_structured`
- `EngineeringArchitectAgent`: `engineering_architect`, internal/dev only

## Runtime Boundary

Business services request a registered lane and task type. Every successful
result carries an LLMRunSnapshot; direct provider SDK calls from business
services are forbidden. Quota, cost, schema, policy, and runtime-guard failures
fail closed. No LLM output mutates channel policy, selects the final upload
decision, or publishes.

`UploadedVideo` remains canonical publication truth. M11 and later surfaces own
operator review, learning promotion, and human-owned channel configuration.
