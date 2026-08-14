"""Persist immutable V2 combined replacement budget authorities.

Revision ID: 0083_combined_budget_authority
Revises: 0082_segment_replay_seed
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0083_combined_budget_authority"
down_revision: str | None = "0082_segment_replay_seed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    money = sa.Numeric(18, 6)
    op.create_table(
        "combined_replacement_budget_authorities",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("authority_ref", sa.Text(), nullable=False),
        sa.Column(
            "video_project_id", uuid, sa.ForeignKey("video_projects.id"), nullable=False
        ),
        sa.Column(
            "budget_reservation_id",
            uuid,
            sa.ForeignKey("mr1_monthly_budget_reservations.id"),
            nullable=False,
        ),
        sa.Column("budget_reservation_ref", sa.Text(), nullable=False),
        sa.Column("support_envelope_hash", sa.String(64), nullable=False),
        sa.Column("route_budget_authority_hash", sa.String(64), nullable=False),
        sa.Column(
            "tts_performance_projection_id",
            uuid,
            sa.ForeignKey("tts_performance_projections.id"),
            nullable=False,
        ),
        sa.Column("tts_performance_projection_hash", sa.String(64), nullable=False),
        sa.Column(
            "source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("new_tts_projected_cost_usd", money, nullable=False),
        sa.Column("forced_alignment_projected_cost_usd", money, nullable=False),
        sa.Column("ai_image_projected_cost_usd", money, nullable=False),
        sa.Column("ai_video_projected_cost_usd", money, nullable=False),
        sa.Column("other_metered_effects_projected_cost_usd", money, nullable=False),
        sa.Column("combined_replacement_projected_cost_usd", money, nullable=False),
        sa.Column("approved_ceiling_usd", money, nullable=False),
        sa.Column("shortfall_usd", money, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("authority_ref", name="uq_combined_replacement_budget_ref"),
        sa.UniqueConstraint(
            "video_project_id",
            "support_envelope_hash",
            "tts_performance_projection_hash",
            "content_hash",
            name="uq_combined_replacement_budget_identity",
        ),
        sa.CheckConstraint(
            "state = 'FROZEN'", name="ck_combined_replacement_budget_state"
        ),
        sa.CheckConstraint(
            "new_tts_projected_cost_usd >= 0 and "
            "forced_alignment_projected_cost_usd >= 0 and "
            "ai_image_projected_cost_usd >= 0 and "
            "ai_video_projected_cost_usd >= 0 and "
            "other_metered_effects_projected_cost_usd >= 0 and "
            "combined_replacement_projected_cost_usd >= 0 and "
            "approved_ceiling_usd >= 0 and shortfall_usd >= 0",
            name="ck_combined_replacement_budget_nonnegative",
        ),
        sa.CheckConstraint(
            "support_envelope_hash ~ '^[0-9a-f]{64}$' and "
            "route_budget_authority_hash ~ '^[0-9a-f]{64}$' and "
            "tts_performance_projection_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_combined_replacement_budget_hashes",
        ),
    )
    op.create_index(
        "ix_combined_replacement_budget_project_created",
        "combined_replacement_budget_authorities",
        ["video_project_id", "created_at"],
    )
    # This is a cost-input witness, not a mutable settlement row.  MR1 owns
    # settlement separately; a new preflight requires a new authority row.
    op.execute(
        """
        CREATE FUNCTION vcos_reject_combined_replacement_budget_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'combined replacement budget authority is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_combined_replacement_budget_append_only
        BEFORE UPDATE OR DELETE ON combined_replacement_budget_authorities
        FOR EACH ROW EXECUTE FUNCTION
        vcos_reject_combined_replacement_budget_mutation();
        """
    )


def downgrade() -> None:
    raise RuntimeError("0083 is forward-only; budget authority history is immutable")
