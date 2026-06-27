import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class PromptTemplateRecord(Base):
    __tablename__ = "prompt_template_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("agent_key", "template_key", "template_version", name="uq_prompt_template_records_identity"),
        Index("ix_prompt_template_records_agent", "agent_key"),
        Index("ix_prompt_template_records_status", "status"),
        Index("ix_prompt_template_records_hash", "prompt_hash"),
    )


class AgentPromptProfile(Base):
    __tablename__ = "agent_prompt_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    default_router_lane: Mapped[str] = mapped_column(String(160), nullable=False)
    allowed_router_lanes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    input_contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    safety_policy_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    common_skill_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    channel_contract_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    market_locale_context_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_agent_prompt_profiles_lane", "default_router_lane"),
        Index("ix_agent_prompt_profiles_status", "status"),
    )


class PromptContractVersion(Base):
    __tablename__ = "prompt_contract_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    schema_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("agent_key", "template_key", "template_version", name="uq_prompt_contract_versions_identity"),
        Index("ix_prompt_contract_versions_schema", "schema_ref", "schema_version"),
        Index("ix_prompt_contract_versions_status", "status"),
    )


class StructuredOutputSchema(Base):
    __tablename__ = "structured_output_schemas"

    id: Mapped[uuid.UUID] = uuid_pk()
    schema_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    dialect: Mapped[str] = mapped_column(String(80), nullable=False)
    json_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("schema_ref", "schema_version", name="uq_structured_output_schemas_identity"),
        Index("ix_structured_output_schemas_status", "status"),
    )


class PromptRenderRun(Base):
    __tablename__ = "prompt_render_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_version: Mapped[str] = mapped_column(String(80), nullable=False)
    rendered_messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    prompt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_context_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    input_payload_ref: Mapped[str | None] = mapped_column(Text)
    output_schema_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    router_lane: Mapped[str] = mapped_column(String(160), nullable=False)
    channel_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_profile_versions.id"))
    compiled_policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"))
    channel_contract_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    compiled_policy_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    market_locale_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    render_vars_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    artifact_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    validation_status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_prompt_render_runs_agent", "agent_key"),
        Index("ix_prompt_render_runs_hash", "prompt_hash", "prompt_context_hash"),
        Index("ix_prompt_render_runs_channel_profile", "channel_profile_version_id"),
        Index("ix_prompt_render_runs_policy_snapshot", "compiled_policy_snapshot_id"),
        Index("ix_prompt_render_runs_created_at", "created_at"),
    )


class PromptAuditSnapshot(Base):
    __tablename__ = "prompt_audit_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_version: Mapped[str] = mapped_column(String(80), nullable=False)
    channel_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_profile_versions.id"))
    compiled_policy_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"))
    prompt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_context_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    router_lane: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_attempt_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    prompt_render_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_render_runs.id"))
    final_output_ref: Mapped[str | None] = mapped_column(Text)
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    repair_attempts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_prompt_audit_snapshots_agent", "agent_key"),
        Index("ix_prompt_audit_snapshots_render_run", "prompt_render_run_id"),
        Index("ix_prompt_audit_snapshots_hash", "prompt_hash", "prompt_context_hash"),
        Index("ix_prompt_audit_snapshots_created_at", "created_at"),
    )


class PromptEvaluationCase(Base):
    __tablename__ = "prompt_evaluation_cases"

    id: Mapped[uuid.UUID] = uuid_pk()
    case_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_fixture_ref: Mapped[str] = mapped_column(Text, nullable=False)
    expected_outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pass_criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_prompt_evaluation_cases_agent", "agent_key"),
        Index("ix_prompt_evaluation_cases_status", "status"),
    )


class PromptEvaluationRun(Base):
    __tablename__ = "prompt_evaluation_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    case_key: Mapped[str] = mapped_column(String(160), nullable=False)
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_version: Mapped[str] = mapped_column(String(80), nullable=False)
    run_state: Mapped[str] = mapped_column(String(40), nullable=False)
    output_ref: Mapped[str | None] = mapped_column(Text)
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_prompt_evaluation_runs_case", "case_key"),
        Index("ix_prompt_evaluation_runs_agent", "agent_key"),
        Index("ix_prompt_evaluation_runs_state", "run_state"),
        Index("ix_prompt_evaluation_runs_created_at", "created_at"),
    )
