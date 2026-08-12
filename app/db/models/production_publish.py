"""Phase 5 final-review and verified-publication authorities.

These tables are deliberately small, immutable authorities around the existing
v2 project/package/render models.  They do not replace the M7 manual-publish
ledger or the existing ``HumanUploadTask``/``UploadedVideo`` entities.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, uuid_pk


class FinalReviewCandidate(Base):
    """Exact, immutable media candidate exposed at the sole human boundary."""

    __tablename__ = "final_review_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_workflow_runs.id"),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    channel_profile_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compiled_channel_policy_snapshots.id"),
        nullable=False,
    )
    production_package_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    production_package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    production_readiness_receipt_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    production_readiness_receipt_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    canonical_media_timeline_ref: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_media_timeline_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    native_render_plan_ref: Mapped[str] = mapped_column(Text, nullable=False)
    native_render_plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    render_output_ref: Mapped[str] = mapped_column(Text, nullable=False)
    render_output_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    technical_qc_receipt_ref: Mapped[str] = mapped_column(Text, nullable=False)
    technical_qc_receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    creative_qc_receipt_ref: Mapped[str] = mapped_column(Text, nullable=False)
    creative_qc_receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_receipt_ref: Mapped[str] = mapped_column(Text, nullable=False)
    archive_receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_object_ref: Mapped[str] = mapped_column(Text, nullable=False)
    archive_verification_state: Mapped[str] = mapped_column(String(40), nullable=False)
    final_media_ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id"), nullable=False
    )
    final_media_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    destination_binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    destination_binding_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    # A controlled first-video closeout may truthfully stop at FINAL_REVIEW_READY
    # while the manual YouTube destination is still PENDING_PLATFORM_ID.  In
    # that narrow FINAL_REVIEW_ONLY mode these values are intentionally absent;
    # the immutable destination artifact id/hash and target-market lineage keep
    # the review candidate bound without inventing a platform identity.
    destination_platform_channel_id: Mapped[str | None] = mapped_column(Text)
    destination_account_identity: Mapped[str | None] = mapped_column(Text)
    target_platform: Mapped[str] = mapped_column(String(40), nullable=False)
    target_surface: Mapped[str] = mapped_column(String(40), nullable=False)
    target_market_lineage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    production_lane: Mapped[str] = mapped_column(String(40), nullable=False)
    content_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    series_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id")
    )
    series_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_runs.id")
    )
    episode_number: Mapped[int | None] = mapped_column(Integer)
    standalone_reason_code: Mapped[str | None] = mapped_column(String(160))
    publish_metadata_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    disclosure_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    materiality_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    materiality_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "candidate_hash",
            name="uq_final_review_candidates_candidate_hash",
        ),
        CheckConstraint(
            "archive_verification_state = 'VERIFIED'",
            name="ck_final_review_candidates_archive_verified",
        ),
        CheckConstraint(
            "production_lane = 'LONG_FORM'",
            name="ck_final_review_candidates_production_lane",
        ),
        CheckConstraint(
            "content_mode in ('SERIES_EPISODE','STANDALONE')",
            name="ck_final_review_candidates_content_mode",
        ),
        CheckConstraint(
            "((target_market_lineage->>'destination_mode' is null "
            "and destination_platform_channel_id is not null "
            "and destination_account_identity is not null) or ("
            "coalesce(target_market_lineage->>'destination_binding_ref', '') "
            "like 'destination-binding://%' "
            "and coalesce(target_market_lineage->>'destination_model_hash', '') "
            "~ '^[0-9a-f]{64}$' "
            "and coalesce(target_market_lineage->>'destination_authority_hash', '') "
            "~ '^[0-9a-f]{64}$' "
            "and coalesce(target_market_lineage->>'destination_binding_hash', '') "
            "~ '^[0-9a-f]{64}$' "
            "and target_market_lineage->>'destination_binding_hash' "
            "is not distinct from "
            "target_market_lineage->>'destination_authority_hash' "
            "and ((target_market_lineage->>'destination_mode' "
            "is not distinct from "
            "'FINAL_REVIEW_ONLY' "
            "and target_market_lineage->>'destination_status' is not distinct from "
            "'PENDING_PLATFORM_ID' "
            "and target_market_lineage->>'publish_execution_allowed' "
            "is not distinct from 'false' "
            "and target_market_lineage->>'automatic_publish' "
            "is not distinct from 'false' "
            "and coalesce(target_market_lineage->>'destination_handle', '') <> '' "
            "and coalesce("
            "target_market_lineage->>'controlled_recovery_authority_id', '') "
            "~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
            "[0-9a-f]{12}$' "
            "and coalesce("
            "target_market_lineage->>'controlled_recovery_authority_hash', '') "
            "~ '^[0-9a-f]{64}$' "
            "and coalesce("
            "target_market_lineage->>'settlement_authority_id', '') "
            "~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
            "[0-9a-f]{12}$' "
            "and coalesce("
            "target_market_lineage->>'settlement_authority_hash', '') "
            "~ '^[0-9a-f]{64}$' "
            "and coalesce("
            "target_market_lineage->>'settlement_qualification_run_id', '') "
            "~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
            "[0-9a-f]{12}$' "
            "and coalesce("
            "target_market_lineage->>'settlement_provenance_hash', '') "
            "~ '^[0-9a-f]{64}$' "
            "and destination_platform_channel_id is null "
            "and destination_account_identity is null) "
            "or (target_market_lineage->>'destination_mode' is not distinct from "
            "'VERIFIED_PUBLISH_DESTINATION' "
            "and target_market_lineage->>'destination_status' "
            "is not distinct from 'VERIFIED' "
            "and target_market_lineage->>'publish_execution_allowed' "
            "is not distinct from 'true' "
            "and target_market_lineage->>'automatic_publish' "
            "is not distinct from 'false' "
            "and destination_platform_channel_id is not null "
            "and destination_account_identity is not null))))",
            name="ck_final_review_candidates_destination_mode",
        ),
        CheckConstraint(
            "(content_mode = 'SERIES_EPISODE' "
            "and series_plan_id is not null "
            "and series_run_id is not null "
            "and episode_number > 0 "
            "and standalone_reason_code is null) "
            "or (content_mode = 'STANDALONE' "
            "and series_plan_id is null "
            "and series_run_id is null "
            "and episode_number is null "
            "and standalone_reason_code is not null)",
            name="ck_final_review_candidates_assignment",
        ),
        CheckConstraint(
            "production_package_hash ~ '^[0-9a-f]{64}$' "
            "and production_readiness_receipt_hash ~ '^[0-9a-f]{64}$' "
            "and canonical_media_timeline_hash ~ '^[0-9a-f]{64}$' "
            "and native_render_plan_hash ~ '^[0-9a-f]{64}$' "
            "and render_output_checksum ~ '^[0-9a-f]{64}$' "
            "and technical_qc_receipt_hash ~ '^[0-9a-f]{64}$' "
            "and creative_qc_receipt_hash ~ '^[0-9a-f]{64}$' "
            "and archive_receipt_hash ~ '^[0-9a-f]{64}$' "
            "and final_media_hash ~ '^[0-9a-f]{64}$' "
            "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
            "and materiality_policy_hash ~ '^[0-9a-f]{64}$' "
            "and candidate_hash ~ '^[0-9a-f]{64}$'",
            name="ck_final_review_candidates_hashes",
        ),
        Index("ix_final_review_candidates_workflow_run_id", "workflow_run_id"),
        Index("ix_final_review_candidates_project_id", "video_project_id"),
        Index("ix_final_review_candidates_final_media_ref_id", "final_media_ref_id"),
        Index("ix_final_review_candidates_created_at", "created_at"),
    )


class FinalVideoDecision(Base):
    """The immutable identity-bound UPLOAD/DO_NOT_UPLOAD decision."""

    __tablename__ = "final_video_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    final_review_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("final_review_candidates.id"),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    operator_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    authenticated_actor_role: Mapped[str] = mapped_column(String(120), nullable=False)
    final_media_ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id"), nullable=False
    )
    final_media_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    production_package_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    production_package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    destination_binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    destination_binding_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    warnings_acknowledged: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "final_review_candidate_id",
            name="uq_final_video_decisions_candidate_id",
        ),
        UniqueConstraint(
            "command_id",
            name="uq_final_video_decisions_command_id",
        ),
        UniqueConstraint(
            "decision_hash",
            name="uq_final_video_decisions_decision_hash",
        ),
        CheckConstraint(
            "decision in ('UPLOAD','DO_NOT_UPLOAD')",
            name="ck_final_video_decisions_decision",
        ),
        CheckConstraint(
            "final_media_hash ~ '^[0-9a-f]{64}$' "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
            "and decision_hash ~ '^[0-9a-f]{64}$'",
            name="ck_final_video_decisions_hashes",
        ),
        Index("ix_final_video_decisions_project_id", "video_project_id"),
        Index("ix_final_video_decisions_final_media_ref_id", "final_media_ref_id"),
        Index("ix_final_video_decisions_created_at", "created_at"),
    )


class SeriesEpisodePublication(Base):
    """Exactly-once receipt for advancing a series after verified publication."""

    __tablename__ = "series_episode_publications"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    series_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id"), nullable=False
    )
    series_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_runs.id"), nullable=False
    )
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_videos.id"), nullable=False
    )
    final_video_decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_video_decisions.id"), nullable=False
    )
    human_upload_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_upload_tasks.id"), nullable=False
    )
    manual_publish_confirmation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("manual_publish_confirmations.id"),
        nullable=False,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "video_project_id",
            name="uq_series_episode_publications_project_id",
        ),
        UniqueConstraint(
            "uploaded_video_id",
            name="uq_series_episode_publications_uploaded_video_id",
        ),
        UniqueConstraint(
            "series_run_id",
            "episode_number",
            name="uq_series_episode_publications_run_episode",
        ),
        CheckConstraint(
            "episode_number > 0",
            name="ck_series_episode_publications_episode_positive",
        ),
        Index("ix_series_episode_publications_series_plan_id", "series_plan_id"),
        Index("ix_series_episode_publications_series_run_id", "series_run_id"),
        Index("ix_series_episode_publications_created_at", "created_at"),
    )


def _immutable_phase5_authority(
    _mapper: Mapper[Any], _connection: Any, target: Any
) -> None:
    raise RuntimeError(f"{target.__class__.__name__.upper()}_IMMUTABLE")


for _immutable_model in (
    FinalReviewCandidate,
    FinalVideoDecision,
    SeriesEpisodePublication,
):
    event.listen(_immutable_model, "before_update", _immutable_phase5_authority)
    event.listen(_immutable_model, "before_delete", _immutable_phase5_authority)
