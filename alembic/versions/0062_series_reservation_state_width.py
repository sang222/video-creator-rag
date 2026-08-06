"""Repair the reservation state width on databases that already ran 0061.

Revision ID: 0062_series_reservation_width
Revises: 0061_qualification_recovery
"""

from alembic import op
import sqlalchemy as sa


revision = "0062_series_reservation_width"
down_revision = "0061_qualification_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    column = next(
        item
        for item in sa.inspect(op.get_bind()).get_columns(
            "series_episode_reservations"
        )
        if item["name"] == "state"
    )
    if getattr(column["type"], "length", None) is not None and column[
        "type"
    ].length < 48:
        op.alter_column(
            "series_episode_reservations",
            "state",
            existing_type=sa.String(length=24),
            type_=sa.String(length=48),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Revision 0061 owns the 48-character type for its lifecycle state.  Keep
    # this repair migration a no-op on downgrade so 0061 can reverse it once.
    pass
