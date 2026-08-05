from __future__ import annotations

import hashlib
import json
import re
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit, urlunsplit

import certifi

from app.providers.base import ProviderResponse


TransportResult = (
    tuple[int, dict[str, Any]] | tuple[int, dict[str, Any], dict[str, str]]
)
Transport = Callable[
    [str, str, dict[str, Any] | None, dict[str, str], int], TransportResult
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
    idempotency_key: str | None = None


@dataclass(frozen=True)
class OpenAIWebSearchRequest:
    """Bounded hosted-web-search request for an already-authorized workflow.

    This intentionally keeps web search separate from ``respond``: callers
    must opt into the dedicated operation, supply a bounded domain policy, and
    consume the returned URLs as discovery metadata only.
    """

    model: str
    reasoning_effort: str
    query: str
    allowed_domains: list[str]
    search_context_size: str = "low"


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
        runtime_origin: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._urllib_transport
        self.runtime_origin = _safe_runtime_origin(runtime_origin)

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
        result = self._responses_request(
            payload=payload,
            model=request.model,
            operation="responses",
            tool_type=None,
            started=started,
            idempotency_key=request.idempotency_key,
        )
        if isinstance(result, ProviderResponse):
            return result
        status, response_payload, _ = result

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

    def web_search(self, *, request: OpenAIWebSearchRequest) -> ProviderResponse:
        """Call the official hosted web-search tool without a fallback path.

        The raw provider result is deliberately retained for the evidence
        executor to extract only tool-returned URLs and citations.  Model prose
        is never promoted to source authority by this adapter.
        """

        started = time.monotonic()
        if not self.api_key:
            return _error_response(
                "OPENAI_CREDENTIAL_MISSING",
                "OPENAI_API_KEY is not configured.",
                started,
                retryable=False,
            )
        try:
            payload = self.build_web_search_payload(request=request)
        except ValueError as exc:
            return _error_response(
                "OPENAI_WEB_SEARCH_REQUEST_INVALID",
                str(exc),
                started,
                retryable=False,
            )
        result = self._responses_request(
            payload=payload,
            model=request.model,
            operation="web_search",
            tool_type="web_search",
            started=started,
        )
        if isinstance(result, ProviderResponse):
            return result
        status, response_payload, _ = result
        return ProviderResponse(
            ok=True,
            output={
                "provider_key": self.provider_key,
                "model": str(response_payload.get("model") or request.model),
                "request_id": response_payload.get("id"),
                "usage": self.extract_usage(response_payload),
                "raw": response_payload,
            },
            latency_ms=_latency_ms(started),
        )

    def _responses_request(
        self,
        *,
        payload: dict[str, Any],
        model: str,
        operation: str,
        tool_type: str | None,
        started: float,
        idempotency_key: str | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, str]] | ProviderResponse:
        """Make exactly one Responses request and retain only safe diagnostics.

        The adapter never retries here.  Callers receive a stable error class and
        an ``output["error"]`` receipt that can be durably persisted without
        storing a prompt, credential, or response body.
        """

        endpoint = f"{self.base_url}/responses"
        try:
            result = _normalize_transport_result(
                self._transport(
                    "POST",
                    endpoint,
                    payload,
                    {
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        **(
                            {"Idempotency-Key": idempotency_key}
                            if idempotency_key
                            else {}
                        ),
                    },
                    self.timeout_seconds,
                )
            )
        except (TimeoutError, OSError) as exc:
            return _error_response(
                "OPENAI_NETWORK_FAILURE",
                _redacted_network_error(exc),
                started,
                retryable=True,
                output={
                    "error": _network_error_details(
                        endpoint=endpoint,
                        operation=operation,
                        request_payload=payload,
                        model=model,
                        tool_type=tool_type,
                        runtime_origin=self.runtime_origin,
                        exc=exc,
                    )
                },
            )

        status, response_payload, response_headers = result
        if status >= 400:
            code, retryable = _http_error(status, response_payload)
            return _error_response(
                code,
                _redacted_api_error(response_payload, status),
                started,
                retryable=retryable,
                output={
                    "error": _http_error_details(
                        endpoint=endpoint,
                        operation=operation,
                        status=status,
                        response_payload=response_payload,
                        response_headers=response_headers,
                        request_payload=payload,
                        model=model,
                        tool_type=tool_type,
                        runtime_origin=self.runtime_origin,
                    )
                },
            )
        return result

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

    def build_web_search_payload(
        self, *, request: OpenAIWebSearchRequest
    ) -> dict[str, Any]:
        if request.reasoning_effort not in {"none", "low", "medium", "high"}:
            raise ValueError(
                "OpenAI reasoning_effort must be none, low, medium, or high"
            )
        if request.search_context_size not in {"low", "medium", "high"}:
            raise ValueError("OpenAI web-search context size is invalid")
        query = request.query.strip()
        if not query:
            raise ValueError("OpenAI web-search query is required")
        domains = sorted(
            {
                item.strip().lower().lstrip(".")
                for item in request.allowed_domains
                if isinstance(item, str) and item.strip()
            }
        )
        if not domains or any("/" in item or ":" in item for item in domains):
            raise ValueError("OpenAI web-search domains are invalid")
        return {
            "model": request.model,
            "input": query,
            "reasoning": {"effort": request.reasoning_effort},
            "tools": [
                {
                    "type": "web_search",
                    "search_context_size": request.search_context_size,
                    "external_web_access": True,
                    "filters": {"allowed_domains": domains},
                }
            ],
            # Discovery is mandatory for this explicit operation.  A normal
            # language response cannot silently replace a missing search call.
            "tool_choice": "required",
            "include": ["web_search_call.action.sources"],
            "store": False,
        }

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
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
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
                return (
                    response.status,
                    json.loads(raw or "{}"),
                    dict(response.headers.items()),
                )
        except urllib_error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw or "{}")
            except json.JSONDecodeError:
                payload = {"error": {"message": "OpenAI returned an unreadable error."}}
            return exc.code, payload, dict(exc.headers.items()) if exc.headers else {}
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


def _normalize_transport_result(
    result: TransportResult,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Keep existing two-value test transports working while retaining headers."""

    if len(result) == 2:
        status, payload = result
        headers: dict[str, str] = {}
    else:
        status, payload, headers = result
    normalized_payload = payload if isinstance(payload, dict) else {}
    normalized_headers = {
        str(key).lower(): str(value)
        for key, value in (headers or {}).items()
        if isinstance(key, str) and isinstance(value, str)
    }
    return int(status), normalized_payload, normalized_headers


def _http_error(status: int, payload: dict[str, Any]) -> tuple[str, bool]:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    error_type = str(error.get("type") or "").lower()
    error_code = str(error.get("code") or "").lower()
    error_signature = f"{error_type} {error_code}"
    if status == 400:
        return "OPENAI_INVALID_REQUEST", False
    if status == 401:
        return "OPENAI_AUTHENTICATION_FAILED", False
    if status == 403:
        return "OPENAI_PERMISSION_DENIED", False
    if status == 404:
        return "OPENAI_ENDPOINT_OR_MODEL_NOT_FOUND", False
    if status == 429:
        if any(
            marker in error_signature
            for marker in ("quota", "insufficient", "billing", "budget", "balance")
        ):
            return "OPENAI_QUOTA_OR_BILLING_BLOCKED", False
        return "OPENAI_RATE_LIMITED", True
    if status >= 500:
        return "OPENAI_PROVIDER_TRANSIENT_FAILURE", True
    return "OPENAI_INVALID_REQUEST", False


def _redacted_api_error(payload: dict[str, Any], status: int) -> str:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = str(error.get("code") or error.get("type") or "unknown")
    return f"OpenAI returned HTTP {status} ({code})."


def _http_error_details(
    *,
    endpoint: str,
    operation: str,
    status: int,
    response_payload: dict[str, Any],
    response_headers: dict[str, str],
    request_payload: dict[str, Any],
    model: str,
    tool_type: str | None,
    runtime_origin: str,
) -> dict[str, Any]:
    error = (
        response_payload.get("error")
        if isinstance(response_payload.get("error"), dict)
        else {}
    )
    return {
        "endpoint": _sanitized_endpoint(endpoint),
        "operation": operation,
        "http_status": status,
        "openai_error_type": _safe_optional_string(error.get("type")),
        "openai_error_code": _safe_optional_string(error.get("code")),
        "openai_error_message": _sanitize_error_message(error.get("message")),
        "x_request_id": response_headers.get("x-request-id"),
        "response_body_hash": _stable_hash(response_payload),
        "request_payload_hash": _stable_hash(request_payload),
        "model": model,
        "tool_type": tool_type,
        "retry_after": response_headers.get("retry-after"),
        "runtime_origin": runtime_origin,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


def _network_error_details(
    *,
    endpoint: str,
    operation: str,
    request_payload: dict[str, Any],
    model: str,
    tool_type: str | None,
    runtime_origin: str,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "endpoint": _sanitized_endpoint(endpoint),
        "operation": operation,
        "http_status": None,
        "openai_error_type": type(exc).__name__,
        "openai_error_code": "NETWORK_FAILURE",
        "openai_error_message": _sanitize_error_message(str(exc)),
        "x_request_id": None,
        "response_body_hash": None,
        "request_payload_hash": _stable_hash(request_payload),
        "model": model,
        "tool_type": tool_type,
        "retry_after": None,
        "runtime_origin": runtime_origin,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


def _stable_hash(value: dict[str, Any]) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _safe_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return _sanitize_error_message(str(value))


def _sanitize_error_message(value: Any) -> str | None:
    if value is None:
        return None
    message = str(value).replace("\x00", " ").strip()
    message = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", message)
    message = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", message)
    return message[:512] or None


def _sanitized_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if not parsed.scheme or not parsed.hostname:
        return "unrecognized-endpoint"
    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _safe_runtime_origin(value: str | None) -> str:
    if not value:
        return "unspecified-runtime"
    normalized = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", normalized):
        return "unspecified-runtime"
    return normalized


def _redacted_network_error(exc: Exception) -> str:
    return _sanitize_error_message(str(exc)) or "OpenAI network request failed."


def _maybe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _latency_ms(started: float) -> int:
    return max(1, int((time.monotonic() - started) * 1000))


def _error_response(
    error_code: str,
    message: str,
    started: float,
    *,
    retryable: bool,
    output: dict[str, Any] | None = None,
) -> ProviderResponse:
    return ProviderResponse(
        ok=False,
        output=output or {},
        error_code=error_code,
        error_message=message,
        retryable=retryable,
        latency_ms=_latency_ms(started),
    )
