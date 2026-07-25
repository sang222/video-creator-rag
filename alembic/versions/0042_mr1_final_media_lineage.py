"""Add checksum and immutable lineage bindings to final media refs.

Revision ID: 0042_mr1_final_lineage
Revises: 0041_geo_delivery
Create Date: 2026-07-21 00:00:00

The columns remain nullable for historical rows. New MR1 native final-media
rows are required by the application service to populate both fields together
with a verified CloudMediaRef.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0042_mr1_final_lineage"
down_revision: str | None = "0041_geo_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "final_media_refs",
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "final_media_refs",
        sa.Column(
            "lineage_artifact_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_final_media_refs_checksum_sha256",
        "final_media_refs",
        ("checksum_sha256 is null or checksum_sha256 ~ '^[0-9a-f]{64}$'"),
    )
    op.create_foreign_key(
        "fk_final_media_refs_lineage_artifact_version_id",
        "final_media_refs",
        "artifact_versions",
        ["lineage_artifact_version_id"],
        ["id"],
    )
    op.create_index(
        "ix_final_media_refs_lineage_artifact_version",
        "final_media_refs",
        ["lineage_artifact_version_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_final_media_refs_lineage_artifact_version",
        table_name="final_media_refs",
    )
    op.drop_constraint(
        "fk_final_media_refs_lineage_artifact_version_id",
        "final_media_refs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_final_media_refs_checksum_sha256",
        "final_media_refs",
        type_="check",
    )
    op.drop_column("final_media_refs", "lineage_artifact_version_id")
    op.drop_column("final_media_refs", "checksum_sha256")
