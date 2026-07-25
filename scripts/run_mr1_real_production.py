from __future__ import annotations

import argparse
import json
import os
import subprocess
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import select

from app.contracts.mr1 import (
    MR1FinalMediaCloseoutCommand,
    MR1ProviderAttemptContinuationCommand,
    MR1ProviderAttemptContinuationReviewCommand,
    MR1StartCommand,
)
from app.core.config import Settings
from app.db.models import Artifact, ArtifactVersion
from app.db.session import session_scope
from app.services.m10_5 import (
    GoogleDriveConfigService,
    GoogleDriveOAuthCredentialService,
)
from app.services.mr1_drive_archive import MR1DriveArchiveService
from app.services.mr1_real_production import (
    MR1RealProductionService,
    RUN_ARTIFACT_TYPE,
)
from app.services.mr1_runtime_readiness import probe_mr1_production_toolchain


ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = ROOT / "reports"
DEFAULT_WORKSPACE_ROOT = ROOT / "var" / "mr1" / "runs"

PROJECT_ID = uuid.UUID("2522a8f1-1ea4-4d66-8ea5-411aaa8f152b")
PACKAGE_ARTIFACT_VERSION_ID = uuid.UUID("7de25ac8-46e4-46da-b112-f805f16ebaaa")
PACKAGE_CONTENT_HASH = (
    "200b3be30b92ccff3b0efb26881d5654ab4b53162afe73d4e7f34bed3b0454bd"
)
APPROVAL_ID = uuid.UUID("4ccc7185-e760-4470-aba9-857ab0a18f77")
APPROVAL_CONTENT_HASH = (
    "4a8c259debc1ae3f94feb7c5be959e0d42bca048911b052a221eda7373d1c25c"
)
PROFILE_ID = uuid.UUID("d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711")
SNAPSHOT_ID = uuid.UUID("e6c33d80-f5d8-4f72-9abc-87de3601b89e")
EXACT_VOICE_ID = "pNInz6obpgDQGcFmaJgB"
EXACT_MODEL_ID = "eleven_multilingual_v2"

SUMMARY_PATH = REPORTS_ROOT / "mr1_summary.json"
REPAIR_CYCLES_PATH = REPORTS_ROOT / "mr1_repair_cycles.json"
REPORT_PATH = REPORTS_ROOT / "mr1_real_production_report.md"
REAPPROVAL_SUMMARY_PATH = REPORTS_ROOT / "mr1_reapproval_v3_summary.json"
CONTINUATION_REVIEW_JSON_PATH = (
    REPORTS_ROOT / "mr1_pexels_continuation_review.json"
)
CONTINUATION_REVIEW_MARKDOWN_PATH = (
    REPORTS_ROOT / "mr1_pexels_continuation_review.md"
)

LEGACY_PROJECT_ID = PROJECT_ID
LEGACY_PACKAGE_ARTIFACT_VERSION_ID = PACKAGE_ARTIFACT_VERSION_ID
LEGACY_PACKAGE_CONTENT_HASH = PACKAGE_CONTENT_HASH
LEGACY_APPROVAL_ID = APPROVAL_ID
LEGACY_APPROVAL_CONTENT_HASH = APPROVAL_CONTENT_HASH
LEGACY_PROFILE_ID = PROFILE_ID
LEGACY_SNAPSHOT_ID = SNAPSHOT_ID
LEGACY_REAPPROVAL_SUMMARY_PATH = REAPPROVAL_SUMMARY_PATH

VERDICT_KEYS = (
    "MR1_ENTRY",
    "MR1_APPROVAL_BINDING",
    "MR1_REUSE_DECISIONS",
    "MR1_PREFLIGHT",
    "MR1_REQUIRED_PROVIDER_EXECUTION",
    "MR1_ELEVENLABS",
    "MR1_FORCED_ALIGNMENT",
    "MR1_CANONICAL_TIMELINE",
    "MR1_PEXELS",
    "MR1_GEMINI_IMAGE",
    "MR1_GOOGLE_VEO",
    "MR1_NATIVE_ASSETS",
    "MR1_ASSET_RESOLUTION",
    "MR1_MEDIA_NORMALIZATION",
    "MR1_NATIVE_RENDER_PLAN",
    "MR1_NATIVE_MOTION_COMPILER",
    "MR1_NATIVE_FFMPEG_RENDER",
    "MR1_TECHNICAL_MEDIA_QC",
    "MR1_CREATIVE_MEDIA_QC",
    "MR1_REVIEW_MEDIA_CANDIDATE",
    "MR1_DRIVE_ARCHIVE",
    "ARCHIVE_VERIFIED",
    "MR1_PROVIDER_CALL_COUNT",
    "MR1_RENDER_ATTEMPTS",
    "MR1_REPAIR_CYCLES",
    "MR1_HUMAN_REVIEW",
    "MR1_FINAL_MEDIA_REF",
    "MR1_FINAL",
    "DESTINATION_STATUS",
    "UPLOAD_READY",
    "PUBLISH_EXECUTION_READY",
    "PROCEED_TO_DESTINATION_CLOSEOUT",
    "PROCEED_TO_PUB1",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Start or idempotently resume the one exact approved MR1 production run."
        )
    )
    parser.add_argument(
        "--authority-mode",
        choices=("legacy", "sc04"),
        required=True,
        help=(
            "Select the immutable authority family. Legacy frozen refs are only "
            "available when this is explicitly 'legacy'."
        ),
    )
    parser.add_argument("--project-id", type=uuid.UUID)
    parser.add_argument("--package-artifact-version-id", type=uuid.UUID)
    parser.add_argument("--package-content-hash")
    parser.add_argument("--approval-id", type=uuid.UUID)
    parser.add_argument("--approval-content-hash")
    parser.add_argument("--profile-id", type=uuid.UUID)
    parser.add_argument("--snapshot-id", type=uuid.UUID)
    parser.add_argument("--reapproval-summary", type=Path)
    parser.add_argument(
        "--resume-run-id",
        type=uuid.UUID,
        help=(
            "Resume one already-persisted MR1 run. Without this flag the exact "
            "approval is started idempotently and an existing run is resumed."
        ),
    )
    parser.add_argument(
        "--readiness-only",
        action="store_true",
        help=(
            "Run the non-generation runtime/credential/toolchain readiness gate "
            "without creating a run or submitting a provider request."
        ),
    )
    parser.add_argument(
        "--prepare-extra-pexels-review",
        action="store_true",
        help=(
            "Persist only the immutable provider-free continuation review "
            "manifest and print its exact operator bindings. Never resumes."
        ),
    )
    parser.add_argument(
        "--approve-extra-pexels-attempt",
        action="store_true",
        help=(
            "Persist exact operator authority for one additional Pexels scene "
            "attempt before resuming the existing run."
        ),
    )
    parser.add_argument(
        "--approve-extra-pexels-sc04-attempt",
        action="store_true",
        help=(
            "Deprecated alias for --approve-extra-pexels-attempt with SC-04."
        ),
    )
    parser.add_argument(
        "--pexels-operation-key",
        choices=("pexels:SC-04", "pexels:SC-07", "pexels:SC-09"),
    )
    parser.add_argument("--approved-pexels-stock-search-intent")
    parser.add_argument("--approved-pexels-sc09-stock-search-intent")
    parser.add_argument(
        "--operator-review-manifest-artifact-version-id",
        type=uuid.UUID,
    )
    parser.add_argument("--operator-review-manifest-content-hash")
    parser.add_argument("--operator-review-task-id", type=uuid.UUID)
    parser.add_argument("--continuation-decided-by-user-id", type=uuid.UUID)
    parser.add_argument(
        "--closeout",
        action="store_true",
        help=(
            "Persist one explicit human full-watch decision for exact run, "
            "candidate, output hash, and Drive receipt bindings."
        ),
    )
    parser.add_argument("--human-decision", choices=("PASS", "REJECT"))
    parser.add_argument("--operator-decision-text")
    parser.add_argument(
        "--review-media-candidate-artifact-version-id",
        type=uuid.UUID,
    )
    parser.add_argument("--review-media-candidate-content-hash")
    parser.add_argument("--reviewed-output-sha256")
    parser.add_argument(
        "--drive-archive-receipt-artifact-version-id",
        type=uuid.UUID,
    )
    parser.add_argument("--drive-archive-receipt-content-hash")
    parser.add_argument("--archive-identity")
    parser.add_argument("--decided-by-user-id", type=uuid.UUID)
    return parser.parse_args()


def _is_sha256(value: str | None) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _configure_exact_authority(args: argparse.Namespace) -> None:
    global PROJECT_ID
    global PACKAGE_ARTIFACT_VERSION_ID
    global PACKAGE_CONTENT_HASH
    global APPROVAL_ID
    global APPROVAL_CONTENT_HASH
    global PROFILE_ID
    global SNAPSHOT_ID
    global REAPPROVAL_SUMMARY_PATH

    explicit = (
        args.project_id,
        args.package_artifact_version_id,
        args.package_content_hash,
        args.approval_id,
        args.approval_content_hash,
        args.profile_id,
        args.snapshot_id,
        args.reapproval_summary,
    )
    if args.authority_mode == "legacy":
        if any(value is not None for value in explicit):
            raise SystemExit(
                "legacy authority uses frozen defaults; do not mix explicit refs"
            )
        PROJECT_ID = LEGACY_PROJECT_ID
        PACKAGE_ARTIFACT_VERSION_ID = LEGACY_PACKAGE_ARTIFACT_VERSION_ID
        PACKAGE_CONTENT_HASH = LEGACY_PACKAGE_CONTENT_HASH
        APPROVAL_ID = LEGACY_APPROVAL_ID
        APPROVAL_CONTENT_HASH = LEGACY_APPROVAL_CONTENT_HASH
        PROFILE_ID = LEGACY_PROFILE_ID
        SNAPSHOT_ID = LEGACY_SNAPSHOT_ID
        REAPPROVAL_SUMMARY_PATH = LEGACY_REAPPROVAL_SUMMARY_PATH
        return

    if any(value is None for value in explicit):
        raise SystemExit(
            "sc04 authority requires exact project, final package/hash, fresh MR1 "
            "approval/hash, profile/snapshot, and reapproval summary"
        )
    if not _is_sha256(args.package_content_hash) or not _is_sha256(
        args.approval_content_hash
    ):
        raise SystemExit("sc04 package and approval hashes must be lowercase SHA-256")
    if (
        args.project_id == LEGACY_PROJECT_ID
        or args.package_artifact_version_id == LEGACY_PACKAGE_ARTIFACT_VERSION_ID
        or args.approval_id == LEGACY_APPROVAL_ID
        or args.approval_content_hash == LEGACY_APPROVAL_CONTENT_HASH
    ):
        raise SystemExit("sc04 execution must not reuse a legacy package or approval")
    summary_path = args.reapproval_summary.resolve()
    if summary_path == LEGACY_REAPPROVAL_SUMMARY_PATH.resolve():
        raise SystemExit("sc04 execution requires a fresh reapproval summary")
    PROJECT_ID = args.project_id
    PACKAGE_ARTIFACT_VERSION_ID = args.package_artifact_version_id
    PACKAGE_CONTENT_HASH = args.package_content_hash
    APPROVAL_ID = args.approval_id
    APPROVAL_CONTENT_HASH = args.approval_content_hash
    PROFILE_ID = args.profile_id
    SNAPSHOT_ID = args.snapshot_id
    REAPPROVAL_SUMMARY_PATH = summary_path


def _explicit_closeout_command(
    args: argparse.Namespace,
) -> MR1FinalMediaCloseoutCommand | None:
    names = (
        "human_decision",
        "operator_decision_text",
        "review_media_candidate_artifact_version_id",
        "review_media_candidate_content_hash",
        "reviewed_output_sha256",
        "drive_archive_receipt_artifact_version_id",
        "drive_archive_receipt_content_hash",
        "archive_identity",
        "decided_by_user_id",
    )
    supplied = {name: getattr(args, name, None) for name in names}
    if not args.closeout:
        continuation_approval = bool(
            args.approve_extra_pexels_attempt
            or args.approve_extra_pexels_sc04_attempt
        )
        closeout_only_supplied = {
            name: value
            for name, value in supplied.items()
            if name != "operator_decision_text"
        }
        if any(value is not None for value in closeout_only_supplied.values()) or (
            supplied["operator_decision_text"] is not None
            and not continuation_approval
        ):
            raise SystemExit(
                "closeout-only inputs require the explicit --closeout mode"
            )
        return None
    if args.authority_mode != "sc04":
        raise SystemExit("MR1 closeout requires sc04 mode with explicit authority refs")
    if args.resume_run_id is None or any(value is None for value in supplied.values()):
        raise SystemExit(
            "--closeout requires exact run, candidate id/hash, reviewed output "
            "SHA-256, Drive receipt id/hash, archive identity, human decision, "
            "operator decision text, and deciding actor id"
        )
    for name in (
        "review_media_candidate_content_hash",
        "reviewed_output_sha256",
        "drive_archive_receipt_content_hash",
    ):
        if not _is_sha256(str(supplied[name])):
            raise SystemExit(f"--{name.replace('_', '-')} must be lowercase SHA-256")
    try:
        return MR1FinalMediaCloseoutCommand(
            run_id=args.resume_run_id,
            project_id=PROJECT_ID,
            review_media_candidate_artifact_version_id=supplied[
                "review_media_candidate_artifact_version_id"
            ],
            review_media_candidate_content_hash=supplied[
                "review_media_candidate_content_hash"
            ],
            reviewed_output_sha256=supplied["reviewed_output_sha256"],
            drive_archive_receipt_artifact_version_id=supplied[
                "drive_archive_receipt_artifact_version_id"
            ],
            drive_archive_receipt_content_hash=supplied[
                "drive_archive_receipt_content_hash"
            ],
            archive_identity=supplied["archive_identity"],
            decided_by_user_id=supplied["decided_by_user_id"],
            decision=supplied["human_decision"],
            operator_decision_text=supplied["operator_decision_text"],
        )
    except ValueError as exc:
        raise SystemExit(f"invalid explicit MR1 closeout authority: {exc}") from exc


def _secret_present(value: Any) -> bool:
    if value is None:
        return False
    if hasattr(value, "get_secret_value"):
        return bool(str(value.get_secret_value()).strip())
    return bool(str(value).strip())


def _runtime_readiness(
    *, settings: Settings, session: Any, workspace_root: Path
) -> dict[str, Any]:
    """Fail closed before MR1 can construct a provider gateway.

    This gate performs no narration, alignment, stock-search, render, Drive upload,
    YouTube or publish call.  OAuth token validation may refresh an expired Drive
    access token; it is required so an unusable archive credential is discovered
    before any approved media-provider attempt is consumed.
    """

    checks: dict[str, bool] = {
        "repository_identity": ROOT
        == Path("/Users/sangss/Desktop/video-creator-rag").resolve(),
        "reapproval_summary_present": REAPPROVAL_SUMMARY_PATH.is_file(),
        "global_provider_execution_enabled": bool(
            settings.provider_real_execution_enabled
        ),
        "production_provider_execution_enabled": bool(
            settings.provider_production_execution_enabled
        ),
        "media_provider_kill_switch_open": not bool(
            settings.media_provider_calls_disabled
        ),
        "elevenlabs_execution_enabled": bool(
            settings.elevenlabs_real_execution_enabled
        ),
        "elevenlabs_generation_enabled": bool(
            settings.elevenlabs_real_generation_enabled
        ),
        "forced_alignment_permission_confirmed": (
            settings.elevenlabs_forced_alignment_permission_confirmed is True
        ),
        "pexels_execution_enabled": bool(settings.pexels_real_execution_enabled),
        "pexels_search_enabled": bool(settings.pexels_real_search_enabled),
        "pexels_exact_scene_cap": settings.pexels_max_clips_per_long == 3,
        "pexels_attribution_required": settings.pexels_attribution_required is True,
        "native_ffmpeg_production_enabled": bool(
            settings.native_ffmpeg_production_enabled
        ),
        "drive_offload_enabled": bool(settings.google_drive_offload_enabled),
        "drive_archive_enabled": bool(settings.google_drive_archive_enabled),
        "drive_real_archive_enabled": bool(settings.google_drive_real_archive_enabled),
        "upload_and_publish_remains_disabled": bool(
            settings.upload_and_publish_disabled
        ),
        "old_provider_smoke_remains_disabled": bool(
            settings.old_provider_smoke_disabled
        ),
        "gemini_unplanned_route_disabled": not bool(
            settings.gemini_image_real_generation_enabled
        ),
        "veo_unplanned_route_disabled": not bool(settings.veo_real_generation_enabled),
        "provider_readiness_probe_disabled": not bool(
            settings.provider_real_readiness_probe_enabled
        ),
        "elevenlabs_credential_configured": _secret_present(
            settings.elevenlabs_api_key
        ),
        "pexels_credential_configured": _secret_present(settings.pexels_api_key),
        "configured_voice_exact": settings.elevenlabs_voice_id == EXACT_VOICE_ID,
        "configured_model_exact": settings.elevenlabs_model_id == EXACT_MODEL_ID,
        "monthly_ai_budget_covers_hard_cap": (
            settings.monthly_ai_budget_usd is not None
            and settings.monthly_ai_budget_usd >= 1
        ),
        "elevenlabs_budget_covers_hard_cap": (
            settings.elevenlabs_monthly_cap_usd is not None
            and settings.elevenlabs_monthly_cap_usd >= 1
        ),
        "stock_budget_nonnegative": (
            settings.stock_monthly_budget_usd is not None
            and settings.stock_monthly_budget_usd >= 0
        ),
        "budget_mode_hard_env": settings.budget_mode == "hard_env",
        "workspace_contained": _is_contained(
            workspace_root.resolve(), (ROOT / "var" / "mr1").resolve()
        ),
        "youtube_gateway_absent_by_design": True,
    }

    reapproval: dict[str, Any] = {}
    if checks["reapproval_summary_present"]:
        try:
            reapproval = json.loads(REAPPROVAL_SUMMARY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            checks["reapproval_summary_present"] = False
    verdicts = reapproval.get("verdicts") or reapproval
    approval = reapproval.get("approval") or reapproval
    exact_target = reapproval.get("exact_target") or {}
    bindings = reapproval.get("exact_bindings") or {}
    no_execution = reapproval.get("no_execution_proof") or reapproval
    summary_project_id = exact_target.get(
        "video_project_id", exact_target.get("project_id")
    )
    summary_profile_id = bindings.get("channel_profile_version_id") or (
        bindings.get("channel_profile_version") or {}
    ).get("id")
    summary_snapshot_id = bindings.get("compiled_policy_snapshot_id") or (
        bindings.get("compiled_channel_policy_snapshot") or {}
    ).get("id")
    checks.update(
        {
            "reapproval_final_pass": verdicts.get("MR1_REAPPROVAL_FINAL") == "PASS",
            "reapproval_proceed_true": verdicts.get("PROCEED_TO_MR1") is True,
            "exact_approval_id": approval.get("approval_decision_id")
            == str(APPROVAL_ID),
            "exact_approval_hash": approval.get("approval_content_hash")
            == APPROVAL_CONTENT_HASH,
            "exact_project_id": summary_project_id == str(PROJECT_ID),
            "exact_package_artifact_version_id": exact_target.get(
                "package_artifact_version_id"
            )
            == str(PACKAGE_ARTIFACT_VERSION_ID),
            "exact_package_content_hash": exact_target.get("package_content_hash")
            == PACKAGE_CONTENT_HASH,
            "exact_profile_id": summary_profile_id == str(PROFILE_ID),
            "exact_snapshot_id": summary_snapshot_id == str(SNAPSHOT_ID),
            "mr1_was_not_started_at_reapproval": verdicts.get("MR1_EXECUTION")
            == "NOT_STARTED",
            "reapproval_provider_calls_zero": no_execution.get("provider_calls") == 0,
            "reapproval_render_calls_zero": no_execution.get("render_calls") == 0,
            "reapproval_drive_calls_zero": no_execution.get("drive_calls") == 0,
            "reapproval_youtube_calls_zero": no_execution.get("youtube_calls") == 0,
        }
    )

    try:
        MR1RealProductionService(
            session,
            workspace_root,
            expected_profile_id=PROFILE_ID,
            expected_snapshot_id=SNAPSHOT_ID,
            settings=settings,
        )._resolve_exact_authority(
            MR1StartCommand(
                approval_id=APPROVAL_ID,
                approval_content_hash=APPROVAL_CONTENT_HASH,
                project_id=PROJECT_ID,
                package_artifact_version_id=PACKAGE_ARTIFACT_VERSION_ID,
            )
        )
    except Exception:
        checks["canonical_database_authority_reopened"] = False
    else:
        checks["canonical_database_authority_reopened"] = True

    toolchain_readiness = probe_mr1_production_toolchain(
        workspace_root=workspace_root,
        allowed_workspace_root=(ROOT / "var" / "mr1").resolve(),
    )
    toolchain_checks = toolchain_readiness.get("checks") or {}
    checks["ffmpeg_ready"] = toolchain_checks.get("ffmpeg_executable") == "PASS"
    checks["ffprobe_ready"] = toolchain_checks.get("ffprobe_executable") == "PASS"
    for name, result in sorted(toolchain_checks.items()):
        checks[f"production_toolchain_{name}"] = result == "PASS"

    git_root = _git_root()
    checks["git_repository_root_exact"] = git_root == ROOT

    drive_config = GoogleDriveConfigService(settings)
    checks["drive_oauth_configured"] = drive_config.oauth_configured()
    checks["drive_root_folder_configured"] = bool(drive_config.root_folder_id())
    checks["drive_upload_mode_qualified"] = drive_config.upload_mode() in {
        "multipart",
        "resumable",
    }
    drive_credentials = GoogleDriveOAuthCredentialService(
        session, config_service=drive_config
    )
    reference = drive_credentials.get_connected_reference()
    checks["drive_connected_reference"] = reference is not None
    checks["drive_access_token_usable"] = False
    drive_root_check_names = (
        "root_folder_metadata_accessible",
        "root_folder_identity_exact",
        "root_folder_type_exact",
        "root_folder_listable",
        "mutation_free",
    )
    for name in drive_root_check_names:
        checks[f"drive_{name}"] = False
    deferred_drive_checks = {
        "drive_access_token_usable",
        *(f"drive_{name}" for name in drive_root_check_names),
    }
    local_preconditions_pass = all(
        passed is True
        for key, passed in checks.items()
        if key not in deferred_drive_checks
    )
    drive_root_readiness: dict[str, Any] = {}
    if (
        reference is not None
        and checks["drive_oauth_configured"]
        and local_preconditions_pass
    ):
        try:
            access_token = drive_credentials.get_valid_access_token(reference)
        except Exception:
            access_token = None
        checks["drive_access_token_usable"] = bool(access_token)
        if access_token:
            drive_archive = MR1DriveArchiveService.from_existing_configuration(
                session=session,
                settings=settings,
                source_root=workspace_root,
                state_root=workspace_root / ".drive-state",
            )
            drive_root_readiness = drive_archive.read_only_root_readiness(
                access_token=access_token
            )
            root_checks = drive_root_readiness.get("checks") or {}
            for name in drive_root_check_names:
                checks[f"drive_{name}"] = root_checks.get(name) == "PASS"

    failed = sorted(key for key, passed in checks.items() if passed is not True)
    evidence = {
        "schema_version": "mr1.runtime-readiness.v1",
        "mode": "NO_BILLABLE_GENERATION_PROBE",
        "exact_authority": {
            "approval_id": str(APPROVAL_ID),
            "approval_content_hash": APPROVAL_CONTENT_HASH,
            "project_id": str(PROJECT_ID),
            "package_artifact_version_id": str(PACKAGE_ARTIFACT_VERSION_ID),
            "package_content_hash": PACKAGE_CONTENT_HASH,
            "profile_id": str(PROFILE_ID),
            "snapshot_id": str(SNAPSHOT_ID),
        },
        "checks": {
            key: "PASS" if passed else "FAIL" for key, passed in sorted(checks.items())
        },
        "production_toolchain_readiness": toolchain_readiness,
        "drive_root_readiness": drive_root_readiness,
        "failed_checks": failed,
        "provider_generation_calls": 0,
        "render_calls": 0,
        "drive_archive_calls": 0,
        "youtube_calls": 0,
        "result": "PASS" if not failed else "FAIL",
        "checked_at": datetime.now(UTC).isoformat(),
    }
    return evidence


def _closeout_runtime_readiness(
    *,
    settings: Settings,
    session: Any,
    workspace_root: Path,
    command: MR1FinalMediaCloseoutCommand,
) -> dict[str, Any]:
    """Probe only the authority and Drive surface needed after full watch."""

    service = MR1RealProductionService(
        session,
        workspace_root,
        expected_profile_id=PROFILE_ID,
        expected_snapshot_id=SNAPSHOT_ID,
        settings=settings,
    )
    try:
        _artifact, version = service._require_run(command.run_id, lock=False)
        state = deepcopy(version.content or {})
    except Exception:
        state = {}
    candidate = state.get("review_media_candidate") or {}
    drive = state.get("drive_archive") or {}
    checks = {
        "repository_identity": ROOT
        == Path("/Users/sangss/Desktop/video-creator-rag").resolve(),
        "git_repository_root_exact": _git_root() == ROOT,
        "exact_run_present": state.get("run_id") == str(command.run_id),
        "exact_project": state.get("project_id") == str(command.project_id),
        "waiting_or_finalizing_boundary": state.get("current_state")
        in {
            "AWAITING_HUMAN_FULL_WATCH",
            "WAITING_HUMAN_REVIEW",
            "FINALIZING_ARCHIVE_SUPPLEMENT",
            "FINAL_MEDIA_REGISTERED",
        },
        "exact_candidate_binding": bool(
            candidate.get("artifact_version_id")
            == str(command.review_media_candidate_artifact_version_id)
            and candidate.get("content_hash")
            == command.review_media_candidate_content_hash
            and candidate.get("output_sha256") == command.reviewed_output_sha256
        ),
        "exact_drive_receipt_binding": bool(
            drive.get("artifact_version_id")
            == str(command.drive_archive_receipt_artifact_version_id)
            and drive.get("content_hash") == command.drive_archive_receipt_content_hash
            and state.get("archive_identity") == command.archive_identity
        ),
        "upload_and_publish_disabled": (settings.upload_and_publish_disabled is True),
        "youtube_unreachable": True,
        "media_provider_generation_not_required": True,
    }
    drive_root_readiness: dict[str, Any] = {}
    if command.decision == "PASS":
        config = GoogleDriveConfigService(settings)
        credentials = GoogleDriveOAuthCredentialService(
            session,
            config_service=config,
        )
        checks.update(
            {
                "drive_offload_enabled": (
                    settings.google_drive_offload_enabled is True
                ),
                "drive_real_archive_enabled": (
                    settings.google_drive_real_archive_enabled is True
                ),
                "drive_archive_enabled": (
                    settings.google_drive_archive_enabled is True
                ),
                "drive_root_configured": bool(config.root_folder_id()),
                "drive_oauth_configured": config.oauth_configured(),
            }
        )
        reference = credentials.get_connected_reference()
        checks["drive_connected_reference"] = reference is not None
        if reference is not None and checks["drive_oauth_configured"]:
            try:
                access_token = credentials.get_valid_access_token(reference)
            except Exception:
                access_token = None
            checks["drive_access_token_usable"] = bool(access_token)
            if access_token:
                archive = MR1DriveArchiveService.from_existing_configuration(
                    session=session,
                    settings=settings,
                    source_root=workspace_root,
                    state_root=workspace_root / ".drive-state",
                )
                drive_root_readiness = archive.read_only_root_readiness(
                    access_token=access_token
                )
                checks["drive_root_readiness"] = (
                    drive_root_readiness.get("result") == "PASS"
                )
            else:
                checks["drive_root_readiness"] = False
        else:
            checks["drive_access_token_usable"] = False
            checks["drive_root_readiness"] = False
    else:
        checks["reject_requires_no_drive_or_provider_gateway"] = True
    failed = sorted(key for key, value in checks.items() if value is not True)
    return {
        "schema_version": "mr1.closeout-runtime-readiness.v1",
        "mode": (
            "POST_WATCH_DRIVE_ONLY_NO_MEDIA_PROVIDER_PROBE"
            if command.decision == "PASS"
            else "POST_WATCH_REJECT_AUTHORITY_ONLY_NO_GATEWAY"
        ),
        "run_id": str(command.run_id),
        "checks": {
            key: "PASS" if value else "FAIL" for key, value in sorted(checks.items())
        },
        "drive_root_readiness": drive_root_readiness,
        "failed_checks": failed,
        "provider_generation_calls": 0,
        "media_provider_preflights": 0,
        "render_calls": 0,
        "drive_mutation_calls": 0,
        "youtube_calls": 0,
        "result": "PASS" if not failed else "FAIL",
        "checked_at": datetime.now(UTC).isoformat(),
    }


def _is_contained(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _git_root() -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return Path(value).resolve() if value else None


def _load_run_state(
    session: Any, result: Mapping[str, Any]
) -> tuple[dict[str, Any], ArtifactVersion | None]:
    raw_id = result.get("run_artifact_version_id")
    if not raw_id:
        return {}, None
    try:
        version_id = uuid.UUID(str(raw_id))
    except ValueError:
        return {}, None
    version = session.get(ArtifactVersion, version_id)
    return deepcopy(version.content or {}) if version is not None else {}, version


def _gateway_preflights(gateways: Any) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    failed: list[str] = []
    for name in ("narration", "alignment", "pexels", "drive"):
        gateway = getattr(gateways, name, None)
        preflight = getattr(gateway, "preflight", None)
        if not callable(preflight):
            evidence[name] = {
                "result": "FAIL",
                "failed_checks": ["LOCAL_PREFLIGHT_NOT_EXPOSED"],
            }
            failed.append(name)
            continue
        item = deepcopy(preflight())
        evidence[name] = item
        if item.get("result") != "PASS":
            failed.append(name)
    return {
        "schema_version": "mr1.gateway-preflights.v1",
        "mode": "LOCAL_NO_NETWORK",
        "gateways": evidence,
        "failed_gateways": failed,
        "result": "PASS" if not failed else "FAIL",
    }


def _recover_persisted_result(
    workspace_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read the durable run after an unexpected exception; never resume it here."""

    with session_scope() as session:
        rows = session.execute(
            select(Artifact, ArtifactVersion)
            .join(ArtifactVersion, Artifact.current_version_id == ArtifactVersion.id)
            .where(
                Artifact.video_project_id == PROJECT_ID,
                Artifact.artifact_type == RUN_ARTIFACT_TYPE,
            )
            .order_by(Artifact.updated_at.desc())
        ).all()
        for _artifact, version in rows:
            state = deepcopy(version.content or {})
            if state.get("approval_id") != str(APPROVAL_ID):
                continue
            service = MR1RealProductionService(
                session,
                workspace_root,
                expected_profile_id=PROFILE_ID,
                expected_snapshot_id=SNAPSHOT_ID,
            )
            # This is a read-only projection of a committed state; it cannot submit.
            result = service._public_result(version, state)
            return result, state
    return {}, {}


def _failure_result(
    error: str, readiness: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    checks = (readiness or {}).get("checks") or {}
    entry_pass = all(
        checks.get(key) == "PASS"
        for key in (
            "repository_identity",
            "git_repository_root_exact",
            "reapproval_final_pass",
            "reapproval_proceed_true",
            "exact_project_id",
            "exact_package_artifact_version_id",
            "exact_package_content_hash",
            "exact_profile_id",
            "exact_snapshot_id",
        )
    )
    approval_pass = entry_pass and all(
        checks.get(key) == "PASS"
        for key in (
            "exact_approval_id",
            "exact_approval_hash",
            "canonical_database_authority_reopened",
        )
    )
    return {
        "approval_id": str(APPROVAL_ID),
        "approval_content_hash": APPROVAL_CONTENT_HASH,
        "exact_target": {
            "project_id": str(PROJECT_ID),
            "package_artifact_version_id": str(PACKAGE_ARTIFACT_VERSION_ID),
            "package_content_hash": PACKAGE_CONTENT_HASH,
            "profile_id": str(PROFILE_ID),
            "snapshot_id": str(SNAPSHOT_ID),
        },
        "current_state": "FAILED_BEFORE_DURABLE_RUN",
        "terminal_error": error,
        "attempts": [],
        "provider_call_counts": {
            "elevenlabs_narration": 0,
            "forced_alignment": 0,
            "pexels_scene_flows": 0,
            "google_gemini_image": 0,
            "google_veo": 0,
            "google_drive_archive_flows": 0,
            "youtube": 0,
        },
        "scene_executions": [],
        "review_media_candidate": None,
        "drive_archive": None,
        "MR1_ENTRY": "PASS" if entry_pass else "FAIL",
        "MR1_APPROVAL_BINDING": "PASS" if approval_pass else "FAIL",
        "MR1_PREFLIGHT": "FAIL",
        "MR1_REQUIRED_PROVIDER_EXECUTION": "FAIL",
        "MR1_ELEVENLABS": "FAIL",
        "MR1_FORCED_ALIGNMENT": "FAIL",
        "MR1_CANONICAL_TIMELINE": "FAIL",
        "MR1_PEXELS": "FAIL",
        "MR1_GEMINI_IMAGE": "NOT_REQUIRED",
        "MR1_GOOGLE_VEO": "NOT_REQUIRED",
        "MR1_NATIVE_ASSETS": "FAIL",
        "MR1_ASSET_RESOLUTION": "FAIL",
        "MR1_MEDIA_NORMALIZATION": "FAIL",
        "MR1_NATIVE_RENDER_PLAN": "FAIL",
        "MR1_NATIVE_MOTION_COMPILER": "FAIL",
        "MR1_NATIVE_FFMPEG_RENDER": "FAIL",
        "MR1_TECHNICAL_MEDIA_QC": "FAIL",
        "MR1_CREATIVE_MEDIA_QC": "FAIL",
        "MR1_REVIEW_MEDIA_CANDIDATE": "FAIL",
        "MR1_DRIVE_ARCHIVE": "FAIL",
        "ARCHIVE_VERIFIED": False,
        "MR1_PROVIDER_CALL_COUNT": 0,
        "MR1_RENDER_ATTEMPTS": 0,
        "MR1_REPAIR_CYCLES": 0,
        "MR1_HUMAN_REVIEW": "PENDING",
        "MR1_FINAL_MEDIA_REF": "NOT_CREATED",
        "MR1_FINAL": "FAIL",
        "DESTINATION_STATUS": "PENDING_PLATFORM_ID",
        "UPLOAD_READY": False,
        "PUBLISH_EXECUTION_READY": False,
        "PROCEED_TO_DESTINATION_CLOSEOUT": False,
        "PROCEED_TO_PUB1": False,
        "youtube_calls": 0,
    }


def _report_payloads(
    *,
    result: Mapping[str, Any],
    state: Mapping[str, Any],
    readiness: Mapping[str, Any] | None,
    invocation: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    generated_at = datetime.now(UTC).isoformat()
    candidate = deepcopy(result.get("review_media_candidate") or {})
    drive = deepcopy(result.get("drive_archive") or {})
    local = deepcopy(state.get("local_result") or {})
    repair_cycles = deepcopy(state.get("repair_cycles") or [])
    paths = {
        "review_mp4": candidate.get("output_file_ref") or candidate.get("output_path"),
        "thumbnail": candidate.get("thumbnail_path"),
        "captions": candidate.get("captions_path"),
        "local_archive": local.get("archive_path")
        or local.get("local_archive_path")
        or state.get("workspace"),
        "workspace": result.get("workspace") or state.get("workspace"),
        "drive_archive_reference": drive.get("drive_folder_id")
        or drive.get("folder_id")
        or drive.get("archive_identity")
        or result.get("archive_identity"),
    }
    next_action = _next_action(result, paths)
    attempts = [_compact_attempt(item) for item in result.get("attempts") or []]
    usage = deepcopy((result.get("narration") or {}).get("usage_metadata") or {})
    summary = {
        "schema_version": "mr1-real-production-summary/v1",
        "generated_at": generated_at,
        "invocation": invocation,
        "run_id": result.get("run_id"),
        "current_state": result.get("current_state"),
        "exact_authority": {
            "approval_id": str(APPROVAL_ID),
            "approval_content_hash": APPROVAL_CONTENT_HASH,
            "project_id": str(PROJECT_ID),
            "package_artifact_version_id": str(PACKAGE_ARTIFACT_VERSION_ID),
            "package_content_hash": PACKAGE_CONTENT_HASH,
            "profile_id": str(PROFILE_ID),
            "snapshot_id": str(SNAPSHOT_ID),
        },
        "runtime_readiness": deepcopy(readiness or {}),
        "verdicts": {key: result.get(key) for key in VERDICT_KEYS},
        "provider_attempts": attempts,
        "provider_call_counts": deepcopy(result.get("provider_call_counts") or {}),
        "cost_evidence": {
            "currency": "USD",
            "approved_estimate_usd": 0.0,
            "approved_hard_cap_usd": 1.0,
            "provider_usage_metadata": usage,
            "actual_cost_usd": local.get("actual_cost_usd"),
            "actual_cost_inferred": False,
        },
        "narration": _compact_evidence(
            result.get("narration") or {},
            (
                "schema_version",
                "provider",
                "operation",
                "request_hash",
                "provider_request_hash",
                "provider_request_id",
                "voice_id",
                "model_id",
                "normalized_text_hash",
                "audio_path",
                "audio_asset_ref",
                "audio_sha256",
                "audio_size_bytes",
                "audio_duration_ms",
                "provider_call_made",
                "network_submit_count",
                "sdk_retry",
                "actual_cost_usd",
            ),
        ),
        "alignment": _compact_evidence(
            result.get("alignment") or {},
            (
                "schema_version",
                "provider",
                "operation",
                "request_hash",
                "provider_request_hash",
                "provider_response_hash",
                "provider_request_id",
                "audio_asset_ref",
                "audio_sha256",
                "audio_duration_ms",
                "spoken_text_hash",
                "word_count",
                "character_count",
                "verification_status",
                "forced_alignment_ref",
                "forced_alignment_content_hash",
                "provider_call_made",
                "network_submit_count",
                "sdk_retry",
                "actual_cost_usd",
            ),
        ),
        "canonical_timeline": _compact_evidence(
            result.get("canonical_timeline") or {},
            (
                "schema_version",
                "result",
                "timing_authority",
                "canonical_timeline_ref",
                "content_hash",
                "timeline_hash",
                "audio_asset_ref",
                "audio_sha256",
                "duration_ms",
                "scene_count",
                "caption_cue_count",
                "estimated_timing_fallback_used",
            ),
        ),
        "scene_executions": deepcopy(result.get("scene_executions") or []),
        "review_media_candidate": candidate or None,
        "drive_archive": drive or None,
        "review_paths": paths,
        "repair_cycle_count": len(repair_cycles),
        "no_fallback_proof": {
            "provider_substitution_used": False,
            "pexels_to_ai_escalation_used": False,
            "gemini_image_calls": int(
                (result.get("provider_call_counts") or {}).get("google_gemini_image", 0)
            ),
            "google_veo_calls": int(
                (result.get("provider_call_counts") or {}).get("google_veo", 0)
            ),
            "youtube_calls": int(result.get("youtube_calls") or 0),
        },
        "publish_boundary_proof": {
            "destination_status": "PENDING_PLATFORM_ID",
            "upload_ready": False,
            "publish_execution_ready": False,
            "proceed_to_pub1": False,
        },
        "terminal_error": result.get("terminal_error"),
        "exact_next_action": next_action,
    }
    repairs = {
        "schema_version": "mr1-real-production-repair-cycles/v1",
        "generated_at": generated_at,
        "run_id": result.get("run_id"),
        "repair_cycle_count": len(repair_cycles),
        "provider_calls_repeated_for_local_repair": False,
        "repair_cycles": repair_cycles,
    }
    return summary, repairs, _markdown_report(summary)


def _compact_attempt(item: Mapping[str, Any]) -> dict[str, Any]:
    output = item.get("output") if isinstance(item.get("output"), Mapping) else {}
    return {
        **_compact_evidence(
            item,
            (
                "operation_key",
                "provider",
                "operation",
                "scene_id",
                "state",
                "submit_state",
                "attempt_count",
                "attempt_cap",
                "request_hash",
                "idempotency_key",
                "network_submit_started",
                "search_submit_count",
                "download_submit_count",
                "failure",
                "last_pre_submit_failure",
            ),
        ),
        "output_evidence": _compact_evidence(
            output,
            (
                "request_hash",
                "provider_request_hash",
                "provider_response_hash",
                "provider_request_id",
                "audio_sha256",
                "audio_duration_ms",
                "forced_alignment_content_hash",
                "scene_id",
                "route",
                "provider_asset_id",
                "local_path",
                "sha256",
                "size_bytes",
                "provider_call_made",
                "network_submit_count",
                "actual_cost_usd",
            ),
        ),
    }


def _compact_evidence(
    value: Mapping[str, Any], fields: tuple[str, ...]
) -> dict[str, Any]:
    return {key: deepcopy(value[key]) for key in fields if key in value}


def _next_action(result: Mapping[str, Any], paths: Mapping[str, Any]) -> str:
    final = result.get("MR1_FINAL")
    if final == "WAITING_HUMAN_REVIEW":
        return (
            "Operator full-watch exact MP4 "
            f"{paths.get('review_mp4') or '[MISSING]'} rồi trả PASS hoặc "
            "REJECT: <reasons>."
        )
    if final == "PASS":
        return "Thực hiện DestinationBinding closeout; không bắt đầu PUB1."
    if final == "BLOCKED_REQUIRES_NEW_MR1_APPROVAL":
        return "Giữ nguyên evidence và tạo MR1 approval mới; không retry attempt đã consume."
    if final == "BLOCKED_REQUIRES_PACKAGE_REVISION_AND_NEW_APPROVAL":
        return (
            "Tạo package revision và MR1 approval mới; không mutate package đã duyệt."
        )
    return (
        "Sửa lỗi deterministic/pre-submit nhỏ nhất rồi chạy lại cùng command "
        "để resume durable run."
    )


def _markdown_report(summary: Mapping[str, Any]) -> str:
    verdicts = summary["verdicts"]
    authority = summary["exact_authority"]
    paths = summary["review_paths"]
    attempts = summary.get("provider_attempts") or []
    scenes = summary.get("scene_executions") or []
    readiness = summary.get("runtime_readiness") or {}

    stage_rows = "\n".join(
        f"| `{key}` | `{_display(verdicts.get(key))}` |" for key in VERDICT_KEYS
    )
    attempt_rows = (
        "\n".join(
            "| `{provider}` | `{operation}` | `{attempt}` / `{cap}` | `{state}` |".format(
                provider=_display(item.get("provider")),
                operation=_display(item.get("operation_key") or item.get("operation")),
                attempt=_display(item.get("attempt_count")),
                cap=_display(item.get("attempt_cap")),
                state=_display(item.get("state")),
            )
            for item in attempts
        )
        or "| — | — | — | — |"
    )
    scene_rows = (
        "\n".join(
            "| `{scene}` | `{route}` | `{status}` | `{fallback}` |".format(
                scene=_display(item.get("scene_id")),
                route=_display(item.get("route")),
                status=_display(item.get("status")),
                fallback=_display(item.get("fallback_used")),
            )
            for item in scenes
        )
        or "| — | — | — | — |"
    )

    return f"""# MR1 — báo cáo real production

Thời điểm ghi: `{summary["generated_at"]}`. Run: `{_display(summary.get("run_id"))}`.
Trạng thái durable: `{_display(summary.get("current_state"))}`.

## Exact authority và entry

| Binding | Exact value |
|---|---|
| ApprovalDecision | `{authority["approval_id"]}` |
| Approval content hash | `{authority["approval_content_hash"]}` |
| VideoProject | `{authority["project_id"]}` |
| Package ArtifactVersion | `{authority["package_artifact_version_id"]}` |
| Package content hash | `{authority["package_content_hash"]}` |
| ChannelProfileVersion v3 | `{authority["profile_id"]}` |
| Compiled policy snapshot | `{authority["snapshot_id"]}` |
| Runtime readiness | `{_display(readiness.get("result"))}` |

Không resolve `latest`; command chỉ nhận exact authority ở trên. Destination vẫn
`PENDING_PLATFORM_ID`; đây không phải publish authority.

## Provider attempts và cost

| Provider | Operation | Attempt | State |
|---|---|---:|---|
{attempt_rows}

Provider logical calls: `{_display(verdicts.get("MR1_PROVIDER_CALL_COUNT"))}`.
Estimate được duyệt `0.00 USD`, hard cap `1.00 USD`; actual cost chỉ được ghi khi có
evidence, không suy diễn từ subscription/free tier. SDK retry, provider switch,
Pexels-to-AI escalation và external AI-video fallback đều bị cấm.

## Narration, alignment, timeline và assets

| Scene | Exact route | Status | Fallback used |
|---|---|---|---|
{scene_rows}

Required provider execution:
`{_display(verdicts.get("MR1_REQUIRED_PROVIDER_EXECUTION"))}`. ElevenLabs:
`{_display(verdicts.get("MR1_ELEVENLABS"))}`; Forced Alignment:
`{_display(verdicts.get("MR1_FORCED_ALIGNMENT"))}`; CanonicalMediaTimeline:
`{_display(verdicts.get("MR1_CANONICAL_TIMELINE"))}`; asset resolution:
`{_display(verdicts.get("MR1_ASSET_RESOLUTION"))}`. Gemini Image và Google Veo không
thuộc exact plan và có call count bằng 0.

## Render, QC, review và Drive

| Gate | Result |
|---|---|
{stage_rows}

Review MP4: `{_display(paths.get("review_mp4"))}`  
Thumbnail: `{_display(paths.get("thumbnail"))}`  
Captions: `{_display(paths.get("captions"))}`  
Local archive/workspace: `{_display(paths.get("local_archive"))}`  
Drive archive ref: `{_display(paths.get("drive_archive_reference"))}`

TechnicalMediaQC phải dùng actual MP4 bytes. Creative QC không thay thế human
full-watch. Trước human PASS chỉ tồn tại `ReviewMediaCandidate`; không tạo
`FinalMediaRef`.

## No-fallback và publish boundary

`provider_substitution_used=false`, `pexels_to_ai_escalation_used=false`,
`youtube_calls=0`. `UPLOAD_READY=false`, `PUBLISH_EXECUTION_READY=false`,
`PROCEED_TO_PUB1=false`. Drive archive không phải YouTube upload.

## Repair cycles và next action

Repair cycles: `{summary["repair_cycle_count"]}`. Local/render/Drive repair phải
reuse exact provider outputs và archive identity; không consume thêm provider
attempt.

Next action: {summary["exact_next_action"]}
"""


def _display(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value).replace("`", "'")


def _write_reports(
    *,
    result: Mapping[str, Any],
    state: Mapping[str, Any],
    readiness: Mapping[str, Any] | None,
    invocation: str,
) -> None:
    summary, repairs, markdown = _report_payloads(
        result=result,
        state=state,
        readiness=readiness,
        invocation=invocation,
    )
    _write_json_atomic(SUMMARY_PATH, summary)
    _write_json_atomic(REPAIR_CYCLES_PATH, repairs)
    _write_text_atomic(REPORT_PATH, markdown)


def _write_success_reports(
    *,
    result: Mapping[str, Any],
    state: Mapping[str, Any],
    readiness: Mapping[str, Any] | None,
    invocation: str,
    continuation_preview: bool = False,
) -> None:
    is_continuation_result = (
        result.get("schema_version")
        == "mr1.provider-attempt-continuation-review.v1"
    )
    if continuation_preview:
        if not is_continuation_result:
            raise RuntimeError(
                "MR1_CONTINUATION_PREVIEW_RESULT_SCHEMA_INVALID"
            )
        _write_continuation_review_report(
            result=result,
            invocation=invocation,
        )
        return
    if is_continuation_result:
        raise RuntimeError(
            "MR1_CONTINUATION_PREVIEW_MODE_REQUIRED_FOR_REPORT"
        )
    _write_reports(
        result=result,
        state=state,
        readiness=readiness,
        invocation=invocation,
    )


def _write_failure_reports(
    *,
    result: Mapping[str, Any],
    state: Mapping[str, Any],
    readiness: Mapping[str, Any] | None,
    invocation: str,
    continuation_preview: bool,
) -> None:
    if not continuation_preview:
        _write_reports(
            result=result,
            state=state,
            readiness=readiness,
            invocation=invocation,
        )
        return
    error_payload = {
        "schema_version": (
            "mr1.pexels-continuation-review-failure-report.v1"
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "invocation": invocation,
        "provider_calls_made": 0,
        "approval_persisted": False,
        "terminal_error": result.get("terminal_error"),
        "readiness": deepcopy(readiness or {}),
    }
    _write_json_atomic(CONTINUATION_REVIEW_JSON_PATH, error_payload)
    _write_text_atomic(
        CONTINUATION_REVIEW_MARKDOWN_PATH,
        (
            "# MR1 Pexels continuation review\n\n"
            "Preview failed before approval or provider execution.\n\n"
            f"Error: `{_display(result.get('terminal_error'))}`\n"
        ),
    )


def _write_continuation_review_report(
    *,
    result: Mapping[str, Any],
    invocation: str,
) -> None:
    manifest = deepcopy(result.get("review_manifest") or {})
    pending_amendments = deepcopy(
        manifest.get("pending_query_amendments") or {}
    )
    pending_unsubmitted_proofs = {
        scene_id: deepcopy(
            (amendment or {}).get("unsubmitted_attempt_snapshot") or {}
        )
        for scene_id, amendment in sorted(pending_amendments.items())
    }
    primary_threshold = manifest.get("semantic_fit_threshold")
    pending_thresholds = {
        scene_id: (amendment.get("request_invariants") or {}).get(
            "semantic_fit_threshold"
        )
        for scene_id, amendment in sorted(pending_amendments.items())
    }
    threshold_unchanged = all(
        threshold == primary_threshold
        for threshold in pending_thresholds.values()
    )
    payload = {
        "schema_version": "mr1.pexels-continuation-review-report.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "invocation": invocation,
        "run_id": result.get("run_id"),
        "operation_key": result.get("operation_key"),
        "approval_persisted": result.get("approval_persisted"),
        "provider_calls_made": result.get("provider_calls_made"),
        "exact_refs": {
            "run_artifact_version_id": result.get(
                "run_artifact_version_id"
            ),
            "review_manifest_artifact_version_id": result.get(
                "review_manifest_artifact_version_id"
            ),
            "review_manifest_content_hash": result.get(
                "review_manifest_content_hash"
            ),
            "operator_review_task_id": result.get(
                "operator_review_task_id"
            ),
            "package_artifact_version_id": manifest.get(
                "package_artifact_version_id"
            ),
            "package_content_hash": manifest.get("package_content_hash"),
        },
        "consumed_attempt_proof": deepcopy(
            manifest.get("prior_consumed_attempt") or {}
        ),
        "pending_unsubmitted_attempt_proofs": pending_unsubmitted_proofs,
        "superseded_review_tasks": deepcopy(
            result.get("superseded_review_tasks") or []
        ),
        "query_review": {
            "primary": {
                "scene_id": manifest.get("scene_id"),
                "package_semantic_intent": manifest.get(
                    "package_semantic_intent"
                ),
                "approved_stock_search_intent": manifest.get(
                    "approved_stock_search_intent"
                ),
                "base_query_evidence": deepcopy(
                    manifest.get("base_query_evidence") or {}
                ),
                "approved_query_authority": deepcopy(
                    manifest.get("approved_query_authority") or {}
                ),
                "query_material_diff": deepcopy(
                    manifest.get("query_material_diff") or {}
                ),
                "stock_search_intent_derivation": deepcopy(
                    manifest.get("stock_search_intent_derivation") or {}
                ),
                "query_intent_coverage_evidence": deepcopy(
                    manifest.get("query_intent_coverage_evidence") or {}
                ),
            },
            "pending_amendments": {
                scene_id: {
                    "operation_key": amendment.get("operation_key"),
                    "package_semantic_intent": amendment.get(
                        "package_semantic_intent"
                    ),
                    "approved_stock_search_intent": amendment.get(
                        "approved_stock_search_intent"
                    ),
                    "base_query_evidence": deepcopy(
                        amendment.get("base_query_evidence") or {}
                    ),
                    "approved_query_authority": deepcopy(
                        amendment.get("approved_query_authority") or {}
                    ),
                    "query_material_diff": deepcopy(
                        amendment.get("query_material_diff") or {}
                    ),
                    "stock_search_intent_derivation": deepcopy(
                        amendment.get(
                            "stock_search_intent_derivation"
                        )
                        or {}
                    ),
                    "query_intent_coverage_evidence": deepcopy(
                        amendment.get(
                            "query_intent_coverage_evidence"
                        )
                        or {}
                    ),
                }
                for scene_id, amendment in sorted(
                    pending_amendments.items()
                )
            },
        },
        "package_semantic_intent_unchanged": bool(
            manifest.get("package_semantic_intent")
            and manifest.get("package_semantic_intent")
            == (
                manifest.get("approved_query_authority") or {}
            ).get("package_semantic_intent")
            == (
                manifest.get("stock_search_intent_derivation") or {}
            ).get("package_semantic_intent")
        ),
        "semantic_fit_threshold": {
            "approved": primary_threshold,
            "pending_scene_values": pending_thresholds,
            "unchanged": threshold_unchanged,
        },
        "required_operator_authority": {
            "decided_by_user_id": result.get(
                "required_decided_by_user_id"
            ),
            "operator_review_task_id": result.get(
                "operator_review_task_id"
            ),
            "operator_decision_text": result.get(
                "required_operator_decision_text"
            ),
        },
        "safety_boundary": {
            "automatic_retry_allowed": manifest.get(
                "automatic_retry_allowed"
            ),
            "provider_substitution_allowed": manifest.get(
                "provider_substitution_allowed"
            ),
            "youtube_upload_authorized": manifest.get(
                "youtube_upload_authorized"
            ),
            "publish_execution_authorized": manifest.get(
                "publish_execution_authorized"
            ),
        },
    }
    pending_rows = "\n".join(
        (
            f"| `{scene_id}` | "
            f"`{_display(proof.get('artifact_version_id'))}` | "
            f"`{_display(proof.get('content_hash'))}` | "
            f"`{_display(proof.get('state'))}` / "
            f"`{_display(proof.get('submit_state'))}` |"
        )
        for scene_id, proof in pending_unsubmitted_proofs.items()
    ) or "| — | — | — | — |"
    consumed = payload["consumed_attempt_proof"]
    markdown = f"""# MR1 Pexels continuation review

Run `{_display(payload["run_id"])}`; operation
`{_display(payload["operation_key"])}`. Preview made
`{_display(payload["provider_calls_made"])}` provider calls and persisted no
approval.

Manifest ArtifactVersion:
`{_display(payload["exact_refs"]["review_manifest_artifact_version_id"])}`  
Manifest SHA-256:
`{_display(payload["exact_refs"]["review_manifest_content_hash"])}`  
Assigned review task:
`{_display(payload["exact_refs"]["operator_review_task_id"])}`.

Consumed attempt proof: ArtifactVersion
`{_display(consumed.get("artifact_version_id"))}`,
hash `{_display(consumed.get("content_hash"))}`, state
`{_display(consumed.get("state"))}` /
`{_display(consumed.get("submit_state"))}`, search submits
`{_display(consumed.get("search_submit_count"))}`.

| Pending scene | ArtifactVersion | Content hash | State |
|---|---|---|---|
{pending_rows}

Semantic-fit threshold remains
`{_display(primary_threshold)}`:
`{_display(threshold_unchanged)}`.

The package scene semantic intent remains byte-exact:
`{_display(payload["package_semantic_intent_unchanged"])}`. The bounded stock
query changes materially:
`{_display((payload["query_review"]["primary"].get("query_material_diff") or {}).get("materially_different"))}`.
Consumed candidate-ranking detail:
`{_display((payload["query_review"]["primary"].get("base_query_evidence") or {}).get("detailed_candidate_ranking_evidence_state"))}`.

Required operator: `{_display(result.get("required_decided_by_user_id"))}`  
Required exact text:
`{_display(result.get("required_operator_decision_text"))}`
"""
    _write_json_atomic(CONTINUATION_REVIEW_JSON_PATH, payload)
    _write_text_atomic(CONTINUATION_REVIEW_MARKDOWN_PATH, markdown)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    _write_text_atomic(path, text)


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _verdict_block(result: Mapping[str, Any]) -> str:
    return "\n".join(f"{key}={_display(result.get(key))}" for key in VERDICT_KEYS)


def main() -> int:
    args = _parse_args()
    _configure_exact_authority(args)
    closeout_command = _explicit_closeout_command(args)
    if args.closeout and (
        args.readiness_only
        or args.prepare_extra_pexels_review
        or args.approve_extra_pexels_attempt
        or args.approve_extra_pexels_sc04_attempt
    ):
        raise SystemExit(
            "--closeout cannot be combined with readiness-only or provider "
            "attempt authorization"
        )
    if args.authority_mode == "sc04" and args.approve_extra_pexels_sc04_attempt:
        raise SystemExit(
            "SC-04 revision uses NATIVE_MOTION_GRAPHIC; an extra Pexels SC-04 "
            "attempt is outside the approved authority"
        )
    os.chdir(ROOT)
    settings = Settings()
    workspace_root = DEFAULT_WORKSPACE_ROOT.resolve()
    readiness: dict[str, Any] | None = None
    result: dict[str, Any] = {}
    state: dict[str, Any] = {}
    invocation = (
        f"closeout:{args.resume_run_id}:{args.human_decision}"
        if closeout_command is not None
        else f"continuation-review:{args.resume_run_id}"
        if args.prepare_extra_pexels_review
        else f"resume:{args.resume_run_id}"
        if args.resume_run_id
        else "start-idempotent"
    )
    continuation_preview = bool(args.prepare_extra_pexels_review)

    try:
        with session_scope() as session:
            closeout_drive_gateway = None
            gateways = None
            continuation = None
            if closeout_command is not None:
                readiness = _closeout_runtime_readiness(
                    settings=settings,
                    session=session,
                    workspace_root=workspace_root,
                    command=closeout_command,
                )
                if readiness["result"] != "PASS":
                    raise RuntimeError(
                        "MR1_CLOSEOUT_RUNTIME_READINESS_FAILED:"
                        + ",".join(readiness["failed_checks"])
                    )
                if closeout_command.decision == "PASS":
                    from app.services.mr1_provider_gateways import (
                        MR1DriveGatewayAdapter,
                    )

                    closeout_drive_service = (
                        MR1DriveArchiveService.from_existing_configuration(
                            session=session,
                            settings=settings,
                            source_root=workspace_root,
                            state_root=workspace_root / ".drive-state",
                        )
                    )
                    closeout_drive_gateway = MR1DriveGatewayAdapter(
                        service=closeout_drive_service,
                        settings=settings,
                        workspace_root=workspace_root,
                    )
                    closeout_gateway_readiness = closeout_drive_gateway.preflight()
                    readiness["drive_gateway_preflight"] = closeout_gateway_readiness
                    if closeout_gateway_readiness.get("result") != "PASS":
                        raise RuntimeError(
                            "MR1_CLOSEOUT_DRIVE_PREFLIGHT_FAILED:"
                            + ",".join(
                                closeout_gateway_readiness.get("failed_checks") or []
                            )
                        )
            elif args.prepare_extra_pexels_review:
                readiness = {
                    "schema_version": (
                        "mr1.provider-continuation-review-readiness.v1"
                    ),
                    "mode": "PROVIDER_FREE_REVIEW_MANIFEST_ONLY",
                    "result": "PASS",
                    "provider_calls": 0,
                    "media_provider_preflights": 0,
                    "render_calls": 0,
                    "drive_mutation_calls": 0,
                    "youtube_calls": 0,
                }
            else:
                readiness = _runtime_readiness(
                    settings=settings,
                    session=session,
                    workspace_root=workspace_root,
                )
                if readiness["result"] != "PASS":
                    if args.readiness_only:
                        print(json.dumps(readiness, indent=2, sort_keys=True))
                        print("MR1_RUNTIME_READINESS=FAIL")
                        return 2
                    raise RuntimeError(
                        "MR1_RUNTIME_READINESS_FAILED:"
                        + ",".join(readiness["failed_checks"])
                    )

                # Imports stay after the no-generation readiness gate. Adapter
                # construction is side-effect free; mutations stay behind the
                # service's durable before-submit callbacks.
                from app.services.mr1_local_production import (
                    MR1LocalProductionContinuation,
                )
                from app.services.mr1_provider_gateways import (
                    build_mr1_production_gateways,
                )

                gateways = build_mr1_production_gateways(
                    session=session,
                    settings=settings,
                    workspace_root=workspace_root,
                )
                gateway_readiness = _gateway_preflights(gateways)
                readiness["gateway_preflights"] = gateway_readiness
                if gateway_readiness["result"] != "PASS":
                    readiness["result"] = "FAIL"
                    readiness.setdefault("failed_checks", []).extend(
                        f"gateway:{name}"
                        for name in gateway_readiness["failed_gateways"]
                    )
                    if args.readiness_only:
                        print(json.dumps(readiness, indent=2, sort_keys=True))
                        print("MR1_RUNTIME_READINESS=FAIL")
                        return 2
                    raise RuntimeError(
                        "MR1_GATEWAY_PREFLIGHT_FAILED:"
                        + ",".join(gateway_readiness["failed_gateways"])
                    )
                try:
                    continuation = MR1LocalProductionContinuation(
                        settings=settings,
                        repository_root=ROOT,
                        workspace_root=workspace_root,
                    )
                except Exception as exc:
                    if not args.readiness_only:
                        raise
                    readiness["result"] = "FAIL"
                    readiness.setdefault("failed_checks", []).append(
                        "local_continuation"
                    )
                    readiness["local_continuation"] = {
                        "result": "FAIL",
                        "error": _redacted_error(exc, settings),
                        "provider_calls": 0,
                        "render_calls": 0,
                        "drive_archive_calls": 0,
                        "youtube_calls": 0,
                    }
                    print(json.dumps(readiness, indent=2, sort_keys=True))
                    print("MR1_RUNTIME_READINESS=FAIL")
                    return 2
            if args.readiness_only:
                readiness["local_continuation"] = {
                    "result": "PASS",
                    "ffmpeg": "READY",
                    "ffprobe": "READY",
                    "provider_calls": 0,
                    "render_calls": 0,
                    "drive_archive_calls": 0,
                    "youtube_calls": 0,
                }
                print(json.dumps(readiness, indent=2, sort_keys=True))
                print("MR1_RUNTIME_READINESS=PASS")
                return 0
            service = MR1RealProductionService(
                session,
                workspace_root,
                local_continuation=continuation,
                commit_boundaries=True,
                expected_profile_id=PROFILE_ID,
                expected_snapshot_id=SNAPSHOT_ID,
                settings=settings,
            )
            if args.resume_run_id is not None:
                _run_artifact, resume_version = service._require_run(
                    args.resume_run_id,
                    lock=False,
                )
                resume_state = resume_version.content or {}
                exact_resume = {
                    "approval_id": str(APPROVAL_ID),
                    "approval_content_hash": APPROVAL_CONTENT_HASH,
                    "project_id": str(PROJECT_ID),
                    "package_artifact_version_id": str(PACKAGE_ARTIFACT_VERSION_ID),
                    "package_content_hash": PACKAGE_CONTENT_HASH,
                }
                mismatches = sorted(
                    key
                    for key, expected in exact_resume.items()
                    if resume_state.get(key) != expected
                )
                if mismatches:
                    raise ValueError(
                        "MR1_RESUME_RUN_AUTHORITY_MISMATCH:" + ",".join(mismatches)
                    )
            approve_extra_pexels = bool(
                args.approve_extra_pexels_attempt
                or args.approve_extra_pexels_sc04_attempt
            )
            prepare_extra_pexels = bool(args.prepare_extra_pexels_review)
            prepared_continuation_review = None
            if prepare_extra_pexels and approve_extra_pexels:
                raise ValueError(
                    "MR1_PEXELS_CONTINUATION_PREPARE_AND_APPROVE_CONFLICT"
                )
            if prepare_extra_pexels or approve_extra_pexels:
                if args.resume_run_id is None:
                    raise ValueError("MR1_PEXELS_CONTINUATION_REQUIRES_RESUME_RUN_ID")
                operation_key = (
                    "pexels:SC-04"
                    if args.approve_extra_pexels_sc04_attempt
                    else args.pexels_operation_key
                )
                if (
                    operation_key is None
                    or not args.approved_pexels_stock_search_intent
                ):
                    raise ValueError(
                        "MR1_PEXELS_CONTINUATION_EXACT_OPERATOR_INPUT_REQUIRED"
                    )
                review_command = MR1ProviderAttemptContinuationReviewCommand(
                    run_id=args.resume_run_id,
                    operation_key=operation_key,
                    approved_stock_search_intent=(
                        args.approved_pexels_stock_search_intent
                    ),
                    approved_pending_scene_stock_search_intents=(
                        {
                            "SC-09": (
                                args.approved_pexels_sc09_stock_search_intent
                            )
                        }
                        if args.approved_pexels_sc09_stock_search_intent
                        else {}
                    ),
                )
                if prepare_extra_pexels:
                    prepared_continuation_review = (
                        service.prepare_provider_attempt_continuation_review(
                            review_command
                        )
                    )
                else:
                    if (
                        not args.operator_decision_text
                        or args.operator_review_manifest_artifact_version_id
                        is None
                        or not args.operator_review_manifest_content_hash
                        or args.operator_review_task_id is None
                        or args.continuation_decided_by_user_id is None
                    ):
                        raise ValueError(
                            "MR1_PEXELS_CONTINUATION_EXACT_REVIEW_BINDING_REQUIRED"
                        )
                    service.approve_provider_attempt_continuation(
                        MR1ProviderAttemptContinuationCommand(
                            **review_command.model_dump(mode="python"),
                            operator_review_manifest_artifact_version_id=(
                                args.operator_review_manifest_artifact_version_id
                            ),
                            operator_review_manifest_content_hash=(
                                args.operator_review_manifest_content_hash
                            ),
                            operator_review_task_id=(
                                args.operator_review_task_id
                            ),
                            decided_by_user_id=(
                                args.continuation_decided_by_user_id
                            ),
                            operator_decision_text=args.operator_decision_text,
                        )
                    )
            if closeout_command is not None:
                result = service.closeout(
                    closeout_command,
                    drive_gateway=closeout_drive_gateway,
                )
            elif prepared_continuation_review is not None:
                result = prepared_continuation_review
            elif args.resume_run_id is not None:
                if gateways is None:
                    raise RuntimeError("MR1_PRODUCTION_GATEWAYS_MISSING")
                result = service.resume(
                    run_id=args.resume_run_id,
                    gateways=gateways,
                )
            else:
                if gateways is None:
                    raise RuntimeError("MR1_PRODUCTION_GATEWAYS_MISSING")
                result = service.start(
                    MR1StartCommand(
                        approval_id=APPROVAL_ID,
                        approval_content_hash=APPROVAL_CONTENT_HASH,
                        project_id=PROJECT_ID,
                        package_artifact_version_id=PACKAGE_ARTIFACT_VERSION_ID,
                    ),
                    gateways=gateways,
                )
            state, _version = _load_run_state(session, result)
    except Exception as exc:
        error = _redacted_error(exc, settings)
        try:
            recovered, recovered_state = _recover_persisted_result(workspace_root)
        except Exception:
            recovered, recovered_state = {}, {}
        if recovered:
            result, state = recovered, recovered_state
            result["terminal_error"] = error
        else:
            result = _failure_result(error, readiness)
        _write_failure_reports(
            result=result,
            state=state,
            readiness=readiness,
            invocation=invocation,
            continuation_preview=continuation_preview,
        )
        print(_verdict_block(result))
        print(f"MR1_TERMINAL_ERROR={error}")
        return 2

    _write_success_reports(
        result=result,
        state=state,
        readiness=readiness,
        invocation=invocation,
        continuation_preview=continuation_preview,
    )
    if (
        result.get("schema_version")
        == "mr1.provider-attempt-continuation-review.v1"
    ):
        print(
            "MR1_CONTINUATION_REVIEW_MANIFEST_ARTIFACT_VERSION_ID="
            + str(result["review_manifest_artifact_version_id"])
        )
        print(
            "MR1_CONTINUATION_REVIEW_MANIFEST_CONTENT_HASH="
            + str(result["review_manifest_content_hash"])
        )
        print(
            "MR1_CONTINUATION_REVIEW_TASK_ID="
            + str(result["operator_review_task_id"])
        )
        print(
            "MR1_CONTINUATION_REQUIRED_DECIDED_BY_USER_ID="
            + str(result["required_decided_by_user_id"])
        )
        print(
            "MR1_CONTINUATION_REQUIRED_OPERATOR_TEXT="
            + str(result["required_operator_decision_text"])
        )
        print("MR1_CONTINUATION_PROVIDER_CALLS_MADE=0")
        return 0
    print(_verdict_block(result))
    candidate = result.get("review_media_candidate") or {}
    if result.get("MR1_FINAL") == "WAITING_HUMAN_REVIEW":
        local = state.get("local_result") or {}
        creative = local.get("creative_media_qc") or {}
        actual_cost = local.get("actual_cost_usd")
        if actual_cost is None:
            actual_cost = (result.get("narration") or {}).get("actual_cost_usd")
        print(f"MR1_REVIEW_MP4={_display(candidate.get('output_file_ref'))}")
        print(f"MR1_THUMBNAIL={_display(candidate.get('thumbnail_path'))}")
        print(f"MR1_CAPTIONS={_display(candidate.get('captions_path'))}")
        print(
            "MR1_LOCAL_ARCHIVE="
            + _display(
                local.get("archive_path")
                or local.get("local_archive_path")
                or result.get("workspace")
            )
        )
        print(
            "MR1_DRIVE_ARCHIVE_REF="
            + _display(
                (result.get("drive_archive") or {}).get("drive_folder_id")
                or result.get("archive_identity")
            )
        )
        print("MR1_ESTIMATED_COST_USD=0.00")
        print(f"MR1_ACTUAL_COST_USD={_display(actual_cost)}")
        print(
            "MR1_PROVIDER_CALL_COUNTS="
            + json.dumps(
                result.get("provider_call_counts") or {},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        print(
            "MR1_REVIEW_REQUIRED_ITEMS="
            + json.dumps(
                creative.get("review_required_items")
                or creative.get("reason_codes")
                or [],
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0 if result.get("MR1_FINAL") in {"WAITING_HUMAN_REVIEW", "PASS"} else 2


def _redacted_error(exc: Exception, settings: Settings) -> str:
    value = f"{type(exc).__name__}:{str(exc)[:800]}"
    secrets = (
        settings.elevenlabs_api_key,
        settings.pexels_api_key,
        settings.google_drive_oauth_client_secret,
    )
    for secret in secrets:
        if secret is None:
            continue
        raw = (
            secret.get_secret_value()
            if hasattr(secret, "get_secret_value")
            else str(secret)
        )
        if raw:
            value = value.replace(raw, "[REDACTED]")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
