from __future__ import annotations

import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.providers.base import ProviderResponse


@dataclass(frozen=True)
class GoogleVertexVeoRequest:
    prompt: str
    model: str
    mode: str
    resolution: str
    duration_seconds: Decimal
    audio_enabled: bool
    output_gcs_uri: str | None = None
    aspect_ratio: str = "16:9"


@dataclass(frozen=True)
class GoogleVertexVeoExecutionConfig:
    project_id: str | None
    location: str | None
    service_account_path: str | None
    real_execution_enabled: bool
    real_smoke_enabled: bool


class GoogleVertexVeoProvider:
    provider_key = "GOOGLE_VERTEX_VEO"

    def generate_video(
        self,
        *,
        request: GoogleVertexVeoRequest,
        config: GoogleVertexVeoExecutionConfig,
    ) -> ProviderResponse:
        started = time.monotonic()
        if not config.real_execution_enabled or not config.real_smoke_enabled:
            return _error_response(
                "VEO_REAL_EXECUTION_DISABLED",
                "Veo real execution is disabled by env guard.",
                started,
                retryable=False,
            )
        missing = _missing_real_execution_config(config)
        if missing:
            return _error_response(
                "VEO_REAL_EXECUTION_CONFIG_MISSING",
                "Missing required Veo real execution env config: " + ", ".join(missing),
                started,
                retryable=False,
            )
        _ensure_google_application_credentials(config.service_account_path)
        try:
            from google import genai  # type: ignore[import-not-found]
            from google.genai import types  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            return _error_response(
                "GOOGLE_GENAI_CLIENT_MISSING",
                "Install the optional google-genai client before running real Veo smoke.",
                started,
                retryable=False,
            )
        try:
            client = genai.Client(vertexai=True, project=config.project_id, location=config.location)
            operation = client.models.generate_videos(
                model=request.model,
                prompt=request.prompt,
                config=types.GenerateVideosConfig(
                    aspect_ratio=request.aspect_ratio,
                    duration_seconds=int(request.duration_seconds),
                    generate_audio=request.audio_enabled,
                    number_of_videos=1,
                    output_gcs_uri=request.output_gcs_uri,
                    resolution=request.resolution,
                ),
            )
        except Exception as exc:
            return _error_response(
                "VEO_PROVIDER_ERROR",
                _redacted_error_message(exc),
                started,
                retryable=False,
            )
        operation_ref = _operation_ref(operation)
        return ProviderResponse(
            ok=True,
            output={
                "provider_key": self.provider_key,
                "model": request.model,
                "mode": request.mode,
                "resolution": request.resolution,
                "audio_enabled": request.audio_enabled,
                "duration_seconds": str(request.duration_seconds),
                "operation_ref": operation_ref,
                "asset_ref": operation_ref,
                "still_frame_ref": None,
                "raw_response_type": type(operation).__name__,
            },
            latency_ms=_latency_ms(started),
        )


def _missing_real_execution_config(config: GoogleVertexVeoExecutionConfig) -> list[str]:
    missing: list[str] = []
    if not config.project_id:
        missing.append("GOOGLE_CLOUD_PROJECT_ID")
    if not config.location:
        missing.append("GOOGLE_CLOUD_LOCATION")
    if not config.service_account_path:
        missing.append("GOOGLE_APPLICATION_CREDENTIALS")
    return missing


def _ensure_google_application_credentials(service_account_path: str | None) -> None:
    if service_account_path and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = service_account_path


def _operation_ref(operation: Any) -> str:
    name = getattr(operation, "name", None)
    if name:
        return f"provider://GOOGLE_VERTEX_VEO/operation/{name}"
    metadata = getattr(operation, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("name"):
        return f"provider://GOOGLE_VERTEX_VEO/operation/{metadata['name']}"
    return "provider://GOOGLE_VERTEX_VEO/operation/unknown"


def _redacted_error_message(exc: Exception) -> str:
    message = str(exc)
    redacted_fragments = ["PRIVATE KEY", "access_token", "refresh_token", "client_secret"]
    for fragment in redacted_fragments:
        message = message.replace(fragment, "[REDACTED]")
    return message[:500]


def _latency_ms(started: float) -> int:
    return max(1, int((time.monotonic() - started) * 1000))


def _error_response(error_code: str, message: str, started: float, *, retryable: bool) -> ProviderResponse:
    return ProviderResponse(
        ok=False,
        error_code=error_code,
        error_message=message,
        retryable=retryable,
        latency_ms=_latency_ms(started),
    )
