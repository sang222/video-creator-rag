# Manual Pilot 001 Plan

Pilot status: planned only. Do not execute this pilot in this task.

## Objective

Verify that the frozen VCOS Runtime LTS v1 can be operated through dashboard/manual ops while preserving all post-freeze boundaries. The pilot validates package and handoff evidence only; it does not generate real media and does not call providers.

## Non-Goals

- Do not create real video/media.
- Do not submit provider render jobs.
- Do not search/download Pexels.
- Do not upload to Drive.
- Do not upload, publish, schedule, or reupload to YouTube.
- Do not add dashboard execute/generate/render/upload controls.
- Do not patch backend/core for P2/P3 findings.

## Operator Flow

1. Confirm Runtime LTS v1 baseline
   - Read `reports/runtime_lts_v1_baseline.md`.
   - Confirm commit SHA, expected tag, migration head, and verifier result.
   - Confirm accepted mode is dashboard/manual-ops/read-only.

2. Channel Init / Contract
   - Select an existing safe channel fixture or channel draft prepared for manual review.
   - Confirm Channel Contract is the runtime authority.
   - Confirm no agent is allowed to mutate `ChannelProfileVersion` or latest mutable channel settings.

3. EffectiveChannelRuntimeContextSnapshot
   - Confirm the selected project/package references an `EffectiveChannelRuntimeContextSnapshot`.
   - Confirm the snapshot is the package/project runtime context.
   - Evidence ref should include snapshot id, hash/content hash, channel id, and project/package id.

4. AgentContextPack
   - Confirm an `AgentContextPackSnapshot` exists for the package.
   - Confirm the pack is compact and replayable.
   - Confirm the pack uses digest/ref/hash and does not include raw memory text, full prior artifacts, or mutable channel settings.

5. Prompt/context digest
   - Confirm `PromptRenderRun` and `PromptAuditSnapshot` refs are present where expected.
   - Confirm prompt/context evidence is hash-based and replayable.
   - Confirm prompt budget and shape gates are active.

6. First scripted package
   - Review the existing first scripted package or package fixture.
   - Confirm the package summary is production-ready as a handoff artifact only.

7. R3D4 output validation/deterministic gates
   - Review `AgentOutputValidationRun`, `R3D4GateBatchRun`, and `R3D4GateRun` summaries.
   - Confirm gates pass or show expected blocker/review states.
   - Confirm media-ready conflict is blocked if deterministic gate state is unsafe or unknown.

8. Packaging handoff
   - Review M1/M12.2R handoff evidence.
   - Confirm package checklist, title/description/subtitle/disclosure refs, thumbnail/card refs, and manual next action exist.
   - Confirm the handoff creates only a manual publish skeleton, not an upload.

9. R3D9 dashboard review
   - Open or inspect the R3D9 dashboard/read model.
   - Expected panels: command center, runtime trace, package ops summary, gate summary, provider/cost firewall, retrieval/memory summaries, manual upload/publish handoff, uploaded-video monitor if a pasted-back video exists.
   - Confirm the dashboard has no job-control buttons for daily, no-view, vector, provider, render, upload, YouTube, execute, generate, or run actions.

10. Provider/Cost panel read-only boundary
    - Review provider readiness/cost blocker summary.
    - Confirm stale provider keys remain inactive/rejected.
    - Confirm cost/firewall state is read-only and `will_execute=false`.

11. Manual publish handoff skeleton
    - Confirm the handoff skeleton contains copy, checklist, disclosure, timing recommendation, and paste-back instructions.
    - Confirm no Drive or YouTube upload is attempted.
    - Confirm any human upload happens outside VCOS and is outside this pilot unless a later task explicitly asks for it.

12. ProductionPainLog handling
    - If any issue appears, classify it before changing code.
    - Log P2/P3 in `reports/production_pain_log.md`.
    - Escalate only P0/P1 to immediate patch review.

13. Final pilot evidence capture
    - Capture artifact ids, dashboard/read-model refs, gate status, provider/cost boundary status, manual next action, and no-execution proofs.
    - Stop after evidence capture. Do not execute provider/media/upload actions.

## Expected Artifacts

- package summary
- runtime trace
- gate result summary
- provider readiness/cost blocker summary
- manual next action
- publish handoff skeleton
- ProductionPainLog entries if issues occur

## Expected Dashboard Panels

- Command Center / next safe actions
- Runtime trace / effective context snapshot
- Package ops summary
- R3D4 gate result summary
- Provider / Cost Firewall read-only panel
- Retrieval manifest and memory influence summary
- Manual publish handoff / upload task skeleton
- Uploaded video monitor only for already pasted-back manual records

## Expected Blocker States

- Missing effective runtime context snapshot: P0/P1 BLOCKED, do not continue package review.
- Missing AgentContextPackSnapshot: P0 BLOCKED, do not continue prompt/package review.
- Prompt payload contains raw mutable context or raw memory text: P0 BLOCKED.
- R3D4 deterministic gate missing/error: P1 BLOCKED.
- Unknown gatekeeper result: REVIEW_REQUIRED; do not advance to media-ready state.
- Provider stack drift or stale active provider key: P0/P1 BLOCKED.
- Any provider/media/upload execution attempt: P0 BLOCKED and immediate patch review.
- Dashboard job-control button appears: P1 BLOCKED unless it creates execution leakage, then P0.
- P2/P3 workflow friction: log only, continue if safe.

## Expected Evidence Refs

- `reports/runtime_lts_v1_baseline.md`
- `reports/r3d10_runtime_lts_freeze_report.md`
- `docs/architecture/runtime_lts_v1.md`
- `docs/architecture/provider_stack_freeze.md`
- `docs/operations/post_freeze_protocol.md`
- `tests/test_r3d10_runtime_lts_freeze.py`
- Runtime evidence ids: channel id, project id, package id, effective snapshot id, context pack id, prompt run/audit refs, gate batch/run ids, handoff/ledger refs
- Dashboard/read-model refs: `/ops/runtime-lts-freeze-check`, R3D9 command center/read model, provider/cost firewall summary

## Expected No-Execution Proofs

- no `ProviderAttempt` created
- no `MediaRenderJob` created
- no provider job submitted
- no external provider network call
- no Drive upload
- no YouTube upload
- no Pexels download/search
- no dashboard execute button
- verifier/read model says `no_provider_media_upload_execution=true`

## Acceptance Checklist

- Runtime baseline reviewed and still PASS.
- Channel Contract is runtime authority.
- EffectiveChannelRuntimeContextSnapshot exists and is referenced.
- AgentContextPackSnapshot exists and is compact/replayable.
- Prompt/context digest refs are replayable.
- First scripted package summary is reviewable without media execution.
- R3D4 deterministic gates are PASS or safely BLOCKED/REVIEW_REQUIRED.
- Packaging handoff produces manual publish skeleton only.
- R3D9 dashboard panels are read-only/manual-ops.
- Provider/Cost panel is read-only and `will_execute=false`.
- No provider/media/upload execution proof is captured.
- P0/P1/P2/P3 findings are classified correctly.
- P2/P3 findings, if any, are logged in `reports/production_pain_log.md`.

## Failure Handling Rule

Stop the pilot at the first P0/P1 blocker, preserve evidence, and do not continue toward package handoff or dashboard review until the blocker is triaged. P0/P1 may trigger immediate patch review. P2/P3 must be logged in ProductionPainLog and batch-reviewed every 2-4 weeks; do not patch backend/core immediately for P2/P3.

## What To Log As P0/P1/P2/P3

| Severity | Log when |
| --- | --- |
| P0 | safety/security/data-integrity/policy/provider leak/upload leak, provider execution, upload/publish/reupload automation, raw prompt context leak, runtime truth corruption |
| P1 | operator-blocking runtime defect, deterministic gate regression, missing required runtime snapshot/pack, dashboard/manual flow blocked without workaround, freeze invariant regression |
| P2 | workflow friction, confusing UI/copy/status, missing convenience view, non-blocking evidence/reporting gap |
| P3 | polish, naming cleanup, minor docs/UI copy, non-urgent refactor |

## Separate Execution Rule

Manual Pilot 001 execution is a separate next task. This file is the source-of-truth plan for that next task and must not be treated as approval to execute the pilot, create media, call provider APIs, or upload anything.
