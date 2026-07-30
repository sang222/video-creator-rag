import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class LLMRouterProfile(Base):
    __tablename__ = "llm_router_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    profile_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    provider_key: Mapped[str] = mapped_column(
        String(80), nullable=False, default="OLLAMA"
    )
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    real_execution_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    default_timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (Index("ix_llm_router_profiles_provider_key", "provider_key"),)


class LLMRouterLane(Base):
    __tablename__ = "llm_router_lanes"

    id: Mapped[uuid.UUID] = uuid_pk()
    router_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_router_profiles.id"), nullable=False
    )
    lane_name: Mapped[str] = mapped_column(String(160), nullable=False)
    lane_description: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_task_types: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    primary_model: Mapped[str] = mapped_column(String(160), nullable=False)
    fallback_models: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    premium_model: Mapped[str | None] = mapped_column(String(160))
    emergency_model: Mapped[str | None] = mapped_column(String(160))
    backup_model: Mapped[str | None] = mapped_column(String(160))
    max_input_tokens: Mapped[int | None] = mapped_column(Integer)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_tier: Mapped[str] = mapped_column(String(40), nullable=False)
    latency_tier: Mapped[str] = mapped_column(String(40), nullable=False)
    critical_path_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    requires_human_approval_for_premium: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    route_priority: Mapped[int] = mapped_column(Integer, nullable=False)
    real_execution_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "router_profile_id", "lane_name", name="uq_llm_router_lanes_profile_lane"
        ),
        Index("ix_llm_router_lanes_lane_name", "lane_name"),
        Index("ix_llm_router_lanes_profile_id", "router_profile_id"),
    )


class LLMModelProfile(Base):
    __tablename__ = "llm_model_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_key: Mapped[str] = mapped_column(
        String(80), nullable=False, default="OLLAMA"
    )
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    model_role: Mapped[str] = mapped_column(String(80), nullable=False)
    lane_names: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    critical_path_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "provider_key", "model_id", name="uq_llm_model_profiles_provider_model"
        ),
        Index("ix_llm_model_profiles_model_id", "model_id"),
        Index("ix_llm_model_profiles_provider_key", "provider_key"),
    )


class LLMRouteAttempt(Base):
    __tablename__ = "llm_route_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    router_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_router_profiles.id"), nullable=False
    )
    lane_name: Mapped[str] = mapped_column(String(160), nullable=False)
    requested_task_type: Mapped[str | None] = mapped_column(String(160))
    selected_model: Mapped[str] = mapped_column(String(160), nullable=False)
    fallback_level: Mapped[str] = mapped_column(String(40), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    prompt_eval_count: Mapped[int | None] = mapped_column(Integer)
    eval_count: Mapped[int | None] = mapped_column(Integer)
    total_duration_ms: Mapped[int | None] = mapped_column(Integer)
    load_duration_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_eval_duration_ms: Mapped[int | None] = mapped_column(Integer)
    eval_duration_ms: Mapped[int | None] = mapped_column(Integer)
    provider_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_attempts.id")
    )
    llm_run_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_run_snapshots.id")
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_llm_route_attempts_profile_id", "router_profile_id"),
        Index("ix_llm_route_attempts_lane_name", "lane_name"),
        Index("ix_llm_route_attempts_status", "status"),
        Index("ix_llm_route_attempts_created_at", "created_at"),
    )


class ContentDerivativeGraphEdge(Base):
    __tablename__ = "content_derivative_graph_edges"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    parent_video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    parent_uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_videos.id")
    )
    derivative_video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    derivative_uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_videos.id")
    )
    derivative_type: Mapped[str] = mapped_column(String(40), nullable=False)
    transformation_summary: Mapped[str] = mapped_column(Text, nullable=False)
    new_value_added: Mapped[str | None] = mapped_column(Text)
    originality_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    reused_runtime_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    publish_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    policy_risk_level: Mapped[str | None] = mapped_column(String(40))
    rights_risk_level: Mapped[str | None] = mapped_column(String(40))
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_derivative_edges_company_id", "company_id"),
        Index("ix_derivative_edges_channel_id", "channel_workspace_id"),
        Index("ix_derivative_edges_parent_project", "parent_video_project_id"),
        Index("ix_derivative_edges_parent_uploaded", "parent_uploaded_video_id"),
        Index("ix_derivative_edges_type", "derivative_type"),
    )


class ShortCandidate(Base):
    __tablename__ = "short_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    parent_video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    parent_voice_timeline_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_timeline_snapshots.id")
    )
    parent_caption_track_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("caption_track_snapshots.id")
    )
    parent_visual_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("visual_plan_snapshots.id")
    )
    start_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    caption_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    core_idea: Mapped[str] = mapped_column(Text, nullable=False)
    hook_line: Mapped[str] = mapped_column(Text, nullable=False)
    standalone_summary: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_title: Mapped[str | None] = mapped_column(Text)
    overlay_text: Mapped[str | None] = mapped_column(Text)
    crop_strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    visual_source: Mapped[str] = mapped_column(String(40), nullable=False)
    candidate_state: Mapped[str] = mapped_column(
        String(40), nullable=False, default="GENERATED"
    )
    policy_risk_level: Mapped[str | None] = mapped_column(String(40))
    rights_risk_level: Mapped[str | None] = mapped_column(String(40))
    production_cost_estimate: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_short_candidates_company_id", "company_id"),
        Index("ix_short_candidates_channel_id", "channel_workspace_id"),
        Index("ix_short_candidates_parent_project", "parent_video_project_id"),
        Index("ix_short_candidates_state", "candidate_state"),
    )


class ShortCandidateScore(Base):
    __tablename__ = "short_candidate_scores"

    id: Mapped[uuid.UUID] = uuid_pk()
    short_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("short_candidates.id"), nullable=False
    )
    hook_strength: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    standalone_clarity: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    insight_density: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    visual_punch: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    audience_relevance: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    bridge_value: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    production_reuse_saving: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False
    )
    context_dependency_penalty: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False
    )
    policy_risk_penalty: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    generic_template_penalty: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False
    )
    total_score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    score_version: Mapped[str] = mapped_column(String(40), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_short_candidate_scores_candidate_id", "short_candidate_id"),
        Index("ix_short_candidate_scores_total", "total_score"),
    )


class ShortRenderPlan(Base):
    __tablename__ = "short_render_plans"

    id: Mapped[uuid.UUID] = uuid_pk()
    short_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("short_candidates.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    target_platform: Mapped[str] = mapped_column(String(40), nullable=False)
    target_aspect_ratio: Mapped[str] = mapped_column(
        String(20), nullable=False, default="9:16"
    )
    target_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    voice_source: Mapped[str] = mapped_column(String(40), nullable=False)
    caption_style_ref: Mapped[str | None] = mapped_column(Text)
    visual_plan: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    render_state: Mapped[str] = mapped_column(String(40), nullable=False)
    blocker_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_short_render_plans_candidate_id", "short_candidate_id"),
        Index("ix_short_render_plans_platform", "target_platform"),
        Index("ix_short_render_plans_state", "render_state"),
    )


class PromoteShortToLongCandidate(Base):
    __tablename__ = "promote_short_to_long_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    source_short_uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_videos.id")
    )
    source_short_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("short_candidates.id")
    )
    winning_hook: Mapped[str] = mapped_column(Text, nullable=False)
    audience_signal: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    suggested_long_topic: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_outline: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    expected_watch_hour_potential: Mapped[str] = mapped_column(
        String(40), nullable=False
    )
    confidence_label: Mapped[str | None] = mapped_column(String(40))
    risk_level: Mapped[str | None] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_promote_short_to_long_company_id", "company_id"),
        Index("ix_promote_short_to_long_channel_id", "channel_workspace_id"),
        Index("ix_promote_short_to_long_state", "state"),
    )


class ReusableArtifact(Base):
    __tablename__ = "reusable_artifacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id")
    )
    artifact_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    source_provider: Mapped[str | None] = mapped_column(String(160))
    license_status: Mapped[str] = mapped_column(String(80), nullable=False)
    rights_envelope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reuse_scope: Mapped[str] = mapped_column(String(40), nullable=False)
    reuse_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_reuse_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    cooldown_days: Mapped[int | None] = mapped_column(Integer)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_video_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_reusable_artifacts_company_id", "company_id"),
        Index("ix_reusable_artifacts_channel_id", "channel_workspace_id"),
        Index("ix_reusable_artifacts_hash", "content_hash"),
        Index("ix_reusable_artifacts_state", "state"),
    )


class AssetReuseIndexEntry(Base):
    __tablename__ = "asset_reuse_index_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    reusable_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reusable_artifacts.id"), nullable=False
    )
    scene_requirement_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    match_reason: Mapped[str] = mapped_column(Text, nullable=False)
    match_score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    last_selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_asset_reuse_entries_artifact_id", "reusable_artifact_id"),
        Index("ix_asset_reuse_entries_requirement_hash", "scene_requirement_hash"),
    )


class DerivativeOriginalityCheck(Base):
    __tablename__ = "derivative_originality_checks"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    content_derivative_edge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_derivative_graph_edges.id")
    )
    short_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("short_candidates.id")
    )
    derivative_type: Mapped[str] = mapped_column(String(40), nullable=False)
    standalone_value_ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    new_value_added_ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    reused_runtime_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    template_repetition_risk: Mapped[str | None] = mapped_column(String(40))
    generic_stock_risk: Mapped[str | None] = mapped_column(String(40))
    commentary_or_context_added: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    policy_flags: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    rights_flags: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    result: Mapped[str] = mapped_column(String(40), nullable=False)
    operator_summary: Mapped[str] = mapped_column(Text, nullable=False)
    technical_appendix: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_derivative_originality_company_id", "company_id"),
        Index("ix_derivative_originality_short_id", "short_candidate_id"),
        Index("ix_derivative_originality_result", "result"),
    )


class OriginalityBudget(Base):
    __tablename__ = "originality_budgets"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id")
    )
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_videos.id")
    )
    new_script_ratio: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    new_narrative_angle_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    new_diagram_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reused_runtime_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    same_template_recent_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    same_stock_clip_recent_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    derivative_count_from_parent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    originality_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    result: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_originality_budgets_company_id", "company_id"),
        Index("ix_originality_budgets_project_id", "video_project_id"),
        Index("ix_originality_budgets_result", "result"),
    )


class DerivativeReleasePlan(Base):
    __tablename__ = "derivative_release_plans"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    parent_video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    parent_uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_videos.id")
    )
    max_shorts_per_long: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    min_spacing_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    preferred_publish_order: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    platform_surface: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    bridge_strategy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    avoid_same_day_spam: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    release_state: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_derivative_release_plans_company_id", "company_id"),
        Index("ix_derivative_release_plans_parent_project", "parent_video_project_id"),
        Index("ix_derivative_release_plans_state", "release_state"),
    )


class CrossPlatformFunnelPackage(Base):
    __tablename__ = "cross_platform_funnel_packages"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    parent_video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    parent_uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_videos.id")
    )
    youtube_long_package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publish_handoff_packages.id")
    )
    selected_short_candidate_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    youtube_shorts_package_status: Mapped[str | None] = mapped_column(String(40))
    tiktok_package_status: Mapped[str | None] = mapped_column(String(40))
    facebook_reels_package_status: Mapped[str | None] = mapped_column(String(40))
    bridge_strategy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    package_state: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_cross_platform_funnel_packages_company_id", "company_id"),
        Index(
            "ix_cross_platform_funnel_packages_parent_project",
            "parent_video_project_id",
        ),
        Index("ix_cross_platform_funnel_packages_state", "package_state"),
    )


class UploadCard(Base):
    __tablename__ = "upload_cards"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    short_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("short_candidates.id")
    )
    render_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("short_render_plans.id")
    )
    file_ref: Mapped[str | None] = mapped_column(Text)
    title_internal: Mapped[str] = mapped_column(Text, nullable=False)
    hook_line: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    cta_type: Mapped[str] = mapped_column(String(40), nullable=False)
    cta_text: Mapped[str | None] = mapped_column(Text)
    pinned_comment: Mapped[str | None] = mapped_column(Text)
    ai_disclosure_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    ai_disclosure_reason: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    music_policy: Mapped[str] = mapped_column(String(40), nullable=False)
    cover_frame_suggestion: Mapped[str | None] = mapped_column(Text)
    human_notes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    paste_back_required_fields: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    card_state: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_upload_cards_company_id", "company_id"),
        Index("ix_upload_cards_channel_id", "channel_workspace_id"),
        Index("ix_upload_cards_short_candidate_id", "short_candidate_id"),
        Index("ix_upload_cards_platform", "platform"),
        Index("ix_upload_cards_state", "card_state"),
    )


class HumanUploadTask(Base):
    __tablename__ = "human_upload_tasks"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    upload_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("upload_cards.id")
    )
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    first_scripted_video_package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("first_scripted_video_packages.id")
    )
    publish_package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publish_handoff_packages.id")
    )
    destination: Mapped[str] = mapped_column(
        String(40), nullable=False, default="YOUTUBE"
    )
    target_platform: Mapped[str] = mapped_column(String(40), nullable=False)
    task_state: Mapped[str] = mapped_column(String(40), nullable=False)
    upload_card_ref: Mapped[str | None] = mapped_column(Text)
    title_snapshot: Mapped[str | None] = mapped_column(Text)
    description_snapshot: Mapped[str | None] = mapped_column(Text)
    thumbnail_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    subtitle_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    required_assets: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    checklist: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    required_checklist: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    scheduled_time_suggestion: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    actual_uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploaded_videos.id")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    operator_note: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    final_review_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_review_candidates.id")
    )
    final_video_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_video_decisions.id")
    )
    final_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id")
    )
    final_media_file_ref: Mapped[str | None] = mapped_column(Text)
    reviewed_checksum: Mapped[str | None] = mapped_column(String(64))
    production_package_artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id")
    )
    production_package_hash: Mapped[str | None] = mapped_column(String(64))
    destination_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    destination_binding_fingerprint: Mapped[str | None] = mapped_column(String(64))
    channel_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profile_versions.id")
    )
    policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id")
    )
    production_lane: Mapped[str | None] = mapped_column(String(40))
    content_mode: Mapped[str | None] = mapped_column(String(40))
    series_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id")
    )
    series_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_runs.id")
    )
    episode_number: Mapped[int | None] = mapped_column(Integer)
    standalone_reason_code: Mapped[str | None] = mapped_column(String(160))
    parent_video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    parent_final_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id")
    )
    archive_object_ref: Mapped[str | None] = mapped_column(Text)
    selected_file_name: Mapped[str | None] = mapped_column(Text)
    selected_file_ref: Mapped[str | None] = mapped_column(Text)
    selected_file_checksum: Mapped[str | None] = mapped_column(String(64))
    attested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    canceled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "final_video_decision_id",
            name="uq_human_upload_tasks_final_video_decision_id",
        ),
        UniqueConstraint(
            "cancel_command_id",
            name="uq_human_upload_tasks_cancel_command_id",
        ),
        CheckConstraint(
            "schema_version in ('v1','v2')",
            name="ck_human_upload_tasks_schema_version",
        ),
        CheckConstraint(
            "task_state in "
            "('READY','UPLOADED','NEEDS_FIX','SKIPPED','CANCELLED',"
            "'READY_FOR_HUMAN_UPLOAD','HUMAN_UPLOAD_IN_PROGRESS',"
            "'UPLOADED_WAITING_BACKFILL','BACKFILLED_WAITING_VERIFICATION',"
            "'UPLOADED_VERIFIED','UPLOADED_UNVERIFIED','BLOCKED',"
            "'READY_FOR_OPERATOR','IN_PROGRESS','AWAITING_CONFIRMATION',"
            "'VERIFIED','CANCELED')",
            name="ck_human_upload_tasks_ck_human_upload_tasks_state",
        ),
        CheckConstraint(
            "target_platform in "
            "('YOUTUBE_LONG','YOUTUBE_SHORTS','YOUTUBE','TIKTOK',"
            "'FACEBOOK_REELS')",
            name="ck_human_upload_tasks_target_platform",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(upload_card_id is null "
            "and first_scripted_video_package_id is null "
            "and publish_package_id is null "
            "and final_review_candidate_id is not null "
            "and final_video_decision_id is not null "
            "and final_media_ref_id is not null "
            "and final_media_file_ref is not null "
            "and reviewed_checksum ~ '^[0-9a-f]{64}$' "
            "and production_package_artifact_version_id is not null "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and destination_binding_id is not null "
            "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
            "and channel_profile_version_id is not null "
            "and policy_snapshot_id is not null "
            "and production_lane in "
            "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
            "and content_mode in ('SERIES_EPISODE','STANDALONE') "
            "and archive_object_ref is not null "
            "and task_state in "
            "('READY_FOR_OPERATOR','IN_PROGRESS','AWAITING_CONFIRMATION',"
            "'VERIFIED','CANCELED'))",
            name="ck_human_upload_tasks_v2_binding",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "((content_mode = 'SERIES_EPISODE' "
            "and series_plan_id is not null "
            "and series_run_id is not null "
            "and episode_number > 0 "
            "and standalone_reason_code is null) "
            "or (content_mode = 'STANDALONE' "
            "and series_plan_id is null "
            "and series_run_id is null "
            "and episode_number is null "
            "and standalone_reason_code is not null))",
            name="ck_human_upload_tasks_v2_assignment",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(task_state in ('READY_FOR_OPERATOR','CANCELED')) or "
            "(selected_file_name is not null "
            "and selected_file_ref is not null "
            "and selected_file_checksum = reviewed_checksum "
            "and attested_by_user_id is not null "
            "and attested_at is not null "
            "and started_by_user_id is not null "
            "and started_at is not null)",
            name="ck_human_upload_tasks_v2_attestation",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(production_lane <> 'LONG_DERIVED_SHORT') or "
            "(parent_video_project_id is not null "
            "and parent_final_media_ref_id is not null)",
            name="ck_human_upload_tasks_v2_parent_lineage",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(task_state <> 'VERIFIED') or "
            "(actual_uploaded_video_id is not null and completed_at is not null)",
            name="ck_human_upload_tasks_v2_verified",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(task_state <> 'CANCELED') or "
            "(cancel_command_id is not null "
            "and canceled_by_user_id is not null "
            "and canceled_at is not null)",
            name="ck_human_upload_tasks_v2_canceled",
        ),
        Index("ix_human_upload_tasks_company_id", "company_id"),
        Index("ix_human_upload_tasks_channel_id", "channel_workspace_id"),
        Index("ix_human_upload_tasks_card_id", "upload_card_id"),
        Index("ix_human_upload_tasks_video_project_id", "video_project_id"),
        Index(
            "ix_human_upload_tasks_first_package_id", "first_scripted_video_package_id"
        ),
        Index("ix_human_upload_tasks_publish_package_id", "publish_package_id"),
        Index(
            "ix_human_upload_tasks_final_review_candidate_id",
            "final_review_candidate_id",
        ),
        Index("ix_human_upload_tasks_final_media_ref_id", "final_media_ref_id"),
        Index(
            "ix_human_upload_tasks_production_package",
            "production_package_artifact_version_id",
        ),
        Index("ix_human_upload_tasks_series_run_id", "series_run_id"),
        Index("ix_human_upload_tasks_destination", "destination"),
        Index("ix_human_upload_tasks_state", "task_state"),
    )


class UsageSavingsLedgerEntry(Base):
    __tablename__ = "usage_savings_ledger_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id")
    )
    video_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id")
    )
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    estimated_cost_without_reuse: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    actual_cost_with_reuse: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    saved_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    saved_tokens: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    saved_ai_video_seconds: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    saved_tts_characters: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_usage_savings_company_id", "company_id"),
        Index("ix_usage_savings_project_id", "video_project_id"),
        Index("ix_usage_savings_created_at", "created_at"),
    )
