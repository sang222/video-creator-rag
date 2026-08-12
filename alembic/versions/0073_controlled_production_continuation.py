"""Add immutable one-shot authority for bounded-repair continuation.

Revision ID: 0073_controlled_continuation
Revises: 0072_controlled_recovery
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0073_controlled_continuation"
down_revision: str | None = "0072_controlled_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "controlled_production_continuation_authorities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "root_replacement_authority_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_qualification_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("source_slot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "continuation_candidate_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "continuation_slot_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "continuation_qualification_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_provider_response_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "repair_authorization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("continuation_reason", sa.String(160), nullable=False),
        sa.Column("root_authority_hash", sa.String(64), nullable=False),
        sa.Column("operator_recovery_schema_version", sa.String(80), nullable=False),
        sa.Column("operator_actor_context", postgresql.JSONB(), nullable=False),
        sa.Column("bounded_content_repair_policy_ref", sa.String(160), nullable=False),
        sa.Column("source_logical_identity_hash", sa.String(64), nullable=False),
        sa.Column("continuation_logical_identity_hash", sa.String(64), nullable=False),
        sa.Column("source_terminal_settlement_hash", sa.String(64), nullable=False),
        sa.Column("source_slot_state", sa.String(48), nullable=False),
        sa.Column("source_candidate_stage", sa.String(48), nullable=False),
        sa.Column("repair_authorization_hash", sa.String(64), nullable=False),
        sa.Column("affected_section_ids", postgresql.JSONB(), nullable=False),
        sa.Column("editable_claim_ids", postgresql.JSONB(), nullable=False),
        sa.Column("removable_claim_ids", postgresql.JSONB(), nullable=False),
        sa.Column(
            "source_background_attempt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("source_provider_response_id", sa.String(200), nullable=False),
        sa.Column("source_provider_request_id", sa.String(200), nullable=False),
        sa.Column("source_raw_provider_response_hash", sa.String(64), nullable=False),
        sa.Column("source_raw_output_hash", sa.String(64), nullable=False),
        sa.Column("source_typed_output_hash", sa.String(64), nullable=False),
        sa.Column("reclassification_receipt_hash", sa.String(64), nullable=False),
        sa.Column("deadline_policy", postgresql.JSONB(), nullable=False),
        sa.Column("slot_projection", postgresql.JSONB(), nullable=False),
        sa.Column("current_authority_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("provider_authority_hash", sa.String(64), nullable=False),
        sa.Column("budget_authority_hash", sa.String(64), nullable=False),
        sa.Column("max_writer_submissions", sa.Integer(), nullable=False),
        sa.Column("max_verifier_submissions", sa.Integer(), nullable=False),
        sa.Column("production_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qualification_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authority_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version = 'vcos.controlled-production-continuation.v1' and "
            "continuation_reason = 'BOUNDED_CONTENT_REPAIR_VERIFIER_CONTINUATION' and "
            "bounded_content_repair_policy_ref = 'script-content-repair.v1:max-1'",
            name="ck_controlled_continuation_identity",
        ),
        sa.CheckConstraint(
            "max_writer_submissions = 0 and max_verifier_submissions = 1",
            name="ck_controlled_continuation_bounds",
        ),
        sa.CheckConstraint(
            "qualification_deadline < production_window_end and "
            "root_authority_hash ~ '^[0-9a-f]{64}$' and "
            "source_logical_identity_hash ~ '^[0-9a-f]{64}$' and "
            "continuation_logical_identity_hash ~ '^[0-9a-f]{64}$' and "
            "source_terminal_settlement_hash ~ '^[0-9a-f]{64}$' and "
            "repair_authorization_hash ~ '^[0-9a-f]{64}$' and "
            "source_raw_provider_response_hash ~ '^[0-9a-f]{64}$' and "
            "source_raw_output_hash ~ '^[0-9a-f]{64}$' and "
            "source_typed_output_hash ~ '^[0-9a-f]{64}$' and "
            "reclassification_receipt_hash ~ '^[0-9a-f]{64}$' and "
            "provider_authority_hash ~ '^[0-9a-f]{64}$' and "
            "budget_authority_hash ~ '^[0-9a-f]{64}$' and "
            "authority_hash ~ '^[0-9a-f]{64}$'",
            name="ck_controlled_continuation_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["root_replacement_authority_id"],
            ["script_contract_replacement_authorities.id"],
            name="fk_controlled_continuation_root",
        ),
        sa.ForeignKeyConstraint(
            ["source_qualification_run_id"],
            ["script_qualification_runs.id"],
            name="fk_controlled_continuation_source_qualification",
        ),
        sa.ForeignKeyConstraint(
            ["source_slot_id"],
            ["long_form_publish_slots.id"],
            name="fk_controlled_continuation_source_slot",
        ),
        sa.ForeignKeyConstraint(
            ["continuation_candidate_id"],
            ["editorial_idea_candidates.id"],
            name="fk_controlled_continuation_candidate",
        ),
        sa.ForeignKeyConstraint(
            ["continuation_slot_id"],
            ["long_form_publish_slots.id"],
            name="fk_controlled_continuation_slot",
        ),
        sa.ForeignKeyConstraint(
            ["continuation_qualification_run_id"],
            ["script_qualification_runs.id"],
            name="fk_controlled_continuation_qualification",
        ),
        sa.ForeignKeyConstraint(
            ["source_provider_response_snapshot_id"],
            ["script_qualification_provider_response_snapshots.id"],
            name="fk_controlled_continuation_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["repair_authorization_id"],
            ["script_content_repair_authorization_receipts.id"],
            name="fk_controlled_continuation_repair_authorization",
        ),
        sa.ForeignKeyConstraint(
            ["source_background_attempt_id"],
            ["script_qualification_background_attempts.id"],
            name="fk_controlled_continuation_background_attempt",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "root_replacement_authority_id",
            name="uq_controlled_continuation_root",
        ),
        sa.UniqueConstraint(
            "source_qualification_run_id",
            name="uq_controlled_continuation_source_qualification",
        ),
        sa.UniqueConstraint(
            "continuation_slot_id", name="uq_controlled_continuation_slot"
        ),
        sa.UniqueConstraint(
            "continuation_qualification_run_id",
            name="uq_controlled_continuation_qualification",
        ),
        sa.UniqueConstraint(
            "source_provider_response_snapshot_id",
            name="uq_controlled_continuation_snapshot",
        ),
        sa.UniqueConstraint(
            "repair_authorization_id",
            name="uq_controlled_continuation_repair_authorization",
        ),
        sa.UniqueConstraint(
            "source_background_attempt_id",
            name="uq_controlled_continuation_background_attempt",
        ),
    )
    op.create_index(
        "ix_controlled_continuation_root",
        "controlled_production_continuation_authorities",
        ["root_replacement_authority_id"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_controlled_continuation_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'controlled production continuation authorities are immutable';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_controlled_continuation_immutable
        BEFORE UPDATE OR DELETE ON controlled_production_continuation_authorities
        FOR EACH ROW EXECUTE FUNCTION prevent_controlled_continuation_mutation();
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_controlled_continuation_seal()
        RETURNS trigger AS $$
        DECLARE source_run script_qualification_runs%ROWTYPE;
        DECLARE child_run script_qualification_runs%ROWTYPE;
        DECLARE source_slot long_form_publish_slots%ROWTYPE;
        DECLARE child_slot long_form_publish_slots%ROWTYPE;
        DECLARE writer_attempt_count integer;
        DECLARE verifier_attempt_count integer;
        BEGIN
            SELECT * INTO source_run FROM script_qualification_runs
            WHERE id = NEW.source_qualification_run_id;
            SELECT * INTO child_run FROM script_qualification_runs
            WHERE id = NEW.continuation_qualification_run_id;
            SELECT * INTO source_slot FROM long_form_publish_slots
            WHERE id = NEW.source_slot_id;
            SELECT * INTO child_slot FROM long_form_publish_slots
            WHERE id = NEW.continuation_slot_id;
            SELECT count(*) INTO writer_attempt_count
            FROM script_qualification_background_attempts
            WHERE script_qualification_run_id = NEW.continuation_qualification_run_id
              AND phase = 'WRITER';
            SELECT count(*) INTO verifier_attempt_count
            FROM script_qualification_background_attempts
            WHERE script_qualification_run_id = NEW.continuation_qualification_run_id
              AND phase = 'VERIFIER';
            IF source_run.id IS NULL
               OR child_run.id IS NULL
               OR source_slot.id IS NULL
               OR child_slot.id IS NULL
               OR source_run.state <> 'BLOCKED_NON_REPAIRABLE'
               OR source_run.publish_slot_id IS DISTINCT FROM source_slot.id
               OR source_run.editorial_idea_candidate_id IS DISTINCT FROM NEW.continuation_candidate_id
               OR source_run.logical_identity_hash IS DISTINCT FROM NEW.source_logical_identity_hash
               OR child_run.supersedes_qualification_run_id IS DISTINCT FROM source_run.id
               OR child_run.publish_slot_id IS DISTINCT FROM child_slot.id
               OR child_run.editorial_idea_candidate_id IS DISTINCT FROM NEW.continuation_candidate_id
               OR child_run.logical_identity_hash IS DISTINCT FROM NEW.continuation_logical_identity_hash
               OR child_run.replacement_authority_id IS DISTINCT FROM NEW.root_replacement_authority_id
               OR child_run.logical_deadline_at IS DISTINCT FROM NEW.qualification_deadline
               OR child_run.repair_attempts IS DISTINCT FROM 1
               OR child_run.script_contract_version IS DISTINCT FROM 'V2_SINGLE_SOURCE'
               OR source_slot.state IS DISTINCT FROM NEW.source_slot_state
               OR source_slot.state IS DISTINCT FROM 'CANCELED'
               OR source_slot.admitted_video_project_id IS NOT NULL
               OR child_slot.state IS DISTINCT FROM 'QUALIFICATION_RESERVED'
               OR child_slot.reserved_candidate_id IS DISTINCT FROM NEW.continuation_candidate_id
               OR child_slot.replaces_slot_id IS DISTINCT FROM source_slot.id
               OR child_slot.replacement_authority_id IS DISTINCT FROM NEW.root_replacement_authority_id
               OR child_slot.replacement_reason IS DISTINCT FROM NEW.continuation_reason
               OR child_slot.target_start_window_close_at IS DISTINCT FROM NEW.production_window_end
               OR NEW.slot_projection->>'slot_id' IS DISTINCT FROM child_slot.id::text
               OR NEW.slot_projection->>'source_slot_id' IS DISTINCT FROM source_slot.id::text
               OR NEW.slot_projection->>'launch_run_id' IS DISTINCT FROM child_slot.launch_run_id::text
               OR NEW.slot_projection->>'launch_policy_version_id' IS DISTINCT FROM child_slot.launch_policy_version_id::text
               OR NEW.slot_projection->>'company_id' IS DISTINCT FROM child_slot.company_id::text
               OR NEW.slot_projection->>'channel_workspace_id' IS DISTINCT FROM child_slot.channel_workspace_id::text
               OR (NEW.slot_projection->>'local_publish_date')::date IS DISTINCT FROM child_slot.local_publish_date
               OR (NEW.slot_projection->>'intended_publish_at')::timestamptz IS DISTINCT FROM child_slot.intended_publish_at
               OR (NEW.slot_projection->>'target_start_window_open_at')::timestamptz IS DISTINCT FROM child_slot.target_start_window_open_at
               OR (NEW.slot_projection->>'target_start_window_close_at')::timestamptz IS DISTINCT FROM child_slot.target_start_window_close_at
               OR NEW.slot_projection->>'reserved_candidate_id' IS DISTINCT FROM child_slot.reserved_candidate_id::text
               OR NEW.slot_projection->>'replacement_authority_id' IS DISTINCT FROM child_slot.replacement_authority_id::text
               OR NEW.slot_projection->>'replacement_reason' IS DISTINCT FROM child_slot.replacement_reason
               OR NEW.slot_projection->>'replacement_lineage_key' IS DISTINCT FROM child_slot.replacement_lineage_key
               OR writer_attempt_count <> 0
               OR verifier_attempt_count <> 0
            THEN
                RAISE EXCEPTION 'controlled continuation seal drift';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_controlled_continuation_seal
        BEFORE INSERT ON controlled_production_continuation_authorities
        FOR EACH ROW EXECUTE FUNCTION validate_controlled_continuation_seal();
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_controlled_continuation_provider_boundary()
        RETURNS trigger AS $$
        DECLARE continuation controlled_production_continuation_authorities%ROWTYPE;
        BEGIN
            SELECT * INTO continuation
            FROM controlled_production_continuation_authorities
            WHERE continuation_qualification_run_id = NEW.script_qualification_run_id;
            IF NOT FOUND THEN
                RETURN NEW;
            END IF;
            IF NEW.phase = 'WRITER' OR continuation.max_writer_submissions <> 0 THEN
                RAISE EXCEPTION 'controlled continuation forbids writer submissions';
            END IF;
            IF NEW.phase <> 'VERIFIER'
               OR continuation.max_verifier_submissions <> 1
               OR NEW.logical_deadline_at IS DISTINCT FROM continuation.qualification_deadline
            THEN
                RAISE EXCEPTION 'controlled continuation verifier authority mismatch';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_controlled_continuation_provider_boundary
        BEFORE INSERT OR UPDATE ON script_qualification_background_attempts
        FOR EACH ROW EXECUTE FUNCTION enforce_controlled_continuation_provider_boundary();
        """
    )


def downgrade() -> None:
    raise RuntimeError("0073 is intentionally forward-only in production")
