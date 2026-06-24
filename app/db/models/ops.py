import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class ProviderRegistryEntry(Base):
    __tablename__ = "provider_registry_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    provider_name: Mapped[str] = mapped_column(Text, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="ACTIVE")
    capability_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    policy_fit_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    cost_model_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    quota_model_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    retry_policy_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_provider_registry_entries_provider_type", "provider_type"),
        Index("ix_provider_registry_entries_status", "status"),
        Index("ix_provider_registry_entries_created_at", "created_at"),
    )


class CredentialReference(Base):
    __tablename__ = "credential_references"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    credential_key: Mapped[str] = mapped_column(String(160), nullable=False)
    credential_type: Mapped[str] = mapped_column(String(40), nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    scope_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("provider_key", "credential_key"),
        Index("ix_credential_references_provider_key", "provider_key"),
        Index("ix_credential_references_status", "status"),
        Index("ix_credential_references_created_at", "created_at"),
    )


class CredentialHealthSnapshot(Base):
    __tablename__ = "credential_health_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    credential_reference_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credential_references.id"), nullable=False
    )
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    health_state: Mapped[str] = mapped_column(String(40), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_credential_health_snapshots_credential_reference_id", "credential_reference_id"),
        Index("ix_credential_health_snapshots_provider_key", "provider_key"),
        Index("ix_credential_health_snapshots_checked_at", "checked_at"),
    )


class QuotaAccount(Base):
    __tablename__ = "quota_accounts"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    quota_scope_type: Mapped[str] = mapped_column(String(40), nullable=False)
    quota_scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    quota_window: Mapped[str] = mapped_column(String(40), nullable=False)
    quota_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    quota_used: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    quota_reserved: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="ACTIVE")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index(
            "ix_quota_accounts_provider_scope",
            "provider_key",
            "quota_scope_type",
            "quota_scope_id",
        ),
        Index("ix_quota_accounts_status", "status"),
        Index("ix_quota_accounts_created_at", "created_at"),
    )


class QuotaEvent(Base):
    __tablename__ = "quota_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    quota_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("quota_accounts.id"))
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason_code: Mapped[str | None] = mapped_column(String(160))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_quota_events_quota_account_id", "quota_account_id"),
        Index("ix_quota_events_provider_key", "provider_key"),
        Index("ix_quota_events_target", "target_type", "target_id"),
        Index("ix_quota_events_created_at", "created_at"),
    )


class CostEvent(Base):
    __tablename__ = "cost_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    cost_scope_type: Mapped[str] = mapped_column(String(40), nullable=False)
    cost_scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    cost_type: Mapped[str] = mapped_column(String(40), nullable=False)
    unit_count: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit_type: Mapped[str | None] = mapped_column(String(40))
    provider_run_ref: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_cost_events_provider_key", "provider_key"),
        Index("ix_cost_events_scope", "cost_scope_type", "cost_scope_id"),
        Index("ix_cost_events_created_at", "created_at"),
    )


class BudgetPolicy(Base):
    __tablename__ = "budget_policies"

    id: Mapped[uuid.UUID] = uuid_pk()
    policy_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    scope_type: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    policy_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_budget_policies_scope", "scope_type", "scope_id"),
        Index("ix_budget_policies_status", "status"),
        Index("ix_budget_policies_created_at", "created_at"),
    )


class ProviderHealthSnapshot(Base):
    __tablename__ = "provider_health_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(40), nullable=False)
    health_state: Mapped[str] = mapped_column(String(40), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    quota_state: Mapped[str | None] = mapped_column(String(40))
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_provider_health_snapshots_provider_key", "provider_key"),
        Index("ix_provider_health_snapshots_health_state", "health_state"),
        Index("ix_provider_health_snapshots_checked_at", "checked_at"),
    )


class ComponentHealthSnapshot(Base):
    __tablename__ = "component_health_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    component_type: Mapped[str] = mapped_column(String(40), nullable=False)
    component_key: Mapped[str] = mapped_column(String(160), nullable=False)
    health_state: Mapped[str] = mapped_column(String(40), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_component_health_snapshots_component", "component_type", "component_key"),
        Index("ix_component_health_snapshots_health_state", "health_state"),
        Index("ix_component_health_snapshots_checked_at", "checked_at"),
    )


class SystemHealthSnapshot(Base):
    __tablename__ = "system_health_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    overall_state: Mapped[str] = mapped_column(String(40), nullable=False)
    component_counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    active_incident_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    action_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_system_health_snapshots_overall_state", "overall_state"),
        Index("ix_system_health_snapshots_captured_at", "captured_at"),
    )


class RetryPolicy(Base):
    __tablename__ = "retry_policies"

    id: Mapped[uuid.UUID] = uuid_pk()
    policy_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    provider_key: Mapped[str | None] = mapped_column(String(160))
    target_type: Mapped[str | None] = mapped_column(String(80))
    policy_blob: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_retry_policies_provider_key", "provider_key"),
        Index("ix_retry_policies_status", "status"),
        Index("ix_retry_policies_created_at", "created_at"),
    )


class ProviderAttempt(Base):
    __tablename__ = "provider_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(160), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message_redacted: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cost_events.id"))
    quota_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("quota_events.id"))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_provider_attempts_provider_operation", "provider_key", "operation_key"),
        Index("ix_provider_attempts_target", "target_type", "target_id"),
        Index("ix_provider_attempts_status", "status"),
        Index("ix_provider_attempts_started_at", "started_at"),
    )


class DeadLetterJob(Base):
    __tablename__ = "dead_letter_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    queue_name: Mapped[str] = mapped_column(String(160), nullable=False)
    job_type: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_ref: Mapped[str | None] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    replay_state: Mapped[str] = mapped_column(String(40), nullable=False, default="REPLAYABLE")
    reason_code: Mapped[str | None] = mapped_column(String(160))
    next_action: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_dead_letter_jobs_queue_name", "queue_name"),
        Index("ix_dead_letter_jobs_replay_state", "replay_state"),
        Index("ix_dead_letter_jobs_target", "target_type", "target_id"),
        Index("ix_dead_letter_jobs_created_at", "created_at"),
    )


class OpsIncident(Base):
    __tablename__ = "ops_incidents"

    id: Mapped[uuid.UUID] = uuid_pk()
    incident_type: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="OPEN")
    impacted_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    next_action: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_ops_incidents_type", "incident_type"),
        Index("ix_ops_incidents_state", "state"),
        Index("ix_ops_incidents_severity", "severity"),
        Index("ix_ops_incidents_created_at", "created_at"),
    )


class ManualAction(Base):
    __tablename__ = "manual_action_queue"

    id: Mapped[uuid.UUID] = uuid_pk()
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    priority: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="OPEN")
    reason_code: Mapped[str | None] = mapped_column(String(160))
    next_action: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        Index("ix_manual_action_queue_action_type", "action_type"),
        Index("ix_manual_action_queue_state", "state"),
        Index("ix_manual_action_queue_priority", "priority"),
        Index("ix_manual_action_queue_target", "target_type", "target_id"),
        Index("ix_manual_action_queue_created_at", "created_at"),
    )
