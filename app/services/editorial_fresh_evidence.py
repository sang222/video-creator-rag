"""Fail-closed fresh-evidence collection for editorial replenishment.

The M5 ``SearchDemandEvidence`` row is the existing durable evidence surface
for editorial research.  This module deliberately extends that surface rather
than creating a second, project-scoped artifact workflow: a project must not
exist before an idea survives research and strict preflight.

Runtime collection is disabled unless a provider has an explicit, hash-bound
editorial-evidence authority in the existing provider registry.  The worker
activates that authority against the existing canonical provider before it can
make a network request; otherwise it records a no-network blocker instead of
trying arbitrary web endpoints or inheriting manual evidence.
"""

from __future__ import annotations

import ipaddress
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.m5 import SearchDemandEvidenceCreate
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models.m5 import SearchDemandEvidence
from app.db.models.m10_1 import LLMRouterLane, LLMRouterProfile
from app.db.models.channel import CompiledChannelPolicySnapshot
from app.db.models.ops import (
    BudgetPolicy,
    CredentialReference,
    ProviderAttempt,
    ProviderRegistryEntry,
)
from app.providers.openai import OpenAIResponsesProvider, OpenAIWebSearchRequest
from app.services.config_registry import content_hash
from app.services.m5 import SearchDemandEvidenceService
from app.services.ops import ProviderHealthService, ProviderRegistryService
from app.services.editorial_research_territory import FirstPartySourceFamilyRegistry


EDITORIAL_EVIDENCE_PROVIDER_CAPABILITY = "editorial_evidence_collection"
EDITORIAL_EVIDENCE_AUTHORITY_KEY = "editorial_evidence_authority"
EDITORIAL_EVIDENCE_SCHEMA = "vcos.editorial-fresh-evidence.v1"
EDITORIAL_EVIDENCE_PROVIDER_KEY = "openai"
EDITORIAL_EVIDENCE_PROVIDER_CONFIG_VERSION = "openai-web-search-https-fetch.v5"
MAX_SOURCE_SNAPSHOT_CHARS = 4_000
_ALLOWED_CONTENT_TYPES = {"text/html", "text/plain"}
_PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".localhost")


@dataclass(frozen=True, slots=True)
class FreshEvidenceAuthority:
    """A validated source-provider authority, never inferred from an LLM."""

    state: str
    provider_key: str | None
    reason_codes: tuple[str, ...]
    policy: dict[str, Any] | None = None
    config_hash: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == "EXISTING_SOURCE_PROVIDER_READY"


def scope_authority_to_research_territory(
    *,
    authority: FreshEvidenceAuthority,
    research_territory: dict[str, Any],
) -> FreshEvidenceAuthority:
    """Narrow a channel-approved source envelope to one planned territory.

    The provider registry establishes the channel's complete first-party
    universe.  A scheduled attempt may use only the source families selected
    by its deterministic territory planner; it can never expand that envelope
    from web-search output.
    """

    policy = dict(authority.policy or {})
    allowed = policy.get("source_families")
    selected = research_territory.get("allowed_source_families")
    if not authority.ready or not isinstance(allowed, list) or not isinstance(selected, list):
        raise ValidationFailureError("EDITORIAL_RESEARCH_SOURCE_FAMILY_AUTHORITY_INVALID")
    allowed_by_id = {
        str(item.get("family_id")): item
        for item in allowed
        if isinstance(item, dict) and item.get("first_party") is True
    }
    selected_ids = [
        str(item.get("family_id"))
        for item in selected
        if isinstance(item, dict) and item.get("first_party") is True
    ]
    if not selected_ids or any(item not in allowed_by_id for item in selected_ids):
        raise ValidationFailureError("EDITORIAL_RESEARCH_SOURCE_FAMILY_AUTHORITY_INVALID")
    scoped_families = [allowed_by_id[item] for item in dict.fromkeys(selected_ids)]
    scoped_policy = {
        **policy,
        "source_families": scoped_families,
        "allowed_domains": sorted(
            {
                str(domain)
                for family in scoped_families
                for domain in family.get("approved_domains") or []
            }
        ),
        "research_territory_hash": research_territory.get("territory_hash"),
    }
    scoped_policy["config_hash"] = content_hash(
        {key: value for key, value in scoped_policy.items() if key != "config_hash"}
    )
    return FreshEvidenceAuthority(
        state=authority.state,
        provider_key=authority.provider_key,
        reason_codes=authority.reason_codes,
        policy=scoped_policy,
        config_hash=scoped_policy["config_hash"],
    )


@dataclass(frozen=True, slots=True)
class FreshEvidenceSource:
    """Normalized output from a source provider before persistence."""

    source_ref: str
    title: str
    publisher: str
    source_class: str
    retrieved_content: str
    retrieved_at: datetime
    query: str
    platform: str = "GOOGLE"
    geo: str = "US"
    language: str = "en-US"
    published_or_updated_at: datetime | None = None
    search_volume_30d: int | None = None
    relative_interest_index: str | None = None
    competition_index: str | None = None
    rights_usage_note: str = "Source retained only as a bounded research extract."
    source_family: str | None = None
    organization: str | None = None
    relevance_to_territory: str | None = None
    search_receipt: dict[str, Any] = field(default_factory=dict)
    fetch_receipt: dict[str, Any] = field(default_factory=dict)


class FreshEvidenceProvider(Protocol):
    """The only boundary allowed to make a source/network request."""

    provider_key: str

    def collect(
        self,
        *,
        research_question: str,
        maximum_sources: int,
        timeout_seconds: int,
    ) -> list[FreshEvidenceSource]: ...


class FreshEvidenceProviderError(RuntimeError):
    """A redaction-safe provider failure suitable for durable receipts."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class EditorialEvidenceActivationResult:
    authority: FreshEvidenceAuthority
    changed: bool


class EditorialEvidenceProviderActivationService:
    """Idempotently extend the existing OpenAI registry entry for evidence.

    The source authority intentionally reuses the canonical Responses provider
    and its existing environment-backed credential reference.  It never
    creates a second registry entry, stores a secret, or enables a fallback.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def activate(
        self,
        *,
        policy_snapshot_id: str,
        policy_snapshot_hash: str,
        company_id: str,
    ) -> EditorialEvidenceActivationResult:
        entry = ProviderRegistryService(self.session).get_entry(
            EDITORIAL_EVIDENCE_PROVIDER_KEY
        )
        if entry is None:
            return EditorialEvidenceActivationResult(
                authority=FreshEvidenceAuthority(
                    state="SOURCE_PROVIDER_MISSING",
                    provider_key=None,
                    reason_codes=("SOURCE_PROVIDER_MISSING",),
                ),
                changed=False,
            )
        settings = get_settings()
        credential = self.session.scalar(
            select(CredentialReference)
            .where(CredentialReference.provider_key == entry.provider_key)
            .where(CredentialReference.credential_key == "openai_api_key")
        )
        credential_present = bool(
            settings.openai_api_key and settings.openai_api_key.get_secret_value()
        )
        if (
            entry.status != "ACTIVE"
            or entry.provider_type != "LLM"
            or credential is None
            or credential.status != "CONFIGURED"
            or credential.secret_ref != "env:OPENAI_API_KEY"
            or not credential_present
        ):
            return EditorialEvidenceActivationResult(
                authority=FreshEvidenceAuthority(
                    state="SOURCE_PROVIDER_CONFIGURED_BUT_NOT_READY",
                    provider_key=entry.provider_key,
                    reason_codes=("SOURCE_PROVIDER_CREDENTIAL_MISSING",),
                ),
                changed=False,
            )
        lane = self.session.scalar(
            select(LLMRouterLane)
            .join(LLMRouterProfile)
            .where(LLMRouterProfile.profile_key == "default")
            .where(LLMRouterLane.lane_name == "default_multimodal")
        )
        if (
            lane is None
            or lane.primary_model != "gpt-5.6-luna"
            or lane.real_execution_enabled is not True
            or list(lane.fallback_models or [])
        ):
            return EditorialEvidenceActivationResult(
                authority=FreshEvidenceAuthority(
                    state="SOURCE_PROVIDER_CONFIGURED_BUT_NOT_READY",
                    provider_key=entry.provider_key,
                    reason_codes=("SOURCE_PROVIDER_LANE_AUTHORITY_UNAVAILABLE",),
                ),
                changed=False,
            )
        budget = self.session.scalar(
            select(BudgetPolicy)
            .where(BudgetPolicy.policy_key == f"openai-standard-monthly-{company_id}")
            .where(BudgetPolicy.status == "ACTIVE")
        )
        per_lane_cap = (
            (budget.policy_blob or {}).get("per_lane_cap_usd") if budget else None
        )
        if per_lane_cap is None:
            return EditorialEvidenceActivationResult(
                authority=FreshEvidenceAuthority(
                    state="SOURCE_PROVIDER_CONFIGURED_BUT_NOT_READY",
                    provider_key=entry.provider_key,
                    reason_codes=("SOURCE_PROVIDER_COST_AUTHORITY_UNAVAILABLE",),
                ),
                changed=False,
            )
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, uuid.UUID(str(policy_snapshot_id))
        )
        payload = snapshot.compiled_payload if snapshot is not None and isinstance(snapshot.compiled_payload, dict) else {}
        contract = payload.get("channel_contract_json") if isinstance(payload.get("channel_contract_json"), dict) else {}
        editorial = contract.get("editorial_strategy") if isinstance(contract.get("editorial_strategy"), dict) else {}
        identity = contract.get("channel_identity") if isinstance(contract.get("channel_identity"), dict) else {}
        channel_terms = [
            *(editorial.get("content_pillars") or []),
            *(editorial.get("allowed_topics") or []),
            *(editorial.get("allowed_angles") or []),
            identity.get("niche"),
            identity.get("brand_promise"),
        ]
        registry = FirstPartySourceFamilyRegistry()
        source_families = registry.families_for(capability_tags=tuple(channel_terms))
        if not source_families:
            return EditorialEvidenceActivationResult(
                authority=FreshEvidenceAuthority(
                    state="SOURCE_PROVIDER_CONFIGURED_BUT_NOT_READY",
                    provider_key=entry.provider_key,
                    reason_codes=("EDITORIAL_SOURCE_FAMILY_AUTHORITY_UNAVAILABLE",),
                ),
                changed=False,
            )
        allowed_domains = sorted(
            {
                domain
                for family in source_families
                for domain in family.approved_domains
            }
        )
        authority_policy = {
            "schema_version": EDITORIAL_EVIDENCE_PROVIDER_CONFIG_VERSION,
            "policy_snapshot_id": str(policy_snapshot_id),
            "policy_snapshot_hash": policy_snapshot_hash,
            "network_access_allowed": True,
            "executor_key": "openai_responses_web_search_https_fetch",
            "search_model": lane.primary_model,
            "search_reasoning_effort": lane.reasoning_effort,
            # The OpenAI provider executes web search; this config-derived
            # envelope defines the independent first-party source universe.
            "allowed_domains": allowed_domains,
            "source_families": [item.receipt() for item in source_families],
            "allowed_source_classes": ["OFFICIAL_DOCUMENT"],
            "maximum_search_calls": 1,
            "maximum_search_results": 8,
            "maximum_sources_per_run": 3,
            # Discovery can rank a temporarily inaccessible first-party page
            # ahead of a reachable official document.  Fetch the bounded
            # discovery set, but still stop as soon as enough snapshots pass.
            "maximum_fetches_per_run": 5,
            "timeout_seconds": min(settings.openai_timeout_seconds, 30),
            # First-party Docs pages currently range to roughly 426 KiB; keep
            # the capture bounded while allowing one complete HTML document.
            "max_response_bytes": 524_288,
            "max_redirects": 3,
            "freshness_days": 30,
            "minimum_sources": 1,
            "maximum_attempts_per_operation": 1,
            "max_cost_usd": str(per_lane_cap),
            "credential_reference": "env:OPENAI_API_KEY",
            "automatic_fallback": False,
        }
        authority_policy["config_hash"] = content_hash(authority_policy)
        capability = dict(entry.capability_blob or {})
        policy_fit = dict(entry.policy_fit_blob or {})
        metadata = dict(entry.metadata_ or {})
        desired_capability = {
            "executor_key": authority_policy["executor_key"],
            "operations": ["search", "fetch"],
            "config_version": EDITORIAL_EVIDENCE_PROVIDER_CONFIG_VERSION,
            "config_hash": authority_policy["config_hash"],
        }
        changed = False
        if capability.get(EDITORIAL_EVIDENCE_PROVIDER_CAPABILITY) != desired_capability:
            capability[EDITORIAL_EVIDENCE_PROVIDER_CAPABILITY] = desired_capability
            entry.capability_blob = capability
            changed = True
        if policy_fit.get(EDITORIAL_EVIDENCE_AUTHORITY_KEY) != authority_policy:
            policy_fit[EDITORIAL_EVIDENCE_AUTHORITY_KEY] = authority_policy
            entry.policy_fit_blob = policy_fit
            changed = True
        evidence_metadata = {
            "credential_env": "OPENAI_API_KEY",
            "credential_stored": False,
            "automatic_fallback": False,
        }
        if metadata.get("editorial_evidence") != evidence_metadata:
            metadata["editorial_evidence"] = evidence_metadata
            entry.metadata_ = metadata
            changed = True
        if changed:
            self.session.flush()
        return EditorialEvidenceActivationResult(
            authority=FreshEvidenceAuthority(
                state="EXISTING_SOURCE_PROVIDER_READY",
                provider_key=entry.provider_key,
                reason_codes=("SOURCE_PROVIDER_AUTHORITY_PASS",),
                policy=authority_policy,
                config_hash=authority_policy["config_hash"],
            ),
            changed=changed,
        )


class _HTMLTextExtractor(HTMLParser):
    """Small deterministic extractor; scripts and styles never become text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self._in_title = False
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        elif lowered == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif lowered == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if not value or self._ignored_depth:
            return
        if self._in_title:
            self.title = " ".join(filter(None, [self.title, value]))
        self.parts.append(value)


class OpenAIWebEvidenceProvider:
    """One bounded OpenAI discovery operation plus safe HTTPS capture.

    Hosted OpenAI web search is discovery only.  Every URL is normalized,
    constrained to the configured official domain authority, fetched through
    the local guarded transport, and persisted only after deterministic
    validation by ``FreshEvidenceCollector``.
    """

    provider_key = EDITORIAL_EVIDENCE_PROVIDER_KEY

    def __init__(
        self,
        *,
        api_key: str | None,
        policy: dict[str, Any],
        now=utc_now,
        search_provider: OpenAIResponsesProvider | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.policy = policy
        self.now = now
        self._search_provider = search_provider or OpenAIResponsesProvider(
            api_key=api_key,
            timeout_seconds=int(policy["timeout_seconds"]),
            runtime_origin="editorial-evidence-executor",
        )
        self._http_client = http_client
        self.last_search_receipt: dict[str, Any] | None = None
        self.last_fetch_receipts: list[dict[str, Any]] = []

    def collect(
        self,
        *,
        research_question: str,
        maximum_sources: int,
        timeout_seconds: int,
    ) -> list[FreshEvidenceSource]:
        if timeout_seconds != int(self.policy["timeout_seconds"]):
            raise FreshEvidenceProviderError(
                "SOURCE_PROVIDER_TIMEOUT_POLICY_MISMATCH", retryable=False
            )
        if maximum_sources > int(self.policy["maximum_sources_per_run"]):
            raise FreshEvidenceProviderError(
                "SOURCE_COLLECTION_BOUND_EXCEEDED", retryable=False
            )
        candidates = self._search(research_question=research_question)
        accepted: list[FreshEvidenceSource] = []
        self.last_fetch_receipts = []
        maximum_fetches = int(
            self.policy.get("maximum_fetches_per_run") or maximum_sources
        )
        for candidate in candidates[:maximum_fetches]:
            try:
                source = self._fetch(candidate=candidate, research_question=research_question)
            except FreshEvidenceProviderError as exc:
                self.last_fetch_receipts.append(
                    {
                        "operation": "fetch",
                        "status": "RETRYABLE_FAILURE" if exc.retryable else "NON_RETRYABLE_FAILURE",
                        "error_code": exc.code,
                        "url": candidate["url"],
                        "canonical_url": candidate["canonical_url"],
                        "result_id": candidate["result_id"],
                    }
                )
                continue
            self.last_fetch_receipts.append(source.fetch_receipt)
            accepted.append(source)
            if len(accepted) >= maximum_sources:
                break
        if not accepted:
            raise FreshEvidenceProviderError("SOURCE_FETCH_INSUFFICIENT", retryable=False)
        return accepted

    def _search(self, *, research_question: str) -> list[dict[str, str]]:
        response = self._search_provider.web_search(
            request=OpenAIWebSearchRequest(
                model=str(self.policy["search_model"]),
                reasoning_effort=str(self.policy["search_reasoning_effort"]),
                query=research_question,
                allowed_domains=list(self.policy["allowed_domains"]),
                search_context_size="low",
            )
        )
        if not response.ok:
            provider_error = response.output.get("error")
            self.last_search_receipt = {
                "operation": "search",
                "status": "RETRYABLE_FAILURE" if response.retryable else "NON_RETRYABLE_FAILURE",
                "error_code": response.error_code or "SOURCE_DISCOVERY_FAILED",
                "query_hash": content_hash({"query": research_question}),
                "provider_error": (
                    dict(provider_error) if isinstance(provider_error, dict) else None
                ),
            }
            raise FreshEvidenceProviderError(
                response.error_code or "SOURCE_DISCOVERY_FAILED",
                retryable=response.retryable,
            )
        raw = response.output.get("raw")
        raw = raw if isinstance(raw, dict) else {}
        candidates = _tool_discovery_candidates(
            response_payload=raw,
            allowed_domains=list(self.policy["allowed_domains"]),
            maximum_results=int(self.policy["maximum_search_results"]),
        )
        search_calls = _web_search_calls(raw)
        self.last_search_receipt = {
            "operation": "search",
            "status": "SUCCESS" if candidates else "NON_RETRYABLE_FAILURE",
            "error_code": None if candidates else "SOURCE_DISCOVERY_NO_URLS",
            "provider_request_id": response.output.get("request_id"),
            "query_hash": content_hash({"query": research_question}),
            "response_hash": content_hash(raw),
            "usage": response.output.get("usage") or {},
            "search_call_ids": search_calls,
            "result_count": len(candidates),
            "result_ids": [item["result_id"] for item in candidates],
        }
        if not candidates:
            raise FreshEvidenceProviderError("SOURCE_DISCOVERY_NO_URLS", retryable=False)
        return candidates

    def _fetch(
        self, *, candidate: dict[str, str], research_question: str
    ) -> FreshEvidenceSource:
        current_url = candidate["canonical_url"]
        redirects: list[str] = []
        client = self._http_client or httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(float(self.policy["timeout_seconds"])),
            headers={"User-Agent": "VCOS-EditorialEvidence/1.0"},
        )
        close_client = self._http_client is None
        try:
            for _ in range(int(self.policy["max_redirects"]) + 1):
                _assert_safe_fetch_url(
                    current_url, allowed_domains=list(self.policy["allowed_domains"])
                )
                try:
                    with client.stream("GET", current_url) as response:
                        status_code = response.status_code
                        if status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise FreshEvidenceProviderError(
                                    "SOURCE_FETCH_REDIRECT_INVALID", retryable=False
                                )
                            next_url = _canonicalize_url(urljoin(current_url, location))
                            _assert_safe_fetch_url(
                                next_url,
                                allowed_domains=list(self.policy["allowed_domains"]),
                            )
                            redirects.append(next_url)
                            current_url = next_url
                            continue
                        if status_code < 200 or status_code >= 300:
                            raise FreshEvidenceProviderError(
                                "SOURCE_FETCH_HTTP_ERROR", retryable=status_code >= 500
                            )
                        content_type = response.headers.get("content-type", "")
                        normalized_type = content_type.split(";", 1)[0].strip().lower()
                        if normalized_type not in _ALLOWED_CONTENT_TYPES:
                            raise FreshEvidenceProviderError(
                                "SOURCE_FETCH_CONTENT_TYPE_FORBIDDEN", retryable=False
                            )
                        declared_size = response.headers.get("content-length")
                        max_bytes = int(self.policy["max_response_bytes"])
                        if declared_size and int(declared_size) > max_bytes:
                            raise FreshEvidenceProviderError(
                                "SOURCE_FETCH_RESPONSE_TOO_LARGE", retryable=False
                            )
                        chunks: list[bytes] = []
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise FreshEvidenceProviderError(
                                    "SOURCE_FETCH_RESPONSE_TOO_LARGE", retryable=False
                                )
                            chunks.append(chunk)
                        raw_bytes = b"".join(chunks)
                        text = raw_bytes.decode(response.encoding or "utf-8", errors="replace")
                        extracted_text, extracted_title = _extract_source_text(
                            text=text, content_type=normalized_type
                        )
                        if len(extracted_text) < 120:
                            raise FreshEvidenceProviderError(
                                "SOURCE_FETCH_CONTENT_EMPTY", retryable=False
                            )
                        retrieved_at = self.now().astimezone(timezone.utc)
                        fetch_receipt = {
                            "operation": "fetch",
                            "status": "SUCCESS",
                            "error_code": None,
                            "result_id": candidate["result_id"],
                            "url": candidate["url"],
                            "canonical_url": current_url,
                            "redirect_chain": redirects,
                            "http_status": status_code,
                            "content_type": normalized_type,
                            "bytes_fetched": total,
                            "retrieved_at": retrieved_at.isoformat(),
                            "etag": response.headers.get("etag"),
                            "last_modified": response.headers.get("last-modified"),
                            "raw_response_hash": content_hash({"bytes": raw_bytes.hex()}),
                            "extractor_version": "vcos.html-text.v1",
                        }
                        family = _configured_source_family(
                            hostname=urlparse(current_url).hostname,
                            policy=self.policy,
                        )
                        if self.policy.get("source_families") and family is None:
                            raise FreshEvidenceProviderError(
                                "SOURCE_FAMILY_UNAPPROVED", retryable=False
                            )
                        return FreshEvidenceSource(
                            source_ref=current_url,
                            title=extracted_title or candidate["title"] or current_url,
                            publisher=urlparse(current_url).hostname or "",
                            source_class="OFFICIAL_DOCUMENT",
                            retrieved_content=extracted_text,
                            retrieved_at=retrieved_at,
                            query=research_question,
                            source_family=(
                                str(family.get("family_id")) if family else None
                            ),
                            organization=(
                                str(family.get("organization")) if family else None
                            ),
                            relevance_to_territory=(
                                "FIRST_PARTY_SOURCE_FAMILY_MATCHED_TERRITORY_QUERY"
                                if family
                                else None
                            ),
                            search_receipt=dict(self.last_search_receipt or {}),
                            fetch_receipt=fetch_receipt,
                        )
                except httpx.TimeoutException as exc:
                    raise FreshEvidenceProviderError(
                        "SOURCE_FETCH_TIMEOUT", retryable=True
                    ) from exc
                except httpx.HTTPError as exc:
                    raise FreshEvidenceProviderError(
                        "SOURCE_FETCH_UNREACHABLE", retryable=True
                    ) from exc
            raise FreshEvidenceProviderError("SOURCE_FETCH_REDIRECT_LIMIT", retryable=False)
        finally:
            if close_client:
                client.close()


@dataclass(frozen=True, slots=True)
class FreshEvidenceCollectionResult:
    authority: FreshEvidenceAuthority
    evidence_ids: tuple[str, ...] = ()
    evidence_refs: tuple[dict[str, Any], ...] = ()
    receipt: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.authority.ready and bool(self.evidence_ids)


class FreshEvidenceCollector:
    """Validate a provider and persist bounded source snapshots in M5.

    A provider registry entry is not enough by itself.  It must explicitly
    declare an editorial-evidence capability and a policy blob bound to the
    exact compiled channel snapshot.  This avoids treating a generic LLM,
    media provider, or a stale provider configuration as research authority.
    """

    def __init__(self, session: Session, *, now=utc_now) -> None:
        self.session = session
        self.now = now

    def inspect_authority(
        self,
        *,
        policy_snapshot_id: str,
        policy_snapshot_hash: str,
    ) -> FreshEvidenceAuthority:
        candidates = list(
            self.session.scalars(
                select(ProviderRegistryEntry)
                .where(ProviderRegistryEntry.status == "ACTIVE")
                .order_by(ProviderRegistryEntry.provider_key)
            ).all()
        )
        configured = [
            item
            for item in candidates
            if bool((item.capability_blob or {}).get(EDITORIAL_EVIDENCE_PROVIDER_CAPABILITY))
        ]
        if not configured:
            return FreshEvidenceAuthority(
                state="SOURCE_PROVIDER_MISSING",
                provider_key=None,
                reason_codes=("SOURCE_PROVIDER_MISSING",),
            )

        for provider in configured:
            policy = (provider.policy_fit_blob or {}).get(
                EDITORIAL_EVIDENCE_AUTHORITY_KEY
            )
            if not isinstance(policy, dict):
                continue
            if (
                policy.get("policy_snapshot_id") != str(policy_snapshot_id)
                or policy.get("policy_snapshot_hash") != policy_snapshot_hash
                or policy.get("network_access_allowed") is not True
            ):
                continue
            if not _valid_policy(policy):
                return FreshEvidenceAuthority(
                    state="SOURCE_PROVIDER_CONFIGURED_BUT_NOT_READY",
                    provider_key=provider.provider_key,
                    reason_codes=("SOURCE_PROVIDER_AUTHORITY_INVALID",),
                )
            return FreshEvidenceAuthority(
                state="EXISTING_SOURCE_PROVIDER_READY",
                provider_key=provider.provider_key,
                reason_codes=("SOURCE_PROVIDER_AUTHORITY_PASS",),
                policy=policy,
                config_hash=str(policy.get("config_hash") or "") or None,
            )

        return FreshEvidenceAuthority(
            state="SOURCE_PROVIDER_CONFIGURED_BUT_NOT_READY",
            provider_key=configured[0].provider_key,
            reason_codes=("SOURCE_PROVIDER_AUTHORITY_UNAVAILABLE",),
        )

    def collect(
        self,
        *,
        authority: FreshEvidenceAuthority,
        provider: FreshEvidenceProvider | None,
        company_id: str,
        channel_workspace_id: str,
        editorial_research_run_id: str,
        context_pack_snapshot_id: str,
        research_question: str,
        research_territory: dict[str, Any] | None = None,
    ) -> FreshEvidenceCollectionResult:
        """Collect only through an already authorized provider.

        The runtime calls this method only after ``inspect_authority`` passes.
        Tests inject a deterministic provider; the production worker does not
        have an adapter until an operator registers one, which remains a hard
        no-network block rather than a fallback.
        """

        if not authority.ready:
            return FreshEvidenceCollectionResult(authority=authority)
        if provider is None or provider.provider_key != authority.provider_key:
            return FreshEvidenceCollectionResult(
                authority=FreshEvidenceAuthority(
                    state="SOURCE_PROVIDER_CONFIGURED_BUT_NOT_READY",
                    provider_key=authority.provider_key,
                    reason_codes=("SOURCE_PROVIDER_ADAPTER_UNAVAILABLE",),
                )
            )
        policy = authority.policy or {}
        maximum_sources = int(policy["maximum_sources_per_run"])
        timeout_seconds = int(policy["timeout_seconds"])
        try:
            source_rows = provider.collect(
                research_question=research_question,
                maximum_sources=maximum_sources,
                timeout_seconds=timeout_seconds,
            )
        except FreshEvidenceProviderError as exc:
            self._record_provider_attempts(
                provider=provider,
                editorial_research_run_id=editorial_research_run_id,
                context_pack_snapshot_id=context_pack_snapshot_id,
            )
            return FreshEvidenceCollectionResult(
                authority=FreshEvidenceAuthority(
                    state="SOURCE_PROVIDER_EXTERNAL_BLOCKED",
                    provider_key=authority.provider_key,
                    reason_codes=(exc.code,),
                    config_hash=authority.config_hash,
                ),
                receipt=self._failure_receipt(
                    provider=provider,
                    authority=authority,
                    editorial_research_run_id=editorial_research_run_id,
                    context_pack_snapshot_id=context_pack_snapshot_id,
                    reason_code=exc.code,
                ),
            )
        self._record_provider_attempts(
            provider=provider,
            editorial_research_run_id=editorial_research_run_id,
            context_pack_snapshot_id=context_pack_snapshot_id,
        )
        if len(source_rows) > maximum_sources:
            return FreshEvidenceCollectionResult(
                authority=FreshEvidenceAuthority(
                    state="SOURCE_PROVIDER_EXTERNAL_BLOCKED",
                    provider_key=authority.provider_key,
                    reason_codes=("SOURCE_COLLECTION_BOUND_EXCEEDED",),
                )
            )

        accepted: list[SearchDemandEvidence] = []
        rejected: list[str] = []
        for source in source_rows:
            reason = _source_validation_reason(
                source=source,
                policy=policy,
                now=self.now(),
            )
            if reason is not None:
                rejected.append(reason)
                continue
            source_body = {
                "source_ref": source.source_ref,
                "title": source.title,
                "publisher": source.publisher,
                "source_class": source.source_class,
                "retrieved_content": source.retrieved_content,
                "retrieved_at": source.retrieved_at.isoformat(),
            }
            if source.source_family:
                source_body["source_family"] = source.source_family
                source_body["organization"] = source.organization
            source_hash = content_hash(source_body)
            existing = self._existing_source(
                editorial_research_run_id=editorial_research_run_id,
                source_hash=source_hash,
            )
            if existing is not None:
                accepted.append(existing)
                continue
            metadata = {
                "editorial_fresh_evidence": {
                    "schema_version": EDITORIAL_EVIDENCE_SCHEMA,
                    "provider_key": authority.provider_key,
                    "editorial_research_run_id": editorial_research_run_id,
                    "context_pack_snapshot_id": context_pack_snapshot_id,
                    "research_question": research_question,
                    "research_territory": dict(research_territory or {}),
                    "search_receipt": source.search_receipt,
                    "fetch_receipt": source.fetch_receipt,
                    "source_snapshot": {
                        "source_ref": source.source_ref,
                        "canonical_url": source.source_ref,
                        "domain": urlparse(source.source_ref).hostname,
                        "title": source.title,
                        "publisher": source.publisher,
                        "source_class": source.source_class,
                        "source_family": source.source_family,
                        "organization": source.organization,
                        "first_party_validated": bool(source.source_family),
                        "relevance_to_territory": source.relevance_to_territory,
                        "research_territory_hash": (research_territory or {}).get(
                            "territory_hash"
                        ),
                        "retrieved_at": source.retrieved_at.isoformat(),
                        "published_or_updated_at": (
                            source.published_or_updated_at.isoformat()
                            if source.published_or_updated_at is not None
                            else None
                        ),
                        "language": source.language,
                        "content_excerpt": source.retrieved_content[
                            :MAX_SOURCE_SNAPSHOT_CHARS
                        ],
                        "content_hash": source_hash,
                        "raw_response_hash": source.fetch_receipt.get(
                            "raw_response_hash"
                        ),
                        "extractor_version": source.fetch_receipt.get(
                            "extractor_version"
                        ),
                        "freshness_state": "FRESH",
                        "quality_decision": "PASS",
                        "rights_usage_note": source.rights_usage_note,
                    },
                }
            }
            evidence = SearchDemandEvidenceService(self.session).create_evidence(
                data=SearchDemandEvidenceCreate(
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                    evidence_source_type="OFFICIAL_DOCUMENT",
                    authority_purpose="CLAIM_SOURCE",
                    source_ref=source.source_ref,
                    query=source.query,
                    platform=source.platform,
                    geo=source.geo,
                    language=source.language,
                    search_volume_30d=source.search_volume_30d,
                    relative_interest_index=source.relative_interest_index,
                    competition_index=source.competition_index,
                    evidence_confidence="HIGH",
                    captured_at=source.retrieved_at,
                    metadata=metadata,
                ),
                correlation_id=f"editorial-fresh-evidence:{editorial_research_run_id}",
            )
            accepted.append(evidence)

        if len(accepted) < int(policy.get("minimum_sources") or 1):
            reason_codes = tuple(sorted(set(rejected))) or (
                "FRESH_EVIDENCE_INSUFFICIENT",
            )
            return FreshEvidenceCollectionResult(
                authority=FreshEvidenceAuthority(
                    state="SOURCE_PROVIDER_EXTERNAL_BLOCKED",
                    provider_key=authority.provider_key,
                    reason_codes=reason_codes,
                )
            )
        refs = tuple(_evidence_ref(item) for item in accepted)
        source_pack = {
            "schema_version": "vcos.editorial-source-pack.v1",
            "editorial_research_run_id": editorial_research_run_id,
            "context_pack_snapshot_id": context_pack_snapshot_id,
            "provider_key": authority.provider_key,
            "provider_config_hash": authority.config_hash,
            "sources": list(refs),
            "research_territory": dict(research_territory or {}),
        }
        source_pack_hash = content_hash(source_pack)
        receipt = {
            "schema_version": EDITORIAL_EVIDENCE_SCHEMA,
            "provider_key": authority.provider_key,
            "provider_config_hash": authority.config_hash,
            "network_call_made": True,
            "source_count": len(accepted),
            "source_pack": {**source_pack, "content_hash": source_pack_hash},
            "research_pack": {
                "schema_version": "vcos.editorial-research-pack.v1",
                "editorial_research_run_id": editorial_research_run_id,
                "context_pack_snapshot_id": context_pack_snapshot_id,
                "research_question": research_question,
                "research_territory": dict(research_territory or {}),
                "source_pack_hash": source_pack_hash,
                "evidence_refs": list(refs),
            },
        }
        receipt["research_pack"]["content_hash"] = content_hash(
            receipt["research_pack"]
        )
        receipt["receipt_hash"] = content_hash(receipt)
        return FreshEvidenceCollectionResult(
            authority=authority,
            evidence_ids=tuple(str(item.id) for item in accepted),
            evidence_refs=refs,
            receipt=receipt,
        )

    def _failure_receipt(
        self,
        *,
        provider: FreshEvidenceProvider,
        authority: FreshEvidenceAuthority,
        editorial_research_run_id: str,
        context_pack_snapshot_id: str,
        reason_code: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": EDITORIAL_EVIDENCE_SCHEMA,
            "provider_key": authority.provider_key,
            "provider_config_hash": authority.config_hash,
            "editorial_research_run_id": editorial_research_run_id,
            "context_pack_snapshot_id": context_pack_snapshot_id,
            "network_call_made": bool(
                getattr(provider, "last_search_receipt", None)
                or getattr(provider, "last_fetch_receipts", None)
            ),
            "reason_codes": [reason_code],
            "search_receipt": getattr(provider, "last_search_receipt", None),
            "fetch_receipts": list(getattr(provider, "last_fetch_receipts", []) or []),
        }

    def _record_provider_attempts(
        self,
        *,
        provider: FreshEvidenceProvider,
        editorial_research_run_id: str,
        context_pack_snapshot_id: str,
    ) -> None:
        """Persist one redacted bounded attempt per actual search/fetch call."""

        try:
            run_id = uuid.UUID(str(editorial_research_run_id))
        except ValueError:
            return
        receipts = [
            item
            for item in [getattr(provider, "last_search_receipt", None)]
            if isinstance(item, dict)
        ] + [
            item
            for item in (getattr(provider, "last_fetch_receipts", []) or [])
            if isinstance(item, dict)
        ]
        if not receipts:
            return
        existing_keys = {
            (
                item.operation_key,
                (item.metadata_ or {}).get("receipt_hash"),
            )
            for item in self.session.scalars(
                select(ProviderAttempt).where(
                    ProviderAttempt.provider_key == provider.provider_key,
                    ProviderAttempt.target_type == "editorial_research_run",
                    ProviderAttempt.target_id == run_id,
                )
            ).all()
        }
        successful = False
        for receipt in receipts:
            operation = str(receipt.get("operation") or "")
            if operation not in {"search", "fetch"}:
                continue
            receipt_hash = content_hash(receipt)
            operation_key = f"editorial_evidence.{operation}"
            if (operation_key, receipt_hash) in existing_keys:
                continue
            status = str(receipt.get("status") or "NON_RETRYABLE_FAILURE")
            if status not in {"SUCCESS", "RETRYABLE_FAILURE", "NON_RETRYABLE_FAILURE"}:
                status = "NON_RETRYABLE_FAILURE"
            provider_error = (
                receipt.get("provider_error")
                if isinstance(receipt.get("provider_error"), dict)
                else {}
            )
            attempt = ProviderAttempt(
                provider_key=provider.provider_key,
                operation_key=operation_key,
                target_type="editorial_research_run",
                target_id=run_id,
                attempt_number=1,
                status=status,
                error_code=receipt.get("error_code"),
                error_message_redacted=(
                    str(provider_error.get("openai_error_message"))[:512]
                    if provider_error.get("openai_error_message")
                    else "redacted editorial evidence provider error"
                    if receipt.get("error_code")
                    else None
                ),
                started_at=utc_now(),
                finished_at=utc_now(),
                latency_ms=None,
                metadata_={
                    "receipt_hash": receipt_hash,
                    "context_pack_snapshot_id": context_pack_snapshot_id,
                    "provider_result_id": receipt.get("result_id"),
                    "provider_request_id": receipt.get("provider_request_id"),
                    "query_hash": receipt.get("query_hash"),
                    "canonical_url": receipt.get("canonical_url"),
                    "usage": receipt.get("usage") or {},
                    "provider_error": provider_error or None,
                    "actual_cost_usd": None,
                    "cost_state": "PROVIDER_REPORTED_COST_UNAVAILABLE",
                },
            )
            self.session.add(attempt)
            successful = successful or status == "SUCCESS"
        self.session.flush()
        if successful:
            ProviderHealthService(self.session).record_observation(
                provider_key=provider.provider_key,
                health_state="HEALTHY",
                reason_codes=["EDITORIAL_EVIDENCE_OPERATION_PASS"],
                metadata={
                    "target_type": "editorial_research_run",
                    "target_id": editorial_research_run_id,
                    "provider_call": True,
                },
            )

    def _existing_source(
        self, *, editorial_research_run_id: str, source_hash: str
    ) -> SearchDemandEvidence | None:
        candidates = self.session.scalars(
            select(SearchDemandEvidence).order_by(SearchDemandEvidence.created_at.desc())
        ).all()
        for evidence in candidates:
            snapshot = (
                ((evidence.metadata_ or {}).get("editorial_fresh_evidence") or {})
                .get("source_snapshot")
                or {}
            )
            lineage = (evidence.metadata_ or {}).get("editorial_fresh_evidence") or {}
            if (
                lineage.get("editorial_research_run_id") == editorial_research_run_id
                and snapshot.get("content_hash") == source_hash
            ):
                return evidence
        return None


def _valid_policy(policy: dict[str, Any]) -> bool:
    allowed_classes = policy.get("allowed_source_classes")
    allowed_domains = policy.get("allowed_domains")
    try:
        return (
            isinstance(allowed_classes, list)
            and bool(allowed_classes)
            and isinstance(allowed_domains, list)
            and bool(allowed_domains)
            and (
                policy.get("source_families") is None
                or (
                    isinstance(policy.get("source_families"), list)
                    and bool(policy.get("source_families"))
                    and all(
                        isinstance(item, dict)
                        and item.get("first_party") is True
                        and isinstance(item.get("approved_domains"), list)
                        and bool(item.get("approved_domains"))
                        for item in policy.get("source_families") or []
                    )
                )
            )
            and 1 <= int(policy.get("maximum_sources_per_run")) <= 5
            and (
                policy.get("maximum_search_calls") is None
                or 1 <= int(policy.get("maximum_search_calls")) <= 1
            )
            and (
                policy.get("maximum_search_results") is None
                or 1 <= int(policy.get("maximum_search_results")) <= 10
            )
            and (
                policy.get("maximum_fetches_per_run") is None
                or 1 <= int(policy.get("maximum_fetches_per_run")) <= 5
            )
            and 1 <= int(policy.get("timeout_seconds")) <= 60
            and (
                policy.get("max_response_bytes") is None
                or 16_384 <= int(policy.get("max_response_bytes")) <= 1_048_576
            )
            and (
                policy.get("max_redirects") is None
                or 0 <= int(policy.get("max_redirects")) <= 5
            )
            and 1 <= int(policy.get("freshness_days")) <= 365
            and (
                policy.get("minimum_sources") is None
                or 1 <= int(policy.get("minimum_sources"))
            )
            and (
                policy.get("automatic_fallback") is None
                or policy.get("automatic_fallback") is False
            )
            and (
                policy.get("config_hash") is None
                or isinstance(policy.get("config_hash"), str)
            )
        )
    except (TypeError, ValueError):
        return False


def _source_validation_reason(
    *, source: FreshEvidenceSource, policy: dict[str, Any], now: datetime
) -> str | None:
    parsed = urlparse(source.source_ref)
    if parsed.scheme != "https" or not parsed.hostname or not source.retrieved_content.strip():
        return "SOURCE_SNAPSHOT_INVALID"
    if source.source_class not in set(policy["allowed_source_classes"]):
        return "SOURCE_QUALITY_INSUFFICIENT"
    if not _hostname_allowed(parsed.hostname, list(policy["allowed_domains"])):
        return "SOURCE_QUALITY_INSUFFICIENT"
    if policy.get("source_families"):
        family = _configured_source_family(hostname=parsed.hostname, policy=policy)
        if (
            family is None
            or source.source_family != family.get("family_id")
            or source.organization != family.get("organization")
        ):
            return "SOURCE_FAMILY_UNAPPROVED"
    retrieved_at = source.retrieved_at.astimezone(timezone.utc)
    cutoff = now.astimezone(timezone.utc) - timedelta(
        days=int(policy["freshness_days"])
    )
    if retrieved_at < cutoff:
        return "SOURCE_FRESHNESS_FAILED"
    return None


def _evidence_ref(evidence: SearchDemandEvidence) -> dict[str, Any]:
    snapshot = (
        ((evidence.metadata_ or {}).get("editorial_fresh_evidence") or {}).get(
            "source_snapshot"
        )
        or {}
    )
    return {
        "type": "search_demand_evidence",
        "id": str(evidence.id),
        "evidence_source_type": evidence.evidence_source_type,
        "source_ref": evidence.source_ref,
        "query": evidence.query,
        "platform": evidence.platform,
        "geo": evidence.geo,
        "confidence": evidence.evidence_confidence,
        "captured_at": evidence.captured_at.isoformat(),
        "source_snapshot_hash": snapshot.get("content_hash"),
        "source_family": snapshot.get("source_family"),
        "organization": snapshot.get("organization"),
    }


def _canonicalize_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.hostname:
        raise FreshEvidenceProviderError("SOURCE_URL_INVALID", retryable=False)
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower().rstrip(".")
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise FreshEvidenceProviderError("SOURCE_URL_INVALID", retryable=False) from exc
    port = parsed.port
    netloc = hostname
    if port is not None and not (scheme == "https" and port == 443):
        netloc = f"{hostname}:{port}"
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"gclid", "fbclid", "mc_cid", "mc_eid"}
    ]
    normalized = urlunparse(
        (scheme, netloc, parsed.path or "/", "", urlencode(query_pairs, doseq=True), "")
    )
    return normalized


def _hostname_allowed(hostname: str | None, allowed_domains: list[str]) -> bool:
    if not hostname:
        return False
    host = hostname.lower().rstrip(".")
    return any(
        host == domain.lower().lstrip(".")
        or host.endswith("." + domain.lower().lstrip("."))
        for domain in allowed_domains
        if isinstance(domain, str) and domain.strip()
    )


def _configured_source_family(
    *, hostname: str | None, policy: dict[str, Any]
) -> dict[str, Any] | None:
    """Return only an explicitly configured first-party family for a host."""

    for family in policy.get("source_families") or []:
        if not isinstance(family, dict) or family.get("first_party") is not True:
            continue
        domains = family.get("approved_domains")
        if isinstance(domains, list) and _hostname_allowed(hostname, domains):
            return family
    return None


def _assert_safe_fetch_url(url: str, *, allowed_domains: list[str]) -> None:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if parsed.scheme.lower() != "https" or not hostname:
        raise FreshEvidenceProviderError("SOURCE_FETCH_SCHEME_FORBIDDEN", retryable=False)
    host = hostname.lower().rstrip(".")
    if (
        host == "localhost"
        or host.endswith(_PRIVATE_HOST_SUFFIXES)
        or not _hostname_allowed(host, allowed_domains)
    ):
        raise FreshEvidenceProviderError("SOURCE_FETCH_SSRF_BLOCKED", retryable=False)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise FreshEvidenceProviderError("SOURCE_FETCH_SSRF_BLOCKED", retryable=False)
        return
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise FreshEvidenceProviderError("SOURCE_FETCH_DNS_UNRESOLVABLE", retryable=True) from exc
    if not addresses:
        raise FreshEvidenceProviderError("SOURCE_FETCH_DNS_UNRESOLVABLE", retryable=True)
    try:
        public = all(ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError as exc:
        raise FreshEvidenceProviderError("SOURCE_FETCH_DNS_INVALID", retryable=False) from exc
    if not public:
        raise FreshEvidenceProviderError("SOURCE_FETCH_SSRF_BLOCKED", retryable=False)


def _extract_source_text(*, text: str, content_type: str) -> tuple[str, str | None]:
    if content_type == "text/plain":
        normalized = " ".join(text.split())
        return normalized, None
    parser = _HTMLTextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise FreshEvidenceProviderError("SOURCE_FETCH_EXTRACTION_FAILED", retryable=False) from exc
    return " ".join(" ".join(parser.parts).split()), parser.title


def _web_search_calls(response_payload: dict[str, Any]) -> list[str]:
    return [
        str(item.get("id"))
        for item in response_payload.get("output") or []
        if isinstance(item, dict)
        and item.get("type") == "web_search_call"
        and item.get("id")
    ]


def _tool_discovery_candidates(
    *,
    response_payload: dict[str, Any],
    allowed_domains: list[str],
    maximum_results: int,
) -> list[dict[str, str]]:
    """Extract only URLs attached to the hosted tool output or citations."""

    discovered: list[tuple[str, str, str]] = []
    for item in response_payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            for source in action.get("sources") or []:
                if isinstance(source, dict) and isinstance(source.get("url"), str):
                    discovered.append(
                        (
                            source["url"],
                            str(source.get("title") or ""),
                            str(item.get("id") or "web-search"),
                        )
                    )
        if item.get("type") == "message":
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                for annotation in content.get("annotations") or []:
                    if (
                        isinstance(annotation, dict)
                        and annotation.get("type") == "url_citation"
                        and isinstance(annotation.get("url"), str)
                    ):
                        discovered.append(
                            (
                                annotation["url"],
                                str(annotation.get("title") or ""),
                                str(item.get("id") or "message"),
                            )
                        )
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_url, title, provider_id in discovered:
        try:
            canonical_url = _canonicalize_url(raw_url)
        except FreshEvidenceProviderError:
            continue
        hostname = urlparse(canonical_url).hostname
        if not _hostname_allowed(hostname, allowed_domains) or canonical_url in seen:
            continue
        seen.add(canonical_url)
        normalized.append(
            {
                "url": raw_url,
                "canonical_url": canonical_url,
                "title": title,
                "result_id": content_hash(
                    {
                        "provider_result_id": provider_id,
                        "canonical_url": canonical_url,
                    }
                ),
            }
        )
        if len(normalized) >= maximum_results:
            break
    return normalized
