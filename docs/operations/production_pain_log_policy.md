# ProductionPainLog Policy

ProductionPainLog stores non-blocking production pain after Runtime LTS v1.

## Goes into ProductionPainLog

- P2/P3 operator friction.
- Repeated manual steps that are safe but annoying.
- UI wording/status confusion.
- Missing read-only report/filter/sort.
- Slow workflow with workaround.
- Docs or runbook gap.

## Does not qualify as P0/P1 by itself

- Preference for a new dashboard shortcut.
- Cosmetic UI issue.
- Extra click where workflow still completes.
- Nice-to-have provider activation.
- Refactor request without runtime break.
- Single non-reproducible annoyance.

## Required fields

- `id`
- `reported_at`
- `owner`
- `status`: `NEW | TRIAGED | BATCHED | ACCEPTED | DEFERRED | CLOSED`
- `severity`: `P2 | P3`
- `area`
- `summary`
- `evidence_refs`
- `operator_impact`
- `workaround`
- `batch_review_target`

## Cadence

- Review every 2-4 weeks.
- Batch related items.
- Promote to P1 only with repeat evidence, operator-blocking impact, or freeze invariant risk.

## Example entries

```yaml
- id: PPL-001
  severity: P2
  area: r3d9_ops
  summary: Provider/cost panel needs package filter by channel.
  status: NEW
  owner: ops
  workaround: Open package summary from Command Center card.
  batch_review_target: 2026-07-31

- id: PPL-002
  severity: P3
  area: docs
  summary: Add screenshot to manual upload runbook.
  status: TRIAGED
  owner: docs
  workaround: Existing text checklist is usable.
  batch_review_target: 2026-08-14
```

No immediate backend/core refactor is allowed for P2/P3 without batch approval.
