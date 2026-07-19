# M12.2 scripted package and D2P1 bridge

D2P1 connects an admissible M5 DailyIdeaDecision to the existing M12.2 `FirstScriptedVideoPackageService`. The public orchestration input is the DailyIdeaDecision ID plus optional operator/research controls. Topic, angle, category, pillar, channel, profile, snapshot, and policy hashes are resolved from authoritative lineage and cannot be overridden by the caller.

## Entry and frozen lineage

`DailyToPackageOrchestrator` requires:

- an immutable `PROPOSED` DailyIdeaDecision eligible for M5 admission, or the same decision with an existing immutable M5 `ProjectAdmissionDecision=ADMIT` receipt, plus its matching daily run/context pack;
- a strict valid EditorialCalendarSlot and ContentCategory;
- active CH1-FLEX v2 profile/snapshot matching the NicheContractDigest;
- TopicNicheAlignmentGate PASS and derived channel-fit PASS;
- matching category, pillar, series, production goal, topic, and angle.

It reuses the earliest existing M5 ADMIT receipt for the DailyIdeaDecision, or invokes the existing M5 admission service once. DailyIdeaDecision itself remains immutable at `PROPOSED`; `ProjectAdmissionDecision=ADMIT` is the transition authority. The admitted VideoProject freezes profile/snapshot/channel-contract, digest, DailyIdeaDecision, slot, category/pillar/series/goal, and topic/angle refs/hashes. A mismatch blocks; the orchestrator never looks up a newer profile or preflight after admission.

Effective Context is compiled or resolved through the existing compiler and must be PASS with the same company/channel/project/profile/snapshot/category and channel-contract hash before package work continues.

## Research handoff

Research is never fabricated. Resolution order is the caller-selected exact version, then the current ResearchPack, then the current SourcePack. The selected ArtifactVersion must belong to the admitted project, be current, and have an exact approval decision.

If research exists but is not approved, D2P1 creates or reuses one evidence ReviewTask bound to the project, decision, digest, slot, topic/category/pillar/series. The durable state is `AWAITING_RESEARCH`, which is a resumable workflow state rather than a technical failure. A rerun resumes when an approved current version is present.

## Package boundary

After `RESEARCH_READY`, D2P1 calls the existing M12.2 package service with values derived from the frozen lineage and approved research. It verifies that the resulting package binds the exact project, channel, profile, compiled snapshot, Effective Context ID/hash, and D2P1 idempotency fingerprint.

The package remains `no_media=true` and `human_review_only=true`. Topic and channel-fit evidence must already PASS; package readiness additionally requires Script, Visual, Thumbnail, and Metadata niche gate evidence. `PACKAGE_READY_FOR_HUMAN_REVIEW` is legal only when the M12.2 package status is ready and all five niche gates PASS. D2P1 then creates or reuses an exact final-human ReviewTask. It does not render, generate media, upload to Drive, or publish.

## Durable state and idempotency

The state machine is:

```text
DAILY_DECISION_ACCEPTED
  -> PROJECT_ADMITTED
  -> EFFECTIVE_CONTEXT_READY
  -> AWAITING_RESEARCH | RESEARCH_READY
  -> PACKAGE_BUILDING
  -> PACKAGE_READY_FOR_HUMAN_REVIEW

Any step -> BLOCKED_POLICY | FAILED_TECHNICAL
```

`BLOCKED_POLICY` cannot bypass policy truth. `FAILED_TECHNICAL` retains `last_successful_state` so the same lineage can be retried.

The fingerprint binds DailyIdeaDecision, admission and project hashes, profile ID/input hash, compiled snapshot ID/hash, NicheContractDigest hash, EditorialSlot hash, approved ResearchPack hash, and package-builder version. The same fingerprint returns the existing project/package; research assignments and final review tasks are also idempotent. Changed/stale lineage requires an explicit new version rather than mutation.

Each durable transition is an immutable `idea_admission_lineage` ArtifactVersion carrying `d2p1.daily-to-package-receipt.v1`: all authoritative refs/hashes, last successful state, niche-gate refs, human-review state, blockers, exact next action, fingerprint, and zero provider/media call counts.

## Read-only status

`GET /daily-idea-decisions/{decision_id}/production-handoff` returns current project, Effective Context, research, package, niche gates, blockers, receipt, human-review state, and exact next action. It never compiles, creates, calls a provider, or advances workflow state.
