"""M11.1 localization auth and publish timing

Revision ID: 0017_m11_1_localization
Revises: 0016_m11_operator_dashboard
Create Date: 2026-06-27 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0017_m11_1_localization"
down_revision: str | None = "0016_m11_operator_dashboard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

OPERATOR_ROLE_CHECK = (
    "role in ('OWNER_ADMIN','CHANNEL_MANAGER','PRODUCER','REVIEWER','PUBLISHER','ANALYST',"
    "'PROCUREMENT_OPERATOR','COMPLIANCE_REVIEWER','LEARNING_REVIEWER','READ_ONLY')"
)
TRANSLATION_MODE_CHECK = "translation_mode in ('HUMAN_REVIEW_REQUIRED','MACHINE_DRAFT_ONLY','DISABLED')"
TRANSLATION_STATUS_CHECK = "translation_status in ('DRAFT','MACHINE_DRAFT','NEEDS_HUMAN_REVIEW','APPROVED','REJECTED','BLOCKED')"
SUBTITLE_REVIEW_CHECK = "human_review_status in ('NOT_REQUIRED','NEEDS_REVIEW','APPROVED','REJECTED','BLOCKED')"
METADATA_REVIEW_CHECK = "human_review_status in ('DRAFT','NEEDS_HUMAN_REVIEW','APPROVED','REJECTED','BLOCKED')"
TIMING_SOURCE_CHECK = "source in ('CHANNEL_CONFIG','HUMAN_OVERRIDE','ANALYTICS_OBSERVED_LATER')"
TIMING_CONFIDENCE_CHECK = "confidence_label in ('CONFIGURED','OBSERVED','UNKNOWN')"


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
    op.add_column("channel_workspaces", sa.Column("primary_region", sa.String(length=16), nullable=True))
    op.add_column("channel_workspaces", sa.Column("primary_timezone", sa.Text(), server_default="UTC", nullable=False))
    op.add_column("channel_workspaces", sa.Column("target_subtitle_languages", JSONB, server_default=_jsonb_array(), nullable=False))
    op.add_column("channel_workspaces", sa.Column("target_metadata_languages", JSONB, server_default=_jsonb_array(), nullable=False))
    op.add_column("channel_workspaces", sa.Column("target_regions", JSONB, server_default=_jsonb_array(), nullable=False))
    op.add_column("channel_workspaces", sa.Column("translation_mode", sa.String(length=40), server_default="DISABLED", nullable=False))
    op.add_column("channel_workspaces", sa.Column("localization_required_for_publish", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("channel_workspaces", sa.Column("localized_metadata_required", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_check_constraint("ck_channel_workspaces_translation_mode", "channel_workspaces", TRANSLATION_MODE_CHECK)
    op.create_check_constraint("ck_channel_workspaces_target_subtitle_languages_array", "channel_workspaces", "jsonb_typeof(target_subtitle_languages) = 'array'")
    op.create_check_constraint("ck_channel_workspaces_target_metadata_languages_array", "channel_workspaces", "jsonb_typeof(target_metadata_languages) = 'array'")
    op.create_check_constraint("ck_channel_workspaces_target_regions_array", "channel_workspaces", "jsonb_typeof(target_regions) = 'array'")

    op.create_table(
        "operator_users",
        _uuid("id", nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="ACTIVE", nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(OPERATOR_ROLE_CHECK, name="ck_operator_users_role"),
        sa.CheckConstraint("status in ('ACTIVE','DISABLED')", name="ck_operator_users_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_operator_users_email"),
    )
    op.create_index("ix_operator_users_email", "operator_users", ["email"])
    op.create_index("ix_operator_users_role", "operator_users", ["role"])
    op.create_index("ix_operator_users_status", "operator_users", ["status"])

    op.create_table(
        "operator_auth_sessions",
        _uuid("id", nullable=False),
        _uuid("user_id", nullable=False),
        sa.Column("session_token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["operator_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_token_hash", name="uq_operator_auth_sessions_token_hash"),
    )
    op.create_index("ix_operator_auth_sessions_user", "operator_auth_sessions", ["user_id"])
    op.create_index("ix_operator_auth_sessions_expires_at", "operator_auth_sessions", ["expires_at"])
    op.create_index("ix_operator_auth_sessions_revoked_at", "operator_auth_sessions", ["revoked_at"])

    op.create_table(
        "localized_subtitle_packages",
        _uuid("id", nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id", nullable=False),
        _uuid("video_project_id", nullable=False),
        _uuid("base_caption_track_id"),
        sa.Column("source_language", sa.String(length=16), nullable=False),
        sa.Column("target_language", sa.String(length=16), nullable=False),
        _uuid("srt_cloud_media_ref_id"),
        _uuid("vtt_cloud_media_ref_id"),
        sa.Column("translation_status", sa.String(length=40), nullable=False),
        sa.Column("human_review_status", sa.String(length=40), nullable=False),
        _uuid("reviewer_id"),
        sa.Column("quality_notes", sa.Text(), nullable=True),
        sa.Column("disclosure_notes", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(TRANSLATION_STATUS_CHECK, name="ck_localized_subtitle_translation_status"),
        sa.CheckConstraint(SUBTITLE_REVIEW_CHECK, name="ck_localized_subtitle_review_status"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["base_caption_track_id"], ["caption_track_snapshots.id"]),
        sa.ForeignKeyConstraint(["srt_cloud_media_ref_id"], ["cloud_media_refs.id"]),
        sa.ForeignKeyConstraint(["vtt_cloud_media_ref_id"], ["cloud_media_refs.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["operator_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_localized_subtitle_packages_company", "localized_subtitle_packages", ["company_id"])
    op.create_index("ix_localized_subtitle_packages_channel", "localized_subtitle_packages", ["channel_workspace_id"])
    op.create_index("ix_localized_subtitle_packages_project", "localized_subtitle_packages", ["video_project_id"])
    op.create_index("ix_localized_subtitle_packages_language", "localized_subtitle_packages", ["target_language"])
    op.create_index("ix_localized_subtitle_packages_translation", "localized_subtitle_packages", ["translation_status"])
    op.create_index("ix_localized_subtitle_packages_review", "localized_subtitle_packages", ["human_review_status"])

    op.create_table(
        "localized_metadata_packages",
        _uuid("id", nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id", nullable=False),
        _uuid("video_project_id", nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=True),
        sa.Column("localized_title", sa.Text(), nullable=False),
        sa.Column("localized_description", sa.Text(), nullable=False),
        sa.Column("localized_tags", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("localized_disclosure_notes", sa.Text(), nullable=True),
        sa.Column("localized_cta_text", sa.Text(), nullable=True),
        sa.Column("human_review_status", sa.String(length=40), nullable=False),
        _uuid("reviewer_id"),
        sa.Column("quality_notes", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(METADATA_REVIEW_CHECK, name="ck_localized_metadata_review_status"),
        sa.CheckConstraint("jsonb_typeof(localized_tags) = 'array'", name="ck_localized_metadata_tags_array"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["operator_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_localized_metadata_packages_company", "localized_metadata_packages", ["company_id"])
    op.create_index("ix_localized_metadata_packages_channel", "localized_metadata_packages", ["channel_workspace_id"])
    op.create_index("ix_localized_metadata_packages_project", "localized_metadata_packages", ["video_project_id"])
    op.create_index("ix_localized_metadata_packages_language", "localized_metadata_packages", ["language"])
    op.create_index("ix_localized_metadata_packages_review", "localized_metadata_packages", ["human_review_status"])

    op.create_table(
        "channel_publish_timing_policies",
        _uuid("id", nullable=False),
        _uuid("channel_workspace_id", nullable=False),
        sa.Column("primary_timezone", sa.Text(), nullable=False),
        sa.Column("operator_timezone", sa.Text(), nullable=True),
        sa.Column("target_regions", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("primary_audience_country", sa.String(length=16), nullable=True),
        sa.Column("preferred_publish_windows", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("avoid_publish_windows", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("publish_days", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("weekend_allowed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("jsonb_typeof(target_regions) = 'array'", name="ck_publish_timing_target_regions_array"),
        sa.CheckConstraint("jsonb_typeof(preferred_publish_windows) = 'array'", name="ck_publish_timing_preferred_windows_array"),
        sa.CheckConstraint("jsonb_typeof(avoid_publish_windows) = 'array'", name="ck_publish_timing_avoid_windows_array"),
        sa.CheckConstraint("jsonb_typeof(publish_days) = 'array'", name="ck_publish_timing_days_array"),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_workspace_id", name="uq_channel_publish_timing_policies_channel"),
    )
    op.create_index("ix_channel_publish_timing_policies_channel", "channel_publish_timing_policies", ["channel_workspace_id"])
    op.create_index("ix_channel_publish_timing_policies_timezone", "channel_publish_timing_policies", ["primary_timezone"])

    op.create_table(
        "publish_timing_suggestions",
        _uuid("id", nullable=False),
        _uuid("channel_workspace_id", nullable=False),
        _uuid("video_project_id"),
        _uuid("publish_handoff_package_id"),
        sa.Column("target_timezone", sa.Text(), nullable=False),
        sa.Column("operator_timezone", sa.Text(), nullable=True),
        sa.Column("suggested_publish_at_local", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suggested_publish_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("operator_local_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=40), server_default="CHANNEL_CONFIG", nullable=False),
        sa.Column("confidence_label", sa.String(length=40), server_default="CONFIGURED", nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        _created_at(),
        sa.CheckConstraint(TIMING_SOURCE_CHECK, name="ck_publish_timing_suggestions_source"),
        sa.CheckConstraint(TIMING_CONFIDENCE_CHECK, name="ck_publish_timing_suggestions_confidence"),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["publish_handoff_package_id"], ["publish_handoff_packages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_publish_timing_suggestions_channel", "publish_timing_suggestions", ["channel_workspace_id"])
    op.create_index("ix_publish_timing_suggestions_project", "publish_timing_suggestions", ["video_project_id"])
    op.create_index("ix_publish_timing_suggestions_handoff", "publish_timing_suggestions", ["publish_handoff_package_id"])
    op.create_index("ix_publish_timing_suggestions_utc", "publish_timing_suggestions", ["suggested_publish_at_utc"])


def downgrade() -> None:
    op.drop_index("ix_publish_timing_suggestions_utc", table_name="publish_timing_suggestions")
    op.drop_index("ix_publish_timing_suggestions_handoff", table_name="publish_timing_suggestions")
    op.drop_index("ix_publish_timing_suggestions_project", table_name="publish_timing_suggestions")
    op.drop_index("ix_publish_timing_suggestions_channel", table_name="publish_timing_suggestions")
    op.drop_table("publish_timing_suggestions")

    op.drop_index("ix_channel_publish_timing_policies_timezone", table_name="channel_publish_timing_policies")
    op.drop_index("ix_channel_publish_timing_policies_channel", table_name="channel_publish_timing_policies")
    op.drop_table("channel_publish_timing_policies")

    op.drop_index("ix_localized_metadata_packages_review", table_name="localized_metadata_packages")
    op.drop_index("ix_localized_metadata_packages_language", table_name="localized_metadata_packages")
    op.drop_index("ix_localized_metadata_packages_project", table_name="localized_metadata_packages")
    op.drop_index("ix_localized_metadata_packages_channel", table_name="localized_metadata_packages")
    op.drop_index("ix_localized_metadata_packages_company", table_name="localized_metadata_packages")
    op.drop_table("localized_metadata_packages")

    op.drop_index("ix_localized_subtitle_packages_review", table_name="localized_subtitle_packages")
    op.drop_index("ix_localized_subtitle_packages_translation", table_name="localized_subtitle_packages")
    op.drop_index("ix_localized_subtitle_packages_language", table_name="localized_subtitle_packages")
    op.drop_index("ix_localized_subtitle_packages_project", table_name="localized_subtitle_packages")
    op.drop_index("ix_localized_subtitle_packages_channel", table_name="localized_subtitle_packages")
    op.drop_index("ix_localized_subtitle_packages_company", table_name="localized_subtitle_packages")
    op.drop_table("localized_subtitle_packages")

    op.drop_index("ix_operator_auth_sessions_revoked_at", table_name="operator_auth_sessions")
    op.drop_index("ix_operator_auth_sessions_expires_at", table_name="operator_auth_sessions")
    op.drop_index("ix_operator_auth_sessions_user", table_name="operator_auth_sessions")
    op.drop_table("operator_auth_sessions")

    op.drop_index("ix_operator_users_status", table_name="operator_users")
    op.drop_index("ix_operator_users_role", table_name="operator_users")
    op.drop_index("ix_operator_users_email", table_name="operator_users")
    op.drop_table("operator_users")

    op.drop_constraint("ck_channel_workspaces_target_regions_array", "channel_workspaces", type_="check")
    op.drop_constraint("ck_channel_workspaces_target_metadata_languages_array", "channel_workspaces", type_="check")
    op.drop_constraint("ck_channel_workspaces_target_subtitle_languages_array", "channel_workspaces", type_="check")
    op.drop_constraint("ck_channel_workspaces_translation_mode", "channel_workspaces", type_="check")
    op.drop_column("channel_workspaces", "localized_metadata_required")
    op.drop_column("channel_workspaces", "localization_required_for_publish")
    op.drop_column("channel_workspaces", "translation_mode")
    op.drop_column("channel_workspaces", "target_regions")
    op.drop_column("channel_workspaces", "target_metadata_languages")
    op.drop_column("channel_workspaces", "target_subtitle_languages")
    op.drop_column("channel_workspaces", "primary_timezone")
    op.drop_column("channel_workspaces", "primary_region")
