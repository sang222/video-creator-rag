from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.ai_image import ai_image_stable_hash


_HASH_PATTERN = r"^[0-9a-f]{64}$"
_TASK_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$"
_AUTHORIZATION_REF_PATTERN = r"^authorization://[A-Za-z0-9][A-Za-z0-9._/-]{0,223}$"
_CLAIM_REF_PATTERN = r"^claim://[A-Za-z0-9][A-Za-z0-9._/-]{0,231}$"
_INCIDENT_REF_PATTERN = r"^incident://[A-Za-z0-9][A-Za-z0-9._/-]{0,231}$"
_ROTATION_REF_PATTERN = r"^rotation://[A-Za-z0-9][A-Za-z0-9._/-]{0,231}$"
_BUDGET_REF_PATTERN = r"^budget://[A-Za-z0-9][A-Za-z0-9._/-]{0,235}$"
_RESERVATION_REF_PATTERN = (
    r"^budget-reservation://[A-Za-z0-9][A-Za-z0-9._/-]{0,223}$"
)
IMG_CANARY_SECURITY_HARD_CAP_USD = Decimal("0.15")
IMG_CANARY_V1_TASK_KEY = "img-canary-master-prompt-2026-07-18"
IMG_CANARY_V1_AUTHORIZATION_REF = (
    "authorization://img-canary/master-prompt/one-paid-request"
)
IMG_CANARY_V1_AUTHORIZATION_FILENAME = "master-authorization.json"
IMG_CANARY_V2_APPROVAL_ID = "d6de1eab-f9bd-44fe-ab23-4bf7e05ce167"
IMG_CANARY_V2_TASK_KEY = f"img-canary-v2-approval-{IMG_CANARY_V2_APPROVAL_ID}"
IMG_CANARY_V2_AUTHORIZATION_REF = (
    f"authorization://img-canary/v2/{IMG_CANARY_V2_APPROVAL_ID}/one-paid-request"
)
IMG_CANARY_V2_AUTHORIZATION_RELATIVE_PATH = (
    Path("authorizations") / f"{IMG_CANARY_V2_APPROVAL_ID}.json"
)
IMG_CANARY_V3_APPROVAL_ID = "operator-3c895af877e10f7f"
IMG_CANARY_V3_TASK_KEY = f"img-canary-v3-approval-{IMG_CANARY_V3_APPROVAL_ID}"
IMG_CANARY_V3_AUTHORIZATION_REF = (
    f"authorization://img-canary/v3/{IMG_CANARY_V3_APPROVAL_ID}/one-paid-request"
)
IMG_CANARY_V3_AUTHORIZATION_RELATIVE_PATH = (
    Path("authorizations") / f"{IMG_CANARY_V3_APPROVAL_ID}.json"
)


def _require_aware(value: datetime, reason: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(reason)


def img_canary_task_authority_identity(
    run_id: str,
) -> tuple[str, str, Path]:
    if run_id.startswith("img-canary-v3-"):
        return (
            IMG_CANARY_V3_TASK_KEY,
            IMG_CANARY_V3_AUTHORIZATION_REF,
            IMG_CANARY_V3_AUTHORIZATION_RELATIVE_PATH,
        )
    if run_id.startswith("img-canary-v2-"):
        return (
            IMG_CANARY_V2_TASK_KEY,
            IMG_CANARY_V2_AUTHORIZATION_REF,
            IMG_CANARY_V2_AUTHORIZATION_RELATIVE_PATH,
        )
    return (
        IMG_CANARY_V1_TASK_KEY,
        IMG_CANARY_V1_AUTHORIZATION_REF,
        Path(IMG_CANARY_V1_AUTHORIZATION_FILENAME),
    )


class IMGCanaryTaskAuthorizationLedger(BaseModel):
    """Task-wide, terminal authorization for at most one provider submission.

    ``CLAIMED`` is intentionally not recoverable to ``AVAILABLE``. A crash after
    claiming may sacrifice the canary, but it cannot replay a paid request.
    """

    schema_version: Literal["img-canary-task-authorization/v1"] = (
        "img-canary-task-authorization/v1"
    )
    task_key: str = Field(pattern=_TASK_KEY_PATTERN)
    authorization_ref: str = Field(pattern=_AUTHORIZATION_REF_PATTERN)
    approval_version: Literal["V2", "V3"] | None = None
    approved_run_id: str | None = None
    approved_request_fingerprint: str | None = Field(
        default=None, pattern=_HASH_PATTERN
    )
    approved_prompt_hash: str | None = Field(default=None, pattern=_HASH_PATTERN)
    approved_serialized_body_hash: str | None = Field(
        default=None, pattern=_HASH_PATTERN
    )
    approved_scoped_approval_hash: str | None = Field(
        default=None, pattern=_HASH_PATTERN
    )
    status: Literal["AVAILABLE", "CLAIMED", "CONSUMED"]
    claimed_run_id: str | None = None
    claimed_request_fingerprint: str | None = Field(
        default=None, pattern=_HASH_PATTERN
    )
    claim_ref: str | None = Field(default=None, pattern=_CLAIM_REF_PATTERN)
    completion_status: Literal[
        "PROVIDER_ATTEMPT_SUBMITTED",
        "PROVIDER_ATTEMPT_COMPLETED",
        "PROVIDER_ATTEMPT_FAILED",
        "FAIL_CLOSED_AFTER_CLAIM",
    ] | None = None
    created_at: datetime
    updated_at: datetime
    claimed_at: datetime | None = None
    consumed_at: datetime | None = None
    content_hash: str = Field(pattern=_HASH_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    def content_hash_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"content_hash"})
        optional_bindings = (
            "approval_version",
            "approved_run_id",
            "approved_request_fingerprint",
            "approved_prompt_hash",
            "approved_serialized_body_hash",
            "approved_scoped_approval_hash",
        )
        if self.approval_version is None:
            for name in optional_bindings:
                payload.pop(name, None)
        return payload

    @model_validator(mode="after")
    def validate_state(self) -> "IMGCanaryTaskAuthorizationLedger":
        _require_aware(self.created_at, "IMG_CANARY_TASK_AUTH_TIMEZONE_REQUIRED")
        _require_aware(self.updated_at, "IMG_CANARY_TASK_AUTH_TIMEZONE_REQUIRED")
        approval_bindings = (
            self.approved_run_id,
            self.approved_request_fingerprint,
            self.approved_prompt_hash,
            self.approved_serialized_body_hash,
            self.approved_scoped_approval_hash,
        )
        if self.approval_version in {"V2", "V3"}:
            approval_version = self.approval_version
            if any(value is None for value in approval_bindings):
                raise ValueError(
                    f"IMG_CANARY_{approval_version}_TASK_AUTH_BINDING_INCOMPLETE"
                )
            expected_task_key, expected_authorization_ref = (
                (IMG_CANARY_V2_TASK_KEY, IMG_CANARY_V2_AUTHORIZATION_REF)
                if approval_version == "V2"
                else (IMG_CANARY_V3_TASK_KEY, IMG_CANARY_V3_AUTHORIZATION_REF)
            )
            if (
                self.task_key != expected_task_key
                or self.authorization_ref != expected_authorization_ref
            ):
                raise ValueError(
                    f"IMG_CANARY_{approval_version}_TASK_AUTH_IDENTITY_MISMATCH"
                )
            if approval_version == "V3" and not str(
                self.approved_run_id
            ).startswith("img-canary-v3-"):
                raise ValueError("IMG_CANARY_V3_TASK_AUTH_RUN_ID_MISMATCH")
        elif any(value is not None for value in approval_bindings):
            raise ValueError("IMG_CANARY_LEGACY_TASK_AUTH_HAS_V2_BINDING")
        if self.updated_at < self.created_at:
            raise ValueError("IMG_CANARY_TASK_AUTH_TIME_INVALID")
        claim_values = (
            self.claimed_run_id,
            self.claimed_request_fingerprint,
            self.claim_ref,
            self.claimed_at,
        )
        if self.status == "AVAILABLE":
            if any(value is not None for value in claim_values):
                raise ValueError("IMG_CANARY_AVAILABLE_AUTH_HAS_CLAIM")
            if self.completion_status is not None or self.consumed_at is not None:
                raise ValueError("IMG_CANARY_AVAILABLE_AUTH_HAS_COMPLETION")
        else:
            if any(value is None for value in claim_values):
                raise ValueError("IMG_CANARY_TASK_AUTH_CLAIM_INCOMPLETE")
            assert self.claimed_at is not None
            _require_aware(self.claimed_at, "IMG_CANARY_TASK_AUTH_TIMEZONE_REQUIRED")
            if self.claimed_at < self.created_at or self.updated_at < self.claimed_at:
                raise ValueError("IMG_CANARY_TASK_AUTH_CLAIM_TIME_INVALID")
            if self.status == "CLAIMED":
                if self.completion_status is not None or self.consumed_at is not None:
                    raise ValueError("IMG_CANARY_CLAIMED_AUTH_HAS_COMPLETION")
            else:
                if self.completion_status is None or self.consumed_at is None:
                    raise ValueError("IMG_CANARY_CONSUMED_AUTH_COMPLETION_REQUIRED")
                _require_aware(
                    self.consumed_at, "IMG_CANARY_TASK_AUTH_TIMEZONE_REQUIRED"
                )
                if self.consumed_at < self.claimed_at or self.updated_at < self.consumed_at:
                    raise ValueError("IMG_CANARY_TASK_AUTH_CONSUMED_TIME_INVALID")
            if self.approval_version in {"V2", "V3"} and (
                self.claimed_run_id != self.approved_run_id
                or self.claimed_request_fingerprint
                != self.approved_request_fingerprint
            ):
                raise ValueError(
                    f"IMG_CANARY_{self.approval_version}_TASK_AUTH_CLAIM_BINDING_MISMATCH"
                )
        expected = ai_image_stable_hash(self.content_hash_payload())
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_TASK_AUTH_HASH_MISMATCH")
        return self


class IMGCanaryCompromisedCredentialRecord(BaseModel):
    """Persisted incident record. It contains a digest, never credential bytes."""

    schema_version: Literal["img-canary-compromised-credential/v1"] = (
        "img-canary-compromised-credential/v1"
    )
    provider: Literal["google_gemini_image"] = "google_gemini_image"
    credential_kind: Literal["GEMINI_API_KEY"] = "GEMINI_API_KEY"
    incident_ref: str = Field(pattern=_INCIDENT_REF_PATTERN)
    compromised_fingerprint_sha256: str = Field(pattern=_HASH_PATTERN)
    recorded_at: datetime
    content_hash: str = Field(pattern=_HASH_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_record(self) -> "IMGCanaryCompromisedCredentialRecord":
        _require_aware(
            self.recorded_at, "IMG_CANARY_CREDENTIAL_INCIDENT_TIMEZONE_REQUIRED"
        )
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_CREDENTIAL_INCIDENT_HASH_MISMATCH")
        return self


class IMGCanaryCredentialRotationEvidence(BaseModel):
    """Safe-to-persist result of comparing a live key with the incident digest.

    The current fingerprint is not included. ``fingerprint_comparison_hash`` is
    a commitment to the comparison inputs and cannot be used as an API key.
    """

    schema_version: Literal["img-canary-credential-rotation-evidence/v1"] = (
        "img-canary-credential-rotation-evidence/v1"
    )
    provider: Literal["google_gemini_image"] = "google_gemini_image"
    credential_kind: Literal["GEMINI_API_KEY"] = "GEMINI_API_KEY"
    status: Literal["PASS", "BLOCKED"]
    rotation_ref: str = Field(pattern=_ROTATION_REF_PATTERN)
    incident_ref: str | None = Field(default=None, pattern=_INCIDENT_REF_PATTERN)
    compromised_record_hash: str | None = Field(default=None, pattern=_HASH_PATTERN)
    credential_configured: bool
    fingerprint_changed: bool
    fingerprint_comparison_hash: str = Field(pattern=_HASH_PATTERN)
    blocker_reason_codes: tuple[str, ...] = ()
    checked_at: datetime
    content_hash: str = Field(pattern=_HASH_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_evidence(self) -> "IMGCanaryCredentialRotationEvidence":
        _require_aware(
            self.checked_at, "IMG_CANARY_CREDENTIAL_ROTATION_TIMEZONE_REQUIRED"
        )
        expected_pass = bool(
            self.credential_configured
            and self.fingerprint_changed
            and self.incident_ref
            and self.compromised_record_hash
        )
        if (self.status == "PASS") != expected_pass:
            raise ValueError("IMG_CANARY_CREDENTIAL_ROTATION_STATUS_MISMATCH")
        if self.status == "PASS" and self.blocker_reason_codes:
            raise ValueError("IMG_CANARY_CREDENTIAL_ROTATION_PASS_HAS_BLOCKERS")
        if self.status == "BLOCKED" and not self.blocker_reason_codes:
            raise ValueError("IMG_CANARY_CREDENTIAL_ROTATION_BLOCKERS_REQUIRED")
        if len(set(self.blocker_reason_codes)) != len(self.blocker_reason_codes):
            raise ValueError("IMG_CANARY_CREDENTIAL_ROTATION_BLOCKERS_DUPLICATED")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_CREDENTIAL_ROTATION_HASH_MISMATCH")
        return self


class IMGCanaryBudgetReservation(BaseModel):
    reservation_ref: str = Field(pattern=_RESERVATION_REF_PATTERN)
    run_id: str = Field(min_length=1, max_length=255)
    request_fingerprint: str = Field(pattern=_HASH_PATTERN)
    amount_usd: Decimal = Field(gt=0)
    status: Literal["RESERVED", "SPENT"]
    reserved_at: datetime
    spent_at: datetime | None = None
    content_hash: str = Field(pattern=_HASH_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_reservation(self) -> "IMGCanaryBudgetReservation":
        _require_aware(
            self.reserved_at, "IMG_CANARY_BUDGET_RESERVATION_TIMEZONE_REQUIRED"
        )
        if self.status == "RESERVED" and self.spent_at is not None:
            raise ValueError("IMG_CANARY_RESERVED_BUDGET_HAS_SPENT_TIME")
        if self.status == "SPENT":
            if self.spent_at is None:
                raise ValueError("IMG_CANARY_SPENT_BUDGET_TIME_REQUIRED")
            _require_aware(
                self.spent_at, "IMG_CANARY_BUDGET_RESERVATION_TIMEZONE_REQUIRED"
            )
            if self.spent_at < self.reserved_at:
                raise ValueError("IMG_CANARY_BUDGET_SPENT_TIME_INVALID")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_BUDGET_RESERVATION_HASH_MISMATCH")
        return self


class IMGCanaryMonthlyBudgetAuthorityLedger(BaseModel):
    schema_version: Literal["img-canary-monthly-budget-authority/v1"] = (
        "img-canary-monthly-budget-authority/v1"
    )
    authority_ref: str = Field(pattern=_BUDGET_REF_PATTERN)
    channel_key: Literal["small-team-ai"] = "small-team-ai"
    billing_period: str = Field(pattern=r"^\d{4}-\d{2}$")
    currency: Literal["USD"] = "USD"
    dedicated_cap_usd: Decimal = Field(ge=0)
    opening_spend_usd: Decimal = Field(ge=0)
    per_request_hard_cap_usd: Decimal = Field(
        gt=0, le=IMG_CANARY_SECURITY_HARD_CAP_USD
    )
    reservations: tuple[IMGCanaryBudgetReservation, ...] = ()
    created_at: datetime
    updated_at: datetime
    content_hash: str = Field(pattern=_HASH_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_ledger(self) -> "IMGCanaryMonthlyBudgetAuthorityLedger":
        _require_aware(self.created_at, "IMG_CANARY_BUDGET_TIMEZONE_REQUIRED")
        _require_aware(self.updated_at, "IMG_CANARY_BUDGET_TIMEZONE_REQUIRED")
        if self.updated_at < self.created_at:
            raise ValueError("IMG_CANARY_BUDGET_TIME_INVALID")
        refs = [item.reservation_ref for item in self.reservations]
        runs = [item.run_id for item in self.reservations]
        fingerprints = [item.request_fingerprint for item in self.reservations]
        if len(refs) != len(set(refs)):
            raise ValueError("IMG_CANARY_BUDGET_RESERVATION_REF_DUPLICATED")
        if len(runs) != len(set(runs)):
            raise ValueError("IMG_CANARY_BUDGET_RUN_DUPLICATED")
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("IMG_CANARY_BUDGET_REQUEST_FINGERPRINT_DUPLICATED")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_BUDGET_AUTHORITY_HASH_MISMATCH")
        return self

    @property
    def reserved_usd(self) -> Decimal:
        return sum(
            (item.amount_usd for item in self.reservations if item.status == "RESERVED"),
            Decimal("0"),
        )

    @property
    def spent_usd(self) -> Decimal:
        return self.opening_spend_usd + sum(
            (item.amount_usd for item in self.reservations if item.status == "SPENT"),
            Decimal("0"),
        )

    @property
    def available_usd(self) -> Decimal:
        return max(
            Decimal("0"),
            self.dedicated_cap_usd - self.spent_usd - self.reserved_usd,
        )


class IMGCanaryBudgetReservationEvidence(BaseModel):
    schema_version: Literal["img-canary-budget-reservation-evidence/v1"] = (
        "img-canary-budget-reservation-evidence/v1"
    )
    authority_ref: str = Field(pattern=_BUDGET_REF_PATTERN)
    authority_ledger_hash: str = Field(pattern=_HASH_PATTERN)
    billing_period: str = Field(pattern=r"^\d{4}-\d{2}$")
    run_id: str = Field(min_length=1, max_length=255)
    request_fingerprint: str = Field(pattern=_HASH_PATTERN)
    request_estimate_usd: Decimal = Field(
        gt=0, le=IMG_CANARY_SECURITY_HARD_CAP_USD
    )
    dedicated_cap_usd: Decimal = Field(ge=0)
    spent_before_usd: Decimal = Field(ge=0)
    reserved_before_usd: Decimal = Field(ge=0)
    available_before_usd: Decimal = Field(ge=0)
    status: Literal[
        "AVAILABLE_UNRESERVED",
        "RESERVED",
        "ALREADY_RESERVED",
        "ALREADY_SPENT",
        "INSUFFICIENT",
    ]
    reservation_ref: str | None = Field(
        default=None, pattern=_RESERVATION_REF_PATTERN
    )
    checked_at: datetime
    content_hash: str = Field(pattern=_HASH_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_evidence(self) -> "IMGCanaryBudgetReservationEvidence":
        _require_aware(self.checked_at, "IMG_CANARY_BUDGET_EVIDENCE_TIMEZONE_REQUIRED")
        calculated_available = max(
            Decimal("0"),
            self.dedicated_cap_usd
            - self.spent_before_usd
            - self.reserved_before_usd,
        )
        if self.available_before_usd != calculated_available:
            raise ValueError("IMG_CANARY_BUDGET_EVIDENCE_AVAILABLE_MISMATCH")
        if self.status == "INSUFFICIENT":
            if self.reservation_ref is not None:
                raise ValueError("IMG_CANARY_BUDGET_INSUFFICIENT_HAS_RESERVATION")
            if self.available_before_usd >= self.request_estimate_usd:
                raise ValueError("IMG_CANARY_BUDGET_INSUFFICIENT_STATUS_INVALID")
        elif self.status == "AVAILABLE_UNRESERVED":
            if self.reservation_ref is not None:
                raise ValueError("IMG_CANARY_BUDGET_AVAILABLE_HAS_RESERVATION")
            if self.available_before_usd < self.request_estimate_usd:
                raise ValueError("IMG_CANARY_BUDGET_AVAILABLE_STATUS_INVALID")
        elif self.reservation_ref is None:
            raise ValueError("IMG_CANARY_BUDGET_RESERVATION_REF_REQUIRED")
        elif self.status == "RESERVED" and (
            self.available_before_usd < self.request_estimate_usd
        ):
            raise ValueError("IMG_CANARY_BUDGET_RESERVED_WITHOUT_CAPACITY")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_BUDGET_EVIDENCE_HASH_MISMATCH")
        return self


__all__ = [
    "IMG_CANARY_SECURITY_HARD_CAP_USD",
    "IMG_CANARY_V1_AUTHORIZATION_FILENAME",
    "IMG_CANARY_V1_AUTHORIZATION_REF",
    "IMG_CANARY_V1_TASK_KEY",
    "IMG_CANARY_V2_APPROVAL_ID",
    "IMG_CANARY_V2_AUTHORIZATION_REF",
    "IMG_CANARY_V2_AUTHORIZATION_RELATIVE_PATH",
    "IMG_CANARY_V2_TASK_KEY",
    "IMG_CANARY_V3_APPROVAL_ID",
    "IMG_CANARY_V3_AUTHORIZATION_REF",
    "IMG_CANARY_V3_AUTHORIZATION_RELATIVE_PATH",
    "IMG_CANARY_V3_TASK_KEY",
    "IMGCanaryBudgetReservation",
    "IMGCanaryBudgetReservationEvidence",
    "IMGCanaryCompromisedCredentialRecord",
    "IMGCanaryCredentialRotationEvidence",
    "IMGCanaryMonthlyBudgetAuthorityLedger",
    "IMGCanaryTaskAuthorizationLedger",
    "img_canary_task_authority_identity",
]
