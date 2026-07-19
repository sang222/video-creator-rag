# NICH1 niche governance

NICH1 makes niche alignment a typed, hash-bound production contract. It extends the existing channel/profile, M5, Effective Context, AgentContextPack, and R3D4 flows; it does not create a second channel contract, project domain, or package domain.

## Authoritative digest

`NicheContractDigestCompiler` compiles `nich1.niche-contract-digest.v1` only from frozen authority objects:

- active ChannelWorkspace, approved/active ChannelProfileVersion, active CompiledChannelPolicySnapshot, and COMPLETE Channel Contract for new strict work;
- active channel-owned ContentCategory;
- strict EditorialCalendarSlot bound to the same company, channel, snapshot, category, pillar, series, and production goal.

The digest contains semantic content plus immutable refs/hashes: primary niche/sub-niche, positioning, brand promise, market/language/locale, audience segments/pains/outcomes, channel and category allowed/forbidden topics, category, pillar, series, production goal, voice/format summary, and visual-source profile. Canonical sorted JSON produces the content hash. Scope, status, profile hash, snapshot hash, category hash, or slot mismatch blocks compilation. After an immutable ADMIT receipt exists, D2P1 may recompile the exact frozen snapshot while it is approved historical; that narrow mode does not permit latest-snapshot substitution and still requires exact hashes and scope.

Legacy slots remain readable. A CH1-FLEX v2 slot is production-eligible only when `EditorialSlotValidator(strict_production=True)` returns PASS; missing category/pillar/series/goal is not silently inferred.

## Daily idea and admission

ResourceResolver stores the full bounded digest and its ref/hash in ContextPackSnapshot together with editorial-slot, runtime-guard, evidence, common-skill, and prompt-budget sections. `DailyIdeaAgent` receives that allowlisted context through Prompt Registry and LLMRouter. It proposes an evidence-bounded `channel_fit_score`; it does not decide policy fit.

IdeaMarketPreflight evaluates:

1. deterministic demand evidence;
2. `TopicNicheAlignmentGate` against the digest and semantic evidence;
3. the proposed score against `gate_policy.channel_fit_threshold` from the compiled snapshot.

`policy_fit_state` is derived from those checks. A caller-provided value is recorded as ignored, not accepted as authority. Missing topic evidence, a score below the threshold, or a BLOCK gate blocks admission.

On ADMIT, M5 freezes the digest and DailyIdeaDecision/slot/category/pillar/series/goal/topic lineage in VideoProject state. Effective Context validates hashes and scope, exposes the bounded niche context to downstream agents, and extends ScriptContractDigest with niche, positioning, brand promise, allowed/forbidden topics, audience pains/outcomes, category/pillar, and digest ref/hash.

## Mandatory gate chain

The strict order is:

```text
Topic -> Script -> Visual -> Thumbnail -> Metadata
```

All gates produce typed `NicheGateResult` values with PASS, REVIEW_REQUIRED, or BLOCK. They require the active snapshot and digest binding, subject hash, criterion-level semantic evidence, reason codes, and evidence refs.

- Topic checks niche/audience/positioning/brand, allowed and forbidden topics, category/pillar/series/goal, claim scope, and adjacent-niche conflict.
- Script checks fidelity to the approved idea, declared niche/category/pillar, audience pains/outcomes, claim scope, and upstream topic PASS.
- Visual checks channel visual direction, `STOCK_ASSISTED` source policy, scene source decisions, mechanism meaning, AI-image justification, and authorized evidence assets.
- Thumbnail checks promise/topic/niche fidelity, text/number claim evidence, forbidden topics, and misleading UI/product representation.
- Metadata checks title/description/tags/chapters/copy/CTA fidelity, category/pillar, claims/evidence, forbidden topics, and adjacent-niche conflict.

R3D4 registers all five gates and includes them in the final package gate list. Missing mandatory evidence is a BLOCK for strict v2 work.

## Dossier and compatibility

`NicheAlignmentDossier` binds the channel contract, profile, snapshot, digest, slot, category, pillar, series, channel-fit evaluation, and available gate results. `PRE_ADMISSION` requires topic and channel-fit PASS. `PRODUCTION_PACKAGE` requires all five gates before a package can be ready for human review.

No historical v1 profile, snapshot, project, slot, gate run, or artifact is rewritten. Legacy work remains readable; new strict-v2 production paths fail closed when authoritative niche lineage is absent or stale. NICH1 performs no media generation, rendering, Drive upload, or YouTube action.
