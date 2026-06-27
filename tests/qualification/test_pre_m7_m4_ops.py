from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

import app.providers.mock as runtime_mock
from app.contracts import (
    BudgetGateCheckRequest,
    BudgetPolicyCreate,
    CostEventCreate,
    CredentialHealthSnapshotCreate,
    CredentialReferenceCreate,
    DeadLetterJobCreate,
    ProviderRegistryEntryCreate,
    QuotaAccountCreate,
    QuotaEventRequest,
    RetryPolicyCreate,
)
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import AuditEvent, CostEvent, CredentialHealthSnapshot, DeadLetterJob, DomainEvent, ProviderAttempt
from app.services import (
    BudgetGateService,
    ConfigRegistryService,
    CostService,
    CredentialReferenceService,
    DeadLetterService,
    ProviderHealthService,
    ProviderRegistryService,
    QuotaService,
    RetryOpsService,
    SystemHealthService,
)
from tests.fakes.providers import FakeAnalyticsProvider, FakeLLMProvider, FakeMediaProvider, FakePlatformProvider, FakeStorageProvider, FakeTTSProvider

from .conftest import ROOT
from .helpers.qualification_asserts import assert_no_secret_payload


REAL_PROVIDER_KEY = "ollama"


def _seed_real_provider(db_session) -> None:
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    registry = ProviderRegistryService(db_session)
    if registry.get_entry(REAL_PROVIDER_KEY) is None:
        registry.create_entry(
            data=ProviderRegistryEntryCreate(
                provider_key=REAL_PROVIDER_KEY,
                provider_name="Ollama / LLMRouter",
                provider_type="LLM",
                capability_blob={"llm_router_lane_bound": True, "guarded_real_execution": True},
                policy_fit_blob={"production_enabled_when_configured": True},
                metadata={"readiness_provider_key": REAL_PROVIDER_KEY},
            )
        )


def test_m4_real_provider_ops_and_test_fakes_are_separated(db_session) -> None:
    _seed_real_provider(db_session)
    entries = ProviderRegistryService(db_session).list_entries()
    assert {entry.provider_key for entry in entries} == {REAL_PROVIDER_KEY}
    with pytest.raises(ValidationFailureError, match="Runtime mock providers were removed"):
        ProviderRegistryService(db_session).seed_mock_providers()
    with pytest.raises(runtime_mock.RuntimeMockProviderRemoved):
        runtime_mock.MockLLMProvider()

    assert FakeLLMProvider().generate(prompt="x").output["json"]["fixture"] == "fake_llm"
    assert FakeTTSProvider().synthesize(text="hello").output["audio_ref"]
    assert FakeMediaProvider().resolve_media(query="q").output["media_ref"]
    assert FakeStorageProvider().store(object_key="a", payload_ref="fake://p").output["storage_ref"]
    assert FakePlatformProvider().check_platform(target_ref="t").output["platform_ref"]
    assert FakeAnalyticsProvider().fetch_metrics(metric_key="views").output["metrics"][0]["value"] == 0
    for mode in ["timeout", "quota_exceeded", "malformed", "unavailable", "retryable_error", "non_retryable_error", "circuit_open"]:
        assert FakeLLMProvider().generate(prompt="x", mode=mode).ok is False

    credential = CredentialReferenceService(db_session).create_reference(
        data=CredentialReferenceCreate(
            provider_key=REAL_PROVIDER_KEY,
            credential_key="primary",
            credential_type="API_KEY",
            secret_ref="env://OLLAMA_BASE_URL",
            status="EXPIRED",
        )
    )
    with pytest.raises(ValidationFailureError):
        CredentialReferenceService(db_session).create_reference(
            data=CredentialReferenceCreate(provider_key=REAL_PROVIDER_KEY, credential_key="raw", credential_type="API_KEY", secret_ref="sk-raw")
        )
    first = CredentialReferenceService(db_session).check_health(data=CredentialHealthSnapshotCreate(credential_reference_id=credential.id))
    credential.status = "REVOKED"
    second = CredentialReferenceService(db_session).check_health(data=CredentialHealthSnapshotCreate(credential_reference_id=credential.id))
    assert first.id != second.id
    assert db_session.query(CredentialHealthSnapshot).filter_by(credential_reference_id=credential.id).count() == 2
    assert second.next_action

    quota = QuotaService(db_session).create_account(
        data=QuotaAccountCreate(provider_key=REAL_PROVIDER_KEY, quota_scope_type="GLOBAL", quota_window="DAILY", quota_limit=Decimal("1"), unit="REQUESTS")
    )
    reserved = QuotaService(db_session).reserve_quota(data=QuotaEventRequest(quota_account_id=quota.id, amount=Decimal("1")))
    consumed = QuotaService(db_session).consume_quota(data=QuotaEventRequest(quota_account_id=quota.id, amount=Decimal("1")))
    rejected = QuotaService(db_session).reserve_quota(data=QuotaEventRequest(quota_account_id=quota.id, amount=Decimal("1")))
    assert [reserved.event_type, consumed.event_type, rejected.event_type] == ["RESERVE", "CONSUME", "REJECT"]
    assert rejected.reason_code == "QUOTA_EXHAUSTED"

    policy = BudgetGateService(db_session).create_policy(
        data=BudgetPolicyCreate(
            policy_key="pre-m7-budget",
            scope_type="GLOBAL",
            status="ACTIVE",
            policy_blob={"require_manual_approval_above_usd": 5},
        )
    )
    review = BudgetGateService(db_session).check(data=BudgetGateCheckRequest(policy_key=policy.policy_key, estimated_cost=Decimal("6")))
    blocked = BudgetGateService(db_session).check(
        data=BudgetGateCheckRequest(policy_key=policy.policy_key, quota_account_id=quota.id, quota_amount=Decimal("1"))
    )
    assert review.decision == "REVIEW_REQUIRED"
    assert blocked.decision == "BLOCK"

    estimated = CostService(db_session).record_event(
        data=CostEventCreate(provider_key=REAL_PROVIDER_KEY, cost_scope_type="GLOBAL", amount=Decimal("0"), cost_type="ESTIMATED")
    )
    assert estimated.cost_type == "ESTIMATED"
    assert "revenue" not in json.dumps(estimated.metadata_, default=str).lower()

    health = ProviderHealthService(db_session).check_provider(provider_key=REAL_PROVIDER_KEY, mode="metadata_only")
    system = SystemHealthService(db_session).create_snapshot()
    assert health.health_state == "UNKNOWN"
    assert health.metadata_["provider_call"] is False
    assert system.next_action
    assert_no_secret_payload([event.payload for event in db_session.scalars(select(AuditEvent)).all()])
    assert_no_secret_payload([event.payload for event in db_session.scalars(select(DomainEvent)).all()])


def test_m4_retry_dead_letter_replay_and_mock_attempts_removed(db_session) -> None:
    _seed_real_provider(db_session)
    retry = RetryOpsService(db_session)
    retry.create_policy(data=RetryPolicyCreate(policy_key="pre-m7-retry", provider_key=REAL_PROVIDER_KEY, status="ACTIVE", policy_blob={"max_attempts": 2}))
    before = db_session.query(ProviderAttempt).count()
    with pytest.raises(ValidationFailureError, match="Runtime mock provider attempts were removed"):
        retry.record_mock_attempt(provider_key=REAL_PROVIDER_KEY, mode="success")
    assert db_session.query(ProviderAttempt).count() == before

    dead_letter = DeadLetterService(db_session).create_job(
        data=DeadLetterJobCreate(
            queue_name="provider_attempts",
            job_type="metadata_only_check",
            reason_code="MAX_RETRY_EXCEEDED",
            next_action="Review provider readiness.",
        )
    )
    replayed = DeadLetterService(db_session).replay_job(dead_letter.id)
    assert replayed.replay_state == "REPLAYED"
    assert db_session.scalar(select(DeadLetterJob).where(DeadLetterJob.reason_code == "MAX_RETRY_EXCEEDED")) is not None

    attempt = ProviderAttempt(
        provider_key=REAL_PROVIDER_KEY,
        operation_key="metadata_only_check",
        attempt_number=1,
        status="SUCCESS",
        started_at=utc_now(),
        finished_at=utc_now(),
        metadata_={"provider_call": False},
    )
    db_session.add(attempt)
    db_session.commit()
    attempt.status = "MUTATED"
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()

    assert db_session.scalar(select(CostEvent).where(CostEvent.provider_key == REAL_PROVIDER_KEY).limit(1)) is None
