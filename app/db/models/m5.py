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
        UUID(as_uuid=True),
        ForeignKey("compiled_channel_policy_snapshots.id"),
        nullable=False,
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id")
    )
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
    target_platforms: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    content_pillar: Mapped[str | None] = mapped_column(Text)
    series_key: Mapped[str | None] = mapped_column(Text)
    format_hint: Mapped[str | None] = mapped_column(Text)
    character_binding_policy_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    risk_level: Mapped[str] = mapped_column(
        String(40), nullable=False, default="UNKNOWN"
    )
    operational_envelope: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
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
            "and production_lane = 'LONG_FORM' "
            "and assignment_mode in "
            "('SERIES_REQUIRED','SERIES_PREFERRED',"
            "'STANDALONE_REQUIRED','OPEN_MIX') "
            "and series_key is null "
            "and (preferred_series_run_id is null "
            "or preferred_series_plan_id is not null))",
            name="ck_editorial_calendar_slots_v2_authority",
        ),
        Index("ix_editorial_calendar_slots_company_id", "company_id"),
        Index(
            "ix_editorial_calendar_slots_channel_workspace_id", "channel_workspace_id"
        ),
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


class EditorialResearchRun(Base):
    __tablename__ = "editorial_research_runs"

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
    editorial_calendar_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_calendar_slots.id")
    )
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    trigger_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="MANUAL"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_pack_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_pack_snapshots.id")
    )
    channel_state_pack_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_state_pack_snapshots.id")
    )
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        CheckConstraint(
            "status in ('PENDING','RUNNING','COMPLETED','BLOCKED','FAILED',"
            "'CANCELLED','ARCHIVED')",
            name="ck_editorial_research_runs_status",
        ),
        CheckConstraint(
            "trigger_type in ('MANUAL','SCHEDULED','TEST','MIGRATED')",
            name="ck_editorial_research_runs_trigger",
        ),
        CheckConstraint(
            "candidate_count >= 0",
            name="ck_editorial_research_runs_candidate_count",
        ),
        Index("ix_editorial_research_runs_company_id", "company_id"),
        Index(
            "ix_editorial_research_runs_channel_workspace_id",
            "channel_workspace_id",
        ),
        Index(
            "ix_editorial_research_runs_profile_id",
            "channel_profile_version_id",
        ),
        Index("ix_editorial_research_runs_policy_snapshot_id", "policy_snapshot_id"),
        Index("ix_editorial_research_runs_slot_id", "editorial_calendar_slot_id"),
        Index("ix_editorial_research_runs_run_date", "run_date"),
        Index("ix_editorial_research_runs_status", "status"),
        Index("ix_editorial_research_runs_created_at", "created_at"),
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
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    editorial_calendar_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_calendar_slots.id")
    )
    allowed_sources: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    excluded_sources: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    redaction_rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    token_budget: Mapped[int | None] = mapped_column(Integer)
    source_order: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    plan_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_retrieval_plan_snapshots_purpose", "purpose"),
        Index("ix_retrieval_plan_snapshots_company_id", "company_id"),
        Index(
            "ix_retrieval_plan_snapshots_channel_workspace_id", "channel_workspace_id"
        ),
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
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    editorial_calendar_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_calendar_slots.id")
    )
    input_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    policy_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    metric_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    memory_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    pack_content: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False)
    pack_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
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
    editorial_research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_research_runs.id")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compiled_channel_policy_snapshots.id"),
        nullable=False,
    )
    context_pack_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_pack_snapshots.id")
    )
    state_blob: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    active_project_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    pending_review_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    readiness_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    provider_health_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    quota_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    evidence_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False)
    state_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index(
            "ix_channel_state_pack_snapshots_editorial_research_run_id",
            "editorial_research_run_id",
        ),
        Index("ix_channel_state_pack_snapshots_company_id", "company_id"),
        Index(
            "ix_channel_state_pack_snapshots_channel_workspace_id",
            "channel_workspace_id",
        ),
        Index(
            "ix_channel_state_pack_snapshots_policy_snapshot_id", "policy_snapshot_id"
        ),
        Index("ix_channel_state_pack_snapshots_created_at", "created_at"),
    )


class SearchDemandEvidence(Base):
    __tablename__ = "search_demand_evidence"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    evidence_source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_purpose: Mapped[str | None] = mapped_column(String(40))
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
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
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
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    editorial_research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_research_runs.id")
    )
    editorial_idea_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id")
    )
    primary_search_intent: Mapped[str] = mapped_column(Text, nullable=False)
    secondary_search_intents: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    keyword_cluster: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    audience_problem: Mapped[str | None] = mapped_column(Text)
    audience_language: Mapped[str | None] = mapped_column(Text)
    target_geo: Mapped[str | None] = mapped_column(Text)
    source_evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    demand_confidence: Mapped[str] = mapped_column(String(40), nullable=False)
    competition_notes: Mapped[str | None] = mapped_column(Text)
    content_gap_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_search_intent_maps_company_id", "company_id"),
        Index("ix_search_intent_maps_channel_workspace_id", "channel_workspace_id"),
        Index(
            "ix_search_intent_maps_editorial_research_run_id",
            "editorial_research_run_id",
        ),
        Index(
            "ix_search_intent_maps_editorial_idea_candidate_id",
            "editorial_idea_candidate_id",
        ),
        Index("ix_search_intent_maps_created_at", "created_at"),
    )


class AudienceTargetPack(Base):
    __tablename__ = "audience_target_packs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    editorial_research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_research_runs.id")
    )
    editorial_idea_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id")
    )
    target_audience: Mapped[str] = mapped_column(Text, nullable=False)
    audience_problem: Mapped[str] = mapped_column(Text, nullable=False)
    audience_language: Mapped[str | None] = mapped_column(Text)
    target_geo: Mapped[str | None] = mapped_column(Text)
    platform_surface_hypothesis: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    audience_rationale: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_audience_target_packs_company_id", "company_id"),
        Index("ix_audience_target_packs_channel_workspace_id", "channel_workspace_id"),
        Index(
            "ix_audience_target_packs_editorial_research_run_id",
            "editorial_research_run_id",
        ),
        Index(
            "ix_audience_target_packs_editorial_idea_candidate_id",
            "editorial_idea_candidate_id",
        ),
        Index("ix_audience_target_packs_created_at", "created_at"),
    )


class IdeaMarketPreflight(Base):
    __tablename__ = "idea_market_preflights"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    editorial_calendar_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_calendar_slots.id")
    )
    editorial_research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_research_runs.id")
    )
    editorial_idea_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id")
    )
    search_intent_map_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_intent_maps.id")
    )
    audience_target_pack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audience_target_packs.id")
    )
    demand_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    channel_fit_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    policy_fit_state: Mapped[str] = mapped_column(String(40), nullable=False)
    niche_contract_digest_ref: Mapped[str | None] = mapped_column(Text)
    niche_contract_digest_hash: Mapped[str | None] = mapped_column(String(64))
    target_market_digest_ref: Mapped[str | None] = mapped_column(Text)
    target_market_digest_hash: Mapped[str | None] = mapped_column(String(64))
    editorial_slot_ref: Mapped[str | None] = mapped_column(Text)
    content_category_ref: Mapped[str | None] = mapped_column(Text)
    target_market: Mapped[str | None] = mapped_column(String(2))
    market_scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    market_fit_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    market_fit_threshold: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    confidence_state: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_blob: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
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
        Index(
            "ix_idea_market_preflights_editorial_research_run_id",
            "editorial_research_run_id",
        ),
        Index(
            "ix_idea_market_preflights_editorial_idea_candidate_id",
            "editorial_idea_candidate_id",
        ),
        Index("ix_idea_market_preflights_decision", "decision"),
        Index("ix_idea_market_preflights_created_at", "created_at"),
        CheckConstraint(
            "(niche_contract_digest_hash is null or "
            "niche_contract_digest_hash ~ '^[0-9a-f]{64}$') and "
            "(target_market_digest_hash is null or "
            "target_market_digest_hash ~ '^[0-9a-f]{64}$')",
            name="ck_idea_market_preflights_authority_hashes",
        ),
    )


class EditorialIdeaCandidate(Base):
    __tablename__ = "editorial_idea_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    editorial_research_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_research_runs.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compiled_channel_policy_snapshots.id"),
        nullable=False,
    )
    context_pack_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_pack_snapshots.id")
    )
    channel_state_pack_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_state_pack_snapshots.id")
    )
    llm_run_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_run_snapshots.id")
    )
    stage: Mapped[str] = mapped_column(String(40), nullable=False, default="RESEARCHED")
    # Historical candidates are never rewritten to make them eligible under a
    # newer editorial contract.  Bounded research repairs create a child and
    # retain this immutable discovery lineage.
    parent_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id")
    )
    replaces_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id")
    )
    replacement_authority_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_contract_replacement_authorities.id")
    )
    replacement_reason: Mapped[str | None] = mapped_column(String(160))
    replacement_lineage_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    script_contract_version: Mapped[str] = mapped_column(
        String(80), nullable=False, default="V1_LEGACY"
    )
    topic_repair_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    proposed_title: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_angle: Mapped[str | None] = mapped_column(Text)
    proposed_format: Mapped[str | None] = mapped_column(Text)
    proposed_pillar: Mapped[str | None] = mapped_column(Text)
    suggested_series_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id")
    )
    rationale: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False)
    budget_readiness: Mapped[str] = mapped_column(
        String(40), nullable=False, default="UNKNOWN"
    )
    rights_policy_state: Mapped[str] = mapped_column(
        String(40), nullable=False, default="UNKNOWN"
    )
    quality_state: Mapped[str] = mapped_column(
        String(40), nullable=False, default="UNKNOWN"
    )
    experiment_phase: Mapped[str | None] = mapped_column(String(40))
    primary_variable_under_test: Mapped[str | None] = mapped_column(String(160))
    audience_promise: Mapped[str | None] = mapped_column(Text)
    audience_promise_version: Mapped[str | None] = mapped_column(String(120))
    audience_promise_hash: Mapped[str | None] = mapped_column(String(64))
    target_audience_definition: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    audience_drift_guard_version: Mapped[str | None] = mapped_column(String(120))
    strategic_intent: Mapped[str | None] = mapped_column(String(40))
    intent_success_criteria: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    intent_success_criteria_version: Mapped[str | None] = mapped_column(String(120))
    intent_success_criteria_hash: Mapped[str | None] = mapped_column(String(64))
    experiment_hypothesis: Mapped[str | None] = mapped_column(Text)
    decision_reversibility: Mapped[str | None] = mapped_column(String(32))
    active_launch_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("first_channel_launch_policy_versions.id"),
    )
    active_launch_policy_hash: Mapped[str | None] = mapped_column(String(64))
    active_launch_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("launch_runs.id")
    )
    active_launch_run_hash: Mapped[str | None] = mapped_column(String(64))
    baseline_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    comparison_group: Mapped[str | None] = mapped_column(String(160))
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "stage in ('RESEARCHED','PREFLIGHT_PASS','PREFLIGHT_BLOCK','GREENLIT',"
            "'SELECTED_FOR_SLOT','IN_PRODUCTION','FINAL_REVIEW_READY','PUBLISHED',"
            "'REJECTED','EXPIRED')",
            name="ck_editorial_idea_candidates_stage",
        ),
        CheckConstraint(
            "topic_repair_depth between 0 and 2",
            name="ck_editorial_candidate_topic_repair_depth",
        ),
        CheckConstraint(
            "budget_readiness in ('READY','BLOCKED','UNKNOWN') "
            "and rights_policy_state in ('PASS','BLOCK','UNKNOWN') "
            "and quality_state in ('PASS','BLOCK','UNKNOWN')",
            name="ck_editorial_idea_candidates_readiness",
        ),
        CheckConstraint(
            "canonical_hash ~ '^[0-9a-f]{64}$'",
            name="ck_editorial_idea_candidates_hash",
        ),
        UniqueConstraint(
            "canonical_hash",
            name="uq_editorial_idea_candidates_canonical_hash",
        ),
        Index(
            "ix_editorial_idea_candidates_research_run_id",
            "editorial_research_run_id",
        ),
        Index("ix_editorial_idea_candidates_company_id", "company_id"),
        Index(
            "ix_editorial_idea_candidates_channel_workspace_id",
            "channel_workspace_id",
        ),
        Index("ix_editorial_idea_candidates_policy_snapshot_id", "policy_snapshot_id"),
        Index(
            "ix_editorial_idea_candidates_context_pack_id", "context_pack_snapshot_id"
        ),
        Index("ix_editorial_idea_candidates_stage", "stage"),
        Index("ix_editorial_candidate_parent", "parent_candidate_id"),
        Index("ix_editorial_candidate_replaces", "replaces_candidate_id"),
        Index(
            "ix_editorial_idea_candidates_active_launch_policy",
            "active_launch_policy_version_id",
        ),
        Index(
            "ix_editorial_idea_candidates_active_launch_run",
            "active_launch_run_id",
        ),
        Index("ix_editorial_idea_candidates_created_at", "created_at"),
        CheckConstraint(
            "script_contract_version in ('V1_LEGACY','V2_SINGLE_SOURCE')",
            name="ck_editorial_candidate_script_contract_version",
        ),
    )


class ProjectAdmissionDecision(Base):
    __tablename__ = "project_admission_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    schema_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    editorial_research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_research_runs.id")
    )
    editorial_idea_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id")
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
    idea_market_preflight_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("idea_market_preflights.id")
    )
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
    resolver_version: Mapped[str | None] = mapped_column(String(80))
    resolver_input_hash: Mapped[str | None] = mapped_column(String(64))
    decision_hash: Mapped[str | None] = mapped_column(String(64))
    assignment_input_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    duration_contract: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    audience_promise: Mapped[str | None] = mapped_column(Text)
    audience_promise_version: Mapped[str | None] = mapped_column(String(120))
    audience_promise_hash: Mapped[str | None] = mapped_column(String(64))
    target_audience_definition: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    audience_drift_guard_version: Mapped[str | None] = mapped_column(String(120))
    strategic_intent: Mapped[str | None] = mapped_column(String(40))
    intent_success_criteria: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    intent_success_criteria_version: Mapped[str | None] = mapped_column(String(120))
    intent_success_criteria_hash: Mapped[str | None] = mapped_column(String(64))
    experiment_hypothesis: Mapped[str | None] = mapped_column(Text)
    primary_variable_under_test: Mapped[str | None] = mapped_column(String(160))
    decision_reversibility: Mapped[str | None] = mapped_column(String(32))
    active_launch_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("first_channel_launch_policy_versions.id"),
    )
    active_launch_policy_hash: Mapped[str | None] = mapped_column(String(64))
    active_launch_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("launch_runs.id")
    )
    active_launch_run_hash: Mapped[str | None] = mapped_column(String(64))
    budget_gate_result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    readiness_gate_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    admitted_video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    created_artifact_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
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
            "and planning_source_type = 'LONG_FORM_PLAN' "
            "and production_lane = 'LONG_FORM' "
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
            "(planning_source_type = 'LONG_FORM_PLAN' "
            "and production_lane = 'LONG_FORM' "
            "and editorial_calendar_slot_id is not null "
            ")",
            name="ck_project_admission_decisions_v2_lane_source",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or ("
            "audience_promise is not null and btrim(audience_promise) <> '' "
            "and audience_promise_version is not null "
            "and audience_promise_hash ~ '^[0-9a-f]{64}$' "
            "and target_audience_definition is not null "
            "and jsonb_typeof(target_audience_definition) = 'object' "
            "and target_audience_definition <> '{}'::jsonb "
            "and audience_drift_guard_version is not null "
            "and strategic_intent in ("
            "'ACQUISITION','AUDIENCE_DEPTH','AUTHORITY',"
            "'SERIES_CONTINUITY','CONTROLLED_EXPERIMENT') "
            "and intent_success_criteria is not null "
            "and jsonb_typeof(intent_success_criteria) = 'object' "
            "and intent_success_criteria <> '{}'::jsonb "
            "and intent_success_criteria_version is not null "
            "and intent_success_criteria_hash ~ '^[0-9a-f]{64}$' "
            "and primary_variable_under_test is not null "
            "and btrim(primary_variable_under_test) <> '' "
            "and decision_reversibility in ('TWO_WAY_DOOR','ONE_WAY_DOOR') "
            "and active_launch_policy_version_id is not null "
            "and active_launch_policy_hash ~ '^[0-9a-f]{64}$' "
            "and active_launch_run_id is not null "
            "and active_launch_run_hash ~ '^[0-9a-f]{64}$' "
            "and (strategic_intent <> 'CONTROLLED_EXPERIMENT' "
            "or (experiment_hypothesis is not null "
            "and btrim(experiment_hypothesis) <> '')))",
            name="ck_project_admission_decisions_v2_strategic_lineage",
        ),
        Index(
            "ix_project_admission_decisions_editorial_research_run_id",
            "editorial_research_run_id",
        ),
        Index(
            "ix_project_admission_decisions_editorial_idea_candidate_id",
            "editorial_idea_candidate_id",
        ),
        Index(
            "ix_project_admission_decisions_editorial_slot_id",
            "editorial_calendar_slot_id",
        ),
        Index(
            "ix_project_admission_decisions_preflight_id", "idea_market_preflight_id"
        ),
        Index(
            "ix_project_admission_decisions_planning_source_type",
            "planning_source_type",
        ),
        Index("ix_project_admission_decisions_production_lane", "production_lane"),
        Index("ix_project_admission_decisions_series_plan_id", "series_plan_id"),
        Index("ix_project_admission_decisions_series_run_id", "series_run_id"),
        Index(
            "ix_project_admission_decisions_active_launch_policy",
            "active_launch_policy_version_id",
        ),
        Index(
            "ix_project_admission_decisions_active_launch_run",
            "active_launch_run_id",
        ),
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
            "uq_project_admission_v2_editorial_candidate",
            "editorial_idea_candidate_id",
            unique=True,
            postgresql_where=text(
                "schema_version = 'v2' and editorial_idea_candidate_id is not null"
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
