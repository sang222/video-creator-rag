"""Freeze current script runtime and assignment authorities.

Revision ID: 0059_script_qualification_current_authorities
Revises: 0058_editorial_script_qual
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0059_script_qual_auth"
down_revision = "0058_editorial_script_qual"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("script_qualification_runs", sa.Column("runtime_contract", postgresql.JSONB(), nullable=True))
    op.add_column("script_qualification_runs", sa.Column("runtime_contract_hash", sa.String(length=64), nullable=True))
    op.add_column("script_qualification_runs", sa.Column("assignment_resolution", postgresql.JSONB(), nullable=True))
    op.add_column("script_qualification_runs", sa.Column("assignment_resolution_hash", sa.String(length=64), nullable=True))
    op.add_column("script_qualification_runs", sa.Column("episode_reservation_active", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_check_constraint("ck_script_qualification_runtime_contract_hash", "script_qualification_runs", "runtime_contract_hash is null or runtime_contract_hash ~ '^[0-9a-f]{64}$'")
    op.create_check_constraint("ck_script_qualification_assignment_resolution_hash", "script_qualification_runs", "assignment_resolution_hash is null or assignment_resolution_hash ~ '^[0-9a-f]{64}$'")


def downgrade() -> None:
    op.drop_constraint("ck_script_qualification_assignment_resolution_hash", "script_qualification_runs", type_="check")
    op.drop_constraint("ck_script_qualification_runtime_contract_hash", "script_qualification_runs", type_="check")
    op.drop_column("script_qualification_runs", "episode_reservation_active")
    op.drop_column("script_qualification_runs", "assignment_resolution_hash")
    op.drop_column("script_qualification_runs", "assignment_resolution")
    op.drop_column("script_qualification_runs", "runtime_contract_hash")
    op.drop_column("script_qualification_runs", "runtime_contract")
