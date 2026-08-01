# Editorial Research, Context, And Admission

## Scope

M5 builds the first AI-assisted workflow path for VCOS:

- Editorial calendar research slots and immutable `EditorialResearchRun` rows.
- ResourceResolver MVP with immutable RetrievalPlanSnapshot and ContextPackSnapshot.
- ChannelStatePackSnapshot derived only from M1-M4 state.
- Safe SearchDemandEvidence and deterministic IdeaMarketPreflight.
- Editorial idea research routed through the registered
  `EditorialIdeaResearchAgent`, Prompt Registry, and `LLMRouter` boundary.
- LLMRunSnapshot capture for every routed authority attempt, including injected offline fixtures.
- Deterministic ProjectAdmissionDecision and budgeted admission into M2 VideoProject.
- Initial allowed draft artifacts only: creative_brief, research_pack, source_pack.
- NICH1 strict editorial-slot validation, bounded niche context, topic/channel-fit gates, and frozen admission lineage for CH1-FLEX v2.

LLM output is proposal, draft, and rationale only. M5 does not approve, publish, render media, ingest assets, scrape sources, or call media providers. The production path requests the approved router lane and fails closed when the LLM runtime guard/provider is unavailable. Tests inject an explicit offline fixture through the same workflow boundary; there is no silent mock fallback.

## Non-Scope

M5 does not build:

- M6 media/render/QC pipeline, thumbnail compositor, TTS/audio/video generation, or asset ingestion.
- M7 publish/upload/manual publish.
- M8 analytics/semantic layer.
- M9 no-view/recovery/self-funding.
- M10 memory promotion workflow.
- M11 dashboard/operator cockpit.
- New direct provider integrations, direct provider SDK calls, or unguarded
  external network calls. Editorial research uses the existing guarded
  LLMRouter boundary only.
- Vector/RAG engine or source scraping/parser.
- OPA/Cedar/general policy engine.
- Algorithm/Growth/View agents or platform evasion systems.

## TopicBank

M5 uses `editorial_calendar_slots` as a production demand envelope. A slot is not raw topic truth and is not a TopicBank item. M5 does not reintroduce TopicBank as a core domain table.

## Editorial Research Lifecycle

1. Create an explicit `editorial_calendar_slot` with `slot_type=RESEARCH`. A
   strict v2 slot binds category, pillar, production goal, active profile, and
   exact policy snapshot.
2. Create `editorial_research_run` with explicit company, channel, profile,
   policy, slot, and run date.
3. Execute through the authenticated API or the durable worker boundary.
4. Execution builds a RetrievalPlanSnapshot, ContextPackSnapshot, and ChannelStatePackSnapshot.
5. `EditorialIdeaResearchAgent` is rendered through Prompt Registry and routed
   through `LLMRouter`; an `EditorialIdeaCandidate` is created only from
   schema-valid, bounded output and auditable evidence.
6. IdeaMarketPreflight performs deterministic demand checks plus NICH1 topic/channel-fit checks for a strict v2 snapshot.
7. The candidate remains non-production until cadence selects a deterministic
   `GREENLIT` candidate; `ProjectAdmissionDecision` then freezes exact
   niche/channel/slot lineage.

No editorial research run looks up an ambiguous latest profile or policy.
Research alone never creates a VideoProject, render, archive, final review
candidate, or upload task.

## ResourceResolver MVP

ResourceResolverService is the only path for building ContextPackSnapshot. It requires explicit scope and explicit allowed sources. It rejects vector/RAG, all-company memory, source scraping, autosuggest, and secret-like payloads.

ContextPackSnapshot is scoped by company/channel/profile/policy/project/slot where applicable. Its hash is deterministic from canonical content and refs.

For strict v2 editorial research, allowed source `niche_contract_digest` causes
ResourceResolverService to compile the authoritative `NicheContractDigest` from
the exact channel contract, profile, policy snapshot, ContentCategory, and
EditorialCalendarSlot. The persisted pack contains bounded semantic content,
not only a hash:

- `niche_contract_digest` and its ref/hash;
- `editorial_slot_digest` for category/pillar/series/goal;
- `runtime_guard_digest` with provider calls disabled;
- bounded `evidence_digest` and `common_skill_digest`;
- the `EditorialIdeaResearchAgent` context contract and PromptBudgetGate report.

Missing, stale, cross-channel, or over-budget context blocks before prompt render.

## Numeric Truth Contract

Numeric truth comes from SQL/read-model state or evidence rows. M5 has no real analytics, so performance metrics are absent or UNKNOWN. LLM output may mention only proposal/rationale fields and must not invent metrics.

## EditorialIdeaResearchAgent LLM workflow

The research boundary accepts only the guarded `llm_router`, renders the
repo-registered `EditorialIdeaResearchAgent` template on lane
`cheap_structured`, validates the BaseEnvelope and typed editorial artifact,
then records the LLM run/attempt/cost-firewall evidence exposed by the router
path.

Authority proposal is source-bound. It may derive ideas only from EditorialCalendarSlot, SearchDemandEvidence, explicit manual/test fixture inputs, or scoped ContextPackSnapshot/ChannelStatePackSnapshot references carrying those inputs. Missing or weak context returns BLOCKED with reason codes instead of fabricating an idea.

Structured output must contain the topic, angle, format, pillar, bounded
evidence, confidence, budget readiness, rights/policy state, and quality state.
It is strict Pydantic-validated before it can become an
`EditorialIdeaCandidate`. Malformed output, schema mismatch, unavailable route,
quota block, or budget block fails safely and creates no VideoProject.

Offline tests may inject a deterministic LLM workflow fixture. The fixture is explicit, still emits an auditable run snapshot, and does not bypass Prompt Registry/context/schema or downstream niche gates.

## RenderSpec Contract

M5 does not create render_spec artifacts and does not render media, call FFmpeg, ingest media, or generate TTS. The M5 RenderSpec draft schema is contract-only for M6: voice timeline is master, each scene carries narration refs and preferred source placeholders, timings must be valid, and overlaps/gaps must be explicit.

## Search Demand Policy

SearchDemandEvidence supports safe manual, CSV, internal, official, and mock sources. M5 does not scrape web pages or autosuggest surfaces. Weak or missing evidence can return REVIEW_REQUIRED; non-search-led ideas are not hard-blocked only because volume is unknown.

## Project Admission

ProjectAdmissionDecision is deterministic and auditable. ADMIT creates a
VideoProject using the exact policy snapshot from the candidate and preflight
lineage. BLOCK creates no project.

`EditorialIdeaCandidate` is immutable research input whose explicit stage
transitions remain auditable. `ProjectAdmissionDecision` is the production
transition authority. VideoProject creation and Effective Context compilation
run inside one transaction; a non-PASS Effective Context rolls the candidate
start back and persists an idempotent BLOCK admission receipt instead of
leaking a partial project.

For CH1-FLEX v2, caller-provided `policy_fit_state` is not policy truth. NICH1 derives the threshold from the compiled snapshot, evaluates the score against the TopicNicheAlignmentGate, and persists score/threshold/result/reason/evidence in the preflight. Admission requires both the topic gate and derived channel fit to PASS.

The admitted project freezes profile/policy/channel-contract refs and hashes,
`NicheContractDigest`, `EditorialIdeaCandidate`, EditorialSlot, category,
pillar, series or standalone assignment, production goal, topic/angle,
topic-gate evidence, and the pre-admission `NicheAlignmentDossier`. Existing
admissions reuse their exact frozen preflight and never resolve a newer row.
Effective Context later validates this frozen lineage and blocks stale or
cross-channel bindings.

M6 can later consume admitted projects and these initial artifacts to build production script, visual plan, render spec, media resource flow, and QC artifacts.

## Testing

M5/NICH1 tests require no license and no real provider. They use injected offline LLM output where a successful proposal is needed and make no external network calls.
