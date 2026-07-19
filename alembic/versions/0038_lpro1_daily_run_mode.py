"""Allow the declared REAL daily run mode without weakening runtime gates.

Revision ID: 0038_lpro1_daily_mode
Revises: 0037_ch1_flex
Create Date: 2026-07-19 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0038_lpro1_daily_mode"
down_revision: str | None = "0037_ch1_flex"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_channel_daily_runs_run_mode",
        "channel_daily_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_channel_daily_runs_run_mode",
        "channel_daily_runs",
        "run_mode in ('MOCK','REAL_DISABLED','REAL')",
    )
    op.drop_constraint(
        "ck_upload_cards_state",
        "upload_cards",
        type_="check",
    )
    op.create_check_constraint(
        "ck_upload_cards_state",
        "upload_cards",
        "card_state in ('DRAFT','READY','UPLOAD_INPUT_MISSING','AWAITING_FINAL_MEDIA','BLOCKED','USED','CANCELLED')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    invalid = bind.execute(
        sa.text("select count(*) from channel_daily_runs where run_mode = 'REAL'")
    ).scalar_one()
    if invalid:
        raise RuntimeError("DOWNGRADE_BLOCKED_REAL_CHANNEL_DAILY_RUNS_EXIST")
    new_upload_states = bind.execute(
        sa.text(
            "select count(*) from upload_cards "
            "where card_state in ('UPLOAD_INPUT_MISSING','AWAITING_FINAL_MEDIA')"
        )
    ).scalar_one()
    if new_upload_states:
        raise RuntimeError("DOWNGRADE_BLOCKED_LPRO1_UPLOAD_CARD_STATES_EXIST")
    op.drop_constraint(
        "ck_upload_cards_state",
        "upload_cards",
        type_="check",
    )
    op.create_check_constraint(
        "ck_upload_cards_state",
        "upload_cards",
        "card_state in ('DRAFT','READY','BLOCKED','USED','CANCELLED')",
    )
    op.drop_constraint(
        "ck_channel_daily_runs_run_mode",
        "channel_daily_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_channel_daily_runs_run_mode",
        "channel_daily_runs",
        "run_mode in ('MOCK','REAL_DISABLED')",
    )
