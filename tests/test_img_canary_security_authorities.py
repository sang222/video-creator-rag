from __future__ import annotations

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest

from app.contracts.img_canary_security import (
    IMG_CANARY_V2_AUTHORIZATION_REF,
    IMG_CANARY_V2_TASK_KEY,
)
from app.services.img_canary_security import (
    IMGCanaryCredentialRotationAuthority,
    IMGCanaryMonthlyBudgetAuthority,
    IMGCanarySecurityAuthorityError,
    IMGCanaryTaskAuthorizationStore,
)


NOW = datetime(2026, 7, 18, 6, 30, tzinfo=UTC)
TASK_KEY = "img-canary-master-prompt-2026-07-18"
AUTHORIZATION_REF = "authorization://img-canary/master-prompt/one-paid-request"
BUDGET_REF = "budget://small-team-ai/2026-07/img-canary"
FINGERPRINT_A = "a" * 64
FINGERPRINT_B = "b" * 64


def _task_store(path: Path) -> IMGCanaryTaskAuthorizationStore:
    return IMGCanaryTaskAuthorizationStore(path)


def _budget_store(path: Path) -> IMGCanaryMonthlyBudgetAuthority:
    return IMGCanaryMonthlyBudgetAuthority(path)


def _initialize_budget(
    path: Path,
    *,
    cap: Decimal = Decimal("0.15"),
    opening_spend: Decimal = Decimal("0"),
) -> IMGCanaryMonthlyBudgetAuthority:
    store = _budget_store(path)
    store.initialize(
        authority_ref=BUDGET_REF,
        billing_period="2026-07",
        dedicated_cap_usd=cap,
        opening_spend_usd=opening_spend,
        per_request_hard_cap_usd=Decimal("0.15"),
        now=NOW,
    )
    return store


def test_task_authorization_is_terminal_across_distinct_runs(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "task-authorization.json"
    store = _task_store(path)
    available = store.initialize(
        task_key=TASK_KEY,
        authorization_ref=AUTHORIZATION_REF,
        now=NOW,
    )

    assert available.status == "AVAILABLE"
    claimed = store.claim(
        run_id="img-canary-run-a",
        request_fingerprint=FINGERPRINT_A,
        now=NOW + timedelta(seconds=1),
    )
    assert claimed.status == "CLAIMED"
    assert claimed.claim_ref

    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_TASK_AUTHORIZATION_ALREADY_CLAIMED",
    ):
        _task_store(path).claim(
            run_id="img-canary-run-b",
            request_fingerprint=FINGERPRINT_B,
            now=NOW + timedelta(seconds=2),
        )

    consumed = store.consume(
        run_id="img-canary-run-a",
        request_fingerprint=FINGERPRINT_A,
        completion_status="PROVIDER_ATTEMPT_COMPLETED",
        now=NOW + timedelta(seconds=3),
    )
    assert consumed.status == "CONSUMED"
    assert consumed.completion_status == "PROVIDER_ATTEMPT_COMPLETED"
    assert store.consume(
        run_id="img-canary-run-a",
        request_fingerprint=FINGERPRINT_A,
        completion_status="PROVIDER_ATTEMPT_COMPLETED",
        now=NOW + timedelta(seconds=4),
    ).content_hash == consumed.content_hash

    with pytest.raises(IMGCanarySecurityAuthorityError):
        store.initialize(
            task_key="different-task",
            authorization_ref=AUTHORIZATION_REF,
            now=NOW,
        )


def test_v2_task_authority_consumes_at_submit_with_exact_claim_and_body_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "v2-task-authorization.json"
    run_id = "img-canary-v2-20260718T120000Z-a1b2c3d4"
    prompt_hash = "c" * 64
    body_hash = "d" * 64
    approval_hash = "e" * 64
    store = _task_store(path)
    store.initialize(
        task_key=IMG_CANARY_V2_TASK_KEY,
        authorization_ref=IMG_CANARY_V2_AUTHORIZATION_REF,
        approval_version="V2",
        approved_run_id=run_id,
        approved_request_fingerprint=FINGERPRINT_A,
        approved_prompt_hash=prompt_hash,
        approved_serialized_body_hash=body_hash,
        approved_scoped_approval_hash=approval_hash,
        now=NOW,
    )
    claimed = store.claim(
        run_id=run_id,
        request_fingerprint=FINGERPRINT_A,
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_TASK_AUTHORIZATION_SERIALIZED_BODY_MISMATCH",
    ):
        store.consume(
            run_id=run_id,
            request_fingerprint=FINGERPRINT_A,
            completion_status="PROVIDER_ATTEMPT_SUBMITTED",
            now=NOW + timedelta(seconds=2),
            expected_claimed_content_hash=claimed.content_hash,
            expected_serialized_body_hash="f" * 64,
            expected_scoped_approval_hash=approval_hash,
        )
    assert store.load().status == "CLAIMED"

    consumed = store.consume(
        run_id=run_id,
        request_fingerprint=FINGERPRINT_A,
        completion_status="PROVIDER_ATTEMPT_SUBMITTED",
        now=NOW + timedelta(seconds=2),
        expected_claimed_content_hash=claimed.content_hash,
        expected_serialized_body_hash=body_hash,
        expected_scoped_approval_hash=approval_hash,
    )
    assert consumed.status == "CONSUMED"
    assert consumed.completion_status == "PROVIDER_ATTEMPT_SUBMITTED"


def test_task_authorization_claim_is_atomic_under_concurrency(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "task-authorization.json"
    _task_store(path).initialize(
        task_key=TASK_KEY,
        authorization_ref=AUTHORIZATION_REF,
        now=NOW,
    )
    barrier = Barrier(2)

    def claim(run_id: str, fingerprint: str) -> str:
        barrier.wait()
        try:
            return _task_store(path).claim(
                run_id=run_id,
                request_fingerprint=fingerprint,
                now=NOW + timedelta(seconds=1),
            ).status
        except IMGCanarySecurityAuthorityError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda values: claim(*values),
                (("img-canary-run-a", FINGERPRINT_A), ("img-canary-run-b", FINGERPRINT_B)),
            )
        )

    assert results.count("CLAIMED") == 1
    assert results.count("IMG_CANARY_TASK_AUTHORIZATION_ALREADY_CLAIMED") == 1
    assert _task_store(path).load().status == "CLAIMED"


def test_atomic_state_write_fsyncs_and_uses_private_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import img_canary_security as security

    calls: list[int] = []
    original_fsync = security.os.fsync

    def recording_fsync(descriptor: int) -> None:
        calls.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(security.os, "fsync", recording_fsync)
    path = tmp_path / "runtime" / "task-authorization.json"
    _task_store(path).initialize(
        task_key=TASK_KEY,
        authorization_ref=AUTHORIZATION_REF,
        now=NOW,
    )

    assert len(calls) >= 4  # parent, new lock, lock parent, data file, replace parent
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.part"))
    assert not list(path.parent.glob(".*.part"))


def test_credential_rotation_requires_a_different_live_key_and_never_persists_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "compromised-credential.json"
    authority = IMGCanaryCredentialRotationAuthority(path)
    compromised = "compromised-secret-material-123"
    replacement = "replacement-secret-material-456"
    record = authority.record_compromised(
        credential=compromised,
        incident_ref="incident://img-canary/exposed-credential/2026-07-18",
        now=NOW,
    )

    persisted = path.read_text(encoding="utf-8")
    assert compromised not in persisted
    assert replacement not in persisted
    assert record.compromised_fingerprint_sha256 in persisted

    blocked = authority.verify_rotation(
        current_credential=compromised,
        rotation_ref="rotation://img-canary/security-ticket/SEC-2026-0718",
        now=NOW + timedelta(seconds=1),
    )
    passed = authority.verify_rotation(
        current_credential=replacement,
        rotation_ref="rotation://img-canary/security-ticket/SEC-2026-0718",
        now=NOW + timedelta(seconds=2),
    )

    assert blocked.status == "BLOCKED"
    assert blocked.fingerprint_changed is False
    assert "IMG_CANARY_CREDENTIAL_ROTATION_REQUIRED" in blocked.blocker_reason_codes
    assert passed.status == "PASS"
    assert passed.fingerprint_changed is True
    safe_evidence = passed.model_dump_json()
    assert compromised not in safe_evidence
    assert replacement not in safe_evidence
    assert "current_fingerprint" not in safe_evidence


def test_credential_authority_is_fail_closed_when_record_is_missing_or_conflicts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "compromised-credential.json"
    authority = IMGCanaryCredentialRotationAuthority(path)
    evidence = authority.verify_rotation(
        current_credential="replacement-secret-material",
        rotation_ref="rotation://img-canary/security-ticket/SEC-1",
        now=NOW,
    )
    assert evidence.status == "BLOCKED"
    assert "IMG_CANARY_COMPROMISED_CREDENTIAL_RECORD_MISSING" in (
        evidence.blocker_reason_codes
    )

    authority.record_compromised(
        credential="first-compromised-value",
        incident_ref="incident://img-canary/exposure/one",
        now=NOW,
    )
    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_COMPROMISED_CREDENTIAL_RECORD_CONFLICT",
    ):
        authority.record_compromised(
            credential="different-value",
            incident_ref="incident://img-canary/exposure/two",
            now=NOW,
        )


def test_zero_dedicated_budget_blocks_without_creating_a_reservation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "budget.json"
    store = _initialize_budget(path, cap=Decimal("0"))

    evidence = store.reserve(
        run_id="img-canary-run-a",
        request_fingerprint=FINGERPRINT_A,
        request_estimate_usd=Decimal("0.101"),
        now=NOW,
    )

    assert evidence.status == "INSUFFICIENT"
    assert evidence.reservation_ref is None
    ledger = store.load()
    assert ledger.reservations == ()
    assert ledger.available_usd == Decimal("0")


def test_capacity_inspection_is_available_but_never_reserves(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "budget.json"
    store = _initialize_budget(path, cap=Decimal("0.15"))
    before_hash = store.load().content_hash

    evidence = store.inspect_capacity(
        run_id="img-canary-run-a",
        request_fingerprint=FINGERPRINT_A,
        request_estimate_usd=Decimal("0.101"),
        now=NOW,
    )

    assert evidence.status == "AVAILABLE_UNRESERVED"
    assert evidence.reservation_ref is None
    after = store.load()
    assert after.reservations == ()
    assert after.content_hash == before_hash


def test_configured_cap_can_be_adopted_atomically_before_any_reservation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "budget.json"
    store = _initialize_budget(path, cap=Decimal("0"))
    zero_hash = store.load().content_hash

    adopted = store.initialize(
        authority_ref=BUDGET_REF,
        billing_period="2026-07",
        dedicated_cap_usd=Decimal("0.15"),
        opening_spend_usd=Decimal("0"),
        per_request_hard_cap_usd=Decimal("0.15"),
        now=NOW + timedelta(seconds=1),
    )

    assert adopted.dedicated_cap_usd == Decimal("0.15")
    assert adopted.content_hash != zero_hash
    assert store.load().content_hash == adopted.content_hash
    assert store.inspect_capacity(
        run_id="img-canary-run-a",
        request_fingerprint=FINGERPRINT_A,
        request_estimate_usd=Decimal("0.101"),
        now=NOW + timedelta(seconds=1),
    ).status == "AVAILABLE_UNRESERVED"


def test_budget_reservation_is_idempotent_and_spend_is_accounted_once(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "budget.json"
    store = _initialize_budget(
        path, cap=Decimal("0.25"), opening_spend=Decimal("0.02")
    )
    first = store.reserve(
        run_id="img-canary-run-a",
        request_fingerprint=FINGERPRINT_A,
        request_estimate_usd=Decimal("0.101"),
        now=NOW,
    )
    duplicate = _budget_store(path).reserve(
        run_id="img-canary-run-a",
        request_fingerprint=FINGERPRINT_A,
        request_estimate_usd=Decimal("0.101"),
        now=NOW + timedelta(seconds=1),
    )

    assert first.status == "RESERVED"
    assert duplicate.status == "ALREADY_RESERVED"
    assert duplicate.reservation_ref == first.reservation_ref
    assert len(store.load().reservations) == 1

    spent = store.mark_spent(
        reservation_ref=first.reservation_ref or "",
        run_id="img-canary-run-a",
        request_fingerprint=FINGERPRINT_A,
        now=NOW + timedelta(seconds=2),
    )
    assert spent.spent_usd == Decimal("0.121")
    assert spent.reserved_usd == Decimal("0")
    assert spent.available_usd == Decimal("0.129")
    idempotent_spent = store.mark_spent(
        reservation_ref=first.reservation_ref or "",
        run_id="img-canary-run-a",
        request_fingerprint=FINGERPRINT_A,
        now=NOW + timedelta(seconds=3),
    )
    assert idempotent_spent.content_hash == spent.content_hash


def test_budget_capacity_is_atomic_under_concurrent_reservations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "budget.json"
    _initialize_budget(path, cap=Decimal("0.15"))
    barrier = Barrier(2)

    def reserve(run_id: str, fingerprint: str) -> str:
        barrier.wait()
        return _budget_store(path).reserve(
            run_id=run_id,
            request_fingerprint=fingerprint,
            request_estimate_usd=Decimal("0.10"),
            now=NOW,
        ).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda values: reserve(*values),
                (("img-canary-run-a", FINGERPRINT_A), ("img-canary-run-b", FINGERPRINT_B)),
            )
        )

    assert sorted(results) == ["INSUFFICIENT", "RESERVED"]
    ledger = _budget_store(path).load()
    assert len(ledger.reservations) == 1
    assert ledger.reserved_usd == Decimal("0.10")
    assert ledger.available_usd == Decimal("0.05")


def test_budget_authority_parameters_are_immutable_and_tampering_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "budget.json"
    store = _initialize_budget(path)
    reservation = store.reserve(
        run_id="img-canary-run-a",
        request_fingerprint=FINGERPRINT_A,
        request_estimate_usd=Decimal("0.101"),
        now=NOW,
    )
    assert reservation.status == "RESERVED"
    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_BUDGET_CAP_CHANGE_AFTER_RESERVATION_BLOCKED",
    ):
        store.initialize(
            authority_ref=BUDGET_REF,
            billing_period="2026-07",
            dedicated_cap_usd=Decimal("0.14"),
            opening_spend_usd=Decimal("0"),
            per_request_hard_cap_usd=Decimal("0.15"),
            now=NOW + timedelta(seconds=1),
        )

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["dedicated_cap_usd"] = "999"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_SECURITY_AUTHORITY_INVALID",
    ):
        store.load()
