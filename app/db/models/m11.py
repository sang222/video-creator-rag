import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class ChannelLifecycleDecision(Base):
    __tablename__ = "channel_lifecycle_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    previous_lifecycle_state: Mapped[str | None] = mapped_column(String(40))
    lifecycle_state: Mapped[str] = mapped_column(String(40), nullable=False)
    health_status: Mapped[str] = mapped_column(String(40), nullable=False, default="NEW")
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decision_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_channel_lifecycle_decisions_channel", "channel_workspace_id"),
        Index("ix_channel_lifecycle_decisions_company", "company_id"),
        Index("ix_channel_lifecycle_decisions_state", "lifecycle_state"),
        Index("ix_channel_lifecycle_decisions_health", "health_status"),
        Index("ix_channel_lifecycle_decisions_created_at", "created_at"),
    )


class LearningReviewDecision(Base):
    __tablename__ = "learning_review_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_candidates.id"), nullable=False
    )
    learning_review_queue_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_review_queue_items.id")
    )
    evidence_bundle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_evidence_bundles.id")
    )
    playbook_candidate_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbook_candidate_drafts.id")
    )
    approved_playbook_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approved_playbook_entries.id")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    decision_state: Mapped[str] = mapped_column(String(40), nullable=False, default="RECORDED")
    actor_role: Mapped[str] = mapped_column(String(80), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    rationale: Mapped[str | None] = mapped_column(Text)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_learning_review_decisions_candidate", "learning_candidate_id"),
        Index("ix_learning_review_decisions_queue_item", "learning_review_queue_item_id"),
        Index("ix_learning_review_decisions_company", "company_id"),
        Index("ix_learning_review_decisions_channel", "channel_workspace_id"),
        Index("ix_learning_review_decisions_action", "action"),
        Index("ix_learning_review_decisions_created_at", "created_at"),
    )


class ApprovedPlaybookEntry(Base):
    __tablename__ = "approved_playbook_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_candidates.id"), nullable=False
    )
    learning_review_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_review_decisions.id")
    )
    playbook_candidate_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbook_candidate_drafts.id")
    )
    evidence_bundle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_evidence_bundles.id")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    playbook_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    limitations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    counter_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    policy_rights_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="APPROVED")
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_approved_playbook_entries_candidate", "learning_candidate_id"),
        Index("ix_approved_playbook_entries_company", "company_id"),
        Index("ix_approved_playbook_entries_channel", "channel_workspace_id"),
        Index("ix_approved_playbook_entries_scope", "scope"),
        Index("ix_approved_playbook_entries_category", "category"),
        Index("ix_approved_playbook_entries_state", "state"),
        Index("ix_approved_playbook_entries_created_at", "created_at"),
    )
