from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.contracts.ai_image import (
    AIImageProvenanceManifest,
    GeneratedImageQCEvidence,
    ai_image_stable_hash,
)
from app.contracts.asset_acquisition import AIGenerationManifest
from app.contracts.google_gemini_image import (
    GeminiImageExecutionGates,
    GeminiImageGenerationRequest,
    GeminiImageOperationReceipt,
)
from app.contracts.native_renderer import NativeOverlayPlan, TextSafeRegion
from app.contracts.visual_direction import VisualDirectionContract
from app.contracts.visual_routing import (
    AIImageEligibilityResult,
    AuthoritativeOverlayContentKind,
    ExactTextNativeOverlayContract,
    NicheVisualSourceProfile,
    SceneVisualRealizationRequirements,
    VisualSourceRoute,
)
from app.core.config import (
    GEMINI_IMAGE_DEFAULT_ASPECT_RATIO,
    GEMINI_IMAGE_DEFAULT_MODEL_ID,
    GEMINI_IMAGE_DEFAULT_SIZE,
    Settings,
)
from app.providers.google_gemini_image import GoogleGeminiImageAdapter, build_fixture_png
from app.services.ai_image import (
    AIImageRequestBuilder,
    ImageNormalizationPlanner,
    ImagePromptCompiler,
    NativeOverlayImageBindingBuilder,
    PostGenerationImageQC,
)
from app.services.google_gemini_image_catalog import GoogleGeminiImageModelPriceCatalog
from app.services.visual_source_routing import AIImageEligibilityGate, VisualSourceRouter


FIXTURE_DIRECTORY = "img1-google-gemini-image-fixture"
FIXTURE_SCENE_ID = "scene-knowledge-silos"
FIXTURE_TIMESTAMP = datetime(2026, 7, 18, tzinfo=UTC)
FIXTURE_IMAGE_RELATIVE_PATH = Path("source/ai-image/knowledge-silos-fixture.png")
FIXTURE_IMAGE_REF = (
    "workspace://img1-google-gemini-image-fixture/"
    "source/ai-image/knowledge-silos-fixture.png"
)
FIXTURE_DECISION_REF = "visual-source-decision://img1/knowledge-silos"
FIXTURE_VISUAL_DIRECTION_REF = "visual-direction://img1/knowledge-silos"
FIXTURE_QC_REF = "manifest://img1/knowledge-silos/post-generation-image-qc"
FIXTURE_DISCLOSURE_REF = "disclosure://img1/knowledge-silos/synthetic-media"
FIXTURE_ATTEMPT_REF = "attempt://img1/knowledge-silos/no-paid-attempt-consumed"


class _LocalGeminiImageFixtureClient:
    """Deterministic in-process transport; it cannot make a network request."""

    raw_temporary_url = (
        "https://fixture.invalid/google-gemini-image/knowledge-silos.png"
        "?token=fixture-secret-never-persist"
    )

    def __init__(self) -> None:
        self.submit_count = 0
        self._image_bytes = build_fixture_png()

    def submit(self, request: GeminiImageGenerationRequest) -> dict[str, Any]:
        self.submit_count += 1
        suffix = request.content_hash[:16]
        return {
            "request_id": f"fixture-request-{suffix}",
            "operation_id": f"fixture-operation-{suffix}",
            "status": "LOCAL_FIXTURE_SUCCEEDED",
            "image_bytes": self._image_bytes,
            "raw_temporary_url": self.raw_temporary_url,
        }


class GoogleGeminiImageLocalFixtureRehearsal:
    """Run one knowledge-silos IMG1 rehearsal with no external side effects."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings(
            _env_file=None,
            provider_real_execution_enabled=False,
            provider_production_execution_enabled=False,
            media_provider_calls_disabled=True,
            gemini_image_real_generation_enabled=False,
            img1_fixture_only=True,
            gemini_image_provider_route_approved=True,
            gemini_image_model_id=GEMINI_IMAGE_DEFAULT_MODEL_ID,
            gemini_image_default_size=GEMINI_IMAGE_DEFAULT_SIZE,
            gemini_image_default_aspect_ratio=GEMINI_IMAGE_DEFAULT_ASPECT_RATIO,
            gemini_image_max_outputs=1,
            gemini_image_max_attempts_per_scene=1,
        )
        self._validate_fixture_settings()

    def run(self, *, workspace_root: Path) -> dict[str, Any]:
        caller_workspace = self._workspace_root(workspace_root)
        rehearsal_root = self._safe_child(caller_workspace, FIXTURE_DIRECTORY)
        manifests_dir = self._safe_child(rehearsal_root, "manifests")
        image_path = self._safe_child(rehearsal_root, *FIXTURE_IMAGE_RELATIVE_PATH.parts)
        manifests_dir.mkdir(parents=True, exist_ok=True)
        image_path.parent.mkdir(parents=True, exist_ok=True)

        requirements = self._requirements()
        eligibility = AIImageEligibilityGate().evaluate(
            requirements,
            rights_policy_allows_generation=True,
        )
        decision = VisualSourceRouter().route(
            requirements,
            rights_policy_allows_generation=True,
        )
        if eligibility.result != AIImageEligibilityResult.AI_IMAGE_WITH_NATIVE_OVERLAY_REQUIRED:
            raise ValueError("IMG1_REHEARSAL_AI_IMAGE_ELIGIBILITY_MISMATCH")
        if decision.preferred_source_route != VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY:
            raise ValueError("IMG1_REHEARSAL_VISUAL_SOURCE_ROUTE_MISMATCH")

        visual_direction = self._visual_direction()
        overlay_plan = self._overlay_plan(decision)
        catalog = GoogleGeminiImageModelPriceCatalog()
        cost = catalog.estimate(
            model_id=self.settings.gemini_image_model_id,
            image_size=self.settings.gemini_image_default_size,
            aspect_ratio=self.settings.gemini_image_default_aspect_ratio,
            output_count=1,
            attempt_count=1,
            hard_cap=Decimal("0.25"),
            approval_amount=Decimal("0.25"),
        )
        approval_ref = "approval://img1/knowledge-silos/offline-fixture-only"
        approval_scope = "IMG1_OFFLINE_FIXTURE_KNOWLEDGE_SILOS"
        idempotency_key = "provider-idem:" + ai_image_stable_hash(
            {
                "provider_key": "google_gemini_image",
                "model_id": self.settings.gemini_image_model_id,
                "scene_id": requirements.scene_id,
                "visual_source_decision_hash": decision.content_hash,
                "native_overlay_plan_hash": overlay_plan.content_hash,
                "approval_scope": approval_scope,
            }
        )

        generic_request = AIImageRequestBuilder().build(
            requirements=requirements,
            decision=decision,
            decision_ref=FIXTURE_DECISION_REF,
            visual_direction=visual_direction,
            visual_direction_ref=FIXTURE_VISUAL_DIRECTION_REF,
            package_id="img1-fixture-package",
            request_id="img1-ai-image-request-knowledge-silos",
            prompt_intent=(
                "An editorial knowledge-silos metaphor using separated architectural volumes, "
                "connected by restrained pathways, with no generated text or interface elements."
            ),
            custom_composition_reason=(
                "Stock cannot carry the specific isolation metaphor; native headline remains authoritative."
            ),
            requested_image_size="2K",
            cost_catalog_ref=catalog.ref,
            cost_estimate_ref=cost.snapshot_hash,
            approval_ref=approval_ref,
            approval_scope=approval_scope,
            idempotency_key=idempotency_key,
            native_overlay_plan=overlay_plan,
            reference_assets=(),
        )
        compiled_prompt = ImagePromptCompiler().compile(
            requirements=requirements,
            visual_direction=visual_direction,
            decision=decision,
            request=generic_request,
            continuity_hints=("Maintain calm editorial geometry across adjacent scenes.",),
        )

        fake_client = _LocalGeminiImageFixtureClient()
        adapter = GoogleGeminiImageAdapter(self.settings, fixture_client=fake_client)
        provider_request = adapter.build_request(generic_request, compiled_prompt)
        gate_payload: dict[str, Any] = {
            "provider_boundary_gate_passed": True,
            "paid_call_authorization_gate_passed": False,
            "provider_cost_estimate_gate_passed": True,
            "channel_monthly_budget_gate_passed": True,
            "paid_attempt_limit_gate_passed": True,
            "provider_idempotency_key_valid": True,
            "global_kill_switch_open": False,
            "provider_kill_switch_open": False,
            "approved_production_execution_scope": False,
            "provider_boundary_gate_ref": "fixture-gate://img1/provider-boundary/pass",
            "paid_call_authorization_gate_ref": None,
            "provider_cost_estimate_gate_ref": provider_request.cost_ref,
            "channel_monthly_budget_gate_ref": "fixture-gate://img1/channel-budget/pass",
            "paid_attempt_limit_gate_ref": FIXTURE_ATTEMPT_REF,
            "provider_idempotency_key_ref": provider_request.idempotency_key,
            "global_kill_switch_ref": None,
            "provider_kill_switch_ref": None,
            "request_fingerprint": adapter.idempotency_fingerprint(provider_request),
        }
        fixture_gates = GeminiImageExecutionGates(
            **gate_payload,
            evidence_hash=ai_image_stable_hash(gate_payload),
        )
        submitted = adapter.submit_generation(
            provider_request,
            gates=fixture_gates,
            fixture_only=True,
        )
        duplicate = adapter.submit_generation(
            provider_request,
            gates=fixture_gates,
            fixture_only=True,
        )
        receipt = self._deterministic_receipt(submitted)
        transient = adapter.transient_output_for(submitted)
        execution_plan = adapter.build_output_download_plan(
            receipt,
            workspace_root=rehearsal_root,
            destination_path=image_path,
        )
        materialization = adapter.materialize_output(execution_plan, transient=transient)
        portable_plan = execution_plan
        portable_materialization = {
            **materialization,
            "local_path": FIXTURE_IMAGE_RELATIVE_PATH.as_posix(),
            "output_reference": receipt.output_reference,
            "production_eligible": False,
            "not_publishable": True,
        }

        normalization = ImageNormalizationPlanner().plan(
            source_ref=FIXTURE_IMAGE_REF,
            source_checksum=materialization["sha256"],
            source_width=materialization["image_width"],
            source_height=materialization["image_height"],
            target_aspect_ratio="16:9",
            image_format=materialization["image_format"],
        )
        qc_evidence = GeneratedImageQCEvidence(
            image_ref=FIXTURE_IMAGE_REF,
            image_hash=materialization["sha256"],
            image_width=materialization["image_width"],
            image_height=materialization["image_height"],
            generated_letters_detected=False,
            generated_numbers_detected=False,
            logo_or_trademark_detected=False,
            fake_ui_detected=False,
            watermark_detected=False,
            artifact_repairable_by_native_overlay=False,
            representative_crop_refs=[f"{FIXTURE_IMAGE_REF}#representative-full-frame"],
            composition_compliance_score=0.96,
            semantic_match_score=0.95,
            visual_language_match_score=0.94,
            technical_image_fitness_score=0.97,
            crop_safety_score=0.96,
            reuse_similarity_score=0.08,
            rights_disclosure_complete=True,
        )
        post_qc = PostGenerationImageQC().evaluate(qc_evidence)
        overlay_binding = NativeOverlayImageBindingBuilder().build(
            request=generic_request,
            overlay_plan=overlay_plan,
            generated_image_ref=FIXTURE_IMAGE_REF,
            generated_image_hash=materialization["sha256"],
        )
        provenance = self._provenance(
            request=generic_request,
            receipt=receipt,
            provider_request=provider_request,
            materialization=materialization,
            cost_snapshot_ref=cost.snapshot_hash,
            approval_ref=approval_ref,
        )
        generation_manifest = self._generation_manifest(
            request=generic_request,
            receipt=receipt,
            provider_request=provider_request,
            materialization=materialization,
        )
        disclosure = {
            "source_role": "AI_GENERATED_STILL_IMAGE",
            "provider_key": "google_gemini_image",
            "provider_model_id": provider_request.model_id,
            "scene_id": requirements.scene_id,
            "synthetic_media_disclosure_required": True,
            "generated_evidence_authority": False,
            "provider_call_made": False,
            "production_eligible": False,
            "not_publishable": True,
            "disclosure_ref": FIXTURE_DISCLOSURE_REF,
        }

        evidence: dict[str, dict[str, Any]] = {
            "scene_visual_realization_requirements.json": requirements.model_dump(mode="json"),
            "ai_image_eligibility_assessment.json": eligibility.model_dump(mode="json"),
            "visual_source_decision.json": decision.model_dump(mode="json"),
            "visual_direction_contract.json": visual_direction.model_dump(mode="json"),
            "native_overlay_plan.json": overlay_plan.model_dump(mode="json"),
            "ai_image_request.json": generic_request.model_dump(mode="json"),
            "compiled_image_prompt.json": compiled_prompt.model_dump(mode="json"),
            "google_gemini_image_generation_request.json": provider_request.model_dump(mode="json"),
            "google_gemini_image_cost_estimate.json": cost.model_dump(mode="json"),
            "google_gemini_image_operation_receipt.json": receipt.model_dump(mode="json"),
            "google_gemini_image_output_materialization_plan.json": portable_plan.model_dump(mode="json"),
            "local_fixture_materialization_receipt.json": portable_materialization,
            "image_normalization_manifest.json": normalization.model_dump(mode="json"),
            "generated_image_qc_evidence.json": qc_evidence.model_dump(mode="json"),
            "post_generation_image_qc_manifest.json": post_qc.model_dump(mode="json"),
            "native_overlay_image_binding.json": overlay_binding.model_dump(mode="json"),
            "ai_generation_manifest.json": generation_manifest.model_dump(mode="json"),
            "ai_image_provenance_manifest.json": provenance.model_dump(mode="json"),
            "synthetic_media_disclosure.json": disclosure,
        }
        self._assert_no_transient_output_persisted(evidence, fake_client)
        evidence_hashes = {
            name: ai_image_stable_hash(payload)
            for name, payload in sorted(evidence.items())
        }
        expected_qc_gates = {
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
        try:
            adapter.transient_output_for(submitted)
            transient_purged = False
        except ValueError:
            transient_purged = True
        acceptance_checks = {
            "stock_assisted_profile": requirements.niche_visual_source_profile
            == NicheVisualSourceProfile.STOCK_ASSISTED,
            "ai_image_route_with_native_overlay": decision.preferred_source_route
            == VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
            "eligibility_passed": eligibility.result
            == AIImageEligibilityResult.AI_IMAGE_WITH_NATIVE_OVERLAY_REQUIRED,
            "catalog_estimate_bound": cost.actual_amount is None
            and cost.model_id == GEMINI_IMAGE_DEFAULT_MODEL_ID
            and cost.image_size == GEMINI_IMAGE_DEFAULT_SIZE,
            "fixture_transport_only": receipt.provider_call_made is False
            and receipt.generation_attempts_consumed == 0
            and receipt.actual_cost is None,
            "real_guards_closed": fixture_gates.global_kill_switch_open is False
            and fixture_gates.provider_kill_switch_open is False
            and fixture_gates.paid_call_authorization_gate_passed is False,
            "duplicate_submit_prevented": fake_client.submit_count == 1
            and duplicate.provider_operation_id == submitted.provider_operation_id,
            "materialization_safe": image_path.is_file()
            and materialization["part_path_remaining"] is False
            and GoogleGeminiImageAdapter._file_sha256(image_path)
            == materialization["sha256"],
            "transient_purged": transient_purged,
            "qc_gate_set_complete": {item.gate for item in post_qc.gate_results}
            == expected_qc_gates,
            "qc_passed": post_qc.verdict == "PASS"
            and all(item.verdict == "PASS" for item in post_qc.gate_results),
            "overlay_authority_bound": overlay_binding.native_overlay_plan_hash
            == overlay_plan.content_hash
            and overlay_binding.image_model_owns_final_text is False,
            "provenance_safe": provenance.generated_evidence_authority is False
            and provenance.provider_call_made is False
            and provenance.not_publishable is True,
            "manifest_safe": generation_manifest.media_kind == "STILL_IMAGE"
            and generation_manifest.production_eligible is False
            and generation_manifest.not_publishable is True,
            "no_fallback": receipt.external_provider_fallback_used is False
            and receipt.fallback_provider_key is None,
        }
        if not all(acceptance_checks.values()):
            failed = sorted(key for key, passed in acceptance_checks.items() if not passed)
            raise ValueError(f"IMG1_REHEARSAL_ACCEPTANCE_CHECK_FAILED:{','.join(failed)}")
        summary: dict[str, Any] = {
            "fixture_key": "img1-google-gemini-image-knowledge-silos",
            "scene_key": "knowledge_silos",
            "scene_id": requirements.scene_id,
            "niche_visual_source_profile": requirements.niche_visual_source_profile.value,
            "visual_source_route": decision.preferred_source_route.value,
            "transport": "LOCAL_FIXTURE_ONLY",
            "provider_key": "google_gemini_image",
            "provider_call_made": False,
            "attempts_consumed": 0,
            "generation_attempts_consumed": receipt.generation_attempts_consumed,
            "actual_cost": None,
            "actual_cost_usd": None,
            "estimated_cost": str(cost.estimated_amount),
            "estimated_cost_usd": str(cost.estimated_amount),
            "production_eligible": False,
            "not_publishable": True,
            "operation_status": receipt.provider_status,
            "normalized_operation_status": receipt.normalized_status,
            "duplicate_submit_prevented": (
                fake_client.submit_count == 1
                and duplicate.provider_operation_id == submitted.provider_operation_id
            ),
            "ai_image_eligibility_result": eligibility.result.value,
            "post_generation_qc_verdict": post_qc.verdict,
            "native_overlay_bound": overlay_binding.native_overlay_plan_hash == overlay_plan.content_hash,
            "normalization_planned_only": normalization.execution_allowed is False,
            "raw_url_persisted": False,
            "part_path_remaining": materialization["part_path_remaining"],
            "external_provider_fallback_used": receipt.external_provider_fallback_used,
            "provider_execution_enabled": False,
            "real_provider_success_claimed": False,
            "database_mutated": False,
            "archive_uploaded": False,
            "archive_upload_performed": False,
            "final_media_rendered": False,
            "final_media_ref_created": False,
            "human_upload_task_created": False,
            "provider_job_snapshot_submitted": False,
            "paid_provider_call_ledger_executed": False,
            "workspace_ref": f"workspace://{FIXTURE_DIRECTORY}",
            "workspace_path": str(rehearsal_root),
            "manifests_path": str(manifests_dir),
            "materialized_image_ref": FIXTURE_IMAGE_REF,
            "materialized_image_path": str(image_path),
            "evidence_manifest_files": sorted(evidence),
            "evidence_manifest_hashes": evidence_hashes,
            "acceptance_checks": acceptance_checks,
            "verdict": "PASS" if all(acceptance_checks.values()) else "FAIL",
        }

        for name, payload in evidence.items():
            self._write_json(self._safe_child(manifests_dir, name), payload)
        self._write_json(
            self._safe_child(manifests_dir, "img1_rehearsal_summary.json"),
            summary,
        )
        return summary

    def _validate_fixture_settings(self) -> None:
        if (
            self.settings.provider_real_execution_enabled
            or self.settings.provider_production_execution_enabled
            or not self.settings.media_provider_calls_disabled
            or self.settings.gemini_image_real_generation_enabled
            or not self.settings.img1_fixture_only
        ):
            raise ValueError("IMG1_REHEARSAL_REQUIRES_EXECUTION_DISABLED_FIXTURE_SETTINGS")
        if (
            self.settings.gemini_image_model_id != GEMINI_IMAGE_DEFAULT_MODEL_ID
            or self.settings.gemini_image_default_size != GEMINI_IMAGE_DEFAULT_SIZE
            or self.settings.gemini_image_default_aspect_ratio
            != GEMINI_IMAGE_DEFAULT_ASPECT_RATIO
            or self.settings.gemini_image_max_outputs != 1
            or self.settings.gemini_image_max_attempts_per_scene != 1
        ):
            raise ValueError("IMG1_REHEARSAL_REQUIRES_LOCKED_DEFAULT_IMAGE_ROUTE")

    @staticmethod
    def _requirements() -> SceneVisualRealizationRequirements:
        payload: dict[str, Any] = {
            "scene_id": FIXTURE_SCENE_ID,
            "semantic_intent": "Show knowledge silos as a custom editorial metaphor.",
            "target_duration_seconds": 6.0,
            "aspect_ratio": "16:9",
            "crop_safety_required": True,
            "previous_scene_summary": "A small team has accumulated disconnected knowledge.",
            "next_scene_summary": "Native labels explain how the silos reconnect.",
            "subject_action": "separated knowledge volumes remain visibly isolated",
            "camera_angle": "slightly elevated editorial view",
            "shot_size": "wide",
            "segment_ids": ["segment-knowledge-silos"],
            "niche_visual_source_profile": NicheVisualSourceProfile.STOCK_ASSISTED,
            "scene_class": "metaphor",
            "narrative_function": "conceptual_metaphor",
            "scene_meaning": "Knowledge remains isolated until a shared operating system reconnects it.",
            "editorial_intent": "Use a custom foundation image while native text owns the exact headline.",
            "filmability_score": 0.20,
            "stock_searchability_score": 0.20,
            "required_specificity": 0.70,
            "custom_composition_score": 0.90,
            "exact_text_dependency": 0.80,
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
            "crop_safety_requirement": "Reserve headline-safe negative space and protect 16:9 crops.",
            "previous_scene_intent_ref": None,
            "next_scene_intent_ref": None,
        }
        return SceneVisualRealizationRequirements(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _visual_direction() -> VisualDirectionContract:
        payload: dict[str, Any] = {
            "contract_version": "img1.visual-direction.v1",
            "channel_id": "small-team-ai",
            "project_id": "img1-fixture-project",
            "format_identity_ref": "format-identity://small-team-ai/editorial-explainer",
            "format_identity_hash": ai_image_stable_hash("small-team-ai-editorial-explainer"),
            "visual_strategy_profile_ref": "visual-strategy://small-team-ai/stock-assisted",
            "visual_strategy_profile_hash": ai_image_stable_hash("small-team-ai-stock-assisted"),
            "realism_level": "editorial photorealism with restrained abstraction",
            "treatment_mode": "architectural knowledge-system metaphor",
            "human_presence_policy": "NO_IDENTIFIABLE_PERSON",
            "environment_type": "abstract editorial workspace",
            "industry_context": "small-team knowledge operations",
            "time_of_day": "timeless studio setting",
            "lighting_direction": "soft directional side light",
            "lighting_temperature": "neutral-warm",
            "palette": ["deep slate", "muted teal", "warm amber"],
            "contrast": "medium-high subject separation",
            "saturation": "restrained",
            "camera_distance": "wide editorial composition",
            "lens_feel": "natural perspective",
            "camera_movement": "none for still foundation",
            "motion_intensity": "none",
            "framing_rule": "weighted right with headline-safe negative space on left",
            "depth_of_field_style": "moderate depth with readable silhouettes",
            "texture_grain": "subtle paper grain",
            "tone_mode": "calm, practical, credible",
            "prohibited_cliches": ["glowing brain", "floating dashboard", "corporate handshake"],
            "channel_identity_markers": ["editorial geometry", "restrained palette"],
            "adjacent_scene_constraints": ["preserve calm geometry and slate palette"],
        }
        return VisualDirectionContract(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _overlay_plan(decision: Any) -> NativeOverlayPlan:
        headline_ref = "content://img1/knowledge-silos/headline"
        exact_payload: dict[str, Any] = {
            "scene_id": FIXTURE_SCENE_ID,
            "source_decision_ref": FIXTURE_DECISION_REF,
            "source_decision_hash": decision.content_hash,
            "preferred_source_route": decision.preferred_source_route,
            "exact_text_required": True,
            "exact_number_required": False,
            "forbidden_generated_text": True,
            "forbidden_generated_logo": True,
            "forbidden_generated_fake_ui": True,
            "native_overlay_required": True,
            "authoritative_content_kinds": [AuthoritativeOverlayContentKind.HEADLINE],
            "authoritative_content_refs": [headline_ref],
        }
        exact = ExactTextNativeOverlayContract(
            **exact_payload,
            content_hash=ai_image_stable_hash(exact_payload),
        )
        text_safe = TextSafeRegion(
            id="knowledge-silos-headline-safe",
            x=0.06,
            y=0.12,
            width=0.40,
            height=0.24,
            purpose="Authoritative native headline",
            minimum_contrast_requirement=4.5,
            alignment="LEFT",
        )
        reserved = TextSafeRegion(
            id="knowledge-silos-caption-reserved",
            x=0.06,
            y=0.82,
            width=0.88,
            height=0.10,
            purpose="Native caption-safe reserve",
            minimum_contrast_requirement=4.5,
            alignment="CENTER",
        )
        payload: dict[str, Any] = {
            "plan_id": "native-overlay-plan://img1/knowledge-silos",
            "scene_id": FIXTURE_SCENE_ID,
            "source_decision_ref": FIXTURE_DECISION_REF,
            "source_decision_hash": decision.content_hash,
            "preferred_source_route": decision.preferred_source_route,
            "exact_text_contract": exact,
            "text_safe_regions": [text_safe],
            "reserved_overlay_regions": [reserved],
            "overlay_content_refs": [headline_ref],
            "native_overlay_required": True,
        }
        return NativeOverlayPlan(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _deterministic_receipt(receipt: GeminiImageOperationReceipt) -> GeminiImageOperationReceipt:
        payload = receipt.model_dump(mode="python", exclude={"state_hash"})
        payload["submitted_at"] = FIXTURE_TIMESTAMP
        payload["completed_at"] = FIXTURE_TIMESTAMP
        return GeminiImageOperationReceipt(
            **payload,
            state_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _provenance(
        *,
        request: Any,
        receipt: GeminiImageOperationReceipt,
        provider_request: GeminiImageGenerationRequest,
        materialization: dict[str, Any],
        cost_snapshot_ref: str,
        approval_ref: str,
    ) -> AIImageProvenanceManifest:
        payload: dict[str, Any] = {
            "provider": "google_gemini_image",
            "provider_model_id": provider_request.model_id,
            "prompt_hash": provider_request.prompt_hash,
            "reference_asset_refs": list(request.reference_asset_refs),
            "reference_asset_hashes": list(request.reference_asset_hashes),
            "generated_at": FIXTURE_TIMESTAMP,
            "output_reference": FIXTURE_IMAGE_REF,
            "output_checksum": materialization["sha256"],
            "image_width": materialization["image_width"],
            "image_height": materialization["image_height"],
            "image_format": materialization["image_format"],
            "cost_snapshot_ref": cost_snapshot_ref,
            "approval_ref": approval_ref,
            "scene_usage_refs": [f"scene://img1/{FIXTURE_SCENE_ID}"],
            "visual_source_decision_ref": request.visual_source_decision_ref,
            "visual_source_decision_hash": request.visual_source_decision_hash,
            "native_overlay_required": True,
            "post_generation_qc_refs": [FIXTURE_QC_REF],
            "synthetic_media_disclosure_ref": FIXTURE_DISCLOSURE_REF,
            "generated_evidence_authority": False,
            "provider_call_made": receipt.provider_call_made,
            "production_eligible": False,
            "not_publishable": True,
        }
        return AIImageProvenanceManifest(
            **payload,
            manifest_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _generation_manifest(
        *,
        request: Any,
        receipt: GeminiImageOperationReceipt,
        provider_request: GeminiImageGenerationRequest,
        materialization: dict[str, Any],
    ) -> AIGenerationManifest:
        relative_path = FIXTURE_IMAGE_RELATIVE_PATH.as_posix()
        payload: dict[str, Any] = {
            "media_kind": "STILL_IMAGE",
            "provider_key": "google_gemini_image",
            "provider_model_id": provider_request.model_id,
            "request_ref": request.request_id,
            "request_hash": request.request_hash,
            "generic_request_ref": request.request_id,
            "generic_request_hash": request.request_hash,
            "provider_request_id": receipt.provider_request_id,
            "provider_operation_id": receipt.provider_operation_id,
            "external_operation_id": receipt.provider_operation_id,
            "provider_status": "LOCAL_FIXTURE_MATERIALIZED",
            "prompt_hash": provider_request.prompt_hash,
            "submitted_at": FIXTURE_TIMESTAMP,
            "completed_at": FIXTURE_TIMESTAMP,
            "output_reference": receipt.output_reference,
            "output_url_reference": receipt.output_reference,
            "local_path": relative_path,
            "downloaded_path": relative_path,
            "size_bytes": materialization["size_bytes"],
            "sha256": materialization["sha256"],
            "downloaded_sha256": materialization["sha256"],
            "image_width": materialization["image_width"],
            "image_height": materialization["image_height"],
            "image_format": materialization["image_format"],
            "cost_snapshot_ref": request.cost_estimate_ref,
            "attempt_record_ref": FIXTURE_ATTEMPT_REF,
            "approval_ref": request.approval_ref,
            "idempotency_key": request.idempotency_key,
            "visual_source_decision_ref": request.visual_source_decision_ref,
            "visual_source_decision_hash": request.visual_source_decision_hash,
            "visual_direction_contract_ref": request.visual_direction_contract_ref,
            "visual_direction_contract_hash": request.visual_direction_contract_hash,
            "native_overlay_required": True,
            "native_overlay_plan_ref": request.native_overlay_plan_ref,
            "native_overlay_plan_hash": request.native_overlay_plan_hash,
            "text_safe_regions": list(request.text_safe_regions),
            "post_generation_qc_refs": [FIXTURE_QC_REF],
            "media_qc_ref": FIXTURE_QC_REF,
            "synthetic_media_disclosure_ref": FIXTURE_DISCLOSURE_REF,
            "production_eligible": False,
            "not_publishable": True,
        }
        return AIGenerationManifest(
            **payload,
            manifest_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _workspace_root(workspace_root: Path) -> Path:
        root = Path(workspace_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise ValueError("IMG1_REHEARSAL_WORKSPACE_ROOT_NOT_DIRECTORY")
        return root

    @staticmethod
    def _safe_child(root: Path, *parts: str) -> Path:
        candidate = root.joinpath(*parts).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("IMG1_REHEARSAL_PATH_ESCAPES_WORKSPACE") from exc
        return candidate

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.unlink(missing_ok=True)
        serialized = json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ": "),
        ) + "\n"
        try:
            with part.open("x", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(part, path)
        except Exception:
            part.unlink(missing_ok=True)
            raise

    @staticmethod
    def _assert_no_transient_output_persisted(
        evidence: dict[str, dict[str, Any]],
        fake_client: _LocalGeminiImageFixtureClient,
    ) -> None:
        serialized = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        if fake_client.raw_temporary_url in serialized or "fixture-secret-never-persist" in serialized:
            raise ValueError("IMG1_REHEARSAL_TRANSIENT_URL_PERSISTENCE_FORBIDDEN")


GoogleGeminiImageFixtureRehearsal = GoogleGeminiImageLocalFixtureRehearsal


__all__ = [
    "GoogleGeminiImageFixtureRehearsal",
    "GoogleGeminiImageLocalFixtureRehearsal",
]
