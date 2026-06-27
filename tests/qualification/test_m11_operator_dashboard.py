from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import ApprovedPlaybookEntry, ChannelProfileVersion, CloudMediaRef, LearningReviewDecision
from app.main import create_app

from .test_m10_learning_review_queue import _import_metrics, _run_m9, _run_m10, _uploaded_video


def test_m11_command_center_and_provider_status_empty_state() -> None:
    client = TestClient(create_app())

    command = client.get("/dashboard/command-center")
    assert command.status_code == 200, command.text
    payload = command.json()
    assert {card["key"] for card in payload["cards"]} >= {
        "critical_queue",
        "ready_to_publish",
        "needs_youtube_auth",
        "needs_drive_auth",
        "learning_review",
    } - {"ready_to_publish"}
    assert any(item["key"] == "no_auto_publish" for item in payload["safety_warnings"])
    assert payload["technical_appendix"]["no_provider_calls"] is True

    queues = client.get("/dashboard/queues")
    assert queues.status_code == 200, queues.text
    assert "items" in queues.json()

    providers = client.get("/providers/status")
    assert providers.status_code == 200, providers.text
    assert providers.json()["integrations"]["cloud_final_renderer"]["state"] in {
        "MISSING_REQUIRED_GAP",
        "CONFIGURED",
    }


def test_m11_channel_lifecycle_is_human_decided(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="M11 Lifecycle")
    db_session.commit()
    client = TestClient(create_app())

    readonly = client.post(
        f"/channels/{scope.channel.id}/lifecycle-decision",
        json={"action": "PAUSE_DAILY_GENERATION", "actor_role": "READ_ONLY_OBSERVER"},
    )
    assert readonly.status_code == 403

    paused = client.post(
        f"/channels/{scope.channel.id}/lifecycle-decision",
        json={
            "action": "PAUSE_DAILY_GENERATION",
            "actor_role": "OWNER_ADMIN",
            "health_status": "WATCHLIST",
            "reason": "Pause requested by human operator.",
        },
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["lifecycle_state"] == "PAUSED"

    lifecycle = client.get(f"/channels/{scope.channel.id}/lifecycle")
    assert lifecycle.status_code == 200, lifecycle.text
    assert lifecycle.json()["daily_generation_allowed"] is False
    assert "PAUSED" in lifecycle.json()["next_action"]

    workspace = client.get(f"/channels/{scope.channel.id}/workspace")
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["health_summary"]["storage_state"] == "NO_CLOUD_MEDIA"


def test_m11_uploaded_video_dashboard_uses_drive_cta_only(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m11-drive")
    ref = CloudMediaRef(
        company_id=uploaded.company_id,
        channel_workspace_id=uploaded.channel_workspace_id,
        video_project_id=uploaded.video_project_id,
        uploaded_video_id=uploaded.id,
        render_package_id=uploaded.render_package_snapshot_id,
        media_type="LONG_FORM_FINAL",
        storage_provider="GOOGLE_DRIVE",
        drive_file_id="drive-file-m11",
        web_view_link="https://drive.google.com/file/d/drive-file-m11/view",
        file_name="final.mp4",
        size_bytes=1024,
        checksum_sha256="abc123",
        local_source_path_hash="hash-only",
        upload_status="VERIFIED",
        verification_status="SIZE_VERIFIED",
        local_cleanup_status="CLEANED",
        uploaded_at=datetime.now(UTC),
        source_refs=[{"type": "uploaded_video", "id": str(uploaded.id)}],
        technical_appendix={"fixture": True},
    )
    db_session.add(ref)
    db_session.commit()

    client = TestClient(create_app())
    response = client.get(f"/uploaded-videos/{uploaded.id}/dashboard")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["media"][0]["cta_label"] == "Open in Google Drive"
    assert payload["media"][0]["web_view_link"].startswith("https://drive.google.com/")
    serialized = response.text
    assert "local_source_path" not in serialized
    assert "web_content_link" not in serialized
    assert "preview_url" not in serialized


def test_m11_learning_approval_creates_audited_playbook_without_profile_mutation(
    db_session,
    qualification_factory,
    tmp_path,
) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m11-learning")
    _import_metrics(
        db_session,
        uploaded,
        metrics={
            "views": 120,
            "likes": 10,
            "comments": 4,
            "impressions": 6000,
            "click_through_rate": 0.08,
            "average_view_duration_seconds": 45,
            "average_view_percentage": 75,
            "watch_time_minutes": 90,
            "subscribers_gained": 3,
            "subscribers_lost": 0,
            "shares": 2,
        },
    )
    _run_m9(db_session, uploaded)
    _run_m10(db_session, uploaded)
    assert db_session.scalars(select(ApprovedPlaybookEntry)).first() is None
    profile_count_before = db_session.query(ChannelProfileVersion).count()
    db_session.commit()

    client = TestClient(create_app())
    candidates = client.get("/learning-candidates").json()
    learning_candidate_id = candidates[0]["id"]
    approved = client.post(
        f"/learning-candidates/{learning_candidate_id}/approve",
        json={"action": "APPROVE", "actor_role": "LEARNING_REVIEWER", "rationale": "Evidence reviewed."},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_playbook_entry_id"] is not None
    assert approved.json()["technical_appendix"]["no_channel_profile_mutation"] is True

    db_session.expire_all()
    assert db_session.query(LearningReviewDecision).count() == 1
    assert db_session.query(ApprovedPlaybookEntry).count() == 1
    assert db_session.query(ChannelProfileVersion).count() == profile_count_before
