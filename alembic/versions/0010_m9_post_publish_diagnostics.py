"""M9 post-publish diagnostics and recovery proposals

Revision ID: 0010_m9_post_publish_diagnostics
Revises: 0009_m8_analytics_sync
Create Date: 2026-06-26 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0010_m9_post_publish_diagnostics"
down_revision: str | None = "0009_m8_analytics_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

PLATFORM_CHECK = "platform in ('YOUTUBE','YOUTUBE_SHORTS','TIKTOK','FACEBOOK','INSTAGRAM','GENERIC')"
OBSERVATION_WINDOW_CHECK = "observation_window in ('T_PLUS_1H','T_PLUS_6H','T_PLUS_24H','T_PLUS_48H','T_PLUS_7D','CUSTOM')"
CONFIDENCE_CHECK = "confidence_level in ('HIGH','MEDIUM','LOW','UNKNOWN')"
SEVERITY_CHECK = "severity in ('INFO','LOW','MEDIUM','HIGH','CRITICAL')"
HEALTH_STATE_CHECK = (
    "health_state in ('HEALTHY','WATCH','NO_VIEW_RISK','UNDERPERFORMING',"
    "'POLICY_REVIEW_REQUIRED','INSUFFICIENT_DATA','UNKNOWN')"
)
PRIMARY_STATUS_CHECK = (
    "primary_status in ('HEALTHY','WATCH','NO_VIEW_RISK','UNDERPERFORMING',"
    "'POLICY_REVIEW_REQUIRED','INSUFFICIENT_DATA','UNKNOWN')"
)


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.execute("alter table manual_action_queue drop constraint if exists ck_manual_action_queue_ck_manual_action_queue_action_type")
    op.execute("alter table manual_action_queue drop constraint if exists ck_manual_action_queue_action_type")
    op.create_check_constraint(
        "ck_manual_action_queue_m9_action_type",
        "manual_action_queue",
        "action_type in ('CHECK_CREDENTIAL','UPDATE_CREDENTIAL_REF','REVIEW_COST_LIMIT','REVIEW_QUOTA','INVESTIGATE_PROVIDER',"
        "'REPLAY_DEAD_LETTER','RESOLVE_INCIDENT','REVIEW_TITLE_THUMBNAIL_VARIANT','REVIEW_DISCLOSURE',"
        "'REVIEW_RIGHTS_EVIDENCE','REVIEW_RETENTION_DROP_SECTION','WAIT_FOR_NEXT_WINDOW','OTHER')",
    )

    op.create_table(
        "post_publish_observation_windows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_window", sa.String(length=40), nullable=False),
        sa.Column("window_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(PLATFORM_CHECK, name="ck_post_publish_windows_platform"),
        sa.CheckConstraint(OBSERVATION_WINDOW_CHECK, name="ck_post_publish_windows_observation_window"),
        sa.CheckConstraint("state in ('PENDING','READY','COMPLETED','SKIPPED','BLOCKED')", name="ck_post_publish_windows_state"),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uploaded_video_id", "observation_window", name="uq_post_publish_windows_video_window"),
    )
    op.create_index("ix_post_publish_windows_uploaded_video_id", "post_publish_observation_windows", ["uploaded_video_id"])
    op.create_index("ix_post_publish_windows_state", "post_publish_observation_windows", ["state"])
    op.create_index("ix_post_publish_windows_expected_check_at", "post_publish_observation_windows", ["expected_check_at"])
    op.create_index("ix_post_publish_windows_platform", "post_publish_observation_windows", ["platform"])

    op.create_table(
        "diagnostic_taxonomy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taxonomy_key", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("taxonomy_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        _created_at(),
        sa.CheckConstraint("status in ('ACTIVE','DISABLED','DEPRECATED')", name="ck_diagnostic_taxonomy_versions_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("taxonomy_key", "version", name="uq_diagnostic_taxonomy_versions_key_version"),
    )
    op.create_index("ix_diagnostic_taxonomy_versions_key", "diagnostic_taxonomy_versions", ["taxonomy_key"])
    op.create_index("ix_diagnostic_taxonomy_versions_status", "diagnostic_taxonomy_versions", ["status"])

    op.create_table(
        "post_publish_health_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("observation_window", sa.String(length=40), nullable=False),
        sa.Column("analytics_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_video_metrics_summary_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("retention_curve_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("traffic_source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("engagement_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_state", sa.String(length=40), nullable=False),
        sa.Column("health_state", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("do_not_do", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint(PLATFORM_CHECK, name="ck_post_publish_health_runs_platform"),
        sa.CheckConstraint(OBSERVATION_WINDOW_CHECK, name="ck_post_publish_health_runs_observation_window"),
        sa.CheckConstraint("run_state in ('PENDING','COMPLETED','BLOCKED','INSUFFICIENT_DATA','FAILED')", name="ck_post_publish_health_runs_run_state"),
        sa.CheckConstraint(HEALTH_STATE_CHECK, name="ck_post_publish_health_runs_health_state"),
        sa.CheckConstraint(SEVERITY_CHECK, name="ck_post_publish_health_runs_severity"),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_post_publish_health_runs_confidence"),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["policy_snapshot_id"], ["compiled_channel_policy_snapshots.id"]),
        sa.ForeignKeyConstraint(["analytics_snapshot_id"], ["analytics_snapshots.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_metrics_summary_id"], ["uploaded_video_metrics_summaries.id"]),
        sa.ForeignKeyConstraint(["retention_curve_snapshot_id"], ["retention_curve_snapshots.id"]),
        sa.ForeignKeyConstraint(["traffic_source_snapshot_id"], ["traffic_source_snapshots.id"]),
        sa.ForeignKeyConstraint(["engagement_snapshot_id"], ["engagement_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_post_publish_health_runs_uploaded_video_id", "post_publish_health_runs", ["uploaded_video_id"])
    op.create_index("ix_post_publish_health_runs_company_id", "post_publish_health_runs", ["company_id"])
    op.create_index("ix_post_publish_health_runs_channel_workspace_id", "post_publish_health_runs", ["channel_workspace_id"])
    op.create_index("ix_post_publish_health_runs_video_project_id", "post_publish_health_runs", ["video_project_id"])
    op.create_index("ix_post_publish_health_runs_observation_window", "post_publish_health_runs", ["observation_window"])
    op.create_index("ix_post_publish_health_runs_run_state", "post_publish_health_runs", ["run_state"])
    op.create_index("ix_post_publish_health_runs_health_state", "post_publish_health_runs", ["health_state"])
    op.create_index("ix_post_publish_health_runs_created_at", "post_publish_health_runs", ["created_at"])

    op.create_table(
        "no_view_diagnostic_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_publish_health_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytics_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_video_metrics_summary_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("observation_window", sa.String(length=40), nullable=False),
        sa.Column("diagnostic_state", sa.String(length=40), nullable=False),
        sa.Column("views", sa.Float(), nullable=True),
        sa.Column("impressions", sa.Float(), nullable=True),
        sa.Column("metric_availability", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("evidence_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        _created_at(),
        sa.CheckConstraint(OBSERVATION_WINDOW_CHECK, name="ck_no_view_diagnostic_runs_observation_window"),
        sa.CheckConstraint(
            "diagnostic_state in ('NOT_APPLICABLE','INSUFFICIENT_DATA','NO_VIEW_RISK','LOW_IMPRESSIONS','DATA_UNAVAILABLE','HEALTHY','UNKNOWN')",
            name="ck_no_view_diagnostic_runs_state",
        ),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_no_view_diagnostic_runs_confidence"),
        sa.ForeignKeyConstraint(["post_publish_health_run_id"], ["post_publish_health_runs.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["analytics_snapshot_id"], ["analytics_snapshots.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_metrics_summary_id"], ["uploaded_video_metrics_summaries.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_no_view_diagnostic_runs_health_run_id", "no_view_diagnostic_runs", ["post_publish_health_run_id"])
    op.create_index("ix_no_view_diagnostic_runs_uploaded_video_id", "no_view_diagnostic_runs", ["uploaded_video_id"])
    op.create_index("ix_no_view_diagnostic_runs_state", "no_view_diagnostic_runs", ["diagnostic_state"])

    op.create_table(
        "packaging_diagnostic_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_publish_health_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytics_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("observation_window", sa.String(length=40), nullable=False),
        sa.Column("diagnostic_state", sa.String(length=40), nullable=False),
        sa.Column("impressions", sa.Float(), nullable=True),
        sa.Column("click_through_rate", sa.Float(), nullable=True),
        sa.Column("views", sa.Float(), nullable=True),
        sa.Column("evidence_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        _created_at(),
        sa.CheckConstraint(OBSERVATION_WINDOW_CHECK, name="ck_packaging_diagnostic_runs_observation_window"),
        sa.CheckConstraint(
            "diagnostic_state in ('NOT_APPLICABLE','INSUFFICIENT_DATA','LOW_CTR','WATCH','HEALTHY','UNKNOWN')",
            name="ck_packaging_diagnostic_runs_state",
        ),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_packaging_diagnostic_runs_confidence"),
        sa.ForeignKeyConstraint(["post_publish_health_run_id"], ["post_publish_health_runs.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["analytics_snapshot_id"], ["analytics_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_packaging_diagnostic_runs_health_run_id", "packaging_diagnostic_runs", ["post_publish_health_run_id"])
    op.create_index("ix_packaging_diagnostic_runs_uploaded_video_id", "packaging_diagnostic_runs", ["uploaded_video_id"])
    op.create_index("ix_packaging_diagnostic_runs_state", "packaging_diagnostic_runs", ["diagnostic_state"])

    op.create_table(
        "retention_diagnostic_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_publish_health_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytics_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("retention_curve_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("observation_window", sa.String(length=40), nullable=False),
        sa.Column("diagnostic_state", sa.String(length=40), nullable=False),
        sa.Column("average_view_duration_seconds", sa.Float(), nullable=True),
        sa.Column("average_view_percentage", sa.Float(), nullable=True),
        sa.Column("evidence_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("scene_alignment", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        _created_at(),
        sa.CheckConstraint(OBSERVATION_WINDOW_CHECK, name="ck_retention_diagnostic_runs_observation_window"),
        sa.CheckConstraint(
            "diagnostic_state in ('NOT_APPLICABLE','INSUFFICIENT_DATA','EARLY_DROP','MID_VIDEO_DROP','WATCH','HEALTHY','UNKNOWN')",
            name="ck_retention_diagnostic_runs_state",
        ),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_retention_diagnostic_runs_confidence"),
        sa.ForeignKeyConstraint(["post_publish_health_run_id"], ["post_publish_health_runs.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["analytics_snapshot_id"], ["analytics_snapshots.id"]),
        sa.ForeignKeyConstraint(["retention_curve_snapshot_id"], ["retention_curve_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retention_diagnostic_runs_health_run_id", "retention_diagnostic_runs", ["post_publish_health_run_id"])
    op.create_index("ix_retention_diagnostic_runs_uploaded_video_id", "retention_diagnostic_runs", ["uploaded_video_id"])
    op.create_index("ix_retention_diagnostic_runs_state", "retention_diagnostic_runs", ["diagnostic_state"])

    op.create_table(
        "engagement_diagnostic_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_publish_health_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytics_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("engagement_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("observation_window", sa.String(length=40), nullable=False),
        sa.Column("diagnostic_state", sa.String(length=40), nullable=False),
        sa.Column("engagement_metrics", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("evidence_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        _created_at(),
        sa.CheckConstraint(OBSERVATION_WINDOW_CHECK, name="ck_engagement_diagnostic_runs_observation_window"),
        sa.CheckConstraint(
            "diagnostic_state in ('NOT_APPLICABLE','INSUFFICIENT_DATA','LOW_ENGAGEMENT','WATCH','HEALTHY','UNKNOWN')",
            name="ck_engagement_diagnostic_runs_state",
        ),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_engagement_diagnostic_runs_confidence"),
        sa.ForeignKeyConstraint(["post_publish_health_run_id"], ["post_publish_health_runs.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["analytics_snapshot_id"], ["analytics_snapshots.id"]),
        sa.ForeignKeyConstraint(["engagement_snapshot_id"], ["engagement_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_engagement_diagnostic_runs_health_run_id", "engagement_diagnostic_runs", ["post_publish_health_run_id"])
    op.create_index("ix_engagement_diagnostic_runs_uploaded_video_id", "engagement_diagnostic_runs", ["uploaded_video_id"])
    op.create_index("ix_engagement_diagnostic_runs_state", "engagement_diagnostic_runs", ["diagnostic_state"])

    op.create_table(
        "policy_rights_diagnostic_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_publish_health_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_window", sa.String(length=40), nullable=False),
        sa.Column("diagnostic_state", sa.String(length=40), nullable=False),
        sa.Column("source_manifest_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rights_envelope_ref", sa.Text(), nullable=True),
        sa.Column("actual_disclosures", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("evidence_blob", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("reason_codes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        _created_at(),
        sa.CheckConstraint(OBSERVATION_WINDOW_CHECK, name="ck_policy_rights_diagnostic_runs_observation_window"),
        sa.CheckConstraint(
            "diagnostic_state in ('NOT_APPLICABLE','REVIEW_REQUIRED','BLOCKED','PASS','UNKNOWN')",
            name="ck_policy_rights_diagnostic_runs_state",
        ),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_policy_rights_diagnostic_runs_confidence"),
        sa.ForeignKeyConstraint(["post_publish_health_run_id"], ["post_publish_health_runs.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["source_manifest_snapshot_id"], ["source_manifest_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policy_rights_diagnostic_runs_health_run_id", "policy_rights_diagnostic_runs", ["post_publish_health_run_id"])
    op.create_index("ix_policy_rights_diagnostic_runs_uploaded_video_id", "policy_rights_diagnostic_runs", ["uploaded_video_id"])
    op.create_index("ix_policy_rights_diagnostic_runs_state", "policy_rights_diagnostic_runs", ["diagnostic_state"])

    op.create_table(
        "failure_trace_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_publish_health_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("platform_video_id", sa.Text(), nullable=False),
        sa.Column("observation_window", sa.String(length=40), nullable=False),
        sa.Column("primary_status", sa.String(length=40), nullable=False),
        sa.Column("primary_suspected_cause", sa.String(length=120), nullable=True),
        sa.Column("secondary_suspected_causes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("confidence_level", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("evidence_plain_text", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("operator_report", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("do_not_do", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint(PLATFORM_CHECK, name="ck_failure_trace_reports_platform"),
        sa.CheckConstraint(OBSERVATION_WINDOW_CHECK, name="ck_failure_trace_reports_observation_window"),
        sa.CheckConstraint(PRIMARY_STATUS_CHECK, name="ck_failure_trace_reports_primary_status"),
        sa.CheckConstraint(SEVERITY_CHECK, name="ck_failure_trace_reports_severity"),
        sa.CheckConstraint(CONFIDENCE_CHECK, name="ck_failure_trace_reports_confidence"),
        sa.ForeignKeyConstraint(["post_publish_health_run_id"], ["post_publish_health_runs.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_failure_trace_reports_health_run_id", "failure_trace_reports", ["post_publish_health_run_id"])
    op.create_index("ix_failure_trace_reports_uploaded_video_id", "failure_trace_reports", ["uploaded_video_id"])
    op.create_index("ix_failure_trace_reports_video_project_id", "failure_trace_reports", ["video_project_id"])
    op.create_index("ix_failure_trace_reports_primary_status", "failure_trace_reports", ["primary_status"])
    op.create_index("ix_failure_trace_reports_created_at", "failure_trace_reports", ["created_at"])

    op.create_table(
        "recovery_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("failure_trace_report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_type", sa.String(length=60), nullable=False),
        sa.Column("proposal_state", sa.String(length=40), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("recommended_actions", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("do_not_do", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("risk_level", sa.String(length=40), nullable=False),
        sa.Column("requires_human_approval", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "proposal_type in ('WAIT_AND_MONITOR','REVIEW_TITLE_THUMBNAIL','REVIEW_HOOK','REVIEW_RETENTION_SECTION',"
            "'REVIEW_RIGHTS_DISCLOSURE','REVIEW_SOURCE_QUALITY','CREATE_FUTURE_VARIANT','NO_ACTION')",
            name="ck_recovery_proposals_type",
        ),
        sa.CheckConstraint(
            "proposal_state in ('PROPOSED','ACCEPTED','REJECTED','SUPERSEDED','CANCELLED')",
            name="ck_recovery_proposals_state",
        ),
        sa.CheckConstraint("risk_level in ('LOW','MEDIUM','HIGH','UNKNOWN')", name="ck_recovery_proposals_risk_level"),
        sa.CheckConstraint("requires_human_approval = true", name="ck_recovery_proposals_human_approval"),
        sa.ForeignKeyConstraint(["failure_trace_report_id"], ["failure_trace_reports.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recovery_proposals_report_id", "recovery_proposals", ["failure_trace_report_id"])
    op.create_index("ix_recovery_proposals_uploaded_video_id", "recovery_proposals", ["uploaded_video_id"])
    op.create_index("ix_recovery_proposals_video_project_id", "recovery_proposals", ["video_project_id"])
    op.create_index("ix_recovery_proposals_type", "recovery_proposals", ["proposal_type"])
    op.create_index("ix_recovery_proposals_state", "recovery_proposals", ["proposal_state"])
    op.create_index("ix_recovery_proposals_created_at", "recovery_proposals", ["created_at"])


def downgrade() -> None:
    for table, indexes in [
        (
            "recovery_proposals",
            [
                "ix_recovery_proposals_created_at",
                "ix_recovery_proposals_state",
                "ix_recovery_proposals_type",
                "ix_recovery_proposals_video_project_id",
                "ix_recovery_proposals_uploaded_video_id",
                "ix_recovery_proposals_report_id",
            ],
        ),
        (
            "failure_trace_reports",
            [
                "ix_failure_trace_reports_created_at",
                "ix_failure_trace_reports_primary_status",
                "ix_failure_trace_reports_video_project_id",
                "ix_failure_trace_reports_uploaded_video_id",
                "ix_failure_trace_reports_health_run_id",
            ],
        ),
        (
            "policy_rights_diagnostic_runs",
            [
                "ix_policy_rights_diagnostic_runs_state",
                "ix_policy_rights_diagnostic_runs_uploaded_video_id",
                "ix_policy_rights_diagnostic_runs_health_run_id",
            ],
        ),
        (
            "engagement_diagnostic_runs",
            [
                "ix_engagement_diagnostic_runs_state",
                "ix_engagement_diagnostic_runs_uploaded_video_id",
                "ix_engagement_diagnostic_runs_health_run_id",
            ],
        ),
        (
            "retention_diagnostic_runs",
            [
                "ix_retention_diagnostic_runs_state",
                "ix_retention_diagnostic_runs_uploaded_video_id",
                "ix_retention_diagnostic_runs_health_run_id",
            ],
        ),
        (
            "packaging_diagnostic_runs",
            [
                "ix_packaging_diagnostic_runs_state",
                "ix_packaging_diagnostic_runs_uploaded_video_id",
                "ix_packaging_diagnostic_runs_health_run_id",
            ],
        ),
        (
            "no_view_diagnostic_runs",
            [
                "ix_no_view_diagnostic_runs_state",
                "ix_no_view_diagnostic_runs_uploaded_video_id",
                "ix_no_view_diagnostic_runs_health_run_id",
            ],
        ),
        (
            "post_publish_health_runs",
            [
                "ix_post_publish_health_runs_created_at",
                "ix_post_publish_health_runs_health_state",
                "ix_post_publish_health_runs_run_state",
                "ix_post_publish_health_runs_observation_window",
                "ix_post_publish_health_runs_video_project_id",
                "ix_post_publish_health_runs_channel_workspace_id",
                "ix_post_publish_health_runs_company_id",
                "ix_post_publish_health_runs_uploaded_video_id",
            ],
        ),
        (
            "diagnostic_taxonomy_versions",
            [
                "ix_diagnostic_taxonomy_versions_status",
                "ix_diagnostic_taxonomy_versions_key",
            ],
        ),
        (
            "post_publish_observation_windows",
            [
                "ix_post_publish_windows_platform",
                "ix_post_publish_windows_expected_check_at",
                "ix_post_publish_windows_state",
                "ix_post_publish_windows_uploaded_video_id",
            ],
        ),
    ]:
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
    op.execute("alter table manual_action_queue drop constraint if exists ck_manual_action_queue_ck_manual_action_queue_m9_action_type")
    op.execute("alter table manual_action_queue drop constraint if exists ck_manual_action_queue_m9_action_type")
    op.create_check_constraint(
        "ck_manual_action_queue_action_type",
        "manual_action_queue",
        "action_type in ('CHECK_CREDENTIAL','UPDATE_CREDENTIAL_REF','REVIEW_COST_LIMIT','REVIEW_QUOTA','INVESTIGATE_PROVIDER',"
        "'REPLAY_DEAD_LETTER','RESOLVE_INCIDENT','OTHER')",
    )
