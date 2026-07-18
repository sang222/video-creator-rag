from __future__ import annotations

import hashlib
import os
import struct
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.contracts.ai_image import AIImageRequest, CompiledImagePrompt, ai_image_stable_hash
from app.contracts.google_gemini_image import (
    GeminiImageExecutionGates,
    GeminiImageGenerationRequest,
    GeminiImageOperationReceipt,
    GeminiImageOutputMaterializationPlan,
    GeminiImageReadiness,
)
from app.core.config import (
    GEMINI_IMAGE_APPROVED_MODEL_IDS,
    GEMINI_IMAGE_SUPPORTED_ASPECT_RATIOS,
    GEMINI_IMAGE_SUPPORTED_SIZES,
    Settings,
    get_settings,
)
from app.services.provider_stack import CANONICAL_PROVIDER_KEYS


MAX_RASTER_FILE_BYTES = 64 * 1024 * 1024
MAX_RASTER_PIXELS = 16_777_216


class GeminiImageFixtureClient(Protocol):
    def submit(self, request: GeminiImageGenerationRequest) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GeminiImageTransientOutput:
    """Execution-only bytes/URL; callers must never serialize this object."""

    output_reference: str
    image_bytes: bytes = field(repr=False)
    raw_temporary_url: str | None = field(default=None, repr=False)


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
    ):
        self.settings = settings or get_settings()
        self.fixture_client = fixture_client
        self._operations_by_fingerprint: dict[str, GeminiImageOperationReceipt] = {}
        self._transient_by_job: dict[str, GeminiImageTransientOutput] = {}

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
        route_registered = self.provider_key in CANONICAL_PROVIDER_KEYS
        execution_enabled = bool(self.settings.gemini_image_real_generation_enabled)
        fixture_only = bool(self.settings.img1_fixture_only)
        ready_for_future_approval = all(
            (
                route_registered,
                key_configured,
                model_configured,
                catalog_present,
                self.settings.gemini_image_provider_route_approved,
            )
        )
        return GeminiImageReadiness(
            provider_route_registered=route_registered,
            credential_configured=key_configured,
            model_configured=model_configured,
            model_catalog_present=catalog_present,
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
                else "Configure missing route, credential or model catalog evidence; do not generate content."
            ),
        )

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
    ) -> GeminiImageOperationReceipt:
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
        existing = self._operations_by_fingerprint.get(fingerprint)
        if existing and existing.normalized_status in {
            "APPROVED",
            "SUBMITTED",
            "SUCCEEDED",
        }:
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
            self._operations_by_fingerprint[fingerprint] = receipt
            return receipt
        raise PermissionError("IMG1_REAL_GEMINI_IMAGE_EXECUTION_NOT_IMPLEMENTED")

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
            self._purge_transient(transient.output_reference)
            return self._materialization_receipt(
                destination=destination,
                sha256=expected_sha256,
                width=width,
                height=height,
                image_format=image_format,
                part_path_remaining=part.exists(),
                already_materialized=True,
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
            os.replace(part, destination)
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
    ) -> dict[str, Any]:
        return {
            "transport": "LOCAL_FIXTURE_ONLY",
            "provider_call_made": False,
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

    def _purge_transient(self, output_reference: str) -> None:
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
            "provider_error_code": None,
            "provider_error_message_redacted": None,
            "provider_call_made": False,
            "generation_attempts_consumed": 0,
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
        data = path.read_bytes()
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return (*GoogleGeminiImageAdapter._probe_png(data), "PNG")
        raise ValueError("GEMINI_IMAGE_IMG1_SAFE_MATERIALIZATION_REQUIRES_PNG")

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
    "GeminiImageTransientOutput",
    "GoogleGeminiImageAdapter",
    "build_fixture_png",
]
