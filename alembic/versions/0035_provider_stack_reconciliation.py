"""Record the final provider-stack reconciliation.

Revision ID: 0035_cr_remove
Revises: 0034_ofv0_originality
Create Date: 2026-07-11 00:00:00

The provider-specific schema was removed from the historical schema definition so
fresh databases never create it. Runtime registries and application metadata are
reconciled in application source rather than seeded by Alembic.
"""
from collections.abc import Sequence


revision: str = "0035_cr_remove"
down_revision: str | None = "0034_ofv0_originality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("0035 is an irreversible provider-stack reconciliation")
