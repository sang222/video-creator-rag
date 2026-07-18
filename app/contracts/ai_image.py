from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.native_renderer import TextSafeRegion
from app.contracts.visual_routing import VisualSourceRoute


AIImageSize = Literal["1K", "2K", "4K"]
AIImageAspectRatio = Literal["16:9", "9:16", "1:1"]
ImageGateVerdict = Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        normalized = value.astimezone(UTC) if value.tzinfo is not None else value
        encoded = normalized.isoformat()
        return encoded.replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"AI_IMAGE_NON_CANONICAL_HASH_VALUE:{type(value).__name__}")


def ai_image_stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AIImageReferenceAsset(BaseModel):
    asset_ref: str = Field(min_length=1)
    asset_hash: str = Field(min_length=1)
    reference_role: Literal["SUBJECT", "COMPOSITION", "EDIT_SOURCE", "STYLE"]
    source: str = Field(min_length=1)
    rights_state: Literal["AUTHORIZED", "OWNED", "LICENSED"]
    checksum: str = Field(min_length=1)
    authorization_ref: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def enforce_reference_rights(self) -> "AIImageReferenceAsset":
        if self.reference_role == "STYLE":
            raise ValueError("AI_IMAGE_STYLE_REFERENCE_UPLOADS_DISABLED")
        if self.asset_hash != self.checksum:
            raise ValueError("AI_IMAGE_REFERENCE_HASH_CHECKSUM_MISMATCH")
        return self


class AIImageRequest(BaseModel):
    """Provider-neutral still-image generation request bound to one VSR1 decision."""

    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    source_segment_ids: list[str] = Field(min_length=1)

    visual_source_decision_ref: str = Field(min_length=1)
    visual_source_decision_hash: str = Field(min_length=1)
    visual_source_route: VisualSourceRoute
    visual_direction_contract_ref: str = Field(min_length=1)
    visual_direction_contract_hash: str = Field(min_length=1)

    scene_meaning: str = Field(min_length=1)
    narrative_function: str = Field(min_length=1)
    prompt_intent: str = Field(min_length=1)
    custom_composition_reason: str = Field(min_length=1)

    aspect_ratio: AIImageAspectRatio
    requested_image_size: AIImageSize
    minimum_effective_resolution: Literal["1080p"] = "1080p"
    four_k_approval_ref: str | None = None

    reference_assets: list[AIImageReferenceAsset] = Field(default_factory=list)
    reference_asset_refs: list[str] = Field(default_factory=list)
    reference_asset_hashes: list[str] = Field(default_factory=list)

    text_safe_regions: list[TextSafeRegion] = Field(default_factory=list)
    reserved_overlay_regions: list[TextSafeRegion] = Field(default_factory=list)
    exact_text_required: bool = False
    exact_number_required: bool = False
    native_overlay_required: bool
    native_overlay_plan_ref: str | None = None
    native_overlay_plan_hash: str | None = None

    forbidden_generated_text: Literal[True] = True
    forbidden_generated_numbers: Literal[True] = True
    forbidden_generated_logo: Literal[True] = True
    forbidden_generated_fake_ui: Literal[True] = True

    scene_truth_classification: Literal[
        "NO_EVIDENCE_TRUTH",
        "EVIDENCE",
        "ACTUAL_UI",
        "ACTUAL_PRODUCT",
        "ACTUAL_DOCUMENT",
    ]
    evidence_truth_requirement: float = Field(ge=0.0, le=1.0)
    product_specificity: float = Field(ge=0.0, le=1.0)
    identity_likeness_policy: Literal[
        "NO_IDENTITY_OR_LIKENESS_AUTHORITY",
        "AUTHORIZED_REFERENCE_ONLY",
    ]

    provider_route: str = Field(min_length=1)
    provider_route_approved: bool
    cost_catalog_ref: str = Field(min_length=1)
    cost_estimate_ref: str = Field(min_length=1)
    approval_ref: str = Field(min_length=1)
    approval_scope: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)

    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    request_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_image_request(self) -> "AIImageRequest":
        allowed_routes = {
            VisualSourceRoute.AI_GENERATED_IMAGE,
            VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
        }
        if self.visual_source_route not in allowed_routes:
            raise ValueError("AI_IMAGE_VISUAL_SOURCE_DECISION_ROUTE_INVALID")
        if not self.provider_route_approved:
            raise ValueError("AI_IMAGE_PROVIDER_ROUTE_APPROVAL_REQUIRED")
        if self.requested_image_size == "1K":
            raise ValueError("AI_IMAGE_EFFECTIVE_RESOLUTION_BELOW_1080P")
        if self.requested_image_size == "4K" and not self.four_k_approval_ref:
            raise ValueError("AI_IMAGE_4K_REVIEW_APPROVAL_REQUIRED")
        if self.scene_truth_classification != "NO_EVIDENCE_TRUTH":
            raise ValueError("AI_IMAGE_EVIDENCE_UI_PRODUCT_DOCUMENT_SCENE_PROHIBITED")
        if self.evidence_truth_requirement >= 0.5:
            raise ValueError("AI_IMAGE_EVIDENCE_TRUTH_PROHIBITED")
        if self.product_specificity >= 0.5:
            raise ValueError("AI_IMAGE_ACTUAL_PRODUCT_SPECIFICITY_PROHIBITED")
        if (
            self.identity_likeness_policy == "AUTHORIZED_REFERENCE_ONLY"
            and not self.reference_assets
        ):
            raise ValueError("AI_IMAGE_AUTHORIZED_IDENTITY_REFERENCE_REQUIRED")
        if (self.exact_text_required or self.exact_number_required) and not self.native_overlay_required:
            raise ValueError("AI_IMAGE_EXACT_CONTENT_REQUIRES_NATIVE_OVERLAY")
        if self.visual_source_route == VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY:
            if not self.native_overlay_required:
                raise ValueError("AI_IMAGE_OVERLAY_ROUTE_REQUIRES_NATIVE_OVERLAY")
        if self.visual_source_route == VisualSourceRoute.AI_GENERATED_IMAGE and self.native_overlay_required:
            raise ValueError("AI_IMAGE_NATIVE_OVERLAY_REQUIRES_OVERLAY_ROUTE")
        if self.native_overlay_required:
            if not self.native_overlay_plan_ref or not self.native_overlay_plan_hash:
                raise ValueError("AI_IMAGE_NATIVE_OVERLAY_PLAN_BINDING_REQUIRED")
            if not self.text_safe_regions:
                raise ValueError("AI_IMAGE_TEXT_SAFE_REGION_REQUIRED")
        if len(self.source_segment_ids) != len(set(self.source_segment_ids)):
            raise ValueError("AI_IMAGE_DUPLICATE_SOURCE_SEGMENT")
        expected_refs = [asset.asset_ref for asset in self.reference_assets]
        expected_hashes = [asset.asset_hash for asset in self.reference_assets]
        if self.reference_asset_refs != expected_refs or self.reference_asset_hashes != expected_hashes:
            raise ValueError("AI_IMAGE_REFERENCE_BINDING_MISMATCH")
        if len(self.reference_asset_refs) != len(set(self.reference_asset_refs)):
            raise ValueError("AI_IMAGE_DUPLICATE_REFERENCE_ASSET")
        region_ids = [
            region.id
            for region in [*self.text_safe_regions, *self.reserved_overlay_regions]
        ]
        if len(region_ids) != len(set(region_ids)):
            raise ValueError("AI_IMAGE_DUPLICATE_OVERLAY_REGION")
        expected_hash = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"request_hash"})
        )
        if self.request_hash != expected_hash:
            raise ValueError("AI_IMAGE_REQUEST_HASH_MISMATCH")
        return self


class CompiledImagePrompt(BaseModel):
    compiler_version: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    visual_source_decision_ref: str = Field(min_length=1)
    visual_source_decision_hash: str = Field(min_length=1)
    generic_request_ref: str = Field(min_length=1)
    generic_request_hash: str = Field(min_length=1)
    visual_direction_contract_ref: str = Field(min_length=1)
    visual_direction_contract_hash: str = Field(min_length=1)

    subject_and_visual_concept: str = Field(min_length=1)
    environment_context: str = Field(min_length=1)
    editorial_composition: str = Field(min_length=1)
    realism_treatment: str = Field(min_length=1)
    lighting: str = Field(min_length=1)
    palette: str = Field(min_length=1)
    camera_framing: str = Field(min_length=1)
    depth_focal_behavior: str = Field(min_length=1)
    negative_space_requirement: str = Field(min_length=1)
    continuity_hints: list[str] = Field(default_factory=list)
    negative_constraints: list[str] = Field(min_length=1)
    prompt: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=1)
    provider_call_made: Literal[False] = False
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_compiled_prompt(self) -> "CompiledImagePrompt":
        mandatory = {
            "no letters",
            "no numbers",
            "no logos",
            "no watermark",
            "no interface text",
            "no fake software UI",
        }
        normalized_constraints = {item.strip() for item in self.negative_constraints}
        if not mandatory.issubset(normalized_constraints):
            raise ValueError("AI_IMAGE_PROMPT_MANDATORY_NEGATIVE_CONSTRAINT_MISSING")
        if self.prompt_hash != ai_image_stable_hash(self.prompt):
            raise ValueError("AI_IMAGE_PROMPT_HASH_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("AI_IMAGE_COMPILED_PROMPT_HASH_MISMATCH")
        return self


class ImageNormalizationManifest(BaseModel):
    source_ref: str = Field(min_length=1)
    source_checksum: str = Field(min_length=1)
    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    target_width: int = Field(gt=0)
    target_height: int = Field(gt=0)
    target_aspect_ratio: AIImageAspectRatio
    crop_plan: dict[str, Any] = Field(min_length=1)
    effective_width_after_crop: int = Field(gt=0)
    effective_height_after_crop: int = Field(gt=0)
    color_profile: str = Field(min_length=1)
    source_format: Literal["PNG", "JPEG", "WEBP"]
    target_format: Literal["PNG", "JPEG", "WEBP"]
    format_conversion: str = Field(min_length=1)
    sharpness_upscale_warning: str | None = None
    upscale_applied: Literal[False] = False
    minimum_effective_resolution: Literal["1080p"] = "1080p"
    checksum: str = Field(min_length=1)
    execution_allowed: Literal[False] = False
    manifest_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_effective_resolution(self) -> "ImageNormalizationManifest":
        minimums = {
            "16:9": (1920, 1080),
            "9:16": (1080, 1920),
            "1:1": (1080, 1080),
        }
        min_width, min_height = minimums[self.target_aspect_ratio]
        if self.effective_width_after_crop < min_width or self.effective_height_after_crop < min_height:
            raise ValueError("AI_IMAGE_EFFECTIVE_RESOLUTION_BELOW_1080P")
        if self.target_width > self.effective_width_after_crop or self.target_height > self.effective_height_after_crop:
            raise ValueError("AI_IMAGE_SILENT_UPSCALE_FORBIDDEN")
        if self.checksum != self.source_checksum:
            raise ValueError("AI_IMAGE_NORMALIZATION_CHECKSUM_BINDING_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"manifest_hash"})
        )
        if self.manifest_hash != expected:
            raise ValueError("AI_IMAGE_NORMALIZATION_MANIFEST_HASH_MISMATCH")
        return self


class GeneratedImageQCEvidence(BaseModel):
    image_ref: str = Field(min_length=1)
    image_hash: str = Field(min_length=1)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    generated_letters_detected: bool = False
    generated_numbers_detected: bool = False
    logo_or_trademark_detected: bool = False
    fake_ui_detected: bool = False
    watermark_detected: bool = False
    artifact_repairable_by_native_overlay: bool = False
    detected_region_boxes: list[dict[str, float]] = Field(default_factory=list)
    representative_crop_refs: list[str] = Field(default_factory=list)
    composition_compliance_score: float = Field(ge=0.0, le=1.0)
    semantic_match_score: float = Field(ge=0.0, le=1.0)
    visual_language_match_score: float = Field(ge=0.0, le=1.0)
    technical_image_fitness_score: float = Field(ge=0.0, le=1.0)
    crop_safety_score: float = Field(ge=0.0, le=1.0)
    reuse_similarity_score: float = Field(ge=0.0, le=1.0)
    rights_disclosure_complete: bool

    model_config = ConfigDict(extra="forbid")


class ImageQCGateResult(BaseModel):
    gate: str = Field(min_length=1)
    verdict: ImageGateVerdict
    reason_codes: list[str] = Field(default_factory=list)
    representative_crop_refs: list[str] = Field(default_factory=list)
    detected_region_boxes: list[dict[str, float]] = Field(default_factory=list)
    repairability: Literal["NOT_REQUIRED", "NATIVE_OVERLAY_REPAIR", "NOT_REPAIRABLE"]

    model_config = ConfigDict(extra="forbid")


class PostGenerationImageQCManifest(BaseModel):
    image_ref: str = Field(min_length=1)
    image_hash: str = Field(min_length=1)
    gate_results: list[ImageQCGateResult] = Field(min_length=9)
    verdict: ImageGateVerdict
    reason_codes: list[str] = Field(default_factory=list)
    fixture_only: Literal[True] = True
    production_eligible: Literal[False] = False
    manifest_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_gate_set(self) -> "PostGenerationImageQCManifest":
        required = {
            "GeneratedTextArtifactGate",
            "FakeUILogoGate",
            "CompositionComplianceGate",
            "SemanticMatchGate",
            "VisualLanguageMatchGate",
            "TechnicalImageFitnessGate",
            "CropSafetyGate",
            "ReuseSimilarityGate",
            "RightsDisclosureCompletenessGate",
        }
        names = [item.gate for item in self.gate_results]
        if len(names) != len(set(names)) or set(names) != required:
            raise ValueError("AI_IMAGE_POST_QC_GATE_SET_INVALID")
        verdicts = {item.verdict for item in self.gate_results}
        expected_verdict: ImageGateVerdict
        if "BLOCK" in verdicts:
            expected_verdict = "BLOCK"
        elif "REVIEW_REQUIRED" in verdicts:
            expected_verdict = "REVIEW_REQUIRED"
        else:
            expected_verdict = "PASS"
        if self.verdict != expected_verdict:
            raise ValueError("AI_IMAGE_POST_QC_VERDICT_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"manifest_hash"})
        )
        if self.manifest_hash != expected:
            raise ValueError("AI_IMAGE_POST_QC_MANIFEST_HASH_MISMATCH")
        return self


class NativeOverlayImageBinding(BaseModel):
    visual_source_decision_ref: str = Field(min_length=1)
    visual_source_decision_hash: str = Field(min_length=1)
    generated_image_ref: str = Field(min_length=1)
    generated_image_hash: str = Field(min_length=1)
    text_safe_regions: list[TextSafeRegion] = Field(min_length=1)
    reserved_overlay_regions: list[TextSafeRegion] = Field(default_factory=list)
    native_overlay_plan_ref: str = Field(min_length=1)
    native_overlay_plan_hash: str = Field(min_length=1)
    authoritative_content_kinds: list[
        Literal["HEADLINE", "NUMBER", "LABEL", "CITATION", "CTA", "TOOL_NAME", "PRODUCT_NAME", "WORKFLOW_NODE"]
    ] = Field(min_length=1)
    image_model_owns_final_text: Literal[False] = False
    production_eligible: Literal[False] = False
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_binding(self) -> "NativeOverlayImageBinding":
        if len(self.authoritative_content_kinds) != len(set(self.authoritative_content_kinds)):
            raise ValueError("AI_IMAGE_DUPLICATE_AUTHORITATIVE_CONTENT_KIND")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("AI_IMAGE_OVERLAY_BINDING_HASH_MISMATCH")
        return self


class AIImageProvenanceManifest(BaseModel):
    provider: str = Field(min_length=1)
    provider_model_id: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=1)
    reference_asset_refs: list[str] = Field(default_factory=list)
    reference_asset_hashes: list[str] = Field(default_factory=list)
    generated_at: datetime
    output_reference: str = Field(min_length=1)
    output_checksum: str = Field(min_length=1)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    image_format: str = Field(min_length=1)
    cost_snapshot_ref: str = Field(min_length=1)
    approval_ref: str = Field(min_length=1)
    scene_usage_refs: list[str] = Field(min_length=1)
    visual_source_decision_ref: str = Field(min_length=1)
    visual_source_decision_hash: str = Field(min_length=1)
    native_overlay_required: bool
    post_generation_qc_refs: list[str] = Field(min_length=1)
    synthetic_media_disclosure_ref: str = Field(min_length=1)
    generated_evidence_authority: Literal[False] = False
    provider_call_made: Literal[False] = False
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    manifest_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_provenance(self) -> "AIImageProvenanceManifest":
        if self.output_reference.startswith(("http://", "https://", "data:")) or "?" in self.output_reference:
            raise ValueError("AI_IMAGE_RAW_OUTPUT_REFERENCE_FORBIDDEN")
        if self.reference_asset_refs and len(self.reference_asset_refs) != len(self.reference_asset_hashes):
            raise ValueError("AI_IMAGE_PROVENANCE_REFERENCE_BINDING_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"manifest_hash"})
        )
        if self.manifest_hash != expected:
            raise ValueError("AI_IMAGE_PROVENANCE_MANIFEST_HASH_MISMATCH")
        return self


__all__ = [
    "AIImageAspectRatio",
    "AIImageProvenanceManifest",
    "AIImageReferenceAsset",
    "AIImageRequest",
    "AIImageSize",
    "ai_image_stable_hash",
    "CompiledImagePrompt",
    "GeneratedImageQCEvidence",
    "ImageNormalizationManifest",
    "ImageQCGateResult",
    "NativeOverlayImageBinding",
    "PostGenerationImageQCManifest",
]
