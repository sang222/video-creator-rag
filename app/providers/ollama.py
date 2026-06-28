from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

from app.providers.base import ProviderResponse


Transport = Callable[[str, str, dict[str, Any] | None, int], tuple[int, dict[str, Any]]]


@dataclass(frozen=True)
class OllamaChatRequest:
    model: str
    prompt: str | None = None
    response_format: str = "text"
    messages: list[dict[str, str]] | None = None
    system: str | None = None


class OllamaLLMProvider:
    provider_key = "OLLAMA"

    def __init__(self, *, base_url: str = "http://localhost:11434", timeout_seconds: int = 30, transport: Transport | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._urllib_transport

    def list_models(self) -> ProviderResponse:
        started = time.monotonic()
        try:
            status, payload = self._transport("GET", f"{self.base_url}/api/tags", None, self.timeout_seconds)
        except TimeoutError as exc:
            return _error_response("PROVIDER_TIMEOUT", str(exc), started, retryable=True)
        except OSError as exc:
            return _error_response("PROVIDER_UNREACHABLE", str(exc), started, retryable=True)
        if status >= 400:
            return _error_response("PROVIDER_HTTP_ERROR", f"Ollama returned HTTP {status}", started, retryable=status >= 500)
        models = payload.get("models", [])
        return ProviderResponse(ok=True, output={"models": models}, latency_ms=_latency_ms(started))

    def chat(self, *, request: OllamaChatRequest) -> ProviderResponse:
        started = time.monotonic()
        payload = self.build_chat_payload(request=request)
        try:
            status, response_payload = self._transport("POST", f"{self.base_url}/api/chat", payload, self.timeout_seconds)
        except TimeoutError as exc:
            return _error_response("PROVIDER_TIMEOUT", str(exc), started, retryable=True)
        except OSError as exc:
            return _error_response("PROVIDER_UNREACHABLE", str(exc), started, retryable=True)
        if status >= 400:
            return _error_response("PROVIDER_HTTP_ERROR", f"Ollama returned HTTP {status}", started, retryable=status >= 500)
        message = response_payload.get("message") or {}
        content = str(message.get("content") or "")
        if request.response_format == "json" and not content:
            return _error_response("PROVIDER_EMPTY_JSON_CONTENT", "Ollama returned empty JSON content.", started, retryable=True)
        output = {
            "provider_key": self.provider_key,
            "model": response_payload.get("model") or request.model,
            "content": content,
            "raw": response_payload,
            "usage": self.extract_usage(response_payload),
        }
        if request.response_format == "json":
            output["json"] = _parse_json_content(content)
        return ProviderResponse(ok=True, output=output, latency_ms=_latency_ms(started))

    def build_chat_payload(self, *, request: OllamaChatRequest) -> dict[str, Any]:
        messages = _request_messages(request)
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": False,
        }
        if request.response_format == "json":
            payload["format"] = "json"
            payload["think"] = False
        return payload

    def extract_usage(self, payload: dict[str, Any]) -> dict[str, int | None]:
        return {
            "prompt_eval_count": _maybe_int(payload.get("prompt_eval_count")),
            "eval_count": _maybe_int(payload.get("eval_count")),
            "total_duration_ms": _ns_to_ms(payload.get("total_duration")),
            "load_duration_ms": _ns_to_ms(payload.get("load_duration")),
            "prompt_eval_duration_ms": _ns_to_ms(payload.get("prompt_eval_duration")),
            "eval_duration_ms": _ns_to_ms(payload.get("eval_duration")),
        }

    def _urllib_transport(self, method: str, url: str, payload: dict[str, Any] | None, timeout_seconds: int) -> tuple[int, dict[str, Any]]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib_request.Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return response.status, json.loads(raw or "{}")
        except urllib_error.HTTPError as exc:
            return exc.code, {"error": exc.read().decode("utf-8", errors="replace")}
        except TimeoutError:
            raise
        except urllib_error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise reason
            raise OSError(str(reason)) from exc


def _parse_json_content(content: str) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _request_messages(request: OllamaChatRequest) -> list[dict[str, str]]:
    if request.messages is not None:
        return [{"role": str(message["role"]), "content": str(message["content"])} for message in request.messages]
    messages: list[dict[str, str]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    messages.append({"role": "user", "content": request.prompt or ""})
    return messages


def _maybe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ns_to_ms(value: Any) -> int | None:
    parsed = _maybe_int(value)
    if parsed is None:
        return None
    return int(parsed / 1_000_000)


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
