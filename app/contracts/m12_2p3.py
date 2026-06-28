from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


PublicPresenceMode = Literal["EXISTING_PUBLIC_CHANNEL", "NEW_CHANNEL_NO_PUBLIC_FOOTPRINT"]
ChannelInitWorkflowStatus = Literal[
    "RESEARCH_PENDING",
    "RESEARCH_COMPLETE",
    "NEEDS_HUMAN_REVIEW",
    "READY_TO_COMPILE",
    "COMPILED_PARTIAL",
    "COMPILED_COMPLETE",
    "ACTIVATED",
    "BLOCKED",
]
ChannelContractStatus = Literal["COMPLETE", "PARTIAL", "MISSING", "STALE", "CONTRADICTORY"]
FieldSourceType = Literal[
    "ADMIN_INPUT",
    "ADMIN_HINT",
    "PUBLIC_RESEARCH_EVIDENCE",
    "RESEARCH_INFERENCE",
    "HUMAN_CONFIRMED",
    "GLOBAL_LOCKED_POLICY",
    "PROVIDER_POLICY",
    "COMPILER_DERIVED",
    "UNKNOWN",
]
ConfidenceLabel = Literal["LOW", "MEDIUM", "HIGH"]
ReviewAction = Literal["confirm", "edit", "reject", "mark_unknown", "add_note"]


class FieldMeta(BaseModel):
    value: Any = None
    source_type: FieldSourceType
    confidence_label: ConfidenceLabel
    evidence_refs: list[str] = Field(default_factory=list)
    review_required: bool
    editable_by_human: bool = True
    locked_reason: str | None = None

    model_config = ConfigDict(extra="forbid")


class MinimalAdminInput(BaseModel):
    company_id: uuid.UUID
    channel_name: str = Field(min_length=1)
    public_presence_mode: PublicPresenceMode
    youtube_url_or_handle: str | None = None
    website_url: str | None = None
    social_profile_links: list[str] = Field(default_factory=list)
    operator_note_purpose: str = Field(min_length=1)
    intended_content_language: str | None = None
    intended_primary_market: str | None = None
    owner_operator_language: str = "vi-VN"
    initial_topic_pillar_hints: list[str] = Field(default_factory=list)
    source_usage_attestation: bool

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_public_anchor(self) -> "MinimalAdminInput":
        anchors = [
            self.youtube_url_or_handle,
            self.website_url,
            *self.social_profile_links,
        ]
        has_anchor = any(str(anchor or "").strip() for anchor in anchors)
        if self.public_presence_mode == "EXISTING_PUBLIC_CHANNEL" and not has_anchor:
            raise ValueError("existing public channel requires at least one public source anchor")
        return self


class EvidenceRef(BaseModel):
    ref_id: str
    source_type: Literal["YOUTUBE_API", "PUBLIC_WEB", "ADMIN_NOTE", "SOCIAL_PROFILE", "CONNECTOR_SNIPPET"]
    url: str | None = None
    title: str | None = None
    snippet: str | None = None
    captured_at: AwareDatetime
    reliability: ConfidenceLabel

    model_config = ConfigDict(extra="forbid")


class ChannelInitDraftCreate(MinimalAdminInput):
    pass


class ChannelContractDraftRead(BaseModel):
    id: uuid.UUID
    init_draft_id: uuid.UUID
    company_id: uuid.UUID
    channel_name: str
    source_urls: list[dict[str, Any]]
    admin_minimal_input: dict[str, Any]
    suggested_channel_contract: dict[str, Any]
    field_source_map_json: dict[str, FieldMeta]
    confidence_summary: dict[str, ConfidenceLabel]
    missing_fields: list[str]
    human_questions: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    evidence_refs: list[EvidenceRef]
    workflow_status: ChannelInitWorkflowStatus
    contract_status: ChannelContractStatus | None
    review_decision_log_json: list[dict[str, Any]]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class ChannelInitDraftRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_name: str
    public_presence_mode: PublicPresenceMode
    youtube_url_or_handle: str | None
    website_url: str | None
    social_profile_links: list[str]
    operator_note_purpose: str
    intended_content_language: str | None
    intended_primary_market: str | None
    owner_operator_language: str
    initial_topic_pillar_hints: list[str]
    source_usage_attestation: bool
    workflow_status: ChannelInitWorkflowStatus
    contract_status: ChannelContractStatus | None
    channel_id: uuid.UUID | None
    channel_profile_version_id: uuid.UUID | None
    compiled_policy_snapshot_id: uuid.UUID | None
    latest_contract_draft: ChannelContractDraftRead | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class ChannelInitResearchRequest(BaseModel):
    enable_optional_web_snippets: bool = False

    model_config = ConfigDict(extra="forbid")


class ReviewFieldDecision(BaseModel):
    field_path: str
    action: ReviewAction
    value: Any = None
    note: str | None = None
    reviewer_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class ChannelContractReviewRequest(BaseModel):
    decisions: list[ReviewFieldDecision] = Field(default_factory=list)
    human_notes: str | None = None

    model_config = ConfigDict(extra="forbid")


class ChannelInitCompileRequest(BaseModel):
    correlation_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class ChannelInitCompileResult(BaseModel):
    init_draft_id: uuid.UUID
    channel_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    compiled_policy_snapshot_id: uuid.UUID
    workflow_status: ChannelInitWorkflowStatus
    contract_status: ChannelContractStatus
    missing_fields: list[str]
    contradiction_reasons: list[str]
    activation_eligibility: bool
    channel_contract_json: dict[str, Any]
    field_source_map_json: dict[str, FieldMeta]

    model_config = ConfigDict(extra="forbid")


class ChannelContractPreviewRead(BaseModel):
    init_draft_id: uuid.UUID
    contract_status: ChannelContractStatus | None
    workflow_status: ChannelInitWorkflowStatus
    channel_contract_json: dict[str, Any]
    field_source_map_json: dict[str, FieldMeta]
    missing_fields: list[str]
    contradiction_reasons: list[str] = Field(default_factory=list)
    field_source_coverage: dict[str, Any]

    model_config = ConfigDict(extra="forbid")
