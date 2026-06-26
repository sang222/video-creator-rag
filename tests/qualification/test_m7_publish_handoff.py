from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select, text
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.contracts.m7 import ManualPublishConfirmationCreate, PublishHandoffCreate
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.db.models import DomainEvent, ManualPublishConfirmation, MediaQCReport, PublishHandoffPackage, UploadedVideo
from app.main import create_app
from app.services import ManualPublishConfirmationService, PublishHandoffService

from .helpers.repo_scanners import all_scope_violations


runner = CliRunner()

M7_TABLES = {
    "publish_handoff_packages",
    "manual_publish_confirmations",
    "uploaded_videos",
    "uploaded_video_publication_summaries",
}

FORBIDDEN_M10_PLUS_TABLES = {
    "analytics_events",
    "analytics_semantic_layers",
    "no_view_incidents",
    "recovery_actions",
    "learning_candidates",
    "memory_promotions",
    "dashboard_widgets",
}


def _actual_metadata(title: str = "Budgeted video workflow") -> dict:
    return {
        "actual_title": title,
        "actual_description": "Manual upload description.",
        "actual_tags": ["workflow"],
        "actual_hashtags": ["#workflow"],
        "actual_privacy_status": "PUBLIC",
        "actual_caption_uploaded": True,
        "actual_made_for_kids": False,
    }


def _actual_disclosures(*, ai: bool = False, rights: bool = True, stock: bool = True) -> dict:
    return {
        "ai_disclosure_confirmed": ai,
        "ai_disclosure_label_used": "synthetic-media" if ai else None,
        "paid_promotion_disclosure_confirmed": False,
        "music_license_confirmed": True,
        "stock_license_confirmed": stock,
        "rights_confirmed": rights,
        "operator_confirmed_no_unlicensed_assets": rights,
    }


def _create_ready_handoff(db_session, flow) -> PublishHandoffPackage:
    handoff = PublishHandoffService(db_session).create_from_render_package(
        data=PublishHandoffCreate(
            render_package_snapshot_id=flow.production_run.render_package_snapshot_id,
            created_by_user_id=flow.operator.id,
        )
    )
    return PublishHandoffService(db_session).mark_ready(handoff_id=handoff.id)


def _create_confirmation(db_session, handoff: PublishHandoffPackage, *, video_id: str = "yt-fixture-001") -> ManualPublishConfirmation:
    return ManualPublishConfirmationService(db_session).create_confirmation(
        data=ManualPublishConfirmationCreate(
            publish_handoff_package_id=handoff.id,
            confirmed_by_user_id=handoff.created_by_user_id,
            actual_video_id=video_id,
            actual_video_url=f"https://www.youtube.com/watch?v={video_id}",
            actual_published_at=datetime.now(UTC),
            actual_metadata=_actual_metadata(),
            actual_disclosures=_actual_disclosures(),
            actual_files={"caption_uploaded": True},
        )
    )


def test_m7_migration_tables_defaults_unique_and_scope(engine, db_session, qualification_factory, tmp_path) -> None:
    tables = set(inspect(engine).get_table_names())
    assert M7_TABLES <= tables
    assert tables.isdisjoint(FORBIDDEN_M10_PLUS_TABLES)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0010_m9_post_publish_diagnostics"

    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    handoff = PublishHandoffService(db_session).create_from_render_package(
        data=PublishHandoffCreate(render_package_snapshot_id=flow.production_run.render_package_snapshot_id)
    )
    assert handoff.planned_metadata
    assert handoff.planned_disclosures
    assert handoff.planned_files
    assert handoff.checklist_snapshot
    assert handoff.operator_instructions
    assert handoff.risk_summary
    assert handoff.reason_codes
    assert db_session.query(UploadedVideo).count() == 0


def test_handoff_create_ready_checklist_and_operator_instructions(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    handoff = PublishHandoffService(db_session).create_from_render_package(
        data=PublishHandoffCreate(render_package_snapshot_id=flow.production_run.render_package_snapshot_id)
    )
    assert handoff.package_state == "DRAFT"
    assert handoff.render_package_snapshot_id == flow.production_run.render_package_snapshot_id
    assert handoff.policy_snapshot_id == flow.project.policy_snapshot_id
    assert handoff.source_manifest_snapshot_id == flow.production_run.source_manifest_snapshot_id
    assert handoff.asset_manifest_snapshot_id == flow.production_run.asset_manifest_snapshot_id
    checklist = handoff.checklist_snapshot
    labels = [item["label"] for item in checklist["items"]]
    assert any("Final video file" in label for label in labels)
    assert "outside VCOS" in handoff.operator_instructions["upload_file_instruction"]
    assert handoff.planned_files["final_video_ref"]["file_path"]

    ready = PublishHandoffService(db_session).mark_ready(handoff_id=handoff.id)
    assert ready.package_state == "READY_FOR_OPERATOR"
    assert "PUBLISH_HANDOFF_READY" in ready.reason_codes


def test_handoff_rejects_missing_package_and_blocks_bad_media_qc(db_session, qualification_factory, tmp_path) -> None:
    with pytest.raises(NotFoundError):
        PublishHandoffService(db_session).create_from_render_package(
            data=PublishHandoffCreate(render_package_snapshot_id="00000000-0000-0000-0000-000000000000")
        )
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    report = db_session.get(MediaQCReport, flow.production_run.media_qc_report_id)
    report.qc_state = "BLOCK"
    handoff = PublishHandoffService(db_session).create_from_render_package(
        data=PublishHandoffCreate(render_package_snapshot_id=flow.production_run.render_package_snapshot_id)
    )
    assert handoff.package_state == "BLOCKED"
    assert "MEDIA_QC_NOT_PASSING" in handoff.reason_codes
    assert handoff.next_action


def test_ai_disclosure_and_rights_missing_require_review(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    from app.db.models import SourceManifestSnapshot

    source_manifest = db_session.get(SourceManifestSnapshot, flow.production_run.source_manifest_snapshot_id)
    source_manifest.source_manifest_blob = {
        **source_manifest.source_manifest_blob,
        "source_refs": [{"scene_id": "scene_ai", "preferred_source": "AI_PLACEHOLDER"}],
    }
    handoff = _create_ready_handoff(db_session, flow)
    assert handoff.planned_metadata["planned_ai_disclosure_required"] is True
    ai_item = next(item for item in handoff.checklist_snapshot["items"] if item["item_id"] == "ai_disclosure")
    assert ai_item["state"] == "PENDING"

    confirmation = ManualPublishConfirmationService(db_session).create_confirmation(
        data=ManualPublishConfirmationCreate(
            publish_handoff_package_id=handoff.id,
            actual_video_id="yt-ai-review",
            actual_video_url="https://www.youtube.com/watch?v=yt-ai-review",
            actual_published_at=datetime.now(UTC),
            actual_metadata=_actual_metadata(),
            actual_disclosures=_actual_disclosures(ai=False, rights=False),
        )
    )
    assert confirmation.confirmation_state == "REVIEW_REQUIRED"
    assert "AI_DISCLOSURE_NOT_CONFIRMED" in confirmation.reason_codes
    assert "RIGHTS_CONFIRMATION_REQUIRED" in confirmation.reason_codes
    with pytest.raises(ValidationFailureError):
        ManualPublishConfirmationService(db_session).accept_confirmation(confirmation_id=confirmation.id)


def test_manual_confirmation_validation_diff_duplicate_and_no_platform_api(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    handoff = _create_ready_handoff(db_session, flow)
    with pytest.raises(ValidationError):
        ManualPublishConfirmationCreate(publish_handoff_package_id=handoff.id)
    with pytest.raises(ValidationFailureError):
        ManualPublishConfirmationService(db_session).create_confirmation(
            data=ManualPublishConfirmationCreate(
                publish_handoff_package_id=handoff.id,
                actual_video_id="bad-url",
                actual_video_url="not-a-url",
                actual_published_at=datetime.now(UTC),
                actual_metadata=_actual_metadata(),
                actual_disclosures=_actual_disclosures(),
            )
        )
    confirmation = ManualPublishConfirmationService(db_session).create_confirmation(
        data=ManualPublishConfirmationCreate(
            publish_handoff_package_id=handoff.id,
            actual_video_id="yt-diff-001",
            actual_video_url="https://www.youtube.com/watch?v=yt-diff-001",
            actual_published_at=datetime.now(UTC),
            actual_metadata=_actual_metadata(title="Changed human title"),
            actual_disclosures=_actual_disclosures(),
        )
    )
    assert confirmation.confirmation_state == "SUBMITTED"
    assert confirmation.metadata_diff["title_changed"] is True
    assert confirmation.metadata_diff["severity"] == "LOW"
    uploaded = ManualPublishConfirmationService(db_session).accept_confirmation(confirmation_id=confirmation.id)
    assert uploaded.platform_video_id == "yt-diff-001"
    assert db_session.query(UploadedVideo).count() == 1

    second_handoff = _create_ready_handoff(db_session, flow)
    with pytest.raises(ConflictError):
        ManualPublishConfirmationService(db_session).create_confirmation(
            data=ManualPublishConfirmationCreate(
                publish_handoff_package_id=second_handoff.id,
                actual_video_id="yt-diff-001",
                actual_video_url="https://www.youtube.com/watch?v=yt-diff-001",
                actual_published_at=datetime.now(UTC),
                actual_metadata=_actual_metadata(),
                actual_disclosures=_actual_disclosures(),
            )
        )
    assert all_scope_violations() == []


def test_uploaded_video_lineage_summary_and_events(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    handoff = _create_ready_handoff(db_session, flow)
    confirmation = _create_confirmation(db_session, handoff, video_id="yt-lineage-001")
    uploaded = ManualPublishConfirmationService(db_session).accept_confirmation(confirmation_id=confirmation.id)
    summary = ManualPublishConfirmationService(db_session).get_publication_summary(uploaded.id)

    assert uploaded.video_project_id == flow.project.id
    assert uploaded.render_package_snapshot_id == flow.production_run.render_package_snapshot_id
    assert uploaded.policy_snapshot_id == flow.project.policy_snapshot_id
    assert uploaded.source_manifest_snapshot_id == flow.production_run.source_manifest_snapshot_id
    assert uploaded.rights_envelope_ref
    assert uploaded.lineage_refs["media_qc_report_id"] == str(flow.production_run.media_qc_report_id)
    assert uploaded.monitoring_state == "READY_FOR_ANALYTICS"
    assert "analytics" not in uploaded.actual_metadata
    assert "Manual publish confirmed" in uploaded.operator_summary["summary"]
    assert summary.operator_status == "READY_FOR_ANALYTICS"
    assert "No metrics exist in M7" in summary.next_action

    event_types = set(db_session.scalars(select(DomainEvent.event_type)).all())
    assert {
        "publish_handoff_package.created",
        "publish_handoff_package.ready",
        "manual_publish_confirmation.created",
        "manual_publish_confirmation.accepted",
        "uploaded_video.created",
        "uploaded_video.ready_for_analytics",
    } <= event_types


def test_m7_api_and_cli_smoke(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    db_session.commit()
    client = TestClient(create_app())
    created = client.post(
        "/publish-handoffs",
        json={"render_package_snapshot_id": str(flow.production_run.render_package_snapshot_id)},
    )
    assert created.status_code == 200, created.text
    handoff_id = created.json()["id"]
    ready = client.post(f"/publish-handoffs/{handoff_id}/mark-ready")
    assert ready.status_code == 200, ready.text
    confirmed = client.post(
        "/manual-publish-confirmations",
        json={
            "publish_handoff_package_id": handoff_id,
            "actual_video_id": "yt-api-001",
            "actual_video_url": "https://www.youtube.com/watch?v=yt-api-001",
            "actual_published_at": datetime.now(UTC).isoformat(),
            "actual_metadata": _actual_metadata(),
            "actual_disclosures": _actual_disclosures(),
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmation_id = confirmed.json()["id"]
    accepted = client.post(f"/manual-publish-confirmations/{confirmation_id}/accept")
    assert accepted.status_code == 200, accepted.text
    uploaded_id = accepted.json()["id"]
    assert client.get(f"/uploaded-videos/{uploaded_id}").status_code == 200
    assert client.get(f"/video-projects/{flow.project.id}/uploaded-videos").status_code == 200
    assert client.get(f"/uploaded-videos/{uploaded_id}/publication-summary").status_code == 200

    for command in [
        ["publish", "handoff-inspect", "--handoff-id", handoff_id],
        ["publish", "confirmation-inspect", "--confirmation-id", confirmation_id],
        ["uploaded-video", "inspect", "--uploaded-video-id", uploaded_id],
        ["uploaded-video", "list-by-project", "--project-id", str(flow.project.id)],
        ["uploaded-video", "summary", "--uploaded-video-id", uploaded_id],
    ]:
        result = runner.invoke(cli_app, command)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)

    route_paths = {route.path for route in create_app().routes}
    assert "/publish-handoffs" in route_paths
    assert "/manual-publish-confirmations" in route_paths
    assert "/uploaded-videos/{uploaded_video_id}" in route_paths
