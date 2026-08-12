"""Extend immutable replacement authority for one controlled first-video recovery.

Revision ID: 0072_controlled_recovery
Revises: 0071_editorial_specificity
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0072_controlled_recovery"
down_revision: str | None = "0071_editorial_specificity"
branch_labels = None
depends_on = None


_TABLE = "script_contract_replacement_authorities"


def upgrade() -> None:
    # Existing SSOT-migration authorities remain byte-for-byte historical.
    # The new receipt fields are nullable for those rows and are required by a
    # conditional constraint only for the operator recovery reason.
    op.add_column(
        _TABLE,
        sa.Column("operator_recovery_schema_version", sa.String(80)),
    )
    op.add_column(
        _TABLE,
        sa.Column("operator_recovery_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        _TABLE,
        sa.Column("operator_recovery_scope_key", sa.String(200)),
    )
    op.add_column(
        _TABLE,
        sa.Column("historical_qualification_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(_TABLE, sa.Column("recovery_strategy", sa.String(80)))
    op.add_column(_TABLE, sa.Column("authority_versions", postgresql.JSONB()))
    op.add_column(_TABLE, sa.Column("freshness_snapshot", postgresql.JSONB()))
    op.add_column(_TABLE, sa.Column("operator_actor_context", postgresql.JSONB()))
    op.add_column(_TABLE, sa.Column("recovery_receipt_hash", sa.String(64)))
    op.create_foreign_key(
        "fk_recovery_historical_qualification",
        _TABLE,
        "script_qualification_runs",
        ["historical_qualification_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_script_replacement_recovery_id",
        _TABLE,
        ["operator_recovery_id"],
    )
    op.create_unique_constraint(
        "uq_script_replacement_recovery_scope",
        _TABLE,
        ["operator_recovery_scope_key"],
    )

    op.drop_constraint(
        "ck_script_contract_replacement_reason",
        _TABLE,
        type_="check",
    )
    op.create_check_constraint(
        "ck_script_contract_replacement_reason",
        _TABLE,
        "replacement_reason in ("
        "'SCRIPT_CONTRACT_SINGLE_SOURCE_OF_TRUTH_MIGRATION',"
        "'OPERATOR_REQUESTED_FIRST_VIDEO_RECOVERY')",
    )
    op.create_check_constraint(
        "ck_script_contract_operator_recovery_receipt",
        _TABLE,
        "replacement_reason <> 'OPERATOR_REQUESTED_FIRST_VIDEO_RECOVERY' or ("
        "operator_recovery_schema_version is not null and "
        "operator_recovery_id = id and "
        "operator_recovery_scope_key is not null and "
        "historical_qualification_id is not null and "
        "recovery_strategy = 'CANDIDATE_REPLACEMENT' and "
        "authority_versions is not null and "
        "freshness_snapshot is not null and "
        "operator_actor_context is not null and "
        "recovery_receipt_hash ~ '^[0-9a-f]{64}$')",
    )

    # 0067 permits exactly one null->fully-linked seal.  Include every new
    # receipt field in that equality guard so none can be changed while the
    # replacement candidate/slot/qualification IDs are attached atomically.
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
               AND NEW.operator_recovery_schema_version IS NOT DISTINCT FROM OLD.operator_recovery_schema_version
               AND NEW.operator_recovery_id IS NOT DISTINCT FROM OLD.operator_recovery_id
               AND NEW.operator_recovery_scope_key IS NOT DISTINCT FROM OLD.operator_recovery_scope_key
               AND NEW.historical_qualification_id IS NOT DISTINCT FROM OLD.historical_qualification_id
               AND NEW.recovery_strategy IS NOT DISTINCT FROM OLD.recovery_strategy
               AND NEW.authority_versions IS NOT DISTINCT FROM OLD.authority_versions
               AND NEW.freshness_snapshot IS NOT DISTINCT FROM OLD.freshness_snapshot
               AND NEW.operator_actor_context IS NOT DISTINCT FROM OLD.operator_actor_context
               AND NEW.recovery_receipt_hash IS NOT DISTINCT FROM OLD.recovery_receipt_hash
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


def downgrade() -> None:
    raise RuntimeError("0072 is intentionally forward-only in production")
