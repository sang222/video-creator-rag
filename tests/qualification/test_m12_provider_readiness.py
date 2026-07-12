from __future__ import annotations

from app.core.config import Settings
from app.services.m12 import PROVIDER_ORDER, ProviderReadinessService


def test_readiness_provider_order_contains_only_current_integrations():
    assert PROVIDER_ORDER == (
        "ollama",
        "youtube-public",
        "youtube-owner",
        "google-drive",
        "elevenlabs",
        "google_veo",
    )


def test_readiness_read_does_not_call_providers(db_session):
    result = ProviderReadinessService(db_session, Settings()).readiness()
    assert result.technical_appendix["no_provider_calls_on_get"] is True
    keys = {item.provider_key for item in result.provider_summaries}
    assert {"elevenlabs", "google_veo"} <= keys


def test_settings_have_no_generic_cloud_renderer_selection():
    fields = Settings.model_fields
    assert "cloud_final_assembly_renderer" not in fields
    assert "cloud_template_renderer" not in fields
    assert "cloud_final_renderer_provider" not in fields
