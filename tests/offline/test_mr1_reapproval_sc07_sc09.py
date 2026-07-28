from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from app.core.errors import ValidationFailureError
from app.services.mr1_reapproval_sc07_sc09 import (
    build_reuse_decision_manifest,
    create_fresh_mr1_reapproval,
)
from app.services.pkg1_sc07_sc09_human_closeout import closeout_operator_decision
from app.services.pkg1_sc07_sc09_revision import build_revision_bundle


def _pass_receipt(bundle: dict) -> dict:
    hashes = {
        key: value["content_hash"]
        for key, value in bundle["artifacts"].items()
    }
    return closeout_operator_decision(
        bundle,
        decision="PASS",
        operator_id="operator-fixture",
        reviewed_revision_content_hash=bundle["identity"]["revision_content_hash"],
        reviewed_package_content_hash=bundle["package_manifest"]["content_hash"],
        reviewed_artifact_hashes=hashes,
        decision_timestamp=datetime(2026, 7, 25, tzinfo=UTC),
    )


def test_fresh_reapproval_requires_exact_human_pass_receipt() -> None:
    bundle = build_revision_bundle()
    with pytest.raises(ValidationFailureError, match="HUMAN_RECEIPT_INVALID"):
        create_fresh_mr1_reapproval(
            bundle,
            {},
            operator_id="operator-fixture",
            approval_timestamp=datetime(2026, 7, 25, tzinfo=UTC),
        )


def test_fresh_reapproval_binds_new_package_and_zero_scene_provider_scope() -> None:
    bundle = build_revision_bundle()
    approval = create_fresh_mr1_reapproval(
        bundle,
        _pass_receipt(bundle),
        operator_id="operator-fixture",
        approval_timestamp=datetime(2026, 7, 25, tzinfo=UTC),
    )
    payload = approval["approval"]
    scope = payload["attempt_scope"]

    assert payload["exact_target"]["revision_id"] == bundle["identity"]["revision_id"]
    assert payload["exact_target"]["package_content_hash"] == (
        bundle["package_manifest"]["content_hash"]
    )
    assert scope["SC-07"]["external_provider_attempts"] == 0
    assert scope["SC-07"]["route"] == "NATIVE_MOTION_GRAPHIC"
    assert scope["SC-09"]["external_provider_attempts"] == 0
    assert scope["SC-09"]["route"] == "NATIVE_DIAGRAM"
    assert scope["fallback"] is False
    assert scope["provider_substitution"] is False
    assert payload["production_render_authorized"] is True
    assert payload["publish_authorized"] is False
    assert payload["destination_status"] == "PENDING_PLATFORM_ID"


def test_reuse_manifest_invalidates_pexels_and_requires_native_local_work() -> None:
    manifest = build_reuse_decision_manifest(build_revision_bundle())
    decisions = {
        item["operation_key"]: item["classification"]
        for item in manifest["decisions"]
    }

    assert decisions["pexels:SC-07"] == "INVALIDATED_BY_REVISION"
    assert decisions["pexels:SC-07:supplement:02"] == "INVALIDATED_BY_REVISION"
    assert decisions["pexels:SC-09"] == "INVALIDATED_BY_REVISION"
    assert decisions["native:SC-07"] == "REQUIRES_LOCAL_COMPILATION_RENDER"
    assert decisions["native:SC-09"] == "REQUIRES_LOCAL_COMPILATION_RENDER"
    assert manifest["old_consumed_ledgers_preserved"] is True


def test_approval_creation_performs_no_execution_and_reuses_no_old_authority() -> None:
    bundle = build_revision_bundle()
    result = create_fresh_mr1_reapproval(
        bundle,
        _pass_receipt(bundle),
        operator_id="operator-fixture",
        approval_timestamp=datetime(2026, 7, 25, tzinfo=UTC),
    )
    payload = result["approval"]

    assert payload["old_task_authorizations_reused"] is False
    assert payload["old_sc07_sc09_pexels_ledgers_reused"] is False
    assert "40193854-8633-45a5-97be-54b380a8c8e5" in payload["supersedes"]
    assert payload["provider_call_count"] == 4
    assert payload["render_call_count"] == 0
    assert payload["drive_call_count"] == 0
    assert payload["youtube_call_count"] == 0
    assert payload["MR1_EXECUTION"] == "NOT_STARTED"
    assert result["task_wide_authorization"]["publish_authorized"] is False


def test_persisted_reapproval_matches_exact_deterministic_approval() -> None:
    reports = Path(__file__).resolve().parents[2] / "reports"
    summary = json.loads(
        (reports / "mr1_reapproval_sc07_sc09_summary.json").read_text(
            encoding="utf-8"
        )
    )
    human_receipt = json.loads(
        (reports / "pkg1_sc07_sc09_human_approval_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    persisted_approval_receipt = json.loads(
        (reports / "mr1_reapproval_sc07_sc09_approval_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    persisted_reuse = json.loads(
        (reports / "mr1_reuse_decision_manifest_sc07_sc09.json").read_text(
            encoding="utf-8"
        )
    )
    bundle = build_revision_bundle()
    result = create_fresh_mr1_reapproval(
        bundle,
        human_receipt,
        operator_id=summary["operator_id"],
        approval_timestamp=datetime.fromisoformat(summary["approval_timestamp"]),
    )

    assert summary["status"] == "PASS_NOT_EXECUTED"
    assert summary["fresh_mr1_approval"]["approval_id"] == (
        result["approval"]["approval_id"]
    )
    assert summary["fresh_mr1_approval"]["content_hash"] == (
        result["approval"]["content_hash"]
    )
    assert persisted_approval_receipt == result["approval_receipt"]
    assert persisted_reuse == result["reuse_decision_manifest"]
    assert summary["MR1_REAPPROVAL_FINAL"] == "PASS"
    assert summary["MR1_EXECUTION"] == "NOT_STARTED"
    assert summary["PROCEED_TO_MR1"] is True
