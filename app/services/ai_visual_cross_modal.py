"""Deterministic AI-only cross-modal lineage and scene-binding QC."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.contracts.ai_visual_cross_modal import (
    AIVisualAssetSemanticAttestation,
    AIVisualCrossModalQCReport,
    AIVisualSceneCrossModalBinding,
    VeoTechnicalMotionInspectionEvidence,
    VerifiedAIVisualEffectEvidence,
)
from app.contracts.ai_visual_production import (
    AIVisualPlanCompilation,
    VideoMotionGrammar,
    VideoVisualStyleBible,
    ai_visual_stable_hash,
)
from app.contracts.cross_modal import (
    NarrationUnitCompilation,
    TimedNarrationBindingSet,
)
from app.services.ai_visual_planner import (
    AIImagePromptCompiler,
    AIVideoPromptCompiler,
)
from app.services.native_render_plan import stable_hash as veo_stable_hash
from app.services.production_package import semantic_hash
from app.services.v2_ai_visual_provider import (
    V2AIImageSceneEffectRecord,
    v2_ai_image_required_semantic_anchors,
)
from app.services.v2_ai_visual_renderer import AIVisualAssetManifestProjection
from app.services.v2_veo_visual_provider import V2VeoEffectRecord


class AIVisualCrossModalError(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _require(condition: bool, reason_code: str) -> None:
    if not condition:
        raise AIVisualCrossModalError(reason_code)


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _veo_hashed_journal_valid(payload: Any, hash_key: str) -> bool:
    if not isinstance(payload, dict):
        return False
    expected = payload.get(hash_key)
    body = dict(payload)
    body.pop(hash_key, None)
    return (
        isinstance(expected, str)
        and len(expected) == 64
        and veo_stable_hash(body) == expected
    )


def _veo_record_from_json(raw: dict[str, Any]) -> V2VeoEffectRecord:
    values = dict(raw)
    for field_name in (
        "prepared_at",
        "submitted_at",
        "response_captured_at",
        "completed_at",
    ):
        value = values.get(field_name)
        if value is not None and not isinstance(value, datetime):
            try:
                values[field_name] = datetime.fromisoformat(
                    str(value).replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise AIVisualCrossModalError(
                    "AI_VISUAL_CROSS_MODAL_VIDEO_EFFECT_RECORD_INVALID"
                ) from exc
    for field_name in (
        "actual_cost_usd",
        "conservative_settlement_cost_usd",
        "output_fps",
    ):
        value = values.get(field_name)
        if value is not None and not isinstance(value, Decimal):
            try:
                values[field_name] = Decimal(str(value))
            except (ValueError, ArithmeticError) as exc:
                raise AIVisualCrossModalError(
                    "AI_VISUAL_CROSS_MODAL_VIDEO_EFFECT_RECORD_INVALID"
                ) from exc
    values["response_journals"] = tuple(values.get("response_journals") or ())
    try:
        return V2VeoEffectRecord(**values)
    except (TypeError, ValueError) as exc:
        raise AIVisualCrossModalError(
            "AI_VISUAL_CROSS_MODAL_VIDEO_EFFECT_RECORD_INVALID"
        ) from exc


def _actual_asset_path(*, workspace_root: Path, output_ref: str) -> Path:
    root = workspace_root.resolve()
    raw = Path(output_ref)
    candidate = raw if raw.is_absolute() else root / raw
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise AIVisualCrossModalError(
            "AI_VISUAL_CROSS_MODAL_ACTUAL_ASSET_MISSING"
        ) from exc
    _require(
        root in resolved.parents and resolved.is_file() and not resolved.is_symlink(),
        "AI_VISUAL_CROSS_MODAL_ACTUAL_ASSET_PATH_INVALID",
    )
    cursor = candidate
    while cursor != root and root in cursor.resolve(strict=False).parents:
        _require(
            not cursor.is_symlink(),
            "AI_VISUAL_CROSS_MODAL_ACTUAL_ASSET_SYMLINK_FORBIDDEN",
        )
        cursor = cursor.parent
    return resolved


def verified_ai_visual_effect_evidence(
    asset_effects: Sequence[Any],
) -> tuple[VerifiedAIVisualEffectEvidence, ...]:
    """Normalize typed provider records without trusting manifest projections.

    Gemini image receipts bind a same-interaction semantic description.  Veo
    records bind the exact provider operation, normalized bytes, technical QC,
    and sampled-frame motion.  The latter intentionally carries no semantic
    description or provider semantic-match assertion.
    """

    normalized: list[VerifiedAIVisualEffectEvidence] = []
    for row in asset_effects:
        _require(
            getattr(row, "state", None) == "VERIFIED",
            "AI_VISUAL_CROSS_MODAL_EFFECT_NOT_VERIFIED",
        )
        raw_evidence = getattr(row, "qc_evidence", None)
        route = getattr(row, "route", None)
        if route == "AI_VIDEO":
            _require(
                isinstance(raw_evidence, dict)
                and raw_evidence.get("schema_version")
                == "vcos.ai-visual-veo-db-record.v1"
                and isinstance(raw_evidence.get("record"), dict)
                and isinstance(raw_evidence.get("technical_motion_evidence"), dict),
                "AI_VISUAL_CROSS_MODAL_VIDEO_EFFECT_EVIDENCE_INVALID",
            )
            raw_record = raw_evidence["record"]
            record = _veo_record_from_json(raw_record)
            try:
                technical = VeoTechnicalMotionInspectionEvidence.model_validate(
                    raw_evidence["technical_motion_evidence"]
                )
            except ValueError as exc:
                raise AIVisualCrossModalError(
                    "AI_VISUAL_CROSS_MODAL_VIDEO_TECHNICAL_MOTION_EVIDENCE_INVALID"
                ) from exc
            authority = dict(record.authority)
            request_journal = dict(record.request_journal)
            provider_request = request_journal.get("provider_request")
            qc_receipt = dict(record.qc_receipt)
            qc_checks = qc_receipt.get("checks")
            normalization = dict(record.normalization_receipt)
            response_journals = [dict(item) for item in record.response_journals]
            prompt = (
                provider_request.get("prompt")
                if isinstance(provider_request, dict)
                else None
            )
            sampled_frame_sha256 = (
                qc_checks.get("sampled_frame_sha256")
                if isinstance(qc_checks, dict)
                else None
            )
            record_envelope = {
                "schema_version": "vcos.ai-visual-veo-db-record.v1",
                "record": raw_record,
            }
            _require(
                record.state == "VERIFIED"
                and record.production_eligible is True
                and record.generation_attempt_count == 1
                and record.asset_effect_id == str(row.id)
                and record.identity_hash == row.effect_identity_hash
                and record.request_hash == row.request_hash
                and raw_evidence.get("record_hash") == semantic_hash(record_envelope)
                and getattr(row, "provider_key", None) == "google_veo"
                and authority.get("provider") == "google_veo"
                and authority.get("asset_effect_id") == str(row.id)
                and authority.get("asset_slot_id") == row.asset_slot_id
                and authority.get("primary_asset_owner_scene_id")
                == row.primary_asset_owner_scene_id
                and authority.get("scene_id") == row.scene_id
                and list(authority.get("bound_scene_ids") or []) == row.bound_scene_ids
                and list(authority.get("bound_scene_plan_hashes") or [])
                == row.bound_scene_plan_hashes
                and authority.get("scene_plan_hash") == row.scene_plan_hash
                and authority.get("compiled_prompt_hash") == row.compiled_prompt_hash
                and authority.get("compiled_prompt_content_hash")
                == row.compiled_prompt_content_hash
                and authority.get("prompt_hash") == row.prompt_hash
                and authority.get("model_id") == row.model_id
                and request_journal.get("schema_version")
                == "vcos.v2-veo-request-journal.v1"
                and request_journal.get("asset_effect_id") == str(row.id)
                and request_journal.get("route") == "AI_VIDEO"
                and request_journal.get("provider") == "google_veo"
                and request_journal.get("identity_hash") == record.identity_hash
                and request_journal.get("request_hash") == record.request_hash
                and request_journal.get("authority") == authority
                and _veo_hashed_journal_valid(request_journal, "journal_hash")
                and isinstance(provider_request, dict)
                and provider_request.get("model") == row.model_id
                and isinstance(prompt, str)
                and bool(prompt)
                and hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                == row.prompt_hash
                and record.request_hash
                == veo_stable_hash(
                    {
                        "identity_hash": record.identity_hash,
                        "provider_request": provider_request,
                    }
                )
                and isinstance(record.provider_operation_id, str)
                and bool(record.provider_operation_id)
                and record.provider_operation_id == row.provider_operation_id
                and bool(response_journals)
                and all(
                    _veo_hashed_journal_valid(item, "journal_hash")
                    and (
                        item.get("provider_operation_id")
                        in {None, record.provider_operation_id}
                    )
                    and item.get("request_hash") == record.request_hash
                    for item in response_journals
                )
                and any(
                    item.get("provider_operation_id") == record.provider_operation_id
                    for item in response_journals
                )
                and record.normalized_output_ref == row.output_ref
                and record.normalized_output_sha256 == row.output_checksum
                and record.normalized_output_size_bytes == row.output_size_bytes
                and record.output_content_type == row.output_content_type
                and record.output_width == row.output_width
                and record.output_height == row.output_height
                and record.output_duration_ms == row.output_duration_ms
                and record.output_fps == row.output_fps
                and record.output_audio_stream_count == 0
                and row.output_audio_stream_count == 0
                and _veo_hashed_journal_valid(normalization, "normalization_hash")
                and normalization.get("output_sha256") == row.output_checksum
                and normalization.get("output_audio_stream_count") == 0
                and normalization.get("provider_audio_discarded") is True
                and _veo_hashed_journal_valid(qc_receipt, "qc_hash")
                and raw_evidence.get("technical_qc") == qc_receipt
                and qc_receipt.get("result") == "PASS"
                and qc_receipt.get("asset_sha256") == row.output_checksum
                and qc_receipt.get("provider_provenance_valid") is True
                and qc_receipt.get("scene_binding_valid") is True
                and qc_receipt.get("provider_audio_authority") is False
                and isinstance(qc_checks, dict)
                and qc_checks.get("sampled_frames_valid") is True
                and qc_checks.get("not_blank") is True
                and qc_checks.get("mostly_black_absent") is True
                and qc_checks.get("not_frozen_throughout") is True
                and isinstance(sampled_frame_sha256, list)
                and len(sampled_frame_sha256) >= 3
                and len(set(sampled_frame_sha256)) >= 2
                and list(sampled_frame_sha256) == technical.sampled_frame_sha256
                and row.qc_ref == technical.qc_ref
                and row.qc_hash == technical.qc_hash == qc_receipt.get("qc_hash")
                and technical.asset_effect_id == str(row.id)
                and technical.asset_effect_identity_hash == row.effect_identity_hash
                and technical.provider_request_hash == row.request_hash
                and technical.asset_slot_id == row.asset_slot_id
                and technical.primary_asset_owner_scene_id
                == row.primary_asset_owner_scene_id
                and technical.bound_scene_ids == row.bound_scene_ids
                and technical.bound_scene_plan_hashes == row.bound_scene_plan_hashes
                and technical.scene_plan_hash == row.scene_plan_hash
                and technical.compiled_prompt_hash == row.compiled_prompt_hash
                and technical.prompt_hash == row.prompt_hash
                and technical.model_id == row.model_id
                and technical.provider_operation_id == record.provider_operation_id
                and technical.output_ref == row.output_ref
                and technical.output_checksum == row.output_checksum
                and technical.content_hash
                == getattr(row, "asset_receipt_hash", technical.content_hash),
                "AI_VISUAL_CROSS_MODAL_VIDEO_PROVIDER_RECEIPT_BINDING_MISMATCH",
            )
            required_anchors = list(technical.required_semantic_anchors)
            # Veo authority binds the intended scene via hashes/prompt.  These
            # anchors are intentions, never observations of the returned video.
            _require(
                len(required_anchors) == 4,
                "AI_VISUAL_CROSS_MODAL_VIDEO_SCENE_INTENT_INCOMPLETE",
            )
            normalized.append(
                VerifiedAIVisualEffectEvidence.build(
                    asset_slot_id=row.asset_slot_id,
                    primary_asset_owner_scene_id=row.primary_asset_owner_scene_id,
                    bound_scene_ids=list(row.bound_scene_ids),
                    bound_scene_plan_hashes=list(row.bound_scene_plan_hashes),
                    route="AI_VIDEO",
                    provider_key="google_veo",
                    model_id=row.model_id,
                    asset_effect_identity_hash=row.effect_identity_hash,
                    provider_request_hash=row.request_hash,
                    provider_operation_id=record.provider_operation_id,
                    compiled_prompt_hash=row.compiled_prompt_hash,
                    prompt=prompt,
                    prompt_hash=row.prompt_hash,
                    output_ref=row.output_ref,
                    output_checksum=row.output_checksum,
                    qc_ref=row.qc_ref,
                    qc_hash=row.qc_hash,
                    provider_receipt_hash=technical.content_hash,
                    asset_inspection_evidence_hash=technical.content_hash,
                    required_semantic_anchors=required_anchors,
                    sampled_frame_sha256=list(sampled_frame_sha256),
                    durable_effect_evidence_hash=ai_visual_stable_hash(raw_evidence),
                )
            )
            continue
        _require(
            route == "AI_IMAGE",
            "AI_VISUAL_CROSS_MODAL_EFFECT_ROUTE_INVALID",
        )
        _require(
            isinstance(raw_evidence, dict)
            and raw_evidence.get("schema_version")
            == "vcos.ai-visual-image-db-record.v1"
            and isinstance(raw_evidence.get("record"), dict),
            "AI_VISUAL_CROSS_MODAL_EFFECT_EVIDENCE_INVALID",
        )
        try:
            record = V2AIImageSceneEffectRecord.model_validate(raw_evidence["record"])
        except ValueError as exc:
            raise AIVisualCrossModalError(
                "AI_VISUAL_CROSS_MODAL_EFFECT_RECORD_INVALID"
            ) from exc
        identity = record.identity
        receipt = record.asset_receipt
        semantic_attestation = (
            getattr(receipt, "semantic_attestation", None)
            if receipt is not None
            else None
        )
        _require(
            record.state.value == "VERIFIED"
            and receipt is not None
            and identity.effect_id == str(row.id)
            and identity.effect_identity_hash == row.effect_identity_hash
            and identity.primary_asset_slot_id == row.asset_slot_id
            and identity.primary_asset_owner_scene_id
            == row.primary_asset_owner_scene_id
            and list(identity.bound_scene_ids) == row.bound_scene_ids
            and list(identity.bound_scene_plan_hashes) == row.bound_scene_plan_hashes
            and identity.compiled_prompt_hash == row.compiled_prompt_hash
            and identity.prompt_hash == row.prompt_hash
            and receipt.receipt_hash
            and receipt.receipt_hash == record.asset_receipt.receipt_hash
            and receipt.checksum_sha256 == row.output_checksum
            and receipt.local_ref == row.output_ref
            and receipt.qc_ref == row.qc_ref
            and receipt.qc_hash == row.qc_hash
            and semantic_attestation is not None
            and semantic_attestation.effect_id == identity.effect_id
            and semantic_attestation.scene_id == identity.scene_id
            and semantic_attestation.scene_plan_hash == identity.scene_plan_hash
            and semantic_attestation.prompt_hash == identity.prompt_hash
            and semantic_attestation.asset_checksum == receipt.checksum_sha256
            and semantic_attestation.required_semantic_anchors
            == identity.required_semantic_anchors
            and semantic_attestation.observed_semantic_anchors
            == identity.required_semantic_anchors
            and semantic_attestation.attestation_source
            == "SAME_INTERACTION_MODEL_OUTPUT"
            and semantic_attestation.semantic_match is True
            and not semantic_attestation.semantic_mismatch_reasons
            and not semantic_attestation.forbidden_content_detected
            and semantic_attestation.model_asserts_description_is_of_generated_output
            is True
            and semantic_attestation.independent_multimodal_inspection_performed
            is False
            and semantic_attestation.human_semantic_review_required is True,
            "AI_VISUAL_CROSS_MODAL_PROVIDER_RECEIPT_BINDING_MISMATCH",
        )
        normalized.append(
            VerifiedAIVisualEffectEvidence.build(
                asset_slot_id=identity.primary_asset_slot_id,
                primary_asset_owner_scene_id=(identity.primary_asset_owner_scene_id),
                bound_scene_ids=list(identity.bound_scene_ids),
                bound_scene_plan_hashes=list(identity.bound_scene_plan_hashes),
                route="AI_IMAGE",
                provider_key=identity.provider_key,
                model_id=identity.model_id,
                asset_effect_identity_hash=identity.effect_identity_hash,
                compiled_prompt_hash=identity.compiled_prompt_hash,
                prompt=identity.prompt,
                prompt_hash=identity.prompt_hash,
                output_ref=receipt.local_ref,
                output_checksum=receipt.checksum_sha256,
                qc_ref=receipt.qc_ref,
                qc_hash=receipt.qc_hash,
                provider_receipt_hash=receipt.receipt_hash,
                asset_semantic_attestation_hash=(semantic_attestation.attestation_hash),
                asset_inspection_evidence_hash=(semantic_attestation.attestation_hash),
                actual_asset_description_source=(
                    semantic_attestation.attestation_source
                ),
                observed_output_summary=(semantic_attestation.observed_output_summary),
                observed_primary_subjects=list(
                    semantic_attestation.observed_primary_subjects
                ),
                observed_action_or_relation=(
                    semantic_attestation.observed_action_or_relation
                ),
                observed_environment=semantic_attestation.observed_environment,
                required_semantic_anchors=list(
                    semantic_attestation.required_semantic_anchors
                ),
                observed_semantic_anchors=list(
                    semantic_attestation.observed_semantic_anchors
                ),
                provider_text_hash=semantic_attestation.provider_text_hash,
                provider_semantic_match_asserted=True,
                provider_semantic_mismatch_reasons=[],
                provider_forbidden_content_detected=[],
                provider_forbidden_content_inspection_performed=True,
                actual_asset_semantic_inspection_performed=True,
                same_interaction_model_output_semantic_inspection_performed=True,
                technical_asset_inspection_performed=True,
                sampled_frame_sha256=[],
                motion_inspection_performed=False,
                independent_multimodal_inspection_performed=False,
                human_semantic_review_required=True,
                durable_effect_evidence_hash=ai_visual_stable_hash(raw_evidence),
            )
        )
    _require(
        len(normalized) == len(asset_effects)
        and len({item.asset_slot_id for item in normalized}) == len(normalized),
        "AI_VISUAL_CROSS_MODAL_EFFECT_EVIDENCE_DUPLICATE",
    )
    return tuple(normalized)


def ai_visual_cross_modal_qc_report(
    *,
    timeline: dict[str, Any],
    timeline_hash: str,
    scene_plan: AIVisualPlanCompilation,
    scene_plan_artifact_hash: str,
    style_bible: VideoVisualStyleBible,
    motion_grammar: VideoMotionGrammar,
    manifest: AIVisualAssetManifestProjection,
    effect_evidence: Sequence[VerifiedAIVisualEffectEvidence],
    workspace_root: Path,
) -> AIVisualCrossModalQCReport:
    """Compile a PASS only when all deterministic cross-modal lineage is exact.

    The returned PASS is explicitly scoped to lineage and scene binding.  It
    cannot be interpreted as an automated judgment that the pixels actually
    depict the requested meaning.
    """

    _require(
        ai_visual_stable_hash(timeline) == timeline_hash,
        "AI_VISUAL_CROSS_MODAL_TIMELINE_HASH_MISMATCH",
    )
    required = {
        "narration_unit_compilation",
        "narration_unit_compilation_hash",
        "timed_narration_unit_bindings",
        "timed_narration_unit_bindings_hash",
    }
    _require(
        all(timeline.get(key) is not None for key in required),
        "AI_VISUAL_CROSS_MODAL_NARRATION_LINEAGE_INCOMPLETE",
    )
    try:
        compilation = NarrationUnitCompilation.model_validate(
            timeline["narration_unit_compilation"]
        )
        bindings = TimedNarrationBindingSet.model_validate(
            timeline["timed_narration_unit_bindings"]
        )
    except ValueError as exc:
        raise AIVisualCrossModalError(
            "AI_VISUAL_CROSS_MODAL_NARRATION_LINEAGE_INVALID"
        ) from exc
    _require(
        timeline["narration_unit_compilation_hash"] == compilation.content_hash
        and timeline["timed_narration_unit_bindings_hash"] == bindings.content_hash
        and bindings.narration_unit_compilation_hash == compilation.content_hash,
        "AI_VISUAL_CROSS_MODAL_NARRATION_LINEAGE_HASH_MISMATCH",
    )
    units_by_id = {item.narration_unit_id: item for item in compilation.narration_units}
    timings_by_id = {item.narration_unit_id: item for item in bindings.bindings}
    _require(
        set(units_by_id) == set(timings_by_id),
        "AI_VISUAL_CROSS_MODAL_NARRATION_TIMING_COVERAGE_MISMATCH",
    )

    scenes_by_id = {item.scene_id: item for item in scene_plan.scenes}
    _require(
        scene_plan.style_bible_hash == style_bible.content_hash
        and motion_grammar.style_bible_hash == style_bible.content_hash
        and scene_plan.canonical_duration_ms == int(timeline.get("duration_ms") or 0)
        and manifest.scene_plan_hash == scene_plan_artifact_hash
        and manifest.style_bible_hash == style_bible.content_hash
        and manifest.motion_grammar_hash == motion_grammar.content_hash
        and manifest.scene_count == len(scene_plan.scenes),
        "AI_VISUAL_CROSS_MODAL_PLAN_AUTHORITY_MISMATCH",
    )

    effects_by_identity = {
        item.asset_effect_identity_hash: item for item in effect_evidence
    }
    _require(
        len(effects_by_identity) == len(effect_evidence) == manifest.asset_count,
        "AI_VISUAL_CROSS_MODAL_EFFECT_MANIFEST_COUNT_MISMATCH",
    )
    asset_by_scene: dict[str, Any] = {}
    attestation_by_slot: dict[str, AIVisualAssetSemanticAttestation] = {}
    image_compiler = AIImagePromptCompiler()
    video_compiler = AIVideoPromptCompiler()
    for asset in manifest.assets:
        evidence = effects_by_identity.get(asset.asset_effect_identity_hash)
        _require(
            evidence is not None
            and evidence.asset_slot_id == asset.asset_slot_id
            and evidence.primary_asset_owner_scene_id
            == asset.primary_asset_owner_scene_id
            and evidence.bound_scene_ids == asset.bound_scene_ids
            and evidence.bound_scene_plan_hashes == asset.bound_scene_plan_hashes
            and evidence.route == asset.route
            and evidence.provider_key == asset.provider_key
            and evidence.model_id == asset.model_id
            and evidence.output_ref == asset.output_ref
            and evidence.output_checksum == asset.output_checksum
            and evidence.qc_ref == asset.qc_ref
            and evidence.qc_hash == asset.qc_hash
            and evidence.provider_receipt_hash == asset.asset_receipt_hash,
            "AI_VISUAL_CROSS_MODAL_ASSET_EFFECT_SWAP_DETECTED",
        )
        expected_scene_hashes = [
            scenes_by_id[scene_id].content_hash
            for scene_id in asset.bound_scene_ids
            if scene_id in scenes_by_id
        ]
        _require(
            len(expected_scene_hashes) == len(asset.bound_scene_ids)
            and expected_scene_hashes == asset.bound_scene_plan_hashes,
            "AI_VISUAL_CROSS_MODAL_ASSET_SCENE_SWAP_DETECTED",
        )
        owner = scenes_by_id.get(asset.primary_asset_owner_scene_id)
        _require(
            owner is not None
            and owner.reuses_primary_asset_from_scene_id is None
            and owner.primary_asset_slot_id == asset.asset_slot_id
            and owner.production_route == asset.route,
            "AI_VISUAL_CROSS_MODAL_ASSET_OWNER_MISMATCH",
        )
        if asset.route == "AI_IMAGE":
            expected_prompt = image_compiler.compile(
                scene_plan=owner,
                style_bible=style_bible,
                motion_grammar=motion_grammar,
            )
        else:
            expected_prompt = video_compiler.compile(
                scene_plan=owner,
                style_bible=style_bible,
            )
        _require(
            evidence.prompt == expected_prompt.prompt
            and evidence.prompt_hash == expected_prompt.prompt_hash
            and evidence.compiled_prompt_hash == expected_prompt.content_hash,
            "AI_VISUAL_CROSS_MODAL_GENERATION_PLAN_MISMATCH",
        )
        expected_semantic_anchors = list(v2_ai_image_required_semantic_anchors(owner))
        if asset.route == "AI_IMAGE":
            _require(
                evidence.required_semantic_anchors == expected_semantic_anchors
                and evidence.observed_semantic_anchors == expected_semantic_anchors,
                "AI_VISUAL_CROSS_MODAL_SEMANTIC_ATTESTATION_SCENE_MISMATCH",
            )
        else:
            _require(
                evidence.required_semantic_anchors == expected_semantic_anchors
                and not evidence.observed_semantic_anchors
                and evidence.actual_asset_semantic_inspection_performed is False
                and evidence.provider_semantic_match_asserted is False
                and evidence.motion_inspection_performed is True,
                "AI_VISUAL_CROSS_MODAL_VIDEO_INTENT_BINDING_MISMATCH",
            )
        actual_path = _actual_asset_path(
            workspace_root=workspace_root,
            output_ref=asset.output_ref,
        )
        _require(
            _sha256_file(actual_path) == asset.output_checksum
            and asset.primary_asset_hash == asset.output_checksum,
            "AI_VISUAL_CROSS_MODAL_ACTUAL_ASSET_CHECKSUM_MISMATCH",
        )
        for scene_id in asset.bound_scene_ids:
            _require(
                scene_id not in asset_by_scene,
                "AI_VISUAL_CROSS_MODAL_SCENE_ASSET_DUPLICATE",
            )
            asset_by_scene[scene_id] = asset
        attestation_by_slot[asset.asset_slot_id] = (
            AIVisualAssetSemanticAttestation.build(
                asset_slot_id=asset.asset_slot_id,
                primary_asset_owner_scene_id=asset.primary_asset_owner_scene_id,
                bound_scene_ids=asset.bound_scene_ids,
                bound_scene_plan_hashes=asset.bound_scene_plan_hashes,
                route=asset.route,
                provider_key=asset.provider_key,
                model_id=asset.model_id,
                owner_scene_meaning=owner.scene_meaning,
                generation_prompt=evidence.prompt,
                generation_prompt_hash=evidence.prompt_hash,
                provider_request_hash=evidence.provider_request_hash,
                provider_operation_id=evidence.provider_operation_id,
                compiled_prompt_hash=evidence.compiled_prompt_hash,
                asset_ref=asset.output_ref,
                asset_checksum=asset.output_checksum,
                asset_effect_identity_hash=asset.asset_effect_identity_hash,
                provider_receipt_hash=evidence.provider_receipt_hash,
                provider_qc_hash=evidence.qc_hash,
                asset_inspection_evidence_hash=(
                    evidence.asset_inspection_evidence_hash
                ),
                asset_semantic_attestation_hash=(
                    evidence.asset_semantic_attestation_hash
                ),
                asset_inspection_scope=evidence.asset_inspection_scope,
                actual_asset_description_source=(
                    evidence.actual_asset_description_source
                ),
                observed_output_summary=evidence.observed_output_summary,
                observed_primary_subjects=evidence.observed_primary_subjects,
                observed_action_or_relation=evidence.observed_action_or_relation,
                observed_environment=evidence.observed_environment,
                required_semantic_anchors=evidence.required_semantic_anchors,
                observed_semantic_anchors=evidence.observed_semantic_anchors,
                provider_text_hash=evidence.provider_text_hash,
                provider_semantic_match_asserted=(
                    evidence.provider_semantic_match_asserted
                ),
                provider_semantic_mismatch_reasons=(
                    evidence.provider_semantic_mismatch_reasons
                ),
                provider_forbidden_content_detected=(
                    evidence.provider_forbidden_content_detected
                ),
                provider_forbidden_content_inspection_performed=(
                    evidence.provider_forbidden_content_inspection_performed
                ),
                sampled_frame_sha256=evidence.sampled_frame_sha256,
                motion_inspection_performed=evidence.motion_inspection_performed,
                actual_asset_semantic_inspection_performed=(
                    evidence.actual_asset_semantic_inspection_performed
                ),
                same_interaction_model_output_semantic_inspection_performed=(
                    evidence.same_interaction_model_output_semantic_inspection_performed
                ),
                actual_asset_semantic_disposition=(
                    evidence.actual_asset_semantic_disposition
                ),
            )
        )

    _require(
        set(asset_by_scene) == set(scenes_by_id),
        "AI_VISUAL_CROSS_MODAL_SCENE_ASSET_COVERAGE_MISMATCH",
    )
    scene_bindings: list[AIVisualSceneCrossModalBinding] = []
    for scene in scene_plan.scenes:
        asset = asset_by_scene[scene.scene_id]
        owner = scenes_by_id[asset.primary_asset_owner_scene_id]
        if scene.scene_id == owner.scene_id:
            _require(
                scene.reuses_primary_asset_from_scene_id is None,
                "AI_VISUAL_CROSS_MODAL_OWNER_REUSE_INVALID",
            )
        else:
            _require(
                scene.reuses_primary_asset_from_scene_id == owner.scene_id
                and scene.primary_asset_slot_id == owner.primary_asset_slot_id
                and scene.production_route == owner.production_route
                and scene.core_subject == owner.core_subject
                and scene.visual_function == owner.visual_function
                and scene.environment == owner.environment
                and scene.visual_goal == owner.visual_goal,
                "AI_VISUAL_CROSS_MODAL_UNRELATED_ASSET_REUSE",
            )
        _require(
            all(item in units_by_id for item in scene.narration_unit_ids),
            "AI_VISUAL_CROSS_MODAL_UNKNOWN_NARRATION_UNIT",
        )
        units = [units_by_id[item] for item in scene.narration_unit_ids]
        timings = [timings_by_id[item] for item in scene.narration_unit_ids]
        information_ids = _dedupe(
            [value for unit in units for value in unit.information_unit_ids]
        )
        semantic_intents = _dedupe([unit.semantic_intent for unit in units])
        expected_meaning = " ".join(semantic_intents)
        _require(
            scene.information_unit_ids == information_ids,
            "AI_VISUAL_CROSS_MODAL_INFORMATION_UNIT_SWAP_DETECTED",
        )
        _require(
            scene.scene_meaning == expected_meaning
            or scene.scene_meaning.startswith(
                expected_meaning + " Complementary beat "
            ),
            "AI_VISUAL_CROSS_MODAL_NARRATION_MEANING_MISMATCH",
        )
        _require(
            all(
                scene.actual_start_ms < timing.actual_end_ms
                and scene.actual_end_ms > timing.actual_start_ms
                for timing in timings
            ),
            "AI_VISUAL_CROSS_MODAL_NARRATION_TIMING_MISMATCH",
        )
        attestation = attestation_by_slot[asset.asset_slot_id]
        scene_bindings.append(
            AIVisualSceneCrossModalBinding.build(
                scene_id=scene.scene_id,
                ordinal=scene.ordinal,
                narration_unit_ids=scene.narration_unit_ids,
                information_unit_ids=scene.information_unit_ids,
                narration_semantic_intents=semantic_intents,
                scene_meaning=scene.scene_meaning,
                scene_plan_hash=scene.content_hash,
                presentation_start_ms=scene.presentation_start_ms,
                presentation_end_ms=scene.presentation_end_ms,
                asset_slot_id=asset.asset_slot_id,
                generation_owner_scene_id=owner.scene_id,
                asset_route=asset.route,
                asset_inspection_scope=attestation.asset_inspection_scope,
                asset_checksum=asset.output_checksum,
                asset_attestation_hash=attestation.content_hash,
                motion_inspection_performed=(attestation.motion_inspection_performed),
                actual_asset_semantic_inspection_performed=(
                    attestation.actual_asset_semantic_inspection_performed
                ),
            )
        )

    ordered_attestations = [
        attestation_by_slot[asset.asset_slot_id] for asset in manifest.assets
    ]
    return AIVisualCrossModalQCReport.build(
        canonical_timeline_hash=timeline_hash,
        ai_visual_scene_plan_artifact_hash=scene_plan_artifact_hash,
        ai_visual_scene_plan_compilation_hash=scene_plan.content_hash,
        asset_manifest_hash=manifest.content_hash,
        verified_effect_evidence_set_hash=ai_visual_stable_hash(
            [item.content_hash for item in effect_evidence]
        ),
        asset_attestations=ordered_attestations,
        scene_bindings=scene_bindings,
    )


__all__ = [
    "AIVisualCrossModalError",
    "ai_visual_cross_modal_qc_report",
    "verified_ai_visual_effect_evidence",
]
