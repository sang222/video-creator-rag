from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from app.core.errors import ValidationFailureError
from app.services.pkg1_sc07_sc09_human_closeout import (
    build_pending_closeout_read_model,
    closeout_operator_decision,
    validate_human_receipt,
)
from app.services.pkg1_sc07_sc09_revision import build_revision_bundle


def _artifact_hashes(bundle: dict) -> dict[str, str]:
    return {
        key: value["content_hash"]
        for key, value in bundle["artifacts"].items()
    }


def test_human_review_starts_pending_and_cannot_auto_pass() -> None:
    bundle = build_revision_bundle()
    read_model = build_pending_closeout_read_model(bundle)

    assert read_model["operator_decision"] is None
    assert read_model["auto_approval_allowed"] is False
    assert read_model["PKG1_SC07_SC09_REVISION_HUMAN_REVIEW"] == "PENDING"
    assert read_model["PKG1_SC07_SC09_REVISION_FINAL"] == "WAITING_HUMAN_REVIEW"
    assert read_model["PRODUCTION_PACKAGE_APPROVED"] is False
    assert read_model["PROCEED_TO_MR1_REAPPROVAL"] is False


def test_hash_mismatch_prevents_human_approval() -> None:
    bundle = build_revision_bundle()

    with pytest.raises(
        ValidationFailureError,
        match="PKG1_SC07_SC09_REVIEWED_PACKAGE_HASH_MISMATCH",
    ):
        closeout_operator_decision(
            bundle,
            decision="PASS",
            operator_id="operator-fixture",
            reviewed_revision_content_hash=bundle["identity"][
                "revision_content_hash"
            ],
            reviewed_package_content_hash="wrong-hash",
            reviewed_artifact_hashes=_artifact_hashes(bundle),
            decision_timestamp=datetime(2026, 7, 25, tzinfo=UTC),
        )


def test_explicit_operator_pass_fixture_creates_hash_bound_receipt_only() -> None:
    bundle = build_revision_bundle()
    receipt = closeout_operator_decision(
        bundle,
        decision="PASS",
        operator_id="operator-fixture",
        reviewed_revision_content_hash=bundle["identity"]["revision_content_hash"],
        reviewed_package_content_hash=bundle["package_manifest"]["content_hash"],
        reviewed_artifact_hashes=_artifact_hashes(bundle),
        decision_timestamp=datetime(2026, 7, 25, tzinfo=UTC),
    )

    assert validate_human_receipt(bundle, receipt) is True
    assert receipt["decision_source"] == "OPERATOR"
    assert receipt["review_authority"] == "HUMAN"
    assert receipt["human_closeout"]["PKG1_SC07_SC09_REVISION_FINAL"] == "PASS"
    assert receipt["human_closeout"]["PRODUCTION_PACKAGE_APPROVED"] is True
    assert receipt["human_closeout"]["UPLOAD_READY"] is False
    assert receipt["human_closeout"]["PUBLISH_EXECUTION_READY"] is False


def test_reject_requires_reason_and_never_opens_reapproval() -> None:
    bundle = build_revision_bundle()
    with pytest.raises(
        ValidationFailureError,
        match="PKG1_SC07_SC09_REJECTION_REASON_REQUIRED",
    ):
        closeout_operator_decision(
            bundle,
            decision="REJECT",
            operator_id="operator-fixture",
            reviewed_revision_content_hash=bundle["identity"][
                "revision_content_hash"
            ],
            reviewed_package_content_hash=bundle["package_manifest"]["content_hash"],
            reviewed_artifact_hashes=_artifact_hashes(bundle),
            decision_timestamp=datetime(2026, 7, 25, tzinfo=UTC),
        )
    receipt = closeout_operator_decision(
        bundle,
        decision="REJECT",
        operator_id="operator-fixture",
        reviewed_revision_content_hash=bundle["identity"]["revision_content_hash"],
        reviewed_package_content_hash=bundle["package_manifest"]["content_hash"],
        reviewed_artifact_hashes=_artifact_hashes(bundle),
        decision_timestamp=datetime(2026, 7, 25, tzinfo=UTC),
        rejection_reason="Adjust SC-09 emphasis.",
    )
    assert receipt["human_closeout"]["PKG1_SC07_SC09_REVISION_FINAL"] == "REJECTED"
    assert receipt["human_closeout"]["PROCEED_TO_MR1_REAPPROVAL"] is False


def test_persisted_closeout_is_exact_human_pass_receipt() -> None:
    bundle = build_revision_bundle()
    reports = Path(__file__).resolve().parents[2] / "reports"
    summary_path = reports / "pkg1_sc07_sc09_human_closeout_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    receipt = json.loads(
        (reports / "pkg1_sc07_sc09_human_approval_receipt.json").read_text(
            encoding="utf-8"
        )
    )

    assert summary["revision"]["bundle_hash"] == bundle["bundle_hash"]
    assert summary["review_packet"]["content_hash"] == (
        bundle["review_packet"]["content_hash"]
    )
    assert validate_human_receipt(bundle, receipt) is True
    assert summary["operator_decision"] == "PASS"
    assert summary["human_approval_receipt"]["content_hash"] == (
        receipt["content_hash"]
    )
    assert summary["PKG1_SC07_SC09_REVISION_HUMAN_REVIEW"] == "PASS"
    assert summary["PKG1_SC07_SC09_REVISION_FINAL"] == "PASS"
    assert summary["PRODUCTION_PACKAGE_APPROVED"] is True
    assert summary["PROCEED_TO_MR1_REAPPROVAL"] is True
