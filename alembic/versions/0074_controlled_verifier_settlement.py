"""Add append-only zero-provider verifier settlement authority.

Revision ID: 0074_verifier_settlement
Revises: 0073_controlled_continuation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0074_verifier_settlement"
down_revision: str | None = "0073_controlled_continuation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "controlled_verifier_settlement_authorities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "root_replacement_authority_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_continuation_authority_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_qualification_run_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("source_slot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "settlement_candidate_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("settlement_slot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "settlement_qualification_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_verifier_attempt_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "source_verifier_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "canonical_script_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("settlement_reason", sa.String(160), nullable=False),
        sa.Column("settlement_policy_version", sa.String(120), nullable=False),
        sa.Column("root_authority_hash", sa.String(64), nullable=False),
        sa.Column("source_continuation_authority_hash", sa.String(64), nullable=False),
        sa.Column("source_logical_identity_hash", sa.String(64), nullable=False),
        sa.Column("settlement_logical_identity_hash", sa.String(64), nullable=False),
        sa.Column("source_terminal_settlement_hash", sa.String(64), nullable=False),
        sa.Column("source_script_hash", sa.String(64), nullable=False),
        sa.Column("source_result_receipts_hash", sa.String(64), nullable=False),
        sa.Column("source_verifier_input_hash", sa.String(64), nullable=False),
        sa.Column("source_verifier_response_id", sa.String(200), nullable=False),
        sa.Column("source_verifier_request_id", sa.String(200), nullable=False),
        sa.Column("source_verifier_raw_response_hash", sa.String(64), nullable=False),
        sa.Column("source_verifier_raw_output_hash", sa.String(64), nullable=False),
        sa.Column("source_verifier_typed_output_hash", sa.String(64), nullable=False),
        sa.Column("source_verifier_schema_identifier", sa.String(160), nullable=False),
        sa.Column("source_verifier_schema_hash", sa.String(64), nullable=False),
        sa.Column("source_verifier_prompt_version", sa.String(120), nullable=False),
        sa.Column("derived_projection", postgresql.JSONB(), nullable=False),
        sa.Column("derived_projection_hash", sa.String(64), nullable=False),
        sa.Column("deadline_policy", postgresql.JSONB(), nullable=False),
        sa.Column("slot_projection", postgresql.JSONB(), nullable=False),
        sa.Column("current_authority_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("provider_authority_hash", sa.String(64), nullable=False),
        sa.Column("budget_authority_hash", sa.String(64), nullable=False),
        sa.Column("max_provider_submissions", sa.Integer(), nullable=False),
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
            "schema_version = 'vcos.controlled-verifier-settlement.v1' and "
            "settlement_reason = 'EXACT_VERIFIER_ARTIFACT_POLICY_PROJECTION' and "
            "settlement_policy_version = 'script-qualification-policy.v3'",
            name="ck_controlled_verifier_settlement_identity",
        ),
        sa.CheckConstraint(
            "max_provider_submissions = 0 and qualification_deadline < production_window_end",
            name="ck_controlled_verifier_settlement_bounds",
        ),
        sa.CheckConstraint(
            "root_authority_hash ~ '^[0-9a-f]{64}$' and "
            "source_continuation_authority_hash ~ '^[0-9a-f]{64}$' and "
            "source_logical_identity_hash ~ '^[0-9a-f]{64}$' and "
            "settlement_logical_identity_hash ~ '^[0-9a-f]{64}$' and "
            "source_terminal_settlement_hash ~ '^[0-9a-f]{64}$' and "
            "source_script_hash ~ '^[0-9a-f]{64}$' and "
            "source_result_receipts_hash ~ '^[0-9a-f]{64}$' and "
            "source_verifier_input_hash ~ '^[0-9a-f]{64}$' and "
            "source_verifier_raw_response_hash ~ '^[0-9a-f]{64}$' and "
            "source_verifier_raw_output_hash ~ '^[0-9a-f]{64}$' and "
            "source_verifier_typed_output_hash ~ '^[0-9a-f]{64}$' and "
            "source_verifier_schema_hash ~ '^[0-9a-f]{64}$' and "
            "derived_projection_hash ~ '^[0-9a-f]{64}$' and "
            "provider_authority_hash ~ '^[0-9a-f]{64}$' and "
            "budget_authority_hash ~ '^[0-9a-f]{64}$' and "
            "authority_hash ~ '^[0-9a-f]{64}$'",
            name="ck_controlled_verifier_settlement_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["root_replacement_authority_id"],
            ["script_contract_replacement_authorities.id"],
            name="fk_verifier_settlement_root",
        ),
        sa.ForeignKeyConstraint(
            ["source_continuation_authority_id"],
            ["controlled_production_continuation_authorities.id"],
            name="fk_verifier_settlement_continuation",
        ),
        sa.ForeignKeyConstraint(
            ["source_qualification_run_id"],
            ["script_qualification_runs.id"],
            name="fk_verifier_settlement_source",
        ),
        sa.ForeignKeyConstraint(
            ["source_slot_id"],
            ["long_form_publish_slots.id"],
            name="fk_verifier_settlement_source_slot",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_candidate_id"],
            ["editorial_idea_candidates.id"],
            name="fk_verifier_settlement_candidate",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_slot_id"],
            ["long_form_publish_slots.id"],
            name="fk_verifier_settlement_slot",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_qualification_run_id"],
            ["script_qualification_runs.id"],
            name="fk_verifier_settlement_run",
        ),
        sa.ForeignKeyConstraint(
            ["source_verifier_attempt_id"],
            ["script_qualification_background_attempts.id"],
            name="fk_verifier_settlement_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["source_verifier_snapshot_id"],
            ["script_qualification_provider_response_snapshots.id"],
            name="fk_verifier_settlement_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_script_artifact_id"],
            ["canonical_script_artifacts.id"],
            name="fk_verifier_settlement_artifact",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "root_replacement_authority_id", name="uq_verifier_settlement_root"
        ),
        sa.UniqueConstraint(
            "source_continuation_authority_id",
            name="uq_verifier_settlement_continuation",
        ),
        sa.UniqueConstraint(
            "source_qualification_run_id", name="uq_verifier_settlement_source"
        ),
        sa.UniqueConstraint("settlement_slot_id", name="uq_verifier_settlement_slot"),
        sa.UniqueConstraint(
            "settlement_qualification_run_id", name="uq_verifier_settlement_run"
        ),
        sa.UniqueConstraint(
            "source_verifier_attempt_id", name="uq_verifier_settlement_attempt"
        ),
        sa.UniqueConstraint(
            "source_verifier_snapshot_id", name="uq_verifier_settlement_snapshot"
        ),
        sa.UniqueConstraint(
            "canonical_script_artifact_id", name="uq_verifier_settlement_artifact"
        ),
    )
    op.create_index(
        "ix_controlled_verifier_settlement_root",
        "controlled_verifier_settlement_authorities",
        ["root_replacement_authority_id"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_controlled_verifier_settlement_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'controlled verifier settlement authorities are immutable';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_controlled_verifier_settlement_immutable
        BEFORE UPDATE OR DELETE ON controlled_verifier_settlement_authorities
        FOR EACH ROW EXECUTE FUNCTION prevent_controlled_verifier_settlement_mutation();
        """
    )
    op.execute(
        """
        CREATE FUNCTION seal_controlled_verifier_settlement()
        RETURNS trigger AS $$
        DECLARE
            root_authority script_contract_replacement_authorities%ROWTYPE;
            continuation_authority controlled_production_continuation_authorities%ROWTYPE;
            source_run script_qualification_runs%ROWTYPE;
            child_run script_qualification_runs%ROWTYPE;
            source_slot long_form_publish_slots%ROWTYPE;
            child_slot long_form_publish_slots%ROWTYPE;
            candidate editorial_idea_candidates%ROWTYPE;
            source_attempt script_qualification_background_attempts%ROWTYPE;
            source_snapshot script_qualification_provider_response_snapshots%ROWTYPE;
            source_artifact canonical_script_artifacts%ROWTYPE;
            child_artifact canonical_script_artifacts%ROWTYPE;
            source_receipt script_qualification_receipts%ROWTYPE;
            child_receipt script_qualification_receipts%ROWTYPE;
            pass_receipt_count integer;
            child_attempt_count integer;
            child_snapshot_count integer;
            source_attempt_count integer;
            source_snapshot_count integer;
        BEGIN
            -- Serialize the zero-provider seal with every attempt/snapshot
            -- insertion for this child.  Without the shared advisory lock two
            -- READ COMMITTED transactions could both observe the other row as
            -- absent and commit a provider attempt beside this authority.
            PERFORM pg_advisory_xact_lock(
                hashtextextended(NEW.settlement_qualification_run_id::text, 7401)
            );

            SELECT * INTO root_authority FROM script_contract_replacement_authorities
              WHERE id = NEW.root_replacement_authority_id;
            SELECT * INTO continuation_authority FROM controlled_production_continuation_authorities
              WHERE id = NEW.source_continuation_authority_id;
            SELECT * INTO source_run FROM script_qualification_runs
              WHERE id = NEW.source_qualification_run_id;
            SELECT * INTO child_run FROM script_qualification_runs
              WHERE id = NEW.settlement_qualification_run_id;
            SELECT * INTO source_slot FROM long_form_publish_slots WHERE id = NEW.source_slot_id;
            SELECT * INTO child_slot FROM long_form_publish_slots WHERE id = NEW.settlement_slot_id;
            SELECT * INTO candidate FROM editorial_idea_candidates
              WHERE id = NEW.settlement_candidate_id;
            SELECT * INTO source_attempt FROM script_qualification_background_attempts
              WHERE id = NEW.source_verifier_attempt_id;
            SELECT * INTO source_snapshot FROM script_qualification_provider_response_snapshots
              WHERE id = NEW.source_verifier_snapshot_id;
            SELECT * INTO source_artifact FROM canonical_script_artifacts
              WHERE id = source_run.canonical_script_artifact_id;
            SELECT * INTO child_artifact FROM canonical_script_artifacts
              WHERE id = NEW.canonical_script_artifact_id;
            SELECT * INTO source_receipt FROM script_qualification_receipts
              WHERE script_qualification_run_id = NEW.source_qualification_run_id;
            SELECT * INTO child_receipt FROM script_qualification_receipts
              WHERE script_qualification_run_id = NEW.settlement_qualification_run_id;
            SELECT count(*) INTO pass_receipt_count FROM script_qualification_receipts
              WHERE script_qualification_run_id = NEW.settlement_qualification_run_id AND result = 'PASS';
            SELECT count(*) INTO child_attempt_count FROM script_qualification_background_attempts
              WHERE script_qualification_run_id = NEW.settlement_qualification_run_id;
            SELECT count(*) INTO child_snapshot_count FROM script_qualification_provider_response_snapshots
              WHERE script_qualification_run_id = NEW.settlement_qualification_run_id;
            SELECT count(*) INTO source_attempt_count FROM script_qualification_background_attempts
              WHERE script_qualification_run_id = NEW.source_qualification_run_id;
            SELECT count(*) INTO source_snapshot_count FROM script_qualification_provider_response_snapshots
              WHERE script_qualification_run_id = NEW.source_qualification_run_id;

            IF root_authority.id IS NULL OR continuation_authority.id IS NULL
               OR source_run.id IS NULL OR child_run.id IS NULL
               OR source_slot.id IS NULL OR child_slot.id IS NULL OR candidate.id IS NULL
               OR source_attempt.id IS NULL OR source_snapshot.id IS NULL
               OR source_artifact.id IS NULL OR child_artifact.id IS NULL
               OR source_receipt.id IS NULL OR child_receipt.id IS NULL
               OR root_authority.authority_hash IS DISTINCT FROM NEW.root_authority_hash
               OR root_authority.replacement_candidate_id IS DISTINCT FROM NEW.settlement_candidate_id
               OR continuation_authority.root_replacement_authority_id IS DISTINCT FROM NEW.root_replacement_authority_id
               OR continuation_authority.continuation_qualification_run_id IS DISTINCT FROM NEW.source_qualification_run_id
               OR continuation_authority.continuation_slot_id IS DISTINCT FROM NEW.source_slot_id
               OR continuation_authority.continuation_candidate_id IS DISTINCT FROM NEW.settlement_candidate_id
               OR continuation_authority.root_authority_hash IS DISTINCT FROM root_authority.authority_hash
               OR continuation_authority.continuation_logical_identity_hash IS DISTINCT FROM source_run.logical_identity_hash
               OR continuation_authority.qualification_deadline IS DISTINCT FROM source_run.logical_deadline_at
               OR continuation_authority.authority_hash IS DISTINCT FROM NEW.source_continuation_authority_hash
               OR source_run.state IS DISTINCT FROM 'BLOCKED_NON_REPAIRABLE'
               OR source_run.publish_slot_id IS DISTINCT FROM NEW.source_slot_id
               OR source_run.editorial_idea_candidate_id IS DISTINCT FROM NEW.settlement_candidate_id
               OR source_run.replacement_authority_id IS DISTINCT FROM NEW.root_replacement_authority_id
               OR source_run.logical_identity_hash IS DISTINCT FROM NEW.source_logical_identity_hash
               OR source_run.script_contract_version IS DISTINCT FROM 'V2_SINGLE_SOURCE'
               OR source_run.gate_policy_version IS DISTINCT FROM 'script-qualification-policy.v2'
               OR source_run.derived_canonical_script_hash IS DISTINCT FROM NEW.source_script_hash
               OR source_run.canonical_script_artifact_id IS NULL
               OR source_run.result_receipts IS NULL
               OR source_run.terminal_settlement_receipt->>'content_hash' IS DISTINCT FROM NEW.source_terminal_settlement_hash
               OR source_run.verifier_receipt->>'provider_response_id' IS DISTINCT FROM NEW.source_verifier_response_id
               OR source_run.verifier_receipt->>'provider_request_id' IS DISTINCT FROM NEW.source_verifier_request_id
               OR source_run.verifier_receipt->>'input_fingerprint' IS DISTINCT FROM NEW.source_verifier_input_hash
               OR source_run.verifier_receipt->>'response_schema_identifier' IS DISTINCT FROM NEW.source_verifier_schema_identifier
               OR source_run.verifier_receipt->>'response_schema_hash' IS DISTINCT FROM NEW.source_verifier_schema_hash
               OR source_run.verifier_receipt->>'prompt_version' IS DISTINCT FROM NEW.source_verifier_prompt_version
               OR source_slot.state IS DISTINCT FROM 'CANCELED'
               OR source_slot.reserved_candidate_id IS DISTINCT FROM NEW.settlement_candidate_id
               OR source_slot.admitted_video_project_id IS NOT NULL
               OR candidate.stage IS DISTINCT FROM 'REJECTED'
               OR child_run.state IS DISTINCT FROM 'QUALIFIED'
               OR child_run.supersedes_qualification_run_id IS DISTINCT FROM NEW.source_qualification_run_id
               OR child_run.publish_slot_id IS DISTINCT FROM NEW.settlement_slot_id
               OR child_run.editorial_idea_candidate_id IS DISTINCT FROM NEW.settlement_candidate_id
               OR child_run.replacement_authority_id IS DISTINCT FROM NEW.root_replacement_authority_id
               OR child_run.logical_identity_hash IS DISTINCT FROM NEW.settlement_logical_identity_hash
               OR child_run.logical_deadline_at IS DISTINCT FROM NEW.qualification_deadline
               OR child_run.gate_policy_version IS DISTINCT FROM NEW.settlement_policy_version
               OR child_run.script_contract_version IS DISTINCT FROM source_run.script_contract_version
               OR child_run.canonical_script_artifact_id IS DISTINCT FROM NEW.canonical_script_artifact_id
               OR child_run.derived_canonical_script_hash IS DISTINCT FROM NEW.source_script_hash
               OR child_run.script_payload IS DISTINCT FROM source_run.script_payload
               OR child_run.script_assignment IS DISTINCT FROM source_run.script_assignment
               OR child_run.script_assignment_hash IS DISTINCT FROM source_run.script_assignment_hash
               OR child_run.factual_evidence_pack IS DISTINCT FROM source_run.factual_evidence_pack
               OR child_run.factual_evidence_pack_hash IS DISTINCT FROM source_run.factual_evidence_pack_hash
               OR child_run.memory_digest IS DISTINCT FROM source_run.memory_digest
               OR child_run.memory_digest_hash IS DISTINCT FROM source_run.memory_digest_hash
               OR child_run.runtime_contract IS DISTINCT FROM source_run.runtime_contract
               OR child_run.runtime_contract_hash IS DISTINCT FROM source_run.runtime_contract_hash
               OR child_run.assignment_resolution IS DISTINCT FROM source_run.assignment_resolution
               OR child_run.assignment_resolution_hash IS DISTINCT FROM source_run.assignment_resolution_hash
               OR child_run.writer_prompt_version IS DISTINCT FROM source_run.writer_prompt_version
               OR child_run.verifier_prompt_version IS DISTINCT FROM source_run.verifier_prompt_version
               OR child_run.model IS DISTINCT FROM source_run.model
               OR child_run.logical_attempt_number IS DISTINCT FROM source_run.logical_attempt_number + 1
               OR child_run.repair_attempts IS DISTINCT FROM source_run.repair_attempts
               OR child_run.episode_reservation_active
               OR child_run.admitted_video_project_id IS NOT NULL
               OR child_run.production_workflow_run_id IS NOT NULL
               OR child_run.failure_receipt IS NOT NULL
               OR child_run.terminal_settlement_receipt IS NOT NULL
               OR child_slot.state IS DISTINCT FROM 'QUALIFICATION_RESERVED'
               OR child_slot.reserved_candidate_id IS DISTINCT FROM NEW.settlement_candidate_id
               OR child_slot.replaces_slot_id IS DISTINCT FROM NEW.source_slot_id
               OR child_slot.replacement_authority_id IS DISTINCT FROM NEW.root_replacement_authority_id
               OR child_slot.replacement_reason IS DISTINCT FROM NEW.settlement_reason
               OR child_slot.admitted_video_project_id IS NOT NULL
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
               OR source_attempt.script_qualification_run_id IS DISTINCT FROM NEW.source_qualification_run_id
               OR source_attempt.phase IS DISTINCT FROM 'VERIFIER'
               OR source_attempt.provider IS DISTINCT FROM 'OPENAI'
               OR source_attempt.background_status IS DISTINCT FROM 'COMPLETED'
               OR source_attempt.submission_attempt_count IS DISTINCT FROM 1
               OR source_attempt.input_fingerprint IS DISTINCT FROM NEW.source_verifier_input_hash
               OR source_attempt.provider_response_id IS DISTINCT FROM NEW.source_verifier_response_id
               OR source_attempt.provider_request_id IS DISTINCT FROM NEW.source_verifier_request_id
               OR source_attempt.response_schema_identifier IS DISTINCT FROM NEW.source_verifier_schema_identifier
               OR source_attempt.response_schema_hash IS DISTINCT FROM NEW.source_verifier_schema_hash
               OR source_attempt.prompt_version IS DISTINCT FROM NEW.source_verifier_prompt_version
               OR source_attempt.output_hash IS DISTINCT FROM NEW.source_verifier_raw_response_hash
               OR source_snapshot.background_attempt_id IS DISTINCT FROM NEW.source_verifier_attempt_id
               OR source_snapshot.script_qualification_run_id IS DISTINCT FROM NEW.source_qualification_run_id
               OR source_snapshot.phase IS DISTINCT FROM 'VERIFIER'
               OR source_snapshot.provider_response_id IS DISTINCT FROM NEW.source_verifier_response_id
               OR source_snapshot.provider_request_id IS DISTINCT FROM NEW.source_verifier_request_id
               OR source_snapshot.producer_input_hash IS DISTINCT FROM NEW.source_verifier_input_hash
               OR source_snapshot.raw_provider_response_hash IS DISTINCT FROM NEW.source_verifier_raw_response_hash
               OR source_snapshot.raw_output_hash IS DISTINCT FROM NEW.source_verifier_raw_output_hash
               OR source_snapshot.accepted_typed_output_hash IS DISTINCT FROM NEW.source_verifier_typed_output_hash
               OR source_snapshot.response_schema_identifier IS DISTINCT FROM NEW.source_verifier_schema_identifier
               OR source_snapshot.response_schema_hash IS DISTINCT FROM NEW.source_verifier_schema_hash
               OR source_snapshot.prompt_version IS DISTINCT FROM NEW.source_verifier_prompt_version
               OR source_snapshot.validation_errors IS DISTINCT FROM '[]'::jsonb
               OR source_artifact.script_qualification_run_id IS DISTINCT FROM source_run.id
               OR source_artifact.canonical_script_hash IS DISTINCT FROM NEW.source_script_hash
               OR child_artifact.script_qualification_run_id IS DISTINCT FROM child_run.id
               OR child_artifact.script_contract_version IS DISTINCT FROM source_artifact.script_contract_version
               OR child_artifact.compiler_version IS DISTINCT FROM source_artifact.compiler_version
               OR child_artifact.ordered_section_ids IS DISTINCT FROM source_artifact.ordered_section_ids
               OR child_artifact.ordered_section_hashes IS DISTINCT FROM source_artifact.ordered_section_hashes
               OR child_artifact.separator_policy IS DISTINCT FROM source_artifact.separator_policy
               OR child_artifact.normalization_policy IS DISTINCT FROM source_artifact.normalization_policy
               OR child_artifact.section_set_hash IS DISTINCT FROM source_artifact.section_set_hash
               OR child_artifact.canonical_script IS DISTINCT FROM source_artifact.canonical_script
               OR child_artifact.canonical_script_hash IS DISTINCT FROM NEW.source_script_hash
               OR child_artifact.total_word_count IS DISTINCT FROM source_artifact.total_word_count
               OR child_artifact.estimated_duration_ms IS DISTINCT FROM source_artifact.estimated_duration_ms
               OR source_receipt.result IS DISTINCT FROM 'BLOCK'
               OR source_receipt.script_hash IS DISTINCT FROM NEW.source_script_hash
               OR source_receipt.content->'receipts' IS DISTINCT FROM source_run.result_receipts
               OR source_receipt.content->'factual_evidence_pack' IS DISTINCT FROM source_run.factual_evidence_pack
               OR source_receipt.content->'memory_digest' IS DISTINCT FROM source_run.memory_digest
               OR source_receipt.content->'runtime_contract' IS DISTINCT FROM source_run.runtime_contract
               OR source_receipt.content->'assignment_resolution' IS DISTINCT FROM source_run.assignment_resolution
               OR source_receipt.content->'producer_provenance'->'writer' IS DISTINCT FROM source_run.writer_receipt
               OR source_receipt.content->'producer_provenance'->'verifier' IS DISTINCT FROM source_run.verifier_receipt
               OR ((source_receipt.content->'qualified_script') - ARRAY['canonical_script_artifact', 'script_contract_version']) IS DISTINCT FROM source_run.script_payload
               OR source_receipt.content->'qualified_script'->>'script_contract_version' IS DISTINCT FROM source_run.script_contract_version
               OR source_receipt.content->'qualified_script'->'canonical_script_artifact'->>'id' IS DISTINCT FROM source_artifact.id::text
               OR source_receipt.content->'qualified_script'->'canonical_script_artifact'->>'compiler_version' IS DISTINCT FROM source_artifact.compiler_version
               OR source_receipt.content->'qualified_script'->'canonical_script_artifact'->'ordered_section_ids' IS DISTINCT FROM source_artifact.ordered_section_ids
               OR source_receipt.content->'qualified_script'->'canonical_script_artifact'->'ordered_section_hashes' IS DISTINCT FROM source_artifact.ordered_section_hashes
               OR source_receipt.content->'qualified_script'->'canonical_script_artifact'->>'separator_policy' IS DISTINCT FROM source_artifact.separator_policy
               OR source_receipt.content->'qualified_script'->'canonical_script_artifact'->>'normalization_policy' IS DISTINCT FROM source_artifact.normalization_policy
               OR source_receipt.content->'qualified_script'->'canonical_script_artifact'->>'section_set_hash' IS DISTINCT FROM source_artifact.section_set_hash
               OR source_receipt.content->'qualified_script'->'canonical_script_artifact'->>'canonical_script' IS DISTINCT FROM source_artifact.canonical_script
               OR source_receipt.content->'qualified_script'->'canonical_script_artifact'->>'canonical_script_hash' IS DISTINCT FROM source_artifact.canonical_script_hash
               OR (source_receipt.content->'qualified_script'->'canonical_script_artifact'->>'total_word_count')::integer IS DISTINCT FROM source_artifact.total_word_count
               OR (source_receipt.content->'qualified_script'->'canonical_script_artifact'->>'estimated_duration_ms')::integer IS DISTINCT FROM source_artifact.estimated_duration_ms
               OR (source_receipt.content->'qualified_script'->'canonical_script_artifact'->>'compiled_at')::timestamptz IS DISTINCT FROM source_artifact.compiled_at
               OR child_receipt.result IS DISTINCT FROM 'PASS'
               OR child_receipt.script_hash IS DISTINCT FROM NEW.source_script_hash
               OR child_receipt.script_assignment_hash IS DISTINCT FROM child_run.script_assignment_hash
               OR child_receipt.factual_evidence_pack_hash IS DISTINCT FROM child_run.factual_evidence_pack_hash
               OR child_receipt.content->'receipts' IS DISTINCT FROM child_run.result_receipts
               OR child_receipt.content->>'run_id' IS DISTINCT FROM child_run.id::text
               OR child_receipt.content->>'result' IS DISTINCT FROM 'PASS'
               OR child_receipt.content->'factual_evidence_pack' IS DISTINCT FROM child_run.factual_evidence_pack
               OR child_receipt.content->'memory_digest' IS DISTINCT FROM child_run.memory_digest
               OR child_receipt.content->'runtime_contract' IS DISTINCT FROM child_run.runtime_contract
               OR child_receipt.content->'assignment_resolution' IS DISTINCT FROM child_run.assignment_resolution
               OR child_receipt.content->'producer_provenance'->'writer' IS DISTINCT FROM child_run.writer_receipt
               OR child_receipt.content->'producer_provenance'->'verifier' IS DISTINCT FROM child_run.verifier_receipt
               OR ((child_receipt.content->'qualified_script') - ARRAY['canonical_script_artifact', 'script_contract_version']) IS DISTINCT FROM child_run.script_payload
               OR child_receipt.content->'qualified_script'->>'script_contract_version' IS DISTINCT FROM child_run.script_contract_version
               OR child_receipt.content->'qualified_script'->'canonical_script_artifact'->>'id' IS DISTINCT FROM child_artifact.id::text
               OR child_receipt.content->'qualified_script'->'canonical_script_artifact'->>'compiler_version' IS DISTINCT FROM child_artifact.compiler_version
               OR child_receipt.content->'qualified_script'->'canonical_script_artifact'->'ordered_section_ids' IS DISTINCT FROM child_artifact.ordered_section_ids
               OR child_receipt.content->'qualified_script'->'canonical_script_artifact'->'ordered_section_hashes' IS DISTINCT FROM child_artifact.ordered_section_hashes
               OR child_receipt.content->'qualified_script'->'canonical_script_artifact'->>'separator_policy' IS DISTINCT FROM child_artifact.separator_policy
               OR child_receipt.content->'qualified_script'->'canonical_script_artifact'->>'normalization_policy' IS DISTINCT FROM child_artifact.normalization_policy
               OR child_receipt.content->'qualified_script'->'canonical_script_artifact'->>'section_set_hash' IS DISTINCT FROM child_artifact.section_set_hash
               OR child_receipt.content->'qualified_script'->'canonical_script_artifact'->>'canonical_script' IS DISTINCT FROM child_artifact.canonical_script
               OR child_receipt.content->'qualified_script'->'canonical_script_artifact'->>'canonical_script_hash' IS DISTINCT FROM child_artifact.canonical_script_hash
               OR (child_receipt.content->'qualified_script'->'canonical_script_artifact'->>'total_word_count')::integer IS DISTINCT FROM child_artifact.total_word_count
               OR (child_receipt.content->'qualified_script'->'canonical_script_artifact'->>'estimated_duration_ms')::integer IS DISTINCT FROM child_artifact.estimated_duration_ms
               OR (child_receipt.content->'qualified_script'->'canonical_script_artifact'->>'compiled_at')::timestamptz IS DISTINCT FROM child_artifact.compiled_at
               OR child_run.writer_receipt->>'producer' IS DISTINCT FROM 'DERIVED_FROM_COMPLETED_VERIFIER_SETTLEMENT'
               OR child_run.writer_receipt->>'producer_type' IS DISTINCT FROM 'OPENAI_BACKGROUND_VERIFIER_SETTLEMENT'
               OR child_run.writer_receipt->>'settlement_source_qualification_run_id' IS DISTINCT FROM source_run.id::text
               OR child_run.writer_receipt->>'settlement_source_verifier_attempt_id' IS DISTINCT FROM source_attempt.id::text
               OR child_run.writer_receipt->>'settlement_source_verifier_snapshot_id' IS DISTINCT FROM source_snapshot.id::text
               OR child_run.writer_receipt->>'settlement_authority_id' IS DISTINCT FROM NEW.id::text
               OR child_run.writer_receipt->>'settlement_authority_hash' IS DISTINCT FROM NEW.authority_hash
               OR child_run.writer_receipt->>'settlement_projection_hash' IS DISTINCT FROM NEW.derived_projection_hash
               OR (child_run.writer_receipt->>'provider_submission_count_for_settlement')::integer IS DISTINCT FROM 0
               OR (child_run.writer_receipt - ARRAY[
                    'producer',
                    'producer_type',
                    'settlement_source_qualification_run_id',
                    'settlement_source_verifier_attempt_id',
                    'settlement_source_verifier_snapshot_id',
                    'settlement_authority_id',
                    'settlement_authority_hash',
                    'settlement_projection_hash',
                    'provider_submission_count_for_settlement'
                  ]) IS DISTINCT FROM (source_run.writer_receipt - ARRAY['producer', 'producer_type'])
               OR child_run.verifier_receipt->>'settlement_authority_id' IS DISTINCT FROM NEW.id::text
               OR child_run.verifier_receipt->>'settlement_authority_hash' IS DISTINCT FROM NEW.authority_hash
               OR child_run.verifier_receipt->>'settlement_source_qualification_run_id' IS DISTINCT FROM source_run.id::text
               OR child_run.verifier_receipt->>'settlement_source_verifier_snapshot_id' IS DISTINCT FROM source_snapshot.id::text
               OR child_run.verifier_receipt->>'settlement_policy_version' IS DISTINCT FROM NEW.settlement_policy_version
               OR child_run.verifier_receipt->>'derived_projection_hash' IS DISTINCT FROM NEW.derived_projection_hash
               OR (child_run.verifier_receipt->>'provider_submission_count_for_settlement')::integer IS DISTINCT FROM 0
               OR (child_run.verifier_receipt - ARRAY[
                    'settlement_authority_id',
                    'settlement_authority_hash',
                    'settlement_source_qualification_run_id',
                    'settlement_source_verifier_snapshot_id',
                    'settlement_policy_version',
                    'derived_projection_hash',
                    'provider_submission_count_for_settlement'
                  ]) IS DISTINCT FROM source_run.verifier_receipt
               OR NEW.derived_projection->>'schema_version' IS DISTINCT FROM 'script-verifier-settlement-projection.v1'
               OR NEW.derived_projection->>'policy_version' IS DISTINCT FROM NEW.settlement_policy_version
               OR NEW.derived_projection->>'source_qualification_run_id' IS DISTINCT FROM source_run.id::text
               OR NEW.derived_projection->>'source_verifier_output_hash' IS DISTINCT FROM NEW.source_verifier_typed_output_hash
               OR NEW.derived_projection->>'source_result_receipts_hash' IS DISTINCT FROM NEW.source_result_receipts_hash
               OR NEW.derived_projection->>'content_hash' IS DISTINCT FROM NEW.derived_projection_hash
               OR jsonb_typeof(NEW.derived_projection->'claim_anchor_decisions') IS DISTINCT FROM 'array'
               OR jsonb_array_length(NEW.derived_projection->'claim_anchor_decisions') < 1
               OR jsonb_typeof(NEW.derived_projection->'removed_fulfillment_spans') IS DISTINCT FROM 'array'
               OR jsonb_array_length(NEW.derived_projection->'removed_fulfillment_spans') < 1
               OR jsonb_typeof(NEW.deadline_policy) IS DISTINCT FROM 'object'
               OR NEW.deadline_policy->>'schema_version' IS DISTINCT FROM 'script-qualification-deadline-policy.v1'
               OR (NEW.deadline_policy->>'downstream_lead_seconds')::integer < 0
               OR NEW.qualification_deadline IS DISTINCT FROM NEW.production_window_end -
                    make_interval(secs => (NEW.deadline_policy->>'downstream_lead_seconds')::integer)
               OR pass_receipt_count IS DISTINCT FROM 1
               OR child_attempt_count IS DISTINCT FROM 0
               OR child_snapshot_count IS DISTINCT FROM 0
               OR source_attempt_count IS DISTINCT FROM 1
               OR source_snapshot_count IS DISTINCT FROM 1
            THEN
                RAISE EXCEPTION 'controlled verifier settlement seal mismatch';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_controlled_verifier_settlement_seal
        BEFORE INSERT ON controlled_verifier_settlement_authorities
        FOR EACH ROW EXECUTE FUNCTION seal_controlled_verifier_settlement();
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_settlement_provider_attempt()
        RETURNS trigger AS $$
        DECLARE
            affected_run_id uuid;
            old_lock_key bigint;
            new_lock_key bigint;
        BEGIN
            affected_run_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.script_qualification_run_id
                ELSE NEW.script_qualification_run_id END;
            IF TG_OP = 'UPDATE' THEN
                old_lock_key := hashtextextended(
                    OLD.script_qualification_run_id::text, 7401
                );
                new_lock_key := hashtextextended(
                    NEW.script_qualification_run_id::text, 7401
                );
                PERFORM pg_advisory_xact_lock(LEAST(old_lock_key, new_lock_key));
                IF old_lock_key IS DISTINCT FROM new_lock_key THEN
                    PERFORM pg_advisory_xact_lock(GREATEST(old_lock_key, new_lock_key));
                END IF;
            ELSE
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(affected_run_id::text, 7401)
                );
            END IF;

            IF TG_OP IN ('UPDATE', 'DELETE') AND EXISTS (
                SELECT 1 FROM controlled_verifier_settlement_authorities
                WHERE source_verifier_attempt_id = OLD.id
                   OR settlement_qualification_run_id = OLD.script_qualification_run_id
            ) THEN
                RAISE EXCEPTION 'controlled verifier settlement attempt evidence is immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            IF EXISTS (
                SELECT 1 FROM controlled_verifier_settlement_authorities
                WHERE settlement_qualification_run_id = NEW.script_qualification_run_id
            ) THEN
                RAISE EXCEPTION 'controlled verifier settlement forbids provider submissions';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_reject_settlement_provider_attempt
        BEFORE INSERT OR UPDATE OR DELETE ON script_qualification_background_attempts
        FOR EACH ROW EXECUTE FUNCTION reject_settlement_provider_attempt();
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_settlement_provider_snapshot()
        RETURNS trigger AS $$
        DECLARE
            affected_run_id uuid;
            old_lock_key bigint;
            new_lock_key bigint;
        BEGIN
            affected_run_id := CASE WHEN TG_OP = 'DELETE'
                THEN OLD.script_qualification_run_id
                ELSE NEW.script_qualification_run_id END;
            IF TG_OP = 'UPDATE' THEN
                old_lock_key := hashtextextended(
                    OLD.script_qualification_run_id::text, 7401
                );
                new_lock_key := hashtextextended(
                    NEW.script_qualification_run_id::text, 7401
                );
                PERFORM pg_advisory_xact_lock(LEAST(old_lock_key, new_lock_key));
                IF old_lock_key IS DISTINCT FROM new_lock_key THEN
                    PERFORM pg_advisory_xact_lock(GREATEST(old_lock_key, new_lock_key));
                END IF;
            ELSE
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(affected_run_id::text, 7401)
                );
            END IF;

            IF TG_OP IN ('UPDATE', 'DELETE') AND EXISTS (
                SELECT 1 FROM controlled_verifier_settlement_authorities
                WHERE source_verifier_snapshot_id = OLD.id
                   OR settlement_qualification_run_id = OLD.script_qualification_run_id
            ) THEN
                RAISE EXCEPTION 'controlled verifier settlement snapshot evidence is immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            IF EXISTS (
                SELECT 1 FROM controlled_verifier_settlement_authorities
                WHERE settlement_qualification_run_id = NEW.script_qualification_run_id
            ) THEN
                RAISE EXCEPTION 'controlled verifier settlement forbids provider snapshots';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_protect_settlement_provider_snapshot
        BEFORE INSERT OR UPDATE OR DELETE ON script_qualification_provider_response_snapshots
        FOR EACH ROW EXECUTE FUNCTION protect_settlement_provider_snapshot();
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_controlled_verifier_settlement_run()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM controlled_verifier_settlement_authorities
                WHERE source_qualification_run_id = OLD.id
            ) THEN
                RAISE EXCEPTION 'controlled verifier settlement source run is immutable';
            END IF;
            IF EXISTS (
                SELECT 1 FROM controlled_verifier_settlement_authorities
                WHERE settlement_qualification_run_id = OLD.id
            ) AND (
                TG_OP = 'DELETE'
                OR to_jsonb(NEW) - ARRAY[
                    'admitted_video_project_id',
                    'production_workflow_run_id',
                    'updated_at'
                ] IS DISTINCT FROM to_jsonb(OLD) - ARRAY[
                    'admitted_video_project_id',
                    'production_workflow_run_id',
                    'updated_at'
                ]
            ) THEN
                RAISE EXCEPTION 'controlled verifier settlement qualified run evidence is immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_protect_controlled_verifier_settlement_run
        BEFORE UPDATE OR DELETE ON script_qualification_runs
        FOR EACH ROW EXECUTE FUNCTION protect_controlled_verifier_settlement_run();
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_controlled_verifier_settlement_receipt()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM controlled_verifier_settlement_authorities
                WHERE source_qualification_run_id = OLD.script_qualification_run_id
                   OR settlement_qualification_run_id = OLD.script_qualification_run_id
            ) THEN
                RAISE EXCEPTION 'controlled verifier settlement qualification receipt is immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_protect_controlled_verifier_settlement_receipt
        BEFORE UPDATE OR DELETE ON script_qualification_receipts
        FOR EACH ROW EXECUTE FUNCTION protect_controlled_verifier_settlement_receipt();
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_controlled_verifier_settlement_slot()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM controlled_verifier_settlement_authorities
                WHERE source_slot_id = OLD.id
            ) THEN
                RAISE EXCEPTION 'controlled verifier settlement source slot is immutable';
            END IF;
            IF EXISTS (
                SELECT 1 FROM controlled_verifier_settlement_authorities
                WHERE settlement_slot_id = OLD.id
            ) AND (
                TG_OP = 'DELETE'
                OR to_jsonb(NEW) - ARRAY[
                    'state',
                    'admitted_video_project_id',
                    'updated_at'
                ] IS DISTINCT FROM to_jsonb(OLD) - ARRAY[
                    'state',
                    'admitted_video_project_id',
                    'updated_at'
                ]
            ) THEN
                RAISE EXCEPTION 'controlled verifier settlement slot authority is immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_protect_controlled_verifier_settlement_slot
        BEFORE UPDATE OR DELETE ON long_form_publish_slots
        FOR EACH ROW EXECUTE FUNCTION protect_controlled_verifier_settlement_slot();
        """
    )


def downgrade() -> None:
    raise RuntimeError("0074 is intentionally forward-only in production")
