from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.google_gemini_image import (
    MANDATORY_GEMINI_IMAGE_NEGATIVE_CONSTRAINTS,
    GeminiImageGenerationRequest,
)
from app.core.config import Settings
from app.providers.google_gemini_image import (
    GeminiImageResponseSafetyError,
    GoogleGeminiImageAdapter,
    build_fixture_png,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        gemini_api_key="v3-test-placeholder-key",
        gemini_image_model_id="gemini-3.1-flash-image",
        gemini_image_default_size="2K",
        gemini_image_default_aspect_ratio="16:9",
        gemini_image_max_outputs=1,
        gemini_image_max_attempts_per_scene=1,
        gemini_image_provider_route_approved=True,
    )


def _request(version: str) -> GeminiImageGenerationRequest:
    prompt = "Local V3 serialization fixture with no generated written content."
    run_id = (
        f"img-canary-{version.lower()}-20260718T120000Z-a1b2c3d4"
        if version in {"V2", "V3"}
        else "unit-test-v1"
    )
    payload = {
        "generic_request_ref": f"ai-image-request://{run_id}",
        "generic_request_hash": "generic-request-hash",
        "project_id": "img-canary-project",
        "scene_id": "scene-v3-provider-contract",
        "visual_source_decision_hash": "visual-source-decision-hash",
        "native_overlay_plan_hash": None,
        "model_id": "gemini-3.1-flash-image",
        "prompt": prompt,
        "prompt_hash": ai_image_stable_hash(prompt),
        "image_size": "2K",
        "aspect_ratio": "16:9",
        "output_count": 1,
        "four_k_approval_ref": None,
        "reference_images": [],
        "reference_types": [],
        "reference_asset_hashes": [],
        "negative_constraints": sorted(MANDATORY_GEMINI_IMAGE_NEGATIVE_CONSTRAINTS),
        "grounding_enabled": False,
        "search_grounding_enabled": False,
        "grounding_approval_ref": None,
        "text_safe_regions": [],
        "native_overlay_required": False,
        "scene_truth_classification": "NO_EVIDENCE_TRUTH",
        "evidence_truth_requirement": 0.0,
        "product_specificity": 0.0,
        "exact_text_required": False,
        "exact_number_required": False,
        "provider_route": "google_gemini_image",
        "cost_ref": f"cost://{run_id}/0.101-usd",
        "approval_ref": f"approval://{run_id}/one-request",
        "approval_scope": f"IMG_CANARY_ONE_SHOT:{run_id}",
        "idempotency_key": f"provider-idem:{run_id}",
    }
    return GeminiImageGenerationRequest(
        **payload,
        content_hash=ai_image_stable_hash(payload),
    )


def test_v3_official_sdk_body_omits_delivery_while_v1_v2_remain_byte_identical() -> None:
    pytest.importorskip("httpx")
    pytest.importorskip("google.genai")
    v1 = _request("V1")
    v2 = _request("V2")
    v3 = _request("V3")

    v1_capture = GoogleGeminiImageAdapter.capture_official_sdk_serialization(v1)
    v2_capture = GoogleGeminiImageAdapter.capture_official_sdk_serialization(v2)
    v3_capture = GoogleGeminiImageAdapter.capture_official_sdk_serialization(v3)

    assert v1_capture["body"] == v2_capture["body"]
    assert v1_capture["body_sha256"] == v2_capture["body_sha256"]
    assert v2_capture["body"]["response_format"] == {
        "type": "image",
        "mime_type": "image/jpeg",
        "delivery": "inline",
        "aspect_ratio": "16:9",
        "image_size": "2K",
    }
    assert v3_capture["body"]["response_format"] == {
        "type": "image",
        "mime_type": "image/jpeg",
        "aspect_ratio": "16:9",
        "image_size": "2K",
    }
    assert "delivery" not in v3_capture["body"]["response_format"]
    assert "response_modalities" not in v3_capture["body"]
    assert v3_capture["sdk_retries_disabled"] is True
    assert v3_capture["body_sha256"] != v2_capture["body_sha256"]


def test_v3_is_a_strict_inline_jpeg_one_shot_contract() -> None:
    request = _request("V3")
    assert request.uses_img_canary_v2_response_contract is False
    assert request.uses_img_canary_v3_response_contract is True
    assert request.uses_strict_inline_jpeg_response_contract is True
    assert request.strict_img_canary_contract_version == "V3"

    png = build_fixture_png(width=1920, height=1080)
    response = {
        "id": "interactions/v3-local-fixture",
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "image",
                        "data": base64.b64encode(png).decode("ascii"),
                        "uri": None,
                        "mime_type": "image/jpeg",
                    }
                ],
            }
        ],
    }
    with pytest.raises(
        GeminiImageResponseSafetyError,
        match="GEMINI_IMAGE_V3_INLINE_JPEG_BYTES_REQUIRED",
    ):
        GoogleGeminiImageAdapter(_settings())._parse_real_response(
            request,
            response,
            submitted_at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        )


def test_v3_http_diagnostic_persists_only_allowlisted_metadata() -> None:
    secret = "prompt-and-api-key-must-never-persist"

    class _ProviderHTTPError(RuntimeError):
        status_code = 400
        body = json.dumps(
            {
                "error": {
                    "code": 400,
                    "status": "INVALID_ARGUMENT",
                    "message": secret,
                    "details": [
                        {
                            "fieldViolations": [
                                {
                                    "field": "response_format.delivery",
                                    "description": secret,
                                }
                            ]
                        }
                    ],
                }
            }
        )

    adapter = GoogleGeminiImageAdapter(_settings())
    diagnostic = adapter._safe_provider_error_diagnostic(_ProviderHTTPError(secret))
    assert diagnostic == {
        "code": 400,
        "category": "INVALID_ARGUMENT",
        "parameter_path": "response_format.delivery",
    }

    request = _request("V3")
    summary = adapter._safe_response_summary(
        request,
        normalized_status="FAILED",
        provider_status="NATIVE_SUBMIT_FAILED",
        provider_request_id=None,
        output_count=0,
        output_mime_type=None,
        usage={},
        provider_error_diagnostic={
            **diagnostic,
            "raw_body": secret,
        },
    )
    durable = json.dumps(summary, sort_keys=True)
    assert summary["provider_error_diagnostic"] == diagnostic
    assert set(summary["provider_error_diagnostic"]) == {
        "code",
        "category",
        "parameter_path",
    }
    assert secret not in durable
    assert "message" not in durable
    assert "description" not in durable
    assert adapter._sanitize_provider_error_diagnostic(
        {"parameter_path": f"input.{secret}"}
    ) == {}

    legacy_summary = adapter._safe_response_summary(
        _request("V2"),
        normalized_status="FAILED",
        provider_status="NATIVE_SUBMIT_FAILED",
        provider_request_id=None,
        output_count=0,
        output_mime_type=None,
        usage={},
        provider_error_diagnostic=diagnostic,
    )
    assert "provider_error_diagnostic" not in legacy_summary


def test_v3_task_binding_hook_uses_authority_metadata_and_v3_approval_ref() -> None:
    request = _request("V3")
    fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(request)
    run_id = request.approval_scope.removeprefix("IMG_CANARY_ONE_SHOT:")
    body_hash = "b" * 64
    approval_hash = "c" * 64
    authority = SimpleNamespace(
        approval_version="V3",
        approved_run_id=run_id,
        approved_request_fingerprint=fingerprint,
        approved_prompt_hash=request.prompt_hash,
        approved_serialized_body_hash=body_hash,
        approved_scoped_approval_hash=approval_hash,
    )
    refs = {
        "serialized_request_body": body_hash,
        "v3_approval_binding": approval_hash,
    }

    assert GoogleGeminiImageAdapter._task_authority_metadata_is_bound(
        request=request,
        run_id=run_id,
        fingerprint=fingerprint,
        task_authority=authority,
        evidence_refs=refs,
    )
    authority.approval_version = "V2"
    assert not GoogleGeminiImageAdapter._task_authority_metadata_is_bound(
        request=request,
        run_id=run_id,
        fingerprint=fingerprint,
        task_authority=authority,
        evidence_refs=refs,
    )
