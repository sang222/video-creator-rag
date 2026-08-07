"""Restore the human editorial-pause lifecycle action.

Revision ID: 0069_lifecycle_pause
Revises: 0068_cross_modal_qc_projection
Create Date: 2026-08-07
"""

from alembic import op


revision = "0069_lifecycle_pause"
down_revision = "0068_cross_modal_qc_projection"
branch_labels = None
depends_on = None


_ACTIONS_WITH_PAUSE = (
    "action in ('KEEP_ACTIVE','PAUSE_EDITORIAL_RESEARCH',"
    "'CONTINUE_OBSERVING','ADD_MANUAL_NOTE','DEACTIVATE_CHANNEL',"
    "'ARCHIVE_CHANNEL','REACTIVATE_CHANNEL')"
)
_ACTIONS_WITHOUT_PAUSE = (
    "action in ('KEEP_ACTIVE','CONTINUE_OBSERVING','ADD_MANUAL_NOTE',"
    "'DEACTIVATE_CHANNEL','ARCHIVE_CHANNEL','REACTIVATE_CHANNEL')"
)


def _replace_check(expression: str) -> None:
    op.drop_constraint(
        "ck_channel_lifecycle_action",
        "channel_lifecycle_decisions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_channel_lifecycle_action",
        "channel_lifecycle_decisions",
        expression,
    )


def upgrade() -> None:
    _replace_check(_ACTIONS_WITH_PAUSE)


def downgrade() -> None:
    _replace_check(_ACTIONS_WITHOUT_PAUSE)
