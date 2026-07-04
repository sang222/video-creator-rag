"""R3D6 vector-safe retrieval foundation

Revision ID: 0029_r3d6_vector_safe_retrieval
Revises: 0028_r3d5_controlled_memory
Create Date: 2026-07-04 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0029_r3d6_vector_safe_retrieval"
down_revision: str | None = "0028_r3d5_controlled_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "embedding_facets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_facet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("facet_type", sa.String(length=80), nullable=False),
        sa.Column("facet_text_hash", sa.String(length=128), nullable=False),
        sa.Column("embedding_model", sa.String(length=160), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding_vector_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("approval_status_at_embed", sa.String(length=40), nullable=False),
        sa.Column("rights_status_at_embed", sa.String(length=40), nullable=False),
        sa.Column("prompt_safety_state_at_embed", sa.String(length=40), nullable=False),
        sa.Column("embedding_eligible_at_embed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("stale_state", sa.String(length=40), server_default="FRESH", nullable=False),
        sa.Column("stale_reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["character_profile_id"], ["character_profiles.id"]),
        sa.ForeignKeyConstraint(["character_version_id"], ["character_versions.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["content_category_id"], ["content_categories.id"]),
        sa.ForeignKeyConstraint(["memory_facet_id"], ["memory_facets.id"]),
        sa.ForeignKeyConstraint(["memory_item_id"], ["channel_memory_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_embedding_facets_memory_facet", "embedding_facets", ["memory_facet_id"])
    op.create_index("ix_embedding_facets_memory_item", "embedding_facets", ["memory_item_id"])
    op.create_index("ix_embedding_facets_company", "embedding_facets", ["company_id"])
    op.create_index("ix_embedding_facets_channel", "embedding_facets", ["channel_workspace_id"])
    op.create_index("ix_embedding_facets_category", "embedding_facets", ["content_category_id"])
    op.create_index("ix_embedding_facets_character_profile", "embedding_facets", ["character_profile_id"])
    op.create_index("ix_embedding_facets_type", "embedding_facets", ["facet_type"])
    op.create_index("ix_embedding_facets_text_hash", "embedding_facets", ["facet_text_hash"])
    op.create_index("ix_embedding_facets_stale_state", "embedding_facets", ["stale_state"])
    op.create_index("ix_embedding_facets_created_at", "embedding_facets", ["created_at"])

    op.create_table(
        "embedding_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_facet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_status", sa.String(length=40), server_default="PENDING", nullable=False),
        sa.Column("blocker_reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("embedding_model", sa.String(length=160), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["memory_facet_id"], ["memory_facets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_embedding_jobs_memory_facet", "embedding_jobs", ["memory_facet_id"])
    op.create_index("ix_embedding_jobs_status", "embedding_jobs", ["job_status"])
    op.create_index("ix_embedding_jobs_created_at", "embedding_jobs", ["created_at"])

    op.create_table(
        "vector_retrieval_manifests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_key", sa.String(length=160), nullable=False),
        sa.Column("use_case", sa.String(length=120), nullable=False),
        sa.Column("query_facet_type", sa.String(length=80), nullable=True),
        sa.Column("query_text_hash", sa.String(length=128), nullable=False),
        sa.Column("sql_filter_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("candidate_count_before_vector", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("candidate_count_after_policy", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("selected_memory_facet_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("blocked_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("rejected_refs_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("vector_model", sa.String(length=160), nullable=True),
        sa.Column("ranking_params_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("retrieval_hash", sa.String(length=128), nullable=False),
        sa.Column("digest_hash", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["character_profile_id"], ["character_profiles.id"]),
        sa.ForeignKeyConstraint(["character_version_id"], ["character_versions.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["content_category_id"], ["content_categories.id"]),
        sa.ForeignKeyConstraint(["effective_context_snapshot_id"], ["effective_channel_runtime_context_snapshots.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vector_retrieval_manifests_project", "vector_retrieval_manifests", ["video_project_id"])
    op.create_index("ix_vector_retrieval_manifests_package", "vector_retrieval_manifests", ["package_id"])
    op.create_index("ix_vector_retrieval_manifests_effective_context", "vector_retrieval_manifests", ["effective_context_snapshot_id"])
    op.create_index("ix_vector_retrieval_manifests_company", "vector_retrieval_manifests", ["company_id"])
    op.create_index("ix_vector_retrieval_manifests_channel", "vector_retrieval_manifests", ["channel_workspace_id"])
    op.create_index("ix_vector_retrieval_manifests_category", "vector_retrieval_manifests", ["content_category_id"])
    op.create_index("ix_vector_retrieval_manifests_agent", "vector_retrieval_manifests", ["agent_key"])
    op.create_index("ix_vector_retrieval_manifests_use_case", "vector_retrieval_manifests", ["use_case"])
    op.create_index("ix_vector_retrieval_manifests_hash", "vector_retrieval_manifests", ["retrieval_hash"])
    op.create_index("ix_vector_retrieval_manifests_created_at", "vector_retrieval_manifests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_vector_retrieval_manifests_created_at", table_name="vector_retrieval_manifests")
    op.drop_index("ix_vector_retrieval_manifests_hash", table_name="vector_retrieval_manifests")
    op.drop_index("ix_vector_retrieval_manifests_use_case", table_name="vector_retrieval_manifests")
    op.drop_index("ix_vector_retrieval_manifests_agent", table_name="vector_retrieval_manifests")
    op.drop_index("ix_vector_retrieval_manifests_category", table_name="vector_retrieval_manifests")
    op.drop_index("ix_vector_retrieval_manifests_channel", table_name="vector_retrieval_manifests")
    op.drop_index("ix_vector_retrieval_manifests_company", table_name="vector_retrieval_manifests")
    op.drop_index("ix_vector_retrieval_manifests_effective_context", table_name="vector_retrieval_manifests")
    op.drop_index("ix_vector_retrieval_manifests_package", table_name="vector_retrieval_manifests")
    op.drop_index("ix_vector_retrieval_manifests_project", table_name="vector_retrieval_manifests")
    op.drop_table("vector_retrieval_manifests")

    op.drop_index("ix_embedding_jobs_created_at", table_name="embedding_jobs")
    op.drop_index("ix_embedding_jobs_status", table_name="embedding_jobs")
    op.drop_index("ix_embedding_jobs_memory_facet", table_name="embedding_jobs")
    op.drop_table("embedding_jobs")

    op.drop_index("ix_embedding_facets_created_at", table_name="embedding_facets")
    op.drop_index("ix_embedding_facets_stale_state", table_name="embedding_facets")
    op.drop_index("ix_embedding_facets_text_hash", table_name="embedding_facets")
    op.drop_index("ix_embedding_facets_type", table_name="embedding_facets")
    op.drop_index("ix_embedding_facets_character_profile", table_name="embedding_facets")
    op.drop_index("ix_embedding_facets_category", table_name="embedding_facets")
    op.drop_index("ix_embedding_facets_channel", table_name="embedding_facets")
    op.drop_index("ix_embedding_facets_company", table_name="embedding_facets")
    op.drop_index("ix_embedding_facets_memory_item", table_name="embedding_facets")
    op.drop_index("ix_embedding_facets_memory_facet", table_name="embedding_facets")
    op.drop_table("embedding_facets")
