import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


AgentContextPackBuildStatus = Literal["OK", "REVIEW_REQUIRED", "BLOCK"]


class AgentContextPackSnapshotRead(BaseModel):
    id: uuid.UUID
    package_id: uuid.UUID
    video_project_id: uuid.UUID
    prompt_render_run_id: uuid.UUID | None
    agent_key: str
    task_type: str | None
    lane: str
    context_pack_version: str
    builder_version: str
    agent_context_contract_hash: str
    effective_context_snapshot_id: uuid.UUID
    effective_context_hash: str
    channel_contract_hash: str | None
    compiled_policy_snapshot_id: uuid.UUID | None
    compiled_policy_snapshot_hash: str | None
    context_pack_hash: str
    prompt_context_hash: str | None
    artifact_digest_refs_json: list[dict[str, Any]] = Field(default_factory=list)
    evidence_digest_hash: str | None
    common_skill_digest_hash: str | None
    runtime_guard_digest_hash: str
    budget_report_json: dict[str, Any] = Field(default_factory=dict)
    omitted_items_json: list[dict[str, Any]] = Field(default_factory=list)
    largest_context_contributors_json: list[dict[str, Any]] = Field(default_factory=list)
    agent_context_contract_json: dict[str, Any] = Field(default_factory=dict)
    context_pack_json: dict[str, Any] = Field(default_factory=dict)
    shape_gate_result_json: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)
