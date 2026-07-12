from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.contracts.native_renderer import CanvasSpec, NativeRenderPlan, NativeRenderScene
from app.services.native_ffmpeg_renderer import FFmpegCommandBuilder, NativeFFmpegRenderer, _inside
from app.services.native_motion_compiler import MOTION_PACK, NativeMotionCompiler
from app.services.native_render_plan import NativeRenderPlanValidator, canonical_plan_hash
from app.services.provider_stack import CANONICAL_PROVIDER_KEYS, LOCAL_CAPABILITY_KEYS


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "var/tmp/native_renderer"
SRT = WORK / "fixtures/nr1_smoke.srt"


def plan(**changes):
    scenes = [
        NativeRenderScene(scene_id="s1", source_segment_ids=["seg-1"], narration_start_ms=0, narration_end_ms=4000, duration_ms=4000, visual_treatment="NATIVE_SLIDE", layout_type="TITLE", animation_type="HOLD_STATIC", transition_out="FADE_SOFT", originality_role="HOOK"),
        NativeRenderScene(scene_id="s2", source_segment_ids=["seg-2"], narration_start_ms=4000, narration_end_ms=8000, duration_ms=4000, visual_treatment="DATA_CARD", layout_type="DATA", animation_type="HIGHLIGHT", transition_out="DISSOLVE", originality_role="EXPLANATION"),
        NativeRenderScene(scene_id="s3", source_segment_ids=["seg-3"], narration_start_ms=8000, narration_end_ms=12000, duration_ms=4000, visual_treatment="STATIC_COMPOSITION", layout_type="STILL", animation_type="SLOW_ZOOM_IN", originality_role="TAKEAWAY"),
    ]
    data = dict(plan_id="nr1-smoke-plan", plan_version=1, package_id="nr1-smoke", video_project_id="synthetic-project", company_id="synthetic-company", channel_id="small-team-ai", channel_profile_version_id="frozen-profile-v1", effective_context_snapshot_id="frozen-context-v1", effective_context_hash="ctx-hash", format_identity_contract_ref="f4ef71b1-6942-49c4-bb69-47244751265d", format_identity_contract_hash="approved-format-hash", format_identity_status="APPROVED", episode_originality_manifest_ref="d0bb74e3-eb8c-44ac-a1d8-b165892e176b", episode_originality_manifest_hash="ofv0-manifest-hash", final_originality_gate="PASS", script_ref="synthetic-script", script_hash="script-hash", srt_ref=str(SRT), srt_hash="srt-hash", visual_plan_ref="approved-visual-plan", visual_plan_hash="visual-plan-hash", canvas_spec=CanvasSpec(width=1920, height=1080), scenes=scenes, global_motion_policy={"motion_pack": "NativeMotionPack_v1"}, caption_policy={"preset": "caption_burn_ass_v1", "srt_ref": str(SRT)}, audio_policy={"preset": "voice_only_basic"}, output_profiles=["YT_LONG_1080P30_SDR_H264_VT"], purpose="NR1_LOCAL_SYNTHETIC_SMOKE", production_eligible=False, status="APPROVED", created_at=datetime.now(UTC), created_by="codex-nr1")
    data.update(changes); result = NativeRenderPlan(**data); result.content_hash = canonical_plan_hash(result); return result


def test_stack_reconciliation_is_local_not_paid():
    assert CANONICAL_PROVIDER_KEYS == ("elevenlabs", "google_veo", "pexels_api")
    assert LOCAL_CAPABILITY_KEYS == ("native_ffmpeg_renderer",)


def test_motion_pack_contract_and_deterministic_compile():
    assert {"cut", "fade_soft", "kenburns_center_soft", "lowerthird_slidein", "caption_burn_ass_v1", "voice_only_basic"} <= MOTION_PACK.keys()
    a = NativeMotionCompiler().compile(plan()); b = NativeMotionCompiler().compile(plan())
    assert a.manifest_hash == b.manifest_hash


@pytest.mark.parametrize("change,code", [({"format_identity_status": "PENDING_HUMAN_APPROVAL"}, "FORMAT_IDENTITY_NOT_APPROVED"), ({"character_policy_mode": "CHARACTER_ALLOWED"}, "CHARACTER_POLICY_CONFLICT")])
def test_deterministic_blocks(change, code):
    with pytest.raises(ValueError, match=code): NativeMotionCompiler().compile(plan(**change))


def test_raw_filter_and_unsupported_motion_rejected():
    p = plan(); p.scenes[0].animation_type = "scale=2;rm -rf"; p.content_hash = canonical_plan_hash(p)
    with pytest.raises(ValueError, match="RAW_FILTER_SYNTAX_REJECTED"): NativeMotionCompiler().compile(p)


def test_path_security(tmp_path):
    with pytest.raises(ValueError, match="PATH_TRAVERSAL"): _inside(tmp_path, Path("../escape"))
    with pytest.raises(ValueError, match="OUTSIDE_WORKSPACE"): _inside(tmp_path, Path("/tmp/outside"))


def test_production_execution_disabled_by_default():
    manifest = NativeMotionCompiler().compile(plan(production_eligible=True, purpose="PRODUCTION"))
    renderer = NativeFFmpegRenderer(WORK, smoke_enabled=False, production_enabled=False)
    with pytest.raises(PermissionError, match="PRODUCTION_RENDER_DISABLED"):
        renderer.execute(manifest, object(), purpose="PRODUCTION")


def test_nr1_local_synthetic_smoke():
    p = plan(); manifest = NativeMotionCompiler().compile(p)
    run_key = "nr1-local-synthetic-smoke"
    command = FFmpegCommandBuilder(WORK).build_synthetic(manifest, run_key=run_key, duration_seconds=12)
    work = WORK / "runs" / run_key
    (work / "native_render_plan.json").write_text(p.model_dump_json(indent=2), encoding="utf-8")
    (work / "compiled_manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    receipt, qc = NativeFFmpegRenderer(WORK, smoke_enabled=True, production_enabled=False).execute(manifest, command, purpose="NR1_LOCAL_SYNTHETIC_SMOKE")
    assert qc.result == "PASS" and receipt.no_provider_calls_confirmed and not receipt.production_eligible
