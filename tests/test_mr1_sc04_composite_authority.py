from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select

from app.contracts.mr1 import MR1ReapprovalCommand
from app.contracts.pkg1_sc04_revision_closeout import (
    PKG1SC04RevisionApprovalCommand,
)
from app.contracts.workflow import ArtifactVersionCreate
from app.core.errors import ValidationFailureError
from app.db.models import Artifact, ArtifactVersion
from app.services.config_registry import content_hash
from app.services.mr1_reapproval import MR1ReapprovalService, SC04_PROJECT_TYPE
from app.services.mr1_real_production import MR1RealProductionService
from app.services.pkg1_sc04_revision import PKG1SC04RevisionService
from app.services.pkg1_sc04_revision_closeout import (
    PKG1SC04RevisionCloseoutService,
)
from app.services.workflow import ArtifactService
from tests.test_pkg1_sc04_visual_revision import _create_artifact, _scope


def _approved_sc04_revision(db_session, tmp_path):
    source, actor_id, overlay, geo_closeout, _, _, _ = _scope(db_session, tmp_path)
    pending = PKG1SC04RevisionService(db_session).build_revision(
        channel_id=source.channel_workspace_id,
        created_by_user_id=actor_id,
        ads_only_overlay_artifact_version_id=overlay.id,
        ads_only_overlay_content_hash=overlay.content_hash,
        geo_closeout_artifact_version_id=geo_closeout.id,
        geo_closeout_content_hash=geo_closeout.content_hash,
    )
    package_id = uuid.UUID(pending["package_artifact_version_id"])
    package = db_session.get(ArtifactVersion, package_id)
    assert package is not None
    closeout = PKG1SC04RevisionCloseoutService(db_session).closeout(
        PKG1SC04RevisionApprovalCommand(
            project_id=uuid.UUID(pending["video_project_id"]),
            review_task_id=uuid.UUID(pending["human_review_task_ids"][0]),
            reviewed_package_artifact_version_id=package.id,
            reviewed_package_hash=package.content_hash,
            reviewed_revision_id=uuid.UUID(pending["revision_id"]),
            reviewed_revision_version=3,
            reviewed_revision_hash=pending["revision_hash"],
            decided_by_user_id=actor_id,
            decision="PASS",
            decision_source="OPERATOR",
            review_authority="HUMAN",
            operator_decision_text="PASS",
            approval_ref=(
                f"operator-approval://pkg1-sc04-revision/"
                f"{pending['revision_id']}/{pending['revision_hash']}/"
                f"{package.id}/{package.content_hash}"
            ),
        )
    )
    return source, pending, closeout


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inject_complete_reuse_evidence(
    db_session,
    *,
    source,
    actor_id,
    run: ArtifactVersion,
    tmp_path: Path,
) -> tuple[Path, ArtifactVersion]:
    package_artifact = db_session.scalar(
        select(Artifact).where(
            Artifact.video_project_id == source.id,
            Artifact.artifact_type == "package_manifest",
        )
    )
    assert package_artifact is not None and package_artifact.current_version_id
    package = db_session.get(ArtifactVersion, package_artifact.current_version_id)
    assert package is not None
    resolved = MR1ReapprovalService(db_session).resolve_package_artifact_authority(
        project=source, package=package
    )
    refs = resolved["refs"]
    spoken = resolved["versions"]["spoken_text_normalized"].content or {}
    voice = resolved["versions"]["voice_policy"].content or {}
    workspace = tmp_path / "source-mr1-run"
    (workspace / "narration").mkdir(parents=True)
    (workspace / "alignment").mkdir()
    (workspace / "provider_evidence").mkdir()
    (workspace / "temporal").mkdir()

    audio = workspace / "narration" / "narration.mp3"
    audio.write_bytes(b"ID3" + b"reusable-audio" * 256)
    audio_sha = _sha256(audio)
    audio_size = audio.stat().st_size
    audio_duration = 453_346
    audio_ref = f"file-sha256:{audio_sha}"
    normalized_core = {
        "spoken_text": spoken["normalized_text"],
        "spoken_text_hash": "9" * 64,
    }
    normalized = {
        **normalized_core,
        "content_hash": content_hash(normalized_core),
    }
    timing_core = {
        "timing_available": True,
        "audio_asset_ref": audio_ref,
        "audio_duration_ms": audio_duration,
        "spoken_text_hash": normalized["spoken_text_hash"],
    }
    timing_seed = {
        **timing_core,
        "content_hash": content_hash(timing_core),
    }
    narration_request_hash = "a" * 64
    narration = {
        "schema_version": "mr1.elevenlabs-narration-result.v1",
        "provider": "elevenlabs",
        "operation": "narration",
        "request_hash": narration_request_hash,
        "voice_id": voice["voice_identity"]["voice_id"],
        "model_id": voice["voice_identity"]["model_id"],
        "voice_settings": deepcopy(voice["pacing_policy"]["settings"]),
        "normalized_text_hash": spoken["normalized_text_hash"],
        "spoken_text_artifact_version_id": refs["spoken_text_normalized"][
            "artifact_version_id"
        ],
        "audio_path": str(audio.resolve()),
        "audio_asset_ref": audio_ref,
        "audio_sha256": audio_sha,
        "audio_size_bytes": audio_size,
        "audio_duration_ms": audio_duration,
        "timing_seed": timing_seed,
        "temporal_spoken_text_normalized": normalized,
        "provider_call_made": True,
        "network_submit_count": 1,
        "sdk_retry": False,
        "secret_values_exposed": False,
        "actual_cost_usd": None,
    }
    forced_core = {
        "verification_status": "PASS",
        "missing_tokens": [],
        "extra_words": [],
        "spoken_text_hash": normalized["spoken_text_hash"],
        "audio_asset_ref": audio_ref,
        "audio_duration_ms": audio_duration,
    }
    forced = {**forced_core, "content_hash": content_hash(forced_core)}
    alignment_request_hash = "b" * 64
    alignment = {
        "schema_version": "mr1.elevenlabs-forced-alignment-result.v1",
        "provider": "forced_alignment",
        "operation": "forced_alignment",
        "request_hash": alignment_request_hash,
        "provider_request_hash": "c" * 64,
        "provider_response_hash": "d" * 64,
        "audio_path": str(audio.resolve()),
        "audio_asset_ref": audio_ref,
        "audio_sha256": audio_sha,
        "audio_duration_ms": audio_duration,
        "spoken_text_hash": refs["spoken_text_normalized"]["content_hash"],
        "normalized_text_hash": spoken["normalized_text_hash"],
        "forced_alignment_content_hash": forced["content_hash"],
        "forced_alignment_ref": f"forced-alignment:{forced['content_hash']}",
        "forced_alignment_evidence": forced,
        "temporal_spoken_text_normalized": normalized,
        "verification_status": "PASS",
        "token_coverage": 1.0,
        "missing_tokens": [],
        "extra_tokens": [],
        "estimated_timing_fallback_used": False,
        "provider_call_made": True,
        "network_submit_count": 1,
        "sdk_retry": False,
        "secret_values_exposed": False,
        "actual_cost_usd": None,
    }
    for relative, payload in (
        ("provider_evidence/narration-output.json", narration),
        ("alignment/alignment.json", alignment),
        ("provider_evidence/alignment-output.json", alignment),
    ):
        (workspace / relative).write_text(
            json.dumps(payload, sort_keys=True), encoding="utf-8"
        )
    temporal_gate_core = {
        "gate_status": "PASS",
        "block_reasons": [],
    }
    temporal_gate = {
        **temporal_gate_core,
        "content_hash": content_hash(temporal_gate_core),
    }
    (workspace / "temporal" / "temporal-authority-gate.json").write_text(
        json.dumps(temporal_gate, sort_keys=True), encoding="utf-8"
    )

    narration_attempt = {
        "schema_version": "mr1.provider-attempt-ledger.v1",
        "run_id": (run.content or {})["run_id"],
        "operation_key": "elevenlabs:narration",
        "provider": "elevenlabs",
        "operation": "narration",
        "attempt_count": 1,
        "network_submit_started": True,
        "state": "SUCCEEDED",
        "request_hash": narration_request_hash,
    }
    alignment_attempt = {
        "schema_version": "mr1.provider-attempt-ledger.v1",
        "run_id": (run.content or {})["run_id"],
        "operation_key": "elevenlabs:forced_alignment",
        "provider": "forced_alignment",
        "operation": "forced_alignment",
        "attempt_count": 1,
        "network_submit_started": True,
        "state": "SUCCEEDED",
        "request_hash": alignment_request_hash,
    }
    narration_attempt_version = _create_artifact(
        db_session,
        project_id=source.id,
        artifact_type="mr1_provider_attempt_ledger",
        actor_id=actor_id,
        content=narration_attempt,
    )
    alignment_attempt_version = _create_artifact(
        db_session,
        project_id=source.id,
        artifact_type="mr1_provider_attempt_ledger",
        actor_id=actor_id,
        content=alignment_attempt,
    )
    pacing_ref = refs["narration_pacing_preflight_estimate"]
    runtime_gate_core = {
        "schema_version": "mr1.narration-runtime-hard-gate.v1",
        "timing_source": "ACTUAL_PROVIDER_AUDIO_DURATION",
        "pacing_artifact_version_id": pacing_ref["artifact_version_id"],
        "pacing_artifact_content_hash": pacing_ref["content_hash"],
        "minimum_duration_ms": 360_000,
        "maximum_duration_ms": 720_000,
        "actual_duration_ms": audio_duration,
        "inclusive_boundaries": True,
        "reason_codes": [],
        "result": "PASS",
    }
    original_run_content = deepcopy(run.content or {})
    original_run_hash = run.content_hash
    state = deepcopy(original_run_content)
    source_temporal = deepcopy(state.get("temporal_authority") or {})
    scene_windows = [
        deepcopy(window)
        for window in source_temporal.get("scene_windows") or []
        if isinstance(window, dict) and window.get("scene_id") == "SC-04"
    ]
    assert scene_windows == [
        {
            "scene_id": "SC-04",
            "start_ms": 146_020,
            "end_ms": 197_120,
            "duration_ms": 51_100,
        }
    ]
    timeline_core = {
        "schema_version": "mr1.sc04-canonical-timeline-authority.v1",
        "timing_authority": "CANONICAL_MEDIA_TIMELINE",
        "audio_asset_ref": audio_ref,
        "audio_duration_ms": audio_duration,
        "scene_windows": scene_windows,
    }
    timeline_hash = content_hash(timeline_core)
    timeline_path = workspace / "temporal" / "canonical-media-timeline.json"
    timeline_path.write_text(
        json.dumps(
            {**timeline_core, "timeline_hash": timeline_hash},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    state.update(
        {
            "workspace": str(workspace.resolve()),
            "provider_outputs": {
                "narration": narration,
                "alignment": alignment,
            },
            "narration_runtime_gate": {
                **runtime_gate_core,
                "content_hash": content_hash(runtime_gate_core),
            },
            "temporal_authority": {
                "result": "PASS",
                "state": "CANONICAL_TIMELINE_READY",
                "timing_authority": "CANONICAL_MEDIA_TIMELINE",
                "token_coverage": 1.0,
                "estimated_timing_fallback_used": False,
                "audio_asset_ref": audio_ref,
                "audio_duration_ms": audio_duration,
                "timeline_ref": str(timeline_path.resolve()),
                "timeline_hash": timeline_hash,
                "scene_windows": scene_windows,
                "verified_alignment_hash": "1" * 64,
                "temporal_gate_hash": temporal_gate["content_hash"],
            },
        }
    )
    state.setdefault("attempts", {}).update(
        {
            "elevenlabs:narration": narration_attempt,
            "elevenlabs:forced_alignment": alignment_attempt,
        }
    )
    state.setdefault("attempt_artifact_ids", {}).update(
        {
            "elevenlabs:narration": str(narration_attempt_version.artifact_id),
            "elevenlabs:forced_alignment": str(alignment_attempt_version.artifact_id),
        }
    )
    persisted_run = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=run.artifact_id,
            parent_version_id=run.id,
            content=state,
            status="submitted",
            created_by_user_id=run.created_by_user_id,
        ),
        correlation_id="test-sc04-complete-reuse-evidence-run-version",
    )
    db_session.refresh(run)
    run_artifact = db_session.get(Artifact, run.artifact_id)
    assert run.content == original_run_content
    assert run.content_hash == original_run_hash
    assert persisted_run.parent_version_id == run.id
    assert persisted_run.content_hash == content_hash(state)
    assert run_artifact is not None
    assert run_artifact.current_version_id == persisted_run.id
    return audio, persisted_run


def _approved_sc04_revision_with_reuse(db_session, tmp_path):
    source, actor_id, overlay, geo_closeout, _, _, run = _scope(db_session, tmp_path)
    audio, persisted_run = _inject_complete_reuse_evidence(
        db_session,
        source=source,
        actor_id=actor_id,
        run=run,
        tmp_path=tmp_path,
    )
    assert (persisted_run.content or {}).get("workspace")
    assert (persisted_run.content or {}).get("provider_outputs")
    pending = PKG1SC04RevisionService(db_session).build_revision(
        channel_id=source.channel_workspace_id,
        created_by_user_id=actor_id,
        ads_only_overlay_artifact_version_id=overlay.id,
        ads_only_overlay_content_hash=overlay.content_hash,
        geo_closeout_artifact_version_id=geo_closeout.id,
        geo_closeout_content_hash=geo_closeout.content_hash,
    )
    package = db_session.get(
        ArtifactVersion, uuid.UUID(pending["package_artifact_version_id"])
    )
    assert package is not None
    closeout = PKG1SC04RevisionCloseoutService(db_session).closeout(
        PKG1SC04RevisionApprovalCommand(
            project_id=uuid.UUID(pending["video_project_id"]),
            review_task_id=uuid.UUID(pending["human_review_task_ids"][0]),
            reviewed_package_artifact_version_id=package.id,
            reviewed_package_hash=package.content_hash,
            reviewed_revision_id=uuid.UUID(pending["revision_id"]),
            reviewed_revision_version=3,
            reviewed_revision_hash=pending["revision_hash"],
            decided_by_user_id=actor_id,
            decision="PASS",
            decision_source="OPERATOR",
            review_authority="HUMAN",
            operator_decision_text="PASS",
            approval_ref=(
                f"operator-approval://pkg1-sc04-revision/"
                f"{pending['revision_id']}/{pending['revision_hash']}/"
                f"{package.id}/{package.content_hash}"
            ),
        )
    )
    return source, pending, closeout, audio


def test_sc04_mr1_reapproval_uses_composite_visual_authority_and_fails_closed_reuse(
    db_session, tmp_path
) -> None:
    source, pending, closeout = _approved_sc04_revision(db_session, tmp_path)
    package = pending["package"]
    bindings = package["exact_bindings"]
    no_execution_before = PKG1SC04RevisionService(
        db_session
    ).source_service._no_execution_counts()

    result = MR1ReapprovalService(db_session).approve(
        MR1ReapprovalCommand(
            project_id=uuid.UUID(pending["video_project_id"]),
            pkg1_approval_decision_id=uuid.UUID(closeout["approval_decision_id"]),
            pkg1_human_review_receipt_version_id=uuid.UUID(
                closeout["human_review_receipt_artifact_version_id"]
            ),
            channel_profile_version_id=uuid.UUID(
                bindings["channel_profile_version"]["id"]
            ),
            compiled_policy_snapshot_id=uuid.UUID(
                bindings["compiled_channel_policy_snapshot"]["id"]
            ),
        )
    )

    assert result["MR1_REAPPROVAL_FINAL"] == "PASS"
    assert result["MR1_REUSE_DECISIONS"] == "PASS"
    assert result["MR1_EXECUTION"] == "NOT_STARTED"
    assert result["provider_calls"] == 0
    assert result["render_calls"] == 0
    assert result["exact_target"]["project_type"] == "PKG1_SC04_REVISION"
    assert (
        result["exact_bindings"]["supplemental_visual_alignment"]
        == (bindings["supplemental_visual_alignment"])
    )
    assert (
        result["exact_bindings"]["market_alignment_dossier_visual_scope"]
        == "HISTORICAL_NONVISUAL_COMPONENTS_ONLY"
    )
    assert (
        result["exact_bindings"]["niche_alignment_dossier_visual_scope"]
        == "HISTORICAL_NONVISUAL_COMPONENTS_ONLY"
    )

    reuse = result["reuse_decision"]
    assert reuse["schema_version"] == "mr1.reuse-decision-manifest.v1"
    assert reuse["fail_closed"] is True
    assert reuse["fresh_run_required"] is True
    assert reuse["reuse_allowed_output_keys"] == []
    assert reuse["prior_output_reuse_count"] == 0
    by_key = {item["output_key"]: item for item in reuse["entries"]}
    assert by_key["scene:SC-04"]["classification"] == ("INVALIDATED_BY_REVISION")
    for entry in reuse["entries"]:
        assert entry["classification"] in {
            "REUSE_VALID",
            "INVALIDATED_BY_REVISION",
            "MISSING",
            "REQUIRES_NEW_EXECUTION",
        }
        assert entry["reuse_authorized"] is False
        assert set(
            (
                "request_identity_proof",
                "script_binding_proof",
                "provider_model_settings_proof",
                "checksum_proof",
                "rights_proof",
                "qc_proof",
            )
        ).issubset(entry)
    assert (
        PKG1SC04RevisionService(db_session).source_service._no_execution_counts()
        == no_execution_before
    )


def test_sc04_mr1_provider_plan_rejects_unchanged_scene_rebinding(
    db_session, tmp_path
) -> None:
    _source, pending, _closeout = _approved_sc04_revision(db_session, tmp_path)
    manifest = pending["package"]
    provider_ref = manifest["effective_artifacts"]["provider_execution_plan"]
    provider_version = db_session.get(
        ArtifactVersion, uuid.UUID(provider_ref["artifact_version_id"])
    )
    assert provider_version is not None
    source_ref = (provider_version.content or {})["supersedes"]
    source_version = db_session.get(
        ArtifactVersion, uuid.UUID(source_ref["artifact_version_id"])
    )
    assert source_version is not None

    service = MR1ReapprovalService(db_session)
    service._validate_provider_plan(
        plan=provider_version.content or {},
        revised=manifest["effective_artifacts"],
        revision_id=manifest["revision_id"],
        revision_hash=manifest["revision_hash"],
        source_plan=source_version.content or {},
    )

    drifted = deepcopy(provider_version.content or {})
    unchanged = next(
        item for item in drifted["scene_routes"] if item["scene_id"] != "SC-04"
    )
    unchanged["idempotency_ref"] = (
        f"provider-plan://{manifest['revision_id']}/{unchanged['scene_id']}"
    )
    with pytest.raises(
        ValidationFailureError,
        match="MR1_SCENE_ROUTE_ATTEMPT_INVALID",
    ):
        service._validate_provider_plan(
            plan=drifted,
            revised=manifest["effective_artifacts"],
            revision_id=manifest["revision_id"],
            revision_hash=manifest["revision_hash"],
            source_plan=source_version.content or {},
        )


def _approve_mr1(db_session, pending, closeout):
    bindings = pending["package"]["exact_bindings"]
    return MR1ReapprovalService(db_session).approve(
        MR1ReapprovalCommand(
            project_id=uuid.UUID(pending["video_project_id"]),
            pkg1_approval_decision_id=uuid.UUID(closeout["approval_decision_id"]),
            pkg1_human_review_receipt_version_id=uuid.UUID(
                closeout["human_review_receipt_artifact_version_id"]
            ),
            channel_profile_version_id=uuid.UUID(
                bindings["channel_profile_version"]["id"]
            ),
            compiled_policy_snapshot_id=uuid.UUID(
                bindings["compiled_channel_policy_snapshot"]["id"]
            ),
        )
    )


def test_sc04_complete_proof_reuses_only_narration_and_alignment(
    db_session, tmp_path
) -> None:
    _source, pending, closeout, _audio = _approved_sc04_revision_with_reuse(
        db_session, tmp_path
    )
    result = _approve_mr1(db_session, pending, closeout)
    reuse = result["reuse_decision"]
    assert reuse["reuse_allowed_output_keys"] == [
        "narration_audio",
        "forced_alignment",
    ]
    assert reuse["prior_output_reuse_count"] == 2
    assert reuse["fresh_provider_call_plan"] == {
        "elevenlabs_narration": 0,
        "elevenlabs_forced_alignment": 0,
    }
    assert reuse["fresh_elevenlabs_execution_cost_usd"] == 0.0
    assert reuse["canonical_timeline_reuse_authorized"] is False
    assert reuse["supporting_visual_subwindows_reuse_authorized"] is False
    assert reuse["fresh_temporal_compilation_required"] is True
    assert reuse["fresh_caption_compilation_required"] is True
    by_key = {item["output_key"]: item for item in reuse["entries"]}
    assert by_key["narration_audio"]["classification"] == "REUSE_VALID"
    assert by_key["forced_alignment"]["classification"] == "REUSE_VALID"
    assert by_key["canonical_timeline_and_captions"]["classification"] == (
        "REQUIRES_NEW_EXECUTION"
    )
    assert by_key["scene:SC-04"]["classification"] == ("INVALIDATED_BY_REVISION")

    fresh_workspace = tmp_path / "fresh-run"
    fresh_workspace.mkdir()
    materialized = MR1RealProductionService(
        db_session, tmp_path / "unused-runtime-root"
    )._materialize_approved_reuse(
        authority={
            "package_variant": SC04_PROJECT_TYPE,
            "reuse_decision_manifest": reuse,
            "reuse_decision_manifest_ref": {
                "artifact_version_id": result["reuse_decision_artifact_version_id"],
                "content_hash": result["reuse_decision_content_hash"],
            },
        },
        workspace=fresh_workspace,
        run_id=uuid.uuid4(),
    )
    assert set(materialized["provider_outputs"]) == {"narration", "alignment"}
    assert set(materialized["receipts"]) == {
        "narration_audio",
        "forced_alignment",
    }
    assert (
        materialized["provider_outputs"]["narration"][
            "provider_call_made_in_current_run"
        ]
        is False
    )
    assert (
        materialized["provider_outputs"]["alignment"][
            "provider_call_made_in_current_run"
        ]
        is False
    )
    assert (fresh_workspace / "narration" / "narration.mp3").is_file()
    assert (fresh_workspace / "alignment" / "alignment.json").is_file()
    assert not (fresh_workspace / "temporal").exists()


def test_sc04_reuse_tamper_falls_back_to_fresh_execution(db_session, tmp_path) -> None:
    _source, pending, closeout, audio = _approved_sc04_revision_with_reuse(
        db_session, tmp_path
    )
    audio.write_bytes(audio.read_bytes() + b"tamper")
    result = _approve_mr1(db_session, pending, closeout)
    reuse = result["reuse_decision"]
    assert reuse["reuse_allowed_output_keys"] == []
    assert reuse["prior_output_reuse_count"] == 0
    by_key = {item["output_key"]: item for item in reuse["entries"]}
    assert by_key["narration_audio"]["classification"] == ("REQUIRES_NEW_EXECUTION")
    assert (
        "NARRATION_AUDIO_SHA256_MISMATCH" in by_key["narration_audio"]["reason_codes"]
    )
    assert by_key["forced_alignment"]["classification"] == ("REQUIRES_NEW_EXECUTION")
    assert (
        "REUSABLE_NARRATION_PREREQUISITE_FAILED"
        in by_key["forced_alignment"]["reason_codes"]
    )


def test_runtime_reuse_rehashes_again_and_rejects_post_approval_tamper(
    db_session, tmp_path
) -> None:
    _source, pending, closeout, audio = _approved_sc04_revision_with_reuse(
        db_session, tmp_path
    )
    result = _approve_mr1(db_session, pending, closeout)
    audio.write_bytes(audio.read_bytes() + b"post-approval-tamper")
    workspace = tmp_path / "fresh-run-tampered"
    workspace.mkdir()
    with pytest.raises(
        ValidationFailureError,
        match="MR1_REUSE_SOURCE_FILE_MISSING_OR_TAMPERED",
    ):
        MR1RealProductionService(
            db_session, tmp_path / "unused-runtime-root"
        )._materialize_approved_reuse(
            authority={
                "package_variant": SC04_PROJECT_TYPE,
                "reuse_decision_manifest": result["reuse_decision"],
                "reuse_decision_manifest_ref": {
                    "artifact_version_id": result["reuse_decision_artifact_version_id"],
                    "content_hash": result["reuse_decision_content_hash"],
                },
            },
            workspace=workspace,
            run_id=uuid.uuid4(),
        )
