import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.time import utc_now


class ReasonCodeDefinition(BaseModel):
    code: str
    description: str

    model_config = ConfigDict(extra="forbid")


class GateResult(BaseModel):
    gate_key: str
    gate_version: str
    decision: Literal["PASS", "BLOCK", "REVIEW_REQUIRED", "SKIPPED", "NOT_APPLICABLE"]
    reason_codes: list[str] = Field(min_length=1)
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: AwareDatetime = Field(default_factory=utc_now)
    correlation_id: str

    model_config = ConfigDict(extra="forbid")


GateDefinitionStatus = Literal["draft", "active", "deprecated", "superseded"]
GateRunTargetType = Literal["video_project", "artifact_version", "review_task"]
GateRunResult = Literal["PASS", "REVIEW_REQUIRED", "BLOCK", "SKIPPED", "NOT_APPLICABLE"]
FreshnessState = Literal["FRESH", "STALE", "UNKNOWN", "NOT_REQUIRED"]
ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
PlatformKey = Literal["youtube", "tiktok", "facebook", "instagram", "meta", "generic"]
PlatformPolicyCatalogStatus = Literal["draft", "active", "retired"]
PlatformPolicyVersionStatus = Literal["draft", "active", "superseded", "retired"]
PolicySourceType = Literal["OFFICIAL", "PRIMARY", "REPUTABLE_SECONDARY", "INTERNAL_NOTE", "MANUAL_REVIEW"]
PolicySourceReliability = Literal["OFFICIAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
PolicyChangeState = Literal[
    "DRAFT",
    "SOURCE_VERIFIED",
    "DIFFED",
    "IMPACT_CLASSIFIED",
    "CATALOG_PATCHED",
    "GATES_UPDATED",
    "REVALIDATION_RUNNING",
    "OPERATOR_REVIEW_REQUIRED",
    "READY_TO_ACTIVATE",
    "ACTIVE",
    "SUPERSEDED",
    "ROLLED_BACK",
    "REJECTED",
    "MONITOR_ONLY",
]
PolicyRevalidationStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]


class GateDefinitionVersionCreate(BaseModel):
    gate_key: str = Field(min_length=1)
    gate_name: str = Field(min_length=1)
    gate_domain: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: GateDefinitionStatus = "draft"
    input_schema_version: str = Field(min_length=1)
    output_schema_version: str = Field(min_length=1)
    definition: dict[str, Any] = Field(default_factory=dict)
    reason_code_refs: list[str] = Field(default_factory=list)
    created_by_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class GateDefinitionVersionRead(GateDefinitionVersionCreate):
    id: uuid.UUID
    created_at: AwareDatetime
    activated_at: AwareDatetime | None
    superseded_at: AwareDatetime | None


class GateRunCreate(BaseModel):
    gate_key: str = Field(min_length=1)
    target_type: GateRunTargetType
    target_id: uuid.UUID
    gate_definition_version_id: uuid.UUID | None = None
    created_by_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class GateRunRead(BaseModel):
    id: uuid.UUID
    gate_definition_version_id: uuid.UUID
    gate_key: str
    target_type: GateRunTargetType
    target_id: uuid.UUID
    video_project_id: uuid.UUID | None
    artifact_version_id: uuid.UUID | None
    review_task_id: uuid.UUID | None
    policy_snapshot_id: uuid.UUID | None
    input_snapshot: dict[str, Any]
    input_snapshot_hash: str
    result: GateRunResult
    reason_codes: list[str] = Field(min_length=1)
    evidence_refs: list[dict[str, Any]]
    metric_refs: list[dict[str, Any]]
    freshness_state: FreshnessState
    confidence_level: ConfidenceLevel
    confidence_reason_codes: list[str]
    decision_basis: dict[str, Any]
    created_review_task_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class PlatformPolicyCatalogCreate(BaseModel):
    catalog_key: str = Field(min_length=1)
    platform: PlatformKey
    policy_domain: str = Field(min_length=1)
    status: PlatformPolicyCatalogStatus = "active"

    model_config = ConfigDict(extra="forbid")


class PlatformPolicyCatalogRead(PlatformPolicyCatalogCreate):
    id: uuid.UUID
    current_version_id: uuid.UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PlatformPolicyVersionCreate(BaseModel):
    catalog_id: uuid.UUID
    version: str = Field(min_length=1)
    status: PlatformPolicyVersionStatus = "draft"
    effective_at: AwareDatetime | None = None
    observed_at: AwareDatetime | None = None
    policy_blob: dict[str, Any] = Field(default_factory=dict)
    interpretation_notes: str | None = None
    created_by_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class PlatformPolicyVersionRead(PlatformPolicyVersionCreate):
    id: uuid.UUID
    created_at: AwareDatetime
    activated_at: AwareDatetime | None
    superseded_at: AwareDatetime | None


class PolicySourceRefCreate(BaseModel):
    policy_version_id: uuid.UUID | None = None
    policy_change_record_id: uuid.UUID | None = None
    source_type: PolicySourceType
    source_title: str | None = None
    source_url: str | None = None
    captured_at: AwareDatetime = Field(default_factory=utc_now)
    reliability: PolicySourceReliability = "UNKNOWN"
    notes: str | None = None

    model_config = ConfigDict(extra="forbid")


class PolicySourceRefRead(PolicySourceRefCreate):
    id: uuid.UUID
    created_at: AwareDatetime


class PolicyChangeRecordCreate(BaseModel):
    change_key: str = Field(min_length=1)
    platform: PlatformKey
    policy_domain: str = Field(min_length=1)
    state: PolicyChangeState = "DRAFT"
    summary: str = Field(min_length=1)
    old_policy_version_id: uuid.UUID | None = None
    new_policy_version_id: uuid.UUID | None = None
    impact_classification: dict[str, Any] = Field(default_factory=dict)
    diff_summary: dict[str, Any] = Field(default_factory=dict)
    affected_gate_keys: list[str] = Field(default_factory=list)
    affected_domains: list[str] = Field(default_factory=list)
    requires_revalidation: bool = False
    rollback_available: bool = False
    created_by_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class PolicyChangeRecordRead(PolicyChangeRecordCreate):
    id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PolicyChangeStateRequest(BaseModel):
    state: PolicyChangeState

    model_config = ConfigDict(extra="forbid")


class PolicyRevalidationBatchCreate(BaseModel):
    policy_change_record_id: uuid.UUID | None = None
    gate_definition_version_id: uuid.UUID | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    created_by_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class PolicyRevalidationBatchRead(PolicyRevalidationBatchCreate):
    id: uuid.UUID
    status: PolicyRevalidationStatus
    counts: dict[str, Any]
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    created_at: AwareDatetime
