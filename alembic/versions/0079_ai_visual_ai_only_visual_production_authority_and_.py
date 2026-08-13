"""Seal AI-only visual production and governed first-video replacement.

Revision ID: 0079_ai_visual
Revises: 0078_v2_drive_recovery_clock

This migration is additive for all historical rows.  Existing workflow and
final-review rows keep null AI-visual bindings; only newly bound AI-visual
rows are subject to the cross-table authority seals below.  Provider effects
use a one-call, compare-and-swap state journal and immutable provider identity.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0079_ai_visual"
down_revision: str | None = "0078_v2_drive_recovery_clock"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    _widen_workflow_visual_stage()
    _create_rerender_authorities()
    _create_visual_production_runs()
    _create_style_and_scene_authorities()
    _create_asset_effects()
    _create_asset_manifests()
    _add_workflow_and_candidate_bindings()
    _create_replacement_lineages()
    _install_database_seals()


def downgrade() -> None:
    raise RuntimeError(
        "0079 AI-only visual production authority is forward-only; erasing "
        "provider-effect or replacement lineage is prohibited"
    )


def _widen_workflow_visual_stage() -> None:
    replacements = (
        (
            "production_workflow_runs",
            "ck_production_workflow_runs_production_workflow_runs_state",
            "state in ("
            "'PLANNING_PENDING','PLANNING_RUNNING','ASSIGNMENT_READY',"
            "'RESEARCH_PENDING','RESEARCH_RUNNING','PACKAGE_PENDING',"
            "'PACKAGE_RUNNING','READY_FOR_PRODUCTION','MEDIA_PENDING',"
            "'MEDIA_RUNNING','VISUAL_PENDING','VISUAL_RUNNING','RENDER_PENDING',"
            "'RENDER_RUNNING','QC_PENDING','QC_RUNNING','ARCHIVE_PENDING',"
            "'ARCHIVE_RUNNING','PAUSED_AFTER_NATIVE_RENDER','FINAL_REVIEW_READY',"
            "'BLOCKED','RETRY_SCHEDULED','CANCELED','FAILED_TERMINAL',"
            "'DEAD_LETTERED','SUPERSEDED')",
        ),
        (
            "production_workflow_runs",
            "ck_production_workflow_runs_production_workflow_runs_stage",
            "current_stage in ("
            "'PLANNING','PREFLIGHT','ADMISSION','RESEARCH','PACKAGE','READINESS',"
            "'MEDIA','VISUAL','RENDER','QC','ARCHIVE','FINALIZE')",
        ),
        (
            "workflow_command_receipts",
            "ck_workflow_command_receipts_workflow_command_receipts_stage",
            "stage in ("
            "'PLANNING','PREFLIGHT','ADMISSION','RESEARCH','PACKAGE','READINESS',"
            "'MEDIA','VISUAL','RENDER','QC','ARCHIVE','FINALIZE')",
        ),
        (
            "workflow_recovery_receipts",
            "ck_workflow_recovery_receipts_ck_workflow_recovery_rece_6992",
            "failed_stage in ("
            "'PLANNING','PREFLIGHT','ADMISSION','RESEARCH','PACKAGE','READINESS',"
            "'MEDIA','VISUAL','RENDER','QC','ARCHIVE','FINALIZE')",
        ),
        (
            "v2_production_effect_ledger",
            "ck_v2_production_effect_ledger_ck_v2_production_effect__9581",
            "stage in ('MEDIA','VISUAL','RENDER','QC','ARCHIVE')",
        ),
    )
    for table, name, predicate in replacements:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({predicate})")


def _create_rerender_authorities() -> None:
    op.create_table(
        "ai_visual_rerender_authorities",
        sa.Column("id", UUID, nullable=False),
        sa.Column("authorized_visual_production_run_id", UUID, nullable=False),
        sa.Column("source_workflow_run_id", UUID, nullable=False),
        sa.Column("replacement_workflow_run_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=False),
        sa.Column("production_package_artifact_version_id", UUID, nullable=False),
        sa.Column("production_package_hash", sa.String(64), nullable=False),
        sa.Column(
            "production_readiness_receipt_artifact_version_id", UUID, nullable=False
        ),
        sa.Column("production_readiness_receipt_hash", sa.String(64), nullable=False),
        sa.Column("script_artifact_version_id", UUID, nullable=False),
        sa.Column("script_content_hash", sa.String(64), nullable=False),
        sa.Column("canonical_narration_hash", sa.String(64), nullable=False),
        sa.Column("audio_ref", sa.Text(), nullable=False),
        sa.Column("audio_checksum", sa.String(64), nullable=False),
        sa.Column("audio_duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("timed_words_artifact_version_id", UUID, nullable=False),
        sa.Column("timed_words_hash", sa.String(64), nullable=False),
        sa.Column("caption_artifact_version_id", UUID, nullable=False),
        sa.Column("caption_hash", sa.String(64), nullable=False),
        sa.Column("caption_checksum", sa.String(64), nullable=False),
        sa.Column("subtitle_qc_artifact_version_id", UUID, nullable=False),
        sa.Column("subtitle_qc_hash", sa.String(64), nullable=False),
        sa.Column("rejected_final_media_ref_id", UUID, nullable=False),
        sa.Column("rejected_final_media_hash", sa.String(64), nullable=False),
        sa.Column("rejected_final_review_candidate_id", UUID, nullable=False),
        sa.Column(
            "rejected_final_review_candidate_hash", sa.String(64), nullable=False
        ),
        sa.Column("rejected_visual_policy", sa.String(120), nullable=False),
        sa.Column("production_visual_policy_version", sa.String(120), nullable=False),
        sa.Column("production_visual_policy_ref", sa.Text(), nullable=False),
        sa.Column("production_visual_policy_hash", sa.String(64), nullable=False),
        sa.Column("budget_reservation_id", UUID, nullable=False),
        sa.Column("budget_reservation_ref", sa.Text(), nullable=False),
        sa.Column("budget_authority_hash", sa.String(64), nullable=False),
        sa.Column("maximum_total_cost_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("maximum_scene_count", sa.Integer(), nullable=False),
        sa.Column("maximum_image_submissions", sa.Integer(), nullable=False),
        sa.Column("maximum_video_submissions", sa.Integer(), nullable=False),
        sa.Column("maximum_tts_submissions", sa.Integer(), nullable=False),
        sa.Column("maximum_forced_alignment_submissions", sa.Integer(), nullable=False),
        sa.Column("narration_timing_recovery_authority_id", UUID, nullable=False),
        sa.Column(
            "narration_timing_recovery_authority_hash", sa.String(64), nullable=False
        ),
        sa.Column("narration_timing_recovery_receipt_id", UUID, nullable=False),
        sa.Column(
            "narration_timing_recovery_receipt_hash", sa.String(64), nullable=False
        ),
        sa.Column("automatic_publish", sa.Boolean(), nullable=False),
        sa.Column("authorized_by_actor_type", sa.String(80), nullable=False),
        sa.Column("authorized_by_actor_id", UUID, nullable=False),
        sa.Column("authorized_by_actor_role", sa.String(80), nullable=False),
        sa.Column("authority_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("authorized_visual_production_run_id"),
        sa.UniqueConstraint("rejected_final_media_ref_id"),
        sa.UniqueConstraint("rejected_final_review_candidate_id"),
        sa.UniqueConstraint("authority_hash"),
        sa.UniqueConstraint(
            "source_workflow_run_id",
            name="uq_ai_visual_rerender_authority_source_workflow",
        ),
        sa.UniqueConstraint(
            "replacement_workflow_run_id",
            name="uq_ai_visual_rerender_authority_replacement_workflow",
        ),
        sa.CheckConstraint(
            "production_visual_policy_version = "
            "'vcos.production-visual-policy.ai-only.v1' and "
            "source_workflow_run_id <> replacement_workflow_run_id and "
            "rejected_visual_policy = 'NATIVE_EXPLANATORY_DIAGRAM' and "
            "maximum_tts_submissions = 0 and "
            "maximum_forced_alignment_submissions = 0 and "
            "maximum_scene_count between 1 and 256 and "
            "maximum_image_submissions between 0 and maximum_scene_count and "
            "maximum_video_submissions between 0 and maximum_scene_count and "
            "maximum_image_submissions + maximum_video_submissions >= 1 and "
            "maximum_total_cost_usd > 0 and automatic_publish = false",
            name="ck_ai_visual_rerender_authority_bounds",
        ),
        sa.CheckConstraint(
            "production_package_hash ~ '^[0-9a-f]{64}$' and "
            "production_readiness_receipt_hash ~ '^[0-9a-f]{64}$' and "
            "script_content_hash ~ '^[0-9a-f]{64}$' and "
            "canonical_narration_hash ~ '^[0-9a-f]{64}$' and "
            "audio_checksum ~ '^[0-9a-f]{64}$' and "
            "timed_words_hash ~ '^[0-9a-f]{64}$' and "
            "caption_hash ~ '^[0-9a-f]{64}$' and "
            "caption_checksum ~ '^[0-9a-f]{64}$' and "
            "subtitle_qc_hash ~ '^[0-9a-f]{64}$' and "
            "narration_timing_recovery_authority_hash ~ '^[0-9a-f]{64}$' and "
            "narration_timing_recovery_receipt_hash ~ '^[0-9a-f]{64}$' and "
            "rejected_final_media_hash ~ '^[0-9a-f]{64}$' and "
            "rejected_final_review_candidate_hash ~ '^[0-9a-f]{64}$' and "
            "production_visual_policy_hash ~ '^[0-9a-f]{64}$' and "
            "budget_authority_hash ~ '^[0-9a-f]{64}$' and "
            "authority_hash ~ '^[0-9a-f]{64}$' and audio_duration_ms > 0",
            name="ck_ai_visual_rerender_authority_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["source_workflow_run_id"], ["production_workflow_runs.id"]
        ),
        sa.ForeignKeyConstraint(
            ["replacement_workflow_run_id"], ["production_workflow_runs.id"]
        ),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(
            ["production_package_artifact_version_id"], ["artifact_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["production_readiness_receipt_artifact_version_id"],
            ["artifact_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["script_artifact_version_id"], ["artifact_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["timed_words_artifact_version_id"], ["artifact_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["caption_artifact_version_id"], ["artifact_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["subtitle_qc_artifact_version_id"], ["artifact_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["rejected_final_media_ref_id"], ["final_media_refs.id"]
        ),
        sa.ForeignKeyConstraint(
            ["rejected_final_review_candidate_id"], ["final_review_candidates.id"]
        ),
        sa.ForeignKeyConstraint(
            ["budget_reservation_id"], ["mr1_monthly_budget_reservations.id"]
        ),
        sa.ForeignKeyConstraint(
            ["narration_timing_recovery_authority_id"],
            ["v2_narration_timing_recovery_authorities.id"],
        ),
        sa.ForeignKeyConstraint(
            ["narration_timing_recovery_receipt_id"],
            ["v2_narration_timing_recovery_receipts.id"],
        ),
    )
    op.create_index(
        "ix_ai_visual_rerender_authority_project",
        "ai_visual_rerender_authorities",
        ["video_project_id"],
    )


def _create_visual_production_runs() -> None:
    op.create_table(
        "ai_visual_production_runs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workflow_run_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=False),
        sa.Column("rerender_authority_id", UUID),
        sa.Column("execution_kind", sa.String(40), nullable=False),
        sa.Column("production_package_artifact_version_id", UUID, nullable=False),
        sa.Column("production_package_hash", sa.String(64), nullable=False),
        sa.Column("production_visual_policy_version", sa.String(120), nullable=False),
        sa.Column("production_visual_policy_ref", sa.Text(), nullable=False),
        sa.Column("production_visual_policy_hash", sa.String(64), nullable=False),
        sa.Column("source_timeline_ref", sa.Text(), nullable=False),
        sa.Column("source_timeline_hash", sa.String(64), nullable=False),
        sa.Column("audio_ref", sa.Text(), nullable=False),
        sa.Column("audio_checksum", sa.String(64), nullable=False),
        sa.Column("audio_duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("timed_words_ref", sa.Text(), nullable=False),
        sa.Column("timed_words_hash", sa.String(64), nullable=False),
        sa.Column("caption_ref", sa.Text(), nullable=False),
        sa.Column("caption_hash", sa.String(64), nullable=False),
        sa.Column("caption_checksum", sa.String(64), nullable=False),
        sa.Column("subtitle_qc_ref", sa.Text(), nullable=False),
        sa.Column("subtitle_qc_hash", sa.String(64), nullable=False),
        sa.Column("budget_reservation_id", UUID, nullable=False),
        sa.Column("budget_reservation_ref", sa.Text(), nullable=False),
        sa.Column("budget_authority_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("current_phase", sa.String(40), nullable=False),
        sa.Column("style_bible_id", UUID),
        sa.Column("style_bible_hash", sa.String(64)),
        sa.Column("scene_plan_id", UUID),
        sa.Column("scene_plan_hash", sa.String(64)),
        sa.Column("asset_manifest_id", UUID),
        sa.Column("asset_manifest_hash", sa.String(64)),
        sa.Column("motion_grammar_ref", sa.Text()),
        sa.Column("motion_grammar_hash", sa.String(64)),
        sa.Column("effect_plan_ref", sa.Text()),
        sa.Column("effect_plan_hash", sa.String(64)),
        sa.Column("render_output_ref", sa.Text()),
        sa.Column("render_output_checksum", sa.String(64)),
        sa.Column("technical_qc_ref", sa.Text()),
        sa.Column("technical_qc_hash", sa.String(64)),
        sa.Column("creative_qc_ref", sa.Text()),
        sa.Column("creative_qc_hash", sa.String(64)),
        sa.Column("cross_modal_qc_ref", sa.Text()),
        sa.Column("cross_modal_qc_hash", sa.String(64)),
        sa.Column("archive_receipt_ref", sa.Text()),
        sa.Column("archive_receipt_hash", sa.String(64)),
        sa.Column("final_media_ref_id", UUID),
        sa.Column("final_review_candidate_id", UUID),
        sa.Column("failure_reason_code", sa.String(160)),
        sa.Column("projection_version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rerender_authority_id"),
        sa.UniqueConstraint(
            "workflow_run_id",
            "execution_kind",
            name="uq_ai_visual_production_run_workflow_kind",
        ),
        sa.CheckConstraint(
            "execution_kind in ('NORMAL_PRODUCTION','GOVERNED_RERENDER') and "
            "((execution_kind='NORMAL_PRODUCTION' and rerender_authority_id is null) or "
            "(execution_kind='GOVERNED_RERENDER' and rerender_authority_id is not null))",
            name="ck_ai_visual_production_run_kind",
        ),
        sa.CheckConstraint(
            "state in ('AUTHORIZED','PLANNED','GENERATING','ASSETS_VERIFIED',"
            "'RENDERING','RENDERED','QC_RUNNING','QC_VERIFIED','ARCHIVING',"
            "'ARCHIVED','FINAL_REVIEW_READY','BLOCKED')",
            name="ck_ai_visual_production_run_state",
        ),
        sa.CheckConstraint(
            "current_phase in ('AUTHORIZE','PLAN','GENERATE','MANIFEST','RENDER',"
            "'QC','ARCHIVE','FINALIZE') and projection_version > 0",
            name="ck_ai_visual_production_run_phase",
        ),
        sa.CheckConstraint(
            "production_visual_policy_version = "
            "'vcos.production-visual-policy.ai-only.v1' and "
            "production_package_hash ~ '^[0-9a-f]{64}$' and "
            "production_visual_policy_hash ~ '^[0-9a-f]{64}$' and "
            "source_timeline_hash ~ '^[0-9a-f]{64}$' and "
            "audio_checksum ~ '^[0-9a-f]{64}$' and "
            "timed_words_hash ~ '^[0-9a-f]{64}$' and "
            "caption_hash ~ '^[0-9a-f]{64}$' and "
            "caption_checksum ~ '^[0-9a-f]{64}$' and "
            "subtitle_qc_hash ~ '^[0-9a-f]{64}$' and "
            "budget_authority_hash ~ '^[0-9a-f]{64}$' and audio_duration_ms > 0",
            name="ck_ai_visual_production_run_hashes",
        ),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["production_workflow_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(
            ["rerender_authority_id"], ["ai_visual_rerender_authorities.id"]
        ),
        sa.ForeignKeyConstraint(
            ["production_package_artifact_version_id"], ["artifact_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["budget_reservation_id"], ["mr1_monthly_budget_reservations.id"]
        ),
        sa.ForeignKeyConstraint(["final_media_ref_id"], ["final_media_refs.id"]),
        sa.ForeignKeyConstraint(
            ["final_review_candidate_id"], ["final_review_candidates.id"]
        ),
    )
    op.create_index(
        "ix_ai_visual_production_run_project",
        "ai_visual_production_runs",
        ["video_project_id"],
    )
    op.create_index(
        "ix_ai_visual_production_run_state",
        "ai_visual_production_runs",
        ["state", "updated_at"],
    )


def _create_style_and_scene_authorities() -> None:
    op.create_table(
        "ai_visual_style_bibles",
        sa.Column("id", UUID, nullable=False),
        sa.Column("visual_production_run_id", UUID, nullable=False),
        sa.Column("schema_version", sa.String(120), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("visual_production_run_id"),
        sa.UniqueConstraint("content_hash"),
        sa.CheckConstraint(
            "schema_version='vcos.video-visual-style-bible.v1' and "
            "content_hash ~ '^[0-9a-f]{64}$' and jsonb_typeof(content)='object'",
            name="ck_ai_visual_style_bible_identity",
        ),
        sa.ForeignKeyConstraint(
            ["visual_production_run_id"], ["ai_visual_production_runs.id"]
        ),
    )
    op.create_table(
        "ai_visual_scene_plan_snapshots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("visual_production_run_id", UUID, nullable=False),
        sa.Column("style_bible_id", UUID, nullable=False),
        sa.Column("style_bible_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(120), nullable=False),
        sa.Column("scene_count", sa.Integer(), nullable=False),
        sa.Column("ai_image_scene_count", sa.Integer(), nullable=False),
        sa.Column("ai_video_scene_count", sa.Integer(), nullable=False),
        sa.Column("unique_asset_slot_count", sa.Integer(), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("visual_production_run_id"),
        sa.UniqueConstraint("content_hash"),
        sa.CheckConstraint(
            "schema_version='vcos.ai-visual-scene-plan-set.v1' and "
            "scene_count > 0 and ai_image_scene_count >= 0 and "
            "ai_video_scene_count >= 0 and unique_asset_slot_count > 0 and "
            "unique_asset_slot_count <= scene_count and "
            "ai_image_scene_count + ai_video_scene_count = scene_count and "
            "style_bible_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$' and jsonb_typeof(content)='object'",
            name="ck_ai_visual_scene_plan_identity",
        ),
        sa.ForeignKeyConstraint(
            ["visual_production_run_id"], ["ai_visual_production_runs.id"]
        ),
        sa.ForeignKeyConstraint(["style_bible_id"], ["ai_visual_style_bibles.id"]),
    )


def _create_asset_effects() -> None:
    op.create_table(
        "ai_visual_asset_effects",
        sa.Column("id", UUID, nullable=False),
        sa.Column("visual_production_run_id", UUID, nullable=False),
        sa.Column("scene_plan_snapshot_id", UUID, nullable=False),
        sa.Column("workflow_run_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=False),
        sa.Column("asset_slot_id", sa.String(120), nullable=False),
        sa.Column("scene_id", sa.String(120), nullable=False),
        sa.Column("bound_scene_ids", JSONB, nullable=False),
        sa.Column("bound_scene_plan_hashes", JSONB, nullable=False),
        sa.Column("bound_scene_count", sa.Integer(), nullable=False),
        sa.Column("primary_asset_owner_scene_id", sa.String(120), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("route", sa.String(40), nullable=False),
        sa.Column("asset_acquisition_mode", sa.String(40), nullable=False),
        sa.Column("provider_key", sa.String(120), nullable=False),
        sa.Column("model_id", sa.String(160), nullable=False),
        sa.Column("provider_config_version", sa.String(120), nullable=False),
        sa.Column("provider_config_hash", sa.String(64), nullable=False),
        sa.Column("price_catalog_version", sa.String(80), nullable=False),
        sa.Column("price_catalog_ref", sa.Text(), nullable=False),
        sa.Column("price_catalog_hash", sa.String(64), nullable=False),
        sa.Column("production_visual_policy_version", sa.String(120), nullable=False),
        sa.Column("production_visual_policy_hash", sa.String(64), nullable=False),
        sa.Column("style_bible_ref", sa.Text(), nullable=False),
        sa.Column("style_bible_hash", sa.String(64), nullable=False),
        sa.Column("scene_plan_ref", sa.Text(), nullable=False),
        sa.Column("scene_plan_hash", sa.String(64), nullable=False),
        sa.Column("compiled_prompt_ref", sa.Text(), nullable=False),
        sa.Column("compiled_prompt_hash", sa.String(64), nullable=False),
        sa.Column("compiled_prompt_content_hash", sa.String(64), nullable=False),
        sa.Column("prompt_compiler_version", sa.String(120), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("generation_policy", JSONB, nullable=False),
        sa.Column("generation_policy_hash", sa.String(64), nullable=False),
        sa.Column("effect_identity_hash", sa.String(64), nullable=False),
        sa.Column("reuse_authority_ref", sa.Text()),
        sa.Column("reuse_authority_hash", sa.String(64)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("approval_ref", sa.Text(), nullable=False),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("budget_reservation_id", UUID, nullable=False),
        sa.Column("budget_authority_ref", sa.Text(), nullable=False),
        sa.Column("budget_authority_hash", sa.String(64), nullable=False),
        sa.Column("cost_estimate_ref", sa.Text(), nullable=False),
        sa.Column("cost_estimate_hash", sa.String(64), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("maximum_cost_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(18, 6)),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("maximum_attempts", sa.Integer(), nullable=False),
        sa.Column("provider_call_count", sa.Integer(), nullable=False),
        sa.Column("submission_owner_token_hash", sa.String(64)),
        sa.Column("submission_lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("provider_operation_id", sa.Text()),
        sa.Column("provider_request_id", sa.Text()),
        sa.Column("provider_response_id", sa.Text()),
        sa.Column("request_journal_ref", sa.Text()),
        sa.Column("request_journal_hash", sa.String(64)),
        sa.Column("response_journal_ref", sa.Text()),
        sa.Column("response_journal_hash", sa.String(64)),
        sa.Column("sanitized_response_hash", sa.String(64)),
        sa.Column("output_ref", sa.Text()),
        sa.Column("output_checksum", sa.String(64)),
        sa.Column("output_size_bytes", sa.BigInteger()),
        sa.Column("output_content_type", sa.String(120)),
        sa.Column("output_width", sa.Integer()),
        sa.Column("output_height", sa.Integer()),
        sa.Column("output_duration_ms", sa.BigInteger()),
        sa.Column("output_fps", sa.Numeric(12, 6)),
        sa.Column("output_audio_stream_count", sa.Integer()),
        sa.Column("normalization_ref", sa.Text()),
        sa.Column("normalization_hash", sa.String(64)),
        sa.Column("qc_evidence", JSONB, nullable=False),
        sa.Column("qc_ref", sa.Text()),
        sa.Column("qc_hash", sa.String(64)),
        sa.Column("cost_settlement_basis", sa.String(80)),
        sa.Column("retry_allowed", sa.Boolean(), nullable=False),
        sa.Column("fallback_allowed", sa.Boolean(), nullable=False),
        sa.Column("failure_reason_code", sa.String(160)),
        sa.Column("failure_evidence_hash", sa.String(64)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("response_captured_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "visual_production_run_id",
            "asset_slot_id",
            name="uq_ai_visual_asset_effect_run_slot",
        ),
        sa.UniqueConstraint(
            "visual_production_run_id",
            "ordinal",
            name="uq_ai_visual_asset_effect_run_ordinal",
        ),
        sa.UniqueConstraint("effect_identity_hash"),
        sa.UniqueConstraint("request_hash"),
        sa.UniqueConstraint("idempotency_key"),
        sa.CheckConstraint(
            "route in ('AI_IMAGE','AI_VIDEO') and "
            "asset_acquisition_mode in ('GENERATED','ARCHIVED_AI_REUSE') and "
            "((route='AI_IMAGE' and provider_key='google_gemini_image') or "
            "(route='AI_VIDEO' and provider_key='google_veo'))",
            name="ck_ai_visual_asset_effect_route_provider",
        ),
        sa.CheckConstraint(
            "state in ('PREPARED','SUBMITTING','OPERATION_RECORDED','POLLING',"
            "'RESPONSE_CAPTURED','DOWNLOADED','NORMALIZED','VERIFIED',"
            "'FAILED_DEFINITIVE','FAILED_UNCERTAIN','BLOCKED')",
            name="ck_ai_visual_asset_effect_state",
        ),
        sa.CheckConstraint(
            "maximum_attempts = 1 and provider_call_count between 0 and 1 and "
            "revision > 0 and ordinal > 0 and bound_scene_count > 0 and "
            "jsonb_typeof(bound_scene_ids)='array' and "
            "jsonb_array_length(bound_scene_ids)=bound_scene_count and "
            "jsonb_typeof(bound_scene_plan_hashes)='array' and "
            "jsonb_array_length(bound_scene_plan_hashes)=bound_scene_count and "
            "estimated_cost_usd > 0 and maximum_cost_usd >= estimated_cost_usd and "
            "(actual_cost_usd is null or actual_cost_usd >= 0) and "
            "retry_allowed=false and fallback_allowed=false",
            name="ck_ai_visual_asset_effect_bounds",
        ),
        sa.CheckConstraint(
            "production_visual_policy_version="
            "'vcos.production-visual-policy.ai-only.v1' and "
            "provider_config_hash ~ '^[0-9a-f]{64}$' and "
            "price_catalog_hash ~ '^[0-9a-f]{64}$' and "
            "production_visual_policy_hash ~ '^[0-9a-f]{64}$' and "
            "style_bible_hash ~ '^[0-9a-f]{64}$' and "
            "scene_plan_hash ~ '^[0-9a-f]{64}$' and "
            "compiled_prompt_hash ~ '^[0-9a-f]{64}$' and "
            "compiled_prompt_content_hash ~ '^[0-9a-f]{64}$' and "
            "prompt_hash ~ '^[0-9a-f]{64}$' and "
            "generation_policy_hash ~ '^[0-9a-f]{64}$' and "
            "effect_identity_hash ~ '^[0-9a-f]{64}$' and "
            "request_hash ~ '^[0-9a-f]{64}$' and "
            "approval_hash ~ '^[0-9a-f]{64}$' and "
            "budget_authority_hash ~ '^[0-9a-f]{64}$' and "
            "cost_estimate_hash ~ '^[0-9a-f]{64}$' and "
            "jsonb_typeof(generation_policy)='object'",
            name="ck_ai_visual_asset_effect_hashes",
        ),
        sa.CheckConstraint(
            "(state <> 'VERIFIED') or ("
            "((asset_acquisition_mode='GENERATED' and provider_call_count=1 and "
            "request_journal_ref is not null and request_journal_hash is not null and "
            "response_journal_ref is not null and response_journal_hash is not null and "
            "reuse_authority_ref is null and reuse_authority_hash is null) or "
            "(asset_acquisition_mode='ARCHIVED_AI_REUSE' and provider_call_count=0 and "
            "request_journal_ref is null and response_journal_ref is null and "
            "reuse_authority_ref is not null and reuse_authority_hash is not null)) and "
            "output_ref is not null and output_checksum is not null and "
            "output_size_bytes > 0 and output_width > 0 and output_height > 0 and "
            "qc_ref is not null and qc_hash is not null and completed_at is not null and "
            "cost_settlement_basis is not null and "
            "((route='AI_IMAGE' and output_duration_ms is null) or "
            "(route='AI_VIDEO' and output_duration_ms > 0 and "
            "normalization_ref is not null and normalization_hash is not null and "
            "output_audio_stream_count=0)))",
            name="ck_ai_visual_asset_effect_verified_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["visual_production_run_id"], ["ai_visual_production_runs.id"]
        ),
        sa.ForeignKeyConstraint(
            ["scene_plan_snapshot_id"], ["ai_visual_scene_plan_snapshots.id"]
        ),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["production_workflow_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(
            ["budget_reservation_id"], ["mr1_monthly_budget_reservations.id"]
        ),
    )
    op.create_index(
        "ix_ai_visual_asset_effect_project",
        "ai_visual_asset_effects",
        ["video_project_id"],
    )
    op.create_index(
        "ix_ai_visual_asset_effect_state",
        "ai_visual_asset_effects",
        ["state", "updated_at"],
    )


def _create_asset_manifests() -> None:
    op.create_table(
        "ai_visual_asset_manifests",
        sa.Column("id", UUID, nullable=False),
        sa.Column("visual_production_run_id", UUID, nullable=False),
        sa.Column("scene_plan_snapshot_id", UUID, nullable=False),
        sa.Column("scene_plan_hash", sa.String(64), nullable=False),
        sa.Column("style_bible_hash", sa.String(64), nullable=False),
        sa.Column("motion_grammar_hash", sa.String(64), nullable=False),
        sa.Column("effect_plan_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(120), nullable=False),
        sa.Column("scene_count", sa.Integer(), nullable=False),
        sa.Column("ai_image_scene_count", sa.Integer(), nullable=False),
        sa.Column("ai_video_scene_count", sa.Integer(), nullable=False),
        sa.Column("asset_count", sa.Integer(), nullable=False),
        sa.Column("ai_image_asset_count", sa.Integer(), nullable=False),
        sa.Column("ai_video_asset_count", sa.Integer(), nullable=False),
        sa.Column("total_provider_call_count", sa.Integer(), nullable=False),
        sa.Column("total_estimated_cost_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column(
            "total_actual_or_conservative_cost_usd", sa.Numeric(18, 6), nullable=False
        ),
        sa.Column("production_eligible", sa.Boolean(), nullable=False),
        sa.Column("renderer_primary_visual_generation", sa.Boolean(), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("visual_production_run_id"),
        sa.UniqueConstraint("scene_plan_snapshot_id"),
        sa.UniqueConstraint("content_hash"),
        sa.CheckConstraint(
            "schema_version='vcos.ai-visual-asset-manifest.v1' and scene_count>0 and "
            "ai_image_scene_count>=0 and ai_video_scene_count>=0 and "
            "ai_image_scene_count+ai_video_scene_count=scene_count and "
            "asset_count>0 and asset_count<=scene_count and "
            "ai_image_asset_count>=0 and ai_video_asset_count>=0 and "
            "ai_image_asset_count+ai_video_asset_count=asset_count and "
            "total_provider_call_count between 0 and asset_count and "
            "total_estimated_cost_usd > 0 and "
            "total_actual_or_conservative_cost_usd >= 0 and "
            "production_eligible=true and renderer_primary_visual_generation=false and "
            "scene_plan_hash ~ '^[0-9a-f]{64}$' and "
            "style_bible_hash ~ '^[0-9a-f]{64}$' and "
            "motion_grammar_hash ~ '^[0-9a-f]{64}$' and "
            "effect_plan_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$' and jsonb_typeof(content)='object'",
            name="ck_ai_visual_asset_manifest_identity",
        ),
        sa.ForeignKeyConstraint(
            ["visual_production_run_id"], ["ai_visual_production_runs.id"]
        ),
        sa.ForeignKeyConstraint(
            ["scene_plan_snapshot_id"], ["ai_visual_scene_plan_snapshots.id"]
        ),
    )


def _add_workflow_and_candidate_bindings() -> None:
    workflow_columns = (
        ("ai_visual_production_run_id", UUID),
        ("ai_visual_policy_ref", sa.Text()),
        ("ai_visual_policy_hash", sa.String(64)),
        ("ai_visual_style_bible_ref", sa.Text()),
        ("ai_visual_style_bible_hash", sa.String(64)),
        ("ai_visual_scene_plan_ref", sa.Text()),
        ("ai_visual_scene_plan_hash", sa.String(64)),
        ("ai_visual_asset_manifest_ref", sa.Text()),
        ("ai_visual_asset_manifest_hash", sa.String(64)),
        ("video_motion_grammar_ref", sa.Text()),
        ("video_motion_grammar_hash", sa.String(64)),
        ("ffmpeg_effect_plan_ref", sa.Text()),
        ("ffmpeg_effect_plan_hash", sa.String(64)),
    )
    for name, column_type in workflow_columns:
        op.add_column("production_workflow_runs", sa.Column(name, column_type))
    op.create_foreign_key(
        "fk_production_workflow_runs_ai_visual_production_run_id",
        "production_workflow_runs",
        "ai_visual_production_runs",
        ["ai_visual_production_run_id"],
        ["id"],
        use_alter=True,
        deferrable=True,
        initially="DEFERRED",
    )

    op.add_column(
        "final_review_candidates", sa.Column("ai_visual_production_run_id", UUID)
    )
    op.add_column(
        "final_review_candidates",
        sa.Column("ai_visual_asset_manifest_hash", sa.String(64)),
    )
    op.add_column(
        "final_review_candidates", sa.Column("ffmpeg_effect_plan_hash", sa.String(64))
    )
    op.add_column(
        "final_review_candidates",
        sa.Column("supersedes_final_review_candidate_id", UUID),
    )
    op.create_foreign_key(
        None,
        "final_review_candidates",
        "ai_visual_production_runs",
        ["ai_visual_production_run_id"],
        ["id"],
    )
    op.create_foreign_key(
        None,
        "final_review_candidates",
        "final_review_candidates",
        ["supersedes_final_review_candidate_id"],
        ["id"],
    )
    op.drop_constraint(
        "ck_final_review_candidates_hashes",
        "final_review_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_final_review_candidates_hashes",
        "final_review_candidates",
        "production_package_hash ~ '^[0-9a-f]{64}$' and "
        "production_readiness_receipt_hash ~ '^[0-9a-f]{64}$' and "
        "canonical_media_timeline_hash ~ '^[0-9a-f]{64}$' and "
        "((ai_visual_production_run_id is null and "
        "ai_visual_asset_manifest_hash is null and "
        "ffmpeg_effect_plan_hash is null and "
        "supersedes_final_review_candidate_id is null) or ("
        "ai_visual_production_run_id is not null and "
        "ai_visual_asset_manifest_hash ~ '^[0-9a-f]{64}$' and "
        "ffmpeg_effect_plan_hash ~ '^[0-9a-f]{64}$')) and "
        "native_render_plan_hash ~ '^[0-9a-f]{64}$' and "
        "render_output_checksum ~ '^[0-9a-f]{64}$' and "
        "technical_qc_receipt_hash ~ '^[0-9a-f]{64}$' and "
        "creative_qc_receipt_hash ~ '^[0-9a-f]{64}$' and "
        "archive_receipt_hash ~ '^[0-9a-f]{64}$' and "
        "final_media_hash ~ '^[0-9a-f]{64}$' and "
        "destination_binding_fingerprint ~ '^[0-9a-f]{64}$' and "
        "materiality_policy_hash ~ '^[0-9a-f]{64}$' and "
        "candidate_hash ~ '^[0-9a-f]{64}$'",
    )


def _create_replacement_lineages() -> None:
    op.create_table(
        "ai_visual_replacement_lineages",
        sa.Column("id", UUID, nullable=False),
        sa.Column("rerender_authority_id", UUID, nullable=False),
        sa.Column("visual_production_run_id", UUID, nullable=False),
        sa.Column("asset_manifest_id", UUID, nullable=False),
        sa.Column("asset_manifest_hash", sa.String(64), nullable=False),
        sa.Column("rejected_final_media_ref_id", UUID, nullable=False),
        sa.Column("replacement_final_media_ref_id", UUID, nullable=False),
        sa.Column("rejected_final_review_candidate_id", UUID, nullable=False),
        sa.Column("replacement_final_review_candidate_id", UUID, nullable=False),
        sa.Column("replacement_render_checksum", sa.String(64), nullable=False),
        sa.Column("replacement_archive_receipt_hash", sa.String(64), nullable=False),
        sa.Column("automatic_publish", sa.Boolean(), nullable=False),
        sa.Column("lineage_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rerender_authority_id"),
        sa.UniqueConstraint("visual_production_run_id"),
        sa.UniqueConstraint("asset_manifest_id"),
        sa.UniqueConstraint("rejected_final_media_ref_id"),
        sa.UniqueConstraint("replacement_final_media_ref_id"),
        sa.UniqueConstraint("rejected_final_review_candidate_id"),
        sa.UniqueConstraint("replacement_final_review_candidate_id"),
        sa.UniqueConstraint("lineage_hash"),
        sa.CheckConstraint(
            "rejected_final_media_ref_id <> replacement_final_media_ref_id and "
            "rejected_final_review_candidate_id <> replacement_final_review_candidate_id and "
            "automatic_publish=false and "
            "asset_manifest_hash ~ '^[0-9a-f]{64}$' and "
            "replacement_render_checksum ~ '^[0-9a-f]{64}$' and "
            "replacement_archive_receipt_hash ~ '^[0-9a-f]{64}$' and "
            "lineage_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ai_visual_replacement_lineage_identity",
        ),
        sa.ForeignKeyConstraint(
            ["rerender_authority_id"], ["ai_visual_rerender_authorities.id"]
        ),
        sa.ForeignKeyConstraint(
            ["visual_production_run_id"], ["ai_visual_production_runs.id"]
        ),
        sa.ForeignKeyConstraint(
            ["asset_manifest_id"], ["ai_visual_asset_manifests.id"]
        ),
        sa.ForeignKeyConstraint(
            ["rejected_final_media_ref_id"], ["final_media_refs.id"]
        ),
        sa.ForeignKeyConstraint(
            ["replacement_final_media_ref_id"], ["final_media_refs.id"]
        ),
        sa.ForeignKeyConstraint(
            ["rejected_final_review_candidate_id"], ["final_review_candidates.id"]
        ),
        sa.ForeignKeyConstraint(
            ["replacement_final_review_candidate_id"], ["final_review_candidates.id"]
        ),
    )


def _install_database_seals() -> None:
    op.execute(_IMMUTABILITY_SQL)
    op.execute(_RERENDER_AUTHORITY_SEAL_SQL)
    op.execute(_PRODUCTION_RUN_SEAL_SQL)
    op.execute(_STYLE_SCENE_SEAL_SQL)
    op.execute(_ASSET_EFFECT_SEAL_SQL)
    op.execute(_MANIFEST_SEAL_SQL)
    op.execute(_FINAL_CANDIDATE_SEAL_SQL)
    op.execute(_REPLACEMENT_LINEAGE_SEAL_SQL)


_IMMUTABILITY_SQL = r"""
CREATE OR REPLACE FUNCTION prevent_ai_visual_immutable_change()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'AI_VISUAL_IMMUTABLE_AUTHORITY';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ai_visual_rerender_authority_immutable
BEFORE UPDATE OR DELETE ON ai_visual_rerender_authorities
FOR EACH ROW EXECUTE FUNCTION prevent_ai_visual_immutable_change();
CREATE TRIGGER trg_ai_visual_style_bible_immutable
BEFORE UPDATE OR DELETE ON ai_visual_style_bibles
FOR EACH ROW EXECUTE FUNCTION prevent_ai_visual_immutable_change();
CREATE TRIGGER trg_ai_visual_scene_plan_immutable
BEFORE UPDATE OR DELETE ON ai_visual_scene_plan_snapshots
FOR EACH ROW EXECUTE FUNCTION prevent_ai_visual_immutable_change();
CREATE TRIGGER trg_ai_visual_asset_manifest_immutable
BEFORE UPDATE OR DELETE ON ai_visual_asset_manifests
FOR EACH ROW EXECUTE FUNCTION prevent_ai_visual_immutable_change();
CREATE TRIGGER trg_ai_visual_replacement_lineage_immutable
BEFORE UPDATE OR DELETE ON ai_visual_replacement_lineages
FOR EACH ROW EXECUTE FUNCTION prevent_ai_visual_immutable_change();
"""


_RERENDER_AUTHORITY_SEAL_SQL = r"""
CREATE OR REPLACE FUNCTION validate_ai_visual_rerender_authority_insert()
RETURNS trigger AS $$
DECLARE
  source_workflow production_workflow_runs%ROWTYPE;
  replacement_workflow production_workflow_runs%ROWTYPE;
  package_record RECORD;
  readiness_record RECORD;
  script_record RECORD;
  timed_words_record RECORD;
  caption_record RECORD;
  subtitle_record RECORD;
  timing v2_narration_timing_recovery_authorities%ROWTYPE;
  timing_receipt v2_narration_timing_recovery_receipts%ROWTYPE;
  old_media final_media_refs%ROWTYPE;
  old_candidate final_review_candidates%ROWTYPE;
  budget mr1_monthly_budget_reservations%ROWTYPE;
BEGIN
  SELECT * INTO source_workflow FROM production_workflow_runs
    WHERE id=NEW.source_workflow_run_id;
  SELECT * INTO replacement_workflow FROM production_workflow_runs
    WHERE id=NEW.replacement_workflow_run_id;
  SELECT av.content_hash, a.video_project_id, a.artifact_type
    INTO package_record FROM artifact_versions av JOIN artifacts a ON a.id=av.artifact_id
    WHERE av.id=NEW.production_package_artifact_version_id;
  SELECT av.content_hash, a.video_project_id, a.artifact_type
    INTO readiness_record FROM artifact_versions av JOIN artifacts a ON a.id=av.artifact_id
    WHERE av.id=NEW.production_readiness_receipt_artifact_version_id;
  SELECT av.content_hash, a.video_project_id, a.artifact_type
    INTO script_record FROM artifact_versions av JOIN artifacts a ON a.id=av.artifact_id
    WHERE av.id=NEW.script_artifact_version_id;
  SELECT av.content_hash, a.video_project_id, a.artifact_type
    INTO timed_words_record FROM artifact_versions av JOIN artifacts a ON a.id=av.artifact_id
    WHERE av.id=NEW.timed_words_artifact_version_id;
  SELECT av.content_hash, a.video_project_id, a.artifact_type
    INTO caption_record FROM artifact_versions av JOIN artifacts a ON a.id=av.artifact_id
    WHERE av.id=NEW.caption_artifact_version_id;
  SELECT av.content_hash, a.video_project_id, a.artifact_type
    INTO subtitle_record FROM artifact_versions av JOIN artifacts a ON a.id=av.artifact_id
    WHERE av.id=NEW.subtitle_qc_artifact_version_id;
  SELECT * INTO timing FROM v2_narration_timing_recovery_authorities
    WHERE id=NEW.narration_timing_recovery_authority_id;
  SELECT * INTO timing_receipt FROM v2_narration_timing_recovery_receipts
    WHERE id=NEW.narration_timing_recovery_receipt_id;
  SELECT * INTO old_media FROM final_media_refs WHERE id=NEW.rejected_final_media_ref_id;
  SELECT * INTO old_candidate FROM final_review_candidates
    WHERE id=NEW.rejected_final_review_candidate_id;
  SELECT * INTO budget FROM mr1_monthly_budget_reservations
    WHERE id=NEW.budget_reservation_id;

  IF source_workflow.id IS NULL
     OR source_workflow.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR source_workflow.production_package_artifact_version_id IS DISTINCT FROM NEW.production_package_artifact_version_id
     OR source_workflow.production_package_hash IS DISTINCT FROM NEW.production_package_hash
     OR source_workflow.production_readiness_receipt_artifact_version_id IS DISTINCT FROM NEW.production_readiness_receipt_artifact_version_id
     OR source_workflow.production_readiness_receipt_hash IS DISTINCT FROM NEW.production_readiness_receipt_hash
     OR package_record.content_hash IS DISTINCT FROM NEW.production_package_hash
     OR package_record.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR package_record.artifact_type IS DISTINCT FROM 'production_package'
     OR readiness_record.content_hash IS DISTINCT FROM NEW.production_readiness_receipt_hash
     OR readiness_record.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR readiness_record.artifact_type IS DISTINCT FROM 'production_readiness_receipt'
     OR script_record.content_hash IS DISTINCT FROM NEW.script_content_hash
     OR script_record.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR timed_words_record.content_hash IS DISTINCT FROM NEW.timed_words_hash
     OR timed_words_record.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR caption_record.content_hash IS DISTINCT FROM NEW.caption_hash
     OR caption_record.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR subtitle_record.content_hash IS DISTINCT FROM NEW.subtitle_qc_hash
     OR subtitle_record.video_project_id IS DISTINCT FROM NEW.video_project_id
  THEN
    RAISE EXCEPTION 'AI_VISUAL_RERENDER_SOURCE_AUTHORITY_MISMATCH';
  END IF;

  IF replacement_workflow.id IS NULL
     OR replacement_workflow.id IS NOT DISTINCT FROM source_workflow.id
     OR replacement_workflow.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR replacement_workflow.company_id IS DISTINCT FROM source_workflow.company_id
     OR replacement_workflow.channel_workspace_id IS DISTINCT FROM source_workflow.channel_workspace_id
     OR replacement_workflow.production_package_artifact_version_id IS DISTINCT FROM NEW.production_package_artifact_version_id
     OR replacement_workflow.production_package_hash IS DISTINCT FROM NEW.production_package_hash
     OR replacement_workflow.production_readiness_receipt_artifact_version_id IS DISTINCT FROM NEW.production_readiness_receipt_artifact_version_id
     OR replacement_workflow.production_readiness_receipt_hash IS DISTINCT FROM NEW.production_readiness_receipt_hash
     OR replacement_workflow.state IS DISTINCT FROM 'VISUAL_PENDING'
     OR replacement_workflow.current_stage IS DISTINCT FROM 'VISUAL'
     OR replacement_workflow.ai_visual_production_run_id IS NOT NULL
     OR replacement_workflow.final_media_ref_id IS NOT NULL
     OR replacement_workflow.final_review_candidate_id IS NOT NULL
     OR replacement_workflow.completed_at IS NOT NULL
  THEN
    RAISE EXCEPTION 'AI_VISUAL_RERENDER_REPLACEMENT_WORKFLOW_MISMATCH';
  END IF;

  IF timing.id IS NULL
     OR timing.workflow_run_id IS DISTINCT FROM NEW.source_workflow_run_id
     OR timing.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR timing.production_package_artifact_version_id IS DISTINCT FROM NEW.production_package_artifact_version_id
     OR timing.production_package_hash IS DISTINCT FROM NEW.production_package_hash
     OR timing.script_artifact_version_id IS DISTINCT FROM NEW.script_artifact_version_id
     OR timing.script_content_hash IS DISTINCT FROM NEW.script_content_hash
     OR timing.audio_relative_path IS DISTINCT FROM NEW.audio_ref
     OR timing.audio_checksum_sha256 IS DISTINCT FROM NEW.audio_checksum
     OR timing.audio_duration_ms IS DISTINCT FROM NEW.audio_duration_ms
     OR timing.max_tts_retries IS DISTINCT FROM 0
     OR timing.max_forced_alignment_submissions IS DISTINCT FROM 1
     OR timing.authority_hash IS DISTINCT FROM NEW.narration_timing_recovery_authority_hash
     OR timing_receipt.id IS NULL
     OR timing_receipt.authority_id IS DISTINCT FROM timing.id
     OR timing_receipt.workflow_run_id IS DISTINCT FROM NEW.source_workflow_run_id
     OR timing_receipt.recovery_state IS DISTINCT FROM 'VERIFIED'
     OR timing_receipt.tts_retry_count IS DISTINCT FROM 0
     OR timing_receipt.receipt_hash IS DISTINCT FROM NEW.narration_timing_recovery_receipt_hash
  THEN
    RAISE EXCEPTION 'AI_VISUAL_RERENDER_NARRATION_REUSE_MISMATCH';
  END IF;

  IF old_media.id IS NULL
     OR old_media.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR old_media.production_package_artifact_version_id IS DISTINCT FROM NEW.production_package_artifact_version_id
     OR old_media.production_package_hash IS DISTINCT FROM NEW.production_package_hash
     OR old_media.checksum_sha256 IS DISTINCT FROM NEW.rejected_final_media_hash
     OR old_candidate.id IS NULL
     OR old_candidate.workflow_run_id IS DISTINCT FROM NEW.source_workflow_run_id
     OR old_candidate.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR old_candidate.final_media_ref_id IS DISTINCT FROM old_media.id
     OR old_candidate.final_media_hash IS DISTINCT FROM NEW.rejected_final_media_hash
     OR old_candidate.candidate_hash IS DISTINCT FROM NEW.rejected_final_review_candidate_hash
  THEN
    RAISE EXCEPTION 'AI_VISUAL_RERENDER_REJECTED_MEDIA_MISMATCH';
  END IF;

  IF budget.id IS NULL
     OR budget.run_id IS DISTINCT FROM NEW.authorized_visual_production_run_id
     OR budget.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR budget.company_id IS DISTINCT FROM replacement_workflow.company_id
     OR budget.channel_workspace_id IS DISTINCT FROM replacement_workflow.channel_workspace_id
     OR budget.reservation_ref IS DISTINCT FROM NEW.budget_reservation_ref
     OR budget.capacity_evidence_json->>'content_hash' IS DISTINCT FROM NEW.budget_authority_hash
     OR budget.status NOT IN ('RESERVED','SUBMITTED')
     OR budget.reserved_amount < NEW.maximum_total_cost_usd
     OR budget.id = timing.budget_reservation_id
     OR NEW.maximum_tts_submissions <> 0
     OR NEW.maximum_forced_alignment_submissions <> 0
     OR NEW.automatic_publish
     OR NEW.authorized_by_actor_type IS DISTINCT FROM 'SYSTEM_WORKER'
     OR NEW.authorized_by_actor_role IS DISTINCT FROM 'SYSTEM_WORKER'
     OR NEW.authorized_by_actor_id IS DISTINCT FROM '6d196d74-7938-5c85-bc10-f25466616258'::uuid
  THEN
    RAISE EXCEPTION 'AI_VISUAL_RERENDER_FRESH_BUDGET_OR_SCOPE_MISMATCH';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ai_visual_rerender_authority_insert_seal
BEFORE INSERT ON ai_visual_rerender_authorities
FOR EACH ROW EXECUTE FUNCTION validate_ai_visual_rerender_authority_insert();
"""


_PRODUCTION_RUN_SEAL_SQL = r"""
CREATE OR REPLACE FUNCTION validate_ai_visual_production_run_insert()
RETURNS trigger AS $$
DECLARE
  workflow production_workflow_runs%ROWTYPE;
  budget mr1_monthly_budget_reservations%ROWTYPE;
  authority ai_visual_rerender_authorities%ROWTYPE;
  timing_receipt v2_narration_timing_recovery_receipts%ROWTYPE;
BEGIN
  SELECT * INTO workflow FROM production_workflow_runs WHERE id=NEW.workflow_run_id;
  SELECT * INTO budget FROM mr1_monthly_budget_reservations WHERE id=NEW.budget_reservation_id;
  IF workflow.id IS NULL
     OR workflow.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR workflow.production_package_artifact_version_id IS DISTINCT FROM NEW.production_package_artifact_version_id
     OR workflow.production_package_hash IS DISTINCT FROM NEW.production_package_hash
     OR budget.id IS NULL
     OR budget.run_id IS DISTINCT FROM NEW.id
     OR budget.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR budget.company_id IS DISTINCT FROM workflow.company_id
     OR budget.channel_workspace_id IS DISTINCT FROM workflow.channel_workspace_id
     OR budget.reservation_ref IS DISTINCT FROM NEW.budget_reservation_ref
     OR budget.capacity_evidence_json->>'content_hash' IS DISTINCT FROM NEW.budget_authority_hash
     OR budget.status NOT IN ('RESERVED','SUBMITTED')
     OR NEW.state <> 'AUTHORIZED'
     OR NEW.current_phase <> 'AUTHORIZE'
     OR NEW.projection_version <> 1
  THEN
    RAISE EXCEPTION 'AI_VISUAL_PRODUCTION_RUN_AUTHORITY_MISMATCH';
  END IF;

  IF NEW.execution_kind='GOVERNED_RERENDER' THEN
    SELECT * INTO authority FROM ai_visual_rerender_authorities
      WHERE id=NEW.rerender_authority_id;
    SELECT * INTO timing_receipt FROM v2_narration_timing_recovery_receipts
      WHERE id=authority.narration_timing_recovery_receipt_id;
    IF authority.id IS NULL
       OR authority.authorized_visual_production_run_id IS DISTINCT FROM NEW.id
       OR authority.replacement_workflow_run_id IS DISTINCT FROM NEW.workflow_run_id
       OR authority.video_project_id IS DISTINCT FROM NEW.video_project_id
       OR authority.production_package_artifact_version_id IS DISTINCT FROM NEW.production_package_artifact_version_id
       OR authority.production_package_hash IS DISTINCT FROM NEW.production_package_hash
       OR authority.production_visual_policy_version IS DISTINCT FROM NEW.production_visual_policy_version
       OR authority.production_visual_policy_ref IS DISTINCT FROM NEW.production_visual_policy_ref
       OR authority.production_visual_policy_hash IS DISTINCT FROM NEW.production_visual_policy_hash
       OR authority.audio_ref IS DISTINCT FROM NEW.audio_ref
       OR authority.audio_checksum IS DISTINCT FROM NEW.audio_checksum
       OR authority.audio_duration_ms IS DISTINCT FROM NEW.audio_duration_ms
       OR authority.timed_words_hash IS DISTINCT FROM NEW.timed_words_hash
       OR authority.caption_hash IS DISTINCT FROM NEW.caption_hash
       OR authority.caption_checksum IS DISTINCT FROM NEW.caption_checksum
       OR authority.subtitle_qc_hash IS DISTINCT FROM NEW.subtitle_qc_hash
       OR authority.budget_reservation_id IS DISTINCT FROM NEW.budget_reservation_id
       OR authority.budget_reservation_ref IS DISTINCT FROM NEW.budget_reservation_ref
       OR authority.budget_authority_hash IS DISTINCT FROM NEW.budget_authority_hash
       OR timing_receipt.canonical_media_timeline_hash IS DISTINCT FROM NEW.source_timeline_hash
    THEN
      RAISE EXCEPTION 'AI_VISUAL_GOVERNED_RERENDER_SPLICE_DETECTED';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ai_visual_production_run_insert_seal
BEFORE INSERT ON ai_visual_production_runs
FOR EACH ROW EXECUTE FUNCTION validate_ai_visual_production_run_insert();

CREATE OR REPLACE FUNCTION validate_ai_visual_production_run_update()
RETURNS trigger AS $$
BEGIN
  IF ROW(
       NEW.id, NEW.workflow_run_id, NEW.video_project_id, NEW.rerender_authority_id,
       NEW.execution_kind, NEW.production_package_artifact_version_id,
       NEW.production_package_hash, NEW.production_visual_policy_version,
       NEW.production_visual_policy_ref, NEW.production_visual_policy_hash,
       NEW.source_timeline_ref, NEW.source_timeline_hash, NEW.audio_ref,
       NEW.audio_checksum, NEW.audio_duration_ms, NEW.timed_words_ref,
       NEW.timed_words_hash, NEW.caption_ref, NEW.caption_hash,
       NEW.caption_checksum, NEW.subtitle_qc_ref, NEW.subtitle_qc_hash,
       NEW.budget_reservation_id, NEW.budget_reservation_ref,
       NEW.budget_authority_hash, NEW.started_at, NEW.created_at
     ) IS DISTINCT FROM ROW(
       OLD.id, OLD.workflow_run_id, OLD.video_project_id, OLD.rerender_authority_id,
       OLD.execution_kind, OLD.production_package_artifact_version_id,
       OLD.production_package_hash, OLD.production_visual_policy_version,
       OLD.production_visual_policy_ref, OLD.production_visual_policy_hash,
       OLD.source_timeline_ref, OLD.source_timeline_hash, OLD.audio_ref,
       OLD.audio_checksum, OLD.audio_duration_ms, OLD.timed_words_ref,
       OLD.timed_words_hash, OLD.caption_ref, OLD.caption_hash,
       OLD.caption_checksum, OLD.subtitle_qc_ref, OLD.subtitle_qc_hash,
       OLD.budget_reservation_id, OLD.budget_reservation_ref,
       OLD.budget_authority_hash, OLD.started_at, OLD.created_at
     )
     OR NEW.projection_version <> OLD.projection_version + 1
  THEN RAISE EXCEPTION 'AI_VISUAL_PRODUCTION_RUN_CAS_OR_IDENTITY_VIOLATION'; END IF;

  IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
       (OLD.state='AUTHORIZED' AND NEW.state IN ('PLANNED','BLOCKED'))
    OR (OLD.state='PLANNED' AND NEW.state IN ('GENERATING','BLOCKED'))
    OR (OLD.state='GENERATING' AND NEW.state IN ('ASSETS_VERIFIED','BLOCKED'))
    OR (OLD.state='ASSETS_VERIFIED' AND NEW.state IN ('RENDERING','BLOCKED'))
    OR (OLD.state='RENDERING' AND NEW.state IN ('RENDERED','BLOCKED'))
    OR (OLD.state='RENDERED' AND NEW.state IN ('QC_RUNNING','BLOCKED'))
    OR (OLD.state='QC_RUNNING' AND NEW.state IN ('QC_VERIFIED','BLOCKED'))
    OR (OLD.state='QC_VERIFIED' AND NEW.state IN ('ARCHIVING','BLOCKED'))
    OR (OLD.state='ARCHIVING' AND NEW.state IN ('ARCHIVED','BLOCKED'))
    OR (OLD.state='ARCHIVED' AND NEW.state IN ('FINAL_REVIEW_READY','BLOCKED'))
  )
  THEN RAISE EXCEPTION 'AI_VISUAL_PRODUCTION_RUN_STATE_TRANSITION_FORBIDDEN'; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ai_visual_production_run_update_cas
BEFORE UPDATE ON ai_visual_production_runs
FOR EACH ROW EXECUTE FUNCTION validate_ai_visual_production_run_update();
"""


_STYLE_SCENE_SEAL_SQL = r"""
CREATE OR REPLACE FUNCTION validate_ai_visual_style_insert()
RETURNS trigger AS $$
DECLARE visual_run ai_visual_production_runs%ROWTYPE;
BEGIN
  SELECT * INTO visual_run FROM ai_visual_production_runs
    WHERE id=NEW.visual_production_run_id;
  IF visual_run.id IS NULL
     OR NEW.content->>'content_hash' IS DISTINCT FROM NEW.content_hash
     OR NEW.content->>'video_project_id' IS DISTINCT FROM visual_run.video_project_id::text
     OR NEW.content->>'package_id' IS DISTINCT FROM visual_run.production_package_artifact_version_id::text
     OR NEW.content->>'aspect_ratio' IS DISTINCT FROM '16:9'
     OR NEW.content->>'visible_generated_text' IS DISTINCT FROM 'false'
     OR NEW.content->>'fake_product_ui_allowed' IS DISTINCT FROM 'false'
  THEN RAISE EXCEPTION 'AI_VISUAL_STYLE_BIBLE_SEAL_MISMATCH'; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION validate_ai_visual_scene_plan_insert()
RETURNS trigger AS $$
DECLARE style ai_visual_style_bibles%ROWTYPE;
DECLARE visual_run ai_visual_production_runs%ROWTYPE;
BEGIN
  SELECT * INTO style FROM ai_visual_style_bibles WHERE id=NEW.style_bible_id;
  SELECT * INTO visual_run FROM ai_visual_production_runs
    WHERE id=NEW.visual_production_run_id;
  IF style.id IS NULL OR visual_run.id IS NULL
     OR style.visual_production_run_id IS DISTINCT FROM visual_run.id
     OR style.content_hash IS DISTINCT FROM NEW.style_bible_hash
     OR NEW.content->>'content_hash' IS DISTINCT FROM NEW.content_hash
     OR NEW.content->>'style_bible_hash' IS DISTINCT FROM NEW.style_bible_hash
     OR jsonb_typeof(NEW.content->'scenes') IS DISTINCT FROM 'array'
     OR jsonb_array_length(NEW.content->'scenes') <> NEW.scene_count
     OR (NEW.content->>'ai_image_scene_count')::integer <> NEW.ai_image_scene_count
     OR (NEW.content->>'ai_video_scene_count')::integer <> NEW.ai_video_scene_count
     OR (NEW.content->>'unique_asset_slot_count')::integer <> NEW.unique_asset_slot_count
  THEN RAISE EXCEPTION 'AI_VISUAL_SCENE_PLAN_SEAL_MISMATCH'; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ai_visual_style_bible_insert_seal
BEFORE INSERT ON ai_visual_style_bibles
FOR EACH ROW EXECUTE FUNCTION validate_ai_visual_style_insert();
CREATE TRIGGER trg_ai_visual_scene_plan_insert_seal
BEFORE INSERT ON ai_visual_scene_plan_snapshots
FOR EACH ROW EXECUTE FUNCTION validate_ai_visual_scene_plan_insert();
"""


_ASSET_EFFECT_SEAL_SQL = r"""
CREATE OR REPLACE FUNCTION validate_ai_visual_effect_evidence(effect ai_visual_asset_effects)
RETURNS void AS $$
BEGIN
  IF jsonb_typeof(effect.qc_evidence) IS DISTINCT FROM 'object'
     OR effect.actual_cost_usd > effect.maximum_cost_usd
     OR effect.request_journal_hash IS NOT NULL
        AND effect.request_journal_hash !~ '^[0-9a-f]{64}$'
     OR effect.response_journal_hash IS NOT NULL
        AND effect.response_journal_hash !~ '^[0-9a-f]{64}$'
     OR effect.sanitized_response_hash IS NOT NULL
        AND effect.sanitized_response_hash !~ '^[0-9a-f]{64}$'
     OR effect.output_checksum IS NOT NULL
        AND effect.output_checksum !~ '^[0-9a-f]{64}$'
     OR effect.normalization_hash IS NOT NULL
        AND effect.normalization_hash !~ '^[0-9a-f]{64}$'
     OR effect.qc_hash IS NOT NULL AND effect.qc_hash !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_EVIDENCE_HASH_INVALID'; END IF;

  IF effect.asset_acquisition_mode='GENERATED'
     AND (effect.request_journal_ref IS NULL OR effect.request_journal_hash IS NULL)
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_REQUEST_JOURNAL_REQUIRED'; END IF;

  IF effect.state='PREPARED' AND (
       effect.provider_call_count<>0 OR effect.submitted_at IS NOT NULL
       OR effect.response_captured_at IS NOT NULL OR effect.completed_at IS NOT NULL
     )
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_PREPARED_EVIDENCE_INVALID';
  ELSIF effect.state='SUBMITTING' AND (
       effect.provider_call_count<>1 OR effect.submitted_at IS NULL
       OR effect.completed_at IS NOT NULL
     )
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_SUBMITTING_EVIDENCE_INVALID';
  ELSIF effect.state IN ('OPERATION_RECORDED','POLLING') AND (
       effect.provider_call_count<>1 OR effect.submitted_at IS NULL
       OR effect.provider_operation_id IS NULL OR effect.response_journal_ref IS NULL
       OR effect.response_journal_hash IS NULL OR effect.completed_at IS NOT NULL
     )
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_OPERATION_EVIDENCE_INVALID';
  ELSIF effect.state='RESPONSE_CAPTURED' AND (
       effect.provider_call_count<>1 OR effect.submitted_at IS NULL
       OR effect.response_captured_at IS NULL OR effect.response_journal_ref IS NULL
       OR effect.response_journal_hash IS NULL OR effect.sanitized_response_hash IS NULL
       OR effect.completed_at IS NOT NULL
     )
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_CAPTURE_EVIDENCE_INVALID';
  ELSIF effect.state='DOWNLOADED' AND (
       effect.provider_call_count<>1 OR effect.provider_operation_id IS NULL
       OR effect.response_captured_at IS NULL OR effect.completed_at IS NOT NULL
     )
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_DOWNLOAD_EVIDENCE_INVALID';
  ELSIF effect.state='NORMALIZED' AND (
       effect.provider_call_count<>1 OR effect.output_ref IS NULL
       OR effect.output_checksum IS NULL OR effect.output_size_bytes<=0
       OR effect.normalization_ref IS NULL OR effect.normalization_hash IS NULL
       OR effect.completed_at IS NOT NULL
     )
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_NORMALIZATION_EVIDENCE_INVALID';
  ELSIF effect.state='VERIFIED' AND (
       effect.completed_at IS NULL OR effect.qc_ref IS NULL OR effect.qc_hash IS NULL
       OR effect.output_ref IS NULL OR effect.output_checksum IS NULL
       OR effect.output_size_bytes<=0 OR effect.output_width<=0 OR effect.output_height<=0
       OR effect.cost_settlement_basis IS NULL
     )
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_VERIFIED_EVIDENCE_INVALID';
  ELSIF effect.state IN ('FAILED_DEFINITIVE','FAILED_UNCERTAIN','BLOCKED') AND (
       effect.provider_call_count<>1 OR effect.completed_at IS NULL
       OR effect.failure_reason_code IS NULL OR effect.cost_settlement_basis IS NULL
     )
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_FAILURE_EVIDENCE_INVALID';
  END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION validate_ai_visual_effect_insert()
RETURNS trigger AS $$
DECLARE visual_run ai_visual_production_runs%ROWTYPE;
DECLARE scene_plan ai_visual_scene_plan_snapshots%ROWTYPE;
DECLARE style ai_visual_style_bibles%ROWTYPE;
DECLARE budget mr1_monthly_budget_reservations%ROWTYPE;
DECLARE owner_scene jsonb;
BEGIN
  SELECT * INTO visual_run FROM ai_visual_production_runs
    WHERE id=NEW.visual_production_run_id;
  SELECT * INTO scene_plan FROM ai_visual_scene_plan_snapshots
    WHERE id=NEW.scene_plan_snapshot_id;
  SELECT * INTO style FROM ai_visual_style_bibles WHERE id=scene_plan.style_bible_id;
  SELECT * INTO budget FROM mr1_monthly_budget_reservations
    WHERE id=NEW.budget_reservation_id;
  SELECT value INTO owner_scene
    FROM jsonb_array_elements(scene_plan.content->'scenes')
    WHERE value->>'scene_id'=NEW.scene_id LIMIT 1;
  IF visual_run.id IS NULL OR scene_plan.id IS NULL OR style.id IS NULL
     OR scene_plan.visual_production_run_id IS DISTINCT FROM visual_run.id
     OR NEW.workflow_run_id IS DISTINCT FROM visual_run.workflow_run_id
     OR NEW.video_project_id IS DISTINCT FROM visual_run.video_project_id
     OR NEW.production_visual_policy_version IS DISTINCT FROM visual_run.production_visual_policy_version
     OR NEW.production_visual_policy_hash IS DISTINCT FROM visual_run.production_visual_policy_hash
     OR NEW.style_bible_hash IS DISTINCT FROM style.content_hash
     OR owner_scene IS NULL
     OR owner_scene->>'content_hash' IS DISTINCT FROM NEW.scene_plan_hash
     OR owner_scene->>'scene_id' IS DISTINCT FROM NEW.primary_asset_owner_scene_id
     OR owner_scene->>'primary_asset_slot_id' IS DISTINCT FROM NEW.asset_slot_id
     OR owner_scene->>'production_route' IS DISTINCT FROM NEW.route
     OR budget.id IS NULL OR budget.id IS DISTINCT FROM visual_run.budget_reservation_id
     OR budget.reservation_ref IS DISTINCT FROM NEW.budget_authority_ref
     OR budget.capacity_evidence_json->>'content_hash' IS DISTINCT FROM NEW.budget_authority_hash
     OR NEW.maximum_cost_usd > budget.reserved_amount
     OR (NEW.asset_acquisition_mode='GENERATED' AND NEW.state<>'PREPARED')
     OR (NEW.asset_acquisition_mode='ARCHIVED_AI_REUSE' AND NEW.state<>'VERIFIED')
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_AUTHORITY_SPLICE_DETECTED'; END IF;
  PERFORM validate_ai_visual_effect_evidence(NEW);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION validate_ai_visual_effect_update()
RETURNS trigger AS $$
BEGIN
  IF ROW(
       NEW.id, NEW.visual_production_run_id, NEW.scene_plan_snapshot_id,
       NEW.workflow_run_id, NEW.video_project_id, NEW.asset_slot_id, NEW.scene_id,
       NEW.bound_scene_ids, NEW.bound_scene_plan_hashes, NEW.bound_scene_count,
       NEW.primary_asset_owner_scene_id, NEW.ordinal, NEW.route,
       NEW.asset_acquisition_mode, NEW.provider_key, NEW.model_id,
       NEW.provider_config_version, NEW.provider_config_hash,
       NEW.price_catalog_version, NEW.price_catalog_ref, NEW.price_catalog_hash,
       NEW.production_visual_policy_version, NEW.production_visual_policy_hash,
       NEW.style_bible_ref, NEW.style_bible_hash, NEW.scene_plan_ref,
       NEW.scene_plan_hash, NEW.compiled_prompt_ref, NEW.compiled_prompt_hash,
       NEW.compiled_prompt_content_hash, NEW.prompt_compiler_version,
       NEW.prompt_hash, NEW.generation_policy, NEW.generation_policy_hash,
       NEW.effect_identity_hash, NEW.reuse_authority_ref, NEW.reuse_authority_hash,
       NEW.request_hash, NEW.idempotency_key, NEW.approval_ref, NEW.approval_hash,
       NEW.budget_reservation_id, NEW.budget_authority_ref,
       NEW.budget_authority_hash, NEW.cost_estimate_ref, NEW.cost_estimate_hash,
       NEW.estimated_cost_usd, NEW.maximum_cost_usd, NEW.maximum_attempts,
       NEW.retry_allowed, NEW.fallback_allowed, NEW.created_at
     ) IS DISTINCT FROM ROW(
       OLD.id, OLD.visual_production_run_id, OLD.scene_plan_snapshot_id,
       OLD.workflow_run_id, OLD.video_project_id, OLD.asset_slot_id, OLD.scene_id,
       OLD.bound_scene_ids, OLD.bound_scene_plan_hashes, OLD.bound_scene_count,
       OLD.primary_asset_owner_scene_id, OLD.ordinal, OLD.route,
       OLD.asset_acquisition_mode, OLD.provider_key, OLD.model_id,
       OLD.provider_config_version, OLD.provider_config_hash,
       OLD.price_catalog_version, OLD.price_catalog_ref, OLD.price_catalog_hash,
       OLD.production_visual_policy_version, OLD.production_visual_policy_hash,
       OLD.style_bible_ref, OLD.style_bible_hash, OLD.scene_plan_ref,
       OLD.scene_plan_hash, OLD.compiled_prompt_ref, OLD.compiled_prompt_hash,
       OLD.compiled_prompt_content_hash, OLD.prompt_compiler_version,
       OLD.prompt_hash, OLD.generation_policy, OLD.generation_policy_hash,
       OLD.effect_identity_hash, OLD.reuse_authority_ref, OLD.reuse_authority_hash,
       OLD.request_hash, OLD.idempotency_key, OLD.approval_ref, OLD.approval_hash,
       OLD.budget_reservation_id, OLD.budget_authority_ref,
       OLD.budget_authority_hash, OLD.cost_estimate_ref, OLD.cost_estimate_hash,
       OLD.estimated_cost_usd, OLD.maximum_cost_usd, OLD.maximum_attempts,
       OLD.retry_allowed, OLD.fallback_allowed, OLD.created_at
     )
     OR NEW.revision <> OLD.revision + 1
     OR NEW.provider_call_count < OLD.provider_call_count
     OR NEW.provider_call_count > OLD.provider_call_count + 1
     OR (OLD.provider_operation_id IS NOT NULL
         AND NEW.provider_operation_id IS DISTINCT FROM OLD.provider_operation_id)
     OR NEW.request_journal_ref IS DISTINCT FROM OLD.request_journal_ref
     OR NEW.request_journal_hash IS DISTINCT FROM OLD.request_journal_hash
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_CAS_OR_IDENTITY_VIOLATION'; END IF;

  IF NOT (
       (OLD.state='PREPARED' AND NEW.state='SUBMITTING'
        AND OLD.provider_call_count=0 AND NEW.provider_call_count=1)
    OR (OLD.state='SUBMITTING' AND NEW.state IN
        ('OPERATION_RECORDED','RESPONSE_CAPTURED','FAILED_DEFINITIVE','FAILED_UNCERTAIN'))
    OR (OLD.state='FAILED_UNCERTAIN' AND NEW.state='OPERATION_RECORDED'
        AND NEW.provider_call_count=1)
    OR (OLD.state='OPERATION_RECORDED' AND NEW.state IN
        ('OPERATION_RECORDED','POLLING','RESPONSE_CAPTURED','FAILED_DEFINITIVE','BLOCKED'))
    OR (OLD.state='POLLING' AND NEW.state IN
        ('POLLING','RESPONSE_CAPTURED','FAILED_DEFINITIVE','BLOCKED'))
    OR (OLD.state='RESPONSE_CAPTURED' AND NEW.state IN
        ('RESPONSE_CAPTURED','DOWNLOADED','VERIFIED','FAILED_DEFINITIVE',
         'FAILED_UNCERTAIN','BLOCKED'))
    OR (OLD.state='DOWNLOADED' AND NEW.state IN ('NORMALIZED','BLOCKED'))
    OR (OLD.state='NORMALIZED' AND NEW.state IN ('VERIFIED','BLOCKED'))
  )
  THEN RAISE EXCEPTION 'AI_VISUAL_EFFECT_STATE_TRANSITION_FORBIDDEN'; END IF;
  PERFORM validate_ai_visual_effect_evidence(NEW);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION prevent_ai_visual_effect_delete()
RETURNS trigger AS $$ BEGIN
  RAISE EXCEPTION 'AI_VISUAL_EFFECT_DELETE_FORBIDDEN';
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ai_visual_effect_insert_seal
BEFORE INSERT ON ai_visual_asset_effects
FOR EACH ROW EXECUTE FUNCTION validate_ai_visual_effect_insert();
CREATE TRIGGER trg_ai_visual_effect_update_cas
BEFORE UPDATE ON ai_visual_asset_effects
FOR EACH ROW EXECUTE FUNCTION validate_ai_visual_effect_update();
CREATE TRIGGER trg_ai_visual_effect_delete_forbidden
BEFORE DELETE ON ai_visual_asset_effects
FOR EACH ROW EXECUTE FUNCTION prevent_ai_visual_effect_delete();
"""


_MANIFEST_SEAL_SQL = r"""
CREATE OR REPLACE FUNCTION validate_ai_visual_manifest_insert()
RETURNS trigger AS $$
DECLARE visual_run ai_visual_production_runs%ROWTYPE;
DECLARE scene_plan ai_visual_scene_plan_snapshots%ROWTYPE;
DECLARE effect_rollup RECORD;
BEGIN
  SELECT * INTO visual_run FROM ai_visual_production_runs
    WHERE id=NEW.visual_production_run_id;
  SELECT * INTO scene_plan FROM ai_visual_scene_plan_snapshots
    WHERE id=NEW.scene_plan_snapshot_id;
  SELECT
    count(*)::integer AS asset_count,
    count(*) FILTER (WHERE route='AI_IMAGE')::integer AS image_count,
    count(*) FILTER (WHERE route='AI_VIDEO')::integer AS video_count,
    count(*) FILTER (WHERE state<>'VERIFIED')::integer AS unverified_count,
    coalesce(sum(provider_call_count),0)::integer AS provider_calls,
    coalesce(sum(estimated_cost_usd),0) AS estimated_cost,
    coalesce(sum(coalesce(actual_cost_usd,estimated_cost_usd)),0) AS settled_cost
  INTO effect_rollup
  FROM ai_visual_asset_effects
  WHERE visual_production_run_id=NEW.visual_production_run_id
    AND scene_plan_snapshot_id=NEW.scene_plan_snapshot_id;

  IF visual_run.id IS NULL OR scene_plan.id IS NULL
     OR scene_plan.visual_production_run_id IS DISTINCT FROM visual_run.id
     OR scene_plan.content_hash IS DISTINCT FROM NEW.scene_plan_hash
     OR scene_plan.style_bible_hash IS DISTINCT FROM NEW.style_bible_hash
     OR scene_plan.scene_count IS DISTINCT FROM NEW.scene_count
     OR scene_plan.ai_image_scene_count IS DISTINCT FROM NEW.ai_image_scene_count
     OR scene_plan.ai_video_scene_count IS DISTINCT FROM NEW.ai_video_scene_count
     OR scene_plan.unique_asset_slot_count IS DISTINCT FROM NEW.asset_count
     OR effect_rollup.asset_count IS DISTINCT FROM NEW.asset_count
     OR effect_rollup.image_count IS DISTINCT FROM NEW.ai_image_asset_count
     OR effect_rollup.video_count IS DISTINCT FROM NEW.ai_video_asset_count
     OR effect_rollup.unverified_count <> 0
     OR effect_rollup.provider_calls IS DISTINCT FROM NEW.total_provider_call_count
     OR effect_rollup.estimated_cost IS DISTINCT FROM NEW.total_estimated_cost_usd
     OR effect_rollup.settled_cost IS DISTINCT FROM NEW.total_actual_or_conservative_cost_usd
     OR visual_run.style_bible_hash IS DISTINCT FROM NEW.style_bible_hash
     OR visual_run.scene_plan_id IS DISTINCT FROM scene_plan.id
     OR visual_run.scene_plan_hash IS DISTINCT FROM NEW.scene_plan_hash
     OR visual_run.motion_grammar_hash IS DISTINCT FROM NEW.motion_grammar_hash
     OR visual_run.effect_plan_hash IS DISTINCT FROM NEW.effect_plan_hash
     OR NEW.content->>'content_hash' IS DISTINCT FROM NEW.content_hash
     OR NEW.content->>'scene_plan_hash' IS DISTINCT FROM NEW.scene_plan_hash
     OR NEW.content->>'style_bible_hash' IS DISTINCT FROM NEW.style_bible_hash
     OR NEW.content->>'motion_grammar_hash' IS DISTINCT FROM NEW.motion_grammar_hash
     OR NEW.content->>'effect_plan_hash' IS DISTINCT FROM NEW.effect_plan_hash
  THEN RAISE EXCEPTION 'AI_VISUAL_ASSET_MANIFEST_SEAL_MISMATCH'; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ai_visual_manifest_insert_seal
BEFORE INSERT ON ai_visual_asset_manifests
FOR EACH ROW EXECUTE FUNCTION validate_ai_visual_manifest_insert();
"""


_FINAL_CANDIDATE_SEAL_SQL = r"""
CREATE OR REPLACE FUNCTION validate_ai_visual_final_candidate_insert()
RETURNS trigger AS $$
DECLARE visual_run ai_visual_production_runs%ROWTYPE;
DECLARE manifest ai_visual_asset_manifests%ROWTYPE;
DECLARE authority ai_visual_rerender_authorities%ROWTYPE;
DECLARE old_candidate final_review_candidates%ROWTYPE;
DECLARE final_media final_media_refs%ROWTYPE;
DECLARE workflow production_workflow_runs%ROWTYPE;
BEGIN
  IF NEW.ai_visual_production_run_id IS NULL
     AND NEW.ai_visual_asset_manifest_hash IS NULL
     AND NEW.ffmpeg_effect_plan_hash IS NULL
     AND NEW.supersedes_final_review_candidate_id IS NULL
  THEN RETURN NEW; END IF;
  IF NEW.ai_visual_production_run_id IS NULL
     OR NEW.ai_visual_asset_manifest_hash IS NULL
     OR NEW.ffmpeg_effect_plan_hash IS NULL
     OR NEW.ai_visual_asset_manifest_hash !~ '^[0-9a-f]{64}$'
     OR NEW.ffmpeg_effect_plan_hash !~ '^[0-9a-f]{64}$'
     OR NEW.supersedes_final_review_candidate_id=NEW.id
  THEN RAISE EXCEPTION 'AI_VISUAL_FINAL_CANDIDATE_LINEAGE_PARTIAL'; END IF;

  SELECT * INTO visual_run FROM ai_visual_production_runs
    WHERE id=NEW.ai_visual_production_run_id;
  SELECT * INTO manifest FROM ai_visual_asset_manifests
    WHERE visual_production_run_id=NEW.ai_visual_production_run_id;
  SELECT * INTO final_media FROM final_media_refs WHERE id=NEW.final_media_ref_id;
  SELECT * INTO workflow FROM production_workflow_runs WHERE id=NEW.workflow_run_id;

  IF visual_run.id IS NULL OR manifest.id IS NULL
     OR final_media.id IS NULL OR workflow.id IS NULL
     OR visual_run.state NOT IN ('ARCHIVED','FINAL_REVIEW_READY')
     OR visual_run.workflow_run_id IS DISTINCT FROM NEW.workflow_run_id
     OR visual_run.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR visual_run.production_package_artifact_version_id IS DISTINCT FROM NEW.production_package_artifact_version_id
     OR visual_run.production_package_hash IS DISTINCT FROM NEW.production_package_hash
     OR visual_run.asset_manifest_id IS DISTINCT FROM manifest.id
     OR visual_run.asset_manifest_hash IS DISTINCT FROM manifest.content_hash
     OR manifest.content_hash IS DISTINCT FROM NEW.ai_visual_asset_manifest_hash
     OR manifest.effect_plan_hash IS DISTINCT FROM NEW.ffmpeg_effect_plan_hash
     OR visual_run.final_media_ref_id IS DISTINCT FROM final_media.id
     OR visual_run.render_output_checksum IS DISTINCT FROM NEW.render_output_checksum
     OR final_media.checksum_sha256 IS DISTINCT FROM NEW.render_output_checksum
     OR final_media.video_project_id IS DISTINCT FROM NEW.video_project_id
     OR final_media.production_package_artifact_version_id IS DISTINCT FROM NEW.production_package_artifact_version_id
     OR final_media.production_package_hash IS DISTINCT FROM NEW.production_package_hash
     OR visual_run.archive_receipt_hash IS DISTINCT FROM NEW.archive_receipt_hash
     OR workflow.ai_visual_production_run_id IS DISTINCT FROM visual_run.id
     OR workflow.ai_visual_asset_manifest_hash IS DISTINCT FROM manifest.content_hash
     OR workflow.ffmpeg_effect_plan_hash IS DISTINCT FROM manifest.effect_plan_hash
  THEN RAISE EXCEPTION 'AI_VISUAL_FINAL_CANDIDATE_SPLICE_DETECTED'; END IF;

  IF visual_run.execution_kind='GOVERNED_RERENDER' THEN
    SELECT * INTO authority FROM ai_visual_rerender_authorities
      WHERE id=visual_run.rerender_authority_id;
    SELECT * INTO old_candidate FROM final_review_candidates
      WHERE id=NEW.supersedes_final_review_candidate_id;
    IF NEW.supersedes_final_review_candidate_id IS NULL
       OR authority.id IS NULL OR old_candidate.id IS NULL
       OR authority.rejected_final_review_candidate_id IS DISTINCT FROM old_candidate.id
       OR authority.rejected_final_review_candidate_hash IS DISTINCT FROM old_candidate.candidate_hash
       OR authority.source_workflow_run_id IS DISTINCT FROM old_candidate.workflow_run_id
       OR authority.replacement_workflow_run_id IS DISTINCT FROM NEW.workflow_run_id
       OR old_candidate.workflow_run_id IS NOT DISTINCT FROM NEW.workflow_run_id
    THEN RAISE EXCEPTION 'AI_VISUAL_FINAL_CANDIDATE_SPLICE_DETECTED'; END IF;
  ELSIF visual_run.execution_kind='NORMAL_PRODUCTION' THEN
    IF visual_run.rerender_authority_id IS NOT NULL
       OR NEW.supersedes_final_review_candidate_id IS NOT NULL
    THEN RAISE EXCEPTION 'AI_VISUAL_FINAL_CANDIDATE_SPLICE_DETECTED'; END IF;
  ELSE
    RAISE EXCEPTION 'AI_VISUAL_FINAL_CANDIDATE_SPLICE_DETECTED';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ai_visual_final_candidate_insert_seal
BEFORE INSERT ON final_review_candidates
FOR EACH ROW EXECUTE FUNCTION validate_ai_visual_final_candidate_insert();
"""


_REPLACEMENT_LINEAGE_SEAL_SQL = r"""
CREATE OR REPLACE FUNCTION validate_ai_visual_replacement_lineage_insert()
RETURNS trigger AS $$
DECLARE authority ai_visual_rerender_authorities%ROWTYPE;
DECLARE visual_run ai_visual_production_runs%ROWTYPE;
DECLARE manifest ai_visual_asset_manifests%ROWTYPE;
DECLARE old_candidate final_review_candidates%ROWTYPE;
DECLARE new_candidate final_review_candidates%ROWTYPE;
DECLARE old_media final_media_refs%ROWTYPE;
DECLARE new_media final_media_refs%ROWTYPE;
BEGIN
  SELECT * INTO authority FROM ai_visual_rerender_authorities
    WHERE id=NEW.rerender_authority_id;
  SELECT * INTO visual_run FROM ai_visual_production_runs
    WHERE id=NEW.visual_production_run_id;
  SELECT * INTO manifest FROM ai_visual_asset_manifests WHERE id=NEW.asset_manifest_id;
  SELECT * INTO old_candidate FROM final_review_candidates
    WHERE id=NEW.rejected_final_review_candidate_id;
  SELECT * INTO new_candidate FROM final_review_candidates
    WHERE id=NEW.replacement_final_review_candidate_id;
  SELECT * INTO old_media FROM final_media_refs WHERE id=NEW.rejected_final_media_ref_id;
  SELECT * INTO new_media FROM final_media_refs WHERE id=NEW.replacement_final_media_ref_id;

  IF authority.id IS NULL OR visual_run.id IS NULL OR manifest.id IS NULL
     OR old_candidate.id IS NULL OR new_candidate.id IS NULL
     OR old_media.id IS NULL OR new_media.id IS NULL
     OR authority.authorized_visual_production_run_id IS DISTINCT FROM visual_run.id
     OR authority.rejected_final_media_ref_id IS DISTINCT FROM old_media.id
     OR authority.rejected_final_review_candidate_id IS DISTINCT FROM old_candidate.id
     OR authority.source_workflow_run_id IS DISTINCT FROM old_candidate.workflow_run_id
     OR authority.replacement_workflow_run_id IS DISTINCT FROM new_candidate.workflow_run_id
     OR old_candidate.workflow_run_id IS NOT DISTINCT FROM new_candidate.workflow_run_id
     OR visual_run.rerender_authority_id IS DISTINCT FROM authority.id
     OR visual_run.asset_manifest_id IS DISTINCT FROM manifest.id
     OR visual_run.asset_manifest_hash IS DISTINCT FROM manifest.content_hash
     OR visual_run.final_media_ref_id IS DISTINCT FROM new_media.id
     OR visual_run.final_review_candidate_id IS DISTINCT FROM new_candidate.id
     OR visual_run.state IS DISTINCT FROM 'FINAL_REVIEW_READY'
     OR manifest.content_hash IS DISTINCT FROM NEW.asset_manifest_hash
     OR old_candidate.final_media_ref_id IS DISTINCT FROM old_media.id
     OR new_candidate.supersedes_final_review_candidate_id IS DISTINCT FROM old_candidate.id
     OR new_candidate.ai_visual_production_run_id IS DISTINCT FROM visual_run.id
     OR new_candidate.ai_visual_asset_manifest_hash IS DISTINCT FROM manifest.content_hash
     OR new_candidate.ffmpeg_effect_plan_hash IS DISTINCT FROM manifest.effect_plan_hash
     OR new_candidate.final_media_ref_id IS DISTINCT FROM new_media.id
     OR new_candidate.render_output_checksum IS DISTINCT FROM NEW.replacement_render_checksum
     OR new_media.checksum_sha256 IS DISTINCT FROM NEW.replacement_render_checksum
     OR new_candidate.archive_receipt_hash IS DISTINCT FROM NEW.replacement_archive_receipt_hash
     OR visual_run.archive_receipt_hash IS DISTINCT FROM NEW.replacement_archive_receipt_hash
  THEN RAISE EXCEPTION 'AI_VISUAL_REPLACEMENT_LINEAGE_SEAL_MISMATCH'; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ai_visual_replacement_lineage_insert_seal
BEFORE INSERT ON ai_visual_replacement_lineages
FOR EACH ROW EXECUTE FUNCTION validate_ai_visual_replacement_lineage_insert();
"""
