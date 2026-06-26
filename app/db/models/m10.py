import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class LearningCandidateGenerationRun(Base):
    __tablename__ = "learning_candidate_generation_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"))
    source_failure_trace_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("failure_trace_reports.id")
    )
    source_recovery_proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("recovery_proposals.id"))
    source_analytics_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("analytics_snapshots.id"))
    source_uploaded_video_metrics_summary_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_video_metrics_summaries.id")
    )
    run_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="RULE_BASED")
    run_state: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_learning_runs_company_id", "company_id"),
        Index("ix_learning_runs_channel_workspace_id", "channel_workspace_id"),
        Index("ix_learning_runs_video_project_id", "video_project_id"),
        Index("ix_learning_runs_uploaded_video_id", "uploaded_video_id"),
        Index("ix_learning_runs_state", "run_state"),
        Index("ix_learning_runs_mode", "run_mode"),
        Index("ix_learning_runs_created_at", "created_at"),
    )


class LearningCandidate(Base):
    __tablename__ = "learning_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    generation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_candidate_generation_runs.id")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"))
    candidate_type: Mapped[str] = mapped_column(String(60), nullable=False)
    candidate_state: Mapped[str] = mapped_column(String(60), nullable=False, default="GENERATED")
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    friendly_status: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_summary: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_learning: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_playbook_text: Mapped[str | None] = mapped_column(Text)
    recommended_scope: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    confidence_label: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    risk_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    evidence_bundle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_evidence_bundles.id")
    )
    eligibility_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_promotion_eligibility_runs.id")
    )
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    diagnostic_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    recovery_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    metric_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    policy_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    rights_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    limitations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    counter_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_learning_candidates_generation_run_id", "generation_run_id"),
        Index("ix_learning_candidates_company_id", "company_id"),
        Index("ix_learning_candidates_channel_workspace_id", "channel_workspace_id"),
        Index("ix_learning_candidates_video_project_id", "video_project_id"),
        Index("ix_learning_candidates_uploaded_video_id", "uploaded_video_id"),
        Index("ix_learning_candidates_type", "candidate_type"),
        Index("ix_learning_candidates_state", "candidate_state"),
        Index("ix_learning_candidates_created_at", "created_at"),
    )


class LearningEvidenceBundle(Base):
    __tablename__ = "learning_evidence_bundles"

    id: Mapped[uuid.UUID] = uuid_pk()
    learning_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("learning_candidates.id"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_video_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    source_project_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    analytics_snapshot_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    diagnostic_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    recovery_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    metric_support: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    counter_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    limitations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    freshness_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    confidence_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    policy_rights_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_learning_evidence_bundles_candidate_id", "learning_candidate_id"),
        Index("ix_learning_evidence_bundles_company_id", "company_id"),
        Index("ix_learning_evidence_bundles_channel_id", "channel_workspace_id"),
        Index("ix_learning_evidence_bundles_created_at", "created_at"),
    )


class LearningPromotionEligibilityRun(Base):
    __tablename__ = "learning_promotion_eligibility_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_candidates.id"), nullable=False
    )
    evidence_bundle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_evidence_bundles.id")
    )
    result: Mapped[str] = mapped_column(String(40), nullable=False)
    min_evidence_met: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metric_freshness_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    policy_flags_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rights_flags_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence_label: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    risk_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    blockers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_learning_eligibility_candidate_id", "learning_candidate_id"),
        Index("ix_learning_eligibility_bundle_id", "evidence_bundle_id"),
        Index("ix_learning_eligibility_result", "result"),
        Index("ix_learning_eligibility_created_at", "created_at"),
    )


class LearningReviewQueueItem(Base):
    __tablename__ = "learning_review_queue_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_candidates.id"), nullable=False
    )
    evidence_bundle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_evidence_bundles.id")
    )
    eligibility_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_promotion_eligibility_runs.id")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"))
    queue_state: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[str] = mapped_column(String(40), nullable=False, default="NORMAL")
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    friendly_status: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_scope: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    confidence_label: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    risk_level: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    next_action: Mapped[str] = mapped_column(Text, nullable=False)
    approval_actions_allowed: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    audit_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_learning_review_queue_candidate_id", "learning_candidate_id"),
        Index("ix_learning_review_queue_company_id", "company_id"),
        Index("ix_learning_review_queue_channel_id", "channel_workspace_id"),
        Index("ix_learning_review_queue_project_id", "video_project_id"),
        Index("ix_learning_review_queue_uploaded_video_id", "uploaded_video_id"),
        Index("ix_learning_review_queue_state", "queue_state"),
        Index("ix_learning_review_queue_priority", "priority"),
        Index("ix_learning_review_queue_created_at", "created_at"),
    )


class PlaybookCandidateDraft(Base):
    __tablename__ = "playbook_candidate_drafts"

    id: Mapped[uuid.UUID] = uuid_pk()
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_candidates.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"))
    candidate_scope: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    playbook_category: Mapped[str] = mapped_column(String(40), nullable=False, default="OTHER")
    draft_text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    risk_notes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_playbook_candidate_drafts_candidate_id", "learning_candidate_id"),
        Index("ix_playbook_candidate_drafts_company_id", "company_id"),
        Index("ix_playbook_candidate_drafts_channel_id", "channel_workspace_id"),
        Index("ix_playbook_candidate_drafts_category", "playbook_category"),
        Index("ix_playbook_candidate_drafts_state", "state"),
        Index("ix_playbook_candidate_drafts_created_at", "created_at"),
    )
