from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.db.models import (
    AccessibilityQCReport,
    Artifact,
    ArtifactVersion,
    CaptionTrackSnapshot,
    MediaQCReport,
    MediaRenderJob,
    RenderPackageSnapshot,
    RenderSpecSnapshot,
    SourceManifestSnapshot,
    VisualPlanSnapshot,
    VoiceTimelineSnapshot,
)

from .ffmpeg_checks import sha256_file
from .qualification_asserts import assert_artifact_types, assert_file_ref_is_verified


def assert_m5_project_lineage(session, flow) -> None:
    project = flow.project
    assert project.policy_snapshot_id == flow.snapshot.id
    assert flow.admission.admitted_video_project_id == project.id
    assert flow.daily_run.project_admission_decision_id == flow.admission.id
    assert flow.idea.llm_run_snapshot_id is not None
    artifact_types = {
        artifact.artifact_type
        for artifact in session.scalars(select(Artifact).where(Artifact.video_project_id == project.id)).all()
    }
    initial_types = {"creative_brief", "research_pack", "source_pack"}
    if hasattr(flow, "production_run"):
        assert initial_types <= artifact_types
        assert not {"publish", "upload", "analytics"} & artifact_types
    else:
        assert_artifact_types(artifact_types, initial_types)
    versions = session.scalars(
        select(ArtifactVersion).join(Artifact, ArtifactVersion.artifact_id == Artifact.id).where(Artifact.video_project_id == project.id)
    ).all()
    assert versions
    for version in versions:
        artifact = session.get(Artifact, version.artifact_id)
        if artifact.artifact_type in initial_types:
            assert version.context_refs
            assert str(flow.idea.context_pack_snapshot_id) in str(version.context_refs)


def assert_m6_render_lineage(session, run) -> None:
    assert run.script_artifact_version_id is not None
    assert run.voice_timeline_snapshot_id is not None
    assert run.caption_track_snapshot_id is not None
    assert run.visual_plan_snapshot_id is not None
    assert run.scene_manifest_snapshot_id is not None
    assert run.asset_manifest_snapshot_id is not None
    assert run.source_manifest_snapshot_id is not None
    assert run.render_spec_snapshot_id is not None
    assert run.render_package_snapshot_id is not None
    assert run.media_qc_report_id is not None
    assert run.accessibility_qc_report_id is not None

    voice = session.get(VoiceTimelineSnapshot, run.voice_timeline_snapshot_id)
    captions = session.get(CaptionTrackSnapshot, run.caption_track_snapshot_id)
    visual = session.get(VisualPlanSnapshot, run.visual_plan_snapshot_id)
    source_manifest = session.get(SourceManifestSnapshot, run.source_manifest_snapshot_id)
    render_spec = session.get(RenderSpecSnapshot, run.render_spec_snapshot_id)
    package = session.get(RenderPackageSnapshot, run.render_package_snapshot_id)
    media_qc = session.get(MediaQCReport, run.media_qc_report_id)
    access_qc = session.get(AccessibilityQCReport, run.accessibility_qc_report_id)

    assert voice.production_artifact_run_id == run.id
    assert captions.voice_timeline_snapshot_id == voice.id
    assert visual.voice_timeline_snapshot_id == voice.id
    assert render_spec.voice_timeline_snapshot_id == voice.id
    assert render_spec.caption_track_snapshot_id == captions.id
    assert package.render_spec_snapshot_id == render_spec.id
    assert media_qc.render_package_snapshot_id == package.id
    assert media_qc.render_spec_snapshot_id == render_spec.id
    assert access_qc.caption_track_snapshot_id == captions.id
    assert source_manifest.source_manifest_blob["provider_classification_summary"]["real_provider_calls"] == 0
    assert source_manifest.source_manifest_blob["provider_classification_summary"]["envato_api_calls"] == 0

    final_ref = package.final_video_ref
    assert_file_ref_is_verified(final_ref)
    video_path = Path(final_ref["file_path"])
    assert sha256_file(video_path) == final_ref["checksum"]
    assert media_qc.qc_state == "PASS"
    assert access_qc.qc_state in {"PASS", "REVIEW_REQUIRED"}
