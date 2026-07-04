import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class PackagingReviewQueueItem(Base):
    __tablename__ = "packaging_review_queue_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("first_scripted_video_packages.id"), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    effective_context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("effective_channel_runtime_context_snapshots.id")
    )
    gate_key: Mapped[str] = mapped_column(String(160), nullable=False)
    issue_code: Mapped[str] = mapped_column(String(160), nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    target_artifact_type: Mapped[str] = mapped_column(String(120), nullable=False)
    target_artifact_ref: Mapped[str | None] = mapped_column(Text)
    source_gate_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("r3d4_gate_runs.id"))
    source_gate_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("r3d4_gate_batch_runs.id"))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING_PATCH")
    next_action_code: Mapped[str] = mapped_column(String(80), nullable=False, default="NEEDS_PROPOSED_PATCH")
    human_readable_title: Mapped[str] = mapped_column(Text, nullable=False)
    human_readable_why: Mapped[str] = mapped_column(Text, nullable=False)
    human_readable_fix: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_packaging_review_queue_items_package", "package_id"),
        Index("ix_packaging_review_queue_items_project", "video_project_id"),
        Index("ix_packaging_review_queue_items_effective_context", "effective_context_snapshot_id"),
        Index("ix_packaging_review_queue_items_gate", "gate_key"),
        Index("ix_packaging_review_queue_items_issue", "issue_code"),
        Index("ix_packaging_review_queue_items_status", "status"),
        Index(
            "ix_packaging_review_queue_items_dedupe",
            "package_id",
            "gate_key",
            "issue_code",
            "target_artifact_ref",
        ),
    )


class PackagingProposedPatch(Base):
    __tablename__ = "packaging_proposed_patches"

    id: Mapped[uuid.UUID] = uuid_pk()
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("packaging_review_queue_items.id"), nullable=False)
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("first_scripted_video_packages.id"), nullable=False)
    proposal_source: Mapped[str] = mapped_column(String(80), nullable=False)
    routed_agent_key: Mapped[str | None] = mapped_column(String(240))
    patch_type: Mapped[str] = mapped_column(String(80), nullable=False)
    before_snapshot_ref: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_patch_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    after_preview_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    affected_artifact_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    risk_level: Mapped[str] = mapped_column(String(40), nullable=False, default="MEDIUM")
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    patch_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_packaging_proposed_patches_queue_item", "queue_item_id"),
        Index("ix_packaging_proposed_patches_package", "package_id"),
        Index("ix_packaging_proposed_patches_status", "status"),
        Index("ix_packaging_proposed_patches_patch_hash", "patch_hash"),
        Index("ix_packaging_proposed_patches_created_at", "created_at"),
    )


class PackagingPatchApprovalDecision(Base):
    __tablename__ = "packaging_patch_approval_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    proposed_patch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("packaging_proposed_patches.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_packaging_patch_approval_decisions_patch", "proposed_patch_id"),
        Index("ix_packaging_patch_approval_decisions_decision", "decision"),
        Index("ix_packaging_patch_approval_decisions_created_at", "created_at"),
    )


class PackagingPatchApplyRun(Base):
    __tablename__ = "packaging_patch_apply_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    proposed_patch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("packaging_proposed_patches.id"), nullable=False)
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("first_scripted_video_packages.id"), nullable=False)
    apply_status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_artifact_ref: Mapped[str | None] = mapped_column(Text)
    created_handoff_override_ref: Mapped[str | None] = mapped_column(Text)
    created_version_hash: Mapped[str | None] = mapped_column(String(128))
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_packaging_patch_apply_runs_patch", "proposed_patch_id"),
        Index("ix_packaging_patch_apply_runs_package", "package_id"),
        Index("ix_packaging_patch_apply_runs_status", "apply_status"),
        Index("ix_packaging_patch_apply_runs_created_at", "created_at"),
    )


class PackagingGateRerunRecord(Base):
    __tablename__ = "packaging_gate_rerun_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("first_scripted_video_packages.id"), nullable=False)
    proposed_patch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("packaging_proposed_patches.id"))
    gate_keys_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rerun_status: Mapped[str] = mapped_column(String(40), nullable=False)
    gate_batch_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("r3d4_gate_batch_runs.id"))
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_packaging_gate_rerun_records_package", "package_id"),
        Index("ix_packaging_gate_rerun_records_patch", "proposed_patch_id"),
        Index("ix_packaging_gate_rerun_records_status", "rerun_status"),
        Index("ix_packaging_gate_rerun_records_created_at", "created_at"),
    )
