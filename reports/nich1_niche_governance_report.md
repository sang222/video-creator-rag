# NICH1 Niche Governance Report

## Status

PASS — bounded semantic niche truth is compiled, routed, enforced, frozen at admission, and required by the scripted-package gate chain.

## Entry problem and repair

The prior runtime could carry profile and policy hashes without enough semantic content to decide whether an idea, script, visual plan, thumbnail, or metadata still belonged to the channel. NICH1 adds an authoritative NicheContractDigest, strict editorial-slot contract, internally derived channel fit, five typed gates, and one consolidated NicheAlignmentDossier. Caller-supplied digest content and policy_fit_state are not authority.

## Repository mapping

| Requirement | Repository authority |
| --- | --- |
| Digest, bindings, gate results, dossier contracts | app/contracts/nich1.py |
| Compiler, slot validator, five gates, dossier builder | app/services/nich1.py |
| Daily context, router, cost firewall, preflight, admission | app/services/m5.py |
| DailyIdeaAgent Prompt Registry entry | app/prompts/registry/agents.yaml |
| DailyIdeaAgent system delta | app/prompts/agents/system_deltas/daily_idea_agent.md |
| Effective Context niche projection | app/services/r3d2.py |
| Bounded AgentContextPack projection | app/services/r3d3.py |
| Strict downstream package gates | app/services/r3d4.py and app/services/m12_2.py |
| Deterministic gate catalog | config/gate_definition_catalog.yaml |
| Focused regression | tests/test_nich1_niche_governance.py |

## Authoritative NicheContractDigest

NicheContractDigestCompiler deterministically derives nich1.niche-contract-digest.v1 from the COMPLETE Channel Contract, approved/active profile, compiled snapshot, active channel-owned category, and strict slot. It contains bounded semantic niche/sub-niche, positioning, brand promise, market/language/locale, audience segments/pains/outcomes, channel and category topic rules, category, pillar, series, production goal, voice/tone, format, and visual-source profile.

The compiler verifies profile and snapshot hashes, channel/category/slot scope, required semantic fields, and canonical content hash. Strict daily production requires the active snapshot. An already-admitted D2P1 project may recompile its frozen snapshot after that snapshot becomes approved historical; this narrow mode still requires exact scope, approved/active status, and all hashes, and is reachable only after an ADMIT receipt and project exist.

Daily Context stores the full bounded digest plus ref/hash, runtime guard, active snapshot ref/hash, category/pillar/series/goal, evidence digest, and a DailyIdeaAgent AgentContextPack. Reserved authority fields cannot be replaced by caller input. Both M5 and D2P1 recompile the digest and compare full semantic content, so a self-hashed forgery cannot pass.

## Editorial slot and topic governance

Strict production slots require category_id, content_pillar_id or key, series_key, and production_goal. Category/channel scope, pillar membership, optional series mappings, profile/snapshot binding, and forbidden production goals are checked before generation/admission. Legacy slots remain readable but cannot enter strict production without these bindings.

TopicNicheAlignmentGate evaluates niche, pillar, category/sub-niche, audience, positioning, brand promise, allow/forbid rules, series, production goal, and claim scope. Hard conflicts block deterministically. Semantic evidence must include criterion-level scores and meaningful scope overlap; one generic anchor cannot auto-pass an adjacent-niche topic.

## Daily LLM and channel-fit enforcement

DailyIdeaAgent is registered on the cheap_structured lane and rendered through Prompt Registry. M5 calls LLMRouterService, requires an auditable persisted LLMRunSnapshot, and has no direct provider SDK or hard-coded provider. An unavailable route, missing snapshot, schema failure, REVIEW_REQUIRED/BLOCK agent envelope, or budget failure propagates closed. Tests use fake routers and make no real LLM call.

PromptBudgetGate and the cost firewall always enforce the frozen compiled cap. An optional runtime budget key cannot loosen it.

The compiled channel-fit threshold is 0.78, deterministically reused from the approved Pexels semantic-fit threshold with ref/version/hash authority. M5 validates score range and evidence, persists score/threshold/result, and derives policy_fit_state. A caller-provided PASS is recorded only as ignored input. Scores below threshold and failed topic evidence cannot PASS or admit a project.

## Mandatory gate registry and ordering

The production chain is:

1. strict slot validation;
2. TopicNicheAlignmentGate;
3. M5 ProjectAdmissionDecision;
4. Effective Context PASS;
5. ScriptNicheAlignmentGate;
6. VisualNicheAlignmentGate;
7. ThumbnailNicheAlignmentGate;
8. MetadataNicheAlignmentGate;
9. package human review.

All five niche gates are typed, content-hashed, evidence-bound, and registered. Script binds the approved decision and digest. Visual governance is separate from VQC and enforces STOCK_ASSISTED, native/diagram-first mechanism meaning, defensible AI imagery, and authorized evidence assets. Thumbnail and metadata gates reject adjacent-niche or misleading promises.

R3D4 and M12.2 cannot claim strict package readiness when any mandatory gate is missing or BLOCK.

## NicheAlignmentDossier

The dossier binds channel/profile/policy, digest, slot/category/pillar, topic/script/visual/thumbnail/metadata results, channel-fit score/threshold, reason codes, human-review requirements, and overall verdict. PRE_ADMISSION requires topic plus channel-fit PASS. PRODUCTION_PACKAGE requires all five gates. Any mandatory BLOCK makes the overall dossier BLOCK.

## Verification evidence

- NICH1 plus CH1 focused suite: 22 passed.
- D2P1 authoritative-lineage suite: 15 passed.
- Required master suite: 148 passed, 1 non-blocking dependency warning.
- Shared M5/R3D2/R3D3/R3D4/M12.1/M12.2 suite: 107 passed, 12 intentional historical mock-contract skips, 1 non-blocking dependency warning.
- M12.1 validator compatibility plus NICH1 envelope propagation: 11 passed.
- No real LLM or media provider ran.

## Boundary and exact next action

NICH1 added no Pexels, Gemini Image, Veo, ElevenLabs, FFmpeg, Drive, FinalMediaRef, YouTube, or MR1 execution. Keep MR1 and PKG1 revision on hold. LPRO1 may begin only in a separate operator-started task.
