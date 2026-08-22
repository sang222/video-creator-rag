"""Concrete, package-bound V2 AI-only VISUAL production stage.

The stage deliberately separates three authorities:

* the frozen source timeline (the source workflow for a governed rerender),
* the replacement workflow and its pre-authorized ``AIVisualProductionRun``, and
* one transactional provider effect for every unique primary asset slot.

Presentation windows that reuse an owner asset never receive a prompt or a
provider effect.  Provider submission starts only after every owner effect is
prepared, every readiness projection passes, and the visual run's dedicated
monthly reservation has been durably marked ``SUBMITTED``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.ai_visual_production import (
    AIVisualCapabilityProjection,
    AIVisualNarrationUnit,
    AIVisualPlanCompilation,
    AIVisualPlanningPolicy,
    CompiledAIImagePrompt,
    CompiledAIVideoPrompt,
    FFmpegEffectPlan,
    MotionNeed,
    MotionIntentProjection,
    VideoMotionGrammar,
    VideoVisualStyleBible,
    ai_visual_stable_hash,
)
from app.contracts.ai_visual_cross_modal import VeoTechnicalMotionInspectionEvidence
from app.contracts.production_workflow import (
    ProductionWorkflowStage,
    WorkflowFailureClassification,
    WorkflowAuthorityRefs,
    WorkflowStageResult,
)
from app.core.config import (
    GEMINI_IMAGE_DEFAULT_ASPECT_RATIO,
    GEMINI_IMAGE_DEFAULT_MODEL_ID,
    GEMINI_IMAGE_DEFAULT_SIZE,
    VEO_DEFAULT_ASPECT_RATIO,
    VEO_DEFAULT_DURATION_SECONDS,
    VEO_DEFAULT_MODEL_ID,
    VEO_DEFAULT_OUTPUT_COUNT,
    VEO_DEFAULT_RESOLUTION,
    get_settings,
)
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.ai_visual import (
    AI_VISUAL_POLICY_VERSION,
    AIVisualAssetEffect,
    AIVisualAssetManifest,
    AIVisualProductionRun,
    AIVisualRerenderAuthority,
    AIVisualScenePlanSnapshot,
    AIVisualStyleBible,
)
from app.db.models.mr1_budget import MR1MonthlyBudgetReservation
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.v2_effect import V2ProductionEffectLedger
from app.db.models.voice_authority import CombinedReplacementBudgetAuthority
from app.db.models.workflow import ArtifactVersion
from app.services.ai_visual_planner import (
    AIImagePromptCompiler,
    AIVideoPromptCompiler,
    MotionIntentPlanner,
    UnifiedAIVisualPlanner,
)
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.google_gemini_image_catalog import (
    GoogleGeminiImageModelPriceCatalog,
)
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.mr1_monthly_budget import MR1MonthlyBudgetAuthority
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.v2_ai_visual_provider import (
    V2AIImageEffectState,
    V2AIImageProductionService,
    V2AIImageSceneEffectIdentity,
    V2AIImageSceneEffectRecord,
    V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD,
    V2GeminiImageOfficialClientFactory,
    V2GeminiImageProductionAdapter,
)
from app.services.v2_ai_visual_renderer import (
    AIVisualAssetManifestProjection,
    VerifiedAIVisualAsset,
    build_ai_visual_asset_manifest,
    validate_ai_visual_asset_manifest,
)
from app.services.v2_ai_visual_store import (
    SQLAlchemyAIImageSceneEffectStore,
    SQLAlchemyVeoEffectStore,
)
from app.services.v2_veo_visual_provider import (
    FFmpegV2VeoMediaRuntime,
    GoogleGenAIVeoSDKClient,
    V2VeoEffectRecord,
    V2VeoExecutionAuthorization,
    V2VeoGenerationAuthority,
    V2VeoOperationPersistenceError,
    V2VeoProductionReadiness,
    V2VeoVisualProductionProvider,
    build_v2_veo_provider_config_payload,
)
from app.services.v2_native_effects import V2LocalNativeProductionAdapter
from app.services.v2_provider_production import (
    V2AuthorizedAdapterOperation,
    V2ProductionAdapterDescriptor,
)
from app.services.production_workflow import WorkflowStageContext, WorkflowStageError
from app.services.native_render_plan import stable_hash as veo_stable_hash


V2_AI_VISUAL_PRODUCTION_ADAPTER_KEY = "v2-ai-visual-production"
V2_AI_VISUAL_STAGE_VERSION = "vcos.v2-ai-visual-production-stage.v1"
V2_AI_VISUAL_SCENE_SET_SCHEMA = "vcos.ai-visual-scene-plan-set.v1"
V2_AI_VISUAL_POLICY_REF = (
    "config://production_visual_policy_catalog/2026-08-13/active-real-long-form-ai-only"
)
V2_AI_VISUAL_PROVIDER_KEY = "google_gemini_image"
V2_AI_VISUAL_VIDEO_PROVIDER_KEY = "google_veo"
V2_AI_VISUAL_VEO_PROVIDER_CONFIG_VERSION = "vcos.google-veo.production.v1"
V2_AI_VISUAL_FIRST_VIDEO_MAX_IMAGES = 14
V2_AI_VISUAL_FIRST_VIDEO_MAX_VIDEOS = 0
V2_AI_VISUAL_CONSERVATIVE_UNIT_COST_USD = V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD
V2_AI_VISUAL_FIRST_VIDEO_IMAGE_COST_USD = (
    V2_AI_VISUAL_CONSERVATIVE_UNIT_COST_USD * V2_AI_VISUAL_FIRST_VIDEO_MAX_IMAGES
)
V2_AI_VISUAL_FIRST_VIDEO_VIDEO_UNIT_COST_USD = Decimal("0.800000")
V2_AI_VISUAL_FIRST_VIDEO_VIDEO_COST_USD = (
    V2_AI_VISUAL_FIRST_VIDEO_VIDEO_UNIT_COST_USD * V2_AI_VISUAL_FIRST_VIDEO_MAX_VIDEOS
)
V2_AI_VISUAL_FIRST_VIDEO_MAX_COST_USD = (
    V2_AI_VISUAL_FIRST_VIDEO_IMAGE_COST_USD + V2_AI_VISUAL_FIRST_VIDEO_VIDEO_COST_USD
)
V2_AI_VISUAL_MOTION_CLASSIFIER_VERSION = (
    "vcos.deterministic-semantic-motion-classifier.v2"
)
V2_AI_VISUAL_DUPLICATION_POLICY_VERSION = "vcos.ai-visual-owner-dhash-gate.v1"
V2_AI_VISUAL_DHASH_BIT_WIDTH = 64
# A 64-bit dHash distance of six or fewer is a conservative near-duplicate
# boundary (at most 9.375% of comparison bits differ).  Only unique owner
# effects are compared; presentation windows reusing that owner never create a
# second effect and therefore never enter this gate.
V2_AI_VISUAL_DHASH_NEAR_DUPLICATE_MAX_DISTANCE = 6
V2_AI_VISUAL_VEO_MAX_POLLS_PER_EVENT = 12
V2_AI_VISUAL_VEO_POLL_INTERVAL_SECONDS = 1.0


_TEMPORAL_MOTION_FUNCTIONS = frozenset(
    {
        "ACTION",
        "FOLLOW",
        "PROCESS",
        "PROGRESSION",
        "TRANSFORMATION",
        "TRANSITION_HERO",
    }
)
_REQUIRED_MOTION_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "TRANSFORM_INTO",
        re.compile(
            r"\b(?:transform(?:s|ed|ing|ation)?|convert(?:s|ed|ing)?)\b"
            r"|\bturn(?:s|ed|ing)?\b.{0,96}\binto\b",
            re.IGNORECASE,
        ),
    ),
    (
        "STATE_CHANGE",
        re.compile(
            r"\b(?:become|becomes|became|changing?\s+into|changes?\s+into|"
            r"evolv(?:e|es|ed|ing)|morph(?:s|ed|ing)?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ORDERED_PROGRESSION",
        re.compile(
            r"\b(?:step[ -]by[ -]step|in sequence|sequence unfolds|"
            r"progress(?:es|ed|ing)? through|flows? through|moves? through|"
            r"passes? through)\b"
            r"|\b(?:first|before)\b.{0,160}\b(?:then|after|finally)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "VI_TRANSFORMATION",
        re.compile(
            r"\b(?:biến|chuyển|chuyển đổi|chuyển hoá)\b.{0,96}\b(?:thành|sang)\b"
            r"|\b(?:từng bước|lần lượt|trước).{0,160}(?:sau|ồi|cuối cùng)\b",
            re.IGNORECASE,
        ),
    ),
)


def _mixed_conservative_cost_within_authority(
    *,
    execution_kind: str,
    image_asset_count: int,
    video_asset_count: int,
    maximum_image_submissions: int,
    maximum_video_submissions: int,
    maximum_total_cost_usd: Decimal,
    video_unit_cost_usd: Decimal | None,
) -> Decimal:
    if video_asset_count and video_unit_cost_usd is None:
        raise ValidationFailureError("V2_AI_VISUAL_VIDEO_COST_AUTHORITY_REQUIRED")
    conservative = (
        V2_AI_VISUAL_CONSERVATIVE_UNIT_COST_USD * image_asset_count
        + (video_unit_cost_usd or Decimal("0")) * video_asset_count
    )
    if (
        image_asset_count < 0
        or video_asset_count < 0
        or image_asset_count + video_asset_count <= 0
        or image_asset_count > maximum_image_submissions
        or video_asset_count > maximum_video_submissions
        or conservative <= 0
        or conservative > maximum_total_cost_usd
    ):
        raise ValidationFailureError("V2_AI_VISUAL_PLAN_OUTSIDE_COST_AUTHORITY")
    if execution_kind == "GOVERNED_RERENDER":
        if (
            image_asset_count != V2_AI_VISUAL_FIRST_VIDEO_MAX_IMAGES
            or video_asset_count != V2_AI_VISUAL_FIRST_VIDEO_MAX_VIDEOS
            or conservative != V2_AI_VISUAL_FIRST_VIDEO_MAX_COST_USD
        ):
            raise ValidationFailureError(
                "V2_AI_VISUAL_FIRST_VIDEO_ROUTE_PROJECTION_MISMATCH"
            )
    elif execution_kind != "NORMAL_PRODUCTION":
        raise ValidationFailureError("V2_AI_VISUAL_EXECUTION_KIND_INVALID")
    return conservative


class _ImageReadiness(Protocol):
    execution_ready: bool
    blocker_codes: Sequence[str]


class AIVisualStageImageService(Protocol):
    @property
    def catalog_hash(self) -> str: ...

    def prepare(
        self, identity: V2AIImageSceneEffectIdentity
    ) -> V2AIImageSceneEffectRecord: ...

    def readiness_projection(
        self, identity: V2AIImageSceneEffectIdentity | None = None
    ) -> _ImageReadiness: ...

    def execute(self, *, effect_id: str) -> V2AIImageSceneEffectRecord: ...


class AIVisualStageVideoService(Protocol):
    @property
    def catalog(self) -> GoogleVeoModelPriceCatalog: ...

    def prepare(self, authority: V2VeoGenerationAuthority) -> V2VeoEffectRecord: ...

    def readiness(
        self,
        *,
        authority: V2VeoGenerationAuthority,
        execution: V2VeoExecutionAuthorization,
    ) -> V2VeoProductionReadiness: ...

    def submit_once(
        self,
        *,
        authority: V2VeoGenerationAuthority,
        execution: V2VeoExecutionAuthorization,
    ) -> V2VeoEffectRecord: ...

    def poll_once(
        self, *, authority: V2VeoGenerationAuthority
    ) -> V2VeoEffectRecord: ...

    def materialize(
        self, *, authority: V2VeoGenerationAuthority
    ) -> V2VeoEffectRecord: ...


@dataclass(frozen=True, slots=True)
class AIVisualStagePlanningArtifacts:
    style_bible: VideoVisualStyleBible
    policy: AIVisualPlanningPolicy
    capability: AIVisualCapabilityProjection
    scene_plan: AIVisualPlanCompilation
    scene_plan_payload: dict[str, Any]
    scene_plan_hash: str
    motion_grammar: VideoMotionGrammar
    motion_classification: Mapping[str, Any]
    image_prompts_by_scene_id: Mapping[str, CompiledAIImagePrompt]
    video_prompts_by_scene_id: Mapping[str, CompiledAIVideoPrompt]


@dataclass(frozen=True, slots=True)
class _StageScope:
    workflow: ProductionWorkflowRun
    visual_run: AIVisualProductionRun
    rerender_authority: AIVisualRerenderAuthority | None
    source_workflow_run_id: uuid.UUID
    package: ArtifactVersion
    budget: MR1MonthlyBudgetReservation
    timeline: dict[str, Any]
    timeline_path: Path
    media_journal: dict[str, Any]
    policy_hash: str
    approval_ref: str
    approval_hash: str
    maximum_image_submissions: int
    maximum_video_submissions: int
    maximum_scene_count: int
    maximum_total_cost_usd: Decimal
    provider_allocations_usd: Mapping[str, Decimal]
    video_unit_cost_usd: Decimal | None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    ).encode("utf-8")


def _persist_exact_json(path: Path, value: Any) -> bool:
    """Write one content-addressed stage artifact or verify exact replay bytes."""

    payload = _json_bytes(value)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValidationFailureError("V2_AI_VISUAL_ARTIFACT_IDENTITY_CONFLICT")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValidationFailureError("V2_AI_VISUAL_ARTIFACT_PARENT_SYMLINK")
    partial = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if partial.exists():
            partial.unlink()
    return False


def _stable_uuid(namespace: str, *parts: object) -> uuid.UUID:
    value = ":".join([namespace, *(str(part) for part in parts)])
    return uuid.uuid5(uuid.NAMESPACE_URL, value)


def _apply_exact_planning_projection(
    visual_run: AIVisualProductionRun,
    *,
    style_bible_id: uuid.UUID,
    style_bible_hash: str,
    scene_plan_id: uuid.UUID,
    scene_plan_hash: str,
    motion_grammar_ref: str,
    motion_grammar_hash: str,
) -> bool:
    """Bind planning once, or prove an exact non-reversing crash replay.

    Returns ``True`` only for the one legal ``AUTHORIZED -> PLANNED``
    mutation.  A replay from ``PLANNED`` or ``GENERATING`` must already carry
    every exact immutable binding and therefore performs no projection write.
    """

    exact_bindings = (
        visual_run.style_bible_id == style_bible_id
        and visual_run.style_bible_hash == style_bible_hash
        and visual_run.scene_plan_id == scene_plan_id
        and visual_run.scene_plan_hash == scene_plan_hash
        and visual_run.motion_grammar_ref == motion_grammar_ref
        and visual_run.motion_grammar_hash == motion_grammar_hash
    )
    if visual_run.state == "AUTHORIZED":
        if visual_run.current_phase != "AUTHORIZE" or any(
            value is not None
            for value in (
                visual_run.style_bible_id,
                visual_run.style_bible_hash,
                visual_run.scene_plan_id,
                visual_run.scene_plan_hash,
                visual_run.motion_grammar_ref,
                visual_run.motion_grammar_hash,
            )
        ):
            raise ValidationFailureError(
                "V2_AI_VISUAL_AUTHORIZED_PLANNING_BINDING_INVALID"
            )
        visual_run.style_bible_id = style_bible_id
        visual_run.style_bible_hash = style_bible_hash
        visual_run.scene_plan_id = scene_plan_id
        visual_run.scene_plan_hash = scene_plan_hash
        visual_run.motion_grammar_ref = motion_grammar_ref
        visual_run.motion_grammar_hash = motion_grammar_hash
        visual_run.state = "PLANNED"
        visual_run.current_phase = "PLAN"
        visual_run.projection_version += 1
        return True
    expected_phase = {
        "PLANNED": "PLAN",
        "GENERATING": "GENERATE",
    }.get(visual_run.state)
    if expected_phase is None:
        raise ValidationFailureError("V2_AI_VISUAL_RUN_PLANNING_STATE_INVALID")
    if visual_run.current_phase != expected_phase or not exact_bindings:
        raise ValidationFailureError("V2_AI_VISUAL_PLANNING_REPLAY_AUTHORITY_DRIFT")
    return False


def _importance(value: str) -> str:
    normalized = value.strip().upper()
    return {
        "SUPPORTING": "SUPPORTING",
        "STANDARD": "STANDARD",
        "CORE": "HIGH",
        "HIGH": "HIGH",
        "HERO": "HERO",
    }.get(normalized, "STANDARD")


def _factual_risk(value: str) -> str:
    normalized = value.strip().upper()
    return normalized if normalized in {"LOW", "MEDIUM", "HIGH"} else "MEDIUM"


def _visual_function(value: str) -> str:
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "AUTHENTIC_EVIDENCE_CONTEXT": "EXAMPLE_CONTEXT",
        "PROCESS_OR_DECISION_MODEL": "PROCESS",
        "EXPLANATORY_CONTEXT": "CONCEPT_MODEL",
        "BOUNDARY_COMPARISON": "COMPARISON",
    }
    canonical = {
        "ACTION",
        "COMPARISON",
        "CONCEPT_MODEL",
        "DATA",
        "EXAMPLE_CONTEXT",
        "FOLLOW",
        "INTERFACE",
        "PROCESS",
        "PROGRESSION",
        "TRANSFORMATION",
        "TRANSITION_HERO",
    }
    resolved = aliases.get(normalized, normalized)
    return resolved if resolved in canonical else "CONCEPT_MODEL"


def _normalized_motion_text(*values: str) -> str:
    return " ".join(" ".join(value.casefold().split()) for value in values if value)


def classify_narration_motion_need(
    *,
    narration_unit_id: str,
    visual_function: str,
    semantic_intent: str,
    spoken_text: str,
) -> tuple[MotionNeed, dict[str, Any]]:
    """Classify intrinsic motion from frozen semantics, never provider supply.

    A temporal function alone is only ``MOTION_BENEFICIAL``.  The classifier
    requires an explicit state-change or ordered-progression cue before motion
    becomes semantically mandatory.  This prevents a provider budget from
    silently changing meaning while still making genuine transformations
    impossible to downgrade to a still image.
    """

    function = _visual_function(visual_function)
    # A parent information-unit semantic intent is commonly repeated verbatim
    # across many child narration windows.  Treating that repeated summary as
    # observed temporal language multiplies one editorial concept into many
    # mandatory provider clips.  Required motion is therefore proven only by
    # the actual spoken text of this exact timed child window; the parent
    # intent remains hash-bound context and may still guide image/native motion.
    normalized_text = _normalized_motion_text(spoken_text)
    matched_cues = [
        cue_id
        for cue_id, pattern in _REQUIRED_MOTION_CUES
        if pattern.search(normalized_text)
    ]
    if function in _TEMPORAL_MOTION_FUNCTIONS and matched_cues:
        motion_need: MotionNeed = "MOTION_REQUIRED"
        reason_code = "EXPLICIT_TEMPORAL_MEANING_REQUIRES_INTRINSIC_MOTION"
    elif function in _TEMPORAL_MOTION_FUNCTIONS:
        motion_need = "MOTION_BENEFICIAL"
        reason_code = "TEMPORAL_FUNCTION_WITHOUT_REQUIRED_PROGRESSION_CUE"
    else:
        motion_need = "STATIC_SUFFICIENT"
        reason_code = "STATIC_SEMANTIC_FUNCTION"
    input_payload = {
        "narration_unit_id": narration_unit_id,
        "visual_function_source": visual_function,
        "visual_function_normalized": function,
        "semantic_intent": semantic_intent,
        "spoken_text": spoken_text,
        "required_cue_source": "EXACT_CHILD_SPOKEN_TEXT_ONLY",
    }
    evidence = {
        "narration_unit_id": narration_unit_id,
        "classifier_version": V2_AI_VISUAL_MOTION_CLASSIFIER_VERSION,
        "input_hash": ai_visual_stable_hash(input_payload),
        "visual_function": function,
        "matched_required_cue_ids": matched_cues,
        "motion_need": motion_need,
        "reason_code": reason_code,
        "semantic_intent_used_for_required_cues": False,
    }
    return motion_need, evidence


def _motion_classification_set(
    items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    counts = Counter(str(item["motion_need"]) for item in items)
    body: dict[str, Any] = {
        "schema_version": "vcos.ai-visual-motion-classification-set.v1",
        "classifier_version": V2_AI_VISUAL_MOTION_CLASSIFIER_VERSION,
        "classification_count": len(items),
        "motion_required_count": counts["MOTION_REQUIRED"],
        "motion_beneficial_count": counts["MOTION_BENEFICIAL"],
        "static_sufficient_count": counts["STATIC_SUFFICIENT"],
        "classifications": [dict(item) for item in items],
    }
    return {**body, "content_hash": ai_visual_stable_hash(body)}


def _owner_dhash_duplication_policy() -> dict[str, Any]:
    body = {
        "schema_version": V2_AI_VISUAL_DUPLICATION_POLICY_VERSION,
        "algorithm": "dHash",
        "bit_width": V2_AI_VISUAL_DHASH_BIT_WIDTH,
        "comparison_scope": "DISTINCT_AI_IMAGE_OWNER_ASSETS_ONLY",
        "near_duplicate_max_hamming_distance": (
            V2_AI_VISUAL_DHASH_NEAR_DUPLICATE_MAX_DISTANCE
        ),
        "reuse_windows_compared": False,
    }
    return {**body, "content_hash": ai_visual_stable_hash(body)}


def _require_distinct_owner_dhashes(values: Sequence[str]) -> None:
    parsed: list[int] = []
    for value in values:
        if not re.fullmatch(r"[0-9a-fA-F]{16}", value):
            raise ValidationFailureError("V2_AI_VISUAL_DHASH64_EVIDENCE_INVALID")
        parsed.append(int(value, 16))
    for left_index, left in enumerate(parsed):
        for right in parsed[left_index + 1 :]:
            if (left ^ right).bit_count() <= (
                V2_AI_VISUAL_DHASH_NEAR_DUPLICATE_MAX_DISTANCE
            ):
                raise ValidationFailureError(
                    "V2_AI_VISUAL_ASSET_DUPLICATION_GATE_FAILED"
                )


def _semantic_group_keys(units: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Collapse repeated information units and pair compatible singletons.

    Pairing is generic and deterministic: only adjacent first-seen singleton
    information units with the same visual function may share an owner asset.
    It never joins non-adjacent narration or crosses a normalized-function
    boundary.
    """
    primary_ids = [str(list(unit["information_unit_ids"])[0]) for unit in units]
    counts = Counter(primary_ids)
    first_seen: list[tuple[str, str]] = []
    seen: set[str] = set()
    for unit, information_id in zip(units, primary_ids):
        if information_id in seen:
            continue
        seen.add(information_id)
        first_seen.append(
            (information_id, _visual_function(str(unit["visual_function"])))
        )

    group_by_information_id: dict[str, str] = {}
    cursor = 0
    while cursor < len(first_seen):
        information_id, function = first_seen[cursor]
        if counts[information_id] > 1:
            group_by_information_id[information_id] = (
                f"information-unit:{information_id}"
            )
            cursor += 1
            continue
        if cursor + 1 < len(first_seen):
            next_id, next_function = first_seen[cursor + 1]
            if counts[next_id] == 1 and next_function == function:
                pair_key = f"adjacent-singletons:{information_id}+{next_id}"
                group_by_information_id[information_id] = pair_key
                group_by_information_id[next_id] = pair_key
                cursor += 2
                continue
        group_by_information_id[information_id] = f"information-unit:{information_id}"
        cursor += 1
    return group_by_information_id


def _narration_units_with_motion_evidence(
    timeline: Mapping[str, Any],
) -> tuple[list[AIVisualNarrationUnit], dict[str, Any]]:
    compilation = timeline.get("narration_unit_compilation")
    binding_set = timeline.get("timed_narration_unit_bindings")
    raw_units = (
        compilation.get("narration_units") if isinstance(compilation, dict) else None
    )
    raw_bindings = (
        binding_set.get("bindings") if isinstance(binding_set, dict) else None
    )
    if (
        not isinstance(raw_units, list)
        or not raw_units
        or not isinstance(raw_bindings, list)
    ):
        raise ValidationFailureError("V2_AI_VISUAL_TIMED_NARRATION_REQUIRED")
    bindings = {
        str(item.get("narration_unit_id")): item
        for item in raw_bindings
        if isinstance(item, dict) and item.get("narration_unit_id")
    }
    if len(bindings) != len(raw_units):
        raise ValidationFailureError("V2_AI_VISUAL_TIMED_NARRATION_BINDING_INCOMPLETE")
    group_by_information_id = _semantic_group_keys(raw_units)
    result: list[AIVisualNarrationUnit] = []
    classifications: list[dict[str, Any]] = []
    for raw in raw_units:
        if not isinstance(raw, dict):
            raise ValidationFailureError("V2_AI_VISUAL_NARRATION_UNIT_INVALID")
        narration_unit_id = str(raw.get("narration_unit_id") or "")
        binding = bindings.get(narration_unit_id)
        information_ids = [
            str(value) for value in raw.get("information_unit_ids") or []
        ]
        if binding is None or not information_ids:
            raise ValidationFailureError(
                "V2_AI_VISUAL_NARRATION_UNIT_AUTHORITY_INVALID"
            )
        semantic_group = group_by_information_id[information_ids[0]]
        source_function = str(raw.get("visual_function") or "")
        function = _visual_function(source_function)
        semantic_intent = str(
            raw.get("semantic_intent") or raw.get("text") or ""
        ).strip()
        spoken_text = str(raw.get("text") or "").strip()
        if not semantic_intent or not spoken_text:
            raise ValidationFailureError("V2_AI_VISUAL_NARRATION_TEXT_REQUIRED")
        motion_need, motion_evidence = classify_narration_motion_need(
            narration_unit_id=narration_unit_id,
            visual_function=source_function,
            semantic_intent=semantic_intent,
            spoken_text=spoken_text,
        )
        result.append(
            AIVisualNarrationUnit(
                narration_unit_id=narration_unit_id,
                information_unit_ids=information_ids,
                actual_start_ms=int(binding["actual_start_ms"]),
                actual_end_ms=int(binding["actual_end_ms"]),
                spoken_text=spoken_text,
                scene_meaning=semantic_intent,
                visual_function=function,
                core_subject=f"semantic subject group {semantic_group}",
                secondary_subjects=[],
                action_or_relation=spoken_text,
                environment=(
                    "a cinematic conceptual environment grounded in the narrated "
                    "workflow, with no interface simulation"
                ),
                visual_goal=(
                    f"Make the narrated {function.casefold().replace('_', ' ')} "
                    "legible through one coherent visual metaphor"
                ),
                composition_direction=(
                    "cinematic off-center subject hierarchy with meaningful negative "
                    "space and complete environmental context"
                ),
                camera_direction="restrained documentary lens language, crop-safe framing",
                continuity_constraints=[
                    "preserve the same subject identity and visual world for this semantic group",
                    "do not introduce legible text, product UI, logos, or presentation graphics",
                ],
                motion_need=motion_need,
                factual_risk=_factual_risk(str(raw.get("factual_risk") or "MEDIUM")),
                importance=_importance(str(raw.get("importance") or "STANDARD")),
                # This active timeline carries no upstream Card E
                # PresentationSemanticIntent.  This explicit non-authority
                # marker can only resolve to a technical CUT; group, function,
                # and position never become transition authorship.
                transition_semantic_reason="UNAUTHORED_TECHNICAL_CUT",
                semantic_group_key=semantic_group,
            )
        )
        classifications.append(motion_evidence)
    return result, _motion_classification_set(classifications)


def narration_units_from_frozen_timeline(
    timeline: Mapping[str, Any],
) -> list[AIVisualNarrationUnit]:
    units, _ = _narration_units_with_motion_evidence(timeline)
    return units


def build_video_visual_style_bible(
    *, visual_run: AIVisualProductionRun
) -> VideoVisualStyleBible:
    style_id = _stable_uuid("vcos-ai-visual-style-bible", visual_run.id)
    return VideoVisualStyleBible.build(
        style_bible_id=str(style_id),
        video_project_id=str(visual_run.video_project_id),
        package_id=str(visual_run.production_package_artifact_version_id),
        overall_visual_language=(
            "cinematic conceptual realism with precise semantic metaphors, human-scale "
            "environments, and a consistent editorial-documentary identity"
        ),
        rendering_style=(
            "photoreal cinematic illustration with restrained premium production design; "
            "never a slide, dashboard, diagram card, or synthetic interface"
        ),
        lighting="soft directional daylight with motivated practical highlights",
        contrast="controlled medium contrast with readable subject separation",
        palette_guidance=[
            "deep indigo and slate foundations",
            "warm amber semantic highlights",
            "natural neutral skin and material tones",
        ],
        materials=[
            "matte glass",
            "brushed metal",
            "paper",
            "natural architectural surfaces",
        ],
        camera_language=(
            "35mm to 50mm documentary perspective, intentional off-center framing, "
            "subtle depth and no mechanical symmetry"
        ),
        depth="layered foreground, subject, and environmental context with gentle falloff",
        technical_illustration_language=(
            "physical cause-and-effect metaphors and spatial relationships, without labels"
        ),
        human_depiction_rules=[
            "use plausible diverse adults only when the narration benefits from human context",
            "avoid uncanny faces, duplicated anatomy, staged stock-photo gestures, and stereotypes",
        ],
        technology_depiction_rules=[
            "represent computation through physical systems and materials rather than fake UI",
            "show no brands, logos, legible screens, or fabricated product controls",
        ],
        negative_aesthetic_constraints=[
            "no centered product render",
            "no generic blue hologram",
            "no corporate stock-photo tableau",
            "no split-screen template",
            "no baked-in captions",
        ],
    )


def _active_motion_policy() -> Mapping[str, Any]:
    loaded = ConfigRegistryService(None).validate_catalog(
        Path(__file__).resolve().parents[2]
        / "config"
        / "production_visual_policy_catalog.yaml"
    )
    items = loaded.content.get("items") or []
    item = items[0] if len(items) == 1 and isinstance(items[0], Mapping) else None
    motion = item.get("motion_policy") if item is not None else None
    if (
        item is None
        or item.get("policy_version") != AI_VISUAL_POLICY_VERSION
        or not isinstance(motion, Mapping)
        or motion.get("maximum_static_presentation_ms") != 14_000
        or motion.get("maximum_ai_video_presentation_ms") != 8_000
        or motion.get("maximum_ai_image_asset_exposure_ms") != 60_000
    ):
        raise ValidationFailureError("V2_AI_VISUAL_MOTION_POLICY_INVALID")
    return motion


def compile_ai_visual_stage_planning(
    *,
    visual_run: AIVisualProductionRun,
    timeline: Mapping[str, Any],
    provider_readiness_ref: str,
    budget_authority_ref: str,
    maximum_image_submissions: int,
    maximum_video_submissions: int,
) -> AIVisualStagePlanningArtifacts:
    if (
        maximum_image_submissions < 0
        or maximum_video_submissions < 0
        or maximum_image_submissions + maximum_video_submissions <= 0
    ):
        raise ValidationFailureError("V2_AI_VISUAL_ROUTE_AUTHORITY_INVALID")
    style_bible = build_video_visual_style_bible(visual_run=visual_run)
    motion_policy = _active_motion_policy()
    # The active planning policy bounds a single AI-image presentation window
    # for asset/runtime reasons.  It does not turn duration into authority for
    # motion or make an otherwise authored stable hold semantically invalid.
    policy = AIVisualPlanningPolicy.production_default(
        maximum_ai_image_presentation_ms=int(
            motion_policy["maximum_static_presentation_ms"]
        ),
        maximum_ai_video_presentation_ms=int(
            motion_policy["maximum_ai_video_presentation_ms"]
        ),
        maximum_ai_image_asset_exposure_ms=int(
            motion_policy["maximum_ai_image_asset_exposure_ms"]
        ),
        function_maximum_duration_ms={
            "ACTION": 7_000,
            "PROCESS": 12_000,
            "COMPARISON": 13_250,
            "DATA": 12_000,
            "INTERFACE": 10_000,
            "CONCEPT_MODEL": 13_250,
            "EXAMPLE_CONTEXT": 13_250,
            "TRANSITION_HERO": 8_000,
        },
    )
    capability = AIVisualCapabilityProjection.build(
        ai_image_production_ready=maximum_image_submissions > 0,
        ai_video_production_ready=maximum_video_submissions > 0,
        ai_video_budget_authorized=maximum_video_submissions > 0,
        maximum_ai_image_assets=maximum_image_submissions,
        maximum_ai_video_scenes=maximum_video_submissions,
        provider_readiness_ref=provider_readiness_ref,
        budget_authority_ref=budget_authority_ref,
    )
    narration_units, motion_classification = _narration_units_with_motion_evidence(
        timeline
    )
    canonical_duration_ms = int(timeline.get("duration_ms") or 0)
    try:
        scene_plan = UnifiedAIVisualPlanner().compile(
            style_bible=style_bible,
            narration_units=narration_units,
            capability=capability,
            policy=policy,
            canonical_duration_ms=canonical_duration_ms,
        )
    except ValueError as exc:
        if str(exc) in {
            "AI_VIDEO_MOTION_REQUIRED_AUTHORITY_UNAVAILABLE",
            "AI_VIDEO_UNIQUE_ASSET_SLOT_BUDGET_EXCEEDED",
        }:
            raise ValidationFailureError(
                "V2_AI_VISUAL_VIDEO_DURATION_AUTHORITY_INSUFFICIENT"
            ) from exc
        raise
    if (
        scene_plan.unique_ai_image_asset_slot_count > maximum_image_submissions
        or scene_plan.unique_ai_video_asset_slot_count > maximum_video_submissions
    ):
        raise ValidationFailureError("V2_AI_VISUAL_PLAN_OUTSIDE_ROUTE_AUTHORITY")
    scene_payload: dict[str, Any] = {
        "schema_version": V2_AI_VISUAL_SCENE_SET_SCHEMA,
        "stage_version": V2_AI_VISUAL_STAGE_VERSION,
        "planner_version": UnifiedAIVisualPlanner.version,
        "source_timeline_hash": visual_run.source_timeline_hash,
        "style_bible_hash": style_bible.content_hash,
        "planning_policy": policy.model_dump(mode="json"),
        "capability_projection": capability.model_dump(mode="json"),
        "motion_classification": motion_classification,
        "asset_duplication_policy": _owner_dhash_duplication_policy(),
        "compilation": scene_plan.model_dump(mode="json"),
    }
    scene_plan_hash = ai_visual_stable_hash(scene_payload)
    motion_grammar = VideoMotionGrammar.production_default(
        grammar_id=str(_stable_uuid("vcos-video-motion-grammar", visual_run.id)),
        style_bible_hash=style_bible.content_hash,
        maximum_static_presentation_ms=14_000,
    )
    image_compiler = AIImagePromptCompiler()
    image_prompts = {
        scene.scene_id: image_compiler.compile(
            scene_plan=scene,
            style_bible=style_bible,
            motion_grammar=motion_grammar,
        )
        for scene in scene_plan.scenes
        if scene.reuses_primary_asset_from_scene_id is None
        and scene.production_route == "AI_IMAGE"
    }
    video_compiler = AIVideoPromptCompiler()
    video_prompts = {
        scene.scene_id: video_compiler.compile(
            scene_plan=scene,
            style_bible=style_bible,
        )
        for scene in scene_plan.scenes
        if scene.reuses_primary_asset_from_scene_id is None
        and scene.production_route == "AI_VIDEO"
    }
    if len(image_prompts) != scene_plan.unique_ai_image_asset_slot_count:
        raise ValidationFailureError("V2_AI_VISUAL_OWNER_PROMPT_DISTRIBUTION_INVALID")
    if len(video_prompts) != scene_plan.unique_ai_video_asset_slot_count:
        raise ValidationFailureError("V2_AI_VISUAL_VIDEO_PROMPT_DISTRIBUTION_INVALID")
    return AIVisualStagePlanningArtifacts(
        style_bible=style_bible,
        policy=policy,
        capability=capability,
        scene_plan=scene_plan,
        scene_plan_payload=scene_payload,
        scene_plan_hash=scene_plan_hash,
        motion_grammar=motion_grammar,
        motion_classification=motion_classification,
        image_prompts_by_scene_id=image_prompts,
        video_prompts_by_scene_id=video_prompts,
    )


def compile_pre_tts_ai_visual_cost_preflight(
    *,
    video_project_id: uuid.UUID,
    preflight_id: uuid.UUID,
    canonical_narration: str,
    estimated_duration_ms: int,
    maximum_image_submissions: int,
    maximum_video_submissions: int,
) -> tuple[AIVisualStagePlanningArtifacts, dict[str, Any]]:
    """Compile the normal-production cost ceiling with the production planner.

    MEDIA has not produced forced-alignment timing yet, so this deliberately
    uses deterministic *pre-TTS* sentence timing.  It is not a second visual
    planner: it feeds those provisional bindings through
    :func:`compile_ai_visual_stage_planning` and freezes the resulting
    ``AIVisualPlanCompilation``.  The post-MEDIA plan must subsequently fit
    inside this owner-slot and cost authority before VISUAL can execute.

    One preflight unit is emitted for every canonical sentence.  The eventual
    narration-unit compiler may coalesce sentences, but cannot introduce text
    outside this canonical input; this keeps the preflight conservative when
    sections and visual owner slots differ.
    """

    text = canonical_narration.strip()
    if not text or estimated_duration_ms <= 0:
        raise ValidationFailureError("COMBINED_REPLACEMENT_VISUAL_PLAN_INVALID")
    sentences = [
        match.group(0).strip()
        for match in re.finditer(r"[^.!?]+(?:[.!?]+|$)", text, flags=re.S)
        if match.group(0).strip()
    ]
    if not sentences:
        raise ValidationFailureError("COMBINED_REPLACEMENT_VISUAL_PLAN_INVALID")
    word_counts = [
        max(1, len(re.findall(r"\\b[\\w'-]+\\b", item))) for item in sentences
    ]
    total_words = sum(word_counts)
    cursor = 0
    raw_units: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for ordinal, (sentence, word_count) in enumerate(
        zip(sentences, word_counts), start=1
    ):
        duration = max(1, round(estimated_duration_ms * word_count / total_words))
        end = (
            estimated_duration_ms
            if ordinal == len(sentences)
            else min(estimated_duration_ms, cursor + duration)
        )
        if end <= cursor:
            end = cursor + 1
        unit_id = f"preflight-nu-{ordinal:03d}"
        visual_function = (
            "PROCESS"
            if any(
                pattern.search(sentence) for _cue_id, pattern in _REQUIRED_MOTION_CUES
            )
            else "CONCEPT_MODEL"
        )
        raw_units.append(
            {
                "narration_unit_id": unit_id,
                "information_unit_ids": [f"preflight-information-{ordinal:03d}"],
                "visual_function": visual_function,
                "semantic_intent": sentence,
                "text": sentence,
                "factual_risk": "MEDIUM",
                "importance": "CORE" if ordinal == 1 else "SUPPORTING",
            }
        )
        bindings.append(
            {
                "narration_unit_id": unit_id,
                "actual_start_ms": cursor,
                "actual_end_ms": end,
            }
        )
        cursor = end
    timeline = {
        "schema_version": "vcos.pre-tts-ai-visual-timeline.v1",
        "duration_ms": max(cursor, estimated_duration_ms),
        "narration_unit_compilation": {"narration_units": raw_units},
        "timed_narration_unit_bindings": {"bindings": bindings},
    }
    timeline_hash = content_hash(timeline)
    artifacts = compile_ai_visual_stage_planning(
        visual_run=SimpleNamespace(
            id=preflight_id,
            video_project_id=video_project_id,
            # A preflight has no package artifact yet.  The planner consumes
            # this only as a stable style-bible namespace.
            production_package_artifact_version_id=preflight_id,
            source_timeline_hash=timeline_hash,
        ),
        timeline=timeline,
        provider_readiness_ref=(
            f"ai-visual-preflight/{preflight_id}/google-gemini-image+google-veo"
        ),
        budget_authority_ref=f"ai-visual-preflight/{preflight_id}/budget",
        maximum_image_submissions=maximum_image_submissions,
        maximum_video_submissions=maximum_video_submissions,
    )
    return artifacts, {**timeline, "content_hash": timeline_hash}


class V2AIVisualProductionAdapter(V2LocalNativeProductionAdapter):
    """Durable V2 VISUAL adapter; Gemini generates, FFmpeg only presents."""

    descriptor = V2ProductionAdapterDescriptor(
        adapter_key=V2_AI_VISUAL_PRODUCTION_ADAPTER_KEY,
        supported_stages=frozenset({ProductionWorkflowStage.VISUAL}),
        production_eligible=True,
        fixture_only=False,
        invokes_mr1=False,
        paid_provider_calls=True,
        automatic_publish=False,
    )

    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        session_factory: Callable[[], Session] | None = None,
        image_service_factory: Callable[[], AIVisualStageImageService] | None = None,
        video_service_factory: Callable[[], AIVisualStageVideoService] | None = None,
    ) -> None:
        super().__init__(
            workspace_root=workspace_root,
            session_factory=session_factory,
        )
        self._image_service_factory = image_service_factory
        self._video_service_factory = video_service_factory
        self._price_catalog = GoogleGeminiImageModelPriceCatalog()
        self._video_price_catalog = GoogleVeoModelPriceCatalog()

    def _validate_operation(
        self,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> None:
        provider = operation.parameters.get("provider_execution")
        if (
            context.run.production_lane != "LONG_FORM"
            or context.run.planning_source_type != "LONG_FORM_PLAN"
            or operation.stage != ProductionWorkflowStage.VISUAL
            or operation.adapter_key != V2_AI_VISUAL_PRODUCTION_ADAPTER_KEY
            or operation.execution_mode != "REAL_LONG_FORM_PRODUCTION"
            or operation.paid_provider_call is not True
            or operation.max_cost_usd <= 0
            or operation.parameters.get("mode") != "AI_ONLY_PRIMARY_VISUAL_GENERATION"
            or not isinstance(provider, Mapping)
            or provider.get("image_provider") != V2_AI_VISUAL_PROVIDER_KEY
            or provider.get("video_provider") != V2_AI_VISUAL_VIDEO_PROVIDER_KEY
            or set(provider.get("active_primary_visual_routes") or ())
            != {"AI_IMAGE", "AI_VIDEO"}
            or provider.get("attempt_limit_per_asset_slot") != 1
            or provider.get("automatic_provider_retry") is not False
            or provider.get("fallback_allowed") is not False
            or provider.get("native_fallback_allowed") is not False
            or provider.get("stock_fallback_allowed") is not False
            or provider.get("screenshot_fallback_allowed") is not False
        ):
            raise ValidationFailureError("V2_AI_VISUAL_OPERATION_NOT_AUTHORIZED")

    def _execute_stage(
        self,
        *,
        ledger_id: uuid.UUID,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> tuple[WorkflowStageResult, dict[str, Any]]:
        if operation.stage != ProductionWorkflowStage.VISUAL:
            raise ValidationFailureError("V2_AI_VISUAL_STAGE_INVALID")
        return self._produce_visual_assets(
            ledger_id=ledger_id,
            context=context,
            operation=operation,
        )

    def _produce_visual_assets(
        self,
        *,
        ledger_id: uuid.UUID,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> tuple[WorkflowStageResult, dict[str, Any]]:
        del ledger_id
        scope = self._load_scope(context=context, operation=operation)
        completed = self._completed_result(scope.visual_run.id)
        if completed is not None:
            return completed
        refs = self._artifact_refs(scope.visual_run.id)
        artifacts = compile_ai_visual_stage_planning(
            visual_run=scope.visual_run,
            timeline=scope.timeline,
            provider_readiness_ref=(
                f"ai-visual-readiness/{scope.visual_run.id}/google-gemini-image"
            ),
            budget_authority_ref=scope.visual_run.budget_reservation_ref,
            maximum_image_submissions=scope.maximum_image_submissions,
            maximum_video_submissions=scope.maximum_video_submissions,
        )
        image_asset_count = artifacts.scene_plan.unique_ai_image_asset_slot_count
        video_asset_count = artifacts.scene_plan.unique_ai_video_asset_slot_count
        conservative_cost = _mixed_conservative_cost_within_authority(
            execution_kind=scope.visual_run.execution_kind,
            image_asset_count=image_asset_count,
            video_asset_count=video_asset_count,
            maximum_image_submissions=scope.maximum_image_submissions,
            maximum_video_submissions=scope.maximum_video_submissions,
            maximum_total_cost_usd=scope.maximum_total_cost_usd,
            video_unit_cost_usd=scope.video_unit_cost_usd,
        )
        if len(artifacts.scene_plan.scenes) > scope.maximum_scene_count:
            raise ValidationFailureError("V2_AI_VISUAL_SCENE_COUNT_EXCEEDS_AUTHORITY")
        self._persist_planning(scope=scope, artifacts=artifacts, refs=refs)

        if scope.budget.status == "SETTLED_CONSERVATIVE":
            self._require_settled_reconciliation_effects(
                visual_run_id=scope.visual_run.id,
                expected_count=artifacts.scene_plan.unique_asset_slot_count,
            )

        image_service = self._image_service() if image_asset_count else None
        video_service = self._video_service() if video_asset_count else None
        identities = (
            self._prepare_image_effects(
                scope=scope,
                artifacts=artifacts,
                refs=refs,
                service=image_service,
            )
            if image_service is not None
            else []
        )
        video_authorities = (
            self._prepare_video_effects(
                scope=scope,
                artifacts=artifacts,
                refs=refs,
                service=video_service,
            )
            if video_service is not None
            else []
        )
        self._mark_budget_submitted(scope)
        if scope.budget.status != "SETTLED_CONSERVATIVE":
            self._mark_generating(scope.visual_run.id)
        try:
            image_records: list[V2AIImageSceneEffectRecord] = []
            for item in identities:
                if image_service is None:
                    raise ValidationFailureError("V2_AI_VISUAL_IMAGE_SERVICE_MISSING")
                record = image_service.execute(effect_id=item.effect_id)
                if (
                    record.state != V2AIImageEffectState.VERIFIED
                    or record.asset_receipt is None
                    or record.provider_call_count != 1
                ):
                    raise ValidationFailureError(
                        "V2_AI_VISUAL_PROVIDER_EFFECT_NOT_VERIFIED"
                    )
                image_records.append(record)
            video_records = (
                self._execute_video_effects(
                    scope=scope,
                    service=video_service,
                    authorities=video_authorities,
                )
                if video_service is not None
                else []
            )
        except WorkflowStageError:
            # Durable Veo processing is not a consumed failure.  The exact
            # operation id remains recorded and the workflow retry only polls;
            # it never submits a second generation.
            raise
        except Exception:
            self._settle_consumed_failure(scope.visual_run.id)
            self._mark_blocked(
                scope.visual_run.id,
                "V2_AI_VISUAL_PROVIDER_OUTCOME_NOT_FULLY_VERIFIED",
            )
            raise
        if scope.budget.status != "SETTLED_CONSERVATIVE":
            self._settle_conservative_success(
                scope.visual_run.id,
                image_records,
                video_records,
                maximum_cost_usd=scope.maximum_total_cost_usd,
            )

        assets = self._verified_assets(image_records)
        assets.extend(self._verified_video_assets(video_records))
        projections, effect_plan = self._compile_motion(
            artifacts=artifacts,
            assets=assets,
        )
        manifest_id = _stable_uuid(
            "vcos-ai-visual-asset-manifest",
            scope.visual_run.id,
            effect_plan.effect_plan_hash,
        )
        manifest = build_ai_visual_asset_manifest(
            manifest_id=str(manifest_id),
            production_visual_policy_ref=scope.visual_run.production_visual_policy_ref,
            production_visual_policy_hash=scope.visual_run.production_visual_policy_hash,
            scene_plan_ref=refs["scene_plan"],
            scene_plan_hash=artifacts.scene_plan_hash,
            style_bible_ref=refs["style_bible"],
            style_bible_hash=artifacts.style_bible.content_hash,
            motion_grammar_ref=refs["motion_grammar"],
            motion_grammar_hash=artifacts.motion_grammar.content_hash,
            effect_plan_ref=refs["effect_plan"],
            effect_plan_hash=effect_plan.effect_plan_hash,
            assets=assets,
        )
        self._persist_completed_artifacts(
            scope=scope,
            artifacts=artifacts,
            refs=refs,
            projections=projections,
            effect_plan=effect_plan,
            manifest=manifest,
            image_records=image_records,
            video_records=video_records,
        )
        result = self._result_from_manifest(
            visual_run=scope.visual_run,
            manifest=manifest,
            manifest_id=manifest_id,
            refs=refs,
            style_bible_hash=artifacts.style_bible.content_hash,
            scene_plan_hash=artifacts.scene_plan_hash,
            motion_grammar_hash=artifacts.motion_grammar.content_hash,
            effect_plan_hash=effect_plan.effect_plan_hash,
            timeline_ref=scope.visual_run.source_timeline_ref,
            timeline_hash=scope.visual_run.source_timeline_hash,
            conservative_cost_usd=conservative_cost,
            reconciled=False,
        )
        journal = self._journal_from_result(result, reconciled=False)
        return result, journal

    def _image_service(self) -> AIVisualStageImageService:
        if self._image_service_factory is not None:
            return self._image_service_factory()
        settings = get_settings()
        client = V2GeminiImageOfficialClientFactory.build(settings)
        return V2AIImageProductionService(
            store=SQLAlchemyAIImageSceneEffectStore(self._session_factory),
            adapter=V2GeminiImageProductionAdapter(client=client),
            settings=settings,
            catalog=self._price_catalog,
            adapter_registered=True,
        )

    def _video_service(self) -> AIVisualStageVideoService:
        if self._video_service_factory is not None:
            return self._video_service_factory()
        settings = get_settings()
        secret = (
            settings.gemini_api_key.get_secret_value().strip()
            if settings.gemini_api_key is not None
            else ""
        )
        client = GoogleGenAIVeoSDKClient.from_api_key(secret)
        return V2VeoVisualProductionProvider(
            store=SQLAlchemyVeoEffectStore(
                self._session_factory,
                workspace_root=self.root,
            ),
            client=client,
            media_runtime=FFmpegV2VeoMediaRuntime(),
            workspace_root=self.root,
            catalog=self._video_price_catalog,
            settings=settings,
            adapter_registered=True,
        )

    def _load_scope(
        self,
        *,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> _StageScope:
        with self._session_factory() as session:
            workflow = session.get(ProductionWorkflowRun, context.run.id)
            if (
                workflow is None
                or workflow.ai_visual_production_run_id is None
                or workflow.production_package_artifact_version_id is None
                or workflow.production_package_hash is None
            ):
                raise ValidationFailureError("V2_AI_VISUAL_RUN_AUTHORITY_REQUIRED")
            visual_run = session.get(
                AIVisualProductionRun,
                workflow.ai_visual_production_run_id,
            )
            if (
                visual_run is None
                or visual_run.workflow_run_id != workflow.id
                or visual_run.video_project_id != workflow.video_project_id
                or visual_run.production_package_artifact_version_id
                != workflow.production_package_artifact_version_id
                or visual_run.production_package_hash
                != workflow.production_package_hash
                or visual_run.production_visual_policy_version
                != AI_VISUAL_POLICY_VERSION
                or visual_run.state
                not in {
                    "AUTHORIZED",
                    "PLANNED",
                    "GENERATING",
                    "ASSETS_VERIFIED",
                }
            ):
                raise ValidationFailureError("V2_AI_VISUAL_RUN_IDENTITY_MISMATCH")
            package = session.get(
                ArtifactVersion,
                visual_run.production_package_artifact_version_id,
            )
            if (
                package is None
                or package.content_hash != visual_run.production_package_hash
            ):
                raise ValidationFailureError("V2_AI_VISUAL_PACKAGE_AUTHORITY_DRIFT")
            rerender = (
                session.get(AIVisualRerenderAuthority, visual_run.rerender_authority_id)
                if visual_run.rerender_authority_id is not None
                else None
            )
            budget = session.get(
                MR1MonthlyBudgetReservation,
                visual_run.budget_reservation_id,
            )
            budget_ceiling = (
                Decimal(budget.reserved_amount) if budget is not None else Decimal("0")
            )
            allocations = (
                {
                    str(key): Decimal(str(value))
                    for key, value in (budget.provider_allocations_json or {}).items()
                }
                if budget is not None
                else {}
            )
            video_unit_cost = self._video_price_catalog.estimate(
                model_id=VEO_DEFAULT_MODEL_ID,
                resolution=VEO_DEFAULT_RESOLUTION,
                duration_seconds=VEO_DEFAULT_DURATION_SECONDS,
                output_count=VEO_DEFAULT_OUTPUT_COUNT,
                hard_cap=Decimal("1000000"),
                approval_amount=Decimal("1000000"),
            ).estimated_amount
            combined_authority: CombinedReplacementBudgetAuthority | None = None
            if visual_run.execution_kind == "GOVERNED_RERENDER":
                if (
                    rerender is None
                    or rerender.authorized_visual_production_run_id != visual_run.id
                    or rerender.replacement_workflow_run_id != workflow.id
                    or rerender.source_workflow_run_id == workflow.id
                    or rerender.video_project_id != visual_run.video_project_id
                    or rerender.production_package_artifact_version_id != package.id
                    or rerender.production_package_hash != package.content_hash
                    or rerender.production_visual_policy_hash
                    != visual_run.production_visual_policy_hash
                    or rerender.budget_reservation_id
                    != visual_run.budget_reservation_id
                    or rerender.budget_reservation_ref
                    != visual_run.budget_reservation_ref
                    or rerender.budget_authority_hash
                    != visual_run.budget_authority_hash
                    or rerender.maximum_tts_submissions != 0
                    or rerender.maximum_forced_alignment_submissions != 0
                    or rerender.automatic_publish
                ):
                    raise ValidationFailureError(
                        "V2_AI_VISUAL_RERENDER_AUTHORITY_DRIFT"
                    )
                source_workflow_run_id = rerender.source_workflow_run_id
                approval_ref = f"ai-visual-rerender-authorities/{rerender.id}"
                approval_hash = rerender.authority_hash
                maximum_images = rerender.maximum_image_submissions
                maximum_videos = rerender.maximum_video_submissions
                maximum_scenes = rerender.maximum_scene_count
                maximum_cost = Decimal(rerender.maximum_total_cost_usd)
                expected_budget_allocations = {
                    key: amount
                    for key, amount in {
                        V2_AI_VISUAL_PROVIDER_KEY: (
                            V2_AI_VISUAL_CONSERVATIVE_UNIT_COST_USD * maximum_images
                        ),
                        V2_AI_VISUAL_VIDEO_PROVIDER_KEY: (
                            video_unit_cost * maximum_videos
                        ),
                    }.items()
                    if amount > 0
                }
                expected_budget_run_id = visual_run.id
                expected_budget_hash = visual_run.budget_authority_hash
                expected_budget_ceiling = maximum_cost
                if operation.max_cost_usd < maximum_cost:
                    raise ValidationFailureError(
                        "V2_AI_VISUAL_RERENDER_OPERATION_COST_DRIFT"
                    )
            elif visual_run.execution_kind == "NORMAL_PRODUCTION" and rerender is None:
                combined_authority = session.scalar(
                    select(CombinedReplacementBudgetAuthority).where(
                        CombinedReplacementBudgetAuthority.video_project_id
                        == visual_run.video_project_id,
                        CombinedReplacementBudgetAuthority.budget_reservation_id
                        == visual_run.budget_reservation_id,
                    )
                )
                source_workflow_run_id = workflow.id
                approval_ref = f"production-workflows/{workflow.id}/visual"
                approval_hash = ai_visual_stable_hash(
                    {
                        "workflow_run_id": str(workflow.id),
                        "visual_production_run_id": str(visual_run.id),
                        "operation_id": operation.operation_id,
                        "input_hash": context.input_hash,
                    }
                )
                image_allocation = allocations.get(
                    V2_AI_VISUAL_PROVIDER_KEY, Decimal("0")
                )
                video_allocation = allocations.get(
                    V2_AI_VISUAL_VIDEO_PROVIDER_KEY, Decimal("0")
                )
                maximum_images = int(
                    image_allocation / V2_AI_VISUAL_CONSERVATIVE_UNIT_COST_USD
                )
                maximum_videos = int(video_allocation / video_unit_cost)
                maximum_cost = image_allocation + video_allocation
                if (
                    combined_authority is None
                    or combined_authority.state != "FROZEN"
                    or combined_authority.content_hash
                    != visual_run.budget_authority_hash
                    or combined_authority.budget_reservation_ref
                    != visual_run.budget_reservation_ref
                    or Decimal(combined_authority.ai_image_projected_cost_usd)
                    != image_allocation
                    or Decimal(combined_authority.ai_video_projected_cost_usd)
                    != video_allocation
                    or maximum_images * V2_AI_VISUAL_CONSERVATIVE_UNIT_COST_USD
                    != image_allocation
                    or maximum_videos * video_unit_cost != video_allocation
                    or maximum_images + maximum_videos <= 0
                    or operation.max_cost_usd < maximum_cost
                ):
                    raise ValidationFailureError(
                        "V2_AI_VISUAL_NORMAL_ROUTE_COST_AUTHORITY_DRIFT"
                    )
                maximum_scenes = 48
                expected_budget_allocations = {
                    str(key): Decimal(str(value))
                    for key, value in (
                        (
                            (combined_authority.source_refs or {}).get(
                                "ai_visual_preflight"
                            )
                            or {}
                        ).get("provider_allocations_usd")
                        or {}
                    ).items()
                }
                expected_budget_run_id = workflow.id
                expected_budget_hash = combined_authority.content_hash
                expected_budget_ceiling = Decimal(
                    combined_authority.combined_replacement_projected_cost_usd
                )
            else:
                raise ValidationFailureError("V2_AI_VISUAL_EXECUTION_KIND_INVALID")

            expected_visual_allocations = {
                key: amount
                for key, amount in {
                    V2_AI_VISUAL_PROVIDER_KEY: (
                        V2_AI_VISUAL_CONSERVATIVE_UNIT_COST_USD * maximum_images
                    ),
                    V2_AI_VISUAL_VIDEO_PROVIDER_KEY: (video_unit_cost * maximum_videos),
                }.items()
                if amount > 0
            }
            if (
                budget is None
                or budget.run_id != expected_budget_run_id
                or budget.video_project_id != visual_run.video_project_id
                or budget.reservation_ref != visual_run.budget_reservation_ref
                or visual_run.budget_authority_hash != expected_budget_hash
                or budget_ceiling <= 0
                or budget_ceiling != expected_budget_ceiling
                or {
                    key: value
                    for key, value in allocations.items()
                    if key
                    in {V2_AI_VISUAL_PROVIDER_KEY, V2_AI_VISUAL_VIDEO_PROVIDER_KEY}
                }
                != expected_visual_allocations
                or allocations != expected_budget_allocations
                or budget.status
                not in {"RESERVED", "SUBMITTED", "SETTLED_CONSERVATIVE"}
            ):
                raise ValidationFailureError("V2_AI_VISUAL_BUDGET_AUTHORITY_DRIFT")
            if budget.status == "SETTLED_CONSERVATIVE" and (
                budget.settlement_kind != "CONSERVATIVE_CATALOG_ESTIMATE_SUCCESS"
                or Decimal(budget.actual_amount or 0) <= 0
                or Decimal(budget.actual_amount or 0) > expected_budget_ceiling
            ):
                raise ValidationFailureError("V2_AI_VISUAL_BUDGET_SETTLEMENT_INVALID")

            policy = ConfigRegistryService(session).validate_catalog(
                Path(__file__).resolve().parents[2]
                / "config"
                / "production_visual_policy_catalog.yaml"
            )
            if (
                visual_run.production_visual_policy_ref != V2_AI_VISUAL_POLICY_REF
                or visual_run.production_visual_policy_hash != policy.content_hash
            ):
                raise ValidationFailureError("V2_AI_VISUAL_POLICY_AUTHORITY_DRIFT")
            media_ledger = session.scalar(
                select(V2ProductionEffectLedger).where(
                    V2ProductionEffectLedger.workflow_run_id == source_workflow_run_id,
                    V2ProductionEffectLedger.stage == "MEDIA",
                )
            )
            media_journal = (
                dict(media_ledger.effect_journal or {})
                if media_ledger is not None
                else {}
            )
            if (
                media_ledger is None
                or media_ledger.state != "VERIFIED"
                or media_ledger.video_project_id != visual_run.video_project_id
                or media_ledger.production_package_artifact_version_id != package.id
                or media_ledger.production_package_hash != package.content_hash
                or media_ledger.result_type
                not in {
                    "V2_ELEVENLABS_CANONICAL_MEDIA_TIMELINE",
                    "V2_CANONICAL_MEDIA_TIMELINE",
                }
                or media_ledger.result_ref != visual_run.source_timeline_ref
                or media_ledger.result_hash != visual_run.source_timeline_hash
                or workflow.canonical_media_timeline_ref
                != visual_run.source_timeline_ref
                or workflow.canonical_media_timeline_hash
                != visual_run.source_timeline_hash
                or media_journal.get("timeline_hash") != visual_run.source_timeline_hash
            ):
                raise ValidationFailureError(
                    "V2_AI_VISUAL_SOURCE_MEDIA_LEDGER_AUTHORITY_DRIFT"
                )
            timeline_relative_path = media_journal.get("timeline_relative_path")
            if not isinstance(timeline_relative_path, str):
                raise ValidationFailureError(
                    "V2_AI_VISUAL_SOURCE_TIMELINE_PATH_REQUIRED"
                )
            timeline_path = self._from_root_relative(timeline_relative_path)
            audio_path = self._from_root_relative(visual_run.audio_ref)
            caption_path = self._from_root_relative(visual_run.caption_ref)
            if (
                media_journal.get("timeline_file_checksum")
                != _sha256_file(timeline_path)
                or _sha256_file(audio_path) != visual_run.audio_checksum
                or _sha256_file(caption_path) != visual_run.caption_checksum
            ):
                raise ValidationFailureError(
                    "V2_AI_VISUAL_SOURCE_MEDIA_BYTES_AUTHORITY_DRIFT"
                )
            timeline = self._load_hashed_timeline(
                timeline_path,
                visual_run.source_timeline_hash,
            )
            self._validate_timeline(
                timeline=timeline,
                visual_run=visual_run,
                source_workflow_run_id=source_workflow_run_id,
                media_journal=media_journal,
            )
            return _StageScope(
                workflow=workflow,
                visual_run=visual_run,
                rerender_authority=rerender,
                source_workflow_run_id=source_workflow_run_id,
                package=package,
                budget=budget,
                timeline=timeline,
                timeline_path=timeline_path,
                media_journal=media_journal,
                policy_hash=policy.content_hash,
                approval_ref=approval_ref,
                approval_hash=approval_hash,
                maximum_image_submissions=maximum_images,
                maximum_video_submissions=maximum_videos,
                maximum_scene_count=maximum_scenes,
                maximum_total_cost_usd=maximum_cost,
                provider_allocations_usd=allocations,
                video_unit_cost_usd=(video_unit_cost if maximum_videos else None),
            )

    def _from_root_relative(self, value: str) -> Path:
        raw = Path(value)
        if raw.is_absolute() or ".." in raw.parts or not raw.parts:
            raise ValidationFailureError("V2_AI_VISUAL_ROOT_RELATIVE_REF_REQUIRED")
        resolved = (self.root / raw).resolve(strict=True)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValidationFailureError("V2_AI_VISUAL_SOURCE_PATH_ESCAPE") from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise ValidationFailureError("V2_AI_VISUAL_SOURCE_FILE_INVALID")
        return resolved

    @staticmethod
    def _load_hashed_timeline(path: Path, expected_hash: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationFailureError(
                "V2_AI_VISUAL_SOURCE_TIMELINE_INVALID"
            ) from exc
        if not isinstance(value, dict) or content_hash(value) != expected_hash:
            raise ValidationFailureError("V2_AI_VISUAL_SOURCE_TIMELINE_HASH_MISMATCH")
        return value

    @staticmethod
    def _validate_timeline(
        *,
        timeline: Mapping[str, Any],
        visual_run: AIVisualProductionRun,
        source_workflow_run_id: uuid.UUID,
        media_journal: Mapping[str, Any],
    ) -> None:
        exact = (
            str(timeline.get("workflow_run_id")) == str(source_workflow_run_id)
            and str(timeline.get("video_project_id"))
            == str(visual_run.video_project_id)
            and str(timeline.get("production_package_artifact_version_id"))
            == str(visual_run.production_package_artifact_version_id)
            and timeline.get("production_package_hash")
            == visual_run.production_package_hash
            and timeline.get("timeline_ref") == visual_run.source_timeline_ref
            and timeline.get("audio_asset_ref") == media_journal.get("audio_asset_ref")
            and timeline.get("audio_checksum") == visual_run.audio_checksum
            and int(timeline.get("duration_ms") or 0) == visual_run.audio_duration_ms
            and timeline.get("timed_words_ref") == visual_run.timed_words_ref
            and timeline.get("caption_ref") == media_journal.get("caption_ref")
            and timeline.get("caption_artifact_hash")
            == media_journal.get("caption_artifact_hash")
            and timeline.get("caption_checksum") == visual_run.caption_checksum
            and timeline.get("subtitle_qc_ref") == visual_run.subtitle_qc_ref
            and timeline.get("subtitle_qc_hash") == visual_run.subtitle_qc_hash
            and timeline.get("subtitle_qc_state") == "PASS"
            and media_journal.get("audio_relative_path") == visual_run.audio_ref
            and media_journal.get("audio_checksum") == visual_run.audio_checksum
            and media_journal.get("caption_relative_path") == visual_run.caption_ref
            and media_journal.get("caption_artifact_hash") == visual_run.caption_hash
            and media_journal.get("caption_checksum") == visual_run.caption_checksum
            and media_journal.get("timed_words_ref") == visual_run.timed_words_ref
            and media_journal.get("timed_words_hash") == visual_run.timed_words_hash
            and media_journal.get("subtitle_qc_ref") == visual_run.subtitle_qc_ref
            and media_journal.get("subtitle_qc_hash") == visual_run.subtitle_qc_hash
            and media_journal.get("subtitle_qc_state") == "PASS"
        )
        if not exact:
            raise ValidationFailureError("V2_AI_VISUAL_SOURCE_TIMELINE_AUTHORITY_DRIFT")

    def _artifact_refs(self, visual_run_id: uuid.UUID) -> dict[str, str]:
        prefix = f"ai-visual-runs/{visual_run_id}"
        return {
            "style_bible": f"{prefix}/style-bible.json",
            "scene_plan": f"{prefix}/scene-plan.json",
            "asset_manifest": f"{prefix}/asset-manifest.json",
            "motion_grammar": f"{prefix}/video-motion-grammar.json",
            "effect_plan": f"{prefix}/ffmpeg-effect-plan.json",
        }

    def _run_dir(self, visual_run_id: uuid.UUID) -> Path:
        path = self.root / "ai-visual-runs" / str(visual_run_id)
        path.mkdir(parents=True, exist_ok=True)
        if (
            path.is_symlink()
            or path.resolve().parent != (self.root / "ai-visual-runs").resolve()
        ):
            raise ValidationFailureError("V2_AI_VISUAL_RUN_DIRECTORY_INVALID")
        return path.resolve()

    def _persist_planning(
        self,
        *,
        scope: _StageScope,
        artifacts: AIVisualStagePlanningArtifacts,
        refs: Mapping[str, str],
    ) -> None:
        run_dir = self._run_dir(scope.visual_run.id)
        _persist_exact_json(
            self.root / refs["style_bible"],
            artifacts.style_bible.model_dump(mode="json"),
        )
        _persist_exact_json(
            self.root / refs["scene_plan"], artifacts.scene_plan_payload
        )
        _persist_exact_json(
            self.root / refs["motion_grammar"],
            artifacts.motion_grammar.model_dump(mode="json"),
        )
        prompt_dir = run_dir / "compiled-prompts"
        for scene_id, prompt in artifacts.image_prompts_by_scene_id.items():
            _persist_exact_json(
                prompt_dir / f"{scene_id}.json", prompt.model_dump(mode="json")
            )
        for scene_id, prompt in artifacts.video_prompts_by_scene_id.items():
            _persist_exact_json(
                prompt_dir / f"{scene_id}.json", prompt.model_dump(mode="json")
            )
        style_id = uuid.UUID(artifacts.style_bible.style_bible_id)
        snapshot_id = _stable_uuid(
            "vcos-ai-visual-scene-plan-snapshot",
            scope.visual_run.id,
            artifacts.scene_plan_hash,
        )
        with self._session_factory() as session:
            visual_run = session.execute(
                select(AIVisualProductionRun)
                .where(AIVisualProductionRun.id == scope.visual_run.id)
                .with_for_update()
            ).scalar_one()
            style = session.scalar(
                select(AIVisualStyleBible).where(
                    AIVisualStyleBible.visual_production_run_id == visual_run.id
                )
            )
            style_content = artifacts.style_bible.model_dump(mode="json")
            if style is None:
                if visual_run.state != "AUTHORIZED":
                    raise ValidationFailureError(
                        "V2_AI_VISUAL_STYLE_BIBLE_REPLAY_MISSING"
                    )
                style = AIVisualStyleBible(
                    id=style_id,
                    visual_production_run_id=visual_run.id,
                    schema_version=artifacts.style_bible.schema_version,
                    content=style_content,
                    content_hash=artifacts.style_bible.content_hash,
                )
                session.add(style)
                session.flush()
            elif (
                style.id != style_id
                or style.content_hash != artifacts.style_bible.content_hash
                or style.content != style_content
            ):
                raise ValidationFailureError("V2_AI_VISUAL_STYLE_BIBLE_CONFLICT")
            snapshot = session.scalar(
                select(AIVisualScenePlanSnapshot).where(
                    AIVisualScenePlanSnapshot.visual_production_run_id == visual_run.id
                )
            )
            if snapshot is None:
                if visual_run.state != "AUTHORIZED":
                    raise ValidationFailureError(
                        "V2_AI_VISUAL_SCENE_PLAN_REPLAY_MISSING"
                    )
                snapshot = AIVisualScenePlanSnapshot(
                    id=snapshot_id,
                    visual_production_run_id=visual_run.id,
                    style_bible_id=style.id,
                    style_bible_hash=style.content_hash,
                    schema_version=V2_AI_VISUAL_SCENE_SET_SCHEMA,
                    scene_count=len(artifacts.scene_plan.scenes),
                    ai_image_scene_count=artifacts.scene_plan.ai_image_scene_count,
                    ai_video_scene_count=artifacts.scene_plan.ai_video_scene_count,
                    unique_asset_slot_count=artifacts.scene_plan.unique_asset_slot_count,
                    content=artifacts.scene_plan_payload,
                    content_hash=artifacts.scene_plan_hash,
                )
                session.add(snapshot)
                session.flush()
            elif (
                snapshot.id != snapshot_id
                or snapshot.style_bible_id != style.id
                or snapshot.content_hash != artifacts.scene_plan_hash
                or snapshot.content != artifacts.scene_plan_payload
            ):
                raise ValidationFailureError("V2_AI_VISUAL_SCENE_PLAN_CONFLICT")
            _apply_exact_planning_projection(
                visual_run,
                style_bible_id=style.id,
                style_bible_hash=style.content_hash,
                scene_plan_id=snapshot.id,
                scene_plan_hash=snapshot.content_hash,
                motion_grammar_ref=refs["motion_grammar"],
                motion_grammar_hash=artifacts.motion_grammar.content_hash,
            )
            session.commit()

    def _prepare_image_effects(
        self,
        *,
        scope: _StageScope,
        artifacts: AIVisualStagePlanningArtifacts,
        refs: Mapping[str, str],
        service: AIVisualStageImageService,
    ) -> list[V2AIImageSceneEffectIdentity]:
        with self._session_factory() as session:
            visual_run = session.get(AIVisualProductionRun, scope.visual_run.id)
            if visual_run is None or visual_run.scene_plan_id is None:
                raise ValidationFailureError("V2_AI_VISUAL_SCENE_SNAPSHOT_REQUIRED")
            scene_snapshot_id = visual_run.scene_plan_id
        if service.catalog_hash != ai_image_stable_hash(self._price_catalog.payload):
            raise ValidationFailureError("V2_AI_VISUAL_IMAGE_CATALOG_DRIFT")
        scenes_by_owner: dict[str, list[Any]] = {}
        for scene in artifacts.scene_plan.scenes:
            owner_id = scene.reuses_primary_asset_from_scene_id or scene.scene_id
            scenes_by_owner.setdefault(owner_id, []).append(scene)
        owners = [
            scene
            for scene in artifacts.scene_plan.scenes
            if scene.reuses_primary_asset_from_scene_id is None
            and scene.production_route == "AI_IMAGE"
        ]
        identities: list[V2AIImageSceneEffectIdentity] = []
        unit_cost = V2_AI_VISUAL_CONSERVATIVE_UNIT_COST_USD
        for owner in owners:
            prompt = artifacts.image_prompts_by_scene_id[owner.scene_id]
            estimate = self._price_catalog.estimate(
                model_id=GEMINI_IMAGE_DEFAULT_MODEL_ID,
                image_size=GEMINI_IMAGE_DEFAULT_SIZE,
                aspect_ratio=GEMINI_IMAGE_DEFAULT_ASPECT_RATIO,
                output_count=1,
                attempt_count=1,
                hard_cap=unit_cost,
                approval_amount=unit_cost,
            )
            cost_estimate_payload = {
                "schema_version": "vcos.ai-visual-slot-cost-estimate.v1",
                "catalog_image_estimate": estimate.model_dump(mode="json"),
                "catalog_image_cost_usd": format(estimate.estimated_amount, "f"),
                "same_interaction_semantic_token_allowance_usd": format(
                    unit_cost - estimate.estimated_amount,
                    "f",
                ),
                "conservative_total_cost_usd": format(unit_cost, "f"),
                "actual_cost_usd": None,
                "settlement_basis": "CONSERVATIVE_CATALOG_ESTIMATE_SUCCESS",
            }
            cost_estimate_hash = ai_image_stable_hash(cost_estimate_payload)
            effect_id = _stable_uuid(
                "vcos-ai-visual-image-effect",
                scope.visual_run.id,
                owner.primary_asset_slot_id,
            )
            effect_dir = self._run_dir(scope.visual_run.id) / "effects" / str(effect_id)
            effect_dir.mkdir(parents=True, exist_ok=True)
            identity = V2AIImageSceneEffectIdentity.from_visual_contracts(
                style_bible=artifacts.style_bible,
                scene_plan=owner,
                compiled_prompt=prompt,
                bound_scene_plans=tuple(scenes_by_owner[owner.scene_id]),
                effect_id=str(effect_id),
                visual_production_run_id=str(scope.visual_run.id),
                scene_plan_snapshot_id=str(scene_snapshot_id),
                workflow_run_id=str(scope.workflow.id),
                video_project_id=str(scope.visual_run.video_project_id),
                production_package_artifact_version_id=str(scope.package.id),
                production_package_hash=scope.package.content_hash,
                production_visual_policy_hash=scope.visual_run.production_visual_policy_hash,
                price_catalog_version=self._price_catalog.version,
                price_catalog_ref=self._price_catalog.ref,
                price_catalog_hash=service.catalog_hash,
                approval_ref=scope.approval_ref,
                approval_hash=scope.approval_hash,
                budget_reservation_id=str(scope.visual_run.budget_reservation_id),
                budget_authority_ref=scope.visual_run.budget_reservation_ref,
                budget_authority_hash=scope.visual_run.budget_authority_hash,
                cost_estimate_ref=(
                    f"ai-visual-runs/{scope.visual_run.id}/cost-estimates/"
                    f"{owner.primary_asset_slot_id}.json"
                ),
                cost_estimate_hash=cost_estimate_hash,
                estimated_cost_usd=unit_cost,
                maximum_cost_usd=unit_cost,
                idempotency_key=f"ai-visual:{effect_id}",
                workspace_root=str(self.root),
                request_journal_path=str(effect_dir / "request-journal.json"),
                response_capture_path=str(effect_dir / "response-capture.jpg"),
                response_capture_journal_path=str(
                    effect_dir / "response-capture-journal.json"
                ),
                destination_path=str(effect_dir / "verified-primary.jpg"),
            )
            _persist_exact_json(
                self._run_dir(scope.visual_run.id)
                / "cost-estimates"
                / f"{owner.primary_asset_slot_id}.json",
                cost_estimate_payload,
            )
            record = service.prepare(identity)
            if record.identity != identity:
                raise ValidationFailureError(
                    "V2_AI_VISUAL_PREPARED_EFFECT_IDENTITY_DRIFT"
                )
            if record.state != V2AIImageEffectState.VERIFIED:
                readiness = service.readiness_projection(identity)
                if not readiness.execution_ready:
                    blockers = list(readiness.blocker_codes)
                    raise ValidationFailureError(
                        blockers[0] if blockers else "V2_AI_VISUAL_PROVIDER_NOT_READY"
                    )
            identities.append(identity)
        if len(identities) != artifacts.scene_plan.unique_ai_image_asset_slot_count:
            raise ValidationFailureError("V2_AI_VISUAL_EFFECT_DISTRIBUTION_INVALID")
        return identities

    def _prepare_video_effects(
        self,
        *,
        scope: _StageScope,
        artifacts: AIVisualStagePlanningArtifacts,
        refs: Mapping[str, str],
        service: AIVisualStageVideoService,
    ) -> list[V2VeoGenerationAuthority]:
        with self._session_factory() as session:
            visual_run = session.get(AIVisualProductionRun, scope.visual_run.id)
            if (
                visual_run is None
                or visual_run.scene_plan_id is None
                or visual_run.style_bible_id is None
            ):
                raise ValidationFailureError("V2_AI_VISUAL_SCENE_SNAPSHOT_REQUIRED")
            scene_snapshot_id = visual_run.scene_plan_id
            style_bible_id = visual_run.style_bible_id
        catalog_hash = veo_stable_hash(service.catalog.payload)
        if catalog_hash != veo_stable_hash(self._video_price_catalog.payload):
            raise ValidationFailureError("V2_AI_VISUAL_VIDEO_CATALOG_DRIFT")
        scenes_by_owner: dict[str, list[Any]] = {}
        for scene in artifacts.scene_plan.scenes:
            owner_id = scene.reuses_primary_asset_from_scene_id or scene.scene_id
            scenes_by_owner.setdefault(owner_id, []).append(scene)
        owners = [
            scene
            for scene in artifacts.scene_plan.scenes
            if scene.reuses_primary_asset_from_scene_id is None
            and scene.production_route == "AI_VIDEO"
        ]
        authorities: list[V2VeoGenerationAuthority] = []
        settings = get_settings()
        for owner in owners:
            prompt = artifacts.video_prompts_by_scene_id[owner.scene_id]
            estimate = self._video_price_catalog.estimate(
                model_id=VEO_DEFAULT_MODEL_ID,
                resolution=VEO_DEFAULT_RESOLUTION,
                duration_seconds=VEO_DEFAULT_DURATION_SECONDS,
                output_count=VEO_DEFAULT_OUTPUT_COUNT,
                hard_cap=scope.video_unit_cost_usd or Decimal("0"),
                approval_amount=scope.video_unit_cost_usd or Decimal("0"),
            )
            if (
                scope.video_unit_cost_usd is None
                or estimate.estimated_amount != scope.video_unit_cost_usd
            ):
                raise ValidationFailureError("V2_AI_VISUAL_VIDEO_COST_AUTHORITY_DRIFT")
            provider_config = build_v2_veo_provider_config_payload(
                provider_config_version=V2_AI_VISUAL_VEO_PROVIDER_CONFIG_VERSION,
                model_id=VEO_DEFAULT_MODEL_ID,
                duration_seconds=VEO_DEFAULT_DURATION_SECONDS,
                resolution=VEO_DEFAULT_RESOLUTION,
                fps=24,
                aspect_ratio=VEO_DEFAULT_ASPECT_RATIO,
                output_count=VEO_DEFAULT_OUTPUT_COUNT,
            )
            cost_payload = {
                "schema_version": "vcos.ai-visual-video-slot-cost-estimate.v1",
                "catalog_estimate": estimate.model_dump(mode="json"),
                "conservative_total_cost_usd": format(estimate.estimated_amount, "f"),
                "actual_cost_usd": None,
                "settlement_basis": "CONSERVATIVE_CATALOG_ESTIMATE_SUCCESS",
            }
            cost_hash = veo_stable_hash(cost_payload)
            effect_id = _stable_uuid(
                "vcos-ai-visual-video-effect",
                scope.visual_run.id,
                owner.primary_asset_slot_id,
            )
            cost_ref = (
                f"ai-visual-runs/{scope.visual_run.id}/cost-estimates/"
                f"{owner.primary_asset_slot_id}.json"
            )
            _persist_exact_json(self.root / cost_ref, cost_payload)
            authority = V2VeoGenerationAuthority.from_compiled_visual_authority(
                scene_plan=owner,
                compiled_prompt=prompt,
                bound_scene_plans=tuple(scenes_by_owner[owner.scene_id]),
                asset_effect_id=str(effect_id),
                replacement_authority_id=scope.approval_ref,
                replacement_authority_hash=scope.approval_hash,
                visual_production_run_id=str(scope.visual_run.id),
                scene_plan_snapshot_id=str(scene_snapshot_id),
                style_bible_id=str(style_bible_id),
                workflow_run_id=str(scope.workflow.id),
                project_id=str(scope.visual_run.video_project_id),
                production_package_artifact_version_id=str(scope.package.id),
                production_package_hash=scope.package.content_hash,
                production_visual_policy_version=AI_VISUAL_POLICY_VERSION,
                production_visual_policy_ref=scope.visual_run.production_visual_policy_ref,
                production_visual_policy_hash=scope.visual_run.production_visual_policy_hash,
                model_id=VEO_DEFAULT_MODEL_ID,
                provider_config_version=V2_AI_VISUAL_VEO_PROVIDER_CONFIG_VERSION,
                provider_config_hash=veo_stable_hash(provider_config),
                catalog_version=self._video_price_catalog.version,
                catalog_ref=self._video_price_catalog.ref,
                catalog_hash=catalog_hash,
                style_bible_ref=refs["style_bible"],
                scene_plan_ref=refs["scene_plan"],
                idempotency_key=f"ai-visual-veo:{effect_id}",
                budget_reservation_id=str(scope.visual_run.budget_reservation_id),
                budget_reservation_ref=scope.visual_run.budget_reservation_ref,
                budget_authority_hash=scope.visual_run.budget_authority_hash,
                cost_estimate_ref=cost_ref,
                cost_estimate_hash=cost_hash,
                approval_ref=scope.approval_ref,
                approval_hash=scope.approval_hash,
                estimated_cost_usd=estimate.estimated_amount,
                maximum_approved_cost_usd=estimate.estimated_amount,
                resolution=VEO_DEFAULT_RESOLUTION,
                fps=24,
                output_count=VEO_DEFAULT_OUTPUT_COUNT,
            )
            record = service.prepare(authority)
            if record.state == "PREPARED":
                execution = self._video_execution_authorization(
                    scope=scope,
                    settings=settings,
                    paid_attempt_available=True,
                )
                readiness = service.readiness(
                    authority=authority,
                    execution=execution,
                )
                if not readiness.ready:
                    blockers = list(readiness.blocker_reason_codes)
                    raise ValidationFailureError(
                        blockers[0]
                        if blockers
                        else "V2_AI_VISUAL_VIDEO_PROVIDER_NOT_READY"
                    )
            authorities.append(authority)
        if len(authorities) != artifacts.scene_plan.unique_ai_video_asset_slot_count:
            raise ValidationFailureError(
                "V2_AI_VISUAL_VIDEO_EFFECT_DISTRIBUTION_INVALID"
            )
        return authorities

    @staticmethod
    def _video_execution_authorization(
        *,
        scope: _StageScope,
        settings: Any,
        paid_attempt_available: bool,
    ) -> V2VeoExecutionAuthorization:
        credential = bool(
            settings.gemini_api_key
            and settings.gemini_api_key.get_secret_value().strip()
        )
        return V2VeoExecutionAuthorization(
            provider_boundary_gate_passed=True,
            provider_real_execution_enabled=bool(
                settings.provider_real_execution_enabled
            ),
            provider_production_execution_enabled=bool(
                settings.provider_production_execution_enabled
            ),
            veo_real_generation_enabled=bool(settings.veo_real_generation_enabled),
            credential_configured=credential,
            budget_reservation_active=scope.budget.status in {"RESERVED", "SUBMITTED"},
            cost_approval_active=scope.maximum_total_cost_usd > 0,
            paid_attempt_available=paid_attempt_available,
            replacement_authority_active=True,
        )

    def _mark_budget_submitted(self, scope: _StageScope) -> None:
        with self._session_factory() as session:
            budget = session.execute(
                select(MR1MonthlyBudgetReservation)
                .where(
                    MR1MonthlyBudgetReservation.id
                    == scope.visual_run.budget_reservation_id
                )
                .with_for_update()
            ).scalar_one()
            if budget.status == "SETTLED_CONSERVATIVE":
                return
            MR1MonthlyBudgetAuthority(session).mark_submitted(budget.reservation_ref)
            session.commit()

    def _execute_video_effects(
        self,
        *,
        scope: _StageScope,
        service: AIVisualStageVideoService,
        authorities: Sequence[V2VeoGenerationAuthority],
    ) -> list[V2VeoEffectRecord]:
        settings = get_settings()
        records: dict[str, V2VeoEffectRecord] = {}
        # Submit every new owner exactly once before polling any one operation.
        # This keeps the stage bounded and ensures a processing clip cannot
        # prevent later prepared owners from receiving their authorized submit.
        for authority in authorities:
            record = service.prepare(authority)
            if record.state == "PREPARED":
                try:
                    record = service.submit_once(
                        authority=authority,
                        execution=self._video_execution_authorization(
                            scope=scope,
                            settings=settings,
                            paid_attempt_available=True,
                        ),
                    )
                except V2VeoOperationPersistenceError as exc:
                    raise WorkflowStageError(
                        classification=(
                            WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE
                        ),
                        error_code="V2_AI_VISUAL_VEO_OPERATION_ID_COMMIT_FAILED",
                        summary=(
                            "Veo accepted the generation but its operation id "
                            "requires exact operator reconciliation; no resubmit is allowed"
                        ),
                        retry_eligible=False,
                        operator_visible_blocker=(
                            "Recover exact Veo operation id "
                            f"{exc.provider_operation_id} for effect "
                            f"{authority.asset_effect_id}; never resubmit"
                        ),
                    ) from exc
            records[authority.asset_effect_id] = record

        pending = False
        for authority in authorities:
            record = records[authority.asset_effect_id]
            for poll_index in range(V2_AI_VISUAL_VEO_MAX_POLLS_PER_EVENT):
                if record.state in {"OPERATION_RECORDED", "POLLING"}:
                    record = service.poll_once(authority=authority)
                    records[authority.asset_effect_id] = record
                    if record.state == "POLLING" and (
                        poll_index + 1 < V2_AI_VISUAL_VEO_MAX_POLLS_PER_EVENT
                    ):
                        time.sleep(V2_AI_VISUAL_VEO_POLL_INTERVAL_SECONDS)
                        continue
                break
            if record.state == "POLLING":
                pending = True
                continue
            if record.state in {"RESPONSE_CAPTURED", "DOWNLOADED", "NORMALIZED"}:
                record = service.materialize(authority=authority)
                records[authority.asset_effect_id] = record
            if (
                record.state != "VERIFIED"
                or record.generation_attempt_count != 1
                or not record.production_eligible
            ):
                raise ValidationFailureError(
                    "V2_AI_VISUAL_VIDEO_PROVIDER_EFFECT_NOT_VERIFIED"
                )
        if pending:
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY,
                error_code="V2_AI_VISUAL_VEO_OPERATION_PROCESSING",
                summary="Veo operations remain processing; retry will poll exact recorded ids",
                retry_eligible=True,
            )
        return [records[item.asset_effect_id] for item in authorities]

    def _require_settled_reconciliation_effects(
        self,
        *,
        visual_run_id: uuid.UUID,
        expected_count: int,
    ) -> None:
        """A settled budget can only reconcile every existing owner effect."""

        with self._session_factory() as session:
            rows = tuple(
                session.scalars(
                    select(AIVisualAssetEffect).where(
                        AIVisualAssetEffect.visual_production_run_id == visual_run_id
                    )
                ).all()
            )
            if len(rows) != expected_count or any(
                row.state != "VERIFIED"
                or row.provider_call_count != 1
                or (row.route, row.provider_key)
                not in {
                    ("AI_IMAGE", V2_AI_VISUAL_PROVIDER_KEY),
                    ("AI_VIDEO", V2_AI_VISUAL_VIDEO_PROVIDER_KEY),
                }
                or row.output_ref is None
                or row.output_checksum is None
                or row.qc_ref is None
                or row.qc_hash is None
                for row in rows
            ):
                raise ValidationFailureError(
                    "V2_AI_VISUAL_SETTLED_BUDGET_RECONCILIATION_INCOMPLETE"
                )

    def _mark_generating(self, visual_run_id: uuid.UUID) -> None:
        with self._session_factory() as session:
            visual_run = session.execute(
                select(AIVisualProductionRun)
                .where(AIVisualProductionRun.id == visual_run_id)
                .with_for_update()
            ).scalar_one()
            if visual_run.state not in {"PLANNED", "GENERATING"}:
                raise ValidationFailureError("V2_AI_VISUAL_RUN_GENERATE_STATE_INVALID")
            if visual_run.state == "GENERATING":
                if visual_run.current_phase != "GENERATE":
                    raise ValidationFailureError(
                        "V2_AI_VISUAL_RUN_GENERATE_PHASE_INVALID"
                    )
                return
            if visual_run.current_phase != "PLAN":
                raise ValidationFailureError("V2_AI_VISUAL_RUN_PLAN_PHASE_INVALID")
            visual_run.state = "GENERATING"
            visual_run.current_phase = "GENERATE"
            visual_run.projection_version += 1
            session.commit()

    def _settle_conservative_success(
        self,
        visual_run_id: uuid.UUID,
        image_records: Sequence[V2AIImageSceneEffectRecord],
        video_records: Sequence[V2VeoEffectRecord],
        *,
        maximum_cost_usd: Decimal,
    ) -> None:
        image_conservative = sum(
            (record.identity.estimated_cost_usd for record in image_records),
            Decimal("0"),
        )
        video_conservative = sum(
            (
                record.conservative_settlement_cost_usd or Decimal("0")
                for record in video_records
            ),
            Decimal("0"),
        )
        visual_conservative = image_conservative + video_conservative
        if visual_conservative <= 0 or visual_conservative > maximum_cost_usd:
            raise ValidationFailureError("V2_AI_VISUAL_CONSERVATIVE_COST_MISMATCH")
        visual_providers = {
            key: value
            for key, value in {
                V2_AI_VISUAL_PROVIDER_KEY: image_conservative,
                V2_AI_VISUAL_VIDEO_PROVIDER_KEY: video_conservative,
            }.items()
            if value > 0
        }
        with self._session_factory() as session:
            visual_run = session.get(AIVisualProductionRun, visual_run_id)
            if visual_run is None:
                raise ValidationFailureError("V2_AI_VISUAL_RUN_NOT_FOUND")
            budget = session.get(
                MR1MonthlyBudgetReservation, visual_run.budget_reservation_id
            )
            allocations = (
                {
                    str(key): Decimal(str(value))
                    for key, value in (budget.provider_allocations_json or {}).items()
                }
                if budget is not None
                else {}
            )
            # A normal run shares the pre-TTS aggregate reservation.  The
            # already-authorized ElevenLabs partition remains conservatively
            # occupied while this method reconciles only the Gemini/Veo
            # partitions; no second visual reservation is created.
            providers = (
                {
                    **allocations,
                    **visual_providers,
                }
                if "elevenlabs" in allocations
                else visual_providers
            )
            conservative = sum(providers.values(), Decimal("0"))
            if (
                not allocations
                or set(providers) != set(allocations)
                or any(providers[key] > allocations[key] for key in providers)
            ):
                raise ValidationFailureError(
                    "V2_AI_VISUAL_COMBINED_SETTLEMENT_AUTHORITY_DRIFT"
                )
            MR1MonthlyBudgetAuthority(session).settle_conservative_success(
                visual_run.budget_reservation_ref,
                conservative_amount_usd=conservative,
                provider_conservative_amounts_usd=providers,
            )
            session.commit()

    def _settle_consumed_failure(self, visual_run_id: uuid.UUID) -> None:
        with self._session_factory() as session:
            visual_run = session.get(AIVisualProductionRun, visual_run_id)
            if visual_run is None:
                return
            budget = session.get(
                MR1MonthlyBudgetReservation,
                visual_run.budget_reservation_id,
            )
            if budget is not None and budget.status == "SUBMITTED":
                MR1MonthlyBudgetAuthority(session).settle_consumed_failure(
                    budget.reservation_ref
                )
                session.commit()

    def _mark_blocked(self, visual_run_id: uuid.UUID, reason: str) -> None:
        with self._session_factory() as session:
            visual_run = session.get(AIVisualProductionRun, visual_run_id)
            if visual_run is None or visual_run.state == "ASSETS_VERIFIED":
                return
            visual_run.state = "BLOCKED"
            visual_run.current_phase = "GENERATE"
            visual_run.failure_reason_code = reason
            visual_run.projection_version += 1
            session.commit()

    @staticmethod
    def _verified_assets(
        records: Sequence[V2AIImageSceneEffectRecord],
    ) -> list[VerifiedAIVisualAsset]:
        receipts = [record.asset_receipt for record in records]
        if any(receipt is None for receipt in receipts):
            raise ValidationFailureError("V2_AI_VISUAL_ASSET_RECEIPT_REQUIRED")
        checksums = [receipt.checksum_sha256 for receipt in receipts if receipt]
        perceptual_hashes = [
            receipt.technical_qc.perceptual_hash for receipt in receipts if receipt
        ]
        if len(set(checksums)) != len(checksums):
            raise ValidationFailureError("V2_AI_VISUAL_ASSET_DUPLICATION_GATE_FAILED")
        _require_distinct_owner_dhashes(perceptual_hashes)
        assets: list[VerifiedAIVisualAsset] = []
        for record in records:
            receipt = record.asset_receipt
            if receipt is None:
                raise ValidationFailureError("V2_AI_VISUAL_ASSET_RECEIPT_REQUIRED")
            identity = record.identity
            assets.append(
                VerifiedAIVisualAsset.build(
                    asset_slot_id=identity.primary_asset_slot_id,
                    primary_asset_owner_scene_id=identity.primary_asset_owner_scene_id,
                    bound_scene_ids=list(identity.bound_scene_ids),
                    bound_scene_plan_hashes=list(identity.bound_scene_plan_hashes),
                    route="AI_IMAGE",
                    asset_acquisition_mode="GENERATED",
                    provider_key=V2_AI_VISUAL_PROVIDER_KEY,
                    model_id=identity.model_id,
                    asset_effect_ref=f"ai-visual-asset-effects/{identity.effect_id}",
                    asset_effect_identity_hash=identity.effect_identity_hash,
                    primary_asset_ref=receipt.local_ref,
                    primary_asset_hash=receipt.checksum_sha256,
                    output_ref=receipt.local_ref,
                    output_checksum=receipt.checksum_sha256,
                    output_size_bytes=receipt.size_bytes,
                    output_content_type=receipt.content_type,
                    width=receipt.width,
                    height=receipt.height,
                    duration_ms=None,
                    fps=None,
                    qc_ref=receipt.qc_ref,
                    qc_hash=receipt.qc_hash,
                    asset_receipt_hash=receipt.receipt_hash,
                )
            )
        return assets

    def _verified_video_assets(
        self,
        records: Sequence[V2VeoEffectRecord],
    ) -> list[VerifiedAIVisualAsset]:
        assets: list[VerifiedAIVisualAsset] = []
        with self._session_factory() as session:
            for record in records:
                row = session.get(
                    AIVisualAssetEffect, uuid.UUID(record.asset_effect_id)
                )
                raw = dict(row.qc_evidence or {}) if row is not None else {}
                try:
                    technical = VeoTechnicalMotionInspectionEvidence.model_validate(
                        raw.get("technical_motion_evidence")
                    )
                except Exception as exc:
                    raise ValidationFailureError(
                        "V2_AI_VISUAL_VIDEO_TECHNICAL_EVIDENCE_REQUIRED"
                    ) from exc
                if (
                    row is None
                    or row.state != "VERIFIED"
                    or row.route != "AI_VIDEO"
                    or row.provider_key != V2_AI_VISUAL_VIDEO_PROVIDER_KEY
                    or row.provider_call_count != 1
                    or record.state != "VERIFIED"
                    or technical.asset_effect_id != str(row.id)
                    or technical.asset_effect_identity_hash != row.effect_identity_hash
                    or technical.output_ref != row.output_ref
                    or technical.output_checksum != row.output_checksum
                    or technical.qc_ref != row.qc_ref
                    or technical.qc_hash != row.qc_hash
                    or technical.actual_asset_semantic_inspection_performed
                    or technical.semantic_conformity_asserted
                    or not technical.human_semantic_review_required
                ):
                    raise ValidationFailureError(
                        "V2_AI_VISUAL_VIDEO_EFFECT_EVIDENCE_DRIFT"
                    )
                assets.append(
                    VerifiedAIVisualAsset.build(
                        asset_slot_id=row.asset_slot_id,
                        primary_asset_owner_scene_id=(row.primary_asset_owner_scene_id),
                        bound_scene_ids=list(row.bound_scene_ids),
                        bound_scene_plan_hashes=list(row.bound_scene_plan_hashes),
                        route="AI_VIDEO",
                        asset_acquisition_mode="GENERATED",
                        provider_key=V2_AI_VISUAL_VIDEO_PROVIDER_KEY,
                        model_id=row.model_id,
                        asset_effect_ref=f"ai-visual-asset-effects/{row.id}",
                        asset_effect_identity_hash=row.effect_identity_hash,
                        primary_asset_ref=str(row.output_ref),
                        primary_asset_hash=str(row.output_checksum),
                        output_ref=str(row.output_ref),
                        output_checksum=str(row.output_checksum),
                        output_size_bytes=int(row.output_size_bytes or 0),
                        output_content_type=str(row.output_content_type or ""),
                        width=int(row.output_width or 0),
                        height=int(row.output_height or 0),
                        duration_ms=int(row.output_duration_ms or 0),
                        fps=float(row.output_fps or 0),
                        qc_ref=str(row.qc_ref),
                        qc_hash=str(row.qc_hash),
                        asset_receipt_hash=technical.content_hash,
                    )
                )
        return assets

    @staticmethod
    def _compile_motion(
        *,
        artifacts: AIVisualStagePlanningArtifacts,
        assets: Sequence[VerifiedAIVisualAsset],
    ) -> tuple[list[MotionIntentProjection], FFmpegEffectPlan]:
        asset_by_scene = {
            scene_id: asset for asset in assets for scene_id in asset.bound_scene_ids
        }
        planner = MotionIntentPlanner()
        projections: list[MotionIntentProjection] = []
        for index, scene in enumerate(artifacts.scene_plan.scenes):
            asset = asset_by_scene.get(scene.scene_id)
            if asset is None:
                raise ValidationFailureError("V2_AI_VISUAL_SCENE_ASSET_MISSING")
            projection = planner.project(
                scene_plan=scene,
                style_bible=artifacts.style_bible,
                motion_grammar=artifacts.motion_grammar,
                primary_asset_ref=asset.primary_asset_ref,
                primary_asset_hash=asset.primary_asset_hash,
                previous_projection=projections[-1] if projections else None,
                next_scene_plan=(
                    artifacts.scene_plan.scenes[index + 1]
                    if index + 1 < len(artifacts.scene_plan.scenes)
                    else None
                ),
            )
            projections.append(projection)
        effect_plan = NativeMotionCompiler().compile_effect_plan(
            projections,
            motion_grammar=artifacts.motion_grammar,
        )
        if not effect_plan.production_eligible:
            raise ValidationFailureError("V2_AI_VISUAL_MOTION_EFFECT_PLAN_BLOCKED")
        return projections, effect_plan

    def _persist_completed_artifacts(
        self,
        *,
        scope: _StageScope,
        artifacts: AIVisualStagePlanningArtifacts,
        refs: Mapping[str, str],
        projections: Sequence[MotionIntentProjection],
        effect_plan: FFmpegEffectPlan,
        manifest: AIVisualAssetManifestProjection,
        image_records: Sequence[V2AIImageSceneEffectRecord],
        video_records: Sequence[V2VeoEffectRecord],
    ) -> None:
        _persist_exact_json(
            self.root / refs["effect_plan"], effect_plan.model_dump(mode="json")
        )
        _persist_exact_json(
            self.root / refs["asset_manifest"], manifest.model_dump(mode="json")
        )
        _persist_exact_json(
            self._run_dir(scope.visual_run.id) / "motion-intents.json",
            {
                "schema_version": "vcos.motion-intent-projection-set.v1",
                "projections": [item.model_dump(mode="json") for item in projections],
                "content_hash": ai_visual_stable_hash(
                    [item.content_hash for item in projections]
                ),
            },
        )
        total_cost = sum(
            (record.identity.estimated_cost_usd for record in image_records),
            Decimal("0"),
        ) + sum(
            (
                record.conservative_settlement_cost_usd or Decimal("0")
                for record in video_records
            ),
            Decimal("0"),
        )
        manifest_id = uuid.UUID(manifest.manifest_id)
        with self._session_factory() as session:
            visual_run = session.execute(
                select(AIVisualProductionRun)
                .where(AIVisualProductionRun.id == scope.visual_run.id)
                .with_for_update()
            ).scalar_one()
            if visual_run.scene_plan_id is None:
                raise ValidationFailureError("V2_AI_VISUAL_SCENE_SNAPSHOT_REQUIRED")
            if (
                visual_run.state != "GENERATING"
                or visual_run.current_phase != "GENERATE"
            ):
                raise ValidationFailureError(
                    "V2_AI_VISUAL_MANIFEST_SOURCE_STATE_INVALID"
                )
            if (
                visual_run.effect_plan_ref is None
                and visual_run.effect_plan_hash is None
            ):
                # The database manifest seal reads the run's effect-plan hash.
                # Bind and flush that exact lineage first, using one legal CAS
                # update, before the immutable manifest insert is attempted.
                visual_run.effect_plan_ref = refs["effect_plan"]
                visual_run.effect_plan_hash = effect_plan.effect_plan_hash
                visual_run.projection_version += 1
                session.flush()
            elif (
                visual_run.effect_plan_ref != refs["effect_plan"]
                or visual_run.effect_plan_hash != effect_plan.effect_plan_hash
            ):
                raise ValidationFailureError("V2_AI_VISUAL_EFFECT_PLAN_CONFLICT")
            existing = session.scalar(
                select(AIVisualAssetManifest).where(
                    AIVisualAssetManifest.visual_production_run_id == visual_run.id
                )
            )
            manifest_content = manifest.model_dump(mode="json")
            if existing is None:
                existing = AIVisualAssetManifest(
                    id=manifest_id,
                    visual_production_run_id=visual_run.id,
                    scene_plan_snapshot_id=visual_run.scene_plan_id,
                    scene_plan_hash=artifacts.scene_plan_hash,
                    style_bible_hash=artifacts.style_bible.content_hash,
                    motion_grammar_hash=artifacts.motion_grammar.content_hash,
                    effect_plan_hash=effect_plan.effect_plan_hash,
                    schema_version=manifest.schema_version,
                    scene_count=manifest.scene_count,
                    ai_image_scene_count=manifest.ai_image_scene_count,
                    ai_video_scene_count=manifest.ai_video_scene_count,
                    asset_count=manifest.asset_count,
                    ai_image_asset_count=manifest.ai_image_asset_count,
                    ai_video_asset_count=manifest.ai_video_asset_count,
                    total_provider_call_count=sum(
                        record.provider_call_count for record in image_records
                    )
                    + sum(record.generation_attempt_count for record in video_records),
                    total_estimated_cost_usd=total_cost,
                    total_actual_or_conservative_cost_usd=total_cost,
                    production_eligible=True,
                    renderer_primary_visual_generation=False,
                    content=manifest_content,
                    content_hash=manifest.content_hash,
                )
                session.add(existing)
                session.flush()
            elif (
                existing.id != manifest_id
                or existing.content_hash != manifest.content_hash
                or existing.content != manifest_content
            ):
                raise ValidationFailureError("V2_AI_VISUAL_ASSET_MANIFEST_CONFLICT")
            visual_run.asset_manifest_id = existing.id
            visual_run.asset_manifest_hash = existing.content_hash
            visual_run.state = "ASSETS_VERIFIED"
            visual_run.current_phase = "MANIFEST"
            visual_run.failure_reason_code = None
            visual_run.projection_version += 1
            session.commit()

    def _completed_result(
        self, visual_run_id: uuid.UUID
    ) -> tuple[WorkflowStageResult, dict[str, Any]] | None:
        with self._session_factory() as session:
            visual_run = session.get(AIVisualProductionRun, visual_run_id)
            if visual_run is None or visual_run.state != "ASSETS_VERIFIED":
                return None
            if (
                visual_run.asset_manifest_id is None
                or visual_run.asset_manifest_hash is None
                or visual_run.style_bible_hash is None
                or visual_run.scene_plan_hash is None
                or visual_run.motion_grammar_hash is None
                or visual_run.effect_plan_hash is None
            ):
                raise ValidationFailureError("V2_AI_VISUAL_COMPLETED_RUN_INCOMPLETE")
            row = session.get(AIVisualAssetManifest, visual_run.asset_manifest_id)
            if row is None or row.content_hash != visual_run.asset_manifest_hash:
                raise ValidationFailureError("V2_AI_VISUAL_COMPLETED_MANIFEST_DRIFT")
            manifest = validate_ai_visual_asset_manifest(row.content)
            refs = self._artifact_refs(visual_run.id)
            if (
                manifest.content_hash != row.content_hash
                or manifest.scene_plan_hash != visual_run.scene_plan_hash
                or manifest.style_bible_hash != visual_run.style_bible_hash
                or manifest.motion_grammar_hash != visual_run.motion_grammar_hash
                or manifest.effect_plan_hash != visual_run.effect_plan_hash
            ):
                raise ValidationFailureError("V2_AI_VISUAL_COMPLETED_AUTHORITY_DRIFT")
            result = self._result_from_manifest(
                visual_run=visual_run,
                manifest=manifest,
                manifest_id=row.id,
                refs=refs,
                style_bible_hash=visual_run.style_bible_hash,
                scene_plan_hash=visual_run.scene_plan_hash,
                motion_grammar_hash=visual_run.motion_grammar_hash,
                effect_plan_hash=visual_run.effect_plan_hash,
                timeline_ref=visual_run.source_timeline_ref,
                timeline_hash=visual_run.source_timeline_hash,
                conservative_cost_usd=Decimal(
                    row.total_actual_or_conservative_cost_usd
                ),
                reconciled=True,
            )
            return result, self._journal_from_result(result, reconciled=True)

    @staticmethod
    def _result_from_manifest(
        *,
        visual_run: AIVisualProductionRun,
        manifest: AIVisualAssetManifestProjection,
        manifest_id: uuid.UUID,
        refs: Mapping[str, str],
        style_bible_hash: str,
        scene_plan_hash: str,
        motion_grammar_hash: str,
        effect_plan_hash: str,
        timeline_ref: str,
        timeline_hash: str,
        conservative_cost_usd: Decimal,
        reconciled: bool,
    ) -> WorkflowStageResult:
        return WorkflowStageResult(
            result_type="V2_AI_VISUAL_ASSET_MANIFEST",
            result_id=manifest_id,
            result_ref=refs["asset_manifest"],
            result_hash=manifest.content_hash,
            result_payload={
                "schema_version": V2_AI_VISUAL_STAGE_VERSION,
                "visual_production_run_id": str(visual_run.id),
                "asset_manifest_ref": refs["asset_manifest"],
                "asset_manifest_hash": manifest.content_hash,
                "scene_count": manifest.scene_count,
                "asset_count": manifest.asset_count,
                "ai_image_asset_count": manifest.ai_image_asset_count,
                "ai_video_asset_count": manifest.ai_video_asset_count,
                "provider_call_count": manifest.asset_count,
                "reused_presentation_window_count": (
                    manifest.scene_count - manifest.asset_count
                ),
                "estimated_and_conservative_cost_usd": format(
                    conservative_cost_usd, "f"
                ),
                "cost_basis": "CONSERVATIVE_CATALOG_ESTIMATE_SUCCESS",
                "renderer_primary_visual_generation": False,
                "native_primary_visuals_present": False,
                "automatic_publish": False,
                "reconciled": reconciled,
            },
            authority_refs=WorkflowAuthorityRefs(
                video_project_id=visual_run.video_project_id,
                production_package_artifact_version_id=(
                    visual_run.production_package_artifact_version_id
                ),
                production_package_hash=visual_run.production_package_hash,
                canonical_media_timeline_ref=timeline_ref,
                canonical_media_timeline_hash=timeline_hash,
                ai_visual_production_run_id=visual_run.id,
                ai_visual_policy_ref=visual_run.production_visual_policy_ref,
                ai_visual_policy_hash=visual_run.production_visual_policy_hash,
                ai_visual_style_bible_ref=refs["style_bible"],
                ai_visual_style_bible_hash=style_bible_hash,
                ai_visual_scene_plan_ref=refs["scene_plan"],
                ai_visual_scene_plan_hash=scene_plan_hash,
                ai_visual_asset_manifest_ref=refs["asset_manifest"],
                ai_visual_asset_manifest_hash=manifest.content_hash,
                video_motion_grammar_ref=refs["motion_grammar"],
                video_motion_grammar_hash=motion_grammar_hash,
                ffmpeg_effect_plan_ref=refs["effect_plan"],
                ffmpeg_effect_plan_hash=effect_plan_hash,
            ),
            reason_codes=[
                "V2_AI_VISUAL_AI_ONLY_POLICY_VERIFIED",
                "V2_AI_VISUAL_OWNER_EFFECTS_VERIFIED",
                "V2_AI_VISUAL_REUSE_WINDOWS_ZERO_PROVIDER_CALLS",
                "V2_AI_VISUAL_MOTION_EFFECT_PLAN_VERIFIED",
                "V2_AI_VISUAL_CONSERVATIVE_BUDGET_SETTLED",
            ],
        )

    @staticmethod
    def _journal_from_result(
        result: WorkflowStageResult, *, reconciled: bool
    ) -> dict[str, Any]:
        payload = result.result_payload
        return {
            "schema_version": "vcos.production-effect-journal.v1",
            "stage_version": V2_AI_VISUAL_STAGE_VERSION,
            "stage": "VISUAL",
            "state": "VERIFIED",
            "asset_manifest_ref": result.result_ref,
            "asset_manifest_hash": result.result_hash,
            "visual_production_run_id": payload["visual_production_run_id"],
            "scene_count": payload["scene_count"],
            "asset_count": payload["asset_count"],
            "provider_call_count": payload["provider_call_count"],
            "ai_video_asset_count": payload["ai_video_asset_count"],
            "reused_presentation_window_count": payload[
                "reused_presentation_window_count"
            ],
            "cost_basis": payload["cost_basis"],
            "reconciled_from_existing_authority": reconciled,
            "completed_at": utc_now().isoformat(),
        }


__all__ = [
    "AIVisualStageImageService",
    "AIVisualStagePlanningArtifacts",
    "V2_AI_VISUAL_PRODUCTION_ADAPTER_KEY",
    "V2_AI_VISUAL_STAGE_VERSION",
    "V2AIVisualProductionAdapter",
    "build_video_visual_style_bible",
    "compile_ai_visual_stage_planning",
    "narration_units_from_frozen_timeline",
]
