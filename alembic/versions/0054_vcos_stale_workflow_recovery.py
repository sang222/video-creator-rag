"""Add deterministic zero-effect stale-workflow recovery authority.

Revision ID: 0054_vcos_stale_recovery
Revises: 0053_ops_incident_constraint
Create Date: 2026-08-02 15:35:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0054_vcos_stale_recovery"
down_revision: str | None = "0053_ops_incident_constraint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_WORKFLOW_STATES = (
    "'PLANNING_PENDING','PLANNING_RUNNING','ASSIGNMENT_READY',"
    "'RESEARCH_PENDING','RESEARCH_RUNNING','PACKAGE_PENDING','PACKAGE_RUNNING',"
    "'READY_FOR_PRODUCTION','MEDIA_PENDING','MEDIA_RUNNING','RENDER_PENDING',"
    "'RENDER_RUNNING','QC_PENDING','QC_RUNNING','ARCHIVE_PENDING',"
    "'ARCHIVE_RUNNING','FINAL_REVIEW_READY','BLOCKED','RETRY_SCHEDULED',"
    "'CANCELED','FAILED_TERMINAL','DEAD_LETTERED','SUPERSEDED'"
)
_WORKFLOW_STATE_CONSTRAINT = (
    "ck_production_workflow_runs_production_workflow_runs_state"
)
_CADENCE_DECISION_CONSTRAINT = (
    "ck_cadence_evaluation_receipts_ck_cadence_receipts_decision"
)
_CADENCE_DECISIONS = (
    "'START_LONG_FORM_PRODUCTION','WAIT_BUFFER_FULL','WAIT_NO_ELIGIBLE_CANDIDATE',"
    "'WAIT_ACTIVE_PRODUCTION','WAIT_OUTSIDE_PRODUCTION_HORIZON','WAIT_BUDGET_BLOCKED',"
    "'WAIT_PROVIDER_AUTHORITY','WAIT_POLICY_OR_RIGHTS_BLOCKED','WAIT_QUALITY_BLOCKED',"
    "'WAIT_LAUNCH_NOT_ACTIVE'"
)


def _replace_workflow_state_constraint(*, include_superseded: bool) -> None:
    states = _WORKFLOW_STATES
    if not include_superseded:
        states = states.removesuffix(",'SUPERSEDED'")
    op.execute(
        "ALTER TABLE production_workflow_runs DROP CONSTRAINT IF EXISTS "
        f"{_WORKFLOW_STATE_CONSTRAINT}"
    )
    op.execute(
        "ALTER TABLE production_workflow_runs ADD CONSTRAINT "
        f"{_WORKFLOW_STATE_CONSTRAINT} CHECK (state in ({states}))"
    )


def upgrade() -> None:
    _replace_workflow_state_constraint(include_superseded=True)
    op.execute(
        "ALTER TABLE cadence_evaluation_receipts DROP CONSTRAINT IF EXISTS "
        f"{_CADENCE_DECISION_CONSTRAINT}"
    )
    op.execute(
        "ALTER TABLE cadence_evaluation_receipts ADD CONSTRAINT "
        f"{_CADENCE_DECISION_CONSTRAINT} CHECK (decision in ({_CADENCE_DECISIONS}))"
    )
    op.create_table(
        "workflow_recovery_receipts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_workflow_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "dead_letter_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dead_letter_jobs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ops_incidents.id"),
            nullable=True,
        ),
        sa.Column(
            "recovery_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("domain_events.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("recovery_version", sa.String(length=80), nullable=False),
        sa.Column("classification", sa.String(length=80), nullable=False),
        sa.Column("decision", sa.String(length=120), nullable=False),
        sa.Column("failed_stage", sa.String(length=40), nullable=False),
        sa.Column("failure_reason_code", sa.String(length=160), nullable=False),
        sa.Column("proof", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "classification = 'STALE_PRE_REPAIR_ZERO_EFFECT_WORKFLOW'",
            name="ck_workflow_recovery_receipts_classification",
        ),
        sa.CheckConstraint(
            "decision = 'AUTO_SUPERSEDE_STALE_PRE_REPAIR_WORKFLOW'",
            name="ck_workflow_recovery_receipts_decision",
        ),
        sa.CheckConstraint(
            "failed_stage in ('PLANNING','PREFLIGHT','ADMISSION','RESEARCH','PACKAGE',"
            "'READINESS','MEDIA','RENDER','QC','ARCHIVE','FINALIZE')",
            name="ck_workflow_recovery_receipts_stage",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' and decision_hash ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_recovery_receipts_hashes",
        ),
    )
    op.create_index(
        "ix_workflow_recovery_receipts_created_at",
        "workflow_recovery_receipts",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_recovery_receipts_created_at",
        table_name="workflow_recovery_receipts",
    )
    op.drop_table("workflow_recovery_receipts")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM cadence_evaluation_receipts
                WHERE decision = 'WAIT_PROVIDER_AUTHORITY'
            ) THEN
                RAISE EXCEPTION '0054 downgrade refused: provider-authority cadence receipts exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "ALTER TABLE cadence_evaluation_receipts DROP CONSTRAINT IF EXISTS "
        f"{_CADENCE_DECISION_CONSTRAINT}"
    )
    op.execute(
        "ALTER TABLE cadence_evaluation_receipts ADD CONSTRAINT "
        f"{_CADENCE_DECISION_CONSTRAINT} CHECK (decision in ("
        "'START_LONG_FORM_PRODUCTION','WAIT_BUFFER_FULL','WAIT_NO_ELIGIBLE_CANDIDATE',"
        "'WAIT_ACTIVE_PRODUCTION','WAIT_OUTSIDE_PRODUCTION_HORIZON','WAIT_BUDGET_BLOCKED',"
        "'WAIT_POLICY_OR_RIGHTS_BLOCKED','WAIT_QUALITY_BLOCKED','WAIT_LAUNCH_NOT_ACTIVE'))"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM production_workflow_runs WHERE state = 'SUPERSEDED') THEN
                RAISE EXCEPTION '0054 downgrade refused: superseded workflow rows exist';
            END IF;
        END
        $$;
        """
    )
    _replace_workflow_state_constraint(include_superseded=False)
