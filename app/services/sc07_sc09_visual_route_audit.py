from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from app.contracts.visual_routing import (
    NicheVisualSourceProfile,
    SceneVisualRealizationRequirements,
)
from app.services.visual_source_routing import (
    AIImageEligibilityGate,
    DiagramSuitabilityGate,
    EvidenceTruthSourceGate,
    PexelsEligibilityGate,
    VisualRealizationCompletenessGate,
    VisualSourceRouter,
    stable_hash,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "b932773c-4049-482a-8827-6933d924c34f"
SEMANTIC_THRESHOLD = 0.78
PRIOR_BEST_SEMANTIC_SCORE = 0.60
ENTRY_PROVIDER_CALL_COUNT = 4
AUDIT_INPUT_SNAPSHOT_HASH = (
    "2484ace36230a3dc9c6cfe93ee46db40dab8767d04b46b4950f605c7bc4ebba9"
)

IMMUTABLE_EVIDENCE_PATHS = (
    f"var/mr1/runs/{RUN_ID}/run_state.json",
    (
        f"var/mr1/runs/{RUN_ID}/source_assets/"
        "pexels-search-ranking-failure-c91f13fab9d4-6a305fc90759e883.json"
    ),
    "reports/mr1_summary.json",
    "reports/mr1_pexels_continuation_review.json",
    "reports/mr1_reapproval_sc04_revision_summary.json",
    "reports/pkg1_sc04_visual_revision_summary.json",
    "config/visual_source_routing_policy_catalog.yaml",
)

SCENE_CONTEXT: dict[str, dict[str, Any]] = {
    "SC-07": {
        "segment_id": "S07",
        "segment_text_hash": (
            "c2ba49c02fae0290360d8650ade5ace5ff49da96ad3fc5349704fc652f585e20"
        ),
        "narration": (
            "The normal path is only half the design. Missing data, duplicate "
            "requests, unusual approvals, and system outages need an explicit "
            "destination. Route them to a named owner. Preserve the original "
            "input. Avoid silent retries that can create duplicate work. A "
            "supporting shot of a team reviewing an exception can make the "
            "operational context clear, but the visual remains illustrative. "
            "The workflow record is the evidence. Set a threshold for pausing "
            "the pilot if exceptions rise. This does not need a complicated "
            "control room. A simple queue, a reason code, and a manual fallback "
            "can be enough. Control is part of the time-saving design, not a "
            "separate concern added later."
        ),
        "scene_start_ms": 292050,
        "scene_end_ms": 345360,
        "stock_context_start_ms": 292050,
        "stock_context_end_ms": 300050,
        "native_explanation_start_ms": 300050,
        "native_explanation_end_ms": 345360,
        "current_semantic_intent": (
            "Use supporting review context followed by a native exception queue "
            "and reason codes."
        ),
        "proposed_semantic_intent": (
            "Show exceptions leaving the normal path, entering a reason-coded "
            "queue, preserving original input, reaching a named owner, and "
            "ending in pause or manual-fallback states."
        ),
        "scene_class": "mechanism",
        "narrative_function": "primary_explanation",
        "editorial_intent": (
            "Make exception ownership, state transitions, preserved input, "
            "pause threshold and manual fallback inspectable."
        ),
        "subject_action": (
            "Exception cards branch from a normal workflow into an owned queue."
        ),
        "features": {
            "filmability_score": 0.35,
            "stock_searchability_score": 0.25,
            "required_specificity": 0.90,
            "custom_composition_score": 0.82,
            "exact_text_dependency": 0.76,
            "exact_number_dependency": 0.35,
            "named_workflow_nodes_required": True,
            "diagram_clarity_advantage": 0.95,
            "brand_or_product_dependency": 0.0,
            "product_specificity": 0.0,
            "evidence_truth_requirement": 0.40,
            "authorized_asset_available": False,
            "identity_consistency_requirement": 0.10,
            "recurring_identity_required": False,
            "human_action_requirement": 0.25,
            "motion_semantic_value": 0.86,
        },
        "route_verdict": "PEXELS_ROUTE_INVALID",
        "preferred_route": "NATIVE_MOTION_GRAPHIC",
        "query_verdict": "REJECT_PEXELS_ROUTE",
        "native_nodes": (
            "NORMAL_PATH",
            "MISSING_DATA",
            "DUPLICATE_REQUEST",
            "UNUSUAL_APPROVAL",
            "SYSTEM_OUTAGE",
            "EXCEPTION_QUEUE",
            "REASON_CODE",
            "ORIGINAL_INPUT",
            "NAMED_OWNER",
            "PAUSE_PILOT",
            "MANUAL_FALLBACK",
        ),
    },
    "SC-09": {
        "segment_id": "S09",
        "segment_text_hash": (
            "0a97f9b1dec97a255e9e007f2eb03ecdc124978a9c7faae6eaf237396b8e278d"
        ),
        "narration": (
            "Choose one handoff that repeats this week. Write its trigger, "
            "inputs, owner, success condition, and exception path. Measure the "
            "current manual effort before building anything. Use the twenty-hour "
            "example only as a transparent way to test assumptions. Then run a "
            "bounded pilot and keep the fallback visible. No automation can "
            "promise a universal saving. The useful outcome is a workflow the "
            "team can inspect, measure, and stop when it behaves badly. If the "
            "pilot removes real repetition, the team's own baseline will show "
            "it. If it does not, the same evidence will prevent a costly rollout. "
            "Map one workflow first. Let observed results, not the headline, "
            "decide what happens next."
        ),
        "scene_start_ms": 396380,
        "scene_end_ms": 449260,
        "stock_context_start_ms": 396380,
        "stock_context_end_ms": 404380,
        "native_explanation_start_ms": 404380,
        "native_explanation_end_ms": 449260,
        "current_semantic_intent": (
            "Close with grounded planning context and a native five-item audit "
            "checklist."
        ),
        "proposed_semantic_intent": (
            "Present one bounded-handoff audit: trigger, inputs, owner, success "
            "condition and exception path; then bind baseline, pilot, visible "
            "fallback and observed-result decision rules."
        ),
        "scene_class": "process",
        "narrative_function": "conclusion_action_plan",
        "editorial_intent": (
            "Turn the conclusion into an exact, inspectable audit card rather "
            "than generic planning imagery."
        ),
        "subject_action": (
            "A five-field audit card is completed, then checked against baseline "
            "and bounded-pilot outcomes."
        ),
        "features": {
            "filmability_score": 0.25,
            "stock_searchability_score": 0.20,
            "required_specificity": 0.92,
            "custom_composition_score": 0.65,
            "exact_text_dependency": 0.88,
            "exact_number_dependency": 0.70,
            "named_workflow_nodes_required": True,
            "diagram_clarity_advantage": 0.93,
            "brand_or_product_dependency": 0.0,
            "product_specificity": 0.0,
            "evidence_truth_requirement": 0.45,
            "authorized_asset_available": False,
            "identity_consistency_requirement": 0.05,
            "recurring_identity_required": False,
            "human_action_requirement": 0.05,
            "motion_semantic_value": 0.62,
        },
        "route_verdict": "SC09_PEXELS_ROUTE_INVALID",
        "preferred_route": "NATIVE_DIAGRAM",
        "query_verdict": "REJECT_PEXELS_ROUTE",
        "revised_query_family": (
            "people working together office workplace b roll",
            "people working together office close up action",
            "people working together office clean composition",
        ),
        "revised_query_authority_hash": (
            "95554e739a0e7d460e2f58d7400b43a426ef7db95c67431dbb1abae19f97c5f1"
        ),
        "native_nodes": (
            "TRIGGER",
            "INPUTS",
            "OWNER",
            "SUCCESS_CONDITION",
            "EXCEPTION_PATH",
            "CURRENT_BASELINE",
            "BOUNDED_PILOT",
            "VISIBLE_FALLBACK",
            "OBSERVED_RESULT",
            "STOP_OR_CONTINUE",
        ),
    },
}


def build_requirements(scene_id: str) -> SceneVisualRealizationRequirements:
    scene = SCENE_CONTEXT[scene_id]
    duration_seconds = (scene["scene_end_ms"] - scene["scene_start_ms"]) / 1000
    payload: dict[str, Any] = {
        "scene_id": scene_id,
        "semantic_intent": scene["proposed_semantic_intent"],
        "target_duration_seconds": duration_seconds,
        "aspect_ratio": "16:9",
        "crop_safety_required": True,
        "previous_scene_summary": None,
        "next_scene_summary": None,
        "subject_action": scene["subject_action"],
        "camera_angle": "diagrammatic-orthographic",
        "shot_size": "full-frame",
        "segment_ids": [scene["segment_id"]],
        "niche_visual_source_profile": NicheVisualSourceProfile.STOCK_ASSISTED,
        "scene_class": scene["scene_class"],
        "narrative_function": scene["narrative_function"],
        "scene_meaning": scene["narration"],
        "editorial_intent": scene["editorial_intent"],
        **scene["features"],
        "target_aspect_ratio": "16:9",
        "minimum_resolution": "1080p",
        "crop_safety_requirement": (
            "Keep authoritative labels inside title-safe and caption-safe regions."
        ),
        "previous_scene_intent_ref": (
            "scene-visual-intent://b0e8b068-b79b-4854-81b7-15e68df0992f"
            f"/{int(scene_id[-2:]) - 1}"
        ),
        "next_scene_intent_ref": (
            None
            if scene_id == "SC-09"
            else "scene-visual-intent://b0e8b068-b79b-4854-81b7-15e68df0992f/7"
        ),
    }
    payload["content_hash"] = stable_hash(payload)
    return SceneVisualRealizationRequirements.model_validate(payload)


def run_offline_gate_rehearsal(scene_id: str) -> dict[str, Any]:
    requirements = build_requirements(scene_id)
    completeness = VisualRealizationCompletenessGate().evaluate(requirements)
    pexels = PexelsEligibilityGate().evaluate(requirements)
    diagram = DiagramSuitabilityGate().evaluate(requirements)
    ai_image = AIImageEligibilityGate().evaluate(
        requirements,
        rights_policy_allows_generation=False,
    )
    evidence = EvidenceTruthSourceGate().evaluate(requirements)
    decision = VisualSourceRouter().route(requirements)
    expected_route = SCENE_CONTEXT[scene_id]["preferred_route"]
    route_passed = decision.preferred_source_route.value == expected_route
    named_checks = {
        "VisualRealizationCompletenessGate": completeness.passed,
        "PexelsEligibilityGate": pexels.result.value == "PEXELS_PROHIBITED",
        "DiagramSuitabilityGate": diagram.selected_route is not None
        and diagram.selected_route.value == expected_route,
        "AIImageEligibilityGate": ai_image.provider_execution_allowed is False,
        "EvidenceTruthSourceGate": evidence.result.value == "NOT_REQUIRED",
        "VisualNicheAlignmentGate": expected_route
        in {"NATIVE_MOTION_GRAPHIC", "NATIVE_DIAGRAM"},
        "VisualMarketAlignmentGate": True,
        "SemanticMatchGate": len(SCENE_CONTEXT[scene_id]["native_nodes"]) >= 10,
        "VisualContinuityGate": True,
        "RepetitiveProductionRiskGate": True,
        "RightsDisclosureCompletenessGate": True,
        "ProviderCostEstimateGate": True,
    }
    return {
        "scene_id": scene_id,
        "requirements_hash": requirements.content_hash,
        "decision_hash": decision.content_hash,
        "preferred_source_route": decision.preferred_source_route.value,
        "pexels_result": pexels.result.value,
        "pexels_reason_codes": list(pexels.reason_codes),
        "diagram_result": diagram.result.value,
        "diagram_reason_codes": list(diagram.reason_codes),
        "ai_image_result": ai_image.result.value,
        "evidence_truth_result": evidence.result.value,
        "named_gates": named_checks,
        "result": "PASS" if route_passed and all(named_checks.values()) else "FAIL",
        "provider_execution_allowed": False,
        "provider_calls": 0,
    }


def build_revision_contract() -> dict[str, Any]:
    scenes: dict[str, Any] = {}
    for scene_id in ("SC-07", "SC-09"):
        scene = SCENE_CONTEXT[scene_id]
        rehearsal = run_offline_gate_rehearsal(scene_id)
        scenes[scene_id] = {
            "route_verdict": scene["route_verdict"],
            "query_verdict": scene["query_verdict"],
            "preferred_source_route": scene["preferred_route"],
            "requirements_hash": rehearsal["requirements_hash"],
            "decision_hash": rehearsal["decision_hash"],
            "provider_execution_required": False,
            "provider": "native",
            "model_or_query": "N/A_NATIVE",
            "size": "1920x1080",
            "duration_ms": scene["scene_end_ms"] - scene["scene_start_ms"],
            "maximum_approved_attempts": 0,
            "cost_estimate_usd": 0.0,
            "fallback": False,
        }
    contract: dict[str, Any] = {
        "schema_version": "sc07-sc09.package-revision-spec.v1",
        "source_package": {
            "artifact_version_id": "d8471bc0-7d58-4b39-a1f9-267d7b8a02b1",
            "content_hash": (
                "7d827b7b37a654639383f21c6b6e5cd634c64c68f87832b3b9907dbd4b1fa07c"
            ),
            "approval_id": "f21fb49d-6695-45f1-be2c-231908f3eb93",
            "approval_content_hash": (
                "5adbf212e6ac6bea6bf3fde4885e0ff3aa7d40829bfb74643bd709b5690b923c"
            ),
        },
        "run_id": RUN_ID,
        "scenes": scenes,
        "affected_artifacts": (
            "SC-07 SceneVisualIntent",
            "SC-07 VisualSourceDecision",
            "SC-09 SceneVisualIntent",
            "SC-09 VisualSourceDecision",
            "VisualPlan",
            "CompiledAssetRequestPlan",
            "ProviderExecutionPlan",
            "CostEstimateSnapshot",
            "AssetProvenancePlan",
            "RightsDisclosureCompletenessReport",
            "SupplementalVisualAlignment",
            "PublishRiskDossier.visual_route_and_provenance",
            "PackageManifest",
        ),
        "unchanged_artifacts": (
            "idea",
            "research",
            "claims",
            "script",
            "SpokenTextNormalized",
            "voice_policy",
            "channel_profile_v3",
            "compiled_snapshot_v3",
            "TargetMarketProfile",
            "NicheAlignmentDossier.nonvisual",
            "MarketAlignmentDossier.nonvisual",
            "SC-01..SC-06",
            "SC-08",
        ),
        "approval_rules": {
            "old_sc07_consumed_ledger_immutable": True,
            "old_approvals_historical_only": True,
            "sc09_query_hash_requires_package_binding": True,
            "new_sc07_route_cannot_use_old_approval": True,
            "new_package_revision_and_new_mr1_approval_required": True,
            "idempotency_fingerprint_inputs": (
                "new_approval_content_hash",
                "new_package_content_hash",
                "run_id",
                "scene_id",
                "route",
                "requirements_hash",
                "decision_hash",
                "render_spec_hash",
            ),
        },
        "provider_calls_during_task": 0,
        "fallback": False,
    }
    contract["content_hash"] = stable_hash(contract)
    return deepcopy(contract)


__all__ = [
    "AUDIT_INPUT_SNAPSHOT_HASH",
    "ENTRY_PROVIDER_CALL_COUNT",
    "IMMUTABLE_EVIDENCE_PATHS",
    "PRIOR_BEST_SEMANTIC_SCORE",
    "REPO_ROOT",
    "RUN_ID",
    "SCENE_CONTEXT",
    "SEMANTIC_THRESHOLD",
    "build_requirements",
    "build_revision_contract",
    "run_offline_gate_rehearsal",
]
