from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts.production_workflow import (
    ProductionWorkflowStage,
    WorkflowAuthorityRefs,
    WorkflowStageResult,
)
from app.contracts.vcos_v2 import ProductionLane
from app.core.errors import ValidationFailureError
from app.services import production_workflow, v2_native_effects
from app.services.production_workflow import (
    GatewayBackedPostReadinessStageHandler,
    PostReadinessProductionGatewayDescriptor,
    ProductionWorkflowCoordinator,
    WorkflowStageError,
)
from app.services.production_publish import (
    _final_review_candidate_hash_candidates,
    stable_hash,
)
from app.services.v2_native_effects import (
    V2_ELEVENLABS_NARRATION_STRATEGY,
    V2_LOCAL_ADAPTER_KEY,
    V2_LOCAL_NARRATION_STRATEGY,
    V2LocalNativeProductionAdapter,
    _ai_visual_creative_gate_checks,
    _persist_ai_visual_qc_artifact_set,
    _validate_ai_visual_run_bindings,
)
from app.services.v2_provider_production import V2AuthorizedAdapterOperation
from app.services.v2_drive_archive import (
    V2GoogleDriveRemoteArchiveAdapter,
    _drive_archive_lineage_content,
    _drive_archive_receipt_content,
    _remote_archive_request_identity,
    has_exact_governed_drive_archive_reconciliation_authority,
)


def test_pre_0079_native_candidate_hash_is_replayable_but_never_used_by_ai() -> None:
    target = {"destination_mode": "FINAL_REVIEW_ONLY"}
    payload = {
        "schema_version": "vcos.final-review-candidate.v2",
        "workflow_run_id": str(uuid.uuid4()),
        "ai_visual_production_run_id": None,
        "ai_visual_asset_manifest_hash": None,
        "ffmpeg_effect_plan_hash": None,
        "target_market_lineage": target,
    }
    historical = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "ai_visual_production_run_id",
            "ai_visual_asset_manifest_hash",
            "ffmpeg_effect_plan_hash",
        }
    }
    historical_hash = stable_hash(historical)

    native_candidates = _final_review_candidate_hash_candidates(
        payload=payload,
        target_market_lineage=target,
        allow_historical_native_payload=True,
    )
    ai_candidates = _final_review_candidate_hash_candidates(
        payload={
            **payload,
            "ai_visual_production_run_id": str(uuid.uuid4()),
            "ai_visual_asset_manifest_hash": "a" * 64,
            "ffmpeg_effect_plan_hash": "b" * 64,
        },
        target_market_lineage=target,
        allow_historical_native_payload=False,
    )

    assert historical_hash in native_candidates
    assert historical_hash not in ai_candidates


def test_ai_visual_run_routes_render_and_qc_away_from_legacy_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual_run_id = uuid.uuid4()
    context = SimpleNamespace(
        run=SimpleNamespace(ai_visual_production_run_id=visual_run_id)
    )
    render_result = (object(), {"render_mode": "AI_VISUAL_ASSEMBLY_ONLY"})
    qc_result = (object(), {"qc_mode": "AI_VISUAL_ONLY"})

    def render_ai(self, **kwargs):
        assert kwargs["context"] is context
        return render_result

    def qc_ai(self, **kwargs):
        assert kwargs["context"] is context
        return qc_result

    monkeypatch.setattr(V2LocalNativeProductionAdapter, "_render_ai_visual", render_ai)
    monkeypatch.setattr(
        V2LocalNativeProductionAdapter, "_quality_control_ai_visual", qc_ai
    )
    adapter = object.__new__(V2LocalNativeProductionAdapter)

    assert (
        adapter._render(ledger_id=uuid.uuid4(), context=context, operation=object())
        is render_result
    )
    assert (
        adapter._quality_control(
            ledger_id=uuid.uuid4(), context=context, operation=object()
        )
        is qc_result
    )


@pytest.mark.parametrize(
    ("execution_mode", "expected_stage"),
    [
        ("REAL_LONG_FORM_PRODUCTION", ProductionWorkflowStage.VISUAL),
        ("QUALIFICATION_LOCAL", ProductionWorkflowStage.RENDER),
    ],
)
def test_worker_post_media_route_never_revives_native_for_real_production(
    monkeypatch: pytest.MonkeyPatch,
    execution_mode: str,
    expected_stage: ProductionWorkflowStage,
) -> None:
    scheduled: list[ProductionWorkflowStage] = []
    coordinator = ProductionWorkflowCoordinator(
        SimpleNamespace(),
        now=lambda: datetime(2026, 8, 14, tzinfo=UTC),
    )
    monkeypatch.setattr(
        production_workflow,
        "_post_readiness_execution_mode",
        lambda _session, _run: execution_mode,
    )
    monkeypatch.setattr(
        coordinator,
        "_schedule_stage",
        lambda _run, stage, **_kwargs: scheduled.append(stage),
    )
    run = SimpleNamespace(
        id=uuid.uuid4(),
        ai_visual_production_run_id=None,
        metadata_={"max_attempts": 5},
        current_stage="MEDIA",
        state="MEDIA_RUNNING",
        state_reason_codes=[],
        last_progress_at=None,
        projection_version=3,
    )
    receipt = SimpleNamespace(
        stage="MEDIA",
        domain_event_id=uuid.uuid4(),
    )

    coordinator._advance_after_receipt(run, receipt)

    assert run.current_stage == expected_stage.value
    assert scheduled == [expected_stage]
    assert run.ai_visual_production_run_id is None


def test_reconcile_after_media_receipt_never_revives_native_for_real_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = SimpleNamespace(
        stage="MEDIA",
        authority_refs={},
        completed_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
    session = SimpleNamespace(
        scalars=lambda _query: SimpleNamespace(all=lambda: [receipt])
    )
    coordinator = ProductionWorkflowCoordinator(
        session,
        now=lambda: datetime(2026, 8, 14, tzinfo=UTC),
    )
    monkeypatch.setattr(
        production_workflow,
        "_post_readiness_execution_mode",
        lambda _session, _run: "REAL_LONG_FORM_PRODUCTION",
    )
    monkeypatch.setattr(
        coordinator,
        "_replace_projection_from_exact_refs",
        lambda _run, _refs: None,
    )
    run = SimpleNamespace(
        id=uuid.uuid4(),
        video_project_id=None,
        ai_visual_production_run_id=None,
        state="MEDIA_RUNNING",
        current_stage="MEDIA",
        last_progress_at=None,
        projection_version=3,
    )

    coordinator._reconcile_locked(run)

    assert run.current_stage == ProductionWorkflowStage.VISUAL.value
    assert run.state == "VISUAL_PENDING"


def test_real_render_gateway_blocks_before_any_renderer_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import ai_visual_rerender_authority

    calls: list[str] = []

    class Gateway:
        descriptor = PostReadinessProductionGatewayDescriptor(
            gateway_id="ai-only-proof",
            version="1",
            supported_lanes=frozenset({ProductionLane.LONG_FORM}),
            production_eligible=True,
            fixture_only=False,
            invokes_mr1=False,
            paid_provider_calls=True,
            automatic_publish=False,
        )

        def produce_media(self, _context):
            raise AssertionError("unexpected MEDIA")

        def produce_visual_assets(self, _context):
            raise AssertionError("unexpected VISUAL")

        def render_media(self, _context):
            calls.append("RENDER")
            raise AssertionError("native renderer became reachable")

        def run_quality_control(self, _context):
            raise AssertionError("unexpected QC")

        def archive_media(self, _context):
            raise AssertionError("unexpected ARCHIVE")

        def build_final_review_candidate(self, _context):
            raise AssertionError("unexpected FINALIZE")

    workflow_id = uuid.uuid4()
    governed = SimpleNamespace(
        provider_plan={
            "execution_authorized": True,
            "execution_mode": "REAL_LONG_FORM_PRODUCTION",
        },
        budget_plan={"budget_authorized": True},
    )
    monkeypatch.setattr(
        ai_visual_rerender_authority,
        "resolve_governed_ai_visual_rerender_execution_authority",
        lambda _session, *, workflow_run_id: (
            governed
            if workflow_run_id == workflow_id
            else pytest.fail("wrong governed workflow lookup")
        ),
    )
    context = SimpleNamespace(
        session=object(),
        run=SimpleNamespace(
            id=workflow_id,
            production_lane="LONG_FORM",
            production_package_artifact_version_id=uuid.uuid4(),
            production_package_hash="a" * 64,
            production_readiness_receipt_artifact_version_id=uuid.uuid4(),
            production_readiness_receipt_hash="b" * 64,
            ai_visual_production_run_id=None,
        ),
        event=SimpleNamespace(attempt_count=1),
        ensure_active=lambda: None,
        heartbeat=lambda: None,
    )
    handler = GatewayBackedPostReadinessStageHandler(
        key="LONG_FORM:RENDER",
        version="test",
        stage=ProductionWorkflowStage.RENDER,
        lane=ProductionLane.LONG_FORM,
        gateway=Gateway(),
    )

    with pytest.raises(WorkflowStageError) as caught:
        handler.execute(context)

    assert caught.value.error_code == "WORKFLOW_REAL_AI_VISUAL_AUTHORITY_REQUIRED"
    assert calls == []


def test_local_native_adapter_preserves_qualification_but_refuses_real_render() -> None:
    adapter = object.__new__(V2LocalNativeProductionAdapter)
    adapter._narration_runtime = object()
    context = SimpleNamespace(
        run=SimpleNamespace(
            production_lane="LONG_FORM",
            planning_source_type="LONG_FORM_PLAN",
            ai_visual_production_run_id=None,
        )
    )

    qualification = V2AuthorizedAdapterOperation(
        operation_id="qualification:render",
        stage=ProductionWorkflowStage.RENDER,
        adapter_key=V2_LOCAL_ADAPTER_KEY,
        paid_provider_call=False,
        max_cost_usd=Decimal("0"),
        parameters={
            "mode": "NATIVE_FFMPEG_LOCAL",
            "audio_strategy": V2_LOCAL_NARRATION_STRATEGY,
        },
        execution_mode="QUALIFICATION_LOCAL",
    )
    adapter._validate_operation(context, qualification)

    real = V2AuthorizedAdapterOperation(
        operation_id="real:render",
        stage=ProductionWorkflowStage.RENDER,
        adapter_key=V2_LOCAL_ADAPTER_KEY,
        paid_provider_call=False,
        max_cost_usd=Decimal("0"),
        parameters={
            "mode": "NATIVE_FFMPEG_LOCAL",
            "audio_strategy": V2_ELEVENLABS_NARRATION_STRATEGY,
        },
        execution_mode="REAL_LONG_FORM_PRODUCTION",
    )
    with pytest.raises(
        ValidationFailureError, match="V2_REAL_AI_VISUAL_AUTHORITY_REQUIRED"
    ):
        adapter._validate_operation(context, real)


def test_ai_visual_qc_artifact_set_resumes_after_injected_mid_write_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = {
        "technical_path": tmp_path / "ai-visual-technical-qc.json",
        "creative_path": tmp_path / "ai-visual-creative-qc.json",
        "render_qc_path": tmp_path / "ai-visual-render-qc.json",
        "cross_modal_path": tmp_path / "cross-modal-qc.json",
    }
    payloads = {
        "technical": {"kind": "technical", "sealed_at": "2026-08-14T00:00:00Z"},
        "creative": {"kind": "creative", "sealed_at": "2026-08-14T00:00:00Z"},
        "render_qc": {"kind": "render", "created_at": "2026-08-14T00:00:00Z"},
        "cross_modal": {
            "kind": "cross-modal",
            "sealed_at": "2026-08-14T00:00:00Z",
        },
    }
    original_write = v2_native_effects._write_json_atomic
    writes = 0

    class InjectedProcessCrash(RuntimeError):
        pass

    def crash_after_second_write(path: Path, payload: dict) -> None:
        nonlocal writes
        original_write(path, payload)
        writes += 1
        if writes == 2:
            raise InjectedProcessCrash("crash during QC artifact set")

    monkeypatch.setattr(
        v2_native_effects, "_write_json_atomic", crash_after_second_write
    )
    with pytest.raises(InjectedProcessCrash):
        _persist_ai_visual_qc_artifact_set(**paths, **payloads)
    assert paths["technical_path"].is_file()
    assert paths["creative_path"].is_file()
    assert not paths["render_qc_path"].exists()

    monkeypatch.setattr(v2_native_effects, "_write_json_atomic", original_write)
    _persist_ai_visual_qc_artifact_set(**paths, **payloads)
    for path_key, payload_key in (
        ("technical_path", "technical"),
        ("creative_path", "creative"),
        ("render_qc_path", "render_qc"),
        ("cross_modal_path", "cross_modal"),
    ):
        assert (
            json.loads(paths[path_key].read_text(encoding="utf-8"))
            == payloads[payload_key]
        )


def test_ai_visual_artifact_resolver_is_run_scoped_and_rejects_urls_and_symlinks(
    tmp_path: Path,
) -> None:
    visual_run_id = uuid.uuid4()
    run_dir = tmp_path / "ai-visual-runs" / str(visual_run_id)
    run_dir.mkdir(parents=True)
    manifest = run_dir / "asset-manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    adapter = object.__new__(V2LocalNativeProductionAdapter)
    adapter.root = tmp_path.resolve()
    relative = f"ai-visual-runs/{visual_run_id}/asset-manifest.json"

    assert (
        adapter._ai_visual_run_artifact(
            visual_run_id, relative, expected_name="asset-manifest.json"
        )
        == manifest.resolve()
    )
    with pytest.raises(ValidationFailureError):
        adapter._ai_visual_run_artifact(
            visual_run_id,
            "https://example.invalid/asset-manifest.json",
            expected_name="asset-manifest.json",
        )
    with pytest.raises(ValidationFailureError):
        adapter._ai_visual_run_artifact(
            visual_run_id,
            f"ai-visual-runs/{visual_run_id}/../asset-manifest.json",
            expected_name="asset-manifest.json",
        )

    manifest.unlink()
    target = run_dir / "sealed.json"
    target.write_text("{}", encoding="utf-8")
    manifest.symlink_to(target)
    with pytest.raises(ValidationFailureError):
        adapter._ai_visual_run_artifact(
            visual_run_id, relative, expected_name="asset-manifest.json"
        )


def test_ai_visual_db_run_manifest_binding_fails_closed_on_hash_drift() -> None:
    workflow_id = uuid.uuid4()
    project_id = uuid.uuid4()
    package_id = uuid.uuid4()
    visual_run_id = uuid.uuid4()
    manifest_id = uuid.uuid4()
    scene_plan_id = uuid.uuid4()
    hashes = {
        name: hashlib.sha256(name.encode()).hexdigest()
        for name in (
            "package",
            "timeline",
            "policy",
            "style",
            "scene",
            "manifest",
            "motion",
            "effect",
        )
    }
    refs = {
        name: f"ai-visual-runs/{visual_run_id}/{name}.json"
        for name in (
            "policy",
            "style",
            "scene",
            "manifest",
            "motion",
            "effect",
        )
    }
    run = SimpleNamespace(
        id=workflow_id,
        video_project_id=project_id,
        production_package_artifact_version_id=package_id,
        production_package_hash=hashes["package"],
        canonical_media_timeline_ref="v2-effect://source/timeline",
        canonical_media_timeline_hash=hashes["timeline"],
        ai_visual_production_run_id=visual_run_id,
        ai_visual_policy_ref=refs["policy"],
        ai_visual_policy_hash=hashes["policy"],
        ai_visual_style_bible_hash=hashes["style"],
        ai_visual_scene_plan_hash=hashes["scene"],
        ai_visual_asset_manifest_hash=hashes["manifest"],
        video_motion_grammar_ref=refs["motion"],
        video_motion_grammar_hash=hashes["motion"],
        ffmpeg_effect_plan_ref=refs["effect"],
        ffmpeg_effect_plan_hash=hashes["effect"],
    )
    visual_run = SimpleNamespace(
        id=visual_run_id,
        workflow_run_id=workflow_id,
        video_project_id=project_id,
        production_package_artifact_version_id=package_id,
        production_package_hash=hashes["package"],
        source_timeline_ref=run.canonical_media_timeline_ref,
        source_timeline_hash=hashes["timeline"],
        production_visual_policy_ref=refs["policy"],
        production_visual_policy_hash=hashes["policy"],
        style_bible_hash=hashes["style"],
        scene_plan_id=scene_plan_id,
        scene_plan_hash=hashes["scene"],
        asset_manifest_id=manifest_id,
        asset_manifest_hash=hashes["manifest"],
        motion_grammar_ref=refs["motion"],
        motion_grammar_hash=hashes["motion"],
        effect_plan_ref=refs["effect"],
        effect_plan_hash=hashes["effect"],
    )
    manifest = SimpleNamespace(
        id=manifest_id,
        visual_production_run_id=visual_run_id,
        scene_plan_snapshot_id=scene_plan_id,
        scene_plan_hash=hashes["scene"],
        style_bible_hash=hashes["style"],
        motion_grammar_hash=hashes["motion"],
        effect_plan_hash=hashes["effect"],
        content_hash=hashes["manifest"],
        production_eligible=True,
        renderer_primary_visual_generation=False,
    )

    _validate_ai_visual_run_bindings(
        run=run,
        project=SimpleNamespace(id=project_id),
        visual_run=visual_run,
        manifest_row=manifest,
    )
    manifest.content_hash = hashlib.sha256(b"drift").hexdigest()
    with pytest.raises(
        ValidationFailureError, match="AI_VISUAL_RUN_AUTHORITY_MISMATCH"
    ):
        _validate_ai_visual_run_bindings(
            run=run,
            project=SimpleNamespace(id=project_id),
            visual_run=visual_run,
            manifest_row=manifest,
        )


def test_ai_visual_qc_gate_set_has_motion_and_forbidden_source_gates_but_no_native_gate(
    tmp_path: Path,
) -> None:
    output = tmp_path / "assembled.mp4"
    output.write_bytes(b"ai-only-render")
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    scene_one = SimpleNamespace(
        scene_id="scene-1", presentation_start_ms=0, presentation_end_ms=1_000
    )
    scene_two = SimpleNamespace(
        scene_id="scene-2", presentation_start_ms=1_000, presentation_end_ms=2_000
    )
    assets = [
        SimpleNamespace(
            route="AI_IMAGE",
            origin="AI_GENERATED",
            verification_state="VERIFIED",
            provider_key="google_gemini_image",
        )
    ]
    required_ai_gates = {
        "MotionCoverageGate",
        "MotionBoundsGate",
        "MotionMeaningAlignmentGate",
        "MotionDiversityGate",
        "TransitionContinuityGate",
        "StaticDurationGate",
        "DeadVisualTimeGate",
        "AssemblerOnlyGate",
        "SRTSidecarOnlyGate",
        "TechnicalMediaGate",
        "RenderedMotionObservationGate",
    }
    authority = SimpleNamespace(
        run=SimpleNamespace(render_output_checksum=checksum),
        visual_run=SimpleNamespace(
            id=uuid.uuid4(),
            audio_duration_ms=2_000,
            audio_checksum="a" * 64,
            audio_ref="audio://immutable-authority",
        ),
        manifest=SimpleNamespace(content_hash="b" * 64),
        effect_plan=SimpleNamespace(
            effect_plan_hash="c" * 64, motion_plan_hash="d" * 64
        ),
    )
    assembly = SimpleNamespace(
        canonical_duration_ms=2_000,
        content_hash="e" * 64,
        asset_manifest_hash="b" * 64,
        effect_plan_hash="c" * 64,
        motion_plan_hash="d" * 64,
        effect_plan=SimpleNamespace(scene_effect_plans=[scene_one, scene_two]),
        asset_manifest=SimpleNamespace(scene_count=2, assets=assets),
    )
    receipt = SimpleNamespace(
        output_ref=str(output),
        output_checksum=checksum,
        assembly_plan_hash="e" * 64,
        narration_audio_checksum="a" * 64,
    )
    ai_qc = SimpleNamespace(
        gate_results=[
            SimpleNamespace(gate=name, verdict="PASS") for name in required_ai_gates
        ]
    )
    image_cross_modal_report = {
        "schema_version": "vcos.ai-visual-cross-modal-qc.v1",
        "asset_attestations": [{"fixture": True}],
        "scene_bindings": [{"fixture": True}],
        "content_hash": "9" * 64,
        "evidence_scope": "LINEAGE_AND_SCENE_BINDING",
        "automated_disposition_scope": (
            "LINEAGE_SCENE_TIMING_TECHNICAL_AND_MOTION_ONLY"
        ),
        "deterministic_disposition": "PASS",
        "image_asset_count": 1,
        "video_asset_count": 0,
        "image_same_interaction_semantic_attestation_count": 1,
        "video_technical_motion_inspection_count": 0,
        "actual_asset_description_source": "SAME_INTERACTION_MODEL_OUTPUT",
        "image_same_interaction_semantic_attestations_verified": True,
        "same_interaction_asset_semantic_attestations_verified": True,
        "actual_asset_semantic_inspection_performed": True,
        "same_interaction_model_output_semantic_inspection_performed": True,
        "video_actual_asset_semantic_inspection_performed": False,
        "video_provider_semantic_match_asserted": False,
        "video_technical_motion_evidence_verified": True,
        "independent_multimodal_inspection_performed": False,
        "actual_asset_semantic_disposition": (
            "PASS_SAME_INTERACTION_ATTESTED_PENDING_HUMAN_REVIEW"
        ),
        "automated_semantic_conformity_asserted": False,
        "human_semantic_review_required": True,
        "human_final_review_required": True,
        "automated_pass_is_not_independent_semantic_conformity": True,
    }
    checks = _ai_visual_creative_gate_checks(
        authority=authority,
        assembly=assembly,
        receipt=receipt,
        ai_qc=ai_qc,
        render_journal={
            "ai_visual_production_run_id": str(authority.visual_run.id),
            "native_primary_visuals_present": False,
            "stock_primary_visuals_present": False,
            "screenshot_primary_visuals_present": False,
            "audio_asset_ref": authority.visual_run.audio_ref,
            "audio_checksum": authority.visual_run.audio_checksum,
        },
        cross_modal_report=image_cross_modal_report,
    )

    assert all(checks.values())
    assert "ForbiddenPrimaryVisualSourceGate" in checks
    assert "RenderedMotionObservationGate" in checks
    assert "NativeExplanatoryVisualGate" not in checks

    mixed_cross_modal_report = {
        **image_cross_modal_report,
        "asset_attestations": [{"route": "AI_IMAGE"}, {"route": "AI_VIDEO"}],
        "scene_bindings": [{"scene": 1}, {"scene": 2}],
        "image_asset_count": 1,
        "video_asset_count": 1,
        "video_technical_motion_inspection_count": 1,
        "actual_asset_description_source": (
            "MIXED_IMAGE_ATTESTATION_AND_VIDEO_TECHNICAL_EVIDENCE"
        ),
        "same_interaction_asset_semantic_attestations_verified": False,
        "actual_asset_semantic_inspection_performed": False,
        "same_interaction_model_output_semantic_inspection_performed": False,
        "actual_asset_semantic_disposition": (
            "MIXED_IMAGE_ATTESTED_VIDEO_PENDING_HUMAN_SEMANTIC_REVIEW"
        ),
    }
    mixed_checks = _ai_visual_creative_gate_checks(
        authority=authority,
        assembly=assembly,
        receipt=receipt,
        ai_qc=ai_qc,
        render_journal={
            "ai_visual_production_run_id": str(authority.visual_run.id),
            "native_primary_visuals_present": False,
            "stock_primary_visuals_present": False,
            "screenshot_primary_visuals_present": False,
            "audio_asset_ref": authority.visual_run.audio_ref,
            "audio_checksum": authority.visual_run.audio_checksum,
        },
        cross_modal_report=mixed_cross_modal_report,
    )
    assert mixed_checks["CrossModalLineageGate"] is True

    false_video_semantic_claim = {
        **mixed_cross_modal_report,
        "video_provider_semantic_match_asserted": True,
    }
    tampered_checks = _ai_visual_creative_gate_checks(
        authority=authority,
        assembly=assembly,
        receipt=receipt,
        ai_qc=ai_qc,
        render_journal={
            "ai_visual_production_run_id": str(authority.visual_run.id),
            "native_primary_visuals_present": False,
            "stock_primary_visuals_present": False,
            "screenshot_primary_visuals_present": False,
            "audio_asset_ref": authority.visual_run.audio_ref,
            "audio_checksum": authority.visual_run.audio_checksum,
        },
        cross_modal_report=false_video_semantic_claim,
    )
    assert tampered_checks["CrossModalLineageGate"] is False

    missing_cross_modal = _ai_visual_creative_gate_checks(
        authority=authority,
        assembly=assembly,
        receipt=receipt,
        ai_qc=ai_qc,
        render_journal={
            "ai_visual_production_run_id": str(authority.visual_run.id),
            "native_primary_visuals_present": False,
            "stock_primary_visuals_present": False,
            "screenshot_primary_visuals_present": False,
            "audio_asset_ref": authority.visual_run.audio_ref,
            "audio_checksum": authority.visual_run.audio_checksum,
        },
        cross_modal_report=None,
    )
    assert missing_cross_modal["CrossModalLineageGate"] is False


def test_ai_visual_stage_settlement_uses_trigger_legal_intermediate_states() -> None:
    workflow_id = uuid.uuid4()
    project_id = uuid.uuid4()
    visual_run_id = uuid.uuid4()
    manifest_hash = "a" * 64
    effect_hash = "b" * 64
    output_hash = "c" * 64
    assembly_hash = "d" * 64
    workflow = SimpleNamespace(
        id=workflow_id,
        video_project_id=project_id,
        ai_visual_production_run_id=visual_run_id,
    )
    visual_run = SimpleNamespace(
        id=visual_run_id,
        workflow_run_id=workflow_id,
        video_project_id=project_id,
        state="ASSETS_VERIFIED",
        current_phase="MANIFEST",
        projection_version=7,
        asset_manifest_hash=manifest_hash,
        effect_plan_hash=effect_hash,
        render_output_ref=None,
        render_output_checksum=None,
    )

    class FakeSession:
        def __init__(self) -> None:
            self.flushes: list[tuple[str, str, int]] = []

        def get(self, model, identifier):
            assert identifier == workflow_id
            return workflow

        def scalar(self, statement):
            return visual_run

        def flush(self) -> None:
            self.flushes.append(
                (
                    visual_run.state,
                    visual_run.current_phase,
                    visual_run.projection_version,
                )
            )

    session = FakeSession()
    render_ref = f"v2-ai-visual-render://fixture/{output_hash}"
    assembly_ref = "v2-effect://fixture/ai-visual-assembly-plan"
    render_result = WorkflowStageResult(
        result_type="V2_AI_VISUAL_RENDER_OUTPUT",
        result_ref=render_ref,
        result_hash=output_hash,
        authority_refs=WorkflowAuthorityRefs(
            video_project_id=project_id,
            native_render_plan_ref=assembly_ref,
            native_render_plan_hash=assembly_hash,
            render_output_ref=render_ref,
            render_output_checksum=output_hash,
        ),
    )
    V2LocalNativeProductionAdapter._settle_ai_visual_projection(
        session=session,
        ledger=SimpleNamespace(workflow_run_id=workflow_id, stage="RENDER"),
        result=render_result,
        journal={
            "ai_visual_production_run_id": str(visual_run_id),
            "render_output_ref": render_ref,
            "output_checksum": output_hash,
            "assembly_plan_ref": assembly_ref,
            "assembly_plan_hash": assembly_hash,
            "asset_manifest_hash": manifest_hash,
            "effect_plan_hash": effect_hash,
        },
    )
    assert session.flushes == [("RENDERING", "RENDER", 8)]
    assert (
        visual_run.state,
        visual_run.current_phase,
        visual_run.projection_version,
    ) == (
        "RENDERED",
        "RENDER",
        9,
    )

    technical_hash = "e" * 64
    creative_hash = "f" * 64
    cross_modal_hash = "1" * 64
    technical_ref = "v2-effect://fixture/ai-visual-technical-qc"
    creative_ref = "v2-effect://fixture/ai-visual-creative-qc"
    cross_modal_ref = "v2-effect://fixture/cross-modal-qc"
    qc_result = WorkflowStageResult(
        result_type="V2_AUTOMATED_AI_VISUAL_QC",
        result_ref=creative_ref,
        result_hash=creative_hash,
        authority_refs=WorkflowAuthorityRefs(
            video_project_id=project_id,
            technical_qc_receipt_ref=technical_ref,
            technical_qc_receipt_hash=technical_hash,
            creative_qc_receipt_ref=creative_ref,
            creative_qc_receipt_hash=creative_hash,
            cross_modal_qc_receipt_ref=cross_modal_ref,
            cross_modal_qc_receipt_hash=cross_modal_hash,
        ),
    )
    V2LocalNativeProductionAdapter._settle_ai_visual_projection(
        session=session,
        ledger=SimpleNamespace(workflow_run_id=workflow_id, stage="QC"),
        result=qc_result,
        journal={
            "ai_visual_production_run_id": str(visual_run_id),
            "technical_qc_ref": technical_ref,
            "technical_qc_hash": technical_hash,
            "creative_qc_ref": creative_ref,
            "creative_qc_hash": creative_hash,
            "cross_modal_qc_ref": cross_modal_ref,
            "cross_modal_qc_hash": cross_modal_hash,
            "asset_manifest_hash": manifest_hash,
            "effect_plan_hash": effect_hash,
        },
    )
    assert session.flushes[-1] == ("QC_RUNNING", "QC", 10)
    assert (
        visual_run.state,
        visual_run.current_phase,
        visual_run.projection_version,
    ) == (
        "QC_VERIFIED",
        "QC",
        11,
    )


def test_governed_archive_threads_exact_source_sidecar_without_replacement_media(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.services import ai_visual_rerender_authority, v2_drive_archive

    replacement_id = uuid.uuid4()
    source_id = uuid.uuid4()
    project_id = uuid.uuid4()
    visual_run_id = uuid.uuid4()
    output = tmp_path / "ai-visual-render.mp4"
    output.write_bytes(b"governed-ai-visual-render")
    output_checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    caption = tmp_path / "canonical-captions.srt"
    caption.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nVerified.\n", encoding="utf-8"
    )
    caption_checksum = hashlib.sha256(caption.read_bytes()).hexdigest()
    sidecar = {
        "caption_relative_path": caption.name,
        "caption_checksum": caption_checksum,
        "caption_ref": f"artifact-version://{uuid.uuid4()}",
        "caption_artifact_hash": "a" * 64,
        "subtitle_qc_ref": f"artifact-version://{uuid.uuid4()}",
        "subtitle_qc_hash": "b" * 64,
    }
    run = SimpleNamespace(
        id=replacement_id,
        ai_visual_production_run_id=visual_run_id,
        company_id=uuid.uuid4(),
        channel_workspace_id=uuid.uuid4(),
        video_project_id=project_id,
        render_output_ref="v2-ai-visual-render://verified",
        render_output_checksum=output_checksum,
        production_package_artifact_version_id=uuid.uuid4(),
        production_package_hash="c" * 64,
    )
    render_ledger = SimpleNamespace(
        effect_journal={
            "output_relative_path": output.name,
            "measured_render_duration_ms": 1_000,
        }
    )
    source_media_ledger = SimpleNamespace(
        effect_journal={**sidecar, "subtitle_qc_state": "PASS"}
    )

    class FakeReadSession:
        def __init__(self) -> None:
            self.scalar_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, identity):
            if identity != visual_run_id:
                pytest.fail("unexpected governed visual-run lookup")
            return SimpleNamespace(
                id=visual_run_id,
                workflow_run_id=replacement_id,
                video_project_id=project_id,
                execution_kind="GOVERNED_RERENDER",
                rerender_authority_id=uuid.uuid4(),
            )

        def scalar(self, statement):
            self.scalar_calls += 1
            params = set(statement.compile().params.values())
            if self.scalar_calls == 1:
                assert replacement_id in params
                return render_ledger
            assert self.scalar_calls == 2
            assert source_id in params
            assert replacement_id not in params
            return source_media_ledger

    read_session = FakeReadSession()
    monkeypatch.setattr(
        v2_drive_archive,
        "_production_inputs",
        lambda _session, workflow_id: (
            (
                run,
                SimpleNamespace(id=project_id),
                object(),
                object(),
                object(),
            )
            if workflow_id == replacement_id
            else pytest.fail("unexpected workflow")
        ),
    )
    monkeypatch.setattr(
        ai_visual_rerender_authority,
        "resolve_governed_ai_visual_rerender_execution_authority",
        lambda _session, *, workflow_run_id, required: (
            SimpleNamespace(source_workflow=SimpleNamespace(id=source_id))
            if workflow_run_id == replacement_id and required is True
            else pytest.fail("governed authority was not resolved exactly")
        ),
    )
    captured: dict[str, object] = {}
    final_media_id = uuid.uuid4()
    artifact = SimpleNamespace(
        final_media=SimpleNamespace(id=final_media_id, checksum_sha256=output_checksum),
        cloud_media=SimpleNamespace(id=uuid.uuid4(), drive_file_id="drive-media"),
        caption_cloud_media=SimpleNamespace(
            id=uuid.uuid4(), drive_file_id="drive-caption"
        ),
        caption_archive_object_ref="drive://drive-caption/canonical-captions.srt",
        archive_receipt_hash="d" * 64,
        archive_object_ref="drive://drive-media/final.mp4",
    )

    def resolve_archive(**kwargs):
        captured.update(kwargs)
        return artifact

    monkeypatch.setattr(
        v2_drive_archive, "_resolve_or_create_v2_drive_archive", resolve_archive
    )
    destination_id = uuid.uuid4()
    monkeypatch.setattr(
        v2_drive_archive,
        "_normalized_destination_for_drive",
        lambda _context: {
            "id": destination_id,
            "content_hash": "e" * 64,
            "binding": {"automatic_publish": False},
        },
    )

    adapter = object.__new__(V2GoogleDriveRemoteArchiveAdapter)
    adapter.root = tmp_path.resolve()
    adapter._session_factory = lambda: read_session
    adapter._readiness_gate = SimpleNamespace(require_ready=lambda **_kwargs: None)
    context = SimpleNamespace(session=object(), run=run, command_id=str(uuid.uuid4()))
    operation = SimpleNamespace(
        parameters={"provider_execution": {"idempotency_key": "governed-archive"}}
    )

    result, _journal = adapter._archive(
        ledger_id=uuid.uuid4(), context=context, operation=operation
    )

    assert read_session.scalar_calls == 2
    assert captured["caption_sidecar_authority"] == {
        **sidecar,
        "subtitle_qc_state": "PASS",
    }
    assert result.authority_refs.final_media_ref_id == final_media_id


def test_interrupted_governed_archive_requires_exact_durable_zero_submit_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.services import v2_drive_archive

    workflow_id = uuid.uuid4()
    source_workflow_id = uuid.uuid4()
    project_id = uuid.uuid4()
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    package_id = uuid.uuid4()
    command_id = f"archive:{uuid.uuid4()}"
    operation_id = f"v2-ai-rerender:{workflow_id}:archive"
    input_hash = "1" * 64
    render = tmp_path / "renders" / "replacement.mp4"
    render.parent.mkdir()
    render.write_bytes(b"durable-render")
    caption = tmp_path / "captions" / "source.srt"
    caption.parent.mkdir()
    caption.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nVerified.\n", encoding="utf-8"
    )
    render_hash = hashlib.sha256(render.read_bytes()).hexdigest()
    caption_hash = hashlib.sha256(caption.read_bytes()).hexdigest()
    sidecar = {
        "caption_relative_path": caption.relative_to(tmp_path).as_posix(),
        "caption_checksum": caption_hash,
        "caption_ref": f"artifact-version://{uuid.uuid4()}",
        "caption_artifact_hash": "2" * 64,
        "subtitle_qc_ref": f"artifact-version://{uuid.uuid4()}",
        "subtitle_qc_hash": "3" * 64,
    }
    run = SimpleNamespace(
        id=workflow_id,
        video_project_id=project_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        production_package_artifact_version_id=package_id,
        production_package_hash="4" * 64,
        production_lane="LONG_FORM",
        render_output_ref="v2-ai-visual-render://replacement",
        render_output_checksum=render_hash,
        canonical_media_timeline_hash="5" * 64,
        native_render_plan_hash="6" * 64,
        technical_qc_receipt_hash="7" * 64,
        creative_qc_receipt_hash="8" * 64,
    )
    idempotency_key = f"{operation_id}:google-drive-archive"
    operation = {
        "stage": "ARCHIVE",
        "adapter_key": "v2-google-drive-remote",
        "operation_id": operation_id,
        "paid_provider_call": False,
        "max_cost_usd": "0",
        "parameters": {
            "mode": "GOOGLE_DRIVE_REMOTE_ARCHIVE",
            "provider_execution": {
                "provider": "google_drive",
                "attempt_limit": 1,
                "idempotency_key": idempotency_key,
                "remote_object_required": True,
                "checksum_readback_required": True,
            },
        },
    }
    ledger = SimpleNamespace(
        workflow_run_id=workflow_id,
        video_project_id=project_id,
        production_package_artifact_version_id=package_id,
        production_package_hash=run.production_package_hash,
        command_id=command_id,
        stage="ARCHIVE",
        operation_id=operation_id,
        adapter_key="v2-google-drive-remote",
        input_hash=input_hash,
        state="EFFECT_STARTED",
        effect_invocation_count=1,
        result_type=None,
        result_id=None,
        result_ref=None,
        result_hash=None,
        result_payload={},
        authority_refs={},
        effect_journal={
            "schema_version": "vcos.production-effect-journal.v1",
            "command_id": command_id,
            "stage": "ARCHIVE",
            "state": "EFFECT_STARTED",
        },
        started_at=object(),
        completed_at=None,
    )
    render_ledger = SimpleNamespace(
        effect_journal={
            "output_relative_path": render.relative_to(tmp_path).as_posix(),
            "measured_render_duration_ms": 1_000,
        }
    )
    media_ledger = SimpleNamespace(
        effect_journal={**sidecar, "subtitle_qc_state": "PASS"}
    )
    request = _remote_archive_request_identity(
        command_id=command_id,
        operation_id=operation_id,
        idempotency_key=idempotency_key,
        source_relative_path=render.relative_to(tmp_path).as_posix(),
        source_checksum=render_hash,
        source_size_bytes=render.stat().st_size,
        measured_render_duration_ms=1_000,
        caption_relative_path=caption.relative_to(tmp_path).as_posix(),
        sidecar=sidecar,
    )
    request_path = (
        tmp_path
        / "effects"
        / hashlib.sha256(command_id.encode()).hexdigest()
        / "google-drive-archive-request-journal.json"
    )
    request_path.parent.mkdir(parents=True)
    request_path.write_text(
        json.dumps({**request, "state": "SUBMITTED"}), encoding="utf-8"
    )
    project = SimpleNamespace(
        id=project_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        schema_version="v2",
        production_lane="LONG_FORM",
        planning_source_type="LONG_FORM_PLAN",
    )
    duration_contract = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "minimum_duration_ms": 900,
            "maximum_duration_ms": 1_100,
        }
    )
    package = SimpleNamespace(
        video_project_id=project_id,
        production_lane=SimpleNamespace(value="LONG_FORM"),
        duration_contract=duration_contract,
    )
    cloud_id = uuid.uuid4()
    caption_cloud_id = uuid.uuid4()
    cloud = SimpleNamespace(
        id=cloud_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        storage_provider="GOOGLE_DRIVE",
        media_type="LONG_FORM_FINAL",
        checksum_sha256=render_hash,
        upload_status="VERIFIED",
        verification_status="CHECKSUM_VERIFIED",
        drive_file_id="durable-render-file",
        web_view_link="https://drive.google.com/file/d/durable-render-file/view",
        size_bytes=render.stat().st_size,
        mime_type="video/mp4",
        file_name="replacement.mp4",
        source_refs=[
            {
                "type": "v2_render_output",
                "workflow_run_id": str(workflow_id),
                "render_output_ref": run.render_output_ref,
                "render_output_checksum": render_hash,
                "production_package_artifact_version_id": str(package_id),
                "production_package_hash": run.production_package_hash,
            }
        ],
        technical_appendix={
            "drive_file_id_verified": True,
            "size_verified": True,
            "checksum_verified": True,
            "measured_render_duration_ms": 1_000,
            "v2_archive_command_id": command_id,
            "v2_archive_idempotency_key": idempotency_key,
        },
    )
    caption_cloud = SimpleNamespace(
        id=caption_cloud_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=project_id,
        storage_provider="GOOGLE_DRIVE",
        media_type="CAPTION",
        checksum_sha256=caption_hash,
        upload_status="VERIFIED",
        verification_status="CHECKSUM_VERIFIED",
        drive_file_id="durable-caption-file",
        web_view_link="https://drive.google.com/file/d/durable-caption-file/view",
        size_bytes=caption.stat().st_size,
        mime_type="application/x-subrip",
        file_name="source.srt",
        source_refs=[
            {
                "type": "v2_caption_sidecar",
                "workflow_run_id": str(workflow_id),
                "caption_ref": sidecar["caption_ref"],
                "caption_checksum": caption_hash,
                "caption_artifact_hash": sidecar["caption_artifact_hash"],
                "subtitle_qc_ref": sidecar["subtitle_qc_ref"],
                "subtitle_qc_hash": sidecar["subtitle_qc_hash"],
            }
        ],
        technical_appendix={
            "drive_file_id_verified": True,
            "size_verified": True,
            "checksum_verified": True,
            "v2_caption_sidecar": True,
            "caption_ref": sidecar["caption_ref"],
            "caption_artifact_hash": sidecar["caption_artifact_hash"],
            "subtitle_qc_ref": sidecar["subtitle_qc_ref"],
            "subtitle_qc_hash": sidecar["subtitle_qc_hash"],
            "v2_archive_command_id": command_id,
            "v2_archive_idempotency_key": f"{idempotency_key}.caption",
        },
    )
    receipt = _drive_archive_receipt_content(
        run=run,
        project=project,
        command_id=command_id,
        operation_id=operation_id,
        cloud=cloud,
        caption_cloud=caption_cloud,
        sidecar=sidecar,
        measured_duration_ms=1_000,
        external_effect_performed=True,
    )
    receipt_hash = v2_drive_archive.content_hash(receipt)
    expected_lineage = _drive_archive_lineage_content(
        run=run,
        project=project,
        package=package,
        command_id=command_id,
        operation_id=operation_id,
        cloud=cloud,
        caption_cloud=caption_cloud,
        sidecar=sidecar,
        measured_duration_ms=1_000,
        archive_receipt_hash=receipt_hash,
        external_effect_performed=True,
    )
    lineage = SimpleNamespace(
        content=expected_lineage,
        content_hash=v2_drive_archive.content_hash(expected_lineage),
        packaging_metadata={
            "producer": "v2-google-drive-remote",
            "archive_command_id": command_id,
            "external_effect_performed": True,
            "_vcos_domain_authority": {"writer": "server_domain_service"},
        },
    )
    final_media = SimpleNamespace(
        id=uuid.uuid4(),
        duration_contract=duration_contract.model_dump(),
        checksum_sha256=render_hash,
    )
    artifact = SimpleNamespace(
        final_media=final_media,
        cloud_media=cloud,
        caption_cloud_media=caption_cloud,
        lineage=lineage,
        archive_receipt_hash=receipt_hash,
        archive_object_ref="drive://durable-render-file/final.mp4",
        caption_archive_object_ref=("drive://durable-caption-file/final-captions.srt"),
    )

    class FakeSession:
        def __init__(self) -> None:
            self.scalar_values = [render_ledger, media_ledger]
            self.scalars_values = [[cloud], [caption_cloud], [final_media]]

        def scalar(self, _statement):
            return self.scalar_values.pop(0)

        def scalars(self, _statement):
            return self.scalars_values.pop(0)

        def get(self, _model, identity):
            assert identity == project_id
            return project

    monkeypatch.setattr(
        v2_drive_archive,
        "ProductionPackageService",
        lambda _session: SimpleNamespace(
            validate_for_readiness=lambda identity: (
                package
                if identity == package_id
                else pytest.fail("wrong package authority")
            )
        ),
    )

    def require_exact(_session, **kwargs):
        assert kwargs == {
            "project_id": project_id,
            "final_media_id": final_media.id,
            "expected_checksum": render_hash,
            "expected_archive_hash": receipt_hash,
        }
        return artifact

    monkeypatch.setattr(
        v2_drive_archive, "require_v2_google_drive_final_media", require_exact
    )

    assert has_exact_governed_drive_archive_reconciliation_authority(
        FakeSession(),
        run=run,
        source_workflow_run_id=source_workflow_id,
        ledger=ledger,
        input_hash=input_hash,
        operation=operation,
        workspace_root=tmp_path,
    )
    ledger.state = "FAILED_UNCERTAIN"
    ledger.effect_journal = {
        **ledger.effect_journal,
        "state": "FAILED_UNCERTAIN",
        "last_error_type": "ProcessCrash",
    }
    assert has_exact_governed_drive_archive_reconciliation_authority(
        FakeSession(),
        run=run,
        source_workflow_run_id=source_workflow_id,
        ledger=ledger,
        input_hash=input_hash,
        operation=operation,
        workspace_root=tmp_path,
    )

    request_path.write_text(
        json.dumps({**request, "state": "SUBMITTED", "command_id": "drift"}),
        encoding="utf-8",
    )
    assert not has_exact_governed_drive_archive_reconciliation_authority(
        FakeSession(),
        run=run,
        source_workflow_run_id=source_workflow_id,
        ledger=ledger,
        input_hash=input_hash,
        operation=operation,
        workspace_root=tmp_path,
    )
    request_path.write_text(
        json.dumps({**request, "state": "SUBMITTED"}), encoding="utf-8"
    )

    uploads = 0

    def forbidden_upload_service(_session):
        nonlocal uploads
        uploads += 1
        raise AssertionError("reconciliation attempted a Drive submission")

    monkeypatch.setattr(
        v2_drive_archive,
        "_resolve_or_create_v2_drive_archive",
        lambda **_kwargs: artifact,
    )
    adapter = object.__new__(V2GoogleDriveRemoteArchiveAdapter)
    adapter.root = tmp_path.resolve()
    adapter._upload_service_factory = forbidden_upload_service
    resolved = adapter._resolve_existing_or_upload(
        context=SimpleNamespace(session=object(), run=run, command_id=command_id),
        operation=SimpleNamespace(
            operation_id=operation_id,
            parameters=operation["parameters"],
        ),
        source=render,
        checksum=render_hash,
        measured_duration_ms=1_000,
        caption_source=caption,
        sidecar=sidecar,
    )
    assert resolved is artifact
    assert uploads == 0
