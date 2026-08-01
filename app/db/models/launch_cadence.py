"""Persistent launch policy, launch run, and long-form cadence authorities."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class FirstChannelLaunchPolicyVersion(Base):
    __tablename__ = "first_channel_launch_policy_versions"

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
    approved_initial_series_plan_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False
    )
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("first_channel_launch_policy_versions.id")
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    launch_mode: Mapped[str] = mapped_column(
        String(64), nullable=False, default="CONTROLLED_EVIDENCE_BUILDING"
    )
    duration_source: Mapped[str] = mapped_column(
        String(48), nullable=False, default="CHANNEL_DURATION_CONTRACT"
    )

    preparation_days_min: Mapped[int] = mapped_column(Integer, nullable=False)
    preparation_days_max: Mapped[int] = mapped_column(Integer, nullable=False)
    idea_candidates_target: Mapped[int] = mapped_column(Integer, nullable=False)
    preflight_pass_target: Mapped[int] = mapped_column(Integer, nullable=False)
    greenlight_target: Mapped[int] = mapped_column(Integer, nullable=False)
    public_ready_buffer_target: Mapped[int] = mapped_column(Integer, nullable=False)
    max_days_produced_ahead: Mapped[int] = mapped_column(Integer, nullable=False)
    max_concurrent_productions: Mapped[int] = mapped_column(Integer, nullable=False)
    max_active_runs: Mapped[int] = mapped_column(Integer, nullable=False)
    initial_series_count: Mapped[int] = mapped_column(Integer, nullable=False)

    first_n_public_videos: Mapped[int] = mapped_column(Integer, nullable=False)
    max_primary_variables_changed_per_video: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    auto_niche_pivot: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    auto_series_kill: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    auto_playbook_promotion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    channel_promise_and_initial_series: Mapped[str] = mapped_column(
        String(40), nullable=False
    )
    pre_render_script_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    pre_render_package_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    final_video_decision: Mapped[str] = mapped_column(String(40), nullable=False)
    public_publish: Mapped[str] = mapped_column(String(32), nullable=False)

    commercial_model: Mapped[str] = mapped_column(String(48), nullable=False)
    affiliate_cta: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sponsor_content: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    primary_cta: Mapped[str] = mapped_column(String(40), nullable=False)

    target_long_form_per_week: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_fallback_long_form_per_week: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    minimum_publish_interval_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    publish_weekdays: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    publish_local_time: Mapped[str] = mapped_column(String(5), nullable=False)
    render_lead_time_min_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    render_lead_time_max_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    same_day_multi_publish: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)

    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
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
            "policy_version",
            name="uq_launch_policy_channel_version",
        ),
        UniqueConstraint("canonical_hash", name="uq_launch_policy_canonical_hash"),
        Index("ix_launch_policy_company", "company_id"),
        Index("ix_launch_policy_channel", "channel_workspace_id"),
        Index(
            "uq_launch_policy_one_approved_channel",
            "channel_workspace_id",
            unique=True,
            postgresql_where=text("state = 'APPROVED'"),
        ),
        CheckConstraint(
            "state in ('DRAFT','APPROVED','SUPERSEDED','ARCHIVED')",
            name="ck_launch_policy_state",
        ),
        CheckConstraint(
            "launch_mode = 'CONTROLLED_EVIDENCE_BUILDING'",
            name="ck_launch_policy_mode",
        ),
        CheckConstraint(
            "duration_source = 'CHANNEL_DURATION_CONTRACT'",
            name="ck_launch_policy_duration_source",
        ),
        CheckConstraint(
            "preparation_days_min > 0 and "
            "preparation_days_max >= preparation_days_min and "
            "idea_candidates_target >= preflight_pass_target and "
            "preflight_pass_target >= greenlight_target and "
            "greenlight_target >= public_ready_buffer_target and "
            "public_ready_buffer_target > 0",
            name="ck_launch_policy_runway_targets",
        ),
        CheckConstraint(
            "max_active_runs between 1 and 2 and "
            "initial_series_count between 0 and 2 and "
            "jsonb_array_length(approved_initial_series_plan_ids) = "
            "initial_series_count",
            name="ck_launch_policy_series_limits",
        ),
        CheckConstraint(
            "max_primary_variables_changed_per_video = 1 and "
            "not auto_niche_pivot and not auto_series_kill and "
            "not auto_playbook_promotion and not pre_render_script_review and "
            "not pre_render_package_review and not affiliate_cta and "
            "not sponsor_content and not same_day_multi_publish",
            name="ck_launch_policy_human_safety",
        ),
        CheckConstraint(
            "target_long_form_per_week between 1 and 2 and "
            "quality_fallback_long_form_per_week between 1 and "
            "target_long_form_per_week and minimum_publish_interval_hours > 0 "
            "and render_lead_time_min_hours > 0 and "
            "render_lead_time_max_hours >= render_lead_time_min_hours and "
            "jsonb_typeof(publish_weekdays) = 'array' and "
            "jsonb_array_length(publish_weekdays) between 1 and "
            "target_long_form_per_week",
            name="ck_launch_policy_cadence",
        ),
        CheckConstraint(
            "(state = 'APPROVED' and approved_by_user_id is not null and "
            "approved_at is not null) or state <> 'APPROVED'",
            name="ck_launch_policy_approval",
        ),
    )


class LaunchRun(Base):
    __tablename__ = "launch_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    launch_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("first_channel_launch_policy_versions.id"),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    launch_key: Mapped[str] = mapped_column(String(160), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="PREPARING")
    preparation_started_on: Mapped[date] = mapped_column(Date, nullable=False)
    launch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    updated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id", "launch_key", name="uq_launch_runs_channel_key"
        ),
        Index("ix_launch_runs_policy", "launch_policy_version_id"),
        Index("ix_launch_runs_channel", "channel_workspace_id"),
        Index(
            "uq_launch_runs_one_open_channel",
            "channel_workspace_id",
            unique=True,
            postgresql_where=text(
                "state in ('PREPARING','READY_TO_LAUNCH','ACTIVE','PAUSED')"
            ),
        ),
        CheckConstraint(
            "state in ('PREPARING','READY_TO_LAUNCH','ACTIVE','PAUSED',"
            "'COMPLETED','CANCELED')",
            name="ck_launch_runs_state",
        ),
    )


class LongFormPublishSlot(Base):
    __tablename__ = "long_form_publish_slots"

    id: Mapped[uuid.UUID] = uuid_pk()
    launch_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("launch_runs.id"), nullable=False
    )
    launch_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("first_channel_launch_policy_versions.id"),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    local_publish_date: Mapped[date] = mapped_column(Date, nullable=False)
    intended_publish_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    target_start_window_open_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    target_start_window_close_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    reserved_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id")
    )
    admitted_video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "intended_publish_at",
            name="uq_long_form_publish_slots_channel_time",
        ),
        UniqueConstraint(
            "launch_run_id",
            "local_publish_date",
            name="uq_long_form_publish_slots_run_date",
        ),
        Index("ix_long_form_publish_slots_run", "launch_run_id"),
        Index("ix_long_form_publish_slots_intended", "intended_publish_at"),
        CheckConstraint(
            "state in ('OPEN','RESERVED','FULFILLED','SKIPPED','CANCELED')",
            name="ck_long_form_publish_slots_state",
        ),
        CheckConstraint(
            "target_start_window_open_at <= target_start_window_close_at and "
            "target_start_window_close_at < intended_publish_at",
            name="ck_long_form_publish_slots_window",
        ),
    )


class CadenceEvaluationReceipt(Base):
    __tablename__ = "cadence_evaluation_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    launch_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("launch_runs.id"), nullable=False
    )
    launch_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("first_channel_launch_policy_versions.id"),
        nullable=False,
    )
    publish_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("long_form_publish_slots.id")
    )
    selected_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id")
    )
    admitted_video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    production_workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_workflow_runs.id")
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evaluation_window_key: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    public_ready_buffer_count: Mapped[int] = mapped_column(Integer, nullable=False)
    active_production_count: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_greenlit_candidate_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False
    )
    budget_provider_readiness: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    blocking_incident_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "launch_run_id",
            "evaluation_window_key",
            name="uq_cadence_receipts_run_window",
        ),
        UniqueConstraint("input_hash", name="uq_cadence_receipts_input_hash"),
        Index("ix_cadence_receipts_run", "launch_run_id"),
        Index("ix_cadence_receipts_evaluated", "evaluated_at"),
        CheckConstraint(
            "public_ready_buffer_count >= 0 and active_production_count >= 0",
            name="ck_cadence_receipts_counts",
        ),
        CheckConstraint(
            "decision in ("
            "'START_LONG_FORM_PRODUCTION','WAIT_BUFFER_FULL',"
            "'WAIT_NO_ELIGIBLE_CANDIDATE','WAIT_ACTIVE_PRODUCTION',"
            "'WAIT_OUTSIDE_PRODUCTION_HORIZON','WAIT_BUDGET_BLOCKED',"
            "'WAIT_POLICY_OR_RIGHTS_BLOCKED','WAIT_QUALITY_BLOCKED',"
            "'WAIT_LAUNCH_NOT_ACTIVE')",
            name="ck_cadence_receipts_decision",
        ),
    )
