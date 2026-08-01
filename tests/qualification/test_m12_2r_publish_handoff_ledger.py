from __future__ import annotations

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.contracts.m12_2r import BackfillUploadedVideoRequest
from app.core.actor import authenticated_actor_context
from app.core.config import Settings
from app.core.errors import ConflictError, ValidationFailureError
from app.db.models import (
    FirstScriptedVideoPackage,
    HumanUploadTask,
    UploadedVideo,
    UploadedVideoBackfillEvent,
)
from app.main import create_app
from app.services import EffectiveChannelRuntimeContextCompiler
from app.services.m12_2r import PublishHandoffLedgerService, parse_youtube_video_id


VALID_VIDEO_ID = "dQw4w9WgXcQ"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        youtube_public_monitor_enabled=False,
        youtube_owner_analytics_enabled=False,
    )


def _ready_package(
    db_session, scope, *, status: str = "READY_FOR_HUMAN_REVIEW"
) -> FirstScriptedVideoPackage:
    effective = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(
        scope.project.id
    )
    effective.publish_timing_context_json = {
        "channel_timezone": "Asia/Ho_Chi_Minh",
        "manual_publish_only": True,
        "configured_publish_window": {
            "windows": [{"day": "MONDAY", "start": "09:00", "end": "11:00"}]
        },
        "source_contract_paths": ["publish_timing"],
    }
    effective.thumbnail_style_context_json = {"style": "clear operator handoff"}
    db_session.flush()
    package = FirstScriptedVideoPackage(
        video_project_id=getattr(scope, "project", None).id
        if getattr(scope, "project", None)
        else None,
        channel_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        compiled_policy_snapshot_id=scope.snapshot.id,
        effective_context_snapshot_id=effective.id,
        effective_context_hash=effective.context_hash,
        provider_readiness_snapshot_id=None,
        package_status=status,
        agent_run_refs=[],
        prompt_render_run_refs=[],
        prompt_audit_snapshot_refs=[],
        artifacts={
            "hook_spec": {
                "hook_type": "DIRECT",
                "first_3_seconds_script": "M12.2R Ledger prepares manual upload only.",
                "first_3_seconds_visual": "Operator reviews a manual handoff card.",
                "promise_made": "M12.2R Ledger prepares manual upload only",
                "payoff_location": "S2",
                "clickbait_risk": "LOW",
            },
            "narration_script": {
                "sentences": [
                    {
                        "sentence_id": "S1",
                        "text": "M12.2R Ledger prepares manual upload only.",
                    },
                    {
                        "sentence_id": "S2",
                        "text": "M12.2R Ledger prepares manual upload only and never calls provider media or upload APIs.",
                    },
                ]
            },
            "metadata_package": {
                "title": "M12.2R Ledger",
                "description": "M12.2R Ledger prepares paste-ready YouTube copy for manual upload only.",
                "subtitle_refs": [
                    {"ref": "subtitle:final", "lifecycle_state": "FINAL"}
                ],
            },
            "thumbnail_brief": {
                "concept": "Operator ledger handoff",
                "text_overlay": "Manual only",
                "main_subject": "VCOS package review",
                "composition": "Clear dashboard crop",
                "mobile_readability_notes": "Short overlay.",
            },
            "visual_plan": {
                "scenes": [
                    {
                        "kind": "CARD",
                        "description": "Operator reviews a manual handoff card.",
                    }
                ]
            },
            "human_review_checklist": {
                "final_human_review": "PENDING",
                "metadata_package_ready": True,
            },
        },
        limitations=["No upload/publish API."],
        risk_limitations_summary={"upload_or_publish_calls_made": False},
        next_action="Human final approval required.",
    )
    db_session.add(package)
    db_session.flush()
    return package


def _legacy_upload_task(
    db_session,
    scope,
    package: FirstScriptedVideoPackage,
) -> HumanUploadTask:
    """Persist an archival v1 ledger row without reviving creation authority."""

    metadata = package.artifacts["metadata_package"]
    task = HumanUploadTask(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=scope.project.id,
        first_scripted_video_package_id=package.id,
        destination="YOUTUBE",
        target_platform="YOUTUBE",
        task_state="READY_FOR_HUMAN_UPLOAD",
        publish_metadata_ref=f"fixture://publish-metadata/{package.id}",
        title_snapshot=metadata["title"],
        description_snapshot=metadata["description"],
        thumbnail_ref=package.artifacts["thumbnail_brief"],
        subtitle_refs=metadata["subtitle_refs"],
        required_assets=[{"type": "LONG_FORM_FINAL"}],
        checklist=[{"code": "MANUAL_UPLOAD_ONLY", "required": True}],
    )
    db_session.add(task)
    db_session.flush()
    return task


def _task(db_session, qualification_factory):
    scope = qualification_factory.m2_project()
    package = _ready_package(db_session, scope)
    service = PublishHandoffLedgerService(db_session, settings=_settings())
    task = _legacy_upload_task(db_session, scope, package)
    return scope, package, task, service


def test_m12_2r_start_upload_task_uses_publish_prepare_permission(
    db_session, qualification_factory
) -> None:
    scope, _package, task, service = _task(db_session, qualification_factory)
    actor = authenticated_actor_context(
        canonical_user_id=scope.admin.id,
        operator_user_id=scope.admin.id,
        actor_role="company_admin",
        permissions={"publish.prepare"},
    )

    started = service.start_upload_task(task.id, actor=actor)

    assert started.status == "HUMAN_UPLOAD_IN_PROGRESS"


def test_m12_2r_start_upload_task_changes_status_only(
    db_session, qualification_factory
) -> None:
    _, _, task, service = _task(db_session, qualification_factory)

    started = service.start_upload_task(task.id)

    assert started.status == "HUMAN_UPLOAD_IN_PROGRESS"
    assert db_session.query(UploadedVideo).count() == 0


@pytest.mark.parametrize(
    "value",
    [
        f"https://www.youtube.com/watch?v={VALID_VIDEO_ID}",
        f"https://youtu.be/{VALID_VIDEO_ID}",
        VALID_VIDEO_ID,
    ],
)
def test_m12_2r_backfill_accepts_youtube_url_variants(
    db_session, qualification_factory, value: str
) -> None:
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


def test_m12_2r_backfill_rejects_invalid_video_id(
    db_session, qualification_factory
) -> None:
    _, _, task, service = _task(db_session, qualification_factory)

    with pytest.raises(ValidationFailureError):
        service.backfill_uploaded_video(
            task_id=task.id,
            data=BackfillUploadedVideoRequest(
                youtube_url_or_video_id="not a youtube id"
            ),
        )

    event = db_session.scalars(select(UploadedVideoBackfillEvent)).one()
    assert event.parse_status == "INVALID"


def test_m12_2r_backfill_detects_duplicate_video_id(
    db_session, qualification_factory
) -> None:
    scope, _, first_task, service = _task(db_session, qualification_factory)
    service.backfill_uploaded_video(
        task_id=first_task.id,
        data=BackfillUploadedVideoRequest(youtube_url_or_video_id=VALID_VIDEO_ID),
    )
    second_package = _ready_package(db_session, scope)
    second_task = _legacy_upload_task(db_session, scope, second_package)

    with pytest.raises(ConflictError):
        service.backfill_uploaded_video(
            task_id=second_task.id,
            data=BackfillUploadedVideoRequest(
                youtube_url_or_video_id=f"https://youtu.be/{VALID_VIDEO_ID}"
            ),
        )

    duplicate_event = db_session.scalars(
        select(UploadedVideoBackfillEvent).where(
            UploadedVideoBackfillEvent.parse_status == "DUPLICATE"
        )
    ).one()
    assert duplicate_event.parsed_video_id == VALID_VIDEO_ID


def test_m12_2r_verify_missing_credentials_is_safe_unavailable(
    db_session, qualification_factory
) -> None:
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


def test_m12_2r_lists_uploaded_videos_and_publish_ledger_counts(
    db_session, qualification_factory
) -> None:
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
    assert (
        parse_youtube_video_id(
            f"https://www.youtube.com/watch?v={VALID_VIDEO_ID}&ab_channel=VCOS"
        )
        == VALID_VIDEO_ID
    )
    assert (
        parse_youtube_video_id(f"https://youtu.be/{VALID_VIDEO_ID}?si=abc")
        == VALID_VIDEO_ID
    )
    assert parse_youtube_video_id(VALID_VIDEO_ID) == VALID_VIDEO_ID
    with pytest.raises(ValidationFailureError):
        parse_youtube_video_id("https://example.com/watch?v=dQw4w9WgXcQ")


def test_m12_2r_api_keeps_ledger_reads_without_legacy_package_routes() -> None:
    routes = {route.path for route in create_app().routes}
    assert "/channels/{channel_id}/upload-tasks" in routes
    assert "/uploaded-videos/{uploaded_video_id}/verify" in routes
    assert "/video-packages/{package_id}/upload-task" not in routes
    assert "/upload-tasks/{task_id}/backfill-uploaded-video" not in routes
    forbidden_routes = [
        path
        for path in routes
        if "youtube-upload" in path or "publish-now" in path or "reupload" in path
    ]
    assert forbidden_routes == []


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
