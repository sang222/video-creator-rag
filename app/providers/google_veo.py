from __future__ import annotations

import hashlib
import shutil
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
            "negative_prompt": "people, faces, dialogue, logos, text overlays",
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
        return latest

    def parse_operation(self, receipt: GoogleVeoOperationReceipt, payload: dict[str, Any]) -> GoogleVeoOperationReceipt:
        raw = str(payload.get("status") or "PROCESSING").upper()
        normalized = {
            "DONE": "SUCCEEDED",
            "SUCCEEDED": "SUCCEEDED",
            "FAILED": "FAILED",
            "MODERATED": "MODERATED",
            "CANCELLED": "CANCELLED",
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
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]

        client = genai.Client(api_key=self.settings.gemini_api_key.get_secret_value())
        operation = client.models.generate_videos(
            model=request.model_id,
            prompt=request.prompt,
            config=types.GenerateVideosConfig(
                aspect_ratio=request.aspect_ratio,
                duration_seconds=request.duration_seconds,
                generate_audio=request.generate_audio_expected,
                negative_prompt=request.negative_prompt,
                number_of_videos=request.output_count,
                resolution=request.resolution,
            ),
        )
        return str(operation.name)

    def _poll_with_official_sdk(self, provider_operation_id: str) -> dict[str, Any]:
        from google import genai  # type: ignore[import-not-found]

        client = genai.Client(api_key=self.settings.gemini_api_key.get_secret_value())
        operation = client.operations.get(provider_operation_id)
        if not operation.done:
            return {"status": "PROCESSING"}
        generated = list(getattr(operation.response, "generated_videos", None) or [])
        if not generated:
            return {"status": "OUTPUT_MISSING"}
        video = generated[0].video
        return {"status": "SUCCEEDED", "output_url": str(getattr(video, "uri", ""))}


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
