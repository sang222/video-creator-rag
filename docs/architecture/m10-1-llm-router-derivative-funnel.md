# M10.1 LLM Router And Derivative Funnel

M10.1 adds a guarded Ollama LLMRouter foundation and backend contracts for long-form to Shorts derivatives, reuse/originality governance, cross-platform export packages, upload cards, and manual upload tasks.

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

## Derivatives And Shorts

Long-form YouTube remains the canonical asset. Shorts are selected derivatives, not mass-generated filler. `ShortCandidateExtractionService` reads stored voice timeline/caption/visual snapshots, creates understandable standalone candidates, and does not force a fixed count when the source lacks strong windows.

`ShortCandidateRankingService` computes deterministic `ShortValueScore` from hook strength, standalone clarity, insight density, visual punch, audience relevance, bridge value, and production reuse saving, minus context dependency, policy risk, and generic template penalties.

## Originality And Reuse

`DerivativeOriginalityCheck` gates derivative reuse. Shorts may reuse parent runtime heavily when standalone value is clear. Follow-up long-form candidates require new narrative arc and new examples. Raw compilations cannot be publish-allowed without new framing/context.

`ReusableArtifact` stores rights/license/reuse scope and cooldown policy. Reuse means governed reuse, not unlimited reuse. Manual stock sources remain manual and are not automated providers.

## Funnel And Upload

`CrossPlatformFunnelPackage` is YouTube-first. TikTok and Facebook are export/support surfaces only in M10.1. `UploadCard` prepares platform-native copy, CTA, disclosure, music policy, and paste-back requirements. `HumanUploadTask` is manual only and must link back to `UploadedVideo` through the existing human confirmation flow when an actual upload is completed.

`UploadedVideo` remains the canonical published video record. YouTube analytics remains the learning authority. TikTok/Facebook analytics learning loops are deferred.

## Deferred

M10.2 owns Media Provider Role Matrix and real media provider routing, including ElevenLabs, Creatomate, AI Hero, and cloud final renderer integration.

M11 owns dashboard/operator cockpit UI, approval actions, upload task dashboard, derivative graph dashboard, learning promotion UX, and human-owned channel config editing.
