from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.contracts.ai_visual_production import (
    MotionIntentProjection,
    NormalizedPoint,
    NormalizedRegion,
    VideoMotionGrammar,
    seal_content_payload,
)
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services import v2_ai_visual_renderer as renderer_module
from app.services.v2_ai_visual_renderer import (
    AIVisualFFmpegAssemblyCompiler,
    AIVisualFFmpegAssemblyRenderer,
    AIVisualRenderExecutionReceipt,
    AIVisualRenderQC,
    VerifiedAIVisualAsset,
    build_ai_visual_asset_manifest,
)


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
RUNTIME_AVAILABLE = bool(FFMPEG and FFPROBE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(*arguments: str) -> None:
    subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _image(path: Path, *, source: str = "testsrc2") -> None:
    _run(
        "-f",
        "lavfi",
        "-i",
        f"{source}=size=320x180:rate=30",
        "-frames:v",
        "1",
        "-update",
        "1",
        str(path),
    )


def _video(path: Path, *, duration_seconds: int = 8) -> None:
    _run(
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=320x180:rate=24:duration={duration_seconds}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(path),
    )


def _audio(path: Path, *, duration_ms: int) -> None:
    _run(
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=48000:duration={duration_ms / 1000}",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(path),
    )


def _srt(path: Path, *, duration_ms: int) -> None:
    seconds, milliseconds = divmod(duration_ms, 1000)
    path.write_text(
        "1\n00:00:00,000 --> "
        f"00:00:{seconds:02d},{milliseconds:03d}\n"
        "Verified sidecar caption.\n",
        encoding="utf-8",
    )


def _effect_plan(
    *,
    scene_specs: list[tuple[str, int, int, str, str, str]],
    primary_refs: list[str],
    primary_hashes: list[str],
):
    style_hash = "a" * 64
    grammar = VideoMotionGrammar.production_default(
        grammar_id="ai-renderer-test-grammar",
        style_bible_hash=style_hash,
        maximum_aggressive_transition_rate=1.0,
    )
    projections: list[MotionIntentProjection] = []
    for index, (
        (route, start_ms, end_ms, preset, camera, transition),
        ref,
        checksum,
    ) in enumerate(
        zip(scene_specs, primary_refs, primary_hashes, strict=True), start=1
    ):
        if camera == "PUSH_IN":
            start_scale, end_scale = 1.0, 1.045
        elif camera == "PULL_OUT":
            start_scale, end_scale = 1.045, 1.0
        elif route == "AI_VIDEO":
            start_scale = end_scale = 1.0
        else:
            start_scale = end_scale = 1.04
        body = {
            "schema_version": "vcos.motion-intent-projection.v1",
            "scene_id": f"scene-{index}",
            "scene_plan_hash": f"{index:x}" * 64,
            "style_bible_hash": style_hash,
            "motion_grammar_hash": grammar.content_hash,
            "primary_asset_ref": ref,
            "primary_asset_hash": checksum,
            "asset_type": route,
            "motion_function": "FOCUS" if index == 1 else "FOLLOW",
            "camera_motion": camera,
            "motion_preset": preset,
            "subject_anchor": "CENTER",
            "custom_subject_anchor": None,
            "focal_point": NormalizedPoint(x=0.5, y=0.5),
            "safe_crop_region": NormalizedRegion(
                x=0.04, y=0.04, width=0.92, height=0.92
            ),
            "intensity": "SUBTLE",
            "start_scale": start_scale,
            "end_scale": end_scale,
            "presentation_start_ms": start_ms,
            "presentation_end_ms": end_ms,
            "transition_in": "cut" if index == 1 else scene_specs[index - 2][5],
            "transition_out": transition,
            "transition_semantic_reason": "CONCLUSION"
            if index == len(scene_specs)
            else "CONTINUATION",
            "motion_semantic_reason": "Meaning-bound fixture presentation motion.",
            "safe_area_constraints": [
                "primary subject remains inside the normalized safe crop region"
            ],
        }
        projections.append(MotionIntentProjection(**seal_content_payload(body)))
    return NativeMotionCompiler().compile_effect_plan(
        projections, motion_grammar=grammar
    )


def _asset(
    *,
    path: Path,
    route: str,
    scene_id: str,
    scene_hash: str,
    slot: str,
    primary_ref: str,
    duration_ms: int | None = None,
    fps: float | None = None,
) -> VerifiedAIVisualAsset:
    checksum = _sha(path)
    return VerifiedAIVisualAsset.build(
        asset_slot_id=slot,
        primary_asset_owner_scene_id=scene_id,
        bound_scene_ids=[scene_id],
        bound_scene_plan_hashes=[scene_hash],
        route=route,
        asset_acquisition_mode="GENERATED",
        provider_key="google_gemini_image" if route == "AI_IMAGE" else "google_veo",
        model_id="gemini-3.1-flash-image"
        if route == "AI_IMAGE"
        else "veo-3.1-fast-generate-preview",
        asset_effect_ref=f"effect://{slot}",
        asset_effect_identity_hash=hashlib.sha256(
            f"effect:{slot}".encode()
        ).hexdigest(),
        primary_asset_ref=primary_ref,
        primary_asset_hash=checksum,
        output_ref=str(path),
        output_checksum=checksum,
        output_size_bytes=path.stat().st_size,
        output_content_type="image/png" if route == "AI_IMAGE" else "video/mp4",
        width=320,
        height=180,
        duration_ms=duration_ms,
        fps=fps,
        qc_ref=f"qc://{slot}",
        qc_hash=hashlib.sha256(f"qc:{slot}".encode()).hexdigest(),
        asset_receipt_hash=hashlib.sha256(f"receipt:{slot}".encode()).hexdigest(),
    )


def _manifest(*, assets, effect):
    return build_ai_visual_asset_manifest(
        manifest_id="renderer-test-manifest",
        production_visual_policy_ref="catalog://production-visual-policy/ai-only/v1",
        production_visual_policy_hash="b" * 64,
        scene_plan_ref="artifact://scene-plan/test",
        scene_plan_hash="c" * 64,
        style_bible_ref="artifact://style-bible/test",
        style_bible_hash="a" * 64,
        motion_grammar_ref=effect.motion_grammar_ref,
        motion_grammar_hash=effect.motion_grammar_hash,
        effect_plan_ref="artifact://effect-plan/test",
        effect_plan_hash=effect.effect_plan_hash,
        assets=assets,
    )


@pytest.mark.skipif(not RUNTIME_AVAILABLE, reason="FFmpeg runtime unavailable")
def test_ai_only_renderer_executes_motion_and_qc_with_srt_sidecar(tmp_path: Path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    audio = tmp_path / "narration.wav"
    srt = tmp_path / "captions.srt"
    _image(first)
    _image(second, source="smptebars")
    _audio(audio, duration_ms=2_000)
    _srt(srt, duration_ms=2_000)
    refs = ["artifact://ai-image/one", "artifact://ai-image/two"]
    effect = _effect_plan(
        scene_specs=[
            ("AI_IMAGE", 0, 1_000, "pushin_slow", "PUSH_IN", "fade_soft"),
            ("AI_IMAGE", 1_000, 2_000, "pan_right_slow", "PAN_RIGHT", "fade_soft"),
        ],
        primary_refs=refs,
        primary_hashes=[_sha(first), _sha(second)],
    )
    assets = [
        _asset(
            path=first,
            route="AI_IMAGE",
            scene_id="scene-1",
            scene_hash="1" * 64,
            slot="slot-1",
            primary_ref=refs[0],
        ),
        _asset(
            path=second,
            route="AI_IMAGE",
            scene_id="scene-2",
            scene_hash="2" * 64,
            slot="slot-2",
            primary_ref=refs[1],
        ),
    ]
    manifest = _manifest(assets=assets, effect=effect)
    compiler = AIVisualFFmpegAssemblyCompiler(ffprobe=str(FFPROBE))
    plan = compiler.compile(
        manifest=manifest,
        effect_plan=effect,
        audio_ref=str(audio),
        audio_checksum=_sha(audio),
        audio_duration_ms=2_000,
        srt_ref=str(srt),
        srt_checksum=_sha(srt),
        workspace_root=tmp_path,
    )
    renderer = AIVisualFFmpegAssemblyRenderer(ffmpeg=str(FFMPEG))
    command = renderer.compile_command(plan, output_ref="assembled.mp4")
    assert "zoompan=" in command.filtergraph_artifact
    assert "xfade=" in command.filtergraph_artifact
    assert "drawtext" not in command.filtergraph_artifact
    assert str(srt) not in command.ffmpeg_argv
    assert command.provider_video_audio_mapped is False

    receipt = renderer.execute(plan, output_ref="assembled.mp4")
    qc = AIVisualRenderQC(ffprobe=str(FFPROBE), ffmpeg=str(FFMPEG)).inspect(
        plan=plan, receipt=receipt
    )
    assert qc.result == "PASS"
    assert qc.checks["all_primary_visuals_ai_generated"] is True
    assert qc.checks["renderer_primary_visual_generation"] is False
    assert qc.checks["renderer_effect_composition"] is True
    assert qc.checks["subtitle_stream_count"] == 0
    assert all(item["observed"] is True for item in qc.checks["motion_observations"])

    replayed_qc = AIVisualRenderQC(ffprobe=str(FFPROBE), ffmpeg=str(FFMPEG)).inspect(
        plan=plan, receipt=receipt
    )
    assert replayed_qc == qc
    assert replayed_qc.created_at == receipt.completed_at


@pytest.mark.skipif(not RUNTIME_AVAILABLE, reason="FFmpeg runtime unavailable")
def test_render_crash_after_output_commit_reconciles_exact_seal_without_second_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    image = tmp_path / "asset.png"
    audio = tmp_path / "narration.wav"
    srt = tmp_path / "captions.srt"
    _image(image)
    _audio(audio, duration_ms=1_000)
    _srt(srt, duration_ms=1_000)
    primary_ref = "artifact://ai-image/crash-replay"
    effect = _effect_plan(
        scene_specs=[("AI_IMAGE", 0, 1_000, "pushin_slow", "PUSH_IN", "fade_soft")],
        primary_refs=[primary_ref],
        primary_hashes=[_sha(image)],
    )
    manifest = _manifest(
        assets=[
            _asset(
                path=image,
                route="AI_IMAGE",
                scene_id="scene-1",
                scene_hash="1" * 64,
                slot="crash-replay-slot",
                primary_ref=primary_ref,
            )
        ],
        effect=effect,
    )
    plan = AIVisualFFmpegAssemblyCompiler(ffprobe=str(FFPROBE)).compile(
        manifest=manifest,
        effect_plan=effect,
        audio_ref=str(audio),
        audio_checksum=_sha(audio),
        audio_duration_ms=1_000,
        srt_ref=str(srt),
        srt_checksum=_sha(srt),
        workspace_root=tmp_path,
    )
    renderer = AIVisualFFmpegAssemblyRenderer(ffmpeg=str(FFMPEG))
    seal_path = tmp_path / "render-completion-seal.json"
    render_invocations = 0
    original_run = renderer_module.subprocess.run

    def counting_run(arguments, *args, **kwargs):
        nonlocal render_invocations
        if isinstance(arguments, (list, tuple)) and "-filter_complex" in arguments:
            render_invocations += 1
        return original_run(arguments, *args, **kwargs)

    class InjectedProcessCrash(RuntimeError):
        pass

    def seal_completion(receipt: AIVisualRenderExecutionReceipt) -> None:
        seal_path.write_text(
            json.dumps(receipt.model_dump(mode="json"), sort_keys=True),
            encoding="utf-8",
        )

    def crash_after_output_commit(_output: Path) -> None:
        raise InjectedProcessCrash("crash after atomic output commit")

    monkeypatch.setattr(renderer_module.subprocess, "run", counting_run)
    with pytest.raises(InjectedProcessCrash):
        renderer.execute(
            plan,
            output_ref="assembled.mp4",
            seal_completion=seal_completion,
            after_output_commit=crash_after_output_commit,
        )

    output = tmp_path / "assembled.mp4"
    assert output.is_file()
    assert seal_path.is_file()
    sealed = AIVisualRenderExecutionReceipt.model_validate_json(
        seal_path.read_text(encoding="utf-8")
    )
    reconciled = renderer.reconcile_completion(
        plan,
        output_ref="assembled.mp4",
        completion_receipt=sealed,
    )
    assert reconciled == sealed
    assert render_invocations == 1
    assert reconciled.renderer_primary_visual_generation is False
    assert reconciled.command_manifest.provider_video_audio_mapped is False

    output.write_bytes(output.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="AI_VISUAL_RENDER_COMPLETION_BYTES_MISMATCH"):
        renderer.reconcile_completion(
            plan,
            output_ref="assembled.mp4",
            completion_receipt=sealed,
        )
    assert render_invocations == 1


@pytest.mark.skipif(not RUNTIME_AVAILABLE, reason="FFmpeg runtime unavailable")
def test_manifest_and_asset_byte_tampering_fail_closed(tmp_path: Path):
    image = tmp_path / "asset.png"
    audio = tmp_path / "narration.wav"
    srt = tmp_path / "captions.srt"
    _image(image)
    _audio(audio, duration_ms=1_000)
    _srt(srt, duration_ms=1_000)
    ref = "artifact://ai-image/one"
    effect = _effect_plan(
        scene_specs=[("AI_IMAGE", 0, 1_000, "pushin_slow", "PUSH_IN", "fade_soft")],
        primary_refs=[ref],
        primary_hashes=[_sha(image)],
    )
    manifest = _manifest(
        assets=[
            _asset(
                path=image,
                route="AI_IMAGE",
                scene_id="scene-1",
                scene_hash="1" * 64,
                slot="slot-1",
                primary_ref=ref,
            )
        ],
        effect=effect,
    )
    compiler = AIVisualFFmpegAssemblyCompiler(ffprobe=str(FFPROBE))
    sealed_plan = compiler.compile(
        manifest=manifest,
        effect_plan=effect,
        audio_ref=str(audio),
        audio_checksum=_sha(audio),
        audio_duration_ms=1_000,
        srt_ref=str(srt),
        srt_checksum=_sha(srt),
        workspace_root=tmp_path,
    )
    image.write_bytes(image.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="AI_VISUAL_ASSET_BYTES_HASH_MISMATCH"):
        compiler.compile(
            manifest=manifest,
            effect_plan=effect,
            audio_ref=str(audio),
            audio_checksum=_sha(audio),
            audio_duration_ms=1_000,
            srt_ref=str(srt),
            srt_checksum=_sha(srt),
            workspace_root=tmp_path,
        )
    with pytest.raises(ValueError, match="AI_VISUAL_RENDER_INPUT_BYTES_HASH_MISMATCH"):
        AIVisualFFmpegAssemblyRenderer(ffmpeg=str(FFMPEG)).compile_command(
            sealed_plan, output_ref="must-not-render.mp4"
        )

    stale = manifest.model_dump(mode="json")
    stale["scene_plan_hash"] = "d" * 64
    with pytest.raises(ValueError, match="AI_VISUAL_ASSET_MANIFEST_HASH_MISMATCH"):
        AIVisualFFmpegAssemblyCompiler(ffprobe=str(FFPROBE)).compile(
            manifest=stale,
            effect_plan=effect,
            audio_ref=str(audio),
            audio_checksum=_sha(audio),
            audio_duration_ms=1_000,
            srt_ref=str(srt),
            srt_checksum=_sha(srt),
            workspace_root=tmp_path,
        )


@pytest.mark.skipif(not RUNTIME_AVAILABLE, reason="FFmpeg runtime unavailable")
def test_exact_eight_second_veo_asset_uses_explicit_trim_without_audio_or_retime(
    tmp_path: Path,
):
    video = tmp_path / "veo-visual-only.mp4"
    audio = tmp_path / "narration.wav"
    srt = tmp_path / "captions.srt"
    _video(video, duration_seconds=8)
    _audio(audio, duration_ms=5_000)
    _srt(srt, duration_ms=5_000)
    ref = "artifact://ai-video/veo-one"
    effect = _effect_plan(
        scene_specs=[
            ("AI_VIDEO", 0, 5_000, "video_intrinsic_preserve", "STATIC", "fade_soft")
        ],
        primary_refs=[ref],
        primary_hashes=[_sha(video)],
    )
    asset = _asset(
        path=video,
        route="AI_VIDEO",
        scene_id="scene-1",
        scene_hash="1" * 64,
        slot="veo-slot",
        primary_ref=ref,
        duration_ms=8_000,
        fps=24,
    )
    manifest = _manifest(assets=[asset], effect=effect)
    compiler = AIVisualFFmpegAssemblyCompiler(ffprobe=str(FFPROBE))
    plan = compiler.compile(
        manifest=manifest,
        effect_plan=effect,
        audio_ref=str(audio),
        audio_checksum=_sha(audio),
        audio_duration_ms=5_000,
        srt_ref=str(srt),
        srt_checksum=_sha(srt),
        workspace_root=tmp_path,
    )
    command = AIVisualFFmpegAssemblyRenderer(ffmpeg=str(FFMPEG)).compile_command(
        plan, output_ref="trimmed.mp4"
    )
    policy = command.ordered_scene_mapping[0]["video_presentation_policy"]
    assert policy == {
        "strategy": "TRIM_HEAD_AND_HOLD_TRANSITION_TAIL_NO_RETIME",
        "trim_start_ms": 0,
        "trim_duration_ms": 5_000,
        "transition_tail_hold_ms": 0,
        "loop": False,
        "retime": False,
    }
    assert "trim=duration=5" in command.filtergraph_artifact
    assert command.ffmpeg_argv[command.ffmpeg_argv.index("-map") + 1] == "[vout]"
    maps = [
        command.ffmpeg_argv[index + 1]
        for index, value in enumerate(command.ffmpeg_argv[:-1])
        if value == "-map"
    ]
    assert maps == ["[vout]", "1:a:0"]

    too_long = _effect_plan(
        scene_specs=[
            ("AI_VIDEO", 0, 9_000, "video_intrinsic_preserve", "STATIC", "fade_soft")
        ],
        primary_refs=[ref],
        primary_hashes=[_sha(video)],
    )
    too_long_manifest = _manifest(assets=[asset], effect=too_long)
    with pytest.raises(ValueError, match="AI_VISUAL_VIDEO_PRESENTATION_EXCEEDS_ASSET"):
        compiler.compile(
            manifest=too_long_manifest,
            effect_plan=too_long,
            audio_ref=str(audio),
            audio_checksum=_sha(audio),
            audio_duration_ms=9_000,
            srt_ref=str(srt),
            srt_checksum=_sha(srt),
            workspace_root=tmp_path,
        )
