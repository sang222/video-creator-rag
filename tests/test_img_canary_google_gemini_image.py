from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.google_gemini_image import (
    MANDATORY_GEMINI_IMAGE_NEGATIVE_CONSTRAINTS,
    GeminiImageExecutionGates,
    GeminiImageGenerationRequest,
)
from app.core.config import Settings
from app.contracts.img_canary import IMGCanaryPreflightEvidence
from app.contracts.img_canary_security import (
    IMGCanaryBudgetReservationEvidence,
    IMGCanaryCredentialRotationEvidence,
    IMGCanaryTaskAuthorizationLedger,
)
from app.providers.google_gemini_image import (
    GeminiImageResponseSafetyError,
    GoogleGeminiImageAdapter,
    build_fixture_png,
)
from app.services.img_canary import IMGCanaryAttemptLedgerStore
from app.services.img_canary_security import (
    IMGCanaryCredentialRotationAuthority,
    IMGCanaryMonthlyBudgetAuthority,
    IMGCanaryTaskAuthorizationStore,
)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "gemini_api_key": "unit-test-placeholder-key",
        "gemini_image_model_id": "gemini-3.1-flash-image",
        "gemini_image_default_size": "2K",
        "gemini_image_default_aspect_ratio": "16:9",
        "gemini_image_max_outputs": 1,
        "gemini_image_max_attempts_per_scene": 1,
        "gemini_image_provider_route_approved": True,
        "gemini_image_real_generation_enabled": True,
        "img1_fixture_only": False,
        "provider_real_execution_enabled": True,
        "provider_production_execution_enabled": True,
        "media_provider_calls_disabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _request() -> GeminiImageGenerationRequest:
    prompt = (
        "Clean professional editorial illustration of fragmented document-like clusters, "
        "one coherent focal composition, generous negative space, with no written content."
    )
    payload: dict[str, Any] = {
        "generic_request_ref": "ai-image-request://img-canary/unit-test",
        "generic_request_hash": "generic-request-hash",
        "project_id": "img-canary-project",
        "scene_id": "scene-fragmented-information",
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
        "cost_ref": "cost://img-canary/unit-test/0.101-usd",
        "approval_ref": "approval://img-canary/unit-test/one-request",
        "approval_scope": "IMG_CANARY_ONE_SHOT:unit-test",
        "idempotency_key": "provider-idem:img-canary-unit-test",
    }
    return GeminiImageGenerationRequest(
        **payload,
        content_hash=ai_image_stable_hash(payload),
    )


def _v2_request() -> GeminiImageGenerationRequest:
    payload = _request().model_dump(mode="python", exclude={"content_hash"})
    payload.update(
        {
            "approval_ref": "approval://img-canary-v2/unit-test/one-request",
            "approval_scope": (
                "IMG_CANARY_ONE_SHOT:"
                "img-canary-v2-20260718T120000Z-a1b2c3d4"
            ),
            "idempotency_key": "provider-idem:img-canary-v2-unit-test",
        }
    )
    return GeminiImageGenerationRequest(
        **payload,
        content_hash=ai_image_stable_hash(payload),
    )


def _gates(
    request: GeminiImageGenerationRequest,
    **overrides: Any,
) -> GeminiImageExecutionGates:
    payload: dict[str, Any] = {
        "provider_boundary_gate_passed": True,
        "paid_call_authorization_gate_passed": True,
        "provider_cost_estimate_gate_passed": True,
        "channel_monthly_budget_gate_passed": True,
        "paid_attempt_limit_gate_passed": True,
        "provider_idempotency_key_valid": True,
        "global_kill_switch_open": True,
        "provider_kill_switch_open": True,
        "approved_production_execution_scope": True,
        "provider_boundary_gate_ref": "gate://img-canary/provider-boundary/pass",
        "paid_call_authorization_gate_ref": request.approval_ref,
        "provider_cost_estimate_gate_ref": request.cost_ref,
        "channel_monthly_budget_gate_ref": "gate://img-canary/monthly-budget/pass",
        "paid_attempt_limit_gate_ref": "gate://img-canary/attempt-limit/one",
        "provider_idempotency_key_ref": request.idempotency_key,
        "global_kill_switch_ref": "scope://img-canary/global-execution",
        "provider_kill_switch_ref": "scope://img-canary/gemini-image-execution",
        "request_fingerprint": GoogleGeminiImageAdapter.idempotency_fingerprint(request),
    }
    payload.update(overrides)
    return GeminiImageExecutionGates(
        **payload,
        evidence_hash=ai_image_stable_hash(payload),
    )


def _preflight(
    request: GeminiImageGenerationRequest,
    gates: GeminiImageExecutionGates,
    *,
    budget: IMGCanaryBudgetReservationEvidence,
    credential: IMGCanaryCredentialRotationEvidence,
    task_authorization: IMGCanaryTaskAuthorizationLedger,
    attempt_ledger_hash: str,
    planning_preflight_hash: str | None = None,
) -> IMGCanaryPreflightEvidence:
    checked_at = datetime(2026, 7, 18, tzinfo=UTC)
    payload: dict[str, Any] = {
        "run_id": "unit-test",
        "status": "PASS",
        "repository_identity_passed": True,
        "worktree_reviewed": True,
        "vqc1_final_passed": True,
        "credential_configured": True,
        "credential_safe_for_use": True,
        "credential_rotation_evidence": credential,
        "route_registered": True,
        "model_catalog_present": True,
        "model_locked": True,
        "image_size_locked": True,
        "aspect_ratio_locked": True,
        "output_count_locked": True,
        "reference_images_empty": True,
        "grounding_disabled": True,
        "raster_decoder_ready": True,
        "provider_boundary_passed": True,
        "cost_estimate_passed": True,
        "paid_authorization_passed": True,
        "monthly_budget_passed": True,
        "task_authorization_passed": True,
        "attempt_limit_passed": True,
        "idempotency_passed": True,
        "global_kill_switch_scoped_open": True,
        "provider_kill_switch_scoped_open": True,
        "defaults_remain_disabled": True,
        "monthly_budget_evidence": budget,
        "task_authorization_evidence": task_authorization,
        "production_database_mutation_required": False,
        "blocker_reason_codes": [],
        "evidence_refs": {
            "provider_request": request.content_hash,
            "execution_gates": gates.evidence_hash,
            "monthly_budget": budget.content_hash,
            "credential_rotation": credential.content_hash,
            "task_authorization": task_authorization.content_hash,
            "attempt_ledger_planned": attempt_ledger_hash,
            "raster_decoder_readiness": (
                IMGCanaryPreflightEvidence.raster_decoder_evidence_hash(ready=True)
            ),
            **(
                {"planning_preflight": planning_preflight_hash}
                if planning_preflight_hash
                else {}
            ),
        },
        "checked_at": checked_at,
        "approval_expires_at": datetime(2099, 7, 18, tzinfo=UTC),
    }
    return IMGCanaryPreflightEvidence(
        **payload,
        content_hash=ai_image_stable_hash(payload),
    )


@dataclass(frozen=True)
class _RuntimeContext:
    planning_preflight: IMGCanaryPreflightEvidence
    preflight: IMGCanaryPreflightEvidence
    preflight_path: Path
    gates_path: Path
    store: IMGCanaryAttemptLedgerStore
    workspace: Path
    destination: Path


def _runtime_context(
    tmp_path: Path,
    request: GeminiImageGenerationRequest,
    gates: GeminiImageExecutionGates,
) -> _RuntimeContext:
    now = datetime(2026, 7, 18, tzinfo=UTC)
    fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(request)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    workspace = repo / "artifacts" / "img_canary" / "unit-test"
    manifests = workspace / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    store = IMGCanaryAttemptLedgerStore(manifests / "attempt-ledger.json")
    store.create(
        run_id="unit-test",
        request_fingerprint=fingerprint,
        idempotency_key=request.idempotency_key,
        now=now,
    )
    attempt_hash = store.load().content_hash

    security_root = repo / "var" / "credentials" / "img-canary"
    credential_authority = IMGCanaryCredentialRotationAuthority(
        security_root / "compromised-credential.json"
    )
    credential_authority.record_compromised(
        credential="superseded-unit-test-placeholder-key",
        incident_ref="incident://img-canary/tests/exposure",
        now=now,
    )
    credential = credential_authority.verify_rotation(
        current_credential="unit-test-placeholder-key",
        rotation_ref="rotation://img-canary/tests/adapter",
        now=now,
    )
    task_store = IMGCanaryTaskAuthorizationStore(
        security_root / "master-authorization.json"
    )
    available_task = task_store.initialize(
        task_key="img-canary-master-prompt-2026-07-18",
        authorization_ref="authorization://img-canary/master-prompt/one-paid-request",
        now=now,
    )
    budget_store = IMGCanaryMonthlyBudgetAuthority(
        security_root / "budget-2026-07.json"
    )
    budget_store.initialize(
        authority_ref="budget://small-team-ai/2026-07/img-canary",
        billing_period="2026-07",
        dedicated_cap_usd=Decimal("1.00"),
        opening_spend_usd=Decimal("0"),
        per_request_hard_cap_usd=Decimal("0.15"),
        now=now,
    )
    available_budget = budget_store.inspect_capacity(
        run_id="unit-test",
        request_fingerprint=fingerprint,
        request_estimate_usd=Decimal("0.101"),
        now=now,
    )
    planning = _preflight(
        request,
        gates,
        budget=available_budget,
        credential=credential,
        task_authorization=available_task,
        attempt_ledger_hash=attempt_hash,
    )
    planning_path = manifests / "preflight.json"
    planning_path.write_text(planning.model_dump_json(indent=2) + "\n", encoding="utf-8")

    claimed = task_store.claim(
        run_id="unit-test",
        request_fingerprint=fingerprint,
        now=now,
    )
    reserved = budget_store.reserve(
        run_id="unit-test",
        request_fingerprint=fingerprint,
        request_estimate_usd=Decimal("0.101"),
        now=now,
    )
    runtime = _preflight(
        request,
        gates,
        budget=reserved,
        credential=credential,
        task_authorization=claimed,
        attempt_ledger_hash=attempt_hash,
        planning_preflight_hash=planning.content_hash,
    )
    runtime_path = manifests / "preflight-runtime-submit.json"
    runtime_path.write_text(runtime.model_dump_json(indent=2) + "\n", encoding="utf-8")
    gates_path = manifests / "execution-gates-runtime-submit.json"
    gates_path.write_text(gates.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return _RuntimeContext(
        planning_preflight=planning,
        preflight=runtime,
        preflight_path=runtime_path,
        gates_path=gates_path,
        store=store,
        workspace=workspace,
        destination=workspace / "source" / "original-generated.raster",
    )


def _image_content(
    image_bytes: bytes,
    *,
    mime_type: str = "image/png",
    uri: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "image",
        "data": base64.b64encode(image_bytes).decode("ascii"),
        "uri": uri,
        "mime_type": mime_type,
    }


def _interaction_response(
    images: list[dict[str, Any]],
    *,
    status: str = "completed",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "interactions/img-canary-unit-test-001",
        "status": status,
        "steps": [{"type": "model_output", "content": images}],
        "output_image": images[-1] if images else None,
        "usage": usage
        or {
            "total_input_tokens": 97,
            "total_output_tokens": 31,
            "total_tokens": 128,
        },
    }


class _FakeInteractions:
    def __init__(
        self,
        *,
        response: Any = None,
        failure: Exception | None = None,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
    ):
        self.response = response
        self.failure = failure
        self.entered = entered
        self.release = release
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None and not self.release.wait(timeout=5):
            raise RuntimeError("FAKE_INTERACTIONS_RELEASE_TIMEOUT")
        if self.failure is not None:
            raise self.failure
        return self.response


class _FakeRealClient:
    def __init__(self, interactions: _FakeInteractions):
        self.interactions = interactions


def _valid_fixture_jpeg(
    tmp_path: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    name: str = "fixture-output",
) -> bytes:
    decoder = GoogleGeminiImageAdapter(_settings()).raster_decoder_path
    if not decoder or not Path(decoder).is_file():
        pytest.skip("FFmpeg JPEG fixture encoder is unavailable")
    source = tmp_path / f"{name}-source.png"
    destination = tmp_path / f"{name}.jpg"
    source.write_bytes(build_fixture_png(width=width, height=height))
    completed = subprocess.run(
        [
            decoder,
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-c:v",
            "mjpeg",
            "-q:v",
            "2",
            str(destination),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0 or not destination.is_file():
        pytest.skip("FFmpeg could not build the local JPEG fixture")
    return destination.read_bytes()


# Adapter real-path execution safety. Orchestration, normalization, render and
# archive assertions are intentionally added by the IMG canary integration suite.


def test_real_adapter_uses_official_interactions_shape_once_and_materializes_before_success(
    tmp_path: Path,
) -> None:
    request = _request()
    png = build_fixture_png(width=1920, height=1080)
    encoded = base64.b64encode(png).decode("ascii")
    fake = _FakeInteractions(
        response=_interaction_response(
            [_image_content(png)],
            usage={
                "total_input_tokens": 97,
                "total_cached_tokens": 0,
                "total_output_tokens": 31,
                "total_tokens": 128,
                "negative_count_is_rejected": -1,
                "raw_provider_secret": "must-not-persist",
            },
        )
    )
    adapter = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    )

    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)
    preflight = runtime.preflight
    store = runtime.store
    workspace = runtime.workspace
    destination = runtime.destination
    receipt = adapter.submit_generation(
        request,
        gates=gates,
        preflight=preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=store,
        workspace_root=workspace,
        destination_path=destination,
    )
    duplicate = adapter.submit_generation(
        request,
        gates=gates,
        preflight=preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=store,
        workspace_root=workspace,
        destination_path=destination,
    )

    assert duplicate.state_hash == receipt.state_hash
    assert len(fake.calls) == 1
    assert fake.calls[0] == {
        "model": "gemini-3.1-flash-image",
        "input": request.prompt,
        "stream": False,
        "store": False,
        "background": False,
        "response_format": {
            "type": "image",
            "mime_type": "image/jpeg",
            "delivery": "inline",
            "aspect_ratio": "16:9",
            "image_size": "2K",
        },
        "timeout": 120.0,
    }
    assert receipt.normalized_status == "SUCCEEDED"
    assert receipt.provider_status == "INTERACTION_COMPLETED"
    assert receipt.provider_request_id == "interactions/img-canary-unit-test-001"
    assert receipt.provider_call_made is True
    assert receipt.generation_attempts_consumed == 1
    assert receipt.fallback_provider_key is None
    assert receipt.external_provider_fallback_used is False
    assert receipt.actual_cost is None
    assert store.load().status == "SUCCEEDED"
    assert store.load().attempts_consumed == 1
    with pytest.raises(ValueError, match="GEMINI_IMAGE_TRANSIENT_OUTPUT_NOT_AVAILABLE"):
        adapter.transient_output_for(receipt)

    durable_receipt = json.dumps(receipt.model_dump(mode="json"), sort_keys=True)
    summary = adapter.provider_response_summary_for(receipt)
    durable_summary = json.dumps(summary, sort_keys=True)
    for forbidden in (encoded, "must-not-persist", "unit-test-placeholder-key", "https://"):
        assert forbidden not in durable_receipt
        assert forbidden not in durable_summary
    assert summary["usage"] == {
        "total_input_tokens": 97,
        "total_cached_tokens": 0,
        "total_output_tokens": 31,
        "total_tokens": 128,
    }
    assert summary["output_sha256"] == hashlib.sha256(png).hexdigest()
    assert summary["raw_response_persisted"] is False
    assert summary["base64_image_data_persisted"] is False
    assert summary["temporary_url_persisted"] is False

    materialized = adapter.materialization_receipt_for(receipt)
    assert materialized["transport"] == "GEMINI_API_NATIVE"
    assert materialized["provider_call_made"] is True
    assert materialized["image_format"] == "PNG"
    assert materialized["sha256"] == hashlib.sha256(png).hexdigest()
    assert destination.read_bytes() == png
    assert not destination.with_name(destination.name + ".part").exists()


def test_official_sdk_serializes_current_inline_image_response_format() -> None:
    httpx = pytest.importorskip("httpx")
    genai = pytest.importorskip("google.genai")
    from google.genai import types

    captured: list[dict[str, Any]] = []

    def handler(request: Any) -> Any:
        captured.append(
            {
                "url": str(request.url),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            json={
                "id": "interactions/img-canary-serialization-test",
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "fixture"}],
                    }
                ],
            },
            request=request,
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = genai.Client(
        api_key="serialization-test-placeholder",
        http_options=types.HttpOptions(
            httpx_client=http_client,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    try:
        client.interactions.create(
            model="gemini-3.1-flash-image",
            input="local serialization fixture",
            stream=False,
            store=False,
            background=False,
            response_format={
                "type": "image",
                "mime_type": "image/jpeg",
                "delivery": "inline",
                "aspect_ratio": "16:9",
                "image_size": "2K",
            },
            timeout=120.0,
        )
    finally:
        client.close()

    assert len(captured) == 1
    assert captured[0]["url"].endswith("/v1beta/interactions")
    assert "serialization-test-placeholder" not in captured[0]["url"]
    assert captured[0]["body"] == {
        "model": "gemini-3.1-flash-image",
        "input": "local serialization fixture",
        "stream": False,
        "store": False,
        "background": False,
        "response_format": {
            "type": "image",
            "mime_type": "image/jpeg",
            "delivery": "inline",
            "aspect_ratio": "16:9",
            "image_size": "2K",
        },
    }


def test_v2_shared_official_sdk_serialization_is_exact_redaction_safe_and_no_retry() -> None:
    pytest.importorskip("httpx")
    pytest.importorskip("google.genai")
    request = _v2_request()

    captured = GoogleGeminiImageAdapter.capture_official_sdk_serialization(request)

    assert captured == {
        "method": "POST",
        "path": "/v1beta/interactions",
        "body": GoogleGeminiImageAdapter.expected_serialized_request_body(request),
        "body_sha256": captured["body_sha256"],
        "credential_in_url": False,
        "credential_in_body": False,
        "sdk_retries_disabled": True,
    }
    assert len(captured["body_sha256"]) == 64
    durable = json.dumps(captured, sort_keys=True)
    assert "serialization-only-placeholder" not in durable
    assert "response_modalities" not in durable
    assert "authorization" not in durable.lower()


def test_v2_strict_response_accepts_one_inline_decodable_jpeg_and_persists_no_payload(
    tmp_path: Path,
) -> None:
    request = _v2_request()
    jpeg = _valid_fixture_jpeg(
        tmp_path,
        width=2752,
        height=1536,
        name="v2-valid-2k",
    )
    encoded = base64.b64encode(jpeg).decode("ascii")
    adapter = GoogleGeminiImageAdapter(_settings())

    receipt, transient, summary = adapter._parse_real_response(
        request,
        _interaction_response([_image_content(jpeg, mime_type="image/jpeg")]),
        submitted_at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )

    assert receipt.normalized_status == "SUCCEEDED"
    assert transient.mime_type == "image/jpeg"
    assert transient.raw_temporary_url is None
    assert summary["output_count"] == 1
    assert summary["output_mime_type"] == "image/jpeg"
    assert (summary["image_width"], summary["image_height"]) == (2752, 1536)
    durable = json.dumps(summary, sort_keys=True)
    assert encoded not in durable
    assert "data:image" not in durable
    assert "https://" not in durable
    assert "unit-test-placeholder-key" not in durable


def test_v2_strict_response_rejects_png_missing_jpeg_mime_uri_and_multiple_outputs(
    tmp_path: Path,
) -> None:
    request = _v2_request()
    adapter = GoogleGeminiImageAdapter(_settings())
    submitted_at = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    png = build_fixture_png(width=1920, height=1080)
    jpeg = _valid_fixture_jpeg(tmp_path, name="v2-contract")
    distinct_output_alias = _interaction_response(
        [_image_content(jpeg, mime_type="image/jpeg")]
    )
    distinct_output_alias["output_image"] = _image_content(
        png,
        mime_type="image/png",
    )

    invalid_cases = (
        (
            _interaction_response([_image_content(png, mime_type="image/jpeg")]),
            "GEMINI_IMAGE_V2_INLINE_JPEG_BYTES_REQUIRED",
        ),
        (
            _interaction_response([_image_content(jpeg, mime_type="image/png")]),
            "GEMINI_IMAGE_V2_INLINE_JPEG_MIME_REQUIRED",
        ),
        (
            _interaction_response(
                [
                    {
                        "type": "image",
                        "data": base64.b64encode(jpeg).decode("ascii"),
                        "uri": None,
                    }
                ]
            ),
            "GEMINI_IMAGE_V2_INLINE_JPEG_MIME_REQUIRED",
        ),
        (
            _interaction_response(
                [_image_content(jpeg, mime_type="image/jpeg", uri="")]
            ),
            "GEMINI_IMAGE_REAL_INLINE_DELIVERY_REQUIRED",
        ),
        (
            _interaction_response(
                [
                    _image_content(jpeg, mime_type="image/jpeg"),
                    _image_content(jpeg, mime_type="image/jpeg"),
                ]
            ),
            "GEMINI_IMAGE_REAL_OUTPUT_COUNT_NOT_ONE",
        ),
        (
            distinct_output_alias,
            "GEMINI_IMAGE_REAL_OUTPUT_COUNT_NOT_ONE",
        ),
    )
    for response, reason_code in invalid_cases:
        with pytest.raises(GeminiImageResponseSafetyError, match=reason_code):
            adapter._parse_real_response(
                request,
                response,
                submitted_at=submitted_at,
            )


def test_v2_strict_response_enforces_2k_16_9_source_suitable_for_1080p(
    tmp_path: Path,
) -> None:
    request = _v2_request()
    adapter = GoogleGeminiImageAdapter(_settings())
    submitted_at = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    undersized = _valid_fixture_jpeg(
        tmp_path,
        width=1280,
        height=720,
        name="v2-undersized",
    )
    wrong_aspect = _valid_fixture_jpeg(
        tmp_path,
        width=1920,
        height=1200,
        name="v2-wrong-aspect",
    )

    for jpeg, reason_code in (
        (undersized, "GEMINI_IMAGE_V2_2K_SOURCE_BELOW_1080P"),
        (wrong_aspect, "GEMINI_IMAGE_V2_SOURCE_ASPECT_RATIO_MISMATCH"),
    ):
        with pytest.raises(GeminiImageResponseSafetyError, match=reason_code):
            adapter._parse_real_response(
                request,
                _interaction_response(
                    [_image_content(jpeg, mime_type="image/jpeg")]
                ),
                submitted_at=submitted_at,
            )


def test_sdk_retry_control_fails_closed_when_generated_resource_changes() -> None:
    class _InteractionsWithoutRetryAuthority:
        sdk_configuration = object()

    with pytest.raises(
        RuntimeError,
        match="GEMINI_IMAGE_SDK_RETRY_CONTROL_UNAVAILABLE",
    ):
        GoogleGeminiImageAdapter._disable_interactions_retries(
            _InteractionsWithoutRetryAuthority()
        )


def test_real_adapter_accepts_valid_jpeg_with_full_safe_decode(
    tmp_path: Path,
) -> None:
    request = _request()
    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)
    jpeg = _valid_fixture_jpeg(tmp_path)
    fake = _FakeInteractions(
        response=_interaction_response(
            [_image_content(jpeg, mime_type="image/jpeg")]
        )
    )
    adapter = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    )

    receipt = adapter.submit_generation(
        request,
        gates=gates,
        preflight=runtime.preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=runtime.store,
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )

    assert receipt.normalized_status == "SUCCEEDED"
    assert len(fake.calls) == 1
    assert runtime.store.load().status == "SUCCEEDED"
    assert adapter.materialization_receipt_for(receipt)["image_format"] == "JPEG"


def test_real_adapter_blocks_before_attempt_when_jpeg_decoder_is_unavailable(
    tmp_path: Path,
) -> None:
    request = _request()
    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)
    fake = _FakeInteractions(
        response=_interaction_response(
            [_image_content(build_fixture_png(width=1920, height=1080))]
        )
    )
    adapter = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
        raster_decoder_path=str(tmp_path / "missing-ffmpeg"),
    )

    receipt = adapter.submit_generation(
        request,
        gates=gates,
        preflight=runtime.preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=runtime.store,
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )

    assert receipt.normalized_status == "PLANNED"
    assert receipt.provider_status == "OUTPUT_DECODER_BLOCKED"
    assert receipt.provider_error_code == "GEMINI_IMAGE_JPEG_SAFE_DECODER_UNAVAILABLE"
    assert receipt.provider_call_made is False
    assert fake.calls == []
    assert runtime.store.load().status == "PLANNED"
    assert runtime.store.load().attempts_consumed == 0


def test_real_adapter_rejects_read_only_legacy_preflight_without_decoder_evidence(
    tmp_path: Path,
) -> None:
    request = _request()
    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)
    legacy_payload = runtime.preflight.model_dump(
        mode="json",
        exclude={"content_hash"},
    )
    legacy_payload.pop("raster_decoder_ready")
    for optional_v2_field in (
        "serialized_request_contract_passed",
        "v2_approval_binding_passed",
        "drive_readiness_passed",
    ):
        legacy_payload.pop(optional_v2_field, None)
    legacy_payload["evidence_refs"].pop("raster_decoder_readiness")
    legacy = IMGCanaryPreflightEvidence(
        **legacy_payload,
        content_hash=ai_image_stable_hash(legacy_payload),
    )
    runtime.preflight_path.write_text(
        legacy.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    fake = _FakeInteractions(
        response=_interaction_response(
            [_image_content(build_fixture_png(width=1920, height=1080))]
        )
    )

    receipt = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    ).submit_generation(
        request,
        gates=gates,
        preflight=legacy,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=runtime.store,
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )

    assert legacy.status == "PASS"
    assert legacy.raster_decoder_ready is None
    assert legacy.content_hash == ai_image_stable_hash(legacy.content_hash_payload())
    assert receipt.provider_status == "PREFLIGHT_BLOCKED"
    assert receipt.provider_call_made is False
    assert fake.calls == []
    assert runtime.store.load().status == "PLANNED"
    assert runtime.store.load().attempts_consumed == 0


def test_real_adapter_closed_gates_and_disabled_settings_never_touch_transport() -> None:
    request = _request()
    fake = _FakeInteractions(response=_interaction_response([_image_content(build_fixture_png())]))

    gate_blocked = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    ).submit_generation(
        request,
        gates=_gates(request, paid_call_authorization_gate_passed=False),
    )
    assert gate_blocked.provider_status == "GATE_BLOCKED"
    assert gate_blocked.provider_call_made is False
    assert gate_blocked.generation_attempts_consumed == 0

    disabled = GoogleGeminiImageAdapter(
        _settings(gemini_image_real_generation_enabled=False),
        real_client=_FakeRealClient(fake),
    ).submit_generation(request, gates=_gates(request))
    assert disabled.provider_status == "EXECUTION_DISABLED"
    assert disabled.provider_call_made is False
    assert disabled.generation_attempts_consumed == 0
    assert fake.calls == []


def test_real_adapter_consumes_failed_submit_once_and_redacts_exception(
    tmp_path: Path,
) -> None:
    request = _request()
    fake = _FakeInteractions(
        failure=RuntimeError(
            "api_key=provider-secret https://signed.invalid/output?token=secret"
        )
    )
    adapter = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    )

    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)
    preflight = runtime.preflight
    store = runtime.store
    workspace = runtime.workspace
    destination = runtime.destination
    receipt = adapter.submit_generation(
        request,
        gates=gates,
        preflight=preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=store,
        workspace_root=workspace,
        destination_path=destination,
    )
    duplicate = adapter.submit_generation(
        request,
        gates=gates,
        preflight=preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=store,
        workspace_root=workspace,
        destination_path=destination,
    )

    assert duplicate.state_hash == receipt.state_hash
    assert len(fake.calls) == 1
    assert receipt.normalized_status == "FAILED"
    assert receipt.provider_status == "NATIVE_SUBMIT_FAILED"
    assert receipt.provider_error_code == "GEMINI_IMAGE_PROVIDER_RUNTIME_ERROR"
    assert receipt.provider_call_made is True
    assert receipt.generation_attempts_consumed == 1
    assert receipt.fallback_provider_key is None
    assert receipt.external_provider_fallback_used is False
    durable = json.dumps(
        {
            "receipt": receipt.model_dump(mode="json"),
            "summary": adapter.provider_response_summary_for(receipt),
        },
        sort_keys=True,
    )
    assert "provider-secret" not in durable
    assert "signed.invalid" not in durable
    assert "token=secret" not in durable
    with pytest.raises(ValueError, match="GEMINI_IMAGE_TRANSIENT_OUTPUT_NOT_AVAILABLE"):
        adapter.transient_output_for(receipt)


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing", "GEMINI_IMAGE_REAL_OUTPUT_COUNT_NOT_ONE"),
        ("multiple", "GEMINI_IMAGE_REAL_OUTPUT_COUNT_NOT_ONE"),
        ("uri", "GEMINI_IMAGE_REAL_INLINE_DELIVERY_REQUIRED"),
        ("invalid_base64", "GEMINI_IMAGE_REAL_INLINE_DATA_INVALID"),
    ],
)
def test_real_adapter_rejects_unsafe_output_without_a_second_submission(
    case: str,
    expected_code: str,
    tmp_path: Path,
) -> None:
    request = _request()
    png = build_fixture_png(width=1920, height=1080)
    image = _image_content(png)
    if case == "missing":
        images: list[dict[str, Any]] = []
    elif case == "multiple":
        images = [image, dict(image)]
    elif case == "uri":
        images = [
            _image_content(
                png,
                uri="https://signed.invalid/output.png?token=must-not-persist",
            )
        ]
    else:
        images = [{**image, "data": "not-valid-base64!"}]
    fake = _FakeInteractions(response=_interaction_response(images))
    adapter = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    )

    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)
    preflight = runtime.preflight
    store = runtime.store
    workspace = runtime.workspace
    destination = runtime.destination
    receipt = adapter.submit_generation(
        request,
        gates=gates,
        preflight=preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=store,
        workspace_root=workspace,
        destination_path=destination,
    )
    duplicate = adapter.submit_generation(
        request,
        gates=gates,
        preflight=preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=store,
        workspace_root=workspace,
        destination_path=destination,
    )

    assert duplicate.state_hash == receipt.state_hash
    assert len(fake.calls) == 1
    assert receipt.normalized_status == "OUTPUT_MISSING"
    assert receipt.provider_error_code == expected_code
    assert receipt.provider_call_made is True
    assert receipt.generation_attempts_consumed == 1
    assert receipt.fallback_provider_key is None
    assert receipt.external_provider_fallback_used is False
    durable = json.dumps(
        {
            "receipt": receipt.model_dump(mode="json"),
            "summary": adapter.provider_response_summary_for(receipt),
        },
        sort_keys=True,
    )
    assert "signed.invalid" not in durable
    assert "must-not-persist" not in durable


def test_real_adapter_atomically_claims_one_submit_across_concurrent_duplicates(
    tmp_path: Path,
) -> None:
    request = _request()
    entered = threading.Event()
    release = threading.Event()
    fake = _FakeInteractions(
        response=_interaction_response(
            [_image_content(build_fixture_png(width=1920, height=1080))]
        ),
        entered=entered,
        release=release,
    )
    adapter = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    )

    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)
    preflight = runtime.preflight
    store = runtime.store
    workspace = runtime.workspace
    destination = runtime.destination
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            adapter.submit_generation,
            request,
            gates=gates,
            preflight=preflight,
            preflight_path=runtime.preflight_path,
            execution_gates_path=runtime.gates_path,
            attempt_store=store,
            workspace_root=workspace,
            destination_path=destination,
        )
        assert entered.wait(timeout=5)
        concurrent_duplicate = adapter.submit_generation(
            request,
            gates=gates,
            preflight=preflight,
            preflight_path=runtime.preflight_path,
            execution_gates_path=runtime.gates_path,
            attempt_store=store,
            workspace_root=workspace,
            destination_path=destination,
        )
        assert concurrent_duplicate.normalized_status == "SUBMITTED"
        assert concurrent_duplicate.generation_attempts_consumed == 1
        release.set()
        completed = first.result(timeout=5)

    assert completed.normalized_status == "SUCCEEDED"
    assert len(fake.calls) == 1
    assert adapter.submit_generation(
        request,
        gates=gates,
        preflight=preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=store,
        workspace_root=workspace,
        destination_path=destination,
    ).state_hash == completed.state_hash


def test_real_adapter_requires_hash_bound_pass_preflight_and_durable_ledger(
    tmp_path: Path,
) -> None:
    request = _request()
    gates = _gates(request)
    fake = _FakeInteractions(
        response=_interaction_response([_image_content(build_fixture_png())])
    )
    adapter = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    )

    missing_preflight = adapter.submit_generation(request, gates=gates)
    assert missing_preflight.provider_status == "PREFLIGHT_BLOCKED"
    assert missing_preflight.provider_call_made is False

    runtime = _runtime_context(tmp_path, request, gates)
    preflight = runtime.preflight
    missing_ledger = adapter.submit_generation(
        request,
        gates=gates,
        preflight=preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )
    assert missing_ledger.provider_status == "DURABLE_ATTEMPT_LEDGER_BLOCKED"
    assert fake.calls == []

    tampered = preflight.model_copy(
        update={"monthly_budget_passed": False},
    )
    blocked = adapter.submit_generation(
        request,
        gates=gates,
        preflight=tampered,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=runtime.store,
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )
    assert blocked.provider_status == "PREFLIGHT_BLOCKED"
    assert fake.calls == []


def test_direct_adapter_rejects_persisted_planning_preflight_without_transport(
    tmp_path: Path,
) -> None:
    request = _request()
    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)
    runtime.preflight_path.write_text(
        runtime.planning_preflight.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    fake = _FakeInteractions(
        response=_interaction_response([_image_content(build_fixture_png())])
    )

    receipt = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    ).submit_generation(
        request,
        gates=gates,
        preflight=runtime.planning_preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=runtime.store,
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )

    assert receipt.provider_status == "PREFLIGHT_BLOCKED"
    assert receipt.provider_call_made is False
    assert runtime.store.load().attempts_consumed == 0
    assert fake.calls == []


def test_direct_adapter_rejects_cloned_attempt_ledger_path_without_transport(
    tmp_path: Path,
) -> None:
    request = _request()
    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)
    clone_path = runtime.workspace / "manifests" / "attempt-ledger-clone.json"
    clone_path.write_bytes(runtime.store.path.read_bytes())
    cloned_store = IMGCanaryAttemptLedgerStore(clone_path)
    fake = _FakeInteractions(
        response=_interaction_response([_image_content(build_fixture_png())])
    )

    receipt = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    ).submit_generation(
        request,
        gates=gates,
        preflight=runtime.preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=cloned_store,
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )

    assert receipt.provider_status == "PREFLIGHT_BLOCKED"
    assert receipt.provider_error_code == (
        "GEMINI_IMAGE_CANONICAL_RUNTIME_AUTHORITY_REQUIRED"
    )
    assert cloned_store.load().attempts_consumed == 0
    assert fake.calls == []


def test_direct_adapter_reopens_canonical_store_instead_of_trusting_spoofed_methods(
    tmp_path: Path,
) -> None:
    request = _request()
    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)

    class SpoofedStore:
        def __init__(self, path: Path) -> None:
            self.path = path

        def load(self) -> Any:
            raise AssertionError("caller-supplied load must never be trusted")

        def consume_at_submit(self, **_: Any) -> Any:
            raise AssertionError("caller-supplied consume must never be trusted")

        def finalize(self, **_: Any) -> Any:
            raise AssertionError("caller-supplied finalize must never be trusted")

    fake = _FakeInteractions(
        response=_interaction_response([_image_content(build_fixture_png())])
    )
    first = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    ).submit_generation(
        request,
        gates=gates,
        preflight=runtime.preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=SpoofedStore(runtime.store.path),
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )
    runtime.destination.unlink()
    replay = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    ).submit_generation(
        request,
        gates=gates,
        preflight=runtime.preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=SpoofedStore(runtime.store.path),
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )

    assert first.normalized_status == "SUCCEEDED"
    assert replay.provider_status == "DURABLE_ATTEMPT_LEDGER_BLOCKED"
    assert replay.provider_call_made is False
    assert len(fake.calls) == 1
    assert runtime.store.load().status == "SUCCEEDED"
    assert runtime.store.load().attempts_consumed == 1


def test_durable_ledger_blocks_replay_across_fresh_adapter_instances(
    tmp_path: Path,
) -> None:
    request = _request()
    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)
    preflight = runtime.preflight
    store = runtime.store
    fake = _FakeInteractions(
        response=_interaction_response(
            [_image_content(build_fixture_png(width=1920, height=1080))]
        )
    )

    first = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    ).submit_generation(
        request,
        gates=gates,
        preflight=preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=store,
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )
    runtime.destination.unlink()
    replay = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    ).submit_generation(
        request,
        gates=gates,
        preflight=preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=IMGCanaryAttemptLedgerStore(store.path),
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )

    assert first.normalized_status == "SUCCEEDED"
    assert replay.provider_status == "DURABLE_ATTEMPT_LEDGER_BLOCKED"
    assert replay.provider_call_made is False
    assert len(fake.calls) == 1
    assert store.load().status == "SUCCEEDED"
    assert store.load().attempts_consumed == 1


def test_durable_attempt_claim_is_atomic_across_store_instances(
    tmp_path: Path,
) -> None:
    request = _request()
    runtime = _runtime_context(tmp_path, request, _gates(request))
    store = runtime.store
    second = IMGCanaryAttemptLedgerStore(store.path)
    fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(request)

    def claim(candidate: IMGCanaryAttemptLedgerStore) -> str:
        try:
            return candidate.consume_at_submit(
                expected_fingerprint=fingerprint,
                now=datetime(2026, 7, 18, 0, 0, 1, tzinfo=UTC),
            ).status
        except PermissionError:
            return "BLOCKED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, (store, second)))

    assert sorted(results) == ["BLOCKED", "EXECUTING"]
    ledger = store.load()
    assert ledger.attempts_consumed == 1
    assert ledger.provider_call_made is True


def test_real_adapter_rejects_structural_but_undecodable_jpeg(
    tmp_path: Path,
) -> None:
    request = _request()
    gates = _gates(request)
    runtime = _runtime_context(tmp_path, request, gates)
    preflight = runtime.preflight
    store = runtime.store
    sof_payload = (
        b"\x08"
        + (1080).to_bytes(2, "big")
        + (1920).to_bytes(2, "big")
        + b"\x01\x01\x11\x00"
    )
    corrupt_jpeg = (
        b"\xff\xd8"
        + b"\xff\xc0"
        + (11).to_bytes(2, "big")
        + sof_payload
        + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
        + b"not-jpeg-entropy"
        + b"\xff\xd9"
    )
    fake = _FakeInteractions(
        response=_interaction_response(
            [_image_content(corrupt_jpeg, mime_type="image/jpeg")]
        )
    )
    adapter = GoogleGeminiImageAdapter(
        _settings(),
        real_client=_FakeRealClient(fake),
    )

    receipt = adapter.submit_generation(
        request,
        gates=gates,
        preflight=preflight,
        preflight_path=runtime.preflight_path,
        execution_gates_path=runtime.gates_path,
        attempt_store=store,
        workspace_root=runtime.workspace,
        destination_path=runtime.destination,
    )

    assert receipt.normalized_status == "OUTPUT_MISSING"
    assert receipt.provider_error_code in {
        "GEMINI_IMAGE_JPEG_SAFE_DECODE_FAILED",
        "GEMINI_IMAGE_JPEG_SAFE_DECODER_UNAVAILABLE",
    }
    assert len(fake.calls) == 1
    assert store.load().status == "BLOCKED_REQUIRES_NEW_APPROVAL"
    assert store.load().attempts_consumed == 1


def test_official_google_genai_interactions_resource_is_forced_to_no_retry() -> None:
    pytest.importorskip("google.genai")
    adapter = GoogleGeminiImageAdapter(_settings())

    client = adapter._build_official_real_client()
    try:
        retry_config = client.interactions.sdk_configuration.retry_config  # type: ignore[attr-defined]
        assert retry_config.strategy == "none"
        assert retry_config.retry_connection_errors is False
        assert retry_config.max_retries == 0
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
