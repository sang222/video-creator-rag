from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.parse
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from app.contracts.asset_acquisition import PexelsDownloadPlan, PexelsQueryPlan
from app.contracts.temporal_authority import (
    SourceToSpokenSpan,
    SpokenTextNormalized,
    SpokenToken,
    TextSpan,
)
from app.core.config import Settings
from app.services.config_registry import content_hash
from app.services.cqr1_real_provider import (
    ElevenLabsConvertWithTimestampsClient,
    ElevenLabsForcedAlignmentClient,
)
from app.services.mr1_drive_archive import MR1ArchiveItem, MR1DriveArchiveService
from app.services.mr1_pexels_authority import (
    build_mr1_pexels_asset_request,
    build_mr1_pexels_query_authority,
    mr1_pexels_stock_search_intent_coverage_evidence,
)
from app.services.mr1_real_production import (
    MR1_DRIVE_FINALIZATION_OPERATION_KEY,
    MR1ProviderGateways,
    mr1_drive_finalization_idempotency_key,
)
from app.services.pa1r import NoRetryHTTPTransport, PexelsPA1RClient
from app.services.pexels_media_downloader import PexelsMediaDownloadClient
from app.services.pexels_query_planner import PexelsQueryPlanner


MR1_VOICE_ID = "pNInz6obpgDQGcFmaJgB"
MR1_MODEL_ID = "eleven_multilingual_v2"
MR1_VOICE_SETTINGS: dict[str, Any] = {
    "speed": 0.9,
    "stability": 0.55,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
}
MR1_PEXELS_SCENES = frozenset({"SC-04", "SC-07", "SC-09"})
_SAFE_COMPONENT = re.compile(r"[^a-z0-9._-]+")


class _BoundaryHTTPTransport:
    """Fire a durable-submit hook at the transport boundary, exactly once."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self._callback: Callable[[], None] | None = None
        self.call_count = 0

    def arm(self, callback: Callable[[], None]) -> None:
        if self._callback is not None or self.call_count:
            raise RuntimeError("MR1_PROVIDER_TRANSPORT_ALREADY_CONSUMED")
        self._callback = callback

    def _before_request(self) -> None:
        callback = self._callback
        if callback is None:
            raise RuntimeError("MR1_PROVIDER_SUBMIT_BOUNDARY_NOT_ARMED")
        if self.call_count:
            raise RuntimeError("MR1_PROVIDER_TRANSPORT_RETRY_FORBIDDEN")
        callback()
        self.call_count = 1

    def json_request(self, *args: Any, **kwargs: Any) -> Any:
        self._before_request()
        return self.delegate.json_request(*args, **kwargs)

    def bytes_request(self, *args: Any, **kwargs: Any) -> Any:
        self._before_request()
        return self.delegate.bytes_request(*args, **kwargs)


class _PexelsBoundaryHTTPTransport(_BoundaryHTTPTransport):
    """Bind the one Pexels search submission to its approved wire request."""

    _OFFICIAL_API_ORIGIN = "https://api.pexels.com"
    _OFFICIAL_SEARCH_ENDPOINT = "/v1/videos/search"

    def __init__(
        self,
        delegate: Any,
        *,
        approved_query_authority: Mapping[str, Any],
    ) -> None:
        super().__init__(delegate)
        self.approved_query_authority = deepcopy(
            dict(approved_query_authority)
        )
        self._expected_query_params = self._compile_expected_query_params()

    def _compile_expected_query_params(self) -> dict[str, str]:
        authority = self.approved_query_authority
        query_family = authority.get("query_family")
        primary_query = authority.get("primary_query")
        per_page = authority.get("per_page")
        if (
            authority.get("schema_version")
            != "mr1.pexels-query-authority.v1"
            or authority.get("endpoint") != self._OFFICIAL_SEARCH_ENDPOINT
            or not isinstance(query_family, list)
            or not query_family
            or any(
                not isinstance(value, str) or not value
                for value in query_family
            )
            or not isinstance(primary_query, str)
            or not primary_query
            or primary_query != query_family[0]
            or not isinstance(authority.get("orientation"), str)
            or not authority["orientation"]
            or not isinstance(authority.get("size_preference"), str)
            or not authority["size_preference"]
            or isinstance(per_page, bool)
            or not isinstance(per_page, int)
            or per_page <= 0
            or not _is_sha256(str(authority.get("plan_hash") or ""))
        ):
            raise RuntimeError(
                "MR1_PEXELS_SEARCH_TRANSPORT_AUTHORITY_INVALID"
            )
        return {
            "query": primary_query,
            "orientation": authority["orientation"],
            "size": authority["size_preference"],
            "per_page": str(per_page),
        }

    def _validate_wire_request(
        self,
        *,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
    ) -> None:
        if method != "GET" or payload is not None:
            raise RuntimeError(
                "MR1_PEXELS_SEARCH_TRANSPORT_METHOD_INVALID"
            )
        try:
            parsed = urllib.parse.urlsplit(url)
        except (TypeError, ValueError):
            raise RuntimeError(
                "MR1_PEXELS_SEARCH_TRANSPORT_ENDPOINT_CHANGED"
            ) from None
        if (
            parsed.scheme != "https"
            or parsed.netloc != "api.pexels.com"
            or parsed.path != self._OFFICIAL_SEARCH_ENDPOINT
            or parsed.fragment
            or urllib.parse.urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, "", "")
            )
            != (
                self._OFFICIAL_API_ORIGIN
                + self._OFFICIAL_SEARCH_ENDPOINT
            )
        ):
            raise RuntimeError(
                "MR1_PEXELS_SEARCH_TRANSPORT_ENDPOINT_CHANGED"
            )
        try:
            pairs = urllib.parse.parse_qsl(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
            )
        except ValueError:
            raise RuntimeError(
                "MR1_PEXELS_SEARCH_TRANSPORT_QUERY_CHANGED"
            ) from None
        if (
            len(pairs) != len(self._expected_query_params)
            or len({key for key, _value in pairs}) != len(pairs)
            or dict(pairs) != self._expected_query_params
        ):
            raise RuntimeError(
                "MR1_PEXELS_SEARCH_TRANSPORT_QUERY_CHANGED"
            )

    def json_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> Any:
        self._validate_wire_request(
            method=method,
            url=url,
            payload=payload,
        )
        return super().json_request(
            method,
            url,
            headers=headers,
            payload=payload,
            timeout=timeout,
        )

    def bytes_request(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("MR1_PEXELS_SEARCH_TRANSPORT_METHOD_INVALID")


class _BoundaryMultipartTransport:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self._callback: Callable[[], None] | None = None
        self.call_count = 0

    def arm(self, callback: Callable[[], None]) -> None:
        if self._callback is not None or self.call_count:
            raise RuntimeError("MR1_ALIGNMENT_TRANSPORT_ALREADY_CONSUMED")
        self._callback = callback

    def multipart_json_request(self, *args: Any, **kwargs: Any) -> Any:
        callback = self._callback
        if callback is None:
            raise RuntimeError("MR1_ALIGNMENT_SUBMIT_BOUNDARY_NOT_ARMED")
        if self.call_count:
            raise RuntimeError("MR1_ALIGNMENT_TRANSPORT_RETRY_FORBIDDEN")
        callback()
        self.call_count = 1
        return self.delegate.multipart_json_request(*args, **kwargs)


class _BoundaryMediaDownloader:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self._callback: Callable[[], None] | None = None
        self.call_count = 0

    def arm(self, callback: Callable[[], None]) -> None:
        if self._callback is not None or self.call_count:
            raise RuntimeError("MR1_PEXELS_DOWNLOAD_TRANSPORT_ALREADY_CONSUMED")
        self._callback = callback

    def download(self, *args: Any, **kwargs: Any) -> Any:
        callback = self._callback
        if callback is None:
            raise RuntimeError("MR1_PEXELS_DOWNLOAD_BOUNDARY_NOT_ARMED")
        if self.call_count:
            raise RuntimeError("MR1_PEXELS_DOWNLOAD_RETRY_FORBIDDEN")
        callback()
        self.call_count = 1
        return self.delegate.download(*args, **kwargs)


class _BoundaryDriveProvider:
    """Invoke the hook on the first Drive mutation, not on local preparation."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self._callback: Callable[[], None] | None = None
        self.mutation_boundary_count = 0

    def arm(self, callback: Callable[[], None]) -> None:
        if self._callback is not None:
            raise RuntimeError("MR1_DRIVE_MUTATION_BOUNDARY_ALREADY_ARMED")
        self.mutation_boundary_count = 0
        self._callback = callback

    def declare_resume_boundary(self) -> None:
        """Satisfy the durable runner boundary for journal-only reconciliation."""

        self._mutating()

    def disarm(self) -> None:
        self._callback = None
        self.mutation_boundary_count = 0

    def _mutating(self) -> None:
        if self.mutation_boundary_count:
            return
        callback = self._callback
        if callback is None:
            raise RuntimeError("MR1_DRIVE_MUTATION_BOUNDARY_NOT_ARMED")
        callback()
        self.mutation_boundary_count = 1

    def ensure_folder_path(self, *args: Any, **kwargs: Any) -> Any:
        self._mutating()
        return self.delegate.ensure_folder_path(*args, **kwargs)

    def upload_file(self, *args: Any, **kwargs: Any) -> Any:
        self._mutating()
        return self.delegate.upload_file(*args, **kwargs)

    def get_file_metadata(self, *args: Any, **kwargs: Any) -> Any:
        return self.delegate.get_file_metadata(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


class MR1NarrationGatewayAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        settings: Settings,
        workspace_root: Path,
        client: ElevenLabsConvertWithTimestampsClient | None = None,
    ) -> None:
        # Construction is deliberately side-effect free even when readiness is
        # incomplete; preflight must be able to report the missing credential.
        self.api_key = str(api_key or "").strip()
        self.settings = settings
        self.workspace_root = Path(workspace_root).resolve()
        base_client = client or ElevenLabsConvertWithTimestampsClient()
        self._transport = _BoundaryHTTPTransport(base_client.transport)
        base_client.transport = self._transport
        self.client = base_client

    def preflight(self, *, request: Mapping[str, Any] | None = None) -> dict[str, Any]:
        checks = {
            "global_real_execution_enabled": self.settings.provider_real_execution_enabled
            is True,
            "production_execution_enabled": self.settings.provider_production_execution_enabled
            is True,
            "media_provider_kill_switch_open": self.settings.media_provider_calls_disabled
            is False,
            "elevenlabs_real_execution_enabled": self.settings.elevenlabs_real_execution_enabled
            is True,
            "elevenlabs_generation_enabled": self.settings.elevenlabs_real_generation_enabled
            is True,
            "credential_present": bool(self.api_key),
            "configured_voice_exact": self.settings.elevenlabs_voice_id == MR1_VOICE_ID,
            "configured_model_exact": self.settings.elevenlabs_model_id == MR1_MODEL_ID,
            "hard_budget_mode": self.settings.budget_mode == "hard_env",
            "monthly_ai_budget_covers_mr1": _numeric_at_least(
                self.settings.monthly_ai_budget_usd, 1
            ),
            "elevenlabs_budget_covers_mr1": _numeric_at_least(
                self.settings.elevenlabs_monthly_cap_usd, 1
            ),
            "sdk_retry_disabled": True,
        }
        if request is not None:
            try:
                self._validate_request(dict(request), destination=None)
            except Exception:
                checks["approved_request_exact"] = False
            else:
                checks["approved_request_exact"] = True
        return _preflight_result("ELEVENLABS_NARRATION", checks)

    def execute_once(
        self,
        request: Mapping[str, Any],
        *,
        destination: Path,
        before_submit: Callable[[], None],
    ) -> dict[str, Any]:
        payload = dict(request)
        self._require_ready(payload)
        target = _validated_destination(
            destination,
            requested=payload.get("destination"),
            workspace_root=self.workspace_root,
            must_be_fresh=True,
        )
        normalized = _temporal_normalized(payload)
        self._transport.arm(before_submit)
        execution = self.client.execute_once(
            api_key=self.api_key,
            normalized=normalized,
            voice_id=str(payload["voice_id"]),
            model_id=str(payload["model_id"]),
            destination=target,
            voice_settings=deepcopy(payload["voice_settings"]),
            audio_asset_ref=f"mr1-audio://{payload['idempotency_key']}",
        )
        if self._transport.call_count != 1:
            raise RuntimeError("MR1_NARRATION_NETWORK_BOUNDARY_COUNT_INVALID")
        result = {
            "schema_version": "mr1.elevenlabs-narration-result.v1",
            "provider": "elevenlabs",
            "operation": "narration",
            "request_hash": payload["request_hash"],
            "provider_request_hash": execution.request_hash,
            "provider_request_id": execution.timing_seed.provider_request_id,
            "voice_id": payload["voice_id"],
            "model_id": payload["model_id"],
            "voice_settings": deepcopy(payload["voice_settings"]),
            "normalized_text_hash": payload["normalized_text_hash"],
            "spoken_text_artifact_version_id": payload[
                "spoken_text_artifact_version_id"
            ],
            "audio_path": str(execution.audio_path.resolve()),
            "audio_asset_ref": execution.audio_asset_ref,
            "audio_sha256": execution.audio_sha256,
            "audio_size_bytes": execution.audio_size_bytes,
            "audio_duration_ms": execution.audio_duration_ms,
            "timing_seed": execution.timing_seed.model_dump(mode="json"),
            "temporal_spoken_text_normalized": normalized.model_dump(mode="json"),
            "usage_metadata": _sanitize_durable(execution.usage_metadata),
            "provider_text_normalization": "off",
            "provider_call_made": True,
            "network_submit_count": 1,
            "sdk_retry": False,
            "actual_cost_usd": None,
            "secret_values_exposed": False,
        }
        return _sanitize_durable(result)

    def _validate_request(
        self, payload: dict[str, Any], destination: Path | None
    ) -> None:
        _validate_request_hash(payload)
        if (
            payload.get("provider") != "elevenlabs"
            or payload.get("operation") != "narration"
        ):
            raise ValueError("MR1_NARRATION_PROVIDER_OR_OPERATION_INVALID")
        if (
            payload.get("voice_id") != MR1_VOICE_ID
            or payload.get("model_id") != MR1_MODEL_ID
        ):
            raise ValueError("MR1_NARRATION_VOICE_OR_MODEL_CHANGED")
        if not _settings_equal(payload.get("voice_settings"), MR1_VOICE_SETTINGS):
            raise ValueError("MR1_NARRATION_VOICE_SETTINGS_CHANGED")
        _validate_common_one_shot(payload)
        _validate_normalized_text(payload)
        if (
            destination is not None
            and Path(str(payload.get("destination"))).resolve() != destination.resolve()
        ):
            raise ValueError("MR1_NARRATION_DESTINATION_CHANGED")

    def _require_ready(self, payload: dict[str, Any]) -> None:
        self._validate_request(payload, destination=None)
        readiness = self.preflight(request=payload)
        if readiness["result"] != "PASS":
            raise RuntimeError(
                "MR1_NARRATION_PREFLIGHT_FAILED:" + ",".join(readiness["failed_checks"])
            )


class MR1AlignmentGatewayAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        settings: Settings,
        workspace_root: Path,
        client: ElevenLabsForcedAlignmentClient | None = None,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.settings = settings
        self.workspace_root = Path(workspace_root).resolve()
        base_client = client or ElevenLabsForcedAlignmentClient()
        self._transport = _BoundaryMultipartTransport(base_client.transport)
        base_client.transport = self._transport
        self.client = base_client

    def preflight(self, *, request: Mapping[str, Any] | None = None) -> dict[str, Any]:
        checks = {
            "global_real_execution_enabled": self.settings.provider_real_execution_enabled
            is True,
            "production_execution_enabled": self.settings.provider_production_execution_enabled
            is True,
            "media_provider_kill_switch_open": self.settings.media_provider_calls_disabled
            is False,
            "elevenlabs_real_execution_enabled": self.settings.elevenlabs_real_execution_enabled
            is True,
            "forced_alignment_permission_confirmed": self.settings.elevenlabs_forced_alignment_permission_confirmed
            is True,
            "credential_present": bool(self.api_key),
            "hard_budget_mode": self.settings.budget_mode == "hard_env",
            "monthly_ai_budget_covers_mr1": _numeric_at_least(
                self.settings.monthly_ai_budget_usd, 1
            ),
            "elevenlabs_budget_covers_mr1": _numeric_at_least(
                self.settings.elevenlabs_monthly_cap_usd, 1
            ),
            "sdk_retry_disabled": True,
        }
        if request is not None:
            try:
                self._validate_request(dict(request))
            except Exception:
                checks["approved_request_exact"] = False
            else:
                checks["approved_request_exact"] = True
        return _preflight_result("ELEVENLABS_FORCED_ALIGNMENT", checks)

    def execute_once(
        self,
        request: Mapping[str, Any],
        *,
        audio_path: Path,
        before_submit: Callable[[], None],
    ) -> dict[str, Any]:
        payload = dict(request)
        self._validate_request(payload)
        readiness = self.preflight(request=payload)
        if readiness["result"] != "PASS":
            raise RuntimeError(
                "MR1_ALIGNMENT_PREFLIGHT_FAILED:" + ",".join(readiness["failed_checks"])
            )
        audio = _validated_existing_file(audio_path, self.workspace_root)
        audio_sha256 = _sha256_file(audio)
        if audio_sha256 != payload.get("audio_sha256"):
            raise RuntimeError("MR1_ALIGNMENT_AUDIO_HASH_CHANGED")
        normalized = _temporal_normalized(payload)
        duration_ms = _probe_duration_ms(audio)
        self._transport.arm(before_submit)
        execution = self.client.execute_once(
            api_key=self.api_key,
            normalized=normalized,
            audio_path=audio,
            audio_asset_ref=str(payload["audio_ref"]),
            audio_duration_ms=duration_ms,
        )
        if self._transport.call_count != 1:
            raise RuntimeError("MR1_ALIGNMENT_NETWORK_BOUNDARY_COUNT_INVALID")
        evidence = execution.evidence.model_dump(mode="json")
        verified_words = deepcopy(evidence["words"])
        coverage = (
            len(normalized.spoken_tokens) - len(execution.evidence.missing_tokens)
        ) / len(normalized.spoken_tokens)
        result = {
            "schema_version": "mr1.elevenlabs-forced-alignment-result.v1",
            "provider": "forced_alignment",
            "operation": "forced_alignment",
            "request_hash": payload["request_hash"],
            "provider_request_hash": execution.request_hash,
            "provider_response_hash": execution.provider_response_hash,
            "provider_request_id": execution.evidence.provider_request_id,
            "audio_path": str(audio),
            "audio_ref": payload["audio_ref"],
            "audio_asset_ref": payload["audio_ref"],
            "audio_sha256": audio_sha256,
            "audio_duration_ms": duration_ms,
            "spoken_text_hash": payload["spoken_text_hash"],
            "normalized_text_hash": payload["normalized_text_hash"],
            "verified_words": verified_words,
            "word_count": len(execution.evidence.words),
            "character_count": len(execution.evidence.characters),
            "token_coverage": round(coverage, 8),
            "missing_tokens": deepcopy(execution.evidence.missing_tokens),
            "extra_tokens": deepcopy(execution.evidence.extra_words),
            "alignment_loss": execution.evidence.alignment_loss,
            "transcript_loss": execution.evidence.transcript_loss,
            "warnings": deepcopy(execution.evidence.warnings),
            "verification_status": execution.evidence.verification_status,
            "estimated_timing_fallback_used": False,
            "forced_alignment_ref": (
                "forced-alignment:" + execution.evidence.content_hash
            ),
            "forced_alignment_content_hash": execution.evidence.content_hash,
            "forced_alignment_evidence": evidence,
            "temporal_spoken_text_normalized": normalized.model_dump(mode="json"),
            "provider_call_made": True,
            "network_submit_count": 1,
            "sdk_retry": False,
            "actual_cost_usd": None,
            "secret_values_exposed": False,
        }
        destination = _validated_destination(
            Path(str(payload["destination"])),
            requested=payload["destination"],
            workspace_root=self.workspace_root,
            must_be_fresh=True,
        )
        _write_json_atomic(destination, result)
        return _sanitize_durable(result)

    @staticmethod
    def _validate_request(payload: dict[str, Any]) -> None:
        _validate_request_hash(payload)
        if (
            payload.get("provider") != "forced_alignment"
            or payload.get("operation") != "forced_alignment"
        ):
            raise ValueError("MR1_ALIGNMENT_PROVIDER_OR_OPERATION_INVALID")
        _validate_common_one_shot(payload)
        _validate_normalized_text(payload)
        if payload.get("strict_token_coverage") != 1.0:
            raise ValueError("MR1_ALIGNMENT_TOKEN_COVERAGE_CHANGED")
        if payload.get("estimated_timing_fallback_allowed") is not False:
            raise ValueError("MR1_ALIGNMENT_FALLBACK_FORBIDDEN")
        if not _is_sha256(str(payload.get("audio_sha256") or "")):
            raise ValueError("MR1_ALIGNMENT_AUDIO_HASH_INVALID")
        tokens = payload.get("spoken_tokens")
        if not isinstance(tokens, list) or not tokens:
            raise ValueError("MR1_ALIGNMENT_SPOKEN_TOKENS_MISSING")
        request_words = [
            str(item.get("text") or "")
            for item in tokens
            if isinstance(item, Mapping) and _wordlike(str(item.get("text") or ""))
        ]
        normalized_words = [
            item.text for item in _temporal_normalized(payload).spoken_tokens
        ]
        if [_comparison_key(value) for value in request_words] != [
            _comparison_key(value) for value in normalized_words
        ]:
            raise ValueError("MR1_ALIGNMENT_APPROVED_TOKEN_SEQUENCE_CHANGED")


class MR1PexelsGatewayAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        settings: Settings,
        workspace_root: Path,
        client_factory: Callable[[Any, Any], PexelsPA1RClient] | None = None,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.settings = settings
        self.workspace_root = Path(workspace_root).resolve()
        self.client_factory = client_factory or self._default_client_factory
        self.submitted_scenes: set[str] = set()
        self.selected_provider_asset_ids: set[str] = set()

    @staticmethod
    def _default_client_factory(
        search_transport: Any, downloader: Any
    ) -> PexelsPA1RClient:
        return PexelsPA1RClient(
            transport=search_transport,
            media_downloader=downloader,
        )

    def preflight(self, *, request: Mapping[str, Any] | None = None) -> dict[str, Any]:
        checks = {
            "global_real_execution_enabled": self.settings.provider_real_execution_enabled
            is True,
            "production_execution_enabled": self.settings.provider_production_execution_enabled
            is True,
            "media_provider_kill_switch_open": self.settings.media_provider_calls_disabled
            is False,
            "pexels_real_execution_enabled": self.settings.pexels_real_execution_enabled
            is True,
            "pexels_search_enabled": self.settings.pexels_real_search_enabled is True,
            "credential_present": bool(self.api_key),
            "three_clip_cap_available": self.settings.pexels_max_clips_per_long == 3,
            "attribution_required": self.settings.pexels_attribution_required is True,
            "hard_budget_mode": self.settings.budget_mode == "hard_env",
            "monthly_ai_budget_covers_mr1": _numeric_at_least(
                self.settings.monthly_ai_budget_usd, 1
            ),
            "stock_budget_nonnegative": _numeric_at_least(
                self.settings.stock_monthly_budget_usd, 0
            ),
            "sdk_retry_disabled": True,
            "gemini_unplanned_route_disabled": self.settings.gemini_image_real_generation_enabled
            is False,
            "veo_unplanned_route_disabled": self.settings.veo_real_generation_enabled
            is False,
            "gemini_or_veo_unreachable": True,
        }
        if request is not None:
            try:
                self._validate_request(dict(request))
            except Exception:
                checks["approved_request_exact"] = False
            else:
                checks["approved_request_exact"] = True
        return _preflight_result("PEXELS_SUPPORTING_ASSET", checks)

    def acquire_scene_once(
        self,
        request: Mapping[str, Any],
        *,
        destination: Path,
        before_search_submit: Callable[[], None],
        before_download_submit: Callable[[], None],
    ) -> dict[str, Any]:
        payload = dict(request)
        self._validate_request(payload)
        readiness = self.preflight(request=payload)
        if readiness["result"] != "PASS":
            raise RuntimeError(
                "MR1_PEXELS_PREFLIGHT_FAILED:" + ",".join(readiness["failed_checks"])
            )
        scene_id = str(payload["scene_id"])
        excluded_provider_asset_ids = {
            str(value) for value in payload["excluded_provider_asset_ids"]
        }
        if not self.selected_provider_asset_ids.issubset(excluded_provider_asset_ids):
            raise RuntimeError("MR1_PEXELS_PRIOR_SELECTION_EXCLUSION_INCOMPLETE")
        if scene_id in self.submitted_scenes:
            raise RuntimeError("MR1_PEXELS_SCENE_ATTEMPT_ALREADY_CONSUMED")
        target = _validated_destination(
            destination,
            requested=payload.get("destination"),
            workspace_root=self.workspace_root,
            must_be_fresh=True,
        )
        asset_request = build_mr1_pexels_asset_request(payload)
        query_authority = build_mr1_pexels_query_authority(payload)
        expected_query_plan = PexelsQueryPlanner().plan(
            asset_request,
            per_page=int(query_authority["per_page"]),
        )
        approved_query_authority = payload.get("approved_query_authority")
        if approved_query_authority is not None and (
            not isinstance(approved_query_authority, dict)
            or approved_query_authority != query_authority
        ):
            raise RuntimeError("MR1_PEXELS_QUERY_AUTHORITY_CHANGED")
        try:
            mr1_pexels_stock_search_intent_coverage_evidence(
                payload,
                query_authority,
                semantic_fit_threshold=float(
                    payload["semantic_fit_threshold"]
                ),
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from None

        base_search = NoRetryHTTPTransport()
        search_transport = _PexelsBoundaryHTTPTransport(
            base_search,
            approved_query_authority=query_authority,
        )
        media_downloader = _BoundaryMediaDownloader(PexelsMediaDownloadClient())
        client = self.client_factory(search_transport, media_downloader)

        def search_boundary() -> None:
            before_search_submit()
            self.submitted_scenes.add(scene_id)

        search_transport.arm(search_boundary)
        safe_search, selected, execution_context = client.search_select_once(
            api_key=self.api_key,
            request=asset_request,
            workspace_directory=target.parent,
            excluded_provider_asset_ids=sorted(excluded_provider_asset_ids),
            semantic_fit_threshold=float(payload["semantic_fit_threshold"]),
        )
        if search_transport.call_count != 1:
            raise RuntimeError("MR1_PEXELS_SEARCH_BOUNDARY_COUNT_INVALID")
        _validate_pexels_safe_search_query_authority(
            safe_search,
            query_authority,
            expected_query_plan=expected_query_plan,
        )
        execution_context.workspace_target_path = target
        plan = PexelsDownloadPlan.model_validate(safe_search["download_plan"])
        if (
            plan.provider_asset_id in excluded_provider_asset_ids
            or plan.provider_asset_id in self.selected_provider_asset_ids
        ):
            raise RuntimeError("MR1_PEXELS_DUPLICATE_PROVIDER_ASSET_SELECTED")
        execution_context.validate_against(plan)
        media_downloader.arm(before_download_submit)
        receipt = client.download_once(
            plan=plan,
            execution_context=execution_context,
            request_id=asset_request.request_id,
        )
        if media_downloader.call_count != 1:
            raise RuntimeError("MR1_PEXELS_DOWNLOAD_BOUNDARY_COUNT_INVALID")
        if not target.is_file() or _sha256_file(target) != receipt.sha256:
            raise RuntimeError("MR1_PEXELS_DOWNLOADED_BYTES_MISMATCH")
        probe = deepcopy(receipt.media_probe or {})
        duration_ms = _duration_ms_from_probe(probe)
        if duration_ms <= 0:
            # The hardened download client intentionally probes only container,
            # codec and dimensions.  MR1 additionally needs duration from the
            # downloaded bytes, so perform a second local-only full ffprobe.
            from app.services.pa1r import probe_media

            probe = probe_media(target)
            duration_ms = _duration_ms_from_probe(probe)
        if duration_ms <= 0:
            raise RuntimeError("MR1_PEXELS_DOWNLOADED_DURATION_INVALID")
        if duration_ms < int(payload["stock_context_duration_ms"]):
            raise RuntimeError("MR1_PEXELS_DOWNLOADED_CLIP_TOO_SHORT_FOR_TIMELINE")
        if probe.get("evidence_sha256") not in {None, receipt.sha256}:
            raise RuntimeError("MR1_PEXELS_PROBE_BYTES_CHANGED")
        self.selected_provider_asset_ids.add(plan.provider_asset_id)
        result = {
            "schema_version": "mr1.pexels-scene-acquisition-result.v1",
            "provider": "pexels_api",
            "scene_id": scene_id,
            "route": "PEXELS_VIDEO",
            "request_hash": payload["request_hash"],
            "asset_request_hash": asset_request.request_hash,
            "package_semantic_intent": payload["semantic_intent"],
            "stock_search_intent": (
                payload.get("stock_search_intent")
                or payload["semantic_intent"]
            ),
            "stock_search_intent_scope": payload.get(
                "stock_search_intent_scope"
            ),
            "canonical_timeline_hash": payload["canonical_timeline_hash"],
            "timing_authority": payload["timing_authority"],
            "scene_start_ms": payload["scene_start_ms"],
            "scene_end_ms": payload["scene_end_ms"],
            "scene_duration_ms": payload["scene_duration_ms"],
            "provider_asset_id": plan.provider_asset_id,
            "provider_file_id": plan.provider_file_id,
            "source_page_url": plan.source_page_url,
            "creator_name": plan.creator_name,
            "creator_url": plan.creator_url,
            "creator_ref": (
                "pexels-creator://"
                + hashlib.sha256(plan.creator_url.encode()).hexdigest()[:24]
            ),
            "license_ref": "https://www.pexels.com/license/",
            "rights_policy_ref": "policy://pexels/supporting-stock/mr1/v1",
            "attribution_copy": f"Video by {plan.creator_name} on Pexels",
            "query_plan": safe_search["query_plan"],
            "ranking": safe_search["ranking"],
            "selected_candidate": selected,
            "cross_scene_exclusion": {
                "excluded_provider_asset_ids": sorted(excluded_provider_asset_ids),
                "filter_applied_before_ranking": True,
                "selected_provider_asset_id_unique": True,
            },
            "download_url_hash": plan.download_url_hash,
            "expected_media_host": plan.expected_media_host,
            "local_path": str(target),
            "sha256": receipt.sha256,
            "size_bytes": receipt.size_bytes,
            "width": plan.width,
            "height": plan.height,
            "duration_ms": duration_ms,
            "media_probe": probe,
            "http_evidence": deepcopy(receipt.http_evidence or {}),
            "search_submit_count": 1,
            "download_submit_count": 1,
            "provider_call_made": True,
            "logical_provider_attempt_count": 1,
            "raw_media_url_persisted": False,
            "automatic_fallback_used": False,
            "provider_substitution_used": False,
            "production_eligible": True,
            "sdk_retry": False,
            "actual_cost_usd": 0.0,
            "secret_values_exposed": False,
        }
        return _sanitize_durable(result)

    @staticmethod
    def _validate_request(payload: dict[str, Any]) -> None:
        _validate_request_hash(payload)
        if (
            payload.get("provider") != "pexels_api"
            or payload.get("operation") != "supporting_asset_acquisition"
        ):
            raise ValueError("MR1_PEXELS_PROVIDER_OR_OPERATION_INVALID")
        if payload.get("scene_id") not in MR1_PEXELS_SCENES:
            raise ValueError("MR1_PEXELS_SCENE_NOT_APPROVED")
        if payload.get("route") != "PEXELS_VIDEO":
            raise ValueError("MR1_PEXELS_ROUTE_CHANGED")
        _validate_common_one_shot(payload)
        exclusions = payload.get("excluded_provider_asset_ids")
        if (
            not isinstance(exclusions, list)
            or len(exclusions) > len(MR1_PEXELS_SCENES) - 1
            or any(
                not isinstance(value, str) or not value.strip() for value in exclusions
            )
            or len(set(exclusions)) != len(exclusions)
        ):
            raise ValueError("MR1_PEXELS_EXCLUDED_PROVIDER_ASSET_IDS_INVALID")
        if not str(payload.get("semantic_intent") or "").strip():
            raise ValueError("MR1_PEXELS_SEMANTIC_INTENT_MISSING")
        stock_search_intent = str(
            payload.get("stock_search_intent")
            or payload.get("semantic_intent")
            or ""
        ).strip()
        if not stock_search_intent:
            raise ValueError("MR1_PEXELS_STOCK_SEARCH_INTENT_MISSING")
        if payload.get("stock_search_intent") is not None and (
            payload.get("stock_search_intent_scope")
            != "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
        ):
            raise ValueError("MR1_PEXELS_STOCK_SEARCH_INTENT_SCOPE_INVALID")
        semantic_fit_threshold = payload.get("semantic_fit_threshold")
        if (
            isinstance(semantic_fit_threshold, bool)
            or not isinstance(semantic_fit_threshold, (int, float))
            or not 0 < float(semantic_fit_threshold) <= 1
            or payload.get("semantic_fit_threshold_authority")
            != (
                "frozen_channel_policy.provider_usage_policy.pexels."
                "semantic_fit_threshold"
            )
        ):
            raise ValueError("MR1_PEXELS_SEMANTIC_THRESHOLD_INVALID")
        forbidden_true = (
            "automatic_pexels_to_ai_fallback",
            "provider_substitution_allowed",
            "generated_evidence_authority",
        )
        if any(payload.get(key) is not False for key in forbidden_true):
            raise ValueError("MR1_PEXELS_FALLBACK_OR_EVIDENCE_AUTHORITY_FORBIDDEN")
        if payload.get("observable_reality_support_only") is not True:
            raise ValueError("MR1_PEXELS_SUPPORTING_ROLE_CHANGED")
        if (
            payload.get("target_market") != "US"
            or payload.get("market_context") != "US_SMALL_BUSINESS"
        ):
            raise ValueError("MR1_PEXELS_MARKET_BINDING_CHANGED")
        if not _is_sha256(str(payload.get("canonical_timeline_hash") or "")):
            raise ValueError("MR1_PEXELS_CANONICAL_TIMELINE_HASH_INVALID")
        if payload.get("timing_authority") != "CANONICAL_MEDIA_TIMELINE":
            raise ValueError("MR1_PEXELS_TIMING_AUTHORITY_INVALID")
        if payload.get("estimated_timing_fallback_used") is not False:
            raise ValueError("MR1_PEXELS_ESTIMATED_TIMING_FALLBACK_FORBIDDEN")
        start_ms = payload.get("scene_start_ms")
        end_ms = payload.get("scene_end_ms")
        duration_ms = payload.get("scene_duration_ms")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (start_ms, end_ms, duration_ms)
        ):
            raise ValueError("MR1_PEXELS_CANONICAL_SCENE_TIMING_INVALID")
        if (
            start_ms < 0
            or end_ms <= start_ms
            or duration_ms <= 0
            or end_ms - start_ms != duration_ms
        ):
            raise ValueError("MR1_PEXELS_CANONICAL_SCENE_TIMING_INVALID")
        stock_start_ms = payload.get("stock_context_start_ms")
        stock_end_ms = payload.get("stock_context_end_ms")
        stock_duration_ms = payload.get("stock_context_duration_ms")
        native_start_ms = payload.get("native_explanation_start_ms")
        native_end_ms = payload.get("native_explanation_end_ms")
        native_duration_ms = payload.get("native_explanation_duration_ms")
        subwindow_values = (
            stock_start_ms,
            stock_end_ms,
            stock_duration_ms,
            native_start_ms,
            native_end_ms,
            native_duration_ms,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in subwindow_values
        ):
            raise ValueError("MR1_PEXELS_SUPPORTING_SUBWINDOW_INVALID")
        if (
            stock_start_ms != start_ms
            or stock_end_ms != native_start_ms
            or native_end_ms != end_ms
            or stock_end_ms - stock_start_ms != stock_duration_ms
            or native_end_ms - native_start_ms != native_duration_ms
            or stock_duration_ms <= 0
            or native_duration_ms <= 0
            or stock_duration_ms + native_duration_ms != duration_ms
            or not _is_sha256(
                str(payload.get("supporting_visual_subwindows_hash") or "")
            )
            or not str(payload.get("native_mechanism") or "").strip()
            or not str(payload.get("supporting_subwindow_policy_ref") or "").strip()
        ):
            raise ValueError("MR1_PEXELS_SUPPORTING_SUBWINDOW_INVALID")
        try:
            minimum_seconds = float(payload["minimum_duration_seconds"])
            maximum_seconds = float(payload["maximum_duration_seconds"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("MR1_PEXELS_APPROVED_DURATION_RANGE_MISSING") from None
        required_whole_seconds = float(math.ceil(stock_duration_ms / 1000))
        if (
            not math.isfinite(minimum_seconds)
            or not math.isfinite(maximum_seconds)
            or minimum_seconds != required_whole_seconds
            or maximum_seconds < minimum_seconds
            or maximum_seconds > 120
        ):
            raise ValueError("MR1_PEXELS_APPROVED_DURATION_RANGE_INVALID")


class MR1DriveGatewayAdapter:
    def __init__(
        self,
        *,
        service: MR1DriveArchiveService,
        settings: Settings,
        workspace_root: Path,
    ) -> None:
        self.service = service
        self.settings = settings
        self.workspace_root = Path(workspace_root).resolve()
        provider = _BoundaryDriveProvider(service.provider)
        service.provider = provider
        self._provider = provider

    def preflight(self, **_: Any) -> dict[str, Any]:
        checks = {
            "drive_offload_enabled": self.settings.google_drive_offload_enabled is True,
            "drive_real_archive_enabled": self.settings.google_drive_real_archive_enabled
            is True,
            "drive_archive_enabled": self.settings.google_drive_archive_enabled is True,
            "configured_root_present": bool(self.service.root_folder_id),
            "oauth_resolver_configured": self.service.access_token_resolver is not None,
            "upload_mode_exact": self.service.upload_mode in {"multipart", "resumable"},
            "source_workspace_contained": self.service.source_root
            == self.workspace_root,
            "upload_and_publish_kill_switch_closed": self.settings.upload_and_publish_disabled
            is True,
            "youtube_not_reachable": True,
            "billable_probe_disabled": True,
        }
        return _preflight_result("GOOGLE_DRIVE_ARCHIVE", checks)

    def verify_read_only_root(
        self, *, access_token: str | None = None
    ) -> dict[str, Any]:
        """Expose the real, mutation-free Drive root readiness evidence."""

        return self.service.read_only_root_readiness(access_token=access_token)

    def upload_or_resume_and_verify(
        self,
        manifest: Mapping[str, Any],
        *,
        archive_identity: str,
        journal_path: Path,
        before_first_mutation: Callable[[], None],
    ) -> dict[str, Any]:
        readiness = self.preflight()
        if readiness["result"] != "PASS":
            raise RuntimeError(
                "MR1_DRIVE_PREFLIGHT_FAILED:" + ",".join(readiness["failed_checks"])
            )
        payload = dict(manifest)
        if payload.get("schema_version") != "mr1.local-archive-manifest.v1":
            raise ValueError("MR1_DRIVE_LOCAL_MANIFEST_SCHEMA_INVALID")
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id or payload.get("archive_identity") != archive_identity:
            raise ValueError("MR1_DRIVE_ARCHIVE_IDENTITY_CHANGED")
        if archive_identity != f"mr1-archive://small-team-ai/{run_id}":
            raise ValueError("MR1_DRIVE_ARCHIVE_IDENTITY_NOT_CANONICAL")
        journal = _validated_destination(
            Path(journal_path),
            requested=str(journal_path),
            workspace_root=self.workspace_root,
            must_be_fresh=False,
        )
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("MR1_DRIVE_ARCHIVE_FILES_EMPTY")
        review_round = payload.get("review_round", 1)
        if (
            isinstance(review_round, bool)
            or not isinstance(review_round, int)
            or review_round < 1
            or review_round > 9_999
        ):
            raise ValueError("MR1_DRIVE_REVIEW_ROUND_INVALID")
        if payload.get("item_count") not in {None, len(files)}:
            raise ValueError("MR1_DRIVE_ARCHIVE_ITEM_COUNT_CHANGED")
        if payload.get("item_set_hash") not in {
            None,
            content_hash({"files": files}),
        }:
            raise ValueError("MR1_DRIVE_ARCHIVE_ITEM_SET_HASH_CHANGED")
        items = _archive_items(files, source_root=self.workspace_root)
        total_size = sum(item.size_bytes for item in items)
        if payload.get("total_size_bytes") not in {None, total_size}:
            raise ValueError("MR1_DRIVE_ARCHIVE_TOTAL_SIZE_CHANGED")
        revision_label = f"r{review_round:04d}"
        service_execution_run_id = f"{run_id}.{revision_label}"
        root_relative_path = f"small-team-ai/mr1/{run_id}/revisions/{revision_label}"
        revision_journal = _validated_destination(
            journal.with_name(
                f"{journal.stem}-{revision_label}{journal.suffix or '.json'}"
            ),
            requested=str(
                journal.with_name(
                    f"{journal.stem}-{revision_label}{journal.suffix or '.json'}"
                )
            ),
            workspace_root=self.workspace_root,
            must_be_fresh=False,
        )
        adapter_journal = {
            "schema_version": "mr1.drive-gateway-journal.v1",
            "run_id": run_id,
            "service_execution_run_id": service_execution_run_id,
            "archive_identity": archive_identity,
            "review_round": review_round,
            "root_relative_path": root_relative_path,
            "item_count": len(items),
            "total_size_bytes": total_size,
            "item_sha256": [item.sha256 for item in items],
            "item_set_hash": content_hash(
                {
                    "items": [
                        {
                            "logical_role": item.logical_role,
                            "name": item.name,
                            "archive_path": item.archive_path,
                            "size_bytes": item.size_bytes,
                            "sha256": item.sha256,
                            "md5": item.md5,
                        }
                        for item in items
                    ]
                }
            ),
            "service_journal_ref": str(
                self.service.journal_path(service_execution_run_id)
            ),
            "state": "READY_TO_RESUME_OR_MUTATE",
        }
        if revision_journal.exists():
            prior = _read_json_object(revision_journal)
            immutable = {
                key: value for key, value in adapter_journal.items() if key != "state"
            }
            if any(prior.get(key) != value for key, value in immutable.items()):
                raise RuntimeError("MR1_DRIVE_GATEWAY_REVISION_JOURNAL_CONFLICT")
        else:
            _write_json_atomic(revision_journal, adapter_journal)
        _update_drive_gateway_index(
            journal,
            run_id=run_id,
            archive_identity=archive_identity,
            review_round=review_round,
            revision_journal=revision_journal,
            adapter_journal=adapter_journal,
            state="READY_TO_RESUME_OR_MUTATE",
        )

        self._provider.arm(before_first_mutation)
        try:
            if (
                self.service.journal_path(service_execution_run_id).is_file()
                or self.service.receipt_path(service_execution_run_id).is_file()
            ):
                # A prior attempt may need only remote readback reconciliation,
                # with no second mutation.  The service ledger still requires
                # its one resume boundary before touching the remote archive.
                self._provider.declare_resume_boundary()
            receipt = self.service.upload_and_verify(
                run_id=service_execution_run_id,
                archive_identity=archive_identity,
                root_relative_path=root_relative_path,
                items=items,
            )
            if self._provider.mutation_boundary_count != 1:
                raise RuntimeError("MR1_DRIVE_MUTATION_BOUNDARY_NOT_DECLARED")
            normalized = _normalize_drive_receipt(
                receipt,
                journal_path=journal,
                service_journal_path=self.service.journal_path(
                    service_execution_run_id
                ),
                canonical_run_id=run_id,
                service_execution_run_id=service_execution_run_id,
                review_round=review_round,
            )
            if normalized["ARCHIVE_VERIFIED"] is not True:
                mismatch_codes = sorted(
                    {
                        *list(normalized.get("mismatch_reason_codes") or []),
                        *list(
                            normalized.get("normalization_mismatch_reason_codes") or []
                        ),
                    }
                )
                raise RuntimeError(
                    "MR1_DRIVE_ARCHIVE_VERIFICATION_FAILED:" + ",".join(mismatch_codes)
                )
            verified_journal = {
                **adapter_journal,
                "state": "VERIFIED",
                "receipt_hash": normalized["receipt_hash"],
            }
            _write_json_atomic(revision_journal, verified_journal)
            _update_drive_gateway_index(
                journal,
                run_id=run_id,
                archive_identity=archive_identity,
                review_round=review_round,
                revision_journal=revision_journal,
                adapter_journal=verified_journal,
                state="VERIFIED",
                receipt_hash=normalized["receipt_hash"],
            )
            return normalized
        finally:
            # The adapter object is reused by the runner for a repairable Drive
            # resume.  Invocation-local callback state must never leak across it.
            self._provider.disarm()

    def upload_finalization_supplement_and_verify(
        self,
        manifest: Mapping[str, Any],
        *,
        archive_identity: str,
        journal_path: Path,
        before_first_mutation: Callable[[], None],
    ) -> dict[str, Any]:
        """Archive post-watch receipts without reopening the review archive.

        The canonical review-round folder remains an immutable exact set.  The
        two post-watch artifacts are uploaded to an independently journaled,
        exact-set ``finalization`` child folder and verified from Drive before
        the caller may register FinalMediaRef.
        """

        readiness = self.preflight()
        if readiness["result"] != "PASS":
            raise RuntimeError(
                "MR1_DRIVE_PREFLIGHT_FAILED:" + ",".join(readiness["failed_checks"])
            )
        payload = dict(manifest)
        if (
            payload.get("schema_version")
            != "mr1.finalization-archive-supplement-manifest.v1"
        ):
            raise ValueError("MR1_DRIVE_FINALIZATION_MANIFEST_SCHEMA_INVALID")
        run_id = str(payload.get("run_id") or "").strip()
        if (
            not run_id
            or payload.get("archive_identity") != archive_identity
            or archive_identity != f"mr1-archive://small-team-ai/{run_id}"
        ):
            raise ValueError("MR1_DRIVE_FINALIZATION_ARCHIVE_IDENTITY_CHANGED")
        review_round = payload.get("review_round")
        if (
            isinstance(review_round, bool)
            or not isinstance(review_round, int)
            or not 1 <= review_round <= 9_999
        ):
            raise ValueError("MR1_DRIVE_REVIEW_ROUND_INVALID")
        canonical_receipt = payload.get("canonical_drive_archive_receipt")
        if (
            not isinstance(canonical_receipt, Mapping)
            or not canonical_receipt.get("artifact_version_id")
            or not _is_sha256(str(canonical_receipt.get("content_hash") or ""))
        ):
            raise ValueError("MR1_DRIVE_FINALIZATION_CANONICAL_RECEIPT_MISSING")
        if payload.get("drive_phase_authority") != {
            "phase": "FINALIZATION_SUPPLEMENT",
            "operation_key": MR1_DRIVE_FINALIZATION_OPERATION_KEY,
            "boundary": "POST_HUMAN_PASS_PRE_FINAL_MEDIA_REF",
            "max_mutations": 1,
            "cost_usd": 0.0,
        }:
            raise ValueError("MR1_DRIVE_FINALIZATION_PHASE_AUTHORITY_INVALID")
        idempotency = payload.get("idempotency_identity") or {}
        expected_idempotency_key = mr1_drive_finalization_idempotency_key(
            run_id=run_id,
            review_round=review_round,
        )
        if (
            set(idempotency)
            != {
                "operation_key",
                "idempotency_key",
                "idempotency_fingerprint",
                "review_round",
                "distinct_from_canonical_archive",
                "automatic_retry_allowed",
            }
            or idempotency.get("operation_key") != MR1_DRIVE_FINALIZATION_OPERATION_KEY
            or idempotency.get("idempotency_key") != expected_idempotency_key
            or not _is_sha256(str(idempotency.get("idempotency_fingerprint") or ""))
            or idempotency.get("review_round") != review_round
            or idempotency.get("distinct_from_canonical_archive") is not True
            or idempotency.get("automatic_retry_allowed") is not False
        ):
            raise ValueError("MR1_DRIVE_FINALIZATION_IDEMPOTENCY_INVALID")
        files = payload.get("files")
        if not isinstance(files, list) or len(files) != 2:
            raise ValueError("MR1_DRIVE_FINALIZATION_FILES_INVALID")
        items = _archive_items(files, source_root=self.workspace_root)
        if {item.logical_role for item in items} != {
            "MR1_HUMAN_FULL_WATCH_RECEIPT",
            "MR1_FINAL_MEDIA_LINEAGE_RECEIPT",
        }:
            raise ValueError("MR1_DRIVE_FINALIZATION_ROLES_INVALID")
        total_size = sum(item.size_bytes for item in items)
        if (
            payload.get("item_count") != len(items)
            or payload.get("total_size_bytes") != total_size
            or payload.get("item_set_hash") != content_hash({"files": files})
        ):
            raise ValueError("MR1_DRIVE_FINALIZATION_ITEM_SET_CHANGED")

        requested_journal = Path(journal_path)
        phase_journal = requested_journal.with_name(
            f"{requested_journal.stem}-r{review_round:04d}-finalization"
            f"{requested_journal.suffix or '.json'}"
        )
        phase_journal = _validated_destination(
            phase_journal,
            requested=str(phase_journal),
            workspace_root=self.workspace_root,
            must_be_fresh=False,
        )
        service_execution_run_id = f"{run_id}.r{review_round:04d}.finalization"
        root_relative_path = (
            f"small-team-ai/mr1/{run_id}/revisions/r{review_round:04d}/finalization"
        )
        phase_core = {
            "schema_version": "mr1.drive-finalization-gateway-journal.v1",
            "run_id": run_id,
            "service_execution_run_id": service_execution_run_id,
            "archive_identity": archive_identity,
            "review_round": review_round,
            "root_relative_path": root_relative_path,
            "canonical_drive_archive_receipt": deepcopy(dict(canonical_receipt)),
            "idempotency_identity": deepcopy(dict(idempotency)),
            "item_count": len(items),
            "total_size_bytes": total_size,
            "item_set_hash": payload["item_set_hash"],
            "manifest_hash": content_hash(payload),
        }
        if phase_journal.exists():
            prior = _read_json_object(phase_journal)
            if any(prior.get(key) != value for key, value in phase_core.items()):
                raise RuntimeError("MR1_DRIVE_FINALIZATION_JOURNAL_CONFLICT")
        else:
            _write_json_atomic(
                phase_journal,
                {**phase_core, "state": "READY_TO_RESUME_OR_MUTATE"},
            )

        self._provider.arm(before_first_mutation)
        try:
            if (
                self.service.journal_path(service_execution_run_id).is_file()
                or self.service.receipt_path(service_execution_run_id).is_file()
            ):
                self._provider.declare_resume_boundary()
            receipt = self.service.upload_and_verify(
                run_id=service_execution_run_id,
                archive_identity=archive_identity,
                root_relative_path=root_relative_path,
                items=items,
            )
            if self._provider.mutation_boundary_count != 1:
                raise RuntimeError(
                    "MR1_DRIVE_FINALIZATION_MUTATION_BOUNDARY_NOT_DECLARED"
                )
            normalized = _normalize_drive_receipt(
                receipt,
                journal_path=phase_journal,
                service_journal_path=self.service.journal_path(
                    service_execution_run_id
                ),
                canonical_run_id=run_id,
                service_execution_run_id=service_execution_run_id,
                review_round=review_round,
            )
            if normalized.get("ARCHIVE_VERIFIED") is not True:
                raise RuntimeError("MR1_DRIVE_FINALIZATION_VERIFICATION_FAILED")
            result = {
                **normalized,
                "archive_phase": "FINALIZATION_SUPPLEMENT",
                "canonical_drive_archive_receipt": deepcopy(dict(canonical_receipt)),
                "supplement_manifest_hash": content_hash(payload),
                "supplement_item_set_hash": payload["item_set_hash"],
            }
            _write_json_atomic(
                phase_journal,
                {
                    **phase_core,
                    "state": "VERIFIED",
                    "receipt_hash": normalized["receipt_hash"],
                },
            )
            return result
        finally:
            self._provider.disarm()


def build_mr1_production_gateways(
    *,
    session: Session,
    settings: Settings,
    workspace_root: Path,
) -> MR1ProviderGateways:
    """Build the four real MR1 boundaries without making a network request."""

    root = Path(workspace_root).resolve()
    elevenlabs_key = _secret_value(settings.elevenlabs_api_key)
    pexels_key = _secret_value(settings.pexels_api_key)
    drive_service = MR1DriveArchiveService.from_existing_configuration(
        session=session,
        settings=settings,
        source_root=root,
        state_root=root / ".drive-state",
    )
    return MR1ProviderGateways(
        narration=MR1NarrationGatewayAdapter(
            api_key=elevenlabs_key,
            settings=settings,
            workspace_root=root,
        ),
        alignment=MR1AlignmentGatewayAdapter(
            api_key=elevenlabs_key,
            settings=settings,
            workspace_root=root,
        ),
        pexels=MR1PexelsGatewayAdapter(
            api_key=pexels_key,
            settings=settings,
            workspace_root=root,
        ),
        drive=MR1DriveGatewayAdapter(
            service=drive_service,
            settings=settings,
            workspace_root=root,
        ),
    )


def _temporal_normalized(payload: Mapping[str, Any]) -> SpokenTextNormalized:
    text = str(payload.get("normalized_text") or "")
    if not text:
        raise ValueError("MR1_NORMALIZED_TEXT_MISSING")
    tokens: list[SpokenToken] = []
    token_pattern = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
    for index, match in enumerate(token_pattern.finditer(text), start=1):
        span = TextSpan(start=match.start(), end=match.end())
        tokens.append(
            SpokenToken(
                token_id=f"spoken-{index:04d}",
                text=match.group(0),
                spoken_span=span,
                source_spans=[span],
                normalization_operation_ids=[],
                comparison_key=_comparison_key(match.group(0)),
            )
        )
    if not tokens:
        raise ValueError("MR1_NORMALIZED_TEXT_TOKENS_MISSING")
    text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    core = {
        "normalization_version": "mr1-approved-spoken-text-identity/v1",
        "script_revision_id": str(
            payload.get("spoken_text_artifact_version_id")
            or payload.get("script_artifact_version_id")
            or "mr1-approved-spoken-text"
        ),
        "source_text_hash": text_sha256,
        "source_character_count": len(text),
        "spoken_text": text,
        "spoken_text_hash": text_sha256,
        "spoken_character_count": len(text),
        "normalization_operations": [],
        "source_to_spoken_spans": [
            SourceToSpokenSpan(
                source_span=TextSpan(start=0, end=len(text)),
                spoken_span=TextSpan(start=0, end=len(text)),
                operation_ids=[],
            ).model_dump(mode="json")
        ],
        "spoken_tokens": [item.model_dump(mode="json") for item in tokens],
        "pronunciation_dictionary_refs": list(
            payload.get("pronunciation_dictionary_refs") or []
        ),
        "normalization_warnings": [],
    }
    normalized = SpokenTextNormalized(**core, content_hash=content_hash(core))
    if normalized.spoken_text != text:
        raise RuntimeError("MR1_APPROVED_PROVIDER_TEXT_CHANGED")
    return normalized


def _validate_normalized_text(payload: Mapping[str, Any]) -> None:
    text = str(payload.get("normalized_text") or "")
    if not text.strip():
        raise ValueError("MR1_NORMALIZED_TEXT_MISSING")
    if content_hash({"normalized_text": text}) != payload.get("normalized_text_hash"):
        raise ValueError("MR1_NORMALIZED_TEXT_HASH_CHANGED")


def _validate_request_hash(payload: Mapping[str, Any]) -> None:
    supplied = str(payload.get("request_hash") or "")
    core = {key: value for key, value in payload.items() if key != "request_hash"}
    if not _is_sha256(supplied) or content_hash(core) != supplied:
        raise ValueError("MR1_PROVIDER_REQUEST_HASH_INVALID")


def _validate_common_one_shot(payload: Mapping[str, Any]) -> None:
    if int(payload.get("attempt_cap") or 0) != 1:
        raise ValueError("MR1_PROVIDER_ATTEMPT_CAP_CHANGED")
    if payload.get("sdk_retry") is not False:
        raise ValueError("MR1_PROVIDER_SDK_RETRY_FORBIDDEN")
    if not str(payload.get("approval_id") or "").strip():
        raise ValueError("MR1_PROVIDER_APPROVAL_BINDING_MISSING")
    if not str(payload.get("idempotency_key") or "").startswith("mr1:"):
        raise ValueError("MR1_PROVIDER_IDEMPOTENCY_KEY_INVALID")
    if not _is_sha256(str(payload.get("idempotency_fingerprint") or "")):
        raise ValueError("MR1_PROVIDER_IDEMPOTENCY_FINGERPRINT_INVALID")


def _archive_items(values: list[Any], *, source_root: Path) -> list[MR1ArchiveItem]:
    items: list[MR1ArchiveItem] = []
    roles: set[str] = set()
    for index, raw in enumerate(values, start=1):
        if not isinstance(raw, Mapping):
            raise TypeError("MR1_DRIVE_ARCHIVE_SOURCE_INVALID")
        role = str(raw.get("logical_role") or raw.get("role") or "").strip()
        if not role or role.casefold() in roles:
            raise ValueError("MR1_DRIVE_ARCHIVE_ROLE_INVALID_OR_DUPLICATE")
        roles.add(role.casefold())
        source_value = (
            raw.get("source_path") or raw.get("local_path") or raw.get("path")
        )
        source = _validated_existing_file(Path(str(source_value or "")), source_root)
        role_component = _safe_component(role)
        name = source.name
        requested_archive_path = raw.get("archive_path") or raw.get(
            "expected_archive_path"
        )
        archive_path = (
            str(requested_archive_path)
            if requested_archive_path
            else f"items/{role_component}/{index:03d}-{role_component}-{name}"
        )
        item = MR1ArchiveItem.from_path(
            logical_role=role,
            source_path=source,
            archive_path=archive_path,
            name=PurePosixPath(archive_path).name,
        )
        if raw.get("sha256") not in {None, item.sha256}:
            raise RuntimeError(f"MR1_DRIVE_SOURCE_HASH_CHANGED:{role}")
        if raw.get("md5") not in {None, item.md5}:
            raise RuntimeError(f"MR1_DRIVE_SOURCE_MD5_CHANGED:{role}")
        if raw.get("size_bytes") not in {None, item.size_bytes}:
            raise RuntimeError(f"MR1_DRIVE_SOURCE_SIZE_CHANGED:{role}")
        if raw.get("name") not in {None, item.name}:
            raise RuntimeError(f"MR1_DRIVE_SOURCE_NAME_CHANGED:{role}")
        items.append(item)
    return items


def _update_drive_gateway_index(
    journal_path: Path,
    *,
    run_id: str,
    archive_identity: str,
    review_round: int,
    revision_journal: Path,
    adapter_journal: Mapping[str, Any],
    state: str,
    receipt_hash: str | None = None,
) -> None:
    existing: dict[str, Any] = {}
    if journal_path.exists():
        existing = _read_json_object(journal_path)
        if existing.get("schema_version") != "mr1.drive-gateway-index.v1":
            raise RuntimeError("MR1_DRIVE_GATEWAY_INDEX_SCHEMA_CONFLICT")
        if (
            existing.get("run_id") != run_id
            or existing.get("archive_identity") != archive_identity
        ):
            raise RuntimeError("MR1_DRIVE_GATEWAY_INDEX_IDENTITY_CONFLICT")
    raw_revisions = existing.get("revisions") or {}
    if not isinstance(raw_revisions, Mapping):
        raise RuntimeError("MR1_DRIVE_GATEWAY_INDEX_REVISIONS_INVALID")
    revisions = deepcopy(dict(raw_revisions))
    revision_key = f"{review_round:04d}"
    revision_record = {
        "review_round": review_round,
        "service_execution_run_id": adapter_journal["service_execution_run_id"],
        "root_relative_path": adapter_journal["root_relative_path"],
        "revision_journal_ref": str(revision_journal),
        "service_journal_ref": adapter_journal["service_journal_ref"],
        "item_count": adapter_journal["item_count"],
        "total_size_bytes": adapter_journal["total_size_bytes"],
        "item_set_hash": adapter_journal["item_set_hash"],
        "state": state,
    }
    if receipt_hash is not None:
        revision_record["receipt_hash"] = receipt_hash
    prior = revisions.get(revision_key)
    if prior is not None:
        if not isinstance(prior, Mapping):
            raise RuntimeError("MR1_DRIVE_GATEWAY_INDEX_REVISION_INVALID")
        immutable_keys = {
            "review_round",
            "service_execution_run_id",
            "root_relative_path",
            "revision_journal_ref",
            "service_journal_ref",
            "item_count",
            "total_size_bytes",
            "item_set_hash",
        }
        if any(prior.get(key) != revision_record[key] for key in immutable_keys):
            raise RuntimeError("MR1_DRIVE_GATEWAY_INDEX_REVISION_CONFLICT")
    revisions[revision_key] = revision_record
    _write_json_atomic(
        journal_path,
        {
            "schema_version": "mr1.drive-gateway-index.v1",
            "run_id": run_id,
            "archive_identity": archive_identity,
            "active_review_round": review_round,
            "active_revision_key": revision_key,
            "revision_count": len(revisions),
            "revisions": revisions,
            "state": state,
            **({"receipt_hash": receipt_hash} if receipt_hash is not None else {}),
        },
    )


def _normalize_drive_receipt(
    receipt: Mapping[str, Any],
    *,
    journal_path: Path,
    service_journal_path: Path,
    canonical_run_id: str | None = None,
    service_execution_run_id: str | None = None,
    review_round: int = 1,
) -> dict[str, Any]:
    result = deepcopy(dict(receipt))
    raw_service_run_id = str(result.get("run_id") or "")
    expected_service_run_id = service_execution_run_id or raw_service_run_id
    resolved_canonical_run_id = canonical_run_id or raw_service_run_id
    execution_identity_ok = bool(
        raw_service_run_id
        and raw_service_run_id == expected_service_run_id
        and resolved_canonical_run_id
    )
    raw_items = result.get("items")
    raw_files = result.get("files")
    items = raw_items if isinstance(raw_items, list) else []
    files = raw_files if isinstance(raw_files, list) else []
    expected = _drive_receipt_int(result.get("expected_item_count"))
    verified = _drive_receipt_int(result.get("verified_item_count"))
    remote = _drive_receipt_int(result.get("remote_item_count"))
    run_folder_id = str(result.get("drive_folder_id") or "").strip()

    item_records_valid = bool(items) and all(
        isinstance(item, Mapping) for item in items
    )
    file_records_valid = bool(files) and all(
        isinstance(item, Mapping) for item in files
    )
    count_fields_exact = bool(
        expected is not None
        and verified is not None
        and remote is not None
        and expected > 0
        and expected == verified == remote == len(items) == len(files)
    )
    ordered_identity_ok = (
        item_records_valid
        and file_records_valid
        and all(
            tuple(
                str(item.get(key) or "")
                for key in ("logical_role", "name", "archive_path")
            )
            == tuple(
                str(file.get(key) or "")
                for key in ("logical_role", "name", "archive_path")
            )
            and all(
                str(item.get(key) or "").strip()
                for key in ("logical_role", "name", "archive_path")
            )
            for item, file in zip(items, files, strict=False)
        )
    )
    archive_paths = [
        str(item.get("archive_path") or "")
        for item in items
        if isinstance(item, Mapping)
    ]
    names = [str(item.get("name") or "") for item in items if isinstance(item, Mapping)]
    roles = [
        str(item.get("logical_role") or "")
        for item in items
        if isinstance(item, Mapping)
    ]
    canonical_item_set_ok = bool(
        item_records_valid
        and archive_paths == sorted(archive_paths)
        and len(set(archive_paths)) == len(items)
        and len(set(names)) == len(items)
        and len({value.casefold() for value in roles}) == len(items)
        and all(
            PurePosixPath(path).name == name
            for path, name in zip(archive_paths, names, strict=False)
        )
    )

    drive_file_ids: list[str] = []
    parent_ok = bool(run_folder_id and file_records_valid)
    names_ok = bool(ordered_identity_ok and canonical_item_set_ok)
    sizes_ok = bool(item_records_valid and file_records_valid)
    checksums_ok = bool(item_records_valid and file_records_valid)
    every_verified = bool(files) and file_records_valid
    normalized_item_set: list[dict[str, Any]] = []
    for item, file in zip(items, files, strict=False):
        if not isinstance(item, Mapping) or not isinstance(file, Mapping):
            parent_ok = names_ok = sizes_ok = checksums_ok = every_verified = False
            continue
        drive_file_id = str(file.get("drive_file_id") or "").strip()
        drive_file_ids.append(drive_file_id)
        parent_match = str(file.get("drive_folder_id") or "").strip() == run_folder_id
        parent_ok = parent_ok and bool(drive_file_id) and parent_match

        item_size = _drive_receipt_int(item.get("size_bytes"))
        local_size = _drive_receipt_int(file.get("local_size_bytes"))
        remote_size = _drive_receipt_int(file.get("remote_size_bytes"))
        size_match = bool(
            item_size is not None
            and item_size >= 0
            and item_size == local_size == remote_size
        )
        sizes_ok = sizes_ok and size_match

        item_sha256 = str(item.get("sha256") or "").lower()
        local_sha256 = str(file.get("local_sha256") or "").lower()
        remote_sha256 = str(file.get("remote_sha256") or "").lower()
        item_md5 = str(item.get("md5") or "").lower()
        local_md5 = str(file.get("local_md5") or "").lower()
        remote_md5 = str(file.get("remote_md5") or "").lower()
        method = str(file.get("verification_method") or "")
        sha256_match = bool(
            _is_sha256(item_sha256) and item_sha256 == local_sha256 == remote_sha256
        )
        md5_match = bool(
            re.fullmatch(r"[0-9a-f]{32}", item_md5)
            and item_md5 == local_md5 == remote_md5
        )
        strong_method_ok = bool(
            (method == "SHA256_PLUS_SIZE" and sha256_match)
            or (method == "MD5_PLUS_SIZE" and md5_match)
        )
        checksum_match = bool(
            _is_sha256(item_sha256)
            and item_sha256 == local_sha256
            and file.get("verified") is True
            and (sha256_match or strong_method_ok)
        )
        checksums_ok = checksums_ok and checksum_match
        every_verified = every_verified and file.get("verified") is True
        normalized_item_set.append(
            {
                "logical_role": str(item.get("logical_role") or ""),
                "name": str(item.get("name") or ""),
                "archive_path": str(item.get("archive_path") or ""),
                "drive_file_id": drive_file_id,
                "drive_folder_id": str(file.get("drive_folder_id") or ""),
                "local_size_bytes": local_size,
                "remote_size_bytes": remote_size,
                "local_sha256": local_sha256,
                "remote_sha256": remote_sha256 or None,
                "verification_method": method,
            }
        )

    duplicate_count = max(
        0,
        (remote or 0) - (expected or 0),
        len(drive_file_ids) - len(set(drive_file_ids)),
        len(items) - len(set(archive_paths)),
        len(items) - len(set(names)),
        len(items) - len({value.casefold() for value in roles}),
    )
    total_local = _drive_receipt_int(result.get("total_local_size_bytes"))
    total_remote = _drive_receipt_int(result.get("total_remote_size_bytes"))
    derived_local_total = sum(
        _drive_receipt_int(item.get("size_bytes")) or 0
        for item in items
        if isinstance(item, Mapping)
    )
    derived_remote_total = sum(
        _drive_receipt_int(item.get("remote_size_bytes")) or 0
        for item in files
        if isinstance(item, Mapping)
    )
    sizes_ok = bool(
        sizes_ok
        and total_local == derived_local_total
        and total_remote == derived_remote_total
        and derived_local_total == derived_remote_total
    )

    manifest_payload = {
        "schema_version": "MR1_DRIVE_ARCHIVE_MANIFEST_V1",
        "run_id": result.get("run_id"),
        "archive_identity": result.get("archive_identity"),
        "root_relative_path": result.get("root_relative_path"),
        "item_count": len(items),
        "total_size_bytes": derived_local_total,
        "items": items,
    }
    supplied_manifest_hash = str(result.get("archive_manifest_hash") or "")
    manifest_hash_verified = bool(
        _is_sha256(supplied_manifest_hash)
        and content_hash(manifest_payload) == supplied_manifest_hash
    )
    supplied_receipt_hash = str(result.get("receipt_hash") or "")
    service_receipt_hash_verified = bool(
        _is_sha256(supplied_receipt_hash)
        and content_hash(
            {key: value for key, value in result.items() if key != "receipt_hash"}
        )
        == supplied_receipt_hash
    )
    mismatch_codes = result.get("mismatch_reason_codes")
    no_reported_mismatch = isinstance(mismatch_codes, list) and not mismatch_codes
    exact = bool(
        result.get("remote_exact_set_verified") is True
        and count_fields_exact
        and ordered_identity_ok
        and canonical_item_set_ok
        and duplicate_count == 0
    )
    archive_verified = bool(
        result.get("archive_state") == "VERIFIED"
        and exact
        and every_verified
        and parent_ok
        and names_ok
        and sizes_ok
        and checksums_ok
        and manifest_hash_verified
        and service_receipt_hash_verified
        and execution_identity_ok
        and no_reported_mismatch
    )
    derived_mismatches: list[str] = []
    for passed, code in (
        (count_fields_exact, "MR1_DRIVE_RECEIPT_COUNT_MISMATCH"),
        (ordered_identity_ok, "MR1_DRIVE_RECEIPT_ORDERED_IDENTITY_MISMATCH"),
        (canonical_item_set_ok, "MR1_DRIVE_RECEIPT_ITEM_SET_NON_CANONICAL"),
        (duplicate_count == 0, "MR1_DRIVE_RECEIPT_DUPLICATE_ITEM"),
        (parent_ok, "MR1_DRIVE_RECEIPT_PARENT_MISMATCH"),
        (names_ok, "MR1_DRIVE_RECEIPT_NAME_OR_PATH_MISMATCH"),
        (sizes_ok, "MR1_DRIVE_RECEIPT_SIZE_MISMATCH"),
        (checksums_ok, "MR1_DRIVE_RECEIPT_CHECKSUM_MISMATCH"),
        (every_verified, "MR1_DRIVE_RECEIPT_ITEM_NOT_VERIFIED"),
        (manifest_hash_verified, "MR1_DRIVE_RECEIPT_MANIFEST_HASH_INVALID"),
        (service_receipt_hash_verified, "MR1_DRIVE_RECEIPT_HASH_INVALID"),
        (execution_identity_ok, "MR1_DRIVE_RECEIPT_EXECUTION_IDENTITY_MISMATCH"),
        (no_reported_mismatch, "MR1_DRIVE_RECEIPT_REPORTED_MISMATCH"),
    ):
        if not passed:
            derived_mismatches.append(code)
    result.update(
        {
            "ARCHIVE_VERIFIED": archive_verified,
            "exact_item_count": expected or 0,
            "actual_item_count": remote or 0,
            "verified_item_count": verified or 0,
            "duplicate_count": duplicate_count,
            "parent_verified": parent_ok,
            "correct_parent": parent_ok,
            "names_verified": names_ok,
            "correct_names": names_ok,
            "sizes_verified": sizes_ok,
            "size_verification": sizes_ok,
            "checksums_verified": checksums_ok,
            "checksum_verification": checksums_ok,
            "ordered_item_identity_verified": ordered_identity_ok,
            "archive_manifest_hash_verified": manifest_hash_verified,
            "service_receipt_hash_verified": service_receipt_hash_verified,
            "service_receipt_hash": supplied_receipt_hash,
            "service_execution_run_id": raw_service_run_id,
            "review_round": review_round,
            "run_id": resolved_canonical_run_id,
            "verified_item_set_hash": content_hash({"items": normalized_item_set}),
            "normalization_mismatch_reason_codes": sorted(derived_mismatches),
            "remote_id_journal_ref": str(journal_path),
            "strong_service_journal_ref": str(service_journal_path),
        }
    )
    canonical_receipt_keys = {
        "schema_version",
        "run_id",
        "archive_identity",
        "archive_manifest_hash",
        "root_relative_path",
        "drive_folder_id",
        "expected_item_count",
        "verified_item_count",
        "remote_item_count",
        "total_local_size_bytes",
        "total_remote_size_bytes",
        "items",
        "files",
        "remote_exact_set_verified",
        "archive_state",
        "mismatch_reason_codes",
        "provider_call_made",
        "transport",
        "verified_at",
    }
    result["receipt_hash"] = content_hash(
        {key: result.get(key) for key in canonical_receipt_keys}
    )
    return _sanitize_durable(result)


def _drive_receipt_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _preflight_result(scope: str, checks: Mapping[str, bool]) -> dict[str, Any]:
    failed = sorted(key for key, value in checks.items() if value is not True)
    return {
        "schema_version": "mr1.real-gateway-preflight.v1",
        "scope": scope,
        "mode": "READ_ONLY_NO_BILLABLE_PROBE",
        "checks": {
            key: ("PASS" if value else "BLOCK") for key, value in checks.items()
        },
        "failed_checks": failed,
        "result": "PASS" if not failed else "BLOCK",
        "provider_calls": 0,
        "billable_generation_probe": False,
        "secret_values_exposed": False,
    }


def _validate_pexels_safe_search_query_authority(
    safe_search: Any,
    approved_query_authority: Mapping[str, Any],
    *,
    expected_query_plan: PexelsQueryPlan,
) -> None:
    if not isinstance(safe_search, Mapping):
        raise RuntimeError("MR1_PEXELS_RETURNED_QUERY_PLAN_CHANGED")
    raw_plan = safe_search.get("query_plan")
    if not isinstance(raw_plan, Mapping):
        raise RuntimeError("MR1_PEXELS_RETURNED_QUERY_PLAN_CHANGED")
    try:
        plan = PexelsQueryPlan.model_validate(dict(raw_plan))
    except (TypeError, ValueError):
        raise RuntimeError(
            "MR1_PEXELS_RETURNED_QUERY_PLAN_CHANGED"
        ) from None
    if (
        plan.model_dump(mode="json")
        != expected_query_plan.model_dump(mode="json")
        or plan.plan_hash != approved_query_authority.get("plan_hash")
    ):
        raise RuntimeError("MR1_PEXELS_RETURNED_QUERY_PLAN_CHANGED")
    returned_plan_authority = {
        "schema_version": "mr1.pexels-query-authority.v1",
        "planner_version": plan.planner_version,
        "locale": plan.locale,
        "endpoint": plan.endpoint,
        "query_family": deepcopy(plan.queries),
        "primary_query": plan.queries[0],
        "orientation": plan.orientation,
        "size_preference": plan.size_preference,
        "per_page": plan.per_page,
        "minimum_resolution": plan.minimum_resolution,
        "preferred_resolution": plan.preferred_resolution,
        "minimum_duration_seconds": plan.minimum_duration_seconds,
        "plan_hash": plan.plan_hash,
    }
    approved_plan_authority = {
        key: approved_query_authority.get(key)
        for key in returned_plan_authority
    }
    if returned_plan_authority != approved_plan_authority:
        raise RuntimeError("MR1_PEXELS_RETURNED_QUERY_PLAN_CHANGED")


def _validated_destination(
    destination: Path,
    *,
    requested: Any,
    workspace_root: Path,
    must_be_fresh: bool,
) -> Path:
    target = Path(destination).resolve()
    if requested is not None and Path(str(requested)).resolve() != target:
        raise ValueError("MR1_PROVIDER_DESTINATION_CHANGED")
    _require_contained(target, workspace_root)
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("MR1_PROVIDER_DESTINATION_SYMLINK_FORBIDDEN")
    if must_be_fresh and (
        target.exists() or target.with_name(target.name + ".part").exists()
    ):
        raise FileExistsError("MR1_PROVIDER_DESTINATION_NOT_FRESH")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _validated_existing_file(path: Path, workspace_root: Path) -> Path:
    resolved = path.resolve(strict=True)
    _require_contained(resolved, workspace_root)
    if not resolved.is_file() or resolved.is_symlink() or resolved.stat().st_size <= 0:
        raise ValueError("MR1_LOCAL_SOURCE_FILE_INVALID")
    return resolved


def _require_contained(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError("MR1_WORKSPACE_PATH_ESCAPE") from None


def _probe_duration_ms(path: Path) -> int:
    from app.services.pa1r import media_duration_seconds, probe_media

    return round(media_duration_seconds(probe_media(path)) * 1000)


def _duration_ms_from_probe(probe: Mapping[str, Any]) -> int:
    from app.services.pa1r import media_duration_seconds

    return round(media_duration_seconds(dict(probe)) * 1000)


def _settings_equal(actual: Any, expected: Mapping[str, Any]) -> bool:
    if not isinstance(actual, Mapping) or set(actual) != set(expected):
        return False
    for key, value in expected.items():
        candidate = actual[key]
        if isinstance(value, bool):
            if candidate is not value:
                return False
        elif abs(float(candidate) - float(value)) > 1e-9:
            return False
    return True


def _numeric_at_least(value: Any, floor: float) -> bool:
    try:
        return value is not None and float(value) >= floor
    except (TypeError, ValueError):
        return False


def _sanitize_durable(value: Any) -> Any:
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = key.casefold()
            if normalized in {
                "api_key",
                "authorization",
                "xi-api-key",
                "raw_media_url",
                "download_url",
                "video_files",
                "access_token",
                "refresh_token",
            }:
                continue
            safe[key] = _sanitize_durable(raw_value)
        return safe
    if isinstance(value, (list, tuple)):
        return [_sanitize_durable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str) and value.lower().startswith(("http://", "https://")):
        parsed = urllib.parse.urlsplit(value)
        if parsed.query or parsed.fragment:
            return "volatile-url-sha256:" + hashlib.sha256(value.encode()).hexdigest()
    return value


def _safe_component(value: str) -> str:
    normalized = _SAFE_COMPONENT.sub("-", value.strip().casefold()).strip("-._")
    if not normalized:
        raise ValueError("MR1_DRIVE_ARCHIVE_ROLE_COMPONENT_INVALID")
    return normalized[:80]


def _wordlike(value: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9]", value))


def _comparison_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value).strip()


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("MR1_JSON_OBJECT_REQUIRED")
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(_sanitize_durable(payload), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
