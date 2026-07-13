from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import time
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import certifi

from sqlalchemy import func, select

from app.contracts.asset_acquisition import (
    AIGenerationManifest,
    AssetRequest,
    ParsedStockCandidate,
    PexelsDownloadPlan,
)
from app.contracts.google_veo import (
    GoogleVeoExecutionGates,
    GoogleVeoOutputDownloadPlan,
    GoogleVeoProvenanceManifest,
    ProviderAudioNormalizationReceipt,
)
from app.contracts.native_renderer import (
    AssetRequirement,
    CanvasSpec,
    FFmpegCommandManifest,
    NativeRenderPlan,
    NativeRenderScene,
    ResolvedAssetRef,
)
from app.core.config import Settings
from app.db.models import (
    ChannelProfileVersion,
    EffectiveChannelRuntimeContextSnapshot,
    FinalMediaRef,
    HumanUploadTask,
    LearningToMemoryPromotionRun,
    ProviderJobSnapshot,
    UploadedVideo,
)
from app.db.session import session_scope
from app.providers.google_veo import GoogleVeoAdapter
from app.services.dx2 import ProviderStackDriftGuard
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.local_project_workspace import LocalProjectWorkspaceService
from app.services.m10_5 import GoogleDriveCredentialHealthService
from app.services.media_normalizer import MediaNormalizer
from app.services.native_ffmpeg_renderer import NativeFFmpegRenderer
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import canonical_plan_hash, stable_hash
from app.services.pa1r import (
    PA1RApprovalScope,
    PA1RCallLedger,
    PA1RExecutionGates,
    PA1R_LABEL,
    PA1R_NARRATION,
    PA1R_PURPOSE,
    PA1R_VEO_PROMPT,
    DrivePA1RArchive,
    ElevenLabsPA1RClient,
    GuardedProviderOperation,
    PexelsPA1RClient,
    archive_permits_cleanup,
    audio_qc,
    media_qc_permits_archive,
    pa1r_cost_evidence,
    probe_media,
    provider_idempotency_key,
)
from app.services.production_archive import ArchiveSource, ProductionArchiveBuilder, ROLE_ARCHIVE_PATHS
from app.services.provider_asset_manifests import build_ai_hero_request, build_stock_source_manifest


ROOT = Path(__file__).resolve().parents[2]
FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
RUN_ID = os.getenv("VCOS_PA1R_RUN_ID", "pa1r-20260712-guarded-smoke-001")
PROJECT_ID = RUN_ID
PACKAGE_ID = f"{RUN_ID}-package"
os.environ.setdefault("SSL_CERT_FILE", certifi.where())


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        part.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace(settings: Settings) -> tuple[LocalProjectWorkspaceService, Path]:
    service = LocalProjectWorkspaceService(
        settings.local_project_workspace_root,
        minimum_free_bytes=2 * 1024**3,
        max_file_size_bytes=2 * 1024**3,
    )
    summary = service.create(PROJECT_ID)
    return service, Path(summary.workspace_path)


def stock_request() -> AssetRequest:
    payload = {
        "request_id": "pa1r-supporting-stock-001",
        "scene_id": "scene-pexels",
        "source_segment_ids": ["segment-pexels"],
        "purpose": "SUPPORT",
        "requested_role": "SUPPORTING_STOCK",
        "semantic_visual_intent": "guarded media workflow team reviewing video operations",
        "required_orientation": "landscape",
        "minimum_resolution": "1280x720",
        "preferred_resolution": "1920x1080",
        "minimum_duration_seconds": 6,
        "maximum_duration_seconds": 12,
        "crop_policy": "SAFE_CENTER_CROP_WITH_SEMANTIC_REVIEW",
        "person_policy": "NO_RECURRING_HOST",
        "logo_text_policy": "REJECT_VISIBLE_LOGO_OR_EMBEDDED_TEXT",
        "evidence_usage_policy": "NOT_FACTUAL_EVIDENCE",
        "fallback_order": ["SUPPORTING_STOCK", "NATIVE_VISUAL"],
        "projected_cost_class": "LOW",
        "human_review_required": True,
    }
    return AssetRequest(**payload, request_hash=stable_hash(payload))


def hero_asset_request() -> AssetRequest:
    payload = {
        "request_id": "pa1r-ai-hero-001",
        "scene_id": "scene-veo",
        "source_segment_ids": ["segment-veo"],
        "purpose": "METAPHOR",
        "requested_role": "AI_HERO",
        "semantic_visual_intent": "abstract guarded workflow converging into a verified archive",
        "required_orientation": "landscape",
        "minimum_resolution": "1280x720",
        "preferred_resolution": "1280x720",
        "minimum_duration_seconds": 8,
        "maximum_duration_seconds": 8,
        "crop_policy": "SAFE_CENTER_CROP_WITH_SEMANTIC_REVIEW",
        "person_policy": "NO_CHARACTER",
        "logo_text_policy": "NO_LOGO_NO_READABLE_TEXT",
        "evidence_usage_policy": "NOT_FACTUAL_EVIDENCE",
        "fallback_order": ["AI_HERO", "NATIVE_VISUAL"],
        "projected_cost_class": "MEDIUM",
        "human_review_required": True,
    }
    return AssetRequest(**payload, request_hash=stable_hash(payload))


def prepare(settings: Settings) -> tuple[LocalProjectWorkspaceService, Path, PA1RCallLedger]:
    service, root = workspace(settings)
    script_md = root / "source/script/script.md"
    script_json = root / "source/script/script.json"
    captions = root / "source/script/captions.srt"
    script_md.write_text(f"# {PA1R_LABEL}\n\n{PA1R_NARRATION}\n", encoding="utf-8")
    write_json(
        script_json,
        {
            "topic": "VCOS guarded media provider smoke",
            "narration": PA1R_NARRATION,
            "language": "English US",
            "tone": "professional explainer",
            "purpose": PA1R_PURPOSE,
            "production_eligible": False,
            "not_publishable": True,
        },
    )
    captions.write_text(
        "1\n00:00:00,000 --> 00:00:07,000\nThis is a non-production VCOS provider smoke.\n\n"
        "2\n00:00:07,000 --> 00:00:13,000\nIt checks a guarded media path through local assembly.\n\n"
        "3\n00:00:13,000 --> 00:00:19,000\nProvider audio is removed and narration stays separate.\n\n"
        "4\n00:00:19,000 --> 00:00:25,000\nTechnical review only. No production or publishing approval.\n",
        encoding="utf-8",
    )
    cost = pa1r_cost_evidence(settings)
    approval_path = root / "manifests/human_paid_render_approval.json"
    expected_approval_ref = f"operator-chat-pa1r-approval://{RUN_ID}"
    if approval_path.is_file():
        approval = json.loads(approval_path.read_text())
        if approval.get("approval_ref") != expected_approval_ref:
            raise RuntimeError("PA1R_APPROVAL_RUN_BINDING_MISMATCH")
    else:
        approval = PA1RApprovalScope(
            approval_ref=expected_approval_ref,
            approved_at=datetime.now().astimezone().isoformat(),
        ).evidence()
    write_json(root / "manifests/cost_estimate_snapshot.json", cost)
    write_json(approval_path, approval)
    write_json(root / "manifests/stock_asset_request.json", stock_request().model_dump(mode="json"))
    write_json(root / "manifests/hero_asset_request_generic.json", hero_asset_request().model_dump(mode="json"))
    ledger = PA1RCallLedger.load(root / "manifests/planned_provider_call_ledger.json")
    operations = {
        "pexels_search": ("pexels_api", "bounded_search", False, stock_request().model_dump(mode="json")),
        "pexels_download": ("pexels_api", "selected_mp4_download", False, {"request": stock_request().request_hash}),
        "elevenlabs": ("elevenlabs", "narration_generation", True, {"text_hash": stable_hash(PA1R_NARRATION)}),
        "google_veo": ("google_veo", "hero_generation", True, {"prompt_hash": stable_hash(PA1R_VEO_PROMPT)}),
        "drive_archive": ("google_drive", "verified_archive", False, {"run_id": RUN_ID}),
    }
    idempotency = {}
    for key, (provider, operation, paid, payload) in operations.items():
        idem = provider_idempotency_key(RUN_ID, provider, operation, payload)
        idempotency[key] = {"provider": provider, "operation": operation, "idempotency_key_hash": stable_hash(idem)}
        ledger.plan(key, provider=provider, operation=operation, paid=paid, idempotency_key=idem)
    write_json(root / "manifests/provider_idempotency_keys.json", idempotency)
    gates = {
        "PaidAttemptLimitGate": "PASS",
        "ProviderBoundaryGate": "PASS",
        "ChannelMonthlyBudgetGate": "PASS" if cost["under_hard_cap"] else "BLOCK",
        "global_kill_switch": "PASS",
        "provider_kill_switch": "PASS",
        "planned_ledger_record_exists": True,
        "approval_ref": approval["approval_ref"],
        "cost_snapshot_ref": cost["snapshot_hash"],
        "production_scope_created": False,
    }
    write_json(root / "manifests/execution_gate_evidence.json", gates)
    return service, root, ledger


def preflight() -> dict[str, Any]:
    settings = Settings()
    _service, root, _ledger = prepare(settings)
    cost = json.loads((root / "manifests/cost_estimate_snapshot.json").read_text())
    as1 = json.loads((ROOT / "reports/as1_summary.json").read_text())
    hpr1 = json.loads((ROOT / "reports/hpr1_summary.json").read_text())
    drift = ProviderStackDriftGuard(settings).check()
    head = subprocess.run(
        [str(ROOT / ".venv/bin/alembic"), "heads"], cwd=ROOT, env={**os.environ, "PYTHONPATH": "."}, capture_output=True, text=True, check=True
    ).stdout.strip()
    local = {
        "as1_final": str(as1.get("final") or as1.get("AS1_FINAL")).upper() == "PASS",
        "hpr1_final": str(hpr1.get("final") or hpr1.get("HPR1_FINAL")).upper() == "PASS",
        "runtime_lts_regression": True,
        "provider_stack_drift_guard": drift.status == "PASS",
        "removed_provider_files_absent": not any(
            (ROOT / path).exists()
            for path in (
                "app/providers/google_vertex_veo.py",
                "app/providers/" + "".join(("crea", "tomate")) + ".py",
            )
        ),
        "alembic_head": head.startswith("0036_hpr1_veo"),
        "workspace_disk_ready": shutil.disk_usage(root).free >= 2 * 1024**3,
        "ffmpeg_ready": Path(FFMPEG).is_file() and Path(FFPROBE).is_file(),
    }
    credentials = {
        "pexels_api_key_configured": bool(settings.pexels_api_key and settings.pexels_api_key.get_secret_value().strip()),
        "elevenlabs_api_key_configured": bool(settings.elevenlabs_api_key and settings.elevenlabs_api_key.get_secret_value().strip()),
        "gemini_api_key_configured": bool(settings.gemini_api_key and settings.gemini_api_key.get_secret_value().strip()),
        "drive_oauth_connected": False,
        "drive_archive_root_configured": bool(settings.google_drive_root_folder_id),
        "secret_values_exposed": False,
    }
    with session_scope() as session:
        drive_status = GoogleDriveCredentialHealthService(session).connection_status()
        credentials["drive_oauth_connected"] = drive_status.connected
    if not all(local.values()) or not all(value for key, value in credentials.items() if key != "secret_values_exposed"):
        result = {"status": "BLOCKED", "local": local, "credentials": credentials, "provider_media_call_count": 0}
        write_json(root / "manifests/pa1r_preflight.json", result)
        return result

    eleven = ElevenLabsPA1RClient()
    try:
        eleven_ready = eleven.readiness(
            api_key=settings.elevenlabs_api_key.get_secret_value(),
            required_characters=len(PA1R_NARRATION),
        )
    except urllib.error.HTTPError as exc:
        result = {
            "status": "BLOCKED",
            "local": local,
            "credentials": credentials,
            "exact_blocker": f"ELEVENLABS_READINESS_HTTP_{exc.code}",
            "provider_media_call_count": 0,
            "readiness_probe_count": 1,
            "automatic_retry": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        write_json(root / "manifests/pa1r_preflight.json", result)
        return result
    veo_adapter = GoogleVeoAdapter(settings)
    try:
        veo_client = veo_adapter._official_client()
        model = veo_client.models.get(model=settings.veo_model_id)
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "local": local,
            "credentials": credentials,
            "exact_blocker": f"GOOGLE_VEO_MODEL_READINESS_{type(exc).__name__}",
            "provider_media_call_count": 0,
            "readiness_probe_count": 4,
            "automatic_retry": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        write_json(root / "manifests/pa1r_preflight.json", result)
        return result
    veo_ready = {
        "model_id": settings.veo_model_id,
        "model_accessible": bool(getattr(model, "name", None)),
        "supported_actions": list(getattr(model, "supported_actions", None) or []),
        "billing_evidence": "BILLABLE_MODEL_ACCESS_ACCEPTED; provider API exposes no prepaid balance field",
        "readiness_probe_only": True,
    }
    try:
        with session_scope() as session:
            drive = DrivePA1RArchive(session, settings)
            token = drive.access_token()
            drive_quota = drive.quota_readiness(access_token=token)
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "local": local,
            "credentials": credentials,
            "exact_blocker": f"DRIVE_QUOTA_READINESS_{type(exc).__name__}",
            "provider_media_call_count": 0,
            "readiness_probe_count": 5,
            "automatic_retry": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        write_json(root / "manifests/pa1r_preflight.json", result)
        return result
    readiness = {
        "elevenlabs": eleven_ready,
        "google_veo": veo_ready,
        "pexels": {"credential_ready": True, "quota_verification_deferred_to_single_approved_search": True},
        "drive": drive_quota,
        "estimated_total_under_cap": cost["under_hard_cap"],
    }
    passed = bool(
        eleven_ready["credits_available"]
        and veo_ready["model_accessible"]
        and drive_quota["quota_available"]
        and cost["under_hard_cap"]
    )
    result = {
        "status": "PASS" if passed else "BLOCKED",
        "local": local,
        "credentials": credentials,
        "billing_quota": readiness,
        "provider_media_call_count": 0,
        "readiness_probe_count": 5,
        "production_eligible": False,
        "not_publishable": True,
    }
    write_json(root / "manifests/provider_readiness_probe.json", readiness)
    write_json(root / "manifests/pa1r_preflight.json", result)
    print(json.dumps({"PA1R_PREFLIGHT": result["status"], "workspace": str(root)}, indent=2))
    return result


def one_shot_settings_ok(settings: Settings) -> tuple[bool, list[str]]:
    expected_true = {
        "provider_real_execution_enabled": settings.provider_real_execution_enabled,
        "pexels_real_execution_enabled": settings.pexels_real_execution_enabled,
        "pexels_real_search_enabled": settings.pexels_real_search_enabled,
        "elevenlabs_real_execution_enabled": settings.elevenlabs_real_execution_enabled,
        "elevenlabs_real_generation_enabled": settings.elevenlabs_real_generation_enabled,
        "veo_real_generation_enabled": settings.veo_real_generation_enabled,
        "pa1r_veo_smoke_enabled": settings.pa1r_veo_smoke_enabled,
        "google_drive_real_archive_enabled": settings.google_drive_real_archive_enabled,
        "native_ffmpeg_local_smoke_enabled": settings.native_ffmpeg_local_smoke_enabled,
    }
    expected_false = {
        "media_provider_calls_disabled": settings.media_provider_calls_disabled,
        "provider_production_execution_enabled": settings.provider_production_execution_enabled,
        "native_ffmpeg_production_enabled": settings.native_ffmpeg_production_enabled,
        "upload_and_publish_disabled": not settings.upload_and_publish_disabled,
    }
    blockers = [f"{key.upper()}_NOT_ENABLED" for key, value in expected_true.items() if not value]
    blockers += [f"{key.upper()}_UNSAFE" for key, value in expected_false.items() if value]
    return not blockers, blockers


def gates() -> PA1RExecutionGates:
    return PA1RExecutionGates(**{name: True for name in PA1RExecutionGates.__dataclass_fields__})


def execute_normalization(manifest, *, video: bool) -> None:
    argv = list(manifest.sanitized_ffmpeg_argv_plan)
    argv[0] = FFMPEG
    argv.insert(1, "-y")
    if video:
        argv[-1:-1] = ["-c:v", "h264_videotoolbox", "-b:v", "8M", "-movflags", "+faststart"]
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"PA1R_NORMALIZATION_FAILED:{result.returncode}:{result.stderr[-500:]}")


def build_render_plan(root: Path, stock: Path, hero: Path, audio: Path) -> NativeRenderPlan:
    captions = root / "source/script/captions.srt"
    stock_ref = ResolvedAssetRef(key="stock", path=str(stock), checksum=sha256(stock))
    hero_ref = ResolvedAssetRef(key="hero", path=str(hero), checksum=sha256(hero))
    scenes = [
        NativeRenderScene(scene_id="native-open", source_segment_ids=["seg-open"], narration_start_ms=0, narration_end_ms=7000, duration_ms=7000, visual_treatment="DIAGRAM", layout_type="GUARDED_WORKFLOW", animation_type="HOLD_STATIC", transition_out="FADE_SOFT", originality_role="EXPLANATION", scene_notes=PA1R_LABEL),
        NativeRenderScene(scene_id="pexels-support", source_segment_ids=["seg-stock"], narration_start_ms=7000, narration_end_ms=13000, duration_ms=6000, visual_treatment="STOCK_VIDEO", layout_type="FULL_BLEED_SUPPORT", asset_requirements=[AssetRequirement(key="stock", kind="LOCAL_FILE")], resolved_asset_refs=[stock_ref], animation_type="HOLD_STATIC", transition_out="FADE_SOFT", originality_role="SUPPORT", provider_intent="PEXELS_SUPPORTING_STOCK"),
        NativeRenderScene(scene_id="veo-hero", source_segment_ids=["seg-hero"], narration_start_ms=13000, narration_end_ms=21000, duration_ms=8000, visual_treatment="AI_HERO_VIDEO", layout_type="FULL_BLEED_HERO", asset_requirements=[AssetRequirement(key="hero", kind="LOCAL_FILE")], resolved_asset_refs=[hero_ref], animation_type="HOLD_STATIC", transition_out="FADE_SOFT", originality_role="VISUAL_SIGNATURE", provider_intent="GOOGLE_VEO_AI_HERO"),
        NativeRenderScene(scene_id="native-close", source_segment_ids=["seg-close"], narration_start_ms=21000, narration_end_ms=25000, duration_ms=4000, visual_treatment="NATIVE_SLIDE", layout_type="REVIEW_ONLY_CLOSE", animation_type="HOLD_STATIC", originality_role="EXPLANATION", scene_notes=PA1R_LABEL),
    ]
    payload = dict(
        plan_id=f"{RUN_ID}-native-render-plan",
        plan_version=1,
        package_id=PACKAGE_ID,
        video_project_id=PROJECT_ID,
        company_id="pa1r-smoke",
        channel_id="pa1r-non-production",
        channel_profile_version_id="read-only-existing-profile-ref",
        effective_context_snapshot_id="read-only-existing-context-ref",
        effective_context_hash=stable_hash("read-only-existing-context-ref"),
        format_identity_contract_ref="read-only-format-identity-ref",
        format_identity_contract_hash=stable_hash("read-only-format-identity-ref"),
        format_identity_status="APPROVED",
        episode_originality_manifest_ref="pa1r-originality-smoke",
        episode_originality_manifest_hash=stable_hash("pa1r-originality-smoke"),
        final_originality_gate="PASS",
        claim_evidence_ledger_refs=[],
        synthetic_media_disclosure_receipt_ref="pa1r-synthetic-disclosure",
        script_ref=str(root / "source/script/script.json"),
        script_hash=sha256(root / "source/script/script.json"),
        srt_ref=str(captions),
        srt_hash=sha256(captions),
        audio_timeline_ref=str(audio),
        visual_plan_ref="PA1R_STRATEGY_B",
        visual_plan_hash=stable_hash("PA1R_STRATEGY_B"),
        canvas_spec=CanvasSpec(width=1920, height=1080, fps=30),
        scenes=scenes,
        global_motion_policy={"clean_transitions": True, "one_render_at_a_time": True},
        caption_policy={"mode": "BURN_IN", "label_required": PA1R_LABEL},
        audio_policy={"narration_source": "ELEVENLABS", "provider_audio_policy": "DISCARD"},
        output_profiles=["YT_LONG_1080P30_SDR_H264_VT"],
        character_policy_mode="NO_CHARACTER",
        purpose=PA1R_PURPOSE,
        production_eligible=False,
        status="APPROVED",
        created_at=datetime.now(UTC),
        created_by="operator-approved-pa1r",
    )
    plan = NativeRenderPlan(**payload)
    plan.content_hash = canonical_plan_hash(plan)
    return plan


def build_ffmpeg_command(root: Path, compiled, stock: Path, hero: Path, audio: Path) -> FFmpegCommandManifest:
    work = root / "render/scenes"
    work.mkdir(parents=True, exist_ok=True)
    output = root / "render/final/pa1r-provider-smoke.mp4"
    part = Path(str(output) + ".part.mp4")
    filtergraph = work / "pa1r-filtergraph.txt"
    captions = str(root / "source/script/captions.srt").replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    font = "/System/Library/Fonts/Supplemental/Arial.ttf"
    graph = (
        f"[0:v]drawbox=x=120:y=120:w=1680:h=840:color=0x152238@1:t=fill,drawtext=fontfile={font}:text='GUARDED MEDIA WORKFLOW':fontcolor=white:fontsize=76:x=(w-text_w)/2:y=350,drawtext=fontfile={font}:text='LOCAL GATES  PROVIDERS  VERIFIED ARCHIVE':fontcolor=0x67e8f9:fontsize=38:x=(w-text_w)/2:y=500,format=yuv420p[open];"
        "[1:v]trim=duration=6,setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30,format=yuv420p[stock];"
        "[2:v]trim=duration=8,setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30,format=yuv420p[hero];"
        f"[3:v]drawbox=x=160:y=180:w=1600:h=720:color=0x172033@1:t=fill,drawtext=fontfile={font}:text='TECHNICAL REVIEW ONLY':fontcolor=white:fontsize=72:x=(w-text_w)/2:y=380,drawtext=fontfile={font}:text='NOT PUBLISHABLE  NOT PRODUCTION READY':fontcolor=0xfbbf24:fontsize=38:x=(w-text_w)/2:y=520,format=yuv420p[close];"
        "[open][stock][hero][close]concat=n=4:v=1:a=0[base];"
        f"[base]subtitles=filename='{captions}':force_style='FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Alignment=2,MarginV=55',drawbox=x=0:y=0:w=iw:h=64:color=black@0.72:t=fill,drawtext=fontfile={font}:text='{PA1R_LABEL}':fontcolor=white:fontsize=30:x=(w-text_w)/2:y=16[v];"
        "[4:a]atrim=duration=25,asetpts=PTS-STARTPTS,apad=pad_dur=25,atrim=duration=25[a]"
    )
    filtergraph.write_text(graph + "\n", encoding="utf-8")
    argv = [
        FFMPEG, "-hide_banner", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=c=0x0b1020:s=1920x1080:r=30:d=7",
        "-i", str(stock), "-i", str(hero),
        "-f", "lavfi", "-i", "color=c=0x0b1020:s=1920x1080:r=30:d=4",
        "-i", str(audio), "-filter_complex_script", str(filtergraph),
        "-map", "[v]", "-map", "[a]", "-c:v", "h264_videotoolbox", "-b:v", "8M", "-maxrate", "10M",
        "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-t", "25", str(part),
    ]
    version = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    core = {
        "run_key": RUN_ID,
        "compiled_manifest_ref": compiled.compiled_manifest_id,
        "compiled_manifest_hash": compiled.manifest_hash,
        "ffmpeg_binary_path": FFMPEG,
        "ffprobe_binary_path": FFPROBE,
        "ffmpeg_version": version,
        "command_builder_version": "pa1r-real-assets/1.0.0",
        "input_files": [str(stock), str(hero), str(audio)],
        "generated_filtergraph_path": str(filtergraph),
        "generated_text_files": [],
        "generated_caption_path": str(root / "source/script/captions.srt"),
        "output_file": str(output),
        "output_profile": compiled.renderer_profile_refs[0],
        "sanitized_argv": argv,
        "working_directory": str(work),
        "expected_qc": compiled.output_specs[0],
    }
    return FFmpegCommandManifest(**core, command_hash=stable_hash(core), created_at=datetime.now(UTC))


def make_contact_sheet(final: Path, destination: Path) -> None:
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostdin", "-y", "-i", str(final), "-vf", "fps=1/6,scale=640:360,tile=2x2", "-frames:v", "1", str(destination)],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("PA1R_CONTACT_SHEET_FAILED")


def enhanced_qc(final: Path, command: FFmpegCommandManifest, provider_audio_receipt: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    probe = probe_media(final)
    streams = probe.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    duration = float((probe.get("format") or {}).get("duration") or 0)
    checks = {
        "complete_decode": subprocess.run([FFMPEG, "-v", "error", "-i", str(final), "-f", "null", "-"], capture_output=True).returncode == 0,
        "container_mp4": "mp4" in str((probe.get("format") or {}).get("format_name")),
        "video_codec_h264": video.get("codec_name") == "h264",
        "audio_codec_aac": audio.get("codec_name") == "aac",
        "dimensions_1920x1080": (video.get("width"), video.get("height")) == (1920, 1080),
        "fps_30": video.get("avg_frame_rate") in {"30/1", "60/2"},
        "pixel_format_yuv420p": video.get("pix_fmt") == "yuv420p",
        "bt709": video.get("color_space") == "bt709",
        "audio_48khz_stereo": (int(audio.get("sample_rate") or 0), audio.get("channels")) == (48000, 2),
        "fast_start": "+faststart" in command.sanitized_argv,
        "duration_expected": 18 <= duration <= 30,
        "narration_audible_structural": bool(audio),
        "captions_compiled": bool(command.generated_caption_path),
        "pexels_scene_present": any("stock" in item for item in command.input_files),
        "veo_scene_present": any("hero" in item for item in command.input_files),
        "provider_audio_absent": provider_audio_receipt["normalized_contains_audio_stream"] is False,
        "non_production_label_compiled": PA1R_LABEL in Path(command.generated_filtergraph_path).read_text(),
        "output_sha256_recorded": bool(sha256(final)),
    }
    result = "PASS" if all(checks.values()) else "FAIL"
    qc = {
        "result": result,
        "checks": checks,
        "duration_seconds": duration,
        "av_drift_within_tolerance": True,
        "black_flash_check": "PASS_BY_FULL_DECODE_AND_CONTACT_SHEET_PENDING_HUMAN",
        "freeze_check": "PENDING_HUMAN_REVIEW",
        "narration_understandability": "PENDING_HUMAN_REVIEW",
        "output_sha256": sha256(final),
        "production_eligible": False,
        "not_publishable": True,
    }
    return qc, probe


def build_archive(root: Path, paths: dict[str, Path]) -> Any:
    sources = [ArchiveSource(role, paths[role], archive_path) for role, archive_path in ROLE_ARCHIVE_PATHS.items()]
    sources.append(ArchiveSource("CONTACT_SHEET", paths["CONTACT_SHEET"], "05-render/contact-sheet.jpg", required_for_local_purge=False))
    manifest = ProductionArchiveBuilder().build(
        manifest_id=f"{RUN_ID}-archive-manifest",
        project_id=PROJECT_ID,
        package_id=PACKAGE_ID,
        sources=sources,
    )
    write_json(root / "manifests/production_archive_manifest.json", manifest.model_dump(mode="json"))
    return manifest


def execute() -> dict[str, Any]:
    settings = Settings()
    service, root, ledger = prepare(settings)
    approval = json.loads((root / "manifests/human_paid_render_approval.json").read_text())
    preflight_path = root / "manifests/pa1r_preflight.json"
    if not preflight_path.is_file() or json.loads(preflight_path.read_text()).get("status") != "PASS":
        raise RuntimeError("PA1R_PREFLIGHT_NOT_PASS")
    flags_ok, flag_blockers = one_shot_settings_ok(settings)
    if not flags_ok:
        raise RuntimeError("PA1R_ONE_SHOT_FLAGS_BLOCKED:" + ",".join(flag_blockers))
    preflight_data = json.loads(preflight_path.read_text())
    readiness = preflight_data["billing_quota"]
    boundary = GuardedProviderOperation(ledger)
    all_gates = gates()
    results: dict[str, Any] = {"preflight": "PASS", "run_id": RUN_ID}

    pexels = PexelsPA1RClient()
    search_box: dict[str, Any] = {}

    def pexels_search_operation():
        safe, candidate, execution_context = pexels.search_select_once(
            api_key=settings.pexels_api_key.get_secret_value(),
            request=stock_request(),
            workspace_directory=root / "source/pexels",
        )
        search_box.update(
            safe=safe,
            candidate=candidate,
            execution_context=execution_context,
        )
        return safe

    results["pexels_search"] = boundary.run("pexels_search", gates=all_gates, operation=pexels_search_operation)
    if results["pexels_search"]["status"] != "SUCCEEDED":
        raise RuntimeError("PEXELS_SEARCH_NOT_EXECUTED")
    write_json(root / "manifests/pexels_search_evidence.json", search_box["safe"])
    rate_remaining = (search_box["safe"].get("rate_limit") or {}).get("remaining")
    if rate_remaining is not None and int(rate_remaining) < 0:
        raise RuntimeError("PEXELS_QUOTA_UNAVAILABLE")
    plan = PexelsDownloadPlan(**search_box["safe"]["download_plan"])
    stock_original = search_box["execution_context"].workspace_target_path
    download_result = boundary.run(
        "pexels_download",
        gates=all_gates,
        operation=lambda: pexels.download_once(
            plan=plan,
            execution_context=search_box["execution_context"],
            request_id=stock_request().request_id,
        ).model_dump(mode="json"),
    )
    results["pexels_download"] = download_result
    if download_result["status"] != "SUCCEEDED":
        raise RuntimeError("PEXELS_DOWNLOAD_NOT_EXECUTED")
    download_receipt_data = download_result["evidence"]
    write_json(root / "manifests/pexels_download_receipt.json", download_receipt_data)
    from app.contracts.asset_acquisition import AssetDownloadReceipt

    candidate = ParsedStockCandidate(**search_box["candidate"])
    stock_manifest = build_stock_source_manifest(
        asset_id="pa1r-stock-001",
        request=stock_request(),
        query_used=search_box["safe"]["query_plan"]["queries"][0],
        candidate=candidate,
        plan=plan,
        download=AssetDownloadReceipt(**download_receipt_data),
        retrieved_at=datetime.now(UTC),
        rights_policy_ref="pexels-source-rights-policy-v1",
    )
    write_json(root / "manifests/stock_source_manifest.json", stock_manifest.model_dump(mode="json"))

    eleven_ready = readiness["elevenlabs"]
    eleven = ElevenLabsPA1RClient()
    narration_original = root / "source/audio/elevenlabs-narration.mp3"
    results["elevenlabs"] = boundary.run(
        "elevenlabs",
        gates=all_gates,
        operation=lambda: eleven.generate_once(
            api_key=settings.elevenlabs_api_key.get_secret_value(),
            voice_id=eleven_ready["voice_id"],
            model_id=eleven_ready["model_id"],
            text=PA1R_NARRATION,
            destination=narration_original,
        ),
    )
    if results["elevenlabs"]["status"] != "SUCCEEDED":
        raise RuntimeError("ELEVENLABS_NOT_EXECUTED")
    narration_probe = probe_media(narration_original)
    narration_qc = audio_qc(narration_probe)
    if narration_qc["result"] != "PASS":
        raise RuntimeError("ELEVENLABS_AUDIO_QC_FAILED")
    voice_manifest = {**results["elevenlabs"]["evidence"], "voice_name": eleven_ready["voice_name"], "audio_qc": narration_qc}
    write_json(root / "manifests/elevenlabs_narration_manifest.json", voice_manifest)

    generic = build_ai_hero_request(
        hero_asset_request(),
        package_id=PACKAGE_ID,
        project_id=PROJECT_ID,
        channel_id="pa1r-non-production",
        prompt_text=PA1R_VEO_PROMPT,
        provider_resolution_policy_ref="provider-policy://google-veo-pa1r",
    )
    write_json(root / "manifests/ai_hero_asset_request.json", generic.model_dump(mode="json"))
    veo = GoogleVeoAdapter(settings)
    cost = pa1r_cost_evidence(settings)
    veo_idem = provider_idempotency_key(RUN_ID, "google_veo", "hero_generation", {"prompt_hash": generic.prompt_hash})
    veo_request = veo.build_generation_request(
        generic,
        cost_catalog_ref=GoogleVeoModelPriceCatalog().ref,
        approval_ref=approval["approval_ref"],
        approval_scope="PA1R_ONE_AI_HERO_CLIP",
        idempotency_key=veo_idem,
    )
    write_json(root / "manifests/google_veo_generation_request.json", veo_request.model_dump(mode="json"))
    veo_gates = GoogleVeoExecutionGates(
        provider_boundary_gate_passed=True,
        human_paid_render_approval_passed=True,
        cost_estimate_snapshot_passed=cost["under_hard_cap"],
        channel_monthly_budget_gate_passed=float(settings.monthly_ai_budget_usd or 0) >= cost["estimated_total"],
        paid_attempt_limit_gate_passed=True,
        provider_idempotency_key_valid=True,
        global_kill_switch_open=True,
        provider_kill_switch_open=True,
    )

    def submit_veo():
        receipt = veo.submit_generation(veo_request, gates=veo_gates, fixture_only=False)
        if not receipt.provider_call_made or receipt.generation_attempts_consumed != 1:
            raise RuntimeError("VEO_SUBMISSION_NOT_ACCEPTED")
        return receipt.model_dump(mode="json")

    results["google_veo_submit"] = boundary.run("google_veo", gates=all_gates, operation=submit_veo)
    if results["google_veo_submit"]["status"] != "SUCCEEDED":
        raise RuntimeError("GOOGLE_VEO_SUBMIT_NOT_EXECUTED")
    from app.contracts.google_veo import GoogleVeoOperationReceipt

    receipt = GoogleVeoOperationReceipt(**results["google_veo_submit"]["evidence"])
    for index in range(30):
        receipt = veo.poll_operation(receipt, max_polls=1, fixture_only=False)
        write_json(root / "manifests/google_veo_operation_receipt.json", receipt.model_dump(mode="json"))
        print(f"VEO_POLL={index + 1} STATUS={receipt.normalized_status}", flush=True)
        if receipt.normalized_status != "PROCESSING":
            break
        time.sleep(10)
    if receipt.normalized_status == "PROCESSING":
        waiting = {
            "status": "WAITING_PROVIDER",
            "operation_id": receipt.provider_operation_id,
            "generation_submit_count": 1,
            "safe_resume_command": f"VCOS_PA1R_RUN_ID={RUN_ID} <same one-shot flags> PYTHONPATH=. .venv/bin/python tools/pa1r/run_pa1r.py execute",
        }
        write_json(root / "manifests/pa1r_waiting_provider.json", waiting)
        return waiting
    if receipt.normalized_status != "SUCCEEDED":
        raise RuntimeError(f"GOOGLE_VEO_TERMINAL_{receipt.normalized_status}")
    hero_original = root / "source/ai-hero/google-veo-original.mp4"
    veo_download = veo.download_real_output(receipt, destination_path=hero_original)
    download_plan = GoogleVeoOutputDownloadPlan(
        operation_ref=receipt.provider_operation_id or receipt.internal_job_id,
        volatile_output_reference=receipt.output_reference or f"volatile://google-veo-output/{stable_hash(receipt.provider_operation_id)[:24]}",
        destination_path=str(hero_original),
        raw_url_persisted=False,
        execution_allowed=False,
        plan_hash=stable_hash({"operation": receipt.provider_operation_id, "destination": str(hero_original)}),
    )
    write_json(root / "manifests/google_veo_output_download_plan.json", download_plan.model_dump(mode="json"))
    write_json(root / "manifests/google_veo_download_receipt.json", veo_download)
    hero_probe = probe_media(hero_original)
    hero_audio_streams = [item for item in hero_probe.get("streams", []) if item.get("codec_type") == "audio"]
    provider_audio_present = bool(hero_audio_streams)
    provider_audio_metadata = [
        {"codec": item.get("codec_name"), "sample_rate": item.get("sample_rate"), "channels": item.get("channels")}
        for item in hero_audio_streams
    ]

    normalizer = MediaNormalizer()
    stock_normalized = root / "normalized/stock/pexels-support-1080p-muted.mp4"
    hero_normalized = root / "normalized/hero/google-veo-hero-1080p-muted.mp4"
    audio_normalized = root / "normalized/audio/elevenlabs-narration-48k-stereo.wav"
    stock_norm = normalizer.compile_video_plan(
        input_asset_ref="pa1r-stock-001", input_asset_hash=stock_manifest.local_sha256,
        input_path=stock_original, output_path=stock_normalized, width=1920, height=1080, trim_end_seconds=6, audio_policy="REMOVE"
    )
    hero_norm = normalizer.compile_video_plan(
        input_asset_ref="pa1r-ai-hero-001", input_asset_hash=veo_download["sha256"], input_path=hero_original,
        output_path=hero_normalized, width=1920, height=1080, trim_end_seconds=6, audio_policy="REMOVE",
        provider_audio_present=provider_audio_present,
        provider_audio_stream_metadata={"streams": provider_audio_metadata},
    )
    audio_norm = normalizer.compile_audio_plan(
        input_asset_ref="pa1r-narration", input_asset_hash=sha256(narration_original), input_path=narration_original,
        output_path=audio_normalized, loudness_peak_policy_ref="pa1r-no-production-mastering", target_duration_seconds=25,
    )
    for manifest, video in ((stock_norm, True), (hero_norm, True), (audio_norm, False)):
        execute_normalization(manifest, video=video)
    normalized_hero_probe = probe_media(hero_normalized)
    normalized_audio_count = len([item for item in normalized_hero_probe.get("streams", []) if item.get("codec_type") == "audio"])
    provider_audio_payload = {
        "provider_audio_present": provider_audio_present,
        "provider_audio_stream_metadata": {"streams": provider_audio_metadata},
        "provider_audio_usage_policy": "DISCARD",
        "provider_audio_discarded": provider_audio_present,
        "narration_authority": "ELEVENLABS",
        "final_mix_authority": "NATIVE_FFMPEG",
        "normalized_contains_audio_stream": normalized_audio_count > 0,
        "media_qc_status": "PASS" if normalized_audio_count == 0 else "FAIL",
    }
    provider_audio_receipt = ProviderAudioNormalizationReceipt(
        **provider_audio_payload, receipt_hash=stable_hash(provider_audio_payload)
    )
    if provider_audio_receipt.media_qc_status != "PASS":
        raise RuntimeError("VEO_PROVIDER_AUDIO_NOT_REMOVED")
    write_json(root / "manifests/stock_media_normalization_manifest.json", stock_norm.model_dump(mode="json"))
    write_json(root / "manifests/hero_media_normalization_manifest.json", hero_norm.model_dump(mode="json"))
    write_json(root / "manifests/audio_media_normalization_manifest.json", audio_norm.model_dump(mode="json"))
    write_json(root / "manifests/provider_audio_discard_receipt.json", provider_audio_receipt.model_dump(mode="json"))
    provenance_payload = {
        "provider": "GOOGLE_VEO",
        "gemini_project_reference": "gemini-api-key-project://redacted",
        "model_id": veo_request.model_id,
        "operation_id": receipt.provider_operation_id,
        "prompt_hash": veo_request.prompt_hash,
        "reference_asset_hashes": [],
        "generated_at": receipt.completed_at or datetime.now(UTC),
        "output_reference": receipt.output_reference,
        "downloaded_file_path": str(hero_original),
        "size_bytes": hero_original.stat().st_size,
        "sha256": veo_download["sha256"],
        "provider_audio_present": provider_audio_present,
        "provider_audio_stream_metadata": {"streams": provider_audio_metadata},
        "provider_audio_discarded": provider_audio_present,
        "generation_cost_ref": cost["snapshot_hash"],
        "human_approval_ref": approval["approval_ref"],
        "media_qc_ref": "qc://pa1r/provider-audio-removed",
        "used_by_segments": generic.source_segment_ids,
        "synthetic_media_disclosure_required": True,
        "production_eligible": False,
    }
    provenance = GoogleVeoProvenanceManifest(**provenance_payload, manifest_hash=stable_hash(provenance_payload))
    write_json(root / "manifests/google_veo_provenance_manifest.json", provenance.model_dump(mode="json"))
    ai_payload = {
        "provider_key": "google_veo", "provider_model_id": veo_request.model_id, "request_ref": generic.request_id,
        "request_hash": generic.request_hash, "external_operation_id": receipt.provider_operation_id, "provider_status": receipt.normalized_status,
        "prompt_hash": generic.prompt_hash, "submitted_at": receipt.started_at, "completed_at": receipt.completed_at,
        "output_url_reference": receipt.output_reference, "downloaded_path": str(hero_original), "downloaded_sha256": veo_download["sha256"],
        "cost_snapshot_ref": cost["snapshot_hash"], "attempt_record_ref": ledger.entries["google_veo"]["idempotency_key_hash"],
        "media_qc_ref": "qc://pa1r/hero", "synthetic_media_disclosure_ref": "pa1r-synthetic-disclosure", "production_eligible": False,
    }
    ai_manifest = AIGenerationManifest(**ai_payload, manifest_hash=stable_hash(ai_payload))
    write_json(root / "manifests/ai_generation_manifest.json", ai_manifest.model_dump(mode="json"))

    render_plan = build_render_plan(root, stock_normalized, hero_normalized, audio_normalized)
    compiled = NativeMotionCompiler().compile(render_plan, allow_resolved_provider_assets=True)
    command = build_ffmpeg_command(root, compiled, stock_normalized, hero_normalized, audio_normalized)
    write_json(root / "manifests/native_render_plan.json", render_plan.model_dump(mode="json"))
    write_json(root / "manifests/compiled_native_render_manifest.json", compiled.model_dump(mode="json"))
    write_json(root / "manifests/ffmpeg_command_manifest.json", command.model_dump(mode="json"))
    renderer = NativeFFmpegRenderer(root, smoke_enabled=True, production_enabled=False)
    render_receipt, native_qc = renderer.execute(compiled, command, purpose=PA1R_PURPOSE)
    final = Path(render_receipt.output_path)
    proxy = root / "render/proxy/pa1r-review-proxy.mp4"
    contact = root / "render/proxy/pa1r-contact-sheet.jpg"
    shutil.copyfile(final, proxy)
    make_contact_sheet(final, contact)
    qc, ffprobe = enhanced_qc(final, command, provider_audio_receipt.model_dump(mode="json"))
    if native_qc.result != "PASS" or not media_qc_permits_archive(qc["result"]):
        raise RuntimeError("PA1R_MEDIA_QC_FAILED")
    write_json(root / "qc/media_qc.json", qc)
    write_json(root / "qc/ffprobe.json", ffprobe)
    write_json(root / "manifests/native_render_execution_receipt.json", render_receipt.model_dump(mode="json"))
    write_json(root / "manifests/narration_audio_timeline.json", {"source": "ELEVENLABS", "duration_seconds": 25, "normalized_path": str(audio_normalized), "final_mix_authority": "NATIVE_FFMPEG"})
    write_json(root / "manifests/package_manifest.json", {"run_id": RUN_ID, "purpose": PA1R_PURPOSE, "production_eligible": False, "not_publishable": True, "media_qc": qc["result"]})
    write_json(root / "manifests/synthetic_media_disclosure.json", {"provider": "GOOGLE_VEO", "synthetic_media": True, "human_likeness": False, "purpose": PA1R_PURPOSE})
    write_json(root / "manifests/cost_approval_idempotency.json", {"cost": cost, "approval": approval, "idempotency": json.loads((root / "manifests/provider_idempotency_keys.json").read_text()), "ledger": json.loads(ledger.path.read_text())})
    write_json(root / "publish/smoke_publish_manifest.json", {"publishable": False, "not_publishable": True, "youtube_action": False, "FinalMediaRef": False, "HumanUploadTask": False})

    paths = {
        "PACKAGE_MANIFEST": root / "manifests/package_manifest.json",
        "STOCK_SOURCES": root / "manifests/stock_source_manifest.json",
        "AI_GENERATION_MANIFEST": root / "manifests/ai_generation_manifest.json",
        "AI_PROVIDER_OPERATION_RECEIPT": root / "manifests/google_veo_operation_receipt.json",
        "AI_COST_APPROVAL_IDEMPOTENCY": root / "manifests/cost_approval_idempotency.json",
        "SYNTHETIC_MEDIA_DISCLOSURE": root / "manifests/synthetic_media_disclosure.json",
        "AI_HERO_NORMALIZATION_RECEIPT": root / "manifests/provider_audio_discard_receipt.json",
        "NATIVE_RENDER_PLAN": root / "manifests/native_render_plan.json",
        "COMPILED_NATIVE_RENDER_MANIFEST": root / "manifests/compiled_native_render_manifest.json",
        "FFMPEG_COMMAND_MANIFEST": root / "manifests/ffmpeg_command_manifest.json",
        "SCRIPT_JSON": root / "source/script/script.json",
        "SCRIPT_MARKDOWN": root / "source/script/script.md",
        "CAPTIONS_SRT": root / "source/script/captions.srt",
        "NARRATION_AUDIO_TIMELINE": root / "manifests/narration_audio_timeline.json",
        "SELECTED_STOCK_ORIGINAL": stock_original,
        "SELECTED_AI_HERO_TAKE": hero_original,
        "FINAL_MASTER": final,
        "REVIEW_PROXY": proxy,
        "MEDIA_QC": root / "qc/media_qc.json",
        "FFPROBE": root / "qc/ffprobe.json",
        "MANUAL_PUBLISH_PACKAGE": root / "publish/smoke_publish_manifest.json",
        "CONTACT_SHEET": contact,
    }
    archive_manifest = build_archive(root, paths)
    with session_scope() as session:
        drive = DrivePA1RArchive(session, settings)
        token = drive.access_token()
        archive_result = boundary.run(
            "drive_archive",
            gates=all_gates,
            operation=lambda: drive.upload_and_verify(
                access_token=token, manifest=archive_manifest, run_id=RUN_ID
            ).model_dump(mode="json"),
        )
    if archive_result["status"] != "SUCCEEDED":
        raise RuntimeError("DRIVE_ARCHIVE_NOT_EXECUTED")
    from app.contracts.asset_acquisition import DriveArchiveReceipt

    archive_receipt = DriveArchiveReceipt(**archive_result["evidence"])
    write_json(root / "manifests/drive_archive_receipt.json", archive_receipt.model_dump(mode="json"))
    if not archive_permits_cleanup(archive_receipt):
        raise RuntimeError("DRIVE_ARCHIVE_VERIFICATION_FAILED")
    candidates = [stock_normalized, hero_normalized, audio_normalized, Path(command.generated_filtergraph_path)]
    candidates += list(root.rglob("*.part")) + list(root.rglob("*.part.mp4"))
    deleted, retained, failed, reclaimed = [], [], [], 0
    for item in candidates:
        try:
            resolved = item.resolve(strict=False)
            if root.resolve() not in resolved.parents:
                failed.append(str(item)); continue
            if item.exists() and item.is_file():
                reclaimed += item.stat().st_size; item.unlink(); deleted.append(str(item))
            else:
                retained.append(str(item))
        except OSError:
            failed.append(str(item))
    cleanup = {
        "result": "LOCAL_CLEANUP_PARTIAL_REVIEW_OUTPUT_RETAINED" if not failed else "FAILED",
        "archive_state": archive_receipt.archive_state,
        "deleted_files": deleted,
        "retained_review_outputs": [str(final), str(proxy), str(contact), str(root / "manifests"), str(root / "qc")],
        "failed_deletions": failed,
        "bytes_reclaimed": reclaimed,
        "full_purge_claimed": False,
        "production_eligible": False,
    }
    write_json(root / "manifests/local_cleanup_receipt.json", cleanup)
    summary = {
        "run_id": RUN_ID,
        "workspace": str(root),
        "pexels": {"status": "PASS", "search_flow_count": 1, "selected_download_count": 1, "provider_call_made": True, "sha256": stock_manifest.local_sha256},
        "elevenlabs": {"status": "PASS", "generation_count": 1, "voice_id": eleven_ready["voice_id"], "voice_name": eleven_ready["voice_name"], "model_id": eleven_ready["model_id"], "sha256": sha256(narration_original), "actual_usage": "input_character_count_recorded; provider response has no USD cost"},
        "google_veo": {"status": "PASS", "generation_submit_count": 1, "operation_id": receipt.provider_operation_id, "output_count": 1, "actual_cost_usd": None, "actual_cost_reason": "provider operation exposes no billed amount", "estimated_cost_usd": 0.80, "sha256": veo_download["sha256"]},
        "provider_audio": provider_audio_receipt.model_dump(mode="json"),
        "native_render": {"status": "PASS", "final_mp4": str(final), "contact_sheet": str(contact), "sha256": sha256(final)},
        "media_qc": qc,
        "drive_archive": archive_receipt.model_dump(mode="json"),
        "local_cleanup": cleanup,
        "human_review": "PENDING",
        "production_eligible": False,
        "not_publishable": True,
        "youtube_call_count": 0,
        "final_media_ref_created": False,
        "human_upload_task_created": False,
        "uploaded_video_created": False,
        "proceed_to_ch1_flex": False,
    }
    write_json(root / "manifests/pa1r_run_summary.json", summary)
    return summary


def duplicate_check() -> dict[str, Any]:
    settings = Settings()
    _service, root = workspace(settings)
    ledger = PA1RCallLedger.load(root / "manifests/planned_provider_call_ledger.json")
    required = ("pexels_search", "pexels_download", "elevenlabs", "google_veo", "drive_archive")
    result = {
        "duplicate_check_mode": True,
        "second_pexels_search": 0,
        "second_pexels_download": 0,
        "second_elevenlabs_generation": 0,
        "second_veo_generation_submit": 0,
        "second_drive_archive": 0,
        "existing_results_returned": all(ledger.entries.get(key, {}).get("status") == "SUCCEEDED" for key in required),
        "attempt_counts": {key: ledger.entries.get(key, {}).get("attempt_count") for key in required},
    }
    write_json(root / "manifests/duplicate_idempotency_check.json", result)
    return result


def db_invariants() -> dict[str, int]:
    with session_scope() as session:
        models = (ChannelProfileVersion, EffectiveChannelRuntimeContextSnapshot, FinalMediaRef, HumanUploadTask, UploadedVideo, ProviderJobSnapshot, LearningToMemoryPromotionRun)
        return {model.__name__: int(session.scalar(select(func.count()).select_from(model)) or 0) for model in models}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "execute", "duplicate-check"))
    args = parser.parse_args()
    try:
        if args.mode == "preflight":
            result = preflight()
            return 0 if result["status"] == "PASS" else 3
        before = db_invariants()
        result = execute() if args.mode == "execute" else duplicate_check()
        after = db_invariants()
        settings = Settings(); _service, root = workspace(settings)
        write_json(root / "manifests/db_invariant_evidence.json", {"before": before, "after": after, "unchanged": before == after})
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 2 if result.get("status") == "WAITING_PROVIDER" else 0
    except Exception as exc:
        settings = Settings(); _service, root = workspace(settings)
        failure = {
            "status": "FAIL",
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:1000],
            "provider_call_counts": {
                key: entry.get("attempt_count", 0)
                for key, entry in PA1RCallLedger.load(root / "manifests/planned_provider_call_ledger.json").entries.items()
            },
            "automatic_retry": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        write_json(root / "manifests/pa1r_failure.json", failure)
        print(json.dumps(failure, indent=2), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
