from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.contracts.m6 import RenderSpecContract, SceneSourceDecisionContract, VoiceTimelineContract

pytestmark = pytest.mark.skip(
    reason="Historical pre-M12 local fixture renderer contract; M12.1R cutover coverage lives in tests/test_m12_1r_mock_runtime_purge.py."
)
from app.db.models import AssetManifestSnapshot, MediaQCReport, RenderPackageSnapshot, RenderSpecSnapshot, SourceManifestSnapshot
from app.services import MediaQCService, SceneSourceDecisionService

from .helpers.ffmpeg_checks import ffprobe_duration
from .helpers.git_checks import is_gitignored
from .helpers.lineage_asserts import assert_m6_render_lineage


def test_m6_local_render_media_qc_rights_and_generated_media_safety(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    run = flow.production_run
    assert run.status == "COMPLETED"
    assert_m6_render_lineage(db_session, run)
    package = db_session.get(RenderPackageSnapshot, run.render_package_snapshot_id)
    video_path = Path(package.final_video_ref["file_path"])
    assert video_path.exists()
    assert video_path.stat().st_size > 0
    assert ffprobe_duration(video_path) > 0
    assert abs(float(package.duration_seconds) - float(db_session.get(RenderSpecSnapshot, run.render_spec_snapshot_id).render_spec_blob["total_duration_seconds"])) <= 0.75
    assert is_gitignored(video_path) or str(video_path).startswith(str(tmp_path))

    assets = db_session.get(AssetManifestSnapshot, run.asset_manifest_snapshot_id)
    for candidate in assets.asset_manifest_blob["candidates"]:
        if candidate["source_type"] == "LOCAL_FIXTURE":
            assert candidate["rights_envelope"]["license_state"] == "INTERNAL_TEST_ONLY"
    source = db_session.get(SourceManifestSnapshot, run.source_manifest_snapshot_id)
    summary = source.source_manifest_blob["provider_classification_summary"]
    assert summary["real_provider_calls"] == 0
    assert summary["envato_api_calls"] == 0


def test_m6_contracts_reject_bad_timing_and_scene_source_is_safe() -> None:
    with pytest.raises(ValidationError):
        VoiceTimelineContract(
            total_duration_seconds=3,
            segments=[
                {
                    "narration_segment_id": "nar_001",
                    "text": "one",
                    "sequence_index": 0,
                    "estimated_start_time": 0,
                    "estimated_end_time": 2,
                    "estimated_duration_seconds": 2,
                },
                {
                    "narration_segment_id": "nar_002",
                    "text": "overlap",
                    "sequence_index": 1,
                    "estimated_start_time": 1,
                    "estimated_end_time": 3,
                    "estimated_duration_seconds": 2,
                },
            ],
            timing_source="ESTIMATED",
            timeline_hash="hash",
            confidence_level="MEDIUM",
        )
    high_risk = SceneSourceDecisionService().decide(
        scene_type="GENERIC_BROLL",
        importance="HIGH",
        specificity="HIGH",
        factual_risk="HIGH",
        need_realism="HIGH",
        approved_asset_pool_match=False,
    )
    process = SceneSourceDecisionService().decide(
        scene_type="PROCESS",
        importance="MEDIUM",
        specificity="MEDIUM",
        factual_risk="LOW",
        need_realism="LOW",
        approved_asset_pool_match=False,
    )
    assert isinstance(high_risk, SceneSourceDecisionContract)
    assert high_risk.preferred_source != "AI_PLACEHOLDER"
    assert high_risk.preferred_source == "SCREENSHOT_PLACEHOLDER"
    assert process.preferred_source == "DIAGRAM_PLACEHOLDER"


def test_m6_media_qc_blocks_missing_file_checksum_or_duration(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    package = db_session.get(RenderPackageSnapshot, flow.production_run.render_package_snapshot_id)
    package.final_video_ref = {**package.final_video_ref, "file_path": str(tmp_path / "missing.mp4")}
    blocked = MediaQCService(db_session).run_qc(render_package_snapshot=package)
    assert blocked.qc_state == "BLOCK"
    assert "MEDIA_QC_BLOCKED" in blocked.reason_codes

    package.final_video_ref = {**package.final_video_ref, "checksum": ""}
    package.checksum_manifest = {}
    blocked_checksum = MediaQCService(db_session).run_qc(render_package_snapshot=package)
    assert blocked_checksum.qc_state == "BLOCK"
    assert "MANIFEST_CHECKSUM_MISSING" in blocked_checksum.reason_codes
    assert db_session.scalars(select(MediaQCReport).where(MediaQCReport.qc_state == "BLOCK")).all()


def test_m6_bad_render_spec_contract_creates_no_render_job(db_session, qualification_factory) -> None:
    flow = qualification_factory.m5_admitted_project()
    with pytest.raises(ValidationError):
        RenderSpecContract(
            render_spec_id="bad",
            video_project_id=flow.project.id,
            voice_timeline_snapshot_id=flow.snapshot.id,
            visual_plan_snapshot_id=flow.snapshot.id,
            caption_track_snapshot_id=flow.snapshot.id,
            asset_manifest_snapshot_id=flow.snapshot.id,
            scene_manifest_snapshot_id=flow.snapshot.id,
            scenes=[],
            render_variants=[],
            audio_tracks=[],
            caption_track_ref="missing",
            default_export_profile={"aspect_ratio": "16:9", "resolution_width": 1280, "resolution_height": 720, "fps": 30},
            render_intent="LOCAL_SMOKE",
            total_duration_seconds=10,
            render_spec_hash="hash",
        )
    from app.db.models import MediaRenderJob

    assert db_session.scalars(select(MediaRenderJob)).all() == []
