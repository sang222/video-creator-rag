import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


FirstVideoPackageStatus = Literal[
    "READY_FOR_HUMAN_REVIEW",
    "REVIEW_REQUIRED",
    "BLOCKED",
    "NOT_CONFIGURED",
    "READY_FOR_MEDIA_PROVIDERS",
    "BLOCKED_PROVIDER_NOT_CONFIGURED",
    "ERROR",
]

VideoGenerationBoundaryStatus = Literal[
    "READY_FOR_MEDIA_PROVIDERS",
    "BLOCKED_PROVIDER_NOT_CONFIGURED",
    "REVIEW_REQUIRED",
    "BLOCKED_GATEKEEPER",
]

M122SPreflightStatus = Literal[
    "READY",
    "BLOCKED_NEEDS_COMPANY",
    "BLOCKED_NEEDS_CHANNEL",
    "BLOCKED_NEEDS_CHANNEL_CONTRACT",
    "BLOCKED_REQUIRED_TAGS",
    "BLOCKED_ACTIVATION_FLAGS",
    "NOT_CONFIGURED",
]


class FirstScriptedVideoPackageRequest(BaseModel):
    channel_id: uuid.UUID
    topic: str | None = Field(default=None, min_length=1)
    research_pack_text: str | None = None
    research_pack_ref: str | None = None
    video_project_id: uuid.UUID | None = None
    target_video_type: Literal["long_form"] = "long_form"
    package_title_seed: str | None = None
    no_media: bool = True
    human_review_only: bool = True

    model_config = ConfigDict(extra="forbid")


class M122SPreflightRead(BaseModel):
    status: M122SPreflightStatus
    next_action: str
    company_id: uuid.UUID | None = None
    channel_id: uuid.UUID | None = None
    channel_profile_version_id: uuid.UUID | None = None
    compiled_policy_snapshot_id: uuid.UUID | None = None
    contract_status: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class FirstScriptedVideoPackageRead(BaseModel):
    id: uuid.UUID
    video_project_id: uuid.UUID | None
    channel_id: uuid.UUID
    channel_profile_version_id: uuid.UUID | None
    compiled_policy_snapshot_id: uuid.UUID | None
    provider_readiness_snapshot_id: uuid.UUID | None
    package_status: FirstVideoPackageStatus
    agent_run_refs: list[dict[str, Any]] = Field(default_factory=list)
    prompt_render_run_refs: list[uuid.UUID] = Field(default_factory=list)
    prompt_audit_snapshot_refs: list[uuid.UUID] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    risk_limitations_summary: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class FirstScriptedVideoPackageReviewRead(BaseModel):
    package_id: uuid.UUID
    package_status: FirstVideoPackageStatus
    channel_binding: dict[str, Any]
    human_review_checklist: dict[str, Any] = Field(default_factory=dict)
    agent_outputs: dict[str, Any] = Field(default_factory=dict)
    prompt_snapshots: dict[str, Any] = Field(default_factory=dict)
    provider_readiness_snapshot_ref: uuid.UUID | None = None
    limitations: list[str] = Field(default_factory=list)
    next_action: str | None = None

    model_config = ConfigDict(extra="forbid")


class VideoGenerationBoundaryRead(BaseModel):
    id: uuid.UUID
    package_id: uuid.UUID
    channel_id: uuid.UUID
    video_project_id: uuid.UUID | None
    required_inputs: dict[str, Any] = Field(default_factory=dict)
    required_providers: list[dict[str, Any]] = Field(default_factory=list)
    provider_readiness: dict[str, Any] = Field(default_factory=dict)
    boundary_status: VideoGenerationBoundaryStatus
    blocked_reasons: list[str] = Field(default_factory=list)
    next_action: str
    operator_summary_vi: str
    no_provider_calls_confirmed: bool
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class FirstScriptedVideoPackageAgentRunsRead(BaseModel):
    package_id: uuid.UUID
    package_status: FirstVideoPackageStatus
    agent_runs: list[dict[str, Any]] = Field(default_factory=list)
    prompt_render_run_refs: list[uuid.UUID] = Field(default_factory=list)
    prompt_audit_snapshot_refs: list[uuid.UUID] = Field(default_factory=list)
    provider_attempt_refs: list[str] = Field(default_factory=list)
    llm_run_snapshot_refs: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")
