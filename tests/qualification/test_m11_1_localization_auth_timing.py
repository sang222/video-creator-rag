from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.contracts.m7 import PublishHandoffCreate
from app.core.config import get_settings
from app.db.models import CloudMediaRef, OperatorAuthSession, OperatorUser
from app.main import create_app
from app.services import PublishHandoffService


def _auth_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("VCOS_DASHBOARD_AUTH_ENABLED", "true")
    monkeypatch.setenv("VCOS_AUTH_MODE", "local_password")
    monkeypatch.setenv("VCOS_BOOTSTRAP_ADMIN_EMAIL", "admin@local.vcos")
    monkeypatch.setenv("VCOS_BOOTSTRAP_ADMIN_PASSWORD", "correct-local-password")
    monkeypatch.setenv("VCOS_BOOTSTRAP_ADMIN_ROLE", "OWNER_ADMIN")
    get_settings.cache_clear()
    return TestClient(create_app())


def test_m11_1_auth_bootstraps_hashes_and_uses_http_only_cookie(db_session, monkeypatch) -> None:
    client = _auth_client(monkeypatch)

    login = client.post("/auth/login", json={"email": "admin@local.vcos", "password": "correct-local-password"})
    assert login.status_code == 200, login.text
    assert db_session.query(OperatorUser).count() == 1

    user = db_session.scalars(select(OperatorUser)).one()
    assert user.password_hash != "correct-local-password"
    assert user.password_hash.startswith("pbkdf2_sha256$")
    assert not hasattr(user, "password")

    default_login = client.post("/auth/login", json={"email": "admin@local.vcos", "password": "admin"})
    assert default_login.status_code == 401
    assert login.json()["user"]["role"] == "OWNER_ADMIN"
    assert "httponly" in login.headers["set-cookie"].lower()

    me = client.get("/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["email"] == "admin@local.vcos"

    logout = client.post("/auth/logout")
    assert logout.status_code == 200
    db_session.expire_all()
    assert db_session.query(OperatorAuthSession).filter(OperatorAuthSession.revoked_at.isnot(None)).count() == 1


def test_m11_1_localization_packages_gate_and_drive_cta(db_session, qualification_factory) -> None:
    scope = qualification_factory.m2_project()
    cloud_ref = CloudMediaRef(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=scope.project.id,
        media_type="CAPTION",
        storage_provider="GOOGLE_DRIVE",
        drive_file_id="subtitle-es",
        web_view_link="https://drive.google.com/file/d/subtitle-es/view",
        file_name="subtitle.es.srt",
        upload_status="VERIFIED",
        verification_status="SIZE_VERIFIED",
        local_cleanup_status="CLEANED",
        uploaded_at=datetime.now(UTC),
    )
    db_session.add(cloud_ref)
    db_session.commit()
    client = TestClient(create_app())

    config = client.post(
        f"/channels/{scope.channel.id}/localization-config",
        json={
            "primary_language": "en",
            "primary_region": "US",
            "primary_timezone": "America/New_York",
            "target_subtitle_languages": ["es"],
            "target_metadata_languages": ["de"],
            "target_regions": ["US"],
            "translation_mode": "HUMAN_REVIEW_REQUIRED",
            "localization_required_for_publish": True,
            "localized_metadata_required": True,
            "actor_role": "OWNER_ADMIN",
        },
    )
    assert config.status_code == 200, config.text
    assert config.json()["operator_summary"].startswith("Cấu hình localization")

    first_gate = client.post(f"/video-projects/{scope.project.id}/localization-readiness/check")
    assert first_gate.status_code == 200, first_gate.text
    assert first_gate.json()["result"] == "BLOCK"
    assert "Đang thiếu phụ đề" in first_gate.json()["operator_summary"]

    subtitle = client.post(
        f"/video-projects/{scope.project.id}/localized-subtitles",
        json={
            "source_language": "en",
            "target_language": "es",
            "srt_cloud_media_ref_id": str(cloud_ref.id),
            "translation_status": "APPROVED",
            "human_review_status": "APPROVED",
        },
    )
    assert subtitle.status_code == 200, subtitle.text
    assert subtitle.json()["google_drive_ctas"][0]["web_view_link"].startswith("https://drive.google.com/")
    assert "local_source_path" not in subtitle.text

    metadata = client.post(
        f"/video-projects/{scope.project.id}/localized-metadata",
        json={
            "language": "de",
            "localized_title": "VCOS Workflow",
            "localized_description": "Von einem Menschen geprüfte Beschreibung.",
            "localized_tags": ["workflow"],
            "human_review_status": "NEEDS_HUMAN_REVIEW",
        },
    )
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["operator_summary"].startswith("Metadata de cần người duyệt")

    second_gate = client.post(f"/video-projects/{scope.project.id}/localization-readiness/check")
    assert second_gate.status_code == 200, second_gate.text
    assert second_gate.json()["result"] == "BLOCK"
    assert second_gate.json()["unreviewed_metadata_languages"] == ["de"]


def test_m11_1_publish_timing_policy_and_suggestion(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    handoff = PublishHandoffService(db_session).create_from_render_package(
        data=PublishHandoffCreate(render_package_snapshot_id=flow.production_run.render_package_snapshot_id)
    )
    db_session.commit()
    client = TestClient(create_app())

    policy = client.post(
        f"/channels/{flow.channel.id}/publish-timing-policy",
        json={
            "primary_timezone": "America/New_York",
            "operator_timezone": "Asia/Ho_Chi_Minh",
            "target_regions": ["US"],
            "primary_audience_country": "US",
            "preferred_publish_windows": [
                {
                    "day_of_week": "MONDAY",
                    "local_time_start": "09:00",
                    "local_time_end": "11:00",
                    "timezone": "America/New_York",
                    "target_region": "US",
                }
            ],
            "publish_days": ["MONDAY"],
            "weekend_allowed": False,
            "notes": "Human configured window.",
        },
    )
    assert policy.status_code == 200, policy.text
    assert policy.json()["operator_summary"].startswith("Khung giờ publish đã cấu hình")

    suggestion = client.post(f"/publish-handoffs/{handoff.id}/publish-timing-suggestion")
    assert suggestion.status_code == 200, suggestion.text
    payload = suggestion.json()
    assert payload["source"] == "CHANNEL_CONFIG"
    assert payload["confidence_label"] == "CONFIGURED"
    assert payload["target_timezone"] == "America/New_York"
    assert payload["operator_timezone"] == "Asia/Ho_Chi_Minh"
    assert "auto-schedule" in payload["operator_summary"]


def test_m11_1_scope_guard_no_forbidden_routes() -> None:
    client = TestClient(create_app())
    forbidden_paths = [
        "/youtube/upload",
        "/youtube/publish",
        "/media/cloud-refs/download",
        "/media/cloud-refs/preview",
        "/videos/reupload-by-country",
    ]
    for path in forbidden_paths:
        assert client.post(path).status_code in {404, 405}
