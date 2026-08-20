"""add company-scoped business actions and continuation-capital review authority

Revision ID: 0088_business_continuation
Revises: 0087_business_os
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0088_business_continuation"
down_revision = "0087_business_os"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.alter_column(
        "business_action_items",
        "channel_workspace_id",
        existing_type=UUID,
        nullable=True,
    )
    op.create_table(
        "continuation_capital_reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("recommendation", sa.String(length=24), nullable=False),
        sa.Column("reason_codes", JSONB, nullable=False),
        sa.Column("input_refs", JSONB, nullable=False),
        sa.Column("evidence_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("human_decision_required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "recommendation IN ('CONTINUE','THROTTLE','PIVOT','KILL_REVIEW')",
            name="ck_continuation_capital_recommendation",
        ),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "evidence_snapshot_hash",
            name="uq_continuation_capital_evidence",
        ),
    )
    op.create_index(
        "ix_continuation_capital_company",
        "continuation_capital_reviews",
        ["company_id"],
    )
    op.create_index(
        "ix_continuation_capital_channel",
        "continuation_capital_reviews",
        ["channel_workspace_id"],
    )
    op.create_index(
        "ix_continuation_capital_recommendation",
        "continuation_capital_reviews",
        ["recommendation"],
    )
    op.create_index(
        "ix_continuation_capital_hash",
        "continuation_capital_reviews",
        ["evidence_snapshot_hash"],
    )


def downgrade() -> None:
    op.drop_table("continuation_capital_reviews")
    op.alter_column(
        "business_action_items",
        "channel_workspace_id",
        existing_type=UUID,
        nullable=False,
    )
