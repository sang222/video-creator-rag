"""Durable exactly-once journal for package-authorized V2 production effects."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


V2_EFFECT_STATES = (
    "PREPARED",
    "EFFECT_STARTED",
    "VERIFIED",
    "FAILED_UNCERTAIN",
)
V2_EFFECT_STAGES = ("MEDIA", "RENDER", "QC", "ARCHIVE")


class V2ProductionEffectLedger(Base):
    """One durable logical side effect for one workflow stage command."""

    __tablename__ = "v2_production_effect_ledger"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_workflow_runs.id"),
        nullable=False,
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video_projects.id"),
        nullable=False,
    )
    production_package_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifact_versions.id"),
        nullable=False,
    )
    production_package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    command_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    adapter_key: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="PREPARED")
    effect_invocation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    result_type: Mapped[str | None] = mapped_column(String(120))
    result_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    result_ref: Mapped[str | None] = mapped_column(Text)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    result_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    authority_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    effect_journal: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "stage",
            name="uq_v2_production_effect_ledger_run_stage",
        ),
        CheckConstraint(
            "stage in (" + ",".join(f"'{stage}'" for stage in V2_EFFECT_STAGES) + ")",
            name="ck_v2_production_effect_ledger_stage",
        ),
        CheckConstraint(
            "state in (" + ",".join(f"'{state}'" for state in V2_EFFECT_STATES) + ")",
            name="ck_v2_production_effect_ledger_state",
        ),
        CheckConstraint(
            "production_package_hash ~ '^[0-9a-f]{64}$' "
            "and input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_production_effect_ledger_identity_hashes",
        ),
        CheckConstraint(
            "result_hash is null or result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_production_effect_ledger_result_hash",
        ),
        CheckConstraint(
            "effect_invocation_count between 0 and 1",
            name="ck_v2_production_effect_ledger_invocation_count",
        ),
        CheckConstraint(
            "(state = 'PREPARED' and effect_invocation_count = 0 "
            "and started_at is null and completed_at is null) or "
            "(state in ('EFFECT_STARTED','FAILED_UNCERTAIN') "
            "and effect_invocation_count = 1 and started_at is not null "
            "and completed_at is null) or "
            "(state = 'VERIFIED' and effect_invocation_count = 1 "
            "and started_at is not null and completed_at is not null "
            "and result_type is not null and result_hash is not null)",
            name="ck_v2_production_effect_ledger_state_evidence",
        ),
        CheckConstraint(
            "completed_at is null or completed_at >= started_at",
            name="ck_v2_production_effect_ledger_completion_order",
        ),
        Index(
            "ix_v2_production_effect_ledger_project",
            "video_project_id",
        ),
        Index(
            "ix_v2_production_effect_ledger_package",
            "production_package_artifact_version_id",
        ),
        Index(
            "ix_v2_production_effect_ledger_state",
            "state",
            "updated_at",
        ),
        Index(
            "ix_v2_production_effect_ledger_operation",
            "adapter_key",
            "operation_id",
        ),
    )


@event.listens_for(V2ProductionEffectLedger, "before_update")
def _verified_v2_effect_update_forbidden(
    _mapper: Mapper[V2ProductionEffectLedger],
    _connection: Any,
    target: V2ProductionEffectLedger,
) -> None:
    history = inspect(target).attrs.state.history
    previous = history.deleted[0] if history.deleted else target.state
    if previous == "VERIFIED":
        raise RuntimeError("V2_PRODUCTION_EFFECT_VERIFIED_IMMUTABLE")


@event.listens_for(V2ProductionEffectLedger, "before_delete")
def _verified_v2_effect_delete_forbidden(
    _mapper: Mapper[V2ProductionEffectLedger],
    _connection: Any,
    target: V2ProductionEffectLedger,
) -> None:
    if target.state == "VERIFIED":
        raise RuntimeError("V2_PRODUCTION_EFFECT_VERIFIED_IMMUTABLE")


__all__ = ["V2ProductionEffectLedger"]
