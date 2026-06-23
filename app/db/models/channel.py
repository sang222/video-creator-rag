import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class ChannelWorkspace(Base):
    __tablename__ = "channel_workspaces"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    primary_language: Mapped[str] = mapped_column(Text, nullable=False, default="en")
    target_market: Mapped[str | None] = mapped_column(Text)
    default_timezone: Mapped[str] = mapped_column(Text, nullable=False, default="UTC")
    active_policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("company_id", "key"),
        Index("ix_channel_workspaces_company_id", "company_id"),
        Index("ix_channel_workspaces_created_at", "created_at"),
    )


class ChannelMembership(Base):
    __tablename__ = "channel_memberships"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("channel_workspace_id", "user_id", "role_id"),
        Index("ix_channel_memberships_channel_workspace_id", "channel_workspace_id"),
        Index("ix_channel_memberships_user_id", "user_id"),
    )


class ChannelProfileVersion(Base):
    __tablename__ = "channel_profile_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    profile_input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    profile_input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_template_key: Mapped[str | None] = mapped_column(Text)
    source_template_version: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("channel_workspace_id", "version"),
        Index("ix_channel_profile_versions_channel_workspace_id", "channel_workspace_id"),
        Index("ix_channel_profile_versions_created_at", "created_at"),
    )


class ChannelProfileCompileRun(Base):
    __tablename__ = "channel_profile_compile_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_profile_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id"), nullable=False
    )
    compiler_version: Mapped[str] = mapped_column(Text, nullable=False)
    capability_matrix_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    output_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_channel_profile_compile_runs_channel_profile_version_id", "channel_profile_version_id"),
        Index("ix_channel_profile_compile_runs_correlation_id", "correlation_id"),
        Index("ix_channel_profile_compile_runs_created_at", "created_at"),
    )


class CompiledChannelPolicySnapshot(Base):
    __tablename__ = "compiled_channel_policy_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    channel_profile_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id"), nullable=False
    )
    compile_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_compile_runs.id")
    )
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="compiled")
    compiler_version: Mapped[str] = mapped_column(Text, nullable=False)
    capability_matrix_version: Mapped[str] = mapped_column(Text, nullable=False)
    compiled_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    profile_input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("channel_workspace_id", "snapshot_version"),
        UniqueConstraint(
            "channel_profile_version_id",
            "compiler_version",
            "capability_matrix_version",
            "profile_input_hash",
            "content_hash",
            name="uq_policy_snapshot_profile_compile_identity",
        ),
        Index("ix_compiled_channel_policy_snapshots_channel_workspace_id", "channel_workspace_id"),
        Index("ix_compiled_channel_policy_snapshots_channel_profile_version_id", "channel_profile_version_id"),
        Index("ix_compiled_channel_policy_snapshots_created_at", "created_at"),
    )
