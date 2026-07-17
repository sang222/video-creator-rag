from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from pydantic import BaseModel, ValidationError

from app.contracts.visual_routing import (
    AIImageEligibilityAssessment,
    AIImageEligibilityResult,
    ArchiveReuseAssessment,
    ArchiveReuseResult,
    DiagramSuitabilityAssessment,
    DiagramSuitabilityResult,
    EvidenceTruthAssessment,
    EvidenceTruthResult,
    NicheVisualSourceProfile,
    PexelsEligibilityAssessment,
    PexelsEligibilityResult,
    SceneVisualRealizationRequirements,
    SourceFallbackClass,
    VisualDecisionStatus,
    VisualSourceDecision,
    VisualSourceRoute,
    VisualSourceRoutingPolicyCatalogItem,
)


DEFAULT_POLICY_CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "visual_source_routing_policy_catalog.yaml"
)
POLICY_REF_SCHEME = "visual-routing-policy"
DECISION_VERSION = "vsr1.visual-source-decision.v1"

_AI_IMAGE_ROUTES = frozenset(
    {
        VisualSourceRoute.AI_GENERATED_IMAGE,
        VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
    }
)
_PEXELS_ROUTES = frozenset({VisualSourceRoute.PEXELS_VIDEO, VisualSourceRoute.PEXELS_PHOTO})
_VEO_ROUTES = frozenset({VisualSourceRoute.VEO_TEXT_TO_VIDEO, VisualSourceRoute.VEO_IMAGE_TO_VIDEO})
_PROVIDER_ROUTES = _PEXELS_ROUTES | _AI_IMAGE_ROUTES | _VEO_ROUTES
_NATIVE_ROUTES = frozenset(
    {
        VisualSourceRoute.NATIVE_DIAGRAM,
        VisualSourceRoute.NATIVE_MOTION_GRAPHIC,
        VisualSourceRoute.EDITORIAL_TEXT_GRAPHIC,
    }
)
_AUTHORIZED_ROUTES = frozenset(
    {
        VisualSourceRoute.AUTHORIZED_UI_OR_PRODUCT_ASSET,
        VisualSourceRoute.HUMAN_SUPPLIED_ASSET,
    }
)
_RESOLUTION_ORDER = {"1080p": 0, "1440p": 1, "2160p": 2}


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    raise TypeError(f"VSR1_NON_CANONICAL_HASH_VALUE:{type(value).__name__}")


def stable_hash(value: Any) -> str:
    """Return a deterministic SHA-256 over canonical, sorted JSON."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unique(reason_codes: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(code) for code in reason_codes if str(code).strip()))


def _model_payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _requirements_payload(requirements: SceneVisualRealizationRequirements) -> dict[str, Any]:
    return requirements.model_dump(mode="json", exclude={"content_hash"})


def _assessment_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "content_hash"}


@dataclass(frozen=True)
class GateEvaluation:
    passed: bool
    reason_codes: tuple[str, ...]
    content_hash: str

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "passed": self.passed,
            "reason_codes": list(self.reason_codes),
            "content_hash": self.content_hash,
        }


class VisualSourceRoutingPolicyCatalog:
    """Load and strictly validate the inactive VSR1 repository policy.

    This class reads one local YAML file. It has no activation, persistence or
    provider behavior.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else DEFAULT_POLICY_CATALOG_PATH
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("VSR1_POLICY_CATALOG_INVALID")
        self._raw = raw
        self._item = self._validate(raw)
        self.typed_item = VisualSourceRoutingPolicyCatalogItem.model_validate(self._item)
        self.catalog_hash = stable_hash(raw)
        self.policy_hash = stable_hash(self._item)
        self.policy_version = str(self._item["policy_version"])
        self.policy_ref = (
            f"{POLICY_REF_SCHEME}://{self._item['key']}/{self.policy_version}"
        )

    @staticmethod
    def _validate(raw: Mapping[str, Any]) -> dict[str, Any]:
        if raw.get("catalog_key") != "visual_source_routing_policy_catalog":
            raise ValueError("VSR1_POLICY_CATALOG_KEY_INVALID")
        if str(raw.get("status", "")).lower() != "draft":
            raise ValueError("VSR1_POLICY_CATALOG_MUST_BE_DRAFT")
        items = raw.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ValueError("VSR1_POLICY_ITEM_NOT_UNIQUE")
        item = dict(items[0])
        lifecycle = item.get("lifecycle")
        if not isinstance(lifecycle, dict):
            raise ValueError("VSR1_POLICY_LIFECYCLE_MISSING")
        if (
            lifecycle.get("state") != "INACTIVE"
            or lifecycle.get("fixture_only") is not True
            or lifecycle.get("channel_profile_binding_allowed") is not False
            or lifecycle.get("provider_execution_allowed") is not False
        ):
            raise ValueError("VSR1_POLICY_LIFECYCLE_NOT_FAIL_CLOSED")

        global_policy = item.get("visual_source_policy")
        if not isinstance(global_policy, dict):
            raise ValueError("VSR1_GLOBAL_VISUAL_SOURCE_POLICY_MISSING")
        if global_policy.get("minimum_output_resolution") != "1080p":
            raise ValueError("VSR1_MINIMUM_RESOLUTION_MUST_BE_1080P")
        if global_policy.get("allow_resolution_downgrade") is not False:
            raise ValueError("VSR1_RESOLUTION_DOWNGRADE_MUST_BE_DISABLED")
        if global_policy.get("auto_pexels_to_ai_failover") is not False:
            raise ValueError("VSR1_AUTO_PEXELS_TO_AI_FAILOVER_MUST_BE_DISABLED")
        if global_policy.get("final_composition_authority") != "native_ffmpeg":
            raise ValueError("VSR1_NATIVE_COMPOSITION_AUTHORITY_REQUIRED")
        if (
            global_policy.get("exact_text_authority") != "native_only"
            or global_policy.get("exact_number_authority") != "native_only"
        ):
            raise ValueError("VSR1_NATIVE_EXACT_CONTENT_AUTHORITY_REQUIRED")
        expected_invariants = {
            "generated_evidence_authority": False,
            "one_source_decision_per_scene": True,
            "new_vendor_requires_operator_approval": True,
            "existing_vendor_new_provider_route_requires_operator_approval": True,
            "maximum_automated_attempts_per_scene": 1,
        }
        if any(global_policy.get(key) != value for key, value in expected_invariants.items()):
            raise ValueError("VSR1_GLOBAL_POLICY_INVARIANT_INVALID")

        profiles = item.get("niche_visual_source_profiles")
        if not isinstance(profiles, list):
            raise ValueError("VSR1_PROFILE_SET_MISSING")
        profile_keys = [entry.get("key") for entry in profiles if isinstance(entry, dict)]
        expected_profiles = {profile.value for profile in NicheVisualSourceProfile}
        if len(profile_keys) != 4 or set(profile_keys) != expected_profiles:
            raise ValueError("VSR1_PROFILE_SET_INVALID")

        routes = item.get("source_routes")
        if not isinstance(routes, list):
            raise ValueError("VSR1_ROUTE_SET_MISSING")
        route_keys = [entry.get("key") for entry in routes if isinstance(entry, dict)]
        expected_routes = {route.value for route in VisualSourceRoute}
        if len(route_keys) != 13 or set(route_keys) != expected_routes:
            raise ValueError("VSR1_ROUTE_SET_INVALID")
        for route in routes:
            try:
                SourceFallbackClass(str(route["fallback_class"]))
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError("VSR1_ROUTE_FALLBACK_CLASS_INVALID") from exc
            if route.get("route_state") not in {
                "PLANNING_ONLY",
                "PLANNING_ONLY_PROVIDER_INACTIVE",
                "PROVIDER_NEUTRAL_PLANNING_ONLY",
                "BLOCKING",
            }:
                raise ValueError("VSR1_ROUTE_STATE_NOT_FAIL_CLOSED")
        route_state_by_key = {route["key"]: route["route_state"] for route in routes}
        if any(
            route_state_by_key[route.value] != "PLANNING_ONLY_PROVIDER_INACTIVE"
            for route in _AI_IMAGE_ROUTES
        ):
            raise ValueError("VSR1_IMAGE_ROUTE_STATE_NOT_INACTIVE")

        thresholds = item.get("routing_thresholds")
        if not isinstance(thresholds, dict):
            raise ValueError("VSR1_ROUTING_THRESHOLDS_MISSING")
        required_threshold_families = {
            "score_range",
            "pexels_eligible_if",
            "pexels_supporting_only_if",
            "pexels_prohibited_if",
            "evidence_truth_source_gate",
            "diagram_suitability_gate",
            "ai_image_eligibility_gate",
            "veo_routing_boundary",
        }
        if any(
            not isinstance(thresholds.get(key), dict)
            for key in required_threshold_families
        ):
            raise ValueError("VSR1_ROUTING_THRESHOLD_FAMILY_MISSING")
        if thresholds["score_range"] != {"minimum": 0.0, "maximum": 1.0}:
            raise ValueError("VSR1_ROUTING_SCORE_RANGE_INVALID")
        ai_policy = thresholds.get("ai_image_eligibility_gate")
        if not isinstance(ai_policy, dict) or ai_policy.get("provider_execution_enabled") is not False:
            raise ValueError("VSR1_IMAGE_PROVIDER_MUST_BE_INACTIVE")

        fallback_classes = item.get("fallback_classes")
        if not isinstance(fallback_classes, dict):
            raise ValueError("VSR1_FALLBACK_CLASSES_MISSING")
        pexels_only = fallback_classes.get(SourceFallbackClass.PEXELS_ONLY.value)
        if not isinstance(pexels_only, dict) or pexels_only.get("pexels_failure_opens_ai") is not False:
            raise ValueError("VSR1_PEXELS_FAILURE_MUST_NOT_OPEN_AI")

        pairs = item.get("forbidden_fallback_pairs")
        if not isinstance(pairs, list):
            raise ValueError("VSR1_FORBIDDEN_FALLBACK_PAIRS_MISSING")
        required_auto_blocks = {
            (source.value, target.value)
            for source in _PEXELS_ROUTES
            for target in _AI_IMAGE_ROUTES
        }
        recorded_auto_blocks = {
            (str(pair.get("from_route")), str(pair.get("to_route")))
            for pair in pairs
            if isinstance(pair, dict)
            and pair.get("scope") == "AUTOMATIC_AFTER_SEARCH_FAILURE"
            and pair.get("reason_code") == "AUTO_PEXELS_TO_AI_FAILOVER_FORBIDDEN"
        }
        if not required_auto_blocks.issubset(recorded_auto_blocks):
            raise ValueError("VSR1_PEXELS_TO_AI_FORBIDDEN_PAIR_INCOMPLETE")

        archive_policy = item.get("archive_reuse_policy")
        if (
            not isinstance(archive_policy, dict)
            or archive_policy.get("route") != VisualSourceRoute.ARCHIVED_ASSET_REUSE.value
            or archive_policy.get("binary_fetch_required_for_routing_fixture") is not False
        ):
            raise ValueError("VSR1_ARCHIVE_POLICY_INVALID")
        exact_policy = item.get("exact_text_and_native_overlay_policy")
        if not isinstance(exact_policy, dict) or any(
            exact_policy.get(key) is not expected
            for key, expected in {
                "generated_text_authority_allowed": False,
                "generated_number_authority_allowed": False,
                "generated_logo_allowed": False,
                "generated_fake_ui_allowed": False,
                "native_overlay_required_for_exact_text_or_number": True,
            }.items()
        ):
            raise ValueError("VSR1_EXACT_CONTENT_POLICY_INVALID")
        return item

    @property
    def raw(self) -> dict[str, Any]:
        return deepcopy(self._raw)

    @property
    def item(self) -> dict[str, Any]:
        return deepcopy(self._item)

    @property
    def policy(self) -> dict[str, Any]:
        return deepcopy(self._item["visual_source_policy"])

    @property
    def thresholds(self) -> dict[str, Any]:
        return deepcopy(self._item["routing_thresholds"])

    def route_policy(self, route: VisualSourceRoute) -> dict[str, Any]:
        matches = [
            entry
            for entry in self._item["source_routes"]
            if entry.get("key") == route.value
        ]
        if len(matches) != 1:
            raise ValueError("VSR1_ROUTE_POLICY_NOT_UNIQUE")
        return deepcopy(matches[0])

    def fallback_class(self, route: VisualSourceRoute) -> SourceFallbackClass:
        return SourceFallbackClass(self.route_policy(route)["fallback_class"])

    def forbidden_pairs_from(self, route: VisualSourceRoute) -> list[dict[str, Any]]:
        return [
            deepcopy(pair)
            for pair in self._item["forbidden_fallback_pairs"]
            if pair.get("from_route") == route.value
        ]

    def fixture_profile(self, fixture_key_or_channel: str) -> NicheVisualSourceProfile:
        fixtures = self._item.get("fixtures") or []
        matches = [
            entry
            for entry in fixtures
            if entry.get("key") == fixture_key_or_channel
            or entry.get("channel_key") == fixture_key_or_channel
        ]
        if len(matches) != 1:
            raise ValueError("VSR1_POLICY_FIXTURE_NOT_UNIQUE")
        fixture = matches[0]
        if (
            fixture.get("fixture_only") is not True
            or fixture.get("active") is not False
            or fixture.get("channel_profile_version_binding") is not None
            or fixture.get("provider_execution_allowed") is not False
        ):
            raise ValueError("VSR1_POLICY_FIXTURE_NOT_FAIL_CLOSED")
        return NicheVisualSourceProfile(fixture["niche_visual_source_profile"])


class _GateBase:
    def __init__(self, catalog: VisualSourceRoutingPolicyCatalog | None = None):
        self.catalog = catalog or VisualSourceRoutingPolicyCatalog()

    def _base(
        self,
        requirements: SceneVisualRealizationRequirements,
        reason_codes: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "scene_id": requirements.scene_id,
            "requirements_hash": requirements.content_hash,
            "policy_ref": self.catalog.policy_ref,
            "policy_version": self.catalog.policy_version,
            "policy_hash": self.catalog.policy_hash,
            "reason_codes": _unique(reason_codes),
        }


class VisualRealizationCompletenessGate:
    """Validate strict routing input without performing classification or I/O."""

    _VALIDATION_REASON_BY_FIELD = (
        ("semantic_intent", "VISUAL_REALIZATION_SEMANTIC_INTENT_MISSING"),
        ("scene_meaning", "VISUAL_REALIZATION_SCENE_MEANING_MISSING"),
        ("narrative_function", "VISUAL_REALIZATION_NARRATIVE_FUNCTION_MISSING"),
        ("niche_visual_source_profile", "VISUAL_REALIZATION_SOURCE_PROFILE_MISSING"),
        ("scene_class", "VISUAL_REALIZATION_SOURCE_CLASSIFICATION_MISSING"),
        ("filmability_score", "VISUAL_REALIZATION_SOURCE_FEATURE_MISSING"),
        ("stock_searchability_score", "VISUAL_REALIZATION_SOURCE_FEATURE_MISSING"),
        (
            "evidence_truth_requirement",
            "VISUAL_REALIZATION_EVIDENCE_TRUTH_REQUIREMENT_MISSING",
        ),
        (
            "authorized_asset_available",
            "VISUAL_REALIZATION_EVIDENCE_AUTHORIZATION_STATE_MISSING",
        ),
        ("exact_text_dependency", "VISUAL_REALIZATION_EXACT_TEXT_DEPENDENCY_MISSING"),
        (
            "exact_number_dependency",
            "VISUAL_REALIZATION_EXACT_NUMBER_DEPENDENCY_MISSING",
        ),
        ("segment_ids", "VISUAL_REALIZATION_SEGMENT_BINDING_MISSING"),
        ("target_aspect_ratio", "VISUAL_REALIZATION_OUTPUT_SHAPE_MISSING"),
        ("minimum_resolution", "VISUAL_REALIZATION_OUTPUT_RESOLUTION_MISSING"),
    )

    def evaluate(
        self,
        requirements: SceneVisualRealizationRequirements | Mapping[str, Any],
    ) -> GateEvaluation:
        reasons: list[str] = []
        if requirements is None:
            reasons = [
                "NO_VISUAL_REALIZATION",
                "VISUAL_REALIZATION_REQUIREMENTS_INVALID",
                "VISUAL_REALIZATION_REQUIREMENTS_MISSING",
            ]
            payload = {"passed": False, "reason_codes": reasons}
            return GateEvaluation(False, tuple(reasons), stable_hash(payload))
        try:
            typed = (
                requirements
                if isinstance(requirements, SceneVisualRealizationRequirements)
                else SceneVisualRealizationRequirements.model_validate(dict(requirements))
            )
        except ValidationError as exc:
            locations = {
                str(error["loc"][0])
                for error in exc.errors()
                if error.get("loc")
            }
            reasons.append("VISUAL_REALIZATION_REQUIREMENTS_INVALID")
            reasons.extend(
                reason
                for field, reason in self._VALIDATION_REASON_BY_FIELD
                if field in locations
            )
            if {"semantic_intent", "scene_meaning"} & locations:
                reasons.append("VISUAL_MEANING_MISSING")
            if "narrative_function" in locations:
                reasons.append("NARRATIVE_FUNCTION_MISSING")
            if {"evidence_truth_requirement", "authorized_asset_available"} & locations:
                reasons.append("EVIDENCE_TRUTH_UNRESOLVED")
            if {"exact_text_dependency", "exact_number_dependency"} & locations:
                reasons.append("EXACT_TEXT_DEPENDENCY_UNRESOLVED")
            if locations - {"semantic_intent", "scene_meaning", "narrative_function"}:
                reasons.append("SOURCE_REQUIREMENT_INCOMPLETE")
            reasons = _unique(reasons)
            payload = {"passed": False, "reason_codes": reasons}
            return GateEvaluation(False, tuple(reasons), stable_hash(payload))
        except (TypeError, ValueError):
            reasons.extend(
                [
                    "VISUAL_REALIZATION_REQUIREMENTS_INVALID",
                    "VISUAL_REALIZATION_REQUIREMENTS_UNREADABLE",
                ]
            )
            payload = {"passed": False, "reason_codes": reasons}
            return GateEvaluation(False, tuple(reasons), stable_hash(payload))

        critical_text = (
            typed.scene_id,
            typed.scene_class,
            typed.narrative_function,
            typed.scene_meaning,
            typed.editorial_intent,
            typed.crop_safety_requirement,
        )
        if not all(str(value).strip() for value in critical_text):
            reasons.append("VISUAL_REALIZATION_CRITICAL_TEXT_MISSING")
        if not typed.segment_ids or len(typed.segment_ids) != len(set(typed.segment_ids)):
            reasons.append("VISUAL_REALIZATION_SEGMENT_BINDING_INVALID")
        if _RESOLUTION_ORDER.get(typed.minimum_resolution, -1) < _RESOLUTION_ORDER["1080p"]:
            reasons.append("VISUAL_REALIZATION_RESOLUTION_BELOW_1080P")
        if typed.content_hash != stable_hash(_requirements_payload(typed)):
            reasons.append("VISUAL_REALIZATION_REQUIREMENTS_HASH_MISMATCH")
        if not reasons:
            reasons.append("VISUAL_REALIZATION_REQUIREMENTS_COMPLETE")
        passed = reasons == ["VISUAL_REALIZATION_REQUIREMENTS_COMPLETE"]
        payload = {
            "passed": passed,
            "scene_id": typed.scene_id,
            "requirements_hash": typed.content_hash,
            "reason_codes": reasons,
        }
        return GateEvaluation(passed, tuple(reasons), stable_hash(payload))


class PexelsEligibilityGate(_GateBase):
    _PROHIBITED_SCENE_CLASSES = frozenset(
        {
            "actual_ui",
            "ui",
            "product",
            "document",
            "evidence",
            "screenshot",
            "mechanism",
            "process",
        }
    )

    def evaluate(
        self,
        requirements: SceneVisualRealizationRequirements,
    ) -> PexelsEligibilityAssessment:
        thresholds = self.catalog.thresholds
        eligible = thresholds["pexels_eligible_if"]
        supporting = thresholds["pexels_supporting_only_if"]
        prohibited = thresholds["pexels_prohibited_if"]
        reasons: list[str] = []
        normalized_class = (
            requirements.scene_class.strip().lower().replace("-", "_").replace(" ", "_")
        )

        hard_blocks = (
            (
                requirements.exact_text_dependency >= prohibited["exact_text_dependency_min"],
                "PEXELS_EXACT_TEXT_AUTHORITY_PROHIBITED",
            ),
            (
                requirements.exact_number_dependency >= prohibited["exact_number_dependency_min"],
                "PEXELS_EXACT_NUMBER_AUTHORITY_PROHIBITED",
            ),
            (
                requirements.evidence_truth_requirement >= prohibited["evidence_truth_requirement_min"],
                "PEXELS_EVIDENCE_TRUTH_PROHIBITED",
            ),
            (
                requirements.custom_composition_score >= prohibited["custom_composition_score_min"],
                "PEXELS_CUSTOM_COMPOSITION_PROHIBITED",
            ),
            (
                requirements.product_specificity >= prohibited["product_specificity_min"],
                "PEXELS_PRODUCT_SPECIFICITY_PROHIBITED",
            ),
            (
                requirements.brand_or_product_dependency >= prohibited["product_specificity_min"],
                "PEXELS_BRAND_OR_PRODUCT_DEPENDENCY_PROHIBITED",
            ),
            (
                requirements.recurring_identity_required is prohibited["recurring_identity_required"],
                "PEXELS_RECURRING_IDENTITY_PROHIBITED",
            ),
            (
                requirements.named_workflow_nodes_required
                is prohibited["named_workflow_nodes_required"],
                "PEXELS_NAMED_WORKFLOW_PROHIBITED",
            ),
            (
                normalized_class in self._PROHIBITED_SCENE_CLASSES,
                "PEXELS_SCENE_TRUTH_OR_MECHANISM_CLASS_PROHIBITED",
            ),
        )
        reasons.extend(code for blocked, code in hard_blocks if blocked)

        if reasons:
            result = PexelsEligibilityResult.PEXELS_PROHIBITED
            routes: list[VisualSourceRoute] = []
            supporting_only = False
        else:
            fully_eligible = (
                requirements.filmability_score >= eligible["filmability_score_min"]
                and requirements.stock_searchability_score
                >= eligible["stock_searchability_score_min"]
                and requirements.custom_composition_score
                <= eligible["custom_composition_score_max"]
                and requirements.exact_text_dependency <= eligible["exact_text_dependency_max"]
                and requirements.exact_number_dependency <= eligible["exact_number_dependency_max"]
                and requirements.evidence_truth_requirement
                <= eligible["evidence_truth_requirement_max"]
                and requirements.identity_consistency_requirement
                <= eligible["identity_consistency_requirement_max"]
            )
            context_support = (
                requirements.filmability_score >= supporting["filmability_score_min"]
                and requirements.stock_searchability_score
                >= supporting["stock_searchability_score_min"]
            )
            if fully_eligible:
                result = PexelsEligibilityResult.PEXELS_ELIGIBLE
                routes = [VisualSourceRoute.PEXELS_VIDEO, VisualSourceRoute.PEXELS_PHOTO]
                supporting_only = False
                reasons.append("PEXELS_OBSERVABLE_MEANING_ELIGIBLE")
            elif context_support:
                result = PexelsEligibilityResult.PEXELS_SUPPORTING_ONLY
                routes = [VisualSourceRoute.PEXELS_VIDEO, VisualSourceRoute.PEXELS_PHOTO]
                supporting_only = True
                reasons.append("PEXELS_CONTEXT_SUPPORT_ONLY")
            else:
                result = PexelsEligibilityResult.PEXELS_LOW_CONFIDENCE
                routes = []
                supporting_only = False
                reasons.append("PEXELS_SEMANTIC_FIT_LOW_CONFIDENCE")

        payload = {
            **self._base(requirements, reasons),
            "result": result,
            "eligible_routes": routes,
            "supporting_only": supporting_only,
            "provider_execution_allowed": False,
        }
        return PexelsEligibilityAssessment(
            **payload,
            content_hash=stable_hash(_assessment_payload(payload)),
        )


class EvidenceTruthSourceGate(_GateBase):
    _AUTHORITY_SCENE_CLASSES = frozenset(
        {"actual_ui", "ui", "product", "document", "evidence", "screenshot"}
    )

    def evaluate(
        self,
        requirements: SceneVisualRealizationRequirements,
        *,
        authorization_evidence_refs: Sequence[str] = (),
    ) -> EvidenceTruthAssessment:
        threshold = self.catalog.thresholds["evidence_truth_source_gate"][
            "evidence_truth_requirement_min"
        ]
        normalized_class = requirements.scene_class.strip().lower().replace("-", "_").replace(" ", "_")
        truth_required = (
            requirements.evidence_truth_requirement >= threshold
            or requirements.product_specificity >= threshold
            or requirements.brand_or_product_dependency >= threshold
            or normalized_class in self._AUTHORITY_SCENE_CLASSES
        )
        refs = _unique(authorization_evidence_refs)
        verified_available = requirements.authorized_asset_available and bool(refs)

        if not truth_required:
            result = EvidenceTruthResult.NOT_REQUIRED
            selected_route = None
            reasons = ["EVIDENCE_TRUTH_AUTHORIZED_SOURCE_NOT_REQUIRED"]
        elif verified_available:
            result = EvidenceTruthResult.AUTHORIZED_SOURCE_AVAILABLE
            selected_route = VisualSourceRoute.AUTHORIZED_UI_OR_PRODUCT_ASSET
            reasons = ["EVIDENCE_TRUTH_AUTHORIZED_SOURCE_VERIFIED"]
        else:
            result = EvidenceTruthResult.BLOCKED
            selected_route = VisualSourceRoute.UNRESOLVED_BLOCK
            reasons = ["EVIDENCE_TRUTH_AUTHORIZED_SOURCE_REQUIRED"]
            if requirements.authorized_asset_available and not refs:
                reasons.append("AUTHORIZED_ASSET_PROVENANCE_MISSING")
            else:
                reasons.append("AUTHORIZED_ASSET_UNAVAILABLE")

        payload = {
            **self._base(requirements, reasons),
            "result": result,
            "evidence_truth_required": truth_required,
            "authorized_asset_required": truth_required,
            "authorized_asset_available": verified_available,
            "authorization_evidence_refs": refs,
            "selected_route": selected_route,
        }
        return EvidenceTruthAssessment(
            **payload,
            content_hash=stable_hash(_assessment_payload(payload)),
        )


class DiagramSuitabilityGate(_GateBase):
    def evaluate(
        self,
        requirements: SceneVisualRealizationRequirements,
    ) -> DiagramSuitabilityAssessment:
        policy = self.catalog.thresholds["diagram_suitability_gate"]
        diagram_preferred = (
            requirements.diagram_clarity_advantage >= policy["diagram_clarity_advantage_min"]
            or requirements.named_workflow_nodes_required
        )
        if not diagram_preferred:
            result = DiagramSuitabilityResult.NOT_PREFERRED
            route = None
            reasons = ["NATIVE_DIAGRAM_NOT_PREFERRED"]
        elif (
            requirements.motion_semantic_value
            >= policy["native_motion_graphic_motion_semantic_value_min"]
        ):
            result = DiagramSuitabilityResult.NATIVE_MOTION_GRAPHIC
            route = VisualSourceRoute.NATIVE_MOTION_GRAPHIC
            reasons = ["NATIVE_MOTION_COMMUNICATES_RELATIONSHIP_OR_STATE"]
        else:
            result = DiagramSuitabilityResult.NATIVE_DIAGRAM
            route = VisualSourceRoute.NATIVE_DIAGRAM
            reasons = ["NATIVE_DIAGRAM_CLARITY_ADVANTAGE"]
        if requirements.named_workflow_nodes_required:
            reasons.append("NAMED_WORKFLOW_REQUIRES_NATIVE_LABEL_AUTHORITY")

        payload = {
            **self._base(requirements, reasons),
            "result": result,
            "diagram_clarity_advantage": requirements.diagram_clarity_advantage,
            "motion_semantic_value": requirements.motion_semantic_value,
            "selected_route": route,
        }
        return DiagramSuitabilityAssessment(
            **payload,
            content_hash=stable_hash(_assessment_payload(payload)),
        )


class AIImageEligibilityGate(_GateBase):
    def evaluate(
        self,
        requirements: SceneVisualRealizationRequirements,
        *,
        rights_policy_allows_generation: bool = False,
    ) -> AIImageEligibilityAssessment:
        policy = self.catalog.thresholds["ai_image_eligibility_gate"]
        reasons: list[str] = []
        normalized_class = requirements.scene_class.strip().lower().replace("-", "_").replace(" ", "_")
        truth_scene = normalized_class in EvidenceTruthSourceGate._AUTHORITY_SCENE_CLASSES
        if not rights_policy_allows_generation:
            reasons.append("AI_IMAGE_RIGHTS_POLICY_PROHIBITS_GENERATION")
        if requirements.evidence_truth_requirement >= policy[
            "evidence_truth_requirement_must_be_below"
        ]:
            reasons.append("AI_IMAGE_CANNOT_IMPERSONATE_EVIDENCE")
        if (
            requirements.product_specificity >= 0.50
            or requirements.brand_or_product_dependency >= 0.50
            or truth_scene
        ):
            reasons.append("AI_IMAGE_ACTUAL_UI_PRODUCT_OR_DOCUMENT_TRUTH_PROHIBITED")
        if (
            requirements.recurring_identity_required
            or requirements.identity_consistency_requirement >= 0.50
        ):
            reasons.append("AI_IMAGE_IDENTITY_OR_LIKENESS_AUTHORITY_PROHIBITED")

        if reasons:
            result = AIImageEligibilityResult.AI_IMAGE_PROHIBITED
            routes: list[VisualSourceRoute] = []
            native_overlay_required = False
        elif requirements.custom_composition_score >= policy["custom_composition_score_min"]:
            native_overlay_required = (
                requirements.exact_text_dependency > 0.0
                or requirements.exact_number_dependency > 0.0
                or requirements.named_workflow_nodes_required
            )
            if native_overlay_required:
                result = AIImageEligibilityResult.AI_IMAGE_WITH_NATIVE_OVERLAY_REQUIRED
                routes = [VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY]
                reasons.append("AI_IMAGE_CUSTOM_COMPOSITION_WITH_NATIVE_OVERLAY_ELIGIBLE")
            else:
                result = AIImageEligibilityResult.AI_IMAGE_ALLOWED
                routes = [VisualSourceRoute.AI_GENERATED_IMAGE]
                reasons.append("AI_IMAGE_CUSTOM_COMPOSITION_ELIGIBLE")
            reasons.append("IMAGE_PROVIDER_ROUTE_NOT_YET_ACTIVE")
        else:
            result = AIImageEligibilityResult.AI_IMAGE_LOW_CONFIDENCE
            routes = []
            native_overlay_required = False
            reasons.append("AI_IMAGE_CUSTOM_COMPOSITION_BELOW_THRESHOLD")

        payload = {
            **self._base(requirements, reasons),
            "result": result,
            "planning_routes": routes,
            "native_overlay_required": native_overlay_required,
            "provider_execution_allowed": False,
            "future_provider_approval_required": True,
        }
        return AIImageEligibilityAssessment(
            **payload,
            content_hash=stable_hash(_assessment_payload(payload)),
        )


class VisualSourceRouter:
    """Pure meaning-first route decision service.

    No provider result, response or search failure is accepted as input. Route
    selection therefore cannot turn a failed Pexels search into an AI request.
    """

    def __init__(self, catalog: VisualSourceRoutingPolicyCatalog | None = None):
        self.catalog = catalog or VisualSourceRoutingPolicyCatalog()
        self.completeness_gate = VisualRealizationCompletenessGate()
        self.pexels_gate = PexelsEligibilityGate(self.catalog)
        self.evidence_gate = EvidenceTruthSourceGate(self.catalog)
        self.diagram_gate = DiagramSuitabilityGate(self.catalog)
        self.ai_image_gate = AIImageEligibilityGate(self.catalog)

    def assess_archive(
        self,
        requirements: SceneVisualRealizationRequirements,
        *,
        matched_asset_ref: str | None,
        reuse_count: int,
        authorization_evidence_refs: Sequence[str],
        semantic_fit_passed: bool,
        rights_scope_permits_reuse: bool,
        reuse_cooldown_permits: bool,
        originality_policy_passed: bool,
        asset_truth_current: bool,
    ) -> ArchiveReuseAssessment:
        gates = (
            semantic_fit_passed,
            rights_scope_permits_reuse,
            reuse_cooldown_permits,
            originality_policy_passed,
            asset_truth_current,
        )
        archive_authorization_refs = [str(ref) for ref in authorization_evidence_refs]
        if matched_asset_ref and all(gates):
            result = ArchiveReuseResult.ELIGIBLE
        elif matched_asset_ref:
            result = ArchiveReuseResult.INELIGIBLE
        else:
            result = ArchiveReuseResult.NOT_EVALUATED
        reasons = [
            "ARCHIVE_MATCH_PRESENT" if matched_asset_ref else "ARCHIVE_MATCH_ABSENT",
            f"ARCHIVE_REUSE_COUNT_{reuse_count}",
            "ARCHIVE_AUTHORIZATION_EVIDENCE_PRESENT"
            if archive_authorization_refs
            else "ARCHIVE_AUTHORIZATION_EVIDENCE_ABSENT",
            "ARCHIVE_SEMANTIC_FIT_PASS" if semantic_fit_passed else "ARCHIVE_SEMANTIC_FIT_FAIL",
            "ARCHIVE_RIGHTS_SCOPE_PASS"
            if rights_scope_permits_reuse
            else "ARCHIVE_RIGHTS_SCOPE_FAIL",
            "ARCHIVE_REUSE_COOLDOWN_PASS"
            if reuse_cooldown_permits
            else "ARCHIVE_REUSE_COOLDOWN_FAIL",
            "ARCHIVE_ORIGINALITY_POLICY_PASS"
            if originality_policy_passed
            else "ARCHIVE_ORIGINALITY_POLICY_FAIL",
            "ARCHIVE_TRUTH_CURRENT" if asset_truth_current else "ARCHIVE_TRUTH_STALE",
        ]
        payload = {
            "scene_id": requirements.scene_id,
            "requirements_hash": requirements.content_hash,
            "policy_ref": self.catalog.policy_ref,
            "policy_version": self.catalog.policy_version,
            "policy_hash": self.catalog.policy_hash,
            "reason_codes": reasons,
            "result": result,
            "matched_asset_ref": matched_asset_ref,
            "reuse_count": reuse_count,
            "authorization_evidence_refs": archive_authorization_refs,
            "semantic_fit_passed": semantic_fit_passed,
            "rights_scope_permits_reuse": rights_scope_permits_reuse,
            "reuse_cooldown_permits": reuse_cooldown_permits,
            "originality_policy_passed": originality_policy_passed,
            "asset_truth_current": asset_truth_current,
        }
        return ArchiveReuseAssessment(
            **payload,
            content_hash=stable_hash(_assessment_payload(payload)),
        )

    def route(
        self,
        requirements: SceneVisualRealizationRequirements,
        *,
        archive_assessment: ArchiveReuseAssessment | None = None,
        authorization_evidence_refs: Sequence[str] = (),
        rights_policy_allows_generation: bool = False,
        still_or_native_motion_sufficient: bool = True,
        future_cost_class_allows_veo: bool = False,
        veo_reference_image_available: bool = False,
    ) -> VisualSourceDecision:
        completeness = self.completeness_gate.evaluate(requirements)
        if not completeness.passed:
            return self._decision(
                requirements,
                VisualSourceRoute.UNRESOLVED_BLOCK,
                list(completeness.reason_codes),
                block_reasons=["VISUAL_REALIZATION_REQUIREMENTS_INCOMPLETE"],
                assessment_hashes={"completeness": completeness.content_hash},
            )

        archive_binding_valid = self._archive_binding_valid(requirements, archive_assessment)
        if archive_assessment is not None and not archive_binding_valid:
            return self._decision(
                requirements,
                VisualSourceRoute.UNRESOLVED_BLOCK,
                ["ARCHIVE_ASSESSMENT_BINDING_INVALID"],
                block_reasons=["ARCHIVE_ASSESSMENT_BINDING_INVALID"],
                assessment_hashes={"archive": archive_assessment.content_hash},
            )
        combined_authorization_refs = list(authorization_evidence_refs)
        if archive_assessment is not None and archive_binding_valid:
            combined_authorization_refs.extend(archive_assessment.authorization_evidence_refs)
        evidence = self.evidence_gate.evaluate(
            requirements,
            authorization_evidence_refs=_unique(combined_authorization_refs),
        )
        if (
            archive_assessment is not None
            and archive_binding_valid
            and archive_assessment.result == ArchiveReuseResult.ELIGIBLE
        ):
            if evidence.result == EvidenceTruthResult.BLOCKED:
                return self._decision(
                    requirements,
                    VisualSourceRoute.UNRESOLVED_BLOCK,
                    [*evidence.reason_codes, "ARCHIVE_CANNOT_BYPASS_EVIDENCE_AUTHORIZATION"],
                    block_reasons=["EVIDENCE_TRUTH_SOURCE_UNRESOLVED"],
                    assessment_hashes={
                        "archive": archive_assessment.content_hash,
                        "evidence_truth": evidence.content_hash,
                    },
                )
            return self._decision(
                requirements,
                VisualSourceRoute.ARCHIVED_ASSET_REUSE,
                [
                    "ARCHIVE_REUSE_ALL_GATES_PASS",
                    *archive_assessment.reason_codes,
                    *evidence.reason_codes,
                ],
                assessment_hashes={
                    "archive": archive_assessment.content_hash,
                    "evidence_truth": evidence.content_hash,
                },
            )

        if evidence.result == EvidenceTruthResult.AUTHORIZED_SOURCE_AVAILABLE:
            return self._decision(
                requirements,
                VisualSourceRoute.AUTHORIZED_UI_OR_PRODUCT_ASSET,
                evidence.reason_codes,
                assessment_hashes={"evidence_truth": evidence.content_hash},
            )
        if evidence.result == EvidenceTruthResult.BLOCKED:
            return self._decision(
                requirements,
                VisualSourceRoute.UNRESOLVED_BLOCK,
                evidence.reason_codes,
                block_reasons=["EVIDENCE_TRUTH_SOURCE_UNRESOLVED"],
                assessment_hashes={"evidence_truth": evidence.content_hash},
            )

        diagram = self.diagram_gate.evaluate(requirements)
        if diagram.selected_route is not None:
            return self._decision(
                requirements,
                diagram.selected_route,
                diagram.reason_codes,
                assessment_hashes={
                    "evidence_truth": evidence.content_hash,
                    "diagram": diagram.content_hash,
                },
            )

        ai_image = self.ai_image_gate.evaluate(
            requirements,
            rights_policy_allows_generation=rights_policy_allows_generation,
        )
        exact_content_required = (
            requirements.exact_text_dependency > 0.0
            or requirements.exact_number_dependency > 0.0
        )
        if exact_content_required:
            if (
                ai_image.result
                == AIImageEligibilityResult.AI_IMAGE_WITH_NATIVE_OVERLAY_REQUIRED
            ):
                return self._decision(
                    requirements,
                    VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
                    [
                        "EXACT_CONTENT_NATIVE_OVERLAY_REQUIRED",
                        *ai_image.reason_codes,
                    ],
                    assessment_hashes={
                        "evidence_truth": evidence.content_hash,
                        "diagram": diagram.content_hash,
                        "ai_image": ai_image.content_hash,
                    },
                )
            return self._decision(
                requirements,
                VisualSourceRoute.EDITORIAL_TEXT_GRAPHIC,
                ["EXACT_CONTENT_REQUIRES_NATIVE_AUTHORITY"],
                assessment_hashes={
                    "evidence_truth": evidence.content_hash,
                    "diagram": diagram.content_hash,
                    "ai_image": ai_image.content_hash,
                },
            )

        pexels = self.pexels_gate.evaluate(requirements)
        pexels_can_carry_scene = (
            pexels.result == PexelsEligibilityResult.PEXELS_ELIGIBLE
            or (
                pexels.result == PexelsEligibilityResult.PEXELS_SUPPORTING_ONLY
                and self._supporting_stock_can_carry_scene(requirements)
            )
        )
        if pexels_can_carry_scene:
            route = self._select_pexels_route(requirements)
            return self._decision(
                requirements,
                route,
                pexels.reason_codes,
                assessment_hashes={
                    "evidence_truth": evidence.content_hash,
                    "diagram": diagram.content_hash,
                    "pexels": pexels.content_hash,
                    "ai_image": ai_image.content_hash,
                },
            )

        if self._veo_eligible(
            requirements,
            still_or_native_motion_sufficient=still_or_native_motion_sufficient,
            future_cost_class_allows_veo=future_cost_class_allows_veo,
        ):
            route = (
                VisualSourceRoute.VEO_IMAGE_TO_VIDEO
                if veo_reference_image_available
                else VisualSourceRoute.VEO_TEXT_TO_VIDEO
            )
            return self._decision(
                requirements,
                route,
                [
                    "VEO_HIGH_SEMANTIC_MOTION_ROUTE_PLANNED",
                    "VEO_PROVIDER_EXECUTION_REQUIRES_FUTURE_APPROVAL",
                ],
                assessment_hashes={
                    "evidence_truth": evidence.content_hash,
                    "diagram": diagram.content_hash,
                    "pexels": pexels.content_hash,
                    "ai_image": ai_image.content_hash,
                },
                routing_context={
                    "still_or_native_motion_sufficient": still_or_native_motion_sufficient,
                    "future_cost_class_allows_veo": future_cost_class_allows_veo,
                    "veo_reference_image_available": veo_reference_image_available,
                },
            )

        if ai_image.result == AIImageEligibilityResult.AI_IMAGE_ALLOWED:
            return self._decision(
                requirements,
                VisualSourceRoute.AI_GENERATED_IMAGE,
                ai_image.reason_codes,
                assessment_hashes={
                    "evidence_truth": evidence.content_hash,
                    "diagram": diagram.content_hash,
                    "pexels": pexels.content_hash,
                    "ai_image": ai_image.content_hash,
                },
            )

        return self._decision(
            requirements,
            VisualSourceRoute.UNRESOLVED_BLOCK,
            [
                "VISUAL_SOURCE_ROUTE_UNRESOLVED",
                f"PEXELS_ASSESSMENT_{pexels.result.value}",
                f"AI_IMAGE_ASSESSMENT_{ai_image.result.value}",
            ],
            block_reasons=["NO_SAFE_VISUAL_SOURCE_ROUTE"],
            assessment_hashes={
                "evidence_truth": evidence.content_hash,
                "diagram": diagram.content_hash,
                "pexels": pexels.content_hash,
                "ai_image": ai_image.content_hash,
            },
        )

    def _archive_binding_valid(
        self,
        requirements: SceneVisualRealizationRequirements,
        assessment: ArchiveReuseAssessment | None,
    ) -> bool:
        if assessment is None:
            return True
        return (
            assessment.scene_id == requirements.scene_id
            and assessment.requirements_hash == requirements.content_hash
            and assessment.policy_ref == self.catalog.policy_ref
            and assessment.policy_version == self.catalog.policy_version
            and assessment.policy_hash == self.catalog.policy_hash
        )

    def _select_pexels_route(
        self,
        requirements: SceneVisualRealizationRequirements,
    ) -> VisualSourceRoute:
        if (
            requirements.human_action_requirement >= 0.50
            or requirements.motion_semantic_value >= 0.30
        ):
            return VisualSourceRoute.PEXELS_VIDEO
        return VisualSourceRoute.PEXELS_PHOTO

    @staticmethod
    def _supporting_stock_can_carry_scene(
        requirements: SceneVisualRealizationRequirements,
    ) -> bool:
        narrative_function = (
            requirements.narrative_function.strip().lower().replace("-", "_").replace(" ", "_")
        )
        scene_class = requirements.scene_class.strip().lower().replace("-", "_").replace(" ", "_")
        return narrative_function in {
            "context",
            "supporting",
            "establishing",
            "transition",
            "b_roll",
        } or scene_class in {"context", "establishing", "transition", "b_roll"}

    def _veo_eligible(
        self,
        requirements: SceneVisualRealizationRequirements,
        *,
        still_or_native_motion_sufficient: bool,
        future_cost_class_allows_veo: bool,
    ) -> bool:
        policy = self.catalog.thresholds["veo_routing_boundary"]
        scene_class = requirements.scene_class.strip().lower().replace("-", "_").replace(" ", "_")
        allowed_classes = {
            str(value).strip().lower().replace("-", "_").replace(" ", "_")
            for value in policy["allowed_scene_classes"]
        }
        return (
            requirements.motion_semantic_value >= policy["motion_semantic_value_min"]
            and scene_class in allowed_classes
            and not still_or_native_motion_sufficient
            and requirements.evidence_truth_requirement
            < policy["evidence_truth_requirement_must_be_below"]
            and future_cost_class_allows_veo
        )

    def _decision(
        self,
        requirements: SceneVisualRealizationRequirements,
        route: VisualSourceRoute,
        reason_codes: Sequence[str],
        *,
        block_reasons: Sequence[str] = (),
        assessment_hashes: Mapping[str, str] | None = None,
        routing_context: Mapping[str, Any] | None = None,
    ) -> VisualSourceDecision:
        allowed = self._allowed_fallback_routes(route)
        all_routes = set(VisualSourceRoute)
        forbidden = sorted(all_routes - {route} - set(allowed), key=lambda value: value.value)
        fallback_class = self.catalog.fallback_class(route)
        provider_required = route in _PROVIDER_ROUTES
        blocked = route == VisualSourceRoute.UNRESOLVED_BLOCK
        reasons = _unique(reason_codes)
        if route in _AI_IMAGE_ROUTES and "IMAGE_PROVIDER_ROUTE_NOT_YET_ACTIVE" not in reasons:
            reasons.append("IMAGE_PROVIDER_ROUTE_NOT_YET_ACTIVE")
        if not reasons:
            reasons = ["VSR1_ROUTE_SELECTED"]
        block_codes = _unique(block_reasons)
        if blocked and not block_codes:
            block_codes = ["NO_SAFE_VISUAL_SOURCE_ROUTE"]

        snapshot = {
            "requirements": _requirements_payload(requirements),
            "requirements_hash": requirements.content_hash,
            "assessment_hashes": dict(sorted((assessment_hashes or {}).items())),
            "routing_context": dict(sorted((routing_context or {}).items())),
            "provider_observations_consumed": False,
            "pexels_search_result_consumed": False,
            "auto_pexels_to_ai_failover": False,
        }
        source_class, preferred_source = self._historical_projection(route)
        historical_fallbacks = [
            self._historical_projection(fallback)[1] for fallback in allowed
        ]
        if not historical_fallbacks:
            historical_fallbacks = [preferred_source]

        payload = {
            "scene_id": requirements.scene_id,
            "source_class": source_class,
            "preferred_source": preferred_source,
            "fallback_order": historical_fallbacks,
            "procurement_required": route in (_PROVIDER_ROUTES | _AUTHORIZED_ROUTES),
            "rights_review_required": route not in (_NATIVE_ROUTES | {VisualSourceRoute.UNRESOLVED_BLOCK}),
            "requires_ai_disclosure_check": route in (_AI_IMAGE_ROUTES | _VEO_ROUTES),
            "max_cost_usd": None,
            "reason_codes": reasons,
            "decision_version": DECISION_VERSION,
            "niche_visual_source_profile": requirements.niche_visual_source_profile,
            "preferred_source_route": route,
            "allowed_fallback_routes": allowed,
            "forbidden_fallback_routes": forbidden,
            "fallback_class": fallback_class,
            "routing_confidence": self._confidence(route),
            "routing_reason_codes": reasons,
            "input_feature_snapshot": snapshot,
            "policy_ref": self.catalog.policy_ref,
            "policy_version": self.catalog.policy_version,
            "policy_hash": self.catalog.policy_hash,
            "estimated_cost_class": self._cost_class(route),
            "provider_execution_required": provider_required,
            "human_approval_required": True,
            "decision_status": (
                VisualDecisionStatus.BLOCKED
                if blocked
                else VisualDecisionStatus.PLANNED
                if provider_required
                else VisualDecisionStatus.READY
            ),
            "block_reason_codes": block_codes,
        }
        if "provider_execution_allowed" in VisualSourceDecision.model_fields:
            payload["provider_execution_allowed"] = False
        return VisualSourceDecision(
            **payload,
            content_hash=stable_hash(payload),
        )

    def _allowed_fallback_routes(self, route: VisualSourceRoute) -> list[VisualSourceRoute]:
        if route == VisualSourceRoute.PEXELS_VIDEO:
            return [VisualSourceRoute.PEXELS_PHOTO]
        if route == VisualSourceRoute.PEXELS_PHOTO:
            return [VisualSourceRoute.PEXELS_VIDEO]
        if route in _AI_IMAGE_ROUTES:
            return [VisualSourceRoute.NATIVE_MOTION_GRAPHIC]
        if route == VisualSourceRoute.NATIVE_DIAGRAM:
            return [VisualSourceRoute.NATIVE_MOTION_GRAPHIC]
        if route == VisualSourceRoute.NATIVE_MOTION_GRAPHIC:
            return [VisualSourceRoute.NATIVE_DIAGRAM]
        if route == VisualSourceRoute.EDITORIAL_TEXT_GRAPHIC:
            return []
        if route == VisualSourceRoute.AUTHORIZED_UI_OR_PRODUCT_ASSET:
            return [VisualSourceRoute.HUMAN_SUPPLIED_ASSET]
        if route == VisualSourceRoute.HUMAN_SUPPLIED_ASSET:
            return [VisualSourceRoute.AUTHORIZED_UI_OR_PRODUCT_ASSET]
        return []

    @staticmethod
    def _historical_projection(route: VisualSourceRoute) -> tuple[str, str]:
        if route == VisualSourceRoute.ARCHIVED_ASSET_REUSE:
            return "APPROVED_ASSET_POOL", "APPROVED_ASSET_POOL"
        if route in _PEXELS_ROUTES:
            return "API_NATIVE_PROVIDER", "STOCK_PLACEHOLDER"
        if route in _AI_IMAGE_ROUTES or route in _VEO_ROUTES:
            return "API_NATIVE_PROVIDER", "AI_PLACEHOLDER"
        if route in _NATIVE_ROUTES:
            return "LOCAL_RENDERER", "DIAGRAM_PLACEHOLDER"
        if route == VisualSourceRoute.AUTHORIZED_UI_OR_PRODUCT_ASSET:
            return "APPROVED_ASSET_POOL", "SCREENSHOT_PLACEHOLDER"
        if route == VisualSourceRoute.HUMAN_SUPPLIED_ASSET:
            return "MANUAL_ASSET_LIBRARY", "MANUAL_PREMIUM_PLACEHOLDER"
        return "APPROVED_ASSET_POOL", "APPROVED_ASSET_POOL"

    @staticmethod
    def _confidence(route: VisualSourceRoute) -> float:
        if route == VisualSourceRoute.UNRESOLVED_BLOCK:
            return 0.0
        if route in _PROVIDER_ROUTES:
            return 0.80
        return 0.95

    @staticmethod
    def _cost_class(route: VisualSourceRoute) -> str:
        if route in _NATIVE_ROUTES or route == VisualSourceRoute.ARCHIVED_ASSET_REUSE:
            return "NONE"
        if route in _PEXELS_ROUTES:
            return "FREE"
        if route in _AI_IMAGE_ROUTES:
            return "UNKNOWN"
        if route in _VEO_ROUTES:
            return "HIGH"
        return "UNKNOWN"


class VisualSourceRoutingPreviewService:
    """Return a read-only, provider-disabled route preview and gate evidence."""

    def __init__(self, catalog: VisualSourceRoutingPolicyCatalog | None = None):
        self.catalog = catalog or VisualSourceRoutingPolicyCatalog()
        self.router = VisualSourceRouter(self.catalog)

    def preview(
        self,
        requirements: SceneVisualRealizationRequirements,
        *,
        archive_assessment: ArchiveReuseAssessment | None = None,
        authorization_evidence_refs: Sequence[str] = (),
        rights_policy_allows_generation: bool = False,
        still_or_native_motion_sufficient: bool = True,
        future_cost_class_allows_veo: bool = False,
        veo_reference_image_available: bool = False,
    ) -> dict[str, Any]:
        completeness = self.router.completeness_gate.evaluate(requirements)
        pexels = self.router.pexels_gate.evaluate(requirements)
        preview_authorization_refs = list(authorization_evidence_refs)
        if self.router._archive_binding_valid(requirements, archive_assessment):
            if archive_assessment is not None:
                preview_authorization_refs.extend(archive_assessment.authorization_evidence_refs)
        evidence = self.router.evidence_gate.evaluate(
            requirements,
            authorization_evidence_refs=_unique(preview_authorization_refs),
        )
        diagram = self.router.diagram_gate.evaluate(requirements)
        ai_image = self.router.ai_image_gate.evaluate(
            requirements,
            rights_policy_allows_generation=rights_policy_allows_generation,
        )
        decision = self.router.route(
            requirements,
            archive_assessment=archive_assessment,
            authorization_evidence_refs=authorization_evidence_refs,
            rights_policy_allows_generation=rights_policy_allows_generation,
            still_or_native_motion_sufficient=still_or_native_motion_sufficient,
            future_cost_class_allows_veo=future_cost_class_allows_veo,
            veo_reference_image_available=veo_reference_image_available,
        )
        payload = {
            "preview_only": True,
            "provider_execution_allowed": False,
            "policy_ref": self.catalog.policy_ref,
            "policy_version": self.catalog.policy_version,
            "policy_hash": self.catalog.policy_hash,
            "requirements_hash": requirements.content_hash,
            "scene_id": decision.scene_id,
            "niche_profile": decision.niche_visual_source_profile.value,
            "preferred_route": decision.preferred_source_route.value,
            "fallback_class": decision.fallback_class.value,
            "reason_codes": list(decision.routing_reason_codes),
            "confidence": decision.routing_confidence,
            "provider_execution_required": decision.provider_execution_required,
            "approval_required": decision.human_approval_required,
            "blockers": list(decision.block_reason_codes),
            "exact_next_action": self._exact_next_action(decision),
            "assessments": {
                "completeness": completeness.model_dump(),
                "archive": _model_payload(archive_assessment) if archive_assessment else None,
                "evidence_truth": _model_payload(evidence),
                "diagram": _model_payload(diagram),
                "pexels": _model_payload(pexels),
                "ai_image": _model_payload(ai_image),
            },
            "decision": _model_payload(decision),
        }
        return {**payload, "preview_hash": stable_hash(payload)}

    @staticmethod
    def _exact_next_action(decision: VisualSourceDecision) -> str:
        if decision.decision_status == VisualDecisionStatus.BLOCKED:
            if "ARCHIVE_ASSESSMENT_BINDING_INVALID" in decision.block_reason_codes:
                return "REBUILD_ARCHIVE_ASSESSMENT_FOR_CURRENT_SCENE_AND_POLICY"
            if "EVIDENCE_TRUTH_SOURCE_UNRESOLVED" in decision.block_reason_codes:
                return "SUPPLY_AUTHORIZED_ASSET_AND_PROVENANCE_OR_REVISE_SCENE"
            return "REVISE_SCENE_REQUIREMENTS_OR_ESCALATE_TO_HUMAN"
        if decision.preferred_source_route in _AI_IMAGE_ROUTES:
            return "WAIT_FOR_IMG1_ROUTE_ACTIVATION_AND_EXPLICIT_OPERATOR_APPROVAL"
        if decision.provider_execution_required:
            return "HUMAN_REVIEW_ROUTE_PLAN_WITH_PROVIDER_EXECUTION_DISABLED"
        return "HUMAN_REVIEW_AND_BIND_DECISION_TO_NATIVE_RENDER_PLAN"


__all__ = [
    "AIImageEligibilityGate",
    "DiagramSuitabilityGate",
    "EvidenceTruthSourceGate",
    "GateEvaluation",
    "PexelsEligibilityGate",
    "VisualRealizationCompletenessGate",
    "VisualSourceRouter",
    "VisualSourceRoutingPolicyCatalog",
    "VisualSourceRoutingPreviewService",
    "stable_hash",
]
