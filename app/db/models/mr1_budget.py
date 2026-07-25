import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class MR1MonthlyBudgetReservation(Base):
    """Durable authority for one MR1 run's monthly budget occupancy.

    Capacity is reserved once per run.  Provider allocations partition the
    same reservation for provider-cap accounting; they are not additional
    spend and therefore cannot double-count the run-level hard ceiling.
    """

    __tablename__ = "mr1_monthly_budget_reservations"

    id: Mapped[uuid.UUID] = uuid_pk()
    reservation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    reserved_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    provider_allocations_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    environment_cap: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    company_cap: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    channel_cap: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    provider_caps_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    provider_actuals_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    settlement_kind: Mapped[str | None] = mapped_column(String(40))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    capacity_evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    reason_code: Mapped[str | None] = mapped_column(String(160))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("reservation_ref", name="uq_mr1_budget_reservation_ref"),
        UniqueConstraint("run_id", name="uq_mr1_budget_reservation_run_id"),
        CheckConstraint(
            "status in ('RESERVED','SUBMITTED','SETTLED_ACTUAL','SETTLED_CONSERVATIVE','RELEASED')",
            name="status",
        ),
        CheckConstraint("reserved_amount >= 0", name="reserved_nonnegative"),
        CheckConstraint(
            "actual_amount is null or actual_amount >= 0",
            name="actual_nonnegative",
        ),
        CheckConstraint("environment_cap >= 0", name="environment_cap_nonnegative"),
        CheckConstraint("company_cap >= 0", name="company_cap_nonnegative"),
        CheckConstraint("channel_cap >= 0", name="channel_cap_nonnegative"),
        CheckConstraint("period_end > period_start", name="period_order"),
        Index("ix_mr1_budget_reservations_project", "video_project_id"),
        Index(
            "ix_mr1_budget_reservations_company_period", "company_id", "period_start"
        ),
        Index(
            "ix_mr1_budget_reservations_channel_period",
            "channel_workspace_id",
            "period_start",
        ),
        Index("ix_mr1_budget_reservations_period_status", "period_start", "status"),
        Index("ix_mr1_budget_reservations_created_at", "created_at"),
    )
