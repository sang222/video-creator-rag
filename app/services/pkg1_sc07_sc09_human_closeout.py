from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Literal, Mapping

from app.core.errors import ValidationFailureError
from app.services.pkg1_sc07_sc09_revision import (
    PROJECT_TYPE,
    revalidate_bundle,
)
from app.services.visual_source_routing import stable_hash


Decision = Literal["PASS", "REJECT"]
APPROVAL_SCOPE = "PKG1_SC07_SC09_REVISION_PACKAGE_PLANNING"
REVIEW_AUTHORITY = "HUMAN"
DECISION_SOURCE = "OPERATOR"


def build_pending_closeout_read_model(bundle: Mapping[str, Any]) -> dict[str, Any]:
    if not revalidate_bundle(bundle):
        raise ValidationFailureError("PKG1_SC07_SC09_BUNDLE_HASH_MISMATCH")
    package = bundle["package_manifest"]
    review = bundle["review_packet"]
    return {
        "schema_version": "pkg1.sc07-sc09-human-closeout-read-model.v1",
        "project_type": PROJECT_TYPE,
        "revision": deepcopy(bundle["identity"]),
        "package_artifact_version_id": package["artifact_version_id"],
        "package_content_hash": package["content_hash"],
        "review_packet_artifact_version_id": review["artifact_version_id"],
        "review_packet_content_hash": review["content_hash"],
        "review_authority": REVIEW_AUTHORITY,
        "decision_source": DECISION_SOURCE,
        "operator_decision": None,
        "PKG1_SC07_SC09_REVISION_HUMAN_REVIEW": "PENDING",
        "PKG1_SC07_SC09_REVISION_FINAL": "WAITING_HUMAN_REVIEW",
        "PRODUCTION_PACKAGE_APPROVED": False,
        "PROCEED_TO_MR1_REAPPROVAL": False,
        "UPLOAD_READY": False,
        "PUBLISH_EXECUTION_READY": False,
        "DESTINATION_STATUS": "PENDING_PLATFORM_ID",
        "exact_next_action": "Operator returns PASS or REJECT: <reason> for the exact review packet.",
        "auto_approval_allowed": False,
    }


def closeout_operator_decision(
    bundle: Mapping[str, Any],
    *,
    decision: Decision,
    operator_id: str,
    reviewed_revision_content_hash: str,
    reviewed_package_content_hash: str,
    reviewed_artifact_hashes: Mapping[str, str],
    decision_timestamp: datetime,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    if not revalidate_bundle(bundle):
        raise ValidationFailureError("PKG1_SC07_SC09_BUNDLE_HASH_MISMATCH")
    if not operator_id.strip():
        raise ValidationFailureError("PKG1_SC07_SC09_OPERATOR_REQUIRED")
    if decision_timestamp.tzinfo is None:
        raise ValidationFailureError("PKG1_SC07_SC09_DECISION_TIMESTAMP_TZ_REQUIRED")
    identity = bundle["identity"]
    package = bundle["package_manifest"]
    if reviewed_revision_content_hash != identity["revision_content_hash"]:
        raise ValidationFailureError("PKG1_SC07_SC09_REVIEWED_REVISION_HASH_MISMATCH")
    if reviewed_package_content_hash != package["content_hash"]:
        raise ValidationFailureError("PKG1_SC07_SC09_REVIEWED_PACKAGE_HASH_MISMATCH")
    current_artifact_hashes = {
        key: value["content_hash"]
        for key, value in bundle["artifacts"].items()
    }
    if dict(reviewed_artifact_hashes) != current_artifact_hashes:
        raise ValidationFailureError("PKG1_SC07_SC09_REVIEWED_ARTIFACT_HASH_MISMATCH")
    if decision == "REJECT" and not (rejection_reason or "").strip():
        raise ValidationFailureError("PKG1_SC07_SC09_REJECTION_REASON_REQUIRED")
    if decision == "PASS" and rejection_reason:
        raise ValidationFailureError("PKG1_SC07_SC09_PASS_WITH_REJECTION_REASON")

    pass_decision = decision == "PASS"
    receipt_payload = {
        "schema_version": "pkg1.sc07-sc09-human-approval-receipt.v1",
        "decision": decision,
        "decision_source": DECISION_SOURCE,
        "review_authority": REVIEW_AUTHORITY,
        "operator_id": operator_id,
        "decision_timestamp": decision_timestamp.isoformat(),
        "rejection_reason": rejection_reason,
        "approval_scope": APPROVAL_SCOPE,
        "revision": {
            "revision_id": identity["revision_id"],
            "revision_version": identity["revision_version"],
            "revision_hash": identity["revision_hash"],
            "revision_content_hash": identity["revision_content_hash"],
        },
        "package": {
            "artifact_version_id": package["artifact_version_id"],
            "content_hash": package["content_hash"],
        },
        "review_packet": {
            "artifact_version_id": bundle["review_packet"]["artifact_version_id"],
            "content_hash": bundle["review_packet"]["content_hash"],
        },
        "reviewed_artifact_hashes": dict(sorted(reviewed_artifact_hashes.items())),
        "reviewed_bundle_hash": bundle["bundle_hash"],
        "hash_revalidation": "PASS",
        "supersession_marker": "SUPERSEDED_BY_SC07_SC09_REVISION",
        "human_closeout": {
            "PKG1_SC07_SC09_REVISION_HUMAN_REVIEW": decision,
            "PKG1_SC07_SC09_REVISION_FINAL": (
                "PASS" if pass_decision else "REJECTED"
            ),
            "PRODUCTION_PACKAGE_APPROVED": pass_decision,
            "PROCEED_TO_MR1_REAPPROVAL": pass_decision,
            "UPLOAD_READY": False,
            "PUBLISH_EXECUTION_READY": False,
            "DESTINATION_STATUS": "PENDING_PLATFORM_ID",
        },
        "no_execution_proof": deepcopy(bundle["no_execution_proof"]),
    }
    content_hash = stable_hash(receipt_payload)
    receipt_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"vcos:pkg1-sc07-sc09-human-receipt:{content_hash}",
        )
    )
    return {
        **receipt_payload,
        "approval_receipt_artifact_version_id": receipt_id,
        "approval_receipt_ref": f"artifact-version://{receipt_id}",
        "content_hash": content_hash,
    }


def validate_human_receipt(
    bundle: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> bool:
    candidate = deepcopy(dict(receipt))
    content_hash = candidate.pop("content_hash", None)
    receipt_id = candidate.pop("approval_receipt_artifact_version_id", None)
    receipt_ref = candidate.pop("approval_receipt_ref", None)
    if content_hash != stable_hash(candidate):
        return False
    expected_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"vcos:pkg1-sc07-sc09-human-receipt:{content_hash}",
        )
    )
    return bool(
        revalidate_bundle(bundle)
        and receipt_id == expected_id
        and receipt_ref == f"artifact-version://{expected_id}"
        and receipt.get("reviewed_bundle_hash") == bundle["bundle_hash"]
        and receipt.get("package", {}).get("content_hash")
        == bundle["package_manifest"]["content_hash"]
    )


__all__ = [
    "APPROVAL_SCOPE",
    "build_pending_closeout_read_model",
    "closeout_operator_decision",
    "validate_human_receipt",
]
