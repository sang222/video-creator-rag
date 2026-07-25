from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol, Sequence

import certifi

from app.contracts.asset_acquisition import (
    AssetDownloadReceipt,
    AssetRequest,
    DriveArchiveFileReceipt,
    DriveArchiveReceipt,
    ParsedStockCandidate,
    PexelsDownloadPlan,
    PexelsQueryPlan,
    ProductionArchiveManifest,
)
from app.core.config import Settings
from app.services.local_project_workspace import AssetDownloadStateMachine
from app.services.m10_5 import (
    GoogleDriveConfigService,
    GoogleDriveMediaStorageProvider,
    GoogleDriveOAuthCredentialService,
)
from app.services.native_render_plan import stable_hash
from app.services.pexels_media_downloader import (
    PexelsDownloadExecutionContext,
    PexelsMediaDownloadClient,
)
from app.services.pexels_query_planner import PexelsQueryPlanner
from app.services.provider_asset_manifests import (
    PexelsDownloadPlanBuilder,
    PexelsRateLimitMetadataParser,
    PexelsRenditionSelector,
    PexelsResponseParser,
)
from app.services.stock_candidate_ranker import (
    StockCandidateRanker,
    observable_semantic_relevance_evidence,
)


PA1R_PURPOSE = "PA1R_NON_PRODUCTION_SMOKE"
PA1R_LABEL = "VCOS NON-PRODUCTION PROVIDER SMOKE"
PA1R_HARD_CAP_USD = 3.00
PA1R_VEO_ESTIMATE_USD = 0.80
PA1R_NARRATION = (
    "This is a non-production VCOS provider smoke. It checks a guarded media path from supporting "
    "footage and an abstract generated visual through local assembly and verified archive. Every asset "
    "remains review-only, with provider audio removed and narration kept separate. This technical sample "
    "does not claim production readiness, business results, or publishing approval."
)
PA1R_VEO_PROMPT = (
    "Abstract cinematic visual metaphor for a guarded media workflow: luminous geometric streams pass "
    "through a sequence of transparent safety gates and converge into one calm blue archive beacon, "
    "documentary explainer style, clean composition, subtle camera movement, no people, no faces, no "
    "presenter, no logos, no readable text, no software interface, no product demo, no evidence claims."
)
APPROVED_ELEVENLABS_MODELS = (
    "eleven_multilingual_v2",
    "eleven_flash_v2_5",
    "eleven_turbo_v2_5",
)
PEXELS_CLIENT_HEADERS = {
    "User-Agent": "VCOS-PA1R/1.0",
    "Accept": "application/json",
}
_SAFE_PROVIDER_ERROR_HEADERS = (
    "content-type",
    "retry-after",
    "x-request-id",
    "cf-ray",
    "server",
)


class RedactedProviderHTTPError(RuntimeError):
    """Provider HTTP failure with only allowlisted, secret-free evidence."""

    def __init__(
        self,
        provider: str,
        exc: urllib.error.HTTPError,
        *,
        secret_values: tuple[str, ...] = (),
    ):
        raw_body = exc.read(4096)
        body = raw_body.decode("utf-8", errors="replace").strip()
        for secret in secret_values:
            if secret:
                body = body.replace(secret, "[REDACTED]")
        normalized_headers = {
            str(name).lower(): str(value)
            for name, value in (exc.headers.items() if exc.headers else ())
        }
        response_headers = {
            name: normalized_headers[name]
            for name in _SAFE_PROVIDER_ERROR_HEADERS
            if name in normalized_headers
        }
        self.safe_evidence = {
            "provider": provider,
            "http_status": int(exc.code),
            "reason": str(exc.reason)[:160],
            "response_headers": response_headers,
            "response_body": body[:1000],
            "secret_values_exposed": False,
        }
        super().__init__(f"{provider.upper()}_HTTP_{exc.code}")


class HTTPTransport(Protocol):
    def json_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> tuple[dict[str, Any] | list[Any], dict[str, str]]: ...

    def bytes_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> tuple[bytes, dict[str, str]]: ...


class NoRetryHTTPTransport:
    """One HTTP attempt per method call. Secret-bearing headers are never returned."""

    def __init__(self):
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def json_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> tuple[dict[str, Any] | list[Any], dict[str, str]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        safe_headers = dict(headers)
        if body is not None:
            safe_headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(
            url, method=method, data=body, headers=safe_headers
        )
        with urllib.request.urlopen(
            request, timeout=timeout, context=self.ssl_context
        ) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            return parsed, {
                str(key): str(value) for key, value in response.headers.items()
            }

    def bytes_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> tuple[bytes, dict[str, str]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        safe_headers = dict(headers)
        if body is not None:
            safe_headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(
            url, method=method, data=body, headers=safe_headers
        )
        with urllib.request.urlopen(
            request, timeout=timeout, context=self.ssl_context
        ) as response:
            return response.read(), {
                str(key): str(value) for key, value in response.headers.items()
            }


@dataclass(frozen=True)
class PA1RApprovalScope:
    approval_ref: str = "operator-prompt-pa1r-2026-07-12"
    approved_at: str = "2026-07-12T00:00:00+07:00"
    purpose: str = PA1R_PURPOSE
    max_pexels_search_flows: int = 1
    max_pexels_downloads: int = 1
    max_elevenlabs_generations: int = 1
    max_veo_generations: int = 1
    max_narration_seconds: int = 25
    hard_cap_usd: float = PA1R_HARD_CAP_USD
    youtube_allowed: bool = False
    production_promotion_allowed: bool = False
    automatic_retry_allowed: bool = False

    def evidence(self) -> dict[str, Any]:
        payload = {
            **self.__dict__,
            "production_eligible": False,
            "not_publishable": True,
        }
        return {**payload, "approval_hash": stable_hash(payload)}


@dataclass
class PA1RExecutionGates:
    approval_present: bool = False
    credential_ready: bool = False
    billing_quota_ready: bool = False
    cost_estimate_ready: bool = False
    idempotency_ready: bool = False
    paid_attempt_ready: bool = False
    provider_boundary_ready: bool = False
    monthly_budget_ready: bool = False
    global_kill_switch_open: bool = False
    provider_kill_switch_open: bool = False
    planned_ledger_exists: bool = False

    @property
    def all_passed(self) -> bool:
        return all(self.__dict__.values())

    @property
    def blockers(self) -> list[str]:
        return [
            name.upper() + "_FAILED"
            for name, value in self.__dict__.items()
            if not value
        ]


@dataclass
class PA1RCallLedger:
    path: Path
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "PA1RCallLedger":
        if not path.is_file():
            return cls(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(path=path, entries=dict(payload.get("entries") or {}))

    def plan(
        self,
        key: str,
        *,
        provider: str,
        operation: str,
        paid: bool,
        idempotency_key: str,
    ) -> None:
        if key in self.entries:
            return
        self.entries[key] = {
            "provider": provider,
            "operation": operation,
            "paid": paid,
            "status": "PLANNED",
            "attempt_count": 0,
            "max_attempts": 1,
            "idempotency_key_hash": hashlib.sha256(
                idempotency_key.encode()
            ).hexdigest(),
            "provider_call_made": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        self.persist()

    def begin_once(self, key: str) -> None:
        entry = self.entries[key]
        if entry["attempt_count"] >= entry["max_attempts"]:
            raise RuntimeError("PA1R_PAID_ATTEMPT_LIMIT_EXCEEDED")
        entry.update(status="EXECUTING", attempt_count=entry["attempt_count"] + 1)
        self.persist()

    def finish(
        self,
        key: str,
        *,
        status: str,
        provider_call_made: bool,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.entries[key].update(
            status=status,
            provider_call_made=provider_call_made,
            evidence=evidence or {},
            completed_at=datetime.now(UTC).isoformat(),
        )
        self.persist()

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": self.entries, "ledger_hash": stable_hash(self.entries)}
        _write_json_atomic(self.path, payload)


class GuardedProviderOperation:
    """Fail-closed one-shot boundary shared by real adapters and deterministic tests."""

    def __init__(self, ledger: PA1RCallLedger):
        self.ledger = ledger

    def run(self, key: str, *, gates: PA1RExecutionGates, operation) -> dict[str, Any]:
        entry = self.ledger.entries.get(key)
        if entry is None:
            return {
                "status": "BLOCKED",
                "provider_call_made": False,
                "reason_codes": ["PLANNED_LEDGER_MISSING"],
            }
        if entry.get("status") == "SUCCEEDED":
            return {
                "status": "DUPLICATE_EXISTING_RESULT",
                "provider_call_made": False,
                "existing_evidence": entry.get("evidence") or {},
            }
        if entry.get("attempt_count", 0) >= entry.get("max_attempts", 1):
            return {
                "status": "BLOCKED",
                "provider_call_made": False,
                "reason_codes": ["PAID_ATTEMPT_LIMIT_EXCEEDED"],
            }
        if not gates.all_passed:
            return {
                "status": "BLOCKED",
                "provider_call_made": False,
                "reason_codes": gates.blockers,
            }
        self.ledger.begin_once(key)
        try:
            evidence = operation()
        except Exception as exc:
            failure_evidence = {
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:500],
            }
            safe_provider_evidence = getattr(exc, "safe_evidence", None)
            if isinstance(safe_provider_evidence, dict):
                safe_evidence_kind = getattr(exc, "safe_evidence_kind", None)
                evidence_key = {
                    "PEXELS_SEARCH_RANKING_FAILURE": (
                        "pexels_search_ranking_failure"
                    ),
                    "PEXELS_DOWNLOAD_VIABILITY_FAILURE": (
                        "pexels_download_viability_failure"
                    ),
                }.get(safe_evidence_kind, "provider_http_error")
                failure_evidence[evidence_key] = safe_provider_evidence
            self.ledger.finish(
                key,
                status="FAILED",
                provider_call_made=True,
                evidence=failure_evidence,
            )
            raise
        self.ledger.finish(
            key, status="SUCCEEDED", provider_call_made=True, evidence=evidence
        )
        return {"status": "SUCCEEDED", "provider_call_made": True, "evidence": evidence}


def _persist_pexels_search_ranking_failure(
    *,
    workspace_directory: Path,
    request: AssetRequest,
    plan: PexelsQueryPlan,
    query: str,
    retrieved_candidates: list[ParsedStockCandidate],
    ranking_candidates: list[ParsedStockCandidate],
    ranking: dict[str, Any],
    exclusions: set[str],
    technical_viability_filter: dict[str, Any],
    headers: dict[str, Any],
    reason_code: str,
    semantic_fit_threshold: float,
    selected_semantic_relevance: float | None,
    highest_ranked_candidate_id: str | None,
    highest_ranked_semantic_relevance: float | None,
    semantic_gate_result: str,
) -> dict[str, Any]:
    """Persist a secret-free receipt before returning a consumed search failure."""

    payload = {
        "schema_version": "vcos.pexels-search-ranking-failure.v2",
        "reason_code": reason_code,
        "recorded_at": datetime.now(UTC).isoformat(),
        "request_id": request.request_id,
        "query_plan": plan.model_dump(mode="json"),
        "retrieval_evidence": {
            "query_used": query,
            "provider_result_count": len(retrieved_candidates),
            "provider_result_order": [
                candidate.provider_asset_id for candidate in retrieved_candidates
            ],
            "provider_search_order_score_contribution": 0.0,
        },
        "cross_scene_exclusion": {
            "excluded_provider_asset_count": len(exclusions),
            "excluded_provider_asset_ids": sorted(exclusions),
            "filter_applied_before_ranking": True,
        },
        "technical_viability_filter": technical_viability_filter,
        "ranking": ranking,
        "semantic_scoring_evidence": [
            observable_semantic_relevance_evidence(
                request,
                candidate,
                confirmed_query_retrieval=True,
            )
            for candidate in ranking_candidates
        ],
        "semantic_fit_gate": {
            "threshold": semantic_fit_threshold,
            "selected_candidate_id": ranking.get("selected_candidate_id"),
            "selected_semantic_relevance": selected_semantic_relevance,
            "highest_ranked_candidate_id": highest_ranked_candidate_id,
            "highest_ranked_semantic_relevance": (
                highest_ranked_semantic_relevance
            ),
            "result": semantic_gate_result,
        },
        "rate_limit": PexelsRateLimitMetadataParser().parse(headers),
        "sanitization": {
            "authorization_header_persisted": False,
            "api_key_persisted": False,
            "raw_provider_payload_persisted": False,
            "raw_media_urls_persisted": False,
            "candidate_text_normalized_to_tokens": True,
            "secret_values_exposed": False,
        },
    }
    payload["content_hash"] = stable_hash(payload)
    request_ref = hashlib.sha256(request.request_id.encode()).hexdigest()[:12]
    evidence_path = Path(workspace_directory) / (
        "pexels-search-ranking-failure-"
        f"{request_ref}-{payload['content_hash'][:16]}.json"
    )
    _write_json_atomic(evidence_path, payload)
    return {
        **payload,
        "evidence_path": str(evidence_path),
        "evidence_persisted": True,
    }


def _pexels_download_viability(
    request: AssetRequest,
    candidates: list[ParsedStockCandidate],
) -> tuple[
    list[ParsedStockCandidate],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    """Apply local download viability gates before semantic ranking."""

    selector = PexelsRenditionSelector()
    viable: list[ParsedStockCandidate] = []
    renditions: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        reason_codes: list[str] = []
        if candidate.duration_seconds < request.minimum_duration_seconds:
            reason_codes.append("DURATION_BELOW_REQUEST_MINIMUM")
        try:
            rendition = selector.select(candidate, request)
        except ValueError as exc:
            if str(exc) != "PEXELS_COMPATIBLE_MP4_NOT_FOUND":
                raise
            rendition = None
            reason_codes.append("PEXELS_COMPATIBLE_MP4_NOT_FOUND")
        if reason_codes:
            rejected.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "provider_asset_id": candidate.provider_asset_id,
                    "duration_seconds": candidate.duration_seconds,
                    "minimum_duration_met": (
                        candidate.duration_seconds
                        >= request.minimum_duration_seconds
                    ),
                    "compatible_mp4_rendition_found": rendition is not None,
                    "reason_codes": reason_codes,
                }
            )
            continue
        if rendition is None:  # pragma: no cover - guarded by reason_codes
            raise RuntimeError("PEXELS_RENDITION_VIABILITY_STATE_INVALID")
        viable.append(candidate)
        renditions[candidate.candidate_id] = rendition

    evidence = {
        "minimum_duration_seconds": request.minimum_duration_seconds,
        "input_candidate_count": len(candidates),
        "viable_candidate_count": len(viable),
        "viable_provider_asset_ids": [
            candidate.provider_asset_id for candidate in viable
        ],
        "rejected_candidates": rejected,
        "minimum_duration_filter_applied_before_ranking": True,
        "rendition_compatibility_filter_applied_before_ranking": True,
        "raw_media_urls_persisted": False,
    }
    return viable, renditions, evidence


def _persist_pexels_download_viability_failure(
    *,
    workspace_directory: Path,
    request: AssetRequest,
    plan: PexelsQueryPlan,
    retrieved_candidates: list[ParsedStockCandidate],
    exclusions: set[str],
    technical_viability_filter: dict[str, Any],
) -> dict[str, Any]:
    """Persist an allowlisted receipt with no raw query, response, or media URL."""

    payload = {
        "schema_version": "vcos.pexels-download-viability-failure.v1",
        "reason_code": "PEXELS_NO_DOWNLOAD_VIABLE_CANDIDATES",
        "recorded_at": datetime.now(UTC).isoformat(),
        "request_id": request.request_id,
        "query_plan_hash": plan.plan_hash,
        "retrieval_evidence": {
            "provider_result_count": len(retrieved_candidates),
            "provider_result_order": [
                candidate.provider_asset_id for candidate in retrieved_candidates
            ],
        },
        "cross_scene_exclusion": {
            "excluded_provider_asset_count": len(exclusions),
            "excluded_provider_asset_ids": sorted(exclusions),
            "filter_applied_before_ranking": True,
        },
        "technical_viability_filter": technical_viability_filter,
        "sanitization": {
            "authorization_header_persisted": False,
            "api_key_persisted": False,
            "raw_query_persisted": False,
            "raw_provider_payload_persisted": False,
            "raw_media_urls_persisted": False,
            "secret_values_exposed": False,
        },
    }
    payload["content_hash"] = stable_hash(payload)
    request_ref = hashlib.sha256(request.request_id.encode()).hexdigest()[:12]
    evidence_path = Path(workspace_directory) / (
        "pexels-download-viability-failure-"
        f"{request_ref}-{payload['content_hash'][:16]}.json"
    )
    _write_json_atomic(evidence_path, payload)
    return {
        **payload,
        "evidence_path": str(evidence_path),
        "evidence_persisted": True,
    }


class PexelsPA1RClient:
    base_url = "https://api.pexels.com"

    def __init__(
        self,
        transport: HTTPTransport | None = None,
        media_downloader: PexelsMediaDownloadClient | None = None,
    ):
        self.transport = transport or NoRetryHTTPTransport()
        self.media_downloader = media_downloader or PexelsMediaDownloadClient()
        self.search_flow_count = 0
        self.selected_download_count = 0

    def search_select_once(
        self,
        *,
        api_key: str,
        request: AssetRequest,
        workspace_directory: Path,
        maximum_download_bytes: int = 500 * 1024 * 1024,
        excluded_provider_asset_ids: Sequence[str] = (),
        semantic_fit_threshold: float = 0.25,
    ) -> tuple[dict[str, Any], dict[str, Any], PexelsDownloadExecutionContext]:
        if (
            isinstance(semantic_fit_threshold, bool)
            or not isinstance(semantic_fit_threshold, (int, float))
            or not 0 < float(semantic_fit_threshold) <= 1
        ):
            raise ValueError("PEXELS_SEMANTIC_FIT_THRESHOLD_INVALID")
        if self.search_flow_count:
            raise RuntimeError("PEXELS_SEARCH_FLOW_LIMIT_EXCEEDED")
        plan = PexelsQueryPlanner().plan(request, per_page=20)
        query = plan.queries[0]
        params = urllib.parse.urlencode(
            {
                "query": query,
                "orientation": plan.orientation,
                "size": plan.size_preference,
                "per_page": plan.per_page,
            }
        )
        try:
            payload, headers = self.transport.json_request(
                "GET",
                f"{self.base_url}{plan.endpoint}?{params}",
                headers={"Authorization": api_key, **PEXELS_CLIENT_HEADERS},
            )
        except urllib.error.HTTPError as exc:
            raise RedactedProviderHTTPError(
                "pexels", exc, secret_values=(api_key,)
            ) from None
        self.search_flow_count += 1
        if not isinstance(payload, dict):
            raise RuntimeError("PEXELS_RESPONSE_INVALID")
        parsed = PexelsResponseParser().parse(payload)
        exclusions = {
            str(value).strip()
            for value in excluded_provider_asset_ids
            if str(value).strip()
        }
        deduped = {
            item.provider_asset_id: item
            for item in parsed
            if item.provider_asset_id not in exclusions
        }
        candidates = list(deduped.values())
        if not candidates:
            raise RuntimeError("PEXELS_NO_UNIQUE_ELIGIBLE_CANDIDATES")
        (
            ranking_candidates,
            compatible_renditions,
            technical_viability_filter,
        ) = _pexels_download_viability(request, candidates)
        if not ranking_candidates:
            failure_evidence = _persist_pexels_download_viability_failure(
                workspace_directory=workspace_directory,
                request=request,
                plan=plan,
                retrieved_candidates=candidates,
                exclusions=exclusions,
                technical_viability_filter=technical_viability_filter,
            )
            error = RuntimeError("PEXELS_NO_DOWNLOAD_VIABLE_CANDIDATES")
            error.safe_evidence_kind = "PEXELS_DOWNLOAD_VIABILITY_FAILURE"
            error.safe_evidence = failure_evidence
            raise error
        ranking = StockCandidateRanker().rank(
            request,
            ranking_candidates,
            minimum_semantic_relevance=float(semantic_fit_threshold),
            confirmed_query_retrieval=True,
        )
        selected = next(
            (
                item
                for item in ranking_candidates
                if item.candidate_id == ranking.selected_candidate_id
            ),
            None,
        )
        top = next(
            (
                item
                for item in ranking.candidate_scores
                if selected is not None and item.candidate_id == selected.candidate_id
            ),
            None,
        )
        highest_ranked = (
            ranking.candidate_scores[0] if ranking.candidate_scores else None
        )
        selected_semantic_relevance = (
            float(top.dimensions.get("semantic_relevance", 0))
            if top is not None
            else None
        )
        highest_ranked_semantic_relevance = (
            float(
                highest_ranked.dimensions.get("semantic_relevance", 0)
            )
            if highest_ranked is not None
            else None
        )
        if (
            selected is None
            or selected_semantic_relevance is None
            or selected_semantic_relevance < float(semantic_fit_threshold)
        ):
            failure_evidence = _persist_pexels_search_ranking_failure(
                workspace_directory=workspace_directory,
                request=request,
                plan=plan,
                query=query,
                retrieved_candidates=candidates,
                ranking_candidates=ranking_candidates,
                ranking=ranking.model_dump(mode="json"),
                exclusions=exclusions,
                technical_viability_filter=technical_viability_filter,
                headers=headers,
                reason_code="PEXELS_SEMANTIC_FIT_INADEQUATE",
                semantic_fit_threshold=float(semantic_fit_threshold),
                selected_semantic_relevance=selected_semantic_relevance,
                highest_ranked_candidate_id=(
                    highest_ranked.candidate_id
                    if highest_ranked is not None
                    else None
                ),
                highest_ranked_semantic_relevance=(
                    highest_ranked_semantic_relevance
                ),
                semantic_gate_result="FAIL",
            )
            error = RuntimeError("PEXELS_SEMANTIC_FIT_INADEQUATE")
            error.safe_evidence_kind = "PEXELS_SEARCH_RANKING_FAILURE"
            error.safe_evidence = failure_evidence
            raise error
        rendition = compatible_renditions[selected.candidate_id]
        download_plan = PexelsDownloadPlanBuilder().build(selected, rendition, request)
        safe = {
            "query_plan": plan.model_dump(mode="json"),
            "cross_scene_exclusion": {
                "excluded_provider_asset_count": len(exclusions),
                "excluded_provider_asset_ids": sorted(exclusions),
                "filter_applied_before_ranking": True,
            },
            "technical_viability_filter": technical_viability_filter,
            "ranking": ranking.model_dump(mode="json"),
            "semantic_fit_gate": {
                "threshold": float(semantic_fit_threshold),
                "selected_semantic_relevance": selected_semantic_relevance,
                "result": "PASS",
            },
            "semantic_scoring_evidence": [
                observable_semantic_relevance_evidence(
                    request,
                    item,
                    confirmed_query_retrieval=True,
                )
                for item in ranking_candidates
            ],
            "selected_candidate": selected.model_dump(
                mode="json", exclude={"video_files"}
            ),
            "download_plan": download_plan.model_dump(mode="json"),
            "rate_limit": PexelsRateLimitMetadataParser().parse(headers),
        }
        execution_context = PexelsDownloadExecutionContext.from_selected_api_rendition(
            provider_asset_id=selected.provider_asset_id,
            rendition=rendition,
            workspace_directory=workspace_directory,
            maximum_allowed_bytes=maximum_download_bytes,
        )
        return (
            safe,
            selected.model_dump(mode="python", exclude={"video_files"}),
            execution_context,
        )

    def download_once(
        self,
        *,
        plan: PexelsDownloadPlan,
        execution_context: PexelsDownloadExecutionContext,
        request_id: str,
    ) -> AssetDownloadReceipt:
        if self.selected_download_count:
            raise RuntimeError("PEXELS_DOWNLOAD_LIMIT_EXCEEDED")
        result = self.media_downloader.download(plan=plan, context=execution_context)
        self.selected_download_count += 1
        destination = Path(result["path"])
        AssetDownloadStateMachine().transition(
            "ASSET_DOWNLOADING",
            "ASSET_DOWNLOADED",
            file_path=destination,
            sha256=result["sha256"],
        )
        payload = {
            "request_id": request_id,
            "state": "ASSET_DOWNLOADED",
            "states": [
                "PLANNED",
                "ASSET_SEARCHING",
                "ASSET_SELECTED",
                "ASSET_DOWNLOADING",
                "ASSET_DOWNLOADED",
            ],
            "transport": "PEXELS_API",
            "provider_call_made": True,
            "production_eligible": False,
            "local_path": str(destination),
            "size_bytes": result["size_bytes"],
            "sha256": result["sha256"],
            "http_evidence": result["http_evidence"],
            "media_probe": result["media_probe"],
            "completed_at": datetime.now(UTC),
        }
        return AssetDownloadReceipt(**payload, receipt_hash=stable_hash(payload))


class ElevenLabsPA1RClient:
    base_url = "https://api.elevenlabs.io/v1"

    def __init__(self, transport: HTTPTransport | None = None):
        self.transport = transport or NoRetryHTTPTransport()
        self.generation_count = 0

    def readiness(self, *, api_key: str, required_characters: int) -> dict[str, Any]:
        headers = {"xi-api-key": api_key}
        subscription, _ = self.transport.json_request(
            "GET", f"{self.base_url}/user/subscription", headers=headers
        )
        voices, _ = self.transport.json_request(
            "GET", f"{self.base_url}/voices", headers=headers
        )
        models, _ = self.transport.json_request(
            "GET", f"{self.base_url}/models", headers=headers
        )
        if (
            not isinstance(subscription, dict)
            or not isinstance(voices, dict)
            or not isinstance(models, list)
        ):
            raise RuntimeError("ELEVENLABS_READINESS_RESPONSE_INVALID")
        used = int(subscription.get("character_count") or 0)
        limit = int(subscription.get("character_limit") or 0)
        remaining = max(0, limit - used)
        candidates = [
            item
            for item in voices.get("voices", [])
            if isinstance(item, dict)
            and str(item.get("category") or "").lower() == "premade"
        ]
        if not candidates:
            raise RuntimeError("ELEVENLABS_APPROVED_EXISTING_VOICE_MISSING")
        candidates.sort(
            key=lambda item: (
                str(item.get("category")),
                str(item.get("name")),
                str(item.get("voice_id")),
            )
        )
        selected_voice = candidates[0]
        available_models = {
            str(item.get("model_id")) for item in models if isinstance(item, dict)
        }
        model_id = next(
            (item for item in APPROVED_ELEVENLABS_MODELS if item in available_models),
            None,
        )
        if not model_id:
            raise RuntimeError("ELEVENLABS_APPROVED_MODEL_MISSING")
        return {
            "credits_available": remaining >= required_characters,
            "character_limit": limit,
            "character_count": used,
            "characters_remaining": remaining,
            "voice_id": str(selected_voice["voice_id"]),
            "voice_name": str(selected_voice.get("name") or "provider-voice"),
            "voice_category": str(selected_voice.get("category") or "existing"),
            "model_id": model_id,
            "provider_call_made": True,
            "readiness_probe_only": True,
        }

    def generate_once(
        self,
        *,
        api_key: str,
        voice_id: str,
        model_id: str,
        text: str,
        destination: Path,
    ) -> dict[str, Any]:
        if self.generation_count:
            raise RuntimeError("ELEVENLABS_GENERATION_LIMIT_EXCEEDED")
        url = f"{self.base_url}/text-to-speech/{urllib.parse.quote(voice_id)}?output_format=mp3_44100_128"
        content, response_headers = self.transport.bytes_request(
            "POST",
            url,
            headers={"xi-api-key": api_key, "Accept": "audio/mpeg"},
            payload={
                "text": text,
                "model_id": model_id,
                "language_code": "en",
                "voice_settings": {
                    "stability": 0.55,
                    "similarity_boost": 0.75,
                    "style": 0.0,
                    "use_speaker_boost": True,
                },
            },
            timeout=120,
        )
        self.generation_count += 1
        if not content:
            raise RuntimeError("ELEVENLABS_EMPTY_AUDIO")
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.name + ".part")
        try:
            part.write_bytes(content)
            os.replace(part, destination)
        finally:
            part.unlink(missing_ok=True)
        return {
            "provider": "ELEVENLABS",
            "model_id": model_id,
            "voice_id": voice_id,
            "input_text_hash": hashlib.sha256(text.encode()).hexdigest(),
            "input_character_count": len(text),
            "output_path": str(destination),
            "size_bytes": destination.stat().st_size,
            "sha256": _sha256_file(destination),
            "request_id_reference": _redacted_header_reference(
                response_headers, "request-id"
            ),
            "generation_count": 1,
            "provider_call_made": True,
            "production_eligible": False,
            "not_publishable": True,
        }


class DrivePA1RArchive:
    def __init__(self, session, settings: Settings):
        self.session = session
        self.settings = settings
        self.config = GoogleDriveConfigService(settings)
        self.credentials = GoogleDriveOAuthCredentialService(
            session, config_service=self.config
        )
        self.provider = GoogleDriveMediaStorageProvider()

    def access_token(self) -> str:
        reference = self.credentials.get_connected_reference()
        if reference is None:
            raise RuntimeError("DRIVE_OAUTH_NOT_CONNECTED")
        token = self.credentials.get_valid_access_token(reference)
        if not token:
            raise RuntimeError("DRIVE_OAUTH_NEEDS_REAUTH")
        return token

    def quota_readiness(
        self, *, access_token: str, transport: HTTPTransport | None = None
    ) -> dict[str, Any]:
        client = transport or NoRetryHTTPTransport()
        payload, _ = client.json_request(
            "GET",
            "https://www.googleapis.com/drive/v3/about?fields=storageQuota",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not isinstance(payload, dict):
            raise RuntimeError("DRIVE_QUOTA_RESPONSE_INVALID")
        quota = payload.get("storageQuota") or {}
        limit = int(quota.get("limit") or 0)
        usage = int(quota.get("usage") or 0)
        return {
            "quota_available": limit == 0 or usage < limit,
            "limit_bytes": limit or None,
            "usage_bytes": usage,
            "remaining_bytes": (limit - usage) if limit else None,
            "provider_call_made": True,
            "readiness_probe_only": True,
        }

    def upload_and_verify(
        self,
        *,
        access_token: str,
        manifest: ProductionArchiveManifest,
        run_id: str,
        today: date | None = None,
    ) -> DriveArchiveReceipt:
        root_id = self.config.root_folder_id()
        if not root_id:
            raise RuntimeError("DRIVE_ROOT_FOLDER_MISSING")
        root_relative = f"smoke_tests/{(today or datetime.now(UTC).date()).isoformat()}/pa1r/{run_id}"
        _validate_pa1r_drive_path(root_relative)
        run_folder_id = self.provider.ensure_folder_path(
            access_token=access_token,
            root_folder_id=root_id,
            folder_path=root_relative.split("/"),
        )
        receipts: list[DriveArchiveFileReceipt] = []
        mismatches: list[str] = []
        for entry in manifest.files:
            relative = Path(entry.expected_archive_path)
            folder_id = self.provider.ensure_folder_path(
                access_token=access_token,
                root_folder_id=run_folder_id,
                folder_path=list(relative.parent.parts)
                if str(relative.parent) != "."
                else [],
            )
            local_path = Path(entry.source_path)
            upload = self.provider.upload_file(
                access_token=access_token,
                local_path=local_path,
                folder_id=folder_id,
                upload_mode=self.config.upload_mode(),
                mime_type=mimetypes.guess_type(local_path.name)[0]
                or "application/octet-stream",
            )
            remote = self.provider.get_file_metadata(
                access_token=access_token, drive_file_id=upload.drive_file_id
            )
            remote_md5 = (
                str((remote.technical_appendix or {}).get("md5_checksum") or "") or None
            )
            size_ok = remote.size_bytes == entry.size_bytes
            sha_ok = bool(
                remote.checksum_sha256 and remote.checksum_sha256 == entry.sha256
            )
            md5_ok = bool(remote_md5 and entry.md5 and remote_md5 == entry.md5)
            verified = bool(remote.drive_file_id and size_ok and (sha_ok or md5_ok))
            method = (
                "SHA256" if sha_ok else "DRIVE_MD5_PLUS_SIZE" if md5_ok else "FAILED"
            )
            if entry.required_for_archive and not verified:
                mismatches.append(f"DRIVE_VERIFY_MISMATCH:{entry.logical_role}")
            receipts.append(
                DriveArchiveFileReceipt(
                    archive_path=entry.expected_archive_path,
                    drive_file_id=remote.drive_file_id,
                    local_size=entry.size_bytes,
                    drive_size=remote.size_bytes,
                    local_sha256=entry.sha256,
                    drive_sha256=remote.checksum_sha256,
                    local_md5=entry.md5,
                    drive_md5=remote_md5,
                    verification_method=method,
                    verified=verified,
                )
            )
        state = "FAILED" if mismatches else "VERIFIED"
        payload = {
            "archive_manifest_ref": manifest.manifest_id,
            "archive_manifest_hash": manifest.manifest_hash,
            "configured_root_folder_id_reference": "configured://google-drive-root",
            "root_relative_folder_path": root_relative,
            "drive_folder_id": run_folder_id,
            "files": [item.model_dump(mode="json") for item in receipts],
            "total_local_size": manifest.total_size_bytes,
            "total_drive_size": sum(item.drive_size or 0 for item in receipts),
            "archive_state": state,
            "mismatch_reason_codes": mismatches,
            "verified_at": datetime.now(UTC) if state == "VERIFIED" else None,
            "provider_call_made": True,
            "transport": "GOOGLE_DRIVE_API",
        }
        return DriveArchiveReceipt(**payload, receipt_hash=stable_hash(payload))


def pa1r_cost_evidence(
    settings: Settings, *, narration_text: str = PA1R_NARRATION
) -> dict[str, Any]:
    monthly_cost = float(settings.elevenlabs_monthly_cap_usd or 0)
    monthly_chars = int(settings.elevenlabs_monthly_credit_cap or 0)
    eleven_estimate = (
        (monthly_cost / monthly_chars * len(narration_text))
        if monthly_cost and monthly_chars
        else 0.05
    )
    total = round(PA1R_VEO_ESTIMATE_USD + eleven_estimate, 6)
    payload = {
        "currency": "USD",
        "pexels_estimate": 0.0,
        "elevenlabs_estimate": round(eleven_estimate, 6),
        "google_veo_estimate": PA1R_VEO_ESTIMATE_USD,
        "native_ffmpeg_estimate": 0.0,
        "drive_estimate": 0.0,
        "estimated_total": total,
        "hard_cap": PA1R_HARD_CAP_USD,
        "under_hard_cap": total <= PA1R_HARD_CAP_USD,
        "actual_cost_usd": None,
        "production_eligible": False,
    }
    return {**payload, "snapshot_hash": stable_hash(payload)}


def provider_idempotency_key(
    run_id: str, provider: str, operation: str, payload: dict[str, Any]
) -> str:
    return "pa1r-idem:" + stable_hash(
        {
            "run_id": run_id,
            "provider": provider,
            "operation": operation,
            "payload_hash": stable_hash(payload),
        }
    )


def probe_media(
    path: Path, *, ffprobe: str = "/opt/homebrew/bin/ffprobe"
) -> dict[str, Any]:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    payload["evidence_sha256"] = _sha256_file(path)
    return payload


def media_duration_seconds(probe: dict[str, Any]) -> float:
    return float((probe.get("format") or {}).get("duration") or 0)


def audio_qc(
    probe: dict[str, Any],
    *,
    minimum_duration: float = 18.0,
    maximum_duration: float = 25.0,
) -> dict[str, Any]:
    streams = probe.get("streams") or []
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = media_duration_seconds(probe)
    passed = bool(audio and minimum_duration <= duration <= maximum_duration)
    return {
        "decodable": bool(audio),
        "non_silent_structural_check": bool(audio),
        "duration_seconds": duration,
        "duration_plausible": minimum_duration <= duration <= maximum_duration,
        "no_truncation_structural_check": bool(audio and duration >= minimum_duration),
        "understandability_human_review": "PENDING",
        "severe_pronunciation_human_review": "PENDING",
        "result": "PASS" if passed else "FAIL",
    }


def media_qc_permits_archive(
    result: str, *, warn_policy_acceptable: bool = False
) -> bool:
    return result == "PASS" or (result == "WARN" and warn_policy_acceptable)


def archive_permits_cleanup(receipt: DriveArchiveReceipt) -> bool:
    return (
        receipt.archive_state == "VERIFIED"
        and not receipt.mismatch_reason_codes
        and all(item.verified for item in receipt.files)
    )


def _validate_pa1r_drive_path(value: str) -> None:
    expected_prefix = "smoke_tests/"
    parts = Path(value).parts
    if not value.startswith(expected_prefix) or len(parts) != 4 or parts[2] != "pa1r":
        raise ValueError("PA1R_DRIVE_PATH_INVALID")
    if value.startswith("/") or ".." in parts:
        raise ValueError("PA1R_DRIVE_PATH_NOT_ROOT_RELATIVE")
    if any(part.lower() in {"vcos", "vcos media"} for part in parts):
        raise ValueError("PA1R_DRIVE_NESTED_VCOS_FORBIDDEN")
    if any(part.lower().endswith("_unknown") for part in parts):
        raise ValueError("PA1R_DRIVE_UNKNOWN_SCOPE_FORBIDDEN")


def _redacted_header_reference(headers: dict[str, str], key: str) -> str | None:
    value = next(
        (value for name, value in headers.items() if name.lower() == key.lower()), None
    )
    return (
        f"header-ref://{hashlib.sha256(value.encode()).hexdigest()[:16]}"
        if value
        else None
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        part.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
