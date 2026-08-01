from __future__ import annotations

import json
import ssl
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

import certifi

from app.providers.base import ProviderResponse


Transport = Callable[
    [str, str, dict[str, Any] | None, dict[str, str], int], tuple[int, dict[str, Any]]
]


@dataclass(frozen=True)
class OpenAIResponsesRequest:
    """A deliberately small, auditable subset of the Responses API contract."""

    model: str
    reasoning_effort: str
    prompt: str | None = None
    messages: list[dict[str, str]] | None = None
    image_inputs: list[dict[str, str]] | None = None
    response_format: str = "text"


class OpenAIResponsesProvider:
    """OpenAI Responses adapter with no provider/model fallback behaviour."""

    provider_key = "OPENAI"

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 30,
        transport: Transport | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._urllib_transport

    def respond(self, *, request: OpenAIResponsesRequest) -> ProviderResponse:
        started = time.monotonic()
        if not self.api_key:
            return _error_response(
                "OPENAI_CREDENTIAL_MISSING",
                "OPENAI_API_KEY is not configured.",
                started,
                retryable=False,
            )
        payload = self.build_responses_payload(request=request)
        try:
            status, response_payload = self._transport(
                "POST",
                f"{self.base_url}/responses",
                payload,
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                self.timeout_seconds,
            )
        except TimeoutError as exc:
            return _error_response(
                "PROVIDER_TIMEOUT", str(exc), started, retryable=True
            )
        except OSError as exc:
            return _error_response(
                "PROVIDER_UNREACHABLE", str(exc), started, retryable=True
            )

        if status >= 400:
            code, retryable = _http_error(status, response_payload)
            return _error_response(
                code,
                _redacted_api_error(response_payload, status),
                started,
                retryable=retryable,
            )

        content = _response_output_text(response_payload)
        output: dict[str, Any] = {
            "provider_key": self.provider_key,
            "model": str(response_payload.get("model") or request.model),
            "content": content,
            "request_id": response_payload.get("id"),
            "usage": self.extract_usage(response_payload),
            "raw": response_payload,
        }
        if request.response_format == "json":
            parsed = _parse_json_content(content)
            if parsed is None:
                return _error_response(
                    "PROVIDER_INVALID_STRUCTURED_OUTPUT",
                    "OpenAI returned content that did not satisfy the JSON response contract.",
                    started,
                    retryable=False,
                )
            output["json"] = parsed
        return ProviderResponse(ok=True, output=output, latency_ms=_latency_ms(started))

    def build_responses_payload(
        self, *, request: OpenAIResponsesRequest
    ) -> dict[str, Any]:
        if request.reasoning_effort not in {"none", "low", "medium", "high"}:
            raise ValueError(
                "OpenAI reasoning_effort must be none, low, medium, or high"
            )
        payload: dict[str, Any] = {
            "model": request.model,
            "input": _request_input(request),
            "reasoning": {"effort": request.reasoning_effort},
            # VCOS persists its own redacted request/response receipt.  Do not
            # retain production content at the provider by default.
            "store": False,
        }
        if request.response_format == "json":
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "vcos_router_output",
                    "schema": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "strict": False,
                }
            }
        return payload

    def extract_usage(self, payload: dict[str, Any]) -> dict[str, int | None]:
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        input_details = (
            usage.get("input_tokens_details")
            if isinstance(usage.get("input_tokens_details"), dict)
            else {}
        )
        output_details = (
            usage.get("output_tokens_details")
            if isinstance(usage.get("output_tokens_details"), dict)
            else {}
        )
        return {
            "input_tokens": _maybe_int(usage.get("input_tokens")),
            "cached_input_tokens": _maybe_int(input_details.get("cached_tokens")),
            "output_tokens": _maybe_int(usage.get("output_tokens")),
            "reasoning_tokens": _maybe_int(output_details.get("reasoning_tokens")),
            "total_tokens": _maybe_int(usage.get("total_tokens")),
        }

    def _urllib_transport(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> tuple[int, dict[str, Any]]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib_request.Request(url, data=body, method=method, headers=headers)
        try:
            # The macOS/Python runtime can lack the system issuer bundle even
            # though the project ships certifi.  Keep TLS verification enabled
            # and provide the packaged CA bundle explicitly; never bypass it.
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            with urllib_request.urlopen(
                request, timeout=timeout_seconds, context=ssl_context
            ) as response:
                raw = response.read().decode("utf-8")
                return response.status, json.loads(raw or "{}")
        except urllib_error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw or "{}")
            except json.JSONDecodeError:
                payload = {"error": {"message": "OpenAI returned an unreadable error."}}
            return exc.code, payload
        except TimeoutError:
            raise
        except urllib_error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise reason
            raise OSError(str(reason)) from exc


def _request_input(request: OpenAIResponsesRequest) -> list[dict[str, Any]]:
    if request.messages is not None:
        messages = request.messages
    else:
        messages = [{"role": "user", "content": request.prompt or ""}]
    inputs = [
        {
            "role": str(message["role"]),
            "content": [{"type": "input_text", "text": str(message["content"])}],
        }
        for message in messages
    ]
    if not request.image_inputs:
        return inputs
    user_input = next(
        (item for item in reversed(inputs) if item["role"] == "user"), None
    )
    if user_input is None:
        user_input = {"role": "user", "content": []}
        inputs.append(user_input)
    for image in request.image_inputs:
        user_input["content"].append(_image_content_part(image))
    return inputs


def _image_content_part(image: dict[str, str]) -> dict[str, str]:
    """Permit typed still-image evidence, never raw video or audio.

    The caller must declare the media type rather than treating an arbitrary
    local/remote object as an image.  The provider receives only a data image
    URI or an HTTPS image URL; video and audio URLs are rejected before any
    network request is made.
    """

    media_type = str(image.get("media_type") or "").strip().lower()
    image_url = str(image.get("image_url") or "").strip()
    allowed_media_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if media_type not in allowed_media_types:
        raise ValueError("OPENAI_VISUAL_INPUT_MUST_BE_STILL_IMAGE")
    lowered_url = image_url.lower()
    if not image_url or any(
        marker in lowered_url
        for marker in (".mp4", ".mov", ".webm", ".m4a", ".mp3", "audio/")
    ):
        raise ValueError("OPENAI_RAW_VIDEO_OR_AUDIO_INPUT_FORBIDDEN")
    if lowered_url.startswith("data:"):
        if not lowered_url.startswith(f"data:{media_type}"):
            raise ValueError("OPENAI_VISUAL_INPUT_MEDIA_TYPE_MISMATCH")
    elif not lowered_url.startswith("https://"):
        raise ValueError("OPENAI_VISUAL_INPUT_URL_REQUIRED")
    return {"type": "input_image", "image_url": image_url}


def _response_output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct
    text_parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                text_parts.append(content["text"])
    return "\n".join(text_parts)


def _parse_json_content(content: str) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _http_error(status: int, payload: dict[str, Any]) -> tuple[str, bool]:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    error_type = str(error.get("type") or "").lower()
    if status == 429:
        return (
            "PROVIDER_QUOTA_EXCEEDED"
            if "quota" in error_type or "insufficient" in error_type
            else "PROVIDER_RATE_LIMITED",
            True,
        )
    if status in {401, 403}:
        return "OPENAI_CREDENTIAL_REJECTED", False
    if status >= 500:
        return "PROVIDER_HTTP_ERROR", True
    return "PROVIDER_HTTP_ERROR", False


def _redacted_api_error(payload: dict[str, Any], status: int) -> str:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = str(error.get("code") or error.get("type") or "unknown")
    return f"OpenAI returned HTTP {status} ({code})."


def _maybe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _latency_ms(started: float) -> int:
    return max(1, int((time.monotonic() - started) * 1000))


def _error_response(
    error_code: str, message: str, started: float, *, retryable: bool
) -> ProviderResponse:
    return ProviderResponse(
        ok=False,
        error_code=error_code,
        error_message=message,
        retryable=retryable,
        latency_ms=_latency_ms(started),
    )
