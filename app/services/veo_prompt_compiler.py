from __future__ import annotations

import hashlib
from typing import Any, Iterable

from app.contracts.visual_direction import (
    CompiledVeoPrompt,
    FixedDurationFitDecision,
    SceneVisualIntent,
    VeoDurationFitThresholds,
    VisualDirectionContract,
)
from app.services.native_render_plan import stable_hash


VEO_PROMPT_COMPILER_VERSION = "veo-prompt-compiler/v1.1.0"

_BASE_NEGATIVE_CONSTRAINTS = (
    "text",
    "letters",
    "logo",
    "watermark",
    "fake software interface",
    "testimonial",
)
_NO_CHARACTER_CONSTRAINTS = (
    "people",
    "person",
    "face",
    "human figure",
    "presenter",
    "speaker",
    "human likeness",
)
_ANALOG_FILM_STRIP_NEGATIVE_CONSTRAINTS = (
    "machine",
    "robotics",
    "screen",
    "display",
    "panel",
    "button",
    "interface",
    "fake UI",
    "diagram",
    "text",
    "letter",
    "number",
    "label",
    "logo",
    "person",
)


class VeoPromptCompiler:
    """Compile prompt text only; this class has no SDK, transport, or provider state."""

    def compile(
        self,
        scene_intent: SceneVisualIntent | str | None = None,
        visual_direction: VisualDirectionContract | None = None,
        *,
        scene_semantic_intent: str | None = None,
        scene_id: str | None = None,
        target_duration_seconds: float | None = None,
        previous_scene_summary: str | None = None,
        next_scene_summary: str | None = None,
        character_policy_mode: str = "NO_CHARACTER",
        channel_provider_policy: dict[str, Any] | None = None,
        visual_direction_ref: str | None = None,
    ) -> CompiledVeoPrompt:
        if visual_direction is None:
            raise ValueError("VEO_VISUAL_DIRECTION_REQUIRED")
        intent = self._scene_intent(
            scene_intent,
            scene_semantic_intent=scene_semantic_intent,
            scene_id=scene_id,
            target_duration_seconds=target_duration_seconds,
            previous_scene_summary=previous_scene_summary,
            next_scene_summary=next_scene_summary,
        )
        provider_policy = dict(channel_provider_policy or {})
        mode = str(provider_policy.get("character_policy_mode") or character_policy_mode).upper()
        subject_action = intent.subject_action or intent.semantic_intent
        environment = f"{visual_direction.environment_type}; {visual_direction.industry_context}"
        realism = f"{visual_direction.realism_level}; {visual_direction.treatment_mode}; tone {visual_direction.tone_mode}"
        lighting = (
            f"{visual_direction.lighting_direction}; {visual_direction.lighting_temperature}; "
            f"{visual_direction.time_of_day}; palette {', '.join(visual_direction.palette)}; "
            f"{visual_direction.contrast} contrast; {visual_direction.saturation} saturation"
        )
        camera_angle_shot_size = f"{intent.camera_angle}; {intent.shot_size or visual_direction.camera_distance}"
        framing = (
            f"{visual_direction.framing_rule}; {visual_direction.lens_feel}; "
            f"{visual_direction.depth_of_field_style}; {visual_direction.texture_grain} texture"
        )
        continuity = self._continuity_hint(intent, visual_direction)
        constraints = [*_BASE_NEGATIVE_CONSTRAINTS, *visual_direction.prohibited_cliches]
        if mode == "NO_CHARACTER":
            constraints.extend(_NO_CHARACTER_CONSTRAINTS)
        if _is_analog_film_strip_table_scene(intent):
            # Keep this material metaphor from drifting into a literal editing
            # machine, control surface, fake interface, or annotated diagram.
            constraints.extend(_ANALOG_FILM_STRIP_NEGATIVE_CONSTRAINTS)
        constraints.extend(provider_policy.get("negative_constraints") or [])
        constraints.extend(provider_policy.get("forbidden_prompt_terms") or [])
        negative_constraints = _dedupe(constraints)

        anatomy = [
            ("Subject/action", subject_action),
            ("Environment/industry context", environment),
            ("Realism/treatment", realism),
            ("Lighting/time of day", lighting),
            ("Camera angle and shot size", camera_angle_shot_size),
            ("Camera movement", visual_direction.camera_movement),
            ("Framing/focal style", framing),
            ("Motion intensity", visual_direction.motion_intensity),
            ("Continuity hint", continuity),
            ("Negative constraints", "avoid " + ", ".join(negative_constraints)),
        ]
        prompt = ". ".join(f"{label}: {value.strip().rstrip('.')}" for label, value in anatomy) + "."
        negative_prompt = ", ".join(negative_constraints)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        direction_ref = visual_direction_ref or (
            f"artifact://visual-direction/{visual_direction.channel_id}/{visual_direction.project_id}/"
            f"{visual_direction.contract_version}"
        )
        payload = {
            "compiler_version": VEO_PROMPT_COMPILER_VERSION,
            "scene_id": intent.scene_id,
            "visual_direction_ref": direction_ref,
            "visual_direction_hash": visual_direction.content_hash,
            "target_duration_seconds": intent.target_duration_seconds,
            "subject_action": subject_action,
            "environment_industry_context": environment,
            "realism_treatment": realism,
            "lighting_time_of_day": lighting,
            "camera_angle_shot_size": camera_angle_shot_size,
            "camera_movement": visual_direction.camera_movement,
            "framing_focal_style": framing,
            "motion_intensity": visual_direction.motion_intensity,
            "continuity_hint": continuity,
            "negative_constraints": negative_constraints,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "prompt_hash": prompt_hash,
            "provider_call_made": False,
        }
        return CompiledVeoPrompt(**payload, content_hash=stable_hash(payload))

    build = compile

    @staticmethod
    def _scene_intent(
        scene_intent: SceneVisualIntent | str | None,
        *,
        scene_semantic_intent: str | None,
        scene_id: str | None,
        target_duration_seconds: float | None,
        previous_scene_summary: str | None,
        next_scene_summary: str | None,
    ) -> SceneVisualIntent:
        if isinstance(scene_intent, SceneVisualIntent):
            updates: dict[str, Any] = {}
            if previous_scene_summary is not None:
                updates["previous_scene_summary"] = previous_scene_summary
            if next_scene_summary is not None:
                updates["next_scene_summary"] = next_scene_summary
            if target_duration_seconds is not None:
                updates["target_duration_seconds"] = target_duration_seconds
            return scene_intent.model_copy(update=updates)
        semantic = scene_semantic_intent or (str(scene_intent) if scene_intent is not None else "")
        if not semantic.strip():
            raise ValueError("VEO_SCENE_SEMANTIC_INTENT_REQUIRED")
        if not scene_id:
            raise ValueError("VEO_SCENE_ID_REQUIRED")
        if target_duration_seconds is None:
            raise ValueError("VEO_NARRATION_DERIVED_TARGET_DURATION_REQUIRED")
        return SceneVisualIntent(
            scene_id=scene_id,
            semantic_intent=semantic,
            target_duration_seconds=target_duration_seconds,
            previous_scene_summary=previous_scene_summary,
            next_scene_summary=next_scene_summary,
        )

    @staticmethod
    def _continuity_hint(intent: SceneVisualIntent, direction: VisualDirectionContract) -> str:
        parts = ["preserve the contract's restrained camera language and palette"]
        if intent.previous_scene_summary:
            parts.append(f"enter naturally from previous scene: {intent.previous_scene_summary}")
        if intent.next_scene_summary:
            parts.append(f"leave a coherent cut toward next scene: {intent.next_scene_summary}")
        if direction.adjacent_scene_constraints:
            parts.append("constraints: " + "; ".join(direction.adjacent_scene_constraints))
        return "; ".join(parts)


class VeoFixedDurationPlanner:
    """Fit an eight-second provider asset to narration timing without changing narration."""

    def __init__(self, thresholds: VeoDurationFitThresholds):
        if not isinstance(thresholds, VeoDurationFitThresholds):
            raise ValueError("VEO_DURATION_FIT_POLICY_REQUIRED")
        self.thresholds = thresholds

    def decide(
        self,
        target_duration_seconds: float,
    ) -> FixedDurationFitDecision:
        target = float(target_duration_seconds)
        provider = self.thresholds.approved_output_duration_seconds
        if target <= 0 or provider <= 0:
            raise ValueError("VEO_DURATION_FIT_INPUT_INVALID")
        delta = target - provider
        if abs(delta) <= self.thresholds.exact_tolerance_seconds:
            decision = "USE_ONE_ASSET"
            trim_head = trim_tail = bridge = 0.0
            allowed = True
            reasons = ["VEO_DURATION_NATIVE_FIT"]
        elif target < provider and target >= self.thresholds.minimum_useful_trim_seconds:
            decision = "TRIM_TO_TARGET"
            trim_total = provider - target
            trim_head = round(trim_total / 2, 6)
            trim_tail = round(trim_total - trim_head, 6)
            bridge = 0.0
            allowed = True
            reasons = ["VEO_TRIM_RETAINS_CENTER_ACTION_PEAK", "VEO_SPEED_CHANGE_FORBIDDEN"]
        elif target > provider and delta <= self.thresholds.small_bridge_max_seconds:
            decision = "USE_NATIVE_OR_SUPPORTING_BRIDGE"
            trim_head = trim_tail = 0.0
            bridge = round(delta, 6)
            allowed = True
            reasons = ["VEO_SMALL_MISMATCH_REQUIRES_BRIDGE", "VEO_LOOP_FORBIDDEN"]
        else:
            decision = "REPLAN_BEFORE_PROVIDER_EXECUTION"
            trim_head = trim_tail = bridge = 0.0
            allowed = False
            reasons = ["VEO_DURATION_MISMATCH_MATERIAL", "VISUAL_PLAN_CHANGE_REQUIRED"]
        payload = {
            "target_duration_seconds": target,
            "provider_duration_seconds": provider,
            "decision": decision,
            "trim_head_seconds": trim_head,
            "trim_tail_seconds": trim_tail,
            "bridge_duration_seconds": bridge,
            "provider_execution_allowed": allowed,
            "narration_timing_changed": self.thresholds.narration_timing_change_allowed,
            "speed_change_allowed": self.thresholds.speed_change_allowed,
            "loop_allowed": self.thresholds.loop_allowed,
            "duration_fit_thresholds": self.thresholds.model_dump(),
            "reason_codes": reasons,
        }
        return FixedDurationFitDecision(**payload, content_hash=stable_hash(payload))

    evaluate = decide
    plan = decide


VeoDurationFitPlanner = VeoFixedDurationPlanner
FixedVeoDurationPolicy = VeoFixedDurationPlanner


def _dedupe(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _is_analog_film_strip_table_scene(intent: SceneVisualIntent) -> bool:
    description = " ".join(
        value for value in (intent.semantic_intent, intent.subject_action) if value
    ).casefold()
    film_strip = any(token in description for token in ("film strip", "celluloid strip"))
    analog_material = any(token in description for token in ("analog", "analogue", "celluloid"))
    tabletop = any(token in description for token in ("table", "tabletop", "matte surface"))
    return film_strip and analog_material and tabletop
