"""Offline regression coverage for V2 ElevenLabs rendering and sidecar captions."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts.native_renderer import (
    CompiledNativeRenderManifest,
    V2ProductionRenderExecutionEnvelope,
)
from app.contracts.production_workflow import ProductionWorkflowStage
from app.core.errors import ValidationFailureError
from app.services.native_ffmpeg_renderer import (
    FFmpegCommandBuilder,
    NativeFFmpegRenderer,
)
from app.services.native_media_qc import NativeMediaQC
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


def _text_sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _native_visual_spec(
    *, scene_id: str, narration_text: str, headline: str
) -> dict[str, object]:
    body = {
        "schema_version": "vcos.native-explanatory-scene.v1",
        "scene_id": scene_id,
        "composition": "DECISION_FLOW",
        "visual_function_hint": "PROCESS_OR_DECISION_MODEL",
        "headline": headline,
        "headline_derivation": "QUALIFIED_PROPOSITION_PREFIX_MAX_116",
        "headline_hash": _text_sha256(headline),
        "semantic_intent_hash": _text_sha256(headline),
        "narration_text_hash": _text_sha256(narration_text),
        "step_labels": ["INPUT", "BOUNDARY", "ACTION"],
        "information_unit_ids": ["iu-001"],
        "source_ref": "qualified-information-unit://iu-001/semantic-intent",
        "source_hash": "b" * 64,
        "factual_ui_representation": False,
        "caption_source": False,
        "renderer_vocabulary_version": "vcos.native-explanatory-v1",
    }
    return {**body, "content_hash": stable_hash(body)}


def _manifest(
    *,
    audio_path: Path,
    overlays: list[dict[str, str]] | None = None,
    audio_strategy: str = V2_ELEVENLABS_NARRATION_STRATEGY,
    audio_checksum: str | None = None,
    audio_asset_ref: str = "v2-effect://media/already-produced-elevenlabs-audio",
    audio_mix_strategy: str | None = None,
    scene_start_ms: int = 0,
    scene_end_ms: int = 1000,
    canonical_duration_ms: int = 1000,
    expected_input_refs: list[str] | None = None,
) -> CompiledNativeRenderManifest:
    checksum = audio_checksum if audio_checksum is not None else _sha256(audio_path)
    strategy = audio_mix_strategy or audio_strategy
    narration_text = "Narration must not be drawn."
    native_spec = _native_visual_spec(
        scene_id="scene-001",
        narration_text=narration_text,
        headline="Structured input crosses a verified action boundary",
    )
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
                "start_ms": scene_start_ms,
                "end_ms": scene_end_ms,
                "duration_ms": scene_end_ms - scene_start_ms,
                "narration_text_hash": _text_sha256(narration_text),
                "native_visual_spec": native_spec,
                # These are deliberately ignored by the renderer.  They
                # simulate content that may remain available to QC/timeline
                # authorities without becoming visible captions.
                "narration_unit_text": narration_text,
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
        "expected_input_refs": expected_input_refs or [],
        "unresolved_inputs": [],
        "compilation_warnings": [],
        "compilation_reason_codes": [],
        "production_eligible": True,
        "temporal_authority_mode": "CANONICAL_STRICT",
        "canonical_media_timeline_ref": "v2-effect://media/timeline",
        "canonical_media_timeline_hash": _HASH,
        "canonical_audio_asset_ref": audio_asset_ref,
        "canonical_duration_ms": canonical_duration_ms,
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
        ffprobe=str(full_ffprobe)
        if full_ffprobe.is_file()
        else _ffmpeg_binary("ffprobe"),
    )


def _execution_envelope(
    *, manifest: CompiledNativeRenderManifest, run_key: str, package_id: uuid.UUID
) -> V2ProductionRenderExecutionEnvelope:
    body = {
        "envelope_version": "vcos.v2-native-render-envelope.v1",
        "workflow_run_id": uuid.uuid4(),
        "command_id": f"render:{run_key}",
        "render_run_key": run_key,
        "production_package_artifact_version_id": package_id,
        "production_package_hash": "c" * 64,
        "provider_execution_plan_ref": "artifact-version://provider-plan",
        "provider_execution_plan_hash": "d" * 64,
        "budget_scope_ref": "budget-reservation://offline",
        "budget_scope_hash": "e" * 64,
        "operation_id": "render-operation",
        "adapter_key": "v2-local-native",
        "plan_ref": manifest.source_plan_ref,
        "plan_hash": manifest.source_plan_hash,
        "production_eligible": True,
        "paid_provider_call": False,
    }
    return V2ProductionRenderExecutionEnvelope(
        **body, authorization_hash=stable_hash(body)
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


def test_elevenlabs_audio_authority_is_required_and_checksum_bound(
    tmp_path: Path,
) -> None:
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


def test_v2_renderer_preserves_caption_authority_and_bounded_silence_windows(
    tmp_path: Path,
) -> None:
    builder = _builder(tmp_path)
    audio = _fixture_audio(tmp_path, ffmpeg=builder.ffmpeg)
    package_id = uuid.uuid4()
    package_ref = f"artifact-version://{package_id}"
    manifest = _manifest(
        audio_path=audio,
        scene_start_ms=300,
        scene_end_ms=900,
        canonical_duration_ms=1_000,
        expected_input_refs=[package_ref],
    )
    command = builder.build_v2_local_native(
        manifest, run_key="bounded-silence", audio_path=audio
    )

    assert command.canonical_caption_compilation_ref == (
        manifest.canonical_caption_compilation_ref
    )
    assert command.canonical_caption_compilation_hash == (
        manifest.canonical_caption_compilation_hash
    )
    plan = json.loads(
        Path(command.expected_qc["native_explanatory_plan_path"]).read_text(
            encoding="utf-8"
        )
    )
    policy = plan["presentation_window_policy"]
    assert policy["spoken_word_timing_unchanged"] is True
    assert policy["timing_synthesized"] is False
    assert policy["windows"] == [
        {
            "scene_id": "scene-001",
            "binding_start_ms": 300,
            "binding_end_ms": 900,
            "presentation_start_ms": 0,
            "presentation_end_ms": 1_000,
            "leading_silence_hold_ms": 300,
            "trailing_silence_hold_ms": 100,
        }
    ]
    envelope = _execution_envelope(
        manifest=manifest, run_key="bounded-silence", package_id=package_id
    )
    receipt, qc = NativeFFmpegRenderer(tmp_path, production_enabled=True).execute(
        manifest,
        command,
        purpose="VCOS_V2_NATIVE_PRODUCTION",
        execution_envelope=envelope,
    )
    assert receipt.exit_code == 0
    assert qc.result == "PASS"
    assert qc.checks["native_presentation_window_policy_attested"] is True


def test_v2_renderer_rejects_silence_hold_over_two_seconds(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    audio = _fixture_audio(tmp_path, ffmpeg=builder.ffmpeg)
    manifest = _manifest(
        audio_path=audio,
        scene_start_ms=2_001,
        scene_end_ms=2_900,
        canonical_duration_ms=3_000,
    )

    with pytest.raises(ValueError, match="V2_NATIVE_PRESENTATION_HOLD_OUTSIDE_POLICY"):
        builder.build_v2_local_native(
            manifest, run_key="excessive-silence", audio_path=audio
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
    assert graph.count("drawtext=") == 4
    generated_text = "\n".join(
        Path(path).read_text(encoding="utf-8") for path in command.generated_text_files
    )
    assert "Narration must not be drawn." not in generated_text
    assert "Canonical text is not an overlay." not in generated_text
    assert "Sidecar cue remains external." not in generated_text
    assert "subtitles=" not in graph
    assert ".ass" not in argv
    assert "subtitles" not in argv
    assert "-map 0:s" not in argv
    assert "-c:s" not in argv
    assert manifest.normalized_caption["mode"] == "SIDECAR_SRT_ONLY"
    assert manifest.caption_schedule["render_consumes_caption_cues"] is False
    assert command.expected_qc["subtitle_stream_count"] == 0
    assert command.expected_qc["drawtext_filtergraph_path"] == str(
        command.generated_filtergraph_path
    )
    assert command.expected_qc["drawtext_filtergraph_checksum_sha256"] == _sha256(
        Path(command.generated_filtergraph_path)
    )
    assert command.expected_qc["native_explanatory_drawtext_count"] == 4
    assert command.expected_qc["semantic_overlay_drawtext_count"] == 4
    assert len(command.expected_qc["semantic_overlay_drawtext_filter_hashes"]) == 4


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
    assert graph.count("drawtext=") == 5
    assert command.expected_qc["native_explanatory_drawtext_count"] == 4
    assert command.expected_qc["semantic_overlay_drawtext_count"] == 5
    assert len(command.expected_qc["semantic_overlay_drawtext_filter_hashes"]) == 5
    assert any(
        Path(path).read_text(encoding="utf-8").strip() == overlay["text"]
        for path in command.generated_text_files
    )


def test_native_visual_rejects_narration_text_as_visible_headline(
    tmp_path: Path,
) -> None:
    builder = _builder(tmp_path)
    audio = _fixture_audio(tmp_path, ffmpeg=builder.ffmpeg)
    manifest = _manifest(audio_path=audio)
    scene = manifest.compiled_scenes[0]
    narration = str(scene["narration_unit_text"])
    spec = dict(scene["native_visual_spec"])
    spec["headline"] = narration
    spec["headline_hash"] = _text_sha256(narration)
    spec_body = {key: value for key, value in spec.items() if key != "content_hash"}
    spec["content_hash"] = stable_hash(spec_body)
    scene["native_visual_spec"] = spec

    with pytest.raises(ValueError, match="V2_NATIVE_EXPLANATORY_VISUAL_INVALID"):
        builder.build_v2_local_native(
            manifest,
            run_key="narration-as-visual",
            audio_path=audio,
        )


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
        subprocess.run(
            command.sanitized_argv, check=True, capture_output=True, text=True
        )
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
        qc = NativeMediaQC(builder.ffprobe, ffmpeg=builder.ffmpeg).inspect(
            Path(command.output_file), command.expected_qc, command.run_key
        )
        results.append(
            (
                codec_types,
                float(probe_payload["format"]["duration"]),
                graph.count("drawtext="),
                command,
                qc,
            )
        )

    assert results[0][0].count("video") == 1
    assert results[0][0].count("audio") == 1
    assert "subtitle" not in results[0][0]
    assert results[0][1] == pytest.approx(1.0, abs=0.05)
    assert results[0][2] == 4
    assert results[1][2] == 5
    assert results[1][3].expected_qc["semantic_overlay_drawtext_count"] == 5
    for result in results:
        assert result[4].result == "PASS"
        assert result[4].checks["drawtext_filtergraph_attested"] is True
        assert result[4].checks["narration_drawtext_count"] == 0
        assert result[4].checks["drawtext_filter_count"] == result[2]
        assert result[4].checks["native_explanatory_plan_attested"] is True
        assert result[4].checks["native_explanatory_visual_present"] is True
        assert result[4].checks["native_explanatory_drawtext_count"] == 4
    assert results[0][4].checks["semantic_overlay_drawtext_count"] == 4
    assert results[1][4].checks["semantic_overlay_drawtext_count"] == 5


def test_native_media_qc_rejects_attested_unapproved_drawtext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "candidate.mp4"
    output.write_bytes(b"\x00\x00\x00\x08ftyp\x00\x00\x00\x08moov\x00\x00\x00\x08mdat")
    filtergraph = tmp_path / "filtergraph.txt"
    filtergraph.write_text(
        "[0:v]format=yuv420p,drawtext=text='Narration must remain audio-only'[v]\n",
        encoding="utf-8",
    )
    probe_payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
                "color_space": "bt709",
                "duration": "1.0",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "duration": "1.0",
            },
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "1.0",
        },
    }

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "ffprobe":
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps(probe_payload), stderr=""
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("app.services.native_media_qc.subprocess.run", fake_run)
    expected = {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "pix_fmt": "yuv420p",
        "audio_codec": "aac",
        "sample_rate": 48000,
        "channels": 2,
        "expected_duration_seconds": 1.0,
        "narration_drawtext_count": 0,
        "semantic_overlay_drawtext_count": 0,
        "drawtext_filtergraph_path": str(filtergraph),
        "drawtext_filtergraph_checksum_sha256": _sha256(filtergraph),
        "semantic_overlay_drawtext_filter_hashes": [],
        "faststart": True,
    }

    report = NativeMediaQC("ffprobe", ffmpeg="ffmpeg").inspect(
        output, expected, "unapproved-drawtext"
    )

    assert report.result == "FAIL"
    assert report.reason_codes == ["QC_NARRATION_DRAWTEXT_COUNT"]
    assert report.checks["drawtext_filtergraph_attested"] is True
    assert report.checks["drawtext_filter_count"] == 1
    assert report.checks["semantic_overlay_drawtext_count"] == 0
    assert report.checks["narration_drawtext_count"] == 1


def test_native_media_qc_rejects_blank_card_despite_valid_plan(
    tmp_path: Path,
) -> None:
    ffmpeg = _ffmpeg_binary("ffmpeg")
    ffprobe = _ffmpeg_binary("ffprobe")
    output = tmp_path / "blank-card.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x17324d:s=320x180:r=30:d=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
    )
    spec = _native_visual_spec(
        scene_id="scene-001",
        narration_text="Narration remains audio only.",
        headline="A verified semantic relationship",
    )
    plan_body = {
        "schema_version": "vcos.native-explanatory-render-plan.v1",
        "compiled_manifest_hash": "c" * 64,
        "scene_specs": [spec],
        "presentation_window_policy": {
            "schema_version": "vcos.native-presentation-window-policy.v1",
            "canonical_duration_ms": 1_000,
            "maximum_silence_hold_ms": 2_000,
            "binding_authority": "ELEVENLABS_FORCED_ALIGNMENT_WORD_BOUNDARIES",
            "presentation_policy": "HOLD_PRECEDING_SCENE_ACROSS_BOUNDED_SILENCE",
            "spoken_word_timing_unchanged": True,
            "timing_synthesized": False,
            "windows": [
                {
                    "scene_id": "scene-001",
                    "binding_start_ms": 0,
                    "binding_end_ms": 1_000,
                    "presentation_start_ms": 0,
                    "presentation_end_ms": 1_000,
                    "leading_silence_hold_ms": 0,
                    "trailing_silence_hold_ms": 0,
                }
            ],
        },
    }
    plan_body["presentation_window_policy"]["content_hash"] = stable_hash(
        plan_body["presentation_window_policy"]
    )
    plan = {**plan_body, "content_hash": stable_hash(plan_body)}
    plan_path = tmp_path / "native-explanatory-render-plan.json"
    plan_path.write_text(
        json.dumps(plan, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    expected = {
        "width": 320,
        "height": 180,
        "fps": 30,
        "pix_fmt": "yuv420p",
        "audio_codec": "aac",
        "sample_rate": 48000,
        "channels": 2,
        "expected_duration_seconds": 1.0,
        "native_explanatory_visual_check_required": True,
        "native_explanatory_plan_path": str(plan_path),
        "native_explanatory_plan_checksum_sha256": _sha256(plan_path),
        "native_explanatory_plan_hash": plan["content_hash"],
        "native_explanatory_scene_count": 1,
        "native_presentation_window_policy_hash": plan_body[
            "presentation_window_policy"
        ]["content_hash"],
        "native_visual_probe_seconds": [0.5],
        "faststart": True,
    }

    report = NativeMediaQC(ffprobe, ffmpeg=ffmpeg).inspect(
        output, expected, "blank-card"
    )

    assert report.checks["native_explanatory_plan_attested"] is True
    assert report.checks["native_explanatory_visual_present"] is False
    assert "QC_NATIVE_EXPLANATORY_VISUAL_PRESENT" in report.reason_codes
