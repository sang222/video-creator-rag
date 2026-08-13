"""Seal one bounded V2 Drive app-property-limit recovery.

Revision ID: 0077_v2_drive_recovery
Revises: 0076_v2_timing_recovery
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0077_v2_drive_recovery"
down_revision: str | None = "0076_v2_timing_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "v2_drive_archive_property_limit_recovery_authorities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "archive_effect_ledger_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "archive_domain_event_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "archive_dead_letter_job_id", postgresql.UUID(as_uuid=True), nullable=False
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
        sa.Column("render_output_ref", sa.Text(), nullable=False),
        sa.Column("render_output_checksum", sa.String(64), nullable=False),
        sa.Column("caption_output_ref", sa.Text(), nullable=False),
        sa.Column("caption_output_checksum", sa.String(64), nullable=False),
        sa.Column("technical_qc_hash", sa.String(64), nullable=False),
        sa.Column("creative_qc_hash", sa.String(64), nullable=False),
        sa.Column("cross_modal_qc_hash", sa.String(64), nullable=False),
        sa.Column(
            "budget_reservation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("budget_reservation_ref", sa.Text(), nullable=False),
        sa.Column("budget_authority_hash", sa.String(64), nullable=False),
        sa.Column("drive_credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("drive_root_folder_id", sa.Text(), nullable=False),
        sa.Column("media_folder_path", postgresql.JSONB(), nullable=False),
        sa.Column("caption_folder_path", postgresql.JSONB(), nullable=False),
        sa.Column("archive_command_id", sa.String(160), nullable=False),
        sa.Column("archive_operation_id", sa.String(160), nullable=False),
        sa.Column("archive_adapter_key", sa.String(80), nullable=False),
        sa.Column("archive_input_hash", sa.String(64), nullable=False),
        sa.Column("legacy_request_journal_ref", sa.Text(), nullable=False),
        sa.Column("legacy_request_journal_hash", sa.String(64), nullable=False),
        sa.Column("legacy_media_idempotency_key", sa.String(240), nullable=False),
        sa.Column("legacy_caption_idempotency_key", sa.String(240), nullable=False),
        sa.Column("media_idempotency_key", sa.String(124), nullable=False),
        sa.Column("caption_idempotency_key", sa.String(124), nullable=False),
        sa.Column(
            "absence_reconciliation_evidence", postgresql.JSONB(), nullable=False
        ),
        sa.Column("absence_reconciliation_hash", sa.String(64), nullable=False),
        sa.Column("original_failure_reason_code", sa.String(160), nullable=False),
        sa.Column("defect_code", sa.String(160), nullable=False),
        sa.Column("max_actual_upload_submissions", sa.Integer(), nullable=False),
        sa.Column("automatic_publish", sa.Boolean(), nullable=False),
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
            "schema_version = 'vcos.v2-drive-archive-property-limit-recovery-authority.v1' "
            "and recovery_reason = 'DRIVE_APP_PROPERTY_LIMIT_PRE_FILE_FAILURE' "
            "and original_failure_reason_code = 'V2_GOOGLE_DRIVE_ARCHIVE_PROVIDER_FAILURE' "
            "and defect_code = 'GOOGLE_DRIVE_APP_PROPERTY_VALUE_LIMIT_EXCEEDED' "
            "and archive_adapter_key = 'v2-google-drive-remote'",
            name="ck_v2_drive_archive_recovery_authority_identity",
        ),
        sa.CheckConstraint(
            "max_actual_upload_submissions = 1 and not automatic_publish "
            "and octet_length('vcos_idempotency_key') + "
            "octet_length(media_idempotency_key) between 21 and 124 "
            "and octet_length('vcos_idempotency_key') + "
            "octet_length(caption_idempotency_key) between 21 and 124 "
            "and media_idempotency_key <> caption_idempotency_key "
            "and legacy_media_idempotency_key <> '' "
            "and legacy_caption_idempotency_key <> '' "
            "and render_output_ref <> '' and caption_output_ref <> '' "
            "and legacy_request_journal_ref <> '' "
            "and jsonb_typeof(media_folder_path) = 'array' "
            "and jsonb_array_length(media_folder_path) > 0 "
            "and jsonb_typeof(caption_folder_path) = 'array' "
            "and jsonb_array_length(caption_folder_path) > 0",
            name="ck_v2_drive_archive_recovery_authority_bounds",
        ),
        sa.CheckConstraint(
            "production_package_hash ~ '^[0-9a-f]{64}$' and render_output_checksum ~ '^[0-9a-f]{64}$' "
            "and caption_output_checksum ~ '^[0-9a-f]{64}$' "
            "and technical_qc_hash ~ '^[0-9a-f]{64}$' and creative_qc_hash ~ '^[0-9a-f]{64}$' "
            "and cross_modal_qc_hash ~ '^[0-9a-f]{64}$' and budget_authority_hash ~ '^[0-9a-f]{64}$' "
            "and archive_input_hash ~ '^[0-9a-f]{64}$' and legacy_request_journal_hash ~ '^[0-9a-f]{64}$' "
            "and absence_reconciliation_hash ~ '^[0-9a-f]{64}$' and authority_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_drive_archive_recovery_authority_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["production_workflow_runs.id"],
            name="fk_v2_drive_archive_recovery_workflow",
        ),
        sa.ForeignKeyConstraint(
            ["video_project_id"],
            ["video_projects.id"],
            name="fk_v2_drive_archive_recovery_project",
        ),
        sa.ForeignKeyConstraint(
            ["archive_effect_ledger_id"],
            ["v2_production_effect_ledger.id"],
            name="fk_v2_drive_archive_recovery_effect",
        ),
        sa.ForeignKeyConstraint(
            ["archive_domain_event_id"],
            ["domain_events.id"],
            name="fk_v2_drive_archive_recovery_event",
        ),
        sa.ForeignKeyConstraint(
            ["archive_dead_letter_job_id"],
            ["dead_letter_jobs.id"],
            name="fk_v2_drive_archive_recovery_dead_letter",
        ),
        sa.ForeignKeyConstraint(
            ["root_replacement_authority_id"],
            ["script_contract_replacement_authorities.id"],
            name="fk_v2_drive_archive_recovery_root",
        ),
        sa.ForeignKeyConstraint(
            ["verifier_settlement_authority_id"],
            ["controlled_verifier_settlement_authorities.id"],
            name="fk_v2_drive_archive_recovery_settlement",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_qualification_run_id"],
            ["script_qualification_runs.id"],
            name="fk_v2_drive_archive_recovery_qualification",
        ),
        sa.ForeignKeyConstraint(
            ["production_package_artifact_version_id"],
            ["artifact_versions.id"],
            name="fk_v2_drive_archive_recovery_package",
        ),
        sa.ForeignKeyConstraint(
            ["budget_reservation_id"],
            ["mr1_monthly_budget_reservations.id"],
            name="fk_v2_drive_archive_recovery_budget",
        ),
        sa.ForeignKeyConstraint(
            ["drive_credential_id"],
            ["google_drive_media_credentials.id"],
            name="fk_v2_drive_archive_recovery_credential",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_run_id", name="uq_v2_drive_archive_recovery_workflow"
        ),
        sa.UniqueConstraint(
            "archive_effect_ledger_id", name="uq_v2_drive_archive_recovery_effect"
        ),
        sa.UniqueConstraint(
            "archive_domain_event_id", name="uq_v2_drive_archive_recovery_event"
        ),
        sa.UniqueConstraint(
            "archive_dead_letter_job_id",
            name="uq_v2_drive_archive_recovery_dead_letter",
        ),
        sa.UniqueConstraint(
            "root_replacement_authority_id",
            name="uq_v2_drive_archive_recovery_root",
        ),
        sa.UniqueConstraint(
            "verifier_settlement_authority_id",
            name="uq_v2_drive_archive_recovery_settlement",
        ),
        sa.UniqueConstraint(
            "settlement_qualification_run_id",
            name="uq_v2_drive_archive_recovery_qualification",
        ),
        sa.UniqueConstraint(
            "budget_reservation_id",
            name="uq_v2_drive_archive_recovery_budget",
        ),
        sa.UniqueConstraint("authority_hash", name="uq_v2_drive_archive_recovery_hash"),
    )
    op.create_index(
        "ix_v2_drive_archive_recovery_authority_project",
        "v2_drive_archive_property_limit_recovery_authorities",
        ["video_project_id"],
    )
    op.create_index(
        "ix_v2_drive_archive_recovery_authority_created_at",
        "v2_drive_archive_property_limit_recovery_authorities",
        ["created_at"],
    )

    op.create_table(
        "v2_drive_archive_property_limit_recovery_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("authority_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "archive_effect_ledger_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "media_cloud_media_ref_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "caption_cloud_media_ref_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("final_media_ref_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_drive_file_id", sa.Text(), nullable=False),
        sa.Column("caption_drive_file_id", sa.Text(), nullable=False),
        sa.Column("media_checksum_sha256", sa.String(64), nullable=False),
        sa.Column("caption_checksum_sha256", sa.String(64), nullable=False),
        sa.Column("archive_receipt_hash", sa.String(64), nullable=False),
        sa.Column("archive_object_ref", sa.Text(), nullable=False),
        sa.Column("caption_archive_object_ref", sa.Text(), nullable=False),
        sa.Column("recovery_request_journal_ref", sa.Text(), nullable=False),
        sa.Column("recovery_request_journal_hash", sa.String(64), nullable=False),
        sa.Column("recovery_response_journal_ref", sa.Text(), nullable=False),
        sa.Column("recovery_response_journal_hash", sa.String(64), nullable=False),
        sa.Column("absence_reconciliation_hash", sa.String(64), nullable=False),
        sa.Column("actual_upload_submissions", sa.Integer(), nullable=False),
        sa.Column("provider_file_count", sa.Integer(), nullable=False),
        sa.Column("checksum_verified_file_count", sa.Integer(), nullable=False),
        sa.Column("automatic_publish", sa.Boolean(), nullable=False),
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
            "schema_version = 'vcos.v2-drive-archive-property-limit-recovery-receipt.v1' "
            "and recovery_state = 'VERIFIED' and actual_upload_submissions = 1 "
            "and provider_file_count = 2 and checksum_verified_file_count = 2 and not automatic_publish",
            name="ck_v2_drive_archive_recovery_receipt_identity",
        ),
        sa.CheckConstraint(
            "media_checksum_sha256 ~ '^[0-9a-f]{64}$' and caption_checksum_sha256 ~ '^[0-9a-f]{64}$' "
            "and archive_receipt_hash ~ '^[0-9a-f]{64}$' and recovery_request_journal_hash ~ '^[0-9a-f]{64}$' "
            "and recovery_response_journal_hash ~ '^[0-9a-f]{64}$' and absence_reconciliation_hash ~ '^[0-9a-f]{64}$' "
            "and receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_drive_archive_recovery_receipt_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["authority_id"],
            ["v2_drive_archive_property_limit_recovery_authorities.id"],
            name="fk_v2_drive_archive_recovery_receipt_authority",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["production_workflow_runs.id"],
            name="fk_v2_drive_archive_recovery_receipt_workflow",
        ),
        sa.ForeignKeyConstraint(
            ["archive_effect_ledger_id"],
            ["v2_production_effect_ledger.id"],
            name="fk_v2_drive_archive_recovery_receipt_effect",
        ),
        sa.ForeignKeyConstraint(
            ["media_cloud_media_ref_id"],
            ["cloud_media_refs.id"],
            name="fk_v2_drive_archive_recovery_receipt_media_cloud",
        ),
        sa.ForeignKeyConstraint(
            ["caption_cloud_media_ref_id"],
            ["cloud_media_refs.id"],
            name="fk_v2_drive_archive_recovery_receipt_caption_cloud",
        ),
        sa.ForeignKeyConstraint(
            ["final_media_ref_id"],
            ["final_media_refs.id"],
            name="fk_v2_drive_archive_recovery_receipt_final_media",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "authority_id", name="uq_v2_drive_archive_recovery_receipt_authority"
        ),
        sa.UniqueConstraint(
            "workflow_run_id", name="uq_v2_drive_archive_recovery_receipt_workflow"
        ),
        sa.UniqueConstraint(
            "archive_effect_ledger_id",
            name="uq_v2_drive_archive_recovery_receipt_effect",
        ),
        sa.UniqueConstraint(
            "media_cloud_media_ref_id",
            name="uq_v2_drive_archive_recovery_receipt_media_cloud",
        ),
        sa.UniqueConstraint(
            "caption_cloud_media_ref_id",
            name="uq_v2_drive_archive_recovery_receipt_caption_cloud",
        ),
        sa.UniqueConstraint(
            "final_media_ref_id",
            name="uq_v2_drive_archive_recovery_receipt_final_media",
        ),
        sa.UniqueConstraint(
            "receipt_hash", name="uq_v2_drive_archive_recovery_receipt_hash"
        ),
    )
    op.create_index(
        "ix_v2_drive_archive_recovery_receipt_created_at",
        "v2_drive_archive_property_limit_recovery_receipts",
        ["created_at"],
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_v2_drive_archive_recovery_mutation()
        RETURNS trigger AS $$ BEGIN
          RAISE EXCEPTION 'V2 Drive archive recovery rows are immutable';
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_v2_drive_archive_recovery_authority_immutable
          BEFORE UPDATE OR DELETE ON v2_drive_archive_property_limit_recovery_authorities
          FOR EACH ROW EXECUTE FUNCTION prevent_v2_drive_archive_recovery_mutation();
        CREATE TRIGGER trg_v2_drive_archive_recovery_receipt_immutable
          BEFORE UPDATE OR DELETE ON v2_drive_archive_property_limit_recovery_receipts
          FOR EACH ROW EXECUTE FUNCTION prevent_v2_drive_archive_recovery_mutation();

        CREATE FUNCTION seal_v2_drive_archive_recovery_authority()
        RETURNS trigger AS $$
        DECLARE
          workflow production_workflow_runs%ROWTYPE;
          effect v2_production_effect_ledger%ROWTYPE;
          archive_event domain_events%ROWTYPE;
          dead_letter dead_letter_jobs%ROWTYPE;
          root_authority script_contract_replacement_authorities%ROWTYPE;
          settlement controlled_verifier_settlement_authorities%ROWTYPE;
          qualification script_qualification_runs%ROWTYPE;
          package_version artifact_versions%ROWTYPE;
          package_artifact artifacts%ROWTYPE;
          budget mr1_monthly_budget_reservations%ROWTYPE;
          credential google_drive_media_credentials%ROWTYPE;
          absence jsonb;
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.workflow_run_id::text, 7701));
          SELECT * INTO workflow FROM production_workflow_runs WHERE id=NEW.workflow_run_id FOR UPDATE;
          SELECT * INTO effect FROM v2_production_effect_ledger WHERE id=NEW.archive_effect_ledger_id FOR UPDATE;
          SELECT * INTO archive_event FROM domain_events WHERE id=NEW.archive_domain_event_id FOR UPDATE;
          SELECT * INTO dead_letter FROM dead_letter_jobs WHERE id=NEW.archive_dead_letter_job_id FOR UPDATE;
          SELECT * INTO root_authority FROM script_contract_replacement_authorities WHERE id=NEW.root_replacement_authority_id FOR UPDATE;
          SELECT * INTO settlement FROM controlled_verifier_settlement_authorities WHERE id=NEW.verifier_settlement_authority_id FOR UPDATE;
          SELECT * INTO qualification FROM script_qualification_runs WHERE id=NEW.settlement_qualification_run_id FOR UPDATE;
          SELECT * INTO package_version FROM artifact_versions WHERE id=NEW.production_package_artifact_version_id FOR UPDATE;
          SELECT * INTO package_artifact FROM artifacts WHERE id=package_version.artifact_id FOR UPDATE;
          SELECT * INTO budget FROM mr1_monthly_budget_reservations WHERE id=NEW.budget_reservation_id FOR UPDATE;
          SELECT * INTO credential FROM google_drive_media_credentials WHERE id=NEW.drive_credential_id FOR UPDATE;
          absence := NEW.absence_reconciliation_evidence;
          IF workflow.id IS NULL OR effect.id IS NULL OR archive_event.id IS NULL OR dead_letter.id IS NULL
             OR root_authority.id IS NULL OR settlement.id IS NULL OR qualification.id IS NULL
             OR package_version.id IS NULL OR package_artifact.id IS NULL
             OR budget.id IS NULL OR credential.id IS NULL
             OR workflow.video_project_id IS DISTINCT FROM NEW.video_project_id
             OR workflow.state IS DISTINCT FROM 'BLOCKED' OR workflow.current_stage IS DISTINCT FROM 'ARCHIVE'
             OR workflow.production_package_artifact_version_id IS DISTINCT FROM NEW.production_package_artifact_version_id
             OR workflow.production_package_hash IS DISTINCT FROM NEW.production_package_hash
             OR workflow.render_output_ref IS DISTINCT FROM NEW.render_output_ref
             OR workflow.render_output_checksum IS DISTINCT FROM NEW.render_output_checksum
             OR workflow.technical_qc_receipt_hash IS DISTINCT FROM NEW.technical_qc_hash
             OR workflow.creative_qc_receipt_hash IS DISTINCT FROM NEW.creative_qc_hash
             OR workflow.cross_modal_qc_receipt_hash IS DISTINCT FROM NEW.cross_modal_qc_hash
             OR workflow.archive_receipt_hash IS NOT NULL OR workflow.final_media_ref_id IS NOT NULL
             OR effect.workflow_run_id IS DISTINCT FROM workflow.id OR effect.video_project_id IS DISTINCT FROM workflow.video_project_id
             OR effect.production_package_artifact_version_id IS DISTINCT FROM NEW.production_package_artifact_version_id
             OR effect.production_package_hash IS DISTINCT FROM NEW.production_package_hash
             OR effect.command_id IS DISTINCT FROM NEW.archive_command_id OR effect.operation_id IS DISTINCT FROM NEW.archive_operation_id
             OR effect.adapter_key IS DISTINCT FROM NEW.archive_adapter_key OR effect.input_hash IS DISTINCT FROM NEW.archive_input_hash
             OR effect.stage IS DISTINCT FROM 'ARCHIVE' OR effect.state IS DISTINCT FROM 'FAILED_UNCERTAIN'
             OR effect.effect_invocation_count IS DISTINCT FROM 1 OR effect.result_type IS NOT NULL
             OR effect.result_id IS NOT NULL OR effect.result_ref IS NOT NULL OR effect.result_hash IS NOT NULL
             OR effect.completed_at IS NOT NULL
             OR archive_event.workflow_run_id IS DISTINCT FROM workflow.id OR archive_event.command_id IS DISTINCT FROM effect.command_id
             OR archive_event.event_type IS DISTINCT FROM 'production.workflow.stage.requested'
             OR archive_event.event_version IS DISTINCT FROM 1
             OR archive_event.aggregate_type IS DISTINCT FROM 'production_workflow_run'
             OR archive_event.aggregate_id IS DISTINCT FROM workflow.id
             OR archive_event.company_id IS DISTINCT FROM workflow.company_id
             OR archive_event.channel_workspace_id IS DISTINCT FROM workflow.channel_workspace_id
             OR archive_event.payload_hash IS NULL
             OR archive_event.payload_hash !~ '^[0-9a-f]{64}$'
             OR archive_event.payload->>'workflow_run_id' IS DISTINCT FROM workflow.id::text
             OR archive_event.payload->>'input_hash' IS DISTINCT FROM effect.input_hash
             OR archive_event.payload->>'handler_key' IS DISTINCT FROM 'production.long_form.archive'
             OR archive_event.payload->>'production_lane' IS DISTINCT FROM workflow.production_lane
             OR archive_event.dead_lettered_at IS NULL OR archive_event.last_error_code IS DISTINCT FROM NEW.original_failure_reason_code
             OR archive_event.payload->>'stage' IS DISTINCT FROM 'ARCHIVE'
             OR dead_letter.domain_event_id IS DISTINCT FROM archive_event.id OR dead_letter.reason_code IS DISTINCT FROM NEW.original_failure_reason_code
             OR dead_letter.workflow_run_id IS DISTINCT FROM workflow.id
             OR dead_letter.command_id IS DISTINCT FROM effect.command_id
             OR dead_letter.target_type IS DISTINCT FROM 'production_workflow_run'
             OR dead_letter.target_id IS DISTINCT FROM workflow.id
             OR dead_letter.replay_state IS DISTINCT FROM 'NOT_REPLAYABLE'
             OR dead_letter.retry_eligible IS DISTINCT FROM false
             OR root_authority.authority_hash IS DISTINCT FROM settlement.root_authority_hash
             OR settlement.root_replacement_authority_id IS DISTINCT FROM root_authority.id
             OR settlement.settlement_qualification_run_id IS DISTINCT FROM qualification.id
             OR qualification.production_workflow_run_id IS DISTINCT FROM workflow.id
             OR qualification.admitted_video_project_id IS DISTINCT FROM workflow.video_project_id
             OR qualification.replacement_authority_id IS DISTINCT FROM root_authority.id
             OR qualification.state IS DISTINCT FROM 'QUALIFIED'
             OR package_version.id IS DISTINCT FROM workflow.production_package_artifact_version_id
             OR package_version.content_hash IS DISTINCT FROM NEW.production_package_hash
             OR package_version.status IS DISTINCT FROM 'submitted'
             OR package_artifact.current_version_id IS DISTINCT FROM package_version.id
             OR package_artifact.video_project_id IS DISTINCT FROM workflow.video_project_id
             OR package_artifact.artifact_type IS DISTINCT FROM 'production_package'
             OR package_artifact.status IS DISTINCT FROM 'draft'
             OR budget.run_id IS DISTINCT FROM workflow.id OR budget.video_project_id IS DISTINCT FROM workflow.video_project_id
             OR budget.company_id IS DISTINCT FROM workflow.company_id
             OR budget.channel_workspace_id IS DISTINCT FROM workflow.channel_workspace_id
             OR budget.reservation_ref IS DISTINCT FROM NEW.budget_reservation_ref
             OR budget.capacity_evidence_json->>'content_hash' IS DISTINCT FROM NEW.budget_authority_hash
             OR budget.status NOT IN ('RESERVED','SUBMITTED')
             OR credential.company_id IS DISTINCT FROM workflow.company_id
             OR credential.channel_workspace_id IS DISTINCT FROM workflow.channel_workspace_id
             OR credential.connection_state IS DISTINCT FROM 'CONNECTED'
             OR credential.root_folder_id IS DISTINCT FROM NEW.drive_root_folder_id
             OR NOT credential.scopes @> '["https://www.googleapis.com/auth/drive.file"]'::jsonb
             OR jsonb_typeof(absence) IS DISTINCT FROM 'object'
             OR absence->>'schema_version' IS DISTINCT FROM 'vcos.v2-drive-archive-property-limit-absence-evidence.v1'
             OR absence->>'workflow_run_id' IS DISTINCT FROM workflow.id::text
             OR absence->>'provider' IS DISTINCT FROM 'google_drive'
             OR absence->>'probe_mode' IS DISTINCT FROM 'GET_ONLY'
             OR absence->>'drive_credential_id' IS DISTINCT FROM credential.id::text
             OR absence->>'drive_root_folder_id' IS DISTINCT FROM NEW.drive_root_folder_id
             OR coalesce(absence->>'observed_at', '') = ''
             OR (absence->>'observed_at')::timestamptz > now()
             OR absence->>'expected_media_checksum' IS DISTINCT FROM NEW.render_output_checksum
             OR absence->>'expected_caption_checksum' IS DISTINCT FROM NEW.caption_output_checksum
             OR absence->'legacy_media'->>'idempotency_key' IS DISTINCT FROM NEW.legacy_media_idempotency_key
             OR absence->'legacy_media'->'folder_path' IS DISTINCT FROM NEW.media_folder_path
             OR absence->'legacy_media'->>'state' IS DISTINCT FROM 'ABSENT'
             OR absence->'legacy_media'->>'match_count' IS DISTINCT FROM '0'
             OR absence->'legacy_caption'->>'idempotency_key' IS DISTINCT FROM NEW.legacy_caption_idempotency_key
             OR absence->'legacy_caption'->'folder_path' IS DISTINCT FROM NEW.caption_folder_path
             OR absence->'legacy_caption'->>'state' IS DISTINCT FROM 'ABSENT'
             OR absence->'legacy_caption'->>'match_count' IS DISTINCT FROM '0'
             OR absence->'canonical_media'->>'idempotency_key' IS DISTINCT FROM NEW.media_idempotency_key
             OR absence->'canonical_media'->'folder_path' IS DISTINCT FROM NEW.media_folder_path
             OR absence->'canonical_media'->>'state' IS DISTINCT FROM 'ABSENT'
             OR absence->'canonical_media'->>'match_count' IS DISTINCT FROM '0'
             OR absence->'canonical_caption'->>'idempotency_key' IS DISTINCT FROM NEW.caption_idempotency_key
             OR absence->'canonical_caption'->'folder_path' IS DISTINCT FROM NEW.caption_folder_path
             OR absence->'canonical_caption'->>'state' IS DISTINCT FROM 'ABSENT'
             OR absence->'canonical_caption'->>'match_count' IS DISTINCT FROM '0'
             OR NEW.authorized_by_actor_type IS DISTINCT FROM 'SYSTEM_WORKER'
             OR NEW.authorized_by_actor_role IS DISTINCT FROM 'SYSTEM_WORKER'
             OR NEW.authorized_by_actor_id IS DISTINCT FROM '6d196d74-7938-5c85-bc10-f25466616258'::uuid
             OR EXISTS (SELECT 1 FROM workflow_command_receipts WHERE workflow_run_id=workflow.id AND stage IN ('ARCHIVE','FINALIZE'))
             OR EXISTS (
                  SELECT 1 FROM cloud_media_refs
                  WHERE video_project_id=workflow.video_project_id
                    AND storage_provider='GOOGLE_DRIVE'
                    AND media_type='LONG_FORM_FINAL'
                    AND checksum_sha256=NEW.render_output_checksum
             )
             OR EXISTS (
                  SELECT 1 FROM cloud_media_refs
                  WHERE video_project_id=workflow.video_project_id
                    AND storage_provider='GOOGLE_DRIVE'
                    AND media_type='CAPTION'
                    AND checksum_sha256=NEW.caption_output_checksum
             )
             OR EXISTS (
                  SELECT 1 FROM final_media_refs
                  WHERE video_project_id=workflow.video_project_id
                    AND production_package_artifact_version_id=NEW.production_package_artifact_version_id
                    AND production_package_hash=NEW.production_package_hash
                    AND checksum_sha256=NEW.render_output_checksum
             )
          THEN RAISE EXCEPTION 'V2 Drive archive recovery authority seal mismatch'; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_v2_drive_archive_recovery_authority_seal
          BEFORE INSERT ON v2_drive_archive_property_limit_recovery_authorities
          FOR EACH ROW EXECUTE FUNCTION seal_v2_drive_archive_recovery_authority();

        CREATE FUNCTION protect_v2_drive_archive_recovery_effect()
        RETURNS trigger AS $$ BEGIN
          IF EXISTS (SELECT 1 FROM v2_drive_archive_property_limit_recovery_authorities WHERE archive_effect_ledger_id=OLD.id)
             AND (TG_OP='DELETE' OR OLD.state IS DISTINCT FROM 'FAILED_UNCERTAIN' OR NEW.state IS DISTINCT FROM 'VERIFIED'
               OR NEW.effect_invocation_count IS DISTINCT FROM 1
               OR NEW.result_type IS DISTINCT FROM 'V2_VERIFIED_GOOGLE_DRIVE_REMOTE_ARCHIVE'
               OR NEW.result_id IS NULL OR coalesce(NEW.result_ref, '') = '' OR NEW.result_hash IS NULL
               OR NEW.completed_at IS NULL
               OR NEW.result_payload->>'archive_state' IS DISTINCT FROM 'VERIFIED'
               OR NEW.result_payload->>'storage_provider' IS DISTINCT FROM 'GOOGLE_DRIVE'
               OR NEW.result_payload->>'external_effect_performed' IS DISTINCT FROM 'true'
               OR NEW.result_payload->>'automatic_publish' IS DISTINCT FROM 'false'
               OR NEW.result_payload->>'cloud_media_ref_id' IS NULL
               OR NEW.result_payload->>'caption_cloud_media_ref_id' IS NULL
               OR NEW.authority_refs->>'archive_receipt_hash' IS DISTINCT FROM NEW.result_hash
               OR NEW.authority_refs->>'archive_object_ref' IS DISTINCT FROM NEW.result_ref
               OR NEW.authority_refs->>'archive_verification_state' IS DISTINCT FROM 'VERIFIED'
               OR NEW.authority_refs->>'final_media_ref_id' IS DISTINCT FROM NEW.result_id::text
               OR to_jsonb(NEW)-ARRAY['state','result_type','result_id','result_ref','result_hash','result_payload','authority_refs','effect_journal','completed_at','updated_at']
                  IS DISTINCT FROM to_jsonb(OLD)-ARRAY['state','result_type','result_id','result_ref','result_hash','result_payload','authority_refs','effect_journal','completed_at','updated_at'])
          THEN RAISE EXCEPTION 'V2 Drive archive recovery effect identity is sealed'; END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_protect_v2_drive_archive_recovery_effect
          BEFORE UPDATE OR DELETE ON v2_production_effect_ledger FOR EACH ROW
          EXECUTE FUNCTION protect_v2_drive_archive_recovery_effect();

        CREATE FUNCTION protect_v2_drive_archive_recovery_event()
        RETURNS trigger AS $$ BEGIN
          IF EXISTS (SELECT 1 FROM v2_drive_archive_property_limit_recovery_authorities WHERE archive_domain_event_id=OLD.id)
          THEN RAISE EXCEPTION 'V2 Drive archive dead-letter event is immutable'; END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_protect_v2_drive_archive_recovery_event BEFORE UPDATE OR DELETE ON domain_events
          FOR EACH ROW EXECUTE FUNCTION protect_v2_drive_archive_recovery_event();
        CREATE FUNCTION protect_v2_drive_archive_recovery_dead_letter()
        RETURNS trigger AS $$ BEGIN
          IF EXISTS (SELECT 1 FROM v2_drive_archive_property_limit_recovery_authorities WHERE archive_dead_letter_job_id=OLD.id)
          THEN RAISE EXCEPTION 'V2 Drive archive dead letter is immutable'; END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_protect_v2_drive_archive_recovery_dead_letter BEFORE UPDATE OR DELETE ON dead_letter_jobs
          FOR EACH ROW EXECUTE FUNCTION protect_v2_drive_archive_recovery_dead_letter();

        CREATE FUNCTION seal_v2_drive_archive_recovery_receipt()
        RETURNS trigger AS $$
        DECLARE authority v2_drive_archive_property_limit_recovery_authorities%ROWTYPE;
                effect v2_production_effect_ledger%ROWTYPE;
                workflow production_workflow_runs%ROWTYPE;
                media_cloud cloud_media_refs%ROWTYPE;
                caption_cloud cloud_media_refs%ROWTYPE;
                final_media final_media_refs%ROWTYPE;
                lineage_version artifact_versions%ROWTYPE;
                lineage_artifact artifacts%ROWTYPE;
                lineage jsonb;
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.workflow_run_id::text, 7701));
          SELECT * INTO authority FROM v2_drive_archive_property_limit_recovery_authorities WHERE id=NEW.authority_id;
          SELECT * INTO effect FROM v2_production_effect_ledger WHERE id=NEW.archive_effect_ledger_id FOR UPDATE;
          SELECT * INTO workflow FROM production_workflow_runs WHERE id=NEW.workflow_run_id FOR UPDATE;
          SELECT * INTO media_cloud FROM cloud_media_refs WHERE id=NEW.media_cloud_media_ref_id FOR UPDATE;
          SELECT * INTO caption_cloud FROM cloud_media_refs WHERE id=NEW.caption_cloud_media_ref_id FOR UPDATE;
          SELECT * INTO final_media FROM final_media_refs WHERE id=NEW.final_media_ref_id FOR UPDATE;
          SELECT * INTO lineage_version FROM artifact_versions WHERE id=final_media.lineage_artifact_version_id FOR UPDATE;
          SELECT * INTO lineage_artifact FROM artifacts WHERE id=lineage_version.artifact_id FOR UPDATE;
          lineage := lineage_version.content;
          IF authority.id IS NULL OR effect.id IS NULL OR workflow.id IS NULL
             OR media_cloud.id IS NULL OR caption_cloud.id IS NULL OR final_media.id IS NULL
             OR lineage_version.id IS NULL OR lineage_artifact.id IS NULL
             OR authority.workflow_run_id IS DISTINCT FROM NEW.workflow_run_id
             OR authority.archive_effect_ledger_id IS DISTINCT FROM effect.id
             OR authority.absence_reconciliation_hash IS DISTINCT FROM NEW.absence_reconciliation_hash
             OR effect.workflow_run_id IS DISTINCT FROM NEW.workflow_run_id OR effect.stage IS DISTINCT FROM 'ARCHIVE'
             OR effect.state IS DISTINCT FROM 'VERIFIED' OR effect.effect_invocation_count IS DISTINCT FROM 1
             OR effect.result_type IS DISTINCT FROM 'V2_VERIFIED_GOOGLE_DRIVE_REMOTE_ARCHIVE'
             OR effect.result_id IS DISTINCT FROM NEW.final_media_ref_id
             OR effect.result_ref IS DISTINCT FROM NEW.archive_object_ref
             OR effect.result_hash IS DISTINCT FROM NEW.archive_receipt_hash
             OR effect.completed_at IS NULL
             OR effect.result_payload->>'cloud_media_ref_id' IS DISTINCT FROM media_cloud.id::text
             OR effect.result_payload->>'caption_cloud_media_ref_id' IS DISTINCT FROM caption_cloud.id::text
             OR effect.result_payload->>'caption_drive_file_id' IS DISTINCT FROM NEW.caption_drive_file_id
             OR effect.result_payload->>'caption_archive_object_ref' IS DISTINCT FROM NEW.caption_archive_object_ref
             OR effect.result_payload->>'checksum_sha256' IS DISTINCT FROM authority.render_output_checksum
             OR effect.authority_refs->>'video_project_id' IS DISTINCT FROM authority.video_project_id::text
             OR effect.authority_refs->>'archive_receipt_hash' IS DISTINCT FROM NEW.archive_receipt_hash
             OR effect.authority_refs->>'archive_object_ref' IS DISTINCT FROM NEW.archive_object_ref
             OR effect.authority_refs->>'final_media_ref_id' IS DISTINCT FROM final_media.id::text
             OR workflow.video_project_id IS DISTINCT FROM authority.video_project_id
             OR media_cloud.id IS NOT DISTINCT FROM caption_cloud.id
             OR media_cloud.company_id IS DISTINCT FROM workflow.company_id
             OR media_cloud.channel_workspace_id IS DISTINCT FROM workflow.channel_workspace_id
             OR media_cloud.video_project_id IS DISTINCT FROM authority.video_project_id
             OR media_cloud.storage_provider IS DISTINCT FROM 'GOOGLE_DRIVE'
             OR media_cloud.media_type IS DISTINCT FROM 'LONG_FORM_FINAL'
             OR media_cloud.drive_file_id IS DISTINCT FROM NEW.media_drive_file_id
             OR media_cloud.checksum_sha256 IS DISTINCT FROM NEW.media_checksum_sha256
             OR media_cloud.checksum_sha256 IS DISTINCT FROM authority.render_output_checksum
             OR media_cloud.upload_status IS DISTINCT FROM 'VERIFIED'
             OR media_cloud.verification_status IS DISTINCT FROM 'CHECKSUM_VERIFIED'
             OR media_cloud.size_bytes IS NULL OR media_cloud.size_bytes <= 0
             OR caption_cloud.company_id IS DISTINCT FROM workflow.company_id
             OR caption_cloud.channel_workspace_id IS DISTINCT FROM workflow.channel_workspace_id
             OR caption_cloud.video_project_id IS DISTINCT FROM authority.video_project_id
             OR caption_cloud.storage_provider IS DISTINCT FROM 'GOOGLE_DRIVE'
             OR caption_cloud.media_type IS DISTINCT FROM 'CAPTION'
             OR caption_cloud.drive_file_id IS DISTINCT FROM NEW.caption_drive_file_id
             OR caption_cloud.checksum_sha256 IS DISTINCT FROM NEW.caption_checksum_sha256
             OR caption_cloud.checksum_sha256 IS DISTINCT FROM authority.caption_output_checksum
             OR caption_cloud.upload_status IS DISTINCT FROM 'VERIFIED'
             OR caption_cloud.verification_status IS DISTINCT FROM 'CHECKSUM_VERIFIED'
             OR caption_cloud.size_bytes IS NULL OR caption_cloud.size_bytes <= 0
             OR NEW.media_drive_file_id = '' OR NEW.caption_drive_file_id = ''
             OR NEW.media_drive_file_id = NEW.caption_drive_file_id
             OR NEW.archive_object_ref IS DISTINCT FROM 'drive://' || NEW.media_drive_file_id || '/final.mp4'
             OR NEW.caption_archive_object_ref IS DISTINCT FROM 'drive://' || NEW.caption_drive_file_id || '/canonical-captions.srt'
             OR final_media.company_id IS DISTINCT FROM workflow.company_id
             OR final_media.channel_workspace_id IS DISTINCT FROM workflow.channel_workspace_id
             OR final_media.video_project_id IS DISTINCT FROM authority.video_project_id
             OR final_media.production_package_artifact_version_id IS DISTINCT FROM authority.production_package_artifact_version_id
             OR final_media.production_package_hash IS DISTINCT FROM authority.production_package_hash
             OR final_media.media_type IS DISTINCT FROM 'LONG_FORM_FINAL'
             OR final_media.file_ref IS DISTINCT FROM NEW.archive_object_ref
             OR final_media.provider_key IS DISTINCT FROM 'v2-google-drive-remote'
             OR final_media.provider_type IS DISTINCT FROM 'MEDIA_STORAGE'
             OR final_media.checksum_sha256 IS DISTINCT FROM authority.render_output_checksum
             OR final_media.cloud_media_ref_id IS DISTINCT FROM media_cloud.id
             OR final_media.duration_contract IS NULL
             OR final_media.duration_seconds IS NULL OR final_media.duration_seconds <= 0
             OR lineage_artifact.video_project_id IS DISTINCT FROM authority.video_project_id
             OR lineage_artifact.artifact_type IS DISTINCT FROM 'v2_drive_final_media_lineage_receipt'
             OR lineage_artifact.current_version_id IS DISTINCT FROM lineage_version.id
             OR lineage_artifact.status IS DISTINCT FROM 'approved'
             OR lineage_version.status IS DISTINCT FROM 'approved'
             OR jsonb_typeof(lineage) IS DISTINCT FROM 'object'
             OR lineage->>'schema_version' IS DISTINCT FROM 'vcos.v2-drive-final-media-lineage.v1'
             OR lineage->>'workflow_run_id' IS DISTINCT FROM workflow.id::text
             OR lineage->>'archive_command_id' IS DISTINCT FROM authority.archive_command_id
             OR lineage->>'provider_operation_id' IS DISTINCT FROM authority.archive_operation_id
             OR lineage->>'video_project_id' IS DISTINCT FROM authority.video_project_id::text
             OR lineage->>'production_package_artifact_version_id' IS DISTINCT FROM authority.production_package_artifact_version_id::text
             OR lineage->>'production_package_hash' IS DISTINCT FROM authority.production_package_hash
             OR lineage->>'render_output_ref' IS DISTINCT FROM authority.render_output_ref
             OR lineage->>'render_output_checksum' IS DISTINCT FROM authority.render_output_checksum
             OR lineage->>'technical_qc_hash' IS DISTINCT FROM authority.technical_qc_hash
             OR lineage->>'creative_qc_hash' IS DISTINCT FROM authority.creative_qc_hash
             OR lineage->>'archive_receipt_hash' IS DISTINCT FROM NEW.archive_receipt_hash
             OR lineage->>'archive_state' IS DISTINCT FROM 'VERIFIED'
             OR lineage->>'cloud_media_ref_id' IS DISTINCT FROM media_cloud.id::text
             OR lineage->>'archive_object_ref' IS DISTINCT FROM NEW.archive_object_ref
             OR lineage->>'storage_provider' IS DISTINCT FROM 'GOOGLE_DRIVE'
             OR lineage->>'caption_ref' IS DISTINCT FROM authority.caption_output_ref
             OR lineage->>'caption_checksum' IS DISTINCT FROM authority.caption_output_checksum
             OR lineage->>'caption_cloud_media_ref_id' IS DISTINCT FROM caption_cloud.id::text
             OR lineage->>'caption_archive_object_ref' IS DISTINCT FROM NEW.caption_archive_object_ref
             OR lineage->>'invokes_mr1' IS DISTINCT FROM 'false'
             OR lineage->>'automatic_publish' IS DISTINCT FROM 'false'
             OR coalesce(NEW.recovery_request_journal_ref, '') = ''
             OR coalesce(NEW.recovery_response_journal_ref, '') = ''
          THEN RAISE EXCEPTION 'V2 Drive archive recovery receipt seal mismatch'; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_v2_drive_archive_recovery_receipt_seal BEFORE INSERT ON v2_drive_archive_property_limit_recovery_receipts
          FOR EACH ROW EXECUTE FUNCTION seal_v2_drive_archive_recovery_receipt();

        CREATE FUNCTION require_v2_drive_archive_recovery_receipt()
        RETURNS trigger AS $$ BEGIN
          IF EXISTS (
               SELECT 1 FROM v2_drive_archive_property_limit_recovery_authorities
               WHERE archive_effect_ledger_id=NEW.id
             ) AND NEW.state='VERIFIED' AND NOT EXISTS (
               SELECT 1
               FROM v2_drive_archive_property_limit_recovery_receipts receipt
               JOIN v2_drive_archive_property_limit_recovery_authorities authority
                 ON authority.id=receipt.authority_id
               WHERE receipt.archive_effect_ledger_id=NEW.id
                 AND receipt.workflow_run_id=NEW.workflow_run_id
                 AND receipt.final_media_ref_id=NEW.result_id
                 AND receipt.archive_object_ref=NEW.result_ref
                 AND receipt.archive_receipt_hash=NEW.result_hash
                 AND authority.archive_effect_ledger_id=NEW.id
             )
          THEN RAISE EXCEPTION 'V2 Drive archive recovery verification requires its sealed receipt'; END IF;
          RETURN NULL;
        END; $$ LANGUAGE plpgsql;
        CREATE CONSTRAINT TRIGGER trg_require_v2_drive_archive_recovery_receipt
          AFTER UPDATE ON v2_production_effect_ledger
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION require_v2_drive_archive_recovery_receipt();

        CREATE FUNCTION protect_v2_drive_archive_recovery_cloud_media()
        RETURNS trigger AS $$ BEGIN
          IF EXISTS (
               SELECT 1 FROM v2_drive_archive_property_limit_recovery_receipts
               WHERE media_cloud_media_ref_id=OLD.id OR caption_cloud_media_ref_id=OLD.id
             )
          THEN RAISE EXCEPTION 'V2 Drive archive recovery cloud media is immutable'; END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_protect_v2_drive_archive_recovery_cloud_media
          BEFORE UPDATE OR DELETE ON cloud_media_refs FOR EACH ROW
          EXECUTE FUNCTION protect_v2_drive_archive_recovery_cloud_media();

        CREATE FUNCTION protect_v2_drive_archive_recovery_final_media()
        RETURNS trigger AS $$ BEGIN
          IF EXISTS (
               SELECT 1 FROM v2_drive_archive_property_limit_recovery_receipts
               WHERE final_media_ref_id=OLD.id
             )
          THEN RAISE EXCEPTION 'V2 Drive archive recovery final media is immutable'; END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_protect_v2_drive_archive_recovery_final_media
          BEFORE UPDATE OR DELETE ON final_media_refs FOR EACH ROW
          EXECUTE FUNCTION protect_v2_drive_archive_recovery_final_media();

        CREATE FUNCTION protect_v2_drive_archive_recovery_lineage_version()
        RETURNS trigger AS $$ BEGIN
          IF EXISTS (
               SELECT 1
               FROM v2_drive_archive_property_limit_recovery_receipts receipt
               JOIN final_media_refs media ON media.id=receipt.final_media_ref_id
               WHERE media.lineage_artifact_version_id=OLD.id
             )
          THEN RAISE EXCEPTION 'V2 Drive archive recovery lineage version is immutable'; END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_protect_v2_drive_archive_recovery_lineage_version
          BEFORE UPDATE OR DELETE ON artifact_versions FOR EACH ROW
          EXECUTE FUNCTION protect_v2_drive_archive_recovery_lineage_version();

        CREATE FUNCTION protect_v2_drive_archive_recovery_lineage_artifact()
        RETURNS trigger AS $$ BEGIN
          IF EXISTS (
               SELECT 1
               FROM v2_drive_archive_property_limit_recovery_receipts receipt
               JOIN final_media_refs media ON media.id=receipt.final_media_ref_id
               JOIN artifact_versions version ON version.id=media.lineage_artifact_version_id
               WHERE version.artifact_id=OLD.id
             )
          THEN RAISE EXCEPTION 'V2 Drive archive recovery lineage artifact is immutable'; END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_protect_v2_drive_archive_recovery_lineage_artifact
          BEFORE UPDATE OR DELETE ON artifacts FOR EACH ROW
          EXECUTE FUNCTION protect_v2_drive_archive_recovery_lineage_artifact();
        """
    )


def downgrade() -> None:
    raise RuntimeError("0077 is intentionally forward-only in production")
