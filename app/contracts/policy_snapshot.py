import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


SnapshotStatus = Literal["compiled", "approved", "active", "archived"]


class ChannelConstitution(BaseModel):
    promise: str
    audience: str
    boundaries: list[str]

    model_config = ConfigDict(extra="forbid")


class CapabilityStatus(BaseModel):
    profile_compiler: str
    policy_snapshot: str
    artifact_workflow: str
    media_pipeline: str
    publish_pipeline: str
    analytics: str
    no_view_diagnostic: str
    envato_manual_asset_pilot_documented: bool
    ffmpeg_renderer_planned: bool

    model_config = ConfigDict(extra="forbid")


class RenderPolicy(BaseModel):
    capcut_prototype_viewer_only: bool
    production_renderer_planned: str
    transcription_pilot: str
    ai_video_mode: str
    visual_plan_required: bool

    model_config = ConfigDict(extra="forbid")


class CompiledChannelPolicyPayload(BaseModel):
    channel_constitution: ChannelConstitution
    operating_blueprint: dict[str, Any]
    content_pillars: list[str] = Field(min_length=1)
    series_plan: list[dict[str, Any]] = Field(min_length=1)
    editorial_calendar_defaults: dict[str, Any]
    initial_content_runway: list[dict[str, Any]] = Field(min_length=1)
    default_playbook: dict[str, Any]
    render_policy: RenderPolicy
    gate_policy: dict[str, Any]
    voice_policy: dict[str, Any]
    evidence_policy: dict[str, Any]
    monetization_policy: dict[str, Any]
    kpi_profile: dict[str, Any]
    editorial_promise: str
    distinctiveness_profile: dict[str, Any]
    format_bible: dict[str, Any]
    capability_status: CapabilityStatus
    channel_contract_json: dict[str, Any] | None = None
    field_source_map_json: dict[str, Any] | None = None
    compiled_policy_snapshot_json: dict[str, Any] | None = None
    contract_status: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    contradiction_reasons: list[str] = Field(default_factory=list)
    activation_required: bool = False

    model_config = ConfigDict(extra="forbid")


class CompiledChannelPolicySnapshot(BaseModel):
    id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    compile_run_id: uuid.UUID | None
    snapshot_version: int
    status: SnapshotStatus
    compiler_version: str
    capability_matrix_version: str
    compiled_payload: CompiledChannelPolicyPayload
    content_hash: str
    profile_input_hash: str
    activated_at: AwareDatetime | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")
