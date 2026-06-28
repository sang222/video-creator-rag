"""M12.2S full agent Ollama rehearsal boundary

Revision ID: 0022_m12_2s_full_rehearsal
Revises: 0021_m12_2r_handoff_ledger
Create Date: 2026-06-28 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0022_m12_2s_full_rehearsal"
down_revision: str | None = "0021_m12_2r_handoff_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
M12_2S_STATUS_CHECK = (
    "package_status in ("
    "'READY_FOR_HUMAN_REVIEW','REVIEW_REQUIRED','BLOCKED','NOT_CONFIGURED','ERROR',"
    "'READY_FOR_MEDIA_PROVIDERS','BLOCKED_PROVIDER_NOT_CONFIGURED'"
    ")"
)
M12_2_STATUS_CHECK = "package_status in ('READY_FOR_HUMAN_REVIEW','REVIEW_REQUIRED','BLOCKED','NOT_CONFIGURED','ERROR')"
BOUNDARY_STATUS_CHECK = (
    "boundary_status in ("
    "'READY_FOR_MEDIA_PROVIDERS','BLOCKED_PROVIDER_NOT_CONFIGURED','REVIEW_REQUIRED','BLOCKED_GATEKEEPER'"
    ")"
)


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _drop_package_status_checks() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            FOR constraint_name IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'first_scripted_video_packages'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%package_status%'
            LOOP
                EXECUTE format('ALTER TABLE first_scripted_video_packages DROP CONSTRAINT %I', constraint_name);
            END LOOP;
        END $$;
        """
    )


def upgrade() -> None:
    _drop_package_status_checks()
    op.execute(
        "ALTER TABLE first_scripted_video_packages "
        "ADD CONSTRAINT ck_first_scripted_video_packages_ck_first_scripted_video_packages_status "
        f"CHECK ({M12_2S_STATUS_CHECK})"
    )

    op.create_table(
        "video_generation_boundaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("required_inputs", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("required_providers", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("provider_readiness", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("boundary_status", sa.String(length=60), nullable=False),
        sa.Column("blocked_reasons", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=False),
        sa.Column("operator_summary_vi", sa.Text(), nullable=False),
        sa.Column("no_provider_calls_confirmed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(BOUNDARY_STATUS_CHECK, name="ck_video_generation_boundaries_status"),
        sa.CheckConstraint("jsonb_typeof(required_inputs) = 'object'", name="ck_video_generation_boundaries_required_inputs_object"),
        sa.CheckConstraint("jsonb_typeof(required_providers) = 'array'", name="ck_video_generation_boundaries_required_providers_array"),
        sa.CheckConstraint("jsonb_typeof(provider_readiness) = 'object'", name="ck_video_generation_boundaries_provider_readiness_object"),
        sa.CheckConstraint("jsonb_typeof(blocked_reasons) = 'array'", name="ck_video_generation_boundaries_blocked_reasons_array"),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_video_generation_boundaries_package", "video_generation_boundaries", ["package_id"])
    op.create_index("ix_video_generation_boundaries_channel", "video_generation_boundaries", ["channel_id"])
    op.create_index("ix_video_generation_boundaries_status", "video_generation_boundaries", ["boundary_status"])
    op.create_index("ix_video_generation_boundaries_created_at", "video_generation_boundaries", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_video_generation_boundaries_created_at", table_name="video_generation_boundaries")
    op.drop_index("ix_video_generation_boundaries_status", table_name="video_generation_boundaries")
    op.drop_index("ix_video_generation_boundaries_channel", table_name="video_generation_boundaries")
    op.drop_index("ix_video_generation_boundaries_package", table_name="video_generation_boundaries")
    op.drop_table("video_generation_boundaries")

    _drop_package_status_checks()
    op.execute(
        "ALTER TABLE first_scripted_video_packages "
        "ADD CONSTRAINT ck_first_scripted_video_packages_ck_first_scripted_video_packages_status "
        f"CHECK ({M12_2_STATUS_CHECK})"
    )
