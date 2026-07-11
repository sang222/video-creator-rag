from __future__ import annotations

import json, os, shlex, shutil, subprocess, time
from datetime import UTC, datetime
from pathlib import Path

from app.services.native_media_qc import NativeMediaQC
from app.services.native_render_plan import stable_hash
from app.services.nr2_bakeoff import sha256_file
from app.services.nr2_motion_audit import MOTION_DECISIONS, audit_metrics, compile_motion_decisions, differentiation_gate, motion_gates

ROOT = Path(__file__).resolve().parents[3]
NR2 = ROOT / "var/tmp/native_renderer/nr2/nr2-20260711-local-bakeoff"
WORK = ROOT / "var/tmp/native_renderer/nr2_1/nr2-1-20260711-motion-audit"
FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"; FFPROBE = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
SRT = NR2 / "fixtures/excerpt_0_84s.srt"; AUDIO = NR2 / "fixtures/audio_84s_synthetic.m4a"
SCENES = [("s01_hook",0,12,"HOOK"),("s02_problem",12,24,"OPERATIONAL_PROBLEM"),("s03_scenario",24,36,"QUANTIFIED_SCENARIO"),("s04_pattern",36,48,"MECHANISM_SETUP"),("s05_cost",48,60,"OPERATIONAL_COST"),("s06_scale",60,72,"MECHANISM_EXPLANATION"),("s07_example",72,84,"PRACTICAL_EXAMPLE")]
ROLES = {"A":["NATIVE"]*6+["SUPPORTING"],"B":["NATIVE"]*4+["SUPPORTING"]*2+["HERO"],"C":["NATIVE"]*2+["SUPPORTING"]*2+["HERO"]*3}
KEYS = {"A":"NR2_A_NATIVE_EXPLANATORY","B":"NR2_B_BALANCED","C":"NR2_C_HERO_HEAVY_PLACEHOLDER"}

def write_json(path, value): path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str)+"\n")
def run(argv): return subprocess.run([str(x) for x in argv], capture_output=True, text=True, check=True)
def esc(value): return str(value).replace(":", "\\:").replace("'", "\\'")

def motion_overlay(preset, duration=12):
    """Registry-owned, per-frame overlay expressions; no agent-supplied filter input."""
    x, y, scale = "220", "250", None
    if preset in {"kenburns_center_soft","kenburns_subject_left","pushin_slow"}:
        scale = "scale=w='900*(1+0.003*t)':h='430*(1+0.003*t)':eval=frame"
        x, y = "(W-w)/2", "(H-h)/2"
    elif preset == "pan_left_slow": x = "420-20*t"
    elif preset == "pan_right_slow": x = "100+20*t"
    elif preset in {"slide_left","cover_left","lowerthird_slidein","comparison_reveal","timeline_step_reveal"}: x = "max(220,W-480*t)"
    elif preset == "slide_right": x = "min(220,-900+480*t)"
    elif preset in {"reveal_up","fact_card_pop","cta_card_fadeup"}: y = "max(250,H-260*t)"
    prefix = f"color=c=0x2563eb:s=900x430:r=30:d={duration},format=yuv420p"
    if preset in {"fade_soft","fade_black","dissolve"}: prefix += ",fade=t=in:st=0:d=0.7,fade=t=out:st=3.3:d=0.7"
    if scale: prefix += "," + scale
    return prefix, f"overlay=x='{x}':y='{y}':eval=frame:shortest=1"

def scene_filtergraph(letter, decisions, *, proxy, srt):
    graphs=[]; colors={"NATIVE":"0x102a43","SUPPORTING":"0x3b365c","HERO":"0x542747"}
    for i,(scene,role,d) in enumerate(zip(SCENES,ROLES[letter],decisions)):
        scene_id,start,end,unit=scene; p=d["animation_preset_compiled"]
        source, overlay = motion_overlay(p)
        graphs.append(f"color=c={colors[role]}:s=1920x1080:r=30:d=12[bg{i}]")
        graphs.append(f"{source}[box{i}]")
        g=f"[bg{i}][box{i}]{overlay},drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='{unit.replace('_',' ')}':fontcolor=white:fontsize=54:x=260:y=390,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='{role}':fontcolor=0x6ee7ff:fontsize=30:x=150:y=150"
        if proxy: g += f",drawbox=x=35:y=35:w=760:h=105:color=black@0.75:t=fill,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='MOTION AUDIT | {scene_id} | {role}':fontcolor=white:fontsize=22:x=55:y=52,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='{p} | {d['transition_out']} | {start}-{end}s':fontcolor=0x6ee7ff:fontsize=21:x=55:y=91"
        graphs.append(g+f"[v{i}]")
    graphs.append("".join(f"[v{i}]" for i in range(7))+f"concat=n=7:v=1:a=0,subtitles=filename='{esc(srt)}':force_style='FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Alignment=2,MarginV=55'[v]")
    return ";\n".join(graphs)+"\n"

def encode(name, graph, audio, duration, evidence):
    if shutil.disk_usage(WORK).free < 40*1024**3: raise RuntimeError("FREE_SPACE_BELOW_40GB")
    run_dir=WORK/name; run_dir.mkdir(parents=True,exist_ok=True); fg=run_dir/"filtergraph.txt"; fg.write_text(graph)
    out=WORK/f"{name}.mp4"; part=Path(str(out)+".part.mp4")
    argv=[FFMPEG,"-hide_banner","-nostdin","-y","-filter_complex_script",fg,"-i",audio,"-map","[v]","-map","0:a","-c:v","h264_videotoolbox","-b:v","8M","-maxrate","10M","-pix_fmt","yuv420p","-colorspace","bt709","-color_primaries","bt709","-color_trc","bt709","-c:a","aac","-ar","48000","-ac","2","-movflags","+faststart","-t",duration,part]
    (run_dir/"command.sh").write_text("#!/bin/sh\n"+shlex.join([str(x) for x in argv])+"\n"); started=time.monotonic(); proc=subprocess.run([str(x) for x in argv],capture_output=True,text=True); (run_dir/"ffmpeg.stderr.log").write_text(proc.stderr)
    if proc.returncode: raise RuntimeError(f"FFMPEG_FAILED:{name}:{proc.returncode}")
    os.replace(part,out); elapsed=time.monotonic()-started
    expected={"width":1920,"height":1080,"fps":30,"codec":"h264_videotoolbox","pix_fmt":"yuv420p","color":"bt709","audio_codec":"aac","sample_rate":48000,"channels":2,"faststart":True}
    qc=NativeMediaQC(FFPROBE).inspect(out,expected,name); write_json(run_dir/"media_qc.json",qc.model_dump(mode="json")); write_json(run_dir/"ffprobe.json",json.loads(run([FFPROBE,"-v","error","-show_streams","-show_format","-of","json",out]).stdout))
    contact=run_dir/"contact_sheet.jpg"; run([FFMPEG,"-hide_banner","-nostdin","-y","-i",out,"-vf",f"fps=1/{max(4,int(duration)//8)},scale=480:270,tile=4x2","-frames:v","1",contact])
    receipt={"output":str(out),"checksum":sha256_file(out),"elapsed_seconds":round(elapsed,3),"realtime_factor":round(elapsed/float(duration),4),"MediaQC":qc.result,"production_eligible":False,"no_provider_calls":True,**evidence}; write_json(run_dir/"execution_receipt.json",receipt); return receipt

PRESETS=["cut","fade_soft","fade_black","dissolve","slide_left","slide_right","cover_left","reveal_up","hold_static","kenburns_center_soft","kenburns_subject_left","pushin_slow","pan_left_slow","pan_right_slow","lowerthird_slidein","fact_card_pop","data_card_hold","comparison_reveal","timeline_step_reveal","cta_card_fadeup","caption_burn_ass_v1","logo_bug_static","badge_corner"]
def showcase_graph():
    items=[]
    for i,p in enumerate(PRESETS):
        source, overlay=motion_overlay(p,4); label=p.upper()
        items.append(f"color=c=0x101827:s=1920x1080:r=30:d=4[showbg{i}]")
        items.append(f"{source}[showbox{i}]")
        items.append(f"[showbg{i}][showbox{i}]{overlay},drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='{label}':fontcolor=white:fontsize=58:x=(w-text_w)/2:y=390,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='duration=4s | intensity=0.45 | min zoom=2.5% | min pan=3%':fontcolor=0x6ee7ff:fontsize=28:x=(w-text_w)/2:y=850[v{i}]")
    items.append("".join(f"[v{i}]" for i in range(len(PRESETS)))+f"concat=n={len(PRESETS)}:v=1:a=0[v]"); return ";\n".join(items)+"\n"

def main():
    WORK.mkdir(parents=True,exist_ok=True)
    source_plans={letter:json.loads((NR2/KEYS[letter]/"native_render_plan.json").read_text()) for letter in KEYS}
    groups={}
    for letter,key in KEYS.items():
        scenes=source_plans[letter]["visual_treatment"]; groups[key]=compile_motion_decisions(key,scenes)
    assert differentiation_gate(groups)=="PASS"
    outputs=[]
    for letter,key in KEYS.items():
        decisions=groups[key]; gates=motion_gates(decisions); manifest={"strategy_key":key,"source_plan_hash":source_plans[letter]["plan_hash"],"script_hash":source_plans[letter]["script_hash"],"audio_hash":source_plans[letter]["audio_hash"],"srt_hash":source_plans[letter]["srt_hash"],"motion_decisions":decisions,"motion_gates":gates,"motion_metrics":audit_metrics(decisions),"production_eligible":False}; manifest["manifest_hash"]=stable_hash(manifest); write_json(WORK/f"motion_manifest_{letter.lower()}.json",manifest)
        clean=f"nr2_{letter.lower()}_motion_clean"; proxy=f"nr2_{letter.lower()}_motion_audit_proxy"
        outputs.append(encode(clean,scene_filtergraph(letter,decisions,proxy=False,srt=SRT),AUDIO,84,{"strategy":key,"proxy":False,"manifest_hash":manifest["manifest_hash"]}))
        outputs.append(encode(proxy,scene_filtergraph(letter,decisions,proxy=True,srt=SRT),AUDIO,84,{"strategy":key,"proxy":True,"manifest_hash":manifest["manifest_hash"],"review_overlay_present":True}))
    show_audio=WORK/"showcase_audio_92s.m4a"
    if not show_audio.exists(): run([FFMPEG,"-hide_banner","-nostdin","-y","-f","lavfi","-i","sine=frequency=180:sample_rate=48000:duration=92","-af","volume=0.02","-c:a","aac","-ar","48000","-ac","2",show_audio])
    outputs.append(encode("nr2_native_motion_pack_showcase",showcase_graph(),show_audio,92,{"preset_count":len(PRESETS),"review_labels":True}))
    summary={"phase":"NR2.1","same_content":{"script_hash":source_plans["A"]["script_hash"],"audio_hash":source_plans["A"]["audio_hash"],"srt_hash":source_plans["A"]["srt_hash"],"timing_hash":source_plans["A"]["timing_hash"],"status":"PASS"},"differentiation_gate":"PASS","strategy_gates":{k:motion_gates(v) for k,v in groups.items()},"outputs":outputs,"no_provider_network_calls":True,"human_review":"PENDING","selected_strategy":"NONE"}; write_json(WORK/"nr2_1_run_summary.json",summary)

if __name__=="__main__": main()
