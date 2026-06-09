from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.config.settings import get_settings
from app.db.base import Base


class EmbeddingVectorType(TypeDecorator):
    impl = JSON
    cache_ok = True

    def __init__(self) -> None:
        super().__init__()
        self.dimension = get_settings().embedding_dimension

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector

                return dialect.type_descriptor(Vector(self.dimension))
            except ImportError:
                return dialect.type_descriptor(JSON())
        return dialect.type_descriptor(JSON())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("company"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    default_language: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    workspaces: Mapped[list["ChannelWorkspace"]] = relationship(back_populates="company")


class ChannelWorkspace(TimestampMixin, Base):
    __tablename__ = "channel_workspaces"
    __table_args__ = (
        UniqueConstraint("id", "company_id", name="uq_channel_workspaces_id_company"),
        Index("ix_channel_workspaces_company_workspace", "company_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("workspace"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_name: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_channel_id: Mapped[str | None] = mapped_column(String(255))
    channel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_url: Mapped[str | None] = mapped_column(String(1024))
    niche: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    target_market: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    follower_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_video_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    monetization_status: Mapped[str] = mapped_column(String(32), default="NOT_STARTED", nullable=False)
    baseline_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    maturity_stage: Mapped[str] = mapped_column(String(64), default="NEW_CHANNEL", nullable=False)

    company: Mapped[Company] = relationship(back_populates="workspaces")
    profile: Mapped["WorkspaceProfile"] = relationship(back_populates="workspace", uselist=False)


class WorkspaceProfile(TimestampMixin, Base):
    __tablename__ = "workspace_profiles"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    brand_voice: Mapped[str | None] = mapped_column(Text)
    target_audience: Mapped[str | None] = mapped_column(Text)
    forbidden_topics: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    preferred_formats: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    target_market: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    monetization_thesis_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    platform_rules_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    human_review_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_workflow_mode: Mapped[str] = mapped_column(
        String(64), default="MONETIZATION_VALIDATION_MODE", nullable=False
    )
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    workspace: Mapped[ChannelWorkspace] = relationship(back_populates="profile")


class WorkspaceMaturitySnapshot(Base):
    __tablename__ = "workspace_maturity_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("maturity"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    maturity_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    follower_count: Mapped[int] = mapped_column(Integer, nullable=False)
    published_video_count: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WorkspaceBaseline(TimestampMixin, Base):
    __tablename__ = "workspace_baselines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("baseline"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    metric_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WorkspaceBudgetPolicy(TimestampMixin, Base):
    __tablename__ = "workspace_budget_policies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("budget"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    cost_per_video_target: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    hard_max_per_video: Mapped[float] = mapped_column(Float, default=2.5, nullable=False)
    daily_budget_limit: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class WorkspaceDailyPlan(TimestampMixin, Base):
    __tablename__ = "workspace_daily_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("daily_plan"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    plan_date: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="CREATED", nullable=False)


class WorkspaceOperationalConstitution(Base):
    __tablename__ = "workspace_operational_constitutions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("constitution"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_versions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class VideoProject(TimestampMixin, Base):
    __tablename__ = "video_projects"
    __table_args__ = (
        UniqueConstraint("id", "company_id", "workspace_id", name="uq_video_projects_id_company_workspace"),
        Index("ix_video_projects_company_workspace", "company_id", "workspace_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("project"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    topic: Mapped[str | None] = mapped_column(Text)
    workflow_mode: Mapped[str | None] = mapped_column(String(64))
    current_state: Mapped[str] = mapped_column(String(64), default="IDEA_FOUND", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class VideoState(Base):
    __tablename__ = "video_states"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("state"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id"), index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    event_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("agent_run"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    node_name: Mapped[str] = mapped_column(String(128), nullable=False)
    input_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="SUCCESS", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AuthorityReview(Base):
    __tablename__ = "authority_reviews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("authority"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    gate: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HumanReview(Base):
    __tablename__ = "human_reviews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("human_review"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    reviewer: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ReviewTask(TimestampMixin, Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        Index("ix_review_tasks_company_workspace_project", "company_id", "workspace_id", "project_id"),
        Index("ix_review_tasks_scope_status", "company_id", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("review_task"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="OPEN", nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewAction(Base):
    __tablename__ = "review_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("review_action"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    review_task_id: Mapped[str] = mapped_column(ForeignKey("review_tasks.id"), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class UploadedVideo(TimestampMixin, Base):
    __tablename__ = "uploaded_videos"
    __table_args__ = (
        Index("ix_uploaded_videos_company_workspace_project", "company_id", "workspace_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("uploaded_video"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_video_id: Mapped[str | None] = mapped_column(String(255))
    video_url: Mapped[str | None] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    thumbnail_uri: Mapped[str | None] = mapped_column(String(1024))
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    visibility: Mapped[str] = mapped_column(String(32), default="PUBLIC", nullable=False)
    monetization_status: Mapped[str] = mapped_column(String(64), default="UNKNOWN", nullable=False)
    upload_status: Mapped[str] = mapped_column(String(64), default="IMPORTED", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class VideoArtifact(TimestampMixin, Base):
    __tablename__ = "video_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("artifact"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id"), index=True, nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    uri: Mapped[str | None] = mapped_column(String(1024))
    content_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class RenderTimeline(TimestampMixin, Base):
    __tablename__ = "render_timelines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("render_timeline"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_projects.id"), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    timeline_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AssetLibrary(TimestampMixin, Base):
    __tablename__ = "asset_library"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("asset"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), default="workspace_only", nullable=False)
    source_project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    scene_id: Mapped[str | None] = mapped_column(String(64))
    topic_cluster: Mapped[str | None] = mapped_column(String(255))
    semantic_tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    visual_style: Mapped[str | None] = mapped_column(String(255))
    character_pack_id: Mapped[str | None] = mapped_column(String(255))
    duration: Mapped[float | None] = mapped_column(Float)
    aspect_ratio: Mapped[str | None] = mapped_column(String(32))
    qa_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    compliance_status: Mapped[str] = mapped_column(String(64), default="PENDING", nullable=False)
    reuse_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class MemoryItem(TimestampMixin, Base):
    __tablename__ = "memory_items"
    __table_args__ = (
        Index("ix_memory_items_scope_workspace_family", "company_id", "scope", "workspace_id", "family"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("memory"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("channel_workspaces.id"), index=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    family: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_video_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="ACTIVE", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[list | None] = mapped_column(EmbeddingVectorType())
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    embedding_version: Mapped[str | None] = mapped_column(String(64))
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EditorialPlaybook(TimestampMixin, Base):
    __tablename__ = "editorial_playbooks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("playbook"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), default="seed_playbook_v1", nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ComplianceReport(TimestampMixin, Base):
    __tablename__ = "compliance_reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("compliance"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    report_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class QAReport(TimestampMixin, Base):
    __tablename__ = "qa_reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("qa"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    qa_type: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class CostEvent(Base):
    __tablename__ = "cost_events"
    __table_args__ = (
        Index("ix_cost_events_company_workspace_project", "company_id", "workspace_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cost"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    node_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), default="mock", nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    media_units: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    raw_usage_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        Index("ix_analytics_snapshots_company_workspace_project", "company_id", "workspace_id", "project_id"),
        Index("ix_analytics_snapshots_company_workspace_video", "company_id", "workspace_id", "uploaded_video_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("analytics"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    uploaded_video_id: Mapped[str | None] = mapped_column(ForeignKey("uploaded_videos.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    hours_since_publish: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ctr: Mapped[float | None] = mapped_column(Float)
    avg_view_duration: Mapped[float | None] = mapped_column(Float)
    avg_percentage_viewed: Mapped[float | None] = mapped_column(Float)
    subscribers_gained: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    likes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shares: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_revenue: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rpm: Mapped[float | None] = mapped_column(Float)
    traffic_source_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    geography_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class RetentionSegment(Base):
    __tablename__ = "retention_segments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("retention"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    uploaded_video_id: Mapped[str | None] = mapped_column(ForeignKey("uploaded_videos.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    segment_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DiagnosisEvent(Base):
    __tablename__ = "diagnosis_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("diagnosis"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("channel_workspaces.id"), index=True, nullable=False)
    uploaded_video_id: Mapped[str | None] = mapped_column(ForeignKey("uploaded_videos.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("video_projects.id"), index=True)
    diagnosis_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
