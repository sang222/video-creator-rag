from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


ALL_MR1_SCENES = tuple(f"SC-{index:02d}" for index in range(1, 10))
PEXELS_VIDEO_ROUTE = "PEXELS_VIDEO"
NATIVE_VISUAL_ROUTES = frozenset(
    {
        "NATIVE_DIAGRAM",
        "NATIVE_MOTION_GRAPHIC",
        "EDITORIAL_TEXT_GRAPHIC",
    }
)


@dataclass(frozen=True)
class MR1VisualRouteAuthority:
    """Cross-validated scene routes from the two exact package authorities."""

    routes: Mapping[str, str]
    providers: Mapping[str, str]
    pexels_scenes: tuple[str, ...]
    native_scenes: tuple[str, ...]


def resolve_mr1_visual_route_authority(
    authority: Mapping[str, Any],
) -> MR1VisualRouteAuthority:
    """Resolve current or revised MR1 routes without scene-id assumptions.

    Both the exact VisualSourceDecisionSet and ProviderExecutionPlan must agree.
    A malformed, incomplete, duplicated, or newly unsupported route fails closed.
    """

    resolved = authority.get("resolved") or {}
    decisions_content = (resolved.get("visual_source_decision_set") or {}).get(
        "content"
    ) or {}
    provider_content = (resolved.get("provider_execution_plan") or {}).get(
        "content"
    ) or {}
    raw_decisions = decisions_content.get("decisions")
    raw_routes = provider_content.get("scene_routes")
    if not isinstance(raw_decisions, list) or not isinstance(raw_routes, list):
        raise ValueError("MR1_VISUAL_ROUTE_AUTHORITY_MISSING")
    if not all(isinstance(item, Mapping) for item in raw_decisions + raw_routes):
        raise ValueError("MR1_VISUAL_ROUTE_AUTHORITY_ITEM_INVALID")

    decision_scene_ids = [str(item.get("scene_id") or "") for item in raw_decisions]
    provider_scene_ids = [str(item.get("scene_id") or "") for item in raw_routes]
    expected = set(ALL_MR1_SCENES)
    if (
        len(raw_decisions) != len(ALL_MR1_SCENES)
        or len(raw_routes) != len(ALL_MR1_SCENES)
        or set(decision_scene_ids) != expected
        or set(provider_scene_ids) != expected
        or len(set(decision_scene_ids)) != len(decision_scene_ids)
        or len(set(provider_scene_ids)) != len(provider_scene_ids)
    ):
        raise ValueError("MR1_VISUAL_ROUTE_AUTHORITY_SCENE_SET_INVALID")
    if (
        decisions_content.get("one_route_per_scene") is not True
        or provider_content.get("one_route_per_scene") is not True
        or decisions_content.get("automatic_pexels_to_ai_fallback") is not False
        or provider_content.get("automatic_pexels_to_ai_fallback") is not False
        or provider_content.get("external_ai_video_fallback") is not False
    ):
        raise ValueError("MR1_VISUAL_ROUTE_AUTHORITY_POLICY_INVALID")

    by_decision = dict(zip(decision_scene_ids, raw_decisions, strict=True))
    by_provider = dict(zip(provider_scene_ids, raw_routes, strict=True))
    routes: dict[str, str] = {}
    providers: dict[str, str] = {}
    pexels_scenes: list[str] = []
    native_scenes: list[str] = []
    for scene_id in ALL_MR1_SCENES:
        decision = by_decision[scene_id]
        provider_route = by_provider[scene_id]
        route = str(decision.get("preferred_source_route") or "")
        provider = str(decision.get("provider") or "")
        if route == PEXELS_VIDEO_ROUTE:
            expected_provider = "pexels_api"
            expected_attempts = 1
            pexels_scenes.append(scene_id)
        elif route in NATIVE_VISUAL_ROUTES:
            expected_provider = "native"
            expected_attempts = 0
            native_scenes.append(scene_id)
        else:
            raise ValueError(f"MR1_VISUAL_ROUTE_UNSUPPORTED:{scene_id}:{route}")

        values = (
            decision.get("planned_requests"),
            decision.get("maximum_automated_attempts"),
            provider_route.get("attempt_cap"),
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) for value in values
        ):
            raise ValueError(f"MR1_VISUAL_ROUTE_ATTEMPT_AUTHORITY_INVALID:{scene_id}")
        if (
            provider != expected_provider
            or str(provider_route.get("route") or "") != route
            or str(provider_route.get("provider") or "") != provider
            or any(value != expected_attempts for value in values)
            or decision.get("automatic_pexels_to_ai_fallback", False) is not False
        ):
            raise ValueError(f"MR1_VISUAL_ROUTE_AUTHORITY_MISMATCH:{scene_id}")
        execution_required = decision.get("provider_execution_required")
        if execution_required is not None and execution_required is not (
            route == PEXELS_VIDEO_ROUTE
        ):
            raise ValueError(
                f"MR1_VISUAL_ROUTE_EXECUTION_REQUIREMENT_INVALID:{scene_id}"
            )
        routes[scene_id] = route
        providers[scene_id] = provider

    return MR1VisualRouteAuthority(
        routes=MappingProxyType(routes),
        providers=MappingProxyType(providers),
        pexels_scenes=tuple(pexels_scenes),
        native_scenes=tuple(native_scenes),
    )
