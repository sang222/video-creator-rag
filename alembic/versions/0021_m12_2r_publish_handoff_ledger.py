"""M12.2R publish handoff ledger and uploaded video backfill

Revision ID: 0021_m12_2r_handoff_ledger
Revises: 0020_m12_2_video_package
Create Date: 2026-06-28 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0021_m12_2r_handoff_ledger"
down_revision: str | None = "0020_m12_2_video_package"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
M12_2R_HUMAN_UPLOAD_TASK_STATE_CHECK = (
    "task_state in ("
    "'READY','UPLOADED','NEEDS_FIX','SKIPPED','CANCELLED',"
    "'READY_FOR_HUMAN_UPLOAD','HUMAN_UPLOAD_IN_PROGRESS','UPLOADED_WAITING_BACKFILL',"
    "'BACKFILLED_WAITING_VERIFICATION','UPLOADED_VERIFIED','UPLOADED_UNVERIFIED','BLOCKED'"
    ")"
)
LEGACY_HUMAN_UPLOAD_TASK_STATE_CHECK = "task_state in ('READY','UPLOADED','NEEDS_FIX','SKIPPED','CANCELLED')"


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.execute("ALTER TABLE human_upload_tasks DROP CONSTRAINT IF EXISTS ck_human_upload_tasks_ck_human_upload_tasks_state")
    op.execute("ALTER TABLE human_upload_tasks DROP CONSTRAINT IF EXISTS ck_human_upload_tasks_state")
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "ADD CONSTRAINT ck_human_upload_tasks_ck_human_upload_tasks_state "
        f"CHECK ({M12_2R_HUMAN_UPLOAD_TASK_STATE_CHECK})"
    )
    op.alter_column("human_upload_tasks", "upload_card_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.add_column("human_upload_tasks", sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("human_upload_tasks", sa.Column("first_scripted_video_package_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("human_upload_tasks", sa.Column("publish_package_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("human_upload_tasks", sa.Column("destination", sa.String(length=40), server_default="YOUTUBE", nullable=False))
    op.add_column("human_upload_tasks", sa.Column("upload_card_ref", sa.Text(), nullable=True))
    op.add_column("human_upload_tasks", sa.Column("title_snapshot", sa.Text(), nullable=True))
    op.add_column("human_upload_tasks", sa.Column("description_snapshot", sa.Text(), nullable=True))
    op.add_column("human_upload_tasks", sa.Column("thumbnail_ref", JSONB, nullable=True))
    op.add_column("human_upload_tasks", sa.Column("subtitle_refs", JSONB, server_default=_jsonb_array(), nullable=False))
    op.add_column("human_upload_tasks", sa.Column("required_assets", JSONB, server_default=_jsonb_array(), nullable=False))
    op.add_column("human_upload_tasks", sa.Column("checklist", JSONB, server_default=_jsonb_array(), nullable=False))
    op.add_column("human_upload_tasks", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("human_upload_tasks", sa.Column("blocked_reason", sa.Text(), nullable=True))
    op.add_column("human_upload_tasks", sa.Column("operator_note", sa.Text(), nullable=True))
    op.create_foreign_key("fk_human_upload_tasks_video_project_id", "human_upload_tasks", "video_projects", ["video_project_id"], ["id"])
    op.create_foreign_key(
        "fk_human_upload_tasks_first_package_id",
        "human_upload_tasks",
        "first_scripted_video_packages",
        ["first_scripted_video_package_id"],
        ["id"],
    )
    op.create_foreign_key("fk_human_upload_tasks_publish_package_id", "human_upload_tasks", "publish_handoff_packages", ["publish_package_id"], ["id"])
    op.create_index("ix_human_upload_tasks_video_project_id", "human_upload_tasks", ["video_project_id"])
    op.create_index("ix_human_upload_tasks_first_package_id", "human_upload_tasks", ["first_scripted_video_package_id"])
    op.create_index("ix_human_upload_tasks_publish_package_id", "human_upload_tasks", ["publish_package_id"])
    op.create_index("ix_human_upload_tasks_destination", "human_upload_tasks", ["destination"])

    op.alter_column("uploaded_videos", "video_project_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.alter_column("uploaded_videos", "policy_snapshot_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.alter_column("uploaded_videos", "publish_handoff_package_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.alter_column("uploaded_videos", "manual_publish_confirmation_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.alter_column("uploaded_videos", "render_package_snapshot_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.add_column("uploaded_videos", sa.Column("first_scripted_video_package_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("uploaded_videos", sa.Column("human_upload_task_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("uploaded_videos", sa.Column("destination", sa.String(length=40), server_default="YOUTUBE", nullable=False))
    op.add_column("uploaded_videos", sa.Column("actual_title", sa.Text(), nullable=True))
    op.add_column("uploaded_videos", sa.Column("actual_visibility", sa.String(length=40), server_default="UNKNOWN", nullable=False))
    op.add_column("uploaded_videos", sa.Column("actual_publish_time", sa.DateTime(timezone=True), nullable=True))
    op.add_column("uploaded_videos", sa.Column("actual_upload_time", sa.DateTime(timezone=True), nullable=True))
    op.add_column("uploaded_videos", sa.Column("playlist_id", sa.Text(), nullable=True))
    op.add_column("uploaded_videos", sa.Column("thumbnail_uploaded", sa.Boolean(), nullable=True))
    op.add_column("uploaded_videos", sa.Column("subtitles_uploaded", sa.Boolean(), nullable=True))
    op.add_column("uploaded_videos", sa.Column("description_modified_from_package", sa.Boolean(), nullable=True))
    op.add_column("uploaded_videos", sa.Column("package_metadata_diff", JSONB, nullable=True))
    op.add_column("uploaded_videos", sa.Column("verification_status", sa.String(length=40), server_default="NOT_VERIFIED", nullable=False))
    op.add_column("uploaded_videos", sa.Column("analytics_sync_status", sa.String(length=40), server_default="NOT_STARTED", nullable=False))
    op.add_column("uploaded_videos", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("uploaded_videos", sa.Column("last_analytics_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("uploaded_videos", sa.Column("operator_note", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_uploaded_videos_first_package_id",
        "uploaded_videos",
        "first_scripted_video_packages",
        ["first_scripted_video_package_id"],
        ["id"],
    )
    op.create_foreign_key("fk_uploaded_videos_human_upload_task_id", "uploaded_videos", "human_upload_tasks", ["human_upload_task_id"], ["id"])
    op.create_index("ix_uploaded_videos_first_package_id", "uploaded_videos", ["first_scripted_video_package_id"])
    op.create_index("ix_uploaded_videos_human_upload_task_id", "uploaded_videos", ["human_upload_task_id"])
    op.create_index("ix_uploaded_videos_destination", "uploaded_videos", ["destination"])
    op.create_index("ix_uploaded_videos_verification_status", "uploaded_videos", ["verification_status"])
    op.create_index("ix_uploaded_videos_analytics_sync_status", "uploaded_videos", ["analytics_sync_status"])

    op.create_table(
        "uploaded_video_backfill_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("human_upload_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_url_or_video_id", sa.Text(), nullable=False),
        sa.Column("parsed_video_id", sa.Text(), nullable=True),
        sa.Column("parse_status", sa.String(length=40), nullable=False),
        sa.Column("previous_status", sa.String(length=80), nullable=True),
        sa.Column("new_status", sa.String(length=80), nullable=True),
        sa.Column("operator_note", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["human_upload_task_id"], ["human_upload_tasks.id"]),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_uploaded_video_backfill_events_uploaded_video_id", "uploaded_video_backfill_events", ["uploaded_video_id"])
    op.create_index("ix_uploaded_video_backfill_events_task_id", "uploaded_video_backfill_events", ["human_upload_task_id"])
    op.create_index("ix_uploaded_video_backfill_events_channel_id", "uploaded_video_backfill_events", ["channel_id"])
    op.create_index("ix_uploaded_video_backfill_events_parse_status", "uploaded_video_backfill_events", ["parse_status"])
    op.create_index("ix_uploaded_video_backfill_events_created_at", "uploaded_video_backfill_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_uploaded_video_backfill_events_created_at", table_name="uploaded_video_backfill_events")
    op.drop_index("ix_uploaded_video_backfill_events_parse_status", table_name="uploaded_video_backfill_events")
    op.drop_index("ix_uploaded_video_backfill_events_channel_id", table_name="uploaded_video_backfill_events")
    op.drop_index("ix_uploaded_video_backfill_events_task_id", table_name="uploaded_video_backfill_events")
    op.drop_index("ix_uploaded_video_backfill_events_uploaded_video_id", table_name="uploaded_video_backfill_events")
    op.drop_table("uploaded_video_backfill_events")

    op.drop_index("ix_uploaded_videos_analytics_sync_status", table_name="uploaded_videos")
    op.drop_index("ix_uploaded_videos_verification_status", table_name="uploaded_videos")
    op.drop_index("ix_uploaded_videos_destination", table_name="uploaded_videos")
    op.drop_index("ix_uploaded_videos_human_upload_task_id", table_name="uploaded_videos")
    op.drop_index("ix_uploaded_videos_first_package_id", table_name="uploaded_videos")
    op.drop_constraint("fk_uploaded_videos_human_upload_task_id", "uploaded_videos", type_="foreignkey")
    op.drop_constraint("fk_uploaded_videos_first_package_id", "uploaded_videos", type_="foreignkey")
    for column in (
        "operator_note",
        "last_analytics_sync_at",
        "last_verified_at",
        "analytics_sync_status",
        "verification_status",
        "package_metadata_diff",
        "description_modified_from_package",
        "subtitles_uploaded",
        "thumbnail_uploaded",
        "playlist_id",
        "actual_upload_time",
        "actual_publish_time",
        "actual_visibility",
        "actual_title",
        "destination",
        "human_upload_task_id",
        "first_scripted_video_package_id",
    ):
        op.drop_column("uploaded_videos", column)
    op.alter_column("uploaded_videos", "render_package_snapshot_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.alter_column("uploaded_videos", "manual_publish_confirmation_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.alter_column("uploaded_videos", "publish_handoff_package_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.alter_column("uploaded_videos", "policy_snapshot_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.alter_column("uploaded_videos", "video_project_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)

    op.drop_index("ix_human_upload_tasks_destination", table_name="human_upload_tasks")
    op.drop_index("ix_human_upload_tasks_publish_package_id", table_name="human_upload_tasks")
    op.drop_index("ix_human_upload_tasks_first_package_id", table_name="human_upload_tasks")
    op.drop_index("ix_human_upload_tasks_video_project_id", table_name="human_upload_tasks")
    op.drop_constraint("fk_human_upload_tasks_publish_package_id", "human_upload_tasks", type_="foreignkey")
    op.drop_constraint("fk_human_upload_tasks_first_package_id", "human_upload_tasks", type_="foreignkey")
    op.drop_constraint("fk_human_upload_tasks_video_project_id", "human_upload_tasks", type_="foreignkey")
    for column in (
        "operator_note",
        "blocked_reason",
        "completed_at",
        "checklist",
        "required_assets",
        "subtitle_refs",
        "thumbnail_ref",
        "description_snapshot",
        "title_snapshot",
        "upload_card_ref",
        "destination",
        "publish_package_id",
        "first_scripted_video_package_id",
        "video_project_id",
    ):
        op.drop_column("human_upload_tasks", column)
    op.execute("ALTER TABLE human_upload_tasks DROP CONSTRAINT IF EXISTS ck_human_upload_tasks_ck_human_upload_tasks_state")
    op.execute("ALTER TABLE human_upload_tasks DROP CONSTRAINT IF EXISTS ck_human_upload_tasks_state")
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "ADD CONSTRAINT ck_human_upload_tasks_ck_human_upload_tasks_state "
        f"CHECK ({LEGACY_HUMAN_UPLOAD_TASK_STATE_CHECK})"
    )
    op.alter_column("human_upload_tasks", "upload_card_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
