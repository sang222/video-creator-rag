import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, uuid_pk


class AgentOutputValidationRun(Base):
    __tablename__ = "agent_output_validation_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    prompt_render_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_render_runs.id"))
    agent_context_pack_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_context_pack_snapshots.id")
    )
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(160), nullable=False)
    output_type: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    validation_state: Mapped[str] = mapped_column(String(80), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    applied_context_refs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    raw_output_ref: Mapped[str | None] = mapped_column(Text)
    raw_output_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_artifact_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    validation_result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_agent_output_validation_runs_package", "package_id"),
        Index("ix_agent_output_validation_runs_project", "video_project_id"),
        Index("ix_agent_output_validation_runs_prompt_render", "prompt_render_run_id"),
        Index("ix_agent_output_validation_runs_context_pack", "agent_context_pack_snapshot_id"),
        Index("ix_agent_output_validation_runs_agent", "agent_key"),
        Index("ix_agent_output_validation_runs_status", "status"),
        Index("ix_agent_output_validation_runs_created_at", "created_at"),
    )


class SchemaViolationLedger(Base):
    __tablename__ = "schema_violation_ledger"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    prompt_render_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_render_runs.id"))
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    artifact_ref: Mapped[str | None] = mapped_column(Text)
    violation_type: Mapped[str] = mapped_column(String(120), nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    invalid_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    repair_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    repair_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_schema_violation_ledger_package", "package_id"),
        Index("ix_schema_violation_ledger_project", "video_project_id"),
        Index("ix_schema_violation_ledger_agent", "agent_key"),
        Index("ix_schema_violation_ledger_type", "violation_type"),
        Index("ix_schema_violation_ledger_created_at", "created_at"),
    )


class R3D4GateBatchRun(Base):
    __tablename__ = "r3d4_gate_batch_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    effective_context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("effective_channel_runtime_context_snapshots.id")
    )
    context_hash: Mapped[str | None] = mapped_column(Text)
    trigger_agent_key: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    hard_block_count: Mapped[int] = mapped_column(nullable=False, default=0)
    review_required_count: Mapped[int] = mapped_column(nullable=False, default=0)
    gate_results_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    reducer_decision_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_r3d4_gate_batch_runs_package", "package_id"),
        Index("ix_r3d4_gate_batch_runs_project", "video_project_id"),
        Index("ix_r3d4_gate_batch_runs_effective_context", "effective_context_snapshot_id"),
        Index("ix_r3d4_gate_batch_runs_status", "status"),
        Index("ix_r3d4_gate_batch_runs_created_at", "created_at"),
    )


class R3D4GateRun(Base):
    __tablename__ = "r3d4_gate_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    gate_batch_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("r3d4_gate_batch_runs.id"))
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    effective_context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("effective_channel_runtime_context_snapshots.id")
    )
    gate_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    measurements_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    fail_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    blocking_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    checked_artifact_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    checked_contract_paths: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    repair_hint: Mapped[str | None] = mapped_column(Text)
    human_readable_summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_r3d4_gate_runs_batch", "gate_batch_run_id"),
        Index("ix_r3d4_gate_runs_package", "package_id"),
        Index("ix_r3d4_gate_runs_project", "video_project_id"),
        Index("ix_r3d4_gate_runs_effective_context", "effective_context_snapshot_id"),
        Index("ix_r3d4_gate_runs_gate", "gate_key"),
        Index("ix_r3d4_gate_runs_status", "status"),
        Index("ix_r3d4_gate_runs_created_at", "created_at"),
    )
