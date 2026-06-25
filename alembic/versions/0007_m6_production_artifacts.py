"""M6 production artifact and local media foundation

Revision ID: 0007_m6_production
Revises: 0006_m5_daily_run
Create Date: 2026-06-25 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007_m6_production"
down_revision: str | None = "0006_m5_daily_run"
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


def upgrade() -> None:
    op.create_table(
        "production_artifact_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_project_admission_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_mode", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("script_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("voice_timeline_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("caption_track_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("visual_plan_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scene_manifest_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("render_spec_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("asset_manifest_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_manifest_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("render_package_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("media_qc_report_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accessibility_qc_report_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("run_mode in ('MOCK','LOCAL_FIXTURE','REAL_DISABLED')", name="ck_production_artifact_runs_run_mode"),
        sa.CheckConstraint("status in ('PENDING','RUNNING','COMPLETED','REVIEW_REQUIRED','BLOCKED','FAILED','CANCELLED')", name="ck_production_artifact_runs_status"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["source_project_admission_decision_id"], ["project_admission_decisions.id"]),
        sa.ForeignKeyConstraint(["script_artifact_version_id"], ["artifact_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_production_artifact_runs_company_id", "production_artifact_runs", ["company_id"])
    op.create_index("ix_production_artifact_runs_channel_workspace_id", "production_artifact_runs", ["channel_workspace_id"])
    op.create_index("ix_production_artifact_runs_video_project_id", "production_artifact_runs", ["video_project_id"])
    op.create_index("ix_production_artifact_runs_policy_snapshot_id", "production_artifact_runs", ["policy_snapshot_id"])
    op.create_index("ix_production_artifact_runs_status", "production_artifact_runs", ["status"])
    op.create_index("ix_production_artifact_runs_created_at", "production_artifact_runs", ["created_at"])

    op.create_table(
        "voice_timeline_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("script_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("total_duration_seconds", sa.Numeric(18, 6), nullable=False),
        sa.Column("timing_source", sa.String(length=40), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("timeline_hash", sa.Text(), nullable=False),
        _created_at(),
        sa.CheckConstraint("timing_source in ('MOCK_TTS','ESTIMATED','LOCAL_AUDIO_ANALYSIS')", name="ck_voice_timeline_snapshots_timing_source"),
        sa.CheckConstraint("confidence_level in ('HIGH','MEDIUM','LOW','UNKNOWN')", name="ck_voice_timeline_snapshots_confidence"),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["script_artifact_version_id"], ["artifact_versions.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_voice_timeline_snapshots_run_id", "voice_timeline_snapshots", ["production_artifact_run_id"])
    op.create_index("ix_voice_timeline_snapshots_video_project_id", "voice_timeline_snapshots", ["video_project_id"])
    op.create_index("ix_voice_timeline_snapshots_created_at", "voice_timeline_snapshots", ["created_at"])

    op.create_table(
        "caption_track_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voice_timeline_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("caption_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("srt_text", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=40), nullable=True),
        sa.Column("caption_hash", sa.Text(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["voice_timeline_snapshot_id"], ["voice_timeline_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_caption_track_snapshots_run_id", "caption_track_snapshots", ["production_artifact_run_id"])
    op.create_index("ix_caption_track_snapshots_video_project_id", "caption_track_snapshots", ["video_project_id"])
    op.create_index("ix_caption_track_snapshots_voice_timeline_id", "caption_track_snapshots", ["voice_timeline_snapshot_id"])
    op.create_index("ix_caption_track_snapshots_created_at", "caption_track_snapshots", ["created_at"])

    op.create_table(
        "visual_plan_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voice_timeline_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("caption_track_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("visual_plan_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("visual_plan_hash", sa.Text(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["voice_timeline_snapshot_id"], ["voice_timeline_snapshots.id"]),
        sa.ForeignKeyConstraint(["caption_track_snapshot_id"], ["caption_track_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_visual_plan_snapshots_run_id", "visual_plan_snapshots", ["production_artifact_run_id"])
    op.create_index("ix_visual_plan_snapshots_video_project_id", "visual_plan_snapshots", ["video_project_id"])
    op.create_index("ix_visual_plan_snapshots_voice_timeline_id", "visual_plan_snapshots", ["voice_timeline_snapshot_id"])
    op.create_index("ix_visual_plan_snapshots_created_at", "visual_plan_snapshots", ["created_at"])

    op.create_table(
        "scene_manifest_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visual_plan_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_manifest_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("scene_manifest_hash", sa.Text(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["visual_plan_snapshot_id"], ["visual_plan_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scene_manifest_snapshots_run_id", "scene_manifest_snapshots", ["production_artifact_run_id"])
    op.create_index("ix_scene_manifest_snapshots_video_project_id", "scene_manifest_snapshots", ["video_project_id"])
    op.create_index("ix_scene_manifest_snapshots_visual_plan_id", "scene_manifest_snapshots", ["visual_plan_snapshot_id"])
    op.create_index("ix_scene_manifest_snapshots_created_at", "scene_manifest_snapshots", ["created_at"])

    op.create_table(
        "asset_manifest_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_manifest_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_manifest_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("asset_manifest_hash", sa.Text(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["scene_manifest_snapshot_id"], ["scene_manifest_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_manifest_snapshots_run_id", "asset_manifest_snapshots", ["production_artifact_run_id"])
    op.create_index("ix_asset_manifest_snapshots_video_project_id", "asset_manifest_snapshots", ["video_project_id"])
    op.create_index("ix_asset_manifest_snapshots_scene_manifest_id", "asset_manifest_snapshots", ["scene_manifest_snapshot_id"])
    op.create_index("ix_asset_manifest_snapshots_created_at", "asset_manifest_snapshots", ["created_at"])

    op.create_table(
        "source_manifest_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_manifest_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_manifest_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("source_manifest_hash", sa.Text(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["asset_manifest_snapshot_id"], ["asset_manifest_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_manifest_snapshots_run_id", "source_manifest_snapshots", ["production_artifact_run_id"])
    op.create_index("ix_source_manifest_snapshots_video_project_id", "source_manifest_snapshots", ["video_project_id"])
    op.create_index("ix_source_manifest_snapshots_asset_manifest_id", "source_manifest_snapshots", ["asset_manifest_snapshot_id"])
    op.create_index("ix_source_manifest_snapshots_created_at", "source_manifest_snapshots", ["created_at"])

    op.create_table(
        "render_spec_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voice_timeline_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visual_plan_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("caption_track_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_manifest_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_manifest_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("render_spec_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("render_spec_hash", sa.Text(), nullable=False),
        sa.Column("validation_state", sa.String(length=40), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        _created_at(),
        sa.CheckConstraint("validation_state in ('PASS','REVIEW_REQUIRED','BLOCK')", name="ck_render_spec_snapshots_validation_state"),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["voice_timeline_snapshot_id"], ["voice_timeline_snapshots.id"]),
        sa.ForeignKeyConstraint(["visual_plan_snapshot_id"], ["visual_plan_snapshots.id"]),
        sa.ForeignKeyConstraint(["caption_track_snapshot_id"], ["caption_track_snapshots.id"]),
        sa.ForeignKeyConstraint(["asset_manifest_snapshot_id"], ["asset_manifest_snapshots.id"]),
        sa.ForeignKeyConstraint(["scene_manifest_snapshot_id"], ["scene_manifest_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_render_spec_snapshots_run_id", "render_spec_snapshots", ["production_artifact_run_id"])
    op.create_index("ix_render_spec_snapshots_video_project_id", "render_spec_snapshots", ["video_project_id"])
    op.create_index("ix_render_spec_snapshots_validation_state", "render_spec_snapshots", ["validation_state"])
    op.create_index("ix_render_spec_snapshots_created_at", "render_spec_snapshots", ["created_at"])

    op.create_table(
        "media_render_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_spec_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_variant_id", sa.String(length=120), nullable=True),
        sa.Column("renderer_key", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output_ref", JSONB, nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message_redacted", sa.Text(), nullable=True),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("renderer_key in ('LOCAL_FFMPEG','MOCK_RENDERER','REAL_DISABLED')", name="ck_media_render_jobs_renderer_key"),
        sa.CheckConstraint("status in ('PENDING','RUNNING','COMPLETED','REVIEW_REQUIRED','BLOCKED','FAILED')", name="ck_media_render_jobs_status"),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["render_spec_snapshot_id"], ["render_spec_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_render_jobs_run_id", "media_render_jobs", ["production_artifact_run_id"])
    op.create_index("ix_media_render_jobs_video_project_id", "media_render_jobs", ["video_project_id"])
    op.create_index("ix_media_render_jobs_render_spec_id", "media_render_jobs", ["render_spec_snapshot_id"])
    op.create_index("ix_media_render_jobs_status", "media_render_jobs", ["status"])
    op.create_index("ix_media_render_jobs_created_at", "media_render_jobs", ["created_at"])

    op.create_table(
        "render_package_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_render_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_spec_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("final_video_ref", JSONB, nullable=True),
        sa.Column("thumbnail_ref", JSONB, nullable=True),
        sa.Column("caption_ref", JSONB, nullable=True),
        sa.Column("manifest_ref", JSONB, nullable=True),
        sa.Column("file_manifest", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("checksum_manifest", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("duration_seconds", sa.Numeric(18, 6), nullable=True),
        sa.Column("variant_outputs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("package_state", sa.String(length=40), nullable=False),
        _created_at(),
        sa.CheckConstraint("package_state in ('CREATED','QC_PASSED','QC_REVIEW_REQUIRED','QC_BLOCKED','FAILED')", name="ck_render_package_snapshots_package_state"),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["media_render_job_id"], ["media_render_jobs.id"]),
        sa.ForeignKeyConstraint(["render_spec_snapshot_id"], ["render_spec_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_render_package_snapshots_run_id", "render_package_snapshots", ["production_artifact_run_id"])
    op.create_index("ix_render_package_snapshots_video_project_id", "render_package_snapshots", ["video_project_id"])
    op.create_index("ix_render_package_snapshots_render_spec_id", "render_package_snapshots", ["render_spec_snapshot_id"])
    op.create_index("ix_render_package_snapshots_created_at", "render_package_snapshots", ["created_at"])

    op.create_table(
        "media_qc_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_package_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("render_spec_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("qc_state", sa.String(length=40), nullable=False),
        sa.Column("duration_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("scene_coverage_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("caption_alignment_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("audio_presence_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("file_integrity_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("manifest_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("variant_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        _created_at(),
        sa.CheckConstraint("qc_state in ('PASS','REVIEW_REQUIRED','BLOCK','FAILED')", name="ck_media_qc_reports_qc_state"),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["render_package_snapshot_id"], ["render_package_snapshots.id"]),
        sa.ForeignKeyConstraint(["render_spec_snapshot_id"], ["render_spec_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_qc_reports_run_id", "media_qc_reports", ["production_artifact_run_id"])
    op.create_index("ix_media_qc_reports_video_project_id", "media_qc_reports", ["video_project_id"])
    op.create_index("ix_media_qc_reports_state", "media_qc_reports", ["qc_state"])
    op.create_index("ix_media_qc_reports_created_at", "media_qc_reports", ["created_at"])

    op.create_table(
        "accessibility_qc_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_artifact_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("caption_track_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("render_package_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("qc_state", sa.String(length=40), nullable=False),
        sa.Column("caption_presence_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("caption_readability_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("safe_area_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("flashing_risk_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("disclosure_placement_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("pronunciation_check", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        _created_at(),
        sa.CheckConstraint("qc_state in ('PASS','REVIEW_REQUIRED','BLOCK','FAILED')", name="ck_accessibility_qc_reports_qc_state"),
        sa.ForeignKeyConstraint(["production_artifact_run_id"], ["production_artifact_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["caption_track_snapshot_id"], ["caption_track_snapshots.id"]),
        sa.ForeignKeyConstraint(["render_package_snapshot_id"], ["render_package_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_accessibility_qc_reports_run_id", "accessibility_qc_reports", ["production_artifact_run_id"])
    op.create_index("ix_accessibility_qc_reports_video_project_id", "accessibility_qc_reports", ["video_project_id"])
    op.create_index("ix_accessibility_qc_reports_state", "accessibility_qc_reports", ["qc_state"])
    op.create_index("ix_accessibility_qc_reports_created_at", "accessibility_qc_reports", ["created_at"])

    op.create_table(
        "pronunciation_dictionary_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("pronunciation_hint", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=40), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("status in ('ACTIVE','DISABLED','NEEDS_REVIEW')", name="ck_pronunciation_dictionary_entries_status"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pronunciation_dictionary_entries_company_id", "pronunciation_dictionary_entries", ["company_id"])
    op.create_index("ix_pronunciation_dictionary_entries_channel_id", "pronunciation_dictionary_entries", ["channel_workspace_id"])
    op.create_index("ix_pronunciation_dictionary_entries_status", "pronunciation_dictionary_entries", ["status"])
    op.create_index("ix_pronunciation_dictionary_entries_created_at", "pronunciation_dictionary_entries", ["created_at"])


def downgrade() -> None:
    for table, indexes in [
        ("pronunciation_dictionary_entries", [
            "ix_pronunciation_dictionary_entries_created_at",
            "ix_pronunciation_dictionary_entries_status",
            "ix_pronunciation_dictionary_entries_channel_id",
            "ix_pronunciation_dictionary_entries_company_id",
        ]),
        ("accessibility_qc_reports", [
            "ix_accessibility_qc_reports_created_at",
            "ix_accessibility_qc_reports_state",
            "ix_accessibility_qc_reports_video_project_id",
            "ix_accessibility_qc_reports_run_id",
        ]),
        ("media_qc_reports", [
            "ix_media_qc_reports_created_at",
            "ix_media_qc_reports_state",
            "ix_media_qc_reports_video_project_id",
            "ix_media_qc_reports_run_id",
        ]),
        ("render_package_snapshots", [
            "ix_render_package_snapshots_created_at",
            "ix_render_package_snapshots_render_spec_id",
            "ix_render_package_snapshots_video_project_id",
            "ix_render_package_snapshots_run_id",
        ]),
        ("media_render_jobs", [
            "ix_media_render_jobs_created_at",
            "ix_media_render_jobs_status",
            "ix_media_render_jobs_render_spec_id",
            "ix_media_render_jobs_video_project_id",
            "ix_media_render_jobs_run_id",
        ]),
        ("render_spec_snapshots", [
            "ix_render_spec_snapshots_created_at",
            "ix_render_spec_snapshots_validation_state",
            "ix_render_spec_snapshots_video_project_id",
            "ix_render_spec_snapshots_run_id",
        ]),
        ("source_manifest_snapshots", [
            "ix_source_manifest_snapshots_created_at",
            "ix_source_manifest_snapshots_asset_manifest_id",
            "ix_source_manifest_snapshots_video_project_id",
            "ix_source_manifest_snapshots_run_id",
        ]),
        ("asset_manifest_snapshots", [
            "ix_asset_manifest_snapshots_created_at",
            "ix_asset_manifest_snapshots_scene_manifest_id",
            "ix_asset_manifest_snapshots_video_project_id",
            "ix_asset_manifest_snapshots_run_id",
        ]),
        ("scene_manifest_snapshots", [
            "ix_scene_manifest_snapshots_created_at",
            "ix_scene_manifest_snapshots_visual_plan_id",
            "ix_scene_manifest_snapshots_video_project_id",
            "ix_scene_manifest_snapshots_run_id",
        ]),
        ("visual_plan_snapshots", [
            "ix_visual_plan_snapshots_created_at",
            "ix_visual_plan_snapshots_voice_timeline_id",
            "ix_visual_plan_snapshots_video_project_id",
            "ix_visual_plan_snapshots_run_id",
        ]),
        ("caption_track_snapshots", [
            "ix_caption_track_snapshots_created_at",
            "ix_caption_track_snapshots_voice_timeline_id",
            "ix_caption_track_snapshots_video_project_id",
            "ix_caption_track_snapshots_run_id",
        ]),
        ("voice_timeline_snapshots", [
            "ix_voice_timeline_snapshots_created_at",
            "ix_voice_timeline_snapshots_video_project_id",
            "ix_voice_timeline_snapshots_run_id",
        ]),
        ("production_artifact_runs", [
            "ix_production_artifact_runs_created_at",
            "ix_production_artifact_runs_status",
            "ix_production_artifact_runs_policy_snapshot_id",
            "ix_production_artifact_runs_video_project_id",
            "ix_production_artifact_runs_channel_workspace_id",
            "ix_production_artifact_runs_company_id",
        ]),
    ]:
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
