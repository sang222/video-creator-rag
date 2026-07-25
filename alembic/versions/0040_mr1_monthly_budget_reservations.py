"""Add durable atomic MR1 monthly budget reservations.

Revision ID: 0040_mr1_budget
Revises: 0039_mr1_final_media
Create Date: 2026-07-19 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0040_mr1_budget"
down_revision: str | None = "0039_mr1_final_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "mr1_monthly_budget_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_ref", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "currency", sa.String(length=3), server_default="USD", nullable=False
        ),
        sa.Column("reserved_amount", sa.Numeric(18, 6), nullable=False),
        sa.Column(
            "provider_allocations_json",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column("environment_cap", sa.Numeric(18, 6), nullable=False),
        sa.Column("company_cap", sa.Numeric(18, 6), nullable=False),
        sa.Column("channel_cap", sa.Numeric(18, 6), nullable=False),
        sa.Column(
            "provider_caps_json",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("actual_amount", sa.Numeric(18, 6), nullable=True),
        sa.Column(
            "provider_actuals_json",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column("settlement_kind", sa.String(length=40), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "capacity_evidence_json",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=160), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('RESERVED','SUBMITTED','SETTLED_ACTUAL','SETTLED_CONSERVATIVE','RELEASED')",
            name="ck_mr1_monthly_budget_reservations_status",
        ),
        sa.CheckConstraint(
            "reserved_amount >= 0",
            name="ck_mr1_monthly_budget_reservations_reserved_nonnegative",
        ),
        sa.CheckConstraint(
            "actual_amount is null or actual_amount >= 0",
            name="ck_mr1_monthly_budget_reservations_actual_nonnegative",
        ),
        sa.CheckConstraint(
            "environment_cap >= 0",
            name="ck_mr1_monthly_budget_reservations_environment_cap_nonnegative",
        ),
        sa.CheckConstraint(
            "company_cap >= 0",
            name="ck_mr1_monthly_budget_reservations_company_cap_nonnegative",
        ),
        sa.CheckConstraint(
            "channel_cap >= 0",
            name="ck_mr1_monthly_budget_reservations_channel_cap_nonnegative",
        ),
        sa.CheckConstraint(
            "period_end > period_start",
            name="ck_mr1_monthly_budget_reservations_period_order",
        ),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reservation_ref", name="uq_mr1_budget_reservation_ref"),
        sa.UniqueConstraint("run_id", name="uq_mr1_budget_reservation_run_id"),
    )
    op.create_index(
        "ix_mr1_budget_reservations_project",
        "mr1_monthly_budget_reservations",
        ["video_project_id"],
    )
    op.create_index(
        "ix_mr1_budget_reservations_company_period",
        "mr1_monthly_budget_reservations",
        ["company_id", "period_start"],
    )
    op.create_index(
        "ix_mr1_budget_reservations_channel_period",
        "mr1_monthly_budget_reservations",
        ["channel_workspace_id", "period_start"],
    )
    op.create_index(
        "ix_mr1_budget_reservations_period_status",
        "mr1_monthly_budget_reservations",
        ["period_start", "status"],
    )
    op.create_index(
        "ix_mr1_budget_reservations_created_at",
        "mr1_monthly_budget_reservations",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mr1_budget_reservations_created_at",
        table_name="mr1_monthly_budget_reservations",
    )
    op.drop_index(
        "ix_mr1_budget_reservations_period_status",
        table_name="mr1_monthly_budget_reservations",
    )
    op.drop_index(
        "ix_mr1_budget_reservations_channel_period",
        table_name="mr1_monthly_budget_reservations",
    )
    op.drop_index(
        "ix_mr1_budget_reservations_company_period",
        table_name="mr1_monthly_budget_reservations",
    )
    op.drop_index(
        "ix_mr1_budget_reservations_project",
        table_name="mr1_monthly_budget_reservations",
    )
    op.drop_table("mr1_monthly_budget_reservations")
