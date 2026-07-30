from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.contracts.m10_2 import FinalMediaRefCreate
from app.contracts.native_renderer import (
    CanvasSpec,
    NativeRenderPlan,
    NativeRenderScene,
)
from app.contracts.vcos_qualification import (
    NativeQualificationRenderRequest,
    NativeQualificationRenderResult,
)
from app.contracts.vcos_v2 import ProductionLane
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.m10_2 import (
    LOCAL_RENDERER_CAPABILITY,
    FinalMediaRefService,
)
from app.services.m10_5 import (
    CloudMediaRefService,
    GoogleDriveUploadResult,
    GoogleDriveVerificationResult,
)
from app.services.native_ffmpeg_renderer import (
    FFmpegCommandBuilder,
    NativeFFmpegRenderer,
)
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import canonical_plan_hash, stable_hash
from app.services.production_package import ChannelDurationContractResolver
from app.services.workflow import ArtifactService


class NativeQualificationService:
    """Exercise the real local NativeFFmpeg stack without provider or publish calls."""

    def render_from_frozen_channel(
        self,
        session: Any,
        request: NativeQualificationRenderRequest,
    ) -> NativeQualificationRenderResult:
        """Verify the request against profile/policy duration authority before render."""

        resolved = ChannelDurationContractResolver(session).resolve(
            profile_version_id=request.channel_profile_version_id,
            policy_snapshot_id=request.duration_contract.source_policy_snapshot_id,
            production_lane=request.production_lane,
        )
        if resolved.model_dump(mode="json") != request.duration_contract.model_dump(
            mode="json"
        ):
            raise RuntimeError("NATIVE_QUALIFICATION_DURATION_AUTHORITY_MISMATCH")
        return self.render(request)

    def render(
        self,
        request: NativeQualificationRenderRequest,
    ) -> NativeQualificationRenderResult:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise RuntimeError("FFMPEG_OR_FFPROBE_UNAVAILABLE")

        workspace = request.workspace_root.resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        inputs = workspace / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        srt_path = inputs / f"{request.run_key}.srt"
        duration_seconds = request.duration_contract.target_duration_ms / 1000.0
        srt_path.write_text(
            "1\n"
            f"00:00:00,000 --> {_srt_timestamp(duration_seconds)}\n"
            "VCOS local qualification: exact package, duration, render and QC.\n",
            encoding="utf-8",
        )

        width, height, output_profile = _output_geometry(request.production_lane)
        plan = NativeRenderPlan(
            plan_id=f"vcos-qualification:{request.run_key}",
            plan_version=2,
            package_id=str(request.production_package_artifact_version_id),
            production_package_schema_version="v2",
            production_package_artifact_version_id=str(
                request.production_package_artifact_version_id
            ),
            production_package_hash=request.production_package_hash,
            duration_contract=request.duration_contract,
            video_project_id=str(request.video_project_id),
            company_id=str(request.company_id),
            channel_id=str(request.channel_workspace_id),
            channel_profile_version_id=str(request.channel_profile_version_id),
            effective_context_snapshot_id=str(request.effective_context_snapshot_id),
            effective_context_hash=request.effective_context_hash,
            format_identity_contract_ref=f"qualification://{request.run_key}/format",
            format_identity_contract_hash=stable_hash(
                {"run_key": request.run_key, "geometry": [width, height]}
            ),
            format_identity_status="APPROVED",
            episode_originality_manifest_ref=(
                f"qualification://{request.run_key}/originality"
            ),
            episode_originality_manifest_hash=stable_hash(
                {"run_key": request.run_key, "result": "PASS"}
            ),
            final_originality_gate="PASS",
            script_ref=f"qualification://{request.run_key}/script",
            script_hash=stable_hash(
                {
                    "run_key": request.run_key,
                    "duration_ms": request.duration_contract.target_duration_ms,
                }
            ),
            srt_ref=str(srt_path),
            srt_hash=_sha256_file(srt_path),
            temporal_authority_mode="LEGACY_HISTORICAL",
            scene_timing_source="CHANNEL_DURATION_CONTRACT",
            caption_timing_source="QUALIFICATION_SRT",
            visual_plan_ref=f"qualification://{request.run_key}/visual-plan",
            visual_plan_hash=stable_hash(
                {"run_key": request.run_key, "lane": request.production_lane.value}
            ),
            canvas_spec=CanvasSpec(width=width, height=height, fps=30),
            scenes=[
                NativeRenderScene(
                    scene_id="qualification-scene-001",
                    source_segment_ids=["qualification-segment-001"],
                    narration_start_ms=0,
                    narration_end_ms=request.duration_contract.target_duration_ms,
                    duration_ms=request.duration_contract.target_duration_ms,
                    visual_treatment="NATIVE_SLIDE",
                    layout_type="QUALIFICATION_CARD",
                    asset_requirements=[],
                    resolved_asset_refs=[],
                    animation_type="HOLD_STATIC",
                    transition_in=None,
                    transition_out=None,
                    originality_role="PRIMARY_EXPLANATION",
                )
            ],
            global_motion_policy={"mode": "LOCAL_DETERMINISTIC"},
            caption_policy={"format": "SRT", "burn_in": True},
            audio_policy={"source": "LOCAL_SYNTHETIC_TONE", "sample_rate": 48000},
            output_profiles=[output_profile],
            character_policy_mode="NO_CHARACTER",
            purpose="NR1_LOCAL_SYNTHETIC_SMOKE",
            production_eligible=False,
            status="APPROVED",
            content_hash="",
            created_at=datetime.now(UTC),
            created_by="vcos-durable-worker",
        )
        plan = plan.model_copy(update={"content_hash": canonical_plan_hash(plan)})
        manifest = NativeMotionCompiler().compile(plan)
        command = FFmpegCommandBuilder(
            workspace,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        ).build_synthetic(
            manifest,
            run_key=request.run_key,
            duration_seconds=duration_seconds,
        )
        receipt, qc = NativeFFmpegRenderer(
            workspace,
            smoke_enabled=True,
            production_enabled=False,
        ).execute(
            manifest,
            command,
            purpose="NR1_LOCAL_SYNTHETIC_SMOKE",
        )
        probe = _probe(Path(receipt.output_path), ffprobe)
        video_stream = next(
            (
                item
                for item in probe.get("streams", [])
                if item.get("codec_type") == "video"
            ),
            None,
        )
        audio_stream = next(
            (
                item
                for item in probe.get("streams", [])
                if item.get("codec_type") == "audio"
            ),
            None,
        )
        actual_duration = float((probe.get("format") or {}).get("duration") or 0)
        if (
            receipt.output_checksum != _sha256_file(Path(receipt.output_path))
            or qc.result != "PASS"
            or video_stream is None
            or audio_stream is None
            or int(video_stream.get("width") or 0) != width
            or int(video_stream.get("height") or 0) != height
            or video_stream.get("codec_name") != "h264"
            or audio_stream.get("codec_name") != "aac"
            or abs(actual_duration - duration_seconds) > 0.30
        ):
            raise RuntimeError("NATIVE_QUALIFICATION_OUTPUT_MISMATCH")
        technical_qc_hash = stable_hash(qc.model_dump(mode="json"))
        creative_qc_hash = stable_hash(
            {
                "schema_version": "vcos.automated-creative-qc.v1",
                "result": "PASS",
                "native_render_plan_hash": plan.content_hash,
                "render_output_checksum": receipt.output_checksum,
                "scene_count": len(plan.scenes),
                "duration_contract_hash": (
                    request.duration_contract.duration_contract_hash
                ),
                "production_lane": request.production_lane.value,
            }
        )
        return NativeQualificationRenderResult(
            plan=plan,
            compiled_manifest=manifest,
            command=command,
            execution_receipt=receipt,
            media_qc=qc,
            output_path=Path(receipt.output_path),
            output_checksum=receipt.output_checksum,
            width=width,
            height=height,
            duration_seconds=actual_duration,
            has_video_stream=True,
            has_audio_stream=True,
            video_codec="h264",
            audio_codec="aac",
            technical_qc_hash=technical_qc_hash,
            creative_qc_hash=creative_qc_hash,
        )

    def persist_verified_final_media(
        self,
        session: Any,
        *,
        request: NativeQualificationRenderRequest,
        render: NativeQualificationRenderResult,
        archive_receipt: dict[str, Any],
        created_by_user_id: uuid.UUID,
    ) -> FinalMediaRef:
        """Register an exact FinalMediaRef only after archive readback verification.

        The archive client remains injectable for qualification, while this
        method exercises the real CloudMediaRef, immutable lineage-artifact,
        canonical package/readiness, and FinalMediaRef persistence boundaries.
        It is replay-safe under a project row lock.
        """

        self._validate_render_archive_binding(
            request=request,
            render=render,
            archive_receipt=archive_receipt,
        )
        project = session.scalar(
            select(VideoProject)
            .where(VideoProject.id == request.video_project_id)
            .with_for_update()
        )
        if (
            project is None
            or project.schema_version != "v2"
            or project.company_id != request.company_id
            or project.channel_workspace_id != request.channel_workspace_id
            or project.production_lane != request.production_lane.value
            or project.channel_profile_version_id != request.channel_profile_version_id
        ):
            raise RuntimeError("NATIVE_QUALIFICATION_PROJECT_SCOPE_MISMATCH")

        existing = session.scalar(
            select(FinalMediaRef).where(
                FinalMediaRef.video_project_id == project.id,
                FinalMediaRef.production_package_artifact_version_id
                == request.production_package_artifact_version_id,
                FinalMediaRef.production_package_hash
                == request.production_package_hash,
                FinalMediaRef.checksum_sha256 == render.output_checksum,
            )
        )
        if existing is not None:
            if (
                existing.cloud_media_ref_id is None
                or existing.lineage_artifact_version_id is None
            ):
                raise RuntimeError(
                    "NATIVE_QUALIFICATION_EXISTING_FINAL_MEDIA_INCOMPLETE"
                )
            return existing

        archive_file = next(
            item
            for item in archive_receipt["files"]
            if item["logical_role"] == "REVIEW_MEDIA"
        )
        cloud_ref = self._cloud_media_ref(
            session,
            request=request,
            render=render,
            archive_receipt=archive_receipt,
            archive_file=archive_file,
        )
        file_ref = (
            f"drive://{cloud_ref.drive_file_id}/"
            f"{cloud_ref.file_name or render.output_path.name}"
        )
        lineage_version = self._lineage_artifact(
            session,
            request=request,
            render=render,
            archive_receipt=archive_receipt,
            cloud_ref=cloud_ref,
            file_ref=file_ref,
            created_by_user_id=created_by_user_id,
        )
        return FinalMediaRefService(session).create(
            data=FinalMediaRefCreate(
                company_id=request.company_id,
                channel_workspace_id=request.channel_workspace_id,
                video_project_id=request.video_project_id,
                production_package_artifact_version_id=(
                    request.production_package_artifact_version_id
                ),
                production_package_hash=request.production_package_hash,
                media_type=(
                    "LONG_FORM_FINAL"
                    if request.production_lane == ProductionLane.LONG_FORM
                    else "SHORT_FINAL"
                ),
                file_ref=file_ref,
                duration_seconds=Decimal(str(render.duration_seconds)),
                aspect_ratio=(
                    "16:9"
                    if request.production_lane == ProductionLane.LONG_FORM
                    else "9:16"
                ),
                resolution=f"{render.width}x{render.height}",
                provider_key="native_ffmpeg",
                provider_type=LOCAL_RENDERER_CAPABILITY,
                checksum_sha256=render.output_checksum,
                cloud_media_ref_id=cloud_ref.id,
                lineage_artifact_version_id=lineage_version.id,
            )
        )

    @staticmethod
    def _validate_render_archive_binding(
        *,
        request: NativeQualificationRenderRequest,
        render: NativeQualificationRenderResult,
        archive_receipt: dict[str, Any],
    ) -> None:
        receipt_payload = dict(archive_receipt)
        receipt_hash = str(receipt_payload.pop("receipt_hash", ""))
        review_files = [
            item
            for item in archive_receipt.get("files", [])
            if item.get("logical_role") == "REVIEW_MEDIA"
        ]
        if (
            render.plan.video_project_id != str(request.video_project_id)
            or render.plan.production_package_artifact_version_id
            != str(request.production_package_artifact_version_id)
            or render.plan.production_package_hash != request.production_package_hash
            or render.output_checksum != render.execution_receipt.output_checksum
            or not render.output_path.is_file()
            or _sha256_file(render.output_path) != render.output_checksum
            or render.media_qc.result != "PASS"
            or archive_receipt.get("archive_state") != "VERIFIED"
            or archive_receipt.get("remote_exact_set_verified") is not True
            or len(review_files) != 1
            or review_files[0].get("verified") is not True
            or review_files[0].get("local_sha256") != render.output_checksum
            or review_files[0].get("remote_sha256") != render.output_checksum
            or receipt_hash != _stable_payload_hash(receipt_payload)
        ):
            raise RuntimeError("NATIVE_QUALIFICATION_FINAL_MEDIA_AUTHORITY_MISMATCH")

    @staticmethod
    def _cloud_media_ref(
        session: Any,
        *,
        request: NativeQualificationRenderRequest,
        render: NativeQualificationRenderResult,
        archive_receipt: dict[str, Any],
        archive_file: dict[str, Any],
    ) -> CloudMediaRef:
        drive_file_id = str(archive_file["drive_file_id"])
        existing = session.scalar(
            select(CloudMediaRef).where(
                CloudMediaRef.video_project_id == request.video_project_id,
                CloudMediaRef.drive_file_id == drive_file_id,
                CloudMediaRef.checksum_sha256 == render.output_checksum,
            )
        )
        if existing is not None:
            return existing
        upload = GoogleDriveUploadResult(
            drive_file_id=drive_file_id,
            drive_folder_id=str(archive_file["drive_folder_id"]),
            web_view_link=(f"https://drive.google.com/file/d/{drive_file_id}/view"),
            file_name=str(archive_file["name"]),
            mime_type="video/mp4",
            size_bytes=int(archive_file["remote_size_bytes"]),
            checksum_sha256=str(archive_file["remote_sha256"]),
            upload_mode="resumable",
            technical_appendix={
                "archive_receipt_hash": archive_receipt["receipt_hash"],
                "remote_exact_set_verified": True,
            },
        )
        verification = GoogleDriveVerificationResult(
            ok=True,
            verification_status="CHECKSUM_VERIFIED",
            reason_code="MEDIA_OFFLOAD_UPLOAD_VERIFIED",
            size_verified=True,
            checksum_verified=True,
            checksum_unavailable=False,
        )
        return CloudMediaRefService(session).create_verified_ref(
            company_id=request.company_id,
            channel_workspace_id=request.channel_workspace_id,
            video_project_id=request.video_project_id,
            uploaded_video_id=None,
            render_package_id=None,
            media_type=(
                "LONG_FORM_FINAL"
                if request.production_lane == ProductionLane.LONG_FORM
                else "SHORT_FINAL"
            ),
            upload_result=upload,
            verification=verification,
            local_source_path_hash=render.output_checksum,
            checksum_sha256=render.output_checksum,
            source_refs=[
                {
                    "type": "archive_receipt",
                    "ref": (
                        f"archive-receipt://{archive_receipt['run_id']}"
                        f"#{archive_receipt['receipt_hash']}"
                    ),
                }
            ],
            retention_policy={
                "cleanup_after_verified": False,
                "qualification_fixture": True,
            },
        )

    @staticmethod
    def _lineage_artifact(
        session: Any,
        *,
        request: NativeQualificationRenderRequest,
        render: NativeQualificationRenderResult,
        archive_receipt: dict[str, Any],
        cloud_ref: CloudMediaRef,
        file_ref: str,
        created_by_user_id: uuid.UUID,
    ) -> ArtifactVersion:
        content = {
            "schema_version": "vcos.native-final-media-lineage.v2",
            "video_project_id": str(request.video_project_id),
            "production_package_artifact_version_id": str(
                request.production_package_artifact_version_id
            ),
            "production_package_hash": request.production_package_hash,
            "duration_contract": request.duration_contract.model_dump(mode="json"),
            "canonical_media_timeline_hash": render.plan.content_hash,
            "native_render_plan_hash": render.plan.content_hash,
            "render_command_hash": render.command.command_hash,
            "render_output_checksum": render.output_checksum,
            "technical_qc_hash": render.technical_qc_hash,
            "creative_qc_hash": render.creative_qc_hash,
            "archive_receipt_hash": archive_receipt["receipt_hash"],
            "archive_manifest_hash": archive_receipt["archive_manifest_hash"],
            "archive_state": "VERIFIED",
            "cloud_media_ref_id": str(cloud_ref.id),
            "file_ref": file_ref,
            "provider_calls": 0,
        }
        artifact = session.scalar(
            select(Artifact).where(
                Artifact.video_project_id == request.video_project_id,
                Artifact.artifact_type == "mr1_final_media_lineage_receipt",
            )
        )
        if artifact is not None:
            version = session.get(ArtifactVersion, artifact.current_version_id)
            if (
                version is None
                or version.content != content
                or version.status != "approved"
                or artifact.status != "approved"
            ):
                raise RuntimeError("NATIVE_QUALIFICATION_LINEAGE_REPLAY_CONFLICT")
            return version
        artifacts = ArtifactService(session)
        artifact = artifacts.create_artifact(
            data=ArtifactCreate(
                video_project_id=request.video_project_id,
                artifact_type="mr1_final_media_lineage_receipt",
                status="approved",
                created_by_user_id=created_by_user_id,
            ),
            correlation_id=f"phase6-final-media-{request.run_key}",
            trusted_authority_write=True,
        )
        version = artifacts.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content=content,
                status="approved",
                created_by_user_id=created_by_user_id,
                source_manifest={
                    "archive_verified": True,
                    "real_mp4": True,
                    "provider_calls": 0,
                },
                evidence_refs=[
                    {
                        "type": "archive_receipt",
                        "ref": (
                            f"archive-receipt://{archive_receipt['run_id']}"
                            f"#{archive_receipt['receipt_hash']}"
                        ),
                    }
                ],
            ),
            correlation_id=f"phase6-final-media-lineage-{request.run_key}",
            trusted_authority_write=True,
        )
        artifact.status = "approved"
        session.flush()
        return version


def _output_geometry(lane: ProductionLane) -> tuple[int, int, str]:
    if lane == ProductionLane.LONG_FORM:
        return 1920, 1080, "YT_LONG_1080P30_SDR_H264_VT"
    if lane in {
        ProductionLane.DAILY_SHORT,
        ProductionLane.LONG_DERIVED_SHORT,
    }:
        return 1080, 1920, "YT_SHORT_1080X1920_30_SDR_H264_VT"
    raise ValueError(f"UNSUPPORTED_PRODUCTION_LANE:{lane}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe(path: Path, ffprobe: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _srt_timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _stable_payload_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
