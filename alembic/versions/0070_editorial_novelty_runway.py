"""Add persisted editorial territory and novelty evidence.

Revision ID: 0070_editorial_novelty_runway
Revises: 0069_lifecycle_pause
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0070_editorial_novelty_runway"
down_revision: str | None = "0069_lifecycle_pause"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical rows remain immutable until the reviewed maintenance service
    # computes a territory from their frozen authorities.
    op.add_column(
        "editorial_idea_candidates",
        sa.Column("editorial_territory_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "editorial_idea_candidates",
        sa.Column("editorial_novelty_receipt", postgresql.JSONB(), nullable=True),
    )
    op.create_index(
        "ix_editorial_idea_candidates_territory",
        "editorial_idea_candidates",
        ["channel_workspace_id", "policy_snapshot_id", "editorial_territory_key"],
    )


def downgrade() -> None:
    raise RuntimeError("0070 is intentionally forward-only in production")
