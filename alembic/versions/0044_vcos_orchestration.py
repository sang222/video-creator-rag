"""Add durable VCOS orchestration, outbox leases, and incident lineage.

Revision ID: 0044_vcos_orchestration
Revises: 0043_vcos_phase123
Create Date: 2026-07-29 00:00:00

The workflow table is a projection only.  Immutable admission, package,
readiness, provider, render, QC, and archive rows remain the domain authority.
Downgrade is refused once any durable orchestration authority has been written.
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0044_vcos_orchestration"
down_revision: str | None = "0043_vcos_phase123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    _create_workflow_projection()
    _extend_domain_event_outbox()
    _create_command_receipts()
    _extend_dead_letters()
    _extend_ops_incidents()


def _create_workflow_projection() -> None:
    op.create_table(
        "production_workflow_runs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=True),
        sa.Column("uploaded_video_id", UUID, nullable=True),
        sa.Column("production_lane", sa.String(length=40), nullable=False),
        sa.Column("planning_source_type", sa.String(length=40), nullable=False),
        sa.Column("planning_source_id", UUID, nullable=False),
        sa.Column("planning_source_hash", sa.String(length=64), nullable=False),
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("start_input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=40),
            server_default=sa.text("'PLANNING_PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "current_stage",
            sa.String(length=40),
            server_default=sa.text("'PLANNING'"),
            nullable=False,
        ),
        sa.Column(
            "state_reason_codes",
            JSONB,
            server_default=_jsonb_array(),
            nullable=False,
        ),
        sa.Column(
            "projection_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("project_admission_decision_id", UUID, nullable=True),
        sa.Column(
            "project_admission_decision_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "production_package_artifact_version_id",
            UUID,
            nullable=True,
        ),
        sa.Column("production_package_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "production_readiness_receipt_artifact_version_id",
            UUID,
            nullable=True,
        ),
        sa.Column(
            "production_readiness_receipt_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("canonical_media_timeline_ref", sa.Text(), nullable=True),
        sa.Column(
            "canonical_media_timeline_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("native_render_plan_ref", sa.Text(), nullable=True),
        sa.Column(
            "native_render_plan_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("render_output_ref", sa.Text(), nullable=True),
        sa.Column(
            "render_output_checksum",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("technical_qc_receipt_ref", sa.Text(), nullable=True),
        sa.Column(
            "technical_qc_receipt_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("creative_qc_receipt_ref", sa.Text(), nullable=True),
        sa.Column(
            "creative_qc_receipt_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("archive_receipt_ref", sa.Text(), nullable=True),
        sa.Column("archive_receipt_hash", sa.String(length=64), nullable=True),
        sa.Column("archive_object_ref", sa.Text(), nullable=True),
        sa.Column(
            "archive_verification_state",
            sa.String(length=40),
            nullable=True,
        ),
        sa.Column("final_media_ref_id", UUID, nullable=True),
        sa.Column("final_media_ref_hash", sa.String(length=64), nullable=True),
        # The candidate table is introduced by 0045.  Its FK is added there.
        sa.Column("final_review_candidate_id", UUID, nullable=True),
        sa.Column(
            "final_review_candidate_artifact_version_id",
            UUID,
            nullable=True,
        ),
        sa.Column(
            "final_review_candidate_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("destination_binding_id", UUID, nullable=True),
        sa.Column(
            "destination_binding_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "destination_binding",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "cancellation_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("cancellation_requested_by_user_id", UUID, nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_progress_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "production_lane in ('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT')",
            name="production_workflow_runs_lane",
        ),
        sa.CheckConstraint(
            "planning_source_type in ('DAILY_IDEA','LONG_FORM_PLAN','DERIVED_SHORT')",
            name="production_workflow_runs_planning_source",
        ),
        sa.CheckConstraint(
            "state in ("
            "'PLANNING_PENDING','PLANNING_RUNNING','ASSIGNMENT_READY',"
            "'RESEARCH_PENDING','RESEARCH_RUNNING','PACKAGE_PENDING',"
            "'PACKAGE_RUNNING','READY_FOR_PRODUCTION','MEDIA_PENDING',"
            "'MEDIA_RUNNING','RENDER_PENDING','RENDER_RUNNING','QC_PENDING',"
            "'QC_RUNNING','ARCHIVE_PENDING','ARCHIVE_RUNNING',"
            "'FINAL_REVIEW_READY','BLOCKED','RETRY_SCHEDULED','CANCELED',"
            "'FAILED_TERMINAL','DEAD_LETTERED')",
            name="production_workflow_runs_state",
        ),
        sa.CheckConstraint(
            "current_stage in "
            "('PLANNING','PREFLIGHT','ADMISSION','RESEARCH','PACKAGE',"
            "'READINESS','MEDIA','RENDER','QC','ARCHIVE','FINALIZE')",
            name="production_workflow_runs_stage",
        ),
        sa.CheckConstraint(
            "planning_source_hash ~ '^[0-9a-f]{64}$' "
            "and workflow_key ~ '^[0-9a-f]{64}$' "
            "and start_input_hash ~ '^[0-9a-f]{64}$'",
            name="production_workflow_runs_identity_hashes",
        ),
        sa.CheckConstraint(
            "projection_version > 0",
            name="production_workflow_runs_projection_version",
        ),
        sa.CheckConstraint(
            "(state <> 'CANCELED') or "
            "(cancellation_requested_at is not null and canceled_at is not null)",
            name="production_workflow_runs_canceled_evidence",
        ),
        sa.CheckConstraint(
            "(archive_verification_state is null) or "
            "(archive_verification_state in "
            "('NOT_STARTED','PENDING','VERIFIED','FAILED','UNCERTAIN'))",
            name="production_workflow_runs_archive_state",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(
            ["project_admission_decision_id"],
            ["project_admission_decisions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["production_package_artifact_version_id"],
            ["artifact_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["production_readiness_receipt_artifact_version_id"],
            ["artifact_versions.id"],
        ),
        sa.ForeignKeyConstraint(["final_media_ref_id"], ["final_media_refs.id"]),
        sa.ForeignKeyConstraint(
            ["final_review_candidate_artifact_version_id"],
            ["artifact_versions.id"],
        ),
        sa.ForeignKeyConstraint(["cancellation_requested_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_key",
            name="uq_production_workflow_runs_workflow_key",
        ),
    )
    for index_name, columns in (
        ("ix_production_workflow_runs_company_id", ["company_id"]),
        (
            "ix_production_workflow_runs_channel_workspace_id",
            ["channel_workspace_id"],
        ),
        ("ix_production_workflow_runs_video_project_id", ["video_project_id"]),
        ("ix_production_workflow_runs_production_lane", ["production_lane"]),
        (
            "ix_production_workflow_runs_state_progress",
            ["state", "last_progress_at"],
        ),
        (
            "ix_production_workflow_runs_source",
            ["planning_source_type", "planning_source_id"],
        ),
        (
            "ix_production_workflow_runs_final_review_candidate_id",
            ["final_review_candidate_id"],
        ),
        ("ix_production_workflow_runs_created_at", ["created_at"]),
    ):
        op.create_index(index_name, "production_workflow_runs", columns)


def _extend_domain_event_outbox() -> None:
    additions = (
        sa.Column("channel_workspace_id", UUID, nullable=True),
        sa.Column("workflow_run_id", UUID, nullable=True),
        sa.Column("command_id", sa.String(length=160), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=160), nullable=True),
        sa.Column("last_error_summary", sa.Text(), nullable=True),
    )
    for column in additions:
        op.add_column("domain_events", column)
    op.create_foreign_key(
        "fk_domain_events_channel_workspace_id_channel_workspaces",
        "domain_events",
        "channel_workspaces",
        ["channel_workspace_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_domain_events_workflow_run_id_production_workflow_runs",
        "domain_events",
        "production_workflow_runs",
        ["workflow_run_id"],
        ["id"],
    )
    op.create_index(
        "ix_domain_events_channel_workspace_id",
        "domain_events",
        ["channel_workspace_id"],
    )
    op.create_index(
        "ix_domain_events_workflow_run_id",
        "domain_events",
        ["workflow_run_id"],
    )
    op.create_index(
        "ix_domain_events_command_id",
        "domain_events",
        ["command_id"],
        unique=True,
    )
    op.create_index(
        "ix_domain_events_outbox_claim",
        "domain_events",
        [
            "delivered_at",
            "dead_lettered_at",
            "next_attempt_at",
            "lease_expires_at",
        ],
    )


def _create_command_receipts() -> None:
    op.create_table(
        "workflow_command_receipts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workflow_run_id", UUID, nullable=False),
        sa.Column("domain_event_id", UUID, nullable=False),
        sa.Column("command_id", sa.String(length=160), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("handler_key", sa.String(length=160), nullable=False),
        sa.Column("handler_version", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "effect_state",
            sa.String(length=40),
            server_default=sa.text("'COMPLETED'"),
            nullable=False,
        ),
        sa.Column("result_type", sa.String(length=120), nullable=False),
        sa.Column("result_id", UUID, nullable=True),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "result_payload",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "authority_refs",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage in ('PLANNING','PREFLIGHT','ADMISSION','RESEARCH',"
            "'PACKAGE','READINESS','MEDIA','RENDER','QC','ARCHIVE','FINALIZE')",
            name="workflow_command_receipts_stage",
        ),
        sa.CheckConstraint(
            "effect_state in ('COMPLETED','RECONCILED','CANCELED')",
            name="workflow_command_receipts_effect_state",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="workflow_command_receipts_input_hash",
        ),
        sa.CheckConstraint(
            "result_hash is null or result_hash ~ '^[0-9a-f]{64}$'",
            name="workflow_command_receipts_result_hash",
        ),
        sa.CheckConstraint(
            "completed_at >= started_at",
            name="workflow_command_receipts_completion_order",
        ),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["production_workflow_runs.id"]),
        sa.ForeignKeyConstraint(["domain_event_id"], ["domain_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "domain_event_id",
            name="uq_workflow_command_receipts_domain_event_id",
        ),
        sa.UniqueConstraint(
            "command_id",
            name="uq_workflow_command_receipts_command_id",
        ),
    )
    for index_name, columns in (
        (
            "ix_workflow_command_receipts_workflow_stage",
            ["workflow_run_id", "stage"],
        ),
        (
            "ix_workflow_command_receipts_handler",
            ["handler_key", "handler_version"],
        ),
        ("ix_workflow_command_receipts_created_at", ["created_at"]),
    ):
        op.create_index(index_name, "workflow_command_receipts", columns)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_vcos_workflow_receipt_change()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'workflow_command_receipts are immutable';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_prevent_vcos_workflow_receipt_change
        BEFORE UPDATE OR DELETE ON workflow_command_receipts
        FOR EACH ROW
        EXECUTE FUNCTION prevent_vcos_workflow_receipt_change();
        """
    )


def _extend_dead_letters() -> None:
    for column in (
        sa.Column("domain_event_id", UUID, nullable=True),
        sa.Column("workflow_run_id", UUID, nullable=True),
        sa.Column("command_id", sa.String(length=160), nullable=True),
        sa.Column(
            "retry_eligible",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    ):
        op.add_column("dead_letter_jobs", column)
    op.create_foreign_key(
        "fk_dead_letter_jobs_domain_event_id_domain_events",
        "dead_letter_jobs",
        "domain_events",
        ["domain_event_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_dead_letter_jobs_workflow_run_id_production_workflow_runs",
        "dead_letter_jobs",
        "production_workflow_runs",
        ["workflow_run_id"],
        ["id"],
    )
    op.create_index(
        "uq_dead_letter_jobs_domain_event_id",
        "dead_letter_jobs",
        ["domain_event_id"],
        unique=True,
    )
    op.create_index(
        "ix_dead_letter_jobs_workflow_run_id",
        "dead_letter_jobs",
        ["workflow_run_id"],
    )
    op.create_index(
        "ix_dead_letter_jobs_command_id",
        "dead_letter_jobs",
        ["command_id"],
    )


def _extend_ops_incidents() -> None:
    op.execute(
        "ALTER TABLE ops_incidents "
        "DROP CONSTRAINT IF EXISTS "
        "ck_ops_incidents_ck_ops_incidents_incident_type"
    )
    op.execute(
        "ALTER TABLE ops_incidents "
        "DROP CONSTRAINT IF EXISTS ck_ops_incidents_incident_type"
    )
    op.execute(
        "ALTER TABLE ops_incidents "
        "ADD CONSTRAINT ck_ops_incidents_ck_ops_incidents_incident_type "
        "CHECK (incident_type in ("
        "'PROVIDER_OUTAGE','CREDENTIAL_MISSING','QUOTA_EXHAUSTED',"
        "'COST_LIMIT_REACHED','DEAD_LETTER_JOB','HEALTH_DEGRADED',"
        "'CONFIG_ERROR','UNKNOWN','WORKER_LEASE_EXPIRED',"
        "'STAGE_RETRY_EXHAUSTED','PROVIDER_OUTCOME_UNCERTAIN',"
        "'BUDGET_SETTLEMENT_UNCERTAIN','RENDER_FAILED','ARCHIVE_FAILED',"
        "'INTEGRITY_MISMATCH','CANCELED_WITH_IN_FLIGHT_EFFECT'))"
    )
    for column in (
        sa.Column("project_id", UUID, nullable=True),
        sa.Column("uploaded_video_id", UUID, nullable=True),
        sa.Column("workflow_run_id", UUID, nullable=True),
        sa.Column("stage", sa.String(length=80), nullable=True),
        sa.Column("domain_event_id", UUID, nullable=True),
        sa.Column("command_id", sa.String(length=160), nullable=True),
        sa.Column(
            "retry_eligible",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "learning_excluded",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("operator_visible_blocker", sa.Text(), nullable=True),
        sa.Column(
            "resolution_evidence",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
    ):
        op.add_column("ops_incidents", column)
    for constraint_name, local_column, remote_table in (
        (
            "fk_ops_incidents_project_id_video_projects",
            "project_id",
            "video_projects",
        ),
        (
            "fk_ops_incidents_uploaded_video_id_uploaded_videos",
            "uploaded_video_id",
            "uploaded_videos",
        ),
        (
            "fk_ops_incidents_workflow_run_id_production_workflow_runs",
            "workflow_run_id",
            "production_workflow_runs",
        ),
        (
            "fk_ops_incidents_domain_event_id_domain_events",
            "domain_event_id",
            "domain_events",
        ),
    ):
        op.create_foreign_key(
            constraint_name,
            "ops_incidents",
            remote_table,
            [local_column],
            ["id"],
        )
    for index_name, columns in (
        ("ix_ops_incidents_project_id", ["project_id"]),
        ("ix_ops_incidents_uploaded_video_id", ["uploaded_video_id"]),
        ("ix_ops_incidents_workflow_run_id", ["workflow_run_id"]),
        ("ix_ops_incidents_domain_event_id", ["domain_event_id"]),
        ("ix_ops_incidents_command_id", ["command_id"]),
        ("ix_ops_incidents_learning_excluded", ["learning_excluded"]),
    ):
        op.create_index(index_name, "ops_incidents", columns)


def downgrade() -> None:
    _fail_closed_if_orchestration_authority_exists()
    _drop_ops_incident_extensions()
    _drop_dead_letter_extensions()
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "trg_prevent_vcos_workflow_receipt_change "
        "ON workflow_command_receipts"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_vcos_workflow_receipt_change()")
    op.drop_table("workflow_command_receipts")
    _drop_domain_event_outbox()
    op.drop_table("production_workflow_runs")


def _fail_closed_if_orchestration_authority_exists() -> None:
    authority_predicate = """
        EXISTS (SELECT 1 FROM production_workflow_runs)
        OR EXISTS (SELECT 1 FROM workflow_command_receipts)
        OR EXISTS (
            SELECT 1 FROM domain_events
            WHERE channel_workspace_id IS NOT NULL
               OR workflow_run_id IS NOT NULL
               OR command_id IS NOT NULL
               OR payload_hash IS NOT NULL
               OR next_attempt_at IS NOT NULL
               OR lease_owner IS NOT NULL
               OR lease_expires_at IS NOT NULL
               OR heartbeat_at IS NOT NULL
               OR delivered_at IS NOT NULL
               OR dead_lettered_at IS NOT NULL
               OR last_error_code IS NOT NULL
               OR last_error_summary IS NOT NULL
        )
        OR EXISTS (
            SELECT 1 FROM dead_letter_jobs
            WHERE domain_event_id IS NOT NULL
               OR workflow_run_id IS NOT NULL
               OR command_id IS NOT NULL
               OR retry_eligible
        )
        OR EXISTS (
            SELECT 1 FROM ops_incidents
            WHERE project_id IS NOT NULL
               OR uploaded_video_id IS NOT NULL
               OR workflow_run_id IS NOT NULL
               OR stage IS NOT NULL
               OR domain_event_id IS NOT NULL
               OR command_id IS NOT NULL
               OR retry_eligible
               OR learning_excluded
               OR operator_visible_blocker IS NOT NULL
               OR resolution_evidence <> '{}'::jsonb
        )
    """
    message = "0044 downgrade refused: authoritative durable orchestration rows exist"
    if context.is_offline_mode():
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF {authority_predicate} THEN
                        RAISE EXCEPTION '{message}';
                    END IF;
                END
                $$;
                """
            )
        )
        return
    if op.get_bind().execute(sa.text(f"SELECT {authority_predicate}")).scalar_one():
        raise RuntimeError(message)


def _drop_ops_incident_extensions() -> None:
    for index_name in (
        "ix_ops_incidents_learning_excluded",
        "ix_ops_incidents_command_id",
        "ix_ops_incidents_domain_event_id",
        "ix_ops_incidents_workflow_run_id",
        "ix_ops_incidents_uploaded_video_id",
        "ix_ops_incidents_project_id",
    ):
        op.drop_index(index_name, table_name="ops_incidents")
    for constraint_name in (
        "fk_ops_incidents_domain_event_id_domain_events",
        "fk_ops_incidents_workflow_run_id_production_workflow_runs",
        "fk_ops_incidents_uploaded_video_id_uploaded_videos",
        "fk_ops_incidents_project_id_video_projects",
    ):
        op.drop_constraint(
            constraint_name,
            "ops_incidents",
            type_="foreignkey",
        )
    for column_name in (
        "resolution_evidence",
        "operator_visible_blocker",
        "learning_excluded",
        "retry_eligible",
        "command_id",
        "domain_event_id",
        "stage",
        "workflow_run_id",
        "uploaded_video_id",
        "project_id",
    ):
        op.drop_column("ops_incidents", column_name)
    op.execute(
        "ALTER TABLE ops_incidents "
        "DROP CONSTRAINT IF EXISTS "
        "ck_ops_incidents_ck_ops_incidents_incident_type"
    )
    op.execute(
        "ALTER TABLE ops_incidents "
        "DROP CONSTRAINT IF EXISTS ck_ops_incidents_incident_type"
    )
    op.execute(
        "ALTER TABLE ops_incidents "
        "ADD CONSTRAINT ck_ops_incidents_ck_ops_incidents_incident_type "
        "CHECK (incident_type in ("
        "'PROVIDER_OUTAGE','CREDENTIAL_MISSING','QUOTA_EXHAUSTED',"
        "'COST_LIMIT_REACHED','DEAD_LETTER_JOB','HEALTH_DEGRADED',"
        "'CONFIG_ERROR','UNKNOWN'))"
    )


def _drop_dead_letter_extensions() -> None:
    for index_name in (
        "ix_dead_letter_jobs_command_id",
        "ix_dead_letter_jobs_workflow_run_id",
        "uq_dead_letter_jobs_domain_event_id",
    ):
        op.drop_index(index_name, table_name="dead_letter_jobs")
    for constraint_name in (
        "fk_dead_letter_jobs_workflow_run_id_production_workflow_runs",
        "fk_dead_letter_jobs_domain_event_id_domain_events",
    ):
        op.drop_constraint(
            constraint_name,
            "dead_letter_jobs",
            type_="foreignkey",
        )
    for column_name in (
        "retry_eligible",
        "command_id",
        "workflow_run_id",
        "domain_event_id",
    ):
        op.drop_column("dead_letter_jobs", column_name)


def _drop_domain_event_outbox() -> None:
    for index_name in (
        "ix_domain_events_outbox_claim",
        "ix_domain_events_command_id",
        "ix_domain_events_workflow_run_id",
        "ix_domain_events_channel_workspace_id",
    ):
        op.drop_index(index_name, table_name="domain_events")
    op.drop_constraint(
        "fk_domain_events_workflow_run_id_production_workflow_runs",
        "domain_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_domain_events_channel_workspace_id_channel_workspaces",
        "domain_events",
        type_="foreignkey",
    )
    for column_name in (
        "last_error_summary",
        "last_error_code",
        "dead_lettered_at",
        "delivered_at",
        "heartbeat_at",
        "lease_expires_at",
        "lease_owner",
        "next_attempt_at",
        "max_attempts",
        "attempt_count",
        "payload_hash",
        "command_id",
        "workflow_run_id",
        "channel_workspace_id",
    ):
        op.drop_column("domain_events", column_name)
