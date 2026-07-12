from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AssetRole = Literal["NATIVE_VISUAL", "SUPPORTING_STOCK", "AI_HERO"]
Orientation = Literal["landscape", "portrait", "square"]
ProjectedCostClass = Literal["NONE", "LOW", "MEDIUM", "HIGH"]
AssetState = Literal[
    "PLANNED",
    "ASSET_SEARCHING",
    "ASSET_SELECTED",
    "ASSET_DOWNLOADING",
    "ASSET_DOWNLOADED",
    "ASSET_NORMALIZED",
    "READY_FOR_RENDER",
    "SEARCH_FAILED",
    "DOWNLOAD_FAILED",
    "CHECKSUM_FAILED",
    "NORMALIZATION_FAILED",
    "BLOCKED_POLICY",
]
ArchiveState = Literal["PLANNED", "UPLOADING", "UPLOADED_UNVERIFIED", "VERIFYING", "VERIFIED", "FAILED"]


class ChannelVisualStrategyProfile(BaseModel):
    profile_ref: str
    profile_hash: str
    channel_id: str
    strategy_key: str
    native_is_backbone: bool = True
    allowed_roles: list[AssetRole] = Field(default_factory=lambda: ["NATIVE_VISUAL", "SUPPORTING_STOCK", "AI_HERO"])
    character_policy_mode: str = "NO_CHARACTER"
    model_config = ConfigDict(extra="forbid")


class ProviderUsagePolicy(BaseModel):
    policy_ref: str
    policy_hash: str
    supported_providers: list[str] = Field(default_factory=lambda: ["NATIVE", "PEXELS", "GOOGLE_VEO"])
    stock_factual_evidence_forbidden: bool = True
    stock_recurring_host_forbidden: bool = True
    ai_hero_filler_forbidden: bool = True
    provider_execution_allowed: bool = False
    model_config = ConfigDict(extra="forbid")


class FormatIdentitySnapshot(BaseModel):
    contract_ref: str
    contract_hash: str
    status: Literal["APPROVED", "PENDING_HUMAN_APPROVAL", "REJECTED"]
    channel_id: str
    character_policy_mode: str
    allowed_asset_roles: list[AssetRole]
    native_explanatory_backbone_required: bool = True
    model_config = ConfigDict(extra="forbid")


class AssetRequest(BaseModel):
    request_id: str
    scene_id: str
    source_segment_ids: list[str] = Field(min_length=1)
    purpose: str
    requested_role: AssetRole
    semantic_visual_intent: str
    required_orientation: Orientation
    minimum_resolution: str
    preferred_resolution: str
    minimum_duration_seconds: float = Field(ge=0)
    maximum_duration_seconds: float = Field(gt=0, le=120)
    crop_policy: str
    person_policy: str
    logo_text_policy: str
    evidence_usage_policy: str
    fallback_order: list[AssetRole] = Field(min_length=1)
    projected_cost_class: ProjectedCostClass
    human_review_required: bool
    request_hash: str
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def policy_consistency(self):
        if self.minimum_duration_seconds > self.maximum_duration_seconds:
            raise ValueError("ASSET_DURATION_RANGE_INVALID")
        if self.requested_role == "SUPPORTING_STOCK":
            if self.evidence_usage_policy == "FACTUAL_EVIDENCE":
                raise ValueError("STOCK_FACTUAL_EVIDENCE_FORBIDDEN")
            if self.person_policy == "RECURRING_HOST":
                raise ValueError("STOCK_RECURRING_HOST_FORBIDDEN")
        if self.requested_role == "AI_HERO" and self.purpose.upper() == "FILLER":
            raise ValueError("AI_HERO_FILLER_FORBIDDEN")
        return self


class CompiledAssetRequestPlan(BaseModel):
    package_id: str
    project_id: str
    channel_id: str
    native_render_plan_ref: str
    native_render_plan_hash: str
    format_identity_ref: str
    format_identity_hash: str
    strategy_profile_ref: str
    strategy_profile_hash: str
    requests: list[AssetRequest]
    native_request_count: int = Field(ge=0)
    supporting_stock_request_count: int = Field(ge=0)
    ai_hero_request_count: int = Field(ge=0)
    unresolved_request_count: int = Field(ge=0)
    provider_execution_allowed: bool = False
    content_hash: str
    model_config = ConfigDict(extra="forbid")


class PexelsQueryPlan(BaseModel):
    request_id: str
    queries: list[str] = Field(min_length=2, max_length=4)
    orientation: Orientation
    size_preference: Literal["small", "medium", "large"]
    per_page: int = Field(ge=1, le=40)
    minimum_resolution: str
    preferred_resolution: str
    minimum_duration_seconds: float = Field(ge=0)
    forbidden_concepts: list[str]
    endpoint: Literal["/v1/videos/search"] = "/v1/videos/search"
    plan_hash: str
    model_config = ConfigDict(extra="forbid")


class ParsedStockCandidate(BaseModel):
    candidate_id: str
    provider_asset_id: str
    source_page_url: str
    creator_name: str
    creator_url: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    composition: str = "UNKNOWN"
    logo_or_text_present: bool | None = None
    identifiable_person_present: bool | None = None
    brand_or_trademark_present: bool | None = None
    motion_suitability: float = Field(default=0.5, ge=0, le=1)
    channel_identity_fit: float = Field(default=0.5, ge=0, le=1)
    prior_use_count: int = Field(default=0, ge=0)
    video_files: list[dict[str, Any]] = Field(default_factory=list)
    source_complete: bool = True
    model_config = ConfigDict(extra="forbid")


class CandidateScore(BaseModel):
    candidate_id: str
    total_score: float
    dimensions: dict[str, float]
    reason_codes: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


class RejectedCandidate(BaseModel):
    candidate_id: str
    reason_codes: list[str]
    model_config = ConfigDict(extra="forbid")


class StockCandidateRankingManifest(BaseModel):
    request_id: str
    candidate_ids: list[str]
    candidate_scores: list[CandidateScore]
    rejected_candidates: list[RejectedCandidate]
    selected_candidate_id: str | None
    ranking_reason_codes: list[str]
    previous_asset_usage_refs: list[str]
    selection_requires_human_review: bool
    manifest_hash: str
    model_config = ConfigDict(extra="forbid")


class PexelsDownloadPlan(BaseModel):
    provider_asset_id: str
    provider_file_id: str
    source_page_url: str
    creator_name: str
    creator_url: str
    selected_download_url_reference: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration: float = Field(gt=0)
    mime_type: str
    expected_usage_role: Literal["SUPPORTING_STOCK"] = "SUPPORTING_STOCK"
    production_eligible: bool = False
    plan_hash: str
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def raw_url_not_durable(self):
        ref = self.selected_download_url_reference.lower()
        if ref.startswith(("http://", "https://")) or "?" in ref:
            raise ValueError("RAW_DOWNLOAD_URL_REFERENCE_FORBIDDEN")
        return self


class StockSourceManifest(BaseModel):
    asset_id: str
    provider: Literal["PEXELS"] = "PEXELS"
    provider_asset_id: str
    provider_file_id: str
    source_page_url: str
    creator_name: str
    creator_url: str
    retrieved_at: datetime
    query_used: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    mime_type: str
    local_path: str
    local_size_bytes: int = Field(gt=0)
    local_sha256: str
    used_by_segments: list[str]
    usage_role: Literal["SUPPORTING_STOCK"] = "SUPPORTING_STOCK"
    rights_policy_ref: str
    attribution_required: bool
    attribution_copy: str
    identifiable_person_present: bool | None
    logo_or_brand_present: bool | None
    human_review_status: str
    manifest_hash: str
    model_config = ConfigDict(extra="forbid")


class AIHeroAssetRequest(BaseModel):
    request_id: str
    package_id: str
    project_id: str
    channel_id: str
    scene_id: str
    source_segment_ids: list[str] = Field(min_length=1)
    visual_intent: str
    hero_reason: Literal["HOOK", "METAPHOR", "EMOTIONAL_PAYOFF", "VISUAL_SIGNATURE", "NATIVE_MOTION_INSUFFICIENT"]
    prompt_text: str
    prompt_hash: str
    prompt_safety_status: Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]
    required_duration_seconds: float = Field(gt=0, le=120)
    preferred_resolution: str
    required_aspect_ratio: Literal["16:9", "9:16"]
    character_policy_mode: str
    projected_cost_class: ProjectedCostClass
    human_approval_required: bool
    provider_resolution_policy_ref: str
    request_hash: str
    model_config = ConfigDict(extra="forbid")


class AIGenerationManifest(BaseModel):
    provider_key: str
    provider_model_id: str
    request_ref: str
    request_hash: str
    external_operation_id: str | None = None
    provider_status: str = "PLANNED"
    prompt_hash: str
    submitted_at: datetime | None = None
    completed_at: datetime | None = None
    output_url_reference: str | None = None
    downloaded_path: str | None = None
    downloaded_sha256: str | None = None
    cost_snapshot_ref: str | None = None
    attempt_record_ref: str | None = None
    media_qc_ref: str | None = None
    synthetic_media_disclosure_ref: str | None = None
    production_eligible: bool = False
    manifest_hash: str
    model_config = ConfigDict(extra="forbid")


class AssetDownloadReceipt(BaseModel):
    request_id: str
    state: AssetState
    states: list[AssetState]
    transport: Literal["LOCAL_FIXTURE_ONLY", "PEXELS_API"] = "LOCAL_FIXTURE_ONLY"
    provider_call_made: bool = False
    production_eligible: bool = False
    local_path: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    completed_at: datetime | None = None
    receipt_hash: str
    model_config = ConfigDict(extra="forbid")


class LocalProjectWorkspaceSummary(BaseModel):
    project_id: str
    workspace_root: str
    workspace_path: str
    directories: list[str]
    available_bytes: int
    ownership_verified: bool
    transport: Literal["LOCAL_FIXTURE_ONLY"] = "LOCAL_FIXTURE_ONLY"
    provider_execution_allowed: bool = False
    summary_hash: str
    model_config = ConfigDict(extra="forbid")


class MediaNormalizationManifest(BaseModel):
    input_asset_ref: str
    input_asset_hash: str
    normalization_profile: dict[str, Any]
    sanitized_ffmpeg_argv_plan: list[str]
    output_path: str
    expected_output_shape: dict[str, Any]
    execution_allowed: bool = False
    manifest_hash: str
    model_config = ConfigDict(extra="forbid")


class ProductionArchiveFileEntry(BaseModel):
    logical_role: str
    source_path: str
    expected_archive_path: str
    size_bytes: int = Field(ge=0)
    sha256: str
    md5: str | None = None
    required_for_archive: bool
    required_for_local_purge: bool
    manifest_hash: str
    model_config = ConfigDict(extra="forbid")


class ProductionArchiveManifest(BaseModel):
    manifest_id: str
    project_id: str
    package_id: str
    sections: list[str]
    files: list[ProductionArchiveFileEntry]
    excluded_paths: list[str]
    total_size_bytes: int = Field(ge=0)
    required_roles_complete: bool
    provider_execution_allowed: bool = False
    manifest_hash: str
    model_config = ConfigDict(extra="forbid")


class DriveArchiveFileReceipt(BaseModel):
    archive_path: str
    local_size: int = Field(ge=0)
    drive_size: int | None = Field(default=None, ge=0)
    local_sha256: str
    drive_sha256: str | None = None
    verified: bool = False
    model_config = ConfigDict(extra="forbid")


class DriveArchiveReceipt(BaseModel):
    archive_manifest_ref: str
    archive_manifest_hash: str
    configured_root_folder_id_reference: str
    root_relative_folder_path: str
    drive_folder_id: str | None = None
    files: list[DriveArchiveFileReceipt]
    total_local_size: int = Field(ge=0)
    total_drive_size: int | None = Field(default=None, ge=0)
    archive_state: ArchiveState
    mismatch_reason_codes: list[str]
    verified_at: datetime | None = None
    provider_call_made: bool = False
    transport: Literal["LOCAL_FIXTURE_ONLY", "GOOGLE_DRIVE_API"] = "LOCAL_FIXTURE_ONLY"
    receipt_hash: str
    model_config = ConfigDict(extra="forbid")


class LocalCleanupReceipt(BaseModel):
    project_id: str
    archive_receipt_ref: str
    archive_receipt_hash: str
    eligibility_status: Literal["ELIGIBLE", "INELIGIBLE"]
    deleted_files: list[str]
    retained_files: list[str]
    failed_deletions: list[str]
    bytes_reclaimed: int = Field(ge=0)
    cleanup_status: Literal["BLOCKED", "ELIGIBLE_NOT_EXECUTED", "COMPLETED", "NOOP_IDEMPOTENT", "FAILED"]
    executed_at: datetime | None = None
    receipt_hash: str
    model_config = ConfigDict(extra="forbid")
