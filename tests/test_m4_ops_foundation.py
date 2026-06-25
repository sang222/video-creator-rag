import json
import socket
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.contracts import (
    BudgetGateCheckRequest,
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
from app.core.errors import ConflictError, ValidationFailureError
from app.db.models import (
    AuditEvent,
    CostEvent,
    CredentialHealthSnapshot,
    DeadLetterJob,
    DomainEvent,
    LLMRunSnapshot,
    ProviderAttempt,
    ProviderRegistryEntry,
    QuotaEvent,
)
from app.main import create_app
from app.providers import (
    MockAnalyticsProvider,
    MockLLMProvider,
    MockMediaProvider,
    MockPlatformProvider,
    MockStorageProvider,
    MockTTSProvider,
)
from app.services import (
    BudgetGateService,
    ComponentHealthService,
    ConfigRegistryService,
    CostService,
    CredentialReferenceService,
    DeadLetterService,
    ManualActionService,
    OpsIncidentService,
    ProviderHealthService,
    ProviderRegistryService,
    QuotaService,
    RetryOpsService,
    SystemHealthService,
)

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()

M4_TABLES = {
    "provider_registry_entries",
    "credential_references",
    "credential_health_snapshots",
    "quota_accounts",
    "quota_events",
    "cost_events",
    "budget_policies",
    "provider_health_snapshots",
    "component_health_snapshots",
    "system_health_snapshots",
    "retry_policies",
    "provider_attempts",
    "dead_letter_jobs",
    "ops_incidents",
    "manual_action_queue",
}

FORBIDDEN_SCOPE_FRAGMENTS = {
    "vector",
    "embedding",
    "publish",
    "upload",
    "semantic",
    "memory_promotion",
    "dashboard",
    "source_scrap",
    "source_parse",
    "algorithm_agent",
    "growth_agent",
    "view_agent",
}


def _seed(db_session) -> None:
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    ProviderRegistryService(db_session).seed_mock_providers()


def test_m4_migration_tables_defaults_and_scope_guard(engine, db_session) -> None:
    tables = set(inspect(engine).get_table_names())
    assert M4_TABLES <= tables
    assert not {table for table in tables for fragment in FORBIDDEN_SCOPE_FRAGMENTS if fragment in table}
    _seed(db_session)
    entry = db_session.scalar(select(ProviderRegistryEntry).where(ProviderRegistryEntry.provider_key == "mock_llm"))
    assert entry.capability_blob["supports_json_mode"] is True
    assert entry.policy_fit_blob["mock_only"] is True
    assert entry.cost_model_blob
    assert entry.quota_model_blob
    assert entry.retry_policy_blob
    health = CredentialReferenceService(db_session).create_reference(
        data=CredentialReferenceCreate(
            provider_key="mock_llm",
            credential_key="none",
            credential_type="NONE",
            status="MISSING",
        )
    )
    snapshot = CredentialReferenceService(db_session).check_health(
        data=CredentialHealthSnapshotCreate(credential_reference_id=health.id)
    )
    assert snapshot.reason_codes
    assert isinstance(snapshot.metadata_, dict)
    with engine.connect() as connection:
        revision = connection.execute(text("select version_num from alembic_version")).scalar_one()
    assert revision == "0007_m6_production"


def test_provider_registry_and_mock_providers_are_deterministic(db_session) -> None:
    service = ProviderRegistryService(db_session)
    first = service.seed_mock_providers()
    second = service.seed_mock_providers()
    assert len(first) == 6
    assert [item.id for item in first] == [item.id for item in second]
    with pytest.raises(ConflictError):
        service.create_entry(
            data=ProviderRegistryEntryCreate(
                provider_key="mock_llm",
                provider_name="Duplicate",
                provider_type="LLM",
            )
        )
    assert MockLLMProvider().generate(prompt="x").output["json"]["fixture"] == "mock_llm"
    assert MockTTSProvider().synthesize(text="hello").output["audio_ref"] == "mock://tts/audio.wav"
    assert MockMediaProvider().resolve_media(query="q").output["license_ref"]
    assert MockStorageProvider().store(object_key="a", payload_ref="mock://p").output["storage_ref"]
    assert MockPlatformProvider().check_platform(target_ref="t").output["status"] == "reachable"
    assert MockAnalyticsProvider().fetch_metrics(metric_key="views").output["metrics"][0]["value"] == 0
    assert MockLLMProvider().generate(prompt="x", mode="timeout").retryable is True
    assert MockLLMProvider().generate(prompt="x", mode="quota_exceeded").error_code == "PROVIDER_QUOTA_EXCEEDED"
    assert MockLLMProvider().generate(prompt="x", mode="malformed").output["raw"]


def test_credential_references_redact_and_health_is_history(db_session) -> None:
    _seed(db_session)
    service = CredentialReferenceService(db_session)
    reference = service.create_reference(
        data=CredentialReferenceCreate(
            provider_key="mock_llm",
            credential_key="primary",
            credential_type="API_KEY",
            secret_ref="vault://vcos/mock_llm/primary",
            status="EXPIRED",
            scope_blob={"scopes": ["contract_test"]},
        )
    )
    assert reference.secret_ref == "vault://vcos/mock_llm/primary"
    with pytest.raises(ValidationFailureError):
        service.create_reference(
            data=CredentialReferenceCreate(
                provider_key="mock_llm",
                credential_key="raw",
                credential_type="API_KEY",
                secret_ref="sk-this-is-raw",
            )
        )
    first = service.check_health(data=CredentialHealthSnapshotCreate(credential_reference_id=reference.id))
    reference.status = "REVOKED"
    second = service.check_health(data=CredentialHealthSnapshotCreate(credential_reference_id=reference.id))
    assert first.id != second.id
    assert db_session.query(CredentialHealthSnapshot).filter_by(credential_reference_id=reference.id).count() == 2
    assert first.next_action
    event_text = json.dumps(
        [event.payload for event in db_session.scalars(select(AuditEvent)).all()]
        + [event.payload for event in db_session.scalars(select(DomainEvent)).all()]
    )
    assert "sk-this-is-raw" not in event_text
    assert "vault://vcos/mock_llm/primary" not in event_text


def test_quota_ledger_deterministic_and_budget_gate(db_session) -> None:
    _seed(db_session)
    quota = QuotaService(db_session)
    account = quota.create_account(
        data=QuotaAccountCreate(
            provider_key="mock_llm",
            quota_scope_type="GLOBAL",
            quota_window="DAILY",
            quota_limit=Decimal("10"),
            unit="REQUESTS",
        )
    )
    reserved = quota.reserve_quota(data=QuotaEventRequest(quota_account_id=account.id, amount=Decimal("4")))
    consumed = quota.consume_quota(data=QuotaEventRequest(quota_account_id=account.id, amount=Decimal("3")))
    released = quota.release_quota(data=QuotaEventRequest(quota_account_id=account.id, amount=Decimal("1")))
    rejected = quota.reserve_quota(data=QuotaEventRequest(quota_account_id=account.id, amount=Decimal("100")))
    db_session.refresh(account)
    assert [reserved.event_type, consumed.event_type, released.event_type, rejected.event_type] == ["RESERVE", "CONSUME", "RELEASE", "REJECT"]
    assert account.quota_used == Decimal("3.000000")
    assert account.quota_reserved == Decimal("0.000000")
    assert rejected.reason_code == "QUOTA_EXHAUSTED"
    db_session.commit()
    rejected.event_type = "ADJUST"
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()
    account = quota.require_account(account.id)
    policy = BudgetGateService(db_session).create_policy(
            data=BudgetPolicyCreate(
                policy_key="m4-budget",
                scope_type="GLOBAL",
                policy_blob={"require_manual_approval_above_usd": 5},
                status="ACTIVE",
            )
        )
    review = BudgetGateService(db_session).check(data=BudgetGateCheckRequest(policy_key=policy.policy_key, estimated_cost=Decimal("6")))
    blocked = BudgetGateService(db_session).check(
        data=BudgetGateCheckRequest(policy_key=policy.policy_key, quota_account_id=account.id, quota_amount=Decimal("100"))
    )
    assert review.decision == "REVIEW_REQUIRED"
    assert blocked.decision == "BLOCK"


def test_cost_events_are_append_only_and_not_pnl(db_session) -> None:
    _seed(db_session)
    service = CostService(db_session)
    events = [
        service.record_event(data=CostEventCreate(provider_key="mock_llm", cost_scope_type="GLOBAL", amount=Decimal("1"), cost_type=kind))
        for kind in ["ESTIMATED", "RESERVED", "ACTUAL", "ADJUSTED", "REFUNDED"]
    ]
    assert [event.cost_type for event in service.list_events(provider_key="mock_llm")] == [event.cost_type for event in events]
    assert service.actual_cash_total(cost_scope_type="GLOBAL") == Decimal("3.000000")
    estimated = events[0]
    estimated.amount = Decimal("99")
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()
    app_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app").rglob("*.py"))
    assert "revenue" not in app_text.lower()
    assert "pnl" not in app_text.lower()


def test_provider_component_and_system_health_history(db_session) -> None:
    _seed(db_session)
    healthy = ProviderHealthService(db_session).check_provider(provider_key="mock_llm", mode="success")
    unavailable = ProviderHealthService(db_session).check_provider(provider_key="mock_llm", mode="unavailable")
    assert healthy.health_state == "HEALTHY"
    assert unavailable.health_state == "UNAVAILABLE"
    assert unavailable.next_action
    degraded_component = ComponentHealthService(db_session).create_snapshot(
        data=ComponentHealthSnapshotCreate(
            component_type="QUEUE",
            component_key="mock_queue",
            health_state="DEGRADED",
            reason_codes=["SYSTEM_HEALTH_DEGRADED"],
            next_action="Inspect queue fixture.",
        )
    )
    assert degraded_component.next_action
    system = SystemHealthService(db_session).create_snapshot()
    assert system.overall_state == "BLOCKED"
    assert system.next_action


def test_retry_provider_attempt_dead_letter_and_incident_lifecycle(db_session) -> None:
    _seed(db_session)
    retry = RetryOpsService(db_session)
    retry.create_policy(
        data=RetryPolicyCreate(
            policy_key="mock-retry",
            provider_key="mock_llm",
            status="ACTIVE",
            policy_blob={"max_attempts": 2},
        )
    )
    success = retry.record_mock_attempt(data=ProviderAttemptMockRequest(provider_key="mock_llm", mode="success"))
    retryable = retry.record_mock_attempt(
        data=ProviderAttemptMockRequest(provider_key="mock_llm", mode="retryable_error", attempt_number=2)
    )
    non_retryable = retry.record_mock_attempt(data=ProviderAttemptMockRequest(provider_key="mock_llm", mode="non_retryable_error"))
    circuit = retry.record_mock_attempt(data=ProviderAttemptMockRequest(provider_key="mock_llm", mode="circuit_open"))
    assert success.status == "SUCCESS"
    assert retryable.status == "RETRYABLE_FAILURE"
    assert non_retryable.status == "NON_RETRYABLE_FAILURE"
    assert circuit.status == "CIRCUIT_OPEN"
    dead_letter = db_session.scalar(select(DeadLetterJob).where(DeadLetterJob.reason_code == "MAX_RETRY_EXCEEDED"))
    assert dead_letter is not None
    replayed = DeadLetterService(db_session).replay_job(dead_letter.id)
    assert replayed.replay_state == "REPLAYED"
    assert OpsIncidentService(db_session).list_incidents()[0].incident_type == "PROVIDER_OUTAGE"
    success.status = "CANCELLED"
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()


def test_ops_incident_and_manual_action_lifecycle(db_session) -> None:
    incident_service = OpsIncidentService(db_session)
    incident = incident_service.create_incident(
        data=OpsIncidentCreate(
            incident_type="QUOTA_EXHAUSTED",
            severity="ERROR",
            reason_codes=["QUOTA_EXHAUSTED"],
            next_action="Review quota account.",
        )
    )
    acknowledged = incident_service.transition(incident.id, "ACKNOWLEDGED")
    resolved = incident_service.transition(incident.id, "RESOLVED")
    assert acknowledged.acknowledged_at is not None
    assert resolved.resolved_at is not None
    action = ManualActionService(db_session).create_action(
        data=ManualActionCreate(
            action_type="REVIEW_QUOTA",
            target_type="quota_account",
            priority="HIGH",
            reason_code="QUOTA_EXHAUSTED",
            next_action="Review quota.",
        )
    )
    completed = ManualActionService(db_session).complete_action(action.id)
    assert completed.state == "DONE"
    with pytest.raises(ValidationError):
        OpsIncidentCreate(incident_type="UNKNOWN", severity="WARNING", next_action="")


def test_m4_no_network_and_llm_snapshot_inert(monkeypatch, db_session) -> None:
    _seed(db_session)

    def fail_network(*args, **kwargs):  # pragma: no cover
        raise AssertionError("network/provider call attempted")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    ProviderHealthService(db_session).check_provider(provider_key="mock_llm", mode="success")
    RetryOpsService(db_session).record_mock_attempt(data=ProviderAttemptMockRequest(provider_key="mock_llm", mode="success"))
    assert db_session.scalar(select(LLMRunSnapshot).limit(1)) is None
    provider_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app" / "providers").rglob("*.py"))
    for forbidden in ["openai", "anthropic", "requests", "httpx", "urllib.request", "boto3"]:
        assert f"import {forbidden}" not in provider_text
        assert f"from {forbidden}" not in provider_text


def test_m4_api_and_cli_smoke(db_session) -> None:
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    db_session.commit()
    cli_seed = runner.invoke(cli_app, ["provider", "seed-mocks"])
    assert cli_seed.exit_code == 0, cli_seed.output
    cli_list = runner.invoke(cli_app, ["provider", "list"])
    assert cli_list.exit_code == 0, cli_list.output
    assert any(item["provider_key"] == "mock_llm" for item in json.loads(cli_list.output))
    client = TestClient(create_app())
    health = client.post("/providers/mock_llm/health-check", json={"mode": "quota_exceeded"})
    assert health.status_code == 200, health.text
    assert health.json()["health_state"] == "QUOTA_EXHAUSTED"
    credential = client.post(
        "/credential-references",
        json={
            "provider_key": "mock_llm",
            "credential_key": "api",
            "credential_type": "API_KEY",
            "secret_ref": "vault://vcos/mock",
            "status": "MISSING",
        },
    )
    assert credential.status_code == 200, credential.text
    assert "sk-" not in credential.text
    quota = client.post(
        "/quota-accounts",
        json={"provider_key": "mock_llm", "quota_scope_type": "GLOBAL", "quota_window": "DAILY", "quota_limit": "2", "unit": "REQUESTS"},
    )
    assert quota.status_code == 200, quota.text
    reserve = client.post("/quota-events/reserve", json={"quota_account_id": quota.json()["id"], "amount": "1"})
    assert reserve.status_code == 200, reserve.text
    attempt = runner.invoke(cli_app, ["provider", "attempt-mock", "--provider-key", "mock_llm", "--mode", "success"])
    assert attempt.exit_code == 0, attempt.output
    incident = runner.invoke(
        cli_app,
        ["incident", "create", "--incident-type", "HEALTH_DEGRADED", "--severity", "WARNING", "--next-action", "Review health."],
    )
    assert incident.exit_code == 0, incident.output
    snapshot = runner.invoke(cli_app, ["system-health", "snapshot"])
    assert snapshot.exit_code == 0, snapshot.output


def test_m4_events_written_and_failed_transactions_write_no_events(db_session) -> None:
    _seed(db_session)
    db_session.commit()
    before = (db_session.query(AuditEvent).count(), db_session.query(DomainEvent).count())
    with pytest.raises(ConflictError):
        ProviderRegistryService(db_session).create_entry(
            data=ProviderRegistryEntryCreate(provider_key="mock_llm", provider_name="Dup", provider_type="LLM")
        )
    db_session.rollback()
    after = (db_session.query(AuditEvent).count(), db_session.query(DomainEvent).count())
    assert after == before
    CostService(db_session).record_event(
        data=CostEventCreate(provider_key="mock_llm", cost_scope_type="GLOBAL", amount=Decimal("1"), cost_type="ESTIMATED")
    )
    event_types = {event.event_type for event in db_session.scalars(select(DomainEvent)).all()}
    assert "cost_event.created" in event_types
    payload_text = json.dumps([event.payload for event in db_session.scalars(select(AuditEvent)).all()])
    assert "sk-" not in payload_text


def test_m4_scope_guard_no_m5_plus_tables_or_services(engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert not {table for table in tables for fragment in FORBIDDEN_SCOPE_FRAGMENTS if fragment in table}
    app_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app").rglob("*.py"))
    forbidden_terms = {
        "VectorStore",
        "PublishPackage",
        "PublishUpload",
        "SemanticLayer",
        "MemoryPromotion",
        "OperatorCockpit",
        "DashboardWidget",
        "SourceScraper",
        "SourceParser",
        "OPA",
        "Cedar",
        "AlgorithmAgent",
        "GrowthAgent",
        "ViewAgent",
    }
    for term in forbidden_terms:
        assert term not in app_text
    routes = {route.path for route in create_app().routes}
    assert not {route for route in routes if any(fragment in route for fragment in ["rag", "vector", "publish", "dashboard"])}
