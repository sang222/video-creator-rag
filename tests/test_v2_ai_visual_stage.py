from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.contracts.ai_visual_cross_modal import (
    VeoTechnicalMotionInspectionEvidence,
)
from app.contracts.production_publish import FinalReviewCandidateRead
from app.core.errors import ValidationFailureError
from app.services.production_workflow import (
    WorkflowStageError,
    _settle_ai_visual_final_review_projection,
)
from app.services.v2_ai_visual_stage import (
    V2_AI_VISUAL_FIRST_VIDEO_MAX_IMAGES,
    V2_AI_VISUAL_FIRST_VIDEO_MAX_VIDEOS,
    V2_AI_VISUAL_FIRST_VIDEO_MAX_COST_USD,
    V2_AI_VISUAL_FIRST_VIDEO_VIDEO_UNIT_COST_USD,
    V2_AI_VISUAL_DHASH_NEAR_DUPLICATE_MAX_DISTANCE,
    V2_AI_VISUAL_MOTION_CLASSIFIER_VERSION,
    V2_AI_VISUAL_PRODUCTION_ADAPTER_KEY,
    V2AIVisualProductionAdapter,
    _apply_exact_planning_projection,
    _mixed_conservative_cost_within_authority,
    compile_ai_visual_stage_planning,
)
from app.services.v2_ai_visual_renderer import build_ai_visual_asset_manifest


_FIRST_VIDEO_TIMINGS = (
    (300, 16_160),
    (16_260, 35_220),
    (36_060, 48_680),
    (49_640, 73_420),
    (74_160, 86_720),
    (87_260, 107_560),
    (108_290, 121_340),
    (121_980, 140_140),
    (140_951, 159_520),
    (160_300, 172_960),
    (173_340, 188_530),
    (188_600, 205_060),
    (205_071, 215_840),
    (215_900, 226_300),
    (227_000, 249_400),
    (250_280, 259_399),
    (259_440, 273_610),
    (273_680, 284_500),
    (285_540, 299_080),
    (299_740, 314_820),
    (315_700, 332_630),
    (333_360, 346_580),
    (347_260, 359_920),
    (360_000, 380_890),
    (381_691, 390_940),
    (391_540, 412_240),
    (412_380, 420_320),
    (421_100, 437_960),
    (439_260, 452_660),
    (453_600, 478_540),
    (479_100, 501_419),
    (501_540, 515_080),
    (515_780, 542_980),
)
_FIRST_VIDEO_INFORMATION_IDS = (
    "iu-001",
    "iu-002",
    *("iu-003" for _ in range(6)),
    "iu-004",
    "iu-005",
    *("iu-006" for _ in range(8)),
    "iu-007",
    "iu-008",
    *("iu-009" for _ in range(7)),
    "iu-010",
    "iu-011",
    *("iu-012" for _ in range(4)),
)
_FIRST_VIDEO_FUNCTIONS = (
    *("AUTHENTIC_EVIDENCE_CONTEXT" for _ in range(2)),
    *("PROCESS_OR_DECISION_MODEL" for _ in range(6)),
    *("EXPLANATORY_CONTEXT" for _ in range(10)),
    "PROCESS_OR_DECISION_MODEL",
    *("BOUNDARY_COMPARISON" for _ in range(10)),
    *("EXPLANATORY_CONTEXT" for _ in range(4)),
)


def _first_video_timeline() -> dict[str, object]:
    units = []
    bindings = []
    for index, ((start_ms, end_ms), information_id, function) in enumerate(
        zip(
            _FIRST_VIDEO_TIMINGS,
            _FIRST_VIDEO_INFORMATION_IDS,
            _FIRST_VIDEO_FUNCTIONS,
            strict=True,
        ),
        start=1,
    ):
        unit_id = f"nu-{index:03d}"
        units.append(
            {
                "narration_unit_id": unit_id,
                "information_unit_ids": [information_id],
                "visual_function": function,
                "semantic_intent": (
                    "Which documented automation-audit step should retain human review "
                    "before a small team commits an external change?"
                    if 3 <= index <= 8
                    else f"Semantic intent {index}"
                ),
                "text": (
                    f"Bounded explanatory detail for decision-flow window {index}."
                    if 3 <= index <= 8
                    else f"Frozen narration unit {index}."
                ),
                "importance": "CORE",
                "factual_risk": "MEDIUM",
            }
        )
        bindings.append(
            {
                "narration_unit_id": unit_id,
                "actual_start_ms": start_ms,
                "actual_end_ms": end_ms,
            }
        )
    return {
        "duration_ms": 543_295,
        "narration_unit_compilation": {"narration_units": units},
        "timed_narration_unit_bindings": {"bindings": bindings},
    }


def _planning_binding_values() -> dict[str, object]:
    return {
        "style_bible_id": uuid.uuid4(),
        "style_bible_hash": "a" * 64,
        "scene_plan_id": uuid.uuid4(),
        "scene_plan_hash": "b" * 64,
        "motion_grammar_ref": "ai-visual-runs/run/video-motion-grammar.json",
        "motion_grammar_hash": "c" * 64,
    }


def test_descriptor_is_paid_ai_visual_only_production_stage() -> None:
    descriptor = V2AIVisualProductionAdapter.descriptor

    assert descriptor.adapter_key == V2_AI_VISUAL_PRODUCTION_ADAPTER_KEY
    assert {stage.value for stage in descriptor.supported_stages} == {"VISUAL"}
    assert descriptor.production_eligible is True
    assert descriptor.fixture_only is False
    assert descriptor.paid_provider_calls is True
    assert descriptor.automatic_publish is False


def test_final_review_read_contract_exposes_exact_ai_visual_lineage() -> None:
    assert {
        "ai_visual_production_run_id",
        "ai_visual_asset_manifest_hash",
        "ffmpeg_effect_plan_hash",
        "supersedes_final_review_candidate_id",
    }.issubset(FinalReviewCandidateRead.model_fields)


def test_verified_asset_duplication_gate_rejects_same_actual_visual() -> None:
    receipt = SimpleNamespace(
        checksum_sha256="a" * 64,
        technical_qc=SimpleNamespace(perceptual_hash="0123456789abcdef"),
    )
    records = [
        SimpleNamespace(asset_receipt=receipt),
        SimpleNamespace(asset_receipt=receipt),
    ]

    with pytest.raises(
        ValidationFailureError,
        match="V2_AI_VISUAL_ASSET_DUPLICATION_GATE_FAILED",
    ):
        V2AIVisualProductionAdapter._verified_assets(records)


def _verified_image_record(*, ordinal: int, checksum: str, dhash: str):
    scene_id = f"scene-{ordinal}"
    return SimpleNamespace(
        identity=SimpleNamespace(
            primary_asset_slot_id=f"slot-{ordinal}",
            primary_asset_owner_scene_id=scene_id,
            bound_scene_ids=(scene_id,),
            bound_scene_plan_hashes=(f"{ordinal:x}" * 64,),
            model_id="gemini-3.1-flash-image",
            effect_id=f"effect-{ordinal}",
            effect_identity_hash=(f"{ordinal + 2:x}" * 64),
        ),
        asset_receipt=SimpleNamespace(
            checksum_sha256=checksum,
            technical_qc=SimpleNamespace(perceptual_hash=dhash),
            local_ref=f"ai-visual-runs/run/effects/{ordinal}/verified-primary.jpg",
            size_bytes=100 + ordinal,
            content_type="image/jpeg",
            width=1920,
            height=1080,
            qc_ref=f"ai-visual-runs/run/effects/{ordinal}/qc.json",
            qc_hash=(f"{ordinal + 4:x}" * 64),
            receipt_hash=(f"{ordinal + 6:x}" * 64),
        ),
    )


def test_verified_asset_duplication_gate_rejects_near_dhash_owner_assets() -> None:
    assert V2_AI_VISUAL_DHASH_NEAR_DUPLICATE_MAX_DISTANCE == 6
    records = [
        _verified_image_record(
            ordinal=1,
            checksum="a" * 64,
            dhash="0000000000000000",
        ),
        _verified_image_record(
            ordinal=2,
            checksum="b" * 64,
            dhash="000000000000003f",
        ),
    ]

    with pytest.raises(
        ValidationFailureError,
        match="V2_AI_VISUAL_ASSET_DUPLICATION_GATE_FAILED",
    ):
        V2AIVisualProductionAdapter._verified_assets(records)


def test_verified_asset_duplication_gate_accepts_distinct_owner_assets() -> None:
    records = [
        _verified_image_record(
            ordinal=1,
            checksum="a" * 64,
            dhash="0000000000000000",
        ),
        _verified_image_record(
            ordinal=2,
            checksum="b" * 64,
            dhash="ffffffffffffffff",
        ),
    ]

    assert len(V2AIVisualProductionAdapter._verified_assets(records)) == 2


def test_first_video_live_shaped_plan_projects_truthful_duration_bounded_assets() -> (
    None
):
    visual_run = SimpleNamespace(
        id=uuid.uuid4(),
        video_project_id=uuid.uuid4(),
        production_package_artifact_version_id=uuid.uuid4(),
        source_timeline_hash="d" * 64,
    )

    artifacts = compile_ai_visual_stage_planning(
        visual_run=visual_run,
        timeline=_first_video_timeline(),
        provider_readiness_ref="ai-visual-readiness/fake/google-gemini-image",
        budget_authority_ref="mr1-budget://fake",
        maximum_image_submissions=64,
        maximum_video_submissions=1,
    )
    scenes = artifacts.scene_plan.scenes
    owners = [
        scene for scene in scenes if scene.reuses_primary_asset_from_scene_id is None
    ]

    assert len(scenes) == 46
    assert len(owners) == 14
    assert artifacts.scene_plan.unique_ai_image_asset_slot_count == 14
    assert artifacts.scene_plan.unique_ai_video_asset_slot_count == 0
    assert artifacts.scene_plan.reused_presentation_window_count == 32
    assert artifacts.scene_plan.ai_video_scene_count == 0
    classification = artifacts.motion_classification
    assert classification["classifier_version"] == (
        V2_AI_VISUAL_MOTION_CLASSIFIER_VERSION
    )
    assert classification["classification_count"] == 33
    assert classification["motion_required_count"] == 0
    assert classification["motion_beneficial_count"] == 7
    assert classification["static_sufficient_count"] == 26
    assert artifacts.scene_plan_payload["motion_classification"] == classification
    assert artifacts.scene_plan_payload["asset_duplication_policy"] == {
        **artifacts.scene_plan_payload["asset_duplication_policy"],
        "algorithm": "dHash",
        "bit_width": 64,
        "near_duplicate_max_hamming_distance": 6,
        "reuse_windows_compared": False,
    }
    assert set(artifacts.image_prompts_by_scene_id) == {
        scene.scene_id for scene in owners if scene.production_route == "AI_IMAGE"
    }
    assert set(artifacts.video_prompts_by_scene_id) == {
        scene.scene_id for scene in owners if scene.production_route == "AI_VIDEO"
    }
    assert not artifacts.video_prompts_by_scene_id
    assert scenes[0].presentation_start_ms == 0
    assert scenes[-1].presentation_end_ms == 543_295
    assert all(
        left.presentation_end_ms == right.presentation_start_ms
        for left, right in zip(scenes, scenes[1:])
    )
    assert (
        max(
            scene.presentation_end_ms - scene.presentation_start_ms
            for scene in scenes
            if scene.production_route == "AI_IMAGE"
        )
        <= artifacts.policy.maximum_ai_image_presentation_ms
    )
    image_exposure_by_slot: dict[str, int] = {}
    for scene in scenes:
        if scene.production_route == "AI_IMAGE":
            image_exposure_by_slot[scene.primary_asset_slot_id] = (
                image_exposure_by_slot.get(scene.primary_asset_slot_id, 0)
                + scene.presentation_end_ms
                - scene.presentation_start_ms
            )
    assert max(image_exposure_by_slot.values()) <= 60_000


def _required_transformation_timeline() -> dict[str, object]:
    return {
        "duration_ms": 8_000,
        "narration_unit_compilation": {
            "narration_units": [
                {
                    "narration_unit_id": "nu-transform",
                    "information_unit_ids": ["iu-transform"],
                    "visual_function": "PROCESS_OR_DECISION_MODEL",
                    "semantic_intent": (
                        "Transform unstructured notes into validated fields, "
                        "step by step."
                    ),
                    "text": (
                        "First the notes are unstructured, then they become a "
                        "validated object."
                    ),
                    "importance": "CORE",
                    "factual_risk": "LOW",
                }
            ]
        },
        "timed_narration_unit_bindings": {
            "bindings": [
                {
                    "narration_unit_id": "nu-transform",
                    "actual_start_ms": 0,
                    "actual_end_ms": 8_000,
                }
            ]
        },
    }


def test_required_transformation_routes_video_or_blocks_before_downgrade() -> None:
    visual_run = SimpleNamespace(
        id=uuid.uuid4(),
        video_project_id=uuid.uuid4(),
        production_package_artifact_version_id=uuid.uuid4(),
        source_timeline_hash="e" * 64,
    )
    with pytest.raises(
        ValidationFailureError,
        match="V2_AI_VISUAL_VIDEO_DURATION_AUTHORITY_INSUFFICIENT",
    ):
        compile_ai_visual_stage_planning(
            visual_run=visual_run,
            timeline=_required_transformation_timeline(),
            provider_readiness_ref="readiness://fake",
            budget_authority_ref="budget://image-only",
            maximum_image_submissions=9,
            maximum_video_submissions=0,
        )

    artifacts = compile_ai_visual_stage_planning(
        visual_run=visual_run,
        timeline=_required_transformation_timeline(),
        provider_readiness_ref="readiness://fake",
        budget_authority_ref="budget://mixed",
        maximum_image_submissions=9,
        maximum_video_submissions=1,
    )
    owner = artifacts.scene_plan.scenes[0]
    assert owner.motion_need == "MOTION_REQUIRED"
    assert owner.production_route == "AI_VIDEO"
    assert artifacts.scene_plan.unique_ai_video_asset_slot_count == 1
    assert artifacts.scene_plan.unique_ai_image_asset_slot_count == 0
    assert set(artifacts.video_prompts_by_scene_id) == {owner.scene_id}
    assert not artifacts.image_prompts_by_scene_id


def test_normal_mixed_asset_count_is_bounded_cap_not_fixed_quota() -> None:
    assert _mixed_conservative_cost_within_authority(
        execution_kind="NORMAL_PRODUCTION",
        image_asset_count=3,
        video_asset_count=0,
        maximum_image_submissions=9,
        maximum_video_submissions=1,
        maximum_total_cost_usd=Decimal("0.999000"),
        video_unit_cost_usd=V2_AI_VISUAL_FIRST_VIDEO_VIDEO_UNIT_COST_USD,
    ) == Decimal("0.333000")

    assert (
        _mixed_conservative_cost_within_authority(
            execution_kind="GOVERNED_RERENDER",
            image_asset_count=V2_AI_VISUAL_FIRST_VIDEO_MAX_IMAGES,
            video_asset_count=V2_AI_VISUAL_FIRST_VIDEO_MAX_VIDEOS,
            maximum_image_submissions=V2_AI_VISUAL_FIRST_VIDEO_MAX_IMAGES,
            maximum_video_submissions=V2_AI_VISUAL_FIRST_VIDEO_MAX_VIDEOS,
            maximum_total_cost_usd=V2_AI_VISUAL_FIRST_VIDEO_MAX_COST_USD,
            video_unit_cost_usd=V2_AI_VISUAL_FIRST_VIDEO_VIDEO_UNIT_COST_USD,
        )
        == V2_AI_VISUAL_FIRST_VIDEO_MAX_COST_USD
    )

    with pytest.raises(
        ValidationFailureError,
        match="V2_AI_VISUAL_PLAN_OUTSIDE_COST_AUTHORITY",
    ):
        _mixed_conservative_cost_within_authority(
            execution_kind="NORMAL_PRODUCTION",
            image_asset_count=4,
            video_asset_count=0,
            maximum_image_submissions=3,
            maximum_video_submissions=0,
            maximum_total_cost_usd=Decimal("0.999000"),
            video_unit_cost_usd=None,
        )


def test_stage_veo_processing_replay_polls_recorded_operation_without_resubmit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.v2_ai_visual_stage as stage_module

    class FakeVideoService:
        def __init__(self) -> None:
            self.record = SimpleNamespace(
                state="PREPARED",
                generation_attempt_count=0,
                production_eligible=False,
            )
            self.submit_count = 0
            self.poll_count = 0
            self.ready = False

        def prepare(self, _authority):
            return self.record

        def submit_once(self, *, authority, execution):
            assert authority.asset_effect_id == "video-effect-001"
            assert not execution.blockers
            self.submit_count += 1
            self.record = SimpleNamespace(
                state="OPERATION_RECORDED",
                generation_attempt_count=1,
                production_eligible=False,
            )
            return self.record

        def poll_once(self, *, authority):
            assert authority.asset_effect_id == "video-effect-001"
            self.poll_count += 1
            self.record = SimpleNamespace(
                state="RESPONSE_CAPTURED" if self.ready else "POLLING",
                generation_attempt_count=1,
                production_eligible=False,
            )
            return self.record

        def materialize(self, *, authority):
            assert authority.asset_effect_id == "video-effect-001"
            self.record = SimpleNamespace(
                state="VERIFIED",
                generation_attempt_count=1,
                production_eligible=True,
            )
            return self.record

    settings = SimpleNamespace(
        gemini_api_key=SimpleNamespace(get_secret_value=lambda: "fake-key"),
        provider_real_execution_enabled=True,
        provider_production_execution_enabled=True,
        veo_real_generation_enabled=True,
    )
    monkeypatch.setattr(stage_module, "get_settings", lambda: settings)
    monkeypatch.setattr(stage_module.time, "sleep", lambda _seconds: None)
    service = FakeVideoService()
    adapter = object.__new__(V2AIVisualProductionAdapter)
    scope = SimpleNamespace(
        budget=SimpleNamespace(status="SUBMITTED"),
        maximum_total_cost_usd=Decimal("0.800000"),
    )
    authority = SimpleNamespace(asset_effect_id="video-effect-001")

    with pytest.raises(
        WorkflowStageError,
        match="Veo operations remain processing",
    ):
        adapter._execute_video_effects(
            scope=scope,
            service=service,
            authorities=[authority],
        )
    assert service.submit_count == 1
    assert service.record.state == "POLLING"

    service.ready = True
    records = adapter._execute_video_effects(
        scope=scope,
        service=service,
        authorities=[authority],
    )
    assert records[0].state == "VERIFIED"
    assert service.submit_count == 1


def test_mixed_verified_assets_seal_video_technical_receipt_in_manifest() -> None:
    effect_id = uuid.uuid4()
    scene_hash = "7" * 64
    technical = VeoTechnicalMotionInspectionEvidence.build(
        asset_effect_id=str(effect_id),
        asset_effect_identity_hash="8" * 64,
        provider_request_hash="9" * 64,
        asset_slot_id="slot-video",
        primary_asset_owner_scene_id="scene-video",
        bound_scene_ids=["scene-video"],
        bound_scene_plan_hashes=[scene_hash],
        scene_plan_hash=scene_hash,
        compiled_prompt_hash="a" * 64,
        prompt_hash="b" * 64,
        required_semantic_anchors=[
            "workflow input",
            "validated schema",
            "bounded tool action",
            "cinematic environment",
        ],
        model_id="veo-3.1-fast-generate-preview",
        provider_operation_id="operations/fake-video-001",
        output_ref="ai-visual-runs/run/effects/video/verified-primary.mp4",
        output_checksum="c" * 64,
        qc_ref="ai-visual-runs/run/effects/video/qc.json",
        qc_hash="d" * 64,
        sampled_frame_sha256=["e" * 64, "f" * 64, "e" * 64],
    )
    row = SimpleNamespace(
        id=effect_id,
        qc_evidence={"technical_motion_evidence": technical.model_dump(mode="json")},
        state="VERIFIED",
        route="AI_VIDEO",
        provider_key="google_veo",
        provider_call_count=1,
        effect_identity_hash="8" * 64,
        output_ref=technical.output_ref,
        output_checksum=technical.output_checksum,
        qc_ref=technical.qc_ref,
        qc_hash=technical.qc_hash,
        asset_slot_id=technical.asset_slot_id,
        primary_asset_owner_scene_id=technical.primary_asset_owner_scene_id,
        bound_scene_ids=technical.bound_scene_ids,
        bound_scene_plan_hashes=technical.bound_scene_plan_hashes,
        model_id=technical.model_id,
        output_size_bytes=2048,
        output_content_type="video/mp4",
        output_width=1280,
        output_height=720,
        output_duration_ms=8000,
        output_fps=24,
    )

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, requested_id):
            return row if requested_id == effect_id else None

    adapter = object.__new__(V2AIVisualProductionAdapter)
    adapter._session_factory = _Session
    video_record = SimpleNamespace(asset_effect_id=str(effect_id), state="VERIFIED")
    image_assets = adapter._verified_assets(
        [
            _verified_image_record(
                ordinal=1,
                checksum="1" * 64,
                dhash="0000000000000000",
            )
        ]
    )
    video_assets = adapter._verified_video_assets([video_record])
    manifest = build_ai_visual_asset_manifest(
        manifest_id=str(uuid.uuid4()),
        production_visual_policy_ref="config://visual-policy/fake",
        production_visual_policy_hash="2" * 64,
        scene_plan_ref="ai-visual-runs/run/scene-plan.json",
        scene_plan_hash="3" * 64,
        style_bible_ref="ai-visual-runs/run/style-bible.json",
        style_bible_hash="4" * 64,
        motion_grammar_ref="ai-visual-runs/run/video-motion-grammar.json",
        motion_grammar_hash="5" * 64,
        effect_plan_ref="ai-visual-runs/run/ffmpeg-effect-plan.json",
        effect_plan_hash="6" * 64,
        assets=[*image_assets, *video_assets],
    )

    assert manifest.ai_image_asset_count == 1
    assert manifest.ai_video_asset_count == 1
    assert manifest.asset_count == 2
    assert video_assets[0].asset_receipt_hash == technical.content_hash
    assert technical.actual_asset_semantic_inspection_performed is False
    assert technical.human_semantic_review_required is True


def test_planning_projection_crash_replay_never_reverses_generating_state() -> None:
    bindings = _planning_binding_values()
    run = SimpleNamespace(
        state="AUTHORIZED",
        current_phase="AUTHORIZE",
        projection_version=1,
        style_bible_id=None,
        style_bible_hash=None,
        scene_plan_id=None,
        scene_plan_hash=None,
        motion_grammar_ref=None,
        motion_grammar_hash=None,
    )

    assert _apply_exact_planning_projection(run, **bindings) is True
    assert (run.state, run.current_phase, run.projection_version) == (
        "PLANNED",
        "PLAN",
        2,
    )
    assert _apply_exact_planning_projection(run, **bindings) is False
    assert (run.state, run.current_phase, run.projection_version) == (
        "PLANNED",
        "PLAN",
        2,
    )

    run.state = "GENERATING"
    run.current_phase = "GENERATE"
    run.projection_version = 3
    assert _apply_exact_planning_projection(run, **bindings) is False
    assert (run.state, run.current_phase, run.projection_version) == (
        "GENERATING",
        "GENERATE",
        3,
    )

    with pytest.raises(
        ValidationFailureError,
        match="V2_AI_VISUAL_PLANNING_REPLAY_AUTHORITY_DRIFT",
    ):
        _apply_exact_planning_projection(
            run,
            **{**bindings, "scene_plan_hash": "e" * 64},
        )


def test_settled_budget_reconciliation_requires_every_verified_owner_effect() -> None:
    visual_run_id = uuid.uuid4()

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _Session:
        def __init__(self, rows):
            self._rows = rows

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def scalars(self, statement):
            del statement
            return _Rows(self._rows)

    adapter = object.__new__(V2AIVisualProductionAdapter)
    incomplete = [
        SimpleNamespace(
            state="PREPARED",
            provider_call_count=0,
            route="AI_IMAGE",
            provider_key="google_gemini_image",
            output_ref=None,
            output_checksum=None,
            qc_ref=None,
            qc_hash=None,
        )
    ]
    adapter._session_factory = lambda: _Session(incomplete)
    with pytest.raises(
        ValidationFailureError,
        match="V2_AI_VISUAL_SETTLED_BUDGET_RECONCILIATION_INCOMPLETE",
    ):
        adapter._require_settled_reconciliation_effects(
            visual_run_id=visual_run_id,
            expected_count=1,
        )

    verified = [
        SimpleNamespace(
            state="VERIFIED",
            provider_call_count=1,
            route="AI_IMAGE",
            provider_key="google_gemini_image",
            output_ref="ai-visual-runs/run/effects/effect/verified-primary.jpg",
            output_checksum="f" * 64,
            qc_ref="ai-visual-runs/run/effects/effect/qc.json",
            qc_hash="0" * 64,
        )
    ]
    adapter._session_factory = lambda: _Session(verified)
    adapter._require_settled_reconciliation_effects(
        visual_run_id=visual_run_id,
        expected_count=1,
    )


def test_normal_final_review_seals_candidate_without_replacement_lineage() -> None:
    workflow_id = uuid.uuid4()
    project_id = uuid.uuid4()
    visual_run_id = uuid.uuid4()
    manifest_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    final_media_id = uuid.uuid4()
    manifest_hash = "a" * 64
    effect_plan_hash = "b" * 64
    render_hash = "c" * 64
    archive_hash = "d" * 64
    run = SimpleNamespace(
        id=workflow_id,
        video_project_id=project_id,
        ai_visual_production_run_id=visual_run_id,
    )
    visual_run = SimpleNamespace(
        id=visual_run_id,
        workflow_run_id=workflow_id,
        video_project_id=project_id,
        rerender_authority_id=None,
        execution_kind="NORMAL_PRODUCTION",
        asset_manifest_id=manifest_id,
        final_media_ref_id=final_media_id,
        render_output_checksum=render_hash,
        archive_receipt_hash=archive_hash,
        state="ARCHIVED",
        current_phase="ARCHIVE",
        final_review_candidate_id=None,
        completed_at=None,
        projection_version=11,
    )
    manifest = SimpleNamespace(
        id=manifest_id,
        content_hash=manifest_hash,
        effect_plan_hash=effect_plan_hash,
    )
    candidate = SimpleNamespace(
        id=candidate_id,
        workflow_run_id=workflow_id,
        ai_visual_production_run_id=visual_run_id,
        ai_visual_asset_manifest_hash=manifest_hash,
        ffmpeg_effect_plan_hash=effect_plan_hash,
        supersedes_final_review_candidate_id=None,
        final_media_ref_id=final_media_id,
        render_output_checksum=render_hash,
        archive_receipt_hash=archive_hash,
    )

    class _Session:
        def __init__(self) -> None:
            self.flush_count = 0

        def get(self, model, identifier, **kwargs):
            del kwargs
            if model.__name__ == "AIVisualProductionRun":
                assert identifier == visual_run_id
                return visual_run
            if model.__name__ == "AIVisualAssetManifest":
                assert identifier == manifest_id
                return manifest
            raise AssertionError(model.__name__)

        def scalar(self, statement):
            del statement
            return None

        def flush(self) -> None:
            self.flush_count += 1

    session = _Session()
    completed_at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    context = SimpleNamespace(run=run, session=session)

    _settle_ai_visual_final_review_projection(
        context=context,
        candidate=candidate,
        completed_at=completed_at,
    )

    assert (
        visual_run.state,
        visual_run.current_phase,
        visual_run.final_review_candidate_id,
        visual_run.completed_at,
        visual_run.projection_version,
    ) == (
        "FINAL_REVIEW_READY",
        "FINALIZE",
        candidate_id,
        completed_at,
        12,
    )
    assert session.flush_count == 1

    _settle_ai_visual_final_review_projection(
        context=context,
        candidate=candidate,
        completed_at=completed_at,
    )
    assert session.flush_count == 1


def test_governed_final_review_exact_replay_requires_existing_lineage() -> None:
    workflow_id = uuid.uuid4()
    source_workflow_id = uuid.uuid4()
    project_id = uuid.uuid4()
    visual_run_id = uuid.uuid4()
    manifest_id = uuid.uuid4()
    authority_id = uuid.uuid4()
    rejected_candidate_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    final_media_id = uuid.uuid4()
    manifest_hash = "1" * 64
    effect_plan_hash = "2" * 64
    render_hash = "3" * 64
    archive_hash = "4" * 64
    run = SimpleNamespace(
        id=workflow_id,
        video_project_id=project_id,
        ai_visual_production_run_id=visual_run_id,
    )
    visual_run = SimpleNamespace(
        id=visual_run_id,
        workflow_run_id=workflow_id,
        video_project_id=project_id,
        rerender_authority_id=authority_id,
        execution_kind="GOVERNED_RERENDER",
        asset_manifest_id=manifest_id,
        final_media_ref_id=final_media_id,
        render_output_checksum=render_hash,
        archive_receipt_hash=archive_hash,
        state="FINAL_REVIEW_READY",
        current_phase="FINALIZE",
        final_review_candidate_id=candidate_id,
        projection_version=12,
    )
    manifest = SimpleNamespace(
        id=manifest_id,
        content_hash=manifest_hash,
        effect_plan_hash=effect_plan_hash,
    )
    authority = SimpleNamespace(
        id=authority_id,
        source_workflow_run_id=source_workflow_id,
        replacement_workflow_run_id=workflow_id,
        rejected_final_review_candidate_id=rejected_candidate_id,
    )
    lineage = SimpleNamespace(
        replacement_final_review_candidate_id=candidate_id,
    )
    candidate = SimpleNamespace(
        id=candidate_id,
        workflow_run_id=workflow_id,
        ai_visual_production_run_id=visual_run_id,
        ai_visual_asset_manifest_hash=manifest_hash,
        ffmpeg_effect_plan_hash=effect_plan_hash,
        supersedes_final_review_candidate_id=rejected_candidate_id,
        final_media_ref_id=final_media_id,
        render_output_checksum=render_hash,
        archive_receipt_hash=archive_hash,
    )

    class _Session:
        def get(self, model, identifier, **kwargs):
            del kwargs
            values = {
                "AIVisualProductionRun": (visual_run_id, visual_run),
                "AIVisualAssetManifest": (manifest_id, manifest),
                "AIVisualRerenderAuthority": (authority_id, authority),
            }
            expected_id, value = values[model.__name__]
            assert identifier == expected_id
            return value

        def scalar(self, statement):
            del statement
            return lineage

        def flush(self) -> None:
            raise AssertionError("exact replay must not mutate")

    _settle_ai_visual_final_review_projection(
        context=SimpleNamespace(run=run, session=_Session()),
        candidate=candidate,
        completed_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
    )
