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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class OpenAIPricingSnapshot(Base):
    __tablename__ = "openai_pricing_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    snapshot_version: Mapped[str] = mapped_column(
        String(160), nullable=False, unique=True
    )
    provider_key: Mapped[str] = mapped_column(
        String(80), nullable=False, default="OPENAI"
    )
    service_tier: Mapped[str] = mapped_column(
        String(40), nullable=False, default="standard"
    )
    pricing_blob: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    evidence_ref: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "provider_key = 'OPENAI'", name="ck_openai_pricing_snapshots_provider"
        ),
        CheckConstraint(
            "service_tier = 'standard'", name="ck_openai_pricing_snapshots_tier"
        ),
        CheckConstraint(
            "status in ('DRAFT','APPROVED','SUPERSEDED')",
            name="ck_openai_pricing_snapshots_status",
        ),
    )


class OpenAICutoverReceipt(Base):
    __tablename__ = "openai_cutover_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_registry_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_registry_entries.id"), nullable=False
    )
    pricing_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("openai_pricing_snapshots.id"), nullable=False
    )
    budget_policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_policies.id"), nullable=False
    )
    credential_reference_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credential_references.id"), nullable=False
    )
    lane_mapping_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_openai_cutover_receipts_status", "status"),
        CheckConstraint(
            "status in ('DRAFT','READY','CANARY_PASSED','BLOCKED')",
            name="ck_openai_cutover_receipts_status",
        ),
    )


class OpenAICanaryArtifact(Base):
    __tablename__ = "openai_canary_artifacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    cutover_receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("openai_cutover_receipts.id"), nullable=False
    )
    artifact_key: Mapped[str] = mapped_column(String(160), nullable=False)
    lane_name: Mapped[str] = mapped_column(String(160), nullable=False)
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(40), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    llm_route_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_route_attempts.id")
    )
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    repair_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "cutover_receipt_id",
            "artifact_key",
            name="uq_openai_canary_receipt_artifact",
        ),
        Index("ix_openai_canary_artifacts_status", "status"),
        CheckConstraint(
            "model_id in ('gpt-5.6-luna','gpt-5.6-terra')",
            name="ck_openai_canary_artifacts_model",
        ),
        CheckConstraint(
            "reasoning_effort in ('none','low','medium','high')",
            name="ck_openai_canary_artifacts_reasoning",
        ),
        CheckConstraint(
            "status in ('PENDING','SUCCESS','FAILED','SKIPPED')",
            name="ck_openai_canary_artifacts_status",
        ),
    )
