"""Add private YouTube staging and public publication boundary.

Revision ID: 0084_youtube_private_delivery
Revises: 0083_combined_budget_authority
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0084_youtube_private_delivery"
down_revision: str | None = "0083_combined_budget_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    ]


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "youtube_publishing_credentials",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("company_id", uuid, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "channel_workspace_id",
            uuid,
            sa.ForeignKey("channel_workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "credential_reference_id",
            uuid,
            sa.ForeignKey("credential_references.id"),
            nullable=False,
        ),
        sa.Column("platform_channel_id", sa.Text(), nullable=False),
        sa.Column("account_identity", sa.Text(), nullable=False),
        sa.Column("oauth_scopes", jsonb, nullable=False),
        sa.Column("capabilities", jsonb, nullable=False),
        sa.Column("public_release_allowed", sa.Boolean(), nullable=False),
        sa.Column("delete_allowed", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "platform_channel_id",
            name="uq_yt_publish_cred_channel",
        ),
        sa.UniqueConstraint(
            "credential_reference_id", name="uq_yt_publish_cred_reference"
        ),
        sa.CheckConstraint(
            "state in ('ACTIVE','REVOKED','BLOCKED')",
            name="ck_yt_publish_cred_state",
        ),
        sa.CheckConstraint(
            "public_release_allowed = false and delete_allowed = false",
            name="ck_yt_publish_cred_private_only",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_yt_publish_cred_hash"
        ),
    )
    op.create_index(
        "ix_yt_publish_cred_company_channel",
        "youtube_publishing_credentials",
        ["company_id", "channel_workspace_id"],
    )
    op.create_index(
        "ix_yt_publish_cred_state", "youtube_publishing_credentials", ["state"]
    )

    op.create_table(
        "production_thumbnail_bindings",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("company_id", uuid, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "channel_workspace_id",
            uuid,
            sa.ForeignKey("channel_workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "video_project_id",
            uuid,
            sa.ForeignKey("video_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "final_review_candidate_id",
            uuid,
            sa.ForeignKey("final_review_candidates.id"),
            nullable=False,
        ),
        sa.Column(
            "thumbnail_variant_id", uuid, sa.ForeignKey("thumbnail_variants.id")
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("provider_key", sa.String(160), nullable=False),
        sa.Column("provider_effect_ref", sa.Text(), nullable=False),
        sa.Column("provider_effect_hash", sa.String(64), nullable=False),
        sa.Column("file_ref", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "final_review_candidate_id", name="uq_thumbnail_binding_candidate"
        ),
        sa.UniqueConstraint("checksum_sha256", name="uq_thumbnail_binding_checksum"),
        sa.CheckConstraint(
            "source_type = 'AI_GENERATED' and state = 'VERIFIED'",
            name="ck_thumbnail_binding_authority",
        ),
        sa.CheckConstraint(
            "mime_type in ('image/jpeg','image/png') and size_bytes > 0 and "
            "size_bytes <= 2097152 and width > 0 and height > 0",
            name="ck_thumbnail_binding_media",
        ),
        sa.CheckConstraint(
            "provider_effect_hash ~ '^[0-9a-f]{64}$' and "
            "checksum_sha256 ~ '^[0-9a-f]{64}$' and content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_thumbnail_binding_hashes",
        ),
    )
    op.create_index(
        "ix_thumbnail_binding_project",
        "production_thumbnail_bindings",
        ["video_project_id"],
    )

    op.create_table(
        "youtube_private_stages",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("company_id", uuid, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "channel_workspace_id",
            uuid,
            sa.ForeignKey("channel_workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "video_project_id", uuid, sa.ForeignKey("video_projects.id"), nullable=False
        ),
        sa.Column(
            "final_review_candidate_id",
            uuid,
            sa.ForeignKey("final_review_candidates.id"),
            nullable=False,
        ),
        sa.Column(
            "final_video_decision_id",
            uuid,
            sa.ForeignKey("final_video_decisions.id"),
            nullable=False,
        ),
        sa.Column(
            "final_media_ref_id",
            uuid,
            sa.ForeignKey("final_media_refs.id"),
            nullable=False,
        ),
        sa.Column("final_media_ref", sa.Text(), nullable=False),
        sa.Column("final_media_checksum", sa.String(64), nullable=False),
        sa.Column(
            "publishing_credential_id",
            uuid,
            sa.ForeignKey("youtube_publishing_credentials.id"),
            nullable=False,
        ),
        sa.Column(
            "production_thumbnail_binding_id",
            uuid,
            sa.ForeignKey("production_thumbnail_bindings.id"),
            nullable=False,
        ),
        sa.Column("caption_ref", sa.Text(), nullable=False),
        sa.Column("caption_hash", sa.String(64), nullable=False),
        sa.Column("staging_metadata", jsonb, nullable=False),
        sa.Column("staging_metadata_hash", sa.String(64), nullable=False),
        sa.Column("public_release_expectation", jsonb, nullable=False),
        sa.Column("public_release_expectation_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(48), nullable=False),
        sa.Column("platform_video_id", sa.Text()),
        sa.Column("studio_url", sa.Text()),
        sa.Column("observed_metadata", jsonb),
        sa.Column("observed_metadata_hash", sa.String(64)),
        sa.Column("processing_status", sa.String(48)),
        sa.Column("private_verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(160)),
        sa.Column("identity_hash", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "final_video_decision_id", name="uq_yt_private_stage_decision"
        ),
        sa.UniqueConstraint("identity_hash", name="uq_yt_private_stage_identity"),
        sa.UniqueConstraint(
            "publishing_credential_id",
            "platform_video_id",
            name="uq_yt_private_stage_video",
        ),
        sa.CheckConstraint(
            "state in ('PREPARED','SESSION_CREATED','UPLOADING','OUTCOME_UNKNOWN',"
            "'BYTES_ACCEPTED','PROCESSING','PRIVATE_VERIFIED','BLOCKED','FAILED')",
            name="ck_yt_private_stage_state",
        ),
        sa.CheckConstraint(
            "final_media_checksum ~ '^[0-9a-f]{64}$' and "
            "caption_hash ~ '^[0-9a-f]{64}$' and "
            "staging_metadata_hash ~ '^[0-9a-f]{64}$' and "
            "public_release_expectation_hash ~ '^[0-9a-f]{64}$' and "
            "identity_hash ~ '^[0-9a-f]{64}$' and "
            "(observed_metadata_hash is null or observed_metadata_hash ~ '^[0-9a-f]{64}$')",
            name="ck_yt_private_stage_hashes",
        ),
        sa.CheckConstraint(
            "coalesce(staging_metadata->>'privacy_status','') = 'PRIVATE' and "
            "coalesce(staging_metadata->>'public_release_by_api','false') = 'false' and "
            "coalesce(public_release_expectation->>'manual_release_only','false') = 'true'",
            name="ck_yt_private_stage_private_only",
        ),
        sa.CheckConstraint(
            "(state <> 'PRIVATE_VERIFIED') or (platform_video_id is not null and "
            "studio_url is not null and observed_metadata is not null and "
            "observed_metadata_hash is not null and processing_status = 'SUCCEEDED' and "
            "private_verified_at is not null)",
            name="ck_yt_private_stage_verified",
        ),
    )
    op.create_index("ix_yt_private_stage_project", "youtube_private_stages", ["video_project_id"])
    op.create_index("ix_yt_private_stage_state", "youtube_private_stages", ["state"])

    op.create_table(
        "youtube_upload_attempts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "youtube_private_stage_id",
            uuid,
            sa.ForeignKey("youtube_private_stages.id"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider_effect_key", sa.String(160), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("session_secret_ref", sa.Text()),
        sa.Column("session_uri_hash", sa.String(64)),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("committed_bytes", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("outcome_certainty", sa.String(32), nullable=False),
        sa.Column("provider_video_id", sa.Text()),
        sa.Column("provider_response_hash", sa.String(64)),
        sa.Column("error_code", sa.String(160)),
        *_timestamps(),
        sa.UniqueConstraint(
            "youtube_private_stage_id", name="uq_yt_upload_attempt_stage"
        ),
        sa.UniqueConstraint("provider_effect_key", name="uq_yt_upload_attempt_effect"),
        sa.CheckConstraint(
            "attempt_number = 1 and total_bytes > 0 and committed_bytes >= 0 and "
            "committed_bytes <= total_bytes",
            name="ck_yt_upload_attempt_counts",
        ),
        sa.CheckConstraint(
            "state in ('INTENDED','SESSION_SUBMITTED','SESSION_CREATED','UPLOADING','OUTCOME_UNKNOWN',"
            "'BYTES_ACCEPTED','VERIFIED','FAILED')",
            name="ck_yt_upload_attempt_state",
        ),
        sa.CheckConstraint(
            "outcome_certainty in ('NOT_SUBMITTED','CERTAIN_PENDING','UNCERTAIN',"
            "'CERTAIN_SUCCESS','CERTAIN_FAILURE')",
            name="ck_yt_upload_attempt_certainty",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' and "
            "(session_uri_hash is null or session_uri_hash ~ '^[0-9a-f]{64}$') and "
            "(provider_response_hash is null or provider_response_hash ~ '^[0-9a-f]{64}$')",
            name="ck_yt_upload_attempt_hashes",
        ),
        sa.CheckConstraint(
            "(state not in ('SESSION_CREATED','UPLOADING','BYTES_ACCEPTED','VERIFIED')) or "
            "(session_secret_ref is not null and session_uri_hash is not null)",
            name="ck_yt_upload_attempt_session",
        ),
        sa.CheckConstraint(
            "(state <> 'VERIFIED') or (provider_video_id is not null and "
            "provider_response_hash is not null and committed_bytes = total_bytes and "
            "outcome_certainty = 'CERTAIN_SUCCESS')",
            name="ck_yt_upload_attempt_verified",
        ),
    )
    op.create_index("ix_yt_upload_attempt_state", "youtube_upload_attempts", ["state"])

    op.create_table(
        "youtube_component_attempts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "youtube_private_stage_id",
            uuid,
            sa.ForeignKey("youtube_private_stages.id"),
            nullable=False,
        ),
        sa.Column("component_type", sa.String(32), nullable=False),
        sa.Column("provider_effect_key", sa.String(160), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("provider_resource_id", sa.Text()),
        sa.Column("response_hash", sa.String(64)),
        sa.Column("error_code", sa.String(160)),
        *_timestamps(),
        sa.UniqueConstraint(
            "youtube_private_stage_id",
            "component_type",
            name="uq_yt_component_attempt_stage_type",
        ),
        sa.UniqueConstraint(
            "provider_effect_key", name="uq_yt_component_attempt_effect"
        ),
        sa.CheckConstraint(
            "component_type in ('THUMBNAIL','CAPTION')",
            name="ck_yt_component_attempt_type",
        ),
        sa.CheckConstraint(
            "state in ('INTENDED','SUBMITTED','OUTCOME_UNKNOWN','VERIFIED','FAILED')",
            name="ck_yt_component_attempt_state",
        ),
        sa.CheckConstraint(
            "attempt_count between 0 and 1", name="ck_yt_component_attempt_count"
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' and "
            "(response_hash is null or response_hash ~ '^[0-9a-f]{64}$')",
            name="ck_yt_component_attempt_hashes",
        ),
        sa.CheckConstraint(
            "(state <> 'VERIFIED') or (attempt_count = 1 and provider_resource_id is not null "
            "and response_hash is not null)",
            name="ck_yt_component_attempt_verified",
        ),
    )

    op.create_table(
        "youtube_component_receipts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "youtube_private_stage_id",
            uuid,
            sa.ForeignKey("youtube_private_stages.id"),
            nullable=False,
        ),
        sa.Column("component_type", sa.String(48), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("provider_resource_id", sa.Text()),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64), nullable=False),
        sa.Column("evidence", jsonb, nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "youtube_private_stage_id",
            "component_type",
            name="uq_yt_component_stage_type",
        ),
        sa.CheckConstraint(
            "component_type in ('VIDEO_UPLOAD','THUMBNAIL','CAPTION','METADATA_READBACK','PROCESSING_READBACK')",
            name="ck_yt_component_type",
        ),
        sa.CheckConstraint("state = 'VERIFIED'", name="ck_yt_component_state"),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' and response_hash ~ '^[0-9a-f]{64}$' "
            "and receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_yt_component_hashes",
        ),
    )
    op.create_index(
        "ix_yt_component_stage", "youtube_component_receipts", ["youtube_private_stage_id"]
    )

    op.create_table(
        "public_publication_receipts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("company_id", uuid, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "channel_workspace_id", uuid, sa.ForeignKey("channel_workspaces.id"), nullable=False
        ),
        sa.Column("video_project_id", uuid, sa.ForeignKey("video_projects.id"), nullable=False),
        sa.Column(
            "final_review_candidate_id", uuid, sa.ForeignKey("final_review_candidates.id"), nullable=False
        ),
        sa.Column(
            "final_video_decision_id", uuid, sa.ForeignKey("final_video_decisions.id"), nullable=False
        ),
        sa.Column(
            "manual_publish_confirmation_id", uuid, sa.ForeignKey("manual_publish_confirmations.id"), nullable=False
        ),
        sa.Column("youtube_private_stage_id", uuid, sa.ForeignKey("youtube_private_stages.id")),
        sa.Column("platform_channel_id", sa.Text(), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("public_url", sa.Text(), nullable=False),
        sa.Column("observed_privacy_status", sa.String(20), nullable=False),
        sa.Column("observed_published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_metadata", jsonb, nullable=False),
        sa.Column("observed_metadata_hash", sa.String(64), nullable=False),
        sa.Column("verification_evidence_ref", sa.Text(), nullable=False),
        sa.Column("verification_evidence_hash", sa.String(64), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.UniqueConstraint("final_video_decision_id", name="uq_public_receipt_decision"),
        sa.UniqueConstraint("platform_channel_id", "platform_video_id", name="uq_public_receipt_video"),
        sa.UniqueConstraint("manual_publish_confirmation_id", name="uq_public_receipt_confirmation"),
        sa.CheckConstraint("observed_privacy_status = 'PUBLIC'", name="ck_public_receipt_public"),
        sa.CheckConstraint(
            "observed_metadata_hash ~ '^[0-9a-f]{64}$' and "
            "verification_evidence_hash ~ '^[0-9a-f]{64}$' and receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_public_receipt_hashes",
        ),
    )
    op.create_index("ix_public_receipt_project", "public_publication_receipts", ["video_project_id"])
    op.create_index("ix_public_receipt_published", "public_publication_receipts", ["observed_published_at"])

    op.add_column("uploaded_videos", sa.Column("public_publication_receipt_id", uuid))
    op.create_foreign_key(
        "fk_uploaded_videos_public_receipt",
        "uploaded_videos",
        "public_publication_receipts",
        ["public_publication_receipt_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_uploaded_videos_public_receipt", "uploaded_videos", ["public_publication_receipt_id"]
    )
    op.drop_constraint("ck_uploaded_videos_schema_version", "uploaded_videos", type_="check")
    op.create_check_constraint(
        "ck_uploaded_videos_schema_version",
        "uploaded_videos",
        "schema_version in ('v1','v2','v3')",
    )
    op.create_check_constraint(
        "ck_uploaded_videos_v3_public_receipt",
        "uploaded_videos",
        "(schema_version <> 'v3') or (public_publication_receipt_id is not null and "
        "actual_visibility = 'PUBLIC' and verification_status = 'VERIFIED' and "
        "analytics_sync_status = 'READY')",
    )

    op.create_table(
        "local_media_purge_attempts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "youtube_private_stage_id",
            uuid,
            sa.ForeignKey("youtube_private_stages.id"),
            nullable=False,
        ),
        sa.Column(
            "final_media_ref_id",
            uuid,
            sa.ForeignKey("final_media_refs.id"),
            nullable=False,
        ),
        sa.Column("command_id", sa.String(160), nullable=False),
        sa.Column("original_file_ref", sa.Text(), nullable=False),
        sa.Column("quarantine_file_ref", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(160)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "youtube_private_stage_id", name="uq_local_media_purge_attempt_stage"
        ),
        sa.UniqueConstraint("command_id", name="uq_local_media_purge_attempt_command"),
        sa.CheckConstraint(
            "state in ('INTENDED','SUBMITTED','QUARANTINED','PURGED','BLOCKED')",
            name="ck_local_media_purge_attempt_state",
        ),
        sa.CheckConstraint(
            "attempt_count between 0 and 1 and "
            "((state = 'INTENDED' and attempt_count = 0) or "
            "(state in ('SUBMITTED','QUARANTINED','PURGED','BLOCKED') and attempt_count = 1))",
            name="ck_local_media_purge_attempt_count",
        ),
        sa.CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_local_media_purge_attempt_hashes",
        ),
    )
    op.create_index(
        "ix_local_media_purge_attempt_state",
        "local_media_purge_attempts",
        ["state"],
    )

    op.create_table(
        "local_media_purge_receipts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "youtube_private_stage_id", uuid, sa.ForeignKey("youtube_private_stages.id"), nullable=False
        ),
        sa.Column("final_media_ref_id", uuid, sa.ForeignKey("final_media_refs.id"), nullable=False),
        sa.Column("local_file_ref", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.UniqueConstraint("youtube_private_stage_id", name="uq_local_purge_stage"),
        sa.UniqueConstraint("final_media_ref_id", name="uq_local_purge_final_media"),
        sa.CheckConstraint("state = 'PURGED'", name="ck_local_purge_state"),
        sa.CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$' and receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_local_purge_hashes",
        ),
    )

    op.create_table(
        "telegram_delivery_notifications",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("company_id", uuid, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("channel_workspace_id", uuid, sa.ForeignKey("channel_workspaces.id"), nullable=False),
        sa.Column("video_project_id", uuid, sa.ForeignKey("video_projects.id"), nullable=False),
        sa.Column("final_review_candidate_id", uuid, sa.ForeignKey("final_review_candidates.id"), nullable=False),
        sa.Column("credential_reference_id", uuid, sa.ForeignKey("credential_references.id")),
        sa.Column("chat_binding_ref", sa.Text()),
        sa.Column("notification_kind", sa.String(48), nullable=False),
        sa.Column("payload", jsonb, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.Text()),
        sa.Column("provider_response_hash", sa.String(64)),
        sa.Column("error_code", sa.String(160)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("final_review_candidate_id", "notification_kind", name="uq_telegram_notice_candidate_kind"),
        sa.CheckConstraint(
            "notification_kind in ('FINAL_REVIEW_READY','YOUTUBE_PRIVATE_VERIFIED')",
            name="ck_telegram_notice_kind",
        ),
        sa.CheckConstraint(
            "state in ('PENDING','SUBMITTED','OUTCOME_UNKNOWN','SENT','BLOCKED_CONFIG','FAILED')",
            name="ck_telegram_notice_state",
        ),
        sa.CheckConstraint("attempt_count between 0 and 1", name="ck_telegram_notice_attempts"),
        sa.CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$' and "
            "(provider_response_hash is null or provider_response_hash ~ '^[0-9a-f]{64}$')",
            name="ck_telegram_notice_hashes",
        ),
        sa.CheckConstraint(
            "(state <> 'SENT') or (attempt_count = 1 and provider_message_id is not null "
            "and provider_response_hash is not null and sent_at is not null)",
            name="ck_telegram_notice_sent",
        ),
    )
    op.create_index("ix_telegram_notice_state", "telegram_delivery_notifications", ["state"])

    op.create_table(
        "youtube_series_playlist_bindings",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("company_id", uuid, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("channel_workspace_id", uuid, sa.ForeignKey("channel_workspaces.id"), nullable=False),
        sa.Column("series_plan_id", uuid, sa.ForeignKey("series_plans.id"), nullable=False),
        sa.Column("publishing_credential_id", uuid, sa.ForeignKey("youtube_publishing_credentials.id"), nullable=False),
        sa.Column("platform_channel_id", sa.Text(), nullable=False),
        sa.Column("youtube_playlist_id", sa.Text()),
        sa.Column("state", sa.String(48), nullable=False),
        sa.Column("expected_metadata", jsonb, nullable=False),
        sa.Column("observed_metadata", jsonb),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("series_plan_id", name="uq_yt_series_playlist_series"),
        sa.UniqueConstraint("platform_channel_id", "youtube_playlist_id", name="uq_yt_series_playlist_external"),
        sa.CheckConstraint(
            "state in ('NOT_CREATED','CREATE_PENDING','CREATED_UNVERIFIED','VERIFIED_EMPTY',"
            "'VERIFIED_WITH_EPISODES','SYNC_DRIFT','BLOCKED')",
            name="ck_yt_series_playlist_state",
        ),
        sa.CheckConstraint("binding_hash ~ '^[0-9a-f]{64}$'", name="ck_yt_series_playlist_hash"),
    )
    op.create_index("ix_yt_series_playlist_state", "youtube_series_playlist_bindings", ["state"])

    op.create_table(
        "youtube_series_episode_bindings",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("youtube_series_playlist_binding_id", uuid, sa.ForeignKey("youtube_series_playlist_bindings.id"), nullable=False),
        sa.Column("series_plan_id", uuid, sa.ForeignKey("series_plans.id"), nullable=False),
        sa.Column("series_run_id", uuid, sa.ForeignKey("series_runs.id"), nullable=False),
        sa.Column("technical_episode_number", sa.Integer(), nullable=False),
        sa.Column("public_episode_ordinal", sa.Integer()),
        sa.Column("public_ordinal_authority_ref", sa.Text()),
        sa.Column("public_ordinal_authority_hash", sa.String(64)),
        sa.Column("video_project_id", uuid, sa.ForeignKey("video_projects.id"), nullable=False),
        sa.Column("youtube_private_stage_id", uuid, sa.ForeignKey("youtube_private_stages.id"), nullable=False),
        sa.Column("public_publication_receipt_id", uuid, sa.ForeignKey("public_publication_receipts.id")),
        sa.Column("youtube_video_id", sa.Text(), nullable=False),
        sa.Column("youtube_playlist_item_id", sa.Text()),
        sa.Column("state", sa.String(48), nullable=False),
        sa.Column("expected_position", sa.Integer()),
        sa.Column("actual_position", sa.Integer()),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("series_run_id", "technical_episode_number", name="uq_yt_series_episode_run_number"),
        sa.UniqueConstraint("youtube_private_stage_id", name="uq_yt_series_episode_stage"),
        sa.UniqueConstraint("youtube_playlist_item_id", name="uq_yt_series_episode_item"),
        sa.CheckConstraint(
            "technical_episode_number > 0 and (public_episode_ordinal is null or public_episode_ordinal > 0) "
            "and (expected_position is null or expected_position >= 0) "
            "and (actual_position is null or actual_position >= 0)",
            name="ck_yt_series_episode_ordinals",
        ),
        sa.CheckConstraint(
            "state in ('PRIVATE_UPLOADED','PUBLICATION_VERIFIED','PLAYLIST_BIND_PENDING',"
            "'PLAYLIST_BOUND_UNVERIFIED','PLAYLIST_BOUND_VERIFIED','SYNC_DRIFT','BLOCKED')",
            name="ck_yt_series_episode_state",
        ),
        sa.CheckConstraint(
            "binding_hash ~ '^[0-9a-f]{64}$' and "
            "(public_ordinal_authority_hash is null or public_ordinal_authority_hash ~ '^[0-9a-f]{64}$')",
            name="ck_yt_series_episode_hashes",
        ),
        sa.CheckConstraint(
            "(state not in ('PLAYLIST_BIND_PENDING','PLAYLIST_BOUND_UNVERIFIED','PLAYLIST_BOUND_VERIFIED')) or "
            "(public_episode_ordinal is not null and public_ordinal_authority_ref is not null "
            "and public_ordinal_authority_hash is not null and expected_position is not null)",
            name="ck_yt_series_episode_public_ordinal",
        ),
    )
    op.create_index("ix_yt_series_episode_series", "youtube_series_episode_bindings", ["series_plan_id"])
    op.create_index("ix_yt_series_episode_state", "youtube_series_episode_bindings", ["state"])

    op.execute(
        """
        CREATE FUNCTION vcos_reject_youtube_delivery_authority_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'youtube delivery authority is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in (
        "production_thumbnail_bindings",
        "youtube_component_receipts",
        "public_publication_receipts",
        "local_media_purge_receipts",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION
            vcos_reject_youtube_delivery_authority_mutation();
            """
        )


def downgrade() -> None:
    raise RuntimeError("0084 is forward-only; delivery authority history is immutable")
