# M5 Daily Run, Context, And Admission

## Scope

M5 builds the first AI-assisted workflow path for VCOS:

- Editorial calendar slots and manual channel daily runs.
- ResourceResolver MVP with immutable RetrievalPlanSnapshot and ContextPackSnapshot.
- ChannelStatePackSnapshot derived only from M1-M4 state.
- Safe SearchDemandEvidence and deterministic IdeaMarketPreflight.
- ChannelAuthorityService using MockLLMProvider only.
- LLMRunSnapshot capture for every mock LLM authority attempt.
- Deterministic ProjectAdmissionDecision and budgeted admission into M2 VideoProject.
- Initial allowed draft artifacts only: creative_brief, research_pack, source_pack.

M5 is mock-first. LLM output is proposal, draft, and rationale only. M5 does not approve, publish, render media, ingest assets, scrape sources, or call real providers.

## Non-Scope

M5 does not build:

- M6 media/render/QC pipeline, thumbnail compositor, TTS/audio/video generation, or asset ingestion.
- M7 publish/upload/manual publish.
- M8 analytics/semantic layer.
- M9 no-view/recovery/self-funding.
- M10 memory promotion workflow.
- M11 dashboard/operator cockpit.
- Real provider integrations or external network calls.
- Vector/RAG engine or source scraping/parser.
- OPA/Cedar/general policy engine.
- Algorithm/Growth/View agents or platform evasion systems.

## TopicBank

M5 uses `editorial_calendar_slots` as a production demand envelope. A slot is not raw topic truth and is not a TopicBank item. M5 does not reintroduce TopicBank as a core domain table.

## Daily Run Lifecycle

1. Create an explicit `editorial_calendar_slot` or run without a slot.
2. Create `channel_daily_run` with explicit `company_id`, `channel_workspace_id`, and `policy_snapshot_id`.
3. Execute manually through API/CLI.
4. Execution builds a RetrievalPlanSnapshot, ContextPackSnapshot, and ChannelStatePackSnapshot.
5. Mock authority creates an LLMRunSnapshot and DailyIdeaDecision only when allowed idea-source context exists.
6. IdeaMarketPreflight performs deterministic demand/channel-fit checks.
7. ProjectAdmissionDecision deterministically admits, blocks, skips, or requires review.

No daily run looks up latest profile or latest policy snapshot.

## ResourceResolver MVP

ResourceResolverService is the only path for building ContextPackSnapshot. It requires explicit scope and explicit allowed sources. It rejects vector/RAG, all-company memory, source scraping, autosuggest, and secret-like payloads.

ContextPackSnapshot is scoped by company/channel/profile/policy/project/slot where applicable. Its hash is deterministic from canonical content and refs.

## Numeric Truth Contract

Numeric truth comes from SQL/read-model state or evidence rows. M5 has no real analytics, so performance metrics are absent or UNKNOWN. LLM output may mention only proposal/rationale fields and must not invent metrics.

## Mock LLM Workflow

ChannelAuthorityService uses MockLLMProvider only. Provider registry, provider health, quota, budget, cost, provider attempt, and LLMRunSnapshot records provide the audit trail.

Authority proposal is source-bound. It may derive ideas only from EditorialCalendarSlot, SearchDemandEvidence, explicit manual/test fixture inputs, or scoped ContextPackSnapshot/ChannelStatePackSnapshot references carrying those inputs. Missing or weak context returns BLOCKED with reason codes instead of fabricating an idea.

Mock LLM structured output is strict Pydantic-validated before it can become DailyIdeaDecision or draft ArtifactVersion content. Malformed output, schema mismatch, unavailable, quota-blocked, or budget-blocked paths fail safely and create no VideoProject.

## RenderSpec Contract

M5 does not create render_spec artifacts and does not render media, call FFmpeg, ingest media, or generate TTS. The M5 RenderSpec draft schema is contract-only for M6: voice timeline is master, each scene carries narration refs and preferred source placeholders, timings must be valid, and overlaps/gaps must be explicit.

## Search Demand Policy

SearchDemandEvidence supports safe manual, CSV, internal, official, and mock sources. M5 does not scrape web pages or autosuggest surfaces. Weak or missing evidence can return REVIEW_REQUIRED; non-search-led ideas are not hard-blocked only because volume is unknown.

## Project Admission

ProjectAdmissionDecision is deterministic and auditable. ADMIT creates a VideoProject using the exact policy snapshot from the daily run and creates only creative_brief, research_pack, and source_pack draft artifact versions. REVIEW_REQUIRED and BLOCK create no project.

M6 can later consume admitted projects and these initial artifacts to build production script, visual plan, render spec, media resource flow, and QC artifacts.

## Testing

M5 tests require no license and no real provider. Tests use MockLLMProvider only and do not make external network calls.
