"""R3D5 controlled memory foundation

Revision ID: 0028_r3d5_controlled_memory
Revises: 0027_r3d4_agent_output_gates
Create Date: 2026-07-04 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0028_r3d5_controlled_memory"
down_revision: str | None = "0027_r3d4_agent_output_gates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "channel_memory_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("memory_type", sa.String(length=80), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("source_content_hash", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("approval_status", sa.String(length=40), server_default="DRAFT", nullable=False),
        sa.Column("rights_status", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("prompt_safety_state", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("reuse_scope", sa.String(length=40), server_default="CHANNEL", nullable=False),
        sa.Column("freshness_state", sa.String(length=40), server_default="FRESH", nullable=False),
        sa.Column("created_from_learning_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_from_failure_trace_report_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_from_recovery_proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_from_approved_playbook_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("human_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["character_binding_id"], ["character_bindings.id"]),
        sa.ForeignKeyConstraint(["character_profile_id"], ["character_profiles.id"]),
        sa.ForeignKeyConstraint(["character_version_id"], ["character_versions.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["content_category_id"], ["content_categories.id"]),
        sa.ForeignKeyConstraint(["created_from_approved_playbook_entry_id"], ["approved_playbook_entries.id"]),
        sa.ForeignKeyConstraint(["created_from_failure_trace_report_id"], ["failure_trace_reports.id"]),
        sa.ForeignKeyConstraint(["created_from_learning_candidate_id"], ["learning_candidates.id"]),
        sa.ForeignKeyConstraint(["created_from_recovery_proposal_id"], ["recovery_proposals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channel_memory_items_company", "channel_memory_items", ["company_id"])
    op.create_index("ix_channel_memory_items_channel", "channel_memory_items", ["channel_workspace_id"])
    op.create_index("ix_channel_memory_items_category", "channel_memory_items", ["content_category_id"])
    op.create_index("ix_channel_memory_items_character_profile", "channel_memory_items", ["character_profile_id"])
    op.create_index("ix_channel_memory_items_approval", "channel_memory_items", ["approval_status"])
    op.create_index("ix_channel_memory_items_rights", "channel_memory_items", ["rights_status"])
    op.create_index("ix_channel_memory_items_prompt_safety", "channel_memory_items", ["prompt_safety_state"])
    op.create_index("ix_channel_memory_items_reuse_scope", "channel_memory_items", ["reuse_scope"])
    op.create_index("ix_channel_memory_items_freshness", "channel_memory_items", ["freshness_state"])
    op.create_index("ix_channel_memory_items_source_hash", "channel_memory_items", ["source_content_hash"])
    op.create_index("ix_channel_memory_items_content_hash", "channel_memory_items", ["content_hash"])
    op.create_index("ix_channel_memory_items_created_at", "channel_memory_items", ["created_at"])

    op.create_table(
        "memory_facets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("facet_type", sa.String(length=80), nullable=False),
        sa.Column("facet_text", sa.Text(), nullable=False),
        sa.Column("facet_text_hash", sa.String(length=128), nullable=False),
        sa.Column("scope_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("allowed_use_cases_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("forbidden_use_cases_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("polarity", sa.String(length=40), server_default="NEUTRAL", nullable=False),
        sa.Column("confidence_label", sa.String(length=40), server_default="UNPROVEN", nullable=False),
        sa.Column("prompt_safety_state", sa.String(length=40), server_default="UNKNOWN", nullable=False),
        sa.Column("embedding_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["character_profile_id"], ["character_profiles.id"]),
        sa.ForeignKeyConstraint(["character_version_id"], ["character_versions.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["content_category_id"], ["content_categories.id"]),
        sa.ForeignKeyConstraint(["memory_item_id"], ["channel_memory_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_facets_item", "memory_facets", ["memory_item_id"])
    op.create_index("ix_memory_facets_company", "memory_facets", ["company_id"])
    op.create_index("ix_memory_facets_channel", "memory_facets", ["channel_workspace_id"])
    op.create_index("ix_memory_facets_category", "memory_facets", ["content_category_id"])
    op.create_index("ix_memory_facets_character_profile", "memory_facets", ["character_profile_id"])
    op.create_index("ix_memory_facets_type", "memory_facets", ["facet_type"])
    op.create_index("ix_memory_facets_text_hash", "memory_facets", ["facet_text_hash"])
    op.create_index("ix_memory_facets_prompt_safety", "memory_facets", ["prompt_safety_state"])
    op.create_index("ix_memory_facets_embedding_eligible", "memory_facets", ["embedding_eligible"])
    op.create_index("ix_memory_facets_created_at", "memory_facets", ["created_at"])

    op.create_table(
        "memory_review_queue_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("queue_status", sa.String(length=40), server_default="PENDING", nullable=False),
        sa.Column("reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["memory_item_id"], ["channel_memory_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_review_queue_item", "memory_review_queue_items", ["memory_item_id"])
    op.create_index("ix_memory_review_queue_status", "memory_review_queue_items", ["queue_status"])
    op.create_index("ix_memory_review_queue_created_at", "memory_review_queue_items", ["created_at"])

    op.create_table(
        "memory_approval_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=40), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("approved_prompt_use_cases_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("rejected_reason_codes_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["memory_item_id"], ["channel_memory_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_approval_decisions_item", "memory_approval_decisions", ["memory_item_id"])
    op.create_index("ix_memory_approval_decisions_decision", "memory_approval_decisions", ["decision"])
    op.create_index("ix_memory_approval_decisions_created_at", "memory_approval_decisions", ["created_at"])

    op.create_table(
        "memory_usage_manifests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("effective_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("memory_item_ids_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("memory_facet_ids_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("use_case", sa.String(length=120), nullable=False),
        sa.Column("usage_status", sa.String(length=40), server_default="PLANNED", nullable=False),
        sa.Column("digest_hash", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["effective_context_snapshot_id"], ["effective_channel_runtime_context_snapshots.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_usage_manifests_project", "memory_usage_manifests", ["video_project_id"])
    op.create_index("ix_memory_usage_manifests_package", "memory_usage_manifests", ["package_id"])
    op.create_index("ix_memory_usage_manifests_effective_context", "memory_usage_manifests", ["effective_context_snapshot_id"])
    op.create_index("ix_memory_usage_manifests_use_case", "memory_usage_manifests", ["use_case"])
    op.create_index("ix_memory_usage_manifests_status", "memory_usage_manifests", ["usage_status"])
    op.create_index("ix_memory_usage_manifests_created_at", "memory_usage_manifests", ["created_at"])

    op.create_table(
        "memory_source_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["memory_item_id"], ["channel_memory_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_source_links_item", "memory_source_links", ["memory_item_id"])
    op.create_index("ix_memory_source_links_source", "memory_source_links", ["source_type"])
    op.create_index("ix_memory_source_links_hash", "memory_source_links", ["source_hash"])
    op.create_index("ix_memory_source_links_created_at", "memory_source_links", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_memory_source_links_created_at", table_name="memory_source_links")
    op.drop_index("ix_memory_source_links_hash", table_name="memory_source_links")
    op.drop_index("ix_memory_source_links_source", table_name="memory_source_links")
    op.drop_index("ix_memory_source_links_item", table_name="memory_source_links")
    op.drop_table("memory_source_links")

    op.drop_index("ix_memory_usage_manifests_created_at", table_name="memory_usage_manifests")
    op.drop_index("ix_memory_usage_manifests_status", table_name="memory_usage_manifests")
    op.drop_index("ix_memory_usage_manifests_use_case", table_name="memory_usage_manifests")
    op.drop_index("ix_memory_usage_manifests_effective_context", table_name="memory_usage_manifests")
    op.drop_index("ix_memory_usage_manifests_package", table_name="memory_usage_manifests")
    op.drop_index("ix_memory_usage_manifests_project", table_name="memory_usage_manifests")
    op.drop_table("memory_usage_manifests")

    op.drop_index("ix_memory_approval_decisions_created_at", table_name="memory_approval_decisions")
    op.drop_index("ix_memory_approval_decisions_decision", table_name="memory_approval_decisions")
    op.drop_index("ix_memory_approval_decisions_item", table_name="memory_approval_decisions")
    op.drop_table("memory_approval_decisions")

    op.drop_index("ix_memory_review_queue_created_at", table_name="memory_review_queue_items")
    op.drop_index("ix_memory_review_queue_status", table_name="memory_review_queue_items")
    op.drop_index("ix_memory_review_queue_item", table_name="memory_review_queue_items")
    op.drop_table("memory_review_queue_items")

    op.drop_index("ix_memory_facets_created_at", table_name="memory_facets")
    op.drop_index("ix_memory_facets_embedding_eligible", table_name="memory_facets")
    op.drop_index("ix_memory_facets_prompt_safety", table_name="memory_facets")
    op.drop_index("ix_memory_facets_text_hash", table_name="memory_facets")
    op.drop_index("ix_memory_facets_type", table_name="memory_facets")
    op.drop_index("ix_memory_facets_character_profile", table_name="memory_facets")
    op.drop_index("ix_memory_facets_category", table_name="memory_facets")
    op.drop_index("ix_memory_facets_channel", table_name="memory_facets")
    op.drop_index("ix_memory_facets_company", table_name="memory_facets")
    op.drop_index("ix_memory_facets_item", table_name="memory_facets")
    op.drop_table("memory_facets")

    op.drop_index("ix_channel_memory_items_created_at", table_name="channel_memory_items")
    op.drop_index("ix_channel_memory_items_content_hash", table_name="channel_memory_items")
    op.drop_index("ix_channel_memory_items_source_hash", table_name="channel_memory_items")
    op.drop_index("ix_channel_memory_items_freshness", table_name="channel_memory_items")
    op.drop_index("ix_channel_memory_items_reuse_scope", table_name="channel_memory_items")
    op.drop_index("ix_channel_memory_items_prompt_safety", table_name="channel_memory_items")
    op.drop_index("ix_channel_memory_items_rights", table_name="channel_memory_items")
    op.drop_index("ix_channel_memory_items_approval", table_name="channel_memory_items")
    op.drop_index("ix_channel_memory_items_character_profile", table_name="channel_memory_items")
    op.drop_index("ix_channel_memory_items_category", table_name="channel_memory_items")
    op.drop_index("ix_channel_memory_items_channel", table_name="channel_memory_items")
    op.drop_index("ix_channel_memory_items_company", table_name="channel_memory_items")
    op.drop_table("channel_memory_items")
