"""R3D7 closed learning retrieval loop

Revision ID: 0030_r3d7_closed_learning_loop
Revises: 0029_r3d6_vector_safe_retrieval
Create Date: 2026-07-04 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0030_r3d7_closed_learning_loop"
down_revision: str | None = "0029_r3d6_vector_safe_retrieval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "memory_influence_manifests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("retrieval_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_facet_ids_used_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("memory_item_ids_used_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("digest_hash", sa.String(length=128), nullable=False),
        sa.Column("prompt_render_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("prompt_context_hash", sa.String(length=128), nullable=False),
        sa.Column("applied_as_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("ignored_memory_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("blocked_memory_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("scope_status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["effective_context_snapshot_id"], ["effective_channel_runtime_context_snapshots.id"]),
        sa.ForeignKeyConstraint(["prompt_render_run_id"], ["prompt_render_runs.id"]),
        sa.ForeignKeyConstraint(["retrieval_manifest_id"], ["vector_retrieval_manifests.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_influence_manifests_project", "memory_influence_manifests", ["video_project_id"])
    op.create_index("ix_memory_influence_manifests_package", "memory_influence_manifests", ["package_id"])
    op.create_index("ix_memory_influence_manifests_effective_context", "memory_influence_manifests", ["effective_context_snapshot_id"])
    op.create_index("ix_memory_influence_manifests_agent", "memory_influence_manifests", ["agent_key"])
    op.create_index("ix_memory_influence_manifests_retrieval", "memory_influence_manifests", ["retrieval_manifest_id"])
    op.create_index("ix_memory_influence_manifests_prompt_render", "memory_influence_manifests", ["prompt_render_run_id"])
    op.create_index("ix_memory_influence_manifests_digest_hash", "memory_influence_manifests", ["digest_hash"])
    op.create_index("ix_memory_influence_manifests_scope_status", "memory_influence_manifests", ["scope_status"])
    op.create_index("ix_memory_influence_manifests_created_at", "memory_influence_manifests", ["created_at"])

    op.create_table(
        "quality_delta_attributions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_memory_influence_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("market_context_hash", sa.String(length=128), nullable=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expected_metric_family", sa.String(length=80), nullable=False),
        sa.Column("expected_improvement_direction", sa.String(length=40), nullable=False),
        sa.Column("baseline_snapshot_ref", JSONB, nullable=True),
        sa.Column("observed_snapshot_ref", JSONB, nullable=True),
        sa.Column("attribution_window", sa.String(length=80), nullable=False),
        sa.Column("confidence_result", sa.String(length=40), nullable=False),
        sa.Column("confidence_delta", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["content_categories.id"]),
        sa.ForeignKeyConstraint(["character_binding_id"], ["character_bindings.id"]),
        sa.ForeignKeyConstraint(["effective_context_snapshot_id"], ["effective_channel_runtime_context_snapshots.id"]),
        sa.ForeignKeyConstraint(["source_memory_influence_manifest_id"], ["memory_influence_manifests.id"]),
        sa.ForeignKeyConstraint(["source_video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["target_uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["target_video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quality_delta_attributions_manifest", "quality_delta_attributions", ["source_memory_influence_manifest_id"])
    op.create_index("ix_quality_delta_attributions_source_project", "quality_delta_attributions", ["source_video_project_id"])
    op.create_index("ix_quality_delta_attributions_target_video", "quality_delta_attributions", ["target_uploaded_video_id"])
    op.create_index("ix_quality_delta_attributions_target_project", "quality_delta_attributions", ["target_video_project_id"])
    op.create_index("ix_quality_delta_attributions_effective_context", "quality_delta_attributions", ["effective_context_snapshot_id"])
    op.create_index("ix_quality_delta_attributions_metric_family", "quality_delta_attributions", ["expected_metric_family"])
    op.create_index("ix_quality_delta_attributions_result", "quality_delta_attributions", ["confidence_result"])
    op.create_index("ix_quality_delta_attributions_created_at", "quality_delta_attributions", ["created_at"])

    op.create_table(
        "learning_to_memory_promotion_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("learning_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_playbook_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_bundle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_memory_item_ids_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("created_memory_facet_ids_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("run_status", sa.String(length=40), nullable=False),
        sa.Column("reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("human_approval_ref", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["approved_playbook_entry_id"], ["approved_playbook_entries.id"]),
        sa.ForeignKeyConstraint(["evidence_bundle_id"], ["learning_evidence_bundles.id"]),
        sa.ForeignKeyConstraint(["learning_candidate_id"], ["learning_candidates.id"]),
        sa.ForeignKeyConstraint(["source_uploaded_video_id"], ["uploaded_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_to_memory_runs_candidate", "learning_to_memory_promotion_runs", ["learning_candidate_id"])
    op.create_index("ix_learning_to_memory_runs_playbook", "learning_to_memory_promotion_runs", ["approved_playbook_entry_id"])
    op.create_index("ix_learning_to_memory_runs_evidence", "learning_to_memory_promotion_runs", ["evidence_bundle_id"])
    op.create_index("ix_learning_to_memory_runs_source_video", "learning_to_memory_promotion_runs", ["source_uploaded_video_id"])
    op.create_index("ix_learning_to_memory_runs_status", "learning_to_memory_promotion_runs", ["run_status"])
    op.create_index("ix_learning_to_memory_runs_created_at", "learning_to_memory_promotion_runs", ["created_at"])

    op.create_table(
        "agent_memory_application_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("memory_influence_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_digest_hash", sa.String(length=128), nullable=False),
        sa.Column("application_mode", sa.String(length=80), nullable=False),
        sa.Column("applied_context_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["memory_influence_manifest_id"], ["memory_influence_manifests.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_memory_application_records_project", "agent_memory_application_records", ["video_project_id"])
    op.create_index("ix_agent_memory_application_records_package", "agent_memory_application_records", ["package_id"])
    op.create_index("ix_agent_memory_application_records_agent", "agent_memory_application_records", ["agent_key"])
    op.create_index("ix_agent_memory_application_records_manifest", "agent_memory_application_records", ["memory_influence_manifest_id"])
    op.create_index("ix_agent_memory_application_records_digest_hash", "agent_memory_application_records", ["memory_digest_hash"])
    op.create_index("ix_agent_memory_application_records_mode", "agent_memory_application_records", ["application_mode"])
    op.create_index("ix_agent_memory_application_records_created_at", "agent_memory_application_records", ["created_at"])

    op.create_table(
        "memory_confidence_update_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_facet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quality_delta_attribution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("old_confidence_label", sa.String(length=40), nullable=False),
        sa.Column("new_confidence_label", sa.String(length=40), nullable=False),
        sa.Column("confidence_delta", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("requires_human_review", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["memory_facet_id"], ["memory_facets.id"]),
        sa.ForeignKeyConstraint(["quality_delta_attribution_id"], ["quality_delta_attributions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_confidence_update_ledger_facet", "memory_confidence_update_ledger", ["memory_facet_id"])
    op.create_index("ix_memory_confidence_update_ledger_attribution", "memory_confidence_update_ledger", ["quality_delta_attribution_id"])
    op.create_index("ix_memory_confidence_update_ledger_created_at", "memory_confidence_update_ledger", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_memory_confidence_update_ledger_created_at", table_name="memory_confidence_update_ledger")
    op.drop_index("ix_memory_confidence_update_ledger_attribution", table_name="memory_confidence_update_ledger")
    op.drop_index("ix_memory_confidence_update_ledger_facet", table_name="memory_confidence_update_ledger")
    op.drop_table("memory_confidence_update_ledger")

    op.drop_index("ix_agent_memory_application_records_created_at", table_name="agent_memory_application_records")
    op.drop_index("ix_agent_memory_application_records_mode", table_name="agent_memory_application_records")
    op.drop_index("ix_agent_memory_application_records_digest_hash", table_name="agent_memory_application_records")
    op.drop_index("ix_agent_memory_application_records_manifest", table_name="agent_memory_application_records")
    op.drop_index("ix_agent_memory_application_records_agent", table_name="agent_memory_application_records")
    op.drop_index("ix_agent_memory_application_records_package", table_name="agent_memory_application_records")
    op.drop_index("ix_agent_memory_application_records_project", table_name="agent_memory_application_records")
    op.drop_table("agent_memory_application_records")

    op.drop_index("ix_learning_to_memory_runs_created_at", table_name="learning_to_memory_promotion_runs")
    op.drop_index("ix_learning_to_memory_runs_status", table_name="learning_to_memory_promotion_runs")
    op.drop_index("ix_learning_to_memory_runs_source_video", table_name="learning_to_memory_promotion_runs")
    op.drop_index("ix_learning_to_memory_runs_evidence", table_name="learning_to_memory_promotion_runs")
    op.drop_index("ix_learning_to_memory_runs_playbook", table_name="learning_to_memory_promotion_runs")
    op.drop_index("ix_learning_to_memory_runs_candidate", table_name="learning_to_memory_promotion_runs")
    op.drop_table("learning_to_memory_promotion_runs")

    op.drop_index("ix_quality_delta_attributions_created_at", table_name="quality_delta_attributions")
    op.drop_index("ix_quality_delta_attributions_result", table_name="quality_delta_attributions")
    op.drop_index("ix_quality_delta_attributions_metric_family", table_name="quality_delta_attributions")
    op.drop_index("ix_quality_delta_attributions_effective_context", table_name="quality_delta_attributions")
    op.drop_index("ix_quality_delta_attributions_target_project", table_name="quality_delta_attributions")
    op.drop_index("ix_quality_delta_attributions_target_video", table_name="quality_delta_attributions")
    op.drop_index("ix_quality_delta_attributions_source_project", table_name="quality_delta_attributions")
    op.drop_index("ix_quality_delta_attributions_manifest", table_name="quality_delta_attributions")
    op.drop_table("quality_delta_attributions")

    op.drop_index("ix_memory_influence_manifests_created_at", table_name="memory_influence_manifests")
    op.drop_index("ix_memory_influence_manifests_scope_status", table_name="memory_influence_manifests")
    op.drop_index("ix_memory_influence_manifests_digest_hash", table_name="memory_influence_manifests")
    op.drop_index("ix_memory_influence_manifests_prompt_render", table_name="memory_influence_manifests")
    op.drop_index("ix_memory_influence_manifests_retrieval", table_name="memory_influence_manifests")
    op.drop_index("ix_memory_influence_manifests_agent", table_name="memory_influence_manifests")
    op.drop_index("ix_memory_influence_manifests_effective_context", table_name="memory_influence_manifests")
    op.drop_index("ix_memory_influence_manifests_package", table_name="memory_influence_manifests")
    op.drop_index("ix_memory_influence_manifests_project", table_name="memory_influence_manifests")
    op.drop_table("memory_influence_manifests")
