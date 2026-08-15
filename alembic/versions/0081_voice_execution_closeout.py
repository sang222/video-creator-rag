"""Add durable, per-segment narration provider effects.

Revision ID: 0081_voice_execution_closeout
Revises: 0080_voice_authority
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0081_voice_execution_closeout"
down_revision: str | None = "0080_voice_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "narration_segment_executions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "video_project_id", uuid, sa.ForeignKey("video_projects.id"), nullable=False
        ),
        sa.Column(
            "narration_voice_snapshot_id",
            uuid,
            sa.ForeignKey("narration_voice_snapshots.id"),
            nullable=False,
        ),
        sa.Column("narration_voice_snapshot_hash", sa.String(64), nullable=False),
        sa.Column(
            "narration_performance_plan_id",
            uuid,
            sa.ForeignKey("narration_performance_plans.id"),
            nullable=False,
        ),
        sa.Column("narration_performance_plan_hash", sa.String(64), nullable=False),
        sa.Column(
            "tts_performance_projection_id",
            uuid,
            sa.ForeignKey("tts_performance_projections.id"),
            nullable=False,
        ),
        sa.Column("tts_performance_projection_hash", sa.String(64), nullable=False),
        sa.Column("segment_id", sa.String(120), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("canonical_text_hash", sa.String(64), nullable=False),
        sa.Column("provider_projection_hash", sa.String(64), nullable=False),
        sa.Column("provider_effect_key", sa.String(160), nullable=False),
        sa.Column("voice_id", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("compiled_voice_settings", jsonb, nullable=False),
        sa.Column("provider_context", jsonb, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("provider_request_hash", sa.String(64)),
        sa.Column("provider_request_id", sa.String(160)),
        sa.Column("audio_ref", sa.Text()),
        sa.Column("audio_checksum", sa.String(64)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("estimated_cost_usd", sa.String(40)),
        sa.Column("actual_cost_usd", sa.String(40)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "outcome_certainty",
            sa.String(24),
            nullable=False,
            server_default="NOT_SENT",
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "video_project_id",
            "provider_effect_key",
            name="uq_narration_segment_effect_key",
        ),
        sa.UniqueConstraint(
            "tts_performance_projection_id",
            "segment_index",
            name="uq_narration_segment_projection_index",
        ),
        sa.CheckConstraint("segment_index >= 0", name="ck_narration_segment_index"),
        sa.CheckConstraint(
            "attempt_count between 0 and 1", name="ck_narration_segment_attempt_count"
        ),
        sa.CheckConstraint(
            "state in ('INTENDED','SUBMITTED','VERIFIED','PROVIDER_OUTCOME_UNKNOWN','FAILED')",
            name="ck_narration_segment_state",
        ),
        sa.CheckConstraint(
            "outcome_certainty in ('NOT_SENT','SUBMITTED','VERIFIED','UNKNOWN','FAILED')",
            name="ck_narration_segment_outcome_certainty",
        ),
        sa.CheckConstraint(
            "narration_voice_snapshot_hash ~ '^[0-9a-f]{64}$' and narration_performance_plan_hash ~ '^[0-9a-f]{64}$' and tts_performance_projection_hash ~ '^[0-9a-f]{64}$' and canonical_text_hash ~ '^[0-9a-f]{64}$' and provider_projection_hash ~ '^[0-9a-f]{64}$' and content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_narration_segment_hashes",
        ),
    )
    op.create_index(
        "ix_narration_segment_project_state",
        "narration_segment_executions",
        ["video_project_id", "state"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "0081 is forward-only; narration paid-effect evidence is immutable"
    )
