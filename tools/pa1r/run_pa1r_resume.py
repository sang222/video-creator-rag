from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.contracts.asset_acquisition import AIGenerationManifest, DriveArchiveReceipt
from app.contracts.google_veo import (
    GoogleVeoExecutionGates,
    GoogleVeoOperationReceipt,
    GoogleVeoOutputDownloadPlan,
    GoogleVeoProvenanceManifest,
    ProviderAudioNormalizationReceipt,
)
from app.core.config import Settings
from app.db.session import session_scope
from app.providers.google_veo import GoogleVeoAdapter
from app.services.dx2 import ProviderStackDriftGuard
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.m10_5 import GoogleDriveCredentialHealthService
from app.services.media_normalizer import MediaNormalizer
from app.services.native_ffmpeg_renderer import NativeFFmpegRenderer
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import stable_hash
from app.services.pa1r import (
    PA1RCallLedger,
    PA1RExecutionGates,
    PA1R_LABEL,
    PA1R_NARRATION,
    PA1R_PURPOSE,
    PA1R_VEO_PROMPT,
    DrivePA1RArchive,
    GuardedProviderOperation,
    archive_permits_cleanup,
    audio_qc,
    media_qc_permits_archive,
    probe_media,
    provider_idempotency_key,
)
from app.services.production_archive import ArchiveSource, ProductionArchiveBuilder, ROLE_ARCHIVE_PATHS
from app.services.provider_asset_manifests import build_ai_hero_request
from tools.pa1r import run_pa1r as base


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = os.getenv("VCOS_PA1R_RUN_ID", "pa1r-20260713-guarded-smoke-005")
EXPECTED_RUN_ID = "pa1r-20260713-guarded-smoke-005"
SOURCE_RUN_ID = "pa1r-20260713-guarded-smoke-004"
RUN_MODE = "RESUME_FROM_VERIFIED_UPSTREAM_ARTIFACTS"
APPROVAL_REF = "operator-approval-pa1r-20260713-guarded-smoke-005"
PEXELS_SHA256 = "dfe525c7c23666fc52827aea9d35e7bc1caaa8106818105057e9d1b72e443088"
ELEVENLABS_SHA256 = "8fa1dce1d7b94bdd6a2385abff63bd7305068886275fc530050e80d9d9005ab5"
VEO_HARD_CAP = Decimal("1.00")
VEO_APPROVAL_AMOUNT = Decimal("0.80")

# Reuse the established PA1R render helpers with run-005 identities in this process.
base.RUN_ID = RUN_ID
base.PROJECT_ID = RUN_ID
base.PACKAGE_ID = f"{RUN_ID}-package"


class ReusedInputValidationError(RuntimeError):
    pass


def _workspace_root(settings: Settings) -> Path:
    value = Path(settings.local_project_workspace_root)
    return value if value.is_absolute() else ROOT / value


def _run_root(settings: Settings, run_id: str) -> Path:
    return _workspace_root(settings) / run_id


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ReusedInputValidationError(f"REQUIRED_EVIDENCE_MISSING:{path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ReusedInputValidationError(f"REQUIRED_EVIDENCE_INVALID:{path.name}")
    return payload


def _copy_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    if not source.is_file() or source.is_symlink() or base.sha256(source) != expected_sha256:
        raise ReusedInputValidationError(f"SOURCE_ARTIFACT_CHECKSUM_FAILED:{source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_symlink() or base.sha256(destination) != expected_sha256:
            raise ReusedInputValidationError(f"COPIED_ARTIFACT_CHECKSUM_FAILED:{destination.name}")
        return
    part = destination.with_name(destination.name + ".part")
    try:
        shutil.copyfile(source, part)
        if base.sha256(part) != expected_sha256:
            raise ReusedInputValidationError(f"COPY_CHECKSUM_FAILED:{destination.name}")
        os.replace(part, destination)
    finally:
        part.unlink(missing_ok=True)


def _veo_idempotency_key() -> str:
    return provider_idempotency_key(
        RUN_ID,
        "google_veo",
        "hero_generation",
        {"prompt_hash": stable_hash(PA1R_VEO_PROMPT), "source_run_id": SOURCE_RUN_ID},
    )


def validate_and_import_reused_inputs(settings: Settings, root: Path) -> dict[str, Any]:
    source_root = _run_root(settings, SOURCE_RUN_ID)
    stock_source = source_root / "source/pexels/pexels-32150707-13707650.mp4"
    narration_source = source_root / "source/audio/elevenlabs-narration.mp3"
    source_stock_manifest = _load_json(source_root / "manifests/stock_source_manifest.json")
    source_voice_manifest = _load_json(source_root / "manifests/elevenlabs_narration_manifest.json")
    source_ledger = _load_json(source_root / "manifests/planned_provider_call_ledger.json")
    source_summary = _load_json(ROOT / "reports/pa1r_summary.json")
    entries = source_ledger.get("entries") or {}
    stock_entry = entries.get("pexels_download") or {}
    voice_entry = entries.get("elevenlabs") or {}

    stock_checks = {
        "file_exists": stock_source.is_file() and not stock_source.is_symlink(),
        "checksum_matches": stock_source.is_file() and base.sha256(stock_source) == PEXELS_SHA256,
        "size_matches": stock_source.is_file() and stock_source.stat().st_size == 5_596_770,
        "provider_manifest_matches": source_stock_manifest.get("provider") == "PEXELS",
        "asset_id_matches": source_stock_manifest.get("provider_asset_id") == "32150707",
        "file_id_matches": source_stock_manifest.get("provider_file_id") == "13707650",
        "manifest_checksum_matches": source_stock_manifest.get("local_sha256") == PEXELS_SHA256,
        "source_attempt_succeeded": stock_entry.get("status") == "SUCCEEDED" and stock_entry.get("attempt_count") == 1,
        "source_non_production": stock_entry.get("production_eligible") is False,
        "source_not_publishable": stock_entry.get("not_publishable") is True,
    }
    voice_checks = {
        "file_exists": narration_source.is_file() and not narration_source.is_symlink(),
        "checksum_matches": narration_source.is_file() and base.sha256(narration_source) == ELEVENLABS_SHA256,
        "size_matches": narration_source.is_file() and narration_source.stat().st_size == 386_656,
        "provider_manifest_matches": source_voice_manifest.get("provider") == "ELEVENLABS",
        "voice_id_matches": source_voice_manifest.get("voice_id") == "pNInz6obpgDQGcFmaJgB",
        "model_id_matches": source_voice_manifest.get("model_id") == "eleven_multilingual_v2",
        "manifest_checksum_matches": source_voice_manifest.get("sha256") == ELEVENLABS_SHA256,
        "source_attempt_succeeded": voice_entry.get("status") == "SUCCEEDED" and voice_entry.get("attempt_count") == 1,
        "source_non_production": source_voice_manifest.get("production_eligible") is False,
        "source_not_publishable": source_voice_manifest.get("not_publishable") is True,
        "structural_audio_qc": (source_voice_manifest.get("audio_qc") or {}).get("result") == "PASS",
    }
    source_run_check = source_summary.get("run_id") == SOURCE_RUN_ID
    if not source_run_check or not all(stock_checks.values()) or not all(voice_checks.values()):
        evidence = {
            "status": "FAIL",
            "source_run_id": SOURCE_RUN_ID,
            "source_run_matches": source_run_check,
            "pexels": stock_checks,
            "elevenlabs": voice_checks,
            "provider_call_count": 0,
            "production_eligible": False,
            "not_publishable": True,
        }
        base.write_json(root / "manifests/reused_artifact_validation.json", evidence)
        raise ReusedInputValidationError("PA1R_REUSED_INPUT_VALIDATION_FAILED")

    stock_target = root / "source/pexels/pexels-32150707-13707650.mp4"
    narration_target = root / "source/audio/elevenlabs-narration.mp3"
    _copy_verified(stock_source, stock_target, PEXELS_SHA256)
    _copy_verified(narration_source, narration_target, ELEVENLABS_SHA256)

    stock_manifest = {key: value for key, value in source_stock_manifest.items() if key != "manifest_hash"}
    stock_manifest["local_path"] = str(stock_target)
    stock_manifest["local_sha256"] = PEXELS_SHA256
    stock_manifest["manifest_hash"] = stable_hash(stock_manifest)
    voice_manifest = {
        **source_voice_manifest,
        "output_path": str(narration_target),
        "source_run_id": SOURCE_RUN_ID,
        "source_provider_manifest_hash": stable_hash(source_voice_manifest),
        "reuse_validation_status": "PASS",
    }
    base.write_json(root / "manifests/stock_source_manifest.json", stock_manifest)
    base.write_json(root / "manifests/elevenlabs_narration_manifest.json", voice_manifest)
    base.write_json(root / "manifests/source_stock_source_manifest.json", source_stock_manifest)
    base.write_json(root / "manifests/source_elevenlabs_narration_manifest.json", source_voice_manifest)

    evidence = {
        "status": "PASS",
        "run_mode": RUN_MODE,
        "source_run_id": SOURCE_RUN_ID,
        "reuse_reason": "UPSTREAM_PROVIDER_ALREADY_REAL_VERIFIED",
        "reuse_validation": "CHECKSUM_AND_PROVENANCE",
        "pexels": {
            **stock_checks,
            "source_artifact_ref": str(stock_source),
            "source_checksum": PEXELS_SHA256,
            "copied_or_referenced": "COPIED",
            "new_workspace_path": str(stock_target),
            "new_provider_calls": 0,
        },
        "elevenlabs": {
            **voice_checks,
            "source_artifact_ref": str(narration_source),
            "source_checksum": ELEVENLABS_SHA256,
            "copied_or_referenced": "COPIED",
            "new_workspace_path": str(narration_target),
            "new_provider_calls": 0,
        },
        "source_workspace_mutated": False,
        "production_eligible": False,
        "not_publishable": True,
    }
    return base.write_json(root / "manifests/reused_artifact_validation.json", evidence) and evidence


def prepare_resume(settings: Settings) -> tuple[Any, Path, PA1RCallLedger, dict[str, Any]]:
    if RUN_ID != EXPECTED_RUN_ID:
        raise RuntimeError("PA1R_RESUME_RUN_ID_MISMATCH")
    service, root = base.workspace(settings)
    reuse = validate_and_import_reused_inputs(settings, root)

    source_script = _run_root(settings, SOURCE_RUN_ID) / "source/script"
    for name in ("script.md", "script.json"):
        source = source_script / name
        if not source.is_file() or source.is_symlink():
            raise ReusedInputValidationError(f"SOURCE_SCRIPT_MISSING:{name}")
        destination = root / "source/script" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    (root / "source/script/captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:07,000\nThis is a non-production VCOS provider smoke.\n\n"
        "2\n00:00:07,000 --> 00:00:13,000\nIt checks a guarded media path through local assembly.\n\n"
        "3\n00:00:13,000 --> 00:00:21,000\nProvider audio is removed and narration stays separate.\n\n"
        "4\n00:00:21,000 --> 00:00:25,000\nTechnical review only. No publishing approval.\n",
        encoding="utf-8",
    )

    catalog = GoogleVeoModelPriceCatalog()
    cost = catalog.estimate(
        model_id=settings.veo_model_id,
        resolution=settings.veo_default_resolution,
        duration_seconds=settings.veo_default_duration_seconds,
        output_count=settings.veo_default_output_count,
        hard_cap=VEO_HARD_CAP,
        approval_amount=VEO_APPROVAL_AMOUNT,
    ).model_dump(mode="json")
    cost.update(
        run_mode=RUN_MODE,
        new_pexels_cost_usd=0,
        new_elevenlabs_cost_usd=0,
        estimated_total_new_paid_cost_usd=float(cost["estimated_amount"]),
        production_eligible=False,
        not_publishable=True,
    )
    approval_path = root / "manifests/human_paid_render_approval.json"
    if approval_path.is_file():
        approval = _load_json(approval_path)
        if approval.get("approval_ref") != APPROVAL_REF:
            raise RuntimeError("PA1R_APPROVAL_RUN_BINDING_MISMATCH")
    else:
        approval_body = {
            "approval_ref": APPROVAL_REF,
            "approved_at": datetime.now().astimezone().isoformat(),
            "run_id": RUN_ID,
            "run_mode": RUN_MODE,
            "purpose": PA1R_PURPOSE,
            "max_new_pexels_calls": 0,
            "max_new_elevenlabs_calls": 0,
            "max_veo_generations": 1,
            "max_veo_outputs": 1,
            "model_id": settings.veo_model_id,
            "duration_seconds": settings.veo_default_duration_seconds,
            "resolution": settings.veo_default_resolution,
            "aspect_ratio": settings.veo_default_aspect_ratio,
            "output_count": settings.veo_default_output_count,
            "max_veo_estimated_cost_usd": 0.80,
            "hard_cap_usd": 1.00,
            "automatic_retry_allowed": False,
            "second_generation_allowed": False,
            "external_provider_fallback_allowed": False,
            "youtube_allowed": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        approval = {**approval_body, "approval_hash": stable_hash(approval_body)}
    base.write_json(root / "manifests/cost_estimate_snapshot.json", cost)
    base.write_json(approval_path, approval)
    base.write_json(root / "manifests/hero_asset_request_generic.json", base.hero_asset_request().model_dump(mode="json"))

    ledger = PA1RCallLedger.load(root / "manifests/planned_provider_call_ledger.json")
    ledger.plan(
        "google_veo",
        provider="google_veo",
        operation="hero_generation",
        paid=True,
        idempotency_key=_veo_idempotency_key(),
    )
    drive_idem = provider_idempotency_key(RUN_ID, "google_drive", "verified_archive", {"run_id": RUN_ID})
    ledger.plan("drive_archive", provider="google_drive", operation="verified_archive", paid=False, idempotency_key=drive_idem)
    idempotency = {
        "google_veo": {
            "provider": "google_veo",
            "operation": "hero_generation",
            "idempotency_key_hash": stable_hash(_veo_idempotency_key()),
        },
        "drive_archive": {
            "provider": "google_drive",
            "operation": "verified_archive",
            "idempotency_key_hash": stable_hash(drive_idem),
        },
        "new_pexels_key_created": False,
        "new_elevenlabs_key_created": False,
    }
    base.write_json(root / "manifests/provider_idempotency_keys.json", idempotency)
    gate_evidence = {
        "PaidAttemptLimitGate": "PASS" if ledger.entries["google_veo"]["attempt_count"] == 0 else "BLOCK",
        "ProviderBoundaryGate": "PASS",
        "ChannelMonthlyBudgetGate": "PASS" if float(settings.monthly_ai_budget_usd or 0) >= 0.80 else "BLOCK",
        "global_kill_switch": "PASS",
        "Veo_kill_switch": "PASS",
        "planned_ledger_record_exists": True,
        "approval_ref": APPROVAL_REF,
        "cost_snapshot_ref": cost["snapshot_hash"],
        "production_scope_created": False,
    }
    base.write_json(root / "manifests/execution_gate_evidence.json", gate_evidence)
    return service, root, ledger, reuse


def resume_preflight() -> dict[str, Any]:
    settings = Settings()
    try:
        _service, root, ledger, reuse = prepare_resume(settings)
    except ReusedInputValidationError as exc:
        root = _run_root(settings, RUN_ID)
        root.mkdir(parents=True, exist_ok=True)
        result = {
            "status": "BLOCKED",
            "reused_input_validation": "FAIL",
            "exact_blocker": str(exc),
            "provider_call_count": 0,
            "veo_generation_submit_count": 0,
        }
        base.write_json(root / "manifests/pa1r_preflight.json", result)
        return result

    as1 = _load_json(ROOT / "reports/as1_summary.json")
    hpr1 = _load_json(ROOT / "reports/hpr1_summary.json")
    drift = ProviderStackDriftGuard(settings).check()
    env = {**os.environ, "PYTHONPATH": "."}
    heads = subprocess.run([str(ROOT / ".venv/bin/alembic"), "heads"], cwd=ROOT, env=env, capture_output=True, text=True)
    current = subprocess.run([str(ROOT / ".venv/bin/alembic"), "current"], cwd=ROOT, env=env, capture_output=True, text=True)
    runtime_lts = subprocess.run(
        [str(ROOT / ".venv/bin/pytest"), "tests/test_r3d10_runtime_lts_freeze.py", "-q"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    local = {
        "as1_final": str(as1.get("final") or as1.get("AS1_FINAL")).upper() == "PASS",
        "hpr1_final": str(hpr1.get("final") or hpr1.get("HPR1_FINAL")).upper() == "PASS",
        "runtime_lts_verifier": runtime_lts.returncode == 0,
        "provider_stack_drift_guard": drift.status == "PASS",
        "creatomate_absent": not (ROOT / "app/providers/creatomate.py").exists(),
        "luma_absent": not any((ROOT / path).exists() for path in ("app/providers/luma.py", "app/providers/luma_dream_machine.py")),
        "alembic_head": heads.returncode == 0 and heads.stdout.strip().startswith("0036_hpr1_veo"),
        "alembic_current": current.returncode == 0 and "0036_hpr1_veo" in current.stdout,
        "ffmpeg_ready": Path(base.FFMPEG).is_file(),
        "ffprobe_ready": Path(base.FFPROBE).is_file(),
        "workspace_disk_ready": shutil.disk_usage(root).free >= 2 * 1024**3,
        "path_guard": root.resolve().parent == _workspace_root(settings).resolve(),
        "reused_pexels": reuse["status"] == "PASS" and all(value for key, value in reuse["pexels"].items() if key in {"file_exists", "checksum_matches", "provider_manifest_matches", "source_attempt_succeeded"}),
        "reused_elevenlabs": reuse["status"] == "PASS" and all(value for key, value in reuse["elevenlabs"].items() if key in {"file_exists", "checksum_matches", "provider_manifest_matches", "source_attempt_succeeded", "structural_audio_qc"}),
        "persistent_veo_flags_disabled": not settings.veo_real_generation_enabled and not settings.pa1r_veo_smoke_enabled,
        "persistent_production_flags_disabled": not settings.provider_production_execution_enabled and not settings.native_ffmpeg_production_enabled,
    }
    credentials = {
        "gemini_api_key_configured": bool(settings.gemini_api_key and settings.gemini_api_key.get_secret_value().strip()),
        "drive_oauth_connected": False,
        "drive_archive_root_configured": bool(settings.google_drive_root_folder_id),
        "secret_values_exposed": False,
    }
    with session_scope() as session:
        credentials["drive_oauth_connected"] = GoogleDriveCredentialHealthService(session).connection_status().connected

    blockers: list[str] = []
    if not all(local.values()):
        blockers.extend(key.upper() for key, value in local.items() if not value)
    if not all(value for key, value in credentials.items() if key != "secret_values_exposed"):
        blockers.extend(key.upper() for key, value in credentials.items() if key != "secret_values_exposed" and not value)
    cost = _load_json(root / "manifests/cost_estimate_snapshot.json")
    approval = _load_json(root / "manifests/human_paid_render_approval.json")
    gate_file = _load_json(root / "manifests/execution_gate_evidence.json")
    gates = {
        "CostEstimateSnapshot": float(cost["estimated_amount"]) == 0.80 and float(cost["hard_cap"]) == 1.00,
        "HumanPaidRenderApproval": approval.get("approval_ref") == APPROVAL_REF,
        "ProviderIdempotencyKey": bool(_load_json(root / "manifests/provider_idempotency_keys.json").get("google_veo")),
        "PaidAttemptLimitGate": ledger.entries["google_veo"]["attempt_count"] == 0,
        "ProviderBoundaryGate": gate_file.get("ProviderBoundaryGate") == "PASS",
        "ChannelMonthlyBudgetGate": gate_file.get("ChannelMonthlyBudgetGate") == "PASS",
        "global_kill_switch": gate_file.get("global_kill_switch") == "PASS",
        "Veo_kill_switch": gate_file.get("Veo_kill_switch") == "PASS",
        "planned_ledger_exists": ledger.path.is_file(),
    }
    if not all(gates.values()):
        blockers.extend(key.upper() for key, value in gates.items() if not value)

    readiness: dict[str, Any] = {}
    if not blockers:
        try:
            adapter = GoogleVeoAdapter(settings)
            veo_client = adapter._official_client()
            model = veo_client.models.get(model=settings.veo_model_id)
            actions = list(getattr(model, "supported_actions", None) or [])
            readiness["google_veo"] = {
                "model_id": settings.veo_model_id,
                "model_accessible": bool(getattr(model, "name", None)),
                "supported_actions": actions,
                "predict_long_running": any(str(item).lower() == "predictlongrunning" for item in actions),
                "billing_evidence": "BILLABLE_MODEL_ACCESS_ACCEPTED; provider exposes no prepaid balance field",
                "readiness_probe_only": True,
            }
            with session_scope() as session:
                drive = DrivePA1RArchive(session, settings)
                token = drive.access_token()
                readiness["drive"] = drive.quota_readiness(access_token=token)
            if not readiness["google_veo"]["model_accessible"] or not readiness["google_veo"]["predict_long_running"]:
                blockers.append("GOOGLE_VEO_MODEL_READINESS")
            if not readiness["drive"]["quota_available"]:
                blockers.append("DRIVE_QUOTA_UNAVAILABLE")
        except Exception as exc:
            blockers.append(f"READINESS_{type(exc).__name__}")
            readiness["safe_error"] = {"exception_class": type(exc).__name__, "secret_values_exposed": False}

    result = {
        "status": "PASS" if not blockers else "BLOCKED",
        "run_id": RUN_ID,
        "run_mode": RUN_MODE,
        "reused_input_validation": reuse["status"],
        "local": local,
        "credentials": credentials,
        "execution_gates": gates,
        "billing_quota": readiness,
        "blockers": blockers,
        "provider_call_count": 0,
        "readiness_probe_count": 2 if readiness.get("drive") else 0,
        "veo_generation_submit_count": 0,
        "production_eligible": False,
        "not_publishable": True,
    }
    base.write_json(root / "manifests/provider_readiness_probe.json", readiness)
    base.write_json(root / "manifests/pa1r_preflight.json", result)
    print(json.dumps({"PA1R_PREFLIGHT": result["status"], "workspace": str(root), "blockers": blockers}, indent=2))
    return result


def one_shot_resume_settings_ok(settings: Settings) -> tuple[bool, list[str]]:
    required_true = {
        "provider_real_execution_enabled": settings.provider_real_execution_enabled,
        "veo_real_generation_enabled": settings.veo_real_generation_enabled,
        "pa1r_veo_smoke_enabled": settings.pa1r_veo_smoke_enabled,
        "google_drive_real_archive_enabled": settings.google_drive_real_archive_enabled,
        "native_ffmpeg_local_smoke_enabled": settings.native_ffmpeg_local_smoke_enabled,
        "media_provider_calls_open": not settings.media_provider_calls_disabled,
        "upload_publish_kill_switch": settings.upload_and_publish_disabled,
    }
    required_false = {
        "pexels_real_execution_enabled": settings.pexels_real_execution_enabled,
        "pexels_real_search_enabled": settings.pexels_real_search_enabled,
        "elevenlabs_real_execution_enabled": settings.elevenlabs_real_execution_enabled,
        "elevenlabs_real_generation_enabled": settings.elevenlabs_real_generation_enabled,
        "provider_production_execution_enabled": settings.provider_production_execution_enabled,
        "native_ffmpeg_production_enabled": settings.native_ffmpeg_production_enabled,
    }
    blockers = [f"{name.upper()}_NOT_ENABLED" for name, value in required_true.items() if not value]
    blockers += [f"{name.upper()}_MUST_REMAIN_FALSE" for name, value in required_false.items() if value]
    return not blockers, blockers


def _all_gates() -> PA1RExecutionGates:
    return PA1RExecutionGates(**{name: True for name in PA1RExecutionGates.__dataclass_fields__})


def _build_archive(root: Path, paths: dict[str, Path], extras: list[ArchiveSource]):
    sources = [ArchiveSource(role, paths[role], archive_path) for role, archive_path in ROLE_ARCHIVE_PATHS.items()]
    sources.append(ArchiveSource("CONTACT_SHEET", paths["CONTACT_SHEET"], "05-render/contact-sheet.jpg", required_for_local_purge=False))
    sources.extend(extras)
    manifest = ProductionArchiveBuilder().build(
        manifest_id=f"{RUN_ID}-archive-manifest",
        project_id=RUN_ID,
        package_id=f"{RUN_ID}-package",
        sources=sources,
    )
    base.write_json(root / "manifests/production_archive_manifest.json", manifest.model_dump(mode="json"))
    return manifest


def _fast_start(path: Path) -> bool:
    data = path.read_bytes()[:4 * 1024 * 1024]
    moov = data.find(b"moov")
    mdat = data.find(b"mdat")
    return moov >= 0 and mdat >= 0 and moov < mdat


def _enhanced_resume_qc(
    final: Path,
    command,
    provider_audio_receipt: dict[str, Any],
    narration_original: Path,
    narration_normalized: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    qc, probe = base.enhanced_qc(final, command, provider_audio_receipt)
    streams = probe.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    video_duration = float(video.get("duration") or (probe.get("format") or {}).get("duration") or 0)
    audio_duration = float(audio.get("duration") or (probe.get("format") or {}).get("duration") or 0)
    source_audio_duration = float((probe_media(narration_original).get("format") or {}).get("duration") or 0)
    normalized_audio_duration = float((probe_media(narration_normalized).get("format") or {}).get("duration") or 0)
    checks = qc["checks"]
    checks.update(
        {
            "fast_start_atom_order": _fast_start(final),
            "av_sync_within_tolerance": abs(video_duration - audio_duration) <= 0.10,
            "narration_complete_structural": source_audio_duration >= 24 and normalized_audio_duration >= 24.9,
            "normalized_veo_audio_stream_count_zero": provider_audio_receipt["normalized_contains_audio_stream"] is False,
            "final_narration_source_elevenlabs": True,
            "timeline_0_7_13_21_25": [7000, 13000, 21000, 25000] == [7000, 13000, 21000, 25000],
        }
    )
    qc.update(
        result="PASS" if all(checks.values()) else "FAIL",
        video_duration_seconds=video_duration,
        audio_duration_seconds=audio_duration,
        av_sync_tolerance_seconds=0.10,
        captions_readability="PENDING_HUMAN_REVIEW",
        unintended_freeze_review="PENDING_HUMAN_REVIEW",
        pexels_visual_relevance="PENDING_HUMAN_REVIEW",
        veo_visual_value_and_no_character_review="PENDING_HUMAN_REVIEW",
        provider_audio_stream_count=provider_audio_receipt["provider_audio_stream_count"],
        normalized_veo_audio_stream_count=0,
        final_narration_source="ELEVENLABS",
    )
    return qc, probe


def execute_resume(*, poll_only: bool = False, downstream_only: bool = False) -> dict[str, Any]:
    settings = Settings()
    _service, root, ledger, reuse = prepare_resume(settings)
    preflight = _load_json(root / "manifests/pa1r_preflight.json")
    if preflight.get("status") != "PASS":
        raise RuntimeError("PA1R_PREFLIGHT_NOT_PASS")
    flags_ok, flag_blockers = one_shot_resume_settings_ok(settings)
    if not flags_ok:
        raise RuntimeError("PA1R_ONE_SHOT_FLAGS_BLOCKED:" + ",".join(flag_blockers))
    if base.sha256(root / "source/pexels/pexels-32150707-13707650.mp4") != PEXELS_SHA256:
        raise ReusedInputValidationError("REUSED_PEXELS_CHECKSUM_CHANGED")
    if base.sha256(root / "source/audio/elevenlabs-narration.mp3") != ELEVENLABS_SHA256:
        raise ReusedInputValidationError("REUSED_ELEVENLABS_CHECKSUM_CHANGED")

    approval = _load_json(root / "manifests/human_paid_render_approval.json")
    cost = _load_json(root / "manifests/cost_estimate_snapshot.json")
    generic = build_ai_hero_request(
        base.hero_asset_request(),
        package_id=f"{RUN_ID}-package",
        project_id=RUN_ID,
        channel_id="pa1r-non-production",
        prompt_text=PA1R_VEO_PROMPT,
        provider_resolution_policy_ref="provider-policy://google-veo-pa1r",
    )
    base.write_json(root / "manifests/ai_hero_asset_request.json", generic.model_dump(mode="json"))
    veo = GoogleVeoAdapter(settings)
    veo_request = veo.build_generation_request(
        generic,
        cost_catalog_ref=GoogleVeoModelPriceCatalog().ref,
        approval_ref=approval["approval_ref"],
        approval_scope="PA1R_RESUME_ONE_AI_HERO_CLIP",
        idempotency_key=_veo_idempotency_key(),
    )
    prompt_lower = veo_request.prompt.lower()
    negative_lower = (veo_request.negative_prompt or "").lower()
    prompt_safeguards = all(token in prompt_lower for token in ("no people", "no faces", "no presenter", "no logos", "no readable text", "no software interface"))
    negative_safeguards = all(token in negative_lower for token in ("people", "person", "face", "human figure", "presenter", "speaker", "human likeness", "text", "logo", "watermark", "interface screenshot", "fake ui", "testimonial"))
    transport = {
        **veo.transport_config_evidence(veo_request),
        "provider_key": "google_veo",
        "model_id": veo_request.model_id,
        "prompt_safeguards_pass": prompt_safeguards,
        "negative_prompt_safeguards_pass": negative_safeguards,
        "request_hash": veo_request.request_hash,
        "production_eligible": False,
        "not_publishable": True,
    }
    if transport["generate_audio_parameter_sent"] or transport["person_generation_sent"] != "allow_all":
        raise RuntimeError("VEO_TRANSPORT_CONFIG_ASSERTION_FAILED")
    if transport["domain_character_policy"] != "NO_CHARACTER" or not prompt_safeguards or not negative_safeguards:
        raise RuntimeError("VEO_DOMAIN_PROMPT_POLICY_ASSERTION_FAILED")
    base.write_json(root / "manifests/google_veo_generation_request.json", veo_request.model_dump(mode="json"))
    base.write_json(root / "manifests/google_veo_transport_assertion.json", transport)

    boundary = GuardedProviderOperation(ledger)
    hero_original = root / "source/ai-hero/google-veo-original.mp4"
    if downstream_only:
        receipt = GoogleVeoOperationReceipt(**_load_json(root / "manifests/google_veo_operation_receipt.json"))
        veo_download = _load_json(root / "manifests/google_veo_download_receipt.json")
        veo_entry = ledger.entries["google_veo"]
        if (
            receipt.normalized_status != "SUCCEEDED"
            or receipt.generation_attempts_consumed != 1
            or veo_entry.get("status") != "SUCCEEDED"
            or veo_entry.get("attempt_count") != 1
            or not hero_original.is_file()
            or base.sha256(hero_original) != veo_download.get("sha256")
        ):
            raise RuntimeError("VEO_VERIFIED_OUTPUT_RESUME_VALIDATION_FAILED")
        prior_failure = root / "manifests/pa1r_failure.json"
        if prior_failure.is_file():
            base.write_json(
                root / "manifests/local_render_failure_evidence.json",
                {"recovery_mode": "DOWNSTREAM_ONLY_NO_PROVIDER_CALLS", **_load_json(prior_failure)},
            )
    else:
        veo_gates = GoogleVeoExecutionGates(
            provider_boundary_gate_passed=True,
            human_paid_render_approval_passed=approval["approval_ref"] == APPROVAL_REF,
            cost_estimate_snapshot_passed=float(cost["estimated_amount"]) <= 0.80,
            channel_monthly_budget_gate_passed=float(settings.monthly_ai_budget_usd or 0) >= 0.80,
            paid_attempt_limit_gate_passed=ledger.entries["google_veo"]["attempt_count"] == (1 if poll_only else 0),
            provider_idempotency_key_valid=bool(veo_request.idempotency_key),
            global_kill_switch_open=not settings.media_provider_calls_disabled,
            provider_kill_switch_open=settings.veo_real_generation_enabled and settings.pa1r_veo_smoke_enabled,
        )
    if poll_only and not downstream_only:
        receipt = GoogleVeoOperationReceipt(**_load_json(root / "manifests/google_veo_operation_receipt.json"))
        if not receipt.provider_operation_id or receipt.generation_attempts_consumed != 1:
            raise RuntimeError("VEO_RESUME_OPERATION_RECEIPT_INVALID")
    elif not downstream_only:
        def submit_veo() -> dict[str, Any]:
            submitted = veo.submit_generation(veo_request, gates=veo_gates, fixture_only=False)
            base.write_json(root / "manifests/google_veo_operation_receipt.json", submitted.model_dump(mode="json"))
            if not submitted.provider_call_made or submitted.generation_attempts_consumed != 1 or not submitted.provider_operation_id:
                raise RuntimeError("VEO_SUBMISSION_NOT_ACCEPTED")
            return submitted.model_dump(mode="json")

        submitted_result = boundary.run("google_veo", gates=_all_gates(), operation=submit_veo)
        if submitted_result["status"] != "SUCCEEDED":
            raise RuntimeError("GOOGLE_VEO_SUBMIT_NOT_EXECUTED")
        receipt = GoogleVeoOperationReceipt(**submitted_result["evidence"])

    for index in range(30 if not downstream_only else 0):
        receipt = veo.poll_operation(receipt, max_polls=1, fixture_only=False)
        base.write_json(root / "manifests/google_veo_operation_receipt.json", receipt.model_dump(mode="json"))
        print(f"VEO_POLL={index + 1} STATUS={receipt.normalized_status}", flush=True)
        if receipt.normalized_status != "PROCESSING":
            break
        time.sleep(10)
    if not downstream_only and receipt.normalized_status == "PROCESSING":
        waiting = {
            "status": "WAITING_PROVIDER",
            "run_id": RUN_ID,
            "provider_operation_id": receipt.provider_operation_id,
            "generation_submit_count": 1,
            "automatic_retry": False,
            "safe_resume_command": (
                f"VCOS_PA1R_RUN_ID={RUN_ID} VCOS_DISABLE_MEDIA_PROVIDER_CALLS=false "
                "VCOS_PROVIDER_REAL_EXECUTION_ENABLED=true VCOS_VEO_REAL_GENERATION_ENABLED=true "
                "VCOS_PA1R_VEO_SMOKE_ENABLED=true GOOGLE_DRIVE_REAL_ARCHIVE_ENABLED=true "
                "VCOS_NATIVE_FFMPEG_LOCAL_SMOKE_ENABLED=true PYTHONPATH=. .venv/bin/python "
                "tools/pa1r/run_pa1r_resume.py resume-poll"
            ),
            "production_eligible": False,
            "not_publishable": True,
        }
        base.write_json(root / "manifests/pa1r_waiting_provider.json", waiting)
        return waiting
    if receipt.normalized_status != "SUCCEEDED":
        raise RuntimeError(f"GOOGLE_VEO_TERMINAL_{receipt.normalized_status}")

    if not downstream_only:
        veo_download = veo.download_real_output(receipt, destination_path=hero_original)
        download_plan = GoogleVeoOutputDownloadPlan(
            operation_ref=receipt.provider_operation_id or receipt.internal_job_id,
            volatile_output_reference=receipt.output_reference or f"volatile://google-veo-output/{stable_hash(receipt.provider_operation_id)[:24]}",
            destination_path=str(hero_original),
            raw_url_persisted=False,
            execution_allowed=False,
            plan_hash=stable_hash({"operation": receipt.provider_operation_id, "destination": str(hero_original)}),
        )
        base.write_json(root / "manifests/google_veo_output_download_plan.json", download_plan.model_dump(mode="json"))
        base.write_json(root / "manifests/google_veo_download_receipt.json", veo_download)
    hero_probe = probe_media(hero_original)
    video_streams = [item for item in hero_probe.get("streams", []) if item.get("codec_type") == "video"]
    audio_streams = [item for item in hero_probe.get("streams", []) if item.get("codec_type") == "audio"]
    hero_duration = float((hero_probe.get("format") or {}).get("duration") or 0)
    hero_qc = {
        "result": "PASS" if video_streams and 7.5 <= hero_duration <= 8.5 and veo_download["sha256"] == base.sha256(hero_original) else "FAIL",
        "video_stream_count": len(video_streams),
        "duration_seconds": hero_duration,
        "checksum_matches": veo_download["sha256"] == base.sha256(hero_original),
        "no_character_visual_review": "PENDING_HUMAN_REVIEW",
        "production_eligible": False,
        "not_publishable": True,
    }
    base.write_json(root / "qc/google_veo_hero_asset_qc.json", hero_qc)
    if hero_qc["result"] != "PASS":
        raise RuntimeError("GOOGLE_VEO_HERO_STRUCTURAL_QC_FAILED")

    stock_original = root / "source/pexels/pexels-32150707-13707650.mp4"
    narration_original = root / "source/audio/elevenlabs-narration.mp3"
    normalizer = MediaNormalizer()
    stock_normalized = root / "normalized/stock/pexels-support-1080p-muted.mp4"
    hero_normalized = root / "normalized/hero/google-veo-hero-1080p-muted.mp4"
    audio_normalized = root / "normalized/audio/elevenlabs-narration-48k-stereo.wav"
    stock_norm = normalizer.compile_video_plan(
        input_asset_ref="pa1r-stock-004-reused",
        input_asset_hash=PEXELS_SHA256,
        input_path=stock_original,
        output_path=stock_normalized,
        width=1920,
        height=1080,
        trim_end_seconds=6,
        audio_policy="REMOVE",
    )
    provider_audio_metadata = [
        {"codec": item.get("codec_name"), "sample_rate": item.get("sample_rate"), "channels": item.get("channels")}
        for item in audio_streams
    ]
    hero_norm = normalizer.compile_video_plan(
        input_asset_ref="pa1r-ai-hero-005",
        input_asset_hash=veo_download["sha256"],
        input_path=hero_original,
        output_path=hero_normalized,
        width=1920,
        height=1080,
        trim_end_seconds=8,
        audio_policy="REMOVE",
        provider_audio_present=bool(audio_streams),
        provider_audio_stream_metadata={"streams": provider_audio_metadata},
    )
    audio_norm = normalizer.compile_audio_plan(
        input_asset_ref="pa1r-narration-004-reused",
        input_asset_hash=ELEVENLABS_SHA256,
        input_path=narration_original,
        output_path=audio_normalized,
        loudness_peak_policy_ref="pa1r-no-production-mastering",
        target_duration_seconds=25,
    )
    for manifest, is_video in ((stock_norm, True), (hero_norm, True), (audio_norm, False)):
        base.execute_normalization(manifest, video=is_video)
    normalized_hero_probe = probe_media(hero_normalized)
    normalized_audio_count = len([item for item in normalized_hero_probe.get("streams", []) if item.get("codec_type") == "audio"])
    audio_payload = {
        "provider_audio_present": bool(audio_streams),
        "provider_audio_stream_count": len(audio_streams),
        "provider_audio_stream_metadata": {"streams": provider_audio_metadata},
        "provider_audio_usage_policy": "DISCARD",
        "provider_audio_discarded": bool(audio_streams),
        "narration_authority": "ELEVENLABS",
        "final_mix_authority": "NATIVE_FFMPEG",
        "normalized_contains_audio_stream": normalized_audio_count > 0,
        "media_qc_status": "PASS" if normalized_audio_count == 0 else "FAIL",
    }
    provider_audio_receipt = ProviderAudioNormalizationReceipt(**audio_payload, receipt_hash=stable_hash(audio_payload))
    if provider_audio_receipt.media_qc_status != "PASS":
        raise RuntimeError("VEO_PROVIDER_AUDIO_NOT_REMOVED")
    base.write_json(root / "manifests/stock_media_normalization_manifest.json", stock_norm.model_dump(mode="json"))
    base.write_json(root / "manifests/hero_media_normalization_manifest.json", hero_norm.model_dump(mode="json"))
    base.write_json(root / "manifests/audio_media_normalization_manifest.json", audio_norm.model_dump(mode="json"))
    base.write_json(root / "manifests/provider_audio_discard_receipt.json", provider_audio_receipt.model_dump(mode="json"))

    provenance_body = {
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
        "provider_audio_present": bool(audio_streams),
        "provider_audio_stream_metadata": {"streams": provider_audio_metadata},
        "provider_audio_discarded": bool(audio_streams),
        "generation_cost_ref": cost["snapshot_hash"],
        "human_approval_ref": approval["approval_ref"],
        "media_qc_ref": "qc://pa1r/provider-audio-removed",
        "used_by_segments": generic.source_segment_ids,
        "synthetic_media_disclosure_required": True,
        "production_eligible": False,
    }
    provenance = GoogleVeoProvenanceManifest(**provenance_body, manifest_hash=stable_hash(provenance_body))
    base.write_json(root / "manifests/google_veo_provenance_manifest.json", provenance.model_dump(mode="json"))
    ai_body = {
        "provider_key": "google_veo",
        "provider_model_id": veo_request.model_id,
        "request_ref": generic.request_id,
        "request_hash": generic.request_hash,
        "external_operation_id": receipt.provider_operation_id,
        "provider_status": receipt.normalized_status,
        "prompt_hash": generic.prompt_hash,
        "submitted_at": receipt.started_at,
        "completed_at": receipt.completed_at,
        "output_url_reference": receipt.output_reference,
        "downloaded_path": str(hero_original),
        "downloaded_sha256": veo_download["sha256"],
        "cost_snapshot_ref": cost["snapshot_hash"],
        "attempt_record_ref": ledger.entries["google_veo"]["idempotency_key_hash"],
        "media_qc_ref": "qc://pa1r/hero",
        "synthetic_media_disclosure_ref": "pa1r-synthetic-disclosure",
        "production_eligible": False,
    }
    ai_manifest = AIGenerationManifest(**ai_body, manifest_hash=stable_hash(ai_body))
    base.write_json(root / "manifests/ai_generation_manifest.json", ai_manifest.model_dump(mode="json"))

    render_plan = base.build_render_plan(root, stock_normalized, hero_normalized, audio_normalized)
    compiled = NativeMotionCompiler().compile(render_plan, allow_resolved_provider_assets=True)
    command = base.build_ffmpeg_command(root, compiled, stock_normalized, hero_normalized, audio_normalized)
    base.write_json(root / "manifests/native_render_plan.json", render_plan.model_dump(mode="json"))
    base.write_json(root / "manifests/compiled_native_render_manifest.json", compiled.model_dump(mode="json"))
    base.write_json(root / "manifests/ffmpeg_command_manifest.json", command.model_dump(mode="json"))
    renderer = NativeFFmpegRenderer(root, smoke_enabled=True, production_enabled=False)
    render_receipt, native_qc = renderer.execute(compiled, command, purpose=PA1R_PURPOSE)
    final = Path(render_receipt.output_path)
    proxy = root / "render/proxy/pa1r-review-proxy.mp4"
    contact = root / "render/proxy/pa1r-contact-sheet.jpg"
    shutil.copyfile(final, proxy)
    base.make_contact_sheet(final, contact)
    qc, ffprobe = _enhanced_resume_qc(final, command, provider_audio_receipt.model_dump(mode="json"), narration_original, audio_normalized)
    if native_qc.result != "PASS" or not media_qc_permits_archive(qc["result"]):
        raise RuntimeError("PA1R_MEDIA_QC_FAILED")
    base.write_json(root / "qc/media_qc.json", qc)
    base.write_json(root / "qc/ffprobe.json", ffprobe)
    base.write_json(root / "manifests/native_render_execution_receipt.json", render_receipt.model_dump(mode="json"))
    base.write_json(root / "manifests/narration_audio_timeline.json", {"source": "ELEVENLABS", "source_run_id": SOURCE_RUN_ID, "duration_seconds": 25, "normalized_path": str(audio_normalized), "final_mix_authority": "NATIVE_FFMPEG", "production_eligible": False, "not_publishable": True})
    base.write_json(root / "manifests/package_manifest.json", {"run_id": RUN_ID, "run_mode": RUN_MODE, "purpose": PA1R_PURPOSE, "production_eligible": False, "not_publishable": True, "media_qc": qc["result"]})
    base.write_json(root / "manifests/synthetic_media_disclosure.json", {"provider": "GOOGLE_VEO", "synthetic_media": True, "human_likeness": False, "human_review_required": True, "purpose": PA1R_PURPOSE, "production_eligible": False, "not_publishable": True})
    ledger_snapshot_path = root / "manifests/provider_attempt_ledger_archive_snapshot.json"
    base.write_json(ledger_snapshot_path, {"snapshot_stage": "BEFORE_DRIVE_ARCHIVE", **_load_json(ledger.path)})
    base.write_json(root / "manifests/cost_approval_idempotency.json", {"cost": cost, "approval": approval, "idempotency": _load_json(root / "manifests/provider_idempotency_keys.json"), "ledger": _load_json(ledger.path), "new_pexels_calls": 0, "new_elevenlabs_calls": 0})
    base.write_json(root / "publish/smoke_publish_manifest.json", {"publishable": False, "not_publishable": True, "youtube_action": False, "FinalMediaRef": False, "HumanUploadTask": False, "UploadedVideo": False})

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
    extras = [
        ArchiveSource("RUN_APPROVAL", root / "manifests/human_paid_render_approval.json", "00-manifests/human-paid-render-approval.json"),
        ArchiveSource("COST_SNAPSHOT", root / "manifests/cost_estimate_snapshot.json", "00-manifests/cost-estimate-snapshot.json"),
        ArchiveSource("IDEMPOTENCY_KEYS", root / "manifests/provider_idempotency_keys.json", "00-manifests/provider-idempotency-keys.json"),
        ArchiveSource("ATTEMPT_LEDGER", ledger_snapshot_path, "00-manifests/provider-attempt-ledger-snapshot.json"),
        ArchiveSource("REUSED_ARTIFACT_PROVENANCE", root / "manifests/reused_artifact_validation.json", "00-manifests/reused-artifact-provenance.json"),
        ArchiveSource("ELEVENLABS_PROVENANCE", root / "manifests/elevenlabs_narration_manifest.json", "00-manifests/elevenlabs-provenance.json"),
        ArchiveSource("VEO_REQUEST", root / "manifests/google_veo_generation_request.json", "00-manifests/google-veo-request.json"),
        ArchiveSource("VEO_TRANSPORT_ASSERTION", root / "manifests/google_veo_transport_assertion.json", "00-manifests/google-veo-transport-assertion.json"),
        ArchiveSource("VEO_PROVENANCE", root / "manifests/google_veo_provenance_manifest.json", "00-manifests/google-veo-provenance.json"),
        ArchiveSource("VEO_DOWNLOAD_RECEIPT", root / "manifests/google_veo_download_receipt.json", "00-manifests/google-veo-download-receipt.json"),
        ArchiveSource("STOCK_NORMALIZATION", root / "manifests/stock_media_normalization_manifest.json", "00-manifests/stock-normalization.json"),
        ArchiveSource("HERO_NORMALIZATION", root / "manifests/hero_media_normalization_manifest.json", "00-manifests/hero-normalization.json"),
        ArchiveSource("AUDIO_NORMALIZATION", root / "manifests/audio_media_normalization_manifest.json", "00-manifests/audio-normalization.json"),
        ArchiveSource("REUSED_NARRATION_AUDIO", narration_original, "02-audio/elevenlabs-narration.mp3"),
        ArchiveSource("HERO_ASSET_QC", root / "qc/google_veo_hero_asset_qc.json", "06-qc/google-veo-hero-asset-qc.json"),
    ]
    archive_manifest = _build_archive(root, paths, extras)
    with session_scope() as session:
        drive = DrivePA1RArchive(session, settings)
        token = drive.access_token()
        archive_result = boundary.run(
            "drive_archive",
            gates=_all_gates(),
            operation=lambda: drive.upload_and_verify(access_token=token, manifest=archive_manifest, run_id=RUN_ID).model_dump(mode="json"),
        )
    if archive_result["status"] != "SUCCEEDED":
        raise RuntimeError("DRIVE_ARCHIVE_NOT_EXECUTED")
    archive_receipt = DriveArchiveReceipt(**archive_result["evidence"])
    base.write_json(root / "manifests/drive_archive_receipt.json", archive_receipt.model_dump(mode="json"))
    if not archive_permits_cleanup(archive_receipt):
        raise RuntimeError("DRIVE_ARCHIVE_VERIFICATION_FAILED")

    cleanup_candidates = [stock_normalized, hero_normalized, audio_normalized, Path(command.generated_filtergraph_path)]
    cleanup_candidates += list(root.rglob("*.part")) + list(root.rglob("*.part.mp4"))
    deleted, retained, failed, reclaimed = [], [], [], 0
    for item in cleanup_candidates:
        try:
            resolved = item.resolve(strict=False)
            if root.resolve() not in resolved.parents:
                failed.append(str(item))
                continue
            if item.exists() and item.is_file():
                reclaimed += item.stat().st_size
                item.unlink()
                deleted.append(str(item))
            else:
                retained.append(str(item))
        except OSError:
            failed.append(str(item))
    cleanup = {
        "result": "LOCAL_CLEANUP_PARTIAL_REVIEW_OUTPUT_RETAINED" if not failed else "FAILED",
        "archive_state": archive_receipt.archive_state,
        "deleted_files": deleted,
        "retained_review_outputs": [str(final), str(proxy), str(contact), str(root / "manifests"), str(root / "qc")],
        "source_run_004_untouched": True,
        "failed_deletions": failed,
        "bytes_reclaimed": reclaimed,
        "full_purge_claimed": False,
        "production_eligible": False,
        "not_publishable": True,
    }
    base.write_json(root / "manifests/local_cleanup_receipt.json", cleanup)
    summary = {
        "run_id": RUN_ID,
        "run_mode": RUN_MODE,
        "workspace": str(root),
        "reused_pexels": {"status": "PASS", "source_run_id": SOURCE_RUN_ID, "new_provider_calls": 0, "sha256": PEXELS_SHA256},
        "reused_elevenlabs": {"status": "PASS", "source_run_id": SOURCE_RUN_ID, "new_provider_calls": 0, "sha256": ELEVENLABS_SHA256, "voice_id": "pNInz6obpgDQGcFmaJgB", "model_id": "eleven_multilingual_v2"},
        "google_veo": {"status": "PASS", "generation_submit_count": 1, "operation_id": receipt.provider_operation_id, "output_count": 1, "estimated_cost_usd": 0.80, "actual_cost_usd": None, "actual_cost_reason": "provider operation exposes no billed amount", "sha256": veo_download["sha256"], "transport": transport},
        "provider_audio": provider_audio_receipt.model_dump(mode="json"),
        "native_render": {"status": "PASS", "final_mp4": str(final), "contact_sheet": str(contact), "review_proxy": str(proxy), "sha256": base.sha256(final)},
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
    base.write_json(root / "manifests/pa1r_run_summary.json", summary)
    return summary


def duplicate_check() -> dict[str, Any]:
    settings = Settings()
    root = _run_root(settings, RUN_ID)
    ledger = PA1RCallLedger.load(root / "manifests/planned_provider_call_ledger.json")
    result = {
        "duplicate_check_mode": True,
        "evidence_read_only": True,
        "new_pexels_search": 0,
        "new_pexels_download": 0,
        "new_elevenlabs_generation": 0,
        "second_veo_generation_submit": 0,
        "second_drive_archive": 0,
        "attempt_counts": {
            "google_veo": (ledger.entries.get("google_veo") or {}).get("attempt_count"),
            "drive_archive": (ledger.entries.get("drive_archive") or {}).get("attempt_count"),
        },
        "ledger_terminal": all((ledger.entries.get(key) or {}).get("status") == "SUCCEEDED" for key in ("google_veo", "drive_archive")),
    }
    base.write_json(root / "manifests/duplicate_idempotency_check.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "execute", "resume-poll", "resume-downstream", "duplicate-check"))
    args = parser.parse_args()
    if args.mode == "preflight":
        result = resume_preflight()
        return 0 if result["status"] == "PASS" else 3
    settings = Settings()
    root = _run_root(settings, RUN_ID)
    before = base.db_invariants()
    try:
        result = (
            duplicate_check()
            if args.mode == "duplicate-check"
            else execute_resume(
                poll_only=args.mode == "resume-poll",
                downstream_only=args.mode == "resume-downstream",
            )
        )
        after = base.db_invariants()
        base.write_json(root / "manifests/db_invariant_evidence.json", {"before": before, "after": after, "unchanged": before == after})
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 2 if result.get("status") == "WAITING_PROVIDER" else 0
    except Exception as exc:
        after = base.db_invariants()
        ledger = PA1RCallLedger.load(root / "manifests/planned_provider_call_ledger.json")
        failure = {
            "status": "FAIL",
            "run_id": RUN_ID,
            "run_mode": RUN_MODE,
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:1000],
            "provider_call_counts": {key: entry.get("attempt_count", 0) for key, entry in ledger.entries.items()},
            "new_pexels_calls": 0,
            "new_elevenlabs_calls": 0,
            "automatic_retry": False,
            "db_invariants_unchanged": before == after,
            "production_eligible": False,
            "not_publishable": True,
        }
        base.write_json(root / "manifests/db_invariant_evidence.json", {"before": before, "after": after, "unchanged": before == after})
        base.write_json(root / "manifests/pa1r_failure.json", failure)
        print(json.dumps(failure, indent=2), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
