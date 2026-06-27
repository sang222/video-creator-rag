import json
import socket
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError
from typer.testing import CliRunner

import app.providers.mock as runtime_mock
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
    ProviderRegistryEntryCreate,
    QuotaAccountCreate,
    QuotaEventRequest,
    RetryPolicyCreate,
)
from app.core.errors import ConflictError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AuditEvent,
    CostEvent,
    CredentialHealthSnapshot,
    DeadLetterJob,
    DomainEvent,
    LLMRunSnapshot,
    ProviderAttempt,
    ProviderRegistryEntry,
)
from app.main import create_app
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
from tests.fakes.providers import (
    FakeAnalyticsProvider,
    FakeLLMProvider,
    FakeMediaProvider,
    FakePlatformProvider,
    FakeStorageProvider,
    FakeTTSProvider,
)

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()
REAL_PROVIDER_KEY = "ollama"

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
    "publish_upload",
    "upload_jobs",
    "upload_attempts",
    "auto_reupload",
    "semantic",
    "memory_promotion",
    "source_scrap",
    "source_parse",
    "algorithm_agent",
    "growth_agent",
    "view_agent",
}


def _seed(db_session) -> ProviderRegistryEntry:
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    service = ProviderRegistryService(db_session)
    entry = service.get_entry(REAL_PROVIDER_KEY)
    if entry is None:
        entry = service.create_entry(
            data=ProviderRegistryEntryCreate(
                provider_key=REAL_PROVIDER_KEY,
                provider_name="Ollama / LLMRouter",
                provider_type="LLM",
                capability_blob={"llm_router_lane_bound": True, "guarded_real_execution": True},
                policy_fit_blob={"production_enabled_when_configured": True},
                cost_model_blob={"provider_cost_unknown": True},
                metadata={"readiness_provider_key": REAL_PROVIDER_KEY},
            )
        )
    return entry


def _provider_attempt(provider_key: str = REAL_PROVIDER_KEY) -> ProviderAttempt:
    return ProviderAttempt(
        provider_key=provider_key,
        operation_key="metadata_only_check",
        attempt_number=1,
        status="SUCCESS",
        started_at=utc_now(),
        finished_at=utc_now(),
        metadata_={"provider_call": False},
    )


def test_m4_migration_tables_defaults_and_scope_guard(engine, db_session) -> None:
    tables = set(inspect(engine).get_table_names())
    assert M4_TABLES <= tables
    assert not {table for table in tables for fragment in FORBIDDEN_SCOPE_FRAGMENTS if fragment in table}
    entry = _seed(db_session)
    assert entry.provider_key == REAL_PROVIDER_KEY
    assert entry.capability_blob["llm_router_lane_bound"] is True
    health = CredentialReferenceService(db_session).create_reference(
        data=CredentialReferenceCreate(
            provider_key=REAL_PROVIDER_KEY,
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
    assert revision == "0019_m12_1_prompt_registry"


def test_provider_registry_is_real_only_and_test_fakes_are_test_scoped(db_session) -> None:
    service = ProviderRegistryService(db_session)
    entry = _seed(db_session)
    assert service.get_entry(REAL_PROVIDER_KEY).id == entry.id
    with pytest.raises(ValidationFailureError, match="Runtime mock providers were removed"):
        service.seed_mock_providers()
    assert not any(item.provider_key.startswith("mock_") for item in service.list_entries())

    with pytest.raises(runtime_mock.RuntimeMockProviderRemoved):
        runtime_mock.MockLLMProvider()
    assert FakeLLMProvider().generate(prompt="x").output["json"]["fixture"] == "fake_llm"
    assert FakeTTSProvider().synthesize(text="hello").output["audio_ref"] == "fake://tts/audio.wav"
    assert FakeMediaProvider().resolve_media(query="q").output["media_ref"]
    assert FakeStorageProvider().store(object_key="a", payload_ref="fake://p").output["storage_ref"]
    assert FakePlatformProvider().check_platform(target_ref="t").output["platform_ref"]
    assert FakeAnalyticsProvider().fetch_metrics(metric_key="views").output["metrics"][0]["value"] == 0
    assert FakeLLMProvider().generate(prompt="x", mode="timeout").retryable is True
    assert FakeLLMProvider().generate(prompt="x", mode="quota_exceeded").error_code == "PROVIDER_QUOTA_EXCEEDED"
    assert FakeLLMProvider().generate(prompt="x", mode="malformed").output["raw"]


def test_credential_references_redact_and_health_is_history(db_session) -> None:
    _seed(db_session)
    service = CredentialReferenceService(db_session)
    reference = service.create_reference(
        data=CredentialReferenceCreate(
            provider_key=REAL_PROVIDER_KEY,
            credential_key="primary",
            credential_type="API_KEY",
            secret_ref="env://OLLAMA_BASE_URL",
            status="EXPIRED",
            scope_blob={"scopes": ["contract_test"]},
        )
    )
    assert reference.secret_ref == "env://OLLAMA_BASE_URL"
    with pytest.raises(ValidationFailureError):
        service.create_reference(
            data=CredentialReferenceCreate(
                provider_key=REAL_PROVIDER_KEY,
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
    assert "env://OLLAMA_BASE_URL" not in event_text


def test_quota_ledger_deterministic_and_budget_gate(db_session) -> None:
    _seed(db_session)
    quota = QuotaService(db_session)
    account = quota.create_account(
        data=QuotaAccountCreate(
            provider_key=REAL_PROVIDER_KEY,
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
        service.record_event(data=CostEventCreate(provider_key=REAL_PROVIDER_KEY, cost_scope_type="GLOBAL", amount=Decimal("1"), cost_type=kind))
        for kind in ["ESTIMATED", "RESERVED", "ACTUAL", "ADJUSTED", "REFUNDED"]
    ]
    assert [event.cost_type for event in service.list_events(provider_key=REAL_PROVIDER_KEY)] == [event.cost_type for event in events]
    assert service.actual_cash_total(cost_scope_type="GLOBAL") == Decimal("3.000000")
    estimated = events[0]
    estimated.amount = Decimal("99")
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()
    app_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app").rglob("*.py"))
    app_text_without_disabled_placeholder = app_text.lower().replace("revenue_disabled", "")
    assert "revenue" not in app_text_without_disabled_placeholder
    assert "pnl" not in app_text.lower()


def test_provider_component_and_system_health_history(db_session) -> None:
    _seed(db_session)
    health = ProviderHealthService(db_session).check_provider(provider_key=REAL_PROVIDER_KEY, mode="success")
    assert health.health_state == "UNKNOWN"
    assert health.reason_codes == ["PROVIDER_HEALTH_NOT_CHECKED"]
    assert health.metadata_["provider_call"] is False
    degraded_component = ComponentHealthService(db_session).create_snapshot(
        data=ComponentHealthSnapshotCreate(
            component_type="QUEUE",
            component_key="ops_queue",
            health_state="DEGRADED",
            reason_codes=["SYSTEM_HEALTH_DEGRADED"],
            next_action="Inspect queue.",
        )
    )
    assert degraded_component.next_action
    system = SystemHealthService(db_session).create_snapshot()
    assert system.overall_state == "BLOCKED"
    assert system.next_action


def test_retry_mock_attempts_fail_fast_and_append_only_attempts_remain_guarded(db_session) -> None:
    _seed(db_session)
    retry = RetryOpsService(db_session)
    retry.create_policy(
        data=RetryPolicyCreate(
            policy_key="real-metadata-retry",
            provider_key=REAL_PROVIDER_KEY,
            status="ACTIVE",
            policy_blob={"max_attempts": 2},
        )
    )
    before = db_session.query(ProviderAttempt).count()
    with pytest.raises(ValidationFailureError, match="Runtime mock provider attempts were removed"):
        retry.record_mock_attempt(provider_key=REAL_PROVIDER_KEY, mode="success")
    assert db_session.query(ProviderAttempt).count() == before

    dead_letter = DeadLetterService(db_session).create_job(
        data=DeadLetterJobCreate(
            queue_name="provider_attempts",
            job_type="metadata_only_check",
            reason_code="MAX_RETRY_EXCEEDED",
            next_action="Review provider readiness before retry.",
        )
    )
    replayed = DeadLetterService(db_session).replay_job(dead_letter.id)
    assert replayed.replay_state == "REPLAYED"

    attempt = _provider_attempt()
    db_session.add(attempt)
    db_session.commit()
    attempt.status = "CANCELLED"
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
    ProviderHealthService(db_session).check_provider(provider_key=REAL_PROVIDER_KEY, mode="success")
    with pytest.raises(ValidationFailureError):
        RetryOpsService(db_session).record_mock_attempt(provider_key=REAL_PROVIDER_KEY, mode="success")
    assert db_session.scalar(select(LLMRunSnapshot).limit(1)) is None
    provider_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app" / "providers").rglob("*.py"))
    for forbidden in ["openai", "anthropic", "requests", "httpx", "urllib.request", "boto3"]:
        assert f"import {forbidden}" not in provider_text
        assert f"from {forbidden}" not in provider_text


def test_m4_api_and_cli_smoke(db_session) -> None:
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    db_session.commit()
    cli_seed = runner.invoke(cli_app, ["provider", "seed-mocks"])
    assert cli_seed.exit_code != 0
    cli_register = runner.invoke(
        cli_app,
        [
            "provider",
            "register",
            "--provider-key",
            REAL_PROVIDER_KEY,
            "--provider-name",
            "Ollama / LLMRouter",
            "--provider-type",
            "LLM",
            "--capability-json",
            "{\"llm_router_lane_bound\": true}",
        ],
    )
    assert cli_register.exit_code == 0, cli_register.output
    cli_list = runner.invoke(cli_app, ["provider", "list"])
    assert cli_list.exit_code == 0, cli_list.output
    assert any(item["provider_key"] == REAL_PROVIDER_KEY for item in json.loads(cli_list.output))
    client = TestClient(create_app())
    health = client.post(f"/providers/{REAL_PROVIDER_KEY}/health-check", json={"mode": "metadata_only"})
    assert health.status_code == 200, health.text
    assert health.json()["health_state"] == "UNKNOWN"
    credential = client.post(
        "/credential-references",
        json={
            "provider_key": REAL_PROVIDER_KEY,
            "credential_key": "api",
            "credential_type": "API_KEY",
            "secret_ref": "env://OLLAMA_BASE_URL",
            "status": "MISSING",
        },
    )
    assert credential.status_code == 200, credential.text
    assert "sk-" not in credential.text
    quota = client.post(
        "/quota-accounts",
        json={
            "provider_key": REAL_PROVIDER_KEY,
            "quota_scope_type": "GLOBAL",
            "quota_window": "DAILY",
            "quota_limit": "2",
            "unit": "REQUESTS",
        },
    )
    assert quota.status_code == 200, quota.text
    reserve = client.post("/quota-events/reserve", json={"quota_account_id": quota.json()["id"], "amount": "1"})
    assert reserve.status_code == 200, reserve.text
    attempt = runner.invoke(cli_app, ["provider", "attempt-mock", "--provider-key", REAL_PROVIDER_KEY, "--mode", "success"])
    assert attempt.exit_code != 0
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
            data=ProviderRegistryEntryCreate(provider_key=REAL_PROVIDER_KEY, provider_name="Dup", provider_type="LLM")
        )
    db_session.rollback()
    after = (db_session.query(AuditEvent).count(), db_session.query(DomainEvent).count())
    assert after == before
    CostService(db_session).record_event(
        data=CostEventCreate(provider_key=REAL_PROVIDER_KEY, cost_scope_type="GLOBAL", amount=Decimal("1"), cost_type="ESTIMATED")
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
    assert not {route for route in routes if any(fragment in route for fragment in ["rag", "vector", "upload-jobs"])}
    assert {route for route in routes if "dashboard" in route} <= {
        "/dashboard/command-center",
        "/dashboard/queues",
        "/dashboard/queues/{queue_type}",
        "/uploaded-videos/{uploaded_video_id}/dashboard",
    }
