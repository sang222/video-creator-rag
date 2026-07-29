import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class EditorialCalendarSlot(Base):
    __tablename__ = "editorial_calendar_slots"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("content_categories.id"))
    slot_date: Mapped[date] = mapped_column(Date, nullable=False)
    slot_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="OPEN")
    schema_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    production_lane: Mapped[str | None] = mapped_column(String(40))
    assignment_mode: Mapped[str | None] = mapped_column(String(40))
    preferred_series_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id")
    )
    preferred_series_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_runs.id")
    )
    production_goal: Mapped[str | None] = mapped_column(Text)
    target_platforms: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    content_pillar: Mapped[str | None] = mapped_column(Text)
    series_key: Mapped[str | None] = mapped_column(Text)
    format_hint: Mapped[str | None] = mapped_column(Text)
    character_binding_policy_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    risk_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    operational_envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        CheckConstraint(
            "schema_version in ('v1','v2')",
            name="ck_editorial_calendar_slots_schema_version",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(schema_version = 'v2' "
            "and production_lane in "
            "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
            "and assignment_mode in "
            "('SERIES_REQUIRED','SERIES_PREFERRED',"
            "'STANDALONE_REQUIRED','OPEN_MIX') "
            "and series_key is null "
            "and (preferred_series_run_id is null "
            "or preferred_series_plan_id is not null))",
            name="ck_editorial_calendar_slots_v2_authority",
        ),
        Index("ix_editorial_calendar_slots_company_id", "company_id"),
        Index("ix_editorial_calendar_slots_channel_workspace_id", "channel_workspace_id"),
        Index("ix_editorial_calendar_slots_policy_snapshot_id", "policy_snapshot_id"),
        Index("ix_editorial_calendar_slots_category_id", "category_id"),
        Index("ix_editorial_calendar_slots_slot_date", "slot_date"),
        Index("ix_editorial_calendar_slots_status", "status"),
        Index("ix_editorial_calendar_slots_production_lane", "production_lane"),
        Index("ix_editorial_calendar_slots_assignment_mode", "assignment_mode"),
        Index(
            "ix_editorial_calendar_slots_preferred_series_plan_id",
            "preferred_series_plan_id",
        ),
        Index(
            "ix_editorial_calendar_slots_preferred_series_run_id",
            "preferred_series_run_id",
        ),
        Index("ix_editorial_calendar_slots_created_at", "created_at"),
    )


class ChannelDailyRun(Base):
    __tablename__ = "channel_daily_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    editorial_calendar_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_calendar_slots.id")
    )
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    run_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="REAL")
    trigger_type: Mapped[str] = mapped_column(String(40), nullable=False, default="MANUAL")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_pack_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_pack_snapshots.id")
    )
    channel_state_pack_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_state_pack_snapshots.id")
    )
    daily_idea_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("daily_idea_decisions.id")
    )
    project_admission_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_admission_decisions.id")
    )
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_channel_daily_runs_company_id", "company_id"),
        Index("ix_channel_daily_runs_channel_workspace_id", "channel_workspace_id"),
        Index("ix_channel_daily_runs_policy_snapshot_id", "policy_snapshot_id"),
        Index("ix_channel_daily_runs_slot_id", "editorial_calendar_slot_id"),
        Index("ix_channel_daily_runs_run_date", "run_date"),
        Index("ix_channel_daily_runs_status", "status"),
        Index("ix_channel_daily_runs_created_at", "created_at"),
    )


class RetrievalPlanSnapshot(Base):
    __tablename__ = "retrieval_plan_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    purpose: Mapped[str] = mapped_column(String(60), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id")
    )
    channel_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id")
    )
    policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id")
    )
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    editorial_calendar_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_calendar_slots.id")
    )
    allowed_sources: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    excluded_sources: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    redaction_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    token_budget: Mapped[int | None] = mapped_column(Integer)
    source_order: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    plan_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_retrieval_plan_snapshots_purpose", "purpose"),
        Index("ix_retrieval_plan_snapshots_company_id", "company_id"),
        Index("ix_retrieval_plan_snapshots_channel_workspace_id", "channel_workspace_id"),
        Index("ix_retrieval_plan_snapshots_policy_snapshot_id", "policy_snapshot_id"),
        Index("ix_retrieval_plan_snapshots_created_at", "created_at"),
    )


class ContextPackSnapshot(Base):
    __tablename__ = "context_pack_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    retrieval_plan_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("retrieval_plan_snapshots.id"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(60), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id")
    )
    channel_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id")
    )
    policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id")
    )
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    editorial_calendar_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_calendar_slots.id")
    )
    input_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    policy_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    metric_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    memory_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    pack_content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False)
    pack_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_context_pack_snapshots_plan_id", "retrieval_plan_snapshot_id"),
        Index("ix_context_pack_snapshots_purpose", "purpose"),
        Index("ix_context_pack_snapshots_company_id", "company_id"),
        Index("ix_context_pack_snapshots_channel_workspace_id", "channel_workspace_id"),
        Index("ix_context_pack_snapshots_policy_snapshot_id", "policy_snapshot_id"),
        Index("ix_context_pack_snapshots_created_at", "created_at"),
    )


class ChannelStatePackSnapshot(Base):
    __tablename__ = "channel_state_pack_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_daily_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_daily_runs.id"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    context_pack_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_pack_snapshots.id")
    )
    state_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    active_project_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    pending_review_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    readiness_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider_health_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    quota_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False)
    state_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_channel_state_pack_snapshots_daily_run_id", "channel_daily_run_id"),
        Index("ix_channel_state_pack_snapshots_company_id", "company_id"),
        Index("ix_channel_state_pack_snapshots_channel_workspace_id", "channel_workspace_id"),
        Index("ix_channel_state_pack_snapshots_policy_snapshot_id", "policy_snapshot_id"),
        Index("ix_channel_state_pack_snapshots_created_at", "created_at"),
    )


class SearchDemandEvidence(Base):
    __tablename__ = "search_demand_evidence"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    evidence_source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    geo: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    lookback_window_days: Mapped[int | None] = mapped_column(Integer)
    search_volume_30d: Mapped[int | None] = mapped_column(Integer)
    relative_interest_index: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    competition_index: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    trending_velocity: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    evidence_confidence: Mapped[str] = mapped_column(String(40), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_search_demand_evidence_company_id", "company_id"),
        Index("ix_search_demand_evidence_channel_workspace_id", "channel_workspace_id"),
        Index("ix_search_demand_evidence_source_type", "evidence_source_type"),
        Index("ix_search_demand_evidence_platform", "platform"),
        Index("ix_search_demand_evidence_created_at", "created_at"),
    )


class SearchIntentMap(Base):
    __tablename__ = "search_intent_maps"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    channel_daily_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_daily_runs.id"))
    daily_idea_decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("daily_idea_decisions.id"))
    primary_search_intent: Mapped[str] = mapped_column(Text, nullable=False)
    secondary_search_intents: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    keyword_cluster: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    audience_problem: Mapped[str | None] = mapped_column(Text)
    audience_language: Mapped[str | None] = mapped_column(Text)
    target_geo: Mapped[str | None] = mapped_column(Text)
    source_evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    demand_confidence: Mapped[str] = mapped_column(String(40), nullable=False)
    competition_notes: Mapped[str | None] = mapped_column(Text)
    content_gap_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_search_intent_maps_company_id", "company_id"),
        Index("ix_search_intent_maps_channel_workspace_id", "channel_workspace_id"),
        Index("ix_search_intent_maps_daily_run_id", "channel_daily_run_id"),
        Index("ix_search_intent_maps_daily_idea_decision_id", "daily_idea_decision_id"),
        Index("ix_search_intent_maps_created_at", "created_at"),
    )


class AudienceTargetPack(Base):
    __tablename__ = "audience_target_packs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    channel_daily_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_daily_runs.id"))
    daily_idea_decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("daily_idea_decisions.id"))
    target_audience: Mapped[str] = mapped_column(Text, nullable=False)
    audience_problem: Mapped[str] = mapped_column(Text, nullable=False)
    audience_language: Mapped[str | None] = mapped_column(Text)
    target_geo: Mapped[str | None] = mapped_column(Text)
    platform_surface_hypothesis: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    audience_rationale: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_audience_target_packs_company_id", "company_id"),
        Index("ix_audience_target_packs_channel_workspace_id", "channel_workspace_id"),
        Index("ix_audience_target_packs_daily_run_id", "channel_daily_run_id"),
        Index("ix_audience_target_packs_daily_idea_decision_id", "daily_idea_decision_id"),
        Index("ix_audience_target_packs_created_at", "created_at"),
    )


class IdeaMarketPreflight(Base):
    __tablename__ = "idea_market_preflights"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    editorial_calendar_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_calendar_slots.id")
    )
    channel_daily_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_daily_runs.id"))
    daily_idea_decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("daily_idea_decisions.id"))
    search_intent_map_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("search_intent_maps.id"))
    audience_target_pack_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("audience_target_packs.id"))
    demand_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    channel_fit_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    policy_fit_state: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence_state: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_idea_market_preflights_company_id", "company_id"),
        Index("ix_idea_market_preflights_channel_workspace_id", "channel_workspace_id"),
        Index(
            "ix_idea_market_preflights_editorial_slot_id",
            "editorial_calendar_slot_id",
        ),
        Index("ix_idea_market_preflights_daily_run_id", "channel_daily_run_id"),
        Index("ix_idea_market_preflights_daily_idea_decision_id", "daily_idea_decision_id"),
        Index("ix_idea_market_preflights_decision", "decision"),
        Index("ix_idea_market_preflights_created_at", "created_at"),
    )


class DailyIdeaDecision(Base):
    __tablename__ = "daily_idea_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_daily_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_daily_runs.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    context_pack_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_pack_snapshots.id"), nullable=False
    )
    channel_state_pack_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_state_pack_snapshots.id")
    )
    llm_run_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("llm_run_snapshots.id"))
    schema_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    production_lane: Mapped[str | None] = mapped_column(String(40))
    proposed_content_mode: Mapped[str | None] = mapped_column(String(40))
    assignment_input_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decision_status: Mapped[str] = mapped_column(String(40), nullable=False)
    proposed_title: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_angle: Mapped[str | None] = mapped_column(Text)
    proposed_format: Mapped[str | None] = mapped_column(Text)
    proposed_pillar: Mapped[str | None] = mapped_column(Text)
    proposed_series_key: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "schema_version in ('v1','v2')",
            name="ck_daily_idea_decisions_schema_version",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(schema_version = 'v2' and production_lane = 'DAILY_SHORT' "
            "and (proposed_content_mode is null "
            "or proposed_content_mode in ('SERIES_EPISODE','STANDALONE')) "
            "and assignment_input_ref is not null)",
            name="ck_daily_idea_decisions_v2_daily_short",
        ),
        Index("ix_daily_idea_decisions_daily_run_id", "channel_daily_run_id"),
        Index("ix_daily_idea_decisions_company_id", "company_id"),
        Index("ix_daily_idea_decisions_channel_workspace_id", "channel_workspace_id"),
        Index("ix_daily_idea_decisions_policy_snapshot_id", "policy_snapshot_id"),
        Index("ix_daily_idea_decisions_context_pack_id", "context_pack_snapshot_id"),
        Index("ix_daily_idea_decisions_llm_run_id", "llm_run_snapshot_id"),
        Index("ix_daily_idea_decisions_production_lane", "production_lane"),
        Index("ix_daily_idea_decisions_status", "decision_status"),
        Index("ix_daily_idea_decisions_created_at", "created_at"),
    )


class ProjectAdmissionDecision(Base):
    __tablename__ = "project_admission_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    schema_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    channel_daily_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_daily_runs.id")
    )
    daily_idea_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("daily_idea_decisions.id")
    )
    editorial_calendar_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_calendar_slots.id")
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id")
    )
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id")
    )
    channel_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id")
    )
    policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id")
    )
    idea_market_preflight_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("idea_market_preflights.id"))
    planning_source_type: Mapped[str | None] = mapped_column(String(40))
    production_lane: Mapped[str | None] = mapped_column(String(40))
    content_mode: Mapped[str | None] = mapped_column(String(40))
    assignment_mode: Mapped[str | None] = mapped_column(String(40))
    series_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id")
    )
    series_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_runs.id")
    )
    episode_number: Mapped[int | None] = mapped_column(Integer)
    episode_role: Mapped[str | None] = mapped_column(String(120))
    standalone_reason_code: Mapped[str | None] = mapped_column(String(160))
    parent_video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    parent_final_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id")
    )
    canonical_timeline_ref: Mapped[str | None] = mapped_column(Text)
    canonical_timeline_hash: Mapped[str | None] = mapped_column(String(64))
    resolver_version: Mapped[str | None] = mapped_column(String(80))
    resolver_input_hash: Mapped[str | None] = mapped_column(String(64))
    decision_hash: Mapped[str | None] = mapped_column(String(64))
    assignment_input_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    duration_contract: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    budget_gate_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    readiness_gate_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    admitted_video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    created_artifact_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "schema_version in ('v1','v2')",
            name="ck_project_admission_decisions_schema_version",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(schema_version = 'v2' "
            "and company_id is not null "
            "and channel_workspace_id is not null "
            "and channel_profile_version_id is not null "
            "and policy_snapshot_id is not null "
            "and planning_source_type in "
            "('DAILY_IDEA','LONG_FORM_PLAN','DERIVED_SHORT') "
            "and production_lane in "
            "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
            "and assignment_mode in "
            "('SERIES_REQUIRED','SERIES_PREFERRED',"
            "'STANDALONE_REQUIRED','OPEN_MIX') "
            "and resolver_version is not null "
            "and resolver_input_hash ~ '^[0-9a-f]{64}$' "
            "and decision_hash ~ '^[0-9a-f]{64}$' "
            "and assignment_input_ref is not null "
            "and ((decision = 'BLOCK') or "
            "(decision = 'ADMIT' "
            "and admitted_video_project_id is not null "
            "and duration_contract is not null "
            "and ((content_mode = 'SERIES_EPISODE' "
            "and series_plan_id is not null "
            "and series_run_id is not null "
            "and episode_number > 0 "
            "and standalone_reason_code is null) "
            "or (content_mode = 'STANDALONE' "
            "and series_plan_id is null "
            "and series_run_id is null "
            "and episode_number is null "
            "and episode_role is null "
            "and standalone_reason_code is not null)))))",
            name="ck_project_admission_decisions_v2_authority",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or (decision = 'BLOCK') or "
            "((planning_source_type = 'DAILY_IDEA' "
            "and production_lane = 'DAILY_SHORT' "
            "and channel_daily_run_id is not null "
            "and daily_idea_decision_id is not null) "
            "or (planning_source_type = 'LONG_FORM_PLAN' "
            "and production_lane = 'LONG_FORM' "
            "and editorial_calendar_slot_id is not null "
            "and channel_daily_run_id is null "
            "and daily_idea_decision_id is null) "
            "or (planning_source_type = 'DERIVED_SHORT' "
            "and production_lane = 'LONG_DERIVED_SHORT' "
            "and content_mode = 'STANDALONE' "
            "and assignment_mode = 'STANDALONE_REQUIRED' "
            "and parent_video_project_id is not null "
            "and canonical_timeline_ref is not null "
            "and canonical_timeline_hash ~ '^[0-9a-f]{64}$'))",
            name="ck_project_admission_decisions_v2_lane_source",
        ),
        Index("ix_project_admission_decisions_daily_run_id", "channel_daily_run_id"),
        Index("ix_project_admission_decisions_daily_idea_id", "daily_idea_decision_id"),
        Index(
            "ix_project_admission_decisions_editorial_slot_id",
            "editorial_calendar_slot_id",
        ),
        Index("ix_project_admission_decisions_preflight_id", "idea_market_preflight_id"),
        Index(
            "ix_project_admission_decisions_planning_source_type",
            "planning_source_type",
        ),
        Index("ix_project_admission_decisions_production_lane", "production_lane"),
        Index("ix_project_admission_decisions_series_plan_id", "series_plan_id"),
        Index("ix_project_admission_decisions_series_run_id", "series_run_id"),
        Index("ix_project_admission_decisions_decision", "decision"),
        Index("ix_project_admission_decisions_project_id", "admitted_video_project_id"),
        Index(
            "uq_project_admission_series_episode",
            "series_run_id",
            "episode_number",
            unique=True,
            postgresql_where=text(
                "series_run_id is not null and episode_number is not null"
            ),
        ),
        Index(
            "uq_project_admission_v2_daily_source",
            "daily_idea_decision_id",
            unique=True,
            postgresql_where=text(
                "schema_version = 'v2' "
                "and planning_source_type = 'DAILY_IDEA' "
                "and daily_idea_decision_id is not null"
            ),
        ),
        Index(
            "uq_project_admission_v2_long_form_source",
            "editorial_calendar_slot_id",
            unique=True,
            postgresql_where=text(
                "schema_version = 'v2' "
                "and planning_source_type = 'LONG_FORM_PLAN' "
                "and editorial_calendar_slot_id is not null"
            ),
        ),
        UniqueConstraint(
            "decision_hash",
            name="uq_project_admission_decisions_decision_hash",
        ),
        Index("ix_project_admission_decisions_created_at", "created_at"),
    )
