from __future__ import annotations

import re
from typing import Any, Iterable

from app.contracts.visual_direction import (
    SceneVisualEvaluation,
    SceneVisualIntent,
    VisualAssetEvidence,
    VisualDirectionContract,
    VisualGateResult,
    VisualScoreThresholds,
)
from app.services.native_render_plan import stable_hash


VISUAL_DIRECTION_CONTRACT_VERSION = "visual-direction-contract/v1.0.0"

_REQUIRED_POLICY_FIELDS = (
    "realism_level",
    "treatment_mode",
    "human_presence_policy",
    "environment_type",
    "industry_context",
    "time_of_day",
    "lighting_direction",
    "lighting_temperature",
    "palette",
    "contrast",
    "saturation",
    "camera_distance",
    "lens_feel",
    "camera_movement",
    "motion_intensity",
    "framing_rule",
    "depth_of_field_style",
    "texture_grain",
    "tone_mode",
)


class VisualDirectionCompiler:
    """Compile immutable project direction without selecting or naming a provider."""

    def compile(
        self,
        *,
        channel_id: str,
        project_id: str,
        format_identity_ref: str,
        format_identity_hash: str,
        visual_strategy_profile_ref: str,
        visual_strategy_profile_hash: str,
        policy: dict[str, Any],
        contract_version: str = VISUAL_DIRECTION_CONTRACT_VERSION,
        adjacent_scene_constraints: list[str] | None = None,
    ) -> VisualDirectionContract:
        if not policy:
            raise ValueError("VISUAL_DIRECTION_POLICY_REQUIRED")
        selected = dict(policy.get("visual_language_policy") or policy)
        missing = [field for field in _REQUIRED_POLICY_FIELDS if selected.get(field) in (None, "", [])]
        if missing:
            raise ValueError(f"VISUAL_DIRECTION_POLICY_INCOMPLETE:{','.join(sorted(missing))}")
        palette = [str(item).strip() for item in selected["palette"] if str(item).strip()]
        if not palette:
            raise ValueError("VISUAL_DIRECTION_PALETTE_EMPTY")
        payload = {
            "contract_version": contract_version,
            "channel_id": channel_id,
            "project_id": project_id,
            "format_identity_ref": format_identity_ref,
            "format_identity_hash": format_identity_hash,
            "visual_strategy_profile_ref": visual_strategy_profile_ref,
            "visual_strategy_profile_hash": visual_strategy_profile_hash,
            **{field: selected[field] for field in _REQUIRED_POLICY_FIELDS if field != "palette"},
            "palette": palette,
            "prohibited_cliches": _dedupe_strings(selected.get("prohibited_cliches") or []),
            "channel_identity_markers": _dedupe_strings(selected.get("channel_identity_markers") or []),
            "adjacent_scene_constraints": _dedupe_strings(
                adjacent_scene_constraints
                if adjacent_scene_constraints is not None
                else selected.get("adjacent_scene_constraints") or []
            ),
        }
        return VisualDirectionContract(**payload, content_hash=stable_hash(payload))

    build = compile


class SceneSemanticMatchGate:
    gate_name = "SceneSemanticMatchGate"

    def __init__(self, thresholds: VisualScoreThresholds):
        if not isinstance(thresholds, VisualScoreThresholds):
            raise ValueError("VISUAL_SCORE_THRESHOLDS_REQUIRED")
        self.thresholds = thresholds

    def evaluate(
        self,
        semantic_score: float,
        *,
        scene_id: str = "unknown-scene",
        asset_ref: str = "unknown-asset",
        hard_conflicts: Iterable[str] | None = None,
    ) -> VisualGateResult:
        score = _bounded_score(semantic_score)
        conflicts = _dedupe_strings(hard_conflicts or [])
        if conflicts and self.thresholds.hard_conflicts_block:
            verdict, reasons = "BLOCK", ["VISUAL_HARD_CONFLICT"]
        else:
            verdict = _threshold_verdict(
                score,
                pass_min=self.thresholds.semantic_pass_min,
                review_min=self.thresholds.semantic_review_min,
            )
            reasons = {
                "PASS": ["SCENE_SEMANTIC_MATCH_PASS"],
                "REVIEW_REQUIRED": ["SCENE_SEMANTIC_MATCH_BORDERLINE"],
                "BLOCK": ["SCENE_SEMANTIC_MISMATCH"],
            }[verdict]
        return VisualGateResult(
            gate=self.gate_name,
            verdict=verdict,
            score=score,
            pass_min=self.thresholds.semantic_pass_min,
            review_min=self.thresholds.semantic_review_min,
            reason_codes=reasons,
            hard_conflict_reasons=conflicts,
            details={"scene_id": scene_id, "asset_ref": asset_ref},
        )

    run = evaluate


class VisualContinuityGate:
    gate_name = "VisualContinuityGate"

    def __init__(self, thresholds: VisualScoreThresholds):
        if not isinstance(thresholds, VisualScoreThresholds):
            raise ValueError("VISUAL_SCORE_THRESHOLDS_REQUIRED")
        self.thresholds = thresholds

    def evaluate(
        self,
        visual_direction_score: float,
        *,
        scene_id: str = "unknown-scene",
        asset_ref: str = "unknown-asset",
        hard_conflicts: Iterable[str] | None = None,
    ) -> VisualGateResult:
        score = _bounded_score(visual_direction_score)
        conflicts = _dedupe_strings(hard_conflicts or [])
        if conflicts and self.thresholds.hard_conflicts_block:
            verdict, reasons = "BLOCK", ["VISUAL_HARD_CONFLICT"]
        else:
            verdict = _threshold_verdict(
                score,
                pass_min=self.thresholds.adjacency_pass_min,
                review_min=self.thresholds.adjacency_review_min,
            )
            reasons = {
                "PASS": ["VISUAL_DIRECTION_CONTINUITY_PASS"],
                "REVIEW_REQUIRED": ["VISUAL_DIRECTION_CONTINUITY_BORDERLINE"],
                "BLOCK": ["VISUAL_DIRECTION_CONFLICT"],
            }[verdict]
        return VisualGateResult(
            gate=self.gate_name,
            verdict=verdict,
            score=score,
            pass_min=self.thresholds.adjacency_pass_min,
            review_min=self.thresholds.adjacency_review_min,
            reason_codes=reasons,
            hard_conflict_reasons=conflicts,
            details={"scene_id": scene_id, "asset_ref": asset_ref},
        )

    run = evaluate


class AssetAdjacencyGate:
    gate_name = "AssetAdjacencyGate"

    def __init__(self, thresholds: VisualScoreThresholds):
        if not isinstance(thresholds, VisualScoreThresholds):
            raise ValueError("VISUAL_SCORE_THRESHOLDS_REQUIRED")
        self.thresholds = thresholds

    def evaluate(
        self,
        adjacency_score: float | None = None,
        *,
        previous_score: float | None = None,
        next_score: float | None = None,
        scene_id: str = "unknown-scene",
        asset_ref: str = "unknown-asset",
        cross_provider_cut: bool = False,
        semantic_verdict: str = "PASS",
        hard_conflicts: Iterable[str] | None = None,
    ) -> VisualGateResult:
        available = [value for value in (adjacency_score, previous_score, next_score) if value is not None]
        score = min(_bounded_score(value) for value in available) if available else None
        conflicts = _dedupe_strings(hard_conflicts or [])
        reasons: list[str]
        if conflicts and self.thresholds.hard_conflicts_block:
            verdict, reasons = "BLOCK", ["ASSET_ADJACENCY_HARD_CONFLICT"]
        elif score is None:
            verdict, reasons = "PASS", ["ASSET_ADJACENCY_NOT_APPLICABLE"]
        else:
            verdict = _threshold_verdict(
                score,
                pass_min=self.thresholds.adjacency_pass_min,
                review_min=self.thresholds.adjacency_review_min,
            )
            reasons = {
                "PASS": ["ASSET_ADJACENCY_PASS"],
                "REVIEW_REQUIRED": ["ASSET_ADJACENCY_BORDERLINE"],
                "BLOCK": ["ASSET_ADJACENCY_CONFLICT"],
            }[verdict]
        if (
            cross_provider_cut
            and self.thresholds.cross_provider_cut_requires_both_scores_pass
            and semantic_verdict != "PASS"
            and verdict != "BLOCK"
        ):
            verdict = "REVIEW_REQUIRED" if semantic_verdict == "REVIEW_REQUIRED" else "BLOCK"
            reasons.append("CROSS_PROVIDER_CUT_REQUIRES_SEMANTIC_PASS")
        if cross_provider_cut and verdict != "PASS":
            reasons.append("CROSS_PROVIDER_CUT_REQUIRES_ADJACENCY_PASS")
        return VisualGateResult(
            gate=self.gate_name,
            verdict=verdict,
            score=score,
            pass_min=self.thresholds.adjacency_pass_min,
            review_min=self.thresholds.adjacency_review_min,
            reason_codes=_dedupe_strings(reasons),
            hard_conflict_reasons=conflicts,
            details={
                "scene_id": scene_id,
                "asset_ref": asset_ref,
                "previous_score": previous_score,
                "next_score": next_score,
                "cross_provider_cut": cross_provider_cut,
                "semantic_verdict": semantic_verdict,
            },
        )

    run = evaluate


class VisualEvaluationService:
    """Aggregate deterministic scene evidence; technical QC is deliberately out of scope."""

    def __init__(self, thresholds: VisualScoreThresholds):
        if not isinstance(thresholds, VisualScoreThresholds):
            raise ValueError("VISUAL_SCORE_THRESHOLDS_REQUIRED")
        self.thresholds = thresholds
        self.semantic_gate = SceneSemanticMatchGate(self.thresholds)
        self.continuity_gate = VisualContinuityGate(self.thresholds)
        self.adjacency_gate = AssetAdjacencyGate(self.thresholds)

    def evaluate_scene(
        self,
        *,
        scene_id: str,
        asset_ref: str,
        semantic_score: float,
        visual_direction_score: float,
        previous_adjacency_score: float | None = None,
        next_adjacency_score: float | None = None,
        current_source_class: str | None = None,
        previous_source_class: str | None = None,
        next_source_class: str | None = None,
        hard_conflict_reasons: list[str] | None = None,
        top_candidate_ranking: list[dict[str, Any]] | None = None,
        selected_rationale: str = "highest deterministic eligible candidate",
        representative_still_refs: list[str] | None = None,
    ) -> SceneVisualEvaluation:
        conflicts = _dedupe_strings(hard_conflict_reasons or [])
        semantic = self.semantic_gate.evaluate(
            semantic_score,
            scene_id=scene_id,
            asset_ref=asset_ref,
            hard_conflicts=conflicts,
        )
        continuity = self.continuity_gate.evaluate(
            visual_direction_score,
            scene_id=scene_id,
            asset_ref=asset_ref,
            hard_conflicts=conflicts,
        )
        cross_provider = any(
            neighbor is not None and current_source_class is not None and neighbor != current_source_class
            for neighbor in (previous_source_class, next_source_class)
        )
        adjacency = self.adjacency_gate.evaluate(
            previous_score=previous_adjacency_score,
            next_score=next_adjacency_score,
            scene_id=scene_id,
            asset_ref=asset_ref,
            cross_provider_cut=cross_provider,
            semantic_verdict=semantic.verdict,
            hard_conflicts=conflicts,
        )
        verdicts = {semantic.verdict, continuity.verdict, adjacency.verdict}
        result = "BLOCK" if "BLOCK" in verdicts else "REVIEW_REQUIRED" if "REVIEW_REQUIRED" in verdicts else "PASS"
        payload = {
            "scene_id": scene_id,
            "asset_ref": asset_ref,
            "semantic_score": _bounded_score(semantic_score),
            "visual_direction_score": _bounded_score(visual_direction_score),
            "previous_adjacency_score": previous_adjacency_score,
            "next_adjacency_score": next_adjacency_score,
            "hard_conflict_reasons": conflicts,
            "top_candidate_ranking": top_candidate_ranking or [],
            "selected_rationale": selected_rationale,
            "representative_still_refs": representative_still_refs or [],
            "gate_results": [
                semantic.model_dump(mode="json"),
                continuity.model_dump(mode="json"),
                adjacency.model_dump(mode="json"),
            ],
            "result": result,
        }
        return SceneVisualEvaluation(**payload, content_hash=stable_hash(payload))

    evaluate = evaluate_scene


VisualContinuityEvaluator = VisualEvaluationService
VisualDirectionContractCompiler = VisualDirectionCompiler


def semantic_match_score(intent: SceneVisualIntent | str, asset: VisualAssetEvidence) -> float:
    text = intent.semantic_intent if isinstance(intent, SceneVisualIntent) else str(intent)
    wanted = _meaningful_tokens(text)
    available = _meaningful_tokens(" ".join([asset.semantic_description, *asset.tags]))
    if not wanted:
        return 0.0
    coverage = len(wanted & available) / len(wanted)
    precision = len(wanted & available) / max(1, len(available))
    return round(min(1.0, 0.8 * coverage + 0.2 * min(1.0, precision * 3)), 6)


def visual_direction_fit_score(contract: VisualDirectionContract, asset: VisualAssetEvidence) -> float:
    comparisons = [
        _categorical_fit(contract.environment_type, asset.environment_type),
        _categorical_fit(contract.industry_context, asset.industry_context),
        _categorical_fit(contract.lighting_direction, asset.lighting_direction),
        _categorical_fit(contract.lighting_temperature, asset.lighting_temperature),
        _categorical_fit(contract.camera_distance, asset.camera_distance),
        _categorical_fit(contract.lens_feel, asset.lens_feel),
        _categorical_fit(contract.camera_movement, asset.camera_movement),
        _categorical_fit(contract.motion_intensity, asset.motion_intensity),
        _categorical_fit(contract.framing_rule, asset.framing_rule),
        _categorical_fit(contract.tone_mode, asset.tone_mode),
        _set_fit(contract.palette, asset.palette),
    ]
    known = [value for value in comparisons if value is not None]
    return round(sum(known) / len(known), 6) if known else 0.5


def adjacency_continuity_score(left: VisualAssetEvidence, right: VisualAssetEvidence) -> float:
    comparisons = [
        _categorical_fit(left.environment_type, right.environment_type),
        _categorical_fit(left.lighting_temperature, right.lighting_temperature),
        _categorical_fit(left.camera_distance, right.camera_distance),
        _categorical_fit(left.camera_movement, right.camera_movement),
        _categorical_fit(left.motion_intensity, right.motion_intensity),
        _categorical_fit(left.tone_mode, right.tone_mode),
        _set_fit(left.palette, right.palette),
    ]
    if left.motion_energy is not None and right.motion_energy is not None:
        comparisons.append(1.0 - abs(left.motion_energy - right.motion_energy))
    known = [value for value in comparisons if value is not None]
    return round(sum(known) / len(known), 6) if known else 0.5


def detect_hard_visual_conflicts(
    *,
    asset: VisualAssetEvidence,
    contract: VisualDirectionContract,
) -> list[str]:
    reasons = list(asset.hard_conflict_reasons)
    if asset.source_class != "NATIVE_VISUAL" and asset.logo_or_text_present is True:
        reasons.append("BRAND_OR_LOGO_RISK")
    if asset.brand_or_trademark_present is True:
        reasons.append("BRAND_OR_LOGO_RISK")
    if asset.fake_ui_used_as_evidence:
        reasons.append("FAKE_UI_USED_AS_FACTUAL_EVIDENCE")
    if asset.implies_endorsement:
        reasons.append("STOCK_IMPLIES_ENDORSEMENT")
    if asset.identifiable_person_present is True and _human_presence_forbidden(contract.human_presence_policy):
        reasons.append("NO_CHARACTER_CONFLICT")
    if _opposed_temperature(contract.lighting_temperature, asset.lighting_temperature):
        reasons.append("STRONG_COLOR_TEMPERATURE_CONFLICT")
    if _camera_language_jolt(contract.camera_movement, asset.camera_movement):
        reasons.append("CAMERA_LANGUAGE_JOLT")
    if _motion_intensity_conflict(contract.motion_intensity, asset.motion_intensity):
        reasons.append("MOTION_INTENSITY_CONFLICT")
    normalized_description = _normalized_phrase(asset.semantic_description)
    for cliche in contract.prohibited_cliches:
        if _normalized_phrase(cliche) in normalized_description:
            reasons.append("PROHIBITED_VISUAL_CLICHE")
            break
    return _dedupe_strings(reasons)


def _threshold_verdict(score: float, *, pass_min: float, review_min: float) -> str:
    if score >= pass_min:
        return "PASS"
    if score >= review_min:
        return "REVIEW_REQUIRED"
    return "BLOCK"


def _bounded_score(value: float) -> float:
    number = float(value)
    if number < 0 or number > 1:
        raise ValueError("VISUAL_SCORE_OUT_OF_RANGE")
    return round(number, 6)


def _meaningful_tokens(value: str) -> set[str]:
    stop = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2 and token not in stop}


def _normalized_phrase(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _human_presence_forbidden(value: str) -> bool:
    normalized = _normalized_phrase(value)
    return any(
        phrase in normalized
        for phrase in ("no character", "no person", "no people", "human forbidden", "humans forbidden")
    ) or normalized in {"none", "forbidden", "disallowed"}


def _opposed_temperature(expected: str, actual: str | None) -> bool:
    if not actual:
        return False
    expected_tokens = _meaningful_tokens(expected)
    actual_tokens = _meaningful_tokens(actual)
    return ("warm" in expected_tokens and "cool" in actual_tokens) or (
        "cool" in expected_tokens and "warm" in actual_tokens
    )


def _camera_language_jolt(expected: str, actual: str | None) -> bool:
    if not actual or "restrained" not in _meaningful_tokens(expected):
        return False
    return bool(_meaningful_tokens(actual) & {"aggressive", "chaotic", "whip", "shaky", "rapid"})


def _motion_intensity_conflict(expected: str, actual: str | None) -> bool:
    if not actual:
        return False
    expected_tokens = _meaningful_tokens(expected)
    actual_tokens = _meaningful_tokens(actual)
    return "high" in actual_tokens and "high" not in expected_tokens


def _categorical_fit(expected: str | None, actual: str | None) -> float | None:
    if not expected or not actual:
        return None
    left, right = _meaningful_tokens(expected), _meaningful_tokens(actual)
    if not left or not right:
        return 1.0 if _normalized_phrase(expected) == _normalized_phrase(actual) else 0.0
    return len(left & right) / len(left | right)


def _set_fit(expected: Iterable[str], actual: Iterable[str]) -> float | None:
    left = {_normalized_phrase(item) for item in expected if _normalized_phrase(item)}
    right = {_normalized_phrase(item) for item in actual if _normalized_phrase(item)}
    if not left or not right:
        return None
    return len(left & right) / len(left | right)


def _dedupe_strings(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
