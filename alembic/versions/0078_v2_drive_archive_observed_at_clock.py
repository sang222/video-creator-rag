"""Use wall-clock time when sealing Drive absence evidence.

Revision ID: 0078_v2_drive_recovery_clock
Revises: 0077_v2_drive_recovery
"""

from __future__ import annotations

from alembic import op


revision: str = "0078_v2_drive_recovery_clock"
down_revision: str | None = "0077_v2_drive_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _replace_authority_seal(
        observed_at_future_guard=(
            "(absence->>'observed_at')::timestamptz > clock_timestamp()"
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "0078 Drive archive evidence clock correction is forward-only; restoring "
        "the transaction-stable authority predicate is prohibited"
    )


def _replace_authority_seal(*, observed_at_future_guard: str) -> None:
    op.execute(
        _AUTHORITY_SEAL_SQL.replace(
            "__OBSERVED_AT_FUTURE_GUARD__", observed_at_future_guard
        )
    )


# Keep this seal body aligned with 0077. The only predicate changed by 0078 is the
# observed_at future guard injected above: now() is transaction-stable, while the
# Drive GET evidence is captured later in that same transaction.
_AUTHORITY_SEAL_SQL = """
        CREATE OR REPLACE FUNCTION seal_v2_drive_archive_recovery_authority()
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
             OR __OBSERVED_AT_FUTURE_GUARD__
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
"""
