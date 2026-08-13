from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.ai_visual_production import (
    AIVisualScenePlan,
    CompiledAIImagePrompt,
    VideoVisualStyleBible,
    ai_visual_stable_hash,
    ai_visual_text_hash,
)
from app.core.config import Settings
from app.db.models.ai_visual import AIVisualAssetEffect
from app.services.google_gemini_image_catalog import (
    GoogleGeminiImageModelPriceCatalog,
)
from app.services.v2_ai_visual_provider import (
    V2AIImageEffectState,
    V2AIImageExecutionBlocked,
    V2AIImageFailureReceipt,
    V2AIImageProductionService,
    V2AIImageRecordTransitions,
    V2AIImageSafeResponseCapture,
    V2AIImageSceneEffectIdentity,
    V2AIImageSceneEffectRecord,
    V2AIImageOutcomeUncertain,
    V2GeminiImageProductionAdapter,
    _serialized_provider_request,
)


def _hash(value: Any) -> str:
    return ai_image_stable_hash(value)


def test_pinned_official_sdk_serializes_current_multimodal_response_format() -> None:
    """No-network canary for the May-2026 Interactions request schema."""

    httpx = pytest.importorskip("httpx")
    genai = pytest.importorskip("google.genai")
    sdk_types = pytest.importorskip("google.genai.types")
    captured: list[dict[str, Any]] = []

    def _capture(request: Any) -> Any:
        captured.append(json.loads(request.content))
        return httpx.Response(
            400,
            request=request,
            json={
                "error": {
                    "code": 400,
                    "message": "expected no-network test stop",
                    "status": "INVALID_ARGUMENT",
                }
            },
        )

    transport_client = httpx.Client(transport=httpx.MockTransport(_capture))
    client = genai.Client(
        api_key="unit-test-placeholder",
        http_options=sdk_types.HttpOptions(
            httpx_client=transport_client,
            retry_options=sdk_types.HttpRetryOptions(attempts=1),
        ),
    )
    request_body = _serialized_provider_request(
        model_id="gemini-3.1-flash-image",
        scene_id="sdk-serialization-canary",
        required_semantic_anchors=("anchor-a", "anchor-b", "anchor-c", "anchor-d"),
        prompt="Generate the governed visual.",
        negative_prompt="No text or presentation-card layout.",
        image_size="2K",
        aspect_ratio="16:9",
    )
    try:
        with pytest.raises(Exception, match="expected no-network test stop"):
            client.interactions.create(**request_body)
    finally:
        client.close()
        transport_client.close()

    assert len(captured) == 1
    wire_body = captured[0]
    assert wire_body["response_format"] == request_body["response_format"]
    assert [item["type"] for item in wire_body["response_format"]] == [
        "text",
        "image",
    ]
    assert wire_body["response_format"][0] == {
        "type": "text",
        "mime_type": "text/plain",
    }
    assert "response_modalities" not in wire_body
    assert "response_mime_type" not in wire_body
    assert "api_key" not in json.dumps(wire_body).lower()


class MemoryEffectStore:
    ready = True

    def __init__(self) -> None:
        self.records: dict[str, V2AIImageSceneEffectRecord] = {}
        self.transitions: list[str] = []

    def load(self, *, effect_id: str) -> V2AIImageSceneEffectRecord | None:
        return self.records.get(effect_id)

    def prepare(
        self, *, identity: V2AIImageSceneEffectIdentity, prepared_at: datetime
    ) -> V2AIImageSceneEffectRecord:
        existing = self.records.get(identity.effect_id)
        if existing is not None:
            return existing
        record = V2AIImageRecordTransitions.prepared(identity, now=prepared_at)
        self.records[identity.effect_id] = record
        self.transitions.append(record.state.value)
        return record

    def _cas(
        self, *, effect_id: str, expected_revision: int, expected_record_hash: str
    ) -> V2AIImageSceneEffectRecord:
        record = self.records[effect_id]
        assert record.revision == expected_revision
        assert record.record_hash == expected_record_hash
        return record

    def claim_submitting(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        submission_owner_token_hash: str,
        submitted_at: datetime,
        lease_expires_at: datetime,
    ) -> V2AIImageSceneEffectRecord:
        record = self._cas(
            effect_id=effect_id,
            expected_revision=expected_revision,
            expected_record_hash=expected_record_hash,
        )
        result = V2AIImageRecordTransitions.submitting(
            record,
            owner_token_hash=submission_owner_token_hash,
            submitted_at=submitted_at,
            lease_expires_at=lease_expires_at,
        )
        self.records[effect_id] = result
        self.transitions.append(result.state.value)
        return result

    def record_response_captured(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        submission_owner_token_hash: str,
        capture: V2AIImageSafeResponseCapture,
        response_journal_hash: str,
    ) -> V2AIImageSceneEffectRecord:
        record = self._cas(
            effect_id=effect_id,
            expected_revision=expected_revision,
            expected_record_hash=expected_record_hash,
        )
        assert record.submission_owner_token_hash == submission_owner_token_hash
        # This is the critical ordering assertion: bytes and redacted journal
        # are durable before the DB moves to RESPONSE_CAPTURED or QC starts.
        assert Path(capture.response_capture_journal_path).is_file()
        if capture.response_capture_path:
            assert Path(capture.response_capture_path).is_file()
        assert (
            hashlib.sha256(
                Path(capture.response_capture_journal_path).read_bytes()
            ).hexdigest()
            == response_journal_hash
        )
        result = V2AIImageRecordTransitions.response_captured(
            record,
            capture=capture,
            response_journal_hash=response_journal_hash,
        )
        self.records[effect_id] = result
        self.transitions.append(result.state.value)
        return result

    def mark_verified(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        receipt: Any,
        completed_at: datetime,
    ) -> V2AIImageSceneEffectRecord:
        record = self._cas(
            effect_id=effect_id,
            expected_revision=expected_revision,
            expected_record_hash=expected_record_hash,
        )
        result = V2AIImageRecordTransitions.verified(
            record, receipt=receipt, completed_at=completed_at
        )
        self.records[effect_id] = result
        self.transitions.append(result.state.value)
        return result

    def _failed(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        failure: V2AIImageFailureReceipt,
    ) -> V2AIImageSceneEffectRecord:
        record = self._cas(
            effect_id=effect_id,
            expected_revision=expected_revision,
            expected_record_hash=expected_record_hash,
        )
        result = V2AIImageRecordTransitions.failed(record, failure=failure)
        self.records[effect_id] = result
        self.transitions.append(result.state.value)
        return result

    def mark_failed_definitive(self, **kwargs: Any) -> V2AIImageSceneEffectRecord:
        return self._failed(**kwargs)

    def mark_failed_uncertain(self, **kwargs: Any) -> V2AIImageSceneEffectRecord:
        return self._failed(**kwargs)


class CaptureAckLostStore(MemoryEffectStore):
    def __init__(self) -> None:
        super().__init__()
        self.lose_capture_ack = True

    def record_response_captured(self, **kwargs: Any) -> V2AIImageSceneEffectRecord:
        result = super().record_response_captured(**kwargs)
        if self.lose_capture_ack:
            self.lose_capture_ack = False
            raise ConnectionError("simulated lost DB acknowledgement")
        return result


class VerifyAckLostStore(MemoryEffectStore):
    def __init__(self) -> None:
        super().__init__()
        self.lose_verify_ack = True

    def mark_verified(self, **kwargs: Any) -> V2AIImageSceneEffectRecord:
        result = super().mark_verified(**kwargs)
        if self.lose_verify_ack:
            self.lose_verify_ack = False
            raise ConnectionError("simulated lost DB acknowledgement")
        return result


class FakeInteractions:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response
        self.error = error
        self.sdk_configuration = SimpleNamespace(
            retry_config=SimpleNamespace(
                strategy="exponential",
                retry_connection_errors=True,
                max_retries=4,
            )
        )

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, interactions: FakeInteractions) -> None:
        self.interactions = interactions


class DefinitiveProviderError(RuntimeError):
    status_code = 400


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        gemini_api_key=SecretStr("fake-test-key"),
        gemini_image_real_generation_enabled=True,
        img1_fixture_only=False,
        gemini_image_provider_route_approved=True,
        provider_real_execution_enabled=True,
        provider_production_execution_enabled=True,
        media_provider_calls_disabled=False,
        extra_ai_image_monthly_budget_usd=Decimal("20.00"),
    )


def _jpeg(*, black: bool = False, width: int = 1920, height: int = 1080) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is required by the production JPEG QC contract")
    source = (
        f"color=c=black:s={width}x{height}:r=1"
        if black
        else f"testsrc2=s={width}x{height}:r=1"
    )
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            source,
            "-frames:v",
            "1",
            "-c:v",
            "mjpeg",
            "-f",
            "image2pipe",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=30,
    )
    return completed.stdout


def _response(
    image_bytes: bytes,
    *,
    semantic_match: bool = True,
    mismatch_reasons: tuple[str, ...] = (),
    forbidden_content: tuple[str, ...] = (),
    include_semantic_output: bool = True,
    semantic_text: str | None = None,
) -> dict[str, Any]:
    if not semantic_match and not mismatch_reasons:
        mismatch_reasons = ("The generated asset does not match the scene intent.",)
    observed = {
        "schema_version": "vcos.gemini-image-observed-output.v1",
        "scene_id": "scene-01",
        "description_is_of_generated_output": True,
        "observed_output_summary": (
            "An asymmetric cinematic workflow transformation rendered with "
            "depth, coherent lighting, and no visible writing."
        ),
        "observed_primary_subjects": ["workflow transformation", "data forms"],
        "observed_action_or_relation": (
            "Messy inputs resolve into one coherent validated structure."
        ),
        "observed_environment": "Abstract editorial technical space.",
        "observed_semantic_anchors": [
            "core_subject:coherent workflow",
            "action_or_relation:messy inputs resolve into validated structure",
            "environment:abstract editorial technical space",
            "visual_goal:make the transformation immediately understandable",
        ],
        "semantic_match": semantic_match,
        "semantic_mismatch_reasons": list(mismatch_reasons),
        "forbidden_content_detected": list(forbidden_content),
    }
    content: list[dict[str, Any]] = []
    if include_semantic_output:
        content.append(
            {
                "type": "text",
                "text": semantic_text
                if semantic_text is not None
                else json.dumps(observed, sort_keys=True),
            }
        )
    content.append(
        {
            "type": "image",
            "mime_type": "image/jpeg",
            "uri": None,
            "data": base64.b64encode(image_bytes).decode("ascii"),
        }
    )
    return {
        "id": "interactions/v2-production-test-1",
        "status": "completed",
        "usage": {"total_tokens": 17},
        "steps": [
            {
                "type": "model_output",
                "content": content,
            }
        ],
    }


def _identity(
    root: Path,
    *,
    effect_id: str = "image-effect-1",
    catalog: GoogleGeminiImageModelPriceCatalog | None = None,
) -> V2AIImageSceneEffectIdentity:
    price_catalog = catalog or GoogleGeminiImageModelPriceCatalog()
    return V2AIImageSceneEffectIdentity.seal(
        effect_id=effect_id,
        visual_production_run_id="visual-production-run-1",
        scene_plan_snapshot_id="scene-plan-snapshot-1",
        workflow_run_id="workflow-run-1",
        video_project_id="video-project-1",
        production_package_artifact_version_id="package-version-1",
        production_package_hash=_hash("package"),
        scene_id="scene-01",
        ordinal=1,
        primary_asset_slot_id="asset-slot-01",
        bound_scene_ids=("scene-01",),
        bound_scene_plan_hashes=(_hash("scene-plan"),),
        primary_asset_owner_scene_id="scene-01",
        production_visual_policy_hash=_hash("ai-only-policy"),
        style_bible_ref="ai-visual-style-bible://style-1",
        style_bible_hash=_hash("style-bible"),
        scene_plan_ref="ai-visual-scene-plan://scene-01",
        scene_plan_hash=_hash("scene-plan"),
        compiled_prompt_ref="ai-visual-compiled-image-prompt://scene-01",
        compiled_prompt_hash=_hash("compiled-prompt"),
        compiled_prompt_content_hash=_hash("compiled-prompt"),
        prompt_compiler_version="vcos.ai-image-prompt-compiler.v1",
        prompt=(
            "Cinematic editorial technical visualization of a coherent workflow, "
            "rich depth, asymmetric composition, no visible writing."
        ),
        negative_prompt=(
            "no presentation slide, no PowerPoint, no three-box flowchart, "
            "no generic infographic card, no text-heavy composition, "
            "no fake dashboard, no fake product UI, no floating random labels, "
            "no visible generated text, no logo, no watermark"
        ),
        prompt_hash=hashlib.sha256(
            (
                "Cinematic editorial technical visualization of a coherent workflow, "
                "rich depth, asymmetric composition, no visible writing."
            ).encode("utf-8")
        ).hexdigest(),
        required_semantic_anchors=(
            "core_subject:coherent workflow",
            "action_or_relation:messy inputs resolve into validated structure",
            "environment:abstract editorial technical space",
            "visual_goal:make the transformation immediately understandable",
        ),
        price_catalog_version=price_catalog.version,
        price_catalog_ref=price_catalog.ref,
        price_catalog_hash=_hash(price_catalog.payload),
        approval_ref="ai-visual-approval://approval-1",
        approval_hash=_hash("approval"),
        budget_reservation_id="budget-reservation-1",
        budget_authority_ref="mr1-budget://reservation-1",
        budget_authority_hash=_hash("budget"),
        cost_estimate_ref="ai-image-cost://estimate-1",
        cost_estimate_hash=_hash("estimate"),
        estimated_cost_usd=Decimal("0.111"),
        maximum_cost_usd=Decimal("1.00"),
        idempotency_key=f"ai-image:{effect_id}",
        workspace_root=str(root),
        request_journal_path=str(root / effect_id / "request.json"),
        response_capture_path=str(root / effect_id / "response-capture.jpg"),
        response_capture_journal_path=str(root / effect_id / "response-capture.json"),
        destination_path=str(root / effect_id / "generated.jpg"),
    )


def _service(
    *,
    store: MemoryEffectStore,
    interactions: FakeInteractions,
) -> V2AIImageProductionService:
    adapter = V2GeminiImageProductionAdapter(client=FakeClient(interactions))
    retry = interactions.sdk_configuration.retry_config
    assert (retry.strategy, retry.retry_connection_errors, retry.max_retries) == (
        "none",
        False,
        0,
    )
    return V2AIImageProductionService(
        store=store,
        adapter=adapter,
        settings=_settings(),
        adapter_registered=True,
    )


def test_success_is_prepared_captured_qced_atomic_and_idempotent(
    tmp_path: Path,
) -> None:
    store = MemoryEffectStore()
    interactions = FakeInteractions(response=_response(_jpeg()))
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)

    prepared = service.prepare(identity)
    assert prepared.state == V2AIImageEffectState.PREPARED
    assert prepared.provider_call_count == 0
    assert Path(identity.request_journal_path).is_file()

    verified = service.execute(effect_id=identity.effect_id)
    assert verified.state == V2AIImageEffectState.VERIFIED
    assert verified.provider_call_count == 1
    assert verified.asset_receipt is not None
    assert verified.asset_receipt.production_eligible is True
    assert verified.asset_receipt.fallback_provider_key is None
    assert verified.asset_receipt.technical_qc.verdict == "PASS"
    assert len(verified.asset_receipt.technical_qc.perceptual_hash) == 16
    assert verified.asset_receipt.semantic_attestation.semantic_match is True
    assert (
        verified.asset_receipt.semantic_attestation.asset_checksum
        == verified.asset_receipt.checksum_sha256
    )
    assert (
        verified.asset_receipt.semantic_attestation.attestation_source
        == "SAME_INTERACTION_MODEL_OUTPUT"
    )
    assert (
        verified.asset_receipt.semantic_attestation.independent_multimodal_inspection_performed
        is False
    )
    assert verified.asset_receipt.semantic_attestation.human_semantic_review_required
    assert verified.asset_receipt.provider_config_hash == identity.provider_config_hash
    assert (
        verified.asset_receipt.generation_policy_hash == identity.generation_policy_hash
    )
    assert verified.asset_receipt.actual_cost_usd is None
    assert (
        verified.asset_receipt.conservative_settlement_cost_usd
        == identity.estimated_cost_usd
    )
    evidence = verified.db_evidence_projection
    assert evidence["state"] == "VERIFIED"
    assert evidence["provider_operation_id"] is None
    assert evidence["request_journal_hash"] == identity.request_journal_hash
    assert evidence["sanitized_response_hash"] == verified.response_capture.capture_hash
    assert evidence["output_checksum"] == verified.asset_receipt.checksum_sha256
    assert evidence["qc_hash"] == verified.asset_receipt.technical_qc.qc_hash
    assert evidence["actual_cost_usd"] is None
    assert evidence["cost_settlement_basis"] == "CONSERVATIVE_CATALOG_ESTIMATE_VERIFIED"
    assert evidence["qc_evidence"]["record"]["record_hash"] == verified.record_hash
    assert Path(identity.destination_path).is_file()
    assert not Path(identity.response_capture_path).exists()
    assert store.transitions == [
        "PREPARED",
        "SUBMITTING",
        "RESPONSE_CAPTURED",
        "VERIFIED",
    ]
    assert len(interactions.calls) == 1
    call = interactions.calls[0]
    assert call["model"] == "gemini-3.1-flash-image"
    assert len(call["response_format"]) == 2
    text_format, image_format = call["response_format"]
    assert text_format == {"type": "text", "mime_type": "text/plain"}
    assert image_format == {
        "type": "image",
        "mime_type": "image/jpeg",
        "aspect_ratio": "16:9",
        "image_size": "2K",
    }
    assert call["generation_config"] == {
        "max_output_tokens": 3000,
        "thinking_level": "minimal",
    }
    assert call["store"] is False and call["stream"] is False
    assert "idempotency" not in call

    response_journal = Path(identity.response_capture_journal_path).read_text()
    response_projection = json.loads(response_journal)
    assert response_projection["base64_image_data_persisted"] is False
    assert (
        base64.b64encode(Path(identity.destination_path).read_bytes()).decode()
        not in response_journal
    )
    again = service.execute(effect_id=identity.effect_id)
    assert again.record_hash == verified.record_hash
    assert service.prepare(identity).record_hash == verified.record_hash
    assert len(interactions.calls) == 1


def test_ambiguous_submit_is_failed_uncertain_and_never_retried(
    tmp_path: Path,
) -> None:
    store = MemoryEffectStore()
    interactions = FakeInteractions(error=TimeoutError("secret provider detail"))
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)
    service.prepare(identity)

    failed = service.execute(effect_id=identity.effect_id)
    assert failed.state == V2AIImageEffectState.FAILED_UNCERTAIN
    assert failed.failure_receipt is not None
    assert failed.failure_receipt.retry_allowed is False
    assert failed.failure_receipt.fallback_allowed is False
    assert "secret" not in failed.failure_receipt.model_dump_json()
    assert (
        service.execute(effect_id=identity.effect_id).record_hash == failed.record_hash
    )
    assert len(interactions.calls) == 1


def test_known_http_rejection_is_definitive_but_still_not_retried(
    tmp_path: Path,
) -> None:
    store = MemoryEffectStore()
    interactions = FakeInteractions(error=DefinitiveProviderError("unsafe"))
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)
    service.prepare(identity)

    failed = service.execute(effect_id=identity.effect_id)
    assert failed.state == V2AIImageEffectState.FAILED_DEFINITIVE
    assert failed.failure_receipt is not None
    assert failed.failure_receipt.reason_code == "V2_AI_IMAGE_PROVIDER_HTTP_400"
    assert failed.retry_allowed is False and failed.fallback_allowed is False
    assert len(interactions.calls) == 1


def test_black_provider_output_is_captured_then_fails_production_qc(
    tmp_path: Path,
) -> None:
    store = MemoryEffectStore()
    interactions = FakeInteractions(response=_response(_jpeg(black=True)))
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)
    service.prepare(identity)

    failed = service.execute(effect_id=identity.effect_id)
    assert failed.state == V2AIImageEffectState.FAILED_DEFINITIVE
    assert failed.response_capture is not None
    assert failed.failure_receipt is not None
    assert failed.failure_receipt.reason_code == "V2_AI_IMAGE_OUTPUT_MOSTLY_BLACK"
    assert Path(identity.response_capture_path).is_file()
    assert not Path(identity.destination_path).exists()
    assert len(interactions.calls) == 1


@pytest.mark.parametrize(
    ("response", "reason_code"),
    [
        (
            lambda: _response(_jpeg(), include_semantic_output=False),
            "V2_AI_IMAGE_SEMANTIC_OUTPUT_COUNT_INVALID",
        ),
        (
            lambda: _response(_jpeg(), semantic_text="not-json"),
            "V2_AI_IMAGE_SEMANTIC_ATTESTATION_INVALID",
        ),
    ],
)
def test_missing_or_malformed_same_interaction_semantics_fail_without_retry(
    tmp_path: Path,
    response: Any,
    reason_code: str,
) -> None:
    store = MemoryEffectStore()
    interactions = FakeInteractions(response=response())
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)
    service.prepare(identity)

    failed = service.execute(effect_id=identity.effect_id)
    assert failed.state == V2AIImageEffectState.FAILED_DEFINITIVE
    assert failed.failure_receipt is not None
    assert failed.failure_receipt.reason_code == reason_code
    assert failed.retry_allowed is False and failed.fallback_allowed is False
    assert service.execute(effect_id=identity.effect_id) == failed
    assert len(interactions.calls) == 1


def test_provider_declared_semantic_mismatch_is_captured_and_blocks_asset(
    tmp_path: Path,
) -> None:
    store = MemoryEffectStore()
    interactions = FakeInteractions(
        response=_response(
            _jpeg(),
            semantic_match=False,
            mismatch_reasons=("The output depicts an unrelated landscape.",),
        )
    )
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)
    service.prepare(identity)

    failed = service.execute(effect_id=identity.effect_id)
    assert failed.state == V2AIImageEffectState.FAILED_DEFINITIVE
    assert failed.response_capture is not None
    assert failed.response_capture.semantic_attestation is not None
    assert failed.response_capture.semantic_attestation.semantic_match is False
    assert failed.failure_receipt is not None
    assert (
        failed.failure_receipt.reason_code == "V2_AI_IMAGE_SEMANTIC_ATTESTATION_BLOCKED"
    )
    assert not Path(identity.destination_path).exists()
    assert service.execute(effect_id=identity.effect_id) == failed
    assert len(interactions.calls) == 1


def test_submitting_reconciles_durable_response_without_second_provider_call(
    tmp_path: Path,
) -> None:
    store = MemoryEffectStore()
    interactions = FakeInteractions(response=None)
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)
    prepared = service.prepare(identity)
    now = datetime.now(UTC)
    owner = _hash("owner")
    submitting = store.claim_submitting(
        effect_id=identity.effect_id,
        expected_revision=prepared.revision,
        expected_record_hash=prepared.record_hash,
        submission_owner_token_hash=owner,
        submitted_at=now,
        lease_expires_at=now + timedelta(minutes=3),
    )
    capture, output = service._capture_response(  # noqa: SLF001 - crash seam
        identity=identity,
        response=_response(_jpeg()),
    )
    service._persist_response_capture(  # noqa: SLF001 - crash seam
        identity=identity,
        capture=capture,
        output_bytes=output,
    )
    assert submitting.state == V2AIImageEffectState.SUBMITTING

    verified = service.execute(effect_id=identity.effect_id)
    assert verified.state == V2AIImageEffectState.VERIFIED
    assert len(interactions.calls) == 0
    assert Path(identity.destination_path).is_file()


def test_lost_db_ack_after_capture_reconciles_without_second_provider_call(
    tmp_path: Path,
) -> None:
    store = CaptureAckLostStore()
    interactions = FakeInteractions(response=_response(_jpeg()))
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)
    service.prepare(identity)

    with pytest.raises(
        V2AIImageOutcomeUncertain,
        match="V2_AI_IMAGE_RESPONSE_CAPTURE_DB_UNCERTAIN",
    ):
        service.execute(effect_id=identity.effect_id)
    assert (
        store.load(effect_id=identity.effect_id).state
        == V2AIImageEffectState.RESPONSE_CAPTURED
    )
    assert len(interactions.calls) == 1

    verified = service.execute(effect_id=identity.effect_id)
    assert verified.state == V2AIImageEffectState.VERIFIED
    assert len(interactions.calls) == 1


def test_lost_db_ack_after_verified_commit_returns_committed_asset(
    tmp_path: Path,
) -> None:
    store = VerifyAckLostStore()
    interactions = FakeInteractions(response=_response(_jpeg()))
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)
    service.prepare(identity)

    verified = service.execute(effect_id=identity.effect_id)
    assert verified.state == V2AIImageEffectState.VERIFIED
    assert verified.asset_receipt is not None
    assert len(interactions.calls) == 1
    assert service.execute(effect_id=identity.effect_id) == verified
    assert len(interactions.calls) == 1


def test_live_submitting_lease_blocks_duplicate_without_mutation(
    tmp_path: Path,
) -> None:
    store = MemoryEffectStore()
    interactions = FakeInteractions(response=None)
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)
    prepared = service.prepare(identity)
    now = datetime.now(UTC)
    submitting = store.claim_submitting(
        effect_id=identity.effect_id,
        expected_revision=prepared.revision,
        expected_record_hash=prepared.record_hash,
        submission_owner_token_hash=_hash("owner"),
        submitted_at=now,
        lease_expires_at=now + timedelta(minutes=3),
    )

    with pytest.raises(
        V2AIImageExecutionBlocked,
        match="V2_AI_IMAGE_SUBMISSION_STILL_IN_FLIGHT",
    ):
        service.execute(effect_id=identity.effect_id)
    assert store.load(effect_id=identity.effect_id) == submitting
    assert len(interactions.calls) == 0


def test_below_1080p_output_is_captured_then_blocked_without_fallback(
    tmp_path: Path,
) -> None:
    store = MemoryEffectStore()
    interactions = FakeInteractions(response=_response(_jpeg(width=1280, height=720)))
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)
    service.prepare(identity)

    failed = service.execute(effect_id=identity.effect_id)
    assert failed.state == V2AIImageEffectState.FAILED_DEFINITIVE
    assert failed.failure_receipt is not None
    assert failed.failure_receipt.reason_code == "V2_AI_IMAGE_RESOLUTION_BELOW_1080P"
    assert failed.retry_allowed is False and failed.fallback_allowed is False
    assert len(interactions.calls) == 1


def test_readiness_is_redacted_and_catalog_drift_blocks_before_submit(
    tmp_path: Path,
) -> None:
    store = MemoryEffectStore()
    interactions = FakeInteractions(response=_response(_jpeg()))
    service = _service(store=store, interactions=interactions)
    identity = _identity(tmp_path)
    readiness = service.readiness_projection(identity)
    assert readiness.execution_ready is True
    dumped = readiness.model_dump_json()
    assert "fake-test-key" not in dumped
    assert readiness.credential_value_exposed is False
    assert readiness.provider_call_made is False
    assert readiness.provider_config_hash == identity.provider_config_hash
    assert readiness.generation_policy_hash == identity.generation_policy_hash
    assert readiness.exact_provider_config_bound is True

    drifted = identity.model_copy(update={"price_catalog_version": "stale"})
    with pytest.raises(V2AIImageExecutionBlocked, match="PRICE_CATALOG_DRIFT"):
        service.prepare(drifted)
    assert len(interactions.calls) == 0


def test_identity_binds_canonical_visual_contract_hashes(tmp_path: Path) -> None:
    style = VideoVisualStyleBible.build(
        style_bible_id="style-1",
        video_project_id="video-project-1",
        package_id="package-version-1",
        overall_visual_language="cinematic technical editorial",
        rendering_style="high fidelity conceptual realism",
        lighting="soft directional",
        contrast="controlled high contrast",
        palette_guidance=["deep navy", "warm amber"],
        materials=["glass", "matte metal"],
        camera_language="restrained asymmetric compositions",
        depth="layered foreground and background",
        technical_illustration_language="conceptual systems without fake UI",
        human_depiction_rules=["natural posture"],
        technology_depiction_rules=["abstract, not a product screenshot"],
        negative_aesthetic_constraints=["no presentation slide"],
        aspect_ratio="16:9",
        visible_generated_text=False,
        fake_product_ui_allowed=False,
    )
    scene_body = {
        "schema_version": "vcos.ai-visual-scene-plan.v1",
        "scene_id": "scene-01",
        "ordinal": 1,
        "narration_unit_ids": ["nu-1"],
        "information_unit_ids": ["iu-1"],
        "actual_start_ms": 0,
        "actual_end_ms": 5_000,
        "presentation_start_ms": 0,
        "presentation_end_ms": 5_000,
        "scene_meaning": "A workflow transforms raw material into a result.",
        "visual_function": "PROCESS",
        "core_subject": "one coherent transformation",
        "secondary_subjects": [],
        "action_or_relation": "input becoming output",
        "environment": "conceptual studio environment",
        "visual_goal": "make the transformation immediately legible",
        "visual_style_direction": "follow the style bible",
        "composition_direction": "asymmetric diagonal flow",
        "camera_direction": "wide editorial camera",
        "continuity_constraints": ["preserve palette"],
        "motion_need": "STATIC_SUFFICIENT",
        "production_route": "AI_IMAGE",
        "primary_asset_slot_id": "asset-slot-01",
        "reuses_primary_asset_from_scene_id": None,
        "asset_reuse_semantic_reason": None,
        "prompt_brief": "coherent transformation",
        "negative_constraints": ["no visible generated text"],
        "factual_risk": "LOW",
        "importance": "STANDARD",
        "transition_semantic_reason": "CONTINUATION",
        "style_bible_hash": style.content_hash,
        "planning_policy_hash": _hash("planning-policy"),
    }
    scene = AIVisualScenePlan(
        **scene_body, content_hash=ai_visual_stable_hash(scene_body)
    )
    negatives = [
        "no presentation slide",
        "no PowerPoint",
        "no three-box flowchart",
        "no generic infographic card",
        "no text-heavy composition",
        "no fake dashboard",
        "no fake product UI",
        "no floating random labels",
        "no visible generated text",
        "no logo",
        "no watermark",
    ]
    prompt_body = {
        "schema_version": "vcos.compiled-ai-image-prompt.v1",
        "scene_id": scene.scene_id,
        "scene_plan_hash": scene.content_hash,
        "style_bible_hash": style.content_hash,
        "prompt_compiler_version": "vcos.ai-image-prompt-compiler.v1",
        "aspect_ratio": "16:9",
        "expected_motion_preset": "slow_push_in",
        "motion_safe_composition": "central safe crop with layered margins",
        "prompt": "Cinematic conceptual transformation with rich depth.",
        "negative_constraints": negatives,
        "negative_prompt": ", ".join(negatives),
        "prompt_hash": ai_visual_text_hash(
            "Cinematic conceptual transformation with rich depth."
        ),
        "provider_call_made": False,
    }
    prompt = CompiledAIImagePrompt(
        **prompt_body, content_hash=ai_visual_stable_hash(prompt_body)
    )
    catalog = GoogleGeminiImageModelPriceCatalog()
    authority = _identity(tmp_path, catalog=catalog).model_dump(
        exclude={
            "effect_identity_hash",
            "request_hash",
            "request_journal_hash",
            "scene_id",
            "ordinal",
            "route",
            "primary_asset_slot_id",
            "bound_scene_ids",
            "bound_scene_plan_hashes",
            "primary_asset_owner_scene_id",
            "style_bible_ref",
            "style_bible_hash",
            "scene_plan_ref",
            "scene_plan_hash",
            "compiled_prompt_ref",
            "compiled_prompt_hash",
            "compiled_prompt_content_hash",
            "prompt_compiler_version",
            "prompt",
            "negative_prompt",
            "prompt_hash",
            "required_semantic_anchors",
        }
    )
    identity = V2AIImageSceneEffectIdentity.from_visual_contracts(
        style_bible=style,
        scene_plan=scene,
        compiled_prompt=prompt,
        **authority,
    )
    assert identity.route == "AI_IMAGE"
    assert identity.scene_plan_hash == scene.content_hash
    assert identity.style_bible_hash == style.content_hash
    assert identity.compiled_prompt_content_hash == prompt.content_hash
    assert identity.prompt_hash == prompt.prompt_hash

    reused_body = {
        **scene_body,
        "scene_id": "scene-02",
        "ordinal": 2,
        "narration_unit_ids": ["nu-2"],
        "information_unit_ids": ["iu-2"],
        "actual_start_ms": 5_000,
        "actual_end_ms": 10_000,
        "presentation_start_ms": 5_000,
        "presentation_end_ms": 10_000,
        "reuses_primary_asset_from_scene_id": scene.scene_id,
        "asset_reuse_semantic_reason": "Continue the exact same transformation.",
    }
    reused = AIVisualScenePlan(
        **reused_body, content_hash=ai_visual_stable_hash(reused_body)
    )
    reused_identity = V2AIImageSceneEffectIdentity.from_visual_contracts(
        style_bible=style,
        scene_plan=scene,
        compiled_prompt=prompt,
        bound_scene_plans=(scene, reused),
        **authority,
    )
    assert reused_identity.bound_scene_ids == ("scene-01", "scene-02")
    assert reused_identity.bound_scene_plan_hashes == (
        scene.content_hash,
        reused.content_hash,
    )
    assert reused_identity.primary_asset_owner_scene_id == scene.scene_id


def test_db_projections_cover_the_current_asset_effect_schema(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    prepared = V2AIImageRecordTransitions.prepared(identity, now=datetime.now(UTC))
    identity_columns = set(identity.db_identity_projection)
    evidence_columns = set(prepared.db_evidence_projection)
    database_columns = set(AIVisualAssetEffect.__table__.columns.keys())

    assert identity_columns.isdisjoint(evidence_columns)
    assert identity_columns | evidence_columns | {"id", "created_at", "updated_at"} == (
        database_columns
    )
    assert identity.db_identity_projection["asset_slot_id"] == (
        identity.primary_asset_slot_id
    )
    assert identity.db_identity_projection["reuse_authority_ref"] is None
    assert identity.db_identity_projection["reuse_authority_hash"] is None
    assert prepared.db_evidence_projection["request_journal_ref"] == (
        identity.request_journal_path
    )
    assert prepared.db_evidence_projection["provider_call_count"] == 0
