from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.contracts import (
    BudgetGateCheckRequest,
    BudgetPolicyCreate,
    CostEventCreate,
    CredentialHealthSnapshotCreate,
    CredentialReferenceCreate,
    ProviderAttemptMockRequest,
    QuotaAccountCreate,
    QuotaEventRequest,
    RetryPolicyCreate,
)
from app.core.errors import ValidationFailureError
from app.db.models import AuditEvent, CostEvent, CredentialHealthSnapshot, DeadLetterJob, DomainEvent, ProviderAttempt
from app.providers import MockAnalyticsProvider, MockLLMProvider, MockMediaProvider, MockPlatformProvider, MockStorageProvider, MockTTSProvider
from app.services import (
    BudgetGateService,
    CostService,
    CredentialReferenceService,
    DeadLetterService,
    ProviderHealthService,
    ProviderRegistryService,
    QuotaService,
    RetryOpsService,
    SystemHealthService,
)

from .helpers.qualification_asserts import assert_no_secret_payload


def test_m4_mock_providers_modes_credentials_quota_cost_and_health(db_session, qualification_factory) -> None:
    qualification_factory.seed_all()
    entries = ProviderRegistryService(db_session).list_entries()
    assert {entry.provider_key for entry in entries} == {
        "mock_llm",
        "mock_tts",
        "mock_media",
        "mock_storage",
        "mock_platform",
        "mock_analytics",
    }
    assert MockLLMProvider().generate(prompt="x").output["json"]["fixture"] == "mock_llm"
    assert MockTTSProvider().synthesize(text="hello").output["audio_ref"]
    assert MockMediaProvider().resolve_media(query="q").output["license_ref"]
    assert MockStorageProvider().store(object_key="a", payload_ref="mock://p").output["storage_ref"]
    assert MockPlatformProvider().check_platform(target_ref="t").output["status"] == "reachable"
    assert MockAnalyticsProvider().fetch_metrics(metric_key="views").output["metrics"][0]["value"] == 0
    for mode in ["timeout", "quota_exceeded", "malformed", "unavailable", "retryable_error", "non_retryable_error", "circuit_open"]:
        assert MockLLMProvider().generate(prompt="x", mode=mode).ok is False

    credential = CredentialReferenceService(db_session).create_reference(
        data=CredentialReferenceCreate(
            provider_key="mock_llm",
            credential_key="primary",
            credential_type="API_KEY",
            secret_ref="env://MOCK_LLM_API_KEY",
            status="EXPIRED",
        )
    )
    with pytest.raises(ValidationFailureError):
        CredentialReferenceService(db_session).create_reference(
            data=CredentialReferenceCreate(provider_key="mock_llm", credential_key="raw", credential_type="API_KEY", secret_ref="sk-raw")
        )
    first = CredentialReferenceService(db_session).check_health(data=CredentialHealthSnapshotCreate(credential_reference_id=credential.id))
    credential.status = "REVOKED"
    second = CredentialReferenceService(db_session).check_health(data=CredentialHealthSnapshotCreate(credential_reference_id=credential.id))
    assert first.id != second.id
    assert db_session.query(CredentialHealthSnapshot).filter_by(credential_reference_id=credential.id).count() == 2
    assert second.next_action

    quota = QuotaService(db_session).create_account(
        data=QuotaAccountCreate(provider_key="mock_llm", quota_scope_type="GLOBAL", quota_window="DAILY", quota_limit=Decimal("1"), unit="REQUESTS")
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
        data=CostEventCreate(provider_key="mock_llm", cost_scope_type="GLOBAL", amount=Decimal("0"), cost_type="ESTIMATED")
    )
    assert estimated.cost_type == "ESTIMATED"
    assert "revenue" not in json.dumps(estimated.metadata_, default=str).lower()

    health = ProviderHealthService(db_session).check_provider(provider_key="mock_llm", mode="unavailable")
    system = SystemHealthService(db_session).create_snapshot()
    assert health.health_state == "UNAVAILABLE"
    assert health.next_action
    assert system.next_action
    assert_no_secret_payload([event.payload for event in db_session.scalars(select(AuditEvent)).all()])
    assert_no_secret_payload([event.payload for event in db_session.scalars(select(DomainEvent)).all()])


def test_m4_retry_dead_letter_replay_and_append_only_attempts(db_session, qualification_factory) -> None:
    qualification_factory.seed_all()
    retry = RetryOpsService(db_session)
    retry.create_policy(data=RetryPolicyCreate(policy_key="pre-m7-retry", provider_key="mock_llm", status="ACTIVE", policy_blob={"max_attempts": 2}))
    retry.record_mock_attempt(data=ProviderAttemptMockRequest(provider_key="mock_llm", mode="success"))
    retryable = retry.record_mock_attempt(data=ProviderAttemptMockRequest(provider_key="mock_llm", mode="retryable_error", attempt_number=2))
    retry.record_mock_attempt(data=ProviderAttemptMockRequest(provider_key="mock_llm", mode="non_retryable_error"))
    retry.record_mock_attempt(data=ProviderAttemptMockRequest(provider_key="mock_llm", mode="circuit_open"))
    assert retryable.status == "RETRYABLE_FAILURE"
    dead_letter = db_session.scalars(select(DeadLetterJob).where(DeadLetterJob.reason_code == "MAX_RETRY_EXCEEDED")).one()
    replayed = DeadLetterService(db_session).replay_job(dead_letter.id)
    assert replayed.replay_state == "REPLAYED"

    attempt = db_session.scalars(select(ProviderAttempt).where(ProviderAttempt.status == "SUCCESS")).first()
    attempt.status = "MUTATED"
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()
