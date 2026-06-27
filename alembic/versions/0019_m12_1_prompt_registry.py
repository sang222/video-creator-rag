"""M12.1 prompt registry, channel contract binding, and prompt audit snapshots

Revision ID: 0019_m12_1_prompt_registry
Revises: 0018_m12_provider_readiness
Create Date: 2026-06-27 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0019_m12_1_prompt_registry"
down_revision: str | None = "0018_m12_provider_readiness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

STATUS_CHECK = "status in ('DRAFT','ACTIVE','DEPRECATED')"
VALIDATION_STATUS_CHECK = "validation_status in ('OK','REVIEW_REQUIRED','BLOCK','ERROR')"
EVAL_STATE_CHECK = "run_state in ('PASS','FAIL','SKIPPED','ERROR')"


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _uuid(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "prompt_template_records",
        _uuid("id"),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("template_key", sa.String(length=160), nullable=False),
        sa.Column("template_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="DRAFT"),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.String(length=128), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(STATUS_CHECK, name="ck_prompt_template_records_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_key", "template_key", "template_version", name="uq_prompt_template_records_identity"),
    )
    op.create_index("ix_prompt_template_records_agent", "prompt_template_records", ["agent_key"])
    op.create_index("ix_prompt_template_records_status", "prompt_template_records", ["status"])
    op.create_index("ix_prompt_template_records_hash", "prompt_template_records", ["prompt_hash"])

    op.create_table(
        "agent_prompt_profiles",
        _uuid("id"),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("default_router_lane", sa.String(length=160), nullable=False),
        sa.Column("allowed_router_lanes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("input_contract", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("output_contract", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("safety_policy_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("common_skill_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("channel_contract_required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("market_locale_context_required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="DRAFT"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(STATUS_CHECK, name="ck_agent_prompt_profiles_status"),
        sa.CheckConstraint("jsonb_typeof(allowed_router_lanes) = 'array'", name="ck_agent_prompt_profiles_lanes_array"),
        sa.CheckConstraint("jsonb_typeof(safety_policy_refs) = 'array'", name="ck_agent_prompt_profiles_safety_array"),
        sa.CheckConstraint("jsonb_typeof(common_skill_refs) = 'array'", name="ck_agent_prompt_profiles_common_array"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_key", name="uq_agent_prompt_profiles_agent_key"),
    )
    op.create_index("ix_agent_prompt_profiles_lane", "agent_prompt_profiles", ["default_router_lane"])
    op.create_index("ix_agent_prompt_profiles_status", "agent_prompt_profiles", ["status"])

    op.create_table(
        "prompt_contract_versions",
        _uuid("id"),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("template_key", sa.String(length=160), nullable=False),
        sa.Column("template_version", sa.String(length=80), nullable=False),
        sa.Column("input_contract", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("output_contract", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("schema_ref", sa.String(length=160), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="DRAFT"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(STATUS_CHECK, name="ck_prompt_contract_versions_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_key", "template_key", "template_version", name="uq_prompt_contract_versions_identity"),
    )
    op.create_index("ix_prompt_contract_versions_schema", "prompt_contract_versions", ["schema_ref", "schema_version"])
    op.create_index("ix_prompt_contract_versions_status", "prompt_contract_versions", ["status"])

    op.create_table(
        "structured_output_schemas",
        _uuid("id"),
        sa.Column("schema_ref", sa.String(length=160), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("dialect", sa.String(length=80), nullable=False),
        sa.Column("json_schema", JSONB, nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="DRAFT"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(STATUS_CHECK, name="ck_structured_output_schemas_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schema_ref", "schema_version", name="uq_structured_output_schemas_identity"),
    )
    op.create_index("ix_structured_output_schemas_status", "structured_output_schemas", ["status"])

    op.create_table(
        "prompt_render_runs",
        _uuid("id"),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("template_key", sa.String(length=160), nullable=False),
        sa.Column("template_version", sa.String(length=80), nullable=False),
        sa.Column("rendered_messages", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("prompt_hash", sa.String(length=128), nullable=False),
        sa.Column("prompt_context_hash", sa.String(length=128), nullable=False),
        sa.Column("input_payload_ref", sa.Text(), nullable=True),
        sa.Column("output_schema_ref", sa.String(length=160), nullable=False),
        sa.Column("router_lane", sa.String(length=160), nullable=False),
        _uuid("channel_profile_version_id", nullable=True),
        _uuid("compiled_policy_snapshot_id", nullable=True),
        sa.Column("channel_contract_json", JSONB, nullable=True),
        sa.Column("compiled_policy_snapshot_json", JSONB, nullable=True),
        sa.Column("market_locale_context_json", JSONB, nullable=True),
        sa.Column("render_vars_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("artifact_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("validation_status", sa.String(length=40), nullable=False),
        _created_at(),
        sa.CheckConstraint(VALIDATION_STATUS_CHECK, name="ck_prompt_render_runs_validation_status"),
        sa.CheckConstraint("jsonb_typeof(rendered_messages) = 'array'", name="ck_prompt_render_runs_messages_array"),
        sa.CheckConstraint("jsonb_typeof(render_vars_json) = 'object'", name="ck_prompt_render_runs_vars_object"),
        sa.CheckConstraint("jsonb_typeof(artifact_refs) = 'array'", name="ck_prompt_render_runs_artifact_refs_array"),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"]),
        sa.ForeignKeyConstraint(["compiled_policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_render_runs_agent", "prompt_render_runs", ["agent_key"])
    op.create_index("ix_prompt_render_runs_hash", "prompt_render_runs", ["prompt_hash", "prompt_context_hash"])
    op.create_index("ix_prompt_render_runs_channel_profile", "prompt_render_runs", ["channel_profile_version_id"])
    op.create_index("ix_prompt_render_runs_policy_snapshot", "prompt_render_runs", ["compiled_policy_snapshot_id"])
    op.create_index("ix_prompt_render_runs_created_at", "prompt_render_runs", ["created_at"])

    op.create_table(
        "prompt_audit_snapshots",
        _uuid("id"),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("template_key", sa.String(length=160), nullable=False),
        sa.Column("template_version", sa.String(length=80), nullable=False),
        _uuid("channel_profile_version_id", nullable=True),
        _uuid("compiled_policy_snapshot_id", nullable=True),
        sa.Column("prompt_hash", sa.String(length=128), nullable=False),
        sa.Column("prompt_context_hash", sa.String(length=128), nullable=False),
        sa.Column("router_lane", sa.String(length=160), nullable=False),
        sa.Column("provider_attempt_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        _uuid("prompt_render_run_id", nullable=True),
        sa.Column("final_output_ref", sa.Text(), nullable=True),
        sa.Column("validation_result", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("repair_attempts", JSONB, server_default=_jsonb_array(), nullable=False),
        _created_at(),
        sa.CheckConstraint("jsonb_typeof(provider_attempt_refs) = 'array'", name="ck_prompt_audit_snapshots_provider_refs_array"),
        sa.CheckConstraint("jsonb_typeof(validation_result) = 'object'", name="ck_prompt_audit_snapshots_validation_object"),
        sa.CheckConstraint("jsonb_typeof(repair_attempts) = 'array'", name="ck_prompt_audit_snapshots_repairs_array"),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"]),
        sa.ForeignKeyConstraint(["compiled_policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["prompt_render_run_id"], ["prompt_render_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_audit_snapshots_agent", "prompt_audit_snapshots", ["agent_key"])
    op.create_index("ix_prompt_audit_snapshots_render_run", "prompt_audit_snapshots", ["prompt_render_run_id"])
    op.create_index("ix_prompt_audit_snapshots_hash", "prompt_audit_snapshots", ["prompt_hash", "prompt_context_hash"])
    op.create_index("ix_prompt_audit_snapshots_created_at", "prompt_audit_snapshots", ["created_at"])

    op.create_table(
        "prompt_evaluation_cases",
        _uuid("id"),
        sa.Column("case_key", sa.String(length=160), nullable=False),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("template_key", sa.String(length=160), nullable=False),
        sa.Column("template_version", sa.String(length=80), nullable=False),
        sa.Column("input_fixture_ref", sa.Text(), nullable=False),
        sa.Column("expected_outcome", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("pass_criteria", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="DRAFT"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(STATUS_CHECK, name="ck_prompt_evaluation_cases_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_key", name="uq_prompt_evaluation_cases_case_key"),
    )
    op.create_index("ix_prompt_evaluation_cases_agent", "prompt_evaluation_cases", ["agent_key"])
    op.create_index("ix_prompt_evaluation_cases_status", "prompt_evaluation_cases", ["status"])

    op.create_table(
        "prompt_evaluation_runs",
        _uuid("id"),
        sa.Column("case_key", sa.String(length=160), nullable=False),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("template_version", sa.String(length=80), nullable=False),
        sa.Column("run_state", sa.String(length=40), nullable=False),
        sa.Column("output_ref", sa.Text(), nullable=True),
        sa.Column("validation_result", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint(EVAL_STATE_CHECK, name="ck_prompt_evaluation_runs_state"),
        sa.CheckConstraint("jsonb_typeof(validation_result) = 'object'", name="ck_prompt_evaluation_runs_validation_object"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_evaluation_runs_case", "prompt_evaluation_runs", ["case_key"])
    op.create_index("ix_prompt_evaluation_runs_agent", "prompt_evaluation_runs", ["agent_key"])
    op.create_index("ix_prompt_evaluation_runs_state", "prompt_evaluation_runs", ["run_state"])
    op.create_index("ix_prompt_evaluation_runs_created_at", "prompt_evaluation_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_prompt_evaluation_runs_created_at", table_name="prompt_evaluation_runs")
    op.drop_index("ix_prompt_evaluation_runs_state", table_name="prompt_evaluation_runs")
    op.drop_index("ix_prompt_evaluation_runs_agent", table_name="prompt_evaluation_runs")
    op.drop_index("ix_prompt_evaluation_runs_case", table_name="prompt_evaluation_runs")
    op.drop_table("prompt_evaluation_runs")

    op.drop_index("ix_prompt_evaluation_cases_status", table_name="prompt_evaluation_cases")
    op.drop_index("ix_prompt_evaluation_cases_agent", table_name="prompt_evaluation_cases")
    op.drop_table("prompt_evaluation_cases")

    op.drop_index("ix_prompt_audit_snapshots_created_at", table_name="prompt_audit_snapshots")
    op.drop_index("ix_prompt_audit_snapshots_hash", table_name="prompt_audit_snapshots")
    op.drop_index("ix_prompt_audit_snapshots_render_run", table_name="prompt_audit_snapshots")
    op.drop_index("ix_prompt_audit_snapshots_agent", table_name="prompt_audit_snapshots")
    op.drop_table("prompt_audit_snapshots")

    op.drop_index("ix_prompt_render_runs_created_at", table_name="prompt_render_runs")
    op.drop_index("ix_prompt_render_runs_policy_snapshot", table_name="prompt_render_runs")
    op.drop_index("ix_prompt_render_runs_channel_profile", table_name="prompt_render_runs")
    op.drop_index("ix_prompt_render_runs_hash", table_name="prompt_render_runs")
    op.drop_index("ix_prompt_render_runs_agent", table_name="prompt_render_runs")
    op.drop_table("prompt_render_runs")

    op.drop_index("ix_structured_output_schemas_status", table_name="structured_output_schemas")
    op.drop_table("structured_output_schemas")

    op.drop_index("ix_prompt_contract_versions_status", table_name="prompt_contract_versions")
    op.drop_index("ix_prompt_contract_versions_schema", table_name="prompt_contract_versions")
    op.drop_table("prompt_contract_versions")

    op.drop_index("ix_agent_prompt_profiles_status", table_name="agent_prompt_profiles")
    op.drop_index("ix_agent_prompt_profiles_lane", table_name="agent_prompt_profiles")
    op.drop_table("agent_prompt_profiles")

    op.drop_index("ix_prompt_template_records_hash", table_name="prompt_template_records")
    op.drop_index("ix_prompt_template_records_status", table_name="prompt_template_records")
    op.drop_index("ix_prompt_template_records_agent", table_name="prompt_template_records")
    op.drop_table("prompt_template_records")
