"""Learning equivalence, promotion re-check and exactly-once review guards.

Revision ID: 0086_learning_authority_closeout
Revises: 0085_series_authority_closeout
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0086_learning_authority_closeout"
down_revision = "0085_series_authority_closeout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("learning_candidates", sa.Column("equivalence_fingerprint", sa.String(64)))
    op.execute(
        """
        UPDATE learning_candidates
        SET equivalence_fingerprint =
          md5(coalesce(candidate_type,'') || '|' || coalesce(recommended_scope,'') || '|' ||
              lower(regexp_replace(coalesce(suggested_learning,''), '\\s+', ' ', 'g')))
          ||
          md5('vcos|' || coalesce(candidate_type,'') || '|' || coalesce(recommended_scope,'') || '|' ||
              lower(regexp_replace(coalesce(suggested_learning,''), '\\s+', ' ', 'g')))
        WHERE equivalence_fingerprint IS NULL
        """
    )
    op.alter_column("learning_candidates", "equivalence_fingerprint", nullable=False)
    op.create_check_constraint("ck_learning_candidates_equivalence_fingerprint", "learning_candidates", "equivalence_fingerprint ~ '^[0-9a-f]{64}$'")
    op.create_index("ix_learning_candidates_equivalence", "learning_candidates", ["channel_workspace_id", "equivalence_fingerprint"])

    op.create_table(
        "learning_system_promotion_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("learning_candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("learning_candidates.id"), nullable=False),
        sa.Column("eligibility_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("learning_promotion_eligibility_runs.id"), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channel_workspaces.id"), nullable=False),
        sa.Column("equivalence_fingerprint", sa.String(64), nullable=False),
        sa.Column("distinct_mature_source_count", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(24), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("policy_version", sa.String(120), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("learning_candidate_id", name="uq_learning_system_promotion_candidate"),
        sa.UniqueConstraint("receipt_hash", name="uq_learning_system_promotion_hash"),
        sa.CheckConstraint("distinct_mature_source_count >= 0", name="ck_learning_system_promotion_count"),
        sa.CheckConstraint("result in ('PROMOTED','EVIDENCE_ONLY','BLOCKED')", name="ck_learning_system_promotion_result"),
        sa.CheckConstraint("equivalence_fingerprint ~ '^[0-9a-f]{64}$' and policy_hash ~ '^[0-9a-f]{64}$' and receipt_hash ~ '^[0-9a-f]{64}$'", name="ck_learning_system_promotion_hashes"),
    )
    op.create_index("ix_learning_system_promotion_fingerprint", "learning_system_promotion_receipts", ["channel_workspace_id", "equivalence_fingerprint"])

    op.create_table(
        "learning_review_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("learning_candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("learning_candidates.id"), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("actor_role", sa.String(80), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("decision_hash", sa.String(64), nullable=False),
        sa.Column("learning_review_decision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("learning_review_decisions.id")),
        sa.Column("state", sa.String(24), nullable=False, server_default="INTENDED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("learning_candidate_id", name="uq_learning_review_commands_candidate"),
        sa.UniqueConstraint("command_id", name="uq_learning_review_commands_command"),
        sa.UniqueConstraint("decision_hash", name="uq_learning_review_commands_hash"),
        sa.CheckConstraint("state in ('INTENDED','COMPLETED','REJECTED')", name="ck_learning_review_commands_state"),
        sa.CheckConstraint("decision_hash ~ '^[0-9a-f]{64}$'", name="ck_learning_review_commands_hash"),
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION vcos_learning_candidate_equivalence_guard()
        RETURNS trigger AS $$
        DECLARE canonical text;
        BEGIN
          IF NEW.equivalence_fingerprint IS NULL OR NEW.equivalence_fingerprint !~ '^[0-9a-f]{64}$' THEN
            canonical := coalesce(NEW.candidate_type,'') || '|' || coalesce(NEW.recommended_scope,'') || '|' ||
              lower(regexp_replace(coalesce(NEW.suggested_learning,''), '\\s+', ' ', 'g'));
            NEW.equivalence_fingerprint := md5(canonical) || md5('vcos|' || canonical);
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_learning_candidate_equivalence_guard
        BEFORE INSERT OR UPDATE OF candidate_type,recommended_scope,suggested_learning,equivalence_fingerprint
        ON learning_candidates
        FOR EACH ROW EXECUTE FUNCTION vcos_learning_candidate_equivalence_guard();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION vcos_learning_review_exactly_once_guard()
        RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM learning_review_decisions
            WHERE learning_candidate_id = NEW.learning_candidate_id
          ) THEN
            RAISE EXCEPTION 'LEARNING_REVIEW_DECISION_ALREADY_TERMINAL';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_learning_review_exactly_once_guard
        BEFORE INSERT ON learning_review_decisions
        FOR EACH ROW EXECUTE FUNCTION vcos_learning_review_exactly_once_guard();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION vcos_learning_audit_semantics_guard()
        RETURNS trigger AS $$
        BEGIN
          IF coalesce((NEW.metadata->>'automated_learning')::boolean, false)
             AND NEW.reason_codes ? 'NO_AUTO_PROMOTION' THEN
            NEW.reason_codes := (NEW.reason_codes - 'NO_AUTO_PROMOTION') || '["SYSTEM_GOVERNED_PROMOTION_PATH"]'::jsonb;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_learning_audit_semantics_guard
        BEFORE INSERT OR UPDATE OF reason_codes,metadata
        ON learning_candidate_generation_runs
        FOR EACH ROW EXECUTE FUNCTION vcos_learning_audit_semantics_guard();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION vcos_system_memory_promotion_guard()
        RETURNS trigger AS $$
        DECLARE eligibility_result text;
        DECLARE recurrence_count integer;
        BEGIN
          IF NEW.approval_authority_type IS DISTINCT FROM 'SYSTEM_POLICY' THEN
            RETURN NEW;
          END IF;
          IF NEW.created_from_learning_candidate_id IS NULL THEN
            RAISE EXCEPTION 'SYSTEM_POLICY_LEARNING_CANDIDATE_REQUIRED';
          END IF;
          SELECT e.result INTO eligibility_result
          FROM learning_candidates c
          JOIN learning_promotion_eligibility_runs e ON e.id = c.eligibility_run_id
          WHERE c.id = NEW.created_from_learning_candidate_id;
          IF eligibility_result IS DISTINCT FROM 'ELIGIBLE_FOR_REVIEW' THEN
            RAISE EXCEPTION 'SYSTEM_PROMOTION_ELIGIBILITY_RECHECK_FAILED';
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
        CREATE TRIGGER trg_system_memory_promotion_guard
        BEFORE INSERT OR UPDATE OF approval_authority_type,approval_status
        ON channel_memory_items
        FOR EACH ROW EXECUTE FUNCTION vcos_system_memory_promotion_guard();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_system_memory_promotion_guard ON channel_memory_items; DROP FUNCTION IF EXISTS vcos_system_memory_promotion_guard()")
    op.execute("DROP TRIGGER IF EXISTS trg_learning_audit_semantics_guard ON learning_candidate_generation_runs; DROP FUNCTION IF EXISTS vcos_learning_audit_semantics_guard()")
    op.execute("DROP TRIGGER IF EXISTS trg_learning_review_exactly_once_guard ON learning_review_decisions; DROP FUNCTION IF EXISTS vcos_learning_review_exactly_once_guard()")
    op.execute("DROP TRIGGER IF EXISTS trg_learning_candidate_equivalence_guard ON learning_candidates; DROP FUNCTION IF EXISTS vcos_learning_candidate_equivalence_guard()")
    op.drop_table("learning_review_commands")
    op.drop_table("learning_system_promotion_receipts")
    op.drop_index("ix_learning_candidates_equivalence", table_name="learning_candidates")
    op.drop_constraint("ck_learning_candidates_equivalence_fingerprint", "learning_candidates", type_="check")
    op.drop_column("learning_candidates", "equivalence_fingerprint")
