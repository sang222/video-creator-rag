"""Permit one atomic linkage seal before replacement-authority immutability."""

from alembic import op


revision = "0067_replacement_seal"
down_revision = "0066_script_ssot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_script_contract_replacement_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'script contract replacement authorities are immutable';
            END IF;
            IF OLD.replacement_candidate_id IS NULL
               AND OLD.replacement_slot_id IS NULL
               AND OLD.replacement_qualification_run_id IS NULL
               AND NEW.replacement_candidate_id IS NOT NULL
               AND NEW.replacement_slot_id IS NOT NULL
               AND NEW.replacement_qualification_run_id IS NOT NULL
               AND NEW.operator_authorized_at IS NOT DISTINCT FROM OLD.operator_authorized_at
               AND NEW.replaces_candidate_id IS NOT DISTINCT FROM OLD.replaces_candidate_id
               AND NEW.replaces_slot_id IS NOT DISTINCT FROM OLD.replaces_slot_id
               AND NEW.replacement_reason IS NOT DISTINCT FROM OLD.replacement_reason
               AND NEW.source_topic_definition_id IS NOT DISTINCT FROM OLD.source_topic_definition_id
               AND NEW.source_preflight_id IS NOT DISTINCT FROM OLD.source_preflight_id
               AND NEW.source_evidence_pack_id IS NOT DISTINCT FROM OLD.source_evidence_pack_id
               AND NEW.source_memory_digest_id IS NOT DISTINCT FROM OLD.source_memory_digest_id
               AND NEW.old_script_contract_version IS NOT DISTINCT FROM OLD.old_script_contract_version
               AND NEW.new_script_contract_version IS NOT DISTINCT FROM OLD.new_script_contract_version
               AND NEW.max_replacement_lineages IS NOT DISTINCT FROM OLD.max_replacement_lineages
               AND NEW.max_initial_writer_submissions IS NOT DISTINCT FROM OLD.max_initial_writer_submissions
               AND NEW.max_verifier_submissions IS NOT DISTINCT FROM OLD.max_verifier_submissions
               AND NEW.bounded_content_repair_policy_ref IS NOT DISTINCT FROM OLD.bounded_content_repair_policy_ref
               AND NEW.production_window_end IS NOT DISTINCT FROM OLD.production_window_end
               AND NEW.qualification_deadline IS NOT DISTINCT FROM OLD.qualification_deadline
               AND NEW.authority_hash IS NOT DISTINCT FROM OLD.authority_hash
               AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'script contract replacement authorities are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "DROP TRIGGER trg_prevent_script_contract_replacement_mutation "
        "ON script_contract_replacement_authorities"
    )
    op.execute(
        "CREATE TRIGGER trg_prevent_script_contract_replacement_mutation "
        "BEFORE UPDATE OR DELETE ON script_contract_replacement_authorities "
        "FOR EACH ROW EXECUTE FUNCTION prevent_script_contract_replacement_mutation()"
    )


def downgrade() -> None:
    raise RuntimeError("0067 is intentionally forward-only in production")
