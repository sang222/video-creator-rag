from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.contracts.pkg1_sc04_revision_closeout import (  # noqa: E402
    PKG1SC04RevisionApprovalCommand,
)
from app.db.session import session_scope  # noqa: E402
from app.services.config_registry import ConfigRegistryService  # noqa: E402
from app.services.pkg1_sc04_revision_closeout import (  # noqa: E402
    PKG1SC04RevisionCloseoutService,
)


SUMMARY_PATH = ROOT / "reports/pkg1_sc04_human_closeout_summary.json"
REPORT_PATH = ROOT / "reports/pkg1_sc04_human_closeout_report.md"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Persist one exact operator PASS for an immutable PKG1 SC-04 "
            "revision package. This does not authorize or start execution."
        )
    )
    parser.add_argument("--project-id", type=uuid.UUID, required=True)
    parser.add_argument("--review-task-id", type=uuid.UUID, required=True)
    parser.add_argument("--package-artifact-version-id", type=uuid.UUID, required=True)
    parser.add_argument("--package-hash", required=True)
    parser.add_argument("--revision-id", type=uuid.UUID, required=True)
    parser.add_argument("--revision-version", type=int, choices=[3], required=True)
    parser.add_argument("--revision-hash", required=True)
    parser.add_argument("--actor-user-id", type=uuid.UUID, required=True)
    parser.add_argument("--decision", choices=["PASS"], required=True)
    parser.add_argument("--operator-decision-text", choices=["PASS"], required=True)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--review-notes")
    return parser


def _report(result: dict[str, Any]) -> str:
    return f"""# PKG1 SC-04 Human Closeout

- Human review: `{result["PKG1_SC04_REVISION_HUMAN_REVIEW"]}`
- Final SC-04 revision state: `{result["PKG1_SC04_REVISION_FINAL"]}`
- Project: `{result["video_project_id"]}` (`{result["project_status"]}`)
- Exact package: `{result["package_artifact_version_id"]}` / `{result["package_content_hash"]}`
- Package artifact status: `{result["package_artifact_status"]}`
- Immutable package-declared status: `{result["immutable_package_declared_status"]}`
- Review task: `{result["review_task_id"]}` (`{result["review_task_status"]}`)
- Approval: `{result["approval_decision_id"]}` / `{result["approval_scope"]}`
- Receipt: `{result["human_review_receipt_artifact_version_id"]}` / `{result["human_review_receipt_content_hash"]}`
- MR1: `{result["MR1_EXECUTION"]}`
- Proceed to fresh MR1 reapproval: `{result["PROCEED_TO_MR1_REAPPROVAL"]}`
- Proceed directly to MR1: `{result["PROCEED_TO_MR1"]}`
- Provider/render/Drive/YouTube calls: `{result["provider_calls"]} / {result["render_calls"]} / {result["drive_calls"]} / {result["youtube_calls"]}`

The closeout records the operator-supplied literal PASS. It does not authorize
provider, render, archive, upload, publish, or MR1 execution.
"""


def main() -> int:
    args = _parser().parse_args()
    command = PKG1SC04RevisionApprovalCommand(
        project_id=args.project_id,
        review_task_id=args.review_task_id,
        reviewed_package_artifact_version_id=(args.package_artifact_version_id),
        reviewed_package_hash=args.package_hash,
        reviewed_revision_id=args.revision_id,
        reviewed_revision_version=args.revision_version,
        reviewed_revision_hash=args.revision_hash,
        decided_by_user_id=args.actor_user_id,
        decision=args.decision,
        decision_source="OPERATOR",
        review_authority="HUMAN",
        operator_decision_text=args.operator_decision_text,
        approval_ref=args.approval_ref,
        review_notes=args.review_notes,
    )
    with session_scope() as session:
        ConfigRegistryService(session).seed(
            [ROOT / "config/artifact_type_registry.yaml"]
        )
    with session_scope() as session:
        result = PKG1SC04RevisionCloseoutService(session).closeout(command)
        session.flush()

    # session_scope has committed before any closeout report is emitted.
    SUMMARY_PATH.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    REPORT_PATH.write_text(_report(result), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
