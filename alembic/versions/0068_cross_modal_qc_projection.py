"""Add an additive receipt projection for narration-timed cross-modal QC."""

from alembic import op
import sqlalchemy as sa


revision = "0068_cross_modal_qc_projection"
down_revision = "0067_replacement_seal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep the immutable cross-modal report addressable from a workflow run."""

    op.add_column(
        "production_workflow_runs",
        sa.Column("cross_modal_qc_receipt_ref", sa.Text(), nullable=True),
    )
    op.add_column(
        "production_workflow_runs",
        sa.Column("cross_modal_qc_receipt_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError("0068 is intentionally forward-only in production")
