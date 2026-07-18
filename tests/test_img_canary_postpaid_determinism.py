from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.img_canary import IMGCanaryRunIdentity
from app.contracts.native_renderer import (
    FFmpegCommandManifest,
    MediaQCReport,
    NativeRenderExecutionReceipt,
)
from app.services.img_canary_vqc import (
    IMG_CANARY_REPRESENTATIVE_CROP_ROLES,
    IMGCanaryRepresentativeCropBuilder,
    img_canary_representative_crop_manifest_path,
    img_canary_representative_crop_paths,
)
from app.services.img_canary_drive import _validate_manifest_and_sources
from app.services.img_canary_runner import IMGCanaryControlledRunner
from app.services.native_ffmpeg_renderer import (
    FFMPEG_FULL_DEFAULT,
    IMG_CANARY_OVERLAY_PANEL_OPACITY,
    IMG_CANARY_OVERLAY_PANEL_RGB,
    _load_completed_render,
    _persist_or_reuse_command,
    srgb_hex_relative_luminance,
)
from app.services.native_render_plan import stable_hash
from app.services.production_archive import (
    IMG_CANARY_ROLE_ARCHIVE_PATHS,
    IMG_CANARY_V1_REQUIRED_ARCHIVE_ROLES,
    IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES,
    IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES,
    ArchiveSource,
    IMGCanaryArchivePathBuilder,
    ProductionArchiveBuilder,
)


def _write_png(path: Path, *, color: str) -> None:
    if not Path(FFMPEG_FULL_DEFAULT).is_file():
        pytest.skip("ffmpeg-full is unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            FFMPEG_FULL_DEFAULT,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=1920x1080",
            "-frames:v",
            "1",
            "-threads",
            "1",
            str(path),
        ],
        capture_output=True,
        text=True,
        shell=False,
    )
    assert completed.returncode == 0, completed.stderr


def _command(tmp_path: Path, *, created_at: datetime, run_key: str = "img-canary-v2-run") -> FFmpegCommandManifest:
    work = tmp_path / "runs" / run_key
    core = {
        "run_key": run_key,
        "compiled_manifest_ref": "compiled-ref",
        "compiled_manifest_hash": "compiled-hash",
        "ffmpeg_binary_path": FFMPEG_FULL_DEFAULT,
        "ffprobe_binary_path": str(Path(FFMPEG_FULL_DEFAULT).with_name("ffprobe")),
        "ffmpeg_version": "fixture",
        "command_builder_version": "fixture-v1",
        "input_files": [str(tmp_path / "source.png")],
        "generated_filtergraph_path": str(work / "filtergraph.txt"),
        "generated_text_files": [str(work / "headline.txt")],
        "generated_caption_path": None,
        "generated_file_checksums": {str(work / "filtergraph.txt"): "a" * 64},
        "output_file": str(work / "img-canary-review.mp4"),
        "output_profile": "YT_LONG_1080P30_SDR_H264",
        "sanitized_argv": [FFMPEG_FULL_DEFAULT, "-version"],
        "working_directory": str(work),
        "expected_qc": {"width": 1920, "height": 1080},
        "temporal_authority_mode": "LEGACY_HISTORICAL",
        "canonical_media_timeline_ref": None,
        "canonical_media_timeline_hash": None,
        "canonical_audio_asset_ref": None,
        "canonical_duration_ms": None,
        "canonical_caption_compilation_ref": None,
        "canonical_caption_compilation_hash": None,
        "canonical_caption_render_payload_hash": None,
    }
    return FFmpegCommandManifest(
        **core,
        command_hash=stable_hash(core),
        created_at=created_at,
    )


def test_real_pixel_crops_are_checksum_bound_and_restart_idempotent(tmp_path: Path) -> None:
    image = tmp_path / "run" / "source" / "normalized-1920x1080.png"
    _write_png(image, color="0x244466")
    builder = IMGCanaryRepresentativeCropBuilder(ffmpeg=FFMPEG_FULL_DEFAULT)
    overlay = SimpleNamespace(x=0.04, y=0.12, width=0.44, height=0.48)
    subject = SimpleNamespace(x=0.60, y=0.20, width=0.28, height=0.52)

    first = builder.build(
        run_id="img-canary-v2-20260718T120000Z-deadbeef",
        image_path=image,
        overlay_safe_region=overlay,
        subject_focal_region=subject,
    )
    paths = img_canary_representative_crop_paths(image)
    before = {role: paths[role].read_bytes() for role in paths}
    manifest_bytes = img_canary_representative_crop_manifest_path(image).read_bytes()
    second = builder.build(
        run_id="img-canary-v2-20260718T120000Z-deadbeef",
        image_path=image,
        overlay_safe_region=overlay,
        subject_focal_region=subject,
    )

    assert first == second
    assert set(paths) == set(IMG_CANARY_REPRESENTATIVE_CROP_ROLES)
    assert manifest_bytes == img_canary_representative_crop_manifest_path(image).read_bytes()
    assert before == {role: paths[role].read_bytes() for role in paths}
    assert first["content_hash"] == ai_image_stable_hash(
        {key: value for key, value in first.items() if key != "content_hash"}
    )
    assert all(
        item["source_image_sha256"] == first["normalized_image"]["sha256"]
        and item["sha256"] == hashlib.sha256(Path(item["artifact_ref"]).read_bytes()).hexdigest()
        for item in first["crops"]
    )

    paths["IMG_CANARY_QC_CROP_OVERLAY_SAFE"].write_bytes(b"corrupt-derived-crop")
    repaired = builder.build(
        run_id="img-canary-v2-20260718T120000Z-deadbeef",
        image_path=image,
        overlay_safe_region=overlay,
        subject_focal_region=subject,
    )
    assert repaired == first
    assert paths["IMG_CANARY_QC_CROP_OVERLAY_SAFE"].read_bytes() == before[
        "IMG_CANARY_QC_CROP_OVERLAY_SAFE"
    ]

    _write_png(image, color="0x662244")
    with pytest.raises(FileExistsError, match="QC_CROP_SOURCE_CONFLICT"):
        builder.build(
            run_id="img-canary-v2-20260718T120000Z-deadbeef",
            image_path=image,
            overlay_safe_region=overlay,
            subject_focal_region=subject,
        )


def test_native_overlay_uses_actual_opaque_panel_luminance() -> None:
    background = srgb_hex_relative_luminance(IMG_CANARY_OVERLAY_PANEL_RGB)
    contrast = (1.0 + 0.05) / (background + 0.05)

    assert IMG_CANARY_OVERLAY_PANEL_OPACITY == 1.0
    assert background > 0.0
    assert contrast >= 4.5
    with pytest.raises(ValueError, match="SRGB_HEX_COLOR_INVALID"):
        srgb_hex_relative_luminance("not-a-color")


def test_image_review_command_identity_reuses_original_timestamp(tmp_path: Path) -> None:
    first_candidate = _command(tmp_path, created_at=datetime(2026, 7, 18, tzinfo=UTC))
    work = Path(first_candidate.working_directory)
    first = _persist_or_reuse_command(work=work, candidate=first_candidate)
    manifest_bytes = (work / "command_manifest.json").read_bytes()
    second_candidate = _command(
        tmp_path,
        created_at=datetime(2026, 7, 18, tzinfo=UTC) + timedelta(minutes=5),
    )
    second = _persist_or_reuse_command(work=work, candidate=second_candidate)

    assert second == first
    assert second.created_at == first_candidate.created_at
    assert (work / "command_manifest.json").read_bytes() == manifest_bytes

    conflict = _command(
        tmp_path,
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
        run_key="img-canary-v2-other-run",
    )
    conflict = conflict.model_copy(
        update={
            "working_directory": str(work),
            "output_file": str(work / "img-canary-review.mp4"),
        }
    )
    with pytest.raises(FileExistsError, match="COMMAND_MANIFEST_IDENTITY_CONFLICT"):
        _persist_or_reuse_command(work=work, candidate=conflict)


def test_completed_render_receipt_prevents_second_render(tmp_path: Path) -> None:
    command = _command(tmp_path, created_at=datetime(2026, 7, 18, tzinfo=UTC))
    work = Path(command.working_directory)
    work.mkdir(parents=True)
    output = Path(command.output_file)
    output.write_bytes(b"deterministic-review-mp4-fixture")
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest = SimpleNamespace(
        compiled_manifest_id="compiled-ref",
        manifest_hash="compiled-hash",
    )
    body = {
        "run_key": command.run_key,
        "manifest_refs": {
            "compiled_manifest": manifest.compiled_manifest_id,
            "compiled_manifest_hash": manifest.manifest_hash,
        },
        "command_hash": command.command_hash,
        "start_time": datetime(2026, 7, 18, tzinfo=UTC),
        "end_time": datetime(2026, 7, 18, tzinfo=UTC) + timedelta(seconds=6),
        "exit_code": 0,
        "elapsed_time": 6.0,
        "realtime_factor": None,
        "peak_rss": None,
        "output_path": str(output),
        "output_checksum": checksum,
        "local_only": True,
        "production_eligible": False,
        "no_provider_calls_confirmed": True,
    }
    receipt = NativeRenderExecutionReceipt(**body, receipt_hash=stable_hash(body))
    qc = MediaQCReport(
        run_key=command.run_key,
        result="PASS",
        checks={"checksum_sha256": checksum},
        reason_codes=[],
        human_review_required=True,
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
    )
    (work / "execution_receipt.json").write_text(receipt.model_dump_json(), encoding="utf-8")
    (work / "media_qc.json").write_text(qc.model_dump_json(), encoding="utf-8")

    loaded = _load_completed_render(
        output=output,
        work=work,
        manifest=manifest,
        command=command,
    )
    assert loaded == (receipt, qc)

    output.write_bytes(b"tampered")
    with pytest.raises(FileExistsError, match="RENDER_COMPLETION_BINDING_MISMATCH"):
        _load_completed_render(
            output=output,
            work=work,
            manifest=manifest,
            command=command,
        )


def test_v2_archive_roles_and_path_are_explicit_without_changing_v1() -> None:
    assert IMG_CANARY_V1_REQUIRED_ARCHIVE_ROLES < IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES
    assert set(IMG_CANARY_REPRESENTATIVE_CROP_ROLES) <= set(
        IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES
    )
    assert not set(IMG_CANARY_REPRESENTATIVE_CROP_ROLES) & set(
        IMG_CANARY_V1_REQUIRED_ARCHIVE_ROLES
    )
    assert {
        "IMG_CANARY_V2_RUNTIME_PREFLIGHT",
        "IMG_CANARY_V2_RUNTIME_EXECUTION_GATES",
    } <= set(IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES)
    assert IMG_CANARY_ROLE_ARCHIVE_PATHS["IMG_CANARY_ORIGINAL_IMAGE"].endswith(".jpg")
    run_id = "img-canary-v2-20260718T120000Z-deadbeef"
    assert IMGCanaryArchivePathBuilder.build(
        run_id=run_id,
        archive_date="2026-07-18",
    ).endswith("/" + run_id)


def test_v3_archive_roles_bind_corrected_request_and_preserve_v1_v2(
    tmp_path: Path,
) -> None:
    expected_v2_delta = {
        "IMG_CANARY_QC_CROP_FULL_FRAME",
        "IMG_CANARY_QC_CROP_OVERLAY_SAFE",
        "IMG_CANARY_QC_CROP_SUBJECT_FOCAL",
        "IMG_CANARY_VQC1_REPORT_JSON",
        "IMG_CANARY_RENDER_EXECUTION_RECEIPT",
        "IMG_CANARY_V2_PREVIOUS_RUN_IMMUTABILITY",
        "IMG_CANARY_V2_SERIALIZED_REQUEST_EVIDENCE",
        "IMG_CANARY_V2_OPERATOR_APPROVAL_BINDING",
        "IMG_CANARY_V2_DRIVE_READINESS_EVIDENCE",
        "IMG_CANARY_V2_RUNTIME_PREFLIGHT",
        "IMG_CANARY_V2_RUNTIME_EXECUTION_GATES",
    }
    expected_v3_delta = {
        "IMG_CANARY_QC_CROP_FULL_FRAME",
        "IMG_CANARY_QC_CROP_OVERLAY_SAFE",
        "IMG_CANARY_QC_CROP_SUBJECT_FOCAL",
        "IMG_CANARY_VQC1_REPORT_JSON",
        "IMG_CANARY_RENDER_EXECUTION_RECEIPT",
        "IMG_CANARY_V3_PREVIOUS_RUNS_IMMUTABILITY",
        "IMG_CANARY_V3_SERIALIZED_REQUEST_EVIDENCE",
        "IMG_CANARY_V3_OPERATOR_APPROVAL_BINDING",
        "IMG_CANARY_V3_DRIVE_READINESS_EVIDENCE",
        "IMG_CANARY_V3_RUNTIME_PREFLIGHT",
        "IMG_CANARY_V3_RUNTIME_EXECUTION_GATES",
    }
    # These hashes freeze the exact pre-V3 role sets, not merely their sizes.
    assert ai_image_stable_hash(
        sorted(IMG_CANARY_V1_REQUIRED_ARCHIVE_ROLES)
    ) == "9fbb80ddf3cc9a13ac73d0e37783ff4f8a4bf17e3c378c7f9ee0fa5e9088aa81"
    assert ai_image_stable_hash(
        sorted(IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES)
    ) == "b31261bf7ac708bcd108dec131a94f10aaae53bba13a7ee494fead23365c97bc"
    assert set(IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES) - set(
        IMG_CANARY_V1_REQUIRED_ARCHIVE_ROLES
    ) == expected_v2_delta
    assert set(IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES) - set(
        IMG_CANARY_V1_REQUIRED_ARCHIVE_ROLES
    ) == expected_v3_delta
    assert not {
        role for role in IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES if "_V3_" in role
    }
    assert not {
        role for role in IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES if "_V2_" in role
    }

    manifest_paths = tmp_path / "run" / "manifests"
    normalized_image_path = tmp_path / "run" / "source" / "normalized.png"
    v2_paths = IMGCanaryControlledRunner._versioned_archive_role_paths(
        run_id="img-canary-v2-20260718T120000Z-cafebabe",
        normalized_image_path=normalized_image_path,
        manifest_paths=manifest_paths,
    )
    v3_paths = IMGCanaryControlledRunner._versioned_archive_role_paths(
        run_id="img-canary-v3-20260718T120000Z-deadbeef",
        normalized_image_path=normalized_image_path,
        manifest_paths=manifest_paths,
    )
    assert set(v2_paths) == expected_v2_delta
    assert set(v3_paths) == expected_v3_delta
    assert v3_paths["IMG_CANARY_V3_PREVIOUS_RUNS_IMMUTABILITY"] == (
        manifest_paths / "previous-runs-immutability.json"
    )
    assert v3_paths["IMG_CANARY_V3_OPERATOR_APPROVAL_BINDING"] == (
        manifest_paths / "operator-approval-v3-binding.json"
    )
    assert not IMGCanaryControlledRunner._versioned_archive_role_paths(
        run_id="img-canary-20260718T120000Z-1234abcd",
        normalized_image_path=normalized_image_path,
        manifest_paths=manifest_paths,
    )

    run_id = "img-canary-v3-20260718T120000Z-deadbeef"
    project_id = "img-canary-v3-test-project"
    package_id = "img-canary-v3-test-package"
    identity_payload = {
        "run_id": run_id,
        "run_type": "IMG_CANARY",
        "project_id": project_id,
        "package_id": package_id,
        "canary_id": "img-canary-v3-test",
        "channel_key": "small-team-ai",
        "niche_visual_source_profile": "STOCK_ASSISTED",
        "production_eligible": False,
        "not_publishable": True,
        "created_at": datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    }
    identity = IMGCanaryRunIdentity(
        **identity_payload,
        content_hash=ai_image_stable_hash(identity_payload),
    )
    sources: list[ArchiveSource] = []
    for index, role in enumerate(sorted(IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES)):
        source = tmp_path / f"source-{index:02d}.bin"
        source.write_text(
            identity.model_dump_json() if role == "IMG_CANARY_RUN_IDENTITY" else role,
            encoding="utf-8",
        )
        sources.append(ArchiveSource(logical_role=role, source_path=source))
    manifest = ProductionArchiveBuilder().build(
        manifest_id=f"{run_id}-archive-v1",
        project_id=project_id,
        package_id=package_id,
        sources=sources,
        required_roles=IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES,
    )
    assert {item.logical_role for item in manifest.files} == set(
        IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES
    )
    assert {
        item.expected_archive_path for item in manifest.files
    } == {
        IMG_CANARY_ROLE_ARCHIVE_PATHS[role]
        for role in IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES
    }
    _validate_manifest_and_sources(manifest, run_id=run_id)
    assert IMGCanaryArchivePathBuilder.build(
        run_id=run_id,
        archive_date="2026-07-18",
    ).endswith("/" + run_id)
