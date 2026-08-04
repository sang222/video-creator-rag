"""Phase E scheduler for exact long-form post-upload learning windows.

This service owns scheduling and durable state only.  It never changes channel,
series, cadence, profile, policy, or controlled-memory authority.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.contracts.events import EventEnvelope
from app.contracts.long_form_analytics import (
    LaunchAnalyticsDashboardRead,
)
from app.contracts.vcos_v2 import StrategicLineageV2
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AnalyticsSnapshot,
    DomainEvent,
    LongFormAnalyticsWindow,
    OpsIncident,
    UploadedVideo,
)
from app.db.models.m5 import ProjectAdmissionDecision
from app.db.models.workflow import VideoProject
from app.services.config_registry import content_hash
from app.services.domain_events import DomainEventBus
from app.services.m10_3 import YouTubeOwnerAnalyticsSyncService
from app.services.m9 import PostPublishHealthMonitorService
from app.services.production_package import strategic_lineage_from_record
from app.contracts.m9 import PostPublishHealthRunCreate


ANALYTICS_WINDOW_EVENT_TYPE = "LONG_FORM_ANALYTICS_WINDOW_DUE"
LEARNING_GENERATION_EVENT_TYPE = "LONG_FORM_LEARNING_GENERATION_DUE"
ANALYTICS_WINDOW_AGGREGATE_TYPE = "long_form_analytics_window"
PRIMARY_METRIC_AUTHORITY = "YOUTUBE_OWNER"
WINDOW_DELTAS = {
    "H24": timedelta(hours=24),
    "H72": timedelta(hours=72),
    "D7": timedelta(days=7),
    "D30": timedelta(days=30),
}
# This versioned, hash-bound maturity policy is persisted into every command.
# Early windows are diagnostic only; D7 is provisional and D30 is mature.
LEARNING_WINDOW_MATURITY_POLICY = {
    "version": "vcos.long-form-learning-maturity.v1",
    "provisional_windows": ["D7"],
    "mature_windows": ["D30"],
    "diagnostic_only_windows": ["H24", "H72"],
}
TERMINAL_STATES = {
    "DIAGNOSTICS_COMPLETE",
    "BLOCKED_AUTH",
    "BLOCKED_DATA_UNAVAILABLE",
    "FAILED_TERMINAL",
    "CANCELED",
}


class LongFormAnalyticsScheduler:
    def __init__(
        self,
        session: Session,
        *,
        now: Callable[[], datetime] = utc_now,
        owner_sync: Callable[..., Any] | None = None,
    ) -> None:
        self.session = session
        self.now = now
        self.owner_sync = owner_sync or self._sync_owner

    def schedule_uploaded_video(
        self, uploaded_video_id: uuid.UUID
    ) -> list[LongFormAnalyticsWindow]:
        """Create exactly four idempotent H24/H72/D7/D30 authorities."""

        uploaded = self._require_long_form_uploaded(uploaded_video_id)
        existing = {
            row.window_type: row
            for row in self.session.scalars(
                select(LongFormAnalyticsWindow).where(
                    LongFormAnalyticsWindow.uploaded_video_id == uploaded.id,
                    LongFormAnalyticsWindow.metric_authority
                    == PRIMARY_METRIC_AUTHORITY,
                )
            ).all()
        }
        windows: list[LongFormAnalyticsWindow] = []
        for window_type, delta in WINDOW_DELTAS.items():
            row = existing.get(window_type)
            if row is None:
                scheduled_for = uploaded.published_at + delta
                lineage = self._lineage(uploaded)
                canonical_input_hash = content_hash(
                    {
                        "schema_version": "vcos.long-form-analytics-window.v1",
                        "uploaded_video_id": str(uploaded.id),
                        "metric_authority": PRIMARY_METRIC_AUTHORITY,
                        "window_type": window_type,
                        "scheduled_for": scheduled_for.isoformat(),
                        "lineage": lineage,
                    }
                )
                row = LongFormAnalyticsWindow(
                    uploaded_video_id=uploaded.id,
                    company_id=uploaded.company_id,
                    channel_workspace_id=uploaded.channel_workspace_id,
                    video_project_id=uploaded.video_project_id,
                    policy_snapshot_id=uploaded.policy_snapshot_id,
                    channel_profile_version_id=uploaded.channel_profile_version_id,
                    destination_binding_id=uploaded.destination_binding_id,
                    destination_binding_fingerprint=uploaded.destination_binding_fingerprint,
                    target_market_lineage=dict(uploaded.target_market_lineage or {}),
                    production_lane="LONG_FORM",
                    content_mode=uploaded.content_mode,
                    series_plan_id=uploaded.series_plan_id,
                    series_run_id=uploaded.series_run_id,
                    episode_number=uploaded.episode_number,
                    standalone_reason_code=uploaded.standalone_reason_code,
                    metric_authority=PRIMARY_METRIC_AUTHORITY,
                    window_type=window_type,
                    scheduled_for=scheduled_for,
                    observed_from=uploaded.published_at,
                    minimum_maturity_at=scheduled_for,
                    state="SCHEDULED",
                    next_attempt_at=scheduled_for,
                    canonical_input_hash=canonical_input_hash,
                    reason_codes=["ANALYTICS_READY_SCHEDULED", "LONG_FORM_ONLY"],
                    metadata_={"lineage": lineage, "scheduler_version": "v1"},
                )
                self.session.add(row)
            windows.append(row)
        self.session.flush()
        return sorted(windows, key=lambda row: row.scheduled_for)

    def enqueue_due_windows(self, *, limit: int = 100) -> int:
        """Place due work in the shared durable outbox with stable identities."""

        now = self.now()
        due = self.session.scalars(
            select(LongFormAnalyticsWindow)
            .where(
                LongFormAnalyticsWindow.state.in_(
                    [
                        "SCHEDULED",
                        "WAITING_FOR_MATURITY",
                        "READY_TO_SYNC",
                        "RETRY_SCHEDULED",
                    ]
                ),
                LongFormAnalyticsWindow.scheduled_for <= now,
                or_(
                    LongFormAnalyticsWindow.next_attempt_at.is_(None),
                    LongFormAnalyticsWindow.next_attempt_at <= now,
                ),
            )
            .order_by(LongFormAnalyticsWindow.scheduled_for, LongFormAnalyticsWindow.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        count = 0
        for window in due:
            event_id = _event_id(window.id, window.attempt_count + 1)
            if self.session.get(DomainEvent, event_id) is not None:
                continue
            event = DomainEventBus(self.session).append(
                EventEnvelope(
                    event_id=event_id,
                    event_type=ANALYTICS_WINDOW_EVENT_TYPE,
                    event_version=1,
                    aggregate_type=ANALYTICS_WINDOW_AGGREGATE_TYPE,
                    aggregate_id=window.id,
                    correlation_id=f"analytics-window:{window.id}",
                    payload={"analytics_window_id": str(window.id)},
                    metadata={"queue": "production-workflow", "analytics_window": True},
                ),
                company_id=window.company_id,
            )
            event.command_id = (
                f"analytics-window:{window.id}:{window.attempt_count + 1}"
            )
            event.max_attempts = window.max_attempts
            count += 1
        self.session.flush()
        return count

    def execute_window(self, window_id: uuid.UUID) -> LongFormAnalyticsWindow:
        window = self.session.scalar(
            select(LongFormAnalyticsWindow)
            .where(LongFormAnalyticsWindow.id == window_id)
            .with_for_update()
        )
        if window is None:
            raise NotFoundError(f"long-form analytics window not found: {window_id}")
        if window.state in TERMINAL_STATES:
            return window
        now = self.now()
        if now < window.minimum_maturity_at:
            window.state = "WAITING_FOR_MATURITY"
            window.next_attempt_at = window.minimum_maturity_at
            window.reason_codes = _dedupe(
                [*window.reason_codes, "OBSERVATION_WINDOW_NOT_MATURE"]
            )
            self.session.flush()
            return window
        if window.analytics_snapshot_id is None:
            existing_snapshot = self._snapshot_for_window(window.id)
            if existing_snapshot is not None:
                window.analytics_snapshot_id = existing_snapshot.id
                window.observed_to = (
                    existing_snapshot.observed_to or existing_snapshot.captured_at
                )
                window.state = "DIAGNOSTICS_PENDING"
            else:
                window.state = "SYNCING"
                window.attempt_count += 1
                sync_run = self.owner_sync(window.uploaded_video_id, window.id)
                if sync_run.run_state in {"NEEDS_AUTH", "SKIPPED"}:
                    window.state = "BLOCKED_AUTH"
                    window.next_attempt_at = None
                    reason_code = str(sync_run.error_code or "ANALYTICS_AUTH_REQUIRED")
                    window.reason_codes = _dedupe(
                        [
                            *window.reason_codes,
                            reason_code,
                        ]
                    )
                    self._record_learning_exclusion(
                        window,
                        incident_type="CREDENTIAL_MISSING",
                        reason_code="ANALYTICS_AUTH_FAILURE",
                        next_action=(
                            "Reconnect the YouTube owner analytics credential, "
                            "then request an explicit retry."
                        ),
                        provider_reason_code=reason_code,
                    )
                    self.session.flush()
                    return window
                if sync_run.run_state != "COMPLETED":
                    self._retry_or_fail(
                        window,
                        str(sync_run.error_code or "ANALYTICS_PROVIDER_ERROR"),
                    )
                    self.session.flush()
                    return window
                snapshot = self._snapshot_for_window(window.id)
                if snapshot is None:
                    self._retry_or_fail(window, "ANALYTICS_SNAPSHOT_MISSING_AFTER_SYNC")
                    self.session.flush()
                    return window
                window.analytics_snapshot_id = snapshot.id
                window.observed_to = snapshot.observed_to or snapshot.captured_at
                window.state = "DIAGNOSTICS_PENDING"
        if window.post_publish_health_run_id is None:
            health = PostPublishHealthMonitorService(self.session).create_health_run(
                data=PostPublishHealthRunCreate(
                    uploaded_video_id=window.uploaded_video_id,
                    observation_window=window.window_type,  # type: ignore[arg-type]
                    analytics_snapshot_id=window.analytics_snapshot_id,
                )
            )
            window.post_publish_health_run_id = health.id
        health = PostPublishHealthMonitorService(self.session).execute_health_run(
            run_id=window.post_publish_health_run_id
        )
        window.state = (
            "DIAGNOSTICS_COMPLETE"
            if health.run_state in {"COMPLETED", "INSUFFICIENT_DATA"}
            else "BLOCKED_DATA_UNAVAILABLE"
        )
        if window.state == "BLOCKED_DATA_UNAVAILABLE":
            self._record_learning_exclusion(
                window,
                incident_type="PROVIDER_OUTAGE",
                reason_code="ANALYTICS_DATA_UNAVAILABLE",
                next_action=(
                    "Verify provider data availability and retry only after "
                    "the missing evidence is available."
                ),
            )
        window.next_attempt_at = None
        window.result_hash = content_hash(
            {
                "analytics_snapshot_id": str(window.analytics_snapshot_id),
                "post_publish_health_run_id": str(window.post_publish_health_run_id),
                "state": window.state,
            }
        )
        window.reason_codes = _dedupe(
            [*window.reason_codes, "ANALYTICS_WINDOW_COMPLETE"]
        )
        self.enqueue_learning_generation_for_window(window)
        self.session.flush()
        return window

    def enqueue_learning_generation_for_window(
        self, window: LongFormAnalyticsWindow
    ) -> bool:
        """Enqueue one durable learning command after diagnostics complete.

        It is intentionally an outbox command, not an untracked M10 call.  The
        command hash binds the exact window/snapshot/health result and policy,
        so rescans and worker restarts cannot produce a second learning run.
        """

        if window.state != "DIAGNOSTICS_COMPLETE":
            return False
        policy = dict(LEARNING_WINDOW_MATURITY_POLICY)
        policy_hash = content_hash(policy)
        if window.window_type in set(policy["diagnostic_only_windows"]):
            window.reason_codes = _dedupe(
                [*window.reason_codes, "LEARNING_DIAGNOSTIC_WINDOW_ONLY"]
            )
            return False
        if window.window_type not in {
            *policy["provisional_windows"],
            *policy["mature_windows"],
        }:
            window.reason_codes = _dedupe(
                [*window.reason_codes, "LEARNING_WINDOW_POLICY_BLOCKED"]
            )
            return False
        command_key = content_hash(
            {
                "uploaded_video_id": str(window.uploaded_video_id),
                "analytics_window_id": str(window.id),
                "analytics_snapshot_id": str(window.analytics_snapshot_id),
                "health_run_id": str(window.post_publish_health_run_id),
                "diagnostic_result_hash": window.result_hash,
                "learning_policy_version": policy["version"],
                "learning_policy_hash": policy_hash,
            }
        )
        event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"vcos/learning-generation/{command_key}")
        if self.session.get(DomainEvent, event_id) is not None:
            return False
        event = DomainEventBus(self.session).append(
            EventEnvelope(
                event_id=event_id,
                event_type=LEARNING_GENERATION_EVENT_TYPE,
                event_version=1,
                aggregate_type="long_form_analytics_window",
                aggregate_id=window.id,
                correlation_id=f"learning-window:{window.id}",
                payload={
                    "analytics_window_id": str(window.id),
                    "uploaded_video_id": str(window.uploaded_video_id),
                    "learning_command_key": command_key,
                    "learning_policy": policy,
                    "learning_policy_hash": policy_hash,
                },
                metadata={"queue": "production-workflow", "learning_generation": True},
            ),
            company_id=window.company_id,
        )
        event.command_id = f"learning-generation:{command_key}"
        event.max_attempts = window.max_attempts
        window.reason_codes = _dedupe(
            [*window.reason_codes, "LEARNING_GENERATION_ENQUEUED"]
        )
        return True

    def list_windows(
        self, uploaded_video_id: uuid.UUID
    ) -> list[LongFormAnalyticsWindow]:
        return list(
            self.session.scalars(
                select(LongFormAnalyticsWindow)
                .where(LongFormAnalyticsWindow.uploaded_video_id == uploaded_video_id)
                .order_by(LongFormAnalyticsWindow.scheduled_for)
            ).all()
        )

    def request_retry(
        self, *, window_id: uuid.UUID, reason: str
    ) -> LongFormAnalyticsWindow:
        window = self.session.scalar(
            select(LongFormAnalyticsWindow)
            .where(LongFormAnalyticsWindow.id == window_id)
            .with_for_update()
        )
        if window is None:
            raise NotFoundError(f"long-form analytics window not found: {window_id}")
        if window.state not in {"RETRY_SCHEDULED", "BLOCKED_DATA_UNAVAILABLE"}:
            raise ValidationFailureError("ANALYTICS_WINDOW_NOT_RETRYABLE")
        window.state = "RETRY_SCHEDULED"
        window.next_attempt_at = self.now()
        window.reason_codes = _dedupe(
            [*window.reason_codes, "OPERATOR_RETRY_REQUESTED", reason]
        )
        self.session.flush()
        return window

    def dashboard(
        self, channel_workspace_id: uuid.UUID
    ) -> LaunchAnalyticsDashboardRead:
        windows = list(
            self.session.scalars(
                select(LongFormAnalyticsWindow)
                .where(
                    LongFormAnalyticsWindow.channel_workspace_id == channel_workspace_id
                )
                .order_by(
                    LongFormAnalyticsWindow.scheduled_for.desc(),
                    LongFormAnalyticsWindow.id.desc(),
                )
            ).all()
        )
        uploaded_ids = {row.uploaded_video_id for row in windows}
        by_state: dict[str, int] = {}
        by_type: dict[str, str] = {}
        metrics: dict[str, object] = {}
        for row in windows:
            by_state[row.state] = by_state.get(row.state, 0) + 1
            by_type[row.window_type] = row.state
            if row.analytics_snapshot_id is None:
                continue
            snapshot = self.session.get(AnalyticsSnapshot, row.analytics_snapshot_id)
            if snapshot is None:
                continue
            for metric_key, detail in (snapshot.metric_availability or {}).items():
                if metric_key in metrics:
                    continue
                normalized = (snapshot.normalized_metrics_blob or {}).get(
                    metric_key, {}
                )
                metrics[metric_key] = {
                    "value": normalized.get("value"),
                    "availability": detail,
                    "window_type": row.window_type,
                    "captured_at": snapshot.captured_at.isoformat(),
                    "analytics_snapshot_id": str(snapshot.id),
                }
        next_milestone = min(
            (row.scheduled_for for row in windows if row.state not in TERMINAL_STATES),
            default=None,
        )
        unavailable = sorted(
            {
                code
                for row in windows
                if row.state in {"BLOCKED_AUTH", "BLOCKED_DATA_UNAVAILABLE"}
                for code in row.reason_codes
            }
        )
        incident_count = 0
        if uploaded_ids:
            incident_count = int(
                self.session.scalar(
                    select(func.count(OpsIncident.id)).where(
                        OpsIncident.uploaded_video_id.in_(uploaded_ids),
                        OpsIncident.state.in_(["OPEN", "ACKNOWLEDGED"]),
                    )
                )
                or 0
            )
        return LaunchAnalyticsDashboardRead(
            channel_workspace_id=channel_workspace_id,
            launch_day=None,
            published_videos=len(uploaded_ids),
            active_series_count=len(
                {row.series_run_id for row in windows if row.series_run_id}
            ),
            next_evidence_milestone=next_milestone,
            windows_by_state=by_state,
            windows_by_type=by_type,
            analytics_freshness=(
                "UNAVAILABLE"
                if unavailable
                else "FRESH"
                if metrics
                else "NOT_YET_SYNCED"
            ),
            incidents_or_exclusions=incident_count,
            metrics=metrics,
            unavailable_metrics=unavailable,
            advanced_details={"window_ids": [str(row.id) for row in windows]},
        )

    def _sync_owner(self, uploaded_video_id: uuid.UUID, window_id: uuid.UUID) -> Any:
        return YouTubeOwnerAnalyticsSyncService(self.session).sync_uploaded_video(
            uploaded_video_id=uploaded_video_id,
            long_form_analytics_window_id=window_id,
        )

    def _snapshot_for_window(self, window_id: uuid.UUID) -> AnalyticsSnapshot | None:
        return self.session.scalars(
            select(AnalyticsSnapshot)
            .where(
                AnalyticsSnapshot.long_form_analytics_window_id == window_id,
            )
            .order_by(AnalyticsSnapshot.created_at.desc())
            .limit(1)
        ).one_or_none()

    def _record_learning_exclusion(
        self,
        window: LongFormAnalyticsWindow,
        *,
        incident_type: str,
        reason_code: str,
        next_action: str,
        provider_reason_code: str | None = None,
    ) -> None:
        existing = self.session.scalars(
            select(OpsIncident)
            .where(
                OpsIncident.uploaded_video_id == window.uploaded_video_id,
                OpsIncident.state.in_(["OPEN", "ACKNOWLEDGED"]),
            )
            .order_by(OpsIncident.created_at.desc())
        ).all()
        if any(reason_code in (item.reason_codes or []) for item in existing):
            return
        self.session.add(
            OpsIncident(
                incident_type=incident_type,
                severity="ERROR",
                state="OPEN",
                project_id=window.video_project_id,
                uploaded_video_id=window.uploaded_video_id,
                retry_eligible=window.state == "BLOCKED_DATA_UNAVAILABLE",
                learning_excluded=True,
                operator_visible_blocker=(
                    "Analytics evidence is excluded from strategic learning "
                    "until this incident is resolved."
                ),
                reason_codes=_dedupe(
                    [
                        reason_code,
                        *([provider_reason_code] if provider_reason_code else []),
                    ]
                ),
                next_action=next_action,
                resolution_evidence={},
                impacted_refs=[
                    {
                        "long_form_analytics_window_id": str(window.id),
                        "window_type": window.window_type,
                        "metric_authority": window.metric_authority,
                    }
                ],
                metadata_={
                    "phase": "E",
                    "analytics_window_id": str(window.id),
                    "learning_excluded": True,
                },
            )
        )

    def _retry_or_fail(self, window: LongFormAnalyticsWindow, code: str) -> None:
        window.reason_codes = _dedupe([*window.reason_codes, code])
        if window.attempt_count >= window.max_attempts:
            window.state = "FAILED_TERMINAL"
            window.next_attempt_at = None
            return
        window.state = "RETRY_SCHEDULED"
        window.next_attempt_at = self.now() + timedelta(
            seconds=min(900, 5 * (2 ** max(0, window.attempt_count - 1)))
        )

    def _require_long_form_uploaded(
        self, uploaded_video_id: uuid.UUID
    ) -> UploadedVideo:
        uploaded = self.session.get(UploadedVideo, uploaded_video_id)
        if uploaded is None:
            raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
        required = [
            uploaded.video_project_id,
            uploaded.policy_snapshot_id,
            uploaded.channel_profile_version_id,
            uploaded.destination_binding_id,
            uploaded.destination_binding_fingerprint,
            uploaded.published_at,
        ]
        if (
            uploaded.schema_version != "v2"
            or uploaded.production_lane != "LONG_FORM"
            or uploaded.content_mode not in {"SERIES_EPISODE", "STANDALONE"}
            or any(value is None for value in required)
        ):
            raise ValidationFailureError(
                "LONG_FORM_ANALYTICS_UPLOADED_VIDEO_AUTHORITY_INVALID"
            )
        uploaded_lineage = self._uploaded_strategic_lineage(uploaded)
        project = self.session.get(VideoProject, uploaded.video_project_id)
        admission = (
            self.session.get(
                ProjectAdmissionDecision,
                project.project_admission_decision_id,
            )
            if project is not None and project.project_admission_decision_id is not None
            else None
        )
        if project is None or admission is None:
            raise ValidationFailureError(
                "LONG_FORM_ANALYTICS_STRATEGIC_AUTHORITY_MISSING"
            )
        project_lineage = strategic_lineage_from_record(
            project,
            invalid_reason_code="VIDEO_PROJECT_STRATEGIC_LINEAGE_INVALID",
        )
        admission_lineage = strategic_lineage_from_record(
            admission,
            invalid_reason_code="PROJECT_ADMISSION_STRATEGIC_LINEAGE_INVALID",
        )
        if (
            project_lineage is None
            or admission_lineage is None
            or project_lineage != admission_lineage
            or uploaded_lineage != project_lineage
        ):
            raise ValidationFailureError(
                "LONG_FORM_ANALYTICS_STRATEGIC_LINEAGE_MISMATCH"
            )
        return uploaded

    @staticmethod
    def _lineage(uploaded: UploadedVideo) -> dict[str, Any]:
        return {
            "video_project_id": str(uploaded.video_project_id),
            "channel_profile_version_id": str(uploaded.channel_profile_version_id),
            "compiled_policy_snapshot_id": str(uploaded.policy_snapshot_id),
            "destination_binding_id": str(uploaded.destination_binding_id),
            "destination_binding_fingerprint": uploaded.destination_binding_fingerprint,
            "target_market_lineage": dict(uploaded.target_market_lineage or {}),
            "strategic_lineage": LongFormAnalyticsScheduler._uploaded_strategic_lineage(
                uploaded
            ).model_dump(mode="json"),
        }

    @staticmethod
    def _uploaded_strategic_lineage(uploaded: UploadedVideo) -> StrategicLineageV2:
        refs = uploaded.lineage_refs if isinstance(uploaded.lineage_refs, dict) else {}
        raw = refs.get("strategic_lineage")
        if not isinstance(raw, dict):
            raise ValidationFailureError(
                "LONG_FORM_ANALYTICS_STRATEGIC_LINEAGE_REQUIRED"
            )
        try:
            return StrategicLineageV2.model_validate(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "LONG_FORM_ANALYTICS_STRATEGIC_LINEAGE_INVALID"
            ) from exc


def _event_id(window_id: uuid.UUID, attempt: int) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL, f"vcos/analytics-window/{window_id}/{attempt}"
    )


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
