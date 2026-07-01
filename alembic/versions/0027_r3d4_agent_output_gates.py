"""R3D4 agent output contracts and deterministic gates

Revision ID: 0027_r3d4_agent_output_gates
Revises: 0026_r3d3_agent_context_pack
Create Date: 2026-07-01 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0027_r3d4_agent_output_gates"
down_revision: str | None = "0026_r3d3_agent_context_pack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
R3D4_PACKAGE_STATUS_CHECK = (
    "package_status in ("
    "'READY_FOR_HUMAN_REVIEW','REVIEW_REQUIRED','BLOCKED','NOT_CONFIGURED','ERROR',"
    "'READY_FOR_MEDIA_PROVIDERS','BLOCKED_PROVIDER_NOT_CONFIGURED','WAITING_PROVIDER_CONFIG'"
    ")"
)
M12_2S_PACKAGE_STATUS_CHECK = (
    "package_status in ("
    "'READY_FOR_HUMAN_REVIEW','REVIEW_REQUIRED','BLOCKED','NOT_CONFIGURED','ERROR',"
    "'READY_FOR_MEDIA_PROVIDERS','BLOCKED_PROVIDER_NOT_CONFIGURED'"
    ")"
)


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _drop_package_status_checks() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            FOR constraint_name IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'first_scripted_video_packages'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%package_status%'
            LOOP
                EXECUTE format('ALTER TABLE first_scripted_video_packages DROP CONSTRAINT %I', constraint_name);
            END LOOP;
        END $$;
        """
    )


def upgrade() -> None:
    _drop_package_status_checks()
    op.execute(
        "ALTER TABLE first_scripted_video_packages "
        "ADD CONSTRAINT ck_first_scripted_video_packages_ck_first_scripted_video_packages_status "
        f"CHECK ({R3D4_PACKAGE_STATUS_CHECK})"
    )

    op.create_table(
        "agent_output_validation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("prompt_render_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_context_pack_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("artifact_type", sa.String(length=160), nullable=False),
        sa.Column("output_type", sa.String(length=160), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("validation_state", sa.String(length=80), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("applied_context_refs_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("evidence_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("raw_output_ref", sa.Text(), nullable=True),
        sa.Column("raw_output_hash", sa.String(length=128), nullable=False),
        sa.Column("output_hash", sa.String(length=128), nullable=False),
        sa.Column("artifact_hash", sa.String(length=128), nullable=False),
        sa.Column("canonical_artifact_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("validation_result_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["prompt_render_run_id"], ["prompt_render_runs.id"]),
        sa.ForeignKeyConstraint(["agent_context_pack_snapshot_id"], ["agent_context_pack_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_output_validation_runs_package", "agent_output_validation_runs", ["package_id"])
    op.create_index("ix_agent_output_validation_runs_project", "agent_output_validation_runs", ["video_project_id"])
    op.create_index("ix_agent_output_validation_runs_prompt_render", "agent_output_validation_runs", ["prompt_render_run_id"])
    op.create_index(
        "ix_agent_output_validation_runs_context_pack",
        "agent_output_validation_runs",
        ["agent_context_pack_snapshot_id"],
    )
    op.create_index("ix_agent_output_validation_runs_agent", "agent_output_validation_runs", ["agent_key"])
    op.create_index("ix_agent_output_validation_runs_status", "agent_output_validation_runs", ["status"])
    op.create_index("ix_agent_output_validation_runs_created_at", "agent_output_validation_runs", ["created_at"])

    op.create_table(
        "schema_violation_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("prompt_render_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("artifact_ref", sa.Text(), nullable=True),
        sa.Column("violation_type", sa.String(length=120), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("missing_fields", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("invalid_fields", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("repair_attempted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("repair_result", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["prompt_render_run_id"], ["prompt_render_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_schema_violation_ledger_package", "schema_violation_ledger", ["package_id"])
    op.create_index("ix_schema_violation_ledger_project", "schema_violation_ledger", ["video_project_id"])
    op.create_index("ix_schema_violation_ledger_agent", "schema_violation_ledger", ["agent_key"])
    op.create_index("ix_schema_violation_ledger_type", "schema_violation_ledger", ["violation_type"])
    op.create_index("ix_schema_violation_ledger_created_at", "schema_violation_ledger", ["created_at"])

    op.create_table(
        "r3d4_gate_batch_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("context_hash", sa.Text(), nullable=True),
        sa.Column("trigger_agent_key", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("hard_block_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("review_required_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("gate_results_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("reducer_decision_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["effective_context_snapshot_id"], ["effective_channel_runtime_context_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_r3d4_gate_batch_runs_package", "r3d4_gate_batch_runs", ["package_id"])
    op.create_index("ix_r3d4_gate_batch_runs_project", "r3d4_gate_batch_runs", ["video_project_id"])
    op.create_index("ix_r3d4_gate_batch_runs_effective_context", "r3d4_gate_batch_runs", ["effective_context_snapshot_id"])
    op.create_index("ix_r3d4_gate_batch_runs_status", "r3d4_gate_batch_runs", ["status"])
    op.create_index("ix_r3d4_gate_batch_runs_created_at", "r3d4_gate_batch_runs", ["created_at"])

    op.create_table(
        "r3d4_gate_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gate_batch_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gate_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("measurements_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("fail_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("blocking_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("checked_artifact_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("checked_contract_paths", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("repair_hint", sa.Text(), nullable=True),
        sa.Column("human_readable_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["gate_batch_run_id"], ["r3d4_gate_batch_runs.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["effective_context_snapshot_id"], ["effective_channel_runtime_context_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_r3d4_gate_runs_batch", "r3d4_gate_runs", ["gate_batch_run_id"])
    op.create_index("ix_r3d4_gate_runs_package", "r3d4_gate_runs", ["package_id"])
    op.create_index("ix_r3d4_gate_runs_project", "r3d4_gate_runs", ["video_project_id"])
    op.create_index("ix_r3d4_gate_runs_effective_context", "r3d4_gate_runs", ["effective_context_snapshot_id"])
    op.create_index("ix_r3d4_gate_runs_gate", "r3d4_gate_runs", ["gate_key"])
    op.create_index("ix_r3d4_gate_runs_status", "r3d4_gate_runs", ["status"])
    op.create_index("ix_r3d4_gate_runs_created_at", "r3d4_gate_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_r3d4_gate_runs_created_at", table_name="r3d4_gate_runs")
    op.drop_index("ix_r3d4_gate_runs_status", table_name="r3d4_gate_runs")
    op.drop_index("ix_r3d4_gate_runs_gate", table_name="r3d4_gate_runs")
    op.drop_index("ix_r3d4_gate_runs_effective_context", table_name="r3d4_gate_runs")
    op.drop_index("ix_r3d4_gate_runs_project", table_name="r3d4_gate_runs")
    op.drop_index("ix_r3d4_gate_runs_package", table_name="r3d4_gate_runs")
    op.drop_index("ix_r3d4_gate_runs_batch", table_name="r3d4_gate_runs")
    op.drop_table("r3d4_gate_runs")

    op.drop_index("ix_r3d4_gate_batch_runs_created_at", table_name="r3d4_gate_batch_runs")
    op.drop_index("ix_r3d4_gate_batch_runs_status", table_name="r3d4_gate_batch_runs")
    op.drop_index("ix_r3d4_gate_batch_runs_effective_context", table_name="r3d4_gate_batch_runs")
    op.drop_index("ix_r3d4_gate_batch_runs_project", table_name="r3d4_gate_batch_runs")
    op.drop_index("ix_r3d4_gate_batch_runs_package", table_name="r3d4_gate_batch_runs")
    op.drop_table("r3d4_gate_batch_runs")

    op.drop_index("ix_schema_violation_ledger_created_at", table_name="schema_violation_ledger")
    op.drop_index("ix_schema_violation_ledger_type", table_name="schema_violation_ledger")
    op.drop_index("ix_schema_violation_ledger_agent", table_name="schema_violation_ledger")
    op.drop_index("ix_schema_violation_ledger_project", table_name="schema_violation_ledger")
    op.drop_index("ix_schema_violation_ledger_package", table_name="schema_violation_ledger")
    op.drop_table("schema_violation_ledger")

    op.drop_index("ix_agent_output_validation_runs_created_at", table_name="agent_output_validation_runs")
    op.drop_index("ix_agent_output_validation_runs_status", table_name="agent_output_validation_runs")
    op.drop_index("ix_agent_output_validation_runs_agent", table_name="agent_output_validation_runs")
    op.drop_index("ix_agent_output_validation_runs_context_pack", table_name="agent_output_validation_runs")
    op.drop_index("ix_agent_output_validation_runs_prompt_render", table_name="agent_output_validation_runs")
    op.drop_index("ix_agent_output_validation_runs_project", table_name="agent_output_validation_runs")
    op.drop_index("ix_agent_output_validation_runs_package", table_name="agent_output_validation_runs")
    op.drop_table("agent_output_validation_runs")

    _drop_package_status_checks()
    op.execute(
        "ALTER TABLE first_scripted_video_packages "
        "ADD CONSTRAINT ck_first_scripted_video_packages_ck_first_scripted_video_packages_status "
        f"CHECK ({M12_2S_PACKAGE_STATUS_CHECK})"
    )
