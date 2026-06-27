from fastapi.testclient import TestClient

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


def test_provider_api_keys_load_from_env(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("ELEVENLABS_API_KEY", "eleven-test")
    monkeypatch.setenv("CREATOMATE_API_KEY", "creatomate-test")
    monkeypatch.setenv("CINEMATIC_AI_API_KEY", "cinematic-test")
    monkeypatch.setenv("CLOUD_FINAL_RENDERER_API_KEY", "cloud-final-test")
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-test")
    monkeypatch.setenv("PIXABAY_API_KEY", "pixabay-test")
    settings = get_settings()
    try:
        assert settings.elevenlabs_api_key is not None
        assert settings.elevenlabs_api_key.get_secret_value() == "eleven-test"
        assert settings.creatomate_api_key is not None
        assert settings.creatomate_api_key.get_secret_value() == "creatomate-test"
        assert settings.cinematic_ai_api_key is not None
        assert settings.cinematic_ai_api_key.get_secret_value() == "cinematic-test"
        assert settings.cloud_final_renderer_api_key is not None
        assert settings.cloud_final_renderer_api_key.get_secret_value() == "cloud-final-test"
        assert settings.pexels_api_key is not None
        assert settings.pexels_api_key.get_secret_value() == "pexels-test"
        assert settings.pixabay_api_key is not None
        assert settings.pixabay_api_key.get_secret_value() == "pixabay-test"
    finally:
        get_settings.cache_clear()
