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
