"""Durable closeout authorities for series, learning, and media-business state.

These models are additive. Existing SeriesPlan/SeriesRun, analytics, learning,
and publishing tables remain the runtime backbone; this module adds only the
missing immutable/versioned truths identified by the architecture closeout.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class SeriesArcVersion(Base):
    __tablename__ = "series_arc_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    series_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_plans.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    planning_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    planned_episode_count: Mapped[int | None] = mapped_column(Integer)
    editorial_coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    supersedes_series_arc_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("series_arc_versions.id"))
    approval_evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("series_plan_id", "version", name="uq_series_arc_versions_plan_version"),
        UniqueConstraint("content_hash", name="uq_series_arc_versions_hash"),
        CheckConstraint("version > 0", name="ck_series_arc_versions_version"),
        CheckConstraint("planning_mode in ('FIXED_COUNT','ROLLING')", name="ck_series_arc_versions_mode"),
        CheckConstraint("(planning_mode = 'FIXED_COUNT' and planned_episode_count > 0) or (planning_mode = 'ROLLING' and planned_episode_count is null)", name="ck_series_arc_versions_count"),
        CheckConstraint("state in ('DRAFT','APPROVED','SUPERSEDED','ARCHIVED')", name="ck_series_arc_versions_state"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_series_arc_versions_hash"),
        Index("ix_series_arc_versions_plan_state", "series_plan_id", "state"),
        Index("ix_series_arc_versions_channel", "channel_workspace_id"),
    )


class SeriesEpisodeBlueprint(Base):
    __tablename__ = "series_episode_blueprints"

    id: Mapped[uuid.UUID] = uuid_pk()
    series_arc_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_arc_versions.id"), nullable=False)
    series_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_plans.id"), nullable=False)
    blueprint_key: Mapped[str] = mapped_column(String(160), nullable=False)
    editorial_position: Mapped[int] = mapped_column(Integer, nullable=False)
    title_hint: Mapped[str | None] = mapped_column(Text)
    editorial_purpose: Mapped[str] = mapped_column(Text, nullable=False)
    coverage_contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="PLANNED")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("series_arc_version_id", "blueprint_key", name="uq_series_episode_blueprints_key"),
        UniqueConstraint("series_arc_version_id", "editorial_position", name="uq_series_episode_blueprints_position"),
        CheckConstraint("editorial_position > 0", name="ck_series_episode_blueprints_position"),
        CheckConstraint("state in ('PLANNED','OPTIONAL','DEFERRED')", name="ck_series_episode_blueprints_state"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_series_episode_blueprints_hash"),
        Index("ix_series_episode_blueprints_plan", "series_plan_id"),
    )


class SeriesEpisodeAttemptAuthority(Base):
    """Technical attempt identity; deliberately not a public episode number."""

    __tablename__ = "series_episode_attempt_authorities"

    id: Mapped[uuid.UUID] = uuid_pk()
    series_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_plans.id"), nullable=False)
    series_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_runs.id"), nullable=False)
    series_arc_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_arc_versions.id"), nullable=False)
    episode_blueprint_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("series_episode_blueprints.id"))
    technical_attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reservation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="RESERVED")
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("series_run_id", "technical_attempt_number", name="uq_series_episode_attempts_run_number"),
        UniqueConstraint("identity_hash", name="uq_series_episode_attempts_hash"),
        CheckConstraint("technical_attempt_number > 0", name="ck_series_episode_attempts_number"),
        CheckConstraint("state in ('RESERVED','QUALIFIED','ADMITTED','ABANDONED','PUBLISHED')", name="ck_series_episode_attempts_state"),
        CheckConstraint("identity_hash ~ '^[0-9a-f]{64}$'", name="ck_series_episode_attempts_hash"),
        Index("ix_series_episode_attempts_project", "video_project_id"),
        Index("ix_series_episode_attempts_arc", "series_arc_version_id"),
    )


class SeriesPublicOrdinalAuthority(Base):
    """Public ordinal is allocated only after a verified PUBLIC receipt."""

    __tablename__ = "series_public_ordinal_authorities"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    series_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_plans.id"), nullable=False)
    series_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_runs.id"), nullable=False)
    series_arc_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_arc_versions.id"), nullable=False)
    episode_attempt_authority_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_episode_attempt_authorities.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    public_publication_receipt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("public_publication_receipts.id"), nullable=False)
    public_episode_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    authority_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("series_plan_id", "public_episode_ordinal", name="uq_series_public_ordinals_plan_ordinal"),
        UniqueConstraint("video_project_id", name="uq_series_public_ordinals_project"),
        UniqueConstraint("public_publication_receipt_id", name="uq_series_public_ordinals_receipt"),
        UniqueConstraint("episode_attempt_authority_id", name="uq_series_public_ordinals_attempt"),
        CheckConstraint("public_episode_ordinal > 0", name="ck_series_public_ordinals_positive"),
        CheckConstraint("authority_hash ~ '^[0-9a-f]{64}$'", name="ck_series_public_ordinals_hash"),
        Index("ix_series_public_ordinals_plan", "series_plan_id"),
    )


class SeriesArcDecisionAuthority(Base):
    __tablename__ = "series_arc_decision_authorities"

    id: Mapped[uuid.UUID] = uuid_pk()
    series_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_plans.id"), nullable=False)
    source_arc_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("series_arc_versions.id"), nullable=False)
    target_arc_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("series_arc_versions.id"))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_public_episode_count: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    decided_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("decision_hash", name="uq_series_arc_decisions_hash"),
        CheckConstraint("action in ('EARLY_COMPLETION','EXTENSION','COMPLETION')", name="ck_series_arc_decisions_action"),
        CheckConstraint("effective_public_episode_count is null or effective_public_episode_count > 0", name="ck_series_arc_decisions_count"),
        CheckConstraint("decision_hash ~ '^[0-9a-f]{64}$'", name="ck_series_arc_decisions_hash"),
        Index("ix_series_arc_decisions_plan", "series_plan_id"),
    )


class LearningSystemPromotionReceipt(Base):
    __tablename__ = "learning_system_promotion_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("learning_candidates.id"), nullable=False)
    eligibility_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("learning_promotion_eligibility_runs.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    equivalence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    distinct_mature_source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    policy_version: Mapped[str] = mapped_column(String(120), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("learning_candidate_id", name="uq_learning_system_promotion_candidate"),
        UniqueConstraint("receipt_hash", name="uq_learning_system_promotion_hash"),
        CheckConstraint("distinct_mature_source_count >= 0", name="ck_learning_system_promotion_count"),
        CheckConstraint("result in ('PROMOTED','EVIDENCE_ONLY','BLOCKED')", name="ck_learning_system_promotion_result"),
        CheckConstraint("equivalence_fingerprint ~ '^[0-9a-f]{64}$' and policy_hash ~ '^[0-9a-f]{64}$' and receipt_hash ~ '^[0-9a-f]{64}$'", name="ck_learning_system_promotion_hashes"),
        Index("ix_learning_system_promotion_fingerprint", "channel_workspace_id", "equivalence_fingerprint"),
    )


class LearningReviewCommand(Base):
    """Exactly-once human review command identity around legacy M11 decisions."""

    __tablename__ = "learning_review_commands"

    id: Mapped[uuid.UUID] = uuid_pk()
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("learning_candidates.id"), nullable=False)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(80), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    learning_review_decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("learning_review_decisions.id"))
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="INTENDED")
    created_at: Mapped[datetime] = utc_created_at()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("learning_candidate_id", name="uq_learning_review_commands_candidate"),
        UniqueConstraint("command_id", name="uq_learning_review_commands_command"),
        UniqueConstraint("decision_hash", name="uq_learning_review_commands_hash"),
        CheckConstraint("state in ('INTENDED','COMPLETED','REJECTED')", name="ck_learning_review_commands_state"),
        CheckConstraint("decision_hash ~ '^[0-9a-f]{64}$'", name="ck_learning_review_commands_hash"),
    )


class PaymentProfileStatus(Base):
    __tablename__ = "payment_profile_statuses"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    payee_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_state: Mapped[str] = mapped_column(String(32), nullable=False)
    address_verification_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_method_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_hold_state: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("company_id", "payee_ref", "source_updated_at", name="uq_payment_profile_status_snapshot"),
        CheckConstraint("source_type in ('API','IMPORT','OPERATOR_ATTESTATION')", name="ck_payment_profile_status_source"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_payment_profile_status_hash"),
        Index("ix_payment_profile_status_company", "company_id", "source_updated_at"),
    )


class MonetizationAccountStatus(Base):
    __tablename__ = "monetization_account_statuses"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    destination_ref: Mapped[str] = mapped_column(Text, nullable=False)
    program_type: Mapped[str] = mapped_column(String(80), nullable=False)
    eligibility_state: Mapped[str] = mapped_column(String(40), nullable=False)
    enrollment_state: Mapped[str] = mapped_column(String(40), nullable=False)
    restriction_state: Mapped[str] = mapped_column(String(40), nullable=False)
    country_eligibility_state: Mapped[str] = mapped_column(String(40), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("channel_workspace_id", "platform", "program_type", "source_updated_at", name="uq_monetization_status_snapshot"),
        CheckConstraint("source_type in ('API','IMPORT','OPERATOR_ATTESTATION')", name="ck_monetization_status_source"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_monetization_status_hash"),
        Index("ix_monetization_status_channel", "channel_workspace_id", "source_updated_at"),
    )


class RevenueSnapshot(Base):
    __tablename__ = "revenue_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    estimated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    finalized_or_locked_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    reversed_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    cash_received_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    cash_receivable_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("channel_workspace_id", "source", "period_start", "period_end", "source_updated_at", name="uq_revenue_snapshot_source_period"),
        CheckConstraint("period_end > period_start", name="ck_revenue_snapshot_period"),
        CheckConstraint("estimated_amount >= 0 and finalized_or_locked_amount >= 0 and reversed_amount >= 0 and cash_received_amount >= 0 and cash_receivable_amount >= 0", name="ck_revenue_snapshot_amounts"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_revenue_snapshot_hash"),
        Index("ix_revenue_snapshot_channel_period", "channel_workspace_id", "period_end"),
    )


class ChannelPnlSnapshot(Base):
    __tablename__ = "channel_pnl_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    direct_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    shared_cost_allocated: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    estimated_revenue: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    finalized_revenue: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    cash_received: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    contribution_margin: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    burn_rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("channel_workspace_id", "period_start", "period_end", name="uq_channel_pnl_period"),
        CheckConstraint("period_end > period_start and direct_cost >= 0 and shared_cost_allocated >= 0 and estimated_revenue >= 0 and finalized_revenue >= 0 and cash_received >= 0 and burn_rate >= 0", name="ck_channel_pnl_values"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_channel_pnl_hash"),
        Index("ix_channel_pnl_channel_period", "channel_workspace_id", "period_end"),
    )


class PlatformEnforcementIncident(Base):
    __tablename__ = "platform_enforcement_incidents"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"))
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    incident_type: Mapped[str] = mapped_column(String(80), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    source_status: Mapped[str] = mapped_column(Text, nullable=False)
    freeze_learning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_platform_enforcement_incident_hash"),
        CheckConstraint("scope in ('VIDEO','CHANNEL','ACCOUNT')", name="ck_platform_enforcement_scope"),
        CheckConstraint("severity in ('INFO','LOW','MEDIUM','HIGH','CRITICAL')", name="ck_platform_enforcement_severity"),
        CheckConstraint("state in ('OPEN','UNDER_REVIEW','APPEAL_READY','SUBMITTED','RESOLVED','DISMISSED')", name="ck_platform_enforcement_state"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_platform_enforcement_hash"),
        Index("ix_platform_enforcement_channel_state", "channel_workspace_id", "state"),
        Index("ix_platform_enforcement_video", "uploaded_video_id"),
    )


class AppealEvidencePack(Base):
    __tablename__ = "appeal_evidence_packs"

    id: Mapped[uuid.UUID] = uuid_pk()
    platform_enforcement_incident_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_enforcement_incidents.id"), nullable=False)
    rights_basis: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    internal_reviewer_ref: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_summary: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("platform_enforcement_incident_id", "content_hash", name="uq_appeal_evidence_pack_version"),
        CheckConstraint("state in ('DRAFT','READY_FOR_HUMAN','SUBMITTED_BY_HUMAN','RESOLVED')", name="ck_appeal_evidence_pack_state"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_appeal_evidence_pack_hash"),
    )


class AffiliateOfferSnapshot(Base):
    __tablename__ = "affiliate_offer_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    merchant: Mapped[str] = mapped_column(String(200), nullable=False)
    offer_ref: Mapped[str] = mapped_column(Text, nullable=False)
    product_ref: Mapped[str | None] = mapped_column(Text)
    commission_model: Mapped[str] = mapped_column(Text, nullable=False)
    attribution_window_text: Mapped[str] = mapped_column(Text, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disclosure_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_affiliate_offer_snapshot_hash"),
        CheckConstraint("state in ('ACTIVE','EXPIRED','SUSPENDED')", name="ck_affiliate_offer_snapshot_state"),
        CheckConstraint("terms_hash ~ '^[0-9a-f]{64}$' and content_hash ~ '^[0-9a-f]{64}$'", name="ck_affiliate_offer_snapshot_hashes"),
        Index("ix_affiliate_offer_snapshot_merchant", "merchant", "effective_at"),
    )


class AffiliateLinkRegistry(Base):
    __tablename__ = "affiliate_link_registry"

    id: Mapped[uuid.UUID] = uuid_pk()
    affiliate_offer_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("affiliate_offer_snapshots.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    destination_url: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_url: Mapped[str | None] = mapped_column(Text)
    utm_template_version: Mapped[str] = mapped_column(String(80), nullable=False)
    disclosure_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    active_state: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")
    last_health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("channel_workspace_id", "destination_url", "affiliate_offer_snapshot_id", name="uq_affiliate_link_registry_target"),
        CheckConstraint("active_state in ('ACTIVE','BROKEN','EXPIRED','DISABLED')", name="ck_affiliate_link_registry_state"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_affiliate_link_registry_hash"),
        Index("ix_affiliate_link_registry_channel_state", "channel_workspace_id", "active_state"),
    )


def _immutable_authority(_mapper: Mapper[Any], _connection: Any, target: Any) -> None:
    raise RuntimeError(f"{target.__class__.__name__.upper()}_IMMUTABLE")


for _model in (
    SeriesPublicOrdinalAuthority,
    LearningSystemPromotionReceipt,
    PaymentProfileStatus,
    MonetizationAccountStatus,
    RevenueSnapshot,
    ChannelPnlSnapshot,
    AffiliateOfferSnapshot,
):
    event.listen(_model, "before_update", _immutable_authority)
    event.listen(_model, "before_delete", _immutable_authority)
