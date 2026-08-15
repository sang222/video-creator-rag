"""add confidence-aware and exactly-once learning authority

Revision ID: 0086_learning_authority
Revises: 0085_series_authority
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0086_learning_authority"
down_revision = "0085_series_authority"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "analytics_evidence_windows",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("uploaded_video_id", UUID, nullable=False),
        sa.Column("window_key", sa.String(length=16), nullable=False),
        sa.Column("source_version", sa.String(length=80), nullable=False),
        sa.Column("maturity_state", sa.String(length=24), nullable=False),
        sa.Column("confidence_state", sa.String(length=24), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Integer(), nullable=True),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("source_snapshot_refs", JSONB, nullable=False),
        sa.Column("evidence_payload", JSONB, nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("matured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "window_key IN ('H24','H72','D7','D30','M11')",
            name="ck_analytics_evidence_window_key",
        ),
        sa.CheckConstraint(
            "maturity_state IN ('TOO_EARLY','MATURE','STALE','INCOMPLETE')",
            name="ck_analytics_evidence_maturity",
        ),
        sa.CheckConstraint(
            "confidence_state IN ('TOO_EARLY','WEAK_SIGNAL','DIRECTIONAL','STABLE','ACTION_READY')",
            name="ck_analytics_evidence_confidence",
        ),
        sa.CheckConstraint("sample_size >= 0", name="ck_analytics_sample_nonnegative"),
        sa.UniqueConstraint(
            "uploaded_video_id",
            "window_key",
            "source_version",
            name="uq_analytics_evidence_window",
        ),
    )
    op.create_index(
        "ix_analytics_evidence_channel",
        "analytics_evidence_windows",
        ["channel_workspace_id"],
    )
    op.create_index(
        "ix_analytics_evidence_video",
        "analytics_evidence_windows",
        ["uploaded_video_id"],
    )
    op.create_index(
        "ix_analytics_evidence_hash",
        "analytics_evidence_windows",
        ["evidence_hash"],
    )

    op.create_table(
        "learning_equivalence_fingerprints",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("source_entity_ref", sa.String(length=260), nullable=False),
        sa.Column("content_product_type", sa.String(length=80), nullable=False),
        sa.Column("series_plan_id", UUID, nullable=True),
        sa.Column("profile_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("target_market", sa.String(length=32), nullable=False),
        sa.Column("content_language", sa.String(length=24), nullable=False),
        sa.Column("format_key", sa.String(length=120), nullable=False),
        sa.Column("normalized_features", JSONB, nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "source_entity_ref",
            name="uq_learning_fingerprint_source",
        ),
    )
    op.create_index(
        "ix_learning_fingerprint_channel",
        "learning_equivalence_fingerprints",
        ["channel_workspace_id"],
    )
    op.create_index(
        "ix_learning_fingerprint_hash",
        "learning_equivalence_fingerprints",
        ["fingerprint"],
    )
    op.create_index(
        "ix_learning_fingerprint_series",
        "learning_equivalence_fingerprints",
        ["series_plan_id"],
    )

    op.create_table(
        "learning_reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("fingerprint_id", UUID, nullable=False),
        sa.Column("analytics_evidence_window_id", UUID, nullable=False),
        sa.Column("window_key", sa.String(length=16), nullable=False),
        sa.Column("command_id", UUID, nullable=False),
        sa.Column("current_policy_hash", sa.String(length=64), nullable=False),
        sa.Column("comparable_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("reason_codes", JSONB, nullable=False),
        sa.Column("audit_trail", JSONB, nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by", UUID, nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('BLOCKED','ELIGIBLE','PROMOTED','REJECTED','SUPERSEDED')",
            name="ck_learning_review_decision",
        ),
        sa.CheckConstraint("comparable_count >= 0", name="ck_learning_comparable_count"),
        sa.UniqueConstraint("command_id", name="uq_learning_review_command"),
        sa.UniqueConstraint(
            "fingerprint_id",
            "window_key",
            "evidence_hash",
            name="uq_learning_review_evidence",
        ),
    )
    op.create_index("ix_learning_review_channel", "learning_reviews", ["channel_workspace_id"])
    op.create_index("ix_learning_review_fingerprint", "learning_reviews", ["fingerprint_id"])
    op.create_index("ix_learning_review_decision", "learning_reviews", ["decision"])

    op.create_table(
        "audience_delivery_plans",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=False),
        sa.Column("publication_receipt_id", UUID, nullable=False),
        sa.Column("target_markets", JSONB, nullable=False),
        sa.Column("target_languages", JSONB, nullable=False),
        sa.Column("packaging_refs", JSONB, nullable=False),
        sa.Column("playlist_refs", JSONB, nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('ELIGIBLE','ACTIVATED','PAUSED','SUPERSEDED')",
            name="ck_audience_delivery_state",
        ),
        sa.UniqueConstraint(
            "publication_receipt_id", name="uq_audience_delivery_publication"
        ),
    )
    op.create_index(
        "ix_audience_delivery_channel", "audience_delivery_plans", ["channel_workspace_id"]
    )
    op.create_index(
        "ix_audience_delivery_project", "audience_delivery_plans", ["video_project_id"]
    )

    op.create_table(
        "learning_operational_incidents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=True),
        sa.Column("incident_type", sa.String(length=48), nullable=False),
        sa.Column("external_ref", sa.String(length=240), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("blocks_learning", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("evidence_payload", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "incident_type IN ('NO_VIEW_CANARY','POLICY_DRIFT','ANALYTICS_DRIFT','LIVE_PROOF')",
            name="ck_learning_operational_incident_type",
        ),
        sa.CheckConstraint(
            "severity IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="ck_learning_operational_severity",
        ),
        sa.CheckConstraint(
            "state IN ('OPEN','RESOLVED','SUPERSEDED')",
            name="ck_learning_operational_state",
        ),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "incident_type",
            "external_ref",
            name="uq_learning_operational_incident",
        ),
    )
    op.create_index(
        "ix_learning_operational_channel",
        "learning_operational_incidents",
        ["channel_workspace_id"],
    )
    op.create_index(
        "ix_learning_operational_state",
        "learning_operational_incidents",
        ["state"],
    )


def downgrade() -> None:
    op.drop_table("learning_operational_incidents")
    op.drop_table("audience_delivery_plans")
    op.drop_table("learning_reviews")
    op.drop_table("learning_equivalence_fingerprints")
    op.drop_table("analytics_evidence_windows")
