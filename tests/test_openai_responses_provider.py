from __future__ import annotations

import pytest

from app.providers.openai import OpenAIResponsesProvider, OpenAIResponsesRequest


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
            model="gpt-5.6-terra",
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
                model="gpt-5.6-terra",
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
            "model": "gpt-5.6-terra",
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
            model="gpt-5.6-terra",
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
    assert response.error_code == "OPENAI_CREDENTIAL_REJECTED"
    assert response.retryable is False
