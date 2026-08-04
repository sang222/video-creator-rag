"""Add durable automatic-learning command and system-approval provenance.

Revision ID: 0057_learning_governance
Revises: 0056_greenlight_authority_split
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0057_learning_governance"
down_revision: str | None = "0056_greenlight_authority_split"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("learning_candidate_generation_runs", sa.Column("long_form_analytics_window_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("learning_candidate_generation_runs", sa.Column("learning_command_key", sa.String(length=128), nullable=True))
    op.create_foreign_key("fk_learning_runs_analytics_window", "learning_candidate_generation_runs", "long_form_analytics_windows", ["long_form_analytics_window_id"], ["id"])
    op.create_index("ix_learning_runs_analytics_window", "learning_candidate_generation_runs", ["long_form_analytics_window_id"])
    op.create_unique_constraint("uq_learning_generation_command_key", "learning_candidate_generation_runs", ["learning_command_key"])
    op.add_column("channel_memory_items", sa.Column("approval_authority_type", sa.String(length=40), nullable=True))
    op.add_column("channel_memory_items", sa.Column("approval_policy_version", sa.String(length=120), nullable=True))
    op.add_column("channel_memory_items", sa.Column("approval_policy_hash", sa.String(length=64), nullable=True))
    op.add_column("channel_memory_items", sa.Column("approval_evidence_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column("memory_approval_decisions", sa.Column("approval_authority_type", sa.String(length=40), nullable=True))
    op.add_column("memory_approval_decisions", sa.Column("policy_version", sa.String(length=120), nullable=True))
    op.add_column("memory_approval_decisions", sa.Column("policy_hash", sa.String(length=64), nullable=True))
    op.add_column("memory_approval_decisions", sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))


def downgrade() -> None:
    op.drop_column("memory_approval_decisions", "evidence_json")
    op.drop_column("memory_approval_decisions", "policy_hash")
    op.drop_column("memory_approval_decisions", "policy_version")
    op.drop_column("memory_approval_decisions", "approval_authority_type")
    op.drop_column("channel_memory_items", "approval_evidence_json")
    op.drop_column("channel_memory_items", "approval_policy_hash")
    op.drop_column("channel_memory_items", "approval_policy_version")
    op.drop_column("channel_memory_items", "approval_authority_type")
    op.drop_constraint("uq_learning_generation_command_key", "learning_candidate_generation_runs", type_="unique")
    op.drop_index("ix_learning_runs_analytics_window", table_name="learning_candidate_generation_runs")
    op.drop_constraint("fk_learning_runs_analytics_window", "learning_candidate_generation_runs", type_="foreignkey")
    op.drop_column("learning_candidate_generation_runs", "learning_command_key")
    op.drop_column("learning_candidate_generation_runs", "long_form_analytics_window_id")
