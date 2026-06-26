"""M10.1 LLM router and derivative funnel backend foundation

Revision ID: 0012_m10_1_router_derivatives
Revises: 0011_m10_learning_review_queue
Create Date: 2026-06-26 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0012_m10_1_router_derivatives"
down_revision: str | None = "0011_m10_learning_review_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

FALLBACK_LEVEL_CHECK = "fallback_level in ('PRIMARY','FALLBACK','PREMIUM','EMERGENCY','BACKUP')"
LLM_ROUTE_STATUS_CHECK = "status in ('SUCCESS','FAILED','SKIPPED','BLOCKED')"
DERIVATIVE_TYPE_CHECK = "derivative_type in ('SHORT','CLIP','FOLLOW_UP_LONG','COMPILATION','UPDATE','TRANSLATION','OTHER')"
SHORT_STATE_CHECK = "candidate_state in ('GENERATED','SCORED','SELECTED_FOR_RENDER','REJECTED','NEEDS_REWRITE','BLOCKED')"
CROP_STRATEGY_CHECK = "crop_strategy in ('VERTICAL_9_16','CENTER_CROP','SMART_CROP','TEMPLATE_CARD','DIAGRAM_CARD')"
VISUAL_SOURCE_CHECK = "visual_source in ('PARENT_HERO_REUSE','PARENT_SCENE_REUSE','TEMPLATE_CARD','DIAGRAM_CARD','SCREENSHOT','NEW_AI_HERO_REQUIRED','UNKNOWN')"
RENDER_STATE_CHECK = "render_state in ('PLANNED','BLOCKED','READY_FOR_M10_2_RENDER','CANCELLED')"
TARGET_PLATFORM_CHECK = "target_platform in ('YOUTUBE_SHORTS','TIKTOK','FACEBOOK_REELS')"
VOICE_SOURCE_CHECK = "voice_source in ('REUSE_PARENT_AUDIO','NEW_SHORT_VOICE_REQUIRED','MOCK_ONLY')"
PROMOTE_STATE_CHECK = "state in ('GENERATED','NEEDS_MORE_EVIDENCE','READY_FOR_HUMAN_REVIEW','REJECTED','CANCELLED')"
WATCH_HOUR_CHECK = "expected_watch_hour_potential in ('LOW','MEDIUM','HIGH','UNKNOWN')"
ARTIFACT_TYPE_CHECK = (
    "artifact_type in ('SCRIPT_BLOCK','RESEARCH_PACKET','DIAGRAM_TEMPLATE','MOTION_TEMPLATE','STOCK_CLIP',"
    "'AI_VIDEO_CLIP','MUSIC_BED','SFX','VOICE_LINE','CAPTION_STYLE','PROMPT_PREFIX','THUMBNAIL_TEMPLATE','OTHER')"
)
REUSE_SCOPE_CHECK = "reuse_scope in ('CHANNEL','SERIES','COMPANY','PROJECT_ONLY')"
REUSABLE_STATE_CHECK = "state in ('ACTIVE','NEEDS_REVIEW','RETIRED','BLOCKED')"
ORIGINALITY_RESULT_CHECK = "result in ('PASS','REVIEW_REQUIRED','BLOCK')"
ORIGINALITY_BUDGET_RESULT_CHECK = "result in ('OK','REVIEW_REQUIRED','BLOCK')"
RELEASE_STATE_CHECK = "release_state in ('DRAFT','READY_FOR_HUMAN_REVIEW','BLOCKED','CANCELLED')"
FUNNEL_STATE_CHECK = "package_state in ('DRAFT','READY_FOR_HUMAN_REVIEW','READY_FOR_UPLOAD_TASKS','BLOCKED','CANCELLED')"
UPLOAD_PLATFORM_CHECK = "platform in ('YOUTUBE_LONG','YOUTUBE_SHORTS','TIKTOK','FACEBOOK_REELS')"
CTA_TYPE_CHECK = "cta_type in ('NONE','SEARCH_YOUTUBE','BRAND_CTA','LINK_IN_BIO','PINNED_COMMENT')"
MUSIC_POLICY_CHECK = "music_policy in ('SAFE_MODE','PLATFORM_NATIVE_MODE','NO_MUSIC_MODE')"
UPLOAD_CARD_STATE_CHECK = "card_state in ('DRAFT','READY','BLOCKED','USED','CANCELLED')"
HUMAN_TASK_STATE_CHECK = "task_state in ('READY','UPLOADED','NEEDS_FIX','SKIPPED','CANCELLED')"


def _jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _uuid_fk(name: str, table: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "llm_router_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_key", sa.String(length=160), nullable=False),
        sa.Column("provider_key", sa.String(length=80), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("real_execution_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("default_timeout_seconds", sa.Integer(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("provider_key = 'OLLAMA'", name="ck_llm_router_profiles_provider_key"),
        sa.CheckConstraint("default_timeout_seconds > 0", name="ck_llm_router_profiles_timeout_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_key", name="uq_llm_router_profiles_profile_key"),
    )
    op.create_index("ix_llm_router_profiles_provider_key", "llm_router_profiles", ["provider_key"])

    op.create_table(
        "llm_router_lanes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("router_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lane_name", sa.String(length=160), nullable=False),
        sa.Column("lane_description", sa.Text(), nullable=False),
        sa.Column("allowed_task_types", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("primary_model", sa.String(length=160), nullable=False),
        sa.Column("fallback_models", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("premium_model", sa.String(length=160), nullable=True),
        sa.Column("emergency_model", sa.String(length=160), nullable=True),
        sa.Column("backup_model", sa.String(length=160), nullable=True),
        sa.Column("max_input_tokens", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_tier", sa.String(length=40), nullable=False),
        sa.Column("latency_tier", sa.String(length=40), nullable=False),
        sa.Column("critical_path_allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("requires_human_approval_for_premium", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("route_priority", sa.Integer(), nullable=False),
        sa.Column("real_execution_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("jsonb_typeof(allowed_task_types) = 'array'", name="ck_llm_router_lanes_allowed_tasks_array"),
        sa.CheckConstraint("jsonb_typeof(fallback_models) = 'array'", name="ck_llm_router_lanes_fallback_models_array"),
        sa.CheckConstraint("route_priority > 0", name="ck_llm_router_lanes_priority_positive"),
        sa.ForeignKeyConstraint(["router_profile_id"], ["llm_router_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("router_profile_id", "lane_name", name="uq_llm_router_lanes_profile_lane"),
    )
    op.create_index("ix_llm_router_lanes_lane_name", "llm_router_lanes", ["lane_name"])
    op.create_index("ix_llm_router_lanes_profile_id", "llm_router_lanes", ["router_profile_id"])

    op.create_table(
        "llm_model_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=80), nullable=False),
        sa.Column("model_id", sa.String(length=160), nullable=False),
        sa.Column("model_role", sa.String(length=80), nullable=False),
        sa.Column("lane_names", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("critical_path_allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("provider_key = 'OLLAMA'", name="ck_llm_model_profiles_provider_key"),
        sa.CheckConstraint("jsonb_typeof(lane_names) = 'array'", name="ck_llm_model_profiles_lane_names_array"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key", "model_id", name="uq_llm_model_profiles_provider_model"),
    )
    op.create_index("ix_llm_model_profiles_model_id", "llm_model_profiles", ["model_id"])
    op.create_index("ix_llm_model_profiles_provider_key", "llm_model_profiles", ["provider_key"])

    op.create_table(
        "llm_route_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("router_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lane_name", sa.String(length=160), nullable=False),
        sa.Column("requested_task_type", sa.String(length=160), nullable=True),
        sa.Column("selected_model", sa.String(length=160), nullable=False),
        sa.Column("fallback_level", sa.String(length=40), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("response_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("prompt_eval_count", sa.Integer(), nullable=True),
        sa.Column("eval_count", sa.Integer(), nullable=True),
        sa.Column("total_duration_ms", sa.Integer(), nullable=True),
        sa.Column("load_duration_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_eval_duration_ms", sa.Integer(), nullable=True),
        sa.Column("eval_duration_ms", sa.Integer(), nullable=True),
        sa.Column("provider_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("llm_run_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        _created_at(),
        sa.CheckConstraint(FALLBACK_LEVEL_CHECK, name="ck_llm_route_attempts_fallback_level"),
        sa.CheckConstraint(LLM_ROUTE_STATUS_CHECK, name="ck_llm_route_attempts_status"),
        sa.ForeignKeyConstraint(["router_profile_id"], ["llm_router_profiles.id"]),
        sa.ForeignKeyConstraint(["provider_attempt_id"], ["provider_attempts.id"]),
        sa.ForeignKeyConstraint(["llm_run_snapshot_id"], ["llm_run_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_route_attempts_profile_id", "llm_route_attempts", ["router_profile_id"])
    op.create_index("ix_llm_route_attempts_lane_name", "llm_route_attempts", ["lane_name"])
    op.create_index("ix_llm_route_attempts_status", "llm_route_attempts", ["status"])
    op.create_index("ix_llm_route_attempts_created_at", "llm_route_attempts", ["created_at"])

    op.create_table(
        "content_derivative_graph_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid_fk("parent_video_project_id", "video_projects"),
        _uuid_fk("parent_uploaded_video_id", "uploaded_videos"),
        _uuid_fk("derivative_video_project_id", "video_projects"),
        _uuid_fk("derivative_uploaded_video_id", "uploaded_videos"),
        sa.Column("derivative_type", sa.String(length=40), nullable=False),
        sa.Column("transformation_summary", sa.Text(), nullable=False),
        sa.Column("new_value_added", sa.Text(), nullable=True),
        sa.Column("originality_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("reused_runtime_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("publish_allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("policy_risk_level", sa.String(length=40), nullable=True),
        sa.Column("rights_risk_level", sa.String(length=40), nullable=True),
        sa.Column("source_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(DERIVATIVE_TYPE_CHECK, name="ck_content_derivative_edges_type"),
        sa.CheckConstraint("jsonb_typeof(source_refs) = 'array'", name="ck_content_derivative_edges_source_refs_array"),
        sa.CheckConstraint("jsonb_typeof(technical_appendix) = 'object'", name="ck_content_derivative_edges_appendix_object"),
        sa.CheckConstraint(
            "publish_allowed = false or derivative_type <> 'COMPILATION' or coalesce(new_value_added, '') <> ''",
            name="ck_content_derivative_edges_compilation_needs_value",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["parent_video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["parent_uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["derivative_video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["derivative_uploaded_video_id"], ["uploaded_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_derivative_edges_company_id", "content_derivative_graph_edges", ["company_id"])
    op.create_index("ix_derivative_edges_channel_id", "content_derivative_graph_edges", ["channel_workspace_id"])
    op.create_index("ix_derivative_edges_parent_project", "content_derivative_graph_edges", ["parent_video_project_id"])
    op.create_index("ix_derivative_edges_parent_uploaded", "content_derivative_graph_edges", ["parent_uploaded_video_id"])
    op.create_index("ix_derivative_edges_type", "content_derivative_graph_edges", ["derivative_type"])

    op.create_table(
        "short_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_video_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid_fk("parent_voice_timeline_id", "voice_timeline_snapshots"),
        _uuid_fk("parent_caption_track_id", "caption_track_snapshots"),
        _uuid_fk("parent_visual_plan_id", "visual_plan_snapshots"),
        sa.Column("start_time_ms", sa.Integer(), nullable=False),
        sa.Column("end_time_ms", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("caption_ids", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("core_idea", sa.Text(), nullable=False),
        sa.Column("hook_line", sa.Text(), nullable=False),
        sa.Column("standalone_summary", sa.Text(), nullable=False),
        sa.Column("suggested_title", sa.Text(), nullable=True),
        sa.Column("overlay_text", sa.Text(), nullable=True),
        sa.Column("crop_strategy", sa.String(length=40), nullable=False),
        sa.Column("visual_source", sa.String(length=40), nullable=False),
        sa.Column("candidate_state", sa.String(length=40), nullable=False),
        sa.Column("policy_risk_level", sa.String(length=40), nullable=True),
        sa.Column("rights_risk_level", sa.String(length=40), nullable=True),
        sa.Column("production_cost_estimate", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(SHORT_STATE_CHECK, name="ck_short_candidates_state"),
        sa.CheckConstraint(CROP_STRATEGY_CHECK, name="ck_short_candidates_crop_strategy"),
        sa.CheckConstraint(VISUAL_SOURCE_CHECK, name="ck_short_candidates_visual_source"),
        sa.CheckConstraint("duration_ms = end_time_ms - start_time_ms", name="ck_short_candidates_duration_matches"),
        sa.CheckConstraint("duration_ms > 0 and duration_ms < 59000", name="ck_short_candidates_duration_cap"),
        sa.CheckConstraint("jsonb_typeof(caption_ids) = 'array'", name="ck_short_candidates_caption_ids_array"),
        sa.CheckConstraint("jsonb_typeof(production_cost_estimate) = 'object'", name="ck_short_candidates_cost_object"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["parent_video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["parent_voice_timeline_id"], ["voice_timeline_snapshots.id"]),
        sa.ForeignKeyConstraint(["parent_caption_track_id"], ["caption_track_snapshots.id"]),
        sa.ForeignKeyConstraint(["parent_visual_plan_id"], ["visual_plan_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_short_candidates_company_id", "short_candidates", ["company_id"])
    op.create_index("ix_short_candidates_channel_id", "short_candidates", ["channel_workspace_id"])
    op.create_index("ix_short_candidates_parent_project", "short_candidates", ["parent_video_project_id"])
    op.create_index("ix_short_candidates_state", "short_candidates", ["candidate_state"])

    op.create_table(
        "short_candidate_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("short_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hook_strength", sa.Numeric(8, 4), nullable=False),
        sa.Column("standalone_clarity", sa.Numeric(8, 4), nullable=False),
        sa.Column("insight_density", sa.Numeric(8, 4), nullable=False),
        sa.Column("visual_punch", sa.Numeric(8, 4), nullable=False),
        sa.Column("audience_relevance", sa.Numeric(8, 4), nullable=False),
        sa.Column("bridge_value", sa.Numeric(8, 4), nullable=False),
        sa.Column("production_reuse_saving", sa.Numeric(8, 4), nullable=False),
        sa.Column("context_dependency_penalty", sa.Numeric(8, 4), nullable=False),
        sa.Column("policy_risk_penalty", sa.Numeric(8, 4), nullable=False),
        sa.Column("generic_template_penalty", sa.Numeric(8, 4), nullable=False),
        sa.Column("total_score", sa.Numeric(8, 4), nullable=False),
        sa.Column("score_version", sa.String(length=40), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["short_candidate_id"], ["short_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_short_candidate_scores_candidate_id", "short_candidate_scores", ["short_candidate_id"])
    op.create_index("ix_short_candidate_scores_total", "short_candidate_scores", ["total_score"])

    op.create_table(
        "short_render_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("short_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_platform", sa.String(length=40), nullable=False),
        sa.Column("target_aspect_ratio", sa.String(length=20), server_default=sa.text("'9:16'"), nullable=False),
        sa.Column("target_duration_ms", sa.Integer(), nullable=False),
        sa.Column("voice_source", sa.String(length=40), nullable=False),
        sa.Column("caption_style_ref", sa.Text(), nullable=True),
        sa.Column("visual_plan", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("render_state", sa.String(length=40), nullable=False),
        sa.Column("blocker_reason", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(TARGET_PLATFORM_CHECK, name="ck_short_render_plans_target_platform"),
        sa.CheckConstraint(VOICE_SOURCE_CHECK, name="ck_short_render_plans_voice_source"),
        sa.CheckConstraint(RENDER_STATE_CHECK, name="ck_short_render_plans_render_state"),
        sa.CheckConstraint("jsonb_typeof(visual_plan) = 'object'", name="ck_short_render_plans_visual_plan_object"),
        sa.ForeignKeyConstraint(["short_candidate_id"], ["short_candidates.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_short_render_plans_candidate_id", "short_render_plans", ["short_candidate_id"])
    op.create_index("ix_short_render_plans_platform", "short_render_plans", ["target_platform"])
    op.create_index("ix_short_render_plans_state", "short_render_plans", ["render_state"])

    op.create_table(
        "promote_short_to_long_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid_fk("source_short_uploaded_video_id", "uploaded_videos"),
        _uuid_fk("source_short_candidate_id", "short_candidates"),
        sa.Column("winning_hook", sa.Text(), nullable=False),
        sa.Column("audience_signal", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("suggested_long_topic", sa.Text(), nullable=False),
        sa.Column("suggested_outline", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("expected_watch_hour_potential", sa.String(length=40), nullable=False),
        sa.Column("confidence_label", sa.String(length=40), nullable=True),
        sa.Column("risk_level", sa.String(length=40), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("evidence_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(WATCH_HOUR_CHECK, name="ck_promote_short_to_long_watch_hours"),
        sa.CheckConstraint(PROMOTE_STATE_CHECK, name="ck_promote_short_to_long_state"),
        sa.CheckConstraint("jsonb_typeof(audience_signal) = 'object'", name="ck_promote_short_to_long_audience_object"),
        sa.CheckConstraint("jsonb_typeof(suggested_outline) = 'object'", name="ck_promote_short_to_long_outline_object"),
        sa.CheckConstraint("jsonb_typeof(evidence_refs) = 'array'", name="ck_promote_short_to_long_refs_array"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["source_short_uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["source_short_candidate_id"], ["short_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_promote_short_to_long_company_id", "promote_short_to_long_candidates", ["company_id"])
    op.create_index("ix_promote_short_to_long_channel_id", "promote_short_to_long_candidates", ["channel_workspace_id"])
    op.create_index("ix_promote_short_to_long_state", "promote_short_to_long_candidates", ["state"])

    op.create_table(
        "reusable_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid_fk("channel_workspace_id", "channel_workspaces"),
        sa.Column("artifact_type", sa.String(length=40), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("source_provider", sa.String(length=160), nullable=True),
        sa.Column("license_status", sa.String(length=80), nullable=False),
        sa.Column("rights_envelope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reuse_scope", sa.String(length=40), nullable=False),
        sa.Column("reuse_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_reuse_policy", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("cooldown_days", sa.Integer(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_video_ids", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("quality_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(ARTIFACT_TYPE_CHECK, name="ck_reusable_artifacts_type"),
        sa.CheckConstraint(REUSE_SCOPE_CHECK, name="ck_reusable_artifacts_scope"),
        sa.CheckConstraint(REUSABLE_STATE_CHECK, name="ck_reusable_artifacts_state"),
        sa.CheckConstraint("reuse_count >= 0", name="ck_reusable_artifacts_reuse_count_nonnegative"),
        sa.CheckConstraint("jsonb_typeof(max_reuse_policy) = 'object'", name="ck_reusable_artifacts_policy_object"),
        sa.CheckConstraint("jsonb_typeof(last_used_video_ids) = 'array'", name="ck_reusable_artifacts_last_used_array"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reusable_artifacts_company_id", "reusable_artifacts", ["company_id"])
    op.create_index("ix_reusable_artifacts_channel_id", "reusable_artifacts", ["channel_workspace_id"])
    op.create_index("ix_reusable_artifacts_hash", "reusable_artifacts", ["content_hash"])
    op.create_index("ix_reusable_artifacts_state", "reusable_artifacts", ["state"])

    op.create_table(
        "asset_reuse_index_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reusable_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_requirement_hash", sa.String(length=128), nullable=False),
        sa.Column("match_reason", sa.Text(), nullable=False),
        sa.Column("match_score", sa.Numeric(8, 4), nullable=False),
        sa.Column("last_selected_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["reusable_artifact_id"], ["reusable_artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_reuse_entries_artifact_id", "asset_reuse_index_entries", ["reusable_artifact_id"])
    op.create_index("ix_asset_reuse_entries_requirement_hash", "asset_reuse_index_entries", ["scene_requirement_hash"])

    op.create_table(
        "derivative_originality_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid_fk("content_derivative_edge_id", "content_derivative_graph_edges"),
        _uuid_fk("short_candidate_id", "short_candidates"),
        sa.Column("derivative_type", sa.String(length=40), nullable=False),
        sa.Column("standalone_value_ok", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("new_value_added_ok", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reused_runtime_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("template_repetition_risk", sa.String(length=40), nullable=True),
        sa.Column("generic_stock_risk", sa.String(length=40), nullable=True),
        sa.Column("commentary_or_context_added", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("policy_flags", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("rights_flags", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("result", sa.String(length=40), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint(DERIVATIVE_TYPE_CHECK, name="ck_derivative_originality_type"),
        sa.CheckConstraint(ORIGINALITY_RESULT_CHECK, name="ck_derivative_originality_result"),
        sa.CheckConstraint("jsonb_typeof(policy_flags) = 'array'", name="ck_derivative_originality_policy_array"),
        sa.CheckConstraint("jsonb_typeof(rights_flags) = 'array'", name="ck_derivative_originality_rights_array"),
        sa.CheckConstraint("jsonb_typeof(technical_appendix) = 'object'", name="ck_derivative_originality_appendix_object"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["content_derivative_edge_id"], ["content_derivative_graph_edges.id"]),
        sa.ForeignKeyConstraint(["short_candidate_id"], ["short_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_derivative_originality_company_id", "derivative_originality_checks", ["company_id"])
    op.create_index("ix_derivative_originality_short_id", "derivative_originality_checks", ["short_candidate_id"])
    op.create_index("ix_derivative_originality_result", "derivative_originality_checks", ["result"])

    op.create_table(
        "originality_budgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid_fk("channel_workspace_id", "channel_workspaces"),
        _uuid_fk("video_project_id", "video_projects"),
        _uuid_fk("uploaded_video_id", "uploaded_videos"),
        sa.Column("new_script_ratio", sa.Numeric(8, 4), nullable=True),
        sa.Column("new_narrative_angle_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("new_diagram_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reused_runtime_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("same_template_recent_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("same_stock_clip_recent_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("derivative_count_from_parent", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("originality_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("result", sa.String(length=40), nullable=False),
        _created_at(),
        sa.CheckConstraint(ORIGINALITY_BUDGET_RESULT_CHECK, name="ck_originality_budgets_result"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_originality_budgets_company_id", "originality_budgets", ["company_id"])
    op.create_index("ix_originality_budgets_project_id", "originality_budgets", ["video_project_id"])
    op.create_index("ix_originality_budgets_result", "originality_budgets", ["result"])

    op.create_table(
        "derivative_release_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid_fk("parent_video_project_id", "video_projects"),
        _uuid_fk("parent_uploaded_video_id", "uploaded_videos"),
        sa.Column("max_shorts_per_long", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("min_spacing_hours", sa.Integer(), server_default=sa.text("24"), nullable=False),
        sa.Column("preferred_publish_order", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("platform_surface", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("bridge_strategy", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("avoid_same_day_spam", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("release_state", sa.String(length=40), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(RELEASE_STATE_CHECK, name="ck_derivative_release_plans_state"),
        sa.CheckConstraint("max_shorts_per_long >= 0 and max_shorts_per_long <= 3", name="ck_derivative_release_plans_max_shorts"),
        sa.CheckConstraint("jsonb_typeof(preferred_publish_order) = 'array'", name="ck_derivative_release_plans_order_array"),
        sa.CheckConstraint("jsonb_typeof(platform_surface) = 'array'", name="ck_derivative_release_plans_surface_array"),
        sa.CheckConstraint("jsonb_typeof(bridge_strategy) = 'object'", name="ck_derivative_release_plans_bridge_object"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["parent_video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["parent_uploaded_video_id"], ["uploaded_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_derivative_release_plans_company_id", "derivative_release_plans", ["company_id"])
    op.create_index("ix_derivative_release_plans_parent_project", "derivative_release_plans", ["parent_video_project_id"])
    op.create_index("ix_derivative_release_plans_state", "derivative_release_plans", ["release_state"])

    op.create_table(
        "cross_platform_funnel_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid_fk("parent_video_project_id", "video_projects"),
        _uuid_fk("parent_uploaded_video_id", "uploaded_videos"),
        _uuid_fk("youtube_long_package_id", "publish_handoff_packages"),
        sa.Column("selected_short_candidate_ids", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("youtube_shorts_package_status", sa.String(length=40), nullable=True),
        sa.Column("tiktok_package_status", sa.String(length=40), nullable=True),
        sa.Column("facebook_reels_package_status", sa.String(length=40), nullable=True),
        sa.Column("bridge_strategy", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("package_state", sa.String(length=40), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(FUNNEL_STATE_CHECK, name="ck_cross_platform_funnel_packages_state"),
        sa.CheckConstraint("jsonb_typeof(selected_short_candidate_ids) = 'array'", name="ck_cross_platform_funnel_packages_selected_array"),
        sa.CheckConstraint("jsonb_typeof(bridge_strategy) = 'object'", name="ck_cross_platform_funnel_packages_bridge_object"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["parent_video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["parent_uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["youtube_long_package_id"], ["publish_handoff_packages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cross_platform_funnel_packages_company_id", "cross_platform_funnel_packages", ["company_id"])
    op.create_index("ix_cross_platform_funnel_packages_parent_project", "cross_platform_funnel_packages", ["parent_video_project_id"])
    op.create_index("ix_cross_platform_funnel_packages_state", "cross_platform_funnel_packages", ["package_state"])

    op.create_table(
        "upload_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        _uuid_fk("video_project_id", "video_projects"),
        _uuid_fk("short_candidate_id", "short_candidates"),
        _uuid_fk("render_plan_id", "short_render_plans"),
        sa.Column("file_ref", sa.Text(), nullable=True),
        sa.Column("title_internal", sa.Text(), nullable=False),
        sa.Column("hook_line", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hashtags", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("cta_type", sa.String(length=40), nullable=False),
        sa.Column("cta_text", sa.Text(), nullable=True),
        sa.Column("pinned_comment", sa.Text(), nullable=True),
        sa.Column("ai_disclosure_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("ai_disclosure_reason", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("music_policy", sa.String(length=40), nullable=False),
        sa.Column("cover_frame_suggestion", sa.Text(), nullable=True),
        sa.Column("human_notes", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("paste_back_required_fields", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("card_state", sa.String(length=40), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(UPLOAD_PLATFORM_CHECK, name="ck_upload_cards_platform"),
        sa.CheckConstraint(CTA_TYPE_CHECK, name="ck_upload_cards_cta_type"),
        sa.CheckConstraint(MUSIC_POLICY_CHECK, name="ck_upload_cards_music_policy"),
        sa.CheckConstraint(UPLOAD_CARD_STATE_CHECK, name="ck_upload_cards_state"),
        sa.CheckConstraint("jsonb_typeof(hashtags) = 'array'", name="ck_upload_cards_hashtags_array"),
        sa.CheckConstraint("jsonb_typeof(ai_disclosure_reason) = 'array'", name="ck_upload_cards_ai_reason_array"),
        sa.CheckConstraint("jsonb_typeof(human_notes) = 'array'", name="ck_upload_cards_human_notes_array"),
        sa.CheckConstraint("jsonb_typeof(paste_back_required_fields) = 'array'", name="ck_upload_cards_paste_back_array"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["short_candidate_id"], ["short_candidates.id"]),
        sa.ForeignKeyConstraint(["render_plan_id"], ["short_render_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_upload_cards_company_id", "upload_cards", ["company_id"])
    op.create_index("ix_upload_cards_channel_id", "upload_cards", ["channel_workspace_id"])
    op.create_index("ix_upload_cards_short_candidate_id", "upload_cards", ["short_candidate_id"])
    op.create_index("ix_upload_cards_platform", "upload_cards", ["platform"])
    op.create_index("ix_upload_cards_state", "upload_cards", ["card_state"])

    op.create_table(
        "human_upload_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("upload_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_platform", sa.String(length=40), nullable=False),
        sa.Column("task_state", sa.String(length=40), nullable=False),
        sa.Column("required_checklist", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("scheduled_time_suggestion", sa.DateTime(timezone=True), nullable=True),
        _uuid_fk("actual_uploaded_video_id", "uploaded_videos"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(UPLOAD_PLATFORM_CHECK.replace("platform", "target_platform"), name="ck_human_upload_tasks_target_platform"),
        sa.CheckConstraint(HUMAN_TASK_STATE_CHECK, name="ck_human_upload_tasks_state"),
        sa.CheckConstraint("jsonb_typeof(required_checklist) = 'array'", name="ck_human_upload_tasks_checklist_array"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["upload_card_id"], ["upload_cards.id"]),
        sa.ForeignKeyConstraint(["actual_uploaded_video_id"], ["uploaded_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_human_upload_tasks_company_id", "human_upload_tasks", ["company_id"])
    op.create_index("ix_human_upload_tasks_channel_id", "human_upload_tasks", ["channel_workspace_id"])
    op.create_index("ix_human_upload_tasks_card_id", "human_upload_tasks", ["upload_card_id"])
    op.create_index("ix_human_upload_tasks_state", "human_upload_tasks", ["task_state"])

    op.create_table(
        "usage_savings_ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid_fk("channel_workspace_id", "channel_workspaces"),
        _uuid_fk("video_project_id", "video_projects"),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("estimated_cost_without_reuse", sa.Numeric(18, 6), nullable=True),
        sa.Column("actual_cost_with_reuse", sa.Numeric(18, 6), nullable=True),
        sa.Column("saved_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("saved_tokens", sa.Numeric(18, 6), nullable=True),
        sa.Column("saved_ai_video_seconds", sa.Numeric(18, 6), nullable=True),
        sa.Column("saved_tts_characters", sa.Numeric(18, 6), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_savings_company_id", "usage_savings_ledger_entries", ["company_id"])
    op.create_index("ix_usage_savings_project_id", "usage_savings_ledger_entries", ["video_project_id"])
    op.create_index("ix_usage_savings_created_at", "usage_savings_ledger_entries", ["created_at"])


def downgrade() -> None:
    for table, indexes in [
        ("usage_savings_ledger_entries", ["ix_usage_savings_created_at", "ix_usage_savings_project_id", "ix_usage_savings_company_id"]),
        ("human_upload_tasks", ["ix_human_upload_tasks_state", "ix_human_upload_tasks_card_id", "ix_human_upload_tasks_channel_id", "ix_human_upload_tasks_company_id"]),
        ("upload_cards", ["ix_upload_cards_state", "ix_upload_cards_platform", "ix_upload_cards_short_candidate_id", "ix_upload_cards_channel_id", "ix_upload_cards_company_id"]),
        ("cross_platform_funnel_packages", ["ix_cross_platform_funnel_packages_state", "ix_cross_platform_funnel_packages_parent_project", "ix_cross_platform_funnel_packages_company_id"]),
        ("derivative_release_plans", ["ix_derivative_release_plans_state", "ix_derivative_release_plans_parent_project", "ix_derivative_release_plans_company_id"]),
        ("originality_budgets", ["ix_originality_budgets_result", "ix_originality_budgets_project_id", "ix_originality_budgets_company_id"]),
        ("derivative_originality_checks", ["ix_derivative_originality_result", "ix_derivative_originality_short_id", "ix_derivative_originality_company_id"]),
        ("asset_reuse_index_entries", ["ix_asset_reuse_entries_requirement_hash", "ix_asset_reuse_entries_artifact_id"]),
        ("reusable_artifacts", ["ix_reusable_artifacts_state", "ix_reusable_artifacts_hash", "ix_reusable_artifacts_channel_id", "ix_reusable_artifacts_company_id"]),
        ("promote_short_to_long_candidates", ["ix_promote_short_to_long_state", "ix_promote_short_to_long_channel_id", "ix_promote_short_to_long_company_id"]),
        ("short_render_plans", ["ix_short_render_plans_state", "ix_short_render_plans_platform", "ix_short_render_plans_candidate_id"]),
        ("short_candidate_scores", ["ix_short_candidate_scores_total", "ix_short_candidate_scores_candidate_id"]),
        ("short_candidates", ["ix_short_candidates_state", "ix_short_candidates_parent_project", "ix_short_candidates_channel_id", "ix_short_candidates_company_id"]),
        ("content_derivative_graph_edges", ["ix_derivative_edges_type", "ix_derivative_edges_parent_uploaded", "ix_derivative_edges_parent_project", "ix_derivative_edges_channel_id", "ix_derivative_edges_company_id"]),
        ("llm_route_attempts", ["ix_llm_route_attempts_created_at", "ix_llm_route_attempts_status", "ix_llm_route_attempts_lane_name", "ix_llm_route_attempts_profile_id"]),
        ("llm_model_profiles", ["ix_llm_model_profiles_provider_key", "ix_llm_model_profiles_model_id"]),
        ("llm_router_lanes", ["ix_llm_router_lanes_profile_id", "ix_llm_router_lanes_lane_name"]),
        ("llm_router_profiles", ["ix_llm_router_profiles_provider_key"]),
    ]:
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
