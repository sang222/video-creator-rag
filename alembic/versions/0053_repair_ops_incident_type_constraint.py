"""Repair the durable orchestration incident-type check constraint.

Revision ID: 0053_ops_incident_constraint
Revises: 0052_vcos_strategic_lineage
Create Date: 2026-08-02 14:05:00

Some existing databases were stamped past the orchestration migration while
retaining the pre-orchestration ``ops_incidents`` constraint.  That makes a
bounded workflow retry unable to persist its terminal dead-letter incident.
This migration is schema-only: it does not alter any immutable workflow,
package, provider, or canary authority.
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0053_ops_incident_constraint"
down_revision: str | None = "0052_vcos_strategic_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BASE_INCIDENT_TYPES = (
    "'PROVIDER_OUTAGE','CREDENTIAL_MISSING','QUOTA_EXHAUSTED',"
    "'COST_LIMIT_REACHED','DEAD_LETTER_JOB','HEALTH_DEGRADED',"
    "'CONFIG_ERROR','UNKNOWN'"
)
_DURABLE_ORCHESTRATION_INCIDENT_TYPES = (
    "'WORKER_LEASE_EXPIRED','STAGE_RETRY_EXHAUSTED',"
    "'PROVIDER_OUTCOME_UNCERTAIN','BUDGET_SETTLEMENT_UNCERTAIN',"
    "'RENDER_FAILED','ARCHIVE_FAILED','INTEGRITY_MISMATCH',"
    "'CANCELED_WITH_IN_FLIGHT_EFFECT'"
)
_CONSTRAINT_NAME = "ck_ops_incidents_ck_ops_incidents_incident_type"


def _replace_incident_type_constraint(*, include_orchestration_types: bool) -> None:
    allowed = _BASE_INCIDENT_TYPES
    if include_orchestration_types:
        allowed = f"{allowed},{_DURABLE_ORCHESTRATION_INCIDENT_TYPES}"
    op.execute(
        f"ALTER TABLE ops_incidents DROP CONSTRAINT IF EXISTS {_CONSTRAINT_NAME}"
    )
    op.execute(
        "ALTER TABLE ops_incidents "
        f"ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"CHECK (incident_type in ({allowed}))"
    )


def upgrade() -> None:
    _replace_incident_type_constraint(include_orchestration_types=True)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM ops_incidents
                WHERE incident_type IN (
                    'WORKER_LEASE_EXPIRED','STAGE_RETRY_EXHAUSTED',
                    'PROVIDER_OUTCOME_UNCERTAIN','BUDGET_SETTLEMENT_UNCERTAIN',
                    'RENDER_FAILED','ARCHIVE_FAILED','INTEGRITY_MISMATCH',
                    'CANCELED_WITH_IN_FLIGHT_EFFECT'
                )
            ) THEN
                RAISE EXCEPTION
                    '0053 downgrade refused: durable orchestration incident rows exist';
            END IF;
        END
        $$;
        """
    )
    _replace_incident_type_constraint(include_orchestration_types=False)
