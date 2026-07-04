import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class EmbeddingFacet(Base):
    __tablename__ = "embedding_facets"

    id: Mapped[uuid.UUID] = uuid_pk()
    memory_facet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memory_facets.id"), nullable=False)
    memory_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_memory_items.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    content_category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("content_categories.id"))
    series_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    character_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_profiles.id"))
    character_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_versions.id"))
    facet_type: Mapped[str] = mapped_column(String(80), nullable=False)
    facet_text_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(160), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_vector_json: Mapped[list[float]] = mapped_column(JSONB, nullable=False, default=list)
    approval_status_at_embed: Mapped[str] = mapped_column(String(40), nullable=False)
    rights_status_at_embed: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_safety_state_at_embed: Mapped[str] = mapped_column(String(40), nullable=False)
    embedding_eligible_at_embed: Mapped[bool] = mapped_column(nullable=False, default=False)
    stale_state: Mapped[str] = mapped_column(String(40), nullable=False, default="FRESH")
    stale_reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_embedding_facets_memory_facet", "memory_facet_id"),
        Index("ix_embedding_facets_memory_item", "memory_item_id"),
        Index("ix_embedding_facets_company", "company_id"),
        Index("ix_embedding_facets_channel", "channel_workspace_id"),
        Index("ix_embedding_facets_category", "content_category_id"),
        Index("ix_embedding_facets_character_profile", "character_profile_id"),
        Index("ix_embedding_facets_type", "facet_type"),
        Index("ix_embedding_facets_text_hash", "facet_text_hash"),
        Index("ix_embedding_facets_stale_state", "stale_state"),
        Index("ix_embedding_facets_created_at", "created_at"),
    )


class EmbeddingJob(Base):
    __tablename__ = "embedding_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    memory_facet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memory_facets.id"), nullable=False)
    job_status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    blocker_reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    embedding_model: Mapped[str | None] = mapped_column(String(160))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_embedding_jobs_memory_facet", "memory_facet_id"),
        Index("ix_embedding_jobs_status", "job_status"),
        Index("ix_embedding_jobs_created_at", "created_at"),
    )


class VectorRetrievalManifest(Base):
    __tablename__ = "vector_retrieval_manifests"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    effective_context_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("effective_channel_runtime_context_snapshots.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    content_category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("content_categories.id"))
    series_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    character_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_profiles.id"))
    character_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("character_versions.id"))
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    use_case: Mapped[str] = mapped_column(String(120), nullable=False)
    query_facet_type: Mapped[str | None] = mapped_column(String(80))
    query_text_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    sql_filter_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    candidate_count_before_vector: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count_after_policy: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_memory_facet_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    blocked_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    rejected_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    vector_model: Mapped[str | None] = mapped_column(String(160))
    ranking_params_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    retrieval_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    digest_hash: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_vector_retrieval_manifests_project", "video_project_id"),
        Index("ix_vector_retrieval_manifests_package", "package_id"),
        Index("ix_vector_retrieval_manifests_effective_context", "effective_context_snapshot_id"),
        Index("ix_vector_retrieval_manifests_company", "company_id"),
        Index("ix_vector_retrieval_manifests_channel", "channel_workspace_id"),
        Index("ix_vector_retrieval_manifests_category", "content_category_id"),
        Index("ix_vector_retrieval_manifests_agent", "agent_key"),
        Index("ix_vector_retrieval_manifests_use_case", "use_case"),
        Index("ix_vector_retrieval_manifests_hash", "retrieval_hash"),
        Index("ix_vector_retrieval_manifests_created_at", "created_at"),
    )
