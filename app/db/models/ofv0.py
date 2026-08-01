import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, uuid_pk


class FormatIdentityContract(Base):
    __tablename__ = "format_identity_contracts"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    channel_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id")
    )
    effective_context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("effective_channel_runtime_context_snapshots.id")
    )
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    character_policy_mode: Mapped[str] = mapped_column(
        String(40), nullable=False, default="NO_CHARACTER"
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("channel_id", "contract_version"),
        Index("ix_format_identity_contracts_channel", "channel_id"),
        Index("ix_format_identity_contracts_status", "status"),
        Index("ix_format_identity_contracts_context", "effective_context_snapshot_id"),
        Index("ix_format_identity_contracts_created_at", "created_at"),
    )


class EpisodeOriginalityManifest(Base):
    __tablename__ = "episode_originality_manifests"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("first_scripted_video_packages.id"),
        nullable=False,
    )
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    format_identity_contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("format_identity_contracts.id"), nullable=False
    )
    format_identity_contract_hash: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    approval_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="PENDING_HUMAN_REVIEW"
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    manifest_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("package_id"),
        Index("ix_episode_originality_manifests_channel", "channel_id"),
        Index(
            "ix_episode_originality_manifests_contract", "format_identity_contract_id"
        ),
        Index("ix_episode_originality_manifests_approval", "approval_status"),
        Index("ix_episode_originality_manifests_created_at", "created_at"),
    )


class ClaimEvidenceLedger(Base):
    __tablename__ = "claim_evidence_ledgers"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("first_scripted_video_packages.id"),
        nullable=False,
    )
    claim_id: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("package_id", "claim_id"),
        Index("ix_claim_evidence_ledgers_package", "package_id"),
        Index("ix_claim_evidence_ledgers_created_at", "created_at"),
    )


class SyntheticMediaDisclosureReceipt(Base):
    __tablename__ = "synthetic_media_disclosure_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("first_scripted_video_packages.id"),
        nullable=False,
    )
    receipt_status: Mapped[str] = mapped_column(
        String(60), nullable=False, default="PRE_RENDER_PLANNED"
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("package_id"),
        Index("ix_synthetic_media_disclosure_receipts_package", "package_id"),
        Index("ix_synthetic_media_disclosure_receipts_status", "receipt_status"),
    )


class PlatformNativePackagePlan(Base):
    __tablename__ = "platform_native_package_plans"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("first_scripted_video_packages.id"),
        nullable=False,
    )
    target_surface: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("source_package_id", "target_surface"),
        Index("ix_platform_native_package_plans_package", "source_package_id"),
        Index("ix_platform_native_package_plans_surface", "target_surface"),
    )


class OriginalityGateRun(Base):
    __tablename__ = "originality_gate_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("first_scripted_video_packages.id"),
        nullable=False,
    )
    gate_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_originality_gate_runs_package", "package_id"),
        Index("ix_originality_gate_runs_gate", "gate_key"),
        Index("ix_originality_gate_runs_status", "status"),
        Index("ix_originality_gate_runs_created_at", "created_at"),
    )
