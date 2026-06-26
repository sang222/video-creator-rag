"""M8 analytics sync and uploaded video metrics foundation

Revision ID: 0009_m8_analytics_sync
Revises: 0008_m7_publish_handoff
Create Date: 2026-06-26 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0009_m8_analytics_sync"
down_revision: str | None = "0008_m7_publish_handoff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

PLATFORM_CHECK = "platform in ('YOUTUBE','YOUTUBE_SHORTS','TIKTOK','FACEBOOK','INSTAGRAM','GENERIC')"
FRESHNESS_CHECK = "freshness_state in ('FRESH','STALE','UNKNOWN','NOT_AVAILABLE')"
CONFIDENCE_CHECK = "confidence_level in ('HIGH','MEDIUM','LOW','UNKNOWN')"


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "analytics_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("sync_mode", sa.String(length=40), nullable=False),
        sa.Column("sync_state", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_key", sa.String(length=160), nullable=True),
        sa.Column("provider_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analytics_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(PLATFORM_CHECK, name="ck_analytics_sync_runs_platform"),
        sa.CheckConstraint(
            "sync_mode in ('MOCK','MANUAL_IMPORT','CSV_IMPORT','REAL_DISABLED')",
            name="ck_analytics_sync_runs_sync_mode",
        ),
        sa.CheckConstraint(
            "sync_state in ('PENDING','RUNNING','COMPLETED','BLOCKED','FAILED','CANCELLED')",
            name="ck_analytics_sync_runs_sync_state",
        ),
        sa.CheckConstraint(
            "sync_state not in ('BLOCKED','FAILED') or (jsonb_array_length(reason_codes) > 0 and next_action is not null)",
            name="ck_analytics_sync_runs_blocked_failed_reason",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["provider_attempt_id"], ["provider_attempts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analytics_sync_runs_company_id", "analytics_sync_runs", ["company_id"])
    op.create_index("ix_analytics_sync_runs_channel_workspace_id", "analytics_sync_runs", ["channel_workspace_id"])
    op.create_index("ix_analytics_sync_runs_uploaded_video_id", "analytics_sync_runs", ["uploaded_video_id"])
    op.create_index("ix_analytics_sync_runs_video_project_id", "analytics_sync_runs", ["video_project_id"])
    op.create_index("ix_analytics_sync_runs_state", "analytics_sync_runs", ["sync_state"])
    op.create_index("ix_analytics_sync_runs_mode", "analytics_sync_runs", ["sync_mode"])
    op.create_index("ix_analytics_sync_runs_platform", "analytics_sync_runs", ["platform"])
    op.create_index("ix_analytics_sync_runs_created_at", "analytics_sync_runs", ["created_at"])

    op.create_table(
        "metric_definition_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_key", sa.String(length=120), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("metric_group", sa.String(length=40), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint("metric_group in ('REACH','ENGAGEMENT','RETENTION','TRAFFIC','AUDIENCE','REVENUE_DISABLED','OTHER')", name="ck_metric_definition_versions_group"),
        sa.CheckConstraint(PLATFORM_CHECK, name="ck_metric_definition_versions_platform"),
        sa.CheckConstraint("unit in ('COUNT','PERCENT','SECONDS','MINUTES','RATIO','CURRENCY','UNKNOWN')", name="ck_metric_definition_versions_unit"),
        sa.CheckConstraint("status in ('ACTIVE','DISABLED','DEPRECATED')", name="ck_metric_definition_versions_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("metric_key", "platform", "version", name="uq_metric_definition_versions_key_platform_version"),
    )
    op.create_index("ix_metric_definition_versions_metric_key", "metric_definition_versions", ["metric_key"])
    op.create_index("ix_metric_definition_versions_platform", "metric_definition_versions", ["platform"])
    op.create_index("ix_metric_definition_versions_group", "metric_definition_versions", ["metric_group"])
    op.create_index("ix_metric_definition_versions_status", "metric_definition_versions", ["status"])

    op.create_table(
        "metric_availability_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytics_sync_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("availability_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("unavailable_metrics", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("unknown_metrics", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("source_metric_keys", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("freshness_state", sa.String(length=40), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.CheckConstraint(PLATFORM_CHECK, name="ck_metric_availability_snapshots_platform"),
        sa.CheckConstraint(FRESHNESS_CHECK, name="ck_metric_availability_snapshots_freshness"),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_metric_availability_snapshots_confidence"),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["analytics_sync_run_id"], ["analytics_sync_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_metric_availability_snapshots_uploaded_video_id", "metric_availability_snapshots", ["uploaded_video_id"])
    op.create_index("ix_metric_availability_snapshots_sync_run_id", "metric_availability_snapshots", ["analytics_sync_run_id"])
    op.create_index("ix_metric_availability_snapshots_platform", "metric_availability_snapshots", ["platform"])
    op.create_index("ix_metric_availability_snapshots_captured_at", "metric_availability_snapshots", ["captured_at"])

    op.create_table(
        "analytics_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytics_sync_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observation_window", sa.String(length=40), nullable=False),
        sa.Column("metrics_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("normalized_metrics_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("metric_availability", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("source_metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("freshness_state", sa.String(length=40), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        _created_at(),
        sa.CheckConstraint(PLATFORM_CHECK, name="ck_analytics_snapshots_platform"),
        sa.CheckConstraint(
            "observation_window in ('T_PLUS_1H','T_PLUS_6H','T_PLUS_24H','T_PLUS_48H','T_PLUS_7D','CUSTOM','UNKNOWN')",
            name="ck_analytics_snapshots_observation_window",
        ),
        sa.CheckConstraint(FRESHNESS_CHECK, name="ck_analytics_snapshots_freshness"),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_analytics_snapshots_confidence"),
        sa.ForeignKeyConstraint(["analytics_sync_run_id"], ["analytics_sync_runs.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analytics_snapshots_sync_run_id", "analytics_snapshots", ["analytics_sync_run_id"])
    op.create_index("ix_analytics_snapshots_uploaded_video_id", "analytics_snapshots", ["uploaded_video_id"])
    op.create_index("ix_analytics_snapshots_company_id", "analytics_snapshots", ["company_id"])
    op.create_index("ix_analytics_snapshots_channel_workspace_id", "analytics_snapshots", ["channel_workspace_id"])
    op.create_index("ix_analytics_snapshots_video_project_id", "analytics_snapshots", ["video_project_id"])
    op.create_index("ix_analytics_snapshots_platform", "analytics_snapshots", ["platform"])
    op.create_index("ix_analytics_snapshots_captured_at", "analytics_snapshots", ["captured_at"])
    op.create_index("ix_analytics_snapshots_created_at", "analytics_snapshots", ["created_at"])

    op.create_table(
        "traffic_source_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytics_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("traffic_sources", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("source_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("freshness_state", sa.String(length=40), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        _created_at(),
        sa.CheckConstraint(PLATFORM_CHECK, name="ck_traffic_source_snapshots_platform"),
        sa.CheckConstraint(FRESHNESS_CHECK, name="ck_traffic_source_snapshots_freshness"),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_traffic_source_snapshots_confidence"),
        sa.ForeignKeyConstraint(["analytics_snapshot_id"], ["analytics_snapshots.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_traffic_source_snapshots_analytics_snapshot_id", "traffic_source_snapshots", ["analytics_snapshot_id"])
    op.create_index("ix_traffic_source_snapshots_uploaded_video_id", "traffic_source_snapshots", ["uploaded_video_id"])
    op.create_index("ix_traffic_source_snapshots_captured_at", "traffic_source_snapshots", ["captured_at"])

    op.create_table(
        "retention_curve_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytics_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_package_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("curve_points", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("curve_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("timeline_alignment", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("freshness_state", sa.String(length=40), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        _created_at(),
        sa.CheckConstraint(PLATFORM_CHECK, name="ck_retention_curve_snapshots_platform"),
        sa.CheckConstraint(FRESHNESS_CHECK, name="ck_retention_curve_snapshots_freshness"),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_retention_curve_snapshots_confidence"),
        sa.CheckConstraint("duration_seconds is null or duration_seconds >= 0", name="ck_retention_curve_snapshots_duration_nonnegative"),
        sa.ForeignKeyConstraint(["analytics_snapshot_id"], ["analytics_snapshots.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["render_package_snapshot_id"], ["render_package_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retention_curve_snapshots_analytics_snapshot_id", "retention_curve_snapshots", ["analytics_snapshot_id"])
    op.create_index("ix_retention_curve_snapshots_uploaded_video_id", "retention_curve_snapshots", ["uploaded_video_id"])
    op.create_index("ix_retention_curve_snapshots_video_project_id", "retention_curve_snapshots", ["video_project_id"])
    op.create_index("ix_retention_curve_snapshots_captured_at", "retention_curve_snapshots", ["captured_at"])

    op.create_table(
        "engagement_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytics_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("engagement_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("freshness_state", sa.String(length=40), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        _created_at(),
        sa.CheckConstraint(PLATFORM_CHECK, name="ck_engagement_snapshots_platform"),
        sa.CheckConstraint(FRESHNESS_CHECK, name="ck_engagement_snapshots_freshness"),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_engagement_snapshots_confidence"),
        sa.ForeignKeyConstraint(["analytics_snapshot_id"], ["analytics_snapshots.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_engagement_snapshots_analytics_snapshot_id", "engagement_snapshots", ["analytics_snapshot_id"])
    op.create_index("ix_engagement_snapshots_uploaded_video_id", "engagement_snapshots", ["uploaded_video_id"])
    op.create_index("ix_engagement_snapshots_captured_at", "engagement_snapshots", ["captured_at"])

    op.create_table(
        "uploaded_video_metrics_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("latest_analytics_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("latest_retention_curve_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("latest_traffic_source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("latest_engagement_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("latest_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("availability_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("freshness_state", sa.String(length=40), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("monitoring_state", sa.String(length=40), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=True),
        sa.Column("next_action", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(PLATFORM_CHECK, name="ck_uploaded_video_metrics_summaries_platform"),
        sa.CheckConstraint(FRESHNESS_CHECK, name="ck_uploaded_video_metrics_summaries_freshness"),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_uploaded_video_metrics_summaries_confidence"),
        sa.CheckConstraint(
            "monitoring_state in ('READY_FOR_ANALYTICS','SYNCED','PARTIAL_DATA','NO_DATA_YET','STALE','BLOCKED')",
            name="ck_uploaded_video_metrics_summaries_monitoring_state",
        ),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["latest_analytics_snapshot_id"], ["analytics_snapshots.id"]),
        sa.ForeignKeyConstraint(["latest_retention_curve_snapshot_id"], ["retention_curve_snapshots.id"]),
        sa.ForeignKeyConstraint(["latest_traffic_source_snapshot_id"], ["traffic_source_snapshots.id"]),
        sa.ForeignKeyConstraint(["latest_engagement_snapshot_id"], ["engagement_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uploaded_video_id", name="uq_uploaded_video_metrics_summaries_uploaded_video_id"),
    )
    op.create_index("ix_uploaded_video_metrics_summaries_company_id", "uploaded_video_metrics_summaries", ["company_id"])
    op.create_index("ix_uploaded_video_metrics_summaries_channel_id", "uploaded_video_metrics_summaries", ["channel_workspace_id"])
    op.create_index("ix_uploaded_video_metrics_summaries_project_id", "uploaded_video_metrics_summaries", ["video_project_id"])
    op.create_index("ix_uploaded_video_metrics_summaries_monitoring_state", "uploaded_video_metrics_summaries", ["monitoring_state"])
    op.create_index("ix_uploaded_video_metrics_summaries_latest_captured_at", "uploaded_video_metrics_summaries", ["latest_captured_at"])


def downgrade() -> None:
    for table, indexes in [
        (
            "uploaded_video_metrics_summaries",
            [
                "ix_uploaded_video_metrics_summaries_latest_captured_at",
                "ix_uploaded_video_metrics_summaries_monitoring_state",
                "ix_uploaded_video_metrics_summaries_project_id",
                "ix_uploaded_video_metrics_summaries_channel_id",
                "ix_uploaded_video_metrics_summaries_company_id",
            ],
        ),
        (
            "engagement_snapshots",
            [
                "ix_engagement_snapshots_captured_at",
                "ix_engagement_snapshots_uploaded_video_id",
                "ix_engagement_snapshots_analytics_snapshot_id",
            ],
        ),
        (
            "retention_curve_snapshots",
            [
                "ix_retention_curve_snapshots_captured_at",
                "ix_retention_curve_snapshots_video_project_id",
                "ix_retention_curve_snapshots_uploaded_video_id",
                "ix_retention_curve_snapshots_analytics_snapshot_id",
            ],
        ),
        (
            "traffic_source_snapshots",
            [
                "ix_traffic_source_snapshots_captured_at",
                "ix_traffic_source_snapshots_uploaded_video_id",
                "ix_traffic_source_snapshots_analytics_snapshot_id",
            ],
        ),
        (
            "analytics_snapshots",
            [
                "ix_analytics_snapshots_created_at",
                "ix_analytics_snapshots_captured_at",
                "ix_analytics_snapshots_platform",
                "ix_analytics_snapshots_video_project_id",
                "ix_analytics_snapshots_channel_workspace_id",
                "ix_analytics_snapshots_company_id",
                "ix_analytics_snapshots_uploaded_video_id",
                "ix_analytics_snapshots_sync_run_id",
            ],
        ),
        (
            "metric_availability_snapshots",
            [
                "ix_metric_availability_snapshots_captured_at",
                "ix_metric_availability_snapshots_platform",
                "ix_metric_availability_snapshots_sync_run_id",
                "ix_metric_availability_snapshots_uploaded_video_id",
            ],
        ),
        (
            "metric_definition_versions",
            [
                "ix_metric_definition_versions_status",
                "ix_metric_definition_versions_group",
                "ix_metric_definition_versions_platform",
                "ix_metric_definition_versions_metric_key",
            ],
        ),
        (
            "analytics_sync_runs",
            [
                "ix_analytics_sync_runs_created_at",
                "ix_analytics_sync_runs_platform",
                "ix_analytics_sync_runs_mode",
                "ix_analytics_sync_runs_state",
                "ix_analytics_sync_runs_video_project_id",
                "ix_analytics_sync_runs_uploaded_video_id",
                "ix_analytics_sync_runs_channel_workspace_id",
                "ix_analytics_sync_runs_company_id",
            ],
        ),
    ]:
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
