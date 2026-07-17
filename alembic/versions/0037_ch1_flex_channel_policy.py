"""Freeze channel-scoped policy references on future video projects.

Revision ID: 0037_ch1_flex
Revises: 0036_hpr1_veo
Create Date: 2026-07-17 00:00:00

All columns are nullable so historical projects remain byte-for-byte policy-bound
to their existing compiled snapshot without a data backfill.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0037_ch1_flex"
down_revision: str | None = "0036_hpr1_veo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("video_projects", sa.Column("channel_profile_version_id", postgresql.UUID(as_uuid=True)))
    op.add_column("video_projects", sa.Column("native_render_policy_snapshot_ref", sa.Text()))
    op.add_column("video_projects", sa.Column("native_render_policy_snapshot_hash", sa.String(length=128)))
    op.add_column("video_projects", sa.Column("creative_quality_policy_ref", sa.Text()))
    op.add_column("video_projects", sa.Column("creative_quality_policy_hash", sa.String(length=128)))
    op.add_column("video_projects", sa.Column("provider_usage_policy_ref", sa.Text()))
    op.add_column("video_projects", sa.Column("provider_usage_policy_hash", sa.String(length=128)))
    op.add_column("video_projects", sa.Column("budget_policy_ref", sa.Text()))
    op.add_column("video_projects", sa.Column("budget_policy_hash", sa.String(length=128)))
    op.add_column("video_projects", sa.Column("format_identity_contract_ref", sa.Text()))
    op.add_column("video_projects", sa.Column("format_identity_contract_hash", sa.String(length=128)))
    op.create_foreign_key(
        "fk_video_projects_channel_profile_version_id",
        "video_projects",
        "channel_profile_versions",
        ["channel_profile_version_id"],
        ["id"],
    )
    op.create_index(
        "ix_video_projects_channel_profile_version_id",
        "video_projects",
        ["channel_profile_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_projects_channel_profile_version_id", table_name="video_projects")
    op.drop_constraint("fk_video_projects_channel_profile_version_id", "video_projects", type_="foreignkey")
    for column in (
        "format_identity_contract_hash",
        "format_identity_contract_ref",
        "budget_policy_hash",
        "budget_policy_ref",
        "provider_usage_policy_hash",
        "provider_usage_policy_ref",
        "creative_quality_policy_hash",
        "creative_quality_policy_ref",
        "native_render_policy_snapshot_hash",
        "native_render_policy_snapshot_ref",
        "channel_profile_version_id",
    ):
        op.drop_column("video_projects", column)
