from __future__ import annotations

import math
import re

from app.contracts.asset_acquisition import AssetRequest, PexelsQueryPlan
from app.contracts.visual_direction import SceneVisualIntent, VisualDirectionContract
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
MEDIA_PRODUCTION_QUERY_SIGNALS = {
    "editing workflow",
    "media operator",
    "media production",
    "media workflow",
    "post production",
    "production workflow",
    "video editing",
    "video production",
    "video workflow",
}
MEDIA_PRODUCTION_QUERY_CORE = "video editing workstation post production"
PHYSICAL_PRODUCTION_QUERY_SIGNALS = {
    "behind the scenes",
    "camera crew",
    "cinematography",
    "film crew",
    "film set",
    "physical production",
    "production crew",
    "production set",
    "studio lighting",
}
PHYSICAL_PRODUCTION_QUERY_CORE = "film crew studio lighting production"
PHYSICAL_PRODUCTION_FORBIDDEN_CONCEPTS = {
    "apple",
    "computer",
    "imac",
    "interface",
    "laptop",
    "logo",
    "monitor",
    "phone",
    "screen",
    "software",
    "television",
    "tv",
    "ui",
}


def bind_minimum_duration_to_canonical_scene(
    request: AssetRequest,
    *,
    scene_duration_ms: int,
) -> AssetRequest:
    """Raise a stock request floor to the whole-second canonical scene need.

    Pexels reports clip durations in seconds. Rounding the canonical duration
    up prevents a one-shot search from selecting a clip that satisfies a
    coarse request floor but is still shorter than the real scene window.
    """

    if scene_duration_ms <= 0:
        raise ValueError("PEXELS_CANONICAL_SCENE_DURATION_INVALID")
    required_seconds = float(math.ceil(scene_duration_ms / 1000))
    minimum_seconds = max(float(request.minimum_duration_seconds), required_seconds)
    if minimum_seconds > float(request.maximum_duration_seconds):
        raise ValueError("PEXELS_CANONICAL_SCENE_EXCEEDS_REQUEST_MAXIMUM")
    payload = request.model_dump(mode="python", exclude={"request_hash"})
    payload["minimum_duration_seconds"] = minimum_seconds
    return AssetRequest(**payload, request_hash=stable_hash(payload))


class PexelsQueryPlanner:
    def plan(
        self,
        request: AssetRequest,
        *,
        size_preference: str = "large",
        per_page: int = 20,
        locale: str = "en-US",
        visual_direction: VisualDirectionContract | None = None,
        visual_direction_ref: str | None = None,
        scene_intent: SceneVisualIntent | None = None,
        previous_scene_summary: str | None = None,
        next_scene_summary: str | None = None,
        asset_reuse_history: list[str] | None = None,
    ) -> PexelsQueryPlan:
        if request.requested_role != "SUPPORTING_STOCK":
            raise ValueError("PEXELS_QUERY_REQUIRES_SUPPORTING_STOCK")
        if request.required_orientation not in {"landscape", "portrait", "square"}:
            raise ValueError("PEXELS_ORIENTATION_UNSUPPORTED")
        if size_preference not in {"small", "medium", "large"}:
            raise ValueError("PEXELS_SIZE_UNSUPPORTED")
        if not 1 <= per_page <= 40:
            raise ValueError("PEXELS_PER_PAGE_OUT_OF_RANGE")
        if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", locale):
            raise ValueError("PEXELS_LOCALE_UNSUPPORTED")
        normalized = re.sub(r"[^a-zA-Z0-9\s-]", " ", request.semantic_visual_intent).lower()
        if any(concept in normalized for concept in UNSAFE_QUERY_CONCEPTS):
            raise ValueError("PEXELS_UNSAFE_QUERY_CONCEPT")
        words = [word for word in normalized.split() if word not in STOP_WORDS and len(word) > 2][:7]
        if not words:
            raise ValueError("PEXELS_QUERY_INTENT_EMPTY")
        core = _semantic_query_core(normalized, words)
        prohibited = set(UNSAFE_QUERY_CONCEPTS)
        if _is_physical_production_intent(normalized):
            prohibited.update(PHYSICAL_PRODUCTION_FORBIDDEN_CONCEPTS)
        if visual_direction is None:
            queries = [
                f"{core} workplace b roll",
                f"{core} close up action",
                f"{core} clean composition",
            ]
            planner_version = "pexels-query-planner/v1.0.0"
        else:
            cliches = {_normalized_phrase(value) for value in visual_direction.prohibited_cliches}
            normalized_intent = _normalized_phrase(request.semantic_visual_intent)
            if any(cliche and cliche in normalized_intent for cliche in cliches):
                raise ValueError("PEXELS_PROHIBITED_CLICHE")
            prohibited.update(visual_direction.prohibited_cliches)
            environment = _compact_terms(visual_direction.environment_type, limit=3)
            industry = _compact_terms(visual_direction.industry_context, limit=3)
            camera = _compact_terms(visual_direction.camera_distance, limit=2)
            lighting = _compact_terms(visual_direction.lighting_temperature, limit=2)
            movement = _compact_terms(visual_direction.camera_movement, limit=2)
            queries = [
                " ".join(part for part in (core, environment, "grounded documentary") if part),
                " ".join(part for part in (core, industry, camera, "real action") if part),
                " ".join(part for part in (core, lighting, movement, "clean framing") if part),
            ]
            planner_version = "pexels-query-planner/v2.0.0"
        queries = [query[:80].strip() for query in queries]
        queries = list(dict.fromkeys(queries))
        if len(queries) < 2:
            queries.append(f"{core} grounded documentary detail"[:80])
        if any(query == request.semantic_visual_intent.strip().lower() for query in queries):
            raise ValueError("PEXELS_FULL_NARRATION_QUERY_FORBIDDEN")
        previous = previous_scene_summary or (scene_intent.previous_scene_summary if scene_intent else None)
        following = next_scene_summary or (scene_intent.next_scene_summary if scene_intent else None)
        payload = {
            "request_id": request.request_id,
            "queries": queries,
            "orientation": request.required_orientation,
            "size_preference": size_preference,
            "per_page": per_page,
            "minimum_resolution": request.minimum_resolution,
            "preferred_resolution": request.preferred_resolution,
            "minimum_duration_seconds": request.minimum_duration_seconds,
            "forbidden_concepts": sorted(prohibited),
            "endpoint": "/v1/videos/search",
        }
        if visual_direction is not None:
            payload.update(
                {
                    "planner_version": planner_version,
                    "locale": locale,
                    "visual_direction_ref": visual_direction_ref
                    or (
                        f"artifact://visual-direction/{visual_direction.channel_id}/"
                        f"{visual_direction.project_id}/{visual_direction.contract_version}"
                    ),
                    "visual_direction_hash": visual_direction.content_hash,
                    "target_duration_seconds": (
                        scene_intent.target_duration_seconds if scene_intent else request.maximum_duration_seconds
                    ),
                    "aspect_ratio": scene_intent.aspect_ratio if scene_intent else None,
                    "crop_safety_required": scene_intent.crop_safety_required if scene_intent else True,
                    "previous_scene_summary": previous,
                    "next_scene_summary": following,
                    "asset_reuse_history": sorted(set(asset_reuse_history or [])),
                }
            )
        return PexelsQueryPlan(**payload, plan_hash=stable_hash(payload))


def _compact_terms(value: str, *, limit: int) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9\s-]", " ", value).replace("_", " ").replace("-", " ").lower()
    words = [word for word in normalized.split() if word not in STOP_WORDS and len(word) > 2]
    return " ".join(words[:limit])


def _semantic_query_core(normalized_intent: str, words: list[str]) -> str:
    corpus = " ".join(re.findall(r"[a-z0-9]+", normalized_intent.lower()))
    padded = f" {corpus} "
    if any(f" {signal} " in padded for signal in PHYSICAL_PRODUCTION_QUERY_SIGNALS):
        return PHYSICAL_PRODUCTION_QUERY_CORE
    if any(f" {signal} " in padded for signal in MEDIA_PRODUCTION_QUERY_SIGNALS):
        return MEDIA_PRODUCTION_QUERY_CORE
    return " ".join(words[:4])


def _is_physical_production_intent(normalized_intent: str) -> bool:
    corpus = " ".join(re.findall(r"[a-z0-9]+", normalized_intent.lower()))
    padded = f" {corpus} "
    return any(
        f" {signal} " in padded
        for signal in PHYSICAL_PRODUCTION_QUERY_SIGNALS
    )


def _normalized_phrase(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))
