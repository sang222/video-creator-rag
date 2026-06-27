from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select, text
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.contracts import AnalyticsSyncRunCreate, AnalyticsSyncRunExecuteRequest, ManualAnalyticsImportContract
from app.contracts.m7 import ManualPublishConfirmationCreate, PublishHandoffCreate
from app.core.errors import ValidationFailureError
from app.db.models import (
    AnalyticsSnapshot,
    AnalyticsSyncRun,
    DomainEvent,
    EngagementSnapshot,
    MetricAvailabilitySnapshot,
    MetricDefinitionVersion,
    ProviderAttempt,
    RetentionCurveSnapshot,
    TrafficSourceSnapshot,
    UploadedVideo,
    UploadedVideoMetricsSummary,
)
from app.main import create_app
from app.services import AnalyticsSyncService, ManualPublishConfirmationService, PublishHandoffService

from .helpers.git_checks import collect_git_status
from .helpers.repo_scanners import all_scope_violations


runner = CliRunner()

M8_TABLES = {
    "analytics_sync_runs",
    "metric_definition_versions",
    "metric_availability_snapshots",
    "analytics_snapshots",
    "traffic_source_snapshots",
    "retention_curve_snapshots",
    "engagement_snapshots",
    "uploaded_video_metrics_summaries",
}

FORBIDDEN_M10_PLUS_TABLES = {
    "memory_promotion_candidates",
    "recovery_actions",
    "dashboard_widgets",
    "algorithm_agents",
    "growth_agents",
    "view_agents",
    "fake_traffic_events",
    "bot_engagement_events",
    "auto_reupload_jobs",
}


def _actual_metadata(title: str = "M8 analytics fixture") -> dict:
    return {
        "actual_title": title,
        "actual_description": "Manual upload description.",
        "actual_tags": ["analytics"],
        "actual_hashtags": ["#analytics"],
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


def _uploaded_video(db_session, qualification_factory, tmp_path, *, video_id: str = "yt-m8-001") -> UploadedVideo:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    handoff = PublishHandoffService(db_session).create_from_render_package(
        data=PublishHandoffCreate(
            render_package_snapshot_id=flow.production_run.render_package_snapshot_id,
            created_by_user_id=flow.operator.id,
        )
    )
    handoff = PublishHandoffService(db_session).mark_ready(handoff_id=handoff.id)
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


def _manual_import_payload(uploaded: UploadedVideo, **overrides) -> ManualAnalyticsImportContract:
    payload = {
        "uploaded_video_id": uploaded.id,
        "platform": uploaded.platform,
        "platform_video_id": uploaded.platform_video_id,
        "captured_at": datetime.now(UTC),
        "observation_window": "T_PLUS_24H",
        "metrics": {
            "views": 0,
            "impressions": 100,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "watch_time_minutes": 0,
        },
        "traffic_sources": [
            {
                "source_key": "browse",
                "source_label": "Browse",
                "views": 0,
                "impressions": 100,
                "watch_time_minutes": 0,
                "percentage": 100,
                "metadata": {"fixture": True},
            }
        ],
        "retention_curve": [
            {"time_seconds": 10, "retention_percent": 80, "viewers_remaining_estimate": 0, "source_metric": "manual"},
            {"time_seconds": 0, "retention_percent": 100, "viewers_remaining_estimate": 0, "source_metric": "manual"},
        ],
        "engagement": {},
        "duration_seconds": 20,
        "timeline_alignment": {"scene_refs": [{"time_seconds": 0, "scene_id": "scene_001"}]},
        "source_note": "local manual fixture",
    }
    payload.update(overrides)
    return ManualAnalyticsImportContract(**payload)


def test_m8_preflight_tags_migration_tables_defaults_and_metric_seed(engine, db_session, qualification_factory) -> None:
    status = collect_git_status()
    assert status.required_tags["m7-manual-publish-handoff"] is True
    tables = set(inspect(engine).get_table_names())
    assert M8_TABLES <= tables
    assert tables.isdisjoint(FORBIDDEN_M10_PLUS_TABLES)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0016_m11_operator_dashboard"
        defaults = connection.execute(
            text(
                """
                select column_name, column_default
                from information_schema.columns
                where table_name in ('analytics_snapshots','analytics_sync_runs','uploaded_video_metrics_summaries')
                  and column_name in ('metrics_blob','normalized_metrics_blob','metric_availability','reason_codes','metadata','metrics_summary','availability_summary')
                """
            )
        ).all()
    default_map = {(row.column_name, row.column_default) for row in defaults}
    assert ("metrics_blob", "'{}'::jsonb") in default_map
    assert ("normalized_metrics_blob", "'{}'::jsonb") in default_map
    assert ("reason_codes", "'[]'::jsonb") in default_map

    qualification_factory.seed_all()
    definitions = db_session.scalars(select(MetricDefinitionVersion)).all()
    assert {item.metric_key for item in definitions} >= {"views", "impressions", "engagement_rate", "completion_rate"}


def test_manual_import_creates_snapshot_summary_and_preserves_zero_vs_missing(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m8-manual")
    service = AnalyticsSyncService(db_session)
    snapshot = service.import_manual(data=_manual_import_payload(uploaded))
    summary = service.get_metrics_summary(uploaded.id)
    availability = db_session.scalars(
        select(MetricAvailabilitySnapshot).where(MetricAvailabilitySnapshot.uploaded_video_id == uploaded.id)
    ).one()

    assert snapshot.uploaded_video_id == uploaded.id
    assert snapshot.video_project_id == uploaded.video_project_id
    assert snapshot.channel_workspace_id == uploaded.channel_workspace_id
    assert snapshot.policy_snapshot_id == uploaded.policy_snapshot_id
    assert snapshot.metrics_blob["views"] == 0
    assert snapshot.metric_availability["views"]["state"] == "AVAILABLE"
    assert "average_view_duration_seconds" in availability.unknown_metrics
    assert "views" not in availability.unknown_metrics
    assert summary.latest_analytics_snapshot_id == snapshot.id
    assert summary.monitoring_state in {"PARTIAL_DATA", "NO_DATA_YET"}
    assert "Some metrics are not available yet" in (summary.operator_summary or "")
    assert "title" not in (summary.next_action or "").lower()
    assert "thumbnail" not in (summary.next_action or "").lower()

    second = service.import_manual(data=_manual_import_payload(uploaded, metrics={"views": 5, "likes": 1, "comments": 0, "shares": 0}))
    assert second.id != snapshot.id
    assert db_session.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.uploaded_video_id == uploaded.id).count() == 2
    assert service.get_metrics_summary(uploaded.id).latest_analytics_snapshot_id == second.id


def test_manual_import_rejects_mismatch_invalid_values_and_invalid_retention(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m8-invalid")
    service = AnalyticsSyncService(db_session)
    with pytest.raises(ValidationFailureError):
        service.import_manual(data=_manual_import_payload(uploaded, platform_video_id="different"))
    with pytest.raises(ValidationError):
        _manual_import_payload(uploaded, metrics={"views": -1})
    with pytest.raises(ValidationError):
        _manual_import_payload(uploaded, metrics={"mystery_metric": 1})
    with pytest.raises(ValidationError):
        _manual_import_payload(uploaded, duration_seconds=2)
    completed_events = db_session.scalars(select(DomainEvent).where(DomainEvent.event_type == "analytics_sync_run.completed")).all()
    assert completed_events == []


def test_mock_sync_creates_provider_attempt_snapshots_and_no_network(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m8-mock")
    service = AnalyticsSyncService(db_session)
    run = service.create_sync_run(data=AnalyticsSyncRunCreate(uploaded_video_id=uploaded.id, sync_mode="MOCK"))
    executed = service.execute_sync_run(sync_run_id=run.id, data=AnalyticsSyncRunExecuteRequest(mock_mode="success"))
    summary = service.get_metrics_summary(uploaded.id)

    assert executed.sync_state == "COMPLETED"
    assert executed.provider_attempt_id is not None
    assert executed.analytics_snapshot_id is not None
    attempt = db_session.get(ProviderAttempt, executed.provider_attempt_id)
    assert attempt.status == "SUCCESS"
    assert attempt.metadata_["no_network_analytics_call"] is True
    assert db_session.query(MetricAvailabilitySnapshot).filter_by(uploaded_video_id=uploaded.id).count() == 1
    assert db_session.query(TrafficSourceSnapshot).filter_by(uploaded_video_id=uploaded.id).count() == 1
    assert db_session.query(RetentionCurveSnapshot).filter_by(uploaded_video_id=uploaded.id).count() == 1
    assert db_session.query(EngagementSnapshot).filter_by(uploaded_video_id=uploaded.id).count() == 1
    assert summary.monitoring_state == "SYNCED"
    assert summary.operator_summary == "Analytics synced successfully"
    assert "diagnosis" not in json.dumps(summary.metrics_summary).lower()


def test_traffic_retention_engagement_and_summary_semantics(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m8-detail")
    snapshot = AnalyticsSyncService(db_session).import_manual(
        data=_manual_import_payload(uploaded, metrics={"views": 10, "likes": 2, "comments": 1, "shares": 1})
    )
    traffic = db_session.scalars(select(TrafficSourceSnapshot).where(TrafficSourceSnapshot.analytics_snapshot_id == snapshot.id)).one()
    retention = db_session.scalars(select(RetentionCurveSnapshot).where(RetentionCurveSnapshot.analytics_snapshot_id == snapshot.id)).one()
    engagement = db_session.scalars(select(EngagementSnapshot).where(EngagementSnapshot.analytics_snapshot_id == snapshot.id)).one()
    summary = AnalyticsSyncService(db_session).get_metrics_summary(uploaded.id)

    assert traffic.source_summary["state"] == "AVAILABLE"
    assert [point["time_seconds"] for point in retention.curve_points] == [0.0, 10.0]
    assert retention.curve_summary["no_retention_diagnosis"] is True
    assert engagement.engagement_blob["engagement_rate"]["computed"] is True
    assert summary.metrics_summary["engagement_rate"]["computed"] is True
    assert "fail" not in (summary.operator_summary or "").lower()
    assert "change title" not in (summary.next_action or "").lower()


def test_blocked_and_no_data_paths_create_operational_summary_only(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m8-block")
    service = AnalyticsSyncService(db_session)
    no_data_summary = service.get_metrics_summary(uploaded.id)
    assert no_data_summary.monitoring_state == "NO_DATA_YET"
    assert no_data_summary.operator_summary == "No analytics data imported yet"

    real_disabled = service.create_sync_run(data=AnalyticsSyncRunCreate(uploaded_video_id=uploaded.id, sync_mode="REAL_DISABLED"))
    assert real_disabled.sync_state == "BLOCKED"
    assert "ANALYTICS_PROVIDER_REAL_DISABLED" in real_disabled.reason_codes
    assert service.get_metrics_summary(uploaded.id).monitoring_state == "BLOCKED"

    uploaded.monitoring_state = "PAUSED"
    blocked = service.create_sync_run(data=AnalyticsSyncRunCreate(uploaded_video_id=uploaded.id, sync_mode="MOCK"))
    assert blocked.sync_state == "BLOCKED"
    assert "UPLOADED_VIDEO_NOT_READY_FOR_ANALYTICS" in blocked.reason_codes


def test_m8_api_cli_smoke_and_scope_guard(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m8-smoke")
    db_session.commit()
    client = TestClient(create_app())
    created = client.post("/analytics-sync-runs", json={"uploaded_video_id": str(uploaded.id), "sync_mode": "MOCK"})
    assert created.status_code == 200, created.text
    sync_run_id = created.json()["id"]
    executed = client.post(f"/analytics-sync-runs/{sync_run_id}/execute", json={"mock_mode": "success"})
    assert executed.status_code == 200, executed.text
    snapshot_id = executed.json()["analytics_snapshot_id"]
    assert client.get(f"/analytics-sync-runs/{sync_run_id}").status_code == 200
    assert client.get(f"/analytics-snapshots/{snapshot_id}").status_code == 200
    assert client.get(f"/uploaded-videos/{uploaded.id}/analytics-snapshots").status_code == 200
    assert client.get(f"/uploaded-videos/{uploaded.id}/metrics-summary").status_code == 200
    assert client.get(f"/uploaded-videos/{uploaded.id}/retention").status_code == 200
    assert client.get(f"/uploaded-videos/{uploaded.id}/traffic-sources").status_code == 200

    manual_payload = {
        "uploaded_video_id": str(uploaded.id),
        "platform": uploaded.platform,
        "platform_video_id": uploaded.platform_video_id,
        "captured_at": datetime.now(UTC).isoformat(),
        "observation_window": "T_PLUS_24H",
        "metrics": {"views": 1, "likes": 0},
    }
    imported = client.post("/analytics/import-manual", json=manual_payload)
    assert imported.status_code == 200, imported.text

    for command in [
        ["analytics", "sync-inspect", "--sync-run-id", sync_run_id],
        ["analytics", "snapshot-inspect", "--snapshot-id", snapshot_id],
        ["analytics", "list-by-uploaded-video", "--uploaded-video-id", str(uploaded.id)],
        ["analytics", "metrics-summary", "--uploaded-video-id", str(uploaded.id)],
        ["analytics", "retention", "--uploaded-video-id", str(uploaded.id)],
        ["analytics", "traffic-sources", "--uploaded-video-id", str(uploaded.id)],
    ]:
        result = runner.invoke(cli_app, command)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)

    assert all_scope_violations() == []
    routes = {route.path for route in create_app().routes}
    assert {route for route in routes if "dashboard" in route} <= {
        "/dashboard/command-center",
        "/dashboard/queues",
        "/dashboard/queues/{queue_type}",
        "/uploaded-videos/{uploaded_video_id}/dashboard",
    }
    forbidden_text = " ".join(routes).lower()
    assert "growth" not in forbidden_text
    assert "no-view" not in forbidden_text


def test_m8_domain_events_are_metric_safe_and_no_completed_on_failed_provider(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m8-events")
    service = AnalyticsSyncService(db_session)
    run = service.create_sync_run(data=AnalyticsSyncRunCreate(uploaded_video_id=uploaded.id, sync_mode="MOCK"))
    failed = service.execute_sync_run(sync_run_id=run.id, data=AnalyticsSyncRunExecuteRequest(mock_mode="unavailable"))
    assert failed.sync_state == "BLOCKED"
    event_types = set(db_session.scalars(select(DomainEvent.event_type)).all())
    assert "analytics_sync_run.blocked" in event_types
    assert "analytics_sync_run.completed" not in event_types

    snapshot = service.import_manual(data=_manual_import_payload(uploaded, metrics={"views": 3, "likes": 1}))
    event_types = set(db_session.scalars(select(DomainEvent.event_type)).all())
    assert {
        "manual_analytics_import.accepted",
        "analytics_snapshot.created",
        "metric_availability_snapshot.created",
        "uploaded_video_metrics_summary.updated",
    } <= event_types
    for event in db_session.scalars(select(DomainEvent)).all():
        text = json.dumps(event.payload, sort_keys=True).lower()
        assert "oauth" not in text
        assert "token" not in text
        assert "credential" not in text
    assert db_session.get(AnalyticsSnapshot, snapshot.id).source_metadata["no_diagnosis_in_m8"] is True
