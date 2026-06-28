import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, uuid_pk


class FirstScriptedVideoPackage(Base):
    __tablename__ = "first_scripted_video_packages"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    channel_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    channel_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_profile_versions.id"))
    compiled_policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"))
    provider_readiness_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("provider_readiness_snapshots.id"))
    package_status: Mapped[str] = mapped_column(String(40), nullable=False)
    agent_run_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    prompt_render_run_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    prompt_audit_snapshot_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    limitations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    risk_limitations_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    next_action: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_first_scripted_video_packages_channel", "channel_id"),
        Index("ix_first_scripted_video_packages_project", "video_project_id"),
        Index("ix_first_scripted_video_packages_status", "package_status"),
        Index("ix_first_scripted_video_packages_created_at", "created_at"),
    )


class VideoGenerationBoundary(Base):
    __tablename__ = "video_generation_boundaries"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("first_scripted_video_packages.id"), nullable=False)
    channel_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    required_inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    required_providers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    provider_readiness: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    boundary_status: Mapped[str] = mapped_column(String(60), nullable=False)
    blocked_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str] = mapped_column(Text, nullable=False)
    operator_summary_vi: Mapped[str] = mapped_column(Text, nullable=False)
    no_provider_calls_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_video_generation_boundaries_package", "package_id"),
        Index("ix_video_generation_boundaries_channel", "channel_id"),
        Index("ix_video_generation_boundaries_status", "boundary_status"),
        Index("ix_video_generation_boundaries_created_at", "created_at"),
    )
