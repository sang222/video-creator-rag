from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.ai_image import ImageGateVerdict, ai_image_stable_hash
from app.contracts.creative_quality_canary import CreativeGateEvidence, TechnicalMediaQCReport
from app.contracts.google_gemini_image import (
    GeminiImageCostEstimateSnapshot,
    GeminiImageGenerationRequest,
)
from app.contracts.img_canary import (
    IMG_CANARY_HARD_CAP_USD,
    IMGCanaryAttemptLedger,
    IMGCanaryProviderResponseSummary,
    IMGCanaryScopedApproval,
)
from app.contracts.native_renderer import TextSafeRegion


VQC1_SCHEMA_VERSION = "vqc1.image-visual-quality-control.v1"

ImageVisualGateName = Literal[
    "GeneratedTextArtifactGate",
    "GeneratedNumberArtifactGate",
    "FakeUILogoGate",
    "WatermarkArtifactGate",
    "CompositionComplianceGate",
    "SemanticMatchGate",
    "VisualLanguageMatchGate",
    "TechnicalImageFitnessGate",
    "CropSafetyGate",
    "ReuseSimilarityGate",
    "VisualContinuityGate",
    "RightsDisclosureCompletenessGate",
    "NativeOverlayComplianceGate",
    "HumanVisualApprovalGate",
]

VQC1_REQUIRED_GATES: tuple[ImageVisualGateName, ...] = (
    "GeneratedTextArtifactGate",
    "GeneratedNumberArtifactGate",
    "FakeUILogoGate",
    "WatermarkArtifactGate",
    "CompositionComplianceGate",
    "SemanticMatchGate",
    "VisualLanguageMatchGate",
    "TechnicalImageFitnessGate",
    "CropSafetyGate",
    "ReuseSimilarityGate",
    "VisualContinuityGate",
    "RightsDisclosureCompletenessGate",
    "NativeOverlayComplianceGate",
    "HumanVisualApprovalGate",
)

HUMAN_VISUAL_REVIEW_DIMENSIONS = (
    "METAPHOR_CLARITY",
    "GENERATED_ARTIFACT_ABSENCE",
    "VISUAL_LANGUAGE_FIT",
    "NATIVE_HEADLINE_READABILITY",
    "CROP_AND_MOTION_PRESERVATION",
    "AUTHORED_NOT_GENERIC",
    "PRODUCTION_PATTERN_ACCEPTABILITY",
)


def img_canary_provider_request_lineage_ref(
    *,
    attempt: IMGCanaryAttemptLedger,
    response: IMGCanaryProviderResponseSummary,
) -> str | None:
    """Resolve the truthful request lineage used by VQC.

    Gemini's successful Interactions response does not always expose a
    provider request ID.  V3 may then refer to the immutable, hash-bound
    provider response itself.  Historical V1/V2 behavior stays strict: they
    still require the explicit ID that was required when those contracts were
    frozen.
    """

    attempt_ref = attempt.provider_request_id_ref
    response_ref = response.provider_request_id_ref
    if attempt_ref is not None or response_ref is not None:
        return attempt_ref if attempt_ref == response_ref else None
    if (
        not attempt.run_id.startswith("img-canary-v3-")
        or attempt.run_id != response.run_id
        or attempt.status != "SUCCEEDED"
        or not attempt.provider_call_made
        or attempt.attempts_consumed != 1
        or response.provider_status != "INTERACTION_COMPLETED"
        or response.output_count != 1
        or response.completed_at is None
        or response.provider_attempts_consumed != 1
        or attempt.provider_operation_id_ref
        != response.provider_operation_id_ref
    ):
        return None
    if response.content_hash != ai_image_stable_hash(
        response.model_dump(mode="json", exclude={"content_hash"})
    ):
        return None
    return (
        f"evidence://img-canary-v3/{response.run_id}/provider-response/"
        f"{response.content_hash}"
    )


class _HashBoundEvidence(BaseModel):
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_content_hash(self) -> "_HashBoundEvidence":
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("VQC1_EVIDENCE_HASH_MISMATCH")
        return self


class NormalizedImageRegion(BaseModel):
    region_id: str = Field(min_length=1)
    region_role: Literal[
        "INTENDED_CROP",
        "SUBJECT_FOCAL",
        "PROTECTED_VISUAL",
        "NATIVE_OVERLAY",
        "SUSPECTED_ARTIFACT",
    ]
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)
    coordinate_space: Literal["normalized"] = "normalized"

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_bounds(self) -> "NormalizedImageRegion":
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("VQC1_NORMALIZED_REGION_OUT_OF_BOUNDS")
        return self


class GeneratedArtifactRegion(BaseModel):
    region: NormalizedImageRegion
    artifact_kind: Literal["TEXT", "NUMBER", "FAKE_UI", "LOGO", "WATERMARK"]
    assessment_state: Literal["SUSPECTED", "DETECTED"]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    representative_crop_ref: str = Field(min_length=1)
    repairability: Literal[
        "NATIVE_OVERLAY_REPAIR",
        "OUTSIDE_VISIBLE_CROP_REVIEW",
        "NOT_REPAIRABLE",
    ]
    review_notes: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def artifact_box_role(self) -> "GeneratedArtifactRegion":
        if self.region.region_role != "SUSPECTED_ARTIFACT":
            raise ValueError("VQC1_ARTIFACT_REGION_ROLE_INVALID")
        return self


class GeneratedArtifactInspectionEvidence(_HashBoundEvidence):
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inspection_state: Literal["ASSESSED", "PENDING"]
    inspection_authority: Literal["GOLDEN_FIXTURE", "HUMAN_OBSERVATION", "UNASSESSED"]
    detected_or_suspected_regions: list[GeneratedArtifactRegion] = Field(default_factory=list)
    representative_crop_refs: list[str] = Field(default_factory=list)
    review_notes: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_inspection_authority(self) -> "GeneratedArtifactInspectionEvidence":
        if self.inspection_state == "PENDING":
            if self.inspection_authority != "UNASSESSED":
                raise ValueError("VQC1_PENDING_ARTIFACT_INSPECTION_AUTHORITY_INVALID")
            if self.detected_or_suspected_regions:
                raise ValueError("VQC1_PENDING_ARTIFACT_INSPECTION_HAS_FINDINGS")
        elif self.inspection_authority == "UNASSESSED":
            raise ValueError("VQC1_ASSESSED_ARTIFACT_INSPECTION_AUTHORITY_MISSING")
        region_ids = [item.region.region_id for item in self.detected_or_suspected_regions]
        if len(region_ids) != len(set(region_ids)):
            raise ValueError("VQC1_DUPLICATE_ARTIFACT_REGION")
        return self


class ReuseSimilarityEvidence(_HashBoundEvidence):
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    comparison_method: Literal["SHA256_EXACT"] = "SHA256_EXACT"
    comparison_asset_refs: list[str] = Field(default_factory=list)
    comparison_asset_sha256: list[str] = Field(default_factory=list)
    prior_use_count: int = Field(default=0, ge=0)
    isolated_canary_scope: Literal[True] = True
    perceptual_hash_available: Literal[False] = False

    @model_validator(mode="after")
    def validate_comparison_bindings(self) -> "ReuseSimilarityEvidence":
        if len(self.comparison_asset_refs) != len(self.comparison_asset_sha256):
            raise ValueError("VQC1_REUSE_COMPARISON_BINDING_MISMATCH")
        if len(self.comparison_asset_refs) != len(set(self.comparison_asset_refs)):
            raise ValueError("VQC1_REUSE_COMPARISON_REF_DUPLICATE")
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in self.comparison_asset_sha256
        ):
            raise ValueError("VQC1_REUSE_COMPARISON_HASH_INVALID")
        return self


class NativeOverlayInputs(_HashBoundEvidence):
    generated_image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_overlay_plan_ref: str = Field(min_length=1)
    native_overlay_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_overlay_binding_ref: str = Field(min_length=1)
    native_overlay_binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authoritative_text_ref: str = Field(min_length=1)
    authoritative_text: str = Field(min_length=1)
    exact_text_native_authority: Literal[True] = True
    generated_image_owns_final_text: Literal[False] = False
    overlay_region: NormalizedImageRegion
    foreground_relative_luminance: float = Field(ge=0.0, le=1.0)
    background_relative_luminance: float = Field(ge=0.0, le=1.0)
    minimum_contrast_ratio: float = Field(ge=1.0, le=21.0)
    font_size_px: int = Field(gt=0)
    minimum_readable_font_size_px: int = Field(gt=0)
    text_fits_without_shrinking: bool

    @model_validator(mode="after")
    def validate_overlay_role(self) -> "NativeOverlayInputs":
        if self.overlay_region.region_role != "NATIVE_OVERLAY":
            raise ValueError("VQC1_NATIVE_OVERLAY_REGION_ROLE_INVALID")
        return self


class StructuredVisualReviewEvidence(_HashBoundEvidence):
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_state: Literal["PENDING"] = "PENDING"
    scene_meaning: str = Field(min_length=1)
    intended_metaphor: str = Field(min_length=1)
    required_composition: list[str] = Field(min_length=1)
    forbidden_interpretations: list[str] = Field(min_length=1)
    channel_visual_language: list[str] = Field(min_length=1)
    observed_output_summary: str = Field(min_length=1)
    semantic_concerns: list[str] = Field(default_factory=list)
    style_concerns: list[str] = Field(default_factory=list)
    continuity_concerns: list[str] = Field(default_factory=list)
    isolated_canary_scope: Literal[True] = True
    adjacent_scene_refs: list[str] = Field(default_factory=list, max_length=0)
    semantic_pass_from_metadata_allowed: Literal[False] = False
    visual_language_pass_from_metadata_allowed: Literal[False] = False


HumanVisualDimension = Literal[
    "METAPHOR_CLARITY",
    "GENERATED_ARTIFACT_ABSENCE",
    "VISUAL_LANGUAGE_FIT",
    "NATIVE_HEADLINE_READABILITY",
    "CROP_AND_MOTION_PRESERVATION",
    "AUTHORED_NOT_GENERIC",
    "PRODUCTION_PATTERN_ACCEPTABILITY",
]


class PendingHumanVisualChecklistItem(BaseModel):
    dimension: HumanVisualDimension
    decision: None = None
    notes: str = ""

    model_config = ConfigDict(extra="forbid")


class HumanVisualReviewEvidence(_HashBoundEvidence):
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_state: Literal["PENDING"] = "PENDING"
    reviewer: None = None
    final_decision: None = None
    checklist: list[PendingHumanVisualChecklistItem] = Field(min_length=7, max_length=7)
    human_final_approval_auto_passed: Literal[False] = False

    @model_validator(mode="after")
    def validate_pending_checklist(self) -> "HumanVisualReviewEvidence":
        dimensions = [item.dimension for item in self.checklist]
        if len(dimensions) != len(set(dimensions)) or set(dimensions) != set(
            HUMAN_VISUAL_REVIEW_DIMENSIONS
        ):
            raise ValueError("VQC1_HUMAN_VISUAL_CHECKLIST_INVALID")
        return self


class RightsDisclosureEvidence(_HashBoundEvidence):
    provider: Literal["google_gemini_image"] = "google_gemini_image"
    vendor: Literal["google"] = "google"
    model: Literal["gemini-3.1-flash-image"] = "gemini-3.1-flash-image"
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_asset_refs: list[str] = Field(default_factory=list)
    reference_asset_rights_refs: list[str] = Field(default_factory=list)
    generation_timestamp: datetime
    provider_request_id: str = Field(min_length=1)
    provider_operation_id: str | None = None
    output_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_width: int = Field(gt=0)
    output_height: int = Field(gt=0)
    cost_estimate_ref: str = Field(min_length=1)
    cost_estimate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    estimated_cost_usd: Decimal = Field(ge=0)
    actual_usage_ref: str | None = None
    actual_cost_usd: Decimal | None = Field(default=None, ge=0)
    approval_ref: str = Field(min_length=1)
    approval_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_ref: str = Field(min_length=1)
    attempt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_attempts_consumed: int = Field(ge=0, le=1)
    idempotency_key: str = Field(min_length=1)
    scene_usage_refs: list[str] = Field(min_length=1)
    native_overlay_binding_ref: str = Field(min_length=1)
    native_overlay_binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic_media_disclosure_ref: str = Field(min_length=1)
    generated_evidence_authority: Literal[False] = False
    provider_call_made: bool

    @model_validator(mode="after")
    def validate_rights_and_safe_refs(self) -> "RightsDisclosureEvidence":
        if self.generation_timestamp.tzinfo is None:
            raise ValueError("VQC1_GENERATION_TIMESTAMP_MUST_BE_AWARE")
        if len(self.reference_asset_refs) != len(self.reference_asset_rights_refs):
            raise ValueError("VQC1_REFERENCE_RIGHTS_BINDING_MISMATCH")
        refs = [
            *self.reference_asset_refs,
            *self.reference_asset_rights_refs,
            self.cost_estimate_ref,
            self.approval_ref,
            self.attempt_ref,
            *self.scene_usage_refs,
            self.native_overlay_binding_ref,
            self.synthetic_media_disclosure_ref,
        ]
        if self.actual_usage_ref:
            refs.append(self.actual_usage_ref)
        if any(
            value.startswith(("http://", "https://", "data:")) or "?" in value
            for value in refs
        ):
            raise ValueError("VQC1_RAW_OR_SIGNED_REFERENCE_FORBIDDEN")
        if self.provider_call_made and self.generation_attempts_consumed != 1:
            raise ValueError("VQC1_PROVIDER_CALL_ATTEMPT_BINDING_INVALID")
        if not self.provider_call_made and self.generation_attempts_consumed != 0:
            raise ValueError("VQC1_FIXTURE_ATTEMPT_BINDING_INVALID")
        return self


class VQC1ImageMaterializationEvidence(_HashBoundEvidence):
    """Typed, hash-bound projection of the provider materialization receipt.

    The receipt fields are kept verbatim so VQC can recompute the hash of the
    adapter receipt.  The remaining fields bind that receipt to the paid
    request and its redacted provider response without persisting raw bytes or
    provider URLs.
    """

    run_id: str = Field(min_length=1)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_id_ref: str = Field(min_length=1)
    provider_operation_id_ref: str | None = None
    estimated_cost_usd: Decimal = Field(ge=0, le=IMG_CANARY_HARD_CAP_USD)
    actual_cost_usd: Decimal | None = Field(
        default=None,
        ge=0,
        le=IMG_CANARY_HARD_CAP_USD,
    )
    materialization_receipt_ref: str = Field(min_length=1)
    materialization_receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    transport: Literal["GEMINI_API_NATIVE"] = "GEMINI_API_NATIVE"
    provider_call_made: Literal[True] = True
    local_path: str = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    image_format: Literal["PNG", "JPEG"]
    raw_url_persisted: Literal[False] = False
    part_path_remaining: Literal[False] = False
    already_materialized: bool

    @model_validator(mode="after")
    def validate_materialization_receipt(self) -> "VQC1ImageMaterializationEvidence":
        receipt_payload = {
            "transport": self.transport,
            "provider_call_made": self.provider_call_made,
            "local_path": self.local_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "image_format": self.image_format,
            "raw_url_persisted": self.raw_url_persisted,
            "part_path_remaining": self.part_path_remaining,
            "already_materialized": self.already_materialized,
        }
        if self.materialization_receipt_hash != ai_image_stable_hash(receipt_payload):
            raise ValueError("VQC1_MATERIALIZATION_RECEIPT_HASH_MISMATCH")
        if self.materialization_receipt_ref.startswith(
            ("http://", "https://", "data:")
        ) or "?" in self.materialization_receipt_ref:
            raise ValueError("VQC1_MATERIALIZATION_RAW_REFERENCE_FORBIDDEN")
        return self


class VQC1ImageNormalizationEvidence(_HashBoundEvidence):
    """Typed JPEG/PNG-to-review-PNG normalization and paid lineage evidence."""

    run_id: str = Field(min_length=1)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_id_ref: str = Field(min_length=1)
    provider_operation_id_ref: str | None = None
    estimated_cost_usd: Decimal = Field(ge=0, le=IMG_CANARY_HARD_CAP_USD)
    actual_cost_usd: Decimal | None = Field(
        default=None,
        ge=0,
        le=IMG_CANARY_HARD_CAP_USD,
    )
    materialization_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalization_receipt_ref: str = Field(min_length=1)
    normalization_receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_size_bytes: int = Field(gt=0)
    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    source_format: Literal["PNG", "JPEG"]
    crop_plan: dict[str, int | str]
    effective_width_after_crop: int = Field(ge=1920)
    effective_height_after_crop: int = Field(ge=1080)
    target_path: str = Field(min_length=1)
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_size_bytes: int = Field(gt=0)
    target_width: Literal[1920] = 1920
    target_height: Literal[1080] = 1080
    target_format: Literal["PNG"] = "PNG"
    upscale_applied: Literal[False] = False
    command: list[str] = Field(min_length=1)
    already_normalized: bool
    part_path_remaining: Literal[False] = False

    @model_validator(mode="after")
    def validate_normalization_receipt(self) -> "VQC1ImageNormalizationEvidence":
        required_crop_keys = {
            "x",
            "y",
            "width",
            "height",
            "target_aspect_ratio",
        }
        if set(self.crop_plan) != required_crop_keys:
            raise ValueError("VQC1_NORMALIZATION_CROP_PLAN_INVALID")
        crop_x = self.crop_plan["x"]
        crop_y = self.crop_plan["y"]
        crop_width = self.crop_plan["width"]
        crop_height = self.crop_plan["height"]
        if (
            not isinstance(crop_x, int)
            or isinstance(crop_x, bool)
            or not isinstance(crop_y, int)
            or isinstance(crop_y, bool)
            or not isinstance(crop_width, int)
            or isinstance(crop_width, bool)
            or not isinstance(crop_height, int)
            or isinstance(crop_height, bool)
            or crop_x < 0
            or crop_y < 0
            or crop_width <= 0
            or crop_height <= 0
            or crop_x + crop_width > self.source_width
            or crop_y + crop_height > self.source_height
            or self.crop_plan["target_aspect_ratio"] != "16:9"
            or crop_width != self.effective_width_after_crop
            or crop_height != self.effective_height_after_crop
        ):
            raise ValueError("VQC1_NORMALIZATION_CROP_PLAN_INVALID")
        receipt_payload = {
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "source_format": self.source_format,
            "crop_plan": self.crop_plan,
            "effective_width_after_crop": self.effective_width_after_crop,
            "effective_height_after_crop": self.effective_height_after_crop,
            "target_path": self.target_path,
            "target_sha256": self.target_sha256,
            "target_width": self.target_width,
            "target_height": self.target_height,
            "target_format": self.target_format,
            "upscale_applied": self.upscale_applied,
            "command": self.command,
            "already_normalized": self.already_normalized,
            "part_path_remaining": self.part_path_remaining,
        }
        if self.normalization_receipt_hash != ai_image_stable_hash(receipt_payload):
            raise ValueError("VQC1_NORMALIZATION_RECEIPT_HASH_MISMATCH")
        if self.normalization_receipt_ref.startswith(
            ("http://", "https://", "data:")
        ) or "?" in self.normalization_receipt_ref:
            raise ValueError("VQC1_NORMALIZATION_RAW_REFERENCE_FORBIDDEN")
        return self


class ImageVisualQualityControlInput(_HashBoundEvidence):
    run_id: str = Field(min_length=1)
    image_ref: str = Field(min_length=1)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_format: Literal["PNG"] = "PNG"
    target_aspect_ratio: Literal["16:9"] = "16:9"
    minimum_effective_width: Literal[1920] = 1920
    minimum_effective_height: Literal[1080] = 1080
    expected_alpha_behavior: Literal["NONE", "ALLOWED"] = "NONE"
    intended_crop: NormalizedImageRegion
    text_safe_regions: list[TextSafeRegion] = Field(min_length=1)
    reserved_overlay_regions: list[TextSafeRegion] = Field(default_factory=list)
    subject_focal_region: NormalizedImageRegion
    protected_visual_regions: list[NormalizedImageRegion] = Field(default_factory=list)
    artifact_inspection: GeneratedArtifactInspectionEvidence
    native_overlay: NativeOverlayInputs
    reuse_similarity: ReuseSimilarityEvidence
    structured_visual_review: StructuredVisualReviewEvidence
    rights_disclosure: RightsDisclosureEvidence
    provider_request: GeminiImageGenerationRequest | None = None
    scoped_approval: IMGCanaryScopedApproval | None = None
    attempt_ledger: IMGCanaryAttemptLedger | None = None
    cost_estimate: GeminiImageCostEstimateSnapshot | None = None
    provider_response: IMGCanaryProviderResponseSummary | None = None
    image_materialization: VQC1ImageMaterializationEvidence | None = None
    image_normalization: VQC1ImageNormalizationEvidence | None = None
    human_visual_review: HumanVisualReviewEvidence

    @model_validator(mode="after")
    def validate_roles_and_ids(self) -> "ImageVisualQualityControlInput":
        if self.intended_crop.region_role != "INTENDED_CROP":
            raise ValueError("VQC1_INTENDED_CROP_REGION_ROLE_INVALID")
        if self.subject_focal_region.region_role != "SUBJECT_FOCAL":
            raise ValueError("VQC1_SUBJECT_FOCAL_REGION_ROLE_INVALID")
        if any(item.region_role != "PROTECTED_VISUAL" for item in self.protected_visual_regions):
            raise ValueError("VQC1_PROTECTED_VISUAL_REGION_ROLE_INVALID")
        ids = [
            self.intended_crop.region_id,
            self.subject_focal_region.region_id,
            self.native_overlay.overlay_region.region_id,
            *[item.region_id for item in self.protected_visual_regions],
            *[item.id for item in self.text_safe_regions],
            *[item.id for item in self.reserved_overlay_regions],
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("VQC1_DUPLICATE_REGION_ID")
        real_bindings = (
            self.provider_request,
            self.scoped_approval,
            self.attempt_ledger,
            self.cost_estimate,
            self.provider_response,
            self.image_materialization,
            self.image_normalization,
        )
        if self.rights_disclosure.provider_call_made and any(
            item is None for item in real_bindings
        ):
            raise ValueError("VQC1_REAL_PROVIDER_TYPED_BINDINGS_REQUIRED")
        if not self.rights_disclosure.provider_call_made and any(
            item is not None for item in real_bindings
        ):
            raise ValueError("VQC1_FIXTURE_PROVIDER_BINDINGS_FORBIDDEN")
        return self


class TechnicalImageProbeEvidence(_HashBoundEvidence):
    image_ref: str = Field(min_length=1)
    local_path: str = Field(min_length=1)
    exists_nonempty: bool
    file_size_bytes: int = Field(ge=0)
    checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    safe_decode: bool
    image_format: Literal["PNG"] | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    aspect_ratio: float | None = Field(default=None, gt=0)
    bit_depth: int | None = Field(default=None, gt=0)
    png_color_type: int | None = Field(default=None, ge=0, le=6)
    color_mode: Literal["GRAYSCALE", "RGB", "INDEXED", "GRAYSCALE_ALPHA", "RGBA"] | None = None
    color_profile: Literal["SRGB", "ICC", "UNSPECIFIED"] | None = None
    alpha_behavior: Literal["NONE", "PRESENT"] | None = None
    corruption_detected: bool
    probe_method: Literal["VQC1_STDLIB_PNG_CRC_ZLIB"] = "VQC1_STDLIB_PNG_CRC_ZLIB"
    reason_codes: list[str] = Field(min_length=1)


class CropSafetyEvidence(_HashBoundEvidence):
    intended_crop: NormalizedImageRegion
    source_width: int | None = Field(default=None, gt=0)
    source_height: int | None = Field(default=None, gt=0)
    crop_x_px: int | None = Field(default=None, ge=0)
    crop_y_px: int | None = Field(default=None, ge=0)
    effective_width: int | None = Field(default=None, gt=0)
    effective_height: int | None = Field(default=None, gt=0)
    minimum_effective_width: int = Field(gt=0)
    minimum_effective_height: int = Field(gt=0)
    target_aspect_ratio_passed: bool
    resolution_passed: bool
    upscale_required: bool
    subject_focal_region_preserved: bool
    protected_visual_regions_preserved: bool
    safe_regions_preserved: bool
    reason_codes: list[str] = Field(min_length=1)


class CompositionComplianceEvidence(_HashBoundEvidence):
    intended_crop: NormalizedImageRegion
    text_safe_region_ids: list[str] = Field(min_length=1)
    reserved_overlay_region_ids: list[str] = Field(default_factory=list)
    subject_focal_region: NormalizedImageRegion
    protected_visual_regions: list[NormalizedImageRegion] = Field(default_factory=list)
    overlay_region: NormalizedImageRegion
    all_regions_normalized_and_bounded: bool
    overlay_inside_text_safe_region: bool
    overlay_collides_with_focal_region: bool
    overlay_collides_with_protected_region: bool
    reserved_overlay_collision_region_ids: list[str] = Field(default_factory=list)
    meaning_bearing_subject_hidden: bool
    reason_codes: list[str] = Field(min_length=1)


class NativeOverlayComplianceEvidence(_HashBoundEvidence):
    generated_image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_checksum_bound: bool
    native_overlay_plan_ref: str = Field(min_length=1)
    native_overlay_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_overlay_binding_ref: str = Field(min_length=1)
    native_overlay_binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authoritative_text_ref: str = Field(min_length=1)
    authoritative_text: str = Field(min_length=1)
    exact_text_native_authority: Literal[True] = True
    generated_image_owns_final_text: Literal[False] = False
    overlay_region: NormalizedImageRegion
    overlay_inside_text_safe_region: bool
    focal_or_protected_collision: bool
    contrast_ratio: float = Field(ge=1.0, le=21.0)
    minimum_contrast_ratio: float = Field(ge=1.0, le=21.0)
    contrast_passed: bool
    text_fits_without_shrinking: bool
    font_size_px: int = Field(gt=0)
    minimum_readable_font_size_px: int = Field(gt=0)
    readable_size_passed: bool
    reason_codes: list[str] = Field(min_length=1)


class ImageVisualGateEvidence(CreativeGateEvidence):
    gate_name: ImageVisualGateName
    authority: Literal["DETERMINISTIC", "CHECKSUM_BOUND_REVIEW", "HUMAN_FINAL"]
    repairability: Literal["NOT_REQUIRED", "DETERMINISTIC_NATIVE_REPAIR", "NOT_REPAIRABLE"]

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_typed_gate(self) -> "ImageVisualGateEvidence":
        if not self.reason_codes:
            raise ValueError("VQC1_GATE_REASON_CODE_REQUIRED")
        if not self.evidence_refs:
            raise ValueError("VQC1_GATE_EVIDENCE_REF_REQUIRED")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("VQC1_GATE_EVIDENCE_HASH_MISMATCH")
        return self


class ImageVisualQualityControlReport(BaseModel):
    schema_version: Literal["vqc1.image-visual-quality-control.v1"] = VQC1_SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    image_ref: str = Field(min_length=1)
    image_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    technical_probe: TechnicalImageProbeEvidence
    technical_media_qc: TechnicalMediaQCReport
    crop_safety_evidence: CropSafetyEvidence
    composition_compliance_evidence: CompositionComplianceEvidence
    native_overlay_compliance_evidence: NativeOverlayComplianceEvidence
    gate_results: list[ImageVisualGateEvidence] = Field(min_length=14, max_length=14)
    technical_status: Literal["PASS", "BLOCK"]
    creative_review_state: Literal["REVIEW_REQUIRED"] = "REVIEW_REQUIRED"
    human_review_state: Literal["PENDING"] = "PENDING"
    archive_eligible_for_review: bool
    verdict: ImageGateVerdict
    human_final_approval_auto_passed: Literal[False] = False
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_report(self) -> "ImageVisualQualityControlReport":
        names = [item.gate_name for item in self.gate_results]
        if len(names) != len(set(names)) or set(names) != set(VQC1_REQUIRED_GATES):
            raise ValueError("VQC1_GATE_SET_INVALID")
        verdicts = {item.result for item in self.gate_results}
        expected_verdict: ImageGateVerdict = (
            "BLOCK"
            if "BLOCK" in verdicts
            else "REVIEW_REQUIRED"
            if "REVIEW_REQUIRED" in verdicts
            else "PASS"
        )
        if self.verdict != expected_verdict:
            raise ValueError("VQC1_REPORT_VERDICT_MISMATCH")
        expected_technical = "PASS" if self.technical_media_qc.result == "PASS" else "BLOCK"
        if self.technical_status != expected_technical:
            raise ValueError("VQC1_TECHNICAL_STATUS_MISMATCH")
        required_archive_passes = {
            "CompositionComplianceGate",
            "TechnicalImageFitnessGate",
            "CropSafetyGate",
            "RightsDisclosureCompletenessGate",
            "NativeOverlayComplianceGate",
        }
        by_name = {item.gate_name: item for item in self.gate_results}
        expected_archive_eligible = (
            self.technical_media_qc.result == "PASS"
            and all(by_name[name].result == "PASS" for name in required_archive_passes)
            and "BLOCK" not in verdicts
        )
        if self.archive_eligible_for_review != expected_archive_eligible:
            raise ValueError("VQC1_ARCHIVE_REVIEW_ELIGIBILITY_MISMATCH")
        expected_hash = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected_hash:
            raise ValueError("VQC1_REPORT_HASH_MISMATCH")
        return self


__all__ = [
    "CompositionComplianceEvidence",
    "CropSafetyEvidence",
    "GeneratedArtifactInspectionEvidence",
    "GeneratedArtifactRegion",
    "HUMAN_VISUAL_REVIEW_DIMENSIONS",
    "HumanVisualReviewEvidence",
    "ImageVisualGateEvidence",
    "ImageVisualGateName",
    "ImageVisualQualityControlInput",
    "ImageVisualQualityControlReport",
    "NativeOverlayComplianceEvidence",
    "NativeOverlayInputs",
    "NormalizedImageRegion",
    "PendingHumanVisualChecklistItem",
    "ReuseSimilarityEvidence",
    "RightsDisclosureEvidence",
    "StructuredVisualReviewEvidence",
    "TechnicalImageProbeEvidence",
    "VQC1ImageMaterializationEvidence",
    "VQC1ImageNormalizationEvidence",
    "VQC1_REQUIRED_GATES",
    "VQC1_SCHEMA_VERSION",
]
