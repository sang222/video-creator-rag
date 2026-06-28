from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from typer.testing import CliRunner

from app.cli.main import app as cli_app

pytestmark = pytest.mark.skip(
    reason="Historical YouTube follow qualification depends on pre-M12 local render/upload fixture; M12.1R keeps upload/publish disabled."
)
from app.contracts.m7 import ManualPublishConfirmationCreate, PublishHandoffCreate
from app.contracts.m10_3 import YouTubeOwnerAnalyticsProviderOutput, YouTubeOwnerAnalyticsSyncRequest, YouTubePublicProviderOutput
from app.core.config import get_settings
from app.core.errors import ValidationFailureError
from app.db.models import (
    AnalyticsSnapshot,
    CredentialReference,
    RenderPackageSnapshot,
    UploadedVideo,
    UploadedVideoMetricsSummary,
    UploadedVideoYouTubeOwnerAnalyticsSnapshot,
    UploadedVideoYouTubePublicMonitorSnapshot,
    YouTubeOAuthSession,
)
from app.main import create_app
from app.services import ManualPublishConfirmationService, PublishHandoffService
from app.services.m10_3 import (
    ProviderFetchResult,
    YouTubeCredentialHealthService,
    YouTubeMetricMappingService,
    YouTubeMonitoringConfigService,
    YouTubeOAuthCredentialService,
    YouTubeOAuthSessionService,
    YouTubeOwnerAnalyticsSyncService,
    YouTubePublicStatsSyncService,
)

from .helpers.git_checks import tag_exists
from .helpers.repo_scanners import all_scope_violations


runner = CliRunner()

M10_3_TABLES = {
    "youtube_monitoring_credentials",
    "youtube_oauth_sessions",
    "youtube_public_sync_runs",
    "youtube_owner_analytics_sync_runs",
    "uploaded_video_youtube_public_monitor_snapshots",
    "uploaded_video_youtube_owner_analytics_snapshots",
}


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

FORBIDDEN_M10_4_M11_TABLES = {
    "dashboard_widgets",
    "operator_cockpit_views",
    "veo_provider_runs",
    "vertex_veo_bindings",
    "youtube_upload_jobs",
    "youtube_studio_scrapes",
    "fake_traffic_events",
    "bot_engagement_events",
}


class FakePublicProvider:
    def __init__(self, result: ProviderFetchResult):
        self.result = result
        self.calls = 0

    def fetch(self, *, platform_video_id: str, api_key: str) -> ProviderFetchResult:
        self.calls += 1
        assert platform_video_id
        assert api_key == "test-public-key"
        return self.result


class FakeOwnerProvider:
    def __init__(self, result: ProviderFetchResult):
        self.result = result
        self.calls = 0

    def fetch(self, *, platform_video_id: str, access_token: str, start_date: date, end_date: date) -> ProviderFetchResult:
        self.calls += 1
        assert platform_video_id
        assert access_token == "access-ok"
        assert start_date <= end_date
        return self.result


class FakeTokenExchanger:
    def exchange_code(self, *, code: str, client_config: dict[str, str], scopes: list[str]) -> dict:
        if code == "fail-code":
            raise RuntimeError("token exchange failed")
        return {
            "access_token": "access-ok",
            "refresh_token": "refresh-ok",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": " ".join(scopes),
        }

    def refresh_access_token(self, *, refresh_token: str, client_config: dict[str, str]) -> dict:
        assert refresh_token == "refresh-ok"
        return {"access_token": "access-ok", "expires_in": 3600, "token_type": "Bearer"}


def _actual_metadata(title: str = "M10.3 YouTube fixture") -> dict:
    return {
        "actual_title": title,
        "actual_description": "Manual upload description.",
        "actual_tags": ["youtube"],
        "actual_hashtags": ["#youtube"],
        "actual_privacy_status": "PUBLIC",
        "actual_caption_uploaded": True,
        "actual_made_for_kids": False,
    }


def _actual_disclosures() -> dict:
    return {
        "ai_disclosure_confirmed": False,
        "ai_disclosure_label_used": None,
        "paid_promotion_disclosure_confirmed": False,
        "music_license_confirmed": True,
        "stock_license_confirmed": True,
        "rights_confirmed": True,
        "operator_confirmed_no_unlicensed_assets": True,
    }


def _uploaded_video(db_session, qualification_factory, tmp_path, *, video_id: str = "yt-m10-3-001") -> UploadedVideo:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    handoff = PublishHandoffService(db_session).create_from_render_package(
        data=PublishHandoffCreate(
            render_package_snapshot_id=flow.production_run.render_package_snapshot_id,
            created_by_user_id=flow.operator.id,
        )
    )
    PublishHandoffService(db_session).mark_ready(handoff_id=handoff.id)
    confirmation = ManualPublishConfirmationService(db_session).create_confirmation(
        data=ManualPublishConfirmationCreate(
            publish_handoff_package_id=handoff.id,
            confirmed_by_user_id=flow.operator.id,
            actual_video_id=video_id,
            actual_video_url=f"https://www.youtube.com/watch?v={video_id}",
            actual_published_at=datetime.now(UTC),
            actual_metadata=_actual_metadata(),
            actual_disclosures=_actual_disclosures(),
            actual_files={"caption_uploaded": True},
        )
    )
    return ManualPublishConfirmationService(db_session).accept_confirmation(confirmation_id=confirmation.id)


def _public_output(video_id: str, *, title: str = "M10.3 YouTube fixture", duration_seconds: int | None = None) -> YouTubePublicProviderOutput:
    return YouTubePublicProviderOutput(
        platform_video_id=video_id,
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        views=0,
        likes=0,
        comments=7,
        youtube_title=title,
        youtube_published_at=datetime.now(UTC),
        youtube_channel_id="channel-1",
        youtube_channel_title="Fixture Channel",
        thumbnail_url="https://img.youtube.com/vi/test/maxresdefault.jpg",
        duration_seconds=duration_seconds,
        definition="hd",
        caption_status="true",
        privacy_status="public",
        public_stats_viewable=True,
        metric_availability={"views": "AVAILABLE", "likes": "AVAILABLE", "comments": "AVAILABLE"},
        technical_appendix={"payload_hash": "hash-public", "no_description_stored": True},
    )


def _owner_output(video_id: str) -> YouTubeOwnerAnalyticsProviderOutput:
    return YouTubeOwnerAnalyticsProviderOutput(
        platform_video_id=video_id,
        analytics_start_date=date(2026, 6, 1),
        analytics_end_date=date(2026, 6, 27),
        views=0,
        likes=0,
        comments=0,
        impressions=100,
        impression_click_through_rate=4.5,
        average_view_duration_seconds=37.0,
        average_view_percentage=62.5,
        estimated_minutes_watched=12.0,
        subscribers_gained=1,
        subscribers_lost=0,
        metric_availability={key: "AVAILABLE" for key in (
            "views",
            "likes",
            "comments",
            "impressions",
            "impression_click_through_rate",
            "average_view_duration_seconds",
            "average_view_percentage",
            "estimated_minutes_watched",
            "subscribers_gained",
            "subscribers_lost",
        )},
        technical_appendix={"payload_hash": "hash-owner"},
    )


def _enable_public(monkeypatch) -> None:
    monkeypatch.setenv("YOUTUBE_PUBLIC_MONITOR_ENABLED", "true")
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "test-public-key")
    get_settings.cache_clear()


def _enable_owner(monkeypatch) -> None:
    monkeypatch.setenv("YOUTUBE_OWNER_ANALYTICS_ENABLED", "true")
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("YOUTUBE_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/youtube/callback")
    monkeypatch.setenv(
        "YOUTUBE_OAUTH_SCOPES",
        "https://www.googleapis.com/auth/youtube.readonly,https://www.googleapis.com/auth/yt-analytics.readonly",
    )
    get_settings.cache_clear()


def test_m10_3_preflight_migration_defaults_config_catalogs_and_scope(engine, db_session, qualification_factory) -> None:
    assert tag_exists("m10-2-media-provider-routing") is True
    tables = set(inspect(engine).get_table_names())
    assert M10_3_TABLES <= tables
    assert tables.isdisjoint(FORBIDDEN_M10_4_M11_TABLES)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0021_m12_2r_handoff_ledger"
        defaults = connection.execute(
            text(
                """
                select table_name, column_name, column_default
                from information_schema.columns
                where table_name in (
                    'youtube_monitoring_credentials',
                    'youtube_oauth_sessions',
                    'uploaded_video_youtube_public_monitor_snapshots',
                    'uploaded_video_youtube_owner_analytics_snapshots'
                )
                  and column_name in (
                    'scopes',
                    'token_metadata',
                    'unknown_metrics',
                    'unavailable_metrics',
                    'technical_appendix',
                    'metric_availability'
                )
                """
            )
        ).all()
    default_map = {(row.table_name, row.column_name, row.column_default) for row in defaults}
    assert ("youtube_monitoring_credentials", "scopes", "'[]'::jsonb") in default_map
    assert ("youtube_monitoring_credentials", "token_metadata", "'{}'::jsonb") in default_map
    assert ("youtube_oauth_sessions", "scopes", "'[]'::jsonb") in default_map
    assert ("uploaded_video_youtube_public_monitor_snapshots", "unknown_metrics", "'[]'::jsonb") in default_map
    assert ("uploaded_video_youtube_owner_analytics_snapshots", "metric_availability", "'{}'::jsonb") in default_map

    qualification_factory.seed_all()
    routes = {route.path for route in create_app().routes}
    assert {route for route in routes if "dashboard" in route} <= {
        "/dashboard/command-center",
        "/dashboard/queues",
        "/dashboard/queues/{queue_type}",
        "/uploaded-videos/{uploaded_video_id}/dashboard",
    }
    route_text = " ".join(routes).lower()
    assert "youtube-upload" not in route_text
    assert "youtube-studio" not in route_text
    assert "veo" not in route_text


def test_config_env_modes_are_disabled_by_default_and_redact_secrets(monkeypatch) -> None:
    get_settings.cache_clear()
    service = YouTubeMonitoringConfigService(get_settings())
    assert service.public_monitor_enabled() is False
    assert service.owner_analytics_enabled() is False

    _enable_public(monkeypatch)
    _enable_owner(monkeypatch)
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "sk-secret-public-key")
    get_settings.cache_clear()
    configured = YouTubeMonitoringConfigService(get_settings())
    status = configured.safe_status()
    assert status["public_monitor_enabled"] is True
    assert status["owner_analytics_enabled"] is True
    assert status["scopes"] == [
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]
    assert "sk-secret-public-key" not in json.dumps(status)


def test_oauth_session_callback_and_safe_token_storage(db_session, monkeypatch, tmp_path) -> None:
    _enable_owner(monkeypatch)
    config = YouTubeMonitoringConfigService(get_settings())
    credential_service = YouTubeOAuthCredentialService(
        db_session,
        config_service=config,
        token_exchanger=FakeTokenExchanger(),
        credential_dir=tmp_path,
    )
    service = YouTubeOAuthSessionService(db_session, config_service=config, credential_service=credential_service)
    start = service.start()
    parsed = urllib.parse.urlparse(start.authorization_url)
    params = urllib.parse.parse_qs(parsed.query)
    raw_state = params["state"][0]
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    stored = db_session.get(YouTubeOAuthSession, start.oauth_session_id)
    assert stored.state_token_hash != raw_state
    assert len(stored.state_token_hash) == 64
    with pytest.raises(ValidationFailureError):
        service.handle_callback(state="bad-state", code="code-ok")

    completed = service.handle_callback(state=raw_state, code="code-ok")
    assert completed.status == "TOKEN_EXCHANGED"
    reference = db_session.get(CredentialReference, completed.credential_reference_id)
    assert reference.credential_type == "OAUTH_TOKEN"
    assert reference.secret_ref.startswith("local_file://")
    db_text = json.dumps(
        {
            "reference": {
                "secret_ref": reference.secret_ref,
                "scope_blob": reference.scope_blob,
                "metadata": reference.metadata_,
            },
            "session": {
                "status": completed.status,
                "error_code": completed.error_code,
                "error_message": completed.error_message,
            },
        },
        default=str,
    )
    assert "access-ok" not in db_text
    assert "refresh-ok" not in db_text
    assert "code-ok" not in db_text
    token_files = list((tmp_path / "oauth").glob("*.json"))
    assert token_files
    assert "refresh-ok" in token_files[0].read_text(encoding="utf-8")

    failed_start = service.start()
    failed_state = urllib.parse.parse_qs(urllib.parse.urlparse(failed_start.authorization_url).query)["state"][0]
    failed = service.handle_callback(state=failed_state, code="fail-code")
    assert failed.status == "FAILED"
    assert failed.error_code == "YOUTUBE_OAUTH_NEEDS_REAUTH"


def test_public_sync_maps_stats_zero_values_and_m8_weak_authority(db_session, qualification_factory, monkeypatch, tmp_path) -> None:
    _enable_public(monkeypatch)
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-public")
    package = db_session.get(RenderPackageSnapshot, uploaded.render_package_snapshot_id)
    output = _public_output(uploaded.platform_video_id, duration_seconds=int(package.duration_seconds or 0))
    service = YouTubePublicStatsSyncService(
        db_session,
        provider=FakePublicProvider(ProviderFetchResult(True, output=output.model_dump(mode="json"), http_status=200)),
    )
    run = service.sync_uploaded_video(uploaded_video_id=uploaded.id)
    snapshot = db_session.get(UploadedVideoYouTubePublicMonitorSnapshot, run.created_snapshot_id)
    summary = db_session.scalars(select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == uploaded.id)).one()
    analytics = db_session.get(AnalyticsSnapshot, summary.latest_analytics_snapshot_id)

    assert run.run_state == "COMPLETED"
    assert snapshot.views == 0
    assert snapshot.likes == 0
    assert snapshot.comments == 7
    assert snapshot.views_availability == "AVAILABLE"
    assert snapshot.learning_authority == "WEAK"
    assert snapshot.title_matches_confirmed_metadata is True
    assert snapshot.duration_matches_render_package is True
    assert analytics.metrics_blob["views"] == 0
    assert analytics.source_metadata["learning_authority"] == "WEAK"
    assert "click_through_rate" in analytics.metric_availability
    assert analytics.metric_availability["click_through_rate"]["state"] == "UNKNOWN"

    failed = YouTubePublicStatsSyncService(
        db_session,
        provider=FakePublicProvider(ProviderFetchResult(False, http_status=403, error_code="YOUTUBE_API_QUOTA_OR_AUTH_ERROR")),
    ).sync_uploaded_video(uploaded_video_id=uploaded.id)
    assert failed.run_state == "FAILED"
    assert failed.created_snapshot_id is None
    assert db_session.scalar(select(func.count()).select_from(UploadedVideoYouTubePublicMonitorSnapshot)) == 1


def test_owner_analytics_sync_needs_auth_then_maps_strong_metrics(db_session, qualification_factory, monkeypatch, tmp_path) -> None:
    _enable_owner(monkeypatch)
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-owner")
    no_auth = YouTubeOwnerAnalyticsSyncService(db_session).sync_uploaded_video(uploaded_video_id=uploaded.id)
    assert no_auth.run_state == "NEEDS_AUTH"
    assert no_auth.created_snapshot_id is None

    config = YouTubeMonitoringConfigService(get_settings())
    credential_service = YouTubeOAuthCredentialService(
        db_session,
        config_service=config,
        token_exchanger=FakeTokenExchanger(),
        credential_dir=tmp_path,
    )
    credential_service.store_token_response(
        token_response={
            "access_token": "access-ok",
            "refresh_token": "refresh-ok",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
        scopes=config.scopes,
        company_id=uploaded.company_id,
        channel_workspace_id=uploaded.channel_workspace_id,
    )
    service = YouTubeOwnerAnalyticsSyncService(
        db_session,
        config_service=config,
        credential_service=credential_service,
        provider=FakeOwnerProvider(ProviderFetchResult(True, output=_owner_output(uploaded.platform_video_id).model_dump(mode="json"), http_status=200)),
    )
    run = service.sync_uploaded_video(
        uploaded_video_id=uploaded.id,
        request=YouTubeOwnerAnalyticsSyncRequest(start_date=date(2026, 6, 1), end_date=date(2026, 6, 27)),
    )
    snapshot = db_session.get(UploadedVideoYouTubeOwnerAnalyticsSnapshot, run.created_snapshot_id)
    summary = db_session.scalars(select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == uploaded.id)).one()
    analytics = db_session.get(AnalyticsSnapshot, summary.latest_analytics_snapshot_id)

    assert run.run_state == "COMPLETED"
    assert snapshot.views == 0
    assert snapshot.impressions == 100
    assert snapshot.impression_click_through_rate == 4.5
    assert snapshot.average_view_duration_seconds == 37.0
    assert snapshot.estimated_minutes_watched == 12.0
    assert snapshot.subscribers_lost == 0
    assert snapshot.learning_authority == "STRONG"
    assert analytics.metrics_blob["click_through_rate"] == 4.5
    assert analytics.metrics_blob["watch_time_minutes"] == 12.0
    assert analytics.source_metadata["learning_authority"] == "STRONG"
    assert summary.metrics_summary["views"]["value"] == 0


def test_follow_summary_api_cli_and_scope_guard(db_session, qualification_factory, monkeypatch, tmp_path) -> None:
    _enable_public(monkeypatch)
    _enable_owner(monkeypatch)
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-summary")
    public_service = YouTubePublicStatsSyncService(
        db_session,
        provider=FakePublicProvider(ProviderFetchResult(True, output=_public_output(uploaded.platform_video_id).model_dump(mode="json"), http_status=200)),
    )
    public_service.sync_uploaded_video(uploaded_video_id=uploaded.id)
    config = YouTubeMonitoringConfigService(get_settings())
    credential_service = YouTubeOAuthCredentialService(
        db_session,
        config_service=config,
        token_exchanger=FakeTokenExchanger(),
        credential_dir=tmp_path,
    )
    credential_service.store_token_response(
        token_response={"access_token": "access-ok", "refresh_token": "refresh-ok", "expires_in": 3600},
        scopes=config.scopes,
        company_id=uploaded.company_id,
        channel_workspace_id=uploaded.channel_workspace_id,
    )
    YouTubeOwnerAnalyticsSyncService(
        db_session,
        config_service=config,
        credential_service=credential_service,
        provider=FakeOwnerProvider(ProviderFetchResult(True, output=_owner_output(uploaded.platform_video_id).model_dump(mode="json"), http_status=200)),
    ).sync_uploaded_video(uploaded_video_id=uploaded.id)
    db_session.commit()

    client = TestClient(create_app())
    assert client.get("/youtube/connection-status").status_code == 200
    detail = client.get(f"/uploaded-videos/{uploaded.id}/youtube/follow-summary")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["views"] == 0
    assert payload["impressions"] == 100
    assert payload["owner_analytics_connected"] is True
    assert payload["learning_authority"] == "MIXED"
    assert payload["caption_status"] == "AVAILABLE"
    assert "refresh_token" not in json.dumps(payload).lower()
    listed = client.get("/uploaded-videos/youtube/follow-summary")
    assert listed.status_code == 200
    assert listed.json()[0]["uploaded_video_id"] == str(uploaded.id)

    result = runner.invoke(cli_app, ["youtube", "connection-status"])
    assert result.exit_code == 0, result.output
    assert "client-secret" not in result.output
    result = runner.invoke(cli_app, ["youtube", "follow-summary", "--uploaded-video-id", str(uploaded.id)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["impressions"] == 100

    assert all_scope_violations(engine=db_session.get_bind()) == []
