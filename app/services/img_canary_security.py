from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Literal, TypeVar

from pydantic import BaseModel

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.img_canary_security import (
    IMGCanaryBudgetReservation,
    IMGCanaryBudgetReservationEvidence,
    IMGCanaryCompromisedCredentialRecord,
    IMGCanaryCredentialRotationEvidence,
    IMGCanaryMonthlyBudgetAuthorityLedger,
    IMGCanaryTaskAuthorizationLedger,
)


_Model = TypeVar("_Model", bound=BaseModel)


class IMGCanarySecurityAuthorityError(RuntimeError):
    """A durable authority is missing, conflicted, consumed, or invalid."""


def _require_aware(now: datetime, reason: str) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(reason)


def _fingerprint(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


def _new_hashed(model_type: type[_Model], payload: dict[str, object]) -> _Model:
    return model_type(**payload, content_hash=ai_image_stable_hash(payload))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_private_parent(path: Path) -> None:
    missing: list[Path] = []
    cursor = path.parent
    while not cursor.exists():
        missing.append(cursor)
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            continue
        # Persist both the new directory inode and its entry in the parent.
        _fsync_directory(directory)
        _fsync_directory(directory.parent)
    _fsync_directory(path.parent)


def _atomic_write_model(path: Path, model: BaseModel) -> None:
    _ensure_private_parent(path)
    part = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.part")
    serialized = json.dumps(
        model.model_dump(mode="json"),
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    descriptor: int | None = None
    try:
        descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(part, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        part.unlink(missing_ok=True)


class _LockedJSONStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve(strict=False)

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        _ensure_private_parent(self.path)
        lock_path = self.path.with_name(self.path.name + ".lock")
        existed = lock_path.exists()
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if not existed:
                os.fsync(descriptor)
                _fsync_directory(lock_path.parent)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _load(self, model_type: type[_Model]) -> _Model:
        try:
            return model_type.model_validate_json(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise IMGCanarySecurityAuthorityError(
                "IMG_CANARY_SECURITY_AUTHORITY_MISSING"
            ) from exc
        except Exception as exc:
            raise IMGCanarySecurityAuthorityError(
                "IMG_CANARY_SECURITY_AUTHORITY_INVALID"
            ) from exc


class IMGCanaryTaskAuthorizationStore(_LockedJSONStore):
    """Durable, task-wide exactly-one claim store.

    The state path is supplied by the caller. It should live in an ignored,
    access-controlled runtime directory.
    """

    def initialize(
        self,
        *,
        task_key: str,
        authorization_ref: str,
        approval_version: Literal["V2", "V3"] | None = None,
        approved_run_id: str | None = None,
        approved_request_fingerprint: str | None = None,
        approved_prompt_hash: str | None = None,
        approved_serialized_body_hash: str | None = None,
        approved_scoped_approval_hash: str | None = None,
        now: datetime,
    ) -> IMGCanaryTaskAuthorizationLedger:
        _require_aware(now, "IMG_CANARY_TASK_AUTH_TIMEZONE_REQUIRED")
        with self._exclusive_lock():
            if self.path.exists():
                existing = self._load(IMGCanaryTaskAuthorizationLedger)
                if (
                    existing.task_key != task_key
                    or existing.authorization_ref != authorization_ref
                    or existing.approval_version != approval_version
                    or existing.approved_run_id != approved_run_id
                    or existing.approved_request_fingerprint
                    != approved_request_fingerprint
                    or existing.approved_prompt_hash != approved_prompt_hash
                    or existing.approved_serialized_body_hash
                    != approved_serialized_body_hash
                    or existing.approved_scoped_approval_hash
                    != approved_scoped_approval_hash
                ):
                    raise IMGCanarySecurityAuthorityError(
                        "IMG_CANARY_TASK_AUTHORITY_IDENTITY_CONFLICT"
                    )
                return existing
            payload: dict[str, object] = {
                "schema_version": "img-canary-task-authorization/v1",
                "task_key": task_key,
                "authorization_ref": authorization_ref,
                "status": "AVAILABLE",
                "claimed_run_id": None,
                "claimed_request_fingerprint": None,
                "claim_ref": None,
                "completion_status": None,
                "created_at": now,
                "updated_at": now,
                "claimed_at": None,
                "consumed_at": None,
            }
            if approval_version in {"V2", "V3"}:
                payload.update(
                    {
                        "approval_version": approval_version,
                        "approved_run_id": approved_run_id,
                        "approved_request_fingerprint": approved_request_fingerprint,
                        "approved_prompt_hash": approved_prompt_hash,
                        "approved_serialized_body_hash": approved_serialized_body_hash,
                        "approved_scoped_approval_hash": approved_scoped_approval_hash,
                    }
                )
            ledger = _new_hashed(IMGCanaryTaskAuthorizationLedger, payload)
            _atomic_write_model(self.path, ledger)
            return ledger

    def load(self) -> IMGCanaryTaskAuthorizationLedger:
        with self._exclusive_lock():
            return self._load(IMGCanaryTaskAuthorizationLedger)

    def claim(
        self,
        *,
        run_id: str,
        request_fingerprint: str,
        now: datetime,
    ) -> IMGCanaryTaskAuthorizationLedger:
        _require_aware(now, "IMG_CANARY_TASK_AUTH_TIMEZONE_REQUIRED")
        with self._exclusive_lock():
            ledger = self._load(IMGCanaryTaskAuthorizationLedger)
            if ledger.status != "AVAILABLE":
                raise IMGCanarySecurityAuthorityError(
                    "IMG_CANARY_TASK_AUTHORIZATION_ALREADY_CLAIMED"
                )
            if ledger.approval_version in {"V2", "V3"} and (
                ledger.approved_run_id != run_id
                or ledger.approved_request_fingerprint != request_fingerprint
            ):
                raise IMGCanarySecurityAuthorityError(
                    f"IMG_CANARY_{ledger.approval_version}_TASK_AUTHORIZATION_BINDING_MISMATCH"
                )
            if now < ledger.created_at:
                raise ValueError("IMG_CANARY_TASK_AUTH_CLAIM_TIME_INVALID")
            claim_digest = ai_image_stable_hash(
                {
                    "task_key": ledger.task_key,
                    "authorization_ref": ledger.authorization_ref,
                    "run_id": run_id,
                    "request_fingerprint": request_fingerprint,
                }
            )
            payload = ledger.content_hash_payload()
            payload.update(
                {
                    "status": "CLAIMED",
                    "claimed_run_id": run_id,
                    "claimed_request_fingerprint": request_fingerprint,
                    "claim_ref": f"claim://img-canary/{claim_digest}",
                    "updated_at": now,
                    "claimed_at": now,
                }
            )
            claimed = _new_hashed(IMGCanaryTaskAuthorizationLedger, payload)
            _atomic_write_model(self.path, claimed)
            return claimed

    def consume(
        self,
        *,
        run_id: str,
        request_fingerprint: str,
        completion_status: Literal[
            "PROVIDER_ATTEMPT_SUBMITTED",
            "PROVIDER_ATTEMPT_COMPLETED",
            "PROVIDER_ATTEMPT_FAILED",
            "FAIL_CLOSED_AFTER_CLAIM",
        ],
        now: datetime,
        expected_claimed_content_hash: str | None = None,
        expected_serialized_body_hash: str | None = None,
        expected_scoped_approval_hash: str | None = None,
    ) -> IMGCanaryTaskAuthorizationLedger:
        _require_aware(now, "IMG_CANARY_TASK_AUTH_TIMEZONE_REQUIRED")
        with self._exclusive_lock():
            ledger = self._load(IMGCanaryTaskAuthorizationLedger)
            if (
                ledger.claimed_run_id != run_id
                or ledger.claimed_request_fingerprint != request_fingerprint
            ):
                raise IMGCanarySecurityAuthorityError(
                    "IMG_CANARY_TASK_AUTHORIZATION_CLAIM_MISMATCH"
                )
            if (
                expected_claimed_content_hash is not None
                and ledger.content_hash != expected_claimed_content_hash
            ):
                raise IMGCanarySecurityAuthorityError(
                    "IMG_CANARY_TASK_AUTHORIZATION_CONTENT_HASH_MISMATCH"
                )
            if (
                expected_serialized_body_hash is not None
                and ledger.approved_serialized_body_hash
                != expected_serialized_body_hash
            ):
                raise IMGCanarySecurityAuthorityError(
                    "IMG_CANARY_TASK_AUTHORIZATION_SERIALIZED_BODY_MISMATCH"
                )
            if (
                expected_scoped_approval_hash is not None
                and ledger.approved_scoped_approval_hash
                != expected_scoped_approval_hash
            ):
                raise IMGCanarySecurityAuthorityError(
                    "IMG_CANARY_TASK_AUTHORIZATION_SCOPED_APPROVAL_MISMATCH"
                )
            if ledger.status == "CONSUMED":
                if ledger.completion_status != completion_status:
                    raise IMGCanarySecurityAuthorityError(
                        "IMG_CANARY_TASK_AUTHORIZATION_COMPLETION_CONFLICT"
                    )
                return ledger
            if ledger.status != "CLAIMED":
                raise IMGCanarySecurityAuthorityError(
                    "IMG_CANARY_TASK_AUTHORIZATION_NOT_CLAIMED"
                )
            assert ledger.claimed_at is not None
            if now < ledger.claimed_at:
                raise ValueError("IMG_CANARY_TASK_AUTH_CONSUMED_TIME_INVALID")
            payload = ledger.content_hash_payload()
            payload.update(
                {
                    "status": "CONSUMED",
                    "completion_status": completion_status,
                    "updated_at": now,
                    "consumed_at": now,
                }
            )
            consumed = _new_hashed(IMGCanaryTaskAuthorizationLedger, payload)
            _atomic_write_model(self.path, consumed)
            return consumed


class IMGCanaryCredentialRotationAuthority(_LockedJSONStore):
    """Records one compromised fingerprint and verifies a replacement key."""

    def record_compromised(
        self,
        *,
        credential: str,
        incident_ref: str,
        now: datetime,
    ) -> IMGCanaryCompromisedCredentialRecord:
        _require_aware(now, "IMG_CANARY_CREDENTIAL_INCIDENT_TIMEZONE_REQUIRED")
        if not credential or credential != credential.strip():
            raise ValueError("IMG_CANARY_COMPROMISED_CREDENTIAL_INVALID")
        fingerprint = _fingerprint(credential)
        with self._exclusive_lock():
            if self.path.exists():
                existing = self._load(IMGCanaryCompromisedCredentialRecord)
                if not (
                    hmac.compare_digest(
                        existing.compromised_fingerprint_sha256, fingerprint
                    )
                    and existing.incident_ref == incident_ref
                ):
                    raise IMGCanarySecurityAuthorityError(
                        "IMG_CANARY_COMPROMISED_CREDENTIAL_RECORD_CONFLICT"
                    )
                return existing
            payload: dict[str, object] = {
                "schema_version": "img-canary-compromised-credential/v1",
                "provider": "google_gemini_image",
                "credential_kind": "GEMINI_API_KEY",
                "incident_ref": incident_ref,
                "compromised_fingerprint_sha256": fingerprint,
                "recorded_at": now,
            }
            record = _new_hashed(IMGCanaryCompromisedCredentialRecord, payload)
            _atomic_write_model(self.path, record)
            return record

    def load(self) -> IMGCanaryCompromisedCredentialRecord:
        with self._exclusive_lock():
            return self._load(IMGCanaryCompromisedCredentialRecord)

    def verify_rotation(
        self,
        *,
        current_credential: str | None,
        rotation_ref: str,
        now: datetime,
    ) -> IMGCanaryCredentialRotationEvidence:
        _require_aware(now, "IMG_CANARY_CREDENTIAL_ROTATION_TIMEZONE_REQUIRED")
        configured = bool(
            current_credential
            and current_credential == current_credential.strip()
        )
        current_fingerprint = (
            _fingerprint(current_credential) if configured and current_credential else None
        )
        with self._exclusive_lock():
            record: IMGCanaryCompromisedCredentialRecord | None
            try:
                record = self._load(IMGCanaryCompromisedCredentialRecord)
            except IMGCanarySecurityAuthorityError:
                record = None
            changed = bool(
                record
                and current_fingerprint
                and not hmac.compare_digest(
                    record.compromised_fingerprint_sha256, current_fingerprint
                )
            )
            blockers: list[str] = []
            if record is None:
                blockers.append("IMG_CANARY_COMPROMISED_CREDENTIAL_RECORD_MISSING")
            if not configured:
                blockers.append("IMG_CANARY_CREDENTIAL_NOT_CONFIGURED")
            elif not changed:
                blockers.append("IMG_CANARY_CREDENTIAL_ROTATION_REQUIRED")
            comparison_hash = ai_image_stable_hash(
                {
                    "compromised_record_hash": record.content_hash if record else None,
                    "current_fingerprint_commitment": ai_image_stable_hash(
                        {
                            "current_fingerprint": current_fingerprint
                            if current_fingerprint
                            else "MISSING"
                        }
                    ),
                }
            )
            payload: dict[str, object] = {
                "schema_version": "img-canary-credential-rotation-evidence/v1",
                "provider": "google_gemini_image",
                "credential_kind": "GEMINI_API_KEY",
                "status": "PASS" if not blockers else "BLOCKED",
                "rotation_ref": rotation_ref,
                "incident_ref": record.incident_ref if record else None,
                "compromised_record_hash": record.content_hash if record else None,
                "credential_configured": configured,
                "fingerprint_changed": changed,
                "fingerprint_comparison_hash": comparison_hash,
                "blocker_reason_codes": tuple(blockers),
                "checked_at": now,
            }
            return _new_hashed(IMGCanaryCredentialRotationEvidence, payload)


class IMGCanaryMonthlyBudgetAuthority(_LockedJSONStore):
    """Shared monthly USD ledger with atomic, idempotent reservations."""

    def initialize(
        self,
        *,
        authority_ref: str,
        billing_period: str,
        dedicated_cap_usd: Decimal,
        opening_spend_usd: Decimal,
        per_request_hard_cap_usd: Decimal,
        now: datetime,
    ) -> IMGCanaryMonthlyBudgetAuthorityLedger:
        _require_aware(now, "IMG_CANARY_BUDGET_TIMEZONE_REQUIRED")
        if now.strftime("%Y-%m") != billing_period:
            raise ValueError("IMG_CANARY_BUDGET_BILLING_PERIOD_TIME_MISMATCH")
        with self._exclusive_lock():
            if self.path.exists():
                existing = self._load(IMGCanaryMonthlyBudgetAuthorityLedger)
                identity = (
                    existing.authority_ref,
                    existing.billing_period,
                    existing.opening_spend_usd,
                    existing.per_request_hard_cap_usd,
                )
                requested = (
                    authority_ref,
                    billing_period,
                    opening_spend_usd,
                    per_request_hard_cap_usd,
                )
                if identity != requested:
                    raise IMGCanarySecurityAuthorityError(
                        "IMG_CANARY_BUDGET_AUTHORITY_IDENTITY_CONFLICT"
                    )
                if existing.dedicated_cap_usd != dedicated_cap_usd:
                    if existing.reservations:
                        raise IMGCanarySecurityAuthorityError(
                            "IMG_CANARY_BUDGET_CAP_CHANGE_AFTER_RESERVATION_BLOCKED"
                        )
                    if now < existing.updated_at:
                        raise ValueError("IMG_CANARY_BUDGET_TIME_INVALID")
                    payload = existing.model_dump(
                        mode="python", exclude={"content_hash"}
                    )
                    payload.update(
                        {
                            "dedicated_cap_usd": dedicated_cap_usd,
                            "updated_at": now,
                        }
                    )
                    adopted = _new_hashed(
                        IMGCanaryMonthlyBudgetAuthorityLedger, payload
                    )
                    _atomic_write_model(self.path, adopted)
                    return adopted
                return existing
            payload: dict[str, object] = {
                "schema_version": "img-canary-monthly-budget-authority/v1",
                "authority_ref": authority_ref,
                "channel_key": "small-team-ai",
                "billing_period": billing_period,
                "currency": "USD",
                "dedicated_cap_usd": dedicated_cap_usd,
                "opening_spend_usd": opening_spend_usd,
                "per_request_hard_cap_usd": per_request_hard_cap_usd,
                "reservations": (),
                "created_at": now,
                "updated_at": now,
            }
            ledger = _new_hashed(IMGCanaryMonthlyBudgetAuthorityLedger, payload)
            _atomic_write_model(self.path, ledger)
            return ledger

    def load(self) -> IMGCanaryMonthlyBudgetAuthorityLedger:
        with self._exclusive_lock():
            return self._load(IMGCanaryMonthlyBudgetAuthorityLedger)

    def inspect_capacity(
        self,
        *,
        run_id: str,
        request_fingerprint: str,
        request_estimate_usd: Decimal,
        now: datetime,
    ) -> IMGCanaryBudgetReservationEvidence:
        """Return current capacity without creating or changing a reservation."""

        _require_aware(now, "IMG_CANARY_BUDGET_TIMEZONE_REQUIRED")
        with self._exclusive_lock():
            ledger = self._load(IMGCanaryMonthlyBudgetAuthorityLedger)
            self._validate_reservation_request(
                ledger=ledger,
                request_estimate_usd=request_estimate_usd,
                now=now,
            )
            existing_run = next(
                (item for item in ledger.reservations if item.run_id == run_id), None
            )
            if existing_run is not None:
                if (
                    existing_run.request_fingerprint != request_fingerprint
                    or existing_run.amount_usd != request_estimate_usd
                ):
                    raise IMGCanarySecurityAuthorityError(
                        "IMG_CANARY_BUDGET_RUN_RESERVATION_CONFLICT"
                    )
                status: Literal["ALREADY_RESERVED", "ALREADY_SPENT"] = (
                    "ALREADY_RESERVED"
                    if existing_run.status == "RESERVED"
                    else "ALREADY_SPENT"
                )
                return self._evidence(
                    ledger=ledger,
                    run_id=run_id,
                    request_fingerprint=request_fingerprint,
                    request_estimate_usd=request_estimate_usd,
                    status=status,
                    reservation_ref=existing_run.reservation_ref,
                    now=now,
                )
            if any(
                item.request_fingerprint == request_fingerprint
                for item in ledger.reservations
            ):
                raise IMGCanarySecurityAuthorityError(
                    "IMG_CANARY_BUDGET_REQUEST_FINGERPRINT_ALREADY_RESERVED"
                )
            status: Literal["AVAILABLE_UNRESERVED", "INSUFFICIENT"] = (
                "AVAILABLE_UNRESERVED"
                if ledger.available_usd >= request_estimate_usd
                else "INSUFFICIENT"
            )
            return self._evidence(
                ledger=ledger,
                run_id=run_id,
                request_fingerprint=request_fingerprint,
                request_estimate_usd=request_estimate_usd,
                status=status,
                reservation_ref=None,
                now=now,
            )

    def reserve(
        self,
        *,
        run_id: str,
        request_fingerprint: str,
        request_estimate_usd: Decimal,
        now: datetime,
    ) -> IMGCanaryBudgetReservationEvidence:
        _require_aware(now, "IMG_CANARY_BUDGET_TIMEZONE_REQUIRED")
        with self._exclusive_lock():
            ledger = self._load(IMGCanaryMonthlyBudgetAuthorityLedger)
            self._validate_reservation_request(
                ledger=ledger,
                request_estimate_usd=request_estimate_usd,
                now=now,
            )
            existing_run = next(
                (item for item in ledger.reservations if item.run_id == run_id), None
            )
            if existing_run is not None:
                if (
                    existing_run.request_fingerprint != request_fingerprint
                    or existing_run.amount_usd != request_estimate_usd
                ):
                    raise IMGCanarySecurityAuthorityError(
                        "IMG_CANARY_BUDGET_RUN_RESERVATION_CONFLICT"
                    )
                status: Literal["ALREADY_RESERVED", "ALREADY_SPENT"] = (
                    "ALREADY_RESERVED"
                    if existing_run.status == "RESERVED"
                    else "ALREADY_SPENT"
                )
                return self._evidence(
                    ledger=ledger,
                    run_id=run_id,
                    request_fingerprint=request_fingerprint,
                    request_estimate_usd=request_estimate_usd,
                    status=status,
                    reservation_ref=existing_run.reservation_ref,
                    now=now,
                )
            if any(
                item.request_fingerprint == request_fingerprint
                for item in ledger.reservations
            ):
                raise IMGCanarySecurityAuthorityError(
                    "IMG_CANARY_BUDGET_REQUEST_FINGERPRINT_ALREADY_RESERVED"
                )
            if ledger.available_usd < request_estimate_usd:
                return self._evidence(
                    ledger=ledger,
                    run_id=run_id,
                    request_fingerprint=request_fingerprint,
                    request_estimate_usd=request_estimate_usd,
                    status="INSUFFICIENT",
                    reservation_ref=None,
                    now=now,
                )
            reservation_digest = ai_image_stable_hash(
                {
                    "authority_ref": ledger.authority_ref,
                    "billing_period": ledger.billing_period,
                    "run_id": run_id,
                    "request_fingerprint": request_fingerprint,
                    "request_estimate_usd": request_estimate_usd,
                }
            )
            reservation_payload: dict[str, object] = {
                "reservation_ref": (
                    f"budget-reservation://img-canary/{ledger.billing_period}/"
                    f"{reservation_digest}"
                ),
                "run_id": run_id,
                "request_fingerprint": request_fingerprint,
                "amount_usd": request_estimate_usd,
                "status": "RESERVED",
                "reserved_at": now,
                "spent_at": None,
            }
            reservation = _new_hashed(
                IMGCanaryBudgetReservation, reservation_payload
            )
            spent_before = ledger.spent_usd
            reserved_before = ledger.reserved_usd
            available_before = ledger.available_usd
            ledger_payload = ledger.model_dump(mode="python", exclude={"content_hash"})
            ledger_payload.update(
                {
                    "reservations": (*ledger.reservations, reservation),
                    "updated_at": now,
                }
            )
            updated = _new_hashed(
                IMGCanaryMonthlyBudgetAuthorityLedger, ledger_payload
            )
            _atomic_write_model(self.path, updated)
            return self._evidence(
                ledger=updated,
                run_id=run_id,
                request_fingerprint=request_fingerprint,
                request_estimate_usd=request_estimate_usd,
                status="RESERVED",
                reservation_ref=reservation.reservation_ref,
                now=now,
                spent_before=spent_before,
                reserved_before=reserved_before,
                available_before=available_before,
            )

    def mark_spent(
        self,
        *,
        reservation_ref: str,
        run_id: str,
        request_fingerprint: str,
        now: datetime,
    ) -> IMGCanaryMonthlyBudgetAuthorityLedger:
        _require_aware(now, "IMG_CANARY_BUDGET_TIMEZONE_REQUIRED")
        with self._exclusive_lock():
            ledger = self._load(IMGCanaryMonthlyBudgetAuthorityLedger)
            reservation = next(
                (
                    item
                    for item in ledger.reservations
                    if item.reservation_ref == reservation_ref
                ),
                None,
            )
            if reservation is None:
                raise IMGCanarySecurityAuthorityError(
                    "IMG_CANARY_BUDGET_RESERVATION_NOT_FOUND"
                )
            if (
                reservation.run_id != run_id
                or reservation.request_fingerprint != request_fingerprint
            ):
                raise IMGCanarySecurityAuthorityError(
                    "IMG_CANARY_BUDGET_RESERVATION_CLAIM_MISMATCH"
                )
            if reservation.status == "SPENT":
                return ledger
            if now < reservation.reserved_at:
                raise ValueError("IMG_CANARY_BUDGET_SPENT_TIME_INVALID")
            reservation_payload = reservation.model_dump(
                mode="python", exclude={"content_hash"}
            )
            reservation_payload.update(
                {"status": "SPENT", "spent_at": now}
            )
            spent = _new_hashed(IMGCanaryBudgetReservation, reservation_payload)
            updated_reservations = tuple(
                spent if item.reservation_ref == reservation_ref else item
                for item in ledger.reservations
            )
            ledger_payload = ledger.model_dump(mode="python", exclude={"content_hash"})
            ledger_payload.update(
                {"reservations": updated_reservations, "updated_at": now}
            )
            updated = _new_hashed(
                IMGCanaryMonthlyBudgetAuthorityLedger, ledger_payload
            )
            _atomic_write_model(self.path, updated)
            return updated

    @staticmethod
    def _evidence(
        *,
        ledger: IMGCanaryMonthlyBudgetAuthorityLedger,
        run_id: str,
        request_fingerprint: str,
        request_estimate_usd: Decimal,
        status: Literal[
            "AVAILABLE_UNRESERVED",
            "RESERVED",
            "ALREADY_RESERVED",
            "ALREADY_SPENT",
            "INSUFFICIENT",
        ],
        reservation_ref: str | None,
        now: datetime,
        spent_before: Decimal | None = None,
        reserved_before: Decimal | None = None,
        available_before: Decimal | None = None,
    ) -> IMGCanaryBudgetReservationEvidence:
        payload: dict[str, object] = {
            "schema_version": "img-canary-budget-reservation-evidence/v1",
            "authority_ref": ledger.authority_ref,
            "authority_ledger_hash": ledger.content_hash,
            "billing_period": ledger.billing_period,
            "run_id": run_id,
            "request_fingerprint": request_fingerprint,
            "request_estimate_usd": request_estimate_usd,
            "dedicated_cap_usd": ledger.dedicated_cap_usd,
            "spent_before_usd": (
                ledger.spent_usd if spent_before is None else spent_before
            ),
            "reserved_before_usd": (
                ledger.reserved_usd if reserved_before is None else reserved_before
            ),
            "available_before_usd": (
                ledger.available_usd if available_before is None else available_before
            ),
            "status": status,
            "reservation_ref": reservation_ref,
            "checked_at": now,
        }
        return _new_hashed(IMGCanaryBudgetReservationEvidence, payload)

    @staticmethod
    def _validate_reservation_request(
        *,
        ledger: IMGCanaryMonthlyBudgetAuthorityLedger,
        request_estimate_usd: Decimal,
        now: datetime,
    ) -> None:
        if now.strftime("%Y-%m") != ledger.billing_period:
            raise IMGCanarySecurityAuthorityError(
                "IMG_CANARY_BUDGET_BILLING_PERIOD_CLOSED"
            )
        if request_estimate_usd <= 0:
            raise ValueError("IMG_CANARY_BUDGET_REQUEST_ESTIMATE_INVALID")
        if request_estimate_usd > ledger.per_request_hard_cap_usd:
            raise IMGCanarySecurityAuthorityError(
                "IMG_CANARY_BUDGET_REQUEST_HARD_CAP_EXCEEDED"
            )


__all__ = [
    "IMGCanaryCredentialRotationAuthority",
    "IMGCanaryMonthlyBudgetAuthority",
    "IMGCanarySecurityAuthorityError",
    "IMGCanaryTaskAuthorizationStore",
]
