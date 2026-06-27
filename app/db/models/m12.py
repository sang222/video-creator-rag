import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, uuid_pk


class ProviderReadinessCheck(Base):
    __tablename__ = "provider_readiness_checks"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(80), nullable=False)
    check_type: Mapped[str] = mapped_column(String(40), nullable=False)
    check_state: Mapped[str] = mapped_column(String(40), nullable=False)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_provider_readiness_checks_provider", "provider_key"),
        Index("ix_provider_readiness_checks_type_state", "check_type", "check_state"),
        Index("ix_provider_readiness_checks_created_at", "created_at"),
    )


class ProviderReadinessSnapshot(Base):
    __tablename__ = "provider_readiness_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    snapshot_state: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_summaries: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    blocking_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    warning_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    next_actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_provider_readiness_snapshots_state", "snapshot_state"),
        Index("ix_provider_readiness_snapshots_created_at", "created_at"),
    )


class RealSmokeRun(Base):
    __tablename__ = "real_smoke_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    smoke_type: Mapped[str] = mapped_column(String(80), nullable=False)
    run_state: Mapped[str] = mapped_column(String(40), nullable=False)
    env_flags: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    result_summary: Mapped[str | None] = mapped_column(Text)
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_real_smoke_runs_provider", "provider_key"),
        Index("ix_real_smoke_runs_state", "run_state"),
        Index("ix_real_smoke_runs_created_at", "created_at"),
    )
