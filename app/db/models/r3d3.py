import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, uuid_pk


class AgentContextPackSnapshot(Base):
    __tablename__ = "agent_context_pack_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    prompt_render_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_render_runs.id"))
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    task_type: Mapped[str | None] = mapped_column(String(160))
    lane: Mapped[str] = mapped_column(String(160), nullable=False)
    context_pack_version: Mapped[str] = mapped_column(String(80), nullable=False)
    builder_version: Mapped[str] = mapped_column(String(80), nullable=False)
    agent_context_contract_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    effective_context_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("effective_channel_runtime_context_snapshots.id"), nullable=False
    )
    effective_context_hash: Mapped[str] = mapped_column(Text, nullable=False)
    channel_contract_hash: Mapped[str | None] = mapped_column(Text)
    compiled_policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id")
    )
    compiled_policy_snapshot_hash: Mapped[str | None] = mapped_column(Text)
    context_pack_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_context_hash: Mapped[str | None] = mapped_column(String(128))
    artifact_digest_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_digest_hash: Mapped[str | None] = mapped_column(String(128))
    common_skill_digest_hash: Mapped[str | None] = mapped_column(String(128))
    runtime_guard_digest_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    budget_report_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    omitted_items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    largest_context_contributors_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    agent_context_contract_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    context_pack_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    shape_gate_result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_agent_context_pack_snapshots_package", "package_id"),
        Index("ix_agent_context_pack_snapshots_project", "video_project_id"),
        Index("ix_agent_context_pack_snapshots_prompt_render", "prompt_render_run_id"),
        Index("ix_agent_context_pack_snapshots_agent", "agent_key"),
        Index("ix_agent_context_pack_snapshots_effective_context", "effective_context_snapshot_id"),
        Index("ix_agent_context_pack_snapshots_context_hash", "context_pack_hash"),
        Index("ix_agent_context_pack_snapshots_prompt_context_hash", "prompt_context_hash"),
        Index("ix_agent_context_pack_snapshots_created_at", "created_at"),
    )
