from __future__ import annotations

from dataclasses import dataclass

from app.contracts.asset_acquisition import (
    AssetRequest,
    ChannelVisualStrategyProfile,
    CompiledAssetRequestPlan,
    FormatIdentitySnapshot,
    ProviderUsagePolicy,
)
from app.contracts.native_renderer import NativeRenderPlan, NativeRenderScene
from app.contracts.temporal_authority import CanonicalMediaTimeline
from app.services.native_render_plan import NativeRenderPlanValidator, canonical_plan_hash, stable_hash


ROLE_PRIORITY = ("NATIVE_VISUAL", "SUPPORTING_STOCK", "AI_HERO")
NATIVE_TREATMENTS = {
    "NATIVE_SLIDE",
    "DIAGRAM",
    "UI_SIMULATION",
    "KINETIC_TYPOGRAPHY",
    "DATA_CARD",
    "QUOTE_SLIDE",
    "COMPARISON_SLIDE",
    "TIMELINE",
    "STATIC_COMPOSITION",
}
PROVIDER_BY_ROLE = {"NATIVE_VISUAL": "NATIVE", "SUPPORTING_STOCK": "PEXELS", "AI_HERO": "GOOGLE_VEO"}


@dataclass(frozen=True)
class CompilationEvidence:
    originality_manifest_ref: str
    originality_manifest_hash: str
    claim_ledger_refs: tuple[str, ...]
    disclosure_receipt_ref: str


class AssetRequestCompiler:
    """Compile semantic asset requests without selecting assets or calling providers."""

    def compile(
        self,
        plan: NativeRenderPlan,
        *,
        format_identity: FormatIdentitySnapshot,
        strategy_profile: ChannelVisualStrategyProfile,
        provider_policy: ProviderUsagePolicy,
        evidence: CompilationEvidence,
        canonical_timeline: CanonicalMediaTimeline | None = None,
    ) -> CompiledAssetRequestPlan:
        self._validate_inputs(
            plan,
            format_identity,
            strategy_profile,
            provider_policy,
            evidence,
            canonical_timeline=canonical_timeline,
        )
        requests = [
            self._compile_scene(plan, scene, format_identity=format_identity, provider_policy=provider_policy)
            for scene in plan.scenes
        ]
        counts = {role: sum(item.requested_role == role for item in requests) for role in ROLE_PRIORITY}
        payload = {
            "package_id": plan.package_id,
            "project_id": plan.video_project_id,
            "channel_id": plan.channel_id,
            "native_render_plan_ref": plan.plan_id,
            "native_render_plan_hash": plan.content_hash or canonical_plan_hash(plan),
            "format_identity_ref": format_identity.contract_ref,
            "format_identity_hash": format_identity.contract_hash,
            "strategy_profile_ref": strategy_profile.profile_ref,
            "strategy_profile_hash": strategy_profile.profile_hash,
            "requests": [request.model_dump(mode="json") for request in requests],
            "native_request_count": counts["NATIVE_VISUAL"],
            "supporting_stock_request_count": counts["SUPPORTING_STOCK"],
            "ai_hero_request_count": counts["AI_HERO"],
            "unresolved_request_count": 0,
            "provider_execution_allowed": False,
        }
        return CompiledAssetRequestPlan(**payload, content_hash=stable_hash(payload))

    @staticmethod
    def _validate_inputs(
        plan: NativeRenderPlan,
        format_identity: FormatIdentitySnapshot,
        strategy_profile: ChannelVisualStrategyProfile,
        provider_policy: ProviderUsagePolicy,
        evidence: CompilationEvidence,
        canonical_timeline: CanonicalMediaTimeline | None,
    ) -> None:
        if plan.temporal_authority_mode == "CANONICAL_STRICT":
            temporal_gate_names = {
                "CanonicalMediaTimelineReferenceGate",
                "ParallelTimingInputGate",
                "CanonicalSceneTimingSourceGate",
                "CanonicalCaptionTimingSourceGate",
                "CanonicalMediaTimelineEvidenceGate",
                "CanonicalMediaTimelineHashGate",
                "CanonicalAudioAssetGate",
                "CanonicalSceneTimingDerivationGate",
            }
            temporal_results = NativeRenderPlanValidator().validate(
                plan,
                canonical_timeline=canonical_timeline,
            )
            temporal_reasons = [
                reason
                for gate in temporal_results
                if gate.gate in temporal_gate_names and gate.verdict == "BLOCK"
                for reason in gate.reason_codes
            ]
            if temporal_reasons:
                raise ValueError(";".join(sorted(set(temporal_reasons))))
        if format_identity.status != "APPROVED":
            raise ValueError("FORMAT_IDENTITY_NOT_APPROVED")
        if plan.channel_id != format_identity.channel_id or plan.channel_id != strategy_profile.channel_id:
            raise ValueError("CHANNEL_SCOPE_MISMATCH")
        if strategy_profile.strategy_key == "NR2_B_BALANCED" and plan.channel_id != "small-team-ai":
            raise ValueError("STRATEGY_B_CHANNEL_SCOPE_VIOLATION")
        if not strategy_profile.native_is_backbone or not format_identity.native_explanatory_backbone_required:
            raise ValueError("NATIVE_BACKBONE_REQUIRED")
        if plan.character_policy_mode != format_identity.character_policy_mode or plan.character_policy_mode != strategy_profile.character_policy_mode:
            raise ValueError("CHARACTER_POLICY_CONFLICT")
        if plan.character_policy_mode != "NO_CHARACTER":
            raise ValueError("CHARACTER_POLICY_CONFLICT")
        if provider_policy.provider_execution_allowed:
            raise ValueError("AS1_PROVIDER_EXECUTION_MUST_BE_DISABLED")
        if not evidence.originality_manifest_ref or not evidence.originality_manifest_hash or not evidence.disclosure_receipt_ref:
            raise ValueError("ASSET_COMPILATION_EVIDENCE_INCOMPLETE")
        roles = {AssetRequestCompiler._scene_role(scene) for scene in plan.scenes}
        if not roles.issubset(set(format_identity.allowed_asset_roles)) or not roles.issubset(set(strategy_profile.allowed_roles)):
            raise ValueError("ASSET_ROLE_CONTRADICTS_FORMAT_IDENTITY")
        unsupported = sorted({PROVIDER_BY_ROLE[role] for role in roles} - set(provider_policy.supported_providers))
        if unsupported:
            raise ValueError(f"UNSUPPORTED_PROVIDER:{','.join(unsupported)}")

    def _compile_scene(
        self,
        plan: NativeRenderPlan,
        scene: NativeRenderScene,
        *,
        format_identity: FormatIdentitySnapshot,
        provider_policy: ProviderUsagePolicy,
    ) -> AssetRequest:
        role = self._scene_role(scene)
        provider_intent = (scene.provider_intent or PROVIDER_BY_ROLE[role]).upper()
        if provider_intent not in provider_policy.supported_providers:
            raise ValueError(f"UNSUPPORTED_PROVIDER:{provider_intent}")
        if provider_intent != PROVIDER_BY_ROLE[role]:
            raise ValueError("PROVIDER_ROLE_CONTRADICTION")
        orientation = "portrait" if plan.canvas_spec.height > plan.canvas_spec.width else "square" if plan.canvas_spec.height == plan.canvas_spec.width else "landscape"
        semantic_intent = scene.scene_notes.strip() or f"{scene.originality_role}: {scene.visual_treatment.replace('_', ' ').lower()}"
        purpose = self._purpose(scene)
        duration = scene.duration_ms / 1000
        minimum_duration = 0 if role == "NATIVE_VISUAL" else min(duration, 4)
        maximum_duration = duration if role != "AI_HERO" else min(8, max(4, duration))
        request_payload = {
            "request_id": f"asset-{scene.scene_id}",
            "scene_id": scene.scene_id,
            "source_segment_ids": scene.source_segment_ids,
            "purpose": purpose,
            "requested_role": role,
            "semantic_visual_intent": semantic_intent,
            "required_orientation": orientation,
            "minimum_resolution": "1280x720" if orientation == "landscape" else "720x1280" if orientation == "portrait" else "720x720",
            "preferred_resolution": f"{plan.canvas_spec.width}x{plan.canvas_spec.height}",
            "minimum_duration_seconds": minimum_duration,
            "maximum_duration_seconds": maximum_duration,
            "crop_policy": "SAFE_CENTER_CROP_WITH_SEMANTIC_REVIEW" if role != "NATIVE_VISUAL" else "NATIVE_CANVAS_EXACT",
            "person_policy": "NO_RECURRING_HOST" if role == "SUPPORTING_STOCK" else format_identity.character_policy_mode,
            "logo_text_policy": "REJECT_VISIBLE_LOGO_OR_EMBEDDED_TEXT" if role != "NATIVE_VISUAL" else "NATIVE_TEXT_ONLY",
            "evidence_usage_policy": "NOT_FACTUAL_EVIDENCE" if role != "NATIVE_VISUAL" else "CLAIM_LEDGER_BOUND",
            "fallback_order": [role, "NATIVE_VISUAL"] if role != "NATIVE_VISUAL" else ["NATIVE_VISUAL"],
            "projected_cost_class": "NONE" if role == "NATIVE_VISUAL" else "LOW" if role == "SUPPORTING_STOCK" else "MEDIUM",
            "human_review_required": role != "NATIVE_VISUAL",
        }
        return AssetRequest(**request_payload, request_hash=stable_hash(request_payload))

    @staticmethod
    def _scene_role(scene: NativeRenderScene) -> str:
        if scene.visual_treatment in NATIVE_TREATMENTS:
            return "NATIVE_VISUAL"
        if scene.visual_treatment == "STOCK_VIDEO":
            return "SUPPORTING_STOCK"
        if scene.visual_treatment == "AI_HERO_VIDEO":
            return "AI_HERO"
        raise ValueError(f"ASSET_TREATMENT_UNSUPPORTED:{scene.visual_treatment}")

    @staticmethod
    def _purpose(scene: NativeRenderScene) -> str:
        role = AssetRequestCompiler._scene_role(scene)
        if role == "AI_HERO":
            mapping = {
                "HOOK": "HOOK",
                "METAPHOR": "METAPHOR",
                "EMOTIONAL_PAYOFF": "EMOTIONAL_PAYOFF",
                "VISUAL_SIGNATURE": "VISUAL_SIGNATURE",
                "NATIVE_MOTION_INSUFFICIENT": "NATIVE_MOTION_INSUFFICIENT",
            }
            purpose = mapping.get(scene.originality_role.upper())
            if not purpose:
                raise ValueError("AI_HERO_REASON_NOT_ALLOWED")
            return purpose
        return scene.originality_role.upper()
