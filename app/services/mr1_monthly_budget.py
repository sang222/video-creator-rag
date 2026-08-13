from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailureError
from app.db.models import (
    ChannelWorkspace,
    Company,
    CostEvent,
    MR1MonthlyBudgetReservation,
    VideoProject,
)


_ADVISORY_LOCK_KEY = 5_571_569_849_978_865_492  # int.from_bytes(b"MR1BUDGT", "big")
_MONEY_SCALE = Decimal("0.000001")
_CASH_COST_TYPES = {"ACTUAL", "ADJUSTED", "REFUNDED"}
_OCCUPYING_STATES = {
    "RESERVED",
    "SUBMITTED",
    "SETTLED_ACTUAL",
    "SETTLED_CONSERVATIVE",
}


def _canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _money(value: Decimal | int | float | str, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationFailureError(f"MR1_BUDGET_INVALID_MONEY:{field}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValidationFailureError(f"MR1_BUDGET_INVALID_MONEY:{field}")
    rounded = parsed.quantize(_MONEY_SCALE)
    if rounded != parsed:
        raise ValidationFailureError(f"MR1_BUDGET_MONEY_SCALE_EXCEEDED:{field}")
    return rounded


def _money_text(value: Decimal | int | float | str) -> str:
    return format(Decimal(str(value)).quantize(_MONEY_SCALE), "f")


def _money_map(
    values: Mapping[str, Decimal | int | float | str], *, field: str
) -> dict[str, Decimal]:
    normalized: dict[str, Decimal] = {}
    for raw_key, raw_value in values.items():
        key = str(raw_key).strip()
        if not key or key in normalized:
            raise ValidationFailureError(f"MR1_BUDGET_INVALID_PROVIDER_KEY:{field}")
        normalized[key] = _money(raw_value, field=f"{field}.{key}")
    if not normalized:
        raise ValidationFailureError(f"MR1_BUDGET_PROVIDER_MAP_EMPTY:{field}")
    return dict(sorted(normalized.items()))


def _serialized_money_map(values: Mapping[str, Decimal]) -> dict[str, str]:
    return {key: _money_text(value) for key, value in sorted(values.items())}


class MR1MonthlyBudgetAuthority:
    """Atomic monthly reservation and settlement authority for MR1.

    All mutations take one stable PostgreSQL transaction advisory lock.  The
    deliberately global lock is small and conservative: it serializes the
    capacity read with the reservation write across environment, provider,
    company and channel caps, including concurrent runs in other companies.
    The caller must commit immediately after each returned mutation before a
    provider boundary is crossed.
    """

    def __init__(
        self,
        session: Session,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(UTC))

    def reserve_run(
        self,
        *,
        run_id: uuid.UUID | str,
        project_id: uuid.UUID | str,
        reservation_amount_usd: Decimal | int | float | str,
        environment_cap_usd: Decimal | int | float | str,
        company_cap_usd: Decimal | int | float | str,
        channel_cap_usd: Decimal | int | float | str,
        provider_allocations_usd: Mapping[str, Decimal | int | float | str],
        provider_caps_usd: Mapping[str, Decimal | int | float | str],
        provider_aliases: Mapping[str, Sequence[str]] | None = None,
        currency: str = "USD",
    ) -> dict[str, Any]:
        """Reserve one hard ceiling, partitioned across provider caps.

        Repeating the exact request for the same run is idempotent.  Any changed
        binding for that run fails closed instead of mutating the authority.
        """

        self._lock_authority()
        normalized_run_id = self._uuid(run_id, field="run_id")
        normalized_project_id = self._uuid(project_id, field="project_id")
        amount = _money(reservation_amount_usd, field="reservation_amount_usd")
        environment_cap = _money(environment_cap_usd, field="environment_cap_usd")
        company_cap = _money(company_cap_usd, field="company_cap_usd")
        channel_cap = _money(channel_cap_usd, field="channel_cap_usd")
        allocations = _money_map(
            provider_allocations_usd, field="provider_allocations_usd"
        )
        provider_caps = _money_map(provider_caps_usd, field="provider_caps_usd")
        if set(allocations) != set(provider_caps):
            raise ValidationFailureError("MR1_BUDGET_PROVIDER_CAP_BINDINGS_INCOMPLETE")
        if sum(allocations.values(), Decimal("0")) != amount:
            raise ValidationFailureError("MR1_BUDGET_PROVIDER_ALLOCATIONS_NOT_EXACT")
        if currency != "USD":
            raise ValidationFailureError("MR1_BUDGET_CURRENCY_NOT_USD")
        aliases = self._normalize_aliases(allocations, provider_aliases)
        now = self._now()
        period_start, period_end = self._month_bounds(now)

        project = self.session.get(VideoProject, normalized_project_id)
        if project is None:
            raise ValidationFailureError("MR1_BUDGET_PROJECT_NOT_FOUND")
        if self.session.get(Company, project.company_id) is None:
            raise ValidationFailureError("MR1_BUDGET_COMPANY_NOT_FOUND")
        channel = self.session.get(ChannelWorkspace, project.channel_workspace_id)
        if channel is None or channel.company_id != project.company_id:
            raise ValidationFailureError("MR1_BUDGET_PROJECT_SCOPE_INVALID")

        request = {
            "schema_version": "mr1.monthly-budget-reservation-request.v1",
            "run_id": str(normalized_run_id),
            "project_id": str(project.id),
            "company_id": str(project.company_id),
            "channel_workspace_id": str(project.channel_workspace_id),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "currency": currency,
            "reserved_amount_usd": _money_text(amount),
            "environment_cap_usd": _money_text(environment_cap),
            "company_cap_usd": _money_text(company_cap),
            "channel_cap_usd": _money_text(channel_cap),
            "provider_allocations_usd": _serialized_money_map(allocations),
            "provider_caps_usd": _serialized_money_map(provider_caps),
            "provider_aliases": aliases,
        }
        request_hash = _canonical_hash(request)
        existing = self.session.scalar(
            select(MR1MonthlyBudgetReservation)
            .where(MR1MonthlyBudgetReservation.run_id == normalized_run_id)
            .with_for_update()
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ValidationFailureError("MR1_BUDGET_RESERVATION_BINDING_MISMATCH")
            return self._evidence(existing)

        capacity = self._capacity_snapshot(
            period_start=period_start,
            period_end=period_end,
            currency=currency,
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            provider_aliases=aliases,
        )
        projected = {
            "environment_usd": capacity["environment_occupied_usd"] + amount,
            "company_usd": capacity["company_occupied_usd"] + amount,
            "channel_usd": capacity["channel_occupied_usd"] + amount,
            "providers_usd": {
                provider: capacity["provider_occupied_usd"].get(provider, Decimal("0"))
                + allocation
                for provider, allocation in allocations.items()
            },
        }
        failed: list[str] = []
        if projected["environment_usd"] > environment_cap:
            failed.append("ENVIRONMENT")
        if projected["company_usd"] > company_cap:
            failed.append("COMPANY")
        if projected["channel_usd"] > channel_cap:
            failed.append("CHANNEL")
        for provider, provider_projected in projected["providers_usd"].items():
            if provider_projected > provider_caps[provider]:
                failed.append(f"PROVIDER:{provider}")
        if failed:
            raise ValidationFailureError(
                "MR1_MONTHLY_BUDGET_EXCEEDED:" + ",".join(sorted(failed))
            )

        capacity_evidence = {
            "schema_version": "mr1.monthly-budget-capacity.v1",
            "request": request,
            "request_hash": request_hash,
            "recorded_cost_event_types": sorted(_CASH_COST_TYPES),
            "before_reservation": self._serialized_capacity(capacity),
            "after_reservation": {
                "environment_occupied_usd": _money_text(projected["environment_usd"]),
                "company_occupied_usd": _money_text(projected["company_usd"]),
                "channel_occupied_usd": _money_text(projected["channel_usd"]),
                "provider_occupied_usd": _serialized_money_map(
                    projected["providers_usd"]
                ),
            },
            "checks": {
                "environment_cap": "PASS",
                "company_cap": "PASS",
                "channel_cap": "PASS",
                **{
                    f"provider_cap:{provider}": "PASS"
                    for provider in sorted(allocations)
                },
            },
            "checked_at": now.isoformat(),
            "lock_contract": "pg_advisory_xact_lock(MR1BUDGT)",
        }
        capacity_evidence["content_hash"] = _canonical_hash(capacity_evidence)
        reservation = MR1MonthlyBudgetReservation(
            reservation_ref=f"mr1-budget://{normalized_run_id}",
            run_id=normalized_run_id,
            video_project_id=project.id,
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            period_start=period_start,
            period_end=period_end,
            currency=currency,
            reserved_amount=amount,
            provider_allocations_json=_serialized_money_map(allocations),
            environment_cap=environment_cap,
            company_cap=company_cap,
            channel_cap=channel_cap,
            provider_caps_json=_serialized_money_map(provider_caps),
            status="RESERVED",
            provider_actuals_json={},
            request_hash=request_hash,
            capacity_evidence_json=capacity_evidence,
            reason_code="MONTHLY_CAPACITY_RESERVED",
        )
        self.session.add(reservation)
        self.session.flush()
        return self._evidence(reservation)

    def mark_submitted(self, reservation_ref: str) -> dict[str, Any]:
        self._lock_authority()
        reservation = self._require_for_update(reservation_ref)
        if reservation.status == "RELEASED":
            raise ValidationFailureError("MR1_BUDGET_RELEASED_BEFORE_PROVIDER_SUBMIT")
        if reservation.status == "RESERVED":
            reservation.status = "SUBMITTED"
            reservation.submitted_at = self._now()
            reservation.reason_code = "PROVIDER_SUBMIT_STARTED"
            self.session.flush()
        return self._evidence(reservation)

    def settle_success(
        self,
        reservation_ref: str,
        *,
        actual_amount_usd: Decimal | int | float | str,
        provider_actuals_usd: Mapping[str, Decimal | int | float | str],
    ) -> dict[str, Any]:
        self._lock_authority()
        reservation = self._require_for_update(reservation_ref)
        actual = _money(actual_amount_usd, field="actual_amount_usd")
        provider_actuals = _money_map(
            provider_actuals_usd, field="provider_actuals_usd"
        )
        allocations = self._row_money_map(reservation.provider_allocations_json)
        if set(provider_actuals) != set(allocations):
            raise ValidationFailureError("MR1_BUDGET_PROVIDER_ACTUAL_BINDING_MISMATCH")
        if sum(provider_actuals.values(), Decimal("0")) != actual:
            raise ValidationFailureError("MR1_BUDGET_PROVIDER_ACTUALS_NOT_EXACT")
        if actual > Decimal(reservation.reserved_amount):
            raise ValidationFailureError("MR1_BUDGET_ACTUAL_EXCEEDS_RESERVED_CEILING")
        if any(provider_actuals[key] > allocations[key] for key in allocations):
            raise ValidationFailureError(
                "MR1_BUDGET_PROVIDER_ACTUAL_EXCEEDS_RESERVED_ALLOCATION"
            )
        if reservation.status == "SETTLED_ACTUAL":
            if (
                Decimal(reservation.actual_amount or 0) != actual
                or self._row_money_map(reservation.provider_actuals_json)
                != provider_actuals
            ):
                raise ValidationFailureError("MR1_BUDGET_ACTUAL_SETTLEMENT_MISMATCH")
            return self._evidence(reservation)
        if reservation.status != "SUBMITTED":
            raise ValidationFailureError(
                "MR1_BUDGET_ACTUAL_SETTLEMENT_REQUIRES_SUBMITTED"
            )
        reservation.status = "SETTLED_ACTUAL"
        reservation.actual_amount = actual
        reservation.provider_actuals_json = _serialized_money_map(provider_actuals)
        reservation.settlement_kind = "ACTUAL"
        reservation.settled_at = self._now()
        reservation.reason_code = "ACTUAL_COST_SETTLED"
        self.session.flush()
        return self._evidence(reservation)

    def settle_conservative_success(
        self,
        reservation_ref: str,
        *,
        conservative_amount_usd: Decimal | int | float | str,
        provider_conservative_amounts_usd: Mapping[str, Decimal | int | float | str],
    ) -> dict[str, Any]:
        """Settle a successful effect when only a safe catalog estimate exists.

        This is intentionally distinct from :meth:`settle_success`: the supplied
        amounts are conservative accounting evidence, not an assertion that the
        provider exposed an actual billed amount.  Replaying the exact settlement
        is idempotent; changed amounts fail closed.
        """

        self._lock_authority()
        reservation = self._require_for_update(reservation_ref)
        conservative = _money(
            conservative_amount_usd,
            field="conservative_amount_usd",
        )
        provider_conservative = _money_map(
            provider_conservative_amounts_usd,
            field="provider_conservative_amounts_usd",
        )
        allocations = self._row_money_map(reservation.provider_allocations_json)
        if set(provider_conservative) != set(allocations):
            raise ValidationFailureError(
                "MR1_BUDGET_PROVIDER_CONSERVATIVE_BINDING_MISMATCH"
            )
        if sum(provider_conservative.values(), Decimal("0")) != conservative:
            raise ValidationFailureError(
                "MR1_BUDGET_PROVIDER_CONSERVATIVE_AMOUNTS_NOT_EXACT"
            )
        if conservative > Decimal(reservation.reserved_amount):
            raise ValidationFailureError(
                "MR1_BUDGET_CONSERVATIVE_EXCEEDS_RESERVED_CEILING"
            )
        if any(provider_conservative[key] > allocations[key] for key in allocations):
            raise ValidationFailureError(
                "MR1_BUDGET_PROVIDER_CONSERVATIVE_EXCEEDS_RESERVED_ALLOCATION"
            )
        if reservation.status == "SETTLED_CONSERVATIVE":
            if (
                reservation.settlement_kind != "CONSERVATIVE_CATALOG_ESTIMATE_SUCCESS"
                or Decimal(reservation.actual_amount or 0) != conservative
                or self._row_money_map(reservation.provider_actuals_json)
                != provider_conservative
            ):
                raise ValidationFailureError(
                    "MR1_BUDGET_CONSERVATIVE_SUCCESS_SETTLEMENT_MISMATCH"
                )
            return self._evidence(reservation)
        if reservation.status != "SUBMITTED":
            raise ValidationFailureError(
                "MR1_BUDGET_CONSERVATIVE_SUCCESS_REQUIRES_SUBMITTED"
            )
        reservation.status = "SETTLED_CONSERVATIVE"
        reservation.actual_amount = conservative
        reservation.provider_actuals_json = _serialized_money_map(provider_conservative)
        reservation.settlement_kind = "CONSERVATIVE_CATALOG_ESTIMATE_SUCCESS"
        reservation.settled_at = self._now()
        reservation.reason_code = "SUCCESS_COST_SETTLED_BY_CATALOG_ESTIMATE"
        self.session.flush()
        return self._evidence(reservation)

    def settle_consumed_failure(self, reservation_ref: str) -> dict[str, Any]:
        self._lock_authority()
        reservation = self._require_for_update(reservation_ref)
        if reservation.status == "SETTLED_CONSERVATIVE":
            return self._evidence(reservation)
        if reservation.status != "SUBMITTED":
            raise ValidationFailureError(
                "MR1_BUDGET_CONSERVATIVE_SETTLEMENT_REQUIRES_SUBMITTED"
            )
        reservation.status = "SETTLED_CONSERVATIVE"
        reservation.actual_amount = reservation.reserved_amount
        reservation.provider_actuals_json = dict(
            reservation.provider_allocations_json or {}
        )
        reservation.settlement_kind = "CONSERVATIVE_RESERVED_CEILING"
        reservation.settled_at = self._now()
        reservation.reason_code = "CONSUMED_FAILURE_COST_UNKNOWN"
        self.session.flush()
        return self._evidence(reservation)

    def release_pre_submit(self, reservation_ref: str) -> dict[str, Any]:
        self._lock_authority()
        reservation = self._require_for_update(reservation_ref)
        if reservation.status == "RELEASED":
            return self._evidence(reservation)
        if reservation.status != "RESERVED":
            raise ValidationFailureError(
                "MR1_BUDGET_RELEASE_FORBIDDEN_AFTER_PROVIDER_SUBMIT"
            )
        reservation.status = "RELEASED"
        reservation.released_at = self._now()
        reservation.reason_code = "PRE_SUBMIT_RESERVATION_RELEASED"
        self.session.flush()
        return self._evidence(reservation)

    def inspect(self, reservation_ref: str) -> dict[str, Any]:
        reservation = self.session.scalar(
            select(MR1MonthlyBudgetReservation).where(
                MR1MonthlyBudgetReservation.reservation_ref == reservation_ref
            )
        )
        if reservation is None:
            raise ValidationFailureError("MR1_BUDGET_RESERVATION_NOT_FOUND")
        return self._evidence(reservation)

    def _capacity_snapshot(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        currency: str,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        provider_aliases: dict[str, list[str]],
    ) -> dict[str, Any]:
        company_channel_ids = set(
            self.session.scalars(
                select(ChannelWorkspace.id).where(
                    ChannelWorkspace.company_id == company_id
                )
            ).all()
        )
        company_project_ids = set(
            self.session.scalars(
                select(VideoProject.id).where(VideoProject.company_id == company_id)
            ).all()
        )
        channel_project_ids = set(
            self.session.scalars(
                select(VideoProject.id).where(
                    VideoProject.channel_workspace_id == channel_workspace_id
                )
            ).all()
        )
        cost_rows = self.session.execute(
            select(
                CostEvent.provider_key,
                CostEvent.cost_scope_type,
                CostEvent.cost_scope_id,
                CostEvent.amount,
            ).where(
                CostEvent.created_at >= period_start,
                CostEvent.created_at < period_end,
                CostEvent.currency == currency,
                CostEvent.cost_type.in_(sorted(_CASH_COST_TYPES)),
            )
        ).all()
        environment_cost = Decimal("0")
        company_cost = Decimal("0")
        channel_cost = Decimal("0")
        provider_cost = {provider: Decimal("0") for provider in provider_aliases}
        aliases_to_provider = {
            alias: provider
            for provider, aliases in provider_aliases.items()
            for alias in aliases
        }
        for provider_key, scope_type, scope_id, raw_amount in cost_rows:
            event_amount = Decimal(raw_amount)
            environment_cost += event_amount
            if (
                (scope_type == "COMPANY" and scope_id == company_id)
                or (scope_type == "CHANNEL" and scope_id in company_channel_ids)
                or (scope_type == "PROJECT" and scope_id in company_project_ids)
            ):
                company_cost += event_amount
            if (scope_type == "CHANNEL" and scope_id == channel_workspace_id) or (
                scope_type == "PROJECT" and scope_id in channel_project_ids
            ):
                channel_cost += event_amount
            canonical_provider = aliases_to_provider.get(provider_key)
            if canonical_provider is not None:
                provider_cost[canonical_provider] += event_amount

        reservation_rows = self.session.scalars(
            select(MR1MonthlyBudgetReservation).where(
                MR1MonthlyBudgetReservation.period_start == period_start,
                MR1MonthlyBudgetReservation.period_end == period_end,
                MR1MonthlyBudgetReservation.currency == currency,
                MR1MonthlyBudgetReservation.status.in_(sorted(_OCCUPYING_STATES)),
            )
        ).all()
        environment_reservations = Decimal("0")
        company_reservations = Decimal("0")
        channel_reservations = Decimal("0")
        provider_reservations = {
            provider: Decimal("0") for provider in provider_aliases
        }
        for row in reservation_rows:
            occupied = self._row_occupied_amount(row)
            environment_reservations += occupied
            if row.company_id == company_id:
                company_reservations += occupied
            if row.channel_workspace_id == channel_workspace_id:
                channel_reservations += occupied
            row_provider_amounts = self._row_provider_occupied_amounts(row)
            for provider in provider_reservations:
                provider_reservations[provider] += row_provider_amounts.get(
                    provider, Decimal("0")
                )

        return {
            "environment_cost_events_usd": environment_cost,
            "environment_reservations_usd": environment_reservations,
            "environment_occupied_usd": environment_cost + environment_reservations,
            "company_cost_events_usd": company_cost,
            "company_reservations_usd": company_reservations,
            "company_occupied_usd": company_cost + company_reservations,
            "channel_cost_events_usd": channel_cost,
            "channel_reservations_usd": channel_reservations,
            "channel_occupied_usd": channel_cost + channel_reservations,
            "provider_cost_events_usd": provider_cost,
            "provider_reservations_usd": provider_reservations,
            "provider_occupied_usd": {
                provider: provider_cost[provider] + provider_reservations[provider]
                for provider in provider_aliases
            },
        }

    @staticmethod
    def _serialized_capacity(capacity: dict[str, Any]) -> dict[str, Any]:
        return {
            key: (
                _serialized_money_map(value)
                if isinstance(value, dict)
                else _money_text(value)
            )
            for key, value in capacity.items()
        }

    @staticmethod
    def _row_occupied_amount(row: MR1MonthlyBudgetReservation) -> Decimal:
        if row.status in {"RESERVED", "SUBMITTED"}:
            return Decimal(row.reserved_amount)
        if row.status in {"SETTLED_ACTUAL", "SETTLED_CONSERVATIVE"}:
            return Decimal(row.actual_amount or 0)
        return Decimal("0")

    def _row_provider_occupied_amounts(
        self, row: MR1MonthlyBudgetReservation
    ) -> dict[str, Decimal]:
        if row.status in {"RESERVED", "SUBMITTED"}:
            return self._row_money_map(row.provider_allocations_json)
        if row.status in {"SETTLED_ACTUAL", "SETTLED_CONSERVATIVE"}:
            return self._row_money_map(row.provider_actuals_json)
        return {}

    @staticmethod
    def _row_money_map(values: Mapping[str, Any] | None) -> dict[str, Decimal]:
        return {
            str(key): Decimal(str(value)).quantize(_MONEY_SCALE)
            for key, value in sorted((values or {}).items())
        }

    @staticmethod
    def _normalize_aliases(
        allocations: Mapping[str, Decimal],
        raw_aliases: Mapping[str, Sequence[str]] | None,
    ) -> dict[str, list[str]]:
        aliases: dict[str, list[str]] = {}
        claimed: set[str] = set()
        for provider in sorted(allocations):
            raw_provider_aliases = (raw_aliases or {}).get(provider)
            if isinstance(raw_provider_aliases, (str, bytes)):
                raise ValidationFailureError(
                    "MR1_BUDGET_PROVIDER_ALIAS_BINDING_INVALID"
                )
            provider_aliases = list(raw_provider_aliases or [provider])
            normalized = sorted({str(value).strip() for value in provider_aliases})
            if not normalized or any(not value for value in normalized):
                raise ValidationFailureError(
                    "MR1_BUDGET_PROVIDER_ALIAS_BINDING_INVALID"
                )
            if claimed.intersection(normalized):
                raise ValidationFailureError(
                    "MR1_BUDGET_PROVIDER_ALIAS_BINDING_OVERLAP"
                )
            aliases[provider] = normalized
            claimed.update(normalized)
        extras = set(raw_aliases or {}) - set(allocations)
        if extras:
            raise ValidationFailureError("MR1_BUDGET_PROVIDER_ALIAS_BINDING_INVALID")
        return aliases

    def _require_for_update(self, reservation_ref: str) -> MR1MonthlyBudgetReservation:
        reservation = self.session.scalar(
            select(MR1MonthlyBudgetReservation)
            .where(MR1MonthlyBudgetReservation.reservation_ref == reservation_ref)
            .with_for_update()
        )
        if reservation is None:
            raise ValidationFailureError("MR1_BUDGET_RESERVATION_NOT_FOUND")
        return reservation

    def _lock_authority(self) -> None:
        self.session.execute(
            text("select pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _ADVISORY_LOCK_KEY},
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationFailureError("MR1_BUDGET_CLOCK_MUST_BE_TIMEZONE_AWARE")
        return value.astimezone(UTC)

    @staticmethod
    def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
        return start, end

    @staticmethod
    def _uuid(value: uuid.UUID | str, *, field: str) -> uuid.UUID:
        try:
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationFailureError(f"MR1_BUDGET_INVALID_UUID:{field}") from exc

    def _evidence(self, row: MR1MonthlyBudgetReservation) -> dict[str, Any]:
        payload = {
            "schema_version": "mr1.monthly-budget-reservation.v2",
            "reservation_id": str(row.id),
            "reservation_ref": row.reservation_ref,
            "run_id": str(row.run_id),
            "project_id": str(row.video_project_id),
            "company_id": str(row.company_id),
            "channel_workspace_id": str(row.channel_workspace_id),
            "period_start": row.period_start.isoformat(),
            "period_end": row.period_end.isoformat(),
            "currency": row.currency,
            "status": row.status,
            "reserved_amount_usd": _money_text(row.reserved_amount),
            "occupied_amount_usd": _money_text(self._row_occupied_amount(row)),
            "provider_allocations_usd": dict(
                sorted((row.provider_allocations_json or {}).items())
            ),
            "environment_cap_usd": _money_text(row.environment_cap),
            "company_cap_usd": _money_text(row.company_cap),
            "channel_cap_usd": _money_text(row.channel_cap),
            "provider_caps_usd": dict(sorted((row.provider_caps_json or {}).items())),
            "actual_amount_usd": (
                _money_text(row.actual_amount)
                if row.actual_amount is not None
                else None
            ),
            "provider_actuals_usd": dict(
                sorted((row.provider_actuals_json or {}).items())
            ),
            "settlement_kind": row.settlement_kind,
            "request_hash": row.request_hash,
            "capacity_evidence": row.capacity_evidence_json,
            "reason_code": row.reason_code,
            "submitted_at": (
                row.submitted_at.isoformat() if row.submitted_at else None
            ),
            "settled_at": row.settled_at.isoformat() if row.settled_at else None,
            "released_at": row.released_at.isoformat() if row.released_at else None,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
        payload["content_hash"] = _canonical_hash(payload)
        return payload
