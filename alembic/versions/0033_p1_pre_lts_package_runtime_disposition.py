"""P1 pre-LTS package runtime disposition

Revision ID: 0033_p1_pre_lts_disposition
Revises: 0032_r3d9_ux2_review_queue
Create Date: 2026-07-04 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0033_p1_pre_lts_disposition"
down_revision: str | None = "0032_r3d9_ux2_review_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "package_runtime_dispositions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("disposition", sa.String(length=80), nullable=False),
        sa.Column("reason_codes_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_package_runtime_dispositions_package", "package_runtime_dispositions", ["package_id"])
    op.create_index("ix_package_runtime_dispositions_disposition", "package_runtime_dispositions", ["disposition"])
    op.create_index("ix_package_runtime_dispositions_created_at", "package_runtime_dispositions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_package_runtime_dispositions_created_at", table_name="package_runtime_dispositions")
    op.drop_index("ix_package_runtime_dispositions_disposition", table_name="package_runtime_dispositions")
    op.drop_index("ix_package_runtime_dispositions_package", table_name="package_runtime_dispositions")
    op.drop_table("package_runtime_dispositions")
