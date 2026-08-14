"""Durable market-aware voice and narration-performance authorities."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
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

from app.db.base import Base
from app.db.models.foundation import utc_created_at, uuid_pk


class VoiceMarketResearchArtifact(Base):
    __tablename__ = "voice_market_research_artifacts"

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
    schema_version: Mapped[str] = mapped_column(
        String(80), nullable=False, default="vcos.voice-market-research.v1"
    )
    market_identity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    requirements: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    confidence_label: Mapped[str] = mapped_column(String(16), nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="APPROVED")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "channel_profile_version_id",
            "policy_snapshot_id",
            "content_hash",
            name="uq_voice_market_research_identity",
        ),
        CheckConstraint(
            "confidence_label in ('LOW','MEDIUM','HIGH')",
            name="ck_voice_market_research_confidence",
        ),
        CheckConstraint(
            "state in ('APPROVED','SUPERSEDED','REJECTED')",
            name="ck_voice_market_research_state",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_voice_market_research_hash",
        ),
        Index(
            "ix_voice_market_research_channel",
            "channel_workspace_id",
            "created_at",
        ),
    )


class VoiceProviderCatalogSnapshot(Base):
    __tablename__ = "voice_provider_catalog_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(
        String(80), nullable=False, default="vcos.voice-provider-catalog.v1"
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(120), nullable=False)
    voices: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    source_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "provider",
            "catalog_version",
            "content_hash",
            name="uq_voice_provider_catalog_identity",
        ),
        CheckConstraint(
            "provider = 'elevenlabs'",
            name="ck_voice_provider_catalog_provider",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_voice_provider_catalog_hash",
        ),
        Index(
            "ix_voice_provider_catalog_channel",
            "channel_workspace_id",
            "created_at",
        ),
    )


class ApprovedVoicePool(Base):
    __tablename__ = "approved_voice_pools"

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
    voice_market_research_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("voice_market_research_artifacts.id"),
        nullable=False,
    )
    provider_catalog_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("voice_provider_catalog_snapshots.id"),
        nullable=False,
    )
    schema_version: Mapped[str] = mapped_column(
        String(80), nullable=False, default="vcos.approved-voice-pool.v1"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    voices: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="APPROVED")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id", "version", name="uq_approved_voice_pool_version"
        ),
        UniqueConstraint(
            "channel_workspace_id",
            "channel_profile_version_id",
            "policy_snapshot_id",
            "content_hash",
            name="uq_approved_voice_pool_identity",
        ),
        CheckConstraint("version > 0", name="ck_approved_voice_pool_version"),
        CheckConstraint(
            "status in ('APPROVED','SUPERSEDED')",
            name="ck_approved_voice_pool_status",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_approved_voice_pool_hash",
        ),
        Index(
            "ix_approved_voice_pool_channel_status",
            "channel_workspace_id",
            "status",
        ),
    )


class VoiceCastingDecision(Base):
    __tablename__ = "voice_casting_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    approved_voice_pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approved_voice_pools.id"), nullable=False
    )
    approved_voice_pool_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(
        String(80), nullable=False, default="vcos.voice-casting-decision.v1"
    )
    qualified_script_ref: Mapped[str] = mapped_column(Text, nullable=False)
    qualified_script_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    narration_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    selected_voice_id: Mapped[str] = mapped_column(Text, nullable=False)
    selected_model_id: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_delivery_profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    selection_reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    market_fit_evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    series_narrator_binding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_narrator_bindings.id", use_alter=True)
    )
    casting_policy_version: Mapped[str] = mapped_column(String(120), nullable=False)
    decision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="FROZEN")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "video_project_id", "decision_version", name="uq_voice_casting_version"
        ),
        UniqueConstraint(
            "video_project_id", "content_hash", name="uq_voice_casting_identity"
        ),
        CheckConstraint("decision_version > 0", name="ck_voice_casting_version"),
        CheckConstraint(
            "narration_mode in ('TECHNICAL_EXPLAINER','ANALYTICAL','TACTICAL',"
            "'STORY_CASE_STUDY','DOCUMENTARY','CAUTIONARY')",
            name="ck_voice_casting_mode",
        ),
        CheckConstraint(
            "state in ('FROZEN','SUPERSEDED')",
            name="ck_voice_casting_state",
        ),
        CheckConstraint(
            "approved_voice_pool_hash ~ '^[0-9a-f]{64}$' and "
            "qualified_script_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_voice_casting_hashes",
        ),
        Index("ix_voice_casting_project_state", "video_project_id", "state"),
    )


class SeriesNarratorBinding(Base):
    __tablename__ = "series_narrator_bindings"

    id: Mapped[uuid.UUID] = uuid_pk()
    series_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id"), nullable=False
    )
    approved_voice_pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approved_voice_pools.id"), nullable=False
    )
    source_voice_casting_decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("voice_casting_decisions.id", use_alter=True),
        nullable=False,
    )
    schema_version: Mapped[str] = mapped_column(
        String(80), nullable=False, default="vcos.series-narrator-binding.v1"
    )
    binding_version: Mapped[int] = mapped_column(Integer, nullable=False)
    voice_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "series_plan_id", "binding_version", name="uq_series_narrator_version"
        ),
        UniqueConstraint(
            "series_plan_id", "content_hash", name="uq_series_narrator_identity"
        ),
        CheckConstraint("binding_version > 0", name="ck_series_narrator_version"),
        CheckConstraint(
            "state in ('ACTIVE','SUPERSEDED')", name="ck_series_narrator_state"
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_series_narrator_hash"
        ),
        Index("ix_series_narrator_plan_state", "series_plan_id", "state"),
    )


class NarrationVoiceSnapshot(Base):
    __tablename__ = "narration_voice_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    voice_casting_decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_casting_decisions.id"), nullable=False
    )
    approved_voice_pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approved_voice_pools.id"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(
        String(80), nullable=False, default="vcos.narration-voice-snapshot.v1"
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    voice_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_voice_settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    voice_catalog_version: Mapped[str] = mapped_column(String(120), nullable=False)
    approved_voice_pool_version: Mapped[int] = mapped_column(Integer, nullable=False)
    market_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    qualified_script_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "video_project_id",
            "content_hash",
            name="uq_narration_voice_snapshot_identity",
        ),
        CheckConstraint("provider = 'elevenlabs'", name="ck_narration_voice_provider"),
        CheckConstraint(
            "state in ('ACTIVE','SUPERSEDED')", name="ck_narration_voice_state"
        ),
        CheckConstraint(
            "market_identity_hash ~ '^[0-9a-f]{64}$' and "
            "qualified_script_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_narration_voice_hashes",
        ),
        Index("ix_narration_voice_project_state", "video_project_id", "state"),
    )


class NarrationPerformancePlan(Base):
    __tablename__ = "narration_performance_plans"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    narration_voice_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("narration_voice_snapshots.id"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(
        String(80), nullable=False, default="vcos.narration-performance-plan.v1"
    )
    qualified_script_ref: Mapped[str] = mapped_column(Text, nullable=False)
    qualified_script_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_narration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    voice_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_delivery: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    beats: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    performance_policy_version: Mapped[str] = mapped_column(String(120), nullable=False)
    coverage_gate_state: Mapped[str] = mapped_column(String(16), nullable=False)
    semantic_alignment_gate_state: Mapped[str] = mapped_column(
        String(16), nullable=False
    )
    continuity_gate_state: Mapped[str] = mapped_column(String(16), nullable=False)
    monotony_risk_gate_state: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="FROZEN")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "video_project_id", "content_hash", name="uq_narration_performance_identity"
        ),
        CheckConstraint(
            "coverage_gate_state = 'PASS' and "
            "semantic_alignment_gate_state = 'PASS' and "
            "continuity_gate_state = 'PASS' and "
            "monotony_risk_gate_state = 'PASS'",
            name="ck_narration_performance_gates",
        ),
        CheckConstraint(
            "state in ('FROZEN','SUPERSEDED')",
            name="ck_narration_performance_state",
        ),
        CheckConstraint(
            "qualified_script_hash ~ '^[0-9a-f]{64}$' and "
            "canonical_narration_hash ~ '^[0-9a-f]{64}$' and "
            "voice_snapshot_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_narration_performance_hashes",
        ),
        Index("ix_narration_performance_project", "video_project_id", "created_at"),
    )


class TTSPerformanceProjection(Base):
    __tablename__ = "tts_performance_projections"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    narration_performance_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("narration_performance_plans.id"), nullable=False
    )
    narration_voice_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("narration_voice_snapshots.id"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(
        String(80), nullable=False, default="vcos.tts-performance-projection.v1"
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    execution_strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_profile_version: Mapped[str] = mapped_column(String(120), nullable=False)
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="FROZEN")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "narration_performance_plan_id",
            "content_hash",
            name="uq_tts_performance_projection_identity",
        ),
        CheckConstraint("provider = 'elevenlabs'", name="ck_tts_projection_provider"),
        CheckConstraint(
            "execution_strategy in ('SINGLE_REQUEST_EXPRESSIVE',"
            "'CONTEXT_STITCHED_MULTI_REQUEST','SEGMENTED_WITH_SEAM_QC')",
            name="ck_tts_projection_strategy",
        ),
        CheckConstraint(
            "state in ('FROZEN','SUPERSEDED')", name="ck_tts_projection_state"
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_tts_projection_hash"
        ),
        Index("ix_tts_performance_project", "video_project_id", "created_at"),
    )


class CombinedReplacementBudgetAuthority(Base):
    """One immutable pre-effect cost authority for a V2 replacement run.

    The row is deliberately separate from MR1's mutable settlement lifecycle.
    It records the exact preflight inputs that justified the reservation and is
    never repurposed for a later package or provider plan.
    """

    __tablename__ = "combined_replacement_budget_authorities"

    id: Mapped[uuid.UUID] = uuid_pk()
    authority_ref: Mapped[str] = mapped_column(Text, nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    budget_reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mr1_monthly_budget_reservations.id"),
        nullable=False,
    )
    budget_reservation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    support_envelope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    route_budget_authority_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    tts_performance_projection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tts_performance_projections.id"), nullable=False
    )
    tts_performance_projection_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    source_refs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    new_tts_projected_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False
    )
    forced_alignment_projected_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False
    )
    ai_image_projected_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False
    )
    ai_video_projected_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False
    )
    other_metered_effects_projected_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False
    )
    combined_replacement_projected_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False
    )
    approved_ceiling_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    shortfall_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="FROZEN")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("authority_ref", name="uq_combined_replacement_budget_ref"),
        UniqueConstraint(
            "video_project_id",
            "support_envelope_hash",
            "tts_performance_projection_hash",
            "content_hash",
            name="uq_combined_replacement_budget_identity",
        ),
        CheckConstraint("state = 'FROZEN'", name="ck_combined_replacement_budget_state"),
        CheckConstraint(
            "new_tts_projected_cost_usd >= 0 and "
            "forced_alignment_projected_cost_usd >= 0 and "
            "ai_image_projected_cost_usd >= 0 and "
            "ai_video_projected_cost_usd >= 0 and "
            "other_metered_effects_projected_cost_usd >= 0 and "
            "combined_replacement_projected_cost_usd >= 0 and "
            "approved_ceiling_usd >= 0 and shortfall_usd >= 0",
            name="ck_combined_replacement_budget_nonnegative",
        ),
        CheckConstraint(
            "support_envelope_hash ~ '^[0-9a-f]{64}$' and "
            "route_budget_authority_hash ~ '^[0-9a-f]{64}$' and "
            "tts_performance_projection_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_combined_replacement_budget_hashes",
        ),
        Index(
            "ix_combined_replacement_budget_project_created",
            "video_project_id",
            "created_at",
        ),
    )


class NarrationSegmentExecution(Base):
    """Append-only paid-effect intent for one projected narration segment.

    The record is deliberately created before the provider call.  It is not a
    cache of a "latest" voice configuration: every hash is an immutable input
    to the exact paid effect and an uncertain effect can never be re-submitted.
    """

    __tablename__ = "narration_segment_executions"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    narration_voice_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("narration_voice_snapshots.id"), nullable=False
    )
    narration_voice_snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    narration_performance_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("narration_performance_plans.id"), nullable=False
    )
    narration_performance_plan_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    tts_performance_projection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tts_performance_projections.id"), nullable=False
    )
    tts_performance_projection_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    segment_id: Mapped[str] = mapped_column(String(120), nullable=False)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_effect_key: Mapped[str] = mapped_column(String(160), nullable=False)
    voice_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    compiled_voice_settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    provider_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="INTENDED")
    provider_request_hash: Mapped[str | None] = mapped_column(String(64))
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    audio_ref: Mapped[str | None] = mapped_column(Text)
    audio_checksum: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    timing_seed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    estimated_cost_usd: Mapped[str | None] = mapped_column(String(40))
    actual_cost_usd: Mapped[str | None] = mapped_column(String(40))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome_certainty: Mapped[str] = mapped_column(
        String(24), nullable=False, default="NOT_SENT"
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "video_project_id",
            "provider_effect_key",
            name="uq_narration_segment_effect_key",
        ),
        UniqueConstraint(
            "tts_performance_projection_id",
            "segment_index",
            name="uq_narration_segment_projection_index",
        ),
        CheckConstraint("segment_index >= 0", name="ck_narration_segment_index"),
        CheckConstraint(
            "attempt_count between 0 and 1", name="ck_narration_segment_attempt_count"
        ),
        CheckConstraint(
            "state in ('INTENDED','SUBMITTED','VERIFIED','PROVIDER_OUTCOME_UNKNOWN','FAILED')",
            name="ck_narration_segment_state",
        ),
        CheckConstraint(
            "outcome_certainty in ('NOT_SENT','SUBMITTED','VERIFIED','UNKNOWN','FAILED')",
            name="ck_narration_segment_outcome_certainty",
        ),
        CheckConstraint(
            "(state <> 'VERIFIED') or (provider_request_hash is not null and "
            "provider_request_id is not null and audio_ref is not null and "
            "audio_checksum is not null and duration_ms > 0 and "
            "timing_seed is not null)",
            name="ck_narration_segment_verified_evidence",
        ),
        CheckConstraint(
            "narration_voice_snapshot_hash ~ '^[0-9a-f]{64}$' and narration_performance_plan_hash ~ '^[0-9a-f]{64}$' and tts_performance_projection_hash ~ '^[0-9a-f]{64}$' and canonical_text_hash ~ '^[0-9a-f]{64}$' and provider_projection_hash ~ '^[0-9a-f]{64}$' and content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_narration_segment_hashes",
        ),
        Index("ix_narration_segment_project_state", "video_project_id", "state"),
    )
