import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


FormatIdentityStatus = Literal["DRAFT", "PENDING_HUMAN_APPROVAL", "APPROVED", "REJECTED", "SUPERSEDED"]
OriginalityGateStatus = Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]


class FormatIdentityContractDraftRequest(BaseModel):
    channel_id: uuid.UUID
    channel_profile_version_id: uuid.UUID | None = None
    effective_context_snapshot_id: uuid.UUID | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class FormatIdentityContractRead(BaseModel):
    id: uuid.UUID
    channel_id: uuid.UUID
    channel_profile_version_id: uuid.UUID | None
    effective_context_snapshot_id: uuid.UUID | None
    contract_version: int
    status: FormatIdentityStatus
    character_policy_mode: str
    content: dict[str, Any]
    content_hash: str
    created_by: str
    approved_by: str | None
    approved_at: AwareDatetime | None
    created_at: AwareDatetime

    model_config = ConfigDict(from_attributes=True)


class FormatIdentityDecisionRequest(BaseModel):
    decided_by: str = Field(min_length=1)
    rationale: str | None = None

    model_config = ConfigDict(extra="forbid")


class ClaimEvidenceInput(BaseModel):
    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    claim_scope: Literal["TITLE", "HOOK", "SCRIPT", "DESCRIPTION", "THUMBNAIL", "VISUAL"] = "SCRIPT"
    claim_type: Literal["EVIDENCE_BACKED", "SCENARIO_BASED", "ESTIMATE", "ILLUSTRATIVE", "UNSUPPORTED"]
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_dates: list[str] = Field(default_factory=list)
    confidence: str = "MEDIUM"
    assumptions: list[str] = Field(default_factory=list)
    calculation_summary: str | None = None
    allowed_wording: list[str] = Field(default_factory=list)
    forbidden_wording: list[str] = Field(default_factory=list)
    visual_representation_notes: str | None = None
    disclaimer_required: bool = False
    human_review_required: bool = True

    model_config = ConfigDict(extra="forbid")


class SyntheticDisclosureInput(BaseModel):
    realistic_ai_person_present: bool = False
    real_person_likeness_used: bool = False
    real_event_altered: bool = False
    synthetic_voice_used: bool = False
    fictional_character_used: bool = False
    ai_generated_video_used: bool = False
    ai_generated_image_used: bool = False
    stock_media_used: bool = False
    platform_label_required: bool = False
    platform_label_reason_codes: list[str] = Field(default_factory=list)
    disclosure_copy: str = ""
    operator_confirmation_required: bool = True
    operator_confirmation_status: str = "PENDING"
    provenance_manifest_refs: list[dict[str, Any]] = Field(default_factory=list)
    final_asset_confirmation_pending: bool = True

    model_config = ConfigDict(extra="forbid")


class OriginalityGateRead(BaseModel):
    gate_key: str
    status: OriginalityGateStatus
    reason_codes: list[str] = Field(default_factory=list)
    compared_episode_refs: list[dict[str, Any]] = Field(default_factory=list)
    comparison_dimensions: list[str] = Field(default_factory=list)
    explanation: str
    recommended_next_action: str
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class OriginalityReviewRead(BaseModel):
    package_id: uuid.UUID
    format_identity: dict[str, Any]
    episode_originality: dict[str, Any]
    claim_evidence: dict[str, Any]
    packaging_truthfulness: dict[str, Any]
    synthetic_disclosure: dict[str, Any]
    platform_plans: list[dict[str, Any]] = Field(default_factory=list)
    final_originality_verdict: OriginalityGateStatus
    compared_recent_episodes: list[dict[str, Any]] = Field(default_factory=list)
    exact_next_action: str
    plain_language_summary: str
    technical_details: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
