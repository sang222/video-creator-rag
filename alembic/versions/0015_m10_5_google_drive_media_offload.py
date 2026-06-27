"""M10.5 Google Drive media offload and cloud archive

Revision ID: 0015_m10_5_drive_offload
Revises: 0014_m10_3_youtube_follow
Create Date: 2026-06-27 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0015_m10_5_drive_offload"
down_revision: str | None = "0014_m10_3_youtube_follow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

MEDIA_TYPE_CHECK = (
    "media_type in ('LONG_FORM_FINAL','SHORT_FINAL','THUMBNAIL','CAPTION','AI_HERO',"
    "'CREATOMATE_ASSET','PUBLISH_PACKAGE','QC_EXPORT','OTHER')"
)
STORAGE_PROVIDER_CHECK = "storage_provider in ('GOOGLE_DRIVE')"
UPLOAD_STATUS_CHECK = "upload_status in ('PENDING','UPLOADING','VERIFIED','FAILED','CANCELLED')"
VERIFICATION_STATUS_CHECK = "verification_status in ('NOT_STARTED','SIZE_VERIFIED','CHECKSUM_VERIFIED','CHECKSUM_UNAVAILABLE','FAILED')"
CLEANUP_STATUS_CHECK = "local_cleanup_status in ('NOT_ELIGIBLE','PENDING','CLEANED','SKIPPED','FAILED')"
OFFLOAD_JOB_STATE_CHECK = "job_state in ('PENDING','UPLOADING','VERIFIED','CLEANED_LOCAL','FAILED','CANCELLED','SKIPPED')"
TARGET_PROVIDER_CHECK = "target_provider in ('GOOGLE_DRIVE')"
RETENTION_STATE_CHECK = "state in ('ACTIVE','DISABLED')"
DRIVE_CONNECTION_STATE_CHECK = "connection_state in ('NOT_CONFIGURED','CONFIGURED','CONNECTED','NEEDS_REAUTH','REVOKED','ERROR')"
DRIVE_OAUTH_STATUS_CHECK = "status in ('STARTED','CALLBACK_RECEIVED','TOKEN_EXCHANGED','FAILED','CANCELLED')"


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
    op.create_table(
        "cloud_media_refs",
        _uuid("id", nullable=False),
        _uuid("company_id"),
        _uuid("channel_workspace_id"),
        _uuid("video_project_id"),
        _uuid("uploaded_video_id"),
        _uuid("render_package_id"),
        sa.Column("media_type", sa.String(length=60), nullable=False),
        sa.Column("storage_provider", sa.String(length=40), server_default="GOOGLE_DRIVE", nullable=False),
        sa.Column("drive_file_id", sa.Text(), nullable=False),
        sa.Column("drive_folder_id", sa.Text(), nullable=True),
        sa.Column("web_view_link", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=128), nullable=True),
        sa.Column("local_source_path_hash", sa.String(length=128), nullable=True),
        sa.Column("upload_status", sa.String(length=40), server_default="VERIFIED", nullable=False),
        sa.Column("verification_status", sa.String(length=40), server_default="SIZE_VERIFIED", nullable=False),
        sa.Column("local_cleanup_status", sa.String(length=40), server_default="NOT_ELIGIBLE", nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_policy", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("source_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(MEDIA_TYPE_CHECK, name="ck_cloud_media_refs_media_type"),
        sa.CheckConstraint(STORAGE_PROVIDER_CHECK, name="ck_cloud_media_refs_storage_provider"),
        sa.CheckConstraint(UPLOAD_STATUS_CHECK, name="ck_cloud_media_refs_upload_status"),
        sa.CheckConstraint(VERIFICATION_STATUS_CHECK, name="ck_cloud_media_refs_verification_status"),
        sa.CheckConstraint(CLEANUP_STATUS_CHECK, name="ck_cloud_media_refs_cleanup_status"),
        sa.CheckConstraint("size_bytes is null or size_bytes >= 0", name="ck_cloud_media_refs_size_nonnegative"),
        sa.CheckConstraint("jsonb_typeof(retention_policy) = 'object'", name="ck_cloud_media_refs_retention_object"),
        sa.CheckConstraint("jsonb_typeof(source_refs) = 'array'", name="ck_cloud_media_refs_source_refs_array"),
        sa.CheckConstraint("jsonb_typeof(technical_appendix) = 'object'", name="ck_cloud_media_refs_appendix_object"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["render_package_id"], ["render_package_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cloud_media_refs_company", "cloud_media_refs", ["company_id"])
    op.create_index("ix_cloud_media_refs_channel", "cloud_media_refs", ["channel_workspace_id"])
    op.create_index("ix_cloud_media_refs_project", "cloud_media_refs", ["video_project_id"])
    op.create_index("ix_cloud_media_refs_uploaded_video", "cloud_media_refs", ["uploaded_video_id"])
    op.create_index("ix_cloud_media_refs_render_package", "cloud_media_refs", ["render_package_id"])
    op.create_index("ix_cloud_media_refs_media_type", "cloud_media_refs", ["media_type"])
    op.create_index("ix_cloud_media_refs_drive_file", "cloud_media_refs", ["drive_file_id"])
    op.create_index("ix_cloud_media_refs_upload_status", "cloud_media_refs", ["upload_status"])
    op.create_index("ix_cloud_media_refs_cleanup_status", "cloud_media_refs", ["local_cleanup_status"])

    op.create_table(
        "media_offload_jobs",
        _uuid("id", nullable=False),
        _uuid("company_id"),
        _uuid("channel_workspace_id"),
        _uuid("video_project_id"),
        _uuid("uploaded_video_id"),
        _uuid("source_media_ref_id"),
        _uuid("render_package_id"),
        sa.Column("local_source_path_hash", sa.String(length=128), nullable=True),
        sa.Column("target_provider", sa.String(length=40), server_default="GOOGLE_DRIVE", nullable=False),
        sa.Column("target_folder_policy", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("target_media_type", sa.String(length=60), nullable=False),
        sa.Column("job_state", sa.String(length=40), server_default="PENDING", nullable=False),
        _uuid("cloud_media_ref_id"),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(TARGET_PROVIDER_CHECK, name="ck_media_offload_jobs_target_provider"),
        sa.CheckConstraint(MEDIA_TYPE_CHECK.replace("media_type", "target_media_type"), name="ck_media_offload_jobs_media_type"),
        sa.CheckConstraint(OFFLOAD_JOB_STATE_CHECK, name="ck_media_offload_jobs_state"),
        sa.CheckConstraint("retry_count >= 0", name="ck_media_offload_jobs_retry_nonnegative"),
        sa.CheckConstraint("jsonb_typeof(target_folder_policy) = 'object'", name="ck_media_offload_jobs_folder_policy_object"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["source_media_ref_id"], ["final_media_refs.id"]),
        sa.ForeignKeyConstraint(["render_package_id"], ["render_package_snapshots.id"]),
        sa.ForeignKeyConstraint(["cloud_media_ref_id"], ["cloud_media_refs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_offload_jobs_company", "media_offload_jobs", ["company_id"])
    op.create_index("ix_media_offload_jobs_channel", "media_offload_jobs", ["channel_workspace_id"])
    op.create_index("ix_media_offload_jobs_project", "media_offload_jobs", ["video_project_id"])
    op.create_index("ix_media_offload_jobs_uploaded_video", "media_offload_jobs", ["uploaded_video_id"])
    op.create_index("ix_media_offload_jobs_source_media_ref", "media_offload_jobs", ["source_media_ref_id"])
    op.create_index("ix_media_offload_jobs_render_package", "media_offload_jobs", ["render_package_id"])
    op.create_index("ix_media_offload_jobs_cloud_ref", "media_offload_jobs", ["cloud_media_ref_id"])
    op.create_index("ix_media_offload_jobs_state", "media_offload_jobs", ["job_state"])
    op.create_index("ix_media_offload_jobs_created_at", "media_offload_jobs", ["created_at"])

    op.create_table(
        "local_media_retention_policies",
        _uuid("id", nullable=False),
        _uuid("company_id"),
        _uuid("channel_workspace_id"),
        sa.Column("keep_local_after_upload", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("cleanup_after_verified", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("max_local_age_hours", sa.Integer(), nullable=True),
        sa.Column("max_local_storage_gb", sa.Integer(), nullable=True),
        sa.Column("protected_paths", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("allowed_cleanup_roots", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("state", sa.String(length=40), server_default="ACTIVE", nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(RETENTION_STATE_CHECK, name="ck_local_retention_policies_state"),
        sa.CheckConstraint("jsonb_typeof(protected_paths) = 'array'", name="ck_local_retention_protected_array"),
        sa.CheckConstraint("jsonb_typeof(allowed_cleanup_roots) = 'array'", name="ck_local_retention_roots_array"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_local_retention_policies_company", "local_media_retention_policies", ["company_id"])
    op.create_index("ix_local_retention_policies_channel", "local_media_retention_policies", ["channel_workspace_id"])
    op.create_index("ix_local_retention_policies_state", "local_media_retention_policies", ["state"])

    op.create_table(
        "google_drive_media_credentials",
        _uuid("id", nullable=False),
        _uuid("company_id"),
        _uuid("channel_workspace_id"),
        _uuid("credential_reference_id", nullable=False),
        sa.Column("connection_state", sa.String(length=40), server_default="NOT_CONFIGURED", nullable=False),
        sa.Column("scopes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("root_folder_id", sa.Text(), nullable=True),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(DRIVE_CONNECTION_STATE_CHECK, name="ck_google_drive_credentials_state"),
        sa.CheckConstraint("jsonb_typeof(scopes) = 'array'", name="ck_google_drive_credentials_scopes_array"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["credential_reference_id"], ["credential_references.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_google_drive_credentials_company", "google_drive_media_credentials", ["company_id"])
    op.create_index("ix_google_drive_credentials_channel", "google_drive_media_credentials", ["channel_workspace_id"])
    op.create_index("ix_google_drive_credentials_reference", "google_drive_media_credentials", ["credential_reference_id"])
    op.create_index("ix_google_drive_credentials_state", "google_drive_media_credentials", ["connection_state"])

    op.create_table(
        "google_drive_oauth_sessions",
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
        sa.CheckConstraint(DRIVE_OAUTH_STATUS_CHECK, name="ck_google_drive_oauth_sessions_status"),
        sa.CheckConstraint("jsonb_typeof(scopes) = 'array'", name="ck_google_drive_oauth_sessions_scopes_array"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["credential_reference_id"], ["credential_references.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_google_drive_oauth_sessions_company", "google_drive_oauth_sessions", ["company_id"])
    op.create_index("ix_google_drive_oauth_sessions_channel", "google_drive_oauth_sessions", ["channel_workspace_id"])
    op.create_index("ix_google_drive_oauth_sessions_state_hash", "google_drive_oauth_sessions", ["state_token_hash"])
    op.create_index("ix_google_drive_oauth_sessions_status", "google_drive_oauth_sessions", ["status"])
    op.create_index("ix_google_drive_oauth_sessions_created_at", "google_drive_oauth_sessions", ["created_at"])

    op.add_column("final_media_refs", sa.Column("cloud_media_ref_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_final_media_refs_cloud_media_ref_id_cloud_media_refs", "final_media_refs", "cloud_media_refs", ["cloud_media_ref_id"], ["id"])
    op.create_index("ix_final_media_refs_cloud_media_ref", "final_media_refs", ["cloud_media_ref_id"])

    op.add_column("long_form_render_packages", sa.Column("cloud_media_refs", JSONB, server_default=_jsonb_array(), nullable=False))
    op.create_check_constraint("ck_long_pkg_cloud_refs_array", "long_form_render_packages", "jsonb_typeof(cloud_media_refs) = 'array'")
    op.add_column("short_render_packages", sa.Column("cloud_media_refs", JSONB, server_default=_jsonb_array(), nullable=False))
    op.create_check_constraint("ck_short_pkg_cloud_refs_array", "short_render_packages", "jsonb_typeof(cloud_media_refs) = 'array'")
    op.add_column("publish_handoff_packages", sa.Column("cloud_media_refs", JSONB, server_default=_jsonb_array(), nullable=False))
    op.create_check_constraint("ck_publish_handoff_cloud_refs_array", "publish_handoff_packages", "jsonb_typeof(cloud_media_refs) = 'array'")


def downgrade() -> None:
    op.drop_constraint("ck_publish_handoff_cloud_refs_array", "publish_handoff_packages", type_="check")
    op.drop_column("publish_handoff_packages", "cloud_media_refs")
    op.drop_constraint("ck_short_pkg_cloud_refs_array", "short_render_packages", type_="check")
    op.drop_column("short_render_packages", "cloud_media_refs")
    op.drop_constraint("ck_long_pkg_cloud_refs_array", "long_form_render_packages", type_="check")
    op.drop_column("long_form_render_packages", "cloud_media_refs")
    op.drop_index("ix_final_media_refs_cloud_media_ref", table_name="final_media_refs")
    op.drop_constraint("fk_final_media_refs_cloud_media_ref_id_cloud_media_refs", "final_media_refs", type_="foreignkey")
    op.drop_column("final_media_refs", "cloud_media_ref_id")

    op.drop_table("google_drive_oauth_sessions")
    op.drop_table("google_drive_media_credentials")
    op.drop_table("local_media_retention_policies")
    op.drop_table("media_offload_jobs")
    op.drop_table("cloud_media_refs")
