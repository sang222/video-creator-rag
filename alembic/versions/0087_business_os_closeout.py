"""Monetization, revenue, enforcement, affiliate and disclosure business state.

Revision ID: 0087_business_os_closeout
Revises: 0086_learning_authority_closeout
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0087_business_os_closeout"
down_revision = "0086_learning_authority_closeout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_profile_statuses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("payee_ref", sa.String(200), nullable=False),
        sa.Column("tax_state", sa.String(32), nullable=False),
        sa.Column("address_verification_state", sa.String(32), nullable=False),
        sa.Column("payment_method_state", sa.String(32), nullable=False),
        sa.Column("payment_hold_state", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_ref", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "payee_ref", "source_updated_at", name="uq_payment_profile_status_snapshot"),
        sa.CheckConstraint("source_type in ('API','IMPORT','OPERATOR_ATTESTATION')", name="ck_payment_profile_status_source"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_payment_profile_status_hash"),
    )
    op.create_index("ix_payment_profile_status_company", "payment_profile_statuses", ["company_id", "source_updated_at"])

    op.create_table(
        "monetization_account_statuses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channel_workspaces.id"), nullable=False),
        sa.Column("platform", sa.String(40), nullable=False),
        sa.Column("destination_ref", sa.Text(), nullable=False),
        sa.Column("program_type", sa.String(80), nullable=False),
        sa.Column("eligibility_state", sa.String(40), nullable=False),
        sa.Column("enrollment_state", sa.String(40), nullable=False),
        sa.Column("restriction_state", sa.String(40), nullable=False),
        sa.Column("country_eligibility_state", sa.String(40), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_ref", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("channel_workspace_id", "platform", "program_type", "source_updated_at", name="uq_monetization_status_snapshot"),
        sa.CheckConstraint("source_type in ('API','IMPORT','OPERATOR_ATTESTATION')", name="ck_monetization_status_source"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_monetization_status_hash"),
    )
    op.create_index("ix_monetization_status_channel", "monetization_account_statuses", ["channel_workspace_id", "source_updated_at"])

    op.create_table(
        "revenue_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channel_workspaces.id"), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("estimated_amount", sa.Numeric(18,6), nullable=False, server_default="0"),
        sa.Column("finalized_or_locked_amount", sa.Numeric(18,6), nullable=False, server_default="0"),
        sa.Column("reversed_amount", sa.Numeric(18,6), nullable=False, server_default="0"),
        sa.Column("cash_received_amount", sa.Numeric(18,6), nullable=False, server_default="0"),
        sa.Column("cash_receivable_amount", sa.Numeric(18,6), nullable=False, server_default="0"),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("channel_workspace_id", "source", "period_start", "period_end", "source_updated_at", name="uq_revenue_snapshot_source_period"),
        sa.CheckConstraint("period_end > period_start", name="ck_revenue_snapshot_period"),
        sa.CheckConstraint("estimated_amount >= 0 and finalized_or_locked_amount >= 0 and reversed_amount >= 0 and cash_received_amount >= 0 and cash_receivable_amount >= 0", name="ck_revenue_snapshot_amounts"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_revenue_snapshot_hash"),
    )
    op.create_index("ix_revenue_snapshot_channel_period", "revenue_snapshots", ["channel_workspace_id", "period_end"])

    op.create_table(
        "channel_pnl_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channel_workspaces.id"), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("direct_cost", sa.Numeric(18,6), nullable=False),
        sa.Column("shared_cost_allocated", sa.Numeric(18,6), nullable=False),
        sa.Column("estimated_revenue", sa.Numeric(18,6), nullable=False),
        sa.Column("finalized_revenue", sa.Numeric(18,6), nullable=False),
        sa.Column("cash_received", sa.Numeric(18,6), nullable=False),
        sa.Column("contribution_margin", sa.Numeric(18,6), nullable=False),
        sa.Column("burn_rate", sa.Numeric(18,6), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("channel_workspace_id", "period_start", "period_end", name="uq_channel_pnl_period"),
        sa.CheckConstraint("period_end > period_start and direct_cost >= 0 and shared_cost_allocated >= 0 and estimated_revenue >= 0 and finalized_revenue >= 0 and cash_received >= 0 and burn_rate >= 0", name="ck_channel_pnl_values"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_channel_pnl_hash"),
    )
    op.create_index("ix_channel_pnl_channel_period", "channel_pnl_snapshots", ["channel_workspace_id", "period_end"])

    op.create_table(
        "platform_enforcement_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channel_workspaces.id"), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("uploaded_videos.id")),
        sa.Column("platform", sa.String(40), nullable=False),
        sa.Column("incident_type", sa.String(80), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="OPEN"),
        sa.Column("source_status", sa.Text(), nullable=False),
        sa.Column("freeze_learning", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("content_hash", name="uq_platform_enforcement_incident_hash"),
        sa.CheckConstraint("scope in ('VIDEO','CHANNEL','ACCOUNT')", name="ck_platform_enforcement_scope"),
        sa.CheckConstraint("severity in ('INFO','LOW','MEDIUM','HIGH','CRITICAL')", name="ck_platform_enforcement_severity"),
        sa.CheckConstraint("state in ('OPEN','UNDER_REVIEW','APPEAL_READY','SUBMITTED','RESOLVED','DISMISSED')", name="ck_platform_enforcement_state"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_platform_enforcement_hash"),
    )
    op.create_index("ix_platform_enforcement_channel_state", "platform_enforcement_incidents", ["channel_workspace_id", "state"])
    op.create_index("ix_platform_enforcement_video", "platform_enforcement_incidents", ["uploaded_video_id"])

    op.create_table(
        "appeal_evidence_packs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("platform_enforcement_incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("platform_enforcement_incidents.id"), nullable=False),
        sa.Column("rights_basis", sa.Text(), nullable=False),
        sa.Column("evidence_items", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("timeline", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("internal_reviewer_ref", sa.Text()),
        sa.Column("state", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("result_summary", sa.Text()),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("platform_enforcement_incident_id", "content_hash", name="uq_appeal_evidence_pack_version"),
        sa.CheckConstraint("state in ('DRAFT','READY_FOR_HUMAN','SUBMITTED_BY_HUMAN','RESOLVED')", name="ck_appeal_evidence_pack_state"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_appeal_evidence_pack_hash"),
    )

    op.create_table(
        "affiliate_offer_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("merchant", sa.String(200), nullable=False),
        sa.Column("offer_ref", sa.Text(), nullable=False),
        sa.Column("product_ref", sa.Text()),
        sa.Column("commission_model", sa.Text(), nullable=False),
        sa.Column("attribution_window_text", sa.Text(), nullable=False),
        sa.Column("terms_hash", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("disclosure_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("state", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("content_hash", name="uq_affiliate_offer_snapshot_hash"),
        sa.CheckConstraint("state in ('ACTIVE','EXPIRED','SUSPENDED')", name="ck_affiliate_offer_snapshot_state"),
        sa.CheckConstraint("terms_hash ~ '^[0-9a-f]{64}$' and content_hash ~ '^[0-9a-f]{64}$'", name="ck_affiliate_offer_snapshot_hashes"),
    )
    op.create_index("ix_affiliate_offer_snapshot_merchant", "affiliate_offer_snapshots", ["merchant", "effective_at"])

    op.create_table(
        "affiliate_link_registry",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("affiliate_offer_snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("affiliate_offer_snapshots.id"), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channel_workspaces.id"), nullable=False),
        sa.Column("destination_url", sa.Text(), nullable=False),
        sa.Column("redirect_url", sa.Text()),
        sa.Column("utm_template_version", sa.String(80), nullable=False),
        sa.Column("disclosure_required", sa.Boolean(), nullable=False),
        sa.Column("active_state", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("last_health_checked_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("channel_workspace_id", "destination_url", "affiliate_offer_snapshot_id", name="uq_affiliate_link_registry_target"),
        sa.CheckConstraint("active_state in ('ACTIVE','BROKEN','EXPIRED','DISABLED')", name="ck_affiliate_link_registry_state"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_affiliate_link_registry_hash"),
    )
    op.create_index("ix_affiliate_link_registry_channel_state", "affiliate_link_registry", ["channel_workspace_id", "active_state"])

    # Strengthen the 0086 system-promotion guard now that enforcement state exists.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION vcos_system_memory_promotion_guard()
        RETURNS trigger AS $$
        DECLARE eligibility_result text;
        DECLARE recurrence_count integer;
        DECLARE source_video uuid;
        BEGIN
          IF NEW.approval_authority_type IS DISTINCT FROM 'SYSTEM_POLICY' THEN
            RETURN NEW;
          END IF;
          IF NEW.created_from_learning_candidate_id IS NULL THEN
            RAISE EXCEPTION 'SYSTEM_POLICY_LEARNING_CANDIDATE_REQUIRED';
          END IF;
          SELECT e.result, c.uploaded_video_id INTO eligibility_result, source_video
          FROM learning_candidates c
          JOIN learning_promotion_eligibility_runs e ON e.id = c.eligibility_run_id
          WHERE c.id = NEW.created_from_learning_candidate_id;
          IF eligibility_result IS DISTINCT FROM 'ELIGIBLE_FOR_REVIEW' THEN
            RAISE EXCEPTION 'SYSTEM_PROMOTION_ELIGIBILITY_RECHECK_FAILED';
          END IF;
          IF EXISTS (
            SELECT 1 FROM platform_enforcement_incidents i
            WHERE i.channel_workspace_id = NEW.channel_workspace_id
              AND i.freeze_learning = true
              AND i.state IN ('OPEN','UNDER_REVIEW','APPEAL_READY','SUBMITTED')
              AND (i.uploaded_video_id IS NULL OR i.uploaded_video_id = source_video)
          ) THEN
            RAISE EXCEPTION 'SYSTEM_PROMOTION_ENFORCEMENT_FREEZE_ACTIVE';
          END IF;
          SELECT count(DISTINCT c.uploaded_video_id) INTO recurrence_count
          FROM learning_candidates c
          JOIN learning_candidate_generation_runs g ON g.id = c.generation_run_id
          JOIN learning_promotion_eligibility_runs e ON e.id = c.eligibility_run_id
          WHERE c.channel_workspace_id = NEW.channel_workspace_id
            AND c.equivalence_fingerprint = (
              SELECT equivalence_fingerprint FROM learning_candidates
              WHERE id = NEW.created_from_learning_candidate_id
            )
            AND c.risk_level = 'LOW'
            AND jsonb_array_length(coalesce(c.policy_flags,'[]'::jsonb)) = 0
            AND jsonb_array_length(coalesce(c.rights_flags,'[]'::jsonb)) = 0
            AND g.metadata->>'maturity' = 'MATURE'
            AND e.result = 'ELIGIBLE_FOR_REVIEW';
          IF coalesce(recurrence_count,0) < 3 THEN
            RAISE EXCEPTION 'SYSTEM_PROMOTION_EQUIVALENCE_RECURRENCE_INSUFFICIENT';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    op.drop_table("affiliate_link_registry")
    op.drop_table("affiliate_offer_snapshots")
    op.drop_table("appeal_evidence_packs")
    op.drop_table("platform_enforcement_incidents")
    op.drop_table("channel_pnl_snapshots")
    op.drop_table("revenue_snapshots")
    op.drop_table("monetization_account_statuses")
    op.drop_table("payment_profile_statuses")
    # Restore the 0086 guard without enforcement dependency.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION vcos_system_memory_promotion_guard()
        RETURNS trigger AS $$
        DECLARE eligibility_result text;
        DECLARE recurrence_count integer;
        BEGIN
          IF NEW.approval_authority_type IS DISTINCT FROM 'SYSTEM_POLICY' THEN RETURN NEW; END IF;
          SELECT e.result INTO eligibility_result
          FROM learning_candidates c JOIN learning_promotion_eligibility_runs e ON e.id = c.eligibility_run_id
          WHERE c.id = NEW.created_from_learning_candidate_id;
          IF eligibility_result IS DISTINCT FROM 'ELIGIBLE_FOR_REVIEW' THEN RAISE EXCEPTION 'SYSTEM_PROMOTION_ELIGIBILITY_RECHECK_FAILED'; END IF;
          SELECT count(DISTINCT c.uploaded_video_id) INTO recurrence_count
          FROM learning_candidates c
          JOIN learning_candidate_generation_runs g ON g.id = c.generation_run_id
          JOIN learning_promotion_eligibility_runs e ON e.id = c.eligibility_run_id
          WHERE c.channel_workspace_id = NEW.channel_workspace_id
            AND c.equivalence_fingerprint = (SELECT equivalence_fingerprint FROM learning_candidates WHERE id = NEW.created_from_learning_candidate_id)
            AND c.risk_level = 'LOW' AND jsonb_array_length(coalesce(c.policy_flags,'[]'::jsonb)) = 0
            AND jsonb_array_length(coalesce(c.rights_flags,'[]'::jsonb)) = 0
            AND g.metadata->>'maturity' = 'MATURE' AND e.result = 'ELIGIBLE_FOR_REVIEW';
          IF coalesce(recurrence_count,0) < 3 THEN RAISE EXCEPTION 'SYSTEM_PROMOTION_EQUIVALENCE_RECURRENCE_INSUFFICIENT'; END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
