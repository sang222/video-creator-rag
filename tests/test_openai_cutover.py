from __future__ import annotations

import base64
import re
import struct
import zlib

import pytest
from sqlalchemy import select

from app.core.actor import authenticated_actor_context
from app.core.config import get_settings
from app.db.models import CredentialReference, OpenAICanaryArtifact, QuotaAccount
from app.providers.base import ProviderResponse
from app.services.m10_1 import FINAL_LANES, LLMRouterService
from app.services.openai_cutover import (
    CANARY_INVENTORY,
    OpenAICutoverService,
    _canary_image_inputs,
)
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


class _InvalidContactSheetProvider(_CanarySuccessProvider):
    def respond(self, *, request) -> ProviderResponse:
        match = re.search(r'"artifact_key": "([^"]+)"', request.prompt or "")
        assert match is not None
        if match.group(1) == "contact-sheet-review":
            self.calls += 1
            return ProviderResponse(
                ok=False,
                error_code="PROVIDER_HTTP_ERROR",
                error_message="OpenAI returned HTTP 400 (invalid_value).",
                retryable=False,
                latency_ms=1,
            )
        return super().respond(request=request)


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


def test_contact_sheet_canary_fixture_is_a_valid_png():
    inputs = _canary_image_inputs(
        {"artifact_key": "contact-sheet-review", "visual_input": "contact_sheet"}
    )
    assert inputs is not None
    image_url = inputs[0]["image_url"]
    encoded = image_url.removeprefix("data:image/png;base64,")
    image = base64.b64decode(encoded, validate=True)
    assert image.startswith(b"\x89PNG\r\n\x1a\n")

    offset = 8
    kinds: list[bytes] = []
    while offset < len(image):
        length = struct.unpack(">I", image[offset : offset + 4])[0]
        kind = image[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        data = image[data_start:data_end]
        expected_crc = struct.unpack(">I", image[data_end : data_end + 4])[0]
        assert zlib.crc32(kind + data) & 0xFFFFFFFF == expected_crc
        kinds.append(kind)
        offset = data_end + 4
    assert kinds == [b"IHDR", b"IDAT", b"IEND"]


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
    # The rejected receipt/artifact stay immutable.  Rotation authorizes a
    # fresh, deterministic receipt with a distinct artifact namespace.
    rotated_receipt_id = service.rotated_canary_receipt_id(
        receipt_id=authority.receipt.id
    )
    assert rotated_receipt_id != authority.receipt.id
    assert authority.receipt.status == "BLOCKED"
    historical_artifact = db_session.scalar(
        select(OpenAICanaryArtifact).where(
            OpenAICanaryArtifact.cutover_receipt_id == authority.receipt.id
        )
    )
    assert historical_artifact is not None
    assert historical_artifact.status == "FAILED"
    resumed_provider = _CanarySuccessProvider()
    resumed = service.run_canary(
        actor=actor,
        company_id=scope.company.id,
        receipt_id=rotated_receipt_id,
        router=LLMRouterService(db_session, provider=resumed_provider),
    )

    assert resumed.status == "CANARY_PASSED"
    assert resumed_provider.calls == len(CANARY_INVENTORY)


def test_openai_canary_retries_only_a_repaired_non_auth_artifact(
    db_session, openai_canary_runtime
) -> None:
    scope, actor = _authority(db_session)
    service = OpenAICutoverService(db_session)
    authority = service.establish_authority(actor=actor, company_id=scope.company.id)

    first_provider = _InvalidContactSheetProvider()
    first = service.run_canary(
        actor=actor,
        company_id=scope.company.id,
        receipt_id=authority.receipt.id,
        router=LLMRouterService(db_session, provider=first_provider),
    )
    assert first.status == "BLOCKED"
    assert first.succeeded == len(CANARY_INVENTORY) - 1
    assert first.failed == 1
    assert first_provider.calls == len(CANARY_INVENTORY)

    repaired_provider = _CanarySuccessProvider()
    resumed = service.run_canary(
        actor=actor,
        company_id=scope.company.id,
        receipt_id=authority.receipt.id,
        router=LLMRouterService(db_session, provider=repaired_provider),
    )
    assert resumed.status == "CANARY_PASSED"
    assert resumed.succeeded == 1
    assert resumed.skipped_idempotent == len(CANARY_INVENTORY) - 1
    assert repaired_provider.calls == 1
