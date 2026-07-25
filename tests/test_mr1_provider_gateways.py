from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.config_registry import content_hash
from app.services.cqr1_real_provider import (
    ElevenLabsConvertWithTimestampsClient,
    ElevenLabsForcedAlignmentClient,
)
from app.services.m10_5 import GoogleDriveUploadResult
from app.services.mr1_drive_archive import MR1DriveArchiveService
from app.services.mr1_pexels_authority import (
    build_mr1_pexels_query_authority,
)
from app.services.mr1_provider_gateways import (
    MR1AlignmentGatewayAdapter,
    MR1DriveGatewayAdapter,
    MR1NarrationGatewayAdapter,
    MR1PexelsGatewayAdapter,
    MR1_MODEL_ID,
    MR1_VOICE_ID,
    MR1_VOICE_SETTINGS,
    _normalize_drive_receipt,
)
from app.services.pexels_query_planner import PexelsQueryPlanner
from app.services.mr1_real_production import (
    MR1_DRIVE_FINALIZATION_OPERATION_KEY,
    MR1RealProductionService,
    mr1_drive_finalization_idempotency_key,
)
from app.services.temporal_authority import SpokenTextNormalizer


def _settings(**updates):
    values = {
        "provider_real_execution_enabled": True,
        "provider_production_execution_enabled": True,
        "media_provider_calls_disabled": False,
        "elevenlabs_real_execution_enabled": True,
        "elevenlabs_real_generation_enabled": True,
        "elevenlabs_forced_alignment_permission_confirmed": True,
        "elevenlabs_voice_id": MR1_VOICE_ID,
        "elevenlabs_model_id": MR1_MODEL_ID,
        "pexels_real_execution_enabled": True,
        "pexels_real_search_enabled": True,
        "pexels_max_clips_per_long": 3,
        "budget_mode": "hard_env",
        "monthly_ai_budget_usd": 1,
        "elevenlabs_monthly_cap_usd": 1,
        "stock_monthly_budget_usd": 0,
        "google_drive_offload_enabled": True,
        "google_drive_archive_enabled": True,
        "google_drive_real_archive_enabled": True,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def _with_hash(core):
    return {**core, "request_hash": content_hash(core)}


class _JSONTransport:
    def __init__(self, response, events):
        self.response = response
        self.events = events
        self.calls = 0
        self.last_kwargs = None

    def json_request(self, *args, **kwargs):
        self.events.append("network")
        self.calls += 1
        self.last_kwargs = kwargs
        return self.response, {"x-request-id": "safe-request"}


class _MultipartTransport:
    def __init__(self, response, events):
        self.response = response
        self.events = events
        self.calls = 0

    def multipart_json_request(self, *args, **kwargs):
        self.events.append("network")
        self.calls += 1
        return self.response, {"x-request-id": "safe-forced-request"}


def _tts_response(text: str):
    characters = list(text)
    step = 2 / (len(characters) + 1)
    alignment = {
        "characters": characters,
        "character_start_times_seconds": [
            index * step for index in range(len(characters))
        ],
        "character_end_times_seconds": [
            (index + 0.8) * step for index in range(len(characters))
        ],
    }
    return {
        "audio_base64": base64.b64encode(b"real-boundary-fake-audio").decode(),
        "alignment": alignment,
        "normalized_alignment": alignment,
        "request_id": "safe-tts-request",
        "usage": {"characters": len(text), "secret_token": "drop-me"},
    }


def _narration_request(destination: Path, text: str):
    core = {
        "provider": "elevenlabs",
        "operation": "narration",
        "script_artifact_version_id": "script-av",
        "script_hash": "a" * 64,
        "spoken_text_artifact_version_id": "spoken-av",
        "spoken_text_hash": "b" * 64,
        "normalized_text_hash": content_hash({"normalized_text": text}),
        "normalized_text": text,
        "voice_policy_artifact_version_id": "voice-av",
        "voice_policy_content_hash": "c" * 64,
        "voice_id": MR1_VOICE_ID,
        "model_id": MR1_MODEL_ID,
        "voice_settings": dict(MR1_VOICE_SETTINGS),
        "language": "en",
        "narration_locale": "en-US",
        "approval_id": "approval-id",
        "approval_content_hash": "d" * 64,
        "approval_ref": "mr1-approval://small-team-ai/exact",
        "cost_snapshot_ref": "cost-av",
        "idempotency_key": "mr1:run:elevenlabs:narration",
        "idempotency_fingerprint": "e" * 64,
        "destination": str(destination),
        "attempt_cap": 1,
        "sdk_retry": False,
    }
    return _with_hash(core)


def test_narration_adapter_declares_boundary_immediately_before_one_fake_transport(
    tmp_path,
):
    text = "AI saves 20 hours."
    events = []
    transport = _JSONTransport(_tts_response(text), events)
    client = ElevenLabsConvertWithTimestampsClient(
        transport,
        media_probe=lambda _: {"format": {"duration": "2.0"}},
    )
    gateway = MR1NarrationGatewayAdapter(
        api_key="secret-never-durable",
        settings=_settings(),
        workspace_root=tmp_path,
        client=client,
    )
    destination = tmp_path / "run" / "narration.mp3"

    result = gateway.execute_once(
        _narration_request(destination, text),
        destination=destination,
        before_submit=lambda: events.append("boundary"),
    )

    assert events == ["boundary", "network"]
    assert transport.calls == 1
    assert transport.last_kwargs["payload"]["text"] == text
    assert transport.last_kwargs["payload"]["apply_text_normalization"] == "off"
    assert (
        result["audio_sha256"] == hashlib.sha256(destination.read_bytes()).hexdigest()
    )
    assert result["network_submit_count"] == 1
    serialized = json.dumps(result)
    assert "secret-never-durable" not in serialized and "drop-me" not in serialized


def test_alignment_adapter_binds_exact_audio_and_writes_safe_evidence(
    tmp_path, monkeypatch
):
    text = "Clear approved words stay aligned."
    normalized = SpokenTextNormalizer().normalize(
        script_revision_id="spoken-av", source_text=text
    )
    words = [
        {
            "text": token.text,
            "start": index * 0.2,
            "end": (index + 0.8) * 0.2,
            "type": "word",
            "loss": 0.01,
        }
        for index, token in enumerate(normalized.spoken_tokens)
    ]
    events = []
    transport = _MultipartTransport(
        {
            "request_id": "safe-forced",
            "words": words,
            "alignment_loss": 0.01,
            "transcript_loss": 0.01,
        },
        events,
    )
    client = ElevenLabsForcedAlignmentClient(transport)
    gateway = MR1AlignmentGatewayAdapter(
        api_key="secret-never-durable",
        settings=_settings(),
        workspace_root=tmp_path,
        client=client,
    )
    audio = tmp_path / "run" / "narration.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"approved-final-audio")
    destination = tmp_path / "run" / "alignment.json"
    pkg_tokens = [
        {"index": index, "text": token.text, "token_id": f"token-{index:06d}"}
        for index, token in enumerate(normalized.spoken_tokens)
    ]
    core = {
        "provider": "forced_alignment",
        "operation": "forced_alignment",
        "audio_ref": str(audio),
        "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "spoken_text_artifact_version_id": "spoken-av",
        "spoken_text_hash": "b" * 64,
        "normalized_text_hash": content_hash({"normalized_text": text}),
        "normalized_text": text,
        "spoken_tokens": pkg_tokens,
        "strict_token_coverage": 1.0,
        "estimated_timing_fallback_allowed": False,
        "approval_id": "approval-id",
        "idempotency_key": "mr1:run:elevenlabs:forced_alignment",
        "idempotency_fingerprint": "e" * 64,
        "destination": str(destination),
        "attempt_cap": 1,
        "sdk_retry": False,
    }
    monkeypatch.setattr(
        "app.services.mr1_provider_gateways._probe_duration_ms", lambda _: 2000
    )

    result = gateway.execute_once(
        _with_hash(core),
        audio_path=audio,
        before_submit=lambda: events.append("boundary"),
    )

    assert events == ["boundary", "network"]
    assert result["verification_status"] == "PASS"
    assert result["token_coverage"] == 1.0
    assert json.loads(destination.read_text())["audio_sha256"] == core["audio_sha256"]


class _FakeExecutionContext:
    def __init__(self, target):
        self.workspace_target_path = target

    def validate_against(self, plan):
        return None


class _FakeMediaDelegate:
    def __init__(self):
        self.calls = 0

    def download(self, *, plan, context):
        self.calls += 1
        context.workspace_target_path.parent.mkdir(parents=True, exist_ok=True)
        context.workspace_target_path.write_bytes(b"fake-selected-pexels-video")
        return {"unused": True}


class _FakePexelsClient:
    def __init__(
        self,
        search_transport,
        downloader,
        *,
        declared_duration_seconds=8.0,
        downloaded_duration_seconds=8.0,
    ):
        self.search_transport = search_transport
        self.downloader = downloader
        self.declared_duration_seconds = declared_duration_seconds
        self.downloaded_duration_seconds = downloaded_duration_seconds

    def search_select_once(
        self,
        *,
        api_key,
        request,
        workspace_directory,
        excluded_provider_asset_ids=(),
        semantic_fit_threshold=None,
    ):
        assert semantic_fit_threshold == 0.78
        assert "asset-1" not in set(excluded_provider_asset_ids)
        query_plan = PexelsQueryPlanner().plan(request, per_page=20)
        params = urllib.parse.urlencode(
            {
                "query": query_plan.queries[0],
                "orientation": query_plan.orientation,
                "size": query_plan.size_preference,
                "per_page": query_plan.per_page,
            }
        )
        self.search_transport.json_request(
            "GET",
            f"https://api.pexels.com{query_plan.endpoint}?{params}",
            headers={"Authorization": api_key},
        )
        raw_hash = hashlib.sha256(b"volatile-download").hexdigest()
        plan = {
            "provider_asset_id": "asset-1",
            "provider_file_id": "file-1",
            "source_page_url": "https://www.pexels.com/video/1/",
            "creator_name": "Safe Creator",
            "creator_url": "https://www.pexels.com/@safe/",
            "volatile_download_reference": f"volatile://pexels-download/{raw_hash[:24]}",
            "download_url_hash": raw_hash,
            "expected_media_host": "videos.pexels.com",
            "query_present": True,
            "width": 1920,
            "height": 1080,
            "duration": self.declared_duration_seconds,
            "mime_type": "video/mp4",
            "expected_usage_role": "SUPPORTING_STOCK",
            "production_eligible": False,
        }
        plan["plan_hash"] = content_hash(plan)
        return (
            {
                "query_plan": query_plan.model_dump(mode="json"),
                "ranking": {"ranking_verdict": "PASS"},
                "download_plan": plan,
            },
            {
                "provider_asset_id": "asset-1",
                "source_page_url": plan["source_page_url"],
            },
            _FakeExecutionContext(workspace_directory / "temporary.mp4"),
        )

    def download_once(self, *, plan, execution_context, request_id):
        self.downloader.download(plan=plan, context=execution_context)
        target = execution_context.workspace_target_path
        return SimpleNamespace(
            sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            size_bytes=target.stat().st_size,
            media_probe={
                "format": {
                    "duration": str(self.downloaded_duration_seconds),
                }
            },
            http_evidence={"final_media_host": "videos.pexels.com"},
        )


def _pexels_request_core(
    destination: Path,
    *,
    semantic_intent: str,
    scene_id: str = "SC-07",
) -> dict:
    return {
        "provider": "pexels_api",
        "operation": "supporting_asset_acquisition",
        "scene_id": scene_id,
        "route": "PEXELS_VIDEO",
        "semantic_intent": semantic_intent,
        "semantic_fit_threshold": 0.78,
        "semantic_fit_threshold_authority": (
            "frozen_channel_policy.provider_usage_policy.pexels."
            "semantic_fit_threshold"
        ),
        "target_market": "US",
        "market_context": "US_SMALL_BUSINESS",
        "observable_reality_support_only": True,
        "generated_evidence_authority": False,
        "automatic_pexels_to_ai_fallback": False,
        "provider_substitution_allowed": False,
        "excluded_provider_asset_ids": [],
        "canonical_timeline_hash": "f" * 64,
        "timing_authority": "CANONICAL_MEDIA_TIMELINE",
        "estimated_timing_fallback_used": False,
        "scene_start_ms": 0,
        "scene_end_ms": 8_000,
        "scene_duration_ms": 8_000,
        "supporting_visual_subwindows_hash": "d" * 64,
        "stock_context_start_ms": 0,
        "stock_context_end_ms": 5_000,
        "stock_context_duration_ms": 5_000,
        "native_explanation_start_ms": 5_000,
        "native_explanation_end_ms": 8_000,
        "native_explanation_duration_ms": 3_000,
        "native_mechanism": "BRIEF_CONTEXT_THEN_EXCEPTION_QUEUE",
        "supporting_subwindow_policy_ref": (
            "policy://supporting-subwindow/v1"
        ),
        "minimum_duration_seconds": 5.0,
        "maximum_duration_seconds": 120.0,
        "approval_id": "approval-id",
        "idempotency_key": f"mr1:run:pexels:{scene_id}",
        "idempotency_fingerprint": "e" * 64,
        "destination": str(destination),
        "attempt_cap": 1,
        "sdk_retry": False,
    }


def _with_approved_pexels_query_authority(core: dict) -> dict:
    authorized = {
        **core,
        "approved_query_authority": build_mr1_pexels_query_authority(core),
    }
    return _with_hash(authorized)


def test_pexels_adapter_has_two_explicit_boundaries_and_no_fallback(tmp_path):
    events = []

    def factory(search_transport, downloader):
        search_transport.delegate = _JSONTransport({}, events)
        downloader.delegate = _FakeMediaDelegate()
        return _FakePexelsClient(search_transport, downloader)

    gateway = MR1PexelsGatewayAdapter(
        api_key="pexels-secret",
        settings=_settings(),
        workspace_root=tmp_path,
        client_factory=factory,
    )
    destination = tmp_path / "run" / "SC-04.mp4"
    core = {
        "provider": "pexels_api",
        "operation": "supporting_asset_acquisition",
        "scene_id": "SC-04",
        "route": "PEXELS_VIDEO",
        "semantic_intent": "film crew studio lighting production",
        "semantic_fit_threshold": 0.78,
        "semantic_fit_threshold_authority": (
            "frozen_channel_policy.provider_usage_policy.pexels.semantic_fit_threshold"
        ),
        "target_market": "US",
        "market_context": "US_SMALL_BUSINESS",
        "observable_reality_support_only": True,
        "generated_evidence_authority": False,
        "automatic_pexels_to_ai_fallback": False,
        "provider_substitution_allowed": False,
        "excluded_provider_asset_ids": [],
        "canonical_timeline_hash": "f" * 64,
        "timing_authority": "CANONICAL_MEDIA_TIMELINE",
        "estimated_timing_fallback_used": False,
        "scene_start_ms": 0,
        "scene_end_ms": 8_000,
        "scene_duration_ms": 8_000,
        "supporting_visual_subwindows_hash": "d" * 64,
        "stock_context_start_ms": 0,
        "stock_context_end_ms": 5_000,
        "stock_context_duration_ms": 5_000,
        "native_explanation_start_ms": 5_000,
        "native_explanation_end_ms": 8_000,
        "native_explanation_duration_ms": 3_000,
        "native_mechanism": "native mechanism",
        "supporting_subwindow_policy_ref": "policy://supporting-subwindow/v1",
        "minimum_duration_seconds": 5.0,
        "maximum_duration_seconds": 120.0,
        "approval_id": "approval-id",
        "idempotency_key": "mr1:run:pexels:SC-04",
        "idempotency_fingerprint": "e" * 64,
        "destination": str(destination),
        "attempt_cap": 1,
        "sdk_retry": False,
    }

    result = gateway.acquire_scene_once(
        _with_approved_pexels_query_authority(core),
        destination=destination,
        before_search_submit=lambda: events.append("search-boundary"),
        before_download_submit=lambda: events.append("download-boundary"),
    )

    assert events == ["search-boundary", "network", "download-boundary"]
    assert result["route"] == "PEXELS_VIDEO"
    assert result["automatic_fallback_used"] is False
    assert result["raw_media_url_persisted"] is False


def test_pexels_adapter_uses_bounded_stock_search_intent_only_for_query(
    tmp_path,
):
    events = []

    def factory(search_transport, downloader):
        search_transport.delegate = _JSONTransport({}, events)
        downloader.delegate = _FakeMediaDelegate()
        return _FakePexelsClient(search_transport, downloader)

    gateway = MR1PexelsGatewayAdapter(
        api_key="pexels-secret",
        settings=_settings(),
        workspace_root=tmp_path,
        client_factory=factory,
    )
    destination = tmp_path / "run" / "SC-07-bounded-stock.mp4"
    package_semantic_intent = (
        "A founder explains a detailed filing workflow while native graphics "
        "carry the mechanism and stock footage supplies only office context."
    )
    core = _pexels_request_core(
        destination,
        semantic_intent=package_semantic_intent,
    )
    core.update(
        {
            "stock_search_intent": (
                "People discussing office paperwork together."
            ),
            "stock_search_intent_scope": (
                "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
            ),
        }
    )
    approved = _with_approved_pexels_query_authority(core)

    result = gateway.acquire_scene_once(
        approved,
        destination=destination,
        before_search_submit=lambda: events.append("search-boundary"),
        before_download_submit=lambda: events.append(
            "download-boundary"
        ),
    )

    assert events == ["search-boundary", "network", "download-boundary"]
    assert result["package_semantic_intent"] == package_semantic_intent
    assert result["stock_search_intent"] == core[
        "stock_search_intent"
    ]
    assert result["stock_search_intent_scope"] == (
        "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
    )
    assert result["query_plan"]["queries"][0] == (
        "people discussing office paperwork workplace b roll"
    )
    assert approved["approved_query_authority"][
        "package_semantic_intent"
    ] == package_semantic_intent
    assert approved["approved_query_authority"][
        "stock_search_intent"
    ] == core["stock_search_intent"]


def test_pexels_adapter_rejects_unscoped_stock_search_intent_before_submit(
    tmp_path,
):
    events = []
    factory_calls = []

    def factory(search_transport, downloader):
        factory_calls.append((search_transport, downloader))
        raise AssertionError("scope gate must precede client construction")

    gateway = MR1PexelsGatewayAdapter(
        api_key="pexels-secret",
        settings=_settings(),
        workspace_root=tmp_path,
        client_factory=factory,
    )
    destination = tmp_path / "run" / "SC-07-unscoped-stock.mp4"
    core = _pexels_request_core(
        destination,
        semantic_intent="Whole package semantic remains unchanged.",
    )
    core["stock_search_intent"] = (
        "People discussing office paperwork together."
    )

    with pytest.raises(
        ValueError,
        match="^MR1_PEXELS_STOCK_SEARCH_INTENT_SCOPE_INVALID$",
    ):
        gateway.acquire_scene_once(
            _with_hash(core),
            destination=destination,
            before_search_submit=lambda: events.append(
                "search-boundary"
            ),
            before_download_submit=lambda: events.append(
                "download-boundary"
            ),
        )
    assert factory_calls == []
    assert events == []
    assert not destination.exists()


def _long_scene_short_stock_context_request(
    destination: Path,
) -> dict:
    core = _pexels_request_core(
        destination,
        semantic_intent="People discussing office paperwork together.",
    )
    return {
        **core,
        "scene_end_ms": 50_000,
        "scene_duration_ms": 50_000,
        "stock_context_end_ms": 8_000,
        "stock_context_duration_ms": 8_000,
        "native_explanation_start_ms": 8_000,
        "native_explanation_end_ms": 50_000,
        "native_explanation_duration_ms": 42_000,
        "minimum_duration_seconds": 8.0,
    }


def test_pexels_download_duration_gate_uses_stock_subwindow_not_full_scene(
    tmp_path,
):
    events = []

    def factory(search_transport, downloader):
        search_transport.delegate = _JSONTransport({}, events)
        downloader.delegate = _FakeMediaDelegate()
        return _FakePexelsClient(
            search_transport,
            downloader,
            declared_duration_seconds=8.0,
            downloaded_duration_seconds=8.0,
        )

    gateway = MR1PexelsGatewayAdapter(
        api_key="pexels-secret",
        settings=_settings(),
        workspace_root=tmp_path,
        client_factory=factory,
    )
    destination = tmp_path / "run" / "SC-07-stock-subwindow.mp4"

    result = gateway.acquire_scene_once(
        _with_approved_pexels_query_authority(
            _long_scene_short_stock_context_request(destination)
        ),
        destination=destination,
        before_search_submit=lambda: events.append("search-boundary"),
        before_download_submit=lambda: events.append("download-boundary"),
    )

    assert result["scene_duration_ms"] == 50_000
    assert result["duration_ms"] == 8_000
    assert events == ["search-boundary", "network", "download-boundary"]


def test_pexels_download_duration_gate_rejects_clip_shorter_than_stock_subwindow(
    tmp_path,
):
    events = []

    def factory(search_transport, downloader):
        search_transport.delegate = _JSONTransport({}, events)
        downloader.delegate = _FakeMediaDelegate()
        return _FakePexelsClient(
            search_transport,
            downloader,
            declared_duration_seconds=8.0,
            downloaded_duration_seconds=7.999,
        )

    gateway = MR1PexelsGatewayAdapter(
        api_key="pexels-secret",
        settings=_settings(),
        workspace_root=tmp_path,
        client_factory=factory,
    )
    destination = tmp_path / "run" / "SC-07-too-short.mp4"

    with pytest.raises(
        RuntimeError,
        match="^MR1_PEXELS_DOWNLOADED_CLIP_TOO_SHORT_FOR_TIMELINE$",
    ):
        gateway.acquire_scene_once(
            _with_approved_pexels_query_authority(
                _long_scene_short_stock_context_request(destination)
            ),
            destination=destination,
            before_search_submit=lambda: events.append("search-boundary"),
            before_download_submit=lambda: events.append("download-boundary"),
        )

    assert events == ["search-boundary", "network", "download-boundary"]


class _InjectedWireRequestPexelsClient:
    def __init__(self, search_transport, *, mutation):
        self.search_transport = search_transport
        self.mutation = mutation

    def search_select_once(
        self,
        *,
        api_key,
        request,
        workspace_directory,
        excluded_provider_asset_ids=(),
        semantic_fit_threshold=None,
    ):
        query_plan = PexelsQueryPlanner().plan(request, per_page=20)
        params = {
            "query": query_plan.queries[0],
            "orientation": query_plan.orientation,
            "size": query_plan.size_preference,
            "per_page": query_plan.per_page,
        }
        method = "GET"
        origin = "https://api.pexels.com"
        if self.mutation == "url":
            origin = "https://arbitrary.invalid"
        elif self.mutation == "query":
            params["query"] = "arbitrary unapproved query"
        elif self.mutation == "method":
            method = "POST"
        else:
            raise AssertionError("unknown injected wire mutation")
        self.search_transport.json_request(
            method,
            f"{origin}{query_plan.endpoint}?"
            + urllib.parse.urlencode(params),
            headers={"Authorization": api_key},
        )
        raise AssertionError("invalid wire request reached the delegate")


@pytest.mark.parametrize(
    "mutation,error_code",
    [
        ("url", "MR1_PEXELS_SEARCH_TRANSPORT_ENDPOINT_CHANGED"),
        ("query", "MR1_PEXELS_SEARCH_TRANSPORT_QUERY_CHANGED"),
        ("method", "MR1_PEXELS_SEARCH_TRANSPORT_METHOD_INVALID"),
    ],
)
def test_pexels_injected_wire_request_is_rejected_before_boundary_or_delegate(
    tmp_path,
    mutation,
    error_code,
):
    events = []
    delegate = _JSONTransport({}, events)
    media_delegate = _FakeMediaDelegate()

    def factory(search_transport, downloader):
        search_transport.delegate = delegate
        downloader.delegate = media_delegate
        return _InjectedWireRequestPexelsClient(
            search_transport,
            mutation=mutation,
        )

    gateway = MR1PexelsGatewayAdapter(
        api_key="pexels-secret",
        settings=_settings(),
        workspace_root=tmp_path,
        client_factory=factory,
    )
    destination = tmp_path / "run" / f"SC-04-{mutation}.mp4"
    core = _pexels_request_core(
        destination,
        semantic_intent="film crew studio lighting production",
        scene_id="SC-04",
    )

    with pytest.raises(RuntimeError, match=f"^{error_code}$"):
        gateway.acquire_scene_once(
            _with_approved_pexels_query_authority(core),
            destination=destination,
            before_search_submit=lambda: events.append("search-boundary"),
            before_download_submit=lambda: events.append(
                "download-boundary"
            ),
        )

    assert events == []
    assert delegate.calls == 0
    assert media_delegate.calls == 0
    assert gateway.submitted_scenes == set()
    assert not destination.exists()


class _MismatchedReturnedQueryPlanPexelsClient(_FakePexelsClient):
    def __init__(self, search_transport, downloader, *, mutation):
        super().__init__(search_transport, downloader)
        self.mutation = mutation

    def search_select_once(self, **kwargs):
        safe_search, selected, execution_context = super().search_select_once(
            **kwargs
        )
        if self.mutation == "query_plan":
            safe_search["query_plan"]["queries"][0] = (
                "arbitrary returned query"
            )
        elif self.mutation == "plan_hash":
            safe_search["query_plan"]["plan_hash"] = "0" * 64
        else:
            raise AssertionError("unknown returned-plan mutation")
        return safe_search, selected, execution_context


@pytest.mark.parametrize("mutation", ["query_plan", "plan_hash"])
def test_pexels_mismatched_returned_query_plan_never_downloads(
    tmp_path,
    mutation,
):
    events = []
    delegate = _JSONTransport({}, events)
    media_delegate = _FakeMediaDelegate()

    def factory(search_transport, downloader):
        search_transport.delegate = delegate
        downloader.delegate = media_delegate
        return _MismatchedReturnedQueryPlanPexelsClient(
            search_transport,
            downloader,
            mutation=mutation,
        )

    gateway = MR1PexelsGatewayAdapter(
        api_key="pexels-secret",
        settings=_settings(),
        workspace_root=tmp_path,
        client_factory=factory,
    )
    destination = tmp_path / "run" / f"SC-04-{mutation}.mp4"
    core = _pexels_request_core(
        destination,
        semantic_intent="film crew studio lighting production",
        scene_id="SC-04",
    )

    with pytest.raises(
        RuntimeError,
        match="^MR1_PEXELS_RETURNED_QUERY_PLAN_CHANGED$",
    ):
        gateway.acquire_scene_once(
            _with_approved_pexels_query_authority(core),
            destination=destination,
            before_search_submit=lambda: events.append("search-boundary"),
            before_download_submit=lambda: events.append(
                "download-boundary"
            ),
        )

    assert events == ["search-boundary", "network"]
    assert delegate.calls == 1
    assert media_delegate.calls == 0
    assert gateway.submitted_scenes == {"SC-04"}
    assert not destination.exists()


def test_pexels_infeasible_query_fails_before_factory_or_submit(tmp_path):
    events = []
    factory_calls = []

    def factory(search_transport, downloader):
        factory_calls.append((search_transport, downloader))
        raise AssertionError("client factory is after the feasibility gate")

    gateway = MR1PexelsGatewayAdapter(
        api_key="pexels-secret",
        settings=_settings(),
        workspace_root=tmp_path,
        client_factory=factory,
    )
    destination = tmp_path / "run" / "SC-07.mp4"
    core = _pexels_request_core(
        destination,
        semantic_intent=(
            "Office coworkers review paperwork at a small-business conference "
            "table while one person points to a missing field."
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="^MR1_PEXELS_QUERY_INTENT_COVERAGE_INADEQUATE$",
    ):
        gateway.acquire_scene_once(
            _with_hash(core),
            destination=destination,
            before_search_submit=lambda: events.append("search-boundary"),
            before_download_submit=lambda: events.append(
                "download-boundary"
            ),
        )

    assert factory_calls == []
    assert events == []
    assert gateway.submitted_scenes == set()
    assert not destination.exists()


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"canonical_timeline_hash": None}, "TIMELINE_HASH"),
        ({"scene_duration_ms": 7_999}, "SCENE_TIMING"),
        ({"stock_context_duration_ms": 4_999}, "SUPPORTING_SUBWINDOW"),
        ({"timing_authority": "PROVIDER_METADATA"}, "TIMING_AUTHORITY"),
        ({"estimated_timing_fallback_used": True}, "FALLBACK"),
        ({"minimum_duration_seconds": 7.0}, "DURATION_RANGE"),
        ({"idempotency_fingerprint": "invalid"}, "IDEMPOTENCY_FINGERPRINT"),
        (
            {"excluded_provider_asset_ids": ["asset-1", "asset-1"]},
            "EXCLUDED_PROVIDER_ASSET_IDS",
        ),
    ],
)
def test_pexels_timing_tamper_fails_before_search(tmp_path, mutation, reason):
    events = []

    def factory(search_transport, downloader):
        search_transport.delegate = _JSONTransport({}, events)
        downloader.delegate = _FakeMediaDelegate()
        return _FakePexelsClient(search_transport, downloader)

    gateway = MR1PexelsGatewayAdapter(
        api_key="pexels-secret",
        settings=_settings(),
        workspace_root=tmp_path,
        client_factory=factory,
    )
    destination = tmp_path / "run" / "SC-04.mp4"
    core = {
        "provider": "pexels_api",
        "operation": "supporting_asset_acquisition",
        "scene_id": "SC-04",
        "route": "PEXELS_VIDEO",
        "semantic_intent": "film crew studio lighting production",
        "semantic_fit_threshold": 0.78,
        "semantic_fit_threshold_authority": (
            "frozen_channel_policy.provider_usage_policy.pexels.semantic_fit_threshold"
        ),
        "target_market": "US",
        "market_context": "US_SMALL_BUSINESS",
        "observable_reality_support_only": True,
        "generated_evidence_authority": False,
        "automatic_pexels_to_ai_fallback": False,
        "provider_substitution_allowed": False,
        "excluded_provider_asset_ids": [],
        "canonical_timeline_hash": "f" * 64,
        "timing_authority": "CANONICAL_MEDIA_TIMELINE",
        "estimated_timing_fallback_used": False,
        "scene_start_ms": 0,
        "scene_end_ms": 8_000,
        "scene_duration_ms": 8_000,
        "supporting_visual_subwindows_hash": "d" * 64,
        "stock_context_start_ms": 0,
        "stock_context_end_ms": 5_000,
        "stock_context_duration_ms": 5_000,
        "native_explanation_start_ms": 5_000,
        "native_explanation_end_ms": 8_000,
        "native_explanation_duration_ms": 3_000,
        "native_mechanism": "native mechanism",
        "supporting_subwindow_policy_ref": "policy://supporting-subwindow/v1",
        "minimum_duration_seconds": 5.0,
        "maximum_duration_seconds": 120.0,
        "approval_id": "approval-id",
        "idempotency_key": "mr1:run:pexels:SC-04",
        "idempotency_fingerprint": "e" * 64,
        "destination": str(destination),
        "attempt_cap": 1,
        "sdk_retry": False,
    }
    core.update(mutation)

    with pytest.raises(ValueError, match=reason):
        gateway.acquire_scene_once(
            _with_hash(core),
            destination=destination,
            before_search_submit=lambda: events.append("search-boundary"),
            before_download_submit=lambda: events.append("download-boundary"),
        )

    assert events == []


class _FakeDrive:
    def __init__(self, *, fail_metadata_once: bool = False):
        self.remote = {}
        self.mutations = 0
        self.upload_calls = 0
        self.metadata_calls = 0
        self.fail_metadata_once = fail_metadata_once

    def ensure_folder_path(self, *, access_token, root_folder_id, folder_path):
        self.mutations += 1
        return f"folder:{root_folder_id}/{'/'.join(folder_path)}"

    def upload_file(
        self, *, access_token, local_path, folder_id, upload_mode, mime_type
    ):
        self.mutations += 1
        self.upload_calls += 1
        data = local_path.read_bytes()
        identifier = f"drive-{len(self.remote) + 1}"
        result = GoogleDriveUploadResult(
            drive_file_id=identifier,
            drive_folder_id=folder_id,
            web_view_link=None,
            file_name=local_path.name,
            mime_type=mime_type,
            size_bytes=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
            upload_mode=upload_mode,
            technical_appendix={},
        )
        self.remote[identifier] = result
        return result

    def get_file_metadata(self, *, access_token, drive_file_id):
        self.metadata_calls += 1
        if self.fail_metadata_once and self.metadata_calls == 1:
            raise RuntimeError("fake metadata readback unavailable once")
        return self.remote[drive_file_id]

    def list_folder_files(self, *, access_token, folder_id):
        return [
            item for item in self.remote.values() if item.drive_folder_id == folder_id
        ]


def test_drive_adapter_converts_sources_and_normalizes_strong_receipt(tmp_path):
    workspace = tmp_path / "workspace"
    media = workspace / "run" / "review.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"review-media")
    provider = _FakeDrive()
    service = MR1DriveArchiveService(
        provider=provider,
        root_folder_id="configured-root",
        upload_mode="resumable",
        source_root=workspace,
        access_token_resolver=lambda: "access-token",
        state_root=workspace / ".drive-state",
    )
    gateway = MR1DriveGatewayAdapter(
        service=service,
        settings=_settings(),
        workspace_root=workspace,
    )
    run_id = "mr1-run-001"
    archive_identity = f"mr1-archive://small-team-ai/{run_id}"
    journal = workspace / "run" / "drive" / "remote-id-journal.json"
    boundaries = []

    result = gateway.upload_or_resume_and_verify(
        {
            "schema_version": "mr1.local-archive-manifest.v1",
            "run_id": run_id,
            "archive_identity": archive_identity,
            "files": [
                {
                    "logical_role": "FINAL_REVIEW_MP4",
                    "source_path": str(media),
                    "sha256": hashlib.sha256(media.read_bytes()).hexdigest(),
                }
            ],
        },
        archive_identity=archive_identity,
        journal_path=journal,
        before_first_mutation=lambda: boundaries.append("drive-mutation"),
    )

    assert boundaries == ["drive-mutation"]
    assert result["ARCHIVE_VERIFIED"] is True
    assert result["exact_item_count"] == result["verified_item_count"] == 1
    assert result["duplicate_count"] == 0
    assert json.loads(journal.read_text())["state"] == "VERIFIED"


def test_drive_same_adapter_resumes_repairable_readback_without_duplicate_upload(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    media = workspace / "run" / "review.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"review-media-for-resume")
    provider = _FakeDrive(fail_metadata_once=True)
    service = MR1DriveArchiveService(
        provider=provider,
        root_folder_id="configured-root",
        upload_mode="resumable",
        source_root=workspace,
        access_token_resolver=lambda: "access-token",
        state_root=workspace / ".drive-state",
    )
    gateway = MR1DriveGatewayAdapter(
        service=service,
        settings=_settings(),
        workspace_root=workspace,
    )
    run_id = "mr1-run-resume-001"
    archive_identity = f"mr1-archive://small-team-ai/{run_id}"
    journal = workspace / "run" / "drive" / "remote-id-journal.json"
    manifest = {
        "schema_version": "mr1.local-archive-manifest.v1",
        "run_id": run_id,
        "archive_identity": archive_identity,
        "files": [
            {
                "logical_role": "FINAL_REVIEW_MP4",
                "source_path": str(media),
                "sha256": hashlib.sha256(media.read_bytes()).hexdigest(),
            }
        ],
    }
    boundaries = []

    with pytest.raises(RuntimeError, match="ARCHIVE_VERIFICATION_FAILED"):
        gateway.upload_or_resume_and_verify(
            manifest,
            archive_identity=archive_identity,
            journal_path=journal,
            before_first_mutation=lambda: boundaries.append("first"),
        )
    assert provider.upload_calls == 1
    # Model a crash after the strong per-item journal was fsynced but before a
    # receipt became durable.  Resume must still declare its boundary and only
    # reconcile/read back the already-uploaded remote object.
    service_run_id = f"{run_id}.r0001"
    service.receipt_path(service_run_id).unlink()
    assert service.journal_path(service_run_id).is_file()

    result = gateway.upload_or_resume_and_verify(
        manifest,
        archive_identity=archive_identity,
        journal_path=journal,
        before_first_mutation=lambda: boundaries.append("resume"),
    )

    assert boundaries == ["first", "resume"]
    assert result["ARCHIVE_VERIFIED"] is True
    assert provider.upload_calls == 1


def test_drive_receipt_normalization_recomputes_each_strong_invariant(tmp_path):
    workspace = tmp_path / "workspace"
    media = workspace / "run" / "review.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"strict-receipt-media")
    provider = _FakeDrive()
    service = MR1DriveArchiveService(
        provider=provider,
        root_folder_id="configured-root",
        upload_mode="resumable",
        source_root=workspace,
        state_root=workspace / ".drive-state",
    )
    run_id = "mr1-strict-receipt.r0001"
    receipt = service.upload_and_verify(
        run_id=run_id,
        archive_identity="mr1-archive://small-team-ai/mr1-strict-receipt",
        root_relative_path="small-team-ai/mr1/mr1-strict-receipt/revisions/r0001",
        items=[
            {
                "logical_role": "FINAL_REVIEW_MP4",
                "source_path": str(media),
                "archive_path": "items/final-review/review.mp4",
                "name": "review.mp4",
                "size_bytes": media.stat().st_size,
                "sha256": hashlib.sha256(media.read_bytes()).hexdigest(),
                "md5": hashlib.md5(
                    media.read_bytes(), usedforsecurity=False
                ).hexdigest(),
            }
        ],
        access_token="access-token",
    )
    mutations = {
        "MR1_DRIVE_RECEIPT_PARENT_MISMATCH": lambda value: value["files"][0].update(
            {"drive_folder_id": "wrong-parent"}
        ),
        "MR1_DRIVE_RECEIPT_ORDERED_IDENTITY_MISMATCH": lambda value: value["files"][
            0
        ].update({"name": "wrong-name.mp4"}),
        "MR1_DRIVE_RECEIPT_SIZE_MISMATCH": lambda value: value["files"][0].update(
            {"remote_size_bytes": value["files"][0]["remote_size_bytes"] + 1}
        ),
        "MR1_DRIVE_RECEIPT_CHECKSUM_MISMATCH": lambda value: value["files"][0].update(
            {"remote_sha256": "0" * 64}
        ),
        "MR1_DRIVE_RECEIPT_COUNT_MISMATCH": lambda value: value.update(
            {"remote_item_count": 2}
        ),
        "MR1_DRIVE_RECEIPT_REPORTED_MISMATCH": lambda value: value.update(
            {"mismatch_reason_codes": ["REMOTE_MISMATCH"]}
        ),
    }

    for expected_code, mutate in mutations.items():
        tampered = deepcopy(receipt)
        mutate(tampered)
        tampered["receipt_hash"] = content_hash(
            {key: value for key, value in tampered.items() if key != "receipt_hash"}
        )
        normalized = _normalize_drive_receipt(
            tampered,
            journal_path=workspace / "drive-index.json",
            service_journal_path=service.journal_path(run_id),
            canonical_run_id="mr1-strict-receipt",
            service_execution_run_id=run_id,
            review_round=1,
        )
        assert normalized["ARCHIVE_VERIFIED"] is False
        assert expected_code in normalized["normalization_mismatch_reason_codes"]


def test_drive_review_rounds_preserve_remote_revisions_without_duplicate_upload(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    media = workspace / "run" / "review.mp4"
    media.parent.mkdir(parents=True)
    provider = _FakeDrive()
    service = MR1DriveArchiveService(
        provider=provider,
        root_folder_id="configured-root",
        upload_mode="resumable",
        source_root=workspace,
        access_token_resolver=lambda: "access-token",
        state_root=workspace / ".drive-state",
    )
    gateway = MR1DriveGatewayAdapter(
        service=service,
        settings=_settings(),
        workspace_root=workspace,
    )
    run_id = "mr1-review-revision-001"
    archive_identity = f"mr1-archive://small-team-ai/{run_id}"
    journal = workspace / "run" / "drive" / "remote-id-journal.json"
    boundaries: list[str] = []

    def manifest(review_round: int) -> dict:
        return {
            "schema_version": "mr1.local-archive-manifest.v1",
            "run_id": run_id,
            "archive_identity": archive_identity,
            "review_round": review_round,
            "files": [
                {
                    "logical_role": "FINAL_REVIEW_MP4",
                    "source_path": str(media),
                    "archive_path": "items/final-review/review.mp4",
                    "sha256": hashlib.sha256(media.read_bytes()).hexdigest(),
                    "size_bytes": media.stat().st_size,
                }
            ],
        }

    media.write_bytes(b"review-round-one")
    first = gateway.upload_or_resume_and_verify(
        manifest(1),
        archive_identity=archive_identity,
        journal_path=journal,
        before_first_mutation=lambda: boundaries.append("round-1"),
    )
    first_remote_ids = set(provider.remote)
    first_folder_id = first["drive_folder_id"]

    media.write_bytes(b"review-round-two-with-repaired-bytes")
    second_manifest = manifest(2)
    second = gateway.upload_or_resume_and_verify(
        second_manifest,
        archive_identity=archive_identity,
        journal_path=journal,
        before_first_mutation=lambda: boundaries.append("round-2"),
    )
    resumed = gateway.upload_or_resume_and_verify(
        second_manifest,
        archive_identity=archive_identity,
        journal_path=journal,
        before_first_mutation=lambda: boundaries.append("round-2-resume"),
    )

    assert first["run_id"] == second["run_id"] == resumed["run_id"] == run_id
    assert first["archive_identity"] == second["archive_identity"] == archive_identity
    assert first["service_execution_run_id"].endswith(".r0001")
    assert second["service_execution_run_id"].endswith(".r0002")
    assert second["drive_folder_id"] != first_folder_id
    assert first_remote_ids < set(provider.remote)
    assert len(provider.remote) == provider.upload_calls == 2
    assert boundaries == ["round-1", "round-2", "round-2-resume"]
    index = json.loads(journal.read_text())
    assert index["revision_count"] == 2
    assert set(index["revisions"]) == {"0001", "0002"}
    assert all(value["state"] == "VERIFIED" for value in index["revisions"].values())


def test_finalization_attempt_identity_reaches_real_drive_adapter(tmp_path):
    workspace = tmp_path / "workspace"
    finalization_dir = workspace / "run" / "finalization"
    finalization_dir.mkdir(parents=True)
    human_receipt = finalization_dir / "human-full-watch-receipt.json"
    lineage_receipt = finalization_dir / "final-media-lineage-receipt.json"
    human_receipt.write_text('{"decision":"PASS"}', encoding="utf-8")
    lineage_receipt.write_text('{"lineage":"exact"}', encoding="utf-8")

    run_id = "mr1-finalization-contract-001"
    review_round = 3
    phases = [
        {
            "phase": "CANONICAL_REVIEW_ARCHIVE",
            "operation_key": "google_drive:archive",
            "boundary": "PRE_HUMAN_PASS",
            "max_mutations": 1,
            "cost_usd": 0.0,
        },
        {
            "phase": "FINALIZATION_SUPPLEMENT",
            "operation_key": MR1_DRIVE_FINALIZATION_OPERATION_KEY,
            "boundary": "POST_HUMAN_PASS_PRE_FINAL_MEDIA_REF",
            "max_mutations": 1,
            "cost_usd": 0.0,
        },
    ]
    runner = object.__new__(MR1RealProductionService)
    runner._visual_route_authority = lambda _authority: SimpleNamespace(
        pexels_scenes=()
    )
    attempts = runner._initial_attempts(
        run_id,
        {
            "approval_id": "approval-id",
            "approval_content_hash": "a" * 64,
            "provider_attempt_scope": {
                "drive_phase_count": 2,
                "drive_idempotency_phases": phases,
            },
        },
        budget_reservation={
            "reservation_ref": "mr1-budget://finalization-contract",
            "request_hash": "b" * 64,
            "content_hash": "c" * 64,
            "status": "RESERVED",
            "reserved_amount_usd": 0.0,
        },
        review_round=review_round,
    )
    ledger = attempts[MR1_DRIVE_FINALIZATION_OPERATION_KEY]
    expected_key = mr1_drive_finalization_idempotency_key(
        run_id=run_id,
        review_round=review_round,
    )
    assert ledger["idempotency_key"] == expected_key
    assert ledger["review_round"] == review_round

    def archive_item(path: Path, logical_role: str) -> dict:
        data = path.read_bytes()
        return {
            "logical_role": logical_role,
            "name": path.name,
            "source_path": str(path.resolve()),
            "archive_path": f"finalization/{path.name}",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "md5": hashlib.md5(data, usedforsecurity=False).hexdigest(),
        }

    files = sorted(
        [
            archive_item(
                human_receipt,
                "MR1_HUMAN_FULL_WATCH_RECEIPT",
            ),
            archive_item(
                lineage_receipt,
                "MR1_FINAL_MEDIA_LINEAGE_RECEIPT",
            ),
        ],
        key=lambda item: item["archive_path"],
    )
    archive_identity = f"mr1-archive://small-team-ai/{run_id}"
    manifest = {
        "schema_version": "mr1.finalization-archive-supplement-manifest.v1",
        "run_id": run_id,
        "project_id": "project-id",
        "archive_identity": archive_identity,
        "review_round": review_round,
        "drive_phase_authority": phases[1],
        "idempotency_identity": {
            "operation_key": MR1_DRIVE_FINALIZATION_OPERATION_KEY,
            "idempotency_key": ledger["idempotency_key"],
            "idempotency_fingerprint": ledger["idempotency_fingerprint"],
            "review_round": review_round,
            "distinct_from_canonical_archive": True,
            "automatic_retry_allowed": False,
        },
        "canonical_drive_archive_receipt": {
            "artifact_version_id": "canonical-receipt-version-id",
            "content_hash": "d" * 64,
        },
        "item_count": len(files),
        "total_size_bytes": sum(item["size_bytes"] for item in files),
        "item_set_hash": content_hash({"files": files}),
        "files": files,
    }
    provider = _FakeDrive()
    service = MR1DriveArchiveService(
        provider=provider,
        root_folder_id="configured-root",
        upload_mode="resumable",
        source_root=workspace,
        access_token_resolver=lambda: "access-token",
        state_root=workspace / ".drive-state",
    )
    gateway = MR1DriveGatewayAdapter(
        service=service,
        settings=_settings(),
        workspace_root=workspace,
    )
    journal = workspace / "run" / "drive" / "remote-id-journal.json"
    boundaries: list[str] = []

    mismatched = deepcopy(manifest)
    mismatched["idempotency_identity"]["idempotency_key"] = (
        f"mr1:{run_id}:google_drive:finalization-supplement"
    )
    with pytest.raises(ValueError, match="MR1_DRIVE_FINALIZATION_IDEMPOTENCY_INVALID"):
        gateway.upload_finalization_supplement_and_verify(
            mismatched,
            archive_identity=archive_identity,
            journal_path=journal,
            before_first_mutation=lambda: boundaries.append("unexpected"),
        )
    assert boundaries == []
    assert provider.mutations == 0

    result = gateway.upload_finalization_supplement_and_verify(
        manifest,
        archive_identity=archive_identity,
        journal_path=journal,
        before_first_mutation=lambda: boundaries.append("finalization"),
    )

    assert boundaries == ["finalization"]
    assert result["ARCHIVE_VERIFIED"] is True
    assert result["service_execution_run_id"] == (
        f"{run_id}.r{review_round:04d}.finalization"
    )
    assert result["archive_phase"] == "FINALIZATION_SUPPLEMENT"
    assert provider.upload_calls == 2
    phase_journal = journal.with_name(
        f"{journal.stem}-r{review_round:04d}-finalization.json"
    )
    persisted = json.loads(phase_journal.read_text(encoding="utf-8"))
    assert persisted["state"] == "VERIFIED"
    assert persisted["idempotency_identity"] == manifest["idempotency_identity"]
