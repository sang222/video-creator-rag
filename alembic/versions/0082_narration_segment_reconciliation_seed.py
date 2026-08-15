"""Persist final timing evidence required for segment replay reconciliation.

Revision ID: 0082_segment_replay_seed
Revises: 0081_voice_execution_closeout
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0082_segment_replay_seed"
down_revision: str | None = "0081_voice_execution_closeout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing historical rows remain immutable; absent evidence is deliberately
    # unreconcilable rather than backfilled or guessed.
    op.add_column(
        "narration_segment_executions",
        sa.Column("timing_seed", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.create_check_constraint(
        "ck_narration_segment_verified_evidence",
        "narration_segment_executions",
        "(state <> 'VERIFIED') or (provider_request_hash is not null and "
        "provider_request_id is not null and audio_ref is not null and "
        "audio_checksum is not null and duration_ms > 0 and timing_seed is not null)",
    )


def downgrade() -> None:
    raise RuntimeError("0082 is forward-only; replay evidence is immutable")
