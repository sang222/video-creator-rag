from __future__ import annotations

import argparse
import json

from app.contracts.img_canary_v3_closeout import IMG_CANARY_V3_CLOSEOUT_RUN_ID
from app.core.config import get_settings
from app.db.session import session_scope
from app.services.img_canary_drive import IMGCanaryDriveArchive
from app.services.img_canary_v3_closeout import (
    IMG_CANARY_V3_CLOSEOUT_CONFIRMATION_TOKEN,
    IMG_CANARY_V3_CLOSEOUT_MANIFEST,
    IMG_CANARY_V3_HUMAN_REVIEW_RECEIPT,
    IMG_CANARY_V3_LOCAL_DRIVE_JOURNAL,
    IMG_CANARY_V3_LOCAL_DRIVE_RECEIPT,
    IMGCanaryV3DriveCloseout,
)
from app.services.pa1r import DrivePA1RArchive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Close the fixed IMG-CANARY-v3 Drive gate")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--confirmation-token", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.run_id != IMG_CANARY_V3_CLOSEOUT_RUN_ID:
        raise SystemExit("IMG_CANARY_V3_CLOSEOUT_RUN_ID_NOT_AUTHORIZED")
    if args.confirmation_token != IMG_CANARY_V3_CLOSEOUT_CONFIRMATION_TOKEN:
        raise SystemExit("IMG_CANARY_V3_CLOSEOUT_CONFIRMATION_INVALID")

    closeout = IMGCanaryV3DriveCloseout()
    closeout.prepare()
    settings = get_settings()
    access_token: str | None = None
    try:
        with session_scope() as session:
            pa1r = DrivePA1RArchive(session, settings)
            drive = IMGCanaryDriveArchive.from_pa1r_archive(pa1r)
            access_token = pa1r.access_token()
            receipt = closeout.export_and_verify(
                drive=drive,
                confirmation_token=args.confirmation_token,
                access_token=access_token,
            )
    finally:
        access_token = None

    print(
        json.dumps(
            {
                "run_id": IMG_CANARY_V3_CLOSEOUT_RUN_ID,
                "human_review": "PASS",
                "human_review_authority": "OPERATOR",
                "human_review_receipt_path": str(IMG_CANARY_V3_HUMAN_REVIEW_RECEIPT),
                "closeout_manifest_path": str(IMG_CANARY_V3_CLOSEOUT_MANIFEST),
                "drive_export": "PASS",
                "archive_verified": receipt.archive_state == "VERIFIED",
                "drive_item_count": len(receipt.files),
                "drive_folder_id": receipt.drive_folder_id,
                "drive_receipt_hash": receipt.receipt_hash,
                "drive_journal_path": str(IMG_CANARY_V3_LOCAL_DRIVE_JOURNAL),
                "drive_receipt_path": str(IMG_CANARY_V3_LOCAL_DRIVE_RECEIPT),
                "provider_attempts_total": 1,
                "gemini_calls_during_closeout": 0,
                "proceed_to_ch1_flex_v2": receipt.archive_state == "VERIFIED",
                "mr1_execution": "ON_HOLD",
                "proceed_to_mr1": False,
                "production_eligible": False,
                "not_publishable": True,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
