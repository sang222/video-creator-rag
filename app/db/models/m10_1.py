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
        String(80), nullable=False, default="OPENAI"
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
    reasoning_effort: Mapped[str] = mapped_column(
        String(40), nullable=False, default="low"
    )
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
        String(80), nullable=False, default="OPENAI"
    )
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    model_role: Mapped[str] = mapped_column(String(80), nullable=False)
    lane_names: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    critical_path_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    capability_blob: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    pricing_snapshot_version: Mapped[str | None] = mapped_column(String(160))
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
    reasoning_effort: Mapped[str | None] = mapped_column(String(40))
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
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


class HumanUploadTask(Base):
    __tablename__ = "human_upload_tasks"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
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
    publish_metadata_ref: Mapped[str | None] = mapped_column(Text)
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
            "target_platform in ('YOUTUBE_LONG','YOUTUBE')",
            name="ck_human_upload_tasks_target_platform",
        ),
        CheckConstraint(
            "(schema_version = 'v1') or "
            "(first_scripted_video_package_id is null "
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
            "and production_lane = 'LONG_FORM' "
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
