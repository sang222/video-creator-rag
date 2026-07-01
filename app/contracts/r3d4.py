import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


GateStatus = Literal["PASS", "REVIEW_REQUIRED", "BLOCK", "SKIPPED_NOT_APPLICABLE"]
GateSeverity = Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
AgentOutputValidationState = Literal["VALID", "CANONICALIZED", "REVIEW_REQUIRED", "BLOCKED"]


class GateEvidenceRef(BaseModel):
    ref_type: str
    ref_id: str
    hash: str | None = None

    model_config = ConfigDict(extra="forbid")


class R3D4GateResultRead(BaseModel):
    gate_key: str
    status: GateStatus
    severity: GateSeverity
    measurements_json: dict[str, Any] = Field(default_factory=dict)
    fail_codes: list[str] = Field(default_factory=list)
    blocking_refs: list[dict[str, Any]] = Field(default_factory=list)
    checked_artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    checked_contract_paths: list[str] = Field(default_factory=list)
    repair_hint: str | None = None
    human_readable_summary: str
    created_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")


class R3D4GateBatchResultRead(BaseModel):
    package_id: uuid.UUID
    video_project_id: uuid.UUID | None
    effective_context_snapshot_id: uuid.UUID | None
    status: GateStatus
    gate_results: list[R3D4GateResultRead] = Field(default_factory=list)
    hard_block_count: int
    review_required_count: int
    context_hash: str | None
    created_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")


class AgentOutputValidationRunRead(BaseModel):
    id: uuid.UUID
    package_id: uuid.UUID
    video_project_id: uuid.UUID | None
    prompt_render_run_id: uuid.UUID | None
    agent_context_pack_snapshot_id: uuid.UUID | None
    agent_key: str
    artifact_type: str
    output_type: str
    schema_version: str
    status: str
    validation_state: str
    reason_codes: list[str] = Field(default_factory=list)
    applied_context_refs_json: dict[str, Any] = Field(default_factory=dict)
    evidence_refs_json: list[dict[str, Any]] = Field(default_factory=list)
    raw_output_ref: str | None
    raw_output_hash: str
    output_hash: str
    artifact_hash: str
    canonical_artifact_json: dict[str, Any] = Field(default_factory=dict)
    validation_result_json: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)
