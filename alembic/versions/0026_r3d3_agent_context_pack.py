"""R3D3 agent context pack snapshots

Revision ID: 0026_r3d3_agent_context_pack
Revises: 0025_r3d2_effective_context
Create Date: 2026-07-01 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0026_r3d3_agent_context_pack"
down_revision: str | None = "0025_r3d2_effective_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "agent_context_pack_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_render_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("task_type", sa.String(length=160), nullable=True),
        sa.Column("lane", sa.String(length=160), nullable=False),
        sa.Column("context_pack_version", sa.String(length=80), nullable=False),
        sa.Column("builder_version", sa.String(length=80), nullable=False),
        sa.Column("agent_context_contract_hash", sa.String(length=128), nullable=False),
        sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effective_context_hash", sa.Text(), nullable=False),
        sa.Column("channel_contract_hash", sa.Text(), nullable=True),
        sa.Column("compiled_policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("compiled_policy_snapshot_hash", sa.Text(), nullable=True),
        sa.Column("context_pack_hash", sa.String(length=128), nullable=False),
        sa.Column("prompt_context_hash", sa.String(length=128), nullable=True),
        sa.Column("artifact_digest_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("evidence_digest_hash", sa.String(length=128), nullable=True),
        sa.Column("common_skill_digest_hash", sa.String(length=128), nullable=True),
        sa.Column("runtime_guard_digest_hash", sa.String(length=128), nullable=False),
        sa.Column("budget_report_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("omitted_items_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("largest_context_contributors_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("agent_context_contract_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("context_pack_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("shape_gate_result_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["prompt_render_run_id"], ["prompt_render_runs.id"]),
        sa.ForeignKeyConstraint(["effective_context_snapshot_id"], ["effective_channel_runtime_context_snapshots.id"]),
        sa.ForeignKeyConstraint(["compiled_policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_context_pack_snapshots_package", "agent_context_pack_snapshots", ["package_id"])
    op.create_index("ix_agent_context_pack_snapshots_project", "agent_context_pack_snapshots", ["video_project_id"])
    op.create_index("ix_agent_context_pack_snapshots_prompt_render", "agent_context_pack_snapshots", ["prompt_render_run_id"])
    op.create_index("ix_agent_context_pack_snapshots_agent", "agent_context_pack_snapshots", ["agent_key"])
    op.create_index(
        "ix_agent_context_pack_snapshots_effective_context",
        "agent_context_pack_snapshots",
        ["effective_context_snapshot_id"],
    )
    op.create_index("ix_agent_context_pack_snapshots_context_hash", "agent_context_pack_snapshots", ["context_pack_hash"])
    op.create_index(
        "ix_agent_context_pack_snapshots_prompt_context_hash",
        "agent_context_pack_snapshots",
        ["prompt_context_hash"],
    )
    op.create_index("ix_agent_context_pack_snapshots_created_at", "agent_context_pack_snapshots", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_context_pack_snapshots_created_at", table_name="agent_context_pack_snapshots")
    op.drop_index("ix_agent_context_pack_snapshots_prompt_context_hash", table_name="agent_context_pack_snapshots")
    op.drop_index("ix_agent_context_pack_snapshots_context_hash", table_name="agent_context_pack_snapshots")
    op.drop_index("ix_agent_context_pack_snapshots_effective_context", table_name="agent_context_pack_snapshots")
    op.drop_index("ix_agent_context_pack_snapshots_agent", table_name="agent_context_pack_snapshots")
    op.drop_index("ix_agent_context_pack_snapshots_prompt_render", table_name="agent_context_pack_snapshots")
    op.drop_index("ix_agent_context_pack_snapshots_project", table_name="agent_context_pack_snapshots")
    op.drop_index("ix_agent_context_pack_snapshots_package", table_name="agent_context_pack_snapshots")
    op.drop_table("agent_context_pack_snapshots")
