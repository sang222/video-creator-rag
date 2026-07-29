"""Close out VCOS security, typed admission, and package authority v2.

Revision ID: 0043_vcos_phase123
Revises: 0042_mr1_final_lineage
Create Date: 2026-07-28 00:00:00

Historical rows are retained as schema version ``v1``.  The downgrade is
allowed only while no Phase 1 identity bridge or authoritative v2 planning /
package row exists, because removing those values would erase semantic truth.
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0043_vcos_phase123"
down_revision: str | None = "0042_mr1_final_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    _add_identity_bridge()
    _create_series_authorities()
    _extend_editorial_and_admission_authorities()
    _extend_video_project_authority()
    _add_canonical_package_projection_bindings()


def _add_identity_bridge() -> None:
    op.add_column(
        "operator_users",
        sa.Column("canonical_user_id", UUID, nullable=True),
    )
    op.create_foreign_key(
        "fk_operator_users_canonical_user_id_users",
        "operator_users",
        "users",
        ["canonical_user_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_operator_users_canonical_user_id",
        "operator_users",
        ["canonical_user_id"],
    )
    op.create_index(
        "ix_operator_users_canonical_user",
        "operator_users",
        ["canonical_user_id"],
    )


def _create_series_authorities() -> None:
    op.create_table(
        "series_plans",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("channel_profile_version_id", UUID, nullable=False),
        sa.Column("policy_snapshot_id", UUID, nullable=False),
        sa.Column("stable_series_key", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("editorial_promise", sa.Text(), nullable=False),
        sa.Column(
            "allowed_production_lanes",
            JSONB,
            server_default=_jsonb_array(),
            nullable=False,
        ),
        sa.Column(
            "episode_role_policy",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(length=40),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("supersedes_series_plan_id", UUID, nullable=True),
        sa.Column(
            "approval_evidence_refs",
            JSONB,
            server_default=_jsonb_array(),
            nullable=False,
        ),
        sa.Column(
            "state_reason_codes",
            JSONB,
            server_default=_jsonb_array(),
            nullable=False,
        ),
        sa.Column("created_by_user_id", UUID, nullable=False),
        sa.Column("approved_by_user_id", UUID, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
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
            "state in ('DRAFT','APPROVED','SUPERSEDED','ARCHIVED')",
            name="ck_series_plans_state",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_series_plans_version_positive",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(allowed_production_lanes) = 'array' "
            "and jsonb_array_length(allowed_production_lanes) > 0 "
            "and allowed_production_lanes "
            "<@ '[\"DAILY_SHORT\",\"LONG_FORM\"]'::jsonb",
            name="ck_series_plans_allowed_lanes",
        ),
        sa.CheckConstraint(
            "(state = 'APPROVED' and approved_by_user_id is not null "
            "and approved_at is not null "
            "and jsonb_array_length(approval_evidence_refs) > 0) "
            "or state <> 'APPROVED'",
            name="ck_series_plans_approval_evidence",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"], ["channel_workspaces.id"]
        ),
        sa.ForeignKeyConstraint(
            ["channel_profile_version_id"], ["channel_profile_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_series_plan_id"], ["series_plans.id"]
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "stable_series_key",
            "version",
            name="uq_series_plans_workspace_key_version",
        ),
    )
    for index_name, columns in (
        ("ix_series_plans_company_id", ["company_id"]),
        ("ix_series_plans_channel_workspace_id", ["channel_workspace_id"]),
        (
            "ix_series_plans_channel_profile_version_id",
            ["channel_profile_version_id"],
        ),
        ("ix_series_plans_policy_snapshot_id", ["policy_snapshot_id"]),
        ("ix_series_plans_stable_series_key", ["stable_series_key"]),
        ("ix_series_plans_state", ["state"]),
        ("ix_series_plans_created_at", ["created_at"]),
    ):
        op.create_index(index_name, "series_plans", columns)
    op.create_index(
        "uq_series_plans_one_approved_key",
        "series_plans",
        ["channel_workspace_id", "stable_series_key"],
        unique=True,
        postgresql_where=sa.text("state = 'APPROVED'"),
    )

    op.create_table(
        "series_runs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("series_plan_id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("channel_profile_version_id", UUID, nullable=False),
        sa.Column("policy_snapshot_id", UUID, nullable=False),
        sa.Column("run_key", sa.String(length=160), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column(
            "first_episode_number",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "next_episode_number",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "reserved_episode_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "published_episode_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "schedule_window_start",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "schedule_window_end",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "state",
            sa.String(length=40),
            server_default=sa.text("'PROPOSED'"),
            nullable=False,
        ),
        sa.Column(
            "state_reason_codes",
            JSONB,
            server_default=_jsonb_array(),
            nullable=False,
        ),
        sa.Column("created_by_user_id", UUID, nullable=False),
        sa.Column("approved_by_user_id", UUID, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "completion_pending_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "state in ('PROPOSED','APPROVED','SCHEDULED','ACTIVE','PAUSED',"
            "'COMPLETION_PENDING','COMPLETED','CANCELED','ARCHIVED')",
            name="ck_series_runs_state",
        ),
        sa.CheckConstraint(
            "run_number > 0 and capacity > 0 and first_episode_number > 0 "
            "and next_episode_number >= first_episode_number "
            "and reserved_episode_count >= 0 "
            "and reserved_episode_count <= capacity "
            "and published_episode_count >= 0 "
            "and published_episode_count <= reserved_episode_count",
            name="ck_series_runs_progress",
        ),
        sa.CheckConstraint(
            "schedule_window_end is null or schedule_window_start is null "
            "or schedule_window_end > schedule_window_start",
            name="ck_series_runs_schedule_window",
        ),
        sa.ForeignKeyConstraint(["series_plan_id"], ["series_plans.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"], ["channel_workspaces.id"]
        ),
        sa.ForeignKeyConstraint(
            ["channel_profile_version_id"], ["channel_profile_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "series_plan_id",
            "run_number",
            name="uq_series_runs_plan_run_number",
        ),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "run_key",
            name="uq_series_runs_workspace_run_key",
        ),
    )
    for index_name, columns in (
        ("ix_series_runs_series_plan_id", ["series_plan_id"]),
        ("ix_series_runs_company_id", ["company_id"]),
        ("ix_series_runs_channel_workspace_id", ["channel_workspace_id"]),
        ("ix_series_runs_state", ["state"]),
        ("ix_series_runs_created_at", ["created_at"]),
    ):
        op.create_index(index_name, "series_runs", columns)


def _extend_editorial_and_admission_authorities() -> None:
    slot_columns = (
        sa.Column(
            "schema_version",
            sa.String(length=16),
            server_default=sa.text("'v1'"),
            nullable=False,
        ),
        sa.Column("production_lane", sa.String(length=40), nullable=True),
        sa.Column("assignment_mode", sa.String(length=40), nullable=True),
        sa.Column("preferred_series_plan_id", UUID, nullable=True),
        sa.Column("preferred_series_run_id", UUID, nullable=True),
    )
    for column in slot_columns:
        op.add_column("editorial_calendar_slots", column)
    op.create_foreign_key(
        "fk_slots_v2_preferred_series_plan",
        "editorial_calendar_slots",
        "series_plans",
        ["preferred_series_plan_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_slots_v2_preferred_series_run",
        "editorial_calendar_slots",
        "series_runs",
        ["preferred_series_run_id"],
        ["id"],
    )
    for index_name, column in (
        ("ix_editorial_calendar_slots_production_lane", "production_lane"),
        ("ix_editorial_calendar_slots_assignment_mode", "assignment_mode"),
        (
            "ix_editorial_calendar_slots_preferred_series_plan_id",
            "preferred_series_plan_id",
        ),
        (
            "ix_editorial_calendar_slots_preferred_series_run_id",
            "preferred_series_run_id",
        ),
    ):
        op.create_index(index_name, "editorial_calendar_slots", [column])
    op.create_check_constraint(
        "ck_editorial_calendar_slots_v2_authority",
        "editorial_calendar_slots",
        "(schema_version = 'v1') or "
        "(schema_version = 'v2' "
        "and production_lane in ('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
        "and assignment_mode in ('SERIES_REQUIRED','SERIES_PREFERRED',"
        "'STANDALONE_REQUIRED','OPEN_MIX') "
        "and series_key is null "
        "and (preferred_series_run_id is null "
        "or preferred_series_plan_id is not null))",
    )
    op.create_check_constraint(
        "ck_editorial_calendar_slots_schema_version",
        "editorial_calendar_slots",
        "schema_version in ('v1','v2')",
    )

    op.add_column(
        "idea_market_preflights",
        sa.Column("editorial_calendar_slot_id", UUID, nullable=True),
    )
    op.create_foreign_key(
        "fk_preflight_v2_editorial_slot",
        "idea_market_preflights",
        "editorial_calendar_slots",
        ["editorial_calendar_slot_id"],
        ["id"],
    )
    op.create_index(
        "ix_idea_market_preflights_editorial_slot_id",
        "idea_market_preflights",
        ["editorial_calendar_slot_id"],
    )

    daily_columns = (
        sa.Column(
            "schema_version",
            sa.String(length=16),
            server_default=sa.text("'v1'"),
            nullable=False,
        ),
        sa.Column("production_lane", sa.String(length=40), nullable=True),
        sa.Column("proposed_content_mode", sa.String(length=40), nullable=True),
        sa.Column("assignment_input_ref", JSONB, nullable=True),
    )
    for column in daily_columns:
        op.add_column("daily_idea_decisions", column)
    op.create_index(
        "ix_daily_idea_decisions_production_lane",
        "daily_idea_decisions",
        ["production_lane"],
    )
    op.create_check_constraint(
        "ck_daily_idea_decisions_schema_version",
        "daily_idea_decisions",
        "schema_version in ('v1','v2')",
    )
    op.create_check_constraint(
        "ck_daily_idea_decisions_v2_daily_short",
        "daily_idea_decisions",
        "(schema_version = 'v1') or "
        "(schema_version = 'v2' and production_lane = 'DAILY_SHORT' "
        "and (proposed_content_mode is null "
        "or proposed_content_mode in ('SERIES_EPISODE','STANDALONE')) "
        "and assignment_input_ref is not null)",
    )

    admission_columns = (
        sa.Column(
            "schema_version",
            sa.String(length=16),
            server_default=sa.text("'v1'"),
            nullable=False,
        ),
        sa.Column("editorial_calendar_slot_id", UUID, nullable=True),
        sa.Column("company_id", UUID, nullable=True),
        sa.Column("channel_workspace_id", UUID, nullable=True),
        sa.Column("channel_profile_version_id", UUID, nullable=True),
        sa.Column("policy_snapshot_id", UUID, nullable=True),
        sa.Column("planning_source_type", sa.String(length=40), nullable=True),
        sa.Column("production_lane", sa.String(length=40), nullable=True),
        sa.Column("content_mode", sa.String(length=40), nullable=True),
        sa.Column("assignment_mode", sa.String(length=40), nullable=True),
        sa.Column("series_plan_id", UUID, nullable=True),
        sa.Column("series_run_id", UUID, nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("episode_role", sa.String(length=120), nullable=True),
        sa.Column(
            "standalone_reason_code",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("parent_video_project_id", UUID, nullable=True),
        sa.Column("parent_final_media_ref_id", UUID, nullable=True),
        sa.Column("canonical_timeline_ref", sa.Text(), nullable=True),
        sa.Column("canonical_timeline_hash", sa.String(length=64), nullable=True),
        sa.Column("resolver_version", sa.String(length=80), nullable=True),
        sa.Column("resolver_input_hash", sa.String(length=64), nullable=True),
        sa.Column("decision_hash", sa.String(length=64), nullable=True),
        sa.Column("assignment_input_ref", JSONB, nullable=True),
        sa.Column("duration_contract", JSONB, nullable=True),
    )
    for column in admission_columns:
        op.add_column("project_admission_decisions", column)
    op.alter_column(
        "project_admission_decisions",
        "channel_daily_run_id",
        existing_type=UUID,
        nullable=True,
    )
    op.alter_column(
        "project_admission_decisions",
        "daily_idea_decision_id",
        existing_type=UUID,
        nullable=True,
    )
    admission_fks = (
        (
            "fk_admission_v2_editorial_slot",
            "editorial_calendar_slot_id",
            "editorial_calendar_slots",
        ),
        (
            "fk_admission_v2_company",
            "company_id",
            "companies",
        ),
        (
            "fk_admission_v2_workspace",
            "channel_workspace_id",
            "channel_workspaces",
        ),
        (
            "fk_admission_v2_profile",
            "channel_profile_version_id",
            "channel_profile_versions",
        ),
        (
            "fk_admission_v2_policy",
            "policy_snapshot_id",
            "compiled_channel_policy_snapshots",
        ),
        (
            "fk_admission_v2_series_plan",
            "series_plan_id",
            "series_plans",
        ),
        (
            "fk_admission_v2_series_run",
            "series_run_id",
            "series_runs",
        ),
        (
            "fk_admission_v2_parent_project",
            "parent_video_project_id",
            "video_projects",
        ),
        (
            "fk_admission_v2_parent_media",
            "parent_final_media_ref_id",
            "final_media_refs",
        ),
    )
    for name, source_column, target_table in admission_fks:
        op.create_foreign_key(
            name,
            "project_admission_decisions",
            target_table,
            [source_column],
            ["id"],
        )
    for index_name, column in (
        (
            "ix_project_admission_decisions_editorial_slot_id",
            "editorial_calendar_slot_id",
        ),
        (
            "ix_project_admission_decisions_planning_source_type",
            "planning_source_type",
        ),
        (
            "ix_project_admission_decisions_production_lane",
            "production_lane",
        ),
        ("ix_project_admission_decisions_series_plan_id", "series_plan_id"),
        ("ix_project_admission_decisions_series_run_id", "series_run_id"),
    ):
        op.create_index(index_name, "project_admission_decisions", [column])
    op.create_index(
        "uq_project_admission_series_episode",
        "project_admission_decisions",
        ["series_run_id", "episode_number"],
        unique=True,
        postgresql_where=sa.text(
            "series_run_id is not null and episode_number is not null"
        ),
    )
    op.create_index(
        "uq_project_admission_v2_daily_source",
        "project_admission_decisions",
        ["daily_idea_decision_id"],
        unique=True,
        postgresql_where=sa.text(
            "schema_version = 'v2' "
            "and planning_source_type = 'DAILY_IDEA' "
            "and daily_idea_decision_id is not null"
        ),
    )
    op.create_index(
        "uq_project_admission_v2_long_form_source",
        "project_admission_decisions",
        ["editorial_calendar_slot_id"],
        unique=True,
        postgresql_where=sa.text(
            "schema_version = 'v2' "
            "and planning_source_type = 'LONG_FORM_PLAN' "
            "and editorial_calendar_slot_id is not null"
        ),
    )
    op.create_unique_constraint(
        "uq_project_admission_decisions_decision_hash",
        "project_admission_decisions",
        ["decision_hash"],
    )
    op.create_check_constraint(
        "ck_project_admission_decisions_schema_version",
        "project_admission_decisions",
        "schema_version in ('v1','v2')",
    )
    op.create_check_constraint(
        "ck_project_admission_decisions_v2_authority",
        "project_admission_decisions",
        "(schema_version = 'v1') or "
        "(schema_version = 'v2' "
        "and company_id is not null "
        "and channel_workspace_id is not null "
        "and channel_profile_version_id is not null "
        "and policy_snapshot_id is not null "
        "and planning_source_type in "
        "('DAILY_IDEA','LONG_FORM_PLAN','DERIVED_SHORT') "
        "and production_lane in "
        "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
        "and assignment_mode in "
        "('SERIES_REQUIRED','SERIES_PREFERRED','STANDALONE_REQUIRED','OPEN_MIX') "
        "and resolver_version is not null "
        "and resolver_input_hash ~ '^[0-9a-f]{64}$' "
        "and decision_hash ~ '^[0-9a-f]{64}$' "
        "and assignment_input_ref is not null "
        "and ((decision = 'BLOCK') or "
        "(decision = 'ADMIT' and admitted_video_project_id is not null "
        "and duration_contract is not null "
        "and ((content_mode = 'SERIES_EPISODE' "
        "and series_plan_id is not null and series_run_id is not null "
        "and episode_number > 0 and standalone_reason_code is null) "
        "or (content_mode = 'STANDALONE' "
        "and series_plan_id is null and series_run_id is null "
        "and episode_number is null and episode_role is null "
        "and standalone_reason_code is not null)))))",
    )
    op.create_check_constraint(
        "ck_project_admission_decisions_v2_lane_source",
        "project_admission_decisions",
        "(schema_version = 'v1') or (decision = 'BLOCK') or "
        "((planning_source_type = 'DAILY_IDEA' "
        "and production_lane = 'DAILY_SHORT' "
        "and channel_daily_run_id is not null "
        "and daily_idea_decision_id is not null) "
        "or (planning_source_type = 'LONG_FORM_PLAN' "
        "and production_lane = 'LONG_FORM' "
        "and editorial_calendar_slot_id is not null "
        "and channel_daily_run_id is null "
        "and daily_idea_decision_id is null) "
        "or (planning_source_type = 'DERIVED_SHORT' "
        "and production_lane = 'LONG_DERIVED_SHORT' "
        "and content_mode = 'STANDALONE' "
        "and assignment_mode = 'STANDALONE_REQUIRED' "
        "and parent_video_project_id is not null "
        "and canonical_timeline_ref is not null "
        "and canonical_timeline_hash ~ '^[0-9a-f]{64}$'))",
    )


def _extend_video_project_authority() -> None:
    project_columns = (
        sa.Column(
            "schema_version",
            sa.String(length=16),
            server_default=sa.text("'v1'"),
            nullable=False,
        ),
        sa.Column("planning_source_type", sa.String(length=40), nullable=True),
        sa.Column("production_lane", sa.String(length=40), nullable=True),
        sa.Column("content_mode", sa.String(length=40), nullable=True),
        sa.Column("assignment_mode", sa.String(length=40), nullable=True),
        sa.Column("series_plan_id", UUID, nullable=True),
        sa.Column("series_run_id", UUID, nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("episode_role", sa.String(length=120), nullable=True),
        sa.Column(
            "standalone_reason_code",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("project_admission_decision_id", UUID, nullable=True),
        sa.Column("parent_video_project_id", UUID, nullable=True),
        sa.Column("parent_final_media_ref_id", UUID, nullable=True),
        sa.Column("canonical_timeline_ref", sa.Text(), nullable=True),
        sa.Column("canonical_timeline_hash", sa.String(length=64), nullable=True),
        sa.Column("duration_contract", JSONB, nullable=True),
        sa.Column(
            "render_eligible",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    for column in project_columns:
        op.add_column("video_projects", column)
    project_fks = (
        (
            "fk_video_projects_v2_series_plan",
            "series_plan_id",
            "series_plans",
        ),
        (
            "fk_video_projects_v2_series_run",
            "series_run_id",
            "series_runs",
        ),
        (
            "fk_video_projects_v2_admission",
            "project_admission_decision_id",
            "project_admission_decisions",
        ),
        (
            "fk_video_projects_v2_parent_project",
            "parent_video_project_id",
            "video_projects",
        ),
        (
            "fk_video_projects_v2_parent_media",
            "parent_final_media_ref_id",
            "final_media_refs",
        ),
    )
    for name, source_column, target_table in project_fks:
        options = (
            {"deferrable": True, "initially": "DEFERRED"}
            if name == "fk_video_projects_v2_admission"
            else {}
        )
        op.create_foreign_key(
            name,
            "video_projects",
            target_table,
            [source_column],
            ["id"],
            **options,
        )
    for index_name, column in (
        ("ix_video_projects_production_lane", "production_lane"),
        ("ix_video_projects_series_plan_id", "series_plan_id"),
        ("ix_video_projects_series_run_id", "series_run_id"),
        ("ix_video_projects_parent_video_project_id", "parent_video_project_id"),
    ):
        op.create_index(index_name, "video_projects", [column])
    op.create_index(
        "ix_video_projects_project_admission_decision_id",
        "video_projects",
        ["project_admission_decision_id"],
        unique=True,
    )
    op.create_unique_constraint(
        "uq_video_projects_series_run_episode",
        "video_projects",
        ["series_run_id", "episode_number"],
    )
    op.create_check_constraint(
        "ck_video_projects_schema_version",
        "video_projects",
        "schema_version in ('v1','v2')",
    )
    op.create_check_constraint(
        "ck_video_projects_v2_assignment",
        "video_projects",
        "(schema_version = 'v1') or "
        "(schema_version = 'v2' "
        "and channel_profile_version_id is not null "
        "and project_admission_decision_id is not null "
        "and planning_source_type in "
        "('DAILY_IDEA','LONG_FORM_PLAN','DERIVED_SHORT') "
        "and production_lane in "
        "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
        "and content_mode in ('SERIES_EPISODE','STANDALONE') "
        "and assignment_mode in "
        "('SERIES_REQUIRED','SERIES_PREFERRED','STANDALONE_REQUIRED','OPEN_MIX') "
        "and duration_contract is not null "
        "and ((content_mode = 'SERIES_EPISODE' "
        "and series_plan_id is not null and series_run_id is not null "
        "and episode_number > 0 and standalone_reason_code is null) "
        "or (content_mode = 'STANDALONE' "
        "and series_plan_id is null and series_run_id is null "
        "and episode_number is null and episode_role is null "
        "and standalone_reason_code is not null)))",
    )
    op.create_check_constraint(
        "ck_video_projects_v2_lane_source",
        "video_projects",
        "(schema_version = 'v1') or "
        "((planning_source_type = 'DAILY_IDEA' "
        "and production_lane = 'DAILY_SHORT') "
        "or (planning_source_type = 'LONG_FORM_PLAN' "
        "and production_lane = 'LONG_FORM') "
        "or (planning_source_type = 'DERIVED_SHORT' "
        "and production_lane = 'LONG_DERIVED_SHORT' "
        "and content_mode = 'STANDALONE' "
        "and assignment_mode = 'STANDALONE_REQUIRED' "
        "and parent_video_project_id is not null "
        "and canonical_timeline_ref is not null "
        "and canonical_timeline_hash ~ '^[0-9a-f]{64}$' "
        "and render_eligible = false))",
    )


def _add_canonical_package_projection_bindings() -> None:
    op.create_index(
        "uq_artifacts_v2_authority_per_project",
        "artifacts",
        ["video_project_id", "artifact_type"],
        unique=True,
        postgresql_where=sa.text(
            "artifact_type in "
            "('production_package','production_readiness_receipt')"
        ),
    )
    projection_tables = (
        ("long_form_render_packages", "fk_lfrp_v2_production_package"),
        ("final_media_refs", "fk_final_media_v2_production_package"),
        ("render_package_snapshots", "fk_render_v2_production_package"),
        ("publish_handoff_packages", "fk_handoff_v2_production_package"),
    )
    for table_name, fk_name in projection_tables:
        op.add_column(
            table_name,
            sa.Column(
                "production_package_artifact_version_id",
                UUID,
                nullable=True,
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "production_package_hash",
                sa.String(length=64),
                nullable=True,
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "duration_contract",
                JSONB,
                nullable=True,
            ),
        )
        op.create_foreign_key(
            fk_name,
            table_name,
            "artifact_versions",
            ["production_package_artifact_version_id"],
            ["id"],
        )
        op.create_check_constraint(
            f"ck_{table_name}_production_package_binding",
            table_name,
            "(production_package_artifact_version_id is null "
            "and production_package_hash is null "
            "and duration_contract is null) or "
            "(production_package_artifact_version_id is not null "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and duration_contract is not null "
            "and jsonb_typeof(duration_contract) = 'object')",
        )
    op.create_index(
        "ix_long_form_render_packages_production_package",
        "long_form_render_packages",
        ["production_package_artifact_version_id"],
    )
    op.create_index(
        "ix_final_media_refs_production_package",
        "final_media_refs",
        ["production_package_artifact_version_id"],
    )
    op.create_index(
        "ix_render_package_snapshots_production_package",
        "render_package_snapshots",
        ["production_package_artifact_version_id"],
    )
    op.create_index(
        "ix_publish_handoff_packages_production_package",
        "publish_handoff_packages",
        ["production_package_artifact_version_id"],
    )


def downgrade() -> None:
    _fail_closed_if_authoritative_rows_exist()
    _drop_canonical_package_projection_bindings()
    _drop_video_project_authority()
    _drop_editorial_and_admission_authorities()
    _drop_series_authorities()
    _drop_identity_bridge()


def _fail_closed_if_authoritative_rows_exist() -> None:
    authority_predicate = """
        EXISTS (SELECT 1 FROM operator_users
                WHERE canonical_user_id IS NOT NULL)
        OR EXISTS (SELECT 1 FROM series_plans)
        OR EXISTS (SELECT 1 FROM series_runs)
        OR EXISTS (SELECT 1 FROM editorial_calendar_slots
                    WHERE schema_version = 'v2')
        OR EXISTS (SELECT 1 FROM daily_idea_decisions
                    WHERE schema_version = 'v2')
        OR EXISTS (SELECT 1 FROM project_admission_decisions
                    WHERE schema_version = 'v2')
        OR EXISTS (SELECT 1 FROM video_projects
                    WHERE schema_version = 'v2')
        OR EXISTS (
            SELECT 1
            FROM artifact_versions av
            JOIN artifacts a ON a.id = av.artifact_id
            WHERE a.artifact_type IN (
                'production_package',
                'production_readiness_receipt'
            )
            AND av.content->>'schema_version' IN (
                'production.package.v2',
                'production.readiness-receipt.v2'
            )
        )
    """
    if context.is_offline_mode():
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF {authority_predicate} THEN
                        RAISE EXCEPTION
                            '0043 downgrade refused: authoritative Phase 1/v2 rows exist';
                    END IF;
                END
                $$;
                """
            )
        )
        return

    bind = op.get_bind()
    has_authority = bind.execute(
        sa.text(f"SELECT {authority_predicate}")
    ).scalar_one()
    if has_authority:
        raise RuntimeError(
            "0043 downgrade refused: authoritative Phase 1/v2 rows exist"
        )


def _drop_canonical_package_projection_bindings() -> None:
    projection_tables = (
        (
            "publish_handoff_packages",
            "ix_publish_handoff_packages_production_package",
            "fk_handoff_v2_production_package",
        ),
        (
            "render_package_snapshots",
            "ix_render_package_snapshots_production_package",
            "fk_render_v2_production_package",
        ),
        (
            "final_media_refs",
            "ix_final_media_refs_production_package",
            "fk_final_media_v2_production_package",
        ),
        (
            "long_form_render_packages",
            "ix_long_form_render_packages_production_package",
            "fk_lfrp_v2_production_package",
        ),
    )
    for table_name, index_name, fk_name in projection_tables:
        op.drop_index(index_name, table_name=table_name)
        op.drop_constraint(
            f"ck_{table_name}_production_package_binding",
            table_name,
            type_="check",
        )
        op.drop_constraint(
            fk_name,
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "duration_contract")
        op.drop_column(table_name, "production_package_hash")
        op.drop_column(
            table_name, "production_package_artifact_version_id"
        )
    op.drop_index(
        "uq_artifacts_v2_authority_per_project",
        table_name="artifacts",
    )


def _drop_video_project_authority() -> None:
    for name in (
        "ck_video_projects_v2_lane_source",
        "ck_video_projects_v2_assignment",
        "ck_video_projects_schema_version",
    ):
        op.drop_constraint(name, "video_projects", type_="check")
    op.drop_constraint(
        "uq_video_projects_series_run_episode",
        "video_projects",
        type_="unique",
    )
    for index_name in (
        "ix_video_projects_project_admission_decision_id",
        "ix_video_projects_parent_video_project_id",
        "ix_video_projects_series_run_id",
        "ix_video_projects_series_plan_id",
        "ix_video_projects_production_lane",
    ):
        op.drop_index(index_name, table_name="video_projects")
    for name in (
        "fk_video_projects_v2_parent_media",
        "fk_video_projects_v2_parent_project",
        "fk_video_projects_v2_admission",
        "fk_video_projects_v2_series_run",
        "fk_video_projects_v2_series_plan",
    ):
        op.drop_constraint(name, "video_projects", type_="foreignkey")
    for column_name in (
        "render_eligible",
        "duration_contract",
        "canonical_timeline_hash",
        "canonical_timeline_ref",
        "parent_final_media_ref_id",
        "parent_video_project_id",
        "project_admission_decision_id",
        "standalone_reason_code",
        "episode_role",
        "episode_number",
        "series_run_id",
        "series_plan_id",
        "assignment_mode",
        "content_mode",
        "production_lane",
        "planning_source_type",
        "schema_version",
    ):
        op.drop_column("video_projects", column_name)


def _drop_editorial_and_admission_authorities() -> None:
    for name in (
        "ck_project_admission_decisions_v2_lane_source",
        "ck_project_admission_decisions_v2_authority",
        "ck_project_admission_decisions_schema_version",
    ):
        op.drop_constraint(
            name, "project_admission_decisions", type_="check"
        )
    op.drop_constraint(
        "uq_project_admission_decisions_decision_hash",
        "project_admission_decisions",
        type_="unique",
    )
    for index_name in (
        "uq_project_admission_v2_long_form_source",
        "uq_project_admission_v2_daily_source",
        "uq_project_admission_series_episode",
        "ix_project_admission_decisions_series_run_id",
        "ix_project_admission_decisions_series_plan_id",
        "ix_project_admission_decisions_production_lane",
        "ix_project_admission_decisions_planning_source_type",
        "ix_project_admission_decisions_editorial_slot_id",
    ):
        op.drop_index(
            index_name, table_name="project_admission_decisions"
        )
    for name in (
        "fk_admission_v2_parent_media",
        "fk_admission_v2_parent_project",
        "fk_admission_v2_series_run",
        "fk_admission_v2_series_plan",
        "fk_admission_v2_policy",
        "fk_admission_v2_profile",
        "fk_admission_v2_workspace",
        "fk_admission_v2_company",
        "fk_admission_v2_editorial_slot",
    ):
        op.drop_constraint(
            name, "project_admission_decisions", type_="foreignkey"
        )
    op.alter_column(
        "project_admission_decisions",
        "daily_idea_decision_id",
        existing_type=UUID,
        nullable=False,
    )
    op.alter_column(
        "project_admission_decisions",
        "channel_daily_run_id",
        existing_type=UUID,
        nullable=False,
    )
    for column_name in (
        "duration_contract",
        "assignment_input_ref",
        "decision_hash",
        "resolver_input_hash",
        "resolver_version",
        "canonical_timeline_hash",
        "canonical_timeline_ref",
        "parent_final_media_ref_id",
        "parent_video_project_id",
        "standalone_reason_code",
        "episode_role",
        "episode_number",
        "series_run_id",
        "series_plan_id",
        "assignment_mode",
        "content_mode",
        "production_lane",
        "planning_source_type",
        "policy_snapshot_id",
        "channel_profile_version_id",
        "channel_workspace_id",
        "company_id",
        "editorial_calendar_slot_id",
        "schema_version",
    ):
        op.drop_column("project_admission_decisions", column_name)

    op.drop_constraint(
        "ck_daily_idea_decisions_v2_daily_short",
        "daily_idea_decisions",
        type_="check",
    )
    op.drop_constraint(
        "ck_daily_idea_decisions_schema_version",
        "daily_idea_decisions",
        type_="check",
    )
    op.drop_index(
        "ix_daily_idea_decisions_production_lane",
        table_name="daily_idea_decisions",
    )
    for column_name in (
        "assignment_input_ref",
        "proposed_content_mode",
        "production_lane",
        "schema_version",
    ):
        op.drop_column("daily_idea_decisions", column_name)

    op.drop_index(
        "ix_idea_market_preflights_editorial_slot_id",
        table_name="idea_market_preflights",
    )
    op.drop_constraint(
        "fk_preflight_v2_editorial_slot",
        "idea_market_preflights",
        type_="foreignkey",
    )
    op.drop_column(
        "idea_market_preflights", "editorial_calendar_slot_id"
    )

    op.drop_constraint(
        "ck_editorial_calendar_slots_schema_version",
        "editorial_calendar_slots",
        type_="check",
    )
    op.drop_constraint(
        "ck_editorial_calendar_slots_v2_authority",
        "editorial_calendar_slots",
        type_="check",
    )
    for index_name in (
        "ix_editorial_calendar_slots_preferred_series_run_id",
        "ix_editorial_calendar_slots_preferred_series_plan_id",
        "ix_editorial_calendar_slots_assignment_mode",
        "ix_editorial_calendar_slots_production_lane",
    ):
        op.drop_index(
            index_name, table_name="editorial_calendar_slots"
        )
    op.drop_constraint(
        "fk_slots_v2_preferred_series_run",
        "editorial_calendar_slots",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_slots_v2_preferred_series_plan",
        "editorial_calendar_slots",
        type_="foreignkey",
    )
    for column_name in (
        "preferred_series_run_id",
        "preferred_series_plan_id",
        "assignment_mode",
        "production_lane",
        "schema_version",
    ):
        op.drop_column("editorial_calendar_slots", column_name)


def _drop_series_authorities() -> None:
    op.drop_table("series_runs")
    op.drop_table("series_plans")


def _drop_identity_bridge() -> None:
    op.drop_index(
        "ix_operator_users_canonical_user",
        table_name="operator_users",
    )
    op.drop_constraint(
        "uq_operator_users_canonical_user_id",
        "operator_users",
        type_="unique",
    )
    op.drop_constraint(
        "fk_operator_users_canonical_user_id_users",
        "operator_users",
        type_="foreignkey",
    )
    op.drop_column("operator_users", "canonical_user_id")
