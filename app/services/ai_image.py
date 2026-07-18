from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.contracts.ai_image import (
    AIImageReferenceAsset,
    AIImageRequest,
    CompiledImagePrompt,
    GeneratedImageQCEvidence,
    ImageNormalizationManifest,
    ImageQCGateResult,
    NativeOverlayImageBinding,
    PostGenerationImageQCManifest,
    ai_image_stable_hash,
)
from app.contracts.native_renderer import NativeOverlayPlan
from app.contracts.visual_direction import VisualDirectionContract
from app.contracts.visual_routing import (
    SceneVisualRealizationRequirements,
    VisualDecisionStatus,
    VisualSourceDecision,
    VisualSourceRoute,
)


AI_IMAGE_PROVIDER_KEY = "google_gemini_image"
AI_IMAGE_ROUTES = {
    VisualSourceRoute.AI_GENERATED_IMAGE,
    VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
}
MANDATORY_IMAGE_NEGATIVE_CONSTRAINTS = [
    "no letters",
    "no numbers",
    "no logos",
    "no watermark",
    "no interface text",
    "no fake software UI",
]


class AIImageRequestBuilder:
    """Compile a provider-neutral request from one real VSR1 decision."""

    def build(
        self,
        *,
        requirements: SceneVisualRealizationRequirements,
        decision: VisualSourceDecision,
        decision_ref: str,
        visual_direction: VisualDirectionContract,
        visual_direction_ref: str,
        package_id: str,
        request_id: str,
        prompt_intent: str,
        custom_composition_reason: str,
        requested_image_size: str,
        cost_catalog_ref: str,
        cost_estimate_ref: str,
        approval_ref: str,
        approval_scope: str,
        idempotency_key: str,
        native_overlay_plan: NativeOverlayPlan | None = None,
        reference_assets: Sequence[AIImageReferenceAsset] = (),
        provider_route: str = AI_IMAGE_PROVIDER_KEY,
        provider_route_approved: bool = True,
        four_k_approval_ref: str | None = None,
    ) -> AIImageRequest:
        self._validate_hashed_artifact(requirements, "AI_IMAGE_REQUIREMENTS_HASH_MISMATCH")
        self._validate_hashed_artifact(
            visual_direction,
            "AI_IMAGE_VISUAL_DIRECTION_HASH_MISMATCH",
        )
        self._validate_decision(requirements, decision)
        if visual_direction.project_id.strip() == "":
            raise ValueError("AI_IMAGE_VISUAL_DIRECTION_PROJECT_REQUIRED")
        if decision.preferred_source_route == VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY:
            if native_overlay_plan is None:
                raise ValueError("AI_IMAGE_NATIVE_OVERLAY_PLAN_REQUIRED")
        elif native_overlay_plan is not None:
            raise ValueError("AI_IMAGE_UNEXPECTED_NATIVE_OVERLAY_PLAN")

        native_overlay_required = native_overlay_plan is not None
        if native_overlay_plan is not None:
            self._validate_hashed_artifact(
                native_overlay_plan,
                "AI_IMAGE_NATIVE_OVERLAY_PLAN_HASH_MISMATCH",
            )
            if native_overlay_plan.scene_id != requirements.scene_id:
                raise ValueError("AI_IMAGE_NATIVE_OVERLAY_SCENE_MISMATCH")
            if native_overlay_plan.source_decision_ref != decision_ref:
                raise ValueError("AI_IMAGE_NATIVE_OVERLAY_DECISION_REF_MISMATCH")
            if native_overlay_plan.source_decision_hash != decision.content_hash:
                raise ValueError("AI_IMAGE_NATIVE_OVERLAY_DECISION_HASH_MISMATCH")
            if native_overlay_plan.preferred_source_route != decision.preferred_source_route:
                raise ValueError("AI_IMAGE_NATIVE_OVERLAY_ROUTE_MISMATCH")

        exact_text_required = requirements.exact_text_dependency > 0.0
        exact_number_required = requirements.exact_number_dependency > 0.0
        reference_list = list(reference_assets)
        payload: dict[str, Any] = {
            "request_id": request_id,
            "project_id": visual_direction.project_id,
            "package_id": package_id,
            "channel_id": visual_direction.channel_id,
            "scene_id": requirements.scene_id,
            "source_segment_ids": list(requirements.segment_ids),
            "visual_source_decision_ref": decision_ref,
            "visual_source_decision_hash": decision.content_hash,
            "visual_source_route": decision.preferred_source_route,
            "visual_direction_contract_ref": visual_direction_ref,
            "visual_direction_contract_hash": visual_direction.content_hash,
            "scene_meaning": requirements.scene_meaning,
            "narrative_function": requirements.narrative_function,
            "prompt_intent": prompt_intent,
            "custom_composition_reason": custom_composition_reason,
            "aspect_ratio": requirements.target_aspect_ratio,
            "requested_image_size": requested_image_size,
            "minimum_effective_resolution": "1080p",
            "four_k_approval_ref": four_k_approval_ref,
            "reference_assets": reference_list,
            "reference_asset_refs": [item.asset_ref for item in reference_list],
            "reference_asset_hashes": [item.asset_hash for item in reference_list],
            "text_safe_regions": (
                list(native_overlay_plan.text_safe_regions)
                if native_overlay_plan is not None
                else []
            ),
            "reserved_overlay_regions": (
                list(native_overlay_plan.reserved_overlay_regions)
                if native_overlay_plan is not None
                else []
            ),
            "exact_text_required": exact_text_required,
            "exact_number_required": exact_number_required,
            "native_overlay_required": native_overlay_required,
            "native_overlay_plan_ref": (
                native_overlay_plan.plan_id if native_overlay_plan is not None else None
            ),
            "native_overlay_plan_hash": (
                native_overlay_plan.content_hash if native_overlay_plan is not None else None
            ),
            "forbidden_generated_text": True,
            "forbidden_generated_numbers": True,
            "forbidden_generated_logo": True,
            "forbidden_generated_fake_ui": True,
            "scene_truth_classification": self._truth_classification(requirements),
            "evidence_truth_requirement": requirements.evidence_truth_requirement,
            "product_specificity": requirements.product_specificity,
            "identity_likeness_policy": (
                "AUTHORIZED_REFERENCE_ONLY"
                if requirements.identity_consistency_requirement > 0.0
                else "NO_IDENTITY_OR_LIKENESS_AUTHORITY"
            ),
            "provider_route": provider_route,
            "provider_route_approved": provider_route_approved,
            "cost_catalog_ref": cost_catalog_ref,
            "cost_estimate_ref": cost_estimate_ref,
            "approval_ref": approval_ref,
            "approval_scope": approval_scope,
            "idempotency_key": idempotency_key,
            "production_eligible": False,
            "not_publishable": True,
        }
        return AIImageRequest(**payload, request_hash=ai_image_stable_hash(payload))

    @staticmethod
    def _validate_decision(
        requirements: SceneVisualRealizationRequirements,
        decision: VisualSourceDecision,
    ) -> None:
        if decision.scene_id != requirements.scene_id:
            raise ValueError("AI_IMAGE_VISUAL_SOURCE_DECISION_SCENE_MISMATCH")
        if decision.content_hash != ai_image_stable_hash(
            decision.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("AI_IMAGE_VISUAL_SOURCE_DECISION_HASH_MISMATCH")
        if decision.input_feature_snapshot.get("requirements_hash") != requirements.content_hash:
            raise ValueError("AI_IMAGE_VISUAL_SOURCE_DECISION_INPUT_MISMATCH")
        if decision.preferred_source_route not in AI_IMAGE_ROUTES:
            raise ValueError("AI_IMAGE_VISUAL_SOURCE_DECISION_ROUTE_INVALID")
        if decision.decision_status not in {
            VisualDecisionStatus.PLANNED,
            VisualDecisionStatus.REVIEW_REQUIRED,
        }:
            raise ValueError("AI_IMAGE_VISUAL_SOURCE_DECISION_STATUS_INVALID")
        if decision.provider_execution_allowed:
            raise ValueError("AI_IMAGE_VSR1_DECISION_MUST_REMAIN_EXECUTION_DISABLED")

    @staticmethod
    def _validate_hashed_artifact(artifact: Any, error_code: str) -> None:
        if artifact.content_hash != ai_image_stable_hash(
            artifact.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError(error_code)

    @staticmethod
    def _truth_classification(
        requirements: SceneVisualRealizationRequirements,
    ) -> str:
        normalized = requirements.scene_class.strip().lower().replace("-", "_").replace(" ", "_")
        if requirements.evidence_truth_requirement >= 0.5 or normalized == "evidence":
            return "EVIDENCE"
        if normalized in {"actual_ui", "ui", "screenshot"}:
            return "ACTUAL_UI"
        if normalized in {"actual_product", "product"}:
            return "ACTUAL_PRODUCT"
        if normalized in {"actual_document", "document"}:
            return "ACTUAL_DOCUMENT"
        return "NO_EVIDENCE_TRUTH"


class ImagePromptCompiler:
    compiler_version = "img1.image-prompt-compiler.v1"

    def compile(
        self,
        *,
        requirements: SceneVisualRealizationRequirements,
        visual_direction: VisualDirectionContract,
        decision: VisualSourceDecision,
        request: AIImageRequest,
        continuity_hints: Sequence[str] = (),
    ) -> CompiledImagePrompt:
        AIImageRequestBuilder._validate_hashed_artifact(
            requirements,
            "AI_IMAGE_PROMPT_REQUIREMENTS_HASH_MISMATCH",
        )
        AIImageRequestBuilder._validate_hashed_artifact(
            visual_direction,
            "AI_IMAGE_PROMPT_VISUAL_DIRECTION_HASH_MISMATCH",
        )
        AIImageRequestBuilder._validate_hashed_artifact(
            decision,
            "AI_IMAGE_PROMPT_DECISION_HASH_MISMATCH",
        )
        if request.request_hash != ai_image_stable_hash(
            request.model_dump(mode="json", exclude={"request_hash"})
        ):
            raise ValueError("AI_IMAGE_PROMPT_REQUEST_HASH_MISMATCH")
        if request.scene_id != requirements.scene_id or decision.scene_id != requirements.scene_id:
            raise ValueError("AI_IMAGE_PROMPT_SCENE_BINDING_MISMATCH")
        if request.visual_source_decision_hash != decision.content_hash:
            raise ValueError("AI_IMAGE_PROMPT_DECISION_BINDING_MISMATCH")
        if request.visual_direction_contract_hash != visual_direction.content_hash:
            raise ValueError("AI_IMAGE_PROMPT_DIRECTION_BINDING_MISMATCH")

        subject = request.prompt_intent
        environment = (
            f"{visual_direction.environment_type}; {visual_direction.industry_context}; "
            f"{visual_direction.time_of_day}"
        )
        composition = (
            f"Editorial composition for {requirements.narrative_function}; "
            f"communicate: {requirements.scene_meaning}"
        )
        realism = f"{visual_direction.realism_level}; {visual_direction.treatment_mode}; {visual_direction.texture_grain}"
        lighting = f"{visual_direction.lighting_direction}; {visual_direction.lighting_temperature}"
        palette = ", ".join(visual_direction.palette)
        framing = (
            f"{visual_direction.camera_distance}; {visual_direction.lens_feel}; "
            f"{visual_direction.framing_rule}; {requirements.camera_angle}"
        )
        depth = visual_direction.depth_of_field_style
        negative_space = "Keep a balanced editorial frame with crop-safe negative space."
        if request.native_overlay_required:
            regions = ", ".join(
                f"{region.id}({region.x:.3f},{region.y:.3f},{region.width:.3f},{region.height:.3f})"
                for region in request.text_safe_regions
            )
            negative_space = (
                "Reserve clean negative space for native overlay; avoid visual clutter in the safe region; "
                f"preserve contrast behind overlay zones: {regions}."
            )

        stable_continuity = list(
            dict.fromkeys(
                item.strip()
                for item in [
                    requirements.previous_scene_summary or "",
                    requirements.next_scene_summary or "",
                    *continuity_hints,
                    *visual_direction.adjacent_scene_constraints,
                ]
                if item and item.strip()
            )
        )
        negatives = list(
            dict.fromkeys(
                [
                    *MANDATORY_IMAGE_NEGATIVE_CONSTRAINTS,
                    *[f"avoid {item}" for item in visual_direction.prohibited_cliches],
                ]
            )
        )
        prompt_sections = [
            f"Subject and visual concept: {subject}",
            f"Environment and context: {environment}",
            f"Editorial composition: {composition}",
            f"Realism and treatment: {realism}",
            f"Lighting: {lighting}",
            f"Palette: {palette}",
            f"Camera and framing: {framing}",
            f"Depth and focal behavior: {depth}",
            f"Negative-space requirement: {negative_space}",
            f"Continuity hints: {'; '.join(stable_continuity) if stable_continuity else 'none'}",
            f"Negative constraints: {'; '.join(negatives)}",
        ]
        prompt = "\n".join(prompt_sections)
        payload: dict[str, Any] = {
            "compiler_version": self.compiler_version,
            "scene_id": requirements.scene_id,
            "visual_source_decision_ref": request.visual_source_decision_ref,
            "visual_source_decision_hash": decision.content_hash,
            "generic_request_ref": request.request_id,
            "generic_request_hash": request.request_hash,
            "visual_direction_contract_ref": request.visual_direction_contract_ref,
            "visual_direction_contract_hash": visual_direction.content_hash,
            "subject_and_visual_concept": subject,
            "environment_context": environment,
            "editorial_composition": composition,
            "realism_treatment": realism,
            "lighting": lighting,
            "palette": palette,
            "camera_framing": framing,
            "depth_focal_behavior": depth,
            "negative_space_requirement": negative_space,
            "continuity_hints": stable_continuity,
            "negative_constraints": negatives,
            "prompt": prompt,
            "prompt_hash": ai_image_stable_hash(prompt),
            "provider_call_made": False,
        }
        return CompiledImagePrompt(**payload, content_hash=ai_image_stable_hash(payload))


class PostGenerationImageQC:
    """IMG1 deterministic fixture contract; VQC1 may replace detector calibration."""

    def evaluate(self, evidence: GeneratedImageQCEvidence) -> PostGenerationImageQCManifest:
        generated_reasons: list[str] = []
        if evidence.generated_letters_detected:
            generated_reasons.append("GENERATED_TEXT_ARTIFACT")
        if evidence.generated_numbers_detected:
            generated_reasons.append("GENERATED_NUMBER_ARTIFACT")
        generated_verdict = self._artifact_verdict(
            bool(generated_reasons), evidence.artifact_repairable_by_native_overlay
        )

        fake_reasons: list[str] = []
        if evidence.fake_ui_detected:
            fake_reasons.append("FAKE_UI_RISK")
        if evidence.logo_or_trademark_detected:
            fake_reasons.append("LOGO_OR_TRADEMARK_RISK")
        if evidence.watermark_detected:
            fake_reasons.append("WATERMARK_RISK")

        results = [
            self._result("GeneratedTextArtifactGate", generated_verdict, generated_reasons, evidence),
            self._result("FakeUILogoGate", "BLOCK" if fake_reasons else "PASS", fake_reasons, evidence),
            self._score_result("CompositionComplianceGate", evidence.composition_compliance_score),
            self._score_result("SemanticMatchGate", evidence.semantic_match_score),
            self._score_result("VisualLanguageMatchGate", evidence.visual_language_match_score),
            self._score_result("TechnicalImageFitnessGate", evidence.technical_image_fitness_score, pass_min=0.90, review_min=0.80),
            self._score_result("CropSafetyGate", evidence.crop_safety_score),
            self._reuse_result(evidence.reuse_similarity_score),
            self._result(
                "RightsDisclosureCompletenessGate",
                "PASS" if evidence.rights_disclosure_complete else "BLOCK",
                [] if evidence.rights_disclosure_complete else ["RIGHTS_DISCLOSURE_INCOMPLETE"],
                evidence,
            ),
        ]
        verdicts = {item.verdict for item in results}
        verdict = "BLOCK" if "BLOCK" in verdicts else "REVIEW_REQUIRED" if "REVIEW_REQUIRED" in verdicts else "PASS"
        reasons = list(dict.fromkeys(code for item in results for code in item.reason_codes))
        payload = {
            "image_ref": evidence.image_ref,
            "image_hash": evidence.image_hash,
            "gate_results": results,
            "verdict": verdict,
            "reason_codes": reasons,
            "fixture_only": True,
            "production_eligible": False,
        }
        return PostGenerationImageQCManifest(
            **payload,
            manifest_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _artifact_verdict(detected: bool, repairable: bool) -> str:
        if not detected:
            return "PASS"
        return "REVIEW_REQUIRED" if repairable else "BLOCK"

    @staticmethod
    def _result(
        gate: str,
        verdict: str,
        reason_codes: list[str],
        evidence: GeneratedImageQCEvidence,
    ) -> ImageQCGateResult:
        return ImageQCGateResult(
            gate=gate,
            verdict=verdict,
            reason_codes=reason_codes,
            representative_crop_refs=evidence.representative_crop_refs,
            detected_region_boxes=evidence.detected_region_boxes,
            repairability=(
                "NATIVE_OVERLAY_REPAIR"
                if verdict == "REVIEW_REQUIRED" and evidence.artifact_repairable_by_native_overlay
                else "NOT_REPAIRABLE"
                if verdict == "BLOCK"
                else "NOT_REQUIRED"
            ),
        )

    def _score_result(
        self,
        gate: str,
        score: float,
        *,
        pass_min: float = 0.80,
        review_min: float = 0.65,
    ) -> ImageQCGateResult:
        if score >= pass_min:
            verdict, reasons = "PASS", []
        elif score >= review_min:
            verdict, reasons = "REVIEW_REQUIRED", [f"{gate.upper()}_REVIEW_REQUIRED"]
        else:
            verdict, reasons = "BLOCK", [f"{gate.upper()}_BELOW_MINIMUM"]
        return ImageQCGateResult(
            gate=gate,
            verdict=verdict,
            reason_codes=reasons,
            representative_crop_refs=[],
            detected_region_boxes=[],
            repairability="NOT_REQUIRED" if verdict == "PASS" else "NOT_REPAIRABLE",
        )

    @staticmethod
    def _reuse_result(score: float) -> ImageQCGateResult:
        if score <= 0.80:
            verdict, reasons = "PASS", []
        elif score <= 0.90:
            verdict, reasons = "REVIEW_REQUIRED", ["REUSE_SIMILARITY_REVIEW_REQUIRED"]
        else:
            verdict, reasons = "BLOCK", ["REUSE_SIMILARITY_TOO_HIGH"]
        return ImageQCGateResult(
            gate="ReuseSimilarityGate",
            verdict=verdict,
            reason_codes=reasons,
            representative_crop_refs=[],
            detected_region_boxes=[],
            repairability="NOT_REQUIRED" if verdict == "PASS" else "NOT_REPAIRABLE",
        )


class ImageNormalizationPlanner:
    _RATIOS = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}

    def plan(
        self,
        *,
        source_ref: str,
        source_checksum: str,
        source_width: int,
        source_height: int,
        target_aspect_ratio: str,
        image_format: str,
        color_profile: str = "sRGB",
    ) -> ImageNormalizationManifest:
        ratio = self._RATIOS[target_aspect_ratio]
        source_ratio = source_width / source_height
        if source_ratio > ratio:
            crop_height = source_height
            crop_width = int(source_height * ratio)
            x = (source_width - crop_width) // 2
            y = 0
        else:
            crop_width = source_width
            crop_height = int(source_width / ratio)
            x = 0
            y = (source_height - crop_height) // 2
        crop_plan = {
            "mode": "CENTER_CROP",
            "x": x,
            "y": y,
            "width": crop_width,
            "height": crop_height,
        }
        normalized_format = image_format.upper()
        payload: dict[str, Any] = {
            "source_ref": source_ref,
            "source_checksum": source_checksum,
            "source_width": source_width,
            "source_height": source_height,
            "target_width": crop_width,
            "target_height": crop_height,
            "target_aspect_ratio": target_aspect_ratio,
            "crop_plan": crop_plan,
            "effective_width_after_crop": crop_width,
            "effective_height_after_crop": crop_height,
            "color_profile": color_profile,
            "source_format": normalized_format,
            "target_format": normalized_format,
            "format_conversion": "NONE",
            "sharpness_upscale_warning": None,
            "upscale_applied": False,
            "minimum_effective_resolution": "1080p",
            "checksum": source_checksum,
            "execution_allowed": False,
        }
        return ImageNormalizationManifest(
            **payload,
            manifest_hash=ai_image_stable_hash(payload),
        )


class NativeOverlayImageBindingBuilder:
    _KIND_MAP = {
        "HEADLINE": "HEADLINE",
        "NUMBER": "NUMBER",
        "PERCENTAGE": "NUMBER",
        "DATA_VALUE": "NUMBER",
        "WORKFLOW_LABEL": "WORKFLOW_NODE",
        "TOOL_NAME": "TOOL_NAME",
        "PRODUCT_NAME": "PRODUCT_NAME",
        "QUOTE": "LABEL",
        "CITATION": "CITATION",
        "CTA": "CTA",
        "UI_TEXT": "LABEL",
    }

    def build(
        self,
        *,
        request: AIImageRequest,
        overlay_plan: NativeOverlayPlan,
        generated_image_ref: str,
        generated_image_hash: str,
    ) -> NativeOverlayImageBinding:
        if not request.native_overlay_required:
            raise ValueError("AI_IMAGE_NATIVE_OVERLAY_BINDING_NOT_REQUIRED")
        if overlay_plan.content_hash != ai_image_stable_hash(
            overlay_plan.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("AI_IMAGE_NATIVE_OVERLAY_PLAN_HASH_MISMATCH")
        if request.native_overlay_plan_ref != overlay_plan.plan_id:
            raise ValueError("AI_IMAGE_NATIVE_OVERLAY_BINDING_PLAN_REF_MISMATCH")
        if request.native_overlay_plan_hash != overlay_plan.content_hash:
            raise ValueError("AI_IMAGE_NATIVE_OVERLAY_BINDING_PLAN_MISMATCH")
        if (
            request.scene_id != overlay_plan.scene_id
            or request.visual_source_decision_ref != overlay_plan.source_decision_ref
            or request.visual_source_decision_hash != overlay_plan.source_decision_hash
        ):
            raise ValueError("AI_IMAGE_NATIVE_OVERLAY_BINDING_SOURCE_MISMATCH")
        if (
            list(request.text_safe_regions) != list(overlay_plan.text_safe_regions)
            or list(request.reserved_overlay_regions)
            != list(overlay_plan.reserved_overlay_regions)
        ):
            raise ValueError("AI_IMAGE_NATIVE_OVERLAY_BINDING_REGION_MISMATCH")
        kinds = list(
            dict.fromkeys(
                self._KIND_MAP[item.value]
                for item in overlay_plan.exact_text_contract.authoritative_content_kinds
            )
        )
        payload = {
            "visual_source_decision_ref": request.visual_source_decision_ref,
            "visual_source_decision_hash": request.visual_source_decision_hash,
            "generated_image_ref": generated_image_ref,
            "generated_image_hash": generated_image_hash,
            "text_safe_regions": list(request.text_safe_regions),
            "reserved_overlay_regions": list(request.reserved_overlay_regions),
            "native_overlay_plan_ref": overlay_plan.plan_id,
            "native_overlay_plan_hash": overlay_plan.content_hash,
            "authoritative_content_kinds": kinds,
            "image_model_owns_final_text": False,
            "production_eligible": False,
        }
        return NativeOverlayImageBinding(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )


__all__ = [
    "AI_IMAGE_PROVIDER_KEY",
    "AIImageRequestBuilder",
    "ImageNormalizationPlanner",
    "ImagePromptCompiler",
    "MANDATORY_IMAGE_NEGATIVE_CONSTRAINTS",
    "NativeOverlayImageBindingBuilder",
    "PostGenerationImageQC",
]
