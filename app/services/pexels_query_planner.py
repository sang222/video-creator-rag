from __future__ import annotations

import re

from app.contracts.asset_acquisition import AssetRequest, PexelsQueryPlan
from app.services.native_render_plan import stable_hash


UNSAFE_QUERY_CONCEPTS = {
    "testimonial",
    "endorsement",
    "proof",
    "celebrity likeness",
    "recurring host",
    "fake result",
}
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
}


class PexelsQueryPlanner:
    def plan(self, request: AssetRequest, *, size_preference: str = "large", per_page: int = 20) -> PexelsQueryPlan:
        if request.requested_role != "SUPPORTING_STOCK":
            raise ValueError("PEXELS_QUERY_REQUIRES_SUPPORTING_STOCK")
        if request.required_orientation not in {"landscape", "portrait", "square"}:
            raise ValueError("PEXELS_ORIENTATION_UNSUPPORTED")
        if size_preference not in {"small", "medium", "large"}:
            raise ValueError("PEXELS_SIZE_UNSUPPORTED")
        if not 1 <= per_page <= 40:
            raise ValueError("PEXELS_PER_PAGE_OUT_OF_RANGE")
        normalized = re.sub(r"[^a-zA-Z0-9\s-]", " ", request.semantic_visual_intent).lower()
        if any(concept in normalized for concept in UNSAFE_QUERY_CONCEPTS):
            raise ValueError("PEXELS_UNSAFE_QUERY_CONCEPT")
        words = [word for word in normalized.split() if word not in STOP_WORDS and len(word) > 2][:7]
        if not words:
            raise ValueError("PEXELS_QUERY_INTENT_EMPTY")
        core = " ".join(words[:4])
        queries = [
            f"{core} workplace b roll",
            f"{core} close up action",
            f"{core} clean composition",
        ]
        queries = [query[:80].strip() for query in queries]
        if any(query == request.semantic_visual_intent.strip().lower() for query in queries):
            raise ValueError("PEXELS_FULL_NARRATION_QUERY_FORBIDDEN")
        payload = {
            "request_id": request.request_id,
            "queries": queries,
            "orientation": request.required_orientation,
            "size_preference": size_preference,
            "per_page": per_page,
            "minimum_resolution": request.minimum_resolution,
            "preferred_resolution": request.preferred_resolution,
            "minimum_duration_seconds": request.minimum_duration_seconds,
            "forbidden_concepts": sorted(UNSAFE_QUERY_CONCEPTS),
            "endpoint": "/v1/videos/search",
        }
        return PexelsQueryPlan(**payload, plan_hash=stable_hash(payload))
