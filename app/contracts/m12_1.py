import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


PromptRecordStatus = Literal["DRAFT", "ACTIVE", "DEPRECATED"]
PromptValidationStatus = Literal["OK", "REVIEW_REQUIRED", "BLOCK", "ERROR"]
AgentEnvelopeStatus = Literal["OK", "REVIEW_REQUIRED", "BLOCK", "REFUSAL", "ERROR"]
ConfidenceLabel = Literal["LOW", "MEDIUM", "HIGH"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
PromptEvaluationRunState = Literal["PASS", "FAIL", "SKIPPED", "ERROR"]
MessageRole = Literal["system", "user", "assistant"]


class _ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PromptMessage(BaseModel):
    role: MessageRole
    content: str

    model_config = ConfigDict(extra="forbid")


class AgentOutputEnvelope(BaseModel):
    contract_version: str
    agent_key: str
    status: AgentEnvelopeStatus
    confidence_label: ConfidenceLabel
    risk_level: RiskLevel | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_action: str | None = None
    operator_summary_vi: str
    technical_appendix: dict[str, Any] = Field(default_factory=dict)
    artifact: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class PromptTemplateRecordRead(_ReadModel):
    id: uuid.UUID
    agent_key: str
    template_key: str
    template_version: str
    status: PromptRecordStatus
    file_path: str
    prompt_hash: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AgentPromptProfileRead(_ReadModel):
    id: uuid.UUID
    agent_key: str
    default_router_lane: str
    allowed_router_lanes: list[str]
    input_contract: dict[str, Any]
    output_contract: dict[str, Any]
    safety_policy_refs: list[str]
    common_skill_refs: list[str]
    channel_contract_required: bool
    market_locale_context_required: bool
    status: PromptRecordStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PromptContractVersionRead(_ReadModel):
    id: uuid.UUID
    agent_key: str
    template_key: str
    template_version: str
    input_contract: dict[str, Any]
    output_contract: dict[str, Any]
    schema_ref: str
    schema_version: str
    status: PromptRecordStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime


class StructuredOutputSchemaRead(_ReadModel):
    id: uuid.UUID
    schema_ref: str
    schema_version: str
    dialect: str
    json_schema: dict[str, Any]
    status: PromptRecordStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PromptRenderRunRead(_ReadModel):
    id: uuid.UUID
    agent_key: str
    template_key: str
    template_version: str
    rendered_messages: list[dict[str, Any]]
    prompt_hash: str
    prompt_context_hash: str
    input_payload_ref: str | None
    output_schema_ref: str
    router_lane: str
    channel_profile_version_id: uuid.UUID | None
    compiled_policy_snapshot_id: uuid.UUID | None
    validation_status: PromptValidationStatus
    created_at: AwareDatetime


class PromptAuditSnapshotRead(_ReadModel):
    id: uuid.UUID
    agent_key: str
    template_key: str
    template_version: str
    channel_profile_version_id: uuid.UUID | None
    compiled_policy_snapshot_id: uuid.UUID | None
    prompt_hash: str
    prompt_context_hash: str
    router_lane: str
    provider_attempt_refs: list[dict[str, Any]]
    prompt_render_run_id: uuid.UUID | None
    final_output_ref: str | None
    validation_result: dict[str, Any]
    repair_attempts: list[dict[str, Any]]
    created_at: AwareDatetime


class PromptEvaluationCaseRead(_ReadModel):
    id: uuid.UUID
    case_key: str
    agent_key: str
    template_key: str
    template_version: str
    input_fixture_ref: str
    expected_outcome: dict[str, Any]
    pass_criteria: dict[str, Any]
    status: PromptRecordStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PromptEvaluationRunRead(_ReadModel):
    id: uuid.UUID
    case_key: str
    agent_key: str
    template_version: str
    run_state: PromptEvaluationRunState
    output_ref: str | None
    validation_result: dict[str, Any]
    created_at: AwareDatetime


class PromptRegistrySyncSummary(BaseModel):
    template_count: int
    profile_count: int
    contract_count: int
    schema_count: int
    evaluation_case_count: int
    agent_keys: list[str]
    prompt_hashes: dict[str, str]

    model_config = ConfigDict(extra="forbid")


class PromptRenderRequest(BaseModel):
    agent_key: str
    template_key: str | None = None
    template_version: str | None = None
    router_lane: str | None = None
    task_payload: dict[str, Any] = Field(default_factory=dict)
    render_vars: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    input_payload_ref: str | None = None
    channel_profile_version_id: uuid.UUID | None = None
    compiled_policy_snapshot_id: uuid.UUID | None = None
    channel_contract_json: dict[str, Any] | None = None
    compiled_policy_snapshot_json: dict[str, Any] | None = None
    market_locale_context_json: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class PromptRenderResult(BaseModel):
    status: PromptValidationStatus
    agent_key: str
    template_key: str
    template_version: str
    router_lane: str
    rendered_messages: list[PromptMessage] = Field(default_factory=list)
    prompt_hash: str
    prompt_context_hash: str
    output_schema_ref: str
    prompt_render_run_id: uuid.UUID
    prompt_audit_snapshot_id: uuid.UUID
    blocking_output: AgentOutputEnvelope | None = None
    reason_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PromptOutputValidationRequest(BaseModel):
    agent_key: str
    raw_output: str | dict[str, Any]
    schema_ref: str = "base_agent_envelope"
    prompt_render_run_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class PromptOutputValidationResult(BaseModel):
    status: PromptValidationStatus
    parsed_output: dict[str, Any] | None = None
    validation_result: dict[str, Any]
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")
