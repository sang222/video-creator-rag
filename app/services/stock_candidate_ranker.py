from __future__ import annotations

import re

from app.contracts.asset_acquisition import (
    AssetRequest,
    CandidateScore,
    ParsedStockCandidate,
    RejectedCandidate,
    StockCandidateRankingManifest,
)
from app.contracts.visual_direction import (
    VisualAssetEvidence,
    VisualDirectionContract,
    VisualRankingWeights,
    VisualRiskPenalties,
    VisualScoreThresholds,
)
from app.services.native_render_plan import stable_hash
from app.services.visual_direction import (
    adjacency_continuity_score,
    detect_hard_visual_conflicts,
    visual_direction_fit_score,
)


WEIGHTS = {
    "semantic_relevance": 0.20,
    "composition": 0.08,
    "resolution": 0.10,
    "duration": 0.08,
    "orientation_crop_safety": 0.10,
    "unwanted_logo_text": 0.08,
    "identifiable_person_risk": 0.08,
    "brand_trademark_risk": 0.06,
    "prior_use_similarity": 0.06,
    "motion_suitability": 0.06,
    "channel_visual_identity_fit": 0.06,
    "source_completeness": 0.04,
}

OBSERVABLE_SEMANTIC_POLICY_VERSION = (
    "candidate-description-tags-intent-coverage/v2"
)
CONFIRMED_QUERY_RETRIEVAL_REVIEW_FLOOR = 0.25
_SEMANTIC_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}


class StockCandidateRanker:
    def rank(
        self,
        request: AssetRequest,
        candidates: list[ParsedStockCandidate],
        *,
        previous_asset_usage_refs: list[str] | None = None,
        visual_direction: VisualDirectionContract | None = None,
        visual_direction_ref: str | None = None,
        previous_scene: VisualAssetEvidence | dict | str | None = None,
        next_scene: VisualAssetEvidence | dict | str | None = None,
        asset_reuse_history: list[str] | None = None,
        minimum_semantic_relevance: float | None = None,
        confirmed_query_retrieval: bool = False,
        weights: VisualRankingWeights | None = None,
        risk_penalties: VisualRiskPenalties | None = None,
        thresholds: VisualScoreThresholds | None = None,
    ) -> StockCandidateRankingManifest:
        if request.requested_role != "SUPPORTING_STOCK":
            raise ValueError("STOCK_RANKING_ROLE_INVALID")
        if visual_direction is not None:
            missing_policy = [
                name
                for name, value in (
                    ("ranking_weights", weights),
                    ("risk_penalties", risk_penalties),
                    ("score_thresholds", thresholds),
                )
                if value is None
            ]
            if missing_policy:
                raise ValueError("CONTEXTUAL_VISUAL_POLICY_REQUIRED:" + ",".join(missing_policy))
            return self._rank_contextual(
                request,
                candidates,
                visual_direction=visual_direction,
                visual_direction_ref=visual_direction_ref,
                previous_scene=previous_scene,
                next_scene=next_scene,
                previous_asset_usage_refs=previous_asset_usage_refs,
                asset_reuse_history=asset_reuse_history,
                weights=weights,
                risk_penalties=risk_penalties,
                thresholds=thresholds,
            )
        return self._rank_legacy(
            request,
            candidates,
            previous_asset_usage_refs=previous_asset_usage_refs,
            minimum_semantic_relevance=minimum_semantic_relevance,
            confirmed_query_retrieval=confirmed_query_retrieval,
        )

    def _rank_legacy(
        self,
        request: AssetRequest,
        candidates: list[ParsedStockCandidate],
        *,
        previous_asset_usage_refs: list[str] | None,
        minimum_semantic_relevance: float | None,
        confirmed_query_retrieval: bool,
    ) -> StockCandidateRankingManifest:
        if (
            minimum_semantic_relevance is not None
            and (
                isinstance(minimum_semantic_relevance, bool)
                or not isinstance(minimum_semantic_relevance, (int, float))
                or not 0 < float(minimum_semantic_relevance) <= 1
            )
        ):
            raise ValueError("STOCK_MINIMUM_SEMANTIC_RELEVANCE_INVALID")
        scored: list[CandidateScore] = []
        rejected: list[RejectedCandidate] = []
        for candidate in candidates:
            hard_reasons = self._hard_rejections(candidate)
            if hard_reasons:
                rejected.append(RejectedCandidate(candidate_id=candidate.candidate_id, reason_codes=hard_reasons))
                continue
            dimensions = self._dimensions(
                request,
                candidate,
                confirmed_query_retrieval=confirmed_query_retrieval,
            )
            score = round(sum(dimensions[key] * WEIGHTS[key] for key in WEIGHTS) * 100, 6)
            reasons = []
            if candidate.prior_use_count:
                reasons.append("PRIOR_USE_SIMILARITY_RISK")
            if any(value is None for value in (candidate.logo_or_text_present, candidate.identifiable_person_present, candidate.brand_or_trademark_present)):
                reasons.append("METADATA_RISK_REQUIRES_HUMAN_REVIEW")
            scored.append(CandidateScore(candidate_id=candidate.candidate_id, total_score=score, dimensions=dimensions, reason_codes=reasons))
        scored.sort(key=lambda item: (-item.total_score, item.candidate_id))
        selected_score = next(
            (
                item
                for item in scored
                if minimum_semantic_relevance is None
                or item.dimensions["semantic_relevance"]
                >= float(minimum_semantic_relevance)
            ),
            None,
        )
        selected = selected_score.candidate_id if selected_score is not None else None
        # Metadata ranking cannot replace rights/person/logo review in AS1.
        review = True
        reason_codes = [
            "DETERMINISTIC_METADATA_MULTIDIMENSIONAL_RANKING",
            "OBSERVABLE_CANDIDATE_TEXT_SEMANTIC_EVIDENCE",
        ]
        if any(candidate.prior_use_count for candidate in candidates):
            reason_codes.append("SAME_ASSET_REUSE_RISK_REPRESENTED")
        if minimum_semantic_relevance is not None:
            reason_codes.append("INDEPENDENT_SEMANTIC_SELECTION_GATE")
            if selected is None:
                reason_codes.append("NO_CANDIDATE_MET_SEMANTIC_SELECTION_GATE")
        if review:
            reason_codes.append("HUMAN_REVIEW_BOUNDARY")
        payload = {
            "request_id": request.request_id,
            "candidate_ids": sorted(candidate.candidate_id for candidate in candidates),
            "candidate_scores": [item.model_dump(mode="json") for item in scored],
            "rejected_candidates": [item.model_dump(mode="json") for item in sorted(rejected, key=lambda item: item.candidate_id)],
            "selected_candidate_id": selected,
            "ranking_reason_codes": reason_codes,
            "previous_asset_usage_refs": sorted(previous_asset_usage_refs or []),
            "selection_requires_human_review": review,
        }
        return StockCandidateRankingManifest(**payload, manifest_hash=stable_hash(payload))

    def _rank_contextual(
        self,
        request: AssetRequest,
        candidates: list[ParsedStockCandidate],
        *,
        visual_direction: VisualDirectionContract,
        visual_direction_ref: str | None,
        previous_scene: VisualAssetEvidence | dict | str | None,
        next_scene: VisualAssetEvidence | dict | str | None,
        previous_asset_usage_refs: list[str] | None,
        asset_reuse_history: list[str] | None,
        weights: VisualRankingWeights,
        risk_penalties: VisualRiskPenalties,
        thresholds: VisualScoreThresholds,
    ) -> StockCandidateRankingManifest:
        weight_map = weights.model_dump()
        scored: list[CandidateScore] = []
        rejected: list[RejectedCandidate] = []
        for candidate in candidates:
            hard_reasons = self._hard_rejections(candidate, visual_direction=visual_direction)
            if hard_reasons:
                rejected.append(RejectedCandidate(candidate_id=candidate.candidate_id, reason_codes=hard_reasons))
                continue
            dimensions = self._contextual_dimensions(
                request,
                candidate,
                visual_direction=visual_direction,
                previous_scene=previous_scene,
                next_scene=next_scene,
            )
            risk_penalty = self._risk_penalty(
                candidate,
                asset_reuse_history=asset_reuse_history,
                policy=risk_penalties,
            )
            score = max(0.0, min(1.0, sum(dimensions[key] * weight_map[key] for key in weight_map) - risk_penalty))
            reasons: list[str] = []
            if candidate.prior_use_count or candidate.candidate_id in set(asset_reuse_history or []):
                reasons.append("PRIOR_USE_SIMILARITY_RISK")
            if risk_penalty:
                reasons.append("EXPLICIT_RISK_PENALTY_APPLIED")
            if any(
                value is None
                for value in (
                    candidate.logo_or_text_present,
                    candidate.identifiable_person_present,
                    candidate.brand_or_trademark_present,
                )
            ):
                reasons.append("METADATA_RISK_REQUIRES_HUMAN_REVIEW")
            if dimensions["semantic_relevance"] < thresholds.semantic_review_min:
                reasons.append("SCENE_SEMANTIC_MISMATCH")
            elif dimensions["semantic_relevance"] < thresholds.semantic_pass_min:
                reasons.append("SCENE_SEMANTIC_MATCH_BORDERLINE")
            if min(dimensions["visual_direction_fit"], dimensions["previous_scene_continuity"], dimensions["next_scene_continuity"]) < thresholds.adjacency_review_min:
                reasons.append("VISUAL_CONTINUITY_CONFLICT")
            scored.append(
                CandidateScore(
                    candidate_id=candidate.candidate_id,
                    total_score=round(score, 6),
                    dimensions=dimensions | {"risk_penalty": round(risk_penalty, 6)},
                    reason_codes=reasons,
                )
            )
        scored.sort(key=lambda item: (-item.total_score, item.candidate_id))
        candidate_verdicts = {
            item.candidate_id: self._ranking_verdict(item, thresholds)
            for item in scored
        }
        selected_score = next(
            (item for item in scored if candidate_verdicts[item.candidate_id] != "BLOCK"),
            None,
        )
        top = selected_score or (scored[0] if scored else None)
        verdict = candidate_verdicts[top.candidate_id] if top is not None else "BLOCK"
        selected = selected_score.candidate_id if selected_score is not None else None
        reason_codes = ["DETERMINISTIC_CONTEXTUAL_VISUAL_RANKING", f"VISUAL_RANKING_{verdict}"]
        if any(candidate.prior_use_count for candidate in candidates) or asset_reuse_history:
            reason_codes.append("SAME_ASSET_REUSE_RISK_REPRESENTED")
        if verdict != "PASS":
            reason_codes.append("HUMAN_REVIEW_BOUNDARY")
        selected_rationale = None
        blocked_by_gate = [
            candidate_id for candidate_id, candidate_verdict in candidate_verdicts.items()
            if candidate_verdict == "BLOCK"
        ]
        if blocked_by_gate:
            reason_codes.append("INDEPENDENT_VISUAL_GATE_BLOCKED_CANDIDATE")
        if selected_score is not None:
            selected_rationale = (
                f"{top.candidate_id} selected after independent gates from deterministic semantic, direction, adjacency, "
                f"crop, motion, technical and originality signals; verdict={verdict}."
            )
        elif top is not None:
            selected_rationale = (
                f"No candidate selected; highest raw-score candidate {top.candidate_id} failed an independent "
                f"semantic or continuity gate; verdict={verdict}."
            )
        direction_ref = visual_direction_ref or (
            f"artifact://visual-direction/{visual_direction.channel_id}/{visual_direction.project_id}/"
            f"{visual_direction.contract_version}"
        )
        payload = {
            "request_id": request.request_id,
            "candidate_ids": sorted(candidate.candidate_id for candidate in candidates),
            "candidate_scores": [item.model_dump(mode="json") for item in scored],
            "rejected_candidates": [
                item.model_dump(mode="json") for item in sorted(rejected, key=lambda item: item.candidate_id)
            ],
            "selected_candidate_id": selected,
            "ranking_reason_codes": reason_codes,
            "previous_asset_usage_refs": sorted(previous_asset_usage_refs or []),
            "selection_requires_human_review": verdict != "PASS",
            "ranking_verdict": verdict,
            "visual_direction_ref": direction_ref,
            "visual_direction_hash": visual_direction.content_hash,
            "previous_scene_summary": _scene_summary(previous_scene),
            "next_scene_summary": _scene_summary(next_scene),
            "asset_reuse_history": sorted(set(asset_reuse_history or [])),
            "selected_rationale": selected_rationale,
            "ranking_weights": weight_map,
            "ranking_risk_penalties": risk_penalties.model_dump(),
            "ranking_thresholds": {
                "semantic_pass_min": thresholds.semantic_pass_min,
                "semantic_review_min": thresholds.semantic_review_min,
                "adjacency_pass_min": thresholds.adjacency_pass_min,
                "adjacency_review_min": thresholds.adjacency_review_min,
            },
        }
        return StockCandidateRankingManifest(**payload, manifest_hash=stable_hash(payload))

    @staticmethod
    def _hard_rejections(
        candidate: ParsedStockCandidate,
        *,
        visual_direction: VisualDirectionContract | None = None,
    ) -> list[str]:
        reasons: list[str] = []
        if not candidate.source_complete:
            reasons.append("SOURCE_METADATA_INCOMPLETE")
        if candidate.logo_or_text_present is True:
            reasons.append("UNWANTED_LOGO_OR_TEXT")
        if candidate.brand_or_trademark_present is True:
            reasons.append("BRAND_TRADEMARK_RISK")
        reasons.extend(candidate.hard_conflict_tags)
        if visual_direction is not None:
            corpus = _normalized_phrase(" ".join([candidate.description, *candidate.tags]))
            if any(_normalized_phrase(cliche) in corpus for cliche in visual_direction.prohibited_cliches):
                reasons.append("PROHIBITED_VISUAL_CLICHE")
            evidence = VisualAssetEvidence(
                scene_id="candidate-hard-gate",
                asset_ref=candidate.candidate_id,
                source_class="SUPPORTING_STOCK",
                semantic_description=candidate.description or " ".join(candidate.tags) or candidate.candidate_id,
                tags=candidate.tags,
                lighting_temperature=candidate.lighting_temperature,
                camera_movement=candidate.camera_movement,
                motion_intensity=candidate.motion_intensity,
                logo_or_text_present=candidate.logo_or_text_present,
                identifiable_person_present=candidate.identifiable_person_present,
                brand_or_trademark_present=candidate.brand_or_trademark_present,
                hard_conflict_reasons=candidate.hard_conflict_tags,
            )
            reasons.extend(detect_hard_visual_conflicts(asset=evidence, contract=visual_direction))
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _dimensions(
        request: AssetRequest,
        candidate: ParsedStockCandidate,
        *,
        confirmed_query_retrieval: bool = False,
    ) -> dict[str, float]:
        semantic = float(
            observable_semantic_relevance_evidence(
                request,
                candidate,
                confirmed_query_retrieval=confirmed_query_retrieval,
            )["semantic_relevance"]
        )
        min_width, min_height = (int(part) for part in request.minimum_resolution.split("x"))
        resolution = min(1.0, min(candidate.width / min_width, candidate.height / min_height))
        duration = 1.0 if request.minimum_duration_seconds <= candidate.duration_seconds <= request.maximum_duration_seconds + 5 else 0.4
        landscape = candidate.width >= candidate.height
        orientation = 1.0 if (request.required_orientation == "landscape" and landscape) or (request.required_orientation == "portrait" and not landscape) or request.required_orientation == "square" else 0.2
        return {
            "semantic_relevance": round(semantic, 6),
            "composition": 1.0 if candidate.composition.upper() in {"CLEAN", "CENTER_SAFE", "NEGATIVE_SPACE"} else 0.6,
            "resolution": round(resolution, 6),
            "duration": duration,
            "orientation_crop_safety": orientation,
            "unwanted_logo_text": 1.0 if candidate.logo_or_text_present is False else 0.5,
            "identifiable_person_risk": 1.0 if candidate.identifiable_person_present is False else 0.4 if candidate.identifiable_person_present is None else 0.2,
            "brand_trademark_risk": 1.0 if candidate.brand_or_trademark_present is False else 0.5,
            "prior_use_similarity": max(0.0, 1.0 - 0.35 * candidate.prior_use_count),
            "motion_suitability": candidate.motion_suitability,
            "channel_visual_identity_fit": candidate.channel_identity_fit,
            "source_completeness": 1.0 if candidate.source_complete else 0.0,
        }

    def _contextual_dimensions(
        self,
        request: AssetRequest,
        candidate: ParsedStockCandidate,
        *,
        visual_direction: VisualDirectionContract,
        previous_scene: VisualAssetEvidence | dict | str | None,
        next_scene: VisualAssetEvidence | dict | str | None,
    ) -> dict[str, float]:
        evidence = _candidate_evidence(request, candidate)
        semantic = candidate.semantic_relevance_score
        if semantic is None:
            semantic = float(
                observable_semantic_relevance_evidence(request, candidate)[
                    "semantic_relevance"
                ]
            )
        direction_fit = candidate.visual_direction_fit_score
        if direction_fit is None:
            direction_fit = visual_direction_fit_score(visual_direction, evidence)
        previous_fit = candidate.previous_scene_continuity_score
        if previous_fit is None:
            previous_fit = _adjacency_with_context(evidence, previous_scene)
        next_fit = candidate.next_scene_continuity_score
        if next_fit is None:
            next_fit = _adjacency_with_context(evidence, next_scene)
        crop = candidate.crop_safety_score
        if crop is None:
            crop = 1.0 if candidate.composition.upper() in {"CLEAN", "CENTER_SAFE", "NEGATIVE_SPACE"} else 0.6
            landscape = candidate.width >= candidate.height
            orientation_ok = (
                (request.required_orientation == "landscape" and landscape)
                or (request.required_orientation == "portrait" and not landscape)
                or request.required_orientation == "square"
            )
            if not orientation_ok:
                crop *= 0.3
        technical = candidate.technical_quality_score
        if technical is None:
            min_width, min_height = (int(part) for part in request.minimum_resolution.split("x"))
            technical = min(1.0, min(candidate.width / min_width, candidate.height / min_height))
        originality = candidate.originality_score
        if originality is None:
            originality = max(0.0, 1.0 - 0.25 * candidate.prior_use_count)
        return {
            "semantic_relevance": round(float(semantic), 6),
            "visual_direction_fit": round(float(direction_fit), 6),
            "previous_scene_continuity": round(float(previous_fit), 6),
            "next_scene_continuity": round(float(next_fit), 6),
            "crop_safety": round(float(crop), 6),
            "motion_suitability": round(float(candidate.motion_suitability), 6),
            "technical_quality": round(float(technical), 6),
            "originality_bonus": round(float(originality), 6),
        }

    @staticmethod
    def _risk_penalty(
        candidate: ParsedStockCandidate,
        *,
        asset_reuse_history: list[str] | None,
        policy: VisualRiskPenalties,
    ) -> float:
        penalty = candidate.explicit_risk_penalty
        penalty += min(policy.prior_use_cap, candidate.prior_use_count * policy.prior_use_per_count)
        if candidate.candidate_id in set(asset_reuse_history or []):
            penalty += policy.exact_asset_reuse
        if candidate.logo_or_text_present is None:
            penalty += policy.unknown_logo_or_text
        if candidate.identifiable_person_present is None:
            penalty += policy.unknown_person_identity
        if candidate.brand_or_trademark_present is None:
            penalty += policy.unknown_brand_or_trademark
        if candidate.identifiable_person_present is True:
            penalty += policy.identifiable_person_present
        return min(policy.total_cap, penalty)

    @staticmethod
    def _ranking_verdict(top: CandidateScore | None, thresholds: VisualScoreThresholds) -> str:
        if top is None:
            return "BLOCK"
        dimensions = top.dimensions
        semantic = dimensions["semantic_relevance"]
        continuity = min(
            dimensions["visual_direction_fit"],
            dimensions["previous_scene_continuity"],
            dimensions["next_scene_continuity"],
        )
        if semantic < thresholds.semantic_review_min or continuity < thresholds.adjacency_review_min:
            return "BLOCK"
        if (
            semantic < thresholds.semantic_pass_min
            or continuity < thresholds.adjacency_pass_min
            or "METADATA_RISK_REQUIRES_HUMAN_REVIEW" in top.reason_codes
        ):
            return "REVIEW_REQUIRED"
        return "PASS"


def observable_semantic_relevance_evidence(
    request: AssetRequest,
    candidate: ParsedStockCandidate,
    *,
    confirmed_query_retrieval: bool = False,
) -> dict[str, object]:
    """Score only provider-observable candidate text against the approved intent.

    Pexels search responses do not expose numeric semantic, channel-fit, or
    motion-fit scores.  Their model defaults therefore must not contribute to
    the independent semantic gate.  Description text (including the parser's
    public source-page slug fallback) and provider tags are the complete
    pre-download evidence set.
    """

    intent_tokens = observable_semantic_tokens(request.semantic_visual_intent)
    candidate_tokens = observable_semantic_tokens(
        " ".join([candidate.description, *candidate.tags])
    )
    matched_tokens = intent_tokens & candidate_tokens
    coverage = len(matched_tokens) / max(1, len(intent_tokens))
    retrieval_floor = (
        CONFIRMED_QUERY_RETRIEVAL_REVIEW_FLOOR
        if confirmed_query_retrieval
        else 0.0
    )
    semantic_relevance = max(coverage, retrieval_floor)
    return {
        "policy_version": OBSERVABLE_SEMANTIC_POLICY_VERSION,
        "formula": (
            "max(matched_intent_tokens / intent_tokens, "
            "confirmed_query_retrieval_review_floor)"
        ),
        "candidate_id": candidate.candidate_id,
        "intent_tokens": sorted(intent_tokens),
        "candidate_text_tokens": sorted(candidate_tokens),
        "matched_intent_tokens": sorted(matched_tokens),
        "intent_token_count": len(intent_tokens),
        "candidate_text_token_count": len(candidate_tokens),
        "matched_intent_token_count": len(matched_tokens),
        "lexical_intent_coverage": round(coverage, 6),
        "confirmed_query_retrieval": confirmed_query_retrieval,
        "confirmed_query_retrieval_review_floor": retrieval_floor,
        "semantic_relevance": round(semantic_relevance, 6),
        "provider_search_order_score_contribution": 0.0,
        "motion_suitability_score_contribution": 0.0,
        "channel_identity_fit_score_contribution": 0.0,
    }


def observable_semantic_tokens(value: str) -> set[str]:
    """Normalize the public-text tokens used by the semantic evidence gate."""

    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in _SEMANTIC_STOP_WORDS
    }


def _candidate_evidence(request: AssetRequest, candidate: ParsedStockCandidate) -> VisualAssetEvidence:
    return VisualAssetEvidence(
        scene_id=request.scene_id,
        asset_ref=candidate.candidate_id,
        source_class="SUPPORTING_STOCK",
        semantic_description=candidate.description or " ".join(candidate.tags) or candidate.candidate_id,
        tags=candidate.tags,
        environment_type=candidate.environment_type,
        industry_context=candidate.industry_context,
        lighting_direction=candidate.lighting_direction,
        lighting_temperature=candidate.lighting_temperature,
        palette=candidate.palette,
        camera_distance=candidate.shot_scale,
        camera_movement=candidate.camera_movement,
        motion_intensity=candidate.motion_intensity,
        motion_energy=candidate.motion_energy,
        crop_safety_score=candidate.crop_safety_score,
        technical_quality_score=candidate.technical_quality_score,
        originality_score=candidate.originality_score,
        logo_or_text_present=candidate.logo_or_text_present,
        identifiable_person_present=candidate.identifiable_person_present,
        brand_or_trademark_present=candidate.brand_or_trademark_present,
        hard_conflict_reasons=candidate.hard_conflict_tags,
        representative_still_refs=candidate.representative_still_refs,
    )


def _adjacency_with_context(
    candidate: VisualAssetEvidence,
    context: VisualAssetEvidence | dict | str | None,
) -> float:
    if context is None:
        return 1.0
    if isinstance(context, VisualAssetEvidence):
        return adjacency_continuity_score(context, candidate)
    candidate_tokens = _tokens(
        " ".join(
            value
            for value in (
                candidate.semantic_description,
                candidate.environment_type,
                candidate.lighting_temperature,
                candidate.camera_distance,
                candidate.camera_movement,
                candidate.motion_intensity,
                candidate.tone_mode,
                " ".join(candidate.palette),
            )
            if value
        )
    )
    context_tokens = _tokens(_scene_summary(context) or "")
    if not candidate_tokens or not context_tokens:
        return 0.5
    overlap = len(candidate_tokens & context_tokens) / len(candidate_tokens | context_tokens)
    return round(min(1.0, 0.45 + 1.5 * overlap), 6)


def _scene_summary(value: VisualAssetEvidence | dict | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, VisualAssetEvidence):
        return value.semantic_description
    if isinstance(value, str):
        return value
    return " ".join(str(item) for key, item in sorted(value.items()) if key not in {"asset_ref", "checksum"})


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2}


def _normalized_phrase(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))
