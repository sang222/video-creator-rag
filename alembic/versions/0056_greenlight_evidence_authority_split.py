"""Separate claim-source authority from quantitative market-demand authority.

Revision ID: 0056_greenlight_authority_split
Revises: 0055_editorial_fresh_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0056_greenlight_authority_split"
down_revision: str | None = "0055_editorial_fresh_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable is intentional: historical immutable rows remain readable and
    # the service derives their legacy authority conservatively by source type.
    op.add_column(
        "search_demand_evidence",
        sa.Column("authority_purpose", sa.String(length=40), nullable=True),
    )
    op.create_index(
        "ix_search_demand_evidence_authority_purpose",
        "search_demand_evidence",
        ["authority_purpose"],
    )


def downgrade() -> None:
    op.drop_index("ix_search_demand_evidence_authority_purpose", table_name="search_demand_evidence")
    op.drop_column("search_demand_evidence", "authority_purpose")
