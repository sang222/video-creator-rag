from __future__ import annotations

import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.native_renderer import (
    CompiledNativeRenderManifest,
    FFmpegCommandManifest,
    MediaQCReport,
    NativeRenderExecutionReceipt,
    NativeRenderPlan,
)
from app.contracts.vcos_v2 import DurationContractV2, ProductionLane


class NativeQualificationRenderRequest(BaseModel):
    """Exact local-only inputs for one real Phase 6 media qualification."""

    run_key: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    workspace_root: Path
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    production_package_artifact_version_id: uuid.UUID
    production_package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_profile_version_id: uuid.UUID
    effective_context_snapshot_id: uuid.UUID
    effective_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_contract: DurationContractV2
    production_lane: ProductionLane

    model_config = ConfigDict(extra="forbid", frozen=True)


class NativeQualificationRenderResult(BaseModel):
    plan: NativeRenderPlan
    compiled_manifest: CompiledNativeRenderManifest
    command: FFmpegCommandManifest
    execution_receipt: NativeRenderExecutionReceipt
    media_qc: MediaQCReport
    output_path: Path
    output_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int
    height: int
    duration_seconds: float = Field(gt=0)
    has_video_stream: bool
    has_audio_stream: bool
    video_codec: str
    audio_codec: str
    technical_qc_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    creative_qc_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    local_only: bool = True
    paid_provider_calls: int = 0

    model_config = ConfigDict(extra="forbid", frozen=True)
