from __future__ import annotations

from app.core.config import Settings
from app.services.provider_stack import provider_key_rejection_reasons
from app.services.r3d8 import EXECUTION_FLAG_BY_PROVIDER, PAID_PROVIDER_KEYS


def test_paid_provider_set_contains_only_current_paid_external_providers():
    assert PAID_PROVIDER_KEYS == {"elevenlabs", "luma_api"}


def test_execution_flags_preserve_current_provider_safety():
    assert EXECUTION_FLAG_BY_PROVIDER == {
        "elevenlabs": "elevenlabs_real_generation_enabled",
        "luma_api": "luma_real_generation_enabled",
        "pexels_api": "pexels_real_search_enabled",
        "google_drive_archive": "google_drive_real_archive_enabled",
    }


def test_all_provider_execution_defaults_are_off():
    settings = Settings()
    assert not settings.provider_real_execution_enabled
    assert not settings.elevenlabs_real_generation_enabled
    assert not settings.luma_real_generation_enabled
    assert not settings.pexels_real_search_enabled
    assert not settings.google_drive_real_archive_enabled


def test_unknown_provider_has_no_compatibility_path():
    assert provider_key_rejection_reasons("retired-cloud-renderer") == ["UNKNOWN_PROVIDER_KEY"]
