from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.image_visual_quality_control import (
    HUMAN_VISUAL_REVIEW_DIMENSIONS,
    GeneratedArtifactInspectionEvidence,
    HumanVisualReviewEvidence,
    ImageVisualQualityControlInput,
    ImageVisualQualityControlReport,
    NativeOverlayInputs,
    NormalizedImageRegion,
    PendingHumanVisualChecklistItem,
    ReuseSimilarityEvidence,
    RightsDisclosureEvidence,
    StructuredVisualReviewEvidence,
    VQC1ImageMaterializationEvidence,
    VQC1ImageNormalizationEvidence,
    img_canary_provider_request_lineage_ref,
)
from app.contracts.img_canary import (
    IMGCanaryAttemptLedger,
    IMGCanaryProviderResponseSummary,
)
from app.providers.google_gemini_image import GoogleGeminiImageAdapter
from app.services.image_visual_quality_control import (
    ImageVisualQualityControlService,
)
from app.services.img_canary import IMGCanaryPlanBundle
from app.services.native_ffmpeg_renderer import (
    FFMPEG_FULL_DEFAULT,
    IMG_CANARY_OVERLAY_PANEL_RGB,
    srgb_hex_relative_luminance,
)


IMG_CANARY_REPRESENTATIVE_CROP_ROLES = (
    "IMG_CANARY_QC_CROP_FULL_FRAME",
    "IMG_CANARY_QC_CROP_OVERLAY_SAFE",
    "IMG_CANARY_QC_CROP_SUBJECT_FOCAL",
)
_CROP_FILENAMES = {
    "IMG_CANARY_QC_CROP_FULL_FRAME": "full-frame.png",
    "IMG_CANARY_QC_CROP_OVERLAY_SAFE": "overlay-safe.png",
    "IMG_CANARY_QC_CROP_SUBJECT_FOCAL": "subject-focal.png",
}


def _bound(model_class: type[Any], **payload: Any) -> Any:
    return model_class(
        **payload,
        content_hash=ai_image_stable_hash(payload),
    )


def img_canary_representative_crop_paths(image_path: Path) -> dict[str, Path]:
    """Return the canonical crop paths derived from one normalized image path."""

    image = image_path.resolve(strict=False)
    crop_root = image.parent.parent / "qc-crops"
    return {
        role: crop_root / _CROP_FILENAMES[role]
        for role in IMG_CANARY_REPRESENTATIVE_CROP_ROLES
    }


def img_canary_representative_crop_manifest_path(image_path: Path) -> Path:
    image = image_path.resolve(strict=False)
    return image.parent.parent / "qc-crops" / "qc-crops-manifest.json"


class IMGCanaryRepresentativeCropBuilder:
    """Create restart-safe, checksum-bound real-pixel review crops.

    The generated PNGs are deterministic derivatives of the normalized image.
    A valid existing manifest is returned byte-for-byte without rewriting files.
    Corrupt derived crops are repaired from the same bound source; a different
    source at the same canonical path is a hard conflict.
    """

    def __init__(self, *, ffmpeg: str = FFMPEG_FULL_DEFAULT):
        self.ffmpeg = ffmpeg

    def build(
        self,
        *,
        run_id: str,
        image_path: Path,
        overlay_safe_region: Any,
        subject_focal_region: Any,
    ) -> dict[str, Any]:
        image = image_path.resolve(strict=True)
        if image.is_symlink() or not image.is_file():
            raise ValueError("IMG_CANARY_QC_CROP_SOURCE_INVALID")
        width, height, image_format = GoogleGeminiImageAdapter.probe_image(image)
        if (width, height, image_format) != (1920, 1080, "PNG"):
            raise ValueError("IMG_CANARY_QC_CROP_SOURCE_NOT_NORMALIZED")
        source_sha256 = GoogleGeminiImageAdapter._file_sha256(image)
        output_paths = img_canary_representative_crop_paths(image)
        manifest_path = img_canary_representative_crop_manifest_path(image)
        crop_root = manifest_path.parent
        workspace_root = image.parent.parent.resolve()
        if (
            (crop_root.exists() and crop_root.is_symlink())
            or crop_root.resolve(strict=False).parent != workspace_root
            or any(path.exists() and path.is_symlink() for path in output_paths.values())
        ):
            raise ValueError("IMG_CANARY_QC_CROP_PATH_ESCAPE_OR_SYMLINK")
        specs = {
            "IMG_CANARY_QC_CROP_FULL_FRAME": (0.0, 0.0, 1.0, 1.0),
            "IMG_CANARY_QC_CROP_OVERLAY_SAFE": self._region_tuple(
                overlay_safe_region
            ),
            "IMG_CANARY_QC_CROP_SUBJECT_FOCAL": self._region_tuple(
                subject_focal_region
            ),
        }
        expected_boxes = {
            role: self._pixel_box(spec, width=width, height=height)
            for role, spec in specs.items()
        }

        if manifest_path.exists():
            persisted = self._load_manifest(manifest_path)
            self._validate_manifest_binding(
                persisted,
                run_id=run_id,
                image=image,
                source_sha256=source_sha256,
                width=width,
                height=height,
                specs=specs,
                boxes=expected_boxes,
                output_paths=output_paths,
            )
            if self._all_outputs_valid(persisted):
                return persisted

        crop_root.mkdir(parents=True, exist_ok=True)
        for role in IMG_CANARY_REPRESENTATIVE_CROP_ROLES:
            destination = output_paths[role]
            box = expected_boxes[role]
            if role == "IMG_CANARY_QC_CROP_FULL_FRAME":
                self._copy_atomic(image, destination)
            else:
                self._crop_atomic(image, destination, box=box)

        crop_entries: list[dict[str, Any]] = []
        for role in IMG_CANARY_REPRESENTATIVE_CROP_ROLES:
            path = output_paths[role].resolve(strict=True)
            crop_width, crop_height, crop_format = GoogleGeminiImageAdapter.probe_image(
                path
            )
            box = expected_boxes[role]
            if (crop_width, crop_height, crop_format) != (
                box[2],
                box[3],
                "PNG",
            ):
                raise RuntimeError("IMG_CANARY_QC_CROP_OUTPUT_INVALID")
            crop_entries.append(
                {
                    "logical_role": role,
                    "artifact_ref": str(path),
                    "source_image_sha256": source_sha256,
                    "normalized_region": {
                        "x": specs[role][0],
                        "y": specs[role][1],
                        "width": specs[role][2],
                        "height": specs[role][3],
                    },
                    "pixel_box": {
                        "x": box[0],
                        "y": box[1],
                        "width": box[2],
                        "height": box[3],
                    },
                    "width": crop_width,
                    "height": crop_height,
                    "image_format": crop_format,
                    "size_bytes": path.stat().st_size,
                    "sha256": GoogleGeminiImageAdapter._file_sha256(path),
                }
            )
        payload = {
            "schema_version": "img-canary-qc-crops.v1",
            "run_id": run_id,
            "normalized_image": {
                "path": str(image),
                "sha256": source_sha256,
                "size_bytes": image.stat().st_size,
                "width": width,
                "height": height,
                "image_format": image_format,
            },
            "crops": crop_entries,
            "all_crops_real_pixel_derivatives": True,
            "human_artifact_absence_claimed": False,
        }
        manifest = {**payload, "content_hash": ai_image_stable_hash(payload)}
        self._write_json_atomic(manifest_path, manifest)
        return manifest

    @staticmethod
    def _region_tuple(region: Any) -> tuple[float, float, float, float]:
        values = tuple(float(getattr(region, key)) for key in ("x", "y", "width", "height"))
        x, y, width, height = values
        if (
            x < 0.0
            or y < 0.0
            or width <= 0.0
            or height <= 0.0
            or x + width > 1.0
            or y + height > 1.0
        ):
            raise ValueError("IMG_CANARY_QC_CROP_REGION_OUT_OF_BOUNDS")
        return values

    @staticmethod
    def _pixel_box(
        region: tuple[float, float, float, float],
        *,
        width: int,
        height: int,
    ) -> tuple[int, int, int, int]:
        x = round(width * region[0])
        y = round(height * region[1])
        right = round(width * (region[0] + region[2]))
        bottom = round(height * (region[1] + region[3]))
        crop_width = min(width, right) - x
        crop_height = min(height, bottom) - y
        if x < 0 or y < 0 or crop_width <= 0 or crop_height <= 0:
            raise ValueError("IMG_CANARY_QC_CROP_PIXEL_BOX_INVALID")
        return x, y, crop_width, crop_height

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        if not path.is_file() or path.is_symlink():
            raise FileExistsError("IMG_CANARY_QC_CROP_MANIFEST_INVALID")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileExistsError("IMG_CANARY_QC_CROP_MANIFEST_INVALID") from exc
        if not isinstance(payload, dict):
            raise FileExistsError("IMG_CANARY_QC_CROP_MANIFEST_INVALID")
        content_hash = payload.get("content_hash")
        if content_hash != ai_image_stable_hash(
            {key: value for key, value in payload.items() if key != "content_hash"}
        ):
            raise FileExistsError("IMG_CANARY_QC_CROP_MANIFEST_HASH_MISMATCH")
        return payload

    @staticmethod
    def _validate_manifest_binding(
        persisted: dict[str, Any],
        *,
        run_id: str,
        image: Path,
        source_sha256: str,
        width: int,
        height: int,
        specs: dict[str, tuple[float, float, float, float]],
        boxes: dict[str, tuple[int, int, int, int]],
        output_paths: dict[str, Path],
    ) -> None:
        normalized = persisted.get("normalized_image")
        crop_items = persisted.get("crops")
        if not isinstance(normalized, dict) or not isinstance(crop_items, list):
            raise FileExistsError("IMG_CANARY_QC_CROP_MANIFEST_BINDING_INVALID")
        by_role = {
            str(item.get("logical_role")): item
            for item in crop_items
            if isinstance(item, dict)
        }
        expected_source = {
            "path": str(image),
            "sha256": source_sha256,
            "size_bytes": image.stat().st_size,
            "width": width,
            "height": height,
            "image_format": "PNG",
        }
        if (
            persisted.get("schema_version") != "img-canary-qc-crops.v1"
            or persisted.get("run_id") != run_id
            or normalized != expected_source
            or set(by_role) != set(IMG_CANARY_REPRESENTATIVE_CROP_ROLES)
            or len(crop_items) != len(by_role)
        ):
            raise FileExistsError("IMG_CANARY_QC_CROP_SOURCE_CONFLICT")
        for role in IMG_CANARY_REPRESENTATIVE_CROP_ROLES:
            item = by_role[role]
            expected_region = dict(
                zip(("x", "y", "width", "height"), specs[role], strict=True)
            )
            expected_box = dict(
                zip(("x", "y", "width", "height"), boxes[role], strict=True)
            )
            if (
                item.get("artifact_ref") != str(output_paths[role].resolve())
                or item.get("source_image_sha256") != source_sha256
                or item.get("normalized_region") != expected_region
                or item.get("pixel_box") != expected_box
            ):
                raise FileExistsError("IMG_CANARY_QC_CROP_SOURCE_CONFLICT")

    @staticmethod
    def _all_outputs_valid(persisted: dict[str, Any]) -> bool:
        try:
            for item in persisted["crops"]:
                path = Path(item["artifact_ref"])
                if not path.is_file() or path.is_symlink():
                    return False
                width, height, image_format = GoogleGeminiImageAdapter.probe_image(path)
                if (
                    (width, height, image_format)
                    != (item["width"], item["height"], "PNG")
                    or path.stat().st_size != item["size_bytes"]
                    or GoogleGeminiImageAdapter._file_sha256(path) != item["sha256"]
                ):
                    return False
        except (KeyError, OSError, ValueError, TypeError):
            return False
        return True

    @staticmethod
    def _copy_atomic(source: Path, destination: Path) -> None:
        part = destination.with_name(destination.name + ".part")
        part.unlink(missing_ok=True)
        try:
            with source.open("rb") as input_stream, part.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            os.replace(part, destination)
            IMGCanaryRepresentativeCropBuilder._fsync_directory(destination.parent)
        finally:
            part.unlink(missing_ok=True)

    def _crop_atomic(
        self,
        source: Path,
        destination: Path,
        *,
        box: tuple[int, int, int, int],
    ) -> None:
        x, y, width, height = box
        part = destination.with_name(destination.stem + ".part.png")
        part.unlink(missing_ok=True)
        argv = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"crop={width}:{height}:{x}:{y},format=rgb24",
            "-map_metadata",
            "-1",
            "-frames:v",
            "1",
            "-threads",
            "1",
            "-fflags",
            "+bitexact",
            "-flags:v",
            "+bitexact",
            "-compression_level",
            "9",
            str(part),
        ]
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                shell=False,
            )
            if completed.returncode != 0 or not part.is_file():
                raise RuntimeError(
                    f"IMG_CANARY_QC_CROP_FFMPEG_FAILED:{completed.returncode}"
                )
            with part.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(part, destination)
            self._fsync_directory(destination.parent)
        finally:
            part.unlink(missing_ok=True)

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        part = path.with_name(path.name + ".part")
        part.unlink(missing_ok=True)
        try:
            with part.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(part, path)
            IMGCanaryRepresentativeCropBuilder._fsync_directory(path.parent)
        finally:
            part.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class IMGCanaryVQCEvidenceBuilder:
    """Build checksum-bound real-image VQC evidence without creative auto-PASS."""

    def __init__(self, *, ffmpeg: str = FFMPEG_FULL_DEFAULT):
        self.ffmpeg = ffmpeg

    def build_and_evaluate(
        self,
        *,
        bundle: IMGCanaryPlanBundle,
        normalized_image_path: Path,
        provider_response: IMGCanaryProviderResponseSummary,
        attempt_ledger: IMGCanaryAttemptLedger,
        materialization_receipt: dict[str, Any],
        normalization_receipt: dict[str, Any],
        comparison_asset_refs: list[str] | None = None,
        comparison_asset_sha256: list[str] | None = None,
        observed_output_summary: str = (
            "Real canary output is technically inspected; semantic and visual-language "
            "judgment remains pending human review."
        ),
        now: datetime | None = None,
    ) -> tuple[ImageVisualQualityControlInput, ImageVisualQualityControlReport]:
        evidence = self.build(
            bundle=bundle,
            normalized_image_path=normalized_image_path,
            provider_response=provider_response,
            attempt_ledger=attempt_ledger,
            materialization_receipt=materialization_receipt,
            normalization_receipt=normalization_receipt,
            comparison_asset_refs=comparison_asset_refs,
            comparison_asset_sha256=comparison_asset_sha256,
            observed_output_summary=observed_output_summary,
            now=now,
        )
        report = ImageVisualQualityControlService().evaluate(
            image_path=normalized_image_path,
            evidence=evidence,
        )
        return evidence, report

    def build(
        self,
        *,
        bundle: IMGCanaryPlanBundle,
        normalized_image_path: Path,
        provider_response: IMGCanaryProviderResponseSummary,
        attempt_ledger: IMGCanaryAttemptLedger,
        materialization_receipt: dict[str, Any],
        normalization_receipt: dict[str, Any],
        comparison_asset_refs: list[str] | None = None,
        comparison_asset_sha256: list[str] | None = None,
        observed_output_summary: str,
        now: datetime | None = None,
    ) -> ImageVisualQualityControlInput:
        timestamp = now or datetime.now(UTC)
        if timestamp.tzinfo is None:
            raise ValueError("IMG_CANARY_VQC_TIMEZONE_REQUIRED")
        image = normalized_image_path.resolve(strict=True)
        width, height, image_format = GoogleGeminiImageAdapter.probe_image(image)
        checksum = GoogleGeminiImageAdapter._file_sha256(image)
        if (width, height, image_format) != (1920, 1080, "PNG"):
            raise ValueError("IMG_CANARY_VQC_NORMALIZED_IMAGE_INVALID")
        if (
            provider_response.run_id != bundle.run_identity.run_id
            or attempt_ledger.run_id != bundle.run_identity.run_id
            or attempt_ledger.status != "SUCCEEDED"
            or attempt_ledger.attempts_consumed != 1
            or not attempt_ledger.provider_call_made
        ):
            raise ValueError("IMG_CANARY_VQC_PROVIDER_STATE_INVALID")
        if provider_response.content_hash != ai_image_stable_hash(
            provider_response.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("IMG_CANARY_VQC_PROVIDER_RESPONSE_HASH_MISMATCH")
        if attempt_ledger.content_hash != ai_image_stable_hash(
            attempt_ledger.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("IMG_CANARY_VQC_ATTEMPT_HASH_MISMATCH")
        provider_request_lineage_ref = img_canary_provider_request_lineage_ref(
            attempt=attempt_ledger,
            response=provider_response,
        )
        if provider_request_lineage_ref is None:
            raise ValueError("IMG_CANARY_VQC_PROVIDER_REQUEST_ID_REF_REQUIRED")

        materialization_hash = ai_image_stable_hash(materialization_receipt)
        materialization_payload = {
            "run_id": bundle.run_identity.run_id,
            "request_hash": bundle.provider_request.content_hash,
            "provider_response_hash": provider_response.content_hash,
            "provider_request_id_ref": provider_request_lineage_ref,
            "provider_operation_id_ref": attempt_ledger.provider_operation_id_ref,
            "estimated_cost_usd": bundle.cost.estimated_amount,
            "actual_cost_usd": provider_response.actual_cost_usd,
            "materialization_receipt_ref": (
                f"materialization://img-canary/{bundle.run_identity.run_id}/original"
            ),
            "materialization_receipt_hash": materialization_hash,
            **materialization_receipt,
        }
        materialization = _bound(
            VQC1ImageMaterializationEvidence,
            **materialization_payload,
        )
        normalization_hash = normalization_receipt.get("content_hash")
        if not isinstance(normalization_hash, str):
            raise ValueError("IMG_CANARY_VQC_NORMALIZATION_RECEIPT_HASH_REQUIRED")
        normalization_payload = {
            "run_id": bundle.run_identity.run_id,
            "request_hash": bundle.provider_request.content_hash,
            "provider_response_hash": provider_response.content_hash,
            "provider_request_id_ref": provider_request_lineage_ref,
            "provider_operation_id_ref": attempt_ledger.provider_operation_id_ref,
            "estimated_cost_usd": bundle.cost.estimated_amount,
            "actual_cost_usd": provider_response.actual_cost_usd,
            "materialization_evidence_hash": materialization.content_hash,
            "normalization_receipt_ref": (
                f"normalization://img-canary/{bundle.run_identity.run_id}/review-png"
            ),
            "normalization_receipt_hash": normalization_hash,
            "source_size_bytes": int(materialization_receipt["size_bytes"]),
            "target_size_bytes": image.stat().st_size,
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

        safe = bundle.overlay_plan.text_safe_regions[0]
        overlay_region = NormalizedImageRegion(
            region_id="img-canary-native-headline-region",
            region_role="NATIVE_OVERLAY",
            x=safe.x + 0.01,
            y=safe.y + 0.01,
            width=safe.width - 0.02,
            height=safe.height - 0.02,
        )
        overlay_binding_ref = (
            f"overlay-binding://img-canary/{bundle.run_identity.run_id}/normalized-image"
        )
        overlay_binding_hash = ai_image_stable_hash(
            {
                "run_id": bundle.run_identity.run_id,
                "image_sha256": checksum,
                "overlay_plan_hash": bundle.overlay_plan.content_hash,
                "headline_hash": bundle.headline.content_hash,
            }
        )
        subject_focal_region = NormalizedImageRegion(
            region_id="img-canary-subject-focal",
            region_role="SUBJECT_FOCAL",
            x=0.60,
            y=0.20,
            width=0.28,
            height=0.52,
        )
        crop_manifest = IMGCanaryRepresentativeCropBuilder(
            ffmpeg=self.ffmpeg
        ).build(
            run_id=bundle.run_identity.run_id,
            image_path=image,
            overlay_safe_region=safe,
            subject_focal_region=subject_focal_region,
        )
        representative_crop_refs = [
            str(item["artifact_ref"]) for item in crop_manifest["crops"]
        ]
        overlay = _bound(
            NativeOverlayInputs,
            generated_image_sha256=checksum,
            native_overlay_plan_ref=bundle.overlay_plan.plan_id,
            native_overlay_plan_hash=bundle.overlay_plan.content_hash,
            native_overlay_binding_ref=overlay_binding_ref,
            native_overlay_binding_hash=overlay_binding_hash,
            authoritative_text_ref=bundle.headline.artifact_ref,
            authoritative_text=bundle.headline.exact_text,
            exact_text_native_authority=True,
            generated_image_owns_final_text=False,
            overlay_region=overlay_region,
            foreground_relative_luminance=1.0,
            background_relative_luminance=srgb_hex_relative_luminance(
                IMG_CANARY_OVERLAY_PANEL_RGB
            ),
            minimum_contrast_ratio=4.5,
            font_size_px=max(42, min(64, round(min(width, height) * 0.055))),
            minimum_readable_font_size_px=44,
            text_fits_without_shrinking=True,
        )
        artifact = _bound(
            GeneratedArtifactInspectionEvidence,
            image_sha256=checksum,
            inspection_state="PENDING",
            inspection_authority="UNASSESSED",
            detected_or_suspected_regions=[],
            representative_crop_refs=representative_crop_refs,
            review_notes=(
                "Real-pixel representative crops are checksum-bound to the normalized "
                "image; human artifact observation remains pending and no absence claim "
                "is inferred from metadata."
            ),
        )
        refs = list(comparison_asset_refs or [])
        hashes = list(comparison_asset_sha256 or [])
        reuse = _bound(
            ReuseSimilarityEvidence,
            image_sha256=checksum,
            comparison_method="SHA256_EXACT",
            comparison_asset_refs=refs,
            comparison_asset_sha256=hashes,
            prior_use_count=0,
            isolated_canary_scope=True,
            perceptual_hash_available=False,
        )
        visual = _bound(
            StructuredVisualReviewEvidence,
            image_sha256=checksum,
            review_state="PENDING",
            scene_meaning=(
                "Information is fragmented across disconnected locations and nobody "
                "sees the whole picture."
            ),
            intended_metaphor=(
                "Separated knowledge islands with one coherent focal composition."
            ),
            required_composition=[
                "one coherent focal system on the right",
                "large clean native-headline region on the left",
            ],
            forbidden_interpretations=[
                "software UI",
                "science-fiction magic",
                "authoritative generated text or numbers",
            ],
            channel_visual_language=[
                "clean editorial geometry",
                "restrained modern business explainer",
                "slate teal and warm amber palette",
            ],
            observed_output_summary=observed_output_summary,
            semantic_concerns=[],
            style_concerns=[],
            continuity_concerns=[],
            isolated_canary_scope=True,
            adjacent_scene_refs=[],
            semantic_pass_from_metadata_allowed=False,
            visual_language_pass_from_metadata_allowed=False,
        )
        human = _bound(
            HumanVisualReviewEvidence,
            image_sha256=checksum,
            review_state="PENDING",
            reviewer=None,
            final_decision=None,
            checklist=[
                PendingHumanVisualChecklistItem(dimension=dimension)
                for dimension in HUMAN_VISUAL_REVIEW_DIMENSIONS
            ],
            human_final_approval_auto_passed=False,
        )
        rights = _bound(
            RightsDisclosureEvidence,
            provider="google_gemini_image",
            vendor="google",
            model=bundle.provider_request.model_id,
            request_hash=bundle.provider_request.content_hash,
            prompt_hash=bundle.provider_request.prompt_hash,
            reference_asset_refs=[],
            reference_asset_rights_refs=[],
            generation_timestamp=provider_response.completed_at or timestamp,
            provider_request_id=provider_request_lineage_ref,
            provider_operation_id=attempt_ledger.provider_operation_id_ref,
            output_checksum=checksum,
            output_width=width,
            output_height=height,
            cost_estimate_ref=bundle.provider_request.cost_ref,
            cost_estimate_hash=bundle.cost.snapshot_hash,
            estimated_cost_usd=bundle.cost.estimated_amount,
            actual_usage_ref=(
                f"usage://img-canary/{bundle.run_identity.run_id}/provider-summary"
            ),
            actual_cost_usd=provider_response.actual_cost_usd,
            approval_ref=bundle.approval.approval_ref,
            approval_hash=bundle.approval.content_hash,
            attempt_ref=(
                f"attempt://img-canary/{bundle.run_identity.run_id}/one-shot"
            ),
            attempt_hash=attempt_ledger.content_hash,
            generation_attempts_consumed=1,
            idempotency_key=bundle.provider_request.idempotency_key,
            scene_usage_refs=[
                f"scene://img-canary/{bundle.run_identity.run_id}/fragmented-information"
            ],
            native_overlay_binding_ref=overlay_binding_ref,
            native_overlay_binding_hash=overlay_binding_hash,
            synthetic_media_disclosure_ref=(
                f"disclosure://img-canary/{bundle.run_identity.run_id}/synthetic-media"
            ),
            generated_evidence_authority=False,
            provider_call_made=True,
        )
        payload = {
            "run_id": bundle.run_identity.run_id,
            "image_ref": (
                f"artifact://img-canary/{bundle.run_identity.run_id}/normalized.png"
            ),
            "expected_sha256": checksum,
            "expected_format": "PNG",
            "target_aspect_ratio": "16:9",
            "minimum_effective_width": 1920,
            "minimum_effective_height": 1080,
            "expected_alpha_behavior": "NONE",
            "intended_crop": NormalizedImageRegion(
                region_id="img-canary-review-crop",
                region_role="INTENDED_CROP",
                x=0.0,
                y=0.0,
                width=1.0,
                height=1.0,
            ),
            "text_safe_regions": list(bundle.overlay_plan.text_safe_regions),
            "reserved_overlay_regions": list(
                bundle.overlay_plan.reserved_overlay_regions
            ),
            "subject_focal_region": subject_focal_region,
            "protected_visual_regions": [
                NormalizedImageRegion(
                    region_id="img-canary-protected-subject",
                    region_role="PROTECTED_VISUAL",
                    x=0.56,
                    y=0.16,
                    width=0.36,
                    height=0.60,
                )
            ],
            "artifact_inspection": artifact,
            "native_overlay": overlay,
            "reuse_similarity": reuse,
            "structured_visual_review": visual,
            "rights_disclosure": rights,
            "provider_request": bundle.provider_request,
            "scoped_approval": bundle.approval,
            "attempt_ledger": attempt_ledger,
            "cost_estimate": bundle.cost,
            "provider_response": provider_response,
            "image_materialization": materialization,
            "image_normalization": normalization,
            "human_visual_review": human,
        }
        return _bound(ImageVisualQualityControlInput, **payload)


__all__ = [
    "IMG_CANARY_REPRESENTATIVE_CROP_ROLES",
    "IMGCanaryRepresentativeCropBuilder",
    "IMGCanaryVQCEvidenceBuilder",
    "img_canary_representative_crop_manifest_path",
    "img_canary_representative_crop_paths",
]
