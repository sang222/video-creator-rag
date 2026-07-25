from __future__ import annotations

import base64
import io
import json
import urllib.error
from pathlib import Path

import pytest

from app.contracts.asset_acquisition import AssetRequest
from app.contracts.visual_direction import (
    SceneVisualIntent,
    VisualRankingWeights,
    VisualRiskPenalties,
    VisualScoreThresholds,
)
from app.services.cqr1_real_provider import (
    ElevenLabsConvertWithTimestampsClient,
    ElevenLabsForcedAlignmentClient,
    PlannedPexelsV2SearchClient,
)
from app.services.creative_quality_policy import CreativeQualityPolicyCatalog
from app.services.native_render_plan import stable_hash
from app.services.pa1r import RedactedProviderHTTPError
from app.services.pexels_query_planner import (
    PexelsQueryPlanner,
    bind_minimum_duration_to_canonical_scene,
)
from app.services.temporal_authority import SpokenTextNormalizer
from app.services.visual_direction import VisualDirectionCompiler


ROOT = Path(__file__).resolve().parents[1]


class FakeJSONTransport:
    def __init__(self, response: dict, headers: dict[str, str] | None = None):
        self.response = response
        self.response_headers = headers or {}
        self.calls: list[dict] = []

    def json_request(self, method, url, *, headers, payload=None, timeout=30):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout": timeout,
            }
        )
        return self.response, self.response_headers

    def bytes_request(self, method, url, *, headers, payload=None, timeout=60):
        raise AssertionError("bytes transport is not part of this boundary")


class FakeMultipartTransport:
    def __init__(self, response: dict, headers: dict[str, str] | None = None):
        self.response = response
        self.response_headers = headers or {}
        self.calls: list[dict] = []

    def multipart_json_request(
        self,
        method,
        url,
        *,
        headers,
        fields,
        files,
        timeout=120,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "fields": fields,
                "files": files,
                "timeout": timeout,
            }
        )
        return self.response, self.response_headers


def _normalized(text: str = "Clear words stay aligned."):
    return SpokenTextNormalizer().normalize(
        script_revision_id="cqr1-real-provider-fixture",
        source_text=text,
    )


def _provider_alignment(normalized, *, duration_seconds: float = 2.0) -> dict:
    characters = list(normalized.spoken_text)
    step = duration_seconds / (len(characters) + 1)
    starts = [round(index * step, 6) for index in range(len(characters))]
    ends = [round(start + step * 0.8, 6) for start in starts]
    alignment = {
        "characters": characters,
        "character_start_times_seconds": starts,
        "character_end_times_seconds": ends,
    }
    return {
        "audio_base64": base64.b64encode(b"fixture-audio-one-call").decode("ascii"),
        "alignment": alignment,
        "normalized_alignment": alignment,
        "request_id": "tts-request-safe",
        "usage": {"characters": len(normalized.spoken_text), "api_token": "must-drop"},
    }


def _forced_alignment(normalized, *, duration_seconds: float = 2.0) -> dict:
    step = duration_seconds / (len(normalized.spoken_tokens) + 1)
    words = []
    for index, token in enumerate(normalized.spoken_tokens):
        start = index * step
        words.append(
            {
                "text": token.text,
                "start": round(start, 6),
                "end": round(start + step * 0.8, 6),
                "type": "word",
                "loss": 0.01,
            }
        )
    return {
        "request_id": "forced-request-safe",
        "words": words,
        "alignment_loss": 0.01,
        "transcript_loss": 0.02,
    }


def test_convert_with_timestamps_is_one_shot_atomic_and_uses_exact_spoken_text(tmp_path):
    normalized = _normalized()
    transport = FakeJSONTransport(_provider_alignment(normalized))
    client = ElevenLabsConvertWithTimestampsClient(
        transport,
        media_probe=lambda _: {"format": {"duration": "2.0"}},
    )
    destination = tmp_path / "narration.mp3"

    result = client.execute_once(
        api_key="secret-never-persisted",
        normalized=normalized,
        voice_id="voice-safe",
        model_id="eleven_multilingual_v2",
        destination=destination,
        voice_settings={"stability": 0.55},
    )

    assert destination.read_bytes() == b"fixture-audio-one-call"
    assert not destination.with_name(destination.name + ".part").exists()
    assert result.audio_duration_ms == 2000
    assert result.timing_seed.timing_available is True
    assert result.usage_metadata == {"characters": len(normalized.spoken_text)}
    assert transport.calls[0]["url"].endswith("/v1/text-to-speech/voice-safe/with-timestamps")
    assert transport.calls[0]["payload"]["text"] == normalized.spoken_text
    assert transport.calls[0]["payload"]["apply_text_normalization"] == "off"
    assert "secret-never-persisted" not in json.dumps(result.safe_evidence())
    with pytest.raises(RuntimeError, match="TTS_CALL_LIMIT"):
        client.execute_once(
            api_key="secret-never-persisted",
            normalized=normalized,
            voice_id="voice-safe",
            model_id="eleven_multilingual_v2",
            destination=tmp_path / "second.mp3",
        )
    assert len(transport.calls) == 1


def test_convert_with_timestamps_invalid_audio_leaves_no_partial_file(tmp_path):
    normalized = _normalized()
    transport = FakeJSONTransport(
        {**_provider_alignment(normalized), "audio_base64": "not valid base64"}
    )
    destination = tmp_path / "invalid.mp3"
    client = ElevenLabsConvertWithTimestampsClient(
        transport,
        media_probe=lambda _: {"format": {"duration": "2.0"}},
    )

    with pytest.raises(ValueError, match="AUDIO_BASE64_INVALID"):
        client.execute_once(
            api_key="secret",
            normalized=normalized,
            voice_id="voice",
            model_id="model",
            destination=destination,
        )
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".part").exists()
    with pytest.raises(RuntimeError, match="TTS_CALL_LIMIT"):
        client.execute_once(
            api_key="secret",
            normalized=normalized,
            voice_id="voice",
            model_id="model",
            destination=destination,
        )
    assert len(transport.calls) == 1


def test_elevenlabs_timestamp_http_error_is_redacted_and_not_retried(tmp_path):
    secret = "elevenlabs-secret-must-not-leak"

    class RejectingTransport(FakeJSONTransport):
        def json_request(self, method, url, *, headers, payload=None, timeout=30):
            self.calls.append({"method": method, "url": url})
            raise urllib.error.HTTPError(
                url,
                403,
                "Forbidden",
                {"X-Request-Id": "safe-request", "Set-Cookie": "must-drop"},
                io.BytesIO(f'{{"detail":"{secret}"}}'.encode()),
            )

    transport = RejectingTransport({})
    client = ElevenLabsConvertWithTimestampsClient(transport)
    with pytest.raises(RedactedProviderHTTPError) as caught:
        client.execute_once(
            api_key=secret,
            normalized=_normalized(),
            voice_id="voice",
            model_id="model",
            destination=tmp_path / "narration.mp3",
        )
    serialized = json.dumps(caught.value.safe_evidence)
    assert secret not in serialized
    assert "set-cookie" not in serialized
    assert caught.value.safe_evidence["response_headers"] == {
        "x-request-id": "safe-request"
    }
    assert len(transport.calls) == 1


def test_forced_alignment_is_one_shot_multipart_and_reuses_strict_parser(tmp_path):
    normalized = _normalized("Clear words align.")
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"one-final-audio")
    transport = FakeMultipartTransport(
        _forced_alignment(normalized),
        {"x-request-id": "forced-header-safe"},
    )
    client = ElevenLabsForcedAlignmentClient(transport)

    result = client.execute_once(
        api_key="secret-never-persisted",
        normalized=normalized,
        audio_path=audio,
        audio_asset_ref="artifact://audio/final",
        audio_duration_ms=2000,
    )

    assert result.evidence.verification_status == "PASS"
    assert result.evidence.provider_request_id_availability == "PRESENT"
    assert result.evidence.missing_tokens == [] and result.evidence.extra_words == []
    assert all(word.source_spoken_token_ids for word in result.evidence.words)
    call = transport.calls[0]
    assert call["url"].endswith("/v1/forced-alignment")
    assert call["fields"] == {"text": normalized.spoken_text}
    assert call["files"]["file"][0] == "narration.mp3"
    assert call["files"]["file"][2] == audio.read_bytes()
    assert "secret-never-persisted" not in json.dumps(result.safe_evidence())
    with pytest.raises(RuntimeError, match="FORCED_ALIGNMENT_CALL_LIMIT"):
        client.execute_once(
            api_key="secret-never-persisted",
            normalized=normalized,
            audio_path=audio,
            audio_asset_ref="artifact://audio/final",
            audio_duration_ms=2000,
        )
    assert len(transport.calls) == 1


def test_forced_alignment_without_provider_request_id_keeps_strong_hash_bindings(
    tmp_path,
):
    normalized = _normalized("Clear words align.")
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"one-final-audio")
    response = _forced_alignment(normalized)
    response.pop("request_id")
    captured: list[dict] = []
    transport = FakeMultipartTransport(response, {})
    client = ElevenLabsForcedAlignmentClient(
        transport,
        response_capture=lambda payload: captured.append(dict(payload)),
    )

    result = client.execute_once(
        api_key="secret-never-persisted",
        normalized=normalized,
        audio_path=audio,
        audio_asset_ref="file-sha256:immutable-audio",
        audio_duration_ms=2000,
    )
    safe = result.safe_evidence()

    assert result.evidence.provider_request_id is None
    assert (
        result.evidence.provider_request_id_availability
        == "NOT_EXPOSED_BY_ENDPOINT"
    )
    assert safe["provider_request_id"] is None
    assert safe["provider_request_id_availability"] == "NOT_EXPOSED_BY_ENDPOINT"
    assert safe["provider_request_hash"] == result.request_hash
    assert safe["provider_response_hash"] == result.provider_response_hash
    assert safe["forced_alignment_content_hash"] == result.evidence.content_hash
    assert len(safe["provider_request_hash"]) == 64
    assert len(safe["provider_response_hash"]) == 64
    assert captured[0]["content_hash"] == result.provider_response_hash
    assert (
        captured[0]["provider_request_id_availability"]
        == "NOT_EXPOSED_BY_ENDPOINT"
    )
    assert result.evidence.verification_status == "PASS"
    assert len(transport.calls) == 1 and client.call_count == 1


def test_forced_alignment_http_error_is_redacted_and_consumes_one_call(tmp_path):
    secret = "forced-secret-must-not-leak"
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"one-final-audio")

    class RejectingMultipart(FakeMultipartTransport):
        def multipart_json_request(self, method, url, **kwargs):
            self.calls.append({"method": method, "url": url})
            raise urllib.error.HTTPError(
                url,
                401,
                "Unauthorized",
                {"X-Request-Id": "safe-forced-request"},
                io.BytesIO(f'{{"detail":"{secret}"}}'.encode()),
            )

    transport = RejectingMultipart({})
    client = ElevenLabsForcedAlignmentClient(transport)
    with pytest.raises(RedactedProviderHTTPError) as caught:
        client.execute_once(
            api_key=secret,
            normalized=_normalized(),
            audio_path=audio,
            audio_asset_ref="artifact://audio/final",
            audio_duration_ms=2000,
        )
    assert secret not in json.dumps(caught.value.safe_evidence)
    assert len(transport.calls) == 1 and client.call_count == 1


def test_forced_alignment_captures_safe_parser_input_before_parse_failure(tmp_path):
    normalized = _normalized("Clear words align.")
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"one-final-audio")
    response = _forced_alignment(normalized)
    response["words"][1]["start"] = -1
    response["api_token"] = "must-drop"
    captured: list[dict] = []
    transport = FakeMultipartTransport(
        response,
        {"X-Request-Id": "safe-request", "Set-Cookie": "must-drop"},
    )
    client = ElevenLabsForcedAlignmentClient(
        transport,
        response_capture=lambda payload: captured.append(dict(payload)),
    )

    with pytest.raises(ValueError, match="TEMPORAL_ALIGNMENT_"):
        client.execute_once(
            api_key="secret",
            normalized=normalized,
            audio_path=audio,
            audio_asset_ref="artifact://audio/final",
            audio_duration_ms=2000,
        )

    assert len(captured) == 1 and captured[0]["content_hash"]
    serialized = json.dumps(captured[0])
    assert "must-drop" not in serialized and "api_token" not in serialized
    assert captured[0]["response_headers"] == {"x-request-id": "safe-request"}
    assert captured[0]["response"]["words"][1]["start"] == -1
    assert len(transport.calls) == 1 and client.call_count == 1


def _visual_inputs():
    policy = CreativeQualityPolicyCatalog(
        ROOT / "config/creative_quality_policy_catalog.yaml"
    ).approved_snapshot("small-team-ai")
    direction = VisualDirectionCompiler().compile(
        channel_id="small-team-ai",
        project_id="pa1r-cqr1-20260714-paid-canary-001",
        format_identity_ref="f4ef71b1-6942-49c4-bb69-47244751265d",
        format_identity_hash="8522fb38cdfe3ff6ae615d39b7d1c8ff2a6fb34a33363276bd3ebea98a320cbc",
        visual_strategy_profile_ref=policy["policy_ref"],
        visual_strategy_profile_hash=policy["policy_hash"],
        policy=policy,
    )
    payload = {
        "request_id": "cqr1-paid-canary-stock-request",
        "scene_id": "cqr1-stock-support",
        "source_segment_ids": ["segment-stock"],
        "purpose": "GROUNDED_DOCUMENTARY_CONTEXT",
        "requested_role": "SUPPORTING_STOCK",
        "semantic_visual_intent": "media operator reviews approved production workflow",
        "required_orientation": "landscape",
        "minimum_resolution": "1280x720",
        "preferred_resolution": "1920x1080",
        "minimum_duration_seconds": 4.0,
        "maximum_duration_seconds": 8.0,
        "crop_policy": "SAFE_CENTER_CROP_WITH_SEMANTIC_REVIEW",
        "person_policy": "NO_RECURRING_HOST",
        "logo_text_policy": "REJECT_VISIBLE_LOGO_OR_EMBEDDED_TEXT",
        "evidence_usage_policy": "NOT_FACTUAL_EVIDENCE",
        "fallback_order": ["NATIVE_VISUAL", "SUPPORTING_STOCK"],
        "projected_cost_class": "LOW",
        "human_review_required": True,
    }
    request = AssetRequest(**payload, request_hash=stable_hash(payload))
    intent = SceneVisualIntent(
        scene_id=request.scene_id,
        semantic_intent=request.semantic_visual_intent,
        target_duration_seconds=6.0,
        previous_scene_summary="native workflow diagram",
        next_scene_summary="restrained synchronized hero transition",
    )
    plan = PexelsQueryPlanner().plan(
        request,
        per_page=2,
        visual_direction=direction,
        scene_intent=intent,
    )
    return policy, direction, request, plan


def _physical_production_visual_inputs():
    policy, direction, request, _ = _visual_inputs()
    payload = request.model_dump(mode="python", exclude={"request_hash"})
    payload["semantic_visual_intent"] = (
        "behind the scenes film crew adjusts studio lighting for physical production"
    )
    physical_request = AssetRequest(
        **payload,
        request_hash=stable_hash(payload),
    )
    intent = SceneVisualIntent(
        scene_id=physical_request.scene_id,
        semantic_intent=physical_request.semantic_visual_intent,
        target_duration_seconds=6.0,
        previous_scene_summary="native workflow diagram",
        next_scene_summary="restrained synchronized hero transition",
    )
    plan = PexelsQueryPlanner().plan(
        physical_request,
        per_page=3,
        visual_direction=direction,
        scene_intent=intent,
    )
    return policy, direction, physical_request, plan


def test_pexels_request_floor_is_ceil_bound_to_canonical_scene_before_search():
    _, direction, request, _ = _visual_inputs()

    bound = bind_minimum_duration_to_canonical_scene(
        request,
        scene_duration_ms=6_292,
    )
    plan = PexelsQueryPlanner().plan(
        bound,
        per_page=2,
        visual_direction=direction,
        scene_intent=SceneVisualIntent(
            scene_id=bound.scene_id,
            semantic_intent=bound.semantic_visual_intent,
            target_duration_seconds=6.292,
        ),
    )

    assert request.minimum_duration_seconds == 4.0
    assert bound.minimum_duration_seconds == 7.0
    assert bound.maximum_duration_seconds == request.maximum_duration_seconds == 8.0
    assert plan.minimum_duration_seconds == 7.0
    assert plan.target_duration_seconds == 6.292
    assert bound.request_hash == stable_hash(
        bound.model_dump(mode="python", exclude={"request_hash"})
    )


def test_pexels_media_workflow_query_prioritizes_editing_workstation_semantics():
    _, _, _, plan = _visual_inputs()

    assert plan.queries[0].startswith(
        "video editing workstation post production"
    )
    assert "media operator reviews approved" not in plan.queries[0]


def test_pexels_physical_production_query_is_screen_free_and_auditable():
    _, _, _, plan = _physical_production_visual_inputs()

    assert plan.queries[0].startswith("film crew studio lighting production")
    assert "video editing workstation" not in plan.queries[0]
    assert {
        "apple",
        "computer",
        "imac",
        "interface",
        "laptop",
        "logo",
        "monitor",
        "phone",
        "screen",
        "software",
        "tv",
        "ui",
    }.issubset(set(plan.forbidden_concepts))


def test_pexels_canonical_duration_binding_blocks_when_scene_exceeds_maximum():
    _, _, request, _ = _visual_inputs()

    with pytest.raises(
        ValueError,
        match="PEXELS_CANONICAL_SCENE_EXCEEDS_REQUEST_MAXIMUM",
    ):
        bind_minimum_duration_to_canonical_scene(
            request,
            scene_duration_ms=8_001,
        )


def test_planned_pexels_v2_executes_one_query_and_fails_closed_on_metadata_gap():
    policy, direction, request, plan = _visual_inputs()
    payload = json.loads(
        (ROOT / "tests/fixtures/as1/pexels_response.json").read_text()
    )
    transport = FakeJSONTransport(
        payload,
        {
            "X-Ratelimit-Limit": "200",
            "X-Ratelimit-Remaining": "199",
            "Set-Cookie": "must-not-persist",
        },
    )
    client = PlannedPexelsV2SearchClient(transport)

    result = client.search_and_rank_once(
        api_key="pexels-secret-never-persisted",
        plan=plan,
        request=request,
        visual_direction=direction,
        weights=VisualRankingWeights.from_policy(policy),
        risk_penalties=VisualRiskPenalties.from_policy(policy),
        thresholds=VisualScoreThresholds.from_policy(policy),
        previous_scene="native workflow diagram",
        next_scene="restrained synchronized hero transition",
    )

    assert client.search_flow_count == 1 and len(transport.calls) == 1
    assert transport.calls[0]["url"].count("/v1/videos/search?") == 1
    assert result.query_used == plan.queries[0]
    assert result.ranking.visual_direction_hash == direction.content_hash
    assert result.ranking.ranking_verdict in {"PASS", "REVIEW_REQUIRED", "BLOCK"}
    assert result.rate_limit == {"limit": 200, "remaining": 199, "reset": None}
    safe = json.dumps(result.safe_evidence())
    assert "pexels-secret-never-persisted" not in safe
    assert "must-not-persist" not in safe
    assert "videos.pexels.example" not in safe
    with pytest.raises(RuntimeError, match="SEARCH_FLOW_LIMIT"):
        client.search_and_rank_once(
            api_key="pexels-secret-never-persisted",
            plan=plan,
            request=request,
            visual_direction=direction,
            weights=VisualRankingWeights.from_policy(policy),
            risk_penalties=VisualRiskPenalties.from_policy(policy),
            thresholds=VisualScoreThresholds.from_policy(policy),
        )
    assert len(transport.calls) == 1


def test_planned_pexels_metadata_gate_blocks_aircraft_and_allows_editing_workstation():
    policy, direction, request, plan = _visual_inputs()
    payload = {
        "page": 1,
        "per_page": 2,
        "videos": [
            {
                "id": 3001,
                "width": 1920,
                "height": 1080,
                "duration": 8,
                "url": "https://www.pexels.com/video/planes-parked-at-prague-airport-3001/",
                "user": {
                    "name": "Airport Creator",
                    "url": "https://www.pexels.com/@airport-creator",
                },
                "description": "Planes parked at Prague airport beside an aircraft runway",
                "video_files": [
                    {
                        "id": 7001,
                        "file_type": "video/mp4",
                        "width": 1920,
                        "height": 1080,
                        "link": "https://videos.pexels.example/airport.mp4",
                    }
                ],
            },
            {
                "id": 3002,
                "width": 1920,
                "height": 1080,
                "duration": 8,
                "url": "https://www.pexels.com/video/editor-at-video-editing-workstation-3002/",
                "user": {
                    "name": "Editing Creator",
                    "url": "https://www.pexels.com/@editing-creator",
                },
                "description": "Video editor working at an editing workstation with timeline footage",
                "video_files": [
                    {
                        "id": 7002,
                        "file_type": "video/mp4",
                        "width": 1920,
                        "height": 1080,
                        "link": "https://videos.pexels.example/editing.mp4",
                    }
                ],
            },
        ],
    }
    transport = FakeJSONTransport(payload)
    client = PlannedPexelsV2SearchClient(transport)

    result = client.search_and_rank_once(
        api_key="secret",
        plan=plan,
        request=request,
        visual_direction=direction,
        weights=VisualRankingWeights.from_policy(policy),
        risk_penalties=VisualRiskPenalties.from_policy(policy),
        thresholds=VisualScoreThresholds.from_policy(policy),
        previous_scene="native workflow diagram",
        next_scene="restrained synchronized hero transition",
        allow_provider_search_review_floor=True,
    )

    assert len(transport.calls) == 1 and client.search_flow_count == 1
    assert result.ranking.selected_candidate_id == "pexels-3002"
    assert result.selected_candidate is not None
    assert result.selected_candidate.candidate_id == "pexels-3002"
    rejected = {
        item.candidate_id: item.reason_codes
        for item in result.ranking.rejected_candidates
    }
    assert "PEXELS_METADATA_OUT_OF_DOMAIN" in rejected["pexels-3001"]
    assert (
        "PEXELS_METADATA_REQUIRED_POST_PRODUCTION_OR_WORKSTATION_SIGNAL_MISSING"
        in rejected["pexels-3001"]
    )
    assert "pexels-3002" not in rejected
    gate = result.scoring_basis["metadata_semantic_hard_gate"]
    assert gate["status"] == "APPLIED"
    assert [item["verdict"] for item in gate["candidate_decisions"]] == [
        "BLOCK",
        "ELIGIBLE_FOR_RANKING",
    ]
    assert gate["candidate_decisions"][0]["out_of_domain_metadata_matches"] == [
        "airport",
        "planes",
    ]
    assert "workstation" in gate["candidate_decisions"][1][
        "positive_metadata_matches"
    ]


def test_planned_pexels_physical_gate_requires_set_metadata_and_blocks_screens():
    policy, direction, request, plan = _physical_production_visual_inputs()
    payload = {
        "page": 1,
        "per_page": 3,
        "videos": [
            {
                "id": 4101,
                "width": 1920,
                "height": 1080,
                "duration": 8,
                "url": "https://www.pexels.com/video/film-crew-at-computer-4101/",
                "user": {
                    "name": "Screen Creator",
                    "url": "https://www.pexels.com/@screen-creator",
                },
                "description": (
                    "Film crew beside studio lighting watches a screen, computer, "
                    "monitor, laptop, software interface UI, phone, TV, Apple iMac "
                    "and logo"
                ),
                "logo_or_text_present": False,
                "brand_or_trademark_present": False,
                "video_files": [
                    {
                        "id": 8101,
                        "file_type": "video/mp4",
                        "width": 1920,
                        "height": 1080,
                        "link": "https://videos.pexels.example/screen.mp4",
                    }
                ],
            },
            {
                "id": 4102,
                "width": 1920,
                "height": 1080,
                "duration": 8,
                "url": "https://www.pexels.com/video/film-crew-studio-lighting-4102/",
                "user": {
                    "name": "Physical Creator",
                    "url": "https://www.pexels.com/@physical-creator",
                },
                "description": (
                    "Behind the scenes film crew adjusts studio lighting around "
                    "a cinema camera on a tripod"
                ),
                "logo_or_text_present": False,
                "brand_or_trademark_present": False,
                "video_files": [
                    {
                        "id": 8102,
                        "file_type": "video/mp4",
                        "width": 1920,
                        "height": 1080,
                        "link": "https://videos.pexels.example/physical.mp4",
                    }
                ],
            },
            {
                "id": 4103,
                "width": 1920,
                "height": 1080,
                "duration": 8,
                "url": "https://www.pexels.com/video/empty-warehouse-4103/",
                "user": {
                    "name": "Warehouse Creator",
                    "url": "https://www.pexels.com/@warehouse-creator",
                },
                "description": "An empty industrial warehouse with plain walls",
                "logo_or_text_present": False,
                "brand_or_trademark_present": False,
                "video_files": [
                    {
                        "id": 8103,
                        "file_type": "video/mp4",
                        "width": 1920,
                        "height": 1080,
                        "link": "https://videos.pexels.example/warehouse.mp4",
                    }
                ],
            },
        ],
    }
    transport = FakeJSONTransport(payload)
    client = PlannedPexelsV2SearchClient(transport)

    result = client.search_and_rank_once(
        api_key="secret",
        plan=plan,
        request=request,
        visual_direction=direction,
        weights=VisualRankingWeights.from_policy(policy),
        risk_penalties=VisualRiskPenalties.from_policy(policy),
        thresholds=VisualScoreThresholds.from_policy(policy),
        previous_scene="native workflow diagram",
        next_scene="restrained synchronized hero transition",
        allow_provider_search_review_floor=True,
    )

    assert len(transport.calls) == 1 and client.search_flow_count == 1
    assert result.ranking.selected_candidate_id == "pexels-4102"
    rejected = {
        item.candidate_id: item.reason_codes
        for item in result.ranking.rejected_candidates
    }
    assert "PEXELS_METADATA_SCREEN_DEVICE_UI_OR_LOGO_CONFLICT" in rejected[
        "pexels-4101"
    ]
    assert (
        "PEXELS_METADATA_REQUIRED_PHYSICAL_PRODUCTION_SIGNAL_MISSING"
        in rejected["pexels-4103"]
    )
    assert "pexels-4102" not in rejected
    gate = result.scoring_basis["metadata_semantic_hard_gate"]
    assert gate["request_domain"] == "SCREEN_FREE_PHYSICAL_PRODUCTION"
    assert gate["policy_ref"].endswith("/screen-free-physical-production/v1")
    decisions = {
        item["candidate_id"]: item for item in gate["candidate_decisions"]
    }
    assert decisions["pexels-4102"]["verdict"] == "ELIGIBLE_FOR_RANKING"
    assert "studio lighting" in decisions["pexels-4102"][
        "required_physical_metadata_matches"
    ]
    assert decisions["pexels-4101"][
        "forbidden_screen_device_ui_logo_matches"
    ] == ["computer"]


def test_planned_pexels_rejects_non_v2_or_tampered_plan_before_transport():
    policy, direction, request, plan = _visual_inputs()
    transport = FakeJSONTransport({"videos": []})
    client = PlannedPexelsV2SearchClient(transport)
    tampered = plan.model_copy(update={"queries": ["tampered", *plan.queries[1:]]})

    with pytest.raises(ValueError, match="PLAN_HASH_MISMATCH"):
        client.search_and_rank_once(
            api_key="secret",
            plan=tampered,
            request=request,
            visual_direction=direction,
            weights=VisualRankingWeights.from_policy(policy),
            risk_penalties=VisualRiskPenalties.from_policy(policy),
            thresholds=VisualScoreThresholds.from_policy(policy),
        )
    assert transport.calls == [] and client.search_flow_count == 0
