import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class ChannelMemoryItem(Base):
    __tablename__ = "channel_memory_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    content_category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("content_categories.id"))
    series_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    character_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_profiles.id"))
    character_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_versions.id"))
    character_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_bindings.id"))
    memory_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    source_content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    approval_status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    rights_status: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    prompt_safety_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    reuse_scope: Mapped[str] = mapped_column(String(40), nullable=False, default="CHANNEL")
    freshness_state: Mapped[str] = mapped_column(String(40), nullable=False, default="FRESH")
    created_from_learning_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_candidates.id")
    )
    created_from_failure_trace_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("failure_trace_reports.id")
    )
    created_from_recovery_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_proposals.id")
    )
    created_from_approved_playbook_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approved_playbook_entries.id")
    )
    human_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approval_authority_type: Mapped[str | None] = mapped_column(String(40))
    approval_policy_version: Mapped[str | None] = mapped_column(String(120))
    approval_policy_hash: Mapped[str | None] = mapped_column(String(64))
    approval_evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_channel_memory_items_company", "company_id"),
        Index("ix_channel_memory_items_channel", "channel_workspace_id"),
        Index("ix_channel_memory_items_category", "content_category_id"),
        Index("ix_channel_memory_items_character_profile", "character_profile_id"),
        Index("ix_channel_memory_items_approval", "approval_status"),
        Index("ix_channel_memory_items_rights", "rights_status"),
        Index("ix_channel_memory_items_prompt_safety", "prompt_safety_state"),
        Index("ix_channel_memory_items_reuse_scope", "reuse_scope"),
        Index("ix_channel_memory_items_freshness", "freshness_state"),
        Index("ix_channel_memory_items_source_hash", "source_content_hash"),
        Index("ix_channel_memory_items_content_hash", "content_hash"),
        Index("ix_channel_memory_items_created_at", "created_at"),
    )


class MemoryFacet(Base):
    __tablename__ = "memory_facets"

    id: Mapped[uuid.UUID] = uuid_pk()
    memory_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_memory_items.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    content_category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("content_categories.id"))
    character_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_profiles.id"))
    character_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_versions.id"))
    facet_type: Mapped[str] = mapped_column(String(80), nullable=False)
    facet_text: Mapped[str] = mapped_column(Text, nullable=False)
    facet_text_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    allowed_use_cases_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    forbidden_use_cases_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    polarity: Mapped[str] = mapped_column(String(40), nullable=False, default="NEUTRAL")
    confidence_label: Mapped[str] = mapped_column(String(40), nullable=False, default="UNPROVEN")
    prompt_safety_state: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    embedding_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_memory_facets_item", "memory_item_id"),
        Index("ix_memory_facets_company", "company_id"),
        Index("ix_memory_facets_channel", "channel_workspace_id"),
        Index("ix_memory_facets_category", "content_category_id"),
        Index("ix_memory_facets_character_profile", "character_profile_id"),
        Index("ix_memory_facets_type", "facet_type"),
        Index("ix_memory_facets_text_hash", "facet_text_hash"),
        Index("ix_memory_facets_prompt_safety", "prompt_safety_state"),
        Index("ix_memory_facets_embedding_eligible", "embedding_eligible"),
        Index("ix_memory_facets_created_at", "created_at"),
    )


class MemoryReviewQueueItem(Base):
    __tablename__ = "memory_review_queue_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    memory_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_memory_items.id"), nullable=False)
    queue_status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    reviewer_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_memory_review_queue_item", "memory_item_id"),
        Index("ix_memory_review_queue_status", "queue_status"),
        Index("ix_memory_review_queue_created_at", "created_at"),
    )


class MemoryApprovalDecision(Base):
    __tablename__ = "memory_approval_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    memory_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_memory_items.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approval_authority_type: Mapped[str | None] = mapped_column(String(40))
    policy_version: Mapped[str | None] = mapped_column(String(120))
    policy_hash: Mapped[str | None] = mapped_column(String(64))
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    approved_prompt_use_cases_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rejected_reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_memory_approval_decisions_item", "memory_item_id"),
        Index("ix_memory_approval_decisions_decision", "decision"),
        Index("ix_memory_approval_decisions_created_at", "created_at"),
    )


class MemoryUsageManifest(Base):
    __tablename__ = "memory_usage_manifests"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    effective_context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("effective_channel_runtime_context_snapshots.id")
    )
    memory_item_ids_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    memory_facet_ids_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    use_case: Mapped[str] = mapped_column(String(120), nullable=False)
    usage_status: Mapped[str] = mapped_column(String(40), nullable=False, default="PLANNED")
    digest_hash: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_memory_usage_manifests_project", "video_project_id"),
        Index("ix_memory_usage_manifests_package", "package_id"),
        Index("ix_memory_usage_manifests_effective_context", "effective_context_snapshot_id"),
        Index("ix_memory_usage_manifests_use_case", "use_case"),
        Index("ix_memory_usage_manifests_status", "usage_status"),
        Index("ix_memory_usage_manifests_created_at", "created_at"),
    )


class MemorySourceLink(Base):
    __tablename__ = "memory_source_links"

    id: Mapped[uuid.UUID] = uuid_pk()
    memory_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_memory_items.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_memory_source_links_item", "memory_item_id"),
        Index("ix_memory_source_links_source", "source_type"),
        Index("ix_memory_source_links_hash", "source_hash"),
        Index("ix_memory_source_links_created_at", "created_at"),
    )
