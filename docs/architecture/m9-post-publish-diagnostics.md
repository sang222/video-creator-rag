# M9 Post-Publish Diagnostics

M9 diagnoses uploaded video health after publish. It reads M7 `UploadedVideo` records and M8 analytics snapshots/summaries only.

## Scope

- Deterministic observation windows: `T_PLUS_1H`, `T_PLUS_6H`, `T_PLUS_24H`, `T_PLUS_48H`, `T_PLUS_7D`.
- PostPublishHealthRun orchestration.
- No-view, packaging/CTR, retention, engagement, and policy/rights/disclosure diagnostics.
- FailureTraceReport with operator summary and technical appendix.
- RecoveryProposal records that require human approval.
- Manual action queue integration for review/wait tasks.

## Non-Scope

- No analytics sync.
- No dashboard or operator cockpit UI.
- No memory promotion.
- No auto publish/upload/reupload.
- No platform metadata edit.
- No real provider/OAuth/platform call.
- No scraping.
- No vector/RAG, OPA/Cedar, Algorithm/Growth/View agents.
- No fake traffic, bot engagement, platform evasion, or IP/VPS tricks.

## Flow

`UploadedVideo -> UploadedVideoMetricsSummary / AnalyticsSnapshot -> Observation Window Check -> PostPublishHealthRun -> diagnostics -> FailureTraceReport -> RecoveryProposal -> ManualAction when needed`

M9 returns `INSUFFICIENT_DATA` when the observation window is not ready or required metrics are unavailable. Zero metrics remain numeric zero and are not treated as missing.

## Operator Report

Every failure trace report carries:

- `operator_summary`
- `friendly_status`
- `severity_label`
- `confidence_label`
- `likely_cause_label`
- `evidence_plain_text`
- `next_action`
- `do_not_do`
- `owner_role`
- `due_at`
- `checklist`
- `technical_appendix`

Technical codes remain available in the appendix. The default report is human-friendly.

## Recovery Proposal

Allowed proposal types are wait/monitor, review title/thumbnail, review hook, review retention section, review rights/disclosure, review source quality, create future variant, and no action.

Forbidden actions are automatic reupload, automatic platform edit, fake engagement, bought views, bot comments/likes, platform evasion, and automatic publish.

## Future Milestones

M10 consumes M9 evidence to prepare learning candidates and M11-ready review queue items. M10 does not approve or promote those candidates. M11 may display M9/M10 summaries in a dashboard later.
