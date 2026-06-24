"""M5 daily run context and admission foundation

Revision ID: 0006_m5_daily_run
Revises: 0005_m4_ops_foundation
Create Date: 2026-06-24 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_m5_daily_run"
down_revision: str | None = "0005_m4_ops_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


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
        "editorial_calendar_slots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot_date", sa.Date(), nullable=False),
        sa.Column("slot_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("production_goal", sa.Text(), nullable=True),
        sa.Column("target_platforms", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("content_pillar", sa.Text(), nullable=True),
        sa.Column("series_key", sa.Text(), nullable=True),
        sa.Column("format_hint", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=40), nullable=False),
        sa.Column("operational_envelope", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("slot_type in ('DAILY','WEEKLY','CAMPAIGN','EVERGREEN','EXPERIMENT','MANUAL')", name="ck_editorial_calendar_slots_slot_type"),
        sa.CheckConstraint("status in ('OPEN','ASSIGNED','ADMITTED','SKIPPED','CANCELLED')", name="ck_editorial_calendar_slots_status"),
        sa.CheckConstraint("risk_level in ('LOW','MEDIUM','HIGH','UNKNOWN')", name="ck_editorial_calendar_slots_risk_level"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_editorial_calendar_slots_company_id", "editorial_calendar_slots", ["company_id"])
    op.create_index("ix_editorial_calendar_slots_channel_workspace_id", "editorial_calendar_slots", ["channel_workspace_id"])
    op.create_index("ix_editorial_calendar_slots_policy_snapshot_id", "editorial_calendar_slots", ["policy_snapshot_id"])
    op.create_index("ix_editorial_calendar_slots_slot_date", "editorial_calendar_slots", ["slot_date"])
    op.create_index("ix_editorial_calendar_slots_status", "editorial_calendar_slots", ["status"])
    op.create_index("ix_editorial_calendar_slots_created_at", "editorial_calendar_slots", ["created_at"])

    op.create_table(
        "retrieval_plan_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=60), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel_profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("editorial_calendar_slot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("allowed_sources", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("excluded_sources", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("redaction_rules", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=True),
        sa.Column("source_order", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("plan_hash", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        _created_at(),
        sa.CheckConstraint("purpose in ('DAILY_IDEA','PROJECT_ADMISSION','AUTHORITY_REVIEW','SEARCH_DEMAND','TEST')", name="ck_retrieval_plan_snapshots_purpose"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["editorial_calendar_slot_id"], ["editorial_calendar_slots.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retrieval_plan_snapshots_purpose", "retrieval_plan_snapshots", ["purpose"])
    op.create_index("ix_retrieval_plan_snapshots_company_id", "retrieval_plan_snapshots", ["company_id"])
    op.create_index("ix_retrieval_plan_snapshots_channel_workspace_id", "retrieval_plan_snapshots", ["channel_workspace_id"])
    op.create_index("ix_retrieval_plan_snapshots_policy_snapshot_id", "retrieval_plan_snapshots", ["policy_snapshot_id"])
    op.create_index("ix_retrieval_plan_snapshots_created_at", "retrieval_plan_snapshots", ["created_at"])

    op.create_table(
        "context_pack_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("retrieval_plan_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=60), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel_profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("editorial_calendar_slot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("policy_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("metric_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("memory_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("pack_content", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("freshness_state", sa.String(length=40), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("pack_hash", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        _created_at(),
        sa.CheckConstraint("purpose in ('DAILY_IDEA','PROJECT_ADMISSION','AUTHORITY_REVIEW','SEARCH_DEMAND','TEST')", name="ck_context_pack_snapshots_purpose"),
        sa.CheckConstraint("freshness_state in ('FRESH','STALE','UNKNOWN','NOT_REQUIRED')", name="ck_context_pack_snapshots_freshness_state"),
        sa.CheckConstraint("confidence_level in ('HIGH','MEDIUM','LOW','UNKNOWN')", name="ck_context_pack_snapshots_confidence_level"),
        sa.ForeignKeyConstraint(["retrieval_plan_snapshot_id"], ["retrieval_plan_snapshots.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["editorial_calendar_slot_id"], ["editorial_calendar_slots.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_context_pack_snapshots_plan_id", "context_pack_snapshots", ["retrieval_plan_snapshot_id"])
    op.create_index("ix_context_pack_snapshots_purpose", "context_pack_snapshots", ["purpose"])
    op.create_index("ix_context_pack_snapshots_company_id", "context_pack_snapshots", ["company_id"])
    op.create_index("ix_context_pack_snapshots_channel_workspace_id", "context_pack_snapshots", ["channel_workspace_id"])
    op.create_index("ix_context_pack_snapshots_policy_snapshot_id", "context_pack_snapshots", ["policy_snapshot_id"])
    op.create_index("ix_context_pack_snapshots_created_at", "context_pack_snapshots", ["created_at"])

    op.create_table(
        "channel_daily_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("editorial_calendar_slot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("run_mode", sa.String(length=40), nullable=False),
        sa.Column("trigger_type", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("context_pack_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel_state_pack_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("daily_idea_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_admission_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("status in ('PENDING','RUNNING','COMPLETED','BLOCKED','FAILED','CANCELLED')", name="ck_channel_daily_runs_status"),
        sa.CheckConstraint("run_mode in ('MOCK','REAL_DISABLED')", name="ck_channel_daily_runs_run_mode"),
        sa.CheckConstraint("trigger_type in ('MANUAL','SCHEDULED','TEST')", name="ck_channel_daily_runs_trigger_type"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["editorial_calendar_slot_id"], ["editorial_calendar_slots.id"]),
        sa.ForeignKeyConstraint(["context_pack_snapshot_id"], ["context_pack_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channel_daily_runs_company_id", "channel_daily_runs", ["company_id"])
    op.create_index("ix_channel_daily_runs_channel_workspace_id", "channel_daily_runs", ["channel_workspace_id"])
    op.create_index("ix_channel_daily_runs_policy_snapshot_id", "channel_daily_runs", ["policy_snapshot_id"])
    op.create_index("ix_channel_daily_runs_slot_id", "channel_daily_runs", ["editorial_calendar_slot_id"])
    op.create_index("ix_channel_daily_runs_run_date", "channel_daily_runs", ["run_date"])
    op.create_index("ix_channel_daily_runs_status", "channel_daily_runs", ["status"])
    op.create_index("ix_channel_daily_runs_created_at", "channel_daily_runs", ["created_at"])

    op.create_table(
        "channel_state_pack_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_daily_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_pack_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("active_project_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("pending_review_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("readiness_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("provider_health_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("quota_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("evidence_summary", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("freshness_state", sa.String(length=40), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("state_hash", sa.Text(), nullable=False),
        _created_at(),
        sa.CheckConstraint("freshness_state in ('FRESH','STALE','UNKNOWN','NOT_REQUIRED')", name="ck_channel_state_pack_snapshots_freshness_state"),
        sa.CheckConstraint("confidence_level in ('HIGH','MEDIUM','LOW','UNKNOWN')", name="ck_channel_state_pack_snapshots_confidence_level"),
        sa.ForeignKeyConstraint(["channel_daily_run_id"], ["channel_daily_runs.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["context_pack_snapshot_id"], ["context_pack_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channel_state_pack_snapshots_daily_run_id", "channel_state_pack_snapshots", ["channel_daily_run_id"])
    op.create_index("ix_channel_state_pack_snapshots_company_id", "channel_state_pack_snapshots", ["company_id"])
    op.create_index("ix_channel_state_pack_snapshots_channel_workspace_id", "channel_state_pack_snapshots", ["channel_workspace_id"])
    op.create_index("ix_channel_state_pack_snapshots_policy_snapshot_id", "channel_state_pack_snapshots", ["policy_snapshot_id"])
    op.create_index("ix_channel_state_pack_snapshots_created_at", "channel_state_pack_snapshots", ["created_at"])

    op.create_table(
        "search_demand_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_source_type", sa.String(length=80), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("geo", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("lookback_window_days", sa.Integer(), nullable=True),
        sa.Column("search_volume_30d", sa.Integer(), nullable=True),
        sa.Column("relative_interest_index", sa.Numeric(10, 4), nullable=True),
        sa.Column("competition_index", sa.Numeric(10, 4), nullable=True),
        sa.Column("trending_velocity", sa.Numeric(10, 4), nullable=True),
        sa.Column("evidence_confidence", sa.String(length=40), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("metadata", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint("evidence_source_type in ('OFFICIAL_MANUAL','PAID_TOOL_CSV','GOOGLE_TRENDS_CSV','YOUTUBE_ANALYTICS','TIKTOK_CREATOR_SEARCH_INSIGHTS_MANUAL','INTERNAL_ANALYTICS','MOCK','MANUAL_RESEARCH')", name="ck_search_demand_evidence_source_type"),
        sa.CheckConstraint("platform in ('YOUTUBE','TIKTOK','FACEBOOK','INSTAGRAM','GOOGLE','GENERIC')", name="ck_search_demand_evidence_platform"),
        sa.CheckConstraint("evidence_confidence in ('HIGH','MEDIUM','LOW','UNKNOWN')", name="ck_search_demand_evidence_confidence"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_demand_evidence_company_id", "search_demand_evidence", ["company_id"])
    op.create_index("ix_search_demand_evidence_channel_workspace_id", "search_demand_evidence", ["channel_workspace_id"])
    op.create_index("ix_search_demand_evidence_source_type", "search_demand_evidence", ["evidence_source_type"])
    op.create_index("ix_search_demand_evidence_platform", "search_demand_evidence", ["platform"])
    op.create_index("ix_search_demand_evidence_created_at", "search_demand_evidence", ["created_at"])

    op.create_table(
        "search_intent_maps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_daily_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("daily_idea_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("primary_search_intent", sa.Text(), nullable=False),
        sa.Column("secondary_search_intents", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("keyword_cluster", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("audience_problem", sa.Text(), nullable=True),
        sa.Column("audience_language", sa.Text(), nullable=True),
        sa.Column("target_geo", sa.Text(), nullable=True),
        sa.Column("source_evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("demand_confidence", sa.String(length=40), nullable=False),
        sa.Column("competition_notes", sa.Text(), nullable=True),
        sa.Column("content_gap_notes", sa.Text(), nullable=True),
        _created_at(),
        sa.CheckConstraint("demand_confidence in ('HIGH','MEDIUM','LOW','UNKNOWN')", name="ck_search_intent_maps_demand_confidence"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["channel_daily_run_id"], ["channel_daily_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_intent_maps_company_id", "search_intent_maps", ["company_id"])
    op.create_index("ix_search_intent_maps_channel_workspace_id", "search_intent_maps", ["channel_workspace_id"])
    op.create_index("ix_search_intent_maps_daily_run_id", "search_intent_maps", ["channel_daily_run_id"])
    op.create_index("ix_search_intent_maps_daily_idea_decision_id", "search_intent_maps", ["daily_idea_decision_id"])
    op.create_index("ix_search_intent_maps_created_at", "search_intent_maps", ["created_at"])

    op.create_table(
        "audience_target_packs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_daily_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("daily_idea_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_audience", sa.Text(), nullable=False),
        sa.Column("audience_problem", sa.Text(), nullable=False),
        sa.Column("audience_language", sa.Text(), nullable=True),
        sa.Column("target_geo", sa.Text(), nullable=True),
        sa.Column("platform_surface_hypothesis", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("audience_rationale", sa.Text(), nullable=True),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        _created_at(),
        sa.CheckConstraint("confidence_level in ('HIGH','MEDIUM','LOW','UNKNOWN')", name="ck_audience_target_packs_confidence_level"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["channel_daily_run_id"], ["channel_daily_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audience_target_packs_company_id", "audience_target_packs", ["company_id"])
    op.create_index("ix_audience_target_packs_channel_workspace_id", "audience_target_packs", ["channel_workspace_id"])
    op.create_index("ix_audience_target_packs_daily_run_id", "audience_target_packs", ["channel_daily_run_id"])
    op.create_index("ix_audience_target_packs_daily_idea_decision_id", "audience_target_packs", ["daily_idea_decision_id"])
    op.create_index("ix_audience_target_packs_created_at", "audience_target_packs", ["created_at"])

    op.create_table(
        "idea_market_preflights",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_daily_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("daily_idea_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("search_intent_map_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("audience_target_pack_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("demand_score", sa.Numeric(10, 4), nullable=True),
        sa.Column("channel_fit_score", sa.Numeric(10, 4), nullable=True),
        sa.Column("policy_fit_state", sa.String(length=40), nullable=False),
        sa.Column("confidence_state", sa.String(length=40), nullable=False),
        sa.Column("evidence_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("decision", sa.String(length=40), nullable=False),
        _created_at(),
        sa.CheckConstraint("policy_fit_state in ('PASS','REVIEW_REQUIRED','BLOCK','UNKNOWN')", name="ck_idea_market_preflights_policy_fit_state"),
        sa.CheckConstraint("confidence_state in ('HIGH','MEDIUM','LOW','UNKNOWN')", name="ck_idea_market_preflights_confidence_state"),
        sa.CheckConstraint("decision in ('PASS','REVIEW_REQUIRED','BLOCK','SKIPPED')", name="ck_idea_market_preflights_decision"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["channel_daily_run_id"], ["channel_daily_runs.id"]),
        sa.ForeignKeyConstraint(["search_intent_map_id"], ["search_intent_maps.id"]),
        sa.ForeignKeyConstraint(["audience_target_pack_id"], ["audience_target_packs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_idea_market_preflights_company_id", "idea_market_preflights", ["company_id"])
    op.create_index("ix_idea_market_preflights_channel_workspace_id", "idea_market_preflights", ["channel_workspace_id"])
    op.create_index("ix_idea_market_preflights_daily_run_id", "idea_market_preflights", ["channel_daily_run_id"])
    op.create_index("ix_idea_market_preflights_daily_idea_decision_id", "idea_market_preflights", ["daily_idea_decision_id"])
    op.create_index("ix_idea_market_preflights_decision", "idea_market_preflights", ["decision"])
    op.create_index("ix_idea_market_preflights_created_at", "idea_market_preflights", ["created_at"])

    op.create_table(
        "daily_idea_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_daily_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_pack_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_state_pack_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("llm_run_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_status", sa.String(length=40), nullable=False),
        sa.Column("proposed_title", sa.Text(), nullable=False),
        sa.Column("proposed_angle", sa.Text(), nullable=True),
        sa.Column("proposed_format", sa.Text(), nullable=True),
        sa.Column("proposed_pillar", sa.Text(), nullable=True),
        sa.Column("proposed_series_key", sa.Text(), nullable=True),
        sa.Column("rationale", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        _created_at(),
        sa.CheckConstraint("decision_status in ('PROPOSED','ADMITTED','REVIEW_REQUIRED','BLOCKED','REJECTED','SKIPPED')", name="ck_daily_idea_decisions_status"),
        sa.CheckConstraint("confidence_level in ('HIGH','MEDIUM','LOW','UNKNOWN')", name="ck_daily_idea_decisions_confidence_level"),
        sa.ForeignKeyConstraint(["channel_daily_run_id"], ["channel_daily_runs.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["context_pack_snapshot_id"], ["context_pack_snapshots.id"]),
        sa.ForeignKeyConstraint(["channel_state_pack_snapshot_id"], ["channel_state_pack_snapshots.id"]),
        sa.ForeignKeyConstraint(["llm_run_snapshot_id"], ["llm_run_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_daily_idea_decisions_daily_run_id", "daily_idea_decisions", ["channel_daily_run_id"])
    op.create_index("ix_daily_idea_decisions_company_id", "daily_idea_decisions", ["company_id"])
    op.create_index("ix_daily_idea_decisions_channel_workspace_id", "daily_idea_decisions", ["channel_workspace_id"])
    op.create_index("ix_daily_idea_decisions_policy_snapshot_id", "daily_idea_decisions", ["policy_snapshot_id"])
    op.create_index("ix_daily_idea_decisions_context_pack_id", "daily_idea_decisions", ["context_pack_snapshot_id"])
    op.create_index("ix_daily_idea_decisions_llm_run_id", "daily_idea_decisions", ["llm_run_snapshot_id"])
    op.create_index("ix_daily_idea_decisions_status", "daily_idea_decisions", ["decision_status"])
    op.create_index("ix_daily_idea_decisions_created_at", "daily_idea_decisions", ["created_at"])

    op.create_table(
        "project_admission_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_daily_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("daily_idea_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idea_market_preflight_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("budget_gate_result", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("readiness_gate_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("decision", sa.String(length=40), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("admitted_video_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_artifact_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        _created_at(),
        sa.CheckConstraint("decision in ('ADMIT','REVIEW_REQUIRED','BLOCK','SKIP')", name="ck_project_admission_decisions_decision"),
        sa.ForeignKeyConstraint(["channel_daily_run_id"], ["channel_daily_runs.id"]),
        sa.ForeignKeyConstraint(["daily_idea_decision_id"], ["daily_idea_decisions.id"]),
        sa.ForeignKeyConstraint(["idea_market_preflight_id"], ["idea_market_preflights.id"]),
        sa.ForeignKeyConstraint(["admitted_video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_admission_decisions_daily_run_id", "project_admission_decisions", ["channel_daily_run_id"])
    op.create_index("ix_project_admission_decisions_daily_idea_id", "project_admission_decisions", ["daily_idea_decision_id"])
    op.create_index("ix_project_admission_decisions_preflight_id", "project_admission_decisions", ["idea_market_preflight_id"])
    op.create_index("ix_project_admission_decisions_decision", "project_admission_decisions", ["decision"])
    op.create_index("ix_project_admission_decisions_project_id", "project_admission_decisions", ["admitted_video_project_id"])
    op.create_index("ix_project_admission_decisions_created_at", "project_admission_decisions", ["created_at"])

    op.create_foreign_key("fk_channel_daily_runs_channel_state_pack_snapshot_id", "channel_daily_runs", "channel_state_pack_snapshots", ["channel_state_pack_snapshot_id"], ["id"])
    op.create_foreign_key("fk_channel_daily_runs_daily_idea_decision_id", "channel_daily_runs", "daily_idea_decisions", ["daily_idea_decision_id"], ["id"])
    op.create_foreign_key("fk_channel_daily_runs_project_admission_decision_id", "channel_daily_runs", "project_admission_decisions", ["project_admission_decision_id"], ["id"])
    op.create_foreign_key("fk_search_intent_maps_daily_idea_decision_id", "search_intent_maps", "daily_idea_decisions", ["daily_idea_decision_id"], ["id"])
    op.create_foreign_key("fk_audience_target_packs_daily_idea_decision_id", "audience_target_packs", "daily_idea_decisions", ["daily_idea_decision_id"], ["id"])
    op.create_foreign_key("fk_idea_market_preflights_daily_idea_decision_id", "idea_market_preflights", "daily_idea_decisions", ["daily_idea_decision_id"], ["id"])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_m5_immutable_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'M5 snapshots and decisions are immutable after creation';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_prevent_retrieval_plan_update
        BEFORE UPDATE ON retrieval_plan_snapshots
        FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update();

        CREATE TRIGGER trg_prevent_context_pack_update
        BEFORE UPDATE ON context_pack_snapshots
        FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update();

        CREATE TRIGGER trg_prevent_channel_state_pack_update
        BEFORE UPDATE ON channel_state_pack_snapshots
        FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update();

        CREATE TRIGGER trg_prevent_search_demand_evidence_update
        BEFORE UPDATE ON search_demand_evidence
        FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update();

        CREATE TRIGGER trg_prevent_search_intent_map_update
        BEFORE UPDATE ON search_intent_maps
        FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update();

        CREATE TRIGGER trg_prevent_audience_target_pack_update
        BEFORE UPDATE ON audience_target_packs
        FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update();

        CREATE TRIGGER trg_prevent_idea_market_preflight_update
        BEFORE UPDATE ON idea_market_preflights
        FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update();

        CREATE TRIGGER trg_prevent_daily_idea_decision_update
        BEFORE UPDATE ON daily_idea_decisions
        FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update();

        CREATE TRIGGER trg_prevent_project_admission_decision_update
        BEFORE UPDATE ON project_admission_decisions
        FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update();
        """
    )


def downgrade() -> None:
    for table, trigger in [
        ("project_admission_decisions", "trg_prevent_project_admission_decision_update"),
        ("daily_idea_decisions", "trg_prevent_daily_idea_decision_update"),
        ("idea_market_preflights", "trg_prevent_idea_market_preflight_update"),
        ("audience_target_packs", "trg_prevent_audience_target_pack_update"),
        ("search_intent_maps", "trg_prevent_search_intent_map_update"),
        ("search_demand_evidence", "trg_prevent_search_demand_evidence_update"),
        ("channel_state_pack_snapshots", "trg_prevent_channel_state_pack_update"),
        ("context_pack_snapshots", "trg_prevent_context_pack_update"),
        ("retrieval_plan_snapshots", "trg_prevent_retrieval_plan_update"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_m5_immutable_update()")

    op.drop_constraint("fk_idea_market_preflights_daily_idea_decision_id", "idea_market_preflights", type_="foreignkey")
    op.drop_constraint("fk_audience_target_packs_daily_idea_decision_id", "audience_target_packs", type_="foreignkey")
    op.drop_constraint("fk_search_intent_maps_daily_idea_decision_id", "search_intent_maps", type_="foreignkey")
    op.drop_constraint("fk_channel_daily_runs_project_admission_decision_id", "channel_daily_runs", type_="foreignkey")
    op.drop_constraint("fk_channel_daily_runs_daily_idea_decision_id", "channel_daily_runs", type_="foreignkey")
    op.drop_constraint("fk_channel_daily_runs_channel_state_pack_snapshot_id", "channel_daily_runs", type_="foreignkey")

    for index in [
        "ix_project_admission_decisions_created_at",
        "ix_project_admission_decisions_project_id",
        "ix_project_admission_decisions_decision",
        "ix_project_admission_decisions_preflight_id",
        "ix_project_admission_decisions_daily_idea_id",
        "ix_project_admission_decisions_daily_run_id",
    ]:
        op.drop_index(index, table_name="project_admission_decisions")
    op.drop_table("project_admission_decisions")

    for index in [
        "ix_daily_idea_decisions_created_at",
        "ix_daily_idea_decisions_status",
        "ix_daily_idea_decisions_llm_run_id",
        "ix_daily_idea_decisions_context_pack_id",
        "ix_daily_idea_decisions_policy_snapshot_id",
        "ix_daily_idea_decisions_channel_workspace_id",
        "ix_daily_idea_decisions_company_id",
        "ix_daily_idea_decisions_daily_run_id",
    ]:
        op.drop_index(index, table_name="daily_idea_decisions")
    op.drop_table("daily_idea_decisions")

    for index in [
        "ix_idea_market_preflights_created_at",
        "ix_idea_market_preflights_decision",
        "ix_idea_market_preflights_daily_idea_decision_id",
        "ix_idea_market_preflights_daily_run_id",
        "ix_idea_market_preflights_channel_workspace_id",
        "ix_idea_market_preflights_company_id",
    ]:
        op.drop_index(index, table_name="idea_market_preflights")
    op.drop_table("idea_market_preflights")

    for index in [
        "ix_audience_target_packs_created_at",
        "ix_audience_target_packs_daily_idea_decision_id",
        "ix_audience_target_packs_daily_run_id",
        "ix_audience_target_packs_channel_workspace_id",
        "ix_audience_target_packs_company_id",
    ]:
        op.drop_index(index, table_name="audience_target_packs")
    op.drop_table("audience_target_packs")

    for index in [
        "ix_search_intent_maps_created_at",
        "ix_search_intent_maps_daily_idea_decision_id",
        "ix_search_intent_maps_daily_run_id",
        "ix_search_intent_maps_channel_workspace_id",
        "ix_search_intent_maps_company_id",
    ]:
        op.drop_index(index, table_name="search_intent_maps")
    op.drop_table("search_intent_maps")

    for index in [
        "ix_search_demand_evidence_created_at",
        "ix_search_demand_evidence_platform",
        "ix_search_demand_evidence_source_type",
        "ix_search_demand_evidence_channel_workspace_id",
        "ix_search_demand_evidence_company_id",
    ]:
        op.drop_index(index, table_name="search_demand_evidence")
    op.drop_table("search_demand_evidence")

    for index in [
        "ix_channel_state_pack_snapshots_created_at",
        "ix_channel_state_pack_snapshots_policy_snapshot_id",
        "ix_channel_state_pack_snapshots_channel_workspace_id",
        "ix_channel_state_pack_snapshots_company_id",
        "ix_channel_state_pack_snapshots_daily_run_id",
    ]:
        op.drop_index(index, table_name="channel_state_pack_snapshots")
    op.drop_table("channel_state_pack_snapshots")

    for index in [
        "ix_channel_daily_runs_created_at",
        "ix_channel_daily_runs_status",
        "ix_channel_daily_runs_run_date",
        "ix_channel_daily_runs_slot_id",
        "ix_channel_daily_runs_policy_snapshot_id",
        "ix_channel_daily_runs_channel_workspace_id",
        "ix_channel_daily_runs_company_id",
    ]:
        op.drop_index(index, table_name="channel_daily_runs")
    op.drop_table("channel_daily_runs")

    for index in [
        "ix_context_pack_snapshots_created_at",
        "ix_context_pack_snapshots_policy_snapshot_id",
        "ix_context_pack_snapshots_channel_workspace_id",
        "ix_context_pack_snapshots_company_id",
        "ix_context_pack_snapshots_purpose",
        "ix_context_pack_snapshots_plan_id",
    ]:
        op.drop_index(index, table_name="context_pack_snapshots")
    op.drop_table("context_pack_snapshots")

    for index in [
        "ix_retrieval_plan_snapshots_created_at",
        "ix_retrieval_plan_snapshots_policy_snapshot_id",
        "ix_retrieval_plan_snapshots_channel_workspace_id",
        "ix_retrieval_plan_snapshots_company_id",
        "ix_retrieval_plan_snapshots_purpose",
    ]:
        op.drop_index(index, table_name="retrieval_plan_snapshots")
    op.drop_table("retrieval_plan_snapshots")

    for index in [
        "ix_editorial_calendar_slots_created_at",
        "ix_editorial_calendar_slots_status",
        "ix_editorial_calendar_slots_slot_date",
        "ix_editorial_calendar_slots_policy_snapshot_id",
        "ix_editorial_calendar_slots_channel_workspace_id",
        "ix_editorial_calendar_slots_company_id",
    ]:
        op.drop_index(index, table_name="editorial_calendar_slots")
    op.drop_table("editorial_calendar_slots")
