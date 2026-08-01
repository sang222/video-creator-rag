"""Persistent Phase 2 authorities for typed series planning and execution."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class SeriesPlan(Base):
    __tablename__ = "series_plans"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    channel_profile_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compiled_channel_policy_snapshots.id"),
        nullable=False,
    )
    stable_series_key: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    editorial_promise: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_production_lanes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    episode_role_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_series_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id")
    )
    approval_evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    state_reason_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "stable_series_key",
            "version",
            name="uq_series_plans_workspace_key_version",
        ),
        Index("ix_series_plans_company_id", "company_id"),
        Index("ix_series_plans_channel_workspace_id", "channel_workspace_id"),
        Index(
            "ix_series_plans_channel_profile_version_id",
            "channel_profile_version_id",
        ),
        Index("ix_series_plans_policy_snapshot_id", "policy_snapshot_id"),
        Index("ix_series_plans_stable_series_key", "stable_series_key"),
        Index("ix_series_plans_state", "state"),
        Index("ix_series_plans_created_at", "created_at"),
        Index(
            "uq_series_plans_one_approved_key",
            "channel_workspace_id",
            "stable_series_key",
            unique=True,
            postgresql_where=text("state = 'APPROVED'"),
        ),
        CheckConstraint(
            "state in ('DRAFT','APPROVED','SUPERSEDED','ARCHIVED')",
            name="ck_series_plans_state",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_series_plans_version_positive",
        ),
        CheckConstraint(
            "jsonb_typeof(allowed_production_lanes) = 'array' "
            "and allowed_production_lanes = '[\"LONG_FORM\"]'::jsonb",
            name="ck_series_plans_allowed_lanes",
        ),
        CheckConstraint(
            "(state = 'APPROVED' and approved_by_user_id is not null "
            "and approved_at is not null "
            "and jsonb_array_length(approval_evidence_refs) > 0) "
            "or state <> 'APPROVED'",
            name="ck_series_plans_approval_evidence",
        ),
    )


class SeriesRun(Base):
    __tablename__ = "series_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    series_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    channel_profile_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compiled_channel_policy_snapshots.id"),
        nullable=False,
    )
    run_key: Mapped[str] = mapped_column(String(160), nullable=False)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    first_episode_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    next_episode_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reserved_episode_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    published_episode_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schedule_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    schedule_window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="PROPOSED")
    state_reason_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_pending_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "series_plan_id",
            "run_number",
            name="uq_series_runs_plan_run_number",
        ),
        UniqueConstraint(
            "channel_workspace_id",
            "run_key",
            name="uq_series_runs_workspace_run_key",
        ),
        Index("ix_series_runs_series_plan_id", "series_plan_id"),
        Index("ix_series_runs_company_id", "company_id"),
        Index("ix_series_runs_channel_workspace_id", "channel_workspace_id"),
        Index("ix_series_runs_state", "state"),
        Index("ix_series_runs_created_at", "created_at"),
        CheckConstraint(
            "state in ('PROPOSED','APPROVED','SCHEDULED','ACTIVE','PAUSED',"
            "'COMPLETION_PENDING','COMPLETED','CANCELED','ARCHIVED')",
            name="ck_series_runs_state",
        ),
        CheckConstraint(
            "run_number > 0 and capacity > 0 and first_episode_number > 0 "
            "and next_episode_number >= first_episode_number "
            "and reserved_episode_count >= 0 "
            "and reserved_episode_count <= capacity "
            "and published_episode_count >= 0 "
            "and published_episode_count <= reserved_episode_count",
            name="ck_series_runs_progress",
        ),
        CheckConstraint(
            "schedule_window_end is null or schedule_window_start is null "
            "or schedule_window_end > schedule_window_start",
            name="ck_series_runs_schedule_window",
        ),
    )
