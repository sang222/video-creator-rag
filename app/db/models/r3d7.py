import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, uuid_pk


class MemoryInfluenceManifest(Base):
    __tablename__ = "memory_influence_manifests"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    effective_context_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("effective_channel_runtime_context_snapshots.id"), nullable=False
    )
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    retrieval_manifest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vector_retrieval_manifests.id"), nullable=False
    )
    memory_facet_ids_used_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    memory_item_ids_used_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    digest_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_render_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_render_runs.id"))
    prompt_context_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    applied_as_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ignored_memory_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    blocked_memory_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    scope_status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_memory_influence_manifests_project", "video_project_id"),
        Index("ix_memory_influence_manifests_package", "package_id"),
        Index("ix_memory_influence_manifests_effective_context", "effective_context_snapshot_id"),
        Index("ix_memory_influence_manifests_agent", "agent_key"),
        Index("ix_memory_influence_manifests_retrieval", "retrieval_manifest_id"),
        Index("ix_memory_influence_manifests_prompt_render", "prompt_render_run_id"),
        Index("ix_memory_influence_manifests_digest_hash", "digest_hash"),
        Index("ix_memory_influence_manifests_scope_status", "scope_status"),
        Index("ix_memory_influence_manifests_created_at", "created_at"),
    )


class QualityDeltaAttribution(Base):
    __tablename__ = "quality_delta_attributions"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_memory_influence_manifest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_influence_manifests.id"), nullable=False
    )
    source_video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    target_uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"))
    target_video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    effective_context_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("effective_channel_runtime_context_snapshots.id"), nullable=False
    )
    market_context_hash: Mapped[str | None] = mapped_column(String(128))
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("content_categories.id"))
    character_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_bindings.id"))
    expected_metric_family: Mapped[str] = mapped_column(String(80), nullable=False)
    expected_improvement_direction: Mapped[str] = mapped_column(String(40), nullable=False)
    baseline_snapshot_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    observed_snapshot_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attribution_window: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_result: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_quality_delta_attributions_manifest", "source_memory_influence_manifest_id"),
        Index("ix_quality_delta_attributions_source_project", "source_video_project_id"),
        Index("ix_quality_delta_attributions_target_video", "target_uploaded_video_id"),
        Index("ix_quality_delta_attributions_target_project", "target_video_project_id"),
        Index("ix_quality_delta_attributions_effective_context", "effective_context_snapshot_id"),
        Index("ix_quality_delta_attributions_metric_family", "expected_metric_family"),
        Index("ix_quality_delta_attributions_result", "confidence_result"),
        Index("ix_quality_delta_attributions_created_at", "created_at"),
    )


class LearningToMemoryPromotionRun(Base):
    __tablename__ = "learning_to_memory_promotion_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    learning_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("learning_candidates.id"))
    approved_playbook_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("approved_playbook_entries.id"))
    evidence_bundle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("learning_evidence_bundles.id"))
    source_uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"))
    created_memory_item_ids_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_memory_facet_ids_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    run_status: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    human_approval_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_learning_to_memory_runs_candidate", "learning_candidate_id"),
        Index("ix_learning_to_memory_runs_playbook", "approved_playbook_entry_id"),
        Index("ix_learning_to_memory_runs_evidence", "evidence_bundle_id"),
        Index("ix_learning_to_memory_runs_source_video", "source_uploaded_video_id"),
        Index("ix_learning_to_memory_runs_status", "run_status"),
        Index("ix_learning_to_memory_runs_created_at", "created_at"),
    )


class AgentMemoryApplicationRecord(Base):
    __tablename__ = "agent_memory_application_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False)
    package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    memory_influence_manifest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_influence_manifests.id"), nullable=False
    )
    memory_digest_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    application_mode: Mapped[str] = mapped_column(String(80), nullable=False)
    applied_context_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_agent_memory_application_records_project", "video_project_id"),
        Index("ix_agent_memory_application_records_package", "package_id"),
        Index("ix_agent_memory_application_records_agent", "agent_key"),
        Index("ix_agent_memory_application_records_manifest", "memory_influence_manifest_id"),
        Index("ix_agent_memory_application_records_digest_hash", "memory_digest_hash"),
        Index("ix_agent_memory_application_records_mode", "application_mode"),
        Index("ix_agent_memory_application_records_created_at", "created_at"),
    )


class MemoryConfidenceUpdateLedger(Base):
    __tablename__ = "memory_confidence_update_ledger"

    id: Mapped[uuid.UUID] = uuid_pk()
    memory_facet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memory_facets.id"), nullable=False)
    quality_delta_attribution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quality_delta_attributions.id")
    )
    old_confidence_label: Mapped[str] = mapped_column(String(40), nullable=False)
    new_confidence_label: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_memory_confidence_update_ledger_facet", "memory_facet_id"),
        Index("ix_memory_confidence_update_ledger_attribution", "quality_delta_attribution_id"),
        Index("ix_memory_confidence_update_ledger_created_at", "created_at"),
    )
