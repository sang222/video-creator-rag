"""M7 manual publish handoff foundation

Revision ID: 0008_m7_publish_handoff
Revises: 0007_m6_production
Create Date: 2026-06-25 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008_m7_publish_handoff"
down_revision: str | None = "0007_m6_production"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


TARGET_PLATFORM_CHECK = "target_platform in ('YOUTUBE','YOUTUBE_SHORTS','TIKTOK','FACEBOOK','INSTAGRAM','GENERIC')"
TARGET_SURFACE_CHECK = "target_surface in ('LONG_FORM','SHORT_FORM','REELS','FEED','STORY','GENERIC')"


def upgrade() -> None:
    op.create_table(
        "publish_handoff_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("render_package_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_spec_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("media_qc_report_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accessibility_qc_report_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_manifest_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("asset_manifest_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_platform", sa.String(length=40), nullable=False),
        sa.Column("target_surface", sa.String(length=40), nullable=False),
        sa.Column("destination_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("render_variant_id", sa.String(length=120), nullable=True),
        sa.Column("package_state", sa.String(length=40), nullable=False),
        sa.Column("planned_metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("planned_disclosures", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("planned_files", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("checklist_snapshot", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("operator_instructions", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("risk_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(TARGET_PLATFORM_CHECK, name="ck_publish_handoff_packages_target_platform"),
        sa.CheckConstraint(TARGET_SURFACE_CHECK, name="ck_publish_handoff_packages_target_surface"),
        sa.CheckConstraint(
            "package_state in ('DRAFT','READY_FOR_OPERATOR','BLOCKED','CONFIRMED_PUBLISHED','CANCELLED')",
            name="ck_publish_handoff_packages_package_state",
        ),
        sa.CheckConstraint(
            "package_state != 'BLOCKED' or next_action is not null",
            name="ck_publish_handoff_packages_blocked_next_action",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["render_package_snapshot_id"], ["render_package_snapshots.id"]),
        sa.ForeignKeyConstraint(["render_spec_snapshot_id"], ["render_spec_snapshots.id"]),
        sa.ForeignKeyConstraint(["media_qc_report_id"], ["media_qc_reports.id"]),
        sa.ForeignKeyConstraint(["accessibility_qc_report_id"], ["accessibility_qc_reports.id"]),
        sa.ForeignKeyConstraint(["source_manifest_snapshot_id"], ["source_manifest_snapshots.id"]),
        sa.ForeignKeyConstraint(["asset_manifest_snapshot_id"], ["asset_manifest_snapshots.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_publish_handoff_packages_company_id", "publish_handoff_packages", ["company_id"])
    op.create_index("ix_publish_handoff_packages_channel_workspace_id", "publish_handoff_packages", ["channel_workspace_id"])
    op.create_index("ix_publish_handoff_packages_video_project_id", "publish_handoff_packages", ["video_project_id"])
    op.create_index("ix_publish_handoff_packages_render_package_id", "publish_handoff_packages", ["render_package_snapshot_id"])
    op.create_index("ix_publish_handoff_packages_state", "publish_handoff_packages", ["package_state"])
    op.create_index("ix_publish_handoff_packages_platform", "publish_handoff_packages", ["target_platform"])
    op.create_index("ix_publish_handoff_packages_created_at", "publish_handoff_packages", ["created_at"])

    op.create_table(
        "manual_publish_confirmations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publish_handoff_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_platform", sa.String(length=40), nullable=False),
        sa.Column("target_surface", sa.String(length=40), nullable=False),
        sa.Column("confirmed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmation_state", sa.String(length=40), nullable=False),
        sa.Column("actual_video_id", sa.Text(), nullable=True),
        sa.Column("actual_video_url", sa.Text(), nullable=True),
        sa.Column("actual_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("actual_disclosures", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("actual_files", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("operator_notes", sa.Text(), nullable=True),
        sa.Column("validation_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("metadata_diff", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(TARGET_PLATFORM_CHECK, name="ck_manual_publish_confirmations_target_platform"),
        sa.CheckConstraint(TARGET_SURFACE_CHECK, name="ck_manual_publish_confirmations_target_surface"),
        sa.CheckConstraint(
            "confirmation_state in ('DRAFT','SUBMITTED','ACCEPTED','REVIEW_REQUIRED','REJECTED','CANCELLED')",
            name="ck_manual_publish_confirmations_state",
        ),
        sa.ForeignKeyConstraint(["publish_handoff_package_id"], ["publish_handoff_packages.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_manual_publish_confirmations_handoff_id", "manual_publish_confirmations", ["publish_handoff_package_id"])
    op.create_index("ix_manual_publish_confirmations_channel_workspace_id", "manual_publish_confirmations", ["channel_workspace_id"])
    op.create_index("ix_manual_publish_confirmations_video_project_id", "manual_publish_confirmations", ["video_project_id"])
    op.create_index("ix_manual_publish_confirmations_state", "manual_publish_confirmations", ["confirmation_state"])
    op.create_index("ix_manual_publish_confirmations_platform_video_id", "manual_publish_confirmations", ["target_platform", "actual_video_id"])
    op.create_index("ix_manual_publish_confirmations_created_at", "manual_publish_confirmations", ["created_at"])

    op.create_table(
        "uploaded_videos",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publish_handoff_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("manual_publish_confirmation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_package_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_manifest_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rights_envelope_ref", sa.Text(), nullable=True),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("video_url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publish_status", sa.String(length=40), nullable=False),
        sa.Column("actual_metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("actual_disclosures", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("lineage_refs", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("monitoring_state", sa.String(length=40), nullable=False),
        sa.Column("operator_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("platform in ('YOUTUBE','YOUTUBE_SHORTS','TIKTOK','FACEBOOK','INSTAGRAM','GENERIC')", name="ck_uploaded_videos_platform"),
        sa.CheckConstraint("publish_status in ('CONFIRMED','REVIEW_REQUIRED','REMOVED','UNKNOWN')", name="ck_uploaded_videos_publish_status"),
        sa.CheckConstraint("monitoring_state in ('NOT_STARTED','READY_FOR_ANALYTICS','PAUSED','NOT_SUPPORTED')", name="ck_uploaded_videos_monitoring_state"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["publish_handoff_package_id"], ["publish_handoff_packages.id"]),
        sa.ForeignKeyConstraint(["manual_publish_confirmation_id"], ["manual_publish_confirmations.id"]),
        sa.ForeignKeyConstraint(["render_package_snapshot_id"], ["render_package_snapshots.id"]),
        sa.ForeignKeyConstraint(["source_manifest_snapshot_id"], ["source_manifest_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_workspace_id", "platform", "platform_video_id", name="uq_uploaded_videos_channel_platform_video"),
    )
    op.create_index("ix_uploaded_videos_company_id", "uploaded_videos", ["company_id"])
    op.create_index("ix_uploaded_videos_channel_workspace_id", "uploaded_videos", ["channel_workspace_id"])
    op.create_index("ix_uploaded_videos_video_project_id", "uploaded_videos", ["video_project_id"])
    op.create_index("ix_uploaded_videos_platform", "uploaded_videos", ["platform"])
    op.create_index("ix_uploaded_videos_published_at", "uploaded_videos", ["published_at"])
    op.create_index("ix_uploaded_videos_monitoring_state", "uploaded_videos", ["monitoring_state"])

    op.create_table(
        "uploaded_video_publication_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("video_url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("publish_status", sa.String(length=40), nullable=False),
        sa.Column("monitoring_state", sa.String(length=40), nullable=False),
        sa.Column("operator_status", sa.String(length=80), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("freshness_state", sa.String(length=40), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("platform in ('YOUTUBE','YOUTUBE_SHORTS','TIKTOK','FACEBOOK','INSTAGRAM','GENERIC')", name="ck_uploaded_video_publication_summaries_platform"),
        sa.CheckConstraint("publish_status in ('CONFIRMED','REVIEW_REQUIRED','REMOVED','UNKNOWN')", name="ck_uploaded_video_publication_summaries_publish_status"),
        sa.CheckConstraint("monitoring_state in ('NOT_STARTED','READY_FOR_ANALYTICS','PAUSED','NOT_SUPPORTED')", name="ck_uploaded_video_publication_summaries_monitoring_state"),
        sa.CheckConstraint(
            "operator_status in ('READY_FOR_ANALYTICS','NEEDS_DISCLOSURE_REVIEW','NEEDS_LICENSE_REVIEW','NEEDS_METADATA_REVIEW','CONFIRMED')",
            name="ck_uploaded_video_publication_summaries_operator_status",
        ),
        sa.CheckConstraint("freshness_state in ('NOT_STARTED','CURRENT','STALE','UNKNOWN')", name="ck_uploaded_video_publication_summaries_freshness"),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uploaded_video_id", name="uq_uploaded_video_publication_summaries_uploaded_video_id"),
    )
    op.create_index("ix_uploaded_video_publication_summaries_project_id", "uploaded_video_publication_summaries", ["video_project_id"])
    op.create_index("ix_uploaded_video_publication_summaries_channel_id", "uploaded_video_publication_summaries", ["channel_workspace_id"])
    op.create_index("ix_uploaded_video_publication_summaries_operator_status", "uploaded_video_publication_summaries", ["operator_status"])
    op.create_index("ix_uploaded_video_publication_summaries_created_at", "uploaded_video_publication_summaries", ["created_at"])


def downgrade() -> None:
    for table, indexes in [
        (
            "uploaded_video_publication_summaries",
            [
                "ix_uploaded_video_publication_summaries_created_at",
                "ix_uploaded_video_publication_summaries_operator_status",
                "ix_uploaded_video_publication_summaries_channel_id",
                "ix_uploaded_video_publication_summaries_project_id",
            ],
        ),
        (
            "uploaded_videos",
            [
                "ix_uploaded_videos_monitoring_state",
                "ix_uploaded_videos_published_at",
                "ix_uploaded_videos_platform",
                "ix_uploaded_videos_video_project_id",
                "ix_uploaded_videos_channel_workspace_id",
                "ix_uploaded_videos_company_id",
            ],
        ),
        (
            "manual_publish_confirmations",
            [
                "ix_manual_publish_confirmations_created_at",
                "ix_manual_publish_confirmations_platform_video_id",
                "ix_manual_publish_confirmations_state",
                "ix_manual_publish_confirmations_video_project_id",
                "ix_manual_publish_confirmations_channel_workspace_id",
                "ix_manual_publish_confirmations_handoff_id",
            ],
        ),
        (
            "publish_handoff_packages",
            [
                "ix_publish_handoff_packages_created_at",
                "ix_publish_handoff_packages_platform",
                "ix_publish_handoff_packages_state",
                "ix_publish_handoff_packages_render_package_id",
                "ix_publish_handoff_packages_video_project_id",
                "ix_publish_handoff_packages_channel_workspace_id",
                "ix_publish_handoff_packages_company_id",
            ],
        ),
    ]:
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
