"""OFV0 originality and format validation foundation

Revision ID: 0034_ofv0_originality
Revises: 0033_p1_pre_lts_disposition
Create Date: 2026-07-11 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0034_ofv0_originality"
down_revision: str | None = "0033_p1_pre_lts_disposition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_default(value: str):
    return sa.text(value)


def upgrade() -> None:
    op.create_table(
        "format_identity_contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_profile_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True)),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("character_policy_mode", sa.String(length=40), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), server_default=_json_default("'{}'::jsonb"), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.Text()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"]),
        sa.ForeignKeyConstraint(["effective_context_snapshot_id"], ["effective_channel_runtime_context_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", "contract_version"),
    )
    op.create_index("ix_format_identity_contracts_channel", "format_identity_contracts", ["channel_id"])
    op.create_index("ix_format_identity_contracts_status", "format_identity_contracts", ["status"])
    op.create_index("ix_format_identity_contracts_context", "format_identity_contracts", ["effective_context_snapshot_id"])
    op.create_index("ix_format_identity_contracts_created_at", "format_identity_contracts", ["created_at"])
    op.create_table(
        "episode_originality_manifests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True)),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("format_identity_contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("format_identity_contract_hash", sa.String(length=128), nullable=False),
        sa.Column("approval_status", sa.String(length=40), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), server_default=_json_default("'{}'::jsonb"), nullable=False),
        sa.Column("manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["format_identity_contract_id"], ["format_identity_contracts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_id"),
    )
    op.create_index("ix_episode_originality_manifests_channel", "episode_originality_manifests", ["channel_id"])
    op.create_index("ix_episode_originality_manifests_contract", "episode_originality_manifests", ["format_identity_contract_id"])
    op.create_index("ix_episode_originality_manifests_approval", "episode_originality_manifests", ["approval_status"])
    op.create_index("ix_episode_originality_manifests_created_at", "episode_originality_manifests", ["created_at"])
    op.create_table(
        "claim_evidence_ledgers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", sa.String(length=160), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), server_default=_json_default("'{}'::jsonb"), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_id", "claim_id"),
    )
    op.create_index("ix_claim_evidence_ledgers_package", "claim_evidence_ledgers", ["package_id"])
    op.create_index("ix_claim_evidence_ledgers_created_at", "claim_evidence_ledgers", ["created_at"])
    op.create_table(
        "synthetic_media_disclosure_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_status", sa.String(length=60), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), server_default=_json_default("'{}'::jsonb"), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_id"),
    )
    op.create_index("ix_synthetic_media_disclosure_receipts_package", "synthetic_media_disclosure_receipts", ["package_id"])
    op.create_index("ix_synthetic_media_disclosure_receipts_status", "synthetic_media_disclosure_receipts", ["receipt_status"])
    op.create_table(
        "platform_native_package_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_surface", sa.String(length=40), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), server_default=_json_default("'{}'::jsonb"), nullable=False),
        sa.Column("derivative_manifest_ref", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["source_package_id"], ["first_scripted_video_packages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_package_id", "target_surface"),
    )
    op.create_index("ix_platform_native_package_plans_package", "platform_native_package_plans", ["source_package_id"])
    op.create_index("ix_platform_native_package_plans_surface", "platform_native_package_plans", ["target_surface"])
    op.create_table(
        "originality_gate_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gate_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), server_default=_json_default("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_originality_gate_runs_package", "originality_gate_runs", ["package_id"])
    op.create_index("ix_originality_gate_runs_gate", "originality_gate_runs", ["gate_key"])
    op.create_index("ix_originality_gate_runs_status", "originality_gate_runs", ["status"])
    op.create_index("ix_originality_gate_runs_created_at", "originality_gate_runs", ["created_at"])


def downgrade() -> None:
    for name in ("ix_originality_gate_runs_created_at", "ix_originality_gate_runs_status", "ix_originality_gate_runs_gate", "ix_originality_gate_runs_package"):
        op.drop_index(name, table_name="originality_gate_runs")
    op.drop_table("originality_gate_runs")
    for name in ("ix_platform_native_package_plans_surface", "ix_platform_native_package_plans_package"):
        op.drop_index(name, table_name="platform_native_package_plans")
    op.drop_table("platform_native_package_plans")
    for name in ("ix_synthetic_media_disclosure_receipts_status", "ix_synthetic_media_disclosure_receipts_package"):
        op.drop_index(name, table_name="synthetic_media_disclosure_receipts")
    op.drop_table("synthetic_media_disclosure_receipts")
    for name in ("ix_claim_evidence_ledgers_created_at", "ix_claim_evidence_ledgers_package"):
        op.drop_index(name, table_name="claim_evidence_ledgers")
    op.drop_table("claim_evidence_ledgers")
    for name in ("ix_episode_originality_manifests_created_at", "ix_episode_originality_manifests_approval", "ix_episode_originality_manifests_contract", "ix_episode_originality_manifests_channel"):
        op.drop_index(name, table_name="episode_originality_manifests")
    op.drop_table("episode_originality_manifests")
    for name in ("ix_format_identity_contracts_created_at", "ix_format_identity_contracts_context", "ix_format_identity_contracts_status", "ix_format_identity_contracts_channel"):
        op.drop_index(name, table_name="format_identity_contracts")
    op.drop_table("format_identity_contracts")
