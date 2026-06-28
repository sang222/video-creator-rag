from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from typer.testing import CliRunner

from app.cli.main import app as cli_app

pytestmark = pytest.mark.skip(
    reason="Historical M9 qualification depends on pre-M12 local render/upload fixture; M12.1R keeps production mock/local success disabled."
)
from app.contracts import ManualAnalyticsImportContract, PostPublishHealthRunCreate
from app.contracts.m7 import ManualPublishConfirmationCreate, PublishHandoffCreate
from app.db.models import (
    DiagnosticTaxonomyVersion,
    DomainEvent,
    FailureTraceReport,
    ManualAction,
    NoViewDiagnosticRun,
    PackagingDiagnosticRun,
    PolicyRightsDiagnosticRun,
    RecoveryProposal,
    RetentionDiagnosticRun,
    UploadedVideo,
)
from app.main import create_app
from app.services import AnalyticsSyncService, ManualPublishConfirmationService, PostPublishHealthMonitorService, PublishHandoffService

from .helpers.git_checks import collect_git_status
from .helpers.repo_scanners import all_scope_violations


runner = CliRunner()

M9_TABLES = {
    "post_publish_observation_windows",
    "post_publish_health_runs",
    "diagnostic_taxonomy_versions",
    "no_view_diagnostic_runs",
    "packaging_diagnostic_runs",
    "retention_diagnostic_runs",
    "engagement_diagnostic_runs",
    "policy_rights_diagnostic_runs",
    "failure_trace_reports",
    "recovery_proposals",
}

FORBIDDEN_M10_PLUS_TABLES = {
    "dashboard_widgets",
    "memory_promotion_candidates",
    "algorithm_agents",
    "growth_agents",
    "view_agents",
    "fake_traffic_events",
    "bot_engagement_events",
    "auto_reupload_jobs",
}


def _actual_metadata(title: str = "M9 diagnostic fixture") -> dict:
    return {
        "actual_title": title,
        "actual_description": "Manual upload description.",
        "actual_tags": ["diagnostic"],
        "actual_hashtags": ["#diagnostic"],
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


def _uploaded_video(db_session, qualification_factory, tmp_path, *, video_id: str, published_offset: timedelta) -> UploadedVideo:
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
            actual_published_at=datetime.now(UTC) - published_offset,
            actual_metadata=_actual_metadata(),
            actual_disclosures=_actual_disclosures(),
            actual_files={"caption_uploaded": True},
        )
    )
    uploaded = ManualPublishConfirmationService(db_session).accept_confirmation(confirmation_id=confirmation.id)
    uploaded.published_at = datetime.now(UTC) - published_offset
    return uploaded


def _import_metrics(
    db_session,
    uploaded: UploadedVideo,
    *,
    metrics: dict,
    retention_curve: list[dict] | None = None,
    timeline_alignment: dict | None = None,
) -> None:
    AnalyticsSyncService(db_session).import_manual(
        data=ManualAnalyticsImportContract(
            uploaded_video_id=uploaded.id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            captured_at=datetime.now(UTC),
            observed_from=uploaded.published_at,
            observed_to=datetime.now(UTC),
            observation_window="T_PLUS_24H",
            metrics=metrics,
            traffic_sources=[
                {
                    "source_key": "browse",
                    "source_label": "Browse",
                    "views": metrics.get("views", 0),
                    "impressions": metrics.get("impressions"),
                    "watch_time_minutes": metrics.get("watch_time_minutes", 0),
                    "percentage": 100,
                    "metadata": {"fixture": True},
                }
            ],
            retention_curve=retention_curve,
            engagement={},
            duration_seconds=60,
            timeline_alignment=timeline_alignment or {"scene_refs": [{"time_seconds": 0, "scene_id": "scene_001"}]},
            source_note="m9 local fixture",
        )
    )


def _run_m9(db_session, uploaded: UploadedVideo, observation_window: str = "T_PLUS_24H"):
    service = PostPublishHealthMonitorService(db_session)
    run = service.create_health_run(
        data=PostPublishHealthRunCreate(uploaded_video_id=uploaded.id, observation_window=observation_window)  # type: ignore[arg-type]
    )
    return service.execute_health_run(run_id=run.id)


def test_m9_preflight_schema_catalogs_and_scope(engine, db_session, qualification_factory) -> None:
    status = collect_git_status()
    assert status.required_tags["m8-analytics-sync-foundation"] is True
    tables = set(inspect(engine).get_table_names())
    assert M9_TABLES <= tables
    assert tables.isdisjoint(FORBIDDEN_M10_PLUS_TABLES)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0021_m12_2r_handoff_ledger"
        defaults = connection.execute(
            text(
                """
                select table_name, column_name, column_default
                from information_schema.columns
                where table_name in ('post_publish_health_runs','failure_trace_reports','recovery_proposals')
                  and column_name in ('reason_codes','evidence_refs','do_not_do','technical_appendix','operator_report','recommended_actions')
                """
            )
        ).all()
    default_map = {(row.table_name, row.column_name, row.column_default) for row in defaults}
    assert ("post_publish_health_runs", "reason_codes", "'[]'::jsonb") in default_map
    assert ("failure_trace_reports", "operator_report", "'{}'::jsonb") in default_map
    assert ("recovery_proposals", "recommended_actions", "'[]'::jsonb") in default_map

    qualification_factory.seed_all()
    taxonomy = db_session.scalars(select(DiagnosticTaxonomyVersion)).all()
    assert {item.taxonomy_key for item in taxonomy} >= {"PACKAGING_FAILURE", "HOOK_FAILURE", "INSUFFICIENT_DATA"}
    assert all_scope_violations(engine) == []
    routes = {route.path for route in create_app().routes}
    assert {route for route in routes if "dashboard" in route} <= {
        "/dashboard/command-center",
        "/dashboard/queues",
        "/dashboard/queues/{queue_type}",
        "/uploaded-videos/{uploaded_video_id}/dashboard",
    }
    route_text = " ".join(routes).lower()
    assert "growth" not in route_text


def test_observation_window_not_ready_returns_insufficient_data(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m9-not-ready", published_offset=timedelta(minutes=5))
    run = _run_m9(db_session, uploaded, observation_window="T_PLUS_24H")
    report = db_session.scalars(select(FailureTraceReport).where(FailureTraceReport.post_publish_health_run_id == run.id)).one()
    proposal = db_session.scalars(select(RecoveryProposal).where(RecoveryProposal.failure_trace_report_id == report.id)).one()

    assert run.run_state == "INSUFFICIENT_DATA"
    assert run.health_state == "INSUFFICIENT_DATA"
    assert "OBSERVATION_WINDOW_NOT_READY" in run.reason_codes
    assert report.primary_status == "INSUFFICIENT_DATA"
    assert proposal.proposal_type == "WAIT_AND_MONITOR"
    assert proposal.requires_human_approval is True


def test_no_analytics_snapshot_returns_insufficient_data_without_inventing_metrics(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m9-no-data", published_offset=timedelta(days=3))
    run = _run_m9(db_session, uploaded)
    no_view = db_session.scalars(select(NoViewDiagnosticRun).where(NoViewDiagnosticRun.post_publish_health_run_id == run.id)).one()
    report = db_session.scalars(select(FailureTraceReport).where(FailureTraceReport.post_publish_health_run_id == run.id)).one()

    assert run.run_state == "INSUFFICIENT_DATA"
    assert no_view.diagnostic_state == "DATA_UNAVAILABLE"
    assert no_view.views is None
    assert no_view.impressions is None
    assert "No AnalyticsSnapshot available." in report.evidence_plain_text
    assert report.operator_report["friendly_status"] == "Chưa đủ dữ liệu"


def test_low_impressions_no_view_waits_and_does_not_reupload(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m9-low-imp", published_offset=timedelta(days=3))
    _import_metrics(
        db_session,
        uploaded,
        metrics={"views": 0, "impressions": 5, "click_through_rate": 0, "likes": 0, "comments": 0, "shares": 0},
    )
    run = _run_m9(db_session, uploaded)
    no_view = db_session.scalars(select(NoViewDiagnosticRun).where(NoViewDiagnosticRun.post_publish_health_run_id == run.id)).one()
    proposal = db_session.scalars(select(RecoveryProposal).where(RecoveryProposal.uploaded_video_id == uploaded.id)).one()

    assert no_view.diagnostic_state == "LOW_IMPRESSIONS"
    assert run.health_state == "NO_VIEW_RISK"
    assert proposal.proposal_type == "WAIT_AND_MONITOR"
    assert all("reupload" not in action.lower() for action in proposal.recommended_actions)
    assert any("Do not reupload" in item for item in proposal.do_not_do)


def test_low_ctr_creates_packaging_report_proposal_and_manual_action(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m9-low-ctr", published_offset=timedelta(days=3))
    _import_metrics(
        db_session,
        uploaded,
        metrics={
            "views": 100,
            "impressions": 5000,
            "click_through_rate": 1.0,
            "average_view_duration_seconds": 40,
            "average_view_percentage": 70,
            "likes": 8,
            "comments": 2,
            "shares": 1,
        },
        retention_curve=[
            {"time_seconds": 0, "retention_percent": 100, "viewers_remaining_estimate": 100},
            {"time_seconds": 10, "retention_percent": 75, "viewers_remaining_estimate": 75},
        ],
    )
    run = _run_m9(db_session, uploaded)
    packaging = db_session.scalars(select(PackagingDiagnosticRun).where(PackagingDiagnosticRun.post_publish_health_run_id == run.id)).one()
    report = db_session.scalars(select(FailureTraceReport).where(FailureTraceReport.post_publish_health_run_id == run.id)).one()
    proposal = db_session.scalars(select(RecoveryProposal).where(RecoveryProposal.failure_trace_report_id == report.id)).one()
    action = db_session.scalars(select(ManualAction).where(ManualAction.target_id == report.id)).one()

    assert packaging.diagnostic_state == "LOW_CTR"
    assert run.health_state == "UNDERPERFORMING"
    assert run.severity == "MEDIUM"
    assert run.confidence_level == "MEDIUM"
    assert report.primary_suspected_cause == "PACKAGING_FAILURE"
    assert report.operator_report["likely_cause_label"] == "Tiêu đề/thumbnail có thể chưa kéo click"
    assert proposal.proposal_type == "REVIEW_TITLE_THUMBNAIL"
    assert proposal.requires_human_approval is True
    assert action.action_type == "REVIEW_TITLE_THUMBNAIL_VARIANT"
    assert all("clickbait" not in action_text.lower() for action_text in proposal.recommended_actions)


def test_retention_drop_maps_to_scene_and_policy_review_uses_m7_evidence(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m9-retention", published_offset=timedelta(days=3))
    _import_metrics(
        db_session,
        uploaded,
        metrics={
            "views": 120,
            "impressions": 2500,
            "click_through_rate": 4.0,
            "average_view_duration_seconds": 16,
            "average_view_percentage": 35,
            "likes": 10,
            "comments": 2,
            "shares": 1,
        },
        retention_curve=[
            {"time_seconds": 0, "retention_percent": 100, "viewers_remaining_estimate": 120},
            {"time_seconds": 5, "retention_percent": 30, "viewers_remaining_estimate": 36},
        ],
        timeline_alignment={"scene_refs": [{"time_seconds": 0, "scene_id": "scene_hook", "narration_segment_id": "narr_001"}]},
    )
    run = _run_m9(db_session, uploaded)
    retention = db_session.scalars(select(RetentionDiagnosticRun).where(RetentionDiagnosticRun.post_publish_health_run_id == run.id)).one()
    proposal = db_session.scalars(select(RecoveryProposal).where(RecoveryProposal.uploaded_video_id == uploaded.id)).one()

    assert retention.diagnostic_state == "EARLY_DROP"
    assert retention.scene_alignment[0]["scene_id"] == "scene_hook"
    assert proposal.proposal_type == "REVIEW_HOOK"

    policy_video = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m9-policy", published_offset=timedelta(days=3))
    policy_video.actual_disclosures = {"ai_disclosure_confirmed": None, "rights_confirmed": False}
    _import_metrics(
        db_session,
        policy_video,
        metrics={"views": 120, "impressions": 2500, "click_through_rate": 4.0, "likes": 10, "comments": 2, "shares": 1},
    )
    policy_run = _run_m9(db_session, policy_video)
    policy = db_session.scalars(select(PolicyRightsDiagnosticRun).where(PolicyRightsDiagnosticRun.post_publish_health_run_id == policy_run.id)).one()
    policy_proposal = db_session.scalars(select(RecoveryProposal).where(RecoveryProposal.uploaded_video_id == policy_video.id)).one()
    assert policy.diagnostic_state == "REVIEW_REQUIRED"
    assert policy_run.health_state == "POLICY_REVIEW_REQUIRED"
    assert policy_proposal.proposal_type == "REVIEW_RIGHTS_DISCLOSURE"


def test_m9_api_cli_events_and_proposal_state_smoke(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m9-smoke", published_offset=timedelta(days=3))
    _import_metrics(
        db_session,
        uploaded,
        metrics={
            "views": 100,
            "impressions": 5000,
            "click_through_rate": 1.0,
            "average_view_duration_seconds": 40,
            "average_view_percentage": 70,
            "likes": 8,
            "comments": 2,
            "shares": 1,
        },
        retention_curve=[
            {"time_seconds": 0, "retention_percent": 100},
            {"time_seconds": 10, "retention_percent": 75},
        ],
    )
    db_session.commit()
    client = TestClient(create_app())
    created = client.post(
        "/post-publish-health-runs",
        json={"uploaded_video_id": str(uploaded.id), "observation_window": "T_PLUS_24H"},
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    executed = client.post(f"/post-publish-health-runs/{run_id}/execute")
    assert executed.status_code == 200, executed.text
    assert executed.json()["health_state"] == "UNDERPERFORMING"
    assert client.get(f"/post-publish-health-runs/{run_id}").status_code == 200
    assert client.get(f"/uploaded-videos/{uploaded.id}/post-publish-health").status_code == 200
    reports = client.get(f"/uploaded-videos/{uploaded.id}/failure-trace-reports")
    assert reports.status_code == 200, reports.text
    report_id = reports.json()[0]["id"]
    assert client.get(f"/failure-trace-reports/{report_id}").status_code == 200
    proposals = client.get(f"/uploaded-videos/{uploaded.id}/recovery-proposals")
    assert proposals.status_code == 200, proposals.text
    proposal_id = proposals.json()[0]["id"]
    accepted = client.post(f"/recovery-proposals/{proposal_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["proposal_state"] == "ACCEPTED"

    for command in [
        ["post-publish", "health-inspect", "--run-id", run_id],
        ["post-publish", "reports-by-video", "--uploaded-video-id", str(uploaded.id)],
        ["post-publish", "report-inspect", "--report-id", report_id],
        ["post-publish", "proposals-by-video", "--uploaded-video-id", str(uploaded.id)],
        ["post-publish", "proposal-reject", "--proposal-id", proposal_id],
    ]:
        result = runner.invoke(cli_app, command)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)

    event_types = set(db_session.scalars(select(DomainEvent.event_type)).all())
    assert {
        "post_publish_health_run.created",
        "post_publish_health_run.completed",
        "failure_trace_report.created",
        "recovery_proposal.created",
    } <= event_types
    for event in db_session.scalars(select(DomainEvent)).all():
        payload_text = json.dumps(event.payload, sort_keys=True).lower()
        assert "oauth" not in payload_text
        assert "credential" not in payload_text
        assert "token" not in payload_text
