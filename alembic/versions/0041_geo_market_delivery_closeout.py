"""Add nullable strict geo-market publication lineage bindings.

Revision ID: 0041_geo_delivery
Revises: 0040_mr1_budget
Create Date: 2026-07-21 00:00:00

All columns are nullable so historical M7 and approval records retain their
original semantics.  The application enforces these fields fail-closed only
when a strict market-lineage envelope is supplied.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0041_geo_delivery"
down_revision: str | None = "0040_mr1_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    approval_columns = (
        sa.Column("policy_snapshot_id", UUID, nullable=True),
        sa.Column("destination_binding_id", UUID, nullable=True),
        sa.Column("destination_binding_fingerprint", sa.String(64), nullable=True),
        sa.Column("market_policy_hash", sa.String(64), nullable=True),
        sa.Column("approved_package_hash", sa.String(64), nullable=True),
        sa.Column("target_market_profile_ref", sa.Text(), nullable=True),
        sa.Column("target_market_profile_hash", sa.String(64), nullable=True),
        sa.Column("market_alignment_dossier_ref", sa.Text(), nullable=True),
        sa.Column("market_alignment_dossier_hash", sa.String(64), nullable=True),
        sa.Column("approved_publish_window", JSONB, nullable=True),
    )
    for column in approval_columns:
        op.add_column("approval_decisions", column)
    op.create_foreign_key(
        "fk_approval_decisions_policy_snapshot_id",
        "approval_decisions",
        "compiled_channel_policy_snapshots",
        ["policy_snapshot_id"],
        ["id"],
    )

    handoff_columns = (
        sa.Column("destination_binding_fingerprint", sa.String(64), nullable=True),
        sa.Column("market_policy_hash", sa.String(64), nullable=True),
        sa.Column("approved_package_hash", sa.String(64), nullable=True),
        sa.Column("approval_decision_id", UUID, nullable=True),
        sa.Column("target_market_profile_ref", sa.Text(), nullable=True),
        sa.Column("target_market_profile_hash", sa.String(64), nullable=True),
        sa.Column("market_alignment_dossier_ref", sa.Text(), nullable=True),
        sa.Column("market_alignment_dossier_hash", sa.String(64), nullable=True),
        sa.Column("approved_publish_timezone", sa.Text(), nullable=True),
        sa.Column("approved_publish_window", JSONB, nullable=True),
    )
    for column in handoff_columns:
        op.add_column("publish_handoff_packages", column)
    op.create_foreign_key(
        "fk_publish_handoff_approval_decision_id",
        "publish_handoff_packages",
        "approval_decisions",
        ["approval_decision_id"],
        ["id"],
    )

    confirmation_columns = (
        sa.Column("destination_binding_id", UUID, nullable=True),
        sa.Column("destination_binding_fingerprint", sa.String(64), nullable=True),
        sa.Column("market_policy_hash", sa.String(64), nullable=True),
        sa.Column("approved_package_hash", sa.String(64), nullable=True),
    )
    for column in confirmation_columns:
        op.add_column("manual_publish_confirmations", column)

    uploaded_columns = (
        sa.Column("destination_binding_id", UUID, nullable=True),
        sa.Column("destination_binding_fingerprint", sa.String(64), nullable=True),
        sa.Column("market_policy_hash", sa.String(64), nullable=True),
        sa.Column("approved_package_hash", sa.String(64), nullable=True),
    )
    for column in uploaded_columns:
        op.add_column("uploaded_videos", column)

    op.create_index(
        "ix_publish_handoff_destination_binding_id",
        "publish_handoff_packages",
        ["destination_binding_id"],
    )
    op.create_index(
        "ix_manual_publish_destination_binding_id",
        "manual_publish_confirmations",
        ["destination_binding_id"],
    )
    op.create_index(
        "ix_uploaded_videos_destination_binding_id",
        "uploaded_videos",
        ["destination_binding_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_uploaded_videos_destination_binding_id", table_name="uploaded_videos"
    )
    op.drop_index(
        "ix_manual_publish_destination_binding_id",
        table_name="manual_publish_confirmations",
    )
    op.drop_index(
        "ix_publish_handoff_destination_binding_id",
        table_name="publish_handoff_packages",
    )

    for column_name in (
        "approved_package_hash",
        "market_policy_hash",
        "destination_binding_fingerprint",
        "destination_binding_id",
    ):
        op.drop_column("uploaded_videos", column_name)

    for column_name in (
        "approved_package_hash",
        "market_policy_hash",
        "destination_binding_fingerprint",
        "destination_binding_id",
    ):
        op.drop_column("manual_publish_confirmations", column_name)

    op.drop_constraint(
        "fk_publish_handoff_approval_decision_id",
        "publish_handoff_packages",
        type_="foreignkey",
    )
    for column_name in (
        "approved_publish_window",
        "approved_publish_timezone",
        "market_alignment_dossier_hash",
        "market_alignment_dossier_ref",
        "target_market_profile_hash",
        "target_market_profile_ref",
        "approval_decision_id",
        "approved_package_hash",
        "market_policy_hash",
        "destination_binding_fingerprint",
    ):
        op.drop_column("publish_handoff_packages", column_name)

    op.drop_constraint(
        "fk_approval_decisions_policy_snapshot_id",
        "approval_decisions",
        type_="foreignkey",
    )
    for column_name in (
        "approved_publish_window",
        "market_alignment_dossier_hash",
        "market_alignment_dossier_ref",
        "target_market_profile_hash",
        "target_market_profile_ref",
        "approved_package_hash",
        "market_policy_hash",
        "destination_binding_fingerprint",
        "destination_binding_id",
        "policy_snapshot_id",
    ):
        op.drop_column("approval_decisions", column_name)
