# M8 Analytics Sync

## Scope

M8 turns an M7 `UploadedVideo` into append-only analytics history and a latest metrics read model.

Included:

- `AnalyticsSyncRun`
- mock/manual/local import analytics providers
- `AnalyticsSnapshot`
- `MetricAvailabilitySnapshot`
- `TrafficSourceSnapshot`
- `RetentionCurveSnapshot`
- `EngagementSnapshot`
- `UploadedVideoMetricsSummary`
- API, CLI, config catalogs, audit/domain events, and tests

M8 reads `UploadedVideo`; it does not create uploaded videos.

## Non-Scope

M8 does not diagnose no-view or underperformance. It does not decide that a video failed. It does not create recovery proposals, title or thumbnail advice, dashboards, auto upload, real platform analytics integrations, OAuth, token refresh, scraping, vector/RAG, OPA/Cedar, Algorithm/Growth/View agents, fake traffic, bot engagement, platform evasion, IP tricks, or auto reupload.

## Lifecycle

1. M7 creates `UploadedVideo` with `monitoring_state=READY_FOR_ANALYTICS`.
2. M8 creates an `AnalyticsSyncRun` for that uploaded video.
3. A local mock/manual/import provider returns validated analytics output.
4. M8 creates an append-only `AnalyticsSnapshot`.
5. M8 records metric availability, traffic sources, retention curve, and engagement snapshots when data is supplied or marks absence explicitly.
6. M8 updates `UploadedVideoMetricsSummary` as a derived read model.

`AnalyticsSyncRun` may be `BLOCKED` or `FAILED`, but those states are operational provider/import states only.

## Metric Truth

`metrics_blob` stores raw imported/provider metric values.

`normalized_metrics_blob` stores normalized values with unit, platform, source, provider key, captured timestamp, and raw or computed provenance.

Missing metrics are `UNKNOWN`, not zero.

Platform-unsupported metrics are `NOT_AVAILABLE`, not zero.

Zero values remain available numeric values.

## Freshness And Confidence

Snapshots carry `freshness_state`:

- `FRESH`
- `STALE`
- `UNKNOWN`
- `NOT_AVAILABLE`

Snapshots carry `confidence_level`:

- `HIGH`
- `MEDIUM`
- `LOW`
- `UNKNOWN`

Manual imports default to medium confidence when metric values exist and low confidence when no metrics exist. Mock sync is high confidence for deterministic fixture data.

## Traffic Sources

`TrafficSourceSnapshot` stores source rows and summary only. Missing traffic data is represented as `UNKNOWN`/`NOT_AVAILABLE`; it is not treated as success.

## Retention Curve

`RetentionCurveSnapshot` stores sorted retention points. Points must be non-negative and within known duration when duration is known.

Retention drops are not interpreted in M8.

## Metrics Summary

`UploadedVideoMetricsSummary` is derived from latest snapshots. It is not source truth.

Operator summaries are operational only:

- `Analytics synced successfully`
- `Some metrics are not available yet`
- `No analytics data imported yet`
- `Analytics provider unavailable`

Allowed next actions are operational import/sync/wait actions only.

## M9 Consumption

M9 may later consume M8 snapshots and summaries for diagnostics. M8 itself emits no no-view diagnosis, recovery proposal, title/thumbnail recommendation, or dashboard UI.
