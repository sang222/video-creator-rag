"""M2 artifact workflow backbone

Revision ID: 0003_m2_workflow
Revises: 0002_m1_channel_profile_backbone
Create Date: 2026-06-24 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_m2_workflow"
down_revision: str | None = "0002_m1_channel_profile_backbone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("project_type", sa.String(length=80), nullable=True),
        sa.Column("priority", sa.String(length=40), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("financial_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("brand_safety_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("legal_compliance_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("audience_delivery_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_video_projects_company_id", "video_projects", ["company_id"])
    op.create_index("ix_video_projects_channel_workspace_id", "video_projects", ["channel_workspace_id"])
    op.create_index("ix_video_projects_policy_snapshot_id", "video_projects", ["policy_snapshot_id"])
    op.create_index("ix_video_projects_created_at", "video_projects", ["created_at"])

    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_type", sa.String(length=120), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_video_project_id", "artifacts", ["video_project_id"])
    op.create_index("ix_artifacts_current_version_id", "artifacts", ["current_version_id"])
    op.create_index("ix_artifacts_created_at", "artifacts", ["created_at"])

    op.create_table(
        "artifact_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_entity_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("packaging_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("media_qc_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("source_manifest", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("context_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("claim_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("retrieval_plan_ref", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["parent_version_id"], ["artifact_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id", "version_number"),
    )
    op.create_index("ix_artifact_versions_artifact_id", "artifact_versions", ["artifact_id"])
    op.create_index("ix_artifact_versions_parent_version_id", "artifact_versions", ["parent_version_id"])
    op.create_index("ix_artifact_versions_created_at", "artifact_versions", ["created_at"])
    op.create_foreign_key(
        "fk_artifacts_current_version_id",
        "artifacts",
        "artifact_versions",
        ["current_version_id"],
        ["id"],
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_artifact_version_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'artifact_versions are immutable after creation';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_prevent_artifact_version_update
        BEFORE UPDATE ON artifact_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_artifact_version_update();
        """
    )

    op.create_table(
        "review_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("review_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("assigned_to_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_reason_codes", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("evidence_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("review_scope", sa.Text(), nullable=True),
        sa.Column("context_pack_ref", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assigned_to_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_artifact_version_id"], ["artifact_versions.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_tasks_video_project_id", "review_tasks", ["video_project_id"])
    op.create_index("ix_review_tasks_target_artifact_version_id", "review_tasks", ["target_artifact_version_id"])
    op.create_index("ix_review_tasks_created_at", "review_tasks", ["created_at"])

    op.create_table(
        "review_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("reason_code", sa.String(length=160), nullable=False),
        sa.Column("finding_text", sa.Text(), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["review_task_id"], ["review_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_findings_review_task_id", "review_findings", ["review_task_id"])
    op.create_index("ix_review_findings_created_at", "review_findings", ["created_at"])

    op.create_table(
        "revision_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("resolved_by_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["resolved_by_artifact_version_id"], ["artifact_versions.id"]),
        sa.ForeignKeyConstraint(["review_task_id"], ["review_tasks.id"]),
        sa.ForeignKeyConstraint(["target_artifact_version_id"], ["artifact_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_revision_requests_review_task_id", "revision_requests", ["review_task_id"])
    op.create_index("ix_revision_requests_target_artifact_version_id", "revision_requests", ["target_artifact_version_id"])
    op.create_index("ix_revision_requests_created_at", "revision_requests", ["created_at"])

    op.create_table(
        "approval_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision", sa.String(length=40), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("decision_basis", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("evidence_basis", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("policy_basis", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("context_pack_ref", sa.Text(), nullable=True),
        sa.Column("human_decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_artifact_version_id"], ["artifact_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_decisions_target_artifact_version_id", "approval_decisions", ["target_artifact_version_id"])
    op.create_index("ix_approval_decisions_target", "approval_decisions", ["target_type", "target_id"])
    op.create_index("ix_approval_decisions_created_at", "approval_decisions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_approval_decisions_created_at", table_name="approval_decisions")
    op.drop_index("ix_approval_decisions_target", table_name="approval_decisions")
    op.drop_index("ix_approval_decisions_target_artifact_version_id", table_name="approval_decisions")
    op.drop_table("approval_decisions")
    op.drop_index("ix_revision_requests_created_at", table_name="revision_requests")
    op.drop_index("ix_revision_requests_target_artifact_version_id", table_name="revision_requests")
    op.drop_index("ix_revision_requests_review_task_id", table_name="revision_requests")
    op.drop_table("revision_requests")
    op.drop_index("ix_review_findings_created_at", table_name="review_findings")
    op.drop_index("ix_review_findings_review_task_id", table_name="review_findings")
    op.drop_table("review_findings")
    op.drop_index("ix_review_tasks_created_at", table_name="review_tasks")
    op.drop_index("ix_review_tasks_target_artifact_version_id", table_name="review_tasks")
    op.drop_index("ix_review_tasks_video_project_id", table_name="review_tasks")
    op.drop_table("review_tasks")
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_artifact_version_update ON artifact_versions")
    op.execute("DROP FUNCTION IF EXISTS prevent_artifact_version_update()")
    op.drop_constraint("fk_artifacts_current_version_id", "artifacts", type_="foreignkey")
    op.drop_index("ix_artifact_versions_created_at", table_name="artifact_versions")
    op.drop_index("ix_artifact_versions_parent_version_id", table_name="artifact_versions")
    op.drop_index("ix_artifact_versions_artifact_id", table_name="artifact_versions")
    op.drop_table("artifact_versions")
    op.drop_index("ix_artifacts_created_at", table_name="artifacts")
    op.drop_index("ix_artifacts_current_version_id", table_name="artifacts")
    op.drop_index("ix_artifacts_video_project_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_video_projects_created_at", table_name="video_projects")
    op.drop_index("ix_video_projects_policy_snapshot_id", table_name="video_projects")
    op.drop_index("ix_video_projects_channel_workspace_id", table_name="video_projects")
    op.drop_index("ix_video_projects_company_id", table_name="video_projects")
    op.drop_table("video_projects")
