"""Crash-safe production boundary for V2 Gemini still-image effects.

This module is deliberately separate from the historical IMG canary adapter.
It accepts a code-injected official SDK client, but it never constructs one as
an implicit execution fallback and never promotes canary approval evidence.
The service commits a durable ``SUBMITTING`` claim before reaching the SDK and
persists a redacted response journal plus the inline raster before any raster
parsing or QC.  An ambiguous post-submit outcome is terminal for the current
effect: reconciliation may finish an already captured response, but it may not
issue a second provider request.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib
import json
import os
import re
import shutil
import statistics
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.ai_visual_production import (
    AIVisualScenePlan,
    CompiledAIImagePrompt,
    VideoVisualStyleBible,
)
from app.core.config import (
    GEMINI_IMAGE_DEFAULT_ASPECT_RATIO,
    GEMINI_IMAGE_DEFAULT_MODEL_ID,
    GEMINI_IMAGE_DEFAULT_SIZE,
    GEMINI_IMAGE_MAX_ATTEMPTS_PER_SCENE,
    GEMINI_IMAGE_MAX_OUTPUTS,
    Settings,
    get_settings,
)
from app.services.google_gemini_image_catalog import (
    GoogleGeminiImageModelPriceCatalog,
)


V2_GEMINI_IMAGE_PRODUCTION_ADAPTER_KEY = "v2-google-gemini-image"
V2_GEMINI_IMAGE_PROVIDER_KEY = "google_gemini_image"
V2_GEMINI_IMAGE_PROVIDER_CONFIG_VERSION = "vcos.v2-gemini-image-production.v1"
V2_AI_VISUAL_POLICY_VERSION = "vcos.production-visual-policy.ai-only.v1"
V2_GEMINI_IMAGE_EFFECT_SCHEMA = "vcos.v2-ai-image-scene-effect.v1"
V2_GEMINI_IMAGE_REQUEST_JOURNAL_SCHEMA = "vcos.v2-gemini-image-request-journal.v1"
V2_GEMINI_IMAGE_CAPTURE_SCHEMA = "vcos.v2-gemini-image-response-capture.v1"
V2_GEMINI_IMAGE_SEMANTIC_ATTESTATION_SCHEMA = (
    "vcos.v2-ai-image-asset-semantic-attestation.v1"
)
V2_GEMINI_IMAGE_QC_SCHEMA = "vcos.v2-ai-image-technical-qc.v1"
V2_GEMINI_IMAGE_ASSET_SCHEMA = "vcos.v2-ai-image-asset-receipt.v1"
V2_GEMINI_IMAGE_FAILURE_SCHEMA = "vcos.v2-ai-image-failure.v1"
V2_GEMINI_IMAGE_READINESS_SCHEMA = "vcos.v2-gemini-image-readiness.v1"
V2_GEMINI_IMAGE_DB_RECORD_SCHEMA = "vcos.ai-visual-image-db-record.v1"
V2_GEMINI_IMAGE_PINNED_SDK_DISTRIBUTION = "google-genai"
V2_GEMINI_IMAGE_PINNED_SDK_VERSION = "2.10.0"
V2_GEMINI_IMAGE_REQUEST_TIMEOUT_SECONDS = 120.0
V2_GEMINI_IMAGE_SUBMISSION_LEASE_SECONDS = 180
V2_GEMINI_IMAGE_SAFE_DECODE_TIMEOUT_SECONDS = 30.0
V2_GEMINI_IMAGE_DECODER_PROBE_TIMEOUT_SECONDS = 10.0
V2_GEMINI_IMAGE_MAX_RASTER_BYTES = 64 * 1024 * 1024
V2_GEMINI_IMAGE_MAX_RASTER_PIXELS = 16_777_216
V2_GEMINI_IMAGE_MINIMUM_WIDTH = 1920
V2_GEMINI_IMAGE_MINIMUM_HEIGHT = 1080
V2_GEMINI_IMAGE_ASPECT_TOLERANCE_PERCENT = 1
V2_GEMINI_IMAGE_SAMPLE_WIDTH = 64
V2_GEMINI_IMAGE_SAMPLE_HEIGHT = 36
V2_GEMINI_IMAGE_MINIMUM_LUMA_MEAN = 8.0
V2_GEMINI_IMAGE_MINIMUM_LUMA_STDDEV = 3.0
V2_GEMINI_IMAGE_MINIMUM_LUMA_RANGE = 12
V2_GEMINI_IMAGE_MAXIMUM_BLACK_FRACTION = 0.97
V2_GEMINI_IMAGE_MAX_PROVIDER_INPUT_BYTES = 12_000
V2_GEMINI_IMAGE_MAX_OUTPUT_TOKENS = 3_000
V2_GEMINI_IMAGE_2K_OUTPUT_TOKENS = 1_680
V2_GEMINI_IMAGE_INPUT_PRICE_PER_MILLION_TOKENS_USD = Decimal("0.500000")
V2_GEMINI_IMAGE_TEXT_THINKING_PRICE_PER_MILLION_TOKENS_USD = Decimal("3.000000")
V2_GEMINI_IMAGE_SEMANTIC_TOKEN_ALLOWANCE_USD = Decimal("0.010000")
V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD = Decimal("0.111000")

SHA256_PATTERN = r"^[0-9a-f]{64}$"
SAFE_CODE_PATTERN = r"^[A-Z0-9_]{1,120}$"
SAFE_PROVIDER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class V2AIImageEffectState(StrEnum):
    PREPARED = "PREPARED"
    SUBMITTING = "SUBMITTING"
    RESPONSE_CAPTURED = "RESPONSE_CAPTURED"
    VERIFIED = "VERIFIED"
    FAILED_DEFINITIVE = "FAILED_DEFINITIVE"
    FAILED_UNCERTAIN = "FAILED_UNCERTAIN"


class V2AIImageProviderBoundaryError(RuntimeError):
    """Base error whose message is a durable-safe reason code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class V2AIImageExecutionBlocked(V2AIImageProviderBoundaryError):
    """No provider call was made because readiness or identity failed."""


class V2AIImageOutcomeUncertain(V2AIImageProviderBoundaryError):
    """A provider call may have succeeded; automatic retry is forbidden."""


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("V2_AI_IMAGE_AWARE_DATETIME_REQUIRED")
    return value.astimezone(UTC)


def _sealed_hash(payload: dict[str, Any]) -> str:
    return ai_image_stable_hash(payload)


def _provider_config_payload() -> dict[str, Any]:
    return {
        "provider_config_version": V2_GEMINI_IMAGE_PROVIDER_CONFIG_VERSION,
        "adapter_key": V2_GEMINI_IMAGE_PRODUCTION_ADAPTER_KEY,
        "provider_key": V2_GEMINI_IMAGE_PROVIDER_KEY,
        "transport": "GEMINI_API_NATIVE_INTERACTIONS",
        "official_sdk_distribution": V2_GEMINI_IMAGE_PINNED_SDK_DISTRIBUTION,
        "official_sdk_version": V2_GEMINI_IMAGE_PINNED_SDK_VERSION,
        "model_id": GEMINI_IMAGE_DEFAULT_MODEL_ID,
        "image_size": GEMINI_IMAGE_DEFAULT_SIZE,
        "aspect_ratio": GEMINI_IMAGE_DEFAULT_ASPECT_RATIO,
        "output_count": 1,
        "response_mime_type": "image/jpeg",
        "same_interaction_semantic_attestation_required": True,
        "semantic_attestation_schema": (V2_GEMINI_IMAGE_SEMANTIC_ATTESTATION_SCHEMA),
        "semantic_attestation_independent_inspection": False,
        "inline_delivery_required": True,
        "store": False,
        "stream": False,
        "background": False,
        "request_timeout_seconds": V2_GEMINI_IMAGE_REQUEST_TIMEOUT_SECONDS,
        "maximum_provider_input_bytes": V2_GEMINI_IMAGE_MAX_PROVIDER_INPUT_BYTES,
        "maximum_output_tokens": V2_GEMINI_IMAGE_MAX_OUTPUT_TOKENS,
        "thinking_level": "minimal",
        "semantic_token_cost_allowance_usd": str(
            V2_GEMINI_IMAGE_SEMANTIC_TOKEN_ALLOWANCE_USD
        ),
        "semantic_token_cost_upper_bound_usd": str(
            (
                Decimal(V2_GEMINI_IMAGE_MAX_PROVIDER_INPUT_BYTES)
                * V2_GEMINI_IMAGE_INPUT_PRICE_PER_MILLION_TOKENS_USD
                + Decimal(
                    V2_GEMINI_IMAGE_MAX_OUTPUT_TOKENS - V2_GEMINI_IMAGE_2K_OUTPUT_TOKENS
                )
                * V2_GEMINI_IMAGE_TEXT_THINKING_PRICE_PER_MILLION_TOKENS_USD
            )
            / Decimal("1000000")
        ),
        "token_bound_assumption": "ONE_UTF8_BYTE_COUNTS_AS_AT_MOST_ONE_TOKEN",
        "maximum_attempts": 1,
        "automatic_retry_allowed": False,
        "provider_fallback_allowed": False,
        "minimum_width": V2_GEMINI_IMAGE_MINIMUM_WIDTH,
        "minimum_height": V2_GEMINI_IMAGE_MINIMUM_HEIGHT,
        "aspect_tolerance_percent": V2_GEMINI_IMAGE_ASPECT_TOLERANCE_PERCENT,
        "maximum_raster_bytes": V2_GEMINI_IMAGE_MAX_RASTER_BYTES,
        "maximum_raster_pixels": V2_GEMINI_IMAGE_MAX_RASTER_PIXELS,
        "blank_luma_stddev_minimum": V2_GEMINI_IMAGE_MINIMUM_LUMA_STDDEV,
        "mostly_black_fraction_maximum": V2_GEMINI_IMAGE_MAXIMUM_BLACK_FRACTION,
    }


def v2_gemini_image_provider_config_hash() -> str:
    return _sealed_hash(_provider_config_payload())


def v2_ai_image_required_semantic_anchors(
    scene_plan: AIVisualScenePlan,
) -> tuple[str, ...]:
    """Canonical scene facts the same-interaction description must attest."""

    anchors = (
        f"core_subject:{scene_plan.core_subject.strip()}",
        f"action_or_relation:{scene_plan.action_or_relation.strip()}",
        f"environment:{scene_plan.environment.strip()}",
        f"visual_goal:{scene_plan.visual_goal.strip()}",
    )
    if any(len(value) > 600 for value in anchors) or len(set(anchors)) != len(anchors):
        raise ValueError("V2_AI_IMAGE_SEMANTIC_ANCHOR_SET_INVALID")
    return anchors


def _generation_policy_payload() -> dict[str, Any]:
    return {
        "generation_mode": "GEMINI_TEXT_TO_IMAGE",
        "asset_acquisition_mode": "GENERATED",
        "image_size": GEMINI_IMAGE_DEFAULT_SIZE,
        "aspect_ratio": GEMINI_IMAGE_DEFAULT_ASPECT_RATIO,
        "output_count": 1,
        "maximum_attempts": 1,
        "automatic_retry_allowed": False,
        "provider_fallback_allowed": False,
        "native_fallback_allowed": False,
        "stock_fallback_allowed": False,
        "screenshot_fallback_allowed": False,
        "grounding_enabled": False,
        "reference_images_enabled": False,
        "synthetic_media_disclosure_required": True,
    }


def _provider_input(
    *,
    scene_id: str,
    required_semantic_anchors: tuple[str, ...],
    prompt: str,
    negative_prompt: str,
) -> str:
    return (
        f"{prompt.strip()}\n\n"
        "Negative constraints (all are mandatory):\n"
        f"{negative_prompt.strip()}\n\n"
        "Semantic output contract (mandatory, same interaction): return exactly "
        "one generated JPEG and exactly one plain-text JSON object describing "
        "the actual generated image, not merely the requested prompt. The JSON "
        "must contain exactly these keys: schema_version, scene_id, "
        "description_is_of_generated_output, observed_output_summary, "
        "observed_primary_subjects, observed_action_or_relation, "
        "observed_environment, observed_semantic_anchors, semantic_match, "
        "semantic_mismatch_reasons, "
        "forbidden_content_detected. Use schema_version "
        "vcos.gemini-image-observed-output.v1, scene_id "
        f"{json.dumps(scene_id)}, description_is_of_generated_output=true, "
        "arrays of short strings for the array fields, and booleans for "
        "semantic_match. The canonical semantic anchors are "
        f"{json.dumps(list(required_semantic_anchors), ensure_ascii=True)}. "
        "Include an anchor in observed_semantic_anchors only when that exact "
        "fact is visibly realized by the actual final image; preserve its exact "
        "string. Set semantic_match=true only when every canonical anchor is "
        "visibly realized. Set semantic_match=false and explain any mismatch if "
        "the actual image does not faithfully realize the requested meaning. "
        "Do not wrap the JSON in Markdown or add any other text."
    )


def _serialized_provider_request(
    *,
    model_id: str,
    scene_id: str,
    required_semantic_anchors: tuple[str, ...],
    prompt: str,
    negative_prompt: str,
    image_size: str,
    aspect_ratio: str,
) -> dict[str, Any]:
    """Exact request body sent by the official interactions resource.

    Timeout is an SDK transport option, not part of the serialized body hash.
    There is intentionally no provider fallback or unsupported idempotency
    parameter.  The durable effect ledger supplies internal at-most-once
    semantics; Gemini interactions currently supplies no reconciliation token
    that is known before submission.
    """

    provider_input = _provider_input(
        scene_id=scene_id,
        required_semantic_anchors=required_semantic_anchors,
        prompt=prompt,
        negative_prompt=negative_prompt,
    )
    if len(provider_input.encode("utf-8")) > V2_GEMINI_IMAGE_MAX_PROVIDER_INPUT_BYTES:
        raise ValueError("V2_AI_IMAGE_PROVIDER_INPUT_TOO_LARGE")
    return {
        "model": model_id,
        "input": provider_input,
        "stream": False,
        "store": False,
        "background": False,
        "generation_config": {
            "max_output_tokens": V2_GEMINI_IMAGE_MAX_OUTPUT_TOKENS,
            "thinking_level": "minimal",
        },
        "response_format": [
            {"type": "text", "mime_type": "text/plain"},
            {
                "type": "image",
                "mime_type": "image/jpeg",
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
            },
        ],
    }


def _request_journal_payload(values: dict[str, Any]) -> dict[str, Any]:
    """Canonical pre-submit journal without a credential or mutable state."""

    body = {
        key: value
        for key, value in values.items()
        if key not in {"request_journal_hash", "effect_identity_hash"}
    }
    return {
        "schema_version": V2_GEMINI_IMAGE_REQUEST_JOURNAL_SCHEMA,
        "effect_identity": body,
        "serialized_provider_request": _serialized_provider_request(
            model_id=str(body.get("model_id", GEMINI_IMAGE_DEFAULT_MODEL_ID)),
            scene_id=str(body["scene_id"]),
            required_semantic_anchors=tuple(body["required_semantic_anchors"]),
            prompt=str(body["prompt"]),
            negative_prompt=str(body["negative_prompt"]),
            image_size=str(body.get("image_size", GEMINI_IMAGE_DEFAULT_SIZE)),
            aspect_ratio=str(
                body.get("aspect_ratio", GEMINI_IMAGE_DEFAULT_ASPECT_RATIO)
            ),
        ),
        "credential_persisted": False,
        "authorization_header_persisted": False,
        "sdk_retry_count": 0,
        "fallback_provider_key": None,
    }


class V2AIImageSceneEffectIdentity(BaseModel):
    """Immutable server-owned identity prepared before a paid scene effect."""

    schema_version: Literal[V2_GEMINI_IMAGE_EFFECT_SCHEMA] = (
        V2_GEMINI_IMAGE_EFFECT_SCHEMA
    )
    effect_id: str = Field(min_length=1, max_length=160)
    visual_production_run_id: str = Field(min_length=1, max_length=160)
    scene_plan_snapshot_id: str = Field(min_length=1, max_length=160)
    workflow_run_id: str = Field(min_length=1, max_length=160)
    video_project_id: str = Field(min_length=1, max_length=160)
    production_package_artifact_version_id: str = Field(min_length=1, max_length=160)
    production_package_hash: str = Field(pattern=SHA256_PATTERN)

    scene_id: str = Field(min_length=1, max_length=160)
    ordinal: int = Field(ge=1)
    primary_asset_slot_id: str = Field(min_length=1, max_length=160)
    bound_scene_ids: tuple[str, ...] = Field(min_length=1)
    bound_scene_plan_hashes: tuple[str, ...] = Field(min_length=1)
    primary_asset_owner_scene_id: str = Field(min_length=1, max_length=160)
    route: Literal["AI_IMAGE"] = "AI_IMAGE"
    asset_acquisition_mode: Literal["GENERATED"] = "GENERATED"
    production_visual_policy_version: Literal[V2_AI_VISUAL_POLICY_VERSION] = (
        V2_AI_VISUAL_POLICY_VERSION
    )
    production_visual_policy_hash: str = Field(pattern=SHA256_PATTERN)
    style_bible_ref: str = Field(min_length=1, max_length=500)
    style_bible_hash: str = Field(pattern=SHA256_PATTERN)
    scene_plan_ref: str = Field(min_length=1, max_length=500)
    scene_plan_hash: str = Field(pattern=SHA256_PATTERN)
    compiled_prompt_ref: str = Field(min_length=1, max_length=500)
    compiled_prompt_hash: str = Field(pattern=SHA256_PATTERN)
    compiled_prompt_content_hash: str = Field(pattern=SHA256_PATTERN)
    prompt_compiler_version: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=32_000)
    negative_prompt: str = Field(min_length=1, max_length=16_000)
    prompt_hash: str = Field(pattern=SHA256_PATTERN)
    required_semantic_anchors: tuple[str, ...] = Field(min_length=4, max_length=4)

    adapter_key: Literal[V2_GEMINI_IMAGE_PRODUCTION_ADAPTER_KEY] = (
        V2_GEMINI_IMAGE_PRODUCTION_ADAPTER_KEY
    )
    provider_key: Literal[V2_GEMINI_IMAGE_PROVIDER_KEY] = V2_GEMINI_IMAGE_PROVIDER_KEY
    provider_config_version: Literal[V2_GEMINI_IMAGE_PROVIDER_CONFIG_VERSION] = (
        V2_GEMINI_IMAGE_PROVIDER_CONFIG_VERSION
    )
    provider_config_hash: str = Field(pattern=SHA256_PATTERN)
    model_id: Literal[GEMINI_IMAGE_DEFAULT_MODEL_ID] = GEMINI_IMAGE_DEFAULT_MODEL_ID
    price_catalog_version: str = Field(min_length=1, max_length=80)
    price_catalog_ref: str = Field(min_length=1, max_length=500)
    price_catalog_hash: str = Field(pattern=SHA256_PATTERN)
    image_size: Literal[GEMINI_IMAGE_DEFAULT_SIZE] = GEMINI_IMAGE_DEFAULT_SIZE
    aspect_ratio: Literal[GEMINI_IMAGE_DEFAULT_ASPECT_RATIO] = (
        GEMINI_IMAGE_DEFAULT_ASPECT_RATIO
    )
    output_count: Literal[1] = GEMINI_IMAGE_MAX_OUTPUTS
    maximum_attempts: Literal[1] = GEMINI_IMAGE_MAX_ATTEMPTS_PER_SCENE
    generation_policy_hash: str = Field(pattern=SHA256_PATTERN)

    approval_ref: str = Field(min_length=1, max_length=500)
    approval_hash: str = Field(pattern=SHA256_PATTERN)
    budget_reservation_id: str = Field(min_length=1, max_length=160)
    budget_authority_ref: str = Field(min_length=1, max_length=500)
    budget_authority_hash: str = Field(pattern=SHA256_PATTERN)
    cost_estimate_ref: str = Field(min_length=1, max_length=500)
    cost_estimate_hash: str = Field(pattern=SHA256_PATTERN)
    estimated_cost_usd: Decimal = Field(gt=Decimal("0"))
    maximum_cost_usd: Decimal = Field(gt=Decimal("0"))
    idempotency_key: str = Field(min_length=1, max_length=160)

    workspace_root: str = Field(min_length=1, max_length=2000)
    request_journal_path: str = Field(min_length=1, max_length=2000)
    response_capture_path: str = Field(min_length=1, max_length=2000)
    response_capture_journal_path: str = Field(min_length=1, max_length=2000)
    destination_path: str = Field(min_length=1, max_length=2000)

    request_hash: str = Field(pattern=SHA256_PATTERN)
    request_journal_hash: str = Field(pattern=SHA256_PATTERN)
    provider_retry_allowed: Literal[False] = False
    provider_fallback_allowed: Literal[False] = False
    effect_identity_hash: str = Field(pattern=SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def seal(cls, **values: Any) -> "V2AIImageSceneEffectIdentity":
        request_body = _serialized_provider_request(
            model_id=str(values.get("model_id", GEMINI_IMAGE_DEFAULT_MODEL_ID)),
            scene_id=str(values["scene_id"]),
            required_semantic_anchors=tuple(values["required_semantic_anchors"]),
            prompt=str(values["prompt"]),
            negative_prompt=str(values["negative_prompt"]),
            image_size=str(values.get("image_size", GEMINI_IMAGE_DEFAULT_SIZE)),
            aspect_ratio=str(
                values.get("aspect_ratio", GEMINI_IMAGE_DEFAULT_ASPECT_RATIO)
            ),
        )
        payload = {
            **values,
            "provider_config_hash": values.get(
                "provider_config_hash", v2_gemini_image_provider_config_hash()
            ),
            "generation_policy_hash": values.get(
                "generation_policy_hash",
                _sealed_hash(_generation_policy_payload()),
            ),
        }
        provisional = cls.model_construct(
            **payload,
            request_hash="0" * 64,
            request_journal_hash="0" * 64,
            effect_identity_hash="0" * 64,
        )
        identity_payload = provisional.model_dump(
            mode="json",
            exclude={
                "effect_identity_hash",
                "request_hash",
                "request_journal_hash",
            },
        )
        payload["effect_identity_hash"] = _sealed_hash(identity_payload)
        payload["request_hash"] = _sealed_hash(
            {
                "effect_identity_hash": payload["effect_identity_hash"],
                "provider_request": request_body,
            }
        )
        provisional = cls.model_construct(
            **payload,
            request_journal_hash="0" * 64,
        )
        journal_identity = provisional.model_dump(
            mode="json", exclude={"effect_identity_hash"}
        )
        payload["request_journal_hash"] = _sealed_hash(
            _request_journal_payload(journal_identity)
        )
        return cls(**payload)

    @model_validator(mode="after")
    def validate_identity(self) -> "V2AIImageSceneEffectIdentity":
        if self.estimated_cost_usd > self.maximum_cost_usd:
            raise ValueError("V2_AI_IMAGE_COST_EXCEEDS_AUTHORITY")
        if self.prompt_hash != hashlib.sha256(self.prompt.encode("utf-8")).hexdigest():
            raise ValueError("V2_AI_IMAGE_PROMPT_HASH_MISMATCH")
        provider_request = _serialized_provider_request(
            model_id=self.model_id,
            scene_id=self.scene_id,
            required_semantic_anchors=self.required_semantic_anchors,
            prompt=self.prompt,
            negative_prompt=self.negative_prompt,
            image_size=self.image_size,
            aspect_ratio=self.aspect_ratio,
        )
        expected_identity_hash = _sealed_hash(
            self.model_dump(
                mode="json",
                exclude={
                    "effect_identity_hash",
                    "request_hash",
                    "request_journal_hash",
                },
            )
        )
        expected_request_hash = _sealed_hash(
            {
                "effect_identity_hash": expected_identity_hash,
                "provider_request": provider_request,
            }
        )
        if self.request_hash != expected_request_hash:
            raise ValueError("V2_AI_IMAGE_PROVIDER_REQUEST_HASH_MISMATCH")
        if self.request_journal_hash != _sealed_hash(
            _request_journal_payload(
                self.model_dump(mode="json", exclude={"effect_identity_hash"})
            )
        ):
            raise ValueError("V2_AI_IMAGE_REQUEST_JOURNAL_HASH_MISMATCH")
        if self.effect_identity_hash != expected_identity_hash:
            raise ValueError("V2_AI_IMAGE_EFFECT_IDENTITY_HASH_MISMATCH")
        if (
            self.provider_config_hash != v2_gemini_image_provider_config_hash()
            or self.generation_policy_hash != _sealed_hash(_generation_policy_payload())
        ):
            raise ValueError("V2_AI_IMAGE_PROVIDER_POLICY_HASH_MISMATCH")
        if (
            self.compiled_prompt_hash != self.compiled_prompt_content_hash
            or not self.bound_scene_ids
            or len(self.bound_scene_ids) != len(self.bound_scene_plan_hashes)
            or len(set(self.bound_scene_ids)) != len(self.bound_scene_ids)
            or self.scene_id != self.primary_asset_owner_scene_id
            or self.scene_id not in self.bound_scene_ids
            or self.scene_plan_hash
            != self.bound_scene_plan_hashes[self.bound_scene_ids.index(self.scene_id)]
            or any(
                not re.fullmatch(SHA256_PATTERN, value)
                for value in self.bound_scene_plan_hashes
            )
            or len(set(self.required_semantic_anchors)) != 4
            or any(
                not value.strip() or len(value) > 600
                for value in self.required_semantic_anchors
            )
        ):
            raise ValueError("V2_AI_IMAGE_BOUND_SCENE_AUTHORITY_INVALID")
        paths = {
            self.request_journal_path,
            self.response_capture_path,
            self.response_capture_journal_path,
            self.destination_path,
        }
        if len(paths) != 4:
            raise ValueError("V2_AI_IMAGE_EFFECT_PATHS_MUST_BE_DISTINCT")
        return self

    @property
    def generation_policy(self) -> dict[str, Any]:
        return _generation_policy_payload()

    @property
    def db_identity_projection(self) -> dict[str, Any]:
        """Exact insert projection for a generated ``AIVisualAssetEffect``."""

        return {
            "visual_production_run_id": self.visual_production_run_id,
            "scene_plan_snapshot_id": self.scene_plan_snapshot_id,
            "workflow_run_id": self.workflow_run_id,
            "video_project_id": self.video_project_id,
            "asset_slot_id": self.primary_asset_slot_id,
            "scene_id": self.scene_id,
            "bound_scene_ids": list(self.bound_scene_ids),
            "bound_scene_plan_hashes": list(self.bound_scene_plan_hashes),
            "bound_scene_count": len(self.bound_scene_ids),
            "primary_asset_owner_scene_id": self.primary_asset_owner_scene_id,
            "ordinal": self.ordinal,
            "route": self.route,
            "asset_acquisition_mode": self.asset_acquisition_mode,
            "provider_key": self.provider_key,
            "model_id": self.model_id,
            "provider_config_version": self.provider_config_version,
            "provider_config_hash": self.provider_config_hash,
            "price_catalog_version": self.price_catalog_version,
            "price_catalog_ref": self.price_catalog_ref,
            "price_catalog_hash": self.price_catalog_hash,
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
            "effect_identity_hash": self.effect_identity_hash,
            "reuse_authority_ref": None,
            "reuse_authority_hash": None,
            "request_hash": self.request_hash,
            "idempotency_key": self.idempotency_key,
            "approval_ref": self.approval_ref,
            "approval_hash": self.approval_hash,
            "budget_reservation_id": self.budget_reservation_id,
            "budget_authority_ref": self.budget_authority_ref,
            "budget_authority_hash": self.budget_authority_hash,
            "cost_estimate_ref": self.cost_estimate_ref,
            "cost_estimate_hash": self.cost_estimate_hash,
            "estimated_cost_usd": self.estimated_cost_usd,
            "maximum_cost_usd": self.maximum_cost_usd,
            "maximum_attempts": self.maximum_attempts,
            "retry_allowed": False,
            "fallback_allowed": False,
        }

    @classmethod
    def from_visual_contracts(
        cls,
        *,
        style_bible: VideoVisualStyleBible,
        scene_plan: AIVisualScenePlan,
        compiled_prompt: CompiledAIImagePrompt,
        bound_scene_plans: tuple[AIVisualScenePlan, ...] | None = None,
        **authority: Any,
    ) -> "V2AIImageSceneEffectIdentity":
        """Bind the provider effect to the canonical AI-visual contracts."""

        if (
            scene_plan.production_route != "AI_IMAGE"
            or getattr(scene_plan, "reuses_primary_asset_from_scene_id", None)
            is not None
            or scene_plan.style_bible_hash != style_bible.content_hash
            or compiled_prompt.scene_id != scene_plan.scene_id
            or compiled_prompt.scene_plan_hash != scene_plan.content_hash
            or compiled_prompt.style_bible_hash != style_bible.content_hash
            or compiled_prompt.aspect_ratio != "16:9"
            or compiled_prompt.provider_call_made is not False
            or style_bible.video_project_id
            != str(authority.get("video_project_id") or "")
            or style_bible.package_id
            != str(authority.get("production_package_artifact_version_id") or "")
        ):
            raise ValueError("V2_AI_IMAGE_VISUAL_CONTRACT_BINDING_INVALID")
        bound = tuple(bound_scene_plans or (scene_plan,))
        if any(
            item.primary_asset_slot_id != scene_plan.primary_asset_slot_id
            or item.production_route != "AI_IMAGE"
            or (
                item.scene_id != scene_plan.scene_id
                and item.reuses_primary_asset_from_scene_id != scene_plan.scene_id
            )
            for item in bound
        ):
            raise ValueError("V2_AI_IMAGE_BOUND_SCENE_PLAN_MISMATCH")
        return cls.seal(
            **authority,
            scene_id=scene_plan.scene_id,
            ordinal=scene_plan.ordinal,
            primary_asset_slot_id=scene_plan.primary_asset_slot_id,
            bound_scene_ids=tuple(item.scene_id for item in bound),
            bound_scene_plan_hashes=tuple(item.content_hash for item in bound),
            primary_asset_owner_scene_id=scene_plan.scene_id,
            route="AI_IMAGE",
            style_bible_ref=(f"ai-visual-style-bible://{style_bible.style_bible_id}"),
            style_bible_hash=style_bible.content_hash,
            scene_plan_ref=f"ai-visual-scene-plan://{scene_plan.scene_id}",
            scene_plan_hash=scene_plan.content_hash,
            compiled_prompt_ref=(
                f"ai-visual-compiled-image-prompt://{scene_plan.scene_id}"
            ),
            compiled_prompt_hash=compiled_prompt.content_hash,
            compiled_prompt_content_hash=compiled_prompt.content_hash,
            prompt_compiler_version=compiled_prompt.prompt_compiler_version,
            prompt=compiled_prompt.prompt,
            negative_prompt=compiled_prompt.negative_prompt,
            prompt_hash=compiled_prompt.prompt_hash,
            required_semantic_anchors=v2_ai_image_required_semantic_anchors(scene_plan),
        )


class _V2GeminiImageObservedOutput(BaseModel):
    """Strict provider-authored description returned with the generated JPEG."""

    schema_version: Literal["vcos.gemini-image-observed-output.v1"] = (
        "vcos.gemini-image-observed-output.v1"
    )
    scene_id: str = Field(min_length=1, max_length=160)
    description_is_of_generated_output: Literal[True] = True
    observed_output_summary: str = Field(min_length=1, max_length=4_000)
    observed_primary_subjects: tuple[str, ...] = Field(min_length=1, max_length=12)
    observed_action_or_relation: str = Field(min_length=1, max_length=1_000)
    observed_environment: str = Field(min_length=1, max_length=1_000)
    observed_semantic_anchors: tuple[str, ...] = Field(max_length=4)
    semantic_match: bool
    semantic_mismatch_reasons: tuple[str, ...] = Field(max_length=12)
    forbidden_content_detected: tuple[str, ...] = Field(max_length=12)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_observation(self) -> "_V2GeminiImageObservedOutput":
        sequences = (
            self.observed_primary_subjects,
            self.semantic_mismatch_reasons,
            self.forbidden_content_detected,
        )
        if any(
            not isinstance(item, str) or not item.strip() or len(item.strip()) > 240
            for values in sequences
            for item in values
        ):
            raise ValueError("V2_AI_IMAGE_SEMANTIC_OBSERVATION_TEXT_INVALID")
        if len(set(self.observed_primary_subjects)) != len(
            self.observed_primary_subjects
        ):
            raise ValueError("V2_AI_IMAGE_SEMANTIC_SUBJECTS_NOT_UNIQUE")
        if len(set(self.observed_semantic_anchors)) != len(
            self.observed_semantic_anchors
        ) or any(
            not value.strip() or len(value) > 600
            for value in self.observed_semantic_anchors
        ):
            raise ValueError("V2_AI_IMAGE_SEMANTIC_ANCHORS_INVALID")
        if self.semantic_match and self.semantic_mismatch_reasons:
            raise ValueError("V2_AI_IMAGE_SEMANTIC_MATCH_REASON_CONFLICT")
        if not self.semantic_match and not self.semantic_mismatch_reasons:
            raise ValueError("V2_AI_IMAGE_SEMANTIC_MISMATCH_REASON_REQUIRED")
        if self.forbidden_content_detected and self.semantic_match:
            raise ValueError("V2_AI_IMAGE_FORBIDDEN_CONTENT_MATCH_CONFLICT")
        return self


class V2AIImageAssetSemanticAttestation(BaseModel):
    """Checksum-bound description emitted in the same Gemini interaction.

    This is useful semantic evidence but is deliberately not represented as an
    independent multimodal inspection.  Final human review remains mandatory.
    """

    schema_version: Literal[V2_GEMINI_IMAGE_SEMANTIC_ATTESTATION_SCHEMA] = (
        V2_GEMINI_IMAGE_SEMANTIC_ATTESTATION_SCHEMA
    )
    effect_id: str = Field(min_length=1, max_length=160)
    scene_id: str = Field(min_length=1, max_length=160)
    scene_plan_hash: str = Field(pattern=SHA256_PATTERN)
    prompt_hash: str = Field(pattern=SHA256_PATTERN)
    asset_checksum: str = Field(pattern=SHA256_PATTERN)
    attestation_source: Literal["SAME_INTERACTION_MODEL_OUTPUT"] = (
        "SAME_INTERACTION_MODEL_OUTPUT"
    )
    observed_output_summary: str = Field(min_length=1, max_length=4_000)
    observed_primary_subjects: tuple[str, ...] = Field(min_length=1, max_length=12)
    observed_action_or_relation: str = Field(min_length=1, max_length=1_000)
    observed_environment: str = Field(min_length=1, max_length=1_000)
    required_semantic_anchors: tuple[str, ...] = Field(min_length=4, max_length=4)
    observed_semantic_anchors: tuple[str, ...] = Field(max_length=4)
    semantic_match: bool
    semantic_mismatch_reasons: tuple[str, ...] = Field(max_length=12)
    forbidden_content_detected: tuple[str, ...] = Field(max_length=12)
    model_asserts_description_is_of_generated_output: Literal[True] = True
    independent_multimodal_inspection_performed: Literal[False] = False
    human_semantic_review_required: Literal[True] = True
    provider_text_hash: str = Field(pattern=SHA256_PATTERN)
    attestation_hash: str = Field(pattern=SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def seal(cls, **values: Any) -> "V2AIImageAssetSemanticAttestation":
        provisional = cls.model_construct(**values, attestation_hash="0" * 64)
        canonical = provisional.model_dump(mode="json", exclude={"attestation_hash"})
        return cls(**values, attestation_hash=_sealed_hash(canonical))

    @model_validator(mode="after")
    def validate_attestation(self) -> "V2AIImageAssetSemanticAttestation":
        observation = _V2GeminiImageObservedOutput(
            scene_id=self.scene_id,
            description_is_of_generated_output=(
                self.model_asserts_description_is_of_generated_output
            ),
            observed_output_summary=self.observed_output_summary,
            observed_primary_subjects=self.observed_primary_subjects,
            observed_action_or_relation=self.observed_action_or_relation,
            observed_environment=self.observed_environment,
            observed_semantic_anchors=self.observed_semantic_anchors,
            semantic_match=self.semantic_match,
            semantic_mismatch_reasons=self.semantic_mismatch_reasons,
            forbidden_content_detected=self.forbidden_content_detected,
        )
        if (
            observation.scene_id != self.scene_id
            or len(set(self.required_semantic_anchors)) != 4
            or len(set(self.observed_semantic_anchors))
            != len(self.observed_semantic_anchors)
            or any(
                value not in self.required_semantic_anchors
                for value in self.observed_semantic_anchors
            )
            or (
                self.semantic_match
                and self.observed_semantic_anchors != self.required_semantic_anchors
            )
        ):
            raise ValueError("V2_AI_IMAGE_SEMANTIC_ATTESTATION_SCENE_MISMATCH")
        expected = _sealed_hash(
            self.model_dump(mode="json", exclude={"attestation_hash"})
        )
        if self.attestation_hash != expected:
            raise ValueError("V2_AI_IMAGE_SEMANTIC_ATTESTATION_HASH_MISMATCH")
        return self


class V2AIImageSafeResponseCapture(BaseModel):
    """Redacted durable response envelope written before raster parsing."""

    schema_version: Literal[V2_GEMINI_IMAGE_CAPTURE_SCHEMA] = (
        V2_GEMINI_IMAGE_CAPTURE_SCHEMA
    )
    effect_id: str = Field(min_length=1, max_length=160)
    effect_identity_hash: str = Field(pattern=SHA256_PATTERN)
    provider_key: Literal[V2_GEMINI_IMAGE_PROVIDER_KEY] = V2_GEMINI_IMAGE_PROVIDER_KEY
    model_id: Literal[GEMINI_IMAGE_DEFAULT_MODEL_ID] = GEMINI_IMAGE_DEFAULT_MODEL_ID
    provider_request_id: str | None = Field(
        default=None, pattern=SAFE_PROVIDER_ID_PATTERN
    )
    provider_response_id: str | None = Field(
        default=None, pattern=SAFE_PROVIDER_ID_PATTERN
    )
    provider_status: str = Field(pattern=SAFE_CODE_PATTERN)
    provider_call_count: Literal[1] = 1
    output_count: int = Field(ge=0, le=16)
    semantic_output_count: int = Field(ge=0, le=16)
    output_mime_type: str | None = Field(default=None, max_length=80)
    output_size_bytes: int | None = Field(default=None, gt=0)
    output_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    response_capture_path: str | None = Field(default=None, max_length=2000)
    response_capture_journal_path: str = Field(min_length=1, max_length=2000)
    semantic_attestation: V2AIImageAssetSemanticAttestation | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    normalization_error_code: str | None = Field(
        default=None, pattern=SAFE_CODE_PATTERN
    )
    captured_at: AwareDatetime
    raw_response_persisted: Literal[False] = False
    base64_image_data_persisted: Literal[False] = False
    temporary_url_persisted: Literal[False] = False
    authorization_headers_persisted: Literal[False] = False
    capture_hash: str = Field(pattern=SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def seal(cls, **values: Any) -> "V2AIImageSafeResponseCapture":
        provisional = cls.model_construct(**values, capture_hash="0" * 64)
        canonical = provisional.model_dump(mode="json", exclude={"capture_hash"})
        return cls(**values, capture_hash=_sealed_hash(canonical))

    @model_validator(mode="after")
    def validate_capture(self) -> "V2AIImageSafeResponseCapture":
        successful_envelope = self.normalization_error_code is None
        output_fields = (
            self.output_mime_type,
            self.output_size_bytes,
            self.output_sha256,
            self.response_capture_path,
            self.semantic_attestation,
        )
        if successful_envelope and (
            self.provider_status != "COMPLETED"
            or self.output_count != 1
            or self.semantic_output_count != 1
            or self.output_mime_type != "image/jpeg"
            or any(value is None for value in output_fields)
        ):
            raise ValueError("V2_AI_IMAGE_RESPONSE_CAPTURE_SUCCESS_INVALID")
        if not successful_envelope and any(
            value is not None for value in output_fields
        ):
            raise ValueError("V2_AI_IMAGE_RESPONSE_CAPTURE_FAILURE_LEAK")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in self.usage.values()
        ):
            raise ValueError("V2_AI_IMAGE_RESPONSE_USAGE_INVALID")
        expected = _sealed_hash(self.model_dump(mode="json", exclude={"capture_hash"}))
        if self.capture_hash != expected:
            raise ValueError("V2_AI_IMAGE_RESPONSE_CAPTURE_HASH_MISMATCH")
        return self


class V2AIImageTechnicalQCReceipt(BaseModel):
    schema_version: Literal[V2_GEMINI_IMAGE_QC_SCHEMA] = V2_GEMINI_IMAGE_QC_SCHEMA
    effect_id: str = Field(min_length=1, max_length=160)
    scene_id: str = Field(min_length=1, max_length=160)
    primary_asset_slot_id: str = Field(min_length=1, max_length=160)
    scene_plan_hash: str = Field(pattern=SHA256_PATTERN)
    style_bible_hash: str = Field(pattern=SHA256_PATTERN)
    prompt_hash: str = Field(pattern=SHA256_PATTERN)
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(gt=0)
    content_type: Literal["image/jpeg"] = "image/jpeg"
    image_format: Literal["JPEG"] = "JPEG"
    width: int = Field(ge=V2_GEMINI_IMAGE_MINIMUM_WIDTH)
    height: int = Field(ge=V2_GEMINI_IMAGE_MINIMUM_HEIGHT)
    aspect_ratio: Literal["16:9"] = "16:9"
    mean_luma: float = Field(ge=0.0, le=255.0)
    luma_stddev: float = Field(ge=0.0, le=255.0)
    luma_range: int = Field(ge=0, le=255)
    black_fraction: float = Field(ge=0.0, le=1.0)
    perceptual_hash: str = Field(pattern=r"^[0-9a-f]{16}$")
    checks_passed: tuple[
        Literal[
            "provider_provenance",
            "checksum",
            "decode",
            "resolution",
            "aspect_ratio",
            "not_blank",
            "not_mostly_black",
            "scene_binding",
        ],
        ...,
    ]
    verdict: Literal["PASS"] = "PASS"
    inspected_at: AwareDatetime
    qc_hash: str = Field(pattern=SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def seal(cls, **values: Any) -> "V2AIImageTechnicalQCReceipt":
        provisional = cls.model_construct(**values, qc_hash="0" * 64)
        canonical = provisional.model_dump(mode="json", exclude={"qc_hash"})
        return cls(**values, qc_hash=_sealed_hash(canonical))

    @model_validator(mode="after")
    def validate_qc(self) -> "V2AIImageTechnicalQCReceipt":
        required = {
            "provider_provenance",
            "checksum",
            "decode",
            "resolution",
            "aspect_ratio",
            "not_blank",
            "not_mostly_black",
            "scene_binding",
        }
        if set(self.checks_passed) != required or len(self.checks_passed) != len(
            required
        ):
            raise ValueError("V2_AI_IMAGE_QC_CHECK_SET_INVALID")
        expected = _sealed_hash(self.model_dump(mode="json", exclude={"qc_hash"}))
        if self.qc_hash != expected:
            raise ValueError("V2_AI_IMAGE_QC_HASH_MISMATCH")
        return self


class V2AIImageAssetReceipt(BaseModel):
    """Production-eligible asset lineage after byte QC and atomic output."""

    schema_version: Literal[V2_GEMINI_IMAGE_ASSET_SCHEMA] = V2_GEMINI_IMAGE_ASSET_SCHEMA
    asset_id: str = Field(min_length=1, max_length=160)
    effect_id: str = Field(min_length=1, max_length=160)
    scene_id: str = Field(min_length=1, max_length=160)
    primary_asset_slot_id: str = Field(min_length=1, max_length=160)
    route: Literal["AI_IMAGE"] = "AI_IMAGE"
    provider: Literal[V2_GEMINI_IMAGE_PROVIDER_KEY] = V2_GEMINI_IMAGE_PROVIDER_KEY
    model: Literal[GEMINI_IMAGE_DEFAULT_MODEL_ID] = GEMINI_IMAGE_DEFAULT_MODEL_ID
    provider_config_version: Literal[V2_GEMINI_IMAGE_PROVIDER_CONFIG_VERSION] = (
        V2_GEMINI_IMAGE_PROVIDER_CONFIG_VERSION
    )
    provider_config_hash: str = Field(pattern=SHA256_PATTERN)
    generation_policy_hash: str = Field(pattern=SHA256_PATTERN)
    price_catalog_version: str = Field(min_length=1, max_length=80)
    price_catalog_hash: str = Field(pattern=SHA256_PATTERN)
    provider_request_id: str | None = Field(
        default=None, pattern=SAFE_PROVIDER_ID_PATTERN
    )
    provider_response_id: str | None = Field(
        default=None, pattern=SAFE_PROVIDER_ID_PATTERN
    )
    idempotency_key: str = Field(min_length=1, max_length=160)
    effect_identity_hash: str = Field(pattern=SHA256_PATTERN)
    request_hash: str = Field(pattern=SHA256_PATTERN)
    response_capture_hash: str = Field(pattern=SHA256_PATTERN)
    response_journal_hash: str = Field(pattern=SHA256_PATTERN)
    scene_plan_hash: str = Field(pattern=SHA256_PATTERN)
    style_bible_hash: str = Field(pattern=SHA256_PATTERN)
    compiled_prompt_hash: str = Field(pattern=SHA256_PATTERN)
    compiled_prompt_content_hash: str = Field(pattern=SHA256_PATTERN)
    prompt_hash: str = Field(pattern=SHA256_PATTERN)
    generated_at: AwareDatetime
    content_type: Literal["image/jpeg"] = "image/jpeg"
    width: int = Field(ge=V2_GEMINI_IMAGE_MINIMUM_WIDTH)
    height: int = Field(ge=V2_GEMINI_IMAGE_MINIMUM_HEIGHT)
    local_ref: str = Field(min_length=1, max_length=2000)
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(gt=0)
    provider_attempt_ref: str = Field(min_length=1, max_length=500)
    cost_ref: str = Field(min_length=1, max_length=500)
    # The synchronous image endpoint does not expose authoritative billing.
    # Never relabel the frozen catalog estimate as an observed provider cost.
    actual_cost_usd: None = None
    conservative_settlement_cost_usd: Decimal = Field(gt=Decimal("0"))
    cost_settlement_basis: Literal["CONSERVATIVE_CATALOG_ESTIMATE_VERIFIED"] = (
        "CONSERVATIVE_CATALOG_ESTIMATE_VERIFIED"
    )
    qc_ref: str = Field(min_length=1, max_length=500)
    qc_hash: str = Field(pattern=SHA256_PATTERN)
    technical_qc: V2AIImageTechnicalQCReceipt
    semantic_attestation: V2AIImageAssetSemanticAttestation
    production_eligible: Literal[True] = True
    provider_call_count: Literal[1] = 1
    retry_count: Literal[0] = 0
    fallback_provider_key: None = None
    receipt_hash: str = Field(pattern=SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def seal(cls, **values: Any) -> "V2AIImageAssetReceipt":
        provisional = cls.model_construct(**values, receipt_hash="0" * 64)
        canonical = provisional.model_dump(mode="json", exclude={"receipt_hash"})
        return cls(**values, receipt_hash=_sealed_hash(canonical))

    @model_validator(mode="after")
    def validate_receipt(self) -> "V2AIImageAssetReceipt":
        qc = self.technical_qc
        semantic = self.semantic_attestation
        if (
            qc.effect_id != self.effect_id
            or qc.scene_id != self.scene_id
            or qc.primary_asset_slot_id != self.primary_asset_slot_id
            or qc.scene_plan_hash != self.scene_plan_hash
            or qc.style_bible_hash != self.style_bible_hash
            or qc.prompt_hash != self.prompt_hash
            or qc.checksum_sha256 != self.checksum_sha256
            or qc.size_bytes != self.size_bytes
            or qc.content_type != self.content_type
            or qc.width != self.width
            or qc.height != self.height
            or qc.qc_hash != self.qc_hash
            or semantic.effect_id != self.effect_id
            or semantic.scene_id != self.scene_id
            or semantic.scene_plan_hash != self.scene_plan_hash
            or semantic.prompt_hash != self.prompt_hash
            or semantic.asset_checksum != self.checksum_sha256
            or semantic.required_semantic_anchors != semantic.observed_semantic_anchors
            or not semantic.semantic_match
            or bool(semantic.semantic_mismatch_reasons)
            or bool(semantic.forbidden_content_detected)
            or semantic.independent_multimodal_inspection_performed
            or not semantic.human_semantic_review_required
        ):
            raise ValueError("V2_AI_IMAGE_ASSET_QC_BINDING_MISMATCH")
        expected = _sealed_hash(self.model_dump(mode="json", exclude={"receipt_hash"}))
        if self.receipt_hash != expected:
            raise ValueError("V2_AI_IMAGE_ASSET_RECEIPT_HASH_MISMATCH")
        return self


class V2AIImageFailureReceipt(BaseModel):
    schema_version: Literal[V2_GEMINI_IMAGE_FAILURE_SCHEMA] = (
        V2_GEMINI_IMAGE_FAILURE_SCHEMA
    )
    effect_id: str = Field(min_length=1, max_length=160)
    classification: Literal["DEFINITIVE", "UNCERTAIN"]
    reason_code: str = Field(pattern=SAFE_CODE_PATTERN)
    provider_call_made: Literal[True] = True
    provider_call_count: Literal[1] = 1
    retry_allowed: Literal[False] = False
    fallback_allowed: Literal[False] = False
    message_redacted: Literal[
        "Provider details redacted; inspect durable safe evidence and authority refs."
    ] = "Provider details redacted; inspect durable safe evidence and authority refs."
    failed_at: AwareDatetime
    failure_hash: str = Field(pattern=SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def seal(cls, **values: Any) -> "V2AIImageFailureReceipt":
        provisional = cls.model_construct(**values, failure_hash="0" * 64)
        canonical = provisional.model_dump(mode="json", exclude={"failure_hash"})
        return cls(**values, failure_hash=_sealed_hash(canonical))

    @model_validator(mode="after")
    def validate_failure(self) -> "V2AIImageFailureReceipt":
        expected = _sealed_hash(self.model_dump(mode="json", exclude={"failure_hash"}))
        if self.failure_hash != expected:
            raise ValueError("V2_AI_IMAGE_FAILURE_HASH_MISMATCH")
        return self


class V2AIImageSceneEffectRecord(BaseModel):
    """DB-neutral projection for an atomic scene-effect row."""

    identity: V2AIImageSceneEffectIdentity
    state: V2AIImageEffectState
    revision: int = Field(ge=0)
    provider_call_count: int = Field(ge=0, le=1)
    prepared_at: AwareDatetime
    submitted_at: AwareDatetime | None = None
    submission_owner_token_hash: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    submission_lease_expires_at: AwareDatetime | None = None
    response_captured_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    response_capture: V2AIImageSafeResponseCapture | None = None
    response_journal_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    asset_receipt: V2AIImageAssetReceipt | None = None
    failure_receipt: V2AIImageFailureReceipt | None = None
    retry_allowed: Literal[False] = False
    fallback_allowed: Literal[False] = False
    record_hash: str = Field(pattern=SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @property
    def db_evidence_projection(self) -> dict[str, Any]:
        """Exact mutable evidence projection for ``AIVisualAssetEffect``.

        ``AIVisualAssetEffect`` currently has no dedicated record-attestation
        column.  The complete typed record (including ``record_hash``) is
        therefore retained under ``qc_evidence``; the remaining keys are the
        queryable state/evidence columns.  A concrete store must update this
        projection and the revision with one compare-and-swap transaction.
        """

        capture = self.response_capture
        receipt = self.asset_receipt
        failure = self.failure_receipt
        actual_cost_usd: Decimal | None = None
        cost_settlement_basis: str | None = None
        if receipt is not None:
            actual_cost_usd = receipt.actual_cost_usd
            cost_settlement_basis = receipt.cost_settlement_basis
        elif failure is not None:
            cost_settlement_basis = (
                "CONSERVATIVE_CATALOG_ESTIMATE_" + failure.classification
            )
        return {
            "state": self.state.value,
            "revision": self.revision,
            "provider_call_count": self.provider_call_count,
            "submission_owner_token_hash": self.submission_owner_token_hash,
            "submission_lease_expires_at": self.submission_lease_expires_at,
            # Gemini still-image submission exposes no pre-submit operation
            # identity that could authorize an exact retry/reconciliation.
            "provider_operation_id": None,
            "provider_request_id": (
                capture.provider_request_id if capture is not None else None
            ),
            "provider_response_id": (
                capture.provider_response_id if capture is not None else None
            ),
            "request_journal_ref": self.identity.request_journal_path,
            "request_journal_hash": self.identity.request_journal_hash,
            "response_journal_ref": (
                capture.response_capture_journal_path if capture is not None else None
            ),
            "response_journal_hash": self.response_journal_hash,
            "sanitized_response_hash": (
                capture.capture_hash if capture is not None else None
            ),
            "output_ref": receipt.local_ref if receipt is not None else None,
            "output_checksum": (
                receipt.checksum_sha256 if receipt is not None else None
            ),
            "output_size_bytes": receipt.size_bytes if receipt is not None else None,
            "output_content_type": (
                receipt.content_type if receipt is not None else None
            ),
            "output_width": receipt.width if receipt is not None else None,
            "output_height": receipt.height if receipt is not None else None,
            "output_duration_ms": None,
            "output_fps": None,
            "output_audio_stream_count": None,
            "normalization_ref": None,
            "normalization_hash": None,
            "qc_evidence": {
                "schema_version": V2_GEMINI_IMAGE_DB_RECORD_SCHEMA,
                "record": self.model_dump(mode="json"),
            },
            "qc_ref": receipt.qc_ref if receipt is not None else None,
            "qc_hash": receipt.qc_hash if receipt is not None else None,
            "actual_cost_usd": actual_cost_usd,
            "cost_settlement_basis": cost_settlement_basis,
            "failure_reason_code": (
                failure.reason_code if failure is not None else None
            ),
            "failure_evidence_hash": (
                failure.failure_hash if failure is not None else None
            ),
            "submitted_at": self.submitted_at,
            "response_captured_at": self.response_captured_at,
            "completed_at": self.completed_at,
        }

    @model_validator(mode="after")
    def validate_state_evidence(self) -> "V2AIImageSceneEffectRecord":
        if self.provider_call_count not in {0, 1}:
            raise ValueError("V2_AI_IMAGE_PROVIDER_CALL_COUNT_INVALID")
        if self.submitted_at is not None and self.submitted_at < self.prepared_at:
            raise ValueError("V2_AI_IMAGE_SUBMISSION_TIME_INVALID")
        state = self.state
        if state == V2AIImageEffectState.PREPARED:
            valid = (
                self.provider_call_count == 0
                and self.submitted_at is None
                and self.submission_owner_token_hash is None
                and self.submission_lease_expires_at is None
                and self.response_capture is None
                and self.response_journal_hash is None
                and self.asset_receipt is None
                and self.failure_receipt is None
                and self.completed_at is None
            )
        elif state == V2AIImageEffectState.SUBMITTING:
            valid = (
                self.provider_call_count == 1
                and self.submitted_at is not None
                and self.submission_owner_token_hash is not None
                and self.submission_lease_expires_at is not None
                and self.submission_lease_expires_at > self.submitted_at
                and self.response_capture is None
                and self.response_journal_hash is None
                and self.asset_receipt is None
                and self.failure_receipt is None
                and self.completed_at is None
            )
        elif state == V2AIImageEffectState.RESPONSE_CAPTURED:
            valid = (
                self.provider_call_count == 1
                and self.submitted_at is not None
                and self.response_captured_at is not None
                and self.response_capture is not None
                and self.response_journal_hash is not None
                and self.asset_receipt is None
                and self.failure_receipt is None
                and self.completed_at is None
            )
        elif state == V2AIImageEffectState.VERIFIED:
            valid = (
                self.provider_call_count == 1
                and self.response_capture is not None
                and self.response_journal_hash is not None
                and self.asset_receipt is not None
                and self.failure_receipt is None
                and self.completed_at is not None
            )
        elif state == V2AIImageEffectState.FAILED_DEFINITIVE:
            valid = (
                self.provider_call_count == 1
                and self.asset_receipt is None
                and self.failure_receipt is not None
                and self.failure_receipt.classification == "DEFINITIVE"
                and self.completed_at is not None
            )
        elif state == V2AIImageEffectState.FAILED_UNCERTAIN:
            valid = (
                self.provider_call_count == 1
                and self.asset_receipt is None
                and self.failure_receipt is not None
                and self.failure_receipt.classification == "UNCERTAIN"
                and self.completed_at is not None
            )
        else:  # pragma: no cover - enum makes this unreachable
            valid = False
        if not valid:
            raise ValueError("V2_AI_IMAGE_EFFECT_STATE_EVIDENCE_INVALID")
        if self.response_capture is not None and (
            self.response_capture.effect_id != self.identity.effect_id
            or self.response_capture.effect_identity_hash
            != self.identity.effect_identity_hash
        ):
            raise ValueError("V2_AI_IMAGE_RESPONSE_CAPTURE_IDENTITY_MISMATCH")
        if self.asset_receipt is not None and (
            self.asset_receipt.effect_id != self.identity.effect_id
            or self.asset_receipt.scene_id != self.identity.scene_id
            or self.asset_receipt.primary_asset_slot_id
            != self.identity.primary_asset_slot_id
            or self.asset_receipt.scene_plan_hash != self.identity.scene_plan_hash
            or self.asset_receipt.style_bible_hash != self.identity.style_bible_hash
            or self.asset_receipt.compiled_prompt_hash
            != self.identity.compiled_prompt_hash
            or self.asset_receipt.compiled_prompt_content_hash
            != self.identity.compiled_prompt_content_hash
            or self.asset_receipt.prompt_hash != self.identity.prompt_hash
            or self.asset_receipt.provider_config_hash
            != self.identity.provider_config_hash
            or self.asset_receipt.generation_policy_hash
            != self.identity.generation_policy_hash
            or self.asset_receipt.effect_identity_hash
            != self.identity.effect_identity_hash
            or self.asset_receipt.request_hash != self.identity.request_hash
            or self.response_capture is None
            or self.asset_receipt.response_capture_hash
            != self.response_capture.capture_hash
            or self.asset_receipt.response_journal_hash != self.response_journal_hash
            or self.asset_receipt.actual_cost_usd is not None
            or self.asset_receipt.conservative_settlement_cost_usd
            != self.identity.estimated_cost_usd
        ):
            raise ValueError("V2_AI_IMAGE_ASSET_IDENTITY_MISMATCH")
        if self.failure_receipt is not None and (
            self.failure_receipt.effect_id != self.identity.effect_id
        ):
            raise ValueError("V2_AI_IMAGE_FAILURE_IDENTITY_MISMATCH")
        expected = _sealed_hash(self.model_dump(mode="json", exclude={"record_hash"}))
        if self.record_hash != expected:
            raise ValueError("V2_AI_IMAGE_EFFECT_RECORD_HASH_MISMATCH")
        return self


def _seal_effect_record(**values: Any) -> V2AIImageSceneEffectRecord:
    provisional = V2AIImageSceneEffectRecord.model_construct(
        **values, record_hash="0" * 64
    )
    canonical = provisional.model_dump(mode="json", exclude={"record_hash"})
    return V2AIImageSceneEffectRecord(
        **values,
        record_hash=_sealed_hash(canonical),
    )


def _record_values(
    record: V2AIImageSceneEffectRecord, *, exclude: set[str]
) -> dict[str, Any]:
    """Return shallow values so nested frozen contracts stay typed while sealing."""

    return {
        name: getattr(record, name)
        for name in type(record).model_fields
        if name not in exclude
    }


class V2AIImageRecordTransitions:
    """Pure transition builders shared by DB implementations and tests."""

    @staticmethod
    def prepared(
        identity: V2AIImageSceneEffectIdentity, *, now: datetime
    ) -> V2AIImageSceneEffectRecord:
        return _seal_effect_record(
            identity=identity,
            state=V2AIImageEffectState.PREPARED,
            revision=1,
            provider_call_count=0,
            prepared_at=_aware_utc(now),
            submitted_at=None,
            submission_owner_token_hash=None,
            submission_lease_expires_at=None,
            response_captured_at=None,
            completed_at=None,
            response_capture=None,
            response_journal_hash=None,
            asset_receipt=None,
            failure_receipt=None,
        )

    @staticmethod
    def submitting(
        record: V2AIImageSceneEffectRecord,
        *,
        owner_token_hash: str,
        submitted_at: datetime,
        lease_expires_at: datetime,
    ) -> V2AIImageSceneEffectRecord:
        if record.state != V2AIImageEffectState.PREPARED:
            raise ValueError("V2_AI_IMAGE_PREPARED_STATE_REQUIRED")
        return _seal_effect_record(
            **_record_values(
                record,
                exclude={
                    "state",
                    "revision",
                    "provider_call_count",
                    "submitted_at",
                    "submission_owner_token_hash",
                    "submission_lease_expires_at",
                    "record_hash",
                },
            ),
            state=V2AIImageEffectState.SUBMITTING,
            revision=record.revision + 1,
            provider_call_count=1,
            submitted_at=_aware_utc(submitted_at),
            submission_owner_token_hash=owner_token_hash,
            submission_lease_expires_at=_aware_utc(lease_expires_at),
        )

    @staticmethod
    def response_captured(
        record: V2AIImageSceneEffectRecord,
        *,
        capture: V2AIImageSafeResponseCapture,
        response_journal_hash: str,
    ) -> V2AIImageSceneEffectRecord:
        if record.state != V2AIImageEffectState.SUBMITTING:
            raise ValueError("V2_AI_IMAGE_SUBMITTING_STATE_REQUIRED")
        return _seal_effect_record(
            **_record_values(
                record,
                exclude={
                    "state",
                    "revision",
                    "response_captured_at",
                    "response_capture",
                    "response_journal_hash",
                    "record_hash",
                },
            ),
            state=V2AIImageEffectState.RESPONSE_CAPTURED,
            revision=record.revision + 1,
            response_captured_at=capture.captured_at,
            response_capture=capture,
            response_journal_hash=response_journal_hash,
        )

    @staticmethod
    def verified(
        record: V2AIImageSceneEffectRecord,
        *,
        receipt: V2AIImageAssetReceipt,
        completed_at: datetime,
    ) -> V2AIImageSceneEffectRecord:
        if record.state != V2AIImageEffectState.RESPONSE_CAPTURED:
            raise ValueError("V2_AI_IMAGE_CAPTURED_STATE_REQUIRED")
        return _seal_effect_record(
            **_record_values(
                record,
                exclude={
                    "state",
                    "revision",
                    "asset_receipt",
                    "completed_at",
                    "record_hash",
                },
            ),
            state=V2AIImageEffectState.VERIFIED,
            revision=record.revision + 1,
            asset_receipt=receipt,
            completed_at=_aware_utc(completed_at),
        )

    @staticmethod
    def failed(
        record: V2AIImageSceneEffectRecord,
        *,
        failure: V2AIImageFailureReceipt,
    ) -> V2AIImageSceneEffectRecord:
        if record.state not in {
            V2AIImageEffectState.SUBMITTING,
            V2AIImageEffectState.RESPONSE_CAPTURED,
        }:
            raise ValueError("V2_AI_IMAGE_FAILURE_SOURCE_STATE_INVALID")
        state = (
            V2AIImageEffectState.FAILED_DEFINITIVE
            if failure.classification == "DEFINITIVE"
            else V2AIImageEffectState.FAILED_UNCERTAIN
        )
        return _seal_effect_record(
            **_record_values(
                record,
                exclude={
                    "state",
                    "revision",
                    "failure_receipt",
                    "completed_at",
                    "record_hash",
                },
            ),
            state=state,
            revision=record.revision + 1,
            failure_receipt=failure,
            completed_at=failure.failed_at,
        )


@runtime_checkable
class V2AIImageSceneEffectStore(Protocol):
    """Atomic DB boundary required by the production service.

    Every mutation is compare-and-swap on both revision and record hash.  The
    concrete DB store must commit before returning the resulting validated
    projection.
    """

    @property
    def ready(self) -> bool: ...

    def load(self, *, effect_id: str) -> V2AIImageSceneEffectRecord | None: ...

    def prepare(
        self, *, identity: V2AIImageSceneEffectIdentity, prepared_at: datetime
    ) -> V2AIImageSceneEffectRecord: ...

    def claim_submitting(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        submission_owner_token_hash: str,
        submitted_at: datetime,
        lease_expires_at: datetime,
    ) -> V2AIImageSceneEffectRecord: ...

    def record_response_captured(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        submission_owner_token_hash: str,
        capture: V2AIImageSafeResponseCapture,
        response_journal_hash: str,
    ) -> V2AIImageSceneEffectRecord: ...

    def mark_verified(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        receipt: V2AIImageAssetReceipt,
        completed_at: datetime,
    ) -> V2AIImageSceneEffectRecord: ...

    def mark_failed_definitive(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        failure: V2AIImageFailureReceipt,
    ) -> V2AIImageSceneEffectRecord: ...

    def mark_failed_uncertain(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        failure: V2AIImageFailureReceipt,
    ) -> V2AIImageSceneEffectRecord: ...


class V2GeminiInteractionsAPI(Protocol):
    sdk_configuration: Any

    def create(self, **kwargs: Any) -> Any: ...


class V2GeminiImageClient(Protocol):
    interactions: V2GeminiInteractionsAPI


class V2GeminiImageOfficialClientFactory:
    """Explicit official-SDK factory; construction performs no provider call."""

    @staticmethod
    def disable_and_verify_retries(interactions: Any) -> None:
        sdk_configuration = getattr(interactions, "sdk_configuration", None)
        retry_config = getattr(sdk_configuration, "retry_config", None)
        fields = ("strategy", "retry_connection_errors", "max_retries")
        if retry_config is None or any(
            not hasattr(retry_config, field_name) for field_name in fields
        ):
            raise RuntimeError("V2_GEMINI_IMAGE_SDK_RETRY_CONTROL_UNAVAILABLE")
        retry_config.strategy = "none"
        retry_config.retry_connection_errors = False
        retry_config.max_retries = 0
        if (
            retry_config.strategy != "none"
            or retry_config.retry_connection_errors is not False
            or retry_config.max_retries != 0
        ):
            raise RuntimeError("V2_GEMINI_IMAGE_SDK_RETRIES_NOT_DISABLED")

    @classmethod
    def build(cls, settings: Settings | None = None) -> V2GeminiImageClient:
        runtime_settings = settings or get_settings()
        credential = (
            runtime_settings.gemini_api_key.get_secret_value().strip()
            if runtime_settings.gemini_api_key
            else ""
        )
        if not credential:
            raise RuntimeError("V2_GEMINI_IMAGE_CREDENTIAL_NOT_CONFIGURED")
        try:
            genai = importlib.import_module("google.genai")
            types = importlib.import_module("google.genai.types")
        except ImportError as exc:
            raise RuntimeError("V2_GEMINI_IMAGE_OFFICIAL_SDK_NOT_INSTALLED") from exc
        try:
            installed = package_version(V2_GEMINI_IMAGE_PINNED_SDK_DISTRIBUTION)
        except PackageNotFoundError as exc:
            raise RuntimeError("V2_GEMINI_IMAGE_OFFICIAL_SDK_NOT_INSTALLED") from exc
        if installed != V2_GEMINI_IMAGE_PINNED_SDK_VERSION:
            raise RuntimeError("V2_GEMINI_IMAGE_OFFICIAL_SDK_VERSION_DRIFT")
        client = genai.Client(
            api_key=credential,
            http_options=types.HttpOptions(
                timeout=int(V2_GEMINI_IMAGE_REQUEST_TIMEOUT_SECONDS * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        cls.disable_and_verify_retries(client.interactions)
        return client


class V2GeminiImageProductionAdapter:
    """One-call transport adapter; state ownership stays in the service/store."""

    adapter_key = V2_GEMINI_IMAGE_PRODUCTION_ADAPTER_KEY
    provider_key = V2_GEMINI_IMAGE_PROVIDER_KEY
    provider_fallback_allowed = False
    automatic_retry_allowed = False

    def __init__(self, *, client: V2GeminiImageClient):
        if client is None or getattr(client, "interactions", None) is None:
            raise TypeError("V2_GEMINI_IMAGE_INJECTED_CLIENT_REQUIRED")
        V2GeminiImageOfficialClientFactory.disable_and_verify_retries(
            client.interactions
        )
        self._client = client
        self.sdk_retries_disabled = True

    @staticmethod
    def interaction_create_kwargs(
        identity: V2AIImageSceneEffectIdentity,
    ) -> dict[str, Any]:
        body = _serialized_provider_request(
            model_id=identity.model_id,
            scene_id=identity.scene_id,
            required_semantic_anchors=identity.required_semantic_anchors,
            prompt=identity.prompt,
            negative_prompt=identity.negative_prompt,
            image_size=identity.image_size,
            aspect_ratio=identity.aspect_ratio,
        )
        if (
            _sealed_hash(
                {
                    "effect_identity_hash": identity.effect_identity_hash,
                    "provider_request": body,
                }
            )
            != identity.request_hash
        ):
            raise V2AIImageExecutionBlocked(
                "V2_AI_IMAGE_PROVIDER_REQUEST_HASH_MISMATCH"
            )
        return {**body, "timeout": V2_GEMINI_IMAGE_REQUEST_TIMEOUT_SECONDS}

    def submit_claimed_effect(self, record: V2AIImageSceneEffectRecord) -> Any:
        """Invoke exactly once, only after a durable SUBMITTING projection."""

        if (
            record.state != V2AIImageEffectState.SUBMITTING
            or record.provider_call_count != 1
            or not record.submission_owner_token_hash
        ):
            raise V2AIImageExecutionBlocked(
                "V2_AI_IMAGE_DURABLE_SUBMITTING_CLAIM_REQUIRED"
            )
        return self._client.interactions.create(
            **self.interaction_create_kwargs(record.identity)
        )


class V2GeminiImageProductionReadiness(BaseModel):
    schema_version: Literal[V2_GEMINI_IMAGE_READINESS_SCHEMA] = (
        V2_GEMINI_IMAGE_READINESS_SCHEMA
    )
    adapter_key: Literal[V2_GEMINI_IMAGE_PRODUCTION_ADAPTER_KEY] = (
        V2_GEMINI_IMAGE_PRODUCTION_ADAPTER_KEY
    )
    provider_key: Literal[V2_GEMINI_IMAGE_PROVIDER_KEY] = V2_GEMINI_IMAGE_PROVIDER_KEY
    model_id: Literal[GEMINI_IMAGE_DEFAULT_MODEL_ID] = GEMINI_IMAGE_DEFAULT_MODEL_ID
    provider_config_version: Literal[V2_GEMINI_IMAGE_PROVIDER_CONFIG_VERSION] = (
        V2_GEMINI_IMAGE_PROVIDER_CONFIG_VERSION
    )
    provider_config_hash: str = Field(pattern=SHA256_PATTERN)
    generation_policy_hash: str = Field(pattern=SHA256_PATTERN)
    price_catalog_version: str
    price_catalog_hash: str = Field(pattern=SHA256_PATTERN)
    credential_configured: bool
    credential_value_exposed: Literal[False] = False
    route_approved: bool
    real_generation_enabled: bool
    fixture_only_disabled: bool
    global_execution_enabled: bool
    production_execution_enabled: bool
    media_provider_calls_enabled: bool
    adapter_registered: bool
    injected_client_configured: bool
    official_sdk_version_pinned: bool
    sdk_retries_disabled: bool
    raster_decoder_ready: bool
    scene_effect_store_ready: bool
    package_contract_bound: bool
    budget_authority_bound: bool
    exact_catalog_identity_bound: bool
    exact_model_identity_bound: bool
    exact_provider_config_bound: bool
    no_provider_fallback: Literal[True] = True
    no_automatic_retry: Literal[True] = True
    provider_call_made: Literal[False] = False
    execution_ready: bool
    blocker_codes: tuple[str, ...]
    projection_hash: str = Field(pattern=SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def seal(cls, **values: Any) -> "V2GeminiImageProductionReadiness":
        provisional = cls.model_construct(**values, projection_hash="0" * 64)
        canonical = provisional.model_dump(mode="json", exclude={"projection_hash"})
        return cls(**values, projection_hash=_sealed_hash(canonical))

    @model_validator(mode="after")
    def validate_projection(self) -> "V2GeminiImageProductionReadiness":
        checks = (
            self.credential_configured,
            self.route_approved,
            self.real_generation_enabled,
            self.fixture_only_disabled,
            self.global_execution_enabled,
            self.production_execution_enabled,
            self.media_provider_calls_enabled,
            self.adapter_registered,
            self.injected_client_configured,
            self.official_sdk_version_pinned,
            self.sdk_retries_disabled,
            self.raster_decoder_ready,
            self.scene_effect_store_ready,
            self.package_contract_bound,
            self.budget_authority_bound,
            self.exact_catalog_identity_bound,
            self.exact_model_identity_bound,
            self.exact_provider_config_bound,
        )
        if self.execution_ready != all(checks):
            raise ValueError("V2_GEMINI_IMAGE_READINESS_PROJECTION_INVALID")
        if self.execution_ready == bool(self.blocker_codes):
            raise ValueError("V2_GEMINI_IMAGE_READINESS_BLOCKERS_INVALID")
        expected = _sealed_hash(
            self.model_dump(mode="json", exclude={"projection_hash"})
        )
        if self.projection_hash != expected:
            raise ValueError("V2_GEMINI_IMAGE_READINESS_HASH_MISMATCH")
        return self


class V2AIImageTechnicalQC:
    """Bounded native JPEG decode and obvious-failure production QC."""

    def __init__(
        self,
        *,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg")
        self.ffprobe_path = ffprobe_path or shutil.which("ffprobe")

    def runtime_ready(self) -> bool:
        if not self.ffmpeg_path or not self.ffprobe_path:
            return False
        binaries = (Path(self.ffmpeg_path), Path(self.ffprobe_path))
        if any(not path.is_file() or not os.access(path, os.X_OK) for path in binaries):
            return False
        try:
            probe = subprocess.run(
                [self.ffprobe_path, "-v", "error", "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=V2_GEMINI_IMAGE_DECODER_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
            decoders = subprocess.run(
                [self.ffmpeg_path, "-hide_banner", "-v", "error", "-decoders"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=V2_GEMINI_IMAGE_DECODER_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return bool(
            probe.returncode == 0
            and decoders.returncode == 0
            and any(
                len(parts) >= 2 and parts[1] == b"mjpeg"
                for parts in (line.split() for line in decoders.stdout.splitlines())
            )
        )

    def inspect(
        self,
        *,
        identity: V2AIImageSceneEffectIdentity,
        capture: V2AIImageSafeResponseCapture,
        path: Path,
        now: datetime,
    ) -> V2AIImageTechnicalQCReceipt:
        if capture.normalization_error_code is not None:
            raise V2AIImageProviderBoundaryError(capture.normalization_error_code)
        if not self.runtime_ready():
            raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_SAFE_DECODER_UNAVAILABLE")
        if not path.is_file() or path.is_symlink():
            raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_CAPTURE_FILE_MISSING")
        size_bytes = path.stat().st_size
        if not 0 < size_bytes <= V2_GEMINI_IMAGE_MAX_RASTER_BYTES:
            raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_RASTER_SIZE_INVALID")
        checksum = _sha256_file(path)
        if checksum != capture.output_sha256 or size_bytes != capture.output_size_bytes:
            raise V2AIImageProviderBoundaryError(
                "V2_AI_IMAGE_CAPTURE_CHECKSUM_MISMATCH"
            )
        with path.open("rb") as stream:
            if stream.read(3) != b"\xff\xd8\xff":
                raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_JPEG_BYTES_REQUIRED")
        width, height = self._probe_dimensions(path)
        if width * height > V2_GEMINI_IMAGE_MAX_RASTER_PIXELS:
            raise V2AIImageProviderBoundaryError(
                "V2_AI_IMAGE_RASTER_DIMENSIONS_INVALID"
            )
        if (
            width < V2_GEMINI_IMAGE_MINIMUM_WIDTH
            or height < V2_GEMINI_IMAGE_MINIMUM_HEIGHT
        ):
            raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_RESOLUTION_BELOW_1080P")
        aspect_error = abs(width * 9 - height * 16)
        if aspect_error * 100 > height * 16 * V2_GEMINI_IMAGE_ASPECT_TOLERANCE_PERCENT:
            raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_ASPECT_RATIO_MISMATCH")
        luma = self._decode_luma_sample(path)
        mean_luma = statistics.fmean(luma)
        luma_stddev = statistics.pstdev(luma)
        luma_range = max(luma) - min(luma)
        black_fraction = sum(value <= 4 for value in luma) / len(luma)
        perceptual_hash = _perceptual_dhash(luma)
        if (
            mean_luma < V2_GEMINI_IMAGE_MINIMUM_LUMA_MEAN
            or black_fraction > V2_GEMINI_IMAGE_MAXIMUM_BLACK_FRACTION
        ):
            raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_OUTPUT_MOSTLY_BLACK")
        if (
            luma_stddev < V2_GEMINI_IMAGE_MINIMUM_LUMA_STDDEV
            or luma_range < V2_GEMINI_IMAGE_MINIMUM_LUMA_RANGE
        ):
            raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_OUTPUT_BLANK")
        return V2AIImageTechnicalQCReceipt.seal(
            effect_id=identity.effect_id,
            scene_id=identity.scene_id,
            primary_asset_slot_id=identity.primary_asset_slot_id,
            scene_plan_hash=identity.scene_plan_hash,
            style_bible_hash=identity.style_bible_hash,
            prompt_hash=identity.prompt_hash,
            checksum_sha256=checksum,
            size_bytes=size_bytes,
            width=width,
            height=height,
            mean_luma=round(mean_luma, 6),
            luma_stddev=round(luma_stddev, 6),
            luma_range=luma_range,
            black_fraction=round(black_fraction, 6),
            perceptual_hash=perceptual_hash,
            checks_passed=(
                "provider_provenance",
                "checksum",
                "decode",
                "resolution",
                "aspect_ratio",
                "not_blank",
                "not_mostly_black",
                "scene_binding",
            ),
            inspected_at=_aware_utc(now),
        )

    def _probe_dimensions(self, path: Path) -> tuple[int, int]:
        try:
            completed = subprocess.run(
                [
                    str(self.ffprobe_path),
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,width,height",
                    "-of",
                    "json",
                    str(path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=V2_GEMINI_IMAGE_SAFE_DECODE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise V2AIImageProviderBoundaryError(
                "V2_AI_IMAGE_SAFE_DECODE_FAILED"
            ) from exc
        if completed.returncode != 0 or len(completed.stdout) > 16_384:
            raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_SAFE_DECODE_FAILED")
        try:
            streams = json.loads(completed.stdout).get("streams")
            stream = (
                streams[0] if isinstance(streams, list) and len(streams) == 1 else None
            )
            codec = stream.get("codec_name") if isinstance(stream, dict) else None
            width = stream.get("width") if isinstance(stream, dict) else None
            height = stream.get("height") if isinstance(stream, dict) else None
        except (
            AttributeError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            stream = None
            codec = width = height = None
        if (
            codec not in {"mjpeg", "jpeg"}
            or not isinstance(width, int)
            or isinstance(width, bool)
            or not isinstance(height, int)
            or isinstance(height, bool)
            or width <= 0
            or height <= 0
        ):
            raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_JPEG_DECODE_INVALID")
        return width, height

    def _decode_luma_sample(self, path: Path) -> list[int]:
        expected = V2_GEMINI_IMAGE_SAMPLE_WIDTH * V2_GEMINI_IMAGE_SAMPLE_HEIGHT
        try:
            completed = subprocess.run(
                [
                    str(self.ffmpeg_path),
                    "-hide_banner",
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-err_detect",
                    "explode",
                    "-threads",
                    "1",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    (
                        f"scale={V2_GEMINI_IMAGE_SAMPLE_WIDTH}:"
                        f"{V2_GEMINI_IMAGE_SAMPLE_HEIGHT}:flags=area,format=gray"
                    ),
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=V2_GEMINI_IMAGE_SAFE_DECODE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise V2AIImageProviderBoundaryError(
                "V2_AI_IMAGE_SAFE_DECODE_FAILED"
            ) from exc
        if completed.returncode != 0 or len(completed.stdout) != expected:
            raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_SAFE_DECODE_FAILED")
        return list(completed.stdout)


def _perceptual_dhash(luma: list[int]) -> str:
    """Return a deterministic 64-bit difference hash of decoded pixels."""

    width = V2_GEMINI_IMAGE_SAMPLE_WIDTH
    height = V2_GEMINI_IMAGE_SAMPLE_HEIGHT
    if len(luma) != width * height:
        raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_LUMA_SAMPLE_INVALID")
    grid: list[list[float]] = []
    for row in range(8):
        y0 = row * height // 8
        y1 = (row + 1) * height // 8
        values: list[float] = []
        for column in range(9):
            x0 = column * width // 9
            x1 = (column + 1) * width // 9
            block = [luma[y * width + x] for y in range(y0, y1) for x in range(x0, x1)]
            if not block:
                raise V2AIImageProviderBoundaryError("V2_AI_IMAGE_LUMA_SAMPLE_INVALID")
            values.append(statistics.fmean(block))
        grid.append(values)
    bits = 0
    for row in grid:
        for left, right in zip(row[:-1], row[1:], strict=True):
            bits = (bits << 1) | int(left > right)
    return f"{bits:016x}"


class V2AIImageProductionService:
    """Durable scene-effect coordinator around the injected Gemini adapter."""

    def __init__(
        self,
        *,
        store: V2AIImageSceneEffectStore,
        adapter: V2GeminiImageProductionAdapter,
        settings: Settings | None = None,
        catalog: GoogleGeminiImageModelPriceCatalog | None = None,
        technical_qc: V2AIImageTechnicalQC | None = None,
        adapter_registered: bool = False,
        clock: Any | None = None,
    ) -> None:
        if not isinstance(store, V2AIImageSceneEffectStore):
            raise TypeError("V2_AI_IMAGE_SCENE_EFFECT_STORE_INVALID")
        self._store = store
        self._adapter = adapter
        self._settings = settings or get_settings()
        self._catalog = catalog or GoogleGeminiImageModelPriceCatalog()
        self._technical_qc = technical_qc or V2AIImageTechnicalQC()
        self._adapter_registered = adapter_registered
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def catalog_hash(self) -> str:
        return _sealed_hash(self._catalog.payload)

    def prepare(
        self, identity: V2AIImageSceneEffectIdentity
    ) -> V2AIImageSceneEffectRecord:
        self._validate_identity_authorities(identity)
        existing = self._store.load(effect_id=identity.effect_id)
        if existing is not None:
            self._validate_record_identity(existing, identity)
            self._verify_request_journal(identity)
            return existing
        self._validate_effect_paths(identity, require_empty=True)
        self._persist_request_journal(identity)
        record = self._store.prepare(identity=identity, prepared_at=self._now())
        self._validate_record_identity(record, identity)
        if record.state != V2AIImageEffectState.PREPARED:
            # Idempotent preparation may return an already advanced exact row.
            return record
        return record

    def readiness_projection(
        self, identity: V2AIImageSceneEffectIdentity | None = None
    ) -> V2GeminiImageProductionReadiness:
        settings = self._settings
        credential_configured = bool(
            settings.gemini_api_key
            and settings.gemini_api_key.get_secret_value().strip()
        )
        try:
            sdk_pinned = (
                package_version(V2_GEMINI_IMAGE_PINNED_SDK_DISTRIBUTION)
                == V2_GEMINI_IMAGE_PINNED_SDK_VERSION
            )
        except PackageNotFoundError:
            sdk_pinned = False
        catalog_bound = identity is not None and all(
            (
                identity.price_catalog_version == self._catalog.version,
                identity.price_catalog_ref == self._catalog.ref,
                identity.price_catalog_hash == self.catalog_hash,
            )
        )
        model_bound = identity is not None and all(
            (
                identity.model_id == settings.gemini_image_model_id,
                identity.image_size == settings.gemini_image_default_size,
                identity.aspect_ratio == settings.gemini_image_default_aspect_ratio,
                identity.output_count == settings.gemini_image_max_outputs == 1,
                identity.maximum_attempts
                == settings.gemini_image_max_attempts_per_scene
                == 1,
            )
        )
        package_bound = identity is not None and all(
            (
                identity.route == "AI_IMAGE",
                bool(identity.primary_asset_slot_id),
                identity.primary_asset_owner_scene_id == identity.scene_id,
                identity.scene_id in identity.bound_scene_ids,
                len(identity.bound_scene_ids) == len(identity.bound_scene_plan_hashes),
                bool(identity.production_package_hash),
                bool(identity.production_visual_policy_hash),
                bool(identity.style_bible_hash),
                bool(identity.scene_plan_hash),
                identity.compiled_prompt_hash == identity.compiled_prompt_content_hash,
                bool(identity.compiled_prompt_content_hash),
                bool(identity.prompt_hash),
            )
        )
        provider_config_bound = identity is not None and all(
            (
                identity.provider_config_version
                == V2_GEMINI_IMAGE_PROVIDER_CONFIG_VERSION,
                identity.provider_config_hash == v2_gemini_image_provider_config_hash(),
                identity.generation_policy_hash
                == _sealed_hash(_generation_policy_payload()),
                identity.provider_retry_allowed is False,
                identity.provider_fallback_allowed is False,
            )
        )
        image_budget_cap = settings.extra_ai_image_monthly_budget_usd
        budget_bound = identity is not None and all(
            (
                bool(identity.approval_hash),
                bool(identity.budget_authority_hash),
                bool(identity.cost_estimate_hash),
                image_budget_cap is not None,
                image_budget_cap is not None
                and identity.maximum_cost_usd <= image_budget_cap,
                identity.estimated_cost_usd <= identity.maximum_cost_usd,
            )
        )
        checks = {
            "credential_configured": credential_configured,
            "route_approved": bool(settings.gemini_image_provider_route_approved),
            "real_generation_enabled": bool(
                settings.gemini_image_real_generation_enabled
            ),
            "fixture_only_disabled": not settings.img1_fixture_only,
            "global_execution_enabled": bool(settings.provider_real_execution_enabled),
            "production_execution_enabled": bool(
                settings.provider_production_execution_enabled
            ),
            "media_provider_calls_enabled": not settings.media_provider_calls_disabled,
            "adapter_registered": self._adapter_registered,
            "injected_client_configured": self._adapter is not None,
            "official_sdk_version_pinned": sdk_pinned,
            "sdk_retries_disabled": self._adapter.sdk_retries_disabled,
            "raster_decoder_ready": self._technical_qc.runtime_ready(),
            "scene_effect_store_ready": bool(self._store.ready),
            "package_contract_bound": package_bound,
            "budget_authority_bound": budget_bound,
            "exact_catalog_identity_bound": catalog_bound,
            "exact_model_identity_bound": model_bound,
            "exact_provider_config_bound": provider_config_bound,
        }
        blockers = tuple(
            f"V2_GEMINI_IMAGE_{name.upper()}_BLOCKED"
            for name, passed in checks.items()
            if not passed
        )
        return V2GeminiImageProductionReadiness.seal(
            provider_config_hash=v2_gemini_image_provider_config_hash(),
            generation_policy_hash=_sealed_hash(_generation_policy_payload()),
            price_catalog_version=self._catalog.version,
            price_catalog_hash=self.catalog_hash,
            **checks,
            execution_ready=not blockers,
            blocker_codes=blockers,
        )

    def execute(self, *, effect_id: str) -> V2AIImageSceneEffectRecord:
        """Execute or reconcile one effect without ever auto-regenerating it."""

        record = self._load_required(effect_id)
        if record.state in {
            V2AIImageEffectState.VERIFIED,
            V2AIImageEffectState.FAILED_DEFINITIVE,
            V2AIImageEffectState.FAILED_UNCERTAIN,
        }:
            return record
        if record.state == V2AIImageEffectState.RESPONSE_CAPTURED:
            return self._finish_captured(record)
        if record.state == V2AIImageEffectState.SUBMITTING:
            return self._reconcile_submitting(record)
        if record.state != V2AIImageEffectState.PREPARED:
            raise V2AIImageExecutionBlocked("V2_AI_IMAGE_EFFECT_STATE_INVALID")

        readiness = self.readiness_projection(record.identity)
        if not readiness.execution_ready:
            raise V2AIImageExecutionBlocked(readiness.blocker_codes[0])
        self._validate_identity_authorities(record.identity)
        self._validate_effect_paths(record.identity, require_empty=True)
        self._verify_request_journal(record.identity)
        now = self._now()
        owner_token_hash = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        submitting = self._store.claim_submitting(
            effect_id=effect_id,
            expected_revision=record.revision,
            expected_record_hash=record.record_hash,
            submission_owner_token_hash=owner_token_hash,
            submitted_at=now,
            lease_expires_at=now
            + timedelta(seconds=V2_GEMINI_IMAGE_SUBMISSION_LEASE_SECONDS),
        )
        self._validate_submitting_claim(
            submitting,
            identity=record.identity,
            owner_token_hash=owner_token_hash,
        )
        try:
            response = self._adapter.submit_claimed_effect(submitting)
        except Exception as exc:
            return self._record_submission_exception(submitting, exc)

        try:
            capture, output_bytes = self._capture_response(
                identity=submitting.identity,
                response=response,
            )
        except Exception as exc:
            return self._mark_failed(
                submitting,
                classification="UNCERTAIN",
                reason_code=self._safe_exception_code(
                    exc, fallback="V2_AI_IMAGE_RESPONSE_CAPTURE_UNCERTAIN"
                ),
            )
        try:
            response_journal_hash = self._persist_response_capture(
                identity=submitting.identity,
                capture=capture,
                output_bytes=output_bytes,
            )
        except Exception as exc:
            # If both durable capture files are complete, leave SUBMITTING so a
            # later call can reconcile them.  Otherwise the accepted response
            # cannot be recovered and this authority becomes uncertain.
            if self._durable_capture_matches(submitting.identity, capture):
                raise V2AIImageOutcomeUncertain(
                    "V2_AI_IMAGE_RESPONSE_CAPTURE_DB_PENDING"
                ) from exc
            return self._mark_failed(
                submitting,
                classification="UNCERTAIN",
                reason_code=self._safe_exception_code(
                    exc, fallback="V2_AI_IMAGE_RESPONSE_CAPTURE_UNCERTAIN"
                ),
            )
        try:
            captured = self._store.record_response_captured(
                effect_id=effect_id,
                expected_revision=submitting.revision,
                expected_record_hash=submitting.record_hash,
                submission_owner_token_hash=owner_token_hash,
                capture=capture,
                response_journal_hash=response_journal_hash,
            )
        except Exception as exc:
            # A CAS implementation may have committed before losing its reply.
            # Never overwrite that possibly-committed state with failure.
            raise V2AIImageOutcomeUncertain(
                "V2_AI_IMAGE_RESPONSE_CAPTURE_DB_UNCERTAIN"
            ) from exc
        self._validate_captured_record(captured, capture)
        if capture.normalization_error_code is not None:
            return self._mark_failed(
                captured,
                classification="DEFINITIVE",
                reason_code=capture.normalization_error_code,
            )
        return self._finish_captured(captured)

    def _reconcile_submitting(
        self, record: V2AIImageSceneEffectRecord
    ) -> V2AIImageSceneEffectRecord:
        journal_path = Path(record.identity.response_capture_journal_path)
        if journal_path.is_file() and not journal_path.is_symlink():
            try:
                journal_bytes = journal_path.read_bytes()
                capture = V2AIImageSafeResponseCapture.model_validate_json(
                    journal_bytes
                )
                self._validate_capture_identity(record.identity, capture)
                captured = self._store.record_response_captured(
                    effect_id=record.identity.effect_id,
                    expected_revision=record.revision,
                    expected_record_hash=record.record_hash,
                    submission_owner_token_hash=(
                        record.submission_owner_token_hash or ""
                    ),
                    capture=capture,
                    response_journal_hash=hashlib.sha256(journal_bytes).hexdigest(),
                )
                self._validate_captured_record(captured, capture)
                if capture.normalization_error_code is not None:
                    return self._mark_failed(
                        captured,
                        classification="DEFINITIVE",
                        reason_code=capture.normalization_error_code,
                    )
                return self._finish_captured(captured)
            except V2AIImageProviderBoundaryError:
                raise
            except Exception:
                # A corrupt or foreign journal cannot authorize completion.
                pass
        lease = record.submission_lease_expires_at
        if lease is not None and self._now() < lease:
            raise V2AIImageExecutionBlocked("V2_AI_IMAGE_SUBMISSION_STILL_IN_FLIGHT")
        return self._mark_failed(
            record,
            classification="UNCERTAIN",
            reason_code="V2_AI_IMAGE_SUBMISSION_OUTCOME_UNCERTAIN",
        )

    def _finish_captured(
        self, record: V2AIImageSceneEffectRecord
    ) -> V2AIImageSceneEffectRecord:
        capture = record.response_capture
        if capture is None:
            raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_RESPONSE_CAPTURE_REQUIRED")
        if capture.normalization_error_code is not None:
            return self._mark_failed(
                record,
                classification="DEFINITIVE",
                reason_code=capture.normalization_error_code,
            )
        identity = record.identity
        semantic = capture.semantic_attestation
        if semantic is None:
            return self._mark_failed(
                record,
                classification="DEFINITIVE",
                reason_code="V2_AI_IMAGE_SEMANTIC_ATTESTATION_REQUIRED",
            )
        if (
            not semantic.semantic_match
            or semantic.semantic_mismatch_reasons
            or semantic.forbidden_content_detected
        ):
            return self._mark_failed(
                record,
                classification="DEFINITIVE",
                reason_code="V2_AI_IMAGE_SEMANTIC_ATTESTATION_BLOCKED",
            )
        self._validate_effect_paths(identity, require_empty=False)
        capture_path = Path(identity.response_capture_path)
        destination = Path(identity.destination_path)
        source = capture_path if capture_path.is_file() else destination
        try:
            qc = self._technical_qc.inspect(
                identity=identity,
                capture=capture,
                path=source,
                now=self._now(),
            )
        except V2AIImageProviderBoundaryError as exc:
            return self._mark_failed(
                record,
                classification="DEFINITIVE",
                reason_code=exc.code,
            )
        try:
            self._materialize_atomic(
                capture_path=capture_path,
                destination=destination,
                expected_checksum=qc.checksum_sha256,
            )
            output_checksum = _sha256_file(destination)
        except V2AIImageOutcomeUncertain:
            raise
        except Exception as exc:
            raise V2AIImageOutcomeUncertain(
                "V2_AI_IMAGE_ATOMIC_OUTPUT_UNCERTAIN"
            ) from exc
        if output_checksum != qc.checksum_sha256:
            raise V2AIImageOutcomeUncertain(
                "V2_AI_IMAGE_ATOMIC_OUTPUT_VERIFICATION_FAILED"
            )
        completed_at = self._now()
        receipt = V2AIImageAssetReceipt.seal(
            asset_id=f"v2-ai-image-asset-{identity.effect_identity_hash[:24]}",
            effect_id=identity.effect_id,
            scene_id=identity.scene_id,
            primary_asset_slot_id=identity.primary_asset_slot_id,
            provider_config_version=identity.provider_config_version,
            provider_config_hash=identity.provider_config_hash,
            generation_policy_hash=identity.generation_policy_hash,
            price_catalog_version=identity.price_catalog_version,
            price_catalog_hash=identity.price_catalog_hash,
            provider_request_id=capture.provider_request_id,
            provider_response_id=capture.provider_response_id,
            idempotency_key=identity.idempotency_key,
            effect_identity_hash=identity.effect_identity_hash,
            request_hash=identity.request_hash,
            response_capture_hash=capture.capture_hash,
            response_journal_hash=record.response_journal_hash,
            scene_plan_hash=identity.scene_plan_hash,
            style_bible_hash=identity.style_bible_hash,
            compiled_prompt_hash=identity.compiled_prompt_hash,
            compiled_prompt_content_hash=identity.compiled_prompt_content_hash,
            prompt_hash=identity.prompt_hash,
            generated_at=capture.captured_at,
            width=qc.width,
            height=qc.height,
            local_ref=str(destination),
            checksum_sha256=qc.checksum_sha256,
            size_bytes=qc.size_bytes,
            provider_attempt_ref=(
                f"v2-ai-image-effect://{identity.effect_id}/provider-attempt/1"
            ),
            cost_ref=identity.cost_estimate_ref,
            actual_cost_usd=None,
            conservative_settlement_cost_usd=identity.estimated_cost_usd,
            qc_ref=f"v2-ai-image-effect://{identity.effect_id}/technical-qc",
            qc_hash=qc.qc_hash,
            technical_qc=qc,
            semantic_attestation=semantic,
        )
        try:
            verified = self._store.mark_verified(
                effect_id=identity.effect_id,
                expected_revision=record.revision,
                expected_record_hash=record.record_hash,
                receipt=receipt,
                completed_at=completed_at,
            )
        except Exception as exc:
            reconciled = self._store.load(effect_id=identity.effect_id)
            if (
                reconciled is not None
                and reconciled.state == V2AIImageEffectState.VERIFIED
                and reconciled.asset_receipt == receipt
            ):
                return reconciled
            raise V2AIImageOutcomeUncertain(
                "V2_AI_IMAGE_VERIFIED_DB_COMMIT_UNCERTAIN"
            ) from exc
        if (
            verified.state != V2AIImageEffectState.VERIFIED
            or verified.asset_receipt != receipt
            or verified.provider_call_count != 1
        ):
            raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_VERIFIED_DB_COMMIT_INVALID")
        return verified

    def _capture_response(
        self,
        *,
        identity: V2AIImageSceneEffectIdentity,
        response: Any,
    ) -> tuple[V2AIImageSafeResponseCapture, bytes | None]:
        provider_id = _safe_provider_identifier(_value(response, "id"))
        status = _safe_provider_status(_value(response, "status"))
        outputs = _image_outputs(response)
        text_outputs = _text_outputs(response)
        usage = _safe_usage(_value(response, "usage"))
        error: str | None = None
        data: bytes | None = None
        supplied_mime: str | None = None
        semantic_attestation: V2AIImageAssetSemanticAttestation | None = None
        if status != "COMPLETED":
            error = f"V2_AI_IMAGE_PROVIDER_STATUS_{status}"
        elif len(outputs) != 1:
            error = "V2_AI_IMAGE_PROVIDER_OUTPUT_COUNT_INVALID"
        else:
            output = outputs[0]
            uri = _value(output, "uri")
            supplied_mime = _value(output, "mime_type")
            encoded = _value(output, "data")
            if uri is not None:
                error = "V2_AI_IMAGE_INLINE_DELIVERY_REQUIRED"
            elif supplied_mime != "image/jpeg":
                error = "V2_AI_IMAGE_INLINE_JPEG_MIME_REQUIRED"
            elif not isinstance(encoded, str) or not encoded:
                error = "V2_AI_IMAGE_INLINE_DATA_MISSING"
            elif len(encoded) > ((V2_GEMINI_IMAGE_MAX_RASTER_BYTES + 2) // 3) * 4:
                error = "V2_AI_IMAGE_INLINE_DATA_TOO_LARGE"
            else:
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError):
                    error = "V2_AI_IMAGE_INLINE_DATA_INVALID"
                if (
                    data is not None
                    and not 0 < len(data) <= V2_GEMINI_IMAGE_MAX_RASTER_BYTES
                ):
                    data = None
                    error = "V2_AI_IMAGE_OUTPUT_BYTES_INVALID"
        if error is None and data is not None:
            if len(text_outputs) != 1:
                error = "V2_AI_IMAGE_SEMANTIC_OUTPUT_COUNT_INVALID"
            else:
                try:
                    semantic_attestation = _semantic_attestation_from_provider_text(
                        identity=identity,
                        asset_checksum=hashlib.sha256(data).hexdigest(),
                        provider_text=text_outputs[0],
                    )
                except ValueError as exc:
                    error = (
                        "V2_AI_IMAGE_SEMANTIC_ATTESTATION_SCENE_MISMATCH"
                        if "SCENE_MISMATCH" in str(exc)
                        else "V2_AI_IMAGE_SEMANTIC_ATTESTATION_INVALID"
                    )
            if error is not None:
                data = None
        values: dict[str, Any] = {
            "effect_id": identity.effect_id,
            "effect_identity_hash": identity.effect_identity_hash,
            "provider_request_id": provider_id,
            "provider_response_id": provider_id,
            "provider_status": status,
            "output_count": len(outputs),
            "semantic_output_count": len(text_outputs),
            "response_capture_journal_path": identity.response_capture_journal_path,
            "usage": usage,
            "normalization_error_code": error,
            "captured_at": self._now(),
        }
        if error is None and data is not None:
            values.update(
                {
                    "output_mime_type": supplied_mime,
                    "output_size_bytes": len(data),
                    "output_sha256": hashlib.sha256(data).hexdigest(),
                    "response_capture_path": identity.response_capture_path,
                    "semantic_attestation": semantic_attestation,
                }
            )
        return V2AIImageSafeResponseCapture.seal(**values), data

    def _persist_response_capture(
        self,
        *,
        identity: V2AIImageSceneEffectIdentity,
        capture: V2AIImageSafeResponseCapture,
        output_bytes: bytes | None,
    ) -> str:
        self._validate_capture_identity(identity, capture)
        if capture.normalization_error_code is None:
            if output_bytes is None:
                raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_CAPTURE_BYTES_MISSING")
            _write_bytes_atomic_exact(
                Path(identity.response_capture_path),
                output_bytes,
                expected_sha256=capture.output_sha256 or "",
            )
        journal_bytes = (
            json.dumps(
                capture.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode("utf-8")
        _write_bytes_atomic_exact(
            Path(identity.response_capture_journal_path),
            journal_bytes,
            expected_sha256=hashlib.sha256(journal_bytes).hexdigest(),
        )
        return hashlib.sha256(journal_bytes).hexdigest()

    @staticmethod
    def _request_journal_bytes(
        identity: V2AIImageSceneEffectIdentity,
    ) -> bytes:
        payload = _request_journal_payload(
            identity.model_dump(mode="json", exclude={"effect_identity_hash"})
        )
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != identity.request_journal_hash:
            raise V2AIImageExecutionBlocked("V2_AI_IMAGE_REQUEST_JOURNAL_HASH_MISMATCH")
        return encoded

    def _persist_request_journal(self, identity: V2AIImageSceneEffectIdentity) -> None:
        encoded = self._request_journal_bytes(identity)
        _write_bytes_atomic_exact(
            Path(identity.request_journal_path),
            encoded,
            expected_sha256=identity.request_journal_hash,
        )

    def _verify_request_journal(self, identity: V2AIImageSceneEffectIdentity) -> None:
        path = Path(identity.request_journal_path)
        if (
            not path.is_file()
            or path.is_symlink()
            or _sha256_file(path) != identity.request_journal_hash
            or path.read_bytes() != self._request_journal_bytes(identity)
        ):
            raise V2AIImageExecutionBlocked(
                "V2_AI_IMAGE_DURABLE_REQUEST_JOURNAL_REQUIRED"
            )

    @staticmethod
    def _durable_capture_matches(
        identity: V2AIImageSceneEffectIdentity,
        capture: V2AIImageSafeResponseCapture,
    ) -> bool:
        journal_path = Path(identity.response_capture_journal_path)
        try:
            if journal_path.is_symlink() or not journal_path.is_file():
                return False
            persisted = V2AIImageSafeResponseCapture.model_validate_json(
                journal_path.read_bytes()
            )
            if persisted != capture:
                return False
            if capture.normalization_error_code is not None:
                return True
            output_path = Path(identity.response_capture_path)
            return bool(
                output_path.is_file()
                and not output_path.is_symlink()
                and output_path.stat().st_size == capture.output_size_bytes
                and _sha256_file(output_path) == capture.output_sha256
            )
        except (OSError, ValueError):
            return False

    def _record_submission_exception(
        self, record: V2AIImageSceneEffectRecord, exc: Exception
    ) -> V2AIImageSceneEffectRecord:
        status_code = getattr(exc, "status_code", None)
        definitive = (
            isinstance(status_code, int)
            and 400 <= status_code < 500
            and status_code not in {408, 409, 425, 429}
        )
        reason = (
            f"V2_AI_IMAGE_PROVIDER_HTTP_{status_code}"
            if isinstance(status_code, int) and 100 <= status_code <= 599
            else self._safe_exception_code(
                exc, fallback="V2_AI_IMAGE_PROVIDER_SUBMISSION_UNCERTAIN"
            )
        )
        return self._mark_failed(
            record,
            classification="DEFINITIVE" if definitive else "UNCERTAIN",
            reason_code=reason,
        )

    def _mark_failed(
        self,
        record: V2AIImageSceneEffectRecord,
        *,
        classification: Literal["DEFINITIVE", "UNCERTAIN"],
        reason_code: str,
    ) -> V2AIImageSceneEffectRecord:
        safe_code = _normalize_safe_code(reason_code)
        failure = V2AIImageFailureReceipt.seal(
            effect_id=record.identity.effect_id,
            classification=classification,
            reason_code=safe_code,
            failed_at=self._now(),
        )
        method = (
            self._store.mark_failed_definitive
            if classification == "DEFINITIVE"
            else self._store.mark_failed_uncertain
        )
        try:
            failed = method(
                effect_id=record.identity.effect_id,
                expected_revision=record.revision,
                expected_record_hash=record.record_hash,
                failure=failure,
            )
        except Exception as exc:
            reconciled = self._store.load(effect_id=record.identity.effect_id)
            if reconciled is not None and reconciled.failure_receipt == failure:
                return reconciled
            raise V2AIImageOutcomeUncertain(
                "V2_AI_IMAGE_FAILURE_DB_COMMIT_UNCERTAIN"
            ) from exc
        expected_state = (
            V2AIImageEffectState.FAILED_DEFINITIVE
            if classification == "DEFINITIVE"
            else V2AIImageEffectState.FAILED_UNCERTAIN
        )
        if (
            failed.state != expected_state
            or failed.failure_receipt != failure
            or failed.provider_call_count != 1
            or failed.retry_allowed
            or failed.fallback_allowed
        ):
            raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_FAILURE_DB_COMMIT_INVALID")
        return failed

    def _validate_identity_authorities(
        self, identity: V2AIImageSceneEffectIdentity
    ) -> None:
        if (
            identity.provider_key != V2_GEMINI_IMAGE_PROVIDER_KEY
            or identity.adapter_key != V2_GEMINI_IMAGE_PRODUCTION_ADAPTER_KEY
            or identity.provider_config_version
            != V2_GEMINI_IMAGE_PROVIDER_CONFIG_VERSION
            or identity.model_id != GEMINI_IMAGE_DEFAULT_MODEL_ID
            or identity.image_size != "2K"
            or identity.aspect_ratio != "16:9"
            or identity.output_count != 1
            or identity.maximum_attempts != 1
            or identity.provider_retry_allowed
            or identity.provider_fallback_allowed
        ):
            raise V2AIImageExecutionBlocked("V2_AI_IMAGE_EXACT_ROUTE_INVALID")
        if (
            identity.price_catalog_version != self._catalog.version
            or identity.price_catalog_ref != self._catalog.ref
            or identity.price_catalog_hash != self.catalog_hash
        ):
            raise V2AIImageExecutionBlocked("V2_AI_IMAGE_PRICE_CATALOG_DRIFT")
        row = self._catalog.row(
            model_id=identity.model_id,
            image_size=identity.image_size,
            aspect_ratio=identity.aspect_ratio,
        )
        catalog_cost = Decimal(str(row["estimated_unit_cost_usd"]))
        conservative_cost = catalog_cost + V2_GEMINI_IMAGE_SEMANTIC_TOKEN_ALLOWANCE_USD
        semantic_token_upper_bound = (
            Decimal(V2_GEMINI_IMAGE_MAX_PROVIDER_INPUT_BYTES)
            * V2_GEMINI_IMAGE_INPUT_PRICE_PER_MILLION_TOKENS_USD
            + Decimal(
                V2_GEMINI_IMAGE_MAX_OUTPUT_TOKENS - V2_GEMINI_IMAGE_2K_OUTPUT_TOKENS
            )
            * V2_GEMINI_IMAGE_TEXT_THINKING_PRICE_PER_MILLION_TOKENS_USD
        ) / Decimal("1000000")
        if (
            row.get("policy_state") != "ALLOWED"
            or semantic_token_upper_bound > V2_GEMINI_IMAGE_SEMANTIC_TOKEN_ALLOWANCE_USD
            or conservative_cost != V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD
            or identity.estimated_cost_usd != conservative_cost
            or identity.estimated_cost_usd > identity.maximum_cost_usd
        ):
            raise V2AIImageExecutionBlocked("V2_AI_IMAGE_COST_AUTHORITY_INVALID")

    @staticmethod
    def _validate_record_identity(
        record: V2AIImageSceneEffectRecord,
        identity: V2AIImageSceneEffectIdentity,
    ) -> None:
        if (
            record.identity.effect_id != identity.effect_id
            or record.identity.effect_identity_hash != identity.effect_identity_hash
        ):
            raise V2AIImageExecutionBlocked("V2_AI_IMAGE_IDEMPOTENCY_IDENTITY_CONFLICT")

    @staticmethod
    def _validate_submitting_claim(
        record: V2AIImageSceneEffectRecord,
        *,
        identity: V2AIImageSceneEffectIdentity,
        owner_token_hash: str,
    ) -> None:
        if (
            record.state != V2AIImageEffectState.SUBMITTING
            or record.identity.effect_identity_hash != identity.effect_identity_hash
            or record.provider_call_count != 1
            or record.submission_owner_token_hash != owner_token_hash
        ):
            raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_SUBMITTING_DB_CLAIM_INVALID")

    @staticmethod
    def _validate_captured_record(
        record: V2AIImageSceneEffectRecord,
        capture: V2AIImageSafeResponseCapture,
    ) -> None:
        if (
            record.state != V2AIImageEffectState.RESPONSE_CAPTURED
            or record.response_capture != capture
            or record.provider_call_count != 1
        ):
            raise V2AIImageOutcomeUncertain(
                "V2_AI_IMAGE_RESPONSE_CAPTURE_DB_COMMIT_INVALID"
            )

    @staticmethod
    def _validate_capture_identity(
        identity: V2AIImageSceneEffectIdentity,
        capture: V2AIImageSafeResponseCapture,
    ) -> None:
        if (
            capture.effect_id != identity.effect_id
            or capture.effect_identity_hash != identity.effect_identity_hash
            or capture.response_capture_journal_path
            != identity.response_capture_journal_path
            or (
                capture.response_capture_path is not None
                and capture.response_capture_path != identity.response_capture_path
            )
            or (
                capture.semantic_attestation is not None
                and (
                    capture.semantic_attestation.effect_id != identity.effect_id
                    or capture.semantic_attestation.scene_id != identity.scene_id
                    or capture.semantic_attestation.scene_plan_hash
                    != identity.scene_plan_hash
                    or capture.semantic_attestation.prompt_hash != identity.prompt_hash
                    or capture.semantic_attestation.asset_checksum
                    != capture.output_sha256
                    or capture.semantic_attestation.required_semantic_anchors
                    != identity.required_semantic_anchors
                )
            )
        ):
            raise V2AIImageOutcomeUncertain(
                "V2_AI_IMAGE_RESPONSE_CAPTURE_IDENTITY_MISMATCH"
            )

    def _validate_effect_paths(
        self,
        identity: V2AIImageSceneEffectIdentity,
        *,
        require_empty: bool,
    ) -> None:
        root = Path(identity.workspace_root).expanduser().resolve(strict=True)
        if not root.is_dir() or root.is_symlink():
            raise V2AIImageExecutionBlocked("V2_AI_IMAGE_WORKSPACE_ROOT_INVALID")
        paths = tuple(
            Path(value).expanduser().resolve(strict=False)
            for value in (
                identity.request_journal_path,
                identity.response_capture_path,
                identity.response_capture_journal_path,
                identity.destination_path,
            )
        )
        if len(set(paths)) != len(paths):
            raise V2AIImageExecutionBlocked("V2_AI_IMAGE_EFFECT_PATHS_NOT_DISTINCT")
        for path in paths:
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise V2AIImageExecutionBlocked(
                    "V2_AI_IMAGE_PATH_ESCAPES_WORKSPACE"
                ) from exc
            if path == root or path.is_symlink():
                raise V2AIImageExecutionBlocked("V2_AI_IMAGE_OUTPUT_PATH_INVALID")
            current = path.parent
            while current != root:
                if current.exists() and current.is_symlink():
                    raise V2AIImageExecutionBlocked("V2_AI_IMAGE_OUTPUT_PARENT_SYMLINK")
                current = current.parent
            if (
                path
                != Path(identity.request_journal_path)
                .expanduser()
                .resolve(strict=False)
                and require_empty
                and (path.exists() or path.with_name(path.name + ".part").exists())
            ):
                raise V2AIImageExecutionBlocked(
                    "V2_AI_IMAGE_OUTPUT_DESTINATION_NOT_EMPTY"
                )
        if Path(identity.destination_path).suffix.casefold() not in {".jpg", ".jpeg"}:
            raise V2AIImageExecutionBlocked("V2_AI_IMAGE_DESTINATION_JPEG_REQUIRED")

    def _load_required(self, effect_id: str) -> V2AIImageSceneEffectRecord:
        record = self._store.load(effect_id=effect_id)
        if record is None:
            raise V2AIImageExecutionBlocked("V2_AI_IMAGE_EFFECT_NOT_PREPARED")
        self._validate_identity_authorities(record.identity)
        return record

    def _now(self) -> datetime:
        return _aware_utc(self._clock())

    @staticmethod
    def _safe_exception_code(exc: Exception, *, fallback: str) -> str:
        if isinstance(exc, V2AIImageProviderBoundaryError):
            return exc.code
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int) and 100 <= status_code <= 599:
            return f"V2_AI_IMAGE_PROVIDER_HTTP_{status_code}"
        class_name = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).upper()
        class_name = re.sub(r"[^A-Z0-9_]", "_", class_name)[:64]
        candidate = f"V2_AI_IMAGE_PROVIDER_{class_name}" if class_name else fallback
        return _normalize_safe_code(candidate)

    @staticmethod
    def _materialize_atomic(
        *, capture_path: Path, destination: Path, expected_checksum: str
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if (
                not destination.is_file()
                or destination.is_symlink()
                or _sha256_file(destination) != expected_checksum
            ):
                raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_DESTINATION_CONFLICT")
            if (
                capture_path.is_file()
                and _sha256_file(capture_path) == expected_checksum
            ):
                capture_path.unlink()
                _fsync_directory(capture_path.parent)
            return
        if not capture_path.is_file() or capture_path.is_symlink():
            raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_CAPTURE_FILE_MISSING")
        if _sha256_file(capture_path) != expected_checksum:
            raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_CAPTURE_CHECKSUM_MISMATCH")
        os.replace(capture_path, destination)
        _fsync_directory(destination.parent)


def _normalize_safe_code(value: str) -> str:
    candidate = re.sub(r"[^A-Z0-9_]", "_", str(value).upper())[:120]
    return (
        candidate if re.fullmatch(SAFE_CODE_PATTERN, candidate) else "V2_AI_IMAGE_ERROR"
    )


def _value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _safe_provider_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if re.fullmatch(SAFE_PROVIDER_ID_PATTERN, candidate) else None


def _safe_provider_status(value: Any) -> str:
    if not isinstance(value, str):
        return "UNKNOWN"
    normalized = value.strip().casefold()
    known = {
        "in_progress",
        "requires_action",
        "completed",
        "failed",
        "cancelled",
        "incomplete",
        "budget_exceeded",
    }
    return normalized.upper() if normalized in known else "UNKNOWN"


def _image_outputs(response: Any) -> list[Any]:
    images: list[Any] = []
    steps = _value(response, "steps")
    if isinstance(steps, list):
        for step in steps:
            if _value(step, "type") != "model_output":
                continue
            content = _value(step, "content")
            if isinstance(content, list):
                images.extend(
                    item for item in content if _value(item, "type") == "image"
                )
    output_image = _value(response, "output_image")
    if output_image is not None and _value(output_image, "type") == "image":
        aliases_existing = any(
            all(
                _value(item, field) == _value(output_image, field)
                for field in ("data", "uri", "mime_type")
            )
            for item in images
        )
        if not aliases_existing:
            images.append(output_image)
    return images


def _text_outputs(response: Any) -> list[str]:
    texts: list[str] = []
    steps = _value(response, "steps")
    if isinstance(steps, list):
        for step in steps:
            if _value(step, "type") != "model_output":
                continue
            content = _value(step, "content")
            if not isinstance(content, list):
                continue
            for item in content:
                if _value(item, "type") not in {"text", "output_text"}:
                    continue
                candidate = _value(item, "text")
                if isinstance(candidate, str) and candidate.strip():
                    texts.append(candidate.strip())
    direct = _value(response, "output_text")
    if isinstance(direct, str) and direct.strip() and direct.strip() not in texts:
        texts.append(direct.strip())
    return texts


def _semantic_attestation_from_provider_text(
    *,
    identity: V2AIImageSceneEffectIdentity,
    asset_checksum: str,
    provider_text: str,
) -> V2AIImageAssetSemanticAttestation:
    normalized = provider_text.strip()
    try:
        observed = _V2GeminiImageObservedOutput.model_validate_json(normalized)
    except (TypeError, ValueError) as exc:
        raise ValueError("V2_AI_IMAGE_SEMANTIC_ATTESTATION_INVALID") from exc
    if observed.scene_id != identity.scene_id:
        raise ValueError("V2_AI_IMAGE_SEMANTIC_ATTESTATION_SCENE_MISMATCH")
    return V2AIImageAssetSemanticAttestation.seal(
        effect_id=identity.effect_id,
        scene_id=identity.scene_id,
        scene_plan_hash=identity.scene_plan_hash,
        prompt_hash=identity.prompt_hash,
        asset_checksum=asset_checksum,
        observed_output_summary=observed.observed_output_summary,
        observed_primary_subjects=observed.observed_primary_subjects,
        observed_action_or_relation=observed.observed_action_or_relation,
        observed_environment=observed.observed_environment,
        required_semantic_anchors=identity.required_semantic_anchors,
        observed_semantic_anchors=observed.observed_semantic_anchors,
        semantic_match=observed.semantic_match,
        semantic_mismatch_reasons=observed.semantic_mismatch_reasons,
        forbidden_content_detected=observed.forbidden_content_detected,
        model_asserts_description_is_of_generated_output=(
            observed.description_is_of_generated_output
        ),
        provider_text_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


def _safe_usage(value: Any) -> dict[str, int]:
    fields = (
        "total_input_tokens",
        "total_cached_tokens",
        "total_output_tokens",
        "total_tool_use_tokens",
        "total_thought_tokens",
        "total_tokens",
    )
    result: dict[str, int] = {}
    for field in fields:
        candidate = _value(value, field)
        if (
            isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and candidate >= 0
        ):
            result[field] = candidate
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes_atomic_exact(path: Path, data: bytes, *, expected_sha256: str) -> None:
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_ATOMIC_WRITE_HASH_INVALID")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if (
            not path.is_file()
            or path.is_symlink()
            or _sha256_file(path) != expected_sha256
        ):
            raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_DURABLE_CAPTURE_CONFLICT")
        return
    part = path.with_name(path.name + ".part")
    if part.exists():
        raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_UNOWNED_PART_FILE_EXISTS")
    created = False
    try:
        with part.open("xb") as stream:
            created = True
            for offset in range(0, len(data), 1024 * 1024):
                stream.write(data[offset : offset + 1024 * 1024])
            stream.flush()
            os.fsync(stream.fileno())
        if _sha256_file(part) != expected_sha256:
            raise V2AIImageOutcomeUncertain("V2_AI_IMAGE_ATOMIC_WRITE_HASH_MISMATCH")
        os.replace(part, path)
        _fsync_directory(path.parent)
    except Exception:
        if created:
            part.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "V2AIImageAssetReceipt",
    "V2AIImageAssetSemanticAttestation",
    "V2AIImageEffectState",
    "V2AIImageExecutionBlocked",
    "V2AIImageFailureReceipt",
    "V2AIImageOutcomeUncertain",
    "V2AIImageProductionService",
    "V2AIImageProviderBoundaryError",
    "V2AIImageRecordTransitions",
    "V2AIImageSafeResponseCapture",
    "V2AIImageSceneEffectIdentity",
    "V2AIImageSceneEffectRecord",
    "V2AIImageSceneEffectStore",
    "V2AIImageTechnicalQC",
    "V2AIImageTechnicalQCReceipt",
    "V2GeminiImageClient",
    "V2GeminiImageOfficialClientFactory",
    "V2GeminiImageProductionAdapter",
    "V2GeminiImageProductionReadiness",
    "V2_GEMINI_IMAGE_PRODUCTION_ADAPTER_KEY",
    "V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD",
    "V2_GEMINI_IMAGE_DB_RECORD_SCHEMA",
    "V2_GEMINI_IMAGE_PROVIDER_CONFIG_VERSION",
    "v2_ai_image_required_semantic_anchors",
    "v2_gemini_image_provider_config_hash",
]
