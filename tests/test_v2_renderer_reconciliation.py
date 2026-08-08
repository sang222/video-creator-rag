"""Offline regression coverage for V2 ElevenLabs rendering and sidecar captions."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts.native_renderer import CompiledNativeRenderManifest
from app.contracts.production_workflow import ProductionWorkflowStage
from app.core.errors import ValidationFailureError
from app.services.native_ffmpeg_renderer import FFmpegCommandBuilder
from app.services.native_render_plan import stable_hash
from app.services.v2_native_effects import (
    V2_ELEVENLABS_NARRATION_STRATEGY,
    V2_LOCAL_ADAPTER_KEY,
    V2_LOCAL_NARRATION_STRATEGY,
    V2_SILENT_AUDIO_STRATEGY,
    V2LocalNativeProductionAdapter,
    _semantic_overlays_from_visual_plan,
)
from app.services.v2_provider_production import V2AuthorizedAdapterOperation


_HASH = "a" * 64


def _ffmpeg_binary(name: str) -> str:
    binary = shutil.which(name)
    if binary is None:
        pytest.skip(f"{name} is required for the offline renderer canary")
    return binary


def _fixture_audio(root: Path, *, ffmpeg: str) -> Path:
    output = root / "already-produced-elevenlabs-fixture.wav"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-ac",
            "2",
            str(output),
        ],
        check=True,
    )
    return output


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(
    *,
    audio_path: Path,
    overlays: list[dict[str, str]] | None = None,
    audio_strategy: str = V2_ELEVENLABS_NARRATION_STRATEGY,
    audio_checksum: str | None = None,
    audio_asset_ref: str = "v2-effect://media/already-produced-elevenlabs-audio",
    audio_mix_strategy: str | None = None,
) -> CompiledNativeRenderManifest:
    checksum = audio_checksum if audio_checksum is not None else _sha256(audio_path)
    strategy = audio_mix_strategy or audio_strategy
    body = {
        "source_plan_ref": "v2-effect://render/native-plan",
        "source_plan_hash": _HASH,
        "compiler_version": "v2-local-native-compiler/1.0.0",
        "motion_pack_version": "v2-package-native-cards/1.0.0",
        "renderer_profile_refs": ["v2-native-h264-aac"],
        "ffmpeg_capability_digest": _HASH,
        "normalized_canvas": {"width": 1920, "height": 1080, "fps": 30},
        "normalized_audio": {
            "strategy": audio_strategy,
            "sample_rate": 48000,
            "channels": 2,
            "audio_asset_ref": audio_asset_ref,
            "audio_checksum": checksum,
            "narration_present": True,
            "alignment_method": "ELEVENLABS_TIMESTAMPS",
        },
        "normalized_caption": {
            "mode": "SIDECAR_SRT_ONLY",
            "caption_ref": "artifact-version://sidecar-srt",
            "caption_checksum": _HASH,
            "subtitle_qc_ref": "artifact-version://subtitle-qc",
            "separate_caption_track": True,
            "render_consumes_caption_cues": False,
        },
        "compiled_scenes": [
            {
                "scene_id": "scene-001",
                "start_ms": 0,
                "end_ms": 1000,
                "duration_ms": 1000,
                # These are deliberately ignored by the renderer.  They
                # simulate content that may remain available to QC/timeline
                # authorities without becoming visible captions.
                "narration_unit_text": "Narration must not be drawn.",
                "canonical_narration_text": "Canonical text is not an overlay.",
                "srt_cue_text": "Sidecar cue remains external.",
            }
        ],
        "asset_request_plan": None,
        "transition_schedule": [],
        "overlay_schedule": overlays or [],
        "audio_mix_schedule": {
            "strategy": strategy,
            "audio_asset_ref": audio_asset_ref,
            "audio_checksum": checksum,
            "narration_present": True,
            "alignment_method": "ELEVENLABS_TIMESTAMPS",
        },
        "caption_schedule": {
            "authority": "SIDECAR_SRT_ONLY",
            "caption_ref": "artifact-version://sidecar-srt",
            "timed_words_ref": "artifact-version://timed-words",
            "subtitle_qc_ref": "artifact-version://subtitle-qc",
            "render_consumes_caption_cues": False,
        },
        "output_specs": [
            {
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "pix_fmt": "yuv420p",
                "audio_codec": "aac",
            }
        ],
        "expected_input_refs": [],
        "unresolved_inputs": [],
        "compilation_warnings": [],
        "compilation_reason_codes": [],
        "production_eligible": True,
        "temporal_authority_mode": "CANONICAL_STRICT",
        "canonical_media_timeline_ref": "v2-effect://media/timeline",
        "canonical_media_timeline_hash": _HASH,
        "canonical_audio_asset_ref": audio_asset_ref,
        "canonical_duration_ms": 1000,
        "canonical_caption_compilation_ref": "artifact-version://sidecar-srt",
        "canonical_caption_compilation_hash": _HASH,
        "visual_direction_contract_ref": "artifact-version://visual-plan",
        "visual_direction_contract_hash": _HASH,
        "creative_gate_results": {},
        "render_purpose": "VCOS_V2_NATIVE_PRODUCTION",
    }
    draft = CompiledNativeRenderManifest(
        compiled_manifest_id="pending",
        ffmpeg_binary_requirement="ffmpeg-with-libx264-aac",
        manifest_hash="0" * 64,
        created_at=datetime.now(UTC),
        **body,
    )
    manifest_hash = stable_hash(
        draft.model_dump(
            mode="json",
            exclude={
                "compiled_manifest_id",
                "ffmpeg_binary_requirement",
                "manifest_hash",
                "created_at",
            },
        )
    )
    return draft.model_copy(
        update={
            "compiled_manifest_id": f"v2-native-manifest:{manifest_hash}",
            "manifest_hash": manifest_hash,
        }
    )


def _builder(root: Path) -> FFmpegCommandBuilder:
    full_ffmpeg = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    full_ffprobe = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
    return FFmpegCommandBuilder(
        root,
        ffmpeg=str(full_ffmpeg) if full_ffmpeg.is_file() else _ffmpeg_binary("ffmpeg"),
        ffprobe=str(full_ffprobe) if full_ffprobe.is_file() else _ffmpeg_binary("ffprobe"),
    )


def _real_render_operation(audio_strategy: str) -> V2AuthorizedAdapterOperation:
    return V2AuthorizedAdapterOperation(
        operation_id="render-operation",
        stage=ProductionWorkflowStage.RENDER,
        adapter_key=V2_LOCAL_ADAPTER_KEY,
        paid_provider_call=False,
        max_cost_usd=Decimal("0"),
        execution_mode="REAL_LONG_FORM_PRODUCTION",
        parameters={
            "mode": "NATIVE_FFMPEG_LOCAL",
            "audio_strategy": audio_strategy,
        },
    )


def test_real_long_form_render_requires_elevenlabs_and_never_local_fallback() -> None:
    adapter = object.__new__(V2LocalNativeProductionAdapter)
    adapter._narration_runtime = None
    context = SimpleNamespace(
        run=SimpleNamespace(
            production_lane="LONG_FORM", planning_source_type="LONG_FORM_PLAN"
        )
    )

    adapter._validate_operation(
        context, _real_render_operation(V2_ELEVENLABS_NARRATION_STRATEGY)
    )
    for forbidden in (V2_LOCAL_NARRATION_STRATEGY, V2_SILENT_AUDIO_STRATEGY):
        with pytest.raises(
            ValidationFailureError,
            match="V2_REAL_RENDER_ELEVENLABS_NARRATION_REQUIRED",
        ):
            adapter._validate_operation(context, _real_render_operation(forbidden))


def test_elevenlabs_audio_authority_is_required_and_checksum_bound(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    audio = _fixture_audio(tmp_path, ffmpeg=builder.ffmpeg)

    command = builder.build_v2_local_native(
        _manifest(audio_path=audio), run_key="elevenlabs-authority", audio_path=audio
    )
    assert command.input_files == [str(audio)]
    assert command.expected_qc["narration_drawtext_count"] == 0

    with pytest.raises(ValueError, match="V2_NATIVE_AUDIO_AUTHORITY_MISMATCH"):
        builder.build_v2_local_native(
            _manifest(audio_path=audio, audio_mix_strategy=V2_LOCAL_NARRATION_STRATEGY),
            run_key="strategy-drift",
            audio_path=audio,
        )
    with pytest.raises(ValueError, match="V2_NATIVE_NARRATION_AUDIO_CHECKSUM_MISMATCH"):
        builder.build_v2_local_native(
            _manifest(audio_path=audio, audio_checksum="b" * 64),
            run_key="checksum-drift",
            audio_path=audio,
        )
    with pytest.raises(ValueError, match="V2_NATIVE_AUDIO_AUTHORITY_MISMATCH"):
        builder.build_v2_local_native(
            _manifest(audio_path=audio, audio_asset_ref=""),
            run_key="missing-authority",
            audio_path=audio,
        )


def test_narration_and_srt_fields_never_create_drawtext(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    audio = _fixture_audio(tmp_path, ffmpeg=builder.ffmpeg)
    manifest = _manifest(audio_path=audio)
    command = builder.build_v2_local_native(
        manifest, run_key="sidecar-only", audio_path=audio
    )
    graph = Path(command.generated_filtergraph_path).read_text(encoding="utf-8")
    argv = " ".join(command.sanitized_argv)
    assert "drawtext" not in graph
    assert command.generated_text_files == []
    assert "subtitles=" not in graph
    assert ".ass" not in argv
    assert "subtitles" not in argv
    assert "-map 0:s" not in argv
    assert "-c:s" not in argv
    assert manifest.normalized_caption["mode"] == "SIDECAR_SRT_ONLY"
    assert manifest.caption_schedule["render_consumes_caption_cues"] is False
    assert command.expected_qc["subtitle_stream_count"] == 0


def test_explicit_semantic_overlay_preserves_exact_native_text(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    audio = _fixture_audio(tmp_path, ffmpeg=builder.ffmpeg)
    overlay = {
        "overlay_id": "diagram-label-001",
        "scene_id": "scene-001",
        "overlay_type": "DIAGRAM_LABEL",
        "text": "Verified decision boundary",
        "source_ref": "artifact-version://diagram-label-authority",
        "source_hash": "c" * 64,
    }
    command = builder.build_v2_local_native(
        _manifest(audio_path=audio, overlays=[overlay]),
        run_key="semantic-overlay",
        audio_path=audio,
    )
    graph = Path(command.generated_filtergraph_path).read_text(encoding="utf-8")
    assert graph.count("drawtext=") == 1
    assert command.expected_qc["semantic_overlay_drawtext_count"] == 1
    assert Path(command.generated_text_files[0]).read_text(encoding="utf-8").strip() == overlay["text"]


def test_timeline_projects_only_typed_semantic_overlay_authority() -> None:
    overlay = {
        "overlay_id": "data-label-001",
        "scene_id": "scene-001",
        "overlay_type": "DATA_LABEL",
        "text": "42% verified",
        "source_ref": "artifact-version://data-label-authority",
        "source_hash": "e" * 64,
    }
    visual = SimpleNamespace(content={"semantic_overlays": [overlay]})
    assert _semantic_overlays_from_visual_plan(
        visual=visual, scenes=[{"scene_id": "scene-001"}]
    ) == [overlay]

    invalid = dict(overlay, overlay_type="NARRATION")
    with pytest.raises(
        ValidationFailureError, match="V2_SEMANTIC_OVERLAY_CONTRACT_INVALID"
    ):
        _semantic_overlays_from_visual_plan(
            visual=SimpleNamespace(content={"semantic_overlays": [invalid]}),
            scenes=[{"scene_id": "scene-001"}],
        )


def test_offline_render_canaries_keep_subtitles_sidecar_only(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    audio = _fixture_audio(tmp_path, ffmpeg=builder.ffmpeg)
    overlay = {
        "overlay_id": "title-card-001",
        "scene_id": "scene-001",
        "overlay_type": "TITLE_CARD",
        "text": "Explicit title card",
        "source_ref": "artifact-version://title-card-authority",
        "source_hash": "d" * 64,
    }

    results = []
    for run_key, overlays in (("canary-no-overlay", []), ("canary-overlay", [overlay])):
        command = builder.build_v2_local_native(
            _manifest(audio_path=audio, overlays=overlays),
            run_key=run_key,
            audio_path=audio,
        )
        subprocess.run(command.sanitized_argv, check=True, capture_output=True, text=True)
        Path(str(command.output_file) + ".part.mp4").replace(command.output_file)
        probe = subprocess.run(
            [
                builder.ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                command.output_file,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        probe_payload = json.loads(probe.stdout)
        streams = probe_payload["streams"]
        codec_types = [stream["codec_type"] for stream in streams]
        graph = Path(command.generated_filtergraph_path).read_text(encoding="utf-8")
        results.append(
            (
                codec_types,
                float(probe_payload["format"]["duration"]),
                graph.count("drawtext="),
                command,
            )
        )

    assert results[0][0].count("video") == 1
    assert results[0][0].count("audio") == 1
    assert "subtitle" not in results[0][0]
    assert results[0][1] == pytest.approx(1.0, abs=0.05)
    assert results[0][2] == 0
    assert results[1][2] == 1
    assert results[1][3].expected_qc["semantic_overlay_drawtext_count"] == 1
