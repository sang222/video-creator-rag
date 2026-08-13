from __future__ import annotations

import hashlib
import json
import re
import socket
import uuid
from copy import deepcopy
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.core.actor import _system_worker_actor
from app.core.errors import ValidationFailureError
from app.db.models.foundation import DomainEvent
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.m7 import (
    ManualPublishConfirmation,
    PublishHandoffPackage,
    UploadedVideo,
)
from app.db.models.mr1_budget import MR1MonthlyBudgetReservation
from app.db.models.ops import DeadLetterJob, ProviderAttempt
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.db.models.v2_effect import (
    V2NarrationTimingRecoveryAuthority,
    V2NarrationTimingRecoveryReceipt,
    V2ProductionEffectLedger,
)
from app.services.cqr1_real_provider import ElevenLabsForcedAlignmentClient
from app.services.cqr1_real_provider import _safe_forced_alignment_response_capture
from app.services.config_registry import content_hash
from app.services.launch_cadence import LongFormCadenceService
from app.services.mr1_provider_gateways import (
    MR1AlignmentGatewayAdapter,
    _temporal_normalized,
)
from app.services.mr1_monthly_budget import MR1MonthlyBudgetAuthority
from app.services.production_workflow import build_default_stage_handler_registry
from app.services.script_verifier_settlement import (
    ScriptVerifierSettlementRecoveryService,
)
from app.services.temporal_authority import (
    ElevenLabsForcedAlignmentRequestBuilder,
    ElevenLabsForcedAlignmentResponseParser,
)
from app.services.v2_elevenlabs_narration import (
    V2ElevenLabsNarrationAdapter,
    _build_srt_cues,
)
from app.services.v2_narration_timing_recovery import (
    V2NarrationTimingRecoveryService,
    _authority_body,
    _receipt_body,
)
from app.services.v2_provider_production import build_v2_provider_production_gateway
from app.workers.production_workflow import ProductionWorkflowWorker
from tests.qualification.conftest import QualificationFactory
from tests.test_controlled_verifier_settlement import (
    _blocked_live_shaped_source,
    _install_ready_finalization_authorities,
    _install_research_context_source,
    _run_exact_worker_to_readiness,
)
import tests.test_controlled_verifier_settlement as settlement_test


_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64
_HASH_D = "d" * 64
_HASH_E = "e" * 64
_HASH_F = "f" * 64


class _NetworkForbidden(AssertionError):
    pass


@pytest.fixture(autouse=True)
def _forbid_accidental_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recovery regression may use only its explicitly injected fake client."""

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise _NetworkForbidden("test attempted an undeclared network connection")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


class _CountingForcedAlignmentClient:
    """One-shot fake with a provider-like capture-before-parse boundary."""

    def __init__(
        self,
        *,
        response: dict[str, Any],
        parse: Callable[[dict[str, Any]], object],
        fail_after_capture: bool = False,
    ) -> None:
        self.response = deepcopy(response)
        self.parse = parse
        self.fail_after_capture = fail_after_capture
        self.call_count = 0
        self.capture_count = 0

    def execute_once(
        self,
        *,
        response_capture: Callable[[dict[str, Any]], None],
        **_kwargs: object,
    ) -> object:
        if self.call_count:
            raise AssertionError("forced-alignment fake was invoked more than once")
        self.call_count += 1
        response_capture(deepcopy(self.response))
        self.capture_count += 1
        if self.fail_after_capture:
            raise ValueError("injected parser crash after durable response capture")
        return self.parse(deepcopy(self.response))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _live_request_identity(
    *, command_id: str, audio_relative_path: str
) -> dict[str, Any]:
    return {
        "schema_version": "vcos.v2-elevenlabs-request.v1",
        "command_id": command_id,
        "idempotency_key": (
            "v2-elevenlabs-narration:project:media:package:elevenlabs-final-narration"
        ),
        "script_content_hash": _HASH_A,
        "approved_script_hash": _HASH_B,
        "voice_id": "pNInz6obpgDQGcFmaJgB",
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "speed": 0.9,
            "style": 0.0,
            "stability": 0.55,
            "similarity_boost": 0.75,
            "use_speaker_boost": True,
        },
        "estimated_cost_usd": "1",
        "output_relative_path": audio_relative_path,
        "attempt_limit": 1,
    }


def _forced_response(words: list[str]) -> dict[str, Any]:
    step = 0.25
    return {
        "request_id": "forced-alignment-request-001",
        "words": [
            {
                "type": "word",
                "text": word,
                "start": index * step,
                "end": (index + 1) * step,
            }
            for index, word in enumerate(words)
        ],
        "alignment_loss": 0.01,
        "transcript_loss": 0.01,
    }


def _no_provider_settings(permission: bool | None) -> SimpleNamespace:
    return SimpleNamespace(
        provider_real_execution_enabled=True,
        provider_production_execution_enabled=True,
        media_provider_calls_disabled=False,
        elevenlabs_real_execution_enabled=True,
        elevenlabs_forced_alignment_permission_confirmed=permission,
        elevenlabs_api_key=SimpleNamespace(get_secret_value=lambda: "test-secret"),
        budget_mode="hard_env",
        monthly_ai_budget_usd=250,
        elevenlabs_monthly_cap_usd=22,
    )


class _DatabaseForbidden:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"permission failure touched the database via {name}")


class _ReadOnlyNoAuthorityDatabase:
    """Allow the single authority lookup needed to distinguish offline replay."""

    def scalar(self, _statement: object) -> None:
        return None

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"failed precondition mutated the database via {name}")


@pytest.mark.parametrize("permission", [None, False])
def test_recovery_permission_blocks_before_authority_database_or_client(
    tmp_path: Path, permission: bool | None
) -> None:
    client = SimpleNamespace(call_count=0)
    service = V2NarrationTimingRecoveryService(
        _ReadOnlyNoAuthorityDatabase(),  # type: ignore[arg-type]
        settings=_no_provider_settings(permission),  # type: ignore[arg-type]
        session_factory=lambda: _DatabaseForbidden(),  # type: ignore[arg-type]
        client=client,
        workspace_root=tmp_path,
    )

    with pytest.raises(
        ValidationFailureError,
        match="V2_NARRATION_TIMING_RECOVERY_PERMISSION_NOT_CONFIRMED",
    ):
        service.recover(uuid.uuid4(), _controlled_worker_actor())

    assert client.call_count == 0


def test_recovery_provider_preflight_blocks_before_authority_or_client(
    db_session,
    tmp_path: Path,
) -> None:
    settings = _no_provider_settings(True)
    settings.provider_real_execution_enabled = False
    client = SimpleNamespace(call_count=0)
    service = V2NarrationTimingRecoveryService(
        db_session,
        settings=settings,  # type: ignore[arg-type]
        client=client,
        workspace_root=tmp_path,
    )

    with pytest.raises(
        ValidationFailureError,
        match=(
            "V2_NARRATION_TIMING_RECOVERY_PROVIDER_PREFLIGHT_FAILED:"
            "global_real_execution_enabled"
        ),
    ):
        service.authorize(uuid.uuid4(), _controlled_worker_actor())

    assert (
        db_session.scalar(select(func.count(V2NarrationTimingRecoveryAuthority.id)))
        == 0
    )
    assert client.call_count == 0


class _FixtureMultipartTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = deepcopy(response)
        self.call_count = 0

    def multipart_json_request(self, *_args: object, **_kwargs: object):
        self.call_count += 1
        return deepcopy(self.response), {"request-id": "forced-alignment-request-001"}


@pytest.mark.parametrize("permission", [None, False])
def test_unknown_or_false_permission_blocks_before_alignment_network(
    tmp_path: Path, permission: bool | None
) -> None:
    settings = SimpleNamespace(
        provider_real_execution_enabled=True,
        provider_production_execution_enabled=True,
        media_provider_calls_disabled=False,
        elevenlabs_real_execution_enabled=True,
        elevenlabs_forced_alignment_permission_confirmed=permission,
        budget_mode="hard_env",
        monthly_ai_budget_usd=250,
        elevenlabs_monthly_cap_usd=22,
    )
    transport = _FixtureMultipartTransport(_forced_response(["alpha"]))
    gateway = MR1AlignmentGatewayAdapter(
        api_key="configured-test-secret",
        settings=settings,  # type: ignore[arg-type]
        workspace_root=tmp_path,
        client=ElevenLabsForcedAlignmentClient(transport=transport),  # type: ignore[arg-type]
    )

    preflight = gateway.preflight()

    assert preflight["result"] == "BLOCK"
    assert preflight["failed_checks"] == ["forced_alignment_permission_confirmed"]
    assert preflight["provider_calls"] == 0
    assert preflight["billable_generation_probe"] is False
    assert transport.call_count == 0


def test_forced_alignment_response_is_captured_before_parser_and_replays_offline(
    tmp_path: Path,
) -> None:
    """The one provider response remains sufficient after a local parser crash."""

    normalized = _temporal_normalized({"normalized_text": "alpha beta gamma"})
    raw_response = _forced_response(["alpha", "beta", "gamma"])
    transport = _FixtureMultipartTransport(raw_response)
    captured: list[dict[str, Any]] = []
    client = ElevenLabsForcedAlignmentClient(
        transport=transport,  # type: ignore[arg-type]
        response_capture=lambda value: captured.append(deepcopy(dict(value))),
    )
    client.response_parser.parse = lambda **_kwargs: (_ for _ in ()).throw(
        ValueError("injected parser crash")
    )
    audio = tmp_path / "sealed.mp3"
    audio.write_bytes(b"sealed audio bytes")

    with pytest.raises(ValueError, match="injected parser crash"):
        client.execute_once(
            api_key="test-secret",
            normalized=normalized,
            audio_path=audio,
            audio_asset_ref=f"file-sha256:{_sha256(audio)}",
            audio_duration_ms=1_000,
        )

    assert client.call_count == 1
    assert transport.call_count == 1
    assert len(captured) == 1
    assert captured[0]["capture_scope"] == "FORCED_ALIGNMENT_PARSER_INPUT_ALLOWLIST"
    assert captured[0]["secret_values_exposed"] is False
    replayed = ElevenLabsForcedAlignmentResponseParser().parse(
        response=captured[0]["response"],
        response_headers=captured[0]["response_headers"],
        normalized=normalized,
        audio_asset_ref=f"file-sha256:{_sha256(audio)}",
        audio_duration_ms=1_000,
    )

    assert replayed.verification_status == "PASS"
    assert replayed.provider_request_id == "forced-alignment-request-001"
    assert transport.call_count == 1


def test_exact_alignment_rejects_captured_raw_character_text_drift() -> None:
    normalized = _temporal_normalized({"normalized_text": "alpha beta"})
    response = _ExactForcedAlignmentClient._response(normalized, 1_000)
    evidence = ElevenLabsForcedAlignmentResponseParser().parse(
        response=response,
        response_headers={"request-id": "forced-alignment-recovery-001"},
        normalized=normalized,
        audio_asset_ref="v2-elevenlabs://exact-character-negative",
        audio_duration_ms=1_000,
    )
    response["characters"][0]["text"] = "z"

    with pytest.raises(
        ValidationFailureError,
        match="V2_NARRATION_TIMING_RECOVERY_EXACT_COVERAGE_REQUIRED",
    ):
        V2NarrationTimingRecoveryService._validate_exact_alignment(
            evidence,
            normalized,
            raw_response=response,
        )


def _raw_authority_values() -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "workflow_run_id": uuid.uuid4(),
        "video_project_id": uuid.uuid4(),
        "media_effect_ledger_id": uuid.uuid4(),
        "media_domain_event_id": uuid.uuid4(),
        "media_dead_letter_job_id": uuid.uuid4(),
        "root_replacement_authority_id": uuid.uuid4(),
        "verifier_settlement_authority_id": uuid.uuid4(),
        "settlement_qualification_run_id": uuid.uuid4(),
        "production_package_artifact_version_id": uuid.uuid4(),
        "production_package_hash": _HASH_A,
        "script_artifact_version_id": uuid.uuid4(),
        "script_content_hash": _HASH_B,
        "approved_script_hash": _HASH_C,
        "budget_reservation_id": uuid.uuid4(),
        "budget_reservation_ref": "mr1-budget://workflow",
        "budget_authority_hash": _HASH_D,
        "provider_policy_hash": _HASH_E,
        "tts_request_journal_ref": "effects/media/elevenlabs-request-journal.json",
        "tts_request_identity_hash": _HASH_F,
        "tts_idempotency_key": "v2-elevenlabs-narration:project:media:package",
        "audio_relative_path": "effects/media/elevenlabs-final-narration.mp3",
        "audio_checksum_sha256": _HASH_A,
        "audio_size_bytes": 128,
        "audio_duration_ms": 420_000,
        "original_failure_reason_code": "V2_ELEVENLABS_PROVIDER_FAILURE",
        "forced_alignment_permission_confirmed": True,
        "max_tts_retries": 0,
        "max_forced_alignment_submissions": 1,
        "schema_version": "vcos.v2-narration-timing-recovery-authority.v1",
        "recovery_reason": "DURABLE_TTS_AUDIO_MISSING_TIMING_PROVENANCE",
        "authorized_by_actor_type": "system_worker",
        "authorized_by_actor_id": uuid.uuid4(),
        "authorized_by_actor_role": "SYSTEM_WORKER",
        "authority_hash": _HASH_B,
    }


_AUTHORITY_INSERT = text(
    """
    INSERT INTO v2_narration_timing_recovery_authorities (
        id, workflow_run_id, video_project_id, media_effect_ledger_id,
        media_domain_event_id, media_dead_letter_job_id,
        root_replacement_authority_id, verifier_settlement_authority_id,
        settlement_qualification_run_id, production_package_artifact_version_id,
        production_package_hash, script_artifact_version_id, script_content_hash,
        approved_script_hash, budget_reservation_id, budget_reservation_ref,
        budget_authority_hash, provider_policy_hash, tts_request_journal_ref,
        tts_request_identity_hash, tts_idempotency_key, audio_relative_path,
        audio_checksum_sha256, audio_size_bytes, audio_duration_ms,
        original_failure_reason_code, forced_alignment_permission_confirmed,
        max_tts_retries, max_forced_alignment_submissions, schema_version,
        recovery_reason, authorized_by_actor_type, authorized_by_actor_id,
        authorized_by_actor_role, authority_hash
    ) VALUES (
        :id, :workflow_run_id, :video_project_id, :media_effect_ledger_id,
        :media_domain_event_id, :media_dead_letter_job_id,
        :root_replacement_authority_id, :verifier_settlement_authority_id,
        :settlement_qualification_run_id, :production_package_artifact_version_id,
        :production_package_hash, :script_artifact_version_id, :script_content_hash,
        :approved_script_hash, :budget_reservation_id, :budget_reservation_ref,
        :budget_authority_hash, :provider_policy_hash, :tts_request_journal_ref,
        :tts_request_identity_hash, :tts_idempotency_key, :audio_relative_path,
        :audio_checksum_sha256, :audio_size_bytes, :audio_duration_ms,
        :original_failure_reason_code, :forced_alignment_permission_confirmed,
        :max_tts_retries, :max_forced_alignment_submissions, :schema_version,
        :recovery_reason, :authorized_by_actor_type, :authorized_by_actor_id,
        :authorized_by_actor_role, :authority_hash
    )
    """
)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("forced_alignment_permission_confirmed", False),
        ("max_tts_retries", 1),
        ("max_forced_alignment_submissions", 0),
        ("max_forced_alignment_submissions", 2),
        ("audio_checksum_sha256", "tampered"),
    ],
)
def test_database_rejects_timing_recovery_authority_expansion(
    db_session, field: str, value: object
) -> None:
    values = _raw_authority_values()
    values[field] = value
    db_session.execute(text("SET LOCAL session_replication_role = replica"))

    with pytest.raises(DBAPIError):
        db_session.execute(_AUTHORITY_INSERT, values)


def test_database_recovery_authority_is_immutable(db_session) -> None:
    values = _raw_authority_values()
    db_session.execute(text("SET LOCAL session_replication_role = replica"))
    db_session.execute(_AUTHORITY_INSERT, values)
    db_session.execute(text("SET LOCAL session_replication_role = origin"))

    with pytest.raises(DBAPIError, match="immutable"):
        db_session.execute(
            text(
                "UPDATE v2_narration_timing_recovery_authorities "
                "SET audio_size_bytes = audio_size_bytes + 1 WHERE id = :id"
            ),
            {"id": values["id"]},
        )


_RECEIPT_INSERT = text(
    """
    INSERT INTO v2_narration_timing_recovery_receipts (
        id, authority_id, workflow_run_id, media_effect_ledger_id,
        forced_alignment_request_hash, forced_alignment_provider_response_hash,
        forced_alignment_provider_request_id,
        forced_alignment_provider_request_id_availability,
        forced_alignment_evidence_hash, recovered_timing_seed_hash,
        narration_receipt_hash, canonical_media_timeline_hash,
        provider_call_count, tts_retry_count, schema_version, recovery_state,
        receipt_hash
    ) VALUES (
        :id, :authority_id, :workflow_run_id, :media_effect_ledger_id,
        :forced_alignment_request_hash, :forced_alignment_provider_response_hash,
        :forced_alignment_provider_request_id,
        :forced_alignment_provider_request_id_availability,
        :forced_alignment_evidence_hash, :recovered_timing_seed_hash,
        :narration_receipt_hash, :canonical_media_timeline_hash,
        :provider_call_count, :tts_retry_count, :schema_version, :recovery_state,
        :receipt_hash
    )
    """
)


def _raw_receipt_values(authority: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "authority_id": authority["id"],
        "workflow_run_id": authority["workflow_run_id"],
        "media_effect_ledger_id": authority["media_effect_ledger_id"],
        "forced_alignment_request_hash": _HASH_A,
        "forced_alignment_provider_response_hash": _HASH_B,
        "forced_alignment_provider_request_id": "forced-request-001",
        "forced_alignment_provider_request_id_availability": "PRESENT",
        "forced_alignment_evidence_hash": _HASH_C,
        "recovered_timing_seed_hash": _HASH_D,
        "narration_receipt_hash": _HASH_E,
        "canonical_media_timeline_hash": _HASH_F,
        "provider_call_count": 1,
        "tts_retry_count": 0,
        "schema_version": "vcos.v2-narration-timing-recovery-receipt.v1",
        "recovery_state": "VERIFIED",
        "receipt_hash": _HASH_A,
    }


def test_authority_and_receipt_hash_bodies_bind_explicit_identity_and_time() -> None:
    created_at = datetime(2026, 8, 13, 9, 30, tzinfo=UTC)
    authority = _raw_authority_values()
    authority.pop("authority_hash")
    authority["created_at"] = created_at
    receipt = _raw_receipt_values(authority)
    receipt.pop("receipt_hash")
    receipt["created_at"] = created_at

    authority_body = _authority_body(authority)
    receipt_body = _receipt_body(receipt)

    assert authority_body["authority_id"] == str(authority["id"])
    assert "id" not in authority_body
    assert authority_body["created_at"] == created_at.isoformat()
    assert receipt_body["receipt_id"] == str(receipt["id"])
    assert "id" not in receipt_body
    assert receipt_body["created_at"] == created_at.isoformat()


def test_authority_scope_comparison_rejects_lineage_audio_budget_or_hash_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = V2NarrationTimingRecoveryService(
        _DatabaseForbidden(),  # type: ignore[arg-type]
        settings=_no_provider_settings(True),  # type: ignore[arg-type]
        session_factory=lambda: _DatabaseForbidden(),  # type: ignore[arg-type]
        workspace_root=tmp_path,
    )
    workflow_id, project_id = uuid.uuid4(), uuid.uuid4()
    request_identity = _live_request_identity(
        command_id="sealed-media-command",
        audio_relative_path="effects/sealed/final.mp3",
    )
    scope = SimpleNamespace(
        run=SimpleNamespace(id=workflow_id, video_project_id=project_id),
        ledger=SimpleNamespace(id=uuid.uuid4(), command_id="sealed-media-command"),
        event=SimpleNamespace(id=uuid.uuid4()),
        dead_letter=SimpleNamespace(id=uuid.uuid4()),
        root=SimpleNamespace(id=uuid.uuid4()),
        settlement=SimpleNamespace(id=uuid.uuid4()),
        qualification=SimpleNamespace(id=uuid.uuid4()),
        package_version=SimpleNamespace(id=uuid.uuid4(), content_hash=_HASH_A),
        package=SimpleNamespace(compiled_policy_snapshot_hash=_HASH_B),
        script_version=SimpleNamespace(id=uuid.uuid4(), content_hash=_HASH_C),
        approved_script_hash=_HASH_D,
        budget=SimpleNamespace(id=uuid.uuid4(), reservation_ref="mr1-budget://sealed"),
        budget_authority_hash=_HASH_E,
        request_identity=request_identity,
        audio_relative_path="effects/sealed/final.mp3",
        audio_checksum=_HASH_F,
        audio_size_bytes=128,
        audio_duration_ms=420_000,
    )
    authority = SimpleNamespace(
        workflow_run_id=workflow_id,
        video_project_id=project_id,
        media_effect_ledger_id=scope.ledger.id,
        media_domain_event_id=scope.event.id,
        media_dead_letter_job_id=scope.dead_letter.id,
        root_replacement_authority_id=scope.root.id,
        verifier_settlement_authority_id=scope.settlement.id,
        settlement_qualification_run_id=scope.qualification.id,
        production_package_artifact_version_id=scope.package_version.id,
        production_package_hash=_HASH_A,
        script_artifact_version_id=scope.script_version.id,
        script_content_hash=_HASH_C,
        approved_script_hash=_HASH_D,
        budget_reservation_id=scope.budget.id,
        budget_reservation_ref=scope.budget.reservation_ref,
        tts_request_identity_hash=content_hash(request_identity),
        tts_request_journal_ref=service.adapter._relative(
            service.adapter._effect_dir(scope.ledger.command_id)
            / "elevenlabs-request-journal.json"
        ),
        tts_idempotency_key=request_identity["idempotency_key"],
        budget_authority_hash=_HASH_E,
        provider_policy_hash=_HASH_B,
        audio_relative_path=scope.audio_relative_path,
        audio_checksum_sha256=_HASH_F,
        audio_size_bytes=scope.audio_size_bytes,
        audio_duration_ms=scope.audio_duration_ms,
    )
    monkeypatch.setattr(
        service,
        "_validate_existing_authority",
        lambda _authority: None,
    )
    service._assert_authority_matches_scope(authority, scope)

    for field, drift in (
        ("video_project_id", uuid.uuid4()),
        ("media_effect_ledger_id", uuid.uuid4()),
        ("production_package_hash", "0" * 64),
        ("script_content_hash", "1" * 64),
        ("approved_script_hash", "2" * 64),
        ("budget_reservation_id", uuid.uuid4()),
        ("budget_authority_hash", "3" * 64),
        ("provider_policy_hash", "4" * 64),
        ("tts_request_identity_hash", "5" * 64),
        ("tts_idempotency_key", "forked-tts-identity"),
        ("audio_relative_path", "effects/forked/final.mp3"),
        ("audio_checksum_sha256", "6" * 64),
        ("audio_size_bytes", 129),
        ("audio_duration_ms", 419_999),
    ):
        forked = deepcopy(authority)
        setattr(forked, field, drift)
        with pytest.raises(
            ValidationFailureError,
            match="V2_NARRATION_TIMING_RECOVERY_AUTHORITY_DRIFT",
        ):
            service._assert_authority_matches_scope(forked, scope)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_call_count", 0),
        ("provider_call_count", 2),
        ("tts_retry_count", 1),
        ("recovery_state", "PARTIAL"),
        ("forced_alignment_provider_request_id", None),
        (
            "forced_alignment_provider_request_id_availability",
            "NOT_EXPOSED_BY_ENDPOINT",
        ),
    ],
)
def test_database_rejects_recovery_receipt_count_or_identity_tamper(
    db_session, field: str, value: object
) -> None:
    authority = _raw_authority_values()
    receipt = _raw_receipt_values(authority)
    receipt[field] = value
    db_session.execute(text("SET LOCAL session_replication_role = replica"))
    db_session.execute(_AUTHORITY_INSERT, authority)

    with pytest.raises(DBAPIError):
        db_session.execute(_RECEIPT_INSERT, receipt)


def test_database_recovery_receipt_is_immutable(db_session) -> None:
    authority = _raw_authority_values()
    receipt = _raw_receipt_values(authority)
    db_session.execute(text("SET LOCAL session_replication_role = replica"))
    db_session.execute(_AUTHORITY_INSERT, authority)
    db_session.execute(_RECEIPT_INSERT, receipt)
    db_session.execute(text("SET LOCAL session_replication_role = origin"))

    with pytest.raises(DBAPIError, match="immutable"):
        db_session.execute(
            text(
                "UPDATE v2_narration_timing_recovery_receipts "
                "SET provider_call_count = 0 WHERE id = :id"
            ),
            {"id": receipt["id"]},
        )


def test_authority_seal_accepts_only_blocked_workflow_and_unsettled_budget(
    db_session,
) -> None:
    definition = db_session.scalar(
        text(
            "SELECT pg_get_functiondef("
            "'seal_v2_narration_timing_recovery_authority()'::regprocedure)"
        )
    )
    assert isinstance(definition, str)
    normalized = " ".join(definition.split())
    assert "workflow.state IS DISTINCT FROM 'BLOCKED'" in normalized
    assert "MEDIA_RUNNING" not in normalized
    assert "budget.status NOT IN ('RESERVED', 'SUBMITTED')" in normalized
    assert "SETTLED_" not in normalized


def test_authority_seal_binds_exact_live_package_and_controlled_actor() -> None:
    migration = Path("alembic/versions/0076_v2_narration_timing_recovery.py").read_text(
        encoding="utf-8"
    )

    assert "package_version.status IS DISTINCT FROM 'submitted'" in migration
    assert "package_artifact.status IS DISTINCT FROM 'draft'" in migration
    assert "package_version.status IS DISTINCT FROM 'approved'" not in migration
    assert "package_artifact.status IS DISTINCT FROM 'approved'" not in migration
    assert "script_version.status IS DISTINCT FROM 'approved'" in migration
    assert "script_artifact.status IS DISTINCT FROM 'approved'" in migration
    assert "'6d196d74-7938-5c85-bc10-f25466616258'::uuid" in migration


class _LedgerSession:
    def __init__(self, ledger: SimpleNamespace, project: SimpleNamespace) -> None:
        self.ledger = ledger
        self.project = project

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def scalar(self, _statement: object) -> SimpleNamespace:
        return self.ledger

    def get(self, _model: object, identifier: object) -> SimpleNamespace | None:
        return self.project if identifier == self.project.id else None

    def commit(self) -> None:
        return None

    def refresh(self, _row: object) -> None:
        return None


def test_media_reconciliation_keeps_one_tts_and_one_alignment_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id = uuid.uuid4()
    project = SimpleNamespace(id=uuid.uuid4())
    ledger = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_run_id=workflow_id,
        stage="MEDIA",
        adapter_key="v2-elevenlabs-narration",
        state="FAILED_UNCERTAIN",
        effect_invocation_count=1,
        result_type=None,
        result_id=None,
        result_ref=None,
        result_hash=None,
        result_payload={},
        authority_refs={},
        effect_journal={},
        command_id="live-shaped-media-command",
        completed_at=None,
    )
    fake_session = _LedgerSession(ledger, project)
    adapter = V2ElevenLabsNarrationAdapter(
        workspace_root=tmp_path,
        session_factory=lambda: fake_session,  # type: ignore[arg-type]
        settings=_no_provider_settings(True),  # type: ignore[arg-type]
    )

    @contextmanager
    def no_lock(_command_id: str):
        yield

    monkeypatch.setattr(adapter, "_command_lock", no_lock)
    run = SimpleNamespace(id=workflow_id)
    package = SimpleNamespace()
    script = SimpleNamespace(
        content={"narration_text": "alpha beta"}, content_hash=_HASH_A
    )
    visual = SimpleNamespace()
    monkeypatch.setattr(
        "app.services.v2_elevenlabs_narration._production_inputs",
        lambda _session, _workflow_id: (run, project, package, script, visual),
    )
    sidecar = {
        "caption_ref": "artifact-version://caption",
        "caption_checksum": _HASH_B,
        "caption_relative_path": "effects/media/canonical-captions.srt",
        "subtitle_qc_ref": "artifact-version://subtitle-qc",
        "subtitle_qc_hash": _HASH_C,
        "subtitle_qc_state": "PASS",
    }
    monkeypatch.setattr(
        "app.services.v2_elevenlabs_narration._persist_sidecar_artifacts",
        lambda **_kwargs: deepcopy(sidecar),
    )
    monkeypatch.setattr(
        "app.services.v2_elevenlabs_narration._build_timeline",
        lambda **kwargs: {
            "schema_version": "vcos.canonical-media-timeline.v2",
            "duration_ms": kwargs["audio"]["duration_ms"],
            "scenes": [{"scene_id": "scene-001"}],
        },
    )
    audio = {
        "audio_asset_ref": "v2-elevenlabs://sealed-live-audio",
        "audio_checksum": _HASH_D,
        "audio_relative_path": "effects/media/elevenlabs-final-narration.mp3",
        "duration_ms": 420_000,
        "alignment_method": "ELEVENLABS_FORCED_ALIGNMENT_RECOVERY",
        "provider_request_hash": _HASH_E,
        "provider_request_id": "forced-alignment-request-001",
        "estimated_cost_usd": "1",
        "actual_cost_usd": None,
    }

    result = adapter.reconcile_recovered_media(
        workflow_run_id=workflow_id,
        ledger_id=ledger.id,
        audio=audio,
        recovery_authority_id=uuid.uuid4(),
        recovery_authority_hash=_HASH_F,
    )

    assert ledger.state == "VERIFIED"
    assert ledger.effect_invocation_count == 1
    assert ledger.effect_journal["provider_call_count"] == 2
    assert ledger.effect_journal["tts_provider_call_count"] == 1
    assert ledger.effect_journal["tts_retry_count"] == 0
    assert ledger.effect_journal["forced_alignment_provider_call_count"] == 1
    assert ledger.effect_journal["caption_ref"] == sidecar["caption_ref"]
    assert ledger.effect_journal["subtitle_qc_ref"] == sidecar["subtitle_qc_ref"]
    assert result.result_ref == f"v2-effect://{ledger.id}/canonical-media-timeline"
    assert result.result_payload["subtitle_qc_state"] == "PASS"
    assert result.effect_state.value == "RECONCILED"
    timeline_path = next(tmp_path.glob("effects/*/canonical-media-timeline.json"))
    assert _load_json(timeline_path)["duration_ms"] == 420_000


def test_short_final_caption_is_not_merged_into_overlong_previous_cue() -> None:
    cues = _build_srt_cues(
        [
            {"index": 1, "text": "alpha", "start_ms": 0, "end_ms": 5_900},
            {"index": 2, "text": "omega", "start_ms": 7_000, "end_ms": 7_700},
        ]
    )

    assert len(cues) == 2
    assert all(cue["end_ms"] - cue["start_ms"] <= 7_000 for cue in cues)


class _AudioThenFailTTSClient:
    def __init__(self) -> None:
        self.call_count = 0

    def execute_once(self, *, destination: Path, **_kwargs: object) -> None:
        self.call_count += 1
        destination.write_bytes(b"sealed-live-shaped-elevenlabs-audio")
        raise RuntimeError("fixture lost timestamp response after sealed audio")


class _ExactForcedAlignmentClient:
    def __init__(self) -> None:
        self.call_count = 0
        self.response_capture = None

    @staticmethod
    def _response(normalized: Any, duration_ms: int) -> dict[str, Any]:
        spoken = normalized.spoken_text
        char_step = duration_ms / max(len(spoken), 1)
        words = []
        for token in normalized.spoken_tokens:
            start_ms = token.spoken_span.start / len(spoken) * duration_ms
            end_ms = token.spoken_span.end / len(spoken) * duration_ms
            words.append(
                {
                    "type": "word",
                    "text": token.text,
                    "start_ms": round(start_ms),
                    "end_ms": max(round(end_ms), round(start_ms) + 1),
                }
            )
        return {
            "request_id": "forced-alignment-recovery-001",
            "words": words,
            "characters": [
                {
                    "text": character,
                    "start": index * char_step / 1000,
                    "end": (
                        index * char_step / 1000
                        if character in {",", ".", ":", "-"}
                        else (index + 1) * char_step / 1000
                    ),
                }
                for index, character in enumerate(spoken)
            ],
            "alignment_loss": 0.01,
            "transcript_loss": 0.01,
        }

    def execute_once(
        self,
        *,
        normalized: Any,
        audio_asset_ref: str,
        audio_duration_ms: int,
        response_capture: Callable[[dict[str, Any]], None],
        **_kwargs: object,
    ) -> SimpleNamespace:
        if self.call_count:
            raise AssertionError("forced alignment called twice")
        self.call_count += 1
        response = self._response(normalized, audio_duration_ms)
        captured = _safe_forced_alignment_response_capture(
            response, {"request-id": "forced-alignment-recovery-001"}
        )
        response_capture(captured)
        evidence = ElevenLabsForcedAlignmentResponseParser().parse(
            response=response,
            response_headers={"request-id": "forced-alignment-recovery-001"},
            normalized=normalized,
            audio_asset_ref=audio_asset_ref,
            audio_duration_ms=audio_duration_ms,
        )
        return SimpleNamespace(
            request_hash=ElevenLabsForcedAlignmentRequestBuilder().build(
                audio_asset_ref=audio_asset_ref, normalized=normalized
            )["request_hash"],
            provider_response_hash=captured["content_hash"],
            evidence=evidence,
        )


class _CaptureThenCrashForcedAlignmentClient(_ExactForcedAlignmentClient):
    def execute_once(
        self,
        *,
        normalized: Any,
        audio_duration_ms: int,
        response_capture: Callable[[dict[str, Any]], None],
        **_kwargs: object,
    ) -> SimpleNamespace:
        if self.call_count:
            raise AssertionError("forced alignment called twice")
        self.call_count += 1
        captured = _safe_forced_alignment_response_capture(
            self._response(normalized, audio_duration_ms),
            {"request-id": "forced-alignment-recovery-001"},
        )
        response_capture(captured)
        raise RuntimeError("injected local parser crash after durable capture")


class _FailBeforeResponseCaptureClient:
    def __init__(self) -> None:
        self.call_count = 0

    def execute_once(self, **_kwargs: object) -> SimpleNamespace:
        self.call_count += 1
        raise TimeoutError("provider outcome deliberately unknown")


class _UnexpectedForcedAlignmentClient:
    def __init__(self) -> None:
        self.call_count = 0

    def execute_once(self, **_kwargs: object) -> SimpleNamespace:
        self.call_count += 1
        raise AssertionError("durable recovery replay attempted provider network")


def _controlled_worker_actor():
    return _system_worker_actor(
        "vcos-controlled-recovery",
        permissions={"production.workflow.execute"},
    )


def _recovery_service(
    *,
    factory: sessionmaker,
    session: Any,
    settings: Any,
    client: Any,
    workspace_root: Path,
) -> V2NarrationTimingRecoveryService:
    return V2NarrationTimingRecoveryService(
        session,
        settings=settings,
        session_factory=factory,
        audio_probe=lambda _path: 420_000,
        client=client,
        workspace_root=workspace_root,
    )


_ORIGINAL_LIVE_SHAPED_PAYLOAD = settlement_test._live_shaped_v2_payload


def _compact_live_shaped_payload(run, *, repaired: bool = False) -> dict[str, Any]:
    """Preserve the semantic fixture with human-sized, still-unique tokens."""

    original = _ORIGINAL_LIVE_SHAPED_PAYLOAD(run, repaired=repaired)
    aliases: dict[str, str] = {}

    def base36(value: int) -> str:
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        output = ""
        while value:
            value, remainder = divmod(value, 36)
            output = digits[remainder] + output
        return output or "0"

    def compact(text_value: str) -> str:
        value = re.sub(r"(?:repaired|original)section(\d+)", r"part\1", text_value)
        value = re.sub(r"sentence(\d+)", r"item\1", value)

        def replace_token(match: re.Match[str]) -> str:
            raw = match.group(0)
            if raw not in aliases:
                aliases[raw] = "w" + base36(len(aliases) + 1)
            return aliases[raw]

        return re.sub(r"s\d+n\d+token\d+", replace_token, value)

    for section in original["sections"]:
        section["narration"] = compact(section["narration"])
    for claim in original["claims"]:
        claim["claim_text"] = compact(claim["claim_text"])
    return original


def _build_live_shaped_failed_media(
    *,
    db_session,
    engine,
    qualification_factory,
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,
) -> tuple[uuid.UUID, _AudioThenFailTTSClient, Any]:
    monkeypatch.setattr(
        settlement_test,
        "_live_shaped_v2_payload",
        _compact_live_shaped_payload,
    )
    _install_research_context_source(monkeypatch)
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )
    _install_ready_finalization_authorities(monkeypatch)
    child = ScriptVerifierSettlementRecoveryService(
        db_session, now=lambda: lineage.settlement_now
    ).create(source_qualification_run_id=lineage.child.id)
    _admission, workflow = LongFormCadenceService(
        db_session, now=lambda: lineage.settlement_now
    ).finalize_qualified_script_run(
        script_qualification_run_id=child.id,
        actor=_controlled_worker_actor(),
    )
    db_session.commit()
    _run_exact_worker_to_readiness(engine, workflow.id)

    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    tts = _AudioThenFailTTSClient()
    settings = _no_provider_settings(True)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "offline-fixture-secret")
    media_adapter = V2ElevenLabsNarrationAdapter(
        workspace_root=workspace_root,
        session_factory=factory,
        settings=settings,  # type: ignore[arg-type]
        client=tts,  # type: ignore[arg-type]
    )
    gateway = build_v2_provider_production_gateway(
        adapters={"v2-elevenlabs-narration": media_adapter}
    )
    worker = ProductionWorkflowWorker(
        session_factory=factory,
        worker_id=f"timing-recovery-failure-{workflow.id}",
        handlers=build_default_stage_handler_registry(post_readiness_gateway=gateway),
    )
    with factory() as session:
        media_event_id = session.scalar(
            select(DomainEvent.id).where(
                DomainEvent.workflow_run_id == workflow.id,
                DomainEvent.payload["stage"].astext == "MEDIA",
                DomainEvent.dead_lettered_at.is_(None),
            )
        )
    assert media_event_id is not None
    failed = worker.run_exact_event(event_id=media_event_id)
    assert failed.status == "DEAD_LETTERED"
    assert tts.call_count == 1
    return workflow.id, tts, settings


def test_live_shaped_timing_recovery_is_one_shot_append_only_and_idempotent(
    db_session,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workflow_id, tts, settings = _build_live_shaped_failed_media(
        db_session=db_session,
        engine=engine,
        qualification_factory=QualificationFactory(db_session),
        monkeypatch=monkeypatch,
        workspace_root=tmp_path,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    # Model a process crash immediately after recovery moved the reservation
    # to SUBMITTED but before it could reconcile the MEDIA ledger.  The live
    # incident currently starts RESERVED; this explicit transition exercises
    # the idempotent resume branch that must release row locks even when there
    # is no status change left to commit.
    with factory() as submitted:
        reservation = submitted.scalar(
            select(MR1MonthlyBudgetReservation).where(
                MR1MonthlyBudgetReservation.run_id == workflow_id
            )
        )
        assert reservation is not None
        MR1MonthlyBudgetAuthority(submitted).mark_submitted(reservation.reservation_ref)
        submitted.commit()
    with factory() as before:
        run = before.get(ProductionWorkflowRun, workflow_id)
        budget = before.scalar(
            select(MR1MonthlyBudgetReservation).where(
                MR1MonthlyBudgetReservation.run_id == workflow_id
            )
        )
        ledger = before.scalar(
            select(V2ProductionEffectLedger).where(
                V2ProductionEffectLedger.workflow_run_id == workflow_id,
                V2ProductionEffectLedger.stage == "MEDIA",
            )
        )
        event = before.scalar(
            select(DomainEvent).where(
                DomainEvent.workflow_run_id == workflow_id,
                DomainEvent.command_id == ledger.command_id,
            )
        )
        dead_letter = before.scalar(
            select(DeadLetterJob).where(DeadLetterJob.domain_event_id == event.id)
        )
        assert run.state == "BLOCKED"
        # Recovery must release validation row locks even though it has no
        # RESERVED -> SUBMITTED transition left to commit.
        assert budget is not None and budget.status == "SUBMITTED"
        budget_history = (
            budget.id,
            budget.reservation_ref,
            budget.reserved_amount,
            budget.environment_cap,
            budget.company_cap,
            budget.channel_cap,
            deepcopy(budget.provider_caps_json),
            budget.status,
            budget.actual_amount,
            deepcopy(budget.provider_actuals_json),
            budget.submitted_at,
            budget.settled_at,
        )
        assert ledger.state == "FAILED_UNCERTAIN"
        assert ledger.effect_invocation_count == 1
        assert event.dead_lettered_at is not None
        assert dead_letter.replay_state == "NOT_REPLAYABLE"
        media_command_id = ledger.command_id
        effect_dir = (
            tmp_path
            / "effects"
            / hashlib.sha256(media_command_id.encode("utf-8")).hexdigest()
        )
        assert not (effect_dir / "elevenlabs-provider-response-journal.json").exists()
        assert not (effect_dir / "elevenlabs-narration-receipt.json").exists()
        assert (
            before.scalar(
                select(func.count())
                .select_from(V2ProductionEffectLedger)
                .where(
                    V2ProductionEffectLedger.workflow_run_id == workflow_id,
                    V2ProductionEffectLedger.stage.in_(("RENDER", "QC", "ARCHIVE")),
                )
            )
            == 0
        )
        assert (
            before.scalar(
                select(func.count())
                .select_from(WorkflowCommandReceipt)
                .where(
                    WorkflowCommandReceipt.workflow_run_id == workflow_id,
                    WorkflowCommandReceipt.stage.in_(
                        ("MEDIA", "RENDER", "QC", "ARCHIVE", "FINALIZE")
                    ),
                )
            )
            == 0
        )
        history = (
            event.id,
            event.dead_lettered_at,
            event.last_error_code,
            dead_letter.id,
            dead_letter.created_at,
            dead_letter.reason_code,
            dead_letter.replay_state,
        )

    forced = _ExactForcedAlignmentClient()
    with factory() as recovery_session:
        service = V2NarrationTimingRecoveryService(
            recovery_session,
            settings=settings,  # type: ignore[arg-type]
            session_factory=factory,
            audio_probe=lambda _path: 420_000,
            client=forced,
            workspace_root=tmp_path,
        )
        result = service.recover(workflow_id, _controlled_worker_actor())
        replay = service.recover(workflow_id, _controlled_worker_actor())

    assert result.replayed is False
    assert replay.replayed is True
    assert replay.authority_id == result.authority_id
    assert replay.receipt_id == result.receipt_id
    assert result.workflow_state == "RENDER_PENDING"
    assert result.next_domain_event_id is not None
    assert forced.call_count == 1
    assert tts.call_count == 1

    response_journal = _load_json(
        effect_dir / "elevenlabs-forced-alignment-response-journal.json"
    )
    recovered_narration = _load_json(effect_dir / "elevenlabs-narration-receipt.json")
    assert response_journal["capture"]["secret_values_exposed"] is False
    assert recovered_narration["alignment_method"] == (
        "ELEVENLABS_FORCED_ALIGNMENT_RECOVERY"
    )
    assert recovered_narration["usage_metadata"] == {
        "provider": "elevenlabs_forced_alignment",
        "provider_call_count": 1,
        "tts_retry_count": 0,
    }
    alignment_audit = recovered_narration["alignment_audit"]
    assert alignment_audit["exact_raw_character_sequence"] is True
    assert alignment_audit["exact_word_token_coverage"] is True
    assert alignment_audit["provider_zero_duration_character_count"] > 0
    assert alignment_audit["zero_duration_character_timing_synthesized"] is False
    timing_metadata = recovered_narration["timing_seed"]["response_metadata"]
    assert timing_metadata["alignment_audit"] == alignment_audit
    assert (
        len(timing_metadata["caption_timed_words"])
        == alignment_audit["canonical_spoken_token_count"]
    )
    assert len(
        recovered_narration["timing_seed"]["normalized_character_alignment"]
    ) == (
        alignment_audit["raw_character_count"]
        - alignment_audit["provider_zero_duration_character_count"]
    )
    for expected_path in (
        "elevenlabs-forced-alignment-request-journal.json",
        "elevenlabs-forced-alignment-response-journal.json",
        "elevenlabs-forced-alignment-evidence.json",
        "elevenlabs-recovered-timing-seed.json",
        "elevenlabs-narration-receipt.json",
        "canonical-captions.srt",
        "canonical-media-timeline.json",
        "effect-journal.json",
    ):
        assert (effect_dir / expected_path).is_file(), expected_path
    assert not (effect_dir / "elevenlabs-provider-response-journal.json").exists()

    with factory() as check:
        run = check.get(ProductionWorkflowRun, workflow_id)
        ledger = check.get(V2ProductionEffectLedger, result.media_effect_ledger_id)
        event = check.scalar(
            select(DomainEvent).where(
                DomainEvent.workflow_run_id == workflow_id,
                DomainEvent.command_id == ledger.command_id,
            )
        )
        dead_letter = check.scalar(
            select(DeadLetterJob).where(DeadLetterJob.domain_event_id == event.id)
        )
        authority = check.get(V2NarrationTimingRecoveryAuthority, result.authority_id)
        budget = check.scalar(
            select(MR1MonthlyBudgetReservation).where(
                MR1MonthlyBudgetReservation.run_id == workflow_id
            )
        )
        receipt = check.get(V2NarrationTimingRecoveryReceipt, result.receipt_id)
        media_receipt = check.get(
            WorkflowCommandReceipt, result.workflow_command_receipt_id
        )
        assert run.state == "RENDER_PENDING"
        assert run.current_stage == "RENDER"
        assert run.canonical_media_timeline_ref == ledger.result_ref
        assert ledger.state == "VERIFIED"
        assert ledger.effect_invocation_count == 1
        assert ledger.effect_journal["tts_retry_count"] == 0
        assert ledger.effect_journal["forced_alignment_provider_call_count"] == 1
        assert ledger.effect_journal["provider_call_count"] == 2
        assert authority.max_tts_retries == 0
        assert authority.max_forced_alignment_submissions == 1
        assert authority.budget_reservation_id == budget.id
        assert authority.budget_reservation_ref == budget.reservation_ref
        assert (
            authority.budget_authority_hash
            == (budget.capacity_evidence_json["content_hash"])
        )
        assert (
            budget.id,
            budget.reservation_ref,
            budget.reserved_amount,
            budget.environment_cap,
            budget.company_cap,
            budget.channel_cap,
            budget.provider_caps_json,
            budget.status,
            budget.actual_amount,
            budget.provider_actuals_json,
            budget.submitted_at,
            budget.settled_at,
        ) == budget_history
        assert receipt.provider_call_count == 1
        assert receipt.tts_retry_count == 0
        assert receipt.canonical_media_timeline_hash == ledger.result_hash
        assert (
            receipt.forced_alignment_provider_response_hash
            == (response_journal["capture"]["content_hash"])
        )
        assert (
            receipt.forced_alignment_provider_response_hash
            != (response_journal["content_hash"])
        )
        assert media_receipt.effect_state == "RECONCILED"
        assert media_receipt.result_hash == ledger.result_hash
        assert media_receipt.result_payload["timing_recovery_receipt_id"] == str(
            receipt.id
        )
        assert media_receipt.result_payload["timing_recovery_receipt_hash"] == (
            receipt.receipt_hash
        )
        assert media_receipt.result_payload["recovered_media_domain_event_id"] == str(
            event.id
        )
        assert media_receipt.result_payload[
            "recovered_media_dead_letter_job_id"
        ] == str(dead_letter.id)
        assert (
            event.id,
            event.dead_lettered_at,
            event.last_error_code,
            dead_letter.id,
            dead_letter.created_at,
            dead_letter.reason_code,
            dead_letter.replay_state,
        ) == history
        assert (
            check.scalar(
                select(func.count())
                .select_from(ProviderAttempt)
                .where(
                    ProviderAttempt.target_id.in_([workflow_id, run.video_project_id])
                )
            )
            == 0
        )
        assert (
            check.scalar(
                select(func.count()).select_from(V2NarrationTimingRecoveryAuthority)
            )
            == 1
        )
        assert (
            check.scalar(
                select(func.count()).select_from(V2NarrationTimingRecoveryReceipt)
            )
            == 1
        )
        render_events = list(
            check.scalars(
                select(DomainEvent).where(
                    DomainEvent.workflow_run_id == workflow_id,
                    DomainEvent.payload["stage"].astext == "RENDER",
                )
            ).all()
        )
        assert len(render_events) == 1
        assert render_events[0].id == result.next_domain_event_id
        assert render_events[0].delivered_at is None
        assert (
            check.scalar(
                select(func.count())
                .select_from(V2ProductionEffectLedger)
                .where(
                    V2ProductionEffectLedger.workflow_run_id == workflow_id,
                    V2ProductionEffectLedger.stage.in_(("RENDER", "QC", "ARCHIVE")),
                )
            )
            == 0
        )
        for forbidden_effect in (
            FinalMediaRef,
            CloudMediaRef,
            PublishHandoffPackage,
            ManualPublishConfirmation,
            UploadedVideo,
        ):
            assert check.scalar(select(func.count()).select_from(forbidden_effect)) == 0


def test_captured_response_recovers_offline_after_local_parser_crash(
    db_session,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workflow_id, _tts, settings = _build_live_shaped_failed_media(
        db_session=db_session,
        engine=engine,
        qualification_factory=QualificationFactory(db_session),
        monkeypatch=monkeypatch,
        workspace_root=tmp_path,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    crashed = _CaptureThenCrashForcedAlignmentClient()
    with factory() as session:
        service = _recovery_service(
            factory=factory,
            session=session,
            settings=settings,
            client=crashed,
            workspace_root=tmp_path,
        )
        with pytest.raises(RuntimeError, match="local parser crash"):
            service.recover(workflow_id, _controlled_worker_actor())
    assert crashed.call_count == 1

    offline = _UnexpectedForcedAlignmentClient()
    with factory() as session:
        recovered = _recovery_service(
            factory=factory,
            session=session,
            settings=settings,
            client=offline,
            workspace_root=tmp_path,
        ).recover(workflow_id, _controlled_worker_actor())

    assert recovered.workflow_state == "RENDER_PENDING"
    assert offline.call_count == 0


def test_request_without_response_is_uncertain_and_never_resubmits(
    db_session,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workflow_id, _tts, settings = _build_live_shaped_failed_media(
        db_session=db_session,
        engine=engine,
        qualification_factory=QualificationFactory(db_session),
        monkeypatch=monkeypatch,
        workspace_root=tmp_path,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    uncertain = _FailBeforeResponseCaptureClient()
    with factory() as session:
        service = _recovery_service(
            factory=factory,
            session=session,
            settings=settings,
            client=uncertain,
            workspace_root=tmp_path,
        )
        with pytest.raises(TimeoutError, match="outcome deliberately unknown"):
            service.recover(workflow_id, _controlled_worker_actor())
    assert uncertain.call_count == 1

    offline = _UnexpectedForcedAlignmentClient()
    with factory() as session:
        with pytest.raises(
            ValidationFailureError,
            match="V2_NARRATION_TIMING_RECOVERY_OUTCOME_UNKNOWN",
        ):
            _recovery_service(
                factory=factory,
                session=session,
                settings=settings,
                client=offline,
                workspace_root=tmp_path,
            ).recover(workflow_id, _controlled_worker_actor())
    assert offline.call_count == 0


def test_verified_ledger_crash_resumes_without_second_provider_submission(
    db_session,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workflow_id, _tts, settings = _build_live_shaped_failed_media(
        db_session=db_session,
        engine=engine,
        qualification_factory=QualificationFactory(db_session),
        monkeypatch=monkeypatch,
        workspace_root=tmp_path,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    forced = _ExactForcedAlignmentClient()
    with factory() as session:
        service = _recovery_service(
            factory=factory,
            session=session,
            settings=settings,
            client=forced,
            workspace_root=tmp_path,
        )
        reconcile = service.adapter.reconcile_recovered_media

        def crash_after_verified(**kwargs: Any) -> None:
            reconcile(**kwargs)
            raise RuntimeError("injected crash after durable VERIFIED ledger")

        monkeypatch.setattr(
            service.adapter,
            "reconcile_recovered_media",
            crash_after_verified,
        )
        with pytest.raises(RuntimeError, match="durable VERIFIED ledger"):
            service.recover(workflow_id, _controlled_worker_actor())
    assert forced.call_count == 1
    with factory() as check:
        ledger = check.scalar(
            select(V2ProductionEffectLedger).where(
                V2ProductionEffectLedger.workflow_run_id == workflow_id,
                V2ProductionEffectLedger.stage == "MEDIA",
            )
        )
        assert ledger is not None and ledger.state == "VERIFIED"
        assert (
            check.scalar(
                select(func.count()).select_from(V2NarrationTimingRecoveryReceipt)
            )
            == 0
        )

    offline = _UnexpectedForcedAlignmentClient()
    with factory() as session:
        recovered = _recovery_service(
            factory=factory,
            session=session,
            settings=settings,
            client=offline,
            workspace_root=tmp_path,
        ).recover(workflow_id, _controlled_worker_actor())
    assert recovered.workflow_state == "RENDER_PENDING"
    assert offline.call_count == 0


def test_recovery_advisory_lock_rejects_a_concurrent_second_caller(
    engine,
    tmp_path: Path,
) -> None:
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    workflow_id = uuid.uuid4()
    with factory() as first_session, factory() as second_session:
        first = _recovery_service(
            factory=factory,
            session=first_session,
            settings=_no_provider_settings(True),
            client=_UnexpectedForcedAlignmentClient(),
            workspace_root=tmp_path,
        )
        second = _recovery_service(
            factory=factory,
            session=second_session,
            settings=_no_provider_settings(True),
            client=_UnexpectedForcedAlignmentClient(),
            workspace_root=tmp_path,
        )
        with first._recovery_lock(workflow_id):
            with pytest.raises(
                ValidationFailureError,
                match="V2_NARRATION_TIMING_RECOVERY_IN_PROGRESS",
            ):
                with second._recovery_lock(workflow_id):
                    pytest.fail("both recovery callers acquired the one-shot lock")
