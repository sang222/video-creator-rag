from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.ai_image import (
    AIImageAspectRatio,
    AIImageReferenceAsset,
    AIImageSize,
    ai_image_stable_hash,
)
from app.contracts.native_renderer import TextSafeRegion
from app.core.config import (
    GEMINI_IMAGE_APPROVED_MODEL_IDS,
    GEMINI_IMAGE_SUPPORTED_ASPECT_RATIOS,
    GEMINI_IMAGE_SUPPORTED_SIZES,
)


GeminiImageOperationStatus = Literal[
    "PLANNED",
    "APPROVED",
    "SUBMITTED",
    "SUCCEEDED",
    "FAILED",
    "MODERATED",
    "OUTPUT_MISSING",
    "MATERIALIZATION_FAILED",
    "QC_FAILED",
]
MANDATORY_GEMINI_IMAGE_NEGATIVE_CONSTRAINTS = {
    "no letters",
    "no numbers",
    "no logos",
    "no watermark",
    "no interface text",
    "no fake software UI",
}


class GeminiImageGenerationRequest(BaseModel):
    generic_request_ref: str = Field(min_length=1)
    generic_request_hash: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    visual_source_decision_hash: str = Field(min_length=1)
    native_overlay_plan_hash: str | None = None

    model_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=1)
    image_size: AIImageSize
    aspect_ratio: AIImageAspectRatio
    output_count: int = Field(default=1, ge=1)
    four_k_approval_ref: str | None = None

    reference_images: list[AIImageReferenceAsset] = Field(default_factory=list)
    reference_types: list[str] = Field(default_factory=list)
    reference_asset_hashes: list[str] = Field(default_factory=list)
    negative_constraints: list[str] = Field(min_length=1)

    grounding_enabled: bool = False
    search_grounding_enabled: bool = False
    grounding_approval_ref: str | None = None

    text_safe_regions: list[TextSafeRegion] = Field(default_factory=list)
    native_overlay_required: bool
    scene_truth_classification: Literal["NO_EVIDENCE_TRUTH"]
    evidence_truth_requirement: float = Field(ge=0.0, le=1.0)
    product_specificity: float = Field(ge=0.0, le=1.0)
    exact_text_required: bool = False
    exact_number_required: bool = False

    provider_route: Literal["google_gemini_image"] = "google_gemini_image"
    cost_ref: str = Field(min_length=1)
    approval_ref: str = Field(min_length=1)
    approval_scope: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_provider_request(self) -> "GeminiImageGenerationRequest":
        if self.model_id not in GEMINI_IMAGE_APPROVED_MODEL_IDS:
            raise ValueError("GEMINI_IMAGE_MODEL_NOT_APPROVED")
        if self.image_size not in GEMINI_IMAGE_SUPPORTED_SIZES:
            raise ValueError("GEMINI_IMAGE_SIZE_NOT_SUPPORTED")
        if self.aspect_ratio not in GEMINI_IMAGE_SUPPORTED_ASPECT_RATIOS:
            raise ValueError("GEMINI_IMAGE_ASPECT_RATIO_NOT_SUPPORTED")
        if self.output_count != 1:
            raise ValueError("GEMINI_IMAGE_OUTPUT_COUNT_MUST_EQUAL_ONE")
        if self.image_size == "1K":
            raise ValueError("GEMINI_IMAGE_EFFECTIVE_RESOLUTION_BELOW_1080P")
        if self.image_size == "4K" and not self.four_k_approval_ref:
            raise ValueError("GEMINI_IMAGE_4K_REVIEW_APPROVAL_REQUIRED")
        if (self.grounding_enabled or self.search_grounding_enabled) and not self.grounding_approval_ref:
            raise ValueError("GEMINI_IMAGE_GROUNDING_APPROVAL_REQUIRED")
        if not MANDATORY_GEMINI_IMAGE_NEGATIVE_CONSTRAINTS.issubset(
            {item.strip() for item in self.negative_constraints}
        ):
            raise ValueError("GEMINI_IMAGE_MANDATORY_NEGATIVE_CONSTRAINT_MISSING")
        if self.evidence_truth_requirement >= 0.5 or self.product_specificity >= 0.5:
            raise ValueError("GEMINI_IMAGE_EVIDENCE_UI_PRODUCT_TRUTH_PROHIBITED")
        if (self.exact_text_required or self.exact_number_required) and (
            not self.native_overlay_required or not self.native_overlay_plan_hash
        ):
            raise ValueError("GEMINI_IMAGE_EXACT_CONTENT_NATIVE_OVERLAY_REQUIRED")
        if self.native_overlay_required:
            if not self.native_overlay_plan_hash or not self.text_safe_regions:
                raise ValueError("GEMINI_IMAGE_NATIVE_OVERLAY_BINDING_REQUIRED")
        elif self.native_overlay_plan_hash or self.text_safe_regions:
            raise ValueError("GEMINI_IMAGE_NATIVE_OVERLAY_BINDING_UNEXPECTED")
        expected_types = [item.reference_role for item in self.reference_images]
        expected_hashes = [item.asset_hash for item in self.reference_images]
        if self.reference_types != expected_types or self.reference_asset_hashes != expected_hashes:
            raise ValueError("GEMINI_IMAGE_REFERENCE_BINDING_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("GEMINI_IMAGE_REQUEST_HASH_MISMATCH")
        return self


class GeminiImageExecutionGates(BaseModel):
    provider_boundary_gate_passed: bool
    paid_call_authorization_gate_passed: bool
    provider_cost_estimate_gate_passed: bool
    channel_monthly_budget_gate_passed: bool
    paid_attempt_limit_gate_passed: bool
    provider_idempotency_key_valid: bool
    global_kill_switch_open: bool
    provider_kill_switch_open: bool
    approved_production_execution_scope: bool = False
    provider_boundary_gate_ref: str | None = None
    paid_call_authorization_gate_ref: str | None = None
    provider_cost_estimate_gate_ref: str | None = None
    channel_monthly_budget_gate_ref: str | None = None
    paid_attempt_limit_gate_ref: str | None = None
    provider_idempotency_key_ref: str | None = None
    global_kill_switch_ref: str | None = None
    provider_kill_switch_ref: str | None = None
    request_fingerprint: str = Field(min_length=1)
    evidence_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_gate_evidence(self) -> "GeminiImageExecutionGates":
        required_when_passed = (
            (self.provider_boundary_gate_passed, self.provider_boundary_gate_ref),
            (
                self.paid_call_authorization_gate_passed,
                self.paid_call_authorization_gate_ref,
            ),
            (self.provider_cost_estimate_gate_passed, self.provider_cost_estimate_gate_ref),
            (self.channel_monthly_budget_gate_passed, self.channel_monthly_budget_gate_ref),
            (self.paid_attempt_limit_gate_passed, self.paid_attempt_limit_gate_ref),
            (self.provider_idempotency_key_valid, self.provider_idempotency_key_ref),
            (self.global_kill_switch_open, self.global_kill_switch_ref),
            (self.provider_kill_switch_open, self.provider_kill_switch_ref),
        )
        if any(passed and not reference for passed, reference in required_when_passed):
            raise ValueError("GEMINI_IMAGE_GATE_EVIDENCE_REF_REQUIRED")
        expected_hash = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"evidence_hash"})
        )
        if self.evidence_hash != expected_hash:
            raise ValueError("GEMINI_IMAGE_GATE_EVIDENCE_HASH_MISMATCH")
        return self

    @property
    def all_passed(self) -> bool:
        return all(
            (
                self.provider_boundary_gate_passed,
                self.paid_call_authorization_gate_passed,
                self.provider_cost_estimate_gate_passed,
                self.channel_monthly_budget_gate_passed,
                self.paid_attempt_limit_gate_passed,
                self.provider_idempotency_key_valid,
                self.global_kill_switch_open,
                self.provider_kill_switch_open,
            )
        )

    @property
    def fixture_planning_passed(self) -> bool:
        """Non-network rehearsal checks; paid authorization and kill switches stay closed."""

        return all(
            (
                self.provider_boundary_gate_passed,
                self.provider_cost_estimate_gate_passed,
                self.channel_monthly_budget_gate_passed,
                self.paid_attempt_limit_gate_passed,
                self.provider_idempotency_key_valid,
            )
        )


class GeminiImageOperationReceipt(BaseModel):
    internal_job_id: str = Field(min_length=1)
    provider_request_id: str | None = None
    provider_operation_id: str | None = None
    request_ref: str = Field(min_length=1)
    request_hash: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    provider_status: str = Field(min_length=1)
    normalized_status: GeminiImageOperationStatus
    submitted_at: datetime | None = None
    completed_at: datetime | None = None
    output_reference: str | None = None
    provider_error_code: str | None = None
    provider_error_message_redacted: str | None = None
    provider_call_made: bool = False
    generation_attempts_consumed: int = Field(default=0, ge=0, le=1)
    actual_cost: Decimal | None = None
    fallback_provider_key: Literal[None] = None
    external_provider_fallback_used: Literal[False] = False
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    state_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_attempt_state(self) -> "GeminiImageOperationReceipt":
        if self.provider_call_made and self.generation_attempts_consumed != 1:
            raise ValueError("GEMINI_IMAGE_NETWORK_SUBMIT_MUST_CONSUME_ONE_ATTEMPT")
        if not self.provider_call_made and self.generation_attempts_consumed != 0:
            raise ValueError("GEMINI_IMAGE_NON_NETWORK_FLOW_MUST_CONSUME_ZERO_ATTEMPTS")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"state_hash"})
        )
        if self.state_hash != expected:
            raise ValueError("GEMINI_IMAGE_OPERATION_STATE_HASH_MISMATCH")
        return self


class GeminiImageOutputMaterializationPlan(BaseModel):
    request_ref: str = Field(min_length=1)
    output_reference: str = Field(min_length=1)
    workspace_root: str = Field(min_length=1)
    destination_path: str = Field(min_length=1)
    raw_url_persisted: Literal[False] = False
    execution_allowed: Literal[False] = False
    plan_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def durable_output_reference_is_redacted(self) -> "GeminiImageOutputMaterializationPlan":
        if self.output_reference.startswith(("http://", "https://", "data:")) or "?" in self.output_reference:
            raise ValueError("GEMINI_IMAGE_RAW_OUTPUT_REFERENCE_FORBIDDEN")
        if not self.output_reference.startswith("volatile://google-gemini-image/"):
            raise ValueError("GEMINI_IMAGE_VOLATILE_OUTPUT_REFERENCE_REQUIRED")
        root = Path(self.workspace_root).expanduser().resolve()
        destination = Path(self.destination_path).expanduser().resolve()
        try:
            destination.relative_to(root)
        except ValueError as exc:
            raise ValueError("GEMINI_IMAGE_OUTPUT_PATH_ESCAPES_WORKSPACE") from exc
        if destination == root:
            raise ValueError("GEMINI_IMAGE_OUTPUT_DESTINATION_MUST_BE_FILE")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"plan_hash"})
        )
        if self.plan_hash != expected:
            raise ValueError("GEMINI_IMAGE_OUTPUT_PLAN_HASH_MISMATCH")
        return self


class GeminiImageCostEstimateSnapshot(BaseModel):
    price_catalog_version: str = Field(min_length=1)
    price_catalog_ref: str = Field(min_length=1)
    provider_key: Literal["google_gemini_image"] = "google_gemini_image"
    model_id: str = Field(min_length=1)
    image_size: AIImageSize
    aspect_ratio: AIImageAspectRatio
    output_count: int = Field(ge=1, le=1)
    attempt_count: int = Field(ge=1, le=1)
    currency: Literal["USD"] = "USD"
    estimated_unit_cost: Decimal = Field(gt=0)
    estimated_amount: Decimal = Field(gt=0)
    hard_cap: Decimal = Field(gt=0)
    approval_amount: Decimal = Field(gt=0)
    actual_amount: Literal[None] = None
    effective_date: date
    source_note: str = Field(min_length=1)
    snapshot_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_cost_snapshot(self) -> "GeminiImageCostEstimateSnapshot":
        expected_amount = self.estimated_unit_cost * self.output_count * self.attempt_count
        if self.estimated_amount != expected_amount:
            raise ValueError("GEMINI_IMAGE_COST_ESTIMATE_MISMATCH")
        if self.estimated_amount > self.hard_cap or self.estimated_amount > self.approval_amount:
            raise ValueError("GEMINI_IMAGE_COST_CAP_EXCEEDED")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"snapshot_hash"})
        )
        if self.snapshot_hash != expected:
            raise ValueError("GEMINI_IMAGE_COST_SNAPSHOT_HASH_MISMATCH")
        return self


class GeminiImageReadiness(BaseModel):
    provider_key: Literal["google_gemini_image"] = "google_gemini_image"
    vendor: Literal["google"] = "google"
    capability: Literal["AI_IMAGE_GENERATION"] = "AI_IMAGE_GENERATION"
    transport: Literal["GEMINI_API_NATIVE"] = "GEMINI_API_NATIVE"
    provider_route_registered: bool
    credential_configured: bool
    credential_value_redacted: Literal[True] = True
    model_configured: bool
    model_catalog_present: bool
    route_approval_state: bool
    execution_enabled: bool
    fixture_only: bool
    cost_catalog_state: Literal["PRESENT", "MISSING"]
    global_kill_switch_open: bool
    provider_kill_switch_open: bool
    will_execute: Literal[False] = False
    provider_call_made: Literal[False] = False
    exact_next_action: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


__all__ = [
    "GeminiImageCostEstimateSnapshot",
    "GeminiImageExecutionGates",
    "GeminiImageGenerationRequest",
    "GeminiImageOperationReceipt",
    "GeminiImageOutputMaterializationPlan",
    "GeminiImageReadiness",
]
