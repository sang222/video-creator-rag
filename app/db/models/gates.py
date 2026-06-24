import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class GateDefinitionVersion(Base):
    __tablename__ = "gate_definition_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    gate_key: Mapped[str] = mapped_column(String(160), nullable=False)
    gate_name: Mapped[str] = mapped_column(Text, nullable=False)
    gate_domain: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    input_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason_code_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("gate_key", "version"),
        Index("ix_gate_definition_versions_gate_key", "gate_key"),
        Index("ix_gate_definition_versions_status", "status"),
        Index("ix_gate_definition_versions_created_at", "created_at"),
    )


class GateRun(Base):
    __tablename__ = "gate_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    gate_definition_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gate_definition_versions.id"), nullable=False
    )
    gate_key: Mapped[str] = mapped_column(String(160), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("artifact_versions.id"))
    review_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_tasks.id"))
    policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id")
    )
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    input_snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    metric_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence_reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    decision_basis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_review_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_tasks.id"))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_gate_runs_gate_definition_version_id", "gate_definition_version_id"),
        Index("ix_gate_runs_gate_key", "gate_key"),
        Index("ix_gate_runs_target", "target_type", "target_id"),
        Index("ix_gate_runs_video_project_id", "video_project_id"),
        Index("ix_gate_runs_artifact_version_id", "artifact_version_id"),
        Index("ix_gate_runs_created_at", "created_at"),
    )


class PlatformPolicyCatalog(Base):
    __tablename__ = "platform_policy_catalogs"

    id: Mapped[uuid.UUID] = uuid_pk()
    catalog_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_domain: Mapped[str] = mapped_column(String(120), nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_policy_versions.id"))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="active")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_platform_policy_catalogs_platform", "platform"),
        Index("ix_platform_policy_catalogs_policy_domain", "policy_domain"),
        Index("ix_platform_policy_catalogs_created_at", "created_at"),
    )


class PlatformPolicyVersion(Base):
    __tablename__ = "platform_policy_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    catalog_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_policy_catalogs.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    policy_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    interpretation_notes: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("catalog_id", "version"),
        Index("ix_platform_policy_versions_catalog_id", "catalog_id"),
        Index("ix_platform_policy_versions_status", "status"),
        Index("ix_platform_policy_versions_created_at", "created_at"),
    )


class PolicyChangeRecord(Base):
    __tablename__ = "policy_change_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    change_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_domain: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(60), nullable=False, default="DRAFT")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    old_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_policy_versions.id"))
    new_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_policy_versions.id"))
    impact_classification: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    diff_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    affected_gate_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    affected_domains: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    requires_revalidation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rollback_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_policy_change_records_platform", "platform"),
        Index("ix_policy_change_records_policy_domain", "policy_domain"),
        Index("ix_policy_change_records_state", "state"),
        Index("ix_policy_change_records_created_at", "created_at"),
    )


class PolicySourceRef(Base):
    __tablename__ = "policy_source_refs"

    id: Mapped[uuid.UUID] = uuid_pk()
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_policy_versions.id"))
    policy_change_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("policy_change_records.id"))
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_title: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reliability: Mapped[str] = mapped_column(String(40), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_policy_source_refs_policy_version_id", "policy_version_id"),
        Index("ix_policy_source_refs_policy_change_record_id", "policy_change_record_id"),
        Index("ix_policy_source_refs_created_at", "created_at"),
    )


class PolicyRevalidationBatch(Base):
    __tablename__ = "policy_revalidation_batches"

    id: Mapped[uuid.UUID] = uuid_pk()
    policy_change_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("policy_change_records.id"))
    gate_definition_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gate_definition_versions.id")
    )
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_policy_revalidation_batches_policy_change_record_id", "policy_change_record_id"),
        Index("ix_policy_revalidation_batches_status", "status"),
        Index("ix_policy_revalidation_batches_created_at", "created_at"),
    )
