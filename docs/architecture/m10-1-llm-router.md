# M10.1 LLM Router

M10.1 provides the guarded LLMRouter foundation used by active long-form
research, planning, scripting, metadata, visual, quality, and learning agents.

## LLMRouter

Real execution is disabled by default. VCOS calls local Ollama only when `VCOS_LLM_REAL_EXECUTION_ENABLED=true`, `VCOS_LLM_PROVIDER=ollama`, and the router profile/lane are enabled. The real smoke test is skipped unless `VCOS_LLM_ROUTER_REAL_SMOKE=true`.

Router lanes:

- `cheap_structured`: primary `gpt-oss:20b-cloud`, fallback `qwen3.5:cloud`
- `default_multimodal`: primary `qwen3.5:cloud`, fallback `gemma4:31b-cloud`
- `visual_creative_review`: primary `minimax-m3:cloud`, fallback `qwen3.5:cloud`, emergency `gemma4:31b-cloud`
- `long_context_text`: primary `deepseek-v4-flash:cloud`, fallback `nemotron-3-super:cloud`, premium `deepseek-v4-flash:cloud`
- `engineering_architect`: primary `qwen3-coder:480b-cloud`, fallback `kimi-k2.7-code:cloud`, backup `deepseek-v4-flash:cloud`
- `gatekeeper_soft_review`: primary `nemotron-3-super:cloud`, fallback `deepseek-v4-flash:cloud`, premium `deepseek-v4-flash:cloud`

No route may use the excluded model family. Business services route by lane name, not by hardcoded runtime model. Model configuration is explicit by lane role through `VCOS_LLM_MODEL_<LANE>_<ROLE>` environment variables. Agents map to lanes only; they do not own separate model defaults.

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
