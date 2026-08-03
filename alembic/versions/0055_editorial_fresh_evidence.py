"""Allow hash-bound official documents in durable M5 evidence rows.

Revision ID: 0055_editorial_fresh_evidence
Revises: 0054_vcos_stale_recovery
Create Date: 2026-08-03 22:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0055_editorial_fresh_evidence"
down_revision: str | None = "0054_vcos_stale_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT = "ck_search_demand_evidence_source_type"
_VALUES = (
    "'OFFICIAL_DOCUMENT','OFFICIAL_MANUAL','PAID_TOOL_CSV',"
    "'GOOGLE_TRENDS_CSV','YOUTUBE_ANALYTICS',"
    "'TIKTOK_CREATOR_SEARCH_INSIGHTS_MANUAL','INTERNAL_ANALYTICS',"
    "'MOCK','MANUAL_RESEARCH'"
)
_PREVIOUS_VALUES = _VALUES.replace("'OFFICIAL_DOCUMENT',", "")


def _replace_constraint(values: str) -> None:
    op.execute(
        """
        DO $$
        DECLARE constraint_name text;
        BEGIN
            FOR constraint_name IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'search_demand_evidence'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) ILIKE '%evidence_source_type%'
            LOOP
                EXECUTE format(
                    'ALTER TABLE search_demand_evidence DROP CONSTRAINT %I',
                    constraint_name
                );
            END LOOP;
        END
        $$;
        """
    )
    op.execute(
        "ALTER TABLE search_demand_evidence ADD CONSTRAINT "
        f"{_CONSTRAINT} CHECK (evidence_source_type in ({values}))"
    )


def upgrade() -> None:
    _replace_constraint(_VALUES)


def downgrade() -> None:
    if op.get_bind().execute(
        sa.text(
            "select exists (select 1 from search_demand_evidence "
            "where evidence_source_type = 'OFFICIAL_DOCUMENT')"
        )
    ).scalar():
        raise RuntimeError(
            "0055 downgrade refused: OFFICIAL_DOCUMENT evidence rows exist"
        )
    _replace_constraint(_PREVIOUS_VALUES)
