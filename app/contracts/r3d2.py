import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


EffectiveContextCompileStatus = Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]


class EffectiveChannelRuntimeContextSnapshotRead(BaseModel):
    id: uuid.UUID
    video_project_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_profile_version_id: uuid.UUID | None
    compiled_policy_snapshot_id: uuid.UUID | None
    channel_contract_hash: str | None
    field_source_map_hash: str | None
    content_category_id: uuid.UUID | None
    character_binding_id: uuid.UUID | None
    character_policy_mode: str | None
    character_profile_id: uuid.UUID | None
    character_version_id: uuid.UUID | None
    character_image_branch_id: uuid.UUID | None
    reference_asset_pack_id: uuid.UUID | None
    voice_profile_id: uuid.UUID | None
    compile_status: EffectiveContextCompileStatus
    reason_codes_json: list[str] = Field(default_factory=list)
    source_refs_json: list[dict[str, Any]] = Field(default_factory=list)
    context_hash: str
    market_locale_context_json: dict[str, Any] = Field(default_factory=dict)
    audience_context_json: dict[str, Any] = Field(default_factory=dict)
    brand_voice_persona_context_json: dict[str, Any] = Field(default_factory=dict)
    category_runtime_context_json: dict[str, Any] = Field(default_factory=dict)
    character_identity_context_json: dict[str, Any] = Field(default_factory=dict)
    visual_style_context_json: dict[str, Any] = Field(default_factory=dict)
    voice_audio_context_json: dict[str, Any] = Field(default_factory=dict)
    thumbnail_style_context_json: dict[str, Any] = Field(default_factory=dict)
    metadata_seo_policy_context_json: dict[str, Any] = Field(default_factory=dict)
    publish_timing_context_json: dict[str, Any] = Field(default_factory=dict)
    source_rights_disclosure_context_json: dict[str, Any] = Field(default_factory=dict)
    monetization_cta_context_json: dict[str, Any] = Field(default_factory=dict)
    cost_provider_policy_context_json: dict[str, Any] = Field(default_factory=dict)
    safety_forbidden_claims_context_json: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)
