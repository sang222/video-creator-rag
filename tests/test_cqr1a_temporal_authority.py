from __future__ import annotations

import hashlib
import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.contracts.native_renderer import CanvasSpec, NativeRenderPlan, NativeRenderScene
from app.contracts.temporal_authority import (
    CanonicalMediaTimeline,
    EditorialSegmentInput,
    FinalNarrationAudio,
    ForcedAlignmentEvidence,
    NarrationTimingSeed,
    TextSpan,
)
from app.core.config import Settings
from app.main import create_app
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import canonical_plan_hash, stable_hash
from app.services.production_archive import CQR1A_REQUIRED_ARCHIVE_ROLES, LEGACY_REQUIRED_ARCHIVE_ROLES
from app.services.temporal_authority import (
    CanonicalMediaTimelineCompiler,
    CanonicalTimelineArtifactPersistenceService,
    ElevenLabsForcedAlignmentRequestBuilder,
    ElevenLabsForcedAlignmentResponseParser,
    ElevenLabsTimestampRequestBuilder,
    ElevenLabsTimingResponseParser,
    FixtureOnlyAlignmentTransport,
    NarrationAlignmentVerifier,
    NarrationAlignmentReconciler,
    SpokenTextNormalizer,
    TemporalAuthorityGate,
    elevenlabs_temporal_permission_readiness,
    fixture_alignment_response,
    run_cqr1a_fixture_rehearsal,
)


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_PA1R_HASHES = {
    "reports/pa1r_guarded_provider_smoke_report.md": "ebe6b0eafa6d1dc3c96d4182d4278f37ad9fa031a88c896fa3f0b00687977c74",
    "reports/pa1r_provider_smoke_human_review.md": "20cc3a63798726119b2d74ebf4b2062ccc24b3526c4f433d55f1dc8cd2f0402c",
    "reports/pa1r_summary.json": "1e274e1e0a6ad8bafe93f39cec210e8a7431fa145edc7c2e97851d965e3bfeb9",
}


def authority_components(source_text: str = "AI saves 12.5% for 3-5 teams at vcos.ai."):
    normalized = SpokenTextNormalizer().normalize(script_revision_id="script-rev-1", source_text=source_text)
    duration_ms = max(5_000, len(normalized.spoken_text) * 60)
    provider_raw, forced_raw = fixture_alignment_response(normalized, duration_ms=duration_ms)
    seed = ElevenLabsTimingResponseParser().parse(
        response=provider_raw,
        normalized=normalized,
        audio_asset_ref="fixture://final-narration.wav",
        audio_duration_ms=duration_ms,
        model_id="fixture-model",
        voice_id="fixture-voice",
    )
    forced = ElevenLabsForcedAlignmentResponseParser().parse(
        response=forced_raw,
        normalized=normalized,
        audio_asset_ref=seed.audio_asset_ref,
        audio_duration_ms=duration_ms,
    )
    verified = NarrationAlignmentReconciler().reconcile(
        normalized=normalized,
        timing_seed=seed,
        forced_alignment=forced,
        audio_asset_ref=seed.audio_asset_ref,
        audio_duration_ms=duration_ms,
    )
    timeline = CanonicalMediaTimelineCompiler().compile(
        project_id="fixture-project",
        package_id="fixture-package",
        channel_id="small-team-ai",
        script_revision_id=normalized.script_revision_id,
        spoken_text_revision_id=normalized.content_hash,
        tts_request_id="fixture-tts-request",
        normalized=normalized,
        alignment=verified,
        segments=[
            EditorialSegmentInput(
                segment_id="scene-1",
                editorial_span=TextSpan(start=0, end=len(source_text)),
                spoken_token_ids=[item.token_id for item in normalized.spoken_tokens],
            )
        ],
    )
    audio_payload = {"audio_asset_ref": seed.audio_asset_ref, "duration_ms": duration_ms, "is_final": True}
    audio = FinalNarrationAudio(**audio_payload, content_hash=stable_hash(audio_payload))
    return normalized, provider_raw, forced_raw, seed, forced, verified, timeline, audio


def strict_plan(tmp_path: Path, timeline: CanonicalMediaTimeline, **changes) -> NativeRenderPlan:
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nFixture\n", encoding="utf-8")
    segment = timeline.segments[0]
    scene = NativeRenderScene(
        scene_id=segment.segment_id,
        source_segment_ids=[segment.segment_id],
        narration_start_ms=segment.scene_start_ms,
        narration_end_ms=segment.scene_end_ms,
        duration_ms=segment.target_scene_duration_ms,
        visual_treatment="NATIVE_SLIDE",
        layout_type="TITLE",
        originality_role="HOOK",
    )
    payload = {
        "plan_id": "cqr1a-strict-plan",
        "plan_version": 1,
        "package_id": timeline.package_id,
        "video_project_id": timeline.project_id,
        "company_id": "fixture-company",
        "channel_id": timeline.channel_id,
        "channel_profile_version_id": "frozen-profile",
        "effective_context_snapshot_id": "frozen-context",
        "effective_context_hash": "frozen-context-hash",
        "format_identity_contract_ref": "format-ref",
        "format_identity_contract_hash": "format-hash",
        "episode_originality_manifest_ref": "originality-ref",
        "episode_originality_manifest_hash": "originality-hash",
        "script_ref": "script-ref",
        "script_hash": "script-hash",
        "srt_ref": str(srt),
        "srt_hash": hashlib.sha256(srt.read_bytes()).hexdigest(),
        "temporal_authority_mode": "CANONICAL_STRICT",
        "canonical_media_timeline_ref": f"canonical-timeline:{timeline.timeline_hash}",
        "canonical_media_timeline_hash": timeline.timeline_hash,
        "canonical_audio_asset_ref": timeline.audio_asset_id,
        "scene_timing_source": "CANONICAL_MEDIA_TIMELINE",
        "caption_timing_source": "CANONICAL_MEDIA_TIMELINE",
        "visual_plan_ref": "visual-plan-ref",
        "visual_plan_hash": "visual-plan-hash",
        "canvas_spec": CanvasSpec(width=1920, height=1080),
        "scenes": [scene],
        "audio_policy": {"narration_asset_ref": timeline.audio_asset_id},
        "caption_policy": {"timing_source": "CANONICAL_MEDIA_TIMELINE"},
        "output_profiles": ["YT_LONG_1080P30_SDR_H264_VT"],
        "production_eligible": False,
        "purpose": "CQR1A_LOCAL_FIXTURE_REHEARSAL",
        "status": "APPROVED",
        "created_at": datetime(2026, 7, 14, tzinfo=UTC),
        "created_by": "cqr1a-fixture",
    }
    payload.update(changes)
    plan = NativeRenderPlan(**payload)
    plan.content_hash = canonical_plan_hash(plan)
    return plan


def test_spoken_normalization_is_deterministic_versioned_and_hash_stable():
    normalizer = SpokenTextNormalizer()
    kwargs = {
        "script_revision_id": "rev-1",
        "source_text": "VCOS costs $12.50 on 2026-07-14.",
        "pronunciation_dictionary": {"VCOS": "V C O S"},
        "pronunciation_dictionary_refs": ["dict-v1"],
    }
    first = normalizer.normalize(**kwargs)
    second = normalizer.normalize(**kwargs)
    assert first == second
    assert first.content_hash == second.content_hash
    assert first.normalization_version == "spoken-text-normalizer/en-v1.0.0"


def test_all_source_characters_and_spoken_tokens_are_mapped_with_traceable_operations():
    normalized, *_ = authority_components()
    cursor = 0
    for mapping in normalized.source_to_spoken_spans:
        assert mapping.source_span.start == cursor
        cursor = mapping.source_span.end
    assert cursor > 0
    assert all(item.source_span and item.spoken_span for item in normalized.normalization_operations)
    assert all(item.source_spans for item in normalized.spoken_tokens)
    assert all(item.whitelisted for item in normalized.normalization_operations)


@pytest.mark.parametrize(
    "source,operation",
    [
        ("AI helps.", "ACRONYM_PRONUNCIATION"),
        ("It costs $1,250.50.", "CURRENCY_VERBALIZATION"),
        ("Ship on 2026-07-14.", "DATE_VERBALIZATION"),
        ("Version 2.5 ships.", "NUMBER_VERBALIZATION"),
        ("Gain 12.5% now.", "PERCENTAGE_VERBALIZATION"),
        ("Use 3-5 steps.", "NUMBER_RANGE_VERBALIZATION"),
        ("Visit vcos.ai now.", "URL_PRONUNCIATION"),
    ],
)
def test_required_normalization_cases(source: str, operation: str):
    normalized = SpokenTextNormalizer().normalize(script_revision_id="rev", source_text=source)
    assert operation in {item.operation_type for item in normalized.normalization_operations}


def test_pronunciation_dictionary_mapping_is_explicit():
    normalized = SpokenTextNormalizer().normalize(
        script_revision_id="rev",
        source_text="VCOS works.",
        pronunciation_dictionary={"VCOS": "vee coss"},
        pronunciation_dictionary_refs=["approved-dict"],
    )
    assert normalized.spoken_text.startswith("vee coss")
    assert normalized.pronunciation_dictionary_refs == ["approved-dict"]


def test_ambiguous_normalization_and_semantic_deletion_block():
    with pytest.raises(ValueError, match="AMBIGUOUS_NORMALIZATION"):
        SpokenTextNormalizer().normalize(script_revision_id="rev", source_text="Use approx. 5 steps.")
    with pytest.raises(ValueError, match="SEMANTIC_TEXT_DELETION_BLOCKED"):
        SpokenTextNormalizer().normalize(
            script_revision_id="rev",
            source_text="VCOS works.",
            pronunciation_dictionary={"VCOS": ""},
        )


def test_provider_timestamp_contract_parses_normalized_alignment_but_is_not_final_authority():
    normalized, _, _, seed, _, verified, timeline, _ = authority_components()
    request = ElevenLabsTimestampRequestBuilder().build(
        normalized=normalized,
        voice_id="voice",
        model_id="model",
    )
    assert request["endpoint_semantics"] == "CONVERT_WITH_TIMESTAMPS"
    assert request["payload"]["text"] == normalized.spoken_text
    assert request["payload"]["apply_text_normalization"] == "off"
    assert request["provider_call_made"] is False and request["transport_enabled"] is False
    assert seed.timing_available and seed.normalized_character_alignment
    assert not hasattr(seed, "verified_words")
    assert verified.provider_seed_ref.endswith(seed.content_hash)
    assert timeline.verified_alignment_ref.endswith(verified.content_hash)


def test_forced_alignment_request_fixture_transport_and_response_parser():
    normalized, _, forced_raw, seed, *_ = authority_components()
    request = ElevenLabsForcedAlignmentRequestBuilder().build(audio_asset_ref=seed.audio_asset_ref, normalized=normalized)
    transport = FixtureOnlyAlignmentTransport()
    fixture_with_official_shape = {
        **forced_raw,
        "characters": [{"text": "A", "start": 0.0, "end": 0.01}],
        "loss": 0.01,
    }
    response = transport.execute(request=request, fixture_response=fixture_with_official_shape)
    verifier = NarrationAlignmentVerifier()
    evidence = verifier.parse_evidence(
        response=response,
        normalized=normalized,
        audio_asset_ref=seed.audio_asset_ref,
        audio_duration_ms=seed.audio_duration_ms,
    )
    assert evidence.verification_status == "PASS"
    assert all(item.source_spoken_token_ids for item in evidence.words)
    assert evidence.characters[0].character == "A" and evidence.alignment_loss == 0.01
    assert transport.provider_call_made is False and transport.network_call_made is False


def test_missing_provider_or_forced_alignment_blocks_strict_reconciliation():
    normalized, _, _, seed, forced, *_ = authority_components()
    reconciler = NarrationAlignmentReconciler()
    without_provider = reconciler.reconcile(
        normalized=normalized,
        timing_seed=None,
        forced_alignment=forced,
        audio_asset_ref=seed.audio_asset_ref,
        audio_duration_ms=seed.audio_duration_ms,
    )
    without_forced = reconciler.reconcile(
        normalized=normalized,
        timing_seed=seed,
        forced_alignment=None,
        audio_asset_ref=seed.audio_asset_ref,
        audio_duration_ms=seed.audio_duration_ms,
    )
    assert without_provider.verification_status == "BLOCK"
    assert "TEMPORAL_PROVIDER_TIMING_MISSING" in without_provider.reconciliation_reason_codes
    assert without_forced.verification_status == "BLOCK"
    assert "TEMPORAL_FORCED_ALIGNMENT_MISSING" in without_forced.reconciliation_reason_codes


def test_missing_and_extra_spoken_words_block_coverage():
    normalized, _, forced_raw, seed, *_ = authority_components()
    missing_raw = {**forced_raw, "words": forced_raw["words"][:-1]}
    extra_raw = {**forced_raw, "words": [*forced_raw["words"], {"text": "surprise", "start": seed.audio_duration_ms / 1000 - 0.05, "end": seed.audio_duration_ms / 1000, "type": "word"}]}
    parser = ElevenLabsForcedAlignmentResponseParser()
    missing = parser.parse(response=missing_raw, normalized=normalized, audio_asset_ref=seed.audio_asset_ref, audio_duration_ms=seed.audio_duration_ms)
    extra = parser.parse(response=extra_raw, normalized=normalized, audio_asset_ref=seed.audio_asset_ref, audio_duration_ms=seed.audio_duration_ms)
    assert missing.verification_status == "BLOCK" and missing.missing_tokens
    assert extra.verification_status == "BLOCK" and extra.extra_words == ["surprise"]


def test_whitelisted_orthographic_difference_passes_and_is_recorded():
    normalized, _, forced_raw, seed, *_ = authority_components("Hello world.")
    words = [dict(item) for item in forced_raw["words"]]
    words[0]["text"] = "Hello,"
    evidence = ElevenLabsForcedAlignmentResponseParser().parse(
        response={**forced_raw, "words": words},
        normalized=normalized,
        audio_asset_ref=seed.audio_asset_ref,
        audio_duration_ms=seed.audio_duration_ms,
    )
    verified = NarrationAlignmentReconciler().reconcile(
        normalized=normalized,
        timing_seed=seed,
        forced_alignment=evidence,
        audio_asset_ref=seed.audio_asset_ref,
        audio_duration_ms=seed.audio_duration_ms,
    )
    assert evidence.verification_status == "PASS"
    assert verified.verification_status == "PASS"
    assert {item["reason_code"] for item in verified.normalization_only_differences} == {"WHITELISTED_ORTHOGRAPHIC_DIFFERENCE"}


def test_non_monotonic_and_out_of_audio_word_times_block_in_parser():
    normalized, _, forced_raw, seed, *_ = authority_components()
    non_monotonic_words = [dict(item) for item in forced_raw["words"]]
    non_monotonic_words[1]["start"] = -0.001
    with pytest.raises(ValueError, match="NON_MONOTONIC|BOUNDS"):
        ElevenLabsForcedAlignmentResponseParser().parse(
            response={**forced_raw, "words": non_monotonic_words},
            normalized=normalized,
            audio_asset_ref=seed.audio_asset_ref,
            audio_duration_ms=seed.audio_duration_ms,
        )
    out_of_bounds = [dict(item) for item in forced_raw["words"]]
    out_of_bounds[-1]["end"] = seed.audio_duration_ms / 1000 + 0.1
    with pytest.raises(ValueError, match="BOUNDS"):
        ElevenLabsForcedAlignmentResponseParser().parse(
            response={**forced_raw, "words": out_of_bounds},
            normalized=normalized,
            audio_asset_ref=seed.audio_asset_ref,
            audio_duration_ms=seed.audio_duration_ms,
        )


def test_high_alignment_conflict_blocks_and_reconciliation_is_deterministic():
    normalized, _, _, seed, forced, *_ = authority_components()
    previous = forced.words[-2]
    last = forced.words[-1]
    shifted_start = max(previous.start_ms, last.start_ms - 300)
    shifted = last.model_copy(update={"start_ms": shifted_start, "end_ms": last.end_ms - 300})
    conflicting = forced.model_copy(update={"words": [*forced.words[:-1], shifted]})
    reconciler = NarrationAlignmentReconciler()
    kwargs = dict(normalized=normalized, timing_seed=seed, forced_alignment=conflicting, audio_asset_ref=seed.audio_asset_ref, audio_duration_ms=seed.audio_duration_ms)
    first = reconciler.reconcile(**kwargs)
    second = reconciler.reconcile(**kwargs)
    assert first == second
    assert first.verification_status == "BLOCK"
    assert "TEMPORAL_HIGH_ALIGNMENT_CONFLICT" in first.reconciliation_reason_codes


def test_verified_alignment_requires_exactly_one_hundred_percent_token_coverage():
    normalized, _, _, _, _, verified, *_ = authority_components()
    assert verified.verification_status == "PASS"
    assert verified.token_coverage == 1.0
    assert {token for word in verified.verified_words for token in word.source_spoken_token_ids} == {item.token_id for item in normalized.spoken_tokens}


def test_canonical_timeline_hash_and_scene_timing_are_deterministic_and_audio_derived():
    normalized, _, _, _, _, verified, timeline, _ = authority_components()
    rebuilt = CanonicalMediaTimelineCompiler().compile(
        project_id=timeline.project_id,
        package_id=timeline.package_id,
        channel_id=timeline.channel_id,
        script_revision_id=timeline.script_revision_id,
        spoken_text_revision_id=timeline.spoken_text_revision_id,
        tts_request_id=timeline.tts_request_id,
        normalized=normalized,
        alignment=verified,
        segments=[EditorialSegmentInput(segment_id="scene-1", editorial_span=timeline.segments[0].editorial_span, spoken_token_ids=[item.token_id for item in normalized.spoken_tokens])],
    )
    segment = timeline.segments[0]
    assert rebuilt.timeline_hash == timeline.timeline_hash
    assert segment.scene_start_ms == verified.verified_words[0].start_ms
    assert segment.scene_end_ms == timeline.audio_duration_ms
    assert segment.audio_end_ms == verified.verified_words[-1].end_ms
    assert segment.timing_source == "VERIFIED_NARRATION_ALIGNMENT"


def test_temporal_authority_gate_passes_and_rejects_estimate_duration_and_parallel_timeline():
    normalized, _, _, _, _, verified, timeline, audio = authority_components()
    gate = TemporalAuthorityGate()
    assert gate.evaluate(normalized=normalized, final_audio=audio, alignment=verified, timeline=timeline).gate_status == "PASS"

    bad_segment = timeline.segments[0].model_copy(update={"timing_source": "ESTIMATED"})
    estimated_payload = timeline.model_dump(mode="json", exclude={"timeline_hash"})
    estimated_payload["segments"] = [bad_segment.model_dump(mode="json")]
    estimated = timeline.model_copy(update={"segments": [bad_segment], "timeline_hash": stable_hash(estimated_payload)})
    estimated_result = gate.evaluate(normalized=normalized, final_audio=audio, alignment=verified, timeline=estimated)
    assert "TEMPORAL_SCENE_ESTIMATE_USED" in estimated_result.block_reasons

    duration_payload = timeline.model_dump(mode="json", exclude={"timeline_hash"})
    duration_payload["audio_duration_ms"] += 1000
    duration_mismatch = CanonicalMediaTimeline(**duration_payload, timeline_hash=stable_hash(duration_payload))
    duration_result = gate.evaluate(normalized=normalized, final_audio=audio, alignment=verified, timeline=duration_mismatch)
    assert "TEMPORAL_AUDIO_DURATION_MISMATCH" in duration_result.block_reasons

    parallel_payload = timeline.model_dump(mode="json", exclude={"timeline_hash"})
    parallel_payload["compilation_warnings"] = ["TEMPORAL_PARALLEL_TIMELINE_DETECTED"]
    parallel = CanonicalMediaTimeline(**parallel_payload, timeline_hash=stable_hash(parallel_payload))
    parallel_result = gate.evaluate(normalized=normalized, final_audio=audio, alignment=verified, timeline=parallel)
    assert "TEMPORAL_PARALLEL_TIMELINE_DETECTED" in parallel_result.block_reasons


def test_gate_rejects_missing_or_multiple_final_audio_authorities():
    normalized, _, _, _, _, verified, timeline, audio = authority_components()
    gate = TemporalAuthorityGate()
    missing = gate.evaluate(normalized=normalized, final_audio=None, alignment=verified, timeline=timeline)
    multiple = gate.evaluate(normalized=normalized, final_audio=[audio, audio.model_copy()], alignment=verified, timeline=timeline)
    assert "TEMPORAL_AUDIO_MISSING" in missing.block_reasons
    assert "TEMPORAL_MULTIPLE_FINAL_AUDIO_AUTHORITIES" in multiple.block_reasons


def test_native_render_plan_strict_mode_requires_ref_hash_and_matching_timeline(tmp_path: Path):
    *_, timeline, _ = authority_components()
    valid = strict_plan(tmp_path, timeline)
    manifest = NativeMotionCompiler().compile(valid, canonical_timeline=timeline)
    assert manifest.canonical_media_timeline_ref == valid.canonical_media_timeline_ref
    assert manifest.canonical_media_timeline_hash == timeline.timeline_hash
    missing = strict_plan(tmp_path, timeline, canonical_media_timeline_ref=None)
    with pytest.raises(ValueError, match="TEMPORAL_CANONICAL_TIMELINE_REQUIRED"):
        NativeMotionCompiler().compile(missing, canonical_timeline=timeline)


def test_renderer_compilation_blocks_hash_audio_scene_and_parallel_conflicts(tmp_path: Path):
    *_, timeline, _ = authority_components()
    compiler = NativeMotionCompiler()
    bad_hash = strict_plan(tmp_path, timeline, canonical_media_timeline_hash="wrong")
    with pytest.raises(ValueError, match="TEMPORAL_TIMELINE_HASH_MISMATCH"):
        compiler.compile(bad_hash, canonical_timeline=timeline)
    bad_audio = strict_plan(tmp_path, timeline, canonical_audio_asset_ref="other-audio")
    with pytest.raises(ValueError, match="TEMPORAL_AUDIO_ASSET_MISMATCH"):
        compiler.compile(bad_audio, canonical_timeline=timeline)
    bad_scene = strict_plan(tmp_path, timeline)
    bad_scene.scenes[0].narration_end_ms -= 1
    bad_scene.scenes[0].duration_ms -= 1
    bad_scene.content_hash = canonical_plan_hash(bad_scene)
    with pytest.raises(ValueError, match="TEMPORAL_SCENE_NOT_DERIVED_FROM_TIMELINE"):
        compiler.compile(bad_scene, canonical_timeline=timeline)
    parallel = strict_plan(tmp_path, timeline, parallel_timing_inputs=["legacy-caption-timeline"])
    with pytest.raises(ValueError, match="TEMPORAL_PARALLEL_TIMELINE_DETECTED"):
        compiler.compile(parallel, canonical_timeline=timeline)


def test_fixture_rehearsal_passes_without_network_provider_drive_youtube_or_domain_mutation(tmp_path: Path, monkeypatch):
    def forbidden_socket(*args, **kwargs):
        raise AssertionError("network socket forbidden")

    monkeypatch.setattr(socket, "socket", forbidden_socket)
    summary = run_cqr1a_fixture_rehearsal(tmp_path / "cqr1a-fixture")
    assert summary["gate_status"] == "PASS" and summary["token_coverage"] == 1.0
    assert summary["provider_call_made"] is False and summary["network_call_made"] is False
    assert summary["drive_call_made"] is False and summary["youtube_call_made"] is False
    assert summary["fixture_is_real_provider_verification"] is False
    assert summary["created_entities"] == {
        "FinalMediaRef": 0,
        "HumanUploadTask": 0,
        "UploadedVideo": 0,
        "ChannelProfileVersion": 0,
        "frozen_context": 0,
    }


def test_readiness_exposes_only_safe_booleans_and_defaults_permission_to_unknown():
    settings = Settings(
        _env_file=None,
        ELEVENLABS_API_KEY="secret-never-returned",
        ELEVENLABS_VOICE_ID="voice",
        ELEVENLABS_MODEL_ID="model",
    )
    result = elevenlabs_temporal_permission_readiness(settings)
    assert result == {
        "ELEVENLABS_TTS_CONFIGURED": True,
        "ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED": "unknown",
    }
    assert "secret-never-returned" not in json.dumps(result)


def test_generic_artifact_and_future_archive_roles_avoid_database_migration():
    registry = (ROOT / "config/artifact_type_registry.yaml").read_text(encoding="utf-8")
    assert "narration_timeline" in registry
    assert CanonicalTimelineArtifactPersistenceService.artifact_type == "narration_timeline"
    assert "CANONICAL_MEDIA_TIMELINE" in CQR1A_REQUIRED_ARCHIVE_ROLES
    assert "CANONICAL_MEDIA_TIMELINE" not in LEGACY_REQUIRED_ARCHIVE_ROLES


def test_read_only_evidence_routes_exist_and_no_action_route_is_added():
    routes = {(route.path, method) for route in create_app().routes for method in getattr(route, "methods", set())}
    assert ("/video-projects/{project_id}/temporal-authority", "GET") in routes
    assert ("/video-packages/{package_id}/canonical-media-timeline", "GET") in routes
    assert not any("temporal-authority" in path and method != "GET" for path, method in routes)


def test_historical_pa1r_evidence_is_byte_immutable():
    for relative_path, expected in HISTORICAL_PA1R_HASHES.items():
        actual = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert actual == expected
