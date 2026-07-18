from __future__ import annotations

import hashlib
import subprocess
import struct
import zlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.creative_quality_canary import CreativeGateEvidence
from app.contracts.image_visual_quality_control import (
    GeneratedArtifactInspectionEvidence,
    GeneratedArtifactRegion,
    HUMAN_VISUAL_REVIEW_DIMENSIONS,
    HumanVisualReviewEvidence,
    ImageVisualQualityControlInput,
    NativeOverlayInputs,
    NormalizedImageRegion,
    PendingHumanVisualChecklistItem,
    ReuseSimilarityEvidence,
    RightsDisclosureEvidence,
    StructuredVisualReviewEvidence,
    VQC1_REQUIRED_GATES,
    VQC1ImageMaterializationEvidence,
    VQC1ImageNormalizationEvidence,
)
from app.contracts.google_gemini_image import GeminiImageOutputMaterializationPlan
from app.contracts.img_canary import (
    IMGCanaryAttemptLedger,
    IMGCanaryProviderResponseSummary,
)
from app.contracts.native_renderer import TextSafeRegion
from app.core.config import Settings
from app.providers.google_gemini_image import (
    GeminiImageTransientOutput,
    GoogleGeminiImageAdapter,
    build_fixture_png,
)
from app.services.image_visual_quality_control import (
    ImageTechnicalProbe,
    ImageVisualQualityControlService,
)
from app.services.img_canary import IMGCanaryImageNormalizer, IMGCanaryPlanBuilder
from app.services.native_ffmpeg_renderer import FFMPEG_FULL_DEFAULT


REPO_ROOT = Path(__file__).resolve().parents[1]
T = TypeVar("T", bound=BaseModel)


def _bound(model_cls: type[T], **payload: Any) -> T:
    return model_cls(**payload, content_hash=ai_image_stable_hash(payload))


def _rebuild(model: T, **updates: Any) -> T:
    payload = model.model_dump(mode="python", exclude={"content_hash"})
    payload.update(updates)
    return type(model)(**payload, content_hash=ai_image_stable_hash(payload))


def _rgba_png(width: int = 2560, height: int = 1440) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + bytes((31, 52, 73, 128)) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height, level=9))
        + chunk(b"IEND", b"")
    )


def _artifact_region(
    kind: str,
    *,
    repairability: str = "NOT_REPAIRABLE",
) -> GeneratedArtifactRegion:
    return GeneratedArtifactRegion(
        region=NormalizedImageRegion(
            region_id=f"artifact-{kind.lower()}",
            region_role="SUSPECTED_ARTIFACT",
            x=0.12,
            y=0.14,
            width=0.08,
            height=0.05,
        ),
        artifact_kind=kind,
        assessment_state="DETECTED",
        confidence=0.96,
        representative_crop_ref=f"fixture://vqc1/crops/{kind.lower()}",
        repairability=repairability,
        review_notes=f"Golden negative fixture for {kind}.",
    )


def _input_for(
    tmp_path: Path,
    *,
    image_bytes: bytes | None = None,
    width: int = 2560,
    height: int = 1440,
    artifact_regions: list[GeneratedArtifactRegion] | None = None,
    inspection_state: str = "ASSESSED",
    inspection_authority: str = "GOLDEN_FIXTURE",
    intended_crop: NormalizedImageRegion | None = None,
    focal_region: NormalizedImageRegion | None = None,
    protected_regions: list[NormalizedImageRegion] | None = None,
    overlay_region: NormalizedImageRegion | None = None,
    reserved_overlay_regions: list[TextSafeRegion] | None = None,
    expected_alpha_behavior: str = "NONE",
    comparison_hashes: list[str] | None = None,
    comparison_refs: list[str] | None = None,
    prior_use_count: int = 0,
) -> tuple[Path, ImageVisualQualityControlInput]:
    data = image_bytes if image_bytes is not None else build_fixture_png(width, height)
    image_path = tmp_path / "vqc1-source.png"
    image_path.write_bytes(data)
    checksum = hashlib.sha256(data).hexdigest()

    intended_crop = intended_crop or NormalizedImageRegion(
        region_id="crop-16x9",
        region_role="INTENDED_CROP",
        x=0.0,
        y=0.0,
        width=1.0,
        height=1.0,
    )
    focal_region = focal_region or NormalizedImageRegion(
        region_id="subject-focal",
        region_role="SUBJECT_FOCAL",
        x=0.62,
        y=0.25,
        width=0.22,
        height=0.35,
    )
    protected_regions = protected_regions if protected_regions is not None else [
        NormalizedImageRegion(
            region_id="protected-subject",
            region_role="PROTECTED_VISUAL",
            x=0.58,
            y=0.18,
            width=0.30,
            height=0.52,
        )
    ]
    overlay_region = overlay_region or NormalizedImageRegion(
        region_id="headline-overlay",
        region_role="NATIVE_OVERLAY",
        x=0.08,
        y=0.12,
        width=0.36,
        height=0.16,
    )
    safe_region = TextSafeRegion(
        id="headline-safe",
        x=0.06,
        y=0.08,
        width=0.44,
        height=0.24,
        purpose="Native authoritative headline",
        minimum_contrast_requirement=4.5,
        alignment="left",
    )
    artifact_regions = artifact_regions or []
    artifact_payload = {
        "image_sha256": checksum,
        "inspection_state": inspection_state,
        "inspection_authority": inspection_authority,
        "detected_or_suspected_regions": artifact_regions,
        "representative_crop_refs": ["fixture://vqc1/crops/full-frame"],
        "review_notes": "Checksum-bound golden artifact inspection.",
    }
    artifact = _bound(GeneratedArtifactInspectionEvidence, **artifact_payload)

    comparison_hashes = comparison_hashes if comparison_hashes is not None else ["f" * 64]
    comparison_refs = comparison_refs if comparison_refs is not None else [
        "fixture://vqc1/comparison/known-clean"
    ]
    reuse_payload = {
        "image_sha256": checksum,
        "comparison_method": "SHA256_EXACT",
        "comparison_asset_refs": comparison_refs,
        "comparison_asset_sha256": comparison_hashes,
        "prior_use_count": prior_use_count,
        "isolated_canary_scope": True,
        "perceptual_hash_available": False,
    }
    reuse = _bound(ReuseSimilarityEvidence, **reuse_payload)

    overlay_payload = {
        "generated_image_sha256": checksum,
        "native_overlay_plan_ref": "overlay-plan://vqc1/headline",
        "native_overlay_plan_hash": "a" * 64,
        "native_overlay_binding_ref": "overlay-binding://vqc1/headline",
        "native_overlay_binding_hash": "b" * 64,
        "authoritative_text_ref": "copy://vqc1/headline",
        "authoritative_text": "Information is everywhere. Context is nowhere.",
        "exact_text_native_authority": True,
        "generated_image_owns_final_text": False,
        "overlay_region": overlay_region,
        "foreground_relative_luminance": 1.0,
        "background_relative_luminance": 0.0,
        "minimum_contrast_ratio": 4.5,
        "font_size_px": 64,
        "minimum_readable_font_size_px": 44,
        "text_fits_without_shrinking": True,
    }
    overlay = _bound(NativeOverlayInputs, **overlay_payload)

    visual_payload = {
        "image_sha256": checksum,
        "review_state": "PENDING",
        "scene_meaning": (
            "Information is fragmented across disconnected locations and nobody sees the whole picture."
        ),
        "intended_metaphor": "Separated knowledge islands with one coherent focal composition.",
        "required_composition": ["one focal system", "clean headline negative space"],
        "forbidden_interpretations": ["software UI", "science-fiction magic"],
        "channel_visual_language": ["clean editorial geometry", "restrained business explainer"],
        "observed_output_summary": "Golden technical fixture; creative judgment remains pending.",
        "semantic_concerns": [],
        "style_concerns": [],
        "continuity_concerns": [],
        "isolated_canary_scope": True,
        "adjacent_scene_refs": [],
        "semantic_pass_from_metadata_allowed": False,
        "visual_language_pass_from_metadata_allowed": False,
    }
    visual_review = _bound(StructuredVisualReviewEvidence, **visual_payload)

    human_payload = {
        "image_sha256": checksum,
        "review_state": "PENDING",
        "reviewer": None,
        "final_decision": None,
        "checklist": [
            PendingHumanVisualChecklistItem(dimension=dimension)
            for dimension in HUMAN_VISUAL_REVIEW_DIMENSIONS
        ],
        "human_final_approval_auto_passed": False,
    }
    human = _bound(HumanVisualReviewEvidence, **human_payload)

    rights_payload = {
        "provider": "google_gemini_image",
        "vendor": "google",
        "model": "gemini-3.1-flash-image",
        "request_hash": "c" * 64,
        "prompt_hash": "d" * 64,
        "reference_asset_refs": [],
        "reference_asset_rights_refs": [],
        "generation_timestamp": datetime(2026, 7, 18, tzinfo=UTC),
        "provider_request_id": "fixture-request-vqc1",
        "provider_operation_id": "fixture-operation-vqc1",
        "output_checksum": checksum,
        "output_width": width,
        "output_height": height,
        "cost_estimate_ref": "cost://vqc1/fixture",
        "cost_estimate_hash": "e" * 64,
        "estimated_cost_usd": Decimal("0.101"),
        "actual_usage_ref": None,
        "actual_cost_usd": None,
        "approval_ref": "approval://vqc1/offline-fixture",
        "approval_hash": "1" * 64,
        "attempt_ref": "attempt://vqc1/zero-paid-attempts",
        "attempt_hash": "2" * 64,
        "generation_attempts_consumed": 0,
        "idempotency_key": "vqc1-fixture-idempotency",
        "scene_usage_refs": ["scene://vqc1/fragmented-knowledge"],
        "native_overlay_binding_ref": overlay.native_overlay_binding_ref,
        "native_overlay_binding_hash": overlay.native_overlay_binding_hash,
        "synthetic_media_disclosure_ref": "disclosure://vqc1/synthetic-media",
        "generated_evidence_authority": False,
        "provider_call_made": False,
    }
    rights = _bound(RightsDisclosureEvidence, **rights_payload)

    input_payload = {
        "run_id": "vqc1-offline-golden",
        "image_ref": "fixture://vqc1/clean-editorial.png",
        "expected_sha256": checksum,
        "expected_format": "PNG",
        "target_aspect_ratio": "16:9",
        "minimum_effective_width": 1920,
        "minimum_effective_height": 1080,
        "expected_alpha_behavior": expected_alpha_behavior,
        "intended_crop": intended_crop,
        "text_safe_regions": [safe_region],
        "reserved_overlay_regions": reserved_overlay_regions or [],
        "subject_focal_region": focal_region,
        "protected_visual_regions": protected_regions,
        "artifact_inspection": artifact,
        "native_overlay": overlay,
        "reuse_similarity": reuse,
        "structured_visual_review": visual_review,
        "rights_disclosure": rights,
        "provider_request": None,
        "scoped_approval": None,
        "attempt_ledger": None,
        "cost_estimate": None,
        "provider_response": None,
        "image_materialization": None,
        "image_normalization": None,
        "human_visual_review": human,
    }
    return image_path, _bound(ImageVisualQualityControlInput, **input_payload)


def _real_jpeg_normalization_input(
    tmp_path: Path,
) -> tuple[Path, ImageVisualQualityControlInput]:
    settings = Settings(
        _env_file=None,
        gemini_image_model_id="gemini-3.1-flash-image",
        gemini_image_default_size="2K",
        gemini_image_default_aspect_ratio="16:9",
        gemini_image_max_outputs=1,
        gemini_image_max_attempts_per_scene=1,
    )
    timestamp = datetime(2026, 7, 18, 7, 30, tzinfo=UTC)
    bundle = IMGCanaryPlanBuilder(settings).build(
        now=timestamp,
        run_suffix="a1b2c3d4",
    )

    workspace = (tmp_path / "real-provider-chain").resolve()
    fixture_png = workspace / "fixture-source.png"
    encoded_jpeg = workspace / "encoded-provider-output.jpg"
    fixture_png.parent.mkdir(parents=True)
    fixture_png.write_bytes(build_fixture_png(2560, 1440))
    completed = subprocess.run(
        [
            FFMPEG_FULL_DEFAULT,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-n",
            "-i",
            str(fixture_png),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(encoded_jpeg),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    jpeg_bytes = encoded_jpeg.read_bytes()
    encoded_jpeg.unlink()

    output_reference = "volatile://google-gemini-image/vqc1-real-chain"
    original_path = workspace / "source" / "generated-original.jpg"
    plan_payload = {
        "request_ref": bundle.provider_request.generic_request_ref,
        "output_reference": output_reference,
        "workspace_root": str(workspace),
        "destination_path": str(original_path),
        "raw_url_persisted": False,
        "execution_allowed": False,
    }
    materialization_plan = GeminiImageOutputMaterializationPlan(
        **plan_payload,
        plan_hash=ai_image_stable_hash(plan_payload),
    )
    adapter = GoogleGeminiImageAdapter(
        settings,
        raster_decoder_path=FFMPEG_FULL_DEFAULT,
    )
    materialization_receipt = adapter.materialize_output(
        materialization_plan,
        transient=GeminiImageTransientOutput(
            output_reference=output_reference,
            image_bytes=jpeg_bytes,
            mime_type="image/jpeg",
            transport="GEMINI_API_NATIVE",
            provider_call_made=True,
        ),
    )
    normalized_path = tmp_path / "vqc1-source.png"
    normalization_receipt = IMGCanaryImageNormalizer(
        ffmpeg=FFMPEG_FULL_DEFAULT
    ).normalize(
        source_path=original_path,
        destination_path=normalized_path,
        workspace_root=tmp_path,
    )
    normalized_bytes = normalized_path.read_bytes()
    image_path, fixture_evidence = _input_for(
        tmp_path,
        image_bytes=normalized_bytes,
        width=1920,
        height=1080,
    )
    assert image_path.resolve() == normalized_path.resolve()

    provider_request_id = "interactions/img-canary-vqc1-real-chain-001"
    provider_operation_id = "operations/img-canary-vqc1-real-chain-001"
    actual_cost = Decimal("0.101")
    attempt_payload = {
        "run_id": bundle.run_identity.run_id,
        "request_fingerprint": GoogleGeminiImageAdapter.idempotency_fingerprint(
            bundle.provider_request
        ),
        "idempotency_key_hash": ai_image_stable_hash(
            bundle.provider_request.idempotency_key
        ),
        "attempt_limit": 1,
        "attempts_consumed": 1,
        "status": "SUCCEEDED",
        "provider_call_made": True,
        "provider_request_id_ref": provider_request_id,
        "provider_operation_id_ref": provider_operation_id,
        "failure_reason_code": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    attempt = IMGCanaryAttemptLedger(
        **attempt_payload,
        content_hash=ai_image_stable_hash(attempt_payload),
    )
    response_payload = {
        "run_id": bundle.run_identity.run_id,
        "provider": "google_gemini_image",
        "model": "gemini-3.1-flash-image",
        "provider_status": "INTERACTION_COMPLETED",
        "provider_request_id_ref": provider_request_id,
        "provider_operation_id_ref": provider_operation_id,
        "submitted_at": timestamp,
        "completed_at": timestamp,
        "output_count": 1,
        "output_checksum": materialization_receipt["sha256"],
        "image_width": materialization_receipt["image_width"],
        "image_height": materialization_receipt["image_height"],
        "image_format": materialization_receipt["image_format"],
        "size_bytes": materialization_receipt["size_bytes"],
        "usage_metadata": {"total_tokens": 128},
        "estimated_cost_usd": bundle.cost.estimated_amount,
        "actual_cost_usd": actual_cost,
        "provider_attempts_consumed": 1,
        "raw_response_persisted": False,
        "raw_image_bytes_persisted_in_manifest": False,
        "raw_url_persisted": False,
        "api_key_persisted": False,
        "external_fallback_used": False,
    }
    response = IMGCanaryProviderResponseSummary(
        **response_payload,
        content_hash=ai_image_stable_hash(response_payload),
    )

    materialization_payload = {
        "run_id": bundle.run_identity.run_id,
        "request_hash": bundle.provider_request.content_hash,
        "provider_response_hash": response.content_hash,
        "provider_request_id_ref": provider_request_id,
        "provider_operation_id_ref": provider_operation_id,
        "estimated_cost_usd": bundle.cost.estimated_amount,
        "actual_cost_usd": actual_cost,
        "materialization_receipt_ref": (
            f"materialization://img-canary/{bundle.run_identity.run_id}/original"
        ),
        "materialization_receipt_hash": ai_image_stable_hash(
            materialization_receipt
        ),
        **materialization_receipt,
    }
    materialization = _bound(
        VQC1ImageMaterializationEvidence,
        **materialization_payload,
    )
    normalization_payload = {
        "run_id": bundle.run_identity.run_id,
        "request_hash": bundle.provider_request.content_hash,
        "provider_response_hash": response.content_hash,
        "provider_request_id_ref": provider_request_id,
        "provider_operation_id_ref": provider_operation_id,
        "estimated_cost_usd": bundle.cost.estimated_amount,
        "actual_cost_usd": actual_cost,
        "materialization_evidence_hash": materialization.content_hash,
        "normalization_receipt_ref": (
            f"normalization://img-canary/{bundle.run_identity.run_id}/review-png"
        ),
        "normalization_receipt_hash": normalization_receipt["content_hash"],
        "source_size_bytes": materialization_receipt["size_bytes"],
        "target_size_bytes": normalized_path.stat().st_size,
        **{
            key: value
            for key, value in normalization_receipt.items()
            if key != "content_hash"
        },
    }
    normalization = _bound(
        VQC1ImageNormalizationEvidence,
        **normalization_payload,
    )

    rights_payload = fixture_evidence.rights_disclosure.model_dump(
        mode="python",
        exclude={"content_hash"},
    )
    rights_payload.update(
        {
            "request_hash": bundle.provider_request.content_hash,
            "prompt_hash": bundle.provider_request.prompt_hash,
            "generation_timestamp": timestamp,
            "provider_request_id": provider_request_id,
            "provider_operation_id": provider_operation_id,
            "output_checksum": normalization.target_sha256,
            "output_width": normalization.target_width,
            "output_height": normalization.target_height,
            "cost_estimate_ref": bundle.provider_request.cost_ref,
            "cost_estimate_hash": bundle.cost.snapshot_hash,
            "estimated_cost_usd": bundle.cost.estimated_amount,
            "actual_usage_ref": (
                f"usage://img-canary/{bundle.run_identity.run_id}/provider-summary"
            ),
            "actual_cost_usd": actual_cost,
            "approval_ref": bundle.approval.approval_ref,
            "approval_hash": bundle.approval.content_hash,
            "attempt_ref": (
                f"attempt://img-canary/{bundle.run_identity.run_id}/one-shot"
            ),
            "attempt_hash": attempt.content_hash,
            "generation_attempts_consumed": 1,
            "idempotency_key": bundle.provider_request.idempotency_key,
            "scene_usage_refs": [
                f"scene://img-canary/{bundle.run_identity.run_id}/fragmented-information"
            ],
            "provider_call_made": True,
        }
    )
    rights = _bound(RightsDisclosureEvidence, **rights_payload)
    evidence = _rebuild(
        fixture_evidence,
        run_id=bundle.run_identity.run_id,
        image_ref=f"artifact://img-canary/{bundle.run_identity.run_id}/normalized.png",
        rights_disclosure=rights,
        provider_request=bundle.provider_request,
        scoped_approval=bundle.approval,
        attempt_ledger=attempt,
        cost_estimate=bundle.cost,
        provider_response=response,
        image_materialization=materialization,
        image_normalization=normalization,
    )
    return image_path, evidence


def _gate(report, name: str):
    return next(item for item in report.gate_results if item.gate_name == name)


def test_clean_actual_bytes_produce_typed_fourteen_gate_review_package(tmp_path: Path) -> None:
    image_path, evidence = _input_for(tmp_path)
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )

    assert report.technical_probe.safe_decode is True
    assert report.technical_probe.checksum_sha256 == hashlib.sha256(image_path.read_bytes()).hexdigest()
    assert (report.technical_probe.width, report.technical_probe.height) == (2560, 1440)
    assert report.technical_probe.image_format == "PNG"
    assert report.technical_media_qc.result == "PASS"
    assert report.technical_status == "PASS"
    assert report.verdict == "REVIEW_REQUIRED"
    assert report.creative_review_state == "REVIEW_REQUIRED"
    assert report.human_review_state == "PENDING"
    assert report.archive_eligible_for_review is True
    assert report.human_final_approval_auto_passed is False
    assert report.production_eligible is False and report.not_publishable is True
    assert len(report.gate_results) == 14
    assert {item.gate_name for item in report.gate_results} == set(VQC1_REQUIRED_GATES)
    assert all(isinstance(item, CreativeGateEvidence) for item in report.gate_results)
    assert all(item.reason_codes and item.evidence_refs and item.content_hash for item in report.gate_results)
    assert {
        _gate(report, "SemanticMatchGate").result,
        _gate(report, "VisualLanguageMatchGate").result,
        _gate(report, "VisualContinuityGate").result,
        _gate(report, "HumanVisualApprovalGate").result,
    } == {"REVIEW_REQUIRED"}


def test_probe_reads_real_bytes_and_rejects_metadata_checksum_tamper(tmp_path: Path) -> None:
    image_path, evidence = _input_for(tmp_path)
    tampered = _rebuild(evidence, expected_sha256="0" * 64)
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=tampered,
    )
    assert report.technical_probe.safe_decode is True
    assert report.technical_media_qc.result == "FAIL"
    assert "TECHNICAL_CHECK_FAILED_CHECKSUM_BINDING" in report.technical_media_qc.reason_codes
    assert _gate(report, "TechnicalImageFitnessGate").result == "BLOCK"
    assert report.verdict == "BLOCK"


@pytest.mark.parametrize(
    ("payload", "reason_code"),
    [
        (b"\x89PNG\r\n\x1a\ntruncated", "VQC1_PNG_TRUNCATED"),
        (b"GIF89a-not-an-approved-raster", "VQC1_IMAGE_FORMAT_UNSUPPORTED"),
    ],
)
def test_corrupt_and_unsupported_bytes_block(
    tmp_path: Path,
    payload: bytes,
    reason_code: str,
) -> None:
    image_path, evidence = _input_for(
        tmp_path,
        image_bytes=payload,
        width=2560,
        height=1440,
    )
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    assert report.technical_probe.safe_decode is False
    assert reason_code in report.technical_probe.reason_codes
    assert _gate(report, "TechnicalImageFitnessGate").result == "BLOCK"
    assert report.archive_eligible_for_review is False


def test_unexpected_alpha_behavior_blocks_technical_fitness(tmp_path: Path) -> None:
    image_path, evidence = _input_for(tmp_path, image_bytes=_rgba_png())
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    assert report.technical_probe.alpha_behavior == "PRESENT"
    assert "TECHNICAL_CHECK_FAILED_ALPHA_POLICY" in report.technical_media_qc.reason_codes
    assert _gate(report, "TechnicalImageFitnessGate").result == "BLOCK"


def test_normalized_region_and_text_safe_region_bounds_are_fail_closed() -> None:
    with pytest.raises(ValidationError, match="VQC1_NORMALIZED_REGION_OUT_OF_BOUNDS"):
        NormalizedImageRegion(
            region_id="bad",
            region_role="SUBJECT_FOCAL",
            x=0.9,
            y=0.2,
            width=0.2,
            height=0.2,
        )
    with pytest.raises(ValidationError, match="VSR1_TEXT_SAFE_REGION_OUT_OF_BOUNDS"):
        TextSafeRegion(
            id="bad-safe",
            x=0.8,
            y=0.1,
            width=0.3,
            height=0.2,
            purpose="invalid",
            minimum_contrast_requirement=4.5,
            alignment="left",
        )


def test_crop_effective_resolution_is_calculated_from_probed_bytes(tmp_path: Path) -> None:
    crop = NormalizedImageRegion(
        region_id="crop-16x9",
        region_role="INTENDED_CROP",
        x=0.0,
        y=0.0,
        width=0.5,
        height=0.5,
    )
    focal = NormalizedImageRegion(
        region_id="subject-focal",
        region_role="SUBJECT_FOCAL",
        x=0.20,
        y=0.20,
        width=0.10,
        height=0.10,
    )
    image_path, evidence = _input_for(
        tmp_path,
        intended_crop=crop,
        focal_region=focal,
        protected_regions=[],
    )
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    crop_evidence = report.crop_safety_evidence
    assert (crop_evidence.effective_width, crop_evidence.effective_height) == (1280, 720)
    assert crop_evidence.resolution_passed is False
    assert crop_evidence.upscale_required is True
    assert "CROP_EFFECTIVE_RESOLUTION_BELOW_1080P" in crop_evidence.reason_codes
    assert _gate(report, "CropSafetyGate").result == "BLOCK"


def test_native_overlay_cannot_hide_focal_subject(tmp_path: Path) -> None:
    focal = NormalizedImageRegion(
        region_id="subject-focal",
        region_role="SUBJECT_FOCAL",
        x=0.10,
        y=0.14,
        width=0.30,
        height=0.20,
    )
    image_path, evidence = _input_for(
        tmp_path,
        focal_region=focal,
        protected_regions=[],
    )
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    assert report.composition_compliance_evidence.meaning_bearing_subject_hidden is True
    assert "NATIVE_OVERLAY_COLLIDES_WITH_SUBJECT_FOCAL_REGION" in report.composition_compliance_evidence.reason_codes
    assert _gate(report, "CompositionComplianceGate").result == "BLOCK"
    assert _gate(report, "NativeOverlayComplianceGate").result == "BLOCK"


def test_reserved_overlay_region_cannot_collide_with_critical_subject(tmp_path: Path) -> None:
    reserved = TextSafeRegion(
        id="reserved-lower-third",
        x=0.60,
        y=0.30,
        width=0.20,
        height=0.20,
        purpose="Reserved native lower-third",
        minimum_contrast_requirement=4.5,
        alignment="left",
    )
    image_path, evidence = _input_for(
        tmp_path,
        reserved_overlay_regions=[reserved],
    )
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    composition = report.composition_compliance_evidence
    assert composition.reserved_overlay_collision_region_ids == ["reserved-lower-third"]
    assert "RESERVED_OVERLAY_COLLIDES_WITH_CRITICAL_VISUAL_REGION" in composition.reason_codes
    assert _gate(report, "CompositionComplianceGate").result == "BLOCK"


@pytest.mark.parametrize(
    ("artifact_kind", "gate_name", "reason_code"),
    [
        ("TEXT", "GeneratedTextArtifactGate", "GENERATED_TEXT_ARTIFACT"),
        ("NUMBER", "GeneratedNumberArtifactGate", "GENERATED_NUMBER_ARTIFACT"),
        ("FAKE_UI", "FakeUILogoGate", "FAKE_UI_RISK"),
        ("LOGO", "FakeUILogoGate", "LOGO_OR_TRADEMARK_RISK"),
        ("WATERMARK", "WatermarkArtifactGate", "WATERMARK_RISK"),
    ],
)
def test_irreparable_generated_artifacts_block_with_typed_regions(
    tmp_path: Path,
    artifact_kind: str,
    gate_name: str,
    reason_code: str,
) -> None:
    image_path, evidence = _input_for(
        tmp_path,
        artifact_regions=[_artifact_region(artifact_kind)],
    )
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    gate = _gate(report, gate_name)
    assert gate.result == "BLOCK"
    assert reason_code in gate.reason_codes
    assert gate.metrics["detected_regions"][0]["region"]["coordinate_space"] == "normalized"


def test_repairable_artifact_stays_review_required_until_new_evidence(tmp_path: Path) -> None:
    image_path, evidence = _input_for(
        tmp_path,
        artifact_regions=[
            _artifact_region("TEXT", repairability="NATIVE_OVERLAY_REPAIR")
        ],
    )
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    gate = _gate(report, "GeneratedTextArtifactGate")
    assert gate.result == "REVIEW_REQUIRED"
    assert gate.repairability == "DETERMINISTIC_NATIVE_REPAIR"
    assert "GENERATED_ARTIFACT_REPAIR_REQUIRED" in gate.reason_codes


def test_unassessed_artifacts_cannot_receive_metadata_only_pass(tmp_path: Path) -> None:
    image_path, evidence = _input_for(
        tmp_path,
        inspection_state="PENDING",
        inspection_authority="UNASSESSED",
    )
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    for name in (
        "GeneratedTextArtifactGate",
        "GeneratedNumberArtifactGate",
        "FakeUILogoGate",
        "WatermarkArtifactGate",
    ):
        assert _gate(report, name).result == "REVIEW_REQUIRED"


def test_exact_text_remains_native_authority_and_contrast_is_deterministic(tmp_path: Path) -> None:
    image_path, evidence = _input_for(tmp_path)
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    overlay = report.native_overlay_compliance_evidence
    assert overlay.authoritative_text == "Information is everywhere. Context is nowhere."
    assert overlay.exact_text_native_authority is True
    assert overlay.generated_image_owns_final_text is False
    assert overlay.contrast_ratio == 21.0
    assert overlay.contrast_passed is True
    assert _gate(report, "NativeOverlayComplianceGate").result == "PASS"


def test_exact_sha_reuse_uses_real_checksum_and_empty_corpus_reviews(tmp_path: Path) -> None:
    image_bytes = build_fixture_png(2560, 1440)
    checksum = hashlib.sha256(image_bytes).hexdigest()
    duplicate_path, duplicate_evidence = _input_for(
        tmp_path,
        image_bytes=image_bytes,
        comparison_hashes=[checksum],
        comparison_refs=["fixture://vqc1/comparison/duplicate"],
    )
    duplicate = ImageVisualQualityControlService().evaluate(
        image_path=duplicate_path,
        evidence=duplicate_evidence,
    )
    assert _gate(duplicate, "ReuseSimilarityGate").result == "BLOCK"

    empty_path, empty_evidence = _input_for(
        tmp_path,
        image_bytes=image_bytes,
        comparison_hashes=[],
        comparison_refs=[],
    )
    empty = ImageVisualQualityControlService().evaluate(
        image_path=empty_path,
        evidence=empty_evidence,
    )
    assert _gate(empty, "ReuseSimilarityGate").result == "REVIEW_REQUIRED"
    assert "REUSE_COMPARISON_CORPUS_EMPTY" in _gate(empty, "ReuseSimilarityGate").reason_codes


def test_semantic_style_and_isolated_continuity_never_auto_pass(tmp_path: Path) -> None:
    image_path, evidence = _input_for(tmp_path)
    review = _rebuild(
        evidence.structured_visual_review,
        observed_output_summary="Filename and fixture metadata claim a perfect semantic match.",
        semantic_concerns=[],
        style_concerns=[],
        continuity_concerns=[],
    )
    evidence = _rebuild(evidence, structured_visual_review=review)
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    assert _gate(report, "SemanticMatchGate").result == "REVIEW_REQUIRED"
    assert _gate(report, "VisualLanguageMatchGate").result == "REVIEW_REQUIRED"
    continuity = _gate(report, "VisualContinuityGate")
    assert continuity.result == "REVIEW_REQUIRED"
    assert continuity.metrics["isolated_canary_scope"] is True
    assert continuity.metrics["multi_scene_continuity_pass_claimed"] is False


def test_off_brand_observation_stays_explicitly_review_required(tmp_path: Path) -> None:
    image_path, evidence = _input_for(tmp_path)
    review = _rebuild(
        evidence.structured_visual_review,
        style_concerns=["OFF_BRAND_VISUAL_TREATMENT"],
    )
    evidence = _rebuild(evidence, structured_visual_review=review)
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    gate = _gate(report, "VisualLanguageMatchGate")
    assert gate.result == "REVIEW_REQUIRED"
    assert "OFF_BRAND_VISUAL_TREATMENT" in gate.reason_codes


def test_rights_provenance_disclosure_are_mandatory_and_byte_bound(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        RightsDisclosureEvidence(
            provider="google_gemini_image",
            vendor="google",
            model="gemini-3.1-flash-image",
            request_hash="a" * 64,
            prompt_hash="b" * 64,
            content_hash="c" * 64,
        )

    image_path, evidence = _input_for(tmp_path)
    rights = _rebuild(evidence.rights_disclosure, output_checksum="0" * 64)
    evidence = _rebuild(evidence, rights_disclosure=rights)
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    gate = _gate(report, "RightsDisclosureCompletenessGate")
    assert gate.result == "BLOCK"
    assert "RIGHTS_OUTPUT_CHECKSUM_BINDING_MISMATCH" in gate.reason_codes


def test_real_jpeg_materialization_normalization_and_vqc_probe_are_fully_bound(
    tmp_path: Path,
) -> None:
    image_path, evidence = _real_jpeg_normalization_input(tmp_path)
    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )

    gate = _gate(report, "RightsDisclosureCompletenessGate")
    assert gate.result == "PASS"
    assert gate.reason_codes == ["RIGHTS_PROVENANCE_DISCLOSURE_COMPLETE"]
    assert gate.metrics["real_provider_materialization_bound"] is True
    assert gate.metrics["normalization_to_vqc_probe_bound"] is True
    assert evidence.provider_response is not None
    assert evidence.image_materialization is not None
    assert evidence.image_normalization is not None
    assert evidence.provider_response.image_format == "JPEG"
    assert evidence.image_materialization.sha256 == evidence.provider_response.output_checksum
    assert evidence.image_normalization.source_sha256 == evidence.image_materialization.sha256
    assert evidence.image_normalization.target_sha256 == report.technical_probe.checksum_sha256
    assert report.technical_probe.image_format == "PNG"
    assert report.archive_eligible_for_review is True


@pytest.mark.parametrize(
    ("tamper_kind", "reason_code"),
    [
        ("provider_original_checksum", "RIGHTS_PROVIDER_MATERIALIZATION_CHAIN_MISMATCH"),
        ("provider_request_id", "RIGHTS_PROVIDER_MATERIALIZATION_CHAIN_MISMATCH"),
        ("normalized_target_size", "RIGHTS_NORMALIZATION_TO_VQC_CHAIN_MISMATCH"),
    ],
)
def test_real_provider_image_chain_tampering_blocks_rights_gate(
    tmp_path: Path,
    tamper_kind: str,
    reason_code: str,
) -> None:
    image_path, evidence = _real_jpeg_normalization_input(tmp_path)
    if tamper_kind == "provider_original_checksum":
        assert evidence.provider_response is not None
        response = _rebuild(evidence.provider_response, output_checksum="0" * 64)
        evidence = _rebuild(evidence, provider_response=response)
    elif tamper_kind == "provider_request_id":
        assert evidence.image_materialization is not None
        materialization = _rebuild(
            evidence.image_materialization,
            provider_request_id_ref="interactions/tampered-provider-request",
        )
        evidence = _rebuild(evidence, image_materialization=materialization)
    else:
        assert evidence.image_normalization is not None
        normalization = _rebuild(
            evidence.image_normalization,
            target_size_bytes=evidence.image_normalization.target_size_bytes + 1,
        )
        evidence = _rebuild(evidence, image_normalization=normalization)

    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    gate = _gate(report, "RightsDisclosureCompletenessGate")
    assert gate.result == "BLOCK"
    assert reason_code in gate.reason_codes
    assert report.archive_eligible_for_review is False


@pytest.mark.parametrize(
    "missing_field",
    [
        "request_hash",
        "prompt_hash",
        "approval_ref",
        "idempotency_key",
        "synthetic_media_disclosure_ref",
    ],
)
def test_each_critical_rights_binding_is_contract_required(
    tmp_path: Path,
    missing_field: str,
) -> None:
    _, evidence = _input_for(tmp_path)
    payload = evidence.rights_disclosure.model_dump(
        mode="python",
        exclude={"content_hash"},
    )
    payload.pop(missing_field)
    with pytest.raises(ValidationError):
        RightsDisclosureEvidence(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )


def test_checksum_and_human_review_boundary_are_required_input_contracts(tmp_path: Path) -> None:
    _, evidence = _input_for(tmp_path)
    payload = evidence.model_dump(mode="python", exclude={"content_hash"})
    payload.pop("expected_sha256")
    with pytest.raises(ValidationError):
        ImageVisualQualityControlInput(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    payload = evidence.model_dump(mode="python", exclude={"content_hash"})
    payload.pop("human_visual_review")
    with pytest.raises(ValidationError):
        ImageVisualQualityControlInput(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )


def test_human_final_approval_contract_cannot_be_auto_passed(tmp_path: Path) -> None:
    image_path, evidence = _input_for(tmp_path)
    payload = evidence.human_visual_review.model_dump(
        mode="python",
        exclude={"content_hash"},
    )
    payload["review_state"] = "PASS"
    payload["final_decision"] = "PASS"
    with pytest.raises(ValidationError):
        HumanVisualReviewEvidence(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    report = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    gate = _gate(report, "HumanVisualApprovalGate")
    assert gate.result == "REVIEW_REQUIRED"
    assert gate.authority == "HUMAN_FINAL"
    assert report.human_review_state == "PENDING"


def test_vqc1_fixture_path_makes_no_provider_call_and_preserves_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    historical = [
        REPO_ROOT / "reports" / "vsr1_summary.json",
        REPO_ROOT / "reports" / "img1_summary.json",
        REPO_ROOT / "reports" / "img1_google_gemini_image_provider_report.md",
    ]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in historical}

    def forbidden_submit(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("VQC1_OFFLINE_FIXTURE_MUST_NOT_CALL_PROVIDER")

    monkeypatch.setattr(GoogleGeminiImageAdapter, "submit_generation", forbidden_submit)
    image_path, evidence = _input_for(tmp_path)
    first = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    second = ImageVisualQualityControlService().evaluate(
        image_path=image_path,
        evidence=evidence,
    )
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in historical}

    assert before == after
    assert first.content_hash == second.content_hash
    assert first.human_review_state == "PENDING"
    assert first.verdict == "REVIEW_REQUIRED"


def test_probe_evidence_hash_is_byte_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "probe.png"
    path.write_bytes(build_fixture_png(2560, 1440))
    probe = ImageTechnicalProbe()
    first = probe.probe(path=path, image_ref="fixture://vqc1/probe.png")
    second = probe.probe(path=path, image_ref="fixture://vqc1/probe.png")
    assert first == second
    assert first.content_hash == ai_image_stable_hash(
        first.model_dump(mode="json", exclude={"content_hash"})
    )
