from fastapi.testclient import TestClient

from app.core.config import VEO_DEFAULT_MODEL_ID
from app.core.config import get_settings
from app.main import create_app


def test_fastapi_app_boots() -> None:
    application = create_app()
    assert application.title == "VCOS"


def test_health_returns_ok_when_db_available() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"


def test_local_frontend_cors_preflight_allows_dashboard_api() -> None:
    client = TestClient(create_app())
    response = client.options(
        "/uploaded-videos",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "content-type" in response.headers["access-control-allow-headers"].lower()


def test_provider_api_keys_load_from_env(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("ELEVENLABS_API_KEY", "eleven-test")
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-test")
    monkeypatch.setenv("PIXABAY_API_KEY", "pixabay-test")
    monkeypatch.setenv("VCOS_AI_VIDEO_HERO_PROVIDER", "google_veo")
    monkeypatch.setenv("GEMINI_API_KEY", "google_veo-test")
    monkeypatch.setenv("VEO_MODEL_ID", VEO_DEFAULT_MODEL_ID)
    settings = get_settings()
    try:
        assert settings.elevenlabs_api_key is not None
        assert settings.elevenlabs_api_key.get_secret_value() == "eleven-test"
        assert settings.pexels_api_key is not None
        assert settings.pexels_api_key.get_secret_value() == "pexels-test"
        assert settings.pixabay_api_key is not None
        assert settings.pixabay_api_key.get_secret_value() == "pixabay-test"
        assert settings.ai_video_hero_provider == "google_veo"
        assert settings.gemini_api_key is not None
        assert settings.gemini_api_key.get_secret_value() == "google_veo-test"
        assert settings.veo_model_id == "veo-3.1-fast-generate-preview"
        assert settings.veo_model_id == VEO_DEFAULT_MODEL_ID
        assert settings.veo_default_duration_seconds == 8
        assert settings.veo_default_resolution == "720p"
        assert settings.veo_real_generation_enabled is False
        assert settings.pa1r_veo_smoke_enabled is False
    finally:
        get_settings.cache_clear()


def test_noncanonical_veo_model_env_alias_is_ignored(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.delenv("VEO_MODEL_ID", raising=False)
    monkeypatch.setenv("VCOS_VEO_MODEL", "ignored-noncanonical-value")
    try:
        assert get_settings().veo_model_id == VEO_DEFAULT_MODEL_ID
    finally:
        get_settings.cache_clear()
