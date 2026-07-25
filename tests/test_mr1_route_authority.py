from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.contracts.visual_routing import VisualSourceRoute
from app.services.mr1_local_production import (
    MR1LocalProductionContinuation,
    MR1_SC04_NATIVE_MOTION_BLUEPRINT,
    MR1_SCENE_VISUAL_BLUEPRINTS,
)
from app.services.mr1_real_production import MR1RealProductionService
from app.services.mr1_route_authority import (
    ALL_MR1_SCENES,
    resolve_mr1_visual_route_authority,
)


def _authority(*, native_sc04: bool) -> dict:
    pexels_scenes = {"SC-07", "SC-09"}
    if not native_sc04:
        pexels_scenes.add("SC-04")
    decisions = []
    scene_routes = []
    visual_scenes = []
    for scene_id in ALL_MR1_SCENES:
        is_pexels = scene_id in pexels_scenes
        route = "PEXELS_VIDEO" if is_pexels else "NATIVE_DIAGRAM"
        if native_sc04 and scene_id == "SC-04":
            route = "NATIVE_MOTION_GRAPHIC"
        provider = "pexels_api" if is_pexels else "native"
        attempts = 1 if is_pexels else 0
        decision = {
            "scene_id": scene_id,
            "preferred_source_route": route,
            "provider": provider,
            "planned_requests": attempts,
            "maximum_automated_attempts": attempts,
            "eligibility": "TEST_ROUTE_AUTHORITY",
        }
        if native_sc04:
            decision["automatic_pexels_to_ai_fallback"] = False
            decision["provider_execution_required"] = is_pexels
        decisions.append(decision)
        scene_routes.append(
            {
                "scene_id": scene_id,
                "route": route,
                "provider": provider,
                "attempt_cap": attempts,
            }
        )
        visual_scenes.append(
            {
                "scene_id": scene_id,
                "semantic_intent": (
                    MR1_SC04_NATIVE_MOTION_BLUEPRINT["semantic_intent"]
                    if native_sc04 and scene_id == "SC-04"
                    else MR1_SCENE_VISUAL_BLUEPRINTS[scene_id]["semantic_intent"]
                ),
                **(
                    {"native_mechanism": MR1_SC04_NATIVE_MOTION_BLUEPRINT["mechanism"]}
                    if native_sc04 and scene_id == "SC-04"
                    else {}
                ),
            }
        )
    return {
        "approval_id": str(uuid.uuid4()),
        "approval_content_hash": "a" * 64,
        "cost_scope": {"hard_cap": 1.0},
        "resolved": {
            "visual_source_decision_set": {
                "artifact_version_id": str(uuid.uuid4()),
                "content": {
                    "one_route_per_scene": True,
                    "automatic_pexels_to_ai_fallback": False,
                    "decisions": decisions,
                },
            },
            "provider_execution_plan": {
                "content": {
                    "one_route_per_scene": True,
                    "automatic_pexels_to_ai_fallback": False,
                    "external_ai_video_fallback": False,
                    "scene_routes": scene_routes,
                }
            },
            "visual_plan": {"content": {"scenes": visual_scenes}},
        },
    }


def _timeline() -> SimpleNamespace:
    segments = []
    start_ms = 0
    for scene_id in ALL_MR1_SCENES:
        segments.append(
            SimpleNamespace(
                segment_id=scene_id,
                scene_start_ms=start_ms,
                scene_end_ms=start_ms + 10_000,
                target_scene_duration_ms=10_000,
            )
        )
        start_ms += 10_000
    return SimpleNamespace(segments=segments, timeline_hash="b" * 64)


def test_legacy_authority_inherits_global_fallback_and_keeps_sc04_pexels() -> None:
    authority = _authority(native_sc04=False)

    routes = resolve_mr1_visual_route_authority(authority)

    assert routes.pexels_scenes == ("SC-04", "SC-07", "SC-09")
    manifest = MR1LocalProductionContinuation._supporting_visual_subwindows_manifest(
        _timeline(), authority=authority
    )
    assert [item["scene_id"] for item in manifest["supporting_visual_subwindows"]] == [
        "SC-04",
        "SC-07",
        "SC-09",
    ]


def test_revised_sc04_native_route_creates_no_sc04_provider_authority() -> None:
    authority = _authority(native_sc04=True)

    routes = resolve_mr1_visual_route_authority(authority)
    decisions = MR1LocalProductionContinuation._visual_decisions(authority)
    blueprints = MR1LocalProductionContinuation._scene_visual_blueprints(authority)
    attempts = object.__new__(MR1RealProductionService)._initial_attempts(
        uuid.uuid4(),
        authority,
        budget_reservation={
            "reservation_ref": "mr1-budget://test",
            "request_hash": "c" * 64,
            "content_hash": "d" * 64,
            "status": "RESERVED",
            "reserved_amount_usd": 1.0,
        },
    )
    manifest = MR1LocalProductionContinuation._supporting_visual_subwindows_manifest(
        _timeline(), authority=authority
    )

    assert routes.pexels_scenes == ("SC-07", "SC-09")
    assert routes.native_scenes == (
        "SC-01",
        "SC-02",
        "SC-03",
        "SC-04",
        "SC-05",
        "SC-06",
        "SC-08",
    )
    assert next(
        item for item in decisions if item.scene_id == "SC-04"
    ).preferred_route == (VisualSourceRoute.NATIVE_MOTION_GRAPHIC)
    assert blueprints["SC-04"]["mechanism"] == (
        "BASELINE_CHECKLIST_THEN_INFORMATION_VS_JUDGMENT_SPLIT"
    )
    assert "pexels:SC-04" not in attempts
    assert {key for key in attempts if key.startswith("pexels:")} == {
        "pexels:SC-07",
        "pexels:SC-09",
    }
    assert [item["scene_id"] for item in manifest["supporting_visual_subwindows"]] == [
        "SC-07",
        "SC-09",
    ]


def test_route_authority_fails_closed_when_exact_artifacts_disagree() -> None:
    authority = _authority(native_sc04=True)
    authority["resolved"]["provider_execution_plan"]["content"]["scene_routes"][3][
        "route"
    ] = "PEXELS_VIDEO"

    with pytest.raises(ValueError, match="MR1_VISUAL_ROUTE_AUTHORITY_MISMATCH:SC-04"):
        resolve_mr1_visual_route_authority(authority)
