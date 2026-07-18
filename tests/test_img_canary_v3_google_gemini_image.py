from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.image_visual_quality_control import (
    img_canary_provider_request_lineage_ref,
)
from app.contracts.img_canary import (
    IMGCanaryAttemptLedger,
    IMGCanaryDriveReadinessEvidence,
    IMGCanaryProviderResponseSummary,
    IMGCanaryV3SerializedRequestEvidence,
)
from app.contracts.img_canary_security import (
    IMG_CANARY_V3_AUTHORIZATION_REF,
    IMG_CANARY_V3_AUTHORIZATION_RELATIVE_PATH,
    IMG_CANARY_V3_TASK_KEY,
)
from app.core.config import Settings
from app.providers.google_gemini_image import (
    GoogleGeminiImageAdapter,
    build_fixture_png,
)
from app.services.img_canary import (
    IMGCanaryAttemptLedgerStore,
    IMGCanaryImageNormalizer,
    IMGCanaryPlanBuilder,
    IMGCanaryPreflightService,
)
from app.services.img_canary_runner import IMGCanaryControlledRunner
from app.services.img_canary_security import (
    IMGCanaryCredentialRotationAuthority,
    IMGCanaryMonthlyBudgetAuthority,
    IMGCanaryTaskAuthorizationStore,
)
from app.services.img_canary_vqc import IMGCanaryVQCEvidenceBuilder
from scripts.run_img_canary import _parser, _repair_history_name


NOW = datetime(2026, 7, 18, 16, 0, tzinfo=UTC)
PREVIOUS_RUNS_EVIDENCE_HASH = "e" * 64


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "gemini_api_key": "v3-test-replacement-placeholder",
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
        "extra_ai_image_monthly_budget_usd": Decimal("20.00"),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _v3_bundle(*, suffix: str = "a1b2c3d4"):
    return IMGCanaryPlanBuilder(
        _settings(),
        approval_version="v3",
    ).build(
        now=NOW,
        run_suffix=suffix,
        previous_runs_evidence_hash=PREVIOUS_RUNS_EVIDENCE_HASH,
    )


def _drive_readiness(run_id: str) -> IMGCanaryDriveReadinessEvidence:
    payload: dict[str, object] = {
        "schema_version": "img-canary-v3-drive-readiness/v1",
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


def _v3_success_vqc_inputs(
    tmp_path: Path,
    *,
    suffix: str,
    provider_request_id_ref: str | None,
):
    bundle = _v3_bundle(suffix=suffix)
    workspace = tmp_path / suffix
    source = workspace / "source" / "original-generated.png"
    normalized = workspace / "normalized" / "normalized-1920x1080.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(build_fixture_png(width=1920, height=1080))
    normalization = IMGCanaryImageNormalizer().normalize(
        source_path=source,
        destination_path=normalized,
        workspace_root=workspace,
    )
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
        provider_request_id_ref=provider_request_id_ref,
        provider_operation_id_ref=provider_request_id_ref,
        now=NOW + timedelta(seconds=2),
    )
    width, height, image_format = GoogleGeminiImageAdapter.probe_image(source)
    response_payload: dict[str, Any] = {
        "run_id": bundle.run_identity.run_id,
        "provider": "google_gemini_image",
        "model": "gemini-3.1-flash-image",
        "provider_status": "INTERACTION_COMPLETED",
        "provider_request_id_ref": provider_request_id_ref,
        "provider_operation_id_ref": provider_request_id_ref,
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
    response = IMGCanaryProviderResponseSummary(
        **response_payload,
        content_hash=ai_image_stable_hash(response_payload),
    )
    materialization = {
        "transport": "GEMINI_API_NATIVE",
        "provider_call_made": True,
        "local_path": str(source.resolve()),
        "size_bytes": source.stat().st_size,
        "sha256": GoogleGeminiImageAdapter._file_sha256(source),
        "image_width": width,
        "image_height": height,
        "image_format": image_format,
        "raw_url_persisted": False,
        "part_path_remaining": False,
        "already_materialized": False,
    }
    return bundle, normalized, response, attempt, materialization, normalization


def _successful_lineage_models(
    *,
    run_id: str,
    provider_request_id_ref: str | None,
) -> tuple[IMGCanaryAttemptLedger, IMGCanaryProviderResponseSummary]:
    attempt_payload: dict[str, Any] = {
        "run_id": run_id,
        "request_fingerprint": "a" * 64,
        "idempotency_key_hash": "b" * 64,
        "attempt_limit": 1,
        "attempts_consumed": 1,
        "status": "SUCCEEDED",
        "provider_call_made": True,
        "provider_request_id_ref": provider_request_id_ref,
        "provider_operation_id_ref": provider_request_id_ref,
        "failure_reason_code": None,
        "created_at": NOW,
        "updated_at": NOW + timedelta(seconds=2),
    }
    attempt = IMGCanaryAttemptLedger(
        **attempt_payload,
        content_hash=ai_image_stable_hash(attempt_payload),
    )
    response_payload: dict[str, Any] = {
        "run_id": run_id,
        "provider": "google_gemini_image",
        "model": "gemini-3.1-flash-image",
        "provider_status": "INTERACTION_COMPLETED",
        "provider_request_id_ref": provider_request_id_ref,
        "provider_operation_id_ref": provider_request_id_ref,
        "submitted_at": NOW,
        "completed_at": NOW + timedelta(seconds=1),
        "output_count": 1,
        "output_checksum": "c" * 64,
        "image_width": 1920,
        "image_height": 1080,
        "image_format": "PNG",
        "size_bytes": 1024,
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
    response = IMGCanaryProviderResponseSummary(
        **response_payload,
        content_hash=ai_image_stable_hash(response_payload),
    )
    return attempt, response


def test_v3_serialized_body_omits_delivery_and_binds_new_operator_approval() -> None:
    bundle = _v3_bundle()
    serialized = bundle.serialized_request_evidence
    binding = bundle.v3_approval_binding
    assert isinstance(serialized, IMGCanaryV3SerializedRequestEvidence)
    assert binding is not None
    assert bundle.run_identity.run_id.startswith("img-canary-v3-")
    assert bundle.provider_request.uses_img_canary_v3_response_contract is True

    captured = GoogleGeminiImageAdapter.capture_official_sdk_serialization(
        bundle.provider_request
    )
    body = captured["body"]
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
    assert body["store"] is False
    assert "response_modalities" not in body
    assert body["response_format"] == {
        "type": "image",
        "mime_type": "image/jpeg",
        "aspect_ratio": "16:9",
        "image_size": "2K",
    }
    assert "delivery" not in body["response_format"]
    assert serialized.serialized_body_hash == captured["body_sha256"]
    assert serialized.redacted_request_body["input"] == (
        f"sha256://prompt/{bundle.provider_request.prompt_hash}"
    )
    assert bundle.provider_request.prompt not in serialized.model_dump_json()
    assert "v3-test-replacement-placeholder" not in serialized.model_dump_json()

    assert binding.approval_source_ref == (
        "operator-message://codex-thread/2026-07-18/fix-and-rerun"
    )
    assert binding.approval_source_sha256 == (
        "3c895af877e10f7faa7db9fd2ad92752cb43305c13ce7d078cb1adfa077e9ada"
    )
    assert binding.approval_id == "operator-3c895af877e10f7f"
    assert binding.task_key == IMG_CANARY_V3_TASK_KEY
    assert binding.task_authorization_ref == IMG_CANARY_V3_AUTHORIZATION_REF
    assert binding.previous_runs_evidence_hash == PREVIOUS_RUNS_EVIDENCE_HASH
    assert binding.serialized_request_evidence_hash == serialized.content_hash
    assert binding.serialized_body_hash == serialized.serialized_body_hash
    assert binding.estimated_cost_usd == Decimal("0.101")
    assert binding.hard_cap_usd == Decimal("0.15")
    assert binding.attempt_limit == 1
    assert binding.external_fallback_allowed is False
    assert binding.production_eligible is False
    assert binding.not_publishable is True


def test_v3_serialized_contract_rejects_delivery_even_with_fresh_hashes() -> None:
    serialized = _v3_bundle().serialized_request_evidence
    assert isinstance(serialized, IMGCanaryV3SerializedRequestEvidence)
    payload = serialized.model_dump(mode="json", exclude={"content_hash"})
    redacted = dict(payload["redacted_request_body"])
    response_format = dict(redacted["response_format"])
    response_format["delivery"] = "inline"
    redacted["response_format"] = response_format
    payload["redacted_request_body"] = redacted
    payload["redacted_body_hash"] = ai_image_stable_hash(redacted)
    payload["response_format_hash"] = ai_image_stable_hash(response_format)

    with pytest.raises(
        ValidationError,
        match="IMG_CANARY_V3_SERIALIZED_RESPONSE_FORMAT_INVALID",
    ):
        IMGCanaryV3SerializedRequestEvidence(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )


def test_v3_keeps_v2_delivery_contract_unchanged() -> None:
    v2 = IMGCanaryPlanBuilder(
        _settings(),
        approval_version="v2",
    ).build(
        now=NOW,
        run_suffix="bbbbbbbb",
        previous_run_evidence_hash="d" * 64,
    )
    body = GoogleGeminiImageAdapter.expected_serialized_request_body(
        v2.provider_request
    )
    assert body["response_format"]["delivery"] == "inline"
    assert v2.v2_approval_binding is not None
    assert v2.v3_approval_binding is None


def test_v3_combined_terminal_snapshot_is_complete_and_read_only() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    runner = IMGCanaryControlledRunner(
        repo_root=repo_root,
        scoped_settings=_settings(),
        approval_version="v3",
    )
    historical_roots = (
        repo_root
        / "artifacts"
        / "img_canary"
        / "img-canary-20260718T075252Z-319bacb0",
        repo_root
        / "artifacts"
        / "img_canary"
        / "img-canary-v2-20260718T091203Z-cce118a4",
    )
    before = {
        str(path.relative_to(repo_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for root in historical_roots
        for path in root.rglob("*")
        if path.is_file()
    }
    evidence = runner._capture_previous_runs_immutability(now=NOW)
    after = {
        str(path.relative_to(repo_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for root in historical_roots
        for path in root.rglob("*")
        if path.is_file()
    }

    assert before == after
    assert evidence.v1_terminal_run.file_count == 24
    assert evidence.v2_terminal_run.file_count == 28
    assert evidence.v1_terminal_run.file_sha256_by_relative_path | (
        evidence.v2_terminal_run.file_sha256_by_relative_path
    ) == before
    assert evidence.v1_terminal_run.task_completion_status == (
        "PROVIDER_ATTEMPT_FAILED"
    )
    assert evidence.v2_terminal_run.task_completion_status == (
        "PROVIDER_ATTEMPT_SUBMITTED"
    )
    assert evidence.v2_terminal_run.provider_output_count == 0
    assert evidence.v2_terminal_run.external_fallback_used is False


def test_v3_runner_plan_uses_fresh_authority_and_zero_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    historical = IMGCanaryControlledRunner(
        repo_root=repo_root,
        scoped_settings=_settings(),
        approval_version="v3",
    )._capture_previous_runs_immutability(now=NOW)
    isolated_repo = tmp_path / "repo"
    (isolated_repo / ".git").mkdir(parents=True)
    runner = IMGCanaryControlledRunner(
        repo_root=isolated_repo,
        scoped_settings=_settings(),
        approval_version="v3",
    )
    monkeypatch.setattr(
        runner,
        "_capture_previous_runs_immutability",
        lambda *, now: historical,
    )

    planned = runner.plan(now=NOW, run_suffix="cccccccc")
    assert planned.bundle.run_identity.run_id == (
        "img-canary-v3-20260718T160000Z-cccccccc"
    )
    assert planned.planned_attempt.attempts_consumed == 0
    assert planned.planned_attempt.provider_call_made is False
    assert planned.previous_runs_evidence == historical
    assert planned.previous_runs_evidence_path == (
        planned.workspace_root / "manifests" / "previous-runs-immutability.json"
    )
    assert planned.artifact_paths["operator-approval-v3-binding.json"].is_file()
    assert planned.artifact_paths["serialized-request-evidence.json"].is_file()
    assert planned.artifact_paths["previous-runs-immutability.json"].is_file()
    assert planned.task_authorization_path == (
        isolated_repo
        / "var"
        / "credentials"
        / "img-canary"
        / IMG_CANARY_V3_AUTHORIZATION_RELATIVE_PATH
    ).resolve(strict=False)
    task = IMGCanaryTaskAuthorizationStore(
        planned.task_authorization_path
    ).load()
    assert task.status == "AVAILABLE"
    assert task.approval_version == "V3"
    assert task.approved_run_id == planned.bundle.run_identity.run_id
    assert task.approved_scoped_approval_hash == (
        planned.bundle.v3_approval_binding.content_hash
    )


def test_v3_offline_preflight_binds_all_v3_refs_without_consuming_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        GoogleGeminiImageAdapter,
        "raster_decoder_ready",
        lambda self: True,
    )
    bundle = _v3_bundle(suffix="dddddddd")
    serialized = bundle.serialized_request_evidence
    binding = bundle.v3_approval_binding
    assert isinstance(serialized, IMGCanaryV3SerializedRequestEvidence)
    assert binding is not None
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
    task_store = IMGCanaryTaskAuthorizationStore(
        tmp_path / "security" / IMG_CANARY_V3_AUTHORIZATION_RELATIVE_PATH
    )
    task = task_store.initialize(
        task_key=IMG_CANARY_V3_TASK_KEY,
        authorization_ref=IMG_CANARY_V3_AUTHORIZATION_REF,
        approval_version="V3",
        approved_run_id=bundle.run_identity.run_id,
        approved_request_fingerprint=fingerprint,
        approved_prompt_hash=bundle.provider_request.prompt_hash,
        approved_serialized_body_hash=serialized.serialized_body_hash,
        approved_scoped_approval_hash=binding.content_hash,
        now=NOW,
    )
    credential_store = IMGCanaryCredentialRotationAuthority(
        tmp_path / "security" / "compromised-credential.json"
    )
    credential_store.record_compromised(
        credential="v3-test-superseded-placeholder",
        incident_ref="incident://img-canary/v3/test-predecessor",
        now=NOW,
    )
    credential = credential_store.verify_rotation(
        current_credential="v3-test-replacement-placeholder",
        rotation_ref="rotation://img-canary/v3/test-replacement",
        now=NOW,
    )
    budget_store = IMGCanaryMonthlyBudgetAuthority(
        tmp_path / "security" / "budget-2026-07.json"
    )
    budget_store.initialize(
        authority_ref="budget://small-team-ai/2026-07/img-canary",
        billing_period="2026-07",
        dedicated_cap_usd=Decimal("20.00"),
        opening_spend_usd=Decimal("0.202"),
        per_request_hard_cap_usd=Decimal("0.15"),
        now=NOW,
    )
    budget = budget_store.inspect_capacity(
        run_id=bundle.run_identity.run_id,
        request_fingerprint=fingerprint,
        request_estimate_usd=bundle.cost.estimated_amount,
        now=NOW,
    )
    drive = _drive_readiness(bundle.run_identity.run_id)

    preflight = IMGCanaryPreflightService().evaluate(
        bundle=bundle,
        scoped_settings=_settings(),
        vqc1_final_passed=True,
        credential_rotation_evidence=credential,
        monthly_budget_evidence=budget,
        task_authorization_evidence=task,
        attempt_ledger=attempt,
        drive_readiness_evidence=drive,
        repository_identity_passed=True,
        worktree_reviewed=True,
        now=NOW,
    )

    assert preflight.status == "PASS"
    assert preflight.serialized_request_contract_passed is True
    assert preflight.v2_approval_binding_passed is None
    assert preflight.v3_approval_binding_passed is True
    assert preflight.drive_readiness_passed is True
    assert preflight.evidence_refs["serialized_request_evidence"] == (
        serialized.content_hash
    )
    assert preflight.evidence_refs["serialized_request_body"] == (
        serialized.serialized_body_hash
    )
    assert preflight.evidence_refs["v3_approval_binding"] == binding.content_hash
    assert preflight.evidence_refs["previous_runs_immutability"] == (
        PREVIOUS_RUNS_EVIDENCE_HASH
    )
    assert preflight.evidence_refs["drive_readiness"] == drive.content_hash
    assert IMGCanaryPreflightService.execution_gates(
        bundle=bundle,
        preflight=preflight,
    ).all_passed is True
    assert attempt_store.load().attempts_consumed == 0


def test_cli_exposes_mutually_exclusive_local_v3_profile_without_execution() -> None:
    args = _parser().parse_args(
        ["--fresh-v3-approval", "--run-suffix", "eeeeeeee"]
    )
    assert args.fresh_v3_approval is True
    assert args.fresh_v2_approval is False
    assert args.execute is False
    assert _repair_history_name("v3") == "img_canary_v3_repair_cycles.json"
    assert _repair_history_name("v2") == "img_canary_v2_repair_cycles.json"
    assert _repair_history_name("v1") == "img_canary_repair_cycles.json"
    with pytest.raises(SystemExit):
        _parser().parse_args(["--fresh-v2-approval", "--fresh-v3-approval"])


def test_v3_success_without_provider_id_uses_response_hash_lineage(
    tmp_path: Path,
) -> None:
    (
        bundle,
        normalized,
        response,
        attempt,
        materialization,
        normalization,
    ) = _v3_success_vqc_inputs(
        tmp_path,
        suffix="f1f1f1f1",
        provider_request_id_ref=None,
    )
    response_before = response.model_dump(mode="json")
    attempt_before = attempt.model_dump(mode="json")

    evidence, report = IMGCanaryVQCEvidenceBuilder().build_and_evaluate(
        bundle=bundle,
        normalized_image_path=normalized,
        provider_response=response,
        attempt_ledger=attempt,
        materialization_receipt=materialization,
        normalization_receipt=normalization,
        observed_output_summary="Actual V3 bytes inspected in isolated test.",
        now=NOW + timedelta(seconds=3),
    )

    expected_ref = (
        f"evidence://img-canary-v3/{response.run_id}/provider-response/"
        f"{response.content_hash}"
    )
    assert evidence.rights_disclosure.provider_request_id == expected_ref
    assert evidence.image_materialization is not None
    assert evidence.image_normalization is not None
    assert evidence.image_materialization.provider_request_id_ref == expected_ref
    assert evidence.image_normalization.provider_request_id_ref == expected_ref
    assert evidence.provider_response is not None
    assert evidence.attempt_ledger is not None
    assert evidence.provider_response.provider_request_id_ref is None
    assert evidence.attempt_ledger.provider_request_id_ref is None
    assert response.model_dump(mode="json") == response_before
    assert attempt.model_dump(mode="json") == attempt_before
    rights_gate = next(
        gate
        for gate in report.gate_results
        if gate.gate_name == "RightsDisclosureCompletenessGate"
    )
    assert rights_gate.result == "PASS"
    assert report.archive_eligible_for_review is True


def test_v3_explicit_provider_id_lineage_is_unchanged(tmp_path: Path) -> None:
    explicit_ref = "interactions/v3-explicit-provider-id"
    (
        bundle,
        normalized,
        response,
        attempt,
        materialization,
        normalization,
    ) = _v3_success_vqc_inputs(
        tmp_path,
        suffix="f2f2f2f2",
        provider_request_id_ref=explicit_ref,
    )

    evidence, report = IMGCanaryVQCEvidenceBuilder().build_and_evaluate(
        bundle=bundle,
        normalized_image_path=normalized,
        provider_response=response,
        attempt_ledger=attempt,
        materialization_receipt=materialization,
        normalization_receipt=normalization,
        observed_output_summary="Explicit provider lineage inspected.",
        now=NOW + timedelta(seconds=3),
    )

    assert evidence.rights_disclosure.provider_request_id == explicit_ref
    assert evidence.image_materialization is not None
    assert evidence.image_normalization is not None
    assert evidence.image_materialization.provider_request_id_ref == explicit_ref
    assert evidence.image_normalization.provider_request_id_ref == explicit_ref
    assert evidence.provider_response is not None
    assert evidence.attempt_ledger is not None
    assert evidence.provider_response.provider_request_id_ref == explicit_ref
    assert evidence.attempt_ledger.provider_request_id_ref == explicit_ref
    rights_gate = next(
        gate
        for gate in report.gate_results
        if gate.gate_name == "RightsDisclosureCompletenessGate"
    )
    assert rights_gate.result == "PASS"


@pytest.mark.parametrize(
    "run_id",
    [
        "img-canary-20260718T160000Z-11111111",
        "img-canary-v2-20260718T160000Z-22222222",
    ],
)
def test_v1_v2_provider_id_lineage_policy_remains_strict(run_id: str) -> None:
    explicit_ref = "interactions/historical-explicit-id"
    explicit_attempt, explicit_response = _successful_lineage_models(
        run_id=run_id,
        provider_request_id_ref=explicit_ref,
    )
    assert img_canary_provider_request_lineage_ref(
        attempt=explicit_attempt,
        response=explicit_response,
    ) == explicit_ref

    missing_attempt, missing_response = _successful_lineage_models(
        run_id=run_id,
        provider_request_id_ref=None,
    )
    assert (
        img_canary_provider_request_lineage_ref(
            attempt=missing_attempt,
            response=missing_response,
        )
        is None
    )
