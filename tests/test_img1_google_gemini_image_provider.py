from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import app.providers.google_gemini_image as gemini_image_provider_module
from app.contracts.ai_image import (
    AIImageProvenanceManifest,
    AIImageReferenceAsset,
    AIImageRequest,
    CompiledImagePrompt,
    GeneratedImageQCEvidence,
    ai_image_stable_hash,
)
from app.contracts.asset_acquisition import AIGenerationManifest
from app.contracts.google_gemini_image import (
    GeminiImageCostEstimateSnapshot,
    GeminiImageExecutionGates,
    GeminiImageGenerationRequest,
    GeminiImageOperationReceipt,
    validate_gemini_image_cost_snapshot_integrity,
)
from app.contracts.native_renderer import NativeOverlayPlan, TextSafeRegion
from app.contracts.visual_direction import VisualDirectionContract
from app.contracts.visual_routing import (
    AuthoritativeOverlayContentKind,
    ExactTextNativeOverlayContract,
    NicheVisualSourceProfile,
    SceneVisualRealizationRequirements,
    VisualSourceDecision,
    VisualSourceRoute,
)
from app.core.config import (
    GEMINI_IMAGE_DEFAULT_ASPECT_RATIO,
    GEMINI_IMAGE_DEFAULT_MODEL_ID,
    GEMINI_IMAGE_DEFAULT_SIZE,
    GEMINI_IMAGE_MAX_ATTEMPTS_PER_SCENE,
    GEMINI_IMAGE_MAX_OUTPUTS,
    GEMINI_IMAGE_MINIMUM_EFFECTIVE_RESOLUTION,
    Settings,
)
from app.providers.google_gemini_image import (
    GoogleGeminiImageAdapter,
    build_fixture_png,
)
from app.services.ai_image import (
    AIImageRequestBuilder,
    ImageNormalizationPlanner,
    ImagePromptCompiler,
    MANDATORY_IMAGE_NEGATIVE_CONSTRAINTS,
    NativeOverlayImageBindingBuilder,
    PostGenerationImageQC,
)
from app.services.google_gemini_image_catalog import GoogleGeminiImageModelPriceCatalog
from app.services.provider_stack import CANONICAL_PROVIDER_KEYS, normalize_provider_key
from app.services.r3d8 import (
    GOOGLE_GEMINI_IMAGE_PROVIDER_STAGES,
    MAX_PAID_ATTEMPTS_BY_PROVIDER,
    PAID_PROVIDER_KEYS,
    derive_google_gemini_image_catalog_cost,
)
from app.services.visual_source_routing import (
    VisualSourceRouter,
    stable_hash as visual_hash,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTE_PATH = REPO_ROOT / "app" / "api" / "routes" / "google_gemini_image.py"
PROVIDER_PATH = REPO_ROOT / "app" / "providers" / "google_gemini_image.py"
REHEARSAL_PATH = REPO_ROOT / "app" / "services" / "google_gemini_image_rehearsal.py"


def _requirements(**overrides: Any) -> SceneVisualRealizationRequirements:
    payload: dict[str, Any] = {
        "scene_id": "scene-knowledge-silos",
        "semantic_intent": "Show labeled knowledge silos as an editorial metaphor.",
        "target_duration_seconds": 6.0,
        "aspect_ratio": "16:9",
        "crop_safety_required": True,
        "previous_scene_summary": "A small team gathers fragmented notes.",
        "next_scene_summary": "The shared system reconnects the team.",
        "subject_action": "separating knowledge into isolated silos",
        "camera_angle": "eye-level",
        "shot_size": "wide",
        "segment_ids": ["segment-knowledge-silos"],
        "niche_visual_source_profile": NicheVisualSourceProfile.GENERATED_EDITORIAL_FIRST,
        "scene_class": "metaphor",
        "narrative_function": "conceptual_metaphor",
        "scene_meaning": "A custom composition plus native labels explains isolation.",
        "editorial_intent": "Make organizational fragmentation immediately legible.",
        "filmability_score": 0.20,
        "stock_searchability_score": 0.20,
        "required_specificity": 0.40,
        "custom_composition_score": 0.90,
        "exact_text_dependency": 0.30,
        "exact_number_dependency": 0.0,
        "named_workflow_nodes_required": False,
        "diagram_clarity_advantage": 0.10,
        "brand_or_product_dependency": 0.0,
        "product_specificity": 0.0,
        "evidence_truth_requirement": 0.0,
        "authorized_asset_available": False,
        "identity_consistency_requirement": 0.0,
        "recurring_identity_required": False,
        "human_action_requirement": 0.0,
        "motion_semantic_value": 0.0,
        "target_aspect_ratio": "16:9",
        "minimum_resolution": "1080p",
        "crop_safety_requirement": "Protect the native headline safe region.",
        "previous_scene_intent_ref": None,
        "next_scene_intent_ref": None,
    }
    payload.update(overrides)
    return SceneVisualRealizationRequirements(
        **payload,
        content_hash=visual_hash(payload),
    )


def _visual_direction() -> VisualDirectionContract:
    payload = {
        "contract_version": "img1.visual-direction.v1",
        "channel_id": "small-team-ai",
        "project_id": "project-img1",
        "format_identity_ref": "format://small-team-ai/editorial",
        "format_identity_hash": "format-hash",
        "visual_strategy_profile_ref": "strategy://small-team-ai/generated-editorial-first",
        "visual_strategy_profile_hash": "strategy-hash",
        "realism_level": "editorial photorealism",
        "treatment_mode": "restrained cinematic metaphor",
        "human_presence_policy": "NO_IDENTIFIABLE_PERSON",
        "environment_type": "abstract workplace architecture",
        "industry_context": "small-team knowledge operations",
        "time_of_day": "neutral studio light",
        "lighting_direction": "soft side light",
        "lighting_temperature": "neutral-warm",
        "palette": ["navy", "teal", "warm white"],
        "contrast": "medium-high",
        "saturation": "restrained",
        "camera_distance": "wide",
        "lens_feel": "natural perspective",
        "camera_movement": "none",
        "motion_intensity": "none",
        "framing_rule": "balanced negative space on upper-left",
        "depth_of_field_style": "deep enough for clear silhouette separation",
        "texture_grain": "subtle editorial grain",
        "tone_mode": "calm explanatory",
        "prohibited_cliches": ["glowing robot", "floating fake dashboard"],
        "channel_identity_markers": ["clean editorial geometry"],
        "adjacent_scene_constraints": ["preserve navy and teal continuity"],
    }
    return VisualDirectionContract(
        **payload,
        content_hash=ai_image_stable_hash(payload),
    )


def _overlay_plan(
    decision: VisualSourceDecision,
    *,
    decision_ref: str,
) -> NativeOverlayPlan:
    authoritative_refs = ["copy://scene-knowledge-silos/headline"]
    exact_payload = {
        "scene_id": decision.scene_id,
        "source_decision_ref": decision_ref,
        "source_decision_hash": decision.content_hash,
        "preferred_source_route": decision.preferred_source_route,
        "exact_text_required": True,
        "exact_number_required": False,
        "forbidden_generated_text": True,
        "forbidden_generated_logo": True,
        "forbidden_generated_fake_ui": True,
        "native_overlay_required": True,
        "authoritative_content_kinds": [AuthoritativeOverlayContentKind.HEADLINE],
        "authoritative_content_refs": authoritative_refs,
    }
    exact = ExactTextNativeOverlayContract(
        **exact_payload,
        content_hash=ai_image_stable_hash(exact_payload),
    )
    safe_region = TextSafeRegion(
        id="headline-safe",
        x=0.08,
        y=0.08,
        width=0.50,
        height=0.22,
        purpose="Authoritative knowledge-silos headline",
        minimum_contrast_requirement=4.5,
        alignment="left",
    )
    payload = {
        "plan_id": "native-overlay://scene-knowledge-silos",
        "scene_id": decision.scene_id,
        "source_decision_ref": decision_ref,
        "source_decision_hash": decision.content_hash,
        "preferred_source_route": decision.preferred_source_route,
        "exact_text_contract": exact,
        "text_safe_regions": [safe_region],
        "reserved_overlay_regions": [],
        "overlay_content_refs": authoritative_refs,
        "native_overlay_required": True,
    }
    return NativeOverlayPlan(
        **payload,
        content_hash=ai_image_stable_hash(payload),
    )


def _build_request(
    requirements: SceneVisualRealizationRequirements,
    decision: VisualSourceDecision,
    direction: VisualDirectionContract,
    overlay: NativeOverlayPlan,
    *,
    reference_assets: tuple[AIImageReferenceAsset, ...] = (),
) -> AIImageRequest:
    return AIImageRequestBuilder().build(
        requirements=requirements,
        decision=decision,
        decision_ref="visual-source-decision://scene-knowledge-silos",
        visual_direction=direction,
        visual_direction_ref="visual-direction://project-img1",
        package_id="package-img1",
        request_id="ai-image-request-img1",
        prompt_intent="Editorial metaphor of separated architectural knowledge silos.",
        custom_composition_reason="Stock cannot express the authored metaphor and label-safe layout.",
        requested_image_size="2K",
        cost_catalog_ref="config://google_gemini_image_model_price_catalog/2026-07-17",
        cost_estimate_ref="cost-estimate://img1/knowledge-silos",
        approval_ref="approval://img1/fixture-only",
        approval_scope="IMG1_ONE_FIXTURE_IMAGE",
        idempotency_key="img1-knowledge-silos-v1",
        native_overlay_plan=overlay,
        reference_assets=reference_assets,
    )


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "gemini_api_key": None,
        "gemini_image_model_id": GEMINI_IMAGE_DEFAULT_MODEL_ID,
        "gemini_image_default_size": GEMINI_IMAGE_DEFAULT_SIZE,
        "gemini_image_default_aspect_ratio": GEMINI_IMAGE_DEFAULT_ASPECT_RATIO,
        "gemini_image_max_outputs": 1,
        "gemini_image_max_attempts_per_scene": 1,
        "gemini_image_provider_route_approved": True,
        "gemini_image_real_generation_enabled": False,
        "img1_fixture_only": True,
        "provider_real_execution_enabled": False,
        "provider_production_execution_enabled": False,
        "media_provider_calls_disabled": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _gates(
    request: GeminiImageGenerationRequest,
    **overrides: bool,
) -> GeminiImageExecutionGates:
    values: dict[str, Any] = {
        "provider_boundary_gate_passed": True,
        "paid_call_authorization_gate_passed": False,
        "provider_cost_estimate_gate_passed": True,
        "channel_monthly_budget_gate_passed": True,
        "paid_attempt_limit_gate_passed": True,
        "provider_idempotency_key_valid": True,
        "global_kill_switch_open": False,
        "provider_kill_switch_open": False,
        "approved_production_execution_scope": False,
        "provider_boundary_gate_ref": "gate://provider-boundary/google-gemini-image",
        "paid_call_authorization_gate_ref": request.approval_ref,
        "provider_cost_estimate_gate_ref": request.cost_ref,
        "channel_monthly_budget_gate_ref": "budget://channel/small-team-ai/2026-07",
        "paid_attempt_limit_gate_ref": "attempt-limit://google-gemini-image/one",
        "provider_idempotency_key_ref": request.idempotency_key,
        "global_kill_switch_ref": "kill-switch://providers/global",
        "provider_kill_switch_ref": "kill-switch://google-gemini-image",
        "request_fingerprint": GoogleGeminiImageAdapter.idempotency_fingerprint(
            request
        ),
    }
    values.update(overrides)
    return GeminiImageExecutionGates(
        **values,
        evidence_hash=ai_image_stable_hash(values),
    )


class _FixtureClient:
    def __init__(
        self, *, image_bytes: bytes | None = None, failure: Exception | None = None
    ):
        self.image_bytes = image_bytes or build_fixture_png()
        self.failure = failure
        self.submit_count = 0

    def submit(self, request: GeminiImageGenerationRequest) -> dict[str, Any]:
        self.submit_count += 1
        if self.failure is not None:
            raise self.failure
        return {
            "request_id": "fixture-request-img1",
            "operation_id": "fixture-operation-img1",
            "status": "FIXTURE_SUCCEEDED",
            "image_bytes": self.image_bytes,
            "raw_temporary_url": "https://example.invalid/image.png?token=fixture-secret",
        }


@pytest.fixture
def img1_context() -> dict[str, Any]:
    requirements = _requirements()
    decision = VisualSourceRouter().route(
        requirements,
        rights_policy_allows_generation=True,
    )
    assert (
        decision.preferred_source_route
        == VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY
    )
    direction = _visual_direction()
    overlay = _overlay_plan(
        decision,
        decision_ref="visual-source-decision://scene-knowledge-silos",
    )
    request = _build_request(requirements, decision, direction, overlay)
    prompt = ImagePromptCompiler().compile(
        requirements=requirements,
        visual_direction=direction,
        decision=decision,
        request=request,
    )
    adapter = GoogleGeminiImageAdapter(_settings())
    provider_request = adapter.build_request(request, prompt)
    return {
        "requirements": requirements,
        "decision": decision,
        "direction": direction,
        "overlay": overlay,
        "request": request,
        "prompt": prompt,
        "provider_request": provider_request,
    }


def _rebuild_image_request(request: AIImageRequest, **changes: Any) -> AIImageRequest:
    payload = request.model_dump(mode="json", exclude={"request_hash"})
    payload.update(changes)
    return AIImageRequest(**payload, request_hash=ai_image_stable_hash(payload))


def _rebuild_provider_request(
    request: GeminiImageGenerationRequest,
    **changes: Any,
) -> GeminiImageGenerationRequest:
    payload = request.model_dump(mode="json", exclude={"content_hash"})
    payload.update(changes)
    return GeminiImageGenerationRequest(
        **payload,
        content_hash=ai_image_stable_hash(payload),
    )


def _qc_evidence(**overrides: Any) -> GeneratedImageQCEvidence:
    payload = {
        "image_ref": "fixture://img1/knowledge-silos.png",
        "image_hash": "a" * 64,
        "image_width": 2752,
        "image_height": 1536,
        "generated_letters_detected": False,
        "generated_numbers_detected": False,
        "logo_or_trademark_detected": False,
        "fake_ui_detected": False,
        "watermark_detected": False,
        "artifact_repairable_by_native_overlay": False,
        "detected_region_boxes": [],
        "representative_crop_refs": ["fixture://img1/crop-001"],
        "composition_compliance_score": 0.95,
        "semantic_match_score": 0.95,
        "visual_language_match_score": 0.95,
        "technical_image_fitness_score": 0.95,
        "crop_safety_score": 0.95,
        "reuse_similarity_score": 0.10,
        "rights_disclosure_complete": True,
    }
    payload.update(overrides)
    return GeneratedImageQCEvidence(**payload)


def test_provider_route_secret_defaults_and_readiness_are_fail_closed() -> None:
    assert "google_gemini_image" in CANONICAL_PROVIDER_KEYS
    assert "google_veo" in CANONICAL_PROVIDER_KEYS
    assert normalize_provider_key("gemini_image") == "google_gemini_image"
    assert normalize_provider_key("gemini_image") != normalize_provider_key(
        "google_veo"
    )

    secret_owners = [
        name
        for name, field in Settings.model_fields.items()
        if "GEMINI_API_KEY" in str(field.validation_alias)
    ]
    assert secret_owners == ["gemini_api_key"]
    assert GEMINI_IMAGE_DEFAULT_MODEL_ID == "gemini-3.1-flash-image"
    assert GEMINI_IMAGE_DEFAULT_SIZE == "2K"
    assert GEMINI_IMAGE_DEFAULT_ASPECT_RATIO == "16:9"
    assert GEMINI_IMAGE_MINIMUM_EFFECTIVE_RESOLUTION == "1080p"
    assert GEMINI_IMAGE_MAX_OUTPUTS == 1
    assert GEMINI_IMAGE_MAX_ATTEMPTS_PER_SCENE == 1

    readiness = GoogleGeminiImageAdapter(_settings()).validate_configuration()
    assert readiness.provider_key == "google_gemini_image"
    assert readiness.execution_enabled is False
    assert readiness.fixture_only is True
    assert readiness.will_execute is False
    assert readiness.provider_call_made is False
    assert readiness.credential_value_redacted is True

    route_tree = ast.parse(ROUTE_PATH.read_text(encoding="utf-8"))
    route_methods = [
        node.func.attr
        for node in ast.walk(route_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"get", "post", "put", "patch", "delete"}
    ]
    assert route_methods == ["get"]
    assert "/providers/google-gemini-image/readiness" in ROUTE_PATH.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"image_size": "8K"},
        {"aspect_ratio": "4:3"},
        {"output_count": 2},
        {"image_size": "1K"},
    ],
)
def test_unsupported_size_aspect_output_and_sub_1080p_block(
    img1_context: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        _rebuild_provider_request(img1_context["provider_request"], **changes)


def test_grounding_and_4k_require_explicit_review_approval(
    img1_context: dict[str, Any],
) -> None:
    request = img1_context["provider_request"]
    with pytest.raises(
        ValidationError, match="GEMINI_IMAGE_GROUNDING_APPROVAL_REQUIRED"
    ):
        _rebuild_provider_request(
            request,
            grounding_enabled=True,
            grounding_approval_ref=None,
        )
    grounded = _rebuild_provider_request(
        request,
        grounding_enabled=True,
        grounding_approval_ref="approval://img1/search-grounding-review",
    )
    assert grounded.grounding_enabled is True
    assert grounded.grounding_approval_ref

    with pytest.raises(
        ValidationError, match="GEMINI_IMAGE_4K_REVIEW_APPROVAL_REQUIRED"
    ):
        _rebuild_provider_request(
            request,
            image_size="4K",
            four_k_approval_ref=None,
        )
    four_k = _rebuild_provider_request(
        request,
        image_size="4K",
        four_k_approval_ref="approval://img1/4k-review",
    )
    assert four_k.image_size == "4K"
    assert four_k.four_k_approval_ref


def test_ai_image_request_is_bound_to_the_real_vsr1_decision(
    img1_context: dict[str, Any],
) -> None:
    request = img1_context["request"]
    decision = img1_context["decision"]
    assert request.visual_source_decision_hash == decision.content_hash
    assert (
        request.visual_source_route
        == VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY
    )
    assert request.minimum_effective_resolution == "1080p"
    assert request.production_eligible is False
    assert request.not_publishable is True

    tampered = decision.model_copy(update={"content_hash": "tampered-decision-hash"})
    with pytest.raises(
        ValueError, match="AI_IMAGE_VISUAL_SOURCE_DECISION_HASH_MISMATCH"
    ):
        _build_request(
            img1_context["requirements"],
            tampered,
            img1_context["direction"],
            img1_context["overlay"],
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"scene_truth_classification": "EVIDENCE"},
        {"scene_truth_classification": "ACTUAL_UI"},
        {"scene_truth_classification": "ACTUAL_PRODUCT"},
        {"evidence_truth_requirement": 0.80},
        {"product_specificity": 0.80},
        {
            "visual_source_route": "AI_GENERATED_IMAGE",
            "exact_text_required": True,
            "native_overlay_required": False,
            "native_overlay_plan_ref": None,
            "native_overlay_plan_hash": None,
            "text_safe_regions": [],
            "reserved_overlay_regions": [],
        },
    ],
)
def test_evidence_ui_product_and_unbound_exact_text_block(
    img1_context: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        _rebuild_image_request(img1_context["request"], **changes)


def test_prompt_reserves_safe_regions_and_forbids_generated_authority(
    img1_context: dict[str, Any],
) -> None:
    prompt: CompiledImagePrompt = img1_context["prompt"]
    mandatory = set(MANDATORY_IMAGE_NEGATIVE_CONSTRAINTS)
    assert mandatory <= set(prompt.negative_constraints)
    assert {"no letters", "no numbers", "no logos", "no fake software UI"} <= mandatory
    assert (
        "Reserve clean negative space for native overlay"
        in prompt.negative_space_requirement
    )
    assert "headline-safe(" in prompt.negative_space_requirement
    assert prompt.provider_call_made is False

    payload = prompt.model_dump(mode="json", exclude={"content_hash"})
    payload["negative_constraints"] = [
        item for item in payload["negative_constraints"] if item != "no letters"
    ]
    with pytest.raises(
        ValidationError, match="AI_IMAGE_PROMPT_MANDATORY_NEGATIVE_CONSTRAINT_MISSING"
    ):
        CompiledImagePrompt(**payload, content_hash=ai_image_stable_hash(payload))


def test_reference_assets_require_rights_and_reject_style_uploads(
    img1_context: dict[str, Any],
) -> None:
    checksum = "b" * 64
    reference = AIImageReferenceAsset(
        asset_ref="authorized-asset://subject/001",
        asset_hash=checksum,
        reference_role="SUBJECT",
        source="operator-owned fixture",
        rights_state="OWNED",
        checksum=checksum,
        authorization_ref="rights://img1/operator-owned/001",
    )
    request = _build_request(
        img1_context["requirements"],
        img1_context["decision"],
        img1_context["direction"],
        img1_context["overlay"],
        reference_assets=(reference,),
    )
    assert request.reference_asset_refs == [reference.asset_ref]
    assert request.reference_asset_hashes == [checksum]

    with pytest.raises(
        ValidationError, match="AI_IMAGE_STYLE_REFERENCE_UPLOADS_DISABLED"
    ):
        AIImageReferenceAsset(
            asset_ref="fixture://style/001",
            asset_hash=checksum,
            reference_role="STYLE",
            source="untrusted style upload",
            rights_state="LICENSED",
            checksum=checksum,
            authorization_ref="rights://style/001",
        )
    with pytest.raises(
        ValidationError, match="AI_IMAGE_REFERENCE_HASH_CHECKSUM_MISMATCH"
    ):
        AIImageReferenceAsset(
            asset_ref="fixture://subject/bad-hash",
            asset_hash=checksum,
            reference_role="SUBJECT",
            source="operator fixture",
            rights_state="AUTHORIZED",
            checksum="c" * 64,
            authorization_ref="rights://subject/bad-hash",
        )


def test_versioned_cost_and_approval_idempotency_attempt_guards(
    img1_context: dict[str, Any],
) -> None:
    catalog = GoogleGeminiImageModelPriceCatalog()
    estimate = catalog.estimate(
        model_id="gemini-3.1-flash-image",
        image_size="2K",
        aspect_ratio="16:9",
        output_count=1,
        attempt_count=1,
        hard_cap=Decimal("1.00"),
        approval_amount=Decimal("1.00"),
    )
    assert estimate.price_catalog_version == catalog.version
    assert estimate.price_catalog_ref == catalog.ref
    assert estimate.estimated_amount == Decimal("0.101")
    assert estimate.actual_amount is None
    canonical_estimate = catalog.estimate(
        model_id="gemini-3.1-flash-image",
        image_size="2K",
        aspect_ratio="16:9",
        output_count=1,
        attempt_count=1,
        hard_cap=Decimal("1.00"),
        approval_amount=Decimal("1.00"),
    )
    assert (
        validate_gemini_image_cost_snapshot_integrity(
            estimate,
            provider_key="google_gemini_image",
            model_id="gemini-3.1-flash-image",
            image_size="2K",
            aspect_ratio="16:9",
            output_count=1,
            attempt_count=1,
            catalog_estimate=canonical_estimate,
        )
        is estimate
    )
    with pytest.raises(ValidationError, match="frozen"):
        estimate.estimated_amount = Decimal("0.001")

    tampered_hash = estimate.model_copy(update={"snapshot_hash": "0" * 64})
    with pytest.raises(ValueError, match="GEMINI_IMAGE_COST_SNAPSHOT_HASH_MISMATCH"):
        validate_gemini_image_cost_snapshot_integrity(
            tampered_hash,
            provider_key="google_gemini_image",
            model_id="gemini-3.1-flash-image",
            image_size="2K",
            aspect_ratio="16:9",
            output_count=1,
            attempt_count=1,
            catalog_estimate=canonical_estimate,
        )

    tampered_payload = estimate.model_dump(mode="python", exclude={"snapshot_hash"})
    tampered_payload["estimated_unit_cost"] = Decimal("0.050")
    tampered_payload["estimated_amount"] = Decimal("0.050")
    tampered_cost = GeminiImageCostEstimateSnapshot.model_construct(
        **tampered_payload,
        snapshot_hash=ai_image_stable_hash(tampered_payload),
    )
    with pytest.raises(ValueError, match="GEMINI_IMAGE_COST_CATALOG_ESTIMATE_MISMATCH"):
        validate_gemini_image_cost_snapshot_integrity(
            tampered_cost,
            provider_key="google_gemini_image",
            model_id="gemini-3.1-flash-image",
            image_size="2K",
            aspect_ratio="16:9",
            output_count=1,
            attempt_count=1,
            catalog_estimate=canonical_estimate,
        )
    coherent_binding_tampers = (
        (
            {"provider_key": "unapproved-image-provider"},
            "GEMINI_IMAGE_COST_PROVIDER_BINDING_MISMATCH",
        ),
        (
            {"model_id": "unapproved-image-model"},
            "GEMINI_IMAGE_COST_MODEL_BINDING_MISMATCH",
        ),
        (
            {"aspect_ratio": "1:1"},
            "GEMINI_IMAGE_COST_OUTPUT_BINDING_MISMATCH",
        ),
        (
            {"output_count": 2, "estimated_amount": Decimal("0.202")},
            "GEMINI_IMAGE_COST_OUTPUT_BINDING_MISMATCH",
        ),
        (
            {"attempt_count": 2, "estimated_amount": Decimal("0.202")},
            "GEMINI_IMAGE_COST_ATTEMPT_BINDING_MISMATCH",
        ),
    )
    for updates, reason_code in coherent_binding_tampers:
        tampered = estimate.model_copy(update=updates)
        tampered = tampered.model_copy(
            update={
                "snapshot_hash": ai_image_stable_hash(
                    tampered.model_dump(mode="json", exclude={"snapshot_hash"})
                )
            }
        )
        with pytest.raises(ValueError, match=reason_code):
            validate_gemini_image_cost_snapshot_integrity(
                tampered,
                provider_key="google_gemini_image",
                model_id="gemini-3.1-flash-image",
                image_size="2K",
                aspect_ratio="16:9",
                output_count=1,
                attempt_count=1,
                catalog_estimate=canonical_estimate,
            )
    with pytest.raises(
        ValueError, match="GEMINI_IMAGE_EFFECTIVE_RESOLUTION_BELOW_1080P"
    ):
        catalog.estimate(
            model_id="gemini-3.1-flash-image",
            image_size="1K",
            aspect_ratio="16:9",
            output_count=1,
            attempt_count=1,
            hard_cap=Decimal("1.00"),
            approval_amount=Decimal("1.00"),
        )
    with pytest.raises(ValueError, match="GEMINI_IMAGE_4K_REVIEW_APPROVAL_REQUIRED"):
        catalog.estimate(
            model_id="gemini-3.1-flash-image",
            image_size="4K",
            aspect_ratio="16:9",
            output_count=1,
            attempt_count=1,
            hard_cap=Decimal("1.00"),
            approval_amount=Decimal("1.00"),
        )
    four_k_estimate = catalog.estimate(
        model_id="gemini-3.1-flash-image",
        image_size="4K",
        aspect_ratio="16:9",
        output_count=1,
        attempt_count=1,
        hard_cap=Decimal("1.00"),
        approval_amount=Decimal("1.00"),
        four_k_approval_ref="approval://img1/4k-cost-review",
    )
    assert four_k_estimate.estimated_amount == Decimal("0.151")

    provider_request = img1_context["provider_request"]
    for failed_gate in (
        "provider_boundary_gate_passed",
        "provider_cost_estimate_gate_passed",
        "channel_monthly_budget_gate_passed",
        "paid_attempt_limit_gate_passed",
        "provider_idempotency_key_valid",
    ):
        fixture_client = _FixtureClient()
        adapter = GoogleGeminiImageAdapter(_settings(), fixture_client=fixture_client)
        receipt = adapter.submit_generation(
            provider_request,
            gates=_gates(provider_request, **{failed_gate: False}),
            fixture_only=True,
        )
        assert receipt.provider_status == "FIXTURE_PLANNING_GATE_BLOCKED"
        assert receipt.generation_attempts_consumed == 0
        assert fixture_client.submit_count == 0

    for closed_real_gate in (
        "paid_call_authorization_gate_passed",
        "global_kill_switch_open",
        "provider_kill_switch_open",
    ):
        values = {
            "paid_call_authorization_gate_passed": True,
            "global_kill_switch_open": True,
            "provider_kill_switch_open": True,
            closed_real_gate: False,
        }
        blocked = GoogleGeminiImageAdapter(_settings()).submit_generation(
            provider_request,
            gates=_gates(provider_request, **values),
            fixture_only=False,
        )
        assert blocked.provider_status == "GATE_BLOCKED"
        assert blocked.provider_call_made is False
        assert blocked.generation_attempts_consumed == 0

    disabled_client = _FixtureClient()
    disabled_adapter = GoogleGeminiImageAdapter(
        _settings(), fixture_client=disabled_client
    )
    disabled = disabled_adapter.submit_generation(
        provider_request,
        gates=_gates(
            provider_request,
            paid_call_authorization_gate_passed=True,
            global_kill_switch_open=True,
            provider_kill_switch_open=True,
        ),
        fixture_only=False,
    )
    assert disabled.provider_status == "EXECUTION_DISABLED"
    assert disabled.provider_call_made is False
    assert disabled.generation_attempts_consumed == 0
    assert disabled_client.submit_count == 0

    invalid_attempt = disabled.model_dump(mode="json", exclude={"state_hash"})
    invalid_attempt["generation_attempts_consumed"] = 1
    with pytest.raises(
        ValidationError,
        match="GEMINI_IMAGE_NON_NETWORK_FLOW_MUST_CONSUME_ZERO_ATTEMPTS",
    ):
        GeminiImageOperationReceipt(
            **invalid_attempt,
            state_hash=ai_image_stable_hash(invalid_attempt),
        )


def test_r3d8_uses_the_versioned_image_catalog_and_existing_paid_boundary() -> None:
    catalog = GoogleGeminiImageModelPriceCatalog()
    item = {
        "provider_key": "google_gemini_image",
        "provider_stage": "AI_IMAGE_GENERATION",
        "price_catalog_version": catalog.version,
        "price_catalog_ref": catalog.ref,
        "model_id": "gemini-3.1-flash-image",
        "image_size": "2K",
        "aspect_ratio": "16:9",
        "output_count": 1,
        "attempt_count": 1,
        "estimated_cost": "0.101",
        "actual_amount": None,
        "currency": "USD",
        "hard_cap": "1.00",
        "approval_amount": "1.00",
    }
    result = derive_google_gemini_image_catalog_cost(item, currency="USD")
    assert result.passed is True
    assert result.status == "PASS"
    assert result.details is not None
    assert result.details["cost_authority"] == "VERSIONED_MODEL_PRICE_CATALOG"
    assert result.details["price_catalog_version"] == catalog.version
    assert result.details["estimated_cost"] == "0.101"
    assert result.details["actual_amount"] is None
    assert "google_gemini_image" in PAID_PROVIDER_KEYS
    assert GOOGLE_GEMINI_IMAGE_PROVIDER_STAGES == {"AI_IMAGE_GENERATION"}
    assert MAX_PAID_ATTEMPTS_BY_PROVIDER["google_gemini_image"] == 1

    untrusted = derive_google_gemini_image_catalog_cost(
        {**item, "estimated_cost": "99.00"},
        currency="USD",
    )
    assert untrusted.passed is False
    assert "GEMINI_IMAGE_FREEFORM_ESTIMATE_MISMATCH_CATALOG" in untrusted.reason_codes


def test_fixture_is_zero_network_idempotent_transient_and_atomically_materialized(
    img1_context: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_png = build_fixture_png()
    fixture_client = _FixtureClient(image_bytes=fixture_png)
    adapter = GoogleGeminiImageAdapter(_settings(), fixture_client=fixture_client)
    request = img1_context["provider_request"]

    receipt = adapter.submit_generation(
        request, gates=_gates(request), fixture_only=True
    )
    duplicate = adapter.submit_generation(
        request, gates=_gates(request), fixture_only=True
    )
    assert duplicate.state_hash == receipt.state_hash
    assert fixture_client.submit_count == 1
    assert receipt.provider_call_made is False
    assert receipt.generation_attempts_consumed == 0
    assert receipt.actual_cost is None
    assert receipt.fallback_provider_key is None
    assert receipt.external_provider_fallback_used is False
    assert receipt.output_reference.startswith("volatile://google-gemini-image/")

    transient = adapter.transient_output_for(receipt)
    durable_json = json.dumps(receipt.model_dump(mode="json"), sort_keys=True)
    assert "fixture-secret" not in durable_json
    assert "https://" not in durable_json
    assert "image_bytes" not in durable_json
    assert "fixture-secret" not in repr(transient)
    assert repr(fixture_png[:16]) not in repr(transient)

    workspace = tmp_path / "img1-workspace"
    destination = workspace / "source" / "ai-image" / "knowledge-silos.png"
    with pytest.raises(ValueError, match="GEMINI_IMAGE_OUTPUT_PATH_ESCAPES_WORKSPACE"):
        adapter.build_output_download_plan(
            receipt,
            workspace_root=workspace,
            destination_path=tmp_path / "outside-workspace.png",
        )
    plan = adapter.build_output_download_plan(
        receipt,
        workspace_root=workspace,
        destination_path=destination,
    )
    replacements: list[tuple[Path, Path]] = []
    fsync_targets: list[str] = []
    real_replace = gemini_image_provider_module.os.replace
    real_fsync = gemini_image_provider_module.os.fsync

    def recording_replace(source: str | Path, target: str | Path) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    def recording_fsync(file_descriptor: int) -> None:
        fsync_targets.append(
            "directory" if stat.S_ISDIR(os.fstat(file_descriptor).st_mode) else "file"
        )
        real_fsync(file_descriptor)

    monkeypatch.setattr(gemini_image_provider_module.os, "replace", recording_replace)
    monkeypatch.setattr(gemini_image_provider_module.os, "fsync", recording_fsync)
    materialized = adapter.materialize_output(plan, transient=transient)
    assert replacements == [
        (destination.with_name(destination.name + ".part"), destination)
    ]
    assert fsync_targets == ["file", "directory"]
    assert destination.read_bytes() == fixture_png
    assert materialized["sha256"] == hashlib.sha256(fixture_png).hexdigest()
    assert materialized["image_width"] == 2752
    assert materialized["image_height"] == 1536
    assert materialized["image_format"] == "PNG"
    assert materialized["part_path_remaining"] is False
    assert materialized["already_materialized"] is False
    assert not destination.with_name(destination.name + ".part").exists()
    idempotent = adapter.materialize_output(plan, transient=transient)
    assert idempotent["already_materialized"] is True
    assert destination.read_bytes() == fixture_png


def test_materialization_failure_cleans_part_and_provider_failure_has_no_fallback(
    img1_context: dict[str, Any],
    tmp_path: Path,
) -> None:
    request = img1_context["provider_request"]
    invalid_client = _FixtureClient(image_bytes=b"\x89PNG\r\n\x1a\n" + b"truncated")
    adapter = GoogleGeminiImageAdapter(_settings(), fixture_client=invalid_client)
    receipt = adapter.submit_generation(
        request, gates=_gates(request), fixture_only=True
    )
    workspace = tmp_path / "invalid-workspace"
    destination = workspace / "invalid.png"
    plan = adapter.build_output_download_plan(
        receipt,
        workspace_root=workspace,
        destination_path=destination,
    )
    with pytest.raises(ValueError, match="GEMINI_IMAGE_PNG_TRUNCATED"):
        adapter.materialize_output(
            plan, transient=adapter.transient_output_for(receipt)
        )
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".part").exists()

    conflict_client = _FixtureClient()
    conflict_adapter = GoogleGeminiImageAdapter(
        _settings(), fixture_client=conflict_client
    )
    conflict_receipt = conflict_adapter.submit_generation(
        request,
        gates=_gates(request),
        fixture_only=True,
    )
    conflict_workspace = tmp_path / "conflict-workspace"
    conflict_destination = conflict_workspace / "source" / "existing.png"
    conflict_destination.parent.mkdir(parents=True)
    conflict_destination.write_bytes(b"operator-owned-existing-content")
    conflict_plan = conflict_adapter.build_output_download_plan(
        conflict_receipt,
        workspace_root=conflict_workspace,
        destination_path=conflict_destination,
    )
    with pytest.raises(
        FileExistsError, match="GEMINI_IMAGE_OUTPUT_DESTINATION_ALREADY_EXISTS"
    ):
        conflict_adapter.materialize_output(
            conflict_plan,
            transient=conflict_adapter.transient_output_for(conflict_receipt),
        )
    assert conflict_destination.read_bytes() == b"operator-owned-existing-content"
    assert not conflict_destination.with_name(
        conflict_destination.name + ".part"
    ).exists()

    failure = RuntimeError("fixture provider failure")
    failing_client = _FixtureClient(failure=failure)
    failing_adapter = GoogleGeminiImageAdapter(
        _settings(), fixture_client=failing_client
    )
    with pytest.raises(RuntimeError, match="fixture provider failure"):
        failing_adapter.submit_generation(
            request, gates=_gates(request), fixture_only=True
        )
    assert failing_client.submit_count == 1
    assert not failing_adapter._operations_by_fingerprint


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({"generated_letters_detected": True}, "GENERATED_TEXT_ARTIFACT"),
        ({"fake_ui_detected": True}, "FAKE_UI_RISK"),
        ({"logo_or_trademark_detected": True}, "LOGO_OR_TRADEMARK_RISK"),
        ({"crop_safety_score": 0.40}, "CROPSAFETYGATE_BELOW_MINIMUM"),
    ],
)
def test_generated_text_fake_ui_logo_and_crop_qc_gates_block(
    changes: dict[str, Any],
    reason_code: str,
) -> None:
    manifest = PostGenerationImageQC().evaluate(_qc_evidence(**changes))
    assert manifest.verdict == "BLOCK"
    assert reason_code in manifest.reason_codes
    assert len(manifest.gate_results) == 9
    assert manifest.production_eligible is False


def test_normalization_and_native_overlay_binding_validate(
    img1_context: dict[str, Any],
) -> None:
    checksum = "d" * 64
    normalization = ImageNormalizationPlanner().plan(
        source_ref="fixture://img1/knowledge-silos.png",
        source_checksum=checksum,
        source_width=2752,
        source_height=1536,
        target_aspect_ratio="16:9",
        image_format="PNG",
    )
    assert normalization.minimum_effective_resolution == "1080p"
    assert normalization.effective_width_after_crop >= 1920
    assert normalization.effective_height_after_crop >= 1080
    assert normalization.upscale_applied is False
    assert normalization.execution_allowed is False
    assert normalization.checksum == checksum
    with pytest.raises(
        ValidationError, match="AI_IMAGE_EFFECTIVE_RESOLUTION_BELOW_1080P"
    ):
        ImageNormalizationPlanner().plan(
            source_ref="fixture://img1/too-small.png",
            source_checksum=checksum,
            source_width=1280,
            source_height=720,
            target_aspect_ratio="16:9",
            image_format="PNG",
        )

    binding = NativeOverlayImageBindingBuilder().build(
        request=img1_context["request"],
        overlay_plan=img1_context["overlay"],
        generated_image_ref="fixture://img1/knowledge-silos.png",
        generated_image_hash=checksum,
    )
    assert binding.image_model_owns_final_text is False
    assert binding.authoritative_content_kinds == ["HEADLINE"]
    assert binding.text_safe_regions == img1_context["request"].text_safe_regions
    assert binding.production_eligible is False
    wrong_overlay = img1_context["overlay"].model_copy(
        update={"content_hash": "wrong-overlay-hash"}
    )
    with pytest.raises(ValueError, match="AI_IMAGE_NATIVE_OVERLAY_PLAN_HASH_MISMATCH"):
        NativeOverlayImageBindingBuilder().build(
            request=img1_context["request"],
            overlay_plan=wrong_overlay,
            generated_image_ref="fixture://img1/knowledge-silos.png",
            generated_image_hash=checksum,
        )


def test_offline_rehearsal_passes_without_mutating_historical_artifacts(
    tmp_path: Path,
) -> None:
    from app.services.google_gemini_image_rehearsal import (
        GoogleGeminiImageLocalFixtureRehearsal,
    )

    historical_paths = [
        REPO_ROOT / "reports" / "vsr1_summary.json",
        REPO_ROOT / "reports" / "vsr1_niche_aware_visual_routing_report.md",
        REPO_ROOT / "config" / "visual_source_routing_policy_catalog.yaml",
    ]
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in historical_paths
    }
    summary = GoogleGeminiImageLocalFixtureRehearsal(_settings()).run(
        workspace_root=tmp_path,
    )
    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in historical_paths
    }
    assert after == before

    assert summary["verdict"] == "PASS"
    assert summary["provider_key"] == "google_gemini_image"
    assert summary["visual_source_route"] == "AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY"
    assert summary["transport"] == "LOCAL_FIXTURE_ONLY"
    assert summary["provider_call_made"] is False
    assert summary["generation_attempts_consumed"] == 0
    assert summary["actual_cost"] is None
    assert summary["production_eligible"] is False
    assert summary["not_publishable"] is True
    assert summary["duplicate_submit_prevented"] is True
    assert summary["native_overlay_bound"] is True
    assert summary["normalization_planned_only"] is True
    assert summary["raw_url_persisted"] is False
    assert summary["part_path_remaining"] is False
    assert summary["database_mutated"] is False
    assert summary["archive_uploaded"] is False
    assert summary["final_media_rendered"] is False
    assert summary["acceptance_checks"]["real_guards_closed"] is True
    assert summary["acceptance_checks"]["manifest_safe"] is True
    assert summary["acceptance_checks"]["provenance_safe"] is True

    workspace = tmp_path / "img1-google-gemini-image-fixture"
    summary_path = workspace / "manifests" / "img1_rehearsal_summary.json"
    assert summary_path.is_file()
    assert list((workspace / "source" / "ai-image").glob("*.png"))
    durable_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((workspace / "manifests").glob("*.json"))
    )
    assert "fixture-secret" not in durable_text
    assert "raw_temporary_url" not in durable_text
    assert "image_bytes" not in durable_text

    generation_payload = json.loads(
        (workspace / "manifests" / "ai_generation_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    generation_manifest = AIGenerationManifest.model_validate(generation_payload)
    assert generation_manifest.media_kind == "STILL_IMAGE"
    assert generation_manifest.provider_request_id
    assert generation_manifest.provider_operation_id
    assert generation_manifest.provider_status == "LOCAL_FIXTURE_MATERIALIZED"
    assert generation_manifest.submitted_at is not None
    assert generation_manifest.completed_at is not None
    assert generation_manifest.output_reference.startswith(
        "volatile://google-gemini-image/"
    )
    assert (
        generation_manifest.local_path == "source/ai-image/knowledge-silos-fixture.png"
    )
    assert generation_manifest.image_width == 2752
    assert generation_manifest.image_height == 1536
    assert generation_manifest.image_format == "PNG"
    assert generation_manifest.visual_source_decision_ref
    assert generation_manifest.visual_direction_contract_ref
    assert generation_manifest.native_overlay_required is True
    assert generation_manifest.text_safe_regions
    assert generation_manifest.post_generation_qc_refs
    assert generation_manifest.synthetic_media_disclosure_ref
    assert generation_manifest.production_eligible is False
    assert generation_manifest.not_publishable is True
    assert generation_manifest.manifest_hash == ai_image_stable_hash(
        generation_manifest.model_dump(mode="json", exclude={"manifest_hash"})
    )

    provenance_payload = json.loads(
        (workspace / "manifests" / "ai_image_provenance_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    provenance = AIImageProvenanceManifest.model_validate(provenance_payload)
    assert provenance.provider == "google_gemini_image"
    assert provenance.generated_evidence_authority is False
    assert provenance.provider_call_made is False
    assert provenance.production_eligible is False
    assert provenance.not_publishable is True
    assert provenance.post_generation_qc_refs

    disclosure = json.loads(
        (workspace / "manifests" / "synthetic_media_disclosure.json").read_text(
            encoding="utf-8"
        )
    )
    assert disclosure["synthetic_media_disclosure_required"] is True
    assert disclosure["generated_evidence_authority"] is False
    assert disclosure["provider_call_made"] is False
    assert disclosure["production_eligible"] is False
    assert disclosure["not_publishable"] is True


def test_img1_sources_have_no_network_or_prohibited_execution_boundary() -> None:
    assert REHEARSAL_PATH.is_file()
    source_paths = [PROVIDER_PATH, REHEARSAL_PATH]
    forbidden_imports = {
        "google.genai",
        "httpx",
        "requests",
        "socket",
        "subprocess",
        "urllib.request",
    }
    forbidden_execution_modules = {
        "app.providers.elevenlabs",
        "app.providers.google_veo",
        "app.providers.pexels",
        "app.services.drive_archive",
        "app.services.native_motion_compiler",
        "app.services.youtube",
    }
    imported_modules: set[str] = set()
    combined_source = ""
    for path in source_paths:
        source = path.read_text(encoding="utf-8")
        combined_source += source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
    assert not (imported_modules & forbidden_imports)
    assert not (imported_modules & forbidden_execution_modules)
    for prohibited_symbol in (
        "generate_content(",
        "models.generate_",
        "urlopen(",
        "requests.get(",
        "requests.post(",
        "subprocess.run(",
        "subprocess.Popen(",
    ):
        assert prohibited_symbol not in combined_source
