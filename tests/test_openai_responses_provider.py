from __future__ import annotations

import pytest

from app.providers.openai import (
    OpenAIResponsesProvider,
    OpenAIResponsesRequest,
    OpenAIWebSearchRequest,
)


def test_responses_payload_is_explicit_and_never_enables_store() -> None:
    provider = OpenAIResponsesProvider(api_key="test-key")

    payload = provider.build_responses_payload(
        request=OpenAIResponsesRequest(
            model="gpt-5.6-luna",
            reasoning_effort="none",
            prompt="Return a tiny object.",
            response_format="json",
        )
    )

    assert payload["model"] == "gpt-5.6-luna"
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["store"] is False
    assert payload["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "Return a tiny object."}],
        }
    ]
    assert payload["text"]["format"]["type"] == "json_schema"


def test_responses_payload_accepts_still_contact_sheet_but_not_raw_video() -> None:
    provider = OpenAIResponsesProvider(api_key="test-key")
    payload = provider.build_responses_payload(
        request=OpenAIResponsesRequest(
            model="gpt-5.6-luna",
            reasoning_effort="medium",
            prompt="Review the supplied contact sheet.",
            image_inputs=[
                {
                    "media_type": "image/png",
                    "image_url": "data:image/png;base64,aGVsbG8=",
                }
            ],
        )
    )

    assert payload["input"][0]["content"][-1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,aGVsbG8=",
    }
    with pytest.raises(ValueError, match="OPENAI_VISUAL_INPUT_MUST_BE_STILL_IMAGE"):
        provider.build_responses_payload(
            request=OpenAIResponsesRequest(
                model="gpt-5.6-luna",
                reasoning_effort="medium",
                prompt="Never review raw video.",
                image_inputs=[
                    {
                        "media_type": "video/mp4",
                        "image_url": "https://example.test/final.mp4",
                    }
                ],
            )
        )


def test_responses_provider_parses_usage_and_request_id_without_fallback() -> None:
    calls: list[tuple[str, str, dict]] = []

    def transport(method, url, payload, headers, timeout_seconds):
        calls.append((method, url, payload))
        assert headers["Authorization"] == "Bearer test-key"
        return 200, {
            "id": "resp_canary_1",
            "model": "gpt-5.6-luna",
            "output_text": '{"ok": true}',
            "usage": {
                "input_tokens": 20,
                "input_tokens_details": {"cached_tokens": 4},
                "output_tokens": 9,
                "output_tokens_details": {"reasoning_tokens": 3},
                "total_tokens": 29,
            },
        }

    provider = OpenAIResponsesProvider(
        api_key="test-key", transport=transport, timeout_seconds=3
    )
    response = provider.respond(
        request=OpenAIResponsesRequest(
            model="gpt-5.6-luna",
            reasoning_effort="medium",
            prompt="Return JSON.",
            response_format="json",
        )
    )

    assert response.ok is True
    assert len(calls) == 1
    assert response.output["request_id"] == "resp_canary_1"
    assert response.output["json"] == {"ok": True}
    assert response.output["usage"] == {
        "input_tokens": 20,
        "cached_input_tokens": 4,
        "output_tokens": 9,
        "reasoning_tokens": 3,
        "total_tokens": 29,
    }


def test_responses_provider_fails_closed_for_rejected_credentials() -> None:
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        transport=lambda *args: (401, {"error": {"type": "invalid_api_key"}}),
    )

    response = provider.respond(
        request=OpenAIResponsesRequest(
            model="gpt-5.6-luna",
            reasoning_effort="none",
            prompt="No fallback.",
        )
    )

    assert response.ok is False
    assert response.error_code == "OPENAI_AUTHENTICATION_FAILED"
    assert response.retryable is False


@pytest.mark.parametrize(
    ("status", "error", "expected_code", "retryable"),
    [
        (400, {"type": "invalid_request_error", "code": "invalid_value"}, "OPENAI_INVALID_REQUEST", False),
        (401, {"type": "authentication_error", "code": "invalid_api_key"}, "OPENAI_AUTHENTICATION_FAILED", False),
        (403, {"type": "permission_denied", "code": "restricted_api_key"}, "OPENAI_PERMISSION_DENIED", False),
        (429, {"type": "insufficient_quota", "code": "insufficient_quota"}, "OPENAI_QUOTA_OR_BILLING_BLOCKED", False),
        (429, {"type": "rate_limit_exceeded", "code": "rate_limit_exceeded"}, "OPENAI_RATE_LIMITED", True),
        (500, {"type": "server_error", "code": "internal_error"}, "OPENAI_PROVIDER_TRANSIENT_FAILURE", True),
    ],
)
def test_responses_provider_classifies_http_errors_and_preserves_safe_receipt(
    status, error, expected_code, retryable
) -> None:
    calls = 0

    def transport(*_args):
        nonlocal calls
        calls += 1
        return status, {"error": {**error, "message": "OpenAI diagnostic failure"}}, {
            "X-Request-Id": "req_diagnostic_123",
            "Retry-After": "7",
        }

    response = OpenAIResponsesProvider(
        api_key="test-key",
        transport=transport,
        runtime_origin="production-workflow-worker",
    ).respond(
        request=OpenAIResponsesRequest(
            model="gpt-5.6-luna",
            reasoning_effort="low",
            prompt="Reply with exactly OK",
        )
    )

    assert calls == 1
    assert response.ok is False
    assert response.error_code == expected_code
    assert response.retryable is retryable
    assert response.output["error"] == {
        "endpoint": "https://api.openai.com/v1/responses",
        "operation": "responses",
        "http_status": status,
        "openai_error_type": error["type"],
        "openai_error_code": error["code"],
        "openai_error_message": "OpenAI diagnostic failure",
        "x_request_id": "req_diagnostic_123",
        "response_body_hash": response.output["error"]["response_body_hash"],
        "request_payload_hash": response.output["error"]["request_payload_hash"],
        "model": "gpt-5.6-luna",
        "tool_type": None,
        "retry_after": "7",
        "runtime_origin": "production-workflow-worker",
        "occurred_at": response.output["error"]["occurred_at"],
    }
    assert len(response.output["error"]["response_body_hash"]) == 64
    assert len(response.output["error"]["request_payload_hash"]) == 64
    assert response.output["error"]["occurred_at"].endswith("+00:00")


def test_responses_provider_maps_network_failure_without_retry_or_secret_leak() -> None:
    calls = 0

    def transport(*_args):
        nonlocal calls
        calls += 1
        raise TimeoutError("request failed with Bearer test-key")

    response = OpenAIResponsesProvider(api_key="test-key", transport=transport).respond(
        request=OpenAIResponsesRequest(
            model="gpt-5.6-luna",
            reasoning_effort="low",
            prompt="Private prompt that must only be hashed.",
        )
    )

    assert calls == 1
    assert response.ok is False
    assert response.error_code == "OPENAI_NETWORK_FAILURE"
    assert response.retryable is True
    assert response.output["error"]["http_status"] is None
    assert response.output["error"]["openai_error_type"] == "TimeoutError"
    assert response.output["error"]["openai_error_message"] == "request failed with Bearer [REDACTED]"
    serialized = repr(response)
    assert "test-key" not in serialized


def test_background_submit_persists_response_identity_without_waiting_for_output() -> None:
    calls: list[tuple[str, str, dict | None, int]] = []

    def transport(method, url, payload, _headers, timeout_seconds):
        calls.append((method, url, payload, timeout_seconds))
        return 202, {"id": "resp_background_1", "status": "queued"}, {
            "x-request-id": "req_background_1"
        }

    response = OpenAIResponsesProvider(api_key="test-key", transport=transport).submit_background(
        request=OpenAIResponsesRequest(
            model="gpt-5.6-luna",
            reasoning_effort="medium",
            prompt="Return JSON.",
            response_format="json",
            idempotency_key="qualification-writer-1",
        ),
        timeout_seconds=7,
    )

    assert response.ok is True
    assert response.output["provider_response_id"] == "resp_background_1"
    assert response.output["provider_request_id"] == "req_background_1"
    assert len(calls) == 1
    method, url, payload, timeout_seconds = calls[0]
    assert (method, url, timeout_seconds) == (
        "POST",
        "https://api.openai.com/v1/responses",
        7,
    )
    assert payload is not None and payload["background"] is True
    assert "output_text" not in payload


def test_background_poll_uses_durable_response_id_and_network_error_is_retryable() -> None:
    calls: list[tuple[str, str, dict | None, int]] = []

    def transport(method, url, payload, _headers, timeout_seconds):
        calls.append((method, url, payload, timeout_seconds))
        raise TimeoutError("poll timed out")

    response = OpenAIResponsesProvider(api_key="test-key", transport=transport).retrieve_background(
        response_id="resp_background_1", timeout_seconds=4
    )

    assert response.ok is False
    assert response.error_code == "OPENAI_NETWORK_FAILURE"
    assert response.retryable is True
    assert calls == [
        ("GET", "https://api.openai.com/v1/responses/resp_background_1", None, 4)
    ]
    assert "Authorization" not in serialized
    assert "Private prompt" not in serialized


def test_web_search_payload_matches_supported_schema_without_fallback() -> None:
    payload = OpenAIResponsesProvider(api_key="test-key").build_web_search_payload(
        request=OpenAIWebSearchRequest(
            model="gpt-5.6-luna",
            reasoning_effort="low",
            query="Find the official OpenAI API key safety documentation.",
            allowed_domains=["openai.com"],
        )
    )

    assert payload == {
        "model": "gpt-5.6-luna",
        "input": "Find the official OpenAI API key safety documentation.",
        "reasoning": {"effort": "low"},
        "tools": [
            {
                "type": "web_search",
                "search_context_size": "low",
                "external_web_access": True,
                "filters": {"allowed_domains": ["openai.com"]},
            }
        ],
        "tool_choice": "required",
        "include": ["web_search_call.action.sources"],
        "store": False,
    }
    assert "fallback" not in payload
