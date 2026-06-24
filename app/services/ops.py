import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, EventEnvelope
from app.contracts.ops import (
    BudgetGateCheckRequest,
    BudgetGateDecisionRead,
    BudgetPolicyCreate,
    ComponentHealthSnapshotCreate,
    CostEventCreate,
    CredentialHealthSnapshotCreate,
    CredentialReferenceCreate,
    DeadLetterJobCreate,
    ManualActionCreate,
    OpsIncidentCreate,
    ProviderAttemptMockRequest,
    ProviderRegistryEntryCreate,
    QuotaAccountCreate,
    QuotaEventRequest,
    RetryPolicyCreate,
)
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AuditEvent,
    BudgetPolicy,
    ComponentHealthSnapshot,
    CostEvent,
    CredentialHealthSnapshot,
    CredentialReference,
    DeadLetterJob,
    DomainEvent,
    ManualAction,
    OpsIncident,
    ProviderAttempt,
    ProviderHealthSnapshot,
    ProviderRegistryEntry,
    QuotaAccount,
    QuotaEvent,
    RetryPolicy,
    SystemHealthSnapshot,
)
from app.providers.mock import run_mock_contract
from app.services.audit import AuditService
from app.services.domain_events import DomainEventBus


MOCK_PROVIDER_SEEDS = [
    ProviderRegistryEntryCreate(
        provider_key="mock_llm",
        provider_name="Mock LLM Provider",
        provider_type="LLM",
        status="ACTIVE",
        capability_blob={"supports_streaming": False, "supports_json_mode": True},
        policy_fit_blob={"mock_only": True, "production_enabled": False},
        cost_model_blob={"unit": "TOKENS", "mock_cost_usd": 0},
        quota_model_blob={"unit": "REQUESTS", "mock_limit": 1000},
        retry_policy_blob={"max_attempts": 2, "retryable_error_codes": ["PROVIDER_TIMEOUT", "RETRYABLE_PROVIDER_ERROR"]},
        metadata={"mock": True},
    ),
    ProviderRegistryEntryCreate(
        provider_key="mock_tts",
        provider_name="Mock TTS Provider",
        provider_type="TTS",
        status="ACTIVE",
        capability_blob={"supports_tts": True, "audio_rights_risk_class": "mock"},
        policy_fit_blob={"mock_only": True, "production_enabled": False},
        cost_model_blob={"unit": "SECONDS", "mock_cost_usd": 0},
        quota_model_blob={"unit": "REQUESTS", "mock_limit": 1000},
        retry_policy_blob={"max_attempts": 2},
        metadata={"mock": True},
    ),
    ProviderRegistryEntryCreate(
        provider_key="mock_media",
        provider_name="Mock Media Provider",
        provider_type="MEDIA",
        status="ACTIVE",
        capability_blob={"preserves_metadata": True, "license_evidence_required": True},
        policy_fit_blob={"mock_only": True, "production_enabled": False},
        cost_model_blob={"unit": "REQUESTS", "mock_cost_usd": 0},
        quota_model_blob={"unit": "REQUESTS", "mock_limit": 1000},
        retry_policy_blob={"max_attempts": 2},
        metadata={"mock": True},
    ),
    ProviderRegistryEntryCreate(
        provider_key="mock_storage",
        provider_name="Mock Storage Provider",
        provider_type="STORAGE",
        status="ACTIVE",
        capability_blob={"preserves_metadata": True},
        policy_fit_blob={"mock_only": True, "production_enabled": False},
        cost_model_blob={"unit": "BYTES", "mock_cost_usd": 0},
        quota_model_blob={"unit": "BYTES", "mock_limit": 1048576},
        retry_policy_blob={"max_attempts": 2},
        metadata={"mock": True},
    ),
    ProviderRegistryEntryCreate(
        provider_key="mock_platform",
        provider_name="Mock Platform Provider",
        provider_type="PLATFORM",
        status="ACTIVE",
        capability_blob={"commercial_use_declared": True, "provenance_fidelity": "mock"},
        policy_fit_blob={"mock_only": True, "production_enabled": False},
        cost_model_blob={"unit": "REQUESTS", "mock_cost_usd": 0},
        quota_model_blob={"unit": "REQUESTS", "mock_limit": 1000},
        retry_policy_blob={"max_attempts": 2},
        metadata={"mock": True},
    ),
    ProviderRegistryEntryCreate(
        provider_key="mock_analytics",
        provider_name="Mock Analytics Provider",
        provider_type="ANALYTICS",
        status="ACTIVE",
        capability_blob={"metrics_fixture_only": True},
        policy_fit_blob={"mock_only": True, "production_enabled": False},
        cost_model_blob={"unit": "REQUESTS", "mock_cost_usd": 0},
        quota_model_blob={"unit": "REQUESTS", "mock_limit": 1000},
        retry_policy_blob={"max_attempts": 2},
        metadata={"mock": True},
    ),
]

SECRET_KEY_FRAGMENTS = {"secret", "password", "token", "api_key", "apikey", "private_key", "credential_value"}
RAW_SECRET_MARKERS = ("sk-", "pk_live_", "BEGIN PRIVATE KEY", "anthropic-", "xoxb-", "ghp_")


@dataclass(frozen=True)
class AttemptClassification:
    status: str
    error_code: str | None
    error_message_redacted: str | None
    reason_code: str | None


class ProviderRegistryService:
    def __init__(self, session: Session):
        self.session = session

    def create_entry(self, *, data: ProviderRegistryEntryCreate, correlation_id: str = "m4-provider-registry") -> ProviderRegistryEntry:
        existing = self.get_entry(data.provider_key)
        if existing is not None:
            raise ConflictError(f"provider exists: {data.provider_key}")
        _ensure_no_secret_payload(data.model_dump(mode="json"))
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        entry = ProviderRegistryEntry(**payload, metadata_=metadata)
        self.session.add(entry)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="provider_registry_entry.created",
            aggregate_type="provider_registry_entry",
            aggregate_id=entry.id,
            target_type="provider_registry_entry",
            target_id=entry.id,
            correlation_id=correlation_id,
            payload={"provider_key": entry.provider_key, "provider_type": entry.provider_type, "status": entry.status},
        )
        return entry

    def seed_mock_providers(self) -> list[ProviderRegistryEntry]:
        records: list[ProviderRegistryEntry] = []
        for seed in MOCK_PROVIDER_SEEDS:
            existing = self.get_entry(seed.provider_key)
            if existing is None:
                records.append(self.create_entry(data=seed, correlation_id="m4-provider-registry-seed"))
            else:
                records.append(existing)
        return records

    def get_entry(self, provider_key: str) -> ProviderRegistryEntry | None:
        return self.session.scalars(select(ProviderRegistryEntry).where(ProviderRegistryEntry.provider_key == provider_key)).one_or_none()

    def require_entry(self, provider_key: str) -> ProviderRegistryEntry:
        entry = self.get_entry(provider_key)
        if entry is None:
            raise NotFoundError(f"provider not found: {provider_key}")
        return entry

    def list_entries(self) -> list[ProviderRegistryEntry]:
        return list(self.session.scalars(select(ProviderRegistryEntry).order_by(ProviderRegistryEntry.provider_key.asc())).all())


class CredentialReferenceService:
    def __init__(self, session: Session):
        self.session = session

    def create_reference(self, *, data: CredentialReferenceCreate, correlation_id: str = "m4-credential-reference") -> CredentialReference:
        ProviderRegistryService(self.session).require_entry(data.provider_key)
        if self.session.scalars(
            select(CredentialReference).where(
                CredentialReference.provider_key == data.provider_key,
                CredentialReference.credential_key == data.credential_key,
            )
        ).one_or_none() is not None:
            raise ConflictError(f"credential reference exists: {data.provider_key}/{data.credential_key}")
        _validate_secret_ref(data.secret_ref)
        _ensure_no_secret_payload({"scope_blob": data.scope_blob, "metadata": data.metadata})
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        reference = CredentialReference(**payload, metadata_=metadata)
        self.session.add(reference)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="credential_reference.created",
            aggregate_type="credential_reference",
            aggregate_id=reference.id,
            target_type="credential_reference",
            target_id=reference.id,
            correlation_id=correlation_id,
            payload={
                "provider_key": reference.provider_key,
                "credential_key": reference.credential_key,
                "credential_type": reference.credential_type,
                "status": reference.status,
                "secret_ref_present": reference.secret_ref is not None,
            },
        )
        return reference

    def get_reference(self, credential_reference_id: uuid.UUID) -> CredentialReference | None:
        return self.session.get(CredentialReference, credential_reference_id)

    def require_reference(self, credential_reference_id: uuid.UUID) -> CredentialReference:
        reference = self.get_reference(credential_reference_id)
        if reference is None:
            raise NotFoundError(f"credential reference not found: {credential_reference_id}")
        return reference

    def check_health(
        self,
        *,
        data: CredentialHealthSnapshotCreate,
        correlation_id: str = "m4-credential-health",
    ) -> CredentialHealthSnapshot:
        reference = self.require_reference(data.credential_reference_id)
        state, reasons, next_action = _credential_health_from_status(reference.status, data.health_state, data.reason_codes, data.next_action)
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        snapshot = CredentialHealthSnapshot(
            credential_reference_id=reference.id,
            provider_key=reference.provider_key,
            health_state=state,
            reason_codes=reasons,
            next_action=next_action,
            metadata_=metadata,
        )
        reference.last_checked_at = snapshot.checked_at
        self.session.add(snapshot)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="credential_health_snapshot.created",
            aggregate_type="credential_health_snapshot",
            aggregate_id=snapshot.id,
            target_type="credential_health_snapshot",
            target_id=snapshot.id,
            correlation_id=correlation_id,
            payload={
                "credential_reference_id": str(reference.id),
                "provider_key": reference.provider_key,
                "health_state": snapshot.health_state,
                "reason_codes": snapshot.reason_codes,
                "next_action": snapshot.next_action,
            },
        )
        if snapshot.health_state in {"MISSING", "EXPIRED", "REVOKED", "MISCONFIGURED"}:
            ManualActionService(self.session).create_action(
                data=ManualActionCreate(
                    action_type="CHECK_CREDENTIAL" if snapshot.health_state != "EXPIRED" else "UPDATE_CREDENTIAL_REF",
                    target_type="credential_reference",
                    target_id=reference.id,
                    priority="HIGH",
                    reason_code=snapshot.reason_codes[0] if snapshot.reason_codes else "PROVIDER_CREDENTIAL_MISSING",
                    next_action=snapshot.next_action or "Review credential reference.",
                ),
                correlation_id="m4-credential-health-action",
            )
        return snapshot


class QuotaService:
    def __init__(self, session: Session):
        self.session = session

    def create_account(self, *, data: QuotaAccountCreate, correlation_id: str = "m4-quota-account") -> QuotaAccount:
        ProviderRegistryService(self.session).require_entry(data.provider_key)
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        account = QuotaAccount(**payload, metadata_=metadata)
        self.session.add(account)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="quota_account.created",
            aggregate_type="quota_account",
            aggregate_id=account.id,
            target_type="quota_account",
            target_id=account.id,
            correlation_id=correlation_id,
            payload={"provider_key": account.provider_key, "unit": account.unit, "status": account.status},
        )
        return account

    def get_account(self, quota_account_id: uuid.UUID) -> QuotaAccount | None:
        return self.session.get(QuotaAccount, quota_account_id)

    def require_account(self, quota_account_id: uuid.UUID) -> QuotaAccount:
        account = self.get_account(quota_account_id)
        if account is None:
            raise NotFoundError(f"quota account not found: {quota_account_id}")
        return account

    def reserve_quota(self, *, data: QuotaEventRequest) -> QuotaEvent:
        account = self.require_account(data.quota_account_id)
        if account.status in {"DISABLED", "EXHAUSTED"} or not _quota_has_capacity(account, data.amount):
            event = self._record_event(account, data=data, event_type="REJECT", reason_code=data.reason_code or "QUOTA_EXHAUSTED")
            account.status = "EXHAUSTED" if account.status != "DISABLED" else account.status
            self.session.flush()
            return event
        account.quota_reserved = _decimal(account.quota_reserved) + data.amount
        return self._record_event(account, data=data, event_type="RESERVE", reason_code=data.reason_code or "QUOTA_RESERVED")

    def consume_quota(self, *, data: QuotaEventRequest) -> QuotaEvent:
        account = self.require_account(data.quota_account_id)
        if account.status == "DISABLED" or not _quota_has_capacity(account, data.amount, include_reserved=False):
            return self._record_event(account, data=data, event_type="REJECT", reason_code=data.reason_code or "QUOTA_EXHAUSTED")
        consume_from_reserved = min(_decimal(account.quota_reserved), data.amount)
        account.quota_reserved = _decimal(account.quota_reserved) - consume_from_reserved
        account.quota_used = _decimal(account.quota_used) + data.amount
        if account.quota_limit is not None and account.quota_used >= account.quota_limit:
            account.status = "EXHAUSTED"
        return self._record_event(account, data=data, event_type="CONSUME", reason_code=data.reason_code)

    def release_quota(self, *, data: QuotaEventRequest) -> QuotaEvent:
        account = self.require_account(data.quota_account_id)
        account.quota_reserved = max(Decimal("0"), _decimal(account.quota_reserved) - data.amount)
        if account.status == "EXHAUSTED" and _quota_has_capacity(account, Decimal("0")):
            account.status = "ACTIVE"
        return self._record_event(account, data=data, event_type="RELEASE", reason_code=data.reason_code or "QUOTA_RELEASED")

    def _record_event(self, account: QuotaAccount, *, data: QuotaEventRequest, event_type: str, reason_code: str | None) -> QuotaEvent:
        event = QuotaEvent(
            quota_account_id=account.id,
            provider_key=account.provider_key,
            event_type=event_type,
            amount=data.amount,
            unit=account.unit,
            target_type=data.target_type,
            target_id=data.target_id,
            reason_code=reason_code,
            metadata_=data.metadata,
        )
        self.session.add(event)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="quota_event.created",
            aggregate_type="quota_event",
            aggregate_id=event.id,
            target_type="quota_event",
            target_id=event.id,
            correlation_id="m4-quota-event",
            payload={
                "quota_account_id": str(account.id),
                "provider_key": event.provider_key,
                "event_type": event.event_type,
                "amount": str(event.amount),
                "unit": event.unit,
                "reason_code": event.reason_code,
            },
        )
        return event


class CostService:
    def __init__(self, session: Session):
        self.session = session

    def record_event(self, *, data: CostEventCreate, correlation_id: str = "m4-cost-event") -> CostEvent:
        ProviderRegistryService(self.session).require_entry(data.provider_key)
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        event = CostEvent(**payload, metadata_=metadata)
        self.session.add(event)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="cost_event.created",
            aggregate_type="cost_event",
            aggregate_id=event.id,
            target_type="cost_event",
            target_id=event.id,
            correlation_id=correlation_id,
            payload={
                "provider_key": event.provider_key,
                "cost_scope_type": event.cost_scope_type,
                "cost_scope_id": str(event.cost_scope_id) if event.cost_scope_id else None,
                "amount": str(event.amount),
                "currency": event.currency,
                "cost_type": event.cost_type,
                "is_cash": event.cost_type in {"ACTUAL", "ADJUSTED", "REFUNDED"},
            },
        )
        return event

    def list_events(
        self,
        *,
        provider_key: str | None = None,
        cost_scope_type: str | None = None,
        cost_scope_id: uuid.UUID | None = None,
    ) -> list[CostEvent]:
        statement: Select[tuple[CostEvent]] = select(CostEvent).order_by(CostEvent.created_at.asc())
        if provider_key is not None:
            statement = statement.where(CostEvent.provider_key == provider_key)
        if cost_scope_type is not None:
            statement = statement.where(CostEvent.cost_scope_type == cost_scope_type)
        if cost_scope_id is not None:
            statement = statement.where(CostEvent.cost_scope_id == cost_scope_id)
        return list(self.session.scalars(statement).all())

    def actual_cash_total(self, *, cost_scope_type: str, cost_scope_id: uuid.UUID | None = None) -> Decimal:
        statement = select(func.coalesce(func.sum(CostEvent.amount), 0)).where(
            CostEvent.cost_scope_type == cost_scope_type,
            CostEvent.cost_type.in_(["ACTUAL", "ADJUSTED", "REFUNDED"]),
        )
        if cost_scope_id is not None:
            statement = statement.where(CostEvent.cost_scope_id == cost_scope_id)
        return _decimal(self.session.scalar(statement) or 0)


class BudgetGateService:
    def __init__(self, session: Session):
        self.session = session

    def create_policy(self, *, data: BudgetPolicyCreate, correlation_id: str = "m4-budget-policy") -> BudgetPolicy:
        if self.session.scalars(select(BudgetPolicy).where(BudgetPolicy.policy_key == data.policy_key)).one_or_none() is not None:
            raise ConflictError(f"budget policy exists: {data.policy_key}")
        policy = BudgetPolicy(**data.model_dump())
        self.session.add(policy)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="budget_policy.created",
            aggregate_type="budget_policy",
            aggregate_id=policy.id,
            target_type="budget_policy",
            target_id=policy.id,
            correlation_id=correlation_id,
            payload={"policy_key": policy.policy_key, "scope_type": policy.scope_type, "status": policy.status},
        )
        return policy

    def get_policy(self, policy_key: str) -> BudgetPolicy | None:
        return self.session.scalars(select(BudgetPolicy).where(BudgetPolicy.policy_key == policy_key)).one_or_none()

    def check(self, *, data: BudgetGateCheckRequest, correlation_id: str = "m4-budget-gate") -> BudgetGateDecisionRead:
        policy = self.get_policy(data.policy_key)
        if policy is None:
            raise NotFoundError(f"budget policy not found: {data.policy_key}")
        decision = "PASS"
        reasons = ["SYSTEM_OK"]
        next_action = None
        blob = policy.policy_blob or {}
        if policy.status != "ACTIVE":
            decision = "REVIEW_REQUIRED"
            reasons = ["BUDGET_REVIEW_REQUIRED"]
            next_action = "Activate or replace the budget policy before relying on it."
        if blob.get("block_when_quota_unknown") and data.quota_account_id is None:
            decision = "BLOCK"
            reasons = ["QUOTA_ACCOUNT_MISSING"]
            next_action = "Create a quota account for this provider/scope."
        if data.quota_account_id is not None and data.quota_amount is not None:
            account = QuotaService(self.session).require_account(data.quota_account_id)
            if account.status == "UNKNOWN" and blob.get("block_when_quota_unknown"):
                decision = "BLOCK"
                reasons = ["QUOTA_ACCOUNT_MISSING"]
                next_action = "Resolve unknown quota state."
            elif not _quota_has_capacity(account, data.quota_amount):
                decision = "BLOCK"
                reasons = ["QUOTA_EXHAUSTED"]
                next_action = "Release quota, raise quota, or choose another provider."
        estimated_cost = _decimal(data.estimated_cost or 0)
        manual_threshold = _optional_decimal(blob.get("require_manual_approval_above_usd"))
        if decision == "PASS" and manual_threshold is not None and estimated_cost > manual_threshold:
            decision = "REVIEW_REQUIRED"
            reasons = ["BUDGET_REVIEW_REQUIRED"]
            next_action = "Manual approval required before spending above threshold."
        max_project = _optional_decimal(blob.get("max_project_usd"))
        if max_project is not None and data.scope_type == "PROJECT":
            actual = CostService(self.session).actual_cash_total(cost_scope_type="PROJECT", cost_scope_id=data.scope_id)
            if actual + estimated_cost > max_project:
                decision = "BLOCK"
                reasons = ["COST_LIMIT_REACHED"]
                next_action = "Project cost limit reached."
        _record_ops_event(
            self.session,
            event_type="budget_gate.checked",
            aggregate_type="budget_policy",
            aggregate_id=policy.id,
            target_type="budget_policy",
            target_id=policy.id,
            correlation_id=correlation_id,
            payload={
                "policy_key": policy.policy_key,
                "decision": decision,
                "reason_codes": reasons,
                "deterministic": True,
            },
        )
        return BudgetGateDecisionRead(
            decision=decision,
            reason_codes=reasons,
            next_action=next_action,
            policy_key=policy.policy_key,
            deterministic=True,
        )


class ProviderHealthService:
    def __init__(self, session: Session):
        self.session = session

    def check_provider(self, *, provider_key: str, mode: str = "success", next_action: str | None = None, metadata: dict[str, Any] | None = None) -> ProviderHealthSnapshot:
        entry = ProviderRegistryService(self.session).require_entry(provider_key)
        response = run_mock_contract(provider_key, operation_key="health_check", mode=mode)
        health_state, reasons, action = _provider_health_from_response(response, next_action)
        snapshot = ProviderHealthSnapshot(
            provider_key=provider_key,
            provider_type=entry.provider_type,
            health_state=health_state,
            latency_ms=response.latency_ms,
            error_rate=Decimal("0") if response.ok else Decimal("1"),
            quota_state="EXHAUSTED" if health_state == "QUOTA_EXHAUSTED" else None,
            reason_codes=reasons,
            next_action=action,
            metadata_=metadata or {"mock": True},
        )
        self.session.add(snapshot)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="provider_health_snapshot.created",
            aggregate_type="provider_health_snapshot",
            aggregate_id=snapshot.id,
            target_type="provider_health_snapshot",
            target_id=snapshot.id,
            correlation_id="m4-provider-health",
            payload={
                "provider_key": provider_key,
                "health_state": snapshot.health_state,
                "reason_codes": snapshot.reason_codes,
                "next_action": snapshot.next_action,
            },
        )
        ComponentHealthService(self.session).create_snapshot(
            data=ComponentHealthSnapshotCreate(
                component_type="PROVIDER",
                component_key=provider_key,
                health_state="HEALTHY" if health_state == "HEALTHY" else "DEGRADED" if health_state in {"DEGRADED", "RATE_LIMITED", "QUOTA_EXHAUSTED"} else "UNAVAILABLE",
                reason_codes=reasons,
                next_action=action,
                metadata={"provider_health_snapshot_id": str(snapshot.id)},
            ),
            correlation_id="m4-provider-health-component",
        )
        return snapshot

    def list_health(self, provider_key: str) -> list[ProviderHealthSnapshot]:
        return list(
            self.session.scalars(
                select(ProviderHealthSnapshot)
                .where(ProviderHealthSnapshot.provider_key == provider_key)
                .order_by(ProviderHealthSnapshot.checked_at.asc())
            ).all()
        )


class ComponentHealthService:
    def __init__(self, session: Session):
        self.session = session

    def create_snapshot(self, *, data: ComponentHealthSnapshotCreate, correlation_id: str = "m4-component-health") -> ComponentHealthSnapshot:
        if data.health_state in {"DEGRADED", "UNAVAILABLE"} and not data.next_action:
            raise ValidationFailureError("next_action is required for degraded/unavailable component health")
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        snapshot = ComponentHealthSnapshot(**payload, metadata_=metadata)
        self.session.add(snapshot)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="component_health_snapshot.created",
            aggregate_type="component_health_snapshot",
            aggregate_id=snapshot.id,
            target_type="component_health_snapshot",
            target_id=snapshot.id,
            correlation_id=correlation_id,
            payload={
                "component_type": snapshot.component_type,
                "component_key": snapshot.component_key,
                "health_state": snapshot.health_state,
                "reason_codes": snapshot.reason_codes,
            },
        )
        return snapshot

    def latest_snapshots(self) -> list[ComponentHealthSnapshot]:
        rows = list(self.session.scalars(select(ComponentHealthSnapshot).order_by(ComponentHealthSnapshot.checked_at.desc())).all())
        latest: dict[tuple[str, str], ComponentHealthSnapshot] = {}
        for row in rows:
            key = (row.component_type, row.component_key)
            if key not in latest:
                latest[key] = row
        return list(latest.values())


class SystemHealthService:
    def __init__(self, session: Session):
        self.session = session

    def create_snapshot(self, *, metadata: dict[str, Any] | None = None, correlation_id: str = "m4-system-health") -> SystemHealthSnapshot:
        components = ComponentHealthService(self.session).latest_snapshots()
        active_incident_count = self.session.scalar(select(func.count()).select_from(OpsIncident).where(OpsIncident.state.in_(["OPEN", "ACKNOWLEDGED"]))) or 0
        counts: dict[str, int] = {"HEALTHY": 0, "DEGRADED": 0, "UNAVAILABLE": 0, "UNKNOWN": 0}
        for component in components:
            counts[component.health_state] = counts.get(component.health_state, 0) + 1
        if active_incident_count or counts.get("UNAVAILABLE", 0):
            state = "BLOCKED"
            reasons = ["SYSTEM_HEALTH_BLOCKED"]
            next_action = "Resolve active incidents or unavailable components."
        elif counts.get("DEGRADED", 0):
            state = "DEGRADED"
            reasons = ["SYSTEM_HEALTH_DEGRADED"]
            next_action = "Review degraded components."
        elif not components:
            state = "UNKNOWN"
            reasons = ["SYSTEM_HEALTH_DEGRADED"]
            next_action = None
        else:
            state = "HEALTHY"
            reasons = ["SYSTEM_OK"]
            next_action = None
        snapshot = SystemHealthSnapshot(
            overall_state=state,
            component_counts=counts,
            active_incident_count=active_incident_count,
            action_required=state in {"DEGRADED", "BLOCKED"},
            reason_codes=reasons,
            next_action=next_action,
            metadata_=metadata or {},
        )
        self.session.add(snapshot)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="system_health_snapshot.created",
            aggregate_type="system_health_snapshot",
            aggregate_id=snapshot.id,
            target_type="system_health_snapshot",
            target_id=snapshot.id,
            correlation_id=correlation_id,
            payload={
                "overall_state": snapshot.overall_state,
                "component_counts": snapshot.component_counts,
                "active_incident_count": snapshot.active_incident_count,
                "action_required": snapshot.action_required,
                "reason_codes": snapshot.reason_codes,
            },
        )
        return snapshot

    def latest(self) -> SystemHealthSnapshot | None:
        return self.session.scalars(select(SystemHealthSnapshot).order_by(SystemHealthSnapshot.captured_at.desc()).limit(1)).one_or_none()


class RetryOpsService:
    def __init__(self, session: Session):
        self.session = session

    def create_policy(self, *, data: RetryPolicyCreate, correlation_id: str = "m4-retry-policy") -> RetryPolicy:
        if self.session.scalars(select(RetryPolicy).where(RetryPolicy.policy_key == data.policy_key)).one_or_none() is not None:
            raise ConflictError(f"retry policy exists: {data.policy_key}")
        policy = RetryPolicy(**data.model_dump())
        self.session.add(policy)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="retry_policy.created",
            aggregate_type="retry_policy",
            aggregate_id=policy.id,
            target_type="retry_policy",
            target_id=policy.id,
            correlation_id=correlation_id,
            payload={"policy_key": policy.policy_key, "status": policy.status, "provider_key": policy.provider_key},
        )
        return policy

    def record_mock_attempt(self, *, data: ProviderAttemptMockRequest, correlation_id: str = "m4-provider-attempt") -> ProviderAttempt:
        ProviderRegistryService(self.session).require_entry(data.provider_key)
        started = utc_now()
        response = run_mock_contract(data.provider_key, operation_key=data.operation_key, mode=data.mode)
        classification = _classify_attempt(response, data.mode)
        attempt = ProviderAttempt(
            provider_key=data.provider_key,
            operation_key=data.operation_key,
            target_type=data.target_type,
            target_id=data.target_id,
            attempt_number=data.attempt_number,
            status=classification.status,
            error_code=classification.error_code,
            error_message_redacted=classification.error_message_redacted,
            started_at=started,
            finished_at=utc_now(),
            latency_ms=response.latency_ms,
            metadata_={"mock": True, "output": response.output if response.ok else {}, **data.metadata},
        )
        self.session.add(attempt)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="provider_attempt.created",
            aggregate_type="provider_attempt",
            aggregate_id=attempt.id,
            target_type="provider_attempt",
            target_id=attempt.id,
            correlation_id=correlation_id,
            payload={
                "provider_key": attempt.provider_key,
                "operation_key": attempt.operation_key,
                "status": attempt.status,
                "error_code": attempt.error_code,
                "attempt_number": attempt.attempt_number,
            },
        )
        if attempt.status == "CIRCUIT_OPEN":
            OpsIncidentService(self.session).create_incident(
                data=OpsIncidentCreate(
                    incident_type="PROVIDER_OUTAGE",
                    severity="ERROR",
                    impacted_refs=[{"type": "provider", "provider_key": attempt.provider_key}],
                    reason_codes=["CIRCUIT_BREAKER_OPEN"],
                    next_action="Investigate provider circuit breaker state.",
                ),
                correlation_id="m4-provider-attempt-incident",
            )
        if attempt.status == "RETRYABLE_FAILURE" and data.attempt_number >= self._max_attempts(data.provider_key):
            DeadLetterService(self.session).create_job(
                data=DeadLetterJobCreate(
                    queue_name="provider_attempts",
                    job_type=data.operation_key,
                    target_type=data.target_type or "provider",
                    target_id=data.target_id,
                    fail_count=data.attempt_number,
                    replay_state="REPLAYABLE",
                    reason_code="MAX_RETRY_EXCEEDED",
                    next_action="Review provider attempt and replay when safe.",
                    metadata={"provider_key": data.provider_key, "attempt_id": str(attempt.id)},
                ),
                correlation_id="m4-provider-attempt-dead-letter",
            )
        return attempt

    def get_attempt(self, attempt_id: uuid.UUID) -> ProviderAttempt | None:
        return self.session.get(ProviderAttempt, attempt_id)

    def _max_attempts(self, provider_key: str) -> int:
        policy = self.session.scalars(
            select(RetryPolicy)
            .where(RetryPolicy.status == "ACTIVE")
            .where((RetryPolicy.provider_key == provider_key) | (RetryPolicy.provider_key.is_(None)))
            .order_by(RetryPolicy.provider_key.desc().nullslast())
            .limit(1)
        ).one_or_none()
        if policy is None:
            return 2
        return int((policy.policy_blob or {}).get("max_attempts", 2))


class DeadLetterService:
    def __init__(self, session: Session):
        self.session = session

    def create_job(self, *, data: DeadLetterJobCreate, correlation_id: str = "m4-dead-letter") -> DeadLetterJob:
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        job = DeadLetterJob(**payload, metadata_=metadata)
        self.session.add(job)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="dead_letter_job.created",
            aggregate_type="dead_letter_job",
            aggregate_id=job.id,
            target_type="dead_letter_job",
            target_id=job.id,
            correlation_id=correlation_id,
            payload={"queue_name": job.queue_name, "job_type": job.job_type, "replay_state": job.replay_state, "reason_code": job.reason_code},
        )
        if job.next_action:
            ManualActionService(self.session).create_action(
                data=ManualActionCreate(
                    action_type="REPLAY_DEAD_LETTER",
                    target_type="dead_letter_job",
                    target_id=job.id,
                    priority="HIGH",
                    reason_code=job.reason_code or "DEAD_LETTER_REPLAY_REQUIRED",
                    next_action=job.next_action,
                ),
                correlation_id="m4-dead-letter-action",
            )
        return job

    def get_job(self, job_id: uuid.UUID) -> DeadLetterJob | None:
        return self.session.get(DeadLetterJob, job_id)

    def replay_job(self, job_id: uuid.UUID, correlation_id: str = "m4-dead-letter-replay") -> DeadLetterJob:
        job = self.get_job(job_id)
        if job is None:
            raise NotFoundError(f"dead letter job not found: {job_id}")
        if job.replay_state != "REPLAYABLE":
            raise ValidationFailureError(f"dead letter job is not replayable: {job.replay_state}")
        job.replay_state = "REPLAYED"
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="dead_letter_job.replayed",
            aggregate_type="dead_letter_job",
            aggregate_id=job.id,
            target_type="dead_letter_job",
            target_id=job.id,
            correlation_id=correlation_id,
            payload={"replay_state": job.replay_state, "reason_code": job.reason_code},
        )
        return job


class OpsIncidentService:
    def __init__(self, session: Session):
        self.session = session

    def create_incident(self, *, data: OpsIncidentCreate, correlation_id: str = "m4-ops-incident") -> OpsIncident:
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        incident = OpsIncident(**payload, metadata_=metadata)
        self.session.add(incident)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="ops_incident.created",
            aggregate_type="ops_incident",
            aggregate_id=incident.id,
            target_type="ops_incident",
            target_id=incident.id,
            correlation_id=correlation_id,
            payload={"incident_type": incident.incident_type, "severity": incident.severity, "state": incident.state, "reason_codes": incident.reason_codes},
        )
        return incident

    def list_incidents(self) -> list[OpsIncident]:
        return list(self.session.scalars(select(OpsIncident).order_by(OpsIncident.created_at.desc())).all())

    def transition(self, incident_id: uuid.UUID, state: str, *, correlation_id: str = "m4-ops-incident-state") -> OpsIncident:
        incident = self.session.get(OpsIncident, incident_id)
        if incident is None:
            raise NotFoundError(f"ops incident not found: {incident_id}")
        if state == "ACKNOWLEDGED":
            if incident.state != "OPEN":
                raise ValidationFailureError("only OPEN incidents can be acknowledged")
            incident.state = state
            incident.acknowledged_at = utc_now()
            event_type = "ops_incident.acknowledged"
        elif state == "RESOLVED":
            if incident.state not in {"OPEN", "ACKNOWLEDGED"}:
                raise ValidationFailureError("only OPEN/ACKNOWLEDGED incidents can be resolved")
            incident.state = state
            incident.resolved_at = utc_now()
            event_type = "ops_incident.resolved"
        else:
            raise ValidationFailureError(f"unsupported incident transition: {state}")
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type=event_type,
            aggregate_type="ops_incident",
            aggregate_id=incident.id,
            target_type="ops_incident",
            target_id=incident.id,
            correlation_id=correlation_id,
            payload={"state": incident.state, "reason_codes": incident.reason_codes},
        )
        return incident


class ManualActionService:
    def __init__(self, session: Session):
        self.session = session

    def create_action(self, *, data: ManualActionCreate, correlation_id: str = "m4-manual-action") -> ManualAction:
        action = ManualAction(**data.model_dump())
        self.session.add(action)
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="manual_action.created",
            aggregate_type="manual_action",
            aggregate_id=action.id,
            target_type="manual_action",
            target_id=action.id,
            correlation_id=correlation_id,
            payload={"action_type": action.action_type, "priority": action.priority, "state": action.state, "reason_code": action.reason_code},
        )
        return action

    def list_actions(self) -> list[ManualAction]:
        return list(self.session.scalars(select(ManualAction).order_by(ManualAction.created_at.desc())).all())

    def complete_action(self, action_id: uuid.UUID, *, correlation_id: str = "m4-manual-action-complete") -> ManualAction:
        action = self.session.get(ManualAction, action_id)
        if action is None:
            raise NotFoundError(f"manual action not found: {action_id}")
        if action.state in {"DONE", "CANCELLED"}:
            return action
        action.state = "DONE"
        self.session.flush()
        _record_ops_event(
            self.session,
            event_type="manual_action.completed",
            aggregate_type="manual_action",
            aggregate_id=action.id,
            target_type="manual_action",
            target_id=action.id,
            correlation_id=correlation_id,
            payload={"state": action.state, "action_type": action.action_type},
        )
        return action


def _credential_health_from_status(
    status: str,
    override: str | None,
    reason_codes: list[str],
    next_action: str | None,
) -> tuple[str, list[str], str | None]:
    if override is not None:
        state = override
    else:
        state = {
            "CONFIGURED": "HEALTHY",
            "MISSING": "MISSING",
            "EXPIRED": "EXPIRED",
            "REVOKED": "REVOKED",
            "DISABLED": "MISCONFIGURED",
            "UNKNOWN": "UNKNOWN",
        }.get(status, "UNKNOWN")
    defaults = {
        "HEALTHY": ["SYSTEM_OK"],
        "MISSING": ["PROVIDER_CREDENTIAL_MISSING"],
        "EXPIRED": ["PROVIDER_CREDENTIAL_EXPIRED"],
        "REVOKED": ["PROVIDER_CREDENTIAL_MISSING"],
        "MISCONFIGURED": ["PROVIDER_CREDENTIAL_MISSING"],
        "UNKNOWN": ["PROVIDER_POLICY_FIT_UNKNOWN"],
    }
    reasons = reason_codes or defaults[state]
    action = next_action
    if state in {"MISSING", "EXPIRED", "REVOKED", "MISCONFIGURED"} and not action:
        action = "Review credential reference and configure a valid secret_ref."
    return state, reasons, action


def _provider_health_from_response(response: Any, next_action: str | None) -> tuple[str, list[str], str | None]:
    if response.ok:
        return "HEALTHY", ["SYSTEM_OK"], None
    mapping = {
        "PROVIDER_TIMEOUT": ("DEGRADED", ["PROVIDER_DEGRADED"], "Retry provider health check or inspect provider status."),
        "PROVIDER_QUOTA_EXCEEDED": ("QUOTA_EXHAUSTED", ["PROVIDER_QUOTA_EXHAUSTED"], "Review quota account and provider plan."),
        "MALFORMED_OUTPUT": ("DEGRADED", ["PROVIDER_DEGRADED"], "Inspect provider contract output."),
        "PROVIDER_UNAVAILABLE": ("UNAVAILABLE", ["PROVIDER_UNAVAILABLE"], "Investigate provider outage."),
        "RETRYABLE_PROVIDER_ERROR": ("DEGRADED", ["RETRYABLE_PROVIDER_ERROR"], "Retry after backoff."),
        "NON_RETRYABLE_PROVIDER_ERROR": ("UNAVAILABLE", ["NON_RETRYABLE_PROVIDER_ERROR"], "Manual provider investigation required."),
        "CIRCUIT_BREAKER_OPEN": ("UNAVAILABLE", ["CIRCUIT_BREAKER_OPEN"], "Wait for cool-down or inspect failures."),
    }
    state, reasons, action = mapping.get(response.error_code, ("UNKNOWN", ["PROVIDER_DEGRADED"], "Investigate provider health."))
    return state, reasons, next_action or action


def _classify_attempt(response: Any, mode: str) -> AttemptClassification:
    if response.ok:
        return AttemptClassification("SUCCESS", None, None, None)
    if mode == "quota_exceeded":
        return AttemptClassification("QUOTA_REJECTED", response.error_code, "redacted provider error", "QUOTA_EXHAUSTED")
    if mode == "circuit_open":
        return AttemptClassification("CIRCUIT_OPEN", response.error_code, "redacted provider error", "CIRCUIT_BREAKER_OPEN")
    if response.retryable:
        return AttemptClassification("RETRYABLE_FAILURE", response.error_code, "redacted provider error", "RETRYABLE_PROVIDER_ERROR")
    return AttemptClassification("NON_RETRYABLE_FAILURE", response.error_code, "redacted provider error", "NON_RETRYABLE_PROVIDER_ERROR")


def _quota_has_capacity(account: QuotaAccount, amount: Decimal, *, include_reserved: bool = True) -> bool:
    if account.status in {"DISABLED", "EXHAUSTED"}:
        return False
    if account.quota_limit is None:
        return True
    used = _decimal(account.quota_used)
    reserved = _decimal(account.quota_reserved) if include_reserved else Decimal("0")
    return used + reserved + amount <= _decimal(account.quota_limit)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return _decimal(value)


def _validate_secret_ref(secret_ref: str | None) -> None:
    if secret_ref is None:
        return
    if any(marker in secret_ref for marker in RAW_SECRET_MARKERS):
        raise ValidationFailureError("secret_ref must be a reference, not a raw secret")


def _ensure_no_secret_payload(value: Any) -> None:
    for key, item in _walk_items(value):
        normalized = key.lower().replace("-", "_")
        if any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS) and normalized != "secret_ref":
            raise ValidationFailureError(f"secret-like payload key is not allowed: {key}")
        if isinstance(item, str) and any(marker in item for marker in RAW_SECRET_MARKERS):
            raise ValidationFailureError("raw secret-like value is not allowed")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and any(marker in value for marker in RAW_SECRET_MARKERS):
        return "[REDACTED]"
    return value


def _walk_items(value: Any) -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        items: list[tuple[str, Any]] = []
        for key, child in value.items():
            items.append((str(key), child))
            items.extend(_walk_items(child))
        return items
    if isinstance(value, list):
        items = []
        for child in value:
            items.extend(_walk_items(child))
        return items
    return []


def _record_ops_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    correlation_id: str,
    payload: dict[str, Any],
) -> tuple[AuditEvent, DomainEvent]:
    safe_payload = _redact(payload)
    audit = AuditService(session).append(
        AuditEnvelope(
            actor_type="system",
            action=event_type,
            target_type=target_type,
            target_id=target_id,
            reason_code="AUDIT_EVENT_RECORDED",
            correlation_id=correlation_id,
            payload=safe_payload,
        )
    )
    domain = DomainEventBus(session).append(
        EventEnvelope(
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=safe_payload,
            metadata={"milestone": "M4"},
            correlation_id=correlation_id,
            causation_id=audit.id,
        )
    )
    return audit, domain
