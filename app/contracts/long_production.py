from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.vcos_v2 import DurationContractV2
from app.contracts.visual_routing import SourceFallbackClass, VisualSourceRoute


class LongProductionExecutionMode(StrEnum):
    OFFLINE_FIXTURE = "OFFLINE_FIXTURE"
    REAL_APPROVED_PRODUCTION = "REAL_APPROVED_PRODUCTION"


class LongProductionState(StrEnum):
    PACKAGE_ACCEPTED = "PACKAGE_ACCEPTED"
    AWAITING_NARRATION = "AWAITING_NARRATION"
    NARRATION_READY = "NARRATION_READY"
    AWAITING_ALIGNMENT = "AWAITING_ALIGNMENT"
    ALIGNMENT_READY = "ALIGNMENT_READY"
    CANONICAL_TIMELINE_READY = "CANONICAL_TIMELINE_READY"
    AWAITING_ASSETS = "AWAITING_ASSETS"
    ASSETS_READY = "ASSETS_READY"
    NATIVE_RENDER_PLAN_READY = "NATIVE_RENDER_PLAN_READY"
    RENDERING = "RENDERING"
    RENDERED_AWAITING_TECHNICAL_QC = "RENDERED_AWAITING_TECHNICAL_QC"
    TECHNICAL_QC_PASSED = "TECHNICAL_QC_PASSED"
    CREATIVE_REVIEW_REQUIRED = "CREATIVE_REVIEW_REQUIRED"
    READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
    HUMAN_REVIEW_PASSED = "HUMAN_REVIEW_PASSED"
    AWAITING_ARCHIVE_VERIFICATION = "AWAITING_ARCHIVE_VERIFICATION"
    FINAL_MEDIA_REGISTERED = "FINAL_MEDIA_REGISTERED"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    FAILED_TECHNICAL = "FAILED_TECHNICAL"


class NarrationRequest(BaseModel):
    request_id: str = Field(min_length=1)
    scripted_package_ref: str = Field(min_length=1)
    script_hash: str = Field(min_length=1)
    spoken_text_hash: str = Field(min_length=1)
    voice_policy_ref: str = Field(min_length=1)
    pacing_policy_ref: str = Field(min_length=1)
    provider_execution_plan_ref: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    execution_mode: LongProductionExecutionMode
    duration_contract: DurationContractV2 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class NarrationResult(BaseModel):
    request_id: str = Field(min_length=1)
    provider_key: str = Field(min_length=1)
    provider_manifest_ref: str = Field(min_length=1)
    audio_asset_ref: str = Field(min_length=1)
    audio_sha256: str = Field(min_length=1)
    duration_ms: int = Field(gt=0)
    duration_contract: DurationContractV2 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    fixture_only: bool
    provider_call_made: bool
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def duration_matches_contract(self) -> "NarrationResult":
        if self.duration_contract is not None and not (
            self.duration_contract.minimum_duration_ms
            <= self.duration_ms
            <= self.duration_contract.maximum_duration_ms
        ):
            raise ValueError("NARRATION_DURATION_OUTSIDE_CHANNEL_CONTRACT")
        return self


class ForcedAlignmentRequest(BaseModel):
    request_id: str = Field(min_length=1)
    narration_request_id: str = Field(min_length=1)
    audio_asset_ref: str = Field(min_length=1)
    audio_sha256: str = Field(min_length=1)
    script_hash: str = Field(min_length=1)
    spoken_text_hash: str = Field(min_length=1)
    strict_token_coverage: Literal[1.0] = 1.0
    estimated_timing_fallback_allowed: Literal[False] = False
    idempotency_key: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class VisualSourceBinding(BaseModel):
    scene_id: str = Field(min_length=1)
    decision_ref: str = Field(min_length=1)
    decision_hash: str = Field(min_length=1)
    preferred_route: VisualSourceRoute
    fallback_class: SourceFallbackClass
    routing_reason_codes: list[str] = Field(min_length=1)
    eligibility_gate_refs: list[str] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def resolved_route(self) -> "VisualSourceBinding":
        if self.preferred_route == VisualSourceRoute.UNRESOLVED_BLOCK:
            raise ValueError("LPRO1_UNRESOLVED_VISUAL_SOURCE_BLOCK")
        return self


class ResolvedMediaAsset(BaseModel):
    asset_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    source_decision_ref: str = Field(min_length=1)
    source_decision_hash: str = Field(min_length=1)
    actual_route: VisualSourceRoute
    local_file_ref: str = Field(min_length=1)
    checksum_sha256: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_ms: int | None = Field(default=None, gt=0)
    rights_status: Literal["CONFIRMED", "NOT_REQUIRED"]
    provenance_refs: list[str] = Field(min_length=1)
    normalization_state: Literal["NORMALIZED"]
    scene_usage_ref: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class MediaNormalizationItem(BaseModel):
    asset_id: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    source_checksum: str = Field(min_length=1)
    normalized_ref: str = Field(min_length=1)
    normalized_checksum: str = Field(min_length=1)
    byte_probe: dict[str, Any] = Field(min_length=1)
    state: Literal["PASS"]

    model_config = ConfigDict(extra="forbid")


class MediaNormalizationManifest(BaseModel):
    manifest_id: str = Field(min_length=1)
    items: list[MediaNormalizationItem] = Field(min_length=1)
    target_video: dict[str, Any]
    target_audio: dict[str, Any]
    actual_byte_probe_required: Literal[True] = True
    result: Literal["PASS"]
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class LongFormRenderPackageStrictContract(BaseModel):
    """Strict production projection embedded in the existing render package manifest."""

    contract_version: Literal["lpro1.long-form-render-package.v1"] = "lpro1.long-form-render-package.v1"
    scripted_package_ref: str = Field(min_length=1)
    scripted_package_hash: str = Field(min_length=1)
    production_package_schema_version: Literal["v2"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    production_package_artifact_version_id: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    duration_contract: DurationContractV2 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    project_ref: str = Field(min_length=1)
    project_hash: str = Field(min_length=1)
    channel_profile_version_ref: str = Field(min_length=1)
    compiled_policy_snapshot_ref: str = Field(min_length=1)
    compiled_policy_snapshot_hash: str = Field(min_length=1)
    channel_contract_hash: str = Field(min_length=1)
    niche_contract_digest_ref: str = Field(min_length=1)
    niche_contract_digest_hash: str = Field(min_length=1)
    effective_context_ref: str = Field(min_length=1)
    effective_context_hash: str = Field(min_length=1)
    niche_alignment_dossier_ref: str = Field(min_length=1)
    niche_alignment_dossier_hash: str = Field(min_length=1)
    narration_request_ref: str = Field(min_length=1)
    narration_result_ref: str = Field(min_length=1)
    audio_asset_ref: str = Field(min_length=1)
    audio_asset_hash: str = Field(min_length=1)
    verified_alignment_ref: str = Field(min_length=1)
    verified_alignment_hash: str = Field(min_length=1)
    verified_alignment_status: Literal["PASS"] = "PASS"
    canonical_timeline_ref: str = Field(min_length=1)
    canonical_timeline_hash: str = Field(min_length=1)
    caption_track_ref: str = Field(min_length=1)
    caption_track_hash: str = Field(min_length=1)
    visual_direction_contract_ref: str = Field(min_length=1)
    visual_direction_contract_hash: str = Field(min_length=1)
    visual_source_decisions: list[VisualSourceBinding] = Field(min_length=1)
    resolved_assets: list[ResolvedMediaAsset] = Field(min_length=1)
    asset_usage_manifest_ref: str = Field(min_length=1)
    asset_usage_manifest_hash: str = Field(min_length=1)
    media_normalization_manifest_ref: str = Field(min_length=1)
    media_normalization_manifest_hash: str = Field(min_length=1)
    native_render_policy_snapshot_ref: str = Field(min_length=1)
    native_render_policy_snapshot_hash: str = Field(min_length=1)
    native_render_plan_ref: str = Field(min_length=1)
    native_render_plan_hash: str = Field(min_length=1)
    renderer_eligibility: Literal["PASS"] = "PASS"
    provider_execution_plan_ref: str = Field(min_length=1)
    provider_execution_plan_hash: str = Field(min_length=1)
    cost_estimate_snapshot_ref: str = Field(min_length=1)
    cost_estimate_snapshot_hash: str = Field(min_length=1)
    approval_refs: list[str] = Field(min_length=1)
    idempotency_refs: list[str] = Field(min_length=1)
    target_video_type: Literal["long_form"] = "long_form"
    target_duration_seconds: float = Field(gt=0)
    target_aspect_ratio: Literal["16:9"] = "16:9"
    target_resolution: Literal["1920x1080"] = "1920x1080"
    minimum_effective_resolution: Literal["1080p"] = "1080p"
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def complete_scene_bindings(self) -> "LongFormRenderPackageStrictContract":
        decisions = {item.scene_id: item for item in self.visual_source_decisions}
        assets = {item.scene_id: item for item in self.resolved_assets}
        if len(decisions) != len(self.visual_source_decisions):
            raise ValueError("LPRO1_DUPLICATE_VISUAL_SOURCE_DECISION")
        if set(decisions) != set(assets):
            raise ValueError("LPRO1_SCENE_ASSET_RESOLUTION_INCOMPLETE")
        for scene_id, asset in assets.items():
            decision = decisions[scene_id]
            if (
                asset.source_decision_ref != decision.decision_ref
                or asset.source_decision_hash != decision.decision_hash
                or asset.actual_route != decision.preferred_route
            ):
                raise ValueError("LPRO1_ASSET_DECISION_BINDING_MISMATCH")
        if self.production_package_schema_version == "v2":
            if (
                not self.production_package_artifact_version_id
                or self.duration_contract is None
                or self.production_package_artifact_version_id
                not in self.scripted_package_ref
            ):
                raise ValueError("LPRO1_V2_PACKAGE_DURATION_AUTHORITY_REQUIRED")
            duration_ms = round(self.target_duration_seconds * 1000)
            if not (
                self.duration_contract.minimum_duration_ms
                <= duration_ms
                <= self.duration_contract.maximum_duration_ms
            ):
                raise ValueError("LPRO1_DURATION_OUTSIDE_CHANNEL_CONTRACT")
        return self


class ProductionRenderExecutionEnvelope(BaseModel):
    envelope_version: Literal["lpro1.production-render-envelope.v1"] = "lpro1.production-render-envelope.v1"
    execution_mode: LongProductionExecutionMode
    project_ref: str = Field(min_length=1)
    package_ref: str = Field(min_length=1)
    plan_ref: str = Field(min_length=1)
    plan_hash: str = Field(min_length=1)
    production_eligible: bool
    operator_approval_ref: str | None = None
    provider_execution_plan_ref: str | None = None
    cost_snapshot_ref: str | None = None
    human_review_policy_ref: str | None = None
    archive_policy_ref: str | None = None
    mr1_scoped_approval_ref: str | None = None
    idempotency_key: str = Field(min_length=1)
    authorization_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def mode_is_explicit(self) -> "ProductionRenderExecutionEnvelope":
        if self.production_eligible:
            required = (
                self.operator_approval_ref,
                self.provider_execution_plan_ref,
                self.cost_snapshot_ref,
                self.human_review_policy_ref,
                self.archive_policy_ref,
                self.mr1_scoped_approval_ref,
            )
            if self.execution_mode != LongProductionExecutionMode.REAL_APPROVED_PRODUCTION or not all(required):
                raise ValueError("LPRO1_PRODUCTION_EXECUTION_ENVELOPE_INCOMPLETE")
        elif self.execution_mode != LongProductionExecutionMode.OFFLINE_FIXTURE:
            raise ValueError("LPRO1_OFFLINE_EXECUTION_MODE_REQUIRED")
        return self


class ReviewMediaCandidate(BaseModel):
    candidate_id: str = Field(min_length=1)
    project_ref: str = Field(min_length=1)
    package_ref: str = Field(min_length=1)
    plan_ref: str = Field(min_length=1)
    output_file_ref: str = Field(min_length=1)
    output_sha256: str = Field(min_length=1)
    technical_media_qc_ref: str = Field(min_length=1)
    technical_media_qc_hash: str = Field(min_length=1)
    creative_media_qc_ref: str = Field(min_length=1)
    creative_media_qc_hash: str = Field(min_length=1)
    production_eligible: bool
    not_publishable: bool
    human_review_status: Literal["PENDING", "PASS", "FAIL"] = "PENDING"
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def truthful_fixture_boundary(self) -> "ReviewMediaCandidate":
        if not self.production_eligible and not self.not_publishable:
            raise ValueError("LPRO1_NON_PRODUCTION_CANDIDATE_MUST_NOT_BE_PUBLISHABLE")
        return self


class TestHumanReviewReceipt(BaseModel):
    candidate_ref: str = Field(min_length=1)
    reviewed_hash: str = Field(min_length=1)
    decision: Literal["PASS", "FAIL"]
    authority: Literal["TEST_AUTHORITY"] = "TEST_AUTHORITY"
    production_eligible: Literal[False] = False
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class FinalMediaCloseoutRequest(BaseModel):
    production_eligible: Literal[True]
    review_candidate: ReviewMediaCandidate
    human_review_decision: Literal["PASS", "PENDING", "FAIL"]
    reviewed_hash: str | None
    human_review_receipt_ref: str | None
    technical_qc_result: Literal["PASS", "FAIL"]
    creative_review_result: Literal["ACCEPTED", "REVIEW_REQUIRED", "BLOCK"]
    archive_required: bool
    archive_verification_result: Literal["PASS", "PENDING", "NOT_REQUIRED"]
    package_lineage_valid: bool
    legacy_incomplete_package: bool = False
    provenance_complete: bool
    rights_disclosure_resolved: bool
    file_ref: str | None
    file_checksum: str | None

    model_config = ConfigDict(extra="forbid")


class LongProductionOrchestrationReceipt(BaseModel):
    orchestrator_version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    execution_mode: LongProductionExecutionMode
    current_state: LongProductionState
    package_ref: str = Field(min_length=1)
    project_ref: str = Field(min_length=1)
    lineage_refs: dict[str, Any]
    narration_refs: dict[str, str] = Field(default_factory=dict)
    alignment_refs: dict[str, str] = Field(default_factory=dict)
    canonical_timeline_ref: str | None = None
    asset_resolution_refs: list[str] = Field(default_factory=list)
    normalization_ref: str | None = None
    native_render_plan_ref: str | None = None
    native_motion_compiler_ref: str | None = None
    ffmpeg_receipt_ref: str | None = None
    technical_media_qc_ref: str | None = None
    creative_media_qc_ref: str | None = None
    review_media_candidate_ref: str | None = None
    final_media_ref: str | None = None
    provider_calls: int = Field(default=0, ge=0)
    render_attempts: int = Field(default=0, ge=0)
    state_transitions: list[str] = Field(default_factory=list)
    idempotency_fingerprint: str = Field(min_length=1)
    blockers: list[str] = Field(default_factory=list)
    exact_next_action: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class LongProductionRunRequest(BaseModel):
    package_id: UUID | None = None
    execution_mode: LongProductionExecutionMode = LongProductionExecutionMode.OFFLINE_FIXTURE
    resume_token: str | None = None
    execution_envelope: ProductionRenderExecutionEnvelope | None = None

    model_config = ConfigDict(extra="forbid")


class LongProductionStatusRead(BaseModel):
    project_id: str
    current_state: LongProductionState
    package_readiness: str
    narration_status: str
    alignment_status: str
    timeline_status: str
    asset_status: str
    render_plan_status: str
    render_status: str
    technical_qc_status: str
    creative_qc_status: str
    human_review_status: str
    archive_status: str
    final_media_ref_status: str
    blockers: list[str] = Field(default_factory=list)
    exact_next_action: str
    receipt: LongProductionOrchestrationReceipt | None = None

    model_config = ConfigDict(extra="forbid")
