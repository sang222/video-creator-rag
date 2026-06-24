import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


ProviderType = Literal["LLM", "TTS", "MEDIA", "IMAGE", "VIDEO", "STORAGE", "ANALYTICS", "PLATFORM", "AFFILIATE", "OTHER"]
ProviderStatus = Literal["ACTIVE", "DISABLED", "DEPRECATED", "EXPERIMENTAL"]
CredentialType = Literal["API_KEY", "OAUTH_CLIENT", "OAUTH_TOKEN", "SERVICE_ACCOUNT", "MANUAL", "NONE"]
CredentialStatus = Literal["CONFIGURED", "MISSING", "DISABLED", "EXPIRED", "REVOKED", "UNKNOWN"]
CredentialHealthState = Literal["HEALTHY", "MISSING", "EXPIRED", "REVOKED", "MISCONFIGURED", "UNKNOWN"]
QuotaScopeType = Literal["COMPANY", "CHANNEL", "PROJECT", "GLOBAL"]
QuotaWindow = Literal["DAILY", "WEEKLY", "MONTHLY", "CUSTOM"]
QuotaUnit = Literal["TOKENS", "REQUESTS", "SECONDS", "CREDITS", "BYTES", "USD", "OTHER"]
QuotaStatus = Literal["ACTIVE", "EXHAUSTED", "DISABLED", "UNKNOWN"]
QuotaEventType = Literal["RESERVE", "CONSUME", "RELEASE", "ADJUST", "RESET", "REJECT"]
CostScopeType = Literal["COMPANY", "CHANNEL", "PROJECT", "ARTIFACT", "GATE_RUN", "PROVIDER_TEST", "GLOBAL"]
CostEventType = Literal["ESTIMATED", "RESERVED", "ACTUAL", "ADJUSTED", "REFUNDED"]
BudgetPolicyStatus = Literal["ACTIVE", "DISABLED", "DRAFT"]
BudgetGateDecision = Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]
ProviderHealthState = Literal["HEALTHY", "DEGRADED", "UNAVAILABLE", "RATE_LIMITED", "QUOTA_EXHAUSTED", "CREDENTIAL_MISSING", "UNKNOWN"]
ComponentType = Literal["PROVIDER", "CREDENTIAL", "QUOTA", "DATABASE", "QUEUE", "STORAGE", "API", "CLI", "CONFIG", "OTHER"]
ComponentHealthState = Literal["HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"]
SystemHealthState = Literal["HEALTHY", "DEGRADED", "BLOCKED", "UNKNOWN"]
RetryPolicyStatus = Literal["ACTIVE", "DISABLED", "DRAFT"]
ProviderAttemptStatus = Literal["SUCCESS", "RETRYABLE_FAILURE", "NON_RETRYABLE_FAILURE", "QUOTA_REJECTED", "CIRCUIT_OPEN", "CANCELLED"]
DeadLetterReplayState = Literal["NOT_REPLAYABLE", "REPLAYABLE", "REPLAYED", "DISCARDED"]
OpsIncidentType = Literal["PROVIDER_OUTAGE", "CREDENTIAL_MISSING", "QUOTA_EXHAUSTED", "COST_LIMIT_REACHED", "DEAD_LETTER_JOB", "HEALTH_DEGRADED", "CONFIG_ERROR", "UNKNOWN"]
OpsSeverity = Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
OpsIncidentState = Literal["OPEN", "ACKNOWLEDGED", "RESOLVED", "DISMISSED"]
ManualActionType = Literal["CHECK_CREDENTIAL", "UPDATE_CREDENTIAL_REF", "REVIEW_COST_LIMIT", "REVIEW_QUOTA", "INVESTIGATE_PROVIDER", "REPLAY_DEAD_LETTER", "RESOLVE_INCIDENT", "OTHER"]
ManualActionPriority = Literal["LOW", "MEDIUM", "HIGH", "URGENT"]
ManualActionState = Literal["OPEN", "IN_PROGRESS", "DONE", "CANCELLED"]
MockProviderMode = Literal["success", "timeout", "quota_exceeded", "malformed", "unavailable", "retryable_error", "non_retryable_error", "circuit_open"]


class ProviderRegistryEntryCreate(BaseModel):
    provider_key: str = Field(min_length=1)
    provider_name: str = Field(min_length=1)
    provider_type: ProviderType
    status: ProviderStatus = "ACTIVE"
    capability_blob: dict[str, Any] = Field(default_factory=dict)
    policy_fit_blob: dict[str, Any] = Field(default_factory=dict)
    cost_model_blob: dict[str, Any] = Field(default_factory=dict)
    quota_model_blob: dict[str, Any] = Field(default_factory=dict)
    retry_policy_blob: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProviderRegistryEntryRead(ProviderRegistryEntryCreate):
    id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class CredentialReferenceCreate(BaseModel):
    provider_key: str = Field(min_length=1)
    credential_key: str = Field(min_length=1)
    credential_type: CredentialType
    secret_ref: str | None = None
    scope_blob: dict[str, Any] = Field(default_factory=dict)
    status: CredentialStatus = "UNKNOWN"
    expires_at: AwareDatetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class CredentialReferenceRead(BaseModel):
    id: uuid.UUID
    provider_key: str
    credential_key: str
    credential_type: CredentialType
    secret_ref: str | None = None
    scope_blob: dict[str, Any]
    status: CredentialStatus
    expires_at: AwareDatetime | None
    last_checked_at: AwareDatetime | None
    metadata: dict[str, Any]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class CredentialHealthSnapshotCreate(BaseModel):
    credential_reference_id: uuid.UUID
    health_state: CredentialHealthState | None = None
    reason_codes: list[str] = Field(default_factory=list)
    next_action: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class CredentialHealthSnapshotRead(BaseModel):
    id: uuid.UUID
    credential_reference_id: uuid.UUID
    provider_key: str
    health_state: CredentialHealthState
    checked_at: AwareDatetime
    reason_codes: list[str]
    next_action: str | None
    metadata: dict[str, Any]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class QuotaAccountCreate(BaseModel):
    provider_key: str = Field(min_length=1)
    quota_scope_type: QuotaScopeType
    quota_scope_id: uuid.UUID | None = None
    quota_window: QuotaWindow
    quota_limit: Decimal | None = None
    unit: QuotaUnit
    reset_at: AwareDatetime | None = None
    status: QuotaStatus = "ACTIVE"
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class QuotaAccountRead(QuotaAccountCreate):
    id: uuid.UUID
    quota_used: Decimal
    quota_reserved: Decimal
    created_at: AwareDatetime
    updated_at: AwareDatetime


class QuotaEventRequest(BaseModel):
    quota_account_id: uuid.UUID
    amount: Decimal = Field(ge=0)
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    reason_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class QuotaEventRead(BaseModel):
    id: uuid.UUID
    quota_account_id: uuid.UUID | None
    provider_key: str
    event_type: QuotaEventType
    amount: Decimal
    unit: QuotaUnit
    target_type: str | None
    target_id: uuid.UUID | None
    reason_code: str | None
    metadata: dict[str, Any]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class CostEventCreate(BaseModel):
    provider_key: str = Field(min_length=1)
    cost_scope_type: CostScopeType
    cost_scope_id: uuid.UUID | None = None
    amount: Decimal = Field(ge=0)
    currency: str = "USD"
    cost_type: CostEventType
    unit_count: Decimal | None = None
    unit_type: str | None = None
    provider_run_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class CostEventRead(CostEventCreate):
    id: uuid.UUID
    created_at: AwareDatetime


class BudgetPolicyCreate(BaseModel):
    policy_key: str = Field(min_length=1)
    scope_type: QuotaScopeType
    scope_id: uuid.UUID | None = None
    policy_blob: dict[str, Any] = Field(default_factory=dict)
    status: BudgetPolicyStatus = "DRAFT"

    model_config = ConfigDict(extra="forbid")


class BudgetPolicyRead(BudgetPolicyCreate):
    id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class BudgetGateCheckRequest(BaseModel):
    policy_key: str
    provider_key: str | None = None
    scope_type: QuotaScopeType | None = None
    scope_id: uuid.UUID | None = None
    estimated_cost: Decimal | None = None
    quota_account_id: uuid.UUID | None = None
    quota_amount: Decimal | None = None
    unit: QuotaUnit | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class BudgetGateDecisionRead(BaseModel):
    decision: BudgetGateDecision
    reason_codes: list[str] = Field(min_length=1)
    next_action: str | None = None
    policy_key: str
    deterministic: bool = True

    model_config = ConfigDict(extra="forbid")


class ProviderHealthCheckRequest(BaseModel):
    mode: MockProviderMode = "success"
    next_action: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProviderHealthSnapshotRead(BaseModel):
    id: uuid.UUID
    provider_key: str
    provider_type: ProviderType
    health_state: ProviderHealthState
    checked_at: AwareDatetime
    latency_ms: int | None
    error_rate: Decimal | None
    quota_state: str | None
    reason_codes: list[str]
    next_action: str | None
    metadata: dict[str, Any]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class ComponentHealthSnapshotCreate(BaseModel):
    component_type: ComponentType
    component_key: str = Field(min_length=1)
    health_state: ComponentHealthState
    reason_codes: list[str] = Field(default_factory=list)
    next_action: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ComponentHealthSnapshotRead(ComponentHealthSnapshotCreate):
    id: uuid.UUID
    checked_at: AwareDatetime
    created_at: AwareDatetime


class SystemHealthSnapshotRead(BaseModel):
    id: uuid.UUID
    captured_at: AwareDatetime
    overall_state: SystemHealthState
    component_counts: dict[str, Any]
    active_incident_count: int
    action_required: bool
    reason_codes: list[str]
    next_action: str | None
    metadata: dict[str, Any]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class RetryPolicyCreate(BaseModel):
    policy_key: str = Field(min_length=1)
    provider_key: str | None = None
    target_type: str | None = None
    policy_blob: dict[str, Any] = Field(default_factory=dict)
    status: RetryPolicyStatus = "DRAFT"

    model_config = ConfigDict(extra="forbid")


class RetryPolicyRead(RetryPolicyCreate):
    id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ProviderAttemptMockRequest(BaseModel):
    provider_key: str = Field(min_length=1)
    operation_key: str = "contract_test"
    mode: MockProviderMode = "success"
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    attempt_number: int = Field(default=1, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProviderAttemptRead(BaseModel):
    id: uuid.UUID
    provider_key: str
    operation_key: str
    target_type: str | None
    target_id: uuid.UUID | None
    attempt_number: int
    status: ProviderAttemptStatus
    error_code: str | None
    error_message_redacted: str | None
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    latency_ms: int | None
    cost_event_id: uuid.UUID | None
    quota_event_id: uuid.UUID | None
    metadata: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class DeadLetterJobCreate(BaseModel):
    queue_name: str = Field(min_length=1)
    job_type: str = Field(min_length=1)
    payload_ref: str | None = None
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    fail_count: int = Field(default=0, ge=0)
    replay_state: DeadLetterReplayState = "REPLAYABLE"
    reason_code: str | None = None
    next_action: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class DeadLetterJobRead(DeadLetterJobCreate):
    id: uuid.UUID
    first_failed_at: AwareDatetime
    last_failed_at: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime


class OpsIncidentCreate(BaseModel):
    incident_type: OpsIncidentType
    severity: OpsSeverity
    state: OpsIncidentState = "OPEN"
    impacted_refs: list[dict[str, Any]] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    next_action: str = Field(min_length=1)
    owner_user_id: uuid.UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class OpsIncidentRead(OpsIncidentCreate):
    id: uuid.UUID
    opened_at: AwareDatetime
    acknowledged_at: AwareDatetime | None
    resolved_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ManualActionCreate(BaseModel):
    action_type: ManualActionType
    target_type: str = Field(min_length=1)
    target_id: uuid.UUID | None = None
    priority: ManualActionPriority = "MEDIUM"
    state: ManualActionState = "OPEN"
    reason_code: str | None = None
    next_action: str = Field(min_length=1)
    assignee_user_id: uuid.UUID | None = None
    due_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")


class ManualActionRead(ManualActionCreate):
    id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
