"""Phase E durable scheduler authority for post-upload long-form analytics."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class LongFormAnalyticsWindow(Base):
    __tablename__ = "long_form_analytics_windows"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compiled_channel_policy_snapshots.id"),
        nullable=False,
    )
    channel_profile_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id"), nullable=False
    )
    destination_binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    destination_binding_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    target_market_lineage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    production_lane: Mapped[str] = mapped_column(
        String(40), nullable=False, default="LONG_FORM"
    )
    content_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    series_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id")
    )
    series_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_runs.id")
    )
    episode_number: Mapped[int | None] = mapped_column(Integer)
    standalone_reason_code: Mapped[str | None] = mapped_column(String(160))
    metric_authority: Mapped[str] = mapped_column(
        String(40), nullable=False, default="YOUTUBE_OWNER"
    )
    window_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    minimum_maturity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="SCHEDULED")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analytics_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analytics_snapshots.id")
    )
    post_publish_health_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("post_publish_health_runs.id")
    )
    canonical_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    @property
    def strategic_lineage(self) -> dict[str, Any] | None:
        """Read-only projection of the upload-bound strategic authority."""

        lineage = (self.metadata_ or {}).get("lineage")
        value = lineage.get("strategic_lineage") if isinstance(lineage, dict) else None
        return dict(value) if isinstance(value, dict) else None

    __table_args__ = (
        UniqueConstraint(
            "uploaded_video_id",
            "metric_authority",
            "window_type",
            name="uq_long_form_analytics_window_authority",
        ),
        UniqueConstraint(
            "canonical_input_hash", name="uq_long_form_analytics_window_input_hash"
        ),
        Index(
            "ix_long_form_analytics_windows_due",
            "state",
            "scheduled_for",
            "next_attempt_at",
        ),
        Index("ix_long_form_analytics_windows_uploaded", "uploaded_video_id"),
        Index("ix_long_form_analytics_windows_channel", "channel_workspace_id"),
    )
