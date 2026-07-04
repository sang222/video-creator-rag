import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class RenderRevision(Base):
    __tablename__ = "render_revisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("first_scripted_video_packages.id"), nullable=False)
    effective_context_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("effective_channel_runtime_context_snapshots.id"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_status: Mapped[str] = mapped_column(String(80), nullable=False)
    source_artifact_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    gate_batch_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    render_plan_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_plan_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("package_id", "revision_no", name="uq_render_revisions_package_revision_no"),
        Index("ix_render_revisions_project", "video_project_id"),
        Index("ix_render_revisions_package", "package_id"),
        Index("ix_render_revisions_effective_context", "effective_context_snapshot_id"),
        Index("ix_render_revisions_status", "revision_status"),
        Index("ix_render_revisions_hash", "render_plan_hash"),
        Index("ix_render_revisions_created_at", "created_at"),
    )


class CostEstimateSnapshot(Base):
    __tablename__ = "cost_estimate_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    render_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("render_revisions.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("first_scripted_video_packages.id"), nullable=False)
    estimate_status: Mapped[str] = mapped_column(String(80), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False, default="USD")
    estimated_total_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    estimated_voice_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    estimated_ai_hero_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    estimated_final_render_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    estimated_pexels_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    provider_estimates_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    blocker_reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_cost_estimate_snapshots_revision", "render_revision_id"),
        Index("ix_cost_estimate_snapshots_project", "video_project_id"),
        Index("ix_cost_estimate_snapshots_package", "package_id"),
        Index("ix_cost_estimate_snapshots_status", "estimate_status"),
        Index("ix_cost_estimate_snapshots_hash", "content_hash"),
        Index("ix_cost_estimate_snapshots_created_at", "created_at"),
    )


class HumanPaidRenderApproval(Base):
    __tablename__ = "human_paid_render_approvals"

    id: Mapped[uuid.UUID] = uuid_pk()
    render_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("render_revisions.id"), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(40), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_approved_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    approved_provider_stages_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_human_paid_render_approvals_revision", "render_revision_id"),
        Index("ix_human_paid_render_approvals_status", "approval_status"),
        Index("ix_human_paid_render_approvals_created_at", "created_at"),
    )


class ProviderIdempotencyKey(Base):
    __tablename__ = "provider_idempotency_keys"

    id: Mapped[uuid.UUID] = uuid_pk()
    render_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("render_revisions.id"), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_stage: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "render_revision_id",
            "provider_key",
            "provider_stage",
            "request_fingerprint",
            name="uq_provider_idempotency_revision_provider_stage_fingerprint",
        ),
        Index("ix_provider_idempotency_keys_revision", "render_revision_id"),
        Index("ix_provider_idempotency_keys_provider", "provider_key"),
        Index("ix_provider_idempotency_keys_stage", "provider_stage"),
        Index("ix_provider_idempotency_keys_key", "idempotency_key"),
    )


class ProviderJobSnapshot(Base):
    __tablename__ = "provider_job_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    render_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("render_revisions.id"), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_stage: Mapped[str] = mapped_column(String(120), nullable=False)
    job_status: Mapped[str] = mapped_column(String(60), nullable=False)
    external_job_id: Mapped[str | None] = mapped_column(Text)
    provider_request_hash: Mapped[str | None] = mapped_column(String(128))
    provider_response_hash: Mapped[str | None] = mapped_column(String(128))
    last_error_code: Mapped[str | None] = mapped_column(String(160))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    poll_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_provider_job_snapshots_revision", "render_revision_id"),
        Index("ix_provider_job_snapshots_provider", "provider_key"),
        Index("ix_provider_job_snapshots_stage", "provider_stage"),
        Index("ix_provider_job_snapshots_status", "job_status"),
        Index("ix_provider_job_snapshots_created_at", "created_at"),
    )


class PaidProviderCallLedger(Base):
    __tablename__ = "paid_provider_call_ledger"

    id: Mapped[uuid.UUID] = uuid_pk()
    render_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("render_revisions.id"), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_stage: Mapped[str] = mapped_column(String(120), nullable=False)
    call_type: Mapped[str] = mapped_column(String(40), nullable=False)
    call_status: Mapped[str] = mapped_column(String(40), nullable=False)
    human_approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("human_paid_render_approvals.id"))
    idempotency_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("provider_idempotency_keys.id"))
    cost_estimate_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cost_estimate_snapshots.id"))
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    response_ref: Mapped[str | None] = mapped_column(Text)
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_paid_provider_call_ledger_revision", "render_revision_id"),
        Index("ix_paid_provider_call_ledger_provider", "provider_key"),
        Index("ix_paid_provider_call_ledger_stage", "provider_stage"),
        Index("ix_paid_provider_call_ledger_type", "call_type"),
        Index("ix_paid_provider_call_ledger_status", "call_status"),
        Index("ix_paid_provider_call_ledger_created_at", "created_at"),
    )


class PaidAttemptLimitRecord(Base):
    __tablename__ = "paid_attempt_limit_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    render_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("render_revisions.id"), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_stage: Mapped[str] = mapped_column(String(120), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("render_revision_id", "provider_key", "provider_stage", name="uq_paid_attempt_limit_revision_provider_stage"),
        Index("ix_paid_attempt_limit_records_revision", "render_revision_id"),
        Index("ix_paid_attempt_limit_records_provider", "provider_key"),
        Index("ix_paid_attempt_limit_records_stage", "provider_stage"),
        Index("ix_paid_attempt_limit_records_status", "status"),
    )


class ProxyPreviewArtifactFlag(Base):
    __tablename__ = "proxy_preview_artifact_flags"

    artifact_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("first_scripted_video_packages.id"), nullable=False)
    preview_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    not_final_media: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    not_publishable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_proxy_preview_artifact_flags_project", "video_project_id"),
        Index("ix_proxy_preview_artifact_flags_package", "package_id"),
        Index("ix_proxy_preview_artifact_flags_source_type", "source_type"),
        Index("ix_proxy_preview_artifact_flags_created_at", "created_at"),
    )
