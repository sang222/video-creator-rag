from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.db.session import session_scope
from app.services.img_canary_drive import IMGCanaryDriveArchive
from app.services.img_canary_runner import IMGCanaryControlledRunner
from app.services.pa1r import DrivePA1RArchive


REPO_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Controlled one-shot Google Gemini Image canary runner",
    )
    parser.add_argument("--run-suffix")
    parser.add_argument("--vqc1-final-pass", action="store_true")
    approval_group = parser.add_mutually_exclusive_group()
    approval_group.add_argument(
        "--fresh-v2-approval",
        action="store_true",
        help="Use the fixed one-shot IMG-CANARY-v2 approval attached on 2026-07-18.",
    )
    approval_group.add_argument(
        "--fresh-v3-approval",
        action="store_true",
        help=(
            "Use the fixed one-shot IMG-CANARY-v3 operator approval for the "
            "corrected serialized response_format (no delivery field)."
        ),
    )
    parser.add_argument(
        "--resume-run-id",
        help="Resume deterministic work for an existing run; never creates a new provider request.",
    )
    parser.add_argument(
        "--credential-rotation-ref",
        default="rotation://img-canary/pending/operator-rotation",
        help="Safe rotation/change-ticket reference; fingerprint change is verified locally.",
    )
    parser.add_argument(
        "--record-current-credential-compromised",
        action="store_true",
        help="One-time incident action: persist only the configured key fingerprint as compromised.",
    )
    parser.add_argument(
        "--open-scoped-execution-switches",
        action="store_true",
        help="Open execution switches only in this process; repository defaults remain closed.",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--local-only-no-drive-export",
        action="store_true",
        help="After the paid response, run VQC/render/archive locally and make no Drive upload call.",
    )
    parser.add_argument("--execution-token", default="")
    return parser


def _repository_vqc1_final_passed() -> bool:
    try:
        payload = json.loads(
            (REPO_ROOT / "reports" / "vqc1_summary.json").read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return False
    return payload.get("verdicts", {}).get("VQC1_FINAL") == "PASS"


def _repair_history_name(approval_version: str) -> str:
    return {
        "v1": "img_canary_repair_cycles.json",
        "v2": "img_canary_v2_repair_cycles.json",
        "v3": "img_canary_v3_repair_cycles.json",
    }[approval_version]


def main() -> int:
    args = _parser().parse_args()
    if args.resume_run_id and args.run_suffix:
        raise SystemExit("--resume-run-id cannot be combined with --run-suffix")
    if args.local_only_no_drive_export and not args.execute:
        raise SystemExit("--local-only-no-drive-export requires --execute")
    if (
        args.resume_run_id
        and args.fresh_v2_approval is False
        and args.fresh_v3_approval is False
    ):
        args.fresh_v2_approval = args.resume_run_id.startswith("img-canary-v2-")
        args.fresh_v3_approval = args.resume_run_id.startswith("img-canary-v3-")
    fresh_versioned_approval = bool(
        args.fresh_v2_approval or args.fresh_v3_approval
    )
    approval_version = (
        "v3" if args.fresh_v3_approval else "v2" if args.fresh_v2_approval else "v1"
    )
    settings = get_settings()
    if args.open_scoped_execution_switches:
        settings = settings.model_copy(
            update={
                "gemini_image_real_generation_enabled": True,
                "img1_fixture_only": False,
                "provider_real_execution_enabled": True,
                "provider_production_execution_enabled": True,
                "media_provider_calls_disabled": False,
            }
        )
    now = datetime.now(UTC)
    runner = IMGCanaryControlledRunner(
        repo_root=REPO_ROOT,
        scoped_settings=settings,
        approval_version=approval_version,
    )
    if args.record_current_credential_compromised:
        runner.record_current_credential_compromised(now=now)
    planned = (
        runner.load_planned_run(run_id=args.resume_run_id)
        if args.resume_run_id
        else runner.plan(now=now, run_suffix=args.run_suffix)
    )
    drive_access_token: str | None = None
    readiness_archive: IMGCanaryDriveArchive | None = None
    drive_readiness = None
    if fresh_versioned_approval:
        with session_scope() as session:
            pa1r_archive = DrivePA1RArchive(session, settings)
            readiness_archive = IMGCanaryDriveArchive.from_pa1r_archive(
                pa1r_archive
            )
            drive_access_token = pa1r_archive.access_token()
            drive_readiness = runner.verify_drive_readiness(
                drive_archive=readiness_archive,
                access_token=drive_access_token,
                run_id=planned.bundle.run_identity.run_id,
                now=now,
            )
        drive_access_token = None
    planning_preflight_path = planned.workspace_root / "manifests" / "preflight.json"
    if args.resume_run_id and planning_preflight_path.exists():
        prepared = runner.load_prepared_run(run_id=args.resume_run_id)
    else:
        prepared = runner.preflight(
            planned=planned,
            vqc1_final_passed=(
                _repository_vqc1_final_passed()
                if fresh_versioned_approval
                else args.vqc1_final_pass
            ),
            credential_rotation_ref=args.credential_rotation_ref,
            drive_readiness_evidence=drive_readiness,
            now=now,
        )
    output: dict[str, object] = {
        "run_id": planned.bundle.run_identity.run_id,
        "workspace_root": str(planned.workspace_root),
        "preflight": prepared.preflight.status,
        "blocker_reason_codes": prepared.preflight.blocker_reason_codes,
        "estimated_cost_usd": str(planned.bundle.cost.estimated_amount),
        "hard_cap_usd": "0.15",
        "provider_attempts": planned.planned_attempt.attempts_consumed,
        "external_fallback_used": False,
    }
    provider_succeeded = False
    if args.execute and prepared.preflight.status == "PASS":
        try:
            # Resolve the existing PA1R credential and prove that its OAuth token
            # can read the configured root before the billable Gemini submission.
            # The token remains process-local and is never placed in an artifact.
            with session_scope() as session:
                pa1r_archive = DrivePA1RArchive(session, settings)
                readiness_archive = IMGCanaryDriveArchive.from_pa1r_archive(
                    pa1r_archive
                )
                drive_access_token = pa1r_archive.access_token()
                runner.verify_drive_readiness(
                    drive_archive=readiness_archive,
                    access_token=drive_access_token,
                    run_id=(
                        planned.bundle.run_identity.run_id
                        if fresh_versioned_approval
                        else None
                    ),
                )
            # Do not retain an OAuth token while Gemini is executing. A fresh
            # valid token is resolved only if the paid response succeeded.
            drive_access_token = None

            result = runner.execute_paid_once(
                prepared=prepared,
                explicit_execution_token=args.execution_token,
            )
            provider_succeeded = result.operation_receipt.normalized_status == "SUCCEEDED"
            output.update(
                {
                    "provider_execution": result.operation_receipt.normalized_status,
                    "provider_attempts": result.attempt_ledger.attempts_consumed,
                    "original_image_path": (
                        str(result.original_image_path)
                        if result.original_image_path is not None
                        else None
                    ),
                }
            )
            if provider_succeeded and args.local_only_no_drive_export:
                repair_history_name = _repair_history_name(approval_version)
                repair_history = json.loads(
                    (REPO_ROOT / "reports" / repair_history_name).read_text(
                        encoding="utf-8"
                    )
                )
                repair_cycles = repair_history.get("cycles")
                if not isinstance(repair_cycles, list):
                    raise RuntimeError("IMG_CANARY_REPAIR_HISTORY_INVALID")
                local_review = runner.build_local_review(
                    paid_execution=result,
                    now=datetime.now(UTC),
                )
                reports = runner.write_run_local_report_snapshots(
                    local_review=local_review,
                    repair_cycles=repair_cycles,
                    now=datetime.now(UTC),
                )
                archive = runner.build_archive_manifest(
                    local_review=local_review,
                    vqc_report_markdown_path=reports.vqc_report_markdown_path,
                    vqc_summary_path=reports.vqc_summary_path,
                    canary_report_markdown_path=reports.canary_report_markdown_path,
                    canary_summary_path=reports.canary_summary_path,
                    repair_cycles_path=reports.repair_cycles_path,
                )
                output.update(
                    {
                        "normalized_image_path": str(local_review.normalized_image_path),
                        "review_mp4_path": str(local_review.review_mp4_path),
                        "technical_image_qc": local_review.vqc_report.technical_status,
                        "creative_review": local_review.vqc_report.creative_review_state,
                        "native_render": "PASS",
                        "archive_manifest_path": str(archive.manifest_path),
                        "drive_archive": "SKIPPED_PLATFORM_EXPORT_POLICY",
                        "archive_verified": False,
                        "actual_cost_usd": (
                            str(result.operation_receipt.actual_cost)
                            if result.operation_receipt.actual_cost is not None
                            else None
                        ),
                        "human_review": "NOT_OPENED_DRIVE_VERIFICATION_REQUIRED",
                        "img_canary_final": "BLOCKED_DRIVE_EXPORT_POLICY",
                        "production_eligible": False,
                        "not_publishable": True,
                        "proceed_to_ch1_flex_v2": False,
                    }
                )
            elif provider_succeeded:
                with session_scope() as session:
                    pa1r_archive = DrivePA1RArchive(session, settings)
                    drive_archive = IMGCanaryDriveArchive.from_pa1r_archive(
                        pa1r_archive
                    )
                    drive_access_token = pa1r_archive.access_token()
                    runner.verify_drive_readiness(
                        drive_archive=drive_archive,
                        access_token=drive_access_token,
                        run_id=(
                            planned.bundle.run_identity.run_id
                            if fresh_versioned_approval
                            else None
                        ),
                    )
                repair_history_name = _repair_history_name(approval_version)
                repair_history = json.loads(
                    (REPO_ROOT / "reports" / repair_history_name).read_text(
                        encoding="utf-8"
                    )
                )
                repair_cycles = repair_history.get("cycles")
                if not isinstance(repair_cycles, list):
                    raise RuntimeError("IMG_CANARY_REPAIR_HISTORY_INVALID")
                completion = runner.complete_post_paid_pipeline(
                    paid_execution=result,
                    drive_archive=drive_archive,
                    access_token=drive_access_token,
                    repair_cycles=repair_cycles,
                    now=datetime.now(UTC),
                )
                output.update(
                    {
                        "normalized_image_path": str(
                            completion.local_review.normalized_image_path
                        ),
                        "review_mp4_path": str(completion.local_review.review_mp4_path),
                        "technical_image_qc": completion.local_review.vqc_report.technical_status,
                        "creative_review": completion.local_review.vqc_report.creative_review_state,
                        "native_render": "PASS",
                        "archive_manifest_path": str(completion.archive.manifest_path),
                        "drive_archive": completion.drive_archive_receipt.archive_state,
                        "drive_archive_receipt_path": str(
                            completion.drive_archive_receipt_path
                        ),
                        "drive_archive_receipt_hash": (
                            completion.drive_archive_receipt.receipt_hash
                        ),
                        "archive_verified": True,
                        "actual_cost_usd": (
                            str(result.operation_receipt.actual_cost)
                            if result.operation_receipt.actual_cost is not None
                            else None
                        ),
                        "human_review_packet_path": str(
                            completion.human_review_packet_path
                        ),
                        "human_review_checklist": (
                            completion.human_review_packet.checklist
                        ),
                        "human_review": completion.human_review_packet.review_state,
                        "img_canary_final": "WAITING_HUMAN_REVIEW",
                        "production_eligible": False,
                        "not_publishable": True,
                        "proceed_to_ch1_flex_v2": False,
                    }
                )
        finally:
            # Drop the only CLI-held token reference after upload/verification.
            drive_access_token = None
    elif args.execute:
        output["provider_execution"] = "BLOCKED_PRE_SUBMIT"
    print(json.dumps(output, indent=2, sort_keys=True))
    if prepared.preflight.status != "PASS":
        return 2
    if args.execute and not provider_succeeded:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
