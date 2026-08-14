"""Add market-aware voice casting and narration-performance authority.

Revision ID: 0080_voice_authority
Revises: 0079_ai_visual
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0080_voice_authority"
down_revision: str | None = "0079_ai_visual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    _create_voice_market_research()
    _create_provider_catalogs()
    _create_approved_pools()
    _create_casting_decisions_without_series_fk()
    _create_series_bindings()
    op.create_foreign_key(
        "fk_voice_casting_series_narrator_binding",
        "voice_casting_decisions",
        "series_narrator_bindings",
        ["series_narrator_binding_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    _create_voice_snapshots()
    _create_performance_plans()
    _create_tts_projections()


def downgrade() -> None:
    raise RuntimeError(
        "0080 voice authority is forward-only; deleting frozen casting or "
        "narration-performance lineage is prohibited"
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        )
    ]


def _create_voice_market_research() -> None:
    op.create_table(
        "voice_market_research_artifacts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "channel_workspace_id",
            UUID,
            sa.ForeignKey("channel_workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "channel_profile_version_id",
            UUID,
            sa.ForeignKey("channel_profile_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "policy_snapshot_id",
            UUID,
            sa.ForeignKey("compiled_channel_policy_snapshots.id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("market_identity", JSONB, nullable=False),
        sa.Column("requirements", JSONB, nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("confidence_label", sa.String(16), nullable=False),
        sa.Column("limitations", JSONB, nullable=False, server_default="[]"),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id")),
        *_timestamps(),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "channel_profile_version_id",
            "policy_snapshot_id",
            "content_hash",
            name="uq_voice_market_research_identity",
        ),
        sa.CheckConstraint(
            "confidence_label in ('LOW','MEDIUM','HIGH')",
            name="ck_voice_market_research_confidence",
        ),
        sa.CheckConstraint(
            "state in ('APPROVED','SUPERSEDED','REJECTED')",
            name="ck_voice_market_research_state",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_voice_market_research_hash",
        ),
    )
    op.create_index(
        "ix_voice_market_research_channel",
        "voice_market_research_artifacts",
        ["channel_workspace_id", "created_at"],
    )


def _create_provider_catalogs() -> None:
    op.create_table(
        "voice_provider_catalog_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "channel_workspace_id",
            UUID,
            sa.ForeignKey("channel_workspaces.id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("catalog_version", sa.String(120), nullable=False),
        sa.Column("voices", JSONB, nullable=False),
        sa.Column("source_refs", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id")),
        *_timestamps(),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "provider",
            "catalog_version",
            "content_hash",
            name="uq_voice_provider_catalog_identity",
        ),
        sa.CheckConstraint(
            "provider = 'elevenlabs'", name="ck_voice_provider_catalog_provider"
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_voice_provider_catalog_hash",
        ),
    )
    op.create_index(
        "ix_voice_provider_catalog_channel",
        "voice_provider_catalog_snapshots",
        ["channel_workspace_id", "created_at"],
    )


def _create_approved_pools() -> None:
    op.create_table(
        "approved_voice_pools",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "channel_workspace_id",
            UUID,
            sa.ForeignKey("channel_workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "channel_profile_version_id",
            UUID,
            sa.ForeignKey("channel_profile_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "policy_snapshot_id",
            UUID,
            sa.ForeignKey("compiled_channel_policy_snapshots.id"),
            nullable=False,
        ),
        sa.Column(
            "voice_market_research_id",
            UUID,
            sa.ForeignKey("voice_market_research_artifacts.id"),
            nullable=False,
        ),
        sa.Column(
            "provider_catalog_snapshot_id",
            UUID,
            sa.ForeignKey("voice_provider_catalog_snapshots.id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("voices", JSONB, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("approved_by_user_id", UUID, sa.ForeignKey("users.id")),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "channel_workspace_id", "version", name="uq_approved_voice_pool_version"
        ),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "channel_profile_version_id",
            "policy_snapshot_id",
            "content_hash",
            name="uq_approved_voice_pool_identity",
        ),
        sa.CheckConstraint("version > 0", name="ck_approved_voice_pool_version"),
        sa.CheckConstraint(
            "status in ('APPROVED','SUPERSEDED')",
            name="ck_approved_voice_pool_status",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_approved_voice_pool_hash",
        ),
    )
    op.create_index(
        "ix_approved_voice_pool_channel_status",
        "approved_voice_pools",
        ["channel_workspace_id", "status"],
    )


def _create_casting_decisions_without_series_fk() -> None:
    op.create_table(
        "voice_casting_decisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "video_project_id",
            UUID,
            sa.ForeignKey("video_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "approved_voice_pool_id",
            UUID,
            sa.ForeignKey("approved_voice_pools.id"),
            nullable=False,
        ),
        sa.Column("approved_voice_pool_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("qualified_script_ref", sa.Text(), nullable=False),
        sa.Column("qualified_script_hash", sa.String(64), nullable=False),
        sa.Column("narration_mode", sa.String(40), nullable=False),
        sa.Column("selected_voice_id", sa.Text(), nullable=False),
        sa.Column("selected_model_id", sa.Text(), nullable=False),
        sa.Column("baseline_delivery_profile", JSONB, nullable=False),
        sa.Column("selection_reason_codes", JSONB, nullable=False),
        sa.Column("market_fit_evidence_refs", JSONB, nullable=False),
        sa.Column("series_narrator_binding_id", UUID),
        sa.Column("casting_policy_version", sa.String(120), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id")),
        *_timestamps(),
        sa.UniqueConstraint(
            "video_project_id", "decision_version", name="uq_voice_casting_version"
        ),
        sa.UniqueConstraint(
            "video_project_id", "content_hash", name="uq_voice_casting_identity"
        ),
        sa.CheckConstraint("decision_version > 0", name="ck_voice_casting_version"),
        sa.CheckConstraint(
            "narration_mode in ('TECHNICAL_EXPLAINER','ANALYTICAL','TACTICAL',"
            "'STORY_CASE_STUDY','DOCUMENTARY','CAUTIONARY')",
            name="ck_voice_casting_mode",
        ),
        sa.CheckConstraint(
            "state in ('FROZEN','SUPERSEDED')", name="ck_voice_casting_state"
        ),
        sa.CheckConstraint(
            "approved_voice_pool_hash ~ '^[0-9a-f]{64}$' and "
            "qualified_script_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_voice_casting_hashes",
        ),
    )
    op.create_index(
        "ix_voice_casting_project_state",
        "voice_casting_decisions",
        ["video_project_id", "state"],
    )


def _create_series_bindings() -> None:
    op.create_table(
        "series_narrator_bindings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "series_plan_id", UUID, sa.ForeignKey("series_plans.id"), nullable=False
        ),
        sa.Column(
            "approved_voice_pool_id",
            UUID,
            sa.ForeignKey("approved_voice_pools.id"),
            nullable=False,
        ),
        sa.Column(
            "source_voice_casting_decision_id",
            UUID,
            sa.ForeignKey("voice_casting_decisions.id", deferrable=True, initially="DEFERRED"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("voice_id", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(120), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id")),
        *_timestamps(),
        sa.UniqueConstraint(
            "series_plan_id", "binding_version", name="uq_series_narrator_version"
        ),
        sa.UniqueConstraint(
            "series_plan_id", "content_hash", name="uq_series_narrator_identity"
        ),
        sa.CheckConstraint("binding_version > 0", name="ck_series_narrator_version"),
        sa.CheckConstraint(
            "state in ('ACTIVE','SUPERSEDED')", name="ck_series_narrator_state"
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_series_narrator_hash"
        ),
    )
    op.create_index(
        "ix_series_narrator_plan_state",
        "series_narrator_bindings",
        ["series_plan_id", "state"],
    )


def _create_voice_snapshots() -> None:
    op.create_table(
        "narration_voice_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "video_project_id",
            UUID,
            sa.ForeignKey("video_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "voice_casting_decision_id",
            UUID,
            sa.ForeignKey("voice_casting_decisions.id"),
            nullable=False,
        ),
        sa.Column(
            "approved_voice_pool_id",
            UUID,
            sa.ForeignKey("approved_voice_pools.id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("voice_id", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("baseline_voice_settings", JSONB, nullable=False),
        sa.Column("voice_catalog_version", sa.String(120), nullable=False),
        sa.Column("approved_voice_pool_version", sa.Integer(), nullable=False),
        sa.Column("market_identity_hash", sa.String(64), nullable=False),
        sa.Column("qualified_script_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "video_project_id",
            "content_hash",
            name="uq_narration_voice_snapshot_identity",
        ),
        sa.CheckConstraint("provider = 'elevenlabs'", name="ck_narration_voice_provider"),
        sa.CheckConstraint(
            "state in ('ACTIVE','SUPERSEDED')", name="ck_narration_voice_state"
        ),
        sa.CheckConstraint(
            "market_identity_hash ~ '^[0-9a-f]{64}$' and "
            "qualified_script_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_narration_voice_hashes",
        ),
    )
    op.create_index(
        "ix_narration_voice_project_state",
        "narration_voice_snapshots",
        ["video_project_id", "state"],
    )


def _create_performance_plans() -> None:
    op.create_table(
        "narration_performance_plans",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "video_project_id",
            UUID,
            sa.ForeignKey("video_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "narration_voice_snapshot_id",
            UUID,
            sa.ForeignKey("narration_voice_snapshots.id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("qualified_script_ref", sa.Text(), nullable=False),
        sa.Column("qualified_script_hash", sa.String(64), nullable=False),
        sa.Column("canonical_narration_hash", sa.String(64), nullable=False),
        sa.Column("voice_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("baseline_delivery", JSONB, nullable=False),
        sa.Column("beats", JSONB, nullable=False),
        sa.Column("performance_policy_version", sa.String(120), nullable=False),
        sa.Column("coverage_gate_state", sa.String(16), nullable=False),
        sa.Column("semantic_alignment_gate_state", sa.String(16), nullable=False),
        sa.Column("continuity_gate_state", sa.String(16), nullable=False),
        sa.Column("monotony_risk_gate_state", sa.String(16), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id")),
        *_timestamps(),
        sa.UniqueConstraint(
            "video_project_id",
            "content_hash",
            name="uq_narration_performance_identity",
        ),
        sa.CheckConstraint(
            "coverage_gate_state = 'PASS' and "
            "semantic_alignment_gate_state = 'PASS' and "
            "continuity_gate_state = 'PASS' and "
            "monotony_risk_gate_state = 'PASS'",
            name="ck_narration_performance_gates",
        ),
        sa.CheckConstraint(
            "state in ('FROZEN','SUPERSEDED')",
            name="ck_narration_performance_state",
        ),
        sa.CheckConstraint(
            "qualified_script_hash ~ '^[0-9a-f]{64}$' and "
            "canonical_narration_hash ~ '^[0-9a-f]{64}$' and "
            "voice_snapshot_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_narration_performance_hashes",
        ),
    )
    op.create_index(
        "ix_narration_performance_project",
        "narration_performance_plans",
        ["video_project_id", "created_at"],
    )


def _create_tts_projections() -> None:
    op.create_table(
        "tts_performance_projections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "video_project_id",
            UUID,
            sa.ForeignKey("video_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "narration_performance_plan_id",
            UUID,
            sa.ForeignKey("narration_performance_plans.id"),
            nullable=False,
        ),
        sa.Column(
            "narration_voice_snapshot_id",
            UUID,
            sa.ForeignKey("narration_voice_snapshots.id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("execution_strategy", sa.String(64), nullable=False),
        sa.Column("capability_profile_version", sa.String(120), nullable=False),
        sa.Column("segments", JSONB, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "narration_performance_plan_id",
            "content_hash",
            name="uq_tts_performance_projection_identity",
        ),
        sa.CheckConstraint("provider = 'elevenlabs'", name="ck_tts_projection_provider"),
        sa.CheckConstraint(
            "execution_strategy in ('SINGLE_REQUEST_EXPRESSIVE',"
            "'CONTEXT_STITCHED_MULTI_REQUEST','SEGMENTED_WITH_SEAM_QC')",
            name="ck_tts_projection_strategy",
        ),
        sa.CheckConstraint(
            "state in ('FROZEN','SUPERSEDED')", name="ck_tts_projection_state"
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_tts_projection_hash"
        ),
    )
    op.create_index(
        "ix_tts_performance_project",
        "tts_performance_projections",
        ["video_project_id", "created_at"],
    )
