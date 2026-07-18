from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import importlib
import json
import os
import re
import shutil
import struct
import threading
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.contracts.ai_image import AIImageRequest, CompiledImagePrompt, ai_image_stable_hash
from app.contracts.google_gemini_image import (
    GeminiImageExecutionGates,
    GeminiImageGenerationRequest,
    GeminiImageOperationReceipt,
    GeminiImageOutputMaterializationPlan,
    GeminiImageReadiness,
)
from app.contracts.img_canary import (
    IMGCanaryDriveReadinessEvidence,
    IMGCanaryPreflightEvidence,
    IMGCanaryPreviousRunImmutabilityEvidence,
    IMGCanaryPreviousRunsImmutabilityEvidence,
    IMGCanarySerializedRequestEvidence,
    IMGCanaryV2ApprovalBinding,
    IMGCanaryV3ApprovalBinding,
    IMGCanaryV3SerializedRequestEvidence,
)
from app.contracts.img_canary_security import (
    IMGCanaryMonthlyBudgetAuthorityLedger,
    IMGCanaryTaskAuthorizationLedger,
    img_canary_task_authority_identity,
)
from app.core.config import (
    GEMINI_IMAGE_APPROVED_MODEL_IDS,
    GEMINI_IMAGE_SUPPORTED_ASPECT_RATIOS,
    GEMINI_IMAGE_SUPPORTED_SIZES,
    Settings,
    get_settings,
)
from app.services.provider_stack import CANONICAL_PROVIDER_KEYS
from app.services.img_canary_security import (
    IMGCanaryCredentialRotationAuthority,
    IMGCanarySecurityAuthorityError,
    IMGCanaryTaskAuthorizationStore,
)
from app.services.google_gemini_image_catalog import (
    GoogleGeminiImageModelPriceCatalog,
)


MAX_RASTER_FILE_BYTES = 64 * 1024 * 1024
MAX_RASTER_PIXELS = 16_777_216
GEMINI_IMAGE_REQUEST_TIMEOUT_SECONDS = 120.0
GEMINI_IMAGE_SAFE_DECODE_TIMEOUT_SECONDS = 30.0
GEMINI_IMAGE_DECODER_PROBE_TIMEOUT_SECONDS = 10.0
GEMINI_IMAGE_MAX_ERROR_DIAGNOSTIC_BODY_BYTES = 32 * 1024
GEMINI_IMAGE_V2_MINIMUM_SOURCE_WIDTH = 1920
GEMINI_IMAGE_V2_MINIMUM_SOURCE_HEIGHT = 1080
# The repository's 2K fixture is 2752x1536 (about 0.78% wider than mathematical
# 16:9), so enforce the provider bucket with a tight one-percent tolerance while
# still rejecting a wrong orientation/aspect.
GEMINI_IMAGE_V2_ASPECT_RATIO_TOLERANCE_PERCENT = 1
GEMINI_IMAGE_USAGE_FIELDS = (
    "total_input_tokens",
    "total_cached_tokens",
    "total_output_tokens",
    "total_tool_use_tokens",
    "total_thought_tokens",
    "total_tokens",
)
GEMINI_IMAGE_SAFE_ERROR_CATEGORIES = frozenset(
    {
        "ABORTED",
        "ALREADY_EXISTS",
        "CANCELLED",
        "DEADLINE_EXCEEDED",
        "FAILED_PRECONDITION",
        "INTERNAL",
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "OUT_OF_RANGE",
        "PERMISSION_DENIED",
        "RESOURCE_EXHAUSTED",
        "UNAUTHENTICATED",
        "UNAVAILABLE",
        "UNIMPLEMENTED",
        "UNKNOWN",
    }
)
GEMINI_IMAGE_SAFE_PARAMETER_PATHS = frozenset(
    {
        "background",
        "input",
        "model",
        "response_format",
        "response_format.aspect_ratio",
        "response_format.delivery",
        "response_format.image_size",
        "response_format.mime_type",
        "response_format.type",
        "response_modalities",
        "store",
        "stream",
    }
)


class GeminiImageFixtureClient(Protocol):
    def submit(self, request: GeminiImageGenerationRequest) -> dict[str, Any]: ...


class GeminiImageInteractionsAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class GeminiImageRealClient(Protocol):
    interactions: GeminiImageInteractionsAPI


class GeminiImagePaidAttemptStore(Protocol):
    def load(self) -> Any: ...

    def consume_at_submit(self, *, expected_fingerprint: str, now: datetime) -> Any: ...

    def finalize(
        self,
        *,
        succeeded: bool,
        now: datetime,
        provider_request_id_ref: str | None = None,
        provider_operation_id_ref: str | None = None,
        failure_reason_code: str | None = None,
    ) -> Any: ...


class GeminiImageResponseSafetyError(ValueError):
    """Safe response-normalization error whose message is durable-safe."""

    def __init__(self, code: str, *, normalized_status: str = "OUTPUT_MISSING"):
        super().__init__(code)
        self.code = code
        self.normalized_status = normalized_status


@dataclass(frozen=True)
class GeminiImageTransientOutput:
    """Execution-only bytes/URL; callers must never serialize this object."""

    output_reference: str
    image_bytes: bytes = field(repr=False)
    raw_temporary_url: str | None = field(default=None, repr=False)
    mime_type: str = "image/png"
    transport: str = "LOCAL_FIXTURE_ONLY"
    provider_call_made: bool = False


class GoogleGeminiImageAdapter:
    provider_key = "google_gemini_image"
    vendor = "google"
    capability = "AI_IMAGE_GENERATION"
    transport = "GEMINI_API_NATIVE"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fixture_client: GeminiImageFixtureClient | None = None,
        real_client: GeminiImageRealClient | None = None,
        raster_decoder_path: str | None = None,
    ):
        self.settings = settings or get_settings()
        self.fixture_client = fixture_client
        self.real_client = real_client
        self.raster_decoder_path = raster_decoder_path or self._default_raster_decoder_path()
        self._operations_by_fingerprint: dict[str, GeminiImageOperationReceipt] = {}
        self._transient_by_job: dict[str, GeminiImageTransientOutput] = {}
        self._response_summaries_by_job: dict[str, dict[str, Any]] = {}
        self._materialization_receipts_by_job: dict[str, dict[str, Any]] = {}
        self._state_lock = threading.Lock()

    def validate_configuration(self) -> GeminiImageReadiness:
        key_configured = bool(
            self.settings.gemini_api_key
            and self.settings.gemini_api_key.get_secret_value().strip()
        )
        model_configured = self.settings.gemini_image_model_id in GEMINI_IMAGE_APPROVED_MODEL_IDS
        catalog_path = (
            Path(__file__).resolve().parents[2]
            / "config"
            / "google_gemini_image_model_price_catalog.yaml"
        )
        catalog_present = catalog_path.is_file()
        raster_decoder_ready = self.raster_decoder_ready()
        route_registered = self.provider_key in CANONICAL_PROVIDER_KEYS
        execution_enabled = bool(self.settings.gemini_image_real_generation_enabled)
        fixture_only = bool(self.settings.img1_fixture_only)
        ready_for_future_approval = all(
            (
                route_registered,
                key_configured,
                model_configured,
                catalog_present,
                raster_decoder_ready,
                self.settings.gemini_image_provider_route_approved,
            )
        )
        return GeminiImageReadiness(
            provider_route_registered=route_registered,
            credential_configured=key_configured,
            model_configured=model_configured,
            model_catalog_present=catalog_present,
            raster_decoder_ready=raster_decoder_ready,
            route_approval_state=self.settings.gemini_image_provider_route_approved,
            execution_enabled=execution_enabled,
            fixture_only=fixture_only,
            cost_catalog_state="PRESENT" if catalog_present else "MISSING",
            global_kill_switch_open=bool(
                self.settings.provider_real_execution_enabled
                and self.settings.provider_production_execution_enabled
                and not self.settings.media_provider_calls_disabled
            ),
            provider_kill_switch_open=execution_enabled and not fixture_only,
            exact_next_action=(
                "Keep IMG1 fixture-only; require VQC1 and one separately approved paid canary."
                if ready_for_future_approval
                else (
                    "Configure missing route, credential, model catalog or JPEG decoder evidence; "
                    "do not generate content."
                )
            ),
        )

    @staticmethod
    def interaction_create_kwargs(
        request: GeminiImageGenerationRequest,
    ) -> dict[str, Any]:
        response_format: dict[str, Any] = {
            "type": "image",
            "mime_type": "image/jpeg",
        }
        # V1/V2 evidence already commits to delivery=inline. V3 is a new
        # approval-specific contract matching the accepted SDK surface and must
        # omit this unsupported serialized field without rewriting history.
        if not request.uses_img_canary_v3_response_contract:
            response_format["delivery"] = "inline"
        response_format.update(
            {
                "aspect_ratio": request.aspect_ratio,
                "image_size": request.image_size,
            }
        )
        return {
            "model": request.model_id,
            "input": request.prompt,
            "stream": False,
            "store": False,
            "background": False,
            "response_format": response_format,
            "timeout": GEMINI_IMAGE_REQUEST_TIMEOUT_SECONDS,
        }

    @classmethod
    def expected_serialized_request_body(
        cls,
        request: GeminiImageGenerationRequest,
    ) -> dict[str, Any]:
        payload = cls.interaction_create_kwargs(request)
        payload.pop("timeout")
        return payload

    @classmethod
    def capture_official_sdk_serialization(
        cls,
        request: GeminiImageGenerationRequest,
    ) -> dict[str, Any]:
        """Capture exact official-SDK bytes through a local-only MockTransport."""

        try:
            genai = importlib.import_module("google.genai")
            types = importlib.import_module("google.genai.types")
            httpx = importlib.import_module("httpx")
        except ImportError as exc:
            raise RuntimeError("GEMINI_IMAGE_SERIALIZATION_RUNTIME_UNAVAILABLE") from exc
        captured: list[dict[str, Any]] = []

        def handler(http_request: Any) -> Any:
            raw = bytes(http_request.content)
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise RuntimeError("GEMINI_IMAGE_SERIALIZATION_BODY_NOT_OBJECT")
            captured.append(
                {
                    "method": str(http_request.method),
                    "path": urlsplit(str(http_request.url)).path,
                    "body": body,
                    "body_sha256": hashlib.sha256(raw).hexdigest(),
                    "credential_in_url": "serialization-only-placeholder" in str(
                        http_request.url
                    ),
                    "credential_in_body": b"serialization-only-placeholder" in raw,
                    "sdk_retries_disabled": True,
                }
            )
            return httpx.Response(
                200,
                json={
                    "id": "interactions/img-canary-v2-serialization",
                    "status": "completed",
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [{"type": "text", "text": "local-only"}],
                        }
                    ],
                },
                request=http_request,
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        client = genai.Client(
            api_key="serialization-only-placeholder",
            http_options=types.HttpOptions(
                httpx_client=http_client,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        try:
            cls._disable_interactions_retries(client.interactions)
            client.interactions.create(**cls.interaction_create_kwargs(request))
        finally:
            client.close()
        if len(captured) != 1:
            raise RuntimeError("GEMINI_IMAGE_SERIALIZATION_CALL_COUNT_INVALID")
        result = captured[0]
        if (
            result["method"] != "POST"
            or result["path"] != "/v1beta/interactions"
            or result["credential_in_url"]
            or result["credential_in_body"]
            or result["body"] != cls.expected_serialized_request_body(request)
        ):
            raise RuntimeError("GEMINI_IMAGE_SERIALIZATION_CONTRACT_MISMATCH")
        if "serialization-only-placeholder" in json.dumps(result, sort_keys=True):
            raise RuntimeError("GEMINI_IMAGE_SERIALIZATION_CREDENTIAL_PERSISTENCE")
        return result

    def build_request(
        self,
        generic_request: AIImageRequest,
        compiled_prompt: CompiledImagePrompt,
    ) -> GeminiImageGenerationRequest:
        if generic_request.provider_route != self.provider_key:
            raise ValueError("GEMINI_IMAGE_PROVIDER_ROUTE_MISMATCH")
        if generic_request.request_hash != ai_image_stable_hash(
            generic_request.model_dump(mode="json", exclude={"request_hash"})
        ):
            raise ValueError("GEMINI_IMAGE_GENERIC_REQUEST_HASH_INVALID")
        if compiled_prompt.content_hash != ai_image_stable_hash(
            compiled_prompt.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("GEMINI_IMAGE_COMPILED_PROMPT_CONTENT_HASH_INVALID")
        if compiled_prompt.generic_request_hash != generic_request.request_hash:
            raise ValueError("GEMINI_IMAGE_COMPILED_PROMPT_REQUEST_MISMATCH")
        if compiled_prompt.generic_request_ref != generic_request.request_id:
            raise ValueError("GEMINI_IMAGE_COMPILED_PROMPT_REQUEST_REF_MISMATCH")
        if (
            compiled_prompt.scene_id != generic_request.scene_id
            or compiled_prompt.visual_source_decision_ref
            != generic_request.visual_source_decision_ref
            or compiled_prompt.visual_source_decision_hash
            != generic_request.visual_source_decision_hash
            or compiled_prompt.visual_direction_contract_ref
            != generic_request.visual_direction_contract_ref
            or compiled_prompt.visual_direction_contract_hash
            != generic_request.visual_direction_contract_hash
        ):
            raise ValueError("GEMINI_IMAGE_COMPILED_PROMPT_SOURCE_BINDING_MISMATCH")
        if compiled_prompt.prompt_hash != ai_image_stable_hash(compiled_prompt.prompt):
            raise ValueError("GEMINI_IMAGE_COMPILED_PROMPT_HASH_INVALID")
        payload: dict[str, Any] = {
            "generic_request_ref": generic_request.request_id,
            "generic_request_hash": generic_request.request_hash,
            "project_id": generic_request.project_id,
            "scene_id": generic_request.scene_id,
            "visual_source_decision_hash": generic_request.visual_source_decision_hash,
            "native_overlay_plan_hash": generic_request.native_overlay_plan_hash,
            "model_id": self.settings.gemini_image_model_id,
            "prompt": compiled_prompt.prompt,
            "prompt_hash": compiled_prompt.prompt_hash,
            "image_size": generic_request.requested_image_size,
            "aspect_ratio": generic_request.aspect_ratio,
            "output_count": self.settings.gemini_image_max_outputs,
            "four_k_approval_ref": generic_request.four_k_approval_ref,
            "reference_images": list(generic_request.reference_assets),
            "reference_types": [item.reference_role for item in generic_request.reference_assets],
            "reference_asset_hashes": list(generic_request.reference_asset_hashes),
            "negative_constraints": list(compiled_prompt.negative_constraints),
            "grounding_enabled": False,
            "search_grounding_enabled": False,
            "grounding_approval_ref": None,
            "text_safe_regions": list(generic_request.text_safe_regions),
            "native_overlay_required": generic_request.native_overlay_required,
            "scene_truth_classification": generic_request.scene_truth_classification,
            "evidence_truth_requirement": generic_request.evidence_truth_requirement,
            "product_specificity": generic_request.product_specificity,
            "exact_text_required": generic_request.exact_text_required,
            "exact_number_required": generic_request.exact_number_required,
            "provider_route": self.provider_key,
            "cost_ref": generic_request.cost_estimate_ref,
            "approval_ref": generic_request.approval_ref,
            "approval_scope": generic_request.approval_scope,
            "idempotency_key": generic_request.idempotency_key,
        }
        return GeminiImageGenerationRequest(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    def submit_generation(
        self,
        request: GeminiImageGenerationRequest,
        *,
        gates: GeminiImageExecutionGates,
        fixture_only: bool = False,
        preflight: Any | None = None,
        preflight_path: Path | None = None,
        execution_gates_path: Path | None = None,
        attempt_store: GeminiImagePaidAttemptStore | None = None,
        workspace_root: Path | None = None,
        destination_path: Path | None = None,
    ) -> GeminiImageOperationReceipt:
        if request.content_hash != ai_image_stable_hash(
            request.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("GEMINI_IMAGE_REQUEST_HASH_MISMATCH")
        if gates.evidence_hash != ai_image_stable_hash(
            gates.model_dump(mode="json", exclude={"evidence_hash"})
        ):
            raise ValueError("GEMINI_IMAGE_GATE_EVIDENCE_HASH_MISMATCH")
        fingerprint = self.idempotency_fingerprint(request)
        if gates.request_fingerprint != fingerprint:
            raise ValueError("GEMINI_IMAGE_GATE_REQUEST_FINGERPRINT_MISMATCH")
        if (
            gates.provider_cost_estimate_gate_passed
            and gates.provider_cost_estimate_gate_ref != request.cost_ref
        ):
            raise ValueError("GEMINI_IMAGE_COST_GATE_REQUEST_BINDING_MISMATCH")
        if (
            gates.paid_call_authorization_gate_passed
            and gates.paid_call_authorization_gate_ref != request.approval_ref
        ):
            raise ValueError("GEMINI_IMAGE_APPROVAL_GATE_REQUEST_BINDING_MISMATCH")
        if (
            gates.provider_idempotency_key_valid
            and gates.provider_idempotency_key_ref != request.idempotency_key
        ):
            raise ValueError("GEMINI_IMAGE_IDEMPOTENCY_GATE_REQUEST_BINDING_MISMATCH")
        with self._state_lock:
            existing = self._operations_by_fingerprint.get(fingerprint)
        if existing and (
            existing.generation_attempts_consumed == 1
            or existing.normalized_status in {"APPROVED", "SUBMITTED", "SUCCEEDED"}
        ):
            return existing
        if fixture_only:
            if not self.settings.img1_fixture_only or self.fixture_client is None:
                raise ValueError("GEMINI_IMAGE_FIXTURE_CLIENT_REQUIRED")
            if not gates.fixture_planning_passed:
                return self._receipt(
                    request,
                    normalized_status="PLANNED",
                    provider_status="FIXTURE_PLANNING_GATE_BLOCKED",
                )
            response = self.fixture_client.submit(request)
            receipt, transient = self.parse_response(request, response)
            with self._state_lock:
                existing = self._operations_by_fingerprint.get(fingerprint)
                if existing and existing.normalized_status == "SUCCEEDED":
                    return existing
                self._operations_by_fingerprint[fingerprint] = receipt
                self._transient_by_job[receipt.internal_job_id] = transient
            return receipt

        if not gates.all_passed:
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="GATE_BLOCKED",
            )

        readiness = self.validate_configuration()
        if not (
            self.settings.gemini_image_real_generation_enabled
            and not self.settings.img1_fixture_only
            and self.settings.provider_real_execution_enabled
            and self.settings.provider_production_execution_enabled
            and not self.settings.media_provider_calls_disabled
            and gates.approved_production_execution_scope
            and readiness.provider_route_registered
            and readiness.credential_configured
            and readiness.model_catalog_present
            and readiness.route_approval_state
        ):
            receipt = self._receipt(
                request,
                normalized_status="APPROVED",
                provider_status="EXECUTION_DISABLED",
            )
            with self._state_lock:
                self._operations_by_fingerprint[fingerprint] = receipt
            return receipt

        if request.reference_images:
            raise ValueError("GEMINI_IMAGE_REAL_REFERENCE_INPUT_NOT_AUTHORIZED")
        if request.grounding_enabled or request.search_grounding_enabled:
            raise ValueError("GEMINI_IMAGE_REAL_GROUNDING_NOT_AUTHORIZED")

        if not self._real_preflight_is_bound(
            request=request,
            gates=gates,
            preflight=preflight,
            preflight_path=preflight_path,
            execution_gates_path=execution_gates_path,
            workspace_root=workspace_root,
        ):
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="PREFLIGHT_BLOCKED",
                provider_error_code="GEMINI_IMAGE_PERSISTED_PASS_PREFLIGHT_REQUIRED",
            )
        if attempt_store is None:
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="DURABLE_ATTEMPT_LEDGER_BLOCKED",
                provider_error_code="GEMINI_IMAGE_DURABLE_ATTEMPT_LEDGER_REQUIRED",
            )
        if workspace_root is None or destination_path is None:
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="OUTPUT_DESTINATION_BLOCKED",
                provider_error_code="GEMINI_IMAGE_PREBOUND_OUTPUT_DESTINATION_REQUIRED",
            )
        resolved_root = Path(workspace_root).expanduser().resolve()
        resolved_destination = Path(destination_path).expanduser().resolve()
        try:
            self._require_workspace_child(resolved_root, resolved_destination)
        except ValueError:
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="OUTPUT_DESTINATION_BLOCKED",
                provider_error_code="GEMINI_IMAGE_OUTPUT_DESTINATION_INVALID",
            )
        if (
            resolved_destination.exists()
            or resolved_destination.with_name(resolved_destination.name + ".part").exists()
        ):
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="OUTPUT_DESTINATION_BLOCKED",
                provider_error_code="GEMINI_IMAGE_OUTPUT_DESTINATION_NOT_EMPTY",
            )
        if not self._canonical_runtime_authorities_are_bound(
            request=request,
            preflight=preflight,
            attempt_store=attempt_store,
            workspace_root=resolved_root,
            destination_path=resolved_destination,
        ):
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="PREFLIGHT_BLOCKED",
                provider_error_code=(
                    "GEMINI_IMAGE_CANONICAL_RUNTIME_AUTHORITY_REQUIRED"
                ),
            )
        canonical_attempt_store = self._open_canonical_attempt_store(
            workspace_root=resolved_root,
        )
        if canonical_attempt_store is None:
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="DURABLE_ATTEMPT_LEDGER_BLOCKED",
                provider_error_code=(
                    "GEMINI_IMAGE_CANONICAL_ATTEMPT_LEDGER_UNAVAILABLE"
                ),
            )
        # Never trust caller-supplied ledger methods. The caller object is used
        # only to prove it points at the canonical path; all state transitions
        # are reopened through the repository's concrete flocked store.
        attempt_store = canonical_attempt_store
        try:
            planned_ledger = attempt_store.load()
        except Exception:
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="DURABLE_ATTEMPT_LEDGER_BLOCKED",
                provider_error_code="GEMINI_IMAGE_DURABLE_ATTEMPT_LEDGER_UNREADABLE",
            )
        if not self._planned_attempt_is_bound(
            ledger=planned_ledger,
            request=request,
            fingerprint=fingerprint,
            preflight=preflight,
        ):
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="DURABLE_ATTEMPT_LEDGER_BLOCKED",
                provider_error_code="GEMINI_IMAGE_DURABLE_ATTEMPT_LEDGER_NOT_AVAILABLE",
            )

        # The response contract is JPEG. Prove the bounded native decoder is
        # executable and advertises MJPEG/JPEG decode before consuming the one
        # paid attempt; a missing post-submit decoder is not recoverable by
        # regeneration under the one-shot policy.
        if not self.raster_decoder_ready():
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="OUTPUT_DECODER_BLOCKED",
                provider_error_code="GEMINI_IMAGE_JPEG_SAFE_DECODER_UNAVAILABLE",
            )

        strict_contract_version = request.strict_img_canary_contract_version
        if strict_contract_version is not None:
            try:
                serialization = self.capture_official_sdk_serialization(request)
            except Exception:
                return self._receipt(
                    request,
                    normalized_status="PLANNED",
                    provider_status="SERIALIZED_REQUEST_BLOCKED",
                    provider_error_code=(
                        f"GEMINI_IMAGE_{strict_contract_version}_"
                        "SERIALIZED_REQUEST_RECHECK_FAILED"
                    ),
                )
            if (
                serialization.get("body")
                != self.expected_serialized_request_body(request)
                or serialization.get("body_sha256")
                != preflight.evidence_refs.get("serialized_request_body")
                or serialization.get("sdk_retries_disabled") is not True
            ):
                return self._receipt(
                    request,
                    normalized_status="PLANNED",
                    provider_status="SERIALIZED_REQUEST_BLOCKED",
                    provider_error_code=(
                        f"GEMINI_IMAGE_{strict_contract_version}_"
                        "SERIALIZED_REQUEST_DRIFT"
                    ),
                )

        client = self.real_client or self._build_official_real_client()
        interactions = client.interactions
        submitted_at = datetime.now(UTC)
        if strict_contract_version is not None:
            try:
                consumed_task = self._consume_strict_task_authority_at_submit(
                    request=request,
                    preflight=preflight,
                    workspace_root=resolved_root,
                    fingerprint=fingerprint,
                    now=submitted_at,
                )
            except (IMGCanarySecurityAuthorityError, ValueError, OSError):
                return self._receipt(
                    request,
                    normalized_status="PLANNED",
                    provider_status="TASK_AUTHORIZATION_BLOCKED",
                    provider_error_code=(
                        f"GEMINI_IMAGE_{strict_contract_version}_"
                        "TASK_AUTHORIZATION_NOT_CONSUMED"
                    ),
                )
            if not (
                consumed_task.status == "CONSUMED"
                and consumed_task.completion_status == "PROVIDER_ATTEMPT_SUBMITTED"
                and consumed_task.claimed_run_id == preflight.run_id
                and consumed_task.claimed_request_fingerprint == fingerprint
            ):
                raise RuntimeError(
                    f"GEMINI_IMAGE_{strict_contract_version}_TASK_SUBMIT_CLAIM_INVALID"
                )
        try:
            consumed_ledger = attempt_store.consume_at_submit(
                expected_fingerprint=fingerprint,
                now=submitted_at,
            )
        except (PermissionError, FileExistsError, ValueError):
            return self._receipt(
                request,
                normalized_status="PLANNED",
                provider_status="DURABLE_ATTEMPT_ALREADY_CONSUMED",
                provider_error_code="GEMINI_IMAGE_DURABLE_ATTEMPT_NOT_CLAIMED",
            )
        if not self._consumed_attempt_is_bound(
            ledger=consumed_ledger,
            request=request,
            fingerprint=fingerprint,
            preflight=preflight,
        ):
            raise RuntimeError("GEMINI_IMAGE_DURABLE_ATTEMPT_CLAIM_INVALID")
        submitted = self._receipt(
            request,
            normalized_status="SUBMITTED",
            provider_status="NATIVE_SUBMIT_STARTED",
            submitted_at=submitted_at,
            provider_call_made=True,
            generation_attempts_consumed=1,
        )
        # The claim is made before invoking create(). A concurrent duplicate sees
        # SUBMITTED and cannot issue another paid request through this adapter.
        with self._state_lock:
            existing = self._operations_by_fingerprint.get(fingerprint)
            if existing and (
                existing.generation_attempts_consumed == 1
                or existing.normalized_status in {"APPROVED", "SUBMITTED", "SUCCEEDED"}
            ):
                return existing
            self._operations_by_fingerprint[fingerprint] = submitted

        try:
            response = interactions.create(**self.interaction_create_kwargs(request))
        except Exception as exc:  # The provider attempt is consumed fail-closed.
            provider_error_code = self._provider_exception_code(exc)
            provider_error_diagnostic = (
                self._safe_provider_error_diagnostic(exc)
                if request.uses_img_canary_v3_response_contract
                else None
            )
            receipt = self._real_failure_receipt(
                request,
                submitted_at=submitted_at,
                provider_status="NATIVE_SUBMIT_FAILED",
                provider_error_code=provider_error_code,
            )
            summary = self._safe_response_summary(
                request,
                normalized_status="FAILED",
                provider_status=receipt.provider_status,
                provider_request_id=None,
                output_count=0,
                output_mime_type=None,
                usage={},
                provider_error_diagnostic=provider_error_diagnostic,
            )
            self._finalize_attempt_store(
                attempt_store,
                receipt=receipt,
                succeeded=False,
            )
            self._store_real_result(fingerprint, receipt, summary=summary)
            return receipt

        try:
            receipt, transient, summary = self._parse_real_response(
                request,
                response,
                submitted_at=submitted_at,
            )
        except GeminiImageResponseSafetyError as exc:
            provider_request_id = self._safe_provider_identifier(
                self._value(response, "id")
            )
            provider_status = self._safe_interaction_status(
                self._value(response, "status")
            )
            receipt = self._real_failure_receipt(
                request,
                submitted_at=submitted_at,
                provider_status=f"INTERACTION_{provider_status}",
                provider_error_code=exc.code,
                provider_request_id=provider_request_id,
                normalized_status=exc.normalized_status,
            )
            summary = self._safe_response_summary(
                request,
                normalized_status=receipt.normalized_status,
                provider_status=receipt.provider_status,
                provider_request_id=provider_request_id,
                output_count=0,
                output_mime_type=None,
                usage=self._safe_usage(self._value(response, "usage")),
            )
            self._finalize_attempt_store(
                attempt_store,
                receipt=receipt,
                succeeded=False,
            )
            self._store_real_result(fingerprint, receipt, summary=summary)
            return receipt
        except Exception as exc:
            receipt = self._real_failure_receipt(
                request,
                submitted_at=submitted_at,
                provider_status="RESPONSE_NORMALIZATION_FAILED",
                provider_error_code=self._provider_exception_code(exc),
            )
            summary = self._safe_response_summary(
                request,
                normalized_status="FAILED",
                provider_status=receipt.provider_status,
                provider_request_id=None,
                output_count=0,
                output_mime_type=None,
                usage={},
            )
            self._finalize_attempt_store(
                attempt_store,
                receipt=receipt,
                succeeded=False,
            )
            self._store_real_result(fingerprint, receipt, summary=summary)
            return receipt

        try:
            plan = self.build_output_download_plan(
                receipt,
                workspace_root=resolved_root,
                destination_path=resolved_destination,
            )
            materialization = self.materialize_output(plan, transient=transient)
        except Exception as exc:
            failed = self._real_failure_receipt(
                request,
                submitted_at=submitted_at,
                provider_status="OUTPUT_MATERIALIZATION_FAILED",
                provider_error_code=self._provider_exception_code(exc),
                provider_request_id=receipt.provider_request_id,
                normalized_status="OUTPUT_MISSING",
            )
            failed_summary = {
                **summary,
                "normalized_status": "OUTPUT_MISSING",
                "provider_status": "OUTPUT_MATERIALIZATION_FAILED",
            }
            failed_summary = {
                **{key: value for key, value in failed_summary.items() if key != "summary_hash"},
                "summary_hash": ai_image_stable_hash(
                    {key: value for key, value in failed_summary.items() if key != "summary_hash"}
                ),
            }
            self._store_real_result(
                fingerprint,
                failed,
                transient=transient,
                summary=failed_summary,
            )
            self._finalize_attempt_store(
                attempt_store,
                receipt=failed,
                succeeded=False,
            )
            return failed

        self._store_real_result(
            fingerprint,
            receipt,
            summary=summary,
        )
        with self._state_lock:
            self._materialization_receipts_by_job[receipt.internal_job_id] = materialization
        self._finalize_attempt_store(
            attempt_store,
            receipt=receipt,
            succeeded=True,
        )
        return receipt

    def materialization_receipt_for(
        self,
        receipt: GeminiImageOperationReceipt,
    ) -> dict[str, Any]:
        with self._state_lock:
            materialization = self._materialization_receipts_by_job.get(
                receipt.internal_job_id
            )
            if materialization is None:
                raise ValueError("GEMINI_IMAGE_MATERIALIZATION_RECEIPT_NOT_AVAILABLE")
            return copy.deepcopy(materialization)

    @staticmethod
    def _real_preflight_is_bound(
        *,
        request: GeminiImageGenerationRequest,
        gates: GeminiImageExecutionGates,
        preflight: Any | None,
        preflight_path: Path | None,
        execution_gates_path: Path | None,
        workspace_root: Path | None,
    ) -> bool:
        if preflight is None or getattr(preflight, "status", None) != "PASS":
            return False
        if not isinstance(preflight, IMGCanaryPreflightEvidence):
            return False
        preflight_hash = getattr(preflight, "content_hash", None)
        if preflight_hash != ai_image_stable_hash(preflight.content_hash_payload()):
            return False
        approval_expires_at = getattr(preflight, "approval_expires_at", None)
        if (
            not isinstance(approval_expires_at, datetime)
            or approval_expires_at.tzinfo is None
            or approval_expires_at <= datetime.now(UTC)
        ):
            return False
        if list(getattr(preflight, "blocker_reason_codes", []) or []):
            return False
        expected_decoder_ref = IMGCanaryPreflightEvidence.raster_decoder_evidence_hash(
            ready=True
        )
        if (
            preflight.raster_decoder_ready is not True
            or preflight.evidence_refs.get("raster_decoder_readiness")
            != expected_decoder_ref
        ):
            return False
        run_id = getattr(preflight, "run_id", None)
        if not isinstance(run_id, str) or request.approval_scope != f"IMG_CANARY_ONE_SHOT:{run_id}":
            return False
        is_v2 = request.uses_img_canary_v2_response_contract
        is_v3 = request.uses_img_canary_v3_response_contract
        if (
            is_v2 != run_id.startswith("img-canary-v2-")
            or is_v3 != run_id.startswith("img-canary-v3-")
        ):
            return False
        if workspace_root is None or preflight_path is None or execution_gates_path is None:
            return False
        serialized_evidence: IMGCanarySerializedRequestEvidence | None = None
        approval_binding: IMGCanaryV2ApprovalBinding | None = None
        previous_run_evidence: IMGCanaryPreviousRunImmutabilityEvidence | None = None
        drive_readiness: IMGCanaryDriveReadinessEvidence | None = None
        v3_serialized_evidence: IMGCanaryV3SerializedRequestEvidence | None = None
        v3_approval_binding: IMGCanaryV3ApprovalBinding | None = None
        v3_previous_runs_evidence: IMGCanaryPreviousRunsImmutabilityEvidence | None = None
        v3_drive_readiness: IMGCanaryDriveReadinessEvidence | None = None
        try:
            root = Path(workspace_root).expanduser().resolve(strict=True)
            runtime_preflight_path = Path(preflight_path).expanduser().resolve(strict=True)
            runtime_gates_path = Path(execution_gates_path).expanduser().resolve(strict=True)
            expected_preflight_path = (
                root / "manifests" / "preflight-runtime-submit.json"
            ).resolve(strict=True)
            expected_gates_path = (
                root / "manifests" / "execution-gates-runtime-submit.json"
            ).resolve(strict=True)
            if (
                runtime_preflight_path != expected_preflight_path
                or runtime_gates_path != expected_gates_path
                or Path(preflight_path).is_symlink()
                or Path(execution_gates_path).is_symlink()
            ):
                return False
            persisted_preflight = IMGCanaryPreflightEvidence.model_validate_json(
                runtime_preflight_path.read_text(encoding="utf-8")
            )
            persisted_gates = GeminiImageExecutionGates.model_validate_json(
                runtime_gates_path.read_text(encoding="utf-8")
            )
            planning_path = root / "manifests" / "preflight.json"
            if planning_path.is_symlink():
                return False
            planning_preflight = IMGCanaryPreflightEvidence.model_validate_json(
                planning_path.read_text(encoding="utf-8")
            )
            if is_v2:
                v2_paths = {
                    "serialized": root / "manifests" / "serialized-request-evidence.json",
                    "approval": root / "manifests" / "operator-approval-v2-binding.json",
                    "previous": root / "manifests" / "previous-run-immutability.json",
                    "drive": root / "manifests" / "drive-readiness.json",
                }
                if any(path.is_symlink() for path in v2_paths.values()):
                    return False
                serialized_evidence = (
                    IMGCanarySerializedRequestEvidence.model_validate_json(
                        v2_paths["serialized"].read_text(encoding="utf-8")
                    )
                )
                approval_binding = IMGCanaryV2ApprovalBinding.model_validate_json(
                    v2_paths["approval"].read_text(encoding="utf-8")
                )
                previous_run_evidence = (
                    IMGCanaryPreviousRunImmutabilityEvidence.model_validate_json(
                        v2_paths["previous"].read_text(encoding="utf-8")
                    )
                )
                drive_readiness = IMGCanaryDriveReadinessEvidence.model_validate_json(
                    v2_paths["drive"].read_text(encoding="utf-8")
                )
            elif is_v3:
                v3_paths = {
                    "serialized": root / "manifests" / "serialized-request-evidence.json",
                    "approval": root / "manifests" / "operator-approval-v3-binding.json",
                    "previous": root / "manifests" / "previous-runs-immutability.json",
                    "drive": root / "manifests" / "drive-readiness.json",
                }
                if any(path.is_symlink() for path in v3_paths.values()):
                    return False
                v3_serialized_evidence = (
                    IMGCanaryV3SerializedRequestEvidence.model_validate_json(
                        v3_paths["serialized"].read_text(encoding="utf-8")
                    )
                )
                v3_approval_binding = IMGCanaryV3ApprovalBinding.model_validate_json(
                    v3_paths["approval"].read_text(encoding="utf-8")
                )
                v3_previous_runs_evidence = (
                    IMGCanaryPreviousRunsImmutabilityEvidence.model_validate_json(
                        v3_paths["previous"].read_text(encoding="utf-8")
                    )
                )
                v3_drive_readiness = IMGCanaryDriveReadinessEvidence.model_validate_json(
                    v3_paths["drive"].read_text(encoding="utf-8")
                )
        except Exception:
            return False
        if (
            persisted_preflight.model_dump(mode="json")
            != preflight.model_dump(mode="json")
            or persisted_gates.model_dump(mode="json")
            != gates.model_dump(mode="json")
            or planning_preflight.status != "PASS"
            or planning_preflight.run_id != run_id
            or planning_preflight.task_authorization_evidence.status != "AVAILABLE"
            or planning_preflight.monthly_budget_evidence.status
            != "AVAILABLE_UNRESERVED"
            or planning_preflight.raster_decoder_ready is not True
            or planning_preflight.evidence_refs.get("raster_decoder_readiness")
            != expected_decoder_ref
            or getattr(preflight, "evidence_refs", {}).get("planning_preflight")
            != planning_preflight.content_hash
        ):
            return False
        evidence_refs = getattr(preflight, "evidence_refs", None)
        if not isinstance(evidence_refs, dict) or evidence_refs.get("provider_request") != request.content_hash:
            return False
        fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(request)
        task_evidence = getattr(preflight, "task_authorization_evidence", None)
        budget_evidence = getattr(preflight, "monthly_budget_evidence", None)
        if is_v2:
            v2_ref_names = (
                "serialized_request_evidence",
                "serialized_request_body",
                "v2_approval_binding",
                "previous_run_immutability",
                "drive_readiness",
            )
            if not (
                preflight.serialized_request_contract_passed is True
                and preflight.v2_approval_binding_passed is True
                and preflight.drive_readiness_passed is True
                and planning_preflight.serialized_request_contract_passed is True
                and planning_preflight.v2_approval_binding_passed is True
                and planning_preflight.drive_readiness_passed is True
                and all(
                    isinstance(evidence_refs.get(name), str)
                    and re.fullmatch(r"[0-9a-f]{64}", evidence_refs[name])
                    for name in v2_ref_names
                )
                and all(
                    planning_preflight.evidence_refs.get(name)
                    == evidence_refs.get(name)
                    for name in v2_ref_names
                )
                and serialized_evidence is not None
                and approval_binding is not None
                and previous_run_evidence is not None
                and drive_readiness is not None
                and serialized_evidence.run_id == run_id
                and serialized_evidence.request_hash == request.content_hash
                and serialized_evidence.prompt_hash == request.prompt_hash
                and serialized_evidence.content_hash
                == evidence_refs["serialized_request_evidence"]
                and serialized_evidence.serialized_body_hash
                == evidence_refs["serialized_request_body"]
                and approval_binding.run_id == run_id
                and approval_binding.request_hash == request.content_hash
                and approval_binding.prompt_hash == request.prompt_hash
                and approval_binding.serialized_request_evidence_hash
                == serialized_evidence.content_hash
                and approval_binding.serialized_body_hash
                == serialized_evidence.serialized_body_hash
                and approval_binding.content_hash == evidence_refs["v2_approval_binding"]
                and approval_binding.previous_run_evidence_hash
                == previous_run_evidence.evidence_hash
                and previous_run_evidence.evidence_hash
                == evidence_refs["previous_run_immutability"]
                and drive_readiness.run_id == run_id
                and drive_readiness.status == "PASS"
                and drive_readiness.content_hash == evidence_refs["drive_readiness"]
                and getattr(task_evidence, "approval_version", None) == "V2"
                and getattr(task_evidence, "approved_run_id", None) == run_id
                and getattr(task_evidence, "approved_request_fingerprint", None)
                == fingerprint
                and getattr(task_evidence, "approved_prompt_hash", None)
                == request.prompt_hash
                and getattr(task_evidence, "approved_serialized_body_hash", None)
                == serialized_evidence.serialized_body_hash
                and getattr(task_evidence, "approved_scoped_approval_hash", None)
                == approval_binding.content_hash
            ):
                return False
        elif is_v3:
            v3_ref_names = (
                "serialized_request_evidence",
                "serialized_request_body",
                "v3_approval_binding",
                "previous_runs_immutability",
                "drive_readiness",
            )
            if (
                v3_serialized_evidence is None
                or v3_approval_binding is None
                or v3_previous_runs_evidence is None
                or v3_drive_readiness is None
            ):
                return False
            serialized_body = v3_serialized_evidence.redacted_request_body
            if not (
                getattr(preflight, "serialized_request_contract_passed", None)
                is True
                and getattr(preflight, "v3_approval_binding_passed", None) is True
                and getattr(preflight, "drive_readiness_passed", None) is True
                and getattr(
                    planning_preflight,
                    "serialized_request_contract_passed",
                    None,
                )
                is True
                and getattr(planning_preflight, "v3_approval_binding_passed", None)
                is True
                and getattr(planning_preflight, "drive_readiness_passed", None)
                is True
                and all(
                    isinstance(evidence_refs.get(name), str)
                    and re.fullmatch(r"[0-9a-f]{64}", evidence_refs[name])
                    for name in v3_ref_names
                )
                and all(
                    planning_preflight.evidence_refs.get(name)
                    == evidence_refs.get(name)
                    for name in v3_ref_names
                )
                and v3_serialized_evidence.run_id == run_id
                and v3_serialized_evidence.request_hash == request.content_hash
                and v3_serialized_evidence.prompt_hash == request.prompt_hash
                and v3_serialized_evidence.content_hash
                == evidence_refs["serialized_request_evidence"]
                and v3_serialized_evidence.serialized_body_hash
                == evidence_refs["serialized_request_body"]
                and isinstance(serialized_body, dict)
                and serialized_body.get("response_format")
                == GoogleGeminiImageAdapter.expected_serialized_request_body(
                    request
                )["response_format"]
                and "delivery" not in serialized_body["response_format"]
                and v3_approval_binding.run_id == run_id
                and v3_approval_binding.request_hash == request.content_hash
                and v3_approval_binding.prompt_hash == request.prompt_hash
                and v3_approval_binding.serialized_request_evidence_hash
                == v3_serialized_evidence.content_hash
                and v3_approval_binding.serialized_body_hash
                == v3_serialized_evidence.serialized_body_hash
                and v3_approval_binding.content_hash
                == evidence_refs["v3_approval_binding"]
                and v3_approval_binding.previous_runs_evidence_hash
                == v3_previous_runs_evidence.evidence_hash
                and v3_previous_runs_evidence.evidence_hash
                == evidence_refs["previous_runs_immutability"]
                and v3_drive_readiness.run_id == run_id
                and v3_drive_readiness.status == "PASS"
                and v3_drive_readiness.content_hash
                == evidence_refs["drive_readiness"]
                and GoogleGeminiImageAdapter._task_authority_metadata_is_bound(
                    request=request,
                    run_id=run_id,
                    fingerprint=fingerprint,
                    task_authority=task_evidence,
                    evidence_refs=evidence_refs,
                )
            ):
                return False
        if not (
            getattr(task_evidence, "status", None) == "CLAIMED"
            and getattr(task_evidence, "claimed_run_id", None) == run_id
            and getattr(task_evidence, "claimed_request_fingerprint", None)
            == fingerprint
            and getattr(budget_evidence, "status", None)
            in {"RESERVED", "ALREADY_RESERVED"}
            and getattr(budget_evidence, "run_id", None) == run_id
            and getattr(budget_evidence, "request_fingerprint", None) == fingerprint
            and getattr(budget_evidence, "reservation_ref", None)
            and evidence_refs.get("task_authorization")
            == getattr(task_evidence, "content_hash", None)
            and evidence_refs.get("monthly_budget")
            == getattr(budget_evidence, "content_hash", None)
        ):
            return False
        check_names = (
            "repository_identity_passed",
            "worktree_reviewed",
            "vqc1_final_passed",
            "credential_configured",
            "credential_safe_for_use",
            "route_registered",
            "model_catalog_present",
            "model_locked",
            "image_size_locked",
            "aspect_ratio_locked",
            "output_count_locked",
            "reference_images_empty",
            "grounding_disabled",
            "provider_boundary_passed",
            "cost_estimate_passed",
            "paid_authorization_passed",
            "monthly_budget_passed",
            "attempt_limit_passed",
            "idempotency_passed",
            "global_kill_switch_scoped_open",
            "provider_kill_switch_scoped_open",
            "defaults_remain_disabled",
        )
        if not all(getattr(preflight, name, False) is True for name in check_names):
            return False
        gate_bindings = (
            ("provider_boundary_gate_passed", "provider_boundary_passed"),
            ("paid_call_authorization_gate_passed", "paid_authorization_passed"),
            ("provider_cost_estimate_gate_passed", "cost_estimate_passed"),
            ("channel_monthly_budget_gate_passed", "monthly_budget_passed"),
            ("paid_attempt_limit_gate_passed", "attempt_limit_passed"),
            ("provider_idempotency_key_valid", "idempotency_passed"),
            ("global_kill_switch_open", "global_kill_switch_scoped_open"),
            ("provider_kill_switch_open", "provider_kill_switch_scoped_open"),
        )
        return bool(
            gates.approved_production_execution_scope
            and all(
                getattr(gates, gate_name) is True
                and getattr(preflight, preflight_name) is True
                for gate_name, preflight_name in gate_bindings
            )
        )

    def _canonical_runtime_authorities_are_bound(
        self,
        *,
        request: GeminiImageGenerationRequest,
        preflight: Any,
        attempt_store: GeminiImagePaidAttemptStore,
        workspace_root: Path,
        destination_path: Path,
    ) -> bool:
        """Validate live canonical authorities immediately before attempt claim."""

        run_id = getattr(preflight, "run_id", None)
        fingerprint = self.idempotency_fingerprint(request)
        strict_contract_version = request.strict_img_canary_contract_version
        try:
            root = workspace_root.resolve(strict=True)
            if (
                not isinstance(run_id, str)
                or root.name != run_id
                or root.parent.name != "img_canary"
                or root.parent.parent.name != "artifacts"
            ):
                return False
            repo_root = root.parents[2]
            if not (repo_root / ".git").exists():
                return False
            expected_root = (
                repo_root / "artifacts" / "img_canary" / run_id
            ).resolve(strict=True)
            if root != expected_root:
                return False
            expected_attempt = (
                root / "manifests" / "attempt-ledger.json"
            ).resolve(strict=True)
            attempt_path_value = getattr(attempt_store, "path", None)
            if attempt_path_value is None:
                return False
            attempt_path = Path(attempt_path_value).resolve(strict=True)
            if attempt_path != expected_attempt or Path(attempt_path_value).is_symlink():
                return False
            expected_destination = (
                root
                / "source"
                / (
                    "original-generated.jpg"
                    if strict_contract_version is not None
                    else "original-generated.raster"
                )
            ).resolve(strict=False)
            if destination_path.resolve(strict=False) != expected_destination:
                return False

            security_root = repo_root / "var" / "credentials" / "img-canary"
            expected_task_key, expected_authorization_ref, task_relative_path = (
                img_canary_task_authority_identity(run_id)
            )
            task_path = security_root / task_relative_path
            budget_evidence = preflight.monthly_budget_evidence
            budget_path = security_root / f"budget-{budget_evidence.billing_period}.json"
            credential_path = security_root / "compromised-credential.json"
            task_parent = task_path.parent
            if any(
                path.is_symlink()
                for path in (
                    security_root,
                    task_parent,
                    task_path,
                    budget_path,
                    credential_path,
                )
            ):
                return False
            live_task = IMGCanaryTaskAuthorizationLedger.model_validate_json(
                task_path.read_text(encoding="utf-8")
            )
            live_budget = IMGCanaryMonthlyBudgetAuthorityLedger.model_validate_json(
                budget_path.read_text(encoding="utf-8")
            )
            credential_value = (
                self.settings.gemini_api_key.get_secret_value().strip()
                if self.settings.gemini_api_key
                else None
            )
            live_rotation = IMGCanaryCredentialRotationAuthority(
                credential_path
            ).verify_rotation(
                current_credential=credential_value,
                rotation_ref=preflight.credential_rotation_evidence.rotation_ref,
                now=preflight.credential_rotation_evidence.checked_at,
            )
        except Exception:
            return False

        task_evidence = preflight.task_authorization_evidence
        if not (
            live_task.content_hash == task_evidence.content_hash
            and live_task.status == "CLAIMED"
            and live_task.task_key == expected_task_key
            and live_task.authorization_ref == expected_authorization_ref
            and live_task.claimed_run_id == run_id
            and live_task.claimed_request_fingerprint == fingerprint
        ):
            return False
        if not self._task_authority_metadata_is_bound(
            request=request,
            run_id=run_id,
            fingerprint=fingerprint,
            task_authority=live_task,
            evidence_refs=preflight.evidence_refs,
        ):
            return False
        if (
            live_rotation.model_dump(mode="json")
            != preflight.credential_rotation_evidence.model_dump(mode="json")
            or live_rotation.status != "PASS"
        ):
            return False
        reservation = next(
            (
                item
                for item in live_budget.reservations
                if item.reservation_ref == budget_evidence.reservation_ref
            ),
            None,
        )
        try:
            canonical_estimate = GoogleGeminiImageModelPriceCatalog().estimate(
                model_id=request.model_id,
                image_size=request.image_size,
                aspect_ratio=request.aspect_ratio,
                output_count=request.output_count,
                attempt_count=1,
                hard_cap=Decimal("0.15"),
                approval_amount=Decimal("0.15"),
            )
        except Exception:
            return False
        return bool(
            live_budget.content_hash == budget_evidence.authority_ledger_hash
            and live_budget.authority_ref == budget_evidence.authority_ref
            and live_budget.billing_period == budget_evidence.billing_period
            and budget_evidence.status in {"RESERVED", "ALREADY_RESERVED"}
            and budget_evidence.run_id == run_id
            and budget_evidence.request_fingerprint == fingerprint
            and reservation is not None
            and reservation.status == "RESERVED"
            and reservation.run_id == run_id
            and reservation.request_fingerprint == fingerprint
            and reservation.amount_usd == budget_evidence.request_estimate_usd
            and reservation.amount_usd == canonical_estimate.estimated_amount
            and live_budget.per_request_hard_cap_usd == Decimal("0.15")
        )

    @staticmethod
    def _task_authority_metadata_is_bound(
        *,
        request: GeminiImageGenerationRequest,
        run_id: str,
        fingerprint: str,
        task_authority: Any,
        evidence_refs: dict[str, str],
    ) -> bool:
        contract_version = request.strict_img_canary_contract_version
        if contract_version is None:
            return getattr(task_authority, "approval_version", None) is None
        binding_ref_name = f"{contract_version.lower()}_approval_binding"
        serialized_body_hash = evidence_refs.get("serialized_request_body")
        scoped_approval_hash = evidence_refs.get(binding_ref_name)
        return bool(
            getattr(task_authority, "approval_version", None) == contract_version
            and getattr(task_authority, "approved_run_id", None) == run_id
            and getattr(task_authority, "approved_request_fingerprint", None)
            == fingerprint
            and getattr(task_authority, "approved_prompt_hash", None)
            == request.prompt_hash
            and isinstance(serialized_body_hash, str)
            and getattr(task_authority, "approved_serialized_body_hash", None)
            == serialized_body_hash
            and isinstance(scoped_approval_hash, str)
            and getattr(task_authority, "approved_scoped_approval_hash", None)
            == scoped_approval_hash
        )

    @staticmethod
    def _consume_strict_task_authority_at_submit(
        *,
        request: GeminiImageGenerationRequest,
        preflight: IMGCanaryPreflightEvidence,
        workspace_root: Path,
        fingerprint: str,
        now: datetime,
    ) -> IMGCanaryTaskAuthorizationLedger:
        """Atomically consume the approval-specific authority at submit boundary."""

        run_id = preflight.run_id
        contract_version = request.strict_img_canary_contract_version
        if contract_version is None or not run_id.startswith(
            f"img-canary-{contract_version.lower()}-"
        ):
            raise ValueError("GEMINI_IMAGE_STRICT_TASK_AUTHORIZATION_SCOPE_INVALID")
        _, _, relative_path = img_canary_task_authority_identity(run_id)
        repo_root = workspace_root.parents[2]
        security_root = repo_root / "var" / "credentials" / "img-canary"
        task_path = security_root / relative_path
        if any(
            path.is_symlink()
            for path in (security_root, task_path.parent, task_path)
        ):
            raise ValueError("GEMINI_IMAGE_STRICT_TASK_AUTHORIZATION_PATH_INVALID")
        serialized_body_hash = preflight.evidence_refs.get(
            "serialized_request_body"
        )
        scoped_approval_hash = preflight.evidence_refs.get(
            f"{contract_version.lower()}_approval_binding"
        )
        if not (
            isinstance(serialized_body_hash, str)
            and isinstance(scoped_approval_hash, str)
        ):
            raise ValueError("GEMINI_IMAGE_STRICT_TASK_AUTHORIZATION_BINDING_MISSING")
        return IMGCanaryTaskAuthorizationStore(task_path).consume(
            run_id=run_id,
            request_fingerprint=fingerprint,
            completion_status="PROVIDER_ATTEMPT_SUBMITTED",
            now=now,
            expected_claimed_content_hash=(
                preflight.task_authorization_evidence.content_hash
            ),
            expected_serialized_body_hash=serialized_body_hash,
            expected_scoped_approval_hash=scoped_approval_hash,
        )

    @staticmethod
    def _consume_v2_task_authority_at_submit(
        *,
        request: GeminiImageGenerationRequest,
        preflight: IMGCanaryPreflightEvidence,
        workspace_root: Path,
        fingerprint: str,
        now: datetime,
    ) -> IMGCanaryTaskAuthorizationLedger:
        """Compatibility wrapper retained for the immutable V2 contract."""

        if not request.uses_img_canary_v2_response_contract:
            raise ValueError("GEMINI_IMAGE_V2_TASK_AUTHORIZATION_SCOPE_INVALID")
        return GoogleGeminiImageAdapter._consume_strict_task_authority_at_submit(
            request=request,
            preflight=preflight,
            workspace_root=workspace_root,
            fingerprint=fingerprint,
            now=now,
        )

    @staticmethod
    def _open_canonical_attempt_store(
        *,
        workspace_root: Path,
    ) -> GeminiImagePaidAttemptStore | None:
        raw_path = workspace_root / "manifests" / "attempt-ledger.json"
        try:
            if raw_path.is_symlink():
                return None
            resolved_path = raw_path.resolve(strict=True)
            module = importlib.import_module("app.services.img_canary")
            store_type = getattr(module, "IMGCanaryAttemptLedgerStore")
            store = store_type(resolved_path)
            if Path(store.path).resolve(strict=True) != resolved_path:
                return None
            return store
        except Exception:
            return None

    @staticmethod
    def _planned_attempt_is_bound(
        *,
        ledger: Any,
        request: GeminiImageGenerationRequest,
        fingerprint: str,
        preflight: Any,
    ) -> bool:
        evidence_refs = getattr(preflight, "evidence_refs", None)
        ledger_dump = getattr(ledger, "model_dump", None)
        ledger_hash = getattr(ledger, "content_hash", None)
        ledger_hash_valid = bool(
            callable(ledger_dump)
            and ledger_hash
            == ai_image_stable_hash(
                ledger_dump(mode="json", exclude={"content_hash"})
            )
        )
        return bool(
            ledger_hash_valid
            and isinstance(evidence_refs, dict)
            and evidence_refs.get("attempt_ledger_planned") == ledger_hash
            and
            getattr(ledger, "run_id", None) == getattr(preflight, "run_id", None)
            and getattr(ledger, "request_fingerprint", None) == fingerprint
            and getattr(ledger, "idempotency_key_hash", None)
            == ai_image_stable_hash(request.idempotency_key)
            and getattr(ledger, "attempt_limit", None) == 1
            and getattr(ledger, "attempts_consumed", None) == 0
            and getattr(ledger, "status", None) == "PLANNED"
            and getattr(ledger, "provider_call_made", None) is False
        )

    @staticmethod
    def _consumed_attempt_is_bound(
        *,
        ledger: Any,
        request: GeminiImageGenerationRequest,
        fingerprint: str,
        preflight: Any,
    ) -> bool:
        return bool(
            getattr(ledger, "run_id", None) == getattr(preflight, "run_id", None)
            and getattr(ledger, "request_fingerprint", None) == fingerprint
            and getattr(ledger, "idempotency_key_hash", None)
            == ai_image_stable_hash(request.idempotency_key)
            and getattr(ledger, "attempt_limit", None) == 1
            and getattr(ledger, "attempts_consumed", None) == 1
            and getattr(ledger, "status", None) == "EXECUTING"
            and getattr(ledger, "provider_call_made", None) is True
        )

    @classmethod
    def _finalize_attempt_store(
        cls,
        attempt_store: GeminiImagePaidAttemptStore,
        *,
        receipt: GeminiImageOperationReceipt,
        succeeded: bool,
    ) -> None:
        attempt_store.finalize(
            succeeded=succeeded,
            now=datetime.now(UTC),
            provider_request_id_ref=cls._durable_identifier_ref(
                receipt.provider_request_id
            ),
            provider_operation_id_ref=cls._durable_identifier_ref(
                receipt.provider_operation_id
            ),
            failure_reason_code=(
                None
                if succeeded
                else receipt.provider_error_code or "GEMINI_IMAGE_PROVIDER_OUTPUT_INVALID"
            ),
        )

    @staticmethod
    def _durable_identifier_ref(value: str | None) -> str | None:
        if not value:
            return None
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        return f"provider-id-ref://google-gemini-image/{digest}"

    @staticmethod
    def _disable_interactions_retries(interactions: Any) -> None:
        sdk_configuration = getattr(interactions, "sdk_configuration", None)
        retry_config = getattr(sdk_configuration, "retry_config", None)
        required_retry_fields = (
            "strategy",
            "retry_connection_errors",
            "max_retries",
        )
        if retry_config is None or any(
            not hasattr(retry_config, field_name)
            for field_name in required_retry_fields
        ):
            raise RuntimeError("GEMINI_IMAGE_SDK_RETRY_CONTROL_UNAVAILABLE")
        retry_config.strategy = "none"
        retry_config.retry_connection_errors = False
        retry_config.max_retries = 0
        if (
            retry_config.strategy != "none"
            or retry_config.retry_connection_errors
            or retry_config.max_retries != 0
        ):
            raise RuntimeError("GEMINI_IMAGE_SDK_RETRY_CONTROL_FAILED")

    def _build_official_real_client(self) -> GeminiImageRealClient:
        """Build the pinned official client without SDK-level regeneration retries."""

        api_key = (
            self.settings.gemini_api_key.get_secret_value().strip()
            if self.settings.gemini_api_key
            else ""
        )
        if not api_key:
            raise RuntimeError("GEMINI_IMAGE_CREDENTIAL_NOT_CONFIGURED")
        try:
            genai = importlib.import_module("google.genai")
            types = importlib.import_module("google.genai.types")
        except ImportError as exc:
            raise RuntimeError("GEMINI_IMAGE_OFFICIAL_SDK_NOT_INSTALLED") from exc

        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(GEMINI_IMAGE_REQUEST_TIMEOUT_SECONDS * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        # google-genai 2.10.0 documents attempts=1 as no retries, while its
        # generated interactions bridge currently maps it to max_retries=1.
        # Pin the resource to strategy=none and fail closed if that control is
        # unavailable after a future SDK change.
        self._disable_interactions_retries(client.interactions)
        return client

    def _parse_real_response(
        self,
        request: GeminiImageGenerationRequest,
        response: Any,
        *,
        submitted_at: datetime,
    ) -> tuple[
        GeminiImageOperationReceipt,
        GeminiImageTransientOutput,
        dict[str, Any],
    ]:
        provider_request_id = self._safe_provider_identifier(self._value(response, "id"))
        interaction_status = self._safe_interaction_status(
            self._value(response, "status")
        )
        if interaction_status != "COMPLETED":
            raise GeminiImageResponseSafetyError(
                f"GEMINI_IMAGE_INTERACTION_{interaction_status}",
                normalized_status="FAILED",
            )

        image_outputs = self._real_image_outputs(response)
        if len(image_outputs) != 1:
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_REAL_OUTPUT_COUNT_NOT_ONE"
            )
        image = image_outputs[0]
        strict_contract_version = request.strict_img_canary_contract_version
        strict_response = strict_contract_version is not None
        uri = self._value(image, "uri")
        if (strict_response and uri is not None) or (not strict_response and uri):
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_REAL_INLINE_DELIVERY_REQUIRED"
            )
        supplied_mime_type = self._value(image, "mime_type")
        if strict_response and supplied_mime_type != "image/jpeg":
            raise GeminiImageResponseSafetyError(
                f"GEMINI_IMAGE_{strict_contract_version}_INLINE_JPEG_MIME_REQUIRED"
            )
        encoded = self._value(image, "data")
        if not isinstance(encoded, str) or not encoded:
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_REAL_INLINE_DATA_MISSING"
            )
        maximum_encoded_bytes = ((MAX_RASTER_FILE_BYTES + 2) // 3) * 4
        if len(encoded) > maximum_encoded_bytes:
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_REAL_INLINE_DATA_TOO_LARGE"
            )
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_REAL_INLINE_DATA_INVALID"
            ) from exc
        if not image_bytes or len(image_bytes) > MAX_RASTER_FILE_BYTES:
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_REAL_OUTPUT_BYTES_INVALID"
            )

        try:
            width, height, image_format = self._probe_raster_bytes(image_bytes)
        except ValueError as exc:
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_REAL_OUTPUT_RASTER_INVALID"
            ) from exc
        if strict_response and image_format != "JPEG":
            raise GeminiImageResponseSafetyError(
                f"GEMINI_IMAGE_{strict_contract_version}_INLINE_JPEG_BYTES_REQUIRED"
            )
        self._validate_safe_decode(image_bytes, image_format=image_format)
        detected_mime_type = {
            "PNG": "image/png",
            "JPEG": "image/jpeg",
        }[image_format]
        if supplied_mime_type is not None and supplied_mime_type not in {
            "image/png",
            "image/jpeg",
        }:
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_REAL_OUTPUT_MIME_UNSUPPORTED"
            )
        if supplied_mime_type and supplied_mime_type != detected_mime_type:
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_REAL_OUTPUT_MIME_MISMATCH"
            )
        if strict_contract_version is not None:
            self._validate_strict_source_geometry(
                width=width,
                height=height,
                contract_version=strict_contract_version,
            )

        sha256 = hashlib.sha256(image_bytes).hexdigest()
        output_reference = self._volatile_reference(
            f"{provider_request_id or 'interaction'}:{sha256}"
        )
        completed_at = datetime.now(UTC)
        receipt = self._receipt(
            request,
            normalized_status="SUCCEEDED",
            provider_status="INTERACTION_COMPLETED",
            provider_request_id=provider_request_id,
            provider_operation_id=provider_request_id,
            output_reference=output_reference,
            submitted_at=submitted_at,
            completed_at=completed_at,
            provider_call_made=True,
            generation_attempts_consumed=1,
        )
        transient = GeminiImageTransientOutput(
            output_reference=output_reference,
            image_bytes=image_bytes,
            raw_temporary_url=None,
            mime_type=detected_mime_type,
            transport=self.transport,
            provider_call_made=True,
        )
        summary = self._safe_response_summary(
            request,
            normalized_status=receipt.normalized_status,
            provider_status=receipt.provider_status,
            provider_request_id=provider_request_id,
            output_count=1,
            output_mime_type=detected_mime_type,
            output_size_bytes=len(image_bytes),
            output_sha256=sha256,
            image_width=width,
            image_height=height,
            image_format=image_format,
            usage=self._safe_usage(self._value(response, "usage")),
        )
        return receipt, transient, summary

    @staticmethod
    def _validate_v2_source_geometry(*, width: int, height: int) -> None:
        GoogleGeminiImageAdapter._validate_strict_source_geometry(
            width=width,
            height=height,
            contract_version="V2",
        )

    @staticmethod
    def _validate_strict_source_geometry(
        *,
        width: int,
        height: int,
        contract_version: str,
    ) -> None:
        if contract_version not in {"V2", "V3"}:
            raise ValueError("GEMINI_IMAGE_STRICT_CONTRACT_VERSION_INVALID")
        if (
            width < GEMINI_IMAGE_V2_MINIMUM_SOURCE_WIDTH
            or height < GEMINI_IMAGE_V2_MINIMUM_SOURCE_HEIGHT
        ):
            raise GeminiImageResponseSafetyError(
                f"GEMINI_IMAGE_{contract_version}_2K_SOURCE_BELOW_1080P"
            )
        aspect_error = abs(width * 9 - height * 16)
        if (
            aspect_error * 100
            > height * 16 * GEMINI_IMAGE_V2_ASPECT_RATIO_TOLERANCE_PERCENT
        ):
            raise GeminiImageResponseSafetyError(
                f"GEMINI_IMAGE_{contract_version}_SOURCE_ASPECT_RATIO_MISMATCH"
            )

    def _real_failure_receipt(
        self,
        request: GeminiImageGenerationRequest,
        *,
        submitted_at: datetime,
        provider_status: str,
        provider_error_code: str,
        provider_request_id: str | None = None,
        normalized_status: str = "FAILED",
    ) -> GeminiImageOperationReceipt:
        return self._receipt(
            request,
            normalized_status=normalized_status,
            provider_status=provider_status,
            provider_request_id=provider_request_id,
            provider_operation_id=provider_request_id,
            submitted_at=submitted_at,
            completed_at=datetime.now(UTC),
            provider_error_code=provider_error_code,
            provider_error_message_redacted=(
                "Provider response details redacted; the paid attempt is consumed."
            ),
            provider_call_made=True,
            generation_attempts_consumed=1,
        )

    def _store_real_result(
        self,
        fingerprint: str,
        receipt: GeminiImageOperationReceipt,
        *,
        summary: dict[str, Any],
        transient: GeminiImageTransientOutput | None = None,
    ) -> None:
        with self._state_lock:
            self._operations_by_fingerprint[fingerprint] = receipt
            self._response_summaries_by_job[receipt.internal_job_id] = summary
            if transient is not None:
                self._transient_by_job[receipt.internal_job_id] = transient

    def provider_response_summary_for(
        self,
        receipt: GeminiImageOperationReceipt,
    ) -> dict[str, Any]:
        with self._state_lock:
            summary = self._response_summaries_by_job.get(receipt.internal_job_id)
            if summary is None:
                raise ValueError("GEMINI_IMAGE_PROVIDER_RESPONSE_SUMMARY_NOT_AVAILABLE")
            return copy.deepcopy(summary)

    def _safe_response_summary(
        self,
        request: GeminiImageGenerationRequest,
        *,
        normalized_status: str,
        provider_status: str,
        provider_request_id: str | None,
        output_count: int,
        output_mime_type: str | None,
        usage: dict[str, int],
        output_size_bytes: int | None = None,
        output_sha256: str | None = None,
        image_width: int | None = None,
        image_height: int | None = None,
        image_format: str | None = None,
        provider_error_diagnostic: dict[str, int | str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "summary_version": "img1.gemini-image-response-summary.v1",
            "provider_key": self.provider_key,
            "transport": self.transport,
            "request_ref": request.generic_request_ref,
            "request_hash": request.content_hash,
            "provider_request_id": provider_request_id,
            "model_id": request.model_id,
            "normalized_status": normalized_status,
            "provider_status": provider_status,
            "provider_call_made": True,
            "generation_attempts_consumed": 1,
            "output_count": output_count,
            "output_mime_type": output_mime_type,
            "output_size_bytes": output_size_bytes,
            "output_sha256": output_sha256,
            "image_width": image_width,
            "image_height": image_height,
            "image_format": image_format,
            "usage": usage,
            "raw_response_persisted": False,
            "base64_image_data_persisted": False,
            "temporary_url_persisted": False,
            "authorization_headers_persisted": False,
        }
        if (
            request.uses_img_canary_v3_response_contract
            and provider_error_diagnostic
        ):
            sanitized_diagnostic = self._sanitize_provider_error_diagnostic(
                provider_error_diagnostic
            )
            if sanitized_diagnostic:
                payload["provider_error_diagnostic"] = sanitized_diagnostic
        return {**payload, "summary_hash": ai_image_stable_hash(payload)}

    @classmethod
    def _real_image_outputs(cls, response: Any) -> list[Any]:
        images: list[Any] = []
        steps = cls._value(response, "steps")
        if isinstance(steps, list):
            for step in steps:
                if cls._value(step, "type") != "model_output":
                    continue
                content = cls._value(step, "content")
                if not isinstance(content, list):
                    continue
                images.extend(
                    item for item in content if cls._value(item, "type") == "image"
                )
        output_image = cls._value(response, "output_image")
        if output_image is not None and cls._value(output_image, "type") == "image":
            if not images:
                images.append(output_image)
            elif not any(
                cls._same_image_output(existing, output_image)
                for existing in images
            ):
                # Some SDK shapes expose output_image as an alias of the single
                # model-output image. Count it only when it is a distinct payload.
                images.append(output_image)
        return images

    @classmethod
    def _same_image_output(cls, left: Any, right: Any) -> bool:
        return all(
            cls._value(left, name) == cls._value(right, name)
            for name in ("type", "data", "uri", "mime_type")
        )

    @classmethod
    def _safe_usage(cls, usage: Any) -> dict[str, int]:
        safe: dict[str, int] = {}
        for field_name in GEMINI_IMAGE_USAGE_FIELDS:
            value = cls._value(usage, field_name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe[field_name] = value
        return safe

    @staticmethod
    def _value(value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    @staticmethod
    def _safe_provider_identifier(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if (
            not candidate
            or len(candidate) > 256
            or "://" in candidate
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", candidate)
        ):
            return None
        return candidate

    @staticmethod
    def _safe_interaction_status(value: Any) -> str:
        if not isinstance(value, str):
            return "UNKNOWN"
        normalized = value.strip().lower()
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

    @classmethod
    def _safe_provider_error_diagnostic(
        cls,
        exc: Exception,
    ) -> dict[str, int | str] | None:
        """Extract only allowlisted validation metadata; never stringify errors."""

        raw_diagnostic: dict[str, int | str] = {}

        def safe_attr(name: str) -> Any:
            try:
                return getattr(exc, name, None)
            except Exception:
                return None

        for candidate in (safe_attr("status_code"), safe_attr("code")):
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                if 100 <= candidate <= 599:
                    raw_diagnostic["code"] = candidate
                    break
            elif isinstance(candidate, str) and candidate.isascii():
                stripped = candidate.strip()
                if stripped.isdigit() and 100 <= int(stripped) <= 599:
                    raw_diagnostic["code"] = int(stripped)
                    break

        for candidate in (
            safe_attr("category"),
            safe_attr("status"),
            safe_attr("reason"),
            safe_attr("code"),
        ):
            category = cls._safe_error_category(candidate)
            if category is not None:
                raw_diagnostic["category"] = category
                break

        for name in ("parameter_path", "parameter", "param", "field"):
            parameter_path = cls._safe_parameter_path(safe_attr(name))
            if parameter_path is not None:
                raw_diagnostic["parameter_path"] = parameter_path
                break

        raw_body = safe_attr("body")
        parsed_body: Any = None
        if isinstance(raw_body, str) and (
            len(raw_body) <= GEMINI_IMAGE_MAX_ERROR_DIAGNOSTIC_BODY_BYTES
        ):
            try:
                encoded_body = raw_body.encode("utf-8")
            except UnicodeEncodeError:
                encoded_body = b""
            if encoded_body and (
                len(encoded_body) <= GEMINI_IMAGE_MAX_ERROR_DIAGNOSTIC_BODY_BYTES
            ):
                try:
                    parsed_body = json.loads(encoded_body)
                except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                    parsed_body = None
        elif isinstance(raw_body, bytes) and (
            len(raw_body) <= GEMINI_IMAGE_MAX_ERROR_DIAGNOSTIC_BODY_BYTES
        ):
            try:
                parsed_body = json.loads(raw_body)
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                parsed_body = None

        queue: list[Any] = [
            safe_attr("response_json"),
            safe_attr("error_details"),
            safe_attr("details"),
            parsed_body,
            raw_body if isinstance(raw_body, (dict, list)) else None,
        ]
        seen: set[int] = set()
        while queue and len(seen) < 32:
            candidate = queue.pop(0)
            if not isinstance(candidate, (dict, list)) or id(candidate) in seen:
                continue
            seen.add(id(candidate))
            if isinstance(candidate, list):
                queue.extend(candidate[:16])
                continue
            if "code" not in raw_diagnostic:
                code = candidate.get("code")
                if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599:
                    raw_diagnostic["code"] = code
            if "category" not in raw_diagnostic:
                for key in ("status", "category", "reason", "code"):
                    category = cls._safe_error_category(candidate.get(key))
                    if category is not None:
                        raw_diagnostic["category"] = category
                        break
            if "parameter_path" not in raw_diagnostic:
                for key in ("field", "parameter", "parameter_path", "param"):
                    parameter_path = cls._safe_parameter_path(candidate.get(key))
                    if parameter_path is not None:
                        raw_diagnostic["parameter_path"] = parameter_path
                        break
            # Traverse only schema-known containers. Message, description and
            # arbitrary provider payload values are deliberately never visited.
            for key in (
                "error",
                "details",
                "fieldViolations",
                "field_violations",
                "violations",
            ):
                nested = candidate.get(key)
                if isinstance(nested, (dict, list)):
                    queue.append(nested)

        sanitized = cls._sanitize_provider_error_diagnostic(raw_diagnostic)
        return sanitized or None

    @classmethod
    def _sanitize_provider_error_diagnostic(
        cls,
        diagnostic: dict[str, int | str],
    ) -> dict[str, int | str]:
        sanitized: dict[str, int | str] = {}
        code = diagnostic.get("code")
        if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599:
            sanitized["code"] = code
        category = cls._safe_error_category(diagnostic.get("category"))
        if category is not None:
            sanitized["category"] = category
        parameter_path = cls._safe_parameter_path(diagnostic.get("parameter_path"))
        if parameter_path is not None:
            sanitized["parameter_path"] = parameter_path
        return sanitized

    @staticmethod
    def _safe_error_category(value: Any) -> str | None:
        if not isinstance(value, str) or not value.isascii() or len(value) > 64:
            return None
        normalized = value.strip().upper().replace("-", "_")
        return (
            normalized
            if normalized in GEMINI_IMAGE_SAFE_ERROR_CATEGORIES
            else None
        )

    @staticmethod
    def _safe_parameter_path(value: Any) -> str | None:
        if not isinstance(value, str) or not value.isascii() or len(value) > 160:
            return None
        candidate = value.strip()
        return candidate if candidate in GEMINI_IMAGE_SAFE_PARAMETER_PATHS else None

    @staticmethod
    def _provider_exception_code(exc: Exception) -> str:
        if isinstance(exc, GeminiImageResponseSafetyError):
            return exc.code
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int) and 100 <= status_code <= 599:
            return f"GEMINI_IMAGE_PROVIDER_HTTP_{status_code}"
        code = getattr(exc, "code", None)
        if isinstance(code, int) and code >= 0:
            return f"GEMINI_IMAGE_PROVIDER_CODE_{code}"
        class_name = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).upper()
        class_name = re.sub(r"[^A-Z0-9_]", "_", class_name)[:64]
        return f"GEMINI_IMAGE_PROVIDER_{class_name or 'ERROR'}"

    def parse_response(
        self,
        request: GeminiImageGenerationRequest,
        response: dict[str, Any],
    ) -> tuple[GeminiImageOperationReceipt, GeminiImageTransientOutput]:
        image_bytes = response.get("image_bytes")
        if not isinstance(image_bytes, bytes) or not image_bytes:
            raise ValueError("GEMINI_IMAGE_FIXTURE_OUTPUT_BYTES_MISSING")
        provider_status = str(response.get("status") or "")
        if provider_status not in {"FIXTURE_SUCCEEDED", "LOCAL_FIXTURE_SUCCEEDED"}:
            raise ValueError("GEMINI_IMAGE_FIXTURE_STATUS_NOT_SUCCESS")
        raw_url = response.get("raw_temporary_url")
        raw_url_text = str(raw_url) if raw_url else None
        volatile_seed = raw_url_text or hashlib.sha256(image_bytes).hexdigest()
        output_reference = self._volatile_reference(volatile_seed)
        now = datetime.now(UTC)
        receipt = self._receipt(
            request,
            normalized_status="SUCCEEDED",
            provider_status=provider_status,
            provider_request_id=str(response.get("request_id") or "fixture-request"),
            provider_operation_id=(
                str(response["operation_id"]) if response.get("operation_id") else None
            ),
            output_reference=output_reference,
            submitted_at=now,
            completed_at=now,
        )
        transient = GeminiImageTransientOutput(
            output_reference=output_reference,
            image_bytes=image_bytes,
            raw_temporary_url=raw_url_text,
            mime_type="image/png",
            transport="LOCAL_FIXTURE_ONLY",
            provider_call_made=False,
        )
        return receipt, transient

    def build_output_download_plan(
        self,
        receipt: GeminiImageOperationReceipt,
        *,
        workspace_root: Path,
        destination_path: Path,
    ) -> GeminiImageOutputMaterializationPlan:
        if receipt.normalized_status != "SUCCEEDED" or not receipt.output_reference:
            raise ValueError("GEMINI_IMAGE_OUTPUT_NOT_READY")
        root = Path(workspace_root).expanduser().resolve()
        destination = Path(destination_path).expanduser().resolve()
        self._require_workspace_child(root, destination)
        payload = {
            "request_ref": receipt.request_ref,
            "output_reference": receipt.output_reference,
            "workspace_root": str(root),
            "destination_path": str(destination),
            "raw_url_persisted": False,
            "execution_allowed": False,
        }
        return GeminiImageOutputMaterializationPlan(
            **payload,
            plan_hash=ai_image_stable_hash(payload),
        )

    def transient_output_for(
        self,
        receipt: GeminiImageOperationReceipt,
    ) -> GeminiImageTransientOutput:
        with self._state_lock:
            try:
                return self._transient_by_job[receipt.internal_job_id]
            except KeyError as exc:
                raise ValueError("GEMINI_IMAGE_TRANSIENT_OUTPUT_NOT_AVAILABLE") from exc

    def materialize_output(
        self,
        plan: GeminiImageOutputMaterializationPlan,
        *,
        transient: GeminiImageTransientOutput,
    ) -> dict[str, Any]:
        if plan.output_reference != transient.output_reference:
            raise ValueError("GEMINI_IMAGE_TRANSIENT_OUTPUT_REFERENCE_MISMATCH")
        root = Path(plan.workspace_root).expanduser().resolve()
        destination = Path(plan.destination_path).expanduser().resolve()
        self._require_workspace_child(root, destination)
        root.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.name + ".part")
        digest = hashlib.sha256()
        for offset in range(0, len(transient.image_bytes), 1024 * 1024):
            digest.update(transient.image_bytes[offset : offset + 1024 * 1024])
        expected_sha256 = digest.hexdigest()
        if destination.exists():
            if not destination.is_file() or self._file_sha256(destination) != expected_sha256:
                raise FileExistsError("GEMINI_IMAGE_OUTPUT_DESTINATION_ALREADY_EXISTS")
            width, height, image_format = self.probe_image(destination)
            self._validate_safe_decode(
                destination.read_bytes(),
                image_format=image_format,
            )
            self._purge_transient(transient.output_reference)
            return self._materialization_receipt(
                destination=destination,
                sha256=expected_sha256,
                width=width,
                height=height,
                image_format=image_format,
                part_path_remaining=part.exists(),
                already_materialized=True,
                transport=transient.transport,
                provider_call_made=transient.provider_call_made,
            )
        if part.exists():
            raise FileExistsError("GEMINI_IMAGE_UNOWNED_PART_FILE_EXISTS")
        created_part = False
        try:
            with part.open("xb") as stream:
                created_part = True
                for offset in range(0, len(transient.image_bytes), 1024 * 1024):
                    chunk = transient.image_bytes[offset : offset + 1024 * 1024]
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            width, height, image_format = self.probe_image(part)
            self._validate_safe_decode(part.read_bytes(), image_format=image_format)
            os.replace(part, destination)
            self._fsync_parent_directory(destination.parent)
        except Exception:
            if created_part:
                part.unlink(missing_ok=True)
            raise
        self._purge_transient(transient.output_reference)
        return self._materialization_receipt(
            destination=destination,
            sha256=expected_sha256,
            width=width,
            height=height,
            image_format=image_format,
            part_path_remaining=part.exists(),
            already_materialized=False,
            transport=transient.transport,
            provider_call_made=transient.provider_call_made,
        )

    @staticmethod
    def _materialization_receipt(
        *,
        destination: Path,
        sha256: str,
        width: int,
        height: int,
        image_format: str,
        part_path_remaining: bool,
        already_materialized: bool,
        transport: str,
        provider_call_made: bool,
    ) -> dict[str, Any]:
        return {
            "transport": transport,
            "provider_call_made": provider_call_made,
            "local_path": str(destination),
            "size_bytes": destination.stat().st_size,
            "sha256": sha256,
            "image_width": width,
            "image_height": height,
            "image_format": image_format,
            "raw_url_persisted": False,
            "part_path_remaining": part_path_remaining,
            "already_materialized": already_materialized,
        }

    @staticmethod
    def _require_workspace_child(root: Path, destination: Path) -> None:
        try:
            destination.relative_to(root)
        except ValueError as exc:
            raise ValueError("GEMINI_IMAGE_OUTPUT_PATH_ESCAPES_WORKSPACE") from exc
        if destination == root:
            raise ValueError("GEMINI_IMAGE_OUTPUT_DESTINATION_MUST_BE_FILE")

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fsync_parent_directory(directory: Path) -> None:
        """Make the preceding atomic rename durable before reporting success."""

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(directory, flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _purge_transient(self, output_reference: str) -> None:
        with self._state_lock:
            for job_id, transient in list(self._transient_by_job.items()):
                if transient.output_reference == output_reference:
                    del self._transient_by_job[job_id]

    @staticmethod
    def idempotency_fingerprint(request: GeminiImageGenerationRequest) -> str:
        return ai_image_stable_hash(
            {
                "provider_key": "google_gemini_image",
                "model_id": request.model_id,
                "prompt_hash": request.prompt_hash,
                "reference_asset_hashes": sorted(request.reference_asset_hashes),
                "image_size": request.image_size,
                "aspect_ratio": request.aspect_ratio,
                "output_count": request.output_count,
                "project_id": request.project_id,
                "scene_id": request.scene_id,
                "visual_source_decision_hash": request.visual_source_decision_hash,
                "native_overlay_plan_hash": request.native_overlay_plan_hash,
                "approval_scope": request.approval_scope,
            }
        )

    def _receipt(
        self,
        request: GeminiImageGenerationRequest,
        *,
        normalized_status: str,
        provider_status: str,
        provider_request_id: str | None = None,
        provider_operation_id: str | None = None,
        output_reference: str | None = None,
        submitted_at: datetime | None = None,
        completed_at: datetime | None = None,
        provider_error_code: str | None = None,
        provider_error_message_redacted: str | None = None,
        provider_call_made: bool = False,
        generation_attempts_consumed: int = 0,
    ) -> GeminiImageOperationReceipt:
        payload = {
            "internal_job_id": f"gemini-image-job-{request.content_hash[:16]}",
            "provider_request_id": provider_request_id,
            "provider_operation_id": provider_operation_id,
            "request_ref": request.generic_request_ref,
            "request_hash": request.content_hash,
            "idempotency_key": request.idempotency_key,
            "provider_status": provider_status,
            "normalized_status": normalized_status,
            "submitted_at": submitted_at,
            "completed_at": completed_at,
            "output_reference": output_reference,
            "provider_error_code": provider_error_code,
            "provider_error_message_redacted": provider_error_message_redacted,
            "provider_call_made": provider_call_made,
            "generation_attempts_consumed": generation_attempts_consumed,
            "actual_cost": None,
            "fallback_provider_key": None,
            "external_provider_fallback_used": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        return GeminiImageOperationReceipt(
            **payload,
            state_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _volatile_reference(value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        return f"volatile://google-gemini-image/{digest}"

    @staticmethod
    def probe_image(path: Path) -> tuple[int, int, str]:
        size = path.stat().st_size
        if size <= 0 or size > MAX_RASTER_FILE_BYTES:
            raise ValueError("GEMINI_IMAGE_RASTER_FILE_SIZE_INVALID")
        return GoogleGeminiImageAdapter._probe_raster_bytes(path.read_bytes())

    @staticmethod
    def _default_raster_decoder_path() -> str | None:
        preferred = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
        if preferred.is_file():
            return str(preferred)
        return shutil.which("ffmpeg")

    def raster_decoder_ready(self) -> bool:
        decoder = self.raster_decoder_path
        if not decoder:
            return False
        decoder_path = Path(decoder)
        if not decoder_path.is_file() or not os.access(decoder_path, os.X_OK):
            return False
        subprocess_module = importlib.import_module("subprocess")
        try:
            completed = subprocess_module.run(
                [
                    str(decoder_path),
                    "-hide_banner",
                    "-v",
                    "error",
                    "-decoders",
                ],
                stdout=subprocess_module.PIPE,
                stderr=subprocess_module.PIPE,
                timeout=GEMINI_IMAGE_DECODER_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess_module.TimeoutExpired):
            return False
        if completed.returncode != 0:
            return False
        return any(
            len(parts) >= 2 and parts[1] == b"mjpeg"
            for parts in (line.split() for line in completed.stdout.splitlines())
        )

    def _validate_safe_decode(self, data: bytes, *, image_format: str) -> None:
        # PNG is fully inflated and filter-validated by _probe_png. JPEG entropy
        # must be decoded by the existing native FFmpeg authority; structural
        # marker parsing alone is not sufficient evidence of a usable raster.
        if image_format == "PNG":
            return
        if image_format != "JPEG":
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_RASTER_FORMAT_UNSUPPORTED"
            )
        decoder = self.raster_decoder_path
        if not decoder or not Path(decoder).is_file():
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_JPEG_SAFE_DECODER_UNAVAILABLE"
            )
        try:
            subprocess_module = importlib.import_module("subprocess")
            completed = subprocess_module.run(
                [
                    decoder,
                    "-hide_banner",
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-err_detect",
                    "explode",
                    "-f",
                    "image2pipe",
                    "-i",
                    "pipe:0",
                    "-frames:v",
                    "1",
                    "-f",
                    "null",
                    "-",
                ],
                input=data,
                stdout=subprocess_module.DEVNULL,
                stderr=subprocess_module.PIPE,
                timeout=GEMINI_IMAGE_SAFE_DECODE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess_module.TimeoutExpired) as exc:
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_JPEG_SAFE_DECODE_FAILED"
            ) from exc
        if completed.returncode != 0:
            raise GeminiImageResponseSafetyError(
                "GEMINI_IMAGE_JPEG_SAFE_DECODE_FAILED"
            )

    @staticmethod
    def _probe_raster_bytes(data: bytes) -> tuple[int, int, str]:
        if not data or len(data) > MAX_RASTER_FILE_BYTES:
            raise ValueError("GEMINI_IMAGE_RASTER_FILE_SIZE_INVALID")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return (*GoogleGeminiImageAdapter._probe_png(data), "PNG")
        if data.startswith(b"\xff\xd8\xff"):
            return (*GoogleGeminiImageAdapter._probe_jpeg(data), "JPEG")
        raise ValueError("GEMINI_IMAGE_RASTER_FORMAT_UNSUPPORTED")

    @staticmethod
    def _probe_png(data: bytes) -> tuple[int, int]:
        if len(data) < 45:
            raise ValueError("GEMINI_IMAGE_PNG_TRUNCATED")
        offset = 8
        width = height = 0
        bit_depth = color_type = interlace = -1
        idat_parts: list[bytes] = []
        saw_ihdr = False
        saw_iend = False
        while offset + 12 <= len(data):
            length = int.from_bytes(data[offset : offset + 4], "big")
            chunk_type = data[offset + 4 : offset + 8]
            chunk_end = offset + 12 + length
            if length > MAX_RASTER_FILE_BYTES or chunk_end > len(data):
                raise ValueError("GEMINI_IMAGE_PNG_CHUNK_TRUNCATED")
            payload = data[offset + 8 : offset + 8 + length]
            stored_crc = int.from_bytes(data[offset + 8 + length : chunk_end], "big")
            if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != stored_crc:
                raise ValueError("GEMINI_IMAGE_PNG_CRC_INVALID")
            if not saw_ihdr:
                if chunk_type != b"IHDR" or length != 13:
                    raise ValueError("GEMINI_IMAGE_PNG_IHDR_INVALID")
                width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                    ">IIBBBBB", payload
                )
                GoogleGeminiImageAdapter._validate_dimensions(width, height)
                if compression != 0 or filtering != 0 or interlace != 0:
                    raise ValueError("GEMINI_IMAGE_PNG_ENCODING_UNSUPPORTED")
                valid_depths = {
                    0: {1, 2, 4, 8, 16},
                    2: {8, 16},
                    3: {1, 2, 4, 8},
                    4: {8, 16},
                    6: {8, 16},
                }
                if bit_depth not in valid_depths.get(color_type, set()):
                    raise ValueError("GEMINI_IMAGE_PNG_COLOR_MODE_INVALID")
                saw_ihdr = True
            elif chunk_type == b"IHDR":
                raise ValueError("GEMINI_IMAGE_PNG_DUPLICATE_IHDR")
            if chunk_type == b"IDAT":
                idat_parts.append(payload)
            if chunk_type == b"IEND":
                if length != 0:
                    raise ValueError("GEMINI_IMAGE_PNG_IEND_INVALID")
                saw_iend = True
                offset = chunk_end
                break
            offset = chunk_end
        if not saw_ihdr or not idat_parts or not saw_iend or offset != len(data):
            raise ValueError("GEMINI_IMAGE_PNG_STRUCTURE_INVALID")

        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
        row_payload_bytes = (width * channels * bit_depth + 7) // 8
        expected_decoded_bytes = (row_payload_bytes + 1) * height
        inflater = zlib.decompressobj()
        decoded = inflater.decompress(b"".join(idat_parts), expected_decoded_bytes + 1)
        if len(decoded) != expected_decoded_bytes or inflater.unconsumed_tail:
            raise ValueError("GEMINI_IMAGE_PNG_DECODE_SIZE_INVALID")
        decoded += inflater.flush(1)
        if not inflater.eof or inflater.unused_data or len(decoded) != expected_decoded_bytes:
            raise ValueError("GEMINI_IMAGE_PNG_ZLIB_STREAM_INVALID")
        row_size = row_payload_bytes + 1
        if any(decoded[index] > 4 for index in range(0, len(decoded), row_size)):
            raise ValueError("GEMINI_IMAGE_PNG_FILTER_INVALID")
        return width, height

    @staticmethod
    def _probe_jpeg(data: bytes) -> tuple[int, int]:
        if len(data) < 12 or not data.startswith(b"\xff\xd8"):
            raise ValueError("GEMINI_IMAGE_JPEG_TRUNCATED")
        if not data.endswith(b"\xff\xd9"):
            raise ValueError("GEMINI_IMAGE_JPEG_EOI_MISSING")

        offset = 2
        width = height = 0
        saw_start_of_scan = False
        start_of_frame_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        standalone_markers = {0x01, 0xD8, 0xD9, *range(0xD0, 0xD8)}

        while offset < len(data) - 2:
            if data[offset] != 0xFF:
                raise ValueError("GEMINI_IMAGE_JPEG_MARKER_INVALID")
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                raise ValueError("GEMINI_IMAGE_JPEG_MARKER_TRUNCATED")
            marker = data[offset]
            offset += 1
            if marker == 0x00:
                raise ValueError("GEMINI_IMAGE_JPEG_MARKER_INVALID")
            if marker in standalone_markers:
                if marker == 0xD9:
                    break
                continue
            if offset + 2 > len(data):
                raise ValueError("GEMINI_IMAGE_JPEG_SEGMENT_TRUNCATED")
            segment_length = int.from_bytes(data[offset : offset + 2], "big")
            if segment_length < 2:
                raise ValueError("GEMINI_IMAGE_JPEG_SEGMENT_LENGTH_INVALID")
            segment_end = offset + segment_length
            if segment_end > len(data):
                raise ValueError("GEMINI_IMAGE_JPEG_SEGMENT_TRUNCATED")
            if marker in start_of_frame_markers:
                if segment_length < 8:
                    raise ValueError("GEMINI_IMAGE_JPEG_SOF_INVALID")
                precision = data[offset + 2]
                height = int.from_bytes(data[offset + 3 : offset + 5], "big")
                width = int.from_bytes(data[offset + 5 : offset + 7], "big")
                component_count = data[offset + 7]
                if (
                    precision not in {8, 12}
                    or component_count <= 0
                    or segment_length != 8 + component_count * 3
                ):
                    raise ValueError("GEMINI_IMAGE_JPEG_SOF_INVALID")
                GoogleGeminiImageAdapter._validate_dimensions(width, height)
            if marker == 0xDA:
                saw_start_of_scan = True
                break
            offset = segment_end

        if not width or not height or not saw_start_of_scan:
            raise ValueError("GEMINI_IMAGE_JPEG_STRUCTURE_INVALID")
        return width, height

    @staticmethod
    def _validate_dimensions(width: int, height: int) -> None:
        if width <= 0 or height <= 0 or width * height > MAX_RASTER_PIXELS:
            raise ValueError("GEMINI_IMAGE_RASTER_DIMENSIONS_INVALID")


def build_fixture_png(
    width: int = 2752,
    height: int = 1536,
    *,
    rgb: tuple[int, int, int] = (31, 52, 73),
) -> bytes:
    """Build a deterministic valid RGB PNG with stdlib only."""

    if width <= 0 or height <= 0 or any(value < 0 or value > 255 for value in rgb):
        raise ValueError("IMG1_FIXTURE_PNG_ARGUMENT_INVALID")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    pixels = row * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(
        b"IDAT", zlib.compress(pixels, level=9)
    ) + chunk(b"IEND", b"")


__all__ = [
    "GeminiImageFixtureClient",
    "GeminiImageInteractionsAPI",
    "GeminiImageRealClient",
    "GeminiImageResponseSafetyError",
    "GeminiImageTransientOutput",
    "GoogleGeminiImageAdapter",
    "build_fixture_png",
]
