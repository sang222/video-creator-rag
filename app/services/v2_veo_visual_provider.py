"""Crash-safe, per-scene Google Veo production boundary.

This module deliberately does not register itself with the V2 workflow or own a
database model.  It defines the provider-side state machine and a small durable
store protocol so the visual-production persistence layer can bind one record
to one server-owned scene/effect identity.

Generation is at-most-once.  The store must commit ``SUBMITTING`` before the
injected client is reached.  A submit whose exact provider operation identity
cannot be recovered is terminally uncertain and is never retried by this
service.  Polling and downloading always address an already recorded operation
and therefore cannot create another generation.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import (
    Any,
    Callable,
    Literal,
    Mapping,
    Protocol,
    Sequence,
    runtime_checkable,
)

from app.contracts.ai_visual_production import (
    AIVisualScenePlan,
    CompiledAIVideoPrompt,
)
from app.services.v2_ai_visual_provider import (
    v2_ai_image_required_semantic_anchors,
)
from app.core.config import Settings, VEO_APPROVED_MODEL_IDS
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.native_render_plan import stable_hash


V2_VEO_VISUAL_PROVIDER_VERSION = "vcos.v2-veo-visual-provider.v1"
V2_VEO_REQUEST_JOURNAL_SCHEMA = "vcos.v2-veo-request-journal.v1"
V2_VEO_RESPONSE_JOURNAL_SCHEMA = "vcos.v2-veo-response-journal.v1"
V2_VEO_QC_SCHEMA = "vcos.v2-veo-asset-qc.v1"
V2_VEO_NORMALIZATION_SCHEMA = "vcos.v2-veo-normalization.v1"
V2_VEO_STORE_DURABILITY = "DURABLE_TRANSACTIONAL"

V2VeoEffectState = Literal[
    "PREPARED",
    "SUBMITTING",
    "OPERATION_RECORDED",
    "POLLING",
    "RESPONSE_CAPTURED",
    "DOWNLOADED",
    "NORMALIZED",
    "VERIFIED",
    "FAILED_DEFINITIVE",
    "FAILED_UNCERTAIN",
    "BLOCKED",
]

_RESOLUTION_DIMENSIONS = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "4k": (3840, 2160),
}
_OPERATION_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/:-"
)


def build_v2_veo_provider_config_payload(
    *,
    provider_config_version: str,
    model_id: str,
    duration_seconds: int = 8,
    resolution: str = "720p",
    fps: int = 24,
    aspect_ratio: str = "16:9",
    output_count: int = 1,
    character_policy_mode: str = "NO_CHARACTER",
    human_likeness_requested: bool = False,
    provider_person_generation_transport: str = "allow_all",
    provider_audio_expected: bool = True,
    provider_audio_usage_policy: str = "DISCARD",
) -> dict[str, Any]:
    """Canonical, secret-free transport policy whose hash is stored per effect."""

    return {
        "schema_version": provider_config_version,
        "provider_key": "google_veo",
        "transport": "GEMINI_DEVELOPER_API",
        "service_version": V2_VEO_VISUAL_PROVIDER_VERSION,
        "operation_type": "VEO_TEXT_TO_VIDEO",
        "model_id": model_id,
        "duration_seconds": duration_seconds,
        "resolution": resolution,
        "fps": fps,
        "aspect_ratio": aspect_ratio,
        "output_count": output_count,
        "character_policy_mode": character_policy_mode,
        "human_likeness_requested": human_likeness_requested,
        "provider_person_generation_transport": (provider_person_generation_transport),
        "provider_audio_expected": provider_audio_expected,
        "provider_audio_usage_policy": provider_audio_usage_policy,
        "generate_audio_parameter_sent": False,
        "total_transport_attempts": 1,
        "automatic_retry_attempts": 0,
        "fallback_allowed": False,
    }


class V2VeoProviderError(RuntimeError):
    """Base error carrying a stable, non-secret reason code."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


class V2VeoProviderBlocked(V2VeoProviderError):
    def __init__(self, *reason_codes: str):
        reasons = tuple(sorted(set(reason_codes or ("V2_VEO_BLOCKED",))))
        self.reason_codes = reasons
        super().__init__(reasons[0], ",".join(reasons))


class V2VeoDefinitiveProviderError(V2VeoProviderError):
    """A provider rejection known not to have created an operation."""


class V2VeoOperationPersistenceError(V2VeoProviderError):
    """The provider returned an operation but its durable commit failed.

    ``provider_operation_id`` is intentionally available to an incident
    handler.  Supplying that exact identity to ``reconcile_exact_operation`` is
    the only supported recovery; callers must never invoke submit again.
    """

    def __init__(self, provider_operation_id: str):
        self.provider_operation_id = provider_operation_id
        super().__init__(
            "V2_VEO_OPERATION_IDENTITY_PERSISTENCE_FAILED",
            "V2_VEO_OPERATION_IDENTITY_PERSISTENCE_FAILED",
        )


@dataclass(frozen=True, slots=True)
class V2VeoRetryPolicy:
    total_transport_attempts: int = 1
    automatic_retry_attempts: int = 0

    def __post_init__(self) -> None:
        if self.total_transport_attempts != 1 or self.automatic_retry_attempts != 0:
            raise ValueError("V2_VEO_AUTOMATIC_RETRY_FORBIDDEN")


@dataclass(frozen=True, slots=True)
class V2VeoExecutionAuthorization:
    """Secret-free projection of the real-execution gates."""

    provider_boundary_gate_passed: bool
    provider_real_execution_enabled: bool
    provider_production_execution_enabled: bool
    veo_real_generation_enabled: bool
    credential_configured: bool
    budget_reservation_active: bool
    cost_approval_active: bool
    paid_attempt_available: bool
    replacement_authority_active: bool
    unavailable_behavior: Literal["BLOCK"] = "BLOCK"
    max_generation_attempts: int = 1

    @property
    def blockers(self) -> tuple[str, ...]:
        checks = {
            "V2_VEO_PROVIDER_BOUNDARY_BLOCKED": self.provider_boundary_gate_passed,
            "V2_PROVIDER_REAL_EXECUTION_DISABLED": self.provider_real_execution_enabled,
            "V2_PROVIDER_PRODUCTION_EXECUTION_DISABLED": (
                self.provider_production_execution_enabled
            ),
            "V2_VEO_REAL_GENERATION_DISABLED": self.veo_real_generation_enabled,
            "V2_VEO_CREDENTIAL_MISSING": self.credential_configured,
            "V2_VEO_BUDGET_RESERVATION_INACTIVE": self.budget_reservation_active,
            "V2_VEO_COST_APPROVAL_INACTIVE": self.cost_approval_active,
            "V2_VEO_PAID_ATTEMPT_UNAVAILABLE": self.paid_attempt_available,
            "V2_VEO_REPLACEMENT_AUTHORITY_INACTIVE": (
                self.replacement_authority_active
            ),
            "V2_VEO_FALLBACK_POLICY_INVALID": self.unavailable_behavior == "BLOCK",
            "V2_VEO_ATTEMPT_LIMIT_INVALID": self.max_generation_attempts == 1,
        }
        return tuple(sorted(code for code, passed in checks.items() if not passed))


@dataclass(frozen=True, slots=True)
class V2VeoGenerationAuthority:
    """Exact server-owned lineage and paid authority for one Veo scene."""

    asset_effect_id: str
    replacement_authority_id: str
    replacement_authority_hash: str
    visual_production_run_id: str
    scene_plan_snapshot_id: str
    style_bible_id: str
    workflow_run_id: str
    project_id: str
    production_package_artifact_version_id: str
    production_package_hash: str
    asset_slot_id: str
    scene_id: str
    bound_scene_ids: tuple[str, ...]
    bound_scene_plan_hashes: tuple[str, ...]
    primary_asset_owner_scene_id: str
    ordinal: int
    route: Literal["AI_VIDEO"]
    generation_mode: Literal["VEO_TEXT_TO_VIDEO"]
    asset_acquisition_mode: Literal["GENERATED"]
    production_visual_policy_version: Literal[
        "vcos.production-visual-policy.ai-only.v1"
    ]
    production_visual_policy_ref: str
    production_visual_policy_hash: str
    model_id: str
    provider_config_version: str
    provider_config_hash: str
    catalog_version: str
    catalog_ref: str
    catalog_hash: str
    style_bible_ref: str
    style_bible_hash: str
    scene_plan_ref: str
    scene_plan_hash: str
    compiled_prompt_ref: str
    compiled_prompt_hash: str
    compiled_prompt_content_hash: str
    prompt_compiler_version: str
    prompt: str
    prompt_hash: str
    required_semantic_anchors: tuple[str, str, str, str]
    negative_prompt: str
    idempotency_key: str
    budget_reservation_id: str
    budget_reservation_ref: str
    budget_authority_hash: str
    cost_estimate_ref: str
    cost_estimate_hash: str
    approval_ref: str
    approval_hash: str
    estimated_cost_usd: Decimal
    maximum_approved_cost_usd: Decimal
    duration_seconds: int = 8
    resolution: Literal["720p", "1080p", "4k"] = "720p"
    # Veo 3.1 emits 24 fps.  Keep the normalized asset at the provider-native
    # cadence instead of silently inventing interpolated frames.
    fps: Literal[24] = 24
    aspect_ratio: Literal["16:9"] = "16:9"
    output_count: int = 1
    character_policy_mode: Literal["NO_CHARACTER"] = "NO_CHARACTER"
    human_likeness_requested: bool = False
    provider_person_generation_transport: Literal["allow_all"] = "allow_all"
    provider_audio_expected: bool = True
    provider_audio_usage_policy: Literal["DISCARD"] = "DISCARD"
    synthetic_media_disclosure_required: bool = True

    def __post_init__(self) -> None:
        text_fields = (
            "asset_effect_id",
            "replacement_authority_id",
            "visual_production_run_id",
            "scene_plan_snapshot_id",
            "style_bible_id",
            "workflow_run_id",
            "project_id",
            "production_package_artifact_version_id",
            "asset_slot_id",
            "scene_id",
            "primary_asset_owner_scene_id",
            "production_visual_policy_ref",
            "provider_config_version",
            "catalog_version",
            "catalog_ref",
            "style_bible_ref",
            "scene_plan_ref",
            "compiled_prompt_ref",
            "prompt_compiler_version",
            "prompt",
            "negative_prompt",
            "idempotency_key",
            "budget_reservation_id",
            "budget_reservation_ref",
            "cost_estimate_ref",
            "approval_ref",
        )
        if any(
            not str(getattr(self, field_name)).strip() for field_name in text_fields
        ):
            raise ValueError("V2_VEO_AUTHORITY_TEXT_REQUIRED")
        hash_fields = (
            "replacement_authority_hash",
            "production_package_hash",
            "production_visual_policy_hash",
            "provider_config_hash",
            "catalog_hash",
            "style_bible_hash",
            "scene_plan_hash",
            "compiled_prompt_hash",
            "compiled_prompt_content_hash",
            "prompt_hash",
            "budget_authority_hash",
            "cost_estimate_hash",
            "approval_hash",
        )
        if any(not _is_sha256(getattr(self, field_name)) for field_name in hash_fields):
            raise ValueError("V2_VEO_AUTHORITY_HASH_INVALID")
        if self.prompt_hash != hashlib.sha256(self.prompt.encode("utf-8")).hexdigest():
            raise ValueError("V2_VEO_PROMPT_HASH_MISMATCH")
        if (
            len(self.required_semantic_anchors) != 4
            or len(set(self.required_semantic_anchors)) != 4
            or any(not value.strip() for value in self.required_semantic_anchors)
        ):
            raise ValueError("V2_VEO_REQUIRED_SEMANTIC_ANCHORS_INVALID")
        if (
            self.route != "AI_VIDEO"
            or self.generation_mode != "VEO_TEXT_TO_VIDEO"
            or self.asset_acquisition_mode != "GENERATED"
            or self.production_visual_policy_version
            != "vcos.production-visual-policy.ai-only.v1"
        ):
            raise ValueError("V2_VEO_VISUAL_POLICY_ROUTE_INVALID")
        if (
            not self.bound_scene_ids
            or len(self.bound_scene_ids) != len(self.bound_scene_plan_hashes)
            or len(set(self.bound_scene_ids)) != len(self.bound_scene_ids)
            or self.scene_id != self.primary_asset_owner_scene_id
            or self.scene_id not in self.bound_scene_ids
            or self.scene_plan_hash
            != self.bound_scene_plan_hashes[self.bound_scene_ids.index(self.scene_id)]
            or any(not _is_sha256(value) for value in self.bound_scene_plan_hashes)
        ):
            raise ValueError("V2_VEO_BOUND_SCENE_AUTHORITY_INVALID")
        if self.model_id not in VEO_APPROVED_MODEL_IDS:
            raise ValueError("V2_VEO_MODEL_NOT_APPROVED")
        if (
            self.duration_seconds != 8
            or self.output_count != 1
            or self.fps != 24
            or self.resolution not in _RESOLUTION_DIMENSIONS
            or self.ordinal <= 0
        ):
            raise ValueError("V2_VEO_OUTPUT_PROFILE_INVALID")
        if (
            self.character_policy_mode != "NO_CHARACTER"
            or self.human_likeness_requested
            or self.provider_person_generation_transport != "allow_all"
        ):
            raise ValueError("V2_VEO_NO_CHARACTER_POLICY_CONFLICT")
        if (
            not self.provider_audio_expected
            or self.provider_audio_usage_policy != "DISCARD"
        ):
            raise ValueError("V2_VEO_PROVIDER_AUDIO_POLICY_INVALID")
        if self.provider_config_hash != stable_hash(self.provider_config_payload):
            raise ValueError("V2_VEO_PROVIDER_CONFIG_HASH_MISMATCH")
        negative = self.negative_prompt.casefold()
        if any(
            token not in negative
            for token in ("people", "face", "human", "text", "logo", "watermark")
        ):
            raise ValueError("V2_VEO_NEGATIVE_POLICY_INCOMPLETE")
        if (
            self.estimated_cost_usd <= Decimal("0")
            or self.maximum_approved_cost_usd <= Decimal("0")
            or self.estimated_cost_usd > self.maximum_approved_cost_usd
        ):
            raise ValueError("V2_VEO_COST_AUTHORITY_INVALID")

    @property
    def dimensions(self) -> tuple[int, int]:
        return _RESOLUTION_DIMENSIONS[self.resolution]

    @property
    def provider_config_payload(self) -> dict[str, Any]:
        return build_v2_veo_provider_config_payload(
            provider_config_version=self.provider_config_version,
            model_id=self.model_id,
            duration_seconds=self.duration_seconds,
            resolution=self.resolution,
            fps=self.fps,
            aspect_ratio=self.aspect_ratio,
            output_count=self.output_count,
            character_policy_mode=self.character_policy_mode,
            human_likeness_requested=self.human_likeness_requested,
            provider_person_generation_transport=(
                self.provider_person_generation_transport
            ),
            provider_audio_expected=self.provider_audio_expected,
            provider_audio_usage_policy=self.provider_audio_usage_policy,
        )

    @property
    def identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("prompt")
        # SQL JSON round-trips tuples as arrays.  Seal JSON-native lists here so
        # a durable row compares byte-for-byte with the in-memory authority.
        payload["bound_scene_ids"] = list(self.bound_scene_ids)
        payload["bound_scene_plan_hashes"] = list(self.bound_scene_plan_hashes)
        payload["required_semantic_anchors"] = list(self.required_semantic_anchors)
        payload["estimated_cost_usd"] = str(self.estimated_cost_usd)
        payload["maximum_approved_cost_usd"] = str(self.maximum_approved_cost_usd)
        payload["provider"] = "google_veo"
        payload["service_version"] = V2_VEO_VISUAL_PROVIDER_VERSION
        return payload

    @property
    def identity_hash(self) -> str:
        return stable_hash(self.identity_payload)

    @property
    def provider_request_payload(self) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "prompt": self.prompt,
            "config": {
                "aspect_ratio": self.aspect_ratio,
                "duration_seconds": self.duration_seconds,
                "negative_prompt": self.negative_prompt,
                "number_of_videos": self.output_count,
                # Gemini Developer API Veo 3.1 creates audio.  Sending the SDK
                # generate_audio field is not supported by this transport.
                "generate_audio_parameter_sent": False,
                "person_generation": self.provider_person_generation_transport,
                "resolution": self.resolution,
            },
        }

    @property
    def generation_policy(self) -> dict[str, Any]:
        return {
            "generation_mode": self.generation_mode,
            "duration_seconds": self.duration_seconds,
            "resolution": self.resolution,
            "fps": self.fps,
            "aspect_ratio": self.aspect_ratio,
            "output_count": self.output_count,
            "character_policy_mode": self.character_policy_mode,
            "human_likeness_requested": self.human_likeness_requested,
            "provider_person_generation_transport": (
                self.provider_person_generation_transport
            ),
            "provider_audio_expected": self.provider_audio_expected,
            "provider_audio_usage_policy": self.provider_audio_usage_policy,
            "synthetic_media_disclosure_required": (
                self.synthetic_media_disclosure_required
            ),
            "maximum_attempts": 1,
            "automatic_retry_allowed": False,
            "fallback_allowed": False,
        }

    @property
    def generation_policy_hash(self) -> str:
        return stable_hash(self.generation_policy)

    @property
    def db_identity_projection(self) -> dict[str, Any]:
        """Exact field projection expected by ``AIVisualAssetEffect``."""

        return {
            "visual_production_run_id": self.visual_production_run_id,
            "scene_plan_snapshot_id": self.scene_plan_snapshot_id,
            "workflow_run_id": self.workflow_run_id,
            "video_project_id": self.project_id,
            "asset_slot_id": self.asset_slot_id,
            "scene_id": self.scene_id,
            "bound_scene_ids": list(self.bound_scene_ids),
            "bound_scene_plan_hashes": list(self.bound_scene_plan_hashes),
            "bound_scene_count": len(self.bound_scene_ids),
            "primary_asset_owner_scene_id": self.primary_asset_owner_scene_id,
            "ordinal": self.ordinal,
            "route": self.route,
            "asset_acquisition_mode": self.asset_acquisition_mode,
            "provider_key": "google_veo",
            "model_id": self.model_id,
            "provider_config_version": self.provider_config_version,
            "provider_config_hash": self.provider_config_hash,
            "price_catalog_version": self.catalog_version,
            "price_catalog_ref": self.catalog_ref,
            "price_catalog_hash": self.catalog_hash,
            "production_visual_policy_version": (self.production_visual_policy_version),
            "production_visual_policy_hash": self.production_visual_policy_hash,
            "style_bible_ref": self.style_bible_ref,
            "style_bible_hash": self.style_bible_hash,
            "scene_plan_ref": self.scene_plan_ref,
            "scene_plan_hash": self.scene_plan_hash,
            "compiled_prompt_ref": self.compiled_prompt_ref,
            "compiled_prompt_hash": self.compiled_prompt_hash,
            "compiled_prompt_content_hash": self.compiled_prompt_content_hash,
            "prompt_compiler_version": self.prompt_compiler_version,
            "prompt_hash": self.prompt_hash,
            "generation_policy": self.generation_policy,
            "generation_policy_hash": self.generation_policy_hash,
            "effect_identity_hash": self.identity_hash,
            "request_hash": self.request_hash,
            "idempotency_key": self.idempotency_key,
            "approval_ref": self.approval_ref,
            "approval_hash": self.approval_hash,
            "budget_reservation_id": self.budget_reservation_id,
            "budget_authority_ref": self.budget_reservation_ref,
            "budget_authority_hash": self.budget_authority_hash,
            "cost_estimate_ref": self.cost_estimate_ref,
            "cost_estimate_hash": self.cost_estimate_hash,
            "estimated_cost_usd": self.estimated_cost_usd,
            "maximum_cost_usd": self.maximum_approved_cost_usd,
            "maximum_attempts": 1,
            "retry_allowed": False,
            "fallback_allowed": False,
        }

    @property
    def request_hash(self) -> str:
        return stable_hash(
            {
                "identity_hash": self.identity_hash,
                "provider_request": self.provider_request_payload,
            }
        )

    def request_journal(self, now: datetime) -> dict[str, Any]:
        payload = {
            "schema_version": V2_VEO_REQUEST_JOURNAL_SCHEMA,
            "prepared_at": now.isoformat(),
            "asset_effect_id": self.asset_effect_id,
            "scene_id": self.scene_id,
            "route": self.route,
            "generation_mode": self.generation_mode,
            "provider": "google_veo",
            "identity_hash": self.identity_hash,
            "request_hash": self.request_hash,
            "authority": self.identity_payload,
            "provider_request": self.provider_request_payload,
            "attempt_limit": 1,
            "automatic_retry_attempts": 0,
            "provider_audio_discard_required": True,
            "fallback_allowed": False,
            "secret_values_exposed": False,
        }
        return {**payload, "journal_hash": stable_hash(payload)}

    @classmethod
    def from_compiled_visual_authority(
        cls,
        *,
        scene_plan: AIVisualScenePlan,
        compiled_prompt: CompiledAIVideoPrompt,
        bound_scene_plans: Sequence[AIVisualScenePlan] | None = None,
        **authority: Any,
    ) -> "V2VeoGenerationAuthority":
        """Bind planner/compiler artifacts without re-inferring semantics."""

        if (
            scene_plan.production_route != "AI_VIDEO"
            or scene_plan.scene_id != compiled_prompt.scene_id
            or scene_plan.content_hash != compiled_prompt.scene_plan_hash
            or scene_plan.style_bible_hash != compiled_prompt.style_bible_hash
            or compiled_prompt.provider_generation_duration_ms != 8_000
            or compiled_prompt.provider_audio_usage_policy != "DISCARD"
        ):
            raise ValueError("V2_VEO_COMPILED_VISUAL_BINDING_MISMATCH")
        if (
            scene_plan.reuses_primary_asset_from_scene_id is not None
            or scene_plan.asset_reuse_semantic_reason is not None
        ):
            raise ValueError("V2_VEO_REUSED_SCENE_CANNOT_OWN_GENERATION")
        bound = tuple(
            sorted(bound_scene_plans or (scene_plan,), key=lambda item: item.ordinal)
        )
        if any(
            item.primary_asset_slot_id != scene_plan.primary_asset_slot_id
            or item.production_route != "AI_VIDEO"
            or (
                item.scene_id != scene_plan.scene_id
                and item.reuses_primary_asset_from_scene_id != scene_plan.scene_id
            )
            for item in bound
        ):
            raise ValueError("V2_VEO_BOUND_SCENE_PLAN_MISMATCH")
        supplied = dict(authority)
        exact = {
            "asset_slot_id": scene_plan.primary_asset_slot_id,
            "scene_id": scene_plan.scene_id,
            "bound_scene_ids": tuple(item.scene_id for item in bound),
            "bound_scene_plan_hashes": tuple(item.content_hash for item in bound),
            "primary_asset_owner_scene_id": scene_plan.scene_id,
            "ordinal": scene_plan.ordinal,
            "route": "AI_VIDEO",
            "generation_mode": "VEO_TEXT_TO_VIDEO",
            "asset_acquisition_mode": "GENERATED",
            "style_bible_hash": scene_plan.style_bible_hash,
            "scene_plan_hash": scene_plan.content_hash,
            "compiled_prompt_ref": (
                f"ai-visual-compiled-video-prompt://{scene_plan.scene_id}"
            ),
            "compiled_prompt_hash": compiled_prompt.content_hash,
            "compiled_prompt_content_hash": compiled_prompt.content_hash,
            "prompt_compiler_version": compiled_prompt.prompt_compiler_version,
            "prompt": compiled_prompt.prompt,
            "prompt_hash": compiled_prompt.prompt_hash,
            "required_semantic_anchors": tuple(
                v2_ai_image_required_semantic_anchors(scene_plan)
            ),
            "negative_prompt": compiled_prompt.negative_prompt,
            "duration_seconds": (
                compiled_prompt.provider_generation_duration_ms // 1_000
            ),
            "aspect_ratio": compiled_prompt.aspect_ratio,
            "provider_audio_usage_policy": (
                compiled_prompt.provider_audio_usage_policy
            ),
        }
        for key, value in exact.items():
            if key in supplied and supplied[key] != value:
                raise ValueError("V2_VEO_COMPILED_VISUAL_OVERRIDE_FORBIDDEN")
        return cls(**supplied, **exact)


@dataclass(frozen=True, slots=True)
class V2VeoEffectRecord:
    """Store-neutral projection of one durable per-scene provider effect."""

    asset_effect_id: str
    identity_hash: str
    request_hash: str
    authority: Mapping[str, Any]
    request_journal: Mapping[str, Any]
    state: V2VeoEffectState = "PREPARED"
    version: int = 1
    generation_attempt_count: int = 0
    prepared_at: datetime | None = None
    submitted_at: datetime | None = None
    response_captured_at: datetime | None = None
    completed_at: datetime | None = None
    provider_operation_id: str | None = None
    provider_request_id: str | None = None
    provider_response_id: str | None = None
    response_journals: tuple[Mapping[str, Any], ...] = ()
    raw_output_ref: str | None = None
    raw_output_sha256: str | None = None
    raw_output_size_bytes: int | None = None
    normalized_output_ref: str | None = None
    normalized_output_sha256: str | None = None
    normalized_output_size_bytes: int | None = None
    output_content_type: str | None = None
    output_width: int | None = None
    output_height: int | None = None
    output_duration_ms: int | None = None
    output_fps: Decimal | None = None
    output_audio_stream_count: int | None = None
    normalization_receipt: Mapping[str, Any] = field(default_factory=dict)
    qc_receipt: Mapping[str, Any] = field(default_factory=dict)
    actual_cost_usd: Decimal | None = None
    conservative_settlement_cost_usd: Decimal | None = None
    cost_settlement_basis: str | None = None
    production_eligible: bool = False
    last_error_code: str | None = None

    @property
    def db_state_projection(self) -> dict[str, Any]:
        """Runtime fields that map directly onto ``AIVisualAssetEffect``.

        The concrete store must additionally persist content-addressed refs and
        hashes for request/response/normalization/QC journals in the same CAS
        transaction.  Those storage locations intentionally stay out of this
        provider-neutral record.
        """

        return {
            "state": self.state,
            "revision": self.version,
            "provider_call_count": self.generation_attempt_count,
            "provider_operation_id": self.provider_operation_id,
            "provider_request_id": self.provider_request_id,
            "provider_response_id": self.provider_response_id,
            "output_ref": self.normalized_output_ref,
            "output_checksum": self.normalized_output_sha256,
            "output_size_bytes": self.normalized_output_size_bytes,
            "output_content_type": self.output_content_type,
            "output_width": self.output_width,
            "output_height": self.output_height,
            "output_duration_ms": self.output_duration_ms,
            "output_fps": self.output_fps,
            "output_audio_stream_count": self.output_audio_stream_count,
            "qc_evidence": dict(self.qc_receipt),
            "actual_cost_usd": self.actual_cost_usd,
            "cost_settlement_basis": self.cost_settlement_basis,
            "retry_allowed": False,
            "fallback_allowed": False,
            "failure_reason_code": self.last_error_code,
            "submitted_at": self.submitted_at,
            "response_captured_at": self.response_captured_at,
            "completed_at": self.completed_at,
        }


@runtime_checkable
class V2VeoEffectStore(Protocol):
    """Durable store contract; every method must commit before returning.

    Implementations backed by ``AIVisualAssetEffect`` must enforce:

    * ``load_or_prepare`` is an insert-or-load under the unique effect/request/
      idempotency identities and rejects any existing lineage mismatch;
    * ``compare_and_set`` is one transaction guarded by ``revision`` (mapped to
      ``V2VeoEffectRecord.version``) and the expected state;
    * PREPARED seals the exact request journal bytes plus ref/hash before any
      submit, and SUBMITTING commits ``provider_call_count=1`` before returning;
    * response journals are append-only, content-addressed, sanitized (no raw
      URL, credential, or raw provider payload), and their latest ref/hash are
      committed with each provider state transition;
    * ``provider_operation_id`` is immutable once non-null; journal and media
      refs remain inside the owned workspace; VERIFIED may be committed only
      with checksum, dimensions, duration/fps, zero audio streams, QC hash, and
      cost settlement evidence;
    * ``FAILED_UNCERTAIN`` without an operation identity can only move through
      explicit exact-operation recovery authority.  It can never return to
      PREPARED/SUBMITTING.

    A JSON-file-only or process-memory implementation must not declare
    ``durability == V2_VEO_STORE_DURABILITY`` in production.
    """

    durability: str
    ready: bool

    def load_or_prepare(
        self,
        *,
        asset_effect_id: str,
        identity_hash: str,
        request_hash: str,
        authority: V2VeoGenerationAuthority,
        request_journal: Mapping[str, Any],
    ) -> V2VeoEffectRecord: ...

    def get(self, asset_effect_id: str) -> V2VeoEffectRecord | None: ...

    def compare_and_set(
        self,
        *,
        asset_effect_id: str,
        expected_version: int,
        expected_states: frozenset[str],
        new_state: V2VeoEffectState,
        patch: Mapping[str, Any],
    ) -> V2VeoEffectRecord: ...


@dataclass(frozen=True, slots=True)
class V2VeoProviderSubmission:
    provider_operation_id: str
    provider_status: str = "SUBMITTED"
    provider_response_id: str | None = None


@dataclass(frozen=True, slots=True)
class V2VeoProviderClientReadiness:
    ready: bool
    sdk_available: bool
    sdk_version: str | None
    no_retry_attested: bool
    blocker_reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class V2VeoOperationSnapshot:
    provider_operation_id: str
    provider_status: str
    done: bool
    succeeded: bool
    output_available: bool
    provider_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class V2VeoDownloadedOutput:
    content: bytes
    content_type: str = "video/mp4"
    provider_response_id: str | None = None

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("V2_VEO_DOWNLOADED_OUTPUT_EMPTY")
        if not self.content_type.startswith("video/"):
            raise ValueError("V2_VEO_DOWNLOADED_CONTENT_TYPE_INVALID")


@runtime_checkable
class V2VeoProviderClient(Protocol):
    retry_policy: V2VeoRetryPolicy

    def submit_once(
        self, authority: V2VeoGenerationAuthority
    ) -> V2VeoProviderSubmission: ...

    def poll_exact(self, provider_operation_id: str) -> V2VeoOperationSnapshot: ...

    def download_exact(self, provider_operation_id: str) -> V2VeoDownloadedOutput: ...


class GoogleGenAIVeoSDKClient:
    """Official google-genai transport with an attested one-attempt SDK.

    The SDK client is injected so credentials and environment selection remain
    outside this service.  Its configured ``HttpRetryOptions.attempts`` must be
    exactly one (one total HTTP attempt, therefore zero automatic retries).
    """

    retry_policy = V2VeoRetryPolicy()

    def __init__(self, sdk_client: Any):
        attempts = _google_genai_total_attempts(sdk_client)
        if attempts != 1:
            raise ValueError("V2_VEO_SDK_NO_RETRY_ATTESTATION_FAILED")
        self._client = sdk_client

    @classmethod
    def from_api_key(cls, api_key: str, *, timeout_ms: int = 120_000):
        """Build the only supported official client policy; performs no call."""

        if not api_key.strip():
            raise ValueError("V2_VEO_API_KEY_REQUIRED")
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]

        return cls(
            genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=timeout_ms,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        )

    @staticmethod
    def readiness_projection() -> V2VeoProviderClientReadiness:
        """Report local SDK capability without reading credentials or calling Google."""

        try:
            from google import genai  # type: ignore[import-not-found]
            from google.genai import types  # type: ignore[import-not-found]
        except (ImportError, ModuleNotFoundError):
            return V2VeoProviderClientReadiness(
                ready=False,
                sdk_available=False,
                sdk_version=None,
                no_retry_attested=False,
                blocker_reason_codes=("V2_VEO_GOOGLE_GENAI_SDK_MISSING",),
            )
        required_config_fields = {
            "aspect_ratio",
            "duration_seconds",
            "negative_prompt",
            "number_of_videos",
            "person_generation",
            "resolution",
        }
        fields = set(getattr(types.GenerateVideosConfig, "model_fields", {}))
        operation_fields = set(
            getattr(types.GenerateVideosOperation, "model_fields", {})
        )
        contract_ok = required_config_fields.issubset(fields) and {
            "name",
            "done",
            "error",
            "response",
        }.issubset(operation_fields)
        blockers = () if contract_ok else ("V2_VEO_GOOGLE_GENAI_SDK_CONTRACT_INVALID",)
        return V2VeoProviderClientReadiness(
            ready=contract_ok,
            sdk_available=True,
            sdk_version=str(getattr(genai, "__version__", "unknown")),
            no_retry_attested=True,
            blocker_reason_codes=blockers,
        )

    def submit_once(
        self, authority: V2VeoGenerationAuthority
    ) -> V2VeoProviderSubmission:
        from google.genai import types  # type: ignore[import-not-found]

        config = types.GenerateVideosConfig(
            aspect_ratio=authority.aspect_ratio,
            duration_seconds=authority.duration_seconds,
            negative_prompt=authority.negative_prompt,
            number_of_videos=authority.output_count,
            person_generation=authority.provider_person_generation_transport,
            resolution=authority.resolution,
        )
        operation = self._client.models.generate_videos(
            model=authority.model_id,
            prompt=authority.prompt,
            config=config,
        )
        operation_id = _validate_operation_id(str(getattr(operation, "name", "")))
        return V2VeoProviderSubmission(provider_operation_id=operation_id)

    def poll_exact(self, provider_operation_id: str) -> V2VeoOperationSnapshot:
        from google.genai import types  # type: ignore[import-not-found]

        operation_id = _validate_operation_id(provider_operation_id)
        operation = self._client.operations.get(
            types.GenerateVideosOperation(name=operation_id)
        )
        returned_id = _validate_operation_id(
            str(getattr(operation, "name", "") or operation_id)
        )
        if returned_id != operation_id:
            raise RuntimeError("V2_VEO_POLL_OPERATION_ID_MISMATCH")
        if not bool(getattr(operation, "done", False)):
            return V2VeoOperationSnapshot(
                provider_operation_id=operation_id,
                provider_status="PROCESSING",
                done=False,
                succeeded=False,
                output_available=False,
            )
        error = getattr(operation, "error", None)
        if error:
            return V2VeoOperationSnapshot(
                provider_operation_id=operation_id,
                provider_status="FAILED",
                done=True,
                succeeded=False,
                output_available=False,
                provider_error_code=_safe_provider_error_code(error),
            )
        generated = _generated_videos(operation)
        return V2VeoOperationSnapshot(
            provider_operation_id=operation_id,
            provider_status="SUCCEEDED" if generated else "OUTPUT_MISSING",
            done=True,
            succeeded=bool(generated),
            output_available=bool(generated),
            provider_error_code=None if generated else "VEO_OUTPUT_MISSING",
        )

    def download_exact(self, provider_operation_id: str) -> V2VeoDownloadedOutput:
        from google.genai import types  # type: ignore[import-not-found]

        operation_id = _validate_operation_id(provider_operation_id)
        operation = self._client.operations.get(
            types.GenerateVideosOperation(name=operation_id)
        )
        returned_id = _validate_operation_id(
            str(getattr(operation, "name", "") or operation_id)
        )
        if returned_id != operation_id:
            raise RuntimeError("V2_VEO_DOWNLOAD_OPERATION_ID_MISMATCH")
        generated = _generated_videos(operation)
        if not bool(getattr(operation, "done", False)) or not generated:
            raise RuntimeError("V2_VEO_OUTPUT_NOT_READY")
        video = getattr(generated[0], "video", None)
        if video is None:
            raise RuntimeError("V2_VEO_OUTPUT_MISSING")
        content = self._client.files.download(file=video)
        return V2VeoDownloadedOutput(content=bytes(content))


@dataclass(frozen=True, slots=True)
class V2VeoNormalizationReceipt:
    input_sha256: str
    output_sha256: str
    output_size_bytes: int
    width: int
    height: int
    fps: int
    input_audio_stream_count: int
    output_audio_stream_count: int
    contains_audio_stream: bool
    provider_audio_discarded: bool
    ffmpeg_argv_hash: str

    def as_journal(self) -> dict[str, Any]:
        payload = {
            "schema_version": V2_VEO_NORMALIZATION_SCHEMA,
            **asdict(self),
            "audio_policy": "REMOVE",
            "narration_authority": "ELEVENLABS",
            "final_mix_authority": "NATIVE_FFMPEG",
            "normalization_passed": not self.contains_audio_stream,
        }
        return {**payload, "normalization_hash": stable_hash(payload)}


@dataclass(frozen=True, slots=True)
class V2VeoVideoQCReceipt:
    result: Literal["PASS", "FAIL"]
    checks: Mapping[str, Any]
    reason_codes: tuple[str, ...]
    asset_sha256: str

    def as_journal(self) -> dict[str, Any]:
        payload = {
            "schema_version": V2_VEO_QC_SCHEMA,
            "result": self.result,
            "checks": dict(self.checks),
            "reason_codes": list(self.reason_codes),
            "asset_sha256": self.asset_sha256,
            "provider_provenance_valid": True,
            "scene_binding_valid": True,
            "provider_audio_authority": False,
        }
        return {**payload, "qc_hash": stable_hash(payload)}


@runtime_checkable
class V2VeoMediaRuntime(Protocol):
    def readiness(self) -> Mapping[str, Any]: ...

    def normalize_visual_only(
        self,
        *,
        source: Path,
        destination: Path,
        width: int,
        height: int,
        fps: int,
    ) -> V2VeoNormalizationReceipt: ...

    def inspect(
        self,
        *,
        asset: Path,
        expected_width: int,
        expected_height: int,
        expected_fps: int,
        expected_duration_seconds: float,
    ) -> V2VeoVideoQCReceipt: ...


class FFmpegV2VeoMediaRuntime:
    """Execute local visual-only normalization and byte-level technical QC."""

    def __init__(
        self,
        *,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ) -> None:
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg") or ""
        self.ffprobe = ffprobe or shutil.which("ffprobe") or ""
        self._runner = runner

    def readiness(self) -> Mapping[str, Any]:
        ffmpeg_ready = bool(
            self.ffmpeg
            and Path(self.ffmpeg).is_file()
            and os.access(self.ffmpeg, os.X_OK)
        )
        ffprobe_ready = bool(
            self.ffprobe
            and Path(self.ffprobe).is_file()
            and os.access(self.ffprobe, os.X_OK)
        )
        blockers = []
        if not ffmpeg_ready:
            blockers.append("V2_VEO_FFMPEG_UNAVAILABLE")
        if not ffprobe_ready:
            blockers.append("V2_VEO_FFPROBE_UNAVAILABLE")
        return {
            "ready": not blockers,
            "ffmpeg_available": ffmpeg_ready,
            "ffprobe_available": ffprobe_ready,
            "blockers": blockers,
        }

    def normalize_visual_only(
        self,
        *,
        source: Path,
        destination: Path,
        width: int,
        height: int,
        fps: int,
    ) -> V2VeoNormalizationReceipt:
        if not self.readiness()["ready"]:
            raise RuntimeError("V2_VEO_MEDIA_RUNTIME_UNAVAILABLE")
        if (
            not source.is_file()
            or source.is_symlink()
            or source.stat().st_size <= 0
            or width <= 0
            or height <= 0
            or fps not in {24, 25, 30}
        ):
            raise ValueError("V2_VEO_NORMALIZATION_INPUT_INVALID")
        source_probe = self._probe(source)
        input_audio_count = sum(
            item.get("codec_type") == "audio"
            for item in list(source_probe.get("streams") or [])
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.stem + ".part" + destination.suffix)
        part.unlink(missing_ok=True)
        argv = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={fps},format=yuv420p"
            ),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-movflags",
            "+faststart",
            str(part),
        ]
        completed = self._runner(argv, capture_output=True, text=True, shell=False)
        if completed.returncode != 0 or not part.is_file() or part.stat().st_size <= 0:
            part.unlink(missing_ok=True)
            raise RuntimeError("V2_VEO_FFMPEG_NORMALIZATION_FAILED")
        with part.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(part, destination)
        probe = self._probe(destination)
        video = _video_stream(probe)
        output_audio_count = sum(
            item.get("codec_type") == "audio"
            for item in list(probe.get("streams") or [])
        )
        contains_audio = output_audio_count > 0
        output_sha = _sha256_file(destination)
        receipt = V2VeoNormalizationReceipt(
            input_sha256=_sha256_file(source),
            output_sha256=output_sha,
            output_size_bytes=destination.stat().st_size,
            width=int(video.get("width") or 0),
            height=int(video.get("height") or 0),
            fps=round(_fps(video.get("avg_frame_rate")) or 0),
            input_audio_stream_count=input_audio_count,
            output_audio_stream_count=output_audio_count,
            contains_audio_stream=contains_audio,
            provider_audio_discarded=not contains_audio,
            ffmpeg_argv_hash=stable_hash(argv),
        )
        if (
            receipt.width != width
            or receipt.height != height
            or receipt.fps != fps
            or receipt.contains_audio_stream
            or not receipt.provider_audio_discarded
        ):
            raise RuntimeError("V2_VEO_NORMALIZED_OUTPUT_PROFILE_INVALID")
        return receipt

    def inspect(
        self,
        *,
        asset: Path,
        expected_width: int,
        expected_height: int,
        expected_fps: int,
        expected_duration_seconds: float,
    ) -> V2VeoVideoQCReceipt:
        failures: list[str] = []
        if not asset.is_file() or asset.is_symlink() or asset.stat().st_size <= 0:
            return V2VeoVideoQCReceipt(
                result="FAIL",
                checks={"exists_nonempty": False},
                reason_codes=("V2_VEO_QC_OUTPUT_MISSING",),
                asset_sha256="0" * 64,
            )
        probe = self._probe(asset)
        video = _video_stream(probe)
        duration = _duration_seconds(probe, video)
        fps = _fps(video.get("avg_frame_rate"))
        audio_count = sum(
            item.get("codec_type") == "audio"
            for item in list(probe.get("streams") or [])
        )
        decode = self._runner(
            [
                self.ffmpeg,
                "-v",
                "error",
                "-xerror",
                "-i",
                str(asset),
                "-map",
                "0:v:0",
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            shell=False,
        )
        full_decode = decode.returncode == 0
        probe_seconds = _qc_probe_seconds(duration)
        frames = [self._frame(asset, seconds) for seconds in probe_seconds]
        frame_checks = _evaluate_video_frames(frames)
        checks: dict[str, Any] = {
            "exists_nonempty": True,
            "decode_valid": full_decode,
            "video_stream_present": bool(video),
            "duration_seconds": round(duration, 6),
            "duration_valid": abs(duration - expected_duration_seconds) <= 0.25,
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "resolution_valid": (
                int(video.get("width") or 0) == expected_width
                and int(video.get("height") or 0) == expected_height
            ),
            "fps": fps,
            "fps_valid": (fps is not None and abs(fps - float(expected_fps)) <= 0.01),
            "audio_stream_count": audio_count,
            "provider_audio_discarded": audio_count == 0,
            "sample_probe_seconds": probe_seconds,
            **frame_checks,
        }
        requirements = {
            "decode_valid": True,
            "video_stream_present": True,
            "duration_valid": True,
            "resolution_valid": True,
            "fps_valid": True,
            "provider_audio_discarded": True,
            "sampled_frames_valid": True,
            "not_blank": True,
            "mostly_black_absent": True,
            "not_frozen_throughout": True,
        }
        for key, required in requirements.items():
            if checks.get(key) is not required:
                failures.append("V2_VEO_QC_" + key.upper())
        return V2VeoVideoQCReceipt(
            result="FAIL" if failures else "PASS",
            checks=checks,
            reason_codes=tuple(sorted(set(failures))),
            asset_sha256=_sha256_file(asset),
        )

    def _probe(self, asset: Path) -> dict[str, Any]:
        completed = self._runner(
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(asset),
            ],
            capture_output=True,
            text=True,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("V2_VEO_FFPROBE_FAILED")
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("V2_VEO_FFPROBE_JSON_INVALID") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("V2_VEO_FFPROBE_JSON_INVALID")
        return payload

    def _frame(self, asset: Path, seconds: float) -> bytes:
        completed = self._runner(
            [
                self.ffmpeg,
                "-v",
                "error",
                "-ss",
                f"{seconds:.6f}",
                "-i",
                str(asset),
                "-frames:v",
                "1",
                "-vf",
                "scale=96:54,format=gray",
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
            shell=False,
        )
        return bytes(completed.stdout) if completed.returncode == 0 else b""


@dataclass(frozen=True, slots=True)
class V2VeoProductionReadiness:
    ready: bool
    checks: Mapping[str, bool]
    blocker_reason_codes: tuple[str, ...]


class V2VeoVisualProductionProvider:
    """One-scene Veo generation, reconciliation, materialization, and QC."""

    def __init__(
        self,
        *,
        store: V2VeoEffectStore,
        client: V2VeoProviderClient,
        media_runtime: V2VeoMediaRuntime,
        workspace_root: Path,
        catalog: GoogleVeoModelPriceCatalog | None = None,
        clock: Callable[[], datetime] | None = None,
        settings: Settings | None = None,
        adapter_registered: bool = False,
    ) -> None:
        self.store = store
        self.client = client
        self.media_runtime = media_runtime
        self.workspace_root = workspace_root.resolve()
        self.catalog = catalog or GoogleVeoModelPriceCatalog()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._settings = settings
        self._adapter_registered = adapter_registered

    def readiness(
        self,
        *,
        authority: V2VeoGenerationAuthority,
        execution: V2VeoExecutionAuthorization,
        settings: Settings | None = None,
        adapter_registered: bool | None = None,
    ) -> V2VeoProductionReadiness:
        catalog_ok = self._catalog_reason(authority) is None
        retry_policy_ok = (
            isinstance(getattr(self.client, "retry_policy", None), V2VeoRetryPolicy)
            and self.client.retry_policy.total_transport_attempts == 1
            and self.client.retry_policy.automatic_retry_attempts == 0
        )
        sdk_runtime_ok = True
        sdk_runtime_blockers: tuple[str, ...] = ()
        if isinstance(self.client, GoogleGenAIVeoSDKClient):
            sdk_projection = self.client.readiness_projection()
            sdk_runtime_ok = sdk_projection.ready
            sdk_runtime_blockers = sdk_projection.blocker_reason_codes
        runtime = dict(self.media_runtime.readiness())
        resolved_settings = settings or self._settings
        resolved_registered = (
            self._adapter_registered
            if adapter_registered is None
            else adapter_registered
        )
        settings_checks: dict[str, bool] = {
            "runtime_settings_bound": resolved_settings is not None,
            "runtime_model_exact": False,
            "runtime_duration_exact": False,
            "runtime_resolution_exact": False,
            "runtime_aspect_ratio_exact": False,
            "runtime_output_count_exact": False,
            "runtime_global_execution_enabled": False,
            "runtime_production_execution_enabled": False,
            "runtime_veo_generation_enabled": False,
            "runtime_credential_configured": False,
            "production_adapter_registered": resolved_registered,
        }
        if resolved_settings is not None:
            settings_checks.update(
                {
                    "runtime_model_exact": resolved_settings.veo_model_id
                    == authority.model_id,
                    "runtime_duration_exact": (
                        resolved_settings.veo_default_duration_seconds
                        == authority.duration_seconds
                    ),
                    "runtime_resolution_exact": (
                        resolved_settings.veo_default_resolution == authority.resolution
                    ),
                    "runtime_aspect_ratio_exact": (
                        resolved_settings.veo_default_aspect_ratio
                        == authority.aspect_ratio
                    ),
                    "runtime_output_count_exact": (
                        resolved_settings.veo_default_output_count
                        == authority.output_count
                    ),
                    "runtime_global_execution_enabled": bool(
                        resolved_settings.provider_real_execution_enabled
                    ),
                    "runtime_production_execution_enabled": bool(
                        resolved_settings.provider_production_execution_enabled
                    ),
                    "runtime_veo_generation_enabled": bool(
                        resolved_settings.veo_real_generation_enabled
                    ),
                    "runtime_credential_configured": bool(
                        resolved_settings.gemini_api_key
                        and resolved_settings.gemini_api_key.get_secret_value().strip()
                    ),
                }
            )
        checks = {
            "durable_transactional_store": (
                getattr(self.store, "durability", None) == V2_VEO_STORE_DURABILITY
                and getattr(self.store, "ready", False) is True
            ),
            "client_injected": isinstance(self.client, V2VeoProviderClient),
            "sdk_automatic_retry_disabled": retry_policy_ok,
            "official_sdk_runtime_ready": sdk_runtime_ok,
            "approved_model_catalog_lineage": catalog_ok,
            "media_runtime_ready": runtime.get("ready") is True,
            "execution_authority_ready": not execution.blockers,
            "no_fallback_policy": execution.unavailable_behavior == "BLOCK",
            **settings_checks,
        }
        reason_by_check = {
            "durable_transactional_store": "V2_VEO_DURABLE_STORE_REQUIRED",
            "client_injected": "V2_VEO_CLIENT_INVALID",
            "sdk_automatic_retry_disabled": "V2_VEO_AUTOMATIC_RETRY_FORBIDDEN",
            "official_sdk_runtime_ready": "V2_VEO_GOOGLE_GENAI_SDK_UNAVAILABLE",
            "approved_model_catalog_lineage": (
                self._catalog_reason(authority) or "V2_VEO_CATALOG_INVALID"
            ),
            "media_runtime_ready": "V2_VEO_MEDIA_RUNTIME_UNAVAILABLE",
            "execution_authority_ready": "V2_VEO_EXECUTION_AUTHORITY_BLOCKED",
            "no_fallback_policy": "V2_VEO_FALLBACK_POLICY_INVALID",
            "runtime_settings_bound": "V2_VEO_RUNTIME_SETTINGS_REQUIRED",
            "runtime_model_exact": "V2_VEO_RUNTIME_MODEL_MISMATCH",
            "runtime_duration_exact": "V2_VEO_RUNTIME_DURATION_MISMATCH",
            "runtime_resolution_exact": "V2_VEO_RUNTIME_RESOLUTION_MISMATCH",
            "runtime_aspect_ratio_exact": "V2_VEO_RUNTIME_ASPECT_RATIO_MISMATCH",
            "runtime_output_count_exact": "V2_VEO_RUNTIME_OUTPUT_COUNT_MISMATCH",
            "runtime_global_execution_enabled": "V2_VEO_RUNTIME_GLOBAL_EXECUTION_DISABLED",
            "runtime_production_execution_enabled": "V2_VEO_RUNTIME_PRODUCTION_EXECUTION_DISABLED",
            "runtime_veo_generation_enabled": "V2_VEO_RUNTIME_GENERATION_DISABLED",
            "runtime_credential_configured": "V2_VEO_RUNTIME_CREDENTIAL_MISSING",
            "production_adapter_registered": "V2_VEO_PRODUCTION_ADAPTER_NOT_REGISTERED",
        }
        reasons = [reason_by_check[key] for key, passed in checks.items() if not passed]
        reasons.extend(execution.blockers)
        reasons.extend(sdk_runtime_blockers)
        reasons.extend(str(item) for item in runtime.get("blockers") or [])
        return V2VeoProductionReadiness(
            ready=all(checks.values()),
            checks=checks,
            blocker_reason_codes=tuple(sorted(set(reasons))),
        )

    def prepare(self, authority: V2VeoGenerationAuthority) -> V2VeoEffectRecord:
        catalog_reason = self._catalog_reason(authority)
        if catalog_reason:
            raise V2VeoProviderBlocked(catalog_reason)
        journal = authority.request_journal(self._clock())
        record = self.store.load_or_prepare(
            asset_effect_id=authority.asset_effect_id,
            identity_hash=authority.identity_hash,
            request_hash=authority.request_hash,
            authority=authority,
            request_journal=journal,
        )
        self._assert_record_identity(record, authority)
        return record

    def submit_once(
        self,
        *,
        authority: V2VeoGenerationAuthority,
        execution: V2VeoExecutionAuthorization,
    ) -> V2VeoEffectRecord:
        record = self.prepare(authority)
        if record.provider_operation_id:
            return record
        if record.state in {"VERIFIED", "FAILED_DEFINITIVE", "BLOCKED"}:
            return record
        if record.state in {"SUBMITTING", "FAILED_UNCERTAIN"}:
            return self._block_unknown_submit(record, authority=authority)
        if record.generation_attempt_count > 0:
            raise V2VeoProviderBlocked(
                "V2_VEO_GENERATION_ATTEMPT_ALREADY_CONSUMED",
                "V2_VEO_AUTOMATIC_RESUBMIT_FORBIDDEN",
            )
        if record.state != "PREPARED":
            raise V2VeoProviderBlocked("V2_VEO_EFFECT_NOT_SUBMITTABLE")
        readiness = self.readiness(authority=authority, execution=execution)
        if not readiness.ready:
            raise V2VeoProviderBlocked(*readiness.blocker_reason_codes)
        record = self._transition(
            record,
            expected_states=frozenset({"PREPARED"}),
            new_state="SUBMITTING",
            generation_attempt_count=1,
            submitted_at=self._clock(),
            last_error_code=None,
        )
        try:
            submission = self.client.submit_once(authority)
            operation_id = _validate_operation_id(submission.provider_operation_id)
        except V2VeoDefinitiveProviderError as exc:
            return self._transition(
                record,
                expected_states=frozenset({"SUBMITTING"}),
                new_state="FAILED_DEFINITIVE",
                last_error_code=exc.code,
                actual_cost_usd=Decimal("0"),
                cost_settlement_basis="DEFINITIVE_REJECTION_NO_OPERATION",
                completed_at=self._clock(),
                response_journals=self._append_response(
                    record,
                    event="SUBMIT_DEFINITIVE_FAILURE",
                    provider_status="REJECTED",
                    provider_error_code=exc.code,
                ),
            )
        except Exception as exc:
            self._transition(
                record,
                expected_states=frozenset({"SUBMITTING"}),
                new_state="FAILED_UNCERTAIN",
                last_error_code="V2_VEO_SUBMIT_OUTCOME_UNCERTAIN",
                actual_cost_usd=None,
                conservative_settlement_cost_usd=authority.estimated_cost_usd,
                cost_settlement_basis=("CONSERVATIVE_CATALOG_ESTIMATE_UNCERTAIN"),
                completed_at=self._clock(),
                response_journals=self._append_response(
                    record,
                    event="SUBMIT_OUTCOME_UNCERTAIN",
                    provider_status="UNKNOWN",
                    provider_error_code=type(exc).__name__,
                ),
            )
            raise V2VeoProviderBlocked(
                "V2_VEO_SUBMIT_OUTCOME_UNCERTAIN",
                "V2_VEO_AUTOMATIC_RESUBMIT_FORBIDDEN",
            ) from exc
        response = self._append_response(
            record,
            event="SUBMIT_ACCEPTED",
            provider_status=submission.provider_status,
            provider_operation_id=operation_id,
            provider_response_id=submission.provider_response_id,
        )
        try:
            return self._transition(
                record,
                expected_states=frozenset({"SUBMITTING"}),
                new_state="OPERATION_RECORDED",
                provider_operation_id=operation_id,
                provider_response_id=submission.provider_response_id,
                actual_cost_usd=None,
                conservative_settlement_cost_usd=authority.estimated_cost_usd,
                cost_settlement_basis="CONSERVATIVE_CATALOG_ESTIMATE_ACCEPTED",
                response_journals=response,
            )
        except Exception as exc:
            raise V2VeoOperationPersistenceError(operation_id) from exc

    def reconcile_exact_operation(
        self,
        *,
        authority: V2VeoGenerationAuthority,
        provider_operation_id: str,
        recovery_authority_ref: str,
        recovery_authority_hash: str,
    ) -> V2VeoEffectRecord:
        """Attach an operator-recovered operation identity, never resubmit."""

        record = self.prepare(authority)
        operation_id = _validate_operation_id(provider_operation_id)
        if not recovery_authority_ref.strip() or not _is_sha256(
            recovery_authority_hash
        ):
            raise V2VeoProviderBlocked("V2_VEO_RECOVERY_AUTHORITY_INVALID")
        if record.provider_operation_id:
            if record.provider_operation_id != operation_id:
                raise V2VeoProviderBlocked("V2_VEO_OPERATION_IDENTITY_CONFLICT")
            return self.poll_once(authority=authority)
        if record.generation_attempt_count != 1 or record.state not in {
            "SUBMITTING",
            "FAILED_UNCERTAIN",
        }:
            raise V2VeoProviderBlocked("V2_VEO_OPERATION_RECOVERY_NOT_AUTHORIZED")
        record = self._transition(
            record,
            expected_states=frozenset({"SUBMITTING", "FAILED_UNCERTAIN"}),
            new_state="OPERATION_RECORDED",
            provider_operation_id=operation_id,
            last_error_code=None,
            completed_at=None,
            actual_cost_usd=None,
            conservative_settlement_cost_usd=authority.estimated_cost_usd,
            cost_settlement_basis="CONSERVATIVE_CATALOG_ESTIMATE_ACCEPTED",
            response_journals=self._append_response(
                record,
                event="OPERATION_IDENTITY_RECOVERED",
                provider_status="RECOVERED",
                provider_operation_id=operation_id,
                recovery_authority_ref=recovery_authority_ref,
                recovery_authority_hash=recovery_authority_hash,
            ),
        )
        return self.poll_once(authority=authority)

    def poll_once(self, *, authority: V2VeoGenerationAuthority) -> V2VeoEffectRecord:
        record = self.prepare(authority)
        if record.state in {
            "VERIFIED",
            "RESPONSE_CAPTURED",
            "DOWNLOADED",
            "NORMALIZED",
        }:
            return record
        if not record.provider_operation_id:
            if record.generation_attempt_count > 0:
                return self._block_unknown_submit(record, authority=authority)
            raise V2VeoProviderBlocked("V2_VEO_OPERATION_IDENTITY_MISSING")
        if record.state in {"FAILED_DEFINITIVE", "BLOCKED", "FAILED_UNCERTAIN"}:
            raise V2VeoProviderBlocked("V2_VEO_EFFECT_TERMINAL")
        operation_id = _validate_operation_id(record.provider_operation_id)
        try:
            snapshot = self.client.poll_exact(operation_id)
        except Exception as exc:
            self._transition(
                record,
                expected_states=frozenset(
                    {
                        "OPERATION_RECORDED",
                        "POLLING",
                    }
                ),
                new_state=record.state,
                last_error_code="V2_VEO_POLL_EXTERNAL_FAILURE",
                response_journals=self._append_response(
                    record,
                    event="POLL_EXTERNAL_FAILURE",
                    provider_status="UNKNOWN",
                    provider_operation_id=operation_id,
                    provider_error_code=type(exc).__name__,
                ),
            )
            raise V2VeoProviderBlocked("V2_VEO_POLL_EXTERNAL_FAILURE") from exc
        if snapshot.provider_operation_id != operation_id:
            raise V2VeoProviderBlocked("V2_VEO_POLL_OPERATION_ID_MISMATCH")
        event = "POLL_PROCESSING"
        new_state: V2VeoEffectState = "POLLING"
        error_code = snapshot.provider_error_code
        if snapshot.done and snapshot.succeeded and snapshot.output_available:
            event, new_state = "POLL_SUCCEEDED", "RESPONSE_CAPTURED"
        elif snapshot.done:
            event, new_state = "POLL_DEFINITIVE_FAILURE", "FAILED_DEFINITIVE"
            error_code = error_code or "V2_VEO_PROVIDER_OPERATION_FAILED"
        terminal_patch: dict[str, Any] = {}
        if new_state == "RESPONSE_CAPTURED":
            terminal_patch["response_captured_at"] = self._clock()
        elif new_state == "FAILED_DEFINITIVE":
            terminal_patch["completed_at"] = self._clock()
        return self._transition(
            record,
            expected_states=frozenset({"OPERATION_RECORDED", "POLLING"}),
            new_state=new_state,
            last_error_code=error_code,
            response_journals=self._append_response(
                record,
                event=event,
                provider_status=snapshot.provider_status,
                provider_operation_id=operation_id,
                provider_error_code=error_code,
            ),
            **terminal_patch,
        )

    def materialize(self, *, authority: V2VeoGenerationAuthority) -> V2VeoEffectRecord:
        """Download one exact operation, remove audio, and verify actual bytes."""

        record = self.prepare(authority)
        if record.state == "VERIFIED":
            recorded = self._verify_recorded_file(
                record.normalized_output_ref, record.normalized_output_sha256
            )
            expected = (
                self._asset_directory(authority.asset_effect_id)
                / "google-veo-visual-only.mp4"
            )
            if recorded.resolve() != expected.resolve():
                raise V2VeoProviderBlocked("V2_VEO_NORMALIZED_OUTPUT_REF_MISMATCH")
            return record
        if record.state in {"FAILED_DEFINITIVE", "FAILED_UNCERTAIN", "BLOCKED"}:
            raise V2VeoProviderBlocked("V2_VEO_EFFECT_TERMINAL")
        if not record.provider_operation_id:
            raise V2VeoProviderBlocked("V2_VEO_OPERATION_IDENTITY_MISSING")
        if record.state not in {"RESPONSE_CAPTURED", "DOWNLOADED", "NORMALIZED"}:
            raise V2VeoProviderBlocked("V2_VEO_OUTPUT_NOT_READY")
        width, height = authority.dimensions
        asset_dir = self._asset_directory(authority.asset_effect_id)
        raw_path = asset_dir / "google-veo-provider-output.mp4"
        normalized_path = asset_dir / "google-veo-visual-only.mp4"
        if record.state == "RESPONSE_CAPTURED":
            try:
                downloaded = self.client.download_exact(record.provider_operation_id)
            except Exception as exc:
                self._transition(
                    record,
                    expected_states=frozenset({"RESPONSE_CAPTURED"}),
                    new_state="RESPONSE_CAPTURED",
                    last_error_code="V2_VEO_EXACT_DOWNLOAD_FAILED",
                    response_journals=self._append_response(
                        record,
                        event="EXACT_DOWNLOAD_FAILED",
                        provider_status="SUCCEEDED",
                        provider_operation_id=record.provider_operation_id,
                        provider_error_code=type(exc).__name__,
                    ),
                )
                raise V2VeoProviderBlocked("V2_VEO_EXACT_DOWNLOAD_FAILED") from exc
            raw_sha = _seal_bytes(raw_path, downloaded.content)
            record = self._transition(
                record,
                expected_states=frozenset({"RESPONSE_CAPTURED"}),
                new_state="DOWNLOADED",
                provider_response_id=(
                    downloaded.provider_response_id or record.provider_response_id
                ),
                raw_output_ref=self._relative_ref(raw_path),
                raw_output_sha256=raw_sha,
                raw_output_size_bytes=raw_path.stat().st_size,
                last_error_code=None,
                response_journals=self._append_response(
                    record,
                    event="EXACT_OUTPUT_DOWNLOADED",
                    provider_status="SUCCEEDED",
                    provider_operation_id=record.provider_operation_id,
                    provider_response_id=downloaded.provider_response_id,
                    output_sha256=raw_sha,
                    output_size_bytes=raw_path.stat().st_size,
                    content_type=downloaded.content_type,
                ),
            )
        if record.state == "DOWNLOADED":
            recorded_raw_path = self._verify_recorded_file(
                record.raw_output_ref, record.raw_output_sha256
            )
            if recorded_raw_path.resolve() != raw_path.resolve():
                raise V2VeoProviderBlocked("V2_VEO_RAW_OUTPUT_REF_MISMATCH")
            try:
                receipt = self.media_runtime.normalize_visual_only(
                    source=recorded_raw_path,
                    destination=normalized_path,
                    width=width,
                    height=height,
                    fps=authority.fps,
                )
            except Exception as exc:
                return self._transition(
                    record,
                    expected_states=frozenset({"DOWNLOADED"}),
                    new_state="BLOCKED",
                    last_error_code="V2_VEO_NORMALIZATION_FAILED",
                    completed_at=self._clock(),
                    response_journals=self._append_response(
                        record,
                        event="NORMALIZATION_FAILED",
                        provider_status="SUCCEEDED",
                        provider_operation_id=record.provider_operation_id,
                        provider_error_code=type(exc).__name__,
                    ),
                )
            if receipt.contains_audio_stream or not receipt.provider_audio_discarded:
                return self._transition(
                    record,
                    expected_states=frozenset({"DOWNLOADED"}),
                    new_state="BLOCKED",
                    normalization_receipt=receipt.as_journal(),
                    last_error_code="V2_VEO_PROVIDER_AUDIO_NOT_DISCARDED",
                    completed_at=self._clock(),
                )
            record = self._transition(
                record,
                expected_states=frozenset({"DOWNLOADED"}),
                new_state="NORMALIZED",
                normalized_output_ref=self._relative_ref(normalized_path),
                normalized_output_sha256=receipt.output_sha256,
                normalized_output_size_bytes=receipt.output_size_bytes,
                output_content_type="video/mp4",
                output_width=receipt.width,
                output_height=receipt.height,
                output_fps=Decimal(receipt.fps),
                output_audio_stream_count=receipt.output_audio_stream_count,
                normalization_receipt=receipt.as_journal(),
            )
        if record.state != "NORMALIZED":
            raise V2VeoProviderBlocked("V2_VEO_NORMALIZATION_STATE_INVALID")
        recorded_normalized_path = self._verify_recorded_file(
            record.normalized_output_ref, record.normalized_output_sha256
        )
        if recorded_normalized_path.resolve() != normalized_path.resolve():
            raise V2VeoProviderBlocked("V2_VEO_NORMALIZED_OUTPUT_REF_MISMATCH")
        try:
            qc = self.media_runtime.inspect(
                asset=recorded_normalized_path,
                expected_width=width,
                expected_height=height,
                expected_fps=authority.fps,
                expected_duration_seconds=float(authority.duration_seconds),
            )
        except Exception:
            qc = V2VeoVideoQCReceipt(
                result="FAIL",
                checks={
                    "inspection_completed": False,
                    "provider_audio_discarded": bool(
                        record.normalization_receipt.get("provider_audio_discarded")
                    ),
                },
                reason_codes=("V2_VEO_QC_INSPECTION_FAILED",),
                asset_sha256=_sha256_file(recorded_normalized_path),
            )
        qc_journal = qc.as_journal()
        if qc.asset_sha256 != record.normalized_output_sha256 or qc.result != "PASS":
            return self._transition(
                record,
                expected_states=frozenset({"NORMALIZED"}),
                new_state="BLOCKED",
                qc_receipt=qc_journal,
                completed_at=self._clock(),
                last_error_code=(
                    qc.reason_codes[0]
                    if qc.reason_codes
                    else "V2_VEO_QC_CHECKSUM_MISMATCH"
                ),
            )
        return self._transition(
            record,
            expected_states=frozenset({"NORMALIZED"}),
            new_state="VERIFIED",
            qc_receipt=qc_journal,
            output_duration_ms=int(
                round(float(qc.checks.get("duration_seconds") or 0) * 1_000)
            ),
            actual_cost_usd=None,
            conservative_settlement_cost_usd=authority.estimated_cost_usd,
            cost_settlement_basis="CONSERVATIVE_CATALOG_ESTIMATE_ACCEPTED",
            production_eligible=True,
            completed_at=self._clock(),
            last_error_code=None,
        )

    def _catalog_reason(self, authority: V2VeoGenerationAuthority) -> str | None:
        if self.catalog.payload.get("status") != "active":
            return "V2_VEO_CATALOG_INACTIVE"
        if authority.catalog_version != self.catalog.version:
            return "V2_VEO_CATALOG_VERSION_MISMATCH"
        if authority.catalog_ref != self.catalog.ref:
            return "V2_VEO_CATALOG_REF_MISMATCH"
        if authority.catalog_hash != stable_hash(self.catalog.payload):
            return "V2_VEO_CATALOG_HASH_MISMATCH"
        try:
            model = self.catalog.model(authority.model_id)
            if model.get(
                "transport"
            ) != "GEMINI_API_NATIVE" or authority.aspect_ratio not in set(
                model.get("aspect_ratios") or []
            ):
                return "V2_VEO_CATALOG_TRANSPORT_PROFILE_INVALID"
            estimate = self.catalog.estimate(
                model_id=authority.model_id,
                resolution=authority.resolution,
                duration_seconds=authority.duration_seconds,
                output_count=authority.output_count,
                hard_cap=authority.maximum_approved_cost_usd,
                approval_amount=authority.maximum_approved_cost_usd,
            )
        except ValueError:
            return "V2_VEO_CATALOG_ESTIMATE_INVALID"
        if estimate.estimated_amount != authority.estimated_cost_usd:
            return "V2_VEO_CATALOG_ESTIMATE_MISMATCH"
        return None

    def _assert_record_identity(
        self,
        record: V2VeoEffectRecord,
        authority: V2VeoGenerationAuthority,
    ) -> None:
        operation_required = record.state in {
            "OPERATION_RECORDED",
            "POLLING",
            "RESPONSE_CAPTURED",
            "DOWNLOADED",
            "NORMALIZED",
            "VERIFIED",
        }
        normalized_required = record.state in {"NORMALIZED", "VERIFIED"}
        terminal = record.state in {
            "VERIFIED",
            "FAILED_DEFINITIVE",
            "FAILED_UNCERTAIN",
            "BLOCKED",
        }
        if record.cost_settlement_basis is None:
            cost_evidence_valid = (
                record.actual_cost_usd is None
                and record.conservative_settlement_cost_usd is None
            )
        elif record.cost_settlement_basis.startswith("CONSERVATIVE_"):
            cost_evidence_valid = (
                record.actual_cost_usd is None
                and record.conservative_settlement_cost_usd
                == authority.estimated_cost_usd
            )
        else:
            cost_evidence_valid = (
                record.cost_settlement_basis == "DEFINITIVE_REJECTION_NO_OPERATION"
                and record.actual_cost_usd == Decimal("0")
                and record.conservative_settlement_cost_usd is None
            )
        if (
            record.asset_effect_id != authority.asset_effect_id
            or record.identity_hash != authority.identity_hash
            or record.request_hash != authority.request_hash
            or dict(record.authority) != authority.identity_payload
            or record.request_journal.get("identity_hash") != authority.identity_hash
            or record.request_journal.get("request_hash") != authority.request_hash
            or not _hashed_journal_valid(record.request_journal, "journal_hash")
            or any(
                not _hashed_journal_valid(journal, "journal_hash")
                for journal in record.response_journals
            )
            or record.version < 1
            or record.generation_attempt_count not in {0, 1}
            or record.prepared_at is None
            or (record.generation_attempt_count == 0 and record.state != "PREPARED")
            or (record.generation_attempt_count == 1 and record.submitted_at is None)
            or (
                record.provider_operation_id is not None
                and not _valid_operation_id(record.provider_operation_id)
            )
            or (operation_required and not record.provider_operation_id)
            or (
                record.state
                in {"RESPONSE_CAPTURED", "DOWNLOADED", "NORMALIZED", "VERIFIED"}
                and record.response_captured_at is None
            )
            or (
                normalized_required
                and (
                    not record.normalized_output_ref
                    or not _is_sha256(record.normalized_output_sha256)
                    or not _hashed_journal_valid(
                        record.normalization_receipt, "normalization_hash"
                    )
                    or record.normalized_output_size_bytes is None
                    or record.normalized_output_size_bytes <= 0
                    or record.output_audio_stream_count != 0
                )
            )
            or (
                record.state == "VERIFIED"
                and (
                    not _hashed_journal_valid(record.qc_receipt, "qc_hash")
                    or record.qc_receipt.get("result") != "PASS"
                    or not record.output_duration_ms
                    or record.completed_at is None
                )
            )
            or (terminal and record.completed_at is None)
            or record.production_eligible != (record.state == "VERIFIED")
            or not cost_evidence_valid
        ):
            raise V2VeoProviderBlocked("V2_VEO_DURABLE_IDENTITY_MISMATCH")

    def _block_unknown_submit(
        self,
        record: V2VeoEffectRecord,
        *,
        authority: V2VeoGenerationAuthority,
    ) -> V2VeoEffectRecord:
        if record.provider_operation_id:
            return record
        if record.state != "FAILED_UNCERTAIN":
            record = self._transition(
                record,
                expected_states=frozenset({"SUBMITTING"}),
                new_state="FAILED_UNCERTAIN",
                last_error_code="V2_VEO_SUBMIT_OUTCOME_UNCERTAIN",
                actual_cost_usd=None,
                conservative_settlement_cost_usd=authority.estimated_cost_usd,
                cost_settlement_basis=("CONSERVATIVE_CATALOG_ESTIMATE_UNCERTAIN"),
                completed_at=self._clock(),
            )
        raise V2VeoProviderBlocked(
            "V2_VEO_SUBMIT_OUTCOME_UNCERTAIN",
            "V2_VEO_AUTOMATIC_RESUBMIT_FORBIDDEN",
            "V2_VEO_EXACT_OPERATION_ID_REQUIRED",
        )

    def _transition(
        self,
        record: V2VeoEffectRecord,
        *,
        expected_states: frozenset[str],
        new_state: V2VeoEffectState,
        **patch: Any,
    ) -> V2VeoEffectRecord:
        result = self.store.compare_and_set(
            asset_effect_id=record.asset_effect_id,
            expected_version=record.version,
            expected_states=expected_states,
            new_state=new_state,
            patch=patch,
        )
        return result

    def _append_response(
        self,
        record: V2VeoEffectRecord,
        *,
        event: str,
        provider_status: str,
        provider_operation_id: str | None = None,
        provider_response_id: str | None = None,
        provider_error_code: str | None = None,
        **evidence: Any,
    ) -> tuple[Mapping[str, Any], ...]:
        payload = {
            "schema_version": V2_VEO_RESPONSE_JOURNAL_SCHEMA,
            "recorded_at": self._clock().isoformat(),
            "event": event,
            "request_hash": record.request_hash,
            "provider_status": str(provider_status)[:120],
            "provider_operation_id": provider_operation_id,
            "provider_response_id": (
                str(provider_response_id)[:240] if provider_response_id else None
            ),
            "provider_error_code": (
                str(provider_error_code)[:120] if provider_error_code else None
            ),
            "raw_provider_response_persisted": False,
            "raw_output_url_persisted": False,
            "secret_values_exposed": False,
            **evidence,
        }
        payload["journal_hash"] = stable_hash(payload)
        return (*record.response_journals, payload)

    def _asset_directory(self, asset_effect_id: str) -> Path:
        digest = hashlib.sha256(asset_effect_id.encode("utf-8")).hexdigest()
        path = (self.workspace_root / "ai-visual-assets" / "veo" / digest).resolve()
        if self.workspace_root not in path.parents:
            raise V2VeoProviderBlocked("V2_VEO_WORKSPACE_PATH_INVALID")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _relative_ref(self, path: Path) -> str:
        resolved = path.resolve()
        if self.workspace_root not in resolved.parents:
            raise V2VeoProviderBlocked("V2_VEO_WORKSPACE_PATH_INVALID")
        return resolved.relative_to(self.workspace_root).as_posix()

    def _verify_recorded_file(
        self, relative_ref: str | None, expected_sha256: str | None
    ) -> Path:
        if not relative_ref or not _is_sha256(expected_sha256):
            raise V2VeoProviderBlocked("V2_VEO_RECORDED_FILE_IDENTITY_MISSING")
        candidate = self.workspace_root / relative_ref
        if ".." in Path(relative_ref).parts:
            raise V2VeoProviderBlocked("V2_VEO_RECORDED_FILE_PATH_INVALID")
        resolved = candidate.resolve()
        if (
            self.workspace_root not in resolved.parents
            or not candidate.is_file()
            or candidate.is_symlink()
            or _sha256_file(candidate) != expected_sha256
        ):
            raise V2VeoProviderBlocked("V2_VEO_RECORDED_FILE_MISMATCH")
        return candidate


def _google_genai_total_attempts(client: Any) -> int | None:
    api_client = getattr(client, "_api_client", None)
    http_options = getattr(api_client, "_http_options", None)
    retry_options = getattr(http_options, "retry_options", None)
    attempts = getattr(retry_options, "attempts", None)
    return (
        attempts
        if isinstance(attempts, int) and not isinstance(attempts, bool)
        else None
    )


def _generated_videos(operation: Any) -> list[Any]:
    result = getattr(operation, "response", None) or getattr(operation, "result", None)
    return list(getattr(result, "generated_videos", None) or [])


def _safe_provider_error_code(error: Any) -> str:
    raw: Any = None
    if isinstance(error, Mapping):
        raw = error.get("code")
    else:
        raw = getattr(error, "code", None)
    value = str(raw or "VEO_PROVIDER_ERROR")[:120]
    return (
        "".join(
            character
            for character in value
            if character.isalnum() or character in "._-"
        )
        or "VEO_PROVIDER_ERROR"
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _hashed_journal_valid(journal: Mapping[str, Any], hash_key: str) -> bool:
    payload = dict(journal)
    observed = payload.pop(hash_key, None)
    return _is_sha256(observed) and observed == stable_hash(payload)


def _valid_operation_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 500
        and all(character in _OPERATION_ID_CHARACTERS for character in value)
    )


def _validate_operation_id(value: str) -> str:
    if not _valid_operation_id(value):
        raise ValueError("V2_VEO_PROVIDER_OPERATION_ID_INVALID")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _seal_bytes(destination: Path, content: bytes) -> str:
    if not content:
        raise ValueError("V2_VEO_OUTPUT_BYTES_EMPTY")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    if destination.exists():
        if (
            destination.is_symlink()
            or not destination.is_file()
            or _sha256_file(destination) != digest
        ):
            raise RuntimeError("V2_VEO_EXISTING_OUTPUT_CONFLICT")
        return digest
    part = destination.with_name(destination.name + ".part")
    part.unlink(missing_ok=True)
    try:
        with part.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(part, destination)
    finally:
        part.unlink(missing_ok=True)
    return digest


def _video_stream(probe: Mapping[str, Any]) -> Mapping[str, Any]:
    stream = next(
        (
            item
            for item in list(probe.get("streams") or [])
            if isinstance(item, Mapping) and item.get("codec_type") == "video"
        ),
        None,
    )
    if stream is None:
        raise RuntimeError("V2_VEO_VIDEO_STREAM_MISSING")
    return stream


def _fps(value: Any) -> float | None:
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    return float(rate) if rate > 0 else None


def _duration_seconds(probe: Mapping[str, Any], video: Mapping[str, Any]) -> float:
    raw = (probe.get("format") or {}).get("duration")
    if raw in {None, "", "N/A"}:
        raw = video.get("duration")
    try:
        duration = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("V2_VEO_DURATION_MISSING") from exc
    if duration <= 0:
        raise RuntimeError("V2_VEO_DURATION_INVALID")
    return duration


def _qc_probe_seconds(duration: float) -> list[float]:
    margin = min(0.4, max(0.04, duration * 0.05))
    return [
        round(max(0.0, margin), 6),
        round(max(0.0, duration * 0.25), 6),
        round(max(0.0, duration * 0.50), 6),
        round(max(0.0, duration * 0.75), 6),
        round(max(0.0, duration - margin), 6),
    ]


def _evaluate_video_frames(frames: Sequence[bytes]) -> dict[str, Any]:
    expected_size = 96 * 54
    valid = bool(frames) and all(len(frame) == expected_size for frame in frames)
    if not valid:
        return {
            "sampled_frames_valid": False,
            "sampled_frame_count": len(frames),
            "not_blank": False,
            "mostly_black_absent": False,
            "not_frozen_throughout": False,
            "unique_frame_sha256_count": 0,
            "maximum_frame_delta": 0.0,
        }
    means = [sum(frame) / len(frame) for frame in frames]
    spans = [max(frame) - min(frame) for frame in frames]
    hashes = [hashlib.sha256(frame).hexdigest() for frame in frames]
    deltas = [
        sum(abs(left - right) for left, right in zip(frames[index - 1], frame))
        / len(frame)
        for index, frame in enumerate(frames[1:], start=1)
    ]
    dark_count = sum(mean < 8.0 for mean in means)
    return {
        "sampled_frames_valid": True,
        "sampled_frame_count": len(frames),
        "sampled_frame_sha256": hashes,
        "sampled_mean_luma": [round(value, 3) for value in means],
        "sampled_luma_span": spans,
        "not_blank": any(span >= 8 for span in spans),
        "mostly_black_absent": dark_count < max(1, round(len(frames) * 0.8)),
        "not_frozen_throughout": len(set(hashes)) > 1 and max(deltas or [0]) >= 0.75,
        "unique_frame_sha256_count": len(set(hashes)),
        "maximum_frame_delta": round(max(deltas or [0]), 3),
    }
