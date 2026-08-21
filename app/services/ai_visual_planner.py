from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.contracts.editorial_authorship import validate_viewer_facing_presentation
from app.contracts.ai_visual_production import (
    AIVisualCapabilityProjection,
    AIVisualNarrationUnit,
    AIVisualPlanCompilation,
    AIVisualPlanningPolicy,
    AIVisualRoute,
    AIVisualScenePlan,
    CameraMotion,
    CompiledAIImagePrompt,
    CompiledAIVideoPrompt,
    MotionFunction,
    MotionIntentProjection,
    NormalizedPoint,
    NormalizedRegion,
    SubjectAnchor,
    TransitionPreset,
    VideoMotionGrammar,
    VideoVisualStyleBible,
    ai_visual_stable_hash,
    ai_visual_text_hash,
    seal_content_payload,
)


AI_VISUAL_PLANNER_VERSION = "unified-ai-visual-planner/1.0.0"
AI_IMAGE_PROMPT_COMPILER_VERSION = "ai-image-prompt-compiler/1.0.0"
AI_VIDEO_PROMPT_COMPILER_VERSION = "ai-video-prompt-compiler/1.0.0"
MOTION_INTENT_PLANNER_VERSION = "motion-intent-planner/1.0.0"


GLOBAL_AI_VISUAL_NEGATIVE_CONSTRAINTS = (
    "no presentation slide",
    "no PowerPoint",
    "no three-box flowchart",
    "no generic infographic card",
    "no text-heavy composition",
    "no fake dashboard",
    "no fake product UI",
    "no floating random labels",
    "no visible generated text",
    "no logo",
    "no watermark",
    "no meaningless AI symbols",
    "no robot-head cliché unless semantically necessary",
)


_TEMPORAL_VISUAL_FUNCTIONS = {
    "ACTION",
    "PROCESS",
    "PROGRESSION",
    "TRANSFORMATION",
    "TRANSITION_HERO",
    "FOLLOW",
}


@dataclass(frozen=True)
class _PlanningBeat:
    units: tuple[AIVisualNarrationUnit, ...]
    start_ms: int
    end_ms: int
    route: AIVisualRoute
    split_index: int = 1
    split_count: int = 1


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _scene_function_key(value: str) -> str:
    return value.strip().upper().replace("-", "_").replace(" ", "_")


def _authored_scene_semantic_key(scene: AIVisualScenePlan) -> str:
    """Return only authored scene bindings used for deterministic presentation."""

    return ai_visual_stable_hash(
        {
            "narration_unit_ids": scene.narration_unit_ids,
            "information_unit_ids": scene.information_unit_ids,
            "visual_function": scene.visual_function,
        }
    )


def _maximum_scene_duration_ms(
    unit: AIVisualNarrationUnit,
    policy: AIVisualPlanningPolicy,
    route: AIVisualRoute,
) -> int:
    function_maximum = policy.function_maximum_duration_ms.get(
        _scene_function_key(unit.visual_function),
        policy.maximum_ai_image_presentation_ms,
    )
    if route == "AI_IMAGE":
        return min(function_maximum, policy.maximum_ai_image_presentation_ms)
    return min(function_maximum, policy.maximum_ai_video_presentation_ms)


def _transition_for_reason(
    reason: str,
    grammar: VideoMotionGrammar,
    *,
    semantic_key: str | None = None,
) -> TransitionPreset:
    semantic_candidates: dict[str, tuple[TransitionPreset, ...]] = {
        "CONTINUATION": ("cut", "dissolve", "fade_soft"),
        "NEW_STEP": ("reveal_up", "slide_left", "cut"),
        # A restrained dissolve/fade remains semantically valid for contrast
        # when the active grammar excludes aggressive covers/slides.  Keeping
        # those fallbacks ahead of ``cut`` prevents a long comparison block
        # from collapsing into an ineligible run of identical cuts.
        "CONTRAST": (
            "cover_left",
            "slide_right",
            "dissolve",
            "fade_soft",
            "cut",
        ),
        "TOPIC_SHIFT": ("fade_soft", "dissolve", "cut"),
        "EXAMPLE_SHIFT": ("dissolve", "fade_soft", "cut"),
        "CONCLUSION": ("fade_black", "fade_soft", "cut"),
    }
    allowed = list(grammar.preferred_transition_family)
    candidates = semantic_candidates.get(reason, ("cut",))
    compatible = [candidate for candidate in candidates if candidate in allowed]
    if compatible:
        # Ordinal is retained as a compatibility argument, but never creates
        # variation.  Variation may only come from the authored semantic
        # identity of the scene, which keeps repeated semantic transitions
        # from becoming a mechanical preset loop.
        if semantic_key:
            digest = hashlib.sha256(semantic_key.encode("utf-8")).digest()
            return compatible[int.from_bytes(digest[:4], "big") % len(compatible)]
        return compatible[0]
    if "cut" in allowed:
        return "cut"
    if semantic_key:
        digest = hashlib.sha256(semantic_key.encode("utf-8")).digest()
        return allowed[int.from_bytes(digest[:4], "big") % len(allowed)]
    return allowed[0]


def _motion_function(scene: AIVisualScenePlan) -> MotionFunction:
    function = _scene_function_key(scene.visual_function)
    if "COMPARE" in function or "CONTRAST" in function:
        return "COMPARE"
    if any(
        token in function for token in ("PROCESS", "PROGRESS", "TRANSFORM", "SEQUENCE")
    ):
        return "PROGRESS"
    if any(token in function for token in ("ACTION", "FOLLOW", "EXAMPLE")):
        return "FOLLOW"
    if any(token in function for token in ("DATA", "DETAIL", "FOCUS", "EVIDENCE")):
        return "FOCUS"
    if any(token in function for token in ("HOOK", "ESTABLISH", "CONTEXT", "HERO")):
        return "ESTABLISH"
    if any(token in function for token in ("CONCLUSION", "PAYOFF", "EMPHASIS")):
        return "EMPHASIZE"
    if scene.motion_need == "STATIC_SUFFICIENT":
        return "HOLD"
    return "REVEAL"


def _subject_anchor(scene: AIVisualScenePlan) -> tuple[SubjectAnchor, NormalizedPoint]:
    direction = f"{scene.composition_direction} {scene.camera_direction}".casefold()
    if any(
        token in direction
        for token in ("left third", "left-third", "subject left", "from left")
    ):
        return "LEFT", NormalizedPoint(x=0.33, y=0.5)
    if any(
        token in direction
        for token in ("right third", "right-third", "subject right", "from right")
    ):
        return "RIGHT", NormalizedPoint(x=0.67, y=0.5)
    if "top" in direction:
        return "TOP", NormalizedPoint(x=0.5, y=0.35)
    if "bottom" in direction:
        return "BOTTOM", NormalizedPoint(x=0.5, y=0.65)
    return "CENTER", NormalizedPoint(x=0.5, y=0.5)


def _image_motion_choice(
    scene: AIVisualScenePlan,
) -> tuple[CameraMotion, str, float, float, MotionFunction]:
    function = _motion_function(scene)
    anchor, _ = _subject_anchor(scene)
    duration_ms = scene.presentation_end_ms - scene.presentation_start_ms
    if duration_ms <= 2_500 and scene.motion_need == "STATIC_SUFFICIENT":
        candidates: list[tuple[CameraMotion, str, float, float]] = [
            ("STATIC", "hold_intentional", 1.0, 1.0)
        ]
    elif function == "COMPARE":
        candidates = [
            ("PAN_RIGHT", "pan_right_slow", 1.04, 1.04),
            ("PAN_LEFT", "pan_left_slow", 1.04, 1.04),
        ]
    elif function in {"FOCUS", "EMPHASIZE", "REVEAL"}:
        candidates = [
            ("PUSH_IN", "focus_push", 1.0, 1.055),
            ("PUSH_IN", "pushin_slow", 1.0, 1.045),
            ("PULL_OUT", "pullout_slow", 1.055, 1.0),
        ]
    elif function in {"FOLLOW", "PROGRESS"}:
        if anchor == "LEFT":
            candidates = [
                ("PAN_RIGHT", "pan_right_slow", 1.04, 1.04),
                ("PUSH_IN", "kenburns_subject_left", 1.0, 1.05),
            ]
        elif anchor == "RIGHT":
            candidates = [
                ("PAN_LEFT", "pan_left_slow", 1.04, 1.04),
                ("PUSH_IN", "kenburns_subject_right", 1.0, 1.05),
            ]
        else:
            candidates = [
                ("PAN_RIGHT", "pan_right_slow", 1.04, 1.04),
                ("DRIFT_UP", "drift_up_soft", 1.035, 1.045),
                ("PUSH_IN", "pushin_medium", 1.0, 1.065),
            ]
    elif function == "ESTABLISH":
        candidates = [
            ("PULL_OUT", "pullout_slow", 1.055, 1.0),
            ("DRIFT_DOWN", "drift_down_soft", 1.04, 1.04),
            ("PUSH_IN", "kenburns_center_soft", 1.0, 1.045),
        ]
    else:
        candidates = [
            ("PUSH_IN", "kenburns_center_soft", 1.0, 1.04),
            ("DRIFT_UP", "drift_up_soft", 1.035, 1.04),
            ("PULL_OUT", "pullout_slow", 1.05, 1.0),
        ]
    # A scene ordinal or anti-repeat heuristic cannot authorize a visual
    # change.  Deterministic variation is derived only from authored scene
    # bindings; repeated presets remain valid when those bindings repeat.
    semantic_key = _authored_scene_semantic_key(scene)
    selected = candidates[int(semantic_key[:8], 16) % len(candidates)]
    return (*selected, function)


class UnifiedAIVisualPlanner:
    """Deterministic AI-only planning from frozen timed semantic units."""

    version = AI_VISUAL_PLANNER_VERSION

    def compile(
        self,
        *,
        style_bible: VideoVisualStyleBible,
        narration_units: Sequence[AIVisualNarrationUnit],
        capability: AIVisualCapabilityProjection,
        policy: AIVisualPlanningPolicy,
        canonical_duration_ms: int,
    ) -> AIVisualPlanCompilation:
        units = self._validate_inputs(narration_units, canonical_duration_ms)
        beats = self._build_beats(units, capability=capability, policy=policy)
        scenes = self._materialize_scenes(
            beats,
            style_bible=style_bible,
            policy=policy,
            capability=capability,
            canonical_duration_ms=canonical_duration_ms,
        )
        owners = [
            scene
            for scene in scenes
            if scene.reuses_primary_asset_from_scene_id is None
        ]
        body: dict[str, Any] = {
            "schema_version": "vcos.ai-visual-plan-compilation.v1",
            "style_bible_hash": style_bible.content_hash,
            "planning_policy_hash": policy.content_hash,
            "canonical_duration_ms": canonical_duration_ms,
            "maximum_ai_image_presentation_ms": policy.maximum_ai_image_presentation_ms,
            "maximum_ai_video_presentation_ms": policy.maximum_ai_video_presentation_ms,
            "maximum_ai_image_asset_exposure_ms": (
                policy.maximum_ai_image_asset_exposure_ms
            ),
            "scenes": scenes,
            "ai_image_scene_count": sum(
                scene.production_route == "AI_IMAGE" for scene in scenes
            ),
            "ai_video_scene_count": sum(
                scene.production_route == "AI_VIDEO" for scene in scenes
            ),
            "unique_asset_slot_count": len(owners),
            "unique_ai_image_asset_slot_count": sum(
                scene.production_route == "AI_IMAGE" for scene in owners
            ),
            "unique_ai_video_asset_slot_count": sum(
                scene.production_route == "AI_VIDEO" for scene in owners
            ),
            "reused_presentation_window_count": len(scenes) - len(owners),
            "coverage_gate": "PASS",
        }
        return AIVisualPlanCompilation(
            **body,
            content_hash=ai_visual_stable_hash(body),
        )

    def plan(self, **kwargs: Any) -> list[AIVisualScenePlan]:
        """Compatibility-friendly projection matching the requested scene-list output."""

        return list(self.compile(**kwargs).scenes)

    @staticmethod
    def _validate_inputs(
        narration_units: Sequence[AIVisualNarrationUnit],
        canonical_duration_ms: int,
    ) -> list[AIVisualNarrationUnit]:
        if canonical_duration_ms <= 0:
            raise ValueError("AI_VISUAL_CANONICAL_DURATION_INVALID")
        units = list(narration_units)
        if not units:
            raise ValueError("AI_VISUAL_NARRATION_UNITS_REQUIRED")
        if len({unit.narration_unit_id for unit in units}) != len(units):
            raise ValueError("AI_VISUAL_NARRATION_UNIT_ID_DUPLICATE")
        if units != sorted(
            units, key=lambda item: (item.actual_start_ms, item.actual_end_ms)
        ):
            raise ValueError("AI_VISUAL_NARRATION_UNITS_NOT_ORDERED")
        if any(
            left.actual_end_ms > right.actual_start_ms
            for left, right in zip(units, units[1:])
        ):
            raise ValueError("AI_VISUAL_NARRATION_UNITS_OVERLAP")
        if units[-1].actual_end_ms > canonical_duration_ms:
            raise ValueError("AI_VISUAL_NARRATION_OUTSIDE_CANONICAL_DURATION")
        return units

    def _build_beats(
        self,
        units: Sequence[AIVisualNarrationUnit],
        *,
        capability: AIVisualCapabilityProjection,
        policy: AIVisualPlanningPolicy,
    ) -> list[_PlanningBeat]:
        if (
            not capability.ai_image_production_ready
            and not capability.ai_video_production_ready
        ):
            raise ValueError("AI_VISUAL_NO_PRODUCTION_PROVIDER_READY")
        # Reserve scarce video owners for semantic requirements before any
        # merely beneficial request is considered.  Otherwise an early
        # beneficial atom could consume the sole clip and make a later
        # transformation impossible to route truthfully.
        required_video_effect_count = self.required_ai_video_effect_count(
            units, policy=policy
        )
        if required_video_effect_count > capability.maximum_ai_video_scenes:
            raise ValueError("AI_VIDEO_MOTION_REQUIRED_AUTHORITY_UNAVAILABLE")
        remaining_video = (
            capability.maximum_ai_video_scenes - required_video_effect_count
        )
        routed: list[tuple[AIVisualNarrationUnit, AIVisualRoute]] = []
        for unit in units:
            if unit.motion_need == "MOTION_REQUIRED":
                if not (
                    capability.ai_video_production_ready
                    and capability.ai_video_budget_authorized
                ):
                    raise ValueError("AI_VIDEO_MOTION_REQUIRED_AUTHORITY_UNAVAILABLE")
                route: AIVisualRoute = "AI_VIDEO"
            else:
                route = self._select_route(
                    unit,
                    capability=capability,
                    policy=policy,
                    remaining_video=remaining_video,
                )
            if route == "AI_VIDEO" and unit.motion_need != "MOTION_REQUIRED":
                requested_effects = math.ceil(
                    (unit.actual_end_ms - unit.actual_start_ms)
                    / _maximum_scene_duration_ms(unit, policy, "AI_VIDEO")
                )
                if requested_effects > remaining_video:
                    route = "AI_IMAGE"
                else:
                    remaining_video -= requested_effects
            routed.append((unit, route))

        grouped: list[_PlanningBeat] = []
        for unit, route in routed:
            if grouped and self._can_group(grouped[-1], unit, route, policy):
                previous = grouped[-1]
                grouped[-1] = _PlanningBeat(
                    units=(*previous.units, unit),
                    start_ms=previous.start_ms,
                    end_ms=unit.actual_end_ms,
                    route=route,
                )
            else:
                grouped.append(
                    _PlanningBeat(
                        units=(unit,),
                        start_ms=unit.actual_start_ms,
                        end_ms=unit.actual_end_ms,
                        route=route,
                    )
                )

        split: list[_PlanningBeat] = []
        for beat in grouped:
            maximum = min(
                _maximum_scene_duration_ms(unit, policy, beat.route)
                for unit in beat.units
            )
            duration = beat.end_ms - beat.start_ms
            if duration <= maximum:
                split.append(beat)
                continue
            count = math.ceil(duration / maximum)
            base, remainder = divmod(duration, count)
            cursor = beat.start_ms
            for index in range(count):
                part_duration = base + (1 if index < remainder else 0)
                endpoint = cursor + part_duration
                part_units = tuple(
                    unit
                    for unit in beat.units
                    if unit.actual_start_ms < endpoint and unit.actual_end_ms > cursor
                )
                if not part_units:
                    # A bounded scene can land wholly inside a silence gap.
                    # Keep it bound to the nearest preceding semantic unit (or
                    # the first following unit at the start) without changing
                    # the exact, ordered source partition.
                    preceding = [
                        unit for unit in beat.units if unit.actual_end_ms <= cursor
                    ]
                    part_units = (preceding[-1] if preceding else beat.units[0],)
                split.append(
                    _PlanningBeat(
                        units=part_units,
                        start_ms=cursor,
                        end_ms=endpoint,
                        route=beat.route,
                        split_index=index + 1,
                        split_count=count,
                    )
                )
                cursor = endpoint
        return split

    @staticmethod
    def required_ai_video_effect_count(
        units: Sequence[AIVisualNarrationUnit],
        *,
        policy: AIVisualPlanningPolicy,
    ) -> int:
        """Project bounded provider effects needed by mandatory motion only."""

        runs: list[list[AIVisualNarrationUnit]] = []
        for unit in units:
            if unit.motion_need != "MOTION_REQUIRED":
                continue
            if (
                runs
                and runs[-1][-1].semantic_group_key
                and runs[-1][-1].semantic_group_key == unit.semantic_group_key
                and runs[-1][-1].visual_function == unit.visual_function
            ):
                runs[-1].append(unit)
            else:
                runs.append([unit])
        return sum(
            math.ceil(
                (run[-1].actual_end_ms - run[0].actual_start_ms)
                / min(
                    _maximum_scene_duration_ms(unit, policy, "AI_VIDEO") for unit in run
                )
            )
            for run in runs
        )

    @staticmethod
    def _select_route(
        unit: AIVisualNarrationUnit,
        *,
        capability: AIVisualCapabilityProjection,
        policy: AIVisualPlanningPolicy,
        remaining_video: int,
    ) -> AIVisualRoute:
        video_available = (
            capability.ai_video_production_ready
            and capability.ai_video_budget_authorized
            and remaining_video > 0
        )
        if unit.motion_need == "MOTION_REQUIRED":
            if not video_available:
                raise ValueError("AI_VIDEO_MOTION_REQUIRED_AUTHORITY_UNAVAILABLE")
            return "AI_VIDEO"
        function = _scene_function_key(unit.visual_function)
        semantic_video = unit.motion_need == "MOTION_BENEFICIAL" and (
            function in _TEMPORAL_VISUAL_FUNCTIONS
            or unit.importance in {"HIGH", "HERO"}
        )
        if semantic_video and video_available:
            return "AI_VIDEO"
        if capability.ai_image_production_ready:
            return "AI_IMAGE"
        if video_available and policy.allow_ai_video_for_static_when_image_unavailable:
            return "AI_VIDEO"
        raise ValueError("AI_IMAGE_PRODUCTION_AUTHORITY_UNAVAILABLE")

    @staticmethod
    def _can_group(
        beat: _PlanningBeat,
        unit: AIVisualNarrationUnit,
        route: AIVisualRoute,
        policy: AIVisualPlanningPolicy,
    ) -> bool:
        previous = beat.units[-1]
        if not policy.group_adjacent_semantic_units or beat.route != route:
            return False
        if (
            not previous.semantic_group_key
            or previous.semantic_group_key != unit.semantic_group_key
        ):
            return False
        if previous.visual_function != unit.visual_function:
            return False
        # Group the complete contiguous semantic run before applying duration
        # bounds. ``_build_beats`` owns the following deterministic split phase;
        # rejecting a long run here needlessly splits each narration unit in
        # isolation, inflates scene count, and prevents one coherent owner asset
        # from serving its bounded complementary presentation windows.
        return True

    @staticmethod
    def _materialize_scenes(
        beats: Sequence[_PlanningBeat],
        *,
        style_bible: VideoVisualStyleBible,
        policy: AIVisualPlanningPolicy,
        capability: AIVisualCapabilityProjection,
        canonical_duration_ms: int,
    ) -> list[AIVisualScenePlan]:
        scenes: list[AIVisualScenePlan] = []
        units_by_group: dict[str, list[AIVisualNarrationUnit]] = {}
        for beat in beats:
            for unit in beat.units:
                group_key = unit.semantic_group_key or unit.narration_unit_id
                bucket = units_by_group.setdefault(group_key, [])
                if all(
                    existing.narration_unit_id != unit.narration_unit_id
                    for existing in bucket
                ):
                    bucket.append(unit)
        compatible_owner_by_key: dict[
            tuple[AIVisualRoute, str], tuple[str, str, int]
        ] = {}
        image_slot_count = 0
        video_slot_count = 0
        for index, beat in enumerate(beats):
            ordinal = index + 1
            first = beat.units[0]
            group_key = first.semantic_group_key or first.narration_unit_id
            group_units = units_by_group[group_key]
            # Allocate any silence between adjacent narration beats evenly.
            # The actual semantic windows stay byte-for-byte aligned to source
            # timing, while presentation remains contiguous and one side does
            # not inherit the entire gap (which could push a bounded still over
            # its maximum duration).
            presentation_start = (
                0 if index == 0 else (beats[index - 1].end_ms + beat.start_ms) // 2
            )
            presentation_end = (
                (beat.end_ms + beats[index + 1].start_ms) // 2
                if index + 1 < len(beats)
                else canonical_duration_ms
            )
            narration_ids = _dedupe(unit.narration_unit_id for unit in beat.units)
            information_ids = _dedupe(
                info_id for unit in beat.units for info_id in unit.information_unit_ids
            )
            semantic_suffix = (
                f" Complementary beat {beat.split_index} of {beat.split_count}."
                if beat.split_count > 1
                else ""
            )
            local_scene_meaning = (
                " ".join(_dedupe(unit.scene_meaning for unit in beat.units))
                + semantic_suffix
            )
            visual_goal = " ".join(_dedupe(unit.visual_goal for unit in beat.units))
            composition = first.composition_direction
            camera = first.camera_direction
            continuity = _dedupe(
                constraint
                for unit in beat.units
                for constraint in unit.continuity_constraints
            )
            if beat.split_count > 1:
                composition += (
                    f"; use complementary framing for beat {beat.split_index}/{beat.split_count}, "
                    "without repeating a centered object layout"
                )
                camera += "; preserve subject continuity while changing focal emphasis"
                continuity.append(
                    "retain the same world and subject identity across complementary beats"
                )
            scene_identity = ai_visual_stable_hash(
                {
                    "package_id": style_bible.package_id,
                    "style_bible_hash": style_bible.content_hash,
                    "ordinal": ordinal,
                    "narration_unit_ids": narration_ids,
                    "actual_start_ms": beat.start_ms,
                    "actual_end_ms": beat.end_ms,
                }
            )
            scene_id = f"ai-scene-{ordinal:04d}-{scene_identity[:12]}"
            semantic_group_key = next(
                (
                    unit.semantic_group_key
                    for unit in beat.units
                    if unit.semantic_group_key is not None
                ),
                None,
            )
            compatibility_key = (
                beat.route,
                semantic_group_key
                or ai_visual_stable_hash(
                    {
                        "core_subject": first.core_subject.casefold().strip(),
                        "visual_function": _scene_function_key(first.visual_function),
                        "environment": first.environment.casefold().strip(),
                        "action_or_relation": first.action_or_relation.casefold().strip(),
                    }
                ),
            )
            presentation_duration_ms = presentation_end - presentation_start
            prior_owner = compatible_owner_by_key.get(compatibility_key)
            # An 8-second Veo generation is never stretched, looped, or reused
            # to impersonate a longer temporal event.  Every bounded video
            # presentation window therefore owns one distinct provider effect.
            if beat.route == "AI_VIDEO":
                prior_owner = None
            elif prior_owner is not None and (
                prior_owner[2] + presentation_duration_ms
                > policy.maximum_ai_image_asset_exposure_ms
            ):
                prior_owner = None
            if prior_owner is None:
                asset_slot_identity = ai_visual_stable_hash(
                    {
                        "package_id": style_bible.package_id,
                        "route": beat.route,
                        "semantic_compatibility_key": compatibility_key[1],
                        "owner_scene_id": scene_id,
                    }
                )
                primary_asset_slot_id = f"ai-asset-slot-{asset_slot_identity[:16]}"
                reuses_from = None
                reuse_reason = None
                compatible_owner_by_key[compatibility_key] = (
                    scene_id,
                    primary_asset_slot_id,
                    presentation_duration_ms,
                )
                if beat.route == "AI_IMAGE":
                    image_slot_count += 1
                    if image_slot_count > capability.maximum_ai_image_assets:
                        raise ValueError("AI_IMAGE_UNIQUE_ASSET_SLOT_BUDGET_EXCEEDED")
                else:
                    video_slot_count += 1
                    if video_slot_count > capability.maximum_ai_video_scenes:
                        raise ValueError("AI_VIDEO_UNIQUE_ASSET_SLOT_BUDGET_EXCEEDED")
                # The one provider prompt/receipt owned by this scene must bind
                # the full meaning served by every later reuse window.
                served_units = group_units if beat.route == "AI_IMAGE" else beat.units
                scene_meaning = " ".join(
                    _dedupe(unit.scene_meaning for unit in served_units)
                )
            else:
                reuses_from, primary_asset_slot_id, prior_exposure_ms = prior_owner
                compatible_owner_by_key[compatibility_key] = (
                    reuses_from,
                    primary_asset_slot_id,
                    prior_exposure_ms + presentation_duration_ms,
                )
                scene_meaning = local_scene_meaning
                reuse_reason = (
                    "Reuse the earlier server-owned AI asset across a bounded complementary "
                    "presentation window with the same route, core subject, visual function, "
                    "environment, and semantic asset group."
                )
            negative_constraints = _dedupe(
                [
                    *GLOBAL_AI_VISUAL_NEGATIVE_CONSTRAINTS,
                    *style_bible.negative_aesthetic_constraints,
                ]
            )
            action_units = group_units if beat.route == "AI_IMAGE" else beat.units
            aggregate_action = " ".join(
                _dedupe(unit.spoken_text for unit in action_units)
            )
            prompt_brief = (
                f"Explain {scene_meaning.strip()} through {visual_goal.strip()}. "
                f"Primary subject: {first.core_subject}. Relation/action: {aggregate_action}. "
                f"Environment: {first.environment}. Use a conceptual, semantic visual rather than a slide or fake UI."
            )
            body: dict[str, Any] = {
                "schema_version": "vcos.ai-visual-scene-plan.v1",
                "scene_id": scene_id,
                "ordinal": ordinal,
                "narration_unit_ids": narration_ids,
                "information_unit_ids": information_ids,
                "actual_start_ms": beat.start_ms,
                "actual_end_ms": beat.end_ms,
                "presentation_start_ms": presentation_start,
                "presentation_end_ms": presentation_end,
                "scene_meaning": scene_meaning.strip(),
                "visual_function": first.visual_function,
                "core_subject": first.core_subject,
                "secondary_subjects": _dedupe(
                    subject
                    for unit in beat.units
                    for subject in unit.secondary_subjects
                ),
                "action_or_relation": aggregate_action,
                "environment": first.environment,
                "visual_goal": visual_goal,
                "visual_style_direction": (
                    f"{style_bible.overall_visual_language}; {style_bible.rendering_style}; "
                    f"lighting {style_bible.lighting}; depth {style_bible.depth}"
                ),
                "composition_direction": composition,
                "camera_direction": camera,
                "continuity_constraints": continuity,
                "motion_need": max(
                    (unit.motion_need for unit in beat.units),
                    key=(
                        "STATIC_SUFFICIENT",
                        "MOTION_BENEFICIAL",
                        "MOTION_REQUIRED",
                    ).index,
                ),
                "production_route": beat.route,
                "primary_asset_slot_id": primary_asset_slot_id,
                "reuses_primary_asset_from_scene_id": reuses_from,
                "asset_reuse_semantic_reason": reuse_reason,
                "prompt_brief": prompt_brief,
                "negative_constraints": negative_constraints,
                "factual_risk": max(
                    (unit.factual_risk for unit in beat.units),
                    key=("LOW", "MEDIUM", "HIGH").index,
                ),
                "importance": max(
                    (unit.importance for unit in beat.units),
                    key=("SUPPORTING", "STANDARD", "HIGH", "HERO").index,
                ),
                "transition_semantic_reason": first.transition_semantic_reason,
                "style_bible_hash": style_bible.content_hash,
                "planning_policy_hash": policy.content_hash,
            }
            scenes.append(AIVisualScenePlan(**seal_content_payload(body)))
        return scenes


class MotionIntentPlanner:
    """Select meaning-bound presentation intent; never emit FFmpeg syntax."""

    version = MOTION_INTENT_PLANNER_VERSION

    def project(
        self,
        *,
        scene_plan: AIVisualScenePlan,
        style_bible: VideoVisualStyleBible,
        motion_grammar: VideoMotionGrammar,
        primary_asset_ref: str,
        primary_asset_hash: str,
        previous_projection: MotionIntentProjection | None = None,
        next_scene_plan: AIVisualScenePlan | None = None,
    ) -> MotionIntentProjection:
        self._validate_authority(scene_plan, style_bible, motion_grammar)
        anchor, focal_point = _subject_anchor(scene_plan)
        transition_in = _transition_for_reason(
            scene_plan.transition_semantic_reason,
            motion_grammar,
            semantic_key=_authored_scene_semantic_key(scene_plan),
        )
        if previous_projection is None:
            transition_in = "cut"
        transition_out = (
            _transition_for_reason(
                next_scene_plan.transition_semantic_reason,
                motion_grammar,
                semantic_key=_authored_scene_semantic_key(next_scene_plan),
            )
            if next_scene_plan is not None
            else _transition_for_reason(
                scene_plan.transition_semantic_reason,
                motion_grammar,
                semantic_key=_authored_scene_semantic_key(scene_plan),
            )
        )
        if scene_plan.production_route == "AI_VIDEO":
            camera_motion: CameraMotion = "STATIC"
            motion_preset = "video_intrinsic_preserve"
            start_scale = end_scale = 1.0
            function = _motion_function(scene_plan)
            semantic_reason = f"Preserve provider-generated temporal action for {function.lower()} while normalizing only presentation bounds."
        else:
            camera_motion, motion_preset, start_scale, end_scale, function = (
                _image_motion_choice(
                    scene_plan,
                )
            )
            semantic_reason = f"Use {motion_preset} to {function.lower()} the narrated meaning without generating primary visual content."
        validate_viewer_facing_presentation(
            [
                {
                    "outcome": "HOLD" if function == "HOLD" else "CHANGE",
                    "editorial_reason": semantic_reason,
                    "editorial_authority_ref": scene_plan.content_hash,
                }
            ]
        )
        safe_crop = NormalizedRegion(x=0.04, y=0.04, width=0.92, height=0.92)
        body: dict[str, Any] = {
            "schema_version": "vcos.motion-intent-projection.v1",
            "scene_id": scene_plan.scene_id,
            "scene_plan_hash": scene_plan.content_hash,
            "style_bible_hash": style_bible.content_hash,
            "motion_grammar_hash": motion_grammar.content_hash,
            "primary_asset_ref": primary_asset_ref,
            "primary_asset_hash": primary_asset_hash,
            "asset_type": scene_plan.production_route,
            "motion_function": function,
            "camera_motion": camera_motion,
            "motion_preset": motion_preset,
            "subject_anchor": anchor,
            "custom_subject_anchor": None,
            "focal_point": focal_point,
            "safe_crop_region": safe_crop,
            "intensity": motion_grammar.default_motion_intensity,
            "start_scale": start_scale,
            "end_scale": end_scale,
            "presentation_start_ms": scene_plan.presentation_start_ms,
            "presentation_end_ms": scene_plan.presentation_end_ms,
            "transition_in": transition_in,
            "transition_out": transition_out,
            "transition_semantic_reason": scene_plan.transition_semantic_reason,
            "motion_semantic_reason": semantic_reason,
            "safe_area_constraints": [
                "keep the primary subject inside the normalized safe crop region",
                "expose no frame edge throughout the presentation window",
                "reserve generated typography for optional secondary native overlays only",
            ],
        }
        return MotionIntentProjection(**seal_content_payload(body))

    @staticmethod
    def _validate_authority(
        scene_plan: AIVisualScenePlan,
        style_bible: VideoVisualStyleBible,
        motion_grammar: VideoMotionGrammar,
    ) -> None:
        if scene_plan.style_bible_hash != style_bible.content_hash:
            raise ValueError("MOTION_STYLE_BIBLE_BINDING_MISMATCH")
        if motion_grammar.style_bible_hash != style_bible.content_hash:
            raise ValueError("MOTION_GRAMMAR_STYLE_BIBLE_BINDING_MISMATCH")
        if (
            scene_plan.production_route == "AI_IMAGE"
            and scene_plan.presentation_end_ms - scene_plan.presentation_start_ms
            > motion_grammar.maximum_static_presentation_ms
        ):
            raise ValueError("STATIC_DURATION_EXCESSIVE")


class AIImagePromptCompiler:
    """Hash-bound, provider-neutral still prompt compiler."""

    compiler_version = AI_IMAGE_PROMPT_COMPILER_VERSION

    def compile(
        self,
        *,
        scene_plan: AIVisualScenePlan,
        style_bible: VideoVisualStyleBible,
        motion_grammar: VideoMotionGrammar,
        motion_projection: MotionIntentProjection | None = None,
    ) -> CompiledAIImagePrompt:
        if scene_plan.production_route != "AI_IMAGE":
            raise ValueError("AI_IMAGE_PROMPT_ROUTE_INVALID")
        if scene_plan.reuses_primary_asset_from_scene_id is not None:
            raise ValueError("AI_IMAGE_REUSED_WINDOW_HAS_NO_GENERATION_PROMPT")
        MotionIntentPlanner._validate_authority(scene_plan, style_bible, motion_grammar)
        if motion_projection is not None:
            if (
                motion_projection.scene_plan_hash != scene_plan.content_hash
                or motion_projection.asset_type != "AI_IMAGE"
            ):
                raise ValueError("AI_IMAGE_PROMPT_MOTION_BINDING_MISMATCH")
            motion_preset = motion_projection.motion_preset
            anchor = motion_projection.subject_anchor
            camera_motion = motion_projection.camera_motion
        else:
            camera_motion, motion_preset, _, _, _ = _image_motion_choice(scene_plan)
            anchor, _ = _subject_anchor(scene_plan)
        motion_safe = self._motion_safe_composition(anchor, camera_motion)
        anatomy = [
            ("Scene meaning", scene_plan.scene_meaning),
            ("Visual goal", scene_plan.visual_goal),
            (
                "Subject hierarchy",
                "; ".join(
                    [
                        f"primary {scene_plan.core_subject}",
                        *(
                            f"secondary {subject}"
                            for subject in scene_plan.secondary_subjects
                        ),
                    ]
                ),
            ),
            ("Action or relation", scene_plan.action_or_relation),
            ("Environment", scene_plan.environment),
            ("Video visual language", style_bible.overall_visual_language),
            ("Rendering style", style_bible.rendering_style),
            (
                "Lighting and contrast",
                f"{style_bible.lighting}; {style_bible.contrast}",
            ),
            (
                "Palette and materials",
                f"{', '.join(style_bible.palette_guidance)}; {', '.join(style_bible.materials)}",
            ),
            ("Composition", scene_plan.composition_direction),
            ("Camera", f"{scene_plan.camera_direction}; {style_bible.camera_language}"),
            ("Depth", style_bible.depth),
            ("Motion-safe composition", motion_safe),
            (
                "Continuity",
                "; ".join(scene_plan.continuity_constraints)
                or "preserve the frozen video-level visual identity",
            ),
            ("Target", "cinematic 16:9, crop-safe, no baked-in typography"),
        ]
        prompt = (
            ". ".join(
                f"{label}: {value.strip().rstrip('.')}" for label, value in anatomy
            )
            + "."
        )
        negative_constraints = _dedupe(
            [
                *GLOBAL_AI_VISUAL_NEGATIVE_CONSTRAINTS,
                *style_bible.negative_aesthetic_constraints,
                *scene_plan.negative_constraints,
            ]
        )
        body: dict[str, Any] = {
            "schema_version": "vcos.compiled-ai-image-prompt.v1",
            "scene_id": scene_plan.scene_id,
            "scene_plan_hash": scene_plan.content_hash,
            "style_bible_hash": style_bible.content_hash,
            "prompt_compiler_version": self.compiler_version,
            "aspect_ratio": "16:9",
            "expected_motion_preset": motion_preset,
            "motion_safe_composition": motion_safe,
            "prompt": prompt,
            "negative_constraints": negative_constraints,
            "negative_prompt": ", ".join(negative_constraints),
            "prompt_hash": ai_visual_text_hash(prompt),
            "provider_call_made": False,
        }
        return CompiledAIImagePrompt(**seal_content_payload(body))

    @staticmethod
    def _motion_safe_composition(anchor: SubjectAnchor, motion: CameraMotion) -> str:
        anchor_language = {
            "LEFT": "place the primary subject on the left third with meaningful environment extending right",
            "RIGHT": "place the primary subject on the right third with meaningful environment extending left",
            "TOP": "place the focal subject above center with protected lower environmental space",
            "BOTTOM": "place the focal subject below center with protected upper environmental space",
            "CENTER": "keep the focal subject near center with generous environmental margin on every side",
            "CUSTOM_NORMALIZED_POINT": "protect the declared normalized focal point and surrounding context",
        }[anchor]
        motion_language = {
            "PAN_LEFT": "composition suitable for a slow leftward camera pan",
            "PAN_RIGHT": "composition suitable for a slow rightward camera pan",
            "PUSH_IN": "composition suitable for a restrained push-in without subject clipping",
            "PULL_OUT": "include complete surrounding context for a restrained pull-out",
            "DRIFT_UP": "protect vertical context for a gentle upward drift",
            "DRIFT_DOWN": "protect vertical context for a gentle downward drift",
            "STATIC": "composition suitable for a short intentional hold",
            "CONTROLLED_CUSTOM": "protect the explicitly bounded camera path",
        }[motion]
        return f"{anchor_language}; {motion_language}; keep all critical content inside a 92% crop-safe region"


class AIVideoPromptCompiler:
    """Compile provider-neutral moving-media prompts for safe Veo adaptation."""

    compiler_version = AI_VIDEO_PROMPT_COMPILER_VERSION

    def compile(
        self,
        *,
        scene_plan: AIVisualScenePlan,
        style_bible: VideoVisualStyleBible,
    ) -> CompiledAIVideoPrompt:
        if scene_plan.production_route != "AI_VIDEO":
            raise ValueError("AI_VIDEO_PROMPT_ROUTE_INVALID")
        if scene_plan.reuses_primary_asset_from_scene_id is not None:
            raise ValueError("AI_VIDEO_REUSED_WINDOW_HAS_NO_GENERATION_PROMPT")
        if scene_plan.style_bible_hash != style_bible.content_hash:
            raise ValueError("AI_VIDEO_PROMPT_STYLE_BIBLE_BINDING_MISMATCH")
        target_duration_ms = scene_plan.actual_end_ms - scene_plan.actual_start_ms
        continuity = "; ".join(scene_plan.continuity_constraints) or (
            "preserve the frozen palette, material language, and restrained camera grammar"
        )
        anatomy = [
            (
                "Subject and action",
                f"{scene_plan.core_subject}; {scene_plan.action_or_relation}",
            ),
            ("Semantic outcome", scene_plan.scene_meaning),
            ("Environment", scene_plan.environment),
            ("Visual goal", scene_plan.visual_goal),
            (
                "Realization",
                f"{style_bible.overall_visual_language}; {style_bible.rendering_style}",
            ),
            (
                "Lighting",
                f"{style_bible.lighting}; {style_bible.contrast}; palette {', '.join(style_bible.palette_guidance)}",
            ),
            (
                "Camera and framing",
                f"{scene_plan.camera_direction}; {style_bible.camera_language}; {scene_plan.composition_direction}",
            ),
            (
                "Intrinsic motion",
                "one coherent temporally meaningful action with a readable beginning, progression, and settle",
            ),
            ("Continuity", continuity),
            (
                "Target",
                "single continuous 16:9 generated video shot; no speed ramp; no loop; no baked-in typography",
            ),
        ]
        prompt = (
            ". ".join(
                f"{label}: {value.strip().rstrip('.')}" for label, value in anatomy
            )
            + "."
        )
        negative_constraints = _dedupe(
            [
                *GLOBAL_AI_VISUAL_NEGATIVE_CONSTRAINTS,
                *style_bible.negative_aesthetic_constraints,
                *scene_plan.negative_constraints,
                "no slideshow",
                "no Ken Burns effect",
                "no speed ramp",
                "no repeated loop",
                "no unintended audio authority",
                "no people",
                "no face",
                "no human figure",
            ]
        )
        body: dict[str, Any] = {
            "schema_version": "vcos.compiled-ai-video-prompt.v1",
            "scene_id": scene_plan.scene_id,
            "scene_plan_hash": scene_plan.content_hash,
            "style_bible_hash": style_bible.content_hash,
            "prompt_compiler_version": self.compiler_version,
            "aspect_ratio": "16:9",
            "target_duration_ms": target_duration_ms,
            "provider_generation_duration_ms": 8_000,
            "intrinsic_motion_required": True,
            "provider_audio_usage_policy": "DISCARD",
            "prompt": prompt,
            "negative_constraints": negative_constraints,
            "negative_prompt": ", ".join(negative_constraints),
            "prompt_hash": ai_visual_text_hash(prompt),
            "provider_call_made": False,
        }
        return CompiledAIVideoPrompt(**seal_content_payload(body))


# Repository-consistent aliases for integration code that uses generic names.
UnifiedVisualPlanner = UnifiedAIVisualPlanner
ImagePromptCompiler = AIImagePromptCompiler
VideoPromptCompiler = AIVideoPromptCompiler
