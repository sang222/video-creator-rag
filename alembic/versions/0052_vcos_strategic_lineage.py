"""Freeze strategic lineage on launch-era long-form admissions and projects.

Revision ID: 0052_vcos_strategic_lineage
Revises: 0051_openai_luna_terra_cutover
Create Date: 2026-08-01 10:30:00
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0052_vcos_strategic_lineage"
down_revision: str | None = "0051_openai_luna_terra_cutover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _lineage_columns() -> tuple[sa.Column[object], ...]:
    """Build fresh Column objects for each table without deprecated copying."""

    return (
        sa.Column("audience_promise", sa.Text(), nullable=True),
        sa.Column("audience_promise_version", sa.String(length=120), nullable=True),
        sa.Column("audience_promise_hash", sa.String(length=64), nullable=True),
        sa.Column("target_audience_definition", JSONB, nullable=True),
        sa.Column("audience_drift_guard_version", sa.String(length=120), nullable=True),
        sa.Column("strategic_intent", sa.String(length=40), nullable=True),
        sa.Column("intent_success_criteria", JSONB, nullable=True),
        sa.Column(
            "intent_success_criteria_version", sa.String(length=120), nullable=True
        ),
        sa.Column("intent_success_criteria_hash", sa.String(length=64), nullable=True),
        sa.Column("experiment_hypothesis", sa.Text(), nullable=True),
        sa.Column("decision_reversibility", sa.String(length=32), nullable=True),
        sa.Column("active_launch_policy_version_id", UUID, nullable=True),
        sa.Column("active_launch_policy_hash", sa.String(length=64), nullable=True),
        sa.Column("active_launch_run_id", UUID, nullable=True),
        sa.Column("active_launch_run_hash", sa.String(length=64), nullable=True),
    )


_V2_LINEAGE_CHECK = """
(schema_version = 'v1') or (
    audience_promise is not null and btrim(audience_promise) <> ''
    and audience_promise_version is not null
    and audience_promise_hash ~ '^[0-9a-f]{64}$'
    and target_audience_definition is not null
    and jsonb_typeof(target_audience_definition) = 'object'
    and target_audience_definition <> '{}'::jsonb
    and audience_drift_guard_version is not null
    and strategic_intent in (
        'ACQUISITION','AUDIENCE_DEPTH','AUTHORITY',
        'SERIES_CONTINUITY','CONTROLLED_EXPERIMENT'
    )
    and intent_success_criteria is not null
    and jsonb_typeof(intent_success_criteria) = 'object'
    and intent_success_criteria <> '{}'::jsonb
    and intent_success_criteria_version is not null
    and intent_success_criteria_hash ~ '^[0-9a-f]{64}$'
    and primary_variable_under_test is not null
    and btrim(primary_variable_under_test) <> ''
    and decision_reversibility in ('TWO_WAY_DOOR','ONE_WAY_DOOR')
    and active_launch_policy_version_id is not null
    and active_launch_policy_hash ~ '^[0-9a-f]{64}$'
    and active_launch_run_id is not null
    and active_launch_run_hash ~ '^[0-9a-f]{64}$'
    and (
        strategic_intent <> 'CONTROLLED_EXPERIMENT'
        or (
            experiment_hypothesis is not null
            and btrim(experiment_hypothesis) <> ''
        )
    )
)
"""


def _add_columns(table_name: str, *, include_primary_variable: bool) -> None:
    for column in _lineage_columns():
        op.add_column(table_name, column)
    if include_primary_variable:
        op.add_column(
            table_name,
            sa.Column(
                "primary_variable_under_test", sa.String(length=160), nullable=True
            ),
        )


def _create_launch_authority_foreign_keys(table_name: str) -> None:
    op.create_foreign_key(
        f"fk_{table_name}_active_launch_policy",
        table_name,
        "first_channel_launch_policy_versions",
        ["active_launch_policy_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        f"fk_{table_name}_active_launch_run",
        table_name,
        "launch_runs",
        ["active_launch_run_id"],
        ["id"],
    )


def _drop_launch_authority_foreign_keys(table_name: str) -> None:
    op.drop_constraint(
        f"fk_{table_name}_active_launch_run", table_name, type_="foreignkey"
    )
    op.drop_constraint(
        f"fk_{table_name}_active_launch_policy", table_name, type_="foreignkey"
    )


def upgrade() -> None:
    # Candidate lineage is nullable because pre-cutover research artifacts are
    # immutable. New v2 admissions are guarded below and must carry a complete
    # copy of this authority into their project.
    _add_columns("editorial_idea_candidates", include_primary_variable=False)
    _create_launch_authority_foreign_keys("editorial_idea_candidates")
    op.create_index(
        "ix_editorial_idea_candidates_active_launch_policy",
        "editorial_idea_candidates",
        ["active_launch_policy_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_editorial_idea_candidates_active_launch_run",
        "editorial_idea_candidates",
        ["active_launch_run_id"],
        unique=False,
    )

    _add_columns("project_admission_decisions", include_primary_variable=True)
    _create_launch_authority_foreign_keys("project_admission_decisions")
    op.create_index(
        "ix_project_admission_decisions_active_launch_policy",
        "project_admission_decisions",
        ["active_launch_policy_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_admission_decisions_active_launch_run",
        "project_admission_decisions",
        ["active_launch_run_id"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_project_admission_decisions_v2_strategic_lineage",
        "project_admission_decisions",
        _V2_LINEAGE_CHECK,
    )

    _add_columns("video_projects", include_primary_variable=True)
    _create_launch_authority_foreign_keys("video_projects")
    op.create_index(
        "ix_video_projects_active_launch_policy",
        "video_projects",
        ["active_launch_policy_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_video_projects_active_launch_run",
        "video_projects",
        ["active_launch_run_id"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_video_projects_v2_strategic_lineage",
        "video_projects",
        _V2_LINEAGE_CHECK,
    )


def _assert_no_new_lineage_rows() -> None:
    if context.is_offline_mode():
        op.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM editorial_idea_candidates
                        WHERE active_launch_policy_version_id IS NOT NULL
                           OR active_launch_run_id IS NOT NULL
                    ) OR EXISTS (
                        SELECT 1
                        FROM project_admission_decisions
                        WHERE active_launch_policy_version_id IS NOT NULL
                           OR active_launch_run_id IS NOT NULL
                    ) OR EXISTS (
                        SELECT 1
                        FROM video_projects
                        WHERE active_launch_policy_version_id IS NOT NULL
                           OR active_launch_run_id IS NOT NULL
                    ) THEN
                        RAISE EXCEPTION
                            'DOWNGRADE_BLOCKED_STRATEGIC_LINEAGE_EXISTS';
                    END IF;
                END;
                $$;
                """
            )
        )
        return
    bind = op.get_bind()
    for table_name in (
        "editorial_idea_candidates",
        "project_admission_decisions",
        "video_projects",
    ):
        count = bind.execute(
            sa.text(
                f"select count(*) from {table_name} "
                "where active_launch_policy_version_id is not null "
                "or active_launch_run_id is not null"
            )
        ).scalar_one()
        if count:
            raise RuntimeError("DOWNGRADE_BLOCKED_STRATEGIC_LINEAGE_EXISTS")


def _drop_lineage_columns(table_name: str, *, includes_primary_variable: bool) -> None:
    columns = [column.name for column in _lineage_columns()]
    if includes_primary_variable:
        columns.append("primary_variable_under_test")
    for column_name in reversed(columns):
        op.drop_column(table_name, column_name)


def downgrade() -> None:
    _assert_no_new_lineage_rows()

    op.drop_constraint(
        "ck_video_projects_v2_strategic_lineage", "video_projects", type_="check"
    )
    op.drop_index("ix_video_projects_active_launch_run", table_name="video_projects")
    op.drop_index("ix_video_projects_active_launch_policy", table_name="video_projects")
    _drop_launch_authority_foreign_keys("video_projects")
    _drop_lineage_columns("video_projects", includes_primary_variable=True)

    op.drop_constraint(
        "ck_project_admission_decisions_v2_strategic_lineage",
        "project_admission_decisions",
        type_="check",
    )
    op.drop_index(
        "ix_project_admission_decisions_active_launch_run",
        table_name="project_admission_decisions",
    )
    op.drop_index(
        "ix_project_admission_decisions_active_launch_policy",
        table_name="project_admission_decisions",
    )
    _drop_launch_authority_foreign_keys("project_admission_decisions")
    _drop_lineage_columns("project_admission_decisions", includes_primary_variable=True)

    op.drop_index(
        "ix_editorial_idea_candidates_active_launch_run",
        table_name="editorial_idea_candidates",
    )
    op.drop_index(
        "ix_editorial_idea_candidates_active_launch_policy",
        table_name="editorial_idea_candidates",
    )
    _drop_launch_authority_foreign_keys("editorial_idea_candidates")
    _drop_lineage_columns("editorial_idea_candidates", includes_primary_variable=False)
