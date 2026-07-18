from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path
from typing import Any, Iterable

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.image_visual_quality_control import (
    CompositionComplianceEvidence,
    CropSafetyEvidence,
    GeneratedArtifactInspectionEvidence,
    GeneratedArtifactRegion,
    ImageVisualGateEvidence,
    ImageVisualGateName,
    ImageVisualQualityControlInput,
    ImageVisualQualityControlReport,
    NativeOverlayComplianceEvidence,
    NormalizedImageRegion,
    TechnicalImageProbeEvidence,
    VQC1ImageMaterializationEvidence,
    VQC1ImageNormalizationEvidence,
    img_canary_provider_request_lineage_ref,
)
from app.contracts.native_renderer import TextSafeRegion
from app.services.creative_media_qc import TechnicalMediaQC


MAX_VQC1_IMAGE_BYTES = 64 * 1024 * 1024
MAX_VQC1_IMAGE_PIXELS = 16_777_216
IMAGE_TECHNICAL_CHECKS = (
    "exists_nonempty",
    "safe_decode",
    "checksum_binding",
    "supported_format",
    "dimensions",
    "target_aspect_ratio",
    "color_mode",
    "color_profile_recorded",
    "file_size",
    "corruption_absent",
    "alpha_policy",
    "effective_crop_resolution",
    "no_upscale",
)


class ImageTechnicalProbe:
    """Read and fully validate bounded PNG bytes without trusting caller metadata."""

    def probe(self, *, path: Path, image_ref: str) -> TechnicalImageProbeEvidence:
        candidate = Path(path)
        exists_nonempty = (
            candidate.is_file()
            and not candidate.is_symlink()
            and candidate.stat().st_size > 0
        )
        file_size = candidate.stat().st_size if exists_nonempty else 0
        checksum: str | None = None
        details: dict[str, Any] = {}
        safe_decode = False
        corruption_detected = True
        reasons: list[str] = []

        if not exists_nonempty:
            reasons.append("VQC1_IMAGE_FILE_MISSING_OR_EMPTY")
        elif file_size > MAX_VQC1_IMAGE_BYTES:
            reasons.append("VQC1_IMAGE_FILE_SIZE_EXCEEDS_LIMIT")
        else:
            digest = hashlib.sha256()
            data_parts: list[bytes] = []
            with candidate.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    data_parts.append(chunk)
            checksum = digest.hexdigest()
            data = b"".join(data_parts)
            try:
                details = self._decode_png(data)
            except ValueError as exc:
                reasons.append(str(exc))
            else:
                safe_decode = True
                corruption_detected = False
                reasons.append("VQC1_PNG_SAFE_DECODE_PASS")

        payload: dict[str, Any] = {
            "image_ref": image_ref,
            "local_path": str(candidate.resolve()),
            "exists_nonempty": exists_nonempty,
            "file_size_bytes": file_size,
            "checksum_sha256": checksum,
            "safe_decode": safe_decode,
            "image_format": details.get("image_format"),
            "width": details.get("width"),
            "height": details.get("height"),
            "aspect_ratio": details.get("aspect_ratio"),
            "bit_depth": details.get("bit_depth"),
            "png_color_type": details.get("png_color_type"),
            "color_mode": details.get("color_mode"),
            "color_profile": details.get("color_profile"),
            "alpha_behavior": details.get("alpha_behavior"),
            "corruption_detected": corruption_detected,
            "probe_method": "VQC1_STDLIB_PNG_CRC_ZLIB",
            "reason_codes": reasons,
        }
        return TechnicalImageProbeEvidence(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _decode_png(data: bytes) -> dict[str, Any]:
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("VQC1_IMAGE_FORMAT_UNSUPPORTED")
        if len(data) < 45:
            raise ValueError("VQC1_PNG_TRUNCATED")

        offset = 8
        width = height = 0
        bit_depth = color_type = -1
        idat_parts: list[bytes] = []
        saw_ihdr = False
        saw_iend = False
        saw_srgb = False
        saw_iccp = False
        saw_transparency = False

        while offset + 12 <= len(data):
            length = int.from_bytes(data[offset : offset + 4], "big")
            chunk_type = data[offset + 4 : offset + 8]
            chunk_end = offset + 12 + length
            if length > MAX_VQC1_IMAGE_BYTES or chunk_end > len(data):
                raise ValueError("VQC1_PNG_CHUNK_TRUNCATED")
            chunk_data = data[offset + 8 : offset + 8 + length]
            stored_crc = int.from_bytes(data[offset + 8 + length : chunk_end], "big")
            if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != stored_crc:
                raise ValueError("VQC1_PNG_CRC_INVALID")

            if not saw_ihdr:
                if chunk_type != b"IHDR" or length != 13:
                    raise ValueError("VQC1_PNG_IHDR_INVALID")
                (
                    width,
                    height,
                    bit_depth,
                    color_type,
                    compression,
                    filtering,
                    interlace,
                ) = struct.unpack(">IIBBBBB", chunk_data)
                if (
                    width <= 0
                    or height <= 0
                    or width * height > MAX_VQC1_IMAGE_PIXELS
                ):
                    raise ValueError("VQC1_PNG_DIMENSIONS_INVALID")
                if compression != 0 or filtering != 0 or interlace != 0:
                    raise ValueError("VQC1_PNG_ENCODING_UNSUPPORTED")
                valid_depths = {
                    0: {1, 2, 4, 8, 16},
                    2: {8, 16},
                    3: {1, 2, 4, 8},
                    4: {8, 16},
                    6: {8, 16},
                }
                if bit_depth not in valid_depths.get(color_type, set()):
                    raise ValueError("VQC1_PNG_COLOR_MODE_INVALID")
                saw_ihdr = True
            elif chunk_type == b"IHDR":
                raise ValueError("VQC1_PNG_DUPLICATE_IHDR")

            if chunk_type == b"IDAT":
                idat_parts.append(chunk_data)
            elif chunk_type == b"sRGB":
                saw_srgb = True
            elif chunk_type == b"iCCP":
                saw_iccp = True
            elif chunk_type == b"tRNS":
                saw_transparency = True
            elif chunk_type == b"IEND":
                if length != 0:
                    raise ValueError("VQC1_PNG_IEND_INVALID")
                saw_iend = True
                offset = chunk_end
                break
            offset = chunk_end

        if not saw_ihdr or not idat_parts or not saw_iend or offset != len(data):
            raise ValueError("VQC1_PNG_STRUCTURE_INVALID")

        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
        row_payload_bytes = (width * channels * bit_depth + 7) // 8
        expected_decoded_bytes = (row_payload_bytes + 1) * height
        inflater = zlib.decompressobj()
        decoded = inflater.decompress(b"".join(idat_parts), expected_decoded_bytes + 1)
        if len(decoded) != expected_decoded_bytes or inflater.unconsumed_tail:
            raise ValueError("VQC1_PNG_DECODE_SIZE_INVALID")
        decoded += inflater.flush(1)
        if not inflater.eof or inflater.unused_data or len(decoded) != expected_decoded_bytes:
            raise ValueError("VQC1_PNG_ZLIB_STREAM_INVALID")
        row_size = row_payload_bytes + 1
        if any(decoded[index] > 4 for index in range(0, len(decoded), row_size)):
            raise ValueError("VQC1_PNG_FILTER_INVALID")

        color_modes = {
            0: "GRAYSCALE",
            2: "RGB",
            3: "INDEXED",
            4: "GRAYSCALE_ALPHA",
            6: "RGBA",
        }
        return {
            "image_format": "PNG",
            "width": width,
            "height": height,
            "aspect_ratio": round(width / height, 8),
            "bit_depth": bit_depth,
            "png_color_type": color_type,
            "color_mode": color_modes[color_type],
            "color_profile": "ICC" if saw_iccp else "SRGB" if saw_srgb else "UNSPECIFIED",
            "alpha_behavior": (
                "PRESENT" if color_type in {4, 6} or saw_transparency else "NONE"
            ),
        }


class ImageVisualQualityControlService:
    """VQC1 deterministic image QC with an explicit creative/human stop."""

    def __init__(self, *, probe: ImageTechnicalProbe | None = None) -> None:
        self.probe = probe or ImageTechnicalProbe()

    def evaluate(
        self,
        *,
        image_path: Path,
        evidence: ImageVisualQualityControlInput,
    ) -> ImageVisualQualityControlReport:
        technical_probe = self.probe.probe(path=image_path, image_ref=evidence.image_ref)
        crop = self._crop_evidence(evidence, technical_probe)
        composition = self._composition_evidence(evidence)
        overlay = self._native_overlay_evidence(
            evidence,
            composition,
            technical_probe.checksum_sha256,
        )
        technical_media_qc = self._technical_media_qc(
            evidence,
            technical_probe,
            crop,
        )

        gates: list[ImageVisualGateEvidence] = []
        gates.extend(
            self._artifact_gates(
                inspection=evidence.artifact_inspection,
                actual_sha256=technical_probe.checksum_sha256,
            )
        )
        gates.append(self._composition_gate(composition))
        gates.extend(
            self._creative_review_gates(
                evidence,
                technical_probe.checksum_sha256,
            )
        )
        gates.append(
            self._gate(
                gate_name="TechnicalImageFitnessGate",
                result="PASS" if technical_media_qc.result == "PASS" else "BLOCK",
                reason_codes=(
                    ["TECHNICAL_IMAGE_FITNESS_PASS"]
                    if technical_media_qc.result == "PASS"
                    else technical_media_qc.reason_codes
                ),
                authority="DETERMINISTIC",
                repairability=(
                    "NOT_REQUIRED"
                    if technical_media_qc.result == "PASS"
                    else "NOT_REPAIRABLE"
                ),
                metrics={
                    "technical_media_qc_result": technical_media_qc.result,
                    "required_checks": list(IMAGE_TECHNICAL_CHECKS),
                },
                evidence_refs=[self._ref("technical-probe", technical_probe.content_hash)],
            )
        )
        gates.append(self._crop_gate(crop))
        gates.append(self._reuse_gate(evidence, technical_probe))
        gates.append(self._continuity_gate(evidence))
        gates.append(self._rights_gate(evidence, technical_probe))
        gates.append(self._native_overlay_gate(overlay))
        gates.append(self._human_gate(evidence, technical_probe.checksum_sha256))

        verdicts = {item.result for item in gates}
        verdict = (
            "BLOCK"
            if "BLOCK" in verdicts
            else "REVIEW_REQUIRED"
            if "REVIEW_REQUIRED" in verdicts
            else "PASS"
        )
        by_name = {item.gate_name: item for item in gates}
        archive_gate_names = (
            "CompositionComplianceGate",
            "TechnicalImageFitnessGate",
            "CropSafetyGate",
            "RightsDisclosureCompletenessGate",
            "NativeOverlayComplianceGate",
        )
        archive_eligible = (
            technical_media_qc.result == "PASS"
            and all(by_name[name].result == "PASS" for name in archive_gate_names)
            and "BLOCK" not in verdicts
        )
        payload: dict[str, Any] = {
            "schema_version": "vqc1.image-visual-quality-control.v1",
            "run_id": evidence.run_id,
            "image_ref": evidence.image_ref,
            "image_sha256": technical_probe.checksum_sha256,
            "technical_probe": technical_probe,
            "technical_media_qc": technical_media_qc,
            "crop_safety_evidence": crop,
            "composition_compliance_evidence": composition,
            "native_overlay_compliance_evidence": overlay,
            "gate_results": gates,
            "technical_status": (
                "PASS" if technical_media_qc.result == "PASS" else "BLOCK"
            ),
            "creative_review_state": "REVIEW_REQUIRED",
            "human_review_state": "PENDING",
            "archive_eligible_for_review": archive_eligible,
            "verdict": verdict,
            "human_final_approval_auto_passed": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        json_payload = self._json_payload(payload)
        return ImageVisualQualityControlReport(
            **payload,
            content_hash=ai_image_stable_hash(json_payload),
        )

    @staticmethod
    def _technical_media_qc(
        evidence: ImageVisualQualityControlInput,
        probe: TechnicalImageProbeEvidence,
        crop: CropSafetyEvidence,
    ):
        alpha_passed = (
            evidence.expected_alpha_behavior == "ALLOWED"
            or probe.alpha_behavior == "NONE"
        )
        checks: dict[str, Any] = {
            "exists_nonempty": probe.exists_nonempty,
            "safe_decode": probe.safe_decode,
            "checksum_binding": probe.checksum_sha256 == evidence.expected_sha256,
            "supported_format": probe.image_format == evidence.expected_format,
            "dimensions": bool(probe.width and probe.height),
            "target_aspect_ratio": crop.target_aspect_ratio_passed,
            "color_mode": probe.color_mode in {"RGB", "RGBA", "GRAYSCALE"},
            "color_profile_recorded": probe.color_profile is not None,
            "file_size": 0 < probe.file_size_bytes <= MAX_VQC1_IMAGE_BYTES,
            "corruption_absent": not probe.corruption_detected,
            "alpha_policy": alpha_passed,
            "effective_crop_resolution": crop.resolution_passed,
            "no_upscale": not crop.upscale_required,
            "probe_evidence": probe.model_dump(mode="json"),
        }
        return TechnicalMediaQC().evaluate(
            run_id=evidence.run_id,
            checks=checks,
            required_checks=IMAGE_TECHNICAL_CHECKS,
        )

    def _crop_evidence(
        self,
        evidence: ImageVisualQualityControlInput,
        probe: TechnicalImageProbeEvidence,
    ) -> CropSafetyEvidence:
        source_width = probe.width
        source_height = probe.height
        crop_x = crop_y = effective_width = effective_height = None
        target_aspect_passed = False
        resolution_passed = False
        upscale_required = True
        if source_width and source_height:
            crop_x = int(source_width * evidence.intended_crop.x)
            crop_y = int(source_height * evidence.intended_crop.y)
            effective_width = int(source_width * evidence.intended_crop.width)
            effective_height = int(source_height * evidence.intended_crop.height)
            target_aspect_passed = (
                effective_height > 0
                and abs((effective_width / effective_height) - (16 / 9)) <= 0.01
            )
            resolution_passed = (
                effective_width >= evidence.minimum_effective_width
                and effective_height >= evidence.minimum_effective_height
            )
            upscale_required = not resolution_passed

        focal_preserved = self._contains(evidence.intended_crop, evidence.subject_focal_region)
        protected_preserved = all(
            self._contains(evidence.intended_crop, item)
            for item in evidence.protected_visual_regions
        )
        safe_regions = [*evidence.text_safe_regions, *evidence.reserved_overlay_regions]
        safe_preserved = all(
            self._contains(evidence.intended_crop, item) for item in safe_regions
        )
        reasons: list[str] = []
        if not target_aspect_passed:
            reasons.append("CROP_TARGET_ASPECT_RATIO_MISMATCH")
        if not resolution_passed:
            reasons.append("CROP_EFFECTIVE_RESOLUTION_BELOW_1080P")
        if upscale_required:
            reasons.append("CROP_EXCESSIVE_UPSCALE_REQUIRED")
        if not focal_preserved:
            reasons.append("CROP_SUBJECT_FOCAL_REGION_NOT_PRESERVED")
        if not protected_preserved:
            reasons.append("CROP_PROTECTED_VISUAL_REGION_NOT_PRESERVED")
        if not safe_preserved:
            reasons.append("CROP_TEXT_SAFE_REGION_NOT_PRESERVED")
        if not reasons:
            reasons.append("CROP_SAFETY_PASS")
        payload: dict[str, Any] = {
            "intended_crop": evidence.intended_crop,
            "source_width": source_width,
            "source_height": source_height,
            "crop_x_px": crop_x,
            "crop_y_px": crop_y,
            "effective_width": effective_width,
            "effective_height": effective_height,
            "minimum_effective_width": evidence.minimum_effective_width,
            "minimum_effective_height": evidence.minimum_effective_height,
            "target_aspect_ratio_passed": target_aspect_passed,
            "resolution_passed": resolution_passed,
            "upscale_required": upscale_required,
            "subject_focal_region_preserved": focal_preserved,
            "protected_visual_regions_preserved": protected_preserved,
            "safe_regions_preserved": safe_preserved,
            "reason_codes": reasons,
        }
        return CropSafetyEvidence(
            **payload,
            content_hash=ai_image_stable_hash(self._json_payload(payload)),
        )

    def _composition_evidence(
        self,
        evidence: ImageVisualQualityControlInput,
    ) -> CompositionComplianceEvidence:
        overlay_region = evidence.native_overlay.overlay_region
        overlay_inside_safe = any(
            self._contains(item, overlay_region) for item in evidence.text_safe_regions
        )
        focal_collision = self._intersects(overlay_region, evidence.subject_focal_region)
        protected_collision = any(
            self._intersects(overlay_region, item)
            for item in evidence.protected_visual_regions
        )
        critical_regions: list[Any] = [
            evidence.subject_focal_region,
            *evidence.protected_visual_regions,
        ]
        reserved_collisions = [
            region.id
            for region in evidence.reserved_overlay_regions
            if any(self._intersects(region, critical) for critical in critical_regions)
        ]
        reasons: list[str] = []
        if not overlay_inside_safe:
            reasons.append("NATIVE_OVERLAY_OUTSIDE_TEXT_SAFE_REGION")
        if focal_collision:
            reasons.append("NATIVE_OVERLAY_COLLIDES_WITH_SUBJECT_FOCAL_REGION")
        if protected_collision:
            reasons.append("NATIVE_OVERLAY_COLLIDES_WITH_PROTECTED_VISUAL_REGION")
        if reserved_collisions:
            reasons.append("RESERVED_OVERLAY_COLLIDES_WITH_CRITICAL_VISUAL_REGION")
        if not reasons:
            reasons.append("COMPOSITION_COMPLIANCE_PASS")
        payload: dict[str, Any] = {
            "intended_crop": evidence.intended_crop,
            "text_safe_region_ids": [item.id for item in evidence.text_safe_regions],
            "reserved_overlay_region_ids": [
                item.id for item in evidence.reserved_overlay_regions
            ],
            "subject_focal_region": evidence.subject_focal_region,
            "protected_visual_regions": evidence.protected_visual_regions,
            "overlay_region": overlay_region,
            "all_regions_normalized_and_bounded": True,
            "overlay_inside_text_safe_region": overlay_inside_safe,
            "overlay_collides_with_focal_region": focal_collision,
            "overlay_collides_with_protected_region": protected_collision,
            "reserved_overlay_collision_region_ids": reserved_collisions,
            "meaning_bearing_subject_hidden": (
                focal_collision or protected_collision or bool(reserved_collisions)
            ),
            "reason_codes": reasons,
        }
        return CompositionComplianceEvidence(
            **payload,
            content_hash=ai_image_stable_hash(self._json_payload(payload)),
        )

    def _native_overlay_evidence(
        self,
        evidence: ImageVisualQualityControlInput,
        composition: CompositionComplianceEvidence,
        actual_sha256: str | None,
    ) -> NativeOverlayComplianceEvidence:
        inputs = evidence.native_overlay
        checksum_bound = (
            actual_sha256 is not None
            and inputs.generated_image_sha256 == actual_sha256
        )
        lighter = max(
            inputs.foreground_relative_luminance,
            inputs.background_relative_luminance,
        )
        darker = min(
            inputs.foreground_relative_luminance,
            inputs.background_relative_luminance,
        )
        contrast_ratio = round((lighter + 0.05) / (darker + 0.05), 6)
        containing_regions = [
            item
            for item in evidence.text_safe_regions
            if self._contains(item, inputs.overlay_region)
        ]
        region_minimum = max(
            [item.minimum_contrast_requirement for item in containing_regions]
            or [inputs.minimum_contrast_ratio]
        )
        minimum_contrast = max(inputs.minimum_contrast_ratio, region_minimum)
        contrast_passed = contrast_ratio >= minimum_contrast
        readable_size_passed = (
            inputs.text_fits_without_shrinking
            and inputs.font_size_px >= inputs.minimum_readable_font_size_px
        )
        collision = (
            composition.overlay_collides_with_focal_region
            or composition.overlay_collides_with_protected_region
        )
        reasons: list[str] = []
        if not checksum_bound:
            reasons.append("NATIVE_OVERLAY_IMAGE_CHECKSUM_BINDING_FAILED")
        if not composition.overlay_inside_text_safe_region:
            reasons.append("NATIVE_OVERLAY_SAFE_REGION_BINDING_FAILED")
        if collision:
            reasons.append("NATIVE_OVERLAY_HIDES_MEANING_BEARING_SUBJECT")
        if not contrast_passed:
            reasons.append("NATIVE_OVERLAY_CONTRAST_BELOW_MINIMUM")
        if not readable_size_passed:
            reasons.append("NATIVE_OVERLAY_TEXT_FIT_OR_READABILITY_FAILED")
        if not reasons:
            reasons.append("NATIVE_OVERLAY_COMPLIANCE_PASS")
        payload: dict[str, Any] = {
            "generated_image_sha256": inputs.generated_image_sha256,
            "image_checksum_bound": checksum_bound,
            "native_overlay_plan_ref": inputs.native_overlay_plan_ref,
            "native_overlay_plan_hash": inputs.native_overlay_plan_hash,
            "native_overlay_binding_ref": inputs.native_overlay_binding_ref,
            "native_overlay_binding_hash": inputs.native_overlay_binding_hash,
            "authoritative_text_ref": inputs.authoritative_text_ref,
            "authoritative_text": inputs.authoritative_text,
            "exact_text_native_authority": True,
            "generated_image_owns_final_text": False,
            "overlay_region": inputs.overlay_region,
            "overlay_inside_text_safe_region": composition.overlay_inside_text_safe_region,
            "focal_or_protected_collision": collision,
            "contrast_ratio": contrast_ratio,
            "minimum_contrast_ratio": minimum_contrast,
            "contrast_passed": contrast_passed,
            "text_fits_without_shrinking": inputs.text_fits_without_shrinking,
            "font_size_px": inputs.font_size_px,
            "minimum_readable_font_size_px": inputs.minimum_readable_font_size_px,
            "readable_size_passed": readable_size_passed,
            "reason_codes": reasons,
        }
        return NativeOverlayComplianceEvidence(
            **payload,
            content_hash=ai_image_stable_hash(self._json_payload(payload)),
        )

    def _artifact_gates(
        self,
        *,
        inspection: GeneratedArtifactInspectionEvidence,
        actual_sha256: str | None,
    ) -> list[ImageVisualGateEvidence]:
        return [
            self._artifact_gate(
                "GeneratedTextArtifactGate",
                {"TEXT"},
                inspection,
                actual_sha256,
                "GENERATED_TEXT_ARTIFACT_ABSENT",
            ),
            self._artifact_gate(
                "GeneratedNumberArtifactGate",
                {"NUMBER"},
                inspection,
                actual_sha256,
                "GENERATED_NUMBER_ARTIFACT_ABSENT",
            ),
            self._artifact_gate(
                "FakeUILogoGate",
                {"FAKE_UI", "LOGO"},
                inspection,
                actual_sha256,
                "FAKE_UI_LOGO_ARTIFACT_ABSENT",
            ),
            self._artifact_gate(
                "WatermarkArtifactGate",
                {"WATERMARK"},
                inspection,
                actual_sha256,
                "WATERMARK_ARTIFACT_ABSENT",
            ),
        ]

    def _artifact_gate(
        self,
        gate_name: ImageVisualGateName,
        kinds: set[str],
        inspection: GeneratedArtifactInspectionEvidence,
        actual_sha256: str | None,
        pass_reason: str,
    ) -> ImageVisualGateEvidence:
        matched = [
            item
            for item in inspection.detected_or_suspected_regions
            if item.artifact_kind in kinds
        ]
        if actual_sha256 is None or inspection.image_sha256 != actual_sha256:
            result, reasons, repairability = (
                "BLOCK",
                ["GENERATED_ARTIFACT_INSPECTION_CHECKSUM_MISMATCH"],
                "NOT_REPAIRABLE",
            )
        elif inspection.inspection_state == "PENDING":
            result, reasons, repairability = (
                "REVIEW_REQUIRED",
                ["GENERATED_ARTIFACT_INSPECTION_PENDING"],
                "NOT_REPAIRABLE",
            )
        elif not matched:
            result, reasons, repairability = "PASS", [pass_reason], "NOT_REQUIRED"
        elif any(item.repairability == "NOT_REPAIRABLE" for item in matched):
            result, repairability = "BLOCK", "NOT_REPAIRABLE"
            reasons = self._artifact_reason_codes(matched)
            reasons.append("GENERATED_ARTIFACT_NOT_REPAIRABLE")
        else:
            result, repairability = "REVIEW_REQUIRED", "DETERMINISTIC_NATIVE_REPAIR"
            reasons = self._artifact_reason_codes(matched)
            reasons.append("GENERATED_ARTIFACT_REPAIR_REQUIRED")
        crop_refs = [item.representative_crop_ref for item in matched]
        evidence_refs = crop_refs or [
            self._ref("artifact-inspection", inspection.content_hash)
        ]
        return self._gate(
            gate_name=gate_name,
            result=result,
            reason_codes=list(dict.fromkeys(reasons)),
            authority="CHECKSUM_BOUND_REVIEW",
            repairability=repairability,
            metrics={
                "inspection_state": inspection.inspection_state,
                "inspection_authority": inspection.inspection_authority,
                "matched_region_count": len(matched),
                "detected_regions": [
                    item.model_dump(mode="json") for item in matched
                ],
            },
            evidence_refs=evidence_refs,
        )

    @staticmethod
    def _artifact_reason_codes(regions: Iterable[GeneratedArtifactRegion]) -> list[str]:
        mapping = {
            "TEXT": "GENERATED_TEXT_ARTIFACT",
            "NUMBER": "GENERATED_NUMBER_ARTIFACT",
            "FAKE_UI": "FAKE_UI_RISK",
            "LOGO": "LOGO_OR_TRADEMARK_RISK",
            "WATERMARK": "WATERMARK_RISK",
        }
        return list(dict.fromkeys(mapping[item.artifact_kind] for item in regions))

    def _composition_gate(
        self,
        evidence: CompositionComplianceEvidence,
    ) -> ImageVisualGateEvidence:
        passed = (
            evidence.all_regions_normalized_and_bounded
            and evidence.overlay_inside_text_safe_region
            and not evidence.meaning_bearing_subject_hidden
        )
        return self._gate(
            gate_name="CompositionComplianceGate",
            result="PASS" if passed else "BLOCK",
            reason_codes=evidence.reason_codes,
            authority="DETERMINISTIC",
            repairability="NOT_REQUIRED" if passed else "DETERMINISTIC_NATIVE_REPAIR",
            metrics=evidence.model_dump(mode="json"),
            evidence_refs=[self._ref("composition", evidence.content_hash)],
        )

    def _creative_review_gates(
        self,
        evidence: ImageVisualQualityControlInput,
        actual_sha256: str | None,
    ) -> list[ImageVisualGateEvidence]:
        review = evidence.structured_visual_review
        checksum_bound = actual_sha256 is not None and review.image_sha256 == actual_sha256
        if not checksum_bound:
            semantic_result = style_result = "BLOCK"
            semantic_reasons = style_reasons = [
                "STRUCTURED_VISUAL_REVIEW_CHECKSUM_MISMATCH"
            ]
        else:
            semantic_result = style_result = "REVIEW_REQUIRED"
            semantic_reasons = (
                ["SEMANTIC_CONCERNS_REQUIRE_HUMAN_REVIEW", *review.semantic_concerns]
                if review.semantic_concerns
                else ["SEMANTIC_MATCH_HUMAN_REVIEW_PENDING"]
            )
            style_reasons = (
                ["VISUAL_LANGUAGE_CONCERNS_REQUIRE_HUMAN_REVIEW", *review.style_concerns]
                if review.style_concerns
                else ["VISUAL_LANGUAGE_HUMAN_REVIEW_PENDING"]
            )
        common_metrics = {
            "review_state": review.review_state,
            "scene_meaning": review.scene_meaning,
            "intended_metaphor": review.intended_metaphor,
            "required_composition": review.required_composition,
            "forbidden_interpretations": review.forbidden_interpretations,
            "observed_output_summary": review.observed_output_summary,
            "image_checksum_bound": checksum_bound,
            "metadata_only_pass_allowed": False,
        }
        ref = self._ref("structured-visual-review", review.content_hash)
        return [
            self._gate(
                gate_name="SemanticMatchGate",
                result=semantic_result,
                reason_codes=semantic_reasons,
                authority="CHECKSUM_BOUND_REVIEW",
                repairability="NOT_REPAIRABLE",
                metrics=common_metrics,
                evidence_refs=[ref],
            ),
            self._gate(
                gate_name="VisualLanguageMatchGate",
                result=style_result,
                reason_codes=style_reasons,
                authority="CHECKSUM_BOUND_REVIEW",
                repairability="NOT_REPAIRABLE",
                metrics={
                    **common_metrics,
                    "channel_visual_language": review.channel_visual_language,
                },
                evidence_refs=[ref],
            ),
        ]

    def _crop_gate(self, evidence: CropSafetyEvidence) -> ImageVisualGateEvidence:
        passed = all(
            (
                evidence.target_aspect_ratio_passed,
                evidence.resolution_passed,
                not evidence.upscale_required,
                evidence.subject_focal_region_preserved,
                evidence.protected_visual_regions_preserved,
                evidence.safe_regions_preserved,
            )
        )
        return self._gate(
            gate_name="CropSafetyGate",
            result="PASS" if passed else "BLOCK",
            reason_codes=evidence.reason_codes,
            authority="DETERMINISTIC",
            repairability="NOT_REQUIRED" if passed else "DETERMINISTIC_NATIVE_REPAIR",
            metrics=evidence.model_dump(mode="json"),
            evidence_refs=[self._ref("crop-safety", evidence.content_hash)],
        )

    def _reuse_gate(
        self,
        evidence: ImageVisualQualityControlInput,
        probe: TechnicalImageProbeEvidence,
    ) -> ImageVisualGateEvidence:
        reuse = evidence.reuse_similarity
        actual = probe.checksum_sha256
        if actual is None or reuse.image_sha256 != actual:
            result, reasons = "BLOCK", ["REUSE_EVIDENCE_CHECKSUM_MISMATCH"]
        elif not reuse.comparison_asset_refs:
            result, reasons = "REVIEW_REQUIRED", ["REUSE_COMPARISON_CORPUS_EMPTY"]
        else:
            duplicate_refs = [
                ref
                for ref, checksum in zip(
                    reuse.comparison_asset_refs,
                    reuse.comparison_asset_sha256,
                )
                if checksum == actual
            ]
            if duplicate_refs or reuse.prior_use_count > 1:
                result, reasons = "BLOCK", ["REUSE_SIMILARITY_TOO_HIGH"]
            elif reuse.prior_use_count == 1:
                result, reasons = "REVIEW_REQUIRED", ["REUSE_COUNT_REVIEW_REQUIRED"]
            else:
                result, reasons = "PASS", ["REUSE_SHA256_UNIQUE_IN_COMPARISON_CORPUS"]
        return self._gate(
            gate_name="ReuseSimilarityGate",
            result=result,
            reason_codes=reasons,
            authority="DETERMINISTIC",
            repairability="NOT_REQUIRED" if result == "PASS" else "NOT_REPAIRABLE",
            metrics={
                "comparison_method": reuse.comparison_method,
                "comparison_count": len(reuse.comparison_asset_refs),
                "prior_use_count": reuse.prior_use_count,
                "perceptual_hash_available": False,
            },
            evidence_refs=[self._ref("reuse-similarity", reuse.content_hash)],
        )

    def _continuity_gate(
        self,
        evidence: ImageVisualQualityControlInput,
    ) -> ImageVisualGateEvidence:
        review = evidence.structured_visual_review
        reasons = ["VISUAL_CONTINUITY_ISOLATED_CANARY_HUMAN_REVIEW_PENDING"]
        reasons.extend(review.continuity_concerns)
        return self._gate(
            gate_name="VisualContinuityGate",
            result="REVIEW_REQUIRED",
            reason_codes=list(dict.fromkeys(reasons)),
            authority="CHECKSUM_BOUND_REVIEW",
            repairability="NOT_REPAIRABLE",
            metrics={
                "isolated_canary_scope": True,
                "adjacent_scene_refs": [],
                "multi_scene_continuity_pass_claimed": False,
                "continuity_concerns": review.continuity_concerns,
            },
            evidence_refs=[
                self._ref("structured-visual-review", review.content_hash)
            ],
        )

    def _rights_gate(
        self,
        evidence: ImageVisualQualityControlInput,
        probe: TechnicalImageProbeEvidence,
    ) -> ImageVisualGateEvidence:
        rights = evidence.rights_disclosure
        overlay = evidence.native_overlay
        reasons: list[str] = []
        if rights.output_checksum != probe.checksum_sha256:
            reasons.append("RIGHTS_OUTPUT_CHECKSUM_BINDING_MISMATCH")
        if rights.output_width != probe.width or rights.output_height != probe.height:
            reasons.append("RIGHTS_OUTPUT_DIMENSION_BINDING_MISMATCH")
        if (
            rights.native_overlay_binding_ref != overlay.native_overlay_binding_ref
            or rights.native_overlay_binding_hash != overlay.native_overlay_binding_hash
        ):
            reasons.append("RIGHTS_NATIVE_OVERLAY_BINDING_MISMATCH")
        if rights.provider_call_made:
            request = evidence.provider_request
            approval = evidence.scoped_approval
            attempt = evidence.attempt_ledger
            cost = evidence.cost_estimate
            response = evidence.provider_response
            materialization = evidence.image_materialization
            normalization = evidence.image_normalization
            if any(
                item is None
                for item in (
                    request,
                    approval,
                    attempt,
                    cost,
                    response,
                    materialization,
                    normalization,
                )
            ):
                reasons.append("RIGHTS_TYPED_PROVIDER_BINDINGS_MISSING")
            else:
                assert request is not None
                assert approval is not None
                assert attempt is not None
                assert cost is not None
                assert response is not None
                assert materialization is not None
                assert normalization is not None
                request_hash_valid = request.content_hash == ai_image_stable_hash(
                    request.model_dump(mode="json", exclude={"content_hash"})
                )
                approval_hash_valid = approval.content_hash == ai_image_stable_hash(
                    approval.model_dump(mode="json", exclude={"content_hash"})
                )
                attempt_hash_valid = attempt.content_hash == ai_image_stable_hash(
                    attempt.model_dump(mode="json", exclude={"content_hash"})
                )
                cost_hash_valid = cost.snapshot_hash == ai_image_stable_hash(
                    cost.model_dump(mode="json", exclude={"snapshot_hash"})
                )
                response_hash_valid = response.content_hash == ai_image_stable_hash(
                    response.model_dump(mode="json", exclude={"content_hash"})
                )
                provider_request_lineage_ref = (
                    img_canary_provider_request_lineage_ref(
                        attempt=attempt,
                        response=response,
                    )
                )
                materialization_hash_valid = (
                    materialization.content_hash
                    == ai_image_stable_hash(
                        materialization.model_dump(
                            mode="json",
                            exclude={"content_hash"},
                        )
                    )
                )
                normalization_hash_valid = normalization.content_hash == ai_image_stable_hash(
                    normalization.model_dump(mode="json", exclude={"content_hash"})
                )
                if not all(
                    (
                        request_hash_valid,
                        approval_hash_valid,
                        attempt_hash_valid,
                        cost_hash_valid,
                        response_hash_valid,
                        materialization_hash_valid,
                        normalization_hash_valid,
                    )
                ):
                    reasons.append("RIGHTS_TYPED_PROVIDER_BINDING_HASH_MISMATCH")
                expected_fingerprint = ai_image_stable_hash(
                    {
                        "provider_key": "google_gemini_image",
                        "model_id": request.model_id,
                        "prompt_hash": request.prompt_hash,
                        "reference_asset_hashes": sorted(request.reference_asset_hashes),
                        "image_size": request.image_size,
                        "aspect_ratio": request.aspect_ratio,
                        "output_count": request.output_count,
                        "project_id": request.project_id,
                        "scene_id": request.scene_id,
                        "visual_source_decision_hash": request.visual_source_decision_hash,
                        "native_overlay_plan_hash": request.native_overlay_plan_hash,
                        "approval_scope": request.approval_scope,
                    }
                )
                if not all(
                    (
                        rights.provider == request.provider_route,
                        rights.model == request.model_id == approval.model == response.model,
                        rights.request_hash == request.content_hash == approval.request_hash,
                        rights.prompt_hash == request.prompt_hash == approval.prompt_hash,
                        rights.cost_estimate_ref == request.cost_ref,
                        rights.cost_estimate_hash == cost.snapshot_hash,
                        rights.approval_ref == request.approval_ref == approval.approval_ref,
                        rights.approval_hash == approval.content_hash,
                        rights.attempt_hash == attempt.content_hash,
                        rights.generation_attempts_consumed
                        == attempt.attempts_consumed
                        == response.provider_attempts_consumed
                        == 1,
                        rights.idempotency_key == request.idempotency_key,
                        attempt.idempotency_key_hash
                        == ai_image_stable_hash(request.idempotency_key),
                        attempt.request_fingerprint == expected_fingerprint,
                        attempt.run_id == approval.run_id == response.run_id == evidence.run_id,
                        cost.model_id == request.model_id,
                        cost.image_size == request.image_size,
                        cost.aspect_ratio == request.aspect_ratio,
                        cost.output_count == request.output_count == 1,
                        cost.estimated_amount
                        == approval.estimated_cost_usd
                        == rights.estimated_cost_usd,
                        response.output_count == 1,
                        provider_request_lineage_ref == rights.provider_request_id,
                        response.provider_operation_id_ref
                        == rights.provider_operation_id,
                        attempt.status == "SUCCEEDED",
                        attempt.provider_call_made,
                        provider_request_lineage_ref is not None,
                        attempt.provider_operation_id_ref
                        == response.provider_operation_id_ref,
                    )
                ):
                    reasons.append("RIGHTS_TYPED_PROVIDER_BINDING_MISMATCH")
                materialized_original, materialization_probe_reason = (
                    self._probe_materialized_original(materialization)
                )
                if materialization_probe_reason is not None:
                    reasons.append(materialization_probe_reason)
                elif materialized_original is not None:
                    resolved_materialization_path = self._safe_resolved_path(
                        materialization.local_path
                    )
                    if not all(
                        (
                            resolved_materialization_path is not None,
                            materialized_original["local_path"]
                            == str(resolved_materialization_path),
                            materialized_original["sha256"]
                            == materialization.sha256,
                            materialized_original["size_bytes"]
                            == materialization.size_bytes,
                            materialized_original["image_width"]
                            == materialization.image_width,
                            materialized_original["image_height"]
                            == materialization.image_height,
                            materialized_original["image_format"]
                            == materialization.image_format,
                        )
                    ):
                        reasons.append(
                            "RIGHTS_MATERIALIZED_ORIGINAL_BYTE_BINDING_MISMATCH"
                        )

                if not all(
                    (
                        materialization.run_id == evidence.run_id == response.run_id,
                        materialization.request_hash == request.content_hash,
                        materialization.provider_response_hash == response.content_hash,
                        materialization.provider_request_id_ref
                        == provider_request_lineage_ref
                        == rights.provider_request_id,
                        materialization.provider_operation_id_ref
                        == response.provider_operation_id_ref
                        == rights.provider_operation_id,
                        materialization.estimated_cost_usd
                        == response.estimated_cost_usd
                        == rights.estimated_cost_usd
                        == cost.estimated_amount,
                        materialization.actual_cost_usd
                        == response.actual_cost_usd
                        == rights.actual_cost_usd,
                        materialization.sha256 == response.output_checksum,
                        materialization.image_width == response.image_width,
                        materialization.image_height == response.image_height,
                        materialization.image_format == response.image_format,
                        materialization.size_bytes == response.size_bytes,
                        materialization.provider_call_made,
                        not materialization.raw_url_persisted,
                        not materialization.part_path_remaining,
                    )
                ):
                    reasons.append("RIGHTS_PROVIDER_MATERIALIZATION_CHAIN_MISMATCH")

                source_path = self._safe_resolved_path(normalization.source_path)
                materialized_path = self._safe_resolved_path(materialization.local_path)
                target_path = self._safe_resolved_path(normalization.target_path)
                probed_target_path = self._safe_resolved_path(probe.local_path)
                if not all(
                    (
                        normalization.run_id == evidence.run_id,
                        normalization.request_hash == materialization.request_hash,
                        normalization.provider_response_hash
                        == materialization.provider_response_hash,
                        normalization.provider_request_id_ref
                        == materialization.provider_request_id_ref,
                        normalization.provider_operation_id_ref
                        == materialization.provider_operation_id_ref,
                        normalization.estimated_cost_usd
                        == materialization.estimated_cost_usd,
                        normalization.actual_cost_usd
                        == materialization.actual_cost_usd,
                        normalization.materialization_evidence_hash
                        == materialization.content_hash,
                        source_path is not None,
                        materialized_path is not None,
                        source_path == materialized_path,
                        normalization.source_sha256 == materialization.sha256,
                        normalization.source_size_bytes == materialization.size_bytes,
                        normalization.source_width == materialization.image_width,
                        normalization.source_height == materialization.image_height,
                        normalization.source_format == materialization.image_format,
                        target_path is not None,
                        probed_target_path is not None,
                        target_path == probed_target_path,
                        source_path != target_path,
                        normalization.target_sha256
                        == probe.checksum_sha256
                        == rights.output_checksum
                        == evidence.expected_sha256,
                        normalization.target_size_bytes == probe.file_size_bytes,
                        normalization.target_width
                        == probe.width
                        == rights.output_width,
                        normalization.target_height
                        == probe.height
                        == rights.output_height,
                        normalization.target_format
                        == probe.image_format
                        == evidence.expected_format
                        == "PNG",
                        not normalization.upscale_applied,
                        not normalization.part_path_remaining,
                    )
                ):
                    reasons.append("RIGHTS_NORMALIZATION_TO_VQC_CHAIN_MISMATCH")
        result = "BLOCK" if reasons else "PASS"
        if not reasons:
            reasons.append("RIGHTS_PROVENANCE_DISCLOSURE_COMPLETE")
        return self._gate(
            gate_name="RightsDisclosureCompletenessGate",
            result=result,
            reason_codes=reasons,
            authority="DETERMINISTIC",
            repairability="NOT_REQUIRED" if result == "PASS" else "NOT_REPAIRABLE",
            metrics={
                "provider": rights.provider,
                "vendor": rights.vendor,
                "model": rights.model,
                "provider_call_made": rights.provider_call_made,
                "generation_attempts_consumed": rights.generation_attempts_consumed,
                "generated_evidence_authority": rights.generated_evidence_authority,
                "reference_asset_count": len(rights.reference_asset_refs),
                "real_provider_materialization_bound": bool(
                    rights.provider_call_made
                    and "RIGHTS_PROVIDER_MATERIALIZATION_CHAIN_MISMATCH"
                    not in reasons
                    and "RIGHTS_MATERIALIZED_ORIGINAL_BYTE_BINDING_MISMATCH"
                    not in reasons
                    and "RIGHTS_MATERIALIZED_ORIGINAL_MISSING_OR_INVALID"
                    not in reasons
                    and "RIGHTS_MATERIALIZED_ORIGINAL_FORMAT_UNSUPPORTED"
                    not in reasons
                ),
                "normalization_to_vqc_probe_bound": bool(
                    rights.provider_call_made
                    and "RIGHTS_NORMALIZATION_TO_VQC_CHAIN_MISMATCH" not in reasons
                ),
            },
            evidence_refs=[
                self._ref("rights-disclosure", rights.content_hash),
                *(
                    [
                        self._ref(
                            "image-materialization",
                            evidence.image_materialization.content_hash,
                        ),
                        self._ref(
                            "image-normalization",
                            evidence.image_normalization.content_hash,
                        ),
                    ]
                    if evidence.image_materialization is not None
                    and evidence.image_normalization is not None
                    else []
                ),
            ],
        )

    @staticmethod
    def _probe_materialized_original(
        evidence: VQC1ImageMaterializationEvidence,
    ) -> tuple[dict[str, Any] | None, str | None]:
        candidate = Path(evidence.local_path).expanduser()
        try:
            invalid_candidate = (
                candidate.is_symlink()
                or not candidate.is_file()
                or candidate.stat().st_size <= 0
                or candidate.stat().st_size > MAX_VQC1_IMAGE_BYTES
            )
        except (OSError, RuntimeError):
            invalid_candidate = True
        if invalid_candidate:
            return None, "RIGHTS_MATERIALIZED_ORIGINAL_MISSING_OR_INVALID"
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        try:
            with candidate.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    chunks.append(chunk)
        except OSError:
            return None, "RIGHTS_MATERIALIZED_ORIGINAL_MISSING_OR_INVALID"
        data = b"".join(chunks)
        try:
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                details = ImageTechnicalProbe._decode_png(data)
            elif data.startswith(b"\xff\xd8"):
                details = ImageVisualQualityControlService._decode_jpeg_metadata(data)
            else:
                return None, "RIGHTS_MATERIALIZED_ORIGINAL_FORMAT_UNSUPPORTED"
        except ValueError:
            return None, "RIGHTS_MATERIALIZED_ORIGINAL_MISSING_OR_INVALID"
        resolved_candidate = ImageVisualQualityControlService._safe_resolved_path(
            str(candidate)
        )
        if resolved_candidate is None:
            return None, "RIGHTS_MATERIALIZED_ORIGINAL_MISSING_OR_INVALID"
        return (
            {
                "local_path": str(resolved_candidate),
                "size_bytes": len(data),
                "sha256": digest.hexdigest(),
                "image_width": details["width"],
                "image_height": details["height"],
                "image_format": details["image_format"],
            },
            None,
        )

    @staticmethod
    def _safe_resolved_path(value: str) -> Path | None:
        try:
            return Path(value).expanduser().resolve()
        except (OSError, RuntimeError):
            return None

    @staticmethod
    def _decode_jpeg_metadata(data: bytes) -> dict[str, Any]:
        if len(data) < 8 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
            raise ValueError("VQC1_JPEG_STRUCTURE_INVALID")
        offset = 2
        width = height = 0
        sof_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        while offset < len(data) - 2:
            if data[offset] != 0xFF:
                raise ValueError("VQC1_JPEG_MARKER_INVALID")
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                raise ValueError("VQC1_JPEG_MARKER_TRUNCATED")
            marker = data[offset]
            offset += 1
            if marker == 0xDA:
                break
            if marker in {0x01, 0xD8, 0xD9, *range(0xD0, 0xD8)}:
                continue
            if offset + 2 > len(data):
                raise ValueError("VQC1_JPEG_SEGMENT_TRUNCATED")
            segment_length = int.from_bytes(data[offset : offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                raise ValueError("VQC1_JPEG_SEGMENT_INVALID")
            if marker in sof_markers:
                if segment_length < 8:
                    raise ValueError("VQC1_JPEG_SOF_INVALID")
                height = int.from_bytes(data[offset + 3 : offset + 5], "big")
                width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            offset += segment_length
        if width <= 0 or height <= 0 or width * height > MAX_VQC1_IMAGE_PIXELS:
            raise ValueError("VQC1_JPEG_DIMENSIONS_INVALID")
        return {"image_format": "JPEG", "width": width, "height": height}

    def _native_overlay_gate(
        self,
        evidence: NativeOverlayComplianceEvidence,
    ) -> ImageVisualGateEvidence:
        passed = all(
            (
                evidence.overlay_inside_text_safe_region,
                evidence.image_checksum_bound,
                not evidence.focal_or_protected_collision,
                evidence.contrast_passed,
                evidence.readable_size_passed,
                evidence.exact_text_native_authority,
                not evidence.generated_image_owns_final_text,
            )
        )
        return self._gate(
            gate_name="NativeOverlayComplianceGate",
            result="PASS" if passed else "BLOCK",
            reason_codes=evidence.reason_codes,
            authority="DETERMINISTIC",
            repairability="NOT_REQUIRED" if passed else "DETERMINISTIC_NATIVE_REPAIR",
            metrics=evidence.model_dump(mode="json"),
            evidence_refs=[self._ref("native-overlay", evidence.content_hash)],
        )

    def _human_gate(
        self,
        evidence: ImageVisualQualityControlInput,
        actual_sha256: str | None,
    ) -> ImageVisualGateEvidence:
        human = evidence.human_visual_review
        checksum_bound = actual_sha256 is not None and human.image_sha256 == actual_sha256
        return self._gate(
            gate_name="HumanVisualApprovalGate",
            result="REVIEW_REQUIRED" if checksum_bound else "BLOCK",
            reason_codes=(
                ["HUMAN_VISUAL_APPROVAL_PENDING"]
                if checksum_bound
                else ["HUMAN_VISUAL_REVIEW_CHECKSUM_MISMATCH"]
            ),
            authority="HUMAN_FINAL",
            repairability="NOT_REPAIRABLE",
            metrics={
                "review_state": human.review_state,
                "image_checksum_bound": checksum_bound,
                "checklist_dimensions": [item.dimension for item in human.checklist],
                "human_final_approval_auto_passed": False,
            },
            evidence_refs=[self._ref("human-visual-review", human.content_hash)],
        )

    @staticmethod
    def _gate(
        *,
        gate_name: ImageVisualGateName,
        result: str,
        reason_codes: list[str],
        authority: str,
        repairability: str,
        metrics: dict[str, Any],
        evidence_refs: list[str],
    ) -> ImageVisualGateEvidence:
        payload = {
            "gate_name": gate_name,
            "result": result,
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "metrics": metrics,
            "evidence_refs": list(dict.fromkeys(evidence_refs)),
            "authority": authority,
            "repairability": repairability,
        }
        return ImageVisualGateEvidence(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    @staticmethod
    def _contains(container: Any, item: Any) -> bool:
        epsilon = 1e-9
        return (
            item.x + epsilon >= container.x
            and item.y + epsilon >= container.y
            and item.x + item.width <= container.x + container.width + epsilon
            and item.y + item.height <= container.y + container.height + epsilon
        )

    @staticmethod
    def _intersects(first: Any, second: Any) -> bool:
        return (
            first.x < second.x + second.width
            and first.x + first.width > second.x
            and first.y < second.y + second.height
            and first.y + first.height > second.y
        )

    @staticmethod
    def _ref(kind: str, content_hash: str) -> str:
        return f"evidence://vqc1/{kind}/{content_hash}"

    @staticmethod
    def _json_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value.model_dump(mode="json")
            if hasattr(value, "model_dump")
            else [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in value
            ]
            if isinstance(value, list)
            else value
            for key, value in payload.items()
        }


__all__ = [
    "IMAGE_TECHNICAL_CHECKS",
    "ImageTechnicalProbe",
    "ImageVisualQualityControlService",
    "MAX_VQC1_IMAGE_BYTES",
    "MAX_VQC1_IMAGE_PIXELS",
]
