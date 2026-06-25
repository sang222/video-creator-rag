import json
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, inspect, select
from typer.testing import CliRunner

import app.services.m6 as m6_service
from app.cli.main import app as cli_app
from app.contracts import ArtifactCreate, ArtifactVersionCreate, ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.m6 import (
    CaptionTrackContract,
    NarrationSegmentContract,
    ProductionArtifactRunCreate,
    RenderVariantSpec,
    VoiceTimelineContract,
)
from app.contracts.workflow import VideoProjectCreate
from app.db.models import (
    AccessibilityQCReport,
    Artifact,
    CostEvent,
    LLMRunSnapshot,
    MediaQCReport,
    MediaRenderJob,
    ProviderAttempt,
    RenderPackageSnapshot,
    SourceManifestSnapshot,
    User,
)
from app.main import create_app
from app.services import (
    ArtifactService,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    ProductionArtifactRunService,
    ProviderRegistryService,
    RBACService,
    SceneSourceDecisionService,
    VideoProjectService,
)


ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()

M6_TABLES = {
    "production_artifact_runs",
    "voice_timeline_snapshots",
    "caption_track_snapshots",
    "visual_plan_snapshots",
    "scene_manifest_snapshots",
    "asset_manifest_snapshots",
    "source_manifest_snapshots",
    "render_spec_snapshots",
    "media_render_jobs",
    "render_package_snapshots",
    "media_qc_reports",
    "accessibility_qc_reports",
    "pronunciation_dictionary_entries",
}

FORBIDDEN_M8_PLUS_FRAGMENTS = {
    "analytics_semantic",
    "no_view",
    "memory_promotion",
    "dashboard",
    "source_scrap",
    "source_parse",
    "opa_policy",
    "cedar_policy",
    "algorithm_agent",
    "growth_agent",
    "view_agent",
}


def _user(db_session, email: str) -> User:
    user = User(email=email, display_name=email.split("@")[0], status="active")
    db_session.add(user)
    db_session.flush()
    return user


def _project_with_m5_inputs(db_session):
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    ProviderRegistryService(db_session).seed_mock_providers()
    company = CompanyService(db_session).create_company(name="M6 Co")
    operator = _user(db_session, f"operator-{uuid.uuid4()}@example.com")
    RBACService(db_session).assign_role(user_id=operator.id, role_key="operator", company_id=company.id)
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key=f"m6-{uuid.uuid4().hex[:8]}", name="M6 Channel"),
    )
    profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    compiled = ChannelProfileCompiler(db_session).compile(profile_version_id=profile.id, correlation_id="m6-compile")
    snapshot = ChannelProfileService(db_session).activate_snapshot(snapshot_id=compiled.snapshot_id)
    project = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            title="Budgeted video workflow",
            description="Explain how admitted ideas become production artifacts.",
            project_type="m5_daily_run",
            created_by_user_id=operator.id,
        )
    )
    artifact_service = ArtifactService(db_session)
    for artifact_type, content in {
        "creative_brief": {
            "title": "Budgeted video workflow",
            "angle": "Explain the daily production pipeline for operators.",
            "format": "explainer",
            "status": "draft",
        },
        "research_pack": {
            "evidence_refs": [{"type": "search_demand_evidence", "id": "fixture"}],
            "numeric_truth": "SQL_OR_UNKNOWN",
            "status": "draft",
        },
        "source_pack": {
            "source_refs": [{"type": "manual_fixture", "id": "fixture"}],
            "status": "draft",
        },
    }.items():
        artifact = artifact_service.create_artifact(
            data=ArtifactCreate(video_project_id=project.id, artifact_type=artifact_type, created_by_user_id=operator.id)
        )
        artifact_service.create_artifact_version(
            data=ArtifactVersionCreate(artifact_id=artifact.id, content=content, created_by_user_id=operator.id)
        )
    return company, channel, snapshot, operator, project


def test_m6_migration_tables_defaults_and_scope_guard(engine, db_session) -> None:
    tables = set(inspect(engine).get_table_names())
    assert M6_TABLES <= tables
    assert not {table for table in tables for fragment in FORBIDDEN_M8_PLUS_FRAGMENTS if fragment in table}
    _, _, _, _, project = _project_with_m5_inputs(db_session)
    run = ProductionArtifactRunService(db_session).create_run(
        data=ProductionArtifactRunCreate(video_project_id=project.id)
    )
    assert run.reason_codes == []
    assert run.metadata_ == {}


def test_m6_contracts_reject_bad_timing_and_variant_shape() -> None:
    first = NarrationSegmentContract(
        narration_segment_id="nar_001",
        text="First segment.",
        sequence_index=0,
        estimated_start_time=0,
        estimated_end_time=2,
        estimated_duration_seconds=2,
    )
    overlapping = NarrationSegmentContract(
        narration_segment_id="nar_002",
        text="Overlap.",
        sequence_index=1,
        estimated_start_time=1.5,
        estimated_end_time=3,
        estimated_duration_seconds=1.5,
    )
    with pytest.raises(ValidationError):
        VoiceTimelineContract(
            total_duration_seconds=3,
            segments=[first, overlapping],
            timing_source="ESTIMATED",
            timeline_hash="hash",
            confidence_level="MEDIUM",
        )
    with pytest.raises(ValidationError):
        CaptionTrackContract(cues=[], format="SRT", language="en", caption_hash="hash")
    with pytest.raises(ValidationError):
        RenderVariantSpec(
            variant_id="bad",
            platform="YOUTUBE",
            surface="LONG_FORM",
            aspect_ratio="16:9",
            resolution_width=720,
            resolution_height=1280,
            fps=30,
            crop_strategy="LETTERBOX",
            caption_placement={"placement_key": "lower_third"},
            safe_area_profile={"profile_key": "generic"},
            export_filename="bad.mp4",
            variant_status="READY",
        )


def test_scene_source_decision_is_deterministic_and_no_high_risk_ai_default() -> None:
    service = SceneSourceDecisionService()
    high_risk = service.decide(
        scene_type="GENERIC_BROLL",
        importance="HIGH",
        specificity="HIGH",
        factual_risk="HIGH",
        need_realism="HIGH",
        approved_asset_pool_match=False,
    )
    process = service.decide(
        scene_type="PROCESS",
        importance="MEDIUM",
        specificity="MEDIUM",
        factual_risk="LOW",
        need_realism="LOW",
        approved_asset_pool_match=False,
    )
    assert high_risk.preferred_source != "AI_PLACEHOLDER"
    assert high_risk.preferred_source == "SCREENSHOT_PLACEHOLDER"
    assert process.preferred_source == "DIAGRAM_PLACEHOLDER"
    assert process.source_class == "LOCAL_RENDERER"


def test_production_run_e2e_blocks_safely_when_ffmpeg_unavailable(db_session, monkeypatch) -> None:
    monkeypatch.setattr(m6_service.shutil, "which", lambda name: None)
    _, _, _, _, project = _project_with_m5_inputs(db_session)
    run = ProductionArtifactRunService(db_session).create_run(
        data=ProductionArtifactRunCreate(video_project_id=project.id)
    )
    executed = ProductionArtifactRunService(db_session).execute_local_mock_flow(run_id=run.id)
    assert executed.status == "BLOCKED"
    assert "FFMPEG_UNAVAILABLE" in executed.reason_codes
    assert executed.script_artifact_version_id is not None
    assert executed.voice_timeline_snapshot_id is not None
    assert executed.caption_track_snapshot_id is not None
    assert executed.visual_plan_snapshot_id is not None
    assert executed.scene_manifest_snapshot_id is not None
    assert executed.asset_manifest_snapshot_id is not None
    assert executed.source_manifest_snapshot_id is not None
    assert executed.render_spec_snapshot_id is not None
    assert executed.render_package_snapshot_id is None
    assert db_session.scalars(select(MediaRenderJob)).one().status == "BLOCKED"
    media_qc = db_session.get(MediaQCReport, executed.media_qc_report_id)
    access_qc = db_session.get(AccessibilityQCReport, executed.accessibility_qc_report_id)
    assert media_qc.qc_state == "BLOCK"
    assert access_qc.qc_state == "PASS"
    assert db_session.scalar(
        select(func.count()).select_from(LLMRunSnapshot).where(LLMRunSnapshot.run_type == "M6_SCRIPT_DRAFT")
    ) == 1


def test_mock_llm_attempt_cost_and_source_manifest_are_traceable(db_session, monkeypatch) -> None:
    monkeypatch.setattr(m6_service.shutil, "which", lambda name: None)
    _, _, _, _, project = _project_with_m5_inputs(db_session)
    run = ProductionArtifactRunService(db_session).create_run(
        data=ProductionArtifactRunCreate(video_project_id=project.id)
    )
    ProductionArtifactRunService(db_session).execute_local_mock_flow(run_id=run.id)
    assert db_session.scalar(
        select(func.count()).select_from(LLMRunSnapshot).where(LLMRunSnapshot.run_type == "M6_SCRIPT_DRAFT")
    ) == 1
    assert db_session.scalar(
        select(func.count()).select_from(ProviderAttempt).where(ProviderAttempt.operation_key == "m6_script_draft")
    ) == 1
    assert db_session.scalar(select(func.count()).select_from(CostEvent).where(CostEvent.provider_key == "mock_llm")) >= 1
    source_snapshot = db_session.get(SourceManifestSnapshot, run.source_manifest_snapshot_id)
    summary = source_snapshot.source_manifest_blob["provider_classification_summary"]
    assert summary["real_provider_calls"] == 0
    assert summary["envato_api_calls"] == 0
    assert "render_package" not in [artifact.artifact_type for artifact in db_session.scalars(select(Artifact)).all()]


def test_local_renderer_generates_playable_mp4_when_ffmpeg_available(db_session, tmp_path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe unavailable; local smoke must report BLOCKED on this machine")
    _, _, _, _, project = _project_with_m5_inputs(db_session)
    run = ProductionArtifactRunService(db_session).create_run(
        data=ProductionArtifactRunCreate(video_project_id=project.id)
    )
    executed = ProductionArtifactRunService(db_session).execute_local_mock_flow(run_id=run.id, output_dir=tmp_path)
    assert executed.status == "COMPLETED"
    package = db_session.get(RenderPackageSnapshot, executed.render_package_snapshot_id)
    assert package.final_video_ref["checksum"]
    assert Path(package.final_video_ref["file_path"]).exists()
    assert float(package.duration_seconds) > 0


def test_m6_api_and_cli_smoke(db_session, monkeypatch) -> None:
    monkeypatch.setattr(m6_service.shutil, "which", lambda name: None)
    _, _, _, _, project = _project_with_m5_inputs(db_session)
    project_id = project.id
    db_session.commit()
    client = TestClient(create_app())
    created = client.post("/production-runs", json={"video_project_id": str(project_id)})
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    fetched = client.get(f"/production-runs/{run_id}")
    assert fetched.status_code == 200, fetched.text
    cli = runner.invoke(cli_app, ["production", "inspect", "--production-run-id", run_id])
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.output)["id"] == run_id
