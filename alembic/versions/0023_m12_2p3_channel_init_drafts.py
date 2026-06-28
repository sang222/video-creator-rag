"""M12.2P3 research assisted channel init drafts

Revision ID: 0023_m12_2p3_channel_init_drafts
Revises: b35988076109
Create Date: 2026-06-28 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0023_m12_2p3_channel_init_drafts"
down_revision: str | None = "b35988076109"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
WORKFLOW_STATUS_CHECK = (
    "workflow_status in ("
    "'RESEARCH_PENDING','RESEARCH_COMPLETE','NEEDS_HUMAN_REVIEW','READY_TO_COMPILE',"
    "'COMPILED_PARTIAL','COMPILED_COMPLETE','ACTIVATED','BLOCKED'"
    ")"
)
CONTRACT_STATUS_CHECK = "contract_status is null or contract_status in ('COMPLETE','PARTIAL','MISSING','STALE','CONTRADICTORY')"
PUBLIC_PRESENCE_CHECK = "public_presence_mode in ('EXISTING_PUBLIC_CHANNEL','NEW_CHANNEL_NO_PUBLIC_FOOTPRINT')"


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "channel_init_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_name", sa.Text(), nullable=False),
        sa.Column("public_presence_mode", sa.String(length=80), nullable=False),
        sa.Column("youtube_url_or_handle", sa.Text(), nullable=True),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("social_profile_links", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("operator_note_purpose", sa.Text(), nullable=False),
        sa.Column("intended_content_language", sa.Text(), nullable=True),
        sa.Column("intended_primary_market", sa.Text(), nullable=True),
        sa.Column("owner_operator_language", sa.Text(), server_default="vi-VN", nullable=False),
        sa.Column("initial_topic_pillar_hints", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("source_usage_attestation", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("workflow_status", sa.String(length=80), server_default="RESEARCH_PENDING", nullable=False),
        sa.Column("contract_status", sa.String(length=40), nullable=True),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel_profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("compiled_policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(WORKFLOW_STATUS_CHECK, name="ck_channel_init_drafts_workflow_status"),
        sa.CheckConstraint(CONTRACT_STATUS_CHECK, name="ck_channel_init_drafts_contract_status"),
        sa.CheckConstraint(PUBLIC_PRESENCE_CHECK, name="ck_channel_init_drafts_public_presence_mode"),
        sa.CheckConstraint("jsonb_typeof(social_profile_links) = 'array'", name="ck_channel_init_drafts_social_links_array"),
        sa.CheckConstraint("jsonb_typeof(initial_topic_pillar_hints) = 'array'", name="ck_channel_init_drafts_pillar_hints_array"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"]),
        sa.ForeignKeyConstraint(["compiled_policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channel_init_drafts_company_id", "channel_init_drafts", ["company_id"])
    op.create_index("ix_channel_init_drafts_workflow_status", "channel_init_drafts", ["workflow_status"])
    op.create_index("ix_channel_init_drafts_channel_id", "channel_init_drafts", ["channel_id"])
    op.create_index("ix_channel_init_drafts_created_at", "channel_init_drafts", ["created_at"])

    op.create_table(
        "channel_contract_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("init_draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_name", sa.Text(), nullable=False),
        sa.Column("source_urls", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("admin_minimal_input", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("suggested_channel_contract", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("field_source_map_json", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("confidence_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("missing_fields", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("human_questions", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("risks", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("workflow_status", sa.String(length=80), server_default="NEEDS_HUMAN_REVIEW", nullable=False),
        sa.Column("contract_status", sa.String(length=40), nullable=True),
        sa.Column("review_decision_log_json", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(WORKFLOW_STATUS_CHECK, name="ck_channel_contract_drafts_workflow_status"),
        sa.CheckConstraint(CONTRACT_STATUS_CHECK, name="ck_channel_contract_drafts_contract_status"),
        sa.CheckConstraint("jsonb_typeof(source_urls) = 'array'", name="ck_channel_contract_drafts_source_urls_array"),
        sa.CheckConstraint("jsonb_typeof(admin_minimal_input) = 'object'", name="ck_channel_contract_drafts_admin_input_object"),
        sa.CheckConstraint("jsonb_typeof(suggested_channel_contract) = 'object'", name="ck_channel_contract_drafts_contract_object"),
        sa.CheckConstraint("jsonb_typeof(field_source_map_json) = 'object'", name="ck_channel_contract_drafts_field_map_object"),
        sa.CheckConstraint("jsonb_typeof(confidence_summary) = 'object'", name="ck_channel_contract_drafts_confidence_object"),
        sa.CheckConstraint("jsonb_typeof(missing_fields) = 'array'", name="ck_channel_contract_drafts_missing_fields_array"),
        sa.CheckConstraint("jsonb_typeof(human_questions) = 'array'", name="ck_channel_contract_drafts_questions_array"),
        sa.CheckConstraint("jsonb_typeof(risks) = 'array'", name="ck_channel_contract_drafts_risks_array"),
        sa.CheckConstraint("jsonb_typeof(evidence_refs) = 'array'", name="ck_channel_contract_drafts_evidence_array"),
        sa.CheckConstraint("jsonb_typeof(review_decision_log_json) = 'array'", name="ck_channel_contract_drafts_review_log_array"),
        sa.ForeignKeyConstraint(["init_draft_id"], ["channel_init_drafts.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channel_contract_drafts_init_draft_id", "channel_contract_drafts", ["init_draft_id"])
    op.create_index("ix_channel_contract_drafts_company_id", "channel_contract_drafts", ["company_id"])
    op.create_index("ix_channel_contract_drafts_workflow_status", "channel_contract_drafts", ["workflow_status"])
    op.create_index("ix_channel_contract_drafts_created_at", "channel_contract_drafts", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_channel_contract_drafts_created_at", table_name="channel_contract_drafts")
    op.drop_index("ix_channel_contract_drafts_workflow_status", table_name="channel_contract_drafts")
    op.drop_index("ix_channel_contract_drafts_company_id", table_name="channel_contract_drafts")
    op.drop_index("ix_channel_contract_drafts_init_draft_id", table_name="channel_contract_drafts")
    op.drop_table("channel_contract_drafts")

    op.drop_index("ix_channel_init_drafts_created_at", table_name="channel_init_drafts")
    op.drop_index("ix_channel_init_drafts_channel_id", table_name="channel_init_drafts")
    op.drop_index("ix_channel_init_drafts_workflow_status", table_name="channel_init_drafts")
    op.drop_index("ix_channel_init_drafts_company_id", table_name="channel_init_drafts")
    op.drop_table("channel_init_drafts")
