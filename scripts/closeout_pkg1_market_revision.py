from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.contracts.pkg1_market_revision_closeout import (
    PKG1MarketRevisionApprovalCommand,
)
from app.db.models import ReviewTask
from app.db.session import session_scope
from app.services.config_registry import ConfigRegistryService
from app.services.pkg1_market_revision_closeout import (
    PKG1MarketRevisionCloseoutService,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = uuid.UUID("2522a8f1-1ea4-4d66-8ea5-411aaa8f152b")
REVIEW_TASK_ID = uuid.UUID("a99f2ad4-9b1d-4bc6-bafc-024ecd7e9c56")
PACKAGE_ARTIFACT_VERSION_ID = uuid.UUID(
    "7de25ac8-46e4-46da-b112-f805f16ebaaa"
)
PACKAGE_HASH = (
    "200b3be30b92ccff3b0efb26881d5654ab4b53162afe73d4e7f34bed3b0454bd"
)
REVISION_ID = uuid.UUID("a90e2786-f6e0-5480-94a4-fb28fd000edf")
REVISION_VERSION = 2
REVISION_HASH = (
    "b50ff5d3bcbf07de4b709ae0d9017a9df04fec49481fb14c224a709c85b0875b"
)
APPROVAL_REF = (
    "operator-approval://pkg1-market-revision/"
    f"{REVISION_ID}/v{REVISION_VERSION}/{REVISION_HASH}/package/"
    f"{PACKAGE_ARTIFACT_VERSION_ID}/{PACKAGE_HASH}"
)


def main() -> int:
    with session_scope() as session:
        ConfigRegistryService(session).seed(
            [ROOT / "config/artifact_type_registry.yaml"]
        )

    with session_scope() as session:
        review = session.get(ReviewTask, REVIEW_TASK_ID)
        if review is None or review.assigned_to_user_id is None:
            raise RuntimeError("EXACT_ASSIGNED_OPERATOR_REVIEW_NOT_FOUND")
        command = PKG1MarketRevisionApprovalCommand(
            project_id=PROJECT_ID,
            review_task_id=REVIEW_TASK_ID,
            reviewed_package_artifact_version_id=(
                PACKAGE_ARTIFACT_VERSION_ID
            ),
            reviewed_package_hash=PACKAGE_HASH,
            reviewed_revision_id=REVISION_ID,
            reviewed_revision_version=REVISION_VERSION,
            reviewed_revision_hash=REVISION_HASH,
            decided_by_user_id=review.assigned_to_user_id,
            decision="PASS",
            decision_source="OPERATOR",
            review_authority="HUMAN",
            operator_decision_text="PASS",
            approval_ref=APPROVAL_REF,
            review_notes=None,
        )
        result = PKG1MarketRevisionCloseoutService(session).closeout(command)
        session.flush()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
