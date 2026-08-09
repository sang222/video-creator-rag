"""Persist editorial idea synthesis and specificity authority.

Revision ID: 0071_editorial_specificity
Revises: 0070_editorial_novelty_runway
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0071_editorial_specificity"
down_revision: str | None = "0070_editorial_novelty_runway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep historical discovery lineage intact.  A NULL receipt means the row
    # still requires explicit evaluation by the current authority; it never
    # means an implied PASS.
    op.add_column(
        "editorial_idea_candidates",
        sa.Column("editorial_idea_proposal", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "editorial_idea_candidates",
        sa.Column("editorial_specificity_receipt", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError("0071 is intentionally forward-only in production")
