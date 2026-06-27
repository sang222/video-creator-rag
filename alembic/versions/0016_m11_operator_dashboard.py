"""M11 operator dashboard cockpit

Revision ID: 0016_m11_operator_dashboard
Revises: 0015_m10_5_drive_offload
Create Date: 2026-06-27 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0016_m11_operator_dashboard"
down_revision: str | None = "0015_m10_5_drive_offload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

LIFECYCLE_STATE_CHECK = "lifecycle_state in ('DRAFT','READY','ACTIVE','PAUSED','DEACTIVATED','ARCHIVED')"
HEALTH_STATUS_CHECK = (
    "health_status in ('NEW','OBSERVING','HEALTHY','LOW_VIEW','NO_VIEW','LOW_PROFIT',"
    "'RECOVERY_ACTIVE','WATCHLIST','NEEDS_HUMAN_REVIEW')"
)
LIFECYCLE_ACTION_CHECK = (
    "action in ('KEEP_ACTIVE','PAUSE_DAILY_GENERATION','CONTINUE_OBSERVING','ADD_MANUAL_NOTE',"
    "'DEACTIVATE_CHANNEL','ARCHIVE_CHANNEL','REACTIVATE_CHANNEL')"
)
LEARNING_ACTION_CHECK = "action in ('APPROVE','REJECT','REQUEST_MORE_EVIDENCE','SUPPRESS','EXPIRE')"
DECISION_STATE_CHECK = "decision_state in ('RECORDED','BLOCKED')"
APPROVED_PLAYBOOK_STATE_CHECK = "state in ('APPROVED','RETIRED','SUPERSEDED')"


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _uuid(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "channel_lifecycle_decisions",
        _uuid("id", nullable=False),
        _uuid("channel_workspace_id", nullable=False),
        _uuid("company_id", nullable=False),
        sa.Column("previous_lifecycle_state", sa.String(length=40), nullable=True),
        sa.Column("lifecycle_state", sa.String(length=40), nullable=False),
        sa.Column("health_status", sa.String(length=40), server_default="NEW", nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("next_action", sa.Text(), nullable=False),
        _uuid("decided_by_user_id"),
        sa.Column("decision_metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint(LIFECYCLE_STATE_CHECK.replace("lifecycle_state", "previous_lifecycle_state"), name="ck_channel_lifecycle_previous_state"),
        sa.CheckConstraint(LIFECYCLE_STATE_CHECK, name="ck_channel_lifecycle_state"),
        sa.CheckConstraint(HEALTH_STATUS_CHECK, name="ck_channel_lifecycle_health"),
        sa.CheckConstraint(LIFECYCLE_ACTION_CHECK, name="ck_channel_lifecycle_action"),
        sa.CheckConstraint("jsonb_typeof(decision_metadata) = 'object'", name="ck_channel_lifecycle_metadata_object"),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channel_lifecycle_decisions_channel", "channel_lifecycle_decisions", ["channel_workspace_id"])
    op.create_index("ix_channel_lifecycle_decisions_company", "channel_lifecycle_decisions", ["company_id"])
    op.create_index("ix_channel_lifecycle_decisions_state", "channel_lifecycle_decisions", ["lifecycle_state"])
    op.create_index("ix_channel_lifecycle_decisions_health", "channel_lifecycle_decisions", ["health_status"])
    op.create_index("ix_channel_lifecycle_decisions_created_at", "channel_lifecycle_decisions", ["created_at"])

    op.create_table(
        "learning_review_decisions",
        _uuid("id", nullable=False),
        _uuid("learning_candidate_id", nullable=False),
        _uuid("learning_review_queue_item_id"),
        _uuid("evidence_bundle_id"),
        _uuid("playbook_candidate_draft_id"),
        _uuid("approved_playbook_entry_id"),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id"),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("decision_state", sa.String(length=40), server_default="RECORDED", nullable=False),
        sa.Column("actor_role", sa.String(length=80), nullable=False),
        _uuid("decided_by_user_id"),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint(LEARNING_ACTION_CHECK, name="ck_learning_review_decisions_action"),
        sa.CheckConstraint(DECISION_STATE_CHECK, name="ck_learning_review_decisions_state"),
        sa.CheckConstraint("jsonb_typeof(reason_codes) = 'array'", name="ck_learning_review_decisions_reason_codes_array"),
        sa.CheckConstraint("jsonb_typeof(evidence_refs) = 'array'", name="ck_learning_review_decisions_evidence_array"),
        sa.CheckConstraint("jsonb_typeof(technical_appendix) = 'object'", name="ck_learning_review_decisions_appendix_object"),
        sa.ForeignKeyConstraint(["learning_candidate_id"], ["learning_candidates.id"]),
        sa.ForeignKeyConstraint(["learning_review_queue_item_id"], ["learning_review_queue_items.id"]),
        sa.ForeignKeyConstraint(["evidence_bundle_id"], ["learning_evidence_bundles.id"]),
        sa.ForeignKeyConstraint(["playbook_candidate_draft_id"], ["playbook_candidate_drafts.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_review_decisions_candidate", "learning_review_decisions", ["learning_candidate_id"])
    op.create_index("ix_learning_review_decisions_queue_item", "learning_review_decisions", ["learning_review_queue_item_id"])
    op.create_index("ix_learning_review_decisions_company", "learning_review_decisions", ["company_id"])
    op.create_index("ix_learning_review_decisions_channel", "learning_review_decisions", ["channel_workspace_id"])
    op.create_index("ix_learning_review_decisions_action", "learning_review_decisions", ["action"])
    op.create_index("ix_learning_review_decisions_created_at", "learning_review_decisions", ["created_at"])

    op.create_table(
        "approved_playbook_entries",
        _uuid("id", nullable=False),
        _uuid("learning_candidate_id", nullable=False),
        _uuid("learning_review_decision_id"),
        _uuid("playbook_candidate_draft_id"),
        _uuid("evidence_bundle_id"),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id"),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("playbook_text", sa.Text(), nullable=False),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("limitations", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("counter_evidence", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("policy_rights_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("state", sa.String(length=40), server_default="APPROVED", nullable=False),
        _uuid("approved_by_user_id"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(APPROVED_PLAYBOOK_STATE_CHECK, name="ck_approved_playbook_entries_state"),
        sa.CheckConstraint("jsonb_typeof(evidence_refs) = 'array'", name="ck_approved_playbook_entries_evidence_array"),
        sa.CheckConstraint("jsonb_typeof(limitations) = 'array'", name="ck_approved_playbook_entries_limitations_array"),
        sa.CheckConstraint("jsonb_typeof(counter_evidence) = 'array'", name="ck_approved_playbook_entries_counter_array"),
        sa.CheckConstraint("jsonb_typeof(policy_rights_summary) = 'object'", name="ck_approved_playbook_entries_policy_object"),
        sa.ForeignKeyConstraint(["learning_candidate_id"], ["learning_candidates.id"]),
        sa.ForeignKeyConstraint(["learning_review_decision_id"], ["learning_review_decisions.id"]),
        sa.ForeignKeyConstraint(["playbook_candidate_draft_id"], ["playbook_candidate_drafts.id"]),
        sa.ForeignKeyConstraint(["evidence_bundle_id"], ["learning_evidence_bundles.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approved_playbook_entries_candidate", "approved_playbook_entries", ["learning_candidate_id"])
    op.create_index("ix_approved_playbook_entries_company", "approved_playbook_entries", ["company_id"])
    op.create_index("ix_approved_playbook_entries_channel", "approved_playbook_entries", ["channel_workspace_id"])
    op.create_index("ix_approved_playbook_entries_scope", "approved_playbook_entries", ["scope"])
    op.create_index("ix_approved_playbook_entries_category", "approved_playbook_entries", ["category"])
    op.create_index("ix_approved_playbook_entries_state", "approved_playbook_entries", ["state"])
    op.create_index("ix_approved_playbook_entries_created_at", "approved_playbook_entries", ["created_at"])

    op.create_foreign_key(
        "fk_lrd_approved_playbook_entry",
        "learning_review_decisions",
        "approved_playbook_entries",
        ["approved_playbook_entry_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_lrd_approved_playbook_entry",
        "learning_review_decisions",
        type_="foreignkey",
    )
    op.drop_index("ix_approved_playbook_entries_created_at", table_name="approved_playbook_entries")
    op.drop_index("ix_approved_playbook_entries_state", table_name="approved_playbook_entries")
    op.drop_index("ix_approved_playbook_entries_category", table_name="approved_playbook_entries")
    op.drop_index("ix_approved_playbook_entries_scope", table_name="approved_playbook_entries")
    op.drop_index("ix_approved_playbook_entries_channel", table_name="approved_playbook_entries")
    op.drop_index("ix_approved_playbook_entries_company", table_name="approved_playbook_entries")
    op.drop_index("ix_approved_playbook_entries_candidate", table_name="approved_playbook_entries")
    op.drop_table("approved_playbook_entries")

    op.drop_index("ix_learning_review_decisions_created_at", table_name="learning_review_decisions")
    op.drop_index("ix_learning_review_decisions_action", table_name="learning_review_decisions")
    op.drop_index("ix_learning_review_decisions_channel", table_name="learning_review_decisions")
    op.drop_index("ix_learning_review_decisions_company", table_name="learning_review_decisions")
    op.drop_index("ix_learning_review_decisions_queue_item", table_name="learning_review_decisions")
    op.drop_index("ix_learning_review_decisions_candidate", table_name="learning_review_decisions")
    op.drop_table("learning_review_decisions")

    op.drop_index("ix_channel_lifecycle_decisions_created_at", table_name="channel_lifecycle_decisions")
    op.drop_index("ix_channel_lifecycle_decisions_health", table_name="channel_lifecycle_decisions")
    op.drop_index("ix_channel_lifecycle_decisions_state", table_name="channel_lifecycle_decisions")
    op.drop_index("ix_channel_lifecycle_decisions_company", table_name="channel_lifecycle_decisions")
    op.drop_index("ix_channel_lifecycle_decisions_channel", table_name="channel_lifecycle_decisions")
    op.drop_table("channel_lifecycle_decisions")
