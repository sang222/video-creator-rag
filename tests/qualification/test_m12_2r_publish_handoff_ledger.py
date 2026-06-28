from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.contracts.m12_2r import BackfillUploadedVideoRequest
from app.core.config import Settings
from app.core.errors import ConflictError, ValidationFailureError
from app.db.models import FirstScriptedVideoPackage, UploadedVideo, UploadedVideoBackfillEvent
from app.main import create_app
from app.services.m12_2r import PublishHandoffLedgerService, parse_youtube_video_id


VALID_VIDEO_ID = "dQw4w9WgXcQ"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        youtube_public_monitor_enabled=False,
        youtube_owner_analytics_enabled=False,
    )


def _ready_package(db_session, scope, *, status: str = "READY_FOR_HUMAN_REVIEW") -> FirstScriptedVideoPackage:
    package = FirstScriptedVideoPackage(
        video_project_id=getattr(scope, "project", None).id if getattr(scope, "project", None) else None,
        channel_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        compiled_policy_snapshot_id=scope.snapshot.id,
        provider_readiness_snapshot_id=None,
        package_status=status,
        agent_run_refs=[],
        prompt_render_run_refs=[],
        prompt_audit_snapshot_refs=[],
        artifacts={
            "metadata_package": {"title": "M12.2R Ledger", "description": "Manual upload only."},
            "upload_card_copy": {"title": "M12.2R Ledger", "description": "Paste-ready YouTube copy."},
            "visual_plan": {"scenes": [{"kind": "CARD"}]},
            "human_review_checklist": {"final_human_review": "PENDING", "upload_card_copy_ready": True},
        },
        limitations=["No upload/publish API."],
        risk_limitations_summary={"upload_or_publish_calls_made": False},
        next_action="Human final approval required.",
    )
    db_session.add(package)
    db_session.flush()
    return package


def _task(db_session, qualification_factory):
    scope = qualification_factory.m2_project()
    package = _ready_package(db_session, scope)
    service = PublishHandoffLedgerService(db_session, settings=_settings())
    task = service.create_upload_task_from_package(package.id)
    return scope, package, task, service


def test_m12_2r_create_human_upload_task_from_ready_package_is_idempotent(db_session, qualification_factory) -> None:
    scope = qualification_factory.m2_project()
    package = _ready_package(db_session, scope)
    service = PublishHandoffLedgerService(db_session, settings=_settings())

    first = service.create_upload_task_from_package(package.id)
    second = service.create_upload_task_from_package(package.id)

    assert first.id == second.id
    assert first.status == "READY_FOR_HUMAN_UPLOAD"
    assert first.channel_id == scope.channel.id
    assert first.video_project_id == scope.project.id
    assert first.first_scripted_video_package_id == package.id
    assert first.destination == "YOUTUBE"
    assert first.title_snapshot == "M12.2R Ledger"
    assert first.required_assets


def test_m12_2r_cannot_create_upload_task_for_not_ready_package(db_session, qualification_factory) -> None:
    scope = qualification_factory.m2_project()
    package = _ready_package(db_session, scope, status="BLOCKED")
    service = PublishHandoffLedgerService(db_session, settings=_settings())

    with pytest.raises(ValidationFailureError):
        service.create_upload_task_from_package(package.id)


def test_m12_2r_start_upload_task_changes_status_only(db_session, qualification_factory) -> None:
    _, _, task, service = _task(db_session, qualification_factory)

    started = service.start_upload_task(task.id)

    assert started.status == "HUMAN_UPLOAD_IN_PROGRESS"
    assert db_session.query(UploadedVideo).count() == 0


@pytest.mark.parametrize(
    "value",
    [
        f"https://www.youtube.com/watch?v={VALID_VIDEO_ID}",
        f"https://youtu.be/{VALID_VIDEO_ID}",
        f"https://www.youtube.com/shorts/{VALID_VIDEO_ID}",
        VALID_VIDEO_ID,
    ],
)
def test_m12_2r_backfill_accepts_youtube_url_variants(db_session, qualification_factory, value: str) -> None:
    scope, package, task, service = _task(db_session, qualification_factory)

    result = service.backfill_uploaded_video(
        task_id=task.id,
        data=BackfillUploadedVideoRequest(
            youtube_url_or_video_id=value,
            actual_title="Actual YouTube title",
            actual_visibility="PUBLIC",
            thumbnail_uploaded=True,
            subtitles_uploaded=True,
        ),
    )

    uploaded = db_session.get(UploadedVideo, result.uploaded_video.id)
    assert result.parsed_video_id == VALID_VIDEO_ID
    assert result.task.status == "UPLOADED_UNVERIFIED"
    assert uploaded is not None
    assert uploaded.channel_workspace_id == scope.channel.id
    assert uploaded.video_project_id == scope.project.id
    assert uploaded.first_scripted_video_package_id == package.id
    assert uploaded.human_upload_task_id == task.id
    assert uploaded.platform_video_id == VALID_VIDEO_ID
    assert uploaded.verification_status == "VERIFICATION_UNAVAILABLE"
    assert uploaded.analytics_sync_status == "NOT_CONFIGURED"


def test_m12_2r_backfill_rejects_invalid_video_id(db_session, qualification_factory) -> None:
    _, _, task, service = _task(db_session, qualification_factory)

    with pytest.raises(ValidationFailureError):
        service.backfill_uploaded_video(
            task_id=task.id,
            data=BackfillUploadedVideoRequest(youtube_url_or_video_id="not a youtube id"),
        )

    event = db_session.scalars(select(UploadedVideoBackfillEvent)).one()
    assert event.parse_status == "INVALID"


def test_m12_2r_backfill_detects_duplicate_video_id(db_session, qualification_factory) -> None:
    scope, _, first_task, service = _task(db_session, qualification_factory)
    service.backfill_uploaded_video(
        task_id=first_task.id,
        data=BackfillUploadedVideoRequest(youtube_url_or_video_id=VALID_VIDEO_ID),
    )
    second_package = _ready_package(db_session, scope)
    second_task = service.create_upload_task_from_package(second_package.id)

    with pytest.raises(ConflictError):
        service.backfill_uploaded_video(
            task_id=second_task.id,
            data=BackfillUploadedVideoRequest(youtube_url_or_video_id=f"https://youtu.be/{VALID_VIDEO_ID}"),
        )

    duplicate_event = db_session.scalars(
        select(UploadedVideoBackfillEvent).where(UploadedVideoBackfillEvent.parse_status == "DUPLICATE")
    ).one()
    assert duplicate_event.parsed_video_id == VALID_VIDEO_ID


def test_m12_2r_verify_missing_credentials_is_safe_unavailable(db_session, qualification_factory) -> None:
    _, _, task, service = _task(db_session, qualification_factory)
    result = service.backfill_uploaded_video(
        task_id=task.id,
        data=BackfillUploadedVideoRequest(youtube_url_or_video_id=VALID_VIDEO_ID),
    )

    verification = service.verify_uploaded_video(result.uploaded_video.id)

    assert verification.verification_status == "VERIFICATION_UNAVAILABLE"
    assert verification.analytics_sync_status == "NOT_CONFIGURED"
    assert verification.technical_appendix["provider_calls_made"] is False
    assert verification.technical_appendix["no_metrics_invented"] is True


def test_m12_2r_lists_uploaded_videos_and_publish_ledger_counts(db_session, qualification_factory) -> None:
    scope, _, task, service = _task(db_session, qualification_factory)
    service.start_upload_task(task.id)
    service.backfill_uploaded_video(
        task_id=task.id,
        data=BackfillUploadedVideoRequest(youtube_url_or_video_id=VALID_VIDEO_ID),
    )

    uploaded = service.list_uploaded_videos(channel_id=scope.channel.id)
    ledger = service.publish_ledger(scope.channel.id)

    assert len(uploaded.uploaded_videos) == 1
    assert ledger.uploaded_count == 1
    assert ledger.waiting_verification_count == 1
    assert ledger.analytics_not_configured_count == 1
    assert ledger.need_upload_count == 0


def test_m12_2r_parser_normalizes_and_rejects_invalid_values() -> None:
    assert parse_youtube_video_id(f"https://www.youtube.com/watch?v={VALID_VIDEO_ID}&ab_channel=VCOS") == VALID_VIDEO_ID
    assert parse_youtube_video_id(f"https://youtu.be/{VALID_VIDEO_ID}?si=abc") == VALID_VIDEO_ID
    assert parse_youtube_video_id(f"https://www.youtube.com/shorts/{VALID_VIDEO_ID}") == VALID_VIDEO_ID
    assert parse_youtube_video_id(VALID_VIDEO_ID) == VALID_VIDEO_ID
    with pytest.raises(ValidationFailureError):
        parse_youtube_video_id("https://example.com/watch?v=dQw4w9WgXcQ")


def test_m12_2r_api_routes_have_no_upload_publish_api_and_no_local_paths(db_session, qualification_factory) -> None:
    scope, package, task, _ = _task(db_session, qualification_factory)
    db_session.commit()
    client = TestClient(create_app())

    routes = client.get("/openapi.json").json()["paths"]
    assert "/channels/{channel_id}/upload-tasks" in routes
    assert "/video-packages/{package_id}/upload-task" in routes
    assert "/upload-tasks/{task_id}/backfill-uploaded-video" in routes
    assert "/uploaded-videos/{uploaded_video_id}/verify" in routes
    forbidden_routes = [path for path in routes if "youtube-upload" in path or "publish-now" in path or "reupload" in path]
    assert forbidden_routes == []

    listed = client.get(f"/channels/{scope.channel.id}/upload-tasks")
    created = client.post(f"/video-packages/{package.id}/upload-task")
    backfilled = client.post(
        f"/upload-tasks/{task.id}/backfill-uploaded-video",
        json={"youtube_url_or_video_id": VALID_VIDEO_ID},
    )
    assert listed.status_code == 200
    assert created.status_code == 200
    assert backfilled.status_code == 200
    payload = backfilled.text
    assert "/Users/" not in payload
    assert "file_path" not in payload


def test_m12_2r_cli_exposes_safe_commands_without_upload_publish() -> None:
    runner = CliRunner()
    help_text = runner.invoke(cli_app, ["--help"]).stdout
    task_help = runner.invoke(cli_app, ["upload-tasks", "--help"]).stdout
    videos_help = runner.invoke(cli_app, ["uploaded-videos", "--help"]).stdout

    assert "upload-tasks" in help_text
    assert "uploaded-videos" in help_text
    assert "backfill" in task_help
    assert "verify" in videos_help
    combined = f"{task_help}\n{videos_help}".lower()
    assert "publish-now" not in combined
    assert "schedule" not in combined
    assert "reupload" not in combined
