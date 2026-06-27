"""M10.3 YouTube public and owner analytics follow patch

Revision ID: 0014_m10_3_youtube_follow
Revises: 0013_m10_2_provider_routing
Create Date: 2026-06-27 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0014_m10_3_youtube_follow"
down_revision: str | None = "0013_m10_2_provider_routing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

AUTH_MODE_CHECK = "auth_mode in ('API_KEY','OAUTH2')"
PROVIDER_KEY_CHECK = "provider_key in ('YOUTUBE_DATA_API','YOUTUBE_ANALYTICS_API')"
CONNECTION_STATE_CHECK = (
    "connection_state in ('NOT_CONFIGURED','CONFIGURED','CONNECTED','NEEDS_REAUTH','REVOKED','ERROR')"
)
OAUTH_SESSION_STATUS_CHECK = "status in ('STARTED','CALLBACK_RECEIVED','TOKEN_EXCHANGED','FAILED','CANCELLED')"
PUBLIC_RUN_STATE_CHECK = "run_state in ('PENDING','RUNNING','COMPLETED','FAILED','SKIPPED')"
OWNER_RUN_STATE_CHECK = "run_state in ('PENDING','RUNNING','COMPLETED','FAILED','SKIPPED','NEEDS_AUTH')"
SYNC_SOURCE_CHECK = "source in ('YOUTUBE_DATA_API','YOUTUBE_ANALYTICS_API')"
AVAILABILITY_CHECK = "views_availability in ('AVAILABLE','UNKNOWN','NOT_AVAILABLE') and likes_availability in ('AVAILABLE','UNKNOWN','NOT_AVAILABLE') and comments_availability in ('AVAILABLE','UNKNOWN','NOT_AVAILABLE')"
FRESHNESS_CHECK = "freshness_state in ('FRESH','STALE','UNKNOWN')"
PUBLIC_SYNC_STATUS_CHECK = "sync_status in ('OK','FAILED','PARTIAL','NOT_CONFIGURED','NOT_FOUND','UNAVAILABLE')"
OWNER_SYNC_STATUS_CHECK = "sync_status in ('OK','FAILED','PARTIAL','NEEDS_AUTH','NOT_CONFIGURED','NOT_FOUND','UNAVAILABLE')"
M8_SYNC_MODE_CHECK = (
    "sync_mode in ('MOCK','MANUAL_IMPORT','CSV_IMPORT','REAL_DISABLED','YOUTUBE_PUBLIC_MONITOR','YOUTUBE_OWNER_ANALYTICS')"
)


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _uuid(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    op.drop_constraint("ck_analytics_sync_runs_sync_mode", "analytics_sync_runs", type_="check")
    op.create_check_constraint("ck_analytics_sync_runs_sync_mode", "analytics_sync_runs", M8_SYNC_MODE_CHECK)

    op.create_table(
        "youtube_monitoring_credentials",
        _uuid("id", nullable=False),
        _uuid("company_id"),
        _uuid("channel_workspace_id"),
        _uuid("credential_reference_id", nullable=False),
        sa.Column("auth_mode", sa.String(length=40), nullable=False),
        sa.Column("provider_key", sa.String(length=80), nullable=False),
        sa.Column("connection_state", sa.String(length=40), server_default="NOT_CONFIGURED", nullable=False),
        sa.Column("scopes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("token_metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(AUTH_MODE_CHECK, name="ck_youtube_monitoring_credentials_auth_mode"),
        sa.CheckConstraint(PROVIDER_KEY_CHECK, name="ck_youtube_monitoring_credentials_provider"),
        sa.CheckConstraint(CONNECTION_STATE_CHECK, name="ck_youtube_monitoring_credentials_state"),
        sa.CheckConstraint("jsonb_typeof(scopes) = 'array'", name="ck_youtube_monitoring_credentials_scopes_array"),
        sa.CheckConstraint("jsonb_typeof(token_metadata) = 'object'", name="ck_youtube_monitoring_credentials_token_metadata_object"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["credential_reference_id"], ["credential_references.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_youtube_monitoring_credentials_company", "youtube_monitoring_credentials", ["company_id"])
    op.create_index("ix_youtube_monitoring_credentials_channel", "youtube_monitoring_credentials", ["channel_workspace_id"])
    op.create_index("ix_youtube_monitoring_credentials_reference", "youtube_monitoring_credentials", ["credential_reference_id"])
    op.create_index("ix_youtube_monitoring_credentials_provider", "youtube_monitoring_credentials", ["provider_key"])
    op.create_index("ix_youtube_monitoring_credentials_state", "youtube_monitoring_credentials", ["connection_state"])

    op.create_table(
        "youtube_oauth_sessions",
        _uuid("id", nullable=False),
        _uuid("company_id"),
        _uuid("channel_workspace_id"),
        sa.Column("state_token_hash", sa.String(length=128), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scopes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="STARTED", nullable=False),
        _uuid("credential_reference_id"),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(OAUTH_SESSION_STATUS_CHECK, name="ck_youtube_oauth_sessions_status"),
        sa.CheckConstraint("jsonb_typeof(scopes) = 'array'", name="ck_youtube_oauth_sessions_scopes_array"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["credential_reference_id"], ["credential_references.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_youtube_oauth_sessions_company", "youtube_oauth_sessions", ["company_id"])
    op.create_index("ix_youtube_oauth_sessions_channel", "youtube_oauth_sessions", ["channel_workspace_id"])
    op.create_index("ix_youtube_oauth_sessions_state_hash", "youtube_oauth_sessions", ["state_token_hash"])
    op.create_index("ix_youtube_oauth_sessions_status", "youtube_oauth_sessions", ["status"])
    op.create_index("ix_youtube_oauth_sessions_created_at", "youtube_oauth_sessions", ["created_at"])

    op.create_table(
        "uploaded_video_youtube_public_monitor_snapshots",
        _uuid("id", nullable=False),
        _uuid("uploaded_video_id", nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id"),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments", sa.BigInteger(), nullable=True),
        sa.Column("youtube_title", sa.Text(), nullable=True),
        sa.Column("youtube_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("youtube_channel_id", sa.Text(), nullable=True),
        sa.Column("youtube_channel_title", sa.Text(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("duration_seconds", sa.BigInteger(), nullable=True),
        sa.Column("definition", sa.String(length=40), nullable=True),
        sa.Column("caption_status", sa.String(length=40), nullable=True),
        sa.Column("privacy_status", sa.String(length=40), nullable=True),
        sa.Column("public_stats_viewable", sa.Boolean(), nullable=True),
        sa.Column("title_matches_confirmed_metadata", sa.Boolean(), nullable=True),
        sa.Column("duration_matches_render_package", sa.Boolean(), nullable=True),
        sa.Column("views_availability", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("likes_availability", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("comments_availability", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("freshness_state", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("sync_status", sa.String(length=40), server_default="OK", nullable=False),
        sa.Column("sync_error_code", sa.String(length=160), nullable=True),
        sa.Column("learning_authority", sa.String(length=40), server_default="WEAK", nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unknown_metrics", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("unavailable_metrics", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint(AVAILABILITY_CHECK, name="ck_youtube_public_snapshots_availability"),
        sa.CheckConstraint(FRESHNESS_CHECK, name="ck_youtube_public_snapshots_freshness"),
        sa.CheckConstraint(PUBLIC_SYNC_STATUS_CHECK, name="ck_youtube_public_snapshots_status"),
        sa.CheckConstraint("learning_authority = 'WEAK'", name="ck_youtube_public_snapshots_authority"),
        sa.CheckConstraint("jsonb_typeof(unknown_metrics) = 'array'", name="ck_youtube_public_snapshots_unknown_array"),
        sa.CheckConstraint("jsonb_typeof(unavailable_metrics) = 'array'", name="ck_youtube_public_snapshots_unavailable_array"),
        sa.CheckConstraint("jsonb_typeof(technical_appendix) = 'object'", name="ck_youtube_public_snapshots_appendix_object"),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_youtube_public_snapshots_uploaded_video", "uploaded_video_youtube_public_monitor_snapshots", ["uploaded_video_id"])
    op.create_index("ix_youtube_public_snapshots_company", "uploaded_video_youtube_public_monitor_snapshots", ["company_id"])
    op.create_index("ix_youtube_public_snapshots_channel", "uploaded_video_youtube_public_monitor_snapshots", ["channel_workspace_id"])
    op.create_index("ix_youtube_public_snapshots_platform_video", "uploaded_video_youtube_public_monitor_snapshots", ["platform_video_id"])
    op.create_index("ix_youtube_public_snapshots_last_synced", "uploaded_video_youtube_public_monitor_snapshots", ["last_synced_at"])
    op.create_index("ix_youtube_public_snapshots_status", "uploaded_video_youtube_public_monitor_snapshots", ["sync_status"])

    op.create_table(
        "uploaded_video_youtube_owner_analytics_snapshots",
        _uuid("id", nullable=False),
        _uuid("uploaded_video_id", nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id"),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("analytics_start_date", sa.Date(), nullable=False),
        sa.Column("analytics_end_date", sa.Date(), nullable=False),
        sa.Column("learning_authority", sa.String(length=40), server_default="STRONG", nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments", sa.BigInteger(), nullable=True),
        sa.Column("impressions", sa.BigInteger(), nullable=True),
        sa.Column("impression_click_through_rate", sa.Float(), nullable=True),
        sa.Column("average_view_duration_seconds", sa.Float(), nullable=True),
        sa.Column("average_view_percentage", sa.Float(), nullable=True),
        sa.Column("estimated_minutes_watched", sa.Float(), nullable=True),
        sa.Column("subscribers_gained", sa.BigInteger(), nullable=True),
        sa.Column("subscribers_lost", sa.BigInteger(), nullable=True),
        sa.Column("metric_availability", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("freshness_state", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("sync_status", sa.String(length=40), server_default="OK", nullable=False),
        sa.Column("sync_error_code", sa.String(length=160), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint("analytics_end_date >= analytics_start_date", name="ck_youtube_owner_snapshots_date_range"),
        sa.CheckConstraint(FRESHNESS_CHECK, name="ck_youtube_owner_snapshots_freshness"),
        sa.CheckConstraint(OWNER_SYNC_STATUS_CHECK, name="ck_youtube_owner_snapshots_status"),
        sa.CheckConstraint("learning_authority = 'STRONG'", name="ck_youtube_owner_snapshots_authority"),
        sa.CheckConstraint("jsonb_typeof(metric_availability) = 'object'", name="ck_youtube_owner_snapshots_availability_object"),
        sa.CheckConstraint("jsonb_typeof(technical_appendix) = 'object'", name="ck_youtube_owner_snapshots_appendix_object"),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_youtube_owner_snapshots_uploaded_video", "uploaded_video_youtube_owner_analytics_snapshots", ["uploaded_video_id"])
    op.create_index("ix_youtube_owner_snapshots_company", "uploaded_video_youtube_owner_analytics_snapshots", ["company_id"])
    op.create_index("ix_youtube_owner_snapshots_channel", "uploaded_video_youtube_owner_analytics_snapshots", ["channel_workspace_id"])
    op.create_index("ix_youtube_owner_snapshots_platform_video", "uploaded_video_youtube_owner_analytics_snapshots", ["platform_video_id"])
    op.create_index("ix_youtube_owner_snapshots_last_synced", "uploaded_video_youtube_owner_analytics_snapshots", ["last_synced_at"])
    op.create_index("ix_youtube_owner_snapshots_status", "uploaded_video_youtube_owner_analytics_snapshots", ["sync_status"])

    op.create_table(
        "youtube_public_sync_runs",
        _uuid("id", nullable=False),
        _uuid("uploaded_video_id", nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id"),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("run_state", sa.String(length=40), server_default="PENDING", nullable=False),
        sa.Column("source", sa.String(length=80), server_default="YOUTUBE_DATA_API", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metrics_found", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _uuid("created_snapshot_id"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(PUBLIC_RUN_STATE_CHECK, name="ck_youtube_public_sync_runs_state"),
        sa.CheckConstraint("source = 'YOUTUBE_DATA_API'", name="ck_youtube_public_sync_runs_source"),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["created_snapshot_id"], ["uploaded_video_youtube_public_monitor_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_youtube_public_sync_runs_uploaded_video", "youtube_public_sync_runs", ["uploaded_video_id"])
    op.create_index("ix_youtube_public_sync_runs_company", "youtube_public_sync_runs", ["company_id"])
    op.create_index("ix_youtube_public_sync_runs_channel", "youtube_public_sync_runs", ["channel_workspace_id"])
    op.create_index("ix_youtube_public_sync_runs_platform_video", "youtube_public_sync_runs", ["platform_video_id"])
    op.create_index("ix_youtube_public_sync_runs_state", "youtube_public_sync_runs", ["run_state"])
    op.create_index("ix_youtube_public_sync_runs_created_at", "youtube_public_sync_runs", ["created_at"])

    op.create_table(
        "youtube_owner_analytics_sync_runs",
        _uuid("id", nullable=False),
        _uuid("uploaded_video_id", nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id"),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        _uuid("credential_reference_id"),
        sa.Column("run_state", sa.String(length=40), server_default="PENDING", nullable=False),
        sa.Column("source", sa.String(length=80), server_default="YOUTUBE_ANALYTICS_API", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metrics_found", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _uuid("created_snapshot_id"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(OWNER_RUN_STATE_CHECK, name="ck_youtube_owner_sync_runs_state"),
        sa.CheckConstraint("source = 'YOUTUBE_ANALYTICS_API'", name="ck_youtube_owner_sync_runs_source"),
        sa.CheckConstraint("end_date >= start_date", name="ck_youtube_owner_sync_runs_date_range"),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["credential_reference_id"], ["credential_references.id"]),
        sa.ForeignKeyConstraint(["created_snapshot_id"], ["uploaded_video_youtube_owner_analytics_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_youtube_owner_sync_runs_uploaded_video", "youtube_owner_analytics_sync_runs", ["uploaded_video_id"])
    op.create_index("ix_youtube_owner_sync_runs_company", "youtube_owner_analytics_sync_runs", ["company_id"])
    op.create_index("ix_youtube_owner_sync_runs_channel", "youtube_owner_analytics_sync_runs", ["channel_workspace_id"])
    op.create_index("ix_youtube_owner_sync_runs_platform_video", "youtube_owner_analytics_sync_runs", ["platform_video_id"])
    op.create_index("ix_youtube_owner_sync_runs_state", "youtube_owner_analytics_sync_runs", ["run_state"])
    op.create_index("ix_youtube_owner_sync_runs_created_at", "youtube_owner_analytics_sync_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("youtube_owner_analytics_sync_runs")
    op.drop_table("youtube_public_sync_runs")
    op.drop_table("uploaded_video_youtube_owner_analytics_snapshots")
    op.drop_table("uploaded_video_youtube_public_monitor_snapshots")
    op.drop_table("youtube_oauth_sessions")
    op.drop_table("youtube_monitoring_credentials")
    op.drop_constraint("ck_analytics_sync_runs_sync_mode", "analytics_sync_runs", type_="check")
    op.create_check_constraint(
        "ck_analytics_sync_runs_sync_mode",
        "analytics_sync_runs",
        "sync_mode in ('MOCK','MANUAL_IMPORT','CSV_IMPORT','REAL_DISABLED')",
    )
