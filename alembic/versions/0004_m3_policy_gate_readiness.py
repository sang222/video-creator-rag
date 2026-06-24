"""M3 policy gate readiness foundation

Revision ID: 0004_m3_policy_gate_readiness
Revises: 0003_m2_workflow
Create Date: 2026-06-24 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004_m3_policy_gate_readiness"
down_revision: str | None = "0003_m2_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gate_definition_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gate_key", sa.String(length=160), nullable=False),
        sa.Column("gate_name", sa.Text(), nullable=False),
        sa.Column("gate_domain", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("input_schema_version", sa.String(length=80), nullable=False),
        sa.Column("output_schema_version", sa.String(length=80), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("reason_code_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status in ('draft','active','deprecated','superseded')", name="ck_gate_definition_versions_status"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gate_key", "version"),
    )
    op.create_index("ix_gate_definition_versions_gate_key", "gate_definition_versions", ["gate_key"])
    op.create_index("ix_gate_definition_versions_status", "gate_definition_versions", ["status"])
    op.create_index("ix_gate_definition_versions_created_at", "gate_definition_versions", ["created_at"])

    op.create_table(
        "gate_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gate_definition_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gate_key", sa.String(length=160), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("artifact_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("review_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("input_snapshot_hash", sa.Text(), nullable=False),
        sa.Column("result", sa.String(length=40), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("metric_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("freshness_state", sa.String(length=40), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("confidence_reason_codes", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("decision_basis", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_review_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("target_type in ('video_project','artifact_version','review_task')", name="ck_gate_runs_target_type"),
        sa.CheckConstraint("result in ('PASS','REVIEW_REQUIRED','BLOCK','SKIPPED','NOT_APPLICABLE')", name="ck_gate_runs_result"),
        sa.CheckConstraint("freshness_state in ('FRESH','STALE','UNKNOWN','NOT_REQUIRED')", name="ck_gate_runs_freshness_state"),
        sa.CheckConstraint("confidence_level in ('HIGH','MEDIUM','LOW','UNKNOWN')", name="ck_gate_runs_confidence_level"),
        sa.ForeignKeyConstraint(["artifact_version_id"], ["artifact_versions.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_review_task_id"], ["review_tasks.id"]),
        sa.ForeignKeyConstraint(["gate_definition_version_id"], ["gate_definition_versions.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["review_task_id"], ["review_tasks.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gate_runs_gate_definition_version_id", "gate_runs", ["gate_definition_version_id"])
    op.create_index("ix_gate_runs_gate_key", "gate_runs", ["gate_key"])
    op.create_index("ix_gate_runs_target", "gate_runs", ["target_type", "target_id"])
    op.create_index("ix_gate_runs_video_project_id", "gate_runs", ["video_project_id"])
    op.create_index("ix_gate_runs_artifact_version_id", "gate_runs", ["artifact_version_id"])
    op.create_index("ix_gate_runs_created_at", "gate_runs", ["created_at"])

    op.create_table(
        "platform_policy_catalogs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_key", sa.String(length=160), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("policy_domain", sa.String(length=120), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("platform in ('youtube','tiktok','facebook','instagram','meta','generic')", name="ck_platform_policy_catalogs_platform"),
        sa.CheckConstraint("status in ('draft','active','retired')", name="ck_platform_policy_catalogs_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("catalog_key"),
    )
    op.create_index("ix_platform_policy_catalogs_platform", "platform_policy_catalogs", ["platform"])
    op.create_index("ix_platform_policy_catalogs_policy_domain", "platform_policy_catalogs", ["policy_domain"])
    op.create_index("ix_platform_policy_catalogs_created_at", "platform_policy_catalogs", ["created_at"])

    op.create_table(
        "platform_policy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("policy_blob", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("interpretation_notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status in ('draft','active','superseded','retired')", name="ck_platform_policy_versions_status"),
        sa.ForeignKeyConstraint(["catalog_id"], ["platform_policy_catalogs.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("catalog_id", "version"),
    )
    op.create_index("ix_platform_policy_versions_catalog_id", "platform_policy_versions", ["catalog_id"])
    op.create_index("ix_platform_policy_versions_status", "platform_policy_versions", ["status"])
    op.create_index("ix_platform_policy_versions_created_at", "platform_policy_versions", ["created_at"])
    op.create_foreign_key(
        "fk_platform_policy_catalogs_current_version_id",
        "platform_policy_catalogs",
        "platform_policy_versions",
        ["current_version_id"],
        ["id"],
    )

    op.create_table(
        "policy_change_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("change_key", sa.String(length=160), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("policy_domain", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=60), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("old_policy_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("new_policy_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("impact_classification", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("diff_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("affected_gate_keys", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("affected_domains", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("requires_revalidation", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rollback_available", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("platform in ('youtube','tiktok','facebook','instagram','meta','generic')", name="ck_policy_change_records_platform"),
        sa.CheckConstraint(
            "state in ('DRAFT','SOURCE_VERIFIED','DIFFED','IMPACT_CLASSIFIED','CATALOG_PATCHED','GATES_UPDATED','REVALIDATION_RUNNING','OPERATOR_REVIEW_REQUIRED','READY_TO_ACTIVATE','ACTIVE','SUPERSEDED','ROLLED_BACK','REJECTED','MONITOR_ONLY')",
            name="ck_policy_change_records_state",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["new_policy_version_id"], ["platform_policy_versions.id"]),
        sa.ForeignKeyConstraint(["old_policy_version_id"], ["platform_policy_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("change_key"),
    )
    op.create_index("ix_policy_change_records_platform", "policy_change_records", ["platform"])
    op.create_index("ix_policy_change_records_policy_domain", "policy_change_records", ["policy_domain"])
    op.create_index("ix_policy_change_records_state", "policy_change_records", ["state"])
    op.create_index("ix_policy_change_records_created_at", "policy_change_records", ["created_at"])

    op.create_table(
        "policy_source_refs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_change_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reliability", sa.String(length=40), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source_type in ('OFFICIAL','PRIMARY','REPUTABLE_SECONDARY','INTERNAL_NOTE','MANUAL_REVIEW')", name="ck_policy_source_refs_source_type"),
        sa.CheckConstraint("reliability in ('OFFICIAL','HIGH','MEDIUM','LOW','UNKNOWN')", name="ck_policy_source_refs_reliability"),
        sa.CheckConstraint("policy_version_id is not null or policy_change_record_id is not null", name="ck_policy_source_refs_parent"),
        sa.ForeignKeyConstraint(["policy_change_record_id"], ["policy_change_records.id"]),
        sa.ForeignKeyConstraint(["policy_version_id"], ["platform_policy_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policy_source_refs_policy_version_id", "policy_source_refs", ["policy_version_id"])
    op.create_index("ix_policy_source_refs_policy_change_record_id", "policy_source_refs", ["policy_change_record_id"])
    op.create_index("ix_policy_source_refs_created_at", "policy_source_refs", ["created_at"])

    op.create_table(
        "policy_revalidation_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_change_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gate_definition_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status in ('PENDING','RUNNING','COMPLETED','FAILED','CANCELLED')", name="ck_policy_revalidation_batches_status"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["gate_definition_version_id"], ["gate_definition_versions.id"]),
        sa.ForeignKeyConstraint(["policy_change_record_id"], ["policy_change_records.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policy_revalidation_batches_policy_change_record_id", "policy_revalidation_batches", ["policy_change_record_id"])
    op.create_index("ix_policy_revalidation_batches_status", "policy_revalidation_batches", ["status"])
    op.create_index("ix_policy_revalidation_batches_created_at", "policy_revalidation_batches", ["created_at"])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_gate_run_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'gate_runs are immutable after creation';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_prevent_gate_run_update
        BEFORE UPDATE ON gate_runs
        FOR EACH ROW EXECUTE FUNCTION prevent_gate_run_update();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_active_gate_definition_payload_update()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'active' AND (
                NEW.gate_key IS DISTINCT FROM OLD.gate_key OR
                NEW.gate_name IS DISTINCT FROM OLD.gate_name OR
                NEW.gate_domain IS DISTINCT FROM OLD.gate_domain OR
                NEW.version IS DISTINCT FROM OLD.version OR
                NEW.input_schema_version IS DISTINCT FROM OLD.input_schema_version OR
                NEW.output_schema_version IS DISTINCT FROM OLD.output_schema_version OR
                NEW.definition IS DISTINCT FROM OLD.definition OR
                NEW.reason_code_refs IS DISTINCT FROM OLD.reason_code_refs OR
                NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id OR
                NEW.created_at IS DISTINCT FROM OLD.created_at
            ) THEN
                RAISE EXCEPTION 'active gate definition versions are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_prevent_active_gate_definition_payload_update
        BEFORE UPDATE ON gate_definition_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_active_gate_definition_payload_update();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_active_policy_version_payload_update()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'active' AND (
                NEW.catalog_id IS DISTINCT FROM OLD.catalog_id OR
                NEW.version IS DISTINCT FROM OLD.version OR
                NEW.effective_at IS DISTINCT FROM OLD.effective_at OR
                NEW.observed_at IS DISTINCT FROM OLD.observed_at OR
                NEW.policy_blob IS DISTINCT FROM OLD.policy_blob OR
                NEW.interpretation_notes IS DISTINCT FROM OLD.interpretation_notes OR
                NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id OR
                NEW.created_at IS DISTINCT FROM OLD.created_at
            ) THEN
                RAISE EXCEPTION 'active platform policy versions are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_prevent_active_policy_version_payload_update
        BEFORE UPDATE ON platform_policy_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_active_policy_version_payload_update();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_active_policy_version_payload_update ON platform_policy_versions")
    op.execute("DROP FUNCTION IF EXISTS prevent_active_policy_version_payload_update()")
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_active_gate_definition_payload_update ON gate_definition_versions")
    op.execute("DROP FUNCTION IF EXISTS prevent_active_gate_definition_payload_update()")
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_gate_run_update ON gate_runs")
    op.execute("DROP FUNCTION IF EXISTS prevent_gate_run_update()")
    op.drop_index("ix_policy_revalidation_batches_created_at", table_name="policy_revalidation_batches")
    op.drop_index("ix_policy_revalidation_batches_status", table_name="policy_revalidation_batches")
    op.drop_index("ix_policy_revalidation_batches_policy_change_record_id", table_name="policy_revalidation_batches")
    op.drop_table("policy_revalidation_batches")
    op.drop_index("ix_policy_source_refs_created_at", table_name="policy_source_refs")
    op.drop_index("ix_policy_source_refs_policy_change_record_id", table_name="policy_source_refs")
    op.drop_index("ix_policy_source_refs_policy_version_id", table_name="policy_source_refs")
    op.drop_table("policy_source_refs")
    op.drop_index("ix_policy_change_records_created_at", table_name="policy_change_records")
    op.drop_index("ix_policy_change_records_state", table_name="policy_change_records")
    op.drop_index("ix_policy_change_records_policy_domain", table_name="policy_change_records")
    op.drop_index("ix_policy_change_records_platform", table_name="policy_change_records")
    op.drop_table("policy_change_records")
    op.drop_constraint("fk_platform_policy_catalogs_current_version_id", "platform_policy_catalogs", type_="foreignkey")
    op.drop_index("ix_platform_policy_versions_created_at", table_name="platform_policy_versions")
    op.drop_index("ix_platform_policy_versions_status", table_name="platform_policy_versions")
    op.drop_index("ix_platform_policy_versions_catalog_id", table_name="platform_policy_versions")
    op.drop_table("platform_policy_versions")
    op.drop_index("ix_platform_policy_catalogs_created_at", table_name="platform_policy_catalogs")
    op.drop_index("ix_platform_policy_catalogs_policy_domain", table_name="platform_policy_catalogs")
    op.drop_index("ix_platform_policy_catalogs_platform", table_name="platform_policy_catalogs")
    op.drop_table("platform_policy_catalogs")
    op.drop_index("ix_gate_runs_created_at", table_name="gate_runs")
    op.drop_index("ix_gate_runs_artifact_version_id", table_name="gate_runs")
    op.drop_index("ix_gate_runs_video_project_id", table_name="gate_runs")
    op.drop_index("ix_gate_runs_target", table_name="gate_runs")
    op.drop_index("ix_gate_runs_gate_key", table_name="gate_runs")
    op.drop_index("ix_gate_runs_gate_definition_version_id", table_name="gate_runs")
    op.drop_table("gate_runs")
    op.drop_index("ix_gate_definition_versions_created_at", table_name="gate_definition_versions")
    op.drop_index("ix_gate_definition_versions_status", table_name="gate_definition_versions")
    op.drop_index("ix_gate_definition_versions_gate_key", table_name="gate_definition_versions")
    op.drop_table("gate_definition_versions")
