"""Small, typed capability catalog and deterministic prompt projection.

Full procedures stay in source control.  A provider gets a bounded list of
task-relevant directives plus identities that make the projection auditable.
This deliberately prevents catalog growth from becoming prompt growth.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field


COMPILER_VERSION = "vcos-capability-compiler.v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def estimate_tokens(value: str) -> int:
    """Stable, deliberately conservative token estimate for hard budgets."""

    return len(re.findall(r"\S+", value, flags=re.UNICODE))


class InstructionPrimitive(StrEnum):
    EVIDENCE_BOUND = "EVIDENCE_BOUND"
    ASSIGNMENT_LOCKED = "ASSIGNMENT_LOCKED"
    INFORMATION_GAIN_REQUIRED = "INFORMATION_GAIN_REQUIRED"
    MECHANISM_BEFORE_GENERALIZATION = "MECHANISM_BEFORE_GENERALIZATION"
    CONCRETE_IF_SUPPORTED = "CONCRETE_IF_SUPPORTED"
    SPOKEN_ECONOMY = "SPOKEN_ECONOMY"
    VISUALIZABLE_SEMANTICS = "VISUALIZABLE_SEMANTICS"
    SCENE_MEANING_STABILITY = "SCENE_MEANING_STABILITY"
    AUTHENTIC_SOURCE_PREFERRED = "AUTHENTIC_SOURCE_PREFERRED"
    LEAST_COST_SUFFICIENT_MEDIA = "LEAST_COST_SUFFICIENT_MEDIA"
    ASSET_INTENT_PROOF = "ASSET_INTENT_PROOF"
    MOTION_MEANING_ALIGNED = "MOTION_MEANING_ALIGNED"
    FINAL_TRUTH_ALIGNMENT = "FINAL_TRUTH_ALIGNMENT"
    LOCALIZED_REPAIR_ONLY = "LOCALIZED_REPAIR_ONLY"


class ExecutionClassification(StrEnum):
    CODE_ENFORCED = "CODE_ENFORCED"
    DETERMINISTIC_COMPILER = "DETERMINISTIC_COMPILER"
    DETERMINISTIC_GATE = "DETERMINISTIC_GATE"
    CONTEXT_DIRECTIVE = "CONTEXT_DIRECTIVE"
    SKILL_REQUIRED = "SKILL_REQUIRED"
    SKILL_OPTIONAL = "SKILL_OPTIONAL"
    EVALUATOR_ONLY = "EVALUATOR_ONLY"
    PROVIDER_CAPABILITY = "PROVIDER_CAPABILITY"
    DOCUMENTATION_ONLY = "DOCUMENTATION_ONLY"
    HUMAN_BOUNDARY = "HUMAN_BOUNDARY"
    NO_SKILL_REQUIRED = "NO_SKILL_REQUIRED"


class SkillDefinition(BaseModel):
    skill_id: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=40)
    execution_classification: ExecutionClassification
    applicable_roles: tuple[str, ...] = Field(min_length=1)
    applicable_tasks: tuple[str, ...] = Field(min_length=1)
    required_features: tuple[str, ...] = ()
    primitive_refs: tuple[InstructionPrimitive, ...] = ()
    procedure_blocks: tuple[str, ...] = ()
    prohibited_behaviors: tuple[str, ...] = ()
    validation_hooks: tuple[str, ...] = ()
    max_compiled_tokens: int = Field(ge=1, le=380)
    compatibility: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)


class CompiledSkillProjection(BaseModel):
    compiler_version: str
    role: str
    task: str
    content_mode: str
    stage_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_identities: list[dict[str, str]] = Field(min_length=1)
    primitive_refs: list[InstructionPrimitive]
    directives: list[str]
    instruction_text: str
    estimated_tokens: int = Field(ge=0)
    max_compiled_tokens: int = Field(ge=1)
    compiled_projection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    def receipt_identity(self) -> dict[str, Any]:
        return {
            "compiler_version": self.compiler_version,
            "role": self.role,
            "task": self.task,
            "stage_context_hash": self.stage_context_hash,
            "skills": self.skill_identities,
            "primitive_refs": [item.value for item in self.primitive_refs],
            "compiled_projection_hash": self.compiled_projection_hash,
        }

    def provider_payload(self) -> dict[str, Any]:
        """The only capability representation a generative provider receives."""

        return {
            "projection_ref": self.compiled_projection_hash,
            "compiler_version": self.compiler_version,
            "role": self.role,
            "task": self.task,
            "directives": self.directives,
            "estimated_tokens": self.estimated_tokens,
        }


class CapabilityCompilationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


_PRIMITIVE_TEXT: dict[InstructionPrimitive, str] = {
    InstructionPrimitive.EVIDENCE_BOUND: "Use only supplied evidence for factual claims.",
    InstructionPrimitive.ASSIGNMENT_LOCKED: "Fulfill the frozen assignment; do not widen scope.",
    InstructionPrimitive.INFORMATION_GAIN_REQUIRED: "Each block must add a distinct information delta.",
    InstructionPrimitive.MECHANISM_BEFORE_GENERALIZATION: "Explain the mechanism before drawing a general conclusion.",
    InstructionPrimitive.CONCRETE_IF_SUPPORTED: "Use concrete examples only when the evidence supports them.",
    InstructionPrimitive.SPOKEN_ECONOMY: "Write concise natural speech; remove filler and repeated thesis lines.",
    InstructionPrimitive.VISUALIZABLE_SEMANTICS: "Prefer truthful concepts that can be visualized without invented facts.",
    InstructionPrimitive.SCENE_MEANING_STABILITY: "Keep one visual concept while the same explanation is developing.",
    InstructionPrimitive.AUTHENTIC_SOURCE_PREFERRED: "Prefer authentic evidence when factual authenticity matters.",
    InstructionPrimitive.LEAST_COST_SUFFICIENT_MEDIA: "Choose the least-cost route that still proves the intended meaning.",
    InstructionPrimitive.ASSET_INTENT_PROOF: "Every selected asset must prove subject, action, context, or factual fit.",
    InstructionPrimitive.MOTION_MEANING_ALIGNED: "Use motion only to reveal the meaning being narrated.",
    InstructionPrimitive.FINAL_TRUTH_ALIGNMENT: "Packaging may promise only what final narration and media establish.",
    InstructionPrimitive.LOCALIZED_REPAIR_ONLY: "Repair only the owning unit; preserve unaffected authority exactly.",
}

_BUDGETS = {
    ("script_writer", "long_form_script"): 280,
    ("script_verifier", "factuality_review"): 380,
    ("visual_realization_planner", "visual_realization"): 300,
    ("stock_query", "stock_retrieval"): 140,
    ("image_prompt", "image_prompting"): 220,
    ("veo_prompt", "hero_video_prompting"): 220,
    ("cross_modal_qc", "cross_modal_evaluation"): 380,
    ("packaging_truth", "packaging_truth"): 180,
}


def _definition(
    skill_id: str,
    *,
    roles: tuple[str, ...],
    tasks: tuple[str, ...],
    primitives: tuple[InstructionPrimitive, ...],
    procedures: tuple[str, ...],
    max_tokens: int,
    classification: ExecutionClassification = ExecutionClassification.SKILL_REQUIRED,
    features: tuple[str, ...] = (),
    compatibility: Mapping[str, Any] | None = None,
) -> SkillDefinition:
    body = {
        "skill_id": skill_id,
        "version": "v1",
        "execution_classification": classification.value,
        "applicable_roles": roles,
        "applicable_tasks": tasks,
        "required_features": features,
        "primitive_refs": [item.value for item in primitives],
        "procedure_blocks": procedures,
        "prohibited_behaviors": (),
        "validation_hooks": (),
        "max_compiled_tokens": max_tokens,
        "compatibility": dict(compatibility or {}),
    }
    return SkillDefinition(**body, content_hash=stable_hash(body))


DEFAULT_SKILLS: tuple[SkillDefinition, ...] = (
    _definition(
        "coverage_design",
        roles=("script_writer",),
        tasks=("long_form_script",),
        primitives=(InstructionPrimitive.ASSIGNMENT_LOCKED, InstructionPrimitive.INFORMATION_GAIN_REQUIRED),
        procedures=("Write each planned section for its owned information units only.",),
        max_tokens=100,
    ),
    _definition(
        "production_aware_narration",
        roles=("script_writer",),
        tasks=("long_form_script",),
        primitives=(InstructionPrimitive.EVIDENCE_BOUND, InstructionPrimitive.MECHANISM_BEFORE_GENERALIZATION, InstructionPrimitive.CONCRETE_IF_SUPPORTED, InstructionPrimitive.SPOKEN_ECONOMY, InstructionPrimitive.VISUALIZABLE_SEMANTICS),
        procedures=("Do not add camera directions, provider choices, or invented visual facts.",),
        max_tokens=220,
    ),
    _definition(
        "script_semantic_evaluation",
        roles=("script_verifier",),
        tasks=("factuality_review",),
        primitives=(InstructionPrimitive.EVIDENCE_BOUND, InstructionPrimitive.ASSIGNMENT_LOCKED, InstructionPrimitive.INFORMATION_GAIN_REQUIRED),
        procedures=("Report typed observations only; never issue a final qualification decision.",),
        max_tokens=180,
        classification=ExecutionClassification.EVALUATOR_ONLY,
    ),
    _definition(
        "visual_scene_design",
        roles=("visual_realization_planner",),
        tasks=("visual_realization",),
        primitives=(InstructionPrimitive.VISUALIZABLE_SEMANTICS, InstructionPrimitive.SCENE_MEANING_STABILITY, InstructionPrimitive.ASSET_INTENT_PROOF),
        procedures=("Group narration units by coherent meaning, never one scene per sentence.",),
        max_tokens=170,
    ),
    _definition(
        "visual_source_routing",
        roles=("visual_realization_planner",),
        tasks=("visual_realization",),
        primitives=(InstructionPrimitive.AUTHENTIC_SOURCE_PREFERRED, InstructionPrimitive.LEAST_COST_SUFFICIENT_MEDIA),
        procedures=("Do not treat failed stock search as authorization for paid AI.",),
        max_tokens=120,
    ),
    _definition(
        "stock_retrieval",
        roles=("stock_query",),
        tasks=("stock_retrieval",),
        primitives=(InstructionPrimitive.ASSET_INTENT_PROOF,),
        procedures=("Query visible subject plus action and necessary context; reject abstract slogans.",),
        max_tokens=110,
    ),
    _definition(
        "scene_image_prompting",
        roles=("image_prompt",),
        tasks=("image_prompting",),
        primitives=(InstructionPrimitive.ASSET_INTENT_PROOF,),
        procedures=("Use one subject hierarchy and no generated exact text, numbers, logos, or fake UI.",),
        max_tokens=140,
    ),
    _definition(
        "hero_video_prompting",
        roles=("veo_prompt",),
        tasks=("hero_video_prompting",),
        primitives=(InstructionPrimitive.MOTION_MEANING_ALIGNED,),
        procedures=("Use one subject, action, environment, camera behavior, and temporal progression.",),
        max_tokens=120,
    ),
    _definition(
        "cross_modal_quality_evaluation",
        roles=("cross_modal_qc",),
        tasks=("cross_modal_evaluation",),
        primitives=(InstructionPrimitive.FINAL_TRUTH_ALIGNMENT, InstructionPrimitive.ASSET_INTENT_PROOF),
        procedures=("Emit typed mismatch observations without a PASS score or publishing authority.",),
        max_tokens=160,
        classification=ExecutionClassification.EVALUATOR_ONLY,
    ),
    _definition(
        "packaging_truth_alignment",
        roles=("packaging_truth",),
        tasks=("packaging_truth",),
        primitives=(InstructionPrimitive.FINAL_TRUTH_ALIGNMENT,),
        procedures=("Derive title, thumbnail brief, description, and disclosure from final qualified truth only.",),
        max_tokens=120,
    ),
)


class CapabilityCompiler:
    def __init__(self, definitions: Iterable[SkillDefinition] = DEFAULT_SKILLS) -> None:
        self._definitions = tuple(sorted(definitions, key=lambda item: (item.skill_id, item.version)))
        keys = [(item.skill_id, item.version) for item in self._definitions]
        if len(keys) != len(set(keys)):
            raise ValueError("CAPABILITY_SKILL_IDENTITY_DUPLICATE")

    def compile(
        self,
        *,
        role: str,
        task: str,
        content_mode: str,
        channel_policy: Mapping[str, Any] | None = None,
        stage_context: Mapping[str, Any] | None = None,
        skill_ids: Iterable[str] | None = None,
    ) -> CompiledSkillProjection:
        selected_ids = set(skill_ids or ())
        policy = dict(channel_policy or {})
        context = dict(stage_context or {})
        features = set(policy.get("features") or ()) | set(context.get("features") or ())
        pinned = dict(policy.get("skill_pins") or {})
        selected = [
            item
            for item in self._definitions
            if role in item.applicable_roles
            and task in item.applicable_tasks
            and (not selected_ids or item.skill_id in selected_ids)
            and set(item.required_features).issubset(features)
        ]
        if selected_ids and {item.skill_id for item in selected} != selected_ids:
            raise CapabilityCompilationError("CAPABILITY_SKILL_NOT_APPLICABLE")
        if not selected:
            raise CapabilityCompilationError("CAPABILITY_PROJECTION_EMPTY")
        for item in selected:
            if item.skill_id in pinned and pinned[item.skill_id] != item.version:
                raise CapabilityCompilationError("CAPABILITY_SKILL_VERSION_PIN_MISMATCH")
        self._validate_compatibility(selected, content_mode)
        primitive_refs = list(dict.fromkeys(
            primitive for item in selected for primitive in item.primitive_refs
        ))
        directives = list(dict.fromkeys(
            [*(_PRIMITIVE_TEXT[item] for item in primitive_refs), *(
                procedure for skill in selected for procedure in skill.procedure_blocks
            )]
        ))
        instruction_text = "\n".join(f"- {item}" for item in directives)
        budget = _BUDGETS.get((role, task), min(item.max_compiled_tokens for item in selected))
        estimated = estimate_tokens(instruction_text)
        if estimated > budget:
            raise CapabilityCompilationError("CAPABILITY_PROJECTION_TOKEN_BUDGET_EXCEEDED")
        stage_context_hash = stable_hash({"content_mode": content_mode, "context": context})
        identities = [
            {"skill_id": item.skill_id, "version": item.version, "content_hash": item.content_hash}
            for item in selected
        ]
        body = {
            "compiler_version": COMPILER_VERSION,
            "role": role,
            "task": task,
            "content_mode": content_mode,
            "stage_context_hash": stage_context_hash,
            "skill_identities": identities,
            "primitive_refs": [item.value for item in primitive_refs],
            "directives": directives,
            "instruction_text": instruction_text,
            "estimated_tokens": estimated,
            "max_compiled_tokens": budget,
        }
        return CompiledSkillProjection(**body, compiled_projection_hash=stable_hash(body))

    @staticmethod
    def _validate_compatibility(selected: list[SkillDefinition], content_mode: str) -> None:
        selected_ids = {item.skill_id for item in selected}
        for item in selected:
            compatibility = item.compatibility or {}
            allowed_modes = set(compatibility.get("content_modes") or ())
            if allowed_modes and content_mode not in allowed_modes:
                raise CapabilityCompilationError("CAPABILITY_CONTENT_MODE_INCOMPATIBLE")
            conflicts = set(compatibility.get("conflicts") or ())
            if conflicts & selected_ids:
                raise CapabilityCompilationError("CAPABILITY_SKILL_CONFLICT")


def default_capability_compiler() -> CapabilityCompiler:
    return CapabilityCompiler()
