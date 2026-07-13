from __future__ import annotations

import hashlib
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.contracts.asset_acquisition import AIHeroAssetRequest
from app.contracts.google_veo import (
    AIHeroUnavailableDecision,
    GoogleVeoExecutionGates,
    GoogleVeoGenerationRequest,
    GoogleVeoOperationReceipt,
    GoogleVeoOutputDownloadPlan,
)
from app.core.config import Settings, get_settings
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.native_render_plan import stable_hash


class GoogleVeoSDKClient(Protocol):
    def submit(self, request: GoogleVeoGenerationRequest) -> dict[str, Any]: ...

    def get_operation(self, provider_operation_id: str) -> dict[str, Any]: ...


class GoogleVeoAdapter:
    provider_key = "google_veo"
    transport = "GEMINI_API_NATIVE"

    def __init__(self, settings: Settings | None = None, *, fixture_client: GoogleVeoSDKClient | None = None):
        self.settings = settings or get_settings()
        self.fixture_client = fixture_client
        self._operations_by_fingerprint: dict[str, GoogleVeoOperationReceipt] = {}
        self._sdk_operations_by_id: dict[str, Any] = {}

    def validate_configuration(self) -> dict[str, Any]:
        configured = self.settings.gemini_api_key is not None and bool(self.settings.gemini_api_key.get_secret_value().strip())
        model_catalog_ok = self.settings.veo_model_id in {item["model_id"] for item in GoogleVeoModelPriceCatalog().payload["items"]}
        return {
            "provider_key": self.provider_key,
            "transport": self.transport,
            "credential_configured": configured,
            "credential_value_redacted": True,
            "model_catalog_status": "APPROVED" if model_catalog_ok else "INVALID",
            "execution_enabled": self.settings.veo_real_generation_enabled,
            "smoke_enabled": self.settings.pa1r_veo_smoke_enabled,
            "will_execute": False,
            "provider_call_made": False,
        }

    def build_generation_request(
        self,
        generic_request: AIHeroAssetRequest,
        *,
        cost_catalog_ref: str,
        approval_ref: str,
        approval_scope: str,
        idempotency_key: str,
    ) -> GoogleVeoGenerationRequest:
        if generic_request.prompt_safety_status != "PASS" or not generic_request.human_approval_required:
            raise ValueError("VEO_GENERIC_REQUEST_NOT_APPROVED")
        payload = {
            "request_id": f"veo-{generic_request.request_id}",
            "generic_ai_hero_request_ref": generic_request.request_id,
            "generic_ai_hero_request_hash": generic_request.request_hash,
            "project_id": generic_request.project_id,
            "scene_id": generic_request.scene_id,
            "hero_reason": generic_request.hero_reason,
            "model_id": self.settings.veo_model_id,
            "prompt": generic_request.prompt_text,
            "prompt_hash": generic_request.prompt_hash,
            "duration_seconds": self.settings.veo_default_duration_seconds,
            "resolution": self.settings.veo_default_resolution,
            "aspect_ratio": generic_request.required_aspect_ratio,
            "output_count": self.settings.veo_default_output_count,
            "negative_prompt": (
                "people, person, face, human figure, presenter, speaker, human likeness, "
                "text, letters, logo, watermark, interface screenshot, fake UI, testimonial"
            ),
            "reference_image_refs": [],
            "first_frame_ref": None,
            "last_frame_ref": None,
            "character_policy_mode": generic_request.character_policy_mode,
            "human_likeness_requested": False,
            "generate_audio_expected": True,
            "provider_audio_usage_policy": "DISCARD",
            "synthetic_media_disclosure_required": True,
            "cost_catalog_ref": cost_catalog_ref,
            "approval_ref": approval_ref,
            "approval_scope": approval_scope,
            "idempotency_key": idempotency_key,
        }
        return GoogleVeoGenerationRequest(**payload, request_hash=stable_hash(payload))

    def submit_generation(
        self,
        request: GoogleVeoGenerationRequest,
        *,
        gates: GoogleVeoExecutionGates,
        fixture_only: bool = False,
    ) -> GoogleVeoOperationReceipt:
        fingerprint = self.idempotency_fingerprint(request)
        existing = self._operations_by_fingerprint.get(fingerprint)
        if existing and existing.normalized_status in {"SUBMITTED", "PROCESSING", "SUCCEEDED"}:
            return existing
        if not gates.all_passed:
            return self._receipt(request, "PLANNED", "GATE_BLOCKED", None, attempts=0, provider_call_made=False)
        if fixture_only:
            if self.fixture_client is None:
                raise ValueError("VEO_FIXTURE_CLIENT_REQUIRED")
            response = self.fixture_client.submit(request)
            receipt = self._receipt(
                request,
                "SUBMITTED",
                str(response.get("status") or "SUBMITTED"),
                str(response["operation_id"]),
                attempts=0,
                provider_call_made=False,
            )
            self._operations_by_fingerprint[fingerprint] = receipt
            return receipt
        if not (
            self.settings.veo_real_generation_enabled
            and (self.settings.pa1r_veo_smoke_enabled or gates.approved_production_execution_scope)
        ):
            return self._receipt(request, "APPROVED", "EXECUTION_DISABLED", None, attempts=0, provider_call_made=False)
        if not self.validate_configuration()["credential_configured"]:
            return self._receipt(request, "APPROVED", "CREDENTIAL_MISSING", None, attempts=0, provider_call_made=False)
        operation_id = self._submit_with_official_sdk(request)
        receipt = self._receipt(request, "SUBMITTED", "SUBMITTED", operation_id, attempts=1, provider_call_made=True)
        self._operations_by_fingerprint[fingerprint] = receipt
        return receipt

    def poll_operation(
        self,
        receipt: GoogleVeoOperationReceipt,
        *,
        max_polls: int = 3,
        fixture_only: bool = False,
        poll_interval_seconds: float = 0,
    ) -> GoogleVeoOperationReceipt:
        if not receipt.provider_operation_id:
            return receipt
        latest = receipt
        for _ in range(max_polls):
            if fixture_only:
                if self.fixture_client is None:
                    raise ValueError("VEO_FIXTURE_CLIENT_REQUIRED")
                payload = self.fixture_client.get_operation(receipt.provider_operation_id)
            else:
                if not self.settings.veo_real_generation_enabled:
                    return latest
                payload = self._poll_with_official_sdk(receipt.provider_operation_id)
            latest = self.parse_operation(latest, payload)
            if latest.normalized_status in {"SUCCEEDED", "FAILED", "MODERATED", "CANCELLED", "OUTPUT_MISSING"}:
                break
            if poll_interval_seconds > 0:
                time.sleep(poll_interval_seconds)
        return latest

    def parse_operation(self, receipt: GoogleVeoOperationReceipt, payload: dict[str, Any]) -> GoogleVeoOperationReceipt:
        raw = str(payload.get("status") or "PROCESSING").upper()
        normalized = {
            "DONE": "SUCCEEDED",
            "SUCCEEDED": "SUCCEEDED",
            "FAILED": "FAILED",
            "MODERATED": "MODERATED",
            "CANCELLED": "CANCELLED",
            "OUTPUT_MISSING": "OUTPUT_MISSING",
        }.get(raw, "PROCESSING")
        output_ref = None
        raw_output = payload.get("output_url")
        if raw_output:
            output_ref = self._volatile_reference(str(raw_output))
        now = datetime.now(UTC)
        values = receipt.model_dump(mode="python")
        values.update(
            provider_status=raw,
            normalized_status=normalized,
            last_polled_at=now,
            completed_at=now if normalized in {"SUCCEEDED", "FAILED", "MODERATED", "CANCELLED", "OUTPUT_MISSING"} else None,
            output_reference=output_ref,
            provider_error_code=str(payload.get("error_code")) if payload.get("error_code") else None,
            provider_error_message_redacted=("Provider operation failed; inspect provider console." if payload.get("error_code") else None),
        )
        values["state_hash"] = stable_hash({key: value for key, value in values.items() if key != "state_hash"})
        parsed = GoogleVeoOperationReceipt(**values)
        self._operations_by_fingerprint[self._receipt_fingerprint(parsed)] = parsed
        return parsed

    def build_output_download_plan(
        self,
        receipt: GoogleVeoOperationReceipt,
        *,
        raw_output_url: str,
        destination_path: Path,
    ) -> GoogleVeoOutputDownloadPlan:
        if receipt.normalized_status != "SUCCEEDED":
            raise ValueError("VEO_OUTPUT_NOT_READY")
        payload = {
            "operation_ref": receipt.provider_operation_id or receipt.internal_job_id,
            "volatile_output_reference": self._volatile_reference(raw_output_url),
            "destination_path": str(destination_path),
            "raw_url_persisted": False,
            "execution_allowed": False,
        }
        return GoogleVeoOutputDownloadPlan(**payload, plan_hash=stable_hash(payload))

    def download_output(self, plan: GoogleVeoOutputDownloadPlan, *, fixture_source: Path | None = None) -> dict[str, Any]:
        if fixture_source is None:
            raise RuntimeError("VEO_REAL_DOWNLOAD_REQUIRES_EXPLICIT_ACTIVATION_SCOPE")
        destination = Path(plan.destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture_source, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        return {
            "transport": "LOCAL_FIXTURE_ONLY",
            "provider_call_made": False,
            "downloaded_path": str(destination),
            "size_bytes": destination.stat().st_size,
            "sha256": digest,
        }

    def download_real_output(
        self,
        receipt: GoogleVeoOperationReceipt,
        *,
        destination_path: Path,
    ) -> dict[str, Any]:
        """Download one completed Veo output without creating another generation."""
        if receipt.normalized_status != "SUCCEEDED" or not receipt.provider_operation_id:
            raise RuntimeError("VEO_OUTPUT_NOT_READY")
        if not (self.settings.veo_real_generation_enabled and self.settings.pa1r_veo_smoke_enabled):
            raise PermissionError("VEO_REAL_DOWNLOAD_EXECUTION_DISABLED")
        operation = self._sdk_operations_by_id.get(receipt.provider_operation_id)
        client = self._official_client()
        if operation is None:
            from google.genai import types  # type: ignore[import-not-found]

            operation = types.GenerateVideosOperation(name=receipt.provider_operation_id)
        operation = client.operations.get(operation)
        self._sdk_operations_by_id[receipt.provider_operation_id] = operation
        generated = list(getattr(operation.response or operation.result, "generated_videos", None) or [])
        if not generated or not getattr(generated[0], "video", None):
            raise RuntimeError("VEO_OUTPUT_MISSING")
        content = client.files.download(file=generated[0].video)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        part = destination_path.with_name(destination_path.name + ".part")
        digest = hashlib.sha256()
        try:
            with part.open("xb") as stream:
                for offset in range(0, len(content), 1024 * 1024):
                    chunk = content[offset : offset + 1024 * 1024]
                    stream.write(chunk)
                    digest.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(part, destination_path)
        finally:
            part.unlink(missing_ok=True)
        return {
            "transport": self.transport,
            "provider_call_made": True,
            "downloaded_path": str(destination_path),
            "size_bytes": destination_path.stat().st_size,
            "sha256": digest.hexdigest(),
            "raw_url_persisted": False,
        }

    @staticmethod
    def idempotency_fingerprint(request: GoogleVeoGenerationRequest) -> str:
        return stable_hash(
            {
                "provider_key": "google_veo",
                "model_id": request.model_id,
                "prompt_hash": request.prompt_hash,
                "reference_asset_hashes": sorted(request.reference_image_refs),
                "duration": request.duration_seconds,
                "resolution": request.resolution,
                "aspect_ratio": request.aspect_ratio,
                "output_count": request.output_count,
                "project_id": request.project_id,
                "scene_id": request.scene_id,
                "approval_scope": request.approval_scope,
            }
        )

    def _receipt(
        self,
        request: GoogleVeoGenerationRequest,
        normalized_status: str,
        provider_status: str,
        operation_id: str | None,
        *,
        attempts: int,
        provider_call_made: bool,
    ) -> GoogleVeoOperationReceipt:
        now = datetime.now(UTC)
        payload = {
            "internal_job_id": f"veo-job-{request.request_hash[:16]}",
            "provider_operation_id": operation_id,
            "request_ref": request.request_id,
            "request_hash": request.request_hash,
            "idempotency_key": request.idempotency_key,
            "submit_attempt_no": attempts,
            "provider_status": provider_status,
            "normalized_status": normalized_status,
            "started_at": now if operation_id else None,
            "last_polled_at": None,
            "completed_at": None,
            "provider_error_code": None,
            "provider_error_message_redacted": None,
            "output_reference": None,
            "provider_call_made": provider_call_made,
            "generation_attempts_consumed": attempts,
            "production_eligible": False,
        }
        return GoogleVeoOperationReceipt(**payload, state_hash=stable_hash(payload))

    @staticmethod
    def _volatile_reference(raw_url: str) -> str:
        return f"volatile://google-veo-output/{hashlib.sha256(raw_url.encode()).hexdigest()[:24]}"

    def _receipt_fingerprint(self, receipt: GoogleVeoOperationReceipt) -> str:
        for fingerprint, current in self._operations_by_fingerprint.items():
            if current.request_hash == receipt.request_hash:
                return fingerprint
        return stable_hash(receipt.request_hash)

    def _submit_with_official_sdk(self, request: GoogleVeoGenerationRequest) -> str:
        client = self._official_client()
        config = self._build_sdk_config(request)
        # Gemini Developer API Veo 3.1 audio is always on. The SDK's
        # generate_audio field is an Enterprise Agent Platform-only control,
        # so setting it (even to True) makes the SDK reject the request before
        # an operation is created.
        operation = client.models.generate_videos(
            model=request.model_id,
            prompt=request.prompt,
            config=config,
        )
        operation_id = str(operation.name)
        self._sdk_operations_by_id[operation_id] = operation
        return operation_id

    @staticmethod
    def _build_sdk_config(request: GoogleVeoGenerationRequest):
        from google.genai import types  # type: ignore[import-not-found]

        return types.GenerateVideosConfig(
            aspect_ratio=request.aspect_ratio,
            duration_seconds=request.duration_seconds,
            negative_prompt=request.negative_prompt,
            number_of_videos=request.output_count,
            # Veo 3.1 text-to-video accepts allow_all only. NO_CHARACTER is
            # enforced by the approved prompt/negative prompt and output
            # review boundary, not by this transport compatibility field.
            person_generation="allow_all",
            resolution=request.resolution,
        )

    def transport_config_evidence(self, request: GoogleVeoGenerationRequest) -> dict[str, Any]:
        """Return the exact safe transport assertions used by real submit."""
        config = self._build_sdk_config(request)
        return {
            "transport": self.transport,
            "generate_audio_parameter_sent": config.generate_audio is not None,
            "generate_audio_value": config.generate_audio,
            "person_generation_sent": config.person_generation,
            "domain_character_policy": request.character_policy_mode,
            "provider_audio_usage_policy": request.provider_audio_usage_policy,
            "automatic_retry": False,
        }

    def _poll_with_official_sdk(self, provider_operation_id: str) -> dict[str, Any]:
        from google.genai import types  # type: ignore[import-not-found]

        client = self._official_client()
        operation = self._sdk_operations_by_id.get(provider_operation_id) or types.GenerateVideosOperation(name=provider_operation_id)
        operation = client.operations.get(operation)
        self._sdk_operations_by_id[provider_operation_id] = operation
        if not operation.done:
            return {"status": "PROCESSING"}
        if operation.error:
            return {"status": "FAILED", "error_code": str(operation.error.get("code") or "VEO_PROVIDER_ERROR")}
        generated = list(getattr(operation.response or operation.result, "generated_videos", None) or [])
        if not generated:
            return {"status": "OUTPUT_MISSING"}
        video = generated[0].video
        return {"status": "SUCCEEDED", "output_url": str(getattr(video, "uri", ""))}

    def _official_client(self):
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]

        if self.settings.gemini_api_key is None:
            raise RuntimeError("GEMINI_API_KEY_MISSING")
        return genai.Client(
            api_key=self.settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(
                timeout=120_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )


class GoogleVeoUnavailableRouter:
    def route(
        self,
        *,
        original_ai_hero_intent_ref: str,
        unavailable_reason: str,
        frozen_policy_behavior: str,
        cost_avoided_usd: Any,
    ) -> AIHeroUnavailableDecision:
        if frozen_policy_behavior == "NATIVE_VISUAL_OR_REVIEW":
            decision, source_role, review = "NATIVE_VISUAL_REQUIRED", "NATIVE_VISUAL", True
        elif frozen_policy_behavior == "REVIEW_REQUIRED":
            decision, source_role, review = "REVIEW_REQUIRED", "AI_HERO_UNRESOLVED", True
        else:
            decision, source_role, review = "BLOCK", "AI_HERO_UNRESOLVED", True
        payload = {
            "original_ai_hero_intent_ref": original_ai_hero_intent_ref,
            "unavailable_reason": unavailable_reason,
            "frozen_policy_behavior": frozen_policy_behavior,
            "decision": decision,
            "human_review_required": review,
            "resulting_source_role": source_role,
            "cost_avoided_usd": cost_avoided_usd,
            "external_provider_attempted": False,
            "external_provider_fallback_used": False,
        }
        return AIHeroUnavailableDecision(**payload, decision_hash=stable_hash(payload))
