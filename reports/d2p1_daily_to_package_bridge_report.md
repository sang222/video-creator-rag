# D2P1 Daily-to-Package Bridge Report

## Status

PASS — DailyIdeaDecision now drives the existing M5 admission, Effective Context, research handoff, and M12.2 scripted package without topic re-entry or media execution.

## Repository mapping

| Requirement | Repository authority |
| --- | --- |
| Request, receipt, and status contracts | app/contracts/d2p1.py |
| Orchestrator, state machine, resume, idempotency | app/services/d2p1.py |
| Existing admission and candidate-project savepoint | app/services/m5.py |
| Existing Effective Context | app/services/r3d2.py |
| Existing scripted package | app/services/m12_2.py |
| Read-only HTTP surface | app/api/routes/production_planning.py |
| Focused regression | tests/test_d2p1_daily_to_package_bridge.py |

DailyToPackageRequest primarily accepts daily_idea_decision_id plus optional exact research/operator control refs. Extra fields are forbidden, so topic, channel, category, pillar, and policy cannot be manually repeated or overridden.

## Entry and immutable admission

The orchestrator validates the immutable DailyIdeaDecision, daily run, strict slot/category/pillar/series/goal, authoritative digest, exact CH1-FLEX v2 profile/snapshot, topic gate, derived channel fit, and all ref/hash bindings.

DailyIdeaDecision remains immutable at its original admissible status. M5 records the transition as an immutable ProjectAdmissionDecision with decision ADMIT; it does not update DailyIdeaDecision to ADMITTED. The earliest admission receipt is authoritative and idempotent.

For new work, the selected v2 snapshot must be active. After ADMIT, resume uses the frozen project snapshot even if the channel later activates another policy; the old snapshot must remain approved/active and all scope/hashes must still recompile exactly. No latest-profile, latest-snapshot, or latest-preflight lookup can replace frozen admission lineage.

The admitted project freezes channel, profile v2, compiled snapshot/hash, Channel Contract/hash, NicheContractDigest ref/hash, DailyIdeaDecision ref/hash, EditorialSlot ref/hash, category, pillar, series, production goal, topic, and angle.

## Effective Context transaction boundary

Project admission creates the candidate VideoProject and compiles Effective Context inside one nested transaction. If Effective Context is not PASS, that savepoint rolls back the candidate project and initial artifacts, then persists one terminal BLOCK admission receipt with CANDIDATE_PROJECT_ROLLED_BACK. Reruns return the same receipt and cannot create another project.

On PASS, Effective Context must match company, channel, project, profile, compiled snapshot, category, channel-contract hash, and frozen niche lineage before research or package work proceeds.

## Research handoff and resume

Research is never fabricated. Resolution order is:

1. the explicitly selected approved ResearchPack version;
2. an approved current ResearchPack;
3. an approved current source/evidence pack;
4. otherwise one idempotent ResearchAssignment ReviewTask.

The research artifact must bind project, decision, channel, profile, snapshot, digest, slot, category, pillar, series, topic, and angle. Missing research produces AWAITING_RESEARCH, a durable resumable state. The same decision reuses the same project and assignment. When an approved exact research version appears, the next run resumes and creates one package.

## Scripted package and niche gates

M12.2 receives an authoritative lineage envelope containing the decision, admission/project, Effective Context, digest, slot, and approved research refs/hashes. Topic and angle are derived from that lineage.

Before package creation, topic, channel fit, Effective Context, research approval, and ScriptNicheAlignmentGate must PASS. Package readiness additionally requires Visual, Thumbnail, and Metadata niche gates. The consolidated production NicheAlignmentDossier must include all five gate results and PASS overall.

The package remains no_media = true and human_review_only = true. PACKAGE_READY_FOR_HUMAN_REVIEW means the scripted package and all gate evidence exist; it never means rendered, archived, uploaded, or published.

## State, receipt, and idempotency

The explicit states are:

1. DAILY_DECISION_ACCEPTED
2. PROJECT_ADMITTED
3. EFFECTIVE_CONTEXT_READY
4. AWAITING_RESEARCH
5. RESEARCH_READY
6. PACKAGE_BUILDING
7. PACKAGE_READY_FOR_HUMAN_REVIEW
8. BLOCKED_POLICY
9. FAILED_TECHNICAL

The d2p1.daily-to-package-receipt.v1 ArtifactVersion binds the orchestrator version, decision, admission/project, profile/snapshot, Effective Context, digest, slot, research assignment/pack, package, all niche gates, human-review state, execution counters, blockers, exact next action, and idempotency fingerprint.

The fingerprint binds the decision hash, admission hash, profile, compiled snapshot hash, digest hash, slot hash, exact research hash, and package-builder version. Same lineage returns the same project, assignment, package, and final review task. Stale lineage blocks rather than silently overwriting.

## Read-only status surface

GET /daily-idea-decisions/{decision_id}/production-handoff exposes current state, project, Effective Context, research, package, niche gates, blockers, receipt, human-review state, and exact next action.

The status path performs no admission or workflow advance. When no receipt exists, it recomputes entry validity read-only, so a corrupt or stale entry remains BLOCKED_POLICY instead of appearing ready. Tests confirm no project, admission, or artifact-version write occurs during status reads.

## Verification and zero-execution proof

- D2P1 focused suite: 15 passed.
- CH1 + NICH1 focused suite: 22 passed.
- Required eight-file master suite: 148 passed, 1 non-blocking dependency warning.
- Shared M5/R3D2/R3D3/R3D4/M12.1/M12.2 suite: 107 passed, 12 historical mock-contract skips, 1 non-blocking dependency warning.
- Happy path reaches one scripted package with all five gates PASS and human review PENDING.
- Resume path creates one project and one research assignment, then one package after approval.
- Negative tests cover exact-v2 lookalikes, Effective Context rollback, digest forgery, stale frozen lineage, research mismatch, downstream gate failure, provider attempts, and read-only status.

The D2P1 guard measures cumulative provider/media rows and blocks on any nonzero delta. Test fixtures record zero. Production CH1 activation also recorded zero deltas across ProviderAttempt, ProviderJobSnapshot, paid ledger, LLM runs, MediaRenderJob, FinalMediaRef, Drive rows, upload tasks, and uploaded videos. No Pexels, Gemini Image, Veo, ElevenLabs, FFmpeg, Drive, YouTube, or MR1 call occurred.

## Exact next action

Leave the package at human review, keep PKG1 revision and MR1 on hold, and wait for a separate operator-started LPRO1 task. D2P1 does not start LPRO1.
