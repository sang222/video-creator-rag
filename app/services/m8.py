from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, EventEnvelope
from app.contracts.m8 import (
    AnalyticsProviderOutputContract,
    AnalyticsSyncRunCreate,
    AnalyticsSyncRunExecuteRequest,
    KNOWN_ANALYTICS_METRICS,
    ManualAnalyticsImportContract,
    MetricAvailabilityItem,
)
from app.contracts.ops import ProviderAttemptMockRequest
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AnalyticsSnapshot,
    AnalyticsSyncRun,
    EngagementSnapshot,
    MetricAvailabilitySnapshot,
    MetricDefinitionVersion,
    ProviderAttempt,
    RenderPackageSnapshot,
    RetentionCurveSnapshot,
    TrafficSourceSnapshot,
    UploadedVideo,
    UploadedVideoMetricsSummary,
)
from app.providers.mock import MockAnalyticsProvider
from app.services.audit import AuditService
from app.services.domain_events import DomainEventBus
from app.services.ops import RetryOpsService


SECRET_KEY_FRAGMENTS = {"secret", "password", "token", "api_key", "apikey", "private_key", "credential_value"}
RAW_SECRET_MARKERS = ("sk-", "pk_live_", "BEGIN PRIVATE KEY", "anthropic-", "xoxb-", "ghp_")

METRIC_DEFINITIONS: list[dict[str, Any]] = [
    {
        "metric_key": "views",
        "metric_name": "Views",
        "metric_group": "REACH",
        "unit": "COUNT",
        "description": "Platform-reported view count for the observed window.",
    },
    {
        "metric_key": "impressions",
        "metric_name": "Impressions",
        "metric_group": "REACH",
        "unit": "COUNT",
        "description": "Platform-reported impressions for the observed window.",
    },
    {
        "metric_key": "click_through_rate",
        "metric_name": "Click Through Rate",
        "metric_group": "REACH",
        "unit": "PERCENT",
        "description": "Platform-reported or imported impression click-through rate.",
    },
    {
        "metric_key": "average_view_duration_seconds",
        "metric_name": "Average View Duration",
        "metric_group": "RETENTION",
        "unit": "SECONDS",
        "description": "Average watch duration in seconds.",
    },
    {
        "metric_key": "average_view_percentage",
        "metric_name": "Average View Percentage",
        "metric_group": "RETENTION",
        "unit": "PERCENT",
        "description": "Average percentage watched.",
    },
    {
        "metric_key": "watch_time_minutes",
        "metric_name": "Watch Time",
        "metric_group": "RETENTION",
        "unit": "MINUTES",
        "description": "Total watch time in minutes.",
    },
    {
        "metric_key": "likes",
        "metric_name": "Likes",
        "metric_group": "ENGAGEMENT",
        "unit": "COUNT",
        "description": "Like reactions.",
    },
    {
        "metric_key": "comments",
        "metric_name": "Comments",
        "metric_group": "ENGAGEMENT",
        "unit": "COUNT",
        "description": "Comment count.",
    },
    {
        "metric_key": "shares",
        "metric_name": "Shares",
        "metric_group": "ENGAGEMENT",
        "unit": "COUNT",
        "description": "Share count.",
    },
    {
        "metric_key": "subscribers_gained",
        "metric_name": "Subscribers Gained",
        "metric_group": "AUDIENCE",
        "unit": "COUNT",
        "description": "Subscribers or followers gained where available.",
    },
    {
        "metric_key": "subscribers_lost",
        "metric_name": "Subscribers Lost",
        "metric_group": "AUDIENCE",
        "unit": "COUNT",
        "description": "Subscribers or followers lost where available.",
    },
    {
        "metric_key": "reach",
        "metric_name": "Reach",
        "metric_group": "REACH",
        "unit": "COUNT",
        "description": "Unique reached audience when supplied by the platform or import.",
    },
    {
        "metric_key": "engagement_rate",
        "metric_name": "Engagement Rate",
        "metric_group": "ENGAGEMENT",
        "unit": "RATIO",
        "description": "Simple engagement ratio when supplied or computed from available numerator and denominator.",
    },
    {
        "metric_key": "saves",
        "metric_name": "Saves",
        "metric_group": "ENGAGEMENT",
        "unit": "COUNT",
        "description": "Saved item count when the platform provides it.",
    },
    {
        "metric_key": "bookmarks",
        "metric_name": "Bookmarks",
        "metric_group": "ENGAGEMENT",
        "unit": "COUNT",
        "description": "Bookmark count when the platform provides it.",
    },
    {
        "metric_key": "completion_rate",
        "metric_name": "Completion Rate",
        "metric_group": "RETENTION",
        "unit": "PERCENT",
        "description": "Completion percentage when supplied by the platform or import.",
    },
]

METRIC_UNITS = {item["metric_key"]: item["unit"] for item in METRIC_DEFINITIONS}
PLATFORM_NOT_AVAILABLE_METRICS = {
    "YOUTUBE": {"saves", "bookmarks"},
    "YOUTUBE_SHORTS": {"bookmarks"},
    "TIKTOK": {"subscribers_lost"},
    "FACEBOOK": set(),
    "INSTAGRAM": {"subscribers_lost"},
    "GENERIC": set(),
}
ENGAGEMENT_NUMERATOR_KEYS = ("likes", "comments", "shares", "saves", "bookmarks")


@dataclass(frozen=True)
class SnapshotSet:
    analytics_snapshot: AnalyticsSnapshot
    metric_availability_snapshot: MetricAvailabilitySnapshot
    traffic_source_snapshot: TrafficSourceSnapshot | None
    retention_curve_snapshot: RetentionCurveSnapshot | None
    engagement_snapshot: EngagementSnapshot | None
    metrics_summary: UploadedVideoMetricsSummary


class AnalyticsSyncService:
    def __init__(self, session: Session):
        self.session = session

    def seed_metric_definitions(self) -> list[MetricDefinitionVersion]:
        records: list[MetricDefinitionVersion] = []
        for item in METRIC_DEFINITIONS:
            existing = self.session.scalars(
                select(MetricDefinitionVersion).where(
                    MetricDefinitionVersion.metric_key == item["metric_key"],
                    MetricDefinitionVersion.platform == "GENERIC",
                    MetricDefinitionVersion.version == "1.0.0",
                )
            ).one_or_none()
            if existing is None:
                existing = MetricDefinitionVersion(
                    metric_key=item["metric_key"],
                    metric_name=item["metric_name"],
                    metric_group=item["metric_group"],
                    platform="GENERIC",
                    unit=item["unit"],
                    description=item["description"],
                    status="ACTIVE",
                    version="1.0.0",
                    metadata_={"m8_seed": True},
                )
                self.session.add(existing)
                self.session.flush()
            records.append(existing)
        return records

    def create_sync_run(
        self,
        *,
        data: AnalyticsSyncRunCreate,
        correlation_id: str = "m8-analytics-sync-create",
    ) -> AnalyticsSyncRun:
        uploaded = self._require_uploaded_video(data.uploaded_video_id)
        metadata = _jsonable(data.metadata)
        state = "PENDING"
        reason_codes = ["ANALYTICS_SYNC_CREATED", "NO_DIAGNOSIS_IN_M8", "NO_NETWORK_ANALYTICS_CALL"]
        next_action = "Run analytics sync again."
        provider_key = data.provider_key
        if data.sync_mode == "MOCK":
            provider_key = provider_key or "mock_analytics"
        if data.sync_mode == "REAL_DISABLED":
            state = "BLOCKED"
            reason_codes = ["ANALYTICS_SYNC_CREATED", "ANALYTICS_PROVIDER_REAL_DISABLED", "NO_NETWORK_ANALYTICS_CALL"]
            next_action = "Check provider credentials later."
        elif uploaded.monitoring_state != "READY_FOR_ANALYTICS":
            state = "BLOCKED"
            reason_codes = ["ANALYTICS_SYNC_CREATED", "UPLOADED_VIDEO_NOT_READY_FOR_ANALYTICS"]
            next_action = "Wait until uploaded video is ready for analytics."
        run = AnalyticsSyncRun(
            company_id=uploaded.company_id,
            channel_workspace_id=uploaded.channel_workspace_id,
            uploaded_video_id=uploaded.id,
            video_project_id=uploaded.video_project_id,
            policy_snapshot_id=uploaded.policy_snapshot_id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            sync_mode=data.sync_mode,
            sync_state=state,
            observed_from=data.observed_from,
            observed_to=data.observed_to,
            provider_key=provider_key,
            reason_codes=reason_codes,
            next_action=next_action,
            metadata_=metadata,
        )
        self.session.add(run)
        self.session.flush()
        _record_m8_event(
            self.session,
            event_type="analytics_sync_run.created",
            aggregate_type="analytics_sync_run",
            aggregate_id=run.id,
            target_type="analytics_sync_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code="ANALYTICS_SYNC_CREATED",
            payload={
                "uploaded_video_id": str(run.uploaded_video_id),
                "video_project_id": str(run.video_project_id),
                "policy_snapshot_id": str(run.policy_snapshot_id),
                "sync_mode": run.sync_mode,
                "sync_state": run.sync_state,
                "platform": run.platform,
                "no_network_analytics_call": True,
            },
        )
        if state == "BLOCKED":
            _record_m8_event(
                self.session,
                event_type="analytics_sync_run.blocked",
                aggregate_type="analytics_sync_run",
                aggregate_id=run.id,
                target_type="analytics_sync_run",
                target_id=run.id,
                company_id=run.company_id,
                correlation_id=correlation_id,
                reason_code=reason_codes[-1],
                payload={"reason_codes": reason_codes, "next_action": next_action, "sync_mode": run.sync_mode},
            )
            self._update_summary_blocked(uploaded=uploaded, run=run, correlation_id=correlation_id)
        return run

    def execute_sync_run(
        self,
        *,
        sync_run_id: uuid.UUID,
        data: AnalyticsSyncRunExecuteRequest | None = None,
        correlation_id: str = "m8-analytics-sync-execute",
    ) -> AnalyticsSyncRun:
        run = self.require_sync_run(sync_run_id)
        if run.sync_state in {"BLOCKED", "FAILED", "COMPLETED", "CANCELLED"}:
            return run
        uploaded = self._require_uploaded_video(run.uploaded_video_id)
        if uploaded.monitoring_state != "READY_FOR_ANALYTICS":
            self._block_run(
                run,
                reason_codes=["UPLOADED_VIDEO_NOT_READY_FOR_ANALYTICS"],
                next_action="Wait until uploaded video is ready for analytics.",
                correlation_id=correlation_id,
            )
            self._update_summary_blocked(uploaded=uploaded, run=run, correlation_id=correlation_id)
            return run
        if run.sync_mode == "REAL_DISABLED":
            self._block_run(
                run,
                reason_codes=["ANALYTICS_PROVIDER_REAL_DISABLED", "NO_NETWORK_ANALYTICS_CALL"],
                next_action="Check provider credentials later.",
                correlation_id=correlation_id,
            )
            self._update_summary_blocked(uploaded=uploaded, run=run, correlation_id=correlation_id)
            return run
        if run.sync_mode in {"MANUAL_IMPORT", "CSV_IMPORT"}:
            self._block_run(
                run,
                reason_codes=["MANUAL_ANALYTICS_IMPORTED"],
                next_action="Use analytics import-manual with local metrics payload.",
                correlation_id=correlation_id,
            )
            return run
        run.sync_state = "RUNNING"
        run.started_at = utc_now()
        run.reason_codes = _dedupe([*run.reason_codes, "ANALYTICS_PROVIDER_MOCK_USED", "NO_NETWORK_ANALYTICS_CALL"])
        self.session.flush()
        request = data or AnalyticsSyncRunExecuteRequest()
        attempt = RetryOpsService(self.session).record_mock_attempt(
            data=ProviderAttemptMockRequest(
                provider_key=run.provider_key or "mock_analytics",
                operation_key="analytics_sync",
                mode=request.mock_mode,  # type: ignore[arg-type]
                target_type="uploaded_video",
                target_id=uploaded.id,
                metadata={"analytics_sync_run_id": str(run.id), "no_network_analytics_call": True},
            ),
            correlation_id="m8-analytics-provider-attempt",
        )
        run.provider_attempt_id = attempt.id
        if attempt.status != "SUCCESS":
            state = "BLOCKED" if attempt.status in {"RETRYABLE_FAILURE", "CIRCUIT_OPEN", "QUOTA_REJECTED"} else "FAILED"
            self._finish_unsuccessful_run(
                run,
                sync_state=state,
                reason_codes=["ANALYTICS_SYNC_BLOCKED" if state == "BLOCKED" else "ANALYTICS_SYNC_FAILED", attempt.error_code or "ANALYTICS_SYNC_FAILED"],
                next_action="Run analytics sync again." if state == "BLOCKED" else "Inspect provider attempt before retry.",
                correlation_id=correlation_id,
            )
            self._update_summary_blocked(uploaded=uploaded, run=run, correlation_id=correlation_id)
            return run
        provider = MockAnalyticsProvider()
        response = provider.fetch_video_metrics(
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            published_at=uploaded.published_at,
            mode="success",
        )
        if not response.ok:
            self._finish_unsuccessful_run(
                run,
                sync_state="FAILED",
                reason_codes=["ANALYTICS_SYNC_FAILED", response.error_code or "ANALYTICS_SYNC_FAILED"],
                next_action="Run analytics sync again.",
                correlation_id=correlation_id,
            )
            self._update_summary_blocked(uploaded=uploaded, run=run, correlation_id=correlation_id)
            return run
        output = AnalyticsProviderOutputContract.model_validate(response.output)
        self._create_snapshot_set(
            run=run,
            uploaded=uploaded,
            output=output,
            source="MOCK_ANALYTICS",
            source_note="Deterministic local mock analytics provider.",
            correlation_id=correlation_id,
        )
        return run

    def import_manual(
        self,
        *,
        data: ManualAnalyticsImportContract,
        sync_mode: str = "MANUAL_IMPORT",
        correlation_id: str = "m8-manual-analytics-import",
    ) -> AnalyticsSnapshot:
        uploaded = self._require_uploaded_video(data.uploaded_video_id)
        _validate_uploaded_video_match(uploaded, platform=data.platform, platform_video_id=data.platform_video_id)
        if uploaded.monitoring_state != "READY_FOR_ANALYTICS":
            run = self.create_sync_run(
                data=AnalyticsSyncRunCreate(uploaded_video_id=uploaded.id, sync_mode=sync_mode),  # type: ignore[arg-type]
                correlation_id=correlation_id,
            )
            self._update_summary_blocked(uploaded=uploaded, run=run, correlation_id=correlation_id)
            raise ValidationFailureError("uploaded video is not ready for analytics")
        run = self.create_sync_run(
            data=AnalyticsSyncRunCreate(
                uploaded_video_id=uploaded.id,
                sync_mode=sync_mode,  # type: ignore[arg-type]
                observed_from=data.observed_from,
                observed_to=data.observed_to,
                provider_key="manual_import" if sync_mode == "MANUAL_IMPORT" else "csv_import",
                metadata={"source_note": data.source_note, "imported_by_user_id": str(data.imported_by_user_id) if data.imported_by_user_id else None},
            ),
            correlation_id=correlation_id,
        )
        run.sync_state = "RUNNING"
        run.started_at = utc_now()
        self.session.flush()
        output = AnalyticsProviderOutputContract(
            platform=data.platform,
            platform_video_id=data.platform_video_id,
            captured_at=data.captured_at,
            observed_from=data.observed_from,
            observed_to=data.observed_to,
            observation_window=data.observation_window,
            metrics=data.metrics,
            metric_availability={},
            traffic_sources=data.traffic_sources,
            retention_curve=data.retention_curve,
            engagement=data.engagement,
            provider_metadata={
                "provider_key": run.provider_key,
                "source": sync_mode,
                "source_note": data.source_note,
                "imported_by_user_id": str(data.imported_by_user_id) if data.imported_by_user_id else None,
                "duration_seconds": data.duration_seconds,
                "timeline_alignment": data.timeline_alignment,
                "manual_import": True,
            },
            freshness_state=_freshness_from_window(data.captured_at, data.observed_to),
            confidence_level="MEDIUM" if data.metrics else "LOW",
        )
        snapshot_set = self._create_snapshot_set(
            run=run,
            uploaded=uploaded,
            output=output,
            source=sync_mode,
            source_note=data.source_note,
            duration_seconds=data.duration_seconds,
            timeline_alignment=data.timeline_alignment,
            correlation_id=correlation_id,
        )
        _record_m8_event(
            self.session,
            event_type="manual_analytics_import.accepted",
            aggregate_type="analytics_sync_run",
            aggregate_id=run.id,
            actor_id=data.imported_by_user_id,
            target_type="analytics_sync_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code="MANUAL_ANALYTICS_IMPORTED",
            payload={
                "uploaded_video_id": str(uploaded.id),
                "analytics_snapshot_id": str(snapshot_set.analytics_snapshot.id),
                "platform": uploaded.platform,
                "metric_keys": sorted(data.metrics.keys()),
                "no_diagnosis_in_m8": True,
            },
        )
        return snapshot_set.analytics_snapshot

    def get_sync_run(self, sync_run_id: uuid.UUID) -> AnalyticsSyncRun | None:
        return self.session.get(AnalyticsSyncRun, sync_run_id)

    def require_sync_run(self, sync_run_id: uuid.UUID) -> AnalyticsSyncRun:
        run = self.get_sync_run(sync_run_id)
        if run is None:
            raise NotFoundError(f"analytics sync run not found: {sync_run_id}")
        return run

    def get_snapshot(self, snapshot_id: uuid.UUID) -> AnalyticsSnapshot | None:
        return self.session.get(AnalyticsSnapshot, snapshot_id)

    def require_snapshot(self, snapshot_id: uuid.UUID) -> AnalyticsSnapshot:
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"analytics snapshot not found: {snapshot_id}")
        return snapshot

    def list_snapshots_by_uploaded_video(self, uploaded_video_id: uuid.UUID) -> list[AnalyticsSnapshot]:
        self._require_uploaded_video(uploaded_video_id)
        return list(
            self.session.scalars(
                select(AnalyticsSnapshot)
                .where(AnalyticsSnapshot.uploaded_video_id == uploaded_video_id)
                .order_by(AnalyticsSnapshot.captured_at.desc(), AnalyticsSnapshot.created_at.desc())
            ).all()
        )

    def get_metrics_summary(self, uploaded_video_id: uuid.UUID) -> UploadedVideoMetricsSummary:
        uploaded = self._require_uploaded_video(uploaded_video_id)
        existing = self.session.scalars(
            select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == uploaded.id)
        ).one_or_none()
        if existing is not None:
            return existing
        summary = UploadedVideoMetricsSummary(
            uploaded_video_id=uploaded.id,
            company_id=uploaded.company_id,
            channel_workspace_id=uploaded.channel_workspace_id,
            video_project_id=uploaded.video_project_id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            metrics_summary={},
            availability_summary={},
            freshness_state="UNKNOWN",
            confidence_level="UNKNOWN",
            monitoring_state="NO_DATA_YET",
            operator_summary="No analytics data imported yet",
            next_action="Import latest analytics",
        )
        self.session.add(summary)
        self.session.flush()
        return summary

    def latest_retention(self, uploaded_video_id: uuid.UUID) -> RetentionCurveSnapshot | None:
        self._require_uploaded_video(uploaded_video_id)
        return self.session.scalars(
            select(RetentionCurveSnapshot)
            .where(RetentionCurveSnapshot.uploaded_video_id == uploaded_video_id)
            .order_by(RetentionCurveSnapshot.captured_at.desc(), RetentionCurveSnapshot.created_at.desc())
        ).first()

    def latest_traffic_sources(self, uploaded_video_id: uuid.UUID) -> TrafficSourceSnapshot | None:
        self._require_uploaded_video(uploaded_video_id)
        return self.session.scalars(
            select(TrafficSourceSnapshot)
            .where(TrafficSourceSnapshot.uploaded_video_id == uploaded_video_id)
            .order_by(TrafficSourceSnapshot.captured_at.desc(), TrafficSourceSnapshot.created_at.desc())
        ).first()

    def _create_snapshot_set(
        self,
        *,
        run: AnalyticsSyncRun,
        uploaded: UploadedVideo,
        output: AnalyticsProviderOutputContract,
        source: str,
        source_note: str | None,
        duration_seconds: float | None = None,
        timeline_alignment: dict[str, Any] | None = None,
        correlation_id: str,
    ) -> SnapshotSet:
        _validate_uploaded_video_match(uploaded, platform=output.platform, platform_video_id=output.platform_video_id)
        metrics = dict(output.metrics)
        unknown_metric_payload = {key: value for key, value in metrics.items() if key not in KNOWN_ANALYTICS_METRICS}
        metrics = {key: value for key, value in metrics.items() if key in KNOWN_ANALYTICS_METRICS}
        engagement = dict(output.engagement or {})
        engagement = {key: value for key, value in engagement.items() if key in KNOWN_ANALYTICS_METRICS}
        computed_metrics = _compute_engagement_metrics(metrics, engagement)
        metrics_for_availability = {**metrics, **computed_metrics}
        availability_blob, unavailable_metrics, unknown_metrics = _build_metric_availability(
            platform=uploaded.platform,
            metrics=metrics_for_availability,
            explicit_availability=output.metric_availability,
            provider_key=run.provider_key,
            source=source,
        )
        normalized_metrics = _normalize_metrics(
            metrics=metrics,
            provider_key=run.provider_key,
            source=source,
            platform=uploaded.platform,
            captured_at=output.captured_at.isoformat(),
        )
        normalized_metrics.update(
            _normalize_computed_metrics(
                metrics=computed_metrics,
                provider_key=run.provider_key,
                source=source,
                platform=uploaded.platform,
                captured_at=output.captured_at.isoformat(),
            )
        )
        reason_codes = ["ANALYTICS_SNAPSHOT_CREATED", "NO_DIAGNOSIS_IN_M8"]
        if unavailable_metrics:
            reason_codes.append("METRIC_UNAVAILABLE")
        if unknown_metrics:
            reason_codes.append("METRIC_UNKNOWN")
        snapshot = AnalyticsSnapshot(
            analytics_sync_run_id=run.id,
            uploaded_video_id=uploaded.id,
            company_id=uploaded.company_id,
            channel_workspace_id=uploaded.channel_workspace_id,
            video_project_id=uploaded.video_project_id,
            policy_snapshot_id=uploaded.policy_snapshot_id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            captured_at=output.captured_at,
            observed_from=output.observed_from,
            observed_to=output.observed_to,
            observation_window=output.observation_window,
            metrics_blob=metrics,
            normalized_metrics_blob=normalized_metrics,
            metric_availability=availability_blob,
            source_metadata={
                "source": source,
                "provider_key": run.provider_key,
                "provider_metadata": output.provider_metadata,
                "source_note": source_note,
                "unknown_metric_payload": unknown_metric_payload,
                "raw_metrics_are_not_normalized_metrics": True,
                "no_network_analytics_call": True,
                "no_diagnosis_in_m8": True,
            },
            freshness_state=output.freshness_state,
            confidence_level=output.confidence_level,
            reason_codes=reason_codes,
        )
        self.session.add(snapshot)
        self.session.flush()
        availability_snapshot = MetricAvailabilitySnapshot(
            uploaded_video_id=uploaded.id,
            analytics_sync_run_id=run.id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            availability_blob=availability_blob,
            unavailable_metrics=unavailable_metrics,
            unknown_metrics=unknown_metrics,
            source_metric_keys=sorted(metrics.keys()),
            freshness_state=output.freshness_state,
            confidence_level=output.confidence_level,
            captured_at=output.captured_at,
        )
        self.session.add(availability_snapshot)
        traffic_snapshot = self._create_traffic_source_snapshot(
            uploaded=uploaded,
            snapshot=snapshot,
            traffic_sources=output.traffic_sources,
            freshness_state=output.freshness_state,
            confidence_level=output.confidence_level,
        )
        retention_snapshot = self._create_retention_curve_snapshot(
            uploaded=uploaded,
            snapshot=snapshot,
            retention_curve=output.retention_curve,
            duration_seconds=duration_seconds,
            timeline_alignment=timeline_alignment,
            freshness_state=output.freshness_state,
            confidence_level=output.confidence_level,
        )
        engagement_snapshot = self._create_engagement_snapshot(
            uploaded=uploaded,
            snapshot=snapshot,
            metrics=metrics,
            engagement=engagement,
            computed_metrics=computed_metrics,
            freshness_state=output.freshness_state,
            confidence_level=output.confidence_level,
        )
        run.sync_state = "COMPLETED"
        run.completed_at = utc_now()
        run.observed_from = output.observed_from
        run.observed_to = output.observed_to
        run.analytics_snapshot_id = snapshot.id
        run.reason_codes = _dedupe([*run.reason_codes, "ANALYTICS_SYNC_COMPLETED", "ANALYTICS_SNAPSHOT_CREATED"])
        run.next_action = "Import latest analytics"
        summary = self._update_summary_from_snapshot(
            uploaded=uploaded,
            snapshot=snapshot,
            availability_snapshot=availability_snapshot,
            traffic_snapshot=traffic_snapshot,
            retention_snapshot=retention_snapshot,
            engagement_snapshot=engagement_snapshot,
            correlation_id=correlation_id,
        )
        self.session.flush()
        _record_m8_event(
            self.session,
            event_type="analytics_snapshot.created",
            aggregate_type="analytics_snapshot",
            aggregate_id=snapshot.id,
            target_type="analytics_snapshot",
            target_id=snapshot.id,
            company_id=snapshot.company_id,
            correlation_id=correlation_id,
            reason_code="ANALYTICS_SNAPSHOT_CREATED",
            payload={
                "analytics_sync_run_id": str(run.id),
                "uploaded_video_id": str(uploaded.id),
                "video_project_id": str(uploaded.video_project_id),
                "policy_snapshot_id": str(uploaded.policy_snapshot_id),
                "platform": uploaded.platform,
                "metric_keys": sorted(metrics.keys()),
                "freshness_state": snapshot.freshness_state,
                "confidence_level": snapshot.confidence_level,
                "no_diagnosis_in_m8": True,
            },
        )
        _record_m8_event(
            self.session,
            event_type="metric_availability_snapshot.created",
            aggregate_type="metric_availability_snapshot",
            aggregate_id=availability_snapshot.id,
            target_type="metric_availability_snapshot",
            target_id=availability_snapshot.id,
            company_id=uploaded.company_id,
            correlation_id=correlation_id,
            reason_code="METRIC_AVAILABILITY_CREATED",
            payload={
                "analytics_snapshot_id": str(snapshot.id),
                "uploaded_video_id": str(uploaded.id),
                "unknown_metrics": unknown_metrics,
                "unavailable_metrics": unavailable_metrics,
            },
        )
        if traffic_snapshot is not None:
            _record_m8_event(
                self.session,
                event_type="traffic_source_snapshot.created",
                aggregate_type="traffic_source_snapshot",
                aggregate_id=traffic_snapshot.id,
                target_type="traffic_source_snapshot",
                target_id=traffic_snapshot.id,
                company_id=uploaded.company_id,
                correlation_id=correlation_id,
                reason_code="TRAFFIC_SOURCE_SNAPSHOT_CREATED",
                payload={"analytics_snapshot_id": str(snapshot.id), "uploaded_video_id": str(uploaded.id)},
            )
        if retention_snapshot is not None:
            _record_m8_event(
                self.session,
                event_type="retention_curve_snapshot.created",
                aggregate_type="retention_curve_snapshot",
                aggregate_id=retention_snapshot.id,
                target_type="retention_curve_snapshot",
                target_id=retention_snapshot.id,
                company_id=uploaded.company_id,
                correlation_id=correlation_id,
                reason_code="RETENTION_CURVE_SNAPSHOT_CREATED",
                payload={"analytics_snapshot_id": str(snapshot.id), "uploaded_video_id": str(uploaded.id), "no_retention_diagnosis": True},
            )
        if engagement_snapshot is not None:
            _record_m8_event(
                self.session,
                event_type="engagement_snapshot.created",
                aggregate_type="engagement_snapshot",
                aggregate_id=engagement_snapshot.id,
                target_type="engagement_snapshot",
                target_id=engagement_snapshot.id,
                company_id=uploaded.company_id,
                correlation_id=correlation_id,
                reason_code="ENGAGEMENT_SNAPSHOT_CREATED",
                payload={"analytics_snapshot_id": str(snapshot.id), "uploaded_video_id": str(uploaded.id), "no_diagnosis_in_m8": True},
            )
        _record_m8_event(
            self.session,
            event_type="analytics_sync_run.completed",
            aggregate_type="analytics_sync_run",
            aggregate_id=run.id,
            target_type="analytics_sync_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code="ANALYTICS_SYNC_COMPLETED",
            payload={"analytics_snapshot_id": str(snapshot.id), "uploaded_video_id": str(uploaded.id), "sync_state": run.sync_state},
        )
        return SnapshotSet(
            analytics_snapshot=snapshot,
            metric_availability_snapshot=availability_snapshot,
            traffic_source_snapshot=traffic_snapshot,
            retention_curve_snapshot=retention_snapshot,
            engagement_snapshot=engagement_snapshot,
            metrics_summary=summary,
        )

    def _create_traffic_source_snapshot(
        self,
        *,
        uploaded: UploadedVideo,
        snapshot: AnalyticsSnapshot,
        traffic_sources: list[Any] | None,
        freshness_state: str,
        confidence_level: str,
    ) -> TrafficSourceSnapshot:
        if traffic_sources:
            items = [item.model_dump(mode="json") if hasattr(item, "model_dump") else _jsonable(item) for item in traffic_sources]
            total_percentage = sum(float(item.get("percentage") or 0) for item in items)
            source_summary = {
                "state": "AVAILABLE",
                "source_count": len(items),
                "percentage_total": round(total_percentage, 6),
                "reason_code": "TRAFFIC_SOURCE_SNAPSHOT_CREATED",
                "no_diagnosis": True,
            }
            state = freshness_state
            confidence = confidence_level
        else:
            items = []
            source_summary = {
                "state": "UNKNOWN",
                "reason_code": "METRIC_UNKNOWN",
                "operator_summary": "Traffic source data is not available yet.",
                "no_diagnosis": True,
            }
            state = "NOT_AVAILABLE"
            confidence = "LOW"
        record = TrafficSourceSnapshot(
            analytics_snapshot_id=snapshot.id,
            uploaded_video_id=uploaded.id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            captured_at=snapshot.captured_at,
            traffic_sources=items,
            source_summary=source_summary,
            freshness_state=state,
            confidence_level=confidence,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def _create_retention_curve_snapshot(
        self,
        *,
        uploaded: UploadedVideo,
        snapshot: AnalyticsSnapshot,
        retention_curve: list[Any] | None,
        duration_seconds: float | None,
        timeline_alignment: dict[str, Any] | None,
        freshness_state: str,
        confidence_level: str,
    ) -> RetentionCurveSnapshot | None:
        if not retention_curve:
            return None
        duration = duration_seconds if duration_seconds is not None else _render_package_duration(self.session, uploaded.render_package_snapshot_id)
        points = sorted(
            [point.model_dump(mode="json") if hasattr(point, "model_dump") else _jsonable(point) for point in retention_curve],
            key=lambda item: float(item["time_seconds"]),
        )
        if duration is not None:
            out_of_range = [point for point in points if float(point["time_seconds"]) > duration]
            if out_of_range:
                raise ValidationFailureError("retention curve point exceeds known video duration")
        record = RetentionCurveSnapshot(
            analytics_snapshot_id=snapshot.id,
            uploaded_video_id=uploaded.id,
            video_project_id=uploaded.video_project_id,
            render_package_snapshot_id=uploaded.render_package_snapshot_id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            captured_at=snapshot.captured_at,
            curve_points=points,
            curve_summary={
                "state": "AVAILABLE",
                "point_count": len(points),
                "first_time_seconds": points[0]["time_seconds"] if points else None,
                "last_time_seconds": points[-1]["time_seconds"] if points else None,
                "no_retention_diagnosis": True,
            },
            duration_seconds=duration,
            timeline_alignment=_jsonable(timeline_alignment or {}),
            freshness_state=freshness_state,
            confidence_level=confidence_level,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def _create_engagement_snapshot(
        self,
        *,
        uploaded: UploadedVideo,
        snapshot: AnalyticsSnapshot,
        metrics: dict[str, float],
        engagement: dict[str, float],
        computed_metrics: dict[str, float],
        freshness_state: str,
        confidence_level: str,
    ) -> EngagementSnapshot | None:
        engagement_blob: dict[str, Any] = {}
        for key in ("likes", "comments", "shares", "saves", "bookmarks", "subscribers_gained", "subscribers_lost"):
            if key in metrics:
                engagement_blob[key] = {
                    "value": metrics[key],
                    "source_metric": key,
                    "state": "AVAILABLE",
                }
        for key, value in engagement.items():
            engagement_blob[key] = {"value": value, "source_metric": key, "state": "AVAILABLE"}
        if "engagement_rate" not in metrics and "engagement_rate" not in engagement:
            if "engagement_rate" in computed_metrics:
                numerator_keys = [key for key in ENGAGEMENT_NUMERATOR_KEYS if key in metrics]
                rate = computed_metrics["engagement_rate"]
                engagement_blob["engagement_rate"] = {
                    "value": rate,
                    "unit": "RATIO",
                    "computed": True,
                    "source_metric_keys": [*numerator_keys, "views"],
                    "state": "AVAILABLE",
                    "computation_note": "Simple engagement numerator divided by available views.",
                }
            else:
                engagement_blob["engagement_rate"] = {
                    "state": "UNKNOWN",
                    "reason_code": "METRIC_UNKNOWN",
                    "required_denominator": "views",
                    "available_metric_keys": sorted(metrics.keys()),
                }
        if not engagement_blob:
            return None
        record = EngagementSnapshot(
            analytics_snapshot_id=snapshot.id,
            uploaded_video_id=uploaded.id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            captured_at=snapshot.captured_at,
            engagement_blob=engagement_blob,
            freshness_state=freshness_state,
            confidence_level=confidence_level,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def _update_summary_from_snapshot(
        self,
        *,
        uploaded: UploadedVideo,
        snapshot: AnalyticsSnapshot,
        availability_snapshot: MetricAvailabilitySnapshot,
        traffic_snapshot: TrafficSourceSnapshot | None,
        retention_snapshot: RetentionCurveSnapshot | None,
        engagement_snapshot: EngagementSnapshot | None,
        correlation_id: str,
    ) -> UploadedVideoMetricsSummary:
        summary = self.get_metrics_summary(uploaded.id)
        metric_summary = {
            key: {
                "value": item.get("value"),
                "unit": item.get("unit"),
                "source_metric_key": item.get("source_metric_key"),
                "source_metric_keys": item.get("source_metric_keys"),
                "computed": bool(item.get("computed")),
                "analytics_snapshot_id": str(snapshot.id),
                "captured_at": snapshot.captured_at.isoformat(),
            }
            for key, item in snapshot.normalized_metrics_blob.items()
        }
        if not metric_summary:
            monitoring_state = "NO_DATA_YET"
            operator_summary = "No analytics data imported yet"
            next_action = "Import latest analytics"
        elif availability_snapshot.unknown_metrics:
            monitoring_state = "PARTIAL_DATA"
            operator_summary = "Some metrics are not available yet"
            next_action = "Wait for platform data availability"
        elif snapshot.freshness_state == "STALE":
            monitoring_state = "STALE"
            operator_summary = "Analytics data is stale"
            next_action = "Run analytics sync again"
        else:
            monitoring_state = "SYNCED"
            operator_summary = "Analytics synced successfully"
            next_action = "Import latest analytics"
        summary.latest_analytics_snapshot_id = snapshot.id
        summary.latest_retention_curve_snapshot_id = retention_snapshot.id if retention_snapshot else None
        summary.latest_traffic_source_snapshot_id = traffic_snapshot.id if traffic_snapshot else None
        summary.latest_engagement_snapshot_id = engagement_snapshot.id if engagement_snapshot else None
        summary.latest_captured_at = snapshot.captured_at
        summary.metrics_summary = metric_summary
        summary.availability_summary = {
            "availability": availability_snapshot.availability_blob,
            "unknown_metrics": availability_snapshot.unknown_metrics,
            "unavailable_metrics": availability_snapshot.unavailable_metrics,
            "source_metric_keys": availability_snapshot.source_metric_keys,
            "zero_is_available": True,
            "missing_is_not_zero": True,
        }
        summary.freshness_state = snapshot.freshness_state
        summary.confidence_level = snapshot.confidence_level
        summary.monitoring_state = monitoring_state
        summary.operator_summary = operator_summary
        summary.next_action = next_action
        self.session.flush()
        _record_m8_event(
            self.session,
            event_type="uploaded_video_metrics_summary.updated",
            aggregate_type="uploaded_video_metrics_summary",
            aggregate_id=summary.id,
            target_type="uploaded_video_metrics_summary",
            target_id=summary.id,
            company_id=summary.company_id,
            correlation_id=correlation_id,
            reason_code=(
                "NO_ANALYTICS_DATA_YET"
                if not metric_summary
                else "PARTIAL_ANALYTICS_DATA"
                if availability_snapshot.unknown_metrics
                else "ANALYTICS_SYNC_COMPLETED"
            ),
            payload={
                "uploaded_video_id": str(uploaded.id),
                "latest_analytics_snapshot_id": str(snapshot.id),
                "monitoring_state": summary.monitoring_state,
                "operator_summary": summary.operator_summary,
                "no_diagnosis_in_m8": True,
            },
        )
        return summary

    def _update_summary_blocked(
        self,
        *,
        uploaded: UploadedVideo,
        run: AnalyticsSyncRun,
        correlation_id: str,
    ) -> UploadedVideoMetricsSummary:
        summary = self.get_metrics_summary(uploaded.id)
        summary.freshness_state = "UNKNOWN"
        summary.confidence_level = "LOW"
        summary.monitoring_state = "BLOCKED"
        summary.operator_summary = "Analytics provider unavailable"
        summary.next_action = run.next_action or "Run analytics sync again"
        summary.availability_summary = {
            "reason_codes": run.reason_codes,
            "missing_is_not_zero": True,
            "zero_is_available": True,
        }
        self.session.flush()
        _record_m8_event(
            self.session,
            event_type="uploaded_video_metrics_summary.updated",
            aggregate_type="uploaded_video_metrics_summary",
            aggregate_id=summary.id,
            target_type="uploaded_video_metrics_summary",
            target_id=summary.id,
            company_id=summary.company_id,
            correlation_id=correlation_id,
            reason_code="ANALYTICS_SYNC_BLOCKED",
            payload={
                "uploaded_video_id": str(uploaded.id),
                "analytics_sync_run_id": str(run.id),
                "monitoring_state": summary.monitoring_state,
                "next_action": summary.next_action,
            },
        )
        return summary

    def _block_run(
        self,
        run: AnalyticsSyncRun,
        *,
        reason_codes: list[str],
        next_action: str,
        correlation_id: str,
    ) -> None:
        run.sync_state = "BLOCKED"
        run.completed_at = utc_now()
        run.reason_codes = _dedupe([*run.reason_codes, "ANALYTICS_SYNC_BLOCKED", *reason_codes])
        run.next_action = next_action
        self.session.flush()
        _record_m8_event(
            self.session,
            event_type="analytics_sync_run.blocked",
            aggregate_type="analytics_sync_run",
            aggregate_id=run.id,
            target_type="analytics_sync_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code=reason_codes[0],
            payload={"reason_codes": run.reason_codes, "next_action": run.next_action},
        )

    def _finish_unsuccessful_run(
        self,
        run: AnalyticsSyncRun,
        *,
        sync_state: str,
        reason_codes: list[str],
        next_action: str,
        correlation_id: str,
    ) -> None:
        run.sync_state = sync_state
        run.completed_at = utc_now()
        run.reason_codes = _dedupe([*run.reason_codes, *reason_codes])
        run.next_action = next_action
        self.session.flush()
        _record_m8_event(
            self.session,
            event_type="analytics_sync_run.blocked" if sync_state == "BLOCKED" else "analytics_sync_run.failed",
            aggregate_type="analytics_sync_run",
            aggregate_id=run.id,
            target_type="analytics_sync_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code=reason_codes[0],
            payload={"reason_codes": run.reason_codes, "next_action": run.next_action, "sync_state": sync_state},
        )

    def _require_uploaded_video(self, uploaded_video_id: uuid.UUID) -> UploadedVideo:
        uploaded = self.session.get(UploadedVideo, uploaded_video_id)
        if uploaded is None:
            raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
        return uploaded


def _build_metric_availability(
    *,
    platform: str,
    metrics: dict[str, float],
    explicit_availability: dict[str, MetricAvailabilityItem],
    provider_key: str | None,
    source: str,
) -> tuple[dict[str, Any], list[str], list[str]]:
    unavailable_for_platform = PLATFORM_NOT_AVAILABLE_METRICS.get(platform, set())
    availability: dict[str, Any] = {}
    unavailable: list[str] = []
    unknown: list[str] = []
    for metric_key in sorted(KNOWN_ANALYTICS_METRICS):
        explicit = explicit_availability.get(metric_key)
        if metric_key in metrics:
            item = MetricAvailabilityItem(
                state="AVAILABLE",
                source_metric_key=metric_key,
                unit=METRIC_UNITS.get(metric_key, "UNKNOWN"),  # type: ignore[arg-type]
                provider_key=provider_key,
                source=source,
            )
        elif explicit is not None:
            item = explicit
        elif metric_key in unavailable_for_platform:
            item = MetricAvailabilityItem(
                state="NOT_AVAILABLE",
                reason_code="METRIC_UNAVAILABLE",
                unit=METRIC_UNITS.get(metric_key, "UNKNOWN"),  # type: ignore[arg-type]
                provider_key=provider_key,
                source=source,
            )
        else:
            item = MetricAvailabilityItem(
                state="UNKNOWN",
                reason_code="METRIC_UNKNOWN",
                unit=METRIC_UNITS.get(metric_key, "UNKNOWN"),  # type: ignore[arg-type]
                provider_key=provider_key,
                source=source,
            )
        dumped = item.model_dump(mode="json")
        availability[metric_key] = dumped
        if dumped["state"] == "NOT_AVAILABLE":
            unavailable.append(metric_key)
        elif dumped["state"] == "UNKNOWN":
            unknown.append(metric_key)
    return availability, unavailable, unknown


def _normalize_metrics(
    *,
    metrics: dict[str, float],
    provider_key: str | None,
    source: str,
    platform: str,
    captured_at: str,
) -> dict[str, Any]:
    return {
        key: {
            "value": value,
            "unit": METRIC_UNITS.get(key, "UNKNOWN"),
            "source_metric_key": key,
            "source_provider_key": provider_key,
            "source": source,
            "platform": platform,
            "captured_at": captured_at,
            "raw_metric_ref": f"metrics_blob.{key}",
        }
        for key, value in sorted(metrics.items())
    }


def _compute_engagement_metrics(metrics: dict[str, float], engagement: dict[str, float]) -> dict[str, float]:
    if "engagement_rate" in metrics or "engagement_rate" in engagement:
        return {}
    numerator_keys = [key for key in ENGAGEMENT_NUMERATOR_KEYS if key in metrics]
    if "views" not in metrics or metrics["views"] <= 0 or not numerator_keys:
        return {}
    numerator = sum(metrics[key] for key in numerator_keys)
    return {"engagement_rate": numerator / metrics["views"]}


def _normalize_computed_metrics(
    *,
    metrics: dict[str, float],
    provider_key: str | None,
    source: str,
    platform: str,
    captured_at: str,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in sorted(metrics.items()):
        source_metric_keys = [*ENGAGEMENT_NUMERATOR_KEYS, "views"] if key == "engagement_rate" else []
        normalized[key] = {
            "value": value,
            "unit": METRIC_UNITS.get(key, "UNKNOWN"),
            "source_metric_key": "computed_from_available_metrics",
            "source_metric_keys": source_metric_keys,
            "source_provider_key": provider_key,
            "source": source,
            "platform": platform,
            "captured_at": captured_at,
            "computed": True,
            "raw_metric_ref": None,
        }
    return normalized


def _validate_uploaded_video_match(uploaded: UploadedVideo, *, platform: str, platform_video_id: str) -> None:
    if uploaded.platform != platform or uploaded.platform_video_id != platform_video_id:
        raise ValidationFailureError("platform/video id does not match uploaded video")


def _freshness_from_window(captured_at: Any, observed_to: Any | None) -> str:
    reference = observed_to or captured_at
    delta_seconds = abs((utc_now() - reference).total_seconds())
    return "FRESH" if delta_seconds <= 60 * 60 * 48 else "STALE"


def _render_package_duration(session: Session, render_package_snapshot_id: uuid.UUID) -> float | None:
    package = session.get(RenderPackageSnapshot, render_package_snapshot_id)
    if package is None or package.duration_seconds is None:
        return None
    return float(package.duration_seconds)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _record_m8_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    company_id: uuid.UUID | None,
    correlation_id: str,
    reason_code: str,
    payload: dict[str, Any],
    actor_id: uuid.UUID | None = None,
) -> None:
    safe_payload = _jsonable(payload)
    _ensure_no_secret_payload(safe_payload)
    DomainEventBus(session).append(
        EventEnvelope(
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            correlation_id=correlation_id,
            payload=safe_payload,
            metadata={"milestone": "M8"},
        ),
        company_id=company_id,
    )
    AuditService(session).append(
        AuditEnvelope(
            action=event_type,
            actor_type="system" if actor_id is None else "user",
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            correlation_id=correlation_id,
            reason_code=reason_code,
            payload=safe_payload,
        ),
        company_id=company_id,
    )


def _ensure_no_secret_payload(value: Any) -> None:
    for key, item in _walk_items(value):
        normalized = key.lower().replace("-", "_")
        if any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS) and normalized != "secret_ref":
            raise ValidationFailureError(f"secret-like payload key is not allowed: {key}")
        if isinstance(item, str) and any(marker in item for marker in RAW_SECRET_MARKERS):
            raise ValidationFailureError("raw secret-like value is not allowed")


def _walk_items(value: Any):
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            yield str(child_key), child_value
            yield from _walk_items(child_value)
    elif isinstance(value, list):
        for child_value in value:
            yield "", child_value
            yield from _walk_items(child_value)
