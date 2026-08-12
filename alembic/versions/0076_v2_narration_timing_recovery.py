"""Seal one no-TTS-retry V2 narration timing recovery.

Revision ID: 0076_v2_timing_recovery
Revises: 0075_review_only_destination
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0076_v2_timing_recovery"
down_revision: str | None = "0075_review_only_destination"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "v2_narration_timing_recovery_authorities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "media_effect_ledger_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "media_domain_event_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "media_dead_letter_job_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "root_replacement_authority_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "verifier_settlement_authority_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "settlement_qualification_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "production_package_artifact_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("production_package_hash", sa.String(64), nullable=False),
        sa.Column(
            "script_artifact_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("script_content_hash", sa.String(64), nullable=False),
        sa.Column("approved_script_hash", sa.String(64), nullable=False),
        sa.Column(
            "budget_reservation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("budget_reservation_ref", sa.Text(), nullable=False),
        sa.Column("budget_authority_hash", sa.String(64), nullable=False),
        sa.Column("provider_policy_hash", sa.String(64), nullable=False),
        sa.Column("tts_request_journal_ref", sa.Text(), nullable=False),
        sa.Column("tts_request_identity_hash", sa.String(64), nullable=False),
        sa.Column("tts_idempotency_key", sa.String(240), nullable=False),
        sa.Column("audio_relative_path", sa.Text(), nullable=False),
        sa.Column("audio_checksum_sha256", sa.String(64), nullable=False),
        sa.Column("audio_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("audio_duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("original_failure_reason_code", sa.String(160), nullable=False),
        sa.Column(
            "forced_alignment_permission_confirmed", sa.Boolean(), nullable=False
        ),
        sa.Column("max_tts_retries", sa.Integer(), nullable=False),
        sa.Column("max_forced_alignment_submissions", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(100), nullable=False),
        sa.Column("recovery_reason", sa.String(160), nullable=False),
        sa.Column("authorized_by_actor_type", sa.String(80), nullable=False),
        sa.Column(
            "authorized_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("authorized_by_actor_role", sa.String(80), nullable=False),
        sa.Column("authority_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version = "
            "'vcos.v2-narration-timing-recovery-authority.v1' and "
            "recovery_reason = "
            "'DURABLE_TTS_AUDIO_MISSING_TIMING_PROVENANCE' and "
            "original_failure_reason_code = 'V2_ELEVENLABS_PROVIDER_FAILURE'",
            name="ck_v2_narration_timing_recovery_authority_identity",
        ),
        sa.CheckConstraint(
            "forced_alignment_permission_confirmed and max_tts_retries = 0 "
            "and max_forced_alignment_submissions = 1 and audio_size_bytes > 0 "
            "and audio_duration_ms > 0",
            name="ck_v2_narration_timing_recovery_authority_bounds",
        ),
        sa.CheckConstraint(
            "production_package_hash ~ '^[0-9a-f]{64}$' and "
            "script_content_hash ~ '^[0-9a-f]{64}$' and "
            "approved_script_hash ~ '^[0-9a-f]{64}$' and "
            "budget_authority_hash ~ '^[0-9a-f]{64}$' and "
            "provider_policy_hash ~ '^[0-9a-f]{64}$' and "
            "tts_request_identity_hash ~ '^[0-9a-f]{64}$' and "
            "audio_checksum_sha256 ~ '^[0-9a-f]{64}$' and "
            "authority_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_narration_timing_recovery_authority_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["production_workflow_runs.id"],
            name="fk_v2_timing_recovery_workflow",
        ),
        sa.ForeignKeyConstraint(
            ["video_project_id"],
            ["video_projects.id"],
            name="fk_v2_timing_recovery_project",
        ),
        sa.ForeignKeyConstraint(
            ["media_effect_ledger_id"],
            ["v2_production_effect_ledger.id"],
            name="fk_v2_timing_recovery_effect",
        ),
        sa.ForeignKeyConstraint(
            ["media_domain_event_id"],
            ["domain_events.id"],
            name="fk_v2_timing_recovery_event",
        ),
        sa.ForeignKeyConstraint(
            ["media_dead_letter_job_id"],
            ["dead_letter_jobs.id"],
            name="fk_v2_timing_recovery_dead_letter",
        ),
        sa.ForeignKeyConstraint(
            ["root_replacement_authority_id"],
            ["script_contract_replacement_authorities.id"],
            name="fk_v2_timing_recovery_root",
        ),
        sa.ForeignKeyConstraint(
            ["verifier_settlement_authority_id"],
            ["controlled_verifier_settlement_authorities.id"],
            name="fk_v2_timing_recovery_settlement",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_qualification_run_id"],
            ["script_qualification_runs.id"],
            name="fk_v2_timing_recovery_qualification",
        ),
        sa.ForeignKeyConstraint(
            ["production_package_artifact_version_id"],
            ["artifact_versions.id"],
            name="fk_v2_timing_recovery_package",
        ),
        sa.ForeignKeyConstraint(
            ["script_artifact_version_id"],
            ["artifact_versions.id"],
            name="fk_v2_timing_recovery_script",
        ),
        sa.ForeignKeyConstraint(
            ["budget_reservation_id"],
            ["mr1_monthly_budget_reservations.id"],
            name="fk_v2_timing_recovery_budget",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", name="uq_v2_timing_recovery_workflow"),
        sa.UniqueConstraint(
            "media_effect_ledger_id", name="uq_v2_timing_recovery_effect"
        ),
        sa.UniqueConstraint(
            "media_domain_event_id", name="uq_v2_timing_recovery_event"
        ),
        sa.UniqueConstraint(
            "media_dead_letter_job_id", name="uq_v2_timing_recovery_dead_letter"
        ),
        sa.UniqueConstraint(
            "root_replacement_authority_id", name="uq_v2_timing_recovery_root"
        ),
        sa.UniqueConstraint(
            "verifier_settlement_authority_id",
            name="uq_v2_timing_recovery_settlement",
        ),
        sa.UniqueConstraint(
            "settlement_qualification_run_id",
            name="uq_v2_timing_recovery_qualification",
        ),
        sa.UniqueConstraint(
            "budget_reservation_id", name="uq_v2_timing_recovery_budget"
        ),
        sa.UniqueConstraint("authority_hash", name="uq_v2_timing_recovery_hash"),
    )
    op.create_index(
        "ix_v2_narration_timing_recovery_authority_project",
        "v2_narration_timing_recovery_authorities",
        ["video_project_id"],
    )
    op.create_index(
        "ix_v2_narration_timing_recovery_authority_created_at",
        "v2_narration_timing_recovery_authorities",
        ["created_at"],
    )

    op.create_table(
        "v2_narration_timing_recovery_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("authority_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "media_effect_ledger_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("forced_alignment_request_hash", sa.String(64), nullable=False),
        sa.Column(
            "forced_alignment_provider_response_hash", sa.String(64), nullable=False
        ),
        sa.Column("forced_alignment_provider_request_id", sa.String(200)),
        sa.Column(
            "forced_alignment_provider_request_id_availability",
            sa.String(40),
            nullable=False,
        ),
        sa.Column("forced_alignment_evidence_hash", sa.String(64), nullable=False),
        sa.Column("recovered_timing_seed_hash", sa.String(64), nullable=False),
        sa.Column("narration_receipt_hash", sa.String(64), nullable=False),
        sa.Column("canonical_media_timeline_hash", sa.String(64), nullable=False),
        sa.Column("provider_call_count", sa.Integer(), nullable=False),
        sa.Column("tts_retry_count", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(100), nullable=False),
        sa.Column("recovery_state", sa.String(40), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version = "
            "'vcos.v2-narration-timing-recovery-receipt.v1' and "
            "recovery_state = 'VERIFIED' and provider_call_count = 1 and "
            "tts_retry_count = 0",
            name="ck_v2_narration_timing_recovery_receipt_identity",
        ),
        sa.CheckConstraint(
            "forced_alignment_provider_request_id_availability in "
            "('PRESENT','NOT_EXPOSED_BY_ENDPOINT') and "
            "((forced_alignment_provider_request_id_availability = 'PRESENT' "
            "and forced_alignment_provider_request_id is not null) or "
            "(forced_alignment_provider_request_id_availability = "
            "'NOT_EXPOSED_BY_ENDPOINT' and "
            "forced_alignment_provider_request_id is null))",
            name="ck_v2_narration_timing_recovery_receipt_request_id",
        ),
        sa.CheckConstraint(
            "forced_alignment_request_hash ~ '^[0-9a-f]{64}$' and "
            "forced_alignment_provider_response_hash ~ '^[0-9a-f]{64}$' and "
            "forced_alignment_evidence_hash ~ '^[0-9a-f]{64}$' and "
            "recovered_timing_seed_hash ~ '^[0-9a-f]{64}$' and "
            "narration_receipt_hash ~ '^[0-9a-f]{64}$' and "
            "canonical_media_timeline_hash ~ '^[0-9a-f]{64}$' and "
            "receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_narration_timing_recovery_receipt_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["authority_id"],
            ["v2_narration_timing_recovery_authorities.id"],
            name="fk_v2_timing_recovery_receipt_authority",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["production_workflow_runs.id"],
            name="fk_v2_timing_recovery_receipt_workflow",
        ),
        sa.ForeignKeyConstraint(
            ["media_effect_ledger_id"],
            ["v2_production_effect_ledger.id"],
            name="fk_v2_timing_recovery_receipt_effect",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("authority_id", name="uq_v2_timing_receipt_authority"),
        sa.UniqueConstraint("workflow_run_id", name="uq_v2_timing_receipt_workflow"),
        sa.UniqueConstraint(
            "media_effect_ledger_id", name="uq_v2_timing_receipt_effect"
        ),
        sa.UniqueConstraint("receipt_hash", name="uq_v2_timing_receipt_hash"),
    )
    op.create_index(
        "ix_v2_narration_timing_recovery_receipt_created_at",
        "v2_narration_timing_recovery_receipts",
        ["created_at"],
    )

    _create_immutability_guards()
    _create_authority_seal()
    _create_receipt_seal()
    _protect_bound_history()


def _create_immutability_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_v2_narration_timing_recovery_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'V2 narration timing recovery rows are immutable';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_v2_timing_recovery_authority_immutable
        BEFORE UPDATE OR DELETE ON v2_narration_timing_recovery_authorities
        FOR EACH ROW EXECUTE FUNCTION prevent_v2_narration_timing_recovery_mutation();

        CREATE TRIGGER trg_v2_timing_recovery_receipt_immutable
        BEFORE UPDATE OR DELETE ON v2_narration_timing_recovery_receipts
        FOR EACH ROW EXECUTE FUNCTION prevent_v2_narration_timing_recovery_mutation();
        """
    )


def _create_authority_seal() -> None:
    op.execute(
        """
        CREATE FUNCTION seal_v2_narration_timing_recovery_authority()
        RETURNS trigger AS $$
        DECLARE
            workflow production_workflow_runs%ROWTYPE;
            effect v2_production_effect_ledger%ROWTYPE;
            media_event domain_events%ROWTYPE;
            dead_letter dead_letter_jobs%ROWTYPE;
            root_authority script_contract_replacement_authorities%ROWTYPE;
            settlement controlled_verifier_settlement_authorities%ROWTYPE;
            qualification script_qualification_runs%ROWTYPE;
            package_version artifact_versions%ROWTYPE;
            package_artifact artifacts%ROWTYPE;
            script_version artifact_versions%ROWTYPE;
            script_artifact artifacts%ROWTYPE;
            budget mr1_monthly_budget_reservations%ROWTYPE;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended(NEW.workflow_run_id::text, 7601)
            );

            SELECT * INTO workflow FROM production_workflow_runs
              WHERE id = NEW.workflow_run_id FOR UPDATE;
            SELECT * INTO effect FROM v2_production_effect_ledger
              WHERE id = NEW.media_effect_ledger_id FOR UPDATE;
            SELECT * INTO media_event FROM domain_events
              WHERE id = NEW.media_domain_event_id FOR UPDATE;
            SELECT * INTO dead_letter FROM dead_letter_jobs
              WHERE id = NEW.media_dead_letter_job_id FOR UPDATE;
            SELECT * INTO root_authority FROM script_contract_replacement_authorities
              WHERE id = NEW.root_replacement_authority_id;
            SELECT * INTO settlement FROM controlled_verifier_settlement_authorities
              WHERE id = NEW.verifier_settlement_authority_id;
            SELECT * INTO qualification FROM script_qualification_runs
              WHERE id = NEW.settlement_qualification_run_id;
            SELECT * INTO package_version FROM artifact_versions
              WHERE id = NEW.production_package_artifact_version_id;
            SELECT * INTO package_artifact FROM artifacts
              WHERE id = package_version.artifact_id;
            SELECT * INTO script_version FROM artifact_versions
              WHERE id = NEW.script_artifact_version_id;
            SELECT * INTO script_artifact FROM artifacts
              WHERE id = script_version.artifact_id;
            SELECT * INTO budget FROM mr1_monthly_budget_reservations
              WHERE id = NEW.budget_reservation_id FOR UPDATE;

            IF workflow.id IS NULL OR effect.id IS NULL OR media_event.id IS NULL
               OR dead_letter.id IS NULL OR root_authority.id IS NULL
               OR settlement.id IS NULL OR qualification.id IS NULL
               OR package_version.id IS NULL OR package_artifact.id IS NULL
               OR script_version.id IS NULL OR script_artifact.id IS NULL
               OR budget.id IS NULL
               OR workflow.video_project_id IS DISTINCT FROM NEW.video_project_id
               OR workflow.current_stage IS DISTINCT FROM 'MEDIA'
               OR workflow.state IS DISTINCT FROM 'BLOCKED'
               OR workflow.production_package_artifact_version_id
                    IS DISTINCT FROM NEW.production_package_artifact_version_id
               OR workflow.production_package_hash
                    IS DISTINCT FROM NEW.production_package_hash
               OR workflow.canonical_media_timeline_ref IS NOT NULL
               OR EXISTS (
                    SELECT 1 FROM v2_production_effect_ledger downstream_effect
                    WHERE downstream_effect.workflow_run_id = workflow.id
                      AND downstream_effect.stage IN ('RENDER', 'QC', 'ARCHIVE')
               )
               OR EXISTS (
                    SELECT 1 FROM workflow_command_receipts downstream_receipt
                    WHERE downstream_receipt.workflow_run_id = workflow.id
                      AND downstream_receipt.stage IN (
                          'MEDIA', 'RENDER', 'QC', 'ARCHIVE', 'FINALIZE'
                      )
               )
               OR EXISTS (
                    SELECT 1 FROM domain_events downstream_event
                    WHERE downstream_event.workflow_run_id = workflow.id
                      AND downstream_event.payload->>'stage' IN (
                          'RENDER', 'QC', 'ARCHIVE', 'FINALIZE'
                      )
               )
               OR effect.workflow_run_id IS DISTINCT FROM workflow.id
               OR effect.video_project_id IS DISTINCT FROM workflow.video_project_id
               OR effect.production_package_artifact_version_id
                    IS DISTINCT FROM NEW.production_package_artifact_version_id
               OR effect.production_package_hash
                    IS DISTINCT FROM NEW.production_package_hash
               OR effect.command_id IS DISTINCT FROM media_event.command_id
               OR effect.stage IS DISTINCT FROM 'MEDIA'
               OR effect.adapter_key IS DISTINCT FROM 'v2-elevenlabs-narration'
               OR effect.state IS DISTINCT FROM 'FAILED_UNCERTAIN'
               OR effect.effect_invocation_count IS DISTINCT FROM 1
               OR effect.completed_at IS NOT NULL OR effect.result_hash IS NOT NULL
               OR media_event.workflow_run_id IS DISTINCT FROM workflow.id
               OR media_event.event_type
                    IS DISTINCT FROM 'production.workflow.stage.requested'
               OR media_event.payload->>'stage' IS DISTINCT FROM 'MEDIA'
               OR media_event.dead_lettered_at IS NULL
               OR media_event.last_error_code
                    IS DISTINCT FROM NEW.original_failure_reason_code
               OR dead_letter.domain_event_id IS DISTINCT FROM media_event.id
               OR dead_letter.workflow_run_id IS DISTINCT FROM workflow.id
               OR dead_letter.command_id IS DISTINCT FROM effect.command_id
               OR dead_letter.reason_code
                    IS DISTINCT FROM NEW.original_failure_reason_code
               OR dead_letter.replay_state IS DISTINCT FROM 'NOT_REPLAYABLE'
               OR dead_letter.retry_eligible IS DISTINCT FROM false
               OR root_authority.authority_hash
                    IS DISTINCT FROM settlement.root_authority_hash
               OR settlement.root_replacement_authority_id
                    IS DISTINCT FROM root_authority.id
               OR settlement.settlement_qualification_run_id
                    IS DISTINCT FROM qualification.id
               OR qualification.production_workflow_run_id
                    IS DISTINCT FROM workflow.id
               OR qualification.admitted_video_project_id
                    IS DISTINCT FROM workflow.video_project_id
               OR package_version.content_hash
                    IS DISTINCT FROM NEW.production_package_hash
               OR package_version.status IS DISTINCT FROM 'submitted'
               OR package_artifact.current_version_id
                    IS DISTINCT FROM package_version.id
               OR package_artifact.video_project_id
                    IS DISTINCT FROM workflow.video_project_id
               OR package_artifact.artifact_type
                    IS DISTINCT FROM 'production_package'
               OR package_artifact.status IS DISTINCT FROM 'draft'
               OR package_version.content->'script_ref'->>'artifact_version_id'
                    IS DISTINCT FROM script_version.id::text
               OR package_version.content->'script_ref'->>'content_hash'
                    IS DISTINCT FROM NEW.script_content_hash
               OR package_version.content->>'compiled_policy_snapshot_hash'
                    IS DISTINCT FROM NEW.provider_policy_hash
               OR script_version.content_hash IS DISTINCT FROM NEW.script_content_hash
               OR script_version.status IS DISTINCT FROM 'approved'
               OR script_artifact.current_version_id IS DISTINCT FROM script_version.id
               OR script_artifact.video_project_id IS DISTINCT FROM workflow.video_project_id
               OR script_artifact.artifact_type IS DISTINCT FROM 'script'
               OR script_artifact.status IS DISTINCT FROM 'approved'
               OR budget.run_id IS DISTINCT FROM workflow.id
               OR budget.video_project_id IS DISTINCT FROM workflow.video_project_id
               OR budget.reservation_ref IS DISTINCT FROM NEW.budget_reservation_ref
               OR budget.capacity_evidence_json->>'content_hash'
                    IS DISTINCT FROM NEW.budget_authority_hash
               OR budget.status NOT IN ('RESERVED', 'SUBMITTED')
               OR NEW.budget_reservation_ref
                    IS DISTINCT FROM 'mr1-budget://' || workflow.id::text
               OR NEW.tts_idempotency_key = ''
               OR NEW.tts_request_journal_ref = ''
               OR NEW.audio_relative_path = ''
               OR NEW.authorized_by_actor_type IS DISTINCT FROM 'SYSTEM_WORKER'
               OR NEW.authorized_by_actor_role IS DISTINCT FROM 'SYSTEM_WORKER'
               OR NEW.authorized_by_actor_id IS DISTINCT FROM
                    '6d196d74-7938-5c85-bc10-f25466616258'::uuid
            THEN
                RAISE EXCEPTION 'V2 narration timing recovery authority seal mismatch';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_v2_timing_recovery_authority_seal
        BEFORE INSERT ON v2_narration_timing_recovery_authorities
        FOR EACH ROW EXECUTE FUNCTION seal_v2_narration_timing_recovery_authority();
        """
    )


def _create_receipt_seal() -> None:
    op.execute(
        """
        CREATE FUNCTION seal_v2_narration_timing_recovery_receipt()
        RETURNS trigger AS $$
        DECLARE
            authority v2_narration_timing_recovery_authorities%ROWTYPE;
            effect v2_production_effect_ledger%ROWTYPE;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended(NEW.workflow_run_id::text, 7601)
            );
            SELECT * INTO authority FROM v2_narration_timing_recovery_authorities
              WHERE id = NEW.authority_id;
            SELECT * INTO effect FROM v2_production_effect_ledger
              WHERE id = NEW.media_effect_ledger_id FOR UPDATE;

            IF authority.id IS NULL OR effect.id IS NULL
               OR authority.workflow_run_id IS DISTINCT FROM NEW.workflow_run_id
               OR authority.media_effect_ledger_id
                    IS DISTINCT FROM NEW.media_effect_ledger_id
               OR authority.max_tts_retries IS DISTINCT FROM 0
               OR authority.max_forced_alignment_submissions IS DISTINCT FROM 1
               OR effect.workflow_run_id IS DISTINCT FROM NEW.workflow_run_id
               OR effect.state IS DISTINCT FROM 'VERIFIED'
               OR effect.stage IS DISTINCT FROM 'MEDIA'
               OR effect.adapter_key IS DISTINCT FROM 'v2-elevenlabs-narration'
               OR effect.effect_invocation_count IS DISTINCT FROM 1
               OR effect.result_hash
                    IS DISTINCT FROM NEW.canonical_media_timeline_hash
               OR effect.completed_at IS NULL
               OR effect.result_type
                    IS DISTINCT FROM 'V2_ELEVENLABS_CANONICAL_MEDIA_TIMELINE'
            THEN
                RAISE EXCEPTION 'V2 narration timing recovery receipt seal mismatch';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_v2_timing_recovery_receipt_seal
        BEFORE INSERT ON v2_narration_timing_recovery_receipts
        FOR EACH ROW EXECUTE FUNCTION seal_v2_narration_timing_recovery_receipt();
        """
    )


def _protect_bound_history() -> None:
    op.execute(
        """
        CREATE FUNCTION protect_v2_timing_recovery_effect()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM v2_narration_timing_recovery_authorities
                WHERE media_effect_ledger_id = OLD.id
            ) AND (
                TG_OP = 'DELETE'
                OR OLD.state IS DISTINCT FROM 'FAILED_UNCERTAIN'
                OR NEW.state IS DISTINCT FROM 'VERIFIED'
                OR NEW.effect_invocation_count IS DISTINCT FROM 1
                OR to_jsonb(NEW) - ARRAY[
                    'state', 'result_type', 'result_id', 'result_ref',
                    'result_hash', 'result_payload', 'authority_refs',
                    'effect_journal', 'completed_at', 'updated_at'
                ] IS DISTINCT FROM to_jsonb(OLD) - ARRAY[
                    'state', 'result_type', 'result_id', 'result_ref',
                    'result_hash', 'result_payload', 'authority_refs',
                    'effect_journal', 'completed_at', 'updated_at'
                ]
            ) THEN
                RAISE EXCEPTION 'V2 timing recovery effect identity is sealed';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_protect_v2_timing_recovery_effect
        BEFORE UPDATE OR DELETE ON v2_production_effect_ledger
        FOR EACH ROW EXECUTE FUNCTION protect_v2_timing_recovery_effect();

        CREATE FUNCTION protect_v2_timing_recovery_event()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM v2_narration_timing_recovery_authorities
                WHERE media_domain_event_id = OLD.id
            ) THEN
                RAISE EXCEPTION 'V2 timing recovery dead-letter event is immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_protect_v2_timing_recovery_event
        BEFORE UPDATE OR DELETE ON domain_events
        FOR EACH ROW EXECUTE FUNCTION protect_v2_timing_recovery_event();

        CREATE FUNCTION protect_v2_timing_recovery_dead_letter()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM v2_narration_timing_recovery_authorities
                WHERE media_dead_letter_job_id = OLD.id
            ) THEN
                RAISE EXCEPTION 'V2 timing recovery dead letter is immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_protect_v2_timing_recovery_dead_letter
        BEFORE UPDATE OR DELETE ON dead_letter_jobs
        FOR EACH ROW EXECUTE FUNCTION protect_v2_timing_recovery_dead_letter();
        """
    )


def downgrade() -> None:
    raise RuntimeError("0076 is intentionally forward-only in production")
