"""add monetization, economics, enforcement, and commercial state

Revision ID: 0087_business_os
Revises: 0086_learning_authority
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0087_business_os"
down_revision = "0086_learning_authority"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
MONEY = sa.Numeric(20, 6)


def upgrade() -> None:
    op.create_table(
        "payment_profile_statuses",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("payee_ref", sa.String(length=200), nullable=False),
        sa.Column("tax_state", sa.String(length=32), nullable=False),
        sa.Column("address_verification_state", sa.String(length=32), nullable=False),
        sa.Column("payment_method_state", sa.String(length=32), nullable=False),
        sa.Column("payment_hold_state", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=260), nullable=False),
        sa.Column("confidence_state", sa.String(length=24), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "company_id", "version_number", name="uq_payment_profile_version"
        ),
    )
    op.create_index(
        "ix_payment_profile_company", "payment_profile_statuses", ["company_id"]
    )
    op.create_index(
        "ix_payment_profile_hash", "payment_profile_statuses", ["content_hash"]
    )

    op.create_table(
        "monetization_account_statuses",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("program_type", sa.String(length=80), nullable=False),
        sa.Column("eligibility_state", sa.String(length=32), nullable=False),
        sa.Column("enrollment_state", sa.String(length=32), nullable=False),
        sa.Column("restriction_state", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=260), nullable=False),
        sa.Column("confidence_state", sa.String(length=24), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "platform",
            "version_number",
            name="uq_monetization_account_version",
        ),
    )
    op.create_index(
        "ix_monetization_account_channel",
        "monetization_account_statuses",
        ["channel_workspace_id"],
    )
    op.create_index(
        "ix_monetization_account_company",
        "monetization_account_statuses",
        ["company_id"],
    )

    op.create_table(
        "revenue_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=True),
        sa.Column("source", sa.String(length=48), nullable=False),
        sa.Column("amount_state", sa.String(length=24), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ref", sa.String(length=260), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence_state", sa.String(length=24), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_state IN ('ESTIMATED','PENDING','LOCKED','FINALIZED','REVERSED','PAID')",
            name="ck_revenue_amount_state",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_revenue_amount_nonnegative"),
        sa.CheckConstraint("period_end > period_start", name="ck_revenue_period"),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "source",
            "period_start",
            "period_end",
            "amount_state",
            "source_ref",
            name="uq_revenue_snapshot_source",
        ),
    )
    op.create_index("ix_revenue_channel", "revenue_snapshots", ["channel_workspace_id"])
    op.create_index("ix_revenue_project", "revenue_snapshots", ["video_project_id"])
    op.create_index("ix_revenue_hash", "revenue_snapshots", ["content_hash"])

    op.create_table(
        "channel_pnl_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("estimated_revenue", MONEY, nullable=False, server_default="0"),
        sa.Column("locked_revenue", MONEY, nullable=False, server_default="0"),
        sa.Column("finalized_revenue", MONEY, nullable=False, server_default="0"),
        sa.Column("cash_received", MONEY, nullable=False, server_default="0"),
        sa.Column("reversed_revenue", MONEY, nullable=False, server_default="0"),
        sa.Column("direct_cost", MONEY, nullable=False, server_default="0"),
        sa.Column("allocated_ops_cost", MONEY, nullable=False, server_default="0"),
        sa.Column("contribution_margin", MONEY, nullable=False, server_default="0"),
        sa.Column("calculation_version", sa.String(length=48), nullable=False),
        sa.Column("source_snapshot_refs", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("period_end > period_start", name="ck_channel_pnl_period"),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "period_start",
            "period_end",
            "calculation_version",
            name="uq_channel_pnl_window",
        ),
    )
    op.create_index(
        "ix_channel_pnl_channel", "channel_pnl_snapshots", ["channel_workspace_id"]
    )
    op.create_index("ix_channel_pnl_hash", "channel_pnl_snapshots", ["content_hash"])

    op.create_table(
        "self_funding_assessments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("assessment_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.String(length=48), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("reason_codes", JSONB, nullable=False),
        sa.Column("input_refs", JSONB, nullable=False),
        sa.Column("assessment_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('SELF_FUNDING','FUNDED_EXPERIMENT','RESTRICTED','PAUSED')",
            name="ck_self_funding_decision",
        ),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "assessment_window_end",
            "policy_version",
            name="uq_self_funding_assessment",
        ),
    )
    op.create_index(
        "ix_self_funding_channel", "self_funding_assessments", ["channel_workspace_id"]
    )

    op.create_table(
        "platform_enforcement_incidents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("uploaded_video_id", UUID, nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("external_incident_ref", sa.String(length=240), nullable=False),
        sa.Column("incident_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=24), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column(
            "freeze_learning", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_payload", JSONB, nullable=False),
        sa.Column("source_ref", sa.String(length=260), nullable=False),
        sa.Column("incident_hash", sa.String(length=64), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "severity IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="ck_platform_enforcement_severity",
        ),
        sa.CheckConstraint(
            "state IN ('OPEN','UNDER_REVIEW','RESOLVED','DISMISSED')",
            name="ck_platform_enforcement_state",
        ),
        sa.UniqueConstraint(
            "platform",
            "external_incident_ref",
            name="uq_platform_enforcement_external_ref",
        ),
    )
    op.create_index(
        "ix_platform_enforcement_channel",
        "platform_enforcement_incidents",
        ["channel_workspace_id"],
    )
    op.create_index(
        "ix_platform_enforcement_video",
        "platform_enforcement_incidents",
        ["uploaded_video_id"],
    )
    op.create_index(
        "ix_platform_enforcement_state", "platform_enforcement_incidents", ["state"]
    )

    op.create_table(
        "appeal_evidence_packs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("platform_enforcement_incident_id", UUID, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("rights_basis", sa.Text(), nullable=False),
        sa.Column("evidence_items", JSONB, nullable=False),
        sa.Column("timeline", JSONB, nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("approved_by", UUID, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pack_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('READY_FOR_HUMAN','HUMAN_APPROVED','SUBMITTED','RESOLVED','REJECTED')",
            name="ck_appeal_evidence_pack_state",
        ),
        sa.UniqueConstraint(
            "platform_enforcement_incident_id",
            "version_number",
            name="uq_appeal_evidence_pack_version",
        ),
    )
    op.create_index(
        "ix_appeal_incident",
        "appeal_evidence_packs",
        ["platform_enforcement_incident_id"],
    )

    op.create_table(
        "affiliate_offer_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("merchant", sa.String(length=160), nullable=False),
        sa.Column("offer_ref", sa.String(length=240), nullable=False),
        sa.Column("product_ref", sa.String(length=240), nullable=True),
        sa.Column("commission_model", JSONB, nullable=False),
        sa.Column("attribution_window_text", sa.Text(), nullable=False),
        sa.Column("terms_hash", sa.String(length=64), nullable=False),
        sa.Column("disclosure_required", sa.Boolean(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('ACTIVE','EXPIRED','REVOKED','SUPERSEDED')",
            name="ck_affiliate_offer_state",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > effective_at",
            name="ck_affiliate_offer_window",
        ),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "merchant",
            "offer_ref",
            "terms_hash",
            name="uq_affiliate_offer_terms",
        ),
    )
    op.create_index(
        "ix_affiliate_offer_channel",
        "affiliate_offer_snapshots",
        ["channel_workspace_id"],
    )

    op.create_table(
        "affiliate_link_registry",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("affiliate_offer_snapshot_id", UUID, nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("short_url", sa.Text(), nullable=True),
        sa.Column("utm_policy_version", sa.String(length=80), nullable=False),
        sa.Column("disclosure_required", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("last_health_state", sa.String(length=24), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('ACTIVE','DISABLED','REVOKED')",
            name="ck_affiliate_link_state",
        ),
        sa.CheckConstraint(
            "last_health_state IN ('UNKNOWN','HEALTHY','BROKEN','STALE')",
            name="ck_affiliate_link_health",
        ),
        sa.UniqueConstraint(
            "channel_workspace_id", "canonical_url", name="uq_affiliate_link_url"
        ),
    )
    op.create_index(
        "ix_affiliate_link_channel", "affiliate_link_registry", ["channel_workspace_id"]
    )
    op.create_index(
        "ix_affiliate_link_offer",
        "affiliate_link_registry",
        ["affiliate_offer_snapshot_id"],
    )

    op.create_table(
        "business_disclosure_assessments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=False),
        sa.Column("publish_package_ref", sa.String(length=260), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("required_disclosures", JSONB, nullable=False),
        sa.Column("observed_disclosures", JSONB, nullable=False),
        sa.Column("link_registry_refs", JSONB, nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("reason_codes", JSONB, nullable=False),
        sa.Column("assessment_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('PASS','BLOCK','HUMAN_REVIEW')",
            name="ck_business_disclosure_decision",
        ),
        sa.UniqueConstraint(
            "publish_package_ref",
            "policy_version",
            name="uq_business_disclosure_package",
        ),
    )
    op.create_index(
        "ix_business_disclosure_channel",
        "business_disclosure_assessments",
        ["channel_workspace_id"],
    )
    op.create_index(
        "ix_business_disclosure_project",
        "business_disclosure_assessments",
        ["video_project_id"],
    )

    op.create_table(
        "business_action_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("target_ref", sa.String(length=260), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("assignee_ref", sa.String(length=200), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_refs", JSONB, nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "priority IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="ck_business_action_priority",
        ),
        sa.CheckConstraint(
            "state IN ('OPEN','IN_PROGRESS','DONE','DISMISSED')",
            name="ck_business_action_state",
        ),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "action_type",
            "target_ref",
            "reason_code",
            name="uq_business_action_identity",
        ),
    )
    op.create_index(
        "ix_business_action_channel", "business_action_items", ["channel_workspace_id"]
    )
    op.create_index("ix_business_action_state", "business_action_items", ["state"])


def downgrade() -> None:
    op.drop_table("business_action_items")
    op.drop_table("business_disclosure_assessments")
    op.drop_table("affiliate_link_registry")
    op.drop_table("affiliate_offer_snapshots")
    op.drop_table("appeal_evidence_packs")
    op.drop_table("platform_enforcement_incidents")
    op.drop_table("self_funding_assessments")
    op.drop_table("channel_pnl_snapshots")
    op.drop_table("revenue_snapshots")
    op.drop_table("monetization_account_statuses")
    op.drop_table("payment_profile_statuses")
