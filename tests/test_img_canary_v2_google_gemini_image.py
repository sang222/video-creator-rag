from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.img_canary import (
    IMG_CANARY_REVIEW_CHECKLIST,
    IMGCanaryDriveReadinessEvidence,
    IMGCanaryHumanReviewPacket,
    IMGCanaryProviderResponseSummary,
    IMGCanarySerializedRequestEvidence,
)
from app.contracts.img_canary_security import (
    IMG_CANARY_V1_AUTHORIZATION_REF,
    IMG_CANARY_V1_TASK_KEY,
    IMG_CANARY_V2_AUTHORIZATION_REF,
    IMG_CANARY_V2_AUTHORIZATION_RELATIVE_PATH,
    IMG_CANARY_V2_TASK_KEY,
)
from app.core.config import Settings
from app.providers.google_gemini_image import (
    GeminiImageResponseSafetyError,
    GoogleGeminiImageAdapter,
    build_fixture_png,
)
from app.services.img_canary import (
    IMG_CANARY_HEADLINE,
    IMGCanaryAttemptLedgerStore,
    IMGCanaryImageNormalizer,
    IMGCanaryPlanBuilder,
    IMGCanaryPreflightService,
)
from app.services.img_canary_runner import IMGCanaryControlledRunner
from app.services.img_canary_security import (
    IMGCanaryCredentialRotationAuthority,
    IMGCanaryMonthlyBudgetAuthority,
    IMGCanarySecurityAuthorityError,
    IMGCanaryTaskAuthorizationStore,
)
from app.services.img_canary_vqc import IMGCanaryVQCEvidenceBuilder
from app.services import production_archive


NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
OLD_RUN_ID = "img-canary-20260718T075252Z-319bacb0"
PREVIOUS_RUN_EVIDENCE_HASH = "e" * 64
OLD_OPENING_SPEND = Decimal("0.101")
MONTHLY_BUDGET_CAP = Decimal("20.00")


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "gemini_api_key": "v2-test-replacement-placeholder",
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
        "extra_ai_image_monthly_budget_usd": MONTHLY_BUDGET_CAP,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _v2_bundle(*, suffix: str = "a1b2c3d4"):
    return IMGCanaryPlanBuilder(
        _settings(),
        approval_version="v2",
    ).build(
        now=NOW,
        run_suffix=suffix,
        previous_run_evidence_hash=PREVIOUS_RUN_EVIDENCE_HASH,
    )


def _v2_task_store(path: Path, bundle):
    serialized = bundle.serialized_request_evidence
    binding = bundle.v2_approval_binding
    assert serialized is not None
    assert binding is not None
    store = IMGCanaryTaskAuthorizationStore(path)
    ledger = store.initialize(
        task_key=IMG_CANARY_V2_TASK_KEY,
        authorization_ref=IMG_CANARY_V2_AUTHORIZATION_REF,
        approval_version="V2",
        approved_run_id=bundle.run_identity.run_id,
        approved_request_fingerprint=(
            GoogleGeminiImageAdapter.idempotency_fingerprint(
                bundle.provider_request
            )
        ),
        approved_prompt_hash=bundle.provider_request.prompt_hash,
        approved_serialized_body_hash=serialized.serialized_body_hash,
        approved_scoped_approval_hash=binding.content_hash,
        now=NOW,
    )
    return store, ledger


def _drive_readiness(run_id: str) -> IMGCanaryDriveReadinessEvidence:
    payload = {
        "schema_version": "img-canary-v2-drive-readiness/v1",
        "run_id": run_id,
        "status": "PASS",
        "root_folder_id": "drive-root-img-canary",
        "root_folder_mime_type": "application/vnd.google-apps.folder",
        "oauth_access_token_persisted": False,
        "raw_drive_response_persisted": False,
        "checked_at": NOW,
    }
    return IMGCanaryDriveReadinessEvidence(
        **payload,
        content_hash=ai_image_stable_hash(payload),
    )


def _offline_authorities(tmp_path: Path, bundle):
    fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(
        bundle.provider_request
    )
    attempt_store = IMGCanaryAttemptLedgerStore(
        tmp_path / "workspace" / "manifests" / "attempt-ledger.json"
    )
    attempt = attempt_store.create(
        run_id=bundle.run_identity.run_id,
        request_fingerprint=fingerprint,
        idempotency_key=bundle.provider_request.idempotency_key,
        now=NOW,
    )
    task_store, task = _v2_task_store(
        tmp_path / "security" / IMG_CANARY_V2_AUTHORIZATION_RELATIVE_PATH,
        bundle,
    )
    credential_store = IMGCanaryCredentialRotationAuthority(
        tmp_path / "security" / "compromised-credential.json"
    )
    credential_store.record_compromised(
        credential="v2-test-superseded-placeholder",
        incident_ref="incident://img-canary/v2/test-predecessor",
        now=NOW,
    )
    credential = credential_store.verify_rotation(
        current_credential="v2-test-replacement-placeholder",
        rotation_ref="rotation://img-canary/v2/test-replacement",
        now=NOW,
    )
    budget_store = IMGCanaryMonthlyBudgetAuthority(
        tmp_path / "security" / "budget-2026-07.json"
    )
    budget_store.initialize(
        authority_ref="budget://small-team-ai/2026-07/img-canary",
        billing_period="2026-07",
        dedicated_cap_usd=MONTHLY_BUDGET_CAP,
        opening_spend_usd=OLD_OPENING_SPEND,
        per_request_hard_cap_usd=Decimal("0.15"),
        now=NOW,
    )
    budget = budget_store.inspect_capacity(
        run_id=bundle.run_identity.run_id,
        request_fingerprint=fingerprint,
        request_estimate_usd=bundle.cost.estimated_amount,
        now=NOW,
    )
    return {
        "fingerprint": fingerprint,
        "attempt_store": attempt_store,
        "attempt": attempt,
        "task_store": task_store,
        "task": task,
        "credential": credential,
        "budget_store": budget_store,
        "budget": budget,
    }


def _provider_summary(bundle, source: Path) -> IMGCanaryProviderResponseSummary:
    width, height, image_format = GoogleGeminiImageAdapter.probe_image(source)
    payload: dict[str, Any] = {
        "run_id": bundle.run_identity.run_id,
        "provider": "google_gemini_image",
        "model": "gemini-3.1-flash-image",
        "provider_status": "INTERACTION_COMPLETED",
        "provider_request_id_ref": "interactions/v2-test-one-shot",
        "provider_operation_id_ref": "interactions/v2-test-one-shot",
        "submitted_at": NOW,
        "completed_at": NOW + timedelta(seconds=1),
        "output_count": 1,
        "output_checksum": GoogleGeminiImageAdapter._file_sha256(source),
        "image_width": width,
        "image_height": height,
        "image_format": image_format,
        "size_bytes": source.stat().st_size,
        "usage_metadata": {"total_tokens": 1},
        "estimated_cost_usd": Decimal("0.101"),
        "actual_cost_usd": None,
        "provider_attempts_consumed": 1,
        "raw_response_persisted": False,
        "raw_image_bytes_persisted_in_manifest": False,
        "raw_url_persisted": False,
        "api_key_persisted": False,
        "external_fallback_used": False,
    }
    return IMGCanaryProviderResponseSummary(
        **payload,
        content_hash=ai_image_stable_hash(payload),
    )


def _valid_fixture_jpeg(tmp_path: Path) -> bytes:
    adapter = GoogleGeminiImageAdapter(_settings())
    encoder = Path(adapter.raster_decoder_path)
    if not encoder.is_file():
        pytest.skip("local FFmpeg JPEG fixture encoder is unavailable")
    source = tmp_path / "v2-jpeg-source.png"
    destination = tmp_path / "v2-jpeg-output.jpg"
    source.write_bytes(build_fixture_png(width=1920, height=1080))
    completed = subprocess.run(
        [
            str(encoder),
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
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0 or not destination.is_file():
        pytest.skip("local FFmpeg could not encode JPEG fixture")
    return destination.read_bytes()


def test_v2_official_sdk_body_is_response_format_only_and_binds_approval() -> None:
    bundle = _v2_bundle()
    serialized = bundle.serialized_request_evidence
    binding = bundle.v2_approval_binding
    assert serialized is not None
    assert binding is not None

    captured = GoogleGeminiImageAdapter.capture_official_sdk_serialization(
        bundle.provider_request
    )
    body = captured["body"]
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1beta/interactions"
    assert body == GoogleGeminiImageAdapter.expected_serialized_request_body(
        bundle.provider_request
    )
    assert set(body) == {
        "model",
        "input",
        "stream",
        "store",
        "background",
        "response_format",
    }
    assert "response_modalities" not in body
    assert body["store"] is False
    assert body["response_format"] == {
        "type": "image",
        "mime_type": "image/jpeg",
        "delivery": "inline",
        "aspect_ratio": "16:9",
        "image_size": "2K",
    }
    assert serialized.serialized_body_hash == captured["body_sha256"]
    assert serialized.sdk_retry_attempts == 1
    assert serialized.sdk_retries_disabled is True
    assert serialized.redacted_request_body["input"] == (
        f"sha256://prompt/{bundle.provider_request.prompt_hash}"
    )
    assert bundle.provider_request.prompt not in serialized.model_dump_json()
    assert "v2-test-replacement-placeholder" not in serialized.model_dump_json()

    assert binding.run_id == bundle.run_identity.run_id
    assert binding.base_approval_hash == bundle.approval.content_hash
    assert binding.request_hash == bundle.provider_request.content_hash
    assert binding.prompt_hash == bundle.provider_request.prompt_hash
    assert binding.serialized_request_evidence_hash == serialized.content_hash
    assert binding.serialized_body_hash == serialized.serialized_body_hash
    assert binding.previous_run_evidence_hash == PREVIOUS_RUN_EVIDENCE_HASH
    assert binding.attempt_limit == 1
    assert binding.external_fallback_allowed is False
    assert binding.production_eligible is False
    assert binding.not_publishable is True

    assert bundle.headline.exact_text == IMG_CANARY_HEADLINE
    assert bundle.headline.authority == "NATIVE_OVERLAY"
    assert bundle.headline.generated_pixel_authority is False
    assert IMG_CANARY_HEADLINE not in bundle.provider_request.prompt


def test_serialized_body_contract_rejects_legacy_selector_even_with_fresh_hashes() -> None:
    serialized = _v2_bundle().serialized_request_evidence
    assert serialized is not None
    payload = serialized.model_dump(mode="json", exclude={"content_hash"})
    redacted = dict(payload["redacted_request_body"])
    redacted["response_modalities"] = ["IMAGE"]
    payload["redacted_request_body"] = redacted
    payload["redacted_body_hash"] = ai_image_stable_hash(redacted)

    with pytest.raises(
        ValidationError,
        match="IMG_CANARY_SERIALIZED_REQUEST_KEYS_INVALID",
    ):
        IMGCanarySerializedRequestEvidence(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )


def test_v2_fake_inline_jpeg_parses_but_png_bytes_are_rejected(
    tmp_path: Path,
) -> None:
    bundle = _v2_bundle()
    request = bundle.provider_request
    assert request.uses_img_canary_v2_response_contract is True
    adapter = GoogleGeminiImageAdapter(_settings())
    jpeg = _valid_fixture_jpeg(tmp_path)
    response = {
        "id": "interactions/v2-jpeg-fixture",
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "image",
                        "data": base64.b64encode(jpeg).decode("ascii"),
                        "mime_type": "image/jpeg",
                    }
                ],
            }
        ],
        "usage": {"total_tokens": 1},
    }
    receipt, transient, summary = adapter._parse_real_response(
        request,
        response,
        submitted_at=NOW,
    )
    assert receipt.normalized_status == "SUCCEEDED"
    assert transient.image_bytes == jpeg
    assert transient.mime_type == "image/jpeg"
    assert summary["output_count"] == 1
    assert summary["image_format"] == "JPEG"

    png_response = json.loads(json.dumps(response))
    png_response["steps"][0]["content"][0]["data"] = base64.b64encode(
        build_fixture_png(width=1920, height=1080)
    ).decode("ascii")
    with pytest.raises(
        GeminiImageResponseSafetyError,
        match="GEMINI_IMAGE_V2_INLINE_JPEG_BYTES_REQUIRED",
    ):
        adapter._parse_real_response(
            request,
            png_response,
            submitted_at=NOW,
        )


def test_fresh_v2_authority_and_reservation_never_reopen_old_run(
    tmp_path: Path,
) -> None:
    security = tmp_path / "security"
    old_path = security / "master-authorization.json"
    old_store = IMGCanaryTaskAuthorizationStore(old_path)
    old_store.initialize(
        task_key=IMG_CANARY_V1_TASK_KEY,
        authorization_ref=IMG_CANARY_V1_AUTHORIZATION_REF,
        now=NOW,
    )
    old_store.claim(
        run_id=OLD_RUN_ID,
        request_fingerprint="1" * 64,
        now=NOW + timedelta(seconds=1),
    )
    old_store.consume(
        run_id=OLD_RUN_ID,
        request_fingerprint="1" * 64,
        completion_status="PROVIDER_ATTEMPT_FAILED",
        now=NOW + timedelta(seconds=2),
    )
    immutable_old_bytes = old_path.read_bytes()

    bundle = _v2_bundle()
    v2_path = security / IMG_CANARY_V2_AUTHORIZATION_RELATIVE_PATH
    v2_store, v2_available = _v2_task_store(v2_path, bundle)
    assert v2_path != old_path
    assert v2_available.status == "AVAILABLE"
    assert v2_available.approved_run_id == bundle.run_identity.run_id
    assert old_path.read_bytes() == immutable_old_bytes

    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_TASK_AUTHORIZATION_ALREADY_CLAIMED",
    ):
        old_store.claim(
            run_id=bundle.run_identity.run_id,
            request_fingerprint="2" * 64,
            now=NOW + timedelta(seconds=3),
        )
    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_V2_TASK_AUTHORIZATION_BINDING_MISMATCH",
    ):
        v2_store.claim(
            run_id="img-canary-v2-20260718T090000Z-deadbeef",
            request_fingerprint="2" * 64,
            now=NOW + timedelta(seconds=3),
        )

    budget_store = IMGCanaryMonthlyBudgetAuthority(
        security / "budget-2026-07.json"
    )
    budget_store.initialize(
        authority_ref="budget://small-team-ai/2026-07/img-canary",
        billing_period="2026-07",
        dedicated_cap_usd=MONTHLY_BUDGET_CAP,
        opening_spend_usd=OLD_OPENING_SPEND,
        per_request_hard_cap_usd=Decimal("0.15"),
        now=NOW,
    )
    fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(
        bundle.provider_request
    )
    reservation = budget_store.reserve(
        run_id=bundle.run_identity.run_id,
        request_fingerprint=fingerprint,
        request_estimate_usd=Decimal("0.101"),
        now=NOW + timedelta(seconds=3),
    )
    assert reservation.status == "RESERVED"
    assert reservation.run_id == bundle.run_identity.run_id
    assert reservation.request_fingerprint == fingerprint
    assert reservation.spent_before_usd == OLD_OPENING_SPEND
    assert reservation.available_before_usd == MONTHLY_BUDGET_CAP - OLD_OPENING_SPEND
    assert old_path.read_bytes() == immutable_old_bytes

    claimed = v2_store.claim(
        run_id=bundle.run_identity.run_id,
        request_fingerprint=fingerprint,
        now=NOW + timedelta(seconds=4),
    )
    assert claimed.status == "CLAIMED"
    consumed = v2_store.consume(
        run_id=bundle.run_identity.run_id,
        request_fingerprint=fingerprint,
        completion_status="PROVIDER_ATTEMPT_SUBMITTED",
        now=NOW + timedelta(seconds=5),
    )
    assert consumed.status == "CONSUMED"
    assert consumed.completion_status == "PROVIDER_ATTEMPT_SUBMITTED"
    assert old_path.read_bytes() == immutable_old_bytes

    other = _v2_bundle(suffix="bbbbbbbb")
    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_TASK_AUTHORITY_IDENTITY_CONFLICT",
    ):
        _v2_task_store(v2_path, other)


def test_repository_old_run_snapshot_remains_exact_and_read_only() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    runner = IMGCanaryControlledRunner(
        repo_root=repo_root,
        scoped_settings=_settings(),
        approval_version="v2",
    )
    old_root = repo_root / "artifacts" / "img_canary" / OLD_RUN_ID
    before = {
        str(path.relative_to(repo_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in old_root.rglob("*")
        if path.is_file()
    }
    evidence = runner._capture_previous_run_immutability(now=NOW)
    after = {
        str(path.relative_to(repo_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in old_root.rglob("*")
        if path.is_file()
    }

    assert before == after == evidence.file_sha256_by_relative_path
    assert evidence.file_count == 24
    assert evidence.aggregate_sha256 == (
        "6ea77966c51b012e09430c88e9f3c91d630ea4de67cbc87a54aa1ec1ab13f423"
    )
    assert evidence.task_authority_file_sha256 == (
        "6c115ed2ead3a6a730a26edc775dd68aae91e82dc54ef67661482d9d85c9c440"
    )
    assert evidence.attempts_consumed == 1
    assert evidence.task_authorization_status == "CONSUMED"
    assert evidence.task_completion_status == "PROVIDER_ATTEMPT_FAILED"
    assert evidence.provider_status == "NATIVE_SUBMIT_FAILED"
    assert evidence.provider_error_code == "GEMINI_IMAGE_PROVIDER_HTTP_400"
    assert evidence.provider_output_count == 0
    assert evidence.external_fallback_used is False


def test_v2_preflight_requires_drive_and_decoder_before_attempt_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        GoogleGeminiImageAdapter,
        "raster_decoder_ready",
        lambda self: True,
    )
    bundle = _v2_bundle()
    state = _offline_authorities(tmp_path, bundle)
    service = IMGCanaryPreflightService()

    missing_drive = service.evaluate(
        bundle=bundle,
        scoped_settings=_settings(),
        vqc1_final_passed=True,
        credential_rotation_evidence=state["credential"],
        monthly_budget_evidence=state["budget"],
        task_authorization_evidence=state["task"],
        attempt_ledger=state["attempt"],
        drive_readiness_evidence=None,
        now=NOW,
    )
    assert missing_drive.status == "BLOCKED"
    assert missing_drive.drive_readiness_passed is False
    assert "IMG_CANARY_V2_DRIVE_READINESS_BLOCKED" in (
        missing_drive.blocker_reason_codes
    )
    assert state["attempt_store"].load().attempts_consumed == 0

    drive = _drive_readiness(bundle.run_identity.run_id)
    planning = service.evaluate(
        bundle=bundle,
        scoped_settings=_settings(),
        vqc1_final_passed=True,
        credential_rotation_evidence=state["credential"],
        monthly_budget_evidence=state["budget"],
        task_authorization_evidence=state["task"],
        attempt_ledger=state["attempt"],
        drive_readiness_evidence=drive,
        now=NOW,
    )
    assert planning.status == "PASS"
    assert planning.serialized_request_contract_passed is True
    assert planning.v2_approval_binding_passed is True
    assert planning.drive_readiness_passed is True
    assert planning.evidence_refs["serialized_request_body"] == (
        bundle.serialized_request_evidence.serialized_body_hash
    )
    assert planning.evidence_refs["drive_readiness"] == drive.content_hash
    assert service.execution_gates(
        bundle=bundle,
        preflight=planning,
    ).all_passed is True
    assert state["attempt_store"].load().attempts_consumed == 0

    monkeypatch.setattr(
        GoogleGeminiImageAdapter,
        "raster_decoder_ready",
        lambda self: False,
    )
    decoder_blocked = service.evaluate(
        bundle=bundle,
        scoped_settings=_settings(),
        vqc1_final_passed=True,
        credential_rotation_evidence=state["credential"],
        monthly_budget_evidence=state["budget"],
        task_authorization_evidence=state["task"],
        attempt_ledger=state["attempt"],
        drive_readiness_evidence=drive,
        now=NOW,
    )
    assert decoder_blocked.status == "BLOCKED"
    assert "IMG_CANARY_JPEG_SAFE_DECODER_UNAVAILABLE" in (
        decoder_blocked.blocker_reason_codes
    )
    assert state["attempt_store"].load().attempts_consumed == 0


def test_drive_readiness_is_typed_redacted_and_never_calls_real_drive() -> None:
    bundle = _v2_bundle()

    class _Provider:
        def __init__(self) -> None:
            self.calls = 0

        def get_file_metadata(
            self,
            *,
            access_token: str,
            drive_file_id: str,
        ) -> SimpleNamespace:
            self.calls += 1
            assert access_token == "ephemeral-test-token"
            return SimpleNamespace(
                drive_file_id=drive_file_id,
                mime_type="application/vnd.google-apps.folder",
            )

    provider = _Provider()
    fake_archive = SimpleNamespace(
        root_folder_id="drive-root-img-canary",
        provider=provider,
    )
    evidence = IMGCanaryControlledRunner.verify_drive_readiness(
        drive_archive=fake_archive,
        access_token="ephemeral-test-token",
        run_id=bundle.run_identity.run_id,
        now=NOW,
    )

    assert provider.calls == 1
    assert isinstance(evidence, IMGCanaryDriveReadinessEvidence)
    assert evidence.status == "PASS"
    assert evidence.run_id == bundle.run_identity.run_id
    durable = evidence.model_dump_json()
    assert "ephemeral-test-token" not in durable
    assert evidence.oauth_access_token_persisted is False
    assert evidence.raw_drive_response_persisted is False


def test_one_submit_boundary_is_atomic_and_source_rejection_cannot_regenerate(
    tmp_path: Path,
) -> None:
    bundle = _v2_bundle()
    fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(
        bundle.provider_request
    )
    store = IMGCanaryAttemptLedgerStore(
        tmp_path / "manifests" / "attempt-ledger.json"
    )
    planned = store.create(
        run_id=bundle.run_identity.run_id,
        request_fingerprint=fingerprint,
        idempotency_key=bundle.provider_request.idempotency_key,
        now=NOW,
    )
    assert planned.attempts_consumed == 0
    assert planned.provider_call_made is False

    executing = store.consume_at_submit(
        expected_fingerprint=fingerprint,
        now=NOW + timedelta(seconds=1),
    )
    assert executing.attempts_consumed == 1
    assert executing.provider_call_made is True
    with pytest.raises(
        PermissionError,
        match="IMG_CANARY_PAID_ATTEMPT_ALREADY_CONSUMED",
    ):
        store.consume_at_submit(
            expected_fingerprint=fingerprint,
            now=NOW + timedelta(seconds=2),
        )

    rejected = store.finalize(
        succeeded=False,
        failure_reason_code="IMG_CANARY_V2_SOURCE_IMAGE_REJECTED",
        now=NOW + timedelta(seconds=2),
    )
    assert rejected.status == "BLOCKED_REQUIRES_NEW_APPROVAL"
    assert rejected.attempts_consumed == 1
    assert rejected.failure_reason_code == "IMG_CANARY_V2_SOURCE_IMAGE_REJECTED"
    with pytest.raises(
        PermissionError,
        match="IMG_CANARY_PAID_ATTEMPT_ALREADY_CONSUMED",
    ):
        store.consume_at_submit(
            expected_fingerprint=fingerprint,
            now=NOW + timedelta(seconds=3),
        )

    failure = GoogleGeminiImageAdapter(_settings())._real_failure_receipt(
        bundle.provider_request,
        submitted_at=NOW,
        provider_status="NATIVE_SUBMIT_FAILED",
        provider_error_code="GEMINI_IMAGE_PROVIDER_HTTP_400",
    )
    assert failure.generation_attempts_consumed == 1
    assert failure.external_provider_fallback_used is False
    assert failure.fallback_provider_key is None


def test_same_returned_image_can_be_reprocessed_without_a_generation_attempt(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "artifacts" / "img_canary" / "offline-repair"
    source = workspace / "source" / "original-generated.png"
    destination = workspace / "normalized" / "normalized-1920x1080.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(build_fixture_png(width=1920, height=1080))

    normalizer = IMGCanaryImageNormalizer()
    first = normalizer.normalize(
        source_path=source,
        destination_path=destination,
        workspace_root=workspace,
    )
    second = normalizer.normalize(
        source_path=source,
        destination_path=destination,
        workspace_root=workspace,
    )

    assert first == second
    assert destination.is_file()
    assert not list(workspace.rglob("*.part"))
    assert not list(workspace.rglob("*.part.png"))


def test_real_byte_vqc_binds_checksum_and_keeps_human_review_pending(
    tmp_path: Path,
) -> None:
    bundle = _v2_bundle()
    workspace = tmp_path / "vqc-workspace"
    source = workspace / "source" / "original-generated.png"
    normalized = workspace / "normalized" / "normalized-1920x1080.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(build_fixture_png(width=1920, height=1080))
    normalization = IMGCanaryImageNormalizer().normalize(
        source_path=source,
        destination_path=normalized,
        workspace_root=workspace,
    )
    checksum = GoogleGeminiImageAdapter._file_sha256(normalized)
    response = _provider_summary(bundle, source)
    fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(
        bundle.provider_request
    )
    attempt_store = IMGCanaryAttemptLedgerStore(
        workspace / "manifests" / "attempt-ledger.json"
    )
    attempt_store.create(
        run_id=bundle.run_identity.run_id,
        request_fingerprint=fingerprint,
        idempotency_key=bundle.provider_request.idempotency_key,
        now=NOW,
    )
    attempt_store.consume_at_submit(
        expected_fingerprint=fingerprint,
        now=NOW + timedelta(seconds=1),
    )
    attempt = attempt_store.finalize(
        succeeded=True,
        provider_request_id_ref="interactions/v2-test-one-shot",
        provider_operation_id_ref="interactions/v2-test-one-shot",
        now=NOW + timedelta(seconds=2),
    )
    materialization = {
        "transport": "GEMINI_API_NATIVE",
        "provider_call_made": True,
        "local_path": str(source),
        "size_bytes": source.stat().st_size,
        "sha256": GoogleGeminiImageAdapter._file_sha256(source),
        "image_width": 1920,
        "image_height": 1080,
        "image_format": "PNG",
        "raw_url_persisted": False,
        "part_path_remaining": False,
        "already_materialized": False,
    }
    evidence = IMGCanaryVQCEvidenceBuilder().build(
        bundle=bundle,
        normalized_image_path=normalized,
        provider_response=response,
        attempt_ledger=attempt,
        materialization_receipt=materialization,
        normalization_receipt=normalization,
        observed_output_summary="Actual decoded bytes inspected in isolated test.",
        now=NOW + timedelta(seconds=3),
    )

    assert evidence.expected_sha256 == checksum
    assert evidence.image_normalization.target_sha256 == checksum
    assert evidence.human_visual_review.review_state == "PENDING"
    assert evidence.human_visual_review.human_final_approval_auto_passed is False
    assert evidence.native_overlay.authoritative_text == IMG_CANARY_HEADLINE
    assert evidence.native_overlay.exact_text_native_authority is True
    assert evidence.native_overlay.generated_image_owns_final_text is False

    normalized.write_bytes(
        build_fixture_png(width=1920, height=1080, rgb=(80, 30, 20))
    )
    from app.services.image_visual_quality_control import (
        ImageVisualQualityControlService,
    )

    changed_bytes_report = ImageVisualQualityControlService().evaluate(
        image_path=normalized,
        evidence=evidence,
    )
    assert changed_bytes_report.technical_status == "BLOCK"
    technical = next(
        gate
        for gate in changed_bytes_report.gate_results
        if gate.gate_name == "TechnicalImageFitnessGate"
    )
    assert technical.result == "BLOCK"


def test_v2_archive_roles_are_complete_and_missing_role_is_rejected(
    tmp_path: Path,
) -> None:
    required = getattr(
        production_archive,
        "IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES",
        None,
    )
    assert required is not None, "IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES is required"
    expected_v2_roles = {
        "IMG_CANARY_V2_SERIALIZED_REQUEST_EVIDENCE",
        "IMG_CANARY_V2_OPERATOR_APPROVAL_BINDING",
        "IMG_CANARY_V2_PREVIOUS_RUN_IMMUTABILITY",
        "IMG_CANARY_V2_DRIVE_READINESS_EVIDENCE",
        "IMG_CANARY_V2_RUNTIME_PREFLIGHT",
        "IMG_CANARY_V2_RUNTIME_EXECUTION_GATES",
        "IMG_CANARY_VQC1_REPORT_JSON",
        "IMG_CANARY_RENDER_EXECUTION_RECEIPT",
        "IMG_CANARY_QC_CROP_FULL_FRAME",
        "IMG_CANARY_QC_CROP_OVERLAY_SAFE",
        "IMG_CANARY_QC_CROP_SUBJECT_FOCAL",
    }
    assert expected_v2_roles <= set(required)

    role_paths = production_archive.IMG_CANARY_ROLE_ARCHIVE_PATHS
    assert set(required) <= set(role_paths)
    missing_role = "IMG_CANARY_V2_SERIALIZED_REQUEST_EVIDENCE"
    sources = []
    for role in sorted(set(required) - {missing_role}):
        source = tmp_path / f"{role}.artifact"
        source.write_text(role, encoding="utf-8")
        sources.append(
            production_archive.ArchiveSource(
                logical_role=role,
                source_path=source,
                required_for_archive=True,
                required_for_local_purge=False,
            )
        )
    with pytest.raises(ValueError, match="ARCHIVE_REQUIRED_ROLES_MISSING"):
        production_archive.ProductionArchiveBuilder().build(
            manifest_id="img-canary-v2-test-archive",
            project_id="img-canary-v2-test-project",
            package_id="img-canary-v2-test-package",
            sources=sources,
            required_roles=required,
        )


def test_human_review_cannot_auto_pass_and_mr1_scope_remains_closed() -> None:
    payload: dict[str, Any] = {
        "run_id": "img-canary-v2-20260718T090000Z-a1b2c3d4",
        "review_state": "PENDING",
        "original_image_path": "/tmp/original.jpg",
        "normalized_image_path": "/tmp/normalized.png",
        "review_mp4_path": "/tmp/review.mp4",
        "drive_archive_receipt_ref": "drive-receipt://img-canary-v2/test",
        "drive_archive_receipt_hash": "a" * 64,
        "drive_archive_manifest_ref": "archive://img-canary-v2/test",
        "archive_verified": True,
        "drive_provider_call_made": True,
        "provider_attempts_consumed": 1,
        "estimated_cost_usd": Decimal("0.101"),
        "actual_cost_usd": None,
        "checklist": {name: False for name in IMG_CANARY_REVIEW_CHECKLIST},
        "generated_artifact_ambiguities": [],
        "production_eligible": False,
        "not_publishable": True,
        "proceed_to_ch1_flex_v2": False,
    }
    packet = IMGCanaryHumanReviewPacket(
        **payload,
        content_hash=ai_image_stable_hash(payload),
    )
    assert packet.review_state == "PENDING"
    assert packet.proceed_to_ch1_flex_v2 is False
    assert packet.production_eligible is False
    assert packet.not_publishable is True

    auto_pass = {**payload, "review_state": "PASS"}
    with pytest.raises(ValidationError):
        IMGCanaryHumanReviewPacket(
            **auto_pass,
            content_hash=ai_image_stable_hash(auto_pass),
        )

    # Approval v2 is only a one-shot image canary scope. It carries no MR1,
    # production, publish, or CH1-FLEX activation authority.
    binding = _v2_bundle().v2_approval_binding
    assert binding is not None
    serialized_binding = json.loads(binding.model_dump_json())
    assert serialized_binding["production_eligible"] is False
    assert serialized_binding["not_publishable"] is True
    assert not any("mr1" in key.lower() for key in serialized_binding)


def test_provider_summary_schema_cannot_persist_raw_response_key_base64_or_url() -> None:
    bundle = _v2_bundle()
    source_payload = build_fixture_png(width=1920, height=1080)
    summary_payload: dict[str, Any] = {
        "run_id": bundle.run_identity.run_id,
        "provider": "google_gemini_image",
        "model": "gemini-3.1-flash-image",
        "provider_status": "INTERACTION_COMPLETED",
        "provider_request_id_ref": "interactions/v2-redaction-test",
        "provider_operation_id_ref": "interactions/v2-redaction-test",
        "submitted_at": NOW,
        "completed_at": NOW + timedelta(seconds=1),
        "output_count": 1,
        "output_checksum": hashlib.sha256(source_payload).hexdigest(),
        "image_width": 1920,
        "image_height": 1080,
        "image_format": "PNG",
        "size_bytes": len(source_payload),
        "usage_metadata": {},
        "estimated_cost_usd": Decimal("0.101"),
        "actual_cost_usd": None,
        "provider_attempts_consumed": 1,
        "raw_response_persisted": False,
        "raw_image_bytes_persisted_in_manifest": False,
        "raw_url_persisted": False,
        "api_key_persisted": False,
        "external_fallback_used": False,
    }
    summary = IMGCanaryProviderResponseSummary(
        **summary_payload,
        content_hash=ai_image_stable_hash(summary_payload),
    )
    durable = summary.model_dump_json()
    assert "data:image" not in durable
    assert "https://" not in durable
    assert "v2-test-replacement-placeholder" not in durable

    for forbidden_field, forbidden_value in (
        ("raw_response", {"image": "base64"}),
        ("api_key", "secret"),
        ("signed_url", "https://example.invalid/signed"),
        ("base64_image", "AAA="),
    ):
        invalid = {**summary_payload, forbidden_field: forbidden_value}
        with pytest.raises(ValidationError):
            IMGCanaryProviderResponseSummary(
                **invalid,
                content_hash=ai_image_stable_hash(invalid),
            )
