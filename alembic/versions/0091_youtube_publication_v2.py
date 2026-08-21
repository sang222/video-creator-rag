"""Add candidate-bound private staging and observed public cutover v2."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0091_youtube_publication_v2"
down_revision: str | None = "0090_learning_reassessment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)

    op.drop_constraint(
        "uq_yt_private_stage_decision", "youtube_private_stages", type_="unique"
    )
    op.alter_column(
        "youtube_private_stages", "final_video_decision_id", nullable=True
    )
    op.create_unique_constraint(
        "uq_youtube_private_stages_decision",
        "youtube_private_stages",
        ["final_video_decision_id"],
    )
    op.drop_constraint(
        "ck_yt_private_stage_state", "youtube_private_stages", type_="check"
    )
    op.create_check_constraint(
        "ck_youtube_private_stages_state",
        "youtube_private_stages",
        "state in ('PREPARED','SESSION_CREATED','UPLOADING','OUTCOME_UNKNOWN',"
        "'BYTES_ACCEPTED','PROCESSING','PRIVATE_VERIFIED','PUBLICATION_VERIFIED',"
        "'REJECTED','NEEDS_RERENDER','BLOCKED','FAILED')",
    )
    op.drop_constraint(
        "ck_yt_private_stage_verified", "youtube_private_stages", type_="check"
    )
    op.create_check_constraint(
        "ck_youtube_private_stages_verified",
        "youtube_private_stages",
        "(state not in ('PRIVATE_VERIFIED','PUBLICATION_VERIFIED')) or "
        "(platform_video_id is not null and studio_url is not null and "
        "observed_metadata is not null and observed_metadata_hash is not null and "
        "processing_status = 'SUCCEEDED' and private_verified_at is not null)",
    )

    op.create_table(
        "youtube_private_rework_requests",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("company_id", uuid, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "channel_workspace_id",
            uuid,
            sa.ForeignKey("channel_workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "final_review_candidate_id",
            uuid,
            sa.ForeignKey("final_review_candidates.id"),
            nullable=False,
        ),
        sa.Column(
            "youtube_private_stage_id",
            uuid,
            sa.ForeignKey("youtube_private_stages.id"),
            nullable=False,
        ),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by_actor_id", uuid, nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "youtube_private_stage_id",
            "request_hash",
            name="uq_youtube_private_rework_stage_request",
        ),
        sa.CheckConstraint(
            "disposition in ('REJECT','NEEDS_RERENDER')",
            name="ck_youtube_private_rework_disposition",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_youtube_private_rework_hash",
        ),
    )
    op.create_index(
        "ix_youtube_private_rework_stage",
        "youtube_private_rework_requests",
        ["youtube_private_stage_id"],
    )

    op.alter_column(
        "public_publication_receipts", "final_video_decision_id", nullable=True
    )
    op.alter_column(
        "public_publication_receipts",
        "manual_publish_confirmation_id",
        nullable=True,
    )
    op.create_unique_constraint(
        "uq_public_publication_receipts_stage",
        "public_publication_receipts",
        ["youtube_private_stage_id"],
    )
    op.create_check_constraint(
        "ck_public_publication_receipts_authority",
        "public_publication_receipts",
        "(final_video_decision_id is not null and "
        "manual_publish_confirmation_id is not null) or "
        "(youtube_private_stage_id is not null and "
        "final_video_decision_id is null and "
        "manual_publish_confirmation_id is null)",
    )

    op.drop_constraint(
        "ck_uploaded_videos_schema_version", "uploaded_videos", type_="check"
    )
    op.create_check_constraint(
        "ck_uploaded_videos_schema_version",
        "uploaded_videos",
        "schema_version in ('v1','v2','v3','v4')",
    )
    op.drop_constraint(
        "ck_uploaded_videos_v2_binding", "uploaded_videos", type_="check"
    )
    op.create_check_constraint(
        "ck_uploaded_videos_v2_binding",
        "uploaded_videos",
        "(schema_version in ('v1','v4')) or "
        "(video_project_id is not null and policy_snapshot_id is not null and "
        "manual_publish_confirmation_id is not null and human_upload_task_id is not null and "
        "final_review_candidate_id is not null and final_video_decision_id is not null and "
        "final_media_ref_id is not null and production_package_artifact_version_id is not null and "
        "production_package_hash ~ '^[0-9a-f]{64}$' and channel_profile_version_id is not null and "
        "reviewed_checksum ~ '^[0-9a-f]{64}$' and destination_binding_id is not null and "
        "destination_binding_fingerprint ~ '^[0-9a-f]{64}$' and production_lane = 'LONG_FORM' and "
        "content_mode in ('SERIES_EPISODE','STANDALONE') and target_market_lineage is not null and "
        "archive_supplement is not null and archive_supplement_ref is not null and "
        "archive_supplement_hash ~ '^[0-9a-f]{64}$' and verification_status = 'VERIFIED' and "
        "analytics_sync_status = 'READY' and verified_event_id is not null and "
        "analytics_ready_event_id is not null and analytics_ready_at is not null)",
    )
    op.create_check_constraint(
        "ck_uploaded_videos_v4_public_receipt",
        "uploaded_videos",
        "(schema_version <> 'v4') or (public_publication_receipt_id is not null and "
        "final_video_decision_id is null and manual_publish_confirmation_id is null and "
        "human_upload_task_id is null and "
        "final_review_candidate_id is not null and video_project_id is not null and "
        "policy_snapshot_id is not null and "
        "final_media_ref_id is not null and production_package_artifact_version_id is not null and "
        "production_package_hash ~ '^[0-9a-f]{64}$' and channel_profile_version_id is not null and "
        "reviewed_checksum ~ '^[0-9a-f]{64}$' and destination_binding_id is not null and "
        "destination_binding_fingerprint ~ '^[0-9a-f]{64}$' and production_lane = 'LONG_FORM' and "
        "content_mode in ('SERIES_EPISODE','STANDALONE') and target_market_lineage is not null and "
        "archive_supplement is not null and archive_supplement_ref is not null and "
        "archive_supplement_hash ~ '^[0-9a-f]{64}$' and "
        "platform_video_id is not null and video_url is not null and "
        "published_at is not null and actual_publish_time is not null and "
        "actual_visibility = 'PUBLIC' and verification_status = 'VERIFIED' and "
        "publish_status = 'OBSERVED_PUBLIC' and "
        "analytics_sync_status = 'READY' and verified_event_id is not null and "
        "analytics_ready_event_id is not null and analytics_ready_at is not null)",
    )

    workflow_constraint = "ck_production_workflow_runs_production_workflow_runs_state"
    op.drop_constraint(workflow_constraint, "production_workflow_runs", type_="check")
    op.create_check_constraint(
        workflow_constraint,
        "production_workflow_runs",
        "state in ('PLANNING_PENDING','PLANNING_RUNNING','ASSIGNMENT_READY',"
        "'RESEARCH_PENDING','RESEARCH_RUNNING','PACKAGE_PENDING','PACKAGE_RUNNING',"
        "'READY_FOR_PRODUCTION','MEDIA_PENDING','MEDIA_RUNNING','VISUAL_PENDING',"
        "'VISUAL_RUNNING','RENDER_PENDING','RENDER_RUNNING','QC_PENDING','QC_RUNNING',"
        "'ARCHIVE_PENDING','ARCHIVE_RUNNING','PAUSED_AFTER_NATIVE_RENDER','FINAL_REVIEW_READY',"
        "'PRIVATE_STAGING_PENDING','PRIVATE_VERIFIED_AWAITING_PUBLIC','PUBLICATION_VERIFIED',"
        "'BLOCKED','RETRY_SCHEDULED','CANCELED','FAILED_TERMINAL','DEAD_LETTERED','SUPERSEDED')",
    )


def downgrade() -> None:
    raise RuntimeError(
        "0091 publication cutover is forward-only because it adds immutable rework and publication evidence."
    )
