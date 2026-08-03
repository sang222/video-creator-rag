from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from app.contracts.ai_image import AIImageRequest, CompiledImagePrompt, ai_image_stable_hash
from app.contracts.asset_acquisition import (
    DriveArchiveReceipt,
    ProductionArchiveManifest,
)
from app.contracts.google_gemini_image import (
    GeminiImageCostEstimateSnapshot,
    GeminiImageExecutionGates,
    GeminiImageGenerationRequest,
    validate_gemini_image_cost_snapshot_integrity,
)
from app.contracts.img_canary import (
    IMG_CANARY_ASPECT_RATIO,
    IMG_CANARY_HARD_CAP_USD,
    IMG_CANARY_IMAGE_SIZE,
    IMG_CANARY_MODEL,
    IMG_CANARY_REVIEW_CHECKLIST,
    IMGCanaryAttemptLedger,
    IMGCanaryHumanReviewPacket,
    IMGCanaryDriveReadinessEvidence,
    IMGCanaryMonthlyBudgetEvidence,
    IMGCanaryNativeHeadlineArtifact,
    IMGCanaryPreflightEvidence,
    IMGCanaryV3SerializedRequestEvidence,
    IMGCanarySerializedRequestEvidence,
    IMGCanaryV2ApprovalBinding,
    IMGCanaryV3ApprovalBinding,
    IMGCanaryRunIdentity,
    IMGCanaryScopedApproval,
)
from app.contracts.img_canary_security import (
    IMG_CANARY_V1_AUTHORIZATION_REF,
    IMG_CANARY_V1_TASK_KEY,
    IMG_CANARY_V3_AUTHORIZATION_REF,
    IMG_CANARY_V3_TASK_KEY,
    img_canary_task_authority_identity,
    IMGCanaryBudgetReservationEvidence,
    IMGCanaryCredentialRotationEvidence,
    IMGCanaryTaskAuthorizationLedger,
)
from app.contracts.image_visual_quality_control import ImageVisualQualityControlReport
from app.contracts.native_renderer import (
    AssetRequirement,
    CanvasSpec,
    CompiledNativeRenderManifest,
    NativeOverlayPlan,
    NativeRenderPlan,
    NativeRenderScene,
    ResolvedAssetRef,
    TextSafeRegion,
    NativeRenderExecutionReceipt,
)
from app.contracts.visual_direction import VisualDirectionContract
from app.contracts.visual_routing import (
    AuthoritativeOverlayContentKind,
    ExactTextNativeOverlayContract,
    NicheVisualSourceProfile,
    SceneVisualRealizationRequirements,
    VisualSourceDecision,
    VisualSourceRoute,
)
from app.core.config import Settings
from app.providers.google_gemini_image import GoogleGeminiImageAdapter
from app.services.ai_image import AIImageRequestBuilder, ImagePromptCompiler
from app.services.google_gemini_image_catalog import GoogleGeminiImageModelPriceCatalog
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import canonical_plan_hash, stable_hash
from app.services.native_ffmpeg_renderer import FFMPEG_FULL_DEFAULT
from app.services.production_archive import (
    IMG_CANARY_REQUIRED_ARCHIVE_ROLES,
    IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES,
    IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES,
)
from app.services.provider_stack import CANONICAL_PROVIDER_KEYS
from app.services.visual_source_routing import VisualSourceRouter


IMG_CANARY_HEADLINE = "Information is everywhere. Context is nowhere."
IMG_CANARY_SCENE_ID = "scene-fragmented-information"
IMG_CANARY_DECISION_REF_PREFIX = "visual-source-decision://img-canary"
IMG_CANARY_DIRECTION_REF_PREFIX = "visual-direction://img-canary"
IMG_CANARY_MASTER_TASK_KEY = IMG_CANARY_V1_TASK_KEY
IMG_CANARY_MASTER_AUTHORIZATION_REF = IMG_CANARY_V1_AUTHORIZATION_REF
IMG_CANARY_CREDENTIAL_INCIDENT_REF = (
    "incident://img-canary/exposed-credential/2026-07-18"
)


def _fsync_parent_directory(path: Path) -> None:
    """Make an atomic rename durable across a power loss."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class IMGCanaryPlanBundle:
    run_identity: IMGCanaryRunIdentity
    requirements: SceneVisualRealizationRequirements
    decision: VisualSourceDecision
    visual_direction: VisualDirectionContract
    headline: IMGCanaryNativeHeadlineArtifact
    overlay_plan: NativeOverlayPlan
    cost: GeminiImageCostEstimateSnapshot
    generic_request: AIImageRequest
    compiled_prompt: CompiledImagePrompt
    provider_request: GeminiImageGenerationRequest
    approval: IMGCanaryScopedApproval
    execution_gates: GeminiImageExecutionGates
    serialized_request_evidence: (
        IMGCanarySerializedRequestEvidence
        | IMGCanaryV3SerializedRequestEvidence
        | None
    ) = None
    v2_approval_binding: IMGCanaryV2ApprovalBinding | None = None
    v3_approval_binding: IMGCanaryV3ApprovalBinding | None = None


class IMGCanaryPlanBuilder:
    """Build the immutable one-shot canary request without executing a provider."""

    def __init__(self, settings: Settings, *, approval_version: str = "v1"):
        self.settings = settings
        if approval_version not in {"v1", "v2", "v3"}:
            raise ValueError("IMG_CANARY_APPROVAL_VERSION_INVALID")
        self.approval_version = approval_version

    def build(
        self,
        *,
        now: datetime | None = None,
        run_suffix: str | None = None,
        previous_run_evidence_hash: str | None = None,
        previous_runs_evidence_hash: str | None = None,
    ) -> IMGCanaryPlanBundle:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
        suffix = run_suffix or uuid.uuid4().hex[:8]
        run_prefix = {
            "v1": "img-canary",
            "v2": "img-canary-v2",
            "v3": "img-canary-v3",
        }[self.approval_version]
        run_id = f"{run_prefix}-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{suffix}"
        project_id = f"{run_id}-project"
        package_id = f"{run_id}-package"
        canary_id = f"{run_id}-candidate"
        identity_payload: dict[str, Any] = {
            "run_id": run_id,
            "run_type": "IMG_CANARY",
            "project_id": project_id,
            "package_id": package_id,
            "canary_id": canary_id,
            "channel_key": "small-team-ai",
            "niche_visual_source_profile": "STOCK_ASSISTED",
            "production_eligible": False,
            "not_publishable": True,
            "created_at": timestamp,
        }
        identity = IMGCanaryRunIdentity(
            **identity_payload,
            content_hash=ai_image_stable_hash(identity_payload),
        )

        requirements = self._requirements()
        decision = VisualSourceRouter().route(
            requirements,
            rights_policy_allows_generation=True,
        )
        if decision.preferred_source_route != VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY:
            raise ValueError("IMG_CANARY_VISUAL_SOURCE_ROUTE_MISMATCH")
        decision_ref = f"{IMG_CANARY_DECISION_REF_PREFIX}/{run_id}"
        visual_direction_ref = f"{IMG_CANARY_DIRECTION_REF_PREFIX}/{run_id}"
        direction = self._visual_direction(project_id=project_id)
        headline_ref = f"content://img-canary/{run_id}/headline"
        headline_payload = {
            "artifact_ref": headline_ref,
            "run_id": run_id,
            "scene_id": IMG_CANARY_SCENE_ID,
            "content_kind": "HEADLINE",
            "exact_text": IMG_CANARY_HEADLINE,
            "authority": "NATIVE_OVERLAY",
            "generated_pixel_authority": False,
        }
        headline = IMGCanaryNativeHeadlineArtifact(
            **headline_payload,
            content_hash=ai_image_stable_hash(headline_payload),
        )
        overlay = self._overlay_plan(
            run_id=run_id,
            decision=decision,
            decision_ref=decision_ref,
            headline_ref=headline_ref,
        )

        catalog = GoogleGeminiImageModelPriceCatalog()
        cost = catalog.estimate(
            model_id=IMG_CANARY_MODEL,
            image_size=IMG_CANARY_IMAGE_SIZE,
            aspect_ratio=IMG_CANARY_ASPECT_RATIO,
            output_count=1,
            attempt_count=1,
            hard_cap=IMG_CANARY_HARD_CAP_USD,
            approval_amount=IMG_CANARY_HARD_CAP_USD,
        )
        approval_ref = f"approval://img-canary/{run_id}/one-paid-request"
        approval_scope = f"IMG_CANARY_ONE_SHOT:{run_id}"
        idempotency_key = "provider-idem:" + ai_image_stable_hash(
            {
                "run_id": run_id,
                "provider": "google_gemini_image",
                "model": IMG_CANARY_MODEL,
                "scene_id": requirements.scene_id,
                "decision_hash": decision.content_hash,
                "overlay_hash": overlay.content_hash,
                "approval_scope": approval_scope,
            }
        )
        generic_request = AIImageRequestBuilder().build(
            requirements=requirements,
            decision=decision,
            decision_ref=decision_ref,
            visual_direction=direction,
            visual_direction_ref=visual_direction_ref,
            package_id=package_id,
            request_id=f"ai-image-request://img-canary/{run_id}",
            prompt_intent=(
                "A clean professional editorial illustration of fragmented islands or separated "
                "clusters of documents and message-like shapes, clearly conveying disconnected "
                "knowledge with one coherent focal composition and large clean negative space."
            ),
            custom_composition_reason=(
                "The disconnected-knowledge metaphor requires authored composition; native overlay "
                "owns the exact headline and the generated foundation owns no text or UI."
            ),
            requested_image_size=IMG_CANARY_IMAGE_SIZE,
            cost_catalog_ref=catalog.ref,
            cost_estimate_ref=cost.snapshot_hash,
            approval_ref=approval_ref,
            approval_scope=approval_scope,
            idempotency_key=idempotency_key,
            native_overlay_plan=overlay,
            reference_assets=(),
        )
        compiled_prompt = ImagePromptCompiler().compile(
            requirements=requirements,
            visual_direction=direction,
            decision=decision,
            request=generic_request,
            continuity_hints=(
                "Small Team AI editorial explainer language; restrained slate, teal and warm amber palette.",
                "Isolated one-scene canary; do not infer multi-scene continuity.",
            ),
        )
        if IMG_CANARY_HEADLINE in compiled_prompt.prompt:
            raise ValueError("IMG_CANARY_NATIVE_HEADLINE_LEAKED_INTO_GENERATION_PROMPT")
        adapter = GoogleGeminiImageAdapter(self.settings)
        provider_request = adapter.build_request(generic_request, compiled_prompt)
        approval_payload: dict[str, Any] = {
            "approval_ref": approval_ref,
            "run_id": run_id,
            "project_id": project_id,
            "package_id": package_id,
            "canary_id": canary_id,
            "provider": "google_gemini_image",
            "model": IMG_CANARY_MODEL,
            "image_size": IMG_CANARY_IMAGE_SIZE,
            "aspect_ratio": IMG_CANARY_ASPECT_RATIO,
            "output_count": 1,
            "request_hash": provider_request.content_hash,
            "prompt_hash": provider_request.prompt_hash,
            "visual_source_decision_hash": decision.content_hash,
            "base_decision_status": decision.decision_status,
            "base_provider_execution_allowed": decision.provider_execution_allowed,
            "scoped_provider_boundary_authorized": True,
            "catalog_ref": catalog.ref,
            "estimated_cost_usd": cost.estimated_amount,
            "hard_cap_usd": IMG_CANARY_HARD_CAP_USD,
            "attempt_limit": 1,
            "reference_image_count": 0,
            "grounding_enabled": False,
            "search_grounding_enabled": False,
            "authorized_at": timestamp,
            "expires_at": timestamp + timedelta(hours=6),
            "operator_authorization_source": (
                "CODEX_OPERATOR_MESSAGE"
                if self.approval_version == "v3"
                else "ATTACHED_MASTER_PROMPT"
            ),
            "external_fallback_allowed": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        approval = IMGCanaryScopedApproval(
            **approval_payload,
            content_hash=ai_image_stable_hash(approval_payload),
        )
        serialized_request_evidence: (
            IMGCanarySerializedRequestEvidence
            | IMGCanaryV3SerializedRequestEvidence
            | None
        ) = None
        v2_approval_binding: IMGCanaryV2ApprovalBinding | None = None
        v3_approval_binding: IMGCanaryV3ApprovalBinding | None = None
        if self.approval_version in {"v2", "v3"}:
            historical_evidence_hash = (
                previous_run_evidence_hash
                if self.approval_version == "v2"
                else previous_runs_evidence_hash
            )
            if not historical_evidence_hash:
                raise ValueError(
                    f"IMG_CANARY_{self.approval_version.upper()}_PREVIOUS_RUNS_EVIDENCE_REQUIRED"
                )
            captured = adapter.capture_official_sdk_serialization(provider_request)
            raw_body = captured.get("body")
            raw_body_hash = captured.get("body_sha256")
            if not isinstance(raw_body, dict) or not isinstance(raw_body_hash, str):
                raise RuntimeError(
                    f"IMG_CANARY_{self.approval_version.upper()}_SERIALIZED_BODY_CAPTURE_INVALID"
                )
            redacted_body = dict(raw_body)
            redacted_body["input"] = f"sha256://prompt/{provider_request.prompt_hash}"
            serialized_payload: dict[str, Any] = {
                "schema_version": (
                    "img-canary-serialized-request/v2"
                    if self.approval_version == "v2"
                    else "img-canary-serialized-request/v3"
                ),
                "run_id": run_id,
                "request_hash": provider_request.content_hash,
                "prompt_hash": provider_request.prompt_hash,
                "transport": "OFFICIAL_SDK_HTTPX_MOCK_TRANSPORT",
                "endpoint_path": captured.get("path"),
                "http_method": captured.get("method"),
                "redacted_request_body": redacted_body,
                "serialized_body_hash": raw_body_hash,
                "redacted_body_hash": ai_image_stable_hash(redacted_body),
                "response_format_hash": ai_image_stable_hash(
                    raw_body["response_format"]
                ),
                "sdk_retry_attempts": 1,
                "sdk_retries_disabled": True,
                "api_key_persisted": False,
                "authorization_headers_persisted": False,
                "captured_at": timestamp,
            }
            evidence_type = (
                IMGCanarySerializedRequestEvidence
                if self.approval_version == "v2"
                else IMGCanaryV3SerializedRequestEvidence
            )
            serialized_request_evidence = evidence_type(
                **serialized_payload,
                content_hash=ai_image_stable_hash(serialized_payload),
            )
            common_binding_payload: dict[str, Any] = {
                "base_approval_hash": approval.content_hash,
                "request_hash": provider_request.content_hash,
                "prompt_hash": provider_request.prompt_hash,
                "serialized_request_evidence_hash": (
                    serialized_request_evidence.content_hash
                ),
                "serialized_body_hash": (
                    serialized_request_evidence.serialized_body_hash
                ),
                "provider": "google_gemini_image",
                "model": IMG_CANARY_MODEL,
                "image_size": IMG_CANARY_IMAGE_SIZE,
                "aspect_ratio": IMG_CANARY_ASPECT_RATIO,
                "output_count": 1,
                "estimated_cost_usd": cost.estimated_amount,
                "hard_cap_usd": IMG_CANARY_HARD_CAP_USD,
                "attempt_limit": 1,
                "external_fallback_allowed": False,
                "production_eligible": False,
                "not_publishable": True,
                "authorized_at": timestamp,
            }
            if self.approval_version == "v2":
                binding_payload: dict[str, Any] = {
                    "schema_version": "img-canary-v2-approval-binding/v1",
                    "approval_source_ref": (
                        "attachment://d6de1eab-f9bd-44fe-ab23-4bf7e05ce167"
                    ),
                    "approval_source_sha256": (
                        "6261dfc83261e6470d6a1e0755e827880e57261c8791851b20812267b84e3319"
                    ),
                    "run_id": run_id,
                    "task_key": (
                        "img-canary-v2-approval-d6de1eab-f9bd-44fe-ab23-4bf7e05ce167"
                    ),
                    "task_authorization_ref": (
                        "authorization://img-canary/v2/"
                        "d6de1eab-f9bd-44fe-ab23-4bf7e05ce167/one-paid-request"
                    ),
                    **common_binding_payload,
                    "previous_run_evidence_hash": historical_evidence_hash,
                }
                v2_approval_binding = IMGCanaryV2ApprovalBinding(
                    **binding_payload,
                    content_hash=ai_image_stable_hash(binding_payload),
                )
            else:
                binding_payload = {
                    "schema_version": "img-canary-v3-approval-binding/v1",
                    "approval_source_ref": (
                        "operator-message://codex-thread/2026-07-18/fix-and-rerun"
                    ),
                    "approval_source_sha256": (
                        "3c895af877e10f7faa7db9fd2ad92752cb43305c13ce7d078cb1adfa077e9ada"
                    ),
                    "approval_id": "operator-3c895af877e10f7f",
                    "run_id": run_id,
                    "task_key": IMG_CANARY_V3_TASK_KEY,
                    "task_authorization_ref": IMG_CANARY_V3_AUTHORIZATION_REF,
                    **common_binding_payload,
                    "previous_runs_evidence_hash": historical_evidence_hash,
                }
                v3_approval_binding = IMGCanaryV3ApprovalBinding(
                    **binding_payload,
                    content_hash=ai_image_stable_hash(binding_payload),
                )
        fingerprint = adapter.idempotency_fingerprint(provider_request)
        # This is planning evidence only. Runtime authorization, monthly budget,
        # and both kill switches are derived from a persisted PASS preflight.
        # Keeping these fields closed prevents the plan bundle itself from being
        # replayed as permission to make a paid request.
        gate_payload: dict[str, Any] = {
            "provider_boundary_gate_passed": False,
            "paid_call_authorization_gate_passed": True,
            "provider_cost_estimate_gate_passed": True,
            "channel_monthly_budget_gate_passed": False,
            "paid_attempt_limit_gate_passed": True,
            "provider_idempotency_key_valid": True,
            "global_kill_switch_open": False,
            "provider_kill_switch_open": False,
            "approved_production_execution_scope": False,
            "provider_boundary_gate_ref": None,
            "paid_call_authorization_gate_ref": approval.approval_ref,
            "provider_cost_estimate_gate_ref": provider_request.cost_ref,
            "channel_monthly_budget_gate_ref": None,
            "paid_attempt_limit_gate_ref": f"attempt://img-canary/{run_id}/available",
            "provider_idempotency_key_ref": provider_request.idempotency_key,
            "global_kill_switch_ref": None,
            "provider_kill_switch_ref": None,
            "request_fingerprint": fingerprint,
        }
        gates = GeminiImageExecutionGates(
            **gate_payload,
            evidence_hash=ai_image_stable_hash(gate_payload),
        )
        return IMGCanaryPlanBundle(
            run_identity=identity,
            requirements=requirements,
            decision=decision,
            visual_direction=direction,
            headline=headline,
            overlay_plan=overlay,
            cost=cost,
            generic_request=generic_request,
            compiled_prompt=compiled_prompt,
            provider_request=provider_request,
            approval=approval,
            execution_gates=gates,
            serialized_request_evidence=serialized_request_evidence,
            v2_approval_binding=v2_approval_binding,
            v3_approval_binding=v3_approval_binding,
        )

    @staticmethod
    def _requirements() -> SceneVisualRealizationRequirements:
        payload: dict[str, Any] = {
            "scene_id": IMG_CANARY_SCENE_ID,
            "semantic_intent": "Show fragmented information and missing shared context.",
            "target_duration_seconds": 6.0,
            "aspect_ratio": "16:9",
            "crop_safety_required": True,
            "previous_scene_summary": None,
            "next_scene_summary": None,
            "subject_action": "separated clusters remain visibly disconnected",
            "camera_angle": "slightly elevated editorial view",
            "shot_size": "wide",
            "segment_ids": ["segment-fragmented-information"],
            "niche_visual_source_profile": NicheVisualSourceProfile.STOCK_ASSISTED,
            "scene_class": "abstract_team_state",
            "narrative_function": "conceptual_problem",
            "scene_meaning": (
                "information is fragmented across disconnected locations and nobody sees the whole picture"
            ),
            "editorial_intent": (
                "Use a custom editorial still foundation while native composition owns the exact headline."
            ),
            "filmability_score": 0.10,
            "stock_searchability_score": 0.18,
            "required_specificity": 0.82,
            "custom_composition_score": 0.96,
            "exact_text_dependency": 0.90,
            "exact_number_dependency": 0.0,
            "named_workflow_nodes_required": False,
            "diagram_clarity_advantage": 0.18,
            "brand_or_product_dependency": 0.0,
            "product_specificity": 0.0,
            "evidence_truth_requirement": 0.0,
            "authorized_asset_available": False,
            "identity_consistency_requirement": 0.0,
            "recurring_identity_required": False,
            "human_action_requirement": 0.0,
            "motion_semantic_value": 0.10,
            "target_aspect_ratio": "16:9",
            "minimum_resolution": "1080p",
            "crop_safety_requirement": (
                "Protect a coherent focal cluster on the right and reserve headline-safe negative space on the left."
            ),
            "previous_scene_intent_ref": None,
            "next_scene_intent_ref": None,
        }
        return SceneVisualRealizationRequirements(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _visual_direction(*, project_id: str) -> VisualDirectionContract:
        payload: dict[str, Any] = {
            "contract_version": "img-canary.visual-direction.v1",
            "channel_id": "small-team-ai",
            "project_id": project_id,
            "format_identity_ref": "format-identity://small-team-ai/editorial-explainer",
            "format_identity_hash": ai_image_stable_hash("small-team-ai-editorial-explainer"),
            "visual_strategy_profile_ref": "visual-strategy://small-team-ai/stock-assisted",
            "visual_strategy_profile_hash": ai_image_stable_hash("small-team-ai-stock-assisted"),
            "realism_level": "clean professional editorial illustration",
            "treatment_mode": "restrained geometric knowledge-fragmentation metaphor",
            "human_presence_policy": "NO_IDENTIFIABLE_PERSON",
            "environment_type": "abstract editorial workspace",
            "industry_context": "small-team knowledge operations",
            "time_of_day": "timeless studio setting",
            "lighting_direction": "soft directional side light",
            "lighting_temperature": "neutral-warm",
            "palette": ["deep slate", "muted teal", "warm amber", "off-white"],
            "contrast": "medium-high focal separation",
            "saturation": "restrained",
            "camera_distance": "wide editorial composition",
            "lens_feel": "natural perspective",
            "camera_movement": "none in source still",
            "motion_intensity": "subtle native post motion only",
            "framing_rule": "weighted right focal composition with clean negative space on left",
            "depth_of_field_style": "moderate depth with readable document silhouettes",
            "texture_grain": "subtle paper grain",
            "tone_mode": "calm, practical, credible",
            "prohibited_cliches": [
                "fantasy islands",
                "sci-fi magic",
                "glowing brain",
                "floating dashboard",
                "corporate handshake",
            ],
            "channel_identity_markers": [
                "editorial geometry",
                "restrained palette",
                "clear business-explainer metaphor",
            ],
            "adjacent_scene_constraints": [
                "isolated one-scene canary; record continuity scope without claiming adjacent-scene validation"
            ],
        }
        return VisualDirectionContract(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _overlay_plan(
        *,
        run_id: str,
        decision: VisualSourceDecision,
        decision_ref: str,
        headline_ref: str,
    ) -> NativeOverlayPlan:
        exact_payload: dict[str, Any] = {
            "scene_id": IMG_CANARY_SCENE_ID,
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
            "authoritative_content_refs": [headline_ref],
        }
        exact = ExactTextNativeOverlayContract(
            **exact_payload,
            content_hash=ai_image_stable_hash(exact_payload),
        )
        text_safe = TextSafeRegion(
            id="fragmented-information-headline-safe",
            x=0.055,
            y=0.15,
            width=0.43,
            height=0.34,
            purpose="Authoritative native headline",
            minimum_contrast_requirement=4.5,
            alignment="LEFT",
        )
        reserved = TextSafeRegion(
            id="fragmented-information-caption-reserved",
            x=0.055,
            y=0.83,
            width=0.89,
            height=0.10,
            purpose="Keep lower frame clear for review/player chrome",
            minimum_contrast_requirement=4.5,
            alignment="CENTER",
        )
        payload: dict[str, Any] = {
            "plan_id": f"native-overlay-plan://img-canary/{run_id}",
            "scene_id": IMG_CANARY_SCENE_ID,
            "source_decision_ref": decision_ref,
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


class IMGCanaryPreflightService:
    @staticmethod
    def validate_bundle_cost_integrity(
        bundle: IMGCanaryPlanBundle,
    ) -> None:
        """Recompute catalog truth and every request/approval cost binding."""

        canonical_cost = GoogleGeminiImageModelPriceCatalog().estimate(
            model_id=bundle.provider_request.model_id,
            image_size=bundle.provider_request.image_size,
            aspect_ratio=bundle.provider_request.aspect_ratio,
            output_count=bundle.provider_request.output_count,
            attempt_count=bundle.approval.attempt_limit,
            hard_cap=IMG_CANARY_HARD_CAP_USD,
            approval_amount=IMG_CANARY_HARD_CAP_USD,
        )
        validate_gemini_image_cost_snapshot_integrity(
            bundle.cost,
            provider_key="google_gemini_image",
            model_id=bundle.provider_request.model_id,
            image_size=bundle.provider_request.image_size,
            aspect_ratio=bundle.provider_request.aspect_ratio,
            output_count=bundle.provider_request.output_count,
            attempt_count=bundle.approval.attempt_limit,
            catalog_estimate=canonical_cost,
        )
        if (
            bundle.provider_request.content_hash
            != ai_image_stable_hash(
                bundle.provider_request.model_dump(
                    mode="json", exclude={"content_hash"}
                )
            )
            or bundle.approval.content_hash
            != ai_image_stable_hash(
                bundle.approval.model_dump(mode="json", exclude={"content_hash"})
            )
            or bundle.generic_request.cost_catalog_ref != bundle.cost.price_catalog_ref
            or bundle.generic_request.cost_estimate_ref != bundle.cost.snapshot_hash
            or bundle.provider_request.cost_ref != bundle.cost.snapshot_hash
            or bundle.approval.catalog_ref != bundle.cost.price_catalog_ref
            or bundle.approval.estimated_cost_usd != bundle.cost.estimated_amount
            or bundle.approval.hard_cap_usd != bundle.cost.hard_cap
            or bundle.approval.request_hash != bundle.provider_request.content_hash
        ):
            raise ValueError("IMG_CANARY_COST_BINDING_MISMATCH")

    def evaluate(
        self,
        *,
        bundle: IMGCanaryPlanBundle,
        scoped_settings: Settings,
        vqc1_final_passed: bool,
        credential_rotation_evidence: IMGCanaryCredentialRotationEvidence,
        monthly_budget_evidence: IMGCanaryBudgetReservationEvidence,
        task_authorization_evidence: IMGCanaryTaskAuthorizationLedger,
        attempt_ledger: IMGCanaryAttemptLedger,
        drive_readiness_evidence: IMGCanaryDriveReadinessEvidence | None = None,
        worktree_reviewed: bool = True,
        repository_identity_passed: bool = True,
        runtime_submission: bool = False,
        planning_preflight_hash: str | None = None,
        now: datetime | None = None,
    ) -> IMGCanaryPreflightEvidence:
        checked_at = now or datetime.now(UTC)
        readiness = GoogleGeminiImageAdapter(scoped_settings).validate_configuration()
        default_fields = Settings.model_fields
        defaults_disabled = bool(
            default_fields["gemini_image_real_generation_enabled"].default is False
            and default_fields["img1_fixture_only"].default is True
            and default_fields["provider_real_execution_enabled"].default is False
            and default_fields["provider_production_execution_enabled"].default is False
            and default_fields["media_provider_calls_disabled"].default is True
        )
        self.validate_bundle_cost_integrity(bundle)
        for evidence, reason in (
            (monthly_budget_evidence, "IMG_CANARY_MONTHLY_BUDGET_HASH_MISMATCH"),
            (credential_rotation_evidence, "IMG_CANARY_CREDENTIAL_ROTATION_HASH_MISMATCH"),
            (task_authorization_evidence, "IMG_CANARY_TASK_AUTHORIZATION_HASH_MISMATCH"),
        ):
            hash_payload = (
                evidence.content_hash_payload()
                if isinstance(evidence, IMGCanaryTaskAuthorizationLedger)
                else evidence.model_dump(mode="json", exclude={"content_hash"})
            )
            if evidence.content_hash != ai_image_stable_hash(hash_payload):
                raise ValueError(reason)
        configured_monthly_cap = Decimal(
            scoped_settings.extra_ai_image_monthly_budget_usd
            if scoped_settings.extra_ai_image_monthly_budget_usd is not None
            else scoped_settings.monthly_ai_budget_usd or 0
        )
        expected_fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(
            bundle.provider_request
        )
        expected_task_key, expected_authorization_ref, _ = (
            img_canary_task_authority_identity(bundle.run_identity.run_id)
        )
        is_v2 = bundle.run_identity.run_id.startswith("img-canary-v2-")
        is_v3 = bundle.run_identity.run_id.startswith("img-canary-v3-")
        is_versioned = is_v2 or is_v3
        serialized = bundle.serialized_request_evidence
        v2_binding = bundle.v2_approval_binding
        v3_binding = bundle.v3_approval_binding
        fresh_serialization: dict[str, Any] | None = None
        if is_versioned:
            try:
                fresh_serialization = GoogleGeminiImageAdapter(
                    scoped_settings
                ).capture_official_sdk_serialization(bundle.provider_request)
            except Exception:
                fresh_serialization = None
        expected_raw_body = GoogleGeminiImageAdapter.expected_serialized_request_body(
            bundle.provider_request
        )
        expected_redacted_body = dict(expected_raw_body)
        expected_redacted_body["input"] = (
            f"sha256://prompt/{bundle.provider_request.prompt_hash}"
        )
        serialized_contract_passed = bool(
            is_versioned
            and serialized is not None
            and serialized.run_id == bundle.run_identity.run_id
            and serialized.request_hash == bundle.provider_request.content_hash
            and serialized.prompt_hash == bundle.provider_request.prompt_hash
            and serialized.redacted_request_body == expected_redacted_body
            and fresh_serialization is not None
            and fresh_serialization.get("body") == expected_raw_body
            and fresh_serialization.get("body_sha256")
            == serialized.serialized_body_hash
            and fresh_serialization.get("path") == "/v1beta/interactions"
            and fresh_serialization.get("method") == "POST"
            and serialized.sdk_retries_disabled is True
            and serialized.sdk_retry_attempts == 1
        )
        v2_approval_binding_passed = bool(
            is_v2
            and v2_binding is not None
            and serialized is not None
            and v2_binding.run_id == bundle.run_identity.run_id
            and v2_binding.task_key == expected_task_key
            and v2_binding.task_authorization_ref == expected_authorization_ref
            and v2_binding.base_approval_hash == bundle.approval.content_hash
            and v2_binding.request_hash == bundle.provider_request.content_hash
            and v2_binding.prompt_hash == bundle.provider_request.prompt_hash
            and v2_binding.serialized_request_evidence_hash
            == serialized.content_hash
            and v2_binding.serialized_body_hash == serialized.serialized_body_hash
            and task_authorization_evidence.approval_version == "V2"
            and task_authorization_evidence.approved_run_id
            == bundle.run_identity.run_id
            and task_authorization_evidence.approved_request_fingerprint
            == expected_fingerprint
            and task_authorization_evidence.approved_prompt_hash
            == bundle.provider_request.prompt_hash
            and task_authorization_evidence.approved_serialized_body_hash
            == serialized.serialized_body_hash
            and task_authorization_evidence.approved_scoped_approval_hash
            == v2_binding.content_hash
        )
        v3_approval_binding_passed = bool(
            is_v3
            and v3_binding is not None
            and serialized is not None
            and v3_binding.run_id == bundle.run_identity.run_id
            and v3_binding.task_key == expected_task_key
            and v3_binding.task_authorization_ref == expected_authorization_ref
            and v3_binding.base_approval_hash == bundle.approval.content_hash
            and v3_binding.request_hash == bundle.provider_request.content_hash
            and v3_binding.prompt_hash == bundle.provider_request.prompt_hash
            and v3_binding.serialized_request_evidence_hash
            == serialized.content_hash
            and v3_binding.serialized_body_hash == serialized.serialized_body_hash
            and task_authorization_evidence.approval_version == "V3"
            and task_authorization_evidence.approved_run_id
            == bundle.run_identity.run_id
            and task_authorization_evidence.approved_request_fingerprint
            == expected_fingerprint
            and task_authorization_evidence.approved_prompt_hash
            == bundle.provider_request.prompt_hash
            and task_authorization_evidence.approved_serialized_body_hash
            == serialized.serialized_body_hash
            and task_authorization_evidence.approved_scoped_approval_hash
            == v3_binding.content_hash
        )
        drive_readiness_passed = bool(
            is_versioned
            and drive_readiness_evidence is not None
            and drive_readiness_evidence.run_id == bundle.run_identity.run_id
            and drive_readiness_evidence.status == "PASS"
            and drive_readiness_evidence.content_hash
            == ai_image_stable_hash(
                drive_readiness_evidence.model_dump(
                    mode="json", exclude={"content_hash"}
                )
            )
        )
        monthly_budget_passed = bool(
            monthly_budget_evidence.run_id == bundle.run_identity.run_id
            and monthly_budget_evidence.request_estimate_usd
            == bundle.cost.estimated_amount
            and monthly_budget_evidence.request_fingerprint == expected_fingerprint
            and monthly_budget_evidence.dedicated_cap_usd == configured_monthly_cap
            and monthly_budget_evidence.status
            in {"AVAILABLE_UNRESERVED", "RESERVED", "ALREADY_RESERVED"}
            and (
                monthly_budget_evidence.status == "AVAILABLE_UNRESERVED"
                or monthly_budget_evidence.reservation_ref
            )
        )
        if runtime_submission:
            task_authorization_passed = bool(
                task_authorization_evidence.task_key == expected_task_key
                and task_authorization_evidence.authorization_ref
                == expected_authorization_ref
                and task_authorization_evidence.status == "CLAIMED"
                and task_authorization_evidence.claimed_run_id
                == bundle.run_identity.run_id
                and task_authorization_evidence.claimed_request_fingerprint
                == expected_fingerprint
                and monthly_budget_evidence.status
                in {"RESERVED", "ALREADY_RESERVED"}
                and monthly_budget_evidence.reservation_ref is not None
            )
        else:
            task_authorization_passed = bool(
                task_authorization_evidence.task_key == expected_task_key
                and task_authorization_evidence.authorization_ref
                == expected_authorization_ref
                and task_authorization_evidence.status == "AVAILABLE"
                and task_authorization_evidence.claimed_run_id is None
                and task_authorization_evidence.claimed_request_fingerprint is None
            )
        planned_attempt_bound = bool(
            attempt_ledger.content_hash
            == ai_image_stable_hash(
                attempt_ledger.model_dump(mode="json", exclude={"content_hash"})
            )
            and attempt_ledger.run_id == bundle.run_identity.run_id
            and attempt_ledger.request_fingerprint == expected_fingerprint
            and attempt_ledger.idempotency_key_hash
            == ai_image_stable_hash(bundle.provider_request.idempotency_key)
            and attempt_ledger.status == "PLANNED"
            and attempt_ledger.attempts_consumed == 0
            and not attempt_ledger.provider_call_made
        )
        checks: dict[str, bool] = {
            "repository_identity_passed": repository_identity_passed,
            "worktree_reviewed": worktree_reviewed,
            "vqc1_final_passed": vqc1_final_passed,
            "credential_configured": bool(
                readiness.credential_configured
                and credential_rotation_evidence.credential_configured
            ),
            "credential_safe_for_use": bool(
                credential_rotation_evidence.status == "PASS"
                and credential_rotation_evidence.fingerprint_changed
            ),
            "route_registered": "google_gemini_image" in CANONICAL_PROVIDER_KEYS,
            "model_catalog_present": readiness.model_catalog_present,
            "model_locked": bundle.provider_request.model_id == IMG_CANARY_MODEL,
            "image_size_locked": bundle.provider_request.image_size == IMG_CANARY_IMAGE_SIZE,
            "aspect_ratio_locked": bundle.provider_request.aspect_ratio == IMG_CANARY_ASPECT_RATIO,
            "output_count_locked": bundle.provider_request.output_count == 1,
            "reference_images_empty": not bundle.provider_request.reference_images,
            "grounding_disabled": not bundle.provider_request.grounding_enabled
            and not bundle.provider_request.search_grounding_enabled,
            "raster_decoder_ready": readiness.raster_decoder_ready is True,
            "provider_boundary_passed": bool(
                not bundle.decision.provider_execution_allowed
                and str(bundle.decision.decision_status) == "PLANNED"
                and bundle.approval.base_provider_execution_allowed is False
                and bundle.approval.base_decision_status == "PLANNED"
                and bundle.approval.scoped_provider_boundary_authorized
                and bundle.approval.visual_source_decision_hash == bundle.decision.content_hash
                and bundle.approval.operator_authorization_source
                == (
                    "CODEX_OPERATOR_MESSAGE"
                    if is_v3
                    else "ATTACHED_MASTER_PROMPT"
                )
                and scoped_settings.gemini_image_provider_route_approved
            ),
            "cost_estimate_passed": bool(
                bundle.cost.estimated_amount <= IMG_CANARY_HARD_CAP_USD
                and bundle.cost.hard_cap == IMG_CANARY_HARD_CAP_USD
                and bundle.cost.approval_amount == IMG_CANARY_HARD_CAP_USD
                and bundle.generic_request.cost_catalog_ref == bundle.cost.price_catalog_ref
                and bundle.generic_request.cost_estimate_ref == bundle.cost.snapshot_hash
                and bundle.provider_request.cost_ref == bundle.cost.snapshot_hash
                and bundle.approval.catalog_ref == bundle.cost.price_catalog_ref
                and bundle.approval.estimated_cost_usd == bundle.cost.estimated_amount
                and bundle.approval.hard_cap_usd == IMG_CANARY_HARD_CAP_USD
            ),
            "paid_authorization_passed": bundle.approval.request_hash
            == bundle.provider_request.content_hash
            and bundle.approval.prompt_hash == bundle.provider_request.prompt_hash
            and bundle.approval.expires_at > checked_at,
            "monthly_budget_passed": monthly_budget_passed,
            "task_authorization_passed": task_authorization_passed,
            "attempt_limit_passed": bundle.approval.attempt_limit == 1
            and planned_attempt_bound,
            "idempotency_passed": bundle.execution_gates.request_fingerprint
            == GoogleGeminiImageAdapter.idempotency_fingerprint(bundle.provider_request),
            "global_kill_switch_scoped_open": bool(
                scoped_settings.provider_real_execution_enabled
                and scoped_settings.provider_production_execution_enabled
                and not scoped_settings.media_provider_calls_disabled
            ),
            "provider_kill_switch_scoped_open": bool(
                scoped_settings.gemini_image_real_generation_enabled
                and not scoped_settings.img1_fixture_only
            ),
            "defaults_remain_disabled": defaults_disabled,
        }
        if is_versioned:
            checks.update(
                {
                    "serialized_request_contract_passed": (
                        serialized_contract_passed
                    ),
                    "drive_readiness_passed": drive_readiness_passed,
                }
            )
            if is_v2:
                checks["v2_approval_binding_passed"] = (
                    v2_approval_binding_passed
                )
            else:
                checks["v3_approval_binding_passed"] = (
                    v3_approval_binding_passed
                )
        reason_by_check = {
            "repository_identity_passed": "IMG_CANARY_REPOSITORY_IDENTITY_BLOCKED",
            "worktree_reviewed": "IMG_CANARY_WORKTREE_NOT_REVIEWED",
            "vqc1_final_passed": "VQC1_FINAL_NOT_PASS",
            "credential_configured": "GEMINI_API_KEY_NOT_CONFIGURED",
            "credential_safe_for_use": "GEMINI_API_KEY_ROTATION_REQUIRED_AFTER_EXPOSURE",
            "route_registered": "GEMINI_IMAGE_ROUTE_NOT_REGISTERED",
            "model_catalog_present": "GEMINI_IMAGE_MODEL_CATALOG_MISSING",
            "model_locked": "IMG_CANARY_MODEL_MISMATCH",
            "image_size_locked": "IMG_CANARY_IMAGE_SIZE_MISMATCH",
            "aspect_ratio_locked": "IMG_CANARY_ASPECT_RATIO_MISMATCH",
            "output_count_locked": "IMG_CANARY_OUTPUT_COUNT_MISMATCH",
            "reference_images_empty": "IMG_CANARY_REFERENCE_IMAGE_NOT_AUTHORIZED",
            "grounding_disabled": "IMG_CANARY_GROUNDING_NOT_AUTHORIZED",
            "raster_decoder_ready": "IMG_CANARY_JPEG_SAFE_DECODER_UNAVAILABLE",
            "provider_boundary_passed": "IMG_CANARY_PROVIDER_BOUNDARY_BLOCKED",
            "cost_estimate_passed": "PAID_CANARY_COST_APPROVAL_BLOCKED",
            "paid_authorization_passed": "IMG_CANARY_SCOPED_APPROVAL_INVALID",
            "monthly_budget_passed": "IMG_CANARY_MONTHLY_BUDGET_BLOCKED",
            "task_authorization_passed": "IMG_CANARY_TASK_AUTHORIZATION_UNAVAILABLE",
            "attempt_limit_passed": "IMG_CANARY_ATTEMPT_LIMIT_BLOCKED",
            "idempotency_passed": "IMG_CANARY_IDEMPOTENCY_BLOCKED",
            "global_kill_switch_scoped_open": "IMG_CANARY_GLOBAL_KILL_SWITCH_CLOSED",
            "provider_kill_switch_scoped_open": "IMG_CANARY_PROVIDER_KILL_SWITCH_CLOSED",
            "defaults_remain_disabled": "IMG_CANARY_REPOSITORY_DEFAULTS_CHANGED",
            "serialized_request_contract_passed": (
                "IMG_CANARY_V3_SERIALIZED_REQUEST_CONTRACT_BLOCKED"
                if is_v3
                else "IMG_CANARY_V2_SERIALIZED_REQUEST_CONTRACT_BLOCKED"
            ),
            "v2_approval_binding_passed": "IMG_CANARY_V2_APPROVAL_BINDING_BLOCKED",
            "v3_approval_binding_passed": "IMG_CANARY_V3_APPROVAL_BINDING_BLOCKED",
            "drive_readiness_passed": (
                "IMG_CANARY_V3_DRIVE_READINESS_BLOCKED"
                if is_v3
                else "IMG_CANARY_V2_DRIVE_READINESS_BLOCKED"
            ),
        }
        blockers = [reason_by_check[name] for name, passed in checks.items() if not passed]
        blockers = sorted(blockers)
        payload: dict[str, Any] = {
            "run_id": bundle.run_identity.run_id,
            "status": "PASS" if not blockers else "BLOCKED",
            **checks,
            "credential_rotation_evidence": credential_rotation_evidence,
            "monthly_budget_evidence": monthly_budget_evidence,
            "task_authorization_evidence": task_authorization_evidence,
            "production_database_mutation_required": False,
            "blocker_reason_codes": blockers,
            "evidence_refs": {
                "run_identity": bundle.run_identity.content_hash,
                "provider_request": bundle.provider_request.content_hash,
                "approval": bundle.approval.content_hash,
                "cost": bundle.cost.snapshot_hash,
                "execution_gates": bundle.execution_gates.evidence_hash,
                "monthly_budget": monthly_budget_evidence.content_hash,
                "credential_rotation": credential_rotation_evidence.content_hash,
                "task_authorization": task_authorization_evidence.content_hash,
                "attempt_ledger_planned": attempt_ledger.content_hash,
                "raster_decoder_readiness": (
                    IMGCanaryPreflightEvidence.raster_decoder_evidence_hash(
                        ready=readiness.raster_decoder_ready is True,
                    )
                ),
                **(
                    {
                        "serialized_request_evidence": serialized.content_hash,
                        "serialized_request_body": serialized.serialized_body_hash,
                        "v2_approval_binding": v2_binding.content_hash,
                        "previous_run_immutability": (
                            v2_binding.previous_run_evidence_hash
                        ),
                        "drive_readiness": drive_readiness_evidence.content_hash,
                    }
                    if is_v2
                    and serialized is not None
                    and v2_binding is not None
                    and drive_readiness_evidence is not None
                    else {}
                ),
                **(
                    {
                        "serialized_request_evidence": serialized.content_hash,
                        "serialized_request_body": serialized.serialized_body_hash,
                        "v3_approval_binding": v3_binding.content_hash,
                        "previous_runs_immutability": (
                            v3_binding.previous_runs_evidence_hash
                        ),
                        "drive_readiness": drive_readiness_evidence.content_hash,
                    }
                    if is_v3
                    and serialized is not None
                    and v3_binding is not None
                    and drive_readiness_evidence is not None
                    else {}
                ),
                **(
                    {"planning_preflight": planning_preflight_hash}
                    if planning_preflight_hash
                    else {}
                ),
            },
            "checked_at": checked_at,
            "approval_expires_at": bundle.approval.expires_at,
        }
        return IMGCanaryPreflightEvidence(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def execution_gates(
        *,
        bundle: IMGCanaryPlanBundle,
        preflight: IMGCanaryPreflightEvidence,
    ) -> GeminiImageExecutionGates:
        """Derive paid-submit gates only from the persisted preflight result."""

        if preflight.content_hash != ai_image_stable_hash(
            preflight.content_hash_payload()
        ):
            raise ValueError("IMG_CANARY_PREFLIGHT_HASH_MISMATCH")
        if preflight.run_id != bundle.run_identity.run_id:
            raise ValueError("IMG_CANARY_PREFLIGHT_RUN_BINDING_MISMATCH")
        if preflight.evidence_refs.get("provider_request") != bundle.provider_request.content_hash:
            raise ValueError("IMG_CANARY_PREFLIGHT_REQUEST_BINDING_MISMATCH")
        if preflight.evidence_refs.get("approval") != bundle.approval.content_hash:
            raise ValueError("IMG_CANARY_PREFLIGHT_APPROVAL_BINDING_MISMATCH")
        if preflight.evidence_refs.get("cost") != bundle.cost.snapshot_hash:
            raise ValueError("IMG_CANARY_PREFLIGHT_COST_BINDING_MISMATCH")
        decoder_gate_valid = bool(
            preflight.raster_decoder_ready is True
            and preflight.evidence_refs.get("raster_decoder_readiness")
            == IMGCanaryPreflightEvidence.raster_decoder_evidence_hash(ready=True)
        )
        if preflight.status == "PASS" and not decoder_gate_valid:
            raise ValueError("IMG_CANARY_PREFLIGHT_DECODER_READINESS_REQUIRED")
        is_v2 = bundle.run_identity.run_id.startswith("img-canary-v2-")
        is_v3 = bundle.run_identity.run_id.startswith("img-canary-v3-")
        serialized = bundle.serialized_request_evidence
        v2_binding = bundle.v2_approval_binding
        v3_binding = bundle.v3_approval_binding
        v2_gate_valid = bool(
            not is_v2
            or (
                serialized is not None
                and v2_binding is not None
                and preflight.serialized_request_contract_passed is True
                and preflight.v2_approval_binding_passed is True
                and preflight.drive_readiness_passed is True
                and preflight.evidence_refs.get("serialized_request_evidence")
                == serialized.content_hash
                and preflight.evidence_refs.get("serialized_request_body")
                == serialized.serialized_body_hash
                and preflight.evidence_refs.get("v2_approval_binding")
                == v2_binding.content_hash
                and preflight.evidence_refs.get("previous_run_immutability")
                == v2_binding.previous_run_evidence_hash
                and bool(preflight.evidence_refs.get("drive_readiness"))
            )
        )
        if preflight.status == "PASS" and not v2_gate_valid:
            raise ValueError("IMG_CANARY_V2_PREFLIGHT_BINDING_REQUIRED")
        v3_gate_valid = bool(
            not is_v3
            or (
                serialized is not None
                and v3_binding is not None
                and preflight.serialized_request_contract_passed is True
                and preflight.v3_approval_binding_passed is True
                and preflight.drive_readiness_passed is True
                and preflight.evidence_refs.get("serialized_request_evidence")
                == serialized.content_hash
                and preflight.evidence_refs.get("serialized_request_body")
                == serialized.serialized_body_hash
                and preflight.evidence_refs.get("v3_approval_binding")
                == v3_binding.content_hash
                and preflight.evidence_refs.get("previous_runs_immutability")
                == v3_binding.previous_runs_evidence_hash
                and bool(preflight.evidence_refs.get("drive_readiness"))
            )
        )
        if preflight.status == "PASS" and not v3_gate_valid:
            raise ValueError("IMG_CANARY_V3_PREFLIGHT_BINDING_REQUIRED")

        passed = bool(
            preflight.status == "PASS"
            and decoder_gate_valid
            and v2_gate_valid
            and v3_gate_valid
        )
        preflight_ref = f"preflight://img-canary/{preflight.run_id}/{preflight.content_hash}"
        payload: dict[str, Any] = {
            "provider_boundary_gate_passed": passed and preflight.provider_boundary_passed,
            "paid_call_authorization_gate_passed": passed and preflight.paid_authorization_passed,
            "provider_cost_estimate_gate_passed": passed and preflight.cost_estimate_passed,
            "channel_monthly_budget_gate_passed": passed and preflight.monthly_budget_passed,
            "paid_attempt_limit_gate_passed": passed and preflight.attempt_limit_passed,
            "provider_idempotency_key_valid": passed and preflight.idempotency_passed,
            "global_kill_switch_open": passed and preflight.global_kill_switch_scoped_open,
            "provider_kill_switch_open": passed and preflight.provider_kill_switch_scoped_open,
            "approved_production_execution_scope": passed,
            "provider_boundary_gate_ref": preflight_ref if passed and preflight.provider_boundary_passed else None,
            "paid_call_authorization_gate_ref": (
                bundle.provider_request.approval_ref
                if passed and preflight.paid_authorization_passed
                else None
            ),
            "provider_cost_estimate_gate_ref": (
                bundle.provider_request.cost_ref
                if passed and preflight.cost_estimate_passed
                else None
            ),
            "channel_monthly_budget_gate_ref": preflight_ref if passed and preflight.monthly_budget_passed else None,
            "paid_attempt_limit_gate_ref": preflight_ref if passed and preflight.attempt_limit_passed else None,
            "provider_idempotency_key_ref": (
                bundle.provider_request.idempotency_key
                if passed and preflight.idempotency_passed
                else None
            ),
            "global_kill_switch_ref": preflight_ref if passed and preflight.global_kill_switch_scoped_open else None,
            "provider_kill_switch_ref": preflight_ref if passed and preflight.provider_kill_switch_scoped_open else None,
            "request_fingerprint": GoogleGeminiImageAdapter.idempotency_fingerprint(
                bundle.provider_request
            ),
        }
        return GeminiImageExecutionGates(
            **payload,
            evidence_hash=ai_image_stable_hash(payload),
        )


class IMGCanaryAttemptLedgerStore:
    def __init__(self, path: Path):
        self.path = path.resolve()

    def create(self, *, run_id: str, request_fingerprint: str, idempotency_key: str, now: datetime) -> IMGCanaryAttemptLedger:
        with self._exclusive_lock():
            if self.path.exists():
                existing = self._load_unlocked()
                if (
                    existing.run_id != run_id
                    or existing.request_fingerprint != request_fingerprint
                    or existing.idempotency_key_hash != ai_image_stable_hash(idempotency_key)
                ):
                    raise FileExistsError("IMG_CANARY_ATTEMPT_LEDGER_IDENTITY_CONFLICT")
                return existing
            payload = {
                "run_id": run_id,
                "request_fingerprint": request_fingerprint,
                "idempotency_key_hash": ai_image_stable_hash(idempotency_key),
                "attempt_limit": 1,
                "attempts_consumed": 0,
                "status": "PLANNED",
                "provider_call_made": False,
                "provider_request_id_ref": None,
                "provider_operation_id_ref": None,
                "failure_reason_code": None,
                "created_at": now,
                "updated_at": now,
            }
            ledger = IMGCanaryAttemptLedger(
                **payload,
                content_hash=ai_image_stable_hash(payload),
            )
            self._write_unlocked(ledger)
            return ledger

    def consume_at_submit(self, *, expected_fingerprint: str, now: datetime) -> IMGCanaryAttemptLedger:
        with self._exclusive_lock():
            ledger = self._load_unlocked()
            if ledger.request_fingerprint != expected_fingerprint:
                raise ValueError("IMG_CANARY_ATTEMPT_LEDGER_FINGERPRINT_MISMATCH")
            if ledger.status != "PLANNED" or ledger.attempts_consumed != 0:
                raise PermissionError("IMG_CANARY_PAID_ATTEMPT_ALREADY_CONSUMED")
            payload = ledger.model_dump(mode="python", exclude={"content_hash"})
            payload.update(
                {
                    "attempts_consumed": 1,
                    "status": "EXECUTING",
                    "provider_call_made": True,
                    "updated_at": now,
                }
            )
            consumed = IMGCanaryAttemptLedger(
                **payload,
                content_hash=ai_image_stable_hash(payload),
            )
            self._write_unlocked(consumed)
            return consumed

    def finalize(
        self,
        *,
        succeeded: bool,
        now: datetime,
        provider_request_id_ref: str | None = None,
        provider_operation_id_ref: str | None = None,
        failure_reason_code: str | None = None,
    ) -> IMGCanaryAttemptLedger:
        with self._exclusive_lock():
            ledger = self._load_unlocked()
            if ledger.status != "EXECUTING" or ledger.attempts_consumed != 1:
                raise ValueError("IMG_CANARY_ATTEMPT_LEDGER_NOT_EXECUTING")
            payload = ledger.model_dump(mode="python", exclude={"content_hash"})
            payload.update(
                {
                    "status": "SUCCEEDED" if succeeded else "BLOCKED_REQUIRES_NEW_APPROVAL",
                    "provider_request_id_ref": provider_request_id_ref,
                    "provider_operation_id_ref": provider_operation_id_ref,
                    "failure_reason_code": None if succeeded else failure_reason_code or "PROVIDER_OUTPUT_INVALID",
                    "updated_at": now,
                }
            )
            finalized = IMGCanaryAttemptLedger(
                **payload,
                content_hash=ai_image_stable_hash(payload),
            )
            self._write_unlocked(finalized)
            return finalized

    def load(self) -> IMGCanaryAttemptLedger:
        with self._exclusive_lock():
            return self._load_unlocked()

    def _load_unlocked(self) -> IMGCanaryAttemptLedger:
        return IMGCanaryAttemptLedger.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def _write_unlocked(self, ledger: IMGCanaryAttemptLedger) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        part = self.path.with_name(self.path.name + ".part")
        if part.exists():
            raise FileExistsError("IMG_CANARY_ATTEMPT_LEDGER_PART_CONFLICT")
        try:
            with part.open("x", encoding="utf-8") as stream:
                stream.write(ledger.model_dump_json(indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(part, self.path)
            _fsync_parent_directory(self.path)
        except Exception:
            part.unlink(missing_ok=True)
            raise

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        with lock_path.open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class IMGCanaryNativeReviewPlanBuilder:
    def build(
        self,
        *,
        bundle: IMGCanaryPlanBundle,
        vqc_report: ImageVisualQualityControlReport,
        normalized_image_path: Path,
        workspace_root: Path,
        image_checksum: str,
        created_at: datetime | None = None,
    ) -> tuple[NativeRenderPlan, CompiledNativeRenderManifest]:
        root = workspace_root.resolve()
        image = normalized_image_path.resolve(strict=True)
        try:
            image.relative_to(root)
        except ValueError as exc:
            raise ValueError("IMG_CANARY_RENDER_IMAGE_OUTSIDE_WORKSPACE") from exc
        actual_checksum = GoogleGeminiImageAdapter._file_sha256(image)
        if actual_checksum != image_checksum:
            raise ValueError("IMG_CANARY_RENDER_IMAGE_CHECKSUM_MISMATCH")
        if vqc_report.content_hash != ai_image_stable_hash(
            vqc_report.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("IMG_CANARY_NATIVE_REVIEW_VQC_HASH_MISMATCH")
        required_pass_gates = {
            "CompositionComplianceGate",
            "TechnicalImageFitnessGate",
            "CropSafetyGate",
            "RightsDisclosureCompletenessGate",
            "NativeOverlayComplianceGate",
        }
        gates_by_name = {gate.gate_name: gate for gate in vqc_report.gate_results}
        if (
            vqc_report.run_id != bundle.run_identity.run_id
            or vqc_report.image_sha256 != actual_checksum
            or vqc_report.technical_status != "PASS"
            or not vqc_report.archive_eligible_for_review
            or any(
                gates_by_name.get(name) is None
                or gates_by_name[name].result != "PASS"
                for name in required_pass_gates
            )
            or any(gate.result == "BLOCK" for gate in vqc_report.gate_results)
        ):
            raise ValueError("IMG_CANARY_NATIVE_REVIEW_VQC_NOT_ELIGIBLE")
        no_captions = root / "render" / "no-captions.srt"
        no_captions.parent.mkdir(parents=True, exist_ok=True)
        if not no_captions.exists():
            no_captions.write_text("", encoding="utf-8")
        srt_hash = GoogleGeminiImageAdapter._file_sha256(no_captions)
        created = created_at or datetime.now(UTC)
        scene = NativeRenderScene(
            scene_id=IMG_CANARY_SCENE_ID,
            source_segment_ids=["segment-fragmented-information"],
            narration_start_ms=0,
            narration_end_ms=6000,
            duration_ms=6000,
            visual_treatment="STATIC_COMPOSITION",
            layout_type="GENERATED_STILL_WITH_NATIVE_HEADLINE",
            asset_requirements=[AssetRequirement(key="generated_still", kind="LOCAL_FILE")],
            resolved_asset_refs=[
                ResolvedAssetRef(
                    key="generated_still",
                    path=str(image),
                    checksum=actual_checksum,
                )
            ],
            animation_type="SLOW_ZOOM_IN",
            transition_in=None,
            transition_out=None,
            emphasis_targets=["fragmented-clusters", "native-headline"],
            safe_area_policy="IMG_CANARY_NATIVE_HEADLINE",
            originality_role="NON_PRODUCTION_REVIEW_CANDIDATE",
            provider_intent="GOOGLE_GEMINI_IMAGE_SOURCE_ALREADY_RESOLVED",
            scene_notes="One isolated canary; no narration provider and no adjacent-scene continuity claim.",
            scene_hash=ai_image_stable_hash(
                {
                    "scene_id": IMG_CANARY_SCENE_ID,
                    "image_checksum": actual_checksum,
                    "overlay_hash": bundle.overlay_plan.content_hash,
                }
            ),
            visual_routing_mode="VSR1_STRICT",
            source_decision_ref=bundle.generic_request.visual_source_decision_ref,
            source_decision_hash=bundle.decision.content_hash,
            preferred_source_route=bundle.decision.preferred_source_route,
            exact_text_required=True,
            exact_number_required=False,
            forbidden_generated_text=True,
            forbidden_generated_logo=True,
            forbidden_generated_fake_ui=True,
            text_safe_regions=list(bundle.overlay_plan.text_safe_regions),
            reserved_overlay_regions=list(bundle.overlay_plan.reserved_overlay_regions),
            eligibility_gate_refs=[
                f"vqc1://{bundle.run_identity.run_id}/report/{vqc_report.content_hash}"
            ],
            native_overlay_required=True,
            native_overlay_plan=bundle.overlay_plan,
        )
        plan = NativeRenderPlan(
            plan_id=f"native-render-plan://img-canary/{bundle.run_identity.run_id}",
            plan_version=1,
            package_id=bundle.run_identity.package_id,
            video_project_id=bundle.run_identity.project_id,
            company_id="vcos",
            channel_id="small-team-ai",
            channel_profile_version_id="historical://ch1-flex-v1",
            effective_context_snapshot_id="snapshot://img-canary/scoped-non-production",
            effective_context_hash=ai_image_stable_hash("img-canary-scoped-non-production"),
            format_identity_contract_ref="historical://ch1-flex-v1/format-identity",
            format_identity_contract_hash=ai_image_stable_hash("ch1-flex-v1-format-identity"),
            format_identity_status="APPROVED",
            episode_originality_manifest_ref=f"manifest://img-canary/{bundle.run_identity.run_id}/originality",
            episode_originality_manifest_hash=ai_image_stable_hash(
                {"run_id": bundle.run_identity.run_id, "scope": "ISOLATED_CANARY"}
            ),
            final_originality_gate="PASS",
            claim_evidence_ledger_refs=[],
            synthetic_media_disclosure_receipt_ref=(
                f"disclosure://img-canary/{bundle.run_identity.run_id}/synthetic-media"
            ),
            script_ref=f"manifest://img-canary/{bundle.run_identity.run_id}/scene-requirements",
            script_hash=bundle.requirements.content_hash,
            srt_ref=str(no_captions),
            srt_hash=srt_hash,
            audio_timeline_ref=None,
            temporal_authority_mode="LEGACY_HISTORICAL",
            canonical_media_timeline_ref=None,
            canonical_media_timeline_hash=None,
            canonical_audio_asset_ref=None,
            canonical_caption_compilation_ref=None,
            canonical_caption_compilation_hash=None,
            scene_timing_source="ISOLATED_CANARY_FIXED_DURATION",
            caption_timing_source="NONE",
            parallel_timing_inputs=[],
            visual_plan_ref=bundle.generic_request.visual_source_decision_ref,
            visual_plan_hash=bundle.decision.content_hash,
            visual_direction_contract_ref=bundle.generic_request.visual_direction_contract_ref,
            visual_direction_contract_hash=bundle.visual_direction.content_hash,
            creative_gate_results={
                "SemanticMatchGate": "REVIEW_REQUIRED",
                "VisualContinuityGate": "REVIEW_REQUIRED",
                "AssetAdjacencyGate": "REVIEW_REQUIRED",
            },
            canvas_spec=CanvasSpec(width=1920, height=1080, fps=30),
            scenes=[scene],
            global_motion_policy={
                "preset": "kenburns_center_soft",
                "maximum_zoom": 1.04,
                "deterministic": True,
            },
            caption_policy={"enabled": False},
            audio_policy={"source": "LOCAL_SILENCE", "provider_call_made": False},
            output_profiles=["YT_LONG_1080P30_SDR_H264_VT"],
            character_policy_mode="NO_CHARACTER",
            purpose="IMG_CANARY_NON_PRODUCTION_REVIEW",
            production_eligible=False,
            status="VALIDATED",
            content_hash="",
            created_at=created,
            created_by="codex-img-canary-runner",
        )
        plan = plan.model_copy(update={"content_hash": canonical_plan_hash(plan)})
        compiled = NativeMotionCompiler().compile(
            plan,
            allow_resolved_provider_assets=True,
        )
        return plan, compiled


class IMGCanaryImageNormalizer:
    def __init__(self, *, ffmpeg: str = FFMPEG_FULL_DEFAULT):
        self.ffmpeg = ffmpeg

    def normalize(
        self,
        *,
        source_path: Path,
        destination_path: Path,
        workspace_root: Path,
    ) -> dict[str, Any]:
        root = workspace_root.resolve()
        source = source_path.resolve(strict=True)
        destination = destination_path.resolve(strict=False)
        for candidate in (source, destination):
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError("IMG_CANARY_NORMALIZATION_PATH_ESCAPE") from exc
        if source.is_symlink() or not source.is_file():
            raise ValueError("IMG_CANARY_NORMALIZATION_SOURCE_INVALID")
        source_width, source_height, source_format = GoogleGeminiImageAdapter.probe_image(source)
        source_ratio = source_width / source_height
        target_ratio = 16 / 9
        if source_ratio >= target_ratio:
            crop_height = source_height
            crop_width = int(source_height * target_ratio)
            crop_x = (source_width - crop_width) // 2
            crop_y = 0
        else:
            crop_width = source_width
            crop_height = int(source_width / target_ratio)
            crop_x = 0
            crop_y = (source_height - crop_height) // 2
        if crop_width < 1920 or crop_height < 1080:
            raise ValueError("IMG_CANARY_EFFECTIVE_CROP_RESOLUTION_BELOW_1080P")

        source_checksum = GoogleGeminiImageAdapter._file_sha256(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.name + ".part.png")
        receipt_path = destination.with_name(destination.name + ".normalization.json")
        if not destination.exists() and part.is_file() and receipt_path.is_file():
            try:
                persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
                expected_hash = ai_image_stable_hash(
                    {key: value for key, value in persisted.items() if key != "content_hash"}
                )
                part_width, part_height, part_format = GoogleGeminiImageAdapter.probe_image(part)
                if (
                    persisted.get("content_hash") != expected_hash
                    or persisted.get("source_sha256") != source_checksum
                    or persisted.get("target_path") != str(destination)
                    or persisted.get("target_sha256")
                    != GoogleGeminiImageAdapter._file_sha256(part)
                    or (part_width, part_height, part_format) != (1920, 1080, "PNG")
                ):
                    raise ValueError("IMG_CANARY_NORMALIZATION_RECOVERY_BINDING_INVALID")
                os.replace(part, destination)
                _fsync_parent_directory(destination)
                return persisted
            except Exception as exc:
                raise FileExistsError("IMG_CANARY_NORMALIZATION_RECOVERY_FAILED") from exc
        if destination.exists():
            width, height, image_format = GoogleGeminiImageAdapter.probe_image(destination)
            if (width, height, image_format) != (1920, 1080, "PNG"):
                raise FileExistsError("IMG_CANARY_NORMALIZED_DESTINATION_CONFLICT")
            if not receipt_path.is_file() or receipt_path.is_symlink():
                raise FileExistsError("IMG_CANARY_NORMALIZATION_RECEIPT_MISSING")
            try:
                persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise FileExistsError("IMG_CANARY_NORMALIZATION_RECEIPT_INVALID") from exc
            expected_hash = ai_image_stable_hash(
                {key: value for key, value in persisted.items() if key != "content_hash"}
            )
            expected_crop = {
                "x": crop_x,
                "y": crop_y,
                "width": crop_width,
                "height": crop_height,
                "target_aspect_ratio": "16:9",
            }
            if (
                persisted.get("content_hash") != expected_hash
                or persisted.get("source_sha256") != source_checksum
                or persisted.get("crop_plan") != expected_crop
                or persisted.get("target_path") != str(destination)
                or persisted.get("target_sha256")
                != GoogleGeminiImageAdapter._file_sha256(destination)
                or persisted.get("target_width") != 1920
                or persisted.get("target_height") != 1080
                or persisted.get("target_format") != "PNG"
            ):
                raise FileExistsError("IMG_CANARY_NORMALIZED_DESTINATION_SOURCE_CONFLICT")
            return persisted
        if part.exists():
            raise FileExistsError("IMG_CANARY_NORMALIZATION_PART_CONFLICT")
        if receipt_path.exists():
            raise FileExistsError("IMG_CANARY_NORMALIZATION_RECEIPT_CONFLICT")
        argv = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-n",
            "-i",
            str(source),
            "-vf",
            (
                f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y},"
                "scale=1920:1080:flags=lanczos,format=rgb24"
            ),
            "-frames:v",
            "1",
            "-f",
            "image2",
            "-vcodec",
            "png",
            str(part),
        ]
        try:
            completed = subprocess.run(argv, capture_output=True, text=True, shell=False)
            if completed.returncode != 0:
                raise RuntimeError(f"IMG_CANARY_NORMALIZATION_FFMPEG_FAILED:{completed.returncode}")
            if not part.is_file():
                raise RuntimeError("IMG_CANARY_NORMALIZATION_PART_MISSING")
            with part.open("rb+") as stream:
                os.fsync(stream.fileno())
            width, height, image_format = GoogleGeminiImageAdapter.probe_image(part)
            if (width, height, image_format) != (1920, 1080, "PNG"):
                raise ValueError("IMG_CANARY_NORMALIZED_OUTPUT_SHAPE_INVALID")
            receipt = self._receipt(
                source=source,
                destination=destination,
                target_file=part,
                source_checksum=source_checksum,
                source_width=source_width,
                source_height=source_height,
                source_format=source_format,
                crop=(crop_x, crop_y, crop_width, crop_height),
                command=argv,
                already_normalized=False,
            )
            IMGCanaryArtifactWriter._write_json(receipt_path, receipt)
            os.replace(part, destination)
            _fsync_parent_directory(destination)
        except Exception:
            part.unlink(missing_ok=True)
            if not destination.exists():
                receipt_path.unlink(missing_ok=True)
            raise
        return receipt

    @staticmethod
    def _receipt(
        *,
        source: Path,
        destination: Path,
        target_file: Path | None = None,
        part_path_remaining: bool = False,
        source_checksum: str,
        source_width: int,
        source_height: int,
        source_format: str,
        crop: tuple[int, int, int, int],
        command: list[str],
        already_normalized: bool,
    ) -> dict[str, Any]:
        crop_x, crop_y, crop_width, crop_height = crop
        payload: dict[str, Any] = {
            "source_path": str(source),
            "source_sha256": source_checksum,
            "source_width": source_width,
            "source_height": source_height,
            "source_format": source_format,
            "crop_plan": {
                "x": crop_x,
                "y": crop_y,
                "width": crop_width,
                "height": crop_height,
                "target_aspect_ratio": "16:9",
            },
            "effective_width_after_crop": crop_width,
            "effective_height_after_crop": crop_height,
            "target_path": str(destination),
            "target_sha256": GoogleGeminiImageAdapter._file_sha256(
                target_file or destination
            ),
            "target_width": 1920,
            "target_height": 1080,
            "target_format": "PNG",
            "upscale_applied": False,
            "command": command,
            "already_normalized": already_normalized,
            "part_path_remaining": part_path_remaining,
        }
        return {**payload, "content_hash": ai_image_stable_hash(payload)}


class IMGCanaryArtifactWriter:
    def __init__(self, workspace_root: Path):
        self.root = workspace_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_plan_bundle(self, bundle: IMGCanaryPlanBundle) -> dict[str, Path]:
        artifacts: dict[str, Any] = {
            "run-identity.json": bundle.run_identity,
            "scene-requirements.json": bundle.requirements,
            "visual-source-decision.json": bundle.decision,
            "visual-direction-contract.json": bundle.visual_direction,
            "native-headline.json": bundle.headline,
            "native-overlay-plan.json": bundle.overlay_plan,
            "cost-estimate.json": bundle.cost,
            "ai-image-request.json": bundle.generic_request,
            "compiled-image-prompt.json": bundle.compiled_prompt,
            "gemini-image-request.json": bundle.provider_request,
            "operator-approval.json": bundle.approval,
            "execution-gates.json": bundle.execution_gates,
        }
        if bundle.serialized_request_evidence is not None:
            artifacts["serialized-request-evidence.json"] = (
                bundle.serialized_request_evidence
            )
        if bundle.v2_approval_binding is not None:
            artifacts["operator-approval-v2-binding.json"] = (
                bundle.v2_approval_binding
            )
        if bundle.v3_approval_binding is not None:
            artifacts["operator-approval-v3-binding.json"] = (
                bundle.v3_approval_binding
            )
        paths: dict[str, Path] = {}
        for name, artifact in artifacts.items():
            path = self._child("manifests", name)
            payload = artifact.model_dump(mode="json")
            self._write_json(path, payload)
            paths[name] = path
        return paths

    def write_preflight(self, evidence: IMGCanaryPreflightEvidence) -> Path:
        path = self._child("manifests", "preflight.json")
        self._write_json(path, evidence.model_dump(mode="json"))
        return path

    def build_pending_human_packet(
        self,
        *,
        run_id: str,
        original_image_path: Path,
        normalized_image_path: Path,
        review_mp4_path: Path,
        drive_archive_receipt: DriveArchiveReceipt,
        archive_manifest: ProductionArchiveManifest,
        archive_manifest_path: Path,
        attempt_ledger: IMGCanaryAttemptLedger,
        vqc_report: ImageVisualQualityControlReport,
        render_execution_receipt: NativeRenderExecutionReceipt,
        estimated_cost_usd: Decimal,
        actual_cost_usd: Decimal | None,
        ambiguities: list[str] | None = None,
    ) -> IMGCanaryHumanReviewPacket:
        resolved_local_paths: list[Path] = []
        for path in (original_image_path, normalized_image_path, review_mp4_path):
            resolved = path.resolve(strict=True)
            self._require_child(resolved)
            resolved_local_paths.append(resolved)
        manifest_hash = stable_hash(
            archive_manifest.model_dump(mode="json", exclude={"manifest_hash"})
        )
        if (
            archive_manifest.manifest_hash != manifest_hash
            or not archive_manifest.required_roles_complete
            or archive_manifest.provider_execution_allowed
            or archive_manifest.total_size_bytes
            != sum(item.size_bytes for item in archive_manifest.files)
        ):
            raise ValueError("IMG_CANARY_HUMAN_REVIEW_ARCHIVE_MANIFEST_INVALID")
        manifest_by_role = {
            item.logical_role: item for item in archive_manifest.files
        }
        expected_local_roles = (
            "IMG_CANARY_ORIGINAL_IMAGE",
            "IMG_CANARY_NORMALIZED_IMAGE",
            "IMG_CANARY_REVIEW_MP4",
        )
        required_archive_roles = (
            IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES
            if run_id.startswith("img-canary-v3-")
            else IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES
            if run_id.startswith("img-canary-v2-")
            else IMG_CANARY_REQUIRED_ARCHIVE_ROLES
        )
        if set(manifest_by_role) != set(required_archive_roles):
            raise ValueError("IMG_CANARY_HUMAN_REVIEW_ARCHIVE_ROLE_SET_INVALID")
        for role, resolved in zip(expected_local_roles, resolved_local_paths):
            entry = manifest_by_role[role]
            if (
                Path(entry.source_path).resolve() != resolved
                or entry.size_bytes != resolved.stat().st_size
                or entry.sha256 != GoogleGeminiImageAdapter._file_sha256(resolved)
            ):
                raise ValueError("IMG_CANARY_HUMAN_REVIEW_LOCAL_ARTIFACT_MISMATCH")
        resolved_manifest_path = archive_manifest_path.resolve(strict=True)
        self._require_child(resolved_manifest_path)
        canonical_manifest_bytes = (
            json.dumps(
                archive_manifest.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        if resolved_manifest_path.read_bytes() != canonical_manifest_bytes:
            raise ValueError("IMG_CANARY_HUMAN_REVIEW_ARCHIVE_MANIFEST_BYTES_INVALID")
        expected_drive_hash = stable_hash(
            drive_archive_receipt.model_dump(mode="json", exclude={"receipt_hash"})
        )
        receipt_path_list = [item.archive_path for item in drive_archive_receipt.files]
        receipt_paths = set(receipt_path_list)
        expected_archive_paths = {
            item.expected_archive_path for item in archive_manifest.files
        }
        canonical_manifest_archive_path = "00-manifests/production-archive-manifest.json"
        expected_archive_paths.add(canonical_manifest_archive_path)
        if (
            drive_archive_receipt.receipt_hash != expected_drive_hash
            or drive_archive_receipt.archive_manifest_ref != archive_manifest.manifest_id
            or drive_archive_receipt.archive_manifest_hash != archive_manifest.manifest_hash
            or drive_archive_receipt.archive_state != "VERIFIED"
            or drive_archive_receipt.mismatch_reason_codes
            or not drive_archive_receipt.files
            or not all(item.verified for item in drive_archive_receipt.files)
            or len(receipt_path_list) != len(receipt_paths)
            or receipt_paths != expected_archive_paths
            or not drive_archive_receipt.provider_call_made
            or drive_archive_receipt.transport != "GOOGLE_DRIVE_API"
            or run_id not in drive_archive_receipt.root_relative_folder_path
        ):
            raise ValueError("IMG_CANARY_HUMAN_REVIEW_DRIVE_ARCHIVE_NOT_VERIFIED")
        manifest_entry_by_path = {
            item.expected_archive_path: item for item in archive_manifest.files
        }
        expected_manifest_size = len(canonical_manifest_bytes)
        expected_manifest_sha256 = hashlib.sha256(canonical_manifest_bytes).hexdigest()
        expected_manifest_md5 = hashlib.md5(
            canonical_manifest_bytes,
            usedforsecurity=False,
        ).hexdigest()
        for receipt_item in drive_archive_receipt.files:
            if receipt_item.archive_path == canonical_manifest_archive_path:
                expected_size = expected_manifest_size
                expected_sha256 = expected_manifest_sha256
                expected_md5 = expected_manifest_md5
            else:
                manifest_entry = manifest_entry_by_path.get(receipt_item.archive_path)
                if manifest_entry is None:
                    raise ValueError("IMG_CANARY_HUMAN_REVIEW_DRIVE_ITEM_NOT_IN_MANIFEST")
                expected_size = manifest_entry.size_bytes
                expected_sha256 = manifest_entry.sha256.lower()
                expected_md5 = (
                    manifest_entry.md5.lower() if manifest_entry.md5 is not None else None
                )
            sha256_verified = bool(
                receipt_item.drive_sha256
                and receipt_item.drive_sha256.lower() == expected_sha256
            )
            md5_verified = bool(
                not receipt_item.drive_sha256
                and receipt_item.drive_md5
                and expected_md5
                and receipt_item.drive_md5.lower() == expected_md5
            )
            if (
                not receipt_item.drive_file_id
                or receipt_item.local_size != expected_size
                or receipt_item.drive_size != expected_size
                or receipt_item.local_sha256.lower() != expected_sha256
                or (
                    expected_md5 is not None
                    and (
                        receipt_item.local_md5 is None
                        or receipt_item.local_md5.lower() != expected_md5
                    )
                )
                or not (sha256_verified or md5_verified)
            ):
                raise ValueError("IMG_CANARY_HUMAN_REVIEW_DRIVE_ITEM_MANIFEST_MISMATCH")
        expected_total_size = archive_manifest.total_size_bytes + expected_manifest_size
        if (
            drive_archive_receipt.total_local_size != expected_total_size
            or drive_archive_receipt.total_drive_size != expected_total_size
        ):
            raise ValueError("IMG_CANARY_HUMAN_REVIEW_DRIVE_TOTAL_SIZE_MISMATCH")
        if (
            attempt_ledger.content_hash
            != ai_image_stable_hash(
                attempt_ledger.model_dump(mode="json", exclude={"content_hash"})
            )
            or attempt_ledger.run_id != run_id
            or attempt_ledger.status != "SUCCEEDED"
            or attempt_ledger.attempts_consumed != 1
            or not attempt_ledger.provider_call_made
        ):
            raise ValueError("IMG_CANARY_HUMAN_REVIEW_ATTEMPT_LEDGER_INVALID")
        if (
            vqc_report.content_hash
            != ai_image_stable_hash(
                vqc_report.model_dump(mode="json", exclude={"content_hash"})
            )
            or vqc_report.run_id != run_id
            or vqc_report.technical_status != "PASS"
            or not vqc_report.archive_eligible_for_review
            or any(gate.result == "BLOCK" for gate in vqc_report.gate_results)
        ):
            raise ValueError("IMG_CANARY_HUMAN_REVIEW_VQC_INVALID")
        expected_render_hash = stable_hash(
            render_execution_receipt.model_dump(mode="python", exclude={"receipt_hash"})
        )
        if (
            render_execution_receipt.receipt_hash != expected_render_hash
            or render_execution_receipt.run_key != run_id
            or Path(render_execution_receipt.output_path).resolve()
            != resolved_local_paths[2]
            or render_execution_receipt.output_checksum
            != GoogleGeminiImageAdapter._file_sha256(resolved_local_paths[2])
            or render_execution_receipt.production_eligible
            or not render_execution_receipt.no_provider_calls_confirmed
        ):
            raise ValueError("IMG_CANARY_HUMAN_REVIEW_RENDER_RECEIPT_INVALID")
        if estimated_cost_usd > IMG_CANARY_HARD_CAP_USD or (
            actual_cost_usd is not None and actual_cost_usd > IMG_CANARY_HARD_CAP_USD
        ):
            raise ValueError("IMG_CANARY_HUMAN_REVIEW_COST_CAP_EXCEEDED")
        payload: dict[str, Any] = {
            "run_id": run_id,
            "review_state": "PENDING",
            "original_image_path": str(original_image_path.resolve()),
            "normalized_image_path": str(normalized_image_path.resolve()),
            "review_mp4_path": str(review_mp4_path.resolve()),
            "drive_archive_receipt_ref": (
                f"drive-archive-receipt://img-canary/{run_id}/{drive_archive_receipt.receipt_hash}"
            ),
            "drive_archive_receipt_hash": drive_archive_receipt.receipt_hash,
            "drive_archive_manifest_ref": drive_archive_receipt.archive_manifest_ref,
            "archive_verified": True,
            "drive_provider_call_made": True,
            "provider_attempts_consumed": 1,
            "estimated_cost_usd": estimated_cost_usd,
            "actual_cost_usd": actual_cost_usd,
            "checklist": {name: False for name in IMG_CANARY_REVIEW_CHECKLIST},
            "generated_artifact_ambiguities": list(ambiguities or []),
            "production_eligible": False,
            "not_publishable": True,
            "proceed_to_ch1_flex_v2": False,
        }
        packet = IMGCanaryHumanReviewPacket(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )
        self._write_json(
            self._child("review", "human-review-packet.json"),
            packet.model_dump(mode="json"),
        )
        return packet

    def _child(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts).resolve(strict=False)
        self._require_child(path)
        return path

    def _require_child(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("IMG_CANARY_WORKSPACE_PATH_ESCAPE") from exc

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.unlink(missing_ok=True)
        serialized = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        try:
            with part.open("x", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(part, path)
            _fsync_parent_directory(path)
        except Exception:
            part.unlink(missing_ok=True)
            raise


__all__ = [
    "IMG_CANARY_HEADLINE",
    "IMG_CANARY_SCENE_ID",
    "IMGCanaryArtifactWriter",
    "IMGCanaryAttemptLedgerStore",
    "IMGCanaryImageNormalizer",
    "IMGCanaryNativeReviewPlanBuilder",
    "IMGCanaryPlanBuilder",
    "IMGCanaryPlanBundle",
    "IMGCanaryPreflightService",
]
