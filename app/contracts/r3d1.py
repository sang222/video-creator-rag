import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class CharacterPolicyMode(str, Enum):
    NO_CHARACTER = "NO_CHARACTER"
    OPTIONAL_CHARACTER = "OPTIONAL_CHARACTER"
    REQUIRED_CHARACTER = "REQUIRED_CHARACTER"


class RuntimeScopeErrorCode(str, Enum):
    CATEGORY_SCOPE_MISSING = "CATEGORY_SCOPE_MISSING"
    CATEGORY_NOT_ACTIVE = "CATEGORY_NOT_ACTIVE"
    CHARACTER_REQUIRED_BUT_MISSING = "CHARACTER_REQUIRED_BUT_MISSING"
    CHARACTER_BINDING_NOT_ACTIVE = "CHARACTER_BINDING_NOT_ACTIVE"
    CHARACTER_ASSET_PACK_MISSING = "CHARACTER_ASSET_PACK_MISSING"
    CHARACTER_VOICE_PROFILE_MISSING = "CHARACTER_VOICE_PROFILE_MISSING"
    CHANNEL_CONTRACT_NOT_COMPLETE = "CHANNEL_CONTRACT_NOT_COMPLETE"
    POLICY_SNAPSHOT_MISSING = "POLICY_SNAPSHOT_MISSING"
    CHARACTER_BINDING_FORBIDDEN = "CHARACTER_BINDING_FORBIDDEN"


RecordStatus = Literal["DRAFT", "ACTIVE", "ARCHIVED"]
RightsStatus = Literal["UNKNOWN", "SAFE", "RESTRICTED", "EXPIRED", "BLOCKED"]
PromptSafetyState = Literal["UNKNOWN", "PROMPT_SAFE", "NOT_PROMPT_SAFE"]
ReferenceAssetType = Literal["FACE_REF", "FULL_BODY_REF", "STYLE_REF", "POSE_REF", "VOICE_REF", "OTHER"]
ConsentStatus = Literal["NOT_REQUIRED", "REQUIRED", "VERIFIED", "BLOCKED"]
CommercialUseStatus = Literal["UNKNOWN", "ALLOWED", "RESTRICTED", "BLOCKED"]
CharacterBindingScope = Literal["CHANNEL", "CATEGORY", "SERIES", "PROJECT"]


class _ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ContentCategoryCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    category_key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    sub_niche: str | None = None
    audience_segment: str | None = None
    content_pillar: str | None = None
    default_format_policy_json: dict[str, Any] = Field(default_factory=dict)
    default_visual_style_json: dict[str, Any] = Field(default_factory=dict)
    default_voice_style_json: dict[str, Any] = Field(default_factory=dict)
    default_thumbnail_style_json: dict[str, Any] = Field(default_factory=dict)
    visual_mode: str | None = None
    character_policy_mode: CharacterPolicyMode = CharacterPolicyMode.NO_CHARACTER
    allowed_character_binding_scope: str | None = None
    default_memory_scope: str | None = None
    status: RecordStatus = "DRAFT"
    human_approved_at: AwareDatetime | None = None
    source_refs_json: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ContentCategoryRead(ContentCategoryCreate, _ReadModel):
    id: uuid.UUID
    content_hash: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class CategoryCreativeDigestCreate(BaseModel):
    content_category_id: uuid.UUID
    digest_version: int = Field(ge=1)
    digest_json: dict[str, Any] = Field(default_factory=dict)
    source_refs_json: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class CategoryCreativeDigestRead(CategoryCreativeDigestCreate, _ReadModel):
    id: uuid.UUID
    digest_hash: str
    created_at: AwareDatetime


class CharacterProfileCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    character_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    role_description: str | None = None
    persona_json: dict[str, Any] = Field(default_factory=dict)
    status: RecordStatus = "DRAFT"
    human_approved_at: AwareDatetime | None = None
    source_refs_json: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class CharacterProfileRead(CharacterProfileCreate, _ReadModel):
    id: uuid.UUID
    content_hash: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class CharacterVersionCreate(BaseModel):
    character_profile_id: uuid.UUID
    version: int = Field(ge=1)
    identity_json: dict[str, Any] = Field(default_factory=dict)
    visual_identity_json: dict[str, Any] = Field(default_factory=dict)
    voice_identity_json: dict[str, Any] = Field(default_factory=dict)
    continuity_rules_json: dict[str, Any] = Field(default_factory=dict)
    status: RecordStatus = "DRAFT"
    human_approved_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")


class CharacterVersionRead(CharacterVersionCreate, _ReadModel):
    id: uuid.UUID
    content_hash: str
    created_at: AwareDatetime


class CharacterImageBranchCreate(BaseModel):
    character_version_id: uuid.UUID
    branch_key: str = Field(min_length=1)
    visual_branch_json: dict[str, Any] = Field(default_factory=dict)
    provider_constraints_json: dict[str, Any] = Field(default_factory=dict)
    status: RecordStatus = "DRAFT"
    human_approved_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")


class CharacterImageBranchRead(CharacterImageBranchCreate, _ReadModel):
    id: uuid.UUID
    content_hash: str
    created_at: AwareDatetime


class CharacterReferenceAssetPackCreate(BaseModel):
    character_image_branch_id: uuid.UUID
    pack_key: str = Field(min_length=1)
    pack_manifest_json: dict[str, Any] = Field(default_factory=dict)
    rights_status: RightsStatus = "UNKNOWN"
    prompt_safety_state: PromptSafetyState = "UNKNOWN"
    status: RecordStatus = "DRAFT"
    human_approved_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")


class CharacterReferenceAssetPackRead(CharacterReferenceAssetPackCreate, _ReadModel):
    id: uuid.UUID
    content_hash: str
    created_at: AwareDatetime


class CharacterReferenceAssetCreate(BaseModel):
    reference_asset_pack_id: uuid.UUID
    asset_type: ReferenceAssetType = "OTHER"
    cloud_media_ref_id: uuid.UUID | None = None
    local_ref: str | None = None
    source_refs_json: list[dict[str, Any]] = Field(default_factory=list)
    rights_status: RightsStatus = "UNKNOWN"
    prompt_safety_state: PromptSafetyState = "UNKNOWN"
    checksum_sha256: str | None = None

    model_config = ConfigDict(extra="forbid")


class CharacterReferenceAssetRead(CharacterReferenceAssetCreate, _ReadModel):
    id: uuid.UUID
    created_at: AwareDatetime


class VoiceProfileCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    character_profile_id: uuid.UUID | None = None
    voice_key: str = Field(min_length=1)
    language: str = Field(min_length=1)
    accent: str | None = None
    tone_json: dict[str, Any] = Field(default_factory=dict)
    pace_json: dict[str, Any] = Field(default_factory=dict)
    pronunciation_dictionary_ref: str | None = None
    provider_policy_json: dict[str, Any] = Field(default_factory=dict)
    consent_status: ConsentStatus = "NOT_REQUIRED"
    commercial_use_status: CommercialUseStatus = "UNKNOWN"
    status: RecordStatus = "DRAFT"
    human_approved_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")


class VoiceProfileRead(VoiceProfileCreate, _ReadModel):
    id: uuid.UUID
    content_hash: str
    created_at: AwareDatetime


class CharacterBindingCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    content_category_id: uuid.UUID | None = None
    character_profile_id: uuid.UUID
    character_version_id: uuid.UUID
    character_image_branch_id: uuid.UUID | None = None
    reference_asset_pack_id: uuid.UUID | None = None
    voice_profile_id: uuid.UUID | None = None
    binding_scope: CharacterBindingScope = "CATEGORY"
    status: RecordStatus = "DRAFT"
    human_approved_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")


class CharacterBindingRead(CharacterBindingCreate, _ReadModel):
    id: uuid.UUID
    content_hash: str
    created_at: AwareDatetime
