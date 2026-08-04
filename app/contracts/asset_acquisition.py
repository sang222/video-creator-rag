from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.native_renderer import TextSafeRegion
from app.contracts.ai_image import ai_image_stable_hash


AssetRole = Literal[
    "NATIVE_VISUAL",
    "SUPPORTING_STOCK",
    "AI_HERO",
    "AI_EDITORIAL_STILL",
]
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
ArchiveState = Literal[
    "PLANNED", "UPLOADING", "UPLOADED_UNVERIFIED", "VERIFYING", "VERIFIED", "FAILED"
]


class ChannelVisualStrategyProfile(BaseModel):
    profile_ref: str
    profile_hash: str
    channel_id: str
    strategy_key: str
    native_is_backbone: bool = True
    allowed_roles: list[AssetRole] = Field(
        default_factory=lambda: [
            "NATIVE_VISUAL",
            "SUPPORTING_STOCK",
            "AI_HERO",
            "AI_EDITORIAL_STILL",
        ]
    )
    character_policy_mode: str = "NO_CHARACTER"
    model_config = ConfigDict(extra="forbid")


class ProviderUsagePolicy(BaseModel):
    policy_ref: str
    policy_hash: str
    supported_providers: list[str] = Field(
        default_factory=lambda: [
            "NATIVE",
            "PEXELS",
            "GOOGLE_VEO",
            "GOOGLE_GEMINI_IMAGE",
        ]
    )
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
        if self.requested_role == "AI_EDITORIAL_STILL" and self.purpose.upper() == "FILLER":
            raise ValueError("AI_EDITORIAL_STILL_FILLER_FORBIDDEN")
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
    ai_editorial_still_request_count: int = Field(default=0, ge=0)
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
    planner_version: str = "pexels-query-planner/v1.0.0"
    locale: str = "en-US"
    visual_direction_ref: str | None = None
    visual_direction_hash: str | None = None
    target_duration_seconds: float | None = Field(default=None, gt=0)
    aspect_ratio: Literal["16:9"] | None = None
    crop_safety_required: bool = True
    previous_scene_summary: str | None = None
    next_scene_summary: str | None = None
    asset_reuse_history: list[str] = Field(default_factory=list)
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
    environment_type: str | None = None
    industry_context: str | None = None
    lighting_direction: str | None = None
    lighting_temperature: str | None = None
    palette: list[str] = Field(default_factory=list)
    shot_scale: str | None = None
    camera_movement: str | None = None
    motion_intensity: str | None = None
    motion_energy: float | None = Field(default=None, ge=0, le=1)
    semantic_relevance_score: float | None = Field(default=None, ge=0, le=1)
    visual_direction_fit_score: float | None = Field(default=None, ge=0, le=1)
    previous_scene_continuity_score: float | None = Field(default=None, ge=0, le=1)
    next_scene_continuity_score: float | None = Field(default=None, ge=0, le=1)
    crop_safety_score: float | None = Field(default=None, ge=0, le=1)
    technical_quality_score: float | None = Field(default=None, ge=0, le=1)
    originality_score: float | None = Field(default=None, ge=0, le=1)
    explicit_risk_penalty: float = Field(default=0, ge=0, le=1)
    hard_conflict_tags: list[str] = Field(default_factory=list)
    representative_still_refs: list[str] = Field(default_factory=list)
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
    ranking_verdict: Literal["PASS", "REVIEW_REQUIRED", "BLOCK"] = "REVIEW_REQUIRED"
    visual_direction_ref: str | None = None
    visual_direction_hash: str | None = None
    previous_scene_summary: str | None = None
    next_scene_summary: str | None = None
    asset_reuse_history: list[str] = Field(default_factory=list)
    selected_rationale: str | None = None
    ranking_weights: dict[str, float] = Field(default_factory=dict)
    ranking_risk_penalties: dict[str, float] = Field(default_factory=dict)
    ranking_thresholds: dict[str, float] = Field(default_factory=dict)
    manifest_hash: str
    model_config = ConfigDict(extra="forbid")


class PexelsDownloadPlan(BaseModel):
    provider_asset_id: str
    provider_file_id: str
    source_page_url: str
    creator_name: str
    creator_url: str
    volatile_download_reference: str
    download_url_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_media_host: str
    query_present: bool
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
        ref = self.volatile_download_reference.lower()
        if not ref.startswith("volatile://pexels-download/") or "?" in ref:
            raise ValueError("RAW_DOWNLOAD_URL_REFERENCE_FORBIDDEN")
        host = self.expected_media_host.lower()
        if "://" in host or "/" in host or "?" in host or not host:
            raise ValueError("PEXELS_EXPECTED_MEDIA_HOST_INVALID")
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
    hero_reason: Literal[
        "HOOK",
        "METAPHOR",
        "EMOTIONAL_PAYOFF",
        "VISUAL_SIGNATURE",
        "NATIVE_MOTION_INSUFFICIENT",
    ]
    prompt_text: str
    prompt_hash: str
    prompt_safety_status: Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]
    required_duration_seconds: float = Field(gt=0, le=120)
    preferred_resolution: str
    required_aspect_ratio: Literal["16:9"]
    character_policy_mode: str
    projected_cost_class: ProjectedCostClass
    human_approval_required: bool
    provider_resolution_policy_ref: str
    visual_direction_ref: str | None = None
    visual_direction_hash: str | None = None
    prompt_compiler_version: str | None = None
    negative_constraints: list[str] = Field(default_factory=list)
    continuity_hints: list[str] = Field(default_factory=list)
    duration_fit_decision: dict[str, Any] | None = None
    request_hash: str
    model_config = ConfigDict(extra="forbid")


class AIGenerationManifest(BaseModel):
    media_kind: Literal["VIDEO", "STILL_IMAGE"] = "VIDEO"
    provider_key: str
    provider_model_id: str
    request_ref: str
    request_hash: str
    generic_request_ref: str | None = None
    generic_request_hash: str | None = None
    provider_request_id: str | None = None
    provider_operation_id: str | None = None
    external_operation_id: str | None = None
    provider_status: str = "PLANNED"
    prompt_hash: str
    submitted_at: datetime | None = None
    completed_at: datetime | None = None
    output_reference: str | None = None
    output_url_reference: str | None = None
    local_path: str | None = None
    downloaded_path: str | None = None
    size_bytes: int | None = Field(default=None, gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    downloaded_sha256: str | None = None
    image_width: int | None = Field(default=None, gt=0)
    image_height: int | None = Field(default=None, gt=0)
    image_format: Literal["PNG", "JPEG", "WEBP"] | None = None
    cost_snapshot_ref: str | None = None
    attempt_record_ref: str | None = None
    approval_ref: str | None = None
    idempotency_key: str | None = None
    visual_source_decision_ref: str | None = None
    visual_source_decision_hash: str | None = None
    visual_direction_contract_ref: str | None = None
    visual_direction_contract_hash: str | None = None
    native_overlay_required: bool = False
    native_overlay_plan_ref: str | None = None
    native_overlay_plan_hash: str | None = None
    text_safe_regions: list[TextSafeRegion] = Field(default_factory=list)
    post_generation_qc_refs: list[str] = Field(default_factory=list)
    media_qc_ref: str | None = None
    synthetic_media_disclosure_ref: str | None = None
    production_eligible: bool = False
    not_publishable: bool = True
    manifest_hash: str
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def bind_still_image_legacy_aliases(cls, value: Any) -> Any:
        """Expose neutral names without breaking historical video payloads."""

        if (
            not isinstance(value, dict)
            or value.get("media_kind", "VIDEO") != "STILL_IMAGE"
        ):
            return value
        payload = dict(value)
        alias_pairs = (
            ("generic_request_ref", "request_ref"),
            ("generic_request_hash", "request_hash"),
            ("provider_operation_id", "external_operation_id"),
            ("output_reference", "output_url_reference"),
            ("local_path", "downloaded_path"),
            ("sha256", "downloaded_sha256"),
        )
        for neutral_name, legacy_name in alias_pairs:
            neutral_value = payload.get(neutral_name)
            legacy_value = payload.get(legacy_name)
            if neutral_value is None and legacy_value is not None:
                payload[neutral_name] = legacy_value
            elif legacy_value is None and neutral_value is not None:
                payload[legacy_name] = neutral_value
        if not payload.get("post_generation_qc_refs") and payload.get("media_qc_ref"):
            payload["post_generation_qc_refs"] = [payload["media_qc_ref"]]
        if payload.get("media_qc_ref") is None and payload.get(
            "post_generation_qc_refs"
        ):
            payload["media_qc_ref"] = payload["post_generation_qc_refs"][0]
        return payload

    @model_validator(mode="after")
    def validate_still_image_manifest(self) -> "AIGenerationManifest":
        if self.media_kind != "STILL_IMAGE":
            return self

        required_bindings = (
            self.generic_request_ref,
            self.generic_request_hash,
            self.cost_snapshot_ref,
            self.attempt_record_ref,
            self.approval_ref,
            self.idempotency_key,
            self.visual_source_decision_ref,
            self.visual_source_decision_hash,
            self.visual_direction_contract_ref,
            self.visual_direction_contract_hash,
            self.synthetic_media_disclosure_ref,
        )
        if any(not value for value in required_bindings):
            raise ValueError("AI_STILL_IMAGE_MANIFEST_REQUIRED_BINDING_MISSING")

        alias_bindings = (
            (self.generic_request_ref, self.request_ref),
            (self.generic_request_hash, self.request_hash),
            (self.provider_operation_id, self.external_operation_id),
            (self.output_reference, self.output_url_reference),
            (self.local_path, self.downloaded_path),
            (self.sha256, self.downloaded_sha256),
        )
        if any(
            left is not None and right is not None and left != right
            for left, right in alias_bindings
        ):
            raise ValueError("AI_STILL_IMAGE_MANIFEST_ALIAS_BINDING_MISMATCH")

        for reference in (self.output_reference, self.output_url_reference):
            if reference and self._is_raw_or_signed_reference(reference):
                raise ValueError("AI_STILL_IMAGE_RAW_OR_SIGNED_OUTPUT_URL_FORBIDDEN")
        for path in (self.local_path, self.downloaded_path):
            if path and ("://" in path or "?" in path or "#" in path):
                raise ValueError("AI_STILL_IMAGE_LOCAL_PATH_MUST_NOT_BE_URL")

        local_metadata = (self.local_path, self.size_bytes, self.sha256)
        if any(value is not None for value in local_metadata) and not all(
            value is not None for value in local_metadata
        ):
            raise ValueError("AI_STILL_IMAGE_LOCAL_METADATA_INCOMPLETE")
        image_metadata = (self.image_width, self.image_height, self.image_format)
        if any(value is not None for value in image_metadata) and not all(
            value is not None for value in image_metadata
        ):
            raise ValueError("AI_STILL_IMAGE_DIMENSION_FORMAT_METADATA_INCOMPLETE")
        if any(value is not None for value in (*local_metadata, *image_metadata)):
            if not all(
                value is not None for value in (*local_metadata, *image_metadata)
            ):
                raise ValueError("AI_STILL_IMAGE_MATERIALIZATION_METADATA_INCOMPLETE")
            if (
                not self.output_reference
                or not self.completed_at
                or not self.post_generation_qc_refs
            ):
                raise ValueError("AI_STILL_IMAGE_MATERIALIZATION_EVIDENCE_INCOMPLETE")
        normalized_status = self.provider_status.strip().upper()
        submitted_statuses = {"SUBMITTED", "IN_PROGRESS", "PROCESSING"}
        completed_output_statuses = {
            "SUCCEEDED",
            "COMPLETED",
            "MATERIALIZED",
            "FIXTURE_SUCCEEDED",
            "LOCAL_FIXTURE_SUCCEEDED",
            "LOCAL_FIXTURE_MATERIALIZED",
        }
        if normalized_status in submitted_statuses and self.submitted_at is None:
            raise ValueError("AI_STILL_IMAGE_SUBMITTED_TIMESTAMP_REQUIRED")
        if normalized_status in completed_output_statuses:
            completed_evidence = (
                self.completed_at,
                self.output_reference,
                *local_metadata,
                *image_metadata,
            )
            if (
                any(value is None for value in completed_evidence)
                or not self.post_generation_qc_refs
            ):
                raise ValueError("AI_STILL_IMAGE_COMPLETED_EVIDENCE_INCOMPLETE")

        if self.native_overlay_required:
            if (
                not self.native_overlay_plan_ref
                or not self.native_overlay_plan_hash
                or not self.text_safe_regions
            ):
                raise ValueError("AI_STILL_IMAGE_NATIVE_OVERLAY_BINDING_INCOMPLETE")
        elif (
            self.native_overlay_plan_ref
            or self.native_overlay_plan_hash
            or self.text_safe_regions
        ):
            raise ValueError("AI_STILL_IMAGE_NATIVE_OVERLAY_BINDING_UNEXPECTED")

        if len(self.post_generation_qc_refs) != len(
            set(self.post_generation_qc_refs)
        ) or any(not ref.strip() for ref in self.post_generation_qc_refs):
            raise ValueError("AI_STILL_IMAGE_QC_REFERENCE_INVALID")
        if self.production_eligible or not self.not_publishable:
            raise ValueError("AI_STILL_IMAGE_IMG1_NOT_PUBLISHABLE_REQUIRED")
        expected_hash = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"manifest_hash"})
        )
        if self.manifest_hash != expected_hash:
            raise ValueError("AI_STILL_IMAGE_MANIFEST_HASH_MISMATCH")
        return self

    @staticmethod
    def _is_raw_or_signed_reference(value: str) -> bool:
        normalized = value.strip().lower()
        return (
            normalized.startswith(("http://", "https://", "data:"))
            or "?" in value
            or "#" in value
        )


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
    http_evidence: dict[str, Any] | None = None
    media_probe: dict[str, Any] | None = None
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
    drive_file_id: str | None = None
    local_size: int = Field(ge=0)
    drive_size: int | None = Field(default=None, ge=0)
    local_sha256: str
    drive_sha256: str | None = None
    local_md5: str | None = None
    drive_md5: str | None = None
    verification_method: str = "SHA256_OR_DETERMINISTIC_ALTERNATIVE"
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
    cleanup_status: Literal[
        "BLOCKED", "ELIGIBLE_NOT_EXECUTED", "COMPLETED", "NOOP_IDEMPOTENT", "FAILED"
    ]
    executed_at: datetime | None = None
    receipt_hash: str
    model_config = ConfigDict(extra="forbid")
