from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

import app.services.m6 as m6_service

pytestmark = pytest.mark.skip(
    reason="Historical pre-M12 mock/local-render e2e contract; M12.1R cutover coverage lives in tests/test_m12_1r_mock_runtime_purge.py."
)
from app.contracts.m6 import ProductionArtifactRunCreate, RenderSpecContract
from app.contracts.workflow import VideoProjectCreate
from app.db.models import Artifact, DailyIdeaDecision, MediaRenderJob, RenderPackageSnapshot, RenderSpecSnapshot, VideoProject
from app.services import MediaQCService, ProductionArtifactRunService

from .helpers.git_checks import is_gitignored, staged_binary_media
from .helpers.lineage_asserts import assert_m5_project_lineage, assert_m6_render_lineage
from .helpers.repo_scanners import all_scope_violations


def test_e2e_a_full_happy_path_local_smoke_lineage_and_scope(engine, db_session, qualification_factory) -> None:
    output_dir = Path("var/generated/pre_m7/e2e_a")
    flow = qualification_factory.m6_full_flow(output_dir=output_dir)
    assert flow.daily_run.status == "COMPLETED"
    assert flow.admission.decision == "ADMIT"
    assert flow.production_run.status == "COMPLETED"
    assert_m5_project_lineage(db_session, flow)
    assert_m6_render_lineage(db_session, flow.production_run)
    package = db_session.get(RenderPackageSnapshot, flow.production_run.render_package_snapshot_id)
    assert is_gitignored(Path(package.final_video_ref["file_path"]))
    assert staged_binary_media() == []
    assert all_scope_violations(engine) == []


def test_e2e_b_missing_policy_snapshot_fails_before_project_run_or_artifact(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="E2E missing snapshot")
    with pytest.raises(ValidationError):
        VideoProjectCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            title="missing snapshot",
            created_by_user_id=scope.operator.id,
        )
    assert db_session.query(VideoProject).count() == 0
    assert db_session.query(Artifact).count() == 0


def test_e2e_c_malformed_m5_llm_creates_no_decision_project_or_artifact(db_session, qualification_factory) -> None:
    flow = qualification_factory.m5_admitted_project(mock_mode="malformed")
    assert flow.daily_run.status == "FAILED"
    assert db_session.query(DailyIdeaDecision).count() == 0
    assert db_session.query(VideoProject).count() == 0
    assert db_session.query(Artifact).count() == 0


def test_e2e_d_malformed_m6_script_creates_no_bad_script_or_render_package(db_session, qualification_factory) -> None:
    flow = qualification_factory.m5_admitted_project()
    run = ProductionArtifactRunService(db_session).create_run(data=ProductionArtifactRunCreate(video_project_id=flow.project.id))
    with pytest.raises(Exception):
        ProductionArtifactRunService(db_session).execute_local_mock_flow(run_id=run.id, mock_mode="malformed")
    db_session.refresh(run)
    assert run.status == "FAILED"
    assert run.script_artifact_version_id is None
    assert db_session.scalars(select(RenderPackageSnapshot)).all() == []


def test_e2e_e_f_quota_and_provider_unavailable_stop_unsafe_continuation(db_session, qualification_factory) -> None:
    quota = qualification_factory.m5_admitted_project(quota_limit=Decimal("0"))
    assert quota.daily_run.status == "BLOCKED"
    assert "PROVIDER_QUOTA_BLOCKED" in quota.daily_run.reason_codes
    assert db_session.query(VideoProject).count() == 0

    provider = qualification_factory.m5_admitted_project(provider_health_mode="unavailable")
    assert provider.daily_run.status == "BLOCKED"
    assert "PROVIDER_HEALTH_BLOCKED" in provider.daily_run.reason_codes
    assert db_session.query(VideoProject).count() == 0


def test_e2e_g_invalid_scene_timing_creates_no_render_spec_or_job(db_session, qualification_factory) -> None:
    flow = qualification_factory.m5_admitted_project()
    with pytest.raises(ValidationError):
        RenderSpecContract(
            render_spec_id="invalid-timing",
            video_project_id=flow.project.id,
            voice_timeline_snapshot_id=flow.snapshot.id,
            visual_plan_snapshot_id=flow.snapshot.id,
            caption_track_snapshot_id=flow.snapshot.id,
            asset_manifest_snapshot_id=flow.snapshot.id,
            scene_manifest_snapshot_id=flow.snapshot.id,
            scenes=[
                {"scene_id": "s1", "start_time": 0, "end_time": 2, "narration_segment_id": "n1", "visual_asset_ref": "local://one"},
                {"scene_id": "s2", "start_time": 1, "end_time": 3, "narration_segment_id": "n2", "visual_asset_ref": "local://two"},
            ],
            render_variants=[
                {
                    "variant_id": "default",
                    "platform": "YOUTUBE",
                    "surface": "LONG_FORM",
                    "aspect_ratio": "16:9",
                    "resolution_width": 1280,
                    "resolution_height": 720,
                    "fps": 30,
                    "crop_strategy": "LETTERBOX",
                    "caption_placement": {"placement_key": "lower_third"},
                    "safe_area_profile": {"profile_key": "safe"},
                    "export_filename": "bad.mp4",
                    "variant_status": "READY",
                }
            ],
            audio_tracks=[{"track_id": "a", "track_type": "SILENT", "duration_seconds": 3}],
            caption_track_ref="caption",
            default_export_profile={"aspect_ratio": "16:9", "resolution_width": 1280, "resolution_height": 720, "fps": 30},
            render_intent="LOCAL_SMOKE",
            total_duration_seconds=3,
            render_spec_hash="hash",
        )
    assert db_session.scalars(select(RenderSpecSnapshot)).all() == []
    assert db_session.scalars(select(MediaRenderJob)).all() == []


def test_e2e_h_missing_asset_or_ref_blocks_qc(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    package = db_session.get(RenderPackageSnapshot, flow.production_run.render_package_snapshot_id)
    package.final_video_ref = None
    report = MediaQCService(db_session).run_qc(render_package_snapshot=package)
    assert report.qc_state == "BLOCK"
    assert "MEDIA_QC_BLOCKED" in report.reason_codes


def test_e2e_i_ffmpeg_unavailable_is_blocked_not_fake_pass(db_session, qualification_factory, monkeypatch) -> None:
    monkeypatch.setattr(m6_service.shutil, "which", lambda name: None)
    flow = qualification_factory.m5_admitted_project()
    run = ProductionArtifactRunService(db_session).create_run(data=ProductionArtifactRunCreate(video_project_id=flow.project.id))
    executed = ProductionArtifactRunService(db_session).execute_local_mock_flow(run_id=run.id)
    assert executed.status == "BLOCKED"
    assert "FFMPEG_UNAVAILABLE" in executed.reason_codes
    assert executed.render_package_snapshot_id is None


def test_e2e_j_generated_media_safety_and_checksum(db_session, qualification_factory) -> None:
    output_dir = Path("var/generated/pre_m7/e2e_j")
    flow = qualification_factory.m6_full_flow(output_dir=output_dir)
    package = db_session.get(RenderPackageSnapshot, flow.production_run.render_package_snapshot_id)
    video_path = Path(package.final_video_ref["file_path"])
    assert video_path.exists()
    assert is_gitignored(video_path)
    assert package.final_video_ref["checksum"] == package.checksum_manifest["final_video"]
    assert staged_binary_media() == []


def test_e2e_k_scope_leak_sentinel(engine) -> None:
    assert all_scope_violations(engine) == []
