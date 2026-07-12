"""Remove obsolete AI-video readiness data without relabeling history.

Revision ID: 0036_hpr1_veo
Revises: 0035_cr_remove
Create Date: 2026-07-12 00:00:00

Provider readiness rows and snapshots are ephemeral read-model evidence. Existing
generation, cost, approval, idempotency and paid-call records are not rewritten.
"""
from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = "0036_hpr1_veo"
down_revision: str | None = "0035_cr_remove"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    obsolete_keys = {
        str(row[0])
        for row in connection.execute(
            sa.text(
                "SELECT DISTINCT provider_key FROM provider_readiness_checks "
                "WHERE provider_type = 'AI_VIDEO_HERO_PROVIDER' AND provider_key <> 'google_veo'"
            )
        )
    }
    if obsolete_keys:
        rows = connection.execute(
            sa.text(
                "SELECT id, provider_summaries, blocking_items, warning_items, next_actions "
                "FROM provider_readiness_snapshots"
            )
        )
        for row in rows:
            sanitized = [
                [item for item in list(value or []) if not isinstance(item, dict) or item.get("provider_key") not in obsolete_keys]
                for value in row[1:]
            ]
            connection.execute(
                sa.text(
                    "UPDATE provider_readiness_snapshots SET "
                    "provider_summaries=CAST(:summaries AS jsonb), "
                    "blocking_items=CAST(:blocking AS jsonb), "
                    "warning_items=CAST(:warning AS jsonb), "
                    "next_actions=CAST(:actions AS jsonb) WHERE id=:id"
                ),
                {
                    "id": row[0],
                    "summaries": json.dumps(sanitized[0]),
                    "blocking": json.dumps(sanitized[1]),
                    "warning": json.dumps(sanitized[2]),
                    "actions": json.dumps(sanitized[3]),
                },
            )
    connection.execute(
        sa.text(
            "DELETE FROM provider_readiness_checks "
            "WHERE provider_type = 'AI_VIDEO_HERO_PROVIDER' AND provider_key <> 'google_veo'"
        )
    )


def downgrade() -> None:
    raise RuntimeError("0036 is an irreversible provider-readiness data cleanup")
