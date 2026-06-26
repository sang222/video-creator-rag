"""M10 learning workflow and review queue foundation

Revision ID: 0011_m10_learning_review_queue
Revises: 0010_m9_post_publish_diagnostics
Create Date: 2026-06-26 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0011_m10_learning_review_queue"
down_revision: str | None = "0010_m9_post_publish_diagnostics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

CANDIDATE_TYPE_CHECK = (
    "candidate_type in ('TOPIC_DEMAND_PATTERN','PACKAGING_PATTERN','HOOK_PATTERN','RETENTION_PATTERN',"
    "'VISUAL_SOURCE_PATTERN','VOICE_NARRATION_PATTERN','POLICY_RIGHTS_PATTERN','COST_EFFICIENCY_PATTERN',"
    "'RECOVERY_PATTERN','CHANNEL_FIT_PATTERN','OTHER')"
)
CANDIDATE_STATE_CHECK = (
    "candidate_state in ('GENERATED','NEEDS_MORE_EVIDENCE','INELIGIBLE_LOW_EVIDENCE','BLOCKED_POLICY_RISK',"
    "'BLOCKED_RIGHTS_RISK','READY_FOR_HUMAN_REVIEW','EXPIRED','CANCELLED')"
)
CONFIDENCE_CHECK = "confidence_label in ('HIGH','MEDIUM','LOW','UNKNOWN')"
ELIGIBILITY_RESULT_CHECK = "result in ('ELIGIBLE_FOR_REVIEW','NEEDS_MORE_EVIDENCE','BLOCKED','INELIGIBLE')"
PLAYBOOK_CATEGORY_CHECK = (
    "playbook_category in ('TOPIC','PACKAGING','HOOK','RETENTION','VISUAL_SOURCE','VOICE','POLICY','COST','RECOVERY','OTHER')"
)
PLAYBOOK_STATE_CHECK = "state in ('DRAFT','READY_FOR_REVIEW','BLOCKED','EXPIRED')"
QUEUE_PRIORITY_CHECK = "priority in ('LOW','NORMAL','HIGH','CRITICAL')"
QUEUE_STATE_CHECK = "queue_state in ('READY_FOR_HUMAN_REVIEW','NEEDS_MORE_EVIDENCE','BLOCKED','EXPIRED','CANCELLED')"
RECOMMENDED_SCOPE_CHECK = "recommended_scope in ('CHANNEL','SERIES','COMPANY_DEBRANDED','DO_NOT_PROMOTE','UNKNOWN')"
RUN_MODE_CHECK = "run_mode in ('MOCK','RULE_BASED','MANUAL_TRIGGER','REAL_DISABLED')"
RUN_STATE_CHECK = "run_state in ('PENDING','RUNNING','COMPLETED','BLOCKED','FAILED','CANCELLED')"
RISK_LEVEL_CHECK = "risk_level in ('LOW','MEDIUM','HIGH','BLOCKED','UNKNOWN')"


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "learning_candidate_generation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_failure_trace_report_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_recovery_proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_analytics_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_uploaded_video_metrics_summary_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_mode", sa.String(length=40), nullable=False),
        sa.Column("run_state", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generated_candidate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(RUN_MODE_CHECK, name="ck_learning_candidate_generation_runs_mode"),
        sa.CheckConstraint(RUN_STATE_CHECK, name="ck_learning_candidate_generation_runs_state"),
        sa.CheckConstraint("run_state not in ('BLOCKED','FAILED') or jsonb_array_length(reason_codes) > 0", name="ck_learning_runs_blocked_has_reason"),
        sa.CheckConstraint("run_state not in ('BLOCKED','FAILED') or next_action is not null", name="ck_learning_runs_blocked_has_next_action"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["source_failure_trace_report_id"], ["failure_trace_reports.id"]),
        sa.ForeignKeyConstraint(["source_recovery_proposal_id"], ["recovery_proposals.id"]),
        sa.ForeignKeyConstraint(["source_analytics_snapshot_id"], ["analytics_snapshots.id"]),
        sa.ForeignKeyConstraint(["source_uploaded_video_metrics_summary_id"], ["uploaded_video_metrics_summaries.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_runs_company_id", "learning_candidate_generation_runs", ["company_id"])
    op.create_index("ix_learning_runs_channel_workspace_id", "learning_candidate_generation_runs", ["channel_workspace_id"])
    op.create_index("ix_learning_runs_video_project_id", "learning_candidate_generation_runs", ["video_project_id"])
    op.create_index("ix_learning_runs_uploaded_video_id", "learning_candidate_generation_runs", ["uploaded_video_id"])
    op.create_index("ix_learning_runs_state", "learning_candidate_generation_runs", ["run_state"])
    op.create_index("ix_learning_runs_mode", "learning_candidate_generation_runs", ["run_mode"])
    op.create_index("ix_learning_runs_created_at", "learning_candidate_generation_runs", ["created_at"])

    op.create_table(
        "learning_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_type", sa.String(length=60), nullable=False),
        sa.Column("candidate_state", sa.String(length=60), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("friendly_status", sa.Text(), nullable=False),
        sa.Column("candidate_summary", sa.Text(), nullable=False),
        sa.Column("suggested_learning", sa.Text(), nullable=False),
        sa.Column("suggested_playbook_text", sa.Text(), nullable=True),
        sa.Column("recommended_scope", sa.String(length=40), nullable=False),
        sa.Column("confidence_label", sa.String(length=40), nullable=False),
        sa.Column("risk_level", sa.String(length=40), nullable=False),
        sa.Column("evidence_bundle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("eligibility_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("diagnostic_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("recovery_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("metric_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("policy_flags", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("rights_flags", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("limitations", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("counter_evidence", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(CANDIDATE_TYPE_CHECK, name="ck_learning_candidates_type"),
        sa.CheckConstraint(CANDIDATE_STATE_CHECK, name="ck_learning_candidates_state"),
        sa.CheckConstraint(RECOMMENDED_SCOPE_CHECK, name="ck_learning_candidates_scope"),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_learning_candidates_confidence"),
        sa.CheckConstraint(RISK_LEVEL_CHECK, name="ck_learning_candidates_risk"),
        sa.ForeignKeyConstraint(["generation_run_id"], ["learning_candidate_generation_runs.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_candidates_generation_run_id", "learning_candidates", ["generation_run_id"])
    op.create_index("ix_learning_candidates_company_id", "learning_candidates", ["company_id"])
    op.create_index("ix_learning_candidates_channel_workspace_id", "learning_candidates", ["channel_workspace_id"])
    op.create_index("ix_learning_candidates_video_project_id", "learning_candidates", ["video_project_id"])
    op.create_index("ix_learning_candidates_uploaded_video_id", "learning_candidates", ["uploaded_video_id"])
    op.create_index("ix_learning_candidates_type", "learning_candidates", ["candidate_type"])
    op.create_index("ix_learning_candidates_state", "learning_candidates", ["candidate_state"])
    op.create_index("ix_learning_candidates_created_at", "learning_candidates", ["created_at"])

    op.create_table(
        "learning_evidence_bundles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("learning_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.Column("source_video_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("source_project_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("analytics_snapshot_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("diagnostic_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("recovery_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("metric_support", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("counter_evidence", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("limitations", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("freshness_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("confidence_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("policy_rights_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["learning_candidate_id"], ["learning_candidates.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_evidence_bundles_candidate_id", "learning_evidence_bundles", ["learning_candidate_id"])
    op.create_index("ix_learning_evidence_bundles_company_id", "learning_evidence_bundles", ["company_id"])
    op.create_index("ix_learning_evidence_bundles_channel_id", "learning_evidence_bundles", ["channel_workspace_id"])
    op.create_index("ix_learning_evidence_bundles_created_at", "learning_evidence_bundles", ["created_at"])

    op.create_table(
        "learning_promotion_eligibility_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("learning_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_bundle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result", sa.String(length=40), nullable=False),
        sa.Column("min_evidence_met", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("metric_freshness_ok", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("policy_flags_ok", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rights_flags_ok", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("confidence_label", sa.String(length=40), nullable=False),
        sa.Column("risk_level", sa.String(length=40), nullable=False),
        sa.Column("blockers", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("warnings", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        _created_at(),
        sa.CheckConstraint(ELIGIBILITY_RESULT_CHECK, name="ck_learning_eligibility_runs_result"),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_learning_eligibility_runs_confidence"),
        sa.CheckConstraint(RISK_LEVEL_CHECK, name="ck_learning_eligibility_runs_risk"),
        sa.ForeignKeyConstraint(["learning_candidate_id"], ["learning_candidates.id"]),
        sa.ForeignKeyConstraint(["evidence_bundle_id"], ["learning_evidence_bundles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_eligibility_candidate_id", "learning_promotion_eligibility_runs", ["learning_candidate_id"])
    op.create_index("ix_learning_eligibility_bundle_id", "learning_promotion_eligibility_runs", ["evidence_bundle_id"])
    op.create_index("ix_learning_eligibility_result", "learning_promotion_eligibility_runs", ["result"])
    op.create_index("ix_learning_eligibility_created_at", "learning_promotion_eligibility_runs", ["created_at"])

    op.create_foreign_key(
        "fk_learning_candidates_evidence_bundle_id",
        "learning_candidates",
        "learning_evidence_bundles",
        ["evidence_bundle_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_learning_candidates_eligibility_run_id",
        "learning_candidates",
        "learning_promotion_eligibility_runs",
        ["eligibility_run_id"],
        ["id"],
    )

    op.create_table(
        "learning_review_queue_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("learning_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_bundle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("eligibility_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("queue_state", sa.String(length=40), nullable=False),
        sa.Column("priority", sa.String(length=40), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("friendly_status", sa.Text(), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.Column("recommended_scope", sa.String(length=40), nullable=False),
        sa.Column("confidence_label", sa.String(length=40), nullable=False),
        sa.Column("risk_level", sa.String(length=40), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=False),
        sa.Column("approval_actions_allowed", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("source_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("audit_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(QUEUE_STATE_CHECK, name="ck_learning_review_queue_items_state"),
        sa.CheckConstraint(QUEUE_PRIORITY_CHECK, name="ck_learning_review_queue_items_priority"),
        sa.CheckConstraint(RECOMMENDED_SCOPE_CHECK, name="ck_learning_review_queue_items_scope"),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_learning_review_queue_items_confidence"),
        sa.CheckConstraint(RISK_LEVEL_CHECK, name="ck_learning_review_queue_items_risk"),
        sa.ForeignKeyConstraint(["learning_candidate_id"], ["learning_candidates.id"]),
        sa.ForeignKeyConstraint(["evidence_bundle_id"], ["learning_evidence_bundles.id"]),
        sa.ForeignKeyConstraint(["eligibility_run_id"], ["learning_promotion_eligibility_runs.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_review_queue_candidate_id", "learning_review_queue_items", ["learning_candidate_id"])
    op.create_index("ix_learning_review_queue_company_id", "learning_review_queue_items", ["company_id"])
    op.create_index("ix_learning_review_queue_channel_id", "learning_review_queue_items", ["channel_workspace_id"])
    op.create_index("ix_learning_review_queue_project_id", "learning_review_queue_items", ["video_project_id"])
    op.create_index("ix_learning_review_queue_uploaded_video_id", "learning_review_queue_items", ["uploaded_video_id"])
    op.create_index("ix_learning_review_queue_state", "learning_review_queue_items", ["queue_state"])
    op.create_index("ix_learning_review_queue_priority", "learning_review_queue_items", ["priority"])
    op.create_index("ix_learning_review_queue_created_at", "learning_review_queue_items", ["created_at"])

    op.create_table(
        "playbook_candidate_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("learning_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_scope", sa.String(length=40), nullable=False),
        sa.Column("playbook_category", sa.String(length=40), nullable=False),
        sa.Column("draft_text", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("risk_notes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("candidate_scope in ('CHANNEL','SERIES','COMPANY_DEBRANDED','UNKNOWN')", name="ck_playbook_candidate_drafts_scope"),
        sa.CheckConstraint(PLAYBOOK_CATEGORY_CHECK, name="ck_playbook_candidate_drafts_category"),
        sa.CheckConstraint(PLAYBOOK_STATE_CHECK, name="ck_playbook_candidate_drafts_state"),
        sa.CheckConstraint("state <> 'READY_FOR_REVIEW' or draft_text <> ''", name="ck_playbook_candidate_drafts_text_present"),
        sa.ForeignKeyConstraint(["learning_candidate_id"], ["learning_candidates.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_playbook_candidate_drafts_candidate_id", "playbook_candidate_drafts", ["learning_candidate_id"])
    op.create_index("ix_playbook_candidate_drafts_company_id", "playbook_candidate_drafts", ["company_id"])
    op.create_index("ix_playbook_candidate_drafts_channel_id", "playbook_candidate_drafts", ["channel_workspace_id"])
    op.create_index("ix_playbook_candidate_drafts_category", "playbook_candidate_drafts", ["playbook_category"])
    op.create_index("ix_playbook_candidate_drafts_state", "playbook_candidate_drafts", ["state"])
    op.create_index("ix_playbook_candidate_drafts_created_at", "playbook_candidate_drafts", ["created_at"])


def downgrade() -> None:
    for table, indexes in [
        (
            "playbook_candidate_drafts",
            [
                "ix_playbook_candidate_drafts_created_at",
                "ix_playbook_candidate_drafts_state",
                "ix_playbook_candidate_drafts_category",
                "ix_playbook_candidate_drafts_channel_id",
                "ix_playbook_candidate_drafts_company_id",
                "ix_playbook_candidate_drafts_candidate_id",
            ],
        ),
        (
            "learning_review_queue_items",
            [
                "ix_learning_review_queue_created_at",
                "ix_learning_review_queue_priority",
                "ix_learning_review_queue_state",
                "ix_learning_review_queue_uploaded_video_id",
                "ix_learning_review_queue_project_id",
                "ix_learning_review_queue_channel_id",
                "ix_learning_review_queue_company_id",
                "ix_learning_review_queue_candidate_id",
            ],
        ),
    ]:
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)

    op.drop_constraint("fk_learning_candidates_eligibility_run_id", "learning_candidates", type_="foreignkey")
    op.drop_constraint("fk_learning_candidates_evidence_bundle_id", "learning_candidates", type_="foreignkey")

    for table, indexes in [
        (
            "learning_promotion_eligibility_runs",
            [
                "ix_learning_eligibility_created_at",
                "ix_learning_eligibility_result",
                "ix_learning_eligibility_bundle_id",
                "ix_learning_eligibility_candidate_id",
            ],
        ),
        (
            "learning_evidence_bundles",
            [
                "ix_learning_evidence_bundles_created_at",
                "ix_learning_evidence_bundles_channel_id",
                "ix_learning_evidence_bundles_company_id",
                "ix_learning_evidence_bundles_candidate_id",
            ],
        ),
        (
            "learning_candidates",
            [
                "ix_learning_candidates_created_at",
                "ix_learning_candidates_state",
                "ix_learning_candidates_type",
                "ix_learning_candidates_uploaded_video_id",
                "ix_learning_candidates_video_project_id",
                "ix_learning_candidates_channel_workspace_id",
                "ix_learning_candidates_company_id",
                "ix_learning_candidates_generation_run_id",
            ],
        ),
        (
            "learning_candidate_generation_runs",
            [
                "ix_learning_runs_created_at",
                "ix_learning_runs_mode",
                "ix_learning_runs_state",
                "ix_learning_runs_uploaded_video_id",
                "ix_learning_runs_video_project_id",
                "ix_learning_runs_channel_workspace_id",
                "ix_learning_runs_company_id",
            ],
        ),
    ]:
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
