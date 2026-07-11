from __future__ import annotations

import re

from app.contracts.asset_acquisition import (
    AssetRequest,
    CandidateScore,
    ParsedStockCandidate,
    RejectedCandidate,
    StockCandidateRankingManifest,
)
from app.services.native_render_plan import stable_hash


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


class StockCandidateRanker:
    def rank(
        self,
        request: AssetRequest,
        candidates: list[ParsedStockCandidate],
        *,
        previous_asset_usage_refs: list[str] | None = None,
    ) -> StockCandidateRankingManifest:
        if request.requested_role != "SUPPORTING_STOCK":
            raise ValueError("STOCK_RANKING_ROLE_INVALID")
        scored: list[CandidateScore] = []
        rejected: list[RejectedCandidate] = []
        for candidate in candidates:
            hard_reasons = self._hard_rejections(candidate)
            if hard_reasons:
                rejected.append(RejectedCandidate(candidate_id=candidate.candidate_id, reason_codes=hard_reasons))
                continue
            dimensions = self._dimensions(request, candidate)
            score = round(sum(dimensions[key] * WEIGHTS[key] for key in WEIGHTS) * 100, 6)
            reasons = []
            if candidate.prior_use_count:
                reasons.append("PRIOR_USE_SIMILARITY_RISK")
            if any(value is None for value in (candidate.logo_or_text_present, candidate.identifiable_person_present, candidate.brand_or_trademark_present)):
                reasons.append("METADATA_RISK_REQUIRES_HUMAN_REVIEW")
            scored.append(CandidateScore(candidate_id=candidate.candidate_id, total_score=score, dimensions=dimensions, reason_codes=reasons))
        scored.sort(key=lambda item: (-item.total_score, item.candidate_id))
        selected = scored[0].candidate_id if scored else None
        selected_candidate = next((item for item in candidates if item.candidate_id == selected), None)
        # Metadata ranking cannot replace rights/person/logo review in AS1.
        review = True
        reason_codes = ["DETERMINISTIC_METADATA_MULTIDIMENSIONAL_RANKING"]
        if any(candidate.prior_use_count for candidate in candidates):
            reason_codes.append("SAME_ASSET_REUSE_RISK_REPRESENTED")
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

    @staticmethod
    def _hard_rejections(candidate: ParsedStockCandidate) -> list[str]:
        reasons: list[str] = []
        if not candidate.source_complete:
            reasons.append("SOURCE_METADATA_INCOMPLETE")
        if candidate.logo_or_text_present is True:
            reasons.append("UNWANTED_LOGO_OR_TEXT")
        if candidate.brand_or_trademark_present is True:
            reasons.append("BRAND_TRADEMARK_RISK")
        return reasons

    @staticmethod
    def _dimensions(request: AssetRequest, candidate: ParsedStockCandidate) -> dict[str, float]:
        intent = set(re.findall(r"[a-z0-9]+", request.semantic_visual_intent.lower()))
        metadata = set(candidate.tags) | set(re.findall(r"[a-z0-9]+", candidate.description.lower()))
        overlap = len(intent & metadata) / max(1, len(intent))
        # Semantic fit includes curated metadata signals; keyword overlap is only one sub-signal.
        semantic = min(1.0, 0.55 * overlap + 0.25 * candidate.channel_identity_fit + 0.20 * candidate.motion_suitability)
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
