from __future__ import annotations

import socket
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

import app.services.editorial_fresh_evidence as evidence_module
from app.contracts.ops import BudgetPolicyCreate, CredentialReferenceCreate
from app.core.config import get_settings
from app.core.errors import ValidationFailureError
from app.providers.openai import OpenAIResponsesProvider, OpenAIWebSearchRequest
from app.services.editorial_fresh_evidence import (
    EditorialEvidenceProviderActivationService,
    FreshEvidenceProviderError,
    OpenAIWebEvidenceProvider,
    _canonicalize_url,
    _tool_discovery_candidates,
)
from app.services.m10_1 import LLMRouterConfigLoader
from app.db.models.m10_1 import LLMModelProfile, LLMRouterLane
from app.services.m5 import _candidate_declared_claim_text, _ensure_no_secret_payload
from app.services.ops import (
    BudgetGateService,
    CredentialReferenceService,
    ProviderRegistryService,
)
from tests.qualification.conftest import QualificationFactory


POLICY = {
    "search_model": "gpt-5.6-luna",
    "search_reasoning_effort": "low",
    "allowed_domains": ["openai.com"],
    "maximum_search_results": 5,
    "maximum_sources_per_run": 2,
    "timeout_seconds": 10,
    "max_response_bytes": 16_384,
    "max_redirects": 2,
}


def _public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.33.45", 443))]


def _search_provider(*, urls: list[str], status: int = 200) -> OpenAIResponsesProvider:
    def transport(method, url, payload, headers, timeout_seconds):
        assert method == "POST"
        assert url.endswith("/responses")
        assert headers["Authorization"] == "Bearer test-key"
        assert timeout_seconds == 10
        assert payload["tools"] == [
            {
                "type": "web_search",
                "search_context_size": "low",
                "external_web_access": True,
                "filters": {"allowed_domains": ["openai.com"]},
            }
        ]
        assert payload["tool_choice"] == "required"
        if status != 200:
            return status, {"error": {"type": "invalid_api_key"}}
        return 200, {
            "id": "resp_editorial_evidence_1",
            "model": "gpt-5.6-luna",
            "usage": {"input_tokens": 12, "output_tokens": 9, "total_tokens": 21},
            "output": [
                {
                    "id": "ws_editorial_evidence_1",
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {
                        "type": "search",
                        "sources": [
                            {"url": item, "title": "Official source"} for item in urls
                        ],
                    },
                },
                {
                    "id": "msg_editorial_evidence_1",
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Discovery metadata only.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": item,
                                    "title": "Official source",
                                }
                                for item in urls
                            ],
                        }
                    ],
                },
            ],
        }

    return OpenAIResponsesProvider(api_key="test-key", timeout_seconds=10, transport=transport)


def test_openai_web_search_payload_is_bounded_and_has_no_fallback() -> None:
    payload = OpenAIResponsesProvider(api_key="test-key").build_web_search_payload(
        request=OpenAIWebSearchRequest(
            model="gpt-5.6-luna",
            reasoning_effort="low",
            query="Current official documentation for small-team AI workflows",
            allowed_domains=["openai.com"],
        )
    )

    assert payload["tools"][0]["type"] == "web_search"
    assert payload["tools"][0]["external_web_access"] is True
    assert payload["tool_choice"] == "required"
    assert payload["store"] is False
    assert "fallback" not in payload


def test_m5_allows_only_numeric_openai_usage_counters() -> None:
    _ensure_no_secret_payload(
        {
            "usage": {
                "input_tokens": 12,
                "output_tokens": 9,
                "total_tokens": 21,
                "reasoning_tokens": 3,
                "cached_input_tokens": 0,
            }
        }
    )

    with pytest.raises(ValidationFailureError, match="secret-like payload key"):
        _ensure_no_secret_payload({"token": "not-allowed"})
    with pytest.raises(ValidationFailureError, match="usage counter must be"):
        _ensure_no_secret_payload({"input_tokens": "not-a-counter"})


def test_first_launch_claim_scan_excludes_evidence_provenance_directives() -> None:
    claim_text = _candidate_declared_claim_text(
        SimpleNamespace(
            proposed_title="Bounded OpenAI workflow",
            proposed_angle="A source-grounded practical explanation.",
            rationale={
                "editorial_summary": "Show one controlled workflow.",
                "source_pack": {
                    "research_question": "Do not make time-saving claims."
                },
                "research_pack": {"query": "No ROI claims."},
                "claim_evidence_map": [{"claim_scope": "source-grounded"}],
            },
        )
    )

    assert "controlled workflow" in claim_text
    assert "time-saving" not in claim_text
    assert "roi" not in claim_text


def test_existing_openai_registry_activation_is_idempotent(
    db_session, monkeypatch
) -> None:
    # Provider activation requires an explicit real-execution lane authority;
    # an environment credential alone is intentionally insufficient.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("VCOS_LLM_REAL_EXECUTION_ENABLED", "true")
    get_settings.cache_clear()
    scope = QualificationFactory(db_session).channel_scope(
        name="Editorial evidence activation", strict_long_form=True
    )
    entry = ProviderRegistryService(db_session).require_entry("openai")
    CredentialReferenceService(db_session).create_reference(
        data=CredentialReferenceCreate(
            provider_key="openai",
            credential_key="openai_api_key",
            credential_type="API_KEY",
            secret_ref="env:OPENAI_API_KEY",
            status="CONFIGURED",
            metadata={"environment_handle_only": True},
        )
    )
    BudgetGateService(db_session).create_policy(
        data=BudgetPolicyCreate(
            policy_key=f"openai-standard-monthly-{scope.company.id}",
            scope_type="COMPANY",
            scope_id=scope.company.id,
            status="ACTIVE",
            policy_blob={"monthly_hard_cap_usd": "12.00", "per_lane_cap_usd": "1.00"},
        )
    )
    LLMRouterConfigLoader(db_session).ensure_default_profile(profile_key="default")

    first = EditorialEvidenceProviderActivationService(db_session).activate(
        policy_snapshot_id=str(scope.snapshot.id),
        policy_snapshot_hash=scope.snapshot.content_hash,
        company_id=str(scope.company.id),
    )
    second = EditorialEvidenceProviderActivationService(db_session).activate(
        policy_snapshot_id=str(scope.snapshot.id),
        policy_snapshot_hash=scope.snapshot.content_hash,
        company_id=str(scope.company.id),
    )

    assert first.authority.ready
    assert first.changed is True
    assert second.authority.ready
    assert second.changed is False
    assert entry.capability_blob["editorial_evidence_collection"]["operations"] == [
        "search",
        "fetch",
    ]
    assert entry.policy_fit_blob["editorial_evidence_authority"]["allowed_domains"] == [
        "developers.openai.com"
    ]
    assert entry.policy_fit_blob["editorial_evidence_authority"]["max_response_bytes"] == 524_288
    assert entry.policy_fit_blob["editorial_evidence_authority"]["automatic_fallback"] is False


def test_default_router_retires_terra_and_routes_every_lane_to_luna(db_session) -> None:
    loader = LLMRouterConfigLoader(db_session)
    profile = loader.ensure_default_profile(profile_key="default")
    db_session.add(
        LLMModelProfile(
            provider_key="OPENAI",
            model_id="gpt-5.6-terra",
            model_role="PRIMARY",
            lane_names=["long_context_text"],
            is_enabled=True,
            critical_path_allowed=False,
            capability_blob={},
        )
    )
    db_session.flush()

    loader.ensure_default_profile(profile_key="default")
    lanes = list(
        db_session.scalars(
            select(LLMRouterLane).where(LLMRouterLane.router_profile_id == profile.id)
        )
    )
    enabled_models = list(
        db_session.scalars(
            select(LLMModelProfile).where(LLMModelProfile.is_enabled.is_(True))
        )
    )
    terra = db_session.scalar(
        select(LLMModelProfile).where(LLMModelProfile.model_id == "gpt-5.6-terra")
    )

    assert {lane.primary_model for lane in lanes} == {"gpt-5.6-luna"}
    assert {model.model_id for model in enabled_models} == {"gpt-5.6-luna"}
    assert terra is not None and terra.is_enabled is False and terra.lane_names == []


def test_discovery_normalizes_and_deduplicates_tool_urls() -> None:
    candidates = _tool_discovery_candidates(
        response_payload={
            "output": [
                {
                    "id": "ws_1",
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "url": "https://platform.openai.com/docs/guides/tools?utm_source=x#web",
                                "title": "Tools",
                            },
                            {
                                "url": "https://platform.openai.com/docs/guides/tools?utm_campaign=y",
                                "title": "Tools duplicate",
                            },
                            {"url": "https://untrusted.example.test/docs", "title": "Reject"},
                        ]
                    },
                }
            ]
        },
        allowed_domains=["openai.com"],
        maximum_results=5,
    )

    assert candidates == [
        {
            "url": "https://platform.openai.com/docs/guides/tools?utm_source=x#web",
            "canonical_url": "https://platform.openai.com/docs/guides/tools",
            "title": "Tools",
            "result_id": candidates[0]["result_id"],
        }
    ]
    assert len(candidates[0]["result_id"]) == 64
    assert _canonicalize_url("https://platform.openai.com/a?x=1&utm_source=y#z") == "https://platform.openai.com/a?x=1"


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "http://platform.openai.com/docs",
        "file:///etc/passwd",
        "https://localhost/docs",
        "https://127.0.0.1/docs",
        "https://169.254.169.254/latest/meta-data",
        "https://postgres/docs",
    ],
)
def test_fetch_rejects_unsafe_urls_before_network(monkeypatch, unsafe_url: str) -> None:
    monkeypatch.setattr(evidence_module.socket, "getaddrinfo", _public_dns)
    with pytest.raises(FreshEvidenceProviderError):
        evidence_module._assert_safe_fetch_url(
            unsafe_url,
            allowed_domains=["openai.com"],
        )


def test_search_then_fetch_records_distinct_receipts(monkeypatch) -> None:
    monkeypatch.setattr(evidence_module.socket, "getaddrinfo", _public_dns)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html", "etag": "source-v1"},
                content=(
                    b"<html><title>OpenAI official guide</title><body>"
                    b"A sufficiently long official documentation extract about "
                    b"practical workflow tooling for small teams and operators."
                    b"</body></html>"
                ),
                request=request,
            )
        )
    )
    provider = OpenAIWebEvidenceProvider(
        api_key="test-key",
        policy=POLICY,
        now=lambda: datetime(2026, 8, 3, tzinfo=timezone.utc),
        search_provider=_search_provider(
            urls=["https://platform.openai.com/docs/guides/tools?utm_source=test"]
        ),
        http_client=client,
    )

    sources = provider.collect(
        research_question="Find a current official source.",
        maximum_sources=1,
        timeout_seconds=10,
    )

    assert len(sources) == 1
    source = sources[0]
    assert source.source_ref == "https://platform.openai.com/docs/guides/tools"
    assert source.source_class == "OFFICIAL_DOCUMENT"
    assert source.search_receipt["operation"] == "search"
    assert source.fetch_receipt["operation"] == "fetch"
    assert source.fetch_receipt["content_type"] == "text/html"
    assert source.fetch_receipt["raw_response_hash"]
    assert "Discovery metadata only" not in source.retrieved_content


def test_fetches_later_official_result_after_ranked_http_failures(monkeypatch) -> None:
    monkeypatch.setattr(evidence_module.socket, "getaddrinfo", _public_dns)
    fetches: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        fetches.append(str(request.url))
        if len(fetches) < 3:
            return httpx.Response(403, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=(
                b"<html><title>Reachable official guide</title><body>"
                b"A sufficiently long, current first-party documentation extract "
                b"for bounded editorial evidence collection and validation."
                b"</body></html>"
            ),
            request=request,
        )

    provider = OpenAIWebEvidenceProvider(
        api_key="test-key",
        policy={**POLICY, "maximum_fetches_per_run": 3},
        search_provider=_search_provider(
            urls=[
                "https://help.openai.com/en/articles/first-blocked",
                "https://help.openai.com/en/articles/second-blocked",
                "https://developers.openai.com/api/docs/guides/tools-web-search",
            ]
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    sources = provider.collect(
        research_question="Find a current official source.",
        maximum_sources=1,
        timeout_seconds=10,
    )

    assert len(sources) == 1
    assert sources[0].source_ref == "https://developers.openai.com/api/docs/guides/tools-web-search"
    assert len(fetches) == 3
    assert [item["status"] for item in provider.last_fetch_receipts] == [
        "NON_RETRYABLE_FAILURE",
        "NON_RETRYABLE_FAILURE",
        "SUCCESS",
    ]


@pytest.mark.parametrize(
    "headers,body,error_code",
    [
        ({"content-type": "application/octet-stream"}, b"binary", "SOURCE_FETCH_INSUFFICIENT"),
        ({"content-type": "text/html", "content-length": "99999"}, b"x", "SOURCE_FETCH_INSUFFICIENT"),
    ],
)
def test_fetch_rejects_invalid_content_and_size(monkeypatch, headers, body, error_code) -> None:
    monkeypatch.setattr(evidence_module.socket, "getaddrinfo", _public_dns)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers=headers, content=body, request=request)
        )
    )
    provider = OpenAIWebEvidenceProvider(
        api_key="test-key",
        policy=POLICY,
        search_provider=_search_provider(urls=["https://platform.openai.com/docs"]),
        http_client=client,
    )

    with pytest.raises(FreshEvidenceProviderError, match=error_code):
        provider.collect(
            research_question="Find a current official source.",
            maximum_sources=1,
            timeout_seconds=10,
        )
    assert provider.last_fetch_receipts[0]["status"] == "NON_RETRYABLE_FAILURE"


def test_redirect_to_private_target_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(evidence_module.socket, "getaddrinfo", _public_dns)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={"location": "https://127.0.0.1/private"},
                request=request,
            )
        )
    )
    provider = OpenAIWebEvidenceProvider(
        api_key="test-key",
        policy=POLICY,
        search_provider=_search_provider(urls=["https://platform.openai.com/docs"]),
        http_client=client,
    )

    with pytest.raises(FreshEvidenceProviderError, match="SOURCE_FETCH_INSUFFICIENT"):
        provider.collect(
            research_question="Find a current official source.",
            maximum_sources=1,
            timeout_seconds=10,
        )
    assert provider.last_fetch_receipts[0]["error_code"] == "SOURCE_FETCH_SSRF_BLOCKED"


def test_search_failure_is_fail_closed_without_fetch(monkeypatch) -> None:
    monkeypatch.setattr(evidence_module.socket, "getaddrinfo", _public_dns)
    provider = OpenAIWebEvidenceProvider(
        api_key="test-key",
        policy=POLICY,
        search_provider=_search_provider(urls=[], status=401),
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("fetch must not run"))),
    )

    with pytest.raises(FreshEvidenceProviderError, match="OPENAI_AUTHENTICATION_FAILED"):
        provider.collect(
            research_question="Find a current official source.",
            maximum_sources=1,
            timeout_seconds=10,
        )
    assert provider.last_search_receipt["status"] == "NON_RETRYABLE_FAILURE"
    assert provider.last_fetch_receipts == []
