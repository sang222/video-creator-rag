import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


ProfileStatus = Literal["draft", "compiled", "approved", "active", "archived", "rejected"]
CompileStatus = Literal["started", "succeeded", "failed"]


class ChannelProfileInput(BaseModel):
    template_key: str
    template_version: str
    display_name: str
    target_market: str
    audience_segment: str
    monetization_model: dict[str, Any]
    format_strategy: dict[str, Any]
    risk_tolerance: str
    media_style: dict[str, Any]
    voice_style: dict[str, Any]
    evidence_requirement: dict[str, Any]
    platform_strategy: dict[str, Any]
    human_review_strictness: str
    content_pillars: list[str] = Field(min_length=1)
    series_plan: list[dict[str, Any]] = Field(min_length=1)
    initial_content_runway: list[dict[str, Any]] = Field(min_length=1)
    policies: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class ChannelProfileVersionCreate(BaseModel):
    template_key: str | None = None
    profile_input: ChannelProfileInput | None = None
    created_by: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class ChannelProfileVersionRead(BaseModel):
    id: uuid.UUID
    channel_workspace_id: uuid.UUID
    version: int
    status: ProfileStatus
    profile_input: ChannelProfileInput
    profile_input_hash: str
    source_template_key: str | None
    source_template_version: str | None
    created_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    approved_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class ChannelProfileCompileRequest(BaseModel):
    correlation_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class ChannelProfileCompileResult(BaseModel):
    compile_run_id: uuid.UUID
    snapshot_id: uuid.UUID
    content_hash: str
    profile_input_hash: str
    compiler_version: str
    capability_matrix_version: str
    source_template_version: str
    source_template_catalog_hash: str
    capability_matrix_hash: str
    profile_compiler_policy_hash: str

    model_config = ConfigDict(extra="forbid")


class NicheProfileTemplate(BaseModel):
    template_key: str
    template_version: str
    display_name: str
    target_market: str
    audience_segment: str
    monetization_model: dict[str, Any]
    format_strategy: dict[str, Any]
    risk_tolerance: str
    media_style: dict[str, Any]
    voice_style: dict[str, Any]
    evidence_requirement: dict[str, Any]
    platform_strategy: dict[str, Any]
    human_review_strictness: str
    default_content_pillars: list[str] = Field(min_length=1)
    default_series_plan: list[dict[str, Any]] = Field(min_length=1)
    default_runway: list[dict[str, Any]] = Field(min_length=1)
    default_policies: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class CapabilityMatrix(BaseModel):
    matrix_key: str = "default"
    profile_compiler_available: bool
    policy_snapshot_available: bool
    artifact_workflow_available: bool
    media_pipeline_available: bool
    publish_pipeline_available: bool
    analytics_available: bool
    no_view_diagnostic_available: bool
    envato_manual_asset_pilot_documented: bool
    ffmpeg_renderer_planned: bool

    model_config = ConfigDict(extra="forbid")


class ProfileCompilerPolicy(BaseModel):
    compiler_version: str
    allowed_template_keys: list[str] = Field(min_length=1)
    allowed_audience_segments: list[str] = Field(min_length=1)
    allowed_risk_tolerance: list[str] = Field(min_length=1)
    required_output_sections: list[str] = Field(min_length=1)
    hash_canonicalization_policy: str
    no_llm_policy_truth: bool
    no_niche_specific_pipeline: bool

    model_config = ConfigDict(extra="forbid")
