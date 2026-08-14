from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import certifi

from app.contracts.asset_acquisition import (
    AssetRequest,
    ParsedStockCandidate,
    PexelsQueryPlan,
    StockCandidateRankingManifest,
)
from app.contracts.temporal_authority import (
    ForcedAlignmentEvidence,
    NarrationTimingSeed,
    SpokenTextNormalized,
)
from app.contracts.visual_direction import (
    VisualAssetEvidence,
    VisualDirectionContract,
    VisualRankingWeights,
    VisualRiskPenalties,
    VisualScoreThresholds,
)
from app.services.native_render_plan import stable_hash
from app.services.pa1r import (
    HTTPTransport,
    NoRetryHTTPTransport,
    RedactedProviderHTTPError,
    media_duration_seconds,
    probe_media,
)
from app.services.provider_asset_manifests import (
    PexelsRateLimitMetadataParser,
    PexelsResponseParser,
)
from app.services.stock_candidate_ranker import StockCandidateRanker
from app.services.temporal_authority import (
    ElevenLabsForcedAlignmentRequestBuilder,
    ElevenLabsForcedAlignmentResponseParser,
    ElevenLabsTimestampRequestBuilder,
    ElevenLabsTimingResponseParser,
)


ELEVENLABS_API_BASE_URL = "https://api.elevenlabs.io"
PEXELS_API_BASE_URL = "https://api.pexels.com"
PEXELS_CQR1_HEADERS = {
    "User-Agent": "VCOS-CQR1/1.0",
    "Accept": "application/json",
}
PEXELS_METADATA_SEMANTIC_POLICY_REF = (
    "policy://pexels/metadata-semantic-hard-gate/media-production-workstation/v1"
)
PEXELS_PHYSICAL_PRODUCTION_METADATA_POLICY_REF = (
    "policy://pexels/metadata-semantic-hard-gate/screen-free-physical-production/v1"
)
_PHYSICAL_PRODUCTION_REQUEST_CONCEPTS = {
    "behind the scenes",
    "camera crew",
    "cinematography",
    "film crew",
    "film set",
    "physical production",
    "production crew",
    "production set",
    "studio lighting",
}
_PHYSICAL_PRODUCTION_REQUIRED_METADATA_CONCEPTS = {
    "behind the scenes",
    "boom microphone",
    "camera crew",
    "camera operator",
    "cinema camera",
    "cinematographer",
    "cinematography",
    "clapperboard",
    "film crew",
    "film set",
    "filming",
    "light stand",
    "lighting equipment",
    "production crew",
    "production set",
    "soundstage",
    "studio lights",
    "studio lighting",
    "tripod",
    "video camera",
}
_PHYSICAL_PRODUCTION_FORBIDDEN_METADATA_CONCEPTS = {
    "apple",
    "cell phone",
    "computer",
    "computers",
    "display screen",
    "imac",
    "interface",
    "interfaces",
    "laptop",
    "laptops",
    "logo",
    "logos",
    "mobile phone",
    "monitor",
    "monitors",
    "phone",
    "phones",
    "screen",
    "screens",
    "smartphone",
    "smartphones",
    "software",
    "television",
    "televisions",
    "tv",
    "ui",
}
_MEDIA_PRODUCTION_REQUEST_CONCEPTS = {
    "editing workflow",
    "media operator",
    "media production",
    "media workflow",
    "post production",
    "production workflow",
    "video editing",
    "video production",
    "video workflow",
}
_MEDIA_PRODUCTION_POSITIVE_METADATA_CONCEPTS = {
    "computer",
    "creative workstation",
    "edit suite",
    "editing",
    "editing suite",
    "editor",
    "film editor",
    "footage",
    "keyboard",
    "laptop",
    "media production",
    "media workflow",
    "monitor",
    "post production",
    "production workflow",
    "timeline",
    "video editing",
    "video editor",
    "workstation",
}
_MEDIA_PRODUCTION_REQUIRED_DOMAIN_METADATA_CONCEPTS = {
    "edit suite",
    "editing",
    "editing suite",
    "editor",
    "film editor",
    "footage",
    "media production",
    "media workflow",
    "post production",
    "production workflow",
    "timeline",
    "video editing",
    "video editor",
}
_MEDIA_PRODUCTION_OUT_OF_DOMAIN_METADATA_CONCEPTS = {
    "aeroplane",
    "aeroplanes",
    "aircraft",
    "airplane",
    "airplanes",
    "airport",
    "aviation",
    "flight deck",
    "plane",
    "planes",
    "runway",
}


class MultipartJSONTransport(Protocol):
    """A single-attempt multipart transport used only by Forced Alignment."""

    def multipart_json_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        fields: Mapping[str, str],
        files: Mapping[str, tuple[str, str, bytes]],
        timeout: int = 120,
    ) -> tuple[dict[str, Any] | list[Any], dict[str, str]]: ...


class NoRetryMultipartJSONTransport:
    """Issue exactly one urllib request; this class contains no retry loop."""

    def __init__(self) -> None:
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def multipart_json_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        fields: Mapping[str, str],
        files: Mapping[str, tuple[str, str, bytes]],
        timeout: int = 120,
    ) -> tuple[dict[str, Any] | list[Any], dict[str, str]]:
        boundary = f"vcos-cqr1-{uuid.uuid4().hex}"
        body = _multipart_body(boundary=boundary, fields=fields, files=files)
        request_headers = {
            **headers,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }
        request = urllib.request.Request(
            url, method=method, data=body, headers=request_headers
        )
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=self.ssl_context,
        ) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            return parsed, {
                str(key): str(value) for key, value in response.headers.items()
            }


@dataclass(frozen=True)
class ElevenLabsTimestampExecution:
    request_hash: str
    audio_path: Path
    audio_asset_ref: str
    audio_sha256: str
    audio_size_bytes: int
    audio_duration_ms: int
    timing_seed: NarrationTimingSeed
    usage_metadata: dict[str, Any]
    provider_call_made: bool = True

    def safe_evidence(self) -> dict[str, Any]:
        return {
            "request_hash": self.request_hash,
            "provider_request_id": self.timing_seed.provider_request_id,
            "provider_voice_id": self.timing_seed.provider_voice_id,
            "provider_model_id": self.timing_seed.provider_model_id,
            "voice_settings": self.timing_seed.voice_settings,
            "pronunciation_dictionary_refs": self.timing_seed.pronunciation_dictionary_refs,
            "source_text_hash": self.timing_seed.source_text_hash,
            "spoken_text_hash": self.timing_seed.spoken_text_hash,
            "audio_path": str(self.audio_path),
            "audio_asset_ref": self.audio_asset_ref,
            "audio_sha256": self.audio_sha256,
            "audio_size_bytes": self.audio_size_bytes,
            "audio_duration_ms": self.audio_duration_ms,
            "timing_seed_ref": f"narration-timing-seed:{self.timing_seed.content_hash}",
            "timing_available": self.timing_seed.timing_available,
            "timing_parse_warnings": self.timing_seed.timing_parse_warnings,
            "usage_metadata": self.usage_metadata,
            "provider_text_normalization": "off",
            "provider_call_made": self.provider_call_made,
            "secret_values_exposed": False,
        }


@dataclass(frozen=True)
class ElevenLabsForcedAlignmentExecution:
    request_hash: str
    provider_response_hash: str
    evidence: ForcedAlignmentEvidence
    provider_call_made: bool = True

    def safe_evidence(self) -> dict[str, Any]:
        return {
            "request_hash": self.request_hash,
            "provider_request_hash": self.request_hash,
            "provider_response_hash": self.provider_response_hash,
            "provider_request_id": self.evidence.provider_request_id,
            "provider_request_id_availability": (
                self.evidence.provider_request_id_availability
            ),
            "audio_asset_ref": self.evidence.audio_asset_ref,
            "audio_duration_ms": self.evidence.audio_duration_ms,
            "spoken_text_hash": self.evidence.spoken_text_hash,
            "word_count": len(self.evidence.words),
            "character_count": len(self.evidence.characters),
            "alignment_loss": self.evidence.alignment_loss,
            "transcript_loss": self.evidence.transcript_loss,
            "missing_tokens": self.evidence.missing_tokens,
            "extra_words": self.evidence.extra_words,
            "warnings": self.evidence.warnings,
            "verification_status": self.evidence.verification_status,
            "forced_alignment_ref": f"forced-alignment:{self.evidence.content_hash}",
            "forced_alignment_content_hash": self.evidence.content_hash,
            "provider_call_made": self.provider_call_made,
            "secret_values_exposed": False,
        }


@dataclass(frozen=True)
class PlannedPexelsSearchExecution:
    query_plan: PexelsQueryPlan
    query_used: str
    ranking: StockCandidateRankingManifest
    candidates: tuple[ParsedStockCandidate, ...]
    selected_candidate: ParsedStockCandidate | None
    rate_limit: dict[str, int | None]
    scoring_basis: dict[str, Any] | None = None
    provider_call_made: bool = True

    def safe_evidence(self) -> dict[str, Any]:
        selected = (
            self.selected_candidate.model_dump(mode="json", exclude={"video_files"})
            if self.selected_candidate is not None
            else None
        )
        return {
            "query_plan": self.query_plan.model_dump(mode="json"),
            "query_used": self.query_used,
            "candidate_count": len(self.candidates),
            "ranking": self.ranking.model_dump(mode="json"),
            "selected_candidate": selected,
            "rate_limit": self.rate_limit,
            "scoring_basis": self.scoring_basis or {},
            "provider_call_made": self.provider_call_made,
            "raw_media_url_persisted": False,
            "secret_values_exposed": False,
        }


class ElevenLabsConvertWithTimestampsClient:
    """One-shot real transport around the existing repaired temporal contracts."""

    def __init__(
        self,
        transport: HTTPTransport | None = None,
        *,
        media_probe: Callable[[Path], dict[str, Any]] = probe_media,
        base_url: str = ELEVENLABS_API_BASE_URL,
    ) -> None:
        self.transport = transport or NoRetryHTTPTransport()
        self.media_probe = media_probe
        self.base_url = base_url.rstrip("/")
        self.call_count = 0
        self.request_builder = ElevenLabsTimestampRequestBuilder()
        self.response_parser = ElevenLabsTimingResponseParser()

    def execute_once(
        self,
        *,
        api_key: str,
        normalized: SpokenTextNormalized,
        voice_id: str,
        model_id: str,
        destination: Path,
        voice_settings: dict[str, Any] | None = None,
        seed: int | None = None,
        provider_context: dict[str, Any] | None = None,
        audio_asset_ref: str | None = None,
    ) -> ElevenLabsTimestampExecution:
        _require_nonempty(api_key, "ELEVENLABS_API_KEY_MISSING")
        _require_nonempty(voice_id, "ELEVENLABS_VOICE_ID_MISSING")
        _require_nonempty(model_id, "ELEVENLABS_MODEL_ID_MISSING")
        if self.call_count:
            raise RuntimeError("ELEVENLABS_TTS_CALL_LIMIT_EXCEEDED")
        if (
            destination.exists()
            or destination.with_name(destination.name + ".part").exists()
        ):
            raise FileExistsError("ELEVENLABS_TTS_DESTINATION_NOT_FRESH")
        request = self.request_builder.build(
            normalized=normalized,
            voice_id=voice_id,
            model_id=model_id,
            voice_settings=voice_settings,
            seed=seed,
            provider_context=provider_context,
        )
        self.call_count += 1
        try:
            response, headers = self.transport.json_request(
                "POST",
                self.base_url + str(request["endpoint_path"]),
                headers={"xi-api-key": api_key, "Accept": "application/json"},
                payload=dict(request["payload"]),
                timeout=120,
            )
        except urllib.error.HTTPError as exc:
            raise RedactedProviderHTTPError(
                "elevenlabs",
                exc,
                secret_values=(api_key,),
            ) from None
        if not isinstance(response, dict):
            raise RuntimeError("ELEVENLABS_TTS_RESPONSE_INVALID")
        audio = _decode_audio_payload(response)
        _write_bytes_atomic(destination, audio)
        digest = _sha256_file(destination)
        duration_ms = round(
            media_duration_seconds(self.media_probe(destination)) * 1000
        )
        if duration_ms <= 0:
            raise RuntimeError("ELEVENLABS_TTS_AUDIO_DURATION_INVALID")
        resolved_audio_ref = audio_asset_ref or f"file-sha256:{digest}"
        timing_seed = self.response_parser.parse(
            response=response,
            normalized=normalized,
            audio_asset_ref=resolved_audio_ref,
            audio_duration_ms=duration_ms,
            model_id=model_id,
            voice_id=voice_id,
            voice_settings=voice_settings,
            pronunciation_dictionary_refs=normalized.pronunciation_dictionary_refs,
            seed=seed,
            response_headers=headers,
        )
        return ElevenLabsTimestampExecution(
            request_hash=str(request["request_hash"]),
            audio_path=destination,
            audio_asset_ref=resolved_audio_ref,
            audio_sha256=digest,
            audio_size_bytes=destination.stat().st_size,
            audio_duration_ms=duration_ms,
            timing_seed=timing_seed,
            usage_metadata=_safe_usage_metadata(response),
        )


class ElevenLabsForcedAlignmentClient:
    """One-shot Forced Alignment transport using the existing strict parser."""

    def __init__(
        self,
        transport: MultipartJSONTransport | None = None,
        *,
        base_url: str = ELEVENLABS_API_BASE_URL,
        response_capture: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.transport = transport or NoRetryMultipartJSONTransport()
        self.base_url = base_url.rstrip("/")
        self.call_count = 0
        self.response_capture = response_capture
        self.request_builder = ElevenLabsForcedAlignmentRequestBuilder()
        self.response_parser = ElevenLabsForcedAlignmentResponseParser()

    def execute_once(
        self,
        *,
        api_key: str,
        normalized: SpokenTextNormalized,
        audio_path: Path,
        audio_asset_ref: str,
        audio_duration_ms: int,
    ) -> ElevenLabsForcedAlignmentExecution:
        _require_nonempty(api_key, "ELEVENLABS_API_KEY_MISSING")
        _require_nonempty(audio_asset_ref, "ELEVENLABS_AUDIO_ASSET_REF_MISSING")
        if self.call_count:
            raise RuntimeError("ELEVENLABS_FORCED_ALIGNMENT_CALL_LIMIT_EXCEEDED")
        if not audio_path.is_file() or audio_path.stat().st_size <= 0:
            raise FileNotFoundError("ELEVENLABS_FORCED_ALIGNMENT_AUDIO_MISSING")
        if audio_duration_ms <= 0:
            raise ValueError("ELEVENLABS_FORCED_ALIGNMENT_AUDIO_DURATION_INVALID")
        request = self.request_builder.build(
            audio_asset_ref=audio_asset_ref,
            normalized=normalized,
        )
        mime_type = (
            mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
        )
        self.call_count += 1
        try:
            response, headers = self.transport.multipart_json_request(
                "POST",
                self.base_url + str(request["endpoint_path"]),
                headers={"xi-api-key": api_key, "Accept": "application/json"},
                fields={"text": normalized.spoken_text},
                files={"file": (audio_path.name, mime_type, audio_path.read_bytes())},
                timeout=120,
            )
        except urllib.error.HTTPError as exc:
            raise RedactedProviderHTTPError(
                "elevenlabs",
                exc,
                secret_values=(api_key,),
            ) from None
        if not isinstance(response, dict):
            raise RuntimeError("ELEVENLABS_FORCED_ALIGNMENT_RESPONSE_INVALID")
        safe_response_capture = _safe_forced_alignment_response_capture(
            response, headers
        )
        if self.response_capture is not None:
            self.response_capture(safe_response_capture)
        evidence = self.response_parser.parse(
            response=response,
            normalized=normalized,
            audio_asset_ref=audio_asset_ref,
            audio_duration_ms=audio_duration_ms,
            response_headers=headers,
        )
        return ElevenLabsForcedAlignmentExecution(
            request_hash=str(request["request_hash"]),
            provider_response_hash=str(safe_response_capture["content_hash"]),
            evidence=evidence,
        )


def _safe_forced_alignment_response_capture(
    response: Mapping[str, Any], response_headers: Mapping[str, str]
) -> dict[str, Any]:
    """Retain every parser input while excluding unrelated provider fields."""

    timing_keys = {
        "text",
        "word",
        "start",
        "end",
        "start_ms",
        "end_ms",
        "type",
        "loss",
    }

    def timing_items(raw: Any) -> Any:
        if isinstance(raw, list):
            return [
                {key: item[key] for key in timing_keys if key in item}
                for item in raw
                if isinstance(item, Mapping)
            ]
        if isinstance(raw, Mapping):
            character_keys = {
                "characters",
                "character_start_times_seconds",
                "character_end_times_seconds",
            }
            return {key: raw[key] for key in character_keys if key in raw}
        return raw

    safe_response = {
        key: response[key]
        for key in ("request_id", "alignment_loss", "loss", "transcript_loss")
        if key in response
    }
    safe_response["words"] = timing_items(response.get("words"))
    if "characters" in response:
        safe_response["characters"] = timing_items(response.get("characters"))
    headers = {
        str(key).casefold(): str(value)
        for key, value in response_headers.items()
        if str(key).casefold() in {"request-id", "x-request-id"}
    }
    provider_request_id = str(
        response.get("request_id")
        or headers.get("request-id")
        or headers.get("x-request-id")
        or ""
    ).strip()
    payload = {
        "response": safe_response,
        "response_headers": headers,
        "provider_request_id_availability": (
            "PRESENT" if provider_request_id else "NOT_EXPOSED_BY_ENDPOINT"
        ),
        "capture_scope": "FORCED_ALIGNMENT_PARSER_INPUT_ALLOWLIST",
        "secret_values_exposed": False,
    }
    payload["content_hash"] = stable_hash(payload)
    return payload


class PlannedPexelsV2SearchClient:
    """Execute only the first query from one validated v2 plan, then rank locally."""

    def __init__(
        self,
        transport: HTTPTransport | None = None,
        *,
        base_url: str = PEXELS_API_BASE_URL,
    ) -> None:
        self.transport = transport or NoRetryHTTPTransport()
        self.base_url = base_url.rstrip("/")
        self.search_flow_count = 0
        self.response_parser = PexelsResponseParser()
        self.ranker = StockCandidateRanker()

    def search_and_rank_once(
        self,
        *,
        api_key: str,
        plan: PexelsQueryPlan,
        request: AssetRequest,
        visual_direction: VisualDirectionContract,
        weights: VisualRankingWeights,
        risk_penalties: VisualRiskPenalties,
        thresholds: VisualScoreThresholds,
        previous_scene: VisualAssetEvidence | dict | str | None = None,
        next_scene: VisualAssetEvidence | dict | str | None = None,
        previous_asset_usage_refs: list[str] | None = None,
        asset_reuse_history: list[str] | None = None,
        allow_provider_search_review_floor: bool = False,
    ) -> PlannedPexelsSearchExecution:
        _require_nonempty(api_key, "PEXELS_API_KEY_MISSING")
        if self.search_flow_count:
            raise RuntimeError("PEXELS_SEARCH_FLOW_LIMIT_EXCEEDED")
        _validate_pexels_plan(
            plan=plan,
            request=request,
            visual_direction=visual_direction,
        )
        query = plan.queries[0]
        params = urllib.parse.urlencode(
            {
                "query": query,
                "orientation": plan.orientation,
                "size": plan.size_preference,
                "per_page": plan.per_page,
            }
        )
        self.search_flow_count += 1
        try:
            payload, headers = self.transport.json_request(
                "GET",
                f"{self.base_url}{plan.endpoint}?{params}",
                headers={"Authorization": api_key, **PEXELS_CQR1_HEADERS},
                timeout=30,
            )
        except urllib.error.HTTPError as exc:
            raise RedactedProviderHTTPError(
                "pexels",
                exc,
                secret_values=(api_key,),
            ) from None
        if not isinstance(payload, dict):
            raise RuntimeError("PEXELS_RESPONSE_INVALID")
        parsed = self.response_parser.parse(_fill_pexels_descriptions(payload))
        deduplicated = {candidate.provider_asset_id: candidate for candidate in parsed}
        candidates = tuple(deduplicated.values())
        candidates, metadata_semantic_gate = apply_pexels_metadata_semantic_hard_gate(
            candidates,
            request=request,
        )
        scoring_basis: dict[str, Any] = {
            "provider_search_order_used": False,
            "provisional_review_floor_used": False,
            "metadata_semantic_hard_gate": metadata_semantic_gate,
        }
        if allow_provider_search_review_floor:
            candidates = tuple(
                _apply_provider_search_review_floor(
                    candidates,
                    request=request,
                    thresholds=thresholds,
                )
            )
            scoring_basis = {
                "provider_search_order_used": True,
                "provisional_review_floor_used": True,
                "score_semantics": (
                    "PEXELS_QUERY_RELEVANCE_PLUS_API_METADATA_PROVISIONAL; "
                    "POST_DOWNLOAD_REPRESENTATIVE_FRAME_REVIEW_REQUIRED"
                ),
                "semantic_floor": thresholds.semantic_review_min,
                "adjacency_floor": thresholds.adjacency_review_min,
                "provider_reported_numeric_score": False,
                "metadata_semantic_hard_gate": metadata_semantic_gate,
            }
        else:
            candidates = tuple(sorted(candidates, key=lambda item: item.candidate_id))
        ranking = self.ranker.rank(
            request,
            list(candidates),
            previous_asset_usage_refs=previous_asset_usage_refs,
            visual_direction=visual_direction,
            visual_direction_ref=plan.visual_direction_ref,
            previous_scene=previous_scene,
            next_scene=next_scene,
            asset_reuse_history=asset_reuse_history,
            weights=weights,
            risk_penalties=risk_penalties,
            thresholds=thresholds,
        )
        selected = next(
            (
                item
                for item in candidates
                if item.candidate_id == ranking.selected_candidate_id
            ),
            None,
        )
        return PlannedPexelsSearchExecution(
            query_plan=plan,
            query_used=query,
            ranking=ranking,
            candidates=candidates,
            selected_candidate=selected,
            rate_limit=PexelsRateLimitMetadataParser().parse(headers),
            scoring_basis=scoring_basis,
        )


def apply_pexels_metadata_semantic_hard_gate(
    candidates: tuple[ParsedStockCandidate, ...],
    *,
    request: AssetRequest,
) -> tuple[tuple[ParsedStockCandidate, ...], dict[str, Any]]:
    """Apply an auditable domain gate before metadata ranking and selection.

    Provider result order is not semantic proof.  For media-production workflow
    scenes, a candidate therefore needs an explicit editing/workstation signal
    in its description or tags.  Clear aviation metadata is a hard mismatch
    even if the provider placed that result first.  Rejections are attached as
    hard conflict tags so the existing ranker records them without hiding the
    candidate or issuing another search.
    """

    request_corpus = _normalized_concept_corpus(request.semantic_visual_intent)
    physical_request_matches = _matched_concepts(
        request_corpus,
        _PHYSICAL_PRODUCTION_REQUEST_CONCEPTS,
    )
    if physical_request_matches:
        return _apply_physical_production_metadata_gate(
            candidates,
            request_matches=physical_request_matches,
        )

    request_matches = _matched_concepts(
        request_corpus,
        _MEDIA_PRODUCTION_REQUEST_CONCEPTS,
    )
    if not request_matches:
        return candidates, {
            "policy_ref": PEXELS_METADATA_SEMANTIC_POLICY_REF,
            "status": "NOT_APPLICABLE",
            "request_domain": "UNSCOPED",
            "candidate_decisions": [],
        }

    gated: list[ParsedStockCandidate] = []
    decisions: list[dict[str, Any]] = []
    for candidate in candidates:
        corpus = _normalized_concept_corpus(
            " ".join([candidate.description, *candidate.tags])
        )
        positive_matches = _matched_concepts(
            corpus,
            _MEDIA_PRODUCTION_POSITIVE_METADATA_CONCEPTS,
        )
        required_domain_matches = _matched_concepts(
            corpus,
            _MEDIA_PRODUCTION_REQUIRED_DOMAIN_METADATA_CONCEPTS,
        )
        out_of_domain_matches = _matched_concepts(
            corpus,
            _MEDIA_PRODUCTION_OUT_OF_DOMAIN_METADATA_CONCEPTS,
        )
        reasons: list[str] = []
        if out_of_domain_matches:
            reasons.append("PEXELS_METADATA_OUT_OF_DOMAIN")
        if not required_domain_matches:
            reasons.append(
                "PEXELS_METADATA_REQUIRED_POST_PRODUCTION_OR_WORKSTATION_SIGNAL_MISSING"
            )
        hard_conflicts = list(dict.fromkeys([*candidate.hard_conflict_tags, *reasons]))
        gated.append(
            candidate.model_copy(update={"hard_conflict_tags": hard_conflicts})
        )
        decisions.append(
            {
                "candidate_id": candidate.candidate_id,
                "verdict": "BLOCK" if reasons else "ELIGIBLE_FOR_RANKING",
                "positive_metadata_matches": positive_matches,
                "required_domain_metadata_matches": required_domain_matches,
                "out_of_domain_metadata_matches": out_of_domain_matches,
                "reason_codes": reasons,
            }
        )

    return tuple(gated), {
        "policy_ref": PEXELS_METADATA_SEMANTIC_POLICY_REF,
        "status": "APPLIED",
        "request_domain": "MEDIA_PRODUCTION_WORKSTATION",
        "request_domain_matches": request_matches,
        "required_positive_signal": True,
        "candidate_decisions": decisions,
    }


def _apply_physical_production_metadata_gate(
    candidates: tuple[ParsedStockCandidate, ...],
    *,
    request_matches: list[str],
) -> tuple[tuple[ParsedStockCandidate, ...], dict[str, Any]]:
    """Require physical set-production proof and reject screen/UI metadata.

    This branch intentionally runs before the workstation domain because a
    physical-production request can also contain the broad phrase ``video
    production``.  Provider order never overrides a forbidden device, UI, or
    logo signal.
    """

    gated: list[ParsedStockCandidate] = []
    decisions: list[dict[str, Any]] = []
    for candidate in candidates:
        corpus = _normalized_concept_corpus(
            " ".join([candidate.description, *candidate.tags])
        )
        physical_matches = _matched_concepts(
            corpus,
            _PHYSICAL_PRODUCTION_REQUIRED_METADATA_CONCEPTS,
        )
        forbidden_matches = _matched_concepts(
            corpus,
            _PHYSICAL_PRODUCTION_FORBIDDEN_METADATA_CONCEPTS,
        )
        out_of_domain_matches = _matched_concepts(
            corpus,
            _MEDIA_PRODUCTION_OUT_OF_DOMAIN_METADATA_CONCEPTS,
        )
        reasons: list[str] = []
        if not physical_matches:
            reasons.append(
                "PEXELS_METADATA_REQUIRED_PHYSICAL_PRODUCTION_SIGNAL_MISSING"
            )
        if forbidden_matches:
            reasons.append("PEXELS_METADATA_SCREEN_DEVICE_UI_OR_LOGO_CONFLICT")
        if (
            candidate.logo_or_text_present is True
            or candidate.brand_or_trademark_present is True
        ):
            reasons.append("PEXELS_METADATA_LOGO_OR_BRAND_FLAGGED")
        if out_of_domain_matches:
            reasons.append("PEXELS_METADATA_OUT_OF_DOMAIN")
        reasons = list(dict.fromkeys(reasons))
        hard_conflicts = list(dict.fromkeys([*candidate.hard_conflict_tags, *reasons]))
        gated.append(
            candidate.model_copy(update={"hard_conflict_tags": hard_conflicts})
        )
        decisions.append(
            {
                "candidate_id": candidate.candidate_id,
                "verdict": "BLOCK" if reasons else "ELIGIBLE_FOR_RANKING",
                "required_physical_metadata_matches": physical_matches,
                "forbidden_screen_device_ui_logo_matches": forbidden_matches,
                "logo_or_text_flag": candidate.logo_or_text_present,
                "brand_or_trademark_flag": candidate.brand_or_trademark_present,
                "out_of_domain_metadata_matches": out_of_domain_matches,
                "reason_codes": reasons,
            }
        )

    return tuple(gated), {
        "policy_ref": PEXELS_PHYSICAL_PRODUCTION_METADATA_POLICY_REF,
        "status": "APPLIED",
        "request_domain": "SCREEN_FREE_PHYSICAL_PRODUCTION",
        "request_domain_matches": request_matches,
        "required_positive_signal": True,
        "forbidden_metadata_concepts": sorted(
            _PHYSICAL_PRODUCTION_FORBIDDEN_METADATA_CONCEPTS
        ),
        "candidate_decisions": decisions,
    }


def _normalized_concept_corpus(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _matched_concepts(corpus: str, concepts: set[str]) -> list[str]:
    padded = f" {corpus} "
    return sorted(
        concept
        for concept in concepts
        if f" {_normalized_concept_corpus(concept)} " in padded
    )


def _apply_provider_search_review_floor(
    candidates: tuple[ParsedStockCandidate, ...],
    *,
    request: AssetRequest,
    thresholds: VisualScoreThresholds,
) -> list[ParsedStockCandidate]:
    """Create transparent pre-download review floors from Pexels search order.

    Pexels does not return a numeric semantic score, tags, logo/person flags, or
    continuity metadata.  The API result order is still query-relevance
    evidence.  This helper permits selection only at the policy REVIEW floor;
    it never claims PASS and explicitly leaves the asset for representative-
    frame review after the single authorized download.
    """

    wanted = _meaningful_tokens(request.semantic_visual_intent)
    enriched: list[ParsedStockCandidate] = []
    for index, candidate in enumerate(candidates):
        available = _meaningful_tokens(
            " ".join([candidate.description, *candidate.tags])
        )
        lexical = len(wanted & available) / max(1, len(wanted))
        # Provider order decays but never promotes a metadata-only result above
        # the PASS boundary.  Only the first twelve results receive the review
        # floor; deeper results must qualify from their own metadata.
        order_signal = (
            max(
                thresholds.semantic_review_min,
                thresholds.semantic_pass_min - 0.02 - (index * 0.008),
            )
            if index < 12
            else 0.0
        )
        semantic = min(
            thresholds.semantic_pass_min - 0.000001,
            max(lexical, order_signal),
        )
        direction = thresholds.adjacency_review_min
        technical = min(
            1.0,
            min(candidate.width / 1280, candidate.height / 720),
        )
        enriched.append(
            candidate.model_copy(
                update={
                    "semantic_relevance_score": round(semantic, 6),
                    "visual_direction_fit_score": round(direction, 6),
                    "previous_scene_continuity_score": round(direction, 6),
                    "next_scene_continuity_score": round(direction, 6),
                    "crop_safety_score": 0.8
                    if candidate.width >= candidate.height
                    else 0.0,
                    "technical_quality_score": round(technical, 6),
                    "originality_score": 0.6,
                }
            )
        )
    return enriched


def _meaningful_tokens(value: str) -> set[str]:
    stop = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "with",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in stop
    }


def _validate_pexels_plan(
    *,
    plan: PexelsQueryPlan,
    request: AssetRequest,
    visual_direction: VisualDirectionContract,
) -> None:
    if plan.planner_version != "pexels-query-planner/v2.0.0":
        raise ValueError("PEXELS_V2_PLAN_REQUIRED")
    if plan.plan_hash != stable_hash(
        plan.model_dump(mode="json", exclude={"plan_hash"})
    ):
        raise ValueError("PEXELS_QUERY_PLAN_HASH_MISMATCH")
    if request.request_hash != stable_hash(
        request.model_dump(mode="json", exclude={"request_hash"})
    ):
        raise ValueError("PEXELS_ASSET_REQUEST_HASH_MISMATCH")
    if visual_direction.content_hash != stable_hash(
        visual_direction.model_dump(mode="json", exclude={"content_hash"})
    ):
        raise ValueError("PEXELS_VISUAL_DIRECTION_HASH_MISMATCH")
    mismatched = (
        plan.request_id != request.request_id
        or plan.orientation != request.required_orientation
        or plan.minimum_resolution != request.minimum_resolution
        or plan.preferred_resolution != request.preferred_resolution
        or plan.minimum_duration_seconds != request.minimum_duration_seconds
        or plan.visual_direction_hash != visual_direction.content_hash
        or not plan.visual_direction_ref
    )
    if mismatched:
        raise ValueError("PEXELS_PLANNED_INPUT_BINDING_MISMATCH")


def _multipart_body(
    *,
    boundary: str,
    fields: Mapping[str, str],
    files: Mapping[str, tuple[str, str, bytes]],
) -> bytes:
    chunks: list[bytes] = []
    marker = f"--{boundary}\r\n".encode("ascii")
    for name, value in fields.items():
        chunks.extend(
            (
                marker,
                f'Content-Disposition: form-data; name="{_header_value(name)}"\r\n\r\n'.encode(
                    "utf-8"
                ),
                str(value).encode("utf-8"),
                b"\r\n",
            )
        )
    for name, (filename, content_type, content) in files.items():
        chunks.extend(
            (
                marker,
                (
                    f'Content-Disposition: form-data; name="{_header_value(name)}"; '
                    f'filename="{_header_value(filename)}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                content,
                b"\r\n",
            )
        )
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks)


def _header_value(value: str) -> str:
    return str(value).replace("\r", "_").replace("\n", "_").replace('"', "_")


def _decode_audio_payload(response: Mapping[str, Any]) -> bytes:
    encoded = response.get("audio_base64") or response.get("audio")
    if not isinstance(encoded, str) or not encoded:
        raise RuntimeError("ELEVENLABS_TTS_AUDIO_PAYLOAD_MISSING")
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("ELEVENLABS_TTS_AUDIO_BASE64_INVALID") from exc
    if not audio:
        raise RuntimeError("ELEVENLABS_TTS_AUDIO_EMPTY")
    return audio


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_usage_metadata(response: Mapping[str, Any]) -> dict[str, Any]:
    raw = response.get("usage")
    if not isinstance(raw, Mapping):
        return {}
    allowed: dict[str, Any] = {}
    for key, value in raw.items():
        normalized_key = str(key)
        if any(
            fragment in normalized_key.casefold()
            for fragment in ("token", "secret", "url", "authorization")
        ):
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            allowed[normalized_key] = value
    return allowed


def _fill_pexels_descriptions(payload: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(payload))
    for item in copied.get("videos", []):
        if not isinstance(item, dict) or item.get("description"):
            continue
        slug = urllib.parse.urlsplit(str(item.get("url") or "")).path.replace("-", " ")
        item["description"] = " ".join(slug.split())
    return copied


def _require_nonempty(value: str, reason_code: str) -> None:
    if not str(value or "").strip():
        raise ValueError(reason_code)
