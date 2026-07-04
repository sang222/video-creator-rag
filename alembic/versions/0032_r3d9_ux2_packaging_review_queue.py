"""R3D9 UX2 packaging review queue and patch approval cockpit

Revision ID: 0032_r3d9_ux2_review_queue
Revises: 0031_r3d8_cost_firewall
Create Date: 2026-07-04 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0032_r3d9_ux2_review_queue"
down_revision: str | None = "0031_r3d8_cost_firewall"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "packaging_review_queue_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gate_key", sa.String(length=160), nullable=False),
        sa.Column("issue_code", sa.String(length=160), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("target_artifact_type", sa.String(length=120), nullable=False),
        sa.Column("target_artifact_ref", sa.Text(), nullable=True),
        sa.Column("source_gate_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_gate_batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=40), server_default="PENDING_PATCH", nullable=False),
        sa.Column("next_action_code", sa.String(length=80), server_default="NEEDS_PROPOSED_PATCH", nullable=False),
        sa.Column("human_readable_title", sa.Text(), nullable=False),
        sa.Column("human_readable_why", sa.Text(), nullable=False),
        sa.Column("human_readable_fix", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["effective_context_snapshot_id"], ["effective_channel_runtime_context_snapshots.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.ForeignKeyConstraint(["source_gate_batch_id"], ["r3d4_gate_batch_runs.id"]),
        sa.ForeignKeyConstraint(["source_gate_run_id"], ["r3d4_gate_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_packaging_review_queue_items_package", "packaging_review_queue_items", ["package_id"])
    op.create_index("ix_packaging_review_queue_items_project", "packaging_review_queue_items", ["video_project_id"])
    op.create_index("ix_packaging_review_queue_items_effective_context", "packaging_review_queue_items", ["effective_context_snapshot_id"])
    op.create_index("ix_packaging_review_queue_items_gate", "packaging_review_queue_items", ["gate_key"])
    op.create_index("ix_packaging_review_queue_items_issue", "packaging_review_queue_items", ["issue_code"])
    op.create_index("ix_packaging_review_queue_items_status", "packaging_review_queue_items", ["status"])
    op.create_index(
        "ix_packaging_review_queue_items_dedupe",
        "packaging_review_queue_items",
        ["package_id", "gate_key", "issue_code", "target_artifact_ref"],
    )

    op.create_table(
        "packaging_proposed_patches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("queue_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_source", sa.String(length=80), nullable=False),
        sa.Column("routed_agent_key", sa.String(length=240), nullable=True),
        sa.Column("patch_type", sa.String(length=80), nullable=False),
        sa.Column("before_snapshot_ref", sa.Text(), nullable=False),
        sa.Column("proposed_patch_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("after_preview_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("affected_artifact_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("risk_level", sa.String(length=40), server_default="MEDIUM", nullable=False),
        sa.Column("requires_human_approval", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("patch_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="DRAFT", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.ForeignKeyConstraint(["queue_item_id"], ["packaging_review_queue_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_packaging_proposed_patches_queue_item", "packaging_proposed_patches", ["queue_item_id"])
    op.create_index("ix_packaging_proposed_patches_package", "packaging_proposed_patches", ["package_id"])
    op.create_index("ix_packaging_proposed_patches_status", "packaging_proposed_patches", ["status"])
    op.create_index("ix_packaging_proposed_patches_patch_hash", "packaging_proposed_patches", ["patch_hash"])
    op.create_index("ix_packaging_proposed_patches_created_at", "packaging_proposed_patches", ["created_at"])

    op.create_table(
        "packaging_patch_approval_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposed_patch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=40), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["proposed_patch_id"], ["packaging_proposed_patches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_packaging_patch_approval_decisions_patch", "packaging_patch_approval_decisions", ["proposed_patch_id"])
    op.create_index("ix_packaging_patch_approval_decisions_decision", "packaging_patch_approval_decisions", ["decision"])
    op.create_index("ix_packaging_patch_approval_decisions_created_at", "packaging_patch_approval_decisions", ["created_at"])

    op.create_table(
        "packaging_patch_apply_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposed_patch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("apply_status", sa.String(length=40), nullable=False),
        sa.Column("created_artifact_ref", sa.Text(), nullable=True),
        sa.Column("created_handoff_override_ref", sa.Text(), nullable=True),
        sa.Column("created_version_hash", sa.String(length=128), nullable=True),
        sa.Column("reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.ForeignKeyConstraint(["proposed_patch_id"], ["packaging_proposed_patches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_packaging_patch_apply_runs_patch", "packaging_patch_apply_runs", ["proposed_patch_id"])
    op.create_index("ix_packaging_patch_apply_runs_package", "packaging_patch_apply_runs", ["package_id"])
    op.create_index("ix_packaging_patch_apply_runs_status", "packaging_patch_apply_runs", ["apply_status"])
    op.create_index("ix_packaging_patch_apply_runs_created_at", "packaging_patch_apply_runs", ["created_at"])

    op.create_table(
        "packaging_gate_rerun_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposed_patch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gate_keys_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("rerun_status", sa.String(length=40), nullable=False),
        sa.Column("gate_batch_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["gate_batch_run_id"], ["r3d4_gate_batch_runs.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.ForeignKeyConstraint(["proposed_patch_id"], ["packaging_proposed_patches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_packaging_gate_rerun_records_package", "packaging_gate_rerun_records", ["package_id"])
    op.create_index("ix_packaging_gate_rerun_records_patch", "packaging_gate_rerun_records", ["proposed_patch_id"])
    op.create_index("ix_packaging_gate_rerun_records_status", "packaging_gate_rerun_records", ["rerun_status"])
    op.create_index("ix_packaging_gate_rerun_records_created_at", "packaging_gate_rerun_records", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_packaging_gate_rerun_records_created_at", table_name="packaging_gate_rerun_records")
    op.drop_index("ix_packaging_gate_rerun_records_status", table_name="packaging_gate_rerun_records")
    op.drop_index("ix_packaging_gate_rerun_records_patch", table_name="packaging_gate_rerun_records")
    op.drop_index("ix_packaging_gate_rerun_records_package", table_name="packaging_gate_rerun_records")
    op.drop_table("packaging_gate_rerun_records")

    op.drop_index("ix_packaging_patch_apply_runs_created_at", table_name="packaging_patch_apply_runs")
    op.drop_index("ix_packaging_patch_apply_runs_status", table_name="packaging_patch_apply_runs")
    op.drop_index("ix_packaging_patch_apply_runs_package", table_name="packaging_patch_apply_runs")
    op.drop_index("ix_packaging_patch_apply_runs_patch", table_name="packaging_patch_apply_runs")
    op.drop_table("packaging_patch_apply_runs")

    op.drop_index("ix_packaging_patch_approval_decisions_created_at", table_name="packaging_patch_approval_decisions")
    op.drop_index("ix_packaging_patch_approval_decisions_decision", table_name="packaging_patch_approval_decisions")
    op.drop_index("ix_packaging_patch_approval_decisions_patch", table_name="packaging_patch_approval_decisions")
    op.drop_table("packaging_patch_approval_decisions")

    op.drop_index("ix_packaging_proposed_patches_created_at", table_name="packaging_proposed_patches")
    op.drop_index("ix_packaging_proposed_patches_patch_hash", table_name="packaging_proposed_patches")
    op.drop_index("ix_packaging_proposed_patches_status", table_name="packaging_proposed_patches")
    op.drop_index("ix_packaging_proposed_patches_package", table_name="packaging_proposed_patches")
    op.drop_index("ix_packaging_proposed_patches_queue_item", table_name="packaging_proposed_patches")
    op.drop_table("packaging_proposed_patches")

    op.drop_index("ix_packaging_review_queue_items_dedupe", table_name="packaging_review_queue_items")
    op.drop_index("ix_packaging_review_queue_items_status", table_name="packaging_review_queue_items")
    op.drop_index("ix_packaging_review_queue_items_issue", table_name="packaging_review_queue_items")
    op.drop_index("ix_packaging_review_queue_items_gate", table_name="packaging_review_queue_items")
    op.drop_index("ix_packaging_review_queue_items_effective_context", table_name="packaging_review_queue_items")
    op.drop_index("ix_packaging_review_queue_items_project", table_name="packaging_review_queue_items")
    op.drop_index("ix_packaging_review_queue_items_package", table_name="packaging_review_queue_items")
    op.drop_table("packaging_review_queue_items")
