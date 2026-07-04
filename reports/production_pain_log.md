# Production Pain Log

Runtime LTS v1 rule:
- P0/P1 may trigger immediate patch review.
- P2/P3 must be logged and batch-reviewed every 2-4 weeks.
- No backend/core change for P2/P3 without approved patch window.

Severity rules:
- P0 = safety/security/data-integrity/policy/provider/upload leak
- P1 = operator-blocking runtime defect or freeze invariant regression
- P2 = workflow friction, confusing UI/copy, missing convenience view
- P3 = polish/nice-to-have

| pain_id | date | severity | area | symptom | operator_impact | evidence_ref | expected_behavior | actual_behavior | decision | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PPL-INT1-001 | 2026-07-04 | P2 | qualification_tests | Existing M10.1/M10.5 qualification suites still assert pre-R3D10 migration/config/helper behavior. | INT smoke requires focused evidence instead of whole-suite green. | `reports/int1_post_freeze_integration_smoke_ollama_drive_snowball_report.md` | Qualification tests should match Runtime LTS head and current helper contracts. | Stale tests expect `0021_m12_2r_handoff_ledger`, old `mock_mode` helper behavior, or old Drive config defaults. | Batch-review test maintenance; no backend/core patch in INT1. | NEW |
| PPL-INT1-002 | 2026-07-04 | P2 | drive_archive_smoke | Drive archive smoke config was enabled but OAuth token needed reauth. | Archive smoke was blocked until operator reconnected Drive. | `reports/int1_post_freeze_integration_smoke_ollama_drive_snowball_report.md` | Tiny JSON archive artifact uploads only to configured Drive archive folder. | Reauth completed; focused rerun created CloudMediaRef `c5f489b8-d09d-4818-8146-2206e487d76f` with media_type `OTHER`. | Resolved by OAuth reauth and focused Drive rerun; no backend/core patch. | RESOLVED |
