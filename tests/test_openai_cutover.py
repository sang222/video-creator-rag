from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.core.actor import authenticated_actor_context
from app.core.config import get_settings
from app.db.models import CredentialReference, OpenAICanaryArtifact, QuotaAccount
from app.providers.base import ProviderResponse
from app.services.m10_1 import FINAL_LANES, LLMRouterService
from app.services.openai_cutover import CANARY_INVENTORY, OpenAICutoverService
from app.services.rbac import RBACService
from tests.qualification.conftest import QualificationFactory


class _CanarySuccessProvider:
    provider_key = "OPENAI"

    def __init__(self) -> None:
        self.calls = 0
        self.contact_sheet_seen = False

    def respond(self, *, request) -> ProviderResponse:
        self.calls += 1
        match = re.search(r'"artifact_key": "([^"]+)"', request.prompt or "")
        assert match is not None
        artifact_key = match.group(1)
        if artifact_key == "contact-sheet-review":
            assert request.image_inputs is not None
            assert request.image_inputs[0]["media_type"] == "image/png"
            self.contact_sheet_seen = True
        return ProviderResponse(
            ok=True,
            output={
                "content": (
                    '{"artifact_key":"%s","acceptance":"PASS",'
                    '"schema_version":"v1"}' % artifact_key
                ),
                "json": {
                    "artifact_key": artifact_key,
                    "acceptance": "PASS",
                    "schema_version": "v1",
                },
                "request_id": f"resp-{artifact_key}",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "output_tokens": 5,
                    "reasoning_tokens": 0,
                },
            },
            latency_ms=1,
        )


class _CredentialRejectedProvider:
    provider_key = "OPENAI"

    def __init__(self) -> None:
        self.calls = 0

    def respond(self, *, request) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            ok=False,
            error_code="OPENAI_CREDENTIAL_REJECTED",
            error_message="OpenAI returned HTTP 401 (invalid_api_key).",
            retryable=False,
            latency_ms=1,
        )


@pytest.fixture
def openai_canary_runtime(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("VCOS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("VCOS_LLM_REAL_EXECUTION_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _authority(db_session):
    scope = QualificationFactory(db_session).channel_scope(
        name="OpenAI cutover", strict_long_form=True
    )
    permissions = RBACService(db_session).permissions_for_user(
        user_id=scope.admin.id,
        company_id=scope.company.id,
    )
    actor = authenticated_actor_context(
        canonical_user_id=scope.admin.id,
        operator_user_id=scope.admin.id,
        actor_role="OWNER_ADMIN",
        permissions=permissions,
    )
    return scope, actor


def test_openai_canary_freezes_exact_lanes_costs_and_idempotency(
    db_session, openai_canary_runtime
) -> None:
    scope, actor = _authority(db_session)
    service = OpenAICutoverService(db_session)
    authority = service.establish_authority(actor=actor, company_id=scope.company.id)
    provider = _CanarySuccessProvider()
    router = LLMRouterService(db_session, provider=provider)

    result = service.run_canary(
        actor=actor,
        company_id=scope.company.id,
        receipt_id=authority.receipt.id,
        router=router,
    )

    assert result.status == "CANARY_PASSED"
    assert result.total == len(CANARY_INVENTORY) == 22
    assert result.succeeded == len(CANARY_INVENTORY)
    assert result.failed == 0
    assert provider.calls == len(CANARY_INVENTORY)
    assert provider.contact_sheet_seen is True
    artifacts = list(
        db_session.scalars(
            select(OpenAICanaryArtifact).where(
                OpenAICanaryArtifact.cutover_receipt_id == authority.receipt.id
            )
        ).all()
    )
    assert {item.model_id for item in artifacts} == {
        "gpt-5.6-luna",
        "gpt-5.6-terra",
    }
    assert all(item.status == "SUCCESS" for item in artifacts)
    assert all(item.actual_cost_usd is not None for item in artifacts)
    assert all(item.repair_count == 0 for item in artifacts)
    assert {item.reasoning_effort for item in artifacts} == {
        "none",
        "low",
        "medium",
        "high",
    }
    assert all(not lane.get("fallback_models") for lane in FINAL_LANES)
    quota = db_session.get(QuotaAccount, authority.quota_account.id)
    assert quota is not None
    assert quota.quota_reserved == 0
    assert quota.quota_used == result.actual_cost_usd

    replay = service.run_canary(
        actor=actor,
        company_id=scope.company.id,
        receipt_id=authority.receipt.id,
        router=router,
    )
    assert replay.status == "CANARY_PASSED"
    assert replay.skipped_idempotent == len(CANARY_INVENTORY)
    assert provider.calls == len(CANARY_INVENTORY)
    assert (
        db_session.get(QuotaAccount, authority.quota_account.id).quota_used
        == result.actual_cost_usd
    )


def test_openai_canary_revokes_rejected_credential_and_stops_after_one_request(
    db_session, openai_canary_runtime
) -> None:
    scope, actor = _authority(db_session)
    service = OpenAICutoverService(db_session)
    authority = service.establish_authority(actor=actor, company_id=scope.company.id)
    provider = _CredentialRejectedProvider()

    result = service.run_canary(
        actor=actor,
        company_id=scope.company.id,
        receipt_id=authority.receipt.id,
        router=LLMRouterService(db_session, provider=provider),
    )

    assert result.status == "BLOCKED"
    assert result.failed == 1
    assert provider.calls == 1
    credential = db_session.get(CredentialReference, authority.credential.id)
    assert credential is not None
    assert credential.status == "REVOKED"
    assert db_session.get(QuotaAccount, authority.quota_account.id).quota_reserved == 0

    renewed = service.authorize_rotated_credential(
        actor=actor,
        company_id=scope.company.id,
        receipt_id=authority.receipt.id,
    )

    assert renewed.status == "CONFIGURED"
    resumed_authority = service.establish_authority(
        actor=actor, company_id=scope.company.id
    )
    assert resumed_authority.receipt.status == "READY"
    resumed_provider = _CanarySuccessProvider()
    resumed = service.run_canary(
        actor=actor,
        company_id=scope.company.id,
        receipt_id=authority.receipt.id,
        router=LLMRouterService(db_session, provider=resumed_provider),
    )

    assert resumed.status == "CANARY_PASSED"
    assert resumed_provider.calls == len(CANARY_INVENTORY)
