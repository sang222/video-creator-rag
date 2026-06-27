"""M10.2 media provider role matrix and routing foundation

Revision ID: 0013_m10_2_provider_routing
Revises: 0012_m10_1_router_derivatives
Create Date: 2026-06-27 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0013_m10_2_provider_routing"
down_revision: str | None = "0012_m10_1_router_derivatives"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

PROVIDER_TYPE_CHECK = (
    "provider_type in ("
    "'WORKFLOW_ORCHESTRATOR','LLM_SCRIPT_ENGINE','API_NATIVE_TTS','CAPTION_TIMELINE_ENGINE',"
    "'AI_VIDEO_HERO_PROVIDER','CLOUD_TEMPLATE_RENDERER_LIGHT','CLOUD_FINAL_ASSEMBLY_RENDERER',"
    "'MEDIA_STORAGE','MEDIA_QC_ENGINE','PUBLISH_PACKAGE_BUILDER','API_NATIVE_STOCK_PROVIDER',"
    "'FREE_FALLBACK_PROVIDER','MOCK_PROVIDER','DEFERRED_MANUAL_LIBRARY'"
    ")"
)
RECOMMENDATION_CHECK = (
    "recommendation in ('CORE','CORE_QUALITY_LAYER','CORE_LIGHT_RENDER','REQUIRED_GAP','DEFERRED','FALLBACK','AVOIDED','MOCK')"
)
CAPABILITY_CHECK = (
    "capability in ('SUPPORTED','UNSUPPORTED','BLOCKED_BY_PLAN','REQUIRES_UPGRADE','REQUIRES_EXTERNAL_PROVIDER','MOCK_ONLY')"
)
ROUTING_RESULT_CHECK = (
    "routing_result in ('ROUTED','BLOCKED_PROVIDER_CAPABILITY_REQUIRED','BLOCKED_BUDGET','BLOCKED_LICENSE',"
    "'BLOCKED_UNKNOWN_PROVIDER','BLOCKED_SCOPE')"
)
REQUESTED_PROVIDER_TYPE_CHECK = (
    "requested_provider_type in ("
    "'WORKFLOW_ORCHESTRATOR','LLM_SCRIPT_ENGINE','API_NATIVE_TTS','CAPTION_TIMELINE_ENGINE',"
    "'AI_VIDEO_HERO_PROVIDER','CLOUD_TEMPLATE_RENDERER_LIGHT','CLOUD_FINAL_ASSEMBLY_RENDERER',"
    "'MEDIA_STORAGE','MEDIA_QC_ENGINE','PUBLISH_PACKAGE_BUILDER','API_NATIVE_STOCK_PROVIDER',"
    "'FREE_FALLBACK_PROVIDER','MOCK_PROVIDER','DEFERRED_MANUAL_LIBRARY'"
    ")"
)
SELECTED_PROVIDER_TYPE_CHECK = REQUESTED_PROVIDER_TYPE_CHECK.replace("requested_provider_type", "selected_provider_type")
SOURCE_PROVIDER_TYPE_CHECK = REQUESTED_PROVIDER_TYPE_CHECK.replace("requested_provider_type", "source_provider_type")
BUDGET_MODE_CHECK = "current_mode in ('QUALITY_FIRST_250','CUSTOM','TEST')"
BUDGET_ENFORCEMENT_CHECK = "enforcement in ('HARD_BLOCK','REVIEW_REQUIRED','OBSERVE_ONLY')"
BUDGET_STATE_CHECK = "budget_state in ('OK','WARNING','EXCEEDED','UNKNOWN')"
LONG_PACKAGE_STATE_CHECK = (
    "package_state in ('DRAFT','READY_FOR_FINAL_RENDER','BLOCKED_PROVIDER_CAPABILITY_REQUIRED','FINAL_RENDERED','QC_READY','CANCELLED')"
)
SHORT_PACKAGE_STATE_CHECK = "package_state in ('DRAFT','READY_FOR_TEMPLATE_RENDER','RENDERED','QC_READY','BLOCKED','CANCELLED')"
AI_HERO_USAGE_CHECK = "intended_usage in ('OPENING_HOOK','KEY_METAPHOR','SHORT_HOOK','THUMBNAIL_STILL','OTHER')"
AI_HERO_STATE_CHECK = "generation_state in ('PLANNED','READY_FOR_PROVIDER','GENERATED','BLOCKED','CANCELLED')"
CREATOMATE_STATE_CHECK = "render_state in ('PLANNED','READY_FOR_PROVIDER','RENDERED','BLOCKED','CANCELLED')"
THUMBNAIL_STATE_CHECK = "state in ('DRAFT','READY_FOR_PROVIDER','RENDERED','SELECTED','REJECTED','CANCELLED')"
FINAL_MEDIA_TYPE_CHECK = "media_type in ('LONG_FORM_FINAL','SHORT_FINAL','THUMBNAIL','CARD','AI_HERO','PREVIEW')"
LICENSE_STATUS_CHECK = "license_status in ('CONFIRMED','NEEDS_REVIEW','BLOCKED','NOT_REQUIRED','UNKNOWN')"


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
        "media_provider_role_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("provider_type", sa.String(length=80), nullable=False),
        sa.Column("role_description", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.String(length=40), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_real_provider", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("supports_real_execution", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("monthly_budget_assumption", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(PROVIDER_TYPE_CHECK, name="ck_media_provider_roles_type"),
        sa.CheckConstraint(RECOMMENDATION_CHECK, name="ck_media_provider_roles_recommendation"),
        sa.CheckConstraint("jsonb_typeof(monthly_budget_assumption) = 'object'", name="ck_media_provider_roles_budget_object"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key", name="uq_media_provider_role_profiles_key"),
    )
    op.create_index("ix_media_provider_roles_type", "media_provider_role_profiles", ["provider_type"])
    op.create_index("ix_media_provider_roles_recommendation", "media_provider_role_profiles", ["recommendation"])

    op.create_table(
        "provider_capability_matrix_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("provider_type", sa.String(length=80), nullable=False),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("capability", sa.String(length=40), nullable=False),
        sa.Column("max_duration_seconds", sa.Numeric(18, 6), nullable=True),
        sa.Column("supported_aspect_ratios", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("supported_outputs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("plan_requirement", sa.String(length=120), nullable=True),
        sa.Column("capability_reason", sa.Text(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(PROVIDER_TYPE_CHECK, name="ck_provider_capability_entries_type"),
        sa.CheckConstraint(CAPABILITY_CHECK, name="ck_provider_capability_entries_capability"),
        sa.CheckConstraint("jsonb_typeof(supported_aspect_ratios) = 'array'", name="ck_provider_cap_entries_ratios_array"),
        sa.CheckConstraint("jsonb_typeof(supported_outputs) = 'array'", name="ck_provider_cap_entries_outputs_array"),
        sa.ForeignKeyConstraint(["provider_key"], ["media_provider_role_profiles.provider_key"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key", "job_type", name="uq_provider_capability_provider_job"),
    )
    op.create_index("ix_provider_capability_provider_type", "provider_capability_matrix_entries", ["provider_type"])
    op.create_index("ix_provider_capability_job_type", "provider_capability_matrix_entries", ["job_type"])
    op.create_index("ix_provider_capability_capability", "provider_capability_matrix_entries", ["capability"])

    op.create_table(
        "media_provider_budget_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid("company_id"),
        sa.Column("provider_type", sa.String(length=80), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=True),
        sa.Column("monthly_cap_units", sa.Numeric(18, 6), nullable=True),
        sa.Column("monthly_cap_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("monthly_cap_seconds", sa.Numeric(18, 6), nullable=True),
        sa.Column("monthly_cap_renders", sa.Integer(), nullable=True),
        sa.Column("current_mode", sa.String(length=40), nullable=False),
        sa.Column("enforcement", sa.String(length=40), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(PROVIDER_TYPE_CHECK, name="ck_media_budget_policies_provider_type"),
        sa.CheckConstraint(BUDGET_MODE_CHECK, name="ck_media_budget_policies_mode"),
        sa.CheckConstraint(BUDGET_ENFORCEMENT_CHECK, name="ck_media_budget_policies_enforcement"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_budget_policies_company", "media_provider_budget_policies", ["company_id"])
    op.create_index("ix_media_budget_policies_provider", "media_provider_budget_policies", ["provider_type", "provider_key"])

    op.create_table(
        "media_provider_budget_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid("company_id"),
        sa.Column("provider_type", sa.String(length=80), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estimated_usage_units", sa.Numeric(18, 6), nullable=True),
        sa.Column("estimated_usage_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("estimated_usage_seconds", sa.Numeric(18, 6), nullable=True),
        sa.Column("estimated_render_count", sa.Integer(), nullable=True),
        sa.Column("budget_state", sa.String(length=40), nullable=False),
        _created_at(),
        sa.CheckConstraint(PROVIDER_TYPE_CHECK, name="ck_media_budget_snapshots_provider_type"),
        sa.CheckConstraint(BUDGET_STATE_CHECK, name="ck_media_budget_snapshots_state"),
        sa.CheckConstraint("period_end > period_start", name="ck_media_budget_snapshots_period"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_budget_snapshots_company", "media_provider_budget_snapshots", ["company_id"])
    op.create_index("ix_media_budget_snapshots_provider", "media_provider_budget_snapshots", ["provider_type", "provider_key"])
    op.create_index("ix_media_budget_snapshots_period", "media_provider_budget_snapshots", ["period_start", "period_end"])

    op.create_table(
        "media_render_routing_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid("company_id"),
        _uuid("channel_workspace_id"),
        _uuid("video_project_id"),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("requested_provider_type", sa.String(length=80), nullable=True),
        sa.Column("selected_provider_type", sa.String(length=80), nullable=True),
        sa.Column("selected_provider_key", sa.String(length=160), nullable=True),
        sa.Column("routing_result", sa.String(length=80), nullable=False),
        sa.Column("blocker_reason", sa.Text(), nullable=True),
        _uuid("capability_entry_id"),
        _uuid("budget_snapshot_id"),
        sa.Column("technical_appendix", JSONB, server_default=_jsonb_object(), nullable=False),
        _created_at(),
        sa.CheckConstraint(ROUTING_RESULT_CHECK, name="ck_media_routing_decisions_result"),
        sa.CheckConstraint("requested_provider_type is null or " + REQUESTED_PROVIDER_TYPE_CHECK, name="ck_media_routing_req_provider_type"),
        sa.CheckConstraint("selected_provider_type is null or " + SELECTED_PROVIDER_TYPE_CHECK, name="ck_media_routing_selected_type"),
        sa.CheckConstraint("jsonb_typeof(technical_appendix) = 'object'", name="ck_media_routing_appendix_object"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["capability_entry_id"], ["provider_capability_matrix_entries.id"]),
        sa.ForeignKeyConstraint(["budget_snapshot_id"], ["media_provider_budget_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_routing_decisions_company", "media_render_routing_decisions", ["company_id"])
    op.create_index("ix_media_routing_decisions_project", "media_render_routing_decisions", ["video_project_id"])
    op.create_index("ix_media_routing_decisions_job", "media_render_routing_decisions", ["job_type"])
    op.create_index("ix_media_routing_decisions_result", "media_render_routing_decisions", ["routing_result"])

    op.create_table(
        "long_form_render_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id", nullable=False),
        _uuid("video_project_id", nullable=False),
        _uuid("voice_timeline_id"),
        _uuid("caption_track_id"),
        _uuid("visual_plan_id"),
        sa.Column("ai_hero_asset_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("creatomate_asset_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("approved_asset_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("thumbnail_variant_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("music_sfx_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("render_manifest", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("final_renderer_required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("final_renderer_provider_key", sa.String(length=160), nullable=True),
        sa.Column("package_state", sa.String(length=80), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(LONG_PACKAGE_STATE_CHECK, name="ck_long_form_render_packages_state"),
        sa.CheckConstraint("jsonb_typeof(ai_hero_asset_refs) = 'array'", name="ck_long_pkg_ai_refs_array"),
        sa.CheckConstraint("jsonb_typeof(creatomate_asset_refs) = 'array'", name="ck_long_pkg_creatomate_refs_array"),
        sa.CheckConstraint("jsonb_typeof(approved_asset_refs) = 'array'", name="ck_long_pkg_approved_refs_array"),
        sa.CheckConstraint("jsonb_typeof(thumbnail_variant_refs) = 'array'", name="ck_long_pkg_thumbnail_refs_array"),
        sa.CheckConstraint("jsonb_typeof(music_sfx_refs) = 'array'", name="ck_long_pkg_music_refs_array"),
        sa.CheckConstraint("jsonb_typeof(render_manifest) = 'object'", name="ck_long_pkg_manifest_object"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["voice_timeline_id"], ["voice_timeline_snapshots.id"]),
        sa.ForeignKeyConstraint(["caption_track_id"], ["caption_track_snapshots.id"]),
        sa.ForeignKeyConstraint(["visual_plan_id"], ["visual_plan_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_long_form_render_packages_company", "long_form_render_packages", ["company_id"])
    op.create_index("ix_long_form_render_packages_project", "long_form_render_packages", ["video_project_id"])
    op.create_index("ix_long_form_render_packages_state", "long_form_render_packages", ["package_state"])

    op.create_table(
        "short_render_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id", nullable=False),
        _uuid("video_project_id"),
        _uuid("short_candidate_id"),
        _uuid("short_render_plan_id"),
        sa.Column("voice_ref", sa.Text(), nullable=True),
        _uuid("caption_track_id"),
        sa.Column("hero_reuse_ref", sa.Text(), nullable=True),
        sa.Column("template_asset_refs", JSONB, server_default=_jsonb_array(), nullable=False),
        sa.Column("render_manifest", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("target_duration_seconds", sa.Numeric(18, 6), nullable=True),
        sa.Column("target_aspect_ratio", sa.String(length=20), server_default="9:16", nullable=False),
        sa.Column("hard_cap_seconds", sa.Integer(), server_default="59", nullable=False),
        sa.Column("renderer_provider_key", sa.String(length=160), nullable=True),
        sa.Column("package_state", sa.String(length=80), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(SHORT_PACKAGE_STATE_CHECK, name="ck_short_render_packages_state"),
        sa.CheckConstraint("jsonb_typeof(template_asset_refs) = 'array'", name="ck_short_pkg_template_refs_array"),
        sa.CheckConstraint("jsonb_typeof(render_manifest) = 'object'", name="ck_short_pkg_manifest_object"),
        sa.CheckConstraint("target_duration_seconds is null or target_duration_seconds < hard_cap_seconds", name="ck_short_pkg_duration_cap"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["short_candidate_id"], ["short_candidates.id"]),
        sa.ForeignKeyConstraint(["short_render_plan_id"], ["short_render_plans.id"]),
        sa.ForeignKeyConstraint(["caption_track_id"], ["caption_track_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_short_render_packages_company", "short_render_packages", ["company_id"])
    op.create_index("ix_short_render_packages_candidate", "short_render_packages", ["short_candidate_id"])
    op.create_index("ix_short_render_packages_state", "short_render_packages", ["package_state"])

    op.create_table(
        "ai_hero_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id", nullable=False),
        _uuid("video_project_id"),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("intended_usage", sa.String(length=40), nullable=False),
        sa.Column("provider_type", sa.String(length=80), server_default="AI_VIDEO_HERO_PROVIDER", nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(18, 6), nullable=True),
        sa.Column("asset_ref", sa.Text(), nullable=True),
        sa.Column("still_frame_ref", sa.Text(), nullable=True),
        sa.Column("rights_evidence_ref", sa.Text(), nullable=True),
        sa.Column("generation_state", sa.String(length=40), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(AI_HERO_USAGE_CHECK, name="ck_ai_hero_assets_usage"),
        sa.CheckConstraint(PROVIDER_TYPE_CHECK, name="ck_ai_hero_assets_provider_type"),
        sa.CheckConstraint(AI_HERO_STATE_CHECK, name="ck_ai_hero_assets_state"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_hero_assets_company", "ai_hero_assets", ["company_id"])
    op.create_index("ix_ai_hero_assets_project", "ai_hero_assets", ["video_project_id"])
    op.create_index("ix_ai_hero_assets_state", "ai_hero_assets", ["generation_state"])

    op.create_table(
        "creatomate_render_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id", nullable=False),
        _uuid("video_project_id"),
        _uuid("short_candidate_id"),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("template_key", sa.String(length=160), nullable=True),
        sa.Column("input_payload", JSONB, server_default=_jsonb_object(), nullable=False),
        sa.Column("output_ref", sa.Text(), nullable=True),
        sa.Column("provider_type", sa.String(length=80), server_default="CLOUD_TEMPLATE_RENDERER_LIGHT", nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=True),
        sa.Column("render_state", sa.String(length=40), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(PROVIDER_TYPE_CHECK, name="ck_creatomate_assets_provider_type"),
        sa.CheckConstraint(CREATOMATE_STATE_CHECK, name="ck_creatomate_assets_state"),
        sa.CheckConstraint("jsonb_typeof(input_payload) = 'object'", name="ck_creatomate_assets_payload_object"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["short_candidate_id"], ["short_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_creatomate_assets_company", "creatomate_render_assets", ["company_id"])
    op.create_index("ix_creatomate_assets_project", "creatomate_render_assets", ["video_project_id"])
    op.create_index("ix_creatomate_assets_job", "creatomate_render_assets", ["job_type"])
    op.create_index("ix_creatomate_assets_state", "creatomate_render_assets", ["render_state"])

    op.create_table(
        "thumbnail_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id", nullable=False),
        _uuid("video_project_id", nullable=False),
        sa.Column("variant_label", sa.String(length=160), nullable=False),
        sa.Column("title_text", sa.Text(), nullable=False),
        sa.Column("subtitle_text", sa.Text(), nullable=True),
        sa.Column("hero_still_ref", sa.Text(), nullable=True),
        sa.Column("output_ref", sa.Text(), nullable=True),
        sa.Column("provider_type", sa.String(length=80), server_default="CLOUD_TEMPLATE_RENDERER_LIGHT", nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(PROVIDER_TYPE_CHECK, name="ck_thumbnail_variants_provider_type"),
        sa.CheckConstraint(THUMBNAIL_STATE_CHECK, name="ck_thumbnail_variants_state"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_thumbnail_variants_company", "thumbnail_variants", ["company_id"])
    op.create_index("ix_thumbnail_variants_project", "thumbnail_variants", ["video_project_id"])
    op.create_index("ix_thumbnail_variants_state", "thumbnail_variants", ["state"])

    op.create_table(
        "final_media_refs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id", nullable=False),
        _uuid("video_project_id"),
        _uuid("uploaded_video_id"),
        sa.Column("media_type", sa.String(length=40), nullable=False),
        sa.Column("file_ref", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Numeric(18, 6), nullable=True),
        sa.Column("aspect_ratio", sa.String(length=20), nullable=True),
        sa.Column("resolution", sa.String(length=40), nullable=True),
        sa.Column("provider_key", sa.String(length=160), nullable=True),
        sa.Column("provider_type", sa.String(length=80), nullable=True),
        _uuid("media_qc_report_id"),
        _created_at(),
        sa.CheckConstraint(FINAL_MEDIA_TYPE_CHECK, name="ck_final_media_refs_type"),
        sa.CheckConstraint("provider_type is null or " + PROVIDER_TYPE_CHECK, name="ck_final_media_refs_provider_type"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.ForeignKeyConstraint(["media_qc_report_id"], ["media_qc_reports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_final_media_refs_company", "final_media_refs", ["company_id"])
    op.create_index("ix_final_media_refs_project", "final_media_refs", ["video_project_id"])
    op.create_index("ix_final_media_refs_type", "final_media_refs", ["media_type"])

    op.create_table(
        "license_evidence_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid("company_id", nullable=False),
        _uuid("channel_workspace_id"),
        _uuid("video_project_id"),
        sa.Column("asset_ref", sa.Text(), nullable=False),
        sa.Column("source_provider_type", sa.String(length=80), nullable=False),
        sa.Column("license_status", sa.String(length=40), nullable=False),
        _uuid("rights_envelope_id"),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        _created_at(),
        sa.CheckConstraint(SOURCE_PROVIDER_TYPE_CHECK, name="ck_license_evidence_provider_type"),
        sa.CheckConstraint(LICENSE_STATUS_CHECK, name="ck_license_evidence_status"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_workspace_id"], ["channel_workspaces.id"]),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_license_evidence_company", "license_evidence_records", ["company_id"])
    op.create_index("ix_license_evidence_project", "license_evidence_records", ["video_project_id"])
    op.create_index("ix_license_evidence_asset_ref", "license_evidence_records", ["asset_ref"])
    op.create_index("ix_license_evidence_status", "license_evidence_records", ["license_status"])


def downgrade() -> None:
    op.drop_table("license_evidence_records")
    op.drop_table("final_media_refs")
    op.drop_table("thumbnail_variants")
    op.drop_table("creatomate_render_assets")
    op.drop_table("ai_hero_assets")
    op.drop_table("short_render_packages")
    op.drop_table("long_form_render_packages")
    op.drop_table("media_render_routing_decisions")
    op.drop_table("media_provider_budget_snapshots")
    op.drop_table("media_provider_budget_policies")
    op.drop_table("provider_capability_matrix_entries")
    op.drop_table("media_provider_role_profiles")
