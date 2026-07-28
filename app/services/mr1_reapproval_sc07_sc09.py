from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping

from app.core.errors import ValidationFailureError
from app.services.pkg1_sc07_sc09_human_closeout import validate_human_receipt
from app.services.pkg1_sc07_sc09_revision import (
    CANONICAL_BINDINGS,
    FORBIDDEN_OLD_MR1_APPROVAL_ID,
    RUN_ID,
    SOURCE_MR1_APPROVAL_ID,
    revalidate_bundle,
)
from app.services.visual_source_routing import stable_hash


APPROVAL_PURPOSE = "MR1_SC07_SC09_NATIVE_REVISION_PRODUCTION_RENDER"


def build_reuse_decision_manifest(
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    if not revalidate_bundle(bundle):
        raise ValidationFailureError("MR1_SC07_SC09_BUNDLE_HASH_MISMATCH")
    payload = {
        "schema_version": "mr1.sc07-sc09-reuse-decision-manifest.v1",
        "source_run_id": RUN_ID,
        "target_revision_id": bundle["identity"]["revision_id"],
        "target_package_content_hash": bundle["package_manifest"]["content_hash"],
        "decisions": [
            {
                "operation_key": "elevenlabs:narration",
                "classification": "REUSE_VALID",
                "request_hash": "56fcf5b846a53b11b65ce4ba55df7e0aec607b7654cd133bdae540395b22191f",
                "artifact_checksum": "1fb69c621efbd2a4e84cc352432d0eaf6b69ef74ade0c23d1e85a64031694ffd",
                "proof": "script/text and voice policy unchanged; exact checksum retained",
            },
            {
                "operation_key": "elevenlabs:forced_alignment",
                "classification": "REUSE_VALID",
                "request_hash": "6be543fc3f98be26cd14b365828d3554c3957db4fb6bd067877d307678c591e1",
                "artifact_checksum": "9179c35b4bbcb6b1c412f753d99cfd915fecc3ef1dcd64a62a3a8115c079e95c",
                "proof": "audio and SpokenTextNormalized authorities unchanged",
            },
            {
                "operation_key": "pexels:SC-07",
                "classification": "INVALIDATED_BY_REVISION",
                "historical_state": "CONSUMED_FAILED",
            },
            {
                "operation_key": "pexels:SC-07:supplement:02",
                "classification": "INVALIDATED_BY_REVISION",
                "historical_state": "CONSUMED_FAILED",
            },
            {
                "operation_key": "pexels:SC-09",
                "classification": "INVALIDATED_BY_REVISION",
                "historical_state": "PLANNED_NOT_SUBMITTED",
            },
            {
                "operation_key": "native:SC-07",
                "classification": "REQUIRES_LOCAL_COMPILATION_RENDER",
                "route": "NATIVE_MOTION_GRAPHIC",
            },
            {
                "operation_key": "native:SC-09",
                "classification": "REQUIRES_LOCAL_COMPILATION_RENDER",
                "route": "NATIVE_DIAGRAM",
            },
            {
                "operation_key": "final_media",
                "classification": "MISSING",
            },
            {
                "operation_key": "drive:archive",
                "classification": "REQUIRES_NEW_EXECUTION",
            },
        ],
        "old_consumed_ledgers_preserved": True,
        "provider_calls": 0,
        "render_calls": 0,
        "drive_calls": 0,
        "youtube_calls": 0,
    }
    content_hash = stable_hash(payload)
    manifest_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"vcos:mr1-sc07-sc09-reuse-manifest:{content_hash}",
        )
    )
    return {
        **payload,
        "artifact_version_id": manifest_id,
        "artifact_version_ref": f"artifact-version://{manifest_id}",
        "content_hash": content_hash,
    }


def create_fresh_mr1_reapproval(
    bundle: Mapping[str, Any],
    human_receipt: Mapping[str, Any],
    *,
    operator_id: str,
    approval_timestamp: datetime,
) -> dict[str, Any]:
    if not revalidate_bundle(bundle):
        raise ValidationFailureError("MR1_SC07_SC09_BUNDLE_HASH_MISMATCH")
    if not validate_human_receipt(bundle, human_receipt):
        raise ValidationFailureError("MR1_SC07_SC09_HUMAN_RECEIPT_INVALID")
    closeout = human_receipt.get("human_closeout") or {}
    if (
        human_receipt.get("decision") != "PASS"
        or closeout.get("PKG1_SC07_SC09_REVISION_FINAL") != "PASS"
        or closeout.get("PRODUCTION_PACKAGE_APPROVED") is not True
        or closeout.get("PROCEED_TO_MR1_REAPPROVAL") is not True
    ):
        raise ValidationFailureError("MR1_SC07_SC09_HUMAN_PASS_REQUIRED")
    if not operator_id.strip() or approval_timestamp.tzinfo is None:
        raise ValidationFailureError("MR1_SC07_SC09_APPROVER_AND_TIMESTAMP_REQUIRED")

    provider_plan = bundle["artifacts"]["provider_execution_plan"]
    native = provider_plan["content"]["native_operations"]
    if (
        native.get("SC-07", {}).get("external_provider_attempts") != 0
        or native.get("SC-09", {}).get("external_provider_attempts") != 0
        or provider_plan["content"].get("fallback") is not False
        or provider_plan["content"].get("provider_substitution_allowed") is not False
    ):
        raise ValidationFailureError("MR1_SC07_SC09_PROVIDER_SCOPE_INVALID")

    reuse = build_reuse_decision_manifest(bundle)
    artifact_bindings = {
        key: {
            "artifact_version_id": value["artifact_version_id"],
            "content_hash": value["content_hash"],
        }
        for key, value in bundle["artifacts"].items()
    }
    attempt_scope = {
        "SC-07": {
            "route": "NATIVE_MOTION_GRAPHIC",
            "external_provider_attempts": 0,
            "local_compilation_render_required": True,
        },
        "SC-09": {
            "route": "NATIVE_DIAGRAM",
            "external_provider_attempts": 0,
            "local_compilation_render_required": True,
        },
        "fallback": False,
        "provider_substitution": False,
        "unaffected_scenes_authority": (
            bundle["artifacts"]["provider_execution_plan"]["content_hash"]
        ),
    }
    approval_payload = {
        "schema_version": "mr1.sc07-sc09-execution-approval.v1",
        "approval_purpose": APPROVAL_PURPOSE,
        "operator_id": operator_id,
        "approval_timestamp": approval_timestamp.isoformat(),
        "exact_target": {
            "revision_id": bundle["identity"]["revision_id"],
            "revision_version": bundle["identity"]["revision_version"],
            "revision_hash": bundle["identity"]["revision_hash"],
            "revision_content_hash": bundle["identity"]["revision_content_hash"],
            "package_artifact_version_id": bundle["package_manifest"][
                "artifact_version_id"
            ],
            "package_content_hash": bundle["package_manifest"]["content_hash"],
        },
        "canonical_bindings": deepcopy(CANONICAL_BINDINGS),
        "artifact_bindings": artifact_bindings,
        "human_approval_receipt": {
            "artifact_version_id": human_receipt[
                "approval_receipt_artifact_version_id"
            ],
            "content_hash": human_receipt["content_hash"],
        },
        "attempt_scope": attempt_scope,
        "cost_scope": {
            "cost_estimate_snapshot_hash": bundle["artifacts"][
                "cost_estimate_snapshot"
            ]["content_hash"],
            "SC-07": "COST_0_NATIVE",
            "SC-09": "COST_0_NATIVE",
            "actual_cost_invented": False,
        },
        "idempotency_scope": {
            "inputs": [
                "approval_content_hash",
                "package_content_hash",
                "run_id",
                "scene_id",
                "route",
                "request_hash",
            ],
            "fallback": False,
        },
        "reuse_decision_manifest": {
            "artifact_version_id": reuse["artifact_version_id"],
            "content_hash": reuse["content_hash"],
        },
        "supersedes": [
            SOURCE_MR1_APPROVAL_ID,
            FORBIDDEN_OLD_MR1_APPROVAL_ID,
        ],
        "old_task_authorizations_reused": False,
        "old_sc07_sc09_pexels_ledgers_reused": False,
        "production_render_authorized": True,
        "publish_authorized": False,
        "destination_status": "PENDING_PLATFORM_ID",
        "MR1_EXECUTION": "NOT_STARTED",
        "MR1_RENDER_STATUS": "NOT_STARTED",
        "MR1_HUMAN_REVIEW": "PENDING",
        "provider_call_count": 4,
        "render_call_count": 0,
        "drive_call_count": 0,
        "youtube_call_count": 0,
    }
    content_hash = stable_hash(approval_payload)
    approval_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"vcos:mr1-sc07-sc09-approval:{content_hash}",
        )
    )
    receipt_payload = {
        "schema_version": "mr1.sc07-sc09-approval-receipt.v1",
        "approval_id": approval_id,
        "approval_content_hash": content_hash,
        "approval_ref": (
            f"mr1-approval://small-team-ai/"
            f"{bundle['identity']['revision_id']}/{approval_id}"
        ),
        "exact_target": approval_payload["exact_target"],
        "attempt_scope": attempt_scope,
        "reuse_decision_manifest": {
            "artifact_version_id": reuse["artifact_version_id"],
            "content_hash": reuse["content_hash"],
        },
        "approval_final": "PASS",
        "execution_started": False,
    }
    receipt_hash = stable_hash(receipt_payload)
    receipt_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"vcos:mr1-sc07-sc09-approval-receipt:{receipt_hash}",
        )
    )
    return {
        "approval": {
            **approval_payload,
            "approval_id": approval_id,
            "content_hash": content_hash,
        },
        "approval_receipt": {
            **receipt_payload,
            "artifact_version_id": receipt_id,
            "content_hash": receipt_hash,
        },
        "reuse_decision_manifest": reuse,
        "task_wide_authorization": {
            "authorization_id": str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"vcos:mr1-sc07-sc09-task-authorization:{content_hash}",
                )
            ),
            "approval_id": approval_id,
            "provider_execution_authorized": True,
            "render_authorized": True,
            "publish_authorized": False,
            "fallback": False,
            "provider_substitution": False,
        },
        "MR1_REAPPROVAL_FINAL": "PASS",
        "MR1_EXECUTION": "NOT_STARTED",
        "PROCEED_TO_MR1": True,
    }


__all__ = [
    "APPROVAL_PURPOSE",
    "build_reuse_decision_manifest",
    "create_fresh_mr1_reapproval",
]
