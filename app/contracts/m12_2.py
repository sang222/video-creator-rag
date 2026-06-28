import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


FirstVideoPackageStatus = Literal[
    "READY_FOR_HUMAN_REVIEW",
    "REVIEW_REQUIRED",
    "BLOCKED",
    "NOT_CONFIGURED",
    "ERROR",
]


class FirstScriptedVideoPackageRequest(BaseModel):
    channel_id: uuid.UUID
    topic: str | None = Field(default=None, min_length=1)
    research_pack_text: str | None = None
    research_pack_ref: str | None = None
    video_project_id: uuid.UUID | None = None
    no_media: bool = True
    human_review_only: bool = True

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
