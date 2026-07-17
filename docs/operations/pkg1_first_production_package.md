# PKG1 first production package

PKG1 builds a complete pre-provider package for `small-team-ai`. It stops at human package review. It does not authorize TTS, alignment, stock acquisition, Veo, production render, Drive archive, or YouTube activity.

## Entry and frozen authority

The builder requires `CH1_FLEX_FINAL=PASS` and `PROCEED_TO_PKG1=true` in `reports/ch1_flex_summary.json`. For a new package it also requires the approved profile v1, the channel's active compiled policy snapshot, the active channel-scoped creative policy, and manual YouTube publishing.

Idea selection and IdeaGate happen before `VideoProject` creation. `VideoProjectService` then freezes the profile ID and the native-render, creative-quality, provider-use, budget, and FormatIdentityContract refs and hashes. Downstream compilation reads those frozen values. An idempotent rerun returns the existing package without resolving a newer active profile.

## Artifact workflow

The package is stored as immutable `ArtifactVersion` rows. The initial package contains:

- idea/admission lineage, ResearchPack, SourcePack, CreativeBrief, and Script;
- SpokenTextNormalized, advisory narration pacing preflight, and ClaimEvidenceLedger;
- EpisodeOriginalityManifest, VisualDirectionContract, VisualPlan, and compiled asset request drafts;
- caption-policy binding without cues or SRT;
- planning-only cost, provider, paid-attempt, rights, disclosure, publish, and manual-publish artifacts;
- deterministic gate results and a package manifest.

Previous versions are never edited. An automatic repair creates a child `ArtifactVersion`, a new `GateRun`, and review evidence. At most two automatic revision cycles are accepted.

## Temporal and provider boundary

`NarrationPacingPreflightEstimate` is advisory. Scene plans contain editorial order, spoken-token span intent, and duration ranges, never canonical timestamps. Final caption cues and a strict native render plan wait for final audio, forced alignment, verified narration alignment, and `CanonicalMediaTimeline`.

Provider plans expose future request counts, but every stage remains disabled or waiting for its prerequisite. `actual_cost` stays `null`. The builder snapshots row counts before and after construction and blocks if provider jobs, paid ledgers, render jobs, final media, upload tasks, uploaded videos, media offload jobs, or cloud media refs change.

## Read-only operator surface

- `GET /video-projects/{project_id}/production-package-readiness`
- `GET /video-projects/{project_id}/pkg1`
- `GET /video-projects/{project_id}/provider-execution-plan`

These routes expose frozen lineage, artifact versions, gate results, cost, planned request counts, blockers, human review state, and the exact next action. There is no PKG1 execution endpoint.

## Human handoff

Technical PASS creates one open `final_human` review task. The package remains `PKG1_HUMAN_REVIEW=PENDING`, `MR1_PAID_EXECUTION_APPROVAL=PENDING`, and `PROCEED_TO_MR1=false`. Only the operator may approve the package and separately open MR1.
