from fastapi.testclient import TestClient

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
