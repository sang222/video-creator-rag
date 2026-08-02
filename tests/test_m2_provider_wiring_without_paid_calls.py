from __future__ import annotations

from app.core.config import Settings
from app.services.m2 import (
    ElevenLabsVoiceRequestBuilder,
    GoogleVeoRequestBuilder,
    PexelsSearchRequestBuilder,
    ProviderCapabilityMatrix,
    ProviderConfigRegistry,
    ProviderReadinessM2Service,
    validate_pexels_policy,
)


def test_provider_roles_bind_native_renderer_locally():
    roles = ProviderConfigRegistry(Settings()).configured_provider_by_role()
    assert roles["FINAL_ASSEMBLY_RENDERER"] == "native_ffmpeg_renderer"
    assert roles["TEMPLATE_RENDERER"] == "native_ffmpeg_renderer"


def test_m2_readiness_is_validation_only():
    snapshot = ProviderReadinessM2Service(Settings()).snapshot()
    assert snapshot.no_network_calls_made
    assert all(
        item.no_call_was_made and not item.real_network_probe_enabled
        for item in snapshot.providers
    )


def test_paid_provider_readiness_uses_machine_execution_authorization_label():
    settings = Settings(
        _env_file=None,
        elevenlabs_api_key="test-elevenlabs-key",
        elevenlabs_voice_id="voice-id",
        elevenlabs_model_id="eleven-model",
    )

    provider = ProviderReadinessM2Service(settings).provider_map()["elevenlabs"]

    assert provider.readiness_state == "READY_FOR_EXECUTION_AUTHORIZATION"
    assert "HUMAN" not in provider.readiness_state
    assert provider.no_call_was_made is True


def test_capability_matrix_has_current_external_providers_and_local_renderer():
    keys = {
        item.provider_key for item in ProviderCapabilityMatrix(Settings()).entries()
    }
    assert {
        "elevenlabs",
        "google_veo",
        "google_gemini_image",
        "pexels_api",
        "native_ffmpeg_renderer",
    } <= keys


def test_current_request_builders_never_execute():
    voice = ElevenLabsVoiceRequestBuilder(Settings()).build({"text": "hello"})
    hero = GoogleVeoRequestBuilder(Settings()).build(
        {"prompt": "abstract workflow", "duration_seconds": 4}
    )
    stock = PexelsSearchRequestBuilder(Settings()).build(
        {
            "query": "team office",
            "usage_role": "short_broll",
            "orientation": "landscape",
        }
    )
    assert all(
        item.will_execute is False and item.no_network_call_made
        for item in (voice, hero, stock)
    )


def test_google_veo_duration_over_eight_is_rejected():
    result = GoogleVeoRequestBuilder(Settings()).build(
        {"prompt": "abstract workflow", "duration_seconds": 10}
    )
    assert "VEO_DURATION_NOT_ALLOWED" in result.reason_codes


def test_stock_policy_preserves_supporting_only_boundary():
    assert "PEXELS_USAGE_ROLE_BLOCKED" in validate_pexels_policy(
        "factual_evidence", Settings(), {}
    )
    assert validate_pexels_policy("brief_broll", Settings(), {}) == []
