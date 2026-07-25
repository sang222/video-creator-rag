from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping

from app.contracts.asset_acquisition import AssetRequest
from app.services.config_registry import content_hash
from app.services.pexels_query_planner import PexelsQueryPlanner
from app.services.stock_candidate_ranker import observable_semantic_tokens


MR1_PEXELS_MINIMUM_OBSERVABLE_INTENT_TOKENS = 2
PEXELS_GENERIC_QUERY_CORE_TOKEN_CAP = 4


def mr1_pexels_stock_search_intent(
    payload: Mapping[str, Any],
) -> str:
    """Resolve only the observable stock-search sub-intent.

    The package-bound scene ``semantic_intent`` remains the whole-scene
    authority.  A supplemental ``stock_search_intent`` may narrow only the
    Pexels supporting subwindow and must stay separately request-hash bound.
    """

    return str(
        payload.get("stock_search_intent")
        or payload.get("semantic_intent")
        or ""
    ).strip()


def build_mr1_pexels_asset_request(payload: Mapping[str, Any]) -> AssetRequest:
    """Build the exact supporting-stock request used by the MR1 gateway."""

    core = {
        "request_id": payload["idempotency_key"],
        "scene_id": payload["scene_id"],
        "source_segment_ids": [payload["scene_id"]],
        "purpose": "REAL_APPROVED_PRODUCTION",
        "requested_role": "SUPPORTING_STOCK",
        "semantic_visual_intent": mr1_pexels_stock_search_intent(payload),
        "required_orientation": "landscape",
        "minimum_resolution": "1920x1080",
        "preferred_resolution": "1920x1080",
        "minimum_duration_seconds": float(payload["minimum_duration_seconds"]),
        "maximum_duration_seconds": float(payload["maximum_duration_seconds"]),
        "crop_policy": "CROP_SAFE_16_9",
        "person_policy": "SUPPORTING_CONTEXT_ONLY",
        "logo_text_policy": "NO_LOGOS_OR_READABLE_BRAND_TEXT",
        "evidence_usage_policy": "OBSERVABLE_REALITY_SUPPORT_ONLY",
        "fallback_order": ["SUPPORTING_STOCK"],
        "projected_cost_class": "NONE",
        "human_review_required": True,
    }
    return AssetRequest(**core, request_hash=content_hash(core))


def build_mr1_pexels_query_authority(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the deterministic query family that an approval must bind."""

    plan = PexelsQueryPlanner().plan(
        build_mr1_pexels_asset_request(payload),
        per_page=20,
    )
    return {
        "schema_version": "mr1.pexels-query-authority.v1",
        "intent_field": (
            "stock_search_intent"
            if str(payload.get("stock_search_intent") or "").strip()
            else "semantic_intent"
        ),
        "package_semantic_intent": str(
            payload.get("semantic_intent") or ""
        ).strip(),
        "stock_search_intent": mr1_pexels_stock_search_intent(payload),
        "planner_version": plan.planner_version,
        "locale": plan.locale,
        "endpoint": plan.endpoint,
        "query_family": deepcopy(plan.queries),
        "primary_query": plan.queries[0],
        "orientation": plan.orientation,
        "size_preference": plan.size_preference,
        "per_page": plan.per_page,
        "minimum_resolution": plan.minimum_resolution,
        "preferred_resolution": plan.preferred_resolution,
        "minimum_duration_seconds": plan.minimum_duration_seconds,
        "plan_hash": plan.plan_hash,
    }


def mr1_pexels_stock_search_intent_coverage_evidence(
    payload: Mapping[str, Any],
    query_authority: Mapping[str, Any],
    *,
    semantic_fit_threshold: float,
) -> dict[str, Any]:
    """Prove that an approved query can express its own semantic intent.

    Pexels does not expose detailed title/tag metadata through its API. Before
    a one-shot request is authorized, the query core must therefore retain the
    integer number of observable intent tokens required by the frozen
    candidate threshold.  The candidate itself must later independently meet
    the same integer requirement from its public URL-derived page slug.
    """

    if (
        isinstance(semantic_fit_threshold, bool)
        or not isinstance(semantic_fit_threshold, (int, float))
        or not 0 < float(semantic_fit_threshold) <= 1
    ):
        raise ValueError("MR1_PEXELS_QUERY_INTENT_THRESHOLD_INVALID")
    package_semantic_intent = str(
        payload.get("semantic_intent") or ""
    ).strip()
    stock_search_intent = mr1_pexels_stock_search_intent(payload)
    primary_query = str(query_authority.get("primary_query") or "").strip()
    intent_tokens = observable_semantic_tokens(stock_search_intent)
    query_tokens = observable_semantic_tokens(primary_query)
    if not intent_tokens or not query_tokens:
        raise ValueError("MR1_PEXELS_QUERY_INTENT_EVIDENCE_MISSING")
    matched_tokens = intent_tokens & query_tokens
    coverage = len(matched_tokens) / len(intent_tokens)
    required_matched_intent_token_count = math.ceil(
        float(semantic_fit_threshold) * len(intent_tokens)
    )
    token_count_valid = (
        len(intent_tokens)
        >= MR1_PEXELS_MINIMUM_OBSERVABLE_INTENT_TOKENS
    )
    required_match_available = (
        len(matched_tokens) >= required_matched_intent_token_count
    )
    evidence = {
        "schema_version": "mr1.pexels-stock-search-intent-coverage.v1",
        "package_semantic_intent": package_semantic_intent,
        "stock_search_intent": stock_search_intent,
        "intent_field": (
            "stock_search_intent"
            if str(payload.get("stock_search_intent") or "").strip()
            else "semantic_intent"
        ),
        "primary_query": primary_query,
        "intent_tokens": sorted(intent_tokens),
        "query_tokens": sorted(query_tokens),
        "matched_intent_tokens": sorted(matched_tokens),
        "intent_token_count": len(intent_tokens),
        "minimum_observable_intent_token_count": (
            MR1_PEXELS_MINIMUM_OBSERVABLE_INTENT_TOKENS
        ),
        "planner_generic_query_core_token_cap": (
            PEXELS_GENERIC_QUERY_CORE_TOKEN_CAP
        ),
        "required_matched_intent_token_count": (
            required_matched_intent_token_count
        ),
        "matched_intent_token_count": len(matched_tokens),
        "maximum_missing_intent_token_count_at_threshold": (
            len(intent_tokens) - required_matched_intent_token_count
        ),
        "query_retention_margin_tokens": (
            len(matched_tokens) - required_matched_intent_token_count
        ),
        "query_intent_coverage": round(coverage, 6),
        "semantic_fit_threshold": float(semantic_fit_threshold),
        "intent_token_count_result": "PASS" if token_count_valid else "FAIL",
        "required_match_availability_result": (
            "PASS" if required_match_available else "FAIL"
        ),
        "result": (
            "PASS"
            if token_count_valid and required_match_available
            else "FAIL"
        ),
    }
    evidence["content_hash"] = content_hash(evidence)
    if not token_count_valid:
        raise ValueError("MR1_PEXELS_QUERY_INTENT_TOKEN_COUNT_INADEQUATE")
    if not required_match_available:
        raise ValueError("MR1_PEXELS_QUERY_INTENT_COVERAGE_INADEQUATE")
    return evidence


def mr1_pexels_query_intent_coverage_evidence(
    payload: Mapping[str, Any],
    query_authority: Mapping[str, Any],
    *,
    semantic_fit_threshold: float,
) -> dict[str, Any]:
    """Backward-compatible name for stock-search sub-intent coverage."""

    return mr1_pexels_stock_search_intent_coverage_evidence(
        payload,
        query_authority,
        semantic_fit_threshold=semantic_fit_threshold,
    )
