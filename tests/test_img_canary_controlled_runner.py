from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.contracts.asset_acquisition import (
    DriveArchiveFileReceipt,
    DriveArchiveReceipt,
)
from app.core.config import Settings
from app.providers.google_gemini_image import (
    GoogleGeminiImageAdapter,
    build_fixture_png,
)
from app.services.img_canary_runner import (
    IMG_CANARY_EXPLICIT_EXECUTION_TOKEN,
    IMGCanaryControlledRunner,
)
from app.services.img_canary import (
    IMG_CANARY_CREDENTIAL_INCIDENT_REF,
    IMGCanaryArtifactWriter,
)
from app.services.img_canary_security import IMGCanaryCredentialRotationAuthority
from app.services.native_render_plan import stable_hash


class _Interactions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        png = build_fixture_png(width=1920, height=1080)
        return {
            "id": "interactions/img-canary-runner-test-001",
            "status": "completed",
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "image",
                            "data": base64.b64encode(png).decode("ascii"),
                            "mime_type": "image/png",
                        }
                    ],
                }
            ],
            "usage": {"total_input_tokens": 10, "total_output_tokens": 20},
        }


class _Client:
    def __init__(self, interactions: _Interactions) -> None:
        self.interactions = interactions


class _VerifiedDriveArchive:
    """Fake Drive boundary that derives a verified receipt from the frozen manifest."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def upload_and_verify(
        self,
        *,
        manifest,
        run_id: str,
        archive_date: str,
        access_token: str,
    ) -> DriveArchiveReceipt:
        assert access_token == "ephemeral-drive-token"
        self.calls.append(
            {
                "manifest_hash": manifest.manifest_hash,
                "run_id": run_id,
                "archive_date": archive_date,
            }
        )
        files = [
            DriveArchiveFileReceipt(
                archive_path=entry.expected_archive_path,
                drive_file_id=f"drive-{index}",
                local_size=entry.size_bytes,
                drive_size=entry.size_bytes,
                local_sha256=entry.sha256,
                drive_sha256=entry.sha256,
                local_md5=entry.md5,
                drive_md5=entry.md5,
                verification_method="SHA256",
                verified=True,
            )
            for index, entry in enumerate(manifest.files)
        ]
        manifest_bytes = (
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True).encode(
                "utf-8"
            )
            + b"\n"
        )
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        manifest_md5 = hashlib.md5(
            manifest_bytes,
            usedforsecurity=False,
        ).hexdigest()
        files.append(
            DriveArchiveFileReceipt(
                archive_path="00-manifests/production-archive-manifest.json",
                drive_file_id="drive-manifest",
                local_size=len(manifest_bytes),
                drive_size=len(manifest_bytes),
                local_sha256=manifest_sha,
                drive_sha256=manifest_sha,
                local_md5=manifest_md5,
                drive_md5=manifest_md5,
                verification_method="SHA256",
                verified=True,
            )
        )
        payload = {
            "archive_manifest_ref": manifest.manifest_id,
            "archive_manifest_hash": manifest.manifest_hash,
            "configured_root_folder_id_reference": "configured://drive/root",
            "root_relative_folder_path": (
                f"smoke_tests/{archive_date}/img_canary/{run_id}"
            ),
            "drive_folder_id": "drive-run-folder",
            "files": [item.model_dump(mode="json") for item in files],
            "total_local_size": sum(item.local_size for item in files),
            "total_drive_size": sum(item.drive_size or 0 for item in files),
            "archive_state": "VERIFIED",
            "mismatch_reason_codes": [],
            "verified_at": datetime.fromisoformat(archive_date).replace(tzinfo=UTC),
            "provider_call_made": True,
            "transport": "GOOGLE_DRIVE_API",
        }
        receipt = DriveArchiveReceipt(**payload, receipt_hash="pending")
        return receipt.model_copy(
            update={
                "receipt_hash": stable_hash(
                    receipt.model_dump(mode="json", exclude={"receipt_hash"})
                )
            }
        )


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "gemini_api_key": "runner-test-placeholder",
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
        "extra_ai_image_monthly_budget_usd": Decimal("0.15"),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _seed_compromised_predecessor(
    runner: IMGCanaryControlledRunner,
    *,
    now: datetime,
) -> None:
    IMGCanaryCredentialRotationAuthority(
        runner.security_root / "compromised-credential.json"
    ).record_compromised(
        credential="superseded-runner-test-placeholder",
        incident_ref=IMG_CANARY_CREDENTIAL_INCIDENT_REF,
        now=now,
    )


def test_controlled_runner_persists_bound_preflight_and_submits_at_most_once(
    tmp_path: Path,
) -> None:
    interactions = _Interactions()
    settings = _settings()
    adapter = GoogleGeminiImageAdapter(
        settings,
        real_client=_Client(interactions),
    )
    runner = IMGCanaryControlledRunner(
        repo_root=_repo(tmp_path),
        scoped_settings=settings,
        adapter_factory=lambda: adapter,
    )
    # This path exercises the live transport boundary, which correctly rejects
    # an approval that has expired relative to the wall clock. Keep the test's
    # authority window live instead of coupling it to the original canary date.
    # Leave headroom for the runner's real finalization timestamp while keeping
    # the six-hour approval window live.
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    _seed_compromised_predecessor(runner, now=now)
    planned = runner.plan(now=now, run_suffix="deadbeef")
    prepared = runner.preflight(
        planned=planned,
        vqc1_final_passed=True,
        credential_rotation_ref="rotation://img-canary/tests/runner-pass",
        now=now,
    )

    assert prepared.preflight.status == "PASS"
    assert prepared.execution_gates.all_passed is True
    assert prepared.preflight_path.is_file()
    result = runner.execute_paid_once(
        prepared=prepared,
        explicit_execution_token=IMG_CANARY_EXPLICIT_EXECUTION_TOKEN,
    )
    duplicate = runner.execute_paid_once(
        prepared=prepared,
        explicit_execution_token=IMG_CANARY_EXPLICIT_EXECUTION_TOKEN,
    )

    assert len(interactions.calls) == 1
    runtime_preflight = json.loads(
        (
            planned.workspace_root
            / "manifests"
            / "preflight-runtime-submit.json"
        ).read_text(encoding="utf-8")
    )
    runtime_gates = json.loads(
        (
            planned.workspace_root
            / "manifests"
            / "execution-gates-runtime-submit.json"
        ).read_text(encoding="utf-8")
    )
    assert runtime_preflight["task_authorization_evidence"]["status"] == "CLAIMED"
    assert runtime_preflight["monthly_budget_evidence"]["status"] == "RESERVED"
    assert runtime_preflight["evidence_refs"]["planning_preflight"] == (
        prepared.preflight.content_hash
    )
    assert runtime_gates["channel_monthly_budget_gate_passed"] is True
    assert result.operation_receipt.normalized_status == "SUCCEEDED"
    assert result.attempt_ledger.status == "SUCCEEDED"
    assert result.attempt_ledger.attempts_consumed == 1
    assert result.original_image_path is not None
    assert result.original_image_path.is_file()
    assert result.provider_response_summary is not None
    assert result.materialization_receipt is not None
    assert duplicate.operation_receipt.state_hash == result.operation_receipt.state_hash

    fake_drive = _VerifiedDriveArchive()
    completion = runner.complete_post_paid_pipeline(
        paid_execution=result,
        drive_archive=fake_drive,
        access_token="ephemeral-drive-token",
        repair_cycles=[
            {
                "cycle": 1,
                "phase": "OFFLINE_FIXTURE",
                "result": "PASS",
                "provider_attempts_before": 0,
                "provider_attempts_after": 0,
            }
        ],
        now=now,
    )
    review = completion.local_review
    archive = completion.archive
    drive_receipt = completion.drive_archive_receipt
    packet = completion.human_review_packet
    gates = {gate.gate_name: gate.result for gate in review.vqc_report.gate_results}
    assert review.vqc_evidence.image_materialization is not None
    assert review.vqc_evidence.image_normalization is not None
    assert review.vqc_report.technical_status == "PASS"
    assert review.vqc_report.archive_eligible_for_review is True
    assert gates["RightsDisclosureCompletenessGate"] == "PASS"
    assert gates["GeneratedTextArtifactGate"] == "REVIEW_REQUIRED"
    assert review.review_mp4_path.is_file()
    assert review.render_execution_receipt.no_provider_calls_confirmed is True
    assert archive.manifest.required_roles_complete is True
    assert archive.manifest_path.is_file()
    assert len(archive.manifest.files) == len(archive.source_paths_by_role)
    report_snapshot = json.loads(
        completion.reports.canary_summary_path.read_text(encoding="utf-8")
    )
    assert report_snapshot["captured_stage"] == "LOCAL_REVIEW_COMPLETE_ARCHIVE_PENDING"
    assert report_snapshot["drive_archive"] == "PENDING_NOT_STARTED_AT_SNAPSHOT"
    assert report_snapshot["archive_verified"] is False
    repair_snapshot = json.loads(
        completion.reports.repair_cycles_path.read_text(encoding="utf-8")
    )
    assert repair_snapshot["repair_cycle_count"] == 1
    assert repair_snapshot["provider_attempts_at_snapshot"] == 1
    assert repair_snapshot["additional_generation_submissions_during_repairs"] == 0
    assert completion.drive_archive_receipt_path.is_file()
    assert completion.human_review_packet_path.is_file()
    assert fake_drive.calls == [
        {
            "manifest_hash": archive.manifest.manifest_hash,
            "run_id": planned.bundle.run_identity.run_id,
            "archive_date": now.date().isoformat(),
        }
    ]
    assert packet.review_state == "PENDING"
    assert packet.archive_verified is True
    assert packet.provider_attempts_consumed == 1

    drive_files = drive_receipt.files
    drive_payload = drive_receipt.model_dump(mode="json", exclude={"receipt_hash"})
    tampered_item = drive_files[0].model_copy(
        update={"local_sha256": "0" * 64, "drive_sha256": "0" * 64}
    )
    tampered_payload = {
        **drive_payload,
        "files": [
            tampered_item.model_dump(mode="json"),
            *[item.model_dump(mode="json") for item in drive_files[1:]],
        ],
    }
    tampered_receipt = DriveArchiveReceipt(
        **tampered_payload,
        receipt_hash="pending",
    )
    tampered_receipt = tampered_receipt.model_copy(
        update={
            "receipt_hash": stable_hash(
                tampered_receipt.model_dump(mode="json", exclude={"receipt_hash"})
            )
        }
    )
    with pytest.raises(ValueError, match="DRIVE_ITEM_MANIFEST_MISMATCH"):
        IMGCanaryArtifactWriter(planned.workspace_root).build_pending_human_packet(
            run_id=planned.bundle.run_identity.run_id,
            original_image_path=result.original_image_path,
            normalized_image_path=review.normalized_image_path,
            review_mp4_path=review.review_mp4_path,
            drive_archive_receipt=tampered_receipt,
            archive_manifest=archive.manifest,
            archive_manifest_path=archive.manifest_path,
            attempt_ledger=result.attempt_ledger,
            vqc_report=review.vqc_report,
            render_execution_receipt=review.render_execution_receipt,
            estimated_cost_usd=planned.bundle.cost.estimated_amount,
            actual_cost_usd=None,
        )


def test_controlled_runner_blocks_before_transport_without_safe_key_and_budget(
    tmp_path: Path,
) -> None:
    interactions = _Interactions()
    settings = _settings(extra_ai_image_monthly_budget_usd=Decimal("0"))
    runner = IMGCanaryControlledRunner(
        repo_root=_repo(tmp_path),
        scoped_settings=settings,
        adapter_factory=lambda: GoogleGeminiImageAdapter(
            settings,
            real_client=_Client(interactions),
        ),
    )
    now = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)
    runner.record_current_credential_compromised(now=now)
    planned = runner.plan(now=now, run_suffix="feedface")
    prepared = runner.preflight(
        planned=planned,
        vqc1_final_passed=True,
        credential_rotation_ref="rotation://img-canary/tests/not-rotated",
        now=now,
    )

    assert prepared.preflight.status == "BLOCKED"
    assert "GEMINI_API_KEY_ROTATION_REQUIRED_AFTER_EXPOSURE" in (
        prepared.preflight.blocker_reason_codes
    )
    assert "IMG_CANARY_MONTHLY_BUDGET_BLOCKED" in (
        prepared.preflight.blocker_reason_codes
    )
    assert prepared.planned.planned_attempt.attempts_consumed == 0
    with pytest.raises(PermissionError, match="PERSISTED_PASS_PREFLIGHT_REQUIRED"):
        runner.execute_paid_once(
            prepared=prepared,
            explicit_execution_token=IMG_CANARY_EXPLICIT_EXECUTION_TOKEN,
        )
    assert interactions.calls == []


def test_controlled_runner_preflight_blocks_without_jpeg_decoder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interactions = _Interactions()
    settings = _settings()
    monkeypatch.setattr(
        GoogleGeminiImageAdapter,
        "raster_decoder_ready",
        lambda self: False,
    )
    runner = IMGCanaryControlledRunner(
        repo_root=_repo(tmp_path),
        scoped_settings=settings,
        adapter_factory=lambda: GoogleGeminiImageAdapter(
            settings,
            real_client=_Client(interactions),
        ),
    )
    now = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)
    _seed_compromised_predecessor(runner, now=now)
    planned = runner.plan(now=now, run_suffix="dec0de00")
    prepared = runner.preflight(
        planned=planned,
        vqc1_final_passed=True,
        credential_rotation_ref="rotation://img-canary/tests/decoder",
        now=now,
    )

    assert prepared.preflight.status == "BLOCKED"
    assert "IMG_CANARY_JPEG_SAFE_DECODER_UNAVAILABLE" in (
        prepared.preflight.blocker_reason_codes
    )
    assert prepared.planned.planned_attempt.status == "PLANNED"
    assert prepared.planned.planned_attempt.attempts_consumed == 0
    assert interactions.calls == []


def test_controlled_runner_requires_exact_paid_execution_token(tmp_path: Path) -> None:
    settings = _settings()
    runner = IMGCanaryControlledRunner(
        repo_root=_repo(tmp_path),
        scoped_settings=settings,
    )
    now = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)
    _seed_compromised_predecessor(runner, now=now)
    planned = runner.plan(now=now, run_suffix="cafebabe")
    prepared = runner.preflight(
        planned=planned,
        vqc1_final_passed=True,
        credential_rotation_ref="rotation://img-canary/tests/token",
        now=now,
    )
    with pytest.raises(PermissionError, match="EXPLICIT_EXECUTION_TOKEN_REQUIRED"):
        runner.execute_paid_once(
            prepared=prepared,
            explicit_execution_token="yes",
        )


def test_controlled_runner_rejects_persisted_gate_tamper_before_transport(
    tmp_path: Path,
) -> None:
    interactions = _Interactions()
    settings = _settings()
    runner = IMGCanaryControlledRunner(
        repo_root=_repo(tmp_path),
        scoped_settings=settings,
        adapter_factory=lambda: GoogleGeminiImageAdapter(
            settings,
            real_client=_Client(interactions),
        ),
    )
    now = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)
    _seed_compromised_predecessor(runner, now=now)
    planned = runner.plan(now=now, run_suffix="0badc0de")
    prepared = runner.preflight(
        planned=planned,
        vqc1_final_passed=True,
        credential_rotation_ref="rotation://img-canary/tests/tamper",
        now=now,
    )
    prepared.execution_gates_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="PERSISTED_PREFLIGHT_INVALID"):
        runner.execute_paid_once(
            prepared=prepared,
            explicit_execution_token=IMG_CANARY_EXPLICIT_EXECUTION_TOKEN,
        )
    assert interactions.calls == []


def test_task_wide_authorization_blocks_a_second_distinct_run(
    tmp_path: Path,
) -> None:
    interactions = _Interactions()
    settings = _settings(extra_ai_image_monthly_budget_usd=Decimal("0.30"))
    runner = IMGCanaryControlledRunner(
        repo_root=_repo(tmp_path),
        scoped_settings=settings,
        adapter_factory=lambda: GoogleGeminiImageAdapter(
            settings,
            real_client=_Client(interactions),
        ),
    )
    # Explicit transition timestamps must stay behind the runner's real
    # finalization timestamp while the authority window remains live.
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    _seed_compromised_predecessor(runner, now=now)
    first = runner.plan(now=now, run_suffix="11111111")
    first_prepared = runner.preflight(
        planned=first,
        vqc1_final_passed=True,
        credential_rotation_ref="rotation://img-canary/tests/global-one-shot",
        now=now,
    )
    runner.execute_paid_once(
        prepared=first_prepared,
        explicit_execution_token=IMG_CANARY_EXPLICIT_EXECUTION_TOKEN,
        now=now + timedelta(seconds=1),
    )

    second_now = now + timedelta(seconds=2)
    second = runner.plan(now=second_now, run_suffix="22222222")
    second_prepared = runner.preflight(
        planned=second,
        vqc1_final_passed=True,
        credential_rotation_ref="rotation://img-canary/tests/global-one-shot",
        now=second_now,
    )

    assert second_prepared.preflight.status == "BLOCKED"
    assert "IMG_CANARY_TASK_AUTHORIZATION_UNAVAILABLE" in (
        second_prepared.preflight.blocker_reason_codes
    )
    with pytest.raises(PermissionError, match="PERSISTED_PASS_PREFLIGHT_REQUIRED"):
        runner.execute_paid_once(
            prepared=second_prepared,
            explicit_execution_token=IMG_CANARY_EXPLICIT_EXECUTION_TOKEN,
            now=second_now + timedelta(seconds=1),
        )
    assert len(interactions.calls) == 1


def test_runner_preflight_rejects_a_tampered_cost_snapshot(
    tmp_path: Path,
) -> None:
    settings = _settings()
    runner = IMGCanaryControlledRunner(
        repo_root=_repo(tmp_path),
        scoped_settings=settings,
    )
    now = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)
    _seed_compromised_predecessor(runner, now=now)
    planned = runner.plan(now=now, run_suffix="33333333")
    tampered_cost = planned.bundle.cost.model_copy(
        update={"estimated_amount": Decimal("0.001")}
    )
    tampered = replace(
        planned,
        bundle=replace(planned.bundle, cost=tampered_cost),
    )

    with pytest.raises(ValueError, match="GEMINI_IMAGE_COST_ESTIMATE_MISMATCH"):
        runner.preflight(
            planned=tampered,
            vqc1_final_passed=True,
            credential_rotation_ref="rotation://img-canary/tests/cost-tamper",
            now=now,
        )


def test_runner_rejects_replaced_canonical_security_path_before_preflight(
    tmp_path: Path,
) -> None:
    settings = _settings()
    runner = IMGCanaryControlledRunner(
        repo_root=_repo(tmp_path),
        scoped_settings=settings,
    )
    now = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)
    _seed_compromised_predecessor(runner, now=now)
    planned = runner.plan(now=now, run_suffix="44444444")
    replaced_path = replace(
        planned,
        task_authorization_path=tmp_path / "cloned-master-authorization.json",
    )

    with pytest.raises(ValueError, match="NONCANONICAL_RUN_AUTHORITY_PATH"):
        runner.preflight(
            planned=replaced_path,
            vqc1_final_passed=True,
            credential_rotation_ref="rotation://img-canary/tests/path-tamper",
            now=now,
        )


def test_runner_revalidates_cost_before_claim_or_reservation(
    tmp_path: Path,
) -> None:
    interactions = _Interactions()
    settings = _settings()
    runner = IMGCanaryControlledRunner(
        repo_root=_repo(tmp_path),
        scoped_settings=settings,
        adapter_factory=lambda: GoogleGeminiImageAdapter(
            settings,
            real_client=_Client(interactions),
        ),
    )
    now = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)
    _seed_compromised_predecessor(runner, now=now)
    planned = runner.plan(now=now, run_suffix="55555555")
    prepared = runner.preflight(
        planned=planned,
        vqc1_final_passed=True,
        credential_rotation_ref="rotation://img-canary/tests/execute-cost-tamper",
        now=now,
    )
    tampered_cost = planned.bundle.cost.model_copy(
        update={"estimated_amount": Decimal("0.001")}
    )
    tampered_prepared = replace(
        prepared,
        planned=replace(
            planned,
            bundle=replace(planned.bundle, cost=tampered_cost),
        ),
    )

    with pytest.raises(ValueError, match="GEMINI_IMAGE_COST_ESTIMATE_MISMATCH"):
        runner.execute_paid_once(
            prepared=tampered_prepared,
            explicit_execution_token=IMG_CANARY_EXPLICIT_EXECUTION_TOKEN,
            now=now,
        )
    task_state = json.loads(
        planned.task_authorization_path.read_text(encoding="utf-8")
    )
    budget_state = json.loads(planned.budget_authority_path.read_text(encoding="utf-8"))
    assert task_state["status"] == "AVAILABLE"
    assert budget_state["reservations"] == []
    assert interactions.calls == []
