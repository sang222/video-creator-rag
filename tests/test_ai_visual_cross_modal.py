from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts.ai_visual_cross_modal import (
    VeoTechnicalMotionInspectionEvidence,
    VerifiedAIVisualEffectEvidence,
)
from app.contracts.ai_visual_production import (
    AIVisualPlanCompilation,
    AIVisualPlanningPolicy,
    AIVisualScenePlan,
    VideoMotionGrammar,
    VideoVisualStyleBible,
    ai_visual_stable_hash,
)
from app.contracts.cross_modal import (
    NarrationUnit,
    NarrationUnitCompilation,
    TimedNarrationBindingSet,
    TimedNarrationUnitBinding,
    cross_modal_hash,
)
from app.services.ai_visual_cross_modal import (
    AIVisualCrossModalError,
    ai_visual_cross_modal_qc_report,
    verified_ai_visual_effect_evidence,
)
from app.services.ai_visual_planner import AIImagePromptCompiler, AIVideoPromptCompiler
from app.services.native_render_plan import stable_hash as veo_stable_hash
from app.services.production_package import semantic_hash
from app.services.v2_ai_visual_renderer import (
    VerifiedAIVisualAsset,
    build_ai_visual_asset_manifest,
)
from app.services.v2_ai_visual_store import SQLAlchemyVeoEffectStore
from app.services.v2_veo_visual_provider import V2VeoEffectRecord


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _narration_unit(index: int) -> NarrationUnit:
    text = f"Narration {index}"
    body = {
        "narration_unit_id": f"nu-{index}",
        "section_id": "section-1",
        "ordinal": index,
        "source_text_span": {
            "start": (index - 1) * 20,
            "end": index * 20,
        },
        "source_text_hash": _sha(text),
        "text": text,
        "information_unit_ids": [f"iu-{index}"],
        "assignment_requirement_ids": [f"requirement-{index}"],
        "evidence_span_ids": [f"evidence-{index}"],
        "semantic_intent": f"Semantic meaning {index}",
        "visual_function": "PROCESS_OR_DECISION_MODEL",
        "importance": "CORE",
        "factual_risk": "MEDIUM",
        "estimated_spoken_duration_ms": 1_000,
    }
    return NarrationUnit(**body, content_hash=cross_modal_hash(body))


def _binding(index: int, spoken_hash: str) -> TimedNarrationUnitBinding:
    body = {
        "narration_unit_id": f"nu-{index}",
        "spoken_text_hash": spoken_hash,
        "spoken_token_refs": [f"token-{index}"],
        "verified_word_refs": [f"word-{index}"],
        "actual_start_ms": (index - 1) * 1_000,
        "actual_end_ms": index * 1_000,
        "alignment_confidence": 1.0,
        "alignment_evidence_ref": f"alignment://{index}",
    }
    alignment_hash = cross_modal_hash(body)
    return TimedNarrationUnitBinding(
        **body,
        alignment_hash=alignment_hash,
        content_hash=cross_modal_hash({**body, "alignment_hash": alignment_hash}),
    )


def _fixture(tmp_path: Path, *, mixed_video: bool = False) -> dict[str, object]:
    style = VideoVisualStyleBible.build(
        style_bible_id="style-1",
        video_project_id="project-1",
        package_id="package-1",
        overall_visual_language="cinematic conceptual realism",
        rendering_style="photoreal editorial illustration",
        lighting="soft directional light",
        contrast="controlled contrast",
        palette_guidance=["indigo", "amber"],
        materials=["glass", "metal"],
        camera_language="restrained documentary framing",
        depth="layered depth",
        technical_illustration_language="physical semantic metaphors",
        human_depiction_rules=["natural anatomy"],
        technology_depiction_rules=["no fake UI"],
        negative_aesthetic_constraints=["no presentation slide"],
    )
    policy = AIVisualPlanningPolicy.production_default()
    grammar = VideoMotionGrammar.production_default(
        grammar_id="grammar-1", style_bible_hash=style.content_hash
    )
    units = [_narration_unit(1), _narration_unit(2)]
    compilation_body = {
        "schema_version": "vcos.narration-unit-compilation.v1",
        "canonical_script_hash": _sha("script"),
        "coverage_plan_hash": _sha("coverage"),
        "narration_units": [item.model_dump(mode="json") for item in units],
    }
    compilation = NarrationUnitCompilation(
        **compilation_body, content_hash=cross_modal_hash(compilation_body)
    )
    spoken_hash = _sha("spoken")
    timing_body = {
        "schema_version": "vcos.timed-narration-unit-binding.v1",
        "narration_unit_compilation_hash": compilation.content_hash,
        "spoken_text_hash": spoken_hash,
        "bindings": [
            _binding(1, spoken_hash).model_dump(mode="json"),
            _binding(2, spoken_hash).model_dump(mode="json"),
        ],
    }
    timings = TimedNarrationBindingSet(
        **timing_body, content_hash=cross_modal_hash(timing_body)
    )
    timeline = {
        "duration_ms": 2_000,
        "narration_unit_compilation": compilation.model_dump(mode="json"),
        "narration_unit_compilation_hash": compilation.content_hash,
        "timed_narration_unit_bindings": timings.model_dump(mode="json"),
        "timed_narration_unit_bindings_hash": timings.content_hash,
    }
    scenes: list[AIVisualScenePlan] = []
    for index, unit in enumerate(units, start=1):
        video_scene = mixed_video and index == 2
        scene_body = {
            "schema_version": "vcos.ai-visual-scene-plan.v1",
            "scene_id": f"scene-{index}",
            "ordinal": index,
            "narration_unit_ids": [unit.narration_unit_id],
            "information_unit_ids": list(unit.information_unit_ids),
            "actual_start_ms": (index - 1) * 1_000,
            "actual_end_ms": index * 1_000,
            "presentation_start_ms": (index - 1) * 1_000,
            "presentation_end_ms": index * 1_000,
            "scene_meaning": unit.semantic_intent,
            "visual_function": "PROCESS",
            "core_subject": f"subject-{index}",
            "secondary_subjects": [],
            "action_or_relation": unit.text,
            "environment": f"environment-{index}",
            "visual_goal": f"goal-{index}",
            "visual_style_direction": "follow the style bible",
            "composition_direction": "asymmetric composition",
            "camera_direction": "wide documentary camera",
            "continuity_constraints": ["preserve palette"],
            "motion_need": "MOTION_REQUIRED" if video_scene else "STATIC_SUFFICIENT",
            "production_route": "AI_VIDEO" if video_scene else "AI_IMAGE",
            "primary_asset_slot_id": f"slot-{index}",
            "reuses_primary_asset_from_scene_id": None,
            "asset_reuse_semantic_reason": None,
            "prompt_brief": unit.semantic_intent,
            "negative_constraints": ["no visible generated text"],
            "factual_risk": "MEDIUM",
            "importance": "HIGH",
            "transition_semantic_reason": "TOPIC_SHIFT",
            "style_bible_hash": style.content_hash,
            "planning_policy_hash": policy.content_hash,
        }
        scenes.append(
            AIVisualScenePlan(
                **scene_body, content_hash=ai_visual_stable_hash(scene_body)
            )
        )
    plan_body = {
        "schema_version": "vcos.ai-visual-plan-compilation.v1",
        "style_bible_hash": style.content_hash,
        "planning_policy_hash": policy.content_hash,
        "canonical_duration_ms": 2_000,
        "maximum_ai_image_presentation_ms": 12_000,
        "maximum_ai_video_presentation_ms": 8_000,
        "maximum_ai_image_asset_exposure_ms": 24_000,
        "scenes": scenes,
        "ai_image_scene_count": 1 if mixed_video else 2,
        "ai_video_scene_count": 1 if mixed_video else 0,
        "unique_asset_slot_count": 2,
        "unique_ai_image_asset_slot_count": 1 if mixed_video else 2,
        "unique_ai_video_asset_slot_count": 1 if mixed_video else 0,
        "reused_presentation_window_count": 0,
        "coverage_gate": "PASS",
    }
    plan = AIVisualPlanCompilation(
        **plan_body, content_hash=ai_visual_stable_hash(plan_body)
    )
    scene_plan_artifact_hash = _sha("scene-plan-artifact")
    assets: list[VerifiedAIVisualAsset] = []
    evidence: list[VerifiedAIVisualEffectEvidence] = []
    image_compiler = AIImagePromptCompiler()
    video_compiler = AIVideoPromptCompiler()
    for index, scene in enumerate(scenes, start=1):
        is_video = scene.production_route == "AI_VIDEO"
        asset_path = tmp_path / f"asset-{index}.{'mp4' if is_video else 'jpg'}"
        asset_path.write_bytes(f"valid-ai-asset-{index}".encode())
        checksum = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        effect_hash = _sha(f"effect-{index}")
        receipt_hash = _sha(f"receipt-{index}")
        qc_hash = _sha(f"qc-{index}")
        prompt = (
            video_compiler.compile(scene_plan=scene, style_bible=style)
            if is_video
            else image_compiler.compile(
                scene_plan=scene, style_bible=style, motion_grammar=grammar
            )
        )
        semantic_anchors = [
            f"core_subject:{scene.core_subject}",
            f"action_or_relation:{scene.action_or_relation}",
            f"environment:{scene.environment}",
            f"visual_goal:{scene.visual_goal}",
        ]
        sampled_frame_sha256 = (
            [_sha(f"frame-{index}-{frame}") for frame in range(5)] if is_video else []
        )
        inspection_hash = _sha(f"video-inspection-{index}")
        assets.append(
            VerifiedAIVisualAsset.build(
                asset_slot_id=f"slot-{index}",
                primary_asset_owner_scene_id=scene.scene_id,
                bound_scene_ids=[scene.scene_id],
                bound_scene_plan_hashes=[scene.content_hash],
                route=scene.production_route,
                asset_acquisition_mode="GENERATED",
                provider_key="google_veo" if is_video else "google_gemini_image",
                model_id=(
                    "veo-3.1-fast-generate-preview"
                    if is_video
                    else "gemini-3.1-flash-image"
                ),
                asset_effect_ref=f"effect://{index}",
                asset_effect_identity_hash=effect_hash,
                primary_asset_ref=str(asset_path),
                primary_asset_hash=checksum,
                output_ref=str(asset_path),
                output_checksum=checksum,
                output_size_bytes=asset_path.stat().st_size,
                output_content_type="video/mp4" if is_video else "image/jpeg",
                width=1280 if is_video else 1920,
                height=720 if is_video else 1080,
                duration_ms=8_000 if is_video else None,
                fps=24 if is_video else None,
                qc_ref=f"qc://{index}",
                qc_hash=qc_hash,
                asset_receipt_hash=inspection_hash if is_video else receipt_hash,
            )
        )
        if is_video:
            evidence.append(
                VerifiedAIVisualEffectEvidence.build(
                    asset_slot_id=f"slot-{index}",
                    primary_asset_owner_scene_id=scene.scene_id,
                    bound_scene_ids=[scene.scene_id],
                    bound_scene_plan_hashes=[scene.content_hash],
                    route="AI_VIDEO",
                    provider_key="google_veo",
                    model_id="veo-3.1-fast-generate-preview",
                    asset_effect_identity_hash=effect_hash,
                    provider_request_hash=_sha(f"request-{index}"),
                    provider_operation_id=f"operations/video-{index}",
                    compiled_prompt_hash=prompt.content_hash,
                    prompt=prompt.prompt,
                    prompt_hash=prompt.prompt_hash,
                    output_ref=str(asset_path),
                    output_checksum=checksum,
                    qc_ref=f"qc://{index}",
                    qc_hash=qc_hash,
                    provider_receipt_hash=inspection_hash,
                    asset_inspection_evidence_hash=inspection_hash,
                    required_semantic_anchors=semantic_anchors,
                    sampled_frame_sha256=sampled_frame_sha256,
                    durable_effect_evidence_hash=_sha(f"durable-{index}"),
                )
            )
        else:
            evidence.append(
                VerifiedAIVisualEffectEvidence.build(
                    asset_slot_id=f"slot-{index}",
                    primary_asset_owner_scene_id=scene.scene_id,
                    bound_scene_ids=[scene.scene_id],
                    bound_scene_plan_hashes=[scene.content_hash],
                    route="AI_IMAGE",
                    provider_key="google_gemini_image",
                    model_id="gemini-3.1-flash-image",
                    asset_effect_identity_hash=effect_hash,
                    compiled_prompt_hash=prompt.content_hash,
                    prompt=prompt.prompt,
                    prompt_hash=prompt.prompt_hash,
                    output_ref=str(asset_path),
                    output_checksum=checksum,
                    qc_ref=f"qc://{index}",
                    qc_hash=qc_hash,
                    provider_receipt_hash=receipt_hash,
                    asset_semantic_attestation_hash=_sha(
                        f"semantic-attestation-{index}"
                    ),
                    actual_asset_description_source=("SAME_INTERACTION_MODEL_OUTPUT"),
                    observed_output_summary=f"Observed semantic asset {index}",
                    observed_primary_subjects=[f"subject-{index}"],
                    observed_action_or_relation=f"Narration {index}",
                    observed_environment=f"environment-{index}",
                    required_semantic_anchors=semantic_anchors,
                    observed_semantic_anchors=semantic_anchors,
                    provider_text_hash=_sha(f"provider-text-{index}"),
                    provider_semantic_match_asserted=True,
                    provider_semantic_mismatch_reasons=[],
                    provider_forbidden_content_detected=[],
                    independent_multimodal_inspection_performed=False,
                    human_semantic_review_required=True,
                    durable_effect_evidence_hash=_sha(f"durable-{index}"),
                )
            )
    manifest = build_ai_visual_asset_manifest(
        manifest_id="manifest-1",
        production_visual_policy_ref="config://ai-only",
        production_visual_policy_hash=_sha("policy"),
        scene_plan_ref="artifact://scene-plan",
        scene_plan_hash=scene_plan_artifact_hash,
        style_bible_ref="artifact://style",
        style_bible_hash=style.content_hash,
        motion_grammar_ref="artifact://grammar",
        motion_grammar_hash=grammar.content_hash,
        effect_plan_ref="artifact://effects",
        effect_plan_hash=_sha("effects"),
        assets=assets,
    )
    return {
        "timeline": timeline,
        "timeline_hash": ai_visual_stable_hash(timeline),
        "scene_plan": plan,
        "scene_plan_artifact_hash": scene_plan_artifact_hash,
        "style_bible": style,
        "motion_grammar": grammar,
        "manifest": manifest,
        "effect_evidence": evidence,
        "workspace_root": tmp_path,
    }


def _verified_veo_row(tmp_path: Path) -> SimpleNamespace:
    fixture = _fixture(tmp_path, mixed_video=True)
    scene = fixture["scene_plan"].scenes[1]
    asset = fixture["manifest"].assets[1]
    prompt = AIVideoPromptCompiler().compile(
        scene_plan=scene,
        style_bible=fixture["style_bible"],
    )
    effect_id = "00000000-0000-0000-0000-000000000002"
    operation_id = "operations/veo-cross-modal-2"
    identity_hash = asset.asset_effect_identity_hash
    provider_request = {
        "model": asset.model_id,
        "prompt": prompt.prompt,
        "config": {
            "aspect_ratio": "16:9",
            "duration_seconds": 8,
            "number_of_videos": 1,
            "resolution": "720p",
        },
    }
    request_hash = veo_stable_hash(
        {"identity_hash": identity_hash, "provider_request": provider_request}
    )
    authority = {
        "provider": "google_veo",
        "asset_effect_id": effect_id,
        "asset_slot_id": asset.asset_slot_id,
        "scene_id": scene.scene_id,
        "primary_asset_owner_scene_id": scene.scene_id,
        "bound_scene_ids": [scene.scene_id],
        "bound_scene_plan_hashes": [scene.content_hash],
        "scene_plan_hash": scene.content_hash,
        "compiled_prompt_hash": prompt.content_hash,
        "compiled_prompt_content_hash": prompt.content_hash,
        "prompt_hash": prompt.prompt_hash,
        "model_id": asset.model_id,
    }
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    request_body = {
        "schema_version": "vcos.v2-veo-request-journal.v1",
        "prepared_at": now.isoformat(),
        "asset_effect_id": effect_id,
        "scene_id": scene.scene_id,
        "route": "AI_VIDEO",
        "provider": "google_veo",
        "identity_hash": identity_hash,
        "request_hash": request_hash,
        "authority": authority,
        "provider_request": provider_request,
    }
    request_journal = {
        **request_body,
        "journal_hash": veo_stable_hash(request_body),
    }
    response_body = {
        "schema_version": "vcos.v2-veo-response-journal.v1",
        "recorded_at": now.isoformat(),
        "event": "EXACT_OUTPUT_DOWNLOADED",
        "request_hash": request_hash,
        "provider_status": "SUCCEEDED",
        "provider_operation_id": operation_id,
    }
    response_journal = {
        **response_body,
        "journal_hash": veo_stable_hash(response_body),
    }
    normalization_body = {
        "schema_version": "vcos.v2-veo-normalization.v1",
        "output_sha256": asset.output_checksum,
        "output_audio_stream_count": 0,
        "provider_audio_discarded": True,
    }
    normalization = {
        **normalization_body,
        "normalization_hash": veo_stable_hash(normalization_body),
    }
    sampled_frames = [_sha(f"verified-frame-{index}") for index in range(5)]
    qc_checks = {
        "duration_seconds": 8.0,
        "sampled_frames_valid": True,
        "sampled_frame_sha256": sampled_frames,
        "not_blank": True,
        "mostly_black_absent": True,
        "not_frozen_throughout": True,
    }
    qc_body = {
        "schema_version": "vcos.v2-veo-asset-qc.v1",
        "result": "PASS",
        "checks": qc_checks,
        "reason_codes": [],
        "asset_sha256": asset.output_checksum,
        "provider_provenance_valid": True,
        "scene_binding_valid": True,
        "provider_audio_authority": False,
    }
    qc_receipt = {**qc_body, "qc_hash": veo_stable_hash(qc_body)}
    record = V2VeoEffectRecord(
        asset_effect_id=effect_id,
        identity_hash=identity_hash,
        request_hash=request_hash,
        authority=authority,
        request_journal=request_journal,
        state="VERIFIED",
        version=8,
        generation_attempt_count=1,
        prepared_at=now,
        submitted_at=now,
        response_captured_at=now,
        completed_at=now,
        provider_operation_id=operation_id,
        response_journals=(response_journal,),
        normalized_output_ref=asset.output_ref,
        normalized_output_sha256=asset.output_checksum,
        normalized_output_size_bytes=asset.output_size_bytes,
        output_content_type="video/mp4",
        output_width=asset.width,
        output_height=asset.height,
        output_duration_ms=8_000,
        output_fps=Decimal("24"),
        output_audio_stream_count=0,
        normalization_receipt=normalization,
        qc_receipt=qc_receipt,
        actual_cost_usd=None,
        conservative_settlement_cost_usd=Decimal("0.8"),
        cost_settlement_basis="CONSERVATIVE_CATALOG_ESTIMATE_ACCEPTED",
        production_eligible=True,
    )
    required_anchors = [
        f"core_subject:{scene.core_subject}",
        f"action_or_relation:{scene.action_or_relation}",
        f"environment:{scene.environment}",
        f"visual_goal:{scene.visual_goal}",
    ]
    technical = VeoTechnicalMotionInspectionEvidence.build(
        asset_effect_id=effect_id,
        asset_effect_identity_hash=identity_hash,
        provider_request_hash=request_hash,
        asset_slot_id=asset.asset_slot_id,
        primary_asset_owner_scene_id=scene.scene_id,
        bound_scene_ids=[scene.scene_id],
        bound_scene_plan_hashes=[scene.content_hash],
        scene_plan_hash=scene.content_hash,
        compiled_prompt_hash=prompt.content_hash,
        prompt_hash=prompt.prompt_hash,
        required_semantic_anchors=required_anchors,
        model_id=asset.model_id,
        provider_operation_id=operation_id,
        output_ref=asset.output_ref,
        output_checksum=asset.output_checksum,
        qc_ref="qc://verified-video-2",
        qc_hash=qc_receipt["qc_hash"],
        sampled_frame_sha256=sampled_frames,
    )
    record_envelope = SQLAlchemyVeoEffectStore._record_payload(record)
    qc_evidence = {
        **record_envelope,
        "record_hash": semantic_hash(record_envelope),
        "technical_qc": qc_receipt,
        "technical_motion_evidence": technical.model_dump(mode="json"),
    }
    return SimpleNamespace(
        id=effect_id,
        state="VERIFIED",
        route="AI_VIDEO",
        provider_key="google_veo",
        model_id=asset.model_id,
        effect_identity_hash=identity_hash,
        request_hash=request_hash,
        asset_slot_id=asset.asset_slot_id,
        scene_id=scene.scene_id,
        primary_asset_owner_scene_id=scene.scene_id,
        bound_scene_ids=[scene.scene_id],
        bound_scene_plan_hashes=[scene.content_hash],
        scene_plan_hash=scene.content_hash,
        compiled_prompt_hash=prompt.content_hash,
        compiled_prompt_content_hash=prompt.content_hash,
        prompt_hash=prompt.prompt_hash,
        provider_operation_id=operation_id,
        output_ref=asset.output_ref,
        output_checksum=asset.output_checksum,
        output_size_bytes=asset.output_size_bytes,
        output_content_type="video/mp4",
        output_width=asset.width,
        output_height=asset.height,
        output_duration_ms=8_000,
        output_fps=Decimal("24"),
        output_audio_stream_count=0,
        qc_ref=technical.qc_ref,
        qc_hash=technical.qc_hash,
        asset_receipt_hash=technical.content_hash,
        qc_evidence=qc_evidence,
    )


def test_ai_cross_modal_report_binds_narration_prompt_receipt_and_actual_bytes(
    tmp_path: Path,
) -> None:
    report = ai_visual_cross_modal_qc_report(**_fixture(tmp_path))

    assert report.deterministic_disposition == "PASS"
    assert report.evidence_scope == "LINEAGE_AND_SCENE_BINDING"
    assert report.same_interaction_model_output_semantic_inspection_performed is True
    assert report.actual_asset_description_source == "SAME_INTERACTION_MODEL_OUTPUT"
    assert report.same_interaction_asset_semantic_attestations_verified is True
    assert report.actual_asset_semantic_inspection_performed is True
    assert report.independent_multimodal_inspection_performed is False
    assert (
        report.actual_asset_semantic_disposition
        == "PASS_SAME_INTERACTION_ATTESTED_PENDING_HUMAN_REVIEW"
    )
    assert report.human_semantic_review_required is True
    assert len(report.asset_attestations) == 2
    assert len(report.scene_bindings) == 2
    assert (
        report.asset_attestations[0].observed_semantic_anchors
        == report.asset_attestations[0].required_semantic_anchors
    )


def test_ai_cross_modal_mixed_report_keeps_veo_semantics_pending(
    tmp_path: Path,
) -> None:
    report = ai_visual_cross_modal_qc_report(**_fixture(tmp_path, mixed_video=True))

    assert report.deterministic_disposition == "PASS"
    assert (
        report.automated_disposition_scope
        == "LINEAGE_SCENE_TIMING_TECHNICAL_AND_MOTION_ONLY"
    )
    assert report.image_asset_count == 1
    assert report.video_asset_count == 1
    assert report.image_same_interaction_semantic_attestation_count == 1
    assert report.video_technical_motion_inspection_count == 1
    assert report.semantic_inspected_asset_count == 1
    assert report.semantic_uninspected_asset_count == 1
    assert report.actual_asset_semantic_inspection_performed is False
    assert report.same_interaction_model_output_semantic_inspection_performed is False
    assert report.video_actual_asset_semantic_inspection_performed is False
    assert report.video_provider_semantic_match_asserted is False
    assert report.automated_semantic_conformity_asserted is False
    assert (
        report.actual_asset_description_source
        == "MIXED_IMAGE_ATTESTATION_AND_VIDEO_TECHNICAL_EVIDENCE"
    )
    assert (
        report.actual_asset_semantic_disposition
        == "MIXED_IMAGE_ATTESTED_VIDEO_PENDING_HUMAN_SEMANTIC_REVIEW"
    )
    video = next(item for item in report.asset_attestations if item.route == "AI_VIDEO")
    assert video.actual_asset_description_source == "NO_AUTOMATED_ASSET_DESCRIPTION"
    assert video.provider_semantic_match_asserted is False
    assert video.actual_asset_semantic_inspection_performed is False
    assert video.same_interaction_model_output_semantic_inspection_performed is False
    assert video.motion_inspection_performed is True
    assert video.observed_semantic_anchors == []


def test_verified_veo_effect_normalizes_exact_technical_motion_receipt(
    tmp_path: Path,
) -> None:
    evidence = verified_ai_visual_effect_evidence([_verified_veo_row(tmp_path)])[0]

    assert evidence.route == "AI_VIDEO"
    assert evidence.asset_inspection_scope == "VIDEO_LINEAGE_TECHNICAL_AND_MOTION_ONLY"
    assert evidence.actual_asset_description_source == "NO_AUTOMATED_ASSET_DESCRIPTION"
    assert evidence.provider_semantic_match_asserted is False
    assert evidence.actual_asset_semantic_inspection_performed is False
    assert evidence.same_interaction_model_output_semantic_inspection_performed is False
    assert evidence.automated_semantic_conformity_asserted is False
    assert evidence.motion_inspection_performed is True
    assert len(evidence.sampled_frame_sha256) == 5
    assert evidence.asset_semantic_attestation_hash is None
    assert evidence.asset_inspection_evidence_hash == evidence.provider_receipt_hash


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        (
            lambda row: setattr(row, "prompt_hash", _sha("swapped-prompt")),
            "AI_VISUAL_CROSS_MODAL_VIDEO_PROVIDER_RECEIPT_BINDING_MISMATCH",
        ),
        (
            lambda row: setattr(row, "provider_operation_id", "operations/swapped"),
            "AI_VISUAL_CROSS_MODAL_VIDEO_PROVIDER_RECEIPT_BINDING_MISMATCH",
        ),
        (
            lambda row: setattr(row, "bound_scene_plan_hashes", [_sha("swapped")]),
            "AI_VISUAL_CROSS_MODAL_VIDEO_PROVIDER_RECEIPT_BINDING_MISMATCH",
        ),
    ],
)
def test_verified_veo_effect_rejects_prompt_operation_and_scene_swaps(
    tmp_path: Path,
    mutation: object,
    expected_reason: str,
) -> None:
    row = _verified_veo_row(tmp_path)
    mutation(row)

    with pytest.raises(AIVisualCrossModalError, match=expected_reason):
        verified_ai_visual_effect_evidence([row])


def test_verified_veo_effect_rejects_swapped_receipt(tmp_path: Path) -> None:
    row = _verified_veo_row(tmp_path)
    raw = dict(row.qc_evidence["technical_motion_evidence"])
    body = {key: value for key, value in raw.items() if key != "content_hash"}
    body["output_checksum"] = _sha("unrelated-video")
    swapped = VeoTechnicalMotionInspectionEvidence(
        **body,
        content_hash=ai_visual_stable_hash(body),
    )
    row.qc_evidence = {
        **row.qc_evidence,
        "technical_motion_evidence": swapped.model_dump(mode="json"),
    }

    with pytest.raises(
        AIVisualCrossModalError,
        match="AI_VISUAL_CROSS_MODAL_VIDEO_PROVIDER_RECEIPT_BINDING_MISMATCH",
    ):
        verified_ai_visual_effect_evidence([row])


def test_ai_cross_modal_mixed_report_rejects_changed_video_bytes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, mixed_video=True)
    video = next(
        item for item in fixture["manifest"].assets if item.route == "AI_VIDEO"
    )
    Path(video.output_ref).write_bytes(b"swapped-video-bytes")

    with pytest.raises(
        AIVisualCrossModalError,
        match="AI_VISUAL_CROSS_MODAL_ACTUAL_ASSET_CHECKSUM_MISMATCH",
    ):
        ai_visual_cross_modal_qc_report(**fixture)


def test_ai_cross_modal_blocks_valid_asset_swapped_from_unrelated_scene(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    evidence = list(fixture["effect_evidence"])
    first, second = evidence
    swapped_body = first.model_dump(mode="json", exclude={"content_hash"})
    swapped_body.update(
        output_ref=second.output_ref,
        output_checksum=second.output_checksum,
        provider_receipt_hash=second.provider_receipt_hash,
        qc_ref=second.qc_ref,
        qc_hash=second.qc_hash,
    )
    evidence[0] = VerifiedAIVisualEffectEvidence(
        **swapped_body, content_hash=ai_visual_stable_hash(swapped_body)
    )
    fixture["effect_evidence"] = evidence

    with pytest.raises(
        AIVisualCrossModalError,
        match="AI_VISUAL_CROSS_MODAL_ASSET_EFFECT_SWAP_DETECTED",
    ):
        ai_visual_cross_modal_qc_report(**fixture)


def test_ai_cross_modal_blocks_swapped_scene_plan_hash(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    manifest = fixture["manifest"]
    first, second = manifest.assets
    swapped_asset = first.model_copy(
        update={"bound_scene_plan_hashes": second.bound_scene_plan_hashes}
    )
    swapped_body = swapped_asset.model_dump(mode="json", exclude={"content_hash"})
    swapped_asset = type(first)(
        **swapped_body, content_hash=ai_visual_stable_hash(swapped_body)
    )
    fixture["manifest"] = build_ai_visual_asset_manifest(
        manifest_id=manifest.manifest_id,
        production_visual_policy_ref=manifest.production_visual_policy_ref,
        production_visual_policy_hash=manifest.production_visual_policy_hash,
        scene_plan_ref=manifest.scene_plan_ref,
        scene_plan_hash=manifest.scene_plan_hash,
        style_bible_ref=manifest.style_bible_ref,
        style_bible_hash=manifest.style_bible_hash,
        motion_grammar_ref=manifest.motion_grammar_ref,
        motion_grammar_hash=manifest.motion_grammar_hash,
        effect_plan_ref=manifest.effect_plan_ref,
        effect_plan_hash=manifest.effect_plan_hash,
        assets=[swapped_asset, second],
    )

    with pytest.raises(
        AIVisualCrossModalError,
        match="AI_VISUAL_CROSS_MODAL_ASSET_EFFECT_SWAP_DETECTED",
    ):
        ai_visual_cross_modal_qc_report(**fixture)


def test_ai_cross_modal_blocks_unrelated_checksum_bound_semantic_attestation(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    evidence = list(fixture["effect_evidence"])
    first = evidence[0]
    unrelated_anchors = [
        "core_subject:unrelated tropical bird",
        "action_or_relation:bird rests on a branch",
        "environment:rainforest canopy",
        "visual_goal:wildlife portrait",
    ]
    unrelated_body = first.model_dump(mode="json", exclude={"content_hash"})
    unrelated_body.update(
        observed_output_summary="A tropical bird resting in a rainforest",
        observed_primary_subjects=["tropical bird"],
        observed_action_or_relation="bird rests on a branch",
        observed_environment="rainforest canopy",
        required_semantic_anchors=unrelated_anchors,
        observed_semantic_anchors=unrelated_anchors,
    )
    evidence[0] = VerifiedAIVisualEffectEvidence(
        **unrelated_body,
        content_hash=ai_visual_stable_hash(unrelated_body),
    )
    fixture["effect_evidence"] = evidence

    with pytest.raises(
        AIVisualCrossModalError,
        match="AI_VISUAL_CROSS_MODAL_SEMANTIC_ATTESTATION_SCENE_MISMATCH",
    ):
        ai_visual_cross_modal_qc_report(**fixture)
