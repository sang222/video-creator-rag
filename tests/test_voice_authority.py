from __future__ import annotations

from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.contracts.voice_authority import ProviderVoiceCandidate
from app.core.errors import ValidationFailureError
from app.services.voice_authority import (
    NarrationPerformanceGate,
    VoiceAuthorityService,
    compile_performance_beats,
    infer_narration_mode,
    validate_single_primary_narrator,
    voice_authority_required,
)


def _voice(*, model_id: str = "eleven_multilingual_v2") -> ProviderVoiceCandidate:
    return ProviderVoiceCandidate(
        voice_id="voice-us-01",
        display_name="US technical narrator",
        language_tags=["en"],
        locale_tags=["en-US"],
        accent_tags=["US-neutral"],
        narration_mode_fit=[
            "TECHNICAL_EXPLAINER",
            "ANALYTICAL",
            "TACTICAL",
            "CAUTIONARY",
        ],
        market_fit_tags=["US"],
        clarity_score=95,
        energy_score=75,
        warmth_score=70,
        authority_score=90,
        conversationality_score=80,
        approved_model_ids=[model_id],
        default_model_id=model_id,
        default_settings={
            "speed": 1.0,
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
        safe_setting_bounds={
            "speed": {"min": 0.85, "max": 1.10},
            "stability": {"min": 0.35, "max": 0.75},
            "similarity_boost": {"min": 0.65, "max": 0.90},
            "style": {"min": 0.0, "max": 0.20},
        },
        commercial_use_state="APPROVED",
        availability_state="AVAILABLE",
        priority=10,
        evidence_refs=["provider-catalog://voice-us-01"],
    )


def _script() -> tuple[str, list[dict[str, str]]]:
    sections = [
        {
            "section_id": "hook",
            "heading": "Hook",
            "narration": "A brittle workflow can look correct until the first real provider call fails.",
        },
        {
            "section_id": "problem",
            "heading": "Problem",
            "narration": "The failure is not the API itself. The failure is letting execution identity, retries, and provider state drift apart.",
        },
        {
            "section_id": "explanation",
            "heading": "Explanation",
            "narration": "A durable workflow freezes semantic authority first, then lets deterministic services own external effects and reconciliation.",
        },
        {
            "section_id": "example",
            "heading": "Example",
            "narration": "For narration, the same primary speaker can stay fixed while delivery changes by semantic beat instead of random voice switching.",
        },
        {
            "section_id": "warning",
            "heading": "Warning",
            "narration": "If an external outcome is uncertain, the system must reconcile the exact effect rather than spend again under a new identity.",
        },
        {
            "section_id": "conclusion",
            "heading": "Conclusion",
            "narration": "That separation keeps the channel recognizable while still allowing expressive, context-aware delivery.",
        },
    ]
    return "\n\n".join(section["narration"] for section in sections), sections


def test_provider_voice_candidate_rejects_out_of_bounds_default() -> None:
    payload = _voice().model_dump()
    payload["default_settings"]["speed"] = 1.2
    with pytest.raises(ValueError, match="VOICE_DEFAULT_SETTING_OUTSIDE_SAFE_BOUNDS"):
        ProviderVoiceCandidate.model_validate(payload)


def test_performance_compiler_covers_canonical_script_and_is_not_monotone() -> None:
    narration, sections = _script()
    beats = compile_performance_beats(
        canonical_narration=narration,
        script_sections=sections,
    )
    assert beats[0].source_text_start == 0
    assert beats[-1].source_text_end == len(narration)
    assert [beat.ordinal for beat in beats] == list(range(1, len(beats) + 1))
    assert len({beat.delivery_intent for beat in beats}) >= 3
    assert NarrationPerformanceGate.evaluate(
        narration=narration,
        beats=beats,
    ).passed


def test_tts_projection_groups_adjacent_delivery_intents_and_uses_context() -> None:
    narration, sections = _script()
    beats = compile_performance_beats(
        canonical_narration=narration,
        script_sections=sections,
    )
    segments = VoiceAuthorityService._compile_segments(
        beats=beats,
        voice=_voice(),
        capabilities={
            "supports_voice_settings": True,
            "supports_context_stitching": True,
            "supports_audio_tags": False,
            "max_characters": 10_000,
        },
    )
    assert len(segments) >= 2
    assert segments[0].source_text_start == 0
    assert segments[-1].source_text_end == len(narration)
    assert segments[0].next_text is not None
    assert segments[-1].previous_text is not None
    for segment in segments:
        assert 0.85 <= float(segment.voice_settings["speed"]) <= 1.10
        assert 0.35 <= float(segment.voice_settings["stability"]) <= 0.75
        assert 0.0 <= float(segment.voice_settings["style"]) <= 0.20


def test_performance_gate_rejects_monotony_across_multiple_functions() -> None:
    narration, sections = _script()
    beats = compile_performance_beats(
        canonical_narration=narration,
        script_sections=sections,
    )
    monotone = [
        beat.model_copy(
            update={
                "delivery_intent": "CLEAR_PRECISE",
                "energy": "CONTROLLED",
                "pace": "MEDIUM",
                "emphasis": "MEDIUM",
            }
        )
        for beat in beats
    ]
    result = NarrationPerformanceGate.evaluate(narration=narration, beats=monotone)
    assert not result.passed
    assert "NARRATION_MONOTONY_RISK" in result.reason_codes


def test_single_primary_narrator_guard() -> None:
    same = [
        SimpleNamespace(narration_voice_snapshot_id="snapshot-1"),
        SimpleNamespace(narration_voice_snapshot_id="snapshot-1"),
    ]
    validate_single_primary_narrator(same)
    with pytest.raises(ValidationFailureError, match="VOICE_SWITCH_WITHIN_VIDEO_FORBIDDEN"):
        validate_single_primary_narrator(
            [
                SimpleNamespace(narration_voice_snapshot_id="snapshot-1"),
                SimpleNamespace(narration_voice_snapshot_id="snapshot-2"),
            ]
        )


def test_voice_authority_policy_is_explicit_opt_in() -> None:
    assert voice_authority_required(
        SimpleNamespace(
            compiled_payload={
                "voice_authority_policy": {"required_for_real_production": True}
            }
        )
    )
    assert not voice_authority_required(SimpleNamespace(compiled_payload={}))


def test_narration_mode_is_semantic_and_deterministic() -> None:
    assert (
        infer_narration_mode(
            title="Build a durable workflow",
            canonical_narration="Here is how to build the workflow step by step.",
        )
        == "TACTICAL"
    )
    assert (
        infer_narration_mode(
            title="A critical risk",
            canonical_narration="This warning explains the failure mode.",
        )
        == "CAUTIONARY"
    )


def test_voice_migration_is_single_head() -> None:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert heads == ["0087_business_os"]
