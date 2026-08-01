import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


FallbackLevel = Literal["PRIMARY", "FALLBACK", "PREMIUM", "EMERGENCY", "BACKUP"]
LLMRouteStatus = Literal["SUCCESS", "FAILED", "SKIPPED", "BLOCKED"]


class _ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class LLMRouterProfileRead(_ReadModel):
    id: uuid.UUID
    profile_key: str
    provider_key: str
    base_url: str
    real_execution_enabled: bool
    default_timeout_seconds: int
    created_at: AwareDatetime
    updated_at: AwareDatetime


class LLMRouterLaneRead(_ReadModel):
    id: uuid.UUID
    router_profile_id: uuid.UUID
    lane_name: str
    lane_description: str
    allowed_task_types: list[str]
    primary_model: str
    fallback_models: list[str]
    premium_model: str | None
    emergency_model: str | None
    backup_model: str | None
    max_input_tokens: int | None
    max_output_tokens: int | None
    cost_tier: str
    latency_tier: str
    critical_path_allowed: bool
    requires_human_approval_for_premium: bool
    route_priority: int
    real_execution_enabled: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class LLMModelProfileRead(_ReadModel):
    id: uuid.UUID
    provider_key: str
    model_id: str
    model_role: str
    lane_names: list[str]
    is_enabled: bool
    critical_path_allowed: bool
    notes: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class LLMRouteAttemptRead(_ReadModel):
    id: uuid.UUID
    router_profile_id: uuid.UUID
    lane_name: str
    requested_task_type: str | None
    selected_model: str
    fallback_level: FallbackLevel
    request_hash: str
    response_hash: str | None
    status: LLMRouteStatus
    error_code: str | None
    error_message: str | None
    prompt_eval_count: int | None
    eval_count: int | None
    total_duration_ms: int | None
    load_duration_ms: int | None
    prompt_eval_duration_ms: int | None
    eval_duration_ms: int | None
    provider_attempt_id: uuid.UUID | None
    llm_run_snapshot_id: uuid.UUID | None
    created_at: AwareDatetime


class LLMRouteRequest(BaseModel):
    lane_name: str
    prompt: str | None = None
    messages: list[dict[str, str]] | None = None
    requested_task_type: str | None = None
    response_format: Literal["text", "json"] = "text"
    profile_key: str = "default"

    model_config = ConfigDict(extra="forbid")


class LLMRouteResponse(BaseModel):
    status: LLMRouteStatus
    lane_name: str
    selected_model: str
    fallback_level: FallbackLevel
    content: str | None = None
    structured_output: dict[str, Any] | None = None
    route_attempt_id: uuid.UUID
    provider_attempt_id: uuid.UUID | None = None
    llm_run_snapshot_id: uuid.UUID | None = None
    reason_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class LLMRouterSmokeTestRequest(BaseModel):
    profile_key: str = "default"

    model_config = ConfigDict(extra="forbid")


class LLMRouterSmokeTestRead(BaseModel):
    status: LLMRouteStatus
    real_smoke_enabled: bool
    health_check: dict[str, Any] = Field(default_factory=dict)
    cheap_structured: dict[str, Any] = Field(default_factory=dict)
    long_context_text: dict[str, Any] = Field(default_factory=dict)
    fallback_probe: dict[str, Any] = Field(default_factory=dict)
    route_attempt_ids: list[uuid.UUID] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    next_action: str | None = None

    model_config = ConfigDict(extra="forbid")
