"""Add durable series episode reservation authority.

Revision ID: 0060_series_episode_reservations
Revises: 0059_script_qual_auth
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0060_series_episode_reservations"
down_revision = "0059_script_qual_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "series_episode_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "script_qualification_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("script_qualification_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "series_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("series_plans.id"),
            nullable=False,
        ),
        sa.Column(
            "series_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("series_runs.id"),
            nullable=False,
        ),
        sa.Column("episode_number", sa.Integer(), nullable=False),
        sa.Column("episode_role", sa.String(length=120), nullable=False),
        sa.Column("episode_delta", sa.Text(), nullable=False),
        sa.Column("assignment_resolution_hash", sa.String(length=64), nullable=False),
        sa.Column("reservation_authority_version", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="RESERVED"),
        sa.Column("released_reason_code", sa.String(length=160), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consumed_admission_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_admission_decisions.id"),
            nullable=True,
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("script_qualification_run_id", name="uq_series_episode_reservations_qualification"),
        sa.UniqueConstraint("series_run_id", "episode_number", name="uq_series_episode_reservations_run_episode"),
        sa.CheckConstraint("state in ('RESERVED','RELEASED','CONSUMED')", name="ck_series_episode_reservations_state"),
        sa.CheckConstraint("episode_number > 0", name="ck_series_episode_reservations_episode_number"),
        sa.CheckConstraint("assignment_resolution_hash ~ '^[0-9a-f]{64}$'", name="ck_series_episode_reservations_resolution_hash"),
        sa.CheckConstraint(
            "(state = 'RESERVED' and released_at is null and consumed_at is null and consumed_admission_decision_id is null) "
            "or (state = 'RELEASED' and released_at is not null and consumed_at is null and consumed_admission_decision_id is null) "
            "or (state = 'CONSUMED' and consumed_at is not null and consumed_admission_decision_id is not null)",
            name="ck_series_episode_reservations_lifecycle",
        ),
    )
    op.create_index("ix_series_episode_reservations_series_run", "series_episode_reservations", ["series_run_id"])
    op.create_index("ix_series_episode_reservations_qualification", "series_episode_reservations", ["script_qualification_run_id"])


def downgrade() -> None:
    op.drop_index("ix_series_episode_reservations_qualification", table_name="series_episode_reservations")
    op.drop_index("ix_series_episode_reservations_series_run", table_name="series_episode_reservations")
    op.drop_table("series_episode_reservations")
