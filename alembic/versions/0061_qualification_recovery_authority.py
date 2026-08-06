"""Add qualification recovery, settlement, and abandonment authorities.

Revision ID: 0061_qualification_recovery
Revises: 0060_series_episode_reservations
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0061_qualification_recovery"
down_revision = "0060_series_episode_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "script_qualification_runs",
        sa.Column("terminal_settlement_receipt", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "script_qualification_runs",
        sa.Column(
            "provider_outcome_reconciliation_receipts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column(
        "script_qualification_runs",
        "provider_outcome_reconciliation_receipts",
        server_default=None,
    )

    op.add_column(
        "series_episode_reservations",
        sa.Column("abandoned_reason_code", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "series_episode_reservations",
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "series_episode_reservations",
        "state",
        existing_type=sa.String(length=24),
        type_=sa.String(length=48),
        existing_nullable=False,
    )
    op.drop_constraint(
        "ck_series_episode_reservations_state",
        "series_episode_reservations",
        type_="check",
    )
    op.drop_constraint(
        "ck_series_episode_reservations_lifecycle",
        "series_episode_reservations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_series_episode_reservations_state",
        "series_episode_reservations",
        "state in ('RESERVED','RELEASED','CONSUMED','ABANDONED_AFTER_ADMISSION')",
    )
    op.create_check_constraint(
        "ck_series_episode_reservations_lifecycle",
        "series_episode_reservations",
        "(state = 'RESERVED' and released_at is null and consumed_at is null "
        "and consumed_admission_decision_id is null and abandoned_at is null) or "
        "(state = 'RELEASED' and released_at is not null and consumed_at is null "
        "and consumed_admission_decision_id is null and abandoned_at is null) or "
        "(state = 'CONSUMED' and consumed_at is not null "
        "and consumed_admission_decision_id is not null and abandoned_at is null) or "
        "(state = 'ABANDONED_AFTER_ADMISSION' and consumed_at is not null "
        "and consumed_admission_decision_id is not null and abandoned_at is not null)",
    )

    op.drop_constraint(
        "ck_long_form_publish_slots_state",
        "long_form_publish_slots",
        type_="check",
    )
    op.alter_column(
        "long_form_publish_slots",
        "state",
        existing_type=sa.String(length=24),
        type_=sa.String(length=48),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_long_form_publish_slots_state",
        "long_form_publish_slots",
        "state in ('OPEN','QUALIFICATION_RESERVED','QUALIFICATION_RECONCILIATION_REQUIRED',"
        "'RESERVED','FULFILLED','SKIPPED','CANCELED')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_long_form_publish_slots_state",
        "long_form_publish_slots",
        type_="check",
    )
    op.create_check_constraint(
        "ck_long_form_publish_slots_state",
        "long_form_publish_slots",
        "state in ('OPEN','QUALIFICATION_RESERVED','RESERVED','FULFILLED','SKIPPED','CANCELED')",
    )
    op.alter_column(
        "long_form_publish_slots",
        "state",
        existing_type=sa.String(length=48),
        type_=sa.String(length=24),
        existing_nullable=False,
    )

    op.drop_constraint(
        "ck_series_episode_reservations_lifecycle",
        "series_episode_reservations",
        type_="check",
    )
    op.drop_constraint(
        "ck_series_episode_reservations_state",
        "series_episode_reservations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_series_episode_reservations_state",
        "series_episode_reservations",
        "state in ('RESERVED','RELEASED','CONSUMED')",
    )
    op.create_check_constraint(
        "ck_series_episode_reservations_lifecycle",
        "series_episode_reservations",
        "(state = 'RESERVED' and released_at is null and consumed_at is null "
        "and consumed_admission_decision_id is null) or "
        "(state = 'RELEASED' and released_at is not null and consumed_at is null "
        "and consumed_admission_decision_id is null) or "
        "(state = 'CONSUMED' and consumed_at is not null "
        "and consumed_admission_decision_id is not null)",
    )
    op.alter_column(
        "series_episode_reservations",
        "state",
        existing_type=sa.String(length=48),
        type_=sa.String(length=24),
        existing_nullable=False,
    )
    op.drop_column("series_episode_reservations", "abandoned_at")
    op.drop_column("series_episode_reservations", "abandoned_reason_code")
    op.drop_column(
        "script_qualification_runs", "provider_outcome_reconciliation_receipts"
    )
    op.drop_column("script_qualification_runs", "terminal_settlement_receipt")
