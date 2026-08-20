"""allow policy-current learning reassessment of immutable evidence

Revision ID: 0090_learning_reassessment
Revises: 0089_business_action_lifecycle
"""

from __future__ import annotations

from alembic import op


revision = "0090_learning_reassessment"
down_revision = "0089_business_action_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep command idempotency while allowing a later policy/evidence review."""

    op.drop_constraint(
        "uq_learning_review_evidence", "learning_reviews", type_="unique"
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_learning_review_evidence",
        "learning_reviews",
        ["fingerprint_id", "window_key", "evidence_hash"],
    )
