"""add_company_slug_description

Revision ID: b35988076109
Revises: 0022_m12_2s_full_rehearsal
Create Date: 2026-06-28 13:59:41.752511
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'b35988076109'
down_revision: str | None = '0022_m12_2s_full_rehearsal'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    # Add columns as nullable first (safe for existing rows)
    op.add_column('companies', sa.Column('slug', sa.String(length=128), nullable=True))
    op.add_column('companies', sa.Column('description', sa.Text(), nullable=True))

    # Backfill: set slug from name for any existing rows, description to empty string
    op.execute("UPDATE companies SET slug = LOWER(REPLACE(name, ' ', '-')), description = '' WHERE slug IS NULL")

    # Now make columns NOT NULL and add unique constraint on slug
    op.alter_column('companies', 'slug', nullable=False)
    op.alter_column('companies', 'description', nullable=False, server_default='')
    op.create_unique_constraint(op.f('uq_companies_slug'), 'companies', ['slug'])

def downgrade() -> None:
    op.drop_constraint(op.f('uq_companies_slug'), 'companies', type_='unique')
    op.drop_column('companies', 'description')
    op.drop_column('companies', 'slug')
