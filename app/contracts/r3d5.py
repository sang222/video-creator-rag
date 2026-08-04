import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


MemoryApprovalStatus = Literal["DRAFT", "REVIEW_REQUIRED", "APPROVED", "REJECTED", "ARCHIVED"]
MemoryRightsStatus = Literal["UNKNOWN", "SAFE", "RESTRICTED", "EXPIRED", "BLOCKED"]
MemoryPromptSafetyState = Literal["UNKNOWN", "PROMPT_SAFE", "NOT_PROMPT_SAFE"]
MemoryReuseScope = Literal["CHANNEL", "CATEGORY", "SERIES", "CHARACTER", "COMPANY_APPROVED"]
MemoryFreshnessState = Literal["FRESH", "STALE", "EXPIRED", "NEEDS_REVIEW"]
MemoryReviewQueueStatus = Literal["PENDING", "IN_REVIEW", "APPROVED", "REJECTED", "NEEDS_CHANGES"]
MemoryApprovalDecisionValue = Literal["APPROVE", "REJECT", "REQUEST_CHANGES", "ARCHIVE"]
MemoryApprovalAuthorityType = Literal["HUMAN", "SYSTEM_POLICY"]
MemoryUsageStatus = Literal["PLANNED", "USED_IN_DIGEST", "BLOCKED", "IGNORED"]


class MemoryFacetInput(BaseModel):
    facet_type: str
    facet_text: str
    scope_json: dict[str, Any] = Field(default_factory=dict)
    allowed_use_cases_json: list[str] = Field(default_factory=list)
    forbidden_use_cases_json: list[str] = Field(default_factory=list)
    polarity: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"] = "NEUTRAL"
    confidence_label: Literal["LOW", "MEDIUM", "HIGH", "UNPROVEN"] = "UNPROVEN"
    prompt_safety_state: MemoryPromptSafetyState = "UNKNOWN"
    embedding_eligible: bool = False

    model_config = ConfigDict(extra="forbid")


class ChannelMemoryDraftCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    content_category_id: uuid.UUID | None = None
    series_id: uuid.UUID | None = None
    character_profile_id: uuid.UUID | None = None
    character_version_id: uuid.UUID | None = None
    character_binding_id: uuid.UUID | None = None
    memory_type: str
    source_type: str
    source_ref: str
    source_content: str | dict[str, Any] | list[Any] | None = None
    summary: str
    rights_status: MemoryRightsStatus = "UNKNOWN"
    prompt_safety_state: MemoryPromptSafetyState = "UNKNOWN"
    reuse_scope: MemoryReuseScope = "CHANNEL"
    freshness_state: MemoryFreshnessState = "FRESH"
    created_from_learning_candidate_id: uuid.UUID | None = None
    created_from_failure_trace_report_id: uuid.UUID | None = None
    created_from_recovery_proposal_id: uuid.UUID | None = None
    created_from_approved_playbook_entry_id: uuid.UUID | None = None
    facets: list[MemoryFacetInput] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class MemoryFromApprovedPlaybookCreate(BaseModel):
    content_category_id: uuid.UUID | None = None
    series_id: uuid.UUID | None = None
    character_profile_id: uuid.UUID | None = None
    character_version_id: uuid.UUID | None = None
    character_binding_id: uuid.UUID | None = None
    reuse_scope: MemoryReuseScope = "CHANNEL"
    memory_type: str = "APPROVED_PLAYBOOK"
    prompt_safe: bool = True
    rights_safe: bool = True
    facet_type: str | None = None
    allowed_use_cases_json: list[str] = Field(default_factory=list)
    embedding_eligible: bool = False

    model_config = ConfigDict(extra="forbid")


class MemoryApprovalRequest(BaseModel):
    decided_by: uuid.UUID | None = None
    rationale: str = "Human memory review decision."
    approved_prompt_use_cases_json: list[str] = Field(default_factory=list)
    rejected_reason_codes_json: list[str] = Field(default_factory=list)
    mark_facets_embedding_eligible: bool = False

    model_config = ConfigDict(extra="forbid")


class MemoryUsageManifestCreate(BaseModel):
    video_project_id: uuid.UUID | None = None
    package_id: uuid.UUID | None = None
    effective_context_snapshot_id: uuid.UUID | None = None
    memory_item_ids_json: list[str] = Field(default_factory=list)
    memory_facet_ids_json: list[str] = Field(default_factory=list)
    use_case: str
    usage_status: MemoryUsageStatus = "PLANNED"
    digest_hash: str | None = None

    model_config = ConfigDict(extra="forbid")


class ChannelMemoryItemRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    content_category_id: uuid.UUID | None
    series_id: uuid.UUID | None
    character_profile_id: uuid.UUID | None
    character_version_id: uuid.UUID | None
    character_binding_id: uuid.UUID | None
    memory_type: str
    source_type: str
    source_ref: str
    source_content_hash: str
    summary: str
    approval_status: MemoryApprovalStatus
    rights_status: MemoryRightsStatus
    prompt_safety_state: MemoryPromptSafetyState
    reuse_scope: MemoryReuseScope
    freshness_state: MemoryFreshnessState
    created_from_learning_candidate_id: uuid.UUID | None
    created_from_failure_trace_report_id: uuid.UUID | None
    created_from_recovery_proposal_id: uuid.UUID | None
    created_from_approved_playbook_entry_id: uuid.UUID | None
    human_approved_at: AwareDatetime | None
    approved_by: uuid.UUID | None
    approval_authority_type: MemoryApprovalAuthorityType | None
    approval_policy_version: str | None
    approval_policy_hash: str | None
    approval_evidence_json: dict[str, Any]
    content_hash: str
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class MemoryFacetRead(BaseModel):
    id: uuid.UUID
    memory_item_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    content_category_id: uuid.UUID | None
    character_profile_id: uuid.UUID | None
    character_version_id: uuid.UUID | None
    facet_type: str
    facet_text: str
    facet_text_hash: str
    scope_json: dict[str, Any]
    allowed_use_cases_json: list[str]
    forbidden_use_cases_json: list[str]
    polarity: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"]
    confidence_label: Literal["LOW", "MEDIUM", "HIGH", "UNPROVEN"]
    prompt_safety_state: MemoryPromptSafetyState
    embedding_eligible: bool
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class MemoryReviewQueueItemRead(BaseModel):
    id: uuid.UUID
    memory_item_id: uuid.UUID
    queue_status: MemoryReviewQueueStatus
    reason_codes_json: list[str]
    reviewer_notes: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class MemoryApprovalDecisionRead(BaseModel):
    id: uuid.UUID
    memory_item_id: uuid.UUID
    decision: MemoryApprovalDecisionValue
    decided_by: uuid.UUID | None
    approval_authority_type: MemoryApprovalAuthorityType | None
    policy_version: str | None
    policy_hash: str | None
    evidence_json: dict[str, Any]
    rationale: str
    approved_prompt_use_cases_json: list[str]
    rejected_reason_codes_json: list[str]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class MemoryUsageManifestRead(BaseModel):
    id: uuid.UUID
    video_project_id: uuid.UUID | None
    package_id: uuid.UUID | None
    effective_context_snapshot_id: uuid.UUID | None
    memory_item_ids_json: list[str]
    memory_facet_ids_json: list[str]
    use_case: str
    usage_status: MemoryUsageStatus
    digest_hash: str | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class MemorySourceLinkRead(BaseModel):
    id: uuid.UUID
    memory_item_id: uuid.UUID
    source_type: str
    source_ref: str
    source_hash: str
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)
