"""Add the final-video decision and canonical manual-publish v2 authority.

Revision ID: 0045_vcos_final_publish
Revises: 0044_vcos_orchestration
Create Date: 2026-07-29 00:30:00

Legacy M7/M12.2r rows remain ``v1`` and retain their original lineage.  New
``v2`` writes require an archive-verified final candidate, a session-bound
UPLOAD decision, the exact FinalMediaRef/package/destination, and deterministic
manual confirmation.  Downgrade is refused after any such authority exists.
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0045_vcos_final_publish"
down_revision: str | None = "0044_vcos_orchestration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    _create_final_review_candidates()
    op.create_foreign_key(
        "fk_production_workflow_runs_final_review_candidate_id",
        "production_workflow_runs",
        "final_review_candidates",
        ["final_review_candidate_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    _create_final_video_decisions()
    _extend_human_upload_tasks()
    _extend_manual_publish_confirmations()
    _extend_uploaded_videos()
    _create_series_episode_publications()
    _create_immutable_authority_triggers()


def _create_final_review_candidates() -> None:
    op.create_table(
        "final_review_candidates",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workflow_run_id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=False),
        sa.Column("channel_profile_version_id", UUID, nullable=False),
        sa.Column("policy_snapshot_id", UUID, nullable=False),
        sa.Column(
            "production_package_artifact_version_id",
            UUID,
            nullable=False,
        ),
        sa.Column(
            "production_package_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "production_readiness_receipt_artifact_version_id",
            UUID,
            nullable=False,
        ),
        sa.Column(
            "production_readiness_receipt_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "canonical_media_timeline_ref",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "canonical_media_timeline_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("native_render_plan_ref", sa.Text(), nullable=False),
        sa.Column(
            "native_render_plan_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("render_output_ref", sa.Text(), nullable=False),
        sa.Column(
            "render_output_checksum",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("technical_qc_receipt_ref", sa.Text(), nullable=False),
        sa.Column(
            "technical_qc_receipt_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("creative_qc_receipt_ref", sa.Text(), nullable=False),
        sa.Column(
            "creative_qc_receipt_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("archive_receipt_ref", sa.Text(), nullable=False),
        sa.Column(
            "archive_receipt_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("archive_object_ref", sa.Text(), nullable=False),
        sa.Column(
            "archive_verification_state",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column("final_media_ref_id", UUID, nullable=False),
        sa.Column("final_media_hash", sa.String(length=64), nullable=False),
        sa.Column("destination_binding_id", UUID, nullable=False),
        sa.Column(
            "destination_binding_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "destination_platform_channel_id",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "destination_account_identity",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "target_platform",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column("target_surface", sa.String(length=40), nullable=False),
        sa.Column(
            "target_market_lineage",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "production_lane",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column("content_mode", sa.String(length=40), nullable=False),
        sa.Column("series_plan_id", UUID, nullable=True),
        sa.Column("series_run_id", UUID, nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column(
            "standalone_reason_code",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("parent_video_project_id", UUID, nullable=True),
        sa.Column("parent_final_media_ref_id", UUID, nullable=True),
        sa.Column(
            "publish_metadata_snapshot",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "disclosure_snapshot",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "materiality_policy_snapshot",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "materiality_policy_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "archive_verification_state = 'VERIFIED'",
            name="ck_final_review_candidates_archive_verified",
        ),
        sa.CheckConstraint(
            "production_lane in ('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT')",
            name="ck_final_review_candidates_production_lane",
        ),
        sa.CheckConstraint(
            "content_mode in ('SERIES_EPISODE','STANDALONE')",
            name="ck_final_review_candidates_content_mode",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "(production_lane <> 'LONG_DERIVED_SHORT') or "
            "(parent_video_project_id is not null "
            "and parent_final_media_ref_id is not null)",
            name="ck_final_review_candidates_parent_lineage",
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(["workflow_run_id"], ["production_workflow_runs.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(
            ["channel_profile_version_id"], ["channel_profile_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["policy_snapshot_id"],
            ["compiled_channel_policy_snapshots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["production_package_artifact_version_id"],
            ["artifact_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["production_readiness_receipt_artifact_version_id"],
            ["artifact_versions.id"],
        ),
        sa.ForeignKeyConstraint(["final_media_ref_id"], ["final_media_refs.id"]),
        sa.ForeignKeyConstraint(["series_plan_id"], ["series_plans.id"]),
        sa.ForeignKeyConstraint(["series_run_id"], ["series_runs.id"]),
        sa.ForeignKeyConstraint(["parent_video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["parent_final_media_ref_id"], ["final_media_refs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_hash",
            name="uq_final_review_candidates_candidate_hash",
        ),
    )
    for index_name, columns in (
        ("ix_final_review_candidates_workflow_run_id", ["workflow_run_id"]),
        ("ix_final_review_candidates_project_id", ["video_project_id"]),
        (
            "ix_final_review_candidates_final_media_ref_id",
            ["final_media_ref_id"],
        ),
        ("ix_final_review_candidates_created_at", ["created_at"]),
    ):
        op.create_index(index_name, "final_review_candidates", columns)


def _create_final_video_decisions() -> None:
    op.create_table(
        "final_video_decisions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("final_review_candidate_id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=False),
        sa.Column("decision", sa.String(length=40), nullable=False),
        sa.Column("operator_user_id", UUID, nullable=False),
        sa.Column(
            "authenticated_actor_role",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column("final_media_ref_id", UUID, nullable=False),
        sa.Column("final_media_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "production_package_artifact_version_id",
            UUID,
            nullable=False,
        ),
        sa.Column(
            "production_package_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("destination_binding_id", UUID, nullable=False),
        sa.Column(
            "destination_binding_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("command_id", UUID, nullable=False),
        sa.Column(
            "decision_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "warnings_acknowledged",
            JSONB,
            server_default=_jsonb_array(),
            nullable=False,
        ),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision in ('UPLOAD','DO_NOT_UPLOAD')",
            name="ck_final_video_decisions_decision",
        ),
        sa.CheckConstraint(
            "final_media_hash ~ '^[0-9a-f]{64}$' "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
            "and decision_hash ~ '^[0-9a-f]{64}$'",
            name="ck_final_video_decisions_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["final_review_candidate_id"], ["final_review_candidates.id"]
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["operator_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["final_media_ref_id"], ["final_media_refs.id"]),
        sa.ForeignKeyConstraint(
            ["production_package_artifact_version_id"],
            ["artifact_versions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "final_review_candidate_id",
            name="uq_final_video_decisions_candidate_id",
        ),
        sa.UniqueConstraint(
            "command_id",
            name="uq_final_video_decisions_command_id",
        ),
        sa.UniqueConstraint(
            "decision_hash",
            name="uq_final_video_decisions_decision_hash",
        ),
    )
    for index_name, columns in (
        ("ix_final_video_decisions_project_id", ["video_project_id"]),
        (
            "ix_final_video_decisions_final_media_ref_id",
            ["final_media_ref_id"],
        ),
        ("ix_final_video_decisions_created_at", ["created_at"]),
    ):
        op.create_index(index_name, "final_video_decisions", columns)


def _extend_human_upload_tasks() -> None:
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "DROP CONSTRAINT IF EXISTS "
        "ck_human_upload_tasks_ck_human_upload_tasks_state"
    )
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "DROP CONSTRAINT IF EXISTS ck_human_upload_tasks_state"
    )
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "ADD CONSTRAINT ck_human_upload_tasks_ck_human_upload_tasks_state "
        "CHECK (task_state in ("
        "'READY','UPLOADED','NEEDS_FIX','SKIPPED','CANCELLED',"
        "'READY_FOR_HUMAN_UPLOAD','HUMAN_UPLOAD_IN_PROGRESS',"
        "'UPLOADED_WAITING_BACKFILL','BACKFILLED_WAITING_VERIFICATION',"
        "'UPLOADED_VERIFIED','UPLOADED_UNVERIFIED','BLOCKED',"
        "'READY_FOR_OPERATOR','IN_PROGRESS','AWAITING_CONFIRMATION',"
        "'VERIFIED','CANCELED'))"
    )
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "DROP CONSTRAINT IF EXISTS "
        "ck_human_upload_tasks_ck_human_upload_tasks_target_platform"
    )
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "DROP CONSTRAINT IF EXISTS ck_human_upload_tasks_target_platform"
    )
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "ADD CONSTRAINT "
        "ck_human_upload_tasks_ck_human_upload_tasks_target_platform "
        "CHECK (target_platform in ("
        "'YOUTUBE_LONG','YOUTUBE_SHORTS','TIKTOK','FACEBOOK_REELS','YOUTUBE'))"
    )
    for column in (
        sa.Column(
            "schema_version",
            sa.String(length=16),
            server_default=sa.text("'v1'"),
            nullable=False,
        ),
        sa.Column("final_review_candidate_id", UUID, nullable=True),
        sa.Column("final_video_decision_id", UUID, nullable=True),
        sa.Column("final_media_ref_id", UUID, nullable=True),
        sa.Column("final_media_file_ref", sa.Text(), nullable=True),
        sa.Column("reviewed_checksum", sa.String(length=64), nullable=True),
        sa.Column(
            "production_package_artifact_version_id",
            UUID,
            nullable=True,
        ),
        sa.Column(
            "production_package_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("destination_binding_id", UUID, nullable=True),
        sa.Column(
            "destination_binding_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("channel_profile_version_id", UUID, nullable=True),
        sa.Column("policy_snapshot_id", UUID, nullable=True),
        sa.Column("production_lane", sa.String(length=40), nullable=True),
        sa.Column("content_mode", sa.String(length=40), nullable=True),
        sa.Column("series_plan_id", UUID, nullable=True),
        sa.Column("series_run_id", UUID, nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column(
            "standalone_reason_code",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("parent_video_project_id", UUID, nullable=True),
        sa.Column("parent_final_media_ref_id", UUID, nullable=True),
        sa.Column("archive_object_ref", sa.Text(), nullable=True),
        sa.Column("selected_file_name", sa.Text(), nullable=True),
        sa.Column("selected_file_ref", sa.Text(), nullable=True),
        sa.Column(
            "selected_file_checksum",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("attested_by_user_id", UUID, nullable=True),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_by_user_id", UUID, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_command_id", UUID, nullable=True),
        sa.Column("canceled_by_user_id", UUID, nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("human_upload_tasks", column)
    for name, column, target in (
        (
            "fk_hut_final_review_candidate",
            "final_review_candidate_id",
            "final_review_candidates",
        ),
        (
            "fk_hut_final_video_decision",
            "final_video_decision_id",
            "final_video_decisions",
        ),
        (
            "fk_human_upload_tasks_final_media_ref_id_final_media_refs",
            "final_media_ref_id",
            "final_media_refs",
        ),
        (
            "fk_hut_production_package_av",
            "production_package_artifact_version_id",
            "artifact_versions",
        ),
        (
            "fk_hut_channel_profile_version",
            "channel_profile_version_id",
            "channel_profile_versions",
        ),
        (
            "fk_hut_policy_snapshot",
            "policy_snapshot_id",
            "compiled_channel_policy_snapshots",
        ),
        (
            "fk_human_upload_tasks_series_plan_id_series_plans",
            "series_plan_id",
            "series_plans",
        ),
        (
            "fk_human_upload_tasks_series_run_id_series_runs",
            "series_run_id",
            "series_runs",
        ),
        (
            "fk_human_upload_tasks_parent_video_project_id_video_projects",
            "parent_video_project_id",
            "video_projects",
        ),
        (
            "fk_hut_parent_final_media",
            "parent_final_media_ref_id",
            "final_media_refs",
        ),
        (
            "fk_human_upload_tasks_attested_by_user_id_users",
            "attested_by_user_id",
            "users",
        ),
        (
            "fk_human_upload_tasks_started_by_user_id_users",
            "started_by_user_id",
            "users",
        ),
        (
            "fk_human_upload_tasks_canceled_by_user_id_users",
            "canceled_by_user_id",
            "users",
        ),
    ):
        op.create_foreign_key(name, "human_upload_tasks", target, [column], ["id"])
    op.create_unique_constraint(
        "uq_human_upload_tasks_final_video_decision_id",
        "human_upload_tasks",
        ["final_video_decision_id"],
    )
    op.create_unique_constraint(
        "uq_human_upload_tasks_cancel_command_id",
        "human_upload_tasks",
        ["cancel_command_id"],
    )
    for name, condition in (
        (
            "ck_human_upload_tasks_schema_version",
            "schema_version in ('v1','v2')",
        ),
        (
            "ck_human_upload_tasks_v2_binding",
            "(schema_version = 'v1') or "
            "(upload_card_id is null "
            "and first_scripted_video_package_id is null "
            "and publish_package_id is null "
            "and final_review_candidate_id is not null "
            "and final_video_decision_id is not null "
            "and final_media_ref_id is not null "
            "and final_media_file_ref is not null "
            "and reviewed_checksum ~ '^[0-9a-f]{64}$' "
            "and production_package_artifact_version_id is not null "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and destination_binding_id is not null "
            "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
            "and channel_profile_version_id is not null "
            "and policy_snapshot_id is not null "
            "and production_lane in "
            "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
            "and content_mode in ('SERIES_EPISODE','STANDALONE') "
            "and archive_object_ref is not null "
            "and task_state in "
            "('READY_FOR_OPERATOR','IN_PROGRESS','AWAITING_CONFIRMATION',"
            "'VERIFIED','CANCELED'))",
        ),
        (
            "ck_human_upload_tasks_v2_assignment",
            "(schema_version = 'v1') or "
            "((content_mode = 'SERIES_EPISODE' "
            "and series_plan_id is not null "
            "and series_run_id is not null "
            "and episode_number > 0 "
            "and standalone_reason_code is null) "
            "or (content_mode = 'STANDALONE' "
            "and series_plan_id is null "
            "and series_run_id is null "
            "and episode_number is null "
            "and standalone_reason_code is not null))",
        ),
        (
            "ck_human_upload_tasks_v2_attestation",
            "(schema_version = 'v1') or "
            "(task_state in ('READY_FOR_OPERATOR','CANCELED')) or "
            "(selected_file_name is not null "
            "and selected_file_ref is not null "
            "and selected_file_checksum = reviewed_checksum "
            "and attested_by_user_id is not null "
            "and attested_at is not null "
            "and started_by_user_id is not null "
            "and started_at is not null)",
        ),
        (
            "ck_human_upload_tasks_v2_parent_lineage",
            "(schema_version = 'v1') or "
            "(production_lane <> 'LONG_DERIVED_SHORT') or "
            "(parent_video_project_id is not null "
            "and parent_final_media_ref_id is not null)",
        ),
        (
            "ck_human_upload_tasks_v2_verified",
            "(schema_version = 'v1') or "
            "(task_state <> 'VERIFIED') or "
            "(actual_uploaded_video_id is not null and completed_at is not null)",
        ),
        (
            "ck_human_upload_tasks_v2_canceled",
            "(schema_version = 'v1') or "
            "(task_state <> 'CANCELED') or "
            "(cancel_command_id is not null "
            "and canceled_by_user_id is not null "
            "and canceled_at is not null)",
        ),
    ):
        op.create_check_constraint(name, "human_upload_tasks", condition)
    for index_name, columns in (
        (
            "ix_human_upload_tasks_final_review_candidate_id",
            ["final_review_candidate_id"],
        ),
        (
            "ix_human_upload_tasks_final_media_ref_id",
            ["final_media_ref_id"],
        ),
        (
            "ix_human_upload_tasks_production_package",
            ["production_package_artifact_version_id"],
        ),
        ("ix_human_upload_tasks_series_run_id", ["series_run_id"]),
    ):
        op.create_index(index_name, "human_upload_tasks", columns)


def _extend_manual_publish_confirmations() -> None:
    op.execute(
        "ALTER TABLE manual_publish_confirmations "
        "DROP CONSTRAINT IF EXISTS "
        "ck_manual_publish_confirmations_ck_manual_publish_confirmations_state"
    )
    op.execute(
        "ALTER TABLE manual_publish_confirmations "
        "DROP CONSTRAINT IF EXISTS "
        "ck_manual_publish_confirmations_ck_manual_publish_confi_8603"
    )
    op.execute(
        "ALTER TABLE manual_publish_confirmations "
        "DROP CONSTRAINT IF EXISTS ck_manual_publish_confirmations_state"
    )
    op.execute(
        "ALTER TABLE manual_publish_confirmations "
        "ADD CONSTRAINT ck_manual_publish_confirmations_state "
        "CHECK (confirmation_state in ("
        "'DRAFT','SUBMITTED','ACCEPTED','REVIEW_REQUIRED','REJECTED',"
        "'CANCELLED','VERIFIED','REJECTED_MISMATCH','BLOCKED_DESTINATION',"
        "'CORRECTION_REQUIRED','VARIANCE_ACCEPTED','CANCELED'))"
    )
    op.alter_column(
        "manual_publish_confirmations",
        "publish_handoff_package_id",
        existing_type=UUID,
        nullable=True,
    )
    for column in (
        sa.Column(
            "schema_version",
            sa.String(length=16),
            server_default=sa.text("'v1'"),
            nullable=False,
        ),
        sa.Column("command_id", UUID, nullable=True),
        sa.Column("confirmation_hash", sa.String(length=64), nullable=True),
        sa.Column("human_upload_task_id", UUID, nullable=True),
        sa.Column("final_review_candidate_id", UUID, nullable=True),
        sa.Column("final_video_decision_id", UUID, nullable=True),
        sa.Column("final_media_ref_id", UUID, nullable=True),
        sa.Column("reviewed_checksum", sa.String(length=64), nullable=True),
        sa.Column(
            "production_package_artifact_version_id",
            UUID,
            nullable=True,
        ),
        sa.Column(
            "production_package_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("channel_profile_version_id", UUID, nullable=True),
        sa.Column("platform_channel_id", sa.Text(), nullable=True),
        sa.Column("destination_account_identity", sa.Text(), nullable=True),
        sa.Column(
            "actual_duration_seconds",
            sa.Numeric(18, 6),
            nullable=True,
        ),
        sa.Column("thumbnail_confirmed", sa.Boolean(), nullable=True),
        sa.Column("caption_confirmed", sa.Boolean(), nullable=True),
        sa.Column("playlist_id", sa.Text(), nullable=True),
        sa.Column("playlist_order", sa.Integer(), nullable=True),
        sa.Column(
            "materiality_policy_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("variance_attested_by_user_id", UUID, nullable=True),
        sa.Column(
            "variance_attested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("corrected_by_user_id", UUID, nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "correction_history",
            JSONB,
            server_default=_jsonb_array(),
            nullable=False,
        ),
        sa.Column("verified_by_user_id", UUID, nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_command_id", UUID, nullable=True),
        sa.Column("verification_evidence_ref", sa.Text(), nullable=True),
        sa.Column(
            "verification_evidence_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("canceled_by_user_id", UUID, nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("manual_publish_confirmations", column)
    for name, column, target in (
        (
            "fk_mpc_human_upload_task",
            "human_upload_task_id",
            "human_upload_tasks",
        ),
        (
            "fk_mpc_final_review_candidate",
            "final_review_candidate_id",
            "final_review_candidates",
        ),
        (
            "fk_mpc_final_video_decision",
            "final_video_decision_id",
            "final_video_decisions",
        ),
        (
            "fk_mpc_final_media_ref",
            "final_media_ref_id",
            "final_media_refs",
        ),
        (
            "fk_mpc_production_package_av",
            "production_package_artifact_version_id",
            "artifact_versions",
        ),
        (
            "fk_mpc_channel_profile_version",
            "channel_profile_version_id",
            "channel_profile_versions",
        ),
        (
            "fk_mpc_variance_attested_user",
            "variance_attested_by_user_id",
            "users",
        ),
        (
            "fk_manual_publish_confirmations_corrected_by_user_id_users",
            "corrected_by_user_id",
            "users",
        ),
        (
            "fk_manual_publish_confirmations_verified_by_user_id_users",
            "verified_by_user_id",
            "users",
        ),
        (
            "fk_manual_publish_confirmations_canceled_by_user_id_users",
            "canceled_by_user_id",
            "users",
        ),
    ):
        op.create_foreign_key(
            name,
            "manual_publish_confirmations",
            target,
            [column],
            ["id"],
        )
    for name, columns in (
        ("uq_manual_publish_confirmations_command_id", ["command_id"]),
        (
            "uq_manual_publish_confirmations_human_upload_task_id",
            ["human_upload_task_id"],
        ),
        (
            "uq_manual_publish_confirmations_final_video_decision_id",
            ["final_video_decision_id"],
        ),
        (
            "uq_manual_publish_confirmations_verification_command_id",
            ["verification_command_id"],
        ),
    ):
        op.create_unique_constraint(
            name,
            "manual_publish_confirmations",
            columns,
        )
    for name, condition in (
        (
            "ck_manual_publish_confirmations_schema_version",
            "schema_version in ('v1','v2')",
        ),
        (
            "ck_manual_publish_confirmations_v2_binding",
            "(schema_version = 'v1' and publish_handoff_package_id is not null) "
            "or (schema_version = 'v2' "
            "and publish_handoff_package_id is null "
            "and confirmed_by_user_id is not null "
            "and human_upload_task_id is not null "
            "and final_review_candidate_id is not null "
            "and final_video_decision_id is not null "
            "and final_media_ref_id is not null "
            "and reviewed_checksum ~ '^[0-9a-f]{64}$' "
            "and production_package_artifact_version_id is not null "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and channel_profile_version_id is not null "
            "and destination_binding_id is not null "
            "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
            "and platform_channel_id is not null "
            "and destination_account_identity is not null "
            "and command_id is not null "
            "and confirmation_hash ~ '^[0-9a-f]{64}$' "
            "and actual_video_id is not null "
            "and actual_video_url is not null "
            "and actual_published_at is not null "
            "and actual_duration_seconds > 0 "
            "and thumbnail_confirmed is not null "
            "and caption_confirmed is not null "
            "and materiality_policy_hash ~ '^[0-9a-f]{64}$' "
            "and confirmation_state in "
            "('SUBMITTED','VERIFIED','REJECTED_MISMATCH',"
            "'BLOCKED_DESTINATION','CORRECTION_REQUIRED',"
            "'VARIANCE_ACCEPTED','CANCELED'))",
        ),
        (
            "ck_manual_publish_confirmations_v2_variance_attestation",
            "(schema_version = 'v1') or "
            "(confirmation_state <> 'VARIANCE_ACCEPTED') or "
            "(variance_attested_by_user_id is not null "
            "and variance_attested_at is not null)",
        ),
        (
            "ck_manual_publish_confirmations_v2_verified",
            "(schema_version = 'v1') or "
            "(confirmation_state <> 'VERIFIED') or "
            "(verified_by_user_id is not null "
            "and verified_at is not null "
            "and verification_command_id is not null "
            "and verification_evidence_ref is not null "
            "and verification_evidence_hash ~ '^[0-9a-f]{64}$')",
        ),
    ):
        op.create_check_constraint(
            name,
            "manual_publish_confirmations",
            condition,
        )
    op.create_index(
        "ix_manual_publish_confirmations_final_media_ref_id",
        "manual_publish_confirmations",
        ["final_media_ref_id"],
    )


def _extend_uploaded_videos() -> None:
    for column in (
        sa.Column(
            "schema_version",
            sa.String(length=16),
            server_default=sa.text("'v1'"),
            nullable=False,
        ),
        sa.Column("final_review_candidate_id", UUID, nullable=True),
        sa.Column("final_video_decision_id", UUID, nullable=True),
        sa.Column("final_media_ref_id", UUID, nullable=True),
        sa.Column(
            "production_package_artifact_version_id",
            UUID,
            nullable=True,
        ),
        sa.Column(
            "production_package_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("channel_profile_version_id", UUID, nullable=True),
        sa.Column("reviewed_checksum", sa.String(length=64), nullable=True),
        sa.Column("production_lane", sa.String(length=40), nullable=True),
        sa.Column("content_mode", sa.String(length=40), nullable=True),
        sa.Column("series_plan_id", UUID, nullable=True),
        sa.Column("series_run_id", UUID, nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column(
            "standalone_reason_code",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("parent_video_project_id", UUID, nullable=True),
        sa.Column("parent_final_media_ref_id", UUID, nullable=True),
        sa.Column("target_market_lineage", JSONB, nullable=True),
        sa.Column("archive_supplement", JSONB, nullable=True),
        sa.Column("archive_supplement_ref", sa.Text(), nullable=True),
        sa.Column(
            "archive_supplement_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("verified_event_id", UUID, nullable=True),
        sa.Column("analytics_ready_event_id", UUID, nullable=True),
        sa.Column(
            "analytics_ready_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    ):
        op.add_column("uploaded_videos", column)
    for name, column, target in (
        (
            "fk_uploaded_videos_final_review_candidate",
            "final_review_candidate_id",
            "final_review_candidates",
        ),
        (
            "fk_uploaded_videos_final_video_decision",
            "final_video_decision_id",
            "final_video_decisions",
        ),
        (
            "fk_uploaded_videos_final_media_ref_id_final_media_refs",
            "final_media_ref_id",
            "final_media_refs",
        ),
        (
            "fk_uploaded_videos_production_package_av",
            "production_package_artifact_version_id",
            "artifact_versions",
        ),
        (
            "fk_uploaded_videos_channel_profile_version",
            "channel_profile_version_id",
            "channel_profile_versions",
        ),
        (
            "fk_uploaded_videos_series_plan_id_series_plans",
            "series_plan_id",
            "series_plans",
        ),
        (
            "fk_uploaded_videos_series_run_id_series_runs",
            "series_run_id",
            "series_runs",
        ),
        (
            "fk_uploaded_videos_parent_video_project_id_video_projects",
            "parent_video_project_id",
            "video_projects",
        ),
        (
            "fk_uploaded_videos_parent_final_media_ref_id_final_media_refs",
            "parent_final_media_ref_id",
            "final_media_refs",
        ),
        (
            "fk_uploaded_videos_verified_event_id_domain_events",
            "verified_event_id",
            "domain_events",
        ),
        (
            "fk_uploaded_videos_analytics_ready_event_id_domain_events",
            "analytics_ready_event_id",
            "domain_events",
        ),
    ):
        op.create_foreign_key(name, "uploaded_videos", target, [column], ["id"])
    for name, columns in (
        (
            "uq_uploaded_videos_manual_publish_confirmation_id",
            ["manual_publish_confirmation_id"],
        ),
        (
            "uq_uploaded_videos_final_video_decision_id",
            ["final_video_decision_id"],
        ),
        ("uq_uploaded_videos_final_media_ref_id", ["final_media_ref_id"]),
        ("uq_uploaded_videos_verified_event_id", ["verified_event_id"]),
        (
            "uq_uploaded_videos_analytics_ready_event_id",
            ["analytics_ready_event_id"],
        ),
    ):
        op.create_unique_constraint(name, "uploaded_videos", columns)
    for name, condition in (
        (
            "ck_uploaded_videos_schema_version",
            "schema_version in ('v1','v2')",
        ),
        (
            "ck_uploaded_videos_v2_binding",
            "(schema_version = 'v1') or "
            "(video_project_id is not null "
            "and policy_snapshot_id is not null "
            "and manual_publish_confirmation_id is not null "
            "and human_upload_task_id is not null "
            "and final_review_candidate_id is not null "
            "and final_video_decision_id is not null "
            "and final_media_ref_id is not null "
            "and production_package_artifact_version_id is not null "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and channel_profile_version_id is not null "
            "and reviewed_checksum ~ '^[0-9a-f]{64}$' "
            "and destination_binding_id is not null "
            "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
            "and production_lane in "
            "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
            "and content_mode in ('SERIES_EPISODE','STANDALONE') "
            "and target_market_lineage is not null "
            "and archive_supplement is not null "
            "and archive_supplement_ref is not null "
            "and archive_supplement_hash ~ '^[0-9a-f]{64}$' "
            "and verification_status = 'VERIFIED' "
            "and analytics_sync_status = 'READY' "
            "and verified_event_id is not null "
            "and analytics_ready_event_id is not null "
            "and analytics_ready_at is not null)",
        ),
        (
            "ck_uploaded_videos_v2_assignment",
            "(schema_version = 'v1') or "
            "((content_mode = 'SERIES_EPISODE' "
            "and series_plan_id is not null "
            "and series_run_id is not null "
            "and episode_number > 0 "
            "and standalone_reason_code is null) "
            "or (content_mode = 'STANDALONE' "
            "and series_plan_id is null "
            "and series_run_id is null "
            "and episode_number is null "
            "and standalone_reason_code is not null))",
        ),
        (
            "ck_uploaded_videos_v2_parent_lineage",
            "(schema_version = 'v1') or "
            "(production_lane <> 'LONG_DERIVED_SHORT') or "
            "(parent_video_project_id is not null "
            "and parent_final_media_ref_id is not null)",
        ),
    ):
        op.create_check_constraint(name, "uploaded_videos", condition)
    for index_name, columns in (
        ("ix_uploaded_videos_final_media_ref_id", ["final_media_ref_id"]),
        (
            "ix_uploaded_videos_production_package",
            ["production_package_artifact_version_id"],
        ),
        ("ix_uploaded_videos_series_run_id", ["series_run_id"]),
    ):
        op.create_index(index_name, "uploaded_videos", columns)


def _create_series_episode_publications() -> None:
    op.create_table(
        "series_episode_publications",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=False),
        sa.Column("series_plan_id", UUID, nullable=False),
        sa.Column("series_run_id", UUID, nullable=False),
        sa.Column("episode_number", sa.Integer(), nullable=False),
        sa.Column("uploaded_video_id", UUID, nullable=False),
        sa.Column("final_video_decision_id", UUID, nullable=False),
        sa.Column("human_upload_task_id", UUID, nullable=False),
        sa.Column("manual_publish_confirmation_id", UUID, nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "episode_number > 0",
            name="ck_series_episode_publications_episode_positive",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["series_plan_id"], ["series_plans.id"]),
        sa.ForeignKeyConstraint(["series_run_id"], ["series_runs.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(
            ["final_video_decision_id"], ["final_video_decisions.id"]
        ),
        sa.ForeignKeyConstraint(["human_upload_task_id"], ["human_upload_tasks.id"]),
        sa.ForeignKeyConstraint(
            ["manual_publish_confirmation_id"],
            ["manual_publish_confirmations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "video_project_id",
            name="uq_series_episode_publications_project_id",
        ),
        sa.UniqueConstraint(
            "uploaded_video_id",
            name="uq_series_episode_publications_uploaded_video_id",
        ),
        sa.UniqueConstraint(
            "series_run_id",
            "episode_number",
            name="uq_series_episode_publications_run_episode",
        ),
    )
    for index_name, columns in (
        ("ix_series_episode_publications_series_plan_id", ["series_plan_id"]),
        ("ix_series_episode_publications_series_run_id", ["series_run_id"]),
        ("ix_series_episode_publications_created_at", ["created_at"]),
    ):
        op.create_index(index_name, "series_episode_publications", columns)


def _create_immutable_authority_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_vcos_final_publish_authority_change()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table_name in (
        "final_review_candidates",
        "final_video_decisions",
        "series_episode_publications",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_prevent_{table_name}_change
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION prevent_vcos_final_publish_authority_change();
            """
        )


def downgrade() -> None:
    _fail_closed_if_final_publish_authority_exists()
    for table_name in (
        "series_episode_publications",
        "final_video_decisions",
        "final_review_candidates",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_prevent_{table_name}_change ON {table_name}"
        )
    op.execute("DROP FUNCTION IF EXISTS prevent_vcos_final_publish_authority_change()")
    op.drop_table("series_episode_publications")
    _drop_uploaded_video_extensions()
    _drop_manual_publish_confirmation_extensions()
    _drop_human_upload_task_extensions()
    op.drop_table("final_video_decisions")
    op.drop_constraint(
        "fk_production_workflow_runs_final_review_candidate_id",
        "production_workflow_runs",
        type_="foreignkey",
    )
    op.drop_table("final_review_candidates")


def _fail_closed_if_final_publish_authority_exists() -> None:
    authority_predicate = """
        EXISTS (SELECT 1 FROM final_review_candidates)
        OR EXISTS (SELECT 1 FROM final_video_decisions)
        OR EXISTS (SELECT 1 FROM series_episode_publications)
        OR EXISTS (SELECT 1 FROM human_upload_tasks
                   WHERE schema_version = 'v2')
        OR EXISTS (SELECT 1 FROM manual_publish_confirmations
                   WHERE schema_version = 'v2')
        OR EXISTS (SELECT 1 FROM uploaded_videos
                   WHERE schema_version = 'v2')
        OR EXISTS (SELECT 1 FROM production_workflow_runs
                   WHERE final_review_candidate_id IS NOT NULL)
    """
    message = "0045 downgrade refused: authoritative final-review/publish v2 rows exist"
    if context.is_offline_mode():
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF {authority_predicate} THEN
                        RAISE EXCEPTION '{message}';
                    END IF;
                END
                $$;
                """
            )
        )
        return
    if op.get_bind().execute(sa.text(f"SELECT {authority_predicate}")).scalar_one():
        raise RuntimeError(message)


def _drop_uploaded_video_extensions() -> None:
    for index_name in (
        "ix_uploaded_videos_series_run_id",
        "ix_uploaded_videos_production_package",
        "ix_uploaded_videos_final_media_ref_id",
    ):
        op.drop_index(index_name, table_name="uploaded_videos")
    for name in (
        "ck_uploaded_videos_v2_parent_lineage",
        "ck_uploaded_videos_v2_assignment",
        "ck_uploaded_videos_v2_binding",
        "ck_uploaded_videos_schema_version",
    ):
        op.drop_constraint(name, "uploaded_videos", type_="check")
    for name in (
        "uq_uploaded_videos_analytics_ready_event_id",
        "uq_uploaded_videos_verified_event_id",
        "uq_uploaded_videos_final_media_ref_id",
        "uq_uploaded_videos_final_video_decision_id",
        "uq_uploaded_videos_manual_publish_confirmation_id",
    ):
        op.drop_constraint(name, "uploaded_videos", type_="unique")
    for name in (
        "fk_uploaded_videos_analytics_ready_event_id_domain_events",
        "fk_uploaded_videos_verified_event_id_domain_events",
        "fk_uploaded_videos_parent_final_media_ref_id_final_media_refs",
        "fk_uploaded_videos_parent_video_project_id_video_projects",
        "fk_uploaded_videos_series_run_id_series_runs",
        "fk_uploaded_videos_series_plan_id_series_plans",
        "fk_uploaded_videos_channel_profile_version",
        "fk_uploaded_videos_production_package_av",
        "fk_uploaded_videos_final_media_ref_id_final_media_refs",
        "fk_uploaded_videos_final_video_decision",
        "fk_uploaded_videos_final_review_candidate",
    ):
        op.drop_constraint(name, "uploaded_videos", type_="foreignkey")
    for column_name in (
        "analytics_ready_at",
        "analytics_ready_event_id",
        "verified_event_id",
        "archive_supplement_hash",
        "archive_supplement_ref",
        "archive_supplement",
        "target_market_lineage",
        "parent_final_media_ref_id",
        "parent_video_project_id",
        "standalone_reason_code",
        "episode_number",
        "series_run_id",
        "series_plan_id",
        "content_mode",
        "production_lane",
        "reviewed_checksum",
        "channel_profile_version_id",
        "production_package_hash",
        "production_package_artifact_version_id",
        "final_media_ref_id",
        "final_video_decision_id",
        "final_review_candidate_id",
        "schema_version",
    ):
        op.drop_column("uploaded_videos", column_name)


def _drop_manual_publish_confirmation_extensions() -> None:
    op.drop_index(
        "ix_manual_publish_confirmations_final_media_ref_id",
        table_name="manual_publish_confirmations",
    )
    for name in (
        "ck_manual_publish_confirmations_v2_verified",
        "ck_manual_publish_confirmations_v2_variance_attestation",
        "ck_manual_publish_confirmations_v2_binding",
        "ck_manual_publish_confirmations_schema_version",
    ):
        op.drop_constraint(
            name,
            "manual_publish_confirmations",
            type_="check",
        )
    for name in (
        "uq_manual_publish_confirmations_verification_command_id",
        "uq_manual_publish_confirmations_final_video_decision_id",
        "uq_manual_publish_confirmations_human_upload_task_id",
        "uq_manual_publish_confirmations_command_id",
    ):
        op.drop_constraint(
            name,
            "manual_publish_confirmations",
            type_="unique",
        )
    for name in (
        "fk_manual_publish_confirmations_canceled_by_user_id_users",
        "fk_manual_publish_confirmations_verified_by_user_id_users",
        "fk_manual_publish_confirmations_corrected_by_user_id_users",
        "fk_mpc_variance_attested_user",
        "fk_mpc_channel_profile_version",
        "fk_mpc_production_package_av",
        "fk_mpc_final_media_ref",
        "fk_mpc_final_video_decision",
        "fk_mpc_final_review_candidate",
        "fk_mpc_human_upload_task",
    ):
        op.drop_constraint(
            name,
            "manual_publish_confirmations",
            type_="foreignkey",
        )
    for column_name in (
        "canceled_at",
        "canceled_by_user_id",
        "verification_evidence_hash",
        "verification_evidence_ref",
        "verification_command_id",
        "verified_at",
        "verified_by_user_id",
        "correction_history",
        "corrected_at",
        "corrected_by_user_id",
        "variance_attested_at",
        "variance_attested_by_user_id",
        "materiality_policy_hash",
        "playlist_order",
        "playlist_id",
        "caption_confirmed",
        "thumbnail_confirmed",
        "actual_duration_seconds",
        "destination_account_identity",
        "platform_channel_id",
        "channel_profile_version_id",
        "production_package_hash",
        "production_package_artifact_version_id",
        "reviewed_checksum",
        "final_media_ref_id",
        "final_video_decision_id",
        "final_review_candidate_id",
        "human_upload_task_id",
        "confirmation_hash",
        "command_id",
        "schema_version",
    ):
        op.drop_column("manual_publish_confirmations", column_name)
    op.alter_column(
        "manual_publish_confirmations",
        "publish_handoff_package_id",
        existing_type=UUID,
        nullable=False,
    )
    op.execute(
        "ALTER TABLE manual_publish_confirmations "
        "DROP CONSTRAINT IF EXISTS "
        "ck_manual_publish_confirmations_ck_manual_publish_confirmations_state"
    )
    op.execute(
        "ALTER TABLE manual_publish_confirmations "
        "DROP CONSTRAINT IF EXISTS "
        "ck_manual_publish_confirmations_ck_manual_publish_confi_8603"
    )
    op.execute(
        "ALTER TABLE manual_publish_confirmations "
        "DROP CONSTRAINT IF EXISTS ck_manual_publish_confirmations_state"
    )
    op.execute(
        "ALTER TABLE manual_publish_confirmations "
        "ADD CONSTRAINT ck_manual_publish_confirmations_state "
        "CHECK (confirmation_state in ("
        "'DRAFT','SUBMITTED','ACCEPTED','REVIEW_REQUIRED','REJECTED',"
        "'CANCELLED'))"
    )


def _drop_human_upload_task_extensions() -> None:
    for index_name in (
        "ix_human_upload_tasks_series_run_id",
        "ix_human_upload_tasks_production_package",
        "ix_human_upload_tasks_final_media_ref_id",
        "ix_human_upload_tasks_final_review_candidate_id",
    ):
        op.drop_index(index_name, table_name="human_upload_tasks")
    for name in (
        "ck_human_upload_tasks_v2_canceled",
        "ck_human_upload_tasks_v2_verified",
        "ck_human_upload_tasks_v2_parent_lineage",
        "ck_human_upload_tasks_v2_attestation",
        "ck_human_upload_tasks_v2_assignment",
        "ck_human_upload_tasks_v2_binding",
        "ck_human_upload_tasks_schema_version",
    ):
        op.drop_constraint(name, "human_upload_tasks", type_="check")
    op.drop_constraint(
        "uq_human_upload_tasks_final_video_decision_id",
        "human_upload_tasks",
        type_="unique",
    )
    op.drop_constraint(
        "uq_human_upload_tasks_cancel_command_id",
        "human_upload_tasks",
        type_="unique",
    )
    for name in (
        "fk_human_upload_tasks_canceled_by_user_id_users",
        "fk_human_upload_tasks_started_by_user_id_users",
        "fk_human_upload_tasks_attested_by_user_id_users",
        "fk_hut_parent_final_media",
        "fk_human_upload_tasks_parent_video_project_id_video_projects",
        "fk_human_upload_tasks_series_run_id_series_runs",
        "fk_human_upload_tasks_series_plan_id_series_plans",
        "fk_hut_policy_snapshot",
        "fk_hut_channel_profile_version",
        "fk_hut_production_package_av",
        "fk_human_upload_tasks_final_media_ref_id_final_media_refs",
        "fk_hut_final_video_decision",
        "fk_hut_final_review_candidate",
    ):
        op.drop_constraint(name, "human_upload_tasks", type_="foreignkey")
    for column_name in (
        "canceled_at",
        "canceled_by_user_id",
        "cancel_command_id",
        "started_at",
        "started_by_user_id",
        "attested_at",
        "attested_by_user_id",
        "selected_file_checksum",
        "selected_file_ref",
        "selected_file_name",
        "archive_object_ref",
        "parent_final_media_ref_id",
        "parent_video_project_id",
        "standalone_reason_code",
        "episode_number",
        "series_run_id",
        "series_plan_id",
        "content_mode",
        "production_lane",
        "policy_snapshot_id",
        "channel_profile_version_id",
        "destination_binding_fingerprint",
        "destination_binding_id",
        "production_package_hash",
        "production_package_artifact_version_id",
        "reviewed_checksum",
        "final_media_file_ref",
        "final_media_ref_id",
        "final_video_decision_id",
        "final_review_candidate_id",
        "schema_version",
    ):
        op.drop_column("human_upload_tasks", column_name)
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "DROP CONSTRAINT IF EXISTS "
        "ck_human_upload_tasks_ck_human_upload_tasks_state"
    )
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "DROP CONSTRAINT IF EXISTS ck_human_upload_tasks_state"
    )
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "ADD CONSTRAINT ck_human_upload_tasks_ck_human_upload_tasks_state "
        "CHECK (task_state in "
        "('READY','UPLOADED','NEEDS_FIX','SKIPPED','CANCELLED',"
        "'READY_FOR_HUMAN_UPLOAD','HUMAN_UPLOAD_IN_PROGRESS',"
        "'UPLOADED_WAITING_BACKFILL','BACKFILLED_WAITING_VERIFICATION',"
        "'UPLOADED_VERIFIED','UPLOADED_UNVERIFIED','BLOCKED'))"
    )
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "DROP CONSTRAINT IF EXISTS "
        "ck_human_upload_tasks_ck_human_upload_tasks_target_platform"
    )
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "DROP CONSTRAINT IF EXISTS ck_human_upload_tasks_target_platform"
    )
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "ADD CONSTRAINT "
        "ck_human_upload_tasks_ck_human_upload_tasks_target_platform "
        "CHECK (target_platform in ("
        "'YOUTUBE_LONG','YOUTUBE_SHORTS','TIKTOK','FACEBOOK_REELS'))"
    )
