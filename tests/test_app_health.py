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
    monkeypatch.setenv("CLOUD_FINAL_RENDERER_API_KEY", "cloud-final-test")
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-test")
    monkeypatch.setenv("PIXABAY_API_KEY", "pixabay-test")
    monkeypatch.setenv("VCOS_AI_HERO_PROVIDER", "google_vertex_veo")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT_ID", "vcos-test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/absolute/path/to/service_account.json")
    monkeypatch.setenv("VCOS_VEO_MODEL", "veo-3.1-fast")
    monkeypatch.setenv("VCOS_VEO_COST_PER_SECOND_1080P", "0.10")
    monkeypatch.setenv("VCOS_VEO_MONTHLY_BUDGET_USD", "175")
    settings = get_settings()
    try:
        assert settings.elevenlabs_api_key is not None
        assert settings.elevenlabs_api_key.get_secret_value() == "eleven-test"
        assert settings.creatomate_api_key is not None
        assert settings.creatomate_api_key.get_secret_value() == "creatomate-test"
        assert settings.cloud_final_renderer_api_key is not None
        assert settings.cloud_final_renderer_api_key.get_secret_value() == "cloud-final-test"
        assert settings.pexels_api_key is not None
        assert settings.pexels_api_key.get_secret_value() == "pexels-test"
        assert settings.pixabay_api_key is not None
        assert settings.pixabay_api_key.get_secret_value() == "pixabay-test"
        assert settings.ai_hero_provider == "google_vertex_veo"
        assert settings.google_cloud_project_id == "vcos-test-project"
        assert settings.google_cloud_location == "us-central1"
        assert settings.google_application_credentials == "/absolute/path/to/service_account.json"
        assert settings.veo_model == "veo-3.1-fast"
        assert str(settings.veo_cost_per_second_1080p) == "0.10"
        assert str(settings.veo_monthly_budget_usd) == "175"
    finally:
        get_settings.cache_clear()
