"""Canonical budget and provider readiness for long-form production starts."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.core.config import get_settings
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.channel import ChannelWorkspace, CompiledChannelPolicySnapshot
from app.db.models.m10_5 import GoogleDriveMediaCredential
from app.db.models.ops import CredentialReference
from app.services.m10_5 import GOOGLE_DRIVE_SCOPE, GoogleDriveConfigService
from app.services.config_registry import content_hash
from app.services.ops import CostService


_READY_PROVIDER_STATES = {"READY_FOR_REAL_EXECUTION"}


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
    if (
        scoped.provider_usage_policy.drive_archive_required_before_cleanup
        and not scoped.provider_usage_policy.youtube_private_stage_required_before_cleanup
    ):
        required.append("google_drive_archive")
    # A caller-provided snapshot exists solely for hermetic unit tests. Runtime
    # cadence never supplies it: it recomputes the concrete v2 executor and
    # channel-scoped credential authority below.
    provider_map = (
        {item.provider_key: item for item in readiness_snapshot.providers}
        if readiness_snapshot is not None
        else {}
    )
    providers: list[dict[str, Any]] = []
    blocked: list[str] = []
    for provider_key in sorted(set(required)):
        item = provider_map.get(provider_key)
        runtime = (
            None
            if readiness_snapshot is not None
            else _real_runtime_readiness(
                session=session,
                provider_key=provider_key,
                channel_workspace_id=channel_workspace_id,
                scoped=scoped,
            )
        )
        state = (
            item.readiness_state
            if item is not None
            else str((runtime or {}).get("readiness_state") or "NOT_CONFIGURED")
        )
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
                    else list((runtime or {}).get("reason_codes") or [])
                ),
                "requirements": (runtime or {}).get("requirements", {}),
                "no_call_was_made": bool(item.no_call_was_made)
                if item is not None
                else True,
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
            if readiness_snapshot is not None
            else False
        ),
        "no_network_calls_made": bool(
            readiness_snapshot.no_network_calls_made
            if readiness_snapshot is not None
            else True
        ),
    }


def _real_runtime_readiness(
    *,
    session: Session,
    provider_key: str,
    channel_workspace_id: uuid.UUID,
    scoped: ChannelScopedPolicy,
) -> dict[str, Any]:
    """Read the real v2 pre-start authority without probing a provider."""

    settings = get_settings()
    if provider_key == "elevenlabs":
        api_key = settings.elevenlabs_api_key
        requirements = {
            "real_executor_registered": _real_executor_registered(
                "v2-elevenlabs-narration"
            ),
            "credential_reference_exists": bool(
                api_key and api_key.get_secret_value().strip()
            ),
            "credential_state_healthy": bool(
                api_key and api_key.get_secret_value().strip()
            ),
            "channel_scope_match": bool(scoped.voice_policy.voice_id)
            and bool(scoped.voice_policy.model_id),
            # The immutable channel policy, not an unrelated environment
            # default, is the source of the voice/model passed to ElevenLabs.
            "capability_model_match": bool(scoped.voice_policy.voice_id)
            and bool(scoped.voice_policy.model_id),
            "idempotency_supported": True,
            "attempt_limit_defined": (
                scoped.provider_usage_policy.elevenlabs.initial_tts_attempts == 1
            ),
            "real_execution_enabled": settings.provider_real_execution_enabled
            and settings.provider_production_execution_enabled
            and settings.elevenlabs_real_execution_enabled
            and settings.elevenlabs_real_generation_enabled
            and not settings.media_provider_calls_disabled,
        }
        return _readiness_from_requirements(
            provider_key="elevenlabs", requirements=requirements
        )
    if provider_key == "google_drive_archive":
        workspace = session.get(ChannelWorkspace, channel_workspace_id)
        config = GoogleDriveConfigService(settings)
        try:
            safe_status = config.safe_status()
        except ValidationFailureError:
            safe_status = {}
        credential = (
            session.scalar(
                select(GoogleDriveMediaCredential)
                .where(
                    GoogleDriveMediaCredential.company_id
                    == (workspace.company_id if workspace is not None else None),
                    GoogleDriveMediaCredential.channel_workspace_id
                    == channel_workspace_id,
                    GoogleDriveMediaCredential.connection_state == "CONNECTED",
                )
                .order_by(GoogleDriveMediaCredential.updated_at.desc())
                .limit(1)
            )
            if workspace is not None
            else None
        )
        reference = (
            session.get(CredentialReference, credential.credential_reference_id)
            if credential is not None
            else None
        )
        requirements = {
            "real_executor_registered": _real_executor_registered(
                "v2-google-drive-remote"
            ),
            "credential_reference_exists": reference is not None,
            "credential_state_healthy": reference is not None
            and reference.status not in {"MISSING", "REVOKED", "DISABLED"},
            "channel_scope_match": credential is not None
            and credential.company_id == workspace.company_id
            and credential.channel_workspace_id == channel_workspace_id,
            "capability_model_match": credential is not None
            and GOOGLE_DRIVE_SCOPE in set(credential.scopes or []),
            "idempotency_supported": True,
            "attempt_limit_defined": True,
            "root_authority": credential is not None
            and bool(config.root_folder_id())
            and credential.root_folder_id == config.root_folder_id(),
            "real_execution_enabled": settings.google_drive_offload_enabled
            and settings.google_drive_archive_enabled
            and settings.google_drive_real_archive_enabled
            and bool(safe_status.get("config_state") == "CONFIGURED"),
        }
        return _readiness_from_requirements(
            provider_key="google_drive_archive", requirements=requirements
        )
    return {
        "readiness_state": "BLOCKED_CAPABILITY",
        "reason_codes": ["REAL_PROVIDER_UNKNOWN"],
        "requirements": {},
    }


def _real_executor_registered(adapter_key: str) -> bool:
    """Inspect the real default gateway without making a provider request."""

    try:
        from app.services.v2_provider_production import (
            PackageBoundV2StageGateway,
            build_v2_provider_production_gateway,
        )

        gateway = build_v2_provider_production_gateway()
        return isinstance(gateway.media, PackageBoundV2StageGateway) and (
            adapter_key in gateway.media.registered_adapter_keys
        )
    except Exception:
        return False


def _readiness_from_requirements(
    *, provider_key: str, requirements: dict[str, bool]
) -> dict[str, Any]:
    if all(requirements.values()):
        return {
            "readiness_state": "READY_FOR_REAL_EXECUTION",
            "reason_codes": [],
            "requirements": requirements,
        }
    if not requirements.get("real_executor_registered", False):
        reason = "BLOCKED_EXECUTOR_UNAVAILABLE"
    elif not requirements.get(
        "credential_reference_exists", False
    ) or not requirements.get("credential_state_healthy", False):
        reason = "BLOCKED_CREDENTIAL"
    elif not requirements.get("channel_scope_match", False):
        reason = "BLOCKED_CHANNEL_SCOPE"
    elif provider_key == "google_drive_archive" and not requirements.get(
        "root_authority", False
    ):
        reason = "BLOCKED_ARCHIVE_AUTHORITY"
    elif not requirements.get("capability_model_match", False):
        reason = "BLOCKED_CAPABILITY"
    else:
        reason = "BLOCKED_EXECUTOR_UNAVAILABLE"
    return {
        "readiness_state": reason,
        "reason_codes": [reason],
        "requirements": requirements,
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
