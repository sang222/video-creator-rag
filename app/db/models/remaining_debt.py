"""Durable authority models for the post-freeze VCOS debt closeout.

The tables in this module deliberately keep execution truth append-only and
channel scoped.  They do not perform provider calls and they do not treat live
canaries as complete merely because the software surface exists.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _uuid_indexed(*, nullable: bool = False) -> Mapped[uuid.UUID | None]:
    return mapped_column(PGUUID(as_uuid=True), nullable=nullable, index=True)


def _hash_column() -> Mapped[str]:
    return mapped_column(String(64), nullable=False, index=True)


def _json_dict() -> Mapped[dict[str, Any]]:
    return mapped_column(JSONB, nullable=False, default=dict)


def _json_list() -> Mapped[list[Any]]:
    return mapped_column(JSONB, nullable=False, default=list)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


# ---------------------------------------------------------------------------
# D15 — series authority
# ---------------------------------------------------------------------------


class SeriesArcVersion(Base):
    __tablename__ = "series_arc_versions"
    __table_args__ = (
        UniqueConstraint(
            "series_plan_id", "version_number", name="uq_series_arc_version_number"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    series_plan_id: Mapped[uuid.UUID] = _uuid_indexed()
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_version_id: Mapped[uuid.UUID | None] = _uuid_indexed(nullable=True)
    arc_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    planned_episode_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    premise: Mapped[str] = mapped_column(Text, nullable=False)
    coverage_policy: Mapped[dict[str, Any]] = _json_dict()
    approved_by: Mapped[uuid.UUID | None] = _uuid_indexed(nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class SeriesEpisodeBlueprint(Base):
    __tablename__ = "series_episode_blueprints"
    __table_args__ = (
        UniqueConstraint(
            "series_arc_version_id", "blueprint_key", name="uq_series_blueprint_key"
        ),
        UniqueConstraint(
            "series_arc_version_id",
            "planned_position",
            name="uq_series_blueprint_position",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    series_plan_id: Mapped[uuid.UUID] = _uuid_indexed()
    series_arc_version_id: Mapped[uuid.UUID] = _uuid_indexed()
    blueprint_key: Mapped[str] = mapped_column(String(160), nullable=False)
    planned_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    editorial_contract: Mapped[dict[str, Any]] = _json_dict()
    coverage_tags: Mapped[list[Any]] = _json_list()
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    video_project_id: Mapped[uuid.UUID | None] = _uuid_indexed(nullable=True)
    technical_attempt_ref: Mapped[str | None] = mapped_column(
        String(240), nullable=True
    )
    publication_receipt_id: Mapped[uuid.UUID | None] = _uuid_indexed(nullable=True)
    public_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class SeriesLifecycleDecision(Base):
    __tablename__ = "series_lifecycle_decisions"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_series_lifecycle_command"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    series_plan_id: Mapped[uuid.UUID] = _uuid_indexed()
    series_arc_version_id: Mapped[uuid.UUID] = _uuid_indexed()
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)
    command_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    previous_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resulting_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_refs: Mapped[list[Any]] = _json_list()
    decision_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()


class SeriesPublicOrdinal(Base):
    __tablename__ = "series_public_ordinals"
    __table_args__ = (
        UniqueConstraint(
            "series_plan_id", "public_ordinal", name="uq_series_public_ordinal"
        ),
        UniqueConstraint(
            "series_plan_id",
            "publication_receipt_id",
            name="uq_series_public_receipt",
        ),
        UniqueConstraint(
            "series_plan_id", "video_project_id", name="uq_series_public_project"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    series_plan_id: Mapped[uuid.UUID] = _uuid_indexed()
    series_arc_version_id: Mapped[uuid.UUID] = _uuid_indexed()
    episode_blueprint_id: Mapped[uuid.UUID] = _uuid_indexed()
    video_project_id: Mapped[uuid.UUID] = _uuid_indexed()
    publication_receipt_id: Mapped[uuid.UUID] = _uuid_indexed()
    public_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    playlist_position: Mapped[int] = mapped_column(Integer, nullable=False)
    technical_attempt_ref: Mapped[str | None] = mapped_column(
        String(240), nullable=True
    )
    identity_hash: Mapped[str] = _hash_column()
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# P1 — analytics / learning authority
# ---------------------------------------------------------------------------


class AnalyticsEvidenceWindow(Base):
    __tablename__ = "analytics_evidence_windows"
    __table_args__ = (
        UniqueConstraint(
            "uploaded_video_id",
            "window_key",
            "source_version",
            name="uq_analytics_evidence_window",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    uploaded_video_id: Mapped[uuid.UUID] = _uuid_indexed()
    window_key: Mapped[str] = mapped_column(String(16), nullable=False)
    source_version: Mapped[str] = mapped_column(String(80), nullable=False)
    maturity_state: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence_state: Mapped[str] = mapped_column(String(24), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_snapshot_refs: Mapped[list[Any]] = _json_list()
    evidence_payload: Mapped[dict[str, Any]] = _json_dict()
    evidence_hash: Mapped[str] = _hash_column()
    matured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()


class LearningEquivalenceFingerprint(Base):
    __tablename__ = "learning_equivalence_fingerprints"
    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "source_entity_ref",
            name="uq_learning_fingerprint_source",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    source_entity_ref: Mapped[str] = mapped_column(String(260), nullable=False)
    content_product_type: Mapped[str] = mapped_column(String(80), nullable=False)
    series_plan_id: Mapped[uuid.UUID | None] = _uuid_indexed(nullable=True)
    profile_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_market: Mapped[str] = mapped_column(String(32), nullable=False)
    content_language: Mapped[str] = mapped_column(String(24), nullable=False)
    format_key: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_features: Mapped[dict[str, Any]] = _json_dict()
    fingerprint: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()


class LearningReview(Base):
    __tablename__ = "learning_reviews"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_learning_review_command"),
        UniqueConstraint(
            "fingerprint_id",
            "window_key",
            "evidence_hash",
            name="uq_learning_review_evidence",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    fingerprint_id: Mapped[uuid.UUID] = _uuid_indexed()
    analytics_evidence_window_id: Mapped[uuid.UUID] = _uuid_indexed()
    window_key: Mapped[str] = mapped_column(String(16), nullable=False)
    command_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    current_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    comparable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decision: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    reason_codes: Mapped[list[Any]] = _json_list()
    audit_trail: Mapped[list[Any]] = _json_list()
    evidence_hash: Mapped[str] = _hash_column()
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = _uuid_indexed(nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class AudienceDeliveryPlan(Base):
    __tablename__ = "audience_delivery_plans"
    __table_args__ = (
        UniqueConstraint(
            "publication_receipt_id", name="uq_audience_delivery_publication"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    video_project_id: Mapped[uuid.UUID] = _uuid_indexed()
    publication_receipt_id: Mapped[uuid.UUID] = _uuid_indexed()
    target_markets: Mapped[list[Any]] = _json_list()
    target_languages: Mapped[list[Any]] = _json_list()
    packaging_refs: Mapped[list[Any]] = _json_list()
    playlist_refs: Mapped[list[Any]] = _json_list()
    state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    plan_hash: Mapped[str] = _hash_column()
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class LearningOperationalIncident(Base):
    __tablename__ = "learning_operational_incidents"
    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "incident_type",
            "external_ref",
            name="uq_learning_operational_incident",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    video_project_id: Mapped[uuid.UUID | None] = _uuid_indexed(nullable=True)
    incident_type: Mapped[str] = mapped_column(String(48), nullable=False)
    external_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    blocks_learning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    evidence_payload: Mapped[dict[str, Any]] = _json_dict()
    content_hash: Mapped[str] = _hash_column()
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# P2 — media business operating state
# ---------------------------------------------------------------------------


class PaymentProfileStatus(Base):
    __tablename__ = "payment_profile_statuses"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "version_number", name="uq_payment_profile_version"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    payee_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_state: Mapped[str] = mapped_column(String(32), nullable=False)
    address_verification_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_method_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_hold_state: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(260), nullable=False)
    confidence_state: Mapped[str] = mapped_column(String(24), nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()


class MonetizationAccountStatus(Base):
    __tablename__ = "monetization_account_statuses"
    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "platform",
            "version_number",
            name="uq_monetization_account_version",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    program_type: Mapped[str] = mapped_column(String(80), nullable=False)
    eligibility_state: Mapped[str] = mapped_column(String(32), nullable=False)
    enrollment_state: Mapped[str] = mapped_column(String(32), nullable=False)
    restriction_state: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(260), nullable=False)
    confidence_state: Mapped[str] = mapped_column(String(24), nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()


class RevenueSnapshot(Base):
    __tablename__ = "revenue_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "source",
            "period_start",
            "period_end",
            "amount_state",
            "source_ref",
            name="uq_revenue_snapshot_source",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    video_project_id: Mapped[uuid.UUID | None] = _uuid_indexed(nullable=True)
    source: Mapped[str] = mapped_column(String(48), nullable=False)
    amount_state: Mapped[str] = mapped_column(String(24), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_ref: Mapped[str] = mapped_column(String(260), nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    confidence_state: Mapped[str] = mapped_column(String(24), nullable=False)
    content_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()


class ChannelPnlSnapshot(Base):
    __tablename__ = "channel_pnl_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "period_start",
            "period_end",
            "calculation_version",
            name="uq_channel_pnl_window",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    estimated_revenue: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    locked_revenue: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    finalized_revenue: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    cash_received: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    reversed_revenue: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    direct_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    allocated_ops_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    contribution_margin: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    calculation_version: Mapped[str] = mapped_column(String(48), nullable=False)
    source_snapshot_refs: Mapped[list[Any]] = _json_list()
    content_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()


class SelfFundingAssessment(Base):
    __tablename__ = "self_funding_assessments"
    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "assessment_window_end",
            "policy_version",
            name="uq_self_funding_assessment",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    assessment_window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    policy_version: Mapped[str] = mapped_column(String(48), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    reason_codes: Mapped[list[Any]] = _json_list()
    input_refs: Mapped[list[Any]] = _json_list()
    assessment_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()


class ContinuationCapitalReview(Base):
    """Frozen, human-governed continuation/capital recommendation authority."""

    __tablename__ = "continuation_capital_reviews"
    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "evidence_snapshot_hash",
            name="uq_continuation_capital_evidence",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    recommendation: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    reason_codes: Mapped[list[Any]] = _json_list()
    input_refs: Mapped[list[Any]] = _json_list()
    evidence_snapshot_hash: Mapped[str] = _hash_column()
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    human_decision_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class PlatformEnforcementIncident(Base):
    __tablename__ = "platform_enforcement_incidents"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "external_incident_ref",
            name="uq_platform_enforcement_external_ref",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    uploaded_video_id: Mapped[uuid.UUID | None] = _uuid_indexed(nullable=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    external_incident_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    incident_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    freeze_learning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evidence_payload: Mapped[dict[str, Any]] = _json_dict()
    source_ref: Mapped[str] = mapped_column(String(260), nullable=False)
    incident_hash: Mapped[str] = _hash_column()
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class AppealEvidencePack(Base):
    __tablename__ = "appeal_evidence_packs"
    __table_args__ = (
        UniqueConstraint(
            "platform_enforcement_incident_id",
            "version_number",
            name="uq_appeal_evidence_pack_version",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    platform_enforcement_incident_id: Mapped[uuid.UUID] = _uuid_indexed()
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rights_basis: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_items: Mapped[list[Any]] = _json_list()
    timeline: Mapped[list[Any]] = _json_list()
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = _uuid_indexed(nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pack_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()


class AffiliateOfferSnapshot(Base):
    __tablename__ = "affiliate_offer_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "merchant",
            "offer_ref",
            "terms_hash",
            name="uq_affiliate_offer_terms",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    merchant: Mapped[str] = mapped_column(String(160), nullable=False)
    offer_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    product_ref: Mapped[str | None] = mapped_column(String(240), nullable=True)
    commission_model: Mapped[dict[str, Any]] = _json_dict()
    attribution_window_text: Mapped[str] = mapped_column(Text, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    disclosure_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    snapshot_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()


class AffiliateLinkRegistry(Base):
    __tablename__ = "affiliate_link_registry"
    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id", "canonical_url", name="uq_affiliate_link_url"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    affiliate_offer_snapshot_id: Mapped[uuid.UUID] = _uuid_indexed()
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    short_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    utm_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    disclosure_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    last_health_state: Mapped[str] = mapped_column(String(24), nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class BusinessDisclosureAssessment(Base):
    __tablename__ = "business_disclosure_assessments"
    __table_args__ = (
        UniqueConstraint(
            "publish_package_ref",
            "policy_version",
            name="uq_business_disclosure_package",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID] = _uuid_indexed()
    video_project_id: Mapped[uuid.UUID] = _uuid_indexed()
    publish_package_ref: Mapped[str] = mapped_column(String(260), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    required_disclosures: Mapped[list[Any]] = _json_list()
    observed_disclosures: Mapped[list[Any]] = _json_list()
    link_registry_refs: Mapped[list[Any]] = _json_list()
    decision: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    reason_codes: Mapped[list[Any]] = _json_list()
    assessment_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()


class BusinessActionItem(Base):
    __tablename__ = "business_action_items"
    __table_args__ = (
        UniqueConstraint(
            "channel_workspace_id",
            "action_type",
            "target_ref",
            "reason_code",
            name="uq_business_action_identity",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = _uuid_indexed()
    channel_workspace_id: Mapped[uuid.UUID | None] = _uuid_indexed(nullable=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(260), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    assignee_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evidence_refs: Mapped[list[Any]] = _json_list()
    action_hash: Mapped[str] = _hash_column()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
