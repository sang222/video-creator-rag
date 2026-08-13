"""Durable AI-only visual production and replacement authorities.

The rows in this module deliberately separate provider-created visual content
from deterministic FFmpeg presentation.  Provider effects are one-per-scene,
while the aggregate run and manifest bind those effects to one exact workflow
and production package.  Historical native media remains immutable; a
rerender is represented by an append-only replacement authority and lineage.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
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
    event,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


AI_VISUAL_POLICY_VERSION = "vcos.production-visual-policy.ai-only.v1"
AI_VISUAL_ROUTES = ("AI_IMAGE", "AI_VIDEO")
AI_VISUAL_RUN_STATES = (
    "AUTHORIZED",
    "PLANNED",
    "GENERATING",
    "ASSETS_VERIFIED",
    "RENDERING",
    "RENDERED",
    "QC_RUNNING",
    "QC_VERIFIED",
    "ARCHIVING",
    "ARCHIVED",
    "FINAL_REVIEW_READY",
    "BLOCKED",
)
AI_VISUAL_RUN_PHASES = (
    "AUTHORIZE",
    "PLAN",
    "GENERATE",
    "MANIFEST",
    "RENDER",
    "QC",
    "ARCHIVE",
    "FINALIZE",
)
AI_VISUAL_EFFECT_STATES = (
    "PREPARED",
    "SUBMITTING",
    "OPERATION_RECORDED",
    "POLLING",
    "RESPONSE_CAPTURED",
    "DOWNLOADED",
    "NORMALIZED",
    "VERIFIED",
    "FAILED_DEFINITIVE",
    "FAILED_UNCERTAIN",
    "BLOCKED",
)


class AIVisualRerenderAuthority(Base):
    """Immutable one-video authority to replace rejected native visuals only."""

    __tablename__ = "ai_visual_rerender_authorities"

    id: Mapped[uuid.UUID] = uuid_pk()
    authorized_visual_production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    source_workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_workflow_runs.id"), nullable=False
    )
    replacement_workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_workflow_runs.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    production_package_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    production_package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    production_readiness_receipt_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    production_readiness_receipt_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    script_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    script_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_narration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    audio_ref: Mapped[str] = mapped_column(Text, nullable=False)
    audio_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    audio_duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timed_words_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    timed_words_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    caption_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    caption_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    caption_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    subtitle_qc_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    subtitle_qc_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rejected_final_media_ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("final_media_refs.id"),
        nullable=False,
        unique=True,
    )
    rejected_final_media_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rejected_final_review_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("final_review_candidates.id"),
        nullable=False,
        unique=True,
    )
    rejected_final_review_candidate_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    rejected_visual_policy: Mapped[str] = mapped_column(String(120), nullable=False)
    production_visual_policy_version: Mapped[str] = mapped_column(
        String(120), nullable=False
    )
    production_visual_policy_ref: Mapped[str] = mapped_column(Text, nullable=False)
    production_visual_policy_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    budget_reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mr1_monthly_budget_reservations.id"),
        nullable=False,
    )
    budget_reservation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    budget_authority_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    maximum_total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False
    )
    maximum_scene_count: Mapped[int] = mapped_column(Integer, nullable=False)
    maximum_image_submissions: Mapped[int] = mapped_column(Integer, nullable=False)
    maximum_video_submissions: Mapped[int] = mapped_column(Integer, nullable=False)
    maximum_tts_submissions: Mapped[int] = mapped_column(Integer, nullable=False)
    maximum_forced_alignment_submissions: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    narration_timing_recovery_authority_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("v2_narration_timing_recovery_authorities.id"),
        nullable=False,
    )
    narration_timing_recovery_authority_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    narration_timing_recovery_receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("v2_narration_timing_recovery_receipts.id"),
        nullable=False,
    )
    narration_timing_recovery_receipt_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    automatic_publish: Mapped[bool] = mapped_column(Boolean, nullable=False)
    authorized_by_actor_type: Mapped[str] = mapped_column(String(80), nullable=False)
    authorized_by_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    authorized_by_actor_role: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "source_workflow_run_id",
            name="uq_ai_visual_rerender_authority_source_workflow",
        ),
        UniqueConstraint(
            "replacement_workflow_run_id",
            name="uq_ai_visual_rerender_authority_replacement_workflow",
        ),
        CheckConstraint(
            "production_visual_policy_version = "
            f"'{AI_VISUAL_POLICY_VERSION}' and "
            "source_workflow_run_id <> replacement_workflow_run_id and "
            "rejected_visual_policy = 'NATIVE_EXPLANATORY_DIAGRAM' and "
            "maximum_tts_submissions = 0 and "
            "maximum_forced_alignment_submissions = 0 and "
            "maximum_scene_count between 1 and 256 and "
            "maximum_image_submissions between 0 and maximum_scene_count and "
            "maximum_video_submissions between 0 and maximum_scene_count and "
            "maximum_image_submissions + maximum_video_submissions >= 1 and "
            "maximum_total_cost_usd > 0 and automatic_publish = false",
            name="ck_ai_visual_rerender_authority_bounds",
        ),
        CheckConstraint(
            "production_package_hash ~ '^[0-9a-f]{64}$' and "
            "production_readiness_receipt_hash ~ '^[0-9a-f]{64}$' and "
            "script_content_hash ~ '^[0-9a-f]{64}$' and "
            "canonical_narration_hash ~ '^[0-9a-f]{64}$' and "
            "audio_checksum ~ '^[0-9a-f]{64}$' and "
            "timed_words_hash ~ '^[0-9a-f]{64}$' and "
            "caption_hash ~ '^[0-9a-f]{64}$' and "
            "caption_checksum ~ '^[0-9a-f]{64}$' and "
            "subtitle_qc_hash ~ '^[0-9a-f]{64}$' and "
            "narration_timing_recovery_authority_hash ~ '^[0-9a-f]{64}$' and "
            "narration_timing_recovery_receipt_hash ~ '^[0-9a-f]{64}$' and "
            "rejected_final_media_hash ~ '^[0-9a-f]{64}$' and "
            "rejected_final_review_candidate_hash ~ '^[0-9a-f]{64}$' and "
            "production_visual_policy_hash ~ '^[0-9a-f]{64}$' and "
            "budget_authority_hash ~ '^[0-9a-f]{64}$' and "
            "authority_hash ~ '^[0-9a-f]{64}$' and audio_duration_ms > 0",
            name="ck_ai_visual_rerender_authority_hashes",
        ),
        Index("ix_ai_visual_rerender_authority_project", "video_project_id"),
    )


class AIVisualProductionRun(Base):
    """Mutable sequencing projection over immutable AI visual authorities."""

    __tablename__ = "ai_visual_production_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_workflow_runs.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    rerender_authority_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_visual_rerender_authorities.id"), unique=True
    )
    execution_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    production_package_artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False
    )
    production_package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    production_visual_policy_version: Mapped[str] = mapped_column(
        String(120), nullable=False
    )
    production_visual_policy_ref: Mapped[str] = mapped_column(Text, nullable=False)
    production_visual_policy_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    source_timeline_ref: Mapped[str] = mapped_column(Text, nullable=False)
    source_timeline_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    audio_ref: Mapped[str] = mapped_column(Text, nullable=False)
    audio_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    audio_duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timed_words_ref: Mapped[str] = mapped_column(Text, nullable=False)
    timed_words_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    caption_ref: Mapped[str] = mapped_column(Text, nullable=False)
    caption_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    caption_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    subtitle_qc_ref: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle_qc_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mr1_monthly_budget_reservations.id"),
        nullable=False,
    )
    budget_reservation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    budget_authority_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    current_phase: Mapped[str] = mapped_column(String(40), nullable=False)
    style_bible_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    style_bible_hash: Mapped[str | None] = mapped_column(String(64))
    scene_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scene_plan_hash: Mapped[str | None] = mapped_column(String(64))
    asset_manifest_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    asset_manifest_hash: Mapped[str | None] = mapped_column(String(64))
    motion_grammar_ref: Mapped[str | None] = mapped_column(Text)
    motion_grammar_hash: Mapped[str | None] = mapped_column(String(64))
    effect_plan_ref: Mapped[str | None] = mapped_column(Text)
    effect_plan_hash: Mapped[str | None] = mapped_column(String(64))
    render_output_ref: Mapped[str | None] = mapped_column(Text)
    render_output_checksum: Mapped[str | None] = mapped_column(String(64))
    technical_qc_ref: Mapped[str | None] = mapped_column(Text)
    technical_qc_hash: Mapped[str | None] = mapped_column(String(64))
    creative_qc_ref: Mapped[str | None] = mapped_column(Text)
    creative_qc_hash: Mapped[str | None] = mapped_column(String(64))
    cross_modal_qc_ref: Mapped[str | None] = mapped_column(Text)
    cross_modal_qc_hash: Mapped[str | None] = mapped_column(String(64))
    archive_receipt_ref: Mapped[str | None] = mapped_column(Text)
    archive_receipt_hash: Mapped[str | None] = mapped_column(String(64))
    final_media_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_media_refs.id")
    )
    final_review_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("final_review_candidates.id")
    )
    failure_reason_code: Mapped[str | None] = mapped_column(String(160))
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "execution_kind",
            name="uq_ai_visual_production_run_workflow_kind",
        ),
        CheckConstraint(
            "execution_kind in ('NORMAL_PRODUCTION','GOVERNED_RERENDER') and "
            "((execution_kind='NORMAL_PRODUCTION' and rerender_authority_id is null) or "
            "(execution_kind='GOVERNED_RERENDER' and rerender_authority_id is not null))",
            name="ck_ai_visual_production_run_kind",
        ),
        CheckConstraint(
            "state in (" + ",".join(f"'{v}'" for v in AI_VISUAL_RUN_STATES) + ")",
            name="ck_ai_visual_production_run_state",
        ),
        CheckConstraint(
            "current_phase in ("
            + ",".join(f"'{v}'" for v in AI_VISUAL_RUN_PHASES)
            + ") and projection_version > 0",
            name="ck_ai_visual_production_run_phase",
        ),
        CheckConstraint(
            f"production_visual_policy_version = '{AI_VISUAL_POLICY_VERSION}' and "
            "production_package_hash ~ '^[0-9a-f]{64}$' and "
            "production_visual_policy_hash ~ '^[0-9a-f]{64}$' and "
            "source_timeline_hash ~ '^[0-9a-f]{64}$' and "
            "audio_checksum ~ '^[0-9a-f]{64}$' and "
            "timed_words_hash ~ '^[0-9a-f]{64}$' and "
            "caption_hash ~ '^[0-9a-f]{64}$' and "
            "caption_checksum ~ '^[0-9a-f]{64}$' and "
            "subtitle_qc_hash ~ '^[0-9a-f]{64}$' and "
            "budget_authority_hash ~ '^[0-9a-f]{64}$' and audio_duration_ms > 0",
            name="ck_ai_visual_production_run_hashes",
        ),
        Index("ix_ai_visual_production_run_state", "state", "updated_at"),
        Index("ix_ai_visual_production_run_project", "video_project_id"),
    )


class AIVisualStyleBible(Base):
    __tablename__ = "ai_visual_style_bibles"

    id: Mapped[uuid.UUID] = uuid_pk()
    visual_production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_visual_production_runs.id"),
        nullable=False,
        unique=True,
    )
    schema_version: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "schema_version='vcos.video-visual-style-bible.v1' and "
            "content_hash ~ '^[0-9a-f]{64}$' and jsonb_typeof(content)='object'",
            name="ck_ai_visual_style_bible_identity",
        ),
    )


class AIVisualScenePlanSnapshot(Base):
    __tablename__ = "ai_visual_scene_plan_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    visual_production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_visual_production_runs.id"),
        nullable=False,
        unique=True,
    )
    style_bible_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_visual_style_bibles.id"), nullable=False
    )
    style_bible_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(120), nullable=False)
    scene_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_image_scene_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_video_scene_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unique_asset_slot_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "schema_version='vcos.ai-visual-scene-plan-set.v1' and "
            "scene_count > 0 and ai_image_scene_count >= 0 and "
            "ai_video_scene_count >= 0 and unique_asset_slot_count > 0 and "
            "unique_asset_slot_count <= scene_count and "
            "ai_image_scene_count + ai_video_scene_count = scene_count and "
            "style_bible_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$' and jsonb_typeof(content)='object'",
            name="ck_ai_visual_scene_plan_identity",
        ),
    )


class AIVisualAssetEffect(Base):
    """Exactly one bounded provider generation effect for one canonical scene."""

    __tablename__ = "ai_visual_asset_effects"

    id: Mapped[uuid.UUID] = uuid_pk()
    visual_production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_visual_production_runs.id"), nullable=False
    )
    scene_plan_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_visual_scene_plan_snapshots.id"),
        nullable=False,
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_workflow_runs.id"), nullable=False
    )
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_projects.id"), nullable=False
    )
    asset_slot_id: Mapped[str] = mapped_column(String(120), nullable=False)
    scene_id: Mapped[str] = mapped_column(String(120), nullable=False)
    bound_scene_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    bound_scene_plan_hashes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    bound_scene_count: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_asset_owner_scene_id: Mapped[str] = mapped_column(
        String(120), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    route: Mapped[str] = mapped_column(String(40), nullable=False)
    asset_acquisition_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(120), nullable=False)
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_config_version: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    price_catalog_version: Mapped[str] = mapped_column(String(80), nullable=False)
    price_catalog_ref: Mapped[str] = mapped_column(Text, nullable=False)
    price_catalog_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    production_visual_policy_version: Mapped[str] = mapped_column(
        String(120), nullable=False
    )
    production_visual_policy_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    style_bible_ref: Mapped[str] = mapped_column(Text, nullable=False)
    style_bible_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scene_plan_ref: Mapped[str] = mapped_column(Text, nullable=False)
    scene_plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    compiled_prompt_ref: Mapped[str] = mapped_column(Text, nullable=False)
    compiled_prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    compiled_prompt_content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    prompt_compiler_version: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generation_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effect_identity_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    reuse_authority_ref: Mapped[str | None] = mapped_column(Text)
    reuse_authority_hash: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    approval_ref: Mapped[str] = mapped_column(Text, nullable=False)
    approval_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mr1_monthly_budget_reservations.id"),
        nullable=False,
    )
    budget_authority_ref: Mapped[str] = mapped_column(Text, nullable=False)
    budget_authority_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cost_estimate_ref: Mapped[str] = mapped_column(Text, nullable=False)
    cost_estimate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    maximum_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    maximum_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submission_owner_token_hash: Mapped[str | None] = mapped_column(String(64))
    submission_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    provider_operation_id: Mapped[str | None] = mapped_column(Text)
    provider_request_id: Mapped[str | None] = mapped_column(Text)
    provider_response_id: Mapped[str | None] = mapped_column(Text)
    request_journal_ref: Mapped[str | None] = mapped_column(Text)
    request_journal_hash: Mapped[str | None] = mapped_column(String(64))
    response_journal_ref: Mapped[str | None] = mapped_column(Text)
    response_journal_hash: Mapped[str | None] = mapped_column(String(64))
    sanitized_response_hash: Mapped[str | None] = mapped_column(String(64))
    output_ref: Mapped[str | None] = mapped_column(Text)
    output_checksum: Mapped[str | None] = mapped_column(String(64))
    output_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    output_content_type: Mapped[str | None] = mapped_column(String(120))
    output_width: Mapped[int | None] = mapped_column(Integer)
    output_height: Mapped[int | None] = mapped_column(Integer)
    output_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    output_fps: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    output_audio_stream_count: Mapped[int | None] = mapped_column(Integer)
    normalization_ref: Mapped[str | None] = mapped_column(Text)
    normalization_hash: Mapped[str | None] = mapped_column(String(64))
    qc_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    qc_ref: Mapped[str | None] = mapped_column(Text)
    qc_hash: Mapped[str | None] = mapped_column(String(64))
    cost_settlement_basis: Mapped[str | None] = mapped_column(String(80))
    retry_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fallback_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    failure_reason_code: Mapped[str | None] = mapped_column(String(160))
    failure_evidence_hash: Mapped[str | None] = mapped_column(String(64))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "visual_production_run_id",
            "asset_slot_id",
            name="uq_ai_visual_asset_effect_run_slot",
        ),
        UniqueConstraint(
            "visual_production_run_id",
            "ordinal",
            name="uq_ai_visual_asset_effect_run_ordinal",
        ),
        CheckConstraint(
            "route in ('AI_IMAGE','AI_VIDEO') and "
            "asset_acquisition_mode in ('GENERATED','ARCHIVED_AI_REUSE') and "
            "((route='AI_IMAGE' and provider_key='google_gemini_image') or "
            "(route='AI_VIDEO' and provider_key='google_veo'))",
            name="ck_ai_visual_asset_effect_route_provider",
        ),
        CheckConstraint(
            "state in (" + ",".join(f"'{v}'" for v in AI_VISUAL_EFFECT_STATES) + ")",
            name="ck_ai_visual_asset_effect_state",
        ),
        CheckConstraint(
            "maximum_attempts = 1 and provider_call_count between 0 and 1 and "
            "revision > 0 and ordinal > 0 and bound_scene_count > 0 and "
            "jsonb_typeof(bound_scene_ids)='array' and "
            "jsonb_array_length(bound_scene_ids)=bound_scene_count and "
            "jsonb_typeof(bound_scene_plan_hashes)='array' and "
            "jsonb_array_length(bound_scene_plan_hashes)=bound_scene_count and "
            "estimated_cost_usd > 0 and "
            "maximum_cost_usd >= estimated_cost_usd and "
            "(actual_cost_usd is null or actual_cost_usd >= 0) and "
            "retry_allowed=false and fallback_allowed=false",
            name="ck_ai_visual_asset_effect_bounds",
        ),
        CheckConstraint(
            f"production_visual_policy_version='{AI_VISUAL_POLICY_VERSION}' and "
            "provider_config_hash ~ '^[0-9a-f]{64}$' and "
            "price_catalog_hash ~ '^[0-9a-f]{64}$' and "
            "production_visual_policy_hash ~ '^[0-9a-f]{64}$' and "
            "style_bible_hash ~ '^[0-9a-f]{64}$' and "
            "scene_plan_hash ~ '^[0-9a-f]{64}$' and "
            "compiled_prompt_hash ~ '^[0-9a-f]{64}$' and "
            "compiled_prompt_content_hash ~ '^[0-9a-f]{64}$' and "
            "prompt_hash ~ '^[0-9a-f]{64}$' and "
            "generation_policy_hash ~ '^[0-9a-f]{64}$' and "
            "effect_identity_hash ~ '^[0-9a-f]{64}$' and "
            "request_hash ~ '^[0-9a-f]{64}$' and "
            "approval_hash ~ '^[0-9a-f]{64}$' and "
            "budget_authority_hash ~ '^[0-9a-f]{64}$' and "
            "cost_estimate_hash ~ '^[0-9a-f]{64}$' and "
            "jsonb_typeof(generation_policy)='object'",
            name="ck_ai_visual_asset_effect_hashes",
        ),
        CheckConstraint(
            "(state <> 'VERIFIED') or ("
            "((asset_acquisition_mode='GENERATED' and provider_call_count=1 and "
            "request_journal_ref is not null and request_journal_hash is not null and "
            "response_journal_ref is not null and response_journal_hash is not null and "
            "reuse_authority_ref is null and reuse_authority_hash is null) or "
            "(asset_acquisition_mode='ARCHIVED_AI_REUSE' and provider_call_count=0 and "
            "request_journal_ref is null and response_journal_ref is null and "
            "reuse_authority_ref is not null and reuse_authority_hash is not null)) and "
            "output_ref is not null and output_checksum is not null and "
            "output_size_bytes > 0 and output_width > 0 and output_height > 0 and "
            "qc_ref is not null and qc_hash is not null and completed_at is not null and "
            "cost_settlement_basis is not null and "
            "((route='AI_IMAGE' and output_duration_ms is null) or "
            "(route='AI_VIDEO' and output_duration_ms > 0 and "
            "normalization_ref is not null and normalization_hash is not null and "
            "output_audio_stream_count=0)))",
            name="ck_ai_visual_asset_effect_verified_evidence",
        ),
        Index("ix_ai_visual_asset_effect_state", "state", "updated_at"),
        Index("ix_ai_visual_asset_effect_project", "video_project_id"),
    )


class AIVisualAssetManifest(Base):
    __tablename__ = "ai_visual_asset_manifests"

    id: Mapped[uuid.UUID] = uuid_pk()
    visual_production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_visual_production_runs.id"),
        nullable=False,
        unique=True,
    )
    scene_plan_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_visual_scene_plan_snapshots.id"),
        nullable=False,
        unique=True,
    )
    scene_plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    style_bible_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    motion_grammar_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effect_plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(120), nullable=False)
    scene_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_image_scene_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_video_scene_count: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_image_asset_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_video_asset_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_provider_call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False
    )
    total_actual_or_conservative_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False
    )
    production_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    renderer_primary_visual_generation: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "schema_version='vcos.ai-visual-asset-manifest.v1' and scene_count>0 and "
            "ai_image_scene_count>=0 and ai_video_scene_count>=0 and "
            "ai_image_scene_count+ai_video_scene_count=scene_count and "
            "asset_count>0 and asset_count<=scene_count and "
            "ai_image_asset_count>=0 and ai_video_asset_count>=0 and "
            "ai_image_asset_count+ai_video_asset_count=asset_count and "
            "total_provider_call_count between 0 and asset_count and "
            "total_estimated_cost_usd > 0 and "
            "total_actual_or_conservative_cost_usd >= 0 and "
            "production_eligible=true and renderer_primary_visual_generation=false and "
            "scene_plan_hash ~ '^[0-9a-f]{64}$' and "
            "style_bible_hash ~ '^[0-9a-f]{64}$' and "
            "motion_grammar_hash ~ '^[0-9a-f]{64}$' and "
            "effect_plan_hash ~ '^[0-9a-f]{64}$' and "
            "content_hash ~ '^[0-9a-f]{64}$' and jsonb_typeof(content)='object'",
            name="ck_ai_visual_asset_manifest_identity",
        ),
    )


class AIVisualReplacementLineage(Base):
    """Immutable old-to-new media/candidate replacement receipt."""

    __tablename__ = "ai_visual_replacement_lineages"

    id: Mapped[uuid.UUID] = uuid_pk()
    rerender_authority_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_visual_rerender_authorities.id"),
        nullable=False,
        unique=True,
    )
    visual_production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_visual_production_runs.id"),
        nullable=False,
        unique=True,
    )
    asset_manifest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_visual_asset_manifests.id"),
        nullable=False,
        unique=True,
    )
    asset_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rejected_final_media_ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("final_media_refs.id"),
        nullable=False,
        unique=True,
    )
    replacement_final_media_ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("final_media_refs.id"),
        nullable=False,
        unique=True,
    )
    rejected_final_review_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("final_review_candidates.id"),
        nullable=False,
        unique=True,
    )
    replacement_final_review_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("final_review_candidates.id"),
        nullable=False,
        unique=True,
    )
    replacement_render_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    replacement_archive_receipt_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    automatic_publish: Mapped[bool] = mapped_column(Boolean, nullable=False)
    lineage_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "rejected_final_media_ref_id <> replacement_final_media_ref_id and "
            "rejected_final_review_candidate_id <> replacement_final_review_candidate_id and "
            "automatic_publish=false and "
            "asset_manifest_hash ~ '^[0-9a-f]{64}$' and "
            "replacement_render_checksum ~ '^[0-9a-f]{64}$' and "
            "replacement_archive_receipt_hash ~ '^[0-9a-f]{64}$' and "
            "lineage_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ai_visual_replacement_lineage_identity",
        ),
    )


_IMMUTABLE_MODELS = (
    AIVisualRerenderAuthority,
    AIVisualStyleBible,
    AIVisualScenePlanSnapshot,
    AIVisualAssetManifest,
    AIVisualReplacementLineage,
)


@event.listens_for(Mapper, "before_update")
@event.listens_for(Mapper, "before_delete")
def _prevent_ai_visual_authority_mutation(
    mapper: Mapper, connection: Any, target: Any
) -> None:
    if isinstance(target, _IMMUTABLE_MODELS) and inspect(target).persistent:
        raise ValueError(f"{type(target).__name__.upper()}_IMMUTABLE")
