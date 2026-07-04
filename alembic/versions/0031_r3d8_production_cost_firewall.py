"""R3D8 production cost firewall and provider boundary

Revision ID: 0031_r3d8_cost_firewall
Revises: 0030_r3d7_closed_learning_loop
Create Date: 2026-07-04 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0031_r3d8_cost_firewall"
down_revision: str | None = "0030_r3d7_closed_learning_loop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "render_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("revision_status", sa.String(length=80), nullable=False),
        sa.Column("source_artifact_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("gate_batch_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("render_plan_hash", sa.String(length=128), nullable=False),
        sa.Column("provider_plan_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["effective_context_snapshot_id"], ["effective_channel_runtime_context_snapshots.id"]),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_id", "revision_no", name="uq_render_revisions_package_revision_no"),
    )
    op.create_index("ix_render_revisions_project", "render_revisions", ["video_project_id"])
    op.create_index("ix_render_revisions_package", "render_revisions", ["package_id"])
    op.create_index("ix_render_revisions_effective_context", "render_revisions", ["effective_context_snapshot_id"])
    op.create_index("ix_render_revisions_status", "render_revisions", ["revision_status"])
    op.create_index("ix_render_revisions_hash", "render_revisions", ["render_plan_hash"])
    op.create_index("ix_render_revisions_created_at", "render_revisions", ["created_at"])

    op.create_table(
        "cost_estimate_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("estimate_status", sa.String(length=80), nullable=False),
        sa.Column("currency", sa.String(length=12), server_default="USD", nullable=False),
        sa.Column("estimated_total_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("estimated_voice_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("estimated_ai_hero_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("estimated_final_render_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("estimated_pexels_cost", sa.Numeric(18, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("provider_estimates_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("blocker_reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.ForeignKeyConstraint(["render_revision_id"], ["render_revisions.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_estimate_snapshots_revision", "cost_estimate_snapshots", ["render_revision_id"])
    op.create_index("ix_cost_estimate_snapshots_project", "cost_estimate_snapshots", ["video_project_id"])
    op.create_index("ix_cost_estimate_snapshots_package", "cost_estimate_snapshots", ["package_id"])
    op.create_index("ix_cost_estimate_snapshots_status", "cost_estimate_snapshots", ["estimate_status"])
    op.create_index("ix_cost_estimate_snapshots_hash", "cost_estimate_snapshots", ["content_hash"])
    op.create_index("ix_cost_estimate_snapshots_created_at", "cost_estimate_snapshots", ["created_at"])

    op.create_table(
        "human_paid_render_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_status", sa.String(length=40), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_approved_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("approved_provider_stages_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["render_revision_id"], ["render_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_human_paid_render_approvals_revision", "human_paid_render_approvals", ["render_revision_id"])
    op.create_index("ix_human_paid_render_approvals_status", "human_paid_render_approvals", ["approval_status"])
    op.create_index("ix_human_paid_render_approvals_created_at", "human_paid_render_approvals", ["created_at"])

    op.create_table(
        "provider_idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("provider_stage", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["render_revision_id"], ["render_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "render_revision_id",
            "provider_key",
            "provider_stage",
            "request_fingerprint",
            name="uq_provider_idempotency_revision_provider_stage_fingerprint",
        ),
    )
    op.create_index("ix_provider_idempotency_keys_revision", "provider_idempotency_keys", ["render_revision_id"])
    op.create_index("ix_provider_idempotency_keys_provider", "provider_idempotency_keys", ["provider_key"])
    op.create_index("ix_provider_idempotency_keys_stage", "provider_idempotency_keys", ["provider_stage"])
    op.create_index("ix_provider_idempotency_keys_key", "provider_idempotency_keys", ["idempotency_key"])

    op.create_table(
        "provider_job_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("provider_stage", sa.String(length=120), nullable=False),
        sa.Column("job_status", sa.String(length=60), nullable=False),
        sa.Column("external_job_id", sa.Text(), nullable=True),
        sa.Column("provider_request_hash", sa.String(length=128), nullable=True),
        sa.Column("provider_response_hash", sa.String(length=128), nullable=True),
        sa.Column("last_error_code", sa.String(length=160), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("poll_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["render_revision_id"], ["render_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_job_snapshots_revision", "provider_job_snapshots", ["render_revision_id"])
    op.create_index("ix_provider_job_snapshots_provider", "provider_job_snapshots", ["provider_key"])
    op.create_index("ix_provider_job_snapshots_stage", "provider_job_snapshots", ["provider_stage"])
    op.create_index("ix_provider_job_snapshots_status", "provider_job_snapshots", ["job_status"])
    op.create_index("ix_provider_job_snapshots_created_at", "provider_job_snapshots", ["created_at"])

    op.create_table(
        "paid_provider_call_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("provider_stage", sa.String(length=120), nullable=False),
        sa.Column("call_type", sa.String(length=40), nullable=False),
        sa.Column("call_status", sa.String(length=40), nullable=False),
        sa.Column("human_approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cost_estimate_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("response_ref", sa.Text(), nullable=True),
        sa.Column("reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["cost_estimate_snapshot_id"], ["cost_estimate_snapshots.id"]),
        sa.ForeignKeyConstraint(["human_approval_id"], ["human_paid_render_approvals.id"]),
        sa.ForeignKeyConstraint(["idempotency_key_id"], ["provider_idempotency_keys.id"]),
        sa.ForeignKeyConstraint(["render_revision_id"], ["render_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paid_provider_call_ledger_revision", "paid_provider_call_ledger", ["render_revision_id"])
    op.create_index("ix_paid_provider_call_ledger_provider", "paid_provider_call_ledger", ["provider_key"])
    op.create_index("ix_paid_provider_call_ledger_stage", "paid_provider_call_ledger", ["provider_stage"])
    op.create_index("ix_paid_provider_call_ledger_type", "paid_provider_call_ledger", ["call_type"])
    op.create_index("ix_paid_provider_call_ledger_status", "paid_provider_call_ledger", ["call_status"])
    op.create_index("ix_paid_provider_call_ledger_created_at", "paid_provider_call_ledger", ["created_at"])

    op.create_table(
        "paid_attempt_limit_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("provider_stage", sa.String(length=120), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["render_revision_id"], ["render_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("render_revision_id", "provider_key", "provider_stage", name="uq_paid_attempt_limit_revision_provider_stage"),
    )
    op.create_index("ix_paid_attempt_limit_records_revision", "paid_attempt_limit_records", ["render_revision_id"])
    op.create_index("ix_paid_attempt_limit_records_provider", "paid_attempt_limit_records", ["provider_key"])
    op.create_index("ix_paid_attempt_limit_records_stage", "paid_attempt_limit_records", ["provider_stage"])
    op.create_index("ix_paid_attempt_limit_records_status", "paid_attempt_limit_records", ["status"])

    op.create_table(
        "proxy_preview_artifact_flags",
        sa.Column("artifact_ref", sa.Text(), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preview_only", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("not_final_media", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("not_publishable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["package_id"], ["first_scripted_video_packages.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("artifact_ref"),
    )
    op.create_index("ix_proxy_preview_artifact_flags_project", "proxy_preview_artifact_flags", ["video_project_id"])
    op.create_index("ix_proxy_preview_artifact_flags_package", "proxy_preview_artifact_flags", ["package_id"])
    op.create_index("ix_proxy_preview_artifact_flags_source_type", "proxy_preview_artifact_flags", ["source_type"])
    op.create_index("ix_proxy_preview_artifact_flags_created_at", "proxy_preview_artifact_flags", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_proxy_preview_artifact_flags_created_at", table_name="proxy_preview_artifact_flags")
    op.drop_index("ix_proxy_preview_artifact_flags_source_type", table_name="proxy_preview_artifact_flags")
    op.drop_index("ix_proxy_preview_artifact_flags_package", table_name="proxy_preview_artifact_flags")
    op.drop_index("ix_proxy_preview_artifact_flags_project", table_name="proxy_preview_artifact_flags")
    op.drop_table("proxy_preview_artifact_flags")

    op.drop_index("ix_paid_attempt_limit_records_status", table_name="paid_attempt_limit_records")
    op.drop_index("ix_paid_attempt_limit_records_stage", table_name="paid_attempt_limit_records")
    op.drop_index("ix_paid_attempt_limit_records_provider", table_name="paid_attempt_limit_records")
    op.drop_index("ix_paid_attempt_limit_records_revision", table_name="paid_attempt_limit_records")
    op.drop_table("paid_attempt_limit_records")

    op.drop_index("ix_paid_provider_call_ledger_created_at", table_name="paid_provider_call_ledger")
    op.drop_index("ix_paid_provider_call_ledger_status", table_name="paid_provider_call_ledger")
    op.drop_index("ix_paid_provider_call_ledger_type", table_name="paid_provider_call_ledger")
    op.drop_index("ix_paid_provider_call_ledger_stage", table_name="paid_provider_call_ledger")
    op.drop_index("ix_paid_provider_call_ledger_provider", table_name="paid_provider_call_ledger")
    op.drop_index("ix_paid_provider_call_ledger_revision", table_name="paid_provider_call_ledger")
    op.drop_table("paid_provider_call_ledger")

    op.drop_index("ix_provider_job_snapshots_created_at", table_name="provider_job_snapshots")
    op.drop_index("ix_provider_job_snapshots_status", table_name="provider_job_snapshots")
    op.drop_index("ix_provider_job_snapshots_stage", table_name="provider_job_snapshots")
    op.drop_index("ix_provider_job_snapshots_provider", table_name="provider_job_snapshots")
    op.drop_index("ix_provider_job_snapshots_revision", table_name="provider_job_snapshots")
    op.drop_table("provider_job_snapshots")

    op.drop_index("ix_provider_idempotency_keys_key", table_name="provider_idempotency_keys")
    op.drop_index("ix_provider_idempotency_keys_stage", table_name="provider_idempotency_keys")
    op.drop_index("ix_provider_idempotency_keys_provider", table_name="provider_idempotency_keys")
    op.drop_index("ix_provider_idempotency_keys_revision", table_name="provider_idempotency_keys")
    op.drop_table("provider_idempotency_keys")

    op.drop_index("ix_human_paid_render_approvals_created_at", table_name="human_paid_render_approvals")
    op.drop_index("ix_human_paid_render_approvals_status", table_name="human_paid_render_approvals")
    op.drop_index("ix_human_paid_render_approvals_revision", table_name="human_paid_render_approvals")
    op.drop_table("human_paid_render_approvals")

    op.drop_index("ix_cost_estimate_snapshots_created_at", table_name="cost_estimate_snapshots")
    op.drop_index("ix_cost_estimate_snapshots_hash", table_name="cost_estimate_snapshots")
    op.drop_index("ix_cost_estimate_snapshots_status", table_name="cost_estimate_snapshots")
    op.drop_index("ix_cost_estimate_snapshots_package", table_name="cost_estimate_snapshots")
    op.drop_index("ix_cost_estimate_snapshots_project", table_name="cost_estimate_snapshots")
    op.drop_index("ix_cost_estimate_snapshots_revision", table_name="cost_estimate_snapshots")
    op.drop_table("cost_estimate_snapshots")

    op.drop_index("ix_render_revisions_created_at", table_name="render_revisions")
    op.drop_index("ix_render_revisions_hash", table_name="render_revisions")
    op.drop_index("ix_render_revisions_status", table_name="render_revisions")
    op.drop_index("ix_render_revisions_effective_context", table_name="render_revisions")
    op.drop_index("ix_render_revisions_package", table_name="render_revisions")
    op.drop_index("ix_render_revisions_project", table_name="render_revisions")
    op.drop_table("render_revisions")
