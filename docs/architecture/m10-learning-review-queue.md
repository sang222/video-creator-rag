# M10 Learning Review Queue

M10 prepares learning candidates from stored M8/M9 evidence. It does not approve learning, promote playbooks, build dashboard UI, or mutate channel configuration.

## Lifecycle

`UploadedVideo` plus `AnalyticsSnapshot`, `UploadedVideoMetricsSummary`, `FailureTraceReport`, and `RecoveryProposal` create a `LearningCandidateGenerationRun`.

Executing the run creates:

- `LearningCandidate`
- `LearningEvidenceBundle`
- `LearningPromotionEligibilityRun`
- `LearningReviewQueueItem`
- `PlaybookCandidateDraft` only when eligible for review

The final state for a reusable learning is `READY_FOR_HUMAN_REVIEW`. That means M11 may show it to a human. It does not mean approved.

## Evidence Contract

M10 only uses stored refs from M7, M8, and M9. Evidence bundles preserve:

- source video and project refs
- analytics snapshot refs
- diagnostic refs
- recovery proposal refs
- metric support with availability, freshness, confidence, and source snapshot id
- limitations
- counter-evidence
- policy and rights summary

Zero remains numeric zero. Unknown metrics remain `UNKNOWN`. Unsupported metrics remain `NOT_AVAILABLE`.

## Eligibility Gate

The eligibility gate is deterministic.

- Fresh analytics and enough metric support can produce `ELIGIBLE_FOR_REVIEW`.
- Stale or unknown analytics produces `NEEDS_MORE_EVIDENCE` unless the candidate type does not require analytics.
- Policy or rights risk produces `BLOCKED`.
- Weak or missing evidence produces `NEEDS_MORE_EVIDENCE` or `INELIGIBLE`.

The gate never changes profile, policy, pipeline, title, thumbnail, or production settings.

## Review Queue

`LearningReviewQueueItem` is M11-ready read data. It includes operator summary, friendly status, evidence summary, confidence label, risk level, recommended scope, source refs, audit refs, technical appendix, next action, and future allowed actions.

Allowed future actions are only labels for M11:

- `APPROVE`
- `REJECT`
- `REQUEST_MORE_EVIDENCE`
- `SUPPRESS`
- `EXPIRE`

M10 does not implement those mutations in API or CLI.

## Playbook Candidate Draft

`PlaybookCandidateDraft` stores draft text only. It is not an approved playbook entry and is not used by ResourceResolver or ContextPack.

M11 approval and promotion are required before any draft can become reusable guidance.

## Explicit Non-Scope

M10 does not build dashboard/operator cockpit UI, approval/reject CLI, human approval actions, approved playbook promotion, ChannelProfileVersion mutation, CompiledPolicySnapshot mutation, config review CTA, config upgrade suggestion, suggested config patch, derivative/reuse/shorts/cross-platform funnel backend, real Ollama/LLMRouter, ElevenLabs/Creatomate/AI Hero/cloud renderer integrations, media provider routing, auto publish/upload/reupload, analytics sync, no-view diagnostics, scraping, vector/RAG, OPA/Cedar, Algorithm/Growth/View agents, fake traffic, bot engagement, platform evasion, or IP/VPS tactics.

## Deferred

M10.1 owns real Ollama LLMRouter and derivative/reuse/shorts/cross-platform funnel backend.

M10.2 owns Media Provider Role Matrix and provider routing.

M11 owns dashboard approval UX/actions, playbook promotion UX, and human-owned channel config editing.
