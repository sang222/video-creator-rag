"""Canonical budget and provider readiness for long-form production starts."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.channel import CompiledChannelPolicySnapshot
from app.services.config_registry import content_hash
from app.services.m2 import ProviderReadinessM2Service
from app.services.ops import CostService


_READY_PROVIDER_STATES = {
    "CAPABILITY_READY",
    "READY_FOR_EXECUTION_AUTHORIZATION",
    "READY_FOR_FUTURE_EXECUTION",
}


def resolve_budget_authority(
    session: Session,
    *,
    policy_snapshot_id: uuid.UUID,
    channel_workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """Evaluate the exact frozen channel cost envelope against actual spend."""

    snapshot, scoped = _scoped_policy(
        session,
        policy_snapshot_id=policy_snapshot_id,
        channel_workspace_id=channel_workspace_id,
    )
    budget = scoped.budget_policy
    now = utc_now()
    month_start = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    actual = CostService(session).actual_cash_total(
        cost_scope_type="CHANNEL",
        cost_scope_id=channel_workspace_id,
        currency=budget.currency,
        created_at_from=month_start,
    )
    monthly = Decimal(str(budget.monthly_channel_budget))
    estimate = Decimal(str(budget.max_estimated_cost_per_video))
    remaining = monthly - actual
    decision = "PASS" if estimate <= remaining else "BLOCK"
    return {
        "schema_version": "vcos.production-start-budget.v1",
        "decision": decision,
        "state": "READY" if decision == "PASS" else "BLOCKED",
        "policy_snapshot_id": str(snapshot.id),
        "policy_snapshot_hash": snapshot.content_hash,
        "budget_policy_hash": content_hash(budget.model_dump(mode="json")),
        "currency": budget.currency,
        "budget_period_start": month_start.isoformat(),
        "monthly_channel_budget": str(monthly),
        "actual_channel_cost": str(actual),
        "max_estimated_cost_per_video": str(estimate),
        "remaining_before_start": str(remaining),
        "reason_codes": (
            ["CHANNEL_BUDGET_AUTHORITY_PASS"]
            if decision == "PASS"
            else ["CHANNEL_BUDGET_CAPACITY_BLOCKED"]
        ),
        "deterministic": True,
    }


def resolve_provider_authority(
    session: Session,
    *,
    policy_snapshot_id: uuid.UUID,
    channel_workspace_id: uuid.UUID,
    readiness_snapshot: Any | None = None,
) -> dict[str, Any]:
    """Resolve required providers without executing or probing a provider."""

    snapshot, scoped = _scoped_policy(
        session,
        policy_snapshot_id=policy_snapshot_id,
        channel_workspace_id=channel_workspace_id,
    )
    required: list[str] = []
    if scoped.provider_usage_policy.elevenlabs.enabled:
        required.append("elevenlabs")
    if scoped.provider_usage_policy.drive_archive_required_before_cleanup:
        required.append("google_drive_archive")
    readiness_snapshot = (
        readiness_snapshot
        if readiness_snapshot is not None
        else ProviderReadinessM2Service().snapshot()
    )
    provider_map = {item.provider_key: item for item in readiness_snapshot.providers}
    providers: list[dict[str, Any]] = []
    blocked: list[str] = []
    for provider_key in sorted(set(required)):
        item = provider_map.get(provider_key)
        state = item.readiness_state if item is not None else "NOT_CONFIGURED"
        ready = state in _READY_PROVIDER_STATES
        if not ready:
            blocked.append(provider_key)
        providers.append(
            {
                "provider_key": provider_key,
                "readiness_state": state,
                "ready": ready,
                "reason_codes": (
                    list(item.blocker_reason_codes)
                    if item is not None
                    else ["PROVIDER_READINESS_MISSING"]
                ),
                "no_call_was_made": (
                    bool(item.no_call_was_made) if item is not None else True
                ),
            }
        )
    return {
        "schema_version": "vcos.production-start-provider.v1",
        "state": "BLOCKED" if blocked else "READY",
        "policy_snapshot_id": str(snapshot.id),
        "policy_snapshot_hash": snapshot.content_hash,
        "required_provider_keys": sorted(set(required)),
        "providers": providers,
        "blocked_provider_keys": blocked,
        "reason_codes": (
            ["REQUIRED_PROVIDER_READINESS_BLOCKED"]
            if blocked
            else ["REQUIRED_PROVIDER_READINESS_PASS"]
        ),
        "real_network_probe_enabled": bool(
            readiness_snapshot.real_network_probe_enabled
        ),
        "no_network_calls_made": bool(readiness_snapshot.no_network_calls_made),
    }


def _scoped_policy(
    session: Session,
    *,
    policy_snapshot_id: uuid.UUID,
    channel_workspace_id: uuid.UUID,
) -> tuple[CompiledChannelPolicySnapshot, ChannelScopedPolicy]:
    snapshot = session.get(
        CompiledChannelPolicySnapshot,
        policy_snapshot_id,
    )
    if (
        snapshot is None
        or snapshot.channel_workspace_id != channel_workspace_id
        or snapshot.status not in {"active", "approved"}
    ):
        raise ValidationFailureError("PRODUCTION_START_POLICY_SNAPSHOT_INVALID")
    raw = (snapshot.compiled_payload or {}).get("channel_scoped_policy")
    if not isinstance(raw, dict):
        raise ValidationFailureError("PRODUCTION_START_CHANNEL_POLICY_MISSING")
    try:
        scoped = ChannelScopedPolicy.model_validate(raw)
    except ValidationError as exc:
        raise ValidationFailureError("PRODUCTION_START_CHANNEL_POLICY_INVALID") from exc
    return snapshot, scoped
