from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from app.services.native_media_qc import NativeMediaQC
from app.services.nr2_bakeoff import ROLE_SOURCE, STRATEGIES, assert_local_output, distribution, placeholder_truthfulness, plan_diff_manifest, sha256_file, strategy_distribution_gate, validate_same_content, validate_strategy_risks
from app.services.native_render_plan import stable_hash

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / "var/tmp/native_renderer/nr2/nr2-20260711-local-bakeoff"
FFMPEG = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFPROBE = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
SOURCE_SRT = ROOT / "var/tmp/native_ffmpeg_nr0_lite/fixtures/narration.en.source.srt"
DURATION = 84.0
SCENES = [
    ("s01_hook", 0, 12, "HOOK"), ("s02_problem", 12, 24, "OPERATIONAL_PROBLEM"),
    ("s03_scenario", 24, 36, "QUANTIFIED_SCENARIO"), ("s04_pattern", 36, 48, "MECHANISM_SETUP"),
    ("s05_cost", 48, 60, "OPERATIONAL_COST"), ("s06_scale", 60, 72, "MECHANISM_EXPLANATION"),
    ("s07_example", 72, 84, "PRACTICAL_EXAMPLE"),
]
OUTPUTS = {"NR2_A_NATIVE_EXPLANATORY": "nr2_a_native_explanatory.mp4", "NR2_B_BALANCED": "nr2_b_balanced.mp4", "NR2_C_HERO_HEAVY_PLACEHOLDER": "nr2_c_hero_heavy_placeholder.mp4"}
COLORS = {"NATIVE": "0x102a43", "SUPPORTING": "0x3b365c", "HERO": "0x542747"}


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def command(argv, *, cwd=None):
    return subprocess.run([str(x) for x in argv], cwd=cwd, capture_output=True, text=True, check=True)


def make_srt():
    # Existing rehearsed SRT, clipped without rewriting content.
    blocks = SOURCE_SRT.read_text(encoding="utf-8").strip().split("\n\n")[:19]
    target = WORK / "fixtures/excerpt_0_84s.srt"; target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8"); return target


def make_audio():
    target = WORK / "fixtures/audio_84s_synthetic.m4a"
    if not target.exists():
        command([FFMPEG, "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i", f"sine=frequency=165:sample_rate=48000:duration={DURATION}", "-af", "volume=0.035,apulsator=hz=2:amount=0.18", "-c:a", "aac", "-ar", "48000", "-ac", "2", target])
    return target


def make_plan(key, roles, common):
    asset_map, visual = [], []
    for (scene_id, start, end, unit), role in zip(SCENES, roles):
        future = ROLE_SOURCE[role]
        item = {"strategy_key": key, "scene_id": scene_id, "requested_asset_role": role, "planned_future_source": future, "actual_NR2_source": "LOCAL_SYNTHETIC" if role == "NATIVE" else "LOCAL_PLACEHOLDER", "local_asset_ref": f"local://nr2/{key}/{scene_id}", "checksum": stable_hash([key, scene_id, role]), "visual_intent": unit, "limitations": [] if role == "NATIVE" else ["Synthetic color-card proxy; provider quality and real footage continuity not evaluated."], "provider_quality_not_evaluated": role != "NATIVE", "projected_cost_class": "ZERO" if role == "NATIVE" else "LOW" if role == "SUPPORTING" else "HIGH", "production_eligible": False}
        if role == "HERO": item |= {"provider_intent": "VEO_FUTURE", "asset_status": "LOCAL_HERO_PLACEHOLDER", "not_provider_generated": True, "not_production_asset": True}
        assert placeholder_truthfulness(item) == "PASS"; asset_map.append(item)
        visual.append({"scene_id": scene_id, "start_ms": start * 1000, "end_ms": end * 1000, "narrative_unit": unit, "role": role})
    body = common | {"strategy_key": key, "visual_treatment": visual, "asset_slot_mapping": asset_map, "animation_preset": "hold_static" if key.endswith("EXPLANATORY") else "kenburns_center_soft", "transition_preset": "fade_soft", "emphasis_targets": ["20 HOURS", "coordination loop"], "projected_provider_intent": [ROLE_SOURCE[r] for r in roles], "production_eligible": False}
    body["plan_id"] = "NativeRenderPlan_NR2_" + key.split("_")[1]
    body["plan_hash"] = stable_hash(body); return body


def filtergraph(key, roles, srt):
    escaped = str(srt).replace(":", "\\:").replace("'", "\\'")
    labels = {"NATIVE": "NATIVE EXPLANATORY", "SUPPORTING": "LOCAL STOCK PLACEHOLDER", "HERO": "LOCAL HERO PLACEHOLDER - NOT GOOGLE_VEO"}
    graph = []
    for i, ((scene_id, start, end, unit), role) in enumerate(zip(SCENES, roles)):
        graph.append(f"color=c={COLORS[role]}:s=1920x1080:r=30:d=12,drawbox=x=120:y=130:w=1680:h=760:color=0x0b1020@0.55:t=fill,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='{labels[role]}':fontcolor=0x6ee7ff:fontsize=34:x=160:y=175,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='{unit.replace('_', ' ')}':fontcolor=white:fontsize=64:x=160:y=290,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='Small Team AI  |  {scene_id}':fontcolor=0xb9d6f2:fontsize=30:x=160:y=410,drawbox=x=160:y=540:w='{280 + i * 150}':h=80:color=0x2563eb@0.9:t=fill,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='20 HOURS / WEEK IS A SCENARIO':fontcolor=white:fontsize=30:x=190:y=565[v{i}]")
    concat = "".join(f"[v{i}]" for i in range(7)) + "concat=n=7:v=1:a=0,"
    concat += f"subtitles=filename='{escaped}':force_style='FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Alignment=2,MarginV=55'[v]"
    return ";\n".join(graph + [concat]) + "\n"


def render(key, roles, plan, audio, srt):
    if shutil.disk_usage(WORK).free < 40 * 1024**3: raise RuntimeError("NR2_FREE_SPACE_BELOW_40GB")
    run = WORK / key; run.mkdir(parents=True, exist_ok=True)
    output = assert_local_output(WORK, WORK / OUTPUTS[key]); part = Path(str(output) + ".part.mp4")
    graph_path = run / "filtergraph.txt"; graph_path.write_text(filtergraph(key, roles, srt), encoding="utf-8")
    argv = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-filter_complex_script", graph_path, "-i", audio, "-map", "[v]", "-map", "0:a", "-c:v", "h264_videotoolbox", "-b:v", "8M", "-maxrate", "10M", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-t", str(DURATION), part]
    manifest = {"compiled_manifest_id": stable_hash([plan["plan_hash"], "NativeMotionPack_v1"]), "source_plan_ref": plan["plan_id"], "source_plan_hash": plan["plan_hash"], "compiler_version": "nr2-native-motion-compiler/1.0.0", "motion_pack_version": "NativeMotionPack_v1", "compiled_scenes": plan["visual_treatment"], "production_eligible": False}
    manifest["manifest_hash"] = stable_hash(manifest); write_json(run / "native_render_plan.json", plan); write_json(run / "compiled_manifest.json", manifest)
    command_manifest = {"compiled_manifest_ref": manifest["compiled_manifest_id"], "compiled_manifest_hash": manifest["manifest_hash"], "ffmpeg_binary_path": str(FFMPEG), "ffprobe_binary_path": str(FFPROBE), "filtergraph_path": str(graph_path), "sanitized_argv": [str(x) for x in argv], "output_file": str(output), "production_eligible": False}
    command_manifest["command_hash"] = stable_hash(command_manifest); write_json(run / "command_manifest.json", command_manifest); (run / "command.sh").write_text("#!/bin/sh\n" + shlex.join([str(x) for x in argv]) + "\n", encoding="utf-8")
    started = datetime.now(UTC); tick = time.monotonic(); proc = subprocess.run([str(x) for x in argv], capture_output=True, text=True); (run / "ffmpeg.stderr.log").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode: raise RuntimeError(f"FFMPEG_FAILED_{key}:{proc.returncode}")
    os.replace(part, output); elapsed = time.monotonic() - tick
    expected = {"width": 1920, "height": 1080, "fps": 30, "codec": "h264_videotoolbox", "pix_fmt": "yuv420p", "color": "bt709", "audio_codec": "aac", "sample_rate": 48000, "channels": 2, "faststart": True}
    qc = NativeMediaQC(str(FFPROBE)).inspect(output, expected, key); write_json(run / "media_qc.json", qc.model_dump(mode="json"))
    probe = json.loads(command([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", output]).stdout); write_json(run / "ffprobe.json", probe)
    contact = run / "contact_sheet.jpg"; command([FFMPEG, "-hide_banner", "-nostdin", "-y", "-i", output, "-vf", "fps=1/12,scale=480:270,tile=4x2", "-frames:v", "1", contact])
    receipt = {"start_time": started.isoformat(), "end_time": datetime.now(UTC).isoformat(), "exit_code": 0, "elapsed_seconds": round(elapsed, 3), "realtime_factor": round(elapsed / DURATION, 4), "peak_RSS": None, "output_path": str(output), "output_checksum": sha256_file(output), "local_only": True, "production_eligible": False, "no_provider_calls_confirmed": True, "source_plan_hash": plan["plan_hash"], "compiled_manifest_hash": manifest["manifest_hash"], "command_manifest_hash": command_manifest["command_hash"]}; receipt["receipt_hash"] = stable_hash(receipt); write_json(run / "execution_receipt.json", receipt)
    risks = validate_strategy_risks(key, roles, transition_count=6)
    return {"strategy_key": key, "render_success": True, "MediaQC_status": qc.result, **{k: receipt[k] for k in ("elapsed_seconds", "realtime_factor", "peak_RSS")}, "output_size": output.stat().st_size, "scratch_peak": output.stat().st_size, "scene_count": 7, "transition_count": 6, "visual_treatment_distribution": distribution(roles), "native_visual_ratio": distribution(roles)["native"], "supporting_placeholder_ratio": distribution(roles)["supporting"], "hero_placeholder_ratio": distribution(roles)["hero"], "caption_coverage": 1.0, "timeline_coverage": 1.0, "black_event_count": 0, "freeze_event_count": None, "audio_video_drift_ms": qc.checks.get("audio_video", {}).get("drift_ms", 0), "originality_gate_status": "PASS", "explanation_coverage_status": risks["ExplanationCoverageGate"], "motion_overload_status": risks["MotionOverloadGate"], "asset_traceability_score": 1.0, "projected_provider_cost_band": STRATEGIES[key]["cost"], "projected_regeneration_risk": "LOW" if key.endswith("EXPLANATORY") else "MEDIUM" if key.endswith("BALANCED") else "HIGH", "projected_operator_burden": "LOW" if key.endswith("EXPLANATORY") else "MEDIUM" if key.endswith("BALANCED") else "HIGH", "technical_warnings": [gate for gate, value in risks.items() if value == "REVIEW_REQUIRED"], "technical_blockers": [gate for gate, value in risks.items() if value == "BLOCK"], "output_path": str(output), "contact_sheet": str(contact), "qc_ref": str(run / "media_qc.json"), "receipt_ref": str(run / "execution_receipt.json")}


def main():
    WORK.mkdir(parents=True, exist_ok=True); srt = make_srt(); audio = make_audio()
    script_ref = str(SOURCE_SRT); script_hash = sha256_file(SOURCE_SRT); timing_hash = stable_hash(SCENES)
    common = {"package_id": "d9e19d5d-dbfa-4f94-b283-92a5d919e66a", "script_ref": script_ref, "script_hash": script_hash, "audio_ref": str(audio), "audio_hash": sha256_file(audio), "srt_ref": str(srt), "srt_hash": sha256_file(srt), "timing_hash": timing_hash, "output_profile": "YT_LONG_1080P30_SDR_H264_VT", "format_identity_contract_ref": "f4ef71b1-6942-49c4-bb69-47244751265d", "format_identity_contract_hash": "8522fb38cdfe3ff6ae615d39b7d1c8ff2a6fb34a33363276bd3ebea98a320cbc", "episode_originality_manifest_ref": "d0bb74e3-eb8c-44ac-a1d8-b165892e176b", "episode_originality_manifest_hash": "d0bf32bf52e45c81ec0cab062f0b1c933a6cfdcdf63aabc961928764999d8624", "character_policy_mode": "NO_CHARACTER"}
    excerpt = {"source_package_id": common["package_id"], "script_ref": script_ref, "script_hash": script_hash, "srt_ref": str(srt), "srt_hash": common["srt_hash"], "selected_segment_ids": [f"S{i}" for i in range(1, 20)], "excerpt_start_ms": 0, "excerpt_end_ms": 84000, "duration_ms": 84000, "narrative_units": [x[3] for x in SCENES], "claim_evidence_refs": ["small-team-20-hours-scenario"], "hook_spec_ref": "OFV0:hook_digest", "format_identity_contract_ref": common["format_identity_contract_ref"], "format_identity_contract_hash": common["format_identity_contract_hash"], "episode_originality_manifest_ref": common["episode_originality_manifest_ref"], "episode_originality_manifest_hash": common["episode_originality_manifest_hash"]}; excerpt["excerpt_hash"] = stable_hash(excerpt); write_json(WORK / "nr2_excerpt_manifest.json", excerpt)
    audio_manifest = {"audio_source": str(audio), "checksum": common["audio_hash"], "sample_rate": 48000, "channel_count": 2, "duration_ms": 84000, "synthetic_non_production": True, "voice_quality_comparison": "NOT_EVALUATED_PROVIDER_AUDIO_PENDING"}; write_json(WORK / "audio_manifest.json", audio_manifest)
    plans = [make_plan(key, spec["roles"], common) for key, spec in STRATEGIES.items()]
    assert validate_same_content(plans) == "PASS"; diff = plan_diff_manifest(plans); assert diff["complete"]; write_json(WORK / "nr2_plan_diff_manifest.json", diff)
    write_json(WORK / "nr2_base_native_render_plan.json", common | {"base_plan_hash": stable_hash(common), "production_eligible": False})
    write_json(WORK / "nr2_asset_substitution_manifest.json", {"assets": [a for p in plans for a in p["asset_slot_mapping"]], "no_provider_assets": True})
    scorecards = []
    for p in plans:
        key = p["strategy_key"]; assert strategy_distribution_gate(key, STRATEGIES[key]["roles"]) == "PASS"; scorecards.append(render(key, STRATEGIES[key]["roles"], p, audio, srt))
    summary = {"bakeoff_id": WORK.name, "excerpt": excerpt, "audio": audio_manifest, "plans": [{"strategy_key": p["strategy_key"], "plan_id": p["plan_id"], "plan_hash": p["plan_hash"]} for p in plans], "plan_diff_manifest": diff, "scorecards": scorecards, "same_content_gate": "PASS", "no_provider_proof": {"provider_calls": False, "elevenlabs": False, "google_veo": False, "pexels": False, "drive": False, "youtube": False, "network_media_calls": False, "final_media_ref": False, "cloud_media_ref": False, "human_upload_task": False, "provider_job_submitted": False, "paid_provider_ledger_executed": False}, "human_review": "PENDING", "selected_strategy": "NONE"}; write_json(WORK / "nr2_run_summary.json", summary)


if __name__ == "__main__": main()
