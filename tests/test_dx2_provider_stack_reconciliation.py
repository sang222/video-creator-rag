from __future__ import annotations

from app.core.config import Settings
from app.services.dx2 import ProviderStackDriftGuard
from app.services.m2 import ProviderCapabilityMatrix, ProviderReadinessM2Service
from app.services.provider_stack import (
    CANONICAL_PROVIDER_KEYS,
    LOCAL_CAPABILITY_KEYS,
    OPTIONAL_STORAGE_PROVIDER_KEYS,
    provider_key_rejection_reasons,
)


def test_canonical_external_provider_stack_is_exact():
    assert CANONICAL_PROVIDER_KEYS == ("elevenlabs", "luma_api", "pexels_api")


def test_native_renderer_is_local_not_external_provider():
    assert LOCAL_CAPABILITY_KEYS == ("native_ffmpeg_renderer",)
    assert "native_ffmpeg_renderer" not in CANONICAL_PROVIDER_KEYS


def test_storage_and_manual_publish_integrations_stay_outside_media_provider_stack():
    assert OPTIONAL_STORAGE_PROVIDER_KEYS == ("youtube_readonly", "google_drive_archive")


def test_m2_readiness_contains_only_current_external_providers_plus_integrations():
    keys = {item.provider_key for item in ProviderReadinessM2Service(Settings()).snapshot().providers}
    assert set(CANONICAL_PROVIDER_KEYS) <= keys
    assert "native_ffmpeg_renderer" not in keys


def test_capability_matrix_keeps_native_renderer_local():
    matrix = {item.provider_key: item for item in ProviderCapabilityMatrix(Settings()).entries()}
    assert matrix["native_ffmpeg_renderer"].provider_type == "LOCAL_RENDERER_CAPABILITY"
    assert matrix["native_ffmpeg_renderer"].no_call_in_m2 is True


def test_unknown_provider_key_is_rejected_without_compatibility_alias():
    assert provider_key_rejection_reasons("retired-cloud-renderer") == ["UNKNOWN_PROVIDER_KEY"]


def test_default_catalogs_pass_provider_stack_drift_guard():
    result = ProviderStackDriftGuard().check()
    assert result.status == "PASS"
    assert result.expected_provider_keys == list(CANONICAL_PROVIDER_KEYS)
    assert result.stale_provider_keys == []
    assert result.no_provider_call_made is True
