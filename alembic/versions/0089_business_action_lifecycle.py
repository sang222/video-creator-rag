"""repair business action lifecycle and disclosure assessment identity

Revision ID: 0089_business_action_lifecycle
Revises: 0088_business_continuation
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0089_business_action_lifecycle"
down_revision = "0088_business_continuation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_business_disclosure_package",
        "business_disclosure_assessments",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_business_disclosure_hash",
        "business_disclosure_assessments",
        ["assessment_hash"],
    )
    op.create_index(
        "uq_business_action_company_global_identity",
        "business_action_items",
        ["company_id", "action_type", "target_ref", "reason_code"],
        unique=True,
        postgresql_where=sa.text("channel_workspace_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_business_action_company_global_identity",
        table_name="business_action_items",
    )
    op.drop_constraint(
        "uq_business_disclosure_hash",
        "business_disclosure_assessments",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_business_disclosure_package",
        "business_disclosure_assessments",
        ["publish_package_ref", "policy_version"],
    )
