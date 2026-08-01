"""Durable long-form-only analytics scheduling contracts."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


LongFormAnalyticsWindowType = Literal["H24", "H72", "D7", "D30"]
LongFormAnalyticsWindowState = Literal[
    "SCHEDULED",
    "WAITING_FOR_MATURITY",
    "READY_TO_SYNC",
    "SYNCING",
    "SYNCED",
    "DIAGNOSTICS_PENDING",
    "DIAGNOSTICS_COMPLETE",
    "RETRY_SCHEDULED",
    "BLOCKED_AUTH",
    "BLOCKED_DATA_UNAVAILABLE",
    "FAILED_TERMINAL",
    "CANCELED",
]
MetricAuthority = Literal["YOUTUBE_OWNER", "YOUTUBE_PUBLIC", "MANUAL_VERIFIED"]


class LongFormAnalyticsWindowRead(BaseModel):
    id: uuid.UUID
    uploaded_video_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    production_lane: Literal["LONG_FORM"]
    content_mode: Literal["SERIES_EPISODE", "STANDALONE"]
    series_plan_id: uuid.UUID | None
    series_run_id: uuid.UUID | None
    episode_number: int | None
    standalone_reason_code: str | None
    metric_authority: MetricAuthority
    window_type: LongFormAnalyticsWindowType
    scheduled_for: AwareDatetime
    observed_from: AwareDatetime
    observed_to: AwareDatetime | None
    minimum_maturity_at: AwareDatetime
    state: LongFormAnalyticsWindowState
    attempt_count: int
    next_attempt_at: AwareDatetime | None
    analytics_snapshot_id: uuid.UUID | None
    post_publish_health_run_id: uuid.UUID | None
    canonical_input_hash: str
    reason_codes: list[str]
    strategic_lineage: dict[str, Any] | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class AnalyticsWindowRetryRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

    model_config = ConfigDict(extra="forbid")


class LaunchAnalyticsDashboardRead(BaseModel):
    channel_workspace_id: uuid.UUID
    launch_day: int | None
    published_videos: int
    active_series_count: int
    next_evidence_milestone: AwareDatetime | None
    windows_by_state: dict[str, int]
    windows_by_type: dict[str, str]
    analytics_freshness: str
    incidents_or_exclusions: int
    metrics: dict[str, object]
    unavailable_metrics: list[str]
    advanced_details: dict[str, object]

    model_config = ConfigDict(extra="forbid")
